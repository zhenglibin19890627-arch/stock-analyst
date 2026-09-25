"""US-11 每日报告模块 (Daily Report)（facade）—— t6 拆包起实现拆分至 modules/daily_report/ 包。

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

本包 `__init__` 为 **facade 全量再导出**（比照 modules/backtest_engine t3 /
modules/data_collector.py OPT-3 拆分先例）：全部既有调用方（app、blueprints/report、
blueprints/analysis、advisor、backfill_scheduler、tests、scripts 运维/自测）零改动可用；
`from modules.daily_report import X` 的模块级符号表面与拆分前单文件（1,786 行）
逐符号一致（前后 vars 快照对比证据见 t6 任务报告）。CLI 保留：`python -m modules.daily_report`。

子模块地图（按域划分；导入顺序 = 原文件分节顺序，_env 恒为首个加载的子模块）：
- _env         环境基座：路径守卫 + 公共 logger（名保持 modules.daily_report）+ 中国时区
               + config 超时常量 / db_manager / advisor / collector 共享导入绑定
- _scheduler   调度器域：三窗资金流采集 + A股收盘批次 + 港股 16:10 批次 + 启动补跑
               + start/stop_scheduler 生命周期 + 资金面延迟补采 + M9 周度优化
- _generator   报告生成域：generate_daily_report 批量编排 + _process_single_stock
               + _generate_lock 防抖锁（R18 超时模式宿主）
- _progress    进度追踪域：logs/report_progress.json 读写（012-B）
- _freshness   数据完整度域：_build_data_freshness + _days_between + _has_collection_today
- _store       保存与不变量域：_save_report（R9 红线宿主：daily 顶替 intraday）
- _summary     汇总与查询域：批次 Markdown + 评分差异监控 + 报告查询 API

拆分三约定（AGENTS.md §6）：①测试 monkeypatch 必须指向实现/消费方子模块
（补丁打在 facade 对包内调用不可见，t6 已迁移 6 个测试文件）；②跨模块共享可变
状态与唯一消费方同模块单宿（_generate_lock→_generator，调度器状态→_scheduler，
进度文件路径→_progress，_REPORTS_DIR→_summary）；③子模块依赖自闭合、不反向导入
facade，包 __init__ 导入序保持原文件初始化副作用时序（路径守卫 + config/db_manager
/advisor/collector 导入顺序原样收拢在 _env）。

模块级可变标量（_scheduler_started/各 Timer 柄/_atexit_registered）在运行期会被
start_scheduler/stop_scheduler 重赋值，其 facade 绑定不取导入时快照，而是经下方
PEP 562 `__getattr__` 动态转发到 _scheduler（读面保真：scripts/verify_us11 等经
facade 读取标志位的调用方拿到的是实时值，语义与拆分前单文件完全一致）。
需求映射：§2.7 每日报告；R9 日报不变量 / R18 超时模式 / R19 超时配置锚点保持。
"""

from modules.daily_report import _scheduler  # noqa: F401  —— 可变标量动态转发的宿主模块
from modules.daily_report._env import (
    _CN_TZ as _CN_TZ,
)
from modules.daily_report._env import (
    BATCH_TIMEOUT_SECONDS as BATCH_TIMEOUT_SECONDS,
)
from modules.daily_report._env import (
    STOCK_TIMEOUT_SECONDS as STOCK_TIMEOUT_SECONDS,
)
from modules.daily_report._env import (
    _build_key_factors as _build_key_factors,
)
from modules.daily_report._env import (
    _build_markdown_single as _build_markdown_single,
)
from modules.daily_report._env import (
    collect_stock_data as collect_stock_data,
)
from modules.daily_report._env import (
    datetime as datetime,
)
from modules.daily_report._env import (
    fetch_capital_flow_batch as fetch_capital_flow_batch,
)
from modules.daily_report._env import (
    generate_advice as generate_advice,
)
from modules.daily_report._env import (
    get_connection as get_connection,
)
from modules.daily_report._env import (
    logger as logger,
)
from modules.daily_report._env import (
    logging as logging,
)
from modules.daily_report._env import (
    os as os,
)
from modules.daily_report._env import (
    sys as sys,
)
from modules.daily_report._env import (
    timedelta as timedelta,
)
from modules.daily_report._env import (
    timezone as timezone,
)
from modules.daily_report._freshness import (
    _build_data_freshness as _build_data_freshness,
)
from modules.daily_report._freshness import (
    _days_between as _days_between,
)
from modules.daily_report._freshness import (
    _has_collection_today as _has_collection_today,
)
from modules.daily_report._generator import (
    _generate_lock as _generate_lock,
)
from modules.daily_report._generator import (
    _get_all_stocks as _get_all_stocks,
)
from modules.daily_report._generator import (
    _get_prev_score as _get_prev_score,
)
from modules.daily_report._generator import (
    _process_single_stock as _process_single_stock,
)
from modules.daily_report._generator import (
    generate_daily_report as generate_daily_report,
)
from modules.daily_report._generator import (
    time as time,
)
from modules.daily_report._progress import (
    _REPORT_PROGRESS_PATH as _REPORT_PROGRESS_PATH,
)
from modules.daily_report._progress import (
    _progress_lock as _progress_lock,
)
from modules.daily_report._progress import (
    _update_progress_file as _update_progress_file,
)
from modules.daily_report._progress import (
    _update_progress_stage as _update_progress_stage,
)
from modules.daily_report._scheduler import (
    _CAPITAL_WINDOW_COUNT as _CAPITAL_WINDOW_COUNT,
)
from modules.daily_report._scheduler import (
    _CAPITAL_WINDOW_TIMES as _CAPITAL_WINDOW_TIMES,
)
from modules.daily_report._scheduler import (
    _CATCHUP_AFTER as _CATCHUP_AFTER,
)
from modules.daily_report._scheduler import (
    _CATCHUP_DELAY_SECONDS as _CATCHUP_DELAY_SECONDS,
)
from modules.daily_report._scheduler import (
    _HK_REPORT_TIME as _HK_REPORT_TIME,
)
from modules.daily_report._scheduler import (
    _capital_retry_once as _capital_retry_once,
)
from modules.daily_report._scheduler import (
    _catchup_tick as _catchup_tick,
)
from modules.daily_report._scheduler import (
    _hk_report_tick as _hk_report_tick,
)
from modules.daily_report._scheduler import (
    _optimizer_tick as _optimizer_tick,
)
from modules.daily_report._scheduler import (
    _register_capital_window as _register_capital_window,
)
from modules.daily_report._scheduler import (
    _register_catchup as _register_catchup,
)
from modules.daily_report._scheduler import (
    _register_hk_report as _register_hk_report,
)
from modules.daily_report._scheduler import (
    _run_capital_window as _run_capital_window,
)
from modules.daily_report._scheduler import (
    _run_full_report_flow as _run_full_report_flow,
)
from modules.daily_report._scheduler import (
    _schedule_capital_retry as _schedule_capital_retry,
)
from modules.daily_report._scheduler import (
    _schedule_next as _schedule_next,
)
from modules.daily_report._scheduler import (
    _schedule_optimizer_next as _schedule_optimizer_next,
)
from modules.daily_report._scheduler import (
    _scheduler_tick as _scheduler_tick,
)
from modules.daily_report._scheduler import (
    _split_em_capital_list as _split_em_capital_list,
)
from modules.daily_report._scheduler import (
    _today_report_coverage as _today_report_coverage,
)
from modules.daily_report._scheduler import (
    atexit as atexit,
)
from modules.daily_report._scheduler import (
    start_scheduler as start_scheduler,
)
from modules.daily_report._scheduler import (
    stop_scheduler as stop_scheduler,
)
from modules.daily_report._scheduler import (
    threading as threading,
)
from modules.daily_report._store import (
    _save_report as _save_report,
)
from modules.daily_report._store import (
    json as json,
)
from modules.daily_report._summary import (
    _REPORTS_DIR as _REPORTS_DIR,
)
from modules.daily_report._summary import (
    _build_markdown_summary as _build_markdown_summary,
)
from modules.daily_report._summary import (
    _check_score_differences as _check_score_differences,
)
from modules.daily_report._summary import (
    get_latest_reports as get_latest_reports,
)
from modules.daily_report._summary import (
    get_report_history as get_report_history,
)

# 运行期重赋值的调度器可变标量：不取静态快照，经 __getattr__ 动态转发 _scheduler
# （与拆分前单文件同名字段恒等值；显式名单外属性照常 AttributeError，模块探测行为不变）
_DYNAMIC_STATE_NAMES = (
    '_scheduler_started',
    '_scheduler_timer',
    '_optimizer_timer',
    '_capital_retry_timer',
    '_hk_report_timer',
    '_catchup_timer',
    '_atexit_registered',
)


def __getattr__(name: str):
    if name in _DYNAMIC_STATE_NAMES:
        return getattr(_scheduler, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
