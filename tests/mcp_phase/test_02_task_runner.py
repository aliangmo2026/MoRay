# -*- coding: utf-8 -*-
"""p_mcp·任务2 模板执行器单测：分发/占位/重试/降级/超时/预算/报告/知识步骤（零真实模型）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 tests/mcp_phase/test_02_task_runner.py"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, r"D:\MoRay")
os.environ.setdefault("MORAY_EXPERIENCE_DB",
                      str(Path(tempfile.mkdtemp(prefix="mcp_tr_ut_")) / "exp.jsonl"))

# 按文件路径隔离加载（pytest 单进程收集时，test_01 已可能把 D:\Iris\server\engine.py
# 注册成 sys.modules["engine"]；避免同名包冲突）
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("mcp_ut_task_runner", r"D:\MoRay\engine\task_runner.py")
tr = _ilu.module_from_spec(_spec)
sys.modules["mcp_ut_task_runner"] = tr   # dataclass 解析需要模块在 sys.modules 中
_spec.loader.exec_module(tr)
import yaml  # noqa: E402

TMP0 = Path(tempfile.mkdtemp(prefix="mcp_tr_ut0_"))


def write_tpl(steps, name="ut"):
    p = TMP0 / f"{name}_{os.urandom(3).hex()}.yaml"
    p.write_text(yaml.safe_dump({"name": name, "defaults": {"retries": 1, "timeout": 60},
                                 "steps": steps}, allow_unicode=True), encoding="utf-8")
    return str(p)


class TestPlumbing(unittest.IsolatedAsyncioTestCase):
    async def test_renderer_native_and_miss(self):
        tpl = write_tpl([{"id": "k", "type": "knowledge_read", "args": {"project_id": "ZZ"}}])
        r = tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r1"))
        out = {"a": "{{params.x}}", "b": "{{steps.k.status}}", "c": "{{nope.zz}}", "d": 5}
        self.assertEqual(r.render(out), {"a": "", "b": "", "c": "", "d": 5})

    async def test_mcp_call_envelope_pass_through(self):
        # 直连本机 9301（若在跑）；否则跳过（CI 友好）
        import httpx
        try:
            if httpx.get("http://127.0.0.1:9301/health", timeout=2).status_code != 200:
                raise Exception()
        except Exception:
            self.skipTest("9301 未运行")
        os.environ.setdefault("IRIS_MCP_API_KEY", "morayIrisKey2026")
        tpl = write_tpl([{"id": "bad", "type": "mcp_call", "retries": 0, "args": {
            "tool": "iris_analyze_image", "arguments": {"image_path": "D:\\不存在.png"}}}])
        rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r2")).run()
        self.assertEqual("failed", rep["status"])
        self.assertIn("IRIS_INVALID_INPUT", rep["steps"][0]["error"])

    async def test_unknown_step(self):
        tpl = write_tpl([{"id": "x", "type": "no_such", "retries": 0, "args": {}}])
        rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r3")).run()
        self.assertIn("UNKNOWN_STEP", rep["steps"][0]["error"])


class TestFallbackChannels(unittest.IsolatedAsyncioTestCase):
    async def _mk_proj(self):
        proj = Path(tempfile.mkdtemp(prefix="mcp_proj_"))
        (proj / "main.py").write_text("import foo\nprint(foo)\n", encoding="utf-8")
        return proj

    async def test_zcode_unavailable_falls_to_llm_patch(self):
        proj = await self._mk_proj()
        orig_probe = tr._zcode_probe
        orig_chat = tr.TaskTemplateRunner._gateway_chat
        try:
            tr._zcode_probe = lambda: "cli_not_found"

            async def fake_chat(self, messages, temperature=0.0):
                self.tokens_spent += 100
                return json.dumps({"file": "main.py",
                                   "content": "print('fixed')\n"}, ensure_ascii=False)
            tr.TaskTemplateRunner._gateway_chat = fake_chat
            tpl = write_tpl([{"id": "fix", "type": "zcode_call", "args": {
                "project_dir": str(proj), "prompt": "修一下",
                "target_file": str(proj / "main.py"), "error_content": "ModuleNotFoundError: foo",
                "fallback": "llm_patch"}}])
            rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r4")).run()
            self.assertEqual("completed", rep["status"])
            self.assertIn("fallback:llm_patch", rep["steps"][0]["engine"])
            self.assertEqual("print('fixed')\n", (proj / "main.py").read_text(encoding="utf-8"))
            self.assertTrue((proj / "main.py.mcpbak").is_file())
            self.assertIn("-", rep["steps"][0]["output"]["diff"])
        finally:
            tr._zcode_probe = orig_probe
            tr.TaskTemplateRunner._gateway_chat = orig_chat

    async def test_zcode_real_engine(self):
        orig_probe, orig_real = tr._zcode_probe, tr._zcode_real
        try:
            tr._zcode_probe = lambda: ""
            tr._zcode_real = lambda prompt, project, timeout_sec: {"task_id": "t1",
                                                                   "result": {"exit_code": 0}}
            tpl = write_tpl([{"id": "fix", "type": "zcode_call", "retries": 0, "args": {
                "project_dir": "C:\\", "prompt": "p"}}])
            rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r5")).run()
            self.assertEqual("real:zcode", rep["steps"][0]["engine"])
        finally:
            tr._zcode_probe, tr._zcode_real = orig_probe, orig_real

    async def test_docker_fallback_local_exec_and_failure(self):
        proj = Path(tempfile.mkdtemp(prefix="mcp_proj_"))
        orig = tr._docker_available
        try:
            tr._docker_available = lambda: False
            tpl = write_tpl([{"id": "v", "type": "docker_sandbox", "retries": 0, "args": {
                "command": f'"{sys.executable}" -c "print(42)"', "workdir": str(proj),
                "fallback": "local_exec"}}])
            rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r6")).run()
            self.assertEqual("fallback:local_exec", rep["steps"][0]["engine"])
            self.assertIn("42", rep["steps"][0]["output"]["stdout"])
            # 失败命令 → VERIFY_FAILED + 重试2次
            tpl2 = write_tpl([{"id": "v2", "type": "docker_sandbox", "args": {
                "command": f'"{sys.executable}" -c "import sys;sys.exit(3)"',
                "workdir": str(proj), "fallback": "local_exec"}}])
            rep2 = await tr.TaskTemplateRunner(tpl2, {}, work_dir=str(TMP0 / "r7")).run()
            self.assertEqual("failed", rep2["status"])
            self.assertEqual(2, rep2["steps"][0]["attempts"])
            self.assertIn("VERIFY_FAILED", rep2["steps"][0]["error"])
        finally:
            tr._docker_available = orig

    async def test_local_exec_timeout(self):
        orig = tr._docker_available
        try:
            tr._docker_available = lambda: False
            tpl = write_tpl([{"id": "s", "type": "docker_sandbox", "retries": 0, "args": {
                "command": f'"{sys.executable}" -c "import time;time.sleep(5)"',
                "workdir": str(Path(tempfile.mkdtemp())), "exec_timeout": 1,
                "fallback": "local_exec"}}])
            rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r8")).run()
            self.assertIn("EXEC_TIMEOUT", rep["steps"][0]["error"])
        finally:
            tr._docker_available = orig


class TestBudget(unittest.IsolatedAsyncioTestCase):
    async def test_budget_trip_mid_llm(self):
        proj = Path(tempfile.mkdtemp(prefix="mcp_proj_"))
        (proj / "a.py").write_text("x=1\n", encoding="utf-8")
        orig_probe, orig_chat = tr._zcode_probe, tr.TaskTemplateRunner._gateway_chat
        try:
            tr._zcode_probe = lambda: "nope"

            async def fake_chat(self, messages, temperature=0.0):
                self.tokens_spent += 9
                if self.tokens_spent > self.token_budget:
                    raise tr.BudgetTripped(f"token 预算 {self.tokens_spent} > {self.token_budget}")
                return '{"file": "a.py", "content": "y=2\\n"}'
            tr.TaskTemplateRunner._gateway_chat = fake_chat
            tpl = write_tpl([
                {"id": "f1", "type": "zcode_call", "retries": 0, "args": {
                    "project_dir": str(proj), "target_file": str(proj / "a.py"),
                    "prompt": "p"}},
                {"id": "f2", "type": "zcode_call", "retries": 0, "args": {
                    "project_dir": str(proj), "target_file": str(proj / "a.py"),
                    "prompt": "p"}}])
            r = tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r9"), token_budget=10)
            rep = await r.run()
            self.assertIn("BUDGET", rep["steps"][1]["error"])
            self.assertIsNotNone(rep["human_intervention"])
        finally:
            tr._zcode_probe, tr.TaskTemplateRunner._gateway_chat = orig_probe, orig_chat

    async def test_budget_skips_rest_and_reports_progress(self):
        tpl = write_tpl([{"id": "a", "type": "report", "args": {"path": "{{work_dir}}/a.md",
                                                                "content": "x"}},
                         {"id": "b", "type": "report", "args": {"path": "{{work_dir}}/b.md",
                                                                "content": "y"}}])
        r = tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r10"), token_budget=5)
        r.tokens_spent = 5
        rep = await r.run()
        self.assertEqual("skipped", rep["steps"][0]["status"])
        self.assertTrue((TMP0 / "r10" / "progress.json").is_file())


class TestKnowledgeSteps(unittest.IsolatedAsyncioTestCase):
    async def test_read_write_roundtrip(self):
        tpl = write_tpl([
            {"id": "w", "type": "knowledge_write", "args": {"entry": {
                "project_id": "TRUT", "error_type": "E1", "root_cause_category": "C1",
                "solution": "S1", "success": "{{steps.w.status}}", "duration_sec": "3.5"}}},
            {"id": "r", "type": "knowledge_read", "args": {"project_id": "TRUT"}},
        ], name="kb")
        rep = await tr.TaskTemplateRunner(tpl, {}, work_dir=str(TMP0 / "r11")).run()
        self.assertEqual("completed", rep["status"])
        # 写入时 success 占位引用自身 steps.w（尚未发生）→ 渲染为 "" → False；再写一条 True 的用例
        tpl2 = write_tpl([
            {"id": "g", "type": "report", "args": {"path": "{{work_dir}}/g.md", "content": "ok"}},
            {"id": "w2", "type": "knowledge_write", "args": {"entry": {
                "project_id": "TRUT", "error_type": "E1", "root_cause_category": "C1",
                "solution": "S1", "success": "{{steps.g.status}}"}}},
            {"id": "r2", "type": "knowledge_read", "args": {"project_id": "TRUT"}},
        ], name="kb2")
        rep2 = await tr.TaskTemplateRunner(tpl2, {}, work_dir=str(TMP0 / "r12")).run()
        hint = rep2["steps"][2]["output"] if rep2["steps"][2]["status"] == "ok" else {}
        top = [s for s in rep2["steps"] if s["id"] == "r2"][0]
        self.assertEqual("ok", top["status"])


class TestUtility(unittest.TestCase):
    def test_extract_json_obj(self):
        self.assertEqual(tr._extract_json_obj('x {"a": "brace}in str"} y'), {"a": "brace}in str"})
        self.assertIsNone(tr._extract_json_obj("nope"))
        self.assertIsNone(tr._extract_json_obj('{"unclosed": 1'))

    def test_trim(self):
        out = tr._trim({"big": "z" * 9000, "d": {"s": "y" * 5000}})
        self.assertLess(len(out["big"]), 8100)
        self.assertLess(len(out["d"]["s"]), 4100)

    def test_resolve_path(self):
        root = {"a": [{"b": 1}]}
        self.assertEqual(1, tr.resolve_path(root, "a[0].b"))
        self.assertIsNone(tr.resolve_path(root, "a[9].b"))
        self.assertIsNone(tr.resolve_path(root, "zz"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
