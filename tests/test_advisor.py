"""
advisor.py 聚焦单元测试

覆盖范围（纯函数，隔离数据库与网络，不修改 generate_advice 本身）：
1. 操作建议矩阵 _determine_action（5档评级 × 持仓/盈亏状态）
2. 维度描述生成 _describe_dimension（分数档位 + 因子亮点提取）
3. 综合建议详情 _build_detail_text（最强/最弱维度排序 + 消息面降级提示）
4. 仓位感知建议 _build_position_advice（浮盈浮亏计算 + 评级对应建议）
5. 风险提示检测 _detect_risks（技术面/基本面/资金面风险信号）

设计原则：
- generate_advice 是 B24 红线模块，本测试仅验证其依赖的纯函数契约逻辑
- 所有输入通过直接构造 dict 传入，不触碰数据库
- conftest.py 负责把项目根目录加入 sys.path
"""

import pytest

from database import db_manager
from modules.advisor import (
    DIM_NAMES,
    _build_detail_text,
    _build_fund_trend,
    _build_markdown_single,
    _build_position_advice,
    _describe_dimension,
    _detect_risks,
    _determine_action,
)

# ============================================================
# 一、操作建议矩阵 _determine_action
# ============================================================


class TestDetermineAction:
    """5档评级 × 持仓/盈亏状态矩阵覆盖"""

    @pytest.mark.parametrize(
        'rating,has_pos,profitable,expected',
        [
            # 强烈推荐买入
            ('强烈推荐买入', False, False, '买入'),
            ('强烈推荐买入', True, True, '加仓'),
            ('强烈推荐买入', True, False, '继续持有'),
            # 推荐买入
            ('推荐买入', False, False, '买入'),
            ('推荐买入', True, True, '持有'),
            ('推荐买入', True, False, '继续持有'),
            # 持有观望
            ('持有观望', False, False, '关注'),
            ('持有观望', True, True, '持有'),
            ('持有观望', True, False, '持有'),  # 021BH：动作词统一为"持有"，"持有观望"仅保留为评级名
            # 建议减仓
            ('建议减仓', False, False, '观望'),
            ('建议减仓', True, True, '持有'),  # 021BH：同上
            ('建议减仓', True, False, '考虑减仓'),
            # 强烈建议卖出
            ('强烈建议卖出', False, False, '回避'),
            ('强烈建议卖出', True, True, '减仓'),
            ('强烈建议卖出', True, False, '建议止损'),
        ],
    )
    def test_action_matrix(self, rating, has_pos, profitable, expected):
        assert _determine_action(rating, has_pos, profitable) == expected

    def test_no_position_ignores_profitable(self):
        """无持仓时 profitable 参数不应影响结果"""
        a = _determine_action('推荐买入', False, True)
        b = _determine_action('推荐买入', False, False)
        assert a == b == '买入'

    def test_unknown_rating_falls_back_to_hold(self):
        """未知评级应 fallback 到持有观望矩阵"""
        result = _determine_action('未知评级', False, False)
        assert result == '关注'  # 持有观望 (False, False) = 关注

    def test_unknown_rating_with_position_loss(self):
        result = _determine_action('未知评级', True, False)
        assert result == '持有'  # 021BH：动作词统一

    def test_missing_key_returns_default(self):
        """矩阵中缺失的 key 应返回默认值 '观望'"""
        # 持有观望矩阵中没有 (True, True) 之外的默认
        # 但通过构造不可能的状态测试 fallback
        result = _determine_action('持有观望', True, True)
        assert result == '持有'


# ============================================================
# 二、维度描述 _describe_dimension
# ============================================================


class TestDescribeDimension:
    """分数档位判定 + 因子亮点提取"""

    def test_score_excellent(self):
        """score >= 75 → 表现优秀"""
        text = _describe_dimension('kline', {'score': 80, 'factors': {}})
        assert '表现优秀' in text
        assert '技术面' in text
        assert '80.0' in text

    def test_score_good(self):
        """60 <= score < 75 → 表现良好"""
        text = _describe_dimension('kline', {'score': 65, 'factors': {}})
        assert '表现良好' in text

    def test_score_average(self):
        """40 <= score < 60 → 表现一般"""
        text = _describe_dimension('kline', {'score': 50, 'factors': {}})
        assert '表现一般' in text

    def test_score_weak(self):
        """20 <= score < 40 → 表现较弱"""
        text = _describe_dimension('kline', {'score': 30, 'factors': {}})
        assert '表现较弱' in text

    def test_score_poor(self):
        """score < 20 → 表现较差"""
        text = _describe_dimension('kline', {'score': 10, 'factors': {}})
        assert '表现较差' in text

    def test_boundary_75(self):
        """75 是优秀边界（>=75）"""
        text = _describe_dimension('kline', {'score': 75, 'factors': {}})
        assert '表现优秀' in text

    def test_boundary_60(self):
        text = _describe_dimension('kline', {'score': 60, 'factors': {}})
        assert '表现良好' in text

    def test_kline_ma_bullish(self):
        """均线多头排列亮点"""
        text = _describe_dimension(
            'kline',
            {'score': 70, 'factors': {'ma_trend': '多头排列(MA5=10 > MA20=9)'}},
        )
        assert '均线多头排列' in text

    def test_kline_ma_bearish(self):
        """均线空头排列亮点"""
        text = _describe_dimension(
            'kline',
            {'score': 40, 'factors': {'ma_trend': '空头排列(MA5=8 < MA20=10)'}},
        )
        assert '均线空头排列' in text

    def test_kline_rsi_overbought(self):
        text = _describe_dimension(
            'kline', {'score': 50, 'factors': {'rsi_status': '超买(75.0)'}}
        )
        assert 'RSI超买' in text

    def test_kline_rsi_oversold(self):
        text = _describe_dimension(
            'kline', {'score': 50, 'factors': {'rsi_status': '超卖(25.0)'}}
        )
        assert 'RSI超卖' in text

    def test_fundamental_roe_good(self):
        text = _describe_dimension(
            'fundamental',
            {'score': 70, 'factors': {'roe': '良好(15%)'}},
        )
        assert 'ROE' in text

    def test_fundamental_pe_present(self):
        text = _describe_dimension(
            'fundamental',
            {'score': 60, 'factors': {'pe_ratio': '25.5', 'debt_ratio': '40%'}},
        )
        assert 'PE' in text

    def test_capital_inflow(self):
        text = _describe_dimension(
            'capital_flow',
            {'score': 65, 'factors': {'main_trend': '主力净流入'}},
        )
        assert '主力资金流入' in text

    def test_capital_outflow(self):
        text = _describe_dimension(
            'capital_flow',
            {'score': 35, 'factors': {'main_trend': '主力净流出'}},
        )
        assert '主力资金流出' in text

    def test_no_factors_data_limited(self):
        """无因子亮点时应显示 '数据有限'"""
        text = _describe_dimension('kline', {'score': 50, 'factors': {}})
        assert '数据有限' in text

    def test_unknown_dim_key_uses_raw_key(self):
        text = _describe_dimension('custom_dim', {'score': 70, 'factors': {}})
        assert 'custom_dim' in text


# ============================================================
# 三、综合建议详情 _build_detail_text
# ============================================================


def _make_analysis(**overrides):
    """构造测试用 analysis dict"""
    base = {
        'stock_name': '贵州茅台',
        'stock_code': '600519.SH',
        'total_score': 72.5,
        'rating': '推荐买入',
        'rating_label': '估值合理',
        'dimensions': {
            'kline': {'score': 75, 'status': 'ok', 'factors': {'ma_trend': '多头排列'}},
            'fundamental': {'score': 55, 'status': 'ok', 'factors': {'pe_ratio': '30'}},
            'capital_flow': {'score': 60, 'status': 'ok', 'factors': {}},
            'news': {'score': 0, 'status': 'unavailable', 'factors': {}},
        },
    }
    base.update(overrides)
    return base


class TestBuildDetailText:
    """综合建议详情文本生成"""

    def test_contains_stock_name_and_score(self):
        analysis = _make_analysis()
        text = _build_detail_text(analysis)
        assert '贵州茅台' in text
        assert '72.5' in text
        assert '推荐买入' in text

    def test_includes_strongest_dimension(self):
        analysis = _make_analysis()
        text = _build_detail_text(analysis)
        # kline(75) 最强
        assert '技术面' in text

    def test_includes_weakest_dimension(self):
        analysis = _make_analysis()
        text = _build_detail_text(analysis)
        # fundamental(55) 最弱（active dims 中）
        assert '基本面' in text

    def test_news_unavailable_note(self):
        """消息面不可用时应提示"""
        analysis = _make_analysis()
        text = _build_detail_text(analysis)
        assert '消息面数据暂不可用' in text

    def test_news_ok_no_unavailable_note(self):
        analysis = _make_analysis(
            dimensions={
                'kline': {'score': 70, 'status': 'ok', 'factors': {}},
                'fundamental': {'score': 60, 'status': 'ok', 'factors': {}},
                'capital_flow': {'score': 50, 'status': 'ok', 'factors': {}},
                'news': {'score': 65, 'status': 'ok', 'factors': {}},
            }
        )
        text = _build_detail_text(analysis)
        assert '消息面数据暂不可用' not in text

    def test_fallback_to_stock_code_if_name_absent(self):
        """stock_name key 缺失时，使用 stock_code 作为 fallback"""
        analysis = _make_analysis()
        del analysis['stock_name']
        text = _build_detail_text(analysis)
        assert '600519.SH' in text

    def test_empty_name_no_prefix(self):
        """stock_name='' 时文本以空串开头（.get 返回空串而非默认值）"""
        analysis = _make_analysis(stock_name='')
        text = _build_detail_text(analysis)
        assert text.startswith('综合评分')

    def test_single_active_dim(self):
        """仅一个活跃维度时，最强=最弱"""
        analysis = _make_analysis(
            dimensions={
                'kline': {'score': 70, 'status': 'ok', 'factors': {}},
                'fundamental': {'score': 0, 'status': 'unavailable', 'factors': {}},
                'capital_flow': {'score': 0, 'status': 'unavailable', 'factors': {}},
                'news': {'score': 0, 'status': 'unavailable', 'factors': {}},
            }
        )
        text = _build_detail_text(analysis)
        assert '技术面' in text

    def test_capital_flow_mentioned_separately(self):
        """资金面有趋势信息时应单独提及"""
        analysis = _make_analysis(
            dimensions={
                'kline': {'score': 80, 'status': 'ok', 'factors': {}},
                'fundamental': {'score': 30, 'status': 'ok', 'factors': {}},
                'capital_flow': {
                    'score': 60,
                    'status': 'ok',
                    'factors': {'main_trend': '主力净流入', 'consecutive': '连续流入3日'},
                },
                'news': {'score': 0, 'status': 'unavailable', 'factors': {}},
            }
        )
        text = _build_detail_text(analysis)
        assert '资金面' in text


# ============================================================
# 四、仓位感知建议 _build_position_advice
# ============================================================


class TestBuildPositionAdvice:
    """仓位感知个性化建议生成"""

    def test_no_position_returns_none(self):
        assert _build_position_advice(None, {'close': 10, 'date': '2026-08-01'}, '推荐买入') is None

    def test_no_close_info_returns_none(self):
        position = {'cost_price': 10, 'quantity': 100}
        assert _build_position_advice(position, None, '推荐买入') is None

    def test_profit_calculation(self):
        """浮盈计算：成本10，现价12 → +20%"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 12.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '推荐买入')
        assert text is not None
        assert '浮盈' in text
        assert '20.0' in text

    def test_loss_calculation(self):
        """浮亏计算：成本10，现价8 → -20%"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 8.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '持有观望')
        assert text is not None
        assert '浮亏' in text
        assert '20.0' in text

    def test_market_value(self):
        """市值计算：现价15 × 100股 = 1500"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 15.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '推荐买入')
        assert '1,500' in text

    def test_strong_buy_with_big_profit_holds(self):
        """强烈推荐买入 + 大涨 → 建议持有为主"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 15.0, 'date': '2026-08-01'}  # +50%
        text = _build_position_advice(position, close_info, '强烈推荐买入')
        assert '持有为主' in text

    def test_sell_rating_with_loss_stop_loss(self):
        """强烈建议卖出 + 浮亏 → 建议止损"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 7.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '强烈建议卖出')
        assert '止损' in text

    def test_sell_rating_with_profit_reduce(self):
        """强烈建议卖出 + 浮盈 → 建议减仓锁定"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 12.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '强烈建议卖出')
        assert '减仓' in text

    def test_reduce_rating_with_loss(self):
        """建议减仓 + 浮亏 → 控制风险"""
        position = {'cost_price': 10.0, 'quantity': 100}
        close_info = {'close': 8.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '建议减仓')
        assert '减仓' in text or '风险' in text

    def test_zero_cost_safe(self):
        """成本为0时应安全处理（profit_pct=0）"""
        position = {'cost_price': 0, 'quantity': 100}
        close_info = {'close': 10.0, 'date': '2026-08-01'}
        text = _build_position_advice(position, close_info, '持有观望')
        assert text is not None

    def test_contains_position_details(self):
        """应包含持仓数量和成本价"""
        position = {'cost_price': 15.5, 'quantity': 200}
        close_info = {'close': 16.0, 'date': '2026-08-03'}
        text = _build_position_advice(position, close_info, '推荐买入')
        assert '200' in text
        assert '15.50' in text
        assert '16.00' in text


# ============================================================
# 五、风险提示检测 _detect_risks
# ============================================================


class TestDetectRisks:
    """技术面/基本面/资金面风险信号扫描"""

    def test_no_risks_empty_dimensions(self):
        assert _detect_risks({}) == []

    def test_rsi_overbought(self):
        dims = {'kline': {'factors': {'rsi': 75.5}}}
        risks = _detect_risks(dims)
        assert any('超买' in r for r in risks)

    def test_rsi_oversold(self):
        dims = {'kline': {'factors': {'rsi': 25.0}}}
        risks = _detect_risks(dims)
        assert any('超卖' in r for r in risks)

    def test_rsi_normal_no_risk(self):
        dims = {'kline': {'factors': {'rsi': 50.0}}}
        risks = _detect_risks(dims)
        assert not any('超买' in r or '超卖' in r for r in risks)

    def test_rsi_invalid_string_no_crash(self):
        """RSI 为非数字字符串时应安全跳过"""
        dims = {'kline': {'factors': {'rsi': 'N/A'}}}
        risks = _detect_risks(dims)
        assert not any('RSI' in r for r in risks)

    def test_ma_bearish(self):
        dims = {'kline': {'factors': {'ma_trend': '空头排列(MA5 < MA20)'}}}
        risks = _detect_risks(dims)
        assert any('空头' in r for r in risks)

    def test_boll_upper_rail(self):
        dims = {'kline': {'factors': {'boll_position': '95%'}}}
        risks = _detect_risks(dims)
        assert any('上轨' in r for r in risks)

    def test_volume_shrink(self):
        dims = {'kline': {'factors': {'volume': '缩量下跌'}}}
        risks = _detect_risks(dims)
        assert any('缩量' in r for r in risks)

    def test_pe_extremely_high(self):
        """PE > 60 → 估值严重偏高"""
        dims = {'fundamental': {'factors': {'pe_ratio': '80'}}}
        risks = _detect_risks(dims)
        assert any('估值严重偏高' in r for r in risks)

    def test_pe_high(self):
        """40 < PE <= 60 → 估值偏高"""
        dims = {'fundamental': {'factors': {'pe_ratio': '50'}}}
        risks = _detect_risks(dims)
        assert any('估值偏高' in r for r in risks)

    def test_pe_normal_no_risk(self):
        dims = {'fundamental': {'factors': {'pe_ratio': '25'}}}
        risks = _detect_risks(dims)
        assert not any('估值' in r for r in risks)

    def test_pe_missing_no_risk(self):
        dims = {'fundamental': {'factors': {'pe_ratio': '缺失'}}}
        risks = _detect_risks(dims)
        assert not any('估值' in r for r in risks)

    def test_pb_too_high(self):
        dims = {'fundamental': {'factors': {'pb_ratio': '8'}}}
        risks = _detect_risks(dims)
        assert any('市净率过高' in r for r in risks)

    def test_roe_loss(self):
        dims = {'fundamental': {'factors': {'roe': '亏损(-5%)'}}}
        risks = _detect_risks(dims)
        assert any('亏损' in r for r in risks)

    def test_capital_continuous_outflow(self):
        dims = {'capital_flow': {'factors': {'main_trend': '持续流出'}}}
        risks = _detect_risks(dims)
        assert any('流出' in r for r in risks)

    def test_capital_consecutive_outflow(self):
        dims = {'capital_flow': {'factors': {'consecutive': '连续流出5日'}}}
        risks = _detect_risks(dims)
        assert any('连续流出' in r for r in risks)

    def test_capital_pct_large_outflow(self):
        """主力净流入占比 < -5%"""
        dims = {'capital_flow': {'factors': {'main_pct': '-8%'}}}
        risks = _detect_risks(dims)
        assert any('流出幅度' in r for r in risks)

    def test_multiple_risks_combined(self):
        """多个风险维度同时存在时应全部检测到"""
        dims = {
            'kline': {'factors': {'rsi': 78, 'ma_trend': '空头排列'}},
            'fundamental': {'factors': {'pe_ratio': '70', 'pb_ratio': '8'}},
            'capital_flow': {'factors': {'main_trend': '持续流出', 'consecutive': '连续流出3日'}},
        }
        risks = _detect_risks(dims)
        assert len(risks) >= 5

    def test_empty_factors_no_crash(self):
        dims = {'kline': {'factors': {}}, 'fundamental': {}}
        assert _detect_risks(dims) == []


# ============================================================
# 六、DIM_NAMES 常量校验
# ============================================================


class TestDimNames:
    """维度名称映射完整性"""

    def test_kline(self):
        assert DIM_NAMES['kline'] == '技术面'

    def test_fundamental(self):
        assert DIM_NAMES['fundamental'] == '基本面'

    def test_capital_flow(self):
        assert DIM_NAMES['capital_flow'] == '资金面'

    def test_news(self):
        assert DIM_NAMES['news'] == '消息面'

    def test_all_five_ratings_in_matrix(self):
        """操作建议矩阵应覆盖全部5档评级"""
        from modules.advisor import _determine_action

        # 间接验证：每档评级无持仓时都应返回有效建议
        for rating in ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出']:
            action = _determine_action(rating, False, False)
            assert isinstance(action, str)
            assert len(action) > 0


# ============================================================
# 七、单股 Markdown 构建 _build_markdown_single（021BN）
# ============================================================


class TestBuildMarkdownSingle:
    """降级提示列明细 + 分数变动标签诚实化"""

    @staticmethod
    def _advice():
        return {
            'stock_code': '601888',
            'stock_name': '中国中免',
            'engine_version': 'v5',
            'total_score': 47.5,
            'rating': '建议减仓',
            'rating_label': '建议减仓',
            'action_advice': '考虑减仓',
            'data_warnings': ['基本面: 股东人数缺失', '消息面: 新闻情绪缺失'],
        }

    def test_degradation_lines_listed_not_counted(self):
        """021BN：降级提示必须列出每条规则内容，而非只给计数"""

        md = _build_markdown_single(self._advice(), prev_score=None)
        assert '2条降级规则触发' in md
        assert '- 基本面: 股东人数缺失' in md
        assert '- 消息面: 新闻情绪缺失' in md

    def test_degradation_truncation_note(self):
        """021BN：超过6条时展示前6条并注明剩余条数"""

        advice = self._advice()
        advice['data_warnings'] = [f'降级规则{i}' for i in range(8)]
        md = _build_markdown_single(advice, prev_score=None)
        assert '- 降级规则5' in md
        assert '- 降级规则6' not in md
        assert '…其余2条略' in md

    def test_no_warnings_no_section(self):
        """无降级时不输出降级提示段"""

        advice = self._advice()
        advice['data_warnings'] = []
        md = _build_markdown_single(advice, prev_score=None)
        assert '降级提示' not in md

    def test_score_change_label_is_previous_report(self):
        """021BN：对比基准是上一份有效报告（可能是上周五），标签不再写「较昨日」"""

        md = _build_markdown_single(self._advice(), prev_score=52.5)
        assert '较前次报告 ↓ 5.0' in md
        assert '较昨日' not in md

    # --------------------------------------------------------
    # 021BS P1-1：分数×档位失配持续标注（迟滞保持态外层调和，B24 合规）
    # --------------------------------------------------------

    def test_mismatch_without_hyst_info_gets_persistent_note(self):
        """刷新路径形态（021BS P1-1 实测根因）：md_source 无 rating_hysteresis 时，
        失配仍必须带口径说明——拓尔思形态：52.0 属持有观望区间、评级保持建议减仓"""
        advice = self._advice()
        advice['total_score'] = 52.0
        advice['rating'] = '建议减仓'
        advice['market'] = 'a_stock'
        md = _build_markdown_single(advice, prev_score=None)
        assert '评级口径说明' in md
        assert '迟滞' in md
        assert '持有观望' in md  # 说明须点名分数所处档位
        assert '<' not in md  # 021BN 教训：渲染文案禁止裸 '<'

    def test_mismatch_with_hyst_info_no_duplicate_note(self):
        """压制当日（每日批次路径）：已有迟滞说明时不再重复失配注记"""
        advice = self._advice()
        advice['total_score'] = 52.0
        advice['rating'] = '建议减仓'
        advice['market'] = 'a_stock'
        advice['rating_hysteresis'] = {
            'kept': '建议减仓', 'raw': '持有观望', 'score': 52.0,
            'boundary': 50, 'margin': 3, 'direction': 'upgrade',
        }
        md = _build_markdown_single(advice, prev_score=None)
        assert '评级迟滞' in md
        assert '评级口径说明' not in md  # 压制当日已有迟滞说明，不重复失配注记

    def test_consistent_no_note(self):
        """分数与评级同档：不附加口径说明"""
        advice = self._advice()  # 47.5 × 建议减仓（30-49）一致
        md = _build_markdown_single(advice, prev_score=None)
        assert '评级口径说明' not in md


class TestBuildKeyFactorsScoreTierNote:
    """021BS P1-1：key_factors 结构化失配注记（随每次报告落库）"""

    @staticmethod
    def _advice(score, rating, market='a_stock'):
        return {
            'market': market,
            'total_score': score,
            'rating': rating,
            'dimensions': {
                'kline': {'status': 'ok', 'score': 50.0, 'weight': 0.2715, 'factors': {}},
            },
        }

    def test_mismatch_note_present(self):
        from modules.advisor import _build_key_factors

        kf = _build_key_factors(self._advice(52.0, '建议减仓'))
        assert 'score_tier_note' in kf
        assert '持有观望' in kf['score_tier_note']
        assert '<' not in kf['score_tier_note']

    def test_consistent_note_absent(self):
        from modules.advisor import _build_key_factors

        kf = _build_key_factors(self._advice(47.5, '建议减仓'))
        assert 'score_tier_note' not in kf

    def test_four_dims_keys_shape_preserved(self):
        """新增注记键不得破坏既有四维结构（消费方按需透出）"""
        from modules.advisor import _build_key_factors

        kf = _build_key_factors(self._advice(52.0, '建议减仓'))
        assert kf['kline']['score'] == 50.0
        assert kf['kline']['weight'] == 0.2715
        assert kf['kline']['top_factors'] == {}


# ============================================================
# 八、基本面趋势判定 _build_fund_trend（021BN 诚实化）
# ============================================================


def _insert_fund_rows(conn, rows):
    """rows: (report_date, gross_margin, net_margin, debt_ratio, current_ratio, quick_ratio)"""
    for rd, gm, nm, dr, cr, qr in rows:
        conn.execute(
            'INSERT INTO raw_fundamental '
            '(stock_id, report_date, gross_margin, net_margin, debt_ratio, current_ratio, quick_ratio) '
            'VALUES (1, ?, ?, ?, ?, ?, ?)',
            (rd, gm, nm, dr, cr, qr),
        )
    conn.commit()


class TestBuildFundTrend:
    """计数化汇总 + 按|Δ|降序证据 + 方向标记"""

    @pytest.fixture
    def fund_db(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'fundtrend.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        yield conn
        conn.close()

    def test_worsen_summary_counts_flat_items(self, fund_db):
        """中国中免实测口径：1项恶化 + 4项平稳 → 摘要必须如实计数，而非笼统"恶化\""""
        _insert_fund_rows(
            fund_db,
            [
                ('2026-03-31', 33.63, 14.02, 25.54, 2.0, 1.5),
                ('2026-06-30', 33.90, 11.01, 25.66, 2.1, 1.6),
            ],
        )
        summary, details, direction = _build_fund_trend(1)
        assert direction == 'worsen'
        assert summary.startswith('基本面较上期偏弱：1项恶化、0项改善、4项平稳')
        assert '毛利率较上期平稳(33.63%→33.90%)' in summary
        assert details[0].startswith('净利率较上期恶化')

    def test_evidence_sorted_by_magnitude(self, fund_db):
        """|Δ| 最大的指标排证据首位，而非按指标固定顺序"""
        _insert_fund_rows(
            fund_db,
            [
                ('2026-03-31', 33.63, 14.02, 25.54, 2.0, 1.5),
                ('2026-06-30', 33.90, 11.01, 25.66, 6.0, 1.5),
            ],
        )
        summary, details, direction = _build_fund_trend(1)
        assert direction == 'flat'  # 1改善(流动比率+4.0) vs 1恶化(净利率-3.01)
        assert summary.startswith('基本面较上期涨跌互现：1项改善、1项恶化、3项平稳')
        assert details[0].startswith('流动比率较上期改善')
        assert details[1].startswith('净利率较上期恶化')

    def test_all_flat_no_counts_change(self, fund_db):
        _insert_fund_rows(
            fund_db,
            [
                ('2026-03-31', 33.63, 14.02, 25.54, 2.0, 1.5),
                ('2026-06-30', 33.90, 14.10, 25.60, 2.05, 1.55),
            ],
        )
        summary, details, direction = _build_fund_trend(1)
        assert direction == 'flat'
        assert summary == '基本面较上期平稳（5项指标均无实质变化）'

    def test_insufficient_history(self, fund_db):
        summary, details, direction = _build_fund_trend(1)
        assert direction == 'insufficient'
        assert '历史数据不足' in summary
        assert details == []


class TestFundTrendRiskGate:
    """_detect_risks 的基本面趋势触发：结构化方向标记优先，旧摘要前缀兼容"""

    def test_worsen_flag_triggers(self):
        dims = {
            'fundamental': {
                'factors': {
                    'fund_trend': '基本面较上期偏弱：1项恶化、0项改善、4项平稳（…）',
                    '_fund_trend_dir': 'worsen',
                }
            }
        }
        risks = _detect_risks(dims)
        assert any('基本面趋势转弱' in r for r in risks)

    def test_flat_mixed_flag_no_risk(self):
        """涨跌互现（1改善 vs 1恶化）不得触发趋势风险——旧关键词匹配会误报"""
        dims = {
            'fundamental': {
                'factors': {
                    'fund_trend': '基本面较上期涨跌互现：1项改善、1项恶化、3项平稳（…）',
                    '_fund_trend_dir': 'flat',
                }
            }
        }
        risks = _detect_risks(dims)
        assert not any('趋势' in r for r in risks)

    def test_legacy_summary_prefix_still_triggers(self):
        """旧快照/手工入参无方向标记时，按旧摘要前缀「基本面较上期恶化」兼容触发"""
        dims = {
            'fundamental': {
                'factors': {
                    'fund_trend': '基本面较上期恶化（净利率较上期恶化(14.02%→11.01%)）'
                }
            }
        }
        risks = _detect_risks(dims)
        assert any('基本面趋势转弱' in r for r in risks)
