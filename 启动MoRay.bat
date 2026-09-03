@echo off
rem ============================================================
rem  MoRay 一键启动（Windows）
rem  1) 独立窗口启动本地后端（scripts\start_backend.bat）
rem  2) 轮询 /api/health 就绪后，自动用默认浏览器打开应用
rem  端口：默认 8000，换端口用： set MORAY_PORT=8123 && 启动MoRay.bat
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if "%MORAY_PORT%"=="" set MORAY_PORT=8000

echo [MoRay] 正在启动本地后端（端口 %MORAY_PORT%）...
start "MoRay后端" cmd /c "%~dp0scripts\start_backend.bat"

echo [MoRay] 等待后端就绪...
set /a N=0
:wait_loop
set /a N+=1
if %N% gtr 60 (
    echo [MoRay] 后端 60 秒内未就绪，请查看后端窗口输出。
    pause
    exit /b 1
)
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://127.0.0.1:%MORAY_PORT%/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    ping -n 2 127.0.0.1 >nul
    goto :wait_loop
)

echo [MoRay] 后端就绪，正在打开应用：http://127.0.0.1:%MORAY_PORT%/
start "" "http://127.0.0.1:%MORAY_PORT%/"
echo [MoRay] 应用已打开。关闭后端窗口即停止服务。
ping -n 4 127.0.0.1 >nul
