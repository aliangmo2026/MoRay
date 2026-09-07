# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if ('agentSysPromptOverride' in l) or ('[阶段1 M5] 本机 Agent 系统提示' in l) or ('if (sys) msgs.push' in l):
        print(i + 1, l.rstrip()[:140])
