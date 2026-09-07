# -*- coding: utf-8 -*-
import io

lines = io.open('sw_template.js', encoding='utf-8-sig', errors='replace').readlines()
out = []
for i, l in enumerate(lines):
    out.append('%d %s' % (i + 1, l.rstrip()[:160]))
io.open('work/psw2.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('lines', len(out))
