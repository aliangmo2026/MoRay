# -*- coding: utf-8 -*-
"""v1.0.0 封版备份 + 版本显示点定位"""
import io
import os
import shutil
import time

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'pre_v1_release_' + ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'venv'}


def ignore_fn(directory, entries):
    return [e for e in entries if e in EXCLUDE_DIRS or e.endswith(('.pyc', '.pyo'))]


shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
print('BACKUP_OK dest=%s' % dest)

# 版本显示点：0.3.0 / v0.3.0 出现的文件
print('\n== 0.3.0 / v0.3.0 出现处 ==')
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in EXCLUDE_DIRS and d != 'work']
    for f in fns:
        if not f.endswith(('.js', '.py', '.md', '.html', '.json', '.webmanifest', '.txt')):
            continue
        p = os.path.join(dp, f)
        try:
            txt = io.open(p, encoding='utf-8-sig', errors='replace').read()
        except Exception:
            continue
        if '0.3.0' in txt:
            for i, l in enumerate(txt.splitlines()):
                if '0.3.0' in l:
                    print('%s:%d %s' % (p, i + 1, l.strip()[:150]))
