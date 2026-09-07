"""
021P：回测判定口径市场差异化单元测试

覆盖：
1. 观望档区间差异化：港股 ±3.5%、A股 ±2%（边界值双侧验证）
2. 涨跌档不受市场影响（仅观望档差异化）
3. 默认参数向后兼容（不传 market = A股口径，历史调用方零破坏）
4. HK_JUDGE_OVERRIDES 只覆盖观望档，不引入其他档位漂移
5. 报告解读：港股报告包含口径声明，A股报告不包含
"""

import sqlite3

import pytest

from modules.backtest_engine import HK_JUDGE_OVERRIDES, JUDGEMENT_MATRIX, BacktestEngine, _judge


class TestJudgeMarketDifferentiation:
    """_judge 市场差异化判定。"""

    def test_neutral_a_stock_default_band(self):
        """默认（A股）观望 ±2%：+2.0/-2.0 边界内正确。"""
        assert _judge('持有观望', 2.0) == 1
        assert _judge('持有观望', -2.0) == 1
        assert _judge('持有观望', 1.99) == 1

    def test_neutral_a_stock_out_of_band(self):
        """A股观望 ±2%：±2.01 跑出区间判错。"""
        assert _judge('持有观望', 2.01) == 0
        assert _judge('持有观望', -2.01) == 0

    def test_neutral_hk_wider_band(self):
        """港股观望 ±3.5%：原 A股区间外的 ±2~3.5% 现判正确。"""
        assert _judge('持有观望', 2.5, 'hk_stock') == 1
        assert _judge('持有观望', -3.0, 'hk_stock') == 1
        assert _judge('持有观望', 3.5, 'hk_stock') == 1
        assert _judge('持有观望', -3.5, 'hk_stock') == 1

    def test_neutral_hk_out_of_wider_band(self):
        """港股观望 ±3.5%：±3.51 仍判错（不是无限放宽）。"""
        assert _judge('持有观望', 3.51, 'hk_stock') == 0
        assert _judge('持有观望', -3.51, 'hk_stock') == 0

    def test_directional_ratings_unaffected_by_market(self):
        """涨/跌档判定不受市场参数影响（差异化仅限观望档）。"""
        for market in ('a_stock', 'hk_stock'):
            assert _judge('推荐买入', 0.6, market) == 1
            assert _judge('推荐买入', -2.1, market) == 0
            assert _judge('建议减仓', -0.6, market) == 1
            assert _judge('建议减仓', 2.1, market) == 0

    def test_none_inputs_pass_through(self):
        """空值输入返回 None（不判定）。"""
        assert _judge('持有观望', None, 'hk_stock') is None
        assert _judge(None, 1.0, 'hk_stock') is None

    def test_overrides_only_touch_neutral(self):
        """HK_JUDGE_OVERRIDES 结构约束：只覆盖观望档、只含区间键。"""
        assert set(HK_JUDGE_OVERRIDES.keys()) == {'持有观望'}
        assert set(HK_JUDGE_OVERRIDES['持有观望'].keys()) == {'correct_low', 'correct_high'}
        # 覆盖值确实比 A股宽
        base = JUDGEMENT_MATRIX['持有观望']
        assert HK_JUDGE_OVERRIDES['持有观望']['correct_low'] < base['correct_low']
        assert HK_JUDGE_OVERRIDES['持有观望']['correct_high'] > base['correct_high']


class TestInterpretationMarketNote:
    """报告解读的港股口径声明。"""

    @pytest.fixture()
    def interp_db(self, tmp_path, monkeypatch):
        """最小 backtest_results 表 + monkeypatch DB 路径。"""
        db_path = str(tmp_path / 'interp_test.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            CREATE TABLE backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id INTEGER NOT NULL, rating_id INTEGER NOT NULL,
                market TEXT NOT NULL, rating_date DATE NOT NULL, rating TEXT NOT NULL,
                price_at_rating REAL, price_1d REAL, price_1w REAL, price_1m REAL,
                return_1d REAL, return_1w REAL, return_1m REAL,
                is_correct INTEGER, backtest_date TIMESTAMP,
                dynamic_end_date TEXT, dynamic_return REAL, dynamic_is_correct INTEGER,
                is_simulated INTEGER DEFAULT 0,
                bench_return_1d REAL, bench_return_1w REAL, bench_return_1m REAL,
                alpha_1d REAL, alpha_1w REAL, alpha_1m REAL, is_correct_alpha INTEGER,
                engine_tag TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE ratings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id INTEGER NOT NULL, rating_date DATE NOT NULL,
                rating TEXT, total_score REAL, engine_version TEXT
            )
        ''')
        # 两条可判定样本（观望档，1d 收益在 ±2% 内 → 正确）
        c.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            "price_at_rating, return_1d, is_correct, is_simulated, engine_tag) "
            "VALUES (1, 1, ?, '2026-08-10', '持有观望', 100.0, 1.0, 1, 0, 'v5')",
            ('hk_stock',),
        )
        c.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            "price_at_rating, return_1d, is_correct, is_simulated, engine_tag) "
            "VALUES (2, 2, ?, '2026-08-11', '持有观望', 100.0, -1.0, 1, 0, 'v5')",
            ('hk_stock',),
        )
        conn.commit()
        conn.close()

        import config
        import database.db_manager as dbm

        monkeypatch.setattr(config, 'DB_PATH', db_path)
        monkeypatch.setattr(dbm, 'DB_PATH', db_path)
        return db_path

    def test_hk_report_contains_band_note(self, interp_db):
        """港股市场报告解读包含 ±3.5% 口径声明。"""
        eng = BacktestEngine()
        report = eng.compute_market_report('hk_stock')
        assert report['market'] == 'hk_stock'
        joined = ''.join(report.get('interpretation_parts') or [])
        assert '±3.5%' in joined
        assert '021P' in joined

    def test_a_report_no_band_note(self, interp_db):
        """A股报告解读不包含港股口径声明。"""
        eng = BacktestEngine()
        report = eng.compute_market_report('a_stock')
        joined = ''.join(report.get('interpretation_parts') or [])
        assert '±3.5%' not in joined
