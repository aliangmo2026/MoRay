# -*- coding: utf-8 -*-
import io
lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.strip()
    if t.startswith('function sendUserMessage') or t.startswith('async function sendUserMessage') \
            or t.startswith('function sendChatMessage') or t.startswith('async function sendChatMessage') \
            or ('sendUserMessage =' in t):
        print(i + 1, lines[i].rstrip()[:200])
        for j in range(i, min(i + 6, len(lines))):
            print('   ', lines[j].rstrip()[:160])
