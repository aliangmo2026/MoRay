# -*- coding: utf-8 -*-
"""阶段A通读辅助:定位关键行"""
import io

def scan(path, kws, ctx=0):
    print('==', path)
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    hits = [i for i, l in enumerate(lines) if any(k in l for k in kws)]
    shown = set()
    for i in hits:
        for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
            if j not in shown:
                shown.add(j)
                print(j + 1, lines[j].rstrip()[:160])

for p in ['parts/20_ai.js', 'parts/30_chat.js']:
    scan(p, ['toolCalls', 'onToolStep', 'tool_calls', '"tools"', "'tools'", 'tools:'], ctx=0)

print()
for p in ['parts/10_db.js']:
    scan(p, ['toolsEnabled', 'toolsDisabled', 'toolsMaxRounds', 'DEFAULT_SETTINGS', 'agentNative', 'agentApproval'], ctx=0)

print()
scan('parts/70_models_settings_boot.js', ['APP_VERSION', 'MORAY_VERSION', 'DEFAULT_SETTINGS'], ctx=0)
scan('parts/110_polish.js', ['MORAY_BUILD', 'MORAY_VERSION'], ctx=0)
