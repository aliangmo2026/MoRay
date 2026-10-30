# -*- coding: utf-8 -*-
"""L6.0 runner 接线测试：task_runner 四个策略点在真实执行器上生效（全离线，mock 网关/MCP）。

覆盖任务书 6.2 端到端项（1/2/4/6）的任务级形态：
  kread 深度自适应 / kwrite 反馈闭环落 sidecar / zcode 快速档跳过 / mcp 组合重排 / 全关闸=L5 行为。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l60/test_l60_runner_hooks.py -q
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l60_rh_ut_"))

from engine.task_runner import TaskTemplateRunner          # noqa: E402
from knowledge import strategy_optimizer as so             # noqa: E402
from knowledge.experience_base import ExperienceBase       # noqa: E402

TPL = {
    "name": "l6_ut_flow",
    "defaults": {"retries": 1, "timeout": 30},
    "steps": [
        {"id": "read_experience", "type": "knowledge_read", "timeout": 5,
         "args": {"project_id": "{{params.project_id}}", "top_n": 3}},
        {"id": "analyze_text", "type": "mcp_call", "timeout": 5,
         "args": {"tool": "iris_analyze_text", "arguments": {"text": "x"}}},
        {"id": "report", "type": "report", "timeout": 5,
         "args": {"path": "{{work_dir}}/r.md", "content": "ok"}},
    ],
}


def _env_isolated():
    os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
    os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
    os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
    os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
    os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
    for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_CAUSE_RANK", "MORAY_EXPERIENCE_GLOBAL",
              "MORAY_STRATEGY_OPTIMIZE", "MORAY_FIX_ADAPTIVE", "MORAY_PATTERN_MINER"):
        os.environ.pop(k, None)


def _clear_files():
    for f in TMP.glob("*"):
        if f.is_file():
            f.unlink()


def _mk_runner(params=None):
    tpl_path = TMP / "l6_ut_flow.yaml"
    tpl_path.write_text(json.dumps(TPL, ensure_ascii=False), encoding="utf-8")
    return TaskTemplateRunner(str(tpl_path), params=params or {}, work_dir=str(TMP / "wd"))


class KreadDepthTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_simple_error_narrows_top_n(self):
        r = _mk_runner(params={"error_text": "SyntaxError: invalid syntax"})
        out = r._exec_kread({"project_id": "P", "top_n": 3})
        self.assertLessEqual(len(out["top"]), 2, "简单错误：经验注入面收敛到 2")

    def test_gate_off_keeps_template_value(self):
        os.environ["MORAY_STRATEGY_OPTIMIZE"] = "0"
        try:
            r = _mk_runner(params={"error_text": "SyntaxError: invalid syntax"})
            out = r._exec_kread({"project_id": "P", "top_n": 3})
            self.assertEqual(len(out["top"]), 0)   # 无经验数据，形状只验不出错
        finally:
            os.environ.pop("MORAY_STRATEGY_OPTIMIZE", None)


class KwriteFeedbackTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_kwrite_writes_strategy_feedback(self):
        """任务书 6.2-1 的任务级形态：修复成功经验 → 反馈闭环落 sidecar。"""
        r = _mk_runner(params={"project_id": "P"})
        r.context["steps"]["fix_code"] = {"engine": "real:zcode"}
        entry = {"project_id": "P", "error_type": "ModuleNotFoundError",
                 "root_cause_category": "虚拟环境未安装依赖", "solution": "pip install x",
                 "success": "true"}
        out = r._exec_kwrite({"entry": entry})
        self.assertTrue(out["recorded"])
        st = so.load_stats()
        self.assertEqual(st["projects"]["P"]["causes"]["依赖模块未安装"]["wins"], 1)
        self.assertEqual(st["fix_tiers"]["P"][so.TIER_STANDARD]["tries"], 1,
                         "fix_code.engine 必须被归一为档位反馈")

    def test_kwrite_bad_entry_no_feedback(self):
        r = _mk_runner(params={"project_id": "P"})
        with self.assertRaises(Exception):
            r._exec_kwrite({"entry": {"project_id": "P"}})
        self.assertEqual(so.load_stats()["projects"], {}, "拒绝的条目不得产生反馈")


class ZcodeTierTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_fast_tier_skips_engine_probe(self):
        """任务书 6.2-4 任务级：偏好快速档 → 跳过真实引擎探针直走 llm_patch。"""
        for _ in range(3):
            so.feedback_fix("P", so.TIER_FAST, True)
        r = _mk_runner(params={"project_id": "P"})

        async def fake_patch(args):
            return {"applied": True}

        r._exec_llm_patch = fake_patch
        out, engine = asyncio.run(r._exec_zcode(
            {"prompt": "fix", "project_dir": "X:/none", "fallback": "llm_patch"}, {}))
        self.assertEqual(engine, "fallback:llm_patch(strategy_fast)")
        self.assertEqual(out["strategy_fix"]["tier"], so.TIER_FAST)

    def test_no_pick_goes_probe_path(self):
        r = _mk_runner(params={"project_id": "P"})
        calls = []

        def fake_probe():          # to_thread 只做同步调用（async 函数体不会执行）
            calls.append(1)
            return "zcode_missing"

        import engine.task_runner as tr
        orig = tr._zcode_probe
        tr._zcode_probe = fake_probe

        async def fake_patch(args):
            return {"applied": True}

        r._exec_llm_patch = fake_patch
        try:
            out, engine = asyncio.run(
                r._exec_zcode({"prompt": "fix", "fallback": "llm_patch"}, {}))
        finally:
            tr._zcode_probe = orig
        self.assertEqual(calls, [1], "无策略命中必须照旧走真实引擎探针")
        self.assertTrue(engine.startswith("fallback:llm_patch("))


class McpComposeTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_analyze_step_sets_complexity_and_ranks(self):
        """任务书 6.2-2 任务级：连败根因经 mcp 组合重排被置末标注；complexity 进 context。"""
        eb = ExperienceBase()
        eb.record({"project_id": "P", "error_type": "TypeError",
                   "root_cause_category": "类型错误", "solution": "s", "success": False})
        so.feedback("P", "类型错误", False)
        so.feedback("P", "类型错误", False)
        so.feedback("P", "类型错误", False)
        r = _mk_runner(params={"project_id": "P"})

        class FakeClient:
            async def call_tool(self, tool, arguments):
                return {"code": "OK", "data": {
                    "error_type": "TypeError",
                    "root_cause_candidates": [
                        {"cause": "类型错误", "experience_match": {"cause_key": "类型错误"}},
                        {"cause": "全新假设"}]}}
        import engine.task_runner as tr
        orig = tr.IrisMCPClient
        tr.IrisMCPClient = FakeClient
        try:
            out = asyncio.run(r._exec_mcp(
                {"tool": "iris_analyze_text", "arguments": {}}))
        finally:
            tr.IrisMCPClient = orig
        cands = out["data"]["root_cause_candidates"]
        self.assertEqual(cands[-1].get("strategy_action"), "skip", "连败根因必须置末+标注")
        self.assertIn("strategy_rank_note", out["data"])
        # complexity 采集在 run() 层（RunFlowTest 覆盖），此处只验重排面。

    def test_gate_off_identical_behavior(self):
        """任务书 6.2-6：全关闸 → mcp 输出与 L5 逐字节一致。"""
        for k in ("MORAY_STRATEGY_OPTIMIZE", "MORAY_CAUSE_RANK"):
            os.environ[k] = "0"
        try:
            eb = ExperienceBase()
            eb.record({"project_id": "P", "error_type": "TypeError",
                       "root_cause_category": "类型错误", "solution": "s", "success": False})
            so.feedback("P", "类型错误", False)
            so.feedback("P", "类型错误", False)
            so.feedback("P", "类型错误", False)
            r = _mk_runner(params={"project_id": "P"})

            class FakeClient:
                async def call_tool(self, tool, arguments):
                    return {"code": "OK", "data": {
                        "error_type": "TypeError",
                        "root_cause_candidates": [{"cause": "类型错误"}, {"cause": "全新假设"}]}}
            import engine.task_runner as tr
            orig = tr.IrisMCPClient
            tr.IrisMCPClient = FakeClient
            try:
                out = asyncio.run(r._exec_mcp(
                    {"tool": "iris_analyze_text", "arguments": {}}))
            finally:
                tr.IrisMCPClient = orig
            self.assertNotIn("strategy_rank_note", out["data"])
            self.assertNotIn("experience_rank_note", out["data"])
            self.assertEqual(len(out["data"]["root_cause_candidates"]), 2)
        finally:
            os.environ.pop("MORAY_STRATEGY_OPTIMIZE", None)
            os.environ.pop("MORAY_CAUSE_RANK", None)


class RunFlowTest(unittest.TestCase):
    def setUp(self):
        _env_isolated()
        _clear_files()

    def test_full_run_offline_sets_complexity(self):
        """整条 run() 离线跑通（Fake MCP），analyze 后 complexity 写入 context。"""
        r = _mk_runner(params={"project_id": "P", "error_text": "x"})

        class FakeClient:
            async def call_tool(self, tool, arguments):
                return {"code": "OK", "data": {"error_type": "ModuleNotFoundError",
                                               "root_cause_candidates": []}}
        import engine.task_runner as tr
        orig = tr.IrisMCPClient
        tr.IrisMCPClient = FakeClient
        try:
            rep = asyncio.run(r.run())
        finally:
            tr.IrisMCPClient = orig
        self.assertEqual(rep["status"], "completed")
        self.assertEqual(r.context.get("complexity"), "complex",
                         "依赖类错误 → complex（后续步骤 retries+1 的依据）")


if __name__ == "__main__":
    unittest.main()
