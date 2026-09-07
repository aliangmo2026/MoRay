# -*- coding: utf-8 -*-
import io

lines = io.open('parts/70_models_settings_boot.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if ('数据' in l and ('管理' in l or '备份' in l or '清空' in l)) or ('card-data' in l) or ('exportData' in l):
        print(i + 1, l.rstrip()[:140])
