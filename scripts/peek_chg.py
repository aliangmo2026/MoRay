# -*- coding: utf-8 -*-
import io

lines = io.open('CHANGELOG.md', encoding='utf-8').readlines()
print('total:', len(lines))
for i, l in enumerate(lines[:14]):
    print(i + 1, l.rstrip()[:90])
