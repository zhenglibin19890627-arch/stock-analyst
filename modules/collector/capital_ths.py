"""资金面·同花顺批量源（缓存+熔断）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import time

import akshare as ak

from modules.collector._env import logger

# ============================================================
# 资金面数据采集
# 主数据源：东方财富 stock_individual_fund_flow（通过akshare调用，直连可用）
#   - 优势：返回120天历史资金流向数据，含主力/超大单/大单/中单/小单
#   - 限制：仅支持A股
# 备用数据源：东方财富 push2 接口（部分网络环境被封，仅作回退）
# ============================================================


# ============================================================
# P0-CAPITAL-001：同花顺全市场批量资金流向源
# 单次调用返回全 A 股当日资金流向（5197+只），从根因消除东方财富逐只限流。
# 1 小时缓存（避免重复下载，单次约 6-12s）。
# 018: 同花顺净额为辅助指标（全部资金净流入），非主力净流入。
# 主力净流入由东方财富逐只采集提供（含分层：超大单/大单/中单/小单）。
# ============================================================
_THS_CAPITAL_CACHE = {'data': None, 'ts': 0.0}  # 模块级缓存：{DataFrame, 时间戳}
_THS_CAPITAL_CACHE_TTL = 3600  # 缓存有效期（秒）= 1 小时
_THS_CONSECUTIVE_FAIL_COUNT = 0  # FIX-B：THS连续失败计数
_THS_FAIL_THRESHOLD = 3  # FIX-B：连续失败阈值，达到后标记降级
_THS_REQUEST_TIMEOUT = 60  # 019I：单次 THS 接口请求超时（秒）


def _fetch_capital_flow_ths_batch():
    """
    调用同花顺 stock_fund_flow_individual() 获取全 A 股当日资金流向。
    带模块级缓存（1小时TTL），避免批量场景重复下载。
    FIX-B：THS失败时重试1次（5秒间隔），并尝试备选接口 stock_individual_fund_flow_rank()。
    返回 pandas.DataFrame 或 None。
    """
    global _THS_CONSECUTIVE_FAIL_COUNT

    now_ts = time.time()
    if (
        _THS_CAPITAL_CACHE['data'] is not None
        and (now_ts - _THS_CAPITAL_CACHE['ts']) < _THS_CAPITAL_CACHE_TTL
    ):
        logger.info('[同花顺批量] 命中内存缓存（1小时TTL内），直接复用')
        return _THS_CAPITAL_CACHE['data']

    # FIX-B：连续失败达阈值时跳过THS，直接返回None（由调用方回退EM逐只）
    if _THS_CONSECUTIVE_FAIL_COUNT >= _THS_FAIL_THRESHOLD:
        logger.warning(
            f'[同花顺批量] 连续失败已达阈值({_THS_CONSECUTIVE_FAIL_COUNT})，跳过THS，回退EM逐只'
        )
        return None

    # 019I：THS 接口调用增加超时保护，防止服务器不响应时无限阻塞
    # M-1 修正：禁止使用 with ThreadPoolExecutor（with 退出时 shutdown(wait=True)
    #          会 join 挂死线程，修复无效——经架构师运行时实验 + PM 独立复现确认）
    # 改用 daemon 线程 join(timeout) 模式：
    #   - daemon 线程不参与解释器退出 join，进程退出不被阻塞（R-1 消除）
    #   - t.join(timeout=N) 超时后立即返回，不等待挂死线程
    import threading as _threading_019I

    def _call_with_timeout(fn, label):
        """019I：daemon 线程包装 THS 接口调用，超时返回 (None, True)，正常返回 (result, False)"""
        box = {}
        t = _threading_019I.Thread(
            target=lambda: box.update(r=fn()),
            daemon=True,
        )
        t.start()
        t.join(timeout=_THS_REQUEST_TIMEOUT)
        if t.is_alive():
            logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
            return None, True
        return box.get('r'), False

    # FIX-B：主接口 stock_fund_flow_individual()
    df, _primary_timed_out = _call_with_timeout(_try_ths_primary, '主接口')

    # 019I M-2：主接口超时（hang）视为服务器不响应，跳过重试直接尝试备选
    #（THS 阶段上界 185s→120s；非超时的普通失败仍按 FIX-B 重试1次）
    if df is None and not _primary_timed_out:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df, _ = _call_with_timeout(_try_ths_primary, '主接口(重试)')

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df, _ = _call_with_timeout(_try_ths_rank_backup, '备选接口')

    if df is not None:
        _THS_CONSECUTIVE_FAIL_COUNT = 0  # 成功则重置计数
        _THS_CAPITAL_CACHE['data'] = df
        _THS_CAPITAL_CACHE['ts'] = time.time()
        logger.info(f'[同花顺批量] 获取成功: {len(df)} 只股票当日资金流向')
        return df

    # 全部失败
    _THS_CONSECUTIVE_FAIL_COUNT += 1
    logger.warning(f'[同花顺批量] 全部接口失败，连续失败计数={_THS_CONSECUTIVE_FAIL_COUNT}')
    return None


def _try_ths_primary():
    """FIX-B：主接口 ak.stock_fund_flow_individual()"""
    try:
        logger.info('[同花顺批量] 请求 stock_fund_flow_individual()（全市场资金流向）...')
        df = ak.stock_fund_flow_individual()
        if df is None or df.empty:
            logger.warning('[同花顺批量] 主接口返回空数据')
            return None
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 主接口获取失败: {e}')
        return None


def _try_ths_rank_backup():
    """FIX-B：备选接口 ak.stock_individual_fund_flow_rank(indicator='今日')
    列名与主接口不同，统一映射为 {股票代码, 净额, 成交额} 供后续处理。"""
    try:
        logger.info('[同花顺批量] 请求 stock_individual_fund_flow_rank(indicator=今日)...')
        df = ak.stock_individual_fund_flow_rank(indicator='今日')
        if df is None or df.empty:
            logger.warning('[同花顺批量] 备选接口返回空数据')
            return None
        # 列名映射：备选接口 → 主接口格式
        col_map = {
            '代码': '股票代码',
            '今日主力净流入-净额': '净额',
            '今日成交额': '成交额',
        }
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        # 确保有核心列
        if '股票代码' not in df.columns:
            logger.warning(f'[同花顺批量] 备选接口缺少股票代码列，实际列: {list(df.columns)}')
            return None
        logger.info(f'[同花顺批量] 备选接口获取成功: {len(df)} 行')
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 备选接口获取失败: {e}')
        return None
