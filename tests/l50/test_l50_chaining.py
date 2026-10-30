# -*- coding: utf-8 -*-
"""L5.0 阶段3 单元测试：on_success_next 模板串联（engine/task_runner.py 串联块）。

独立目录 tests/l50/（无 __init__.py → 不污染既有 discover）：
    server\\.venv\\Scripts\\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests\\l50\\test_l50_chaining.py

策略：串联模板对全部用临时目录里的 `report` 步骤模板（零网络、零 LLM）；
on_success_next 支持绝对路径（_template_path 的 .yaml 绝对路径分支），测试不依赖生产模板。
"""
import asyncio
import sys
import unittest
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

import engine.task_runner as tr


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l50_chain_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tpl(self, name, steps_yaml, extra=""):
        p = self.tmp / (name + ".yaml")
        p.write_text(
            "name: %s\nversion: 1\n%s\ndefaults:\n  retries: 0\n  timeout: 20\nsteps:\n%s" %
            (name, extra, steps_yaml),
            encoding="utf-8")
        return p

    def _report_step(self, sid, fname):
        return ("  - id: %s\n    type: report\n    timeout: 20\n"
                "    args:\n      path: \"{{work_dir}}/%s\"\n      content: report-%s\n" % (sid, fname, fname))

    def test_all_ok_triggers_chain(self):
        b = self._tpl("b_flow", self._report_step("b1", "b.md"))
        a = self._tpl("a_flow", self._report_step("a1", "a.md"),
                      extra="on_success_next: \"%s\"" % str(b).replace("\\", "/"))
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w"))
        rep = _run(runner.run())
        self.assertEqual(rep["status"], "completed")
        self.assertEqual(rep["task"], "a_flow→b_flow")
        ids = [s["id"] for s in rep["steps"]]
        self.assertEqual(ids, ["a1", "b1"])                       # 两段步骤拼进同一张卡
        self.assertTrue((self.tmp / "w" / "a.md").is_file())
        self.assertTrue((self.tmp / "w" / "b.md").is_file())      # 同一工作目录

    def test_failed_step_no_chain(self):
        b = self._tpl("b_flow", self._report_step("b1", "b.md"))
        # 中间步骤未知类型失败 + continue_on_fail:true → 整体 status 仍是 completed，
        # 但存在 failed 步 → 不满足"全部 ok"→ 不串联（verify 失败不跑测试的等价保护）。
        a = self._tpl("a_flow",
                      self._report_step("a1", "a.md") +
                      "  - id: bad\n    type: no_such_step\n    continue_on_fail: true\n" +
                      self._report_step("a2", "a2.md"),
                      extra="on_success_next: \"%s\"" % str(b).replace("\\", "/"))
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w2"))
        rep = _run(runner.run())
        self.assertEqual(rep["status"], "completed")               # continue_on_fail 语义不变
        self.assertEqual(rep["task"], "a_flow")                    # 没串联，名字不变
        ids = [s["id"] for s in rep["steps"]]
        self.assertNotIn("b1", ids)

    def test_chain_missing_template_stops_gracefully(self):
        a = self._tpl("a_flow", self._report_step("a1", "a.md"),
                      extra="on_success_next: no_such_template_xyz")
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w3"))
        rep = _run(runner.run())
        self.assertEqual(rep["status"], "completed")               # 主任务照常完成
        self.assertEqual(rep["task"], "a_flow")
        self.assertEqual([s["id"] for s in rep["steps"]], ["a1"])

    def test_budget_exhausted_skips_chain(self):
        b = self._tpl("b_flow", self._report_step("b1", "b.md"))
        a = self._tpl("a_flow", self._report_step("a1", "a.md"),
                      extra="on_success_next: \"%s\"" % str(b).replace("\\", "/"))
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w4"), token_budget=1)
        runner.tokens_spent = 1                                    # 模拟预算已烧完
        rep = _run(runner.run())
        self.assertEqual(rep["status"], "completed")
        self.assertNotIn("b1", [s["id"] for s in rep["steps"]])

    def test_multi_hop_chain(self):
        c = self._tpl("c_flow", self._report_step("c1", "c.md"))
        b = self._tpl("b_flow", self._report_step("b1", "b.md"),
                      extra="on_success_next: \"%s\"" % str(c).replace("\\", "/"))
        a = self._tpl("a_flow", self._report_step("a1", "a.md"),
                      extra="on_success_next: \"%s\"" % str(b).replace("\\", "/"))
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w5"))
        rep = _run(runner.run())
        self.assertEqual([s["id"] for s in rep["steps"]], ["a1", "b1", "c1"])
        self.assertEqual(rep["task"], "a_flow→b_flow→c_flow")

    def test_chain_cycle_guard(self):
        # a → b → a：chain_names 去重，不死循环
        b = self._tpl("b_flow", self._report_step("b1", "b.md"))
        a = self._tpl("a_flow", self._report_step("a1", "a.md"))
        b.write_text(b.read_text(encoding="utf-8") +
                     "on_success_next: \"%s\"\n" % str(a).replace("\\", "/"), encoding="utf-8")
        a.write_text(a.read_text(encoding="utf-8").replace("version: 1",
                     "version: 1\non_success_next: \"%s\"" % str(b).replace("\\", "/")), encoding="utf-8")
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w6"))
        rep = _run(runner.run())
        self.assertEqual([s["id"] for s in rep["steps"]], ["a1", "b1"])   # 只跑一轮

    def test_on_step_sees_chain_steps(self):
        b = self._tpl("b_flow", self._report_step("b1", "b.md"))
        a = self._tpl("a_flow", self._report_step("a1", "a.md"),
                      extra="on_success_next: \"%s\"" % str(b).replace("\\", "/"))
        seen = []
        runner = tr.TaskTemplateRunner(str(a), work_dir=str(self.tmp / "w7"),
                                       on_step=lambda o: seen.append(o.id))
        _run(runner.run())
        self.assertEqual(seen, ["a1", "b1"])                       # 任务卡实时进度覆盖串联段


class ChainHelpersTest(unittest.TestCase):
    def test_template_path_bare_and_yaml(self):
        got = tr._template_path("test_run_flow")
        self.assertIsNotNone(got)
        self.assertTrue(str(got).endswith("test_run_flow.yaml"))
        self.assertIsNone(tr._template_path("definitely_not_a_template_xyz"))

    def test_default_test_cmd_prefers_venv(self):
        d = Path(tempfile.mkdtemp(prefix="l50_cmd_"))
        try:
            venv_py = d / ".venv" / "Scripts" / "python.exe"
            venv_py.parent.mkdir(parents=True)
            venv_py.write_text("", encoding="utf-8")
            self.assertIn(".venv", str(tr.default_test_cmd(d)))
            self.assertIn("-m pytest -q", tr.default_test_cmd(d))
            bare = Path(tempfile.mkdtemp(prefix="l50_cmd2_"))
            try:
                self.assertEqual(tr.default_test_cmd(bare), "python -m pytest -q")
            finally:
                shutil.rmtree(bare, ignore_errors=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_chain_params_injects_test_cmd(self):
        proj = Path(tempfile.mkdtemp(prefix="l50_cp_"))
        tpl = proj / "t.yaml"
        tpl.write_text("x: \"{{params.test_cmd}}\"\n", encoding="utf-8")
        try:
            merged = tr._chain_params({"project_dir": str(proj)}, tpl)
            self.assertIn("pytest", merged["test_cmd"])
            merged2 = tr._chain_params({"project_dir": str(proj), "test_cmd": "custom"}, tpl)
            self.assertEqual(merged2["test_cmd"], "custom")        # 显式值不被覆盖
            tpl2 = proj / "t2.yaml"
            tpl2.write_text("no refs\n", encoding="utf-8")
            merged3 = tr._chain_params({"project_dir": str(proj)}, tpl2)
            self.assertNotIn("test_cmd", merged3)                  # 模板不引用就不注入
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_prod_error_fix_flow_declares_chain(self):
        import yaml
        p = tr.MORAY_ROOT / "templates" / "error_fix_flow.yaml"
        tpl = yaml.safe_load(p.read_text(encoding="utf-8"))
        self.assertEqual(tpl.get("on_success_next"), "test_run_flow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
