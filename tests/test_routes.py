"""
路由层冒烟测试（app.py 拆分 blueprints 后的回归防线）

覆盖目标：
- 全部 9 个业务蓝图的路由均已注册（app 可导入、路由可达）
- 核心只读 GET 端点返回 200 且 JSON 可解析（空库 + 1 条自选股两种状态）
- 轻量写端点（init-db / 分组 CRUD）在隔离库上正常工作

隔离原则：
- 使用 pytest tmp_path 临时 SQLite，monkeypatch database.db_manager.DB_PATH
- 不触碰真实 stock_analyst.db，不发起任何网络请求
- 采集/分析/建议等重端点（依赖 akshare 网络）不在本测试范围
"""

import json

import pytest

import app as app_module
from database import db_manager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离数据库 + Flask test client"""
    db_path = str(tmp_path / 'test_routes.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


@pytest.fixture()
def client_with_stock(client):
    """隔离库 + 插入 1 条自选股（600519 贵州茅台）"""
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks LIMIT 1').fetchone()['id']
    conn.close()
    return client, stock_id


def _assert_ok(resp):
    """断言 200 且 JSON 可解析"""
    assert resp.status_code == 200, f'status={resp.status_code} body={resp.data[:300]}'
    json.loads(resp.data)  # 必须可解析为 JSON


# ---- 页面与系统 ----

def test_index_page(client):
    resp = client.get('/')
    assert resp.status_code == 200


def test_health(client):
    _assert_ok(client.get('/api/health'))


def test_db_stats(client):
    _assert_ok(client.get('/api/db-stats'))


# ---- watchlist 蓝图 ----

def test_groups_list(client):
    _assert_ok(client.get('/api/groups'))


def test_groups_crud(client):
    resp = client.post('/api/groups', json={'name': '测试组', 'type': 'watchlist'})
    assert resp.status_code == 200
    group_id = resp.get_json()['group_id']
    assert client.put(f'/api/groups/{group_id}', json={'name': '测试组2'}).status_code == 200
    assert client.delete(f'/api/groups/{group_id}').status_code == 200


def test_stocks_list(client):
    _assert_ok(client.get('/api/stocks'))


def test_stock_detail_endpoints(client_with_stock):
    """个股只读数据端点(空数据表时应 200 空结果)"""
    client, stock_id = client_with_stock
    paths = [
        '/api/stocks',
        f'/api/stocks/{stock_id}/kline',
        f'/api/stocks/{stock_id}/fundamental',
        f'/api/stocks/{stock_id}/capital',
        f'/api/stocks/{stock_id}/orderbook',
        f'/api/stocks/{stock_id}/valuation',
        f'/api/stocks/{stock_id}/restricted-release',
        f'/api/stocks/{stock_id}/status',
        f'/api/stocks/{stock_id}/news',
    ]
    for path in paths:
        _assert_ok(client.get(path))


def test_fundamental_history_valuation_lookup(client_with_stock):
    """021W-2：基本面历史行 PE/PB 关联"当时真实估值"（百度历史估值表）。

    背景：PE/PB 为实时估值，raw_fundamental 历史期行为 NULL。修复后按财报期
    report_date 关联 stock_valuation_history 中 trade_date <= report_date 的
    最近一个快照点，输出 hist_pe_ttm/hist_pb/hist_valuation_date；未采集历史
    估值时 valuation_history_ready=false。
    """
    client, stock_id = client_with_stock
    conn = db_manager.get_connection()
    # 基本面多期财报（PE/PB 列本身为空，历史期估值来自独立历史估值表）
    for i, report_date in enumerate(['2026-06-30', '2026-03-31', '2025-12-31']):
        conn.execute(
            'INSERT INTO raw_fundamental (stock_id, report_date, roe, data_source) VALUES (?, ?, ?, ?)',
            (stock_id, report_date, 10.0 + i, 'sina_abstract'),
        )
    # 历史估值快照（百度，约每两周一点）
    for trade_date, pe, pb in [
        ('2025-12-20', 48.5, 6.2),
        ('2026-01-06', 56.04, 7.0),
        ('2026-03-25', 52.0, 6.5),
        ('2026-06-23', 50.2, 6.0),
    ]:
        conn.execute(
            'INSERT INTO stock_valuation_history (stock_id, trade_date, pe_ttm, pb, source) VALUES (?, ?, ?, ?, ?)',
            (stock_id, trade_date, pe, pb, 'baidu'),
        )
    conn.commit()
    conn.close()

    resp = client.get(f'/api/stocks/{stock_id}/fundamental')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['valuation_history_ready'] is True
    data = body['data']
    assert len(data) == 3
    # 每行取 ≤ 财报期末的最近快照点作为"当时估值"
    expected = {
        '2026-06-30': (50.2, 6.0, '2026-06-23'),
        '2026-03-31': (52.0, 6.5, '2026-03-25'),
        '2025-12-31': (48.5, 6.2, '2025-12-20'),
    }
    for row in data:
        rp = row['report_date']
        exp_pe, exp_pb, exp_date = expected[rp]
        assert row['hist_pe_ttm'] == exp_pe, rp
        assert row['hist_pb'] == exp_pb, rp
        assert row['hist_valuation_date'] == exp_date, rp


def test_fundamental_no_valuation_history_flag(client_with_stock):
    """021W-2：未采集历史估值时 valuation_history_ready=false 且行内无 hist 字段"""
    client, stock_id = client_with_stock
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO raw_fundamental (stock_id, report_date, roe, data_source) VALUES (?, ?, ?, ?)',
        (stock_id, '2026-06-30', 10.0, 'sina_abstract'),
    )
    conn.commit()
    conn.close()

    resp = client.get(f'/api/stocks/{stock_id}/fundamental')
    body = resp.get_json()
    assert body['valuation_history_ready'] is False
    assert 'hist_valuation_date' not in body['data'][0]
    assert 'hist_pe_ttm' not in body['data'][0]
    assert 'hist_pb' not in body['data'][0]


# ---- portfolio 蓝图 ----

def test_portfolio_endpoints(client):
    paths = [
        '/api/portfolio/groups',
        '/api/portfolio/holdings',
        '/api/portfolio/summary',
        '/api/portfolio/watchlist-scores',
        '/api/portfolio/trades',
        '/api/portfolio/cost-adjustments',
    ]
    for path in paths:
        _assert_ok(client.get(path))


class _TencentQuoteResp:
    """腾讯行情接口 mock 响应（只需 .text）"""

    def __init__(self, text):
        self.text = text


def _tencent_line(code, name, price, prev, pct):
    """构造一行 v_hk03690="100~名称~03690~价格~昨收~…~涨跌幅[32]~…" 响应"""
    fields = ['100', name, code[-5:], str(price), str(prev), '0.00'] + ['0'] * 26
    fields.append(str(pct))  # [32] 涨跌幅
    while len(fields) < 40:
        fields.append('0')
    return f'v_{code}="' + '~'.join(fields) + '";'


def test_realtime_price_batch_hk_prefix_resolved(monkeypatch):
    """021M：港股库内 HK3690 形态必须解析为 hk03600 命中实时价。

    回归背景：原 symbol.zfill(5) 对 'HK3690'（已6字符）无效 → 请求 hkHK3690
    错误代码 → 腾讯返回空 → 港股全部降级写入昨收（盘中显示旧价+旧涨跌幅）。
    修复后经 _normalize_hk_symbol 剥离前缀+左补零。
    """
    import requests

    from blueprints.portfolio import _fetch_realtime_price_batch

    text = (
        _tencent_line('sh600276', '恒瑞医药', 46.88, 46.50, 0.82)
        + _tencent_line('hk03690', '美团-W', 84.95, 87.65, -3.08)
    )

    def _fake_get(url, timeout=8, **kw):
        assert 'hk03690' in url, f'港股代码应归一化为 hk03690，实际请求: {url}'
        assert 'hkHK3690' not in url, 'hkHK3690 是错误代码（021M bug），不应出现'
        return _TencentQuoteResp(text)

    monkeypatch.setattr(requests, 'get', _fake_get)

    result = _fetch_realtime_price_batch(
        [(1, '600276', 'a_stock'), (2, 'HK3690', 'hk_stock')]
    )
    assert result[1] == {'price': 46.88, 'pct_change': 0.82}
    # 港股命中实时价（修复前此 key 缺失 → 调用方降级写入昨收）
    assert result[2] == {'price': 84.95, 'pct_change': -3.08}


def test_watchlist_scores_etag_semantics(client):
    """021E：watchlist-scores 的 ETag 语义锁定。

    匹配 If-None-Match → 304 空体；无匹配 → 200 + ETag。
    前端修复依赖该语义：no-store 请求恒 200；默认条件请求命中 304 空体时
    safeJson 判定非 JSON → 静默放弃刷新（021E 已在前端加 no-store）。
    """
    r1 = client.get('/api/portfolio/watchlist-scores')
    assert r1.status_code == 200
    etag = r1.headers.get('ETag')
    assert etag, '首次响应应携带 ETag'
    r2 = client.get('/api/portfolio/watchlist-scores', headers={'If-None-Match': etag})
    assert r2.status_code == 304
    assert r2.data == b'', '304 响应体应为空（前端安全解析会静默失败的原因）'


# ---- report 蓝图 ----

def test_report_endpoints(client):
    paths = [
        '/api/daily-report/latest',
    ]
    for path in paths:
        _assert_ok(client.get(path))


class _Locked:
    """模拟被占用的生成锁（acquire 返回 False 触发防抖拒绝）"""

    def acquire(self, timeout=0):
        return False

    def release(self):
        pass


def test_report_generate_debounce_returns_message(client, monkeypatch):
    """防抖拒绝（任务进行中）应返回 200+message，而非 500 KeyError。

    回归：盘中快报/每日报告在任务进行中触发时，路由曾直接索引
    result['report_date'] 导致 KeyError，掩盖真实原因（'report_date'）。
    """
    # t6 拆包迁移：_generate_lock 单宿实现子模块 _generator（补丁打在 facade 对包内调用不可见）
    from modules.daily_report import _generator

    monkeypatch.setattr(_generator, '_generate_lock', _Locked())

    for path in ['/api/daily-report/generate', '/api/daily-report/generate-intraday']:
        resp = client.post(path, json={})
        assert resp.status_code == 200, f'{path}: status={resp.status_code} body={resp.data[:300]}'
        data = resp.get_json()
        assert data['success'] is False
        assert '进行中' in data['message'], f'{path}: message={data.get("message")}'


# ---- backtest / export / index / alerts 蓝图 ----

def test_backtest_endpoints(client):
    _assert_ok(client.get('/api/backtest/market-report'))


def test_index_ratings(client):
    _assert_ok(client.get('/api/index-ratings'))


def test_alerts_endpoints(client):
    paths = [
        '/api/alerts/rules',
        '/api/alerts/unread',
    ]
    for path in paths:
        _assert_ok(client.get(path))


def test_alert_rule_create_tech_signal(client):
    """021BP 项2：新预警类型 tech_signal 可创建（blueprint 白名单已同步）"""
    resp = client.post('/api/alerts/rules', json={'rule_type': 'tech_signal'})
    assert resp.status_code == 200, resp.data[:300]
    assert resp.get_json()['success'] is True
    # 白名单外类型仍拒绝
    resp2 = client.post('/api/alerts/rules', json={'rule_type': 'nope'})
    assert resp2.status_code == 400


def test_watchlist_signals_endpoint(client):
    """021BP 项1：自选股信号巡检端点（空库 200 + 快照参考口径标注）"""
    resp = client.get('/api/market/scan/watchlist-signals')
    _assert_ok(resp)
    body = resp.get_json()
    assert body['success'] is True
    assert body['scope'] == 'watchlist_offline'
    assert body['results'] == []
    assert body['errors'] == []


def test_watchlist_sell_signals_endpoint(client):
    """021BQ 项B：自选股卖点信号巡检端点（空库 200 + 卖侧口径标注）"""
    resp = client.get('/api/market/scan/watchlist-sell-signals')
    _assert_ok(resp)
    body = resp.get_json()
    assert body['success'] is True
    assert body['scope'] == 'watchlist_offline'
    assert body['side'] == 'sell'
    assert body['results'] == []
    assert body['errors'] == []


def test_alert_rule_create_sell_signal(client):
    """021BQ 项C：新预警类型 sell_signal 可创建（blueprint 白名单已同步）"""
    resp = client.post('/api/alerts/rules', json={'rule_type': 'sell_signal'})
    assert resp.status_code == 200, resp.data[:300]
    assert resp.get_json()['success'] is True


def test_dashboard_action_list(client):
    """021BP 项3：今日行动清单聚合端点（空库 200 + 结构完整）；021BQ 卖侧统计键增量"""
    resp = client.get('/api/dashboard/action-list')
    _assert_ok(resp)
    body = resp.get_json()
    assert body['success'] is True
    assert body['items'] == []
    assert body['overview'] == []
    assert body['failed_stocks'] == []
    assert body['stats']['active_count'] == 0
    assert 'sell_hits' in body['stats']        # 021BQ 增量键（旧键零改动）
    assert 'sell_resonance_hits' in body['stats']
    assert body['date']


def test_dashboard_intraday_endpoint(client, monkeypatch):
    """021BT：盘中速览端点（空库 200 + 契约形态：disclaimer/session/patrol 齐备）"""
    import modules.intraday_patrol as patrol

    monkeypatch.setattr(patrol, '_LAST_SNAPSHOT', None)
    monkeypatch.setattr(patrol, '_paused', False)
    monkeypatch.setattr(patrol, '_consecutive_failures', 0)
    resp = client.get('/api/dashboard/intraday')
    _assert_ok(resp)
    body = resp.get_json()
    assert body['success'] is True
    assert body['stocks'] == []
    assert body['disclaimer'] == '盘中口径，以收盘确认为准'
    assert 'session' in body and 'in_session' in body['session']
    assert 'patrol' in body and 'enabled' in body['patrol']


def test_dashboard_intraday_refresh_cooldown(client, monkeypatch):
    """021BT：一键刷新端点——首轮执行、冷却期内二轮被拒且带剩余秒数"""
    import modules.intraday_patrol as patrol

    monkeypatch.setattr(patrol, '_LAST_SNAPSHOT', None)
    monkeypatch.setattr(patrol, '_paused', False)
    monkeypatch.setattr(patrol, '_consecutive_failures', 0)
    monkeypatch.setattr(patrol, '_last_manual_refresh_ts', 0.0)
    monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', lambda symbols: {})

    r1 = client.post('/api/dashboard/intraday/refresh')
    _assert_ok(r1)
    b1 = r1.get_json()
    assert b1['success'] is True
    assert b1['ok'] is True  # 空库无持仓 → 零请求空快照轮

    r2 = client.post('/api/dashboard/intraday/refresh')
    _assert_ok(r2)
    b2 = r2.get_json()
    assert b2['success'] is True
    assert b2['ok'] is False
    assert b2['cooldown'] > 0  # 60s 冷却内被拒


def test_batch_analyze_over_limit_rejected(client):
    """021BP 项4：>20 只直接 400（R16 契约边界——前端拆批依赖单次 ≤20，不放宽）"""
    resp = client.post('/api/batch-analyze', json={'stock_ids': list(range(1, 22))})
    assert resp.status_code == 400
    assert '20' in resp.get_json()['message']


def test_export_endpoints(client_with_stock):
    """导出端点(空库下应返回 200 + 空报表,不落盘到工作区)"""
    client, stock_id = client_with_stock
    for path in ['/api/export/watchlist', f'/api/export/daily-report?stock_id={stock_id}']:
        resp = client.get(path)
        assert resp.status_code == 200, f'{path}: {resp.status_code} {resp.data[:200]}'


def test_v5_scoring_demo(client):
    """v5 演示端点(纯引擎演示,不依赖库存数据)"""
    _assert_ok(client.get('/api/v5/scoring-demo'))
