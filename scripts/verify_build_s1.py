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
print('3.17.0 count:', html.count('3.17.0'))
for marker in ['SYSTEM_PROMPT', 'submit_plan', 'buildApprovalDiff', 'wsSidebar', 'agentSelfCheck',
               'find_files', 'search_text', 'edit_file', 'accToolCallDeltas', 'tryCompleteJson']:
    print('marker %s:' % marker, marker in html)
