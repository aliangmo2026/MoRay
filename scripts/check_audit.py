# -*- coding: utf-8 -*-
import json
import urllib.request

env = json.load(open(r'D:\ai工具台\work\e2e_env.json', encoding='utf-8'))
j = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/agent/log?limit=30', timeout=5))
rows = j['data']['rows']
denied = [r for r in rows if r['status'] == 'denied']
okw = [r for r in rows if r['tool'] == 'write_file' and r['status'] == 'ok']
needs = [r for r in rows if r['status'] == 'needs_approval']
print('total logs:', j['data']['total'])
print('denied rows:', [(r['tool'], r['args_summary'], r['approved']) for r in denied][:3])
print('write ok rows:', [(r['tool'], r['args_summary'], r['approved']) for r in okw][:3])
print('needs_approval rows:', [(r['tool'], r['args_summary']) for r in needs][:5])
