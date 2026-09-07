# -*- coding: utf-8 -*-
import io

lines = io.open('parts/20_ai.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if 'toolCalls' in l:
        print(i + 1, l.rstrip()[:150])
