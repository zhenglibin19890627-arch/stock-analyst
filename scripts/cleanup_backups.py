"""
backups/ 备份保留策略清理脚本（OPT-2，2026-09-07）

保留策略（两者取并集，宁多勿删）：
  1. 最近 KEEP_DAYS 天内修改的备份；
  2. 最新的 KEEP_MIN_COUNT 份备份（无论多旧）。

用法：
  python scripts/cleanup_backups.py             # 默认 dry-run，只打印将删除的清单
  python scripts/cleanup_backups.py --execute   # 真正删除（务必先跑 dry-run 人工确认！）

安全约束（红线）：
  - 只处理 backups/ 顶层目录下、匹配已知备份命名前缀（db_backup_* / stock_analyst_backup_*）的 .db 文件；
  - 绝不触碰 stock_analyst.db 本体；
  - 绝不递归进入 backups/ 内任何子目录（如 parent_git_backup_*.bak 这类一次性人工备份）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = PROJECT_ROOT / 'backups'
DB_PATH = PROJECT_ROOT / 'stock_analyst.db'

# 保留策略默认值（改动即影响删除行为，须同步更新 tests/test_cleanup_backups_opt2.py）
KEEP_DAYS = 30
KEEP_MIN_COUNT = 10

BACKUP_PREFIXES = ('db_backup_', 'stock_analyst_backup_')


def is_backup_file(path: Path, backups_dir: Path = BACKUPS_DIR) -> bool:
    """判定是否为可清理的 DB 备份候选：backups 顶层 + 已知命名前缀 + .db 后缀。"""
    try:
        return (
            path.is_file()
            and path.parent == backups_dir
            and path.suffix == '.db'
            and path.name.startswith(BACKUP_PREFIXES)
        )
    except OSError:
        return False


def select_expired(
    entries: list[tuple[Path, float]],
    now_ts: float,
    keep_days: int = KEEP_DAYS,
    keep_min_count: int = KEEP_MIN_COUNT,
) -> list[Path]:
    """从 (path, mtime) 列表中选出应删除的备份（纯函数，便于测试）。

    保留 = 最新 keep_min_count 份 ∪ mtime 距 now_ts 不超过 keep_days 天者；
    返回待删列表（按新→旧排序）。
    """
    ordered = sorted(entries, key=lambda e: e[1], reverse=True)
    keep: set[Path] = set()
    for rank, (path, mtime) in enumerate(ordered):
        if rank < keep_min_count or (now_ts - mtime) <= keep_days * 86400:
            keep.add(path)
    return [path for path, _ in ordered if path not in keep]


def collect_candidates(backups_dir: Path = BACKUPS_DIR) -> list[tuple[Path, float]]:
    """扫描 backups 顶层（不递归），返回 (路径, mtime) 候选列表。"""
    entries: list[tuple[Path, float]] = []
    if not backups_dir.is_dir():
        return entries
    for item in backups_dir.iterdir():  # iterdir 不递归，天然隔离子目录
        if is_backup_file(item, backups_dir):
            entries.append((item, item.stat().st_mtime))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='backups/ 备份保留策略清理（默认 dry-run）')
    parser.add_argument('--execute', action='store_true', help='真正执行删除（默认只打印清单）')
    parser.add_argument('--keep-days', type=int, default=KEEP_DAYS, help=f'保留最近 N 天（默认 {KEEP_DAYS}）')
    parser.add_argument('--keep-min-count', type=int, default=KEEP_MIN_COUNT, help=f'至少保留最新 N 份（默认 {KEEP_MIN_COUNT}）')
    args = parser.parse_args(argv)

    candidates = collect_candidates()
    now_ts = time.time()
    expired = select_expired(candidates, now_ts, args.keep_days, args.keep_min_count)

    # 红线断言：任何待删路径绝不允许是主库本体，且必须位于 backups 目录内
    for path in expired:
        resolved = path.resolve()
        assert resolved != DB_PATH.resolve(), f'安全锁触发：拒绝删除主库 {resolved}'
        assert BACKUPS_DIR.resolve() in resolved.parents, f'安全锁触发：待删文件不在 backups/ 内 {resolved}'

    print(f'备份候选共 {len(candidates)} 份（保留最近 {args.keep_days} 天 且 至少最新 {args.keep_min_count} 份）')
    if not expired:
        print('[OK] 没有需要清理的备份。')
        return 0

    print(f'待删除 {len(expired)} 份：')
    for path in expired:
        age_days = (now_ts - dict(candidates)[path]) / 86400
        print(f'  - {path.name}  （{age_days:.0f} 天前）')

    if not args.execute:
        print('[dry-run] 未做任何删除。确认无误后追加 --execute 执行。')
        return 0

    deleted = 0
    for path in expired:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            print(f'  [失败] {path.name}: {exc}', file=sys.stderr)
    print(f'[OK] 已删除 {deleted}/{len(expired)} 份备份。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
