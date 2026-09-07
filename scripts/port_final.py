# -*- coding: utf-8 -*-
import subprocess

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
left = [l.strip() for l in ns.stdout.decode('gbk', errors='replace').splitlines()
        if any(('127.0.0.1:%d' % p) in l and 'LISTENING' in l for p in (8000, 8898, 8899, 8016))]
print('listeners:', left if left else 'none')
