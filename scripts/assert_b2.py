# -*- coding: utf-8 -*-
import io
import subprocess

html = io.open('web/index.html', encoding='utf-8').read()
div = html.find('id="demoModeBanner"')
body = html.find('<body')
print('div@%d body@%d after-body:%s' % (div, body, div > body))
if div > 0:
    print('ctx:', html[body:body + 260].replace('\n', ' '))

# 杀 8899 全部
ns = subprocess.run(['netstat', '-ano'], capture_output=True)
for l in ns.stdout.decode('gbk', errors='replace').splitlines():
    if '127.0.0.1:8899' in l and 'LISTENING' in l:
        pid = l.split()[-1]
        subprocess.run(['taskkill', '/F', '/T', '/PID', pid], capture_output=True)
        print('killed', pid)
