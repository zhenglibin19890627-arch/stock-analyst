"""
021BW：录流水费用实时预估（GET /api/portfolio/fee-estimate + 分项纯函数 + 方案 A 映射）

覆盖（离线，隔离临时库，不触网）：
1. 分项纯函数 estimate_trade_fee_items：银河免5/东财地板分列、买卖差异、
   港股简化模型、dividend/零金额空态、与 estimate_trade_fee 总额等价
2. broker 优先映射（方案 A）：resolve_broker_profile_for_account 四分支
3. 端点契约：两档券商、买卖差异、最低佣金触发、缺省账户默认档、400/404/空态、
   amount 直填优先、quantity 截断、文案禁裸 '<'
4. 一致性锁：端点 total == estimate_trade_fee 同参、分项和 == total、
   POST 落库佣金 == 端点预估（预估-落库不分叉）
5. 零写库守卫：GET 前后 trade_records/holdings 行数不变
"""

import pytest

import app as app_module
from database import db_manager
from modules.trade_fees import (
    estimate_trade_fee,
    estimate_trade_fee_items,
    resolve_broker_profile,
    resolve_broker_profile_for_account,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离临时库 + Flask test client（同 test_trade_fees_021bk 模式）。

    预置：银河账户（broker 空）、主账户（broker=东方财富，名不命中 keywords）、
    init 自带「默认账户」；A股与港股各一只。"""
    db_path = str(tmp_path / 'test_fee_est.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()

    r = c.post('/api/accounts', json={'name': '银河证券'})
    assert r.status_code == 200, r.get_json()
    r = c.post('/api/accounts', json={'name': '主账户', 'broker': '东方财富'})
    assert r.status_code == 200, r.get_json()

    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')")
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('00700', 'hk_stock', '腾讯控股')")
        conn.commit()
        c._a_id = conn.execute(
            "SELECT id FROM stocks WHERE symbol='600519'").fetchone()['id']
        c._hk_id = conn.execute(
            "SELECT id FROM stocks WHERE symbol='00700'").fetchone()['id']
        c._galaxy_id = conn.execute(
            "SELECT id FROM accounts WHERE name='银河证券'").fetchone()['id']
        c._east_id = conn.execute(
            "SELECT id FROM accounts WHERE name='主账户'").fetchone()['id']
        c._default_id = conn.execute(
            "SELECT id FROM accounts WHERE name='默认账户'").fetchone()['id']
    finally:
        conn.close()
    return c


def _est(client, **params):
    r = client.get('/api/portfolio/fee-estimate', query_string=params)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


class TestItemsPureFunction:
    """分项纯函数：分列数值、规则文案、与旧总额函数等价。"""

    def test_galaxy_free5_buy_components(self):
        items = estimate_trade_fee_items('buy', 10000, '银河证券')
        assert items['commission'] == pytest.approx(1.85)
        assert items['stamp_tax'] == pytest.approx(0.0)   # 买入无印花税（本身即信息）
        assert items['transfer_fee'] == pytest.approx(0.10)
        assert items['misc_fee'] == pytest.approx(0.0)
        assert items['total'] == pytest.approx(1.95)
        assert items['by_rate'] is True
        assert '免5' in ''.join(items['applied_rules'])
        assert '万1.853' in ''.join(items['applied_rules'])
        assert '银河' in items['broker_label'] and '免5' in items['broker_label']

    def test_galaxy_sell_stamp_single_side(self):
        items = estimate_trade_fee_items('sell', 10000, '银河证券')
        assert items['commission'] == pytest.approx(1.85)
        assert items['stamp_tax'] == pytest.approx(5.00)
        assert items['transfer_fee'] == pytest.approx(0.10)
        assert items['total'] == pytest.approx(6.95)
        assert '仅卖出' in ''.join(items['applied_rules'])

    def test_eastmoney_floor_marks_by_rate_false(self):
        items = estimate_trade_fee_items('buy', 4674, '东方财富')
        assert items['commission'] == pytest.approx(5.00)
        assert items['by_rate'] is False
        assert '最低佣金 5 元' in ''.join(items['applied_rules'])

    def test_hk_simplified_model(self):
        items = estimate_trade_fee_items('buy', 10000, '银河证券', market='hk_stock')
        assert items['commission'] == pytest.approx(1.85)
        assert items['stamp_tax'] == pytest.approx(10.00)   # 港股双边 0.1%
        assert items['transfer_fee'] == pytest.approx(0.0)  # 港股无过户费
        assert items['misc_fee'] == pytest.approx(1.00)
        assert items['total'] == pytest.approx(12.85)
        assert '简化模型' in ''.join(items['applied_rules'])

    def test_dividend_types_all_zero(self):
        for tt in ('dividend', 'dividend_tax'):
            items = estimate_trade_fee_items(tt, 10000, '银河证券')
            assert items['total'] == pytest.approx(0.0)
            assert items['commission'] == pytest.approx(0.0)
            assert items['applied_rules'] == ['分红/红利补税类型不计交易费用']

    def test_zero_or_bad_amount_empty_state(self):
        for amt in (0, None, -5, 'abc'):
            items = estimate_trade_fee_items('buy', amt, '银河证券')
            assert items['total'] == pytest.approx(0.0)
            assert items['applied_rules'] == ['填入成交价和数量后显示预估']

    def test_rules_never_contain_bare_lt(self):
        """文案红线：规则串禁裸 '<'（全口径扫描）。"""
        for tt in ('buy', 'sell', 'dividend'):
            for mkt in ('a_stock', 'hk_stock'):
                for amt in (0, 4674, 26983, 100000):
                    items = estimate_trade_fee_items(tt, amt, '银河证券', mkt)
                    for rule in items['applied_rules']:
                        assert '<' not in rule

    def test_total_equals_legacy_estimate_trade_fee(self):
        """总额等价锁：items['total'] 与 estimate_trade_fee 全参数矩阵一致。"""
        for tt in ('buy', 'sell'):
            for amt in (1, 4674, 26983, 26984, 100000):
                for name in ('银河证券', '东方财富', None):
                    for mkt in ('a_stock', 'hk_stock'):
                        items = estimate_trade_fee_items(tt, amt, name, mkt)
                        assert items['total'] == pytest.approx(
                            estimate_trade_fee(tt, amt, name, mkt))


class TestBrokerResolutionPlanA:
    """方案 A：broker 字段优先、账户名兜底。"""

    def test_broker_field_wins(self):
        prof = resolve_broker_profile_for_account('主账户', '东方财富')
        assert prof['commission_rate'] == pytest.approx(0.00015)
        assert prof['commission_min'] == pytest.approx(5.0)

    def test_broker_unmatched_falls_back_to_name(self):
        prof = resolve_broker_profile_for_account('银河证券', '华泰证券')
        assert prof['commission_rate'] == pytest.approx(0.0001853)
        assert prof['commission_min'] == pytest.approx(0.0)

    def test_broker_empty_uses_name(self):
        assert resolve_broker_profile_for_account('银河证券', '') == \
            resolve_broker_profile('银河证券')
        assert resolve_broker_profile_for_account('银河证券', None) == \
            resolve_broker_profile('银河证券')

    def test_both_empty_default_profile(self):
        prof = resolve_broker_profile_for_account('某某证券', '')
        assert prof == resolve_broker_profile(None)
        assert prof['commission_min'] == pytest.approx(5.0)


class TestFeeEstimateEndpoint:
    """端点契约：两档券商、买卖差异、最低佣金触发、错误分支。"""

    def test_galaxy_buy_free5(self, client):
        d = _est(client, trade_type='buy', price=10, quantity=1000,
                 account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['success'] is True
        assert d['account_name'] == '银河证券'
        assert d['commission'] == pytest.approx(1.85)
        assert d['stamp_tax'] == pytest.approx(0.0)
        assert d['transfer_fee'] == pytest.approx(0.10)
        assert d['total'] == pytest.approx(1.95)
        assert '免5' in ''.join(d['applied_rules'])
        assert '银河' in d['broker_label']
        assert d['input']['market'] == 'a_stock'
        assert d['input']['amount'] == pytest.approx(10000)

    def test_galaxy_sell_includes_stamp(self, client):
        d = _est(client, trade_type='sell', price=10, quantity=1000,
                 account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['stamp_tax'] == pytest.approx(5.00)
        assert d['total'] == pytest.approx(6.95)
        assert '仅卖出' in ''.join(d['applied_rules'])

    def test_eastmoney_floor_via_broker_field(self, client):
        """broker=东方财富、账户名不命中 → 东财档：4,674 买入佣金取地板 5 元。"""
        d = _est(client, trade_type='buy', amount=4674,
                 account_id=client._east_id, stock_id=client._a_id)
        assert d['commission'] == pytest.approx(5.00)
        assert d['by_rate'] is False
        assert '最低佣金 5 元' in ''.join(d['applied_rules'])
        assert '最低佣金5元' in d['broker_label']

    def test_default_account_when_param_missing(self, client):
        """缺省账户 → 默认档（万1.5 最低5）：10,000 买入 = 5.1。"""
        d = _est(client, trade_type='buy', price=10, quantity=1000, stock_id=client._a_id)
        assert d['input']['account_id'] == client._default_id
        assert d['total'] == pytest.approx(5.10)
        assert d['broker_label'].startswith('默认档')

    def test_hk_simplified_model(self, client):
        d = _est(client, trade_type='buy', price=100, quantity=100,
                 account_id=client._galaxy_id, stock_id=client._hk_id)
        assert d['input']['market'] == 'hk_stock'
        assert d['stamp_tax'] == pytest.approx(10.00)
        assert d['transfer_fee'] == pytest.approx(0.0)
        assert d['misc_fee'] == pytest.approx(1.00)
        assert d['total'] == pytest.approx(12.85)
        assert '简化模型' in ''.join(d['applied_rules'])
        assert '简化模型' in d['estimation_note']

    def test_dividend_all_zero(self, client):
        d = _est(client, trade_type='dividend', amount=1000,
                 account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['total'] == pytest.approx(0.0)
        assert d['applied_rules'] == ['分红/红利补税类型不计交易费用']

    def test_empty_state_when_no_price_qty(self, client):
        """空参/0 值不是错误：200 全零 + 空态文案（前端防抖高频触发下无错误分支）。"""
        d = _est(client, trade_type='buy', account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['success'] is True
        assert d['total'] == pytest.approx(0.0)
        assert d['applied_rules'] == ['填入成交价和数量后显示预估']

    def test_zero_and_negative_values_empty_state(self, client):
        for params in (
            {'price': 0, 'quantity': 100},
            {'price': 10, 'quantity': 0},
            {'price': -10, 'quantity': 100},
            {'quantity': -100},
        ):
            d = _est(client, trade_type='buy', account_id=client._galaxy_id,
                     stock_id=client._a_id, **params)
            assert d['success'] is True
            assert d['total'] == pytest.approx(0.0)

    def test_bad_price_400(self, client):
        r = client.get('/api/portfolio/fee-estimate',
                       query_string={'trade_type': 'buy', 'price': 'abc'})
        assert r.status_code == 400
        assert r.get_json()['success'] is False

    def test_bad_trade_type_400(self, client):
        r = client.get('/api/portfolio/fee-estimate',
                       query_string={'trade_type': 'foo', 'price': 10, 'quantity': 100})
        assert r.status_code == 400
        assert 'trade_type' in r.get_json()['message']

    def test_missing_account_404(self, client):
        r = client.get('/api/portfolio/fee-estimate',
                       query_string={'trade_type': 'buy', 'price': 10, 'quantity': 100,
                                     'account_id': 99999})
        assert r.status_code == 404
        assert r.get_json()['success'] is False

    def test_amount_direct_overrides_price_qty(self, client):
        """amount 直填优先于 价×量（与 POST L277 落库基数同式，预估永不分叉）。"""
        d = _est(client, trade_type='buy', price=10, quantity=1000, amount=20000,
                 account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['input']['amount'] == pytest.approx(20000)
        assert d['commission'] == pytest.approx(3.71)   # 20000×0.0001853
        assert d['total'] == pytest.approx(3.91)        # 而非 1 万的 1.95

    def test_quantity_int_truncation(self, client):
        d = _est(client, trade_type='buy', price=10, quantity=1234.9,
                 account_id=client._galaxy_id, stock_id=client._a_id)
        assert d['input']['quantity'] == 1234
        assert d['total'] == pytest.approx(2.41)   # 12340：2.29 佣金 + 0.12 过户费


class TestConsistencyLocks:
    """防两处口径漂移的锁：端点 vs 纯函数、分项和 vs 总额、预估 vs 落库。"""

    def test_endpoint_matches_pure_function_matrix(self, client):
        combos = [
            ('buy', 10000, client._galaxy_id, client._a_id, 'a_stock'),
            ('sell', 10000, client._galaxy_id, client._a_id, 'a_stock'),
            ('buy', 26983, client._galaxy_id, client._a_id, 'a_stock'),
            ('sell', 26984, client._galaxy_id, client._a_id, 'a_stock'),
            ('buy', 100000, client._galaxy_id, client._a_id, 'a_stock'),
            ('buy', 100000, client._east_id, client._a_id, 'a_stock'),
            ('sell', 4674, client._east_id, client._a_id, 'a_stock'),
            ('buy', 10000, client._galaxy_id, client._hk_id, 'hk_stock'),
            ('sell', 10000, client._galaxy_id, client._hk_id, 'hk_stock'),
        ]
        for tt, amt, acc_id, sid, mkt in combos:
            d = _est(client, trade_type=tt, amount=amt, account_id=acc_id, stock_id=sid)
            legacy = estimate_trade_fee(tt, amt, d['account_name'], mkt)
            assert d['total'] == pytest.approx(legacy), (tt, amt, acc_id, mkt)
            parts = d['commission'] + d['stamp_tax'] + d['transfer_fee'] + d['misc_fee']
            assert d['total'] == pytest.approx(parts), (tt, amt, acc_id, mkt)

    def test_post_stored_commission_equals_estimate(self, client):
        """021BV/021BW 核心承诺：落库自动估算 == 预估条（同参数完全一致）。

        用「名含银河、broker=东方财富」的账户锁 broker 优先在两端同步生效：
        若任一端仍按账户名匹配，佣金会是 19.53（银河档）而非 16.00（东财档）。"""
        r = client.post('/api/accounts', json={'name': '我的银河账户', 'broker': '东方财富'})
        assert r.status_code == 200, r.get_json()
        acc_id = r.get_json()['account_id']

        est = _est(client, trade_type='buy', amount=100000,
                   account_id=acc_id, stock_id=client._a_id)
        assert est['commission'] == pytest.approx(15.00)   # 东财费率 15，非银河 18.53
        assert est['total'] == pytest.approx(16.00)

        r = client.post(f"/api/portfolio/holdings/{client._a_id}/trades", json={
            'trade_type': 'buy', 'price': 100.0, 'quantity': 1000,
            'trade_date': '2026-09-24', 'account_id': acc_id,
        })
        d = r.get_json()
        assert d['success'], d
        assert d['commission'] == pytest.approx(est['total'])
        assert d['commission_estimated'] is True


class TestReadOnlyGuard:
    def test_get_never_writes(self, client):
        """零写库守卫：GET 前后 trade_records/holdings 行数不变。"""
        r = client.post(f"/api/portfolio/holdings/{client._a_id}/trades", json={
            'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
            'trade_date': '2026-09-24', 'account_id': client._galaxy_id,
        })
        assert r.get_json()['success'], r.get_json()

        def counts():
            conn = db_manager.get_connection()
            try:
                return (
                    conn.execute('SELECT COUNT(*) c FROM trade_records').fetchone()['c'],
                    conn.execute('SELECT COUNT(*) c FROM holdings').fetchone()['c'],
                )
            finally:
                conn.close()

        before = counts()
        for params in (
            {'trade_type': 'buy', 'price': 10, 'quantity': 1000},
            {'trade_type': 'sell', 'amount': 5000},
            {'trade_type': 'dividend', 'amount': 100},
            {'trade_type': 'buy'},   # 空态分支
        ):
            d = _est(client, account_id=client._galaxy_id,
                     stock_id=client._a_id, **params)
            assert d['success'] is True
        assert counts() == before
