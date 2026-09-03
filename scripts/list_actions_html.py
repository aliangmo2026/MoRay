# -*- coding: utf-8 -*-
"""列出 moray-workbench.html 中全部 data-action 值及行号。"""
import io
import re

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    text = f.read()

seen = {}
for i, line in enumerate(text.split('\n'), 1):
    for m in re.finditer(r'data-action="([a-z0-9-]+)"', line):
        seen.setdefault(m.group(1), []).append(i)

for op in sorted(seen):
    print('%-22s x%-3d  lines %s' % (op, len(seen[op]), seen[op][:6]))
