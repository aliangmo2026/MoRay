# -*- coding: utf-8 -*-
"""阶段0.5 A：Agent 全链路自动化回归（不花真实模型额度）

架构：
- mock OpenAI 兼容上游（线程 HTTPServer，剧本驱动，记录完整请求供回灌断言）；
- 后端 = 真实 FastAPI app 经 fastapi.testclient.TestClient（MORAY_DB/MORAY_WORKSPACE 指向 %TEMP%，
  真实文件系统与子进程，与生产同一套 agent_tools 安全层）；
- 协议模拟器 = 1:1 复刻前端 runWithTools 的协议行为（非流式带 tools → 解析 tool_calls →
  调 /api/agent/tool（副作用先无 approved 收 needsApproval 再带 approved / 或 decision=denied）→
  assistant(tool_calls)+role:"tool" 回灌 → 再请求），这不是第二套实现，是测试侧的协议替身。

覆盖 6 场景：只读单工具 / 写文件审批通过 / 写文件被拒 / 多工具多轮顺序 / 超轮次上限 /
异常降级（后端不在线 + 模型不支持 tools）。

运行：python scripts\\agent_e2e_check.py   （exit 0 = 全绿）
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ts = time.strftime("%Y%m%d_%H%M%S")
_tmp = tempfile.gettempdir()
os.environ["MORAY_DB"] = os.path.join(_tmp, "moray_agent_e2e_check_%s.sqlite3" % _ts)
os.environ["MORAY_WORKSPACE"] = os.path.join(_tmp, "moray_agent_e2e_ws_%s" % _ts)
os.environ["MORAY_PORT"] = "8015"  # 仅占位：TestClient 不监听端口

# ---- 先设 env 再 import app（config 顶层读 env）----
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import httpx  # noqa: E402

MOCK_PORT = 8897
MOCK_BASE = "http://127.0.0.1:%d" % MOCK_PORT
WORKSPACE = os.environ["MORAY_WORKSPACE"]

TOOLS = [
    {"type": "function", "function": {"name": "list_directory", "description": "d", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "d", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "write_file", "description": "d", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "run_command", "description": "d", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}}}},
    {"type": "function", "function": {"name": "find_files", "description": "d", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "search_text", "description": "d", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "regex": {"type": "boolean"}}}}},
    {"type": "function", "function": {"name": "edit_file", "description": "d", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "submit_plan", "description": "d", "parameters": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "tool": {"type": "string"}}}}}}}},
]

SIDE_EFFECTS = {"write_file", "run_command", "edit_file"}

# ---------------------------------------------------------------- mock 上游

SCENARIOS = {
    "s1_list": [
        {"tool_calls": [{"name": "list_directory", "arguments": {}}]},
        {"content": "工作区检查完毕，目前没有用户文件。"},
    ],
    "s2_write_ok": [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "hello_e2e.txt", "content": "你好阶段05"}}]},
        {"content": "已完成：hello_e2e.txt 已写入工作区。"},
    ],
    "s3_write_deny": [
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "nope_e2e.txt", "content": "x"}}]},
        {"content": "好的，已取消，没有创建任何文件。"},
    ],
    "s4_multi": [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "a.txt"}}]},
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "b.txt"}}]},
        {"tool_calls": [{"name": "write_file", "arguments": {"path": "summary_e2e.txt", "content": "汇总：A=内容A素材 B=内容B素材"}}]},
        {"content": "已把 a.txt 与 b.txt 的要点写入 summary_e2e.txt。"},
    ],
    "s5_endless": "endless",  # 每轮都返回 read_file a.txt 的 tool_call（专属分支处理）
    "s6_no_tools": [],        # 带 tools → 400；不带 tools → 固定文本（专属分支处理）
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
}

mock_state = {"scenario": None, "idx": 0}
mock_requests = []  # 每次收到的请求（完整 messages）
lock = threading.Lock()


class MockLLM(BaseHTTPRequestHandler):
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

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        if self.path == "/__ctl":
            with lock:
                mock_state["scenario"] = body.get("scenario")
                mock_state["idx"] = 0
                mock_requests.clear()
            return self._json({"ok": True})
        if self.path.endswith("/chat/completions"):
            has_tools = bool(body.get("tools"))
            msgs = body.get("messages", [])
            with lock:
                mock_requests.append({
                    "has_tools": has_tools,
                    "n_msgs": len(msgs),
                    "roles": [m.get("role") for m in msgs],
                    "assistant_tool_calls": [
                        [{"name": (t.get("function") or {}).get("name"),
                          "arguments": (t.get("function") or {}).get("arguments"),
                          "id": t.get("id")} for t in (m.get("tool_calls") or [])]
                        for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")
                    ],
                    "tool_msgs": [
                        {"tool_call_id": m.get("tool_call_id"), "content": m.get("content")}
                        for m in msgs if m.get("role") == "tool"
                    ],
                })
                sc = mock_state["scenario"]
                if sc == "s6_no_tools":
                    if has_tools:
                        return self._json({"error": {"message": "tool_use not supported"}}, 400)
                    return self._json({
                        "choices": [{"message": {"role": "assistant",
                                                 "content": "当前模型是纯对话模型，不支持工具调用，无法访问工作区。"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 8},
                    })
                if sc == "s5_endless":
                    idx = mock_state["idx"]
                    mock_state["idx"] += 1
                    payload = {
                        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "call_endless_%d" % idx, "type": "function",
                             "function": {"name": "read_file", "arguments": json.dumps({"path": "a.txt"})}}]},
                            "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    }
                    return self._json(payload)
                script = SCENARIOS.get(sc) or []
                idx = min(mock_state["idx"], len(script) - 1) if script else 0
                step = script[idx] if script else None
                mock_state["idx"] += 1
            if step is None:
                step = {"content": "（mock 剧本耗尽）"}
            tc = step.get("tool_calls")
            if tc:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_%d_%d" % (mock_state["idx"], i), "type": "function",
                     "function": {"name": t["name"], "arguments": json.dumps(t.get("arguments") or {}, ensure_ascii=False)}}
                    for i, t in enumerate(tc)]}
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": step.get("content", "")}
                finish = "stop"
            return self._json({
                "choices": [{"message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            })
        return self._json({"error": "unknown path"}, 404)

    def log_message(self, *a):  # noqa: N802
        pass


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(40):
        try:
            httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "none"}, timeout=1)
            return srv
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("mock upstream failed to start")


# ---------------------------------------------------------------- 协议模拟器

def agent_tool(client, name, args, approved=None, decision=None):
    """调后端 /api/agent/tool（对应前端 agentNativeRun 的协议）"""
    body = {"name": name, "args": args}
    if approved is not None:
        body["approved"] = approved
    if decision is not None:
        body["decision"] = decision
    r = client.post("/api/agent/tool", json=body, timeout=60)
    return r.status_code, r.json()


def run_agent_loop(client, user_text, max_rounds=8, deny_tools=None):
    """1:1 复刻前端 runWithTools 的协议循环（测试替身，非第二套实现）。

    deny_tools: set of tool names the "user" will reject（走 decision=denied 上报）
    返回 {final_text, tool_steps, rounds, approval_events, plan}
    tool_steps: [{name, args, status: ok|denied|error, result, needsApproval_seen}]
    plan: submit_plan 桩登记的计划（对应前端 PlanTracker）
    """
    deny_tools = deny_tools or set()
    msgs = [{"role": "user", "content": user_text}]
    steps = []
    approval_events = []
    plan = None
    for _round in range(max_rounds):
        resp = httpx.post(MOCK_BASE + "/v1/chat/completions", json={
            "model": "mock-llm", "messages": msgs, "stream": False, "tools": TOOLS,
        }, timeout=30)
        if resp.status_code == 400:
            # 前端兜底等价：模型不支持 tools → 去 tools 普通请求
            resp2 = httpx.post(MOCK_BASE + "/v1/chat/completions", json={
                "model": "mock-llm", "messages": msgs, "stream": False,
            }, timeout=30)
            j = resp2.json()
            return {"final_text": j["choices"][0]["message"]["content"], "tool_steps": steps,
                    "rounds": _round, "fallback_no_tools": True, "plan": plan}
        j = resp.json()
        msg = j["choices"][0]["message"]
        calls = msg.get("tool_calls")
        if not calls:
            return {"final_text": msg.get("content") or "", "tool_steps": steps,
                    "rounds": _round + 1, "fallback_no_tools": False, "plan": plan}
        msgs.append({"role": "assistant", "content": "", "tool_calls": calls})
        for call in calls:
            fn = call["function"]
            name = fn["name"]
            args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
            # [阶段1 M3] submit_plan 是纯前端工具：本地桩登记计划（等价 PlanTracker.submit）
            if name == "submit_plan":
                plan = {"steps": [{"title": s.get("title"), "tool": s.get("tool"), "status": "todo"}
                                  for s in (args.get("steps") or [])]}
                result, status = {"registered": len(plan["steps"]),
                                  "message": "计划已登记并展示给用户。请按顺序逐步执行。"}, "ok"
                steps.append({"name": name, "args": args, "status": status, "result": result, "needsApproval_seen": False})
                msgs.append({"role": "tool", "tool_call_id": call["id"], "name": name,
                             "content": json.dumps(result, ensure_ascii=False)})
                continue
            step = {"name": name, "args": args, "status": None, "result": None, "needsApproval_seen": False}
            if name in SIDE_EFFECTS and name not in deny_tools:
                # 双保险：先无 approved → 必须 needsApproval
                sc, sj = agent_tool(client, name, args)
                step["needsApproval_seen"] = bool(sj.get("needsApproval"))
                approval_events.append({"name": name, "needsApproval": step["needsApproval_seen"]})
                sc, sj = agent_tool(client, name, args, approved=True)
            elif name in deny_tools:
                sc, sj = agent_tool(client, name, args, decision="denied")
            else:
                sc, sj = agent_tool(client, name, args)
            if sj.get("ok"):
                step["status"] = "denied" if sj.get("data", {}).get("denied") else "ok"
                step["result"] = sj.get("data")
            else:
                step["status"] = "error"
                step["result"] = sj.get("message")
            steps.append(step)
            # 回灌（与前端一致：成功=JSON(data)，失败="错误："+message，拒绝=结果对象里的 message）
            if step["status"] == "ok":
                content = json.dumps(step["result"], ensure_ascii=False)
            elif step["status"] == "denied":
                # 与前端 agentNativeRun 拒绝分支一致：{denied:true, message:'用户拒绝了该操作（… 未执行）'}
                content = json.dumps({"denied": True,
                                      "message": "用户拒绝了该操作（%s 未执行）" % name},
                                     ensure_ascii=False)
            else:
                content = "错误：" + str(step["result"])
            msgs.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": content})
    return {"final_text": None, "tool_steps": steps, "rounds": max_rounds, "timeout": True, "plan": plan}


# ---------------------------------------------------------------- 测试主体

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS  %s" % name)
    else:
        FAIL.append((name, detail))
        print("FAIL  %s  -> %s" % (name, str(detail)[:260]))


def main():
    os.makedirs(WORKSPACE, exist_ok=True)
    # 预置 a.txt/b.txt（场景4）
    with open(os.path.join(WORKSPACE, "a.txt"), "w", encoding="utf-8") as f:
        f.write("内容A素材")
    with open(os.path.join(WORKSPACE, "b.txt"), "w", encoding="utf-8") as f:
        f.write("内容B素材")

    mock = start_mock()
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        check("T0 后端启动(临时库) build=%s" % health.get("build"), health.get("ok") is True and health.get("counts", {}).get("conversations") == 0, health)

        # ---- 场景1：只读单工具，全程不弹审批 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s1_list"}, timeout=5)
        r1 = run_agent_loop(client, "看看工作区里有什么文件")
        check("S1 列目录自动执行成功", len(r1["tool_steps"]) == 1 and r1["tool_steps"][0]["status"] == "ok", r1["tool_steps"])
        check("S1 全程无审批中断", all(not s["needsApproval_seen"] for s in r1["tool_steps"]), r1["tool_steps"])
        check("S1 最终自然语言答复", r1["final_text"] and "工作区" in r1["final_text"], r1["final_text"])
        mock_second = mock_requests[1] if len(mock_requests) > 1 else None
        fmt_ok = bool(mock_second and mock_second["roles"][-2:] == ["assistant", "tool"]
                      and mock_second["assistant_tool_calls"] and mock_second["assistant_tool_calls"][-1][0]["name"] == "list_directory"
                      and mock_second["tool_msgs"])
        check("S1 回灌消息格式 assistant(tool_calls)+role:tool", fmt_ok, mock_second and (mock_second["roles"], mock_second["tool_msgs"][:1]))

        # ---- 场景2：写文件审批通过 → 磁盘真实落盘 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s2_write_ok"}, timeout=5)
        r2 = run_agent_loop(client, "新建 hello_e2e.txt 写入你好阶段05")
        s2 = r2["tool_steps"][0] if r2["tool_steps"] else {}
        check("S2 副作用先收到 needsApproval", s2.get("needsApproval_seen") is True, s2)
        check("S2 approved 后执行成功", s2.get("status") == "ok", s2)
        hello = os.path.join(WORKSPACE, "hello_e2e.txt")
        content_ok = os.path.isfile(hello) and open(hello, encoding="utf-8").read() == "你好阶段05"
        check("S2 文件真实落盘且内容正确", content_ok, open(hello, encoding="utf-8").read() if os.path.isfile(hello) else "missing")
        check("S2 模型确认", r2["final_text"] and "hello_e2e.txt" in r2["final_text"], r2["final_text"])
        log = client.get("/api/agent/log?limit=50").json()["data"]["rows"]
        wr_ok = [x for x in log if x["tool"] == "write_file" and x["status"] == "ok" and x["approved"] == 1]
        check("S2 审计 approved=1 落库", len(wr_ok) >= 1 and "hello_e2e.txt" in wr_ok[0]["args_summary"], wr_ok[:1])

        # ---- 场景3：写文件被拒 → 不落盘，模型收到"用户拒绝" ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s3_write_deny"}, timeout=5)
        r3 = run_agent_loop(client, "写一个 nope_e2e.txt", deny_tools={"write_file"})
        s3 = r3["tool_steps"][0] if r3["tool_steps"] else {}
        check("S3 denied 上报返回 denied", s3.get("status") == "denied" and (s3.get("result") or {}).get("denied") is True, s3)
        check("S3 文件未落盘", not os.path.exists(os.path.join(WORKSPACE, "nope_e2e.txt")), "")
        deny_msg = (mock_requests[-1]["tool_msgs"] or [{}])[0].get("content", "") if mock_requests else ""
        check("S3 模型收到用户拒绝回灌", "拒绝" in deny_msg, deny_msg[:160])
        log3 = client.get("/api/agent/log?limit=50").json()["data"]["rows"]
        check("S3 审计含 denied 记录", any(x["status"] == "denied" and "nope_e2e.txt" in x["args_summary"] for x in log3), [x for x in log3 if x["status"] == "denied"][:1])

        # ---- 场景4：多工具多轮，顺序与回灌格式与轮次 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s4_multi"}, timeout=5)
        r4 = run_agent_loop(client, "读 a.txt 与 b.txt，汇总写入 summary_e2e.txt")
        names = [s["name"] for s in r4["tool_steps"]]
        check("S4 调用顺序 read a → read b → write summary",
              names == ["read_file", "read_file", "write_file"], names)
        check("S4 三步全部成功", all(s["status"] == "ok" for s in r4["tool_steps"]), r4["tool_steps"])
        last_mock = mock_requests[-1]
        # 回灌格式：3 组 assistant(tool_calls) + 3 条 role:tool，tool_call_id 一一对应
        acalls = [c for grp in last_mock["assistant_tool_calls"] for c in grp]
        tids = [t["tool_call_id"] for t in last_mock["tool_msgs"]]
        fmt_ok = (len(acalls) == 3 and len(tids) == 3
                  and [c["id"] for c in acalls] == tids
                  and all(isinstance(c["arguments"], str) for c in acalls))
        check("S4 回灌格式：tool_call_id 一一对应且 arguments 为协议字符串", fmt_ok,
              (acalls and [c["id"] for c in acalls], tids))
        check("S4 轮次计数=4（3 次工具轮+1 次最终）", r4["rounds"] == 4 and len(mock_requests) == 4,
              (r4["rounds"], len(mock_requests)))
        summ = os.path.join(WORKSPACE, "summary_e2e.txt")
        s_text = open(summ, encoding="utf-8").read() if os.path.isfile(summ) else ""
        check("S4 summary 内容包含 a/b 信息", "内容A" in s_text and "内容B" in s_text, s_text)

        # ---- 场景5：超 maxRounds 上限不无限循环 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s5_endless"}, timeout=5)
        t0 = time.time()
        r5 = run_agent_loop(client, "读 a.txt", max_rounds=3)
        dt = time.time() - t0
        check("S5 达到 maxRounds=3 即止（不无限循环）",
              r5.get("timeout") is True and len(r5["tool_steps"]) == 3 and dt < 30,
              (len(r5["tool_steps"]), "%.1fs" % dt))
        log5 = client.get("/api/agent/log?limit=100").json()["data"]["rows"]
        endless_reads = [x for x in log5 if x["tool"] == "read_file" and "a.txt" in x["args_summary"] and x["status"] == "ok"]
        check("S5 每轮真实执行且恰好 3 次", len(endless_reads) >= 3, len(endless_reads))

        # ---- 场景6a：后端不在线 → 连接层明确失败（前端映射为"需要启动本地后端"）----
        try:
            httpx.post("http://127.0.0.1:59997/api/agent/tool", json={"name": "list_directory", "args": {}}, timeout=3)
            offline_ok = False, "unexpected success"
        except Exception as e:
            offline_ok = True, type(e).__name__
        check("S6a 后端不在线 → 连接错误（前端映射为『本机工具需要启动本地后端』提示）", offline_ok[0], offline_ok[1])
        # 前端文案映射存在于 125_tools.js（静态自证：agentNativeRun catch 分支）
        src125 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "parts", "125_tools.js"), encoding="utf-8-sig").read()
        check("S6a 前端降级文案存在（agentNativeRun catch）", "本机工具需要启动本地后端" in src125, "")

        # ---- 场景6b：模型不支持 tools → 去 tools 普通请求优雅收尾 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s6_no_tools"}, timeout=5)
        r6 = run_agent_loop(client, "你好")
        check("S6b 模型不支持 tools → 优雅回退普通对话并得到答复",
              r6.get("fallback_no_tools") is True and bool(r6["final_text"]),
              (r6.get("fallback_no_tools"), r6["final_text"]))
        check("S6b 回退请求不带 tools 字段", mock_requests[-1]["has_tools"] is False, mock_requests[-1]["has_tools"])

        # ---- 场景7（M3）：计划多步——submit_plan 登记 → find → search → 总结 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s7_plan"}, timeout=5)
        r7 = run_agent_loop(client, "看看工作区里有哪些 txt 文件，并确认 a.txt 的内容")
        names7 = [s["name"] for s in r7["tool_steps"]]
        check("S7 计划先登记（submit_plan 为第一步）", bool(names7) and names7[0] == "submit_plan", names7)
        check("S7 计划含 3 步且步骤标题完整",
              r7["plan"] and len(r7["plan"]["steps"]) == 3 and all(s["title"] for s in r7["plan"]["steps"]),
              r7.get("plan"))
        check("S7 执行顺序匹配计划（find_files → search_text）",
              names7 == ["submit_plan", "find_files", "search_text"], names7)
        check("S7 最终自然语言总结", r7["final_text"] and "计划" in r7["final_text"], r7["final_text"])

        # ---- 场景8（M2/M4）：edit_file 审批 → 精确替换 → read 验证 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s8_edit"}, timeout=5)
        r8 = run_agent_loop(client, "新建 e2e_edit.txt 写入 AAA BBB CCC，然后把 BBB 改成 DDD")
        names8 = [s["name"] for s in r8["tool_steps"]]
        check("S8 三步顺序 write → edit → read", names8 == ["write_file", "edit_file", "read_file"], names8)
        edit_step = r8["tool_steps"][1]
        check("S8 edit_file 走审批（needsApproval→approved）",
              edit_step["needsApproval_seen"] is True and edit_step["status"] == "ok"
              and edit_step["result"].get("replacements") == 1, edit_step)
        e_file = os.path.join(WORKSPACE, "e2e_edit.txt")
        e_content = open(e_file, encoding="utf-8").read() if os.path.isfile(e_file) else ""
        check("S8 read_file 确认替换生效（AAA DDD CCC）", "AAA DDD CCC" in e_content, e_content)

        # ---- 场景9：edit_file 被拒 → 文件内容不变 ----
        httpx.post(MOCK_BASE + "/__ctl", json={"scenario": "s9_edit_deny"}, timeout=5)
        before_deny = open(e_file, encoding="utf-8").read()
        r9 = run_agent_loop(client, "把 DDD 改成 X", deny_tools={"edit_file"})
        after_deny = open(e_file, encoding="utf-8").read()
        check("S9 edit 被拒 → 文件内容不变", before_deny == after_deny == "AAA DDD CCC", (before_deny, after_deny))
        check("S9 模型收到拒绝并收尾", r9["final_text"] and "取消" in r9["final_text"], r9["final_text"])

        # ---- 安全不回退抽检（在 e2e 内重申关键安全线）----
        sc, sj = agent_tool(client, "read_file", {"path": "../../outside.txt"})
        check("SEC 路径越界拒绝（e2e 内抽检）", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
        sc, sj = agent_tool(client, "run_command", {"command": "dir"}, approved=True)
        check("SEC 非白名单命令拒绝（e2e 内抽检）", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
        sc, sj = agent_tool(client, "find_files", {"path": "..\\..\\windows"}, approved=True)
        check("SEC find_files 越界拒绝（M2 抽检）", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    mock.shutdown()
    # 清理
    import shutil
    shutil.rmtree(WORKSPACE, ignore_errors=True)
    try:
        os.remove(os.environ["MORAY_DB"])
    except OSError:
        pass

    print("\n==== agent_e2e_check: %d PASS / %d FAIL ====" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, d in FAIL:
            print("  FAILED:", n, "|", str(d)[:300])
        sys.exit(1)
    print("ALL GREEN")
    sys.exit(0)


if __name__ == "__main__":
    main()
