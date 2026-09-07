"""
021BI：全市场选股扫描器

覆盖（离线，不触网）：
1. 指标序列：MACD/KDJ/RSI 与 technical_detail 现有函数口径一致性
2. 信号检测器：8 类信号的触发/窗口/优先级（合成K线）
3. 新浪行解析：字段换算（万元→亿）、板块归类、北交所剔除、停牌剔除
4. 筛选引擎：卫生线默认值 + 条件组合 + 排序
"""

import math

import pytest

from modules.market_screener import (
    _kdj_series,
    _macd_series,
    _parse_sina_row,
    _rsi_series,
    apply_filters,
    detect_resonances,
    detect_signals,
)
from modules.technical_detail import _kdj as _td_kdj
from modules.technical_detail import _macd as _td_macd


def _mk_klines(closes):
    """closes → K线行（high/low 由 close 微扰生成，日期递推）。"""
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            'date': f'2026-{1 + i // 28:02d}-{1 + i % 28:02d}',
            'open': c * 0.99,
            'close': c,
            'high': c * 1.01,
            'low': c * 0.98,
            'volume': 1000.0,
        })
    return rows


def _signal_keys(hits):
    return {h['signal'] for h in hits}


class TestIndicatorSeriesConsistency:
    def test_macd_matches_engine(self):
        closes = [100 + 3 * math.sin(i / 3.0) + i * 0.2 for i in range(60)]
        dif, dea = _macd_series(closes)
        td = _td_macd(closes)
        assert td is not None
        assert dif[-1] == pytest.approx(td[0], abs=1e-6)
        assert dea[-1] == pytest.approx(td[1], abs=1e-6)

    def test_kdj_matches_engine(self):
        closes = [100 - i * 0.5 for i in range(40)]
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.98 for c in closes]
        ks, ds, js = _kdj_series(highs, lows, closes)
        td_k, td_d, td_j = _td_kdj(highs, lows, closes)
        assert ks[-1] == pytest.approx(td_k, abs=1e-6)
        assert ds[-1] == pytest.approx(td_d, abs=1e-6)
        assert js[-1] == pytest.approx(td_j, abs=1e-6)

    def test_rsi_overbought_oversold_bounds(self):
        up = [100 + i for i in range(40)]          # 单边涨 → 高RSI
        down = [200 - i for i in range(40)]        # 单边跌 → 低RSI
        assert _rsi_series(up)[-1] > 70
        assert _rsi_series(down)[-1] < 30


class TestSignalDetectors:
    def _closes_v_shape(self):
        """深V：40根下跌 + 4根急反弹 → 水下金叉 + KDJ低位金叉 + 超卖状态边缘。"""
        closes = [100 - i * 1.2 for i in range(40)]
        closes += [closes[-1] + 1.5 * (i + 1) for i in range(4)]
        return closes

    def test_macd_golden_below(self):
        hits = detect_signals(_mk_klines(self._closes_v_shape()), wanted=['macd_golden_below'])
        assert 'macd_golden_below' in _signal_keys(hits)
        hit = [h for h in hits if h['signal'] == 'macd_golden_below'][0]
        assert hit['label'] == 'MACD水下金叉'
        assert hit['note']

    def test_kdj_golden_low_priority(self):
        """深跌45根+反弹4根：交叉发生在第4根反弹内（window=4 覆盖），D<25 → 低位金叉优先。"""
        closes = [110 - i * 1.5 for i in range(45)]
        closes += [closes[-1] + 2.0 * (i + 1) for i in range(4)]
        hits = detect_signals(_mk_klines(closes), wanted=['kdj_golden_low', 'kdj_golden'], window=4)
        # 深V的交叉发生在D<25区域 → 必须归为低位金叉，不得同时报普通金叉
        assert 'kdj_golden_low' in _signal_keys(hits)
        assert 'kdj_golden' not in _signal_keys(hits)

    def test_macd_golden_above(self):
        """上升途中回踩企稳后缺口大阳 → DIF>0 区域金叉（缺口法构造确定性交叉）。"""
        closes = [50 + i * 2.0 for i in range(40)]           # 强趋势，DIF>0
        closes += [closes[-1] - 4.0 * (i + 1) for i in range(4)]   # 回踩（此处死叉，在窗口外）
        closes += [closes[-1]] * 2                                  # 横盘企稳
        closes += [closes[-1] + 25.0]                               # 缺口大阳 → 金叉
        closes += [closes[-1]] * 2
        hits = detect_signals(_mk_klines(closes), wanted=['macd_golden_above', 'macd_dead'])
        assert 'macd_golden_above' in _signal_keys(hits)
        assert 'macd_dead' not in _signal_keys(hits)

    def test_macd_dead(self):
        closes = [50 + i * 1.0 for i in range(40)]
        closes += [closes[-1] - 3.0 * (i + 1) for i in range(3)]
        hits = detect_signals(_mk_klines(closes), wanted=['macd_dead'])
        assert 'macd_dead' in _signal_keys(hits)

    def test_kdj_oversold_state(self):
        closes = [120 - i * 1.5 for i in range(40)]  # 单边深跌
        hits = detect_signals(_mk_klines(closes), wanted=['kdj_oversold'])
        assert 'kdj_oversold' in _signal_keys(hits)

    def test_rsi_oversold_and_overbought(self):
        down = [200 - i * 1.5 for i in range(40)]
        up = down[::-1]
        assert 'rsi_oversold' in _signal_keys(detect_signals(_mk_klines(down), wanted=['rsi_oversold']))
        assert 'rsi_overbought' in _signal_keys(detect_signals(_mk_klines(up), wanted=['rsi_overbought']))

    def test_window_excludes_old_cross(self):
        """金叉发生在窗口外（5根前）→ window=3 不报。"""
        closes = [100 - i * 1.2 for i in range(40)]
        closes += [closes[-1] + 1.5 * (i + 1) for i in range(4)]   # 交叉在这里
        closes += [closes[-1] + 0.05 for _ in range(5)]            # 5 根横盘隔开
        hits = detect_signals(_mk_klines(closes), wanted=['macd_golden_below'], window=3)
        assert _signal_keys(hits) == set() or 'macd_golden_below' not in _signal_keys(hits)

    def test_too_few_bars_returns_empty(self):
        assert detect_signals(_mk_klines([100 - i for i in range(20)])) == []


class TestResonances:
    """021BI 跟进：跨指标系共振（形态均经实证调参）。"""

    def _deep_v(self):
        closes = [110 - i * 1.5 for i in range(45)]
        closes += [closes[-1] + 2.0 * (i + 1) for i in range(4)]
        return closes

    def test_bottom_reverse_highest_tier(self):
        """深跌后反弹：低位金叉+水下金叉同窗 → ⭐⭐⭐ 底部反转（非普通双金叉）。"""
        closes = self._deep_v()
        hits = detect_signals(_mk_klines(closes), window=4)
        res = detect_resonances(hits, _mk_klines(closes))
        assert [r['key'] for r in res] == ['res_bottom_reverse']
        top = res[0]
        assert top['stars'] == 3 and top['kind'] == 'bull'
        assert 'KDJ低位金叉' in top['signals'] and 'MACD水下金叉' in top['signals']

    def test_double_golden_not_low(self):
        """强趋势缺口金叉：KDJ非低位 → ⭐⭐ 双金叉（不得误判为底部反转）。"""
        closes = [50 + i * 2.0 for i in range(40)]
        closes += [closes[-1] - 4.0 * (i + 1) for i in range(4)]
        closes += [closes[-1]] * 2
        closes += [closes[-1] + 25.0]
        closes += [closes[-1]] * 2
        hits = detect_signals(_mk_klines(closes), window=3)
        res = detect_resonances(hits, _mk_klines(closes))
        assert 'res_double_golden' in [r['key'] for r in res]
        assert 'res_bottom_reverse' not in [r['key'] for r in res]

    def test_bear_confirm(self):
        """强上涨后单日崩：死叉+超买同窗 → ⚠️ 空头共振预警。"""
        closes = [50 + i * 3.0 for i in range(40)]
        closes += [closes[-1] - 10.0]
        hits = detect_signals(_mk_klines(closes), window=3,
                              wanted=['macd_dead', 'kdj_overbought', 'rsi_overbought'])
        res = detect_resonances(hits, _mk_klines(closes))
        assert [r['key'] for r in res] == ['res_bear_confirm']
        assert res[0]['kind'] == 'bear'

    def test_oversold_watch_requires_no_trigger(self):
        """单边深跌双超卖且无金叉 → 💤 观察池。"""
        closes = [120 - i * 1.5 for i in range(40)]
        hits = detect_signals(_mk_klines(closes), window=3)
        res = detect_resonances(hits, _mk_klines(closes))
        assert [r['key'] for r in res] == ['res_oversold_watch']

    def test_watch_suppressed_when_trigger_exists(self):
        """超卖+低位金叉同现 → 买点共振成立，不再报观察池。"""
        closes = self._deep_v()
        hits = detect_signals(_mk_klines(closes), window=4)
        res = detect_resonances(hits, _mk_klines(closes))
        assert 'res_oversold_watch' not in [r['key'] for r in res]

    def test_ma20_env_annotation(self):
        """环境注记：深跌股收盘应在 MA20 下方。"""
        closes = self._deep_v()
        res = detect_resonances(detect_signals(_mk_klines(closes), window=4), _mk_klines(closes))
        assert 'MA20下方' in res[0]['note']

    def test_single_signal_no_resonance(self):
        """仅单一信号（无跨系同向）→ 无共振。"""
        closes = [120 - i * 1.5 for i in range(40)]  # 只有 KDJ 超卖状态
        hits = detect_signals(_mk_klines(closes), window=3, wanted=['kdj_oversold'])
        assert detect_resonances(hits) == []


class TestSinaRowParser:
    def test_valid_row_normalized(self):
        row = {'symbol': 'sz300750', 'code': '300750', 'name': '宁德时代', 'trade': '358.10',
               'changepercent': '-1.2', 'turnoverratio': '2.5', 'amount': '2590000',
               'mktcap': '162153100', 'nmc': '142153100', 'per': '19.9', 'pb': '6.4'}
        r = _parse_sina_row(row)
        assert r['board'] == '创业板'
        assert r['mkt_cap'] == pytest.approx(16215.31, abs=0.01)   # 万元 → 亿
        assert r['turnover'] == 2.5
        assert r['industry'] is None and r['volume_ratio'] is None

    def test_bj_code_filtered(self):
        assert _parse_sina_row({'symbol': 'bj835185', 'code': '835185', 'name': '某北交所',
                                'trade': '5.0'}) is None

    def test_suspended_filtered(self):
        assert _parse_sina_row({'symbol': 'sh600000', 'code': '600000', 'name': '浦发银行',
                                'trade': '0'}) is None

    def test_star_market_board(self):
        r = _parse_sina_row({'symbol': 'sh688981', 'code': '688981', 'name': '中芯国际',
                             'trade': '50.0', 'mktcap': '400000000'})
        assert r['board'] == '科创板'


class TestApplyFilters:
    def _rows(self):
        return [
            {'symbol': 'sh600519', 'name': '贵州茅台', 'board': '主板', 'industry': '酿酒行业',
             'price': 1297.0, 'change_pct': -0.15, 'turnover': 1.6, 'mkt_cap': 16215.0,
             'nmc_cap': 16215.0, 'volume_ratio': 0.84},
            {'symbol': 'sz300750', 'name': '宁德时代', 'board': '创业板', 'industry': '电池',
             'price': 358.1, 'change_pct': -1.2, 'turnover': 2.5, 'mkt_cap': 16215.0,
             'nmc_cap': 7000.0, 'volume_ratio': 1.5},
            {'symbol': 'sh600000', 'name': 'ST某某', 'board': '主板', 'industry': '银行',
             'price': 7.0, 'change_pct': 5.0, 'turnover': 3.0, 'mkt_cap': 200.0,
             'nmc_cap': 180.0, 'volume_ratio': 2.0},
            {'symbol': 'sz000001', 'name': '平安银行', 'board': '主板', 'industry': '银行',
             'price': 11.9, 'change_pct': 0.5, 'turnover': 0.5, 'mkt_cap': 2300.0,
             'nmc_cap': 2300.0, 'volume_ratio': None},
        ]

    def test_nmc_cap_range(self):
        """021BI 跟进：流通市值范围筛选（None 视为 0）。"""
        filtered, _ = apply_filters(self._rows(), {'exclude_st': False, 'mkt_cap_min': None,
                                                   'turnover_min': None,
                                                   'nmc_cap_min': 1000.0, 'nmc_cap_max': 8000.0})
        # 宁德 7000 ✓，平安 2300 ✓；茅台 16215 ✗ 上限；ST 180 ✗ 下限
        assert {r['name'] for r in filtered} == {'宁德时代', '平安银行'}

    def test_hygiene_defaults(self):
        filtered, stats = apply_filters(self._rows())  # 默认：剔ST + 市值≥100亿 + 换手≥1%
        names = {r['name'] for r in filtered}
        assert names == {'贵州茅台', '宁德时代'}
        assert stats['after_st'] == 3

    def test_board_and_industry(self):
        filtered, _ = apply_filters(self._rows(), {'boards': ['创业板'], 'exclude_st': False,
                                                   'mkt_cap_min': None, 'turnover_min': None})
        assert [r['symbol'] for r in filtered] == ['sz300750']

    def test_change_pct_range(self):
        filtered, _ = apply_filters(self._rows(), {'change_pct_min': 0, 'exclude_st': False,
                                                   'mkt_cap_min': None, 'turnover_min': None})
        assert {r['name'] for r in filtered} == {'ST某某', '平安银行'}

    def test_turnover_max_range(self):
        """021BI 跟进：换手率上限（范围筛选）。"""
        filtered, _ = apply_filters(self._rows(), {'exclude_st': False, 'mkt_cap_min': None,
                                                   'turnover_min': None, 'turnover_max': 2.0})
        # 茅台 1.6 ✓，平安 0.5 ✓；ST 3.0 ✗ 上限，宁德 2.5 ✗ 上限
        assert {r['name'] for r in filtered} == {'贵州茅台', '平安银行'}

    def test_volume_ratio_range(self):
        """021BI 跟进：量比范围筛选（None 视为 0，遇下限被剔除）。"""
        filtered, _ = apply_filters(self._rows(), {'exclude_st': False, 'mkt_cap_min': None,
                                                   'turnover_min': None,
                                                   'volume_ratio_min': 1.0, 'volume_ratio_max': 1.9})
        # 宁德 1.5 ✓；茅台 0.84 ✗ 下限；ST 2.0 ✗ 上限；平安 None→0 ✗ 下限
        assert [r['name'] for r in filtered] == ['宁德时代']

    def test_sorted_by_mktcap_desc(self):
        filtered, _ = apply_filters(self._rows(), {'exclude_st': False, 'mkt_cap_min': None,
                                                   'turnover_min': None})
        caps = [r['mkt_cap'] for r in filtered]
        assert caps == sorted(caps, reverse=True)
