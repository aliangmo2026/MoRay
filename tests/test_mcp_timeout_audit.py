# -*- coding: utf-8 -*-
"""[P3 2026-11-17] 工具环总时限熔断 → 在飞工具必须补 agent_tool_log 审计。

[体检 2026-11-16 实锤] execute_chat_tool 的审计在 call_tool 返回后才写；
run_server_tool_rounds 的 wait_for 一取消，在飞工具永远走不到审计点——
能力雷达对"被熔断杀死的慢器官"（scribe_scan 120s 熔断形态）是盲的。
本测试把总时限钉到 0.05s、工具执行钉到 sleep(1)，断言：
1. 环正常收尾（超时信封回灌，rounds 计数不变炸）；
2. agent_tool_log 出现 status='timeout' 的 mcp__slow__ping 审计行。
"""
import asyncio

import pytest

from server.app import config
from server.app import crud
from server.app import db
from server.app import mcp_gateway


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """把 config.SQLITE_PATH 指向本测试专属临时库（db.connect 按调用时属性取路径）。"""
    p = tmp_path / "mcp_audit.sqlite3"
    monkeypatch.setattr(config, "SQLITE_PATH", p)
    db.init_db()
    return p


def _timeout_rows():
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT tool, status, detail FROM agent_tool_log WHERE status = 'timeout'")]
    finally:
        conn.close()


def test_fused_tool_gets_timeout_audit(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "MCP_CHAT_TOTAL_TIMEOUT", 0.05)
    monkeypatch.setattr(
        config, "MCP_CHAT_INJECT_SERVERS",
        {"slow": {"prefix": "mcp__slow__", "tools": ["ping"]}}, raising=False)

    async def slow_tool(server, raw, arguments):
        await asyncio.sleep(1.0)  # 远超 0.05s 总时限 → 必被熔断取消
        return {"code": "OK", "data": {}}

    monkeypatch.setattr(mcp_gateway, "execute_chat_tool", slow_tool)

    async def fake_do_request(url, headers, payload):
        return 200, {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "mcp__slow__ping", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, ""

    payload = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
    rounds = asyncio.run(mcp_gateway.run_server_tool_rounds(
        payload, "http://fake", {}, fake_do_request, model="fake-model"))

    # 熔断分支在被 await 的 to_thread 里写审计：函数返回时必已落库
    rows = _timeout_rows()
    assert any(r["tool"] == "mcp__slow__ping" for r in rows), rows
    # 审计 detail 标明是总时限熔断（非单工具超时信封）
    assert all("MCP_TOTAL_TIMEOUT" in r["detail"] for r in rows
               if r["tool"] == "mcp__slow__ping"), rows
    # 环本身正常收尾（熔断不算异常路径）：超时工具仍回灌了 tool 结果 → 计一轮
    assert rounds == 1
