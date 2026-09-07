"""
应用层级联完整性测试（OPT-6，2026-09-07）

背景：SQLite `PRAGMA foreign_keys=OFF`，级联删除完全由应用层管理。
本文件系统化守护全部删除路径的级联链，防止"改库结构时忘记同步级联"。

设计：
- ``STOCK_ID_TABLES`` 是全部含 stock_id 列的子表清单（schema 扫描自动生成，
  2026-09-07 共 29 张）——新表上线时在此清单补一行即可；
- ``test_delete_stock_child_coverage`` 断言 `api_delete_stock` 的 child_tables
  与「清单 - 已登记缺口」双向一致：新增 stock_id 子表而未同步级联 → 本测试红；
- ``test_delete_stock_residue_<table>`` 参数化断言：删除后子表残留为 0；
- 已知缺口表（``KNOWN_UNCOVERED``）单独参数化登记现状：删除后残留 = 1。
  这些表已登记任务书"发现的问题"（OPT-6-G1），另行立项修复；若有人修复级联，
  对应残留断言会失败 → 提示把该表移出 KNOWN_UNCOVERED 并入正式清单。

只加测试不改实现（任务书约束）；红线：不触网、隔离临时库。
"""

import pytest

from app import app
from database import db_manager

# ============================================================
# 清单：全部含 stock_id 列的表（schema 扫描，2026-09-07，29 张）
# ============================================================

STOCK_ID_TABLES = [
    'alert_history', 'alert_rules', 'analysis_results', 'backtest_results',
    'change_logs', 'daily_reports', 'data_status', 'error_logs',
    'holder_structure', 'holdings', 'news_sentiment', 'position_cost_adjustments',
    'positions', 'price_backtest_results', 'price_cache', 'ratings_history',
    'raw_capital_flow', 'raw_express', 'raw_forecast', 'raw_fundamental',
    'raw_kline', 'raw_kline_monthly', 'raw_kline_weekly', 'raw_sentiment',
    'stock_orderbook', 'stock_restricted_release', 'stock_valuation',
    'stock_valuation_history', 'trade_records',
]

# 已登记级联缺口（任务书"发现的问题"OPT-6-G1）：删除自选股时这些表不清，
# 行为为"残留孤儿行"。修复立项前，coverage 断言豁免；residue 断言登记现状。
KNOWN_UNCOVERED = {
    'alert_history', 'alert_rules', 'daily_reports', 'error_logs',
    'holder_structure', 'holdings', 'news_sentiment', 'position_cost_adjustments',
    'price_backtest_results', 'price_cache', 'raw_express', 'raw_forecast',
    'raw_kline_monthly', 'raw_kline_weekly', 'stock_orderbook',
    'stock_restricted_release', 'stock_valuation', 'stock_valuation_history',
    'trade_records',
}

# 各表最小种子行（stock_id 之外的必要列）；new table → 补一行
SEEDS = {
    'alert_history': "INSERT INTO alert_history (rule_id, stock_id, alert_type, message, triggered_at, trigger_date) VALUES (1, {sid}, 'price_above', 't', '2026-09-07 10:00:00', '2026-09-07')",
    'alert_rules': "INSERT INTO alert_rules (stock_id, rule_type, threshold) VALUES ({sid}, 'price_above', 100)",
    'analysis_results': "INSERT INTO analysis_results (stock_id, analysis_date, total_score, rating) VALUES ({sid}, '2026-09-07', 60, '持有')",
    'backtest_results': "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, price_at_rating, backtest_date) VALUES ({sid}, 1, 'a_stock', '2026-09-07', '持有', 10.0, '2026-09-07')",
    'change_logs': "INSERT INTO change_logs (stock_id, log_date, log_type) VALUES ({sid}, '2026-09-07', 'rating')",
    'daily_reports': "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, status) VALUES ('2026-09-07', {sid}, '000333', '测试股', 'success')",
    'data_status': "INSERT INTO data_status (stock_id, dimension, status, message, fetched_at) VALUES ({sid}, 'kline', 'success', 'm', '2026-09-07 10:00:00')",
    'error_logs': "INSERT INTO error_logs (stock_id, module, error_type, error_message) VALUES ({sid}, 'test', 'E', 'm')",
    'holder_structure': "INSERT INTO holder_structure (stock_id, stat_date) VALUES ({sid}, '2026-06-30')",
    'holdings': "INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) VALUES (1, {sid}, 10.0, 0, 'cleared')",
    'news_sentiment': "INSERT INTO news_sentiment (stock_id, news_date, total_count) VALUES ({sid}, '2026-09-07', 0)",
    'position_cost_adjustments': "INSERT INTO position_cost_adjustments (holding_id, stock_id, old_cost, new_cost, reason) VALUES (1, {sid}, 10.0, 11.0, 'r')",
    'positions': "INSERT INTO positions (stock_id, cost_price, quantity) VALUES ({sid}, 10.0, 0)",
    'price_backtest_results': "INSERT INTO price_backtest_results (stock_id, backtest_date, rating, market) VALUES ({sid}, '2026-09-07', '持有', 'a_stock')",
    'price_cache': "INSERT INTO price_cache (stock_id, latest_price, pct_change) VALUES ({sid}, 10.0, 1.0)",
    'ratings_history': "INSERT INTO ratings_history (stock_id, rating_date, rating, total_score) VALUES ({sid}, '2026-09-07', '持有', 60)",
    'raw_capital_flow': "INSERT INTO raw_capital_flow (stock_id, trade_date) VALUES ({sid}, '2026-09-07')",
    'raw_express': "INSERT INTO raw_express (stock_id, symbol, report_period) VALUES ({sid}, '000333', '20260630')",
    'raw_forecast': "INSERT INTO raw_forecast (stock_id, symbol, report_period, indicator) VALUES ({sid}, '000333', '20260630', '净利润')",
    'raw_fundamental': "INSERT INTO raw_fundamental (stock_id, report_date) VALUES ({sid}, '2026-06-30')",
    'raw_kline': "INSERT INTO raw_kline (stock_id, trade_date, close) VALUES ({sid}, '2026-09-07', 10.0)",
    'raw_kline_monthly': "INSERT INTO raw_kline_monthly (stock_id, trade_date, close) VALUES ({sid}, '2026-09-01', 10.0)",
    'raw_kline_weekly': "INSERT INTO raw_kline_weekly (stock_id, trade_date, close) VALUES ({sid}, '2026-09-05', 10.0)",
    'raw_sentiment': "INSERT INTO raw_sentiment (stock_id, info_type, title) VALUES ({sid}, 'news', 't')",
    'stock_orderbook': "INSERT INTO stock_orderbook (stock_id, trade_date, quote_time) VALUES ({sid}, '2026-09-07', '15:00:00')",
    'stock_restricted_release': "INSERT INTO stock_restricted_release (stock_id, release_date) VALUES ({sid}, '2026-12-01')",
    'stock_valuation': "INSERT INTO stock_valuation (stock_id, trade_date) VALUES ({sid}, '2026-09-07')",
    'stock_valuation_history': "INSERT INTO stock_valuation_history (stock_id, trade_date, pe_ttm) VALUES ({sid}, '2026-09-07', 12.5)",
    'trade_records': "INSERT INTO trade_records (stock_id, trade_type, price, quantity, trade_date) VALUES ({sid}, 'buy', 10.0, 100, '2026-09-07')",
}


@pytest.fixture()
def cidb(tmp_path, monkeypatch):
    """级联测试专用隔离库（含 Flask test_client）。"""
    db_file = tmp_path / 'cascade.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('000333', 'a_stock', '测试股')")
    if conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0:
        conn.execute("INSERT INTO accounts (name, is_default) VALUES ('默认账户', 1)")
    conn.execute("INSERT INTO accounts (name, is_default) VALUES ('二级账户', 0)")
    conn.commit()
    conn.close()
    return app.test_client()


def _seed(cidb, table, stock_id=1):
    conn = db_manager.get_connection()
    conn.execute(SEEDS[table].format(sid=stock_id))
    conn.commit()
    conn.close()


def _count(table, stock_id=1):
    conn = db_manager.get_connection()
    n = conn.execute(f'SELECT COUNT(*) AS c FROM {table} WHERE stock_id=?', (stock_id,)).fetchone()['c']
    conn.close()
    return n


def _stock_exists(stock_id=1):
    conn = db_manager.get_connection()
    n = conn.execute('SELECT COUNT(*) AS c FROM stocks WHERE id=?', (stock_id,)).fetchone()['c']
    conn.close()
    return n > 0


def _delete_stock(cidb, stock_id=1):
    return cidb.delete(f'/api/stocks/{stock_id}')


# ============================================================
# 1. 删自选股：child_tables 与 schema 清单双向一致（漂移守卫）
# ============================================================


def test_delete_stock_child_coverage_matches_manifest(cidb):
    """api_delete_stock 的 child_tables 必须 = 清单 - 已登记缺口（双向）。
    新增 stock_id 子表而未同步级联 → 本测试红（先登记缺口再加白名单）。"""
    import re

    src = open('blueprints/watchlist.py', encoding='utf-8').read()
    m = re.search(r'child_tables = \[(.*?)\]', src, re.S)
    listed = set(re.findall(r"'(\w+)'", m.group(1)))
    expected = set(STOCK_ID_TABLES) - KNOWN_UNCOVERED
    assert listed == expected, (
        f'child_tables 与清单漂移：多出 {sorted(listed - expected)}，'
        f'缺少 {sorted(expected - listed)}（新表须同步级联或登记缺口）'
    )


@pytest.mark.parametrize('table', sorted(set(STOCK_ID_TABLES) - KNOWN_UNCOVERED))
def test_delete_stock_residue_zero(cidb, table):
    """删除自选股后，已覆盖子表残留必须为 0。"""
    _seed(cidb, table)
    assert _count(table) == 1
    r = _delete_stock(cidb)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert not _stock_exists()
    assert _count(table) == 0, f'{table} 残留孤儿行'


@pytest.mark.parametrize('table', sorted(KNOWN_UNCOVERED))
def test_delete_stock_known_gap_documented(cidb, table):
    """已登记缺口表：删除自选股后残留孤儿行（现状登记，见任务书 OPT-6-G1）。
    若本断言失败=有人已修复该表级联 → 请把它移出 KNOWN_UNCOVERED 并入正式清单。"""
    _seed(cidb, table)
    r = _delete_stock(cidb)
    assert r.status_code == 200
    assert not _stock_exists()
    assert _count(table) == 1, f'{table} 级联已被修复 → 更新 KNOWN_UNCOVERED'


# ============================================================
# 2. 删分组：组内记录迁移到 NULL（groups 统一表；stock_groups 为迁移前遗留）
# ============================================================


def test_delete_group_watchlist_migrates(cidb):
    conn = db_manager.get_connection()
    conn.execute("INSERT INTO groups (name, type) VALUES ('观察', 'watchlist')")
    gid = conn.execute("SELECT id FROM groups WHERE name='观察'").fetchone()['id']
    conn.execute(f'UPDATE stocks SET group_id={gid} WHERE id=1')
    conn.commit()
    conn.close()

    r = cidb.delete(f'/api/groups/{gid}')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] and data['migrated_count'] == 1

    conn = db_manager.get_connection()
    assert conn.execute('SELECT COUNT(*) AS c FROM groups WHERE id=?', (gid,)).fetchone()['c'] == 0
    assert conn.execute('SELECT group_id FROM stocks WHERE id=1').fetchone()['group_id'] is None
    conn.close()


def test_delete_group_portfolio_migrates(cidb):
    conn = db_manager.get_connection()
    conn.execute("INSERT INTO groups (name, type) VALUES ('长线', 'portfolio')")
    gid = conn.execute("SELECT id FROM groups WHERE name='长线'").fetchone()['id']
    conn.execute(
        f'INSERT INTO holdings (account_id, stock_id, group_id, cost_price, quantity, status) '
        f'VALUES (1, 1, {gid}, 10.0, 100, \'active\')'
    )
    conn.commit()
    conn.close()

    r = cidb.delete(f'/api/groups/{gid}')
    assert r.status_code == 200
    assert r.get_json()['migrated_count'] == 1

    conn = db_manager.get_connection()
    assert conn.execute('SELECT group_id FROM holdings WHERE stock_id=1').fetchone()['group_id'] is None
    assert conn.execute('SELECT quantity FROM holdings WHERE stock_id=1').fetchone()['quantity'] == 100
    conn.close()


# ============================================================
# 3. 删账户（021W）：默认/最后账户禁止删；force_confirm 后持仓移除、
#    流水归入默认账户；同股多仓（AGENTS §3）另一账户持仓不受影响
# ============================================================


def test_delete_account_default_forbidden(cidb):
    """默认账户不可删除（存量数据归属锚点）。"""
    conn = db_manager.get_connection()
    default_id = conn.execute('SELECT id FROM accounts WHERE is_default=1').fetchone()['id']
    conn.close()

    r = cidb.delete(f'/api/accounts/{default_id}')
    assert r.status_code == 403


def test_delete_account_force_confirm_reassigns(cidb):
    conn = db_manager.get_connection()
    default_id = conn.execute('SELECT id FROM accounts WHERE is_default=1').fetchone()['id']
    sub_id = conn.execute("SELECT id FROM accounts WHERE name='二级账户'").fetchone()['id']
    # 同股两账户各一条持仓（AGENTS §3 同股多仓）+ 二级账户两条流水
    for acc in (default_id, sub_id):
        conn.execute(
            'INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) VALUES (?, 1, 10.0, 100, \'active\')',
            (acc,),
        )
    conn.execute(
        "INSERT INTO trade_records (holding_id, account_id, stock_id, trade_type, price, quantity, amount, trade_date) "
        "VALUES ((SELECT id FROM holdings WHERE account_id=? AND stock_id=1), ?, 1, 'buy', 10.0, 100, 1000, '2026-09-01')",
        (sub_id, sub_id),
    )
    conn.commit()
    conn.close()

    # 未带 force_confirm → 409 需二次确认
    r = cidb.delete(f'/api/accounts/{sub_id}', json={})
    assert r.status_code == 409
    assert r.get_json()['need_force_confirm'] is True

    r = cidb.delete(f'/api/accounts/{sub_id}', json={'force_confirm': True})
    assert r.status_code == 200

    conn = db_manager.get_connection()
    # 账户与该账户持仓已移除
    assert conn.execute('SELECT COUNT(*) AS c FROM accounts WHERE id=?', (sub_id,)).fetchone()['c'] == 0
    assert conn.execute('SELECT COUNT(*) AS c FROM holdings WHERE account_id=?', (sub_id,)).fetchone()['c'] == 0
    # 流水保留并归入默认账户，holding_id 断链
    row = conn.execute('SELECT account_id, holding_id FROM trade_records WHERE stock_id=1').fetchone()
    assert row['account_id'] == default_id
    assert row['holding_id'] is None
    # 默认账户同股持仓不受影响（AGENTS §3）
    keep = conn.execute(
        'SELECT quantity FROM holdings WHERE account_id=? AND stock_id=1', (default_id,)
    ).fetchone()
    assert keep['quantity'] == 100
    conn.close()


# ============================================================
# 4. 删持仓：流水保留、holding_id 置 NULL；多账户 409 要求指定账户
# ============================================================


def test_delete_holding_keeps_trades_and_multi_account_guard(cidb):
    conn = db_manager.get_connection()
    a1 = conn.execute('SELECT id FROM accounts WHERE is_default=1').fetchone()['id']
    a2 = conn.execute("SELECT id FROM accounts WHERE name='二级账户'").fetchone()['id']
    for acc in (a1, a2):
        conn.execute(
            'INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) VALUES (?, 1, 10.0, 50, \'active\')',
            (acc,),
        )
        conn.execute(
            'INSERT INTO trade_records (holding_id, account_id, stock_id, trade_type, price, quantity, amount, trade_date) '
            "VALUES ((SELECT id FROM holdings WHERE account_id=? AND stock_id=1), ?, 1, 'buy', 10.0, 50, 500, '2026-09-01')",
            (acc, acc),
        )
    conn.commit()
    conn.close()

    # 同股多账户缺省删除 → 409 要求指定 account_id
    r = cidb.delete('/api/portfolio/holdings/1')
    assert r.status_code == 409

    # 指定账户删除：该账户流水保留且断链；另一账户持仓/流水原样（AGENTS §3）
    r = cidb.delete(f'/api/portfolio/holdings/1?account_id={a2}')
    assert r.status_code == 200

    conn = db_manager.get_connection()
    assert conn.execute('SELECT COUNT(*) AS c FROM holdings WHERE account_id=?', (a2,)).fetchone()['c'] == 0
    t2 = conn.execute('SELECT holding_id FROM trade_records WHERE account_id=?', (a2,)).fetchone()
    assert t2['holding_id'] is None
    keep = conn.execute(
        'SELECT h.quantity AS q, t.holding_id AS hid FROM holdings h '
        'JOIN trade_records t ON t.holding_id = h.id WHERE h.account_id=?',
        (a1,),
    ).fetchone()
    assert keep['q'] == 50 and keep['hid'] is not None
    conn.close()


# ============================================================
# 5. 删流水：安全锁（T+1/大额/已清算）+ 删除后持仓重算一致性
# ============================================================


def test_delete_trade_locks_and_recalculate(cidb):
    from datetime import datetime, timedelta

    conn = db_manager.get_connection()
    a1 = conn.execute('SELECT id FROM accounts WHERE is_default=1').fetchone()['id']
    # 历史流水两条（T+1 已过）：买 100@10、买 100@30（UNIQUE(account_id, stock_id) → 持仓只建一条）
    old = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) VALUES (?, 1, 20.0, 200, \'active\')',
        (a1,),
    )
    for price in (10.0, 30.0):
        conn.execute(
            'INSERT INTO trade_records (account_id, stock_id, trade_type, price, quantity, amount, trade_date, created_at) '
            "VALUES (?, 1, 'buy', ?, 100, ?, '2026-09-01', ?)",
            (a1, price, price * 100, old),
        )
    # 当日流水（T+1 锁定命中）
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO trade_records (account_id, stock_id, trade_type, price, quantity, amount, trade_date, created_at) "
        "VALUES (?, 1, 'buy', 40.0, 100, 4000, '2026-09-07', ?)",
        (a1, now),
    )
    today_id = conn.execute("SELECT id FROM trade_records WHERE price=40.0").fetchone()['id']
    conn.commit()
    conn.close()

    # T+1 锁：当日流水删除被拒
    r = cidb.delete(f'/api/portfolio/trades/{today_id}', json={})
    assert r.status_code == 403, f'T+1 锁未生效: {r.status_code} {r.get_data(as_text=True)[:80]}'
    assert 'T+1' in r.get_json()['message']

    # 删除历史流水之一（买 100@10）→ 持仓重算：
    # 余下 = 历史 买 100@30 + 当日 买 100@40（T+1 锁定仍在库）→ 200 股 @ 均价 35
    conn = db_manager.get_connection()
    old_id = conn.execute("SELECT id FROM trade_records WHERE price=10.0").fetchone()['id']
    conn.close()
    r = cidb.delete(f'/api/portfolio/trades/{old_id}', json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    rec = r.get_json()['recalculated_position']
    assert rec['quantity'] == 200
    assert rec['avg_cost'] == 35.0
    assert rec['status'] == 'active'

    conn = db_manager.get_connection()
    assert conn.execute('SELECT COUNT(*) AS c FROM trade_records WHERE id=?', (old_id,)).fetchone()['c'] == 0
    conn.close()


def test_delete_trade_cleared_stock_forbidden(cidb):
    """已清算（持仓数量=0）股票的历史流水禁止删除（安全锁 1）。"""
    conn = db_manager.get_connection()
    a1 = conn.execute('SELECT id FROM accounts WHERE is_default=1').fetchone()['id']
    old = (datetime_now_minus_days(3))
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity, status) VALUES (?, 1, 0, 0, \'cleared\')',
        (a1,),
    )
    conn.execute(
        'INSERT INTO trade_records (account_id, stock_id, trade_type, price, quantity, amount, trade_date, created_at) '
        "VALUES (?, 1, 'buy', 10.0, 100, 1000, '2026-09-01', ?)",
        (a1, old),
    )
    tid = conn.execute('SELECT id FROM trade_records').fetchone()['id']
    conn.commit()
    conn.close()

    r = cidb.delete(f'/api/portfolio/trades/{tid}', json={})
    assert r.status_code == 403
    assert '清算' in r.get_json()['message']


def datetime_now_minus_days(n):
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=n)).strftime('%Y-%m-%d %H:%M:%S')
