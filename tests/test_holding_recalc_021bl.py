"""
021BL：持仓重算引擎——券商口径（摊薄成本 + 持仓盈亏）

验证（隔离临时库，走 API 全流程）：
1. 买入优先用实际成交金额（021AM 金额直填），缺失回退 价格×数量；佣金计入成本
2. 卖出不改摊薄成本价；已实现盈亏 = 卖出金额 - 费用 - 卖出数量×摊薄成本
3. 补仓后摊薄成本重算（不是停留在买入价）
4. 清仓后 status=cleared、成本价归零（券商口径，不残留旧成本）
5. 盈亏比例字段：无价格时为 None
"""

import pytest

import app as app_module
from database import db_manager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_recalc.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()

    r = c.post('/api/accounts', json={'name': '银河证券'})
    assert r.status_code == 200, r.get_json()
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')")
        conn.commit()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM stocks WHERE symbol='600519'")
        c._stock_id = cursor.fetchone()['id']
    finally:
        conn.close()
    return c


def _add_trade(client, stock_id, **body):
    r = client.post(f'/api/portfolio/holdings/{stock_id}/trades', json=body)
    d = r.get_json()
    assert d['success'], d
    return d['recalculated_position']


class TestDilutedCostBrokerConvention:
    def test_full_lifecycle(self, client):
        sid = client._stock_id
        # 1. 买入 1000@10（金额直填 10000），佣金 5 → 摊薄成本 10.005
        p = _add_trade(client, sid, trade_type='buy', price=10.0, quantity=1000,
                       amount=10000, commission=5, trade_date='2026-09-01')
        assert p['quantity'] == 1000
        assert p['avg_cost'] == pytest.approx(10.005)
        assert p['realized_pnl'] == 0

        # 2. 卖出 500@12（金额直填 6000），佣金 5
        #    已实现 = 6000 - 5 - 500×10.005 = 992.5；摊薄成本不变（券商口径）
        p = _add_trade(client, sid, trade_type='sell', price=12.0, quantity=500,
                       amount=6000, commission=5, trade_date='2026-09-02')
        assert p['quantity'] == 500
        assert p['avg_cost'] == pytest.approx(10.005)
        assert p['realized_pnl'] == pytest.approx(992.5)

        # 3. 补仓 500@9（金额直填 4500），佣金 5
        #    新摊薄成本 = (10.005×500 + 4505) / 1000 = 9.5075（交易后算出新成本）
        p = _add_trade(client, sid, trade_type='buy', price=9.0, quantity=500,
                       amount=4500, commission=5, trade_date='2026-09-03')
        assert p['quantity'] == 1000
        assert p['avg_cost'] == pytest.approx(9.5075)
        assert p['realized_pnl'] == pytest.approx(992.5)

        # 4. 清仓 1000@9.5（金额直填 9500），佣金 5
        #    已实现 += 9500 - 5 - 1000×9.5075 = -12.5 → 累计 980.0
        p = _add_trade(client, sid, trade_type='sell', price=9.5, quantity=1000,
                       amount=9500, commission=5, trade_date='2026-09-04')
        assert p['quantity'] == 0
        assert p['status'] == 'cleared'
        assert p['avg_cost'] == 0          # 清仓后成本归零，不残留
        assert p['realized_pnl'] == pytest.approx(980.0)

        # 5. 列表行：cleared 行成本价 0、盈亏比例 None（无实时价）
        r = client.get('/api/portfolio/holdings')
        rows = [h for h in r.get_json()['holdings'] if h['stock_id'] == sid]
        assert rows, '持仓行缺失'
        h = rows[0]
        assert h['status'] == 'cleared'
        assert (h['cost_price'] or 0) == 0
        assert h['unrealized_pnl_pct'] is None
        assert h['realized_pnl'] == pytest.approx(980.0)

    def test_amount_direct_fill_wins_over_price(self, client):
        """金额直填优先于 价格×数量（与券商实际成交金额口径一致）。"""
        sid = client._stock_id
        # 价×量=10000，但实际成交金额 9800（如含零股/折算差异）→ 成本按 9800
        p = _add_trade(client, sid, trade_type='buy', price=10.0, quantity=1000,
                       amount=9800, commission=4.9, trade_date='2026-09-01')
        assert p['avg_cost'] == pytest.approx(9.8049, abs=1e-3)

    def test_amount_fallback_to_price_times_qty(self, client):
        """金额缺失回退 价格×数量（旧数据兼容）。"""
        sid = client._stock_id
        p = _add_trade(client, sid, trade_type='buy', price=20.0, quantity=100,
                       amount=None, commission=5, trade_date='2026-09-01')
        assert p['avg_cost'] == pytest.approx(20.05)
