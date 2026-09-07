# -*- coding: utf-8 -*-
import os
import shutil

b = r'D:\ai工具台\backup'
src = os.path.join(b, 'pre_agent_20260905_114207')
dst = os.path.join(b, 'pre_agent05_20260905_114207')
if os.path.isdir(src) and not os.path.isdir(dst):
    shutil.move(src, dst)
print('exists pre_agent05:', os.path.isdir(dst))
print('old gone:', not os.path.isdir(src))
