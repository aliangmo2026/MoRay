# -*- coding: utf-8 -*-
import io

for path in ['parts/20_ai.js', 'parts/115_backend_sync.js']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    print('==', path)
    for i, l in enumerate(lines):
        t = l.rstrip()
        if '_useProxy' in t or 'backendSync' in t.lower() and 'http' in t or '127.0.0.1:8000' in t or 'syncURL' in t or 'backendBase' in t or 'localhost:8000' in t:
            print(i + 1, t[:180])
