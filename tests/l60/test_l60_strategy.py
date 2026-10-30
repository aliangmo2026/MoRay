# -*- coding: utf-8 -*-
"""L6.0 阶段2 单元测试：诊断策略自适应（权重/时间衰减/连败跳过/深度自适应/反馈闭环/闸门）。

全离线：临时目录隔离（JSONL / 元数据 / strategy_stats / profiles）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l60/test_l60_strategy.py -q
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l60_st_ut_"))

from knowledge import strategy_optimizer as so                      # noqa: E402
from knowledge.experience_base import ExperienceBase                # noqa: E402

KEY_DEP = "依赖模块未安装"
KEY_CFG = "配置文件缺少键"


class L60Base(unittest.TestCase):
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
        self.eb = ExperienceBase()

    def _rec(self, pid="P", et="ModuleNotFoundError", cat="虚拟环境未安装依赖",
             ok=True, ts=None, **kw):
        e = {"project_id": pid, "error_type": et, "root_cause_category": cat,
             "solution": "pip install foo", "success": ok}
        if ts:
            e["timestamp"] = ts
        e.update(kw)
        return self.eb.record(e)


class WeightTest(L60Base):
    def test_five_successes_raise_weight_and_order(self):
        """任务书 6.2-1：连续 5 次同类成功 → 权重提升、排序靠前。"""
        for _ in range(5):
            self._rec(ok=True)
        self._rec(et="KeyError", cat=KEY_CFG, ok=True)   # 对照：1 次成功
        w_dep = so.weight("P", KEY_DEP)
        w_cfg = so.weight("P", KEY_CFG)
        self.assertGreater(w_dep, w_cfg > 0 and 0 or 0, "5 次成功根因权重必须为正")
        self.assertGreater(w_dep, w_cfg)
        ranked, note = so.rank_with_strategy("P", [
            {"cause": KEY_CFG, "experience_match": {"cause_key": KEY_CFG}},
            {"cause": "依赖模块未安装", "experience_match": {"cause_key": KEY_DEP}},
        ])
        self.assertIn(KEY_DEP, ranked[0]["experience_match"]["cause_key"],
                      "权重重排后高频高成功必须第一")
        self.assertIn("策略重排", note)

    def test_recency_decay_flips_order(self):
        """时间衰减：老的高频根因（30 天前）权重低于近期低频根因——L6 与纯频次排序的区分点。"""
        old = (datetime.now(timezone.utc).astimezone() - timedelta(days=30)) \
            .isoformat(timespec="seconds")
        for _ in range(10):
            self._rec(cat=KEY_CFG, ok=True, ts=old)
        for _ in range(2):
            self._rec(ok=True)   # 依赖：近期 2 次成功
        w_dep = so.weight("P", KEY_DEP)
        w_cfg = so.weight("P", KEY_CFG)
        self.assertGreater(w_dep, w_cfg, "近期根因必须越过 30 天前的老根因（半衰期 14 天）")
        ranked, _ = so.rank_with_strategy("P", [
            {"cause": KEY_CFG, "experience_match": {"cause_key": KEY_CFG}},
            {"cause": KEY_DEP, "experience_match": {"cause_key": KEY_DEP}},
        ])
        self.assertEqual(ranked[0]["experience_match"]["cause_key"], KEY_DEP)

    def test_zero_data_no_reorder(self):
        ranked, note = so.rank_with_strategy("P", [{"cause": "a"}, {"cause": "b"}])
        self.assertEqual(ranked, [{"cause": "a"}, {"cause": "b"}])
        self.assertEqual(note, "", "无历史数据必须原序返回且无说明")


class SkipTest(L60Base):
    def test_three_consecutive_fails_skipped_last(self):
        """任务书 6.2-2：连续 3 次同类失败 → 自动跳过（置末 + 标注）。"""
        self._rec(ok=False)
        so.feedback("P", KEY_DEP, False)
        so.feedback("P", KEY_DEP, False)
        so.feedback("P", KEY_DEP, False)
        cands = [{"cause": KEY_DEP, "experience_match": {"cause_key": KEY_DEP}},
                 {"cause": "fresh"}]
        ranked, note = so.rank_with_strategy("P", cands)
        self.assertEqual(ranked[-1].get("strategy_action"), "skip")
        self.assertIn("跳过", ranked[-1]["strategy_note"])
        self.assertIn("跳过连败根因 1 项", note)
        self.assertEqual(ranked[0], cands[1], "正常候选必须前移到第一位")

    def test_two_fails_not_skipped(self):
        self._rec(ok=False)
        so.feedback("P", KEY_DEP, False)
        so.feedback("P", KEY_DEP, False)
        ranked, _ = so.rank_with_strategy("P", [
            {"cause": KEY_DEP, "experience_match": {"cause_key": KEY_DEP}}])
        self.assertNotIn("strategy_action", ranked[0])
        self.assertFalse(so.load_stats()["projects"].get("P", {}).get("causes", {})
                         .get(KEY_DEP, {}).get("consec_fail", 0) >= so.SKIP_CONSEC_FAIL)

    def test_success_resets_consecutive_fail(self):
        so.feedback("P", KEY_DEP, False)
        so.feedback("P", KEY_DEP, False)
        so.feedback("P", KEY_DEP, True)
        so.feedback("P", KEY_DEP, False)
        st = so.load_stats()["projects"]["P"]["causes"][KEY_DEP]
        self.assertEqual(st["consec_fail"], 1)
        self.assertEqual(st["wins"], 1)
        self.assertEqual(st["tries"], 4)


class DepthTest(L60Base):
    def test_simple_error_narrows_top_n(self):
        self.assertEqual(so.hint_top_n("SyntaxError", "", 3), 2)
        self.assertEqual(so.hint_top_n("IndentationError", "", 3), 2)
        self.assertEqual(so.hint_top_n("ZeroDivisionError", "", 5), 2)

    def test_complex_error_widens_top_n(self):
        self.assertEqual(so.hint_top_n("ModuleNotFoundError", "", 3), 5)
        self.assertEqual(so.hint_top_n("TimeoutError", "", 3), 5)
        self.assertEqual(so.hint_top_n("", "配置文件缺失导致连接失败", 3), 5)

    def test_normal_keeps_base(self):
        self.assertEqual(so.hint_top_n("MNE", "业务描述", 3), 3)

    def test_classify_complexity(self):
        self.assertEqual(so.classify_complexity("SyntaxError"), "simple")
        self.assertEqual(so.classify_complexity("ModuleNotFoundError"), "complex")
        self.assertEqual(so.classify_complexity("MNE", "随便什么业务"), "normal")


class GateTest(L60Base):
    def test_gate_off_returns_asis(self):
        os.environ["MORAY_STRATEGY_OPTIMIZE"] = "0"
        try:
            self._rec(ok=True)
            so.feedback("P", KEY_DEP, False)
            so.feedback("P", KEY_DEP, False)
            so.feedback("P", KEY_DEP, False)
            cands = [{"cause": KEY_DEP, "experience_match": {"cause_key": KEY_DEP}}]
            ranked, note = so.rank_with_strategy("P", cands)
            self.assertEqual((ranked, note), (cands, ""), "关闸 = 原序原样")
            self.assertFalse(so.feedback("P", "x", True) or False)
            self.assertIsNone(so.pick_fix_tier("P", "main.py"), "修复档位独立于诊断闸，但都关=默认链")
        finally:
            os.environ.pop("MORAY_STRATEGY_OPTIMIZE", None)

    def test_gate_env_accepts_false_variants(self):
        for v in ("0", "false", "off", "no"):
            os.environ["MORAY_STRATEGY_OPTIMIZE"] = v
            self.assertFalse(so.strategy_on(), v)
        os.environ.pop("MORAY_STRATEGY_OPTIMIZE", None)
        self.assertTrue(so.strategy_on(), "默认开")


if __name__ == "__main__":
    unittest.main()
