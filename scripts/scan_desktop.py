# -*- coding: utf-8 -*-
"""检查桌面同步目标(MoRay工作台/backup/Cloudflare zip)"""
import os

d = os.path.join(os.path.expanduser('~'), 'Desktop')
print('desktop:', d, 'exists:', os.path.isdir(d))
if os.path.isdir(d):
    for f in sorted(os.listdir(d)):
        full = os.path.join(d, f)
        if 'moray' in f.lower() or 'moway' in f.lower() or 'cloudflare' in f.lower() or 'backup' in f.lower():
            print(' -', f, '(dir)' if os.path.isdir(full) else '(file)')
# 可能桌面在 OneDrive
for base in [os.path.expanduser('~'), os.path.join(os.path.expanduser('~'), 'OneDrive'), os.path.join(os.path.expanduser('~'), 'OneDrive', '桌面'), os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop')]:
    p = os.path.join(base, 'Desktop')
    if os.path.isdir(p):
        print('found desktop:', p)
        for f in sorted(os.listdir(p)):
            full = os.path.join(p, f)
            if 'moray' in f.lower() or 'cloudflare' in f.lower() or 'backup' in f.lower():
                print('   -', f, '(dir)' if os.path.isdir(full) else '(file)')
