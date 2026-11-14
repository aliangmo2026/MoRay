# -*- coding: utf-8 -*-
"""PM Agent（需求澄清前置闸）测试（2026-11-14）。

运行（cwd = 工程根）：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_pm_agent.py -v

覆盖：
  * 启发式预筛：模糊复杂任务命中 / 闲聊·明确指令·带文件路径·短问句·夜班前缀透传；
  * clarify_requirement_sync：反问 / 任务书 / fail-open（模型异常·非 JSON）/
    一轮封顶（上下文已有澄清标记 → 钳掉 questions）/ questions 清洗 / 围栏 JSON；
  * llm_chat 集成：模糊输入 → 合成澄清短路（上游零调用 + X-Moray-PM-Clarify 头）；
    明确输入 → 直接透传（PM 模型零调用，上游正常出答）；organ 回环 / 工具环
    中途轮硬豁免；任务书注入（上游收到的最后一条 user 消息带任务书块）；
    总闸 MORAY_PM_AGENT=0 全链路零变化。

零真实网络：上游 httpx.AsyncClient 整体替换为替身（test_llm_proxy_identity
同款惯例）；PM 模型调用直接 mock pm_agent._call_pm_model。
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

TEST_DIR = PROJECT_ROOT / "test_pm_agent_workspace"
TEST_DIR.mkdir(exist_ok=True)
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MORAY_BUDGET_DB"] = str(TEST_DIR / "bootstrap_budget.sqlite3")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"
os.environ.setdefault("MORAY_MEMORY_DB",
                      os.path.join(TEST_DIR, "memory.sqlite3"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app import config, db, llm_proxy, pm_agent  # noqa: E402

db.init_db()

VAGUE = "帮我优化一下项目"
CLEAR = "读取 README 的内容并总结要点"
GOOD_REPLY = "README 的要点如下：本地优先、MCP 矩阵、可解释判据。"

#: 模型返回：反问 JSON
PM_QUESTIONS_JSON = json.dumps({
    "need_clarify": True,
    "questions": ["你想优化哪个模块？（A) 数据库 / B) 前端交互 / C) 全部）",
                  "验收标准是什么？（A) 启动时间减半 / B) 无明显卡顿）"],
    "prd_draft": "",
}, ensure_ascii=False)

#: 模型返回：任务书 JSON
PM_PRD_JSON = json.dumps({
    "need_clarify": False,
    "questions": [],
    "prd_draft": "【背景】项目整体性能不稳。【目标】启动 <5s。【涉及文件】server/app/db.py。【验收标准】pytest 全绿 + 启动计时 <5s。",
}, ensure_ascii=False)


# ---------------------------------------------------------------- 替身层

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


def _sse_chunks(text: str):
    out = []
    for ch in text:
        out.append(("data: " + json.dumps(
            {"choices": [{"delta": {"content": ch}}]}, ensure_ascii=False) + "\n\n")
            .encode("utf-8"))
    out.append(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n')
    out.append(b"data: [DONE]\n\n")
    return out


class _FakeClient:
    """上游 httpx.AsyncClient 替身（同 test_llm_proxy_identity 惯例）。"""
    replies = []
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
        idx = len(type(self).payloads) - 1
        pool = type(self).replies or [GOOD_REPLY]
        return _JsonResponse({"choices": [{"message": {"role": "assistant",
                                                       "content": pool[idx] if idx < len(pool) else pool[-1]}}],
                              "model": "fake-model",
                              "usage": {"prompt_tokens": 3, "completion_tokens": 4}})

    def build_request(self, method, url, **kwargs):
        return {"url": url, "json": kwargs.get("json")}

    async def send(self, req, stream=False):
        type(self).payloads.append(copy.deepcopy((req or {}).get("json")))
        idx = len(type(self).payloads) - 1
        pool = type(self).replies or [GOOD_REPLY]
        content = pool[idx] if idx < len(pool) else pool[-1]
        return _StreamResponse(_sse_chunks(content))

    async def aclose(self):
        pass


def _joined_content(raw: str) -> str:
    out = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue
        for ch in (obj.get("choices") or []):
            c = (ch.get("delta") or {}).get("content")
            if isinstance(c, str):
                out.append(c)
    return "".join(out)


def _upstream_payloads():
    """真实上游请求（带 messages 的 OpenAI chat payload）。

    MCP 网关注入开启时会先向各 MCP server 拉 tools/list（也走 _do_request →
    _FakeClient.post 入账，jsonrpc payload 无 messages 键）——按形状过滤掉，
    断言只针对真实上游调用。"""
    return [p for p in _FakeClient.payloads
            if isinstance(p, dict) and "messages" in p]


def _body(content=VAGUE, stream=False, messages=None, **extra):
    b = {"messages": messages if messages is not None else [
            {"role": "user", "content": content}],
         "stream": stream, "provider": "ollama", "session_id": "pm-test"}
    b.update(extra)
    return b


class PmBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_provider = config.LLM_PROVIDER
        config.LLM_PROVIDER = "ollama"
        cls._env_saved = {k: os.environ.get(k) for k in
                          ("MORAY_TWIN_RAG", "MORAY_SELF_CARD", "MORAY_IDENTITY",
                           "MORAY_PM_AGENT")}
        os.environ["MORAY_TWIN_RAG"] = "0"
        os.environ["MORAY_SELF_CARD"] = "0"
        os.environ["MORAY_IDENTITY"] = "0"
        os.environ.pop("MORAY_PM_AGENT", None)  # 总闸默认开
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
        _FakeClient.replies = [GOOD_REPLY]
        _FakeClient.payloads = []
        llm_proxy._session_calls.clear()
        pm_agent._VERDICT_CACHE.clear()          # 判决缓存隔离（防测试间串味）

    def _patch_pm(self, side_effect):
        """把 PM 模型调用换成替身（clarify_requirement 经 to_thread 调同名函数）。"""
        return mock.patch.object(pm_agent, "_call_pm_model", side_effect)


# ---------------------------------------------------------------- 预筛
class PrefilterTests(PmBase):
    def test_01_vague_hits(self):
        self.assertTrue(pm_agent.is_pm_candidate(VAGUE))
        self.assertTrue(pm_agent.is_pm_candidate("帮我加一个用户登录功能"))
        self.assertTrue(pm_agent.is_pm_candidate("把整个项目系统地重构一下"))

    def test_02_clear_passes_through(self):
        self.assertFalse(pm_agent.is_pm_candidate(CLEAR))       # 明确指令
        self.assertFalse(pm_agent.is_pm_candidate("你好"))       # 寒暄
        self.assertFalse(pm_agent.is_pm_candidate("晚上好呀moray"))  # 闲聊无模糊动词
        self.assertFalse(pm_agent.is_pm_candidate("帮我优化 server/app/db.py 的查询"))  # 带路径
        self.assertFalse(pm_agent.is_pm_candidate("什么是 RAG？"))  # 短问句
        self.assertFalse(pm_agent.is_pm_candidate("[夜班模式] 帮我优化一下项目"))  # 夜班前缀
        self.assertFalse(pm_agent.is_pm_candidate("优化" * 700))  # 超长粘贴
        self.assertFalse(pm_agent.is_pm_candidate(""))
        self.assertFalse(pm_agent.is_pm_candidate("优化一下"))    # 太短（4 字）

    def test_03_last_user_text_multimodal(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "帮我"},
            {"type": "image_url", "image_url": {"url": "data:..."}}]}]
        self.assertEqual(pm_agent.last_user_text(msgs), "帮我")


# ---------------------------------------------------------------- 同步内核
class ClarifyCoreTests(PmBase):
    def test_10_questions_flow(self):
        with self._patch_pm(lambda t, f: PM_QUESTIONS_JSON):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertTrue(r["need_clarify"])
        self.assertEqual(r["mode"], "clarify")
        self.assertEqual(len(r["questions"]), 2)
        self.assertIn(pm_agent.CLARIFY_MARKER, r["reply_text"])
        self.assertIn("1. ", r["reply_text"])
        self.assertIn("2. ", r["reply_text"])

    def test_11_prd_flow(self):
        with self._patch_pm(lambda t, f: PM_PRD_JSON):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertFalse(r["need_clarify"])
        self.assertEqual(r["mode"], "taskbook")
        self.assertIn("【背景】", r["taskbook_text"])
        self.assertEqual(r["refined_prompt"], json.loads(PM_PRD_JSON)["prd_draft"])

    def test_12_model_fail_fail_open(self):
        def boom(transcript, force_final):
            raise RuntimeError("ollama down")
        with self._patch_pm(boom):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertFalse(r["need_clarify"])
        self.assertEqual(r["mode"], "passthrough")
        self.assertEqual(r["refined_prompt"], VAGUE)
        self.assertIn("model_fail", r["note"])

    def test_13_garbage_output_fail_open(self):
        with self._patch_pm(lambda t, f: "我觉得这个需求挺好的，不用澄清。"):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertEqual(r["mode"], "passthrough")

    def test_14_fenced_json_parses(self):
        fenced = "```json\n" + PM_QUESTIONS_JSON + "\n```"
        with self._patch_pm(lambda t, f: fenced):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertTrue(r["need_clarify"])

    def test_15_prose_wrapped_json_sliced(self):
        wrapped = "好的，这是我的判断：" + PM_PRD_JSON + " 以上。"
        with self._patch_pm(lambda t, f: wrapped):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertEqual(r["mode"], "taskbook")

    def test_16_force_final_after_clarify_round(self):
        ctx = [
            {"role": "user", "content": VAGUE},
            {"role": "assistant", "content": pm_agent.format_clarify_reply(
                ["你想优化哪个模块？"])},          # 上一轮 PM 反问（含 marker）
            {"role": "user", "content": "1. 数据库；2. 查询 <100ms"},  # 用户已回答
        ]
        # 模型不听话还想反问 → 必须被钳成任务书/透传，绝不二次反问
        with self._patch_pm(lambda t, f: PM_QUESTIONS_JSON):
            r = pm_agent.clarify_requirement_sync("1. 数据库；2. 查询 <100ms", ctx)
        self.assertFalse(r["need_clarify"])
        self.assertEqual(r["questions"], [])
        self.assertTrue(r["rounds"] >= 1)

    def test_17_force_final_with_prd(self):
        ctx = [
            {"role": "user", "content": VAGUE},
            {"role": "assistant", "content": pm_agent.format_clarify_reply(["q？"])},
            {"role": "user", "content": "答案"},
        ]
        with self._patch_pm(lambda t, f: PM_PRD_JSON):
            r = pm_agent.clarify_requirement_sync(
                "就优化数据库模块，验收标准是慢查询全部消除", ctx)
        self.assertEqual(r["mode"], "taskbook")

    def test_18_questions_cleaned(self):
        dirty = json.dumps({"need_clarify": True, "questions": [
            "q1", "q1", "", 42, "q2", "q3", "q4"], "prd_draft": ""},
            ensure_ascii=False)
        with self._patch_pm(lambda t, f: dirty):
            r = pm_agent.clarify_requirement_sync(VAGUE, [])
        self.assertEqual(r["questions"], ["q1", "q2", "q3"])  # 去重去非串截 3 条

    def test_19_prefilter_no_model_call(self):
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        with self._patch_pm(spy):
            r = pm_agent.clarify_requirement_sync("晚上好呀moray", [])
        self.assertEqual(called, [])
        self.assertEqual(r["note"], "prefilter_pass")

    def test_20_empty_and_disabled(self):
        r = pm_agent.clarify_requirement_sync("", [])
        self.assertEqual(r["mode"], "passthrough")
        os.environ["MORAY_PM_AGENT"] = "0"
        try:
            called = []

            def spy(t, f):
                called.append(t)
                return PM_QUESTIONS_JSON
            with self._patch_pm(spy):
                r = pm_agent.clarify_requirement_sync(VAGUE, [])
            self.assertEqual(called, [])
            self.assertEqual(r["note"], "pm_disabled")
        finally:
            os.environ.pop("MORAY_PM_AGENT", None)


# ---------------------------------------------------------------- llm_chat 集成
class ChatRouteTests(PmBase):
    def test_30_vague_non_stream_short_circuit(self):
        with self._patch_pm(lambda t, f: PM_QUESTIONS_JSON):
            res = self.client.post("/api/llm/chat", json=_body(stream=False))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("pm_clarify"))
        self.assertIn(pm_agent.CLARIFY_MARKER, data.get("content") or "")
        # 短路纪律：上游一次都没被打
        self.assertEqual(_FakeClient.payloads, [])

    def test_31_vague_stream_short_circuit(self):
        with self._patch_pm(lambda t, f: PM_QUESTIONS_JSON):
            res = self.client.post("/api/llm/chat", json=_body(stream=True))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("x-moray-pm-clarify"), "1")
        self.assertIn(pm_agent.CLARIFY_MARKER, _joined_content(res.text))
        self.assertEqual(_FakeClient.payloads, [])

    def test_32_clear_input_passthrough(self):
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        with self._patch_pm(spy):
            res = self.client.post("/api/llm/chat", json=_body(CLEAR, stream=False))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertNotIn("pm_clarify", data)
        self.assertEqual(data.get("content"), GOOD_REPLY)
        self.assertEqual(called, [])            # PM 模型零调用
        self.assertEqual(len(_upstream_payloads()), 1)  # 上游恰一次

    def test_33_organ_callback_exempt(self):
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        with self._patch_pm(spy):
            res = self.client.post("/api/llm/chat",
                                   json=_body(VAGUE, stream=False),
                                   headers={"X-Moray-Source": "organ"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(called, [])
        self.assertEqual(len(_upstream_payloads()), 1)

    def test_34_tool_round_exempt(self):
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        # 豁免判别式 = 最后一个 user 之后存在 role:"tool"（本轮已开工的半途轮）
        msgs = [{"role": "user", "content": VAGUE},
                {"role": "assistant", "content": "", "tool_calls": []},
                {"role": "tool", "content": "tool output"}]
        with self._patch_pm(spy):
            res = self.client.post("/api/llm/chat",
                                   json=_body(stream=False, messages=msgs))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(called, [])
        self.assertEqual(len(_upstream_payloads()), 1)

    def test_34b_history_tool_before_last_user_not_exempt(self):
        """回归钉死（真机 bug）：历史轮的旧 tool 消息不能豁免新一轮的澄清闸。"""
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        msgs = [
            {"role": "user", "content": "旧任务"},
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "tool", "content": "旧工具结果"},   # 历史 tool（在最后一个 user 之前）
            {"role": "assistant", "content": "上一轮回答"},
            {"role": "user", "content": VAGUE},          # 本轮新需求
        ]
        with self._patch_pm(spy):
            res = self.client.post("/api/llm/chat",
                                   json=_body(stream=False, messages=msgs))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(called), 1)                 # 闸必须照常生效
        self.assertTrue(res.json().get("pm_clarify"))

    def test_35_taskbook_injected_into_upstream(self):
        with self._patch_pm(lambda t, f: PM_PRD_JSON):
            res = self.client.post("/api/llm/chat", json=_body(VAGUE, stream=False))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("ok"))
        self.assertNotIn("pm_clarify", data)     # 放行路径是正常出答
        self.assertEqual(len(_upstream_payloads()), 1)
        sent = _upstream_payloads()[0]
        last_user = [m for m in sent["messages"] if m.get("role") == "user"][-1]
        self.assertIn("[产品经理任务书", str(last_user.get("content")))
        self.assertIn("【背景】", str(last_user.get("content")))

    def test_36_master_switch_off(self):
        called = []

        def spy(t, f):
            called.append(t)
            return PM_QUESTIONS_JSON
        os.environ["MORAY_PM_AGENT"] = "0"
        try:
            with self._patch_pm(spy):
                res = self.client.post("/api/llm/chat", json=_body(VAGUE, stream=False))
            self.assertEqual(res.status_code, 200)
            self.assertEqual(called, [])
            self.assertEqual(len(_upstream_payloads()), 1)
        finally:
            os.environ.pop("MORAY_PM_AGENT", None)

    def test_37_openai_compat_short_circuit(self):
        # /v1/chat/completions 兼容层复用 llm_chat：澄清短路同样生效且转成 OpenAI 形状
        with self._patch_pm(lambda t, f: PM_QUESTIONS_JSON):
            res = self.client.post("/v1/chat/completions",
                                   json=_body(VAGUE, stream=False))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("choices", data)
        self.assertIn(pm_agent.CLARIFY_MARKER,
                      (data["choices"][0].get("message") or {}).get("content") or "")


if __name__ == "__main__":
    unittest.main()
