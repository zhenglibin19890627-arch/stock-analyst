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
  - 021BQ：卖出侧走平行库（SELL_SIGNAL_LIBRARY/SELL_RESONANCE_LIBRARY 与
    detect_sell_signals/detect_sell_resonances），只服务自选股持仓风险链路
    （巡检/预警/行动清单）——在线扫描（run_signal_chunk/run_coarse_scan）
    仍只产买点，往 SIGNAL_LIBRARY/RESONANCE_LIBRARY 加卖侧 key 会泄漏进
    全市场扫描，严禁（2026-09-07 重设计边界）。
"""

import json
import logging
import re
import time
from datetime import datetime

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
    raise last_exc


# ================================================================
# 信号库（2026-09-07 共振重设计：收缩为 4 个触发类金叉信号）
# 删除依据：死叉/超买/超卖是状态或反向信号，不产"买入候选"——
#   死叉股不会出现在金叉结果里；纯超卖=接飞刀半成品（等企稳）。
#   超卖语义由 kdj_golden_low（D<25）与共振层的环境注记承接。
# ================================================================

SIGNAL_LIBRARY = {
    'macd_golden_above': {'label': 'MACD水上金叉', 'note': 'DIF上穿DEA且DIF>0：多头趋势中的加速信号，相对可靠'},
    'macd_golden_below': {'label': 'MACD水下金叉', 'note': 'DIF上穿DEA且DIF≤0：下跌趋势中的反弹信号，需配合量能，偏短线'},
    'kdj_golden_low': {'label': 'KDJ低位金叉', 'note': 'K上穿D且交叉时D低于25：超卖区反转，信号中较可靠的买点'},
    'kdj_golden': {'label': 'KDJ金叉', 'note': 'K上穿D：一般买点参考'},
}

DEFAULT_HYGIENE = {
    'exclude_st': True,        # 剔除 ST/*ST
    'exclude_bj': True,        # 剔除北交所（解析层已按代码过滤）
    'mkt_cap_min': 100.0,      # 总市值 ≥100 亿
    'turnover_min': 1.0,       # 换手率 ≥1%
}

# ================================================================
# 共振库（2026-09-07 重设计：按"共振组合"口径重构为 4 组买点共振）
#   ① 双金叉共振        MACD金叉 + KDJ金叉同窗（同日 ⭐4 / 同窗 ⭐3）
#   ② 周线共振波段      周线MACD多头(DIF>DEA) + 日线窗口金叉（跨周期共振）
#   ③ 底部反转共振      底背离(价格60日新低而DIF未新低) + KDJ低位金叉 + 放量阳线
#   ④ 零轴上二次金叉    近15日MACD第2次金叉且DIF>0 + KDJ中位(D 40~70)金叉
# 已删：空头共振预警（选股器不产卖点，持仓风险归预警系统）、超卖观察池（接飞刀）。
# 兼容说明：用户需求表中"日线金叉+60分钟金叉"因 60 分钟K线未采集不实现，
#   跨周期共振位由②承接（周线数据对触发股按需补拉腾讯周K）。
# ================================================================

RESONANCE_LIBRARY = {
    'res_double_golden': {
        'label': '双金叉共振', 'stars': 4, 'kind': 'bull',
        'note': 'MACD系金叉+KDJ系金叉同窗：两个独立指标系同向触发；同日触发更佳'},
    'res_week_daily': {
        'label': '周线共振波段', 'stars': 5, 'kind': 'bull',
        'note': '周线MACD多头(DIF>DEA)+日线窗口金叉：月线定方向周线定买点，中线波段结构'},
    'res_bottom_reverse': {
        'label': '底部反转共振', 'stars': 5, 'kind': 'bull',
        'note': '底背离(价格创60日新低而DIF未新低)+KDJ低位金叉+放量阳线：左侧反转最强确认'},
    'res_zero_relay': {
        'label': '零轴上二次金叉', 'stars': 5, 'kind': 'bull',
        'note': '近15日MACD第二次金叉且DIF>0+KDJ中位金叉：主升浪中继的经典买点'},
}

_MACD_BULL = ('macd_golden_above', 'macd_golden_below')
_KDJ_BULL = ('kdj_golden_low', 'kdj_golden')
_ALL_BULL = _MACD_BULL + _KDJ_BULL
# 买点型共振取最高档（先到先得）：周线共振 > 底部反转 > 二次金叉 > 双金叉
_RES_TIER_ORDER = ('res_week_daily', 'res_bottom_reverse', 'res_zero_relay', 'res_double_golden')


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


def detect_resonances(hits, kline_rows=None, weekly_kline_rows=None):
    """从单只股票的信号命中推导共振组合（2026-09-07 重设计）。

    hits: detect_signals 输出；kline_rows: 日K（背离/放量/二次金叉证据）；
    weekly_kline_rows: 周K（可选，周线共振波段用，由调用方按需补拉）。
    Returns: [{'key', 'label', 'stars', 'kind', 'note', 'signals': [label@date...]}]
    买点型取最高档（周线共振 > 底部反转 > 二次金叉 > 双金叉）。
    """
    if not hits:
        return []
    keys = {h['signal'] for h in hits}
    macd_bull = any(k in keys for k in _MACD_BULL)
    kdj_bull = any(k in keys for k in _KDJ_BULL)
    if not (macd_bull or kdj_bull):
        return []

    # 环境注记（不定级）：RSI 超卖环境 + MA20 位置
    env = []
    if kline_rows and len(kline_rows) >= 20:
        closes = [r['close'] for r in kline_rows]
        rsis = _rsi_series(closes)
        if rsis and rsis[-1] is not None and rsis[-1] < 30:
            env.append('RSI超卖环境')
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

    found = {}

    # ① 双金叉共振：MACD系 + KDJ系同窗（同日触发 ⭐4，跨日同窗 ⭐3）
    if macd_bull and kdj_bull:
        dates = {h['trigger_date'] for h in hits if h['signal'] in _ALL_BULL}
        res = _res('res_double_golden')
        res['stars'] = 4 if len(dates) == 1 else 3
        if len(dates) == 1:
            res['note'] = '同日双金叉（MACD+KDJ同日触发）：标准买入共振' + env_str
        found['res_double_golden'] = res

    if kline_rows and len(kline_rows) >= 35:
        closes = [r['close'] for r in kline_rows]
        n = len(closes)

        # ② 周线共振波段：周线 MACD 多头（DIF>DEA）+ 日线窗口内金叉
        if weekly_kline_rows and len(weekly_kline_rows) >= 35:
            wdif, wdea = _macd_series([r['close'] for r in weekly_kline_rows])
            if wdif[-1] > wdea[-1]:
                found['res_week_daily'] = _res('res_week_daily')

        # ③ 底部反转共振：底背离 + KDJ低位金叉 + 放量阳线
        if 'kdj_golden_low' in keys and len(kline_rows) >= 60:
            dif, _dea = _macd_series(closes)
            look = 60
            seg = closes[-look:]
            pmin_off = seg.index(min(seg))
            recent_low = pmin_off >= look - 5            # 价格新低出现在近5根内
            dif_seg = dif[-look:]
            divergence = recent_low and dif_seg[pmin_off] > min(dif_seg) + 1e-9
            last = kline_rows[-1]
            prev_vols = [r['volume'] for r in kline_rows[-6:-1]]
            vol_yang = (last['close'] > last['open'] and prev_vols
                        and last['volume'] >= 1.5 * (sum(prev_vols) / len(prev_vols)))
            if divergence and vol_yang:
                found['res_bottom_reverse'] = _res('res_bottom_reverse')

        # ④ 零轴上二次金叉：近15日第2次金叉且 DIF>0 + KDJ中位金叉
        if len(closes) >= 50:
            dif, dea = _macd_series(closes)
            golden2 = [i for i in range(n - 15, n)
                       if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i] and dif[i] > 0]
            if len(golden2) >= 2:
                highs = [r['high'] for r in kline_rows]
                lows = [r['low'] for r in kline_rows]
                ks, ds, _js = _kdj_series(highs, lows, closes)
                mid_golden = any(
                    ks[i - 1] <= ds[i - 1] and ks[i] > ds[i] and 40 <= ds[i] <= 70
                    for i in range(n - 15, n))
                if mid_golden:
                    found['res_zero_relay'] = _res('res_zero_relay')

    # 买点型取最高档
    for key in _RES_TIER_ORDER:
        if key in found:
            return [found[key]]
    return []


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
    return _fetch_tencent_kline(symbol, period='day', key_prefix='qfqday', count=count)


def fetch_kline_weekly(symbol, count=60):
    """腾讯周K（前复权）→ 同日K结构。周线共振波段专用（对触发股按需补拉）。"""
    return _fetch_tencent_kline(symbol, period='week', key_prefix='qfqweek', count=count)


def _fetch_tencent_kline(symbol, period, key_prefix, count):
    """腾讯K线通用拉取（period: day/week）。"""
    r = _http_get(
        'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
        params={'param': f'{symbol},{period},,,{count},qfq'},
        headers=_UA_QQ, channel='qq', timeout=10)
    d = r.json()
    node = (d.get('data') or {}).get(symbol) or {}
    days = node.get(key_prefix) or node.get(period) or []
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

    # ---- MACD 金叉类（死叉已随 2026-09-07 共振重设计移除：选股器不产卖点） ----
    if wanted & {'macd_golden_above', 'macd_golden_below'}:
        dif, dea = _macd_series(closes)
        for i in range(n - window, n):
            if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
                key = 'macd_golden_above' if dif[i] > 0 else 'macd_golden_below'
                if key in wanted:
                    lib = SIGNAL_LIBRARY[key]
                    hits.append({'signal': key, 'label': lib['label'],
                                 'trigger_date': dates[i], 'note': lib['note']})

    # ---- KDJ 金叉类（超买/超卖为状态类，已移出信号库） ----
    if wanted & {'kdj_golden_low', 'kdj_golden'}:
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

    return hits


# ================================================================
# 卖出侧信号库（021BQ 决策闭环：平行库，与买侧同构镜像）
#   边界（2026-09-07 重设计 + 021BQ 方案裁定）：选股器只产买点、持仓风险
#   归预警——本库与 detect_sell_* 系列只服务自选股持仓风险链路（巡检/
#   预警/行动清单/操盘手矩阵），严禁并入 SIGNAL_LIBRARY/RESONANCE_LIBRARY
#   （会泄漏进在线全市场扫描 run_signal_chunk 的缺省 wanted）。
#   指标口径复用 _macd_series/_kdj_series/_rsi_series/_ma_series（R7 精神：
#   与 technical_detail 同源口径不出现第二实现）。
# ================================================================

SELL_SIGNAL_LIBRARY = {
    'macd_dead_above': {'label': 'MACD水上死叉', 'note': 'DIF下穿DEA且DIF>0：多头趋势中的派发/回调信号，持仓者减仓警示'},
    'macd_dead_below': {'label': 'MACD水下死叉', 'note': 'DIF下穿DEA且DIF≤0：空头趋势加速信号，弱势反弹结束'},
    'kdj_dead_high': {'label': 'KDJ高位死叉', 'note': 'K下穿D且交叉时D高于75：超买区回落，信号中较可靠的卖点'},
    'kdj_dead': {'label': 'KDJ死叉', 'note': 'K下穿D：一般卖点参考'},
    'ma20_break': {'label': '破位MA20', 'note': '收盘价自MA20上方跌破MA20（1%缓冲防毛刺）：短线趋势破位事件'},
}

SELL_RESONANCE_LIBRARY = {
    'res_double_dead': {
        'label': '双死叉共振', 'stars': 4, 'kind': 'bear',
        'note': 'MACD系死叉+KDJ系死叉同窗：两个独立指标系同向下行；同日触发更佳'},
    'res_week_bear': {
        'label': '周线空头波段卖', 'stars': 5, 'kind': 'bear',
        'note': '周线MACD空头(DIF<DEA)+日线窗口死叉：下跌趋势顺势卖点，持仓者警惕继续走弱'},
    'res_top_reverse': {
        'label': '顶背离反转卖', 'stars': 5, 'kind': 'bear',
        'note': '顶背离(价格创60日新高而DIF未新高)+KDJ高位死叉：顶部反转最强确认'},
}

_MACD_BEAR = ('macd_dead_above', 'macd_dead_below')
_KDJ_BEAR = ('kdj_dead_high', 'kdj_dead')
_ALL_BEAR = _MACD_BEAR + _KDJ_BEAR
# 卖点型共振取最高档（先到先得）：顶背离反转 ≥ 周线空头 ≥ 双死叉
_SELL_RES_TIER_ORDER = ('res_top_reverse', 'res_week_bear', 'res_double_dead')


def detect_sell_signals(kline_rows, wanted=None, window=3):
    """对单只股票的K线序列检测卖出侧信号（detect_signals 的同构镜像）。

    kline_rows: screener kline 行（时间正序，结构同 fetch_kline 输出）
    wanted: 信号 key 列表（None=全部 SELL_SIGNAL_LIBRARY）
    window: 触发窗口（最近 N 个交易日内发生的交叉/破位事件）

    Returns: [{'signal', 'label', 'trigger_date', 'note'}]
    """
    wanted = set(wanted) if wanted else set(SELL_SIGNAL_LIBRARY.keys())
    if len(kline_rows) < 35:
        return []
    closes = [r['close'] for r in kline_rows]
    highs = [r['high'] for r in kline_rows]
    lows = [r['low'] for r in kline_rows]
    dates = [r['date'] for r in kline_rows]
    n = len(closes)
    hits = []

    # ---- MACD 死叉类（金叉判定的逐条件反向） ----
    if wanted & {'macd_dead_above', 'macd_dead_below'}:
        dif, dea = _macd_series(closes)
        for i in range(n - window, n):
            if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
                key = 'macd_dead_above' if dif[i] > 0 else 'macd_dead_below'
                if key in wanted:
                    lib = SELL_SIGNAL_LIBRARY[key]
                    hits.append({'signal': key, 'label': lib['label'],
                                 'trigger_date': dates[i], 'note': lib['note']})

    # ---- KDJ 死叉类（高位死叉优先于普通死叉，同窗不重复报） ----
    if wanted & {'kdj_dead_high', 'kdj_dead'}:
        ks, ds, _js = _kdj_series(highs, lows, closes)
        high_hit_at = None
        for i in range(n - window, n):
            if ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]:
                if ds[i - 1] > 75:
                    high_hit_at = i
                    break  # 高位死叉优先于普通死叉，同窗不重复报
        if high_hit_at is not None:
            if 'kdj_dead_high' in wanted:
                lib = SELL_SIGNAL_LIBRARY['kdj_dead_high']
                hits.append({'signal': 'kdj_dead_high', 'label': lib['label'],
                             'trigger_date': dates[high_hit_at], 'note': lib['note']})
        else:
            for i in range(n - window, n):
                if ks[i - 1] >= ds[i - 1] and ks[i] < ds[i]:
                    if 'kdj_dead' in wanted:
                        lib = SELL_SIGNAL_LIBRARY['kdj_dead']
                        hits.append({'signal': 'kdj_dead', 'label': lib['label'],
                                     'trigger_date': dates[i], 'note': lib['note']})
                    break

    # ---- 破位 MA20（事件口径：前收在 MA20 上方、今收跌破 MA20×1.01 缓冲线） ----
    if 'ma20_break' in wanted:
        mas = _ma_series(closes, 20)
        for i in range(n - window, n):
            if mas[i] is None or mas[i - 1] is None:
                continue
            if closes[i - 1] >= mas[i - 1] and closes[i] < mas[i] * 1.01:
                lib = SELL_SIGNAL_LIBRARY['ma20_break']
                hits.append({'signal': 'ma20_break', 'label': lib['label'],
                             'trigger_date': dates[i], 'note': lib['note']})

    return hits


def detect_sell_resonances(hits, kline_rows=None, weekly_kline_rows=None):
    """从单只股票的卖出信号命中推导 bear 共振组合（detect_resonances 的同构镜像）。

    hits: detect_sell_signals 输出；kline_rows: 日K（顶背离/放量滞涨证据）；
    weekly_kline_rows: 周K（可选，周线空头波段卖用）。
    Returns: [{'key', 'label', 'stars', 'kind', 'note', 'signals': 'label@date + ...'}]
    卖点型取最高档（顶背离反转 > 周线空头 > 双死叉）。
    """
    if not hits:
        return []
    keys = {h['signal'] for h in hits}
    macd_bear = any(k in keys for k in _MACD_BEAR)
    kdj_bear = any(k in keys for k in _KDJ_BEAR)
    if not (macd_bear or kdj_bear):
        return []

    # 环境注记（不定级）：RSI 超买环境 + MA20 位置
    env = []
    if kline_rows and len(kline_rows) >= 20:
        closes = [r['close'] for r in kline_rows]
        rsis = _rsi_series(closes)
        if rsis and rsis[-1] is not None and rsis[-1] > 70:
            env.append('RSI超买环境')
        ma20 = _ma_series(closes, 20)[-1]
        if ma20:
            env.append('MA20上方' if closes[-1] >= ma20 else 'MA20下方')
    env_str = ('（' + '·'.join(env) + '）') if env else ''

    sig_desc = ' + '.join(f"{h['label']}@{h['trigger_date']}" for h in hits)

    def _res(key):
        lib = SELL_RESONANCE_LIBRARY[key]
        return {'key': key, 'label': lib['label'], 'stars': lib['stars'],
                'kind': lib['kind'], 'note': lib['note'] + env_str,
                'signals': sig_desc}

    found = {}

    # ① 双死叉共振：MACD系 + KDJ系同窗（同日触发 ⭐4，跨日同窗 ⭐3）
    if macd_bear and kdj_bear:
        dates = {h['trigger_date'] for h in hits if h['signal'] in _ALL_BEAR}
        res = _res('res_double_dead')
        res['stars'] = 4 if len(dates) == 1 else 3
        if len(dates) == 1:
            res['note'] = '同日双死叉（MACD+KDJ同日触发）：标准卖出共振' + env_str
        found['res_double_dead'] = res

    if kline_rows and len(kline_rows) >= 35:
        closes = [r['close'] for r in kline_rows]

        # ② 周线空头波段卖：周线 MACD 空头（DIF<DEA）+ 日线窗口内死叉
        if weekly_kline_rows and len(weekly_kline_rows) >= 35:
            wdif, wdea = _macd_series([r['close'] for r in weekly_kline_rows])
            if wdif[-1] < wdea[-1]:
                found['res_week_bear'] = _res('res_week_bear')

        # ③ 顶背离反转卖：顶背离 + KDJ高位死叉（可选放量滞涨注记）
        if 'kdj_dead_high' in keys and len(kline_rows) >= 60:
            dif, _dea = _macd_series(closes)
            look = 60
            seg = closes[-look:]
            pmax_off = seg.index(max(seg))
            recent_high = pmax_off >= look - 5            # 价格新高出现在近5根内
            dif_seg = dif[-look:]
            divergence = recent_high and dif_seg[pmax_off] < max(dif_seg) - 1e-9
            if divergence:
                res = _res('res_top_reverse')
                last = kline_rows[-1]
                prev_vols = [r['volume'] for r in kline_rows[-6:-1]]
                vol_stall = (bool(prev_vols)
                             and last['volume'] >= 1.5 * (sum(prev_vols) / len(prev_vols))
                             and last['close'] <= last['open'])
                if vol_stall:
                    res['note'] += '·放量滞涨'
                found['res_top_reverse'] = res

    # 卖点型取最高档
    for key in _SELL_RES_TIER_ORDER:
        if key in found:
            return [found[key]]
    return []


# ================================================================
# 自选股信号离线复算（021BP 决策闭环 项1；021BQ 起含卖出侧平行复算）
#   复用上方信号纯函数（detect_signals/detect_resonances），输入改为
#   自选股已采集的库内 K 线（raw_kline / raw_kline_weekly），零网络。
#   口径：与在线扫描同为"快照参考"——结果截止最新已采集K线（kline_upto），
#   不入库、不写评分/评级表（只产候选展示，与扫描器同一边界）。
# ================================================================

# 日K读取上限：KLINE_DAYS 全量口径。评分路径 _read_kline_data 的 limit=60
# 仅够共振③(60根)/④(50根)下限，指标预热长度不足——本处自读 250 根，
# 与评分读取完全解耦、互不影响。
_WATCHLIST_DAILY_LIMIT = 250


def _rows_to_screener_klines(rows):
    """raw_kline* 行 → screener kline 行（trade_date→date 唯一改名映射）。

    OHLC 任一缺失的行剔除（评分适配层同口径），volume 缺失按 0 处理。"""
    out = []
    for row in rows:
        d = dict(row)
        if any(d.get(k) is None for k in ('open', 'close', 'high', 'low')):
            continue
        out.append({
            'date': str(d['trade_date']),
            'open': float(d['open']),
            'close': float(d['close']),
            'high': float(d['high']),
            'low': float(d['low']),
            'volume': float(d['volume'] or 0.0),
        })
    return out


def _read_watchlist_klines(cursor, stock_id, daily_limit=_WATCHLIST_DAILY_LIMIT):
    """自选股已采集K线 → screener 行格式（时间正序，零网络）。

    日K取最近 daily_limit 根；周K取 raw_kline_weekly 全量（周线共振用）。
    传入外部 cursor（预警扫描/巡检端点各自持有连接，避免二次连接锁竞争）。
    Returns: (daily_rows, weekly_rows)
    """
    cursor.execute(
        'SELECT trade_date, open, close, high, low, volume '
        'FROM raw_kline WHERE stock_id=? ORDER BY trade_date DESC LIMIT ?',
        (stock_id, daily_limit),
    )
    daily = _rows_to_screener_klines(reversed(cursor.fetchall()))
    cursor.execute(
        'SELECT trade_date, open, close, high, low, volume '
        'FROM raw_kline_weekly WHERE stock_id=? ORDER BY trade_date ASC',
        (stock_id,),
    )
    weekly = _rows_to_screener_klines(cursor.fetchall())
    return daily, weekly


def compute_watchlist_signal_result(kline_rows, weekly_rows=None, wanted=None, window=3):
    """单只股票离线复算：信号 + 共振（纯函数包装，不触库不触网）。

    Returns: {'matches', 'resonances', 'kline_upto', 'kline_count'}；
    K线不足 35 根时 matches 为空列表（detect_signals 门槛），kline_upto 仍回报
    供调用方判断数据新鲜度。
    """
    matches = detect_signals(kline_rows, wanted=wanted, window=window)
    resonances = detect_resonances(matches, kline_rows, weekly_rows)
    return {
        'matches': matches,
        'resonances': resonances,
        'kline_upto': str(kline_rows[-1]['date']) if kline_rows else None,
        'kline_count': len(kline_rows),
    }


def scan_watchlist_signals(stock_ids=None, signals=None, window=3,
                           daily_limit=_WATCHLIST_DAILY_LIMIT):
    """自选股买点信号巡检（只读离线复算，零网络）。

    stock_ids 缺省 = 全部 active 自选股（≤100 只，纯读库+纯函数，毫秒级/只）。
    与 run_signal_chunk（在线第②段）收录口径一致：仅收录有信号/共振命中的股票。

    Returns: {
        'scope': 'watchlist_offline',   # 快照参考口径标注（区别于在线扫描）
        'stock_count': N,               # 巡检股票数
        'results': [{stock_id, symbol, name, matches, resonances,
                     kline_upto, kline_count}],
        'errors': [{stock_id, error}],
    }
    """
    wanted = list(signals) if signals else list(SIGNAL_LIBRARY.keys())
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, symbol, name FROM stocks WHERE status='active' ORDER BY id")
        stocks = [dict(r) for r in cursor.fetchall()]
        if stock_ids:
            wanted_ids = {int(s) for s in stock_ids}
            stocks = [s for s in stocks if s['id'] in wanted_ids]

        results = []
        errors = []
        for s in stocks:
            try:
                daily, weekly = _read_watchlist_klines(cursor, s['id'], daily_limit=daily_limit)
                item = compute_watchlist_signal_result(daily, weekly, wanted=wanted, window=window)
                if item['matches'] or item['resonances']:
                    results.append({
                        'stock_id': s['id'],
                        'symbol': s['symbol'] or '',
                        'name': s['name'] or '',
                        **item,
                    })
            except Exception as e:  # noqa: BLE001 —— 单只失败不阻塞巡检（与 run_signal_chunk 同型）
                errors.append({'stock_id': s['id'], 'error': str(e)[:120]})
        return {
            'scope': 'watchlist_offline',
            'stock_count': len(stocks),
            'results': results,
            'errors': errors,
        }
    finally:
        conn.close()


def compute_watchlist_sell_result(kline_rows, weekly_rows=None, wanted=None, window=3):
    """单只股票卖出侧离线复算：卖点信号 + bear 共振（纯函数包装，不触库不触网）。

    Returns: {'side', 'sell_matches', 'sell_resonances', 'kline_upto', 'kline_count'}；
    K线不足 35 根时 sell_matches 为空列表（detect_sell_signals 门槛），
    kline_upto 仍回报供调用方判断数据新鲜度。
    """
    sell_matches = detect_sell_signals(kline_rows, wanted=wanted, window=window)
    sell_resonances = detect_sell_resonances(sell_matches, kline_rows, weekly_rows)
    return {
        'side': 'sell',
        'sell_matches': sell_matches,
        'sell_resonances': sell_resonances,
        'kline_upto': str(kline_rows[-1]['date']) if kline_rows else None,
        'kline_count': len(kline_rows),
    }


def scan_watchlist_sell_signals(stock_ids=None, signals=None, window=3,
                                daily_limit=_WATCHLIST_DAILY_LIMIT):
    """自选股卖点信号巡检（只读离线复算，零网络；021BQ 决策闭环）。

    与 scan_watchlist_signals 同构镜像：读库口径/收录口径/异常隔离全部一致，
    仅收录有卖点信号/bear 共振命中的股票。持仓者离场提示的巡检数据源
    （预警 check_sell_signal / 行动清单卖侧行均消费本函数或其纯函数包装）。

    Returns: {
        'scope': 'watchlist_offline',   # 快照参考口径标注（区别于在线扫描）
        'side': 'sell',                 # 卖出侧标注（与买侧巡检区分）
        'stock_count': N,               # 巡检股票数
        'results': [{stock_id, symbol, name, sell_matches, sell_resonances,
                     kline_upto, kline_count}],
        'errors': [{stock_id, error}],
    }
    """
    wanted = list(signals) if signals else list(SELL_SIGNAL_LIBRARY.keys())
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, symbol, name FROM stocks WHERE status='active' ORDER BY id")
        stocks = [dict(r) for r in cursor.fetchall()]
        if stock_ids:
            wanted_ids = {int(s) for s in stock_ids}
            stocks = [s for s in stocks if s['id'] in wanted_ids]

        results = []
        errors = []
        for s in stocks:
            try:
                daily, weekly = _read_watchlist_klines(cursor, s['id'], daily_limit=daily_limit)
                item = compute_watchlist_sell_result(daily, weekly, wanted=wanted, window=window)
                if item['sell_matches'] or item['sell_resonances']:
                    results.append({
                        'stock_id': s['id'],
                        'symbol': s['symbol'] or '',
                        'name': s['name'] or '',
                        **item,
                    })
            except Exception as e:  # noqa: BLE001 —— 单只失败不阻塞巡检（与买侧同型）
                errors.append({'stock_id': s['id'], 'error': str(e)[:120]})
        return {
            'scope': 'watchlist_offline',
            'side': 'sell',
            'stock_count': len(stocks),
            'results': results,
            'errors': errors,
        }
    finally:
        conn.close()


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
                # 周线共振波段：仅对日线已触发金叉的候选补拉周K（漏斗收窄后增量请求很小）
                weekly = None
                try:
                    weekly = fetch_kline_weekly(sym)
                except Exception:  # noqa: BLE001 —— 周K失败只降级为无周线共振
                    weekly = None
                results.append({'symbol': sym, 'name': ent.get('name') or '',
                                'matches': matches,
                                'resonances': detect_resonances(matches, klines, weekly)})
        except Exception as e:  # noqa: BLE001
            errors.append({'symbol': sym, 'error': str(e)[:120]})
    return {'results': results, 'errors': errors}
