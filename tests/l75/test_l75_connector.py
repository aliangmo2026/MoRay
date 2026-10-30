# -*- coding: utf-8 -*-
"""L7.5.1 下行调度连接器单测：开关/三路由/风险/分级熔断(5次/30s/延长/半开恢复)/
幂等/防循环hop/契约严校/置信门槛/DAG指令/请求契约字段 + 调度器单行接入。

旁路全离线：httpx.AsyncClient 被 fake 拦截（不连真端口），runner 走注入工厂。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l75/test_l75_connector.py -q
"""
import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l751_conn_ut_"))

import httpx                                   # noqa: E402
from engine import laya_connector as conn      # noqa: E402
from engine import execution_scheduler as es   # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


def envelope(**over):
    """评审 一.2 契约全键假响应（缺哪个键由具体用例 pop 模拟）。"""
    base = {"code": 0, "route_result": "native", "risk_level": "low", "cache_hit": False,
            "template_name": None, "result_data": None, "decision_id": "dflt",
            "confidence": 0.99, "is_fast_path": False, "fallback_reason": None,
            "rule_version": "l75-1.1", "model_version": "moray-laya-final",
            "latency_ms": 1.2}
    base.update(over)
    return base


def _mk(handler):
    """fake httpx.AsyncClient：post 交给 handler(url, body)，可返回 FakeResp 或抛异常。"""
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return handler(url, json)
    return C


def restore(orig):
    httpx.AsyncClient = orig


class FakeRunner:
    def __init__(self, tpl, params, work_dir):
        self.tpl, self.params = tpl, params

    async def run(self):
        return {"task": self.tpl, "status": "completed",
                "steps": [{"id": "x", "type": "x", "status": "ok", "output": {}}],
                "human_intervention": None}


class FakeRun:
    def __init__(self, plan_id="plX"):
        self.plan_id = plan_id
        self.goal = "测试目标"
        self.status = "running"
        self.subtasks = []
        self.decisions = []
        self.work_dir = TMP / "wd" / plan_id
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def add_decision(self, dtype, reason, basis, result):
        self.decisions.append({"type": dtype, "reason": reason, "basis": basis, "result": result})


class ConnectorBase(unittest.TestCase):
    def setUp(self):
        conn.reset_runtime_state()
        os.environ["MORAY_LAYA_DIRECTIVES"] = str(TMP / "directives.json")
        d = TMP / "directives.json"
        if d.is_file():
            d.unlink()
        for k in ("MORAY_LAYA_LINK", "MORAY_LAYA_HOST", "MORAY_LAYA_PORT",
                  "MORAY_LAYA_FUSE_THRESHOLD", "MORAY_LAYA_FUSE_DURATION_S",
                  "MORAY_LAYA_FUSE_MAX_S", "MORAY_LAYA_CONFIDENCE"):
            os.environ.pop(k, None)
        # 独立 config.yaml 副本：与生产默认同形（enabled false / 5 / 30 / 0.7）
        cfg = TMP / "config.yaml"
        cfg.write_text("laya_link:\n  enabled: false\n  host: 127.0.0.1\n  port: 59999\n"
                       "  timeout_ms: 200\n  fuse_threshold: 5\n  fuse_duration_s: 30\n"
                       "  fuse_max_s: 600\n  confidence_threshold: 0.7\n", encoding="utf-8")
        os.environ["MORAY_LAYA_CONFIG"] = str(cfg)
        conn.reset_runtime_state()

    def tearDown(self):
        for k in ("MORAY_LAYA_CONFIG", "MORAY_LAYA_DIRECTIVES", "MORAY_LAYA_LINK",
                  "MORAY_LAYA_FUSE_THRESHOLD", "MORAY_LAYA_FUSE_DURATION_S",
                  "MORAY_LAYA_FUSE_MAX_S", "MORAY_LAYA_CONFIDENCE"):
            os.environ.pop(k, None)
        conn.reset_runtime_state()

    def _enable(self, **env):
        os.environ["MORAY_LAYA_LINK"] = "1"
        for k, v in env.items():
            os.environ[k] = str(v)
        conn.reset_runtime_state()

    def _task(self, tid="t1", ttype="fix", title="修复问题"):
        return {"id": tid, "type": ttype, "title": title}

    def _info(self, task, ttype):
        return {"id": task["id"], "type": ttype, "title": task["title"], "status": "failed",
                "summary": "", "duration_ms": 0}

    def _params(self, ttype):
        p = {"project_dir": str(TMP), "project_id": "P"}
        if ttype == "fix":
            p["error_text"] = "boom"
            p["verify_cmd"] = "py"
        return p

    def _bypass(self, ttype, task, run, params, info, fac, handler):
        orig = httpx.AsyncClient
        httpx.AsyncClient = _mk(handler)
        try:
            return asyncio.run(conn.bypass(ttype, task, run, "P", str(TMP), params, info,
                                           fac, es.ExecutionScheduler._summarize_progress))
        finally:
            restore(orig)


class GateAndRoutesTest(ConnectorBase):
    def test_gate_off_no_http(self):
        """铁则3：总闸关 → bypass False、零 HTTP、零留痕。"""
        called = {"n": 0}

        def handler(url, body):
            called["n"] += 1
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow"))
        run = FakeRun()
        task = self._task()
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)
        self.assertEqual(called["n"], 0)
        self.assertEqual(run.decisions, [])

    def test_route_template_contract_fields_and_trace(self):
        """走模板：runner 执行、decision 全键留痕；评审 一.1 请求新字段齐备。"""
        seen_body = {}

        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            seen_body.update(body)
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow",
                                     decision_id="layadec1", is_fast_path=True,
                                     confidence=0.99))
        self._enable()
        run = FakeRun()
        task = self._task()
        info = self._info(task, "fix")
        got = {}

        def fac(t, p, w):
            got["tpl"] = t
            return FakeRunner(t, p, w)
        ok = self._bypass("fix", task, run, self._params("fix"), info, fac, handler)
        self.assertTrue(ok)
        self.assertEqual(got["tpl"], "text_fix_flow")
        self.assertEqual(info["laya"]["decision_id"], "layadec1")
        self.assertEqual(info["laya"]["rule_version"], "l75-1.1")
        self.assertEqual(info["laya"]["model_version"], "moray-laya-final")
        self.assertTrue(any(d["type"] == "laya-route" for d in run.decisions))
        # 评审 一.1：下行请求新契约字段
        self.assertIn("confidence_threshold", seen_body)
        self.assertAlmostEqual(float(seen_body["confidence_threshold"]), 0.7, places=2)
        self.assertEqual(int(seen_body["x_hop_count"]), 0)
        self.assertIsInstance(seen_body["context_snapshot"], dict)
        self.assertIn("last_subtask", seen_body["context_snapshot"])
        # 评审 一.2：响应新键落留痕
        for k in ("confidence", "is_fast_path", "fallback_reason", "rule_version",
                  "model_version", "latency_ms"):
            self.assertIn(k, info["laya"], "info[laya] 缺契约键 " + k)

    def test_route_cache_adopt_result(self):
        """走缓存：cache_hit 采纳 result_data（is_fast_path=True），不跑 runner。"""
        def handler(url, body):
            return FakeResp(envelope(route_result="cache", cache_hit=True,
                                     template_name="code_review_flow", decision_id="dc",
                                     is_fast_path=True, confidence=0.95,
                                     result_data={"status": "ok", "summary": "历史：审查通过"}))
        self._enable()
        run = FakeRun()
        task = self._task(ttype="review")
        info = self._info(task, "review")
        ran = {"n": 0}

        def fac(t, p, w):
            ran["n"] += 1
            return FakeRunner(t, p, w)
        ok = self._bypass("review", task, run, {"project_id": "P"}, info, fac, handler)
        self.assertTrue(ok)
        self.assertEqual(ran["n"], 0, "缓存命中绝不重跑模板")
        self.assertEqual(info["summary"], "历史：审查通过")
        self.assertTrue(info["laya"]["is_fast_path"])
        self.assertTrue(any(d["type"] == "laya-cache" for d in run.decisions))

    def test_route_native_falls_through(self):
        def handler(url, body):
            return FakeResp(envelope(fallback_reason="local_action_native"))
        self._enable()
        run = FakeRun()
        task = self._task()
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)

    def test_high_risk_forces_native_not_fast_path(self):
        """评审 二.3：risk=high → 快速通路禁用、强制原生、留痕注明权限管控。"""
        def handler(url, body):
            return FakeResp(envelope(risk_level="high", is_fast_path=False,
                                     fallback_reason="high_risk", decision_id="dh"))
        self._enable()
        run = FakeRun()
        task = self._task()
        info = self._info(task, "fix")
        ok = self._bypass("fix", task, run, self._params("fix"), info,
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)
        self.assertIs(False, info["laya"]["is_fast_path"], "高风险必须非快速通路")
        self.assertTrue(any(d["type"] == "laya" and "权限管控" in d["reason"]
                             for d in run.decisions))


class ContractAndConfidenceTest(ConnectorBase):
    def test_contract_missing_key_degrades(self):
        """评审 一.3：响应缺必填键 → 契约故障：降级原生 + 计入熔断连败 + contract_fails 计数。"""
        bad = envelope(route_result="template", template_name="text_fix_flow")
        bad.pop("rule_version")

        def handler(url, body):
            return FakeResp(bad)
        self._enable()
        run = FakeRun()
        task = self._task("tc")
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)
        self.assertEqual(conn.stats()["contract_fails"], 1)
        self.assertEqual(conn.fuse_state()["fails"], 1, "契约故障应计入连败")

    def test_confidence_below_threshold_rejected(self):
        """评审 一.1：confidence < confidence_threshold(0.7) 的模板决策不采纳。"""
        def handler(url, body):
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow",
                                     confidence=0.42, is_fast_path=True))
        self._enable()
        run = FakeRun()
        task = self._task("tq")
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)
        self.assertEqual(conn.stats()["low_confidence"], 1)
        self.assertTrue(any(d["type"] == "laya-lowconf" for d in run.decisions))

    def test_threshold_overridable_by_env(self):
        """门槛可配：MORAY_LAYA_CONFIDENCE=0.4 时 0.42 的决策应被采纳。"""
        def handler(url, body):
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow",
                                     confidence=0.42, is_fast_path=True))
        self._enable(MORAY_LAYA_CONFIDENCE=0.4)
        self.assertAlmostEqual(conn.cfg()["confidence_threshold"], 0.4, places=2)
        run = FakeRun()
        task = self._task("tq2")
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertTrue(ok)


class GradedFuseTest(ConnectorBase):
    """评审 二.2 分级熔断：单次降级留日志 → 连5临时熔断30s → 到期半开试探 → 失败翻倍。"""

    def _failing_handler(self, calls):
        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            calls["n"] += 1
            raise httpx.ConnectError("down")
        return handler

    def _one(self, i, handler):
        run = FakeRun("gf%d" % i)
        task = self._task("g%d" % i, title="修%d" % i)   # 标题各异，hop 不干扰熔断用例
        return self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                            lambda *a: FakeRunner(*a), handler), run

    def test_single_fail_then_5_opens_fuse(self):
        calls = {"n": 0}
        self._enable()
        for i in range(4):
            ok, _ = self._one(i, self._failing_handler(calls))
            self.assertFalse(ok)
        self.assertEqual(calls["n"], 4)
        self.assertEqual(conn.fuse_state()["phase"], "closed", "4 连败未达阈值5，不应熔断")
        self.assertEqual(conn.stats()["single_fallbacks"], 4, "每次异常都是单次降级各留一条日志")
        ok, run5 = self._one(5, self._failing_handler(calls))
        self.assertFalse(ok)
        fs = conn.fuse_state()
        self.assertEqual(fs["phase"], "open")
        self.assertEqual(fs["window_s"], 30, "首次熔断时长 30s（评审 二.2）")
        self.assertGreater(fs["remain_s"], 25)
        # 熔期内：第 6 次不再发起 HTTP
        ok6, run6 = self._one(6, self._failing_handler(calls))
        self.assertFalse(ok6)
        self.assertEqual(calls["n"], 5, "熔断期内不得再建连")
        self.assertTrue(any(d["type"] == "laya-fuse" for d in run6.decisions))

    def test_probe_fail_extends_and_probe_ok_recovers(self):
        calls = {"n": 0}
        self._enable()
        for i in range(5):
            self._one(i, self._failing_handler(calls))
        self.assertEqual(conn.fuse_state()["phase"], "open")
        # 模拟熔断到期：进入半开 → 试探失败 → 时长翻倍
        conn._fuse["open_until"] = time.monotonic() - 0.01
        ok, _ = self._one(90, self._failing_handler(calls))
        self.assertFalse(ok)
        fs = conn.fuse_state()
        self.assertEqual(fs["phase"], "open", "试探失败 → 重新熔断")
        self.assertEqual(fs["window_s"], 60, "试探失败熔断时长翻倍 30→60（评审 二.2）")
        self.assertEqual(conn.stats()["fuse_probe_fails"], 1)
        # 再次到期 → 试探成功 → 恢复
        calls2 = {"n": 0}

        def good(url, body):
            calls2["n"] += 1
            return FakeResp(envelope(route_result="native", fallback_reason="local_action_native"))
        conn._fuse["open_until"] = time.monotonic() - 0.01
        ok, _ = self._one(91, good)
        self.assertFalse(ok)                      # native 决策本身走原生
        self.assertEqual(calls2["n"], 1, "半开应放行一次试探请求")
        fs = conn.fuse_state()
        self.assertEqual(fs["phase"], "closed")
        self.assertEqual(fs["fails"], 0)
        self.assertIsNone(fs["window_s"], "恢复后窗口回落基值")
        self.assertEqual(conn.stats()["fuse_probe_ok"], 1)

    def test_fuse_cap_at_max(self):
        """延长封顶 fuse_max_s：翻倍不超过上限。"""
        calls = {"n": 0}
        self._enable(MORAY_LAYA_FUSE_DURATION_S=8, MORAY_LAYA_FUSE_MAX_S=10)
        for i in range(5):
            self._one(100 + i, self._failing_handler(calls))
        conn._fuse["open_until"] = time.monotonic() - 0.01
        self._one(110, self._failing_handler(calls))
        self.assertEqual(conn.fuse_state()["window_s"], 10, "翻倍被封顶在 fuse_max_s")


class DedupAndHopTest(ConnectorBase):
    def test_idempotent_dedup(self):
        """幂等：同 (plan,task) 只调度一次；重试走原生不再打 Laya。"""
        calls = {"n": 0}

        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            calls["n"] += 1
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow",
                                     decision_id="d1", is_fast_path=True))
        self._enable()
        run = FakeRun("plSame")
        task = self._task("tSame", title="同一题")
        p = self._params("fix")
        self._bypass("fix", task, run, p, self._info(task, "fix"),
                     lambda *a: FakeRunner(*a), handler)
        ok2 = self._bypass("fix", task, run, p, self._info(task, "fix"),
                           lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok2)
        self.assertEqual(calls["n"], 1, "第二次不得再打 Laya dispatch")
        self.assertTrue(any(d["type"] == "laya-dedup" for d in run.decisions))

    def test_hop_limit_blocks_third_pass_same_content(self):
        """评审 二.1 防循环：同内容穿过 Laya 达 2 次 → 第 3 次强制原生（跨计划也拦）。"""
        calls = {"n": 0}

        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            calls["n"] += 1
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow",
                                     decision_id="h%d" % calls["n"], is_fast_path=True))
        self._enable()
        task = {"id": "tk", "type": "fix", "title": "逐字相同的题面"}
        p = self._params("fix")
        o1 = self._bypass("fix", dict(task), FakeRun("hopA"), p, self._info(dict(task), "fix"),
                          lambda *a: FakeRunner(*a), handler)
        o2 = self._bypass("fix", dict(task), FakeRun("hopB"), p, self._info(dict(task), "fix"),
                          lambda *a: FakeRunner(*a), handler)
        runC = FakeRun("hopC")
        o3 = self._bypass("fix", dict(task), runC, p, self._info(dict(task), "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertTrue(o1 and o2, "前两次穿过允许")
        self.assertFalse(o3, "第三次被防循环硬拦")
        self.assertEqual(calls["n"], 2, "第 3 次不发起 HTTP")
        self.assertTrue(any(d["type"] == "laya-hop" for d in runC.decisions))

    def test_hop_count_sent_in_body(self):
        """x_hop_count 上送值 = 该内容已穿过 Laya 的次数。"""
        bodies = []

        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            bodies.append(dict(body))
            return FakeResp(envelope(route_result="native",
                                     fallback_reason="local_action_native"))
        self._enable()
        task = {"id": "xh", "type": "fix", "title": "计数题"}
        p = self._params("fix")
        self._bypass("fix", task, FakeRun("hopX1"), p, self._info(task, "fix"),
                     lambda *a: FakeRunner(*a), handler)
        task2 = {"id": "xh2", "type": "fix", "title": "计数题"}   # 内容同、任务 id 不同
        self._bypass("fix", task2, FakeRun("hopX2"), p, self._info(task2, "fix"),
                     lambda *a: FakeRunner(*a), handler)
        self.assertEqual(int(bodies[0]["x_hop_count"]), 0)
        self.assertEqual(int(bodies[1]["x_hop_count"]), 1)


class DirectiveTest(ConnectorBase):
    def test_force_native_directive(self):
        conn.apply_directive("plD", "force_native", reason="高风险止损")
        calls = {"n": 0}

        def handler(url, body):
            calls["n"] += 1
            return FakeResp(envelope(route_result="template", template_name="text_fix_flow"))
        self._enable()
        run = FakeRun("plD")
        task = self._task()
        ok = self._bypass("fix", task, run, self._params("fix"), self._info(task, "fix"),
                          lambda *a: FakeRunner(*a), handler)
        self.assertFalse(ok)
        self.assertEqual(calls["n"], 0, "force_native 指令下不打 Laya")
        self.assertTrue(any(d["type"] == "laya-directive" for d in run.decisions))

    def test_skip_task_directive(self):
        conn.apply_directive("plS", "skip_task", task_id="tS", reason="高危任务跳过")
        self._enable()

        def handler(url, body):
            raise AssertionError("skip 不该打 HTTP")
        run = FakeRun("plS")
        task = self._task("tS")
        info = self._info(task, "fix")
        ok = self._bypass("fix", task, run, self._params("fix"), info,
                          lambda *a: FakeRunner(*a), handler)
        self.assertTrue(ok)
        self.assertEqual(info["status"], "skipped")
        self.assertEqual([d for d in conn.pending_directives()
                          if d.get("task_id") == "tS"], [], "skip 指令一次性消费")


class SchedulerIntegrationTest(ConnectorBase):
    def test_scheduler_single_line_hook(self):
        """调度器单行接入：开闸经 Laya 路由；关闸原生映射逐字节照旧。"""
        def handler(url, body):
            if "/outcome" in url:
                return FakeResp({"code": 0, "stored": True})
            ttype = body["task_type"]
            tpl = {"review": "code_review_flow", "test": "test_run_flow",
                   "fix": "text_fix_flow"}[ttype]
            return FakeResp(envelope(route_result="template", template_name=tpl,
                                     decision_id="s_" + ttype, is_fast_path=True))
        seen = []

        def factory(tpl, params, wd):
            seen.append(tpl)
            return FakeRunner(tpl, params, wd)

        async def _drive():
            sched = es.ExecutionScheduler(runner_factory=factory)
            plan = {"plan_id": "plSched", "goal": "g", "source": "rule",
                    "context": {"project_id": "P", "project_dir": str(TMP)},
                    "tasks": [{"id": "a", "type": "review", "title": "审查", "deps": []},
                              {"id": "b", "type": "test", "title": "测试", "deps": []}]}
            return await sched.run_plan(plan)

        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "es.json")
        orig = httpx.AsyncClient
        httpx.AsyncClient = _mk(handler)
        try:
            self._enable()
            run_on = asyncio.run(_drive())
            gate_on_tpls = list(seen)
            gate_on_dec = [d["type"] for d in run_on.decisions]
            seen.clear()
            os.environ.pop("MORAY_LAYA_LINK", None)
            conn.reset_runtime_state()
            asyncio.run(_drive())
            gate_off_tpls = list(seen)
        finally:
            restore(orig)
            os.environ.pop("MORAY_EXPERIENCE_DB", None)
            os.environ.pop("MORAY_EXPERIENCE_STATS", None)
        self.assertEqual(gate_on_tpls, ["code_review_flow", "test_run_flow"],
                         "开闸：经 Laya 路由执行两模板")
        self.assertIn("laya-route", gate_on_dec)
        self.assertEqual(gate_off_tpls, ["code_review_flow", "test_run_flow"],
                         "关闸：原生映射逐字节照旧（能力全继承）")


class DefaultsSanityTest(ConnectorBase):
    def test_config_defaults_match_review(self):
        """评审补充默认值落地核对：5次/30s/封顶600/置信0.7。"""
        c = conn.cfg()
        self.assertEqual(c["fuse_threshold"], 5)
        self.assertEqual(c["fuse_duration_s"], 30)
        self.assertEqual(c["fuse_max_s"], 600)
        self.assertAlmostEqual(c["confidence_threshold"], 0.7, places=2)
        self.assertEqual(conn.MAX_HOPS, 2)


if __name__ == "__main__":
    unittest.main()
