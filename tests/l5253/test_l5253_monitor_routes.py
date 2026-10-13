# -*- coding: utf-8 -*-
"""L5.2 /api/monitor/* 路由单测：信封/入参校验/409/404/ack 动作枚举。

独立最小 FastAPI（不 import app.main → 不跑 Argus lifespan 循环，避僵尸调度器陷阱）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l5253/test_l5253_monitor_routes.py -q
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from fastapi import FastAPI                       # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402

from server.app import log_monitor                # noqa: E402
from server.app.monitor_routes import router      # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="l5253_rt_ut_"))


async def _noop_analyze(text, project_name):
    return (None, "离线跳过")


class RouteTest(unittest.TestCase):
    def setUp(self):
        # C2a 结构隔离：默认扫描目录（config.MONITOR_LOG_DIRS→MoRay 根/logs）
        # 含真实生产日志，patch 成空元组，只留 enable 传入的 project_dir 替身
        self._orig_log_dirs = log_monitor.config.MONITOR_LOG_DIRS
        log_monitor.config.MONITOR_LOG_DIRS = ()
        log_monitor.STATE_FILE = TMP / ("st_%s.json" % id(self))
        log_monitor.STATE_DIR = log_monitor.STATE_FILE.parent
        m = log_monitor.manager
        m.state = {"seen": {}, "alerts": []}
        m.enabled = False
        m.extra_dir = ""
        m.level = "ERROR"
        self._orig = log_monitor.analyze_error
        log_monitor.analyze_error = _noop_analyze
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        log_monitor.config.MONITOR_LOG_DIRS = self._orig_log_dirs
        log_monitor.analyze_error = self._orig

    def test_status_envelope(self):
        r = self.client.get("/api/monitor/status")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertFalse(d["data"]["enabled"], "默认关（红线 7）")
        self.assertIn("config_defaults", d["data"])

    def test_enable_bad_path(self):
        r = self.client.post("/api/monitor/enable", json={"project_dir": "Z:\\no_such_dir_xyz"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "path_not_found")

    def test_enable_bad_interval(self):
        r = self.client.post("/api/monitor/enable", json={"interval": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_interval")

    def test_scan_disabled_409(self):
        r = self.client.post("/api/monitor/scan")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "monitor_disabled")

    def test_enable_disable_roundtrip(self):
        r = self.client.post("/api/monitor/enable", json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["data"]["enabled"])
        r2 = self.client.post("/api/monitor/scan")
        self.assertEqual(r2.status_code, 200)
        r3 = self.client.post("/api/monitor/disable")
        self.assertEqual(r3.status_code, 200)
        self.assertFalse(r3.json()["data"]["enabled"])
        self.assertIsNone(log_monitor.manager._task)

    def test_alert_ack_flow(self):
        logs = TMP / "proj" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text(
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\nKeyError: \'missing-key\'\n',
            encoding="utf-8")
        log_monitor.manager.level = "ERROR"
        r_en = self.client.post("/api/monitor/enable",
                                json={"project_dir": str(TMP / "proj")})
        self.assertTrue(r_en.json()["ok"])
        self.client.post("/api/monitor/scan")
        alerts = self.client.get("/api/monitor/alerts").json()["data"]["alerts"]
        self.assertEqual(len(alerts), 1)
        aid = alerts[0]["id"]
        bad = self.client.post("/api/monitor/alerts/%s/ack" % aid, json={"action": "reboot"})
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/monitor/alerts/%s/ack" % aid, json={"action": "ignored"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/api/monitor/alerts").json()["data"]["alerts"], [])
        again = self.client.post("/api/monitor/alerts/%s/ack" % aid, json={"action": "ignored"})
        self.assertEqual(again.status_code, 404)
        self.client.post("/api/monitor/disable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
