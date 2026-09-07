# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
print('total', len(lines))
for i, l in enumerate(lines):
    t = l.rstrip()
    if ('function renderMessages' in t) or ('chatMessagesNormal' in t and 'innerHTML' in t) \
            or ('renderMessages =' in t) or ('function fillAssistantBody' in t) \
            or ('welcome-screen' in t and ('innerHTML' in t or 'display' in t)):
        print(i + 1, t[:170])
