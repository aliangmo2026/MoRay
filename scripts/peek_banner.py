# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'function refreshNativeAgentBanner' in l]
i = hits[0]
for j in range(i + 27, i + 44):
    print(j + 1, lines[j].rstrip()[:150])
