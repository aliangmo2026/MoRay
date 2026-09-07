# -*- coding: utf-8 -*-
"""修复批次完成态 zip"""
import os
import time
import zipfile

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'MoRay_修复批次1-5完整工程_%s.zip' % ts)
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
