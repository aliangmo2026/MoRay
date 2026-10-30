@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
REM ##########################################################
REM MoRay x ZCode isolation acceptance script (Windows native, hard gate / design doc section f)
REM Semantically equivalent to verify_isolation.sh; any CHECK change must be synced to both sides.
REM NOTE (Day 12): wording converted from Chinese to ASCII to fix C5 capture-encoding defect.
REM      Check logic is UNCHANGED, so the .sh twin needs no sync for this revision.
REM      All halfwidth parentheses were removed from echo text on purpose:
REM      inside if-blocks a literal ")" closes the block early and breaks the script.
REM NOTE (Day 12, C4/C5 capture fix): TRUE root cause of the C5 defect was NOT encoding --
REM      cmd's "for /f" command parser splits arguments at "=", mangling "--tmpfs /tmp:size=512m"
REM      into a bogus image pull ("512m:latest"), so the captured stdout was always empty.
REM      C4 used the same for /f + FLAGS pattern and silently false-passed for the same reason.
REM      Both now capture via direct redirect to a temp file + set /p. Check semantics unchanged
REM      (C4: any .env path found => FAIL; C5: uid must equal 1000), so no .sh sync needed.
REM
REM NOTE (Day 12-13, architect-approved sidecar proxy): check B target changed from
REM      host.docker.internal:8000 to the gateway sidecar moray-gw-proxy:8000.
REM      Root cause: internal network has no route to the host at all (Day 12 evidence chain),
REM      so the sidecar proxies moray-zcode-net:8000 -> host 127.0.0.1:8000 via vpnkit on a
REM      non-internal bridge. The sidecar is defined in docker-compose.yml and auto-started
REM      here if not running. Check semantics unchanged: gateway must be reachable from the
REM      agent container. This change IS synced to verify_isolation.sh.
REM
REM Three groups: A) direct public internet must FAIL  B) gateway must SUCCEED
REM               C) no host files outside sandbox (read-only rootfs / no sock / no .env / non-root)
REM Any single failure -> exit /b 1 -> ZCodeAdapter must NOT accept tasks, never run bare (f.4).
REM
REM Run (cmd, this directory): verify_isolation.bat
REM Prereq: Docker Desktop running; MoRay backend on host port 8000; image built.
REM ##########################################################

set "IMAGE=moray/zcode-agent:0.16.9"
if defined ZCODE_IMAGE set "IMAGE=%ZCODE_IMAGE%"
set "NET=moray-zcode-net"
set "SANDBOX=D:\MoRay\workspace\zcode_sandbox"
if defined ZCODE_SANDBOX set "SANDBOX=%ZCODE_SANDBOX%"
set "GATEWAY=http://moray-gw-proxy:8000/api/health"
set "PROXY=moray-gw-proxy"
set "FAILS=0"

REM Security flags identical to production runs (design doc section f)
set "FLAGS=--rm --network %NET% --add-host host.docker.internal:host-gateway --read-only --tmpfs /tmp:size=512m --cap-drop=ALL --security-opt no-new-privileges --user node"

echo === 0/3 preflight ===
docker info >nul 2>&1
if errorlevel 1 (
  echo [FATAL] Docker daemon unreachable -- not installed or not running.
  echo         f.1 fallback: prompt user to install/start Docker Desktop,
  echo         upper layer re-dispatches to LocalAdapter for file-only tasks, never run ZCode bare on host.
  exit /b 1
)
echo [PASS] docker daemon reachable

docker image inspect "%IMAGE%" >nul 2>&1
if errorlevel 1 (
  echo [FATAL] Image %IMAGE% not found. Build it first per README: zcode-src snapshot + docker compose build.
  exit /b 1
)
echo [PASS] image %IMAGE% exists

docker network inspect "%NET%" >nul 2>&1
if errorlevel 1 (
  echo       Network %NET% missing, creating with internal semantics...
  docker network create --internal "%NET%" >nul
  if errorlevel 1 ( echo [FATAL] network creation failed & exit /b 1 )
)
set "NETINT="
for /f %%i in ('docker network inspect -f "{{.Internal}}" "%NET%" 2^>nul') do set "NETINT=%%i"
if "%NETINT%"=="true" (
  echo [PASS] network %NET% internal=true -- NAT egress cut off
) else (
  echo [FAIL] network %NET% internal is not true -- egress not cut off [f.4]
  set /a FAILS+=1
)

if not exist "%SANDBOX%\" mkdir "%SANDBOX%"
echo [PASS] sandbox dir ready: %SANDBOX%

echo       sidecar %PROXY% check...
REM docker.exe emits LF line endings into pipes -- findstr /x does NOT match, so
REM capture names via for /f (no "=" in this command line, safe per the Day 12 C5 lesson)
REM and compare strings instead.
set "PROXYRUN="
for /f "delims=" %%i in ('docker ps --format "{{.Names}}"') do if /i "%%i"=="%PROXY%" set "PROXYRUN=1"
if not defined PROXYRUN (
  echo       sidecar not running -- starting via docker compose up -d %PROXY% ...
  docker compose -f "%~dp0docker-compose.yml" up -d %PROXY% >nul 2>&1
  set "PROXYRUN="
  for /f "delims=" %%i in ('docker ps --format "{{.Names}}"') do if /i "%%i"=="%PROXY%" set "PROXYRUN=1"
  if not defined PROXYRUN (
    echo [FATAL] sidecar %PROXY% failed to start -- run: docker compose -f engines\docker\docker-compose.yml up -d %PROXY%, then re-run. B check cannot proceed without it.
    exit /b 1
  )
)
echo [PASS] sidecar %PROXY% running

echo.
echo === 1/3 check A: direct public internet must fail ===
docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "curl -sS -m 10 -o /dev/null https://www.google.com" >nul 2>&1
if errorlevel 1 (
  echo [PASS] curl https://www.google.com failed -- expected, egress denied
) else (
  echo [FAIL] container REACHED public google -- egress not cut off, must not accept any task [f.4 hard gate]
  set /a FAILS+=1
)
docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "curl -sS -m 10 -o /dev/null https://example.com" >nul 2>&1
if errorlevel 1 (
  echo [PASS] curl https://example.com failed -- expected, second target also denied
) else (
  echo [FAIL] container REACHED example.com -- second target also got through [f.4]
  set /a FAILS+=1
)

echo.
echo === 2/3 check B: MoRay gateway must be reachable ===
docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "curl -fsS -m 10 %GATEWAY%"
if errorlevel 1 (
  echo [FAIL] gateway %GATEWAY% unreachable
  echo       triage order: 1. is MoRay backend running on host port 8000 -- the sidecar forwards to 127.0.0.1:8000
  echo                     2. sidecar %PROXY% state: docker ps / docker logs %PROXY%
  echo                     3. sidecar-to-host path: vpnkit via host.docker.internal on the non-internal bridge, see design doc section d
  set /a FAILS+=1
) else (
  echo [PASS] gateway health check succeeded -- JSON returned above
)

echo.
echo === 3/3 check C: container cannot touch host files outside sandbox ===
docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "touch /etc/moray_escape_test" >nul 2>&1
if errorlevel 1 (
  echo [PASS] C1 rootfs read-only -- write to /etc denied
) else (
  echo [FAIL] C1 rootfs is WRITABLE -- read-only not effective
  set /a FAILS+=1
)

docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "test -e /var/run/docker.sock" >nul 2>&1
if errorlevel 1 (
  echo [PASS] C2 docker.sock not visible
) else (
  echo [FAIL] C2 /var/run/docker.sock visible in container -- hands over full host Docker control
  set /a FAILS+=1
)

docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "test -e /mnt/d -o -e /moray -o -e /host -o -e /D -o -e /mnt/host" >nul 2>&1
if errorlevel 1 (
  echo [PASS] C3 host paths /mnt/d, /moray, /host, /D, /mnt/host all invisible
) else (
  echo [FAIL] C3 a host path is visible in container -- mount surface out of bounds
  set /a FAILS+=1
)

set "ENVFOUND="
docker run %FLAGS% --entrypoint sh "%IMAGE%" -c "find / -xdev -maxdepth 4 -name .env -not -path /workspace/* 2>/dev/null | head -n 1" > "%TEMP%\_moray_c4.txt" 2>nul
set /p ENVFOUND=<"%TEMP%\_moray_c4.txt"
del "%TEMP%\_moray_c4.txt" >nul 2>&1
if defined ENVFOUND (
  echo [FAIL] C4 .env file found in container: %ENVFOUND% -- host secret surface leaked
  set /a FAILS+=1
) else (
  echo [PASS] C4 no .env file anywhere in rootfs
)

set "UIDN="
docker run %FLAGS% --entrypoint id "%IMAGE%" -u > "%TEMP%\_moray_c5.txt" 2>nul
set /p UIDN=<"%TEMP%\_moray_c5.txt"
del "%TEMP%\_moray_c5.txt" >nul 2>&1
if "%UIDN%"=="1000" (
  echo [PASS] C5 running user uid=1000 -- node, non-root
) else (
  echo [FAIL] C5 running user uid=%UIDN% -- expected 1000/node
  set /a FAILS+=1
)

docker run %FLAGS% -v "%SANDBOX%:/workspace" --entrypoint sh "%IMAGE%" -c "touch /workspace/.verify_write && rm /workspace/.verify_write" >nul 2>&1
if errorlevel 1 (
  echo [FAIL] C6 sandbox mount not writable -- bind mount or permissions broken, tasks cannot produce output
  set /a FAILS+=1
) else (
  echo [PASS] C6 sandbox mount writable -- /workspace is the only legal persistent write surface
)

echo.
echo === summary ===
if %FAILS%==0 (
  echo ALL PASS -- f.4 hard gate open, ZCodeAdapter may accept tasks, cache TTL 5 minutes.
  echo This script MUST be re-run after image rebuild / Docker restart / network change.
  exit /b 0
) else (
  echo %FAILS% checks FAILED -- per f.4 hard gate: refuse to start ZCode, accept no tasks, never run bare.
  echo See [FAIL] lines above for details; unverifiable isolation = engine unusable, never run-first-fix-later.
  exit /b 1
)
