# -*- coding: utf-8 -*-
"""L7.7 体验打磨 · 后端确定性测试（任务书 三.1/三.2）。

范式：unittest + 桩 tool_registry.call_tool（零网络零模型，与 L7.6 验收同法）；
LAYA_SELFCHECK_LLM=0 纯规则自检。跑法（lora-training 目录下）：
  D:\\python3.12\\python.exe -X utf8 -m unittest test_l77_agent_polish -v
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import unittest

os.environ.setdefault("LAYA_SELFCHECK_LLM", "0")
os.environ.setdefault("LAYA_PLANNER_LLM", "0")

from modules import executor, task_planner, tool_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ok_env(tool, data="RESULT"):
    return {"ok": True, "tool": tool, "data": data, "error": None, "elapsed_ms": 1}


def _err_env(tool, msg="boom"):
    return {"ok": False, "tool": tool, "data": None,
            "error": {"code": -1, "message": msg}, "elapsed_ms": 1}


class PlannerKeywordExpansion(unittest.TestCase):
    """三.1：单工具新增 ≥10 个常用触发词——用行为断言（新词必须归对工具）。"""

    def test_each_tool_grew_by_12(self):
        for tool, kws in task_planner.TOOL_KEYWORDS:
            self.assertGreaterEqual(len(kws), 16, "%s 词表应扩充到 ≥16 项" % tool)

    def test_new_iris_words(self):
        for sent in ["识图这张扫描件", "提取文字给我", "屏幕截图里的内容", "看看这张图"]:
            self.assertEqual(task_planner._classify(sent), "iris", sent)

    def test_new_file_words(self):
        for sent in ["打开目录看看", "列出文件清单", "把这个另存为 report.md", "追加写入日志"]:
            self.assertEqual(task_planner._classify(sent), "file", sent)

    def test_new_zcode_words(self):
        for sent in ["帮我改代码里的bug", "重构这个模块", "跑测试看看", "执行命令 ls"]:
            self.assertEqual(task_planner._classify(sent), "zcode", sent)

    def test_new_argus_words(self):
        for sent in ["查询最新榜单", "对比评测两家价格", "搜集行业资讯", "全网热点"]:
            self.assertEqual(task_planner._classify(sent), "argus", sent)

    def test_bare_ext_paths(self):
        self.assertEqual(task_planner._extract_path("运行 setup.bat 试试"), "setup.bat")
        self.assertEqual(task_planner._extract_path("读 D:\\MoRay\\tasks.json"), "D:\\MoRay\\tasks.json")


class PlannerChainModes(unittest.TestCase):
    """三.1：新增 3 种常见任务模式匹配（查资料→写文件 / 读文件→改代码→执行 / 截图识别→分析→修复）。"""

    def test_research_then_write(self):
        steps = _run(task_planner.plan("搜索最新大模型微调资讯，然后写入 laya_news.md"))["steps"]
        self.assertEqual([s["tool"] for s in steps], ["argus", "file"])
        self.assertEqual(steps[1]["input"]["op"], "write")
        self.assertEqual(steps[1]["input"]["path"], "laya_news.md")
        self.assertIn("{prev_output}", steps[1]["input"]["content"])
        self.assertNotIn("然后", steps[0]["description"])  # 连接词不残留在左句

    def test_read_fix_run(self):
        steps = _run(task_planner.plan("读取 config.yaml 然后修改代码并运行"))["steps"]
        self.assertEqual([s["tool"] for s in steps], ["file", "zcode", "zcode"])
        self.assertEqual(steps[0]["input"]["op"], "read")
        self.assertEqual(steps[0]["input"]["path"], "config.yaml")
        self.assertIn("{prev_output}", steps[1]["input"]["code"])
        self.assertIn("{prev_output}", steps[2]["input"]["code"])

    def test_shot_analyze_fix(self):
        steps = _run(task_planner.plan("识别这张截图里的报错并分析原因，生成修复代码"))["steps"]
        self.assertEqual([s["tool"] for s in steps], ["iris", "zcode", "zcode"])
        self.assertIn("分析", steps[1]["input"]["code"])
        self.assertIn("修复代码", steps[2]["input"]["code"])
        for s in steps:
            self.assertTrue(s["expected_output"])

    def test_no_false_chain(self):
        # 单工具目标不得被复合模式劫持
        steps = _run(task_planner.plan("查一下今天的天气"))["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["tool"], "argus")


class PlannerConnectorSplit(unittest.TestCase):
    """三.1：多工具连续任务识别——新连接词分句 + 虚词子句并入前步。"""

    def test_new_connectors_split(self):
        steps = _run(task_planner.plan("先搜一下芯片行情随后写入 market.md"))
        tools = [s["tool"] for s in steps["steps"]]
        self.assertIn("argus", tools)

    def test_filler_clause_merged(self):
        steps = _run(task_planner.plan("列出目录的文件清单，写得好看点"))["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["tool"], "file")
        self.assertIn("写得好看点", steps[0]["description"])


class ExecutorEventFields(unittest.TestCase):
    """三.2：retry_count / retry 事件 / 毫秒级 ts / step_result 全量字段 / final 回显 execution_id。"""

    def setUp(self):
        self._orig = tool_registry.call_tool

    def tearDown(self):
        tool_registry.call_tool = self._orig

    def test_fields_and_timestamps(self):
        async def fake(tool, payload):
            return _ok_env(tool)
        tool_registry.call_tool = fake
        steps = [{"step_id": 1, "description": "读目录", "tool": "file",
                  "input": {"op": "list_dir"}, "expected_output": "x"}]
        res = _run(executor.execute_collect("goal", steps, max_retry=0))
        events = res["events"]
        for ev in events:
            self.assertIsInstance(ev.get("ts"), int, "每个事件必须带毫秒 ts")
            self.assertGreater(ev["ts"], 1_700_000_000_000, "ts 应为毫秒级纪元时间")
        sr = next(e for e in events if e["event"] == "step_result")
        self.assertEqual(sr["retry_count"], 0)
        self.assertEqual(sr["status"], "ok")
        self.assertEqual(sr["tool"], "file")
        self.assertEqual(sr["description"], "读目录")
        ck = next(e for e in events if e["event"] == "check")
        self.assertEqual(ck["retry_count"], 0)
        self.assertEqual(res["final"]["execution_id"], events[0]["execution_id"])

    def test_retry_event_and_count(self):
        calls = {"n": 0}

        async def flaky(tool, payload):
            calls["n"] += 1
            return _err_env(tool) if calls["n"] == 1 else _ok_env(tool)
        tool_registry.call_tool = flaky
        steps = [{"step_id": 1, "description": "跑脚本", "tool": "zcode",
                  "input": {"code": "x=1"}, "expected_output": "y"}]
        res = _run(executor.execute_collect("goal", steps, max_retry=2))
        events = res["events"]
        retries = [e for e in events if e["event"] == "retry"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["retry_count"], 1)
        self.assertEqual(retries[0]["ts"] >= events[0]["ts"], True)
        srs = [e for e in events if e["event"] == "step_result"]
        self.assertEqual([e["retry_count"] for e in srs], [0, 1])
        self.assertEqual(srs[0]["status"], "failed")
        self.assertEqual(srs[1]["status"], "ok")
        self.assertEqual(res["final"]["status"], "success")

    def test_cancel_at_boundary(self):
        async def fake(tool, payload):
            return _ok_env(tool)
        tool_registry.call_tool = fake

        async def drive():
            steps = [{"step_id": 1, "description": "a", "tool": "file",
                      "input": {"op": "read", "path": "x.txt"}, "expected_output": "e"}]
            collected = []
            gen = executor.execute_stream("goal", steps, max_retry=0, execution_id="l77-cancel-test")
            first = await gen.__anext__()          # start 帧已注册 execution_id
            executor.cancel("l77-cancel-test")     # 边界前取消
            async for ev in gen:
                collected.append(ev)
            return first, collected
        first, collected = _run(drive())
        final = [e for e in collected if e["event"] == "final"][0]
        self.assertEqual(final["status"], "cancelled")
        self.assertNotIn("l77-cancel-test", executor.running_ids())


class CorsMounted(unittest.TestCase):
    """一.4/二.2：浏览器直连 8001 的前置条件——CORS 中间件已加性挂载。"""

    def test_cors_in_middleware_stack(self):
        import laya_std_server
        origins = [m.cls.__name__ for m in laya_std_server.app.user_middleware]
        self.assertIn("CORSMiddleware", origins)


class SSEWireFormat(unittest.TestCase):
    """_sse 包装帧仍为合法 SSE data 行（前端按行解析的硬契约）。"""

    def test_frames_parseable(self):
        import laya_std_server

        async def drive():
            orig = tool_registry.call_tool

            async def fake(tool, payload):
                return _ok_env(tool)
            tool_registry.call_tool = fake
            steps = [{"step_id": 1, "description": "d", "tool": "file",
                      "input": {"op": "list_dir"}, "expected_output": "e"}]
            out = []
            async for chunk in laya_std_server._sse(
                    executor.execute_stream("goal", steps, max_retry=0)):
                out.append(chunk)
            tool_registry.call_tool = orig
            return out
        chunks = _run(drive())
        text = "".join(chunks)
        frames = [ln[6:] for ln in text.split("\n") if ln.startswith("data: ")]
        events = [json.loads(f) for f in frames]
        self.assertGreaterEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1]["event"], "final")
        self.assertTrue(all(isinstance(e.get("ts"), int) for e in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
