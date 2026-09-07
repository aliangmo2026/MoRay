# -*- coding: utf-8 -*-
"""阶段0.5 产物门禁:幂等×2 + web 副本 + release 四份 hash + 版本号"""
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
        print('%-42s %s' % (p, h))
print('distinct hashes:', len(hs))
html = open('moray-workbench.html', encoding='utf-8').read()
print('产物含 3.16.1:', html.count('3.16.1') >= 2)
print('产物无 3.16.0 构建号残留:', 'MORAY_BUILD = \'3.16.0\'' not in html and 'APP_VERSION = \'v3.16.0\'' not in html)
# 关键新能力命中
for marker in ['data-tool-retry', 'data-steps-toggle', 'stepsSummaryText', 'agentFingerprint',
               'wsTreeRender', 'moray:ws-file-written', '已停止（未开始）', 'agentTrustedClear']:
    print('marker %s:' % marker, marker in html)
