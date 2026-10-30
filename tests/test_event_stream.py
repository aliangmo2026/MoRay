"""
MoRay 事件流完整性自动化测试（国庆冲刺 Day2）
=============================================
验证「审计并补齐事件流」：
  1. 每次工具调用（ok / error / rejected / needs_approval / denied）都落一条 event_snapshots；
  2. 事件字段完整（2.1 清单）：event_id / timestamp / session_id / tool_name / status /
     input / output / duration_ms / error_msg —— 列级 + snapshot_json 双载体；
  3. 配置操作 / 熔断事件（非 TOOL_IMPLS）同样有事件记录（operation 类型，不关联日志行）；
  4. session_id 归属：body 字段 > X-Session-Id 头 > "default" 兜底（与 llm_proxy 同约定）；
  5. db.py 幂等 migration：旧 6 列库补列不丢数据，可反复执行；
  6. 回放契约不破坏：tool_call 事件仍带 source_log_id 关联，snapshot_json 保留 tool/args 旧键。

零额外依赖（unittest + FastAPI TestClient），MORAY_DB 指向临时库，绝不碰用户主库。

运行方式：python tests/test_event_stream.py（推荐 server\\.venv\\Scripts\\python.exe）
"""

import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

# 确保能导入 server.app 模块（本文件位于 tests/ 下，工程根是上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离：临时工作区 + 临时数据库（必须在导入 config 之前设置）----
TEST_DIR = PROJECT_ROOT / "test_event_stream_workspace"
TEST_DIR.mkdir(exist_ok=True)
TEST_DB = TEST_DIR / "test_event_stream.db"
if TEST_DB.exists():
    TEST_DB.unlink()  # 每次全新库，事件计数确定

os.environ["MORAY_WORKSPACE"] = str(TEST_DIR / "ws")
os.environ["MORAY_DB"] = str(TEST_DB)
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"  # Day1 配置项，测试环境显式钉死

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import agent_tools, config, crud, db

db.init_db()  # 建表（含 Day2 新列）


# ---------------------------------------------------------------- 事件读取助手
def latest_events(event_type: str | None = None, limit: int = 5) -> list[dict]:
    """读最近的事件行（列级字段 + 解析后的 snapshot），按 id 倒序"""
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM event_snapshots WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM event_snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["snapshot"] = json.loads(d.pop("snapshot_json"))
            out.append(d)
        return out
    finally:
        conn.close()


def last_event_for(tool: str, status: str) -> dict:
    rows = latest_events(limit=200)
    for r in rows:
        if r["tool_name"] == tool and r["status"] == status:
            return r
    raise AssertionError(f"未找到 tool={tool} status={status} 的事件"
                         f"（最近 {len(rows)} 条：{[(x['tool_name'], x['status']) for x in rows]}）")


class TestEventStream(unittest.TestCase):
    """事件写入完整性（4.1）"""

    def _call(self, name, args=None, **kw):
        return agent_tools._execute_tool_internal(name, args or {}, source="test", **kw)

    def test_ok_event_has_all_fields(self):
        """ok：列级 9 字段 + snapshot canonical 键齐全，output 为真实执行结果"""
        payload, status = self._call("system_info", {}, session_id="sess-ok")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))

        ev = last_event_for("system_info", "ok")
        self.assertEqual(ev["event_type"], "tool_call")
        self.assertGreater(ev["id"], 0)                              # event_id
        self.assertTrue(ev["created_at"])                            # timestamp
        self.assertEqual(ev["session_id"], "sess-ok")                # session_id
        self.assertEqual(ev["tool_name"], "system_info")             # tool_name
        self.assertEqual(ev["status"], "ok")                         # status
        self.assertEqual(json.loads(ev["input_json"]), {})           # input（JSON）
        out = json.loads(ev["output_json"])                          # output（JSON）
        self.assertIsInstance(out, dict)
        self.assertIn("cpuCount", out, "output 应为 system_info 真实返回")  # 系统信息真有内容
        self.assertGreaterEqual(ev["duration_ms"], 0)                # duration_ms
        self.assertEqual(ev["error_msg"], "")                        # 成功无错误

        snap = ev["snapshot"]
        for key in ("session_id", "tool_name", "status", "input", "output",
                    "duration_ms", "error_msg", "timestamp", "tool", "args"):
            self.assertIn(key, snap, f"snapshot_json 缺键 {key}")
        self.assertEqual(snap["schema_version"], 2)

    def test_error_event_records_error_msg(self):
        """error（ToolError）：error_msg 非空、output 为空串"""
        payload, status = self._call("read_file", {"path": "definitely_missing_file.txt"},
                                     session_id="sess-err")
        self.assertEqual(status, 200)
        self.assertFalse(payload.get("ok"))
        ev = last_event_for("read_file", "error")
        self.assertEqual(ev["session_id"], "sess-err")
        self.assertTrue(ev["error_msg"], "error 事件必须带 error_msg")
        self.assertEqual(ev["output_json"], "", "失败不得有 output")
        self.assertEqual(json.loads(ev["input_json"])["path"], "definitely_missing_file.txt")

    def test_rejected_event_from_safety_layer(self):
        """rejected（SafeError 路径越界）：状态与原因都落事件"""
        payload, status = self._call("read_file", {"path": "C:/Windows/System32/cmd.exe"},
                                     session_id="sess-rej")
        ev = last_event_for("read_file", "rejected")
        self.assertEqual(ev["status"], "rejected")
        self.assertTrue(ev["error_msg"])
        self.assertEqual(ev["session_id"], "sess-rej")

    def test_needs_approval_and_denied_never_missed(self):
        """未审批 / 客户端拒绝也要有事件（不能只有 agent_tool_log）"""
        self._call("write_file", {"path": "never.txt", "content": "x"}, approved=False,
                   session_id="sess-appr")
        ev = last_event_for("write_file", "needs_approval")
        self.assertEqual(ev["status"], "needs_approval")
        self.assertTrue(ev["error_msg"])
        self.assertEqual(ev["output_json"], "")

        self._call("list_directory", {}, decision="denied", session_id="sess-deny")
        ev = last_event_for("list_directory", "denied")
        self.assertEqual(ev["session_id"], "sess-deny")

        self.assertFalse((Path(os.environ["MORAY_WORKSPACE"]) / "never.txt").exists(),
                         "needs_approval 绝不能真的写文件")

    def test_non_tool_operations_also_have_events(self):
        """配置操作 / Day1 熔断事件：operation 类型统一落表，不关联日志行（不长出重放按钮）"""
        agent_tools._audit("config_set_workspace", {"workspace": "D:\\somewhere"}, True, "ok", 0)
        ev = last_event_for("config_set_workspace", "ok")
        self.assertEqual(ev["event_type"], "operation")
        self.assertIsNone(ev["source_log_id"], "非工具事件不得关联 agent_tool_log")

        agent_tools._audit("budget_exceeded", {"session_id": "s-budget"}, False, "rejected", 0,
                           "session s-budget exceeded: 31/30", source="llm_proxy")
        ev = last_event_for("budget_exceeded", "rejected")
        self.assertEqual(ev["event_type"], "operation")
        # session_id 兜底解析 args.session_id（Day1 llm_proxy 的传参形状，不改其代码即可归属会话）
        self.assertEqual(ev["session_id"], "s-budget")

    def test_session_id_from_http_body_and_header(self):
        """HTTP 入口：body.session_id / X-Session-Id 头都能归属；都没有落 default"""
        app = FastAPI()
        app.include_router(agent_tools.router)
        client = TestClient(app)

        r = client.post("/api/agent/tool", json={
            "name": "list_directory", "args": {}, "session_id": "s-http"})
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(last_event_for("list_directory", "ok")["session_id"], "s-http")

        r = client.post("/api/agent/tool", json={"name": "system_info", "args": {}},
                        headers={"X-Session-Id": "s-hdr"})
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(last_event_for("system_info", "ok")["session_id"], "s-hdr")

        r = client.post("/api/agent/tool", json={"name": "system_info", "args": {}},
                        headers={"X-Session-Id": ""})
        self.assertTrue(r.json().get("ok"))
        self.assertEqual(last_event_for("system_info", "ok")["session_id"], "default")

    def test_replay_contract_preserved(self):
        """回放契约：tool_call 事件带 source_log_id 关联；list_agent_log 能取到 snapshot_id；
        snapshot 旧键 tool/args 形状不变（/api/agent/replay 读取端兼容）"""
        self._call("read_file", {"path": "replay_src.txt"}, session_id="sess-replay")
        payload, _ = self._call("system_info", {}, session_id="sess-replay")
        self.assertTrue(payload.get("ok"))

        ev = last_event_for("system_info", "ok")
        self.assertIsNotNone(ev["source_log_id"], "工具事件必须关联审计行（时间线重放按钮依赖）")
        self.assertEqual(ev["snapshot"]["tool"], "system_info")  # 回放读取键
        self.assertIsInstance(ev["snapshot"]["args"], dict)

        rows, _total = crud.list_agent_log(limit=30)
        snap_ids = {r.get("snapshot_id") for r in rows if r.get("snapshot_id")}
        self.assertIn(ev["id"], snap_ids, "GET /api/agent/log 的 snapshot_id 应指向新事件")

    def test_crud_list_returns_new_columns(self):
        """3.2 API 契约：crud/list 接口（GET /api/events/snapshots 背后）原样带出新字段"""
        # 自己先制造一条事件（用例按字母序执行，不依赖其他用例先跑）
        agent_tools._execute_tool_internal("system_info", {}, source="test",
                                           session_id="sess-list")
        rows, _total = crud.list_event_snapshots(event_type="tool_call", limit=5)
        self.assertTrue(rows)
        for r in rows:
            for key in ("id", "created_at", "session_id", "tool_name", "status",
                        "duration_ms", "error_msg", "input_json", "output_json", "snapshot"):
                self.assertIn(key, r, f"API 返回的事件缺字段 {key}")


class TestMigration(unittest.TestCase):
    """2.2 幂等 migration：旧库补列、不破坏数据、可反复执行"""

    OLD_SCHEMA = """
    CREATE TABLE agent_tool_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT '',
      tool TEXT NOT NULL, args_summary TEXT NOT NULL DEFAULT '',
      approved INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT '',
      ms INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '');
    CREATE TABLE event_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
      entity_id TEXT NOT NULL DEFAULT '', source_log_id INTEGER,
      snapshot_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')));
    """

    def test_old_db_upgraded_without_data_loss(self):
        old_db = TEST_DIR / "old_schema.db"
        if old_db.exists():
            old_db.unlink()
        conn = sqlite3.connect(old_db)
        conn.executescript(self.OLD_SCHEMA)
        conn.execute("INSERT INTO event_snapshots (event_type, entity_id, snapshot_json)"
                     " VALUES ('legacy', 'keep-me', '{\"tool\": \"x\"}')")
        conn.commit()
        conn.close()

        orig_path = config.SQLITE_PATH
        try:
            config.SQLITE_PATH = old_db
            db.init_db()  # 第一次：ALTER 补列
            db.init_db()  # 第二次：已存在列全部跳过（幂等，不炸）

            conn = sqlite3.connect(old_db)
            conn.row_factory = sqlite3.Row
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(event_snapshots)")}
                self.assertLessEqual({"session_id", "tool_name", "status", "duration_ms",
                                      "error_msg", "input_json", "output_json"}, cols)
                rows = conn.execute("SELECT * FROM event_snapshots").fetchall()
                self.assertEqual(len(rows), 1, "既有数据不得丢失")
                r = dict(rows[0])
                self.assertEqual(r["event_type"], "legacy")
                self.assertEqual(r["entity_id"], "keep-me")  # 旧行原样
                self.assertEqual(r["session_id"], "")        # 新列默认值
            finally:
                conn.close()
        finally:
            config.SQLITE_PATH = orig_path


def run_tests():
    print("=" * 60)
    print("MoRay 事件流完整性自动化测试（Day2）")
    print("=" * 60)
    print(f"测试数据库: {config.SQLITE_PATH}")
    print(f"测试工作区: {os.environ['MORAY_WORKSPACE']}")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestEventStream))
    suite.addTests(loader.loadTestsFromTestCase(TestMigration))

    result = unittest.TextTestRunner(verbosity=2).run(suite)

    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！事件流补齐且旧库迁移安全。")
    else:
        print(f"❌ 测试失败！共 {result.testsRun} 项，"
              f"失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
        for test, tb in result.failures + result.errors:
            print(f"  - {test}")
    print("=" * 60)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
