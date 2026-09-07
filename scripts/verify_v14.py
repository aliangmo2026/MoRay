# -*- coding: utf-8 -*-
"""#14 负向验证：故意改一处版本号 → assemble 应失败并指出不一致；改回后通过"""
import io
import subprocess

p = 'parts/70_models_settings_boot.js'
src = io.open(p, encoding='utf-8-sig').read()
orig = src
src = src.replace("const APP_VERSION = 'v3.18.5';", "const APP_VERSION = 'v9.99.99';", 1)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
r = subprocess.run(['python', 'assemble.py'], capture_output=True, text=True, timeout=120)
ok_fail = '构建失败：内部版本号三处不一致' in r.stdout
print('negative: build failed as expected:', ok_fail)
print('  msg:', [l for l in r.stdout.splitlines() if '不一致' in l][:1])
io.open(p, 'w', encoding='utf-8', newline='').write(orig)
r2 = subprocess.run(['python', 'assemble.py'], capture_output=True, text=True, timeout=120)
print('restored: build ok:', 'assembled OK' in r2.stdout)
