"""
021AK：港股动态窗口波动率标定单元测试

覆盖：
1. _judge vol_scale 缩放：观望带宽放大、方向档阈值随动（含最小有效幅度保护）、
   边界容忍同比例缩放、s=1 等价不缩放、None 退回固定带
2. _sigma_daily：常数序列 σ=0、<5 点返回 None、已知波动序列的量级
3. _calc_dynamic_vol_scale 门控：非港股 None / 日期异常 None（不触库分支）
"""

import math

import pytest

from modules.backtest_engine import (
    HK_VOL_SCALE_MAX,
    HK_VOL_SCALE_MIN,
    BacktestEngine,
    _judge,
    _sigma_daily,
)


class TestJudgeVolScaleNeutral:
    """观望档：带宽按 vol_scale 放大。"""

    def test_scale_1_equivalent_to_none(self):
        assert _judge('持有观望', 3.4, 'hk_stock', neutral_borderline=True, vol_scale=1.0) == 1
        assert _judge('持有观望', 3.6, 'hk_stock', neutral_borderline=True, vol_scale=1.0) is None  # 容忍带
        assert _judge('持有观望', 4.6, 'hk_stock', neutral_borderline=True, vol_scale=1.0) == 0
        r1 = _judge('持有观望', 3.4, 'hk_stock', neutral_borderline=True)
        assert r1 == 1  # None 与 s=1 等价

    def test_scale_2_doubles_band(self):
        # 带宽 ±7%（3.5×2），容忍 ±2%
        assert _judge('持有观望', 6.5, 'hk_stock', neutral_borderline=True, vol_scale=2.0) == 1
        assert _judge('持有观望', 7.5, 'hk_stock', neutral_borderline=True, vol_scale=2.0) is None
        assert _judge('持有观望', 9.5, 'hk_stock', neutral_borderline=True, vol_scale=2.0) == 0

    def test_a_stock_unaffected_by_hk_scaling_argument(self):
        # A股固定带 ±2%+1% 容忍（vol_scale 不传入即固定）
        assert _judge('持有观望', 2.4, 'a_stock', neutral_borderline=True) is None
        assert _judge('持有观望', 3.2, 'a_stock', neutral_borderline=True) == 0


class TestJudgeVolScaleDirectional:
    """方向档：阈值随波动缩放 + 最小有效幅度保护。"""

    def test_up_threshold_scales(self):
        # 推荐买入：correct_min 0.5 → s=3 时 1.5；wrong_max -2 → -6
        assert _judge('推荐买入', 1.4, 'hk_stock', vol_scale=3.0) is None
        assert _judge('推荐买入', 1.6, 'hk_stock', vol_scale=3.0) == 1
        assert _judge('推荐买入', -5.0, 'hk_stock', vol_scale=3.0) is None
        assert _judge('推荐买入', -7.0, 'hk_stock', vol_scale=3.0) == 0

    def test_min_effective_threshold_guard(self):
        # s 极小时 up 档正确阈值不低于 0.3、down 档判错阈值不低于 0.5（对称保护）
        assert _judge('推荐买入', 0.3, 'hk_stock', vol_scale=0.1) == 1
        assert _judge('推荐买入', 0.2, 'hk_stock', vol_scale=0.1) is None
        assert _judge('建议减仓', -0.3, 'hk_stock', vol_scale=0.1) == 1
        assert _judge('建议减仓', 0.5, 'hk_stock', vol_scale=0.1) == 0
        assert _judge('建议减仓', 0.4, 'hk_stock', vol_scale=0.1) is None

    def test_down_threshold_scales(self):
        # 建议减仓：correct_max -0.5 → s=2 时 -1.0；wrong_min 2 → 4
        assert _judge('建议减仓', -0.9, 'hk_stock', vol_scale=2.0) is None
        assert _judge('建议减仓', -1.1, 'hk_stock', vol_scale=2.0) == 1
        assert _judge('建议减仓', 4.5, 'hk_stock', vol_scale=2.0) == 0


class TestSigmaDaily:
    def test_constant_series_zero(self):
        assert _sigma_daily([100.0] * 10) == 0.0

    def test_too_few_points_none(self):
        assert _sigma_daily([100.0, 101.0, 102.0]) is None
        assert _sigma_daily([]) is None

    def test_known_magnitude(self):
        # 交替 ±1%：σ ≈ 1%
        closes = [100.0]
        for i in range(10):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
        sigma = _sigma_daily(closes)
        assert sigma is not None and 0.9 < sigma < 1.1

    def test_none_entries_ignored(self):
        closes = [100.0] + [None] * 3 + [101.0, 102.0, 103.0, 104.0]
        assert _sigma_daily(closes) is not None  # None 被剔除后仍有 5 点


class TestVolScaleGating:
    """_calc_dynamic_vol_scale 门控（纯分支，不触库）。"""

    def test_a_stock_returns_none(self):
        assert BacktestEngine()._calc_dynamic_vol_scale(1, '2026-08-01', '2026-08-10', 'a_stock') is None

    def test_invalid_dates_return_none(self):
        assert BacktestEngine()._calc_dynamic_vol_scale(1, 'bad-date', '2026-08-10', 'hk_stock') is None
        assert BacktestEngine()._calc_dynamic_vol_scale(1, '2026-08-10', '2026-08-10', 'hk_stock') is None  # days<1

    def test_constants(self):
        assert HK_VOL_SCALE_MIN == 0.5
        assert HK_VOL_SCALE_MAX == 4.0


class TestClampMath:
    def test_sqrt_scaling_formula(self):
        # σ=3.5% 日波动、7 天窗口 → scale = 3.5×√7/3.5 = √7 ≈ 2.65（带内未夹）
        scale = 3.5 * math.sqrt(7) / 3.5
        assert HK_VOL_SCALE_MIN <= scale <= HK_VOL_SCALE_MAX
