# -*- coding: utf-8 -*-
"""阶段0.5 通读:对比串行停止 / 60s 定时器 / 消息 DOM 结构"""
import io
import glob

# 1) 对比串行 + 停止
print('===== compare serial/stop =====')
lines = io.open('parts/40_compare_prompts.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.rstrip()
    if any(k in t for k in ('串行', 'serial', 'CompareStop', 'compareStop', 'compareAbort', 'compareCtrl',
                            '未开始', 'stopCompare', 'abortCompare', 'CompareApp.stop', 'running')):
        print(i + 1, t[:170])

print()
print('===== 60s timers across parts =====')
for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        if 'setInterval' in l and ('60000' in l or '60 ' in l or '60*' in l or '60 *' in l or '60000' in l):
            print('%s:%d: %s' % (path, i + 1, l.strip()[:150]))

print()
print('===== message DOM id =====')
lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    t = l.rstrip()
    if 'data-msg-id' in t or ("function buildMessageEl" in t):
        print(i + 1, t[:170])
