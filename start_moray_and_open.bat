@echo off
chcp 65001 >nul
title Moray One-Click Start

echo ========================================
echo   Moray 一键启动
echo ========================================
echo.

REM 启动后端（新窗口）
echo [1/2] 启动 Moray 后端...
start "Moray Backend" cmd /k "cd /d D:\MoRay && start_moray.bat"

echo   等待后端启动...
timeout /t 8 /nobreak >nul

REM 打开浏览器
echo [2/2] 打开浏览器...
start http://127.0.0.1:8000

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo   后端窗口不要关，关了就停了
echo.
pause
