# -*- coding: utf-8 -*-
"""账单修复三件套测试（2026-11-14）。

运行（cwd = 工程根）：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_cost_guard_20261114.py -v

覆盖：
  * 尾部注入（前缀缓存抢救）：事实卡/twin RAG 不再出现在 messages[0]，改为 messages
    **末尾**的独立 system 消息；连续两次请求（事实卡内容变化）messages[0] **字节级
    完全一致**——这是云端前缀缓存命中的硬前提；
  * 同 Session 工具请求频率限制：5 分钟窗口 >80 次带 tools 请求 → 429 + 指定文案；
    organ 回环豁免；纯聊天不计入；
  * Token 日上限自动降级：tokens_total 达 DAILY_MAX_TOKENS → 云端请求就地改写
    provider=ollama（上游收到的是本地模型），拦截事件落账；
  * 预算守卫：record_tokens 累计 / acquire 抛 TokenBudgetExceeded 且不置 tripped 闩锁 /
    token_budget_exceeded 判定。

零真实网络：上游 httpx.AsyncClient 整体替换为替身（test_pm_agent 同款惯例）。
"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_cost_guard_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "500"
os.environ.setdefault("MORAY_MEMORY_DB", os.path.join(TEST_DIR, "memory.sqlite3"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app import cloud_budget_guard as guard  # noqa: E402
from server.app import config, db, llm_proxy  # noqa: E402

db.init_db()

GOOD_REPLY = "本地模型回答。"
CLOUD_REPLY = "云端模型回答。"
FAKE_CLOUD_MODEL = "probe-cloud-model"


class _HealthResponse:
    status_code = 200

    def json(self):
        return {"status": "ok"}


class _JsonResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


def _upstream_payload(content, model):
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "model": model,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


class _StreamResponse:
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass

    def json(self):
        return {}

    @property
    def text(self):
        return ""


def _sse_chunks(text):
    out = ["data: " + json.dumps({"choices": [{"delta": {"content": text}}]},
                                 ensure_ascii=False) + "\n\n"]
    out.append('data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n')
    out.append("data: [DONE]\n\n")
    return [c.encode("utf-8") for c in out]


class _FakeClient:
    """上游 httpx.AsyncClient 替身：post 回非流式、send 回流式，payload 深拷贝入账。"""
    replies = [GOOD_REPLY]
    payloads = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return _HealthResponse()

    async def post(self, url, json=None, headers=None):
        type(self).payloads.append(copy.deepcopy(json))
        return _JsonResponse(_upstream_payload(type(self).replies[-1], FAKE_CLOUD_MODEL))

    def build_request(self, method, url, **kwargs):
        return {"url": url, "json": kwargs.get("json")}

    async def send(self, req, stream=False):
        type(self).payloads.append(copy.deepcopy((req or {}).get("json")))
        return _StreamResponse(_sse_chunks(type(self).replies[-1]))

    async def aclose(self):
        pass


def _upstream_payloads():
    """真实上游请求（带 messages 键）；MCP 网关 tools/list 噪声按形状过滤。"""
    return [p for p in _FakeClient.payloads
            if isinstance(p, dict) and "messages" in p]


class CostGuardBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {}
        for k in ("MORAY_TWIN_RAG", "MORAY_SELF_CARD", "MORAY_IDENTITY",
                  "MORAY_PM_AGENT", "DAILY_MAX_TOKENS", "MORAY_BUDGET_DB"):
            cls._saved[k] = os.environ.get(k)
        os.environ["MORAY_TWIN_RAG"] = "0"
        os.environ["MORAY_IDENTITY"] = "0"
        os.environ.pop("MORAY_PM_AGENT", None)
        os.environ["MORAY_SELF_CARD"] = "1"
        config.LLM_PROVIDER = "ollama"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeClient)
        cls._patcher.start()
        app = FastAPI()
        app.include_router(llm_proxy.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        for k, v in cls._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        _FakeClient.replies = [GOOD_REPLY]
        _FakeClient.payloads = []
        llm_proxy._session_calls.clear()
        llm_proxy._toolreq_times.clear()
        config.MORAY_BUDGET_DB = str(TEST_DIR / "budget_test.sqlite3")
        for f in TEST_DIR.glob("budget_test.sqlite3*"):
            f.unlink(missing_ok=True)


# ---------------------------------------------------------------- 段一：尾部注入
class TailInjectionTests(CostGuardBase):
    def test_01_card_at_tail_head_clean_and_byte_stable(self):
        """事实卡必须在 messages[-1]（system），messages[0] 干净；两次请求
        （卡内容变化）头部字节级一致——前缀缓存命中的硬前提。"""
        replies = {"n1": "状态一", "n2": "状态二"}
        with mock.patch("server.app.self_card.collect_db_state",
                        side_effect=[{"n": 1}, {"n": 2}]), \
             mock.patch("server.app.self_card.render_self_card",
                        side_effect=lambda s: replies["n" + ("1" if s["n"] == 1 else "2")]):
            r1 = self.client.post("/api/llm/chat", json={
                "messages": [{"role": "system", "content": "你是助手"},
                             {"role": "user", "content": "你好"}],
                "stream": False, "provider": "ollama", "session_id": "tail-1"})
            r2 = self.client.post("/api/llm/chat", json={
                "messages": [{"role": "system", "content": "你是助手"},
                             {"role": "user", "content": "你好"}],
                "stream": False, "provider": "ollama", "session_id": "tail-1"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(_upstream_payloads()), 2)
        m1 = _upstream_payloads()[0]["messages"]
        m2 = _upstream_payloads()[1]["messages"]
        # 头部干净：messages[0] 是用户 system，不含事实卡 marker
        self.assertEqual(m1[0]["role"], "system")
        self.assertEqual(m1[0]["content"], "你是助手")
        self.assertNotIn("系统事实卡", m1[0]["content"])
        # 尾部：最后一条 = system + 事实卡（含当次渲染内容）
        self.assertEqual(m1[-1]["role"], "system")
        self.assertIn("系统事实卡", m1[-1]["content"])
        self.assertIn("状态一", m1[-1]["content"])
        # 字节级稳定：两次请求的 messages[0] 逐字节相同（事实卡内容已变化）
        self.assertEqual(json.dumps(m1[0], ensure_ascii=False, sort_keys=True),
                         json.dumps(m2[0], ensure_ascii=False, sort_keys=True))
        self.assertNotEqual(m1[-1]["content"], m2[-1]["content"])
        # 除尾部事实卡外，前缀（system+user）也逐字节一致
        self.assertEqual(m1[:-1], m2[:-1])

    def test_02_no_head_reinsert_when_no_leading_system(self):
        """无前导 system 的请求同样只做尾部注入（旧逻辑会在 messages[0] 插新 system）。"""
        with mock.patch("server.app.self_card.collect_db_state", return_value={"n": 1}), \
             mock.patch("server.app.self_card.render_self_card", return_value="状态一"):
            self.client.post("/api/llm/chat", json={
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False, "provider": "ollama", "session_id": "tail-2"})
        m = _upstream_payloads()[0]["messages"]
        self.assertEqual(m[0]["role"], "user")          # 头部没有被插 system
        self.assertNotIn("系统事实卡", m[0]["content"])
        self.assertEqual(m[-1]["role"], "system")
        self.assertIn("系统事实卡", m[-1]["content"])


# ---------------------------------------------------------------- 段三：频率熔断
def _tool_body(session):
    return {"messages": [{"role": "user", "content": "任务"}],
            "stream": False, "provider": "ollama", "session_id": session,
            "tools": [{"type": "function", "function": {"name": "search_web"}}]}


class RateLimitTests(CostGuardBase):
    def test_10_81st_tool_request_gets_429(self):
        last = None
        for i in range(81):
            last = self.client.post("/api/llm/chat", json=_tool_body("loop-session"))
        self.assertEqual(last.status_code, 429)
        data = last.json()
        self.assertEqual(data.get("code"), "session_agent_loop_suspected")
        self.assertIn("疑似陷入长循环", data.get("message") or "")
        self.assertEqual(last.headers.get("x-should-retry"), "false")

    def test_11_window_slide_allows_again(self):
        for _ in range(81):
            self.client.post("/api/llm/chat", json=_tool_body("loop-session-2"))
        # 模拟窗口滑过：清空时间戳（等价于 5 分钟后）
        llm_proxy._toolreq_times["loop-session-2"].clear()
        r = self.client.post("/api/llm/chat", json=_tool_body("loop-session-2"))
        self.assertEqual(r.status_code, 200)

    def test_12_organ_callback_exempt(self):
        for _ in range(83):
            r = self.client.post("/api/llm/chat", json=_tool_body("organ-session"),
                                 headers={"X-Moray-Source": "organ"})
            self.assertEqual(r.status_code, 200)
        self.assertNotIn("organ-session", llm_proxy._toolreq_times)

    def test_13_plain_chat_not_counted(self):
        for _ in range(85):
            r = self.client.post("/api/llm/chat", json={
                "messages": [{"role": "user", "content": "纯聊天"}],
                "stream": False, "provider": "ollama", "session_id": "chat-session"})
            self.assertEqual(r.status_code, 200)
        self.assertNotIn("chat-session", llm_proxy._toolreq_times)

    def test_14_other_sessions_unaffected(self):
        for _ in range(81):
            self.client.post("/api/llm/chat", json=_tool_body("busy-session"))
        r = self.client.post("/api/llm/chat", json=_tool_body("quiet-session"))
        self.assertEqual(r.status_code, 200)


# ---------------------------------------------------------------- 段二：Token 上限降级
class TokenBudgetTests(CostGuardBase):
    def test_20_cloud_request_downgraded_to_local(self):
        """token 超限：X-Moray-Provider: cloud 的请求被就地改写 provider=ollama，
        上游收到的是本地模型——零云端调用。"""
        with mock.patch.object(guard, "token_budget_exceeded", return_value=True):
            r = self.client.post("/api/llm/chat", json={
                "messages": [{"role": "user", "content": "云端任务"}],
                "stream": False, "provider": "cloud", "model": "deepseek-flash",
                "session_id": "downgrade-1"},
                headers={"X-Moray-Provider": "cloud"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(_upstream_payloads()), 1)
        sent = _upstream_payloads()[0]
        # 关键断言：路由被改写为本地（模型 = 主本地模型），原云端模型名不再外发
        self.assertEqual(sent.get("model"), config.OLLAMA_MODEL)
        self.assertNotEqual(sent.get("model"), "deepseek-flash")

    def test_21_under_budget_routes_cloud(self):
        """未超限：云端请求照常走云端分支（改写不发生）。模型用白名单名（非白名单
        名会被 LLM_MODEL 兜底改写——既有语义，与本测试无关）。"""
        with mock.patch.object(guard, "token_budget_exceeded", return_value=False), \
                mock.patch.object(config, "ENABLE_CLOUD_API", True), \
                mock.patch.object(config, "LLM_BASE_URL", "https://fake.example/v1"), \
                mock.patch.object(config, "LLM_API_KEY", "sk-fake"):
            _FakeClient.replies = [CLOUD_REPLY]
            r = self.client.post("/api/llm/chat", json={
                "messages": [{"role": "user", "content": "云端任务"}],
                "stream": False, "provider": "cloud", "model": "deepseek-flash",
                "session_id": "downgrade-2"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_upstream_payloads()[0].get("model"), "deepseek-flash")

    def test_30_record_tokens_accumulates(self):
        guard.record_tokens(100, 50)
        guard.record_tokens(30, 20)
        state = guard.get_daily_state()
        self.assertEqual(state["tokens_total"], 200)

    def test_31_acquire_raises_token_budget_without_latch(self):
        # config.DAILY_MAX_TOKENS 在 import 期读 env 缓存——运行时须 patch 属性
        saved_enable = config.ENABLE_CLOUD_API
        config.ENABLE_CLOUD_API = True
        try:
            config.DAILY_MAX_TOKENS = 100
            guard.record_tokens(100, 50)     # 150 >= 100
            with self.assertRaises(guard.TokenBudgetExceeded):
                guard.acquire_explicit(trace_id="t-token-1", est_cost_usd=0.01)
            # 关键语义：token 超限**不置熔断闩锁**（次日自然恢复；金额/次数超限才闩死）
            self.assertFalse(guard.get_daily_state()["tripped"])
            # 判定函数同口径
            self.assertTrue(guard.token_budget_exceeded())
        finally:
            config.ENABLE_CLOUD_API = saved_enable
            config.DAILY_MAX_TOKENS = 2000000

    def test_32_zero_cap_disables_check(self):
        with mock.patch.object(config, "DAILY_MAX_TOKENS", 0):
            guard.record_tokens(10_000_000, 0)
            self.assertFalse(guard.token_budget_exceeded())   # 0 = 不限


if __name__ == "__main__":
    unittest.main()
