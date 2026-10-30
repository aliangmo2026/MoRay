# -*- coding: utf-8 -*-
"""L6.0 阶段4 单元测试：修复策略自适应（档位偏好/风险感知/回退加速/反馈/闸门）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l60/test_l60_fix_adaptive.py -q
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l60_fx_ut_"))

from knowledge import strategy_optimizer as so   # noqa: E402


class L60FxBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_FIX_ADAPTIVE",
                  "MORAY_STRATEGY_OPTIMIZE", "MORAY_PATTERN_MINER"):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()

    def _fx(self, pid="P1", tier=so.TIER_FAST, success=True):
        so.feedback_fix(pid, tier, success)


class TierPickTest(L60FxBase):
    def test_no_data_default_chain(self):
        self.assertIsNone(so.pick_fix_tier("P1", "utils/util.py"),
                          "无统计数据 = 默认链（先真实引擎，模板 fallback 不动）")

    def test_fast_tier_preference(self):
        """任务书 6.2-4：不同项目 → 档位偏好不同。"""
        for _ in range(3):
            self._fx("PySmall", so.TIER_FAST, True)        # Python 小脚本：快速档 3/3
        self._fx("PySmall", so.TIER_STANDARD, True)        # 规范档 1/2（连败不达 2，走偏好分支）
        self._fx("PySmall", so.TIER_STANDARD, False)
        for _ in range(2):
            self._fx("ProdApp", so.TIER_STANDARD, True)    # 生产项目：规范档 2/2
        pick = so.pick_fix_tier("PySmall", "tools/render.py")
        self.assertIsNotNone(pick)
        self.assertEqual(pick["tier"], so.TIER_FAST)
        self.assertIn("档位偏好", pick["reason"])
        self.assertIsNone(so.pick_fix_tier("ProdApp", "tools/render.py"),
                          "规范档占优的项目保持默认链（先真实引擎）")

    def test_risk_awareness_core_file(self):
        """核心文件（入口/配置）恒规范档，即使快速档历史更优。"""
        for _ in range(3):
            self._fx("P1", so.TIER_FAST, True)
            self._fx("P1", so.TIER_STANDARD, False)
        pick = so.pick_fix_tier("P1", "main.py")
        self.assertIsNotNone(pick)
        self.assertEqual(pick["tier"], so.TIER_STANDARD)
        self.assertIn("风险感知", pick["reason"])
        self.assertIn("核心文件", pick["reason"])

    def test_core_file_without_history_untouched(self):
        """核心文件 + 无任何档位历史 → None（默认链，不多话）。"""
        self.assertIsNone(so.pick_fix_tier("P1", "main.py"))

    def test_fallback_acceleration(self):
        """回退加速：规范档连续 2 次失败 → 跳过直走快速档。"""
        self._fx("P1", so.TIER_STANDARD, False)
        self._fx("P1", so.TIER_STANDARD, False)
        pick = so.pick_fix_tier("P1", "utils/x.py")
        self.assertIsNotNone(pick)
        self.assertEqual(pick["tier"], so.TIER_FAST)
        self.assertIn("回退加速", pick["reason"])

    def test_fast_tier_consec_fail_stops_preference(self):
        """快速档连续 2 次失败 → 不再偏好快速档（回默认链）。"""
        for _ in range(3):
            self._fx("P1", so.TIER_FAST, True)
        self._fx("P1", so.TIER_FAST, False)
        self._fx("P1", so.TIER_FAST, False)
        self.assertIsNone(so.pick_fix_tier("P1", "utils/x.py"))


class TierFeedbackTest(L60FxBase):
    def test_engine_normalization(self):
        self.assertEqual(so.tier_of_engine("real:zcode"), so.TIER_STANDARD)
        self.assertEqual(so.tier_of_engine("fallback:llm_patch(zcode_down)"), so.TIER_FAST)
        self.assertEqual(so.tier_of_engine("fallback:llm_patch(strategy_fast)"), so.TIER_FAST)
        self.assertIsNone(so.tier_of_engine("real:docker"))
        self.assertIsNone(so.tier_of_engine("mcp"))

    def test_feedback_accumulates(self):
        self._fx("P1", so.TIER_FAST, True)
        self._fx("P1", so.TIER_FAST, False)
        b = so.load_stats()["fix_tiers"]["P1"][so.TIER_FAST]
        self.assertEqual(b["tries"], 2)
        self.assertEqual(b["wins"], 1)
        self.assertEqual(b["consec_fail"], 1)

    def test_feedback_ignores_unknown_tier(self):
        so.feedback_fix("P1", "real:docker", True)
        self.assertEqual(so.load_stats()["fix_tiers"], {})


class GateTest(L60FxBase):
    def test_gate_off_none(self):
        os.environ["MORAY_FIX_ADAPTIVE"] = "0"
        try:
            for _ in range(3):
                self._fx("P1", so.TIER_FAST, True)
            self.assertIsNone(so.pick_fix_tier("P1", "utils/x.py"), "关闸 = 默认链")
            so.feedback_fix("P1", so.TIER_FAST, True)   # 关闸时反馈也不落
            self.assertEqual(so.load_stats()["fix_tiers"], {})
        finally:
            os.environ.pop("MORAY_FIX_ADAPTIVE", None)


if __name__ == "__main__":
    unittest.main()
