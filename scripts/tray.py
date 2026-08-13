"""Stock Analyst 系统托盘程序（pystray）

功能：
- 托盘图标（绿=运行中 / 灰=已停止，打开菜单时自动刷新）
- 菜单：打开系统 / 启动服务 / 停止服务 / 重启服务 / 服务状态 / 退出
- 启动时若服务未运行则自动拉起

运行方式（无窗口）：pythonw.exe scripts\tray.py
依赖：pystray, Pillow（见 requirements.txt）
"""

import os
import socket
import subprocess
import threading

from PIL import Image, ImageDraw

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
MANAGE_BAT = os.path.join(SCRIPTS_DIR, 'manage_service.bat')
SYSTEM_URL = 'http://127.0.0.1:5000'
PORT = 5000


def _service_running():
    """检查本地服务是否可访问"""
    try:
        s = socket.create_connection(('127.0.0.1', PORT), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def _make_icon(running):
    """生成托盘图标：绿=运行中 / 灰=已停止 + 白色上升折线"""
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (39, 174, 96, 255) if running else (150, 150, 150, 255)
    d.ellipse([4, 4, 60, 60], fill=color)
    # 白色上升折线（股票走势象征）
    d.line([(16, 44), (27, 33), (35, 40), (48, 22)], fill=(255, 255, 255, 255), width=5)
    return img


def _run_manage(cmd):
    """后台调用管理脚本（无窗口）"""
    subprocess.Popen(
        ['cmd', '/c', 'manage_service.bat', cmd],
        cwd=SCRIPTS_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _open_system(icon=None, item=None):
    subprocess.Popen(
        ['cmd', '/c', 'start', '', SYSTEM_URL],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main():
    import pystray

    def _refresh_icon_loop(icon):
        """后台线程：每 5 秒检查服务状态，刷新托盘图标颜色（绿=运行/灰=停止）"""
        last = None
        while True:
            try:
                running = _service_running()
                if running != last:
                    icon.icon = _make_icon(running)
                    last = running
            except Exception:
                pass
            import time

            time.sleep(5)

    # 启动时若服务未运行，自动拉起
    if not _service_running():
        _run_manage('start')

    icon = pystray.Icon(
        'StockAnalyst',
        _make_icon(_service_running()),
        'Stock Analyst 智能分析系统',
        menu=pystray.Menu(
            pystray.MenuItem('打开系统', _open_system),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                '启动服务',
                lambda i, it: _run_manage('start'),
                enabled=lambda item: not _service_running(),
            ),
            pystray.MenuItem(
                '停止服务',
                lambda i, it: _run_manage('stop'),
                enabled=lambda item: _service_running(),
            ),
            pystray.MenuItem(
                '重启服务',
                lambda i, it: _run_manage('restart'),
                enabled=lambda item: _service_running(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: (
                    '状态：运行中 (127.0.0.1:5000)'
                    if _service_running()
                    else '状态：已停止'
                ),
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出托盘', lambda i, it: i.stop()),
        ),
    )

    threading.Thread(target=_refresh_icon_loop, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == '__main__':
    main()
