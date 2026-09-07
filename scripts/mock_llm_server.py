# -*- coding: utf-8 -*-
"""阶段E mock LLM：OpenAI 兼容 /v1/chat/completions（非流式 JSON + 流式 SSE 双支持）。

行为由剧本驱动（POST /__ctl {"scenario":"list"} 切换）：
- 每收到一次非流式请求，按剧本进度返回下一项；请求消息含 role:"tool" 或 assistant tool_calls
  时自动推进（同剧本连续消耗）。
- 剧本项：{"content": "..."} 直接文本回复
         {"tool_calls": [{"name": "...", "arguments": {...}}]} 请求调用工具
         {"http_error": 400} 模拟"不支持 tools 的模型"（400 仅当请求体含 tools）
- 流式请求：转发同内容为 SSE delta（无 tools 的兜底流式重试用）。
- /__history 返回最近请求摘要（供断言：tools 是否携带等）。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib.parse

SCENARIOS = {
    "list": [
        {"tool_calls": [{"name": "list_directory", "arguments": {}}]},
        {"content": "工作区里当前没有额外文件。已用列目录工具确认。", "calls": 1},
    ],
    "write_ok": [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "hello.txt", "content": "你好"}}]},
        {"content": "已完成：hello.txt 已写入工作区，内容是“你好”。"},
    ],
    "write_deny": [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "nope.txt", "content": "x"}}]},
        {"content": "好的，已取消写入 nope.txt，没有创建任何文件。"},
    ],
    "multi": [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "b.txt"}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "summary.txt", "content": "A+B 汇总"}}]},
        {"content": "已读取 a.txt 与 b.txt 并写入 summary.txt。"},
    ],
    "multi_slow": [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}], "delay_ms": 2500},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "b.txt"}}], "delay_ms": 2500},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "summary2.txt", "content": "不该出现"}}], "delay_ms": 2500},
        {"content": "多轮完成（此响应不应出现——应被用户停止打断）。"},
    ],
    "escape": [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "C:\\Windows\\win.ini"}}]},
        {"content": "抱歉，读取 C:\\Windows\\win.ini 被安全层拒绝：绝对路径不允许，工具只限工作区内。"},
    ],
    "s7_plan": [
        {"tool_calls": [{"name": "submit_plan", "arguments": {"steps": [
            {"title": "查找工作区 txt 文件", "tool": "find_files"},
            {"title": "搜索关键内容", "tool": "search_text"},
            {"title": "汇总告知用户", "tool": ""}
        ]}}]},
        {"tool_calls": [{"name": "find_files", "arguments": {"pattern": "*.txt"}}]},
        {"tool_calls": [{"name": "search_text", "arguments": {"query": "内容A素材"}}]},
        {"content": "计划 3 步已全部完成：找到 txt 文件并确认 a.txt 含『内容A素材』。"},
    ],
    "s8_edit": [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "e2e_edit.txt", "content": "AAA BBB CCC"}}]},
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "e2e_edit.txt", "old_str": "BBB", "new_str": "DDD"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "e2e_edit.txt"}}]},
        {"content": "已把 e2e_edit.txt 中的 BBB 改为 DDD。"},
    ],
    "s9_edit_deny": [
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "e2e_edit.txt", "old_str": "DDD", "new_str": "X"}}]},
        {"content": "好的，已取消编辑，文件内容保持不变。"},
    ],
    "s10_demo": [
        {"tool_calls": [{"name": "submit_plan", "arguments": {"steps": [
            {"title": "查找工作区 txt 文件", "tool": "find_files"},
            {"title": "读取素材 A（notes/a.txt）", "tool": "read_file"},
            {"title": "读取素材 B（notes/b.txt）", "tool": "read_file"},
            {"title": "更新待办清单标记进度", "tool": "edit_file"},
            {"title": "汇总两份素材要点", "tool": ""}]}}]},
        {"tool_calls": [{"name": "find_files", "arguments": {"pattern": "*.txt"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "notes/a.txt"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "notes/b.txt"}}]},
        {"tool_calls": [{"name": "edit_file", "arguments": {"path": "todo.md", "old_str": "把两个素材的结论写入 summary.md", "new_str": "把两个素材的结论写入 summary.md（已完成）"}}]},
        {"content": "两份素材要点已汇总：A 是项目启动会要点（目标/分工/风险），B 是用户调研记录（示例数据/出处/先读后改）。todo.md 已标记进度完成。"},
    ],
    "stop_w": [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "b.txt"}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "stop_me.txt", "content": "不应存在"}}]},
        {"content": "多轮完成（此响应不应出现——应在审批等待时被用户停止打断）。"},
    ],
    "no_tools_model": [
        # 模拟模型不支持 tools：带 tools 的请求一律 400（不消耗剧本）；无 tools 请求正常回答
        {"content": "当前模型是纯对话模型，不支持工具调用。无法访问工作区，请直接告诉我你需要的文字帮助。"},
    ],
    "plain_tools_model": [
        # 模型支持 tools 但选择直接回答（无 tool_calls，无死循环）
        {"content": "（直接回答，没有调用任何工具）"},
    ],
}

history = []
state = {"scenario": "list", "idx": 0}
lock = threading.Lock()


def _usage(prompt, comp):
    return {"prompt_tokens": prompt, "completion_tokens": comp, "total_tokens": prompt + comp}


class Handler(BaseHTTPRequestHandler):
    def _headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._headers()
        self.end_headers()

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, obj):
        data = ("data: " + json.dumps(obj) + "\n\ndata: [DONE]\n\n").encode("utf-8")
        self.send_response(200)
        self._headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        try:
            return self._do_post()
        except Exception as e:  # noqa: BLE001 - 调试辅助：打印并返回错误（测试期可见）
            import traceback
            traceback.print_exc()
            try:
                self._json({"ok": False, "error": "mock internal: %s" % type(e).__name__}, 500)
            except Exception:
                pass

    def _do_post(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        path = urllib.parse.urlparse(self.path).path
        if path == "/__ctl":
            body = json.loads(raw)
            with lock:
                state["scenario"] = body.get("scenario", "list")
                state["idx"] = 0
                history.clear()
            return self._json({"ok": True, "scenario": state["scenario"]})
        if path == "/__history":
            with lock:
                return self._json({"ok": True, "history": list(history)})
        if path == "/__state":
            with lock:
                return self._json({"ok": True, "state": dict(state)})
        if not path.endswith("/chat/completions"):
            return self._json({"ok": False, "error": {"message": "unknown path " + path}}, 404)
        body = json.loads(raw)
        stream = bool(body.get("stream", False))
        has_tools = bool(body.get("tools"))
        msgs = body.get("messages", [])
        roles = [m.get("role") for m in msgs]
        last_tool_msgs = sum(1 for r in roles if r == "tool")
        last_tool_calls = sum(1 for m in msgs if m.get("role") == "assistant" and m.get("tool_calls"))
        with lock:
            history.append({
                "n": len(history), "has_tools": has_tools, "roles": roles[-6:],
                "tool_msgs": last_tool_msgs, "assistant_tool_calls": last_tool_calls,
                "stream": stream,
            })
            if history and len(history) > 60:
                history.pop(0)
            scenario = SCENARIOS.get(state["scenario"], [])
            idx = min(state["idx"], len(scenario) - 1) if scenario else 0
            # 模型"不支持 tools"场景：带 tools 一律 400（触发前端普通流式兜底）
            if state["scenario"] == "no_tools_model" and has_tools:
                return self._json({"error": {"message": "tool_use not supported by this model"}}, 400)
            step = scenario[idx] if scenario else None
            state["idx"] += 1
        if step is None:
            step = {"content": "（mock 剧本耗尽）"}
        # delay 在锁外执行（多线程 server 下不阻塞控制接口）
        if step.get("delay_ms"):
            time.sleep(int(step["delay_ms"]))
        if step.get("http_error"):
            return self._json({"error": {"message": "mock upstream error"}}, step["http_error"])
        tc = step.get("tool_calls")
        prompt_t = sum(len(str(m.get("content") or "")) // 2 for m in msgs) + 8
        if tc:
            calls = [{
                "id": "call_mock_%d" % i, "type": "function",
                "function": {"name": t["name"], "arguments": json.dumps(t.get("arguments") or {}, ensure_ascii=False)}
            } for i, t in enumerate(tc)]
            message = {"role": "assistant", "content": None, "tool_calls": calls}
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": step.get("content", "")}
            finish = "stop"
        if stream:
            # 流式（普通对话/兜底路径）：完整内容一帧 + finish 帧 + [DONE]
            content = message.get("content") or ""
            frames = []
            if content:
                frames.append({"id": "chatcmpl-mock", "object": "chat.completion.chunk", "model": "mock-llm",
                               "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]})
            frames.append({"id": "chatcmpl-mock", "object": "chat.completion.chunk", "model": "mock-llm",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            data = ("".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        payload = {
            "id": "chatcmpl-mock", "object": "chat.completion", "created": 1, "model": "mock-llm",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": _usage(prompt_t, 12),
        }
        return self._json(payload)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/models":
            return self._json({"object": "list", "data": [{"id": "mock-llm", "object": "model", "owned_by": "mock"}]})
        return self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, *a):
        pass


def main():
    port = int(os_getenv("MOCK_PORT", "8898"))
    # [阶段1.6] 启动时用 MOCK_SCENARIO 指定初始剧本（避免页面跨域调用控制接口）
    initial = os_getenv("MOCK_SCENARIO", "")
    if initial:
        state["scenario"] = initial
        state["idx"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("mock llm listening on", port, "scenario:", state["scenario"])
    srv.serve_forever()


import os as _os  # noqa: E402

os_getenv = _os.environ.get


if __name__ == "__main__":
    main()
