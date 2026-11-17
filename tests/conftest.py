# -*- coding: utf-8 -*-
"""[测试隔离兜底 2026-10-14] 本仓测试默认不碰生产库。

背景：`server/app/config.py` 在 import 时读 `os.environ["MORAY_MEMORY_DB"]`，
缺省即生产库 `server/data/memory.sqlite3`。若某测试 import `server.app.main`
（会跑 lifespan → self_check_loop 起手轮写 self_observation），又漏设 env，
就会污染生产库（10-14 11:39 4 条基线行的由来）。

本 conftest 用 `setdefault` 兜底：显式设了的保持（如 test_memory_bridge），
没设的自动指向临时库。防"以后新增测试忘记隔离"复发。
"""
import os
import sys
import tempfile

import pytest

os.environ.setdefault(
    "MORAY_MEMORY_DB",
    os.path.join(tempfile.gettempdir(), "moray_test_memory.sqlite3"),
)

# [测试隔离兜底 2026-11-17] MCP 注入默认关：真实 config 的 MCP_CHAT_INJECT 默认 1
# （含 iris 9301 等真实器官），llm_proxy 路径测试会起工具环 → fake upstream 被环
# 反复调用、断言计数爆炸（test_llm_proxy_identity 8 例实证），还伴随 iris 握手
# AttributeError 噪声。显式设了的测试保持原值，没设的一律隔离。
os.environ.setdefault("MORAY_MCP_ENABLED", "0")
os.environ.setdefault("MORAY_MCP_CHAT_INJECT", "0")

# [测试隔离兜底 2026-11-17] 主库同款兜底：llm_proxy 的 _record_usage_log 走
# config.SQLITE_PATH（MORAY_DB，缺省即生产库 server/data/moray.sqlite3），
# identity 等测试未隔离 → fake-model 用量行直写生产账本（2026-11-17 清出
# 67 行横跨 10-27→11-17 的测试脏数据）。与 MORAY_MEMORY_DB 同款 setdefault。
os.environ.setdefault(
    "MORAY_DB",
    os.path.join(tempfile.gettempdir(), "moray_test_moray.sqlite3"),
)


@pytest.fixture(autouse=True)
def _reset_llm_proxy_counters():
    """[测试隔离兜底 2026-11-17] llm_proxy 进程内计数桶跨测试残留清理。

    _session_calls（会话熔断）/ _toolreq_times（带工具滑动窗口）是模块级 dict，
    一个测试写入后残留到后续测试 → 组合跑时 RateLimitTests 计数被前置用例污染
    （test_cost_guard_20261114 3 例实证；单跑全绿）。用后即清，纯进程内旁路。"""
    yield
    mod = sys.modules.get("server.app.llm_proxy")
    if mod is not None:
        for _name in ("_session_calls", "_toolreq_times"):
            d = getattr(mod, _name, None)
            if isinstance(d, dict):
                d.clear()
