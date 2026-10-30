# -*- coding: utf-8 -*-
"""L7.0 /api/autonomous/* 路由单测：总闸默认关（409）/受理轮询/重启磁盘恢复/信封。

独立最小 FastAPI（不 import app.main → 不跑 lifespan，monitor_routes 测试同款先例）。
后台协程用桩替换 decompose 产出的调度器 → 全离线。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l70/test_l70_routes.py -q
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

from server.app import planner_routes             # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l70_rt_ut_"))


class StubRun:
    def __init__(self, plan_id):
        self.plan_id = plan_id
        self.status = "completed"
        self.t0 = time.time()
        self.ended_at = time.time() + 0.1

    def view(self):
        return {"plan_id": self.plan_id, "status": "completed", "outcome": "all_ok",
                "subtasks": [{"type": "review", "status": "ok"}],
                "decisions": [], "report": "最终报告R", "error": "",
                "duration_sec": 0.1, "work_dir": ""}


class RoutesBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        for k in ("MORAY_TASK_PLANNER",):
            os.environ.pop(k, None)
        planner_routes._RUNS.clear()
        planner_routes._ORDER.clear()
        self.work = TMP / ("auto_%s" % id(self))
        self.work.mkdir(parents=True, exist_ok=True)
        self._orig_work = planner_routes._work_root
        planner_routes._work_root = lambda: self.work
        app = FastAPI()
        app.include_router(planner_routes.router)
        self.client = TestClient(app)

    def tearDown(self):
        planner_routes._work_root = self._orig_work
        os.environ.pop("MORAY_TASK_PLANNER", None)
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)


class GateOffTest(RoutesBase):
    def test_default_off_409(self):
        """任务书 6.2-5：总闸默认关 → 受理 409，status 如实报 disabled。"""
        r = self.client.post("/api/autonomous/run", json={"goal": "优化一下这个项目"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "planner_disabled")
        s = self.client.get("/api/autonomous/status")
        self.assertTrue(s.json()["data"]["enabled"] is False)

    def test_bad_goal_ignored_when_off(self):
        r = self.client.post("/api/autonomous/run", json={"goal": ""})
        self.assertEqual(r.status_code, 409, "闸关时入参校验都不必做")


class GateOnTest(RoutesBase):
    def setUp(self):
        super().setUp()
        os.environ["MORAY_TASK_PLANNER"] = "1"

    def test_status_enabled(self):
        d = self.client.get("/api/autonomous/status").json()["data"]
        self.assertTrue(d["enabled"])

    def test_run_bad_request(self):
        self.assertEqual(self.client.post("/api/autonomous/run", json={"goal": ""}).status_code, 400)
        self.assertEqual(self.client.post("/api/autonomous/run",
                                          json={"goal": "x", "project_dir": "Z:\\no_dir"}).status_code,
                         400)

    @mock.patch("engine.execution_scheduler.ExecutionScheduler")
    @mock.patch("engine.task_planner.decompose")
    def test_accept_and_poll(self, stub_decompose, stub_sched):
        """受理 202 → 后台规划（桩）+执行（桩） → 轮询拿到 completed 报告。"""
        plan = {"plan_id": "x", "goal": "g", "source": "rule",
                "context": {"project_id": "P", "project_dir": str(TMP)},
                "tasks": [{"id": "t1", "type": "review", "title": "审查"}]}

        async def fake_decompose(goal, pid, pdir, llm_call=None, plan_id=None):
            plan["plan_id"] = plan_id or "x"
            return plan
        stub_decompose.side_effect = fake_decompose
        stub_sched.return_value.run_plan = mock.AsyncMock(return_value=StubRun("x"))
        r = self.client.post("/api/autonomous/run",
                             json={"goal": "优化一下这个项目", "project_id": "P"})
        self.assertEqual(r.status_code, 202)
        plan_id = r.json()["data"]["plan_id"]
        view = None
        for _ in range(120):                    # 后台协程在 portal loop 上完成
            j = self.client.get("/api/autonomous/" + plan_id).json()
            if j["data"].get("report"):
                view = j["data"]
                break
            time.sleep(0.05)
        self.assertIsNotNone(view, "6 秒内必须完成（桩规划+桩调度）")
        self.assertEqual(view["report"], "最终报告R")
        self.assertEqual(view["status"], "completed")

    def test_unknown_plan_404(self):
        r = self.client.get("/api/autonomous/deadbeef00")
        self.assertEqual(r.status_code, 404)

    def test_restart_disk_recovery(self):
        """后端重启（内存表空）→ work/autonomous/{id}/run.json 回读。"""
        d = self.work / "diskcase"
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps(
            {"plan_id": "diskcase", "status": "completed", "report": "盘上快照",
             "subtasks": [], "decisions": []}, ensure_ascii=False), encoding="utf-8")
        r = self.client.get("/api/autonomous/diskcase")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["report"], "盘上快照")


if __name__ == "__main__":
    unittest.main()
