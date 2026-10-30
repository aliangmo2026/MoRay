# -*- coding: utf-8 -*-
"""L4 最小验证 · 阶段3.1 单元测试（POST /api/tasks/run-error-fix + GET 轮询）。

独立目录 tests/l4_min/（无 __init__.py → 不被主 discover 收录，135 基线计数不变）：
    server\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests\\l4_min -t tests\\l4_min -v

策略：TaskTemplateRunner 用 FakeRunner 替换（不发任何真实 Iris/网关请求），
覆盖任务书 3.1 的 正常/异常/边界；步骤超时/重试语义由 task_runner 本体测试
（tests/mcp_phase/test_02_task_runner.py）与真实 E2E 承担。
"""
import asyncio
import base64
import json
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import task_routes


class _Outcome:
    """模仿 engine.task_runner.StepOutcome 的消费面（_summarize_step 用到的字段）。"""
    def __init__(self, sid, stype, status="ok", engine="fake", attempts=1,
                 duration_ms=10, error=""):
        self.id, self.type, self.status = sid, stype, status
        self.engine, self.attempts = engine, attempts
        self.duration_ms, self.error = duration_ms, error


TMP = ROOT / "work" / "l4_min_test"


class FakeRunner:
    """可控替身：hold=True 时挂起在两步之间；release() 放行到终态。"""
    _STEP_ORDER = [{"id": "analyze_error", "type": "mcp_call"},
                   {"id": "fix_code", "type": "zcode_call"},
                   {"id": "generate_report", "type": "report"}]

    def __init__(self, template_path, params=None, work_dir=None, token_budget=None,
                 on_step=None):
        self.tpl = {"name": "fake_flow", "steps": list(self._STEP_ORDER)}
        self.params = dict(params or {})
        self.token_budget = token_budget
        self.work_dir = TMP / ("run_" + uuid.uuid4().hex[:8])
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.on_step = None                        # 路由在构造后赋值，模拟即可
        self._release = threading.Event()
        self.fail_mode = False
        self.report_path = self.work_dir / "fix_report.md"

    def release(self):
        self._release.set()

    def set_fail(self):
        self.fail_mode = True
        self._release.set()

    async def run(self):
        await asyncio.sleep(0.02)
        if self.on_step:
            self.on_step(_Outcome("analyze_error", "mcp_call", engine="mcp", duration_ms=50))
        while not self._release.is_set():          # 挂起点：测试据此观察 running 态
            await asyncio.sleep(0.02)
            if self.fail_mode:
                raise RuntimeError("模拟步骤崩溃")
        if self.fail_mode:
            raise RuntimeError("模拟步骤崩溃")
        if self.on_step:
            self.on_step(_Outcome("fix_code", "zcode_call", engine="fallback:llm_patch",
                                  duration_ms=80))
        self.report_path.write_text("# 修复报告\n根因：除零\n", encoding="utf-8")
        if self.on_step:
            self.on_step(_Outcome("generate_report", "report", engine="report", duration_ms=1))
        return {
            "task": "fake_flow", "status": "completed",
            "tokens_spent": 123, "llm_calls": 2, "duration_sec": 0.6,
            "steps": [
                {"id": "analyze_error", "type": "mcp_call", "status": "ok", "engine": "mcp",
                 "attempts": 1, "duration_ms": 50, "output": {}, "error": ""},
                {"id": "fix_code", "type": "zcode_call", "status": "ok",
                 "engine": "fallback:llm_patch", "attempts": 1, "duration_ms": 80,
                 "output": {"diff": "--- a/main.py\n+++ b/main.py\n+guard", "file": "x"},
                 "error": ""},
                {"id": "generate_report", "type": "report", "status": "ok", "engine": "report",
                 "attempts": 1, "duration_ms": 1,
                 "output": {"path": str(self.report_path)}, "error": ""},
            ],
            "human_intervention": None, "render_misses": [],
        }


class L4TasksEndpointTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(task_routes.router)
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()
        TMP.mkdir(parents=True, exist_ok=True)
        self._saved = dict(task_routes._TASKS)
        task_routes._TASKS.clear()
        task_routes._ORDER.clear()

    def tearDown(self):
        task_routes._TASKS.clear()
        task_routes._TASKS.update(self._saved)
        task_routes._ORDER.clear()
        self.client_ctx.__exit__(None, None, None)

    # ---------- 异常入参（边界） ----------

    def test_empty_body_400(self):
        r = self.client.post("/api/tasks/run-error-fix", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_request")

    def test_bad_json_400(self):
        r = self.client.post("/api/tasks/run-error-fix",
                             content=b"{not-json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_json")

    def test_bad_base64_400(self):
        r = self.client.post("/api/tasks/run-error-fix",
                             json={"error_image_base64": "!!!", "project_dir": str(TMP)})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_image")

    def test_missing_project_dir_400(self):
        r = self.client.post("/api/tasks/run-error-fix",
                             json={"error_text": "boom",
                                   "project_dir": str(TMP / "nope_not_here")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "path_not_found")

    def test_get_unknown_task_404(self):
        r = self.client.get("/api/tasks/nonexistent000")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_found")

    # ---------- 正常链路（异步受理 → 轮询 → 终态） ----------

    def _submit(self, payload, runner_box):
        def factory(*a, **kw):
            fr = FakeRunner(*a, **kw)
            runner_box.append(fr)
            return fr
        with mock.patch.object(task_routes, "TaskTemplateRunner", factory):
            r = self.client.post("/api/tasks/run-error-fix", json=payload)
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        return body["data"]["task_id"]

    def _wait_task(self, tid, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self.client.get("/api/tasks/" + tid).json()["data"]
            if d["status"] != "running":
                return d
            time.sleep(0.05)
        self.fail("task %s did not settle in %ss" % (tid, timeout))

    def test_running_then_completed(self):
        box = []
        tid = self._submit({"error_text": "ZeroDivisionError", "project_dir": str(TMP)}, box)
        runner = box[0]
        # 挂起期间必须是 running；FakeRunner 在 analyze_error 完成后才停在挂起门，
        # 所以观察到的当前阶段是它的下一步（时序确定，不留竞态）
        for _ in range(60):
            d = self.client.get("/api/tasks/" + tid).json()["data"]
            if d["status"] == "running" and d["steps"]:
                break
            time.sleep(0.05)
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["current_stage"], "修复代码")   # analyze_error 已完成 → 下一步
        self.assertNotIn("report", d)                     # 进行中不出终态字段
        runner.release()
        d = self._wait_task(tid)
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["tokens_spent"], 123)
        self.assertIn("根因", d["report"])                 # 报告从磁盘读回
        self.assertIn("guard", d["diff"])                  # diff 从 fix_code 输出抽取
        self.assertEqual([s["status"] for s in d["steps"]], ["ok", "ok", "ok"])

    def test_image_routes_to_error_fix_flow_template(self):
        box = []
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n tiny").decode()
        tid = self._submit({"error_image_base64": png, "project_dir": str(TMP)}, box)
        resp = self.client.get("/api/tasks/" + tid).json()
        runner = box[0]
        self.assertEqual(runner.params.get("screenshot", "") and "screenshot", "screenshot")
        shot = Path(runner.params["screenshot"])
        self.assertTrue(shot.is_file() and shot.stat().st_size > 0)  # 截图已落盘
        box[0].release()
        self._wait_task(tid)
        self.assertEqual(self.client.get("/api/tasks/" + tid).json()["data"]["template"],
                         "error_fix_flow")

    def test_crashed_run_becomes_failed(self):
        box = []
        tid = self._submit({"error_text": "boom", "project_dir": str(TMP)}, box)
        box[0].set_fail()
        d = self._wait_task(tid)
        self.assertEqual(d["status"], "failed")
        self.assertIn("模拟步骤崩溃", d["error"])


class L4HelpersTest(unittest.TestCase):
    def test_b64_dataurl_stripped(self):
        raw = b"\x89PNG\r\n\x1a\nabc"
        ok = task_routes._b64_to_png_bytes("data:image/png;base64," + base64.b64encode(raw).decode())
        self.assertEqual(ok, raw)

    def test_b64_over_size_rejected(self):
        big = base64.b64encode(b"x" * (task_routes.MAX_IMAGE_BYTES + 1)).decode()
        self.assertIsNone(task_routes._b64_to_png_bytes(big))

    def test_record_view_shape(self):
        rec = {"task_id": "t", "template": "text_fix_flow", "status": "running",
               "t0": time.time(), "started": "-", "steps": [], "work_dir": "w",
               "step_order": ["analyze_text", "fix_code"]}
        v = task_routes._record_view(rec)
        self.assertEqual(v["current_stage"], "分析报错")
        self.assertNotIn("report", v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
