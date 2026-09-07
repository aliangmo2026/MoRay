# -*- coding: utf-8 -*-
import io

lines = io.open('parts/20_ai.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'detectBackend' in l]
for i in hits:
    print('--- around line', i + 1)
    for j in range(max(0, i - 2), min(len(lines), i + 30)):
        print(j + 1, lines[j].rstrip()[:150])
