"""
021AX：行业资金流快照自愈（双防线）+ 港股指数列漂移降级 测试

1. _industry_flow_stale：无快照/缺最新日/盘中未定稿/收盘后定稿 四态
2. _maybe_backfill_industry_flow：盘中不触发、收盘后缺失触发、冷却中跳过、
   刷新异常吞掉不抛
3. _hk_report_tick：港股批次完成后追加行业资金流二次刷新（冷却中跳过）
4. fetch_index_kline：HK EM 返回缺 close 列 → 降级新浪
"""

import datetime as dt

import pytest

from database import db_manager


@pytest.fixture()
def flow_env(tmp_path, monkeypatch):
    """隔离库：个股K线至8/26。"""
    db_path = str(tmp_path / 'test_flow_021ax.db')
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
    conn.commit()
    conn.close()
    return sid


def _save_flow(trade_date, created_at=None, code='BK0001'):
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO industry_fund_flow '
        '(trade_date, code, name, main_net, created_at) VALUES (?,?,?,?,?)',
        (trade_date, code, '测试行业', 1.0, created_at),
    )
    conn.commit()
    conn.close()


class TestIndustryFlowStale:
    def test_no_snapshot(self, flow_env):
        from modules.backfill_scheduler import _industry_flow_stale

        assert _industry_flow_stale() is True

    def test_missing_latest_day(self, flow_env):
        from modules.backfill_scheduler import _industry_flow_stale

        _save_flow('2026-08-24')
        assert _industry_flow_stale() is True

    def test_intraday_snapshot_stale(self, flow_env):
        """当日快照但保存于 13:48（盘中）→ 未定稿。"""
        from modules.backfill_scheduler import _industry_flow_stale

        _save_flow('2026-08-26', created_at='2026-08-26 13:48:49')
        assert _industry_flow_stale() is True

    def test_final_snapshot_ok(self, flow_env):
        from modules.backfill_scheduler import _industry_flow_stale

        _save_flow('2026-08-26', created_at='2026-08-26 15:56:31')
        assert _industry_flow_stale() is False


class TestMaybeBackfillIndustryFlow:
    def test_before_gate_no_refresh(self, flow_env, monkeypatch):
        from modules import backfill_scheduler

        called = []
        monkeypatch.setattr(
            'modules.market_overview.refresh_industry_fund_flow',
            lambda: called.append(1),
        )
        monkeypatch.setattr('modules.market_overview.refresh_in_cooldown', lambda: None)
        ok = backfill_scheduler._maybe_backfill_industry_flow(
            now=dt.datetime(2026, 8, 26, 14, 0)
        )
        assert ok is False and not called

    def test_missing_triggers_refresh(self, flow_env, monkeypatch):
        from modules import backfill_scheduler

        called = []
        monkeypatch.setattr(
            'modules.market_overview.refresh_industry_fund_flow',
            lambda: called.append(1),
        )
        monkeypatch.setattr('modules.market_overview.refresh_in_cooldown', lambda: None)
        ok = backfill_scheduler._maybe_backfill_industry_flow(
            now=dt.datetime(2026, 8, 26, 16, 15)
        )
        assert ok is True and len(called) == 1

    def test_cooldown_skips(self, flow_env, monkeypatch):
        from modules import backfill_scheduler

        called = []
        monkeypatch.setattr(
            'modules.market_overview.refresh_industry_fund_flow',
            lambda: called.append(1),
        )
        monkeypatch.setattr(
            'modules.market_overview.refresh_in_cooldown', lambda: 300
        )
        ok = backfill_scheduler._maybe_backfill_industry_flow(
            now=dt.datetime(2026, 8, 26, 16, 15)
        )
        assert ok is False and not called

    def test_refresh_exception_swallowed(self, flow_env, monkeypatch):
        from modules import backfill_scheduler

        def _boom():
            raise RuntimeError('EM 全挂')

        monkeypatch.setattr(
            'modules.market_overview.refresh_industry_fund_flow', _boom
        )
        monkeypatch.setattr('modules.market_overview.refresh_in_cooldown', lambda: None)
        ok = backfill_scheduler._maybe_backfill_industry_flow(
            now=dt.datetime(2026, 8, 26, 16, 15)
        )
        assert ok is False


def test_hk_batch_refreshes_industry_flow(monkeypatch):
    """港股批次(16:10)完成后必须追加行业资金流二次刷新（021AX 主修复）。"""
    import modules.daily_report as dr

    order = []
    monkeypatch.setattr(
        dr, 'generate_daily_report', lambda **kw: order.append('report') or {}
    )
    monkeypatch.setattr('modules.index_collector.refresh_all', lambda: order.append('index'))
    monkeypatch.setattr(
        'modules.market_overview.refresh_in_cooldown', lambda: None
    )
    monkeypatch.setattr(
        'modules.market_overview.refresh_industry_fund_flow',
        lambda: order.append('flow'),
    )
    monkeypatch.setattr(dr, '_register_hk_report', lambda next_day=False: None)
    dr._hk_report_tick()
    assert order == ['report', 'index', 'flow']


def test_hk_batch_cooldown_skips_flow(monkeypatch):
    """冷却中（刚失败过）不硬闯东财。"""
    import modules.daily_report as dr

    order = []
    monkeypatch.setattr(dr, 'generate_daily_report', lambda **kw: {})
    monkeypatch.setattr('modules.index_collector.refresh_all', lambda: None)
    monkeypatch.setattr(
        'modules.market_overview.refresh_in_cooldown', lambda: 120
    )
    monkeypatch.setattr(
        'modules.market_overview.refresh_industry_fund_flow',
        lambda: order.append('flow'),
    )
    monkeypatch.setattr(dr, '_register_hk_report', lambda next_day=False: None)
    dr._hk_report_tick()
    assert order == []


def test_hk_index_em_column_drift_falls_back_to_sina(monkeypatch):
    """EM 返回非空但缺 close 列 → 降级新浪而非空表返回。"""
    import pandas as pd

    import modules.index_collector as ic

    em_df = pd.DataFrame({'date': ['2026-08-25'], 'open': [1], 'high': [1], 'low': [1]})
    sina_df = pd.DataFrame(
        {
            'date': ['2026-08-25'],
            'open': [30000.0],
            'high': [30100.0],
            'low': [29900.0],
            'close': [30050.0],
            'volume': [1],
        }
    )
    calls = {'sina': 0}

    import akshare

    monkeypatch.setattr(
        akshare, 'stock_hk_index_daily_em', lambda symbol: em_df
    )
    monkeypatch.setattr(
        akshare,
        'stock_hk_index_daily_sina',
        lambda symbol: calls.__setitem__('sina', calls['sina'] + 1) or sina_df,
    )
    df = ic.fetch_index_kline(
        {'name': '恒生指数', 'ak_symbol': 'HSI', 'market': 'HK'}
    )
    assert calls['sina'] == 1
    assert not df.empty and 'close' in df.columns and df.iloc[0]['close'] == 30050.0
