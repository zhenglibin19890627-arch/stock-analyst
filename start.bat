@echo off
chcp 65001 >nul 2>&1
title Stock Analyst 智能个股分析系统

REM ============================================================
REM Stock Analyst 标准化启动脚本 (Windows)
REM 功能：Python路径检测 → 依赖检查 → 端口检测释放 → 启动 → 健康检查
REM ============================================================

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo.
echo ============================================================
echo   Stock Analyst 智能个股分析与评级系统 - 启动脚本
echo ============================================================
echo.

REM --- Step 1: Python 路径检测 ---
echo [1/5] 检测 Python 环境...

REM 优先使用 Python312（项目标准环境）
set "PYTHON_EXE=C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe"

if exist "%PYTHON_EXE%" (
    echo   ✓ 使用 Python312: %PYTHON_EXE%
) else (
    REM 回退：尝试系统 PATH 中的 python
    where python >nul 2>&1
    if %errorlevel%==0 (
        for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
        echo   ! 使用系统Python: %PYTHON_EXE%
    ) else (
        echo   X 未找到 Python，请安装 Python 3.12+ 并添加到 PATH
        echo   X 启动失败，错误码: 1
        echo.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" --version 2>nul | findstr /R "3\.\(1[2-9]\|[2-9][0-9]\)" >nul
if %errorlevel% neq 0 (
    echo   ! 警告: Python 版本可能低于 3.12，建议升级
)

REM --- Step 2: 依赖检查 ---
echo.
echo [2/5] 检查关键依赖...

set "DEPS_MISSING=0"
REM 016: deps check aligned with requirements.txt (9 pkgs, dateutil=python-dateutil)
for %%D in (flask pydantic requests akshare pandas numpy dateutil openpyxl pytest) do (
    "%PYTHON_EXE%" -c "import %%D" >nul 2>&1
    if errorlevel 1 (
        echo   X 缺失依赖: %%D
        set "DEPS_MISSING=1"
    ) else (
        echo   ✓ 已安装: %%D
    )
)

if "%DEPS_MISSING%"=="1" (
    echo.
    echo   正在自动安装缺失依赖...
    "%PYTHON_EXE%" -m pip install -r requirements.txt -q
    if %errorlevel% neq 0 (
        echo   X 依赖安装失败，请手动执行: pip install -r requirements.txt
        echo   X 启动失败，错误码: 2
        pause
        exit /b 2
    )
    echo   ✓ 依赖安装完成
)

REM --- Step 3: 端口检测与释放 ---
echo.
echo [3/5] 检测端口 5000 占用...

set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    set "PORT_PID=%%a"
)

if defined PORT_PID (
    echo   ! 端口 5000 已被进程 PID=%PORT_PID% 占用
    echo   正在释放端口...
    taskkill /PID %PORT_PID% /F >nul 2>&1
    if %errorlevel%==0 (
        echo   ✓ 已终止进程 %PORT_PID%，端口已释放
        timeout /t 1 /nobreak >nul
    ) else (
        echo   X 无法终止进程，请手动执行: taskkill /PID %PORT_PID% /F
        echo   X 启动失败，错误码: 3
        pause
        exit /b 3
    )
) else (
    echo   ✓ 端口 5000 空闲
)

REM --- Step 4: 数据库初始化 ---
echo.
echo [4/5] 初始化数据库...

if not exist "%PROJECT_DIR%stock_analyst.db" (
    echo   首次运行，正在创建数据库...
)

REM --- Step 5: 启动服务 ---
echo.
echo [5/5] 启动 Flask 服务...
echo.

start "Stock Analyst Server" /MIN "%PYTHON_EXE%" "%PROJECT_DIR%app.py"

REM 等待服务启动（最多 10 秒）
echo   等待服务就绪...
set "WAIT_COUNT=0"
:WAIT_LOOP
set /a WAIT_COUNT+=1
timeout /t 1 /nobreak >nul

REM 使用 curl 或 PowerShell 进行健康检查
where curl >nul 2>&1
if %errorlevel%==0 (
    curl -s -o nul -w "%%{http_code}" http://127.0.0.1:5000/api/health > "%TEMP%\stock_health.txt" 2>&1
    set /p HEALTH_CODE=<"%TEMP%\stock_health.txt"
    del "%TEMP%\stock_health.txt" >nul 2>&1
) else (
    REM PowerShell 回退方案
    powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/api/health' -UseBasicParsing -TimeoutSec 3; Set-Content -Path '%TEMP%\stock_health.txt' -Value $r.StatusCode } catch { Set-Content -Path '%TEMP%\stock_health.txt' -Value '000' }" >nul 2>&1
    set /p HEALTH_CODE=<"%TEMP%\stock_health.txt"
    del "%TEMP%\stock_health.txt" >nul 2>&1
)

if "%HEALTH_CODE%"=="200" goto :HEALTH_OK
if %WAIT_COUNT% geq 10 goto :HEALTH_FAIL
goto :WAIT_LOOP

:HEALTH_OK
echo.
echo ============================================================
echo   [OK] 服务就绪，访问地址：http://127.0.0.1:5000
echo   v5.0 评分引擎演示：http://127.0.0.1:5000/api/v5/scoring-demo
echo   健康检查：          http://127.0.0.1:5000/api/health
echo ============================================================
echo.
echo   浏览器正在打开...
start "" http://127.0.0.1:5000
echo.
echo   按任意键可关闭此窗口（服务在后台继续运行）
pause >nul
exit /b 0

:HEALTH_FAIL
echo.
echo ============================================================
echo   [FAIL] 启动失败：服务在 10 秒内未就绪
echo   错误码: 4 (服务启动超时)
echo ============================================================
echo.
echo   排查步骤：
echo   1. 检查 Python 路径是否正确: %PYTHON_EXE%
echo   2. 手动运行诊断: "%PYTHON_EXE%" "%PROJECT_DIR%app.py"
echo   3. 检查端口 5000 是否被其他程序占用
echo.
pause
exit /b 4
