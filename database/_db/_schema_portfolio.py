"""建表：持仓域——多交易账户（021S）/持仓/流水/成本修正/价格缓存
（原 init_database「表 15.5-20」区段逐字搬运）。

注意（AGENTS.md §3 多交易账户约定）：holdings 唯一键为 UNIQUE(account_id, stock_id)，
任何 JOIN holdings ON stock_id 的新代码必须处理同股多仓。"""


def _create_portfolio_tables(cursor):
    """创建 accounts/portfolio_groups/holdings/trade_records/
    position_cost_adjustments/price_cache 表。"""
    # ============================================================
    # 15.5 交易账户表 —— 多账户支持（021S）
    # 持仓域隔离维度：holdings/trade_records 经 account_id 归属账户；
    # 自选股/分析/预警保持全局共享，不受账户影响。
    # is_default=1 的账户为系统锚点（存量数据归属），禁止删除。
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,           -- 账户名称（如：华泰主账户）
            broker TEXT DEFAULT '',              -- 券商（可选）
            notes TEXT,                          -- 备注
            is_default INTEGER DEFAULT 0,        -- 1=默认账户（禁止删除）
            display_order INTEGER DEFAULT 0,     -- 排序权重
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # 16. 持仓分组表 —— 持仓管理专用分组（与自选股分组独立）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,           -- 分组名称
            display_order INTEGER DEFAULT 0,    -- 排序权重
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # 17. 持仓表 —— 完整持仓记录（替代原 positions 表的扩展版）
    # 021S 多账户：UNIQUE(stock_id) → UNIQUE(account_id, stock_id)，
    # 同一股票可在不同账户各有一条持仓，成本独立计算。
    # 旧库由 _migrate_holdings_multi_account 幂等重建（见文件尾部迁移区）。
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL DEFAULT 1, -- 所属交易账户（021S）
            stock_id INTEGER NOT NULL,
            group_id INTEGER,                    -- 所属持仓分组
            cost_price REAL DEFAULT 0,           -- 成本价
            quantity INTEGER DEFAULT 0,          -- 持仓数量
            realized_pnl REAL DEFAULT 0,         -- 已实现盈亏
            status TEXT DEFAULT 'active',        -- active / cleared
            latest_price REAL,                   -- 最新价格快照
            price_updated_at TIMESTAMP,          -- 价格获取时间
            is_cost_adjusted INTEGER DEFAULT 0,  -- 成本是否已人工修正
            notes TEXT,                          -- 备注
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id),
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(account_id, stock_id)         -- 同一账户同一股票仅一条持仓
        )
    """)

    # ============================================================
    # 18. 交易流水表 —— 仅作记录备查，不参与成本自动计算
    # 021S 多账户：account_id 标记流水归属账户（与 holding_id 冗余，
    # 持仓删除后流水仍保留账户归属；旧库由迁移回填）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER,                  -- 关联持仓（删除持仓时置NULL）
            account_id INTEGER,                  -- 所属交易账户（021S，冗余便于查询）
            stock_id INTEGER NOT NULL,           -- 股票（冗余字段，便于查询）
            trade_type TEXT NOT NULL,            -- buy / sell / dividend
            price REAL,                          -- 成交价
            quantity INTEGER,                    -- 成交数量
            amount REAL,                         -- 成交金额（不含手续费）
            commission REAL DEFAULT 0,           -- 手续费/佣金（021X：买入计入成本，卖出扣减盈亏）
            trade_date DATE,                     -- 成交日期
            notes TEXT,                          -- 备注
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE SET NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 19. 持仓成本修正记录表 —— 每次人工修正留痕，支持审计追溯
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS position_cost_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER NOT NULL,           -- 关联持仓ID
            stock_id INTEGER NOT NULL,             -- 冗余：股票ID
            old_cost REAL NOT NULL,                -- 修正前成本价
            new_cost REAL NOT NULL,                -- 修正后成本价
            reason TEXT NOT NULL,                  -- 修正原因
            operator TEXT DEFAULT 'system',        -- 修正人（预留）
            operator_ip TEXT,                      -- 操作IP
            device_fingerprint TEXT,               -- 设备指纹
            deviation_pct REAL,                    -- 偏离百分比
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (holding_id) REFERENCES holdings(id),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 20. 行情缓存表 —— 最新价格缓存，避免每次列表请求都实时拉取
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL UNIQUE,      -- 股票ID（唯一）
            latest_price REAL,                     -- 最新价格
            pct_change REAL,                       -- 涨跌幅(%)
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)
