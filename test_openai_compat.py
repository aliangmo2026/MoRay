"""
MoRay OpenAI 兼容路由自动化测试（国庆冲刺 Day14 · 方案A，架构师批准；
批次二·5 任务3 重写：适配 2026-09-30「本地优先强制」架构）
============================================================
验证 server/app/llm_proxy.py 新增 POST /v1/chat/completions（纯协议转换层，
内调 llm_chat 复用熔断/模型路由/上游调用）：
  a) 非流式返回标准 OpenAI chat.completion（id/object/created/model/choices/usage，
     含 reasoning_content / tool_calls 映射；stream 缺省按 OpenAI 语义补 false）；
  b) 流式走 SSE delta（上游 chat.completion.chunk 逐字节透传 + [DONE]；
     本地上游错误时自有事件转 OpenAI error 事件，不泄 {ok:false} 形状）；
  c) 模型路由【本地优先语义】：provider=ollama 模型名原样透传（不做云端白名单兜底），
     缺省回落 config.OLLAMA_MODEL；provider=cloud 被 ENABLE_CLOUD_API 熔断闸门拦截；
  d) 预算熔断在新路由同样生效（429 + OpenAI error 形状），且与 /api/llm/chat
     共享同一 _session_calls 计数桶（X-Session-Id 头经 scope 透传）；
  e) /api/llm/chat 行为不变（回归：成功/参数错/熔断三种自有形状逐项断言）；
  f) 坏 JSON / 非对象 body / 云端未配置 → OpenAI 错误形状，状态码语义保持；
  g) 别名路由 POST /chat/completions（ZCode 在 A5 口径 baseUrl 后自行追加
     /chat/completions 的实际落点）与 /v1 路径同 handler、同语义。

【本地优先】默认路由 = 本机 Ollama（假上游），云端分支被 ENABLE_CLOUD_API=false 熔断；
全部请求走假 httpx 客户端，绝不产生任何真实网络请求（含云端）。

运行方式：D:\\python3.12\\python.exe -X utf8 test_openai_compat.py
"""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

# 确保能导入 server.app 模块
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离：工作区每次 rmtree 重建 + 熔断上限钉小（必须在导入 config 之前设置）----
TEST_DIR = PROJECT_ROOT / "test_openai_compat_workspace"
shutil.rmtree(TEST_DIR, ignore_errors=True)
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "test_openai_compat.db")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "3"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import config, db, llm_proxy

# 审计日志（budget_exceeded 记录）落表需要 schema；写的是上面的临时库，不碰用户主库
db.init_db()

LIMIT = config.MAX_LLM_CALLS_PER_SESSION
#: 本地上游 URL（llm_proxy 按 config.OLLAMA_BASE_URL 拼装；测试断言与其同源，不硬编码）
LOCAL_CHAT_URL = config.OLLAMA_BASE_URL.rstrip("/") + "/v1/chat/completions"

# ---------------------------------------------------------------- 假上游
#: 可变响应槽：测试可临时替换上游返回内容（tool_calls 变体等），setUp 复位
REPLY_HOLDER: dict = {"data": None}

_DEFAULT_REPLY = {
    "choices": [{
        "message": {"role": "assistant", "content": "mock-reply",
                    "reasoning_content": "mock-thinking"},
        "finish_reason": "stop",
    }],
    "model": "qwen3.5:9b",
    "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
}

#: 每次假上游调用的 (url, payload) 记录
captured: list = []


def _current_reply() -> dict:
    return REPLY_HOLDER["data"] if REPLY_HOLDER["data"] is not None else _DEFAULT_REPLY


class _HealthResponse:
    """本地上游健康检查替身（llm_proxy._local_health_ok 的 GET /api/version）"""

    status_code = 200


class _FakeResponse:
    """非流式响应替身：永远 200，返回最小 OpenAI 兼容结构"""
    status_code = 200

    def json(self):
        return _current_reply()

    @property
    def text(self):
        return json.dumps(_current_reply())


def _sse_bytes() -> bytes:
    """上游 OpenAI SSE（Ollama 兼容口形状）：3 个 chunk 事件 + [DONE]"""
    base = {"id": "up1", "object": "chat.completion.chunk", "created": 1,
            "model": "qwen3.5:9b"}
    evt1 = dict(base, choices=[{"index": 0, "delta": {"role": "assistant", "content": "Hel"},
                                "finish_reason": None}])
    evt2 = dict(base, choices=[{"index": 0, "delta": {"content": "lo"},
                                "finish_reason": None}])
    evt3 = dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
    full = "".join(f"data: {json.dumps(e)}\n\n" for e in (evt1, evt2, evt3))
    return full.encode("utf-8") + b"data: [DONE]\n\n"


class _FakeUpstream:
    """流式响应替身：aiter_bytes 故意按任意字节边界切分（模拟真实网络分片）"""

    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aread(self):
        return b"".join(self._chunks)

    async def aclose(self):
        pass


class _FakeAsyncClient:
    """httpx.AsyncClient 替身：只覆盖 llm_proxy 用到的接口，绝不产生真实连接。
    patch 的是 AsyncClient；TestClient 走的是同步 httpx.Client，不受影响。
    [批次二·5] 补 get()：本地优先重构后 llm_chat 先做 Ollama 存活预检（GET /api/version）。"""

    #: 流式分支的上游状态码开关（测错误事件映射时改 500）
    stream_status = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        return _HealthResponse()

    async def post(self, url, json=None, headers=None):
        captured.append((url, json))
        return _FakeResponse()

    def build_request(self, method, url, **kwargs):
        captured.append((url, kwargs.get("json")))
        return (method, url)

    async def send(self, req, stream=False):
        if type(self).stream_status != 200:
            return _FakeUpstream([b'{"error": {"message": "boom"}}'],
                                 status_code=type(self).stream_status)
        full = _sse_bytes()
        # 故意在事件中间切断，验证透传层不做重组、不破坏字节
        return _FakeUpstream([full[:70], full[70:200], full[200:]])

    async def aclose(self):
        pass


def _build_app() -> FastAPI:
    """只挂 llm_proxy 路由的最小应用（不启动整站，避免静态托管等无关依赖）"""
    app = FastAPI()
    app.include_router(llm_proxy.router)
    return app


def _oai_body(session_id=None, stream=None, model="qwen3.5:9b"):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if stream is not None:
        body["stream"] = stream
    if session_id is not None:
        body["session_id"] = session_id
    return body


def _parse_sse_data_lines(text: str):
    """抽出 SSE 里所有 data: 行的载荷（[DONE] 原样保留为字符串）"""
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: "):]
            if payload.strip() == "[DONE]":
                out.append("[DONE]")
            else:
                out.append(json.loads(payload))
    return out


class TestOpenAICompatRoute(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # [本地优先] 显式钉 provider=ollama：请求路由本机 Ollama（假上游），
        # 云端分支仅保留"未配置/被熔断"两条负路径的断言，绝不发真实云端请求。
        cls._orig_cfg = (config.LLM_PROVIDER, config.LLM_BASE_URL,
                         config.LLM_API_KEY, config.LLM_MODEL)
        config.LLM_PROVIDER = "ollama"
        config.LLM_BASE_URL = "http://mock-cloud.test/v1"   # 兜底位；闸门关闭时不可达
        config.LLM_API_KEY = ""
        # 云端路由的模型兜底名（仅 provider=cloud 的负路径测试会消费它；ollama 路由不读它）
        config.LLM_MODEL = "deepseek-flash"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeAsyncClient)
        cls._patcher.start()
        cls.client = TestClient(_build_app())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        (config.LLM_PROVIDER, config.LLM_BASE_URL,
         config.LLM_API_KEY, config.LLM_MODEL) = cls._orig_cfg

    def setUp(self):
        llm_proxy._session_calls.clear()   # 用例间互不残留计数
        captured.clear()
        REPLY_HOLDER["data"] = None
        _FakeAsyncClient.stream_status = 200

    # ================= a) 非流式标准 OpenAI 响应 =================
    def test_a_non_stream_openai_shape(self):
        r = self.client.post("/v1/chat/completions", json=_oai_body(stream=False))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        # 顶层标准键齐全，自有协议键（ok/content/reasoning）不得泄漏
        self.assertEqual(d["object"], "chat.completion")
        self.assertTrue(d["id"].startswith("chatcmpl-"), f"id 应为 chatcmpl-*，实得 {d['id']}")
        self.assertIsInstance(d["created"], int)
        self.assertEqual(d["model"], "qwen3.5:9b")
        for legacy_key in ("ok", "content", "reasoning"):
            self.assertNotIn(legacy_key, d, f"OpenAI 响应不应含自有协议键 {legacy_key}")
        # choices 结构
        c = d["choices"][0]
        self.assertEqual(c["index"], 0)
        self.assertEqual(c["message"]["role"], "assistant")
        self.assertEqual(c["message"]["content"], "mock-reply")
        self.assertEqual(c["message"]["reasoning_content"], "mock-thinking")
        self.assertEqual(c["finish_reason"], "stop")
        # usage 三键透传
        self.assertEqual(d["usage"], {"prompt_tokens": 11, "completion_tokens": 22,
                                      "total_tokens": 33})
        # 内调 llm_chat：上游打到本机 Ollama 兼容口，stream 语义按 OpenAI 规范补 false
        url, payload = captured[-1]
        self.assertEqual(url, LOCAL_CHAT_URL)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["model"], "qwen3.5:9b")

    def test_a2_stream_omitted_defaults_to_false(self):
        """OpenAI 规范：不带 stream 字段 → 非流式 JSON（现有路由缺省 true，语义差须补齐）"""
        r = self.client.post("/v1/chat/completions", json=_oai_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["object"], "chat.completion")
        self.assertIs(captured[-1][1]["stream"], False,
                      "转换层应把缺省 stream 补成 False 再内调")

    def test_a3_tool_calls_mapping(self):
        """上游返回 tool_calls → message.tool_calls 透传 + finish_reason=tool_calls"""
        REPLY_HOLDER["data"] = {
            "choices": [{
                "message": {"role": "assistant", "content": "",
                            "tool_calls": [{"id": "call_1", "type": "function",
                                            "function": {"name": "get_weather",
                                                         "arguments": "{}"}}]},
                "finish_reason": "tool_calls",
            }],
            "model": "qwen3.5:9b",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }
        r = self.client.post("/v1/chat/completions",
                             json=dict(_oai_body(stream=False),
                                       tools=[{"type": "function",
                                               "function": {"name": "get_weather"}}],
                                       tool_choice="auto"))
        self.assertEqual(r.status_code, 200)
        c = r.json()["choices"][0]
        self.assertEqual(c["finish_reason"], "tool_calls")
        self.assertEqual(c["message"]["tool_calls"][0]["function"]["name"], "get_weather")
        # tools/tool_choice 经现有白名单透传给上游（ZCode 工具轮的命脉）
        payload = captured[-1][1]
        self.assertEqual(len(payload["tools"]), 1)
        self.assertEqual(payload["tool_choice"], "auto")

    # ================= b) 流式 SSE delta =================
    def test_b_stream_sse_delta_passthrough(self):
        r = self.client.post("/v1/chat/completions", json=_oai_body(stream=True))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"),
                        f"流式应为 SSE，实得 {r.headers['content-type']}")
        events = _parse_sse_data_lines(r.text)
        self.assertEqual(events[-1], "[DONE]", "流必须以 data: [DONE] 收尾")
        chunks = [e for e in events if e != "[DONE]"]
        self.assertEqual(len(chunks), 3, f"应透传上游 3 个 chunk 事件，实得 {len(chunks)}")
        for e in chunks:
            self.assertEqual(e["object"], "chat.completion.chunk")
        # delta 内容与字节切分重组后的完整性（"Hel"+"lo" 两个 delta 事件）
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "Hel")
        self.assertEqual(chunks[1]["choices"][0]["delta"]["content"], "lo")
        self.assertEqual(chunks[2]["choices"][0]["finish_reason"], "stop")
        self.assertNotIn('"ok"', r.text, "OpenAI SSE 流中不得出现自有协议键")
        self.assertIs(captured[-1][1]["stream"], True)

    def test_b2_stream_local_upstream_error_maps_to_openai_event(self):
        """本地上游流式 5xx：llm_chat 自产的 {ok:false} 错误事件必须转成 OpenAI error 事件。
        [批次二·5 语义] 本地上游错误按红线绝不降级云端，code=local_upstream_error。"""
        _FakeAsyncClient.stream_status = 500
        r = self.client.post("/v1/chat/completions", json=_oai_body(stream=True))
        self.assertEqual(r.status_code, 200)  # SSE 通道本身 200，错误在事件里
        events = _parse_sse_data_lines(r.text)
        self.assertEqual(events[-1], "[DONE]")
        err = events[0]
        self.assertNotIn("ok", err, "自有形状 {ok:false} 不得泄漏给 OpenAI 客户端")
        self.assertEqual(err["error"]["type"], "api_error")
        self.assertEqual(err["error"]["code"], "local_upstream_error",
                         "本地上游错误不得改投云端，应如实报 local_upstream_error")
        self.assertIn("本地 Ollama", err["error"]["message"])
        # 全程只打本地上游，不出现任何云端 URL
        self.assertTrue(all(u == LOCAL_CHAT_URL for u, _ in captured))

    # ================= c) 模型路由【本地优先语义】 =================
    def test_c_model_passthrough_for_ollama(self):
        """[批次二·5 重写] provider=ollama：模型名原样透传（Ollama 接受任意本地模型名，
        不再做云端白名单兜底——兜底会把本机模型名改写成云端名，正是旧架构的病根）"""
        r = self.client.post("/v1/chat/completions",
                             json=_oai_body(stream=False, model="qwen2.5:7b"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured[-1][1]["model"], "qwen2.5:7b",
                         "ollama 路由的模型名必须原样透传")

    def test_c2_default_model_falls_to_ollama_model(self):
        """[批次二·5 重写] 缺省模型名 → 回落 config.OLLAMA_MODEL（本地默认模型）"""
        r = self.client.post("/v1/chat/completions",
                             json=_oai_body(stream=False, model=""))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured[-1][1]["model"], config.OLLAMA_MODEL)

    def test_c3_cloud_provider_blocked_by_gate(self):
        """[批次二·5 新增] provider=cloud 在 ENABLE_CLOUD_API=false 时被熔断闸门拦截：
        即使云端地址已配置也不放行（本地优先的强制闸门）。
        [2026-10-01 Z-4] 原前置断言"测试环境必须保持 ENABLE_CLOUD_API=false"随 .env
        翻 true（ZCode 走云端专用）必然失真——config 导入即 load_dotenv(server/.env)。
        改为测试内显式关闸（save/restore），钉死的语义不变：闸门关 → body.provider=cloud
        被 503 cloud_api_disabled 拦截且零上游请求。"""
        saved_gate = config.ENABLE_CLOUD_API
        config.ENABLE_CLOUD_API = False
        try:
            r = self.client.post("/v1/chat/completions",
                                 json=dict(_oai_body(stream=False), provider="cloud"))
        finally:
            config.ENABLE_CLOUD_API = saved_gate
        self.assertEqual(r.status_code, 503)
        d = r.json()
        self.assertEqual(d["error"]["code"], "cloud_api_disabled")
        # 全程未发出任何上游请求（闸门在路由前拦截）
        self.assertEqual(captured, [])

    # ================= d) 预算熔断在新路由生效 =================
    def test_d_budget_429_openai_shape(self):
        for i in range(LIMIT):
            r = self.client.post("/v1/chat/completions",
                                 json=_oai_body(stream=False, session_id="sA"))
            self.assertEqual(r.status_code, 200, f"第 {i + 1} 次应正常")
        r = self.client.post("/v1/chat/completions",
                             json=_oai_body(stream=False, session_id="sA"))
        self.assertEqual(r.status_code, 429, f"第 {LIMIT + 1} 次应熔断")
        d = r.json()
        self.assertNotIn("ok", d, "熔断响应也须是 OpenAI error 形状")
        self.assertEqual(d["error"]["type"], "rate_limit_error")
        self.assertEqual(d["error"]["code"], "session_budget_exceeded")
        self.assertIn(str(LIMIT), d["error"]["message"])
        # 被拒请求也继续累计（与现有路由同一语义）
        self.assertEqual(llm_proxy._session_calls["sA"], LIMIT + 1)

    def test_d2_budget_shared_with_legacy_route(self):
        """两路由共享同一 _session_calls 桶；X-Session-Id 头经 scope 透传到内调"""
        hdr = {"X-Session-Id": "hdr-shared"}
        r1 = self.client.post("/api/llm/chat", headers=hdr,
                              json={"model": "qwen3.5:9b", "stream": False,
                                    "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r1.status_code, 200)
        for i in range(LIMIT - 1):
            r = self.client.post("/v1/chat/completions", json=_oai_body(stream=False),
                                 headers=hdr)
            self.assertEqual(r.status_code, 200)
        self.assertEqual(llm_proxy._session_calls.get("hdr-shared"), LIMIT,
                         "头归属的 session 应被两路由共同累计")
        r = self.client.post("/v1/chat/completions", json=_oai_body(stream=False),
                             headers=hdr)
        self.assertEqual(r.status_code, 429, "新路由第 %d 次应熔断" % (LIMIT + 1))
        # 同 session 打旧路由同样被拒（桶是同一个）
        r = self.client.post("/api/llm/chat", headers=hdr,
                             json={"model": "qwen3.5:9b", "stream": False,
                                   "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json().get("error"), "session_budget_exceeded",
                         "旧路由熔断形状保持不变")
        self.assertNotIn("default", llm_proxy._session_calls,
                         "头已给 session_id 时不应计入 default 桶")

    # ================= e) /api/llm/chat 行为回归 =================
    def test_e_legacy_route_success_shape_unchanged(self):
        r = self.client.post("/api/llm/chat",
                             json={"model": "qwen3.5:9b", "stream": False,
                                   "session_id": "leg1",
                                   "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        # 自有协议形状逐键断言：不得混入 OpenAI 键
        self.assertEqual(d, {
            "ok": True,
            "content": "mock-reply",
            "reasoning": "mock-thinking",
            "model": "qwen3.5:9b",
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        })
        for oai_key in ("id", "object", "choices", "created"):
            self.assertNotIn(oai_key, d, f"旧路由不应长出 OpenAI 键 {oai_key}")
        self.assertEqual(captured[-1][0], LOCAL_CHAT_URL)

    def test_e2_legacy_route_error_shapes_unchanged(self):
        # bad_messages：400 + {ok:false, code, message}
        r = self.client.post("/api/llm/chat", json={"model": "qwen3.5:9b",
                                                    "messages": [], "stream": False})
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertEqual(d["ok"], False)
        self.assertEqual(d["code"], "bad_messages")
        self.assertNotIn("error", d)
        # 熔断：429 + {ok:false, error, msg}（Day1 规格键名）——[Day24.11b] 增补 message 人话字段，
        # error/msg 原值保留为向后兼容契约（本改动经审计后最小必要更新，见 docs/day24.11b_429_message.md）
        for _ in range(LIMIT):
            self.client.post("/api/llm/chat",
                             json={"model": "qwen3.5:9b", "stream": False,
                                   "session_id": "leg2",
                                   "messages": [{"role": "user", "content": "hi"}]})
        r = self.client.post("/api/llm/chat",
                             json={"model": "qwen3.5:9b", "stream": False,
                                   "session_id": "leg2",
                                   "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(r.status_code, 429)
        d = r.json()
        self.assertEqual(d, {"ok": False, "error": "session_budget_exceeded",
                             "msg": f"本次会话已达到 LLM 调用次数上限（{LIMIT}次）",
                             "message": f"本次会话已达调用次数上限（{LIMIT} 次），请新建对话后继续"})

    # ================= f) 错误形状与状态码语义 =================
    def test_f_bad_json_openai_shape(self):
        r = self.client.post("/v1/chat/completions", content=b"{not-json",
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertEqual(d["error"]["type"], "invalid_request_error")
        self.assertEqual(d["error"]["code"], "bad_json")

    def test_f2_non_object_body_openai_shape(self):
        r = self.client.post("/v1/chat/completions", json=[1, 2, 3])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "bad_json")

    def test_f3_bad_messages_openai_shape(self):
        r = self.client.post("/v1/chat/completions",
                             json={"model": "qwen3.5:9b", "messages": []})
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertEqual(d["error"]["type"], "invalid_request_error")
        self.assertEqual(d["error"]["code"], "bad_messages")

    def test_f4_cloud_not_configured_openai_shape(self):
        """[批次二·5 重写] 云端未配置仅在全局 provider 指向云端时触发（本地优先下
        默认路由是 Ollama，不再因云端未配置而 503）"""
        orig = (config.LLM_PROVIDER, config.LLM_BASE_URL, config.LLM_API_KEY)
        try:
            config.LLM_PROVIDER = "cloud"      # 全局指云（模拟旧部署形态）
            config.LLM_BASE_URL = ""
            config.LLM_API_KEY = ""
            r = self.client.post("/v1/chat/completions", json=_oai_body(stream=False))
            self.assertEqual(r.status_code, 503)
            d = r.json()
            self.assertEqual(d["error"]["type"], "api_error")
            self.assertEqual(d["error"]["code"], "cloud_not_configured")
        finally:
            config.LLM_PROVIDER, config.LLM_BASE_URL, config.LLM_API_KEY = orig

    # ============ g) 别名路由 /chat/completions（ZCode A5 baseUrl 的实际落点）============
    def test_g_alias_route_same_handler(self):
        """ZCode 在 A5 口径 baseUrl（http://moray-gw-proxy:8000，不带 /v1）后自行追加
        /chat/completions —— 别名路由必须与 /v1 路径同形同语义（同一 handler）"""
        r = self.client.post("/chat/completions", json=_oai_body(stream=False))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["object"], "chat.completion")
        self.assertEqual(d["choices"][0]["message"]["content"], "mock-reply")
        self.assertEqual(d["usage"]["total_tokens"], 33)
        self.assertEqual(captured[-1][0], LOCAL_CHAT_URL)

    def test_g2_alias_route_stream_and_budget(self):
        """别名路由的流式与熔断语义与 /v1 完全一致"""
        r = self.client.post("/chat/completions", json=_oai_body(stream=True))
        self.assertEqual(r.status_code, 200)
        events = _parse_sse_data_lines(r.text)
        self.assertEqual(events[-1], "[DONE]")
        self.assertEqual(events[0]["object"], "chat.completion.chunk")
        for _ in range(LIMIT):
            self.assertEqual(
                self.client.post("/chat/completions",
                                 json=_oai_body(stream=False, session_id="alias-s")).status_code,
                200)
        r = self.client.post("/chat/completions",
                             json=_oai_body(stream=False, session_id="alias-s"))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["error"]["code"], "session_budget_exceeded")


def run_tests():
    print("=" * 60)
    print("MoRay OpenAI 兼容路由自动化测试（/v1/chat/completions · 本地优先）")
    print("=" * 60)
    print(f"上限 MAX_LLM_CALLS_PER_SESSION = {LIMIT}")
    print(f"本地上游: {LOCAL_CHAT_URL}（假客户端，零真实网络请求）")
    print(f"测试数据库: {config.SQLITE_PATH}")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestOpenAICompatRoute)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！OpenAI 兼容层与既有路由行为完好。")
    else:
        print(f"❌ 测试失败！共 {result.testsRun} 项，"
              f"失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
    print("=" * 60)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
