# -*- coding: utf-8 -*-
import io
lines = io.open('scripts/test_agent_tools.py', encoding='utf-8').readlines()
for i, l in enumerate(lines):
    if '&&' in l or 'B38' in l:
        print(i + 1, repr(l))
