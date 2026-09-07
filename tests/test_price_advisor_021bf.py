"""
021BF：无持仓价格建议的评级感知修复

修复：评级为建议减仓/强烈建议卖出（仓位0%）时，原实现仍输出
"当前价低于买入区间，可逢低买入"话术和 3 档买入网格——与减仓语义直接矛盾
（宁德时代实际案例：评级建议减仓、现价 358.10 低于买入区间 361.76-376.01）。

覆盖：
1. 减仓/卖出评级：zone_label=支撑参考区间，话术含"不建议买入"，网格为支撑观察梯队（无买入档）
2. 买入评级：zone_label=买入区间，正常买入话术，3档买入网格（行为不变）
3. 观望评级：zone_label=参考区间，20% 仓位 + 网格保留
4. 矛盾场景闭环：现价跌破区间下沿 + 减仓评级 → 不得出现"可逢低买入"
"""

from modules import price_advisor as pa


class TestRatingAwareNoPosition:
    def _gen(self, rating, close=10.0):
        return pa._gen_no_position(
            close, rating, ma20=10.0, ma60=11.0,
            boll_upper=11.5, boll_lower=9.0, atr=0.5,
        )

    def test_reduce_rating_zone_and_action(self):
        r = self._gen('建议减仓')
        assert r['zone_label'] == '支撑参考区间'
        assert '不建议买入' in r['action_suggestion']
        assert '建议减仓' in r['action_suggestion']
        assert r['position_pct'] == 0

    def test_reduce_rating_support_watch_grid(self):
        # 021BG：减仓/卖出评级给"支撑观察梯队"（观察预案），但绝无买入档
        for rating in ('建议减仓', '强烈建议卖出'):
            r = self._gen(rating)
            assert 1 <= len(r['grid']) <= 3
            assert all(g['type'] == 'watch' for g in r['grid'])
            assert all(g['pct'] is None for g in r['grid'])
            assert all(g['price'] < r['current_close'] for g in r['grid'])
            # 严格自上而下递减
            prices = [g['price'] for g in r['grid']]
            assert prices == sorted(prices, reverse=True)

    def test_support_watch_grid_builder(self):
        # 真实低点优先 + ATR 外推补足 + 严格递减
        g = pa._build_support_watch_grid(10.0, 9.8, 0.5, low20=9.1, low60=8.2)
        assert [x['price'] for x in g] == [9.8, 9.1, 8.2]
        assert all(x['type'] == 'watch' for x in g)
        # 区间下沿已跌破（在现价上方）时不入选，从现价下方补位
        g2 = pa._build_support_watch_grid(10.0, 10.5, 0.5)
        assert all(x['price'] < 10.0 for x in g2)
        assert len(g2) == 3  # ATR 外推补足 3 档
        # 无 ATR 回退（百分比步长）
        g3 = pa._build_support_watch_grid(10.0, None, None)
        assert len(g3) == 3

    def test_strong_sell_action(self):
        r = self._gen('强烈建议卖出')
        assert '强烈建议卖出' in r['action_suggestion']
        assert '不建议买入' in r['action_suggestion']
        assert r['position_pct'] == 0

    def test_buy_rating_unchanged(self):
        r = self._gen('推荐买入')
        assert r['zone_label'] == '买入区间'
        assert '买入' in r['action_suggestion']
        assert len(r['grid']) == 3  # 三档买入位保留
        assert all(g['type'] == 'buy' for g in r['grid'])

    def test_watch_rating_reference_label(self):
        r = self._gen('持有观望')
        assert r['zone_label'] == '参考区间'
        assert r['position_pct'] == 20
        assert len(r['grid']) == 3

    def test_contradiction_scenario_closed(self):
        # 宁德时代案例复现：现价跌破区间下沿 + 减仓评级 → 不得出现"可逢低买入"
        # buy_low = max约束后 ≈ 9.0（boll_lower），close=8.8 < 9.0
        r = self._gen('建议减仓', close=8.8)
        assert r['current_close'] < r['buy_range_low']
        assert '可逢低买入' not in r['action_suggestion']
        assert '不建议买入' in r['action_suggestion']

    def test_zone_label_present_for_all_ratings(self):
        for rating in ('强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'):
            assert 'zone_label' in self._gen(rating)
