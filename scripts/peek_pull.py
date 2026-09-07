# -*- coding: utf-8 -*-
import io

lines = io.open('parts/115_backend_sync.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.rstrip()
    if ('messages' in t and ('GET' in t or '_req(' in t or 'get' in t.lower())) or 'pullAll' in t or 'function pull' in t:
        print(i + 1, t[:150])
