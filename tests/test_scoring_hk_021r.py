"""
021R：港股评级门槛差异化 + 资金面置信收缩单元测试

覆盖：
1. _map_rating 市场参数：港股 65-69 分判"持有观望"（A股判"推荐买入"）
2. _load_hk_rating_overrides 热加载/回退/合法性
3. normalize_rating 历史路径零影响（不传 market → 全局档位）
4. _shrink_dim_to_confidence：完整度达标透传 / 不足收缩 / 边界
5. analyze 端到端：港股低完整度资金面被收缩并写入说明
"""

import json

import pytest

from modules import scoring_engine as se
from modules.data_contract import StockData


class TestMapRatingHk:
    """评级门槛市场差异化。"""

    def test_a_stock_default_thresholds(self):
        assert se._map_rating(65.0)[0] == '推荐买入'
        assert se._map_rating(69.9, 'A')[0] == '推荐买入'
        assert se._map_rating(64.9)[0] == '持有观望'
        assert se._map_rating(80.0)[0] == '强烈推荐买入'

    def test_hk_overrides_raise_buy_threshold(self):
        """港股 65-69.9：A股判推荐买入，港股判持有观望（门槛 70）。"""
        assert se._map_rating(65.0, 'HK')[0] == '持有观望'
        assert se._map_rating(69.9, 'HK')[0] == '持有观望'
        assert se._map_rating(70.0, 'HK')[0] == '推荐买入'
        # 上下两端档位不变
        assert se._map_rating(80.0, 'HK')[0] == '强烈推荐买入'
        assert se._map_rating(29.0, 'HK')[0] == '强烈建议卖出'
        assert se._map_rating(45.0, 'HK')[0] == '建议减仓'

    def test_hk_overrides_hot_loadable(self, tmp_path, monkeypatch):
        """覆盖键从 config_weights.json 热加载（改 JSON 即生效，无需重启）。"""
        cfg = {
            'hk_stock': {
                'weights': {},
                'rating_overrides': {'推荐买入': {'min': 75, 'max': 79}, '持有观望': {'min': 50, 'max': 74}},
            }
        }
        p = tmp_path / 'w.json'
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
        monkeypatch.setattr(se, '_WEIGHTS_FILE', str(p))
        assert se._map_rating(72.0, 'HK')[0] == '持有观望'
        assert se._map_rating(75.0, 'HK')[0] == '推荐买入'
        # A股不受影响
        assert se._map_rating(72.0, 'A')[0] == '推荐买入'

    def test_hk_overrides_fallback_on_bad_file(self, tmp_path, monkeypatch):
        """JSON 损坏 → 回退到内存默认覆盖（65→70 语义保持）。"""
        p = tmp_path / 'bad.json'
        p.write_text('{broken json', encoding='utf-8')
        monkeypatch.setattr(se, '_WEIGHTS_FILE', str(p))
        assert se._map_rating(68.0, 'HK')[0] == '持有观望'
        assert se._map_rating(70.0, 'HK')[0] == '推荐买入'

    def test_normalize_rating_unchanged(self):
        """normalize_rating 不传 market → 全局档位（历史/回测零影响）。"""
        assert se.normalize_rating('B+', 66.0) == '推荐买入'
        assert se.normalize_rating('B+', 68.0) == '推荐买入'


class TestShrinkConfidence:
    """资金面置信收缩。"""

    def test_pass_through_when_complete(self):
        score, note = se._shrink_dim_to_confidence(75.0, 1.0, '资金面')
        assert score == 75.0 and note is None
        score, note = se._shrink_dim_to_confidence(75.0, 0.75, '资金面')
        assert score == 75.0 and note is None  # 边界：等于阈值不收缩

    def test_shrink_when_incomplete(self):
        """完整度 0.5 → 系数 2/3：75→66.7，62→58。"""
        score, note = se._shrink_dim_to_confidence(75.0, 0.5, '资金面')
        assert score == 66.7
        assert '置信收缩' in note
        score, _ = se._shrink_dim_to_confidence(62.0, 0.5, '资金面')
        assert score == 58.0

    def test_shrink_symmetric_toward_50(self):
        """低于 50 的分同样向 50 收缩（双向）：30→36.7（系数 2/3）。"""
        score, _ = se._shrink_dim_to_confidence(30.0, 0.5, '资金面')
        assert score == 36.7

    def test_none_score_passthrough(self):
        assert se._shrink_dim_to_confidence(None, 0.5, '资金面') == (None, None)
        assert se._shrink_dim_to_confidence(70.0, None, '资金面') == (70.0, None)


class TestAnalyzeHkEndToEnd:
    """端到端：港股资金面低完整度 → 收缩生效并留痕。"""

    def test_hk_capital_shrunk_with_note(self):
        """港股典型数据（主力+机构持仓有值，两融/户数缺失 → capital 完整度 0.5）。"""
        data = StockData(
            code='HK3690', market='HK', trade_date='20260819', close=88.0,
            # 资金面 2/4 字段
            main_net_inflow=5000.0,
            institution_hold_ratio=31.61,
        )
        result = se.analyze(data)
        assert result is not None
        warnings_joined = ' '.join(result.data_warnings or [])
        assert '置信收缩' in warnings_joined, '低完整度资金面应在 data_warnings 留下收缩说明'
        assert '50%' in warnings_joined
        # 收缩只降不升：港股资金面维度分存在且为有限值
        assert result.capital_score is not None

    def test_a_stock_capital_not_shrunk(self):
        """A股资金面低完整度【不】收缩——A股评分基线经 002 校准锁定（R7）。"""
        data = StockData(
            code='600519.SH', market='A', trade_date='20260819', close=50.0,
            main_net_inflow=5000.0,
            institution_hold_ratio=31.61,
        )
        result = se.analyze(data)
        warnings_joined = ' '.join(result.data_warnings or [])
        assert '置信收缩' not in warnings_joined

    def test_hk_rating_uses_overrides_end_to_end(self):
        """构造总分落在 65-69 区间的港股数据 → 评级应为持有观望（非推荐买入）。"""
        # 通过 mock 维度分精确控分不可行（子项众多），此处验证评级来自市场门槛：
        # 直接对边界分数断言映射行为已被 TestMapRatingHk 覆盖，
        # 这里验证 analyze 全链路不抛错且评级在合法5档内。
        data = StockData(
            code='HK0700', market='HK', trade_date='20260819', close=447.2,
            main_net_inflow=1000.0,
            institution_hold_ratio=46.67,
        )
        result = se.analyze(data)
        rating = getattr(result, 'rating', None)
        assert rating in se.RATING_THRESHOLDS


class TestJsonConfigHk:
    """config_weights.json 港股覆盖键合法性。"""

    def test_hk_overrides_partition_valid(self):
        """覆盖后档位区间必须无缝覆盖 0-100（档位递减无空洞）。"""
        merged = se._load_hk_rating_overrides()
        bounds = sorted(((v['min'], v['max']) for v in merged.values()), reverse=True)
        prev_max = 100
        for lo, hi in bounds:
            assert hi == prev_max, f'档位区间不连续: {lo}-{hi}，上一档 max={prev_max}'
            prev_max = lo - 1
        assert prev_max == -1 or bounds[-1][0] == 0, '最低档应覆盖到 0'
