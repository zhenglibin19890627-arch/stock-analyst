@echo off
REM ============================================================
REM Stock Analyst 静默启动脚本（供计划任务/服务管理调用，无交互）
REM 逻辑：端口占用则退出(已有服务在跑) → 定位Python → 前台运行 app.py
REM 注意：必须前台运行（不 start /MIN），计划任务保持"运行中"状态，
REM       才能用 schtasks /End 或本脚本 stop 正常终止。
REM ============================================================

set "PROJECT_DIR=%~dp0..\"
cd /d "%PROJECT_DIR%"

REM --- 端口 5000 已被占用 → 已有服务在运行，直接退出 ---
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    exit /b 0
)

REM --- Python 路径检测（与 start.bat 一致）---
set "PYTHON_EXE=C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>&1
    if %errorlevel%==0 (
        for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    ) else (
        exit /b 1
    )
)

REM --- 前台启动 Flask 服务（日志由 app.py 写入 logs/app.log）---
REM 用 pythonw.exe（无控制台）启动，服务不依赖父进程/控制台生命周期
set "PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%"
if exist "%PYTHONW_EXE%" (
    "%PYTHONW_EXE%" "%PROJECT_DIR%app.py"
) else (
    "%PYTHON_EXE%" "%PROJECT_DIR%app.py"
)
exit /b 0
