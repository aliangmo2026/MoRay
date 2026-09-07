# -*- coding: utf-8 -*-
"""阶段1 备份:整目录到 backup/pre_stage1_<ts>(排除 backup/.venv/__pycache__ 等)"""
import os
import shutil
import time

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'pre_stage1_' + ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'venv'}


def ignore_fn(directory, entries):
    ignored = []
    for e in entries:
        if e in EXCLUDE_DIRS or e.endswith(('.pyc', '.pyo')):
            ignored.append(e)
    return ignored


shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
n = sum(len(fns) for _, _, fns in os.walk(dest))
size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(dest) for f in fns)
print('BACKUP_OK dest=%s files=%d size_MB=%.1f' % (dest, n, size / 1048576))

# server/.env 云端 key 检查（不打印 key 内容）
env_path = os.path.join(ROOT, 'server', '.env')
if os.path.isfile(env_path):
    has_base = has_key = False
    for line in open(env_path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if s.startswith('MORAY_LLM_BASE_URL=') and len(s) > 20:
            has_base = True
        if s.startswith('MORAY_LLM_API_KEY=') and len(s.split('=', 1)[1].strip()) >= 8:
            has_key = True
    print('.env exists, base_url=%s, key=%s' % (has_base, has_key))
else:
    print('.env: NOT FOUND（无云端 key，冒烟将如实说明）')
