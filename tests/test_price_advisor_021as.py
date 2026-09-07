"""
021AS：减仓/清仓评级的建议减仓价格区间

覆盖：
1. 建议减仓：区间 [现价, 现价+0.6ATR]，比例 50%
2. 强烈建议卖出：退化 [现价, 现价]，比例 100%
3. 其他评级（买入/观望系）返回 None
4. 无 ATR 回退：上限=现价×1.03
5. 浮亏封顶：上限不超过成本价（不诱导等回本）
6. _gen_with_position 集成：结果含 reduce_range 字段
"""

import pytest

from modules import price_advisor as pa


class TestBuildReduceRange:
    def test_reduce_rating_basic(self):
        # close=10, ATR=0.5 → [10.0, 10.3], 50%
        r = pa._build_reduce_range(10.0, '建议减仓', 0.5, cost_price=9.0)
        assert r['low'] == pytest.approx(10.0, abs=0.01)
        assert r['high'] == pytest.approx(10.3, abs=0.01)
        assert r['pct'] == 50

    def test_strong_sell_degenerate(self):
        # 强烈建议卖出：[现价, 现价] 尽快离场
        r = pa._build_reduce_range(10.0, '强烈建议卖出', 0.5, cost_price=9.0)
        assert r['low'] == pytest.approx(10.0, abs=0.01)
        assert r['high'] == pytest.approx(10.0, abs=0.01)
        assert r['pct'] == 100

    def test_other_ratings_none(self):
        for rating in ('强烈推荐买入', '推荐买入', '持有观望'):
            assert pa._build_reduce_range(10.0, rating, 0.5, 9.0) is None

    def test_no_atr_fallback(self):
        r = pa._build_reduce_range(10.0, '建议减仓', None, cost_price=9.0)
        assert r['high'] == pytest.approx(10.3, abs=0.01)  # 10×1.03

    def test_underwater_cap_by_cost(self):
        # 浮亏（成本12 > 现价10）：min(10.3, max(10,12))=10.3 上限不受影响
        r = pa._build_reduce_range(10.0, '建议减仓', 0.5, cost_price=12.0)
        assert r['high'] == pytest.approx(10.3, abs=0.01)
        # 浮盈但成本略低于现价：cost=10.1 → min(10.3, max(10,10.1))=10.1 压低上限
        r2 = pa._build_reduce_range(10.0, '建议减仓', 0.5, cost_price=10.1)
        assert r2['high'] == pytest.approx(10.1, abs=0.01)
        # 下限永不高于上限
        assert r2['low'] <= r2['high']


class TestGenWithPositionIntegration:
    def test_result_contains_reduce_range(self):
        # 成本 9.0（浮盈）< 现价 10：min(10.3, max(10,9))=min(10.3,10)=10.0
        r = pa._gen_with_position(10.0, 9.0, '建议减仓', None, None, 0.5)
        assert 'reduce_range' in r
        assert r['reduce_range']['pct'] == 50
        assert r['reduce_range']['low'] == pytest.approx(10.0, abs=0.01)
        assert r['reduce_range']['high'] == pytest.approx(10.3, abs=0.01)

    def test_result_none_for_hold_rating(self):
        r = pa._gen_with_position(10.0, 9.0, '持有观望', None, None, 0.5)
        assert r['reduce_range'] is None
