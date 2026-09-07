# -*- coding: utf-8 -*-
import glob
import io

hits = []
for p in glob.glob('parts/*.js'):
    lines = io.open(p, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        if ("'/conversations/'" in l or '/conversations/' in l) and ("'/messages'" in l or '/messages' in l):
            hits.append((p, i + 1, l.strip()[:110]))
for h in hits:
    print(h)
print('total hits:', len(hits))
