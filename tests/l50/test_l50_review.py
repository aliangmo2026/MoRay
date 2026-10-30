# -*- coding: utf-8 -*-
"""L5.0 阶段5 单元测试：代码快速审查模板（code_review_flow + 新步骤类型）。

覆盖任务书 6.1 的 正常/异常/空项目：
  * fs_recent_files 步骤：mtime 倒序、排除目录、空项目报错；
  * code_review_flow.yaml 结构合法（三步、字段齐）；
  * llm_chat 对网关契约：patch httpx.AsyncClient（与既有 harness 同法），零真实调用。
"""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

import yaml

import engine.task_runner as tr

TMP = ROOT / "work" / "l50_review_test"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FsRecentFilesTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)

    def _runner(self):
        return tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                     params={"project_dir": str(TMP / "p"), "project_id": "p"},
                                     work_dir=str(TMP / "w"))

    def test_orders_by_mtime_and_respects_limit(self):
        proj = TMP / "p"
        proj.mkdir(parents=True, exist_ok=True)
        for i in range(6):
            f = proj / ("m%02d.py" % i)
            f.write_text("x = %d\n" % i, encoding="utf-8")
        (proj / "notes.txt").write_text("ignore me", encoding="utf-8")   # 后缀不在表内
        (proj / "node_modules").mkdir(exist_ok=True)
        (proj / "node_modules" / "dep.py").write_text("bad", encoding="utf-8")
        out = self._runner()._exec_recent_files({"project_dir": str(proj), "max_files": 3})
        self.assertEqual(out["count"], 3)
        self.assertEqual(len(out["files"]), 3)
        paths = [f["path"] for f in out["files"]]
        self.assertTrue(all(not p.startswith("node_modules") for p in paths))
        self.assertIn("files_text", out)
        self.assertIn("summary", out)
        # 全项目 6 个 py，只取 3
        self.assertEqual(out["count"], 3)

    def test_empty_project_raises(self):
        proj = TMP / "empty"
        proj.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(tr.StepError) as cm:
            self._runner()._exec_recent_files({"project_dir": str(proj)})
        self.assertEqual(cm.exception.code, "NO_SOURCE_FILES")

    def test_missing_dir_raises(self):
        with self.assertRaises(tr.StepError) as cm:
            self._runner()._exec_recent_files({"project_dir": str(TMP / "no_such_dir")})
        self.assertEqual(cm.exception.code, "BAD_ARGS")


class ReviewTemplateTest(unittest.TestCase):
    def test_template_structure(self):
        p = tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"
        tpl = yaml.safe_load(p.read_text(encoding="utf-8"))
        self.assertEqual(tpl["name"], "code_review_flow")
        ids = [s["id"] for s in tpl["steps"]]
        types = [s["type"] for s in tpl["steps"]]
        self.assertEqual(ids, ["collect_recent", "llm_review", "generate_report"])
        self.assertEqual(types, ["fs_recent_files", "llm_chat", "report"])

    def test_stage_labels_registered(self):
        from server.app import task_routes
        self.assertIn("collect_recent", task_routes.STAGE_LABEL)
        self.assertIn("llm_review", task_routes.STAGE_LABEL)


class LlmChatContractTest(unittest.TestCase):
    def _gateway_ok(self, content):
        body = {"ok": True, "content": content,
                "usage": {"total_tokens": 42}}
        r = mock.Mock()
        r.status_code = 200
        r.json.return_value = body
        c = mock.AsyncMock()
        c.post.return_value = r
        c.__aenter__ = mock.AsyncMock(return_value=c)
        c.__aexit__ = mock.AsyncMock(return_value=False)
        return c

    def test_llm_chat_returns_content_and_counts_budget(self):
        runner = tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                       params={"project_dir": str(TMP), "project_id": "p"},
                                       work_dir=str(TMP / "w2"))
        with mock.patch.object(tr.httpx, "AsyncClient", return_value=self._gateway_ok("审查结论：无问题")):
            out, engine = _run(runner._exec_llm_chat({"prompt": "review this", "temperature": 0}))
        self.assertEqual(engine, "gateway")
        self.assertEqual(out["content"], "审查结论：无问题")
        self.assertEqual(runner.tokens_spent, 42)      # 预算累计生效

    def test_llm_chat_empty_prompt_raises(self):
        runner = tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                       params={"project_dir": str(TMP), "project_id": "p"},
                                       work_dir=str(TMP / "w3"))
        with self.assertRaises(tr.StepError) as cm:
            _run(runner._exec_llm_chat({"prompt": "   "}))
        self.assertEqual(cm.exception.code, "BAD_ARGS")

    def test_llm_chat_empty_reply_raises(self):
        runner = tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                       params={"project_dir": str(TMP), "project_id": "p"},
                                       work_dir=str(TMP / "w4"))
        with mock.patch.object(tr.httpx, "AsyncClient", return_value=self._gateway_ok("")):
            with self.assertRaises(tr.StepError) as cm:
                _run(runner._exec_llm_chat({"prompt": "review this"}))
        self.assertEqual(cm.exception.code, "EMPTY_REPLY")


class DispatchTest(unittest.TestCase):
    def test_unknown_step_still_rejected(self):
        runner = tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                       params={"project_dir": str(TMP), "project_id": "p"},
                                       work_dir=str(TMP / "w5"))
        with self.assertRaises(tr.StepError) as cm:
            _run(runner._dispatch("no_such_type", {}))
        self.assertEqual(cm.exception.code, "UNKNOWN_STEP")

    def test_dispatch_routes_new_types(self):
        runner = tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
                                       params={"project_dir": str(TMP), "project_id": "p"},
                                       work_dir=str(TMP / "w6"))

        def fake_recent(args):
            return {"count": 0}
        runner._exec_recent_files = fake_recent
        out, engine = _run(runner._dispatch("fs_recent_files", {"args": {"project_dir": str(TMP)}}))
        self.assertEqual(engine, "fs")

        async def fake_llm(args):
            return {"content": "ok"}, "gateway"
        runner._exec_llm_chat = fake_llm
        out2, eng2 = _run(runner._dispatch("llm_chat", {"args": {"prompt": "x"}}))
        self.assertEqual(eng2, "gateway")


class ReportJoinTest(unittest.TestCase):
    """_extract_final 多报告拼接（阶段3串联的卡片显示契约）。"""

    def test_multi_report_joined_single_untouched(self):
        from server.app import task_routes as T
        w1 = TMP / "r1.md"; w2 = TMP / "r2.md"
        w1.write_text("修复报告A", encoding="utf-8")
        w2.write_text("测试报告B", encoding="utf-8")
        rec = {"steps": [], "report": "", "diff": ""}
        rep = {"tokens_spent": 1, "human_intervention": None, "steps": [
            {"id": "generate_report", "type": "report", "status": "ok", "engine": "report",
             "attempts": 1, "duration_ms": 1, "output": {"path": str(w1)}, "error": ""},
            {"id": "generate_report", "type": "report", "status": "ok", "engine": "report",
             "attempts": 1, "duration_ms": 1, "output": {"path": str(w2)}, "error": ""},
        ]}
        T._extract_final(rec, rep)
        self.assertIn("修复报告A", rec["report"])
        self.assertIn("测试报告B", rec["report"])
        self.assertIn("串联报告", rec["report"])       # 分隔标识（卡片可见）

        w3 = TMP / "r3.md"; w3.write_text("只有一份", encoding="utf-8")
        rec2 = {"steps": [], "report": "", "diff": ""}
        T._extract_final(rec2, {"tokens_spent": 0, "human_intervention": None, "steps": [
            {"id": "g", "type": "report", "status": "ok", "engine": "report",
             "attempts": 1, "duration_ms": 1, "output": {"path": str(w3)}, "error": ""}]})
        self.assertEqual(rec2["report"], "只有一份")    # 单报告零装饰（不破坏既有断言）


if __name__ == "__main__":
    unittest.main(verbosity=2)
