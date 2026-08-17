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
        """盘中时段：刷新今日 bar，历史不动，保留既有 amount"""
        _patch_now(monkeypatch)  # 周四 10:30
        df = pd.DataFrame(
            {
                '日期': ['2026-08-12', '2026-08-13'],
                '开盘': [9.0, 9.6],
                '收盘': [9.5, 11.2],
                '最高': [9.8, 11.5],
                '最低': [8.9, 9.5],
                '成交量': [1000, 900],
            }
        )
        df['涨跌幅'] = df['收盘'].pct_change() * 100
        monkeypatch.setattr(dc, '_fetch_kline_tencent', lambda s, m: df)

        status, msg = dc.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        assert '盘中刷新' in msg

        conn = idb.get_connection()
        row = conn.execute(
            "SELECT close, amount FROM raw_kline WHERE stock_id=1 AND trade_date='2026-08-13'"
        ).fetchone()
        n = conn.execute('SELECT COUNT(*) FROM raw_kline WHERE stock_id=1').fetchone()[0]
        conn.close()
        assert row['close'] == 11.2  # 今日 bar 已更新
        assert row['amount'] == 4000000  # amount 保留（腾讯接口不提供）
        assert n == 2  # 历史数据未动

    def test_skip_out_of_session(self, idb, monkeypatch):
        """非盘中时段（如 20:00）：保持同日跳过，不重拉腾讯K线"""
        _patch_now(monkeypatch, hour=20, minute=0)

        def _boom(*a, **k):
            raise AssertionError('非盘中不应重拉腾讯K线')

        monkeypatch.setattr(dc, '_fetch_kline_tencent', _boom)

        status, msg = dc.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        assert '同日跳过' in msg
