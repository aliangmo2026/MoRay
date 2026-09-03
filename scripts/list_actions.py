# -*- coding: utf-8 -*-
"""列出 parts 中全部 data-action 值（含转义形式）及其位置。"""
import io
import re
import glob

seen = {}
pat = re.compile(r'data-action=.??"?([a-z0-9-]+)"?', re.I)
for path in sorted(glob.glob(r'D:\ai工具台\parts\*.js')):
    with io.open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            for m in re.finditer(r'data-action=\\?"([a-z0-9-]+)\\?"', line):
                seen.setdefault(m.group(1), []).append('%s:%d' % (path.split('\\')[-1], i))
            for m in re.finditer(r'data-action="([a-z0-9-]+)"', line):
                seen.setdefault(m.group(1), []).append('%s:%d' % (path.split('\\')[-1], i))
for op in sorted(seen):
    locs = sorted(set(seen[op]))
    print('%-22s x%-3d  %s' % (op, len(locs), ', '.join(locs[:5])))
