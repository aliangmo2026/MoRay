# -*- coding: utf-8 -*-
import io

for path in ['parts/70_models_settings_boot.js', 'parts/110_polish.js', 'parts/95_features.js']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        t = l.rstrip()
        if ('启动完成' in t) or ('已就绪' in t and ('showNotification' in t or 'title' in t or 'wel' in t.lower())):
            print('%s:%d' % (path, i + 1), t[:180])
