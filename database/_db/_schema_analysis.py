"""建表：分析产出与增量数据维度——每日报告/指数/价格回测/盘口/估值/解禁/
业绩预告快报/历史估值 + 021BI 选股快照与新浪行业映射（逐字搬运）。

注：策略参数种子原居「表 21 与表 22 之间」，现移至 _init 编排层（幂等，无行为差异）；
预警表及其索引在 _schema_alerts.py。"""


def _create_analysis_tables(cursor):
    """创建 daily_reports/index_kline/index_ratings/price_backtest_results 表
    与价格回测、日报日期性能索引。"""
    # ============================================================
    # 21. 每日报告表 —— US-11: 基于v5引擎的每日分析报告
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
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
            UNIQUE(report_date, stock_id),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 22. 指数K线表 —— B8: 指数评级模块K线数据
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS index_kline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,       -- 指数代码
            trade_date TEXT NOT NULL,       -- 交易日期 YYYY-MM-DD
            open REAL, high REAL, low REAL, close REAL,
            volume INTEGER,
            UNIQUE(index_code, trade_date)
        )
    """)

    # ============================================================
    # 23. 指数评级表 —— B8: 指数评级结果
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS index_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            market TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            total_score REAL,
            rating TEXT,
            rating_label TEXT,
            kline_score REAL,
            capital_score REAL,
            close_price REAL,
            pct_change REAL,               -- 当日涨跌幅
            detail_json TEXT,              -- 评分详情JSON
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(index_code, trade_date)
        )
    """)

    # ============================================================
    # 24. 价格建议回测结果表 —— 007: 价格建议命中率验证
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            backtest_date DATE NOT NULL,
            rating TEXT NOT NULL,
            market TEXT NOT NULL,
            has_position INTEGER DEFAULT 0,
            buy_range_low REAL,
            buy_range_high REAL,
            target_price REAL,
            stop_loss REAL,
            take_profit REAL,
            position_pct INTEGER,
            close_at_backtest REAL,
            ma20 REAL,
            ma60 REAL,
            boll_upper REAL,
            boll_lower REAL,
            atr REAL,
            t5_hit_buy_range INTEGER,
            t5_hit_target INTEGER,
            t5_hit_stop_loss INTEGER,
            t5_hit_take_profit INTEGER,
            t5_days_to_buy_range INTEGER,
            t5_days_to_target INTEGER,
            t5_days_to_stop_loss INTEGER,
            t5_days_to_take_profit INTEGER,
            t5_max_high REAL,
            t5_min_low REAL,
            t20_hit_buy_range INTEGER,
            t20_hit_target INTEGER,
            t20_hit_stop_loss INTEGER,
            t20_hit_take_profit INTEGER,
            t20_days_to_buy_range INTEGER,
            t20_days_to_target INTEGER,
            t20_days_to_stop_loss INTEGER,
            t20_days_to_take_profit INTEGER,
            t20_max_high REAL,
            t20_min_low REAL,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_backtest_stock_date
        ON price_backtest_results(stock_id, backtest_date)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_backtest_market
        ON price_backtest_results(market)
    """)

    # ============================================================
    # P2 强制修正项：性能索引（索引优化，非表结构变更）
    # 为 daily_reports.report_date 创建索引，避免 MAX(report_date) 子查询全表扫描
    # ============================================================
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_reports_date
        ON daily_reports(report_date)
    """)



def _create_market_extra_tables(cursor):
    """创建 stock_orderbook/stock_valuation/stock_restricted_release/raw_forecast/
    raw_express/stock_valuation_history/market_snapshot/sina_industry_map 表。

    原序位于迁移之后；CREATE TABLE IF NOT EXISTS 彼此独立且不被任何迁移引用，
    前移到建表阶段无行为差异（021BO 拆分编排）。"""
    # ============================================================
    # 26. 五档盘口表 —— 019Y T1：mootdx 实时行情五档买卖盘（增量数据维度）
    # 每只股票每天保留最新一条快照（INSERT OR REPLACE，盘中重复采集覆盖当日）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_orderbook (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,        -- 采集日期
            quote_time TEXT,                 -- 快照时间（北京时间 HH:MM:SS）
            latest_price REAL,               -- 最新价
            pct_change REAL,                 -- 涨跌幅(%)
            bid1_price REAL, bid1_vol REAL,
            bid2_price REAL, bid2_vol REAL,
            bid3_price REAL, bid3_vol REAL,
            bid4_price REAL, bid4_vol REAL,
            bid5_price REAL, bid5_vol REAL,
            ask1_price REAL, ask1_vol REAL,
            ask2_price REAL, ask2_vol REAL,
            ask3_price REAL, ask3_vol REAL,
            ask4_price REAL, ask4_vol REAL,
            ask5_price REAL, ask5_vol REAL,
            source TEXT DEFAULT NULL,        -- 数据来源：'mootdx'（与 raw_capital_flow.capital_source 同风格）
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 27. 估值数据表 —— 019Y T2：PE/PB/PS/PCF 历史估值（日级低频）
    # 主源 akshare（stock_value_em），降级 baostock（query_history_k_data_plus）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_valuation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,        -- 估值对应交易日
            pe_ttm REAL,                     -- 市盈率(TTM)
            pe REAL,                         -- 市盈率(静态)
            pb_mrq REAL,                     -- 市净率
            ps_ttm REAL,                     -- 市销率(TTM)
            ps REAL,                         -- 市销率(静态)
            pcf_ncf_ttm REAL,                -- 市现率(TTM)
            dv_ttm REAL,                     -- 股息率(TTM)
            total_mv REAL,                   -- 总市值
            source TEXT DEFAULT NULL,        -- 数据来源：'akshare' / 'baostock'
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 28. 限售解禁表 —— 019Y T2：个股限售解禁明细（风险因子，事件级）
    # 数据源 akshare stock_restricted_release_queue_em
    # 采集时整表按 stock_id 重建（DELETE + INSERT），为当日快照语义
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_restricted_release (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            release_date DATE NOT NULL,      -- 解禁时间
            release_type TEXT,               -- 解禁类型（如 首发原股东限售股份）
            release_shares REAL,             -- 解禁数量（股）
            actual_shares REAL,              -- 实际解禁数量（股）
            actual_mv REAL,                  -- 实际解禁市值（元）
            release_ratio REAL,              -- 占解禁前总股本比例
            source TEXT DEFAULT NULL,        -- 数据来源：'akshare'
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # 业绩预告（东财 stock_yjyg_em，A股；业绩预告是财报前的先行指标）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_forecast (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            symbol TEXT,                     -- 股票代码（冗余，便于排查）
            report_period TEXT,              -- 报告期（如 20260630）
            indicator TEXT,                  -- 预测指标（净利润/营业收入/扣非净利润）
            change_desc TEXT,                -- 业绩变动描述（原文）
            forecast_value REAL,             -- 预测数值（元，区间中值）
            change_pct REAL,                 -- 业绩变动幅度（%）
            change_reason TEXT,              -- 业绩变动原因
            forecast_type TEXT,              -- 预告类型（预增/略增/扭亏/预减/略减/续盈等）
            last_year_value REAL,            -- 上年同期值（元）
            announce_date TEXT,              -- 公告日期
            data_source TEXT DEFAULT 'akshare_em',
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, report_period, indicator),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # 业绩快报（东财 stock_yjkb_em，A股；020R-50：财报前点值预估，可信度高于预告）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_express (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            symbol TEXT,                     -- 股票代码（冗余，便于排查）
            report_period TEXT,              -- 报告期（如 20260630）
            eps REAL,                        -- 每股收益（元）
            revenue REAL,                    -- 营业收入（元）
            revenue_yoy REAL,                -- 营业收入同比增长（%）
            np REAL,                         -- 净利润（元）
            np_yoy REAL,                     -- 净利润同比增长（%）
            roe REAL,                        -- 净资产收益率（%）
            announce_date TEXT,              -- 公告日期
            data_source TEXT DEFAULT 'akshare_em',
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, report_period),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 29. 历史估值表 —— 021W-2：百度股市通历史估值（A股）
    # 数据源 akshare stock_zh_valuation_baidu（约每两周一个快照点，覆盖 2000 年至今）
    # 用途：数据详情"基本面数据历史"表格，按财报期关联展示当时的真实 PE/PB
    # （区别于 stock_valuation 的实时估值，本表存历史时点真实估值）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_valuation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,        -- 估值快照交易日
            pe_ttm REAL,                     -- 市盈率(TTM)
            pe REAL,                         -- 市盈率(静态)
            pb REAL,                         -- 市净率
            ps_ttm REAL,                     -- 市销率(TTM)
            source TEXT DEFAULT 'baidu',     -- 数据来源：'baidu'
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 021BI: 全市场选股扫描 —— 快照表（每次扫描整表替换，仅存最新一轮）
    # 注意：本表不关联 stocks 外键（扫描结果大多尚未入自选）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshot (
            symbol TEXT PRIMARY KEY,          -- 腾讯/新浪通用代码：sh600519
            code TEXT NOT NULL,               -- 纯数字代码：600519
            name TEXT NOT NULL,
            board TEXT,                       -- 板块：主板/创业板/科创板
            price REAL,                       -- 现价
            change_pct REAL,                  -- 当日涨跌幅(%)
            turnover REAL,                    -- 换手率(%)
            amount REAL,                      -- 成交额(万元，新浪原始口径)
            mkt_cap REAL,                     -- 总市值(亿元，已换算)
            nmc_cap REAL,                     -- 流通市值(亿元，已换算)
            pe REAL,                          -- 市盈率
            pb REAL,                          -- 市净率
            volume_ratio REAL,                -- 量比（腾讯增强，可空）
            industry TEXT,                    -- 行业（新浪行业映射，可空）
            snapshot_at TEXT NOT NULL         -- 快照时间(本地)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_snapshot_mktcap
        ON market_snapshot(mkt_cap DESC)
    """)

    # ============================================================
    # 021BI: 新浪行业映射缓存（7 天过期，重建成本低：~56 次请求）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sina_industry_map (
            symbol TEXT PRIMARY KEY,          -- sh600519
            industry TEXT NOT NULL,           -- 行业名（如 玻璃行业）
            node TEXT NOT NULL,               -- 新浪行业节点代码（如 new_blhy）
            updated_at TEXT NOT NULL
        )
    """)
