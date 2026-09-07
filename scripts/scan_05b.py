# -*- coding: utf-8 -*-
import io
import glob

print('===== 95_features tick =====')
lines = io.open('parts/95_features.js', encoding='utf-8-sig', errors='replace').readlines()
for i in range(max(0, 435 - 12), min(len(lines), 450)):
    print(i + 1, lines[i].rstrip()[:150])

print()
print('===== probeBackend callers / intervals =====')
for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        t = l.strip()
        if 'probeBackend' in t or ('setInterval' in t and 'setInterval(function' not in t):
            print('%s:%d: %s' % (path, i + 1, t[:160]))

print()
print('===== all setInterval inventory =====')
for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        if 'setInterval(' in l:
            print('%s:%d: %s' % (path, i + 1, l.strip()[:150]))
