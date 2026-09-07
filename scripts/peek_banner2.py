# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
hits = [i for i, l in enumerate(lines) if 'banner.innerHTML' in l]
print('innerHTML at:', [h + 1 for h in hits])
if hits:
    i = hits[0]
    for j in range(i, min(i + 16, len(lines))):
        print(j + 1, lines[j].rstrip()[:150])
