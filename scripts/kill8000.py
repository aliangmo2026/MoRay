# -*- coding: utf-8 -*-
import subprocess

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if '127.0.0.1:8000' in l and 'LISTENING' in l:
        pid = l.split()[-1]
        print('killing 8000 pid', pid)
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
print('done')
