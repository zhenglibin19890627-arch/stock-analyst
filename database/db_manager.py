"""
数据库管理模块
负责创建和管理所有数据库表。你不需要手动操作数据库，这个模块会自动建好一切。
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime

# 确保能找到项目根目录的 config 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

_logger = logging.getLogger(__name__)

# ============================================================
# 破坏性操作自动备份配置
# DROP TABLE / DELETE / 清表 等不可逆操作执行前会自动创建带时间戳的备份
# ============================================================
# 备份存放目录：与数据库文件同级的专用 backups/ 文件夹
BACKUP_DIR = os.path.join(os.path.dirname(DB_PATH), 'backups')
# 保留最近 N 份备份，超出则按修改时间清理最旧的
MAX_BACKUPS = 10


def get_connection():
    """
    连接数据库，如果数据库文件不存在会自动创建。
    WAL模式：允许多个进程同时读取，写入时不阻塞读取。
    busy_timeout：遇到锁时等待10秒而不是立刻报错。
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # 查询结果可以用列名访问
    conn.execute('PRAGMA journal_mode=WAL')  # WAL模式：大幅减少锁冲突
    conn.execute('PRAGMA busy_timeout=10000')  # 锁等待10秒
    conn.execute('PRAGMA foreign_keys=OFF')  # 关闭外键约束（应用层手动管理级联逻辑）
    return conn


def backup_database(reason='manual'):
    """在破坏性操作前自动创建带时间戳的数据库备份。

    采用 SQLite 在线 .backup() API（而非 shutil 文件复制），保证即使在 WAL 模式下
    且数据库正被写入时，也能获得事务一致的完整快照（无需手动处理 -wal/-shm 旁路文件）。
    不修改现有 WAL 模式与 busy_timeout 配置。

    本函数为“尽力而为”语义：备份失败时记录错误并返回 None，不抛异常，由调用方
    决定是否继续。对于真正不可逆的 DROP TABLE，调用方可检查返回值选择中止。

    Args:
        reason: 触发备份的原因标识（如 'drop_daily_reports'、'clear_price_backtest'），
                仅保留字母数字/下划线，写入文件名便于追溯。

    Returns:
        成功返回备份文件绝对路径；失败返回 None。
    """
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # 清理 reason 中不安全的字符，避免破坏文件名
        safe_reason = ''.join(
            c if (c.isalnum() or c in '-_') else '_' for c in str(reason)
        )[:40]
        backup_name = f'db_backup_{timestamp}_{safe_reason}.db'
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        # SQLite 在线热备份：源库无需关闭，自动处理 WAL，输出为完整一致的单文件
        source = sqlite3.connect(DB_PATH)
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        # 保留最近 MAX_BACKUPS 份，清理更早的备份
        _prune_old_backups(keep=MAX_BACKUPS)

        msg = f'[备份] 破坏性操作前已创建数据库备份: {backup_name} (原因: {reason})'
        print(msg)
        _logger.info(msg)
        return backup_path
    except Exception as e:
        err = f'[备份警告] 数据库备份失败，破坏性操作前未能生成备份: {e}'
        print(err)
        _logger.error(err)
        return None


def _prune_old_backups(keep):
    """保留最近 keep 份备份，按修改时间删除更早的。

    仅清理由 backup_database 生成的 db_backup_*.db 文件，不影响目录内其他文件。
    """
    try:
        backups = [
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.startswith('db_backup_') and f.endswith('.db')
        ]
        if len(backups) <= keep:
            return
        # 按修改时间排序，最旧的在前
        backups.sort(key=lambda p: os.path.getmtime(p))
        for old_path in backups[: len(backups) - keep]:
            try:
                os.remove(old_path)
                _logger.info(f'[备份] 清理旧备份: {os.path.basename(old_path)}')
            except OSError:
                pass
    except Exception as e:
        _logger.warning(f'[备份] 清理旧备份时出错: {e}')


def init_database():
    """
    初始化数据库 —— 创建所有需要的表。
    如果表已存在则跳过，不会覆盖已有数据。
    """
    conn = get_connection()
    cursor = conn.cursor()

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
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            group_id INTEGER,                    -- 所属持仓分组
            cost_price REAL DEFAULT 0,           -- 成本价
            quantity INTEGER DEFAULT 0,          -- 持仓数量
            notes TEXT,                          -- 备注
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (stock_id) REFERENCES stocks(id),
            UNIQUE(stock_id)                     -- 同一股票仅一条持仓
        )
    """)

    # ============================================================
    # 18. 交易流水表 —— 仅作记录备查，不参与成本自动计算
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holding_id INTEGER,                  -- 关联持仓（删除持仓时置NULL）
            stock_id INTEGER NOT NULL,           -- 股票（冗余字段，便于查询）
            trade_type TEXT NOT NULL,            -- buy / sell / dividend
            price REAL,                          -- 成交价
            quantity INTEGER,                    -- 成交数量
            amount REAL,                         -- 成交金额
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
    # 插入默认分组到统一 groups 表（如果还不存在）
    # 自选股和持仓各创建同名默认分组，实现双向同步
    # ============================================================
    default_group_names = ['核心持仓', '观察池', '短线关注']
    for name in default_group_names:
        cursor.execute(
            """
            INSERT OR IGNORE INTO groups (name, type, is_default)
            VALUES (?, 'watchlist', 1)
        """,
            (name,),
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO groups (name, type, is_default)
            VALUES (?, 'portfolio', 1)
        """,
            (name,),
        )

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

    # ============================================================
    # 列迁移：为 analysis_results 表补充新字段（安全幂等，已有列则跳过）
    # ============================================================
    _migrate_columns(cursor)

    # ============================================================
    # 013: daily_reports 新增 report_type 列 + 变更唯一约束（幂等）
    # ============================================================
    _migrate_daily_reports_type(cursor)

    # ============================================================
    # 分组表迁移：将旧 stock_groups / portfolio_groups 数据
    # 迁移到统一 groups 表（安全幂等，已迁移则跳过）
    # ============================================================
    _migrate_to_unified_groups(cursor)

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
            # 破坏性操作前自动备份（仅在首次迁移清理重复数据时触发一次）
            backup_database('delete_ratings_history_duplicates')
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

    # ============================================================
    # 24. 预警规则表 —— P3-B: 智能预警规则配置（架构师评审 D2）
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,              -- rating_change / score_below / capital_outflow
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
            alert_type TEXT NOT NULL,             -- rating_change / score_below / capital_outflow
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

    # ============================================================
    # P3-B: 插入全局默认规则（幂等）
    # G2A: 评分跌破默认65分；G2B: 连续净流出默认3天；rating_change 无阈值
    # 注意：SQLite 中 NULL!=NULL，UNIQUE 约束无法去重全局规则(stock_id IS NULL)，
    # 因此用 WHERE NOT EXISTS 逐条幂等插入
    # ============================================================
    for rt, th in [('rating_change', None), ('score_below', 65.0), ('capital_outflow', 3)]:
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

    # 012-C: error_logs 表增强（增加 dimension + traceback 字段）
    try:
        cursor.execute('SELECT dimension FROM error_logs LIMIT 1')
    except Exception:
        cursor.execute('ALTER TABLE error_logs ADD COLUMN dimension TEXT')
    try:
        cursor.execute('SELECT traceback FROM error_logs LIMIT 1')
    except Exception:
        cursor.execute('ALTER TABLE error_logs ADD COLUMN traceback TEXT')

    conn.commit()
    conn.close()
    print('[数据库] 所有表创建完成，默认分组和初始策略参数已就绪。')


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
    # price_cache 索引：按 stock_id 快速查找
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_cache_stock
        ON price_cache(stock_id)
    """)

    # 010-3: price_backtest_results 幂等追加5列（参考 backtest_engine._ensure_columns 模式）
    _ensure_price_backtest_columns(cursor)


def _migrate_daily_reports_type(cursor):
    """013: daily_reports 新增 report_type 列 + 变更唯一约束（幂等）

    将 UNIQUE(report_date, stock_id) 改为 UNIQUE(report_date, stock_id, report_type)，
    采用 SQLite 标准表重建模式。report_type 列存在则跳过。
    """
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
    # 破坏性操作（DROP TABLE）前自动备份
    backup_database('drop_daily_reports_rebuild')
    cursor.execute('DROP TABLE daily_reports')
    cursor.execute('ALTER TABLE daily_reports_new RENAME TO daily_reports')
    # 重建索引
    cursor.execute("""CREATE INDEX IF NOT EXISTS idx_daily_reports_date
        ON daily_reports(report_date)""")
    print('[013迁移] daily_reports 表重建完成')


def _ensure_price_backtest_columns(cursor=None):
    """确保 price_backtest_results 表有010新增的5列（ALTER TABLE ADD COLUMN，幂等）。

    参考 backtest_engine.py L83-103 的 _ensure_columns 模式。
    可在 init_database 中传 cursor 统一执行，也可独立调用（自建连接）。
    """
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


if __name__ == '__main__':
    init_database()
