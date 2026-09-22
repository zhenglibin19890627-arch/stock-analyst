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


def _tier_for_score(score: float, thresholds: dict) -> str | None:
    """分数 → 档位区间判定（与 _map_rating 同源阈值表，仅区间归属判定）。

    命中区间 [min, max] 返回档位名；不在任何区间（异常分数）返回 None。
    """
    for name, t in thresholds.items():
        try:
            if float(t['min']) <= score <= float(t['max']):
                return name
        except (KeyError, TypeError, ValueError):
            continue
    return None


def score_tier_mismatch_note(score, rating, market='a_stock', margin=None) -> str:
    """分数×档位失配的持续口径标注（021BS P1-1：迟滞保持态外层调和，B24 合规）。

    背景（021BS 第一轮审计 P1-1，300229 拓尔思）：021AG 迟滞的 markdown 说明
    只在「压制当日」生成；其后分数回到另一档区间但未越出迟滞带（保持态），
    或存量报告为旧口径时，报告对「分数区间 × 评级」失配无任何持续说明，
    用户按分数区间理解会得到另一档指令。

    本函数按「总分所处档位区间 × 当前评级」判定失配并生成说明文字：
      - 判定与 apply_hysteresis 同源（get_effective_thresholds：A股 80/65/50/30，
        港股 021R overrides 热加载），不重实现边界映射（R7/D4 合规）；
      - 只产出说明、不改评级——评级仍由 generate_advice 权威产出（B24 冻结），
        消费方为报告组装/展示层（advisor 因子构建器 / 读取路径消费方门控）。

    Returns:
        str: 失配时的人类可读说明；一致或无法判定（分数/评级缺失、档位未知、
        分数落在任何区间之外）返回 ''。文案不含裸字符 '<'（021BN 教训）。
    """
    if margin is None:
        margin = RATING_HYSTERESIS_MARGIN
    if score is None or not rating:
        return ''
    try:
        score = float(score)
    except (TypeError, ValueError):
        return ''
    thresholds = get_effective_thresholds(market)
    expected = _tier_for_score(score, thresholds)
    if expected is None or expected not in thresholds or rating not in thresholds:
        return ''
    if expected == rating:
        return ''
    band = thresholds[expected]
    cur = thresholds[rating]
    # 换挡触发线：升档=目标档 min+margin；降档=原档 min−margin（与 apply_hysteresis 同式）
    if float(band['min']) > float(cur['min']):
        switch_line = float(band['min']) + float(margin)
        switch_txt = f'分数持续站上 {switch_line:g} 分后，评级将在后续报告恢复「{expected}」'
    else:
        switch_line = float(cur['min']) - float(margin)
        switch_txt = f'分数持续跌破 {switch_line:g} 分后，评级将在后续报告换至「{expected}」'
    return (
        f'评级口径说明：总分 {score:.1f} 位于「{expected}」档分数区间'
        f'（{float(band["min"]):g}–{float(band["max"]):g} 分），当前评级「{rating}」与其不一致——'
        f'通常为评级迟滞保持态（021AG）：评级仅在分数坚决越过档位边界'
        f'（±{float(margin):g} 分迟滞带）时才换挡，避免边界抖动；{switch_txt}'
    )
