@echo off
chcp 65001 >nul
"D:\MoRay\engines\bin\zcode.cmd" -p - --mode edit --cwd "D:\MoRay\workspace\zcode_sandbox\day24a_probe4" < "D:\MoRay\workspace\zcode_sandbox\day24a_prompt.txt" > "D:\MoRay\workspace\zcode_sandbox\day24a_stdout4.txt" 2> "D:\MoRay\workspace\zcode_sandbox\day24a_stderr4.txt"
exit /b %ERRORLEVEL%
