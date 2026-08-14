"""Stock Analyst 服务看门狗（Watchdog）

由 Windows 计划任务每分钟调用一次（pythonw 静默运行）：
  1. 检查 127.0.0.1:5000 是否已有服务监听 → 无则以 pythonw 分离方式拉起 app.py
  2. 确保托盘图标进程存活（tray.py 内含会话互斥体，重复拉起安全）

设计要点：
  - 全程无控制台窗口，不会误关
  - 分离启动（DETACHED_PROCESS），子进程不随本脚本退出而终止
  - 端口守卫防止与 start.bat / 托盘 / 登录自启并发启动产生双实例
"""
import os
import socket
import subprocess
import sys

PORT = 5000
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPTS_DIR)
PYTHONW_EXE = r'C:\Users\zlb19\AppData\Local\Programs\Python\Python312\pythonw.exe'


def _service_running() -> bool:
    try:
        s = socket.create_connection(('127.0.0.1', PORT), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _spawn_detached(args):
    """pythonw 分离式静默拉起（无窗口，不随本进程退出）。"""
    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    try:
        subprocess.Popen(
            args,
            cwd=PROJECT_DIR,
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _ensure_tray():
    """确保托盘图标进程存活（tray.py 内含会话互斥体，已运行时新实例秒退）。"""
    pyw = PYTHONW_EXE if os.path.exists(PYTHONW_EXE) else 'pythonw'
    _spawn_detached([pyw, os.path.join(SCRIPTS_DIR, 'tray.py')])


def main() -> int:
    if not _service_running():
        pyw = PYTHONW_EXE if os.path.exists(PYTHONW_EXE) else 'pythonw'
        _spawn_detached([pyw, os.path.join(PROJECT_DIR, 'app.py')])
    _ensure_tray()
    return 0


if __name__ == '__main__':
    sys.exit(main())
