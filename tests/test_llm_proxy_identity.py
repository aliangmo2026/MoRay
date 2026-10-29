# -*- coding: utf-8 -*-
"""llm_proxy 身份底座 + 免责声明兜底测试（2026-10-27）。

运行（cwd = 工程根）：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_llm_proxy_identity.py -v

背景：qwen2.5:1.5b 主聊天问"你是谁"复读"我是预训练模型 / 我没有记忆 / 无法提供
具体代码帮助"——messages[0] 从来没有身份指令，事实卡/RAG/用户 system 都不是"身份"。

覆盖：
  * 身份底座恒在 messages[0]，历史 + RAG + 用户 system 全排其后（不可覆盖）；
  * 幂等（不重复插）与 MORAY_IDENTITY 总闸（0/false/no/off 跳过）；
  * 纯套话判定（事故原文命中、自报 MoRay / 超长引用 / 正常回答不命中）；
  * 非流式：纯套话 → 追加纠正指令重试一次（只一次，第二次仍套话就照发）；
  * 流式：正常回复一个请求原样透传（零误伤、逐字不丢）；纯套话 → 丢弃重试一次；
    工具轮（无 content）立即放行，绝不扣到流结束。
零真实网络：httpx.AsyncClient 整体替换为替身（同 test_llm_proxy_fallback 惯例）。
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

TEST_DIR = PROJECT_ROOT / "test_llm_proxy_identity_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MORAY_BUDGET_DB"] = str(TEST_DIR / "bootstrap_budget.sqlite3")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app import config, db, llm_proxy  # noqa: E402
from server.app.llm_proxy import (  # noqa: E402
    IDENTITY_MARKER,
    _could_be_boilerplate,
    _extract_delta_text,
    _is_boilerplate_reply,
    inject_identity_system,
)

db.init_db()

#: 复读事故原文（用户实测 qwen2.5:1.5b 输出，命中 3 处套话特征）
BOILERPLATE = ("我是预训练语言模型，我没有记忆，每次对话都是全新的，"
               "也无法提供具体的代码帮助。")
GOOD_REPLY = "我是 MoRay，你的本地 AI 助手，拥有长期记忆库并可通过内置 Agent 执行代码。"
#: [2026-10-29 身份叛变] r1:7b 实测泄漏原文（会带 DeepSeek/深度求索）
IDENTITY_LEAK = "您好！我是由中国的深度求索（DeepSeek）公司开发的智能助手DeepSeek-R1。"
#: 身份追问的固定口径（llm_proxy 兜底后用户实际看到的那句）
from server.app.llm_proxy import IDENTITY_CANONICAL_REPLY  # noqa: E402

#: 非身份类 user 消息：免责声明重试只在这种问题上生效（身份类改走固定口径）
NON_IDENTITY_ASK = "帮我改一下这段 Python 代码"


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}],
            "model": "fake-model",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}


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


class _StreamResponse:
    """流式替身：按给定分块逐个吐字节。"""

    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass

    def json(self):
        return _chat_payload("")

    @property
    def text(self):
        return ""


def _sse_chunks(text: str):
    """把一段文本切成"逐字 SSE delta 分块"（真实逐 token 形态）。"""
    out = []
    for ch in text:
        out.append(("data: " + json.dumps(
            {"choices": [{"delta": {"content": ch}}]}, ensure_ascii=False) + "\n\n")
            .encode("utf-8"))
    out.append(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n')
    out.append(b"data: [DONE]\n\n")
    return out


class _FakeClient:
    """httpx.AsyncClient 替身：非流式走 post、流式走 build_request+send；请求体深拷贝入账。"""

    replies = []            # list[str]：非流式回复（按次序弹出，耗尽后重复最后一个）
    stream_replies = []     # list[str]：流式回复（同上）
    custom_chunks = None    # list[bytes]：流式自定义分块（工具轮用例）
    payloads = []           # 每次请求体的深拷贝（断言 messages 用）
    mode = "json"           # json = 非流式；stream = 流式

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
        idx = len(type(self).payloads) - 1
        pool = type(self).replies or [GOOD_REPLY]
        return _JsonResponse(_chat_payload(pool[idx] if idx < len(pool) else pool[-1]))

    def build_request(self, method, url, **kwargs):
        return {"url": url, "json": kwargs.get("json")}

    async def send(self, req, stream=False):
        type(self).payloads.append(copy.deepcopy((req or {}).get("json")))
        if type(self).custom_chunks:
            return _StreamResponse(type(self).custom_chunks)
        idx = len(type(self).payloads) - 1
        pool = type(self).stream_replies or [GOOD_REPLY]
        content = pool[idx] if idx < len(pool) else pool[-1]
        return _StreamResponse(_sse_chunks(content))

    async def aclose(self):
        pass


def _joined_content(raw: str) -> str:
    """把 SSE 原文按序拼回正文（逐字分帧 → 断言不能直接在 raw 里找子串）。"""
    out = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:  # noqa: BLE001 - 只为断言，解析失败跳过
            continue
        for ch in (obj.get("choices") or []):
            c = (ch.get("delta") or {}).get("content")
            if isinstance(c, str):
                out.append(c)
    return "".join(out)


def _body(messages=None, stream=False):
    return {"messages": messages or [{"role": "user", "content": "你是谁"}],
            "stream": stream, "provider": "ollama", "session_id": "identity-test"}


class IdentityBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_provider = config.LLM_PROVIDER
        config.LLM_PROVIDER = "ollama"
        # twin RAG / 事实卡对本测试是噪声（身份注入时机与它们无关），关掉跑快且稳
        cls._env_saved = {k: os.environ.get(k) for k in
                          ("MORAY_TWIN_RAG", "MORAY_SELF_CARD", "MORAY_IDENTITY")}
        os.environ["MORAY_TWIN_RAG"] = "0"
        os.environ["MORAY_SELF_CARD"] = "0"
        os.environ["MORAY_IDENTITY"] = "1"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeClient)
        cls._patcher.start()
        app = FastAPI()
        app.include_router(llm_proxy.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        config.LLM_PROVIDER = cls._orig_provider
        for k, v in cls._env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def setUp(self):
        _FakeClient.replies = []
        _FakeClient.stream_replies = []
        _FakeClient.custom_chunks = None
        _FakeClient.payloads = []
        _FakeClient.mode = "json"
        llm_proxy._session_calls.clear()


# ---------------------------------------------------------------- 纯函数层
class PureFunctionTests(IdentityBase):
    def test_01_identity_goes_to_index_zero(self):
        msgs = [
            {"role": "system", "content": "用户自定义人格：你叫小助"},
            {"role": "system", "content": "[相关个人文档知识] 语料片段"},
            {"role": "user", "content": "你是谁"},
            {"role": "assistant", "content": "我是预训练模型"},
        ]
        inject_identity_system(msgs)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertTrue(msgs[0]["content"].startswith(IDENTITY_MARKER))
        # 原有 system 与 RAG 全部排在其后，一条不少
        self.assertEqual(len(msgs), 5)
        self.assertTrue(msgs[1]["content"].startswith("用户自定义人格"))
        self.assertTrue(msgs[2]["content"].startswith("[相关个人文档知识]"))

    def test_02_identity_injection_is_idempotent(self):
        msgs = [{"role": "user", "content": "hi"}]
        inject_identity_system(msgs)
        inject_identity_system(msgs)
        hits = [m for m in msgs if str(m.get("content", "")).startswith(IDENTITY_MARKER)]
        self.assertEqual(len(hits), 1)

    def test_03_identity_gate_off(self):
        os.environ["MORAY_IDENTITY"] = "0"
        try:
            msgs = [{"role": "user", "content": "hi"}]
            inject_identity_system(msgs)
            self.assertEqual(len(msgs), 1)
        finally:
            os.environ["MORAY_IDENTITY"] = "1"

    def test_04_boilerplate_detector(self):
        # 事故原文（复读三连）→ 必须命中
        self.assertTrue(_is_boilerplate_reply(BOILERPLATE))
        # 自报 MoRay 的能力回答 → 不命中
        self.assertFalse(_is_boilerplate_reply(GOOD_REPLY))
        # 超长回答里引用一句套话 → 不命中（宁可漏判不可误伤）
        self.assertFalse(_is_boilerplate_reply(
            "作为一个AI语言模型，我先解释一下这个概念。" + "接下来是正文。" * 100))
        # 空 / 无套话特征 → 不命中
        self.assertFalse(_is_boilerplate_reply(""))
        self.assertFalse(_is_boilerplate_reply("今晚月色很好，适合写两行代码。"))

    def test_05_could_be_boilerplate_first_chunk(self):
        self.assertTrue(_could_be_boilerplate(""))            # 太短，判不了 → 继续缓冲
        self.assertTrue(_could_be_boilerplate("作为一个AI"))  # 开头命中套话特征
        self.assertFalse(_could_be_boilerplate("好的，我来帮你把这段脚本改一下缩进。"))

    def test_06_extract_delta_text_handles_split_lines(self):
        raw = _sse_chunks("你好")
        # 第一个分块故意切一半（模拟真实 TCP 半包）
        first, second = raw[0][:9], raw[0][9:]
        got1, carry = _extract_delta_text(first, b"")
        self.assertEqual(got1, "")                            # 半行不解、不丢
        got2, carry = _extract_delta_text(second, carry)
        self.assertEqual(got1 + got2, "你")
        # 整段喂进去：文本全拿到、carry 清空
        text, carry = _extract_delta_text(b"".join(raw), b"")
        self.assertEqual(text, "你好")
        self.assertEqual(carry, b"")


# ---------------------------------------------------------------- 端到端：非流式
class NonStreamTests(IdentityBase):
    def test_10_normal_reply_single_request_identity_first(self):
        _FakeClient.replies = [GOOD_REPLY]
        r = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("content"), GOOD_REPLY)
        self.assertEqual(len(_FakeClient.payloads), 1)        # 未误触发重试
        sent = _FakeClient.payloads[0]["messages"]
        self.assertTrue(sent[0]["content"].startswith(IDENTITY_MARKER))
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1]["role"], "user")            # 身份在用户消息之前

    def test_11_boilerplate_triggers_one_correction_retry(self):
        _FakeClient.replies = [BOILERPLATE, GOOD_REPLY]
        r = self.client.post("/api/llm/chat", json=_body(
            [{"role": "user", "content": NON_IDENTITY_ASK}]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("content"), GOOD_REPLY)  # 拿到纠正后的回复
        self.assertEqual(len(_FakeClient.payloads), 2)         # 只重试一次
        first, second = _FakeClient.payloads
        self.assertTrue(first["messages"][0]["content"].startswith(IDENTITY_MARKER))
        self.assertTrue(second["messages"][0]["content"].startswith(IDENTITY_MARKER))  # 底座仍在首位
        self.assertTrue(second["messages"][-1]["content"].startswith("【强制纠正】"))   # 纠正垫底

    def test_12_retry_once_only_even_if_still_boilerplate(self):
        _FakeClient.replies = [BOILERPLATE, BOILERPLATE]
        r = self.client.post("/api/llm/chat", json=_body(
            [{"role": "user", "content": NON_IDENTITY_ASK}]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(_FakeClient.payloads), 2)         # 绝不循环
        self.assertEqual(r.json().get("content"), BOILERPLATE)


class IdentityPolicyTests(IdentityBase):
    """[2026-10-29 身份叛变] 身份追问只认“我是 MoRay”，沾厂商名/答空直接换固定口径。"""

    def test_13_identity_vendor_leak_replaced_with_canonical(self):
        _FakeClient.replies = [IDENTITY_LEAK]
        r = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("content"), IDENTITY_CANONICAL_REPLY)
        self.assertEqual(len(_FakeClient.payloads), 1)          # 不重试，省一次生成

    def test_14_identity_empty_answer_replaced_with_canonical(self):
        _FakeClient.replies = [""]
        r = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r.json().get("content"), IDENTITY_CANONICAL_REPLY)

    def test_15_identity_clean_answer_passes_through(self):
        _FakeClient.replies = [GOOD_REPLY]
        r = self.client.post("/api/llm/chat", json=_body())
        self.assertEqual(r.json().get("content"), GOOD_REPLY)
        self.assertEqual(len(_FakeClient.payloads), 1)

    def test_23_identity_stream_leak_replaced_with_canonical(self):
        _FakeClient.mode = "stream"
        _FakeClient.stream_replies = [IDENTITY_LEAK]
        raw = self.client.post("/api/llm/chat", json=_body(stream=True)).text
        self.assertEqual(len(_FakeClient.payloads), 1)
        text = _joined_content(raw)
        self.assertEqual(text, IDENTITY_CANONICAL_REPLY)
        self.assertNotIn("DeepSeek", text)
        self.assertTrue(raw.rstrip().endswith("data: [DONE]"))

    def test_24_identity_stream_empty_answer_replaced(self):
        _FakeClient.mode = "stream"
        _FakeClient.stream_replies = [""]
        raw = self.client.post("/api/llm/chat", json=_body(stream=True)).text
        self.assertEqual(_joined_content(raw), IDENTITY_CANONICAL_REPLY)
        self.assertTrue(raw.rstrip().endswith("data: [DONE]"))


# ---------------------------------------------------------------- 端到端：流式
class StreamTests(IdentityBase):
    def setUp(self):
        super().setUp()
        _FakeClient.mode = "stream"

    def _collect(self, body):
        r = self.client.post("/api/llm/chat", json=body)
        self.assertEqual(r.status_code, 200)
        return r.text

    def test_20_normal_stream_passthrough_no_retry(self):
        _FakeClient.stream_replies = [GOOD_REPLY]
        raw = self._collect(_body(stream=True))
        self.assertEqual(len(_FakeClient.payloads), 1)         # 正常回复零误伤
        self.assertEqual(_joined_content(raw), GOOD_REPLY)     # 逐字 delta 全透传、一字不丢
        self.assertEqual(raw.count('"content"'), len(GOOD_REPLY))
        self.assertTrue(raw.rstrip().endswith("data: [DONE]"))

    def test_21_boilerplate_stream_retried_once(self):
        _FakeClient.stream_replies = [BOILERPLATE, GOOD_REPLY]
        raw = self._collect(_body(
            [{"role": "user", "content": NON_IDENTITY_ASK}], stream=True))
        self.assertEqual(len(_FakeClient.payloads), 2)         # 丢弃套话 + 重试一次
        text = _joined_content(raw)
        self.assertNotIn("我是预训练", text)                   # 套话没有流给客户端
        self.assertIn("我是 MoRay", text)                      # 纠正后的回复照常流出
        second = _FakeClient.payloads[-1]["messages"]
        self.assertTrue(second[0]["content"].startswith(IDENTITY_MARKER))
        self.assertTrue(second[-1]["content"].startswith("【强制纠正】"))

    def test_22_tool_call_stream_not_held_back(self):
        # 工具轮没有 content：必须立即放行，绝不能扣到流结束
        _FakeClient.custom_chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
            b'{"name":"list_directory","arguments":"{}"}}]}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        raw = self._collect(_body(stream=True))
        self.assertIn("tool_calls", raw)                      # 原样透传
        self.assertTrue(raw.rstrip().endswith("data: [DONE]"))


if __name__ == "__main__":
    unittest.main()
