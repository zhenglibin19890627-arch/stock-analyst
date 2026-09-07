"""
021AN：分组完全自定义——无默认组、删除后不复活

覆盖：
1. 全新库 init_database() 后 groups 表为零（两种类型都不预置）
2. 用户自建组删除后，再次 init_database()（模拟服务重启）不复活
3. 存量 is_default=1 的组可正常删除（无删除保护）
4. 删除组后组内记录 group_id 置 NULL（移至未分组）
"""

import pytest

from database import db_manager


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_groups_021an.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    import app as app_module

    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def _count_groups():
    conn = db_manager.get_connection()
    cnt = conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0]
    conn.close()
    return cnt


def test_fresh_db_has_no_default_groups(env):
    """全新库：不预置任何默认分组。"""
    assert _count_groups() == 0
    body = env.get('/api/groups?type=watchlist').get_json()
    assert body['success'] and body['groups'] == []
    body2 = env.get('/api/groups?type=portfolio').get_json()
    assert body2['success'] and body2['groups'] == []


def test_deleted_group_does_not_resurrect(env):
    """自建组删除后，重跑 init_database()（模拟重启）不复活。"""
    body = env.post(
        '/api/groups', json={'name': '我的自定义组', 'type': 'watchlist', 'sync_to_other_type': False}
    ).get_json()
    assert body['success']
    gid = body['group_id']
    assert env.delete(f'/api/groups/{gid}').get_json()['success']
    assert _count_groups() == 0

    db_manager.init_database()  # 模拟服务重启
    assert _count_groups() == 0, '默认组播种已移除，重启不得复活任何组'


def test_legacy_default_flag_group_deletable(env):
    """存量 is_default=1 的组可正常删除（无删除保护），记录移至未分组。"""
    # 造一只股票 + 一个 is_default=1 的组（模拟存量数据）
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    stock_id = conn.execute('SELECT id FROM stocks').fetchone()['id']
    conn.execute(
        "INSERT INTO groups (name, type, is_default) VALUES ('观察池', 'watchlist', 1)"
    )
    gid = conn.execute("SELECT id FROM groups WHERE name='观察池'").fetchone()['id']
    conn.execute('UPDATE stocks SET group_id=? WHERE id=?', (gid, stock_id))
    conn.commit()
    conn.close()

    resp = env.delete(f'/api/groups/{gid}')
    body = resp.get_json()
    assert resp.status_code == 200
    assert body['success']
    assert body['migrated_count'] == 1

    conn = db_manager.get_connection()
    g = conn.execute('SELECT group_id FROM stocks WHERE id=?', (stock_id,)).fetchone()
    cnt = conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0]
    conn.close()
    assert g['group_id'] is None, '组内记录应移至未分组（group_id=NULL）'
    assert cnt == 0
