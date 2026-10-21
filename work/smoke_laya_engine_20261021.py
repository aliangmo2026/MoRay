# -*- coding: utf-8 -*-
"""LayaEngine 桥接自测（2026-10-21）：stub 假 Laya，端到端验证 reader 线程→Queue→SSE。

覆盖：start_stream 受理扁平出参 / 帧原文透传 + [DONE] 哨兵 / 状态机只认 final 帧
（step_result 带 failed 不误伤）/ 传输层失败补合成终态帧 / 四方法错误契约 /
URL 安全闸（同源放行、出源拒环回私有）/ cancel 语义。不碰真实 8001。
"""
import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("MORAY_DB", r"D:\MoRay\data\smoke_laya.db")

sys.path.insert(0, r"D:\MoRay")
from server.app.engines.base import StreamTaskError            # noqa: E402
from server.app.engines.laya_adapter import (                   # noqa: E402
    LayaEngine, _assert_safe_url, _is_forbidden_host)

PORT = 59999
FRAMES = [
    {"event": "start", "execution_id": "t", "goal": "冒烟任务", "total_steps": 1, "ts": 1},
    {"event": "step_start", "step_id": 1, "tool": "file", "attempt": 1, "ts": 2},
    {"event": "step_result", "step_id": 1, "attempt": 1, "status": "failed",
     "ok": False, "elapsed_ms": 5, "data": "x", "error": "boom", "ts": 3},
    {"event": "check", "step_id": 1, "attempt": 1, "success": False, "ts": 4},
    {"event": "final", "status": "success", "result": "ok!", "ts": 5},
]


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "running", "port": PORT, "model": "stub"})
        else:
            self._json({"error": "nope"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        if self.path == "/execute/cancel":
            self._json({"code": 0, "cancelled": True, "running": []})
            return
        if self.path != "/execute":
            self._json({"error": "nope"})
            return
        goal = ""
        try:
            goal = str((json.loads(raw.decode("utf-8")) or {}).get("goal") or "")
        except Exception:
            pass
        if goal == "取消我":
            time.sleep(1.2)  # 让任务保持 running，给 cancel() 留窗口（测运行中取消+终态不覆盖）
        lines = "".join("data: " + json.dumps(f, ensure_ascii=False) + "\n\n" for f in FRAMES)
        body = lines.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


OK = []


def check(name, cond):
    OK.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = LayaEngine(base_url="http://127.0.0.1:%d" % PORT)
    check("NAME/DESCRIPTION", eng.NAME == "laya" and bool(eng.DESCRIPTION))
    check("capabilities 五键", set(eng.capabilities()) ==
          {"can_read", "can_write", "can_exec", "can_network", "is_isolated"})
    check("unavailable_reason 探活", eng.unavailable_reason() is None)

    # ---- 1. 流式主路径：受理→SSE 透传→终态 ----
    res = eng.start_stream({"prompt": "冒烟任务", "max_retry": 2})
    check("start_stream 扁平出参", res.get("ok") is True and res.get("status") == "running"
          and len(res.get("task_id", "")) == 32)
    tid = res["task_id"]
    frames = []
    async for item in eng.stream_events(tid):
        frames.append(item)
    raws = [f for f in frames if f.startswith("data: ") and f != "data: [DONE]\n\n"]
    check("帧原文透传 5 帧", len(raws) == 5)
    check("[DONE] 哨兵收尾", frames[-1] == "data: [DONE]\n\n")
    check("data: 前缀逐条包裹", all(f.startswith("data: ") for f in raws))
    ev0 = json.loads(raws[0][6:])
    check("首帧 start 原文不重序列化", ev0.get("event") == "start" and ev0.get("goal") == "冒烟任务")

    # 状态机：step_result.status=failed 不误伤，只认 final → completed
    st = eng.status(tid)
    check("状态机只认 final → completed", st["status"] == "completed" and st["error"] is None)
    rs = eng.result(tid)
    check("result 契约四键", set(rs) == {"task_id", "status", "result", "error"}
          and rs["result"]["result"] == "ok!")

    # ---- 2. 错误契约 ----
    for name, fn in [
        ("status 未知 KeyError", lambda: eng.status("nope")),
        ("result 未知 KeyError", lambda: eng.result("nope")),
        ("read_task_file 404", lambda: eng.read_task_file("a", "b")),
    ]:
        try:
            fn()
            check(name, False)
        except KeyError:
            check(name, "KeyError" in name)
        except StreamTaskError as e:
            check(name, e.status == 404)

    async def _kill_missing():
        try:
            await eng.kill_stream_task("nope")
            return False
        except StreamTaskError as e:
            return e.status == 404
    check("kill 未知 404", await _kill_missing())

    async def _kill_real():
        r = await eng.kill_stream_task(tid)
        return r.get("ok") is True and "killed" in r
    check("kill 已完成任务出参形状", await _kill_real())

    try:
        eng.start_stream({"max_retry": 1})
        check("缺 prompt 拒绝", False)
    except StreamTaskError as e:
        check("缺 prompt 拒绝", e.code == "bad_request")

    # ---- 3. cancel 语义（假 Laya 恒回 cancelled）----
    res2 = eng.start_stream({"prompt": "取消我"})
    ok_cancel = eng.cancel(res2["task_id"])
    check("cancel 成功收敛 failed/canceled",
          ok_cancel and eng.status(res2["task_id"])["status"] == "failed"
          and eng.result(res2["task_id"])["error"] == "canceled")
    check("cancel 未知 False", eng.cancel("nope") is False)
    await asyncio.sleep(1.6)  # 等 stub 迟到的 final{success} 到达
    check("迟到 final 不覆盖已定终态",
          eng.status(res2["task_id"])["status"] == "failed"
          and eng.result(res2["task_id"])["error"] == "canceled")

    # ---- 4. 传输层失败：死端口 → 合成 final + [DONE]，不挂死 ----
    dead = LayaEngine(base_url="http://127.0.0.1:59998")
    res3 = dead.start_stream({"prompt": "死端口"})
    frames3 = [x async for x in dead.stream_events(res3["task_id"])]
    last = json.loads([f for f in frames3 if f.startswith("data: ") and f != "data: [DONE]\n\n"][-1][6:])
    check("死端口补合成 final{failed}", last.get("event") == "final" and last.get("status") == "failed")
    check("死端口 [DONE] 收尾", frames3[-1] == "data: [DONE]\n\n")
    check("死端口终态 failed", dead.status(res3["task_id"])["status"] == "failed")

    # ---- 5. URL 安全闸 ----
    base = "http://localhost:8001"
    check("同源放行", _assert_safe_url(base + "/execute", base) is None)
    check("黑名单环回", _is_forbidden_host("127.0.0.1") and _is_forbidden_host("localhost"))
    check("黑名单私有/保留", _is_forbidden_host("10.0.0.1") and _is_forbidden_host("169.254.169.254")
          and _is_forbidden_host("0.0.0.0"))
    for name, url in [("拒绝 ftp", "ftp://example.com/x"),
                      ("出源环回拒绝", "http://127.0.0.1:9/x"),
                      ("出源私有拒绝", "http://192.168.1.1/x")]:
        try:
            _assert_safe_url(url, base)
            check(name, False)
        except StreamTaskError as e:
            check(name, e.status == 400)

    srv.shutdown()
    bad = [n for n, c in OK if not c]
    print("\n==== %d/%d PASS%s ====" % (len(OK) - len(bad), len(OK),
                                        "" if not bad else "；FAIL: " + ", ".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
