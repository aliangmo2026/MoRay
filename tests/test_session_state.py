"""
MoRay 会话状态机自动化测试（国庆冲刺 Day3）
==========================================
验证 agent_sessions（pending / running / completed / failed / interrupted）：
  1. 状态流转：入口 running → 成功 completed；失败 failed + error_msg；多步任务允许再入；
  2. needs_approval / denied 停在 running（等待点，不是失败）；未知工具 → failed；
  3. 非法流转被状态机忽略（completed → pending 不发生）；
  4. 断点恢复：mark_stale_sessions_interrupted 把重启遗留的 pending/running 标 interrupted，
     新调用可从 interrupted 恢复流转；
  5. GET /api/agent/session/{id}/status：返回完整状态行；无记录 404；
  6. last_event_id 与 Day2 事件流闭环（指向该会话最新一条 event_snapshots）。

运行方式：server\\.venv\\Scripts\\python.exe tests/test_session_state.py
"""

import os
import sqlite3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离：临时工作区 + 全新临时数据库（导入 config 前设置）----
TEST_DIR = PROJECT_ROOT / "test_session_state_workspace"
TEST_DIR.mkdir(exist_ok=True)
TEST_DB = TEST_DIR / "test_session_state.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["MORAY_WORKSPACE"] = str(TEST_DIR / "ws")
os.environ["MORAY_DB"] = str(TEST_DB)
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import agent_tools, config, crud, db

db.init_db()  # 建表（含 Day3 的 agent_sessions）


class TestSessionState(unittest.TestCase):
    """HTTP 入口驱动的状态流转（4.1 主链路）"""

    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(agent_tools.router)
        cls.client = TestClient(app)

    def _post_tool(self, sid=None, header_sid=None, **body_extra):
        body = {"args": {}}
        body.update(body_extra)
        if sid:
            body["session_id"] = sid
        headers = {"X-Session-Id": header_sid} if header_sid else {}
        return self.client.post("/api/agent/tool", json=body, headers=headers)

    def _status(self, sid):
        r = self.client.get(f"/api/agent/session/{sid}/status")
        return r

    def test_flow_running_to_completed(self):
        """成功路径：调用完成后状态 completed，error_msg 空，last_event_id 指向本次事件"""
        sid = "st-ok"
        r = self._post_tool(sid=sid, name="system_info")
        self.assertTrue(r.json().get("ok"), r.text)

        resp = self._status(sid)
        self.assertEqual(resp.status_code, 200)
        row = resp.json()["data"]
        self.assertEqual(row["status"], "completed")          # pending→running→completed
        self.assertEqual(row["error_msg"], "")
        self.assertGreater(row["last_event_id"], 0)
        self.assertTrue(row["created_at"] and row["updated_at"])

        # 与 Day2 事件流闭环：last_event_id 就是该会话最新一条事件
        conn = sqlite3.connect(config.SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            ev = conn.execute("SELECT * FROM event_snapshots WHERE id = ?",
                              (row["last_event_id"],)).fetchone()
            self.assertIsNotNone(ev)
            self.assertEqual(ev["session_id"], sid)
            self.assertEqual(ev["tool_name"], "system_info")
            max_id = conn.execute("SELECT MAX(id) AS m FROM event_snapshots WHERE session_id = ?",
                                  (sid,)).fetchone()["m"]
            self.assertEqual(max_id, row["last_event_id"])
        finally:
            conn.close()

    def test_flow_failed_with_error_msg_and_retry(self):
        """失败落 failed + error_msg；同会话再次成功可再入 running→completed 并清空错误"""
        sid = "st-fail"
        r = self._post_tool(sid=sid, name="read_file", args={"path": "ghost_file_day3.txt"})
        self.assertFalse(r.json().get("ok"))
        row = self._status(sid).json()["data"]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["error_msg"], "failed 必须记录 error_msg")
        self.assertIn("不存在", row["error_msg"])

        r = self._post_tool(sid=sid, name="system_info")  # 多步任务再入
        self.assertTrue(r.json().get("ok"))
        row = self._status(sid).json()["data"]
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["error_msg"], "", "恢复成功后应清空旧错误")

    def test_unknown_tool_marks_failed(self):
        sid = "st-unknown"
        r = self._post_tool(sid=sid, name="not_a_real_tool")
        self.assertEqual(r.status_code, 400)
        row = self._status(sid).json()["data"]
        self.assertEqual(row["status"], "failed")
        self.assertIn("未知本机工具", row["error_msg"])

    def test_needs_approval_stays_running(self):
        """待审批 = 会话进行中的等待点：状态停 running，不误判 completed/failed"""
        sid = "st-approval"
        r = self._post_tool(sid=sid, name="write_file", args={"path": "x.txt", "content": "y"})
        self.assertTrue(r.json().get("needsApproval"))
        self.assertEqual(self._status(sid).json()["data"]["status"], "running")

    def test_denied_stays_running(self):
        sid = "st-denied"
        r = self._post_tool(sid=sid, name="list_directory", decision="denied")
        self.assertTrue(r.json().get("data", {}).get("denied"))
        self.assertEqual(self._status(sid).json()["data"]["status"], "running")

    def test_session_id_from_header(self):
        sid = "st-header"
        r = self._post_tool(header_sid=sid, name="system_info")
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(self._status(sid).json()["data"]["status"], "completed")

    def test_status_api_404_for_unknown_session(self):
        resp = self._status("never-existed-session")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json().get("code"), "session_not_found")

    def test_sessions_are_isolated(self):
        """会话间状态互不影响（与 Day1 熔断/Day2 事件同口径）"""
        self._post_tool(sid="st-iso-a", name="read_file", args={"path": "missing.txt"})
        self._post_tool(sid="st-iso-b", name="system_info")
        self.assertEqual(self._status("st-iso-a").json()["data"]["status"], "failed")
        self.assertEqual(self._status("st-iso-b").json()["data"]["status"], "completed")


class TestStateMachine(unittest.TestCase):
    """crud 层状态机规则（2.2）与断点恢复（3.1）"""

    def test_illegal_transition_ignored(self):
        sid = "sm-illegal"
        crud.transition_agent_session(sid, "running", error_msg="")
        crud.transition_agent_session(sid, "completed", error_msg="")
        row = crud.transition_agent_session(sid, "pending")  # completed → pending 非法
        self.assertEqual(row["status"], "completed", "非法流转不得改变状态")
        # running → pending 也非法
        crud.transition_agent_session(sid, "running")
        row = crud.transition_agent_session(sid, "pending")
        self.assertEqual(row["status"], "running")

    def test_unknown_status_rejected(self):
        with self.assertRaises(ValueError):
            crud.transition_agent_session("sm-bad", "exploded")

    def test_fresh_session_starts_pending_then_follows_rules(self):
        """新会话首次流转 completed 非法（必须先经过 running）：只留下 pending 记录"""
        sid = "sm-fresh"
        self.assertIsNone(crud.get_agent_session(sid))
        row = crud.transition_agent_session(sid, "completed")
        self.assertEqual(row["status"], "pending", "跳级流转被拒，行仍停在初始 pending")
        row = crud.transition_agent_session(sid, "running")
        self.assertEqual(row["status"], "running")

    def test_restart_recovery_marks_stale_interrupted(self):
        """模拟进程重启：running/pending → interrupted；interrupted 可随新调用恢复"""
        crud.transition_agent_session("rec-running", "running", error_msg="")
        crud.transition_agent_session("rec-done", "running", error_msg="")
        crud.transition_agent_session("rec-done", "completed", error_msg="")

        n = crud.mark_stale_sessions_interrupted()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(crud.get_agent_session("rec-running")["status"], "interrupted")
        self.assertIn("进程重启", crud.get_agent_session("rec-running")["error_msg"])
        # 已完成的会话不受影响
        self.assertEqual(crud.get_agent_session("rec-done")["status"], "completed")
        # interrupted → running 合法（查询后重试的第一步）
        row = crud.transition_agent_session("rec-running", "running", error_msg="")
        self.assertEqual(row["status"], "running")

    def test_direct_call_path_updates_state(self):
        """不经 HTTP、直接调 _execute_tool_internal（MCP/test_security 的用法）同样有状态"""
        payload, status = agent_tools._execute_tool_internal(
            "system_info", {}, source="test", session_id="sm-direct")
        self.assertEqual(status, 200)
        row = crud.get_agent_session("sm-direct")
        self.assertEqual(row["status"], "completed")


def run_tests():
    print("=" * 60)
    print("MoRay 会话状态机自动化测试（Day3）")
    print("=" * 60)
    print(f"测试数据库: {config.SQLITE_PATH}")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestSessionState))
    suite.addTests(loader.loadTestsFromTestCase(TestStateMachine))

    result = unittest.TextTestRunner(verbosity=2).run(suite)

    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！会话状态机完好。")
    else:
        print(f"❌ 测试失败！共 {result.testsRun} 项，"
              f"失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
        for test, tb in result.failures + result.errors:
            print(f"  - {test}")
    print("=" * 60)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
