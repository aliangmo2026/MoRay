# -*- coding: utf-8 -*-
import json
import urllib.request

req = urllib.request.Request('http://127.0.0.1:8898/__history', method='POST', data=b'{}',
                             headers={'Content-Type': 'application/json'})
h = json.loads(urllib.request.urlopen(req, timeout=3).read())
print('history len:', len(h.get('history', [])))
for x in h.get('history', [])[-12:]:
    print(x)
