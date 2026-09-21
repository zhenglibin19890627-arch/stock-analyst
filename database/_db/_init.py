"""init_database 编排层（021BO 拆分）：建表 → 迁移 → 种子 → 收尾列迁移。

顺序与拆分前的 init_database 等价：
- 建表彼此独立（CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS），
  原先穿插在迁移之后的表前移到建表阶段，不被任何迁移引用，无行为差异；
- 迁移保持原相对顺序（列迁移 → 日报重建 → 分组统一 → 多账户 → B12 → B14）；
- 种子（策略参数 / 预警默认规则）为幂等 INSERT，原居迁移之前/散落中段，统一
  后置（其目标表在迁移前后均存在，无行为差异）。
"""

from database._db._migrations import (
    _backfill_local_industry,
    _migrate_columns,
    _migrate_daily_reports_type,
    _migrate_holdings_multi_account,
    _migrate_late_columns,
    _migrate_ratings_unique,
    _migrate_to_unified_groups,
)
from database._db._schema_alerts import _create_alert_tables, _seed_default_alert_rules
from database._db._schema_analysis import _create_analysis_tables, _create_market_extra_tables
from database._db._schema_core import _create_core_tables
from database._db._schema_portfolio import _create_portfolio_tables


def init_database():
    """初始化数据库 —— 创建所有需要的表。

    如果表已存在则跳过，不会覆盖已有数据。
    """
    # 021BO：get_connection 在调用点经 facade 取值（测试 monkeypatch 语义保持）
    from database.db_manager import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    # ---- ① 建表（核心/持仓/分析产出/增量维度/预警）----
    _create_core_tables(cursor)
    _create_portfolio_tables(cursor)
    _create_analysis_tables(cursor)
    _create_market_extra_tables(cursor)
    _create_alert_tables(cursor)

    # ---- ② 迁移（保持原相对顺序）----
    _migrate_columns(cursor)
    _migrate_daily_reports_type(cursor)
    _migrate_to_unified_groups(cursor)
    _migrate_holdings_multi_account(cursor)
    _migrate_ratings_unique(cursor)
    _backfill_local_industry(cursor)

    # ---- ③ 种子数据（幂等）----
    # 021AN：分组完全自定义——不再自动创建任何默认分组
    # （原'核心持仓/观察池/短线关注'每次启动 INSERT OR IGNORE 会在用户
    #  删除后复活；新装环境零预置组，存量默认组由脚本归一为普通组）
    _seed_strategy_params(cursor)
    _seed_default_alert_rules(cursor)

    # ---- ④ 收尾列迁移 ----
    _migrate_late_columns(cursor)

    conn.commit()
    conn.close()
    print('[数据库] 所有表创建完成，初始策略参数已就绪（021AN：分组完全自定义，无预置默认组）。')


def _seed_strategy_params(cursor):
    """插入默认策略参数（如果还不存在；原 init_database 内联段逐字搬运）。"""
    # ============================================================
    # 插入默认策略参数（如果还不存在）
    # ============================================================
    import json

    from config import RATING_THRESHOLDS, WEIGHTS_A_STOCK, WEIGHTS_HK_STOCK

    for market, weights in [('a_stock', WEIGHTS_A_STOCK), ('hk_stock', WEIGHTS_HK_STOCK)]:
        cursor.execute(
            """
            INSERT OR IGNORE INTO strategy_params (market, param_type, param_key, param_value)
            VALUES (?, 'weights', 'current', ?)
        """,
            (market, json.dumps(weights)),
        )

    cursor.execute(
        """
        INSERT OR IGNORE INTO strategy_params (market, param_type, param_key, param_value)
        VALUES ('a_stock', 'thresholds', 'current', ?)
    """,
        (json.dumps({k: v[:2] for k, v in RATING_THRESHOLDS.items()}),),
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO strategy_params (market, param_type, param_key, param_value)
        VALUES ('hk_stock', 'thresholds', 'current', ?)
    """,
        (json.dumps({k: v[:2] for k, v in RATING_THRESHOLDS.items()}),),
    )
