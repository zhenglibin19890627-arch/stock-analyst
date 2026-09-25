"""
021AY：启动补跑（睡眠错过批次钟点自愈）测试

1. _catchup_tick：交易日过点+不齐→补跑；已齐→跳过；未到点→跳过；周末→跳过
2. _register_catchup / stop_scheduler：注册与防御性取消
3. 补跑批次完成后挂载指数/行业流刷新
"""

import datetime as dt

import pytest

from database import db_manager


@pytest.fixture()
def catch_env(tmp_path, monkeypatch):
    """隔离库：2 只股票，0 份今日报告（不齐）。"""
    db_path = str(tmp_path / 'test_catchup_021ay.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()

    conn = db_manager.get_connection()
    for sym in ('600519', '000333'):
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES (?, 'a_stock', ?)",
            (sym, sym),
        )
    conn.commit()
    conn.close()
    return None


WED_2030 = dt.datetime(2026, 8, 26, 20, 30)  # 周三 20:30（过点）


def _mk(monkeypatch, env, gen=None):
    # t6 拆包迁移：调度面实现单宿 modules/daily_report/_scheduler（补丁打在 facade 对包内调用不可见）
    from modules.daily_report import _scheduler as dr

    calls = []

    def _gen(**kw):
        calls.append(('gen', kw))
        return {'success': True}

    monkeypatch.setattr(dr, 'generate_daily_report', gen or _gen)
    monkeypatch.setattr('modules.index_collector.refresh_all', lambda: calls.append('index'))
    monkeypatch.setattr(
        'modules.market_overview.refresh_in_cooldown', lambda: None
    )
    monkeypatch.setattr(
        'modules.market_overview.refresh_industry_fund_flow',
        lambda: calls.append('flow'),
    )
    return dr, calls


class TestCatchupTick:
    def test_incomplete_after_gate_runs_full_batch(self, catch_env, monkeypatch):
        dr, calls = _mk(monkeypatch, catch_env)
        ok = dr._catchup_tick(now=WED_2030)
        assert ok is True
        assert calls[0][0] == 'gen' and calls[0][1].get('force') is True
        assert 'index' in calls and 'flow' in calls  # 挂载项同批执行

    def test_complete_skips(self, catch_env, monkeypatch):
        dr, calls = _mk(monkeypatch, catch_env)
        conn = db_manager.get_connection()
        today = dt.date.today().isoformat()
        sids = [r['id'] for r in conn.execute('SELECT id FROM stocks')]
        for sid in sids:
            conn.execute(
                'INSERT INTO daily_reports (stock_id, report_date, report_type) '
                "VALUES (?, ?, 'daily')",
                (sid, today),
            )
        conn.commit()
        conn.close()
        ok = dr._catchup_tick(now=WED_2030)
        assert ok is False and not calls

    def test_before_gate_skips(self, catch_env, monkeypatch):
        dr, calls = _mk(monkeypatch, catch_env)
        ok = dr._catchup_tick(now=dt.datetime(2026, 8, 26, 15, 0))
        assert ok is False and not calls

    def test_weekend_skips(self, catch_env, monkeypatch):
        dr, calls = _mk(monkeypatch, catch_env)
        ok = dr._catchup_tick(now=dt.datetime(2026, 8, 29, 20, 30))  # 周六
        assert ok is False and not calls

    def test_generate_failure_isolated(self, catch_env, monkeypatch):
        dr, calls = _mk(monkeypatch, catch_env)

        def _boom(**kw):
            raise RuntimeError('采集全挂')

        dr2, _ = _mk(monkeypatch, catch_env, gen=_boom)
        ok = dr2._catchup_tick(now=WED_2030)
        assert ok is False  # 异常吞掉不抛


def test_register_and_stop_catchup(monkeypatch):
    """start_scheduler 注册补跑 Timer；stop_scheduler 防御性取消。"""
    # t6 拆包迁移：调度器可变状态（Timer 柄）单宿 modules/daily_report/_scheduler
    from modules.daily_report import _scheduler as dr

    fired = []

    class _FakeTimer:
        def __init__(self, interval, fn):
            self.interval, self.fn, self.daemon = interval, fn, False

        def start(self):
            fired.append('start')

        def cancel(self):
            fired.append('cancel')

    monkeypatch.setattr(dr.threading, 'Timer', _FakeTimer)
    dr._register_catchup()
    assert fired == ['start']
    dr.stop_scheduler()
    assert 'cancel' in fired
    dr._catchup_timer = None  # 清理全局态
