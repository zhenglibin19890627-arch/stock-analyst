"""持仓/组合/流水/成本修正/价格刷新 API 蓝图 facade（自 app.py 拆分；021BO 再拆包）。

历史上本蓝图是单文件 blueprints/portfolio.py（1,911 行），现按业务域拆分为本包；
`from blueprints.portfolio import bp`（blueprints/__init__.py 聚合注册）与历史全部
模块级名字（bp/_parse_pa_zone/_derive_trader_signal/_fetch_realtime_price_batch/
_recalculate_holding/_check_trade_edit_restriction/账户域助手/config 常量）零改动。

facade 约定（与 database.db_manager facade 同款）：
1. 路由子模块经 `from blueprints.portfolio import bp` 挂路由（bp 先定义后导入子模块）；
2. **monkeypatch 兼容**：config 风控常量由 facade 导入为包属性，路由子模块在
   调用点经 facade 取值——测试 `monkeypatch.setattr(blueprints.portfolio,
   'TRADE_T1_LOCK_ENABLED', False)`（test_trade_fees_021bk）语义保持；
3. 路由函数体为原文件逐段搬运（行为等价拆分）。

子模块地图：_scope 账户域助手 · accounts 账户 CRUD · holdings 持仓/汇总 ·
watchlist_scores 看板评分 · trades 流水+持仓重算 · controls 风控修正 ·
market 预填/价格刷新。
"""

from flask import Blueprint

from config import (
    COST_ADJUSTMENT_COOLDOWN_HOURS,
    COST_ADJUSTMENT_DEVIATION_THRESHOLD,
    PRICE_CACHE_TTL_HOURS,
    TRADE_AMOUNT_VERIFY_THRESHOLD,
    TRADE_T1_LOCK_ENABLED,
)

# 显式类型注解：打破「包 __init__ ↔ 路由子模块」的 mypy 循环类型推断
#（子模块 @bp.route 装饰器引用 bp，无注解时 mypy 报 Cannot determine type of "bp"）
bp: Blueprint = Blueprint('portfolio', __name__)

# ---- 路由子模块（须在 bp 定义之后导入，模块级 @bp.route 才能挂上）----
from blueprints.portfolio import (  # noqa: E402
    accounts,
    controls,
    holdings,
    market,
    trades,
    watchlist_scores,
)
from blueprints.portfolio._scope import (  # noqa: E402
    _account_exists,
    _get_default_account_id,
    _parse_account_scope,
)
from blueprints.portfolio.controls import _check_trade_edit_restriction  # noqa: E402
from blueprints.portfolio.market import _fetch_realtime_price_batch  # noqa: E402
from blueprints.portfolio.trades import _recalculate_holding  # noqa: E402
from blueprints.portfolio.watchlist_scores import (  # noqa: E402
    _derive_trader_signal,
    _parse_pa_zone,
)

__all__ = [
    'bp',
    'COST_ADJUSTMENT_COOLDOWN_HOURS',
    'COST_ADJUSTMENT_DEVIATION_THRESHOLD',
    'PRICE_CACHE_TTL_HOURS',
    'TRADE_AMOUNT_VERIFY_THRESHOLD',
    'TRADE_T1_LOCK_ENABLED',
    'accounts',
    'controls',
    'holdings',
    'market',
    'trades',
    'watchlist_scores',
    '_account_exists',
    '_get_default_account_id',
    '_parse_account_scope',
    '_check_trade_edit_restriction',
    '_fetch_realtime_price_batch',
    '_recalculate_holding',
    '_derive_trader_signal',
    '_parse_pa_zone',
]
