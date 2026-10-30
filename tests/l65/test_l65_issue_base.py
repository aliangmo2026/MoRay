# -*- coding: utf-8 -*-
"""L6.5 阶段2 单元测试：统一问题库（同一 JSONL 零迁移 / 增量键 / 兼容层不变 / 闸）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l65/test_l65_issue_base.py -q
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l65_ib_ut_"))

from knowledge import issue_base as ib                     # noqa: E402
from knowledge.experience_base import ExperienceBase       # noqa: E402


class L65Base(unittest.TestCase):
    def setUp(self):
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
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()
        self.ib = ib.IssueBase()
        self.eb = self.ib.eb


class UnifiedRecordTest(L65Base):
    def test_same_jsonl_additive_keys(self):
        """统一 schema = 同一 JSONL + 增量键；历史数据 100% 保留。"""
        self.eb.record({"project_id": "P", "error_type": "ZeroDivisionError",
                        "root_cause_category": "空集合均值", "success": True})
        rec = self.ib.record_issue({"project_id": "P", "error_type": "ZeroDivisionError",
                                    "root_cause_category": "除零回归", "success": False},
                                   source=ib.SOURCE_REVIEW, issue_kind="高",
                                   applicable="排错/审查", file_type=".py")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["source"], "代码审查")
        self.assertEqual(rec["issue_kind"], "高")
        self.assertEqual(rec["applicable"], "排错/审查")
        self.assertEqual(rec["file_type"], ".py")
        rows = [json.loads(l) for l in (TMP / "exp.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows), 2, "两条记录必须同一文件（零迁移）")
        self.assertNotIn("source", rows[0], "旧记录不含新键（历史原样）")
        self.assertIn("source", rows[1])

    def test_old_rows_still_cluster_with_new(self):
        """新旧记录同池：top_meta 按 cause_key 聚合（query 按原文分组是 L5.0 既有契约，不动）。"""
        for _ in range(3):
            self.eb.record({"project_id": "P", "error_type": "ModuleNotFoundError",
                            "root_cause_category": "虚拟环境未安装依赖",
                            "solution": "pip install x", "success": True})
        self.ib.record_issue({"project_id": "P", "error_type": "ModuleNotFoundError",
                              "root_cause_category": "又缺依赖", "success": True},
                             source=ib.SOURCE_TEST)
        top = self.eb.top_meta("P", "ModuleNotFoundError")
        self.assertEqual(top[0]["times"], 4, "新旧记录必须聚合到同一根因键")
        self.assertEqual(top[0]["success_rate"], 1.0)

    def test_gate_off_no_keys_pure_passthrough(self):
        os.environ["MORAY_UNIFIED_ISSUE"] = "0"
        try:
            rec = self.ib.record_issue({"project_id": "P", "error_type": "TypeError",
                                        "root_cause_category": "类型错误", "success": True},
                                       source=ib.SOURCE_REVIEW)
            self.assertIsNotNone(rec)
            self.assertNotIn("source", rec)
            self.assertNotIn("applicable", rec)
        finally:
            os.environ.pop("MORAY_UNIFIED_ISSUE", None)

    def test_bad_entry_rejected(self):
        self.assertIsNone(self.ib.record_issue({"project_id": "P"}, source=ib.SOURCE_FIX))
        self.assertIsNone(self.ib.record_issue("not-a-dict"))


class CompatLayerTest(L65Base):
    def test_experience_base_apis_untouched(self):
        """兼容层：旧接口行为零变化（含 L6.0 优化器对新行自动生效）。"""
        for _ in range(5):
            self.ib.record_issue({"project_id": "P", "error_type": "ModuleNotFoundError",
                                  "root_cause_category": "虚拟环境未安装依赖",
                                  "solution": "pip install x", "success": True},
                                 source=ib.SOURCE_FIX)
        hint = self.eb.build_hint("P", "ModuleNotFoundError")
        self.assertIn("依赖模块未安装", hint)
        ranked, note = self.eb.rank_candidates("P", [
            {"cause": "缺依赖问题"}, {"cause": "全新假设"}])
        self.assertTrue(any(t.get("experience_match") for t in ranked), note)
        prof = self.eb.read_profile("P")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["records"], 5)


if __name__ == "__main__":
    unittest.main()
