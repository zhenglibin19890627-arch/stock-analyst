"""
数据完整性驱动的持续补采调度器（Backfill Scheduler）

背景：外部数据源（东方财富等）不稳定时，单次采集会遗留缺口
（资金面缺当日真实数据、基本面无数据、K线/消息面缺失等）。
本模块提供"缺口检测 + 周期重试 + 自动退避 + 完整后低频巡检"的
持续补采机制，直到数据完整。

策略：
1. 每 30 分钟检测一次缺口（threading.Timer 串联，不重叠）
2. 缺口维度判定（020H：基于"全市场 K 线并集"的交易日历，覆盖近 10 个交易日）：
   - kline      : 近 10 个交易日中任一交易日缺失该股 K 线
   - capital    : 近 10 个交易日中任一交易日缺失该股主力净流入（真实数据行）
   - ths        : 最新交易日缺失同花顺辅助净额（仅 A 股；随本轮批量刷新一次）
   - fundamental: 完全无数据（增量 TTL 门控负责新鲜度）
   - news       : 3 天内无消息面聚合
3. 每轮最多补 MAX_PER_ROUND 只（按缺口维度数优先），
   复用 collect_stock_data 统一入口（内部增量门控跳过新鲜维度）；
   同花顺净额由本轮一次性批量刷新（fetch_capital_flow_batch）。
4. 退避：本轮失败率 >= 80% → 间隔翻倍（30→60→120 分钟上限）；
   有成功 → 重置 30 分钟
5. 全部完整 → 降为 4 小时低频巡检；新缺口出现 → 恢复 30 分钟
6. 并发防护：复用 daily_report._generate_lock（与日报/手动批次互斥写库）

启动：app.py main() 中调用 start_backfill_scheduler()。
"""

import atexit
import logging
import threading

from database.db_manager import get_connection

logger = logging.getLogger(__name__)

# ============================================================
# 策略参数（可调）
# ============================================================
BASE_INTERVAL_MIN = 30   # 基础检查间隔（分钟）
MAX_INTERVAL_MIN = 120   # 退避间隔上限（分钟）
IDLE_INTERVAL_MIN = 240  # 全部完整后的低频巡检间隔（分钟）
MAX_PER_ROUND = 5        # 每轮最多补采股票数
FAIL_RATE_TO_BACKOFF = 0.8  # 本轮失败率 >= 此值时触发退避
GAP_WINDOW_TRADING_DAYS = 10  # 020H：完整性校验窗口（近 N 个交易日）

# ============================================================
# 调度器状态
# ============================================================
_scheduler_started = False
_timer = None
_backoff_min = BASE_INTERVAL_MIN
_atexit_registered = False


def _recent_trading_days(n=GAP_WINDOW_TRADING_DAYS):
    """以全市场 K 线日期并集为交易日历，返回最近 n 个交易日（ISO 日期，降序）。

    并集口径：任一股票出现过的交易日即计入日历——个别股票 K 线滞后
    不影响日历完整性；周末/节假日天然不含行（非交易日），
    因此资金面/K线缺口判定不再受"自然日当天无数据"误判。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT substr(trade_date, 1, 10) AS d FROM raw_kline "
        "WHERE trade_date >= date('now', 'localtime', '-40 day') "
        "ORDER BY d DESC"
    )
    days = [r['d'] for r in cursor.fetchall()]
    conn.close()
    return days[:n]


def _get_stocks_with_gaps():
    """检测全自选股各维度缺口，返回 {stock_id: [缺失维度列表]}。

    缺口判定（相对日期，容忍周末/节假日）：
      kline      : 最新K线日期 < 3 天前
      fundamental: raw_fundamental 完全无数据（TTL 门控管新鲜度）
      capital    : 当日无任何资金面数据（东财真实或降级均可，代表"已尝试"；
                   盘前判定为缺 → 补采失败 → 自然退避，盘后成功）
      news       : 3 天内无消息面聚合
    """
    conn = get_connection()
    cursor = conn.cursor()
    cal = _recent_trading_days()
    if not cal:
        logger.warning('[补采] 未获取到交易日历（K线数据为空），跳过本轮')
        return {}
    latest_td = cal[0]
    oldest_td = cal[-1]

    cursor.execute(
        "SELECT id, symbol, name, market FROM stocks WHERE status='active' ORDER BY id"
    )
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    gaps = {}
    conn = get_connection()
    cursor = conn.cursor()
    for s in stocks:
        dims = []
        kd = {r['d'] for r in cursor.execute(
            "SELECT DISTINCT substr(trade_date, 1, 10) d FROM raw_kline "
            "WHERE stock_id=? AND trade_date>=?", (s['id'], oldest_td))}
        if any(d not in kd for d in cal):
            dims.append('kline')
        cd = {r['d'] for r in cursor.execute(
            "SELECT DISTINCT substr(trade_date, 1, 10) d FROM raw_capital_flow "
            "WHERE stock_id=? AND main_net_inflow IS NOT NULL AND trade_date>=?",
            (s['id'], oldest_td))}
        miss_c = [d for d in cal if d not in cd]
        if miss_c:
            dims.append('capital')
        if s['market'] == 'a_stock':
            has_ths = cursor.execute(
                'SELECT 1 FROM raw_capital_flow WHERE stock_id=? AND trade_date=? '
                'AND ths_net_inflow IS NOT NULL', (s['id'], latest_td)).fetchone()
            if not has_ths:
                dims.append('ths')
        if not cursor.execute(
            'SELECT 1 FROM raw_fundamental f WHERE f.stock_id=?', (s['id'],)
        ).fetchone():
            dims.append('fundamental')
        if not cursor.execute(
            "SELECT 1 FROM news_sentiment ns WHERE ns.stock_id=? "
            "AND ns.news_date >= date('now', 'localtime', '-3 day')", (s['id'],)
        ).fetchone():
            dims.append('news')
        if dims:
            gaps[s['id']] = {
                'symbol': s['symbol'],
                'name': s['name'],
                'market': s['market'],
                'dims': dims,
                'capital_dates': miss_c if 'capital' in dims else [],
            }
    conn.close()
    return gaps


def _collect_one(stock_id, symbol, market, missing_cap_dates=None):
    """对单只股票执行补采（复用 collect_stock_data 增量门控）。

    与日报流程互斥写库：复用 daily_report._generate_lock（短超时）。
    返回 True 表示本轮采集无 failed 维度。
    020H：东财熔断期间，若该股存在资金面历史缺口日，追加逐日回填（020I 链序：腾讯 westock --date → 新浪 lscjfb）。
    """
    from modules.daily_report import _generate_lock

    if not _generate_lock.acquire(timeout=5):
        logger.warning(f'[补采] {symbol} 获取生成锁超时（可能与日报/手动批次并发），跳过本轮')
        return False
    try:
        from modules.data_collector import collect_stock_data

        result = collect_stock_data(symbol, market)
        failed = []
        if isinstance(result, dict):
            failed = [
                k for k, v in result.items()
                if isinstance(v, (tuple, list)) and v and str(v[0]).startswith('failed')
            ]

        # 020H：东财熔断期间逐日回填历史资金面缺口（新浪 lscjfb）
        if missing_cap_dates:
            try:
                import modules.data_collector as dc

                if dc._em_banned():
                    filled = dc.backfill_capital_history(symbol, market, missing_cap_dates)
                    logger.info(
                        f'[补采] {symbol} 历史资金面逐日回填: {len(filled)}/{len(missing_cap_dates)} 天'
                    )
            except Exception as e:
                logger.warning(f'[补采] {symbol} 历史资金面回填异常: {e}')

        if failed:
            logger.warning(f'[补采] {symbol} 本轮仍有失败维度: {failed}')
            return False
        return True
    except Exception as e:
        logger.error(f'[补采] {symbol} 采集异常: {e}', exc_info=True)
        return False
    finally:
        _generate_lock.release()


def _tick():
    """补采调度器 tick：检测缺口 → 补采 → 按结果调整间隔并注册下轮"""
    global _backoff_min
    try:
        gaps = _get_stocks_with_gaps()
        if not gaps:
            logger.info('[补采] 数据完整，降为低频巡检（%d 分钟）', IDLE_INTERVAL_MIN)
            _schedule_next(IDLE_INTERVAL_MIN)
            return

        # 020H：同花顺净额缺口 → 本轮一次性批量刷新（一次 HTTP 调用覆盖指定 A 股；
        # 周末由 fetch_capital_flow_batch 内部 019G 校验自动跳过）
        ths_symbols = [
            v['symbol'] for v in gaps.values()
            if v['market'] == 'a_stock' and 'ths' in v['dims']
        ]
        if ths_symbols:
            try:
                from modules.data_collector import fetch_capital_flow_batch

                batch = fetch_capital_flow_batch(ths_symbols)
                logger.info('[补采] 同花顺批量已随本轮刷新: %s', batch.get('source', ''))
            except Exception as e:
                logger.warning(f'[补采] 同花顺批量刷新失败: {e}')

        # 按缺口维度数排序，本轮取前 MAX_PER_ROUND 只
        ranked = sorted(
            gaps.items(), key=lambda kv: (len(kv[1]['dims']), kv[0]), reverse=True
        )[:MAX_PER_ROUND]
        logger.info(
            '[补采] 检测到 %d 只股票有缺口，本轮处理 %d 只: %s',
            len(gaps), len(ranked),
            [f"{v['symbol']}({'/'.join(v['dims'])})" for _, v in ranked],
        )

        ok = fail = 0
        for stock_id, info in ranked:
            if _collect_one(
                stock_id, info['symbol'], info['market'],
                missing_cap_dates=info.get('capital_dates') or [],
            ):
                ok += 1
            else:
                fail += 1

        total = ok + fail
        fail_rate = fail / total if total else 0.0
        if fail_rate >= FAIL_RATE_TO_BACKOFF:
            # 数据源大概率不可达：退避（上限 MAX_INTERVAL_MIN）
            _backoff_min = min(_backoff_min * 2, MAX_INTERVAL_MIN)
            logger.warning(
                '[补采] 本轮失败率 %.0f%%（%d/%d），退避至 %d 分钟',
                fail_rate * 100, fail, total, _backoff_min,
            )
        elif ok > 0:
            _backoff_min = BASE_INTERVAL_MIN
            logger.info('[补采] 本轮成功 %d 只，重置为 %d 分钟', ok, _backoff_min)

        _schedule_next(_backoff_min)
    except Exception as e:
        logger.error(f'[补采] tick 异常: {e}', exc_info=True)
        _schedule_next(_backoff_min)


def _schedule_next(interval_min):
    global _timer
    _timer = threading.Timer(interval_min * 60, _tick)
    _timer.daemon = True
    _timer.start()
    logger.info('[补采] 下次检查: %d 分钟后', interval_min)


def start_backfill_scheduler():
    """启动补采调度器（幂等，app.py main() 调用）"""
    global _scheduler_started, _atexit_registered, _backoff_min
    if _scheduler_started:
        return
    _scheduler_started = True
    _backoff_min = BASE_INTERVAL_MIN
    if not _atexit_registered:
        atexit.register(stop_backfill_scheduler)
        _atexit_registered = True
    _schedule_next(BASE_INTERVAL_MIN)
    logger.info('数据补采调度器已启动（每 %d 分钟检查，自动退避至 %d 分钟上限）', BASE_INTERVAL_MIN, MAX_INTERVAL_MIN)


def stop_backfill_scheduler():
    """停止补采调度器（进程退出时调用）"""
    global _timer, _scheduler_started
    if _timer is not None:
        _timer.cancel()
        _timer = None
    _scheduler_started = False
    logger.info('数据补采调度器已停止')
