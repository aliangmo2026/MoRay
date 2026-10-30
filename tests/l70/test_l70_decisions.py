# -*- coding: utf-8 -*-
"""L7.0 阶段4 单元测试：动态决策引擎（分支判定/循环控制/风险判断/决策留痕）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l70/test_l70_decisions.py -q
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l70_dc_ut_"))

from engine import execution_scheduler as es   # noqa: E402


def _run_stub(pid="P"):
    plan = {"plan_id": "dcUT", "goal": "g", "source": "rule",
            "context": {"project_id": pid, "project_dir": str(TMP)}, "tasks": []}
    return es.AutonomousRun(plan, work_dir=str(TMP / "wd_dc"))


def _info(**kw):
    base = {"id": "t", "type": "review", "title": "t", "status": "ok",
            "auto_inserted": False, "summary": "", "duration_ms": 1}
    base.update(kw)
    return base


class DecisionBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()

    def tearDown(self):
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)

    def _sched(self, max_loop=2):
        return es.ExecutionScheduler(runner_factory=lambda *a: None, max_fix_loop=max_loop)


class ReviewDecisionTest(DecisionBase):
    def test_severe_new_issue_inserts_fix(self):
        """任务书 6.2-1：审查发现严重问题 → 自动进入修复流程。"""
        from knowledge import issue_base as ib
        ibx = ib.IssueBase()
        ibx.harvest_review("P", "1. 高 main.py:12 严重除零风险\n")
        s = self._sched()
        run = _run_stub()
        task = {"type": "review", "id": "t1", "_known_issues": set()}   # 快照为空=全是新增
        q = s._decide_after_review(task, _info(), run, [], "P", False, False)
        self.assertEqual(q[0]["type"], "fix")
        self.assertIn("除零风险", q[0]["error_text"])
        self.assertEqual(q[1]["type"], "test", "修复后必须自动验证")
        self.assertEqual(run.decisions[-1]["type"], "review→fix")

    def test_clean_review_skips_fix(self):
        """任务书 6.2-3：审查无重大问题 → 跳过修复，直接进入测试。"""
        s = self._sched()
        run = _run_stub()
        task = {"type": "review", "id": "t1", "_known_issues": set()}
        q = s._decide_after_review(task, _info(), run, [], "P", False, False)
        self.assertEqual([t["type"] for t in q], ["test"])
        self.assertEqual(run.decisions[-1]["type"], "review→skip-fix")

    def test_known_issues_not_retriggered(self):
        """快照命中（遗留发现）不重复触发修复——差分语义。"""
        from knowledge import issue_base as ib
        ibx = ib.IssueBase()
        ibx.harvest_review("P", "1. 高 main.py:12 严重除零风险\n")
        known = {str(r.get("root_cause_category") or "")
                 for r in ib.IssueBase().open_review_issues("P", limit=50)}
        s = self._sched()
        run = _run_stub()
        task = {"type": "review", "id": "t1", "_known_issues": known}
        q = s._decide_after_review(task, _info(), run, [], "P", False, False)
        self.assertEqual([t["type"] for t in q], ["test"])
        self.assertEqual(run.decisions[-1]["type"], "review→skip-fix")


class FixDecisionTest(DecisionBase):
    def test_core_file_forces_verification(self):
        """风险判断：核心文件修改 → 强制验证（决策留痕标注）。"""
        s = self._sched()
        run = _run_stub()
        info = _info(type="fix", status="ok", diff="diff a/main.py b/main.py")
        q, loops = s._decide_after_fix({"type": "fix"}, info, run, [], 0, False)
        self.assertEqual(q[0]["type"], "test")
        self.assertEqual(run.decisions[-1]["type"], "fix→test(风险)")
        self.assertIn("main.py", run.decisions[-1]["basis"])

    def test_non_core_simplified(self):
        s = self._sched()
        run = _run_stub()
        info = _info(type="fix", status="ok", diff="diff b/utils.py b/utils.py")
        q, _ = s._decide_after_fix({"type": "fix"}, info, run, [], 0, False)
        self.assertEqual(run.decisions[-1]["type"], "fix→test", "非核心=常规验证（简化确认）")

    def test_fix_retry_then_report(self):
        """循环控制：修复失败重试 1 次后（第 2 轮）再失败 → 跳过出报告。"""
        s = self._sched(max_loop=2)
        run = _run_stub()
        info = _info(type="fix", status="failed", error="LLM_PATCH_FAILED: bad json")
        q, loops = s._decide_after_fix({"type": "fix", "id": "f"}, info, run, [], 0, False)
        self.assertEqual(q[0]["type"], "fix", "限内必须重试")
        self.assertEqual(loops, 1)
        self.assertEqual(run.decisions[-1]["type"], "fix→retry")
        q, loops = s._decide_after_fix({"type": "fix", "id": "f"}, info, run, q, 1, False)
        self.assertEqual(loops, 2)
        q, loops = s._decide_after_fix({"type": "fix", "id": "f"}, info, run, q, 2, False)
        self.assertEqual(run.decisions[-1]["type"], "fix→report")
        self.assertEqual([t["type"] for t in q], [], "达上限后清空自动修复/测试")


class TestDecisionTest(DecisionBase):
    def test_pass_goes_report(self):
        s = self._sched()
        run = _run_stub()
        info = _info(type="test", tests_passed=True, test_exit_code=0)
        q, _ = s._decide_after_test(info and {"type": "test"}, info, run, [], 0, str(TMP), "P")
        self.assertEqual(run.decisions[-1]["type"], "test→report")
        self.assertEqual(q, [])

    def test_fail_drives_fix_with_fail_text(self):
        """任务书 6.2-4：测试失败 → 自动排错修复，修复入参带失败输出。"""
        s = self._sched()
        run = _run_stub()
        info = _info(type="test", tests_passed=False, test_exit_code=1,
                     fail_text="AssertionError: boom")
        q, loops = s._decide_after_test({"type": "test"}, info, run, [], 0, str(TMP), "P")
        self.assertEqual(q[0]["type"], "fix")
        self.assertIn("AssertionError", q[0]["error_text"])
        self.assertEqual(loops, 1)
        self.assertEqual(run.decisions[-1]["type"], "test→fix→retest")

    def test_fail_loop_capped(self):
        s = self._sched(max_loop=2)
        run = _run_stub()
        info = _info(type="test", tests_passed=False, test_exit_code=1,
                     fail_text="AssertionError: boom")
        q, loops = s._decide_after_test({"type": "test"}, info, run, [], 2, str(TMP), "P")
        self.assertEqual(run.decisions[-1]["type"], "test→report", "达上限如实交付")
        self.assertEqual(q, [])


class DecisionLogTest(DecisionBase):
    def test_decision_fields_traceable(self):
        """决策可追溯：at/type/reason/basis/result 全字段落 decisions[]。"""
        s = self._sched()
        run = _run_stub()
        run.add_decision("review→fix", "审查发现 1 个高严重度问题", "依据文本", "已插入修复任务")
        d = run.decisions[-1]
        for key in ("at", "type", "reason", "basis", "result"):
            self.assertTrue(d.get(key), key)
        self.assertIn("T", d["at"], "ISO 时间戳")


if __name__ == "__main__":
    unittest.main()
