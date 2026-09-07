# -*- coding: utf-8 -*-
import io

src = io.open('assemble.py', encoding='utf-8').read()
for kw in ["'sw.js'", '"sw.js"', 'sw_template', 'SW_TEMPLATE']:
    i = 0
    while True:
        idx = src.find(kw, i)
        if idx < 0:
            break
        print('--- at', idx, repr(src[idx - 120:idx + 160]))
        i = idx + 1
