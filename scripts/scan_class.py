# -*- coding: utf-8 -*-
import io
lines = io.open('parts/20_ai.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.strip()
    if t.startswith('class ') or t.startswith('const AI') or t.startswith('MorayAI') or '= {' in t and ('AI' in t or 'Moray' in t):
        print(i + 1, l.rstrip()[:120])
