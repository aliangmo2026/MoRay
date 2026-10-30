@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MoRay Backup

for /f "tokens=1-3 delims=/-. " %%a in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy MM dd'"') do (
    set YYYY=%%a
    set MM=%%b
    set DD=%%c
)
set FOLDER=backup\day_%YYYY%%MM%%DD%

echo ========================================
echo   MoRay Backup
echo ========================================
echo   Backup dir: %CD%\%FOLDER%
echo.

if not exist "%FOLDER%" mkdir "%FOLDER%"
echo [1/3] Backup dir created

set FILES=^
server\app\db.py ^
server\app\crud.py ^
server\app\agent_tools.py ^
server\app\main.py ^
server\app\mcp_server.py ^
server\app\agent_runner.py ^
server\app\agent_orchestrator.py ^
tests\test_session_state.py ^
tests\test_event_stream.py ^
tests\test_mvp_e2e.py

echo.
echo [2/3] Copying files...
set COUNT=0
set SKIP=0

for %%f in (%FILES%) do (
    if exist "%%f" (
        xcopy "%%f" "%FOLDER%\%%f\" /Y /Q >nul 2>&1
        if errorlevel 1 (
            copy "%%f" "%FOLDER%\%%~nxf" /Y >nul 2>&1
        )
        echo   OK: %%f
        set /a COUNT+=1
    ) else (
        echo   SKIP: %%f
        set /a SKIP+=1
    )
)

echo.
echo [3/3] Done
echo.
echo ========================================
echo   Backed up: %COUNT% files
echo   Skipped: %SKIP% files
echo   Path: %CD%\%FOLDER%
echo ========================================
echo.
pause
