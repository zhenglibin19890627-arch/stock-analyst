# -*- coding: utf-8 -*-
"""020R-51：引擎版本标记（A）+ 技术面专项历史回测（B）测试"""

from datetime import date, timedelta

import pytest

from database import db_manager


@pytest.fixture
def bt_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'bt51_test.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return db_manager


def _insert_synth_kline(db, stock_id, n=130, start='2025-01-01'):
    conn = db.get_connection()
    d0 = date.fromisoformat(start)
    for i in range(n):
        d = d0 + timedelta(days=i)
        base = 10.0 + i * 0.1
        wave = 0.5 if i % 10 < 5 else 0.0
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, high, low, close, volume) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (stock_id, d.isoformat(), base, base + 1.0, base - 1.0, base + wave, 1000000.0),
        )
    conn.commit()
    conn.close()


class TestTechnicalBacktest:
    """020R-51-B：历史回测跑通、分档汇总、短历史跳过"""

    def test_runs_and_buckets_sum(self, bt_db):
        conn = bt_db.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600000', 'a_stock', '测试股')"
        )
        conn.commit()
        conn.close()
        _insert_synth_kline(bt_db, 1)

        from modules import technical_backtest as tb

        res = tb.run_technical_backtest(market='a_stock')
        assert res['samples'] > 0
        total = sum(b['n_all'] for b in res['buckets'].values())
        assert total == res['samples']
        # 全部观测 T+20 平均与基准同口径（朴素持有对照）
        assert res['overall']['t20']['avg'] == res['benchmark_avg_ret20']
        # 方向命中率只在偏多/偏空上计算（中性为 None）
        assert res['buckets']['中性']['t20']['dir_hit'] is None

    def test_skips_short_history(self, bt_db):
        conn = bt_db.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '次新')"
        )
        conn.commit()
        conn.close()
        _insert_synth_kline(bt_db, 1, n=30)

        from modules import technical_backtest as tb

        res = tb.run_technical_backtest(market='a_stock')
        assert res['samples'] == 0
        assert len(res['skipped']) == 1


class TestEngineStats:
    """020R-51-A：回测市场报告按 ratings_history.engine_version 分层"""

    def test_market_report_engine_split(self, bt_db):
        from modules.backtest_engine import BacktestEngine

        conn = bt_db.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600002', 'a_stock', '甲')"
        )
        conn.commit()
        conn.execute(
            "INSERT INTO ratings_history (stock_id, rating_date, rating, total_score, engine_version) "
            "VALUES (1, '2026-08-01', 'A', 85.0, 'v5'), (1, '2026-08-02', 'B', 60.0, 'legacy')"
        )
        conn.commit()
        rh = conn.execute(
            'SELECT id FROM ratings_history ORDER BY rating_date'
        ).fetchall()
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, return_1m, is_correct) "
            "VALUES (1, ?, 'a_stock', '2026-08-01', 'A', 3.0, 1), "
            "(1, ?, 'a_stock', '2026-08-02', 'B', -1.0, 0)",
            (rh[0]['id'], rh[1]['id']),
        )
        conn.commit()
        conn.close()

        report = BacktestEngine().compute_market_report('a_stock')
        assert 'engine_stats' in report
        assert report['engine_stats']['v5']['total'] == 1
        assert report['engine_stats']['v5']['accuracy'] == 1.0
        assert report['engine_stats']['legacy']['total'] == 1
        assert report['engine_stats']['legacy']['accuracy'] == 0.0
