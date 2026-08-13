@echo off
REM ============================================================
REM Stock Analyst 服务管理脚本（开机自启 + 手动控制）
REM
REM 用法:
REM   manage_service.bat install    注册开机自启（登录时自动启动）并立即启动
REM   manage_service.bat start      立即启动服务
REM   manage_service.bat stop       停止服务
REM   manage_service.bat restart    重启服务
REM   manage_service.bat status     查看服务状态
REM   manage_service.bat uninstall  停止服务并取消开机自启
REM
REM 原理：启动文件夹 BAT（登录时自动启动，无需管理员权限）
REM       + PowerShell 隐藏窗口静默启动服务，系统原生，无需第三方软件。
REM ============================================================

set "TASK_NAME=StockAnalyst"
set "SCRIPTS_DIR=%~dp0"
set "PYTHON_EXE=C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
    where python >nul 2>&1
    if %errorlevel%==0 (
        for /f "delims=" %%i in ('where python') do set "PYTHON_EXE=%%i"
    ) else (
        echo X 未找到 Python，无法使用服务管理
        exit /b 1
    )
)

if "%~1"=="" goto usage
if /i "%~1"=="install" goto install
if /i "%~1"=="uninstall" goto uninstall
if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
if /i "%~1"=="restart" goto restart
if /i "%~1"=="status" goto status
if /i "%~1"=="tray" goto tray
goto usage

:usage
echo Stock Analyst 服务管理
echo.
echo 用法: manage_service.bat [install^|uninstall^|start^|stop^|restart^|status]
echo.
echo   install    注册开机自启（登录时自动启动）并立即启动
echo   start      立即启动服务
echo   stop       停止服务
echo   restart    重启服务（stop + start）
echo   status     查看服务状态
echo   uninstall  停止服务并取消开机自启
echo   tray       启动系统托盘图标（服务状态/启停控制）
echo.
exit /b 0

:install
echo [1/2] 注册开机自启（登录时自动启动）...
"%PYTHON_EXE%" "%SCRIPTS_DIR%service_install.py" install
if %errorlevel% neq 0 (
    echo   X 注册失败
    exit /b 1
)
echo.
echo [2/2] 立即启动服务...
call :start
echo.
echo ============================================================
echo   完成！下次开机登录后服务将自动启动（无需手动操作）
echo   管理命令: manage_service.bat stop ^| start ^| restart ^| uninstall ^| status
echo ============================================================
exit /b 0

:start
echo [启动] 正在启动 Stock Analyst 服务...
powershell -NoProfile -Command "Start-Process -FilePath '%SCRIPTS_DIR%service_start.bat' -WindowStyle Hidden"
REM 等待服务就绪（最多 15 秒）
set "WAIT_COUNT=0"
:WAIT_LOOP
set /a WAIT_COUNT+=1
timeout /t 1 /nobreak >nul
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:5000/api/health > "%TEMP%\stock_health.txt" 2>&1
set /p HEALTH_CODE=<"%TEMP%\stock_health.txt"
del "%TEMP%\stock_health.txt" >nul 2>&1
if "%HEALTH_CODE%"=="200" (
    echo   [OK] 服务已就绪: http://127.0.0.1:5000
    exit /b 0
)
if %WAIT_COUNT% geq 15 (
    echo   X 启动超时（15秒未就绪），请查看 logs\app.log
    exit /b 4
)
goto :WAIT_LOOP

:stop
echo [停止] 正在停止 Stock Analyst 服务...
REM 按端口 5000 终止监听进程（确保彻底停止）
set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do set "PORT_PID=%%a"
if "%PORT_PID%"=="" (
    echo   [OK] 服务未在运行
    exit /b 0
)
taskkill /PID %PORT_PID% /F >nul 2>&1
if %errorlevel%==0 (
    echo   [OK] 服务已停止 ^(PID %PORT_PID%^)
) else (
    echo   X 无法终止进程 PID %PORT_PID%，请手动执行: taskkill /PID %PORT_PID% /F
    exit /b 1
)
exit /b 0

:restart
call :stop
echo.
call :start
exit /b 0

:status
echo [状态] Stock Analyst 服务状态:
"%PYTHON_EXE%" "%SCRIPTS_DIR%service_install.py" status
set "PORT_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do set "PORT_PID=%%a"
if "%PORT_PID%"=="" (
    echo   - 服务进程: 未运行
    exit /b 0
)
echo   - 服务进程: 运行中 ^(PID %PORT_PID%^)
curl -s http://127.0.0.1:5000/api/health
echo.
exit /b 0

:tray
echo [托盘] 正在启动系统托盘图标...
set "PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%"
if exist "%PYTHONW_EXE%" (
    start "" "%PYTHONW_EXE%" "%SCRIPTS_DIR%tray.py"
) else (
    start "" "%PYTHON_EXE%" "%SCRIPTS_DIR%tray.py"
)
echo   [OK] 托盘已启动（右下角通知区域）
exit /b 0

:uninstall
echo [卸载] 正在停止服务并取消开机自启...
call :stop
"%PYTHON_EXE%" "%SCRIPTS_DIR%service_install.py" uninstall
exit /b 0
