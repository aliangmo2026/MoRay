# -*- coding: utf-8 -*-
import io

lines = io.open('CHANGELOG.md', encoding='utf-8').readlines()
for i, l in enumerate(lines):
    if l.startswith('## '):
        print(i + 1, l.rstrip()[:70])
    if i > 42:
        break
