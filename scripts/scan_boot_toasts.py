# -*- coding: utf-8 -*-
import io
import glob

for path in ['parts/70_models_settings_boot.js', 'parts/110_polish.js', 'parts/95_features.js', 'parts/30_chat.js', 'parts/20_ai.js']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        if '本地AI工作台启动完成' in l or ("'MoRay 已就绪'" in l) or ('MoRay 已就绪' in l and 'showNotification' in l):
            print('%s:%d' % (path, i + 1), l.rstrip()[:170])
