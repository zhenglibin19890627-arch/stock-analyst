"""模拟回测回填域（t3 拆包，M9-PREFILL）：技术面得分→评级映射 + K线复算。

划分依据：历史模拟回测的评分侧辅助（_SIM_RATING_THRESHOLDS 映射表、
_calc_technical_score_from_kline 复用 scoring_engine 技术面子项流程），
与生产引擎判定链解耦；data_adapter/scoring_engine 保持函数内懒加载不变。
实现体为原文件 L1664-1761 逐字节搬移。
"""

# ============================================================
# 四、四维综合评分模拟回测回填（M9-PREFILL）
# ============================================================

# 技术面得分 → 评级档位映射（任务书约定）
_SIM_RATING_THRESHOLDS = [
    (85, '强烈推荐买入'),
    (70, '推荐买入'),
    (50, '持有观望'),
    (30, '建议减仓'),
    (0, '强烈建议卖出'),
]


def _score_to_rating(score: float) -> str:
    """技术面得分映射为评级档位"""
    for threshold, rating in _SIM_RATING_THRESHOLDS:
        if score >= threshold:
            return rating
    return '强烈建议卖出'


def _calc_technical_score_from_kline(kline_slice: list[dict]) -> float:
    """基于K线切片计算技术面综合得分（0-100）

    复用 scoring_engine 中的技术面子项评分逻辑：
    均线(0.25) + 趋势(0.20) + 超买超卖(0.20) + 量价(0.10) + 量比(0.10) + 波动率(0.15)

    关键：传入截止日期的K线切片，无前瞻偏差。
    """
    from modules.data_adapter import (
        _calc_bollinger,
        _calc_kdj,
        _calc_ma,
        _calc_macd,
        _calc_rsi,
        _calc_volume_ratio,
    )
    from modules.data_contract import StockData
    from modules.scoring_engine import (
        TECHNICAL_SUBITEMS,
        adjust_subitem_weight,
        normalize_subitem_weights,
    )

    if not kline_slice or len(kline_slice) < 5:
        return 50.0  # 数据不足返回中性分

    closes = [float(r['close'] or 0) for r in kline_slice]
    volumes = [float(r['volume'] or 0) for r in kline_slice]
    latest = kline_slice[-1]

    # 计算技术指标
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    rsi_14 = _calc_rsi(closes, 14)
    boll_upper, _, boll_lower = _calc_bollinger(closes, 20)
    macd_dif, macd_dea = _calc_macd(closes)
    kdj_k = _calc_kdj(kline_slice)
    volume_ratio = _calc_volume_ratio(volumes)

    # 构建最小化 StockData（仅技术面字段）
    data = StockData(
        code='SIM',
        market='A',
        trade_date=str(latest.get('trade_date', '')).replace('-', ''),
        close=float(latest['close'] or 0),
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        kdj_k=kdj_k,
        rsi_14=rsi_14,
        volume=int(volumes[-1]) if volumes[-1] else None,
        volume_ratio=volume_ratio,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
    )

    # 复用 scoring_engine 的技术面评分流程
    weighted = [(si, adjust_subitem_weight(data, si)) for si in TECHNICAL_SUBITEMS]
    norm_weights = normalize_subitem_weights(weighted)

    from modules.scoring_engine import SCORING_FUNCTIONS

    dim_score = 0.0
    for si, eff_w in weighted:
        score_fn = SCORING_FUNCTIONS[si.key]
        sub_score, _ = score_fn(data)
        norm_w = norm_weights[si.key]
        if norm_w > 0:
            dim_score += sub_score * norm_w

    return round(dim_score, 1)
