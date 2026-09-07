# -*- coding: utf-8 -*-
import os

for base in ['release', 'web', '.']:
    p = os.path.join(base)
    if os.path.isdir(p):
        files = sorted(os.listdir(p))
        htmls = [f for f in files if f.endswith('.html')]
        print(base, '->', files[:20])
        print('   htmls:', htmls)
