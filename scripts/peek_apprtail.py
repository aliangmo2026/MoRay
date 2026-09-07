# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
start = None
for i, l in enumerate(lines):
    if 'async function agentApproval' in l:
        start = i
        break
# 打印函数尾（obs/refreshIcons/结尾括号）
for j in range(start + 68, min(len(lines), start + 92)):
    print(j + 1, repr(lines[j])[:140])
