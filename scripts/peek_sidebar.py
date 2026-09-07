# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
start = None
for i, l in enumerate(lines):
    if 'function ensureWsSidebar' in l:
        start = i
        break
for j in range(start, min(len(lines), start + 34)):
    print(j + 1, lines[j].rstrip()[:150])
