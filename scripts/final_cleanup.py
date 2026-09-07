# -*- coding: utf-8 -*-
"""收尾:删回归临时库/工作区 + 确认无监听残留"""
import glob
import os
import shutil
import subprocess

tmp = os.environ.get('TEMP', r'C:\Users\莫\AppData\Local\Temp')
for pat in ['moray_reg_*', 'moray_e2e_*']:
    for p in glob.glob(os.path.join(tmp, pat)):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            print('rm', p)
        except OSError:
            pass
ns = subprocess.run(['netstat', '-ano'], capture_output=True)
left = []
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if any(('127.0.0.1:%d' % p) in l and 'LISTENING' in l for p in (8000, 8003, 8898, 8899, 8010, 8011, 8012, 8901, 8902, 8903, 8904)):
        left.append(l.strip())
print('remaining listeners:', left if left else 'none')
