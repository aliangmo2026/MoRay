# -*- coding: utf-8 -*-
"""详细探针:逐步请求,打印每步耗时与响应"""
import json
import time
import urllib.request
import http.client

BASE = 'http://127.0.0.1:8898'


def raw_post(path, body, timeout=8):
    t0 = time.time()
    conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=timeout)
    conn.request('POST', path, body=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return time.time() - t0, resp.status, data[:300]


t, s, d = raw_post('/__ctl', {'scenario': 'multi_slow'})
print('ctl: %.1fs %s %s' % (t, s, d))
for i in range(2):
    t, s, d = raw_post('/v1/chat/completions', {
        'model': 'mock-llm',
        'messages': [{'role': 'user', 'content': 'test %d' % i}],
        'stream': False,
        'tools': [],
    })
    print('req%d: %.1fs %s %s' % (i, t, s, d[:150]))
