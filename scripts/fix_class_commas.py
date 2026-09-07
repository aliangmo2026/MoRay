# -*- coding: utf-8 -*-
"""去掉 class MorayAI 方法间误加的逗号（20_ai.js L493-540 插入区）"""
import io

path = 'parts/20_ai.js'
lines = io.open(path, encoding='utf-8-sig').readlines()
# 精确处理:toolArgsToObject/toolArgsToString/_ollamaMessages 三个方法结束的 '},' → '}'
fixed = 0
for i in range(490, 560):
    t = lines[i].rstrip('\n')
    if t.endswith('},') and i + 1 < len(lines) and ('/**' in lines[i + 1] or lines[i + 1].strip() == ''):
        lines[i] = t[:-1] + '\n'
        fixed += 1
        print('fixed line', i + 1)
io.open(path, 'w', encoding='utf-8', newline='').writelines(lines)
print('total fixed:', fixed)
