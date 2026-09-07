# -*- coding: utf-8 -*-
"""v1.0.0 版本定版：MORAY_VERSION/BUILD/APP_VERSION/config/关于页"""
import io

# 1) 110_polish.js：对外版本 1.0.0 + 内部构建 3.18.0
p = 'parts/110_polish.js'
src = io.open(p, encoding='utf-8-sig').read()
assert "window.MORAY_VERSION = '0.3.0';" in src and "window.MORAY_BUILD = '3.17.2';" in src
src = src.replace("window.MORAY_VERSION = '0.3.0';", "window.MORAY_VERSION = '1.0.0';")
src = src.replace("window.MORAY_BUILD = '3.17.2';", "window.MORAY_BUILD = '3.18.0';")
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('bumped 110 (MORAY_VERSION=1.0.0, MORAY_BUILD=3.18.0)')

# 2) 70_models_settings_boot.js：APP_VERSION + 关于页版本行（动态 v1.0.0）
p = 'parts/70_models_settings_boot.js'
src = io.open(p, encoding='utf-8-sig').read()
assert "const APP_VERSION = 'v3.17.2';" in src
src = src.replace("const APP_VERSION = 'v3.17.2';", "const APP_VERSION = 'v3.18.0';")
old_about = '<div class="flex justify-between"><span>版本</span><span class="font-mono text-text-primary">v3.0.0</span></div>'
assert old_about in src, 'about version row missing'
new_about = '<div class="flex justify-between"><span>版本</span><span class="font-mono text-text-primary">v${(typeof window.MORAY_VERSION !== \'undefined\') ? window.MORAY_VERSION : \'1.0.0\'}</span></div>'
src = src.replace(old_about, new_about)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('bumped 70 (APP_VERSION v3.18.0, about version dynamic)')

# 3) server/app/config.py
p = 'server/app/config.py'
src = io.open(p, encoding='utf-8').read()
assert 'VERSION = "0.3.0"' in src and 'BUILD = "3.17.2"' in src
src = src.replace('VERSION = "0.3.0"', 'VERSION = "1.0.0"')
src = src.replace('BUILD = "3.17.2"', 'BUILD = "3.18.0"')
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('bumped config (VERSION=1.0.0, BUILD=3.18.0)')

# 4) server/README.md 头行
p = 'server/README.md'
src = io.open(p, encoding='utf-8').read()
first = src.splitlines()[0]
assert '0.3.0' in first, first
src = src.replace(first, '# MoRay 本地薄后端（产品 v1.0.0 · 构建 3.18.0）', 1)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('bumped server/README head')

# 5) verify 脚本 glob 目录名 → MoRay-v1.0.0
for sp in ['scripts/verify_build_05.py', 'scripts/verify_build_15.py', 'scripts/verify_build_s1.py', 'scripts/hash_four2.py']:
    s = io.open(sp, encoding='utf-8').read()
    if 'MoRay-v0.3.0' in s:
        s = s.replace('MoRay-v0.3.0', 'MoRay-v1.0.0')
        io.open(sp, 'w', encoding='utf-8', newline='').write(s)
        print('glob updated:', sp)
print('done')
