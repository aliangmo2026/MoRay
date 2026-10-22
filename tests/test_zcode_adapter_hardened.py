"""
MoRay ZCodeAdapter 真身验收测试（国庆冲刺 Day15）
==================================================
覆盖 Day15 接线（骨架 → 真身）的七组要求：
  a) start() 后 event_snapshots 落一条 zcode_task_started（含 task_id/input/session_id）；
  b) agent_sessions 状态 pending（start）→ running（_worker 入口）；
  c) 任务完成后状态转 completed（且 finished 事件 + last_event_id 闭环）；
  d) 任务失败后状态转 failed + error_msg（非零退出码 / 超时两条路径）；
  e) capabilities().is_isolated == True；
  f) inbox 形态命令构造正确（提示词落盘沙箱 inbox 文件、argv 只带短 Read 指令、
     命令行绝不带任务正文，stdin 起进程即关；`-p -` stdin 形态 10-05 实测证伪后重构）；
  g) 审计/状态机写失败只 warning、绝不影响主流程（mock 底层抛异常）；
  另补：session_id 解析链、GC cleanup_stale_tasks、engines_list 申报。

不接真实 ZCode CLI / 不跑真实容器：mock subprocess.Popen，worker 用同步调法
（patch _launch 为 no-op 后直接调 _worker，Day5 "假 codex + 同步调 _worker" 经验）。

隔离：沙箱根注入临时目录；临时工作区每次 rmtree 重建；MORAY_DB 指向全新临时库并
db.init_db() 建表（本模块**真触库**，与 test_zcode_adapter.py 的防御性隔离不同）。

运行方式：server\\.venv\\Scripts\\python.exe -X utf8 tests/test_zcode_adapter_hardened.py
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离（导入应用模块前设置）----
TEST_DIR = PROJECT_ROOT / "test_zcode_adapter_hardened_workspace"
TEST_DIR.mkdir(exist_ok=True)
WS = TEST_DIR / "ws"
shutil.rmtree(WS, ignore_errors=True)
WS.mkdir(exist_ok=True)
SANDBOX = WS / "zcode_sandbox"
TEST_DB = TEST_DIR / "test_zcode_hardened.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["MORAY_WORKSPACE"] = str(WS)
os.environ["MORAY_DB"] = str(TEST_DB)
# [v3 心智反哺隔离 2026-10-23] 同 test_zcode_adapter：MEMORY_DB 硬指不存在路径，
# 心智注入零素材 → prompt 文件逐字原文，逐字断言不被生产库叙事行干扰。
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "test_memory_absent.sqlite3")

from server.app import crud, db
from server.app.engines import zcode_adapter as za
from server.app.engines.local_adapter import engines_list
from server.app.engines.zcode_adapter import ZCodeAdapter

# [v3 心智反哺隔离] config.MEMORY_DB 是 import 时冻结的模块属性，其他测试模块
# 也依赖它（防 import 顺序漂移惯例）——补丁作用域化，只罩本模块用例。
from server.app import config as _app_config  # noqa: E402

_MEM_DB_PATCH = None


def setUpModule():
    global _MEM_DB_PATCH
    _MEM_DB_PATCH = mock.patch.object(_app_config, "MEMORY_DB",
                                      os.environ["MORAY_MEMORY_DB"])
    _MEM_DB_PATCH.start()


def tearDownModule():
    if _MEM_DB_PATCH is not None:
        _MEM_DB_PATCH.stop()

db.init_db()  # 建表（含 Day2 event_snapshots + Day3 agent_sessions）

FAKE_CLI = [r"C:\fake\node.exe", r"C:\fake\zcode.cjs"]
VALID_TASK = {"action": "run_zcode", "prompt": "在沙箱里打个招呼", "timeout_sec": 60}


# ---------------------------------------------------------------- mock 脚手架（复刻自 test_zcode_adapter.py）

class FakeStdin(io.BytesIO):
    """close 后仍可取内容——worker 起进程即关 stdin、从不写入，断言需事后读回。"""

    def close(self):
        try:
            self._snapshot = self.getvalue()
        except ValueError:
            pass

    def getvalue(self):
        try:
            return super().getvalue()
        except ValueError:
            return getattr(self, "_snapshot", b"")


class FakeProc:
    def __init__(self, cmd, _rc=0, _stdout_data=b"", _forever=False, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 43210
        self.stdin = FakeStdin()
        self.stdout = io.BytesIO(_stdout_data)
        self.stderr = io.BytesIO(b"")
        self.killed = False
        self._rc = _rc
        self._forever = _forever

    def poll(self):
        return None if self._forever else self._rc

    def wait(self, timeout=None):
        if self._forever:
            raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout)
        return self._rc

    def kill(self):
        self.killed = True


def _new_engine() -> ZCodeAdapter:
    root = SANDBOX / Path(os.urandom(4).hex()).name
    return ZCodeAdapter(root=root, cli_cmd=FAKE_CLI)


def _start(eng, task):
    """拦掉 launch 线程后受理任务，返回 (task_id, rec)。start() 内的审计/状态机已同步执行。"""
    with mock.patch.object(ZCodeAdapter, "_launch", lambda self, rec: None):
        tid = eng.start(task)
    return tid, eng._tasks[tid]


def _run_worker(eng, rec, rc=0, stdout=b'{"response":"done"}\n', poll_forever=False, popen_raises=None,
                timeout_sec=None, finalize=True):
    """同步调 _worker（确定性收敛，无竞态）。finalize=False 时拦掉收尾，用于观察 running 中间态。

    @returns (proc|None, taskkill_calls)
    """
    procs, taskkill_calls = [], []

    def fake_popen(cmd, **kw):
        if popen_raises is not None:
            raise popen_raises
        p = FakeProc(cmd, _rc=rc, _stdout_data=stdout, _forever=poll_forever, **kw)
        procs.append(p)
        return p

    def fake_run(cmd, **kw):
        taskkill_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    if timeout_sec is not None:
        rec["timeout_sec"] = timeout_sec
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(za.subprocess, "Popen", fake_popen))
        st.enter_context(mock.patch.object(za.subprocess, "run", fake_run))
        if not finalize:
            st.enter_context(mock.patch.object(
                ZCodeAdapter, "_finalize", lambda self, rec, rc, startup_error=None: None))
        eng._worker(rec)
    return (procs[0] if procs else None), taskkill_calls


def _events(sid):
    """该会话在 event_snapshots 的全部行（按 id 升序，dict 化）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM event_snapshots WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _types(sid):
    return [r["event_type"] for r in _events(sid)]


# ---------------------------------------------------------------- (e) is_isolated 升格

class TestIsolatedUpgraded(unittest.TestCase):

    def test_capabilities_is_isolated_true(self):
        self.assertIs(_new_engine().capabilities()["is_isolated"], True)

    def test_engines_list_reports_isolated_true(self):
        data = engines_list()
        zc = next(e for e in data["engines"] if e["name"] == "zcode")
        self.assertIs(zc["capabilities"]["is_isolated"], True)


# ---------------------------------------------------------------- (a) started 事件

class TestStartedEvent(unittest.TestCase):

    def test_started_event_lands_with_payload(self):
        eng = _new_engine()
        sid = "h-started"
        tid, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        self.assertIn("zcode_task_started", _types(sid))
        ev = next(r for r in _events(sid) if r["event_type"] == "zcode_task_started")
        self.assertEqual(ev["session_id"], sid)
        self.assertEqual(ev["tool_name"], "zcode_task_started")
        # source_log_id 恒 None：operation 类事件不关联审计行，否则前端长出坏重放按钮（Day2 铁律）
        self.assertIsNone(ev["source_log_id"])
        snap = json.loads(ev["snapshot_json"])
        self.assertEqual(snap["output"]["task_id"], tid)
        self.assertEqual(snap["output"]["input"], VALID_TASK["prompt"])
        self.assertEqual(snap["output"]["session_id"], sid)


# ---------------------------------------------------------------- (b) pending → running

class TestPendingToRunning(unittest.TestCase):

    def test_session_pending_after_start(self):
        eng = _new_engine()
        sid = "h-pending"
        _start(eng, dict(VALID_TASK, session_id=sid))
        self.assertEqual(crud.get_agent_session(sid)["status"], "pending")

    def test_session_running_after_worker_entry(self):
        eng = _new_engine()
        sid = "h-running"
        _, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        # 拦掉 _finalize，停在 _worker 入口刚置的 running（否则同步跑完会直接到终态）
        _run_worker(eng, rec, rc=0, finalize=False)
        self.assertEqual(crud.get_agent_session(sid)["status"], "running")


# ---------------------------------------------------------------- (c) 完成 → completed

class TestCompletedTransition(unittest.TestCase):

    def test_completed_session_and_finished_event(self):
        eng = _new_engine()
        sid = "h-ok"
        tid, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        _run_worker(eng, rec, rc=0)
        self.assertEqual(rec["status"], "completed")
        sess = crud.get_agent_session(sid)
        self.assertEqual(sess["status"], "completed")
        self.assertEqual(sess["error_msg"], "")
        # finished 事件落库 + status=ok
        self.assertIn("zcode_task_finished", _types(sid))
        fin = next(r for r in _events(sid) if r["event_type"] == "zcode_task_finished")
        self.assertEqual(fin["status"], "ok")
        # last_event_id 闭环指向本次 finished 事件（先写事件再流转的成果）
        self.assertEqual(sess["last_event_id"], fin["id"])
        fin_snap = json.loads(fin["snapshot_json"])
        self.assertEqual(fin_snap["output"]["status"], "completed")
        self.assertIsNotNone(fin_snap["output"]["output"])  # result 产物入事件


# ---------------------------------------------------------------- (d) 失败 → failed + error_msg

class TestFailedTransition(unittest.TestCase):

    def test_nonzero_exit_fails_session(self):
        eng = _new_engine()
        sid = "h-fail"
        _, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        _run_worker(eng, rec, rc=2)
        self.assertEqual(rec["status"], "failed")
        sess = crud.get_agent_session(sid)
        self.assertEqual(sess["status"], "failed")
        self.assertIn("exit_code_2", sess["error_msg"])
        fin = next(r for r in _events(sid) if r["event_type"] == "zcode_task_finished")
        self.assertEqual(fin["status"], "error")

    def test_timeout_fails_session_with_timeout_msg(self):
        eng = _new_engine()
        sid = "h-timeout"
        _, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        _run_worker(eng, rec, poll_forever=True, timeout_sec=0.3)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["error"], "timeout")
        sess = crud.get_agent_session(sid)
        self.assertEqual(sess["status"], "failed")
        self.assertIn("timeout", sess["error_msg"])
        fin = next(r for r in _events(sid) if r["event_type"] == "zcode_task_finished")
        self.assertEqual(fin["status"], "timeout")


# ---------------------------------------------------------------- (f) inbox 命令构造

class TestInboxCommandConstruction(unittest.TestCase):

    def test_prompt_via_inbox_file_not_cmdline(self):
        eng = _new_engine()
        prompt = "帮我修 bug & 跑测试 | 输出 > 报告 \"引用\" ^ 脱字符"  # 专挑 cmd.exe 转义地狱字符
        _, rec = _start(eng, dict(VALID_TASK, prompt=prompt, session_id="h-stdin"))
        proc, _ = _run_worker(eng, rec, rc=0)
        self.assertIsNotNone(proc)
        cmd = proc.cmd
        # [10-05 形态] `-p <短指令>`：指向沙箱内落盘的提示词文件（CLI 的 -p 为带值参数，
        # "`-` 读 stdin" 约定已实测证伪）
        self.assertIn("-p", cmd)
        instr = cmd[cmd.index("-p") + 1]
        self.assertTrue(instr.startswith("Read inbox/prompt_"), instr)
        self.assertTrue(instr.endswith(" and do exactly what it says."), instr)
        # 提示词正文只走落盘文件、绝不进命令行；stdin 起进程即关、全程为空
        self.assertEqual(rec["prompt_file_rel"], "inbox/prompt_%s.md" % rec["task_id"])
        self.assertEqual(proc.stdin.getvalue(), b"")
        self.assertNotIn(prompt, " ".join(cmd))
        # 强制 flag 仍在位
        self.assertEqual(cmd[cmd.index("--mode") + 1], "edit")
        self.assertIn("--json", cmd)


# ---------------------------------------------------------------- (g) 审计写失败不阻断主流程

class TestAuditFailureIsolation(unittest.TestCase):

    def test_db_write_failure_does_not_break_main_flow(self):
        eng = _new_engine()
        sid = "h-auditfail"
        # 让审计（db.connect）与状态机（crud.transition_agent_session）底层双双抛异常
        with mock.patch.object(db, "connect", side_effect=RuntimeError("db down")), \
             mock.patch.object(crud, "transition_agent_session", side_effect=RuntimeError("db down")):
            tid, rec = _start(eng, dict(VALID_TASK, session_id=sid))   # 不得抛
            _run_worker(eng, rec, rc=0)                                # 不得抛
        # 主流程完整跑通：内存态照常收敛 completed，result 照常可取
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(eng.result(tid)["result"]["exit_code"], 0)


# ---------------------------------------------------------------- 附加：session 解析链 + GC

class TestSessionResolution(unittest.TestCase):

    def test_explicit_session_id_used(self):
        eng = _new_engine()
        _, rec = _start(eng, dict(VALID_TASK, session_id="my-explicit"))
        self.assertEqual(rec["session_id"], "my-explicit")

    def test_default_session_id_fallback(self):
        eng = _new_engine()
        _, rec = _start(eng, dict(VALID_TASK))  # 无 session_id
        self.assertEqual(rec["session_id"], "default")


class TestGarbageCollection(unittest.TestCase):

    def test_cleanup_removes_old_terminal_keeps_audit(self):
        eng = _new_engine()
        sid = "h-gc"
        tid, rec = _start(eng, dict(VALID_TASK, session_id=sid))
        _run_worker(eng, rec, rc=0)
        self.assertEqual(rec["status"], "completed")
        # 新鲜终态：阈值 60min 不清
        self.assertEqual(eng.cleanup_stale_tasks(older_than_minutes=60), 0)
        self.assertIn(tid, eng._tasks)
        # 把 ended_at 推到 2 小时前 → 被清
        rec["ended_at"] = time.time() - 3600 * 2
        self.assertEqual(eng.cleanup_stale_tasks(older_than_minutes=60), 1)
        self.assertNotIn(tid, eng._tasks)
        # GC 只清内存记录，持久化审计事件保留
        self.assertIn("zcode_task_finished", _types(sid))

    def test_cleanup_skips_non_terminal(self):
        eng = _new_engine()
        tid, rec = _start(eng, dict(VALID_TASK, session_id="h-gc-pending"))
        self.assertEqual(rec["status"], "pending")
        # pending 不是终态：即便阈值 0 也不清（清了会让 status()/result() 误抛 KeyError）
        self.assertEqual(eng.cleanup_stale_tasks(older_than_minutes=0), 0)
        self.assertIn(tid, eng._tasks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
