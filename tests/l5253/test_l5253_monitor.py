# -*- coding: utf-8 -*-
"""L5.2 主动监控单测（任务书 4.1）：开关默认关 / 开启后工作 / 关闭后停止；
告警去重（同一错误不重复告警）；级别阈值；ack 语义；状态落盘持久。

全离线：临时日志目录 + 临时状态文件 + 打桩 analyze_error（不依赖 Iris 9301）。
运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l5253/test_l5253_monitor.py -q
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

TMP = Path(tempfile.mkdtemp(prefix="l5253_mon_ut_"))

from server.app import log_monitor                   # noqa: E402
from server.app import config as appcfg              # noqa: E402  （与 log_monitor 同一 config 对象）
from server.app.log_monitor import (MonitorManager, _fingerprint,  # noqa: E402
                                    resolve_scan_dirs)

ERR_TRACE = ("Traceback (most recent call last):\n"
             '  File "svc/handler.py", line 42, in handle\n'
             "    return total / count\n"
             "ZeroDivisionError: division by zero\n")


def _fake_analyze_factory():
    """替身分析器：记录调用次数，返回固定分析（离线，不碰 9301）。"""
    calls = []

    async def fake(text, project_name):
        calls.append((text, project_name))
        return ({"root_cause": "除零（替身）", "suggestion": "除法前判空（替身）",
                 "severity": "high", "candidates": [], "source": "fake"}, "")
    return fake, calls


class MonitorBase(unittest.TestCase):
    def setUp(self):
        # C2a 结构隔离：默认扫描目录（config.MONITOR_LOG_DIRS→MoRay 根/logs）
        # 含真实生产日志，patch 成空元组，只留本用例 extra_dir 注入的临时替身目录
        self._orig_log_dirs = appcfg.MONITOR_LOG_DIRS
        appcfg.MONITOR_LOG_DIRS = ()
        self.logs = TMP / ("proj_%s" % id(self)) / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        log_monitor.STATE_FILE = TMP / ("state_%s.json" % id(self))
        log_monitor.STATE_DIR = log_monitor.STATE_FILE.parent
        self.mgr = MonitorManager()
        self.mgr.state = {"seen": {}, "alerts": []}
        self.mgr.extra_dir = str(self.logs.parent)
        self.mgr.level = "ERROR"
        self.mgr.interval = 60
        self._fake, self._calls = _fake_analyze_factory()
        self._orig_analyze = log_monitor.analyze_error
        log_monitor.analyze_error = self._fake

    def tearDown(self):
        log_monitor.analyze_error = self._orig_analyze
        appcfg.MONITOR_LOG_DIRS = self._orig_log_dirs

    def _run(self, coro):
        return asyncio.run(coro)


class SwitchTest(MonitorBase):
    def test_default_off_env_config(self):
        """红线 7：默认关闭——config.MONITOR_ENABLED 未显式开启时为 false。"""
        self.assertFalse(appcfg.MONITOR_ENABLED)

    def test_enable_starts_loop_and_disable_stops(self):
        async def scenario():
            await self.mgr.enable(project_dir=str(self.logs.parent), interval=2)
            self.assertTrue(self.mgr.enabled)
            self.assertIsNotNone(self.mgr._task)
            self.assertFalse(self.mgr._task.done())
            await self.mgr.disable()
            self.assertFalse(self.mgr.enabled)
            self.assertIsNone(self.mgr._task)
        self._run(scenario())

    def test_loop_actually_scans(self):
        """开启后循环真的在扫：interval=1，写错误日志 → 队列里出现告警。"""
        async def scenario():
            await self.mgr.enable(project_dir=str(self.logs.parent), interval=1)
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            for _ in range(60):     # 最多等 6s
                if self.mgr.pending_alerts():
                    break
                await asyncio.sleep(0.1)
            await self.mgr.disable()
            return self.mgr.pending_alerts()
        pend = self._run(scenario())
        self.assertEqual(len(pend), 1, pend)
        self.assertIn("ZeroDivisionError", pend[0]["stack"])
        self.assertEqual(pend[0]["analysis"]["root_cause"], "除零（替身）")

    def test_disable_stops_new_alerts(self):
        async def scenario():
            await self.mgr.enable(project_dir=str(self.logs.parent), interval=1)
            await self.mgr.disable()
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            await asyncio.sleep(0.3)
            return self.mgr.pending_alerts(), self.mgr._task
        pend, task = self._run(scenario())
        self.assertEqual(pend, [])   # 关闭后不再扫描
        self.assertIsNone(task)


class DedupTest(MonitorBase):
    def test_same_error_not_realerted(self):
        """任务书 2.2/4.1：重复错误不重复告警（时间戳/行号变化同指纹）。"""
        async def scenario():
            (self.logs / "app.log").write_text(
                "2026-09-28 10:00:00 ERROR boom\n" + ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            (self.logs / "app.log").write_text(
                "2026-09-28 12:34:56 ERROR boom\n" + ERR_TRACE.replace("line 42", "line 77"),
                encoding="utf-8")
            await self.mgr.scan_once()
            return self.mgr.pending_alerts()
        pend = self._run(scenario())
        self.assertEqual(len(pend), 1, "同错误（仅时间/行号变）必须收敛为一个告警")

    def test_different_error_new_alert(self):
        async def scenario():
            (self.logs / "a.log").write_text(ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            (self.logs / "b.log").write_text(
                "FATAL panic: something totally different exploded\n", encoding="utf-8")
            await self.mgr.scan_once()
            return self.mgr.pending_alerts()
        pend = self._run(scenario())
        self.assertEqual(len(pend), 2, pend)

    def test_seen_persisted_across_restart(self):
        async def scenario():
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            self.mgr.save()
            reloaded = MonitorManager()
            reloaded.state = log_monitor._load_state()
            reloaded.extra_dir = str(self.logs.parent)
            reloaded.level = "ERROR"
            await reloaded.scan_once()
            return reloaded.pending_alerts()
        pend = self._run(scenario())
        self.assertEqual(len(pend), 1, "重启后 seen 仍生效，不重复告警")


class LevelGateTest(MonitorBase):
    def test_fatal_threshold_ignores_error_only(self):
        async def scenario():
            self.mgr.level = "FATAL"
            (self.logs / "app.log").write_text(
                "2026-09-28 10:00:00 ERROR plain error line here\n", encoding="utf-8")
            await self.mgr.scan_once()
            first = self.mgr.pending_alerts()
            (self.logs / "app.log").write_text(
                "2026-09-28 10:00:00 FATAL process aborted with core dumped\n", encoding="utf-8")
            await self.mgr.scan_once()
            return first, self.mgr.pending_alerts()
        first, second = self._run(scenario())
        self.assertEqual(first, [], "FATAL 阈值下 ERROR 行不告警")
        self.assertEqual(len(second), 1)

    def test_pure_traceback_passes_error_gate(self):
        """Python Traceback 不含 ERROR 字样，但 ERROR 阈值下必须告警（异常即错误）。"""
        async def scenario():
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            return self.mgr.pending_alerts()
        self.assertEqual(len(self._run(scenario())), 1)


class AckTest(MonitorBase):
    def test_ack_transitions_and_idempotent(self):
        async def scenario():
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            a = self.mgr.pending_alerts()[0]
            got = self.mgr.ack(a["id"], "ignored")
            again = self.mgr.ack(a["id"], "ignored")
            return a["id"], got, again, self.mgr.pending_alerts()
        aid, got, again, pend = self._run(scenario())
        self.assertEqual(got["status"], "acked")
        self.assertEqual(got["action"], "ignored")
        self.assertIsNone(again, "重复 ack 返回 None（路由层 404 语义）")
        self.assertEqual(pend, [])

    def test_fixing_action_recorded(self):
        async def scenario():
            (self.logs / "app.log").write_text(ERR_TRACE, encoding="utf-8")
            await self.mgr.scan_once()
            a = self.mgr.pending_alerts()[0]
            return self.mgr.ack(a["id"], "fixing")
        rec = self._run(scenario())
        self.assertEqual(rec["action"], "fixing")


class HelperTest(unittest.TestCase):
    def setUp(self):
        # C2a 结构隔离：防真实 D:\MoRay\logs 混入计数（rollup 用例经 scan_once 扫默认目录）
        self._orig_log_dirs = appcfg.MONITOR_LOG_DIRS
        appcfg.MONITOR_LOG_DIRS = ()

    def tearDown(self):
        appcfg.MONITOR_LOG_DIRS = self._orig_log_dirs

    def test_fingerprint_stable_under_noise(self):
        fp1 = _fingerprint('2026-09-28 10:00:00 ERROR at 0x7ffd1a in "module_x" line 42')
        fp2 = _fingerprint('2026-10-02 23:59:59 ERROR at 0x00aa11 in "module_y" line 99')
        self.assertEqual(fp1, fp2, "时间/地址/引号内容/行号归一后同指纹")

    def test_resolve_scan_dirs_relative_and_dedup(self):
        """相对路径按 MoRay 根解析；重复目录去重；enable 传项目时 logs/ 子目录优先。"""
        old = appcfg.MONITOR_LOG_DIRS
        try:
            appcfg.MONITOR_LOG_DIRS = ("logs", "logs")
            dirs = resolve_scan_dirs(str(TMP))
            keys = [str(d).lower() for d in dirs]
            self.assertEqual(len(keys), len(set(keys)), "目录去重")
        finally:
            appcfg.MONITOR_LOG_DIRS = old

    def test_alert_project_dir_rolls_up_from_logs(self):
        """扫 {proj}/logs 时告警归属项目根（立即修复的 project_dir 不能是 logs/）。"""
        log_monitor.STATE_FILE = TMP / "state_rollup.json"
        log_monitor.STATE_DIR = log_monitor.STATE_FILE.parent
        mgr = MonitorManager()
        mgr.state = {"seen": {}, "alerts": []}
        mgr.extra_dir = str(TMP / "rollup")
        mgr.level = "ERROR"
        logs = TMP / "rollup" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "a.log").write_text(ERR_TRACE, encoding="utf-8")
        orig = log_monitor.analyze_error

        async def fake(text, project_name):
            return (None, "离线跳过")
        log_monitor.analyze_error = fake
        try:
            asyncio.run(mgr.scan_once())
        finally:
            log_monitor.analyze_error = orig
        pend = mgr.pending_alerts()
        self.assertEqual(len(pend), 1)
        self.assertEqual(Path(pend[0]["project_dir"]).name, "rollup")


if __name__ == "__main__":
    unittest.main(verbosity=2)
