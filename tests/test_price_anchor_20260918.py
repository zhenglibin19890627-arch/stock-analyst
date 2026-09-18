"""
2026-09-18（A 方向）：止盈棘轮 + 止损成本底线专项测试

用户痛点：止盈/止损/网格每日按新现价重画——止盈追涨永远差一口气、止损阴跌无底线。
修复（price_advisor._gen_with_position）：
- 止损成本底线：stop_loss = max(现价×(1-11%), 成本×(1-8%))——固定最大亏损锁定
- 止盈棘轮（滚动窗口10天）：take_profit = max(公式值, 近10天历史报告最高止盈)

隔离临时库 + 直接构造 daily_reports 历史快照，走 generate_price_advice 全流程。
"""

import pytest

import app as app_module
from database import db_manager
from modules.price_advisor import _gen_with_position, generate_price_advice


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_ratchet.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()

    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('300999', 'a_stock', '测试股')")
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT id FROM stocks WHERE symbol='300999'")
        c._stock_id = cur.fetchone()['id']
        c._account_id = conn.execute(
            "SELECT id FROM accounts WHERE is_default=1").fetchone()['id']
    finally:
        conn.close()
    return c


def _seed_holding(stock_id, account_id, cost):
    """直接写一行持仓（模拟已持仓状态）"""
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) "
            "VALUES (?, ?, ?, 1000, 'active')",
            (account_id, stock_id, cost),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_kline(stock_id, close, days=30):
    """写入一段平价K线（close 恒定，MA/BOLL/ATR 可算）"""
    conn = db_manager.get_connection()
    try:
        for i in range(days, 0, -1):
            conn.execute(
                "INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) "
                "VALUES (?, date('2026-09-18', ?), ?, ?, ?, ?, 1000)",
                (stock_id, f'-{i} day', close, close, close * 1.01, close * 0.99),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_history_tp(stock_id, rows):
    """写入历史报告快照（has_position 口径，带 take_profit）"""
    conn = db_manager.get_connection()
    import json
    try:
        for date, tp in rows:
            pa = {'has_position': True, 'take_profit': tp, 'stop_loss': tp * 0.9}
            conn.execute(
                "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, "
                "engine_version, total_score, rating, key_factors, status, report_type, price_advice) "
                "VALUES (?, ?, '300999', '测试股', 'v5', 50, '持有观望', '{}', 'ok', 'daily', ?)",
                (date, stock_id, json.dumps(pa)),
            )
        conn.commit()
    finally:
        conn.close()


class TestStopCostFloor:
    """止损成本底线：浮亏有固定最大亏损，不再无底线下移"""

    def test_floor_lifts_stop_when_price_low(self):
        # close=10, 成本=12：现价锚 10×0.89=8.9 < 底线 12×0.92=11.04 → 底线生效
        r = _gen_with_position(10.0, 12.0, '持有观望', ma60=None, boll_upper=None,
                               atr=None, market='a_stock')
        assert r['stop_loss'] == pytest.approx(11.04, abs=0.01)
        assert r['stop_cost_floor'] == pytest.approx(11.04, abs=0.01)

    def test_price_anchor_wins_when_higher(self):
        # close=13, 成本=12：现价锚 13×0.89=11.57 > 底线 11.04 → 现价锚生效（浮盈保护）
        r = _gen_with_position(13.0, 12.0, '持有观望', ma60=None, boll_upper=None,
                               atr=None, market='a_stock')
        assert r['stop_loss'] == pytest.approx(11.57, abs=0.01)

    def test_deep_loss_triggers_s4(self):
        # 深亏：close=10 已破底线 11.04 → 状态机 S4 建议止损清仓（特性：固定最大亏损）
        r = _gen_with_position(10.0, 12.0, '持有观望', ma60=None, boll_upper=None,
                               atr=None, market='a_stock')
        assert r['state'] == 'S4'
        assert '止损' in r['action_suggestion'] or '清仓' in r['action_suggestion']

    def test_hk_floor_wider(self):
        # 港股底线 -14%：12×0.86=10.32
        r = _gen_with_position(10.0, 12.0, '持有观望', ma60=None, boll_upper=None,
                               atr=None, market='hk_stock')
        assert r['stop_loss'] == pytest.approx(10.32, abs=0.01)


class TestTakeProfitRatchet:
    """止盈棘轮（滚动窗口）：止盈价只升不降"""

    def test_ratchet_lifts_tp_above_formula(self, client):
        sid = client._stock_id
        _seed_holding(sid, client._account_id, 12.0)
        _seed_kline(sid, 10.0)
        # 3 天前历史止盈 11.2 > 当日公式值（10×1.075 封顶=10.75）→ 棘轮抬到 11.2
        _seed_history_tp(sid, [('2026-09-15', 11.2), ('2026-09-10', 11.0)])
        r = generate_price_advice(sid, {'rating': '持有观望', 'latest_close': 10.0,
                                        'market': 'a_stock'})
        assert r['available'] and r['has_position']
        assert r['take_profit'] == pytest.approx(11.2, abs=0.01)
        assert r['tp_ratchet_base'] == pytest.approx(11.2, abs=0.01)

    def test_formula_wins_when_higher(self, client):
        sid = client._stock_id
        _seed_holding(sid, client._account_id, 9.0)
        _seed_kline(sid, 10.0)
        # 历史止盈 10.2 < 当日公式值 11.0（min_tp 10.4, fixed 11.2, 阻力回退 11.0）→ 公式值生效
        _seed_history_tp(sid, [('2026-09-15', 10.2)])
        r = generate_price_advice(sid, {'rating': '持有观望', 'latest_close': 10.0,
                                        'market': 'a_stock'})
        assert r['take_profit'] == pytest.approx(11.0, abs=0.01)

    def test_window_expiry(self, client):
        sid = client._stock_id
        _seed_holding(sid, client._account_id, 12.0)
        _seed_kline(sid, 10.0)
        # 窗口外（>10 天前）的历史止盈过期 → 退化为公式值 11.0
        _seed_history_tp(sid, [('2026-09-01', 15.0)])
        r = generate_price_advice(sid, {'rating': '持有观望', 'latest_close': 10.0,
                                        'market': 'a_stock'})
        assert r['take_profit'] == pytest.approx(11.0, abs=0.01)
        assert r['tp_ratchet_base'] is None

    def test_no_history_falls_back(self, client):
        sid = client._stock_id
        _seed_holding(sid, client._account_id, 9.0)
        _seed_kline(sid, 10.0)
        # 无历史快照 → 纯公式值 11.0
        r = generate_price_advice(sid, {'rating': '持有观望', 'latest_close': 10.0,
                                        'market': 'a_stock'})
        assert r['take_profit'] == pytest.approx(11.0, abs=0.01)
        assert r['tp_ratchet_base'] is None
