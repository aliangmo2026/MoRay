# -*- coding: utf-8 -*-
"""L5.3 跨项目经验迁移单测（任务书 4.1）：scope 自动判定 / global 聚合 / 新项目冷启动 /
跨项目推荐来源标注（项目优先、通用补位）/ 独立闸关 = 回 L5.1 行为。

全离线：临时 JSONL + 临时元数据 + 临时画像目录。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l5253/test_l5253_experience_xfer.py -q
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l5253_xfer_ut_"))

from knowledge.experience_base import (ExperienceBase,  # noqa: E402
                                       classify_scope, normalize_cause)


class XferBase(unittest.TestCase):
    def setUp(self):
        d = TMP / ("case_%s" % self.id().split(".")[-1])
        d.mkdir(parents=True, exist_ok=True)
        os.environ["MORAY_EXPERIENCE_DB"] = str(d / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(d / "experience_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(d / "profiles")
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)
        os.environ.pop("MORAY_EXPERIENCE_GLOBAL", None)
        self.d = d
        self.eb = ExperienceBase()

    def _lang(self, pid="OLD", ok=True, n=1, **kw):
        """语言级（除零）经验——应进 global 层。"""
        rows = []
        for _ in range(n):
            base = {"project_id": pid, "error_type": "ZeroDivisionError",
                    "root_cause_category": "空列表传入导致除零",
                    "error_snippet": "ZeroDivisionError: division by zero",
                    "solution": "除法前判空", "duration_sec": 11.0, "success": ok}
            base.update(kw)
            rows.append(self.eb.record(base))
        return rows

    def _biz(self, pid="OLD", ok=True, **kw):
        base = {"project_id": pid, "error_type": "BizRuleFail",
                "root_cause_category": "购物车四舍五入规则与财务口径不一致",
                "solution": "统一 Decimal 量化", "duration_sec": 20.0, "success": ok}
        base.update(kw)
        return self.eb.record(base)


class ScopeTest(XferBase):
    def test_language_level_records_marked_global(self):
        rec = self._lang()[0]
        self.assertEqual(rec["scope"], "global")
        self.assertIn("除零", rec["cause_key"])

    def test_business_records_marked_project(self):
        rec = self._biz()
        self.assertEqual(rec["scope"], "project")

    def test_explicit_scope_respected(self):
        rec = self.eb.record({"project_id": "P", "error_type": "TypeError",
                              "root_cause_category": "参数类型错", "scope": "project"})
        self.assertEqual(rec["scope"], "project", "显式合法值优先于自动判定")

    def test_classify_scope_examples(self):
        self.assertEqual(classify_scope({"error_type": "IndentationError",
                                         "root_cause_category": "块缩进混乱"}), "global")
        self.assertEqual(classify_scope({"error_type": "ModuleNotFoundError",
                                         "root_cause_category": "venv 缺包"}), "global")
        self.assertEqual(classify_scope({"error_type": "BizX",
                                         "root_cause_category": "会员折扣与税率口径冲突"}), "project")


class GlobalBucketTest(XferBase):
    def test_global_aggregates_cross_project(self):
        self._lang(pid="A", n=2)
        self._lang(pid="B", n=1, error_snippet="division by zero")
        g = self.eb.top_global(top_n=5)
        self.assertTrue(g)
        self.assertEqual(g[0]["times"], 3, "跨项目计数聚合")
        self.assertEqual(g[0]["scope"], "global")
        st = self.eb.stats()
        self.assertEqual(st["evolution"]["global_causes"], 1)
        self.assertEqual(st["evolution"]["global_records"], 3)

    def test_business_experience_never_enters_global(self):
        self._biz(pid="A")
        self._biz(pid="B")
        self.assertEqual(self.eb.top_global(top_n=5), [])

    def test_language_filter(self):
        self._lang(pid="A")   # python 特征（ZeroDivisionError）
        self.assertEqual(len(self.eb.top_global(language="python")), 1)
        self.assertEqual(self.eb.top_global(language="java"), [])

    def test_undiscovered_global_zero_success_not_recommended(self):
        """全局层也要成功率>0 才有推荐价值：score>0 过滤。"""
        self._lang(pid="A", ok=False, n=2)
        hint = self.eb.build_hint("FRESH", "ZeroDivisionError", 3)
        self.assertEqual(hint, "", "0 成功的全局根因不进优先假设")


class ColdStartTest(XferBase):
    def test_new_project_hint_carries_generic_with_tag(self):
        """任务书 3.3：新项目第一次报错 → 自动带同语言通用根因（标注来源）。"""
        self._lang(pid="MATURE", n=3)
        hint = self.eb.build_hint("BRAND_NEW", "ZeroDivisionError", 3)
        self.assertIn("通用经验", hint)
        self.assertIn("除零", hint)
        self.assertNotIn("历史 3 次", hint)     # 通用行走"跨项目 N 次"措辞，不与本项目混淆
        self.assertIn("跨项目 3 次", hint)

    def test_project_experience_first_generic_backfills(self):
        """任务书 3.4：先查项目特定、再查通用——项目根因排前，通用只补位。"""
        self._biz(pid="P", ok=True)
        self._lang(pid="OTHER", n=2)
        hint = self.eb.build_hint("P", "BizRuleFail", 3)
        lines = hint.splitlines()
        self.assertTrue(lines[0].startswith("1. "), lines)
        self.assertIn("四舍五入", lines[0])
        self.assertNotIn("通用经验", lines[0], "项目层行不带通用标注")
        self.assertTrue(any("通用经验" in ln for ln in lines[1:]), "不足 top_n 用通用补位")

    def test_mature_project_not_polluted_when_full(self):
        """项目层已有足量高置信根因时不掺通用行（避免稀释本项目信号）。"""
        self._lang(pid="P", n=5)
        self._biz(pid="Q", ok=True)
        hint = self.eb.build_hint("P", "ZeroDivisionError", 1)
        self.assertNotIn("通用经验", hint)

    def test_meta_version_bump_self_rebuilds_legacy(self):
        """l51-1 旧元数据 → 版本失配自动全量重建为 l52-1（既有自愈通道），
        且重建时旧行（无 scope 键）按规则现算并入 global。"""
        self._lang(pid="A", n=2)
        meta_f = self.d / "experience_stats.json"
        meta = json.loads(meta_f.read_text(encoding="utf-8"))
        meta["version"] = "l51-1"                      # 伪装 L5.1 旧档
        meta.pop("global", None)
        meta_f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        g = self.eb.top_global(top_n=5)                # ensure_meta → 重建
        self.assertTrue(g and g[0]["times"] == 2)
        fresh = json.loads(meta_f.read_text(encoding="utf-8"))
        self.assertNotEqual(fresh["version"], "l51-1")


class RankCrossProjectTest(XferBase):
    def test_project_hit_wins_over_generic(self):
        """同一候选既在项目和全局 → 按项目层判定（来源=历史经验（本项目））。"""
        self._lang(pid="P", n=5)          # 项目高频成功 → 优先验证档
        cands = [{"cause": "空列表 len 为 0 触发 ZeroDivisionError", "source": "llm"}]
        ranked, note = self.eb.rank_candidates("P", cands)
        em = ranked[0]["experience_match"]
        self.assertEqual(em["source"], "历史经验（本项目）")
        self.assertTrue(em["action"].startswith("优先验证"))
        self.assertIn("候选命中项目历史", note)

    def test_cold_project_uses_generic_reference(self):
        """新项目（无项目桶）也能用全局层：参考档 + 来源标注，不给优先/置末强结论。"""
        self._lang(pid="MATURE", n=3)
        cands = [{"cause": "随便先重启试试", "source": "llm"},
                 {"cause": "ZeroDivisionError：除法分母为 0", "source": "rules"}]
        ranked, note = self.eb.rank_candidates("BRAND_NEW", cands)
        self.assertTrue(note, "通用层命中应产生说明")
        self.assertIn("通用经验 1", note)
        hit = [c for c in ranked if (c.get("experience_match") or {}).get("source")
               == "通用经验（同语言）"]
        self.assertEqual(len(hit), 1)
        self.assertTrue(hit[0]["experience_match"]["action"].startswith("参考"))
        self.assertNotIn("experience_match", ranked[0] if ranked[0] is not hit[0] else ranked[-1])

    def test_generic_never_deprioritizes(self):
        """全局层 0 成功不触发"置末"（那是本项目失败史的专属结论）。"""
        self._lang(pid="OLD", ok=False, n=3)   # 全局除零 0 成功（但 score=0）
        # score=0 全局根因不进推荐；候选不命中 → 原序
        cands = [{"cause": "ZeroDivisionError 除零"}, {"cause": "别的"}]
        ranked, note = self.eb.rank_candidates("FRESH", cands)
        self.assertEqual((ranked, note), (cands, ""))


class GateTest(XferBase):
    def test_global_gate_off_reverts_l51(self):
        """MORAY_EXPERIENCE_GLOBAL=0：不写 scope、不聚合、不补位、不跨项目推荐。"""
        os.environ["MORAY_EXPERIENCE_GLOBAL"] = "0"
        eb = ExperienceBase()
        rec = eb.record({"project_id": "A", "error_type": "ZeroDivisionError",
                         "root_cause_category": "除零", "success": True,
                         "solution": "判空", "duration_sec": 1})
        self.assertNotIn("scope", rec)
        self.assertEqual(eb.top_global(top_n=5), [])
        self.assertEqual(eb.build_hint("BRAND_NEW", "ZeroDivisionError", 3), "")
        ranked, note = eb.rank_candidates("BRAND_NEW", [{"cause": "ZeroDivisionError 除零"}])
        self.assertEqual((ranked, note), ([{"cause": "ZeroDivisionError 除零"}], ""))
        st = eb.stats()
        self.assertFalse(st["evolution"]["global_on"])
        del os.environ["MORAY_EXPERIENCE_GLOBAL"]

    def test_total_gate_off_keeps_l50_shape(self):
        """总闸关 → scope 同样不写（L5.0 行形状承诺不变）。"""
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        rec = self.eb.record({"project_id": "A", "error_type": "ZeroDivisionError",
                              "root_cause_category": "除零", "success": True})
        self.assertNotIn("scope", rec)
        self.assertNotIn("cause_key", rec)
        del os.environ["MORAY_EXPERIENCE_EVOLUTION"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
