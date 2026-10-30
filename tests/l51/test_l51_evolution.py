# -*- coding: utf-8 -*-
"""L5.1 阶段5.1 单元测试：经验进化（归一化聚类 / Top5 元数据 / 排查顺序重排 / 项目画像 / 隔离 / 闸门 / 自愈）。

全离线：临时目录三件套（JSONL / experience_stats.json / profiles），
不碰正式经验库与正式画像目录。运行：
    server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l51/test_l51_evolution.py -q
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l51_ut_"))

from knowledge.experience_base import (ExperienceBase, detect_language,  # noqa: E402
                                       normalize_cause, project_profile_hash)

# 同一种"除零"错误、LLM 三种不同措辞的长描述（模拟现网 root_cause_category 形态）
WORDINGS = [
    "`cart` 是空列表，`calc_total(cart, 0)` 把空列表传入函数，`len(items)` 为 0 时执行 "
    "`total / len(items)` 触发除零异常。第二个参数 0 不是除零原因。",
    "调用 calc_total 时传入的 cart 是空列表，函数内部执行 avg = total / len(items)，"
    "此时 len(items) 为 0，导致 ZeroDivisionError: division by zero。",
    "第 10 行 cart 为空，函数内 items 长度为 0；第 4 行 total / len(items) 除数为 0，"
    "触发 ZeroDivisionError。参数 0 不是根因。",
]


class L51Base(unittest.TestCase):
    def setUp(self):
        # env 隔离在 setUp 内设置（模块导入期设置会在多文件同跑时被末导入者覆盖）
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ.pop("MORAY_EXPERIENCE_EVOLUTION", None)
        os.environ.pop("MORAY_CAUSE_RANK", None)
        for f in (TMP / "exp.jsonl", TMP / "experience_stats.json"):
            if f.exists():
                f.unlink()
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        self.eb = ExperienceBase()

    def _zero(self, pid="P", i=0, ok=True, **kw):
        base = {"project_id": pid, "error_type": "ZeroDivisionError",
                "root_cause_category": WORDINGS[i % 3],
                "error_snippet": "ZeroDivisionError: division by zero",
                "solution": "在除法前对 len(items) 做零值判断",
                "duration_sec": 12.0, "success": ok}
        base.update(kw)
        return self.eb.record(base)


class NormalizeTest(unittest.TestCase):
    def test_same_error_three_wordings_one_key(self):
        keys = {normalize_cause({"error_type": "ZeroDivisionError",
                                 "root_cause_category": w}) for w in WORDINGS}
        self.assertEqual(len(keys), 1, "三种措辞必须收敛到同一根因键: %s" % keys)
        self.assertIn("除零", list(keys)[0])

    def test_free_text_falls_back_to_category(self):
        k = normalize_cause({"error_type": "MNE", "root_cause_category": "高频根因"})
        self.assertEqual(k, "高频根因")

    def test_language_detection(self):
        self.assertEqual(detect_language({"error_type": "ZeroDivisionError",
                                          "root_cause_category": "Traceback (most recent call last)"}),
                         "python")
        self.assertEqual(detect_language({"error_type": "NullPointerException",
                                          "root_cause_category": "Exception in thread main"}),
                         "java")


class ClusteringTest(L51Base):
    def test_three_records_top1_correct(self):
        """任务书 5.1：3 次同类报错 → Top 1 正确（且来自不同措辞）。"""
        for i in range(3):
            self._zero(i=i)
        top = self.eb.top_meta("P", None, 5)
        self.assertTrue(top)
        self.assertEqual(top[0]["times"], 3)
        self.assertEqual(top[0]["successes"], 3)
        self.assertIn("除零", top[0]["root_cause_category"])

    def test_top5_order_freq_times_rate(self):
        for _ in range(4):
            self._zero(ok=True)
        for _ in range(3):
            self._zero(i=99 % 3, ok=False, error_type="TimeoutError",
                       root_cause_category="外部接口 timed out 超时", solution="")
        # 同类 Timeout 键：错误类型 TimeoutError → 键"操作超时"，3 次 0 成功 → score 0
        top = self.eb.top_meta("P", None, 5)
        self.assertEqual(top[0]["root_cause_category"], "除零错误（分母为0/空集合均值）")
        self.assertGreater(top[0]["score"], 0)
        zero_rate = [r for r in top if r["successes"] == 0]
        for r in zero_rate:
            self.assertEqual(r["score"], 0)

    def test_record_writes_cause_key_and_language(self):
        rec = self._zero()
        self.assertIn("cause_key", rec)
        self.assertEqual(rec["language"], "python")

    def test_query_semantics_unchanged(self):
        """L5.0 契约不破：query 仍按原文类别聚合。"""
        self._zero(i=0)
        self._zero(i=1)
        rows = self.eb.query("P", "ZeroDivisionError", top_n=5)
        self.assertEqual(len(rows), 2, "措辞不同 → query 原文聚合应得 2 组")


class RankTest(L51Base):
    def test_high_freq_first_zero_success_last(self):
        for _ in range(5):
            self._zero(ok=True)
        for _ in range(3):
            self._zero(ok=False, error_type="FileNotFoundError",
                       root_cause_category="config.yaml 文件不存在，路径拼错")
        cands = [
            {"cause": "建议先重启服务再看", "source": "llm", "priority": 1},
            {"cause": "config.yaml 缺失：No such file or directory 文件不存在", "source": "rules", "priority": 2},
            {"cause": "calc_total 里 len(items) 为 0，total / len(items) 触发 ZeroDivisionError",
             "source": "llm", "priority": 3},
        ]
        ranked, note = self.eb.rank_candidates("P", cands)
        self.assertTrue(note)
        self.assertEqual(ranked[0]["experience_match"]["action"].startswith("优先验证"), True)
        self.assertIn("除零", ranked[0]["experience_match"]["cause_key"])
        last = ranked[-1]
        self.assertIn("置末", last["experience_match"]["action"])
        # 未命中历史的候选保持相对序（在中间）
        self.assertNotIn("experience_match", ranked[1])
        # 原列表未被就地修改
        self.assertEqual(cands[0]["cause"], "建议先重启服务再看")

    def test_unknown_project_noop(self):
        self._zero()
        cands = [{"cause": "任意原因"}]
        ranked, note = self.eb.rank_candidates("NO_SUCH_PROJECT", cands)
        self.assertEqual(note, "")
        self.assertEqual(ranked, cands)

    def test_gate_off_evolution_returns_asis(self):
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        cands = [{"cause": "除零"}, {"cause": "别的"}]
        ranked, note = self.eb.rank_candidates("P", cands)
        self.assertEqual((ranked, note), (cands, ""))


class ProfileTest(L51Base):
    def test_profile_generated_and_readable(self):
        self._zero(ok=True, duration_sec=10.0)
        self._zero(ok=True, duration_sec=20.0, i=1)
        prof = self.eb.read_profile("P")
        self.assertIsNotNone(prof)
        self.assertEqual(prof["project_id"], "P")
        self.assertEqual(prof["records"], 2)
        self.assertEqual(prof["high_freq_language"], "python")
        self.assertEqual(prof["top_error_types"][0]["error_type"], "ZeroDivisionError")
        self.assertEqual(prof["top_root_causes"][0]["times"], 2)
        self.assertIn("len(items)", prof["common_solutions"][0]["solution"])
        self.assertEqual(prof["avg_duration_sec"], 15.0)
        self.assertEqual(prof["avg_duration_sec_success"], 15.0)
        # 文件名 = sha256 前 16 位
        p = TMP / "profiles" / ("%s.json" % project_profile_hash("P"))
        self.assertTrue(p.is_file())

    def test_project_isolation(self):
        self._zero(pid="P1")
        self._zero(pid="P2", error_type="TimeoutError", root_cause_category="外部接口 timed out 超时")
        self.assertEqual(self.eb.top_meta("P1", None, 5)[0]["times"], 1)
        self.assertEqual(self.eb.top_meta("P2", None, 5)[0]["root_cause_category"], "操作超时")
        prof1 = self.eb.read_profile("P1")
        self.assertNotEqual(prof1["top_error_types"][0]["error_type"], "TimeoutError")
        # 画像互不污染：P1 画像里没有 P2
        self.assertNotIn("P2", json.dumps(prof1, ensure_ascii=False))


class ResilienceTest(L51Base):
    def test_meta_missing_self_heals(self):
        for i in range(3):
            self._zero(i=i)
        meta = TMP / "experience_stats.json"
        self.assertTrue(meta.is_file())
        meta.unlink()
        top = self.eb.top_meta("P", None, 5)   # ensure_meta → 全量重建
        self.assertTrue(top and top[0]["times"] == 3)
        self.assertTrue(meta.is_file(), "重建后元数据文件必须回位")

    def test_external_append_caught_up(self):
        self._zero()
        with open(TMP / "exp.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"project_id": "P", "error_type": "ZeroDivisionError",
                                "root_cause_category": WORDINGS[1], "success": True,
                                "timestamp": "2026-09-28T00:00:00+08:00"}, ensure_ascii=False) + "\n")
        top = self.eb.top_meta("P", None, 5)
        self.assertEqual(top[0]["times"], 2, "别的入口直写 JSONL 后，读侧必须追平增量")

    def test_gate_off_record_is_l50_shaped(self):
        os.environ["MORAY_EXPERIENCE_EVOLUTION"] = "0"
        rec = self._zero()
        self.assertNotIn("cause_key", rec)
        self.assertFalse((TMP / "experience_stats.json").exists())
        self.assertEqual(self.eb.stats()["records"], 1)

    def test_hint_reads_meta_with_real_data_shape(self):
        for i in range(3):
            self._zero(i=i, ok=(i != 2))
        hint = self.eb.build_hint("P", "ZeroDivisionError", 3)
        self.assertIn("除零", hint)
        self.assertIn("历史 3 次", hint)
        self.assertIn("成功率 66%", hint)   # 2/3


if __name__ == "__main__":
    unittest.main(verbosity=2)
