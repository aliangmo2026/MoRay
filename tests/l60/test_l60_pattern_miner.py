# -*- coding: utf-8 -*-
"""L6.0 阶段3 单元测试：错误模式自动发现（聚类/候选规则/衰减/自动生效叠加/闸门/懒触发）。

全离线。运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l60/test_l60_pattern_miner.py -q
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l60_pm_ut_"))

from knowledge import pattern_miner as pm                   # noqa: E402
from knowledge.experience_base import ExperienceBase, normalize_cause  # noqa: E402

# 未分类业务错误（error_type 无异常类名、类别文本过不了关键词规则）×3 种近似措辞
BIZ = [
    ("USR_1001", "订单金额分摊逻辑把四舍五入差值丢掉了"),
    ("USR_1001", "订单金额分摊时四舍五入差值被丢弃"),
    ("USR_1001", "订单金额的分摊逻辑丢了四舍五入差值"),
]


class L60PmBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_PATTERN_MINER",
                  "MORAY_PATTERN_MINER_AUTO", "MORAY_STRATEGY_OPTIMIZE"):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()
        self.eb = ExperienceBase()

    def _biz(self, pid="P", i=0, ok=True):
        et, cat = BIZ[i % len(BIZ)]
        return self.eb.record({"project_id": pid, "error_type": et,
                               "root_cause_category": cat, "solution": "补差值科目",
                               "success": ok})


class MineTest(L60PmBase):
    def test_unclassified_detected(self):
        rows = self.eb._load()
        self.assertEqual(len(pm._unclassified_rows(rows)), 0, "无记录=无未分类")
        self._biz(i=0)
        rows = self.eb._load()
        unc = pm._unclassified_rows(rows)
        self.assertEqual(len(unc), 1, "业务自由文本必须走兜底分支")

    def test_three_similar_generate_candidate(self):
        """任务书 6.2-3：新错误类型出现 3 次 → 自动生成候选模式（默认不生效）。"""
        for i in range(3):
            self._biz(i=i)
        self._biz(i=0, pid="Q")   # 同措辞放另一项目：聚类按全局文本，不受项目隔离影响
        r = pm.mine(force=True)
        self.assertTrue(r["mined"])
        data = pm.load_candidates()
        self.assertEqual(len(data["candidates"]), 1, r)
        c = data["candidates"][0]
        self.assertEqual(c["status"], "candidate", "默认只建议不生效（红线/任务书 3.3）")
        self.assertFalse(c["auto"])
        self.assertGreaterEqual(c["times"], 3)
        self.assertTrue(c["tokens"], "必须提炼出共享关键词")

    def test_two_similar_not_enough(self):
        for i in range(2):
            self._biz(i=i)
        r = pm.mine(force=True)
        self.assertEqual(len(pm.load_candidates()["candidates"]), 0,
                         "MIN_CLUSTER=3：两条不生成候选")

    def test_classified_rows_never_mined(self):
        """命中人工规则的行不进聚类（避免把已分类错误再挖一遍）。"""
        for _ in range(3):
            self.eb.record({"project_id": "P", "error_type": "ModuleNotFoundError",
                            "root_cause_category": "虚拟环境未安装依赖",
                            "solution": "pip install", "success": True})
        r = pm.mine(force=True)
        self.assertEqual(r["unclassified"], 0)
        self.assertEqual(len(pm.load_candidates()["candidates"]), 0)


class OverlayTest(L60PmBase):
    def test_promote_makes_rule_active_and_normalize_uses_it(self):
        self._biz(i=0)
        self._biz(i=1)
        self._biz(i=2)
        pm.mine(force=True)
        data = pm.load_candidates()
        pattern = data["candidates"][0]["pattern"]
        skey = data["candidates"][0]["suggested_key"]
        # candidate 状态不得参与分类：相似措辞 BIZ[1] 兜底返回"自己的原文"，而非统一键
        self.assertNotEqual(normalize_cause({"error_type": "USR_1001",
                                             "root_cause_category": BIZ[1][1]}), skey,
                            "candidate 生效前相似措辞必须各自兜底、不统一")
        self.assertTrue(pm.promote(pattern))
        key = normalize_cause({"error_type": "USR_1001",
                               "root_cause_category": BIZ[1][1]})
        self.assertEqual(key, skey, "promote 后叠加规则必须把相似措辞统一到建议键")

    def test_auto_apply_flag(self):
        os.environ["MORAY_PATTERN_MINER_AUTO"] = "1"
        try:
            for i in range(3):
                self._biz(i=i)
            pm.mine(force=True)
            data = pm.load_candidates()
            self.assertEqual(data["candidates"][0]["status"], "active")
            key = normalize_cause({"error_type": "USR_1001",
                                   "root_cause_category": BIZ[1][1]})
            self.assertEqual(key, data["candidates"][0]["suggested_key"])
        finally:
            os.environ.pop("MORAY_PATTERN_MINER_AUTO", None)

    def test_miner_gate_off_no_overlay(self):
        os.environ["MORAY_PATTERN_MINER"] = "0"
        try:
            for i in range(3):
                self._biz(i=i)
            pm.mine(force=True)
            self.assertEqual(pm.runtime_rules(), [], "关闸 = 叠加表恒空（只用人工规则）")
            self.assertEqual(pm.maybe_mine()["reason"], "gate_off")
        finally:
            os.environ.pop("MORAY_PATTERN_MINER", None)

    def test_decayed_rule_exits_overlay(self):
        """模式衰减：30 天未出现 → decayed，退出运行时叠加表。"""
        old = (datetime.now(timezone.utc).astimezone() - timedelta(days=40)) \
            .isoformat(timespec="seconds")
        for i in range(3):
            self.eb.record({"project_id": "P", "error_type": "USR_1001",
                            "root_cause_category": BIZ[i][1], "solution": "s",
                            "success": True, "timestamp": old})
        pm.mine(force=True)
        data = pm.load_candidates()
        self.assertEqual(data["candidates"][0]["status"], "decayed", "40 天未出现必须衰减")
        self.assertEqual(pm.runtime_rules(), [], "decayed 不得进叠加表")

    def test_lazy_trigger_threshold(self):
        """maybe_mine 懒触发：新增 <20 条且 <7 天 → 不真跑。"""
        for i in range(3):
            self._biz(i=i)
        r = pm.mine(force=False)
        self.assertFalse(r["mined"])
        self.assertIn("阈值", r["reason"], "冷启动/增量两种未达阈值文案都要覆盖")
        r2 = pm.mine(force=True)
        self.assertTrue(r2["mined"])
        r3 = pm.mine(force=False)   # 刚挖过：阈值重新计
        self.assertFalse(r3["mined"])

    def test_record_hook_runs_miner(self):
        """ExperienceBase.record() 后的懒触发钩子不破坏记录主链路（返回值/形状不变）。"""
        rec = self._biz(i=0)
        self.assertIsNotNone(rec)
        self.assertIn("cause_key", rec)
        self.assertIn("language", rec)


if __name__ == "__main__":
    unittest.main()
