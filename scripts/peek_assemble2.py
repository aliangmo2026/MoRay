# -*- coding: utf-8 -*-
import io

lines = io.open('assemble.py', encoding='utf-8').readlines()
for i, l in enumerate(lines):
    if '--web' in l or '_args.web' in l or 'web_dir' in l:
        print(i + 1, l.rstrip()[:140])
