# -*- coding: utf-8 -*-
import io
lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i in range(952, 1090):
    print(i + 1, lines[i].rstrip()[:150])
