"""
MoRay 单 session LLM 调用次数熔断自动化测试（国庆冲刺 Day1；
批次二·5 任务3 重写：适配 2026-09-30「本地优先强制」架构）
============================================================
验证 server/app/llm_proxy.py 的 /api/llm/chat 内存级熔断：
  1. 同一 session 前 30 次正常返回，第 31 次返回 429（body.error = session_budget_exceeded）；
  2. 不同 session_id 互不影响（A 超限不影响 B）；
  3. session_id 来源：请求体字段 > X-Session-Id 头 > "default" 兜底。

【本地优先】[批次二·5] 上游 = 本机 Ollama（假 httpx 客户端，含健康检查 GET 替身），
熔断语义与 provider 无关、全部保留；绝不发任何真实网络请求（含云端）。

运行方式：D:\\python3.12\\python.exe -X utf8 test_budget.py
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

# 确保能导入 server.app 模块
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离：临时工作区/数据库 + 显式钉死熔断上限（必须在导入 config 之前设置）----
TEST_DIR = PROJECT_ROOT / "test_budget_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "test_budget.db")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import config, db, llm_proxy

# 审计日志（budget_exceeded 记录）落表需要 schema；写的是上面的临时库，不碰用户主库
db.init_db()

LIMIT = config.MAX_LLM_CALLS_PER_SESSION


# ---------------------------------------------------------------- 假上游
class _HealthResponse:
    """本地上游健康检查替身（llm_proxy._local_health_ok 的 GET /api/version）"""

    status_code = 200


class _FakeResponse:
    """非流式响应替身：永远 200，返回最小 OpenAI 兼容结构"""
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
    """httpx.AsyncClient 替身：只覆盖 llm_proxy 用到的接口，绝不产生真实连接。
    注意：patch 的是 AsyncClient；TestClient 走的是同步 httpx.Client，不受影响。
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
    """只挂 llm_proxy 路由的最小应用（不启动整站，避免静态托管等无关依赖）"""
    app = FastAPI()
    app.include_router(llm_proxy.router)
    return app


def _chat_body(session_id=None):
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
    if session_id is not None:
        body["session_id"] = session_id
    return body


class TestSessionBudget(unittest.TestCase):
    """单 session LLM 调用熔断"""

    @classmethod
    def setUpClass(cls):
        # [本地优先] 显式钉 provider=ollama：路由本机 Ollama（假上游），确定性不受
        # 开发机 .env 的 MORAY_LLM_PROVIDER 影响；云端分支全程不可达（闸门关闭）。
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
        llm_proxy._session_calls.clear()  # 用例间互不残留计数

    def _assert_ok(self, resp, note):
        self.assertEqual(resp.status_code, 200, f"{note}：应正常返回 200，实得 {resp.status_code}")
        self.assertTrue(resp.json().get("ok"), f"{note}：body.ok 应为 True")

    def _assert_blocked(self, resp, note):
        self.assertEqual(resp.status_code, 429, f"{note}：应返回 429，实得 {resp.status_code}")
        data = resp.json()
        self.assertFalse(data.get("ok"), f"{note}：body.ok 应为 False")
        self.assertEqual(data.get("error"), "session_budget_exceeded",
                         f"{note}：error 字段应为 session_budget_exceeded，实得 {data.get('error')}")
        self.assertIn(str(LIMIT), data.get("msg", ""),
                      f"{note}：msg 应包含上限次数 {LIMIT}，实得 {data.get('msg')}")
        # [Day24.11b] 新增人话字段 message：老字段 error/msg 保留（向后兼容），前端代理分支优先读它
        self.assertIn(str(LIMIT), data.get("message", ""),
                      f"{note}：message 应包含上限次数 {LIMIT}，实得 {data.get('message')}")
        self.assertIn("请新建对话", data.get("message", ""),
                      f"{note}：message 应含可执行指引（请新建对话），实得 {data.get('message')}")

    # ---- 4.1 前 30 次正常、第 31 次 429 ----
    def test_first_limit_pass_then_429(self):
        for i in range(1, LIMIT + 1):
            self._assert_ok(self.client.post("/api/llm/chat", json=_chat_body("session-A")),
                            f"第 {i} 次调用")
        self._assert_blocked(self.client.post("/api/llm/chat", json=_chat_body("session-A")),
                             f"第 {LIMIT + 1} 次调用")

    # ---- 4.1 不同 session 互不影响 ----
    def test_sessions_are_isolated(self):
        for i in range(LIMIT + 2):  # 把 session-A 打到超限之后还多打一次
            self.client.post("/api/llm/chat", json=_chat_body("session-A"))
        self._assert_blocked(self.client.post("/api/llm/chat", json=_chat_body("session-A")),
                             "session-A 已超限")
        for i in range(1, LIMIT + 1):  # session-B 完全不受影响
            self._assert_ok(self.client.post("/api/llm/chat", json=_chat_body("session-B")),
                            f"session-B 第 {i} 次调用")
        self._assert_blocked(self.client.post("/api/llm/chat", json=_chat_body("session-B")),
                             "session-B 自己打满后也应超限")

    # ---- session_id 缺失 → 归入 default ----
    def test_missing_session_id_falls_back_to_default(self):
        for i in range(1, LIMIT + 1):
            self._assert_ok(self.client.post("/api/llm/chat", json=_chat_body()),
                            f"无 session_id 第 {i} 次调用")
        self._assert_blocked(self.client.post("/api/llm/chat", json=_chat_body()),
                             "无 session_id 第 " + str(LIMIT + 1) + " 次调用")
        self.assertIn("default", llm_proxy._session_calls,
                      "缺失 session_id 的请求应计入 'default' 桶")

    # ---- session_id 走 X-Session-Id 头 ----
    def test_session_id_from_header(self):
        self._assert_ok(self.client.post("/api/llm/chat", json=_chat_body(),
                                         headers={"X-Session-Id": "hdr-session"}),
                        "header 携带 session_id")
        self.assertIn("hdr-session", llm_proxy._session_calls,
                      "X-Session-Id 头应被识别为独立 session")
        self.assertNotIn("default", llm_proxy._session_calls,
                         "header 已给 session_id 时不应计入 default 桶")

    # ---- 超限也计数（熔断深度可观测）+ 只内存不落库 ----
    def test_exceeded_calls_keep_counting(self):
        for i in range(LIMIT + 3):
            self.client.post("/api/llm/chat", json=_chat_body("session-C"))
        self.assertEqual(llm_proxy._session_calls["session-C"], LIMIT + 3,
                         "被拒请求也应继续累计计数")


def run_tests():
    """运行所有测试"""
    print("=" * 60)
    print("MoRay 单 session LLM 调用熔断自动化测试（本地优先）")
    print("=" * 60)
    print(f"上限 MAX_LLM_CALLS_PER_SESSION = {LIMIT}")
    print(f"本地上游: {config.OLLAMA_BASE_URL}/v1（假客户端，零真实网络请求）")
    print(f"测试数据库: {config.SQLITE_PATH}")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestSessionBudget))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！熔断机制完好。")
    else:
        print(f"❌ 测试失败！共 {result.testsRun} 项，"
              f"失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
        if result.failures:
            print("\n失败的测试：")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if result.errors:
            print("\n错误的测试：")
            for test, traceback in result.errors:
                print(f"  - {test}")
    print("=" * 60)

    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
