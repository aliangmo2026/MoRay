# -*- coding: utf-8 -*-
"""修复 PowerShell 误写的字面 `n 为真实换行。"""
import io

p = r'D:\ai工具台\parts\30_chat.js'
with io.open(p, encoding='utf-8') as f:
    c = f.read()

# 字面反引号 n（`` + n）替换为真实换行
before = c.count('`n')
c = c.replace('`n', '\n')
with io.open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(c)
print('替换字面`n:', before, '处')
