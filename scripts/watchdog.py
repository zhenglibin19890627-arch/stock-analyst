# -*- coding: utf-8 -*-
"""Stock Analyst 服务看门狗（Watchdog）

由 Windows 计划任务每 5 分钟调用一次（pythonw 静默运行）：
  1. 检查 127.0.0.1:5000 是否已有服务监听 → 有则直接退出（幂等）
  2. 无则以 pythonw 分离方式拉起 app.py（无窗口，随系统存续）

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


def main() -> int:
    if _service_running():
        return 0  # 服务正常，无需动作

    pyw = PYTHONW_EXE if os.path.exists(PYTHONW_EXE) else 'pythonw'
    flags = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    try:
        subprocess.Popen(
            [pyw, os.path.join(PROJECT_DIR, 'app.py')],
            cwd=PROJECT_DIR,
            creationflags=flags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return 0
    except OSError:
        return 1


if __name__ == '__main__':
    sys.exit(main())
