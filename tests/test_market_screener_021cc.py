"""
021CC 共振星级语义重构第一批：判定层特性测试（方案
docs/reports/021cc_indicator_phase_star_plan_20261010.md §3.2/§3.3/§3.4）

覆盖（离线纯函数，合成K线，不触网不触库）：
1. T1 完结周守卫：_completed_weekly 剔除在途周 bar，在途周剔除改变判定
2. T3 叠加不折叠：detect_resonances/detect_sell_resonances 按档位序返回全部
   组合（主组合在前）；每条目保持 021BZ 六键集
3. 健康修正：RSI超买追高 / 震荡骗线频繁 / 量能未确认 三来源各 −1，下限 2★，
   注记带原因；双金叉动态星不覆盖修正结果（同股同扣）
4. D2 严格版阶段函数：detect_indicator_phase 四例（升/降/纠缠/不足35根 None）
"""

import pytest

from modules.market_screener import (
    _buy_health_deductions,
    _completed_weekly,
    detect_indicator_phase,
    detect_resonances,
    detect_sell_resonances,
    detect_sell_signals,
    detect_signals,
)


def _mk_klines(closes, volumes=None):
    """closes → K线行（high/low 由 close 微扰生成，日期递推；volumes 可逐根给定）。"""
    vols = volumes or [1000.0] * len(closes)
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            'date': f'2026-{1 + i // 28:02d}-{1 + i % 28:02d}',
            'open': c * 0.99,
            'close': c,
            'high': c * 1.01,
            'low': c * 0.98,
            'volume': vols[i],
        })
    return rows


_RES_KEYS = {'key', 'label', 'stars', 'kind', 'note', 'signals'}


# ================================================================
# 一、T1 完结周守卫（在途周剔除改变判定）
# ================================================================


class TestCompletedWeeklyGuard:
    @staticmethod
    def _weekly(dates, closes):
        """显式日期的周K行（周bar日期语义与 _mk_klines 的日历递推解耦）。"""
        return [{'date': d, 'open': c * 0.99, 'close': c, 'high': c * 1.01,
                 'low': c * 0.98, 'volume': 1000.0} for d, c in zip(dates, closes)]

    def test_inflight_week_dropped(self):
        """末根周bar日期晚于最新日K日期（在途周）→ 剔除最后一根。"""
        daily = _mk_klines([100 + i for i in range(40)])   # 末日 2026-02-12
        weekly = self._weekly(
            ['2025-12-12', '2025-12-19', '2025-12-26', '2026-01-02', '2026-01-09',
             '2026-01-16', '2026-01-23', '2026-01-30', '2026-02-13'],
            [100 + i for i in range(9)])
        assert weekly[-1]['date'] > daily[-1]['date']      # 前置：周bar在途
        kept = _completed_weekly(weekly, daily)
        assert len(kept) == len(weekly) - 1
        assert kept[-1]['date'] == '2026-01-30'

    def test_completed_week_kept(self):
        """末根周bar日期不晚于最新日K日期（已完结周）→ 原样保留。"""
        daily = _mk_klines([100 + i for i in range(40)])   # 末日 2026-02-12
        weekly = self._weekly(
            ['2025-12-12', '2025-12-19', '2025-12-26', '2026-01-02', '2026-01-09',
             '2026-01-16', '2026-01-23', '2026-01-30', '2026-02-12'],
            [100 + i for i in range(9)])
        kept = _completed_weekly(weekly, daily)
        assert kept == weekly

    def test_degenerate_inputs_passthrough(self):
        """空输入 / 键异常 → 原样返回（不抛错不硬剔）。"""
        daily = _mk_klines([100 + i for i in range(5)])
        assert _completed_weekly([], daily) == []
        assert _completed_weekly(None, daily) is None
        assert _completed_weekly([{'no_date': 1}], daily) == [{'no_date': 1}]

    def test_inflight_drop_changes_verdict(self):
        """判定改变实证：周线多头由最后一根在途bar支撑时，剔除后 DIF≤DEA →
        周线共振从有到无（守卫不是摆设）。"""
        # 前 8 根缓慢下行（DIF<DEA），末根在途巨阳拉升（DIF 上穿）——
        # 含在途bar：周线多头成立；剔除在途bar：周线空头保持
        down = [140 - i * 0.5 for i in range(8)]
        weekly = _mk_klines(down + [down[-1] + 20.0])
        daily_last = weekly[-2]['date']                    # 日K止于上一完结周
        daily = [dict(r) for r in _mk_klines([100 + i * 0.1 for i in range(40)])]
        daily[-1] = dict(daily[-1], date=daily_last)
        kept = _completed_weekly(weekly, daily)
        assert len(kept) == 8

        def _week_bull(rows):
            from modules.market_screener import _macd_series
            wdif, wdea = _macd_series([r['close'] for r in rows])
            return wdif[-1] > wdea[-1]

        assert _week_bull(weekly) is True                  # 含在途：多头（将被动周重绘）
        assert _week_bull(kept) is False                   # 已完结：空头（守卫生效）


# ================================================================
# 二、T3 叠加不折叠（档位序全返回 + 六键集保持）
# ================================================================


class TestOverlayNotFolded:
    def _deep_v_confirmed(self):
        """深V + 末根量能确认：双金叉（跨日 3★）+ 可控环境。"""
        closes = [110 - i * 1.5 for i in range(45)]
        closes += [closes[-1] + 2.0 * (i + 1) for i in range(4)]
        rows = _mk_klines(closes)
        rows[-1]['volume'] = 3000.0
        return rows

    def test_buy_overlay_tier_order(self):
        """周线+双金叉同中 → [周线共振(主), 双金叉(叠加)] 档位序。"""
        rows = self._deep_v_confirmed()
        hits = detect_signals(rows, window=4)
        weekly = _mk_klines([100 + i * 1.0 for i in range(40)])
        res = detect_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == ['res_week_daily', 'res_double_golden']
        assert all(set(r.keys()) == _RES_KEYS for r in res)   # 021BZ 六键集不变

    def test_sell_overlay_tier_order(self):
        """卖侧镜像：周线空头(5★主) + 双死叉(4★叠加) 同批透出，主在前。"""
        closes = [60 + i * 2.0 for i in range(44)] + [156.0, 138.0]
        rows = _mk_klines(closes)
        weekly = _mk_klines([140 - i for i in range(40)])     # 周线空头
        hits = detect_sell_signals(rows)
        res = detect_sell_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == ['res_week_bear', 'res_double_dead']
        assert res[0]['stars'] == 5 and res[1]['stars'] == 4
        assert all(set(r.keys()) == _RES_KEYS for r in res)

    def test_single_combo_unchanged(self):
        """单一组合命中 → 返回单元素列表（与折叠时代形状一致）。"""
        rows = self._deep_v_confirmed()
        hits = detect_signals(rows, window=4)
        res = detect_resonances(hits, rows)
        assert [r['key'] for r in res] == ['res_double_golden']


# ================================================================
# 三、健康修正（三来源各 −1、下限 2★、注记带原因、同股同扣）
# ================================================================


class TestBuyHealthDeductions:
    def test_rsi_overbought_minus_one(self):
        """单边强涨 → RSI>70 → −1『RSI超买追高』；量能确认隔离其余来源。"""
        closes = [50 + i * 1.2 for i in range(40)]
        vols = [1000.0] * 39 + [3000.0]
        ded, mods = _buy_health_deductions(_mk_klines(closes, vols))
        assert ded == 1 and mods == ['RSI超买追高']

    def test_flip_churn_minus_one(self):
        """近20日符号翻转 ≥3 次 → −1『震荡骗线频繁』（叠加横盘反复穿越）。"""
        # 20 根基准后做锯齿：MACD/KDJ 差值反复过零（构造确定性 ≥3 翻转）
        base = [100.0] * 25
        zig = []
        v = 100.0
        for i in range(20):
            v += 6.0 if i % 2 == 0 else -6.0
            zig.append(v)
        closes = base + zig
        rows = _mk_klines(closes)
        rows[-1]['volume'] = 3000.0                        # 量能确认隔离量能源
        ded, mods = _buy_health_deductions(rows)
        assert ded == 1 and mods == ['震荡骗线频繁']

    def test_volume_unconfirmed_minus_one(self):
        """常量量能（末根 < 前5均量1.5倍）→ −1『量能未确认』；RSI/骗线不触发。"""
        closes = [110 - i * 1.5 for i in range(45)] + [44.0, 52.0]
        ded, mods = _buy_health_deductions(_mk_klines(closes))
        assert ded == 1 and mods == ['量能未确认']

    def test_three_sources_stack_and_note(self):
        """多来源可叠加：RSI超买+量能未确认 → −2，共振注记带『健康修正-2（…）』。"""
        closes = [50 + i * 1.2 for i in range(45)]         # 单边强涨 → RSI>70
        rows = _mk_klines(closes)                          # 常量量能 → 量能未确认
        ded, mods = _buy_health_deductions(rows)
        assert ded == 2 and set(mods) == {'RSI超买追高', '量能未确认'}
        hits = [{'signal': 'macd_golden_above', 'label': 'MACD水上金叉',
                 'trigger_date': rows[-1]['date'], 'note': ''},
                {'signal': 'kdj_golden', 'label': 'KDJ金叉',
                 'trigger_date': rows[-1]['date'], 'note': ''}]
        res = detect_resonances(hits, rows)
        assert len(res) == 1
        assert res[0]['key'] == 'res_double_golden'
        assert res[0]['stars'] == 2                        # 同日双金叉 4★ − 2
        assert '健康修正-2（RSI超买追高、量能未确认）' in res[0]['note']

    def test_floor_two_stars(self):
        """下限 2★：基础 3★（跨日双金叉）遇 −2 修正 → 2★ 而非 1★/0★。"""
        closes = [50 + i * 1.2 for i in range(45)]         # RSI>70 + 量能未确认 = −2
        rows = _mk_klines(closes)
        hits = [{'signal': 'macd_golden_above', 'label': 'MACD水上金叉',
                 'trigger_date': '2026-02-17', 'note': ''},
                {'signal': 'kdj_golden', 'label': 'KDJ金叉',
                 'trigger_date': '2026-02-18', 'note': ''}]  # 跨日 → 基础 3★
        res = detect_resonances(hits, rows)
        assert res[0]['key'] == 'res_double_golden'
        assert res[0]['stars'] == 2                        # max(2, 3−2)
        assert '健康修正-2' in res[0]['note']

    def test_dynamic_star_respects_deduction(self):
        """同股同扣：同日双金叉动态星 4★ 不得覆盖健康修正（4−1=3★ 而非 4★）。"""
        closes = [110 - i * 1.5 for i in range(45)] + [44.0, 52.0]
        rows = _mk_klines(closes)                          # 常量量能 → 量能未确认 −1
        hits = [{'signal': 'macd_golden_below', 'label': 'MACD水下金叉',
                 'trigger_date': rows[-1]['date'], 'note': ''},
                {'signal': 'kdj_golden_low', 'label': 'KDJ低位金叉',
                 'trigger_date': rows[-1]['date'], 'note': ''}]  # 同日 → 基础 4★
        res = detect_resonances(hits, rows)
        assert res[0]['stars'] == 3                        # 修正前 4★，修正后 3★
        assert '健康修正-1（量能未确认）' in res[0]['note']

    def test_no_deduction_without_klines(self):
        """无 K线（历史调用形态）→ 零扣减（数据不足不猜）。"""
        hits = [{'signal': 'macd_golden_below', 'label': 'MACD水下金叉',
                 'trigger_date': '2026-09-22', 'note': ''},
                {'signal': 'kdj_golden_low', 'label': 'KDJ低位金叉',
                 'trigger_date': '2026-09-22', 'note': ''}]
        res = detect_resonances(hits)
        assert res[0]['stars'] == 4 and '健康修正' not in res[0]['note']

    def test_insufficient_bars_skip(self):
        """<20 根 → 健康修正整体跳过（返回 0, []）。"""
        ded, mods = _buy_health_deductions(_mk_klines([100 + i for i in range(19)]))
        assert ded == 0 and mods == []


# ================================================================
# 四、D2 严格版阶段函数（升/降/纠缠/不足35根 None）
# ================================================================


class TestDetectIndicatorPhase:
    def test_bull_phase(self):
        """上行途中浅回踩后再度放量上推 → DIF>DEA 且柱体不减 且 K>D → ▲多头。"""
        rows = _mk_klines([100 + i * 1.0 for i in range(36)] + [134.0, 133.0, 136.0, 140.0])
        phase = detect_indicator_phase(rows)
        assert phase is not None
        assert phase['phase'] == 'bull'
        assert '红柱' in phase['note'] and 'K>D' in phase['note']

    def test_bear_phase(self):
        """下行途中加速下探 → DIF<DEA 且柱体不增 且 K<D → ▼空头。"""
        rows = _mk_klines([200 - i * 0.8 for i in range(35)] + [172.0, 168.0, 163.0, 157.0])
        phase = detect_indicator_phase(rows)
        assert phase is not None
        assert phase['phase'] == 'bear'
        assert '绿柱' in phase['note'] and 'K<D' in phase['note']

    def test_entangle_phase(self):
        """收敛上行（柱体转向但交叉未发生）→ ◌纠缠，不硬标方向。"""
        # 渐近饱和上行：DIF<DEA（未金叉）但柱体不再收缩（空头条件不齐）→ 纠缠
        rows = _mk_klines([100 + 30 * (1 - 1 / (1 + 0.15 * i)) for i in range(40)])
        phase = detect_indicator_phase(rows)
        assert phase is not None
        assert phase['phase'] == 'entangle'
        assert phase['note']

    def test_too_few_bars_none(self):
        """<35 根 → None（数据不足不硬标）；35 根起可判。"""
        assert detect_indicator_phase(_mk_klines([100 + i for i in range(34)])) is None
        assert detect_indicator_phase([]) is None
        assert detect_indicator_phase(
            _mk_klines([100 + i * 1.0 for i in range(36)] + [134.0, 133.0, 136.0, 140.0])
        ) is not None

    def test_status_not_signal_contract(self):
        """阶段输出为状态描述（phase/note），不含买卖指令字段——展示槽位契约。"""
        rows = _mk_klines([100 + i * 1.0 for i in range(36)] + [134.0, 133.0, 136.0, 140.0])
        phase = detect_indicator_phase(rows)
        assert set(phase.keys()) == {'phase', 'note'}
