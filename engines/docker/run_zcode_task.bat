@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
REM ============================================================
REM [Day16 -> Day17] MoRay ZCodeAdapter real-Docker wrapper.
REM
REM Chain: ZCodeAdapter.start() -> this bat -> real docker run -> zcode CLI
REM        inside moray/zcode-agent:0.16.9 on the internal moray-zcode-net.
REM
REM The adapter invokes (see server/app/engines/zcode_adapter.py
REM _build_command, all values are forced constants there):
REM   run_zcode_task.bat -p - --mode edit --cwd <host_sandbox_path>
REM                      --disallowed-tools Bash,WebFetch,Write --json
REM
REM [Day17 FIX, architect plan B] CLI 0.16.9 has NO stdin reading logic:
REM "-p -" reached the model as a literal "-" (Day 16 finding,
REM docs/day16_real_e2e.md section 4.3). This wrapper now DRAINS the
REM adapter's stdin (the prompt, UTF-8 bytes, written then closed by
REM zcode_adapter.py _worker) and passes the content INLINE as the -p
REM argument. The adapter's "-p -" argv pair still arrives and is ignored:
REM adapter argv shape and .py stay untouched, only this wrapper changed.
REM Day 14 proven inline shape restored: zcode -p "<content>" ...
REM
REM Limitations (plan A territory, file-based handoff):
REM   - content is normalized to a single line (newlines -> spaces);
REM   - a double quote or a trailing backslash inside the prompt would
REM     break argv quoting; a leading ";" line is dropped by for /f;
REM   - inline length guard 7000 bytes (cmd 8191-char line limit);
REM     longer prompts must move to a file-based plan A.
REM
REM Mirror constants - keep in sync with zcode_adapter.py:
REM   FORCED_MODE = edit
REM   DISALLOWED_TOOLS = Bash,WebFetch,Write
REM Mounts and env are identical to the Day 14 proven real-task run
REM (docs/day14_link_test.md section 2.2); -i kept from Day 16 (harmless:
REM stdin is drained before docker starts, CLI 0.16.9 never reads stdin).
REM All echo text is ASCII-only per the Day 12 C5 encoding lesson.
REM ============================================================

set "IMAGE=moray/zcode-agent:0.16.9"
set "NET=moray-zcode-net"
set "ROOT=D:\MoRay\workspace\zcode_sandbox"

set "CWD_HOST="
set "PREV="
for %%a in (%*) do (
  if "!PREV!"=="--cwd" set "CWD_HOST=%%~a"
  set "PREV=%%~a"
)

if not defined CWD_HOST (
  >&2 echo [moray-zcode-wrapper] FATAL: no --cwd argument received from adapter
  exit /b 2
)

REM host sandbox root -> /workspace ; deeper paths keep their tail.
REM Containment check by global replacement: what remains after stripping the
REM root prefix must be empty or start with a path separator (prefix semantics).
set "REL=!CWD_HOST:%ROOT%=!"
if not "!REL!"=="" (
  if not "!REL:~0,1!"=="\" if not "!REL:~0,1!"=="/" (
    >&2 echo [moray-zcode-wrapper] FATAL: --cwd outside sandbox root: !CWD_HOST!
    exit /b 2
  )
  REM traversal guard lives inside the non-empty branch: with an empty REL the
  REM expression !REL:..=! degrades to the literal text "..=" and always fires
  if not "!REL:..=!"=="!REL!" (
    >&2 echo [moray-zcode-wrapper] FATAL: path traversal rejected in --cwd
    exit /b 2
  )
  set "REL=!REL:~1!"
  set "REL=!REL:\=/!"
  set "CWD_CTR=/workspace/!REL!"
) else set "CWD_CTR=/workspace"

>&2 echo [moray-zcode-wrapper] adapter argv: %*
>&2 echo [moray-zcode-wrapper] host cwd : !CWD_HOST!
>&2 echo [moray-zcode-wrapper] ctr cwd  : !CWD_CTR!
>&2 echo [moray-zcode-wrapper] image=!IMAGE! net=!NET!

REM ===== [Day17] drain stdin -> inline -p (architect plan B) =====
REM Nested scope disables delayed expansion so ! and % chars in the prompt
REM survive verbatim through capture and expansion. Phase A variables
REM (IMAGE/NET/CWD_CTR) are inherited here and referenced with normal %.
setlocal DisableDelayedExpansion

REM One PowerShell pass: stdin bytes -> UTF-8 string -> two files
REM (raw copy for evidence, single-line normalized for the -p argument).
REM Explicit UTF8Encoding($false): no BOM; the adapter writes UTF-8 with no
REM trailing newline (zcode_adapter.py: proc.stdin.write(prompt.encode("utf-8"))).
set "PF_RAW=%TEMP%\moray_prompt_raw_%RANDOM%%RANDOM%.txt"
set "PF_ONE=%TEMP%\moray_prompt_one_%RANDOM%%RANDOM%.txt"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$in=[Console]::OpenStandardInput();$ms=New-Object System.IO.MemoryStream;$in.CopyTo($ms);$t=[Text.Encoding]::UTF8.GetString($ms.ToArray());$u=New-Object System.Text.UTF8Encoding($false);$one=$t -replace '\r?\n',' ';$one=$one.Trim();[IO.File]::WriteAllText('%PF_RAW%',$t,$u);[IO.File]::WriteAllText('%PF_ONE%',$one,$u)"

set "PBYTES="
for %%Z in ("%PF_ONE%") do set "PBYTES=%%~zZ"
if not defined PBYTES (
  >&2 echo [moray-zcode-wrapper] FATAL: stdin drain failed, powershell missing or IO error
  exit /b 2
)
if %PBYTES% EQU 0 (
  >&2 echo [moray-zcode-wrapper] FATAL: empty prompt on stdin
  del /q "%PF_RAW%" "%PF_ONE%" >nul 2>&1
  exit /b 2
)
if %PBYTES% GTR 7000 (
  >&2 echo [moray-zcode-wrapper] FATAL: prompt too long for inline -p, plan A file-based handoff required
  del /q "%PF_RAW%" "%PF_ONE%" >nul 2>&1
  exit /b 2
)
set "PROMPT="
for /f "delims=" %%L in ('type "%PF_ONE%"') do set "PROMPT=%%L"
if not defined PROMPT (
  >&2 echo [moray-zcode-wrapper] FATAL: empty prompt after normalization
  del /q "%PF_RAW%" "%PF_ONE%" >nul 2>&1
  exit /b 2
)
>&2 echo [moray-zcode-wrapper] stdin drained: %PBYTES% bytes, passing inline via -p

docker run --rm -i --network %NET% ^
  -v "D:/MoRay/workspace/zcode_sandbox:/workspace" ^
  -v "D:/MoRay/engines/docker/provider/zcode-provider.json:/run/moray/zcode-provider.json:ro" ^
  -v "D:/MoRay/engines/docker/zcode-src/config/provider/zcode-builtin.json:/opt/zcode/provider/zcode-builtin.json:ro" ^
  -e "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=/run/moray/zcode-provider.json" ^
  -e "ZCODE_MODEL_TELEMETRY_ENABLED=0" ^
  -w "%CWD_CTR%" ^
  %IMAGE% ^
  zcode -p "%PROMPT%" --mode edit --cwd "%CWD_CTR%" --disallowed-tools Bash,WebFetch,Write --json

set "DOCKER_RC=%errorlevel%"
>&2 echo [moray-zcode-wrapper] docker exit code: %DOCKER_RC%
del /q "%PF_RAW%" "%PF_ONE%" >nul 2>&1
exit /b %DOCKER_RC%
