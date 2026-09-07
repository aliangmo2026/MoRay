# -*- coding: utf-8 -*-
"""全 parts 扫描 UI 辅助函数定义"""
import io, glob

for path in sorted(glob.glob('parts/*.js')):
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        t = l.strip()
        for head in ('function showModal', 'function showConfirm', 'function showNotification',
                     'function on(', 'function escapeHtml', 'function refreshIcons',
                     'window.on =', 'function escapeAttr'):
            if t.startswith(head):
                print('%s:%d: %s' % (path, i + 1, t[:150]))
