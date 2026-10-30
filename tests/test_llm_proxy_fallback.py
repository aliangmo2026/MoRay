# -*- coding: utf-8 -*-
"""llm_proxy 三层开关 / 云端预算闸门接线测试（任务二新增，2026-09-30）。

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_llm_proxy_fallback.py -v

要点：
  * 上游 = 假 httpx.AsyncClient（含健康检查 GET 替身），零真实网络；
  * L0 下本地失败 → 503 local_upstream_error，绝不触碰云端（测试 3 双重断言：URL 全本机 + acquire 未调）；
  * 显式 provider=cloud 且闸门通过 → 必须调用 acquire_explicit 预扣（mock 断言，测试 4）；
  * 不覆盖 test_budget.py / test_budget_day24_11.py 的任何行为（仅复用其隔离惯例）。
"""
import json
import os
import socket
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 与 test_cloud_budget_guard.py 共用同一隔离区 + 同一组 env（config 单例按首次导入读取，两侧必须一致）
TEST_DIR = PROJECT_ROOT / "test_cloud_budget_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MORAY_BUDGET_DB"] = str(TEST_DIR / "bootstrap_budget.sqlite3")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import cloud_budget_guard as guard
from server.app import config, db, llm_proxy

db.init_db()


# ---------------------------------------------------------------- 假上游（记录 URL）
class _HealthResponse:
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


class _RecordingAsyncClient:
    """httpx.AsyncClient 替身：记录全部请求 URL；raise_on_post=True 时模拟本地 Ollama 拒连。"""

    urls = []
    raise_on_post = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        type(self).urls.append(url)
        return _HealthResponse()

    async def post(self, url, json=None, headers=None):
        type(self).urls.append(url)
        if type(self).raise_on_post:
            raise httpx.ConnectError("connection refused (fake)")
        return _FakeResponse()

    def build_request(self, method, url, **kwargs):
        type(self).urls.append(url)
        return (method, url)

    async def send(self, req, stream=False):
        return _FakeResponse()

    async def aclose(self):
        pass


class _ErrorResponse:
    """[Z-3] 非 200 上游响应替身：json()/text() 双通道（_do_request 两条解析路径都碰）。"""

    def __init__(self, status_code, message="boom (fake)"):
        self.status_code = status_code
        self._payload = {"error": {"message": message}}

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class _UpstreamErrorAsyncClient(_RecordingAsyncClient):
    """[Z-3] POST 恒返回 status_code 的假上游（云端失败回冲测试用）；GET 健康检查继承 200。"""

    status_code = 500

    async def post(self, url, json=None, headers=None):
        type(self).urls.append(url)
        return _ErrorResponse(type(self).status_code)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(llm_proxy.router)
    return app


def _body(provider=None):
    b = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
    if provider:
        b["provider"] = provider
    return b


class LlmProxyFallbackTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_provider = config.LLM_PROVIDER
        config.LLM_PROVIDER = "ollama"  # 与 test_budget 同款：钉本地 provider，不受开发机 .env 影响
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _RecordingAsyncClient)
        cls._patcher.start()
        cls.client = TestClient(_build_app())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        config.LLM_PROVIDER = cls._orig_provider

    _FIELDS = ("ENABLE_CLOUD_API", "ENABLE_CLOUD_FALLBACK", "ENABLE_ZCODE_CLOUD",
               "DAILY_BUDGET_USD", "DAILY_MAX_CALLS", "PER_CALL_BUDGET_USD",
               "MORAY_BUDGET_DB", "LLM_BASE_URL", "LLM_API_KEY")

    def setUp(self):
        _RecordingAsyncClient.urls = []
        _RecordingAsyncClient.raise_on_post = False
        llm_proxy._session_calls.clear()
        self._saved = {name: getattr(config, name) for name in self._FIELDS}
        config.ENABLE_CLOUD_API = False
        config.ENABLE_CLOUD_FALLBACK = False
        config.ENABLE_ZCODE_CLOUD = False  # [Z-4] 默认关；test_13 显式开
        config.LLM_BASE_URL = ""
        config.LLM_API_KEY = ""
        self.budget_db = TEST_DIR / ("fb_%s.sqlite3" % uuid.uuid4().hex[:8])
        config.MORAY_BUDGET_DB = str(self.budget_db)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(self.budget_db) + suffix)
            if p.exists():
                p.unlink()


class CloudGateTests(LlmProxyFallbackTestBase):
    def test_01_l0_cloud_disabled_returns_503(self):
        config.ENABLE_CLOUD_API = False
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"   # 配置齐全，只关闸门
        config.LLM_API_KEY = "k"
        r = self.client.post("/api/llm/chat", json=_body("cloud"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "cloud_api_disabled")

    def test_02_cloud_api_on_but_base_url_empty_503(self):
        # llm_proxy 的一级配置检查只看 config.LLM_PROVIDER（请求体 provider 不触发该分支，
        # 属既有缺口、不在本任务改动点内）→ 本用例走全局 provider=cloud 的显式配置路径。
        orig = config.LLM_PROVIDER
        try:
            config.LLM_PROVIDER = "cloud"
            config.ENABLE_CLOUD_API = True
            config.LLM_BASE_URL = ""
            config.LLM_API_KEY = "k"
            r = self.client.post("/api/llm/chat", json=_body("cloud"))
        finally:
            config.LLM_PROVIDER = orig
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "cloud_not_configured")

    def test_03_l0_local_failure_never_falls_back_to_cloud(self):
        config.ENABLE_CLOUD_API = False
        _RecordingAsyncClient.raise_on_post = True
        r = self.client.post("/api/llm/chat", json=_body())  # provider 缺省 → ollama
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "local_upstream_error")
        hosts = {u.split("/")[2] for u in _RecordingAsyncClient.urls if "://" in u}
        self.assertTrue(hosts, "应至少发起过一次上游请求（假客户端）")
        self.assertTrue(all(h.startswith(("127.0.0.1", "localhost")) for h in hosts),
                        "上游必须全是本机，实得 %s" % hosts)
        with mock.patch.object(guard, "acquire_explicit") as spy:
            r2 = self.client.post("/api/llm/chat", json=_body())
            self.assertEqual(r2.status_code, 503)
            spy.assert_not_called()   # 本地失败路径绝不触碰云端预算闸门

    def test_04_explicit_cloud_invokes_acquire_explicit(self):
        config.ENABLE_CLOUD_API = True
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        permit = guard.CloudPermit(permit_id="p-test", reserved_usd=float(config.PER_CALL_BUDGET_USD),
                                   trace_id="t4")
        with mock.patch.object(guard, "acquire_explicit", return_value=permit) as spy:
            r = self.client.post("/api/llm/chat", json=_body("cloud"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        self.assertEqual(kwargs.get("est_cost_usd"), float(config.PER_CALL_BUDGET_USD),
                         "预扣额应=最坏情况 PER_CALL_BUDGET_USD")
        self.assertTrue(kwargs.get("trace_id"))

    def test_05_all_upstream_endpoints_local(self):
        config.ENABLE_CLOUD_API = True
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        with mock.patch.object(guard, "acquire_explicit",
                               return_value=guard.CloudPermit("p5", 0.1, "t5")):
            r_cloud = self.client.post("/api/llm/chat", json=_body("cloud"))
        r_local = self.client.post("/api/llm/chat", json=_body())  # ollama 成功路径
        self.assertEqual(r_cloud.status_code, 200)
        self.assertEqual(r_local.status_code, 200)
        for url in _RecordingAsyncClient.urls:
            if "://" in url:
                self.assertTrue(url.split("/")[2].startswith(("127.0.0.1", "localhost")),
                                "非本机 endpoint：%s" % url)

    def test_06_no_real_network(self):
        outside = []
        original_connect = socket.socket.connect

        def guarded(sock, address):
            if isinstance(address, tuple) and address and isinstance(address[0], str):
                if address[0] not in ("127.0.0.1", "::1", "localhost"):
                    outside.append(address)
            return original_connect(sock, address)

        with mock.patch.object(socket.socket, "connect", guarded):
            config.ENABLE_CLOUD_API = True
            config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
            config.LLM_API_KEY = "k"
            with mock.patch.object(guard, "acquire_explicit",
                                   return_value=guard.CloudPermit("p6", 0.1, "t6")):
                r1 = self.client.post("/api/llm/chat", json=_body("cloud"))
            r2 = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(outside, [], "全程不得出现非 127.0.0.1 出站")

    def test_07_body_provider_cloud_empty_base_url_503(self):
        # [任务二点五] 任务二 test_02 未覆盖的真实缺口路径：config.LLM_PROVIDER=ollama（类钉）
        # + body.provider="cloud"。修复后一级配置检查应直接命中 503 cloud_not_configured，
        # 且拒绝发生在任何 session 计数/预算预扣之前。
        config.ENABLE_CLOUD_API = True
        config.LLM_BASE_URL = ""
        config.LLM_API_KEY = ""
        with mock.patch.object(guard, "acquire_explicit") as spy:
            r = self.client.post("/api/llm/chat", json=_body("cloud"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "cloud_not_configured")
        spy.assert_not_called()
        self.assertEqual(llm_proxy._session_calls, {}, "被拒请求不得消耗 session 计数")


class HeaderProviderRefundTests(LlmProxyFallbackTestBase):
    """[2026-10-01 Z-3] X-Moray-Provider 头判定 + 云端失败路径 refund 接线。

    头判定优先级链最终形态：X-Moray-Provider 头（白名单 cloud/ollama/custom_27b 内）
    > body.provider > config.LLM_PROVIDER；白名单外的头值一律忽略。
    """

    def test_08_header_cloud_with_gate_disabled_503(self):
        # 头把 provider 拉成 cloud，但 ENABLE_CLOUD_API=false：一级预检闸门先拦
        #（body 里没有任何 provider，纯靠头驱动；拒绝发生在 acquire 之前）
        config.ENABLE_CLOUD_API = False
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        with mock.patch.object(guard, "acquire_explicit") as spy:
            r = self.client.post("/api/llm/chat", json=_body(),
                                 headers={"X-Moray-Provider": "cloud"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "cloud_api_disabled")
        spy.assert_not_called()

    def test_09_header_ollama_overrides_body_cloud(self):
        # 头 ollama > 体 provider=cloud：必须走本地假 Ollama，绝不触碰云端闸门/预算
        config.ENABLE_CLOUD_API = False
        with mock.patch.object(guard, "acquire_explicit") as spy:
            r = self.client.post("/api/llm/chat", json=_body("cloud"),
                                 headers={"X-Moray-Provider": "ollama"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        spy.assert_not_called()
        hosts = {u.split("/")[2] for u in _RecordingAsyncClient.urls if "://" in u}
        self.assertTrue(hosts, "应发起过本地上游请求（假客户端）")
        self.assertTrue(all(h.startswith(("127.0.0.1", "localhost")) for h in hosts),
                        "头覆盖后必须全走本机上游，实得 %s" % hosts)

    def test_10_header_illegal_value_falls_back_to_body(self):
        # 白名单外头值（"evil"）一律忽略 → 回落 body.provider="ollama" 走本地 200；
        # 若实现不过白名单，provider 会被拉成 "evil" 误入云端分支 → 503，两种实现可区分。
        config.ENABLE_CLOUD_API = False
        r = self.client.post("/api/llm/chat", json=_body("ollama"),
                             headers={"X-Moray-Provider": "evil"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))

    def test_11_cloud_upstream_error_triggers_refund(self):
        # 云端上游 500：失败响应返回前必须调 refund 全额回冲，reason 带上游状态码
        config.ENABLE_CLOUD_API = True
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        permit = guard.CloudPermit(permit_id="p-refund", reserved_usd=0.1, trace_id="t11")
        with mock.patch.object(llm_proxy.httpx, "AsyncClient", _UpstreamErrorAsyncClient), \
             mock.patch.object(guard, "acquire_explicit", return_value=permit), \
             mock.patch.object(guard, "refund") as refund_spy:
            r = self.client.post("/api/llm/chat", json=_body("cloud"))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json().get("code"), "upstream_error")
        refund_spy.assert_called_once()
        refunded_permit = refund_spy.call_args.args[0]
        self.assertEqual(refunded_permit.permit_id, "p-refund")
        self.assertEqual(refund_spy.call_args.kwargs.get("reason"),
                         "upstream_error_status_500")


class ZcodeCloudGateTests(LlmProxyFallbackTestBase):
    """[2026-10-01 Z-4] ENABLE_ZCODE_CLOUD 接线：只约束 header 触发的云端（ZCode 链路）。

    语义钉死：
      * header=cloud：总闸 ENABLE_CLOUD_API 放行后还要过 ENABLE_ZCODE_CLOUD
        （test_12 关→503 / test_13 开→走云端分支）；
      * body 显式 provider=cloud：前端手动路径，**不受** ENABLE_ZCODE_CLOUD 约束
        （test_14，与 test_12 构成唯一变量差分）；
      * 两处判定（一级预检 + 上游选择段）同源同语义，拒绝都发生在预算预扣之前。
    """

    def test_12_header_cloud_zcode_switch_off_503(self):
        config.ENABLE_CLOUD_API = True
        config.ENABLE_ZCODE_CLOUD = False
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        with mock.patch.object(guard, "acquire_explicit") as spy:
            r = self.client.post("/api/llm/chat", json=_body(),
                                 headers={"X-Moray-Provider": "cloud"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json().get("code"), "cloud_zcode_disabled")
        spy.assert_not_called()                            # 拒绝在预算预扣之前
        self.assertEqual(_RecordingAsyncClient.urls, [])   # 未发起任何上游请求
        self.assertEqual(llm_proxy._session_calls, {})     # 未消耗 session 计数

    def test_13_header_cloud_zcode_switch_on_goes_cloud(self):
        config.ENABLE_CLOUD_API = True
        config.ENABLE_ZCODE_CLOUD = True
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        permit = guard.CloudPermit(permit_id="p-z13",
                                   reserved_usd=float(config.PER_CALL_BUDGET_USD),
                                   trace_id="t13")
        with mock.patch.object(guard, "acquire_explicit", return_value=permit) as spy:
            r = self.client.post("/api/llm/chat", json=_body(),
                                 headers={"X-Moray-Provider": "cloud"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        spy.assert_called_once()                           # header 触发同样必须预扣预算
        post_urls = [u for u in _RecordingAsyncClient.urls
                     if "chat/completions" in str(u)]
        self.assertTrue(post_urls and all("127.0.0.1:9" in str(u) for u in post_urls),
                        "必须走云端上游（LLM_BASE_URL），实得 %s" % post_urls)

    def test_14_body_cloud_ignores_zcode_switch(self):
        config.ENABLE_CLOUD_API = True
        config.ENABLE_ZCODE_CLOUD = False                  # 开关关着也放行
        config.LLM_BASE_URL = "http://127.0.0.1:9/v1"
        config.LLM_API_KEY = "k"
        permit = guard.CloudPermit(permit_id="p-z14",
                                   reserved_usd=float(config.PER_CALL_BUDGET_USD),
                                   trace_id="t14")
        with mock.patch.object(guard, "acquire_explicit", return_value=permit) as spy:
            r = self.client.post("/api/llm/chat", json=_body("cloud"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get("ok"))
        spy.assert_called_once()
        post_urls = [u for u in _RecordingAsyncClient.urls
                     if "chat/completions" in str(u)]
        self.assertTrue(post_urls and all("127.0.0.1:9" in str(u) for u in post_urls),
                        "body 显式 cloud 必须照走云端上游，实得 %s" % post_urls)


if __name__ == "__main__":
    unittest.main()
