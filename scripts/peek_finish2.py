# -*- coding: utf-8 -*-
import io

lines = io.open('parts/20_ai.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if '_finish' in l and ('_finish(state' in l or '_finish(s,' in l or ' _finish(' in l) and 'return' not in l.lower()]
for i in hits[:4]:
    print('--- def near line', i + 1)
    for j in range(i, min(i + 26, len(lines))):
        print(j + 1, lines[j].rstrip()[:150])
