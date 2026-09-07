# -*- coding: utf-8 -*-
"""杀掉 8898 当前监听进程"""
import subprocess

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
killed = []
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if '127.0.0.1:8898' in l and 'LISTENING' in l:
        pid = l.split()[-1]
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
        killed.append(pid)
print('killed:', killed)
