# -*- coding: utf-8 -*-
import json
import urllib.request

j = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/agent/log?limit=30', timeout=5))
rows = j['data']['rows']
print('total:', j['data']['total'])
for r in rows[:14]:
    print('%-14s | %-10s | %s' % (r['tool'], r['status'], r['args_summary'][:60]))
stop_rows = [r for r in rows if 'stop_me' in r['args_summary']]
print('stop_me 相关记录数:', len(stop_rows))
