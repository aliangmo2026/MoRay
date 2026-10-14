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
import tempfile

os.environ.setdefault(
    "MORAY_MEMORY_DB",
    os.path.join(tempfile.gettempdir(), "moray_test_memory.sqlite3"),
)
