# -*- coding: utf-8 -*-
"""v1.0.0 定稿：四份产物 hash + 版本命中 + release 命名"""
import glob
import hashlib
import io
import os

cands = ['moray-workbench.html', 'web/index.html', 'web/moray-workbench.html']
cands += sorted(glob.glob('release/MoRay-v1.0.0/*.html'))
hs = set()
for p in cands:
    if os.path.exists(p):
        h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        hs.add(h)
        print('%-44s %s' % (p, h))
print('distinct:', len(hs))
html = open('moray-workbench.html', encoding='utf-8').read()
print('产物 MORAY_VERSION=1.0.0:', "window.MORAY_VERSION = '1.0.0';" in html)
print('产物 MORAY_BUILD=3.18.0:', "window.MORAY_BUILD = '3.18.0';" in html)
print('产物无 0.3.0 版本号:', 'MORAY_VERSION = \'0.3.0\'' not in html)
rel = glob.glob('release/*')
print('release:', [os.path.basename(r) for r in rel])
