# -*- coding: utf-8 -*-
"""L6.0 阶段5 单元测试：GET /api/self/stats 自我评估仪表盘。

独立最小 FastAPI（不 import app.main → 不跑 lifespan，monitor_routes 测试同款先例）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l60/test_l60_self_stats.py -q
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

from knowledge import strategy_optimizer as so   # noqa: E402
from knowledge.experience_base import ExperienceBase  # noqa: E402
from server.app.self_routes import router        # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l60_ss_ut_"))


class SelfStatsTest(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_CAUSE_RANK", "MORAY_EXPERIENCE_GLOBAL",
                  "MORAY_STRATEGY_OPTIMIZE", "MORAY_FIX_ADAPTIVE",
                  "MORAY_PATTERN_MINER", "MORAY_PATTERN_MINER_AUTO"):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _seed(self):
        eb = ExperienceBase()
        for _ in range(3):
            eb.record({"project_id": "Alpha", "error_type": "ModuleNotFoundError",
                       "root_cause_category": "虚拟环境未安装依赖",
                       "solution": "pip install x", "duration_sec": 10.0, "success": True})
        eb.record({"project_id": "Alpha", "error_type": "TimeoutError",
                   "root_cause_category": "外部接口超时", "solution": "加重试",
                   "duration_sec": 60.0, "success": False})
        eb.record({"project_id": "Alpha", "error_type": "TimeoutError",
                   "root_cause_category": "消息队列消费超时", "solution": "扩容消费者",
                   "duration_sec": 60.0, "success": False})
        eb.record({"project_id": "Beta", "error_type": "ZeroDivisionError",
                   "root_cause_category": "空集合均值", "solution": "判空",
                   "duration_sec": 5.0, "success": True})
        so.feedback("Alpha", "依赖模块未安装", True)
        so.feedback_fix("Alpha", so.TIER_STANDARD, True)
        so.feedback("Beta", "类型错误", False)
        so.feedback("Beta", "类型错误", False)
        so.feedback("Beta", "类型错误", False)

    def test_envelope_and_blocks(self):
        r = self.client.get("/api/self/stats")
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        d = j["data"]
        for key in ("gates", "global", "per_project", "top_errors", "hardest_errors",
                    "experience", "strategy", "fix_tiers", "patterns"):
            self.assertIn(key, d)

    def test_stats_accuracy(self):
        """任务书 6.2-5：统计准确。"""
        self._seed()
        d = self.client.get("/api/self/stats").json()["data"]
        g = d["global"]
        self.assertEqual(g["records"], 6)
        self.assertEqual(g["successes"], 4)
        self.assertEqual(g["projects"], 2)
        self.assertEqual(g["success_rate"], 0.667)
        self.assertEqual(g["avg_duration_sec"], 25.8, "(10×3+60+60+5)/6 = 155/6 ≈ 25.8")
        by_id = {p["project_id"]: p for p in d["per_project"]}
        self.assertEqual(by_id["Alpha"]["success_rate"], 0.6)
        self.assertEqual(by_id["Beta"]["success_rate"], 1.0)
        # Top 高频：依赖模块未安装 3 次
        top_keys = [c["cause_key"] for c in d["top_errors"]]
        self.assertIn("依赖模块未安装", top_keys)
        # 最难：聚合键 times≥2 才进池；超时类归一化为"操作超时"（2 次全败 → 成功率 0）
        hard = {c["cause_key"]: c for c in d["hardest_errors"]}
        self.assertIn("操作超时", hard)
        self.assertEqual(hard["操作超时"]["success_rate"], 0.0)
        self.assertEqual(hard["操作超时"]["times"], 2)
        # 经验库规模
        ex = d["experience"]
        self.assertEqual(ex["records"], 6)
        self.assertEqual(ex["projects"], 2)

    def test_evolution_view(self):
        self._seed()
        d = self.client.get("/api/self/stats").json()["data"]
        st = d["strategy"]
        self.assertEqual(st["tracked_causes"], 2)
        self.assertEqual(len(st["skipped_causes"]), 1)
        self.assertEqual(st["skipped_causes"][0]["cause_key"], "类型错误")
        self.assertEqual(st["skipped_causes"][0]["consec_fail"], 3)
        tiers = {t["tier"]: t for t in d["fix_tiers"]}
        self.assertIn(so.TIER_STANDARD, tiers)
        self.assertEqual(tiers[so.TIER_STANDARD]["tries"], 1)
        self.assertEqual(tiers[so.TIER_STANDARD]["success_rate"], 1.0)
        pa = d["patterns"]
        self.assertEqual(pa["total"], 0)

    def test_gates_reflect_env(self):
        os.environ["MORAY_FIX_ADAPTIVE"] = "0"
        try:
            d = self.client.get("/api/self/stats").json()["data"]
            self.assertTrue(d["gates"]["strategy_optimize"], "默认开")
            self.assertFalse(d["gates"]["fix_adaptive"], "显式关")
        finally:
            os.environ.pop("MORAY_FIX_ADAPTIVE", None)

    def test_empty_db_no_500(self):
        r = self.client.get("/api/self/stats")
        self.assertEqual(r.status_code, 200)
        d = r.json()["data"]
        self.assertEqual(d["global"]["records"], 0)
        self.assertEqual(d["global"]["success_rate"], 0.0)
        self.assertIsNone(d["global"]["avg_duration_sec"])


if __name__ == "__main__":
    unittest.main()
