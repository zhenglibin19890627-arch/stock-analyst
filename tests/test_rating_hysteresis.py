"""评级变更迟滞单元测试（021AG）。

纯函数测试：升/降档×带内/带外、边界精确值、多档跳变、港股门槛、
首次评级、开关关闭、未知档位、说明文案。
"""
import importlib

import pytest


def _apply(score, raw, prev, market='a_stock', margin=None):
    from modules.rating_hysteresis import apply_hysteresis

    return apply_hysteresis(score, raw, prev, market=market, margin=margin)


class TestUpgrade:
    """升档：score ≥ 目标档 min + margin 才换挡"""

    def test_within_band_keeps_prev(self):
        # 观望→推荐买入：边界65，margin3 → 65~67.9 维持观望
        final, info = _apply(66.5, '推荐买入', '持有观望')
        assert final == '持有观望'
        assert info is not None and info['direction'] == 'upgrade'
        assert info['boundary'] == 65

    def test_beyond_band_switches(self):
        final, info = _apply(68.0, '推荐买入', '持有观望')
        assert final == '推荐买入' and info is None

    def test_exact_threshold_switches(self):
        # 恰好 65+3=68 → 换挡（≥ 含等于）
        final, _ = _apply(68.0, '推荐买入', '持有观望')
        assert final == '推荐买入'

    def test_just_below_threshold_keeps(self):
        final, info = _apply(67.9, '推荐买入', '持有观望')
        assert final == '持有观望' and info is not None

    def test_multi_grade_jump_switches_immediately(self):
        # 观望→强推：raw 要求 ≥80，天然 ≥80+3，立即换挡
        final, info = _apply(85.0, '强烈推荐买入', '持有观望')
        assert final == '强烈推荐买入' and info is None


class TestDowngrade:
    """降档：score < 原档 min − margin 才换挡"""

    def test_within_band_keeps_prev(self):
        # 推荐→观望：原档边界65，margin3 → 62~64 维持推荐
        final, info = _apply(63.5, '持有观望', '推荐买入')
        assert final == '推荐买入'
        assert info is not None and info['direction'] == 'downgrade'

    def test_beyond_band_switches(self):
        final, info = _apply(61.9, '持有观望', '推荐买入')
        assert final == '持有观望' and info is None

    def test_crash_multi_grade_not_delayed(self):
        # 推荐→建议减仓：raw 要求 ≤49，天然 <65-3，崩盘立即换挡
        final, info = _apply(45.0, '建议减仓', '推荐买入')
        assert final == '建议减仓' and info is None


class TestEdgeCases:
    def test_same_rating_passthrough(self):
        final, info = _apply(70.0, '推荐买入', '推荐买入')
        assert final == '推荐买入' and info is None

    def test_first_rating_no_hysteresis(self):
        final, info = _apply(65.5, '推荐买入', None)
        assert final == '推荐买入' and info is None

    def test_disabled_switch_passthrough(self):
        import config

        old = config.RATING_HYSTERESIS_ENABLED
        config.RATING_HYSTERESIS_ENABLED = False
        try:
            # 重新加载使模块读到新值
            import modules.rating_hysteresis as rh

            importlib.reload(rh)
            final, info = rh.apply_hysteresis(66.5, '推荐买入', '持有观望')
            assert final == '推荐买入' and info is None
        finally:
            config.RATING_HYSTERESIS_ENABLED = old
            importlib.reload(
                __import__('modules.rating_hysteresis', fromlist=['apply_hysteresis'])
            )

    def test_hk_thresholds_used(self):
        # 港股：推荐买入 min=70（021R override），margin3 → 70~72.9 维持观望
        final, info = _apply(71.5, '推荐买入', '持有观望', market='hk_stock')
        assert final == '持有观望'
        assert info is not None and info['boundary'] == 70

    def test_hk_beyond_band_switches(self):
        final, info = _apply(73.5, '推荐买入', '持有观望', market='hk_stock')
        assert final == '推荐买入' and info is None

    def test_custom_margin(self):
        # margin=6：观望→推荐需 ≥71
        final, info = _apply(69.0, '推荐买入', '持有观望', margin=6.0)
        assert final == '持有观望' and info is not None

    def test_unknown_prev_rating_passthrough(self):
        final, info = _apply(66.0, '推荐买入', 'B+')
        assert final == '推荐买入' and info is None

    def test_note_text_contains_key_info(self):
        from modules.rating_hysteresis import hysteresis_note

        _, info = _apply(66.5, '推荐买入', '持有观望')
        note = hysteresis_note(info)
        assert '持有观望' in note and '推荐买入' in note and '66.5' in note

    def test_note_empty_when_none(self):
        from modules.rating_hysteresis import hysteresis_note

        assert hysteresis_note(None) == ''


class TestThresholdsSource:
    def test_a_stock_uses_global_thresholds(self):
        from modules.rating_hysteresis import get_effective_thresholds
        from modules.scoring_engine import RATING_THRESHOLDS

        assert get_effective_thresholds('a_stock') is RATING_THRESHOLDS

    def test_hk_uses_overrides(self):
        from modules.rating_hysteresis import get_effective_thresholds

        th = get_effective_thresholds('hk_stock')
        assert th['推荐买入']['min'] == 70  # 021R override
