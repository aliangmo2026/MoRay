# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'syncNativeAgentBtn' in l or 'toggleNativeAgent' in l]
for i in hits:
    print(i + 1, lines[i].rstrip()[:130])
