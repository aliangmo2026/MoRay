@echo off
rem ============================================================
rem  MoRay one-click launcher (Windows)
rem    1) locate Python (3.10+) and prepare the venv server\.venv
rem    2) make sure dependencies are installed (root requirements.txt)
rem    3) check the port, then start uvicorn in its own window
rem    4) poll http://127.0.0.1:<port>/api/health (max 60s) and open the browser
rem
rem  Port: default 8000. Override with:  set MORAY_PORT=8123  (then run this file)
rem  Set MORAY_NO_BROWSER=1 to skip opening the browser (for scripts / tests).
rem
rem  NOTE: every message below is English on purpose -- cmd.exe codepage
rem  differences (chcp 65001 vs GBK) are what produced garbled text before.
rem ============================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
set "VENV=%ROOT%server\.venv"
set "PYVENV=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%requirements.txt"

rem ---- re-entry point: run the backend in the foreground of its own window ----
if /i "%~1"=="--backend" goto :run_backend

cd /d "%ROOT%"

if "%MORAY_PORT%"=="" set "MORAY_PORT=8000"
echo %MORAY_PORT%|findstr /r "^[0-9][0-9]*$" >nul 2>&1
if errorlevel 1 (
  echo [MoRay] ERROR: invalid MORAY_PORT "%MORAY_PORT%" - it must be a port number, e.g. 8000
  echo         Example:  set MORAY_PORT=8123   then run this launcher again
  pause
  exit /b 1
)
set "PORT=%MORAY_PORT%"
set "URL=http://127.0.0.1:%PORT%/"
set "HEALTH=%URL%api/health"

echo ============================================================
echo   MoRay launcher        port: %PORT%
echo   app:  %URL%
echo ============================================================
echo.

rem ---- 1. locate Python (python, then py -3); reject the Store stub ----
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo [MoRay] ERROR: Python was not found in PATH.
  echo         Fix: install Python 3.10 or newer from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [MoRay] ERROR: Python 3.10 or newer is required by this backend.
  %PY% -c "import sys; print('        found: ' + sys.version.split()[0])"
  echo         Fix: install a newer Python from https://www.python.org/downloads/ and retry.
  pause
  exit /b 1
)

rem ---- 2. virtual environment (created on first run; kept afterwards) ----
if not exist "%PYVENV%" (
  echo [MoRay] First run: creating the virtual environment server\.venv ...
  %PY% -m venv "%VENV%"
  if not exist "%PYVENV%" (
    echo.
    echo [MoRay] ERROR: could not create the virtual environment in server\.venv
    echo         Fix: make sure the "venv" module is available ^(repair/reinstall Python^),
    echo         and that this folder is writable ^(avoid Program Files / OneDrive-synced paths^).
    pause
    exit /b 1
  )
)

rem ---- 3. dependencies: import check, auto install from requirements.txt ----
"%PYVENV%" -c "import fastapi, uvicorn, httpx" >nul 2>&1
if errorlevel 1 (
  echo [MoRay] Installing backend dependencies ^(first run can take 1-2 minutes^)...
  "%PYVENV%" -m pip install --disable-pip-version-check --timeout 30 --retries 1 -r "%REQ%"
  if errorlevel 1 (
    echo.
    echo [MoRay] PyPI unreachable or install failed - retrying with the Aliyun mirror...
    "%PYVENV%" -m pip install --disable-pip-version-check --timeout 30 --retries 1 -r "%REQ%" -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 goto :dep_error
  )
  "%PYVENV%" -c "import fastapi, uvicorn, httpx" >nul 2>&1
  if errorlevel 1 goto :dep_error
  echo [MoRay] Dependencies installed.
)

rem ---- 4. port check: a free port proceeds, an existing MoRay backend is reused,
rem         anything else on that port is an error (clear message, no traceback)
call :port_busy
if errorlevel 1 goto :port_free
call :probe_health
if not errorlevel 1 (
  echo [MoRay] A MoRay backend is already running on port %PORT% - reusing it.
  echo [MoRay] Opening %URL%
  call :open_browser
  exit /b 0
)
echo.
echo [MoRay] ERROR: port %PORT% is already in use by another program.
echo         Fix: close this window, then start again on a free port:
echo              set MORAY_PORT=8123
echo              ^(then double-click this launcher again^)
echo         Tip: find the owner with:  netstat -ano ^| findstr :%PORT%
pause
exit /b 1
:port_free

rem ---- 5. start the backend in its own window (log stays visible) ----
echo [MoRay] Starting the backend on port %PORT% ...
start "MoRay Backend" cmd /k ""%~f0" --backend %PORT%"

rem ---- 6. wait for health (max 60s), then open the browser ----
echo [MoRay] Waiting for %HEALTH% ...
set /a TRIES=0
:wait_loop
set /a TRIES+=1
if %TRIES% gtr 60 goto :timeout_error
call :probe_health
if not errorlevel 1 goto :ready
ping -n 2 127.0.0.1 >nul
goto :wait_loop

:ready
echo.
echo [MoRay] Backend is healthy - opening %URL%
call :open_browser
echo [MoRay] MoRay is running. Close the "MoRay Backend" window (or press Ctrl+C there) to stop it.
ping -n 4 127.0.0.1 >nul
exit /b 0

:timeout_error
echo.
echo [MoRay] ERROR: the backend did not become healthy within 60 seconds.
echo         Checks:
echo           1^) read the "MoRay Backend" window - the Python traceback is printed there
echo           2^) port %PORT% may be blocked: set MORAY_PORT=8123, then run this launcher again
echo           3^) run scripts\start_backend.bat directly to see the same server output
echo           4^) first run on a slow network can take longer than a minute to install packages
pause
exit /b 1

:dep_error
echo.
echo [MoRay] ERROR: dependency installation failed.
echo         Checks:
echo           1^) internet access / proxy / firewall - both PyPI and the Aliyun mirror failed
echo           2^) retry manually:  "%PYVENV%" -m pip install -r "%REQ%"
echo           3^) Python 3.10+ is required:  %PY% --version
pause
exit /b 1

rem ============================================================
rem  helpers
rem ============================================================

rem :port_busy  -> errorlevel 0 when something is LISTENING on %PORT%, else 1
:port_busy
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
exit /b %errorlevel%

rem :probe_health -> errorlevel 0 when GET /api/health answers 200, else 1
:probe_health
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%HEALTH%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
exit /b %errorlevel%

rem :open_browser -> open %URL% unless MORAY_NO_BROWSER=1
:open_browser
if "%MORAY_NO_BROWSER%"=="1" (
  echo [MoRay] MORAY_NO_BROWSER=1 - skipping the browser. Open %URL% manually.
  exit /b 0
)
start "" "%URL%"
exit /b 0

rem ---- backend foreground (own window): uvicorn serves the app and the static UI ----
:run_backend
set "MORAY_PORT=%~2"
cd /d "%ROOT%"
echo [MoRay] Backend: uvicorn server.app.main:app on 127.0.0.1:%MORAY_PORT% ^(Ctrl+C stops it^)
echo.
"%PYVENV%" -m uvicorn server.app.main:app --host 127.0.0.1 --port %MORAY_PORT%
echo.
echo [MoRay] Backend stopped. Press any key to close this window.
pause >nul
exit /b 0
