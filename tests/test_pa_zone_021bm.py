"""
021BM：看板建议卡价格区间提取（_parse_pa_zone）

覆盖：正常 JSON（无持仓买入档 / 有持仓补仓档标签）、坏 JSON、缺区间字段、
非 dict 结构、空值——全部离线纯函数。
"""

import json

from blueprints.portfolio import _parse_pa_zone


def _pa(grid, low=10.0, high=12.5, label='买入区间', stop=9.2):
    return json.dumps(
        {
            'zone_label': label,
            'buy_range_low': low,
            'buy_range_high': high,
            'stop_loss': stop,
            'current_close': 11.8,
            'grid': grid,
        },
        ensure_ascii=False,
    )


class TestParsePaZone:
    def test_no_position_buy_levels(self):
        pa = _pa([
            {'level': 1, 'price': 10.0, 'pct': 40, 'type': 'buy', 'label': '第一买入位'},
            {'level': 2, 'price': 10.8, 'pct': 35, 'type': 'buy', 'label': '第二买入位'},
            {'level': 3, 'price': 12.5, 'pct': 25, 'type': 'buy', 'label': '第三买入位'},
        ])
        z = _parse_pa_zone(pa)
        assert z['label'] == '买入区间'
        assert z['low'] == 10.0 and z['high'] == 12.5
        assert z['stop_loss'] == 9.2
        assert len(z['levels']) == 2  # 最多取前两档
        assert z['levels'][0]['label'] == '第一买入位'
        assert z['levels'][0]['pct'] == 40

    def test_held_position_add_levels(self):
        """有持仓：买入侧档位即补仓档（标签透传，看板直接显示）。"""
        pa = _pa(
            [
                {'level': 1, 'price': 10.5, 'pct': 10, 'type': 'buy', 'label': '补仓一档'},
                {'level': 2, 'price': 9.8, 'pct': 15, 'type': 'buy', 'label': '补仓二档'},
                {'level': 3, 'price': 13.0, 'pct': 50, 'type': 'reduce', 'label': '减仓位'},
            ],
            label='加仓区间',
        )
        z = _parse_pa_zone(pa)
        assert z['label'] == '加仓区间'
        assert [l['label'] for l in z['levels']] == ['补仓一档', '补仓二档']
        assert all(l['price'] for l in z['levels'])

    def test_reduce_levels_filtered_out(self):
        """网格里无买入档（如 S4 防御分支只有离场档）→ levels 为空但不报错。"""
        pa = _pa([{'level': 1, 'price': 9.0, 'pct': 100, 'type': 'reduce', 'label': '止损清仓位'}])
        z = _parse_pa_zone(pa)
        assert z is not None and z['levels'] == []

    def test_bad_and_missing_inputs(self):
        assert _parse_pa_zone(None) is None
        assert _parse_pa_zone('') is None
        assert _parse_pa_zone('not-json') is None
        assert _parse_pa_zone('[1,2,3]') is None  # 非 dict
        # 缺区间字段
        assert _parse_pa_zone(json.dumps({'zone_label': 'x'})) is None
