"""
MoRay 通用执行引擎验收测试（国庆冲刺 Day7）
==========================================
覆盖任务 4.1 的五条用例：
  a) ExecutionEngine 抽象接口不能直接实例化（缺方法的子类也不能）；
  b) LocalAdapter 正常 write_file（受理 → completed → 文件真实落盘、内容一致）；
  c) LocalAdapter 拒绝越界路径（".." 穿越 / 白名单外绝对路径 / 写 root 本身）；
  d) LocalAdapter 拒绝 exec / network 动作（以及一切非 write_file 动作）；
  e) GET /api/engines/list 返回 LocalAdapter（name=local + 能力申报与规格一致）。

隔离：LocalAdapter 的 root 注入到临时目录（构造参数仅供测试；生产默认
D:\\MoRay\\workspace\\local_engine 只断言常量本身，不往里写）；
临时工作区每次 rmtree 重建（防上次产物造成假阳性）+ 临时数据库（MORAY_DB）。

运行方式：server\\.venv\\Scripts\\python.exe -X utf8 tests/test_engines.py
"""

import os
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离（导入 config 前设置）----
TEST_DIR = PROJECT_ROOT / "test_engines_workspace"
TEST_DIR.mkdir(exist_ok=True)
WS = TEST_DIR / "ws"
# 工作区必须每次全新：残留文件会让"被拒绝的写没发生"断言假阳性
shutil.rmtree(WS, ignore_errors=True)
WS.mkdir(exist_ok=True)
ENGINE_ROOT = WS / "engine_root"   # LocalAdapter 白名单根（注入替代生产目录）
TEST_DB = TEST_DIR / "test_engines.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["MORAY_WORKSPACE"] = str(WS)
os.environ["MORAY_DB"] = str(TEST_DB)
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "memory.sqlite3")

from fastapi.testclient import TestClient

from server.app.engines.base import ExecutionEngine
from server.app.engines.local_adapter import DEFAULT_ROOT, LocalAdapter
from server.app.main import app  # 生产应用本体（验证 Day7 两行挂载真实生效）


def _new_engine() -> LocalAdapter:
    """每个用例独立的引擎 + 独立白名单根（互相不串状态）"""
    root = ENGINE_ROOT / Path(os.urandom(4).hex()).name
    return LocalAdapter(root=root)


class TestAbstractInterface(unittest.TestCase):
    """4.1-a：抽象接口不可实例化"""

    def test_execution_engine_cannot_instantiate(self):
        with self.assertRaises(TypeError):
            ExecutionEngine()  # abc 保证：直接实例化抛 TypeError

    def test_incomplete_subclass_cannot_instantiate(self):
        class HalfBaked(ExecutionEngine):
            def capabilities(self):
                return {}
        with self.assertRaises(TypeError):
            HalfBaked()  # 缺 start/status/cancel/result 任一方法都不放行

    def test_local_adapter_is_execution_engine(self):
        self.assertIsInstance(_new_engine(), ExecutionEngine)


class TestLocalAdapterWrite(unittest.TestCase):
    """4.1-b：正常 write_file"""

    def test_write_file_succeeds(self):
        eng = _new_engine()
        tid = eng.start({"action": "write_file", "path": "hello.txt", "content": "你好 Day7"})
        st = eng.status(tid)
        self.assertEqual(st["status"], "completed")

        target = eng.root / "hello.txt"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "你好 Day7")

        res = eng.result(tid)
        self.assertEqual(res["status"], "completed")
        self.assertIsNone(res["error"])
        self.assertEqual(res["result"]["path"], str(target.resolve()))
        self.assertEqual(res["result"]["bytes"], len("你好 Day7".encode("utf-8")))

    def test_write_nested_path_and_task_ids_unique(self):
        eng = _new_engine()
        t1 = eng.start({"action": "write_file", "path": "a/b/c.md", "content": "# nested"})
        t2 = eng.start({"action": "write_file", "path": "d.txt", "content": "x"})
        self.assertNotEqual(t1, t2)
        self.assertEqual(eng.status(t1)["status"], "completed")
        self.assertTrue((eng.root / "a" / "b" / "c.md").exists())
        self.assertEqual((eng.root / "a" / "b" / "c.md").read_text(encoding="utf-8"), "# nested")

    def test_status_of_unknown_task_raises(self):
        eng = _new_engine()
        with self.assertRaises(KeyError):
            eng.status("no-such-task")
        with self.assertRaises(KeyError):
            eng.result("no-such-task")
        self.assertFalse(eng.cancel("no-such-task"))

    def test_default_root_matches_spec(self):
        # 生产白名单根钉死为规格路径（2.c）；本测试不往里写任何东西
        self.assertEqual(DEFAULT_ROOT, Path(r"D:\MoRay\workspace\local_engine"))
        self.assertEqual(LocalAdapter().root, DEFAULT_ROOT)


class TestLocalAdapterRejectPath(unittest.TestCase):
    """4.1-c：越界路径一律拒绝"""

    def _assert_rejected(self, eng, path, needle="path_outside_whitelist"):
        tid = eng.start({"action": "write_file", "path": path, "content": "evil"})
        st = eng.status(tid)
        self.assertEqual(st["status"], "failed")
        self.assertIn(needle, st["error"])
        self.assertIsNone(eng.result(tid)["result"])

    def test_dotdot_traversal_rejected(self):
        eng = _new_engine()
        self._assert_rejected(eng, "..\\escape.txt")
        self._assert_rejected(eng, "a/../../escape2.txt")
        # 被拒的写必须真的没发生
        self.assertFalse((eng.root.parent / "escape.txt").exists())
        self.assertFalse((eng.root.parent.parent / "escape2.txt").exists())

    def test_absolute_outside_rejected(self):
        eng = _new_engine()
        self._assert_rejected(eng, r"D:\Windows\system32\pwned.txt")
        self._assert_rejected(eng, str(eng.root.parent / "outside.txt"))
        self.assertFalse((eng.root.parent / "outside.txt").exists())

    def test_write_root_itself_rejected(self):
        eng = _new_engine()
        self._assert_rejected(eng, ".")

    def test_missing_path_or_bad_content_rejected(self):
        eng = _new_engine()
        tid = eng.start({"action": "write_file", "path": "", "content": "x"})
        self.assertEqual(eng.status(tid)["status"], "failed")
        tid = eng.start({"action": "write_file", "path": "ok.txt", "content": 123})
        self.assertEqual(eng.status(tid)["status"], "failed")


class TestLocalAdapterRejectAction(unittest.TestCase):
    """4.1-d：exec / network 及一切非 write_file 动作拒绝"""

    def _assert_action_rejected(self, task, needle="action_not_supported"):
        eng = _new_engine()
        tid = eng.start(task)
        st = eng.status(tid)
        self.assertEqual(st["status"], "failed")
        self.assertIn(needle, st["error"])

    def test_exec_rejected(self):
        self._assert_action_rejected({"action": "exec", "command": "calc.exe"})

    def test_network_rejected(self):
        self._assert_action_rejected({"action": "network", "url": "http://example.com"})

    def test_read_rejected_even_inside_whitelist(self):
        # 本引擎不接受任何读任务 → "读白名单外文件"天然不可能
        self._assert_action_rejected({"action": "read_file", "path": "hello.txt"})

    def test_capabilities_declared_honestly(self):
        caps = _new_engine().capabilities()
        self.assertEqual(caps, {
            "can_read": True, "can_write": True,
            "can_exec": False, "can_network": False, "is_isolated": False,
        })


class TestEnginesListAPI(unittest.TestCase):
    """4.1-e：GET /api/engines/list"""

    def test_list_returns_local_adapter(self):
        with TestClient(app) as client:   # 走生产 app 本体（含 Day7 挂载的两行）
            resp = client.get("/api/engines/list")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        names = [e["name"] for e in data["engines"]]
        self.assertIn("local", names)
        local = next(e for e in data["engines"] if e["name"] == "local")
        self.assertEqual(local["capabilities"]["can_write"], True)
        self.assertEqual(local["capabilities"]["can_exec"], False)
        self.assertEqual(local["capabilities"]["can_network"], False)
        self.assertEqual(local["capabilities"]["is_isolated"], False)
        self.assertTrue(local["description"])

    def test_existing_routes_untouched(self):
        # 3.3：Day7 挂载不改变既有路由行为（抽查健康检查）
        with TestClient(app) as client:
            resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
