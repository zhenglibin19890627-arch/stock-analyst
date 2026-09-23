"""
021BK：交易费用估算（021BV 2026-09-24 起银河档免 5 + 分项舍入对齐交割单）

覆盖（离线，纯函数）：
1. 券商匹配：银河万1.853 免5 / 东方财富万1.5 最低5 / 未知名回落默认
2. A股模型：佣金、印花税仅卖出、过户费双边；分项各自取整到分
3. 港股简化模型；dividend 类型与零金额返回 0
4. 021BV 边界：旧地板门槛 26,983.27 两侧连续、大额走费率、东财地板回归
"""

import pytest

import app as app_module
from database import db_manager
from modules.trade_fees import estimate_trade_fee, resolve_broker_profile


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
        """留空佣金 → 自动估算入库并打标记（银河万1.853 免5：10000买入
        = round(1.853)+round(0.1) = 1.95，不再有 5 元地板）。
        注：须显式 account_id 指向银河账户——init_database 预置「默认账户」，
        缺省会命中默认档（万1.5 最低5）。旧断言 5.1 因两档地板同值而误绿。"""
        conn = db_manager.get_connection()
        try:
            galaxy_id = conn.execute(
                "SELECT id FROM accounts WHERE name LIKE '%银河%'").fetchone()['id']
        finally:
            conn.close()
        r = client.post(f"/api/portfolio/holdings/{client._stock_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'trade_date': '2026-09-03', 'account_id': galaxy_id,
        })
        d = r.get_json()
        assert d['success'], d
        assert d['commission'] == pytest.approx(1.95)
        assert d['commission_estimated'] is True
        conn = db_manager.get_connection()
        try:
            row = conn.execute(
                'SELECT commission, commission_estimated FROM trade_records '
                'WHERE id=?', (d['trade_id'],)).fetchone()
            assert row['commission'] == pytest.approx(1.95)
            assert row['commission_estimated'] == 1
        finally:
            conn.close()

    def test_add_trade_default_account_keeps_floor(self, client):
        """缺省账户（init 预置「默认账户」未命中券商 keywords）→ 默认档
        万1.5 最低5：10000 买入 = 5.1（默认档地板回归，021BV 不动）。"""
        r = client.post(f"/api/portfolio/holdings/{client._stock_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'trade_date': '2026-09-03',
        })
        d = r.get_json()
        assert d['success'], d
        assert d['commission'] == pytest.approx(5.1)
        assert d['commission_estimated'] is True

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
        prof = resolve_broker_profile('银河证券')
        assert prof['commission_rate'] == 0.0001853
        assert prof['commission_min'] == 0.0  # 021BV 免5（回退方式见 config 注释）

    def test_eastmoney(self):
        prof = resolve_broker_profile('东方财富')
        assert prof['commission_rate'] == 0.00015
        assert prof['commission_min'] == 5.0  # 东财地板实证保留（8/8 分厘拟合）
        assert resolve_broker_profile('东财账户')['commission_rate'] == 0.00015

    def test_unknown_falls_back(self):
        assert resolve_broker_profile('某某证券') == resolve_broker_profile(None)


class TestAStockFee:
    def test_galaxy_free5_small_amount(self):
        """021BV 免5 生效：10000×0.0001853=1.853（佣金分项 round→1.85）
        + 过户费 0.1 → 1.95；旧口径地板 5 元不再触发。"""
        assert estimate_trade_fee('buy', 10000, '银河证券') == pytest.approx(1.95)

    def test_sell_includes_stamp(self):
        # 银河卖出：佣金 1.85 + 印花税 5.00 + 过户费 0.10（分项舍入求和）
        assert estimate_trade_fee('sell', 10000, '银河证券') == pytest.approx(6.95)

    def test_galaxy_large_amount_rate_applies(self):
        """大额走纯费率：100000×0.0001853=18.53（≥旧地板值，费率真实参与）。"""
        assert estimate_trade_fee('buy', 100000, '银河证券') == pytest.approx(19.53)
        # 卖出：18.53 + 印花税 50.00 + 过户费 1.00
        assert estimate_trade_fee('sell', 100000, '银河证券') == pytest.approx(69.53)

    def test_boundary_26983_continuous_after_free5(self):
        """旧地板门槛 5/0.0001853≈26,983.27 两侧：免5后佣金连续无跳变，
        两侧均为纯费率口径（4.9999/5.0001 分项均 round→5.00）+ 过户费 0.27。"""
        fee_lo = estimate_trade_fee('buy', 26983, '银河证券')
        fee_hi = estimate_trade_fee('buy', 26984, '银河证券')
        assert fee_lo == pytest.approx(5.27)
        assert fee_hi == pytest.approx(5.27)
        assert fee_hi >= fee_lo

    def test_per_component_rounding_aligns_with_statement(self):
        """021BV 分项舍入：分项各自取整到分再求和（交割单口径）。
        自搜索一个「总分先加后舍 vs 分项和」相差 1 分的卖出金额，锁定实现走
        分项口径（t1 §2 审计点6 的 id=59 型 1 分差不再复现）。"""
        found = None
        for amt in range(1000, 30000):
            per_component = (
                round(amt * 0.0001853, 2)
                + round(amt * 0.00001, 2)
                + round(amt * 0.0005, 2)
            )
            total_first = round(
                amt * 0.0001853 + amt * 0.00001 + amt * 0.0005, 2
            )
            if round(per_component, 2) != total_first:
                found = (amt, round(per_component, 2))
                break
        assert found is not None, '未找到分项/总分分异样本（用例构造失效）'
        amt, expected = found
        assert estimate_trade_fee('sell', amt, '银河证券') == pytest.approx(expected)

    def test_eastmoney_floor_regression(self):
        """东财档不动回归：4,674 元买入（万1.5=0.70 地板）仍取最低 5 元
        （t1 §3.2 id=36 实证口径）+ 过户费 0.05。"""
        assert estimate_trade_fee('buy', 4674, '东方财富') == pytest.approx(5.05)

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
        # 港股：佣金 1.85 + 印花税 10.00 + 杂费 1.00（银河免5后）
        assert estimate_trade_fee('buy', 10000, '银河证券', market='hk_stock') == pytest.approx(12.85)

    def test_dividend_no_fee(self):
        assert estimate_trade_fee('dividend', 10000, '银河证券') == 0.0
        assert estimate_trade_fee('dividend_tax', 10000, '银河证券') == 0.0

    def test_zero_amount(self):
        assert estimate_trade_fee('buy', 0, '银河证券') == 0.0
        assert estimate_trade_fee('buy', None, '银河证券') == 0.0
