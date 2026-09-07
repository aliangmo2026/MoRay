# -*- coding: utf-8 -*-
import io
import glob

for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        t = l.strip()
        if t.startswith('function showNotification') or t.startswith('async function showNotification') or 'window.showNotification' in t:
            print('== %s:%d' % (path, i + 1))
            for j in range(i, min(i + 50, len(lines))):
                print(j + 1, lines[j].rstrip()[:160])
            break
