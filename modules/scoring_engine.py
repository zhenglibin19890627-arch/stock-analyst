"""
v5.0 四维评分引擎原型 (Scoring Engine Prototype)

基于标准数据契约(StockData)的四维量化评分引擎。
实现 v5.0 报告第3章"维度内子权重调整"机制（Q03决策），
当字段缺失时按降级规则自动调整子项权重并重新归一化。

输入：StockData 对象（由数据采集层/适配器层/MockDataProvider 提供）
输出：AnalysisResult 对象（v5.0标准分析结果契约）

与旧版 analysis_engine.py 的区别：
1. 完全消费 StockData 契约，与数据库解耦（可用 MockDataProvider 直接测试）
2. 实现子项级权重调整（A类归零/B类降权/C类默认填充），而非维度级粗粒度归零
3. 输出符合 v5.0 AnalysisResult 契约

降级机制（Q03 核心创新）：
  每个维度由若干子项组成，各子项有预设权重。
  当某子项依赖的数据字段缺失时，按三类规则调整其权重：
    A) 权重归零型 — 子项依赖字段全缺失，权重置0，剩余子项按比例补足
    B) 权重降低型 — 子项依赖字段全缺失，权重降低指定比例（默认30%）
    C) 默认值填充型 — 用中性默认值填充缺失字段，权重保持不变
  调整后在维度内重新归一化所有子项权重至总和=1.0。

业务歧义决策（第6章 D01-D03）：
  D01: news_sentiment 中性填充值 = 0.0
  D02: main_net_inflow 中性填充值 = 0.0（万元）
  D03: AnalysisResult.sentiment_score 对应消息面维度得分（命名沿用契约原设计）
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from modules.data_contract import AnalysisResult, StockData

logger = logging.getLogger(__name__)

# 北京时间
_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# D01/D02 中性填充值（第6章决策）
NEUTRAL_SENTIMENT = 0.0  # news_sentiment 中性值
NEUTRAL_INFLOW = 0.0  # main_net_inflow 中性值（万元）
DEFAULT_VOLUME_RATIO = 1.0  # volume_ratio 默认值（Q04）

# 降权比例（B类，对应 ma60/macd 规则中的"降权30%"）
REDUCE_RATIO = 0.30

# 评级映射（与 config_weights.json rating_mapping 一致）
# RATING-ALIGN-004：评级档位对齐需求 v1.1 §2.3.1
# 中文5档，边界 80/65/50/30，key 与 label 统一为中文
# B18-Hotfix: strong_buy 85→80, buy 70→65（监理批准 2026-07-25）
RATING_THRESHOLDS = {
    '强烈推荐买入': {'min': 80, 'max': 100},
    '推荐买入': {'min': 65, 'max': 79},
    '持有观望': {'min': 50, 'max': 64},
    '建议减仓': {'min': 30, 'max': 49},
    '强烈建议卖出': {'min': 0, 'max': 29},
}

# 历史 A/B+/B/C/D → 新中文5档 字符串映射（有分数时优先用分数精确映射）
RATING_LEGACY_MAP = {
    'A': '强烈推荐买入',
    'B+': '推荐买入',
    'B': '持有观望',
    'C': '建议减仓',  # ISSUE-1 修正：原'持有观望'偏移一档
    'D': '强烈建议卖出',  # ISSUE-1 修正：原'建议减仓'偏移一档
}


def normalize_rating(rating_str, total_score=None):
    """将任意评级（新旧）统一映射到新中文5档。

    RATING-ALIGN-004 历史兼容：历史 ratings_history 中 rating 为 A/B+/B/C/D，
    本函数将其映射到新中文5档。

    B12-T4 修复：旧格式评级与 total_score 矛盾时，优先使用评级字符串映射。
    （旧引擎存在 rating 与 score 不一致的 bug，score 不可信）
    """
    if rating_str is None:
        return None
    # 新档位直接返回
    if rating_str in RATING_THRESHOLDS:
        return rating_str
    # 旧格式：检查 rating 与 score 是否一致
    if rating_str in RATING_LEGACY_MAP:
        legacy_mapped = RATING_LEGACY_MAP[rating_str]
        if total_score is not None:
            try:
                score_mapped, _ = _map_rating(float(total_score))
                if score_mapped == legacy_mapped:
                    # 一致：使用 score 精确映射（原逻辑）
                    return score_mapped
                else:
                    # B12-T4: 矛盾时优先使用评级字符串映射
                    logger.warning(
                        f'normalize_rating 矛盾: rating={rating_str}->{legacy_mapped}, '
                        f'score={total_score}->{score_mapped}, 采用评级映射={legacy_mapped}'
                    )
                    return legacy_mapped
            except (ValueError, TypeError):
                pass
        return legacy_mapped
    # 未知格式：有 score 时按 score 映射
    if total_score is not None:
        try:
            grade, _ = _map_rating(float(total_score))
            return grade
        except (ValueError, TypeError):
            pass
    return rating_str


# 维度权重配置文件路径
_WEIGHTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config_weights.json'
)

# 默认维度权重（A股，JSON文件不存在时回退）
_DEFAULT_DIM_WEIGHTS = {
    'A': {'kline': 0.25, 'fundamental': 0.25, 'capital_flow': 0.40, 'news': 0.10},
    'HK': {'kline': 0.30, 'fundamental': 0.30, 'capital_flow': 0.30, 'news': 0.10},
}


# ================================================================
# 一、子项定义（SubItem）与四维子项注册表
# ================================================================


@dataclass
class SubItem:
    """维度内的一个评分子项

    Attributes:
        name: 子项中文名（如"均线"）
        key: 子项英文键（如"ma"）
        fields: 依赖的 StockData 字段列表
        base_weight: 维度内基础权重（0-1，所有子项之和=1.0）
        degradation: 降级类型 "zero"(A) / "reduce"(B) / "keep_default"(C)
        reduce_ratio: B类降权比例（默认0.30）
        default_fills: C类默认填充值 {字段名: 值}
    """

    name: str
    key: str
    fields: list[str]
    base_weight: float
    degradation: str  # "zero" / "reduce" / "keep_default"
    reduce_ratio: float = REDUCE_RATIO
    default_fills: dict[str, float] = field(default_factory=dict)


# --- 技术面 6 子项 ---
TECHNICAL_SUBITEMS: list[SubItem] = [
    SubItem('均线', 'ma', ['ma5', 'ma10', 'ma20'], 0.25, 'zero'),
    SubItem('趋势', 'trend', ['ma60', 'macd_dif', 'macd_dea'], 0.20, 'reduce'),
    SubItem('超买超卖', 'obos', ['kdj_k', 'rsi_14'], 0.20, 'reduce'),
    SubItem('量价分析', 'vol_price', ['volume'], 0.10, 'zero'),
    SubItem(
        '量比',
        'vol_ratio',
        ['volume_ratio'],
        0.10,
        'keep_default',
        default_fills={'volume_ratio': DEFAULT_VOLUME_RATIO},
    ),
    SubItem('波动率', 'volatility', ['boll_upper', 'boll_lower'], 0.15, 'reduce'),
]

# --- 基本面 5 子项 ---
FUNDAMENTAL_SUBITEMS: list[SubItem] = [
    SubItem('估值', 'valuation', ['pe_ttm', 'pb'], 0.25, 'reduce'),
    SubItem('盈利能力', 'profitability', ['roe', 'gross_margin'], 0.30, 'reduce'),
    SubItem('成长性', 'growth', ['revenue_yoy', 'net_profit_yoy'], 0.25, 'reduce'),
    SubItem('现金流质量', 'cashflow', ['ocf_to_profit'], 0.10, 'zero'),
    SubItem('财务健康度', 'fin_health', ['debt_to_asset', 'current_ratio'], 0.10, 'reduce'),
]

# --- 消息面 2 子项 ---
NEWS_SUBITEMS: list[SubItem] = [
    SubItem(
        '情绪',
        'sentiment',
        ['news_sentiment'],
        0.70,
        'keep_default',
        default_fills={'news_sentiment': NEUTRAL_SENTIMENT},
    ),
    SubItem('股东行为', 'holder', ['holder_increase'], 0.30, 'zero'),
]

# --- 资金面 3 子项 ---
# B26：北向资金数据源自2024-08-16起停更（港交所政策变更），降权0.30→0.10
# 释放权重按主力:两融=45:25比例分配给主力(+0.10)和两融(+0.10)
CAPITAL_SUBITEMS: list[SubItem] = [
    SubItem(
        '主力资金',
        'main_capital',
        ['main_net_inflow'],
        0.55,
        'zero',  # 019T T2: C类(keep_default 填充) → A类(归零)；缺失=无信息，不占权重
    ),
    SubItem('互联互通', 'north_capital', ['north_net_buy'], 0.10, 'reduce'),
    SubItem('杠杆资金', 'margin_capital', ['margin_balance_chg'], 0.35, 'reduce'),
]


# ================================================================
# 二、维度内子权重调整引擎（Q03 核心机制）
# ================================================================


def _field_present(data: StockData, field_name: str) -> bool:
    """检查字段是否有值（非 None）"""
    return getattr(data, field_name, None) is not None


def _subitem_completeness(data: StockData, subitem: SubItem) -> tuple[int, int, float]:
    """计算子项的字段完整度

    Returns: (present_count, total_count, completeness_ratio)
    """
    present = sum(1 for f in subitem.fields if _field_present(data, f))
    total = len(subitem.fields)
    ratio = present / total if total > 0 else 0.0
    return present, total, ratio


def adjust_subitem_weight(data: StockData, subitem: SubItem) -> float:
    """根据字段缺失情况调整子项权重（Q03 三类降级规则）

    A类(归零型): 字段全缺失 → 权重=0；有字段 → 保持原权重
    B类(降权型): 字段全缺失 → 权重×(1-reduce_ratio)；有字段 → 保持原权重
    C类(填充型): 始终保持原权重（缺失字段用默认值填充）
    """
    present, total, _ = _subitem_completeness(data, subitem)

    if subitem.degradation == 'zero':
        # A类：字段全缺失则权重归零
        if present == 0:
            return 0.0
        return subitem.base_weight

    elif subitem.degradation == 'reduce':
        # B类：字段全缺失则降权
        if present == 0:
            return subitem.base_weight * (1.0 - subitem.reduce_ratio)
        return subitem.base_weight

    elif subitem.degradation == 'keep_default':
        # C类：始终保持权重（缺失字段由评分函数填充默认值）
        return subitem.base_weight

    return subitem.base_weight


def normalize_subitem_weights(weighted: list[tuple[SubItem, float]]) -> dict[str, float]:
    """维度内子项权重归一化至总和=1.0

    Args:
        weighted: [(SubItem, effective_weight), ...]
    Returns:
        {subitem_key: normalized_weight}
    """
    total = sum(w for _, w in weighted)
    if total <= 0:
        # 所有权重为0（维度不可用），返回全0
        return {si.key: 0.0 for si, _ in weighted}
    return {si.key: round(w / total, 4) for si, w in weighted}


# ================================================================
# 三、各子项评分函数（0-100分）
# ================================================================


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """限制分数在 [lo, hi] 范围内"""
    return max(lo, min(hi, value))


# --- 技术面子项评分 ---


def score_ma(data: StockData) -> tuple[float, dict]:
    """均线子项评分：金叉/死叉 + 价格与均线位置"""
    ma5, ma10, ma20 = data.ma5, data.ma10, data.ma20
    close = data.close
    available = {k: v for k, v in [('ma5', ma5), ('ma10', ma10), ('ma20', ma20)] if v is not None}

    if not available:
        return 50.0, {'note': '均线数据全缺失，返回中性分'}

    score = 50.0
    detail = {}

    if 'ma5' in available and 'ma20' in available:
        if ma5 > ma20:
            deviation = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            score = _clamp(85.0 + deviation * 1.5)
            detail['cross'] = f'金叉(MA5={ma5:.2f} > MA20={ma20:.2f})'
        else:
            deviation = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0
            score = _clamp(15.0 - deviation * 1.0)
            detail['cross'] = f'死叉(MA5={ma5:.2f} < MA20={ma20:.2f})'

    # 价格在均线之上加分
    for name, ma_val in available.items():
        if close > ma_val:
            score += 4.0
            detail[f'{name}_position'] = '价格在均线之上'
        else:
            score -= 4.0
            detail[f'{name}_position'] = '价格在均线之下'

    return _clamp(score), detail


def score_trend(data: StockData) -> tuple[float, dict]:
    """趋势子项评分：MACD + MA60长期趋势"""
    ma60, macd_dif, macd_dea = data.ma60, data.macd_dif, data.macd_dea
    close = data.close
    available = {
        k: v
        for k, v in [('ma60', ma60), ('macd_dif', macd_dif), ('macd_dea', macd_dea)]
        if v is not None
    }

    if not available:
        return 50.0, {'note': '趋势数据全缺失，返回中性分'}

    score = 50.0
    detail = {}

    # MACD 判断
    if 'macd_dif' in available and 'macd_dea' in available:
        if macd_dif > macd_dea:
            hist = macd_dif - macd_dea
            score = _clamp(82.0 + min(20.0, hist * 40))
            detail['macd'] = f'DIF>DEA 多头({hist:.4f})'
        else:
            hist = macd_dea - macd_dif
            score = _clamp(10.0 - min(15.0, hist * 30))
            detail['macd'] = f'DIF<DEA 空头({-hist:.4f})'
    elif 'macd_dif' in available:
        if macd_dif > 0:
            score += 8.0
            detail['macd_dif'] = f'DIF>0({macd_dif:.4f})'
        else:
            score -= 8.0
            detail['macd_dif'] = f'DIF<0({macd_dif:.4f})'

    # MA60 长期趋势
    if 'ma60' in available and ma60 > 0:
        if close > ma60:
            above_pct = (close - ma60) / ma60 * 100
            score += min(18.0, above_pct * 0.8)
            detail['ma60'] = f'价格在60日均线上方(+{above_pct:.1f}%)'
        else:
            below_pct = (ma60 - close) / ma60 * 100
            score -= min(18.0, below_pct * 0.8)
            detail['ma60'] = f'价格在60日均线下方(-{below_pct:.1f}%)'

    return _clamp(score), detail


def score_obos(data: StockData) -> tuple[float, dict]:
    """超买超卖子项评分：RSI + KDJ K值"""
    rsi = data.rsi_14
    kdj_k = data.kdj_k
    scores = []
    detail = {}

    if rsi is not None:
        if rsi > 80:
            rsi_s = _clamp(20.0 - (rsi - 80) * 1.5)  # 严重超买
            detail['rsi'] = f'严重超买({rsi:.1f})'
        elif rsi > 70:
            rsi_s = _clamp(50.0 - (rsi - 70) * 2.0)  # 超买
            detail['rsi'] = f'超买({rsi:.1f})'
        elif 45 <= rsi <= 65:
            # 健康区域，55为最佳
            rsi_s = _clamp(87.0 + max(0, 10 - abs(rsi - 55) * 2))
            detail['rsi'] = f'健康({rsi:.1f})'
        elif 30 <= rsi < 45 or 65 < rsi <= 70:
            rsi_s = 50.0  # 中性偏强
            detail['rsi'] = f'中性({rsi:.1f})'
        elif rsi < 30:
            rsi_s = _clamp(30.0 + (30 - rsi) * 0.5)  # 超卖有反弹机会
            detail['rsi'] = f'超卖({rsi:.1f})'
        else:
            rsi_s = 50.0
            detail['rsi'] = f'中性({rsi:.1f})'
        scores.append(rsi_s)

    if kdj_k is not None:
        if kdj_k > 80:
            kdj_s = 35.0  # 超买
            detail['kdj_k'] = f'超买({kdj_k:.1f})'
        elif kdj_k < 20:
            kdj_s = 45.0  # 超卖
            detail['kdj_k'] = f'超卖({kdj_k:.1f})'
        elif 40 <= kdj_k <= 60:
            kdj_s = 82.0  # 健康区域
            detail['kdj_k'] = f'健康({kdj_k:.1f})'
        elif 20 <= kdj_k < 40:
            kdj_s = 45.0  # 偏弱
            detail['kdj_k'] = f'偏弱({kdj_k:.1f})'
        else:
            kdj_s = 75.0
            detail['kdj_k'] = f'中性({kdj_k:.1f})'
        scores.append(kdj_s)

    if not scores:
        return 50.0, {'note': '超买超卖数据全缺失，返回中性分'}

    return _clamp(sum(scores) / len(scores)), detail


def score_vol_price(data: StockData) -> tuple[float, dict]:
    """量价分析子项评分：成交量绝对值映射"""
    vol = data.volume
    if vol is None:
        return 50.0, {'note': '成交量缺失，返回中性分'}

    detail = {'volume': f'{vol:,}股'}
    # 成交量映射：活跃度评分
    # 100万以下低活跃，500万-2000万正常，2000万以上活跃
    if vol >= 20_000_000:
        return 88.0, detail
    elif vol >= 5_000_000:
        return 72.0, detail
    elif vol >= 1_000_000:
        return 60.0, detail
    elif vol >= 100_000:
        return 40.0, detail
    elif vol > 0:
        return 35.0, detail  # 极低成交量
    else:
        return 30.0, detail  # 零成交量，异常


def score_vol_ratio(data: StockData) -> tuple[float, dict]:
    """量比子项评分：volume_ratio 映射（缺失时用默认值1.0填充，Q04）"""
    vr = data.volume_ratio
    if vr is None:
        vr = DEFAULT_VOLUME_RATIO  # Q04: 分析适配默认值

    detail = {'volume_ratio': f'{vr:.2f}'}

    if vr > 3.0:
        return 50.0, {**detail, 'note': '异常放量，需关注'}
    elif vr > 2.0:
        return 70.0, {**detail, 'note': '显著放量'}
    elif vr > 1.3:
        return 80.0, {**detail, 'note': '温和放量'}  # O2-B: 75→80
    elif vr >= 0.8:
        return 65.0, {**detail, 'note': '正常量能'}
    elif vr >= 0.5:
        return 55.0, {**detail, 'note': '量能偏弱'}
    else:
        return 40.0, {**detail, 'note': '缩量明显'}


def score_volatility(data: StockData) -> tuple[float, dict]:
    """波动率子项评分：布林带位置"""
    boll_upper, boll_lower = data.boll_upper, data.boll_lower
    close = data.close

    if boll_upper is None or boll_lower is None:
        return 50.0, {'note': '布林带数据缺失，返回中性分'}

    band_width = boll_upper - boll_lower
    detail = {'boll_upper': f'{boll_upper:.2f}', 'boll_lower': f'{boll_lower:.2f}'}

    if band_width <= 0:
        return 50.0, {**detail, 'note': '布林带带宽为零'}

    # 位置百分比：0%=触及下轨，100%=触及上轨
    position = (close - boll_lower) / band_width * 100
    detail['position'] = f'{position:.1f}%'

    if 40 <= position <= 70:
        return 88.0, detail  # 中轨偏上，健康
    elif 20 <= position < 40:
        return 65.0, detail  # 偏弱但有支撑
    elif 70 < position <= 85:
        return 65.0, detail  # 偏强但需警惕
    elif position > 85:
        return 55.0, detail  # 触及上轨，回调风险
    elif 10 <= position < 20:
        return 50.0, detail  # 接近下轨
    else:
        return 30.0, detail  # 触及下轨


# --- 基本面子项评分 ---


def score_valuation(data: StockData) -> tuple[float, dict]:
    """估值子项评分：PE + PB（低估值得分高）"""
    pe, pb = data.pe_ttm, data.pb
    scores = []
    detail = {}

    if pe is not None:
        if pe <= 0:
            pe_s = 20.0  # 亏损
            detail['pe'] = f'{pe:.2f}(亏损/负值)'
        elif pe <= 15:
            pe_s = 97.0  # 低估
            detail['pe'] = f'{pe:.2f}(低估)'
        elif pe <= 25:
            pe_s = 80.0  # 合理
            detail['pe'] = f'{pe:.2f}(合理)'
        elif pe <= 40:
            pe_s = 60.0  # 偏高
            detail['pe'] = f'{pe:.2f}(偏高)'
        elif pe <= 60:
            pe_s = 35.0  # 高估
            detail['pe'] = f'{pe:.2f}(高估)'
        else:
            pe_s = 15.0  # 严重高估
            detail['pe'] = f'{pe:.2f}(严重高估)'
        scores.append(pe_s)

    if pb is not None:
        if pb <= 0:
            pb_s = 20.0
            detail['pb'] = f'{pb:.2f}(负值)'
        elif pb <= 1:
            pb_s = 88.0  # 破净
            detail['pb'] = f'{pb:.2f}(破净)'
        elif pb <= 2:
            pb_s = 75.0
            detail['pb'] = f'{pb:.2f}(合理偏低)'
        elif pb <= 4:
            pb_s = 60.0
            detail['pb'] = f'{pb:.2f}(合理)'
        elif pb <= 6:
            pb_s = 40.0
            detail['pb'] = f'{pb:.2f}(偏高)'
        else:
            pb_s = 20.0
            detail['pb'] = f'{pb:.2f}(高估)'
        scores.append(pb_s)

    if not scores:
        return 50.0, {'note': '估值数据全缺失，返回中性分'}

    return _clamp(sum(scores) / len(scores)), detail


def score_profitability(data: StockData) -> tuple[float, dict]:
    """盈利能力子项评分：ROE + 毛利率"""
    roe, gross_margin = data.roe, data.gross_margin
    scores = []
    detail = {}

    if roe is not None:
        if roe >= 20:
            roe_s = 98.0
            detail['roe'] = f'{roe:.2f}%(优秀)'
        elif roe >= 15:
            roe_s = 95.0
            detail['roe'] = f'{roe:.2f}%(良好)'
        elif roe >= 10:
            roe_s = 80.0
            detail['roe'] = f'{roe:.2f}%(一般)'
        elif roe >= 5:
            roe_s = 62.0
            detail['roe'] = f'{roe:.2f}%(偏低)'
        elif roe >= 0:
            roe_s = 25.0
            detail['roe'] = f'{roe:.2f}%(较差)'
        else:
            roe_s = 10.0
            detail['roe'] = f'{roe:.2f}%(亏损)'
        scores.append(roe_s)

    if gross_margin is not None:
        if gross_margin >= 50:
            gm_s = 92.0
            detail['gross_margin'] = f'{gross_margin:.2f}%(高)'
        elif gross_margin >= 30:
            gm_s = 76.0
            detail['gross_margin'] = f'{gross_margin:.2f}%(中高)'
        elif gross_margin >= 15:
            gm_s = 55.0
            detail['gross_margin'] = f'{gross_margin:.2f}%(中)'
        elif gross_margin >= 0:
            gm_s = 35.0
            detail['gross_margin'] = f'{gross_margin:.2f}%(低)'
        else:
            gm_s = 15.0
            detail['gross_margin'] = f'{gross_margin:.2f}%(负值)'
        scores.append(gm_s)

    if not scores:
        return 50.0, {'note': '盈利能力数据全缺失，返回中性分'}

    return _clamp(sum(scores) / len(scores)), detail


def score_growth(data: StockData) -> tuple[float, dict]:
    """成长性子项评分：营收增长 + 净利润增长"""
    rev_yoy, np_yoy = data.revenue_yoy, data.net_profit_yoy
    scores = []
    detail = {}

    if rev_yoy is not None:
        if rev_yoy >= 30:
            rev_s = 96.0
        elif rev_yoy >= 20:
            rev_s = 95.0
        elif rev_yoy >= 10:
            rev_s = 80.0
        elif rev_yoy >= 0:
            rev_s = 62.0
        elif rev_yoy >= -10:
            rev_s = 30.0
        else:
            rev_s = 15.0
        detail['revenue_yoy'] = f'{rev_yoy:.2f}%'
        scores.append(rev_s)

    if np_yoy is not None:
        if np_yoy >= 50:
            np_s = 96.0
        elif np_yoy >= 30:
            np_s = 85.0
        elif np_yoy >= 15:
            np_s = 72.0
        elif np_yoy >= 0:
            np_s = 52.0
        elif np_yoy >= -20:
            np_s = 30.0
        else:
            np_s = 12.0
        detail['net_profit_yoy'] = f'{np_yoy:.2f}%'
        scores.append(np_s)

    if not scores:
        return 50.0, {'note': '成长性数据全缺失，返回中性分'}

    return _clamp(sum(scores) / len(scores)), detail


def score_cashflow(data: StockData) -> tuple[float, dict]:
    """现金流质量子项评分：经营现金流/净利润"""
    ocf = data.ocf_to_profit
    if ocf is None:
        return 50.0, {'note': '现金流数据缺失，返回中性分'}

    detail = {'ocf_to_profit': f'{ocf:.2f}'}
    # 比值接近1.0最健康（现金流与利润匹配）
    if ocf >= 1.2:
        return 92.0, {**detail, 'note': '现金流充裕'}
    elif ocf >= 0.8:
        return 88.0, {**detail, 'note': '现金流健康'}
    elif ocf >= 0.5:
        return 60.0, {**detail, 'note': '现金流一般'}
    elif ocf >= 0:
        return 40.0, {**detail, 'note': '现金流偏弱'}
    else:
        return 15.0, {**detail, 'note': '现金流为负，警惕'}


def score_fin_health(data: StockData) -> tuple[float, dict]:
    """财务健康度子项评分：资产负债率 + 流动比率"""
    debt_ratio, current_ratio = data.debt_to_asset, data.current_ratio
    scores = []
    detail = {}

    if debt_ratio is not None:
        if debt_ratio <= 30:
            dr_s = 95.0
            detail['debt_to_asset'] = f'{debt_ratio:.2f}%(低杠杆)'
        elif debt_ratio <= 50:
            dr_s = 75.0
            detail['debt_to_asset'] = f'{debt_ratio:.2f}%(适中)'
        elif debt_ratio <= 60:
            dr_s = 60.0
            detail['debt_to_asset'] = f'{debt_ratio:.2f}%(偏高)'
        elif debt_ratio <= 70:
            dr_s = 40.0
            detail['debt_to_asset'] = f'{debt_ratio:.2f}%(高杠杆)'
        else:
            dr_s = 20.0
            detail['debt_to_asset'] = f'{debt_ratio:.2f}%(极高杠杆)'
        scores.append(dr_s)

    if current_ratio is not None:
        if current_ratio >= 2.0:
            cr_s = 88.0
            detail['current_ratio'] = f'{current_ratio:.2f}(充足)'
        elif current_ratio >= 1.5:
            cr_s = 72.0
            detail['current_ratio'] = f'{current_ratio:.2f}(良好)'
        elif current_ratio >= 1.0:
            cr_s = 55.0
            detail['current_ratio'] = f'{current_ratio:.2f}(正常)'
        elif current_ratio >= 0.5:
            cr_s = 30.0
            detail['current_ratio'] = f'{current_ratio:.2f}(偏紧)'
        else:
            cr_s = 15.0
            detail['current_ratio'] = f'{current_ratio:.2f}(紧张)'
        scores.append(cr_s)

    if not scores:
        return 50.0, {'note': '财务健康度数据全缺失，返回中性分'}

    return _clamp(sum(scores) / len(scores)), detail


# --- 消息面子项评分 ---


def score_sentiment(data: StockData) -> tuple[float, dict]:
    """情绪子项评分：news_sentiment 映射（缺失时用中性0.0填充，D01）"""
    sentiment = data.news_sentiment
    if sentiment is None:
        sentiment = NEUTRAL_SENTIMENT  # D01: 中性值0.0

    detail = {'news_sentiment': f'{sentiment:+.2f}'}
    # -1.0(极空) ~ +1.0(极多) 映射到 0 ~ 100，50为中性
    # B18-T4: 映射曲线从 (sentiment+1)*50 → (sentiment+1)*48，轻微压缩上限
    score = _clamp((sentiment + 1.0) * 48.0, 0, 95)
    if sentiment > 0.3:
        detail['note'] = '显著正面'
    elif sentiment > 0.1:
        detail['note'] = '偏正面'
    elif sentiment < -0.3:
        detail['note'] = '显著负面'
    elif sentiment < -0.1:
        detail['note'] = '偏负面'
    else:
        detail['note'] = '中性'

    return score, detail


def score_holder(data: StockData) -> tuple[float, dict]:
    """股东行为子项评分：大股东/高管是否增持"""
    holder = data.holder_increase
    if holder is None:
        return 50.0, {'note': '股东行为数据缺失，返回中性分'}

    if holder:
        return 82.0, {'holder_increase': 'True(增持，利好)'}
    else:
        return 35.0, {'holder_increase': 'False(未增持/减持)'}


# --- 资金面子项评分 ---


def score_main_capital(data: StockData) -> tuple[float, dict]:
    """主力资金子项评分：主力净流入（缺失时返回中性 50，不填充 0.0 进档位）

    019T T2（遗留项⑨修复）：缺失 → 不再 D02 填充 0.0（会落入 inflow>=0 档得 85 分，
    形成"数据越缺、分越高"的偏多偏差）；缺失分支改为返回 50.0 中性分 + note，
    展示诚实化。实测值路径与修复前逐位一致。
    """
    inflow = data.main_net_inflow
    if inflow is None:
        return 50.0, {'note': '主力资金数据缺失，返回中性分（不填充、不占权重）'}

    detail = {'main_net_inflow': f'{inflow:.2f}万元'}
    # 正流入得分高，负流入得分低
    # B18-T1: 中性基准上调至65，各档位适度上调
    # B18-Hotfix-T1: 激进校准，各档位再上调+5~8分
    # 阈值参考：±5000万元为显著分界
    if inflow >= 5000:
        return 95.0, {**detail, 'note': '大幅净流入'}
    elif inflow >= 1000:
        return 87.0, {**detail, 'note': '温和净流入'}
    elif inflow >= 0:
        return 85.0, {**detail, 'note': '小幅净流入'}  # O2-A+: 82→85
    elif inflow >= -1000:
        return 60.0, {**detail, 'note': '小幅净流出'}
    elif inflow >= -5000:
        return 42.0, {**detail, 'note': '温和净流出'}
    else:
        return 20.0, {**detail, 'note': '大幅净流出'}


def score_north_capital(data: StockData) -> tuple[float, dict]:
    """互联互通子项评分：北向/港股通净买入（缺失返回中性 50）

    019T T2（开放项 A 同批修复）：缺失分支 70 → 50（去除中性偏暖残存）；
    degradation 仍为 B 类 reduce，实测档位不变。
    """
    north = data.north_net_buy
    if north is None:
        return 50.0, {'note': '互联互通数据缺失，返回中性分'}

    detail = {'north_net_buy': f'{north:.2f}万元'}
    if north >= 3000:
        return 88.0, {**detail, 'note': '北向大幅买入'}  # O2-A+: 85→88
    elif north >= 500:
        return 70.0, {**detail, 'note': '北向温和买入'}
    elif north >= 0:
        return 70.0, {**detail, 'note': '北向小幅买入'}
    elif north >= -500:
        return 52.0, {**detail, 'note': '北向小幅卖出'}
    elif north >= -3000:
        return 40.0, {**detail, 'note': '北向温和卖出'}
    else:
        return 15.0, {**detail, 'note': '北向大幅卖出'}


def score_margin_capital(data: StockData) -> tuple[float, dict]:
    """杠杆资金子项评分：融资余额变化（缺失返回中性 50）

    019T T2（开放项 A 同批修复）：缺失分支 68 → 50（去除中性偏暖残存）；
    degradation 仍为 B 类 reduce，实测档位不变。
    """
    margin = data.margin_balance_chg
    if margin is None:
        return 50.0, {'note': '杠杆资金数据缺失，返回中性分'}

    detail = {'margin_balance_chg': f'{margin:.2f}万元'}
    if margin >= 2000:
        return 88.0, {**detail, 'note': '融资余额大幅增加'}  # O2-A+: 85→88
    elif margin >= 500:
        return 70.0, {**detail, 'note': '融资余额增加'}
    elif margin >= 0:
        return 70.0, {**detail, 'note': '融资余额小幅增加'}
    elif margin >= -500:
        return 52.0, {**detail, 'note': '融资余额小幅减少'}
    elif margin >= -2000:
        return 32.0, {**detail, 'note': '融资余额减少'}
    else:
        return 20.0, {**detail, 'note': '融资余额大幅减少'}


# 子项评分函数注册表
SCORING_FUNCTIONS: dict[str, Callable[[StockData], tuple[float, dict]]] = {
    # 技术面
    'ma': score_ma,
    'trend': score_trend,
    'obos': score_obos,
    'vol_price': score_vol_price,
    'vol_ratio': score_vol_ratio,
    'volatility': score_volatility,
    # 基本面
    'valuation': score_valuation,
    'profitability': score_profitability,
    'growth': score_growth,
    'cashflow': score_cashflow,
    'fin_health': score_fin_health,
    # 消息面
    'sentiment': score_sentiment,
    'holder': score_holder,
    # 资金面
    'main_capital': score_main_capital,
    'north_capital': score_north_capital,
    'margin_capital': score_margin_capital,
}


# ================================================================
# 四、维度评分聚合器
# ================================================================


def score_dimension(
    data: StockData,
    subitems: list[SubItem],
    dim_name: str,
) -> tuple[float | None, dict]:
    """对单个维度执行子项级评分与权重调整

    Returns:
        (dimension_score 0-100 or None if unavailable, detail_dict)
    """
    # 1. 计算每个子项的有效权重
    weighted = [(si, adjust_subitem_weight(data, si)) for si in subitems]

    # 2. 归一化
    norm_weights = normalize_subitem_weights(weighted)

    # 3. 检查维度是否可用（至少一个子项有权重>0且有数据）
    active_subitems = []
    for si, eff_w in weighted:
        if eff_w > 0:
            present, _, _ = _subitem_completeness(data, si)
            active_subitems.append((si, present, norm_weights[si.key]))

    if not active_subitems:
        return None, {
            'dimension': dim_name,
            'status': 'unavailable',
            'reason': f'{dim_name}所有子项数据缺失',
            'subitems': {},
        }

    # 4. 逐子项评分并按归一化权重加权求和
    dim_score = 0.0
    subitem_details = {}
    for si, eff_w in weighted:
        score_fn = SCORING_FUNCTIONS[si.key]
        sub_score, sub_detail = score_fn(data)
        norm_w = norm_weights[si.key]
        present, total, completeness = _subitem_completeness(data, si)

        subitem_details[si.key] = {
            'name': si.name,
            'score': round(sub_score, 1),
            'base_weight': si.base_weight,
            'effective_weight': round(eff_w, 4),
            'normalized_weight': norm_w,
            'completeness': round(completeness, 2),
            'degradation': si.degradation,
            'detail': sub_detail,
        }

        if norm_w > 0:
            dim_score += sub_score * norm_w

    return round(dim_score, 1), {
        'dimension': dim_name,
        'status': 'ok',
        'score': round(dim_score, 1),
        'subitems': subitem_details,
    }


# ================================================================
# 五、维度权重归一化 + 总评分
# ================================================================


def _load_dim_weights(market: str, industry: str = None) -> dict[str, float]:
    """从 config_weights.json 热加载维度权重，支持行业覆盖，回退到默认值。

    加载优先级（B17-T2）：
    1. A股且提供行业名 → 查 industry_overrides，命中则用行业权重
    2. 未命中 / 港股 / 无行业 → 按市场加载默认权重
    3. 配置异常 → 内存默认值
    """
    try:
        with open(_WEIGHTS_FILE, encoding='utf-8') as f:
            config = json.load(f)

        # B17-T2: 行业权重覆盖（仅A股生效）
        if industry and market == 'A':
            overrides = config.get('industry_overrides', {})
            if industry in overrides:
                return overrides[industry]

        # 原有逻辑：按市场加载默认权重
        market_key = 'a_stock' if market == 'A' else 'hk_stock'
        weights = config.get(market_key, {}).get('weights')
        if weights:
            return weights
    except (OSError, FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f'权重配置加载失败: {e}，使用默认值')

    return dict(_DEFAULT_DIM_WEIGHTS.get(market, _DEFAULT_DIM_WEIGHTS['A']))


def _normalize_dim_weights(
    raw_weights: dict[str, float],
    available_dims: set[str],
    min_weight: float = 0.05,
) -> tuple[dict[str, float], bool]:
    """维度权重归一化：不可用维度权重归零，剩余按比例补足

    关键规则（兼容旧引擎逻辑）：
    1. 不可用维度权重直接归零
    2. 可用但配置权重为0的维度，分配最低权重(min_weight)避免被忽略
    3. 剩余维度按比例归一化至总和=1.0
    """
    active = {}
    for k in available_dims:
        config_w = raw_weights.get(k, 0)
        if config_w > 0:
            active[k] = config_w
        else:
            active[k] = min_weight

    total = sum(active.values())
    if total == 0:
        n = len(available_dims)
        if n > 0:
            return {k: round(1.0 / n, 4) for k in available_dims}, True
        return {}, True

    normalized = {k: round(v / total, 4) for k, v in active.items()}
    was_rescaled = abs(total - 1.0) > 0.001
    return normalized, was_rescaled


def _map_rating(total_score: float) -> tuple[str, str]:
    """总分 → 评级档位（中文5档，80/65/50/30 边界）

    RATING-ALIGN-004：返回中文5档，key 与 label 统一。
    """
    sorted_ratings = sorted(RATING_THRESHOLDS.items(), key=lambda x: x[1]['min'], reverse=True)
    for grade, info in sorted_ratings:
        if total_score >= info['min']:
            return grade, grade  # 中文5档 key 即 label
    last = sorted_ratings[-1]
    return last[0], last[0]


def _generate_suggestion(rating: str, dim_scores: dict) -> str:
    """根据评级和维度强弱生成一句话操作建议（严禁硬编码评级结论）"""
    DIM_CN = {
        'kline': '技术面',
        'fundamental': '基本面',
        'capital_flow': '资金面',
        'news': '消息面',
    }
    active = [(k, v['score'], v) for k, v in dim_scores.items() if v.get('status') == 'ok']
    if not active:
        return '数据不足，暂无法给出建议'

    active.sort(key=lambda x: x[1], reverse=True)
    strongest = active[0]
    weakest = active[-1]

    highlights = []
    s_factors = strongest[2].get('subitems', {})
    # 提取最强维度亮点
    for si_detail in s_factors.values():
        if si_detail['score'] >= 75:
            highlights.append(f'{DIM_CN.get(strongest[0], strongest[0])}表现突出')
            break

    risks = []
    w_factors = weakest[2].get('subitems', {})
    for si_detail in w_factors.values():
        if si_detail['score'] <= 30:
            risks.append(f'{DIM_CN.get(weakest[0], weakest[0])}存在隐忧')
            break

    # RATING-ALIGN-004：操作建议对齐中文5档
    rating_action = {
        '强烈推荐买入': '短线可重点关注',
        '推荐买入': '逢低可考虑布局',
        '持有观望': '持有观望为主',
        '建议减仓': '谨慎参与或减仓',
        '强烈建议卖出': '建议回避或止损',
    }
    action = rating_action.get(rating, '观望')

    parts = []
    if highlights:
        parts.append(highlights[0])
    if risks:
        parts.append('但' + risks[0])

    if parts:
        return '，'.join(parts) + '，' + action
    return action


# ================================================================
# 六、主入口：analyze()
# ================================================================


def analyze(data: StockData) -> AnalysisResult:
    """对 StockData 执行四维综合评分，返回 AnalysisResult

    流程：
    1. 确保数据质量已计算
    2. 四维独立评分（含子项权重调整）
    3. 维度权重归一化（不可用维度归零）
    4. 加权求和得总分
    5. 评级映射 + 操作建议生成
    6. 组装 AnalysisResult 契约

    Args:
        data: StockData 标准数据契约对象
    Returns:
        AnalysisResult 分析结果
    """
    # 1. 计算数据质量
    dq = data.compute_data_quality()

    # 2. 四维评分
    tech_score, tech_detail = score_dimension(data, TECHNICAL_SUBITEMS, 'technical')
    fund_score, fund_detail = score_dimension(data, FUNDAMENTAL_SUBITEMS, 'fundamental')
    news_score, news_detail = score_dimension(data, NEWS_SUBITEMS, 'news')
    cap_score, cap_detail = score_dimension(data, CAPITAL_SUBITEMS, 'capital')

    dim_results = {
        'kline': tech_detail,
        'fundamental': fund_detail,
        'news': news_detail,
        'capital_flow': cap_detail,
    }

    # 3. 确定可用维度
    available_dims = set()
    dim_scores_map = {}
    for cfg_key, score_val, detail in [
        ('kline', tech_score, tech_detail),
        ('fundamental', fund_score, fund_detail),
        ('news', news_score, news_detail),
        ('capital_flow', cap_score, cap_detail),
    ]:
        if score_val is not None:
            available_dims.add(cfg_key)
            dim_scores_map[cfg_key] = score_val

    # 4. 维度权重归一化（B17-T2：传入行业以支持行业权重覆盖）
    raw_weights = _load_dim_weights(data.market, getattr(data, 'industry', None))
    norm_dim_weights, was_rescaled = _normalize_dim_weights(raw_weights, available_dims)

    # 5. 总分
    total_score = 0.0
    for dim_key, dim_score_val in dim_scores_map.items():
        total_score += dim_score_val * norm_dim_weights.get(dim_key, 0)
    total_score = round(total_score, 1)

    # 6. 评级
    rating, rating_label = _map_rating(total_score)

    # 7. 操作建议
    suggestion = _generate_suggestion(rating, dim_results)

    # 8. 数据警告
    warnings = []
    dq_map = dq.model_dump() if dq else {}
    for dim_key, detail in dim_results.items():
        if detail.get('status') != 'ok':
            warnings.append(f'{detail.get("dimension", dim_key)}: {detail.get("reason", "不可用")}')
        else:
            # 透明化：维度有评分但实际数据完整度极低（依赖默认值填充）
            dq_key = {
                'kline': 'technical',
                'fundamental': 'fundamental',
                'news': 'news',
                'capital_flow': 'capital',
            }.get(dim_key, dim_key)
            dq_val = dq_map.get(dq_key, 1.0)
            if dq_val < 0.15:
                filled_subitems = [
                    si['name']
                    for si in detail.get('subitems', {}).values()
                    if si.get('degradation') == 'keep_default'
                    and si.get('completeness', 1.0) == 0.0
                ]
                if filled_subitems:
                    warnings.append(
                        f'{detail.get("dimension", dim_key)}完整度仅{dq_val:.0%}，'
                        f'评分依赖默认值填充(子项: {",".join(filled_subitems)})'
                    )
    if was_rescaled:
        warnings.append(f'维度权重已重新归一化（活跃维度: {sorted(available_dims)}）')

    # B10: 资金面受限标注（北向资金/两融数据源不可用时提示用户）
    if data.north_net_buy is None and data.margin_balance_chg is None:
        warnings.append('资金面提示：北向资金/两融数据源暂不可用，当前评分仅基于主力资金流向')

    # 9. 组装结果
    # B12-T3: score_date 使用 K 线数据的 trade_date（而非 datetime.now()）
    # data.trade_date 格式为 YYYYMMDD，需转换为 YYYY-MM-DD
    _td = str(data.trade_date or '').strip()
    if len(_td) == 8 and _td.isdigit():
        score_date = f'{_td[:4]}-{_td[4:6]}-{_td[6:8]}'
    else:
        # 兒底：trade_date 无效时使用当前日期
        score_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        logger.warning(f'score_date 回退到 datetime.now(): trade_date={data.trade_date!r}')

    result = AnalysisResult(
        code=data.code,
        score_date=score_date,
        total_score=total_score,
        rating=rating,
        rating_label=rating_label,
        # 四维得分（D03: sentiment_score 对应消息面）
        technical_score=tech_score,
        fundamental_score=fund_score,
        sentiment_score=news_score,
        capital_score=cap_score,
        # 归一化后的维度权重
        technical_weight=norm_dim_weights.get('kline', 0.0),
        fundamental_weight=norm_dim_weights.get('fundamental', 0.0),
        sentiment_weight=norm_dim_weights.get('news', 0.0),
        capital_weight=norm_dim_weights.get('capital_flow', 0.0),
        # 元数据
        operation_suggestion=suggestion,
        data_warnings=warnings,
        data_quality=dq.model_dump() if dq else None,
        degradations=data.to_analysis_dict().get('degradations', {}),
    )

    logger.info(
        f'[{data.code}] 评分完成: 总分={total_score}, 评级={rating}, '
        f'活跃维度={sorted(available_dims)}, 权重重分配={was_rescaled}'
    )

    return result


# ================================================================
# 七、数据库真实数据入口（P1新增）
# ================================================================


def analyze_from_db(stock_id: int):
    """从数据库加载真实数据并执行 v5.0 四维评分

    流程：
    1. 调用 data_adapter 从 SQLite 读取真实数据并计算技术指标
    2. 构建 StockData 标准契约
    3. 调用 analyze() 执行四维评分
    4. 返回 AnalysisResult

    Args:
        stock_id: 数据库 stocks.id
    Returns:
        AnalysisResult 对象，或 None（数据不足）
    """
    from modules.data_adapter import load_stockdata_from_db

    data = load_stockdata_from_db(stock_id)
    if data is None:
        logger.warning(f'stock_id={stock_id} 数据不足，无法执行 v5.0 评分')
        return None

    return analyze(data)


# ================================================================
# 八、命令行测试入口（用 MockDataProvider 三场景验证）
# ================================================================


def _print_result(result: AnalysisResult, scenario: str):
    """格式化打印评分结果"""
    print(f'\n{"=" * 70}')
    print(
        f'  场景: {scenario}  |  股票: {result.code}  |  评级: {result.rating}({result.rating_label})'
    )
    print(f'{"=" * 70}')
    print(f'  综合评分: {result.total_score:.1f}')
    print(f'  操作建议: {result.operation_suggestion}')
    print()
    print('  维度得分:')
    print(f'    技术面  {result.technical_score or 0:>6.1f}  (权重 {result.technical_weight:.1%})')
    print(
        f'    基本面  {result.fundamental_score or 0:>6.1f}  (权重 {result.fundamental_weight:.1%})'
    )
    print(f'    消息面  {result.sentiment_score or 0:>6.1f}  (权重 {result.sentiment_weight:.1%})')
    print(f'    资金面  {result.capital_score or 0:>6.1f}  (权重 {result.capital_weight:.1%})')
    print()
    print(f'  数据质量: {result.data_quality}')
    if result.data_warnings:
        print('  数据警告:')
        for w in result.data_warnings:
            print(f'    - {w}')
    print(f'  降级规则触发: {len(result.degradations)}条')
    print()


if __name__ == '__main__':
    from modules.mock_data_provider import MockDataProvider

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    provider = MockDataProvider()

    # --- 场景1: normal（完整数据）---
    data_normal = provider.generate('normal', code='600519.SH', market='A', close=1680.0, seed=42)
    result_normal = analyze(data_normal)
    _print_result(result_normal, 'normal（完整数据）')

    # --- 场景2: boundary random（边界值）---
    data_boundary = provider.generate('boundary', code='000001.SZ', market='A', close=15.0, seed=42)
    result_boundary = analyze(data_boundary)
    _print_result(result_boundary, 'boundary（边界值）')

    # --- 场景3: partial 30%（字段缺失）---
    data_partial = provider.generate(
        'partial', code='00700.HK', market='HK', close=350.0, missing_rate=0.3, seed=42
    )
    result_partial = analyze(data_partial)
    _print_result(result_partial, 'partial 30%（字段缺失）')

    # --- 场景4: partial 70%（严重缺失，验证降级机制）---
    data_severe = provider.generate(
        'partial', code='300750.SZ', market='A', close=200.0, missing_rate=0.7, seed=99
    )
    result_severe = analyze(data_severe)
    _print_result(result_severe, 'partial 70%（严重缺失）')

    print(f'{"=" * 70}')
    print('  四维评分引擎原型测试完成 [PASS]')
    print(f'{"=" * 70}')
