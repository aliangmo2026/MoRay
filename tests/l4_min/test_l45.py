# -*- coding: utf-8 -*-
"""L4.5 · 阶段4.1 单元测试（项目选择配置 + test_run_flow 模板 + run-test-flow 端点 + 静态守卫）。

同目录 tests/l4_min/（不被主 discover 收录，135 基线计数不变）：
    server\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests\\l4_min -t tests\\l4_min -v

策略沿用 test_l4_tasks：TaskTemplateRunner 用替身（不发真实 Iris/网关/pytest 请求）；
真实模板只验证"可加载 + 步骤契约与类型受 dispatch 支持"，不执行。
"""
import asyncio
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "l4_min"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import config, task_routes
from engine.task_runner import TaskTemplateRunner
from test_l4_tasks import FakeRunner, _Outcome, TMP

TPL_PATH = ROOT / "templates" / "test_run_flow.yaml"

# task_runner._dispatch 支持的类型全集（新模板不得越界，越界即 UNKNOWN_STEP）
DISPATCH_TYPES = {"mcp_call", "zcode_call", "docker_sandbox", "report",
                  "knowledge_read", "knowledge_write"}


class FlowRunnerStub(FakeRunner):
    """按 test_run_flow 真实步骤序的替身（步骤名走 STAGE_LABEL 新键）。"""
    _STEP_ORDER = [{"id": "run_tests", "type": "docker_sandbox"},
                   {"id": "analyze_failures", "type": "mcp_call"},
                   {"id": "generate_report", "type": "report"}]

    async def run(self):
        await asyncio.sleep(0.02)
        if self.on_step:
            self.on_step(_Outcome("run_tests", "docker_sandbox", engine="fallback:local_exec",
                                  duration_ms=700))
        while not self._release.is_set():
            await asyncio.sleep(0.02)
        if self.fail_mode:
            raise RuntimeError("模拟步骤崩溃")
        if self.on_step:
            self.on_step(_Outcome("analyze_failures", "mcp_call", engine="mcp", duration_ms=90))
        self.report_path.write_text("# 测试分析报告\n全部通过\n", encoding="utf-8")
        if self.on_step:
            self.on_step(_Outcome("generate_report", "report", engine="report", duration_ms=1))
        return {
            "task": "test_run_flow", "status": "completed",
            "tokens_spent": 55, "llm_calls": 1, "duration_sec": 0.9,
            "steps": [
                {"id": "run_tests", "type": "docker_sandbox", "status": "ok",
                 "engine": "fallback:local_exec", "attempts": 1, "duration_ms": 700,
                 "output": {"exit_code": 0, "stdout": "5 passed", "stderr": "", "mode": "local_exec"},
                 "error": ""},
                {"id": "analyze_failures", "type": "mcp_call", "status": "ok", "engine": "mcp",
                 "attempts": 1, "duration_ms": 90, "output": {}, "error": ""},
                {"id": "generate_report", "type": "report", "status": "ok", "engine": "report",
                 "attempts": 1, "duration_ms": 1, "output": {"path": str(self.report_path)},
                 "error": ""},
            ],
            "human_intervention": None, "render_misses": [],
        }


class L45TemplateTest(unittest.TestCase):
    """test_run_flow.yaml：真实文件可被 runner 加载，步骤契约不越界。"""

    def test_yaml_loads_with_supported_steps(self):
        wd = TMP / ("tplload_" + uuid.uuid4().hex[:8])
        try:
            r = TaskTemplateRunner(str(TPL_PATH),
                                   params={"project_dir": str(TMP), "project_id": "unit",
                                           "test_cmd": "python -m pytest -q"},
                                   work_dir=str(wd))
            steps = r.tpl.get("steps") or []
            self.assertEqual([s.get("id") for s in steps],
                             ["run_tests", "read_experience", "analyze_failures",
                              "generate_report", "record_experience"])
            for s in steps:
                self.assertIn(s.get("type"), DISPATCH_TYPES,
                              "步骤 %s 类型 %r 超出 task_runner dispatch 支持面" % (s.get("id"), s.get("type")))
            self.assertEqual(steps[0]["type"], "docker_sandbox")
            self.assertTrue(steps[0].get("continue_on_fail"),
                            "run_tests 必须 continue_on_fail（测试失败也要出报告）")
            self.assertEqual(r.tpl.get("name"), "test_run_flow")
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class L45ClientCase(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(task_routes.router)
        self.ctx = TestClient(app)
        self.client = self.ctx.__enter__()
        TMP.mkdir(parents=True, exist_ok=True)
        self._saved = dict(task_routes._TASKS)
        task_routes._TASKS.clear()
        task_routes._ORDER.clear()

    def tearDown(self):
        task_routes._TASKS.clear()
        task_routes._TASKS.update(self._saved)
        task_routes._ORDER.clear()
        self.ctx.__exit__(None, None, None)


class L45ProjectsEndpointTest(L45ClientCase):
    """项目列表接口：配置项 + 工作区兜底，不存在目录过滤（项目选择卡数据源）。"""

    def test_filters_missing_dirs_and_resolves(self):
        with mock.patch.object(config, "TASK_PROJECTS", (str(TMP), str(TMP / "nope_not_here"))), \
             mock.patch.object(config, "WORKSPACE_DEFAULT", str(TMP / "ws_missing")):
            r = self.client.get("/api/tasks/projects")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        paths = [p["path"] for p in data["projects"]]
        self.assertEqual(paths, [str(Path(TMP).resolve())])
        self.assertEqual(data["default"], str(Path(TMP).resolve()))

    def test_workspace_fallback_when_no_config(self):
        with mock.patch.object(config, "TASK_PROJECTS", ()), \
             mock.patch.object(config, "WORKSPACE_DEFAULT", str(TMP)):
            r = self.client.get("/api/tasks/projects")
        paths = [p["path"] for p in r.json()["data"]["projects"]]
        self.assertEqual(paths, [str(Path(TMP).resolve())])

    def test_not_swallowed_by_task_id_route(self):
        """路由顺序守卫：/api/tasks/projects 必须 200，不能掉进 GET /api/tasks/{task_id} 的 404。"""
        r = self.client.get("/api/tasks/projects")
        self.assertEqual(r.status_code, 200, "被 {task_id} 路由吞掉 = 注册顺序回归")
        self.assertTrue(r.json()["ok"])


class L45RunTestFlowTest(L45ClientCase):
    """POST /api/tasks/run-test-flow：镜像 run-error-fix 的受理/轮询契约。"""

    def _submit(self, payload, runner_box, cls=FlowRunnerStub):
        def factory(*a, **kw):
            fr = cls(*a, **kw)
            runner_box.append(fr)
            return fr
        with mock.patch.object(task_routes, "TaskTemplateRunner", factory):
            r = self.client.post("/api/tasks/run-test-flow", json=payload)
        return r

    def _wait(self, tid, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self.client.get("/api/tasks/" + tid).json()["data"]
            if d["status"] != "running":
                return d
            time.sleep(0.05)
        self.fail("task %s not settled" % tid)

    def test_bad_json_400(self):
        r = self.client.post("/api/tasks/run-test-flow",
                             content=b"{oops", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_json")

    def test_missing_project_dir_400(self):
        r = self._submit({"project_dir": str(TMP / "nope")}, [])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "path_not_found")

    def test_empty_body_falls_back_to_workspace(self):
        box = []
        with mock.patch.object(config, "WORKSPACE_DEFAULT", str(TMP)):
            r = self._submit({}, box)
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(box[0].params["project_dir"], str(Path(TMP).resolve()))
        self.assertEqual(box[0].params["test_cmd"], task_routes.DEFAULT_TEST_CMD)
        box[0].release()
        self._wait(r.json()["data"]["task_id"])

    def test_running_then_completed_labels(self):
        box = []
        r = self._submit({"project_dir": str(TMP), "test_cmd": "python -m pytest -q tests/l4_min"}, box)
        self.assertEqual(r.status_code, 202)
        tid = r.json()["data"]["task_id"]
        self.assertEqual(r.json()["data"]["template"], "test_run_flow")
        runner = box[0]
        # project_id 兜底 = 目录名
        self.assertEqual(runner.params["project_id"], Path(TMP).name)
        # run_tests 步完成后、挂起门未放 → current_stage 必须是下一个未完成步的人话名
        d = {}
        for _ in range(60):
            d = self.client.get("/api/tasks/" + tid).json()["data"]
            if d["status"] == "running" and d["steps"]:
                break
            time.sleep(0.05)
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["current_stage"], "分析失败")   # STAGE_LABEL 新键生效
        runner.release()
        d = self._wait(tid)
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["tokens_spent"], 55)
        self.assertIn("全部通过", d["report"])
        self.assertEqual([s["label"] for s in d["steps"]], ["运行测试", "分析失败", "生成报告"])

    def test_crashed_run_becomes_failed(self):
        box = []
        r = self._submit({"project_dir": str(TMP)}, box)
        tid = r.json()["data"]["task_id"]
        box[0].set_fail()
        d = self._wait(tid)
        self.assertEqual(d["status"], "failed")
        self.assertIn("模拟步骤崩溃", d["error"])


class L45EngineFailureOutputContractTest(unittest.TestCase):
    """[L4.5 修复回归] VERIFY_FAILED 失败步必须保留已采集的 stdout/stderr
    （E2E 实证 bug：run_tests 失败 → 输出被丢弃 → 报告空 → Iris 分析无料）。"""

    def test_runner_step_output_on_failure(self):
        import asyncio
        r = TaskTemplateRunner(str(TPL_PATH),
                               params={"project_dir": str(TMP), "project_id": "unit",
                                       "test_cmd": 'python -c "import sys;sys.stderr.write(\'boom-42\');sys.exit(3)"'},
                               work_dir=str(TMP / ("fout_" + uuid.uuid4().hex[:8])))
        # 直接跑第一个 run_tests 步（docker 不可用机器会走 local_exec fallback；
        # 可用机器走 docker 失败 VERIFY_FAILED——两种都带 out 或明确失败），
        # 用 asyncio.run 执行 _run_step，断言失败步输出保留。
        spec = r.tpl["steps"][0]
        o = asyncio.run(r._run_step("run_tests", spec["type"], spec, 60, 0))
        try:
            self.assertEqual(o.status, "failed")
            if "VERIFY_FAILED" in o.error:
                self.assertIn("exit=", o.error)
                # 失败必须携带采集面（本修复的核心断言；docker 容器里 python 可能不存在，
                # 但 stdout/stderr 键必须在）
                self.assertIn("stdout", o.output)
                self.assertIn("stderr", o.output)
                self.assertIn("exit_code", o.output)
        finally:
            shutil.rmtree(r.work_dir, ignore_errors=True)


class L45StaticGuardsTest(unittest.TestCase):
    """构建产物与配置面的静态守卫（防"改了源忘了重建 / 忘了登记"两连坑）。"""

    def test_workbench_contains_l45_branches(self):
        html = (ROOT / "moray-workbench.html").read_text(encoding="utf-8", errors="replace")
        for marker in ("run-test-flow", "openProjectPicker", "moray_test_intent",
                       "moray_task_projects_recent", "TEST_RE"):
            self.assertIn(marker, html, "前端产物缺 %r（88 改后未重跑 assemble.py？）" % marker)

    def test_config_exposes_task_projects_tuple(self):
        self.assertIsInstance(config.TASK_PROJECTS, tuple)

    def test_stage_labels_registered(self):
        for sid in ("run_tests", "analyze_failures"):
            self.assertIn(sid, task_routes.STAGE_LABEL)

    def test_env_example_documents_gateway(self):
        txt = (ROOT / "server" / ".env.example").read_text(encoding="utf-8", errors="replace")
        self.assertIn("IRIS_GATEWAY_URL", txt)
        self.assertIn("MORAY_TASK_PROJECTS", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
