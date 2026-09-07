# -*- coding: utf-8 -*-
"""E2E 收尾清理：按 e2e_env.json 杀进程 + 删临时库/工作区（供浏览器测试后调用）"""
import json
import os
import shutil
import subprocess
import sys

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "e2e_env.json")
if not os.path.exists(p):
    print("no env file")
    sys.exit(0)
env = json.load(open(p, encoding="utf-8"))
for key in ("mock_pid", "server_pid"):
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(env[key])], capture_output=True)
    except Exception:
        pass
for d in ("workspace",):
    shutil.rmtree(env.get(d, ""), ignore_errors=True) if env.get(d) else None
try:
    os.remove(env.get("db", ""))
except OSError:
    pass
print("cleaned:", env.get("workspace"), env.get("db"))
