# -*- coding: utf-8 -*-
r"""MCP 工具环总时限机制测试（P0 2026-11-07）

mock 掉 do_request / execute_chat_tool，直调 mcp_gateway.run_server_tool_rounds：
  用例A（预算内）  快工具 0.1s + 第二轮无 tool_calls → 正常结束，无超时标记
  用例B（总时限）  慢工具 10s + 预算 2s → wait_for 取消，注超时信封回灌，
                  payload["_moray_mcp_timeout"]=True，末尾注入系统提示，
                  注入工具被摘除，总耗时 ≈2s（≪10s）
  用例C（协议面）  超时熔断后 messages 仍满足 OpenAI 协议（assistant.tool_calls
                  与 tool 结果一一配对）

运行：cd /d D:\MoRay && D:\python3.12\python.exe scripts\test_mcp_total_timeout.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.app import config, mcp_gateway  # noqa: E402

PASS = []


def check(name, cond, detail=""):
    PASS.append(bool(cond))
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  <" + str(detail) + ">") if detail else ""))


def _tool_call_req(name="mcp__argus__search_web"):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name, "arguments": "{\"query\": \"t\"}"}}],
        "usage": {}}}]}


async def _run_case(slow_tool: bool, budget: float):
    """起一个带 mock 的 run_server_tool_rounds，返回 (rounds, payload, elapsed)。"""
    config.MCP_CHAT_TOTAL_TIMEOUT = budget
    executed = {"n": 0}

    async def fake_do_request(url, headers, payload):
        # 第一轮：模型要工具；第二轮：模型直接给最终答案（仅用例A会走到）
        if any(m.get("role") == "tool" for m in payload.get("messages", [])):
            return 200, {"choices": [{"message": {"content": "最终答案"}, "usage": {}}]}, ""
        return 200, _tool_call_req(), ""

    async def fake_execute(server, raw, args):
        executed["n"] += 1
        if slow_tool:
            await asyncio.sleep(10.0)
        return {"code": "OK", "data": {"message": "fake"}}

    orig_exec = mcp_gateway.execute_chat_tool
    mcp_gateway.execute_chat_tool = fake_execute
    payload = {"messages": [{"role": "user", "content": "hi"}],
               "tools": [{"type": "function", "function": {"name": "mcp__argus__search_web"}}],
               "stream": False}
    t0 = time.monotonic()
    try:
        rounds = await mcp_gateway.run_server_tool_rounds(
            payload, "http://upstream", {}, fake_do_request, model="mock")
    finally:
        mcp_gateway.execute_chat_tool = orig_exec
        config.MCP_CHAT_TOTAL_TIMEOUT = 120.0
    return rounds, payload, time.monotonic() - t0, executed["n"]


def _protocol_valid(messages) -> bool:
    """assistant.tool_calls 的每个 id 都有紧随的 tool 结果（OpenAI 配对纪律）。"""
    pending_ids = set()
    for m in messages or []:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                pending_ids.add(str((tc or {}).get("id")))
        elif role == "tool":
            tid = str(m.get("tool_call_id"))
            if tid not in pending_ids:
                return False
            pending_ids.discard(tid)
    return not pending_ids


async def main_async():
    print("=" * 72)
    print("MCP 工具环总时限机制测试")
    print("=" * 72)

    # ---- 用例A：预算内正常跑完 ----
    rounds, payload, elapsed, executed = await _run_case(slow_tool=False, budget=10.0)
    print("\n[用例A] 快工具（0.1s）+ 预算 10s")
    check("正常执行工具", executed == 1, "executed=%d" % executed)
    check("无超时标记", not payload.get("_moray_mcp_timeout"))
    check("无熔断标记", not payload.get("_moray_mcp_fused"))
    check("注入工具已摘除", not isinstance(payload.get("tools"), list)
          or not payload["tools"], "tools=%r" % (payload.get("tools"),))

    # ---- 用例B：总时限熔断 ----
    rounds, payload, elapsed, executed = await _run_case(slow_tool=True, budget=2.0)
    print("\n[用例B] 慢工具（10s）+ 预算 2s")
    check("总耗时 ≈ 预算（未被 10s 工具拖死）", elapsed < 6.0,
          "%.1fs（10s 工具 vs 2s 预算）" % elapsed)
    check("超时标记落 payload", payload.get("_moray_mcp_timeout") is True)
    check("非死循环熔断（两种熔断独立）", not payload.get("_moray_mcp_fused"))
    check("工具结果已回灌（信封=超时中止）", any(
        m.get("role") == "tool" and "MCP_TOTAL_TIMEOUT" in str(m.get("content"))
        for m in payload.get("messages", [])))
    last = (payload.get("messages") or [{}])[-1]
    check("末尾注入系统提示", last.get("role") == "system"
          and "基于现有信息" in str(last.get("content", "")),
          str(last.get("content", ""))[:40])
    check("注入工具已摘除（终答轮不可再起工具轮）",
          not isinstance(payload.get("tools"), list) or not payload["tools"])

    # ---- 用例C：协议面完整性 ----
    print("\n[用例C] 熔断后 messages 协议完整性")
    check("assistant.tool_calls 与 tool 结果配对", _protocol_valid(payload.get("messages")))

    total, passed = len(PASS), sum(1 for x in PASS if x)
    print("=" * 72)
    print("RESULT: %s  (%d/%d)" % ("ALL GREEN" if passed == total else "HAS FAIL",
                                   passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
