"""
数据库管理 facade（021BO 拆分，2026-09-21）。

历史上本文件承载全部数据库层实现（1,571 行：42 张表建表 + 全部迁移 + 连接/备份），
现拆分为 database/_db/ 实现包，本文件保留为 **facade 全量再导出**——全仓 90+ 处
`from database.db_manager import ...` 调用方零改动。

facade 约定（比照 modules/collector facade，另加一条更强约束）：
1. 调用方只应 `from database.db_manager import X`，不要深入 database._db 内部；
2. **monkeypatch 兼容**：测试在 facade 上打补丁（DB_PATH/BACKUP_DIR 等）必须继续
   生效——实现子模块对可变配置与可替换服务（get_connection/backup_database/
   init_database）一律在调用点经本 facade 动态取值，禁止模块级快照；
3. 表结构 SQL / 迁移为原文件逐段搬运（行为等价拆分）。改库结构前先读
   AGENTS.md「关键风险边界」与 docs/RED_LINES.md——WAL/busy_timeout/foreign_keys
   为红线 R12 检查锚点，破坏性操作前备份为 R11（备份失败必须中止）。
"""

import logging
import os
import sys

# 兼容历史用法：直接运行本文件（python database/db_manager.py）可独立建库
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH  # noqa: E402  —— 依赖上面的 sys.path 注入

# 备份目录与保留份数（测试经 monkeypatch.setattr(db_manager, 'BACKUP_DIR', ...) 改写）
BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), 'backups')
MAX_BACKUPS = 10

_logger = logging.getLogger(__name__)

# ---- 实现包全量再导出（历史模块面的全部名字，含下划线私有名）----
from database._db._backup import _prune_old_backups, auto_backup_db, backup_database  # noqa: E402
from database._db._connection import get_connection  # noqa: E402
from database._db._init import init_database  # noqa: E402
from database._db._migrations import (  # noqa: E402
    _ensure_price_backtest_columns,
    _migrate_columns,
    _migrate_daily_reports_type,
    _migrate_holdings_multi_account,
    _migrate_to_unified_groups,
)

__all__ = [
    'DB_PATH',
    'BACKUP_DIR',
    'MAX_BACKUPS',
    'get_connection',
    'auto_backup_db',
    'backup_database',
    '_prune_old_backups',
    'init_database',
    '_migrate_to_unified_groups',
    '_migrate_columns',
    '_migrate_daily_reports_type',
    '_migrate_holdings_multi_account',
    '_ensure_price_backtest_columns',
]


if __name__ == '__main__':
    init_database()
