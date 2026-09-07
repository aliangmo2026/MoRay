# -*- coding: utf-8 -*-
"""E2E 环境启动器：mock LLM(8898) + 临时后端(8000, 临时 DB/workspace) + 预置工作区文件。
输出 PID 文件与准备状态。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "..", "server")
VENV_PY = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
ts = time.strftime("%Y%m%d_%H%M%S")
tmp = tempfile.gettempdir()

ws = os.path.join(tmp, "moray_e2e_ws_" + ts)
os.makedirs(ws, exist_ok=True)
# 场景4 预置文件
open(os.path.join(ws, "a.txt"), "w", encoding="utf-8").write("内容A：第一份素材")
open(os.path.join(ws, "b.txt"), "w", encoding="utf-8").write("内容B：第二份素材")
open(os.path.join(ws, "已有文件.txt"), "w", encoding="utf-8").write("预先存在的文件")

env = dict(os.environ)
env["MORAY_DB"] = os.path.join(tmp, "moray_e2e_%s.sqlite3" % ts)
env["MORAY_WORKSPACE"] = ws

# 启动 mock LLM
mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm_server.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
# 启动临时后端(8000)
srv = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                        "--port", "8000", "--log-level", "warning"],
                       cwd=SERVER_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

for _ in range(80):
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=1) as r:
            ok8000 = r.status == 200
            break
    except Exception:
        ok8000 = False
        time.sleep(0.3)
try:
    with urllib.request.urlopen("http://127.0.0.1:8898/v1/models", timeout=1) as r:
        okmock = r.status == 200
except Exception:
    okmock = False

state = {
    "mock_pid": mock.pid, "server_pid": srv.pid,
    "backend_ok": ok8000, "mock_ok": okmock,
    "workspace": ws,
    "db": env["MORAY_DB"],
    "backend_url": "http://127.0.0.1:8000",
    "mock_url": "http://127.0.0.1:8898",
}
print(json.dumps(state, ensure_ascii=False, indent=1))
