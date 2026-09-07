# -*- coding: utf-8 -*-
import io

p110 = io.open('parts/110_polish.js', encoding='utf-8-sig', errors='replace').readlines()
print('== 110 启动通知与文案 ==')
for i, l in enumerate(p110):
    t = l.rstrip()
    if 'showNotification' in t or '离线模式' in t or '纯前端' in t or '已就绪' in t:
        print(i + 1, t[:170])

p70 = io.open('parts/70_models_settings_boot.js', encoding='utf-8-sig', errors='replace').readlines()
print()
print('== 70 设置页关键点 ==')
for i, l in enumerate(p70):
    t = l.rstrip()
    if ('gpt-4o-mini' in t) or ('设为默认' in t) or ('删除' in t and 'model' in t.lower()) or ('setupSettingsSideNav' in t) or ('showNotification' in t and '就绪' in t):
        print(i + 1, t[:170])
