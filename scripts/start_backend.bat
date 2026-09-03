@echo off
rem ============================================================
rem  MoRay 本地后端一键启动（Windows）
rem  自动：找 Python -> 建 venv -> 装依赖(官方源失败回退阿里镜像)
rem        -> 端口占用检测 -> uvicorn 启动 -> 自动打开健康检查
rem  端口：默认 8000，换端口用： set MORAY_PORT=8123 && 本脚本
rem ============================================================
cd /d "%~dp0.."

set "VENV=%~dp0..\server\.venv"
if "%MORAY_PORT%"=="" set "MORAY_PORT=8000"

rem ---- 1. 找 Python（优先 python，其次 py -3）----
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo [MoRay] 未检测到 Python 3.11+，请先安装：https://www.python.org/downloads/
  pause
  exit /b 1
)

rem ---- 2. 虚拟环境（不存在才创建）----
if not exist "%VENV%\Scripts\python.exe" (
  echo [MoRay] 首次运行：创建虚拟环境 server\.venv ...
  %PY% -m venv "%VENV%" || goto :err
)

rem ---- 3. 安装依赖（官方源失败自动回退阿里云镜像；已装则秒级跳过）----
echo [MoRay] 安装依赖（fastapi, uvicorn[standard]）...
"%VENV%\Scripts\python.exe" -m pip install -q --timeout 30 --retries 1 -r server\requirements.txt >nul 2>nul
if errorlevel 1 (
  echo [MoRay] 官方源安装失败，回退阿里云镜像重试...
  "%VENV%\Scripts\python.exe" -m pip install -q --timeout 30 --retries 1 -r server\requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ >nul 2>nul || goto :err
)

rem ---- 4. 端口占用检测（被占用给出友好提示而非堆栈）----
netstat -ano | findstr ":%MORAY_PORT%" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [MoRay] 端口 %MORAY_PORT% 已被占用。
  echo [MoRay] 请换端口后重试： set MORAY_PORT=8123  然后重新运行本脚本。
  pause
  exit /b 1
)

rem ---- 5. 启动 uvicorn（窗口保持；延迟 2 秒自动打开健康检查）----
echo.
echo [MoRay] 后端地址：  http://127.0.0.1:%MORAY_PORT%
echo [MoRay] 健康检查：  http://127.0.0.1:%MORAY_PORT%/api/health
echo [MoRay] MoRay 后端运行中，关闭此窗口即停止服务（Ctrl+C 可正常停止）。
echo.
start "" /min "%VENV%\Scripts\python.exe" -c "import time,webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:%MORAY_PORT%/api/health')"
cd server
"%VENV%\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %MORAY_PORT%
echo.
echo [MoRay] 服务已停止。窗口即将关闭。
ping -n 4 127.0.0.1 >nul
goto :eof

:err
echo.
echo [MoRay] 启动失败：请确认 Python 3.11+ 已安装、网络可用后重试。
pause
