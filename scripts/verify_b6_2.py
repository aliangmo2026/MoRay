# -*- coding: utf-8 -*-
"""#2 负向验证：改 110 MORAY_VERSION 9.9.9 → assemble 失败列出差异；改回通过"""
import io
import subprocess

p = 'parts/110_polish.js'
src = io.open(p, encoding='utf-8-sig').read()
orig = src
assert "window.MORAY_VERSION = '1.0.0';" in src
src = src.replace("window.MORAY_VERSION = '1.0.0';", "window.MORAY_VERSION = '9.9.9';", 1)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
r = subprocess.run(['python', 'assemble.py'], capture_output=True, text=True, timeout=120)
print('exit code:', r.returncode)
print('failure message:', [l for l in r.stdout.splitlines() if '对外产品版本不一致' in l][:1])
ok_fail = r.returncode != 0 and '对外产品版本不一致' in r.stdout and '110_polish.js MORAY_VERSION=9.9.9' in r.stdout and 'PRODUCT_VERSION=1.0.0' in r.stdout
print('negative as expected:', ok_fail)
io.open(p, 'w', encoding='utf-8', newline='').write(orig)
r2 = subprocess.run(['python', 'assemble.py'], capture_output=True, text=True, timeout=120)
print('restored ok:', 'assembled OK' in r2.stdout and r2.returncode == 0)
