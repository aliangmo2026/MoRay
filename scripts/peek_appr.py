# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
start = None
for i, l in enumerate(lines):
    if 'data-agent-approve-no' in l:
        start = i
        break
if start:
    for j in range(max(0, start - 22), min(len(lines), start + 30)):
        print(j + 1, lines[j].rstrip()[:170])
