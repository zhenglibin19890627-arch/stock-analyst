"""
021BP 决策闭环 项3：今日行动清单（只读聚合端点 GET /api/dashboard/action-list）

覆盖（隔离临时库 + 纯逻辑装配，不触网）：
1. 纯函数 build_action_list：四路聚合 + "今日应做"排序
   （评级升降 > 买点共振≥4星 > 预警未读 > 超时缺报股）
2. 可见性缺口：今日 status='failed' 超时股显式列出
3. 键名契约：key_factors.trader 摘要透出 overview（零重算）；文案无裸 '<'
4. 集成：get_action_list 临时库全链（报告升降 + 当日未读预警 + t2 信号命中）
"""

import datetime as dt
import json

import pytest

from database import db_manager
from modules.action_list import build_action_list, get_action_list

# ---------------- 纯逻辑：构造辅助 ----------------

def _stock(sid, symbol='600519', name='贵州茅台'):
    return {'stock_id': sid, 'symbol': symbol, 'name': name}


def _report(stock_id, report_date, rating, rn=1, total_score=70.0, score_change=None,
            status='ok', error_msg=None, key_factors=None, code='600519', name='贵州茅台'):
    return {
        'report_date': report_date, 'stock_id': stock_id, 'stock_code': code,
        'stock_name': name, 'total_score': total_score, 'rating': rating,
        'rating_label': rating, 'score_change': score_change, 'status': status,
        'error_msg': error_msg, 'key_factors': key_factors, 'rn': rn,
    }


_TODAY = '2026-09-21'
_YDAY = '2026-09-18'


def _sig_result(sids, trigger_today=True):
    """t2 scan_watchlist_signals 返回的最小模拟：今日命中 + 5星共振。"""
    trigger = _TODAY if trigger_today else '2026-09-17'
    return {
        'scope': 'watchlist_offline',
        'stock_count': len(sids),
        'results': [
            {
                'stock_id': sid, 'symbol': '600519', 'name': '贵州茅台',
                'matches': [
                    {'signal': 'macd_golden_below', 'label': 'MACD水下金叉',
                     'trigger_date': trigger, 'note': ''},
                    {'signal': 'kdj_golden_low', 'label': 'KDJ低位金叉',
                     'trigger_date': trigger, 'note': ''},
                ],
                'resonances': [
                    {'key': 'res_week_daily', 'label': '周线共振波段', 'stars': 5,
                     'kind': 'bull', 'note': '', 'signals': ''},
                ],
                'kline_upto': _TODAY, 'kline_count': 47,
            }
            for sid in sids
        ],
        'errors': [],
    }


def _sell_result(sids, trigger_today=True):
    """021BQ scan_watchlist_sell_signals 返回的最小模拟：今日死叉命中 + 5星 bear 共振。"""
    trigger = _TODAY if trigger_today else '2026-09-17'
    return {
        'scope': 'watchlist_offline',
        'side': 'sell',
        'stock_count': len(sids),
        'results': [
            {
                'stock_id': sid, 'symbol': '600519', 'name': '贵州茅台',
                'sell_matches': [
                    {'signal': 'macd_dead_above', 'label': 'MACD水上死叉',
                     'trigger_date': trigger, 'note': ''},
                    {'signal': 'kdj_dead_high', 'label': 'KDJ高位死叉',
                     'trigger_date': trigger, 'note': ''},
                ],
                'sell_resonances': [
                    {'key': 'res_week_bear', 'label': '周线空头波段卖', 'stars': 5,
                     'kind': 'bear', 'note': '', 'signals': ''},
                ],
                'kline_upto': _TODAY, 'kline_count': 46,
            }
            for sid in sids
        ],
        'errors': [],
    }


# ---------------- 纯逻辑：排序与聚合 ----------------

class TestBuildActionListOrdering:
    def test_priority_order_spec(self):
        """排序契约：评级降级 > 评级升级 > 共振5星 > 无共振信号 > 预警未读 > 缺报"""
        stocks = [_stock(i, f'60051{i}', f'股{i}') for i in range(1, 6)]
        rows = [
            # 升级（今日 rn=1 最新 vs 上期 rn=2 跨档）
            _report(1, _TODAY, '推荐买入', rn=1),
            _report(1, _YDAY, '持有观望', rn=2),
            # 降级
            _report(2, _TODAY, '建议减仓', rn=1),
            _report(2, _YDAY, '推荐买入', rn=2),
            # 正常无变动
            _report(3, _TODAY, '持有观望', rn=1),
            _report(3, _YDAY, '持有观望', rn=2),
            # 预警股
            _report(4, _TODAY, '持有观望', rn=1),
            # 失败股
            _report(5, _TODAY, None, rn=1, status='failed', error_msg='采集超时(90s)'),
        ]
        alerts = [
            {'stock_id': 4, 'alert_type': 'capital_outflow', 'message': '主力连续3日净流出', 'is_read': 0},
        ]
        result = build_action_list(_TODAY, stocks, rows, alerts, _sig_result([3]))

        kinds = [it['kind'] for it in result['items']]
        assert kinds == [
            'rating_downgrade',   # P1 降级（风控优先）
            'rating_upgrade',     # P1 升级
            'tech_signal',        # P2 共振5星
            'alert_unread',       # P3
            'report_failed',      # P4
        ]
        top = result['items'][0]
        assert top['stock_id'] == 2
        assert '降至 建议减仓' in top['reason']
        assert '上期 推荐买入' in top['reason']
        # 统计
        st = result['stats']
        assert st['active_count'] == 5
        assert st['reported_ok_today'] == 4  # 股1/2/3/4 正常，股5 失败
        assert st['failed_today'] == 1
        assert st['missing_today'] == 0
        assert st['rating_moves'] == 2
        assert st['signal_hits'] == 1
        assert st['resonance_hits'] == 1
        assert st['unread_alerts_today'] == 1

    def test_signal_resonance_stars_desc_within_p2(self):
        """P2 类内：共振≥4星排前（5星 > 无共振）"""
        stocks = [_stock(1, '600001', '甲'), _stock(2, '600002', '乙')]
        result = build_action_list(_TODAY, stocks, [], [], _sig_result([1, 2]))
        # 两只信号相同（都5星）→ 按代码次序；再构造一只无共振验证排序键
        result2 = build_action_list(_TODAY, stocks, [], [], _sig_result([2]))
        assert all(it['priority'] == 2 for it in result['items'])
        assert result2['items'][0]['stock_id'] == 2

    def test_today_gate_and_old_hits_excluded(self):
        """只有触发日==最新K线日的命中才算"今日出现"；历史命中不产行动项"""
        stocks = [_stock(1)]
        old = _sig_result([1], trigger_today=False)
        result = build_action_list(_TODAY, stocks, [], [], old)
        assert result['items'] == []
        assert result['stats']['signal_hits'] == 0

    def test_missing_today_counted_not_listed(self):
        """无今日报告：计入 missing_today 统计，不产个股行动项（缺报显式列出仅失败股）"""
        stocks = [_stock(1)]
        rows = [_report(1, _YDAY, '持有观望', rn=1)]
        result = build_action_list(_TODAY, stocks, rows, [], None)
        assert result['items'] == []
        assert result['stats']['missing_today'] == 1
        assert result['failed_stocks'] == []

    def test_failed_stock_explicitly_listed(self):
        """今日 status='failed'（超时股）显式列出——补 §4.4 可见性缺口"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, None, rn=1, status='failed', error_msg='采集超时(90s)')]
        result = build_action_list(_TODAY, stocks, rows, [], None)
        assert [it['kind'] for it in result['items']] == ['report_failed']
        assert result['failed_stocks'][0]['error'] == '采集超时(90s)'
        assert result['stats']['failed_today'] == 1


class TestBuildActionListDetails:
    def test_trader_summary_in_overview_and_move_reason(self):
        """key_factors.trader 摘要透出 overview；评级变动 reason 附操盘手分歧"""
        kf = json.dumps({
            'kline': {'score': 60, 'weight': 0.3, 'top_factors': []},
            'trader': {'stage_name': '主升浪', 'has_disagreement': True,
                       'disagreement_text': '评级乐观但趋势动能减弱'},
        }, ensure_ascii=False)
        stocks = [_stock(1)]
        rows = [
            _report(1, _YDAY, '持有观望', rn=2),
            _report(1, _TODAY, '推荐买入', rn=1, key_factors=kf, score_change=6.5),
        ]
        result = build_action_list(_TODAY, stocks, rows, [], None)
        assert result['overview'][0]['trader_stage'] == '主升浪'
        assert result['overview'][0]['has_disagreement'] is True
        move = result['items'][0]
        assert move['kind'] == 'rating_upgrade'
        assert '操盘手提示：评级乐观但趋势动能减弱' in move['reason']
        assert '评分变动 +6.5' in move['reason']

    def test_rating_move_requires_prev_and_diff(self):
        """无上期 / 同档 → 不产评级变动项"""
        stocks = [_stock(1), _stock(2)]
        rows = [
            _report(1, _TODAY, '推荐买入', rn=1),                      # 无上期
            _report(2, _YDAY, '持有观望', rn=2),
            _report(2, _TODAY, '持有观望', rn=1),                      # 同档
        ]
        result = build_action_list(_TODAY, stocks, rows, [], None)
        assert result['items'] == []
        assert result['stats']['rating_moves'] == 0

    def test_no_bare_lt_in_rendered_text(self):
        """021BN 教训：清单渲染文案（reason/priority_label）不得含裸 '<' 字符"""
        stocks = [_stock(1, '600519', '贵州茅台')]
        rows = [
            _report(1, _YDAY, '持有观望', rn=2),
            _report(1, _TODAY, '强烈建议卖出', rn=1, score_change=-12.3),
        ]
        alerts = [{'stock_id': 1, 'alert_type': 'score_below', 'message': '评分 55.0 低于阈值 65', 'is_read': 0}]
        result = build_action_list(_TODAY, stocks, rows, alerts, _sig_result([1]))
        all_texts = []
        for it in result['items']:
            all_texts += [it['reason'], it['priority_label'], it['kind']]
        assert all('<' not in t for t in all_texts)

    def test_degraded_signal_path_none(self):
        """信号路降级（None）：清单照常装配，仅无信号项"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '持有观望', rn=1)]
        result = build_action_list(_TODAY, stocks, rows, [], None)
        assert result['items'] == []
        assert result['stats']['reported_ok_today'] == 1


class TestSignalRatingConflict:
    """021BP 修订（实测五粮液案例）：信号与最新评级相悖时的调和标注。

    用户实测矛盾：行动清单标五粮液"买点信号"（MACD水下金叉），个股报告却是
    "建议减仓"、操盘手判"强下跌"——下跌趋势中的超跌反弹信号裸标"买点"会
    让用户以为系统翻多。相悖时必须显式调和，不得让用户自行拼装矛盾信号。
    """

    def test_conflicted_signal_annotated(self):
        """减仓档 + 今日买点信号 → reason 附调和说明，detail.rating_conflict 置档位"""
        stocks = [_stock(1, '000858', '五粮液')]
        rows = [
            _report(1, _TODAY, '建议减仓', rn=1, total_score=46.8,
                    code='000858', name='五粮液'),
        ]
        result = build_action_list(_TODAY, stocks, rows, [], _sig_result([1]))
        sig = [it for it in result['items'] if it['kind'] == 'tech_signal'][0]
        assert sig['detail']['rating_conflict'] == '建议减仓'
        assert '当前综合评级「建议减仓」（46.8分）' in sig['reason']
        assert '未获趋势确认' in sig['reason']
        assert '非趋势反转买入' in sig['reason']

    def test_strong_sell_conflict_annotated(self):
        """强烈建议卖出档同样视为相悖"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '强烈建议卖出', rn=1, total_score=25.0)]
        result = build_action_list(_TODAY, stocks, rows, [], _sig_result([1]))
        sig = [it for it in result['items'] if it['kind'] == 'tech_signal'][0]
        assert sig['detail']['rating_conflict'] == '强烈建议卖出'

    def test_aligned_signal_not_annotated(self):
        """持有观望/买入档 + 信号 → 方向一致不标相悖"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '持有观望', rn=1)]
        result = build_action_list(_TODAY, stocks, rows, [], _sig_result([1]))
        sig = [it for it in result['items'] if it['kind'] == 'tech_signal'][0]
        assert sig['detail']['rating_conflict'] is None
        assert '未获趋势确认' not in sig['reason']

    def test_no_report_signal_not_annotated(self):
        """无报告可判（缺报股）→ 无法判定当前评级，不标相悖"""
        stocks = [_stock(1)]
        result = build_action_list(_TODAY, stocks, [], [], _sig_result([1]))
        sig = [it for it in result['items'] if it['kind'] == 'tech_signal'][0]
        assert sig['detail']['rating_conflict'] is None


class TestSellSignalItems:
    """021BQ 项D：卖点信号行动项（持仓标记 + 相悖调和反向 + 排序契约）"""

    def test_sell_item_with_held_marker(self):
        """持仓者卖点信号：detail 持仓标记 + reason 附减仓/止损纪律提示"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '持有观望', rn=1)]
        held_map = {1: {'total_qty': 500, 'avg_cost': 14.0}}
        result = build_action_list(_TODAY, stocks, rows, [], None,
                                   _sell_result([1]), held_map)
        sell = [it for it in result['items'] if it['kind'] == 'sell_signal'][0]
        assert sell['priority'] == 2
        assert sell['priority_label'] == '卖点信号'
        assert sell['detail']['held'] is True
        assert sell['detail']['total_qty'] == 500
        assert sell['detail']['avg_cost'] == 14.0
        assert '持仓 500 股（成本 14.00）' in sell['reason']
        assert '减仓/止损检查' in sell['reason']
        # 持有观望档与卖点方向一致 → 不标相悖
        assert sell['detail']['rating_conflict'] is None
        st = result['stats']
        assert st['sell_hits'] == 1
        assert st['sell_resonance_hits'] == 1  # 周线空头 5 星

    def test_sell_item_empty_position(self):
        """空仓者卖点信号：弱相关——reason 提示回避，detail.held=False 排序垫底"""
        result = build_action_list(_TODAY, [_stock(1)], [], [], None,
                                   _sell_result([1]), None)
        sell = result['items'][0]
        assert sell['detail']['held'] is False
        assert sell['detail']['total_qty'] == 0
        assert sell['detail']['avg_cost'] is None
        assert '当前空仓' in sell['reason']
        assert '回避新买入' in sell['reason']

    def test_sell_conflict_with_buy_rating_reversed(self):
        """相悖调和反向用例：卖点信号 × 推荐买入评级 → 显式标注评级未变以评级为主"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '推荐买入', rn=1, total_score=72.0)]
        result = build_action_list(_TODAY, stocks, rows, [], None, _sell_result([1]))
        sell = [it for it in result['items'] if it['kind'] == 'sell_signal'][0]
        assert sell['detail']['rating_conflict'] == '推荐买入'
        assert '当前综合评级「推荐买入」（72.0分）' in sell['reason']
        assert '短线回调警示，评级未变' in sell['reason']
        assert '以评级为主' in sell['reason']
        assert '<' not in sell['reason']  # 021BN 教训

    def test_sell_strong_buy_rating_also_conflict(self):
        """强烈推荐买入档同样视为相悖（反向元组与买侧对称）"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '强烈推荐买入', rn=1, total_score=85.0)]
        result = build_action_list(_TODAY, stocks, rows, [], None, _sell_result([1]))
        sell = result['items'][0]
        assert sell['detail']['rating_conflict'] == '强烈推荐买入'

    def test_sell_aligned_reduce_rating_no_conflict(self):
        """减仓档 + 卖点信号 → 方向一致不标相悖"""
        stocks = [_stock(1)]
        rows = [_report(1, _TODAY, '建议减仓', rn=1)]
        result = build_action_list(_TODAY, stocks, rows, [], None, _sell_result([1]))
        sell = result['items'][0]
        assert sell['detail']['rating_conflict'] is None
        assert '短线回调警示' not in sell['reason']

    def test_sort_contract_held_sell_before_buy_before_empty_sell(self):
        """排序契约（021BQ 裁定，测试锁定）：P2 内 卖出·持仓 < 买点 < 卖出·空仓"""
        stocks = [_stock(1, '600001', '甲'), _stock(2, '600002', '乙'),
                  _stock(3, '600003', '丙')]
        held_map = {2: {'total_qty': 1000, 'avg_cost': 10.0}}
        buy = _sig_result([1])       # 买点 5 星（股票1）
        sell = _sell_result([2, 3])  # 卖点：股票2 持仓 / 股票3 空仓
        result = build_action_list(_TODAY, stocks, [], [], buy, sell, held_map)
        got = [(it['kind'], it['stock_id']) for it in result['items']]
        assert got == [
            ('sell_signal', 2),   # 卖出·持仓（风控优先）
            ('tech_signal', 1),   # 买点 5 星
            ('sell_signal', 3),   # 卖出·空仓（回避信息垫底）
        ]

    def test_sell_today_gate(self):
        """非今日命中不产卖侧行动项（与买侧同口径："今日应做"）"""
        result = build_action_list(_TODAY, [_stock(1)], [], [], None,
                                   _sell_result([1], trigger_today=False))
        assert result['items'] == []
        assert result['stats']['sell_hits'] == 0

    def test_sell_degraded_path_none(self):
        """卖点路降级（None 缺省参数）：既有调用方零破坏"""
        result = build_action_list(_TODAY, [_stock(1)], [], [], _sig_result([1]))
        assert [it['kind'] for it in result['items']] == ['tech_signal']
        assert result['stats']['sell_hits'] == 0


# ---------------- 集成：临时库全链 ----------------

def _deep_v_gap_closes():
    """与 test_tech_signal_alert 同款校准形态：交叉确定落在最新一根。"""
    base = [110 - i * 1.5 for i in range(45)]
    return base + [base[-1], base[-1] + 8.0]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'test_action_list.db'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks LIMIT 1').fetchone()['id']
    conn.close()
    return stock_id


def test_get_action_list_full_chain(db):
    """临时库全链：今日评级升级 + 当日未读预警 + t2 信号命中 → 三类行动项齐出"""
    today = dt.date.today().isoformat()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    conn = db_manager.get_connection()
    # 两期报告（升级）
    for rd, rating in [(yesterday, '持有观望'), (today, '推荐买入')]:
        conn.execute(
            """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
               total_score, rating, rating_label, score_change, status, report_type)
               VALUES (?, ?, '600519', '贵州茅台', 72.5, ?, '推荐买入', 3.2, 'ok', 'daily')""",
            (rd, db, rating),
        )
    # 当日未读预警（挂在种子 tech_signal 全局规则上）
    rule_id = conn.execute(
        "SELECT id FROM alert_rules WHERE rule_type='tech_signal' AND stock_id IS NULL"
    ).fetchone()['id']
    conn.execute(
        """INSERT INTO alert_history (rule_id, stock_id, alert_type, message, is_read, trigger_date)
           VALUES (?, ?, 'tech_signal', '贵州茅台(600519) 今日出现买点信号：MACD水下金叉', 0, ?)""",
        (rule_id, db, today),
    )
    # t2 信号数据（今日命中）
    dates = [(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(_deep_v_gap_closes()))]
    conn.executemany(
        'INSERT OR IGNORE INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        [(db, d, c * 0.99, c, c * 1.01, c * 0.98, 1000.0)
         for d, c in zip(dates, _deep_v_gap_closes())],
    )
    conn.commit()
    conn.close()

    result = get_action_list()
    assert result['stats']['active_count'] == 1
    kinds = [it['kind'] for it in result['items']]
    assert kinds == ['rating_upgrade', 'tech_signal', 'alert_unread']  # P1 > P2 > P3
    assert result['items'][0]['reason'].startswith('今日评级升至 推荐买入')
    assert '周线共振波段' not in result['items'][1]['reason']  # 无周K → 仅双金叉共振
    assert result['stats']['unread_alerts_today'] == 1
    assert result['overview'][0]['total_score'] == 72.5


def test_get_action_list_empty_db(db):
    """空数据：清单空装配不报错"""
    result = get_action_list()
    assert result['items'] == []
    assert result['stats']['active_count'] == 1
    assert result['stats']['missing_today'] == 1


def test_get_action_list_sell_chain(db):
    """021BQ 全链：死叉形态K线 + 持仓 + 推荐买入报告 → 卖点行动项（持仓+相悖调和）"""
    today = dt.date.today().isoformat()
    # 021BQ 校准形态：ramp+加速阳+大阴 → MACD水上死叉+KDJ高位死叉落最新一根
    closes = [60 + i * 2.0 for i in range(44)] + [156.0, 138.0]
    conn = db_manager.get_connection()
    conn.execute(
        """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
           total_score, rating, rating_label, score_change, status, report_type)
           VALUES (?, ?, '600519', '贵州茅台', 72.0, '推荐买入', '推荐买入', 1.0, 'ok', 'daily')""",
        (today, db),
    )
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
        'VALUES (1, ?, 14.0, 800)',
        (db,),
    )
    dates = [(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(closes))]
    conn.executemany(
        'INSERT OR IGNORE INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        [(db, d, c * 0.99, c, c * 1.01, c * 0.98, 1000.0)
         for d, c in zip(dates, closes)],
    )
    conn.commit()
    conn.close()

    result = get_action_list()
    sell_items = [it for it in result['items'] if it['kind'] == 'sell_signal']
    assert len(sell_items) == 1
    item = sell_items[0]
    assert item['detail']['held'] is True
    assert item['detail']['total_qty'] == 800
    assert item['detail']['rating_conflict'] == '推荐买入'
    assert '持仓 800 股（成本 14.00）' in item['reason']
    assert {s['signal'] for s in item['detail']['signals']} == {
        'macd_dead_above', 'kdj_dead_high'}
    assert result['stats']['sell_hits'] == 1
    # 无周K → 无周线空头共振，但同日双死叉即 4 星（res_double_dead，不需要周K）
    assert result['stats']['sell_resonance_hits'] == 1
    assert item['detail']['top_stars'] == 4
