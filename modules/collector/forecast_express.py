"""业绩预告/快报采集（置信度折价入分的上游数据）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import time
from datetime import datetime

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _safe_num
from modules.collector.symbols_status import save_data_status

# ============================================================
# 业绩预告采集（东财 stock_yjyg_em，A股）
# ============================================================
# 全市场预告 DataFrame 缓存：按报告期 key，1 小时 TTL，
# 避免批量场景每只股票重复下载全市场 4800+ 行数据
_FORECAST_CACHE = {'data': {}, 'ts': 0.0}
_FORECAST_CACHE_TTL = 3600


def _forecast_report_periods():
    """候选报告期列表（新→旧）：今年中报、今年一季报、去年年报"""
    y = datetime.now(_CN_TZ).year
    return [f'{y}0630', f'{y}0331', f'{y - 1}1231']


def _get_forecast_df_for_period(period):
    """拉取指定报告期的全市场业绩预告（带 1 小时内存缓存），返回 DataFrame 或 None"""
    global _FORECAST_CACHE
    now_ts = time.time()
    cached = _FORECAST_CACHE['data'].get(period)
    if cached is not None and (now_ts - _FORECAST_CACHE['ts']) < _FORECAST_CACHE_TTL:
        return cached
    try:
        import akshare as ak

        logger.info(f'[业绩预告] 请求 stock_yjyg_em(date={period})')
        df = ak.stock_yjyg_em(date=period)
        if df is None or df.empty:
            logger.warning(f'[业绩预告] 报告期 {period} 返回空数据')
            return None
        df['_code6'] = df['股票代码'].astype(str).str.zfill(6)
        _FORECAST_CACHE['data'][period] = df
        _FORECAST_CACHE['ts'] = now_ts
        return df
    except Exception as e:
        logger.warning(f'[业绩预告] 报告期 {period} 获取失败: {e}')
        return None


def _dimension_done_today(stock_id, dimension):
    """020R-60：该维度今日是否已有采集记录（success/skipped 均视为已完成）。

    用于盘中不变的数据维度（业绩预告/快报）加"当日门控"，
    避免盘中重复点击/盘中快报每次都重复查询全市场表。
    """
    try:
        conn = get_connection()
        today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        row = conn.execute(
            'SELECT 1 FROM data_status WHERE stock_id=? AND dimension=? AND fetched_at LIKE ? LIMIT 1',
            (stock_id, dimension, today + '%'),
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception:  # noqa: BLE001
        return False


def collect_forecast(stock_id, symbol, market='a_stock'):
    """采集单只股票业绩预告（东财），写入 raw_forecast。

    港股无东财业绩预告数据，跳过。
    按报告期（今年中报→一季报→去年年报）逐期尝试，写入全部命中的预告行。
    020R-60：当日门控——预告结果日内不变（新公告通常盘后发布）。
    返回 (status, message)。
    """
    if market != 'a_stock':
        save_data_status(stock_id, 'forecast', 'skipped', '港股无东财业绩预告数据')
        return 'skipped', '港股无东财业绩预告数据'

    # 020R-60：当日已采集过（含 skipped）→ 直接跳过
    if _dimension_done_today(stock_id, 'forecast'):
        msg = '同日跳过(业绩预告当日已采集)'
        save_data_status(stock_id, 'forecast', 'success', msg)
        return 'success', msg

    try:
        written = 0
        used_periods = []
        for period in _forecast_report_periods():
            df = _get_forecast_df_for_period(period)
            if df is None or df.empty:
                continue
            stock_rows = df[df['_code6'] == symbol]
            if stock_rows.empty:
                continue
            used_periods.append(period)
            conn = get_connection()
            cursor = conn.cursor()
            for _, row in stock_rows.iterrows():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_forecast
                        (stock_id, symbol, report_period, indicator, change_desc,
                         forecast_value, change_pct, change_reason, forecast_type,
                         last_year_value, announce_date, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'akshare_em')
                """,
                    (
                        stock_id,
                        symbol,
                        period,
                        str(row.get('预测指标') or ''),
                        str(row.get('业绩变动') or '')[:1000],
                        _safe_num(row.get('预测数值')),
                        _safe_num(row.get('业绩变动幅度')),
                        str(row.get('业绩变动原因') or '')[:1500],
                        str(row.get('预告类型') or ''),
                        _safe_num(row.get('上年同期值')),
                        str(row.get('公告日期') or '')[:10],
                    ),
                )
                written += 1
            conn.commit()
            conn.close()

        if written > 0:
            msg = f'业绩预告已入库 {written} 条（报告期: {", ".join(used_periods)}）'
            save_data_status(stock_id, 'forecast', 'success', msg)
            return 'success', msg
        msg = f'最近三个报告期暂无业绩预告（{", ".join(_forecast_report_periods())}）'
        save_data_status(stock_id, 'forecast', 'success', msg)
        return 'success', msg
    except Exception as e:
        logger.warning(f'[{symbol}] 业绩预告采集失败: {e}')
        save_data_status(stock_id, 'forecast', 'failed', str(e))
        return 'failed', str(e)


# ============================================================
# 业绩快报采集（东财 stock_yjkb_em，A股）020R-50
# ============================================================
# 全市场快报 DataFrame 缓存：按报告期 key，1 小时 TTL（与预告共用 TTL 常量）
_EXPRESS_CACHE = {'data': {}, 'ts': 0.0}


def _get_express_df_for_period(period):
    """拉取指定报告期的全市场业绩快报（带 1 小时内存缓存），返回 DataFrame 或 None"""
    global _EXPRESS_CACHE
    now_ts = time.time()
    cached = _EXPRESS_CACHE['data'].get(period)
    if cached is not None and (now_ts - _EXPRESS_CACHE['ts']) < _FORECAST_CACHE_TTL:
        return cached
    try:
        import akshare as ak

        logger.info(f'[业绩快报] 请求 stock_yjkb_em(date={period})')
        df = ak.stock_yjkb_em(date=period)
        if df is None or df.empty:
            logger.warning(f'[业绩快报] 报告期 {period} 返回空数据')
            return None
        df['_code6'] = df['股票代码'].astype(str).str.zfill(6)
        _EXPRESS_CACHE['data'][period] = df
        _EXPRESS_CACHE['ts'] = now_ts
        return df
    except Exception as e:
        logger.warning(f'[业绩快报] 报告期 {period} 获取失败: {e}')
        return None


def collect_express(stock_id, symbol, market='a_stock'):
    """采集单只股票业绩快报（东财），写入 raw_express。

    港股无东财业绩快报数据，跳过。
    按报告期（今年中报→一季报→去年年报）逐期尝试，写入全部命中的快报行。
    020R-60：当日门控——快报结果日内不变。
    返回 (status, message)。
    """
    if market != 'a_stock':
        save_data_status(stock_id, 'express', 'skipped', '港股无东财业绩快报数据')
        return 'skipped', '港股无东财业绩快报数据'

    # 020R-60：当日已采集过（含 skipped）→ 直接跳过
    if _dimension_done_today(stock_id, 'express'):
        msg = '同日跳过(业绩快报当日已采集)'
        save_data_status(stock_id, 'express', 'success', msg)
        return 'success', msg

    try:
        written = 0
        used_periods = []
        for period in _forecast_report_periods():
            df = _get_express_df_for_period(period)
            if df is None or df.empty:
                continue
            stock_rows = df[df['_code6'] == symbol]
            if stock_rows.empty:
                continue
            used_periods.append(period)
            conn = get_connection()
            cursor = conn.cursor()
            for _, row in stock_rows.iterrows():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_express
                        (stock_id, symbol, report_period, eps, revenue, revenue_yoy,
                         np, np_yoy, roe, announce_date, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'akshare_em')
                """,
                    (
                        stock_id,
                        symbol,
                        period,
                        _safe_num(row.get('每股收益')),
                        _safe_num(row.get('营业收入-营业收入')),
                        _safe_num(row.get('营业收入-同比增长')),
                        _safe_num(row.get('净利润-净利润')),
                        _safe_num(row.get('净利润-同比增长')),
                        _safe_num(row.get('净资产收益率')),
                        str(row.get('公告日期') or '')[:10],
                    ),
                )
                written += 1
            conn.commit()
            conn.close()

        if written > 0:
            msg = f'业绩快报已入库 {written} 条（报告期: {", ".join(used_periods)}）'
            save_data_status(stock_id, 'express', 'success', msg)
            return 'success', msg
        msg = f'最近三个报告期暂无业绩快报（{", ".join(_forecast_report_periods())}）'
        save_data_status(stock_id, 'express', 'success', msg)
        return 'success', msg
    except Exception as e:
        logger.warning(f'[{symbol}] 业绩快报采集失败: {e}')
        save_data_status(stock_id, 'express', 'failed', str(e))
        return 'failed', str(e)
