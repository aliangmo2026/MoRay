# -*- coding: utf-8 -*-
import io
import json
import subprocess

env = json.load(open(r'D:\ai工具台\work\e2e_env.json', encoding='utf-8'))
print('env mock_pid:', env['mock_pid'])
out = subprocess.run(['tasklist', '/FI', 'PID eq %d' % env['mock_pid'], '/FO', 'CSV'], capture_output=True)
print(out.stdout.decode('gbk', errors='replace'))
# netstat 找 8898 的 PID
ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if '127.0.0.1:8898' in l and 'LISTENING' in l:
        print('listener:', l.strip())
