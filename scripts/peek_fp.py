# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if 'agentFingerprint(' in l:
        print(i + 1, l.rstrip()[:160])
