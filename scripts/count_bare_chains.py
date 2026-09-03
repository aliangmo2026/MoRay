# -*- coding: utf-8 -*-
"""统计 parts/*.js 中的裸链式事件绑定（不加保护直接 addEventListener 的元素查询链）。
裸链三类接收者：
  A. X.querySelector('sel').addEventListener(...)
  B. document.getElementById('id').addEventListener(...)  / X.getElementById(...)
  C. $('id').addEventListener(...)（局部 $ 助手）
"""
import io
import re
import glob

RECV_PATTERNS = [
    re.compile(r'[\w$\]]+\(\s*[\'"`][^()`]*[\'"`]\s*\)\s*$'),  # 以单层括号调用结尾
]

def find_receiver_start(text, dot_pos):
    """从 '.'（addEventListener 前的点）位置向前回溯，返回 receiver 表达式起点。"""
    depth = 0
    i = dot_pos - 1
    in_str = None
    while i >= 0:
        ch = text[i]
        if in_str:
            if ch == '\\':
                i -= 2
                continue
            if ch == in_str:
                in_str = None
            i -= 1
            continue
        if ch in ('"', "'", '`'):
            in_str = ch
        elif ch in (')', ']', '}'):
            depth += 1
        elif ch in ('(', '[', '{'):
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and not (ch.isalnum() or ch in '._$'):
            break
        i -= 1
    return i + 1


def classify(receiver):
    r = receiver.strip()
    if re.search(r'\.querySelector\s*\(', r) or re.search(r'\.querySelectorAll\s*\(', r):
        return 'querySelector'
    if re.search(r'getElementById\s*\(', r):
        return 'getElementById'
    if re.match(r'^\$\(', r):
        return 'dollar'
    return None


total = {}
for path in sorted(glob.glob(r'D:\ai工具台\parts\*.js')):
    with io.open(path, encoding='utf-8') as f:
        text = f.read()
    n = 0
    kinds = {}
    for m in re.finditer(r'addEventListener\s*\(', text):
        dot = m.start() - 1
        # receiver 结束于 '.'（跳过空白）
        while text[dot] in ' \t\n':
            dot -= 1
        if text[dot] != '.':
            continue  # 非链式（如 on(...) 内部、变量调用）跳过——这里统计的是 x.y 形式
        start = find_receiver_start(text, dot)
        receiver = text[start:dot].strip()
        kind = classify(receiver)
        if kind:
            n += 1
            kinds[kind] = kinds.get(kind, 0) + 1
            line = text[:m.start()].count('\n') + 1
    if n:
        total[path] = (n, kinds)

sum_all = 0
for path, (n, kinds) in total.items():
    sum_all += n
    print('%s: %d  %s' % (path.split('\\')[-1], n, kinds))
print('TOTAL bare chains:', sum_all)
