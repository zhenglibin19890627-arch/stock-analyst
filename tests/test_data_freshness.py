"""
报告生成前的数据完整度检查 _build_data_freshness 单元测试

覆盖：
1. 数据完整（无滞后/替代源）→ has_issue=False
2. 资金面估算兜底/新浪顶替 → has_issue=True 且行含来源说明
3. K线滞后 >3 天 → ⚠️
4. 新闻滞后 >7 天 → ⚠️
5. 各维度完全缺失 → ⚠️
6. _days_between 日期差工具函数
"""

from datetime import datetime, timedelta

import pytest

from database import db_manager
from modules.daily_report import _build_data_freshness, _days_between


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """临时库 + 基础表 + 一只股票（无任何维度数据）"""
    db_file = tmp_path / 'freshness.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
    conn.commit()
    conn.close()
    return db_manager


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime('%Y-%m-%d')


def _insert_kline(db, stock_id, date):
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, 10, 11, 12, 9, 1000)',
        (stock_id, date),
    )
    conn.commit()
    conn.close()


def _insert_fundamental(db, stock_id, report_date):
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO raw_fundamental (stock_id, report_date, roe) VALUES (?, ?, 12.5)',
        (stock_id, report_date),
    )
    conn.commit()
    conn.close()


def _insert_capital(db, stock_id, date, source=None, estimated=0):
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, capital_source, is_estimated) '
        'VALUES (?, ?, 100.0, ?, ?)',
        (stock_id, date, source, estimated),
    )
    conn.commit()
    conn.close()


def _insert_sentiment(db, stock_id, news_date, info_date):
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO news_sentiment (stock_id, news_date, avg_sentiment, total_count) '
        'VALUES (?, ?, 0.3, 10)',
        (stock_id, news_date),
    )
    conn.execute(
        "INSERT INTO raw_sentiment (stock_id, info_type, title, info_date) "
        "VALUES (?, 'news', '测试新闻', ?)",
        (stock_id, info_date),
    )
    conn.commit()
    conn.close()


class TestDaysBetween:
    def test_same_day(self):
        assert _days_between('2026-08-13', '2026-08-13') == 0

    def test_one_day(self):
        assert _days_between('2026-08-12', '2026-08-13') == 1

    def test_invalid(self):
        assert _days_between('abc', '2026-08-13') is None
        assert _days_between(None, '2026-08-13') is None


class TestDataFreshness:
    def test_all_fresh(self, db):
        """各维度数据新鲜 → has_issue=False"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1))  # 东财真实
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(2))

        r = _build_data_freshness(1)
        assert r['has_issue'] is False
        assert len(r['lines']) == 5

    def test_capital_estimated_flag(self, db):
        """资金面估算兜底（不参评）→ 有 issue 且行含说明"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1), estimated=1)
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(1))

        r = _build_data_freshness(1)
        assert r['has_issue'] is True
        assert any('估算兜底' in line for line in r['lines'])

    def test_capital_sina_fallback_flag(self, db):
        """资金面新浪顶替 → 有 issue"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1), source='sina_main')
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(1))

        r = _build_data_freshness(1)
        assert r['has_issue'] is True
        assert any('新浪顶替' in line for line in r['lines'])

    def test_kline_stale_flag(self, db):
        """K线滞后 5 天（相对全市场最新交易日）→ ⚠️

        021A 更新：020R-19 起滞后基准改为"全市场 K 线日期最大值"
        （休市日数据至最新交易日即视为最新）。需另一只股票提供更新
        的交易日基准，本股票的 5 天前 K 线才构成滞后。
        """
        conn = db.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600000', 'a_stock', '基准股')")
        conn.commit()
        conn.close()
        _insert_kline(db, 2, _days_ago(0))  # 市场最新交易日 = 今天
        _insert_kline(db, 1, _days_ago(5))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1))
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(1))

        r = _build_data_freshness(1)
        assert r['has_issue'] is True
        assert any('K线' in line and '⚠️' in line for line in r['lines'])

    def test_news_stale_flag(self, db):
        """新闻滞后 10 天 → ⚠️"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1))
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(10))

        r = _build_data_freshness(1)
        assert r['has_issue'] is True
        assert any('消息面' in line and '⚠️' in line for line in r['lines'])

    def test_all_missing(self, db):
        """全部维度缺失 → ⚠️"""
        r = _build_data_freshness(1)
        assert r['has_issue'] is True
        assert any('缺失' in line for line in r['lines'])

    def test_forecast_present(self, db):
        """业绩预告有数据 → 行说明条数与报告期（020R-50 起标签为「业绩预期」）"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1))
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(1))
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO raw_forecast (stock_id, symbol, report_period, indicator, forecast_type) "
            "VALUES (1, '600276', '20260630', '归属于上市公司股东的净利润', '预增')"
        )
        conn.commit()
        conn.close()

        r = _build_data_freshness(1)
        assert any('业绩预期' in line and '预告 1 条' in line and '20260630' in line for line in r['lines'])

    def test_express_present(self, db):
        """业绩快报有数据 → 行说明条数与报告期（020R-50）"""
        _insert_kline(db, 1, _days_ago(1))
        _insert_fundamental(db, 1, '2026-06-30')
        _insert_capital(db, 1, _days_ago(1))
        _insert_sentiment(db, 1, _days_ago(1), _days_ago(1))
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO raw_express (stock_id, symbol, report_period, np_yoy) "
            "VALUES (1, '601888', '20260630', 19.49)"
        )
        conn.commit()
        conn.close()

        r = _build_data_freshness(1)
        assert any('业绩预期' in line and '快报 1 条' in line and '20260630' in line for line in r['lines'])
