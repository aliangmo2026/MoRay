# -*- coding: utf-8 -*-
import json
import time
import http.client


def raw_post(path, body, timeout=6):
    t0 = time.time()
    conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=timeout)
    conn.request('POST', path, body=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return time.time() - t0, resp.status, data[:200]


t, s, d = raw_post('/__ctl', {'scenario': 'list'})
print('ctl list: %.1fs %s' % (t, s))
t, s, d = raw_post('/v1/chat/completions', {
    'model': 'mock-llm',
    'messages': [{'role': 'user', 'content': 'hello'}],
    'stream': False,
    'tools': [],
})
print('req list: %.1fs %s %s' % (t, s, d[:120]))
t, s, d = raw_post('/__ctl', {'scenario': 'multi_slow'})
print('ctl slow: %.1fs %s' % (t, s))
t, s, d = raw_post('/v1/chat/completions', {
    'model': 'mock-llm',
    'messages': [{'role': 'user', 'content': 'hi'}],
    'stream': False,
    'tools': [],
}, timeout=8)
print('req slow: %.1fs %s %s' % (t, s, d[:120]))
