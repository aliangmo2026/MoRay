# -*- coding: utf-8 -*-
"""列出 parts 中全部 data-op 值及其出现文件/行号。"""
import io
import re
import glob

seen = {}
for path in sorted(glob.glob(r'D:\ai工具台\parts\*.js')):
    with io.open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            for m in re.finditer(r'data-op="([^"]+)"', line):
                seen.setdefault(m.group(1), []).append('%s:%d' % (path.split('\\')[-1], i))

for op in sorted(seen):
    locs = seen[op]
    print('%-14s x%-3d  %s' % (op, len(locs), ', '.join(locs[:4])))
