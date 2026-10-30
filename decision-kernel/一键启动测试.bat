@echo off
chcp 65001 >nul
title MoRay 决策内核 v1.0 最小闭环测试
cd /d "%~dp0"
echo ================================================================
echo   MoRay 决策内核 v1.0 最小闭环测试
echo   （纯 Node.js 本地运行，零外部依赖，无网络调用）
echo ================================================================
echo.
node run-poc.js
echo.
pause
