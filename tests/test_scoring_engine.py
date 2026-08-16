"""
scoring_engine.py 聚焦单元测试

覆盖范围：
1. 子项评分函数（评分公式 + 档位边界值）
2. 权重应用与降级机制（A 类归零 / B 类降权 / C 类填充）
3. 权重归一化（子项级 + 维度级）
4. 评级映射（_map_rating / normalize_rating）
5. 端到端 analyze()（基于 MockDataProvider，隔离数据库与网络）
"""

import pytest

from modules.data_contract import StockData
from modules.scoring_engine import (
    CAPITAL_SUBITEMS,
    DEFAULT_VOLUME_RATIO,
    NEUTRAL_INFLOW,
    NEUTRAL_SENTIMENT,
    RATING_THRESHOLDS,
    REDUCE_RATIO,
    TECHNICAL_SUBITEMS,
    SubItem,
    _clamp,
    _map_rating,
    _normalize_dim_weights,
    adjust_subitem_weight,
    analyze,
    normalize_rating,
    normalize_subitem_weights,
    score_cashflow,
    score_dimension,
    score_fin_health,
    score_growth,
    score_holder,
    score_ma,
    score_main_capital,
    score_margin_capital,
    score_obos,
    score_profitability,
    score_sentiment,
    score_trend,
    score_valuation,
    score_vol_price,
    score_vol_ratio,
    score_volatility,
)

# ============================================================
# 辅助：构造最小 StockData（默认仅必填字段，其余 None）
# ============================================================


def _sd(**overrides) -> StockData:
    """构造 StockData，默认只含必填字段，用 overrides 覆盖指定字段。

    所有可选字段默认 None，便于隔离测试单个子项评分函数。
    """
    defaults = dict(code='600519.SH', market='A', trade_date='20260803', close=50.0)
    defaults.update(overrides)
    return StockData(**defaults)


# ============================================================
# 一、辅助函数 _clamp 边界值
# ============================================================


class TestClamp:
    """_clamp 将分数限制在 [0, 100]"""

    def test_below_zero_clamps_to_zero(self):
        assert _clamp(-5) == 0.0

    def test_above_hundred_clamps_to_hundred(self):
        assert _clamp(150) == 100.0

    def test_in_range_unchanged(self):
        assert _clamp(50) == 50.0

    def test_exact_boundaries(self):
        assert _clamp(0) == 0.0
        assert _clamp(100) == 100.0

    def test_custom_range(self):
        assert _clamp(-1, lo=10, hi=20) == 10.0
        assert _clamp(25, lo=10, hi=20) == 20.0


# ============================================================
# 二、技术面子项评分
# ============================================================


class TestTechnicalScoring:
    """技术面 6 子项评分公式与边界值"""

    # --- score_ma 均线 ---
    def test_ma_all_missing_returns_neutral(self):
        score, _ = score_ma(_sd())
        assert score == 50.0

    def test_ma_golden_cross_scores_higher_than_death_cross(self):
        # 金叉：MA5>MA20，价格站上均线
        golden, _ = score_ma(_sd(ma5=51, ma20=50, close=51.5))
        # 死叉：MA5<MA20，价格跌破均线
        death, _ = score_ma(_sd(ma5=49, ma20=50, close=48.5))
        assert golden > death
        assert golden > 60  # 金叉偏多
        assert death < 30  # 死叉偏空

    def test_ma_golden_cross_exact_value(self):
        # deviation=(51-50)/50*100=2 -> 85+3=88; close 高于 ma5/ma20 各+4 -> 96
        score, detail = score_ma(_sd(ma5=51, ma20=50, close=51.5))
        assert score == 96.0
        assert '金叉' in detail.get('cross', '')

    def test_ma_price_above_ma_adds_bonus(self):
        above, _ = score_ma(_sd(ma5=51, ma20=50, close=55))
        below, _ = score_ma(_sd(ma5=51, ma20=50, close=50.5))
        assert above > below  # 价格在均线上方得分更高

    # --- score_trend 趋势 ---
    def test_trend_all_missing_returns_neutral(self):
        assert score_trend(_sd())[0] == 50.0

    def test_trend_macd_bullish_vs_bearish(self):
        bullish, _ = score_trend(_sd(macd_dif=0.1, macd_dea=0.05))
        bearish, _ = score_trend(_sd(macd_dif=0.05, macd_dea=0.1))
        assert bullish > bearish
        assert bullish > 60
        assert bearish < 30

    def test_trend_macd_bullish_exact(self):
        # hist=0.05 -> 82+min(20, 0.05*40)=82+2=84
        score, detail = score_trend(_sd(macd_dif=0.1, macd_dea=0.05))
        assert score == 84.0
        assert '多头' in detail.get('macd', '')

    def test_trend_ma60_above_adds_score(self):
        above, _ = score_trend(_sd(ma60=40, close=50))  # 价格在 MA60 上方
        below, _ = score_trend(_sd(ma60=60, close=50))  # 价格在 MA60 下方
        assert above > below

    # --- score_obos 超买超卖 ---
    @pytest.mark.parametrize(
        'rsi,expected',
        [
            (85, 12.5),  # 严重超买 20-(85-80)*1.5
            (75, 40.0),  # 超买 50-(75-70)*2
            (55, 97.0),  # 健康最佳 87+10
            (50, 87.0),  # 健康 87+max(0,10-|50-55|*2)=87+0
            (40, 50.0),  # 中性 30<=40<45
            (25, 32.5),  # 超卖 30+(30-25)*0.5
        ],
    )
    def test_obos_rsi_levels(self, rsi, expected):
        score, _ = score_obos(_sd(rsi_14=rsi))
        assert score == expected

    @pytest.mark.parametrize(
        'kdj,expected',
        [
            (85, 35.0),  # 超买
            (10, 45.0),  # 超卖
            (50, 82.0),  # 健康
            (30, 45.0),  # 偏弱 20<=30<40
            (65, 75.0),  # 中性 60<65<=80 else 分支
        ],
    )
    def test_obos_kdj_levels(self, kdj, expected):
        score, _ = score_obos(_sd(kdj_k=kdj))
        assert score == expected

    def test_obos_all_missing_returns_neutral(self):
        assert score_obos(_sd())[0] == 50.0

    def test_obos_both_present_averages(self):
        # rsi=55->97, kdj=50->82, 平均 89.5
        score, _ = score_obos(_sd(rsi_14=55, kdj_k=50))
        assert score == pytest.approx(89.5)

    # --- score_vol_price 量价分析 ---
    @pytest.mark.parametrize(
        'vol,expected',
        [
            (20_000_000, 88.0),
            (5_000_000, 72.0),
            (1_000_000, 60.0),
            (100_000, 40.0),
            (1, 35.0),  # 极低成交量
            (0, 30.0),  # 零成交量异常
        ],
    )
    def test_vol_price_levels(self, vol, expected):
        score, _ = score_vol_price(_sd(volume=vol))
        assert score == expected

    def test_vol_price_missing_returns_neutral(self):
        assert score_vol_price(_sd())[0] == 50.0

    # --- score_vol_ratio 量比 ---
    @pytest.mark.parametrize(
        'vr,expected',
        [
            (3.5, 50.0),  # 异常放量
            (2.5, 70.0),  # 显著放量
            (1.5, 80.0),  # 温和放量
            (0.8, 65.0),  # 正常量能
            (0.5, 55.0),  # 量能偏弱
            (0.3, 40.0),  # 缩量明显
        ],
    )
    def test_vol_ratio_levels(self, vr, expected):
        score, _ = score_vol_ratio(_sd(volume_ratio=vr))
        assert score == expected

    def test_vol_ratio_missing_uses_default(self):
        # 缺失时用 DEFAULT_VOLUME_RATIO(1.0) 填充 -> 正常量能 65
        score, detail = score_vol_ratio(_sd())
        assert score == 65.0
        assert DEFAULT_VOLUME_RATIO == 1.0

    # --- score_volatility 波动率 ---
    def test_volatility_missing_returns_neutral(self):
        assert score_volatility(_sd())[0] == 50.0

    def test_volatility_zero_bandwidth_returns_neutral(self):
        # 上轨=下轨，带宽为 0
        score, _ = score_volatility(_sd(boll_upper=50, boll_lower=50, close=50))
        assert score == 50.0

    @pytest.mark.parametrize(
        'close,expected,note',
        [
            (55, 88.0, '中轨偏上健康 40-70'),  # pos=50
            (53, 65.0, '偏弱有支撑 20-40'),  # pos=30
            (58.5, 65.0, '偏强警惕 70-85'),  # pos=85
            (59, 55.0, '触及上轨回调风险 >85'),  # pos=90
            (51.5, 50.0, '接近下轨 10-20'),  # pos=15
            (50.5, 30.0, '触及下轨 <10'),  # pos=5
        ],
    )
    def test_volatility_position_levels(self, close, expected, note):
        score, _ = score_volatility(_sd(boll_upper=60, boll_lower=50, close=close))
        assert score == expected


# ============================================================
# 三、基本面子项评分
# ============================================================


class TestFundamentalScoring:
    """基本面 5 子项评分公式与边界值"""

    # --- score_valuation 估值 ---
    @pytest.mark.parametrize(
        'pe,expected',
        [
            (0, 20.0),  # 亏损/负值/零
            (10, 97.0),  # 低估
            (20, 80.0),  # 合理
            (40, 60.0),  # 偏高
            (50, 35.0),  # 高估
            (80, 15.0),  # 严重高估
        ],
    )
    def test_valuation_pe_levels(self, pe, expected):
        score, _ = score_valuation(_sd(pe_ttm=pe))
        assert score == expected

    @pytest.mark.parametrize(
        'pb,expected',
        [
            (0, 20.0),
            (0.5, 88.0),  # 破净
            (1.5, 75.0),  # 合理偏低
            (3.0, 60.0),  # 合理
            (5.0, 40.0),  # 偏高
            (8.0, 20.0),  # 高估
        ],
    )
    def test_valuation_pb_levels(self, pb, expected):
        score, _ = score_valuation(_sd(pb=pb))
        assert score == expected

    def test_valuation_both_present_averages(self):
        # pe=10 -> 97, pb=0.5 -> 88, 平均 92.5
        score, _ = score_valuation(_sd(pe_ttm=10, pb=0.5))
        assert score == 92.5

    def test_valuation_all_missing_returns_neutral(self):
        assert score_valuation(_sd())[0] == 50.0

    # --- score_profitability 盈利能力 ---
    @pytest.mark.parametrize(
        'roe,expected',
        [
            (25, 98.0),  # 优秀 >=20
            (18, 95.0),  # 良好 >=15
            (12, 80.0),  # 一般 >=10
            (7, 62.0),  # 偏低 >=5
            (3, 25.0),  # 较差 >=0
            (-5, 10.0),  # 亏损
        ],
    )
    def test_profitability_roe_levels(self, roe, expected):
        score, _ = score_profitability(_sd(roe=roe))
        assert score == expected

    @pytest.mark.parametrize(
        'gm,expected',
        [
            (60, 92.0),  # 高 >=50
            (35, 76.0),  # 中高 >=30
            (20, 55.0),  # 中 >=15
            (10, 35.0),  # 低 >=0
            (-5, 15.0),  # 负值
        ],
    )
    def test_profitability_gross_margin_levels(self, gm, expected):
        score, _ = score_profitability(_sd(gross_margin=gm))
        assert score == expected

    def test_profitability_all_missing_returns_neutral(self):
        assert score_profitability(_sd())[0] == 50.0

    # --- score_growth 成长性 ---
    @pytest.mark.parametrize(
        'rev,expected',
        [
            (35, 96.0),  # >=30
            (25, 95.0),  # >=20
            (15, 80.0),  # >=10
            (5, 62.0),  # >=0
            (-5, 30.0),  # >=-10
            (-20, 15.0),  # <-10
        ],
    )
    def test_growth_revenue_levels(self, rev, expected):
        score, _ = score_growth(_sd(revenue_yoy=rev))
        assert score == expected

    @pytest.mark.parametrize(
        'np_yoy,expected',
        [
            (60, 96.0),  # >=50
            (40, 85.0),  # >=30
            (20, 72.0),  # >=15
            (5, 52.0),  # >=0
            (-10, 30.0),  # >=-20
            (-30, 12.0),  # <-20
        ],
    )
    def test_growth_net_profit_levels(self, np_yoy, expected):
        score, _ = score_growth(_sd(net_profit_yoy=np_yoy))
        assert score == expected

    def test_growth_all_missing_returns_neutral(self):
        assert score_growth(_sd())[0] == 50.0

    # --- score_cashflow 现金流质量 ---
    @pytest.mark.parametrize(
        'ocf,expected',
        [
            (1.2, 92.0),  # 充裕 >=1.2
            (0.8, 88.0),  # 健康 >=0.8
            (0.5, 60.0),  # 一般 >=0.5
            (0.1, 40.0),  # 偏弱 >=0
            (-0.3, 15.0),  # 为负
        ],
    )
    def test_cashflow_levels(self, ocf, expected):
        score, _ = score_cashflow(_sd(ocf_to_profit=ocf))
        assert score == expected

    def test_cashflow_missing_returns_neutral(self):
        assert score_cashflow(_sd())[0] == 50.0

    # --- score_fin_health 财务健康度 ---
    @pytest.mark.parametrize(
        'debt,expected',
        [
            (20, 95.0),  # 低杠杆 <=30
            (40, 75.0),  # 适中 <=50
            (55, 60.0),  # 偏高 <=60
            (65, 40.0),  # 高杠杆 <=70
            (80, 20.0),  # 极高杠杆
        ],
    )
    def test_fin_health_debt_levels(self, debt, expected):
        score, _ = score_fin_health(_sd(debt_to_asset=debt))
        assert score == expected

    @pytest.mark.parametrize(
        'cr,expected',
        [
            (2.5, 88.0),  # 充足 >=2.0
            (1.8, 72.0),  # 良好 >=1.5
            (1.2, 55.0),  # 正常 >=1.0
            (0.8, 30.0),  # 偏紧 >=0.5
            (0.3, 15.0),  # 紧张
        ],
    )
    def test_fin_health_current_ratio_levels(self, cr, expected):
        score, _ = score_fin_health(_sd(current_ratio=cr))
        assert score == expected

    def test_fin_health_all_missing_returns_neutral(self):
        assert score_fin_health(_sd())[0] == 50.0


# ============================================================
# 四、消息面子项评分
# ============================================================


class TestNewsScoring:
    """消息面 2 子项评分"""

    # --- score_sentiment 情绪 ---
    def test_sentiment_missing_uses_neutral(self):
        # 缺失填充 0.0 -> (0+1)*48=48
        score, detail = score_sentiment(_sd())
        assert score == 48.0
        assert NEUTRAL_SENTIMENT == 0.0

    @pytest.mark.parametrize(
        'sent,expected',
        [
            (1.0, 95.0),  # 极多 (2*48=96 clamp 95)
            (0.5, 72.0),  # 偏多 1.5*48
            (0.0, 48.0),  # 中性
            (-0.5, 24.0),  # 偏空 0.5*48
            (-1.0, 0.0),  # 极空
        ],
    )
    def test_sentiment_mapping(self, sent, expected):
        score, _ = score_sentiment(_sd(news_sentiment=sent))
        assert score == expected

    def test_sentiment_always_within_bounds(self):
        for s in [-1.0, -0.3, 0.0, 0.3, 1.0]:
            score, _ = score_sentiment(_sd(news_sentiment=s))
            assert 0.0 <= score <= 95.0

    # --- score_holder 股东行为 ---
    def test_holder_increase_true(self):
        score, detail = score_holder(_sd(holder_increase=True))
        assert score == 82.0

    def test_holder_increase_false(self):
        score, detail = score_holder(_sd(holder_increase=False))
        assert score == 35.0

    def test_holder_missing_returns_neutral(self):
        assert score_holder(_sd())[0] == 50.0


# ============================================================
# 五、资金面子项评分
# ============================================================


class TestCapitalScoring:
    """资金面 3 子项评分"""

    # --- score_main_capital 主力资金 ---
    # 019T T2：缺失分支不再 D02 填充 0.0 进档位（原 85 分偏多偏差），返回中性 50
    def test_main_capital_missing_returns_neutral(self):
        score, detail = score_main_capital(_sd())
        assert score == 50.0
        assert '缺失' in detail.get('note', '')
        assert NEUTRAL_INFLOW == 0.0  # D02 常量保留（历史决策记录，不再用于填充）

    @pytest.mark.parametrize(
        'inflow,expected',
        [
            (5000, 95.0),  # 大幅净流入
            (1000, 87.0),  # 温和净流入
            (0, 85.0),  # 实测 0（非缺失）必须仍 85 —— 019T 回归断言
            (-1000, 60.0),  # 小幅净流出
            (-5000, 42.0),  # 温和净流出
            (-5001, 20.0),  # 大幅净流出
        ],
    )
    def test_main_capital_levels(self, inflow, expected):
        # 实测值路径与修复前逐位一致（T2 零回归）
        score, _ = score_main_capital(_sd(main_net_inflow=inflow))
        assert score == expected

    def test_main_capital_four_branches(self):
        """019T T2 四类分支：缺失 / 实测0 / 实测正 / 实测负"""
        missing, _ = score_main_capital(_sd())
        zero, _ = score_main_capital(_sd(main_net_inflow=0.0))
        positive, _ = score_main_capital(_sd(main_net_inflow=5000.0))
        negative, _ = score_main_capital(_sd(main_net_inflow=-5000.0))
        assert missing == 50.0
        assert zero == 85.0
        assert positive == 95.0
        assert negative == 42.0

    # --- score_margin_capital 杠杆资金 ---
    # 019T T2（开放项 A）：缺失 68 → 50，实测档位不变
    def test_margin_capital_missing_returns_neutral(self):
        score, _ = score_margin_capital(_sd())
        assert score == 50.0

    @pytest.mark.parametrize(
        'margin,expected',
        [
            (2000, 88.0),  # 大幅增加
            (500, 70.0),  # 增加
            (0, 70.0),  # 小幅增加
            (-500, 52.0),  # 小幅减少
            (-2000, 32.0),  # 减少
            (-2001, 20.0),  # 大幅减少
        ],
    )
    def test_margin_capital_levels(self, margin, expected):
        score, _ = score_margin_capital(_sd(margin_balance_chg=margin))
        assert score == expected

    # --- 019T T2 配置回归：degradation 类型（020R-47 更新：互联互通子项已移除） ---
    def test_capital_subitems_degradation_019T(self):
        """020R-47：资金面 4 子项——main 归零型、margin 降权型、inst_hold/holder_count 归零型；权重合计 1.0"""
        by_key = {si.key: si for si in CAPITAL_SUBITEMS}
        assert set(by_key.keys()) == {'main_capital', 'margin_capital', 'inst_hold', 'holder_count'}
        main_si = by_key['main_capital']
        assert main_si.degradation == 'zero'
        assert main_si.default_fills == {}
        assert abs(main_si.base_weight - 0.50) < 1e-9
        assert by_key['margin_capital'].degradation == 'reduce'
        assert by_key['inst_hold'].degradation == 'zero'
        assert by_key['holder_count'].degradation == 'zero'
        total = sum(si.base_weight for si in CAPITAL_SUBITEMS)
        assert abs(total - 1.0) < 1e-9


# ============================================================
# 六、权重应用与降级机制（Q03 核心）
# ============================================================


class TestSubitemWeightAdjustment:
    """adjust_subitem_weight 三类降级规则"""

    def test_zero_type_all_missing_returns_zero(self):
        # A 类（归零）：ma 子项，ma5/ma10/ma20 全缺失 -> 权重 0
        ma_subitem = SubItem('均线', 'ma', ['ma5', 'ma10', 'ma20'], 0.25, 'zero')
        assert adjust_subitem_weight(_sd(), ma_subitem) == 0.0

    def test_zero_type_present_returns_base(self):
        ma_subitem = SubItem('均线', 'ma', ['ma5', 'ma10', 'ma20'], 0.25, 'zero')
        assert adjust_subitem_weight(_sd(ma5=10), ma_subitem) == 0.25

    def test_reduce_type_all_missing_reduces_weight(self):
        # B 类（降权）：全缺失 -> base*(1-0.3)
        trend_subitem = SubItem('趋势', 'trend', ['ma60', 'macd_dif', 'macd_dea'], 0.20, 'reduce')
        expected = 0.20 * (1.0 - REDUCE_RATIO)
        assert adjust_subitem_weight(_sd(), trend_subitem) == pytest.approx(expected)

    def test_reduce_type_present_returns_base(self):
        trend_subitem = SubItem('趋势', 'trend', ['ma60', 'macd_dif', 'macd_dea'], 0.20, 'reduce')
        assert adjust_subitem_weight(_sd(macd_dif=0.1), trend_subitem) == 0.20

    def test_keep_default_type_always_keeps_base(self):
        # C 类（填充）：无论字段是否缺失，权重保持
        vr_subitem = SubItem(
            '量比',
            'vol_ratio',
            ['volume_ratio'],
            0.10,
            'keep_default',
            default_fills={'volume_ratio': 1.0},
        )
        assert adjust_subitem_weight(_sd(), vr_subitem) == 0.10
        assert adjust_subitem_weight(_sd(volume_ratio=1.5), vr_subitem) == 0.10

    def test_registered_subitems_degradation_consistency(self):
        """注册表中子项的降级类型与预期一致（防止配置漂移）——020R-48 多周期结构"""
        # A 类（归零型）包含 monthly_trend / vol_price
        zero_keys = {si.key for si in TECHNICAL_SUBITEMS if si.degradation == 'zero'}
        assert {'monthly_trend', 'vol_price'}.issubset(zero_keys)
        # B 类（降权型）包含 weekly_trend / weekly_obos / weekly_vol / obos
        reduce_keys = {si.key for si in TECHNICAL_SUBITEMS if si.degradation == 'reduce'}
        assert {'weekly_trend', 'weekly_obos', 'weekly_vol', 'obos'}.issubset(reduce_keys)
        # C 类（填充型）包含 vol_ratio
        keep_keys = {si.key for si in TECHNICAL_SUBITEMS if si.degradation == 'keep_default'}
        assert 'vol_ratio' in keep_keys
        # 权重合计 1.0
        total = sum(si.base_weight for si in TECHNICAL_SUBITEMS)
        assert abs(total - 1.0) < 1e-9


class TestWeightNormalization:
    """normalize_subitem_weights 子项级归一化"""

    def test_normalizes_to_one(self):
        si_a = SubItem('A', 'a', ['ma5'], 0.3, 'zero')
        si_b = SubItem('B', 'b', ['ma10'], 0.7, 'zero')
        result = normalize_subitem_weights([(si_a, 0.3), (si_b, 0.7)])
        assert result == {'a': 0.3, 'b': 0.7}
        assert sum(result.values()) == pytest.approx(1.0)

    def test_rescales_when_one_zeroed(self):
        si_a = SubItem('A', 'a', ['ma5'], 0.25, 'zero')
        si_b = SubItem('B', 'b', ['ma10'], 0.25, 'zero')
        # a 权重归零，剩余 b 承担全部
        result = normalize_subitem_weights([(si_a, 0.0), (si_b, 0.25)])
        assert result['a'] == 0.0
        assert result['b'] == 1.0

    def test_all_zero_returns_all_zero(self):
        si_a = SubItem('A', 'a', ['ma5'], 0.25, 'zero')
        si_b = SubItem('B', 'b', ['ma10'], 0.25, 'zero')
        result = normalize_subitem_weights([(si_a, 0.0), (si_b, 0.0)])
        assert result == {'a': 0.0, 'b': 0.0}


class TestDimWeightNormalization:
    """_normalize_dim_weights 维度级归一化"""

    def test_already_normalized(self):
        weights = {'kline': 0.4, 'fundamental': 0.6}
        norm, rescaled = _normalize_dim_weights(weights, {'kline', 'fundamental'})
        assert norm == {'kline': 0.4, 'fundamental': 0.6}
        assert rescaled is False

    def test_unavailable_dim_zeroed_and_rescaled(self):
        # fundamental 不可用，kline 独占
        weights = {'kline': 0.4, 'fundamental': 0.6}
        norm, rescaled = _normalize_dim_weights(weights, {'kline'})
        assert norm['kline'] == 1.0
        assert rescaled is True

    def test_zero_config_weight_gets_minimum(self):
        # 可用但配置为 0 的维度分配 min_weight
        weights = {'kline': 0.5, 'news': 0.0}
        norm, _ = _normalize_dim_weights(weights, {'kline', 'news'})
        assert norm['news'] > 0  # 不被忽略
        assert sum(norm.values()) == pytest.approx(1.0)

    def test_all_unavailable_uniform_split(self):
        weights = {'kline': 0.0, 'fundamental': 0.0}
        norm, rescaled = _normalize_dim_weights(weights, {'kline', 'fundamental'}, min_weight=0.05)
        # 全为 0 配置时各分 min_weight 后再归一化 -> 均分
        assert norm['kline'] == pytest.approx(norm['fundamental'])
        assert sum(norm.values()) == pytest.approx(1.0)
        assert rescaled is True


# ============================================================
# 七、维度评分聚合 score_dimension
# ============================================================


class TestScoreDimension:
    """score_dimension 聚合验证"""

    def test_technical_dimension_returns_score(self):
        data = _sd(
            ma5=52,
            ma20=50,
            macd_dif=0.1,
            macd_dea=0.05,
            rsi_14=55,
            kdj_k=50,
            volume=5_000_000,
            volume_ratio=1.5,
            boll_upper=55,
            boll_lower=45,
        )
        score, detail = score_dimension(data, TECHNICAL_SUBITEMS, 'technical')
        assert score is not None
        assert 0.0 <= score <= 100.0
        assert detail['status'] == 'ok'
        # 子项权重归一化后总和应为 1
        norm_weights = [si['normalized_weight'] for si in detail['subitems'].values()]
        assert sum(norm_weights) == pytest.approx(1.0, abs=1e-3)

    def test_dimension_unavailable_when_all_zero(self):
        # 构造一个全部 A 类且全缺失的维度 -> unavailable
        all_zero_subitems = [
            SubItem('X', 'x', ['ma5'], 0.5, 'zero'),
            SubItem('Y', 'y', ['ma10'], 0.5, 'zero'),
        ]
        score, detail = score_dimension(_sd(), all_zero_subitems, 'test_dim')
        assert score is None
        assert detail['status'] == 'unavailable'


# ============================================================
# 八、评级映射
# ============================================================


class TestRatingMapping:
    """_map_rating / normalize_rating（边界 80/65/50/30）"""

    @pytest.mark.parametrize(
        'score,expected',
        [
            (100, '强烈推荐买入'),
            (80, '强烈推荐买入'),
            (79.9, '推荐买入'),
            (65, '推荐买入'),
            (64, '持有观望'),
            (50, '持有观望'),
            (49, '建议减仓'),
            (30, '建议减仓'),
            (29, '强烈建议卖出'),
            (0, '强烈建议卖出'),
        ],
    )
    def test_map_rating_thresholds(self, score, expected):
        grade, label = _map_rating(score)
        assert grade == expected
        assert label == expected  # 中文5档 key 即 label

    def test_normalize_new_rating_passthrough(self):
        assert normalize_rating('推荐买入') == '推荐买入'
        assert normalize_rating('强烈建议卖出') == '强烈建议卖出'

    def test_normalize_legacy_mapping(self):
        assert normalize_rating('A') == '强烈推荐买入'
        assert normalize_rating('B+') == '推荐买入'
        assert normalize_rating('B') == '持有观望'
        assert normalize_rating('C') == '建议减仓'
        assert normalize_rating('D') == '强烈建议卖出'

    def test_normalize_none_returns_none(self):
        assert normalize_rating(None) is None

    def test_rating_thresholds_consistency(self):
        """RATING_THRESHOLDS 五档边界连续且覆盖 0-100"""
        sorted_ratings = sorted(RATING_THRESHOLDS.items(), key=lambda x: x[1]['min'])
        assert sorted_ratings[0][1]['min'] == 0
        assert sorted_ratings[-1][1]['max'] == 100


# ============================================================
# 九、端到端 analyze（MockDataProvider 隔离数据库与网络）
# ============================================================


class TestAnalyzeEndToEnd:
    """analyze() 完整流程，使用 MockDataProvider 生成纯内存数据"""

    def test_analyze_normal_scenario(self, provider):
        data = provider.generate('normal', code='600519.SH', market='A', close=50.0, seed=42)
        result = analyze(data)

        assert result.code == '600519.SH'
        assert 0.0 <= result.total_score <= 100.0
        assert result.rating in RATING_THRESHOLDS
        # 维度权重之和约为 1
        total_w = (
            result.technical_weight
            + result.fundamental_weight
            + result.sentiment_weight
            + result.capital_weight
        )
        assert total_w == pytest.approx(1.0, abs=1e-3)
        # 四维得分均有效
        for s in (
            result.technical_score,
            result.fundamental_score,
            result.sentiment_score,
            result.capital_score,
        ):
            assert s is not None and 0.0 <= s <= 100.0

    def test_analyze_boundary_scenario(self, provider):
        data = provider.generate('boundary', code='000001.SZ', market='A', close=15.0, seed=7)
        result = analyze(data)
        assert 0.0 <= result.total_score <= 100.0
        assert result.rating in RATING_THRESHOLDS

    def test_analyze_partial_scenario_triggers_degradation(self, provider):
        # 30% 字段缺失，验证降级机制不崩溃且产生有效结果
        data = provider.generate(
            'partial', code='300750.SZ', market='A', close=100.0, missing_rate=0.3, seed=42
        )
        result = analyze(data)
        assert 0.0 <= result.total_score <= 100.0
        # 缺失字段应产生降级记录
        assert isinstance(result.degradations, dict)

    def test_analyze_severe_missing_still_scores(self, provider):
        # 70% 严重缺失，验证权重重分配后仍产出有效评分
        data = provider.generate(
            'partial', code='300750.SZ', market='A', close=100.0, missing_rate=0.7, seed=99
        )
        result = analyze(data)
        assert 0.0 <= result.total_score <= 100.0
        assert result.rating in RATING_THRESHOLDS

    def test_analyze_minimal_required_fields_only(self):
        """仅必填字段（所有可选 None），降级机制兜底不崩溃"""
        data = _sd()  # 仅 code/market/trade_date/close
        result = analyze(data)
        assert 0.0 <= result.total_score <= 100.0
        assert result.rating in RATING_THRESHOLDS
        # 大量字段缺失应触发风险提示
        assert len(result.data_warnings) >= 1
        # score_date 由 trade_date 转换
        assert result.score_date == '2026-08-03'

    def test_analyze_hk_market_weights(self, provider):
        data = provider.generate('normal', code='00700.HK', market='HK', close=350.0, seed=42)
        result = analyze(data)
        assert result.code == '00700.HK'
        # 港股权重从 config_weights.json 加载，四维权重和为 1
        total_w = (
            result.technical_weight
            + result.fundamental_weight
            + result.sentiment_weight
            + result.capital_weight
        )
        assert total_w == pytest.approx(1.0, abs=1e-3)

    def test_analyze_boundary_exhaustive_no_crash(self, provider):
        """exhaustive 模式逐字段逐极端值，全部能被 analyze 处理"""
        batch = provider.generate(
            'boundary', boundary_mode='exhaustive', code='600519.SH', market='A', close=50.0
        )
        assert isinstance(batch, list) and len(batch) > 0
        for data in batch:
            result = analyze(data)
            assert 0.0 <= result.total_score <= 100.0
            assert result.rating in RATING_THRESHOLDS

    def test_analyze_score_consistent_with_rating(self, provider):
        """总分与评级档位区间一致"""
        data = provider.generate('normal', code='600519.SH', market='A', close=50.0, seed=42)
        result = analyze(data)
        info = RATING_THRESHOLDS[result.rating]
        assert info['min'] <= result.total_score <= info['max']
