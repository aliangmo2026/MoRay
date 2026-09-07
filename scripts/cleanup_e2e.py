# -*- coding: utf-8 -*-
"""收尾:杀 8899 静态服务器 + 删 e2e 临时库/工作区"""
import glob
import json
import os
import shutil
import subprocess

# 杀 8899
ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if '127.0.0.1:8899' in l and 'LISTENING' in l:
        pid = l.split()[-1]
        subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
        print('killed 8899 pid', pid)

# 删临时文件
tmp = os.environ.get('TEMP', r'C:\Users\莫\AppData\Local\Temp')
for pat in ['moray_e2e_ws_*', 'moray_e2e_*_ui2', 'moray_agent_ws_*', 'moray_agent_ws2_*', 'moray_agent_wsA2_*', 'moray_agent_outside_*']:
    for d in glob.glob(os.path.join(tmp, pat)):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print('rmdir', d)
for pat in ['moray_e2e_*.sqlite3', 'moray_agent_test_*.sqlite3', 'moray_llmproxy_test.sqlite3', 'moray_agent_import.sqlite3']:
    for f in glob.glob(os.path.join(tmp, pat)):
        try:
            os.remove(f)
            print('rm', f)
        except OSError:
            pass
print('cleanup done')
