# -*- coding: utf-8 -*-
"""L7.0 阶段2 单元测试：任务规划器（目标拆解/类型识别/依赖编排/规则兜底/总闸默认关）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l70/test_l70_planner.py -q
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l70_pl_ut_"))

from engine import task_planner as tp   # noqa: E402

GOOD_LLM = asyncio.coroutine if False else None  # 占位（py3.8 无需）


def _fake_llm(content):
    async def call(prompt):
        return content
    return call


def _bad_llm():
    async def call(prompt):
        raise RuntimeError("gateway down")
    return call


class PlannerBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"   # 关进化层（规划测试只关心拆解）
        for k in ("MORAY_TASK_PLANNER",):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()

    def tearDown(self):
        os.environ.pop("MORAY_TASK_PLANNER", None)
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)


class GateTest(PlannerBase):
    def test_default_off(self):
        """红线7：总闸默认关闭 → decompose 零成本返回 disabled，不调 LLM。"""
        called = []

        async def spy(prompt):
            called.append(1)
            return "{}"
        plan = asyncio.run(tp.decompose("优化一下这个项目", "P", llm_call=spy))
        self.assertFalse(plan["enabled"])
        self.assertEqual(plan["source"], "disabled")
        self.assertEqual(plan["tasks"], [])
        self.assertEqual(called, [], "闸关绝不能触发 LLM 调用")

    def test_gate_on_requires_env(self):
        os.environ["MORAY_TASK_PLANNER"] = "1"
        self.assertTrue(tp.planner_on())
        os.environ["MORAY_TASK_PLANNER"] = "true"
        self.assertTrue(tp.planner_on())
        os.environ["MORAY_TASK_PLANNER"] = "0"
        self.assertFalse(tp.planner_on())


class DecomposeTest(PlannerBase):
    def setUp(self):
        super().setUp()
        os.environ["MORAY_TASK_PLANNER"] = "1"

    def test_llm_plan_linear_deps_and_report_end(self):
        """任务书 6.2-1：大目标 → 拆解为 审查→修复→测试→报告，依赖线性、报告收尾。"""
        raw = ('{"tasks":[{"type":"review","title":"审查","why":"w"},'
               '{"type":"fix","title":"修复","why":"w"},'
               '{"type":"test","title":"测试","why":"w"},'
               '{"type":"report","title":"报告","why":"w"}]}')
        plan = asyncio.run(tp.decompose("优化一下这个项目", "P", llm_call=_fake_llm(raw)))
        self.assertEqual(plan["source"], "llm")
        self.assertEqual([t["type"] for t in plan["tasks"]],
                         ["review", "fix", "test", "report"])
        self.assertEqual(plan["tasks"][0]["deps"], [])
        self.assertEqual(plan["tasks"][1]["deps"], [plan["tasks"][0]["id"]],
                         "后一个消费前一个（线性 DAG）")
        self.assertEqual(plan["tasks"][-1]["type"], "report")

    def test_llm_missing_report_appended(self):
        raw = '{"tasks":[{"type":"review","title":"审查","why":"w"}]}'
        plan = asyncio.run(tp.decompose("全面检查", "P", llm_call=_fake_llm(raw)))
        self.assertEqual(plan["tasks"][-1]["type"], "report", "报告收尾自动补齐")

    def test_llm_bad_types_filtered(self):
        raw = ('{"tasks":[{"type":"deploy","title":"x","why":"w"},'
               '{"type":"test","title":"t","why":"w"},'
               '{"type":"logs","title":"l","why":"w"}]}')
        plan = asyncio.run(tp.decompose("检查服务状态", "P", llm_call=_fake_llm(raw)))
        self.assertEqual([t["type"] for t in plan["tasks"]][:-1], ["test", "logs"])

    def test_llm_failure_rule_fallback(self):
        """LLM 坏掉 → 规则兜底（诚实标 source=rule），无 LLM 也出可用计划。"""
        plan = asyncio.run(tp.decompose("优化一下这个项目", "P", llm_call=_bad_llm()))
        self.assertEqual(plan["source"], "rule")
        self.assertIn("规则兜底", plan["notes"])
        self.assertEqual(plan["tasks"][0]["type"], "review")
        self.assertEqual(plan["tasks"][-1]["type"], "report")

    def test_rule_chains_by_goal(self):
        cases = [("检查一下服务状态", "logs"),
                 ("修复一下项目里的问题", "fix"),
                 ("全面体检这个项目", "review"),
                 ("帮我跑测试", "test")]
        for goal, first in cases:
            plan = asyncio.run(tp.decompose(goal, "P", llm_call=_bad_llm()))
            self.assertEqual(plan["tasks"][0]["type"], first, goal)

    def test_plan_id_injection(self):
        plan = asyncio.run(tp.decompose("优化一下", "P", llm_call=_bad_llm(),
                                        plan_id="fixed123"))
        self.assertEqual(plan["plan_id"], "fixed123", "受理端点预生成凭证语义")

    def test_context_in_prompt(self):
        """拆解依据注入：画像/高频根因进 prompt（任务书 2.2）。"""
        from knowledge import issue_base as ib
        ibx = ib.IssueBase()
        for _ in range(3):
            ibx.record_issue({"project_id": "P", "error_type": "ModuleNotFoundError",
                              "root_cause_category": "虚拟环境未安装依赖",
                              "solution": "pip install x", "success": True},
                             source=ib.SOURCE_FIX)
        seen = {}

        async def spy(prompt):
            seen["p"] = prompt
            return '{"tasks":[{"type":"review","title":"r","why":"w"}]}'
        plan = asyncio.run(tp.decompose("优化一下", "P", llm_call=spy))
        self.assertEqual(plan["source"], "llm")
        self.assertIn("虚拟环境未安装依赖", seen["p"], "高频根因必须进规划 prompt")
        self.assertIn("P", seen["p"])


if __name__ == "__main__":
    unittest.main()
