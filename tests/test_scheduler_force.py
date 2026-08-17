"""
021F（方案A）：16:10 定时收盘批次默认强制重算的调度行为测试。

背景：2026-08-17 实测 29 只股票全天停留在 09:56 早盘分数——收盘批次
因 B11 复用（当日已有报告）全程跳过（耗时0s），收盘数据更新后未重算。
方案 A：交易日收盘批次 force=True（强制重算），非交易日保持默认复用。
本测试只锁定调度入口的参数语义，不触网（generate_daily_report 等全部 mock）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from modules import daily_report

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


class _FixedDatetime(datetime):
    """固定 now() 的 datetime 替身（仅替换 daily_report.datetime，timedelta/timezone 不动）"""
    DAY = datetime(2026, 8, 17, 17, 10, tzinfo=_CN_TZ)  # 周一（交易日）

    @classmethod
    def now(cls, tz=None):
        return cls.DAY


@pytest.fixture()
def mocked_flow(monkeypatch):
    """mock _run_full_report_flow 的全部下游（不触网、不写库、不注册真实 Timer）"""
    captured = {}

    def fake_generate(**kwargs):
        captured['force'] = kwargs.get('force', False)
        return None

    monkeypatch.setattr(daily_report, 'generate_daily_report', fake_generate)
    monkeypatch.setattr(daily_report, '_get_all_stocks', lambda: [])
    monkeypatch.setattr(daily_report, '_schedule_capital_retry', lambda symbols: None)
    monkeypatch.setattr('modules.alert_engine.scan_once', lambda: None)
    monkeypatch.setattr('modules.index_collector.refresh_all', lambda: None)
    monkeypatch.setattr('modules.market_overview.refresh_industry_fund_flow', lambda: None)
    return captured


def test_trading_day_scheduled_run_forces_recalc(mocked_flow, monkeypatch):
    """交易日收盘批次 → force=True（强制重算，不复用早盘报告）"""
    _FixedDatetime.DAY = datetime(2026, 8, 17, 17, 10, tzinfo=_CN_TZ)  # 周一
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)
    daily_report._run_full_report_flow()
    assert mocked_flow['force'] is True


def test_weekend_scheduled_run_keeps_reuse(mocked_flow, monkeypatch):
    """非交易日批次 → force=False（保持默认复用，避免周末脏写与无效重算）"""
    _FixedDatetime.DAY = datetime(2026, 8, 15, 17, 10, tzinfo=_CN_TZ)  # 周六
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)
    daily_report._run_full_report_flow()
    assert mocked_flow['force'] is False
