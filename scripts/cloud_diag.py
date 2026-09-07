# -*- coding: utf-8 -*-
"""云端冒烟 502 诊断：读响应体 message + 打印 .env base_url（不打印 key）"""
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server")
VENV_PY = os.path.join(SERVER, ".venv", "Scripts", "python.exe")

env = dict(os.environ)
env["MORAY_DB"] = os.path.join(tempfile.gettempdir(), "moray_cloud_smoke.sqlite3")
proc = subprocess.Popen([VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                         "--port", "8016", "--log-level", "warning"],
                        cwd=SERVER, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8016"
try:
    for _ in range(60):
        try:
            if json.load(urllib.request.urlopen(BASE + "/api/health", timeout=1)).get("ok"):
                break
        except Exception:
            time.sleep(0.4)
    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False, "max_tokens": 8,
    }
    req = urllib.request.Request(BASE + "/api/llm/chat", method="POST",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        j = json.load(urllib.request.urlopen(req, timeout=60))
        print("no-tools plain:", j.get("ok"), repr((j.get("content") or "")[:80]))
    except urllib.error.HTTPError as e:
        print("plain HTTP", e.code, e.read().decode("utf-8", errors="replace")[:400])
finally:
    proc.kill()
