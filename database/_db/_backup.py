"""备份层：每日自动备份 + 破坏性操作前在线热备份 + 旧备份清理（红线 R11 载体）。"""

import logging
import os
import sqlite3
from datetime import datetime

_logger = logging.getLogger(__name__)


def auto_backup_db() -> str | None:
    """每日自动备份数据库（幂等：当日已有 auto 备份则跳过）。

    2026-09-07 新增（批判性审查：此前全库仅有人工备份，服务常驻期间磁盘损坏/
    误删/写坏将丢失全部数据）。使用 sqlite3 backup API（WAL 模式安全，含未
    checkpoint 数据），命名 db_backup_YYYYMMDD_auto.db —— 沿用 db_backup_*
    前缀，纳入 scripts/cleanup_backups.py 既有保留策略。
    调用点：app 启动 + 补采调度 tick（每 30 分钟，幂等代价为零）。
    返回备份路径；当日已存在返回 None。
    """
    # 021BO：可变配置在调用点经 facade 取值（monkeypatch 语义保持）
    from database.db_manager import BACKUP_DIR, DB_PATH

    today = datetime.now().strftime('%Y%m%d')
    target = os.path.join(BACKUP_DIR, f'db_backup_{today}_auto.db')
    if os.path.exists(target):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    _logger.info(f'[自动备份] 已创建当日备份: {target}')
    return target


def backup_database(reason='manual'):
    """在破坏性操作前自动创建带时间戳的数据库备份。

    采用 SQLite 在线 .backup() API（而非 shutil 文件复制），保证即使在 WAL 模式下
    且数据库正被写入时，也能获得事务一致的完整快照（无需手动处理 -wal/-shm 旁路文件）。
    不修改现有 WAL 模式与 busy_timeout 配置。

    本函数为“尽力而为”语义：备份失败时记录错误并返回 None，不抛异常，由调用方
    决定是否继续。对于真正不可逆的 DROP TABLE，调用方可检查返回值选择中止。

    Args:
        reason: 触发备份的原因标识（如 'drop_daily_reports'、'clear_price_backtest'），
                仅保留字母数字/下划线，写入文件名便于追溯。

    Returns:
        成功返回备份文件绝对路径；失败返回 None。
    """
    # 021BO：可变配置在调用点经 facade 取值（monkeypatch 语义保持）
    from database.db_manager import BACKUP_DIR, DB_PATH, MAX_BACKUPS

    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # 清理 reason 中不安全的字符，避免破坏文件名
        safe_reason = ''.join(
            c if (c.isalnum() or c in '-_') else '_' for c in str(reason)
        )[:40]
        backup_name = f'db_backup_{timestamp}_{safe_reason}.db'
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        # SQLite 在线热备份：源库无需关闭，自动处理 WAL，输出为完整一致的单文件
        source = sqlite3.connect(DB_PATH)
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()

        # 保留最近 MAX_BACKUPS 份，清理更早的备份
        _prune_old_backups(keep=MAX_BACKUPS)

        msg = f'[备份] 破坏性操作前已创建数据库备份: {backup_name} (原因: {reason})'
        print(msg)
        _logger.info(msg)
        return backup_path
    except Exception as e:
        err = f'[备份警告] 数据库备份失败，破坏性操作前未能生成备份: {e}'
        print(err)
        _logger.error(err)
        return None


def _prune_old_backups(keep):
    """保留最近 keep 份备份，按修改时间删除更早的。

    仅清理由 backup_database 生成的 db_backup_*.db 文件，不影响目录内其他文件。
    """
    # 021BO：BACKUP_DIR 在调用点经 facade 取值
    from database.db_manager import BACKUP_DIR

    try:
        backups = [
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.startswith('db_backup_') and f.endswith('.db')
        ]
        if len(backups) <= keep:
            return
        # 按修改时间排序，最旧的在前
        backups.sort(key=lambda p: os.path.getmtime(p))
        for old_path in backups[: len(backups) - keep]:
            try:
                os.remove(old_path)
                _logger.info(f'[备份] 清理旧备份: {os.path.basename(old_path)}')
            except OSError:
                pass
    except Exception as e:
        _logger.warning(f'[备份] 清理旧备份时出错: {e}')
