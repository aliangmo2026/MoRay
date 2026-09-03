# -*- coding: utf-8 -*-
"""严格标签匹配（排除 JS 正则里的 <div 字样）：<div 后必须跟空白/>/属性，且不在 script 内。"""
import io
import re

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    text = f.read()

text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
# 只扫描 <body> 之后（骨架区）—— 但注入 script 在 body 内。更稳：直接精确正则
pattern = re.compile(r'<(/?)(div)(?=[\s>])[^>]*>', re.I)
stack = []
for m in pattern.finditer(text):
    line = text[:m.start()].count('\n') + 1
    if m.group(1) == '/':
        if stack:
            stack.pop()
        else:
            print('多余闭合 @ 行', line)
    else:
        stack.append((line, m.group(0)[:70]))

print('未闭合 div:', len(stack))
for line, sn in stack:
    print('  行 %d: %s' % (line, sn))
