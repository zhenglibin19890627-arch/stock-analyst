"""
021BI 全市场选股扫描器 (Market Screener)

两段漏斗：
  第①段 快照粗筛：新浪 hs_a 分页列表（~56 页 / 5553 只，约 1 分钟）→ 卫生线 + 条件过滤
  第②段 技术信号精筛：腾讯 120 日前复权 K 线（逐票，候选 ≤300，前端分批驱动）
        → 本地计算 MACD/KDJ/RSI 序列 → 8 类信号检测（口径与 scoring_engine 同源：
        MACD(12,26,9) 复用 technical_detail._ema_list，KDJ(9,3,3) k=d=50 起步，RSI14）

数据源（021BI 前置探测实测于本机 2026-08-28）：
  - 新浪 Market_Center.getHQNodeData：全市场列表（主源，东财断连不影响）
  - 新浪 newSinaHy.php + 同端点行业节点：行业映射（缓存 7 天）
  - 腾讯 qt.gtimg.cn：批量行情增强（量比/振幅）；web.ifzq.gtimg.cn：日K
  - 明确不依赖东财（push2 长期断连期间本功能不受影响）

边界：
  - 扫描器只生产候选，不自动入库；加自选 ≤20 只（BATCH_OPERATION_LIMIT 红线）
  - 快照 ≠ 评级，结果页须标注"快照参考"
  - 无新 pip 依赖（仅 requests）
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta

import requests

from database.db_manager import get_connection
from modules.technical_detail import _ema_list

logger = logging.getLogger(__name__)

# ================================================================
# 请求礼貌约束（独立于东财管道；探测实测新浪 0.4s 间隔稳定）
# ================================================================

_UA_SINA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn',
}
_UA_QQ = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://gu.qq.com/',
}
_SINA_MIN_INTERVAL = 0.35   # 秒
_QQ_MIN_INTERVAL = 0.25
_SINA_PAGE_SIZE = 100
_SINA_MAX_PAGES = 80        # 5553 只 ≈ 56 页，80 为安全上限
_SNAPSHOT_MAX_AGE_DAYS = 7  # 行业映射缓存天数

_last_ts = {'sina': 0.0, 'qq': 0.0}


def _pace(channel):
    """通道级最小间隔（sina / qq），避免打爆免费接口。"""
    key = 'sina' if channel == 'sina' else 'qq'
    interval = _SINA_MIN_INTERVAL if channel == 'sina' else _QQ_MIN_INTERVAL
    wait = _last_ts[key] + interval - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_ts[key] = time.time()


def _http_get(url, params=None, headers=None, channel='sina', timeout=10, retries=2):
    """带退避的 GET（非东财源，独立于 _http_get_em 管道）。"""
    last_exc = None
    for attempt in range(retries + 1):
        _pace(channel)
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
            last_exc = RuntimeError(f'HTTP {r.status_code}')
        except Exception as e:  # noqa: BLE001
            last_exc = e
        time.sleep(0.8 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


# ================================================================
# 信号库（8 类，触发窗口内检测；note 为结果页白话解读）
# ================================================================

SIGNAL_LIBRARY = {
    'macd_golden_above': {'label': 'MACD水上金叉', 'note': 'DIF上穿DEA且DIF>0：多头趋势中的加速信号，相对可靠'},
    'macd_golden_below': {'label': 'MACD水下金叉', 'note': 'DIF上穿DEA且DIF≤0：下跌趋势中的反弹信号，需配合量能，偏短线'},
    'macd_dead': {'label': 'MACD死叉', 'note': 'DIF下穿DEA：转空预警，用于排除'},
    'kdj_golden_low': {'label': 'KDJ低位金叉', 'note': 'K上穿D且交叉时D<25：超卖区反转，信号中较可靠的买点'},
    'kdj_golden': {'label': 'KDJ金叉', 'note': 'K上穿D：一般买点参考'},
    'kdj_oversold': {'label': 'KDJ超卖', 'note': 'J<0 或 K<20：短期超跌状态，等企稳信号'},
    'kdj_overbought': {'label': 'KDJ超买', 'note': 'J>100 或 K>80：短期过热，谨防回调'},
    'rsi_oversold': {'label': 'RSI超卖', 'note': 'RSI14<30：超跌状态（Wilder 口径，与评分引擎一致）'},
    'rsi_overbought': {'label': 'RSI超买', 'note': 'RSI14>70：过热状态'},
}

DEFAULT_HYGIENE = {
    'exclude_st': True,        # 剔除 ST/*ST
    'exclude_bj': True,        # 剔除北交所（解析层已按代码过滤）
    'mkt_cap_min': 100.0,      # 总市值 ≥100 亿
    'turnover_min': 1.0,       # 换手率 ≥1%
}

# ================================================================
# 共振库（021BI 跟进：跨指标系同向叠加，不新增指标、零额外请求）
# 规则：两个独立指标系（MACD/KDJ）的触发证据落在同一窗口内才成立；
#       RSI 为状态类，只做环境层不作为触发证据。
# kind: bull=买点型 / bear=风险预警（用于排除） / watch=观察池
# ================================================================

RESONANCE_LIBRARY = {
    'res_bottom_reverse': {
        'label': '底部反转共振', 'stars': 3, 'kind': 'bull',
        'note': 'KDJ低位金叉+MACD水下金叉同窗：超卖反转与趋势反弹双确认，经典底部结构'},
    'res_double_golden': {
        'label': '双金叉共振', 'stars': 2, 'kind': 'bull',
        'note': 'MACD系金叉+KDJ系金叉同窗：两个独立指标系同向触发'},
    'res_bear_confirm': {
        'label': '空头共振预警', 'stars': 0, 'kind': 'bear',
        'note': 'MACD死叉+超买状态同窗：转空双确认，建议排除'},
    'res_oversold_watch': {
        'label': '超卖待确认', 'stars': 1, 'kind': 'watch',
        'note': 'KDJ与RSI双超卖且窗口内无金叉：深度超跌观察池，等右侧触发'},
}

_MACD_BULL = ('macd_golden_above', 'macd_golden_below')
_KDJ_BULL = ('kdj_golden_low', 'kdj_golden')


def _ma_series(closes, n):
    """简单移动平均序列（前 n-1 位为 None）。"""
    out = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def detect_resonances(hits, kline_rows=None):
    """从单只股票的信号命中推导共振（同窗口前提由 detect_signals 保证）。

    hits: detect_signals 输出；kline_rows: 可选，用于 MA20 环境注记。
    Returns: [{'key', 'label', 'stars', 'kind', 'note', 'signals': [label@date...]}]
    每股买点型取最高档（底部反转 > 双金叉）；空头预警与观察池独立判定。
    """
    if not hits:
        return []
    keys = {h['signal'] for h in hits}
    macd_bull = any(k in keys for k in _MACD_BULL)
    kdj_bull = any(k in keys for k in _KDJ_BULL)
    bull_trigger = macd_bull or kdj_bull

    # 环境注记（不定级）
    env = []
    if 'rsi_oversold' in keys:
        env.append('+RSI超卖环境')
    if kline_rows and len(kline_rows) >= 20:
        closes = [r['close'] for r in kline_rows]
        ma20 = _ma_series(closes, 20)[-1]
        if ma20:
            env.append('MA20上方' if closes[-1] >= ma20 else 'MA20下方')
    env_str = ('（' + '·'.join(env) + '）') if env else ''

    sig_desc = ' + '.join(f"{h['label']}@{h['trigger_date']}" for h in hits)

    def _res(key):
        lib = RESONANCE_LIBRARY[key]
        return {'key': key, 'label': lib['label'], 'stars': lib['stars'],
                'kind': lib['kind'], 'note': lib['note'] + env_str,
                'signals': sig_desc}

    out = []
    # 买点型：取最高档
    if 'kdj_golden_low' in keys and 'macd_golden_below' in keys:
        out.append(_res('res_bottom_reverse'))
    elif macd_bull and kdj_bull:
        out.append(_res('res_double_golden'))
    # 空头预警：独立判定（窗口内先死叉后金叉的极端V转可两者并存，如实展示）
    if 'macd_dead' in keys and ('kdj_overbought' in keys or 'rsi_overbought' in keys):
        out.append(_res('res_bear_confirm'))
    # 超卖观察池：双超卖状态且无任何触发类金叉
    if 'kdj_oversold' in keys and 'rsi_oversold' in keys and not bull_trigger:
        out.append(_res('res_oversold_watch'))
    return out


# ================================================================
# 新浪：全市场快照
# ================================================================


def _parse_sina_row(row):
    """新浪单行 → 标准快照 dict；非法/非沪深A股返回 None。"""
    try:
        symbol = str(row.get('symbol') or '')
        code = str(row.get('code') or '')
        name = str(row.get('name') or '')
        price = float(row.get('trade') or 0)
        # 仅沪深A股：60x主板 / 000,001,002,003主板中小 / 300,301创业板 / 688,689科创板
        if not re.match(r'^(60|00|30|68)', code):
            return None
        if price <= 0:
            return None

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        if code.startswith('30'):
            board = '创业板'
        elif code.startswith('68'):
            board = '科创板'
        else:
            board = '主板'
        mktcap_wan = _f(row.get('mktcap'))
        nmc_wan = _f(row.get('nmc'))
        return {
            'symbol': symbol,
            'code': code,
            'name': name,
            'board': board,
            'price': price,
            'change_pct': _f(row.get('changepercent')),
            'turnover': _f(row.get('turnoverratio')),
            'amount': _f(row.get('amount')),        # 万元（新浪原始口径）
            'mkt_cap': round(mktcap_wan / 10000, 2) if mktcap_wan else None,   # 亿
            'nmc_cap': round(nmc_wan / 10000, 2) if nmc_wan else None,         # 亿
            'pe': _f(row.get('per')),
            'pb': _f(row.get('pb')),
            'volume_ratio': None,   # 腾讯增强字段，粗筛后补
            'industry': None,       # 行业映射，扫描时回填
        }
    except Exception:  # noqa: BLE001
        return None


def fetch_sina_snapshot(max_pages=_SINA_MAX_PAGES):
    """拉全市场快照（hs_a 分页）。返回 (rows, meta)。

    meta: {pages, universe, stopped}
    """
    url = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           'Market_Center.getHQNodeData')
    rows = []
    pages = 0
    stopped = 'complete'
    empty_streak = 0
    for page in range(1, max_pages + 1):
        try:
            r = _http_get(
                url,
                params={'page': page, 'num': _SINA_PAGE_SIZE, 'sort': 'amount',
                        'asc': 0, 'node': 'hs_a'},
                headers=_UA_SINA, channel='sina', timeout=10, retries=1)
            arr = json.loads(r.text)
        except Exception as e:  # noqa: BLE001
            empty_streak += 1
            logger.warning(f'[扫描器] 新浪第 {page} 页失败: {e}')
            if empty_streak >= 3:
                stopped = f'连续 {empty_streak} 页失败，中止于第 {page} 页'
                break
            continue
        if not isinstance(arr, list) or not arr:
            stopped = 'complete'
            break
        empty_streak = 0
        pages = page
        for raw in arr:
            parsed = _parse_sina_row(raw)
            if parsed is not None:
                rows.append(parsed)
        if len(arr) < _SINA_PAGE_SIZE:
            break
    return rows, {'pages': pages, 'universe': len(rows), 'stopped': stopped}


# ================================================================
# 新浪：行业映射（缓存 7 天）
# ================================================================


def _industry_cache_age_days():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(updated_at) FROM sina_industry_map')
        row = cursor.fetchone()
        cursor.execute('SELECT COUNT(*) FROM sina_industry_map')
        n = cursor.fetchone()[0]
        conn.close()
        if not row or not row[0] or n < 1000:
            return None
        updated = datetime.fromisoformat(str(row[0]))
        return (datetime.now() - updated).days
    except Exception:  # noqa: BLE001
        return None


def _load_industry_map_db():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol, industry FROM sina_industry_map')
        result = {r['symbol']: r['industry'] for r in cursor.fetchall()}
        conn.close()
        return result
    except Exception:  # noqa: BLE001
        return {}


def _save_industry_map(mapping):
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat(timespec='seconds')
    cursor.execute('DELETE FROM sina_industry_map')
    cursor.executemany(
        'INSERT OR REPLACE INTO sina_industry_map (symbol, industry, node, updated_at) '
        'VALUES (?, ?, ?, ?)',
        [(sym, ind, node, now) for sym, (ind, node) in mapping.items()],
    )
    conn.commit()
    conn.close()


def fetch_industry_map(force=False, max_nodes=200):
    """行业映射 symbol → 行业名。缓存 7 天；失败时降级用库内旧缓存。

    来源：newSinaHy.php 类别表 + getHQNodeData 按行业节点取成员（~55 行业 ≈ 70 请求）。
    """
    if not force:
        age = _industry_cache_age_days()
        if age is not None and age < _SNAPSHOT_MAX_AGE_DAYS:
            return _load_industry_map_db()

    rc = _http_get('https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
                   headers=_UA_SINA, channel='sina', timeout=10)
    rc.encoding = 'gbk'
    # 格式："new_blhy":"new_blhy,玻璃行业,19,..." → 节点/行业名
    pairs = re.findall(r'"(new_\w+)":"\1,([^,]+),', rc.text)
    if len(pairs) < 10:
        raise RuntimeError(f'新浪行业类别表解析异常：仅 {len(pairs)} 个行业')

    mapping = {}
    for node, industry in pairs[:max_nodes]:
        page = 1
        while page <= 12:  # 单行业最多 12 页（1200 只，足够）
            try:
                r = _http_get(
                    'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                    'Market_Center.getHQNodeData',
                    params={'page': page, 'num': 100, 'sort': 'amount', 'asc': 0,
                            'node': node},
                    headers=_UA_SINA, channel='sina', timeout=10, retries=1)
                arr = json.loads(r.text)
            except Exception as e:  # noqa: BLE001
                logger.warning(f'[扫描器] 行业节点 {node} 第 {page} 页失败: {e}')
                break
            if not isinstance(arr, list) or not arr:
                break
            for row in arr:
                sym = str(row.get('symbol') or '')
                if sym:
                    mapping[sym] = (industry, node)
            if len(arr) < 100:
                break
            page += 1

    if len(mapping) < 1000:
        raise RuntimeError(f'行业成员映射过少（{len(mapping)}），疑似接口变更')
    _save_industry_map(mapping)
    return mapping


# ================================================================
# 腾讯：批量行情增强 + K线
# ================================================================


def fetch_tencent_enrich(symbols):
    """批量补量比/振幅（60 码/请求）。返回 {symbol: {volume_ratio, amplitude}}。"""
    result = {}
    for i in range(0, len(symbols), 60):
        chunk = symbols[i:i + 60]
        try:
            r = _http_get('https://qt.gtimg.cn/q=' + ','.join(chunk),
                          headers=_UA_QQ, channel='qq', timeout=10)
            r.encoding = 'gbk'
        except Exception as e:  # noqa: BLE001
            logger.warning(f'[扫描器] 腾讯增强批次失败: {e}')
            continue
        for part in r.text.split(';'):
            part = part.strip()
            if '~' not in part:
                continue
            fields = part.split('~')
            sym = ''
            for s in chunk:
                if s in part:
                    sym = s
                    break
            if not sym or len(fields) < 50:
                continue

            def _f(idx):
                try:
                    return float(fields[idx])
                except (IndexError, TypeError, ValueError):
                    return None

            result[sym] = {'volume_ratio': _f(49), 'amplitude': _f(43)}
    return result


def fetch_kline(symbol, count=120):
    """腾讯日K（前复权）→ [{date, open, close, high, low, volume}]，时间正序。"""
    r = _http_get(
        'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
        params={'param': f'{symbol},day,,,{count},qfq'},
        headers=_UA_QQ, channel='qq', timeout=10)
    d = r.json()
    node = (d.get('data') or {}).get(symbol) or {}
    days = node.get('qfqday') or node.get('day') or []
    out = []
    for row in days:
        try:
            out.append({
                'date': row[0],
                'open': float(row[1]),
                'close': float(row[2]),
                'high': float(row[3]),
                'low': float(row[4]),
                'volume': float(row[5]) if len(row) > 5 else 0.0,
            })
        except (IndexError, TypeError, ValueError):
            continue
    return out


# ================================================================
# 指标序列（口径与 technical_detail 一致）
# ================================================================


def _macd_series(closes):
    """MACD(12,26,9) → (dif, dea)。closes 需 ≥35 根。"""
    ema12 = _ema_list(closes, 12)
    ema26 = _ema_list(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema_list(dif, 9)
    return dif, dea


def _kdj_series(highs, lows, closes, n=9):
    """KDJ(9,3,3) 序列 → (k, d, j)。种子 k=d=50，与 technical_detail._kdj 一致。"""
    k_prev = 50.0
    d_prev = 50.0
    ks, ds, js = [], [], []
    for i in range(len(closes)):
        window_high = max(highs[max(0, i - n + 1): i + 1])
        window_low = min(lows[max(0, i - n + 1): i + 1])
        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100.0
        k_prev = 2.0 / 3.0 * k_prev + 1.0 / 3.0 * rsv
        d_prev = 2.0 / 3.0 * d_prev + 1.0 / 3.0 * k_prev
        ks.append(k_prev)
        ds.append(d_prev)
        js.append(3.0 * k_prev - 2.0 * d_prev)
    return ks, ds, js


def _rsi_series(closes, n=14):
    """Wilder RSI 序列（首个值位于下标 n，需 n+1 根）。"""
    if len(closes) < n + 1:
        return []
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / n
    avg_loss = losses / n
    rsis = [None] * n
    rsis.append(100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(n + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
        rsis.append(100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss))
    return rsis


# ================================================================
# 信号检测
# ================================================================


def detect_signals(kline_rows, wanted=None, window=3):
    """对单只股票的K线序列检测信号。

    kline_rows: fetch_kline 输出（时间正序）
    wanted: 信号 key 列表（None=全部）
    window: 触发窗口（最近 N 个交易日内发生的交叉；状态类看最新一根）

    Returns: [{'signal', 'label', 'trigger_date', 'note'}]
    """
    wanted = set(wanted) if wanted else set(SIGNAL_LIBRARY.keys())
    if len(kline_rows) < 35:
        return []
    closes = [r['close'] for r in kline_rows]
    highs = [r['high'] for r in kline_rows]
    lows = [r['low'] for r in kline_rows]
    dates = [r['date'] for r in kline_rows]
    n = len(closes)
    hits = []

    def _within(i):
        return i >= n - window

    # ---- MACD 交叉类 ----
    if wanted & {'macd_golden_above', 'macd_golden_below', 'macd_dead'}:
        dif, dea = _macd_series(closes)
        for i in range(n - window, n):
            if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
                key = 'macd_golden_above' if dif[i] > 0 else 'macd_golden_below'
                if key in wanted:
                    lib = SIGNAL_LIBRARY[key]
                    hits.append({'signal': key, 'label': lib['label'],
                                 'trigger_date': dates[i], 'note': lib['note']})
            elif dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
                if 'macd_dead' in wanted:
                    lib = SIGNAL_LIBRARY['macd_dead']
                    hits.append({'signal': 'macd_dead', 'label': lib['label'],
                                 'trigger_date': dates[i], 'note': lib['note']})

    # ---- KDJ ----
    if wanted & {'kdj_golden_low', 'kdj_golden', 'kdj_oversold', 'kdj_overbought'}:
        ks, ds, js = _kdj_series(highs, lows, closes)
        low_hit_at = None
        for i in range(n - window, n):
            if ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]:
                if ds[i - 1] < 25:
                    low_hit_at = i
                    break  # 低位金叉优先于普通金叉，同窗不重复报
        if low_hit_at is not None:
            if 'kdj_golden_low' in wanted:
                lib = SIGNAL_LIBRARY['kdj_golden_low']
                hits.append({'signal': 'kdj_golden_low', 'label': lib['label'],
                             'trigger_date': dates[low_hit_at], 'note': lib['note']})
        else:
            for i in range(n - window, n):
                if ks[i - 1] <= ds[i - 1] and ks[i] > ds[i]:
                    if 'kdj_golden' in wanted:
                        lib = SIGNAL_LIBRARY['kdj_golden']
                        hits.append({'signal': 'kdj_golden', 'label': lib['label'],
                                     'trigger_date': dates[i], 'note': lib['note']})
                    break
        if 'kdj_oversold' in wanted and (js[-1] < 0 or ks[-1] < 20):
            lib = SIGNAL_LIBRARY['kdj_oversold']
            hits.append({'signal': 'kdj_oversold', 'label': lib['label'],
                         'trigger_date': dates[-1], 'note': lib['note']})
        if 'kdj_overbought' in wanted and (js[-1] > 100 or ks[-1] > 80):
            lib = SIGNAL_LIBRARY['kdj_overbought']
            hits.append({'signal': 'kdj_overbought', 'label': lib['label'],
                         'trigger_date': dates[-1], 'note': lib['note']})

    # ---- RSI 状态类 ----
    if wanted & {'rsi_oversold', 'rsi_overbought'}:
        rsis = _rsi_series(closes)
        if rsis and rsis[-1] is not None:
            if 'rsi_oversold' in wanted and rsis[-1] < 30:
                lib = SIGNAL_LIBRARY['rsi_oversold']
                hits.append({'signal': 'rsi_oversold', 'label': lib['label'],
                             'trigger_date': dates[-1], 'note': lib['note']})
            if 'rsi_overbought' in wanted and rsis[-1] > 70:
                lib = SIGNAL_LIBRARY['rsi_overbought']
                hits.append({'signal': 'rsi_overbought', 'label': lib['label'],
                             'trigger_date': dates[-1], 'note': lib['note']})

    return hits


# ================================================================
# 快照存取 + 筛选引擎
# ================================================================


def save_snapshot(rows):
    """快照整表替换（仅存最新一轮）。"""
    now = datetime.now().isoformat(timespec='seconds')
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM market_snapshot')
    cursor.executemany(
        'INSERT OR REPLACE INTO market_snapshot (symbol, code, name, board, price, '
        'change_pct, turnover, amount, mkt_cap, nmc_cap, pe, pb, volume_ratio, industry, '
        'snapshot_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [
            (r['symbol'], r['code'], r['name'], r.get('board'), r.get('price'),
             r.get('change_pct'), r.get('turnover'), r.get('amount'), r.get('mkt_cap'),
             r.get('nmc_cap'), r.get('pe'), r.get('pb'), r.get('volume_ratio'),
             r.get('industry'), now)
            for r in rows
        ],
    )
    conn.commit()
    conn.close()
    return now


def load_snapshot(max_age_hours=6):
    """读取库内快照。过期（超 max_age_hours）返回 None 提示前端刷新。"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM market_snapshot')
        n = cursor.fetchone()[0]
        if n == 0:
            conn.close()
            return None
        cursor.execute('SELECT MAX(snapshot_at) FROM market_snapshot')
        snapshot_at = cursor.fetchone()[0]
        cursor.execute(
            'SELECT symbol, code, name, board, price, change_pct, turnover, amount, '
            'mkt_cap, nmc_cap, pe, pb, volume_ratio, industry '
            'FROM market_snapshot ORDER BY mkt_cap DESC')
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        age_hours = None
        try:
            dt = datetime.fromisoformat(str(snapshot_at))
            age_hours = (datetime.now() - dt).total_seconds() / 3600
        except (TypeError, ValueError):
            pass
        return {'rows': rows, 'snapshot_at': str(snapshot_at),
                'stale': age_hours is not None and age_hours > max_age_hours}
    except Exception as e:  # noqa: BLE001
        logger.error(f'[扫描器] 快照读取失败: {e}')
        return None


def apply_filters(rows, filters=None):
    """筛选引擎（纯本地）。filters 见 DEFAULT_HYGIENE + 可选项。

    Returns: (filtered_rows, stats)
    """
    f = filters or {}
    stats = {'input': len(rows), 'after_st': None, 'after_mktcap': None, 'after_nmc': None,
             'after_turnover': None, 'after_board': None, 'after_industry': None,
             'after_change': None, 'after_vratio': None}

    out = rows
    if f.get('exclude_st', DEFAULT_HYGIENE['exclude_st']):
        out = [r for r in out if 'ST' not in str(r.get('name') or '').upper()]
    stats['after_st'] = len(out)

    mkt_min = f.get('mkt_cap_min', DEFAULT_HYGIENE['mkt_cap_min'])
    mkt_max = f.get('mkt_cap_max')
    if mkt_min is not None:
        out = [r for r in out if (r.get('mkt_cap') or 0) >= mkt_min]
    if mkt_max is not None:
        out = [r for r in out if (r.get('mkt_cap') or 0) <= mkt_max]
    stats['after_mktcap'] = len(out)

    # 021BI 跟进：流通市值范围（亿）
    nmc_min = f.get('nmc_cap_min')
    nmc_max = f.get('nmc_cap_max')
    if nmc_min is not None:
        out = [r for r in out if (r.get('nmc_cap') or 0) >= nmc_min]
    if nmc_max is not None:
        out = [r for r in out if (r.get('nmc_cap') or 0) <= nmc_max]
    stats['after_nmc'] = len(out)

    t_min = f.get('turnover_min', DEFAULT_HYGIENE['turnover_min'])
    t_max = f.get('turnover_max')
    if t_min is not None:
        out = [r for r in out if (r.get('turnover') or 0) >= t_min]
    if t_max is not None:
        out = [r for r in out if (r.get('turnover') or 0) <= t_max]
    stats['after_turnover'] = len(out)

    boards = f.get('boards')
    if boards:
        out = [r for r in out if r.get('board') in boards]
    stats['after_board'] = len(out)

    industries = f.get('industries')
    if industries:
        out = [r for r in out if r.get('industry') in industries]
    stats['after_industry'] = len(out)

    c_min, c_max = f.get('change_pct_min'), f.get('change_pct_max')
    if c_min is not None:
        out = [r for r in out if (r.get('change_pct') or -999) >= c_min]
    if c_max is not None:
        out = [r for r in out if (r.get('change_pct') or 999) <= c_max]
    stats['after_change'] = len(out)

    vr_min = f.get('volume_ratio_min')
    vr_max = f.get('volume_ratio_max')
    if vr_min is not None:
        out = [r for r in out if (r.get('volume_ratio') or 0) >= vr_min]
    if vr_max is not None:
        out = [r for r in out if (r.get('volume_ratio') or 0) <= vr_max]
    stats['after_vratio'] = len(out)

    sort_key = f.get('sort_key') or 'mkt_cap'
    reverse = f.get('sort_desc', True)
    out = sorted(out, key=lambda r: (r.get(sort_key) or 0), reverse=reverse)
    return out, stats


def run_coarse_scan(filters=None, refresh=False, enrich_top=2000):
    """第①段主入口：快照（缓存或重拉）→ 行业回填 → 筛选 → 增强量比。

    Returns: {'available', 'snapshot_at', 'stats', 'rows', 'universe', 'error'}
    rows 为筛选后结果。量比增强覆盖量比筛选前的全部存活者（上限 enrich_top，
    腾讯 60 码/批：2000 只 ≈ 34 批 ≈ 25s），使量比范围筛选作用于全量而非前300。
    """
    try:
        snap = None if refresh else load_snapshot()
        if snap is None:
            rows, meta = fetch_sina_snapshot()
            if len(rows) < 1000:
                return {'available': False,
                        'error': f'快照数量异常（{len(rows)} 只），疑似数据源变更',
                        'meta': meta}
            try:
                ind_map = fetch_industry_map()
                for r in rows:
                    ind = ind_map.get(r['symbol'])
                    # fetch 返回 {symbol: (industry, node)}；库缓存回退返回 {symbol: industry}
                    r['industry'] = ind[0] if isinstance(ind, tuple) else ind
            except Exception as e:  # noqa: BLE001
                logger.warning(f'[扫描器] 行业映射失败（本轮无行业列）: {e}')
            snapshot_at = save_snapshot(rows)
            snap = {'rows': rows, 'snapshot_at': snapshot_at, 'stale': False}

        # 第一步：除量比外的全部条件（量比需先增强再筛）
        pre_filters = {k: v for k, v in (filters or {}).items()
                       if not k.startswith('volume_ratio')}
        filtered, stats = apply_filters(snap['rows'], pre_filters)
        # 第二步：量比增强（覆盖全部存活者，≤enrich_top）
        enrich_syms = [r['symbol'] for r in filtered[:enrich_top]]
        if enrich_syms:
            enriched = fetch_tencent_enrich(enrich_syms)
            for r in filtered[:enrich_top]:
                extra = enriched.get(r['symbol'])
                if extra:
                    r['volume_ratio'] = extra.get('volume_ratio')
        # 第三步：量比范围筛选（增强后补跑）
        if filters and (filters.get('volume_ratio_min') is not None
                        or filters.get('volume_ratio_max') is not None):
            filtered, stats = apply_filters(filtered, filters)

        return {'available': True, 'snapshot_at': snap['snapshot_at'],
                'stale': snap.get('stale', False), 'stats': stats,
                'universe': len(snap['rows']),
                'rows': filtered[:500]}
    except Exception as e:  # noqa: BLE001
        logger.error(f'[扫描器] 粗筛失败: {e}', exc_info=True)
        return {'available': False, 'error': f'粗筛失败: {e}'}


def run_signal_chunk(entries, signals=None, window=3):
    """第②段单批：对 ≤50 只候选逐票拉K线检测信号。

    entries: [{'symbol', 'name'}]
    Returns: {'results': [{symbol, name, matches: [...]}], 'errors': [...]}
    """
    if len(entries) > 50:
        entries = entries[:50]
    wanted = list(signals) if signals else list(SIGNAL_LIBRARY.keys())
    results = []
    errors = []
    for ent in entries:
        sym = ent.get('symbol')
        try:
            klines = fetch_kline(sym)
            matches = detect_signals(klines, wanted=wanted, window=window)
            if matches:
                results.append({'symbol': sym, 'name': ent.get('name') or '',
                                'matches': matches,
                                'resonances': detect_resonances(matches, klines)})
        except Exception as e:  # noqa: BLE001
            errors.append({'symbol': sym, 'error': str(e)[:120]})
    return {'results': results, 'errors': errors}
