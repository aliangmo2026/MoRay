# -*- coding: utf-8 -*-
"""定位 20_ai.js 的括号/引号不平衡点（词法扫描，忽略字符串/注释/正则）。"""
import io
import re

with io.open(r'D:\ai工具台\parts\20_ai.js', encoding='utf-8') as f:
    text = f.read()

lines = text.split('\n')
stack = []
for i, line in enumerate(lines, 1):
    # 粗略词法：去掉字符串字面量（简单处理引号）
    s = re.sub(r'`[^`]*`', '``', line)
    s = re.sub(r'"[^"]*"', '""', s)
    s = re.sub(r"'[^']*'", "''", s)
    s = re.sub(r'//.*$', '', s)
    for ch in s:
        if ch in '({[':
            stack.append((ch, i))
        elif ch in ')}]':
            if not stack:
                print('多余闭合 %s @ 行 %d' % (ch, i))
            else:
                o, oi = stack.pop()
                pair = {'(': ')', '{': '}', '[': ']'}[o]
                if ch != pair:
                    print('不匹配 %s(行%d) vs %s(行%d)' % (o, oi, ch, i))
print('栈剩余:', len(stack))
for ch, i in stack[:10]:
    print('  未闭合 %s @ 行 %d' % (ch, i))
