"""
数据完整性驱动的持续补采调度器（Backfill Scheduler）

背景：外部数据源（东方财富等）不稳定时，单次采集会遗留缺口
（资金面缺当日真实数据、基本面无数据、K线/消息面缺失等）。
本模块提供"缺口检测 + 周期重试 + 自动退避 + 完整后低频巡检"的
持续补采机制，直到数据完整。

策略：
1. 工作日每 30 分钟检测一次缺口（threading.Timer 串联，不重叠）
2. 缺口维度判定（相对日期容忍周末/节假日）：
   - kline      : 最新K线 < 3 天前（缺失）
   - fundamental: 完全无数据（增量 TTL 门控负责新鲜度）
   - capital    : 当日无资金面数据（盘后东财出当日数据；盘前判定会自然退避）
   - news       : 3 天内无消息面聚合
3. 每轮最多补 MAX_PER_ROUND 只（按缺口维度数优先），
   复用 collect_stock_data 统一入口（内部增量门控跳过新鲜维度）
4. 退避：本轮失败率 >= 80% → 间隔翻倍（30→60→120 分钟上限）；
   有成功 → 重置 30 分钟
5. 全部完整 → 降为 4 小时低频巡检；新缺口出现 → 恢复 30 分钟
6. 并发防护：复用 daily_report._generate_lock（与日报/手动批次互斥写库）

启动：app.py main() 中调用 start_backfill_scheduler()。
"""

import atexit
import logging
import threading
from datetime import datetime, timedelta, timezone

from database.db_manager import get_connection

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# ============================================================
# 策略参数（可调）
# ============================================================
BASE_INTERVAL_MIN = 30   # 基础检查间隔（分钟）
MAX_INTERVAL_MIN = 120   # 退避间隔上限（分钟）
IDLE_INTERVAL_MIN = 240  # 全部完整后的低频巡检间隔（分钟）
MAX_PER_ROUND = 5        # 每轮最多补采股票数
FAIL_RATE_TO_BACKOFF = 0.8  # 本轮失败率 >= 此值时触发退避

# ============================================================
# 调度器状态
# ============================================================
_scheduler_started = False
_timer = None
_backoff_min = BASE_INTERVAL_MIN
_atexit_registered = False


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
    cursor.execute(
        """
        SELECT s.id, s.symbol, s.name, s.market,
               (SELECT MAX(k.trade_date) FROM raw_kline k WHERE k.stock_id = s.id) AS latest_kline,
               EXISTS(SELECT 1 FROM raw_fundamental f WHERE f.stock_id = s.id) AS has_fund,
               EXISTS(SELECT 1 FROM raw_capital_flow rc
                      WHERE rc.stock_id = s.id AND rc.trade_date = date('now', 'localtime')) AS has_capital_today,
               EXISTS(SELECT 1 FROM news_sentiment ns
                      WHERE ns.stock_id = s.id AND ns.news_date >= date('now', 'localtime', '-3 day')) AS has_news_recent
        FROM stocks s
        WHERE s.status = 'active'
        ORDER BY s.id
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    today = datetime.now(_CN_TZ).date()
    kline_cutoff = (today - timedelta(days=3)).isoformat()

    gaps = {}
    for r in rows:
        dims = []
        latest_kline = str(r['latest_kline'] or '')[:10]
        if not latest_kline or latest_kline < kline_cutoff:
            dims.append('kline')
        if not r['has_fund']:
            dims.append('fundamental')
        if not r['has_capital_today']:
            dims.append('capital')
        if not r['has_news_recent']:
            dims.append('news')
        if dims:
            gaps[r['id']] = {
                'symbol': r['symbol'],
                'name': r['name'],
                'market': r['market'],
                'dims': dims,
            }
    return gaps


def _collect_one(stock_id, symbol, market):
    """对单只股票执行补采（复用 collect_stock_data 增量门控）。

    与日报流程互斥写库：复用 daily_report._generate_lock（短超时）。
    返回 True 表示本轮采集无 failed 维度。
    """
    from modules.daily_report import _generate_lock

    if not _generate_lock.acquire(timeout=5):
        logger.warning(f'[补采] {symbol} 获取生成锁超时（可能与日报/手动批次并发），跳过本轮')
        return False
    try:
        from modules.data_collector import collect_stock_data

        result = collect_stock_data(symbol, market)
        # 判定成功：各维度值均不以 'failed' 开头（值形如 ('ok', msg) / ('failed', msg)）
        if not isinstance(result, dict):
            return False
        failed = [
            k for k, v in result.items()
            if isinstance(v, (tuple, list)) and v and str(v[0]).startswith('failed')
        ]
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
            if _collect_one(stock_id, info['symbol'], info['market']):
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
