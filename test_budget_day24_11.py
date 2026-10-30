# -*- coding: utf-8 -*-
"""Day24.11 预算机制修复专项测试（批次二·5 任务3 重写：适配本地优先架构）
================================
配套改动（不改 test_budget.py 的既有 5 项，故单独一个文件）：

  A. 前端带 session_id ——「按对话隔离」在 HTTP 路径上的行为（conv:a / conv:b 互不影响）；
  B. 按天重置 —— 跨自然日惰性清零（含"清零后可继续调用"的端到端断言）；
  C. default 桶可观测 —— 不带 session_id 时的节流告警 + 启动报告残留清零。

【本地优先】[批次二·5] 上游 = 本机 Ollama（假 httpx 客户端，含健康检查 GET 替身），
预算语义与 provider 无关、全部保留；绝不发任何真实网络请求（含云端）。

运行方式：D:\\python3.12\\python.exe -X utf8 test_budget_day24_11.py
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离：独立工作区/库 + 钉死上限（必须在导入 config 之前设置）----
TEST_DIR = PROJECT_ROOT / "test_budget_day2411_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "test_budget_day2411.db")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "3"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import config, db, llm_proxy

db.init_db()
LIMIT = config.MAX_LLM_CALLS_PER_SESSION


class _HealthResponse:
    """本地上游健康检查替身（llm_proxy._local_health_ok 的 GET /api/version）"""

    status_code = 200


class _FakeResponse:
    status_code = 200

    def __init__(self):
        self._data = {
            "choices": [{"message": {"role": "assistant", "content": "mock-reply"}}],
            "model": "mock-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


class _FakeAsyncClient:
    """httpx.AsyncClient 替身。
    [批次二·5] 补 get()：本地优先重构后 llm_chat 先做 Ollama 存活预检（GET /api/version）。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return _HealthResponse()

    async def post(self, url, json=None, headers=None):
        return _FakeResponse()

    def build_request(self, method, url, **kwargs):
        return (method, url)

    async def send(self, req, stream=False):
        return _FakeResponse()

    async def aclose(self):
        pass


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(llm_proxy.router)
    return app


def _body(session_id=None):
    b = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
    if session_id is not None:
        b["session_id"] = session_id
    return b


class TestBudgetIsolationByConversation(unittest.TestCase):
    """改动 A：按对话隔离（前端发的 conv:<id> 走的就是这条路径）"""

    @classmethod
    def setUpClass(cls):
        # [本地优先] 显式钉 provider=ollama：路由本机 Ollama（假上游），确定性不受
        # 开发机 .env 的 MORAY_LLM_PROVIDER 影响。
        cls._orig_provider = config.LLM_PROVIDER
        config.LLM_PROVIDER = "ollama"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeAsyncClient)
        cls._patcher.start()
        cls.client = TestClient(_build_app())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        config.LLM_PROVIDER = cls._orig_provider

    def setUp(self):
        llm_proxy._session_calls.clear()
        llm_proxy._budget_day = ""
        llm_proxy._default_bucket_warned = False

    def test_conversation_buckets_are_isolated(self):
        """conv:A 打满并超限后，conv:B 仍是全新额度（验收 2）"""
        for i in range(1, LIMIT + 1):
            r = self.client.post("/api/llm/chat", json=_body("conv:A"))
            self.assertEqual(r.status_code, 200, f"conv:A 第 {i} 次应 200")
        blocked = self.client.post("/api/llm/chat", json=_body("conv:A"))
        self.assertEqual(blocked.status_code, 429, "conv:A 第 LIMIT+1 次应 429")
        self.assertEqual(blocked.json().get("error"), "session_budget_exceeded")
        for i in range(1, LIMIT + 1):
            r = self.client.post("/api/llm/chat", json=_body("conv:B"))
            self.assertEqual(r.status_code, 200, f"conv:A 超限后 conv:B 第 {i} 次仍应 200")
        self.assertEqual(llm_proxy._session_calls["conv:A"], LIMIT + 1)
        self.assertEqual(llm_proxy._session_calls["conv:B"], LIMIT)

    def test_same_conversation_accumulates(self):
        """同一对话内多轮问答累计计数（验收 3）"""
        for i in range(1, LIMIT + 3):
            self.client.post("/api/llm/chat", json=_body("conv:acc"))
        self.assertEqual(llm_proxy._session_calls["conv:acc"], LIMIT + 2,
                         "被拒请求也应继续累计（原有语义不变）")

    def test_conv_prefix_does_not_collide_with_argus_sid(self):
        """前缀让对话桶与 Argus 研究任务桶彻底分开（两套 id 同值时也不撞桶）"""
        sid = "abc123"
        self.client.post("/api/llm/chat", json=_body(sid))
        self.client.post("/api/llm/chat", json=_body("conv:" + sid))
        self.assertEqual(llm_proxy._session_calls[sid], 1)
        self.assertEqual(llm_proxy._session_calls["conv:" + sid], 1)

    def test_missing_session_id_still_falls_back_to_default(self):
        """向后兼容：不带 session_id 的行为与改动前完全一致（验收：无对话 ID 时仍可用）"""
        r = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(llm_proxy._session_calls.get("default"), 1)

    def test_default_bucket_warns_once_per_process(self):
        """改动 C：不带 session_id 时告警一次（节流），带上后不再告警"""
        self.client.post("/api/llm/chat", json=_body())
        self.assertTrue(llm_proxy._default_bucket_warned, "落入 default 桶应置位告警标志")
        llm_proxy._default_bucket_warned = False
        self.client.post("/api/llm/chat", json=_body("conv:warn"))
        self.assertFalse(llm_proxy._default_bucket_warned,
                         "带了 session_id 的请求不应触发 default 桶告警")


class TestBudgetDailyReset(unittest.TestCase):
    """改动 B：按自然日惰性重置"""

    @classmethod
    def setUpClass(cls):
        cls._orig_provider = config.LLM_PROVIDER
        config.LLM_PROVIDER = "ollama"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeAsyncClient)
        cls._patcher.start()
        cls.client = TestClient(_build_app())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        config.LLM_PROVIDER = cls._orig_provider

    def setUp(self):
        llm_proxy._session_calls.clear()
        llm_proxy._budget_day = ""
        llm_proxy._default_bucket_warned = False

    def test_same_day_keeps_counting(self):
        """同一天内不重置：打满后仍被拒"""
        for _ in range(LIMIT + 1):
            self.client.post("/api/llm/chat", json=_body("conv:day"))
        self.assertEqual(self.client.post("/api/llm/chat", json=_body("conv:day")).status_code, 429)

    def test_cross_day_reset_unblocks_exhausted_bucket(self):
        """跨日重置：已超限的桶在"次日"首个请求时被清零，可继续调用（验收 5）"""
        for _ in range(LIMIT + 2):
            self.client.post("/api/llm/chat", json=_body("conv:day"))
        self.assertGreater(llm_proxy._session_calls["conv:day"], LIMIT)

        llm_proxy._budget_day = "2000-01-01"  # 伪造"上一个自然日"
        r = self.client.post("/api/llm/chat", json=_body("conv:day"))
        self.assertEqual(r.status_code, 200, "跨日后应清零并放行")
        self.assertEqual(llm_proxy._session_calls, {"conv:day": 1}, "跨日应清空全部桶后重新计数")

    def test_reset_is_lazy_and_needs_no_timer(self):
        """惰性实现：仅靠一次请求就完成跨日判定，无定时器/后台任务参与"""
        llm_proxy._session_calls["conv:x"] = 9
        llm_proxy._budget_day = "2000-01-01"
        llm_proxy._roll_budget_day()
        self.assertEqual(llm_proxy._session_calls, {})
        self.assertEqual(llm_proxy._budget_day, llm_proxy.time.strftime("%Y-%m-%d"))


class TestStartupBudgetNote(unittest.TestCase):
    """改动 C：启动报告（如实报告，不伪造残留数字）"""

    def setUp(self):
        llm_proxy._session_calls.clear()
        llm_proxy._default_bucket_warned = True

    def test_residue_cleared_and_flag_reset(self):
        llm_proxy._session_calls["default"] = 104
        llm_proxy._session_calls["conv:y"] = 7
        llm_proxy.startup_budget_note()
        self.assertEqual(llm_proxy._session_calls, {}, "启动报告应清掉任何残留计数")
        self.assertFalse(llm_proxy._default_bucket_warned, "启动应复位节流标志（新进程重新可告警）")

    def test_fresh_process_reports_zero_residue(self):
        """新进程里残留恒为 0——接口存在的意义是把"计数已就绪 + 策略"打到控制台"""
        llm_proxy._session_calls.clear()
        llm_proxy.startup_budget_note()
        self.assertEqual(llm_proxy._session_calls, {})
        self.assertEqual(sum(llm_proxy._session_calls.values()), 0)


def run_tests():
    print("=" * 62)
    print("MoRay 预算机制修复专项测试（Day24.11 · 本地优先）")
    print("=" * 62)
    print(f"上限 MAX_LLM_CALLS_PER_SESSION = {LIMIT}")
    print(f"本地上游: {config.OLLAMA_BASE_URL}/v1（假客户端，零真实网络请求）")
    print(f"测试数据库: {config.SQLITE_PATH}")
    print("=" * 62)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in (TestBudgetIsolationByConversation, TestBudgetDailyReset, TestStartupBudgetNote):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    print()
    print("=" * 62)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！")
    else:
        print(f"❌ 失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
    print("=" * 62)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
