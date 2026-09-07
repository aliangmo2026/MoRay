# -*- coding: utf-8 -*-
import io

p70 = io.open('parts/70_models_settings_boot.js', encoding='utf-8-sig', errors='replace').readlines()
print('== 70 关于页/版本显示 ==')
for i, l in enumerate(p70):
    if 'MORAY_VERSION' in l or ('版本' in l and ('v' in l and '0.3' in l or 'VERSION' in l)) or '关于 MoRay' in l:
        print(i + 1, l.rstrip()[:160])

pbr = io.open('scripts/build_release.py', encoding='utf-8-sig', errors='replace').readlines()
print('\n== build_release 命名 ==')
for i, l in enumerate(pbr):
    if 'v0.3.0' in l or '0.3.0' in l or 'VERSION' in l:
        print(i + 1, l.rstrip()[:160])
