# -*- coding: utf-8 -*-
import subprocess

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if ('127.0.0.1:8000' in l or '0.0.0.0:8000' in l) and 'LISTENING' in l:
        print('8000 listener:', l.strip())
