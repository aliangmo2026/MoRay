# -*- coding: utf-8 -*-
"""栈式定位未闭合的 <div>（忽略注释/字符串）。"""
import io
import re

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    text = f.read()

# 去掉注释块
text = re.sub(r'<!--.*?-->', '', text, flags=re.S)

# 词法扫描：按 <div ...> 与 </div>（标签内无 > 的字符串）
pattern = re.compile(r'<(/?)(div)\b[^>]*>', re.I)
stack = []
for m in pattern.finditer(text):
    line = text[:m.start()].count('\n') + 1
    if m.group(1) == '/':
        if stack:
            stack.pop()
        else:
            print('多余闭合 @ 行', line)
    else:
        stack.append((m.group(2), line, m.group(0)[:60]))

print('未闭合 div 数量:', len(stack))
for tag, line, snippet in stack:
    print('  行 %d: %s' % (line, snippet))
