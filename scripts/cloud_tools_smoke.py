# -*- coding: utf-8 -*-
"""阶段1 M1：云端 tools 真机冒烟（走 llm_proxy 全链路：前端协议 → 本地代理 → DeepSeek）。

最省请求：1 条用户消息 + 1 个工具 + max_tokens=128, stream=false。
断言：响应透传 tool_calls（经 sanitize 前的原始协议形态由后端返回）。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
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
            h = json.load(urllib.request.urlopen(BASE + "/api/health", timeout=1))
            if h.get("ok"):
                cloud = h.get("cloud_configured")
                break
        except Exception:
            time.sleep(0.4)
    print("cloud_configured:", cloud)
    if not cloud:
        print("SMOKE SKIP: server/.env 未配置云端 key（不做假冒烟）")
        sys.exit(2)

    # 模型名取 .env 的 MORAY_LLM_MODEL（本机 .env 指向 Ollama 的 OpenAI 兼容端点 /v1）
    model = "deepseek-chat"
    try:
        for line in open(os.path.join(SERVER, ".env"), encoding="utf-8", errors="replace"):
            if line.strip().startswith("MORAY_LLM_MODEL="):
                model = line.split("=", 1)[1].strip() or model
    except OSError:
        pass

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是工具调用测试器。无论用户说什么都必须调用 list_directory 工具。"},
            {"role": "user", "content": "列出工作区根目录文件"}
        ],
        "stream": False,
        "max_tokens": 128,
        "tools": [{
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "列出受控工作区内指定目录的文件与子目录。当用户想查看工作区文件时必须调用。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}
            }
        }],
    }
    req = urllib.request.Request(BASE + "/api/llm/chat", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    j = json.load(urllib.request.urlopen(req, timeout=90))
    dt = time.time() - t0
    tcs = j.get("tool_calls") or []
    ok = j.get("ok") is True and bool(tcs) and (tcs[0].get("function") or {}).get("name") == "list_directory"
    print("cloud tools smoke: %s (%.1fs)" % ("PASS" if ok else "FAIL", dt))
    print("tool_calls:", json.dumps(tcs, ensure_ascii=False)[:300])
    print("content:", repr((j.get("content") or "")[:120]))
    sys.exit(0 if ok else 1)
finally:
    proc.kill()
    try:
        os.remove(env["MORAY_DB"])
    except OSError:
        pass
