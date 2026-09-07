# -*- coding: utf-8 -*-
"""阶段A备份:整目录备份到 backup/pre_agent_<时间戳>,排除 backup 自身与 .venv/__pycache__。"""
import shutil, sys, time, os

ROOT = r"D:\ai工具台"
ts = time.strftime("%Y%m%d_%H%M%S")
dest = os.path.join(ROOT, "backup", f"pre_agent_{ts}")

EXCLUDE_DIRS = {"backup", ".venv", "__pycache__", ".git", "node_modules", "venv"}

def ignore_fn(directory, entries):
    ignored = []
    base = os.path.basename(directory)
    # 顶层遇到 backup 直接整目录跳过由 EXCLUDE 处理,这里再兜底
    for e in entries:
        if e in EXCLUDE_DIRS:
            ignored.append(e)
        elif e.endswith((".pyc", ".pyo")):
            ignored.append(e)
    return ignored

shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
# 统计
n_files = 0
n_dirs = 0
for dp, dns, fns in os.walk(dest):
    n_dirs += len(dns)
    n_files += len(fns)
size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(dest) for f in fns)
print("BACKUP_OK")
print("dest =", dest)
print("files =", n_files, "dirs =", n_dirs, "size_MB = %.1f" % (size / 1048576))
