# -*- coding: utf-8 -*-
"""p_mcp·任务3 适配层单测：本地日志三源 / 路由端点 / MCP 客户端工具函数（零真实模型）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 tests/mcp_phase/test_03_adapters.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from time import time

sys.path.insert(0, r"D:\MoRay")
os.environ.setdefault("IRIS_MCP_API_KEY", "morayIrisKey2026")

from adapters import local_log_adapter as lla  # noqa: E402
from adapters.local_log_adapter import (LocalLogAdapter, clear_terminal_buffer,
                                        push_terminal_line, summarize_for_prompt)  # noqa: E402
from adapters.iris_mcp_client import IrisMCPClient, expand_value, _base_from_sse_url  # noqa: E402

TB = ("2026-01-01 00:00:01 INFO start\n"
      "2026-01-01 00:00:02 ERROR boom failed\n"
      "Traceback (most recent call last):\n"
      '  File "worker.py", line 8, in run\n'
      "    open(p)\n"
      "FileNotFoundError: no cfg.yaml\n"
      "2026-01-01 00:00:03 WARNING retry\n")


def make_project(with_logs: bool = True) -> Path:
    proj = Path(tempfile.mkdtemp(prefix="mcp_log_"))
    if with_logs:
        (proj / "logs").mkdir()
        (proj / "logs" / "old.log").write_text("INFO ancient\n", encoding="utf-8")
        os.utime(proj / "logs" / "old.log", (time() - 3600, time() - 3600))
        (proj / "logs" / "new.log").write_text(TB, encoding="utf-8")
    return proj


class TestSource1Logs(unittest.TestCase):
    def test_latest_file_and_error_filter(self):
        c = LocalLogAdapter().collect(project_root=str(make_project()))
        self.assertTrue(c["logs"]["file"].endswith("new.log"))
        self.assertTrue(any("boom" in l for l in c["logs"]["error_lines"]))
        self.assertFalse(any("INFO start" in l for l in c["logs"]["error_lines"]))

    def test_missing_dir_graceful(self):
        c = LocalLogAdapter().collect(project_root=str(make_project(with_logs=False)))
        self.assertIsNone(c["logs"]["file"])
        self.assertFalse(c["has_error_data"])


class TestSource2Buffer(unittest.TestCase):
    def setUp(self):
        clear_terminal_buffer()

    def tearDown(self):
        clear_terminal_buffer()

    def test_cap_50_and_error_capture(self):
        for i in range(70):
            push_terminal_line(f"out{i}")
        push_terminal_line("ERROR: buffer exploded")
        c = LocalLogAdapter().collect()
        lines = c["terminal_buffer"]["lines"]
        self.assertEqual(50, len(lines))
        self.assertNotIn("out20", lines)     # 71 次 push 后只留最后 50 条（out21..out69 + ERROR）
        self.assertIn("out21", lines)
        self.assertIn("out69", lines)
        self.assertTrue(any("exploded" in l for l in c["logs"]["error_lines"]))
        self.assertTrue(c["has_error_data"])

    def test_empty_buffer(self):
        c = LocalLogAdapter().collect()
        self.assertFalse(c["terminal_buffer"]["available"])


@unittest.skipUnless(shutil.which("git"), "git 不可用")
class TestSource3Git(unittest.TestCase):
    def _git(self, d: Path, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "-C", str(d)] + list(args), check=True,
                       capture_output=True, env=env)

    def test_commit_files_listed(self):
        d = make_project(with_logs=False)
        self._git(d, "init", "-q")
        (d / "app.py").write_text("print(1)\n", encoding="utf-8")
        self._git(d, "add", "-A")
        self._git(d, "commit", "-q", "-m", "init app")
        c = LocalLogAdapter().collect(project_root=str(d))
        self.assertIsNotNone(c["git_changes"])
        self.assertIn("app.py", c["git_changes"]["files"])
        self.assertEqual("init app", c["git_changes"]["subject"])

    def test_non_repo_returns_none(self):
        c = LocalLogAdapter().collect(project_root=str(make_project(with_logs=False)))
        self.assertIsNone(c["git_changes"])


class TestIntentAndSummarize(unittest.TestCase):
    def test_keyword_list_matches(self):
        from adapters.routes import matches_crash_intent
        for kw in ("刚才程序报错了", "崩了", "跑不起来了", "出错了", "挂了", "traceback appeared"):
            self.assertTrue(matches_crash_intent(kw), kw)
        for kw in ("帮我写个函数", "今天天气", "/订阅 每天9点"):
            self.assertFalse(matches_crash_intent(kw), kw)

    def test_summarize(self):
        c = LocalLogAdapter().collect(project_root=str(make_project()))
        s = summarize_for_prompt(c)
        self.assertIn("Traceback", s)
        self.assertIn("boom", s)


class TestClientUtils(unittest.TestCase):
    def test_expand(self):
        os.environ["MCP_UT_KEY"] = "abc"
        self.assertEqual("abc", expand_value("${MCP_UT_KEY}"))
        self.assertEqual("", expand_value("${MCP_UT_MISSING}"))
        self.assertEqual(5, expand_value(5))

    def test_base_split(self):
        self.assertEqual(("http://127.0.0.1:9301", "/sse"),
                         _base_from_sse_url("http://127.0.0.1:9301/sse"))


class _StubClient:
    """替身 IrisMCPClient：路由层单测不依赖 9301/真实模型（真链路放最终 E2E）。"""
    cfg = {"enabled": True, "url": "http://127.0.0.1:9301/sse", "api_key": "x", "timeout": 30}

    async def health(self):
        return {"ok": False, "code": "IRIS_UNREACHABLE", "message": "stub"}

    async def call_tool(self, name, arguments):
        return {"code": "IRIS_UNREACHABLE", "data": {"message": "stub 不可达"}}


class TestRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from unittest import mock
        import adapters.routes as routes
        cls._patcher = mock.patch.object(routes, "IrisMCPClient", _StubClient)
        cls._patcher.start()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(routes.router)
        cls.cli = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def test_no_intent_and_empty(self):
        r = self.cli.post("/api/adapters/log-intent", json={"text": "你好"})
        self.assertEqual(400, r.status_code)
        self.assertEqual("no_intent", r.json()["code"])
        r = self.cli.post("/api/adapters/log-intent", json={"text": "  "})
        self.assertEqual(400, r.status_code)
        self.assertEqual("bad_text", r.json()["code"])

    def test_no_data_friendly(self):
        clear_terminal_buffer()
        d = str(make_project(with_logs=False))
        r = self.cli.post("/api/adapters/log-intent",
                          json={"text": "刚才崩了", "project_root": d})
        j = r.json()
        self.assertTrue(j["ok"] and not j["data"]["has_data"])
        self.assertIn("请提供截图或贴入报错文本", j["data"]["message"])

    def test_has_data_mcp_unreachable_503(self):
        r = self.cli.post("/api/adapters/log-intent",
                          json={"text": "程序报错了", "project_root": str(make_project())})
        self.assertEqual(503, r.status_code)
        self.assertEqual("IRIS_UNREACHABLE", r.json()["code"])

    def test_health_endpoint(self):
        r = self.cli.get("/api/adapters/health")
        j = r.json()
        self.assertTrue(j["ok"] and j["data"]["enabled"] is True)

    def test_health_mcp_disabled(self):
        from unittest import mock
        import adapters.routes as routes
        class Disabled(_StubClient):
            cfg = {**_StubClient.cfg, "enabled": False}
        with mock.patch.object(routes, "IrisMCPClient", Disabled):
            r = self.cli.post("/api/adapters/log-intent",
                              json={"text": "崩了", "project_root": str(make_project())})
            self.assertEqual(503, r.status_code)
            self.assertEqual("iris_disabled", r.json()["code"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
