# -*- coding: utf-8 -*-
"""阶段C 单测：llm_proxy tools/tool_choice 透传 + tool_calls 响应透传，且无 tools 旧请求零变化。

拓扑：fake 上游(8898, 记录收到的 payload) <- uvicorn llm_proxy(8012, MORAY_LLM_* env 指向 fake)。
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "..", "server")
VENV_PY = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")

received = []

TOOLS = [
    {"type": "function", "function": {"name": "get_current_time", "description": "d", "parameters": {"type": "object", "properties": {}}}}
]


class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        received.append(body)
        has_tools = isinstance(body.get("tools"), list) and body.get("tools")
        if has_tools:
            payload = {
                "id": "chatcmpl-fake1",
                "choices": [{"index": 0, "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call_1", "type": "function",
                                    "function": {"name": "get_current_time", "arguments": "{}"}}],
                }, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        else:
            payload = {
                "id": "chatcmpl-fake2",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "普通回答"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }
        stream = body.get("stream", False)
        if stream:
            # 上游按 SSE 返回：data: {json}\n\n + [DONE]
            data = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # 静默
        pass


def main():
    up = HTTPServer(("127.0.0.1", 8898), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["MORAY_DB"] = os.path.join(os.environ.get("TEMP", "."), "moray_llmproxy_test.sqlite3")
    env["MORAY_LLM_BASE_URL"] = "http://127.0.0.1:8898/v1"
    env["MORAY_LLM_API_KEY"] = "sk-test123456"
    env["MORAY_LLM_MODEL"] = "deepseek-chat"
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8012", "--log-level", "warning"],
        cwd=SERVER_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:8012"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        fails = []

        def check(name, cond, detail=""):
            print(("PASS " if cond else "FAIL ") + name + (" | " + str(detail)[:200] if detail else ""))
            if not cond:
                fails.append(name)

        # 1) 无 tools 旧请求：payload 无 tools 键、响应无 tool_calls、content 正常
        r = httpx.post(base + "/api/llm/chat", json={
            "model": "deepseek-chat", "messages": [{"role": "user", "content": "你好"}], "stream": False
        }, timeout=15)
        j = r.json()
        check("C1 无tools请求 200 ok", r.status_code == 200 and j.get("ok"), j)
        check("C2 无tools内容正确", j.get("content") == "普通回答", j)
        check("C3 无tools响应无tool_calls键", "tool_calls" not in j, j)
        check("C4 上游payload无tools键", "tools" not in received[0] and "tool_choice" not in received[0], received[0])

        # 2) 带 tools 请求：透传 tools + tool_choice
        r = httpx.post(base + "/api/llm/chat", json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "现在几点"}],
            "stream": False, "tools": TOOLS, "tool_choice": "auto",
        }, timeout=15)
        j = r.json()
        check("C5 带tools请求 200 ok", r.status_code == 200 and j.get("ok"), j)
        check("C6 上游收到tools透传", received[-1].get("tools") == TOOLS, received[-1].get("tools"))
        check("C7 上游收到tool_choice=auto", received[-1].get("tool_choice") == "auto", received[-1].get("tool_choice"))
        check("C8 响应透传tool_calls", j.get("tool_calls") and j["tool_calls"][0]["function"]["name"] == "get_current_time", j)

        # 3) tools 为空数组时不上发 tools 键（防空 tools 破坏上游）
        before = len(received)
        r = httpx.post(base + "/api/llm/chat", json={
            "model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}],
            "stream": False, "tools": [], "tool_choice": "none",
        }, timeout=15)
        check("C9 空tools数组不破坏请求", r.status_code == 200 and r.json().get("ok"), r.text[:200])
        check("C10 空tools未上发tools键", len(received) == before + 1 and "tools" not in received[-1], received[-1] if len(received) > before else None)
        check("C11 tool_choice=none 透传", received[-1].get("tool_choice") == "none", received[-1] if len(received) > before else None)

        # 4) 流式请求（无 tools）仍逐字节透传
        with httpx.stream("POST", base + "/api/llm/chat", json={
            "model": "deepseek-chat", "messages": [{"role": "user", "content": "s"}], "stream": True,
        }, timeout=15) as sr:
            chunk = next(sr.iter_lines())
            check("C12 无tools流式兼容", chunk.startswith("data: {"), chunk[:120])

        print("\n结果:", "全部通过 ✔" if not fails else ("失败: " + ", ".join(fails)))
        sys.exit(0 if not fails else 1)
    finally:
        proc.kill()
        up.shutdown()


if __name__ == "__main__":
    main()
