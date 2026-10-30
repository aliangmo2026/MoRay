# -*- coding: utf-8 -*-
"""L7.5 上行事件处理器单测：4 类事件对接现有能力 + 回调路由契约。

全离线：IssueBase/统计 sidecar/指令文件都指到 TMP；task_exception 的 run-error-fix 用桩。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l75/test_l75_events.py -q
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

TMP = Path(tempfile.mkdtemp(prefix="l75_ev_ut_"))

from engine import laya_connector as conn               # noqa: E402
from server.app import laya_event_handler as hnd        # noqa: E402
from server.app import task_routes as _tr               # noqa: E402


class EventBase(unittest.TestCase):
    def setUp(self):
        conn.reset_runtime_state()
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "exp_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pc.json")
        os.environ["MORAY_LAYA_DIRECTIVES"] = str(TMP / "directives.json")
        os.environ["MORAY_LAYA_STATS"] = str(TMP / "stats.json")
        os.environ["MORAY_LAYA_EVENT_SPOOL"] = str(TMP / "events_spool.jsonl")
        cfg = TMP / "config.yaml"
        cfg.write_text("laya_link:\n  enabled: false\n  auto_fix_on_exception: true\n",
                       encoding="utf-8")
        os.environ["MORAY_LAYA_CONFIG"] = str(cfg)
        hnd.clear_seen_events()
        for f in (TMP / "directives.json", TMP / "stats.json", TMP / "exp.jsonl",
                  TMP / "events_spool.jsonl"):
            if f.is_file():
                f.unlink()
        conn.reset_runtime_state()

    def tearDown(self):
        for k in ("MORAY_EXPERIENCE_DB", "MORAY_EXPERIENCE_STATS", "MORAY_STRATEGY_STATS",
                  "MORAY_PROJECT_PROFILES_DIR", "MORAY_PATTERN_CANDIDATES",
                  "MORAY_LAYA_DIRECTIVES", "MORAY_LAYA_STATS", "MORAY_LAYA_CONFIG",
                  "MORAY_LAYA_LINK", "MORAY_LAYA_AUTO_FIX", "MORAY_LAYA_EVENT_SPOOL"):
            os.environ.pop(k, None)
        hnd.clear_seen_events()
        conn.reset_runtime_state()


class UnknownScenarioTest(EventBase):
    def test_written_to_issue_base_with_laya_source(self):
        """未知场景 → 统一问题库，来源标「Laya引擎」（零迁移同库）。"""
        asyncio.run(hnd.handle_unknown_scenario({
            "scenario": "把二维码做成屏保", "task_type": "weird", "project_id": "P1"}))
        recs = [json.loads(l) for l in (TMP / "exp.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        laya = [r for r in recs if r.get("source") == "Laya引擎"]
        self.assertEqual(len(laya), 1)
        self.assertEqual(laya[0]["error_type"], "laya-unknown-scenario")
        self.assertIn("二维码", laya[0]["root_cause_category"])

    def test_empty_scenario_no_write(self):
        asyncio.run(hnd.handle_unknown_scenario({"scenario": "   ", "project_id": "P1"}))
        self.assertFalse((TMP / "exp.jsonl").is_file() and
                         (TMP / "exp.jsonl").read_text(encoding="utf-8").strip())


class HighRiskTest(EventBase):
    def test_creates_force_native_directive(self):
        """高风险 → 落 DAG 指令（force_native），供调度执行点消费触发动态干预。"""
        asyncio.run(hnd.handle_high_risk({"plan_id": "plHR", "task_id": "plHR:t3",
                                          "reason": "命中删除数据库关键词"}))
        pend = conn.pending_directives()
        self.assertTrue(any(d["kind"] == "force_native" and d["plan_id"] == "plHR" for d in pend))

    def test_parse_plan_from_task_id(self):
        asyncio.run(hnd.handle_high_risk({"task_id": "plFrom:task9", "reason": "x"}))
        self.assertTrue(any(d["plan_id"] == "plFrom" for d in conn.pending_directives()))

    def test_missing_plan_only_logs(self):
        asyncio.run(hnd.handle_high_risk({"reason": "无定位"}))   # 不抛异常
        self.assertEqual([d for d in conn.pending_directives()], [])


class TaskExceptionTest(EventBase):
    def test_auto_fix_calls_run_error_fix(self):
        """异常 → 接入现有 L4 run-error-fix（同前端"立即修复"链路），用桩拦截。"""
        seen = {}

        async def fake_fix(request):
            body = await request.json()
            seen["body"] = body

            class R:
                status_code = 202
            return R()
        orig = _tr.run_error_fix
        _tr.run_error_fix = fake_fix
        try:
            asyncio.run(hnd.handle_task_exception({
                "error_text": "TypeError: None", "project_dir": str(TMP), "project_id": "P9"}))
        finally:
            _tr.run_error_fix = orig
        self.assertEqual(seen["body"]["error_text"], "TypeError: None")
        self.assertEqual(seen["body"]["project_id"], "P9")

    def test_auto_fix_off_only_logs(self):
        os.environ["MORAY_LAYA_AUTO_FIX"] = "0"
        conn.reset_runtime_state()
        called = {"n": 0}

        async def boom(request):
            called["n"] += 1
        orig = _tr.run_error_fix
        _tr.run_error_fix = boom
        try:
            asyncio.run(hnd.handle_task_exception({"error_text": "x"}))
        finally:
            _tr.run_error_fix = orig
        self.assertEqual(called["n"], 0, "auto_fix 关时不触发修复")

    def test_no_error_text_ignored(self):
        called = {"n": 0}

        async def boom(request):
            called["n"] += 1
        orig = _tr.run_error_fix
        _tr.run_error_fix = boom
        try:
            asyncio.run(hnd.handle_task_exception({"foo": "bar"}))
        finally:
            _tr.run_error_fix = orig
        self.assertEqual(called["n"], 0)


class StatsReportTest(EventBase):
    def test_updates_global_stats_sidecar(self):
        """统计数据 → 更新全局性能统计 sidecar（含 Laya 上报块 + MoRay 计数）。"""
        conn._stats["dispatches"] = 5
        asyncio.run(hnd.handle_stats_report({"counters": {"decisions": 42}, "cache_size": 3}))
        data = json.loads((TMP / "stats.json").read_text(encoding="utf-8"))
        self.assertEqual(data["laya"]["counters"]["decisions"], 42)
        self.assertEqual(data["moray_connector"]["dispatches"], 5)
        self.assertIn("fuse", data)


class DispatchTest(EventBase):
    def test_process_event_routes_and_swallows(self):
        """process_event：命中白名单分派；未知类型返回 None；异常不外溢。"""
        r = asyncio.run(hnd.process_event({"event_type": "stats_report", "payload": {"k": 1}}))
        self.assertEqual(r, "stats_report")
        self.assertIsNone(asyncio.run(hnd.process_event({"event_type": "bogus"})))
        # 处理器抛错 → 吞掉返回 None（不 500、不回压 Laya）
        orig = hnd._HANDLERS["unknown_scenario"]

        async def raiser(payload):
            raise RuntimeError("boom")
        hnd._HANDLERS["unknown_scenario"] = raiser
        try:
            self.assertIsNone(asyncio.run(hnd.process_event(
                {"event_type": "unknown_scenario", "payload": {"scenario": "x"}})))
        finally:
            hnd._HANDLERS["unknown_scenario"] = orig


class EndpointContractTest(EventBase):
    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(hnd.router)
        return TestClient(app)

    def test_gate_off_returns_200_not_processed(self):
        """总闸关：回调仍 200（对 Laya 友好），accepted=false、零副作用。"""
        c = self._client()
        resp = c.post("/api/laya/events", json={"event_type": "unknown_scenario",
                                               "payload": {"scenario": "s", "project_id": "P"}})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["accepted"])
        self.assertFalse((TMP / "exp.jsonl").is_file() and
                         (TMP / "exp.jsonl").read_text(encoding="utf-8").strip())

    def test_gate_on_accepts(self):
        os.environ["MORAY_LAYA_LINK"] = "1"
        conn.reset_runtime_state()
        c = self._client()
        resp = c.post("/api/laya/events", json={"event_type": "high_risk_detected",
                                               "payload": {"plan_id": "plEP", "reason": "r"}})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["data"]["accepted"])

    def test_bad_json_and_unknown_type(self):
        c = self._client()
        self.assertEqual(c.post("/api/laya/events", content="not-json",
                               headers={"content-type": "application/json"}).status_code, 400)
        os.environ["MORAY_LAYA_LINK"] = "1"
        conn.reset_runtime_state()
        resp = c.post("/api/laya/events", json={"event_type": "mystery"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["data"]["accepted"])
        self.assertEqual(resp.json()["data"]["reason"], "unknown_event_type")


class DurabilityTest(EventBase):
    """评审 三.3：处理失败指数退避重试 → 落盘暂存不丢事件 → 补报；event_id 去重。"""

    def _failing_handler(self, errors=None):
        state = {"n": 0}

        async def fn(payload):
            state["n"] += 1
            raise RuntimeError("boom %d" % state["n"])
        return fn

    def test_retry_chain_recovers_in_memory(self):
        """第一次失败、第二次成功 → 内存重试消化，不落盘。"""
        state = {"n": 0}

        async def flaky(payload):
            state["n"] += 1
            if state["n"] < 2:
                raise RuntimeError("transient")
        orig = hnd._HANDLERS["unknown_scenario"]
        hnd._HANDLERS["unknown_scenario"] = flaky
        try:
            ok = asyncio.run(hnd._retry_chain(
                {"event_type": "unknown_scenario", "payload": {"scenario": "重试即愈",
                                                              "project_id": "PR"}},
                delays=(0.01, 0.01, 0.01)))
        finally:
            hnd._HANDLERS["unknown_scenario"] = orig
        self.assertTrue(ok)
        self.assertFalse((TMP / "events_spool.jsonl").is_file(), "内存重试成功不应落盘")

    def test_all_retries_exhausted_spools_event(self):
        """重试用尽 → 落盘 spool（不丢事件）。"""
        hnd._HANDLERS["unknown_scenario"] = self._failing_handler()
        try:
            ok = asyncio.run(hnd._retry_chain(
                {"event_type": "unknown_scenario", "event_id": "evS1",
                 "payload": {"scenario": "必坏事件", "project_id": "PR"}},
                delays=(0.01, 0.01)))
        finally:
            hnd._HANDLERS.pop("unknown_scenario", None)
            hnd._HANDLERS["unknown_scenario"] = hnd.handle_unknown_scenario
        self.assertFalse(ok)
        spool = TMP / "events_spool.jsonl"
        self.assertTrue(spool.is_file(), "重试用尽必须落盘暂存")
        line = spool.read_text(encoding="utf-8").strip()
        self.assertIn("evS1", line)

    def test_flush_spool_replays_after_recovery(self):
        """补报：处理器恢复后 flush_spool_once 把积压事件重新处理并清盘。"""
        hnd._spool_append({"event_type": "unknown_scenario", "event_id": "evF1",
                           "payload": {"scenario": "补报事件", "project_id": "PF"}})
        r = asyncio.run(hnd.flush_spool_once())      # 处理器真实可用 → 应消化
        self.assertEqual(r["replayed"], 1)
        self.assertEqual(r["kept"], 0)
        self.assertFalse((TMP / "events_spool.jsonl").is_file())
        recs = [json.loads(l) for l in (TMP / "exp.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        self.assertTrue(any(x.get("source") == "Laya引擎" for x in recs), "补报应真实入库")

    def test_flush_spool_keeps_still_failing(self):
        """补报时仍失败 → 保留在 spool（不丢）。"""
        hnd._spool_append({"event_type": "unknown_scenario", "event_id": "evK1",
                           "payload": {"scenario": "x", "project_id": "PK"}})
        hnd._HANDLERS["unknown_scenario"] = self._failing_handler()
        try:
            r = asyncio.run(hnd.flush_spool_once())
        finally:
            hnd._HANDLERS["unknown_scenario"] = hnd.handle_unknown_scenario
        self.assertEqual(r["replayed"], 0)
        self.assertEqual(r["kept"], 1)
        self.assertIn("evK1", (TMP / "events_spool.jsonl").read_text(encoding="utf-8"))

    def test_event_id_dedup(self):
        """event_id 去重：首次放行，重复拦截（Laya 补报重发不重复入库）。"""
        self.assertFalse(hnd._mark_seen("evD1"))
        self.assertTrue(hnd._mark_seen("evD1"))
        self.assertFalse(hnd._mark_seen(""))        # 无 id 不去重（如实处理）

    def test_endpoint_duplicate_event_id_marked(self):
        """端点：同 event_id 二次上报 → 200 + duplicated=true（接收即返回，不再处理）。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(hnd.router)
        os.environ["MORAY_LAYA_LINK"] = "1"
        conn.reset_runtime_state()
        try:
            c = TestClient(app)
            evt = {"event_type": "stats_report", "event_id": "epDup1", "payload": {"a": 1}}
            r1 = c.post("/api/laya/events", json=evt).json()
            r2 = c.post("/api/laya/events", json=evt).json()
        finally:
            os.environ.pop("MORAY_LAYA_LINK", None)
            conn.reset_runtime_state()
        self.assertTrue(r1["data"]["accepted"])
        self.assertTrue(r2["data"].get("duplicated"), "重复 event_id 应被标记")


if __name__ == "__main__":
    unittest.main()
