# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
start = None
for i, l in enumerate(lines):
    if "def.name === 'write_file'" in l and 'argCards' in lines[i + 1] if i + 1 < len(lines) else False:
        start = i
if start is None:
    for i, l in enumerate(lines):
        if '目标文件（工作区内，相对路径）' in l:
            start = i - 3
            break
for j in range(start - 2, min(len(lines), start + 18)):
    print(j + 1, repr(lines[j])[:200])
