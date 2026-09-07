# -*- coding: utf-8 -*-
"""批次备份：backup/pre_fix_<批次>_<ts>（排除 backup/.venv 等）"""
import os
import shutil
import sys
import time

ROOT = r'D:\ai工具台'
batch = sys.argv[1] if len(sys.argv) > 1 else 'b1'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'pre_fix_%s_%s' % (batch, ts))
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'venv'}


def ignore_fn(directory, entries):
    return [e for e in entries if e in EXCLUDE_DIRS or e.endswith(('.pyc', '.pyo'))]


shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
print('BACKUP_OK dest=%s' % dest)
