# -*- coding: utf-8 -*-
"""任务A：把 parts/*.js 中三类裸链式事件绑定改为 on() 安全绑定。
  A. X.querySelector('sel').addEventListener(t, fn)  -> on(X.querySelector('sel'), t, fn)
  B. document.getElementById('id').addEventListener  -> on(document.getElementById('id'), ...)
  C. $('id').addEventListener                        -> on($('id'), ...)
括号/字符串感知回溯 receiver；只改"元素查询链直接 addEventListener"，其余不动。
"""
import io
import re
import glob

def find_receiver_start(text, dot_pos):
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


changed_total = 0
for path in sorted(glob.glob(r'D:\ai工具台\parts\*.js')):
    with io.open(path, encoding='utf-8') as f:
        text = f.read()
    out = []
    last = 0
    n = 0
    for m in re.finditer(r'addEventListener\s*\(', text):
        dot = m.start() - 1
        while dot >= 0 and text[dot] in ' \t\n':
            dot -= 1
        if dot < 0 or text[dot] != '.':
            continue
        start = find_receiver_start(text, dot)
        receiver = text[start:dot].strip()
        kind = classify(receiver)
        if not kind:
            continue
        # 拼接：保留 last..start 原文 + on(RECEIVER, + 原参数（m.end() 之后）
        out.append(text[last:start])
        out.append('on(' + receiver + ', ')
        last = m.end()
        n += 1
        line = text[:start].count('\n') + 1
        print('%s:%d  [%s]  %s' % (path.split('\\')[-1], line, kind, receiver[:70]))
    if n:
        out.append(text[last:])
        with io.open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(''.join(out))
        changed_total += n
print('TOTAL converted:', changed_total)
