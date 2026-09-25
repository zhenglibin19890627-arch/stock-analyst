"""M8 评级有效性监测（回测）引擎（facade）—— t3 拆包起实现拆分至 modules/backtest_engine/ 包。

三层架构之中层——回测业务层。功能：
1. 固定周期回测（T+1 / T+5 / T+20）
2. 动态周期回测（评级变更链）
3. 有效性判定矩阵（中文5档 + 历史兼容）
4. 市场级回测报告（A股/港股独立）
5. 个股回测明细
6. 权重实验场景（D4 消息面 0→20%）

本包 `__init__` 为 **facade 全量再导出**（比照 modules/data_collector.py facade 拆分
先例）：全部既有调用方（blueprints/backtest、blueprints/analysis、blueprints/portfolio/
watchlist_scores、advisor、export_engine、dynamic_optimizer、tests、scripts 审计/回填）
零改动可用；`from modules.backtest_engine import X` 的模块级符号表面与拆分前单文件
（1,887 行）逐符号一致（前后 vars 快照对比证据见 t3 任务报告）。

子模块地图（按域划分，导入顺序 = 原文件分节顺序的字母序化；各子模块自持依赖、
互不反向导入 facade，故初始化语义与顺序无关，_env 经首个导入自动最先就位）：
- _env               环境基座：路径守卫 + 公共 logger（名保持 modules.backtest_engine）+ 时区
- evidence           回测证据展示域（021BU）：分级门槛常量 + 诚实门 + 证据表聚合
- engine             核心回测引擎：BacktestEngine 整类（固定周期/alpha/动态/批量/报告/解读）
- judgement          有效性判定域：判定矩阵/市场差异化常量 + _judge/_sigma_daily（纯函数零 IO）
- position           位置注记域：_current_pos_pctile + position_note_for（报告页位置分化标注）
- schema             表结构迁移域：_ensure_columns 幂等加列 + pos_pctile/dd20 列回填
- sentiment_note     情绪检验现状常量模板域（021BW）：SENTIMENT_EVIDENCE_NOTE 唯一编辑点
- simulate           模拟回测回填域（M9-PREFILL）：技术面得分→评级映射 + K线复算
- weight_experiment  权重实验域（D4 预留）：WeightExperimentRunner（仅模拟不改生产权重）

拆分三约定（AGENTS.md §6）：①测试 monkeypatch 必须指向实现/消费方子模块
（补丁打在 facade 对包内调用不可见）；②共享可变状态与其唯一 global 写入者同模块
（本包无模块级可变状态，grep 零 global 写入）；③子模块导入顺序不得改变初始化语义
（各子模块依赖自闭合，包 __init__ 导入序仅决定符号编排，不改任何副作用时序——
原文件的唯一初始化副作用：路径守卫 + db_manager/scoring_engine 导入 + logger 创建，
全部收拢在 _env 且恒为首个加载的子模块）。
需求映射：§2.8 评级有效性监测(回测)模块；方案文档：docs/m8_backtest_framework_plan_20260720.md
"""

from modules.backtest_engine._env import (
    _CN_TZ as _CN_TZ,
)
from modules.backtest_engine._env import (
    datetime as datetime,
)
from modules.backtest_engine._env import (
    get_connection as get_connection,
)
from modules.backtest_engine._env import (
    logger as logger,
)
from modules.backtest_engine._env import (
    logging as logging,
)
from modules.backtest_engine._env import (
    normalize_rating as normalize_rating,
)
from modules.backtest_engine._env import (
    os as os,
)
from modules.backtest_engine._env import (
    sys as sys,
)
from modules.backtest_engine._env import (
    timedelta as timedelta,
)
from modules.backtest_engine._env import (
    timezone as timezone,
)
from modules.backtest_engine.engine import (
    BacktestEngine as BacktestEngine,
)
from modules.backtest_engine.evidence import (
    EVIDENCE_N_FULL as EVIDENCE_N_FULL,
)
from modules.backtest_engine.evidence import (
    EVIDENCE_N_PARTIAL as EVIDENCE_N_PARTIAL,
)
from modules.backtest_engine.evidence import (
    empty_rating_evidence as empty_rating_evidence,
)
from modules.backtest_engine.evidence import (
    format_evidence_cell as format_evidence_cell,
)
from modules.backtest_engine.evidence import (
    grade_sample_class as grade_sample_class,
)
from modules.backtest_engine.evidence import (
    price_advice_evidence_summary as price_advice_evidence_summary,
)
from modules.backtest_engine.evidence import (
    rating_evidence_for as rating_evidence_for,
)
from modules.backtest_engine.evidence import (
    rating_evidence_table as rating_evidence_table,
)
from modules.backtest_engine.judgement import (
    HK_JUDGE_OVERRIDES as HK_JUDGE_OVERRIDES,
)
from modules.backtest_engine.judgement import (
    HK_VOL_SCALE_MAX as HK_VOL_SCALE_MAX,
)
from modules.backtest_engine.judgement import (
    HK_VOL_SCALE_MIN as HK_VOL_SCALE_MIN,
)
from modules.backtest_engine.judgement import (
    JUDGEMENT_MATRIX as JUDGEMENT_MATRIX,
)
from modules.backtest_engine.judgement import (
    NEUTRAL_BORDERLINE_TOL as NEUTRAL_BORDERLINE_TOL,
)
from modules.backtest_engine.judgement import (
    _judge as _judge,
)
from modules.backtest_engine.judgement import (
    _sigma_daily as _sigma_daily,
)
from modules.backtest_engine.position import (
    _current_pos_pctile as _current_pos_pctile,
)
from modules.backtest_engine.position import (
    position_note_for as position_note_for,
)
from modules.backtest_engine.schema import (
    _backfill_pos_dd as _backfill_pos_dd,
)
from modules.backtest_engine.schema import (
    _calc_pos_and_dd20 as _calc_pos_and_dd20,
)
from modules.backtest_engine.schema import (
    _ensure_columns as _ensure_columns,
)
from modules.backtest_engine.sentiment_note import (
    SENTIMENT_EVIDENCE_NOTE as SENTIMENT_EVIDENCE_NOTE,
)
from modules.backtest_engine.sentiment_note import (
    sentiment_evidence_note_for as sentiment_evidence_note_for,
)
from modules.backtest_engine.simulate import (
    _SIM_RATING_THRESHOLDS as _SIM_RATING_THRESHOLDS,
)
from modules.backtest_engine.simulate import (
    _calc_technical_score_from_kline as _calc_technical_score_from_kline,
)
from modules.backtest_engine.simulate import (
    _score_to_rating as _score_to_rating,
)
from modules.backtest_engine.weight_experiment import (
    WeightExperimentRunner as WeightExperimentRunner,
)
