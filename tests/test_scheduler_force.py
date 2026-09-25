"""
021F（方案A）：16:10 定时收盘批次默认强制重算的调度行为测试。

背景：2026-08-17 实测 29 只股票全天停留在 09:56 早盘分数——收盘批次
因 B11 复用（当日已有报告）全程跳过（耗时0s），收盘数据更新后未重算。
方案 A：交易日收盘批次 force=True（强制重算），非交易日保持默认复用。
本测试只锁定调度入口的参数语义，不触网（generate_daily_report 等全部 mock）。
"""

from datetime import datetime, timedelta, timezone

import pytest

# t6 拆包迁移：调度面实现单宿 modules/daily_report/_scheduler（补丁打在 facade 对包内调用不可见），
# 变量名保持 daily_report 以最小化本文件 diff。
from modules.daily_report import _scheduler as daily_report

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
        captured['market'] = kwargs.get('market_filter')
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


# ---- 021Z：市场拆分（A股主批次 / 港股独立批次）----

def test_main_batch_filters_a_stock_only(mocked_flow, monkeypatch):
    """021Z：A股主批次仅生成 a_stock——港股 16:00 收盘晚于主批次，由独立批次负责"""
    _FixedDatetime.DAY = datetime(2026, 8, 17, 15, 54, tzinfo=_CN_TZ)  # 周一
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)
    daily_report._run_full_report_flow()
    assert mocked_flow['market'] == 'a_stock'


def test_hk_batch_trading_day_forces_recalc(mocked_flow, monkeypatch):
    """021Z：港股批次（交易日）→ force=True 且仅生成 hk_stock，完成后注册次日批次"""
    _FixedDatetime.DAY = datetime(2026, 8, 17, 16, 10, tzinfo=_CN_TZ)  # 周一
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)
    registered = {}
    monkeypatch.setattr(
        daily_report, '_register_hk_report', lambda next_day=False: registered.update(next_day=next_day)
    )
    daily_report._hk_report_tick()
    assert mocked_flow['force'] is True
    assert mocked_flow['market'] == 'hk_stock'
    assert registered['next_day'] is True


def test_hk_batch_weekend_keeps_reuse(mocked_flow, monkeypatch):
    """021Z：港股批次（非交易日）→ force=False 复用，不脏写"""
    _FixedDatetime.DAY = datetime(2026, 8, 15, 16, 10, tzinfo=_CN_TZ)  # 周六
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)
    monkeypatch.setattr(daily_report, '_register_hk_report', lambda next_day=False: None)
    daily_report._hk_report_tick()
    assert mocked_flow['force'] is False
    assert mocked_flow['market'] == 'hk_stock'


def test_hk_tick_reschedules_even_on_exception(mocked_flow, monkeypatch):
    """021Z：生成异常也不断链——finally 中仍注册次日批次"""
    _FixedDatetime.DAY = datetime(2026, 8, 17, 16, 10, tzinfo=_CN_TZ)  # 周一
    monkeypatch.setattr(daily_report, 'datetime', _FixedDatetime)

    def boom(**kwargs):
        raise RuntimeError('模拟生成失败')

    monkeypatch.setattr(daily_report, 'generate_daily_report', boom)
    registered = {}
    monkeypatch.setattr(
        daily_report, '_register_hk_report', lambda next_day=False: registered.update(next_day=next_day)
    )
    daily_report._hk_report_tick()  # 不应抛出
    assert registered['next_day'] is True
