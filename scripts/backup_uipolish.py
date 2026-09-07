# -*- coding: utf-8 -*-
"""阶段1.5 备份：backup/pre_ui_polish_<ts>（排除 backup/.venv 等）"""
import os
import shutil
import time

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'pre_ui_polish_' + ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'venv'}


def ignore_fn(directory, entries):
    return [e for e in entries if e in EXCLUDE_DIRS or e.endswith(('.pyc', '.pyo'))]


shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
n = sum(len(fns) for _, _, fns in os.walk(dest))
print('BACKUP_OK dest=%s files=%d' % (dest, n))
