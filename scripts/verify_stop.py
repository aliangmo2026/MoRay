# -*- coding: utf-8 -*-
import json
import os
import urllib.request

env = json.load(open(r'D:\ai工具台\work\e2e_env.json', encoding='utf-8'))
print('stop_me.txt exists:', os.path.exists(os.path.join(env['workspace'], 'stop_me.txt')))
req = urllib.request.Request('http://127.0.0.1:8898/__state', method='POST', data=b'{}',
                             headers={'Content-Type': 'application/json'})
print('mock state:', urllib.request.urlopen(req, timeout=3).read().decode())
