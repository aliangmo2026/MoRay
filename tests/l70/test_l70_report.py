# -*- coding: utf-8 -*-
"""L7.0 阶段5 单元测试：报告整合器（五段式结构/问题聚合/经验沉淀/自我评估更新）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l70/test_l70_report.py -q
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l70_rp_ut_"))

from engine import execution_scheduler as es   # noqa: E402
from engine import report_integrator as ri     # noqa: E402


class ReportBase(unittest.TestCase):
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

    def _run(self, outcome="all_ok"):
        plan = {"plan_id": "rpUT", "goal": "优化这个项目", "source": "rule",
                "tasks": [{"id": "t1", "type": "review", "title": "审查"},
                          {"id": "t2", "type": "fix", "title": "修复"},
                          {"id": "t3", "type": "test", "title": "测试"}],
                "context": {"project_id": "P", "project_dir": str(TMP)}}
        run = es.AutonomousRun(plan, work_dir=str(TMP / "wd_rp"))
        run.subtasks = [
            {"id": "t1", "type": "review", "title": "审查", "status": "ok",
             "summary": "审查（ok）", "duration_ms": 10, "report_text": "审查结论：两处问题"},
            {"id": "t2", "type": "fix", "title": "修复", "status": "ok",
             "summary": "修复（ok）", "duration_ms": 20, "diff": "diff b/utils.py"},
            {"id": "t3", "type": "test", "title": "测试", "status": "ok",
             "summary": "测试（ok）", "duration_ms": 30, "tests_passed": True,
             "test_exit_code": 0},
        ]
        run.add_decision("review→fix", "审查发现 1 个高严重度问题", "依据", "已插入修复任务")
        run.add_decision("test→report", "测试全部通过，进入最终交付", "exit=0", "出报告")
        run.outcome = outcome
        run.status = "completed"
        run.ended_at = run.t0 + 60
        return run


class IntegrateTest(ReportBase):
    def test_five_sections(self):
        """统一结构：总览→执行过程→发现的问题→处理结果→建议。"""
        run = self._run()
        text = ri.integrate(run.plan, run)
        for head in ("## 1. 总览", "## 2. 执行过程", "## 3. 发现的问题",
                     "## 4. 处理结果", "## 5. 建议"):
            self.assertIn(head, text)
        self.assertIn("优化这个项目", text)
        self.assertIn("全部成功", text)
        self.assertIn("review→fix", text, "决策留痕必须出现在执行过程")
        self.assertIn("✅", text)

    def test_problems_aggregated(self):
        run = self._run()
        run.subtasks[2]["tests_passed"] = False
        run.subtasks[2]["fail_text"] = "AssertionError: boom"
        text = ri.integrate(run.plan, run)
        self.assertIn("[测试失败]", text)
        self.assertIn("AssertionError", text)

    def test_failure_report_honest(self):
        run = self._run(outcome="partial")
        run.subtasks[2]["status"] = "failed"
        text = ri.integrate(run.plan, run)
        self.assertIn("部分成功", text)
        self.assertIn("❌", text)


class SedimentTest(ReportBase):
    def test_experience_recorded_with_source(self):
        """全流程经验沉淀：source=自治任务、success 按 outcome。"""
        run = self._run(outcome="all_ok")
        out = ri.sediment(run.plan, run)
        self.assertTrue(out["recorded"])
        self.assertTrue(Path(out["summary_path"]).is_file())
        rows = [json.loads(l) for l in (TMP / "exp.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        rec = rows[-1]
        self.assertEqual(rec["source"], "自治任务")
        self.assertEqual(rec["error_type"], "autonomous-run")
        self.assertTrue(rec["success"])
        summary = json.loads(Path(out["summary_path"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["outcome"], "all_ok")
        self.assertEqual(summary["decisions"], 2)

    def test_partial_records_failure(self):
        run = self._run(outcome="partial")
        ri.sediment(run.plan, run)
        rows = [json.loads(l) for l in (TMP / "exp.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        self.assertFalse(rows[-1]["success"])

    def test_self_stats_reflects(self):
        """任务书 5.3：沉淀后自我评估统计自然反映（画像/成功率）。
        画像由经验库进化层产出（record() 钩子），本用例需开 MORAY_EXPERIENCE_EVOLUTION。"""
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)
        run = self._run(outcome="all_ok")
        ri.sediment(run.plan, run)
        from knowledge.experience_base import ExperienceBase
        eb = ExperienceBase()
        prof = eb.read_profile("P")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["records"], 1)
        self.assertEqual(prof["success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
