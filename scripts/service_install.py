"""Stock Analyst 开机自启注册/注销（启动文件夹方式，无需管理员权限）

用法:
  python service_install.py install    在用户启动文件夹创建隐藏启动器
  python service_install.py uninstall  删除启动文件夹中的启动器
  python service_install.py status     输出自启状态

原理：Windows 启动文件夹（shell:startup）中的 .bat 在用户登录时自动执行，
脚本内部调用 PowerShell Start-Process -WindowStyle Hidden 隐藏启动服务，
等效于"登录即启动"，且无需管理员权限。
"""

import os
import sys

ENTRY_NAME = 'StockAnalystAutostart.bat'


def _startup_dir():
    return os.path.join(
        os.environ.get('APPDATA', ''),
        r'Microsoft\Windows\Start Menu\Programs\Startup',
    )


def _entry_path():
    return os.path.join(_startup_dir(), ENTRY_NAME)


def install():
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    bat_path = os.path.join(scripts_dir, 'service_start.bat')
    if not os.path.exists(bat_path):
        print('[FAIL] 未找到 service_start.bat:', bat_path)
        return 1
    # 登录时启动：服务（隐藏）+ 系统托盘图标（复用 manage_service.bat 的 Python 检测）
    content = (
        '@echo off\r\n'
        'call "%~dp0manage_service.bat" start\r\n'
        'call "%~dp0manage_service.bat" tray\r\n'
        'exit /b 0\r\n'
    )
    target = _entry_path()
    with open(target, 'w', encoding='ascii') as f:
        f.write(content)
    print('[OK] 开机自启已注册（登录时自动启动服务 + 托盘图标）:', target)
    return 0


def uninstall():
    target = _entry_path()
    if os.path.exists(target):
        os.remove(target)
        print('[OK] 已取消开机自启:', target)
    else:
        print('! 未找到自启项（可能尚未注册）')
    return 0


def status():
    target = _entry_path()
    print('  - 开机自启:', '已注册（登录时自动启动）' if os.path.exists(target) else '未注册')
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'install':
        return install()
    if cmd == 'uninstall':
        return uninstall()
    return status()


if __name__ == '__main__':
    sys.exit(main())
