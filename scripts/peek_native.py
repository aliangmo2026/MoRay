# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if 'name:' in l and ("'run_command'" in l or 'find_files' in l):
        print(i + 1, l.rstrip()[:80])
# 找 NATIVE_TOOLS 数组结尾
hits = [i for i, l in enumerate(lines) if 'NATIVE_TOOLS.forEach' in l]
print('forEach at', hits[0] + 1 if hits else None)
for j in range(hits[0] - 8, hits[0] + 2):
    print(j + 1, lines[j].rstrip()[:100])
