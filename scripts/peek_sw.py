# -*- coding: utf-8 -*-
import io
import re

for p in ['sw.js', 'sw_template.js']:
    try:
        src = io.open(p, encoding='utf-8-sig', errors='replace').read()
    except OSError:
        print(p, 'missing')
        continue
    caches = set(re.findall(r"'([A-Za-z0-9_\-]*cache[A-Za-z0-9_\-]*)'|\"([A-Za-z0-9_\-]*cache[A-Za-z0-9_\-]*)\"|([A-Za-z0-9_\-]*CACHE[A-Za-z0-9_\-]*)", src))
    print('==', p, 'len', len(src))
    for c in caches:
        print('  cache-name-token:', c)
    for l in src.splitlines():
        if 'VERSION' in l or 'BUILD' in l:
            print('  ', l.strip()[:130])
