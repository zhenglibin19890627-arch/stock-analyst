# -*- coding: utf-8 -*-
"""020R-56：港股估值兜底修复测试
- 同日 failed 记录不跳过（允许重试）
- 同日 success 记录跳过
- 腾讯兜底写入 PE/PB/总市值
"""

import pytest

from database import db_manager
from modules import data_collector as dc


@pytest.fixture
def val_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'val56_test.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('HK3690', 'hk_stock', '美团-W')"
    )
    # 腾讯快照无日期：trade_date 取自该股最新K线
    conn.execute(
        "INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (1, '2026-08-14', 100.0)"
    )
    conn.commit()
    conn.close()
    return db_manager


def _insert_status(db, status):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO data_status (stock_id, dimension, status, message, fetched_at) "
        "VALUES (1, 'valuation', ?, 't', datetime('now', 'localtime'))",
        (status,),
    )
    conn.commit()
    conn.close()


class TestValuationSameDaySkip:
    def test_failed_same_day_retries_tencent(self, val_db, monkeypatch):
        """同日最新记录为 failed → 不跳过，走降级链路（腾讯兜底）重试并入库"""
        _insert_status(val_db, 'failed')

        def _fail_ak(*a, **k):
            raise RuntimeError('akshare 失效')

        def _fail_bs(*a, **k):
            raise RuntimeError('baostock 不支持港股')

        monkeypatch.setattr(dc, '_fetch_valuation_akshare', _fail_ak)
        monkeypatch.setattr(dc, '_fetch_valuation_baostock', _fail_bs)
        monkeypatch.setattr(
            dc, '_fetch_valuation_tencent', lambda s, m: (-20.83, 4.88, 5.385e11)
        )

        status, msg = dc.fetch_valuation('HK3690', 'hk_stock')
        assert status == 'success'
        assert 'tencent' in msg

        conn = val_db.get_connection()
        row = conn.execute(
            'SELECT pe_ttm, pb_mrq, total_mv, source FROM stock_valuation WHERE stock_id=1'
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['pe_ttm'] == -20.83
        assert row['pb_mrq'] == 4.88
        assert row['total_mv'] == pytest.approx(5.385e11)
        assert row['source'] == 'tencent'

    def test_success_same_day_skips(self, val_db, monkeypatch):
        """同日最新记录为真实 success → 跳过，不再触网"""
        _insert_status(val_db, 'success')

        def _boom(*a, **k):
            raise AssertionError('成功当日不应再触发采集')

        monkeypatch.setattr(dc, '_fetch_valuation_akshare', _boom)
        monkeypatch.setattr(dc, '_fetch_valuation_baostock', _boom)
        monkeypatch.setattr(dc, '_fetch_valuation_tencent', _boom)

        status, msg = dc.fetch_valuation('HK3690', 'hk_stock')
        assert status == 'success'
        assert '同日跳过' in msg
        # 跳过记录写为 skipped（防止跳过链自延续）
        conn = val_db.get_connection()
        st = conn.execute(
            "SELECT status FROM data_status WHERE stock_id=1 AND dimension='valuation' "
            'ORDER BY fetched_at DESC LIMIT 1'
        ).fetchone()
        conn.close()
        assert st['status'] == 'skipped'

    def test_legacy_skip_chain_record_collects(self, val_db, monkeypatch):
        """历史遗留的「success + 同日跳过」记录（跳过链）→ 不跳过，真正采集"""
        conn = val_db.get_connection()
        conn.execute(
            "INSERT INTO data_status (stock_id, dimension, status, message, fetched_at) "
            "VALUES (1, 'valuation', 'success', '同日跳过(估值当日已采集)', datetime('now', 'localtime'))"
        )
        conn.commit()
        conn.close()

        def _fail_ak(*a, **k):
            raise RuntimeError('akshare 失效')

        def _fail_bs(*a, **k):
            raise RuntimeError('baostock 不支持港股')

        monkeypatch.setattr(dc, '_fetch_valuation_akshare', _fail_ak)
        monkeypatch.setattr(dc, '_fetch_valuation_baostock', _fail_bs)
        monkeypatch.setattr(
            dc, '_fetch_valuation_tencent', lambda s, m: (8.41, 2.01, 1.73e11)
        )

        status, msg = dc.fetch_valuation('HK3690', 'hk_stock')
        assert status == 'success'
        assert 'tencent' in msg
