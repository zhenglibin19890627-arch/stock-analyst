# -*- coding: utf-8 -*-
"""020R-52：回测中心真实性治理测试
- 评级解读：引擎断点声明 / 二项检验显著性 / 周期趋势条件化
- 价格回测报告：真实样本（anchor_rating_date 非空）主口径与解读
"""

import pytest

from database import db_manager


def _base_report(**over):
    rep = {
        'total': 100,
        'date_range': '2026-07-16 ~ 2026-08-14',
        'correct_count': 63,
        'wrong_count': 37,
        'accuracy': 0.63,
        'period_accuracy': {
            '1d': {'accuracy': 0.65},
            '1w': {'accuracy': 0.49},
            '1m': {'accuracy': 0.38},
        },
        'dynamic_accuracy': 0.51,
        'dynamic_count': 80,
        'rating_stats': {},
        'engine_stats': {
            'v5': {'total': 1, 'accuracy': None, 'dyn_accuracy': None, 'avg_return_1m': None},
            '未标记(历史)': {
                'total': 99,
                'accuracy': 0.63,
                'dyn_accuracy': 0.5,
                'avg_return_1m': 2.7,
            },
        },
    }
    rep.update(over)
    return rep


class TestRatingInterpretation020R52:
    def _interp(self, report):
        from modules.backtest_engine import BacktestEngine

        BacktestEngine()._build_interpretation(report)
        return report['interpretation_parts'], report['interpretation_tones']

    def test_engine_composition_declares_v5_not_validated(self):
        """v5 样本 <30 时必须声明「当前评分规则尚未验证」"""
        parts, tones = self._interp(_base_report())
        assert any('引擎构成' in p and 'v5（当前规则）1 条' in p for p in parts)
        idx = next(i for i, p in enumerate(parts) if '尚未积累足够回测样本' in p)
        assert tones[idx] == 'bad'

    def test_v5_sufficient_sample_reported(self):
        """v5 样本 ≥30 且有准确率时给出 v5 口径结论"""
        rep = _base_report(
            engine_stats={
                'v5': {'total': 30, 'accuracy': 0.65, 'dyn_accuracy': 0.6, 'avg_return_1m': 3.0},
                '未标记(历史)': {'total': 70, 'accuracy': 0.62, 'dyn_accuracy': 0.5, 'avg_return_1m': 2.0},
            }
        )
        parts, tones = self._interp(rep)
        assert any('v5 规则样本 30 条' in p and '65%' in p for p in parts)
        assert not any('尚未积累足够回测样本' in p for p in parts)

    def test_significance_and_cost_note(self):
        """总体准确率行附二项检验 p 值与成本提示"""
        parts, _ = self._interp(_base_report())
        assert any('二项检验 p=' in p for p in parts)
        assert any('未扣除交易成本' in p for p in parts)

    def test_period_trend_conditional_rising(self):
        """准确率随持有期提升 → 「周期增强」而非「周期衰减」"""
        rep = _base_report(
            period_accuracy={
                '1d': {'accuracy': 0.40},
                '1w': {'accuracy': 0.50},
                '1m': {'accuracy': 0.60},
            }
        )
        parts, tones = self._interp(rep)
        idx = next(i for i, p in enumerate(parts) if p.startswith('周期增强'))
        assert tones[idx] == 'good'

    def test_period_trend_conditional_mixed(self):
        """无单调趋势 → 中性措辞，不再无条件说「递减」"""
        rep = _base_report(
            period_accuracy={
                '1d': {'accuracy': 0.50},
                '1w': {'accuracy': 0.60},
                '1m': {'accuracy': 0.55},
            }
        )
        parts, tones = self._interp(rep)
        idx = next(i for i, p in enumerate(parts) if p.startswith('周期维度：准确率随持有期无单调趋势'))
        assert tones[idx] == 'neutral'
        assert not any('周期衰减' in p for p in parts)


@pytest.fixture
def pb_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'pb52_test.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600000', 'a_stock', '测试股')"
    )
    conn.commit()
    conn.close()
    return db_manager


def _insert_price_rows(db):
    conn = db.get_connection()
    # 2 条真实评级回测点（anchor_rating_date 非空）+ 1 条历史重建点（anchor 为空）
    rows = [
        ('2026-08-01', '推荐买入', 0, 1, 1, 0, 0, '2026-08-01'),
        ('2026-08-02', '持有观望', 0, 1, 1, 1, 0, '2026-08-02'),
        ('2026-05-01', '推荐买入', 0, 1, 1, 1, 1, None),
    ]
    for backtest_date, rating, has_pos, t5b, t20b, t20t, t20s, anchor in rows:
        conn.execute(
            'INSERT INTO price_backtest_results '
            '(stock_id, backtest_date, rating, market, has_position, '
            't5_hit_buy_range, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, anchor_rating_date) '
            'VALUES (1, ?, ?, \'a_stock\', ?, ?, ?, ?, ?, ?)',
            (backtest_date, rating, has_pos, t5b, t20b, t20t, t20s, anchor),
        )
    conn.commit()
    conn.close()


class TestPriceBacktestRealSample020R52:
    def test_real_sample_primary(self, pb_db):
        """真实样本（anchor 非空）为主口径，命中率只统计真实样本"""
        _insert_price_rows(pb_db)

        from modules.price_backtest import compute_price_backtest_report

        rep = compute_price_backtest_report('a_stock')
        assert rep['total_points'] == 3
        assert rep['real_sample']['total'] == 2
        assert rep['real_hit_rates']['t20']['buy_range'] == 1.0
        assert rep['real_hit_rates']['t20']['target'] == 0.5
        assert rep['real_hit_rates']['t20']['stop_loss'] == 0.0
        # 解读首条声明真实样本与重建点构成
        assert '真实评级回测点' in rep['interpretation_parts'][0]
        assert '未来函数偏差' in rep['interpretation_parts'][0]
        # 全样本参照行存在
        assert any('全样本参照' in p for p in rep['interpretation_parts'])

    def test_fallback_warns_when_no_real_sample(self, pb_db):
        """无真实样本时退回全样本口径并前置警示"""
        conn = pb_db.get_connection()
        conn.execute(
            'INSERT INTO price_backtest_results '
            '(stock_id, backtest_date, rating, market, has_position, '
            't5_hit_buy_range, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, anchor_rating_date) '
            "VALUES (1, '2026-05-01', '推荐买入', 'a_stock', 0, 1, 1, 0, 0, NULL)"
        )
        conn.commit()
        conn.close()

        from modules.price_backtest import compute_price_backtest_report

        rep = compute_price_backtest_report('a_stock')
        assert rep['real_sample']['total'] == 0
        assert '历史重建点' in rep['interpretation_parts'][0]
