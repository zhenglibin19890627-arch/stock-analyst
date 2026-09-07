"""
OPT-8（2026-09-07）：数据源健康度端点测试

覆盖 GET /api/health/sources：
- 按维度聚合近 7 天 data_status（成功率/最后成功/连续失败/红黄绿分级）
- 按数据源模块聚合近 7 天 error_logs
- 整体分级取最差；端点只读（调用量表计数不变）
"""

import pytest

from app import app
from database import db_manager


@pytest.fixture()
def hdb(tmp_path, monkeypatch):
    db_file = tmp_path / 'health.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return app.test_client()


def _seed_status(stock_id, dimension, status, fetched_at):
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO data_status (stock_id, dimension, status, message, fetched_at) VALUES (?,?,?,?,?)',
        (stock_id, dimension, status, '', fetched_at),
    )
    conn.commit()
    conn.close()


def _seed_error(module, dimension, created_at, msg='boom'):
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO error_logs (stock_id, module, error_type, error_message, dimension, created_at) '
        "VALUES (1, ?, 'E', ?, ?, ?)",
        (module, msg, dimension, created_at),
    )
    conn.commit()
    conn.close()


def _counts():
    conn = db_manager.get_connection()
    a = conn.execute('SELECT COUNT(*) AS c FROM data_status').fetchone()['c']
    b = conn.execute('SELECT COUNT(*) AS c FROM error_logs').fetchone()['c']
    conn.close()
    return a, b


def test_health_sources_empty_all_green(hdb):
    r = hdb.get('/api/health/sources')
    assert r.status_code == 200
    d = r.get_json()
    assert d['success'] and d['window_days'] == 7
    assert d['overall_level'] == 'green'
    assert d['dimensions'] == [] and d['sources'] == []


def test_health_sources_dimension_aggregation(hdb):
    # kline：3 成 1 败（成功率 0.75 → green）；capital：1 成 2 败且最新两连败（0.33 且连续 2 → yellow）
    _seed_status(1, 'kline', 'success', '2026-09-01 10:00:00')
    _seed_status(1, 'kline', 'success', '2026-09-02 10:00:00')
    _seed_status(1, 'kline', 'success', '2026-09-03 10:00:00')
    _seed_status(1, 'kline', 'error', '2026-09-04 10:00:00')
    _seed_status(1, 'capital', 'error', '2026-09-06 10:00:00')
    _seed_status(1, 'capital', 'error', '2026-09-05 10:00:00')
    _seed_status(1, 'capital', 'success', '2026-09-04 10:00:00')
    # 7 天窗口外的旧记录不计入
    _seed_status(1, 'kline', 'error', '2026-01-01 10:00:00')

    d = hdb.get('/api/health/sources').get_json()
    by_dim = {x['dimension']: x for x in d['dimensions']}
    k = by_dim['kline']
    assert k['total'] == 4 and k['ok'] == 3
    assert abs(k['success_rate'] - 0.75) < 1e-6
    assert k['last_ok'] == '2026-09-03 10:00:00'
    assert k['consecutive_failures'] == 1
    c = by_dim['capital']
    assert c['consecutive_failures'] == 2
    assert c['level'] == 'red'  # 成功率 0.33 < 50%（规则：红=成功率<50% 或 连续失败≥3）
    assert d['overall_level'] == 'red'


def test_health_sources_red_on_low_rate(hdb):
    # 成功率 0 + 连续失败 3 → red
    for i, day in enumerate(['2026-09-04', '2026-09-05', '2026-09-06']):
        _seed_status(1, 'kline', 'error', f'{day} 10:00:0{i}')
    d = hdb.get('/api/health/sources').get_json()
    assert d['dimensions'][0]['level'] == 'red'
    assert d['overall_level'] == 'red'


def test_health_sources_module_errors(hdb):
    _seed_error('modules.collector.capital_flow._http_get_em', 'capital', '2026-09-06 09:00:00')
    _seed_error('modules.collector.capital_flow._http_get_em', 'capital', '2026-09-07 09:00:00')
    d = hdb.get('/api/health/sources').get_json()
    assert len(d['sources']) == 1
    m = d['sources'][0]
    assert m['module'] == 'modules.collector.capital_flow._http_get_em'
    assert m['errors_7d'] == 2
    assert m['last_error_at'] == '2026-09-07 09:00:00'
    assert m['level'] == 'red'  # 24h 内有错


def test_health_sources_is_readonly(hdb):
    _seed_status(1, 'kline', 'success', '2026-09-06 10:00:00')
    _seed_error('m.x', 'kline', '2026-09-06 11:00:00')
    before = _counts()
    for _ in range(3):
        assert hdb.get('/api/health/sources').status_code == 200
    assert _counts() == before
