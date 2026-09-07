# -*- coding: utf-8 -*-
"""验证 stop_w 场景在 8898 正常(4 步推进)"""
import json
import time
import http.client


def post(path, body, timeout=6):
    conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=timeout)
    conn.request('POST', path, body=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    r = conn.getresponse()
    d = r.read()
    conn.close()
    return r.status, d[:200]


print('ctl', post('/__ctl', {'scenario': 'stop_w'}))
for i in range(4):
    t0 = time.time()
    s, d = post('/v1/chat/completions', {
        'model': 'm', 'messages': [{'role': 'user', 'content': 'x%d' % i}],
        'stream': False, 'tools': [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {}}}],
    })
    print('req%d: %s %.2fs %s' % (i, s, time.time() - t0, d[:130]))
