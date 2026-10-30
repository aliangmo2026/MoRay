# -*- coding: utf-8 -*-
"""p_mcp·任务4 经验库单测：记录/查询/排序/隔离/容错/hint（全离线，临时 JSONL）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 tests/mcp_phase/test_04_experience.py"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, r"D:\MoRay")
DB = Path(tempfile.mkdtemp(prefix="mcp_exp_ut_")) / "exp.jsonl"
os.environ["MORAY_EXPERIENCE_DB"] = str(DB)

from knowledge.experience_base import ExperienceBase  # noqa: E402


class TestExperience(unittest.TestCase):
    def setUp(self):
        # [L5.1] env 每跑前置回本文件自己的临时库：同进程混跑别的会改写 MORAY_* env 的
        # 测试文件（如 tests/l51）时，不再被"末导入/末设置者赢"污染（断言语义零变化）。
        os.environ["MORAY_EXPERIENCE_DB"] = str(DB)
        os.environ["MORAY_EXPERIENCE_STATS"] = str(DB.parent / "experience_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(DB.parent / "profiles")
        if DB.exists():
            DB.unlink()
        self.eb = ExperienceBase()

    def _rec(self, proj="P1", et="MNE", cat="缺依赖", ok=True, **kw):
        base = {"project_id": proj, "error_type": et, "root_cause_category": cat,
                "error_snippet": "No module named 'x'", "solution": "pip install x",
                "duration_sec": 9, "success": ok}
        base.update(kw)
        return self.eb.record(base)

    def test_record_and_required(self):
        self.assertIsNotNone(self._rec())
        self.assertIsNone(self.eb.record({"project_id": "", "error_type": "a",
                                          "root_cause_category": "b"}))
        self.assertIsNone(self.eb.record("not a dict"))
        self.assertIsNone(self.eb.record({}))

    def test_query_score_order(self):
        self._rec(cat="A", ok=True)
        self._rec(cat="A", ok=True)           # A: 2×1.0=2
        for _ in range(4):
            self._rec(cat="B", ok=True)
        for _ in range(2):
            self._rec(cat="B", ok=False)      # B: 6×(4/6)=4
        rows = self.eb.query("P1", "MNE", top_n=5)
        by = {r["root_cause_category"]: r for r in rows}
        self.assertGreater(by["B"]["score"], by["A"]["score"])
        self.assertEqual("B", rows[0]["root_cause_category"])  # 频次×成功率

    def test_project_isolation(self):
        self._rec(proj="P1", cat="只属P1")
        self._rec(proj="P2", cat="只属P2")
        cats1 = {r["root_cause_category"] for r in self.eb.query("P1", "MNE")}
        cats2 = {r["root_cause_category"] for r in self.eb.query("P2", "MNE")}
        self.assertEqual({"只属P1"}, cats1)
        self.assertEqual({"只属P2"}, cats2)

    def test_error_type_isolation(self):
        self._rec(et="MNE")
        self._rec(et="TypeError", cat="类型错")
        self.assertEqual("类型错", self.eb.query(None, "TypeError")[0]["root_cause_category"])

    def test_build_hint(self):
        self.assertEqual("", self.eb.build_hint("NO_SUCH_PROJ", "X"))
        for _ in range(3):
            self._rec(cat="高频根因")
        hint = self.eb.build_hint("P1", "MNE")
        self.assertIn("高频根因", hint)
        self.assertIn("成功率 100%", hint)

    def test_dirty_line_tolerated(self):
        self._rec()
        with open(DB, "a", encoding="utf-8") as f:
            f.write("{坏行\n")
        self.assertEqual(1, self.eb.stats()["records"])

    def test_timestamp_default_and_snippet_cap(self):
        rec = self.eb.record({"project_id": "P", "error_type": "T",
                              "root_cause_category": "C", "solution": "x" * 3000})
        self.assertIn("timestamp", rec)
        self.assertLessEqual(len(rec["solution"]), 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
