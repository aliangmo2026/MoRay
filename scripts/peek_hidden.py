# -*- coding: utf-8 -*-
import io

lines = io.open('parts/70_models_settings_boot.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if 'document.hidden' in l:
        print(i + 1, l.rstrip()[:120])
