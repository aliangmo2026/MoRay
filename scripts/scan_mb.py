# -*- coding: utf-8 -*-
import io

for path in ['parts/85_robust.js', 'parts/95_features.js', 'parts/115_backend_sync.js', 'parts/80_enhance.js']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    print('==', path)
    for i, l in enumerate(lines):
        t = l.rstrip()
        if 'MorayBackend' in t and ('=' in t or 'probe' in t or 'detect' in t or 'origin' in t):
            print(i + 1, t[:180])
