"""
021S 多交易账户单元测试

覆盖目标：
- 迁移/初始化：默认账户自动创建（幂等）
- 账户 CRUD：创建/改名/删除守卫（默认账户不可删、最后账户不可删、非空需二次确认）
- 持仓隔离：同一股票在不同账户各有一条持仓，成本独立
- 流水重算隔离：_recalculate_holding 按（股票, 账户）维度互不影响
- 汇总聚合：summary 全账户视图输出 accounts_breakdown，分账户口径正确
- 删除歧义：多账户同股删除未指定账户时返回 409

隔离原则：
- pytest tmp_path 临时 SQLite，monkeypatch database.db_manager.DB_PATH
- 不触碰真实 stock_analyst.db，不发起任何网络请求
"""

import json

import pytest

import app as app_module
from database import db_manager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离数据库 + Flask test client"""
    db_path = str(tmp_path / 'test_accounts.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


@pytest.fixture()
def client_with_stocks(client):
    """隔离库 + 插入 2 只自选股，返回 (client, stock_id_a, stock_id_b)"""
    conn = db_manager.get_connection()
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')")
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('000001', 'a_stock', '平安银行')")
    conn.commit()
    ids = [r['id'] for r in conn.execute('SELECT id FROM stocks ORDER BY id').fetchall()]
    conn.close()
    return client, ids[0], ids[1]


def _get_default_account_id():
    conn = db_manager.get_connection()
    row = conn.execute(
        'SELECT id FROM accounts WHERE is_default = 1 ORDER BY id LIMIT 1'
    ).fetchone()
    conn.close()
    return row['id'] if row else None


# ---- 默认账户 ----

def test_default_account_auto_created(client):
    """init_database 后应自动存在且仅存在一个默认账户"""
    conn = db_manager.get_connection()
    rows = conn.execute('SELECT * FROM accounts').fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]['name'] == '默认账户'
    assert rows[0]['is_default'] == 1


def test_init_idempotent_no_duplicate_accounts(client):
    """重复 init 不产生重复默认账户"""
    db_manager.init_database()
    db_manager.init_database()
    conn = db_manager.get_connection()
    count = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    conn.close()
    assert count == 1


# ---- 账户 CRUD ----

def test_account_create_and_list(client):
    resp = client.post('/api/accounts', json={'name': '华泰主账户', 'broker': '华泰证券'})
    assert resp.status_code == 200
    account_id = resp.get_json()['account_id']

    resp = client.get('/api/accounts')
    data = resp.get_json()
    assert data['success']
    names = [a['name'] for a in data['accounts']]
    assert '华泰主账户' in names
    acc = next(a for a in data['accounts'] if a['id'] == account_id)
    assert acc['broker'] == '华泰证券'
    assert acc['holding_count'] == 0


def test_account_create_duplicate_name_409(client):
    client.post('/api/accounts', json={'name': '测试账户'})
    resp = client.post('/api/accounts', json={'name': '测试账户'})
    assert resp.status_code == 409


def test_account_create_empty_name_400(client):
    assert client.post('/api/accounts', json={'name': '  '}).status_code == 400


def test_account_rename(client):
    account_id = client.post('/api/accounts', json={'name': '旧名'}).get_json()['account_id']
    resp = client.put(f'/api/accounts/{account_id}', json={'name': '新名'})
    assert resp.status_code == 200
    conn = db_manager.get_connection()
    name = conn.execute('SELECT name FROM accounts WHERE id=?', (account_id,)).fetchone()[0]
    conn.close()
    assert name == '新名'


def test_delete_default_account_forbidden(client):
    default_id = _get_default_account_id()
    resp = client.delete(f'/api/accounts/{default_id}', json={'force_confirm': True})
    assert resp.status_code == 403


def test_delete_last_account_forbidden(client):
    """仅剩默认账户时不可删除（先建后删的防御路径）"""
    account_id = client.post('/api/accounts', json={'name': '临时'}).get_json()['account_id']
    # 删除新建账户成功（此时剩默认账户）
    assert client.delete(f'/api/accounts/{account_id}', json={'force_confirm': True}).status_code == 200
    # 默认账户被禁删 → 最后一个账户实际不可能被删除
    default_id = _get_default_account_id()
    assert client.delete(f'/api/accounts/{default_id}').status_code == 403


def test_delete_nonempty_account_requires_force(client_with_stocks):
    """账户下有持仓时，不带 force_confirm 应返回 409"""
    client, stock_a, _ = client_with_stocks
    account_id = client.post('/api/accounts', json={'name': '有仓账户'}).get_json()['account_id']
    client.post(
        f'/api/portfolio/holdings/{stock_a}',
        json={'cost_price': 10.0, 'quantity': 100, 'account_id': account_id},
    )
    resp = client.delete(f'/api/accounts/{account_id}')
    assert resp.status_code == 409
    assert resp.get_json().get('need_force_confirm') is True
    # 带 force_confirm 后成功，流水归入默认账户
    resp2 = client.delete(f'/api/accounts/{account_id}', json={'force_confirm': True})
    assert resp2.status_code == 200


# ---- 持仓隔离 ----

def test_same_stock_two_accounts_two_holdings(client_with_stocks):
    """同一股票可在两个账户各有一条持仓，成本独立"""
    client, stock_a, _ = client_with_stocks
    acc1 = _get_default_account_id()
    acc2 = client.post('/api/accounts', json={'name': '第二账户'}).get_json()['account_id']

    r1 = client.post(
        f'/api/portfolio/holdings/{stock_a}',
        json={'cost_price': 1500.0, 'quantity': 100, 'account_id': acc1},
    )
    r2 = client.post(
        f'/api/portfolio/holdings/{stock_a}',
        json={'cost_price': 1600.0, 'quantity': 200, 'account_id': acc2},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['holding_id'] != r2.get_json()['holding_id']

    holdings = client.get('/api/portfolio/holdings').get_json()['holdings']
    rows = [h for h in holdings if h['stock_id'] == stock_a]
    assert len(rows) == 2
    costs = {h['cost_price'] for h in rows}
    assert costs == {1500.0, 1600.0}


def test_holdings_filter_by_account(client_with_stocks):
    client, stock_a, stock_b = client_with_stocks
    acc2 = client.post('/api/accounts', json={'name': '过滤账户'}).get_json()['account_id']
    client.post(f'/api/portfolio/holdings/{stock_a}', json={'cost_price': 10, 'quantity': 100})
    client.post(
        f'/api/portfolio/holdings/{stock_b}',
        json={'cost_price': 20, 'quantity': 200, 'account_id': acc2},
    )

    scoped = client.get(f'/api/portfolio/holdings?account_id={acc2}').get_json()['holdings']
    assert [h['stock_id'] for h in scoped] == [stock_b]
    assert scoped[0]['account_name'] == '过滤账户'

    all_rows = client.get('/api/portfolio/holdings').get_json()['holdings']
    assert len(all_rows) == 2


def test_upsert_conflict_scoped_by_account(client_with_stocks):
    """同账户重复 POST 是更新而非新增；不同账户互不冲突"""
    client, stock_a, _ = client_with_stocks
    acc1 = _get_default_account_id()
    client.post(f'/api/portfolio/holdings/{stock_a}', json={'cost_price': 10, 'quantity': 100, 'account_id': acc1})
    client.post(f'/api/portfolio/holdings/{stock_a}', json={'cost_price': 11, 'quantity': 200, 'account_id': acc1})

    conn = db_manager.get_connection()
    cnt = conn.execute('SELECT COUNT(*) FROM holdings WHERE stock_id=?', (stock_a,)).fetchone()[0]
    cost = conn.execute(
        'SELECT cost_price FROM holdings WHERE stock_id=? AND account_id=?', (stock_a, acc1)
    ).fetchone()[0]
    conn.close()
    assert cnt == 1
    assert cost == 11


def test_delete_holding_multi_account_409_without_account_id(client_with_stocks):
    """多账户同股删除未指定 account_id 时返回 409 并列出持仓"""
    client, stock_a, _ = client_with_stocks
    acc2 = client.post('/api/accounts', json={'name': '歧义账户'}).get_json()['account_id']
    client.post(f'/api/portfolio/holdings/{stock_a}', json={'cost_price': 10, 'quantity': 100})
    client.post(
        f'/api/portfolio/holdings/{stock_a}',
        json={'cost_price': 20, 'quantity': 200, 'account_id': acc2},
    )
    resp = client.delete(f'/api/portfolio/holdings/{stock_a}')
    assert resp.status_code == 409
    assert len(resp.get_json()['holdings']) == 2
    # 指定账户后删除成功
    ok = client.delete(f'/api/portfolio/holdings/{stock_a}?account_id={acc2}')
    assert ok.status_code == 200
    remaining = [
        h for h in client.get('/api/portfolio/holdings').get_json()['holdings']
        if h['stock_id'] == stock_a
    ]
    assert len(remaining) == 1


# ---- 流水重算隔离 ----

def test_trade_recalc_isolated_by_account(client_with_stocks):
    """两账户各自买入同一股票，重算互不影响"""
    # commission=0 为显式填写（021BK"填写即尊重"），屏蔽佣金自动估算——
    # 本测试验证账户隔离，不验证费用模型（后者由 test_trade_fees_021bk.py 覆盖）
    client, stock_a, _ = client_with_stocks
    acc1 = _get_default_account_id()
    acc2 = client.post('/api/accounts', json={'name': '重算账户'}).get_json()['account_id']

    r1 = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01', 'account_id': acc1, 'commission': 0},
    )
    r2 = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 200.0, 'quantity': 50,
              'trade_date': '2026-08-02', 'account_id': acc2, 'commission': 0},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    p1 = r1.get_json()['recalculated_position']
    p2 = r2.get_json()['recalculated_position']
    assert p1['quantity'] == 100 and p1['avg_cost'] == 100.0
    assert p2['quantity'] == 50 and p2['avg_cost'] == 200.0

    # 卖出 acc1 的部分持仓不应影响 acc2
    r3 = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'sell', 'price': 110.0, 'quantity': 40,
              'trade_date': '2026-08-03', 'account_id': acc1, 'commission': 0},
    )
    p3 = r3.get_json()['recalculated_position']
    assert p3['quantity'] == 60
    assert abs(p3['realized_pnl'] - 400.0) < 0.01  # (110-100)*40

    conn = db_manager.get_connection()
    qty2 = conn.execute(
        'SELECT quantity FROM holdings WHERE stock_id=? AND account_id=?',
        (stock_a, acc2),
    ).fetchone()[0]
    conn.close()
    assert qty2 == 50


def test_trades_list_contains_account_name(client_with_stocks):
    client, stock_a, _ = client_with_stocks
    acc2 = client.post('/api/accounts', json={'name': '流水账户'}).get_json()['account_id']
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100, 'quantity': 100,
              'trade_date': '2026-08-01', 'account_id': acc2},
    )
    trades = client.get(f'/api/portfolio/holdings/{stock_a}/trades').get_json()['trades']
    assert trades[0]['account_name'] == '流水账户'

    scoped = client.get(
        f'/api/portfolio/holdings/{stock_a}/trades?account_id={acc2}'
    ).get_json()['trades']
    assert len(scoped) == 1


def test_all_trades_filter_by_account(client_with_stocks):
    client, stock_a, _ = client_with_stocks
    acc2 = client.post('/api/accounts', json={'name': '全局过滤'}).get_json()['account_id']
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100, 'quantity': 100, 'trade_date': '2026-08-01'},
    )
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 200, 'quantity': 50,
              'trade_date': '2026-08-02', 'account_id': acc2},
    )
    scoped = client.get(f'/api/portfolio/trades?account_id={acc2}').get_json()
    assert scoped['count'] == 1
    assert scoped['trades'][0]['account_name'] == '全局过滤'


# ---- 汇总聚合 ----

def test_summary_breakdown_across_accounts(client_with_stocks):
    """全账户视图输出分账户汇总，单账户视图不输出分解"""
    client, stock_a, _ = client_with_stocks
    acc1 = _get_default_account_id()
    acc2 = client.post('/api/accounts', json={'name': '汇总账户'}).get_json()['account_id']

    # 写入 price_cache 使市值可计算
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
        "VALUES (?, 120.0, 2.0, datetime('now', 'localtime'))",
        (stock_a,),
    )
    conn.commit()
    conn.close()

    # commission=0 显式填写，屏蔽 021BK 佣金估算——本测试验证分账户汇总口径
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01', 'account_id': acc1, 'commission': 0},
    )
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 110.0, 'quantity': 50,
              'trade_date': '2026-08-02', 'account_id': acc2, 'commission': 0},
    )

    data = client.get('/api/portfolio/summary').get_json()
    assert data['account_scope'] == 'all'
    bd = {b['account_id']: b for b in data['accounts_breakdown']}
    assert set(bd.keys()) == {acc1, acc2}
    # 市值 = 数量 × 最新价(120)
    assert bd[acc1]['total_market_value'] == 12000.0   # 100 × 120
    assert bd[acc2]['total_market_value'] == 6000.0    # 50 × 120
    # 浮动盈亏 = (市价 - 成本) × 数量
    assert bd[acc1]['total_unrealized_pnl'] == 2000.0  # (120-100)*100
    assert bd[acc2]['total_unrealized_pnl'] == 500.0   # (120-110)*50
    # 总市值 = 两账户之和
    assert data['total_market_value'] == 18000.0

    scoped = client.get(f'/api/portfolio/summary?account_id={acc2}').get_json()
    assert scoped['account_scope'] == acc2
    assert scoped['accounts_breakdown'] is None
    assert scoped['total_market_value'] == 6000.0


# ---- 手续费（021X） ----

def test_buy_commission_folded_into_cost(client_with_stocks):
    """买入手续费计入持仓成本：avg_cost = (金额+佣金)/数量"""
    client, stock_a, _ = client_with_stocks
    resp = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01', 'commission': 5.0},
    )
    assert resp.status_code == 200
    p = resp.get_json()['recalculated_position']
    assert p['quantity'] == 100
    assert abs(p['avg_cost'] - 100.05) < 0.0001  # (100*100+5)/100


def test_sell_commission_reduces_realized_pnl(client_with_stocks):
    """卖出手续费扣减已实现盈亏"""
    client, stock_a, _ = client_with_stocks
    # 买腿显式 commission=0（021BK 填写即尊重），保证成本恰为 100.0；
    # 卖腿显式 7.0 即为本测试的验证对象
    client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01', 'commission': 0},
    )
    resp = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'sell', 'price': 110.0, 'quantity': 50,
              'trade_date': '2026-08-02', 'commission': 7.0},
    )
    p = resp.get_json()['recalculated_position']
    # (110-100)*50 - 7 = 493
    assert abs(p['realized_pnl'] - 493.0) < 0.01


def test_negative_commission_rejected(client_with_stocks):
    client, stock_a, _ = client_with_stocks
    resp = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01', 'commission': -1},
    )
    assert resp.status_code == 400


def test_commission_edit_and_list_roundtrip(client_with_stocks):
    """流水列表返回 commission，编辑可修改并触发重算（T+1 锁定需回填创建时间）"""
    client, stock_a, _ = client_with_stocks
    r = client.post(
        f'/api/portfolio/holdings/{stock_a}/trades',
        json={'trade_type': 'buy', 'price': 100.0, 'quantity': 100,
              'trade_date': '2026-08-01'},
    )
    trade_id = r.get_json()['trade_id']
    # 模拟 T+1：回填 created_at 为两天前，绕过当日锁定（仅测试库操作）
    conn = db_manager.get_connection()
    conn.execute(
        "UPDATE trade_records SET created_at = datetime('now', 'localtime', '-2 days') WHERE id = ?",
        (trade_id,),
    )
    conn.commit()
    conn.close()
    # 编辑补录手续费
    resp = client.put(
        f'/api/portfolio/trades/{trade_id}',
        json={'commission': 10.0},
    )
    assert resp.status_code == 200
    p = resp.get_json()['recalculated_position']
    assert abs(p['avg_cost'] - 100.1) < 0.0001  # (10000+10)/100
    trades = client.get(f'/api/portfolio/holdings/{stock_a}/trades').get_json()['trades']
    assert trades[0]['commission'] == 10.0


# ---- 兼容性 ----

def test_legacy_endpoints_backward_compatible(client_with_stocks):
    """不带 account_id 的旧调用方式保持可用（缺省=全部账户）"""
    client, stock_a, _ = client_with_stocks
    # 不带 account_id 创建持仓 → 归入默认账户
    resp = client.post(f'/api/portfolio/holdings/{stock_a}', json={'cost_price': 10, 'quantity': 100})
    assert resp.status_code == 200
    default_id = _get_default_account_id()
    assert resp.get_json()['account_id'] == default_id
    # 单持仓时不带 account_id 删除 → 直接成功
    resp = client.delete(f'/api/portfolio/holdings/{stock_a}')
    assert resp.status_code == 200
