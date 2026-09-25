"""调度器域（t6 拆包）：三窗资金流采集 + 收盘批次 + 港股批次 + 启动补跑 + M9 周度优化。

调度面完整迁移：_scheduler_tick 三窗链（019X T2 / 021Y 前移 15:30-15:54）、
_run_full_report_flow A股主批次（021F force / 021Z 仅A股）、_hk_report_tick 港股
16:10 批次（021Z/021AW/021AX 挂载项）、_catchup_tick 启动补跑（021AY）、
start_scheduler/stop_scheduler 生命周期（强制修正项1）、资金面延迟补采（019Q）、
M9 每周优化调度（周日 20:00）。所有钟点/延迟常量与 Timer 形态逐字保持。

消费方 monkeypatch 一律指向本模块（tests/test_scheduler_force 等 t6 已迁移）；
模块级可变状态（_scheduler_started/各 Timer 柄/_atexit_registered）单宿本模块，
facade 经 PEP 562 __getattr__ 动态转发，运行期重赋值读面保真。
"""

import atexit
import os
import threading
from datetime import datetime, timedelta

from modules.daily_report._env import _CN_TZ, fetch_capital_flow_batch, get_connection, logger
from modules.daily_report._generator import _generate_lock, _get_all_stocks, generate_daily_report

# ================================================================
# 强制修正项1：调度器生命周期管理
# ================================================================

_scheduler_started = False
_scheduler_timer = None
_optimizer_timer = None  # M9: 每周优化定时器
_capital_retry_timer = None  # 019Q: 资金面延迟补采一次性 Timer（30分钟）
_atexit_registered = False  # 标记 atexit 钩子是否已注册（供测试验证）

# 019X T2：资金流采集三窗调度（错峰拆分，降低单窗口请求密度；021L 起主源为腾讯 westock）
# 窗1(15:30)/窗2(15:42)/窗3(15:54)，每窗采集资金面补采清单的 1/3（按代码排序固定切分；
# 021L 起 westock 行计为"已完成"，清单仅含真实数据缺失股，日常窗内近乎空转）；
# 窗1/窗2 只采集不生成报告；窗3 采集完成后执行一次完整日报流程。
# 021Y：三窗整体前移（16:10/16:40/17:10 → 15:30/15:42/15:54）——
# 021L 后主源为腾讯系批量接口、单窗实测仅需数秒，旧 30 分钟间距是东财限频时代的历史包袱；
# 15:30 起步为资金面定稿保留 30 分钟收盘缓冲（A股 15:00 收盘），
# 报告约 15:57 完成（原 ~17:12），提前约 75 分钟。
# 提前代价：个别股票资金面若未定稿，当日分数按当时数据计算，由次日批次纠正。
_CAPITAL_WINDOW_COUNT = 3
_CAPITAL_WINDOW_TIMES = ((15, 30), (15, 42), (15, 54))  # 窗1/窗2/窗3 固定钟点

# 021Z：港股报告独立批次时间——港股 16:00 收盘，晚于 A股主批次(15:54)，
# 主批次时港股尚未收盘、数据非定稿。港股报告延至此时刻单独重算
# （force=True，交易日），保证港股分数基于收盘数据。
_HK_REPORT_TIME = (16, 10)
_hk_report_timer = None

# 021AY：启动补跑——电脑睡眠会整体错过 15:54/16:10 两个批次钟点
# （计划任务睡眠期间不运行，唤醒后 Watchdog 拉起服务只排明天任务；
# 实测 2026-08-26 15:39 入睡 → 20:46 唤醒，当日 31 只仅 8 只有报告）。
# 服务启动 _CATCHUP_DELAY 秒后自查：交易日 && 已过港股批次+缓冲 &&
# 当日报告不齐 → 补跑一次完整批次（force=True，A股+港股一起）。
_CATCHUP_AFTER = (16, 15)      # 补跑门槛：港股批次 16:10 + 5 分钟缓冲
_CATCHUP_DELAY_SECONDS = 90    # 启动后延迟（等网络/采集器就绪）
_catchup_timer = None


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
        # 窗3 结束后注册次日窗1（15:30）
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
    # 021F（方案A）：收盘批次在交易日默认强制重算（force=True）——
    # 当日早盘/盘中已生成报告时，B11 复用会把早盘分数一路沿用至收盘后
    # （2026-08-17 实测：29 只全天停留在 09:56 早盘分数）。
    # 收盘数据更新后必须重算；非交易日保持默认复用，避免周末脏写与无效重算。
    # 021Z：主批次仅生成 A股——港股 16:00 收盘晚于本批次，由 _hk_report_tick 独立批次生成。
    _trading_day = datetime.now(_CN_TZ).weekday() < 5
    logger.info(
        '定时调度器触发每日报告生成%s',
        '（交易日：force=True 强制重算）' if _trading_day else '（非交易日：默认复用）',
    )
    generate_daily_report(force=_trading_day, market_filter='a_stock')

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

    # 020R-53：行业资金流向每日快照挂载点——与指数刷新同模式，每个报告日
    # 落库一次当日行业资金快照，为市场行情页提供时间维度（历史回看 + 5日累计）。
    # 东财接口失败时由 market_overview 冷却机制兜底，异常隔离不阻塞调度。
    try:
        from modules.market_overview import refresh_industry_fund_flow

        refresh_industry_fund_flow()
    except Exception as e:
        logger.error(f'行业资金流向每日快照异常（不阻塞调度）: {e}', exc_info=True)


def _register_hk_report(next_day=False):
    """021Z：注册港股报告批次（一次性 daemon Timer，固定钟点 _HK_REPORT_TIME）

    与资金流采集窗同型；next_day=True 时注册次日（批次完成后链式续注册，
    或启动时今日钟点已过）。
    """
    global _hk_report_timer
    hk_h, hk_m = _HK_REPORT_TIME
    now = datetime.now(_CN_TZ)
    target = now.replace(hour=hk_h, minute=hk_m, second=0, microsecond=0)
    if next_day:
        target += timedelta(days=1)
    delay = (target - now).total_seconds()
    if delay <= 0:
        delay = 1  # 已过固定钟点，尽快补触发
    _hk_report_timer = threading.Timer(delay, _hk_report_tick)
    _hk_report_timer.daemon = True
    _hk_report_timer.start()
    logger.info(f'下次港股报告批次: {target.strftime("%Y-%m-%d %H:%M")} ({delay:.0f}秒后)')


def _hk_report_tick():
    """021Z：港股收盘批次回调——港股 16:00 收盘后单独重算全部港股当日报告。

    - 交易日 force=True 强制重算（与 021F 收盘批次口径一致）；非交易日默认复用；
    - 仅生成港股（market_filter='hk_stock'），预警扫描/指数刷新等挂载项仍只在
      A股主批次执行一次，不在此重复；
    - 完成后链式注册次日批次（finally 保证异常也不断链）。
    """
    try:
        _trading_day = datetime.now(_CN_TZ).weekday() < 5
        logger.info(
            '[港股批次] 触发港股报告生成%s',
            '（交易日：force=True 强制重算）' if _trading_day else '（非交易日：默认复用）',
        )
        try:
            result = generate_daily_report(force=_trading_day, market_filter='hk_stock')
            logger.info(f'[港股批次] 港股报告生成完成: {result}')
        except Exception as e:
            logger.error(f'[港股批次] 港股报告生成异常（不阻塞调度）: {e}', exc_info=True)

        # 021AW：港股批次完成后追加一次指数刷新——15:54 主批次的指数刷新
        # 发生在 15:56，东财指数接口当天 bar 有时尚未发布（实测 8/25 返回
        # 最新只到 8/24，且此后无任何调度补采指数，缺口永久滞留）。
        # 16:10 时 A股(15:00收盘)与港股(16:00收盘)当天 bar 均已发布，
        # INSERT OR REPLACE 幂等覆盖；残余滞后由 backfill_scheduler 指数
        # 自愈检查兜底。异常隔离，不阻塞次日批次注册。
        try:
            from modules.index_collector import refresh_all

            refresh_all()
            logger.info('[港股批次] 指数二次刷新完成（021AW）')
        except Exception as e:
            logger.error(f'[港股批次] 指数二次刷新异常（不阻塞调度）: {e}', exc_info=True)

        # 021AX：行业资金流二次刷新——每日唯一落库时机在 15:56 日报批次后，
        # 8/25 该时点东财 5 域名全挂且失败后无重试路径 → 8/25 快照整天缺失
        # （市场行情页无 8/25 数据）。此处 16:10 二次尝试，并尊重 10 分钟
        # 冷却（冷却中说明刚失败过，硬闯会加重东财熔断）；残余缺口由
        # backfill_scheduler 行业资金流自愈兜底。
        try:
            from modules.market_overview import refresh_in_cooldown, refresh_industry_fund_flow

            remain = refresh_in_cooldown()
            if remain:
                logger.info('[港股批次] 行业资金流冷却中（剩 %d 秒），跳过二次刷新', remain)
            else:
                refresh_industry_fund_flow()
                logger.info('[港股批次] 行业资金流二次刷新完成（021AX）')
        except Exception as e:
            logger.error(f'[港股批次] 行业资金流二次刷新异常（不阻塞调度）: {e}', exc_info=True)
    finally:
        _register_hk_report(next_day=True)


def _schedule_next():
    """019X T2：窗3 结束后注册次日窗1（021Y 起 15:30）"""
    global _scheduler_timer
    now = datetime.now(_CN_TZ)
    _h, _m = _CAPITAL_WINDOW_TIMES[0]
    tomorrow = now.replace(hour=_h, minute=_m, second=0, microsecond=0) + timedelta(days=1)
    delay = (tomorrow - now).total_seconds()
    _scheduler_timer = threading.Timer(delay, _scheduler_tick, args=(0,))
    _scheduler_timer.daemon = True
    _scheduler_timer.start()
    logger.info(f'下次定时报告: {tomorrow.strftime("%Y-%m-%d %H:%M")} ({delay:.0f}秒后)')


def _today_report_coverage():
    """021AY：当日日报覆盖 → (已有股票数, 总股票数)。"""
    conn = get_connection()
    try:
        today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        n_total = conn.execute('SELECT COUNT(*) AS n FROM stocks').fetchone()['n']
        n_done = conn.execute(
            'SELECT COUNT(DISTINCT stock_id) AS n FROM daily_reports WHERE report_date = ?',
            (today,),
        ).fetchone()['n']
        return n_done, n_total
    finally:
        conn.close()


def _catchup_tick(now=None):
    """021AY：启动补跑回调——错过当日全部批次钟点且报告不齐时补跑一次。

    触发条件（全部满足才跑）：
    - 交易日（weekday<5，与 _hk_report_tick 同约定）；
    - 已过 _CATCHUP_AFTER（16:15：A股 15:54 / 港股 16:10 批次均已过点）；
    - 当日报告覆盖不齐（有股票还没有今日报告）。
    正常运行日批次已完成 → 覆盖齐 → 跳过；睡眠错过日 → 覆盖缺 → 补跑。
    补跑 = 完整批次（force=True，A股+港股）+ 指数刷新 + 行业资金流快照，
    与 _hk_report_tick 的挂载项一致，保证市场行情数据同日补齐。
    一次性：不链式续注册（次日由常规批次负责）。
    """
    if now is None:
        now = datetime.now(_CN_TZ)
    try:
        if now.weekday() >= 5:
            return False
        if (now.hour, now.minute) < _CATCHUP_AFTER:
            return False
        n_done, n_total = _today_report_coverage()
        if not n_total or n_done >= n_total:
            logger.info(
                '[启动补跑] 当日报告已齐（%d/%d），无需补跑', n_done, n_total
            )
            return False

        logger.warning(
            '[启动补跑] 当日批次钟点已过但报告不齐（%d/%d），补跑完整批次',
            n_done, n_total,
        )
        try:
            result = generate_daily_report(force=True)
            logger.info(f'[启动补跑] 批次完成: {result}')
        except Exception as e:
            logger.error(f'[启动补跑] 批次生成异常: {e}', exc_info=True)
            return False

        # 批次挂载项（与 _hk_report_tick 同型）：指数 + 行业资金流
        try:
            from modules.index_collector import refresh_all

            refresh_all()
        except Exception as e:
            logger.error(f'[启动补跑] 指数刷新异常（不阻塞）: {e}', exc_info=True)
        try:
            from modules.market_overview import (
                refresh_in_cooldown,
                refresh_industry_fund_flow,
            )

            if not refresh_in_cooldown():
                refresh_industry_fund_flow()
        except Exception as e:
            logger.error(f'[启动补跑] 行业资金流刷新异常（不阻塞）: {e}', exc_info=True)
        return True
    except Exception as e:
        logger.error(f'[启动补跑] 检查异常（放弃本轮）: {e}', exc_info=True)
        return False


def _register_catchup():
    """021AY：注册一次性启动补跑 Timer（start_scheduler 尾部调用）。"""
    global _catchup_timer
    _catchup_timer = threading.Timer(_CATCHUP_DELAY_SECONDS, _catchup_tick)
    _catchup_timer.daemon = True
    _catchup_timer.start()
    logger.info(
        f'[启动补跑] 已注册（{_CATCHUP_DELAY_SECONDS}秒后自查，'
        f'交易日 {_CATCHUP_AFTER[0]:02d}:{_CATCHUP_AFTER[1]:02d} 后且报告不齐时补跑）'
    )


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
    # 021Y：以窗3钟点为"今日三窗已结束"的判断线（原硬编码 17:10）
    _lh, _lm = _CAPITAL_WINDOW_TIMES[-1]
    last_window_time = now.replace(hour=_lh, minute=_lm, second=0, microsecond=0)
    if now >= last_window_time:
        # 019Z: 今天三窗已全部结束（窗3之后启动），排到明天
        _schedule_next()
    else:
        # 019Z: 今天还有窗未到，注册今天的窗1（已过钟点由1秒补触发兜底）
        _register_capital_window(0)

    # 021Z：港股报告批次（16:10）——今天未到点注册今天，已过则排明天
    hk_target = now.replace(
        hour=_HK_REPORT_TIME[0], minute=_HK_REPORT_TIME[1], second=0, microsecond=0
    )
    _register_hk_report(next_day=(now >= hk_target))

    # 021AY：启动补跑（覆盖睡眠错过批次钟点的场景）
    _register_catchup()

    _schedule_optimizer_next()  # M9: 启动每周优化定时器
    logger.info(
        '✅ 每日报告定时调度器已启动'
        f'（每日{_CAPITAL_WINDOW_TIMES[0][0]:02d}:{_CAPITAL_WINDOW_TIMES[0][1]:02d}起三窗采集、'
        f'{_CAPITAL_WINDOW_TIMES[-1][0]:02d}:{_CAPITAL_WINDOW_TIMES[-1][1]:02d}后生成A股报告，'
        f'{_HK_REPORT_TIME[0]:02d}:{_HK_REPORT_TIME[1]:02d}生成港股报告，每周日20:00自动优化）'
    )


def stop_scheduler():
    """停止定时调度器（进程退出时调用）"""
    global _scheduler_timer, _optimizer_timer, _scheduler_started, _capital_retry_timer
    global _hk_report_timer
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
    if _hk_report_timer is not None:
        # 021Z：取消未触发的港股报告批次 Timer（同防御性收尾）
        _hk_report_timer.cancel()
        _hk_report_timer = None
    global _catchup_timer
    if _catchup_timer is not None:
        # 021AY：取消未触发的启动补跑 Timer（同防御性收尾）
        _catchup_timer.cancel()
        _catchup_timer = None
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
    fetch_capital_flow_batch(a_symbols)——复用补采清单入口：主源真实数据
    （capital_source IS NULL 或 ='westock'，且非估算）才算"已完成"（021L 起 westock
    为主源）；sina_main / ths_total 行仍进入补采清单 —— 主源链恢复时可覆盖升级。
    021L：主力净流入链路为腾讯 westock（主源）→ 东财三层（兜底）→ 新浪 lscjfb
    主力口径(sina_main) → 估算兜底（仅展示不参评）。异常隔离仅记日志。
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

    缺口判定（M-6，必须带 is_estimated 条件；021L 扩展 westock 同计为真实）：
    len(a_symbols) - COUNT(当日 raw_capital_flow WHERE stock_id IN a_symbols
      AND (capital_source IS NULL OR capital_source='westock')
      AND (is_estimated=0 OR is_estimated IS NULL)) > 0
    估算兜底行 capital_source=NULL（DB 实证）——若缺口 SQL 只判 capital_source IS NULL
    会把估算行误计为"EM 成功"→ 延迟补采永不触发；必须附加 is_estimated 条件（M-6）。
    021L：westock 行（capital_source='westock'，主源真实数据）计为无缺口，
    不再为覆盖 westock 而注册延迟补采。
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
            f"AND (rc.capital_source IS NULL OR rc.capital_source = 'westock') "
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
        f'（一次性，不再重试；与次日15:30批次无冲突）'
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
