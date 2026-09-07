# -*- coding: utf-8 -*-
import io
import glob

for p in glob.glob('parts/*.js'):
    lines = io.open(p, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        if 'function showTypingIndicator' in l or 'function finalizeAssistantUI' in l:
            print('==', p, i + 1)
            for j in range(i, min(i + 15, len(lines))):
                print(j + 1, lines[j].rstrip()[:150])
