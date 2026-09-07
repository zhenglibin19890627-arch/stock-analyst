"""单股全维度采集编排入口 collect_stock_data。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import traceback

from database.db_manager import get_connection
from modules.collector._env import logger
from modules.collector.capital_flow import fetch_capital_flow
from modules.collector.capital_margin import fetch_margin_balance
from modules.collector.capital_sources import fetch_north_capital
from modules.collector.forecast_express import collect_express, collect_forecast
from modules.collector.fundamental_sina import (
    _apply_fundamental_detail,
    fetch_a_fundamental,
    fetch_fundamental_detail,
)
from modules.collector.hk_fundamental import fetch_hk_fundamental
from modules.collector.holder_structure import (
    _fetch_holder_increase_hk,
    _fetch_holder_structure_hk,
    _save_holder_increase,
    _save_holder_structure,
    fetch_holder_increase,
    fetch_holder_structure,
)
from modules.collector.kline import aggregate_period_klines, fetch_kline
from modules.collector.mootdx import fetch_orderbook
from modules.collector.sentiment_industry import fetch_sentiment
from modules.collector.symbols_status import _log_error_to_db, get_stock_id
from modules.collector.valuation import fetch_restricted_release, fetch_valuation

# ============================================================
# 统一采集入口
# ============================================================


def collect_stock_data(symbol, market, force_full=False):
    """
    对外统一接口：根据市场类型，自动调用对应的数据采集函数。
    011增加 force_full 参数透传（强制全量采集，绕过所有增量缓存）。

    参数:
        symbol: 股票代码（如 '000001' 或 '00700'）
        market: 市场（'a_stock' 或 'hk_stock'）
        force_full: True时绕过所有增量缓存，全量采集

    返回:
        dict: 各维度采集状态汇总
    """
    results = {}
    market_name = 'A股' if market == 'a_stock' else '港股'
    ff = '[FULL_REFRESH] ' if force_full else ''

    # 012-C: 获取 stock_id 用于错误日志记录
    stock_id = get_stock_id(symbol, market)

    logger.info(f'========== 开始采集{market_name} {symbol} {ff}==========')

    # K线（A股和港股统一用腾讯接口）
    try:
        results['kline'] = fetch_kline(symbol, market, force_full=force_full)
    except Exception as e:
        results['kline'] = ('failed', f'K线采集异常: {e}')
        logger.warning(f'[{symbol}] K线采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='kline',
            traceback_str=traceback.format_exc(),
        )

    # 020R-48：日线更新后同步聚合周线/月线（失败不阻塞主流程）
    if stock_id:
        try:
            agg_status, agg_msg = aggregate_period_klines(stock_id)
            results['kline_periods'] = (agg_status, agg_msg)
        except Exception as e:
            logger.warning(f'[{symbol}] 周/月线聚合异常(不阻塞): {e}')

    # 基本面
    if market == 'a_stock':
        try:
            results['fundamental'] = fetch_a_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )
        # B10: 基本面字段补全（仅填充NULL字段，不覆盖已有值）
        try:
            if stock_id:
                # B11-API-DEDUP：检查 fetch_a_fundamental 是否已获取足够数据，避免重复调用
                conn_chk = get_connection()
                cursor_chk = conn_chk.cursor()
                cursor_chk.execute(
                    """
                    SELECT roe, gross_margin, revenue_growth, profit_growth,
                           ocf_to_net_profit, debt_ratio, current_ratio
                    FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1
                """,
                    (stock_id,),
                )
                fund_row = cursor_chk.fetchone()
                conn_chk.close()

                need_detail = True
                if fund_row:
                    non_null = sum(
                        1
                        for v in [
                            fund_row['roe'],
                            fund_row['gross_margin'],
                            fund_row['revenue_growth'],
                            fund_row['profit_growth'],
                            fund_row['ocf_to_net_profit'],
                            fund_row['debt_ratio'],
                            fund_row['current_ratio'],
                        ]
                        if v is not None
                    )
                    if non_null >= 5:
                        need_detail = False
                        logger.info(
                            f'[{symbol}] 基本面已有{non_null}个字段，跳过 fetch_fundamental_detail'
                        )

                if need_detail:
                    detail = fetch_fundamental_detail(symbol)
                    _apply_fundamental_detail(stock_id, detail)

                # B10: 股东增减持采集
                holder_val = fetch_holder_increase(symbol)
                _save_holder_increase(stock_id, holder_val)
                results['holder_increase'] = ('success', f'holder_increase={holder_val}')

                # 020R-45: 股东人数与机构持仓采集（A股专属，失败静默降级）
                hs_data = fetch_holder_structure(symbol)
                _save_holder_structure(stock_id, hs_data)
                results['holder_structure'] = (
                    'success',
                    f'holder_change={hs_data.get("holder_count_change_pct") if hs_data else None}, '
                    f'inst_ratio={hs_data.get("inst_ratio") if hs_data else None}',
                )
        except Exception as e:
            logger.warning(f'[{symbol}] B10基本面补全/股东增减持异常(不阻塞): {e}')
    else:
        try:
            results['fundamental'] = fetch_hk_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'港股基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 港股基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )

        # 021I：港股股东数据——机构持仓/机构增减持（腾讯 westock shareholder，失败静默降级）
        try:
            holder_val = _fetch_holder_increase_hk(symbol)
            _save_holder_increase(stock_id, holder_val)
            results['holder_increase'] = ('success', f'holder_increase={holder_val}')
            hs_data = _fetch_holder_structure_hk(symbol)
            _save_holder_structure(stock_id, hs_data)
            results['holder_structure'] = (
                'success',
                f'inst_ratio={hs_data.get("inst_ratio") if hs_data else None}',
            )
        except Exception as e:
            logger.warning(f'[{symbol}] 港股股东数据采集异常(不阻塞): {e}')

    # 资金面（A股和港股统一用东方财富push2接口）
    # 红线：fetch_capital_flow 不加 force_full 参数（同日跳过基于已有真实数据，不重复采集）
    try:
        results['capital'] = fetch_capital_flow(symbol, market)
    except Exception as e:
        results['capital'] = ('failed', f'资金面采集异常: {e}')
        logger.warning(f'[{symbol}] 资金面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='capital',
            traceback_str=traceback.format_exc(),
        )

    # DATASRC-C：北向资金 + 融资融券补齐（仅 A股，失败不阻塞主流程）
    if market == 'a_stock':
        try:
            results['north_capital'] = fetch_north_capital(symbol, market, force_full=force_full)
        except Exception as e:
            results['north_capital'] = ('failed', f'北向资金采集异常: {e}')
            logger.warning(f'[{symbol}] 北向资金采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='north',
                traceback_str=traceback.format_exc(),
            )
        try:
            results['margin_balance'] = fetch_margin_balance(symbol, market, force_full=force_full)
        except Exception as e:
            results['margin_balance'] = ('failed', f'融资余额采集异常: {e}')
            logger.warning(f'[{symbol}] 融资余额采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='margin',
                traceback_str=traceback.format_exc(),
            )

    # 019Y：新增数据维度（失败不阻塞主流程）
    # 五档盘口（mootdx，仅A股，实时快照，每只每天保留最新一条）
    try:
        results['orderbook'] = fetch_orderbook(symbol, market)
    except Exception as e:
        results['orderbook'] = ('failed', f'五档盘口采集异常: {e}')
        logger.warning(f'[{symbol}] 五档盘口采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='orderbook',
            traceback_str=traceback.format_exc(),
        )

    # 估值数据（akshare主源 → baostock备用，日级低频，同日跳过）
    try:
        results['valuation'] = fetch_valuation(symbol, market)
    except Exception as e:
        results['valuation'] = ('failed', f'估值采集异常: {e}')
        logger.warning(f'[{symbol}] 估值采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='valuation',
            traceback_str=traceback.format_exc(),
        )

    # 限售解禁（akshare，仅A股，事件级风险因子）
    try:
        results['restricted_release'] = fetch_restricted_release(symbol, market)
    except Exception as e:
        results['restricted_release'] = ('failed', f'限售解禁采集异常: {e}')
        logger.warning(f'[{symbol}] 限售解禁采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='restricted_release',
            traceback_str=traceback.format_exc(),
        )

    # 业绩预告（A股；港股跳过）
    try:
        results['forecast'] = collect_forecast(stock_id, symbol, market)
    except Exception as e:
        results['forecast'] = ('failed', f'业绩预告采集异常: {e}')
        logger.warning(f'[{symbol}] 业绩预告采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='forecast',
            traceback_str=traceback.format_exc(),
        )

    # 业绩快报（A股；港股跳过）020R-50
    try:
        results['express'] = collect_express(stock_id, symbol, market)
    except Exception as e:
        results['express'] = ('failed', f'业绩快报采集异常: {e}')
        logger.warning(f'[{symbol}] 业绩快报采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='express',
            traceback_str=traceback.format_exc(),
        )

    # 消息面
    try:
        results['sentiment'] = fetch_sentiment(symbol, market, force_full=force_full)
    except Exception as e:
        results['sentiment'] = ('failed', f'消息面采集异常: {e}')
        logger.warning(f'[{symbol}] 消息面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='sentiment',
            traceback_str=traceback.format_exc(),
        )

    logger.info(f'========== {symbol} 数据采集完成 {ff}==========')
    return results
