"""建表与种子：智能预警域（P3-B）——alert_rules/alert_history + 未读/单股/日期
索引 + 全局默认规则（WHERE NOT EXISTS 幂等插入）。"""


def _create_alert_tables(cursor):
    """创建 alert_rules/alert_history 表与三个查询索引。

    注：三个索引在原文件中位于 stock_valuation_history 之后（表 29 区段尾部），
    现随本域归并；CREATE INDEX IF NOT EXISTS 与位置无关（021BO 拆分编排）。"""
    # ============================================================
    # 24. 预警规则表 —— P3-B: 智能预警规则配置（架构师评审 D2）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,              -- rating_change / score_below / capital_outflow / tech_signal / sell_signal
            stock_id INTEGER,                     -- NULL=全局默认规则
            threshold REAL,                       -- 阈值（评分阈值/连续天数，按 rule_type 解释）
            enabled INTEGER DEFAULT 1,            -- 1=启用, 0=停用
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 25. 预警历史表 —— P3-B: 预警触发记录（架构师评审 D2）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,             -- 关联触发规则
            stock_id INTEGER NOT NULL,            -- 触发股票
            alert_type TEXT NOT NULL,             -- rating_change / score_below / capital_outflow / tech_signal / sell_signal
            trigger_value TEXT,                   -- 触发值详情（JSON格式）
            message TEXT NOT NULL,                -- 人类可读预警消息
            is_read INTEGER DEFAULT 0,            -- 0=未读, 1=已读
            triggered_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            trigger_date DATE NOT NULL,           -- 幂等去重用
            FOREIGN KEY (rule_id) REFERENCES alert_rules(id),
            FOREIGN KEY (stock_id) REFERENCES stocks(id),
            UNIQUE(rule_id, stock_id, trigger_date)  -- 幂等约束：同规则同股票同日不重复
        )
    """)

    # 索引：未读预警列表（高频查询）
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_history_unread
        ON alert_history(is_read, triggered_at DESC)
    """)
    # 索引：单只股票预警历史
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_history_stock
        ON alert_history(stock_id, triggered_at DESC)
    """)
    # 索引：按日期查询
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_alert_history_date
        ON alert_history(trigger_date)
    """)



def _seed_default_alert_rules(cursor):
    """插入全局默认预警规则（幂等）。"""
    # ============================================================
    # P3-B: 插入全局默认规则（幂等）
    # G2A: 评分跌破默认65分；G2B: 连续净流出默认3天；rating_change 无阈值
    # 021BP 项2: tech_signal 买点信号巡检默认开启（无阈值=任意买点信号都提醒；
    #   阈值语义=共振星级门槛 3/4/5），检查器复用 market_screener 纯函数
    #   对已采集K线离线复算，走 scan_once 既有每日收盘后挂载点，幂等写入
    # 021BQ 项C: sell_signal 卖点信号巡检默认开启（与 tech_signal 对称；
    #   走卖侧平行库 SELL_SIGNAL_LIBRARY/bear 共振，在线全市场扫描不受影响；
    #   每日每股至多 1 条（幂等约束），用户可一键停用全局规则）
    # 注意：SQLite 中 NULL!=NULL，UNIQUE 约束无法去重全局规则(stock_id IS NULL)，
    # 因此用 WHERE NOT EXISTS 逐条幂等插入
    # ============================================================
    for rt, th in [
        ('rating_change', None),
        ('score_below', 65.0),
        ('capital_outflow', 3),
        ('tech_signal', None),
        ('sell_signal', None),
    ]:
        cursor.execute(
            """
            INSERT INTO alert_rules (rule_type, stock_id, threshold, enabled)
            SELECT ?, NULL, ?, 1
            WHERE NOT EXISTS (
                SELECT 1 FROM alert_rules WHERE rule_type=? AND stock_id IS NULL
            )
        """,
            (rt, th, rt),
        )
