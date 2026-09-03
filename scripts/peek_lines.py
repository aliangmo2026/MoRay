# -*- coding: utf-8 -*-
"""查看 moray-workbench.html 指定行范围。"""
import io
import sys

start = int(sys.argv[1]) if len(sys.argv) > 1 else 6520
end = int(sys.argv[2]) if len(sys.argv) > 2 else 6536
with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    lines = f.read().split('\n')
for i in range(start, min(end + 1, len(lines) + 1)):
    print('%d: %s' % (i, lines[i - 1][:130]))
