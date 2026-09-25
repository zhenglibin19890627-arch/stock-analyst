"""
021AW：指数K线滞后自愈（两处防线）测试

1. _index_kline_stale：落后/同步/空库三态判定
2. _maybe_refresh_stale_indexes：盘中不检查、收盘后滞后触发刷新、刷新异常不抛
3. _hk_report_tick：港股批次（16:10）完成后追加一次指数刷新
4. _tick：无个股缺口空转时指数自愈仍生效
"""

import datetime as dt

import pytest

from database import db_manager


@pytest.fixture()
def idx_env(tmp_path, monkeypatch):
    """隔离库：1只股票K线至8/26，指数K线至8/24（复刻8/25故障现场）。"""
    db_path = str(tmp_path / 'test_idx_021aw.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()

    conn = db_manager.get_connection()
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', 'X')")
    sid = conn.execute('SELECT id FROM stocks').fetchone()['id']
    conn.execute(
        'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        "VALUES (?, '2026-08-26', 10, 10, 10, 10, 100)",
        (sid,),
    )
    conn.execute(
        'INSERT INTO index_kline (index_code, trade_date, open, high, low, close, volume) '
        "VALUES ('000001', '2026-08-24', 3000, 3000, 3000, 3000, 1)"
    )
    conn.commit()
    conn.close()
    return sid


class TestIndexKlineStale:
    def test_stale(self, idx_env):
        from modules.backfill_scheduler import _index_kline_stale

        assert _index_kline_stale() is True  # 指数8/24 < 个股8/26

    def test_sync_after_refresh(self, idx_env):
        from modules.backfill_scheduler import _index_kline_stale

        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO index_kline (index_code, trade_date, open, high, low, close, volume) '
            "VALUES ('000001', '2026-08-26', 3000, 3000, 3000, 3000, 1)"
        )
        conn.commit()
        conn.close()
        assert _index_kline_stale() is False

    def test_empty_kline_not_stale(self, tmp_path, monkeypatch):
        from modules.backfill_scheduler import _index_kline_stale

        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'empty.db'))
        db_manager.init_database()
        assert _index_kline_stale() is False


class TestMaybeRefreshStaleIndexes:
    def test_before_close_time_no_refresh(self, idx_env, monkeypatch):
        from modules import backfill_scheduler

        called = []
        monkeypatch.setattr(
            'modules.index_collector.refresh_all', lambda: called.append(1)
        )
        # 14:59 盘中：不检查（个股当日行已存在而指数bar未发布，比较必假滞后）
        ok = backfill_scheduler._maybe_refresh_stale_indexes(
            now=dt.datetime(2026, 8, 26, 14, 59)
        )
        assert ok is False and not called

    def test_after_close_stale_triggers_refresh(self, idx_env, monkeypatch):
        from modules import backfill_scheduler

        called = []
        monkeypatch.setattr(
            'modules.index_collector.refresh_all', lambda: called.append(1)
        )
        ok = backfill_scheduler._maybe_refresh_stale_indexes(
            now=dt.datetime(2026, 8, 26, 16, 15)
        )
        assert ok is True and len(called) == 1

    def test_refresh_exception_swallowed(self, idx_env, monkeypatch):
        from modules import backfill_scheduler

        def _boom():
            raise RuntimeError('EM 不可达')

        monkeypatch.setattr('modules.index_collector.refresh_all', _boom)
        ok = backfill_scheduler._maybe_refresh_stale_indexes(
            now=dt.datetime(2026, 8, 26, 16, 15)
        )
        assert ok is False  # 失败不抛，等待下轮巡检


# OPT-5：真实定时器验证（基线实测 6.3s），默认跳过；全量运行：pytest -m "slow or not slow"
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_hk_batch_refreshes_indexes(monkeypatch):
    """港股批次(16:10)完成后必须追加指数刷新（021AW 主修复）。"""
    # t6 拆包迁移：调度面实现单宿 modules/daily_report/_scheduler
    from modules.daily_report import _scheduler as dr

    order = []
    monkeypatch.setattr(
        dr, 'generate_daily_report', lambda **kw: order.append('report') or {}
    )
    monkeypatch.setattr(
        'modules.index_collector.refresh_all', lambda: order.append('index')
    )
    monkeypatch.setattr(dr, '_register_hk_report', lambda next_day=False: None)
    dr._hk_report_tick()
    assert order == ['report', 'index']


# OPT-5：真实时钟自愈验证（基线实测 34.9s），默认跳过；全量运行：pytest -m "slow or not slow"
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_tick_self_heals_even_without_stock_gaps(idx_env, monkeypatch):
    """空转巡检（无个股缺口）时指数自愈仍生效且按低频间隔续排。"""
    from modules import backfill_scheduler

    called = []
    monkeypatch.setattr(backfill_scheduler, '_get_stocks_with_gaps', lambda: {})
    monkeypatch.setattr(
        'modules.index_collector.refresh_all', lambda: called.append(1)
    )
    scheduled = []
    monkeypatch.setattr(
        backfill_scheduler, '_schedule_next', lambda m: scheduled.append(m)
    )
    # 包装计数 + 注入"收盘后"时刻绕过时钟门槛
    orig = backfill_scheduler._maybe_refresh_stale_indexes
    monkeypatch.setattr(
        backfill_scheduler,
        '_maybe_refresh_stale_indexes',
        lambda: called.append('heal') or orig(now=dt.datetime(2026, 8, 26, 16, 20)),
    )
    backfill_scheduler._tick()
    assert 'heal' in called and 1 in called  # 自愈检查执行且触发了刷新
    assert scheduled == [backfill_scheduler.IDLE_INTERVAL_MIN]  # 空转降为低频
