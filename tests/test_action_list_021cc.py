"""
021CC 行动清单收尾：行动语义矩阵（时机×方向）/ 持有阶段槽 / counts 分布

覆盖（纯逻辑装配 + 临时库集成，不触网）：
1. 行动语义矩阵七分支：5★✓正常买入候选 / 5★✗仅短线反弹参考严止损 /
   4★✓可建底仓 / 4★✗观察等确认 / 3★及无共振✓持有等信号 / ✗回避 /
   评级缺失不产行动语义；「。行动：X」置于既有相悖调和文案之后
2. 持有条目阶段槽：持仓纪律/持有买点行 detail.phase + reason 前缀
   「指标阶段▲多头；」（▲多头/▼空头/◌纠缠；数据不足不带、非持仓不带）
3. counts：stats 共振档位分布（'5'/'4'/'3'（含健康修正降档）/'无'，买卖两侧）
4. 集成：get_action_list 为持仓股离线算 phase（raw_kline 最近 80 根）；
   K线不足 35 根不硬标（detail.phase None，reason 无前缀）
"""

import datetime as dt

import pytest

from database import db_manager
from modules.action_list import build_action_list, get_action_list

_TODAY = '2026-10-12'
_YDAY = '2026-10-09'


def _stock(sid, symbol='600519', name='贵州茅台'):
    return {'stock_id': sid, 'symbol': symbol, 'name': name}


def _report(stock_id, rating, rn=1, total_score=70.0, score_change=None,
            status='ok', error_msg=None, code='600519', name='贵州茅台'):
    return {
        'report_date': _TODAY, 'stock_id': stock_id, 'stock_code': code,
        'stock_name': name, 'total_score': total_score, 'rating': rating,
        'rating_label': rating, 'score_change': score_change, 'status': status,
        'error_msg': error_msg, 'key_factors': None, 'rn': rn,
    }


def _sig_multi(per_stock):
    """per_stock: {sid: (stars, key, label)}；stars=0 → 无共振（resonances 空）。"""
    results = []
    for sid, (stars, key, label) in per_stock.items():
        resonances = ([{'key': key, 'label': label, 'stars': stars, 'kind': 'bull',
                        'note': '', 'signals': ''}] if stars else [])
        results.append({
            'stock_id': sid, 'symbol': f'60051{sid}', 'name': f'股{sid}',
            'matches': [{'signal': 'kdj_golden', 'label': 'KDJ金叉',
                         'trigger_date': _TODAY, 'note': ''}],
            'resonances': resonances,
            'kline_upto': _TODAY, 'kline_count': 47,
        })
    return {'scope': 'watchlist_offline', 'stock_count': len(results),
            'results': results, 'errors': []}


def _build_one(stars, rating, key='res_bottom_reverse', label='底部反转共振',
               rating_status='ok'):
    """单股最小装配：指定共振星与最新评级 → 行动项（tech_signal）。"""
    stocks = [_stock(1)]
    rows = []
    if rating is not None:
        rows = [_report(1, rating, rn=1, status=rating_status)]
    sig = _sig_multi({1: (stars, key, label)})
    result = build_action_list(_TODAY, stocks, rows, [], sig)
    return [it for it in result['items'] if it['kind'] == 'tech_signal'][0]


# ================================================================
# 一、行动语义矩阵七分支（时机 × 方向）
# ================================================================


class TestActionSemanticsMatrix:
    def test_five_stars_aligned_normal_buy(self):
        """5★✓ → 正常买入候选。"""
        it = _build_one(5, '持有观望')
        assert it['detail']['action'] == '正常买入候选'
        assert it['detail']['direction_ok'] is True
        assert it['reason'].endswith('。行动：正常买入候选')

    def test_five_stars_conflict_short_bounce(self):
        """5★✗ → 仅短线反弹参考，严止损；「。行动：」置于既有相悖调和文案之后。"""
        it = _build_one(5, '建议减仓')
        assert it['detail']['action'] == '仅短线反弹参考，严止损'
        assert it['detail']['direction_ok'] is False
        assert it['detail']['rating_conflict'] == '建议减仓'
        assert '非趋势反转买入。行动：仅短线反弹参考，严止损' in it['reason']

    def test_four_stars_aligned_base_position(self):
        """4★✓ → 可建底仓（周线共振 D1 降档后 4★ 为真实主档）。"""
        it = _build_one(4, '推荐买入', key='res_week_daily', label='周线共振波段')
        assert it['detail']['action'] == '可建底仓'
        assert it['detail']['direction_ok'] is True

    def test_four_stars_conflict_watch_confirm(self):
        """4★✗ → 观察等确认。"""
        it = _build_one(4, '强烈建议卖出', key='res_week_daily', label='周线共振波段')
        assert it['detail']['action'] == '观察等确认'
        assert it['detail']['direction_ok'] is False

    def test_three_stars_aligned_hold_wait(self):
        """3★✓ → 持有等信号。"""
        it = _build_one(3, '持有观望', key='res_double_golden', label='双金叉共振')
        assert it['detail']['action'] == '持有等信号'

    def test_no_resonance_aligned_hold_wait(self):
        """无共振✓ → 持有等信号（3★及无共振同格）。"""
        it = _build_one(0, '持有观望')
        assert it['detail']['action'] == '持有等信号'
        assert it['detail']['direction_ok'] is True

    def test_three_stars_and_no_resonance_conflict_avoid(self):
        """3★✗ / 无共振✗ → 回避。"""
        for stars in (3, 0):
            it = _build_one(stars, '建议减仓')
            assert it['detail']['action'] == '回避'

    def test_missing_rating_no_semantics(self):
        """评级缺失（无报告 / 今日报告失败）→ direction_ok None，不产行动语义。"""
        it = _build_one(5, None)
        assert it['detail']['action'] is None
        assert it['detail']['direction_ok'] is None
        assert '。行动：' not in it['reason']
        it2 = _build_one(5, '建议减仓', rating_status='failed')
        assert it2['detail']['action'] is None
        assert it2['detail']['direction_ok'] is None
        assert '。行动：' not in it2['reason']

    def test_matrix_reason_no_bare_lt(self):
        """矩阵文案与阶段前缀汇入 reason 后仍禁裸 '<'（021BN 教训）。"""
        for stars in (5, 4, 3, 0):
            for rating in ('推荐买入', '持有观望', '建议减仓', '强烈建议卖出'):
                it = _build_one(stars, rating)
                assert '<' not in it['reason']


# ================================================================
# 二、持有条目阶段槽（纪律项 + 持有买点项）
# ================================================================


class TestHeldPhaseSlot:
    def test_held_tech_signal_prefix_and_detail(self):
        """持有买点行：reason 前缀「指标阶段▲多头；」+ detail.phase 原样携带。"""
        phase = {'phase': 'bull', 'note': 'MACD红柱扩张·K>D(62)'}
        held_map = {1: {'total_qty': 100, 'avg_cost': 10.0}}
        result = build_action_list(_TODAY, [_stock(1)], [], [], _sig_multi({1: (5, 'res_bottom_reverse', '底部反转共振')}),
                                   held_map=held_map, phase_map={1: phase})
        sig = [it for it in result['items'] if it['kind'] == 'tech_signal'][0]
        assert sig['reason'].startswith('指标阶段▲多头；今日出现买点信号')
        assert sig['detail']['phase'] == phase

    def test_held_bear_and_entangle_prefix(self):
        """▼空头 / ◌纠缠 前缀样式。"""
        held_map = {1: {'total_qty': 100, 'avg_cost': 10.0}}
        for phase_val, label in (('bear', '▼空头'), ('entangle', '◌纠缠')):
            result = build_action_list(
                _TODAY, [_stock(1)], [], [],
                _sig_multi({1: (5, 'res_bottom_reverse', '底部反转共振')}),
                held_map=held_map, phase_map={1: {'phase': phase_val, 'note': 'x'}})
            sig = result['items'][0]
            assert sig['reason'].startswith(f'指标阶段{label}；今日出现买点信号')
            assert sig['detail']['phase']['phase'] == phase_val

    def test_not_held_no_prefix_and_phase_none(self):
        """非持仓的买点行：不带阶段前缀，detail.phase 为 None（阶段槽仅持仓三处）。"""
        result = build_action_list(
            _TODAY, [_stock(1)], [], [],
            _sig_multi({1: (5, 'res_bottom_reverse', '底部反转共振')}),
            held_map=None, phase_map={1: {'phase': 'bull', 'note': 'x'}})
        sig = result['items'][0]
        assert not sig['reason'].startswith('指标阶段')
        assert sig['detail']['phase'] is None

    def test_phase_map_absent_behavior_unchanged(self):
        """未提供 phase_map（既有纯函数调用方）→ 无前缀、槽位 None（行为零破坏）。"""
        held_map = {1: {'total_qty': 100, 'avg_cost': 10.0}}
        result = build_action_list(
            _TODAY, [_stock(1)], [], [],
            _sig_multi({1: (5, 'res_bottom_reverse', '底部反转共振')}), held_map=held_map)
        sig = result['items'][0]
        assert not sig['reason'].startswith('指标阶段')
        assert sig['detail']['phase'] is None

    def test_stop_discipline_prefix_and_detail(self):
        """持仓纪律行：reason 前缀 + detail.phase（纪律无条件最高，阶段仅注记）。"""
        phase = {'phase': 'bear', 'note': 'MACD绿柱扩张·K<D(40)'}
        disc = [{'stock_id': 1, 'total_qty': 1000, 'avg_cost': 10.0, 'close': 8.0,
                 'close_date': _TODAY, 'discipline_stop': 9.2, 'pa_stop': None,
                 'effective_stop': 9.2}]
        result = build_action_list(_TODAY, [_stock(1)], [], [], None,
                                   held_map={}, discipline_rows=disc,
                                   phase_map={1: phase})
        it = result['items'][0]
        assert it['kind'] == 'stop_discipline'
        assert it['reason'].startswith('指标阶段▼空头；止损纪律已触发')
        assert it['detail']['phase'] == phase
        assert '不等评级' in it['reason']        # 纪律语义保持

    def test_stop_discipline_phase_none_no_prefix(self):
        """纪律行阶段缺失（不足/异常）→ 无前缀不硬标，行为与 021BR 一致。"""
        disc = [{'stock_id': 1, 'total_qty': 1000, 'avg_cost': 10.0, 'close': 8.0,
                 'close_date': _TODAY, 'discipline_stop': 9.2, 'pa_stop': None,
                 'effective_stop': 9.2}]
        result = build_action_list(_TODAY, [_stock(1)], [], [], None,
                                   held_map={}, discipline_rows=disc,
                                   phase_map={1: None})
        it = result['items'][0]
        assert it['reason'].startswith('止损纪律已触发')
        assert it['detail']['phase'] is None


# ================================================================
# 三、counts：共振档位分布（5/4/3/无，买卖两侧）
# ================================================================


class TestCountsDistribution:
    def test_buy_star_dist_four_buckets(self):
        """买侧四桶：5★/4★/3★/无共振各计一只；signal_hits 同步。"""
        stocks = [_stock(i, f'60051{i}', f'股{i}') for i in range(1, 5)]
        sig = _sig_multi({
            1: (5, 'res_bottom_reverse', '底部反转共振'),
            2: (4, 'res_week_daily', '周线共振波段'),
            3: (3, 'res_double_golden', '双金叉共振'),
            4: (0, '', ''),
        })
        result = build_action_list(_TODAY, stocks, [], [], sig)
        st = result['stats']
        assert st['signal_hits'] == 4
        assert st['resonance_star_dist'] == {'5': 1, '4': 1, '3': 1, '无': 1}

    def test_buy_star_dist_health_downgrade_in_three_bucket(self):
        """健康修正降档 2★ 计入 '3' 桶（3★及以下口径，注释声明）。"""
        from modules.action_list import _star_dist_key

        assert _star_dist_key(2) == '3'

    def test_sell_star_dist(self):
        """卖侧分布独立计数：5★+3★ → {'5':1,'4':0,'3':1,'无':0}；sell_hits 同步。"""
        stocks = [_stock(1, '600511', '股1'), _stock(2, '600512', '股2')]
        sell = {
            'scope': 'watchlist_offline', 'side': 'sell', 'stock_count': 2,
            'results': [
                {'stock_id': sid, 'symbol': f'60051{sid}', 'name': f'股{sid}',
                 'sell_matches': [{'signal': 'macd_dead_above', 'label': 'MACD水上死叉',
                                   'trigger_date': _TODAY, 'note': ''}],
                 'sell_resonances': [{'key': key, 'label': label, 'stars': stars,
                                      'kind': 'bear', 'note': '', 'signals': ''}],
                 'kline_upto': _TODAY, 'kline_count': 46}
                for sid, (stars, key, label) in {
                    1: (5, 'res_week_bear', '周线空头波段卖'),
                    2: (3, 'res_double_dead', '双死叉共振')}.items()
            ],
            'errors': [],
        }
        result = build_action_list(_TODAY, stocks, [], [], None, sell)
        st = result['stats']
        assert st['sell_hits'] == 2
        assert st['sell_resonance_star_dist'] == {'5': 1, '4': 0, '3': 1, '无': 0}

    def test_empty_inputs_zero_dist(self):
        """空输入 → 分布四桶全零（键集恒在，监控脚本可直接消费）。"""
        result = build_action_list(_TODAY, [_stock(1)], [], [], None)
        st = result['stats']
        assert st['resonance_star_dist'] == {'5': 0, '4': 0, '3': 0, '无': 0}
        assert st['sell_resonance_star_dist'] == {'5': 0, '4': 0, '3': 0, '无': 0}


# ================================================================
# 四、集成：get_action_list 持仓股阶段离线计算（raw_kline ≥80 根）
# ================================================================


def _crash_tail_closes():
    """76 根缓涨 + 4 根急跌 → 阶段 ▼空头；最新收盘远低于成本纪律线（触发纪律项）。"""
    return [20 + i * 0.1 for i in range(76)] + [22.0, 19.0, 15.0, 12.0]


def _insert_klines(stock_id, closes):
    conn = db_manager.get_connection()
    dates = [(dt.date(2026, 6, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(closes))]
    conn.executemany(
        'INSERT OR IGNORE INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        [(stock_id, d, c * 0.99, c, c * 1.01, c * 0.98, 1000.0)
         for d, c in zip(dates, closes)],
    )
    conn.commit()
    conn.close()


def _add_holding(stock_id, cost):
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
        'VALUES (1, ?, ?, 500)', (stock_id, cost),
    )
    conn.commit()
    conn.close()


def _stop_discipline_item(result):
    return [it for it in result['items'] if it['kind'] == 'stop_discipline'][0]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'test_action_021cc.db'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks LIMIT 1').fetchone()['id']
    conn.close()
    return stock_id


def test_phase_offline_computed_for_held(db):
    """持仓股 + ≥80 根日K → 纪律行带离线阶段槽与前缀（缓涨后急跌 → ▼空头）。"""
    _insert_klines(db, _crash_tail_closes())
    _add_holding(db, 20.0)                      # 纪律线 18.40 > 现价 12 → 触发
    result = get_action_list()
    item = _stop_discipline_item(result)
    assert item['detail']['phase'] is not None
    assert item['detail']['phase']['phase'] == 'bear'
    assert item['reason'].startswith('指标阶段▼空头；止损纪律已触发')


def test_phase_absent_when_insufficient_bars(db):
    """持仓股 K线不足 35 根 → 不硬标：detail.phase None，reason 无前缀。"""
    _insert_klines(db, [30 - i * 0.5 for i in range(20)])   # 20 根，收 20.5
    _add_holding(db, 25.0)                      # 纪律线 23.0 > 20.5 → 触发
    result = get_action_list()
    item = _stop_discipline_item(result)
    assert item['detail']['phase'] is None
    assert not item['reason'].startswith('指标阶段')
    assert item['reason'].startswith('止损纪律已触发')   # 021BR 原文案语义保持


def test_phase_absent_when_no_klines(db):
    """持仓股无任何日K → 异常/空数据降级：纪律行照常装配，阶段槽 None。"""
    _add_holding(db, 25.0)
    conn = db_manager.get_connection()
    today = dt.date.today().isoformat()
    conn.execute(
        """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
           total_score, rating, rating_label, score_change, status, report_type)
           VALUES (?, ?, '600519', '贵州茅台', 55.0, '持有观望', '持有观望', 0.0, 'ok', 'daily')""",
        (today, db),
    )
    conn.commit()
    conn.close()
    result = get_action_list()
    # 无K线 → 纪律扫描无现价可判（降级跳过）；清单正常装配不报错
    assert not [it for it in result['items'] if it['kind'] == 'stop_discipline']
    assert result['stats']['active_count'] == 1
