"""021BN-c（2026-09-08）：行业资金流断连自愈——历史接口回补口径修正测试

实测场景：东财实时 clist 接口被风控（RemoteDisconnected）期间，
push2his 历史日K接口（fflow/daykline）仍可用。本批修复：
- 回补基准行业清单改为近 10 交易日快照 code 并集（截断快照不再传染）
- 缺口检测纳入"快照最新日之后至个股K线最新日"的工作日（此前只向前看）
- 实时刷新失败写 error_logs（数据源健康度卡可见）
"""

from datetime import datetime, timedelta

import pytest

from app import app
from database import db_manager
from modules.market_overview import _backfill_base_codes, _compute_gap_dates


@pytest.fixture()
def fdb(tmp_path, monkeypatch):
    db_file = tmp_path / 'ff021bnc.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return app.test_client()


def _seed_snapshot(trade_date, codes):
    conn = db_manager.get_connection()
    for code, name in codes:
        conn.execute(
            'INSERT INTO industry_fund_flow (trade_date, code, name, main_net) VALUES (?,?,?,?)',
            (trade_date, code, name, 100.0),
        )
    conn.commit()
    conn.close()


def test_backfill_base_codes_union_covers_truncated_latest(fdb):
    """基准 = 近10日快照 code 并集：最新一日截断（只有 BK1）不传染回补清单。"""
    _seed_snapshot('2026-09-03', [('BK1', '电子'), ('BK2', '银行'), ('BK3', '医药')])
    _seed_snapshot('2026-09-07', [('BK1', '电子')])
    codes = _backfill_base_codes()
    got = {c for c, _ in codes}
    assert got == {'BK1', 'BK2', 'BK3'}


def test_backfill_base_codes_empty(fdb):
    assert _backfill_base_codes() == []


def test_compute_gap_dates_includes_day_after_latest():
    """核心场景：实时接口断连的"当日"（快照最新日之后、K线最新日之前）要进回补清单。"""
    gaps = _compute_gap_dates(
        '2026-09-07', '2026-09-08', {'2026-09-04', '2026-09-07'}
    )
    assert gaps == ['2026-09-08']


def test_compute_gap_dates_backward_only_when_no_raw_max():
    """无 K 线基准（raw_max=None）时维持 021BJ 原行为：只向前找历史缺口。"""
    gaps = _compute_gap_dates('2026-09-07', None, {'2026-09-03', '2026-09-07'})
    # 向前：09-04 缺、09-03 有 → 止
    assert gaps == ['2026-09-04']


def test_compute_gap_dates_skips_weekend():
    """周五快照缺周一 → 周一（工作日）入选，周末不入。have 含更早日使向前扫描即止。"""
    gaps = _compute_gap_dates('2026-09-04', '2026-09-07', {'2026-09-04', '2026-09-03'})
    assert gaps == ['2026-09-07']


def test_compute_gap_dates_no_gap_on_normal_day():
    """快照与 K 线同日、历史无缺口 → 空清单。"""
    have = {'2026-09-07', '2026-09-04'}
    assert _compute_gap_dates('2026-09-08', '2026-09-08', have) == []
    assert _compute_gap_dates('2026-09-07', '2026-09-07', have) == []


def test_compute_gap_dates_empty_latest():
    assert _compute_gap_dates(None, '2026-09-08', set()) == []


def test_source_error_visible_in_health_card(fdb):
    """实时刷新失败写 error_logs 后，健康度端点按数据源模块聚合可见（不被过滤）。"""
    conn = db_manager.get_connection()
    # 021BN-c⑤ 同款定时炸弹修复：created_at 必须相对 now——健康度端点只聚合
    # 近 7 天 error_logs，绝对日期（2026-09-08）在滚动窗口滑过后必腐烂。
    ts = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        'INSERT INTO error_logs (stock_id, module, error_type, error_message, created_at) '
        "VALUES (0, 'modules.market_overview', 'fetch_failed', '东财行业资金流实时接口刷新失败: x', ?)",
        (ts,),
    )
    conn.commit()
    conn.close()
    d = fdb.get('/api/health/sources').get_json()
    mods = [m['module'] for m in d['sources']]
    assert 'modules.market_overview' in mods
