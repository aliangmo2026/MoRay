# -*- coding: utf-8 -*-
"""阶段1.5 收尾：临时清理 + 完成态 zip + 端口复查"""
import glob
import os
import shutil
import subprocess
import time
import zipfile

tmp = os.environ.get('TEMP', r'C:\Users\莫\AppData\Local\Temp')
for pat in ['moray_uipolish*']:
    for p in glob.glob(os.path.join(tmp, pat)):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            print('rm', p)
        except OSError:
            pass

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'MoRay_阶段15UI精修完整工程_%s.zip' % ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'release'}
n = 0
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
        for f in fns:
            if f in ('moray-workbench.html', 'sw.js', 'manifest.webmanifest') or f.endswith(('.pyc', '.pyo')):
                continue
            full = os.path.join(dp, f)
            z.write(full, os.path.relpath(full, ROOT))
            n += 1
print('zip:', dest, 'files:', n)

ns = subprocess.run(['netstat', '-ano'], capture_output=True)
left = [l.strip() for l in ns.stdout.decode('gbk', errors='replace').splitlines()
        if any(('127.0.0.1:%d' % p) in l and 'LISTENING' in l for p in (8000, 8003, 8897, 8898, 8899, 8010, 8011, 8012, 8016))]
print('listeners:', left if left else 'none')
