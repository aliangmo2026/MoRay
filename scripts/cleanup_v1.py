# -*- coding: utf-8 -*-
"""确认 peek_copy.py/scan_tmp2.py 无引用后删除"""
import glob
import io
import os

for target in ['peek_copy.py', 'scan_tmp2.py']:
    hits = []
    for p in glob.glob('**/*', recursive=True):
        if not os.path.isfile(p) or p.endswith(('.pyc', '.png', '.jpg', '.zip', '.sqlite3', target)):
            continue
        try:
            txt = io.open(p, encoding='utf-8-sig', errors='replace').read()
        except Exception:
            continue
        if target in txt:
            hits.append(p)
    path = os.path.join('scripts', target)
    if hits:
        print('keep', path, '(refs:', hits, ')')
    elif os.path.exists(path):
        os.remove(path)
        print('removed', path)
    else:
        print('already gone:', path)
