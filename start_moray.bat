@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Moray Backend

if exist "server\.venv\Scripts\python.exe" (
    echo Starting Moray backend on port 8000...
    server\.venv\Scripts\python.exe -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
) else (
    echo ERROR: server\.venv not found
    echo Please run Æô¶¯MoRay.bat first to set up the environment
    pause
    exit /b 1
)

pause
