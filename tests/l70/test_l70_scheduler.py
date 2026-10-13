# -*- coding: utf-8 -*-
"""L7.0 阶段3 单元测试：执行调度器（类型映射/参数传递/状态管理/离线全链）。

runner_factory 注入 FakeRunner → 不触网关/Iris；log_monitor 注入 noop。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l70/test_l70_scheduler.py -q
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l70_sc_ut_"))

from engine import execution_scheduler as es   # noqa: E402
from engine import task_planner as tp          # noqa: E402


def _progress(tpl, exit_code=0, diff="", human=None, report_text="报告文本"):
    """构造模板 progress dict（形状对齐 task_runner.run() 出参）。"""
    steps = [{"id": "run_tests" if tpl == "test_run_flow" else "x", "type": "x",
              "status": "ok", "engine": "e", "output": {"exit_code": exit_code,
                                                        "stdout": "1 passed" if exit_code == 0 else "1 failed",
                                                        "stderr": "" if exit_code == 0 else "AssertionError: boom"}}]
    if tpl == "text_fix_flow":
        steps = [{"id": "fix_code", "type": "zcode_call", "status": "ok",
                  "output": {"diff": diff or "b/utils.py"}}]
    report_path = TMP / ("rep_%s.md" % tpl)
    report_path.write_text(report_text, encoding="utf-8")
    steps.append({"id": "generate_report", "type": "report", "status": "ok",
                  "output": {"path": str(report_path)}})
    return {"task": tpl, "status": "completed", "steps": steps,
            "human_intervention": human, "tokens_spent": 0}


class FakeRunner:
    """按模板名回放预设 progress；记录收到的 params 供传参断言。"""
    calls = []

    def __init__(self, tpl, params, work_dir, script):
        self.tpl, self.params = tpl, params
        FakeRunner.calls.append({"tpl": tpl, "params": params})
        self._script = script

    async def run(self):
        return self._script.get(self.tpl, _progress(self.tpl))


class SchedulerBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        for k in ("MORAY_TASK_PLANNER",):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):     # 经验库/报告全清（发现条目不得跨用例泄漏）
            if f.is_file():
                f.unlink()
        FakeRunner.calls = []
        self.script = {}
        self.sched = es.ExecutionScheduler(
            runner_factory=lambda tpl, params, wd: FakeRunner(tpl, params, wd, self.script))

    def tearDown(self):
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)

    def _plan(self, types, pid="P", pdir=str(TMP)):
        tasks = [{"id": "t%d" % (i + 1), "type": t, "title": t, "deps": [],
                  "expected": ""} for i, t in enumerate(types)]
        return {"plan_id": "plUT%d" % len(types), "goal": "测试目标",
                "source": "rule", "context": {"project_id": pid, "project_dir": pdir},
                "tasks": tasks}


class MappingTest(SchedulerBase):
    def test_type_to_template_mapping(self):
        """任务书 3.2：审查/修复/测试 → 对应原子单元（模板名映射）。"""
        self.script = {"code_review_flow": _progress("code_review_flow"),
                       "test_run_flow": _progress("test_run_flow", exit_code=0)}
        run = asyncio.run(self.sched.run_plan(self._plan(["review", "test"])))
        got = [c["tpl"] for c in FakeRunner.calls]
        self.assertEqual(got, ["code_review_flow", "test_run_flow"])
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.outcome, "all_ok")

    def test_params_passed(self):
        """任务书 3.2：入参自动装配（project_dir/project_id/verify_cmd/test_cmd）。"""
        self.script = {"code_review_flow": _progress("code_review_flow"),
                       "test_run_flow": _progress("test_run_flow", exit_code=0)}
        asyncio.run(self.sched.run_plan(self._plan(["review", "test"], pid="MyProj")))
        rev = next(c for c in FakeRunner.calls if c["tpl"] == "code_review_flow")
        self.assertEqual(rev["params"]["project_id"], "MyProj")
        tst = next(c for c in FakeRunner.calls if c["tpl"] == "test_run_flow")
        self.assertIn("test_cmd", tst["params"], "test_cmd 必须自动补齐（venv 探测同源）")

    def test_logs_action_noop_analysis(self):
        os.environ["MONITOR_LOG_DIRS"] = str(TMP / "logs")
        try:
            from server.app import log_monitor as lm
            # C2a 结构隔离：上面那行 env 不生效（config 变量名是 MONITOR_LOG_DIR 单数，
            # 且 import 期已定值）——直接 patch 模块属性：默认扫描目录→空、
            # 单例 extra_dir→本用例空替身（目录不存在=跳过）、STATE_FILE→tmp（防写生产 alerts.json）
            cfg = lm.config
            orig_dirs = cfg.MONITOR_LOG_DIRS
            cfg.MONITOR_LOG_DIRS = ()
            orig_extra = lm.manager.extra_dir
            lm.manager.extra_dir = str(TMP / "logs")
            orig_sf, orig_sd = lm.STATE_FILE, lm.STATE_DIR
            lm.STATE_FILE = TMP / "state_l70.json"
            lm.STATE_DIR = lm.STATE_FILE.parent
            async def noop(text, project_name):
                return None, "离线跳过"
            orig = lm.analyze_error
            lm.analyze_error = noop
            lm.manager.state = {"seen": {}, "alerts": []}
            try:
                run = asyncio.run(self.sched.run_plan(self._plan(["logs"])))
            finally:
                lm.analyze_error = orig
            self.assertEqual(run.subtasks[0]["type"], "logs")
            self.assertEqual(run.subtasks[0]["status"], "ok")
            self.assertIn("待处理告警", run.subtasks[0]["summary"])
        finally:
            cfg.MONITOR_LOG_DIRS = orig_dirs
            lm.manager.extra_dir = orig_extra
            lm.STATE_FILE, lm.STATE_DIR = orig_sf, orig_sd
            os.environ.pop("MONITOR_LOG_DIRS", None)


class UpstreamDownstreamTest(SchedulerBase):
    def test_review_severe_feeds_fix_error_text(self):
        """任务书 3.2 参数传递：审查出的问题列表 → 修复任务的 error_text。
        收割发生在 review 执行期间（对齐真实 L6.5 钩子时机——快照之前的才算遗留）。"""
        from knowledge import issue_base as ib
        ibx = ib.IssueBase()
        self.script = {"text_fix_flow": _progress("text_fix_flow"),
                       "test_run_flow": _progress("test_run_flow", exit_code=0)}

        def factory(tpl, params, wd):
            if tpl == "code_review_flow":
                class ReviewRunner:
                    async def run(self):
                        ibx.harvest_review("P", "1. 高 main.py:12 严重除零风险\n")
                        return _progress("code_review_flow")
                return ReviewRunner()
            return FakeRunner(tpl, params, wd, self.script)   # __init__ 已自动记录 calls
        self.sched = es.ExecutionScheduler(runner_factory=factory)
        run = asyncio.run(self.sched.run_plan(self._plan(["review"])))
        fix_calls = [c for c in FakeRunner.calls if c["tpl"] == "text_fix_flow"]
        self.assertEqual(len(fix_calls), 1, "严重发现必须自动插入修复")
        self.assertIn("除零风险", fix_calls[0]["params"]["error_text"])
        self.assertTrue(any(d["type"] == "review→fix" for d in run.decisions))

    def test_old_review_findings_not_retriggered(self):
        """审查前已存在的遗留发现（快照命中）不得重复触发修复。"""
        from knowledge import issue_base as ib
        ibx = ib.IssueBase()
        ibx.harvest_review("P", "1. 高 main.py:12 严重除零风险\n")   # 遗留：快照之前
        self.script = {"code_review_flow": _progress("code_review_flow"),
                       "test_run_flow": _progress("test_run_flow", exit_code=0)}
        run = asyncio.run(self.sched.run_plan(self._plan(["review"])))
        self.assertEqual([c for c in FakeRunner.calls if c["tpl"] == "text_fix_flow"], [])
        self.assertTrue(any(d["type"] == "review→skip-fix" for d in run.decisions))

    def test_fix_root_cause_feeds_test(self):
        """修复后自动测试（test_cmd/参数齐备，同链继承）。"""
        self.script = {"text_fix_flow": _progress("text_fix_flow"),
                       "test_run_flow": _progress("test_run_flow", exit_code=0)}
        run = asyncio.run(self.sched.run_plan(self._plan(["fix"])))
        self.assertIn("fix→test", [d["type"] for d in run.decisions])
        self.assertEqual(len([c for c in FakeRunner.calls if c["tpl"] == "test_run_flow"]), 1)


class StateTest(SchedulerBase):
    def test_persist_and_view(self):
        self.script = {"test_run_flow": _progress("test_run_flow", exit_code=0)}
        run = asyncio.run(self.sched.run_plan(self._plan(["test"])))
        disk = TMP / "autonomous" if False else run.work_dir / "run.json"
        self.assertTrue(disk.is_file(), "状态必须落盘 work/autonomous/{id}/run.json")
        data = json.loads(disk.read_text(encoding="utf-8"))
        self.assertEqual(data["plan_id"], run.plan_id)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(run.view()["subtasks"][0]["type"], "test")

    def test_subtask_failure_marks_partial(self):
        # 测试恒失败 → 修复 → 复测 → 仍失败 → 修复(第2轮) → 复测 → 仍失败 → 达上限出报告
        # （MAX_FIX_LOOP=2：fix×2、test×3，循环有界且如实交付 partial）
        self.script = {"test_run_flow": _progress("test_run_flow", exit_code=1),
                       "text_fix_flow": _progress("text_fix_flow")}
        run = asyncio.run(self.sched.run_plan(self._plan(["test"])))
        self.assertEqual(run.outcome, "partial")
        self.assertTrue(any(d["type"] == "test→fix→retest" for d in run.decisions))
        self.assertTrue(any(d["type"] == "test→report" for d in run.decisions),
                        "达上限必须以 test→report 决策收尾")
        self.assertEqual(len([c for c in FakeRunner.calls if c["tpl"] == "text_fix_flow"]), 2)
        self.assertEqual(len([c for c in FakeRunner.calls if c["tpl"] == "test_run_flow"]), 3)


if __name__ == "__main__":
    unittest.main()
