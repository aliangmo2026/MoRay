@echo off
chcp 65001 >nul
"D:\MoRay\engines\bin\zcode.cmd" -p "Write a file notes.txt with one line: hello world. Do not run commands." --mode edit --verbose --cwd "D:\MoRay\workspace\zcode_sandbox\day24a_probe7" > "D:\MoRay\workspace\zcode_sandbox\day24a_stdout7.txt" 2> "D:\MoRay\workspace\zcode_sandbox\day24a_stderr7.txt"
exit /b %ERRORLEVEL%
