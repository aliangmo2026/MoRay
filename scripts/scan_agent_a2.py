# -*- coding: utf-8 -*-
"""阶段A通读:MoraySettings 定义 + chatStream tools + 30_chat 发送入口"""
import io

def scan(path, kws, ctx=1, maxshow=200):
    print('==', path)
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    hits = [i for i, l in enumerate(lines) if any(k in l for k in kws)]
    shown = []
    for i in hits:
        for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
            shown.append(j)
    prev = -10
    for j in sorted(set(shown))[:maxshow]:
        if j - prev > 2:
            print('   ...')
        print(j + 1, lines[j].rstrip()[:170])
        prev = j

scan('parts/10_db.js', ['MoraySettings =', 'const MoraySettings', 'toolsEnabled'], ctx=0)
print()
scan('parts/20_ai.js', ['tools', 'toolCalls'], ctx=0)
print()
scan('parts/30_chat.js', ['onToolStep', 'noTools', 'chatStream(', 'chatStream({'], ctx=0)
print()
scan('parts/70_models_settings_boot.js', ['DEFAULT', 'defaults'], ctx=0)
