"""
021BR t3：price_advisor 成本口径账户无关聚合（021W 残留收口）专项测试

诊断 A4/A5（t1）：_read_cost_price 旧「ORDER BY quantity DESC LIMIT 1」单行取
最大账户成本（中免银河 61.05），与 trader_advisor/action_list 聚合口径 57.53
双基数并存 → 价格建议止损 56.16 与纪律线 52.93 同日双数值。
修复：holdings 改 021BQ 同款 SUM 聚合（数量汇总 + 加权成本），三处同口径；
positions 表 fallback 保留。
"""

import pytest

import app as app_module
from database import db_manager
from modules.price_advisor import _read_cost_price


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_pa_021br.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()
    conn = db_manager.get_connection()
    try:
        conn.execute("INSERT INTO accounts (name) VALUES ('账户A')")
        conn.execute("INSERT INTO accounts (name) VALUES ('账户B')")
        conn.commit()
    finally:
        conn.close()
    return c


def _stock(client, symbol='601888'):
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES (?, 'a_stock', '测试股')",
            (symbol,))
        conn.commit()
        return conn.execute(
            'SELECT id FROM stocks WHERE symbol=?', (symbol,)).fetchone()['id']
    finally:
        conn.close()


class TestCostAggregation021BR:
    """_read_cost_price 账户无关聚合（与 trader_advisor/action_list 同口径）"""

    def test_multi_account_weighted(self, client):
        """中免实况口径：1100@61.046 + 1000@53.6686 → 加权 57.5330（非单行 61.046）"""
        sid = _stock(client)
        conn = db_manager.get_connection()
        try:
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (1, ?, 61.046, 1100)', (sid,))
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (2, ?, 53.6686, 1000)', (sid,))
            conn.commit()
        finally:
            conn.close()
        cost = _read_cost_price(sid)
        assert cost == pytest.approx(57.5330, abs=0.001)

    def test_ignores_zero_qty_rows(self, client):
        """清仓行（quantity=0）不计入聚合"""
        sid = _stock(client)
        conn = db_manager.get_connection()
        try:
            conn.execute(
                "INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) "
                "VALUES (1, ?, 99.0, 0, 'cleared')", (sid,))
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (2, ?, 20.0, 500)', (sid,))
            conn.commit()
        finally:
            conn.close()
        assert _read_cost_price(sid) == pytest.approx(20.0, abs=0.001)

    def test_no_holding_returns_none(self, client):
        sid = _stock(client)
        assert _read_cost_price(sid) is None

    def test_positions_fallback_kept(self, client):
        """holdings 无行 → positions 表 fallback 保留（021S 前兼容）"""
        sid = _stock(client)
        conn = db_manager.get_connection()
        try:
            conn.execute(
                'INSERT INTO positions (stock_id, cost_price, quantity) VALUES (?, 15.0, 300)',
                (sid,))
            conn.commit()
        finally:
            conn.close()
        assert _read_cost_price(sid) == pytest.approx(15.0, abs=0.001)
