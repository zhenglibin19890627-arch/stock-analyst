"""dynamic_optimizer（021AI）核心纯函数单元测试。

覆盖：总分→档位映射与 scoring_engine 同序、网格完备性与约束、
迟滞重放（升档带内维持/带外换挡/降档）、窗口构建与分段汇总、
门槛常量约束。不触库（load_replay_data/search 属集成路径，由
scripts/run_dynamic_optimizer.py 真实数据报告覆盖）。
"""

from modules.dynamic_optimizer import (
    DIMS,
    GRID_STEP,
    MIN_JUDGED_WINDOWS,
    MIN_TEST_GAIN,
    MIN_V5_RATING_DAYS,
    WEIGHT_MAX,
    WEIGHT_MIN,
    _rating_from_score,
    build_windows,
    grid_candidates,
    replay_rating_sequence,
    score_windows,
)
from modules.rating_hysteresis import get_effective_thresholds


class TestRatingFromScore:
    def test_matches_thresholds_order(self):
        th = get_effective_thresholds('a_stock')
        assert _rating_from_score(85.0, th) == '强烈推荐买入'
        assert _rating_from_score(80.0, th) == '强烈推荐买入'
        assert _rating_from_score(79.9, th) == '推荐买入'
        assert _rating_from_score(65.0, th) == '推荐买入'
        assert _rating_from_score(64.9, th) == '持有观望'
        assert _rating_from_score(50.0, th) == '持有观望'
        assert _rating_from_score(49.9, th) == '建议减仓'
        assert _rating_from_score(10.0, th) == '强烈建议卖出'

    def test_hk_thresholds(self):
        th = get_effective_thresholds('hk_stock')
        assert _rating_from_score(69.0, th) == '持有观望'  # 021R：港股推荐买入 70 起
        assert _rating_from_score(70.0, th) == '推荐买入'


class TestGridCandidates:
    def test_all_sum_to_one(self):
        for cand in grid_candidates():
            assert abs(sum(cand.values()) - 1.0) < 1e-9

    def test_bounds_respected(self):
        for cand in grid_candidates():
            for d in DIMS:
                assert WEIGHT_MIN - 1e-9 <= cand[d] <= WEIGHT_MAX + 1e-9

    def test_step_alignment(self):
        for cand in grid_candidates():
            for d in DIMS:
                assert abs(cand[d] / GRID_STEP - round(cand[d] / GRID_STEP)) < 1e-9

    def test_contains_balanced_vector(self):
        cands = grid_candidates()
        assert {'kline': 0.25, 'fundamental': 0.25, 'capital_flow': 0.25, 'news': 0.25} in cands


def _days(scores_prices):
    """构造重放序列：[(四维分, 当日价格)]。"""
    return [
        {
            'date': f'2026-08-{i + 1:02d}',
            'price': p,
            'dims': {
                'kline': s[0],
                'fundamental': s[1],
                'capital_flow': s[2],
                'news': s[3],
            },
        }
        for i, (s, p) in enumerate(scores_prices)
    ]


_W = {'kline': 0.25, 'fundamental': 0.25, 'capital_flow': 0.25, 'news': 0.25}


class TestReplayHysteresis:
    def test_upgrade_within_band_kept(self):
        # 均分63→66：原始映射"推荐买入"（≥65），但 <68 迟滞带 → 维持观望
        days = _days([((70, 70, 70, 42), 10.0), ((70, 70, 70, 60), 10.1)])  # 63.0→66.0
        seq = replay_rating_sequence(days, _W, 'a_stock')
        assert seq[0]['rating'] == '持有观望'
        assert seq[1]['rating'] == '持有观望'  # 迟滞生效

    def test_upgrade_beyond_band_switches(self):
        days = _days([((70, 70, 70, 42), 10.0), ((80, 80, 80, 60), 10.1)])  # 63.0→75.0
        seq = replay_rating_sequence(days, _W, 'a_stock')
        assert seq[0]['rating'] == '持有观望'
        assert seq[1]['rating'] == '推荐买入'

    def test_downgrade_within_band_kept(self):
        # 均分72→63：原始映射观望，但 ≥62（65-3）→ 维持推荐买入
        days = _days([((75, 75, 75, 63), 10.0), ((65, 65, 65, 57), 10.1)])  # 72→63
        seq = replay_rating_sequence(days, _W, 'a_stock')
        assert seq[0]['rating'] == '推荐买入'
        assert seq[1]['rating'] == '推荐买入'

    def test_multi_grade_crash_switches(self):
        # 85→45：崩盘多档跳变天然满足，立即降档
        days = _days([((90, 90, 90, 70), 10.0), ((50, 50, 50, 30), 10.1)])
        seq = replay_rating_sequence(days, _W, 'a_stock')
        assert seq[0]['rating'] == '强烈推荐买入'
        assert seq[1]['rating'] == '建议减仓'


class TestWindows:
    def test_windows_and_split(self):
        # 三段评级：观望→改评推荐→改评观望；四个窗口全部可判定
        days = _days(
            [
                ((60, 60, 60, 60), 100.0),  # 60 观望
                ((60, 60, 60, 60), 100.0),  # 观望
                ((75, 75, 75, 75), 101.5),  # 75 → 改评"推荐买入"（>68）
                ((75, 75, 75, 75), 101.5),
                ((60, 60, 60, 60), 103.0),  # 60 <62 → 改评"持有观望"
            ]
        )
        data = {1: days}
        windows = build_windows(data, _W, 'a_stock')
        # 改评点：idx2（观望→推荐）、idx4（推荐→观望）
        # 窗口：[0,2] [1,2] [2,4] [3,4]（idx4 后无改评点不计）
        assert len(windows) == 4
        s = score_windows(windows)
        assert s['n_judged'] == 4
        # [0,2]/[1,2] 观望 +1.5% 判对；[2,4]/[3,4] 推荐 +1.48% 判对
        assert s['correct'] == 4

        s2 = score_windows(windows, '2026-08-02')
        assert s2['train']['n_judged'] == 2
        assert s2['test']['n_judged'] == 2

    def test_borderline_excluded(self):
        # 观望窗口收益 +2.4%（带外<1%）→ 021AH 判 None 不计入
        days = _days(
            [
                ((60, 60, 60, 60), 100.0),
                ((75, 75, 75, 75), 102.4),  # 改评推荐；窗口收益 +2.4%
                ((75, 75, 75, 75), 102.4),
            ]
        )
        windows = build_windows({1: days}, _W, 'a_stock')
        # 仅一个窗口（0→1），观望档收益 2.4% 判 None
        assert len(windows) == 1
        assert windows[0][1] == '持有观望'
        assert windows[0][3] is None


class TestGateConstants:
    def test_thresholds_sane(self):
        assert MIN_V5_RATING_DAYS == 300
        assert MIN_JUDGED_WINDOWS == 100
        assert MIN_TEST_GAIN == 0.03
        assert WEIGHT_MIN == 0.05 and WEIGHT_MAX == 0.50
