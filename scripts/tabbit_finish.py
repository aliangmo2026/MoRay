# -*- coding: utf-8 -*-
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
r = subprocess.run([CLI, 'finish', '--task', 'moray-fix-b1'], capture_output=True, text=True, timeout=60)
print('finish:', r.stdout[-300:])
