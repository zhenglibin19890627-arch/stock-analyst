"""
019T T3：回测基准化（alpha 判定）单元测试

覆盖：
1. _ensure_columns 幂等追加 7 个基准/alpha 列（不重建表、不动既有列）
2. _get_bench_tn 时间对齐（基准价 = trade_date <= rating_date 最近收盘；T+n 严格第 n 行之后）
3. _compute_alpha_block 全路径：有基准 / 缺基准（全 NULL 不判定）/ 主 alpha 顺延
4. is_correct 原口径保留（_judge 未被改动）
"""

import sqlite3

import pytest
from modules import backtest_engine as be
from modules.backtest_engine import BacktestEngine, _judge


@pytest.fixture()
def bench_db(tmp_path, monkeypatch):
    """临时 SQLite：index_kline + backtest_results 最小表结构"""
    db_path = str(tmp_path / 'bench_test.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        CREATE TABLE index_kline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            UNIQUE(index_code, trade_date)
        )
    ''')
    c.execute('''
        CREATE TABLE backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL, rating_id INTEGER NOT NULL,
            market TEXT NOT NULL, rating_date DATE NOT NULL, rating TEXT NOT NULL,
            price_at_rating REAL, price_1d REAL, price_1w REAL, price_1m REAL,
            return_1d REAL, return_1w REAL, return_1m REAL,
            is_correct INTEGER, backtest_date TIMESTAMP
        )
    ''')
    rows = [
        ('000300', '2026-07-30', 100.0), ('000300', '2026-07-31', 101.0),
        ('000300', '2026-08-03', 102.0), ('000300', '2026-08-04', 103.5),
        ('000300', '2026-08-05', 104.0), ('000300', '2026-08-06', 105.0),
        ('000300', '2026-08-07', 106.0),
        ('HSI', '2026-07-31', 20000.0), ('HSI', '2026-08-03', 20100.0),
    ]
    for code, d, close in rows:
        c.execute('INSERT INTO index_kline (index_code, trade_date, close) VALUES (?,?,?)',
                  (code, d, close))
    conn.commit()
    conn.close()

    import config
    import database.db_manager as dbm

    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(dbm, 'DB_PATH', db_path)
    yield db_path


class TestEnsureColumns:
    def test_appends_alpha_columns_idempotently(self, bench_db):
        conn = sqlite3.connect(bench_db)
        before = {r[1] for r in conn.execute('PRAGMA table_info(backtest_results)')}
        conn.close()
        assert 'bench_return_1d' not in before

        BacktestEngine()  # __init__ 触发 _ensure_columns
        BacktestEngine()  # 幂等：二次执行不报错

        conn = sqlite3.connect(bench_db)
        after = {r[1] for r in conn.execute('PRAGMA table_info(backtest_results)')}
        conn.close()
        for col in ('bench_return_1d', 'bench_return_1w', 'bench_return_1m',
                    'alpha_1d', 'alpha_1w', 'alpha_1m', 'is_correct_alpha'):
            assert col in after
        # 既有列不受影响
        for col in before:
            assert col in after


class TestGetBenchTn:
    def test_base_is_latest_close_not_after_rating_date(self, bench_db):
        engine = BacktestEngine()
        base, base_date, tn = engine._get_bench_tn('000300', '2026-08-03', 1)
        assert base == 102.0  # <= 08-03 最近收盘（07-31 之后的 08-03）
        assert base_date == '2026-08-03'
        assert tn == 103.5  # 基准行之后第 1 行

    def test_base_before_weekend_rating_date(self, bench_db):
        # 周末补跑场景：rating_date 无 K 线，取之前最近收盘
        engine = BacktestEngine()
        base, base_date, _ = engine._get_bench_tn('000300', '2026-08-01', 0)
        assert base_date == '2026-07-31'
        assert base == 101.0

    def test_tn_nth_row_strictly_after_base(self, bench_db):
        engine = BacktestEngine()
        base, base_date, tn5 = engine._get_bench_tn('000300', '2026-07-31', 5)
        assert base_date == '2026-07-31'
        assert tn5 == 106.0  # 07-31 之后第 5 行（08-07）
        _, _, tn6 = engine._get_bench_tn('000300', '2026-07-31', 6)
        assert tn6 is None  # 第 6 行不存在

    def test_bench_missing_returns_none(self, bench_db):
        engine = BacktestEngine()
        base, _, tn = engine._get_bench_tn('000300', '2020-01-01', 1)
        assert base is None and tn is None


class TestComputeAlphaBlock:
    def test_full_alpha_computation(self, bench_db):
        engine = BacktestEngine()
        returns = {'return_1d': 2.5, 'return_1w': 3.0, 'return_1m': 5.0}
        block = engine._compute_alpha_block('a_stock', '2026-07-31', '推荐买入', returns)
        # 基准：base=101.0(07-31)；T+1=102.0 → bench +0.99%；T+5=106.0(08-07) → bench +4.95%
        assert block['bench_return_1d'] == pytest.approx(0.99, abs=0.01)
        assert block['bench_return_1w'] == pytest.approx(4.95, abs=0.01)
        assert block['bench_return_1m'] is None  # 07-31 后第 20 行不存在
        assert block['alpha_1d'] == pytest.approx(2.5 - 0.99, abs=0.02)
        assert block['alpha_1w'] == pytest.approx(3.0 - 4.95, abs=0.02)
        assert block['alpha_1m'] is None
        # 主 alpha = alpha_1d ≈ 1.51 >= 0.5 → 正确
        assert block['is_correct_alpha'] == 1

    def test_missing_bench_all_null_no_judgement(self, bench_db):
        engine = BacktestEngine()
        returns = {'return_1d': 2.5, 'return_1w': 3.0, 'return_1m': 5.0}
        block = engine._compute_alpha_block('a_stock', '2020-01-01', '推荐买入', returns)
        for v in block.values():
            assert v is None  # 缺基准 → 不判定、不代理

    def test_primary_alpha_falls_through_to_1w(self, bench_db):
        engine = BacktestEngine()
        returns = {'return_1d': None, 'return_1w': -1.0, 'return_1m': None}
        block = engine._compute_alpha_block('a_stock', '2026-07-31', '强烈推荐买入', returns)
        assert block['alpha_1d'] is None
        assert block['alpha_1w'] == pytest.approx(-1.0 - 4.95, abs=0.02)
        # 主 alpha 顺延 1w = -5.95 <= -3.0 → 错误（0）
        assert block['is_correct_alpha'] == 0

    def test_unknown_market_no_bench(self, bench_db):
        engine = BacktestEngine()
        block = engine._compute_alpha_block('other_market', '2026-07-31', '持有观望', {})
        assert all(v is None for v in block.values())


class TestJudgeUnchanged:
    def test_is_correct_original_semantics_kept(self):
        # 019T T3：is_correct 原口径（绝对收益判定）保留，未被改动
        assert _judge('推荐买入', 1.0) == 1
        assert _judge('推荐买入', -2.5) == 0
        assert _judge('推荐买入', 0.2) is None
        assert _judge('持有观望', 1.0) == 1
        assert _judge('持有观望', 3.0) == 0
        assert _judge('建议减仓', -1.0) == 1
        assert _judge(None, 1.0) is None
