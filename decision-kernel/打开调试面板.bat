@echo off
chcp 65001 >nul
title MoRay 决策内核 v1.0 可视化调试面板
cd /d "%~dp0"
echo ================================================================
echo   MoRay 决策内核 v1.0 可视化调试面板
echo   复用核心函数 encodeWorldState + makeDecision，零侵入
echo ================================================================
echo.
echo   启动本地调试服务（端口 18080），1 秒后自动打开浏览器...
echo   服务运行日志将保留在本窗口，关闭本窗口即停止服务。
echo.
start "" cmd /c "ping -n 2 127.0.0.1 >nul & start http://127.0.0.1:18080/debug-panel.html"
node debug-server.js
echo.
echo 服务已停止。
pause
