# -*- coding: utf-8 -*-
import io

lines = io.open('parts/20_ai.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if '_finish(' in l and ('function' in l or 'self._finish' in l or 'this._finish' in l)]
for i in hits[:3]:
    print('---', i + 1)
    for j in range(i, min(i + 30, len(lines))):
        print(j + 1, lines[j].rstrip()[:150])
