# -*- coding: utf-8 -*-
"""扫描骨架区（原型 CSS）硬编码深色，输出定位上下文，供浅色主题适配决策。"""
import io
import re

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    text = f.read()

# 只扫骨架区（注入块之前 = 原型 CSS；注入块内是 parts，改动走 parts）
marker = 'MoRay v2.0 应用层（第二阶段）'
cut = text.find(marker)
skeleton = text[:cut]
lines = skeleton.split('\n')

patterns = {
    'surface-void #0A0C14': r'#0A0C14',
    'surface-deep #10131D': r'#10131D',
    'surface-panel #151927': r'#151927',
    'surface-card #1A1F30': r'#1A1F30',
    'surface-floating #212736': r'#212736',
    'surface-hover #262D40': r'#262D40',
    'line-ghost #262C3E': r'#262C3E',
    'code-bg #0D1119': r'#0D1119',
    'deep-shadow rgba(10,12,20': r'rgba\(\s*10\s*,\s*12\s*,\s*20',
    'light-text rgba(232,236,245': r'rgba\(\s*232\s*,\s*236\s*,\s*245',
}

for name, pat in patterns.items():
    hits = []
    for i, line in enumerate(lines, 1):
        if re.search(pat, line, re.I):
            hits.append((i, line.strip()[:90]))
    if hits:
        print('== %s (%d 处)' % (name, len(hits)))
        for ln, sn in hits[:8]:
            print('  %d: %s' % (ln, sn))
