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

import app as app_module
import pytest
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


def test_engine_status(client):
    _assert_ok(client.get('/api/engine/status'))


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
        f'/api/stocks/{stock_id}/analysis',
        f'/api/stocks/{stock_id}/ratings',
    ]
    for path in paths:
        _assert_ok(client.get(path))


def test_ratings_list(client):
    _assert_ok(client.get('/api/ratings'))


# ---- portfolio 蓝图 ----

def test_portfolio_endpoints(client):
    paths = [
        '/api/portfolio/groups',
        '/api/portfolio/holdings',
        '/api/portfolio/summary',
        '/api/portfolio/watchlist-scores',
        '/api/portfolio/trades',
        '/api/portfolio/cost-adjustments',
        '/api/portfolio/realized-pnl',
    ]
    for path in paths:
        _assert_ok(client.get(path))


# ---- report 蓝图 ----

def test_report_endpoints(client):
    paths = [
        '/api/daily-report/latest',
        '/api/daily-report/history',
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
    import modules.daily_report as daily_report

    monkeypatch.setattr(daily_report, '_generate_lock', _Locked())

    for path in ['/api/daily-report/generate', '/api/daily-report/generate-intraday']:
        resp = client.post(path, json={})
        assert resp.status_code == 200, f'{path}: status={resp.status_code} body={resp.data[:300]}'
        data = resp.get_json()
        assert data['success'] is False
        assert '进行中' in data['message'], f'{path}: message={data.get("message")}'


# ---- backtest / export / index / alerts 蓝图 ----

def test_backtest_endpoints(client):
    _assert_ok(client.get('/api/backtest/status'))
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


def test_export_endpoints(client_with_stock):
    """导出端点(空库下应返回 200 + 空报表,不落盘到工作区)"""
    client, stock_id = client_with_stock
    for path in ['/api/export/watchlist', f'/api/export/daily-report?stock_id={stock_id}']:
        resp = client.get(path)
        assert resp.status_code == 200, f'{path}: {resp.status_code} {resp.data[:200]}'


def test_v5_scoring_demo(client):
    """v5 演示端点(纯引擎演示,不依赖库存数据)"""
    _assert_ok(client.get('/api/v5/scoring-demo'))
