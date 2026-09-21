"""连接层：WAL + busy_timeout=10s + foreign_keys=OFF（红线 R12 配置锚点）。"""

import sqlite3


def get_connection():
    """
    连接数据库，如果数据库文件不存在会自动创建。
    WAL模式：允许多个进程同时读取，写入时不阻塞读取。
    busy_timeout：遇到锁时等待10秒而不是立刻报错。
    """
    # 021BO：DB_PATH 在调用点经 facade 取值（测试 monkeypatch 语义保持）
    from database.db_manager import DB_PATH

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # 查询结果可以用列名访问
    conn.execute('PRAGMA journal_mode=WAL')  # WAL模式：大幅减少锁冲突
    conn.execute('PRAGMA busy_timeout=10000')  # 锁等待10秒
    conn.execute('PRAGMA foreign_keys=OFF')  # 关闭外键约束（应用层手动管理级联逻辑）
    return conn
