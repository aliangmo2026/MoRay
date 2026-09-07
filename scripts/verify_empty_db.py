# -*- coding: utf-8 -*-
"""空库启动验证:health/build/config/agent 审计表 + 真实库未被触碰"""
import json
import os
import time
import urllib.request

base = 'http://127.0.0.1:8003'
h = json.load(urllib.request.urlopen(base + '/api/health', timeout=5))
print('health:', h['ok'], 'build:', h['build'], 'counts:', h['counts'])
c = json.load(urllib.request.urlopen(base + '/api/agent/config', timeout=5))
print('agent config:', c['ok'], c['data']['workspace'], 'exists:', c['data']['workspaceExists'])
l = json.load(urllib.request.urlopen(base + '/api/agent/log?limit=5', timeout=5))
print('agent log total:', l['data']['total'])
# 空库写入一条审计(验证新表可用)
import urllib.request as ur
req = ur.Request(base + '/api/agent/tool', method='POST',
                 data=json.dumps({'name': 'list_directory', 'args': {}, 'approved': True}).encode(),
                 headers={'Content-Type': 'application/json'})
r = json.load(ur.urlopen(req, timeout=5))
print('tool on empty db:', r['ok'], 'count:', r['data']['count'])
p = r'server\data\moray.sqlite3'
st = os.stat(p)
print('real db after: size=%d mtime=%s (unchanged=%s)' % (
    st.st_size,
    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)),
    st.st_size == 114688))
