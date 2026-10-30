@echo off
chcp 65001 >nul
"D:\MoRay\engines\bin\zcode.cmd" -p "Write a Python file fib2.py in the current directory. Define fib(n) returning the n-th Fibonacci number (0-indexed). Add a __main__ block that prints fib(10). Keep under 25 lines. Do not run any commands." --json --mode edit --cwd "D:\MoRay\workspace\zcode_sandbox\day24a_probe6" > "D:\MoRay\workspace\zcode_sandbox\day24a_stdout6.txt" 2> "D:\MoRay\workspace\zcode_sandbox\day24a_stderr6.txt"
exit /b %ERRORLEVEL%
