"""
OPT-8（2026-09-07）：数据源健康度端点测试

覆盖 GET /api/health/sources：
- 按维度聚合近 7 天 data_status（成功率/最后成功/连续失败/红黄绿分级）
- 按数据源模块聚合近 7 天 error_logs
- 整体分级取最差；端点只读（调用量表计数不变）

021BN 口径修正：
- skipped（节流/维度不适用）不计入成功率分子分母、不算连续失败
- 全 skipped（无有效分母）判绿不告警
- north_capital 停更维度标灰（grey）且排除出整体分级
- 非数据源模块（prefill_analytics 等应用层日志）不进 sources
- 维度附接口说明（label/desc）

021BN-c：种子日期由绝对值改为相对 now——7 天滚动窗口下绝对日期
隔天必腐烂（实测 09-08 晚间 3 例误红）。
"""

from datetime import datetime, timedelta

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


def _ts(days_ago, h=10, m=0, s=0):
    """相对当前时刻的种子时间戳（同日内用 h/m/s 区分先后）。"""
    day = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
    return f'{day} {h:02d}:{m:02d}:{s:02d}'


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
    _seed_status(1, 'kline', 'success', _ts(3, 10))
    _seed_status(1, 'kline', 'success', _ts(2, 10))
    _seed_status(1, 'kline', 'success', _ts(1, 10))
    _seed_status(1, 'kline', 'error', _ts(0, 10))
    _seed_status(1, 'capital', 'error', _ts(0, 10, 0, 0))
    _seed_status(1, 'capital', 'error', _ts(1, 10))
    _seed_status(1, 'capital', 'success', _ts(2, 10))
    # 7 天窗口外的旧记录不计入
    _seed_status(1, 'kline', 'error', _ts(20, 10))

    d = hdb.get('/api/health/sources').get_json()
    by_dim = {x['dimension']: x for x in d['dimensions']}
    k = by_dim['kline']
    assert k['total'] == 4 and k['ok'] == 3
    assert abs(k['success_rate'] - 0.75) < 1e-6
    assert k['last_ok'] == _ts(1, 10)
    assert k['consecutive_failures'] == 1
    c = by_dim['capital']
    assert c['consecutive_failures'] == 2
    assert c['level'] == 'red'  # 成功率 0.33 < 50%（规则：红=成功率<50% 或 连续失败≥3）
    assert d['overall_level'] == 'red'


def test_health_sources_red_on_low_rate(hdb):
    # 成功率 0 + 连续失败 3 → red
    for i in range(3):
        _seed_status(1, 'kline', 'error', _ts(0, 9, 0, i))
    d = hdb.get('/api/health/sources').get_json()
    assert d['dimensions'][0]['level'] == 'red'
    assert d['overall_level'] == 'red'


def test_health_sources_module_errors(hdb):
    _seed_error('modules.collector.capital_flow._http_get_em', 'capital', _ts(2, 9))
    _seed_error('modules.collector.capital_flow._http_get_em', 'capital', _ts(0, 9))
    d = hdb.get('/api/health/sources').get_json()
    assert len(d['sources']) == 1
    m = d['sources'][0]
    assert m['module'] == 'modules.collector.capital_flow._http_get_em'
    assert m['errors_7d'] == 2
    assert m['last_error_at'] == _ts(0, 9)
    assert m['level'] == 'red'  # 24h 内有错


def test_health_sources_is_readonly(hdb):
    _seed_status(1, 'kline', 'success', _ts(2, 10))
    _seed_error('m.x', 'kline', _ts(2, 11))
    before = _counts()
    for _ in range(3):
        assert hdb.get('/api/health/sources').status_code == 200
    assert _counts() == before


# ============================================================
# 021BN：口径修正
# ============================================================


def test_skipped_excluded_from_rate_and_streak(hdb):
    """港股无快报/当日节流产生的 skipped 不算失败：1成+3跳过+1败 → 成功率 50%、连续失败 1"""
    _seed_status(1, 'express', 'success', _ts(2, 10))
    _seed_status(1, 'express', 'skipped', _ts(1, 10))
    _seed_status(1, 'express', 'skipped', _ts(1, 11))
    _seed_status(1, 'express', 'skipped', _ts(0, 10))
    _seed_status(1, 'express', 'failed', _ts(0, 11))
    d = hdb.get('/api/health/sources').get_json()
    x = d['dimensions'][0]
    assert x['total'] == 5 and x['skipped'] == 3
    assert abs(x['success_rate'] - 0.5) < 1e-6  # 1 / (5-3)，skipped 剔除出分母
    assert x['consecutive_failures'] == 1  # skipped 不算失败也不定稿失败段
    assert x['level'] == 'yellow'  # 有失败但不再触发红


def test_all_skipped_is_green(hdb):
    """全部为 skipped（无有效分母）→ 绿灯不告警（旧口径会因 rate=0 误判红）"""
    for i in range(3):
        _seed_status(1, 'orderbook', 'skipped', _ts(0, 9, 0, i))
    d = hdb.get('/api/health/sources').get_json()
    x = d['dimensions'][0]
    assert x['success_rate'] is None
    assert x['level'] == 'green'
    assert d['overall_level'] == 'green'


def test_north_capital_discontinued_grey(hdb):
    """停更维度（北向资金）：有失败也只标灰、附停更标记，且不拖垮整体分级"""
    _seed_status(1, 'north_capital', 'failed', _ts(6, 10))
    d = hdb.get('/api/health/sources').get_json()
    x = d['dimensions'][0]
    assert x['discontinued'] is True
    assert x['level'] == 'grey'
    assert x['label'] == '北向资金'
    assert '停更' in x['desc']
    assert d['overall_level'] == 'green'


def test_prefill_analytics_not_a_source(hdb):
    """应用层提示日志模块不进"数据源健康度\""""
    _seed_error('prefill_analytics', None, _ts(1, 9))
    d = hdb.get('/api/health/sources').get_json()
    assert d['sources'] == []


def test_dimensions_have_meta(hdb):
    """维度附带接口说明（label/desc），供前端"说明"列展示"""
    _seed_status(1, 'kline', 'success', _ts(2, 10))
    d = hdb.get('/api/health/sources').get_json()
    x = d['dimensions'][0]
    assert x['label'] == 'K线行情'
    assert '腾讯' in x['desc']
