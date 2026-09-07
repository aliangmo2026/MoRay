# -*- coding: utf-8 -*-
import io

lines = io.open('parts/125_tools.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'function ensureWsSidebar' in l]
i = hits[0]
out = []
for j in range(i, min(i + 16, len(lines))):
    out.append('%d %s' % (j + 1, lines[j].rstrip()[:140]))
io.open('work/psbh.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('written', len(out))
