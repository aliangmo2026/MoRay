# -*- coding: utf-8 -*-
"""tests/test_intent_endpoint.py — L7.8 /api/intent/decide 端点测试（2026-10-01）。

运行（cwd 必须是 lora-training，保证 import 命中工程内模块）：
    cd /d D:\\MoRay\\decision-kernel\\lora-training
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_intent_endpoint.py -v

隔离说明：laya_std_server 在 import 时会以 append 模式打开 unknown_scenarios.log
（cwd 相对路径），故在本模块 import 它之前先 chdir 到临时目录，避免污染工程目录。

输入文本设计依据 intent_router 规则层（_rule_decide）：
  * <30 字且无 命令动词/绝对路径/```围栏 → 规则层直接判 chat（decided_by=rule）；
  * 要走到 L3 分类器（test_07/test_10），文本必须 ≥30 字且不命中任何 agent 信号。
"""
import asyncio
import os
import socket
import tempfile
import time

_TMP_DIR = tempfile.mkdtemp(prefix="laya_intent_ep_test_")
os.chdir(_TMP_DIR)          # 必须在 import laya_std_server 之前执行，隔离其落盘副作用

import httpx
import pytest
from fastapi.testclient import TestClient

import intent_router
import laya_std_server
from laya_std_server import app

client = TestClient(app)

# 中性长文本：≥30 字、无命令动词/绝对路径/围栏/后缀 → 规则层放行，落到 L3 分类器
_LONG_NEUTRAL = "今天天气看起来非常不错我们一起出门去公园散散步顺便聊聊最近的生活状态怎么样呀"

ENDPOINT = "/api/intent/decide"


def _post(payload: dict):
    return client.post(ENDPOINT, json=payload)


# ---- 1. 空字符串 → 规则层判 chat ----
def test_01_empty_text_returns_chat_by_rule():
    r = _post({"text": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["decided_by"] == "rule"
    assert body["confidence"] == pytest.approx(0.90)


# ---- 2. 问候语 → chat（<30 字无 agent 信号，规则层短路） ----
def test_02_greeting_returns_chat():
    r = _post({"text": "你好"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["intent_type"] == "chat"


# ---- 3. 代码意图（命令动词"重构"）→ agent/code ----
def test_03_code_intent_returns_agent():
    r = _post({"text": "帮我重构这个函数"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "agent"
    assert body["intent_type"] == "code"
    assert body["decided_by"] == "rule"


# ---- 4. 绝对路径 + 创建动词 → agent/file_op ----
def test_04_path_intent_returns_agent():
    r = _post({"text": r"创建 D:\MoRay\workspace\a.py"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "agent"
    assert body["intent_type"] == "file_op"
    assert body["decided_by"] == "rule"


# ---- 5. explicit_mode=agent 覆盖规则层（L1 短路） ----
def test_05_explicit_agent_overrides():
    r = _post({"text": "你好", "explicit_mode": "agent"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "agent"
    assert body["decided_by"] == "explicit"
    assert body["confidence"] == 1.0


# ---- 6. explicit_mode=chat 覆盖规则层（L1 短路） ----
def test_06_explicit_chat_overrides():
    r = _post({"text": "重构函数", "explicit_mode": "chat"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["decided_by"] == "explicit"


# ---- 7. 分类器超时 → 降级 chat（不实际调 Ollama） ----
def test_07_classifier_timeout_falls_back_to_chat(monkeypatch):
    def _boom(_text):
        raise TimeoutError("timed out")

    monkeypatch.setattr(intent_router, "_classify_raw", _boom)
    r = _post({"text": _LONG_NEUTRAL})     # ≥30 字中性文本才会走到 L3
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["decided_by"] == "fallback"


# ---- 8. 事件循环不被阻塞：并发 10 个 100ms 慢判定，总耗时 <500ms ----
def test_08_non_blocking_event_loop(monkeypatch):
    def slow_decide(_inp):
        time.sleep(0.1)     # 模拟 decide() 的同步阻塞（如 urllib 调 Ollama）
        return intent_router.IntentResult(
            mode="chat", intent_type="chat", confidence=0.0,
            tools_hint=[], reason="slow-stub", decided_by="fallback")

    # 端点通过模块全局名 _intent_decide 调用，替换它即替换 decide
    monkeypatch.setattr(laya_std_server, "_intent_decide", slow_decide)

    async def _run_concurrent():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            async def one(i: int):
                resp = await ac.post(ENDPOINT, json={"text": "随便聊聊天 %d" % i})
                assert resp.status_code == 200
                return resp.json()
            t0 = time.perf_counter()
            results = await asyncio.gather(*(one(i) for i in range(10)))
            return time.perf_counter() - t0, results

    elapsed, results = asyncio.run(_run_concurrent())
    assert all(r["code"] == 0 and r["decided_by"] == "fallback" for r in results)
    # 若端点直接同步调用 decide，gather 会串行执行 10×100ms ≈ 1000ms；
    # asyncio.to_thread 生效时 10 个线程池任务并行，总耗时约 100~200ms
    assert elapsed < 0.5, "10 并发总耗时 %.0fms，疑似阻塞事件循环（阈值 500ms）" % (elapsed * 1000)


# ---- 9. 成功响应严格 8 键 ----
def test_09_response_schema_keys():
    r = _post({"text": "你好"})
    assert r.status_code == 200
    body = r.json()
    expected = {"code", "mode", "intent_type", "confidence",
                "tools_hint", "reason", "decided_by", "latency_ms"}
    assert expected.issubset(body.keys()), "缺少键: %s" % (expected - set(body.keys()))
    assert body["code"] == 0
    assert isinstance(body["tools_hint"], list)
    assert isinstance(body["latency_ms"], int)
    assert isinstance(body["confidence"], float)


# ---- 10. 全链路无非 127.0.0.1 出站连接（socket 层审计） ----
def test_10_no_non_localhost_outbound(monkeypatch):
    seen = []

    _real_connect = socket.socket.connect

    def _rec_connect(self, address):
        seen.append(address)
        return _real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _rec_connect)

    r = _post({"text": _LONG_NEUTRAL})     # 走到 L3，若 Ollama 在跑则是 127.0.0.1:11434
    assert r.status_code == 200

    hosts = [a[0] if isinstance(a, tuple) else str(a) for a in seen]
    bad = [h for h in hosts if h not in ("127.0.0.1", "localhost", "::1")]
    assert not bad, "发现非本机出站连接: %s（全部连接: %s）" % (bad, hosts)


# ---- 11. 分类器超时 → HTTP 端点返回 mode=chat（任务A v2 回归保护：换模型/改超时后硬保险不破） ----
def test_11_classifier_timeout_returns_chat_regression(monkeypatch):
    def _timeout(_text):
        raise TimeoutError("timed out")

    monkeypatch.setattr(intent_router, "_classify_raw", _timeout)
    r = _post({"text": _LONG_NEUTRAL})     # ≥30 字中性文本才会走到 L3
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["decided_by"] == "fallback"
    assert body["confidence"] == 0.0
    assert isinstance(body["latency_ms"], int)


# ---- 12. v4 L2 动词+名词规则回归：v3 端点层判错的 6 条 agent 样本 ----
# （#1 "查一下…政策" 在 v4 决策下判 chat；2026-10-01 U-6-3 搜索动词强信号规则生效后
#   单命中即 agent（0.85/search）——本样本同步翻转，防规则回退）
_V3_MISS_SAMPLES = [
    ("查一下最新的新能源补贴政策", "agent"),
    ("把这段代码里的 bug 修一下", "agent"),
    ("帮我写个 Python 脚本批量重命名文件", "agent"),
    ("分析一下这个日志里的错误原因", "agent"),
    ("帮我设计一个用户登录的数据库表结构", "agent"),
    ("看看我桌面上那个文件里写了什么", "agent"),
]


def test_12_v4_rule_layer_agent_samples():
    for text, expected in _V3_MISS_SAMPLES:
        r = _post({"text": text})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0, text
        assert body["mode"] == expected, "文本=%r 期望=%s 实际=%s" % (text, expected, body["mode"])
        assert body["decided_by"] == "rule", text


# ---- 13. v5 回归：移除"需求"名词后"帮我看看这个需求怎么实现"不再命中动词+名词组合 ----
# （v4 被"看看+需求"判 agent；v5 该句 12 字<30 落 L2 短文本规则，零模型调用判 chat）
def test_13_requirement_text_returns_chat():
    r = _post({"text": "帮我看看这个需求怎么实现"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "chat"
    assert body["decided_by"] == "rule"
    assert body["confidence"] == pytest.approx(0.90)


# ---- 14. U-6-3 回归：用户实况原句走搜索动词强信号 → agent/search/rule ----
# （2026-10-01 用户输入本句被 <30 字短文本规则判 chat，前端走本地聊天收到判定模型裸 JSON）
def test_14_u63_search_verb_policy_query_is_agent():
    r = _post({"text": "查一下 2026 年最新的新能源补贴政策"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["mode"] == "agent"
    assert body["intent_type"] == "search"
    assert body["decided_by"] == "rule"
    assert body["confidence"] == pytest.approx(0.85)
