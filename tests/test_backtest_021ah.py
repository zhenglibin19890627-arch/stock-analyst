"""
021AH：动态回测观望档边界模糊容忍单元测试

覆盖：
1. 动态口径（neutral_borderline=True）：跑出带外 <1% 返回 None（不计入）
2. 固定周期口径（默认 False）：行为与旧版完全一致（跑出即判错）
3. 边界精确值：带外 1.0% 恰好不计入、1.01% 判错、带内仍判对
4. 港股差异化区间 + 模糊容忍叠加（±3.5% 带，3.5~4.5 不计入）
5. 涨/跌档不受 neutral_borderline 参数影响
6. NEUTRAL_BORDERLINE_TOL 常量约束
"""

import pytest

from modules.backtest_engine import NEUTRAL_BORDERLINE_TOL, _judge


class TestNeutralBorderlineDynamic:
    """动态口径：观望档跑出带外 <1% 不计入。"""

    def test_just_out_of_band_returns_none(self):
        assert _judge('持有观望', 2.5, 'a_stock', neutral_borderline=True) is None
        assert _judge('持有观望', -2.5, 'a_stock', neutral_borderline=True) is None

    def test_exact_tol_boundary_returns_none(self):
        # 带外恰好 1.0%（2.0+1.0=3.0）：仍属模糊，不计入
        assert _judge('持有观望', 3.0, 'a_stock', neutral_borderline=True) is None
        assert _judge('持有观望', -3.0, 'a_stock', neutral_borderline=True) is None

    def test_beyond_tol_returns_wrong(self):
        assert _judge('持有观望', 3.01, 'a_stock', neutral_borderline=True) == 0
        assert _judge('持有观望', -3.01, 'a_stock', neutral_borderline=True) == 0

    def test_inside_band_still_correct(self):
        assert _judge('持有观望', 1.5, 'a_stock', neutral_borderline=True) == 1
        assert _judge('持有观望', -1.5, 'a_stock', neutral_borderline=True) == 1
        assert _judge('持有观望', 2.0, 'a_stock', neutral_borderline=True) == 1


class TestFixedPeriodUnchanged:
    """固定周期口径（默认参数）：旧行为零变化。"""

    def test_default_keeps_old_behavior(self):
        assert _judge('持有观望', 2.5) == 0
        assert _judge('持有观望', -2.5) == 0
        assert _judge('持有观望', 2.5, 'a_stock') == 0

    def test_explicit_false_same_as_default(self):
        assert _judge('持有观望', 2.5, neutral_borderline=False) == 0


class TestHkBandPlusTolerance:
    """港股 ±3.5% 带 + 1% 容忍叠加。"""

    def test_hk_just_out_returns_none(self):
        assert _judge('持有观望', 3.8, 'hk_stock', neutral_borderline=True) is None
        assert _judge('持有观望', -4.0, 'hk_stock', neutral_borderline=True) is None

    def test_hk_beyond_tol_returns_wrong(self):
        assert _judge('持有观望', 4.6, 'hk_stock', neutral_borderline=True) == 0
        assert _judge('持有观望', -4.6, 'hk_stock', neutral_borderline=True) == 0

    def test_hk_inside_band_still_correct(self):
        assert _judge('持有观望', 3.0, 'hk_stock', neutral_borderline=True) == 1


class TestDirectionalUnaffected:
    """涨/跌档不受模糊容忍参数影响。"""

    def test_up_down_unchanged(self):
        for nb in (True, False):
            assert _judge('推荐买入', 0.6, 'a_stock', neutral_borderline=nb) == 1
            assert _judge('推荐买入', -2.1, 'a_stock', neutral_borderline=nb) == 0
            assert _judge('建议减仓', -0.6, 'a_stock', neutral_borderline=nb) == 1
            assert _judge('建议减仓', 2.1, 'a_stock', neutral_borderline=nb) == 0


class TestConstant:
    def test_tol_value(self):
        assert NEUTRAL_BORDERLINE_TOL == 1.0
