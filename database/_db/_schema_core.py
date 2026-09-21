"""建表：核心与原始数据域——自选股/分组/持仓锚点 + raw_* 采集数据 + 分析结果
（原 init_database「表 1-15」区段逐字搬运；CREATE TABLE IF NOT EXISTS 幂等）。"""


def _create_core_tables(cursor):
    """创建 stocks/groups/positions、raw_kline/fundamental/capital/sentiment、
    analysis_results/ratings_history/backtest_results/strategy_params/data_status、
    news_sentiment/error_logs 等表（段内顺序与原文件一致）。"""
    # ============================================================
    # 1. 自选股表 —— 你添加的每一只股票
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,           -- 股票代码（如 000001、00700）
            market TEXT NOT NULL,           -- 市场：A股(a_stock) 或 港股(hk_stock)
            name TEXT,                      -- 股票名称（如 平安银行、腾讯控股）
            group_id INTEGER,               -- 所属分组
            status TEXT DEFAULT 'active',   -- 状态：active(正常) / suspended(停牌) / delisted(退市)
            is_new_stock INTEGER DEFAULT 0, -- 是否新股（1=上市不足250天）
            added_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),  -- 添加时间（本地时区）
            UNIQUE(symbol, market)          -- 同一市场内代码唯一
        )
    """)

    # ============================================================
    # 2. 统一分组表 —— watchlist / portfolio / global 三种类型
    #    取代原 stock_groups 和 portfolio_groups 两张表
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,               -- 分组名称
            type TEXT NOT NULL DEFAULT 'watchlist',  -- watchlist / portfolio / global
            display_order INTEGER DEFAULT 0, -- 排序权重
            is_default INTEGER DEFAULT 0,    -- 是否系统默认分组
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(name, type)               -- 同类型下名称唯一
        )
    """)

    # 兼容旧表：如果存在则保留但不再使用（迁移后由 _migrate_groups 负责）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_default INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # 3. 持仓信息表 —— 你手动录入的成本和数量
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            cost_price REAL,                 -- 持仓成本价
            quantity INTEGER DEFAULT 0,      -- 持仓数量（股）
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id),
            UNIQUE(stock_id)
        )
    """)

    # ============================================================
    # 4. K线数据表 —— 每天的价格和成交量
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_kline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,        -- 交易日期
            open REAL,                       -- 开盘价
            close REAL,                      -- 收盘价
            high REAL,                       -- 最高价
            low REAL,                        -- 最低价
            volume REAL,                     -- 成交量
            amount REAL,                     -- 成交额
            turnover REAL,                   -- 换手率
            pct_change REAL,                 -- 涨跌幅(%)
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 5. 基本面数据表 —— 财务指标
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_fundamental (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            report_date DATE,                -- 财报日期
            roe REAL,                        -- 净资产收益率(%)
            roa REAL,                        -- 总资产收益率(%)
            pe_ratio REAL,                   -- 市盈率
            pb_ratio REAL,                   -- 市净率
            ps_ratio REAL,                   -- 市销率
            peg_ratio REAL,                  -- 市盈率相对盈利增长比率
            gross_margin REAL,               -- 毛利率(%)
            net_margin REAL,                 -- 净利率(%)
            debt_ratio REAL,                 -- 资产负债率(%)
            current_ratio REAL,              -- 流动比率
            quick_ratio REAL,                -- 速动比率
            revenue_growth REAL,             -- 营收增长率(%)
            profit_growth REAL,              -- 净利润增长率(%)
            non_recurring_profit_growth REAL,-- 扣非净利润增长率(%)
            ocf_to_net_profit REAL,          -- 经营现金流/净利润
            free_cash_flow REAL,             -- 自由现金流
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, report_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 6. 资金面数据表 —— 资金流向
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_capital_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,
            main_net_inflow REAL,            -- 主力净流入(万元)
            main_net_inflow_pct REAL,        -- 主力净流入占比(%)
            super_large_net REAL,            -- 超大单净流入
            large_net REAL,                  -- 大单净流入
            medium_net REAL,                 -- 中单净流入
            small_net REAL,                  -- 小单净流入
            north_holding_change REAL,       -- 北向资金/港股通持股变化
            margin_balance REAL,             -- 融资融券余额(万元)
            ths_net_inflow REAL,             -- 同花顺全资金净流入(万元)，辅助指标（018新增）
            south_net_buy REAL,              -- 港股通(南下)当日净增持市值(万港元，021Q；腾讯 hkfund LgtCapChgDaily)
            south_hold_ratio REAL,           -- 港股通(南下)持股占比(%，021Q；腾讯 hkfund LgtHoldRatio)
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 6.5 行业资金流向表 —— 市场行情页（东财行业资金流排行快照）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS industry_fund_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date DATE NOT NULL,            -- 交易日（东财更新时间戳换算）
            code TEXT NOT NULL,                  -- 行业代码（BKxxxx）
            name TEXT NOT NULL,                  -- 行业名称
            pct_change REAL,                     -- 涨跌幅(%)
            main_net REAL,                       -- 主力净流入(元)
            main_pct REAL,                       -- 主力净流入占比(%)
            super_net REAL,                      -- 超大单净流入(元)
            big_net REAL,                        -- 大单净流入(元)
            mid_net REAL,                        -- 中单净流入(元)
            small_net REAL,                      -- 小单净流入(元)
            lead_stock TEXT,                     -- 主力净流入最大股
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # 6.6 股东人数与机构持仓表 —— 020R-45（资金面-筹码结构）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holder_structure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            stat_date DATE NOT NULL,             -- 股东户数统计截止日
            holder_count INTEGER,                -- 股东户数(户)
            holder_count_change_pct REAL,        -- 股东户数增减比例(%)
            total_shares REAL,                   -- 总股本(股)
            inst_shares REAL,                    -- 机构持股总数(股，六类汇总)
            inst_ratio REAL,                     -- 机构持仓比例(%)
            inst_report_date TEXT,               -- 机构持仓报告期(YYYYMMDD)
            inst_count INTEGER,                  -- 机构股东数量(家，021Q；港股腾讯 shareholder 机构持仓统计)
            inst_count_change_pct REAL,          -- 机构股东数量环比(%，021Q；正值=机构增加，方向语义与A股户数相反)
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, stat_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 6.7 南向资金（港股通）大盘快照 —— 020R-47（仅展示参考，不参评）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS south_fund_flow (
            trade_date TEXT PRIMARY KEY,          -- 交易日 YYYY-MM-DD
            net_buy REAL,                         -- 当日成交净买额(亿元)
            buy_amount REAL,                      -- 买入成交额(亿元)
            sell_amount REAL,                     -- 卖出成交额(亿元)
            cumulative_net REAL,                  -- 历史累计净买额(亿元)
            hold_market_value REAL,               -- 持股市值(亿元)
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # ============================================================
    # 6.8 周线/月线K线表 —— 020R-48B（多周期技术面：由日线聚合，参与技术面评分）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_kline_weekly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,             -- 该周最后一个交易日
            open REAL, close REAL, high REAL, low REAL, volume REAL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_kline_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,             -- 该月最后一个交易日
            open REAL, close REAL, high REAL, low REAL, volume REAL,
            UNIQUE(stock_id, trade_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 7. 消息面数据表 —— 公告、研报等
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            info_type TEXT,                  -- 类型：announcement/research/news/policy
            title TEXT,                      -- 标题
            content TEXT,                    -- 内容摘要
            sentiment_score REAL,            -- 情绪评分（-1到1，负=利空，正=利好）
            info_date DATE,                  -- 日期
            source TEXT,                     -- 来源
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 8. 分析结果表 —— 四维打分结果
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            analysis_date DATE NOT NULL,     -- 分析日期
            fundamental_score REAL,          -- 基本面得分(0-100)
            technical_score REAL,            -- 技术面得分
            sentiment_score REAL,            -- 消息面得分
            capital_score REAL,              -- 资金面得分
            fundamental_weight REAL,         -- 基本面权重
            technical_weight REAL,
            sentiment_weight REAL,
            capital_weight REAL,
            total_score REAL,                -- 综合得分
            rating TEXT,                     -- 评级档位
            data_warnings TEXT,              -- 数据缺失提示（JSON格式）
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, analysis_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 9. 评级历史表
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            rating_date DATE NOT NULL,
            rating TEXT NOT NULL,            -- 评级档位
            total_score REAL NOT NULL,       -- 综合得分
            action_advice TEXT,              -- 操作建议
            is_change INTEGER DEFAULT 0,     -- 是否与上次评级不同（1=变化）
            price_at_rating REAL,            -- 评级时的股价
            engine_version TEXT,             -- 020R-51：产生该评级的引擎（v5/legacy；历史行为 NULL）
            UNIQUE(stock_id, rating_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 10. 变更日志表
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS change_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            log_date TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            log_type TEXT NOT NULL,          -- 类型：score_change/rating_change/advice_change
            dimension TEXT,                  -- 变化的维度（fundamental/technical/...）
            old_value TEXT,                  -- 旧值
            new_value TEXT,                  -- 新值
            description TEXT,                -- 变更说明
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 11. 回测结果表
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            rating_id INTEGER NOT NULL,      -- 关联评级记录
            market TEXT NOT NULL,            -- 市场（A股/港股分开回测）
            rating_date DATE NOT NULL,
            rating TEXT NOT NULL,
            price_at_rating REAL,            -- 评级时价格
            price_1d REAL,                   -- 1天后价格
            price_1w REAL,                   -- 1周后价格
            price_1m REAL,                   -- 1月后价格
            return_1d REAL,                  -- 1天收益率(%)
            return_1w REAL,                  -- 1周收益率(%)
            return_1m REAL,                  -- 1月收益率(%)
            is_correct INTEGER,              -- 评级是否正确（1=正确, 0=错误）
            backtest_date TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id),
            FOREIGN KEY (rating_id) REFERENCES ratings_history(id)
        )
    """)

    # ============================================================
    # 12. 策略参数表 —— 权重、阈值（供自动优化模块用）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS strategy_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,            -- 市场：a_stock / hk_stock
            param_type TEXT NOT NULL,        -- 参数类型：weights / scoring_rules / thresholds
            param_key TEXT NOT NULL,         -- 参数名
            param_value TEXT NOT NULL,       -- 参数值（JSON格式）
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(market, param_type, param_key)
        )
    """)

    # ============================================================
    # 13. 数据获取状态表 —— 记录每次数据采集的结果
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            dimension TEXT NOT NULL,         -- 维度：kline/fundamental/capital/sentiment
            status TEXT NOT NULL,            -- success / partial / failed
            message TEXT,                    -- 详情说明（如失败原因）
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 14. 消息面日聚合表 —— 模块4: 每日新闻情绪汇总
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            news_date DATE NOT NULL,             -- 聚合日期
            avg_sentiment REAL,                  -- 日均情绪得分(-1~1)
            positive_count INTEGER DEFAULT 0,    -- 正面新闻数
            negative_count INTEGER DEFAULT 0,    -- 负面新闻数
            neutral_count INTEGER DEFAULT 0,     -- 中性新闻数
            total_count INTEGER DEFAULT 0,       -- 总新闻数
            top_news_title TEXT,                 -- 最重要新闻标题(供展示)
            source_urls TEXT,                    -- 新闻来源URL列表(JSON数组)
            fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            UNIQUE(stock_id, news_date),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)

    # ============================================================
    # 15. 错误日志表 —— 各模块异常记录
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER,
            module TEXT,                         -- 模块名：news_collector等
            error_type TEXT,                     -- 错误类型
            error_message TEXT,                  -- 错误详情
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id)
        )
    """)
