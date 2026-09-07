# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.rstrip()
    if 'typingEl' in t or ('stream-cursor' in t and 'remove' in t):
        print(i + 1, t[:150])
