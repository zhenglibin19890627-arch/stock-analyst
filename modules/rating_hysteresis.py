"""评级变更迟滞（Rating Hysteresis）— 021AG。

背景（2026-08-22 诊断，backtest_results 全量 + ratings_history 实证）：
  - 67% 的评级变更间隔 ≤3 天；42% 的评级日分数距档位边界 ≤3 分——
    分数在档位线附近抖动导致评级高频换挡（whipsaw）；
  - 动态回测窗口（评级日→下次改评）中位仅 5 天，每次改评信号强度不足，
    动态准确率 46.6%（380 条）接近随机。

规则：
  分数跨过档位边界（原始映射已变档）还不够，必须再超出 MARGIN 分才换挡：
    升档：score ≥ 目标档 min + MARGIN
    降档：score < 原档 min − MARGIN
  多档跳变（如崩盘 85→55）天然满足迟滞条件，立即换挡不受拖延；
  仅"边界附近的相邻档抖动"被迟滞带吸收。

边界取数：与评分引擎同源——A股用 scoring_engine.RATING_THRESHOLDS（80/65/50/30），
港股热加载 config_weights.json hk_stock.rating_overrides（021R），不另设一套。

纯函数模块：无 DB/网络依赖，便于单元测试与历史回放仿真。
"""

import logging

from config import RATING_HYSTERESIS_ENABLED, RATING_HYSTERESIS_MARGIN
from modules.scoring_engine import RATING_THRESHOLDS, _load_hk_rating_overrides

logger = logging.getLogger(__name__)


def get_effective_thresholds(market: str) -> dict:
    """按市场取生效档位表（与 scoring_engine._map_rating 同源）。

    market: 'a_stock' / 'hk_stock'（宽容 'A'/'HK'）。
    """
    if market in ('hk_stock', 'HK'):
        return _load_hk_rating_overrides()
    return RATING_THRESHOLDS


def apply_hysteresis(score, raw_rating, prev_rating, market='a_stock', margin=None):
    """对"原始映射评级"施加迟滞，返回 (最终评级, 迟滞信息或None)。

    Args:
        score: 当日总分（float）
        raw_rating: 分数直接映射出的评级（中文5档）
        prev_rating: 上一次生效评级（中文5档，None=首次评级）
        market: 'a_stock' / 'hk_stock'
        margin: 迟滞带宽度（分），None=用 config 默认

    Returns:
        (final_rating, info)
        final_rating: 迟滞后最终评级
        info: 触发迟滞时为 dict(kept/raw/score/boundary/margin/direction)，否则 None
    """
    if margin is None:
        margin = RATING_HYSTERESIS_MARGIN
    if (
        not RATING_HYSTERESIS_ENABLED
        or score is None
        or raw_rating is None
        or prev_rating is None
        or raw_rating == prev_rating
    ):
        return raw_rating, None

    thresholds = get_effective_thresholds(market)
    if raw_rating not in thresholds or prev_rating not in thresholds:
        # 未知档位（历史遗留/异常）：不做迟滞，透传原始映射
        logger.warning(f'迟滞跳过：未知档位 raw={raw_rating} prev={prev_rating}')
        return raw_rating, None

    raw_rank = thresholds[raw_rating]['min']
    prev_rank = thresholds[prev_rating]['min']
    if raw_rank == prev_rank:
        # 同档不同写法（理论不可能，防御）
        return raw_rating, None

    if raw_rank > prev_rank:
        # 升档：须越过目标档下边界 + margin
        boundary = float(thresholds[raw_rating]['min'])
        if score >= boundary + margin:
            return raw_rating, None
        direction = 'upgrade'
    else:
        # 降档：须跌穿原档下边界 − margin
        boundary = float(thresholds[prev_rating]['min'])
        if score < boundary - margin:
            return raw_rating, None
        direction = 'downgrade'

    info = {
        'kept': prev_rating,
        'raw': raw_rating,
        'score': round(float(score), 1),
        'boundary': boundary,
        'margin': margin,
        'direction': direction,
    }
    logger.info(
        f'评级迟滞生效：score={score}（{direction}，边界{boundary}±{margin}），'
        f'维持「{prev_rating}」，原始映射「{raw_rating}」'
    )
    return prev_rating, info


def hysteresis_note(info) -> str:
    """迟滞信息 → 人类可读说明（用于报告/markdown）。"""
    if not info:
        return ''
    arrow = '升' if info['direction'] == 'upgrade' else '降'
    return (
        f"评级迟滞：今日分数 {info['score']} 已越过{arrow}档边界（{info['boundary']}）"
        f"但未达 ±{info['margin']:g} 分迟滞带，维持「{info['kept']}」"
        f"（原始映射：{info['raw']}）——评级仅在分数坚决越界时换挡，避免边界抖动"
    )
