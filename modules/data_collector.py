"""
数据采集模块（facade）—— OPT-3（2026-09-07）起实现拆分至 modules/collector/ 包。

本文件仅为兼容再导出层：全部既有调用方（backfill_scheduler/daily_report/blueprints/tests/scripts）
零改动可用；模块级符号表面与拆分前逐符号一致（vars 快照断言）。
"""

from modules.collector._env import (
    _CN_TZ as _CN_TZ,
)
from modules.collector._env import (
    EM_USE_PROXY as EM_USE_PROXY,
)
from modules.collector._env import (
    FUNDAMENTAL_REPORT_TTL_DAYS as FUNDAMENTAL_REPORT_TTL_DAYS,
)
from modules.collector._env import (
    KLINE_DAYS as KLINE_DAYS,
)
from modules.collector._env import (
    MAX_RETRIES as MAX_RETRIES,
)
from modules.collector._env import (
    NORTH_CAPITAL_CACHE_DAYS as NORTH_CAPITAL_CACHE_DAYS,
)
from modules.collector._env import (
    PE_PB_CACHE_TTL_HOURS as PE_PB_CACHE_TTL_HOURS,
)
from modules.collector._env import (
    _td as _td,
)
from modules.collector._env import (
    datetime as datetime,
)
from modules.collector._env import (
    get_connection as get_connection,
)
from modules.collector._env import (
    json as json,
)
from modules.collector._env import (
    logger as logger,
)
from modules.collector._env import (
    logging as logging,
)
from modules.collector._env import (
    math as math,
)
from modules.collector._env import (
    now_cn as now_cn,
)
from modules.collector._env import (
    os as os,
)
from modules.collector._env import (
    pd as pd,
)
from modules.collector._env import (
    sys as sys,
)
from modules.collector._env import (
    time as time,
)
from modules.collector._env import (
    timedelta as timedelta,
)
from modules.collector._env import (
    timezone as timezone,
)
from modules.collector._env import (
    traceback as traceback,
)
from modules.collector.capital_em import (
    _EM_BACKOFF_CAP_SECONDS as _EM_BACKOFF_CAP_SECONDS,
)
from modules.collector.capital_em import (
    _EM_BAN_STATE_FILE as _EM_BAN_STATE_FILE,
)
from modules.collector.capital_em import (
    _EM_BAN_TTL_SECONDS as _EM_BAN_TTL_SECONDS,
)
from modules.collector.capital_em import (
    _EM_BAN_UNTIL as _EM_BAN_UNTIL,
)
from modules.collector.capital_em import (
    _EM_BATCH_GAP_RANGE as _EM_BATCH_GAP_RANGE,
)
from modules.collector.capital_em import (
    _EM_BATCH_SIZE as _EM_BATCH_SIZE,
)
from modules.collector.capital_em import (
    _EM_CIRCUIT_BREAK_N as _EM_CIRCUIT_BREAK_N,
)
from modules.collector.capital_em import (
    _EM_COOLDOWN_FAIL_N as _EM_COOLDOWN_FAIL_N,
)
from modules.collector.capital_em import (
    _EM_COOLDOWN_SECONDS as _EM_COOLDOWN_SECONDS,
)
from modules.collector.capital_em import (
    _EM_FALLBACK_TOTAL_CAP_SECONDS as _EM_FALLBACK_TOTAL_CAP_SECONDS,
)
from modules.collector.capital_em import (
    _EM_INTER_DELAY_RANGE as _EM_INTER_DELAY_RANGE,
)
from modules.collector.capital_em import (
    _em_ban_state_clear as _em_ban_state_clear,
)
from modules.collector.capital_em import (
    _em_ban_state_load as _em_ban_state_load,
)
from modules.collector.capital_em import (
    _em_ban_state_save as _em_ban_state_save,
)
from modules.collector.capital_em import (
    _em_banned as _em_banned,
)
from modules.collector.capital_em import (
    _em_clear_ban as _em_clear_ban,
)
from modules.collector.capital_em import (
    _em_record_ban as _em_record_ban,
)
from modules.collector.capital_em import (
    _fetch_capital_flow_em as _fetch_capital_flow_em,
)
from modules.collector.capital_em import (
    _fetch_capital_flow_em_individual as _fetch_capital_flow_em_individual,
)
from modules.collector.capital_em import (
    _get_em_market_code as _get_em_market_code,
)
from modules.collector.capital_em import (
    _get_em_secid as _get_em_secid,
)
from modules.collector.capital_em import (
    _parse_cn_amount as _parse_cn_amount,
)
from modules.collector.capital_em import (
    _safe_float_pct as _safe_float_pct,
)
from modules.collector.capital_em import (
    _safe_float_wan as _safe_float_wan,
)
from modules.collector.capital_em import (
    _safe_num as _safe_num,
)
from modules.collector.capital_flow import (
    _EM_CONSECUTIVE_FAIL_COUNT as _EM_CONSECUTIVE_FAIL_COUNT,
)
from modules.collector.capital_flow import (
    _em_batch_collect as _em_batch_collect,
)
from modules.collector.capital_flow import (
    backfill_capital_history as backfill_capital_history,
)
from modules.collector.capital_flow import (
    backfill_hk_total_net as backfill_hk_total_net,
)
from modules.collector.capital_flow import (
    fetch_capital_flow as fetch_capital_flow,
)
from modules.collector.capital_flow import (
    fetch_capital_flow_batch as fetch_capital_flow_batch,
)
from modules.collector.capital_margin import (
    _MARGIN_CACHE as _MARGIN_CACHE,
)
from modules.collector.capital_margin import (
    _fetch_margin_data_sse as _fetch_margin_data_sse,
)
from modules.collector.capital_margin import (
    _fetch_margin_data_szse as _fetch_margin_data_szse,
)
from modules.collector.capital_margin import (
    fetch_margin_balance as fetch_margin_balance,
)
from modules.collector.capital_sources import (
    _fetch_capital_flow_netease as _fetch_capital_flow_netease,
)
from modules.collector.capital_sources import (
    _fetch_capital_flow_sina as _fetch_capital_flow_sina,
)
from modules.collector.capital_sources import (
    _fetch_capital_flow_sina_main as _fetch_capital_flow_sina_main,
)
from modules.collector.capital_sources import (
    _fetch_capital_flow_tencent_hk as _fetch_capital_flow_tencent_hk,
)
from modules.collector.capital_sources import (
    fetch_north_capital as fetch_north_capital,
)
from modules.collector.capital_ths import (
    _THS_CAPITAL_CACHE as _THS_CAPITAL_CACHE,
)
from modules.collector.capital_ths import (
    _THS_CAPITAL_CACHE_TTL as _THS_CAPITAL_CACHE_TTL,
)
from modules.collector.capital_ths import (
    _THS_CONSECUTIVE_FAIL_COUNT as _THS_CONSECUTIVE_FAIL_COUNT,
)
from modules.collector.capital_ths import (
    _THS_FAIL_THRESHOLD as _THS_FAIL_THRESHOLD,
)
from modules.collector.capital_ths import (
    _THS_REQUEST_TIMEOUT as _THS_REQUEST_TIMEOUT,
)
from modules.collector.capital_ths import (
    _fetch_capital_flow_ths_batch as _fetch_capital_flow_ths_batch,
)
from modules.collector.capital_ths import (
    _try_ths_primary as _try_ths_primary,
)
from modules.collector.capital_ths import (
    _try_ths_rank_backup as _try_ths_rank_backup,
)
from modules.collector.capital_westock import (
    _WESTOCK_CONSECUTIVE_FAIL as _WESTOCK_CONSECUTIVE_FAIL,
)
from modules.collector.capital_westock import (
    _WESTOCK_COOLDOWN_FAIL_N as _WESTOCK_COOLDOWN_FAIL_N,
)
from modules.collector.capital_westock import (
    _WESTOCK_COOLDOWN_SECONDS as _WESTOCK_COOLDOWN_SECONDS,
)
from modules.collector.capital_westock import (
    _WESTOCK_COOLDOWN_UNTIL as _WESTOCK_COOLDOWN_UNTIL,
)
from modules.collector.capital_westock import (
    _WESTOCK_PACKAGE as _WESTOCK_PACKAGE,
)
from modules.collector.capital_westock import (
    _WESTOCK_TIMEOUT_SECONDS as _WESTOCK_TIMEOUT_SECONDS,
)
from modules.collector.capital_westock import (
    _fetch_capital_flow_westock as _fetch_capital_flow_westock,
)
from modules.collector.capital_westock import (
    _parse_westock_markdown as _parse_westock_markdown,
)
from modules.collector.capital_westock import (
    _westock_cli_query as _westock_cli_query,
)
from modules.collector.capital_westock import (
    _westock_cooldown_active as _westock_cooldown_active,
)
from modules.collector.capital_westock import (
    _westock_record_failure as _westock_record_failure,
)
from modules.collector.capital_westock import (
    _westock_reset as _westock_reset,
)
from modules.collector.collect import (
    collect_stock_data as collect_stock_data,
)
from modules.collector.forecast_express import (
    _EXPRESS_CACHE as _EXPRESS_CACHE,
)
from modules.collector.forecast_express import (
    _FORECAST_CACHE as _FORECAST_CACHE,
)
from modules.collector.forecast_express import (
    _FORECAST_CACHE_TTL as _FORECAST_CACHE_TTL,
)
from modules.collector.forecast_express import (
    _dimension_done_today as _dimension_done_today,
)
from modules.collector.forecast_express import (
    _forecast_report_periods as _forecast_report_periods,
)
from modules.collector.forecast_express import (
    _get_express_df_for_period as _get_express_df_for_period,
)
from modules.collector.forecast_express import (
    _get_forecast_df_for_period as _get_forecast_df_for_period,
)
from modules.collector.forecast_express import (
    collect_express as collect_express,
)
from modules.collector.forecast_express import (
    collect_forecast as collect_forecast,
)
from modules.collector.fundamental_sina import (
    _ABSTRACT_FIELD_MAP as _ABSTRACT_FIELD_MAP,
)
from modules.collector.fundamental_sina import (
    _ABSTRACT_GROUP_PRIORITY as _ABSTRACT_GROUP_PRIORITY,
)
from modules.collector.fundamental_sina import (
    _FUND_ABSTRACT_TIMEOUT as _FUND_ABSTRACT_TIMEOUT,
)
from modules.collector.fundamental_sina import (
    _abstract_name_index as _abstract_name_index,
)
from modules.collector.fundamental_sina import (
    _apply_fundamental_detail as _apply_fundamental_detail,
)
from modules.collector.fundamental_sina import (
    _extract_abstract_rows as _extract_abstract_rows,
)
from modules.collector.fundamental_sina import (
    _extract_indicator_rows as _extract_indicator_rows,
)
from modules.collector.fundamental_sina import (
    _fetch_a_fundamental_sina as _fetch_a_fundamental_sina,
)
from modules.collector.fundamental_sina import (
    _fetch_a_fundamental_sina_indicator as _fetch_a_fundamental_sina_indicator,
)
from modules.collector.fundamental_sina import (
    _safe_row_val as _safe_row_val,
)
from modules.collector.fundamental_sina import (
    fetch_a_fundamental as fetch_a_fundamental,
)
from modules.collector.fundamental_sina import (
    fetch_fundamental_detail as fetch_fundamental_detail,
)
from modules.collector.hk_fundamental import (
    _fetch_hk_fundamental_em as _fetch_hk_fundamental_em,
)
from modules.collector.hk_fundamental import (
    _num_or_none_flow as _num_or_none_flow,
)
from modules.collector.hk_fundamental import (
    fetch_hk_fundamental as fetch_hk_fundamental,
)
from modules.collector.hk_fundamental import (
    fetch_south_flow_snapshot as fetch_south_flow_snapshot,
)
from modules.collector.holder_structure import (
    _HK_SHAREHOLDER_CACHE as _HK_SHAREHOLDER_CACHE,
)
from modules.collector.holder_structure import (
    _HK_SHAREHOLDER_CACHE_TTL as _HK_SHAREHOLDER_CACHE_TTL,
)
from modules.collector.holder_structure import (
    HOLD_INST_TYPES as HOLD_INST_TYPES,
)
from modules.collector.holder_structure import (
    _fetch_holder_increase_hk as _fetch_holder_increase_hk,
)
from modules.collector.holder_structure import (
    _fetch_holder_structure_hk as _fetch_holder_structure_hk,
)
from modules.collector.holder_structure import (
    _fetch_shareholder_westock as _fetch_shareholder_westock,
)
from modules.collector.holder_structure import (
    _fund_hold_cache as _fund_hold_cache,
)
from modules.collector.holder_structure import (
    _fund_hold_cache_time as _fund_hold_cache_time,
)
from modules.collector.holder_structure import (
    _get_fund_hold_table as _get_fund_hold_table,
)
from modules.collector.holder_structure import (
    _holder_cache as _holder_cache,
)
from modules.collector.holder_structure import (
    _holder_cache_time as _holder_cache_time,
)
from modules.collector.holder_structure import (
    _latest_fund_hold_dates as _latest_fund_hold_dates,
)
from modules.collector.holder_structure import (
    _num_or_none as _num_or_none,
)
from modules.collector.holder_structure import (
    _parse_westock_shareholder as _parse_westock_shareholder,
)
from modules.collector.holder_structure import (
    _quarter_to_date as _quarter_to_date,
)
from modules.collector.holder_structure import (
    _save_holder_increase as _save_holder_increase,
)
from modules.collector.holder_structure import (
    _save_holder_structure as _save_holder_structure,
)
from modules.collector.holder_structure import (
    fetch_holder_increase as fetch_holder_increase,
)
from modules.collector.holder_structure import (
    fetch_holder_structure as fetch_holder_structure,
)
from modules.collector.http_client import (
    _EM_LAST_REQUEST_TS as _EM_LAST_REQUEST_TS,
)
from modules.collector.http_client import (
    _EM_MIN_INTERVAL_SECONDS as _EM_MIN_INTERVAL_SECONDS,
)
from modules.collector.http_client import (
    _EM_RETRY_BACKOFFS as _EM_RETRY_BACKOFFS,
)
from modules.collector.http_client import (
    _EM_RETRY_JITTER as _EM_RETRY_JITTER,
)
from modules.collector.http_client import (
    _EM_RETRY_ROUNDS as _EM_RETRY_ROUNDS,
)
from modules.collector.http_client import (
    _SINA_REQUEST_TIMEOUT as _SINA_REQUEST_TIMEOUT,
)
from modules.collector.http_client import (
    _UA_POOL as _UA_POOL,
)
from modules.collector.http_client import (
    ProxyHealthTracker as ProxyHealthTracker,
)
from modules.collector.http_client import (
    _call_ak_with_timeout as _call_ak_with_timeout,
)
from modules.collector.http_client import (
    _call_with_timeout as _call_with_timeout,
)
from modules.collector.http_client import (
    _http_get as _http_get,
)
from modules.collector.http_client import (
    _http_get_em as _http_get_em,
)
from modules.collector.http_client import (
    _no_proxy_request as _no_proxy_request,
)
from modules.collector.http_client import (
    _original_request as _original_request,
)
from modules.collector.http_client import (
    _proxy_health as _proxy_health,
)
from modules.collector.http_client import (
    _random as _random,
)
from modules.collector.http_client import (
    _random_ua as _random_ua,
)
from modules.collector.http_client import (
    _rotate_em_host as _rotate_em_host,
)
from modules.collector.http_client import (
    _urlreq as _urlreq,
)
from modules.collector.http_client import (
    ak as ak,
)
from modules.collector.http_client import (
    requests as requests,
)
from modules.collector.http_client import (
    retry as retry,
)
from modules.collector.kline import (
    _fetch_kline_tencent as _fetch_kline_tencent,
)
from modules.collector.kline import (
    _is_intraday_session as _is_intraday_session,
)
from modules.collector.kline import (
    _refresh_kline_today_bar as _refresh_kline_today_bar,
)
from modules.collector.kline import (
    aggregate_period_klines as aggregate_period_klines,
)
from modules.collector.kline import (
    fetch_kline as fetch_kline,
)
from modules.collector.mootdx import (
    _MOOTDX_CLIENT as _MOOTDX_CLIENT,
)
from modules.collector.mootdx import (
    _MOOTDX_FALLBACK_SERVERS as _MOOTDX_FALLBACK_SERVERS,
)
from modules.collector.mootdx import (
    _MOOTDX_INIT_DONE as _MOOTDX_INIT_DONE,
)
from modules.collector.mootdx import (
    _MOOTDX_LOCK as _MOOTDX_LOCK,
)
from modules.collector.mootdx import (
    _ensure_kline_source_column as _ensure_kline_source_column,
)
from modules.collector.mootdx import (
    _fetch_kline_mootdx as _fetch_kline_mootdx,
)
from modules.collector.mootdx import (
    _fetch_realtime_quote_mootdx as _fetch_realtime_quote_mootdx,
)
from modules.collector.mootdx import (
    _mootdx_client as _mootdx_client,
)
from modules.collector.mootdx import (
    _mootdx_symbol as _mootdx_symbol,
)
from modules.collector.mootdx import (
    _mootdx_verify as _mootdx_verify,
)
from modules.collector.mootdx import (
    _threading_019y as _threading_019y,
)
from modules.collector.mootdx import (
    backfill_kline_history_mootdx as backfill_kline_history_mootdx,
)
from modules.collector.mootdx import (
    fetch_orderbook as fetch_orderbook,
)
from modules.collector.mootdx import (
    get_realtime_quote_mootdx as get_realtime_quote_mootdx,
)
from modules.collector.sentiment_industry import (
    _LOCAL_INDUSTRY_MAP as _LOCAL_INDUSTRY_MAP,
)
from modules.collector.sentiment_industry import (
    fetch_sentiment as fetch_sentiment,
)
from modules.collector.sentiment_industry import (
    fetch_stock_industry as fetch_stock_industry,
)
from modules.collector.symbols_status import (
    _get_tencent_prefix as _get_tencent_prefix,
)
from modules.collector.symbols_status import (
    _log_error_to_db as _log_error_to_db,
)
from modules.collector.symbols_status import (
    _normalize_hk_symbol as _normalize_hk_symbol,
)
from modules.collector.symbols_status import (
    _num_float as _num_float,
)
from modules.collector.symbols_status import (
    get_stock_id as get_stock_id,
)
from modules.collector.symbols_status import (
    save_data_status as save_data_status,
)
from modules.collector.valuation import (
    _BS_LOCK as _BS_LOCK,
)
from modules.collector.valuation import (
    _BS_LOGGED_IN as _BS_LOGGED_IN,
)
from modules.collector.valuation import (
    _atexit_019y as _atexit_019y,
)
from modules.collector.valuation import (
    _bs_code as _bs_code,
)
from modules.collector.valuation import (
    _bs_ensure_login as _bs_ensure_login,
)
from modules.collector.valuation import (
    _bs_logout as _bs_logout,
)
from modules.collector.valuation import (
    _fetch_valuation_akshare as _fetch_valuation_akshare,
)
from modules.collector.valuation import (
    _fetch_valuation_baostock as _fetch_valuation_baostock,
)
from modules.collector.valuation import (
    _fetch_valuation_tencent as _fetch_valuation_tencent,
)
from modules.collector.valuation import (
    _pick_val as _pick_val,
)
from modules.collector.valuation import (
    _threading_019y_bs as _threading_019y_bs,
)
from modules.collector.valuation import (
    fetch_fundamental_baostock as fetch_fundamental_baostock,
)
from modules.collector.valuation import (
    fetch_restricted_release as fetch_restricted_release,
)
from modules.collector.valuation import (
    fetch_valuation as fetch_valuation,
)
from modules.collector.valuation import (
    fetch_valuation_history as fetch_valuation_history,
)
