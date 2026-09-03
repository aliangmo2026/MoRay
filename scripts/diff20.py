# -*- coding: utf-8 -*-
"""精确对比 20_ai.js 当前版与备份版（仅打印内容真正不同的行）。"""
import io

with io.open(r'D:\ai工具台\parts\20_ai.js', encoding='utf-8') as f:
    cur = f.read().split('\n')
with io.open(r'D:\ai工具台\backup\pre_modelFix_20260830_163816\parts\20_ai.js', encoding='utf-8') as f:
    old = f.read().split('\n')

print('当前行数:', len(cur), ' 备份行数:', len(old))
# 找第一个内容不同的行（忽略行尾空白）
diffs = 0
for i in range(max(len(cur), len(old))):
    c = cur[i].rstrip() if i < len(cur) else '<EOF>'
    o = old[i].rstrip() if i < len(old) else '<EOF>'
    if c != o:
        diffs += 1
        if diffs <= 25:
            print('行 %d:' % (i + 1))
            print('  备份: %s' % o[:100])
            print('  当前: %s' % c[:100])
print('总差异行:', diffs)
