# -*- coding: utf-8 -*-
"""检查 test_agent_tools.py M2 段的实际内容（确认无拼接错乱）"""
import io

lines = io.open('scripts/test_agent_tools.py', encoding='utf-8').readlines()
start = None
for i, l in enumerate(lines):
    if 'M2-F1' in l:
        start = i
        break
for j in range(max(0, start - 6), min(len(lines), start + 90)):
    print(j + 1, lines[j].rstrip()[:170])
