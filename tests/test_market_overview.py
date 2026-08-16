"""
market_overview 行业资金流冷却机制单元测试（021C）。

背景：2026-08-15 晚实测刷新失败后 1 分钟内重复硬闯东财 7 次——
冷却状态原为纯内存态，服务重启（联调/看门狗拉起）即清零。
021C 起改为内存 + 落盘双写，本测试锁定落盘语义。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from modules import market_overview as mo

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    f = tmp_path / 'industry_ff_cooldown.json'
    monkeypatch.setattr(mo, '_COOLDOWN_STATE_FILE', str(f))
    return f


def test_mark_failure_persists(state_file, monkeypatch):
    """_mark_failure 双写：内存 + 落盘"""
    monkeypatch.setattr(mo, '_last_failure_at', None)
    mo._mark_failure()
    assert mo._last_failure_at is not None
    data = json.loads(state_file.read_text(encoding='utf-8'))
    assert 'failed_at' in data


def test_refresh_in_cooldown_reads_file_after_restart(state_file, monkeypatch):
    """模拟重启（内存态清零）后，冷却从落盘文件恢复生效"""
    monkeypatch.setattr(mo, '_last_failure_at', None)
    now = datetime.now(_CN_TZ)
    state_file.write_text(json.dumps({'failed_at': now.isoformat()}), encoding='utf-8')
    left = mo.refresh_in_cooldown()
    assert left is not None
    assert left <= mo.REFRESH_COOLDOWN_SECONDS
    assert left > mo.REFRESH_COOLDOWN_SECONDS - 10


def test_refresh_in_cooldown_expired(state_file, monkeypatch):
    """冷却时间已过 → 返回 None（可重试）"""
    monkeypatch.setattr(mo, '_last_failure_at', None)
    old = datetime.now(_CN_TZ) - timedelta(seconds=mo.REFRESH_COOLDOWN_SECONDS + 60)
    state_file.write_text(json.dumps({'failed_at': old.isoformat()}), encoding='utf-8')
    assert mo.refresh_in_cooldown() is None


def test_refresh_in_cooldown_no_state(state_file, monkeypatch):
    """无任何失败记录 → 返回 None"""
    monkeypatch.setattr(mo, '_last_failure_at', None)
    assert mo.refresh_in_cooldown() is None
