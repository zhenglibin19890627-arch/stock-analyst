"""
OPT-2：backups/ 备份保留策略清理脚本单元测试

覆盖 scripts/cleanup_backups.py 的纯函数逻辑：
- select_expired：保留策略并集边界（最近 N 天 ∪ 最新 M 份）
- is_backup_file：候选文件安全过滤（前缀/后缀/目录层级）
不触碰真实 backups/ 目录，全部使用 tmp_path 构造。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cleanup_backups import is_backup_file, select_expired

DAY = 86400


def _mk(entries: list[tuple[str, float]]) -> list[tuple[Path, float]]:
    """把 (名字, mtime) 构造成 (Path, mtime)，Path 只作标识不落盘。"""
    return [(Path(name), mtime) for name, mtime in entries]


class TestSelectExpired:
    def test_union_keep_35_daily_files(self):
        """35 份每日一份：超出 30 天且排名 10 名以外的 5 份应被删除。"""
        now = 1_800_000_000.0
        entries = _mk([(f'db_backup_{i:03d}.db', now - (34 - i) * DAY) for i in range(35)])
        expired = select_expired(entries, now, keep_days=30, keep_min_count=10)
        # 最老的 5 份：年龄 34/33/32/31/30... 注意边界——恰好 30 天的保留（<=）
        names = [p.name for p in expired]
        assert len(expired) == 4
        assert 'db_backup_000.db' in names and 'db_backup_003.db' in names
        assert all(name not in names for name in ('db_backup_004.db', 'db_backup_010.db', 'db_backup_034.db'))

    def test_min_count_protects_old_backups(self):
        """只有 8 份且全部很旧：保底份数保护，一份都不删。"""
        now = 1_800_000_000.0
        entries = _mk([(f'db_backup_{i}.db', now - (200 - i) * DAY) for i in range(8)])
        assert select_expired(entries, now, keep_days=30, keep_min_count=10) == []

    def test_boundary_exactly_keep_days_is_kept(self):
        """年龄恰好等于 keep_days 天的备份应保留（<= 判定）。"""
        now = 1_800_000_000.0
        entries = _mk([(f'a{i}.db', now - i * DAY) for i in range(15)])
        entries.append((Path('edge.db'), now - 30 * DAY))
        expired = select_expired(entries, now, keep_days=30, keep_min_count=10)
        assert Path('edge.db') not in expired

    def test_just_over_keep_days_expired_when_beyond_min_count(self):
        """年龄 31 天且排名在保底之外：删除。"""
        now = 1_800_000_000.0
        entries = _mk([(f'a{i}.db', now - i * DAY) for i in range(15)])
        entries.append((Path('old.db'), now - 31 * DAY))
        expired = select_expired(entries, now, keep_days=30, keep_min_count=10)
        assert Path('old.db') in expired

    def test_empty_entries(self):
        assert select_expired([], time.time()) == []

    def test_result_sorted_newest_first(self):
        """待删列表按新→旧排序，便于人工审阅。"""
        now = 1_800_000_000.0
        entries = _mk([(f'db_backup_{i:03d}.db', now - (40 - i) * DAY) for i in range(40)])
        expired = select_expired(entries, now, keep_days=30, keep_min_count=10)
        mtimes = [dict(entries)[p] for p in expired]
        assert mtimes == sorted(mtimes, reverse=True)


class TestIsBackupFile:
    def _make_tree(self, tmp_path: Path) -> Path:
        backups = tmp_path / 'backups'
        backups.mkdir()
        (backups / 'db_backup_20260907_080000_migrate.db').write_text('x')
        (backups / 'stock_analyst_backup_019S_20260809_2209.db').write_text('x')
        (backups / 'stock_analyst.db').write_text('x')  # 主库同名副本——绝不属于候选
        (backups / 'notes.txt').write_text('x')
        sub = backups / 'parent_git_backup_20260813.bak'
        sub.mkdir()
        (sub / 'db_backup_sneaky.db').write_text('x')  # 子目录内的同名文件——绝不属于候选
        return backups

    def test_known_prefixes_accepted(self, tmp_path):
        backups = self._make_tree(tmp_path)
        assert is_backup_file(backups / 'db_backup_20260907_080000_migrate.db', backups)
        assert is_backup_file(backups / 'stock_analyst_backup_019S_20260809_2209.db', backups)

    def test_main_db_and_non_db_rejected(self, tmp_path):
        backups = self._make_tree(tmp_path)
        assert not is_backup_file(backups / 'stock_analyst.db', backups)
        assert not is_backup_file(backups / 'notes.txt', backups)

    def test_subdirectory_files_never_candidates(self, tmp_path):
        """一次性人工备份子目录（如 parent_git_backup_*.bak）内的文件绝不可清理。"""
        backups = self._make_tree(tmp_path)
        sneaky = backups / 'parent_git_backup_20260813.bak' / 'db_backup_sneaky.db'
        assert not is_backup_file(sneaky, backups)

    def test_missing_file_rejected(self, tmp_path):
        backups = tmp_path / 'backups'
        backups.mkdir()
        assert not is_backup_file(backups / 'db_backup_ghost.db', backups)
