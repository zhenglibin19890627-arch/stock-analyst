"""database._db —— db_manager facade 的实现包（021BO 拆分，2026-09-21）。

原 database/db_manager.py（1,571 行：42 张表建表 + 全部迁移 + 连接/备份）按职责
拆分为本包；调用方仍应 `from database.db_manager import X`（facade 全量再导出）。

包内约定：
1. 可变配置（DB_PATH/BACKUP_DIR/MAX_BACKUPS）与可替换服务（get_connection/
   backup_database/init_database）一律在**调用点**经 facade 动态取值——测试
   `monkeypatch.setattr(db_manager, 'DB_PATH', ...)` 的语义由此保持；
2. 私有实现间的调用直接相对导入，不经 facade；
3. 表结构 SQL 与迁移逻辑为原文件逐段搬运（行为等价拆分）。改库结构前先读
   AGENTS.md「关键风险边界」与 docs/RED_LINES.md——WAL/busy_timeout/foreign_keys
   为红线 R12 检查锚点，破坏性操作前备份为 R11。
"""
