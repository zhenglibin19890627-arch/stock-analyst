"""股东结构：基金持仓/机构/股东人数 + 港股 westock shareholder + 持股缓存。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import time
from datetime import datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_westock import _westock_cli_query
from modules.collector.symbols_status import _num_float


def fetch_holder_increase(symbol: str, preloaded_df=None):
    """B10: 获取近30天大股东/高管增减持信息（仅A股）。
    数据源：雪球内部交易接口 stock_inner_trade_xq()
    列结构: 股票代码/股票名称/变动日期/变动人/变动股数/成交均价/变动后持股数/与董监高关系/董监高职务
    增减持方向通过 '变动股数' 正负判断（正=增持，负=减持）

    B11-API-DEDUP：支持预加载数据(preloaded_df)和模块级缓存，避免批量时重复调用全市场接口。

    020R-44 三态语义：
        True  = 近30天有增持（利好）
        False = 近30天无增持（含：有减持、或30天内/全市场无任何披露记录）
        None  = 接口不可用/返回空（数据缺失，评分时该项权重归零）
    """
    global _holder_cache, _holder_cache_time

    try:
        if preloaded_df is not None:
            df = preloaded_df
        elif (
            _holder_cache is not None
            and _holder_cache_time
            and (time.time() - _holder_cache_time) < 600
        ):
            # 10分钟内缓存有效
            df = _holder_cache
        else:
            df = ak.stock_inner_trade_xq()
            _holder_cache = df
            _holder_cache_time = time.time()

        if df is None or df.empty:
            return None
        # 股票代码列含前缀 SZ/SH，如 'SZ000858'
        code_col = '股票代码'
        if code_col not in df.columns:
            return None
        # 过滤本股票
        sub = df[df[code_col].str.contains(symbol, na=False)]
        if sub.empty:
            # 020R-44：全市场无该股任何披露记录 → 视为近30天无增持（False），
            # 与「接口失败(None)」区分开
            return False
        # 过滤近30天
        date_col = '变动日期'
        if date_col not in sub.columns:
            return None
        cutoff = (datetime.now(_CN_TZ) - timedelta(days=30)).strftime('%Y-%m-%d')
        sub = sub[sub[date_col].astype(str) >= cutoff]
        if sub.empty:
            # 020R-44：30天内无变动 → 近30天无增持（False）
            return False
        # 判断方向：通过 '变动股数' 正负判断（正=增持，负=减持）
        shares_col = '变动股数'
        if shares_col not in sub.columns:
            return None
        shares = sub[shares_col].tolist()
        has_increase = any(float(s) > 0 for s in shares if s is not None)
        has_decrease = any(float(s) < 0 for s in shares if s is not None)
        if has_increase and not has_decrease:
            return True
        elif has_decrease and not has_increase:
            return False
        elif has_increase and has_decrease:
            # 既有增持又有减持，按最近一笔判断
            latest_shares = sub.iloc[0][shares_col]
            return float(latest_shares) > 0 if latest_shares is not None else None
        return False
    except Exception as e:
        logger.warning(f'[B10 股东增减持 {symbol}] 接口失败(静默降级): {e}')
        return None


def _save_holder_increase(stock_id: int, holder_increase):
    """B10: 将 holder_increase 写入 raw_fundamental 表（ALTER TABLE 新增列）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 确保列存在（幂等 ALTER TABLE）
    try:
        cursor.execute('ALTER TABLE raw_fundamental ADD COLUMN holder_increase BOOLEAN')
        conn.commit()
        logger.info('[B10] raw_fundamental 表新增 holder_increase 列')
    except Exception:
        pass  # 列已存在
    # 更新最新记录
    if holder_increase is not None:
        cursor.execute(
            'UPDATE raw_fundamental SET holder_increase = ? WHERE stock_id = ? AND report_date = (SELECT MAX(report_date) FROM raw_fundamental WHERE stock_id = ?)',
            (holder_increase, stock_id, stock_id),
        )
        conn.commit()
        logger.info(f'[B10 股东增减持] stock_id={stock_id}, holder_increase={holder_increase}')
    conn.close()


# ============================================================
# 020R-45：股东人数与机构持仓采集（A股专属，资金面-筹码结构）
# ============================================================

HOLD_INST_TYPES = ['基金持仓', 'QFII持仓', '社保持仓', '券商持仓', '保险持仓']  # 020R-45：阳光私募接口不支持，5类

_fund_hold_cache: dict = {}
_fund_hold_cache_time: dict = {}


def _num_or_none(v):
    """宽松数值转换（'-'/''/NaN → None）。"""
    try:
        if v is None or v == '' or v == '-':
            return None
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_fund_hold_dates():
    """候选机构持仓报告期（YYYYMMDD）：今天之前最近若干季度末，从新到旧。"""
    today = datetime.now(_CN_TZ)
    candidates = set()
    for year in (today.year, today.year - 1, today.year - 2):
        for md in ('1231', '0930', '0630', '0331'):
            d = f'{year}{md}'
            if d <= today.strftime('%Y%m%d'):
                candidates.add(d)
    return sorted(candidates, reverse=True)


def _get_fund_hold_table(hold_type, date):
    """取某类机构持仓全市场表（10分钟模块级缓存，批量采集复用）。失败返回 None。"""
    key = (hold_type, date)
    now = time.time()
    cached_at = _fund_hold_cache_time.get(key, 0)
    if key in _fund_hold_cache and cached_at and (now - cached_at) < 600:
        return _fund_hold_cache[key]
    try:
        df = ak.stock_report_fund_hold(symbol=hold_type, date=date)
        _fund_hold_cache[key] = df
        _fund_hold_cache_time[key] = now
        return df
    except Exception as e:
        logger.warning(f'[020R-45 机构持仓] {hold_type} {date} 获取失败: {e}')
        return None


def fetch_holder_structure(symbol: str):
    """020R-45：采集股东人数（东财户数明细）与机构持仓（东财六类机构持股汇总）。

    Returns:
        dict: stat_date / holder_count / holder_count_change_pct / total_shares /
              inst_shares / inst_ratio / inst_report_date
        接口不可用或数据异常时返回 None（静默降级，不阻塞主流程）。
    """
    try:
        gdhs = ak.stock_zh_a_gdhs_detail_em(symbol=symbol)
        if gdhs is None or gdhs.empty:
            return None
        # 020R-45：该接口按日期升序返回，最新一期在最后一行（iloc[-1]）
        latest = gdhs.iloc[-1]
        total_shares = _num_or_none(latest.get('总股本'))
        result = {
            'stat_date': str(latest.get('股东户数统计截止日'))[:10],
            'holder_count': _num_or_none(latest.get('股东户数-本次')),
            'holder_count_change_pct': _num_or_none(latest.get('股东户数-增减比例')),
            'total_shares': total_shares,
            'inst_shares': None,
            'inst_ratio': None,
            'inst_report_date': None,
            'source': 'em',
        }
        # 机构持仓：六类机构持股汇总 / 总股本
        inst_shares = 0.0
        inst_date = None
        for hold_type in HOLD_INST_TYPES:
            for date in _latest_fund_hold_dates():
                df = _get_fund_hold_table(hold_type, date)
                if df is None or df.empty or '股票代码' not in df.columns:
                    continue
                sub = df[df['股票代码'].astype(str).str.contains(symbol, na=False)]
                if sub.empty:
                    continue
                if '持股总数' in df.columns:
                    try:
                        inst_shares += float(sub.iloc[0]['持股总数'] or 0)
                    except (TypeError, ValueError):
                        pass
                inst_date = inst_date or date
                break  # 该类型取最近一个有数据的报告期
        if inst_date:
            result['inst_shares'] = round(inst_shares, 2)
            result['inst_report_date'] = inst_date
            if total_shares and total_shares > 0:
                result['inst_ratio'] = round(inst_shares / total_shares * 100, 2)
        return result
    except Exception as e:
        logger.warning(f'[020R-45 股东人数/机构持仓 {symbol}] 采集失败(静默降级): {e}')
        return None


def _save_holder_structure(stock_id: int, data):
    """020R-45：股东人数/机构持仓快照落库（按 stat_date 幂等，保留最近 12 期）。
    021I：新增 source 列（'em'=A股东财口径 / 'westock'=港股腾讯 shareholder 口径）。
    021Q：新增 inst_count / inst_count_change_pct（港股机构股东数量，A股恒 NULL）。
    """
    if not data or data.get('stat_date') is None:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT OR REPLACE INTO holder_structure '
        '(stock_id, stat_date, holder_count, holder_count_change_pct, total_shares, '
        'inst_shares, inst_ratio, inst_report_date, source, inst_count, inst_count_change_pct) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (
            stock_id, data['stat_date'], data.get('holder_count'),
            data.get('holder_count_change_pct'), data.get('total_shares'),
            data.get('inst_shares'), data.get('inst_ratio'), data.get('inst_report_date'),
            data.get('source'), data.get('inst_count'), data.get('inst_count_change_pct'),
        ),
    )
    cursor.execute(
        'DELETE FROM holder_structure WHERE stock_id=? AND stat_date NOT IN '
        '(SELECT stat_date FROM holder_structure WHERE stock_id=? ORDER BY stat_date DESC LIMIT 12)',
        (stock_id, stock_id),
    )
    conn.commit()
    conn.close()
    logger.info(
        f'[020R-45 股东人数/机构持仓] stock_id={stock_id}, '
        f'holder_change={data.get("holder_count_change_pct")}, inst_ratio={data.get("inst_ratio")}'
    )


# ============================================================
# 021I：港股股东数据——腾讯 westock shareholder 命令
# 覆盖：机构持仓（机构持仓统计块）+ 股东分布 + 机构增减持方向。
# 股东人数（股东户数）：港交所不强制披露、腾讯亦无——港股该字段恒 None（缺失归零）。
# ============================================================

_HK_SHAREHOLDER_CACHE: dict[str, tuple[float, Any]] = {}  # {symbol: (ts, data)}，按股票代码分键（10 分钟 TTL）
_HK_SHAREHOLDER_CACHE_TTL = 600  # 10 分钟


def _quarter_to_date(period_str):
    """'2026 Q2' → '2026-06-30'；无法识别时原样返回。"""
    import re as _re

    m = _re.match(r'^\s*(\d{4})\s*Q([1-4])\s*$', str(period_str or ''))
    if not m:
        return (str(period_str or '') or None)
    year, q = int(m.group(1)), int(m.group(2))
    day = 31 if q in (1, 4) else 30
    return f'{year}-{q * 3:02d}-{day}'


def _parse_westock_shareholder(text):
    """021I：解析 shareholder 命令输出（含多张 Markdown 表）。

    Returns: {'inst': {机构持仓统计行}, 'distribution': [...]} 或 None。
    与 _parse_westock_markdown（只取第一张表）区分：按表头关键词定位。
    """
    lines = text.splitlines()
    tables = []
    cur = []
    for ln in lines:
        s = ln.strip()
        if s.startswith('|'):
            cur.append(s)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)

    result = {}
    for tbl in tables:
        if len(tbl) < 2:
            continue
        headers = [h.strip() for h in tbl[0].strip('|').split('|')]
        data_lines = [ln_ for ln_ in tbl[1:] if not set(ln_) <= set('|-: ')]
        if not data_lines:
            continue
        rows = [
            dict(zip(headers, [c.strip() for c in ln_.strip('|').split('|')]))
            for ln_ in data_lines
            if len(ln_.strip('|').split('|')) == len(headers)
        ]
        if not rows:
            continue
        if 'holdingPct' in headers and 'holdingShares' in headers:
            result['inst'] = rows[0]
            # 021Q：保留上一报告期行（机构股东数量环比计算用）
            if len(rows) > 1:
                result['inst_prev'] = rows[1]
        elif 'institution' in headers:
            result['distribution'] = rows
    return result if (result.get('inst') or result.get('distribution')) else None


def _fetch_shareholder_westock(symbol):
    """021I：调用 westock shareholder 命令获取港股股东数据（按股票代码 10 分钟缓存）。

    Returns: {'inst': dict, 'distribution': list} 或 None（接口失败/无数据静默降级）。
    """
    global _HK_SHAREHOLDER_CACHE
    try:
        code_key = str(symbol)
        cached = _HK_SHAREHOLDER_CACHE.get(code_key)
        if cached and (time.time() - cached[0]) < _HK_SHAREHOLDER_CACHE_TTL:
            return cached[1]
        # 021I：westock CLI 要求 5 位港股代码（hk03690），库内 4 位（HK3690）须左补零，
        # 否则接口静默返回空（rc=0，无输出）。
        code = str(symbol).replace('HK', '').replace('hk', '').zfill(5)
        text = _westock_cli_query('shareholder', f'hk{code}')
        if not text:
            return None
        parsed = _parse_westock_shareholder(text)
        _HK_SHAREHOLDER_CACHE[code_key] = (time.time(), parsed)
        if parsed is None:
            logger.warning(f'[{symbol}] westock shareholder 输出无法解析: {text[:200]}')
        return parsed
    except Exception as e:
        logger.warning(f'[{symbol}] westock shareholder 获取失败(静默降级): {e}')
        return None


# OPT-3：_num_float 归入 symbols_status.py（数值规范化助手家族；capital_westock 亦引用，断循环导入）


def _fetch_holder_structure_hk(symbol):
    """021I：港股机构持仓（腾讯 shareholder 机构持仓统计块）。

    021Q：新增机构股东数量（instCount）+ 环比——港交所无股东人数强制披露，
    机构股东数量是港股最接近的筹码集中度代理指标（季度粒度）。
    注意方向语义：机构数量下降=机构离场（利空），与 A股"户数下降=筹码集中"
    （利好）相反——评分接入时须反向映射，本层只如实存数。
    股东人数 holder_count / holder_count_change_pct 仍恒 None（无披露源）。
    stat_date = 机构持仓报告期（季度末），与 A股户数截止日同列共存。
    Returns: dict（source='westock'）或 None。
    """
    parsed = _fetch_shareholder_westock(symbol)
    if not parsed or not parsed.get('inst'):
        return None
    inst = parsed['inst']
    inst_shares = _num_float(inst.get('holdingShares'))
    inst_ratio = _num_float(inst.get('holdingPct'))
    # 021Q：机构股东数量 + 环比（与上一报告期比）
    inst_count = _num_float(inst.get('instCount'))
    inst_count_chg = None
    prev = parsed.get('inst_prev') or {}
    prev_count = _num_float(prev.get('instCount'))
    if inst_count is not None and prev_count:
        inst_count_chg = round((inst_count - prev_count) / prev_count * 100, 2)
    if inst_shares is None and inst_ratio is None and inst_count is None:
        return None
    report_date = _quarter_to_date(inst.get('reportingPeriod'))
    return {
        'stat_date': report_date,
        'holder_count': None,
        'holder_count_change_pct': None,
        'total_shares': None,
        'inst_shares': round(inst_shares, 2) if inst_shares is not None else None,
        'inst_ratio': round(inst_ratio, 2) if inst_ratio is not None else None,
        'inst_report_date': report_date,
        'inst_count': int(inst_count) if inst_count is not None else None,
        'inst_count_change_pct': inst_count_chg,
        'source': 'westock',
    }


def _fetch_holder_increase_hk(symbol):
    """021I：港股股东行为——季度级机构增减持方向（腾讯 shareholder）。

    三态语义（与 A股 020R-44 对齐；粒度=季度而非30天）：
        True  = 近季机构净增持（changeShares>0 或 instIncreaseCount>0）
        False = 有数据且近季无机构净增持（净减持或不变）
        None  = 接口不可用/无机构持仓统计（数据缺失，权重归零）
    """
    parsed = _fetch_shareholder_westock(symbol)
    if not parsed or not parsed.get('inst'):
        return None
    inst = parsed['inst']
    change = _num_float(inst.get('changeShares'))
    inc_count = _num_float(inst.get('instIncreaseCount'))
    if change is None and inc_count is None:
        return None
    if (change is not None and change > 0) or (inc_count is not None and inc_count > 0):
        return True
    return False

# B11-API-DEDUP：股东增减持接口缓存（10分钟TTL，避免批量时重复调用全市场接口）
_holder_cache: Any | None = None
_holder_cache_time: float | None = None
