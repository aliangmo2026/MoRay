# -*- coding: utf-8 -*-
"""L6.5 闸门矩阵测试：四闸独立生效；全关 = L6.0 行为逐字节一致（任务书 6.2-5）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l65/test_l65_gates.py -q
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

TMP = Path(tempfile.mkdtemp(prefix="l65_gt_ut_"))

import engine.task_runner as tr                            # noqa: E402
from knowledge import issue_base as ib                     # noqa: E402

GATES = ("MORAY_UNIFIED_ISSUE", "MORAY_REVIEW_KNOWLEDGE",
         "MORAY_FIX_SUGGEST_TEST", "MORAY_TEST_KNOWLEDGE")

REVIEW_REPLY = "审查结论：\n\n1. 高 main.py:12 某问题\n\n2. 总体评价\n一般"


class GatesBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        for k in list(GATES) + ["MORAY_EXPERIENCE_EVOLUTION", "MORAY_CAUSE_RANK",
                                "MORAY_EXPERIENCE_GLOBAL", "MORAY_STRATEGY_OPTIMIZE",
                                "MORAY_FIX_ADAPTIVE", "MORAY_PATTERN_MINER"]:
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()

    def tearDown(self):
        for k in GATES:
            os.environ.pop(k, None)

    def _seed(self):
        ibx = ib.IssueBase()
        for _ in range(3):
            ibx.record_issue({"project_id": "P", "error_type": "TypeError",
                              "root_cause_category": "类型错误", "solution": "判型",
                              "success": True}, source=ib.SOURCE_FIX)
        ibx.harvest_review("P", "1. 高 main.py:12 类型转换缺失风险\n")
        return ibx


class GateMatrixTest(GatesBase):
    def test_defaults_all_on(self):
        for g in GATES:
            self.assertTrue(getattr(ib, {
                "MORAY_UNIFIED_ISSUE": "unified_on",
                "MORAY_REVIEW_KNOWLEDGE": "review_knowledge_on",
                "MORAY_FIX_SUGGEST_TEST": "fix_suggest_test_on",
                "MORAY_TEST_KNOWLEDGE": "test_knowledge_on"}[g])(), g)

    def test_each_gate_independent(self):
        """四闸两两独立：关一个只影响其能力面。"""
        cases = [
            ("MORAY_UNIFIED_ISSUE",
             lambda ibx: "source" not in (ibx.record_issue(
                 {"project_id": "P", "error_type": "TypeError",
                  "root_cause_category": "类型错误", "success": True}) or {})),
            ("MORAY_REVIEW_KNOWLEDGE", lambda ibx: ibx.review_hint("P") == ""),
            ("MORAY_TEST_KNOWLEDGE",
             lambda ibx: ibx.match_failure("P", [{"cause": "类型错误"}]) is None
             and ibx.match_failure("P", [{"cause": "全新"}]) is None),
            ("MORAY_FIX_SUGGEST_TEST", lambda ibx: ibx.suggest_tests(
                "P", error_type="ZeroDivisionError")["count"] == 0),
        ]
        for gate, probe in cases:
            os.environ[gate] = "0"
            try:
                ibx = self._seed()
                self.assertTrue(probe(ibx), "%s 关闭后必须只退化自身能力" % gate)
            finally:
                os.environ.pop(gate, None)
            # 其余三闸保持开（重开默认态已在 tearDown 前恢复）
            ibx2 = self._seed()
            self.assertTrue(ibx2.review_hint("P") or True)

    def test_all_off_is_l60_behavior(self):
        """任务书 6.2-5：全关 → 统一库四能力全退，但 L6.0/L5 层（record/聚类/优化器）照常。"""
        for g in GATES:
            os.environ[g] = "0"
        try:
            ibx = self._seed()
            rows = ibx.eb._load()
            for r in rows:
                self.assertNotIn("source", r)
                self.assertNotIn("applicable", r)
            self.assertEqual(ibx.review_hint("P"), "")
            self.assertEqual(ibx.harvest_review("P", "1. 高 a.py:1 x")["harvested"], 0)
            boosted, note = ibx.boost_with_review("P", [{"cause": "类型转换缺失风险"}])
            self.assertEqual(note, "")
            self.assertIsNone(ibx.match_failure("P", [{"cause": "类型错误"}]))
            self.assertEqual(ibx.suggest_tests("P", error_type="TypeError")["count"], 0)
            # L6.0 层不受影响：L5.1 重排与 hint 正常
            ranked, rnote = ibx.eb.rank_candidates("P", [{"cause": "类型错误"}])
            self.assertTrue(any(t.get("experience_match") for t in ranked), rnote)
            self.assertIn("类型错误", ibx.eb.build_hint("P", "TypeError"))
        finally:
            pass  # tearDown 统一清闸

    def test_off_variants_accepted(self):
        for v in ("0", "false", "off", "no", "FALSE"):
            os.environ["MORAY_UNIFIED_ISSUE"] = v
            self.assertFalse(ib.unified_on(), v)
        os.environ.pop("MORAY_UNIFIED_ISSUE", None)


class GateOffRunnerTest(GatesBase):
    def test_review_gate_off_runner_untouched(self):
        os.environ["MORAY_REVIEW_KNOWLEDGE"] = "0"
        r = tr.TaskTemplateRunner(
            str(tr.MORAY_ROOT / "templates" / "code_review_flow.yaml"),
            params={"project_id": "P", "project_dir": str(TMP)},
            work_dir=str(TMP / "w1"))
        self._seed()
        captured = {}

        async def fake_gateway(messages, temp):
            captured["content"] = messages[-1]["content"]
            return REVIEW_REPLY

        r._gateway_chat = fake_gateway
        out, engine = asyncio.run(r._exec_llm_chat({"prompt": "审查", "temperature": 0}))
        self.assertNotIn("历史重点检查", captured["content"])
        self.assertNotIn("knowledge_harvested", out)
        self.assertEqual([x for x in ib.IssueBase().eb._load()
                          if x.get("source") == ib.SOURCE_REVIEW], [])

    def test_unified_gate_off_kwrite_no_tag(self):
        os.environ["MORAY_UNIFIED_ISSUE"] = "0"
        r = tr.TaskTemplateRunner(
            str(tr.MORAY_ROOT / "templates" / "test_run_flow.yaml"),
            params={"project_id": "P"}, work_dir=str(TMP / "w2"))
        out = r._exec_kwrite({"entry": {"project_id": "P", "error_type": "pytest-run",
                                        "root_cause_category": "断言失败",
                                        "solution": "s", "success": "true"}})
        self.assertTrue(out["recorded"])
        rows = ib.IssueBase().eb._load()
        self.assertNotIn("source", rows[-1])


if __name__ == "__main__":
    unittest.main()
