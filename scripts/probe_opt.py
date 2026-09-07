# -*- coding: utf-8 -*-
"""全量检查 mock 的 OPTIONS 与 SSE 响应头"""
import json
import http.client

# 1) OPTIONS preflight
conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=5)
conn.request('OPTIONS', '/v1/chat/completions',
             headers={'Origin': 'http://127.0.0.1:8000',
                      'Access-Control-Request-Method': 'POST',
                      'Access-Control-Request-Headers': 'authorization,content-type'})
r = conn.getresponse()
print('OPTIONS:', r.status)
print('  allow-origin:', r.getheader('Access-Control-Allow-Origin'))
print('  allow-headers:', r.getheader('Access-Control-Allow-Headers'))
print('  allow-methods:', r.getheader('Access-Control-Allow-Methods'))
r.read()
conn.close()

# 2) SSE 全量读
conn = http.client.HTTPConnection('127.0.0.1', 8898, timeout=6)
conn.request('POST', '/v1/chat/completions',
             body=json.dumps({'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}], 'stream': True}).encode(),
             headers={'Content-Type': 'application/json'})
r = conn.getresponse()
print('SSE status:', r.status, 'CT:', r.getheader('Content-Type'), 'CL:', r.getheader('Content-Length'))
data = r.read()
print('SSE total bytes:', len(data))
print('SSE tail:', data[-80:])
conn.close()
