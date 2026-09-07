# -*- coding: utf-8 -*-
import io

lines = io.open('scripts/mock_llm_server.py', encoding='utf-8').readlines()
hits = [i for i, l in enumerate(lines) if 'def main' in l]
i = hits[0]
for j in range(i, i + 8):
    print(j + 1, lines[j].rstrip()[:130])
