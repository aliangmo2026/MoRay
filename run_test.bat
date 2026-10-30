@echo off
chcp 65001 >nul
title MoRay 安全测试
echo ============================================
echo    MoRay 安全机制自动化测试
echo ============================================
echo.

cd /d "%~dp0"

REM 使用项目的虚拟环境 Python（如果存在）
if exist "server\.venv\Scripts\python.exe" (
    echo 使用虚拟环境 Python...
    "server\.venv\Scripts\python.exe" test_security.py
) else (
    echo 使用系统 Python...
    python test_security.py
)

echo.
echo ============================================
echo 测试完成，按任意键退出...
pause >nul
