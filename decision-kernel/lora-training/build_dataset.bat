@echo off
chcp 65001 >nul
cd /d %~dp0
python clean_samples.py   || goto :fail
python convert_format.py  || goto :fail
python split_dataset.py   || goto :fail
python env_check.py       || goto :fail
echo.
echo [ALL DONE] see output\ folder
exit /b 0
:fail
echo [FAIL] pipeline stopped, see error above
exit /b 1
