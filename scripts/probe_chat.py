# -*- coding: utf-8 -*-
import json
import urllib.request
import time

BASE = 'http://127.0.0.1:8898'


def post(path, body):
    req = urllib.request.Request(BASE + path, method='POST',
                                 data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


post('/__ctl', {'scenario': 'multi_slow'})
print('ctl ok')
for i in range(3):
    t0 = time.time()
    j = post('/v1/chat/completions', {
        'model': 'mock-llm',
        'messages': [{'role': 'user', 'content': 'test %d' % i}],
        'stream': False,
        'tools': [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}}}}],
    })
    dt = time.time() - t0
    ch = j.get('choices', [{}])[0]
    msg = ch.get('message', {})
    print('req%d: %.1fs finish=%s tool_calls=%s content=%r' % (
        i, dt, ch.get('finish_reason'),
        [(t['function']['name']) for t in msg.get('tool_calls', [])] if msg.get('tool_calls') else None,
        msg.get('content')))
