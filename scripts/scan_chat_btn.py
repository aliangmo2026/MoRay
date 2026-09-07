# -*- coding: utf-8 -*-
"""定位 30_chat.js 输入台按钮区(think 按钮/chatInputNormal/发送按钮)"""
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
out = []
for i, l in enumerate(lines):
    if ('deepThink' in l) or ('thinkMode' in l and 'get(' not in l) or ('chatInputNormal' in l) \
            or ('renderChatInput' in l) or ('buildChatInput' in l) or ('深度思考' in l):
        out.append('%d: %s' % (i + 1, l.rstrip()[:160]))
open('work/scan_chat_btn.txt', 'w', encoding='utf-8').write('\n'.join(out))
print(len(out), 'hits -> work/scan_chat_btn.txt')
