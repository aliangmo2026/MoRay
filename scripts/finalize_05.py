# -*- coding: utf-8 -*-
"""阶段0.5 收尾:删临时库/工作区 + 完成态 zip + 端口复查"""
import glob
import os
import shutil
import subprocess
import time
import zipfile

tmp = os.environ.get('TEMP', r'C:\Users\莫\AppData\Local\Temp')
for pat in ['moray_05reg*', 'moray_agent_e2e_check_*', 'moray_agent_e2e_ws_*']:
    for p in glob.glob(os.path.join(tmp, pat)):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            print('rm', p)
        except OSError:
            pass

# 完成态 zip
ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'MoRay_阶段05闭环打磨完整工程_%s.zip' % ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'work', 'release'}
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

# 端口复查
ns = subprocess.run(['netstat', '-ano'], capture_output=True)
left = [l.strip() for l in ns.stdout.decode('gbk', errors='replace').splitlines()
        if any(('127.0.0.1:%d' % p) in l and 'LISTENING' in l for p in (8000, 8003, 8897, 8898, 8899))]
print('remaining listeners:', left if left else 'none')
