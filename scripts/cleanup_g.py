# -*- coding: utf-8 -*-
import glob
import os
import subprocess

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if ('127.0.0.1:8003' in l or '127.0.0.1:8000' in l or '127.0.0.1:8898' in l) and 'LISTENING' in l:
        pid = l.split()[-1]
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
        print('killed', pid)
for f in glob.glob(os.path.join(os.environ.get('TEMP', '.'), 'moray_empty_g.sqlite3')):
    try:
        os.remove(f)
        print('rm', f)
    except OSError:
        pass
print('ok')
