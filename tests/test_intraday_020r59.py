# -*- coding: utf-8 -*-
"""020R-59：盘中刷新测试（交易时段判断 + 当日K线bar刷新 + 非盘中保持同日跳过）"""

import datetime as _dt

import pandas as pd
import pytest

from database import db_manager
from modules import data_collector as dc


class _FakeDT:
    """伪造 datetime：now() 返回固定时刻（year/month/day/hour/minute 可配）"""

    def __init__(self, real):
        self._real = real

    def now(self, tz=None):
        return self._real


def _patch_now(monkeypatch, year=2026, month=8, day=13, hour=10, minute=30):
    monkeypatch.setattr(dc, 'datetime', _FakeDT(_dt.datetime(year, month, day, hour, minute)))


@pytest.fixture
def idb(tmp_path, monkeypatch):
    db_file = tmp_path / 'id59.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')"
    )
    conn.execute(
        'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume, amount, pct_change) '
        "VALUES (1, '2026-08-12', 9.0, 9.5, 9.8, 8.9, 1000, 5000000, 1.0),"
        "(1, '2026-08-13', 9.6, 10.0, 10.2, 9.5, 800, 4000000, 5.26)"
    )
    conn.commit()
    conn.close()
    return db_manager


class TestIntradaySession:
    @pytest.mark.parametrize(
        'hour,minute,market,expected',
        [
            (10, 30, 'a_stock', True),
            (12, 0, 'a_stock', False),  # 午休
            (14, 0, 'a_stock', True),
            (15, 30, 'a_stock', False),  # 已收盘
            (9, 0, 'a_stock', False),  # 未开盘
            (15, 30, 'hk_stock', True),
            (16, 30, 'hk_stock', False),
            (10, 0, 'hk_stock', True),
        ],
    )
    def test_session(self, monkeypatch, hour, minute, market, expected):
        _patch_now(monkeypatch, hour=hour, minute=minute)
        assert dc._is_intraday_session(market) is expected

    def test_weekend_false(self, monkeypatch):
        _patch_now(monkeypatch, day=15)  # 2026-08-15 周六
        assert dc._is_intraday_session('a_stock') is False


class TestIntradayKlineRefresh:
    def test_refresh_today_bar_in_session(self, idb, monkeypatch):
        """盘中时段：腾讯行情一个请求刷新今日 bar，历史不动，保留既有 amount"""
        _patch_now(monkeypatch)  # 周四 10:30

        parts = [''] * 88
        parts[0] = 'v_sz002352="1'
        parts[1] = '顺丰控股'
        parts[2] = '002352'
        parts[3] = '32.46'  # 现价
        parts[4] = '32.74'  # 昨收
        parts[5] = '32.72'  # 今开
        parts[6] = '77746'  # 成交量
        parts[33] = '32.79'  # 最高
        parts[34] = '32.44'  # 最低
        quote_text = '~'.join(parts)

        class _FakeResp:
            encoding = None

            def __init__(self, text):
                self.text = text

        monkeypatch.setattr(dc, '_http_get', lambda url: _FakeResp(quote_text))

        status, msg = dc.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        assert '盘中刷新' in msg

        conn = idb.get_connection()
        row = conn.execute(
            "SELECT close, pct_change, amount FROM raw_kline WHERE stock_id=1 AND trade_date='2026-08-13'"
        ).fetchone()
        n = conn.execute('SELECT COUNT(*) FROM raw_kline WHERE stock_id=1').fetchone()[0]
        conn.close()
        assert row['close'] == 32.46  # 今日 bar 已更新（腾讯行情现价）
        assert row['pct_change'] == pytest.approx(-0.86, abs=0.01)  # (32.46-32.74)/32.74
        assert row['amount'] == 4000000  # amount 保留
        assert n == 2  # 历史数据未动

    def test_skip_out_of_session(self, idb, monkeypatch):
        """非盘中时段（如 20:00）：保持同日跳过，不重拉腾讯行情"""
        _patch_now(monkeypatch, hour=20, minute=0)

        def _boom(*a, **k):
            raise AssertionError('非盘中不应重拉腾讯行情')

        monkeypatch.setattr(dc, '_http_get', _boom)

        status, msg = dc.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        assert '同日跳过' in msg


class TestSentimentIntraday020R60:
    def _seed_today_news(self, idb):
        conn = idb.get_connection()
        conn.execute(
            "INSERT INTO news_sentiment (stock_id, news_date, avg_sentiment, total_count) "
            "VALUES (1, '2026-08-13', 0.3, 10)"
        )
        conn.commit()
        conn.close()

    def test_intraday_bypasses_skip(self, idb, monkeypatch):
        """盘中时段：当日已有消息面记录也重采（午间公告进盘中快报）"""
        _patch_now(monkeypatch)  # 周四 10:30
        self._seed_today_news(idb)

        import modules.news_collector as nc

        monkeypatch.setattr(nc, 'collect_news', lambda *a, **k: ('success', '盘中重采成功'))
        status, msg = dc.fetch_sentiment('600276', 'a_stock')
        assert status == 'success'
        assert '盘中重采成功' in msg

    def test_out_of_session_skips(self, idb, monkeypatch):
        """非盘中：当日已有记录 → 同日跳过，不重采"""
        _patch_now(monkeypatch, hour=20, minute=0)
        self._seed_today_news(idb)

        import modules.news_collector as nc

        def _boom(*a, **k):
            raise AssertionError('非盘中不应重采消息面')

        monkeypatch.setattr(nc, 'collect_news', _boom)
        status, msg = dc.fetch_sentiment('600276', 'a_stock')
        assert status == 'success'
        assert '当日跳过' in msg


class TestForecastExpressDayGate020R60:
    def _seed_status(self, idb, dimension):
        conn = idb.get_connection()
        conn.execute(
            "INSERT INTO data_status (stock_id, dimension, status, message, fetched_at) "
            "VALUES (1, ?, 'success', 'x', datetime('now', 'localtime'))",
            (dimension,),
        )
        conn.commit()
        conn.close()

    def test_forecast_day_gate(self, idb, monkeypatch):
        self._seed_status(idb, 'forecast')

        def _boom(*a, **k):
            raise AssertionError('当日已采集不应再查全市场预告表')

        monkeypatch.setattr(dc, '_get_forecast_df_for_period', _boom)
        status, msg = dc.collect_forecast(1, '600276', 'a_stock')
        assert status == 'success'
        assert '同日跳过' in msg

    def test_express_day_gate(self, idb, monkeypatch):
        self._seed_status(idb, 'express')
        monkeypatch.setattr(
            dc, '_get_express_df_for_period',
            lambda p: (_ for _ in ()).throw(AssertionError('当日已采集不应再查全市场快报表')),
        )
        status, msg = dc.collect_express(1, '600276', 'a_stock')
        assert status == 'success'
        assert '同日跳过' in msg

    def test_forecast_no_record_proceeds(self, idb, monkeypatch):
        monkeypatch.setattr(dc, '_get_forecast_df_for_period', lambda p: pd.DataFrame())
        status, msg = dc.collect_forecast(1, '600276', 'a_stock')
        assert status == 'success'
        assert '暂无' in msg
