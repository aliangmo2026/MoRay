# -*- coding: utf-8 -*-
r"""弃请求取消传播单元测试（体检修复2 2026-11-14）。

修复前实证（体检压测）：7 条被客户端放弃的非流式请求在服务端继续跑完，
Ollama 16 条 ESTABLISHED 占队 10 分钟——uvicorn 对非流式响应不取消 handler。
修复后 `_cancel_on_disconnect`：重活与断连轮询竞速，客户端先走 → cancel 重活。

跑法：D:/python3.12/python.exe -m pytest tests/test_llm_proxy_disconnect.py -v
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import llm_proxy  # noqa: E402


class _FakeRequest:
    """断连行为可编程的最小 request 桩（只需 is_disconnected 协程）。"""

    def __init__(self, script):
        # script: 每次 is_disconnected 调用依次弹出的值；只剩最后一个后恒返该值
        self._script = list(script)
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]


def test_returns_result_when_client_stays_connected():
    async def _work():
        await asyncio.sleep(0.05)
        return "done"

    req = _FakeRequest([False])
    t0 = time.monotonic()
    ok, result = asyncio.run(llm_proxy._cancel_on_disconnect(req, _work()))
    assert ok is True
    assert result == "done"
    assert time.monotonic() - t0 < 2.0, "客户端在位时不应被断连轮询拖慢"


def test_cancels_work_when_client_disconnects():
    started = {"v": False}
    finished = {"v": False}

    async def _slow_work():
        started["v"] = True
        try:
            await asyncio.sleep(30)
            finished["v"] = True
            return "too late"
        except asyncio.CancelledError:
            raise

    # 第一次探测仍连接，第二次（0.5s 后）报断开
    req = _FakeRequest([False, True])
    t0 = time.monotonic()
    ok, result = asyncio.run(llm_proxy._cancel_on_disconnect(req, _slow_work()))
    elapsed = time.monotonic() - t0
    assert ok is False
    assert result is None
    assert started["v"] is True
    assert finished["v"] is False, "断开后慢工作必须被取消（不得继续跑 30s）"
    assert elapsed < 5.0, "取消必须立即生效（实际 %.1fs）" % elapsed


def test_work_wins_tight_race():
    async def _fast_work():
        return 42

    req = _FakeRequest([True])  # 即使客户端已断开，工作先完成则照常返回结果
    ok, result = asyncio.run(llm_proxy._cancel_on_disconnect(req, _fast_work()))
    assert ok is True
    assert result == 42


def test_work_exception_propagates_unchanged():
    async def _boom():
        raise RuntimeError("upstream exploded")

    req = _FakeRequest([False])
    with pytest.raises(RuntimeError):
        asyncio.run(llm_proxy._cancel_on_disconnect(req, _boom()))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
