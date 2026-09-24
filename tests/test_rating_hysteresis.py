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


class TestScoreTierMismatchNote:
    """021BS P1-1：分数×档位失配持续口径标注（合成迟滞保持态场景）。

    锁定契约：失配 → 非空说明（含两档位名/迟滞口径/换挡触发线，无裸 '<'）；
    一致或无法判定 → ''。与 apply_hysteresis 同源阈值，只产说明不改评级。
    """

    def _note(self, score, rating, market='a_stock'):
        from modules.rating_hysteresis import score_tier_mismatch_note

        return score_tier_mismatch_note(score, rating, market)

    def test_keep_state_mismatch_produces_note(self):
        # 021BS P1-1 实测形态（300229 拓尔思）：52.0 在持有观望区间，评级保持建议减仓
        note = self._note(52.0, '建议减仓')
        assert note
        assert '持有观望' in note and '建议减仓' in note
        assert '迟滞' in note
        assert '52.0' in note
        assert '<' not in note  # 021BN 教训：渲染文案禁止裸 '<'

    def test_upgrade_switch_line_is_boundary_plus_margin(self):
        # 50 + 3 = 53：说明必须给出与 apply_hysteresis 同式的升档触发线
        note = self._note(52.0, '建议减仓')
        assert '53' in note

    def test_downgrade_mismatch_produces_note(self):
        # 63.5 在建议减仓区间之外（观望带内）但评级已是更低的建议减仓 → 反向失配
        note = self._note(46.0, '持有观望')
        assert note and '持有观望' in note and '建议减仓' in note
        # 降档触发线 = 原档(持有观望) min − margin = 50 − 3 = 47
        assert '47' in note

    def test_consistent_returns_empty(self):
        assert self._note(58.0, '持有观望') == ''
        assert self._note(70.0, '推荐买入') == ''
        assert self._note(45.0, '建议减仓') == ''

    def test_hk_market_uses_overrides(self):
        # 港股 021R override：68 分属持有观望（≤69）、70 分属推荐买入（≥70）
        assert self._note(68.0, '推荐买入', 'hk_stock') != ''
        assert self._note(68.0, '持有观望', 'hk_stock') == ''
        assert self._note(72.0, '持有观望', 'hk_stock') != ''

    def test_unjudgeable_returns_empty(self):
        assert self._note(None, '持有观望') == ''
        assert self._note(52.0, '') == ''
        assert self._note(52.0, None) == ''
        assert self._note('abc', '持有观望') == ''
        # 未知评级档位
        assert self._note(52.0, 'B+') == ''

    def test_band_outside_all_tiers_returns_empty(self):
        # 低于最低档下边界（异常分数）：连续判定无档可归 → 无法判定返回 ''
        assert self._note(-5.0, '建议减仓') == ''

    def test_gap_scores_are_annotated(self):
        """021BW t4 F1 回归：档位表整数边界之间的表缝小数分数必须产出标注。

        旧实现闭区间 [min, max] 对 49.7（49–50 表缝）返回 None → 标注静默缺失
        （600519 实测）；连续判定（对齐 _map_rating）后必须命中档位。
        """
        gap_cases = [
            (49.7, '持有观望', '建议减仓'),     # 49–50 表缝（600519 实测形态）
            (49.99, '持有观望', '建议减仓'),
            (64.9, '建议减仓', '持有观望'),     # 64–65 表缝
            (79.9, '持有观望', '推荐买入'),     # 79–80 表缝
            (29.5, '持有观望', '强烈建议卖出'),  # 29–30 表缝
        ]
        for score, rating, expected_tier in gap_cases:
            note = self._note(score, rating)
            assert note, f'{score}×{rating} 未产出标注'
            assert expected_tier in note, f'{score} 未判入 {expected_tier}'
            assert rating in note
            assert '<' not in note

    def test_gap_score_tier_for_score_continuous(self):
        """_tier_for_score 连续口径与 _map_rating 全程一致（含表缝与边界值）。"""
        from modules.rating_hysteresis import _tier_for_score
        from modules.scoring_engine import RATING_THRESHOLDS, _map_rating

        for score in (0, 29.5, 30, 48.0, 49.0, 49.7, 49.99, 50, 52.0, 64.9, 65,
                      70, 79.9, 80, 95, 100):
            assert _tier_for_score(score, RATING_THRESHOLDS) == _map_rating(score)[0], score
        assert _tier_for_score(-5.0, RATING_THRESHOLDS) is None  # 异常分数不判定

    def test_gap_score_interval_display_uses_switch_boundary(self):
        """表缝分数的区间展示用连续换挡边界：49.7 显示 30–50，非自相矛盾的 30–49。"""
        note = self._note(49.7, '持有观望')
        assert '30–50 分' in note
        assert '30–49' not in note

    def test_in_table_score_interval_display_stable(self):
        """档表内分数维持既有区间展示（min–max）——防 stored×live 注记文本漂移。"""
        note = self._note(52.0, '建议减仓')
        assert '50–64 分' in note  # 持有观望档表原展示，与 021BS 存量注记逐字一致

    def test_hk_gap_score_annotated(self):
        """港股 021R override 表缝（69–70）：69.9 属持有观望 × 推荐买入 → 标注在场。"""
        note = self._note(69.9, '推荐买入', 'hk_stock')
        assert note and '持有观望' in note and '<' not in note

    def test_above_top_tier_annotated_as_top(self):
        """连续口径下超高分数归最高档（与 _map_rating 同款）：150×持有观望 → 失配标注。"""
        note = self._note(150.0, '持有观望')
        assert note and '强烈推荐买入' in note
