"""
021AM：红利补税（dividend_tax）流水单元/路由测试

覆盖：
1. 新增 dividend_tax 流水：独立日期独立金额，realized_pnl 正确扣减
2. 分红 + 补税组合：net = 分红 − 补税
3. 编辑 dividend_tax（改金额/日期）触发重算
4. 删除 dividend_tax 回滚盈亏
5. 全局流水汇总：total_dividend_tax 单列 + net_amount 计入补税
6. 筛选参数 type=dividend_tax
7. 非法类型仍被拒绝
"""

import pytest

from database import db_manager


@pytest.fixture()
def setup_env(tmp_path, monkeypatch):
    """隔离库 + 1 只股票 + 买入 1000 股 @10（realized_pnl 基线 0）。"""
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
    # 默认账户买入建仓
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'buy', 'price': 10.0, 'quantity': 1000, 'trade_date': '2026-06-01'},
    )
    assert r.get_json()['success']
    return client, stock_id


def _realized_pnl(client, stock_id):
    body = client.get('/api/portfolio/holdings?stock_id=' + str(stock_id)).get_json()
    rows = body.get('holdings') or body.get('data') or []
    for h in rows:
        if h.get('stock_id') == stock_id:
            return h.get('realized_pnl')
    return None


def test_add_dividend_tax_reduces_pnl(setup_env):
    """分红 200 → 补税 40：realized_pnl = 200 − 40 = 160（补税日期独立）。"""
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
    assert body['recalculated_position']['realized_pnl'] == pytest.approx(160.0)


def test_dividend_tax_standalone(setup_env):
    """只有补税没有分红（历史补录场景）：realized_pnl = −40。"""
    client, stock_id = setup_env
    r = client.post(
        f'/api/portfolio/holdings/{stock_id}/trades',
        json={'trade_type': 'dividend_tax', 'amount': 40.0, 'trade_date': '2026-08-05'},
    )
    body = r.get_json()
    assert body['success']
    assert body['recalculated_position']['realized_pnl'] == pytest.approx(-40.0)


def test_edit_dividend_tax_recalculates(setup_env):
    """编辑补税金额 40 → 25：盈亏随之更新。"""
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
    assert r3['recalculated_position']['realized_pnl'] == pytest.approx(175.0)


def test_delete_dividend_tax_restores_pnl(setup_env):
    """删除补税流水：盈亏回滚。"""
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
    assert r4['recalculated_position']['realized_pnl'] == pytest.approx(200.0)


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
