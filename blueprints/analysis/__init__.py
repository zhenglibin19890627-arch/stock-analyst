"""四维分析/评级/建议/v5 评分演示 API 蓝图 facade(自 app.py 拆分,函数体零改动)。

t7 拆包：原单文件 blueprints/analysis.py（1,185 行）按业务域拆分为本包；
`from blueprints.analysis import bp`（blueprints/__init__.py 聚合注册）与历史全部
模块级名字（9 个路由函数 / 15 个装配与明细助手 / _REPORT_STALE_MINUTES / logger）
零改动——tests/test_score_tier_note_021bs 经 facade 导入 _attach_score_tier_note
无需迁移；ALL_BLUEPRINTS 注册面与全部 URL 规则（9 条，端点名 analysis.api_*）
逐字不变，app.py 零改动。

facade 约定（与 blueprints.portfolio 包同款）：
1. 路由子模块经 `from blueprints.analysis import bp` 挂路由（bp 先定义后导入子模块，
   显式类型注解打破「包 __init__ ↔ 路由子模块」的 mypy 循环类型推断）；
2. 共享展示层工具仍统一复用 blueprints/_utils.py（_resolve_report_type 经 facade
   再导出，包内不复制）；
3. 路由函数体为原文件逐段搬运（行为等价拆分）。

子模块地图：_details 四维明细（技术/基本面/资金面/消息面/行业资金背景展示计算）·
_enrich 响应增强（上一轮快照/风险解析/数据完整度/失配注记 021BS/回测证据 021BU/
共振快照 021BZ/打分子项 021V）· advice 评级/建议/报告读取路由（analyze/advise/
report-latest + 统一增强装配）· v5_demo 评分引擎演示/调试 · cards 只读卡片
（趋势罗盘/操盘手建议/评级位置标注）。
"""

import logging

from flask import Blueprint, jsonify, request

from blueprints._utils import _resolve_report_type
from database.db_manager import get_connection

logger = logging.getLogger(__name__)

# 显式类型注解：打破「包 __init__ ↔ 路由子模块」的 mypy 循环类型推断
#（子模块 @bp.route 装饰器引用 bp，无注解时 mypy 报 Cannot determine type of "bp"）
bp: Blueprint = Blueprint('analysis', __name__)

# ---- 路由子模块（须在 bp 定义之后导入，模块级 @bp.route 才能挂上）----
from blueprints.analysis import (  # noqa: E402
    advice,
    cards,
    v5_demo,
)
from blueprints.analysis._details import (  # noqa: E402
    _capital_detail_for_stock,
    _fundamental_detail_for_stock,
    _industry_flow_bg_for_stock,
    _news_detail_for_stock,
    _technical_detail_for_stock,
)
from blueprints.analysis._enrich import (  # noqa: E402
    _attach_backtest_evidence,
    _attach_resonance_snapshot,
    _attach_score_tier_note,
    _attach_scoring_subitems,
    _enrich_data_warnings,
    _parse_markdown_risks,
    _prev_report_snapshot,
)
from blueprints.analysis.advice import (  # noqa: E402
    _REPORT_STALE_MINUTES,
    _enrich_advice_result,
    _generate_fresh_advice,
    api_advise_stock,
    api_analyze_stock,
    api_get_report_latest,
)
from blueprints.analysis.cards import (  # noqa: E402
    api_stock_position_note,
    api_stock_trader_advice,
    api_stock_trend,
)
from blueprints.analysis.v5_demo import (  # noqa: E402
    api_v5_scoring_analyze,
    api_v5_scoring_demo,
    api_v5_scoring_validation,
)

__all__ = [
    'bp',
    'logger',
    'logging',
    'Blueprint',
    'jsonify',
    'request',
    '_resolve_report_type',
    'get_connection',
    'advice',
    'cards',
    'v5_demo',
    '_capital_detail_for_stock',
    '_fundamental_detail_for_stock',
    '_industry_flow_bg_for_stock',
    '_news_detail_for_stock',
    '_technical_detail_for_stock',
    '_attach_backtest_evidence',
    '_attach_resonance_snapshot',
    '_attach_score_tier_note',
    '_attach_scoring_subitems',
    '_enrich_data_warnings',
    '_parse_markdown_risks',
    '_prev_report_snapshot',
    '_REPORT_STALE_MINUTES',
    '_enrich_advice_result',
    '_generate_fresh_advice',
    'api_advise_stock',
    'api_analyze_stock',
    'api_get_report_latest',
    'api_stock_position_note',
    'api_stock_trader_advice',
    'api_stock_trend',
    'api_v5_scoring_analyze',
    'api_v5_scoring_demo',
    'api_v5_scoring_validation',
]
