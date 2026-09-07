# -*- coding: utf-8 -*-
"""完成态备份 zip(排除 backup 自身/.venv/__pycache__/release/web 产物冗余)"""
import os
import time
import zipfile

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'MoRay_阶段0本机Agent完整工程_%s.zip' % ts)

EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'work', 'release'}
EXCLUDE_FILES = {'moray-workbench.html', 'sw.js', 'manifest.webmanifest'}

n = 0
total = 0
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
        for f in fns:
            if f in EXCLUDE_FILES:
                continue
            if f.endswith(('.pyc', '.pyo')):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, ROOT)
            z.write(full, rel)
            n += 1
            total += os.path.getsize(full)
print('zip:', dest)
print('files:', n, 'src_MB: %.1f' % (total / 1048576))
