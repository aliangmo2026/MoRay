# -*- coding: utf-8 -*-
import io
import glob
import hashlib

sw = io.open('sw.js', encoding='utf-8-sig').read()
print('sw CACHE_NAME:', [l for l in sw.splitlines() if 'CACHE_NAME =' in l])
for p in ['web/moray-workbench.html', 'moray-workbench.html', 'web/index.html']:
    txt = io.open(p, encoding='utf-8').read()
    print(p, 'banner:', 'demoModeBanner' in txt)
cands = ['moray-workbench.html', 'web/index.html', 'web/moray-workbench.html']
cands += sorted(glob.glob('release/MoRay-v1.0.0/*.html'))
for p in cands:
    h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    print('%-40s %s' % (p, h[:16]))
