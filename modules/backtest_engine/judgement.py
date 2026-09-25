"""有效性判定域（t3 拆包）：判定矩阵 + 市场差异化常量 + _judge/_sigma_daily。

划分依据：判定矩阵与 _judge 是回测唯一「评级+收益 → 1/0/None」语义核
（R20 A/H 独立；021P 港股 ±3.5%；021AH 边界模糊容忍；021AK 波动率标定），
纯常量+纯函数零 IO，被引擎/动态优化器/scripts/tests 多方消费，独立成域便于守护。
实现体为原文件 L34-139 逐字节搬移。
"""

# ============================================================
# 一、有效性判定矩阵（RATING-ALIGN-004 中文5档 + 历史兼容）
# ============================================================

JUDGEMENT_MATRIX = {
    '强烈推荐买入': {'direction': 'up', 'correct_min': 1.0, 'wrong_max': -3.0},
    '推荐买入': {'direction': 'up', 'correct_min': 0.5, 'wrong_max': -2.0},
    '持有观望': {'direction': 'neutral', 'correct_low': -2.0, 'correct_high': 2.0},
    '建议减仓': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
    '强烈建议卖出': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
}

# 021P：市场差异化判定——港股观望档区间 ±2% → ±3.5%。
# 实证（2026-08-19，backtest_results 全量）：港股无涨跌停/T+0/做空自由，
# 日常波幅显著大于A股，观望档 1 周落点仅 12% 留在 ±2% 区间（A股 34%）——
# 跑出区间 ≠ 方向判断错，是 A股标定的窗口装不下港股日常波动。
# 按港股日常波幅放宽至 ±3.5%（绝对收益与 alpha 口径统一适用）。
HK_JUDGE_OVERRIDES = {
    '持有观望': {'correct_low': -3.5, 'correct_high': 3.5},
}

# 021AH：观望档边界模糊容忍（仅动态口径）。
# 实证（2026-08-22 诊断）：动态窗口终点=下次改评日，而改评主要由价格跑出
# 区带触发——观望档 55% 的窗口以"刚跑出 ±2% 带"收尾，属口径结构性偏置
# 而非方向判断错误。跑出带外但幅度 < 1% 的样本判定为 None（模糊不计入），
# 同批数据实测 46.6% → 50.6%（观望档 47% → 55%）。固定周期口径无此
# 终点相关性，不启用。
NEUTRAL_BORDERLINE_TOL = 1.0

# 021AK：港股动态窗口波动率标定。
# 实证（2026-08-22，港股 70 条动态样本）：动态收益极度分散（P10 -16.6% / P90 +21.5%，
# 64% 落在 ±4.5% 带外），±3.5% 带按固定 1 周标定（021P）装不下 7~23 天动态窗口的
# 噪声——窗口越长、个股越活跃，"没走出观望"的带宽应按 σ·√t 放大。
# 标定：scale = σ(前20日日收益率) × √(窗口日历天数) / 3.5，夹在 [0.5, 4.0]；
# 实测 70 条重判 33% → 57%（51 条可判定，模糊样本按带宽同比例容忍不计入）。
# A股不启用（021AH 固定带+容忍口径已验证，且 A股动态收益分散度远低于港股）。
HK_VOL_SCALE_MIN, HK_VOL_SCALE_MAX = 0.5, 4.0


def _sigma_daily(closes):
    """日收益率标准差（%）。closes 为按时间升序的收盘价列表，<5 个点返回 None。"""
    vals = [c for c in closes if c]
    if len(vals) < 5:
        return None
    rets = []
    for i in range(1, len(vals)):
        if vals[i - 1] > 0:
            rets.append((vals[i] / vals[i - 1] - 1) * 100)
    if len(rets) < 4:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return var**0.5


def _judge(rating_norm, return_pct, market='a_stock', neutral_borderline=False, vol_scale=None):
    """根据归一化评级和收益率判定有效性。

    Args:
        rating_norm: 归一化评级（中文5档）
        return_pct: 收益率百分比（绝对收益或 alpha 超额）
        market: 'a_stock' / 'hk_stock'——021P 起港股观望档用 ±3.5% 差异化区间
        neutral_borderline: 021AH 观望档边界模糊容忍——跑出带外幅度
            < NEUTRAL_BORDERLINE_TOL 时返回 None（不计入统计）。仅动态口径使用。
        vol_scale: 021AK 港股动态窗口波动率标定系数——观望带宽/方向档阈值/
            边界容忍均按该系数缩放（噪声随 σ√t 放大，判定带同比例放大）；
            None=不缩放（A股及固定周期口径）。

    Returns:
        1 = 正确, 0 = 错误, None = 中性（无法明确判定）
    """
    if return_pct is None or rating_norm is None:
        return None
    config = JUDGEMENT_MATRIX.get(rating_norm)
    if not config:
        return None
    if market == 'hk_stock' and rating_norm in HK_JUDGE_OVERRIDES:
        config = {**config, **HK_JUDGE_OVERRIDES[rating_norm]}
    s = vol_scale if (vol_scale is not None and vol_scale > 0) else 1.0
    direction = config['direction']
    if direction == 'up':
        # 021AK：阈值随波动缩放，但保留最小有效幅度（±0.3%）防 s 过小时噪声判定
        correct_min = max(config['correct_min'] * s, 0.3)
        wrong_max = min(config['wrong_max'] * s, -0.5)
        if return_pct >= correct_min:
            return 1
        elif return_pct <= wrong_max:
            return 0
        return None
    elif direction == 'down':
        correct_max = min(config['correct_max'] * s, -0.3)
        wrong_min = max(config['wrong_min'] * s, 0.5)
        if return_pct <= correct_max:
            return 1
        elif return_pct >= wrong_min:
            return 0
        return None
    else:  # neutral
        low, high = config['correct_low'] * s, config['correct_high'] * s
        if low <= return_pct <= high:
            return 1
        tol = NEUTRAL_BORDERLINE_TOL * s if neutral_borderline else 0.0
        if neutral_borderline and abs(return_pct) <= max(abs(low), high) + tol:
            # 021AH：刚跑出观望带一点——方向说不清，不计入（容忍随带宽同比例缩放）
            return None
        return 0
