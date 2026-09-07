# -*- coding: utf-8 -*-
import hashlib
import glob
import os

cands = ['moray-workbench.html', 'web/index.html', 'web/moray-workbench.html']
cands += sorted(glob.glob('release/MoRay-v1.0.0/*.html'))
hs = set()
for p in cands:
    if os.path.exists(p):
        h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        hs.add(h)
        print('%-42s %s' % (p, h))
print('distinct:', len(hs))
html = open('moray-workbench.html', encoding='utf-8').read()
print('3.17.1 count:', html.count('3.17.1'))
for marker in ['NotificationRuntime', 'uiPolishStyle', 'buildApprovalDiff', 'ws-resize-handle',
               'data-audit-clear', 'notification--polished', 'prefers-reduced-motion',
               'ws-empty', 'plan-head', '本地后端未启动']:
    print('marker %s:' % marker, marker in html)
