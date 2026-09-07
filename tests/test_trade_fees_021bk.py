"""
021BK：交易费用估算

覆盖（离线，纯函数）：
1. 券商匹配：银河万1.8 / 东方财富万1.5 / 未知名回落默认
2. A股模型：佣金最低 5 元、印花税仅卖出、过户费双边
3. 港股简化模型；dividend 类型与零金额返回 0
"""

import pytest

from modules.trade_fees import estimate_trade_fee, resolve_broker_profile

import app as app_module
from database import db_manager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离临时库 + Flask test client（同 test_routes 模式），预置银河账户与一只股票。"""
    db_path = str(tmp_path / 'test_fees.db')
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


class TestFeeEstimationApi:
    def test_add_trade_empty_commission_auto_estimates(self, client):
        """留空佣金 → 自动估算入库并打标记（银河万1.8：10000买入 = 5 + 0.1）。"""
        r = client.post(f"/api/portfolio/holdings/{client._stock_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'trade_date': '2026-09-03',
        })
        d = r.get_json()
        assert d['success'], d
        assert d['commission'] == pytest.approx(5.1)
        assert d['commission_estimated'] is True
        conn = db_manager.get_connection()
        try:
            row = conn.execute(
                'SELECT commission, commission_estimated FROM trade_records '
                'WHERE id=?', (d['trade_id'],)).fetchone()
            assert row['commission'] == pytest.approx(5.1)
            assert row['commission_estimated'] == 1
        finally:
            conn.close()

    def test_add_trade_explicit_commission_respected(self, client):
        """明确填写佣金 → 完全尊重，不打估算标记。"""
        r = client.post(f"/api/portfolio/holdings/{client._stock_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'commission': 3.5, 'trade_date': '2026-09-03',
        })
        d = r.get_json()
        assert d['success'] and d['commission'] == 3.5
        assert d['commission_estimated'] is False

    def test_edit_commission_clears_estimated_flag(self, client, monkeypatch):
        """隔天交割单 → 编辑为实际值 → 标记清除、持仓重算（测试内关闭 T+1 锁）。"""
        import blueprints.portfolio as portfolio_mod

        r = client.post(f"/api/portfolio/holdings/{client._stock_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'trade_date': '2026-09-03',
        })
        trade_id = r.get_json()['trade_id']

        monkeypatch.setattr(portfolio_mod, 'TRADE_T1_LOCK_ENABLED', False)
        r2 = client.put(f'/api/portfolio/trades/{trade_id}',
                        json={'commission': 6.8})
        assert r2.get_json()['success'], r2.get_json()

        conn = db_manager.get_connection()
        try:
            row = conn.execute(
                'SELECT commission, commission_estimated FROM trade_records '
                'WHERE id=?', (trade_id,)).fetchone()
            assert row['commission'] == 6.8
            assert row['commission_estimated'] == 0
        finally:
            conn.close()


class TestBrokerProfile:
    def test_galaxy(self):
        assert resolve_broker_profile('银河证券')['commission_rate'] == 0.00018

    def test_eastmoney(self):
        assert resolve_broker_profile('东方财富')['commission_rate'] == 0.00015
        assert resolve_broker_profile('东财账户')['commission_rate'] == 0.00015

    def test_unknown_falls_back(self):
        assert resolve_broker_profile('某某证券') == resolve_broker_profile(None)


class TestAStockFee:
    def test_buy_small_amount_floor(self):
        # 银河万1.8：10000×0.00018=1.8 < 5 → 取最低 5 元 + 过户费 0.1
        assert estimate_trade_fee('buy', 10000, '银河证券') == 5.1

    def test_sell_includes_stamp(self):
        # 卖出：5 + 印花税 5 + 过户费 0.1 = 10.1
        assert estimate_trade_fee('sell', 10000, '银河证券') == pytest.approx(10.1)

    def test_buy_large_amount_rate_applies(self):
        # 东财万1.5：100000×0.00015=15 > 5 → 佣金 15 + 过户费 1
        assert estimate_trade_fee('buy', 100000, '东方财富') == pytest.approx(16.0)

    def test_sell_large_amount(self):
        # 东财：15 + 印花税 50 + 过户费 1 = 66
        assert estimate_trade_fee('sell', 100000, '东方财富') == pytest.approx(66.0)

    def test_default_broker_when_no_name(self):
        # 未知名 → 默认档（万1.5 最低5）：10000 买入 = 5 + 0.1
        assert estimate_trade_fee('buy', 10000, None) == 5.1


class TestHkAndEdge:
    def test_hk_buy(self):
        # 港股：佣金 5 + 印花税 10 + 杂费 1 = 16
        assert estimate_trade_fee('buy', 10000, '银河证券', market='hk_stock') == pytest.approx(16.0)

    def test_dividend_no_fee(self):
        assert estimate_trade_fee('dividend', 10000, '银河证券') == 0.0
        assert estimate_trade_fee('dividend_tax', 10000, '银河证券') == 0.0

    def test_zero_amount(self):
        assert estimate_trade_fee('buy', 0, '银河证券') == 0.0
        assert estimate_trade_fee('buy', None, '银河证券') == 0.0
