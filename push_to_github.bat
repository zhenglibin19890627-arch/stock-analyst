@echo off
chcp 65001 >nul 2>&1
title Stock Analyst - 推送到 GitHub
cd /d "%~dp0"

echo ============================================
echo   Stock Analyst - 推送到 GitHub
echo ============================================
echo.

git push -u origin master

echo.
echo ============================================
if %errorlevel%==0 (
    echo   [OK] 推送完成
) else (
    echo   [X] 推送失败：首次推送请在弹窗中完成 GitHub 登录授权；
    echo       若已授权仍失败，请检查网络或手动执行 git push
)
echo ============================================
pause
