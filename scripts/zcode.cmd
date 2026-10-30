@echo off
rem MoRay project-local ZCode launcher (batch 2 task D / batch 2.5 task 4).
rem HARD-OVERLAY the project-migrated config: the parent shell may carry the
rem desktop ZCode's ZCODE_PERSONAL_PROVIDER_CONFIG_FILE (pointing at the user's
rem real C:\ config with cloud keys) -- it must never leak into MoRay runs.
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\work\zcode-home") do set "ZCODE_DATA_BASE_DIR=%%~fI"
set "ZCODE_STORAGE_DIR=%ZCODE_DATA_BASE_DIR%"
set "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=%ZCODE_DATA_BASE_DIR%\.zcode\v2\provider_config.json"
set "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE="
set "ZCODE_MODEL_TELEMETRY_ENABLED=0"
rem OTLP telemetry vars are left UNSET on purpose: no endpoint = telemetry fully off.
if "%ZCODE_SCRIPT%"=="" set "ZCODE_SCRIPT=D:\\ZCode_Test\\zcode-repo\\apps\\zcode-cli\\packages\\cli\\dist\\zcode.cjs"
if not exist "%ZCODE_SCRIPT%" (
  echo [moray-zcode] ZCode CLI script not found: %ZCODE_SCRIPT%
  echo [moray-zcode] Set ZCODE_SCRIPT to your zcode.cjs build and retry.
  exit /b 1
)
node "%ZCODE_SCRIPT%" %*
