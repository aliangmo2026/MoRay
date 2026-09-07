# -*- coding: utf-8 -*-
"""全 parts 找 window.MorayBackend 赋值/探测"""
import io, glob

for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    hits = [i for i, l in enumerate(lines) if 'MorayBackend' in l and ('window' in l or 'W.MorayBackend' in l or 'MorayBackend =' in l)]
    if hits:
        print('==', path)
        for i in hits:
            print(i + 1, lines[i].rstrip()[:180])
