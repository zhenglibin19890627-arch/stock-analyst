"""
021AO：分组两侧独立——默认不同步创建/改名

覆盖：
1. POST /api/groups 默认不建另一类型同名组；显式 sync_to_other_type=True 才建
2. PUT /api/groups/<id> 默认只改当前类型；显式 True 才同步改名
3. POST /api/portfolio/groups（兼容别名）不再自动建 watchlist 同名组
"""

import pytest

from database import db_manager


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_groups_021ao.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    import app as app_module

    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def _names(env, gtype):
    body = env.get(f'/api/groups?type={gtype}').get_json()
    return sorted(g['name'] for g in body['groups'])


def test_create_group_no_sync_by_default(env):
    """默认只建当前类型，另一类型不出现同名组。"""
    body = env.post(
        '/api/groups', json={'name': '高股息', 'type': 'watchlist'}
    ).get_json()
    assert body['success']
    assert body['counterpart_created'] is False
    assert _names(env, 'watchlist') == ['高股息']
    assert _names(env, 'portfolio') == []


def test_create_group_explicit_sync(env):
    """显式 sync_to_other_type=True 才两侧同建。"""
    body = env.post(
        '/api/groups',
        json={'name': '高股息', 'type': 'watchlist', 'sync_to_other_type': True},
    ).get_json()
    assert body['success']
    assert body['counterpart_created'] is True
    assert _names(env, 'watchlist') == ['高股息']
    assert _names(env, 'portfolio') == ['高股息']


def test_rename_no_sync_by_default(env):
    """改名默认只改当前类型；显式 True 同步另一侧。"""
    env.post('/api/groups', json={'name': '高股息', 'type': 'watchlist'})
    env.post(
        '/api/groups',
        json={'name': '高股息', 'type': 'portfolio', 'sync_to_other_type': True},
    )
    wid = env.get('/api/groups?type=watchlist').get_json()['groups'][0]['id']

    body = env.put(f'/api/groups/{wid}', json={'name': '红利策略'}).get_json()
    assert body['success']
    assert _names(env, 'watchlist') == ['红利策略']
    assert _names(env, 'portfolio') == ['高股息'], '默认不应同步改名'

    # 显式同步：两侧已不同名（高股息 vs 红利策略）→ 找不到配对组，不联动
    pid = env.get('/api/groups?type=portfolio').get_json()['groups'][0]['id']
    body2 = env.put(
        f'/api/groups/{pid}',
        json={'name': '红利优选', 'sync_to_other_type': True},
    ).get_json()
    assert body2['success'] and body2['counterpart_updated'] is False
    assert _names(env, 'portfolio') == ['红利优选']
    assert _names(env, 'watchlist') == ['红利策略']

    # 两侧同名时显式同步：改名联动
    env.post('/api/groups', json={'name': '周期股', 'type': 'watchlist'})
    env.post(
        '/api/groups',
        json={'name': '周期股', 'type': 'portfolio', 'sync_to_other_type': True},
    )
    groups_p = env.get('/api/groups?type=portfolio').get_json()['groups']
    pid2 = next(g['id'] for g in groups_p if g['name'] == '周期股')
    body3 = env.put(
        f'/api/groups/{pid2}',
        json={'name': '顺周期', 'sync_to_other_type': True},
    ).get_json()
    assert body3['success'] and body3['counterpart_updated'] is True
    assert _names(env, 'portfolio') == ['红利优选', '顺周期']
    assert _names(env, 'watchlist') == ['红利策略', '顺周期']


def test_portfolio_groups_alias_no_auto_sync(env):
    """POST /api/portfolio/groups 兼容端点不再自动建 watchlist 同名组。"""
    body = env.post('/api/portfolio/groups', json={'name': '打新仓'}).get_json()
    assert body['success']
    assert _names(env, 'portfolio') == ['打新仓']
    assert _names(env, 'watchlist') == []
