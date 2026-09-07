"""
021AQ：浮亏持仓网格重构——两级止损 + 分批补仓 + 分批回本

直接测试 price_advisor._build_grid（price_backtest 仅锚定补仓一档公式，
其口径由 test_price_backtest_021aj 守护，此处不重复）。

场景基线（示例）：close=10.0，cost=12.0（浮亏），ATR=0.3，
stop_loss=8.9（A股 -11%），take_profit 视场景。
"""

import pytest

from modules import price_advisor as pa


def _grid(close, cost, tp, stop, atr=0.3, state='S3'):
    return pa._build_grid(
        close, None, None, atr, cost, tp, stop, '持有观望',
        has_position=True, state=state,
    )


def _labels(grid):
    return [g['label'] for g in grid]


def _assert_ascending(grid):
    prices = [g['price'] for g in grid]
    assert prices == sorted(prices), f'档位须自下而上递增: {prices}'


class TestUnderwaterTargetBelowCost:
    """浮亏 + 止盈目标低于回本价（原'回本清仓一把梭'场景）"""

    def test_staged_breakeven_exit(self):
        # tp=11.5 < cost=12：回本减仓50% @12 + 回本清仓100% @max(12+0.18, 12.24)=12.24
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9)
        labels = _labels(g)
        assert '回本减仓位' in labels and '回本清仓位' in labels
        rb = next(x for x in g if x['label'] == '回本减仓位')
        rc = next(x for x in g if x['label'] == '回本清仓位')
        assert rb['pct'] == 50 and rb['price'] == pytest.approx(12.0, abs=0.01)
        assert rc['pct'] == 100 and rc['price'] == pytest.approx(12.24, abs=0.01)

    def test_two_stage_stop(self):
        # 破位减仓50% @止损8.9；止损清仓100% @max(8.9-0.3, 8.544)=8.6
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9)
        pb = next(x for x in g if x['label'] == '破位减仓位')
        sc = next(x for x in g if x['label'] == '止损清仓位')
        assert pb['pct'] == 50 and pb['price'] == pytest.approx(8.9, abs=0.01)
        assert sc['pct'] == 100 and sc['price'] == pytest.approx(8.6, abs=0.01)

    def test_staged_adds_ordered_ascending(self):
        # 一档 max(8.9+0.15, 10-0.3)=9.7；二档 max(9.05, 10-0.66)=9.34 ≤ 9.7-0.09 → 两档
        # 列表按价格升序：二档(9.34)在一档(9.7)之前
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9)
        adds = [x for x in g if x['type'] == 'add']
        assert len(adds) == 2
        assert adds[0]['label'] == '补仓二档' and adds[0]['price'] == pytest.approx(9.34, abs=0.01)
        assert adds[1]['label'] == '补仓一档' and adds[1]['price'] == pytest.approx(9.7, abs=0.01)
        assert adds[0]['pct'] == 15 and adds[1]['pct'] == 10
        _assert_ascending(g)

    def test_second_add_skipped_when_floor_flattens(self):
        # stop=8.0, ATR=1.0：一档 max(8.5, 9.0)=9.0；二档 max(8.5, 7.8)=8.5 ≤ 8.7 → 两档；
        # 再构造被地板抬平场景：stop=9.3, ATR=1.0：一档 max(9.8, 9.0)=9.8，
        # 二档 max(9.8, 7.8)=9.8 > 9.8-0.3 → 跳过二档
        g2 = _grid(10.0, 12.0, tp=11.5, stop=9.3, atr=1.0)
        adds2 = [x for x in g2 if x['type'] == 'add']
        assert len(adds2) == 1
        assert adds2[0]['label'] == '补仓一档'
        assert adds2[0]['price'] == pytest.approx(9.8, abs=0.01)
        _assert_ascending(g2)

    def test_levels_numbered(self):
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9)
        assert [g_['level'] for g_ in g] == list(range(1, len(g) + 1))


class TestUnderwaterTargetAboveCost:
    """浮亏 + 止盈目标高于回本价：保留回本减仓30% → 第一止盈 → 最终止盈"""

    def test_recovery_ladder_kept(self):
        g = _grid(10.0, 12.0, tp=13.0, stop=8.9)
        labels = _labels(g)
        assert '回本减仓位' in labels and '第一止盈位' in labels and '最终止盈位' in labels
        rb = next(x for x in g if x['label'] == '回本减仓位')
        assert rb['pct'] == 30 and rb['price'] == pytest.approx(12.0, abs=0.01)
        # 两级止损与两档补仓仍在
        assert '破位减仓位' in labels and '止损清仓位' in labels
        assert sum(1 for x in g if x['type'] == 'add') == 2
        _assert_ascending(g)


class TestProfitAndEdge:
    """浮盈路径不受影响 + 边界场景"""

    def test_profit_path_unchanged(self):
        # 浮盈：两级止损/补仓档照常 + 第一止盈/最终止盈，无回本档
        g = _grid(10.0, 8.0, tp=11.0, stop=8.9)
        labels = _labels(g)
        assert '第一止盈位' in labels and '最终止盈位' in labels
        assert '回本减仓位' not in labels and '回本清仓位' not in labels
        tp1 = next(x for x in g if x['label'] == '第一止盈位')
        assert tp1['price'] == pytest.approx(10.18, abs=0.01)  # close+0.6ATR
        _assert_ascending(g)

    def test_no_atr_fallbacks(self):
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9, atr=None)
        labels = _labels(g)
        # 无 ATR：补仓二档跳过；止损清仓 = 止损×0.96；回本清仓 = 回本×1.03
        assert sum(1 for x in g if x['type'] == 'add') == 1
        sc = next(x for x in g if x['label'] == '止损清仓位')
        assert sc['price'] == pytest.approx(8.54, abs=0.01)
        rc = next(x for x in g if x['label'] == '回本清仓位')
        assert rc['price'] == pytest.approx(12.36, abs=0.01)
        _assert_ascending(g)

    def test_s4_defensive_exit_only(self):
        # S4 防御分支：只给离场档无补仓；清仓=max(10-0.3, 9.5)=9.7，
        # 反抽=max(8.9, 10.2)=10.2（现价略上方离场）
        g = _grid(10.0, 12.0, tp=11.5, stop=8.9, state='S4')
        assert all(x['type'] != 'add' for x in g)
        sc = next(x for x in g if x['label'] == '止损清仓位')
        rb = next(x for x in g if x['label'] == '反抽减仓位')
        assert sc['price'] == pytest.approx(9.7, abs=0.01)
        assert rb['price'] == pytest.approx(10.2, abs=0.01)
        _assert_ascending(g)
