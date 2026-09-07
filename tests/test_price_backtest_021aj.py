"""
021AJ：价格建议市场校准 + 命中口径修复单元测试

覆盖：
1. _check_hit 口径修复：无目标价/无买入区间的行不再被计为"未命中"（保持 None）
2. 无持仓目标价封顶：A股 +7.5% 上限；港股不封顶（沿用 boll/ma60）
3. 有持仓止损校准：A股统一 -11%（与评级无关）；港股沿用评级分档（建议减仓 -4%）
4. price_advisor / price_backtest 双模块同步（同输入同输出、常量一致）
"""

import pytest

from modules import price_advisor as pa
from modules import price_backtest as pb


def _kline(days):
    """构造连续K线：[(high, low)]，价格平稳在 100 附近。"""
    return [{'high': h, 'low': lo, 'close': 100.0} for h, lo in days]


class TestCheckHitMetricFix:
    """021AJ 口径修复：None 价位不进'未命中'分母。"""

    def test_target_none_stays_none(self):
        advice = {'target_price': None, 'stop_loss': 90.0, 'take_profit': None,
                  'buy_range_low': None, 'buy_range_high': None}
        r = pb._check_hit(_kline([(101, 99)] * 5), advice, 't20')
        assert r['t20_hit_target'] is None  # 旧口径此处为 0（稀释命中率）
        assert r['t20_hit_stop_loss'] == 0  # 有止损价 → 正常判定（未触发=0）

    def test_buy_range_none_stays_none(self):
        advice = {'target_price': 108.0, 'stop_loss': 90.0, 'take_profit': None,
                  'buy_range_low': None, 'buy_range_high': None}
        r = pb._check_hit(_kline([(101, 99)] * 5), advice, 't20')
        assert r['t20_hit_buy_range'] is None

    def test_hit_target_still_counts_when_present(self):
        advice = {'target_price': 105.0, 'stop_loss': 90.0,
                  'buy_range_low': 95.0, 'buy_range_high': 102.0}
        r = pb._check_hit(_kline([(106, 99)] + [(101, 99)] * 4), advice, 't20')
        assert r['t20_hit_target'] == 1
        assert r['t20_hit_stop_loss'] == 0
        assert r['t20_hit_buy_range'] == 1

    def test_stop_loss_hit_counted(self):
        advice = {'target_price': 105.0, 'stop_loss': 99.0,
                  'buy_range_low': 95.0, 'buy_range_high': 102.0}
        r = pb._check_hit(_kline([(101, 98)] + [(101, 99)] * 4), advice, 't20')
        assert r['t20_hit_stop_loss'] == 1


class TestTargetCap:
    """无持仓目标价封顶（A股 +7.5%；港股不封顶）。"""

    def test_a_stock_capped(self):
        # boll=120 / ma60=118 → 原目标 120，A股封顶 107.5
        a = pb._gen_no_position(100.0, '持有观望', 101.0, 118.0, 120.0, 95.0, 2.0, 'a_stock')
        assert a['target_price'] == pytest.approx(107.5, abs=0.01)

    def test_a_stock_floor_kept(self):
        # boll=101 → 原目标 max(101, 105)=105，封顶 107.5 不影响
        a = pb._gen_no_position(100.0, '持有观望', 101.0, 101.0, 101.0, 95.0, 2.0, 'a_stock')
        assert a['target_price'] == pytest.approx(105.0, abs=0.01)

    def test_hk_capped_11(self):
        # 021AL：港股封顶 +11%（波幅约 A股 1.5 倍）——boll=120 → 111.0
        a = pb._gen_no_position(100.0, '持有观望', 101.0, 118.0, 120.0, 95.0, 2.0, 'hk_stock')
        assert a['target_price'] == pytest.approx(111.0, abs=0.01)

    def test_hk_floor_kept(self):
        # 021AL：目标下限 105 仍生效（boll=101 → max(101,105)=105 < 111 不受封顶影响）
        a = pb._gen_no_position(100.0, '持有观望', 101.0, 101.0, 101.0, 95.0, 2.0, 'hk_stock')
        assert a['target_price'] == pytest.approx(105.0, abs=0.01)

    def test_no_market_defaults_a_stock(self):
        a = pb._gen_no_position(100.0, '持有观望', 101.0, 118.0, 120.0, 95.0, 2.0)
        assert a['target_price'] == pytest.approx(107.5, abs=0.01)


class TestPositionStopCalibration:
    """有持仓止损校准（A股统一 -11%；021AL 港股统一 -16%）。"""

    def test_a_stock_uniform(self):
        # 建议减仓分档 -4%，A股校准覆盖为 -11%
        a = pb._gen_with_position(100.0, 98.0, '建议减仓', 105.0, 108.0, 2.0, 'a_stock')
        assert a['stop_loss'] == pytest.approx(89.0, abs=0.01)
        # 强烈推荐买入分档 -8%，同样被 -11% 覆盖
        b = pb._gen_with_position(100.0, 98.0, '强烈推荐买入', 105.0, 108.0, 2.0, 'a_stock')
        assert b['stop_loss'] == pytest.approx(89.0, abs=0.01)

    def test_hk_uniform_16(self):
        # 021AL：港股统一 -16%（持有观望分档 -5% 实测触发 40% → 校准）
        a = pb._gen_with_position(100.0, 98.0, '持有观望', 105.0, 108.0, 2.0, 'hk_stock')
        assert a['stop_loss'] == pytest.approx(84.0, abs=0.01)
        b = pb._gen_with_position(100.0, 98.0, '强烈建议卖出', 105.0, 108.0, 2.0, 'hk_stock')
        assert b['stop_loss'] == pytest.approx(84.0, abs=0.01)


class TestModuleSync:
    """price_advisor 与 price_backtest 双模块同步约束。"""

    def test_constants_equal(self):
        assert pa.TARGET_CAP == pb.TARGET_CAP
        assert pa.POSITION_STOP_PCT == pb.POSITION_STOP_PCT

    def test_no_position_outputs_match(self):
        for market in ('a_stock', 'hk_stock'):
            r1 = pa._gen_no_position(100.0, '持有观望', 101.0, 118.0, 120.0, 95.0, 2.0, market=market)
            r2 = pb._gen_no_position(100.0, '持有观望', 101.0, 118.0, 120.0, 95.0, 2.0, market)
            assert r1['target_price'] == r2['target_price']
            assert r1['stop_loss'] == r2['stop_loss']

    def test_with_position_stop_matches(self):
        for market in ('a_stock', 'hk_stock'):
            r1 = pa._gen_with_position(100.0, 98.0, '建议减仓', 105.0, 108.0, 2.0, market=market)
            r2 = pb._gen_with_position(100.0, 98.0, '建议减仓', 105.0, 108.0, 2.0, market=market)
            assert r1['stop_loss'] == r2['stop_loss']
