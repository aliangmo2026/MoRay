# -*- coding: utf-8 -*-
"""查看桌面快捷方式目标与可能的外部 backup 位置"""
import os
import subprocess

lnk = os.path.join(os.path.expanduser('~'), 'Desktop', 'MoRay 工作台.lnk')
out = subprocess.run(['powershell', '-NoProfile', '-Command',
                      '(New-Object -ComObject WScript.Shell).CreateShortcut("' + lnk + '").TargetPath'],
                     capture_output=True)
print('lnk target:', out.stdout.decode('utf-8', errors='replace').strip() or out.stderr.decode('utf-8', errors='replace')[:200])

# 检查常见 backup 位置
for p in [r'D:\ai工具台\backup', r'D:\MoRay工作台', r'D:\MoRayWorkspace']:
    print(p, '->', 'exists' if os.path.isdir(p) else 'no')
for p in [r'D:\ai工具台\backup']:
    if os.path.isdir(p):
        print('backup contents:', sorted(os.listdir(p))[-12:])
