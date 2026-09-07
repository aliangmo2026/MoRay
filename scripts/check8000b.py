# -*- coding: utf-8 -*-
import subprocess
import urllib.request

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
left = [l.strip() for l in ns.stdout.decode('gbk', errors='replace').splitlines()
        if '127.0.0.1:8000' in l and 'LISTENING' in l]
print('8000 listener:', left if left else 'none')
try:
    print('health:', urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status)
except Exception as e:
    print('8000 dead:', type(e).__name__)
