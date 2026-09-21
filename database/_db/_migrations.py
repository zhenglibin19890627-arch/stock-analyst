"""迁移层：列迁移 / 表重建 / 数据回填（原 db_manager.py 迁移函数逐字搬运）。

调用序由 _init.init_database 编排（保持原相对顺序）；涉及 DROP TABLE / DELETE
的迁移在操作前必须经 facade 的 backup_database 备份且失败即中止（红线 R11）。
"""

import sqlite3


def _migrate_to_unified_groups(cursor):
    """将旧 stock_groups 和 portfolio_groups 数据迁移到统一 groups 表。
    迁移步骤：
    1. 检查是否已迁移（groups 表是否有数据且旧表有备份标记）
    2. 从 stock_groups 迁移 → type='watchlist'
    3. 从 portfolio_groups 迁移 → type='portfolio'
    4. 构建 ID 映射，更新 stocks.group_id 和 holdings.group_id
    5. 旧表重命名为 _backup_* 作为备份
    """
    import sqlite3 as _sqlite3

    # 检查旧表是否存在且有数据
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stock_groups'")
    has_stock_groups = cursor.fetchone() is not None
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_groups'")
    has_portfolio_groups = cursor.fetchone() is not None

    # 检查是否已迁移：以旧表数据量为准（不再依赖 _backup_* 标记表的存在性，
    # 迁移残留的 _backup_* 备份表可安全清理而不会触发重复迁移）。
    # 旧表均为空 = 从未使用旧结构，或已完成迁移（CREATE TABLE 每次启动会重建空表）。
    cursor.execute('SELECT COUNT(*) FROM stock_groups')
    stock_groups_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM portfolio_groups')
    portfolio_groups_count = cursor.fetchone()[0]

    if stock_groups_count == 0 and portfolio_groups_count == 0:
        return  # 旧表无数据，无需迁移

    print('[迁移] 开始迁移分组数据到统一 groups 表...')

    # ---- 1. 迁移 stock_groups → groups(type='watchlist') ----
    id_map_watchlist = {}  # old_id -> new_id
    if has_stock_groups:
        cursor.execute('SELECT * FROM stock_groups')
        for row in cursor.fetchall():
            old_id = row['id']
            name = row['name']
            is_default = row['is_default'] if 'is_default' in row.keys() else 0
            try:
                cursor.execute(
                    'INSERT OR IGNORE INTO groups (name, type, is_default) VALUES (?, ?, ?)',
                    (name, 'watchlist', is_default),
                )
            except _sqlite3.IntegrityError:
                pass  # 同名已存在
            # 获取新 ID
            cursor.execute('SELECT id FROM groups WHERE name=? AND type=?', (name, 'watchlist'))
            new_row = cursor.fetchone()
            if new_row:
                id_map_watchlist[old_id] = new_row['id']

    # ---- 2. 迁移 portfolio_groups → groups(type='portfolio') ----
    id_map_portfolio = {}  # old_id -> new_id
    if has_portfolio_groups:
        cursor.execute('SELECT * FROM portfolio_groups')
        for row in cursor.fetchall():
            old_id = row['id']
            name = row['name']
            display_order = row['display_order'] if 'display_order' in row.keys() else 0
            try:
                cursor.execute(
                    'INSERT OR IGNORE INTO groups (name, type, display_order) VALUES (?, ?, ?)',
                    (name, 'portfolio', display_order),
                )
            except _sqlite3.IntegrityError:
                pass
            cursor.execute('SELECT id FROM groups WHERE name=? AND type=?', (name, 'portfolio'))
            new_row = cursor.fetchone()
            if new_row:
                id_map_portfolio[old_id] = new_row['id']

    # ---- 3. 更新 stocks.group_id ----
    if id_map_watchlist:
        for old_id, new_id in id_map_watchlist.items():
            cursor.execute('UPDATE stocks SET group_id=? WHERE group_id=?', (new_id, old_id))
        print(f'[迁移] stocks.group_id 映射完成 ({len(id_map_watchlist)} 个分组)')

    # ---- 4. 更新 holdings.group_id ----
    if id_map_portfolio:
        for old_id, new_id in id_map_portfolio.items():
            cursor.execute('UPDATE holdings SET group_id=? WHERE group_id=?', (new_id, old_id))
        print(f'[迁移] holdings.group_id 映射完成 ({len(id_map_portfolio)} 个分组)')

    # ---- 5. 备份旧表（仅迁移有数据的旧表；目标备份名已存在时跳过，避免 ALTER 冲突）----
    if has_stock_groups and stock_groups_count > 0:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_backup_stock_groups'"
        )
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE stock_groups RENAME TO _backup_stock_groups')
            print('[迁移] stock_groups 已备份为 _backup_stock_groups')
    if has_portfolio_groups and portfolio_groups_count > 0:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_backup_portfolio_groups'"
        )
        if cursor.fetchone() is None:
            cursor.execute('ALTER TABLE portfolio_groups RENAME TO _backup_portfolio_groups')
            print('[迁移] portfolio_groups 已备份为 _backup_portfolio_groups')

    print('[迁移] 分组迁移完成。')


def _migrate_columns(cursor):
    """安全地给已有表添加新列（SQLite 不支持 IF NOT EXISTS，用 try-except）"""
    migrations = [
        ('analysis_results', 'rating_time', 'TIMESTAMP'),
        ('analysis_results', 'operation_suggestion', 'TEXT'),
        # 持仓表新增字段：已实现盈亏 + 状态（active/cleared）
        ('holdings', 'realized_pnl', 'REAL DEFAULT 0'),
        ('holdings', 'status', "TEXT DEFAULT 'active'"),
        # 持仓表新增字段：最新价格 + 价格获取时间 + 成本是否已人工修正
        ('holdings', 'latest_price', 'REAL'),
        ('holdings', 'price_updated_at', 'TIMESTAMP'),
        ('holdings', 'is_cost_adjusted', 'INTEGER DEFAULT 0'),
        # 自选股表新增字段：计划买入数量 + 目标成本价（加入持仓时预填）
        ('stocks', 'planned_quantity', 'INTEGER'),
        ('stocks', 'target_cost', 'REAL'),
        # M9-PREFILL：回测结果表新增模拟标记列
        ('backtest_results', 'is_simulated', 'INTEGER DEFAULT 0'),
        # INDUSTRY-DYNAMIC：自选股表新增行业分类列
        ('stocks', 'industry', "TEXT DEFAULT ''"),
        # 005: 日报表新增价格建议列
        ('daily_reports', 'price_advice', 'TEXT'),
        # 018: 资金面表新增同花顺辅助指标列
        ('raw_capital_flow', 'ths_net_inflow', 'REAL'),
        # 019E: 资金面表新增估算标记列（0=真实数据, 1=估算兜底仅展示）
        ('raw_capital_flow', 'is_estimated', 'INTEGER NOT NULL DEFAULT 0'),
        # 019K: 资金面表新增数据来源标记列（NULL=东方财富真实；'sina_main'=新浪 lscjfb 主力口径顶替，
        # 'ths_total'=同花顺全部资金口径顶替——019S 起不再产生新顶替行，仅存量行使用，待存量清零后评审简化）
        ('raw_capital_flow', 'capital_source', 'TEXT DEFAULT NULL'),
        # 019P: 基本面表新增数据来源标记列（'sina_abstract'/'sina_analysis_indicator'/'em_hk'；NULL=存量旧数据）
        ('raw_fundamental', 'data_source', 'TEXT DEFAULT NULL'),
        # B10: 基本面表新增股东增减持标记列（data_adapter 读取，原由 data_collector._save_holder_increase
        # 运行时动态 ALTER 添加；迁移列表缺失会导致全新库初始化后 data_adapter 读取崩溃，故补登记）
        ('raw_fundamental', 'holder_increase', 'BOOLEAN'),
        # 019Y: K线表新增数据来源标记列（'mootdx'=K线降级备用源；NULL=腾讯主源，与资本面 capital_source 同风格）
        ('raw_kline', 'data_source', 'TEXT DEFAULT NULL'),
        # 020O: 资金面表新增全资金净流入列（腾讯 hkfund TotalNetFlow，主力+散户主动净额；
        # 仅港股有值——A股 asfund 散户为被动镜像、全口径恒等0，无此数据）
        ('raw_capital_flow', 'total_net_inflow', 'REAL'),
        # 021I: 股东人数/机构持仓表新增数据来源标记列（'em'=A股东财口径；'westock'=港股腾讯 shareholder；NULL=存量）
        ('holder_structure', 'source', 'TEXT DEFAULT NULL'),
        # 021Q: 港股资金面/股东数据补强——raw_capital_flow 新增南下两列
        # （腾讯 hkfund _lgtHoldInfo：当日净增持市值万港元 + 持股占比%，
        #   仅港股通标的有值，A股恒 NULL）
        ('raw_capital_flow', 'south_net_buy', 'REAL'),
        ('raw_capital_flow', 'south_hold_ratio', 'REAL'),
        # 021Q: holder_structure 新增机构股东数量两列（腾讯 shareholder 机构持仓统计
        # instCount 季度环比；注意方向语义与 A股股东户数相反：机构减少=利空）
        ('holder_structure', 'inst_count', 'INTEGER'),
        ('holder_structure', 'inst_count_change_pct', 'REAL'),
        # 020R-51: 评级历史表新增引擎版本列（回测报告按引擎分层统计；历史行保持 NULL）
        ('ratings_history', 'engine_version', 'TEXT'),
        # 021S: 交易流水表新增账户归属列（多账户支持；旧库由迁移函数从持仓回填）
        ('trade_records', 'account_id', 'INTEGER'),
        # 021X: 交易流水表新增手续费列（买入计入成本、卖出/分红扣减已实现盈亏）
        ('trade_records', 'commission', 'REAL DEFAULT 0'),
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass  # 列已存在，跳过

    # 创建性能索引（IF NOT EXISTS 安全）
    # trade_records 联合索引：支持已实现盈亏按股票+时间查询
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_records_stock_date
        ON trade_records(stock_id, trade_date, created_at)
    """)
    # 021S: 流水按账户查询索引
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_records_account
        ON trade_records(account_id)
    """)
    # price_cache 索引：按 stock_id 快速查找
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_cache_stock
        ON price_cache(stock_id)
    """)

    # 010-3: price_backtest_results 幂等追加5列（参考 backtest_engine._ensure_columns 模式）
    _ensure_price_backtest_columns(cursor)


def _migrate_ratings_unique(cursor):
    """B12: ratings_history 去重迁移（幂等；原 init_database 内联段逐字搬运）。

    CREATE TABLE IF NOT EXISTS 不会修改已有表结构，因此对已有数据库必须用
    CREATE UNIQUE INDEX 来补加约束。迁移前先清理重复数据。
    """
    # 021BO：backup_database 在调用点经 facade 取值（红线 R11：备份失败必中止）
    from database.db_manager import backup_database

    # ============================================================
    # B12-T1: ratings_history 去重迁移（幂等）
    # CREATE TABLE IF NOT EXISTS 不会修改已有表结构，
    # 因此对已有数据库必须用 CREATE UNIQUE INDEX 来补加约束。
    # 迁移前先清理重复数据：每组 (stock_id, rating_date) 仅保留 id 最大的记录。
    # ============================================================
    try:
        cursor.execute('PRAGMA index_list(ratings_history)')
        indexes = [row[1] for row in cursor.fetchall()]  # row[1] = index name
        if 'idx_ratings_unique' not in indexes:
            # 破坏性操作前自动备份（仅在首次迁移清理重复数据时触发一次）；
            # 备份失败必须中止（红线 R11，021B 起）——异常由外层 except 捕获并告警
            if backup_database('delete_ratings_history_duplicates') is None:
                raise RuntimeError(
                    '[B12迁移] 破坏性操作前备份失败，中止重复数据清理以保护数据（红线 R11）'
                )
            # 先清理重复数据：每组 (stock_id, rating_date) 保留 id 最大的
            cursor.execute("""
                DELETE FROM ratings_history
                WHERE id NOT IN (
                    SELECT MAX(id) FROM ratings_history
                    GROUP BY stock_id, rating_date
                )
            """)
            deleted = cursor.rowcount
            # 创建唯一索引
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_unique
                ON ratings_history(stock_id, rating_date)
            """)
            if deleted > 0:
                print(f'[B12迁移] ratings_history 清理 {deleted} 条重复记录')
    except Exception as e:
        print(f'[B12迁移] ratings_history 迁移警告: {e}')


def _backfill_local_industry(cursor):
    """B14: 行业本地映射补全（幂等；原 init_database 内联段逐字搬运）。"""
    # ============================================================
    # B14: 行业本地映射补全（幂等）
    # akshare 东方财富接口被封时，已有记录 industry 全部为“未分类”，
    # 启动时用本地映射补全。只更新 NULL/未分类/空值，不覆盖已有正确值。
    # ============================================================
    try:
        from modules.data_collector import _LOCAL_INDUSTRY_MAP

        b14_updated = 0
        for symbol, industry in _LOCAL_INDUSTRY_MAP.items():
            cursor.execute(
                """
                UPDATE stocks SET industry = ?
                WHERE symbol = ? AND (industry IS NULL OR industry = '未分类' OR industry = '')
            """,
                (industry, symbol),
            )
            b14_updated += cursor.rowcount
        if b14_updated > 0:
            print(f'[B14迁移] 行业映射补全完成，更新 {b14_updated} 条记录')
    except Exception as e:
        print(f'[B14迁移] 行业补全警告: {e}')


def _migrate_daily_reports_type(cursor):
    """013: daily_reports 新增 report_type 列 + 变更唯一约束（幂等）

    将 UNIQUE(report_date, stock_id) 改为 UNIQUE(report_date, stock_id, report_type)，
    采用 SQLite 标准表重建模式。report_type 列存在则跳过。
    """
    # 021BO：backup_database 在调用点经 facade 取值（红线 R11：备份失败必中止）
    from database.db_manager import backup_database

    # 检查是否已迁移（report_type 列存在则跳过）
    cursor.execute('PRAGMA table_info(daily_reports)')
    cols = [row[1] for row in cursor.fetchall()]
    if 'report_type' in cols:
        return

    print('[013迁移] daily_reports 新增 report_type 列，重建唯一约束...')
    # 表重建
    cursor.execute("""CREATE TABLE daily_reports_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_date DATE NOT NULL,
        stock_id INTEGER NOT NULL,
        stock_code TEXT,
        stock_name TEXT,
        engine_version TEXT,
        total_score REAL,
        rating TEXT,
        rating_label TEXT,
        prev_score REAL,
        score_change REAL,
        key_factors TEXT,
        data_warnings TEXT,
        status TEXT DEFAULT 'ok',
        error_msg TEXT,
        markdown_content TEXT,
        generated_at TEXT,
        price_advice TEXT,
        report_type TEXT DEFAULT 'daily',
        UNIQUE(report_date, stock_id, report_type),
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )""")
    cursor.execute("""INSERT INTO daily_reports_new
        (id, report_date, stock_id, stock_code, stock_name, engine_version,
         total_score, rating, rating_label, prev_score, score_change,
         key_factors, data_warnings, status, error_msg, markdown_content,
         generated_at, price_advice, report_type)
        SELECT id, report_date, stock_id, stock_code, stock_name, engine_version,
               total_score, rating, rating_label, prev_score, score_change,
               key_factors, data_warnings, status, error_msg, markdown_content,
               generated_at, price_advice, 'daily'
        FROM daily_reports""")
    # 破坏性操作（DROP TABLE）前自动备份；备份失败必须中止（红线 R11，021B 起）
    if backup_database('drop_daily_reports_rebuild') is None:
        raise RuntimeError(
            '[013迁移] 破坏性操作前备份失败，中止表重建以保护数据（红线 R11）'
        )
    cursor.execute('DROP TABLE daily_reports')
    cursor.execute('ALTER TABLE daily_reports_new RENAME TO daily_reports')
    # 重建索引
    cursor.execute("""CREATE INDEX IF NOT EXISTS idx_daily_reports_date
        ON daily_reports(report_date)""")
    print('[013迁移] daily_reports 表重建完成')


def _migrate_holdings_multi_account(cursor):
    """021S 多账户支持迁移（幂等）。

    步骤：
    1. 确保 accounts 表存在默认账户（is_default=1，作为存量数据归属锚点，禁止删除）
    2. 旧库 holdings 无 account_id 列时重建表：
       UNIQUE(stock_id) → UNIQUE(account_id, stock_id)，存量持仓全部归入默认账户。
       SQLite 无法 ALTER 唯一约束，采用标准表重建模式（同 _migrate_daily_reports_type）。
    3. trade_records.account_id 回填：优先取关联持仓的账户；
       持仓已删除的断链流水归入默认账户（保留流水不丢失）。

    调用时机：init_database 中 _migrate_columns 与分组迁移之后，
    保证可选列（realized_pnl 等）与 group_id 映射均已就位。
    """
    # 021BO：backup_database 在调用点经 facade 取值（红线 R11：备份失败必中止）
    from database.db_manager import backup_database

    # ---- 1. 默认账户（幂等）----
    cursor.execute('SELECT id FROM accounts WHERE is_default = 1 ORDER BY id LIMIT 1')
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            """
            INSERT INTO accounts (name, broker, notes, is_default)
            SELECT '默认账户', '', '系统自动创建的首个账户（存量持仓/流水归属）', 1
            WHERE NOT EXISTS (SELECT 1 FROM accounts WHERE name = '默认账户')
        """
        )
        cursor.execute("SELECT id FROM accounts WHERE name = '默认账户'")
        row = cursor.fetchone()
        if not row:
            # 极端情况：已存在同名非默认账户 → 取最早账户兜底
            cursor.execute('SELECT id FROM accounts ORDER BY id LIMIT 1')
            row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE accounts SET is_default = 1 WHERE id = ?', (row['id'],))
    if not row:
        raise RuntimeError('[多账户迁移] 无法确定默认账户，中止迁移')

    default_account_id = row['id']
    # 收敛异常的多默认标记（历史误操作防御）：仅保留最早一个
    cursor.execute(
        'UPDATE accounts SET is_default = 0 WHERE is_default = 1 AND id != ?',
        (default_account_id,),
    )

    # ---- 2. holdings 表检查 / 重建 ----
    cursor.execute('PRAGMA table_info(holdings)')
    cols = [r[1] for r in cursor.fetchall()]
    if 'account_id' not in cols:
        print('[021S迁移] holdings 新增 account_id，重建唯一约束 UNIQUE(account_id, stock_id)...')
        # 破坏性操作（DROP TABLE）前自动备份；备份失败必须中止（红线 R11）
        if backup_database('holdings_multi_account_rebuild') is None:
            raise RuntimeError(
                '[021S迁移] 破坏性操作前备份失败，中止表重建以保护数据（红线 R11）'
            )
        cursor.execute("""
            CREATE TABLE holdings_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL DEFAULT 1,
                stock_id INTEGER NOT NULL,
                group_id INTEGER,
                cost_price REAL DEFAULT 0,
                quantity INTEGER DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                latest_price REAL,
                price_updated_at TIMESTAMP,
                is_cost_adjusted INTEGER DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (stock_id) REFERENCES stocks(id),
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                UNIQUE(account_id, stock_id)
            )
        """)
        cursor.execute(
            """
            INSERT INTO holdings_new
                (id, account_id, stock_id, group_id, cost_price, quantity,
                 realized_pnl, status, latest_price, price_updated_at,
                 is_cost_adjusted, notes, created_at, updated_at)
            SELECT id, ?, stock_id, group_id, cost_price, quantity,
                   COALESCE(realized_pnl, 0), COALESCE(status, 'active'),
                   latest_price, price_updated_at,
                   COALESCE(is_cost_adjusted, 0), notes, created_at, updated_at
            FROM holdings
        """,
            (default_account_id,),
        )
        migrated_count = cursor.execute('SELECT COUNT(*) FROM holdings_new').fetchone()[0]
        cursor.execute('DROP TABLE holdings')
        cursor.execute('ALTER TABLE holdings_new RENAME TO holdings')
        print(f'[021S迁移] {migrated_count} 条存量持仓已归入默认账户(id={default_account_id})')
    else:
        # 已是新结构：兜底回填 NULL（理论上不应存在）
        cursor.execute(
            'UPDATE holdings SET account_id = ? WHERE account_id IS NULL',
            (default_account_id,),
        )

    # ---- 3. trade_records.account_id 回填 ----
    cursor.execute('PRAGMA table_info(trade_records)')
    tcols = [r[1] for r in cursor.fetchall()]
    if 'account_id' in tcols:
        cursor.execute(
            """
            UPDATE trade_records SET account_id = COALESCE(
                (SELECT h.account_id FROM holdings h WHERE h.id = trade_records.holding_id),
                ?
            )
            WHERE account_id IS NULL
        """,
            (default_account_id,),
        )


def _ensure_price_backtest_columns(cursor=None):
    """确保 price_backtest_results 表有010新增的5列（ALTER TABLE ADD COLUMN，幂等）。

    参考 backtest_engine.py L83-103 的 _ensure_columns 模式。
    可在 init_database 中传 cursor 统一执行，也可独立调用（自建连接）。
    """
    # 021BO：get_connection 在调用点经 facade 取值
    from database.db_manager import get_connection

    own_conn = False
    if cursor is None:
        conn = get_connection()
        cursor = conn.cursor()
        own_conn = True

    needed = {
        'rating_confidence': 'TEXT',  # 锚点可信度：confirmed/mismatched/unknown
        'anchor_rating_date': 'DATE',  # 匹配到的历史评级日期
        'anchor_rating': 'TEXT',  # 匹配到的历史评级值
        'bias_risk': 'TEXT',  # 偏差风险：high/medium/low
        'days_since_rating': 'INTEGER',  # 回测日距最近评级日的天数
        't5_hit_add': 'INTEGER',  # 补仓区间命中（有持仓网格补仓位，T+5）
        't20_hit_add': 'INTEGER',  # 补仓区间命中（T+20）
        't5_hit_hold': 'INTEGER',  # 持有区间命中（未触发止盈且未触发止损，T+5）
        't20_hit_hold': 'INTEGER',  # 持有区间命中（T+20）
    }
    cursor.execute('PRAGMA table_info(price_backtest_results)')
    existing = {row['name'] for row in cursor.fetchall()}
    for col, col_type in needed.items():
        if col not in existing:
            try:
                cursor.execute(f'ALTER TABLE price_backtest_results ADD COLUMN {col} {col_type}')
            except Exception:
                pass  # 列已存在

    if own_conn:
        conn.commit()
        conn.close()


def _migrate_late_columns(cursor):
    """收尾列迁移（原 init_database 末尾内联段逐字搬运）：error_logs 增强 +
    trade_records 佣金估算标记（幂等：先 SELECT 探测再 ALTER）。"""
    # 012-C: error_logs 表增强（增加 dimension + traceback 字段）
    try:
        cursor.execute('SELECT dimension FROM error_logs LIMIT 1')
    except Exception:
        cursor.execute('ALTER TABLE error_logs ADD COLUMN dimension TEXT')
    try:
        cursor.execute('SELECT traceback FROM error_logs LIMIT 1')
    except Exception:
        cursor.execute('ALTER TABLE error_logs ADD COLUMN traceback TEXT')

    # 021BK: trade_records 佣金估算标记（1=系统自动估算，用户改实际值后清 0）
    try:
        cursor.execute('SELECT commission_estimated FROM trade_records LIMIT 1')
    except Exception:
        cursor.execute('ALTER TABLE trade_records ADD COLUMN commission_estimated INTEGER DEFAULT 0')
