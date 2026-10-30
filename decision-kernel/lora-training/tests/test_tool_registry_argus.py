# -*- coding: utf-8 -*-
r"""tool_registry ARGUS_BASE 默认端口回归测试（U-6-4，2026-10-01）。

背景：整个生态（Electron 启动器 / start_moray_full.ps1 / D:\Argus\start_argus.bat）
已统一 Argus 到 8010；旧默认 8003 已废弃。手动 `python laya_std_server.py` 拉起的
Laya 拿不到 LAYA_ARGUS_BASE 注入，落回旧默认 8003 导致 argus 搜索 ConnectError。

运行（零网络，只读模块常量）：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_tool_registry_argus.py -v
"""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.tool_registry as tool_registry


class ArgusBaseTests(unittest.TestCase):
    def tearDown(self):
        # 还原现场：清掉测试设置的 env 并重载模块，避免污染同会话其他测试
        os.environ.pop("LAYA_ARGUS_BASE", None)
        importlib.reload(tool_registry)

    def test_01_default_port_is_8010(self):
        # 不设 LAYA_ARGUS_BASE：默认值必须是 http://127.0.0.1:8010（8003 已废弃）
        os.environ.pop("LAYA_ARGUS_BASE", None)
        importlib.reload(tool_registry)
        self.assertEqual(tool_registry.ARGUS_BASE, "http://127.0.0.1:8010")

    def test_02_env_override_wins(self):
        # 设 LAYA_ARGUS_BASE：环境变量优先于默认值
        os.environ["LAYA_ARGUS_BASE"] = "http://x:9999"
        importlib.reload(tool_registry)
        self.assertEqual(tool_registry.ARGUS_BASE, "http://x:9999")


if __name__ == "__main__":
    unittest.main()
