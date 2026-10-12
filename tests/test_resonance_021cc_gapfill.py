"""
021CC 测试查漏补缺（与 t1 产出的 test_market_screener_021cc.py /
test_action_list_021cc.py 互补，不重复；方案 docs/reports/
021cc_indicator_phase_star_plan_20261010.md §3.3/§3.4/§5）：

1. 完结周守卫升级到 detect_resonances 判定链路（t1 已测 _completed_weekly 纯函数与
   周线多空谓词，本文件补「共振级」双向实证）：
   - 盘中暴涨粉饰：在途 bar 把已完结空头周粉饰成多头 → 守卫剔除，不出周线共振；
   - 盘中暴跌重绘：在途 bar 把已完结多头周砸成空头 → 守卫保留完结判定，共振不丢。
2. 叠加多组合三组合同批（t1 覆盖两组合）：档位序全返回、主在前；星值按新口径
   断言（week_daily 4★ 基础，健康修正可再降）。
3. 健康修正下限 2★ 对最高基础档（5★ 零轴上二次金叉）成立：三来源全触发 -3 → 2★，
   同批三组合同扣同底（同股同扣 × 下限的交叉面）。
4. 分布监控脚本 scripts/resonance_stats_021cc.py：聚合纯函数 + 临时库离线采集
   （零网络零写库口径回归；importlib 按路径加载，与 test_data_integrity_021bx 同款机制）。
"""

import datetime as dt
import importlib.util
import os

from database import db_manager
from database.db_manager import get_connection, init_database
from modules.market_screener import (
    _buy_health_deductions,
    _completed_weekly,
    _macd_series,
    detect_resonances,
    detect_signals,
)

_RES_KEYS = {'key', 'label', 'stars', 'kind', 'note', 'signals'}
_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'scripts', 'resonance_stats_021cc.py')


def _mk_klines(closes, volumes=None, day0=(2026, 1, 1)):
    """closes → K线行（真实日历递推并跳过周末：周bar/守卫需要真实日期可比）。"""
    vols = volumes or [1000.0] * len(closes)
    rows = []
    d = dt.date(*day0)
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        rows.append({'date': d.isoformat(), 'open': c * 0.99, 'close': c,
                     'high': c * 1.01, 'low': c * 0.98, 'volume': vols[i]})
        d += dt.timedelta(days=1)
    return rows


def _daily_deepv():
    """深V + 末根量能确认：MACD水下金叉 + KDJ低位金叉跨日 → 双金叉 3★（零健康修正）。"""
    closes = [110 - i * 1.5 for i in range(45)]
    closes += [closes[-1] + 2.0 * (i + 1) for i in range(3)]
    rows = _mk_klines(closes)
    rows[-1]['volume'] = 3000.0
    return rows


def _weekly_with_inflight(base_closes, delta):
    """(已完结周, 含在途bar周)：在途bar日期远晚于日K末日，delta 决定粉饰方向。"""
    weekly = _mk_klines(base_closes, day0=(2025, 4, 1))
    inflight = dict(weekly[-1])
    inflight['date'] = '2026-12-31'
    inflight['close'] = weekly[-1]['close'] + delta
    inflight['open'] = weekly[-1]['close'] * 0.99
    inflight['high'] = max(inflight['close'], weekly[-1]['close']) * 1.01
    inflight['low'] = min(inflight['close'], weekly[-1]['close']) * 0.98
    return weekly, weekly + [inflight]


def _week_bull(rows):
    dif, dea = _macd_series([r['close'] for r in rows])
    return dif[-1] > dea[-1]


# ================================================================
# 一、完结周守卫 @ detect_resonances 判定链路（t1 只测纯函数/谓词级）
# ================================================================


class TestCompletedWeeklyGuardAtResonanceLevel:
    """021CC T1 的两类盘中场景，均以共振条目有无为断言面。"""

    def test_inflight_pump_not_admitted(self):
        """已完结周空头 + 在途bar暴涨粉饰成多头 → 守卫剔除，不产周线共振。"""
        daily = _daily_deepv()
        hits = detect_signals(daily, window=3)
        completed, pumped = _weekly_with_inflight([100 - i * 0.05 for i in range(36)], +25.0)
        assert _completed_weekly(pumped, daily) == completed   # 守卫剔除在途bar
        assert _week_bull(completed) is False                  # 已完结：空头
        assert _week_bull(pumped) is True                      # 含在途：将被粉饰为多头
        res = detect_resonances(hits, daily, pumped)
        assert 'res_week_daily' not in [r['key'] for r in res]  # 假信号不放行
        assert [r['key'] for r in res] == ['res_double_golden']

    def test_inflight_crash_does_not_repaint(self):
        """已完结周多头 + 在途bar暴跌砸成空头 → 守卫保留完结判定，周线共振不丢。"""
        daily = _daily_deepv()
        hits = detect_signals(daily, window=3)
        completed, crashed = _weekly_with_inflight([100 + i * 0.05 for i in range(36)], -25.0)
        assert _completed_weekly(crashed, daily) == completed
        assert _week_bull(completed) is True                   # 已完结：多头
        assert _week_bull(crashed) is False                    # 含在途：盘中重绘为空头
        res = detect_resonances(hits, daily, crashed)
        assert res[0]['key'] == 'res_week_daily'               # 主组合在前（档位序）
        assert res[0]['stars'] == 4                            # 新口径：D1 降档后 4★


# ================================================================
# 二、叠加多组合（三组合同批，t1 覆盖两组合）
# ================================================================


class TestTripleOverlayTierOrder:
    @staticmethod
    def _churn_rows(rep_last=12.0):
        """强趋势内两次回调-修复：周线多头 + 零轴二次金叉 + 双金叉三组合同中；
        常量量能保留健康修正（震荡骗线 -1 + 量能未确认 -1）。"""
        z = [50 + i * 1.4 for i in range(45)]
        z += [z[-1] - 8.0 * (i + 1) for i in range(3)]
        z += [z[-1] + 12.0 * (i + 1) for i in range(3)]
        z += [z[-1] - 8.0 * (i + 1) for i in range(3)]
        z += [z[-1] + rep_last * (i + 1) for i in range(3)]
        return _mk_klines(z)

    def test_three_combos_full_tier_order(self):
        rows = self._churn_rows()
        hits = detect_signals(rows, window=3)
        weekly = _mk_klines([100 + i * 1.0 for i in range(40)], day0=(2025, 4, 1))
        res = detect_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == [
            'res_week_daily', 'res_zero_relay', 'res_double_golden']   # 全档位序，主在前
        assert all(set(r.keys()) == _RES_KEYS for r in res)            # 021BZ 六键集不变
        ded, mods = _buy_health_deductions(rows)
        assert ded == 2 and set(mods) == {'震荡骗线频繁', '量能未确认'}
        # 新口径星值：4★/5★/3★ 基础 − 同一笔修正 2，3★ 组合触下限 2★
        assert res[0]['stars'] == max(2, 4 - ded) == 2
        assert res[1]['stars'] == max(2, 5 - ded) == 3
        assert res[2]['stars'] == max(2, 3 - ded) == 2


# ================================================================
# 三、健康修正下限 2★ @ 最高基础档 5★（三来源全触发）
# ================================================================


class TestHealthFloorOnFiveStarBase:
    @staticmethod
    def _rows():
        """末次修复更陡（RSI 推上 70）+ 常量量能 + 近20日反复翻转：三来源全触发。"""
        z = [50 + i * 1.4 for i in range(45)]
        z += [z[-1] - 7.5 * (i + 1) for i in range(3)]
        z += [z[-1] + 12.5 * (i + 1) for i in range(3)]
        z += [z[-1] - 7.5 * (i + 1) for i in range(3)]
        z += [z[-1] + 13.5 * (i + 1) for i in range(3)]
        return _mk_klines(z)

    def test_all_three_sources_floor_five_star_combo(self):
        rows = self._rows()
        ded, mods = _buy_health_deductions(rows)
        assert ded == 3
        assert set(mods) == {'RSI超买追高', '震荡骗线频繁', '量能未确认'}
        hits = detect_signals(rows, window=3)
        weekly = _mk_klines([100 + i * 1.0 for i in range(40)], day0=(2025, 4, 1))
        res = detect_resonances(hits, rows, weekly)
        assert {r['key'] for r in res} == {
            'res_week_daily', 'res_zero_relay', 'res_double_golden'}
        # 5★ 基础（零轴上二次）−3 → 触及下限 2★；同批三组合同扣同底（同股同扣）
        assert all(r['stars'] == 2 for r in res)
        for r in res:
            assert '健康修正-3（RSI超买追高、震荡骗线频繁、量能未确认）' in r['note']


# ================================================================
# 四、分布监控脚本 scripts/resonance_stats_021cc.py（方案 §5）
# ================================================================


def _load_stats_script():
    spec = importlib.util.spec_from_file_location('resonance_stats_021cc', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResonanceStatsScript:
    """聚合纯函数（档位/健康分布口径）+ 临时库离线采集（零网络零写库回归）。"""

    @staticmethod
    def _collected():
        return {
            'stocks_total': 4,
            'rows': [
                {'stock_id': 1, 'symbol': '600519', 'name': '甲', 'kline_count': 250,
                 'kline_upto': '2026-10-12', 'matches_n': 2,
                 'combos': [{'key': 'res_week_daily', 'label': '周线共振波段', 'stars': 2},
                            {'key': 'res_double_golden', 'label': '双金叉共振', 'stars': 2}],
                 'health_ded': 2, 'health_mods': ['震荡骗线频繁', '量能未确认']},
                {'stock_id': 2, 'symbol': '600520', 'name': '乙', 'kline_count': 250,
                 'kline_upto': '2026-10-10', 'matches_n': 1,
                 'combos': [{'key': 'res_bottom_reverse', 'label': '底部反转共振', 'stars': 4}],
                 'health_ded': 0, 'health_mods': []},
                {'stock_id': 3, 'symbol': '600521', 'name': '丙', 'kline_count': 20,
                 'kline_upto': '2026-10-12', 'matches_n': 0, 'combos': [],
                 'health_ded': 0, 'health_mods': []},
                {'stock_id': 4, 'symbol': '600522', 'name': '丁', 'kline_count': 0,
                 'kline_upto': None, 'matches_n': 0, 'combos': [],
                 'health_ded': 0, 'health_mods': []},
            ],
            'errors': [{'stock_id': 9, 'error': 'boom'}],
        }

    def test_aggregate_buckets_2star_separate_and_health(self):
        """2★ 单独计档（区别于行动清单 '3' 桶口径）+ 健康三来源计数 + 数据面统计。"""
        mod = _load_stats_script()
        stats = mod.aggregate(self._collected())
        assert stats['resonance_entries_total'] == 3
        assert stats['star_dist'] == {'5': 0, '4': 1, '3': 0, '2': 2}
        assert stats['stock_star_dist'] == {'5': 0, '4': 1, '3': 0, '2': 1}  # 个股口径取主组合
        assert stats['stocks_with_resonance'] == 2
        assert stats['stocks_insufficient_kline'] == 1
        assert stats['error_count'] == 1
        assert stats['kline_upto_latest'] == '2026-10-12'
        assert stats['res_combo_stars']['res_week_daily'] == {'label': '周线共振波段', '2': 1}
        h = stats['health']
        assert h['stocks_flagged'] == 1 and h['ded_dist'] == {'2': 1}
        assert h['sources'] == {'RSI超买追高': 0, '震荡骗线频繁': 1, '量能未确认': 1}

    def test_console_render_no_bare_lt(self):
        """控制台文案禁裸 '<'（R 合规，021BN 教训），关键小节标题在位。"""
        mod = _load_stats_script()
        text = mod.render_console(mod.aggregate(self._collected()))
        assert '<' not in text
        assert '共振档位分布' in text and '健康修正来源分布' in text
        assert '条目合计 3' in text

    def test_collect_rows_offline_tmp_db(self, tmp_path, monkeypatch):
        """临时库两种自选股：深V双金叉（有共振）+ 不足35根（不可判）→ 分布对账。"""
        mod = _load_stats_script()
        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'stats_021cc.db'))
        init_database()
        conn = get_connection()
        conn.executemany(
            "INSERT INTO stocks (symbol, market, name) VALUES (?, 'a_stock', ?)",
            [('600519', '深V股'), ('600520', '短史股')])
        conn.commit()
        ids = [r['id'] for r in conn.execute('SELECT id FROM stocks ORDER BY id')]

        def _seed(stock_id, closes):
            d = dt.date(2026, 6, 1)
            params = []
            for i, c in enumerate(closes):
                while d.weekday() >= 5:
                    d += dt.timedelta(days=1)
                vol = 3000.0 if i == len(closes) - 1 else 1000.0   # 末根放量 → 量能确认
                params.append((stock_id, d.isoformat(), c * 0.99, c,
                               c * 1.01, c * 0.98, vol))
                d += dt.timedelta(days=1)
            conn.executemany(
                'INSERT OR IGNORE INTO raw_kline (stock_id, trade_date, open, close, '
                'high, low, volume) VALUES (?, ?, ?, ?, ?, ?, ?)', params)

        _seed(ids[0], [110 - i * 1.5 for i in range(45)] + [46.0, 48.0, 50.0])
        _seed(ids[1], [30 - i * 0.5 for i in range(20)])
        conn.commit()
        conn.close()

        collected = mod.collect_rows()
        assert collected['stocks_total'] == 2 and not collected['errors']
        by_id = {r['stock_id']: r for r in collected['rows']}
        assert [r['key'] for r in by_id[ids[0]]['combos']] == ['res_double_golden']
        assert by_id[ids[0]]['health_ded'] == 0              # 末根量能确认 → 零修正
        assert by_id[ids[1]]['combos'] == []                 # 不足35根不可判，不硬标

        stats = mod.aggregate(collected)
        assert stats['star_dist'] == {'5': 0, '4': 0, '3': 1, '2': 0}
        assert stats['stocks_insufficient_kline'] == 1
        assert stats['stocks_total'] - stats['stocks_with_resonance'] == 1   # 无共振 1 只
