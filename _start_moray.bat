@echo off
cd /d D:\MoRay
REM [2026-10-04 timeout-drift-fix] Reflection gate/timeout authoritative values live in server/.env
REM (MORAY_REFLECTION / MORAY_REFLECTION_TIMEOUT). This script must NOT re-set them:
REM process env overrides .env, and duplicated inline sets are exactly what caused the 120/300/600 drift.
REM NOTE: keep this file ASCII-only in comments - cmd reads bat as GBK, UTF-8 Chinese breaks parsing.
server\.venv\Scripts\python.exe -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000 > "%TEMP%\moray.out.log" 2> "%TEMP%\moray.err.log"
