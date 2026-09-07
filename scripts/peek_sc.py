# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'async function runAgentSelfCheck' in l]
print('hit:', hits)
i = hits[0]
for j in range(i - 5, i + 3):
    print(j + 1, lines[j].rstrip()[:130])
