"""
US-11 每日报告模块 (Daily Report)

基于 v5.0 引擎为自选股生成每日分析报告，含评分变动、关键因子异动、降级提示。

功能：
1. 批量调用 advisor.generate_advice() 生成报告（统一走灰度控制器分流）
2. 单只失败不阻塞，标记 failed 并记录错误
3. 输出 Markdown 结构化报告 + 写入 daily_reports 表
4. 支持定时触发（threading.Timer）和手动触发
5. 幂等性：同一天重复生成覆盖旧报告（UPSERT）

强制修正项1（调度器生命周期管理）：
- 全局标志位 _scheduler_started 防止重复注册
- atexit 钩子取消定时器
- 仅当 WERKZEUG_RUN_MAIN=='true' 时启动（避免 Flask reloader 误触发）
- 内存锁 _generate_lock 防止并发写入冲突（修正项2-防抖保护）
"""

import atexit
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BATCH_TIMEOUT_SECONDS, STOCK_TIMEOUT_SECONDS
from database.db_manager import get_connection

# 019A: 收敛三表一致逻辑，关键因子/Markdown 构建函数统一从 advisor 导入
# 避免 daily_report 与 advisor 重复定义导致 drift
from modules.advisor import _build_key_factors, _build_markdown_single, generate_advice

# FIX-A：日报流程集成数据采集
from modules.data_collector import collect_stock_data, fetch_capital_flow_batch

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
_REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')

# ================================================================
# 强制修正项1：调度器生命周期管理
# ================================================================

_scheduler_started = False
_scheduler_timer = None
_optimizer_timer = None  # M9: 每周优化定时器
_capital_retry_timer = None  # 019Q: 资金面延迟补采一次性 Timer（30分钟）
_atexit_registered = False  # 标记 atexit 钩子是否已注册（供测试验证）
_generate_lock = threading.Lock()  # 修正项2：防抖保护

# 019X T2：资金流采集三窗调度（错峰拆分，降低单窗口东财请求密度）
# 窗1(16:10)/窗2(16:40)/窗3(17:10)，每窗采集资金流东财清单的 1/3（按代码排序固定切分）；
# 窗1/窗2 只采集不生成报告；窗3 采集完成后执行一次完整日报流程。
_CAPITAL_WINDOW_COUNT = 3
_CAPITAL_WINDOW_TIMES = ((16, 10), (16, 40), (17, 10))  # 窗1/窗2/窗3 固定钟点


def _scheduler_tick(window_idx=0):
    """定时器回调：T2 三窗调度（窗1/窗2 只采集资金流东财清单的1/3，窗3 采集后执行完整日报流程）

    窗间串联：本窗任务开始时预先注册下一窗的一次性 daemon Timer（固定钟点，
    与 _schedule_capital_retry 同型）；若前窗超时未结束，后窗触发时获取生成锁
    超时即跳过并记日志，剩余股票由补采链路兜底。
    """
    global _scheduler_timer
    try:
        if window_idx < _CAPITAL_WINDOW_COUNT - 1:
            _register_capital_window(window_idx + 1)
        _run_capital_window(window_idx)
    except Exception as e:
        logger.error(f'定时调度器执行异常: {e}', exc_info=True)
    finally:
        # 窗3 结束后注册次日窗1（16:10）
        if window_idx >= _CAPITAL_WINDOW_COUNT - 1:
            _schedule_next()


def _split_em_capital_list(a_symbols):
    """019X T2：资金流东财清单按代码排序固定切分为 1/3 三份（可复现切分）"""
    sorted_symbols = sorted(a_symbols)
    n = len(sorted_symbols)
    if n == 0:
        return [[], [], []]
    size = (n + _CAPITAL_WINDOW_COUNT - 1) // _CAPITAL_WINDOW_COUNT
    return [
        sorted_symbols[i * size:(i + 1) * size]
        for i in range(_CAPITAL_WINDOW_COUNT)
    ]


def _run_capital_window(window_idx):
    """019X T2：单窗任务体。窗1/窗2 只采集资金流东财清单的1/3；
    窗3 采集完成后执行一次完整日报流程（挂载顺序与现状一致）。

    并发防护：复用 _generate_lock（后窗触发时前窗未结束则跳过并记日志，
    剩余股票由补采链路兜底）；窗3 的日报流程内部自行获取生成锁，
    故本处采集完毕立即释放，避免死锁。
    """
    try:
        a_symbols = sorted(s['symbol'] for s in _get_all_stocks() if s['market'] == 'a_stock')
        third = _split_em_capital_list(a_symbols)[window_idx]

        if not _generate_lock.acquire(timeout=5):
            logger.warning(
                f'[资金流采集窗] 第{window_idx + 1}窗触发时前一任务仍在运行'
                f'（获取生成锁超时），跳过本窗采集，剩余股票由补采链路兜底'
            )
            return
        try:
            if third:
                logger.info(
                    f'[资金流采集窗] 第{window_idx + 1}窗开始采集'
                    f'（{len(third)}/{len(a_symbols)}只）: {third}'
                )
                result = fetch_capital_flow_batch(third)
                logger.info(f'[资金流采集窗] 第{window_idx + 1}窗采集完成: {result}')
            else:
                logger.info(f'[资金流采集窗] 第{window_idx + 1}窗清单为空，跳过采集')
        except Exception as e:
            logger.error(f'[资金流采集窗] 第{window_idx + 1}窗采集异常（仅记日志，不阻塞调度）: {e}', exc_info=True)
        finally:
            _generate_lock.release()
    except Exception as e:
        logger.error(f'[资金流采集窗] 第{window_idx + 1}窗任务异常: {e}', exc_info=True)

    if window_idx >= _CAPITAL_WINDOW_COUNT - 1:
        _run_full_report_flow()


def _register_capital_window(window_idx):
    """019X T2：注册第 window_idx+1 个资金流采集窗（一次性 daemon Timer，与 _schedule_capital_retry 同型）

    固定钟点触发；若目标钟点已过（前窗超时），尽快补触发（1秒后），
    保证后窗不因前窗耗时而被无限顺延。
    """
    global _scheduler_timer
    hour, minute = _CAPITAL_WINDOW_TIMES[window_idx]
    now = datetime.now(_CN_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delay = (target - now).total_seconds()
    if delay <= 0:
        delay = 1  # 已过固定钟点，尽快补触发
    _scheduler_timer = threading.Timer(delay, _scheduler_tick, args=(window_idx,))
    _scheduler_timer.daemon = True
    _scheduler_timer.start()
    logger.info(
        f'下次资金流采集窗{window_idx + 1}/3: {target.strftime("%Y-%m-%d %H:%M")}'
        f' ({delay:.0f}秒后)'
    )


def _run_full_report_flow():
    """019X T2：窗3 采集完成后执行一次完整日报流程（挂载顺序与现状完全一致）

    日报生成 → P3-B 预警扫描 → 延迟补采注册 → 指数刷新，各环节异常隔离仅记日志。
    """
    logger.info('定时调度器触发每日报告生成')
    generate_daily_report()

    # P3-B: 日报生成后挂载预警扫描（异常隔离，不阻塞日报）
    # 架构师 D1 评审：双层异常隔离，预警扫描失败仅记日志
    try:
        from modules.alert_engine import scan_once

        scan_once()
    except Exception as e:
        logger.error(f'P3-B 预警扫描异常（不阻塞日报）: {e}', exc_info=True)

    # 019Q Task 5：延迟自动补采注册点（D-3 裁定）
    # generate_daily_report() 返回后、_schedule_next() 前调用；不注册在
    # generate_daily_report 内部——该函数同时被 app.py 手动 API 与 force 重跑
    # 调用，内部注册会让手动触发产生 30 分钟延迟副作用（D-3 裁定）。
    # 缺口数 > 0 且工作日才注册；一次性 daemon Timer(1800)，回调内不再注册
    # → 天然满足"仍失败不再重试，等待次日批次"。异常隔离仅记日志，不阻塞调度。
    try:
        _stocks = _get_all_stocks()
        _a_symbols = [s['symbol'] for s in _stocks if s['market'] == 'a_stock']
        _schedule_capital_retry(_a_symbols)
    except Exception as e:
        logger.error(f'[资金面补采] 延迟补采注册异常（不阻塞调度）: {e}', exc_info=True)

    # 019T T3（评审 P-3 / R-5 修复）：指数定时刷新挂载点
    # generate_daily_report() 返回后、_schedule_next() 前调用；指数刷新此前仅
    # 依赖手动 API（POST /api/index-ratings/refresh），导致 index_kline 长期滞后
    # （库内止于 08-05，08-06/08-07 缺失）。7 只指数、耗时可忽略；异常隔离只记
    # 日志，与 P3-B 预警扫描同一挂载模式，不阻塞调度。
    try:
        from modules.index_collector import refresh_all

        refresh_all()
    except Exception as e:
        logger.error(f'指数定时刷新异常（不阻塞调度）: {e}', exc_info=True)


def _schedule_next():
    """019X T2：窗3 结束后注册次日窗1（16:10）"""
    global _scheduler_timer
    now = datetime.now(_CN_TZ)
    tomorrow = now.replace(hour=16, minute=10, second=0, microsecond=0) + timedelta(days=1)
    delay = (tomorrow - now).total_seconds()
    _scheduler_timer = threading.Timer(delay, _scheduler_tick, args=(0,))
    _scheduler_timer.daemon = True
    _scheduler_timer.start()
    logger.info(f'下次定时报告: {tomorrow.strftime("%Y-%m-%d %H:%M")} ({delay:.0f}秒后)')


def start_scheduler():
    """启动定时调度器（仅 Flask 主进程调用）

    强制修正项1实现：
    ① 全局标志位 _scheduler_started 防止重复注册
    ② atexit 钩子确保进程退出时取消定时器
    ③ 仅当 WERKZEUG_RUN_MAIN=='true' 时启动（避免 reloader 主进程误触发）
    """
    global _scheduler_started

    if _scheduler_started:
        logger.info('定时调度器已启动，跳过重复注册')
        return

    # Flask debug 模式下，reloader 会启动两个进程
    # 仅在子进程（实际运行 Flask 的进程）中启动定时器
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # 非 debug 模式直接运行（python app.py 无 debug）
        # 或者 debug 模式的主进程，都不启动
        # 判断是否为 debug 模式
        from config import FLASK_DEBUG

        if FLASK_DEBUG:
            logger.info('检测到 Flask debug 模式，定时器将在 reloader 子进程中启动')
            return
        # 非 debug 模式，继续启动

    _scheduler_started = True

    # 注册 atexit 钩子
    atexit.register(stop_scheduler)
    global _atexit_registered
    _atexit_registered = True

    now = datetime.now(_CN_TZ)
    last_window_time = now.replace(hour=17, minute=10, second=0, microsecond=0)
    if now >= last_window_time:
        # 019Z: 今天三窗已全部结束（17:10之后启动），排到明天
        _schedule_next()
    else:
        # 019Z: 今天还有窗未到，注册今天的窗1（已过钟点由1秒补触发兜底）
        _register_capital_window(0)
    _schedule_optimizer_next()  # M9: 启动每周优化定时器
    logger.info('✅ 每日报告定时调度器已启动（默认每日16:10，每周日20:00自动优化）')


def stop_scheduler():
    """停止定时调度器（进程退出时调用）"""
    global _scheduler_timer, _optimizer_timer, _scheduler_started, _capital_retry_timer
    if _scheduler_timer is not None:
        _scheduler_timer.cancel()
        _scheduler_timer = None
    if _optimizer_timer is not None:
        _optimizer_timer.cancel()
        _optimizer_timer = None
    if _capital_retry_timer is not None:
        # 019Q Task 5.6：防御性取消未触发的资金面补采 Timer
        # （daemon 线程进程退出即亡，此处为防御性收尾）
        _capital_retry_timer.cancel()
        _capital_retry_timer = None
    _scheduler_started = False
    logger.info('定时调度器已停止')


# ================================================================
# 019Q Task 5：资金面延迟自动补采（D-3 裁定：甲+乙融合）
# 注册点：_scheduler_tick（generate_daily_report 返回后、_schedule_next 前）
# 触发条件：缺口数 > 0 且工作日（周一~周五，019G 同型判定）
# 任务体：_generate_lock 短超时 + 复用 fetch_capital_flow_batch（019E 补采清单入口）
# 一次性：threading.Timer(1800)、daemon=True（与 _schedule_next 同型，L85-86）
# ================================================================


def _capital_retry_once(a_symbols):
    """019Q Task 5：延迟补采任务体（一次性，回调内不注册下一次）

    先 _generate_lock.acquire(timeout=5) 防与手动批次并发写库（R-6），拿不到即放弃
    本轮（手动批次本身含资金面采集，放弃无害）；拿到后调用
    fetch_capital_flow_batch(a_symbols)——复用 019E 补采清单入口：只有东财真数据
    （capital_source IS NULL 且非估算）才算"已完成"；sina_main / ths_total 行仍
    进入补采清单 —— 东财 30 分钟内恢复时可覆盖回补（"东财恢复后自动回补"的实现），
    新浪重采不降级已有数据（019Q QA F9 实证）。019S：主力净流入链路为东财三层 →
    新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评），ths_total 仅为
    历史存量（处置后清零），字面量保留仅为防御。异常隔离仅记日志。
    """
    if not _generate_lock.acquire(timeout=5):
        logger.warning('[资金面补采] 获取生成锁超时（可能与手动批次并发），放弃本轮延迟补采')
        return
    try:
        logger.info(f'[资金面补采] 延迟补采开始（30分钟一次性），待采: {a_symbols}')
        result = fetch_capital_flow_batch(a_symbols)
        logger.info(f'[资金面补采] 延迟补采完成: {result}')
    except Exception as e:
        logger.error(f'[资金面补采] 延迟补采异常（仅记日志，不再重试）: {e}', exc_info=True)
    finally:
        _generate_lock.release()


def _schedule_capital_retry(a_symbols):
    """019Q Task 5：延迟自动补采注册（模块级，仅由 _scheduler_tick 调用）

    缺口判定（M-6，必须带 is_estimated 条件）：
    len(a_symbols) - COUNT(当日 raw_capital_flow WHERE stock_id IN a_symbols
      AND capital_source IS NULL AND (is_estimated=0 OR is_estimated IS NULL)) > 0
    估算兜底行 capital_source=NULL（DB 实证）——若缺口 SQL 只判 capital_source IS NULL
    会把估算行误计为"EM 成功"→ 延迟补采永不触发；必须附加 is_estimated 条件（M-6）。
    """
    global _capital_retry_timer
    now = datetime.now(_CN_TZ)
    # 工作日（周一~周五，019G 同型判定）才注册；非交易日不注册（R-4 双保险第2道）
    if now.weekday() >= 5:  # 5=周六, 6=周日
        logger.info(f'[资金面补采] 非交易日（{now.strftime("%A")}），不注册延迟补采')
        return
    if not a_symbols:
        return
    if _capital_retry_timer is not None and _capital_retry_timer.is_alive():
        logger.info('[资金面补采] 已有未触发的延迟补采 Timer，跳过重复注册')
        return

    today_str = now.strftime('%Y-%m-%d')
    gap = 0
    try:
        placeholders = ','.join('?' for _ in a_symbols)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT COUNT(DISTINCT rc.stock_id) FROM raw_capital_flow rc '
            f'JOIN stocks s ON s.id = rc.stock_id '
            f'WHERE s.symbol IN ({placeholders}) AND s.market = ? AND rc.trade_date = ? '
            f'AND rc.capital_source IS NULL '
            f'AND (rc.is_estimated = 0 OR rc.is_estimated IS NULL)',
            (*a_symbols, 'a_stock', today_str),
        )
        real_count = cursor.fetchone()[0]
        conn.close()
        gap = len(a_symbols) - real_count
    except Exception as e:
        logger.error(f'[资金面补采] 缺口统计异常（不注册）: {e}')
        return

    if gap <= 0:
        logger.info(f'[资金面补采] 无缺口（{len(a_symbols)} 只均有真实数据），不注册延迟补采')
        return

    _capital_retry_timer = threading.Timer(1800, _capital_retry_once, args=(a_symbols,))
    _capital_retry_timer.daemon = True
    _capital_retry_timer.start()
    logger.info(
        f'[资金面补采] 检测到 {gap}/{len(a_symbols)} 只缺口，30分钟后自动补采'
        f'（一次性，不再重试；与次日16:10批次无冲突）'
    )


# ================================================================
# M9: 每周自动优化调度（周日 20:00）
# ================================================================


def _optimizer_tick():
    """每周优化定时器回调：对A股和港股分别执行自动优化"""
    global _optimizer_timer
    try:
        from modules.optimizer_engine import OptimizerEngine

        engine = OptimizerEngine()
        logger.info('[M9] 每周自动优化开始')
        for mkt in ['a_stock', 'hk_stock']:
            result = engine.run_weekly_optimization(mkt)
            logger.info(
                f'[M9] {mkt}: adjusted={result.get("adjusted")}, reason={result.get("reason")}'
            )
    except Exception as e:
        logger.error(f'[M9] 每周优化执行异常: {e}', exc_info=True)
    finally:
        _schedule_optimizer_next()


def _schedule_optimizer_next():
    """计算到下一个周日 20:00 的秒数，注册下一次优化定时"""
    global _optimizer_timer
    now = datetime.now(_CN_TZ)
    # weekday(): Monday=0, Sunday=6
    days_until_sunday = (6 - now.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 20:
        days_until_sunday = 7  # 今天周日但已过20:00，等下周
    next_run = now.replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(
        days=days_until_sunday
    )
    delay = (next_run - now).total_seconds()
    _optimizer_timer = threading.Timer(delay, _optimizer_tick)
    _optimizer_timer.daemon = True
    _optimizer_timer.start()
    logger.info(f'[M9] 下次自动优化: {next_run.strftime("%Y-%m-%d %H:%M")} ({delay:.0f}秒后)')


# ================================================================
# 报告生成核心逻辑
# ================================================================


def _get_all_stocks():
    """获取所有自选股列表"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.symbol, s.name, s.market
        FROM stocks s
        WHERE s.status = 'active'
        ORDER BY s.id
    """)
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return stocks


def _get_prev_score(stock_id, report_date):
    """获取上一交易日的评分（用于计算分数变动）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT total_score FROM daily_reports
        WHERE stock_id = ? AND report_date < ? AND status = 'ok'
        ORDER BY report_date DESC LIMIT 1
    """,
        (stock_id, report_date),
    )
    row = cursor.fetchone()
    conn.close()
    return row['total_score'] if row else None


# 019A: 关键因子/Markdown 构建函数已收敛至 advisor 模块，此处统一导入
# （_build_key_factors / _build_markdown_single 见文件顶部 import）


def _save_report(
    report_date,
    stock_id,
    stock_code,
    stock_name,
    engine_version,
    total_score,
    rating,
    rating_label,
    prev_score,
    score_change,
    key_factors,
    data_warnings,
    markdown_content,
    status='ok',
    error_msg=None,
    price_advice=None,
    report_type='daily',
):
    """写入 daily_reports 表（DELETE + INSERT 幂等操作）

    013: report_type 区分 daily / intraday
      - daily: 删除当天该股票所有记录（含 intraday），插入新 daily（最终版）
      - intraday: 仅删除之前的 intraday，不动 daily
    """
    conn = get_connection()
    cursor = conn.cursor()
    generated_at = datetime.now(_CN_TZ).isoformat()

    if report_type == 'daily':
        # 盘后日报：删除当天该股票所有记录（含 intraday），插入新 daily
        cursor.execute(
            'DELETE FROM daily_reports WHERE report_date=? AND stock_id=?', (report_date, stock_id)
        )
    else:
        # 盘中快报：仅删除之前的 intraday，不动 daily
        cursor.execute(
            "DELETE FROM daily_reports WHERE report_date=? AND stock_id=? AND report_type='intraday'",
            (report_date, stock_id),
        )

    cursor.execute(
        """
        INSERT INTO daily_reports
            (report_date, stock_id, stock_code, stock_name, engine_version,
             total_score, rating, rating_label, prev_score, score_change,
             key_factors, data_warnings, status, error_msg, markdown_content,
             generated_at, price_advice, report_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            report_date,
            stock_id,
            stock_code,
            stock_name,
            engine_version,
            total_score,
            rating,
            rating_label,
            prev_score,
            score_change,
            json.dumps(key_factors, ensure_ascii=False) if key_factors else None,
            json.dumps(data_warnings, ensure_ascii=False) if data_warnings else None,
            status,
            error_msg,
            markdown_content,
            generated_at,
            json.dumps(price_advice, ensure_ascii=False) if price_advice else None,
            report_type,
        ),
    )
    conn.commit()
    conn.close()


# ================================================================
# 012-B: 进度追踪 + 超时控制辅助函数
# ================================================================

# 进度文件并发写锁（线程池内多只股票同时更新 stage 时防互相覆盖）
_progress_lock = threading.Lock()
# 进度文件路径（供查询 API 复用）
_REPORT_PROGRESS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'logs',
    'report_progress.json',
)


def _update_progress_file(data: dict):
    """012-B: 更新进度文件 logs/report_progress.json（线程安全）"""
    try:
        os.makedirs(os.path.dirname(_REPORT_PROGRESS_PATH), exist_ok=True)
        with _progress_lock:
            with open(_REPORT_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 进度文件写入失败不阻塞业务


def _update_progress_stage(symbol: str, stage: str, current: int = None):
    """增量更新进度文件的 stage（当前正在做什么）与 last_update。

    由工作线程（_process_single_stock）调用：读现有进度 → 更新 stage → 写回。
    失败静默，不阻塞采集流程。
    """
    try:
        with _progress_lock:
            data = {}
            try:
                with open(_REPORT_PROGRESS_PATH, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                return
            data['stage'] = stage
            if symbol:
                data['current_symbol'] = symbol
            if current is not None:
                data['current'] = current
            data['last_update'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
            with open(_REPORT_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 进度写入失败不阻塞业务


def _process_single_stock(stock, target_date, force, report_type='daily'):
    """012-B: 单只股票处理（供 ThreadPoolExecutor 调用）

    将原 for 循环体内的逻辑封装为独立函数。
    返回值与原有 results.append 结构一致。
    013: report_type 透传至 _save_report，并限定复用检查范围。
    """
    stock_id = stock['id']
    symbol = stock['symbol']
    name = stock.get('name', '')
    market = stock.get('market', 'a_stock')

    # B11-REPORT-REUSE：检查当日是否已有有效报告，有则跳过采集+分析
    # B15-T2: force=True 时跳过复用检查
    # 013: 复用检查限定为同 report_type，intraday 不会复用 daily
    if not force:
        conn_check = get_connection()
        cursor_check = conn_check.cursor()
        cursor_check.execute(
            'SELECT total_score, rating, rating_label, engine_version, key_factors, '
            'data_warnings, markdown_content, generated_at, prev_score, score_change '
            'FROM daily_reports WHERE stock_id=? AND report_date=? AND status="ok" '
            'AND report_type=?',
            (stock_id, target_date, report_type),
        )
        existing = cursor_check.fetchone()
        conn_check.close()
    else:
        existing = None

    if existing:
        # 今日已有有效报告，直接使用已有数据，跳过采集+分析
        logger.info(f'[{symbol}] 今日已有有效报告，跳过采集+分析')
        engine = existing['engine_version'] or 'legacy'
        total_score = existing['total_score'] or 0
        rating_val = existing['rating'] or ''
        score_change = existing['score_change']

        return {
            'stock_id': stock_id,
            'symbol': symbol,
            'name': name,
            'status': 'ok',
            'engine': engine,
            'score': total_score,
            'rating': rating_val,
            'score_change': score_change,
            'reused': True,
        }

    # FIX-A 改动2：每只股票先采集后分析
    # 012-B 增强：线程内更新进度 stage（采集阶段），前端进度条可显示"当前在干什么"
    _update_progress_stage(symbol, '采集数据中')
    collect_stock_data(symbol, market)
    _update_progress_stage(symbol, '分析评分中')
    # 统一调用 advisor.generate_advice()，由 engine_switcher 自动分流
    advice = generate_advice(stock_id, report_date=target_date)

    if not advice.get('success'):
        _update_progress_stage(symbol, '生成失败')
        raise Exception(advice.get('message', '生成失败'))

    _update_progress_stage(symbol, '写入报告')

    # 005: 日报集成价格建议
    from modules.price_advisor import generate_price_advice

    price_advice = generate_price_advice(stock_id, advice) if advice.get('success') else None

    engine = advice.get('engine_version', 'legacy')
    total_score = advice.get('total_score', 0)
    rating = advice.get('rating', '')
    rating_label = advice.get('rating_label', '')

    # 检测 fallback
    from modules.engine_switcher import should_use_v5

    expected_v5 = should_use_v5(stock_id)
    is_fallback = expected_v5 and engine != 'v5'
    if is_fallback:
        logger.warning(f'[{symbol}] v5引擎fallback触发，实际使用{engine}')

    # 获取前日分数
    prev_score = _get_prev_score(stock_id, target_date)
    score_change = round(total_score - prev_score, 1) if prev_score is not None else None

    # 构建关键因子
    key_factors = _build_key_factors(advice)

    # 构建单只 Markdown
    md_content = _build_markdown_single(advice, prev_score)

    # 写入数据库
    _save_report(
        report_date=target_date,
        stock_id=stock_id,
        stock_code=symbol,
        stock_name=name,
        engine_version=engine,
        total_score=total_score,
        rating=rating,
        rating_label=rating_label,
        prev_score=prev_score,
        score_change=score_change,
        key_factors=key_factors,
        data_warnings=advice.get('data_warnings', []),
        markdown_content=md_content,
        price_advice=price_advice,
        report_type=report_type,
    )

    return {
        'stock_id': stock_id,
        'symbol': symbol,
        'name': name,
        'status': 'ok',
        'engine': engine,
        'score': total_score,
        'rating': rating,
        'score_change': score_change,
        'reused': False,
        'is_fallback': is_fallback,
    }


def generate_daily_report(target_date=None, force=False, report_type='daily'):
    """生成每日分析报告

    Args:
        target_date: 报告日期(YYYY-MM-DD)，默认今天
        force: 强制全量刷新，忽略已有结果
        report_type: 报告类型 'daily'(盘后日报) / 'intraday'(盘中快报)
    Returns:
        dict: 生成结果汇总
    """
    # 修正项2：防抖保护
    if not _generate_lock.acquire(timeout=5):
        logger.warning('报告生成任务已在进行中，跳过重复触发')
        return {'success': False, 'message': '报告生成任务已在进行中'}

    try:
        if target_date is None:
            target_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

        logger.info(f'开始生成每日报告 date={target_date}')

        stocks = _get_all_stocks()
        if not stocks:
            return {'success': False, 'message': '没有自选股'}

        results = []
        success_count = 0
        fail_count = 0
        v5_count = 0
        legacy_count = 0
        fallback_count = 0
        reuse_count = 0

        # === 018: 循环前批量预取A股同花顺辅助指标（不阻断东财逐只采集） ===
        a_symbols = [s['symbol'] for s in stocks if s['market'] == 'a_stock']
        if a_symbols:
            try:
                batch_result = fetch_capital_flow_batch(a_symbols)
                logger.info(f'[日报] 资金面批量预取: {batch_result}')
            except Exception as e:
                logger.warning(f'[日报] 资金面批量预取失败(不阻断): {e}')

        # === 012-B: 批次超时 + 进度追踪 ===
        batch_start = time.time()
        total = len(stocks)
        started_at_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')

        _update_progress_file(
            {
                'date': target_date,
                'total': total,
                'current': 0,
                'current_symbol': '',
                'current_name': '',
                'stage': '准备中',
                'status': 'running',
                'started_at': started_at_str,
                'last_update': '',
                'finished_at': None,
            }
        )

        for idx, stock in enumerate(stocks, 1):
            # 整体超时检查（软超时）
            if time.time() - batch_start > BATCH_TIMEOUT_SECONDS:
                remaining = total - idx + 1
                logger.warning(
                    f'[日报进度] 批次整体超时({BATCH_TIMEOUT_SECONDS}s)，剩余{remaining}只跳过'
                )
                fail_count += remaining
                break

            symbol = stock['symbol']
            name = stock.get('name', '')
            stock_id = stock['id']
            logger.info(f'[日报进度] {idx}/{total} 开始 {symbol} {name}')
            _update_progress_file(
                {
                    'date': target_date,
                    'total': total,
                    'current': idx,
                    'current_symbol': symbol,
                    'current_name': name,
                    'stage': '开始处理',
                    'status': 'running',
                    'started_at': started_at_str,
                    'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                    'finished_at': None,
                }
            )

            # 单只超时控制（019J：daemon 线程 + join(timeout)，替代 executor 上下文管理器
            # M-1 红线：executor 上下文管理器退出时 __exit__ 调用 shutdown(wait=True)
            # 会 join 挂死 worker，超时保护形同虚设——本实现超时后立即 continue，不 join 不等待 worker）
            try:
                # box 模式：线程内异常不自动传播，必须显式捕获（否则超时判定会误判）
                box = {'exc': None}

                def _run_single_stock():
                    try:
                        box['r'] = _process_single_stock(stock, target_date, force, report_type)
                    except Exception as e:
                        box['exc'] = e

                t = threading.Thread(target=_run_single_stock, daemon=True)
                t.start()
                t.join(timeout=STOCK_TIMEOUT_SECONDS)
                if t.is_alive():
                    # 超时：写 failed 记录 + results.append + continue，不等待 worker
                    # （worker 迟到完成时 _process_single_stock 内部 _save_report
                    #   DELETE+INSERT 会覆盖 failed 为 ok，数据自愈不丢分）
                    fail_count += 1
                    logger.error(f'[日报进度] {symbol} 超时({STOCK_TIMEOUT_SECONDS}s)，跳过')
                    _save_report(
                        report_date=target_date,
                        stock_id=stock_id,
                        stock_code=symbol,
                        stock_name=name,
                        engine_version=None,
                        total_score=None,
                        rating=None,
                        rating_label=None,
                        prev_score=None,
                        score_change=None,
                        key_factors=None,
                        data_warnings=None,
                        markdown_content=None,
                        status='failed',
                        error_msg=f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        price_advice=None,
                        report_type=report_type,
                    )
                    results.append(
                        {
                            'stock_id': stock_id,
                            'symbol': symbol,
                            'name': name,
                            'status': 'failed',
                            'error': f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        }
                    )
                    continue

                # 线程内异常重抛，走外层 except（fail_count+1 + failed 记录，与现状一致）
                if box.get('exc') is not None:
                    raise box['exc']
                result = box['r']

                # 处理成功结果
                if result.get('reused'):
                    reuse_count += 1
                if result.get('is_fallback'):
                    fallback_count += 1

                success_count += 1
                engine = result.get('engine', 'legacy')
                if engine == 'v5':
                    v5_count += 1
                else:
                    legacy_count += 1

                results.append(
                    {
                        'stock_id': result['stock_id'],
                        'symbol': result['symbol'],
                        'name': result['name'],
                        'status': 'ok',
                        'engine': engine,
                        'score': result.get('score'),
                        'rating': result.get('rating'),
                        'score_change': result.get('score_change'),
                    }
                )

            except Exception as e:
                fail_count += 1
                error_msg = str(e)
                logger.error(f'[{symbol}] 报告生成失败: {error_msg}')

                # 记录失败
                _save_report(
                    report_date=target_date,
                    stock_id=stock_id,
                    stock_code=symbol,
                    stock_name=name,
                    engine_version=None,
                    total_score=None,
                    rating=None,
                    rating_label=None,
                    prev_score=None,
                    score_change=None,
                    key_factors=None,
                    data_warnings=None,
                    markdown_content=None,
                    status='failed',
                    error_msg=error_msg,
                    price_advice=None,
                    report_type=report_type,
                )

                results.append(
                    {
                        'stock_id': stock_id,
                        'symbol': symbol,
                        'name': name,
                        'status': 'failed',
                        'error': error_msg,
                    }
                )

            logger.info(f'[日报进度] {symbol} 完成')

        # 批次完成
        elapsed = int(time.time() - batch_start)
        logger.info(
            f'[日报进度] ===== 批次完成 成功{success_count}/失败{fail_count} 耗时{elapsed}s ====='
        )
        _update_progress_file(
            {
                'date': target_date,
                'total': total,
                'current': total,
                'current_symbol': '',
                'current_name': '',
                'stage': '完成',
                'status': 'done',
                'started_at': started_at_str,
                'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            }
        )
        # === 012-B END ===

        # P3-A：评分差异监控（v5 vs legacy）
        score_diff_flags = _check_score_differences(target_date, results)

        # 生成汇总 Markdown 并保存到文件
        full_md = _build_markdown_summary(target_date, results)

        # 012-C: 失败摘要
        failure_summary = None
        if fail_count > 0:
            by_reason = {}
            for r in results:
                if r.get('status') == 'failed':
                    reason = r.get('error', '未知')
                    by_reason.setdefault(reason, []).append(r.get('symbol', ''))
            failure_summary = {'total_failed': fail_count, 'by_reason': by_reason}

        summary = {
            'success': True,
            'report_date': target_date,
            'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),  # 019D: 批次生成时刻
            'total': len(stocks),
            'success_count': success_count,
            'fail_count': fail_count,
            'v5_count': v5_count,
            'legacy_count': legacy_count,
            'fallback_count': fallback_count,
            'reuse_count': reuse_count,
            'score_diff_flags': score_diff_flags,
            'failure_summary': failure_summary,  # 012-C 新增
            'results': results,
            'markdown': full_md,
        }

        logger.info(
            f'每日报告生成完成 date={target_date}: '
            f'成功{success_count}/失败{fail_count} '
            f'v5={v5_count} legacy={legacy_count} fallback={fallback_count} '
            f'score_diff_flags={len(score_diff_flags)}'
        )

        return summary

    finally:
        _generate_lock.release()


def _check_score_differences(report_date, results):
    """P3-A 强制补充：评分差异监控

    从 daily_reports 表查每只股票最近一次 engine=legacy 的评分，
    与当前 v5 评分比对，差异>15分则标记人工复核。

    数据源：daily_reports 表（ratings_history 无 engine_version 字段）
    """
    flags = []
    DIFF_THRESHOLD = 15.0

    try:
        conn = get_connection()
        cursor = conn.cursor()

        for r in results:
            if r.get('status') != 'ok' or r.get('engine') != 'v5':
                continue

            stock_id = r['stock_id']
            v5_score = r.get('score', 0)

            # 查最近一次 legacy 引擎的评分
            cursor.execute(
                """SELECT total_score, report_date FROM daily_reports
                   WHERE stock_id = ? AND engine_version = 'legacy'
                     AND report_date < ?
                   ORDER BY report_date DESC LIMIT 1""",
                (stock_id, report_date),
            )
            legacy_row = cursor.fetchone()

            if legacy_row and legacy_row['total_score'] is not None:
                legacy_score = legacy_row['total_score']
                diff = abs(v5_score - legacy_score)

                if diff > DIFF_THRESHOLD:
                    flags.append(
                        {
                            'stock_id': stock_id,
                            'symbol': r.get('symbol', ''),
                            'name': r.get('name', ''),
                            'v5_score': v5_score,
                            'legacy_score': legacy_score,
                            'diff': round(diff, 1),
                            'legacy_date': legacy_row['report_date'],
                            'message': f'{r.get("name", "")} v5={v5_score} vs legacy={legacy_score}，差异{round(diff, 1)}分需人工复核',
                        }
                    )
                    logger.warning(
                        f'评分差异告警: stock_id={stock_id} symbol={r.get("symbol", "")} '
                        f'v5={v5_score} legacy={legacy_score} diff={round(diff, 1)}'
                    )

        conn.close()
    except Exception as e:
        logger.error(f'评分差异监控失败: {e}')

    return flags


def _build_markdown_summary(report_date, results):
    """构建汇总 Markdown 报告并保存到文件"""
    ok_results = [r for r in results if r['status'] == 'ok']
    failed_results = [r for r in results if r['status'] == 'failed']

    md = f'# 📊 每日分析报告 — {report_date}\n\n'

    # 概览表
    md += '## 一、概览\n\n'
    md += '| 股票 | 代码 | 引擎 | 总分 | 评级 | 较昨日 |\n'
    md += '|:---|:---|:---:|:---:|:---:|:---:|\n'
    for r in ok_results:
        engine_tag = '🚀' if r.get('engine') == 'v5' else '⚙️'
        change_str = ''
        if r.get('score_change') is not None:
            change = r['score_change']
            arrow = '↑' if change > 0 else ('↓' if change < 0 else '→')
            change_str = f'{arrow} {abs(change):.1f}'
        md += f'| {r["name"]} | {r["symbol"]} | {engine_tag} | {r["score"]:.1f} | {r["rating"]} | {change_str} |\n'

    if failed_results:
        md += f'\n> ⚠️ {len(failed_results)} 只股票生成失败\n'

    # 重点关注
    md += '\n## 二、重点关注\n\n'

    # 评分异动（变动 >5 分）
    big_changes = [
        r for r in ok_results if r.get('score_change') is not None and abs(r['score_change']) >= 5
    ]
    if big_changes:
        md += '### ⚠️ 评分异动\n\n'
        for r in big_changes:
            change = r['score_change']
            direction = '上涨' if change > 0 else '下跌'
            md += f'- **{r["name"]}**（{r["symbol"]}）：{direction} {abs(change):.1f}分 → {r["score"]:.1f}\n'
    else:
        md += '### 评分异动\n\n无显著异动\n'

    md += '\n'

    # 汇总统计
    md += '## 三、引擎统计\n\n'
    v5_count = sum(1 for r in ok_results if r.get('engine') == 'v5')
    legacy_count = sum(1 for r in ok_results if r.get('engine') == 'legacy')
    md += f'- v5引擎：{v5_count} 只\n'
    md += f'- 经典引擎：{legacy_count} 只\n'
    md += f'- 生成失败：{len(failed_results)} 只\n\n'

    # 逐只详情从数据库读取
    md += '## 四、逐只详情\n\n'

    conn = get_connection()
    cursor = conn.cursor()
    for r in results:
        if r['status'] == 'ok':
            cursor.execute(
                'SELECT markdown_content FROM daily_reports WHERE report_date=? AND stock_id=?',
                (report_date, r['stock_id']),
            )
            row = cursor.fetchone()
            if row and row['markdown_content']:
                md += row['markdown_content']
    conn.close()

    # 保存到文件
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    filepath = os.path.join(_REPORTS_DIR, f'{report_date}.md')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md)

    return md


# ================================================================
# 查询接口
# ================================================================


def get_latest_reports():
    """获取最新一期报告列表（013-Hotfix：优先 daily，无 daily 时取 intraday）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(report_date) as latest_date FROM daily_reports
    """)
    row = cursor.fetchone()
    if not row or not row['latest_date']:
        conn.close()
        return {'success': True, 'report_date': None, 'reports': []}

    latest_date = row['latest_date']

    # 013-Hotfix: 优先取 daily，无 daily 时取 intraday，避免同一天混合返回导致列表重复
    cursor.execute(
        """
        SELECT * FROM daily_reports
        WHERE report_date = ? AND report_type = 'daily' AND status = 'ok'
        ORDER BY total_score DESC
    """,
        (latest_date,),
    )
    reports = [dict(r) for r in cursor.fetchall()]

    if not reports:
        cursor.execute(
            """
            SELECT * FROM daily_reports
            WHERE report_date = ? AND report_type = 'intraday' AND status = 'ok'
            ORDER BY total_score DESC
        """,
            (latest_date,),
        )
        reports = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'success': True, 'report_date': latest_date, 'reports': reports}


def get_reports_by_date(report_date):
    """获取指定日期的报告（013-Hotfix：优先 daily，无 daily 时取 intraday）"""
    conn = get_connection()
    cursor = conn.cursor()

    # 013-Hotfix: 优先取 daily，无 daily 时取 intraday，避免同一天混合返回导致列表重复
    cursor.execute(
        """
        SELECT * FROM daily_reports
        WHERE report_date = ? AND report_type = 'daily' AND status = 'ok'
        ORDER BY total_score DESC
    """,
        (report_date,),
    )
    reports = [dict(r) for r in cursor.fetchall()]

    if not reports:
        cursor.execute(
            """
            SELECT * FROM daily_reports
            WHERE report_date = ? AND report_type = 'intraday' AND status = 'ok'
            ORDER BY total_score DESC
        """,
            (report_date,),
        )
        reports = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'success': True, 'report_date': report_date, 'reports': reports}


def get_report_history(page=1, page_size=30):
    """获取报告历史（分页）"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(DISTINCT report_date) as cnt FROM daily_reports')
    total = cursor.fetchone()['cnt']

    offset = (page - 1) * page_size
    cursor.execute(
        """
        SELECT report_date, COUNT(*) as stock_count,
               SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_count,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail_count,
               SUM(CASE WHEN engine_version='v5' THEN 1 ELSE 0 END) as v5_count,
               MAX(generated_at) as generated_at
        FROM daily_reports
        GROUP BY report_date
        ORDER BY report_date DESC
        LIMIT ? OFFSET ?
    """,
        (page_size, offset),
    )
    dates = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        'success': True,
        'total': total,
        'page': page,
        'page_size': page_size,
        'dates': dates,
    }


# ================================================================
# 命令行入口
# ================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    import argparse

    parser = argparse.ArgumentParser(description='US-11 每日报告生成')
    parser.add_argument('--date', type=str, default=None, help='报告日期 YYYY-MM-DD')
    parser.add_argument('--list', action='store_true', help='查看历史报告列表')
    args = parser.parse_args()

    if args.list:
        history = get_report_history(page=1, page_size=10)
        print(json.dumps(history, ensure_ascii=False, indent=2))
    else:
        result = generate_daily_report(args.date)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != 'markdown'}, ensure_ascii=False, indent=2
            )
        )
