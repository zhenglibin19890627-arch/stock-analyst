"""
021AM/021CB：红利补税（dividend_tax）与分红（dividend）流水单元/路由测试

覆盖：
1. 新增 dividend_tax 流水：独立日期独立金额，持仓中折回成本（021CB 红到成本口径）
2. 分红 + 补税组合：成本净变化 = −(分红 − 补税)
3. 编辑 dividend_tax（改金额/日期）触发重算
4. 删除 dividend_tax 回滚成本
5. 全局流水汇总：total_dividend_tax 单列 + net_amount 计入补税
6. 筛选参数 type=dividend_tax
7. 非法类型仍被拒绝
注：021CB 起分红/补税持仓中折进成本价（对齐券商 App 摊薄口径），
不再计入已实现盈亏（清仓后的分红/补税仍走已实现盈亏）。
"""

import pytest

from database import db_manager


@pytest.fixture()
def setup_env(tmp_path, monkeypatch):
    """隔离库 + 1 只股票 + 买入 1000 股 @10（commission 显式 0，成本基数恰为 10000）。"""
    db_path = str(tmp_path / 'test_divtax.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    import app as app_module

    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks').fetchone()['id']
    conn.close()

    client = app_module.app.test_client()
    # 默认账户买入建仓（commission 显式 0 → 隔离费用估算，成本基数 = 10000）
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'buy', 'price': 10.0, 'quantity': 1000,
              'commission': 0.0, 'trade_date': '2026-06-01'},
    )
    assert r.get_json()['success']
    return client, stock_id


def _holding_field(client, stock_id, field):
    body = client.get('/api/portfolio/holdings?stock_id=' + str(stock_id)).get_json()
    rows = body.get('holdings') or body.get('data') or []
    for h in rows:
        if h.get('stock_id') == stock_id:
            return h.get(field)
    return None


def test_add_dividend_tax_folds_into_cost(setup_env):
    """021CB：分红 200 折进成本、补税 40 折回成本 → 成本 = (10000−200+40)/1000 = 9.84；
    已实现盈亏保持 0（分红不再计入已实现盈亏）。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend', 'amount': 200.0, 'trade_date': '2026-07-01',
              'notes': '中期派息'},
    )
    assert r.get_json()['success']
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05',
              'notes': '7/1 派息的差异扣税'},
    )
    body = r.get_json()
    assert body['success']
    pos = body['recalculated_position']
    assert pos['avg_cost'] == pytest.approx(9.84)
    assert pos['realized_pnl'] == pytest.approx(0.0)
    assert _holding_field(client, stock_id, 'cost_price') == pytest.approx(9.84)


def test_dividend_tax_standalone(setup_env):
    """021CB：只有补税没有分红（历史补录场景）→ 成本 = (10000+40)/1000 = 10.04。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    )
    body = r.get_json()
    assert body['success']
    assert body['recalculated_position']['avg_cost'] == pytest.approx(10.04)
    assert body['recalculated_position']['realized_pnl'] == pytest.approx(0.0)


def test_edit_dividend_tax_recalculates(setup_env):
    """021CB：编辑补税金额 40 → 25：成本随之更新 = (10000−200+25)/1000 = 9.825。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend', 'amount': 200.0, 'trade_date': '2026-07-01'},
    ).get_json()
    r2 = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    ).get_json()
    tax_id = r2['trade_id']

    # T+1 锁定：当日（今天）不可改——用未来日期规避测试环境时间差异不可行，
    # 直接改库把 created_at 拨回昨天再走 API
    conn = db_manager.get_connection()
    conn.execute(
        "UPDATE trade_records SET created_at = datetime('now', 'localtime', '-2 day') WHERE id = ?",
        (tax_id,),
    )
    conn.commit()
    conn.close()

    r3 = client.put(
        f'/api/portfolio/trades/{tax_id}',
        json={'trade_type': 'dividend_tax', 'amount': 25.0, 'trade_date': '2026-08-05'},
    ).get_json()
    assert r3['success']
    assert r3['recalculated_position']['avg_cost'] == pytest.approx(9.825)
    assert r3['recalculated_position']['realized_pnl'] == pytest.approx(0.0)


def test_delete_dividend_tax_restores_cost(setup_env):
    """021CB：删除补税流水 → 成本回滚 = (10000−200)/1000 = 9.80。"""
    client, stock_id = setup_env
    r2 = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend', 'amount': 200.0, 'trade_date': '2026-07-01'},
    ).get_json()
    r3 = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    ).get_json()
    tax_id = r3['trade_id']
    conn = db_manager.get_connection()
    conn.execute(
        "UPDATE trade_records SET created_at = datetime('now', 'localtime', '-2 day') WHERE id = ?",
        (tax_id,),
    )
    conn.commit()
    conn.close()

    r4 = client.delete(f'/api/portfolio/trades/{tax_id}').get_json()
    assert r4['success']
    assert r4['recalculated_position']['avg_cost'] == pytest.approx(9.80)
    assert r4['recalculated_position']['realized_pnl'] == pytest.approx(0.0)


def test_dividend_after_cleared_goes_realized(setup_env):
    """021CB 边界：清仓后到账的分红无成本可摊 → 计入已实现盈亏（而非负成本）。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'sell', 'price': 10.0, 'quantity': 1000,
              'commission': 0.0, 'trade_date': '2026-06-20'},
    ).get_json()
    assert r['success']
    assert r['recalculated_position']['status'] == 'cleared'
    r2 = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend', 'amount': 88.0, 'trade_date': '2026-07-01'},
    ).get_json()
    assert r2['success']
    pos = r2['recalculated_position']
    assert pos['realized_pnl'] == pytest.approx(88.0)
    assert pos['quantity'] == 0


def test_all_trades_summary_includes_tax(setup_env):
    """全局汇总：total_dividend_tax 单列，net_amount 扣除补税。"""
    client, stock_id = setup_env
    client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend', 'amount': 200.0, 'trade_date': '2026-07-01'},
    )
    client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    )
    body = client.get('/api/portfolio/trades').get_json()
    s = body['summary']
    assert s['total_dividend'] == pytest.approx(200.0)
    assert s['total_dividend_tax'] == pytest.approx(40.0)
    # net = sell(0) + dividend(200) − tax(40) − buy(10000) = -9840
    assert s['net_amount'] == pytest.approx(-9840.0)

    # 筛选
    body2 = client.get('/api/portfolio/trades?type=dividend_tax').get_json()
    assert body2['count'] == 1
    assert body2['trades'][0]['trade_type'] == 'dividend_tax'


def test_invalid_type_rejected(setup_env):
    """非法类型仍被拒绝。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    )
    assert r.status_code == 400
