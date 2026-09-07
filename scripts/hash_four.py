# -*- coding: utf-8 -*-
"""四份产物 hash:根 html / web/index.html / web/moray-workbench.html / release html"""
import glob
import hashlib
import os

cands = ['moray-workbench.html', 'web/index.html', 'web/moray-workbench.html']
rels = sorted(glob.glob('release/*.html'))
print('release htmls:', rels)
cands += rels
for p in cands:
    if os.path.exists(p):
        h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        print('%-32s %s' % (p, h))
hs = set()
for p in cands:
    if os.path.exists(p):
        hs.add(hashlib.sha256(open(p, 'rb').read()).hexdigest())
print('distinct hashes:', len(hs))
