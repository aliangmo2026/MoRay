# -*- coding: utf-8 -*-
"""L5.1 阶段5.2 端到端：连续 6 次同类报错走真实 text_fix_flow 模板链，验证经验进化生效。

链路全真实：TaskTemplateRunner + 临时经验库(JSONL/stats/画像) + 真 zcode 探针（本机必不可用
→llm_patch 降级，网关替身上游）+ 真 local_exec 验证（项目里放一个真会除零的 main.py，
fake llm 返回修复版全文）。仅替身两处：IrisMCPClient.call_tool（返回结构化候选并录屏收到的
hint）、httpx.AsyncClient（网关）。
判定点（任务书 5.2）：
  1. 前 5 次完成 → 元数据该项目 Top1 = 除零键 ×5（不同措辞被归一化收敛）；
  2. 第 6 次 read_experience 产出的 hint 含"除零"；analyze 候选被经验重排（历史键第一 + note）；
  3. 项目画像 JSON 正确生成（records=6、高频语言 python）；
  4. 三模板/串联/模糊意图零影响由全量回归另证。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l51/test_l51_e2e.py -q
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l51_e2e_"))

import httpx  # noqa: E402
from unittest import mock  # noqa: E402
import engine.task_runner as tr  # noqa: E402
from knowledge.experience_base import ExperienceBase  # noqa: E402

PROJ = TMP / "l51_e2e_proj"
BUG_CODE = (
    "def calc_total(items, discount):\n"
    "    total = sum(items)\n"
    "    avg = total / len(items)\n"
    "    return total, avg\n\n"
    "print(calc_total([], 0)[1])\n")
FIXED_CODE = (
    "def calc_total(items, discount):\n"
    "    total = sum(items)\n"
    "    avg = total / len(items) if items else 0\n"
    "    return total, avg\n\n"
    "print(calc_total([], 0)[1])\n")

WORDINGS = [
    "cart 是空列表，calc_total 里 len(items) 为 0，total / len(items) 触发除零异常",
    "调用 calc_total(cart, 0) 时传入空序列，avg = total / len(items) 中分母为 0",
    "第 4 行除数为 0：items 为空导致 len(items)==0，ZeroDivisionError",
]


class FakeMCP:
    """替身 Iris：返回结构化候选（候选0 措辞轮换），并录屏每次收到的 hint。"""
    calls = []
    round_no = 0   # 每轮 _run_once 递增（不依赖 calls 计数）

    def __init__(self, cfg=None):
        pass

    async def call_tool(self, tool, arguments):
        FakeMCP.calls.append(dict(arguments))
        i = FakeMCP.round_no
        cands = [
            {"cause": WORDINGS[(i - 1) % 3] if i <= 5 else "先重启服务再试（干扰候选）",
             "evidence": "llm 诊断", "source": "llm", "priority": 1},
            {"cause": "calc_total 空集合除法：ZeroDivisionError: division by zero",
             "evidence": "规则提取", "source": "rules", "priority": 2},
        ]
        return {"code": "OK", "data": {
            "error_type": "ZeroDivisionError",
            "error_content": "Traceback...ZeroDivisionError: division by zero",
            "file_type": "python", "file_path": "main.py", "line_number": 3,
            "severity": "high", "root_cause_candidates": cands,
            "diagnosis": {"root_cause": WORDINGS[(i - 1) % 3], "plan": "加空集合判断",
                          "fix_code": FIXED_CODE}}}
        # 注：第 6 次候选0 是"重启服务"干扰项 → 期望经验重排把候选1（除零键）提到最前


class FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


class FakeAsyncClient:
    """替身网关：llm_patch 恒返回修复全文。"""
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        content = json.dumps({"file": "main.py", "content": FIXED_CODE}, ensure_ascii=False)
        return FakeResponse({"ok": True, "content": content,
                             "usage": {"total_tokens": 60}})


class L51E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 进程级 env 隔离必须在类执行时设置（不在模块导入时）：
        # pytest 同跑多文件时后导入者的模块级设置会先覆盖别人（Day 老坑"末导入者赢"）。
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        FakeMCP.round_no = 0
        PROJ.mkdir(parents=True, exist_ok=True)
        (PROJ / "main.py").write_text(BUG_CODE, encoding="utf-8")
        cls.eb = ExperienceBase()
        cls.reports = []
        cls.run_dirs = []

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        for d in cls.run_dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _run_once(self):
        FakeMCP.calls = []
        FakeMCP.round_no += 1
        (PROJ / "main.py").write_text(BUG_CODE, encoding="utf-8")   # 每轮重置回带 bug 的项目
        with mock.patch.object(tr, "IrisMCPClient", FakeMCP), \
             mock.patch.object(httpx, "AsyncClient", FakeAsyncClient), \
             mock.patch.object(tr, "_docker_available", lambda: False), \
             mock.patch.object(tr, "_zcode_probe",
                               lambda: "l51_e2e 前提钉死：zcode 判不可用 → llm_patch 降级链"):
            runner = tr.TaskTemplateRunner(
                str(ROOT / "templates" / "text_fix_flow.yaml"),
                params={"project_dir": str(PROJ), "project_id": "l51_e2e_proj",
                        "error_text": "Traceback...ZeroDivisionError: division by zero",
                        "verify_cmd": "python main.py"},
                token_budget=8000)
            self.run_dirs.append(str(runner.work_dir))
            rep = asyncio_run(runner.run())
        return rep, FakeMCP.calls

    def test_1_five_rounds_then_sixth_prioritizes_history(self):
        for n in range(1, 6):
            rep, calls = self._run_once()
            self.assertEqual(rep["status"], "completed", "第 %d 轮应完整跑完: %s" % (n, rep.get("steps")))
            top = self.eb.top_meta("l51_e2e_proj", None, 5)
            self.assertEqual(top[0]["times"], n, "第 %d 轮后 Top1 计数应为 %d" % (n, top[0]["times"]))
            self.assertIn("除零", top[0]["root_cause_category"])
            # 前 5 轮 hint 可为空/浅（历史不足），重排 note 至多弱命中
        # —— 第 6 次 ——
        rep, calls = self._run_once()
        self.assertEqual(rep["status"], "completed")
        steps = {s["id"]: s for s in rep["steps"]}
        self.assertEqual(steps["read_experience"]["status"], "ok")
        hint = steps["read_experience"]["output"]["hint"]
        self.assertIn("除零", hint, "第 6 次必须读到高频根因 Top5")
        self.assertIn("历史 5 次", hint)
        self.assertIn("除零", calls[0].get("text", ""), "hint 确实注入到了 analyze 提示文本")
        data = steps["analyze_text"]["output"]["data"]
        self.assertTrue(data.get("experience_rank_note"), "重排说明应写入分析产物")
        first = data["root_cause_candidates"][0]
        self.assertIn("calc_total 空集合除法", first["cause"], "高频根因必须自动排到最前面验证")
        self.assertTrue(first["experience_match"]["action"].startswith("优先验证"))
        self.assertEqual(steps["record_experience"]["status"], "ok")
        self.assertEqual(self.eb.top_meta("l51_e2e_proj", None, 5)[0]["times"], 6)

    def test_2_profile_generated_on_real_chain(self):
        prof = self.eb.read_profile("l51_e2e_proj")
        self.assertIsNotNone(prof)
        self.assertGreaterEqual(prof["records"], 6)
        self.assertEqual(prof["high_freq_language"], "python")
        self.assertEqual(prof["top_root_causes"][0]["cause_key"],
                         "除零错误（分母为0/空集合均值）")
        self.assertTrue((TMP / "profiles").is_dir() and
                        any((TMP / "profiles").glob("*.json")))

    def test_3_endpoint_shape_with_testclient(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from server.app import task_routes
        app = FastAPI()
        app.include_router(task_routes.router)
        with TestClient(app) as client:
            r = client.get("/api/experience/profile", params={"project_id": "l51_e2e_proj"})
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertTrue(d["ok"])
            self.assertTrue(d["data"]["found"])
            self.assertIn("除零", d["data"]["profile"]["top_root_causes"][0]["cause_key"])
            r2 = client.get("/api/experience/profile")
            self.assertEqual(r2.status_code, 400)
            r3 = client.get("/api/experience/profile", params={"project_id": "no_such"})
            self.assertFalse(r3.json()["data"]["found"])


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main(verbosity=2)
