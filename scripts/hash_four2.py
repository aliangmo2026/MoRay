# -*- coding: utf-8 -*-
"""四份产物 hash(含 release 包内 html)"""
import glob
import hashlib
import os

cands = ['moray-workbench.html', 'web/index.html', 'web/moray-workbench.html']
rels = sorted(glob.glob('release/MoRay-v1.0.0/*.html'))
cands += rels
for p in cands:
    if os.path.exists(p):
        h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
        print('%-40s %s' % (p, h))
hs = {hashlib.sha256(open(p, 'rb').read()).hexdigest() for p in cands if os.path.exists(p)}
print('distinct hashes:', len(hs))
