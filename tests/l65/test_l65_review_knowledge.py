# -*- coding: utf-8 -*-
"""L6.5 阶段3 单元测试：代码审查知识赋能（注入 hint / 收割反哺 / 审查发现回流 / 闸）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l65/test_l65_review_knowledge.py -q
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l65_rv_ut_"))

from knowledge import issue_base as ib   # noqa: E402

REVIEW_TEXT = """审查结论如下：

1. 高 main.py:12 订单金额分摊把四舍五入差值丢掉了，建议补差值科目
2. 中 utils/date.py:8 日期解析未校验时区，可能偏移一天
3. 低 README 过期不影响功能

2. 总体评价
整体质量尚可，两处需修复。
"""


class L65RvBase(unittest.TestCase):
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
        self.ibx = ib.IssueBase()

    def _seed_fix_history(self, pid="P"):
        for _ in range(3):
            self.ibx.record_issue({"project_id": pid, "error_type": "ModuleNotFoundError",
                                   "root_cause_category": "虚拟环境未安装依赖",
                                   "solution": "pip install x", "success": True},
                                  source=ib.SOURCE_FIX)


class HintTest(L65RvBase):
    def test_hint_contains_project_top(self):
        self._seed_fix_history()
        hint = self.ibx.review_hint("P")
        self.assertIn("历史重点检查", hint)
        self.assertIn("依赖模块未安装", hint)
        self.assertIn("pip install x", hint)

    def test_hint_empty_when_no_data(self):
        self.assertEqual(self.ibx.review_hint("Empty"), "")

    def test_hint_includes_open_review_issues(self):
        self.ibx.harvest_review("P", REVIEW_TEXT)
        hint = self.ibx.review_hint("P")
        self.assertIn("历史审查待处理", hint)
        self.assertIn("订单金额分摊", hint)

    def test_gate_off_empty(self):
        os.environ["MORAY_REVIEW_KNOWLEDGE"] = "0"
        try:
            self._seed_fix_history()
            self.assertEqual(self.ibx.review_hint("P"), "")
        finally:
            os.environ.pop("MORAY_REVIEW_KNOWLEDGE", None)


class HarvestTest(L65RvBase):
    def test_harvest_parses_items(self):
        r = self.ibx.harvest_review("P", REVIEW_TEXT)
        self.assertEqual(r["harvested"], 3, r)
        self.assertEqual(r["items"][0]["severity"], "高")
        self.assertEqual(r["items"][0]["file"], "main.py")
        self.assertEqual(r["items"][1]["severity"], "中")
        rows = self.ibx.eb._load()
        rev = [x for x in rows if x.get("source") == ib.SOURCE_REVIEW]
        self.assertEqual(len(rev), 3)
        for x in rev:
            self.assertEqual(x["error_type"], "code-review")
            self.assertFalse(x["success"], "审查发现是未修复观察，不得记成功")
            self.assertEqual(x["applicable"], "排错/审查")

    def test_harvest_section2_excluded(self):
        r = self.ibx.harvest_review("P", REVIEW_TEXT)
        descs = " ".join(x["desc"] for x in r["items"])
        self.assertNotIn("总体评价", descs)

    def test_harvest_clean_report_zero(self):
        r = self.ibx.harvest_review("P", "1. 未发现明显问题\n\n2. 总体评价\n良好")
        self.assertEqual(r["harvested"], 0)

    def test_harvest_gate_off_noop(self):
        os.environ["MORAY_UNIFIED_ISSUE"] = "0"
        try:
            r = self.ibx.harvest_review("P", REVIEW_TEXT)
            self.assertEqual(r["harvested"], 0)
            self.assertEqual(self.ibx.eb._load(), [])
        finally:
            os.environ.pop("MORAY_UNIFIED_ISSUE", None)


class BoostTest(L65RvBase):
    def test_review_finding_boosts_error_candidate(self):
        """任务书 6.2-2：审查发现的新问题 → 下次排错时优先排查。"""
        self.ibx.harvest_review("P", REVIEW_TEXT)
        cands = [{"cause": "全新无关假设"},
                 {"cause": "订单金额分摊把四舍五入差值丢掉了"}]
        boosted, note = self.ibx.boost_with_review("P", cands)
        self.assertIn("审查知识回流", note)
        self.assertEqual(boosted[0].get("review_match"), None or boosted[0].get("review_match"))
        self.assertIn("历史审查曾发现同类问题", boosted[0]["review_match"])
        self.assertEqual(boosted[0]["cause"], cands[1]["cause"], "命中候选必须前置")
        self.assertNotIn("review_match", boosted[1])

    def test_boost_no_match_untouched(self):
        self.ibx.harvest_review("P", REVIEW_TEXT)
        cands = [{"cause": "完全无关的问题"}]
        boosted, note = self.ibx.boost_with_review("P", cands)
        self.assertEqual((boosted, note), (cands, ""))

    def test_boost_gate_off(self):
        self.ibx.harvest_review("P", REVIEW_TEXT)
        os.environ["MORAY_REVIEW_KNOWLEDGE"] = "0"
        try:
            boosted, note = self.ibx.boost_with_review(
                "P", [{"cause": "订单金额分摊把四舍五入差值丢掉了"}])
            self.assertEqual(note, "")
            self.assertNotIn("review_match", boosted[0])
        finally:
            os.environ.pop("MORAY_REVIEW_KNOWLEDGE", None)


if __name__ == "__main__":
    unittest.main()
