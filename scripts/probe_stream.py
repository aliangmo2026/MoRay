# -*- coding: utf-8 -*-
"""直接对 8898 发 no_tools_model 的流式请求(模拟兜底),看是否 Failed"""
import json
import time
import http.client

conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=6)
conn.request('POST', '/__ctl', body=json.dumps({'scenario': 'no_tools_model'}).encode(),
             headers={'Content-Type': 'application/json'})
r = conn.getresponse()
print('ctl:', r.status, r.read()[:80])
conn.close()
# 流式不带 tools(兜底形态)
conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=6)
conn.request('POST', '/v1/chat/completions',
             body=json.dumps({'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}],
                              'stream': True}).encode(),
             headers={'Content-Type': 'application/json'})
r = conn.getresponse()
print('stream status:', r.status)
data = r.read(400)
print('stream body head:', data[:300])
conn.close()
# 带 tools 的非流式 → 400
conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=6)
conn.request('POST', '/v1/chat/completions',
             body=json.dumps({'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}],
                              'stream': False, 'tools': [{}]}).encode(),
             headers={'Content-Type': 'application/json'})
r = conn.getresponse()
print('tools req:', r.status, r.read()[:120])
conn.close()
