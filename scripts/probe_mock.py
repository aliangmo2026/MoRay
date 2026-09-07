# -*- coding: utf-8 -*-
import json
import urllib.request

req = urllib.request.Request('http://127.0.0.1:8898/__state', method='POST',
                             data=b'{}', headers={'Content-Type': 'application/json'})
try:
    print('state:', urllib.request.urlopen(req, timeout=3).read().decode())
except Exception as e:
    print('state err:', e)
# 设置 multi_slow 并读回
req2 = urllib.request.Request('http://127.0.0.1:8898/__ctl', method='POST',
                              data=json.dumps({'scenario': 'multi_slow'}).encode(),
                              headers={'Content-Type': 'application/json'})
try:
    print('ctl:', urllib.request.urlopen(req2, timeout=3).read().decode())
except Exception as e:
    print('ctl err:', e)
