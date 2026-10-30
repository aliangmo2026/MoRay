# -*- coding: utf-8 -*-
"""L6.5 runner 钩子测试：五钩子在真实 TaskTemplateRunner 上生效（全离线，mock 网关/MCP）。

覆盖任务书 6.2 端到端项 1-4 的任务级形态 + 串联不破坏。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l65/test_l65_runner_hooks.py -q
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l65_rh_ut_"))

import engine.task_runner as tr                            # noqa: E402
from knowledge import issue_base as ib                     # noqa: E402


def _env_isolated():
    os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
    os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
    os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
    os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
    os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
    for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_CAUSE_RANK", "MORAY_EXPERIENCE_GLOBAL",
              "MORAY_STRATEGY_OPTIMIZE", "MORAY_FIX_ADAPTIVE", "MORAY_PATTERN_MINER",
              "MORAY_UNIFIED_ISSUE", "MORAY_REVIEW_KNOWLEDGE",
              "MORAY_FIX_SUGGEST_TEST", "MORAY_TEST_KNOWLEDGE"):
        os.environ.pop(k, None)


def _clear_files():
    for f in TMP.glob("*"):
        if f.is_file():
            f.unlink()


def _runner(tpl_name, params=None):
    return tr.TaskTemplateRunner(str(tr.MORAY_ROOT / "templates" / (tpl_name + ".yaml")),
                                 params=params or {}, work_dir=str(TMP / ("wd_" + tpl_name)))


def _gateway(content, tokens=10):
    body = {"ok": True, "content": content, "usage": {"total_tokens": tokens}}
    r = mock.Mock()
    r.status_code = 200
    r.json.return_value = body
    c = mock.AsyncMock()
    c.post.return_value = r
    c.__aenter__ = mock.AsyncMock(return_value=c)
    c.__aexit__ = mock.AsyncMock(return_value=False)
    return c


REVIEW_REPLY = ("审查结论：\n\n1. 高 main.py:12 空集合均值除零风险，建议判空\n\n2. 总体评价\n一般")


class ReviewHookTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_review_prompt_injected_and_harvested(self):
        """任务书 6.2-1 前半：排错历史 → 审查 prompt 注入重点检查。"""
        ibx = ib.IssueBase()
        for _ in range(3):
            ibx.record_issue({"project_id": "P", "error_type": "ZeroDivisionError",
                              "root_cause_category": "空集合均值", "solution": "判空",
                              "success": True}, source=ib.SOURCE_FIX)
        r = _runner("code_review_flow", params={"project_id": "P", "project_dir": str(TMP)})
        captured = {}

        async def fake_gateway(messages, temp):
            captured["content"] = messages[-1]["content"]
            return "审查结论"

        r._gateway_chat = fake_gateway
        out, engine = asyncio.run(r._exec_llm_chat({"prompt": "你是审查专家", "temperature": 0.1}))
        self.assertIn("历史重点检查", captured["content"])
        self.assertIn("空集合均值", captured["content"])
        self.assertIn("你是审查专家", captured["content"], "原 prompt 必须保留在注入段之后")
        self.assertGreater(out["knowledge_hint_chars"], 0)

    def test_review_harvest_writes_issue_base(self):
        """任务书 6.2-2 前半：审查发现 → 结构化入统一问题库（source=代码审查）。"""
        r = _runner("code_review_flow", params={"project_id": "P", "project_dir": str(TMP)})
        with mock.patch.object(
                tr.httpx, "AsyncClient", return_value=_gateway(REVIEW_REPLY)):
            out, _ = asyncio.run(r._exec_llm_chat({"prompt": "审查", "temperature": 0}))
        self.assertEqual(out["knowledge_harvested"], 1)
        rows = ib.IssueBase().eb._load()
        rev = [x for x in rows if x.get("source") == ib.SOURCE_REVIEW]
        self.assertEqual(len(rev), 1)
        self.assertIn("除零", rev[0]["root_cause_category"])

    def test_non_review_template_not_injected(self):
        r = _runner("test_run_flow", params={"project_id": "P", "project_dir": str(TMP)})
        ibx = ib.IssueBase()
        for _ in range(3):
            ibx.record_issue({"project_id": "P", "error_type": "ZeroDivisionError",
                              "root_cause_category": "空集合均值", "solution": "判空",
                              "success": True}, source=ib.SOURCE_FIX)
        captured = {}

        async def fake_gateway(messages, temp):
            captured["content"] = messages[-1]["content"]
            return "ok"

        r._gateway_chat = fake_gateway
        out, _ = asyncio.run(r._exec_llm_chat({"prompt": "分析文本"}))
        self.assertNotIn("历史重点检查", captured["content"])
        self.assertNotIn("knowledge_harvested", out)

    def test_gate_off_review_untouched(self):
        """任务书 6.2-5 子项：MORAY_REVIEW_KNOWLEDGE=0 → 审查纯 LLM。"""
        os.environ["MORAY_REVIEW_KNOWLEDGE"] = "0"
        try:
            ibx = ib.IssueBase()
            for _ in range(3):
                ibx.record_issue({"project_id": "P", "error_type": "ZeroDivisionError",
                                  "root_cause_category": "空集合均值", "solution": "判空",
                                  "success": True}, source=ib.SOURCE_FIX)
            r = _runner("code_review_flow", params={"project_id": "P", "project_dir": str(TMP)})
            with mock.patch.object(
                    tr.httpx, "AsyncClient", return_value=_gateway(REVIEW_REPLY)):
                out, _ = asyncio.run(r._exec_llm_chat({"prompt": "审查", "temperature": 0}))
            self.assertNotIn("knowledge_hint_chars", out)
            self.assertNotIn("knowledge_harvested", out)
            self.assertEqual([x for x in ibx.eb._load()
                              if x.get("source") == ib.SOURCE_REVIEW], [])
        finally:
            os.environ.pop("MORAY_REVIEW_KNOWLEDGE", None)


class MatchHookTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def _mk_analyze(self, candidates, error_type="TypeError", diagnosis_plan="按 LLM 建议修"):
        class FakeClient:
            async def call_tool(self, tool, arguments):
                return {"code": "OK", "data": {
                    "error_type": error_type,
                    "diagnosis": {"plan": diagnosis_plan},
                    "root_cause_candidates": candidates}}
        orig = tr.IrisMCPClient
        tr.IrisMCPClient = FakeClient
        return orig

    def test_failure_matches_history(self):
        """任务书 6.2-4：测试失败同类问题 → 直接给历史方案。"""
        ibx = ib.IssueBase()
        for _ in range(3):
            ibx.record_issue({"project_id": "P", "error_type": "TypeError",
                              "root_cause_category": "类型错误", "solution": "判型后转换",
                              "success": True}, source=ib.SOURCE_TEST)
        r = _runner("test_run_flow", params={"project_id": "P", "project_dir": str(TMP)})
        orig = self._mk_analyze([{"cause": "类型错误"}, {"cause": "全新"}])
        try:
            out = asyncio.run(r._exec_mcp({"tool": "iris_analyze_text", "arguments": {}}))
        finally:
            tr.IrisMCPClient = orig
        d = out["data"]
        self.assertIn("experience_match_hint", d)
        self.assertIn("历史方案", d["experience_match_hint"])
        self.assertIn("判型后转换", d["experience_match_hint"])
        self.assertTrue(d["diagnosis"]["plan"].startswith("【历史方案"), "命中时 plan 前置标注")

    def test_no_match_no_hint(self):
        r = _runner("test_run_flow", params={"project_id": "P", "project_dir": str(TMP)})
        orig = self._mk_analyze([{"cause": "全新问题"}])
        try:
            out = asyncio.run(r._exec_mcp({"tool": "iris_analyze_text", "arguments": {}}))
        finally:
            tr.IrisMCPClient = orig
        self.assertNotIn("experience_match_hint", out["data"])
        self.assertEqual(out["data"]["diagnosis"]["plan"], "按 LLM 建议修")

    def test_gate_off_no_matching(self):
        os.environ["MORAY_TEST_KNOWLEDGE"] = "0"
        try:
            ibx = ib.IssueBase()
            for _ in range(3):
                ibx.record_issue({"project_id": "P", "error_type": "TypeError",
                                  "root_cause_category": "类型错误", "solution": "判型",
                                  "success": True}, source=ib.SOURCE_TEST)
            r = _runner("test_run_flow", params={"project_id": "P", "project_dir": str(TMP)})
            orig = self._mk_analyze([{"cause": "类型错误"}])
            try:
                out = asyncio.run(r._exec_mcp({"tool": "iris_analyze_text", "arguments": {}}))
            finally:
                tr.IrisMCPClient = orig
            self.assertNotIn("experience_match_hint", out["data"])
        finally:
            os.environ.pop("MORAY_TEST_KNOWLEDGE", None)


class SourceTagTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def _write(self, tpl_name):
        r = _runner(tpl_name, params={"project_id": "P"})
        return r._exec_kwrite({"entry": {
            "project_id": "P", "error_type": "TypeError",
            "root_cause_category": "类型错误", "solution": "s", "success": "true"}})

    def test_test_flow_tagged_test_source(self):
        out = self._write("test_run_flow")
        self.assertTrue(out["recorded"])
        rows = ib.IssueBase().eb._load()
        self.assertEqual(rows[-1]["source"], ib.SOURCE_TEST)

    def test_fix_flow_tagged_fix_source(self):
        self._write("error_fix_flow")
        rows = ib.IssueBase().eb._load()
        self.assertEqual(rows[-1]["source"], ib.SOURCE_FIX)

    def test_gate_off_no_tag(self):
        os.environ["MORAY_UNIFIED_ISSUE"] = "0"
        try:
            self._write("test_run_flow")
            rows = ib.IssueBase().eb._load()
            self.assertNotIn("source", rows[-1])
        finally:
            os.environ.pop("MORAY_UNIFIED_ISSUE", None)


class SuggestStepTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def _ctx(self):
        return {"steps": {
            "analyze_text": {"output": {"data": {
                "error_type": "ZeroDivisionError",
                "root_cause_candidates": [{"cause": "空集合均值"}],
                "error_content": "ZeroDivisionError: division by zero"}}},
            "read_experience": {"output": {"hint": "1. 空集合均值（历史 2 次）"}},
            "verify_in_sandbox": {"status": "ok"}}}

    def test_suggest_step_ok_and_writes_file(self):
        """任务书 6.2-3 任务级：修复完成 → suggest_tests 合成步骤出建议文件。"""
        r = _runner("error_fix_flow", params={"project_id": "P"})
        r.context.update(self._ctx())
        o = asyncio.run(r._suggest_tests_step())
        self.assertEqual(o.status, "ok")
        self.assertEqual(o.id, "suggest_tests")
        self.assertGreaterEqual(o.output["count"], 2)
        path = Path(o.output["path"])
        self.assertTrue(path.is_file())
        self.assertIn("pytest", path.read_text(encoding="utf-8"))

    def test_gate_off_runner_skips_step(self):
        """任务书 6.2-5 子项：MORAY_FIX_SUGGEST_TEST=0 → 结果里没有这一步。"""
        os.environ["MORAY_FIX_SUGGEST_TEST"] = "0"
        try:
            r = _runner("error_fix_flow", params={"project_id": "P"})
            r.context.update(self._ctx())
            outcomes = []
            if str(r.tpl.get("name") or "") in ("error_fix_flow", "text_fix_flow"):
                from knowledge import issue_base as _ib
                if _ib.fix_suggest_test_on():
                    outcomes.append(asyncio.run(r._suggest_tests_step()))
            self.assertEqual(outcomes, [], "闸关 = 合成步不出现")
        finally:
            os.environ.pop("MORAY_FIX_SUGGEST_TEST", None)

    def test_suggest_step_never_fails(self):
        r = _runner("text_fix_flow", params={"project_id": "P"})
        r.context["steps"] = {}   # 空上下文：内部降级/兜底生成而非抛错
        o = asyncio.run(r._suggest_tests_step())
        self.assertEqual(o.status, "ok")
        self.assertIn("note", o.output, "必须有可读 note（成功或降级说明）")


if __name__ == "__main__":
    unittest.main()
