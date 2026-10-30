# -*- coding: utf-8 -*-
"""cloud_budget_guard 单元测试（任务二新增，2026-09-30）。

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_cloud_budget_guard.py -v

隔离：主库（事件落点）与预算库均指向 test_cloud_budget_workspace\\，不碰用户数据；
每条用例用独立临时预算库文件（setUp 创建 / tearDown 删除），互不残留。
零真实网络（用例 10 用 socket 守卫证明：预算闸门只碰本地 SQLite）。
"""
import os
import socket
import sqlite3
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_cloud_budget_workspace"
TEST_DIR.mkdir(exist_ok=True)
# 必须在导入 server.app.config 之前设置（config 在导入期读 env）
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MORAY_BUDGET_DB"] = str(TEST_DIR / "bootstrap_budget.sqlite3")

from server.app import cloud_budget_guard as guard
from server.app import config, crud, db

db.init_db()  # event_snapshots schema：Cloud* 事件经 crud 落主库（隔离库）


class GuardTestBase(unittest.TestCase):
    """每条用例：独立临时预算库 + 显式钉死配置（不改全局默认语义）。"""

    _FIELDS = ("ENABLE_CLOUD_API", "ENABLE_CLOUD_FALLBACK", "DAILY_BUDGET_USD",
               "DAILY_MAX_CALLS", "PER_CALL_BUDGET_USD", "MORAY_BUDGET_DB")

    def setUp(self):
        self._saved = {name: getattr(config, name) for name in self._FIELDS}
        self.budget_db = TEST_DIR / ("budget_%s.sqlite3" % uuid.uuid4().hex[:8])
        config.MORAY_BUDGET_DB = str(self.budget_db)
        config.ENABLE_CLOUD_API = True
        config.ENABLE_CLOUD_FALLBACK = False
        config.DAILY_BUDGET_USD = 1.0
        config.DAILY_MAX_CALLS = 100
        config.PER_CALL_BUDGET_USD = 0.1

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(config, name, value)
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(self.budget_db) + suffix)
            if p.exists():
                p.unlink()

    def _set_total(self, value):
        """直接改当日行 total_usd（测试 5"额度恢复"用），熔断位不动。"""
        conn = sqlite3.connect(config.MORAY_BUDGET_DB)
        try:
            conn.execute("UPDATE budget_daily SET total_usd = ? WHERE date = ?",
                         (value, guard._today()))
            conn.commit()
        finally:
            conn.close()

    def _res_row(self, permit_id):
        """读 budget_reservations 单行 (status, settled_usd)（refund 状态断言用）。"""
        conn = sqlite3.connect(config.MORAY_BUDGET_DB)
        try:
            return conn.execute("SELECT status, settled_usd FROM budget_reservations "
                                "WHERE permit_id = ?", (permit_id,)).fetchone()
        finally:
            conn.close()


class AcquireGateTests(GuardTestBase):
    def test_01_cloud_disabled_raises(self):
        config.ENABLE_CLOUD_API = False
        with self.assertRaises(guard.CloudDisabled):
            guard.acquire_explicit("t01", 0.01)

    def test_02_fallback_flag_required(self):
        # L1：API 开、fallback 关 → fallback 入口必须拒绝
        config.ENABLE_CLOUD_API = True
        config.ENABLE_CLOUD_FALLBACK = False
        with self.assertRaises(guard.CloudNotForFallback):
            guard.acquire_fallback("t02", 0.01)

    def test_11_explicit_ignores_fallback_flag(self):
        # 校准 2：显式路径不受 fallback 开关约束（同条件下 acquire_explicit 成功）
        config.ENABLE_CLOUD_API = True
        config.ENABLE_CLOUD_FALLBACK = False
        permit = guard.acquire_explicit("t11", 0.05)
        self.assertIsInstance(permit, guard.CloudPermit)
        self.assertTrue(permit.permit_id)
        self.assertEqual(permit.trace_id, "t11")
        self.assertEqual(permit.reserved_usd, 0.05)
        state = guard.get_daily_state()
        self.assertAlmostEqual(state["total_usd"], 0.05)
        self.assertEqual(state["call_count"], 1)

    def test_12_fallback_raises_same_condition(self):
        config.ENABLE_CLOUD_API = True
        config.ENABLE_CLOUD_FALLBACK = False
        with self.assertRaises(guard.CloudNotForFallback):
            guard.acquire_fallback("t12", 0.05)


class BudgetLimitTests(GuardTestBase):
    def test_03_single_call_over_per_call_limit(self):
        config.PER_CALL_BUDGET_USD = 0.1
        with self.assertRaises(guard.BudgetExceeded):
            guard.acquire_explicit("t03", 0.2)
        self.assertTrue(guard.get_daily_state()["tripped"], "任一超限都应打开熔断")

    def test_04_daily_budget_exceeded_trips(self):
        config.DAILY_BUDGET_USD = 0.1
        config.PER_CALL_BUDGET_USD = 0.1
        guard.acquire_explicit("t04a", 0.06)                       # total 0.06
        with self.assertRaises(guard.BudgetExceeded):              # 0.06+0.05 > 0.1
            guard.acquire_explicit("t04b", 0.05)
        state = guard.get_daily_state()
        self.assertEqual(int(state["tripped"]), 1)
        rows, _ = crud.list_event_snapshots(event_type="CloudBudgetTripped")
        self.assertTrue(any(r["snapshot"].get("trace_id") == "t04b" for r in rows),
                        "熔断应写 CloudBudgetTripped 事件")

    def test_05_tripped_latches_until_reset(self):
        config.DAILY_BUDGET_USD = 0.1
        config.PER_CALL_BUDGET_USD = 0.1
        guard.acquire_explicit("t05a", 0.06)
        with self.assertRaises(guard.BudgetExceeded):
            guard.acquire_explicit("t05b", 0.05)                   # 触发熔断
        self._set_total(0.0)                                       # 人为"恢复额度"（不动 tripped）
        with self.assertRaises(guard.BudgetExceeded):
            guard.acquire_explicit("t05c", 0.01)                   # 仍必须拒绝
        guard.reset_circuit()
        permit = guard.acquire_explicit("t05d", 0.01)              # 重置后放行
        self.assertEqual(permit.reserved_usd, 0.01)

    def test_06_unreadable_budget_db_fails_closed(self):
        config.MORAY_BUDGET_DB = str(TEST_DIR)                     # 路径指向目录 → 打不开
        with self.assertRaises(guard.BudgetStateUnavailable):
            guard.acquire_explicit("t06", 0.01)

    def test_07_concurrent_only_one_wins(self):
        # 0.875/0.5/0.25 全是二进制精确值，规避浮点边界抖动：
        # 预占 0.5 → 剩余 0.375，恰好容得下一次 0.25、容不下第二次。
        config.DAILY_BUDGET_USD = 0.875
        config.PER_CALL_BUDGET_USD = 0.5
        config.DAILY_MAX_CALLS = 100
        guard.acquire_explicit("t07-fill", 0.5)
        barrier = threading.Barrier(10)
        results = {"ok": [], "exceeded": 0, "other": []}
        lock = threading.Lock()

        def worker(n):
            barrier.wait()
            try:
                p = guard.acquire_explicit("t07-race-%d" % n, 0.25)
                with lock:
                    results["ok"].append(p.permit_id)
            except guard.BudgetExceeded:
                with lock:
                    results["exceeded"] += 1
            except Exception as e:  # noqa: BLE001 - 测试收集器：任何非预期异常都要浮出
                with lock:
                    results["other"].append(repr(e))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(results["ok"]), 1, str(results))
        self.assertEqual(results["exceeded"], 9, str(results))
        self.assertEqual(results["other"], [], str(results))

    def test_08_cross_day_auto_reset(self):
        config.DAILY_BUDGET_USD = 0.1
        config.PER_CALL_BUDGET_USD = 0.1
        guard.acquire_explicit("t08a", 0.06)
        with self.assertRaises(guard.BudgetExceeded):
            guard.acquire_explicit("t08b", 0.05)                   # 今日熔断
        conn = sqlite3.connect(config.MORAY_BUDGET_DB)             # 伪造 date 字段 → 变成"昨天"
        try:
            conn.execute("UPDATE budget_daily SET date = '2000-01-01'")
            conn.commit()
        finally:
            conn.close()
        permit = guard.acquire_explicit("t08c", 0.05)              # 新日期 = 全新额度 + 熔断不复现
        self.assertEqual(permit.reserved_usd, 0.05)
        state = guard.get_daily_state()
        self.assertAlmostEqual(state["total_usd"], 0.05)
        self.assertFalse(state["tripped"])

    def test_09_settle_reflects_actual_not_reserved(self):
        permit = guard.acquire_explicit("t09", 0.06)
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], 0.06)
        guard.settle(permit, 0.02)
        state = guard.get_daily_state()
        self.assertAlmostEqual(state["total_usd"], 0.02, msg="结算后 total 应反映 actual")
        rows, _ = crud.list_event_snapshots(event_type="CloudCallSettled")
        self.assertTrue(any(r["snapshot"].get("trace_id") == "t09" for r in rows),
                        "结算应写 CloudCallSettled 事件")

    def test_10_no_non_localhost_outbound(self):
        outside = []
        original_connect = socket.socket.connect

        def guarded(sock, address):
            if isinstance(address, tuple) and address and isinstance(address[0], str):
                if address[0] not in ("127.0.0.1", "::1", "localhost"):
                    outside.append(address)
            return original_connect(sock, address)

        with mock.patch.object(socket.socket, "connect", guarded):
            guard.acquire_explicit("t10a", 0.01)
            permit = guard.acquire_explicit("t10b", 0.01)
            guard.settle(permit, 0.005)
            guard.get_daily_state()
            config.ENABLE_CLOUD_API = False
            with self.assertRaises(guard.CloudDisabled):
                guard.acquire_explicit("t10c", 0.01)
        self.assertEqual(outside, [], "预算闸门不得产生任何非 127.0.0.1 出站")

    def test_13_settle_refund_and_overage_floor(self):
        # [任务二点五] actual=0.0 = 全额退款；actual > reserved 差额补记；total 恒有下限 0.0。
        config.DAILY_BUDGET_USD = 1.0
        config.PER_CALL_BUDGET_USD = 0.1
        p1 = guard.acquire_explicit("t13a", 0.06)          # total 0.06
        guard.settle(p1, 0.0)                              # 全额退款 → total = 0.06 - 0.06
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], 0.0)
        p2 = guard.acquire_explicit("t13b", 0.06)          # total 0.06
        guard.settle(p2, 0.12)                             # actual > reserved：差额 0.06 补记
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], 0.12)
        p3 = guard.acquire_explicit("t13c", 0.06)          # total 0.18
        self._set_total(0.02)                              # 人为压低账面（模拟其他调整）
        guard.settle(p3, 0.0)                              # 0.02 - 0.06 = -0.04 → 下限钉在 0.0
        state = guard.get_daily_state()
        self.assertAlmostEqual(state["total_usd"], 0.0)
        self.assertGreaterEqual(state["total_usd"], 0.0)


class RefundTests(GuardTestBase):
    """[2026-10-01 Z-3] refund：上游失败路径的全额回冲（区别于 settle 的成功路径真值化）。"""

    def test_14_refund_restores_total_to_original(self):
        # 原值 + reserve - reserve = 原值：新库原值 0.0，预扣 0.06 后回冲应回到 0.0
        config.DAILY_BUDGET_USD = 1.0
        config.PER_CALL_BUDGET_USD = 0.1
        before = guard.get_daily_state()["total_usd"]
        permit = guard.acquire_explicit("t14", 0.06)
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], before + 0.06)
        guard.refund(permit, reason="upstream_error_status_500")
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], before,
                               msg="回冲后 total 应回到预扣前原值")
        rows, _ = crud.list_event_snapshots(event_type="CloudCallRefunded")
        self.assertTrue(any(r["snapshot"].get("trace_id") == "t14" for r in rows),
                        "回冲应写 CloudCallRefunded 事件")

    def test_15_refund_idempotent_no_double_refund(self):
        config.DAILY_BUDGET_USD = 1.0
        config.PER_CALL_BUDGET_USD = 0.1
        permit = guard.acquire_explicit("t15", 0.06)
        guard.refund(permit, reason="first")
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], 0.0)
        guard.refund(permit, reason="second")               # 重复调用：不抛异常、不二次回冲
        self.assertAlmostEqual(guard.get_daily_state()["total_usd"], 0.0,
                               msg="重复 refund 不得把账面扣成负数或多回冲")
        row = self._res_row(permit.permit_id)
        self.assertEqual(row[0], "refunded")
        self.assertAlmostEqual(float(row[1]), 0.0)

    def test_16_refund_marks_reservation_refunded(self):
        config.DAILY_BUDGET_USD = 1.0
        config.PER_CALL_BUDGET_USD = 0.1
        permit = guard.acquire_explicit("t16", 0.05)
        guard.refund(permit, reason="test_16")
        row = self._res_row(permit.permit_id)
        self.assertEqual(row[0], "refunded", "回冲后 status 应为 refunded")
        self.assertAlmostEqual(float(row[1]), 0.0, "settled_usd 应钉 0.0（全额回冲口径）")

    def test_17_refund_does_not_restore_call_count(self):
        # 失败也计入次数（防刷）：refund 只回冲金额，不动 call_count
        config.DAILY_BUDGET_USD = 1.0
        config.PER_CALL_BUDGET_USD = 0.1
        permit = guard.acquire_explicit("t17", 0.05)
        self.assertEqual(guard.get_daily_state()["call_count"], 1)
        guard.refund(permit, reason="test_17")
        state = guard.get_daily_state()
        self.assertEqual(state["call_count"], 1, "refund 不得回冲 call_count")
        self.assertAlmostEqual(state["total_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
