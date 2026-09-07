"""
db_manager.auto_backup_db 每日自动备份测试（2026-09-07 批判性审查新增）。

此前全库仅有人工备份，服务常驻期间数据损坏将无当日快照可回。
"""

from database import db_manager
from database.db_manager import auto_backup_db


def _setup(tmp_path, monkeypatch):
    db = tmp_path / 'test.db'
    backup_dir = tmp_path / 'backups'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(backup_dir))
    db_manager.init_database()  # 建表，确保库有内容
    return backup_dir


def test_auto_backup_creates_daily_file(tmp_path, monkeypatch):
    backup_dir = _setup(tmp_path, monkeypatch)

    result = auto_backup_db()
    assert result is not None
    files = list(backup_dir.glob('db_backup_*_auto.db'))
    assert len(files) == 1


def test_auto_backup_idempotent_same_day(tmp_path, monkeypatch):
    backup_dir = _setup(tmp_path, monkeypatch)

    first = auto_backup_db()
    second = auto_backup_db()  # 当日已有 → 跳过
    assert first is not None
    assert second is None
    assert len(list(backup_dir.glob('db_backup_*_auto.db'))) == 1


def test_auto_backup_survives_missing_dir(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)  # backups 目录不预创建

    result = auto_backup_db()  # 内部 makedirs 兜底
    assert result is not None
