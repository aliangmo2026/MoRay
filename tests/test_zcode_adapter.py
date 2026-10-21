"""
MoRay ZCodeAdapter 骨架验收测试（国庆冲刺 Day10）
==================================================
覆盖任务书 3.1 的五组用例：
  a) ZCodeAdapter 能实例化且 capabilities() 诚实申报（五键精确一致，Docker 未接不谎报）；
  b) start() 拒绝非 run_zcode 动作（词表收口，含设计文档旧词 zcode_run）；
  c) start() 拒绝越界 cwd（".." 穿越 / 白名单外绝对路径，且被拒目录不落地）；
  d) --mode edit 与 --disallowed-tools 被强制注入（mock subprocess 验证命令行参数，
     任务体传 mode=yolo 也被压回 edit；提示词落盘沙箱 inbox 文件、argv 只带短指令）；
  e) status()/cancel()/result() 基本行为（KeyError/False 契约语义、退出码收敛、
     超时收树、取消收敛 failed+canceled、usage 诚实解析）。

3.2：不接真实 ZCode CLI——mock subprocess.Popen；worker 用同步调法
（patch _launch 为 no-op 后直接调 _worker，Day5 "假 codex .bat + 同步调 _worker" 经验）。

隔离：沙箱根注入临时目录（构造参数仅供测试；生产常量 SANDBOX_ROOT 只断言不写入）；
临时工作区每次 rmtree 重建（防残留造成"被拒的写没发生"假阳性——既有测试铁律）；
MORAY_DB 指向临时库（本模块不触库，防御性隔离）。

运行方式：server\\.venv\\Scripts\\python.exe -X utf8 tests/test_zcode_adapter.py
"""

import io
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离（导入应用模块前设置）----
TEST_DIR = PROJECT_ROOT / "test_zcode_adapter_workspace"
TEST_DIR.mkdir(exist_ok=True)
WS = TEST_DIR / "ws"
# 工作区必须每次全新：残留目录会让"越界 cwd 没落地"断言假阳性
shutil.rmtree(WS, ignore_errors=True)
WS.mkdir(exist_ok=True)
SANDBOX = WS / "zcode_sandbox"          # 注入版沙箱根（替代生产 D:\MoRay\workspace\zcode_sandbox）
os.environ.setdefault("MORAY_DB", str(TEST_DIR / "test_zcode.db"))

from server.app.engines import zcode_adapter as za
from server.app.engines.base import ExecutionEngine
from server.app.engines.local_adapter import engines_list
from server.app.engines.zcode_adapter import SANDBOX_ROOT, ZCodeAdapter

FAKE_CLI = [r"C:\fake\node.exe", r"C:\fake\zcode.cjs"]


# ---------------------------------------------------------------- mock 脚手架

class FakeStdin(io.BytesIO):
    """close 后仍可取内容的 BytesIO——worker 写完提示词即 close（agent_runner 惯例），
    断言需要事后读回写入内容，故 close 不真关、只做快照。"""

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
    """假 Popen 返回对象：stdin/stdout/stderr 为内存流，poll 立即回退出码（或永远 None）"""

    # _rc/_stdout_data/_forever 用私有名：worker 真实传入的 Popen kwargs（stdin/stdout/
    # stderr=PIPE、cwd、env…）原样落 self.kwargs 供断言，避免与假流属性撞名
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
    """每个用例独立的引擎 + 独立沙箱根（互相不串状态）"""
    root = SANDBOX / Path(os.urandom(4).hex()).name
    return ZCodeAdapter(root=root, cli_cmd=FAKE_CLI)


def _run_sync(eng, task, rc=0, stdout=b'{"response":"done"}\n', poll_forever=False, popen_raises=None, timeout_sec=None):
    """start（拦掉 launch 线程）后同步调 _worker——确定性收敛，无竞态。

    @returns (task_id, rec, proc|None, taskkill_calls)
    """
    with mock.patch.object(ZCodeAdapter, "_launch", lambda self, rec: None):
        tid = eng.start(task)
    rec = eng._tasks[tid]
    if rec["status"] == "failed":          # 受理阶段就被拒：worker 不该也没机会跑
        return tid, rec, None, []
    procs = []
    taskkill_calls = []

    def fake_popen(cmd, **kw):
        if popen_raises is not None:
            raise popen_raises
        p = FakeProc(cmd, _rc=rc, _stdout_data=stdout, _forever=poll_forever, **kw)
        procs.append(p)
        return p

    def fake_run(cmd, **kw):
        taskkill_calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    if timeout_sec is not None:            # 绕过 clamp（下限 10s 太慢），直改记录
        rec["timeout_sec"] = timeout_sec
    with mock.patch.object(za.subprocess, "Popen", fake_popen), \
         mock.patch.object(za.subprocess, "run", fake_run):
        eng._worker(rec)
    return tid, rec, (procs[0] if procs else None), taskkill_calls


VALID_TASK = {"action": "run_zcode", "prompt": "在沙箱里打个招呼", "timeout_sec": 60}


# ---------------------------------------------------------------- 3.1-a 实例化与能力申报

class TestIdentityAndCapabilities(unittest.TestCase):

    def test_instantiable_and_is_execution_engine(self):
        eng = _new_engine()
        self.assertIsInstance(eng, ExecutionEngine)
        self.assertEqual(eng.NAME, "zcode")
        self.assertTrue(eng.DESCRIPTION)

    def test_capabilities_honest_no_docker(self):
        # [Day15] is_isolated 升格为 True：Docker 隔离层经 Day11b-b 验收 9/9 全绿——五键精确一致
        self.assertEqual(_new_engine().capabilities(), {
            "can_read": False,
            "can_write": True,
            "can_exec": False,
            "can_network": False,
            "is_isolated": True,
        })

    def test_default_sandbox_root_matches_spec(self):
        # 生产沙箱根钉死为任务书 2.c 路径；本测试不往里写任何东西
        self.assertEqual(SANDBOX_ROOT, Path(r"D:\MoRay\workspace\zcode_sandbox"))
        self.assertEqual(ZCodeAdapter().root, SANDBOX_ROOT)

    def test_constructor_has_no_side_effects(self):
        # /api/engines/list 的模块级实例化要求：构造不创建目录、不起进程
        root = SANDBOX / "no-side-effect-check"
        ZCodeAdapter(root=root, cli_cmd=FAKE_CLI)
        self.assertFalse(root.exists())


# ---------------------------------------------------------------- 3.1-b 动作白名单

class TestRejectNonRunZcodeAction(unittest.TestCase):

    def _assert_rejected(self, task, needle):
        eng = _new_engine()
        with mock.patch.object(za.subprocess, "Popen", mock.Mock()) as m:
            tid = eng.start(task)
            st = eng.status(tid)
        self.assertEqual(st["status"], "failed")
        self.assertIn(needle, st["error"])
        self.assertIsNone(eng.result(tid)["result"])
        m.assert_not_called()              # 受理都没过，绝不允许碰到 Popen

    def test_write_file_action_rejected(self):
        self._assert_rejected({"action": "write_file", "path": "a.txt", "content": "x"},
                              "action_not_supported")

    def test_old_design_word_zcode_run_rejected(self):
        # 词表收口：骨架阶段只认 run_zcode（设计文档 d 节的 zcode_run 是后置形态词表）
        self._assert_rejected({"action": "zcode_run", "prompt": "x"}, "action_not_supported")

    def test_exec_and_missing_action_rejected(self):
        self._assert_rejected({"action": "exec", "command": "calc.exe"}, "action_not_supported")
        self._assert_rejected({"prompt": "没有动作字段"}, "action_not_supported")

    def test_empty_prompt_rejected(self):
        self._assert_rejected({"action": "run_zcode", "prompt": "   "}, "missing prompt")

    def test_non_dict_task_raises(self):
        with self.assertRaises(ValueError):
            _new_engine().start("not-a-dict")


# ---------------------------------------------------------------- 3.1-c cwd 白名单

class TestRejectOutsideCwd(unittest.TestCase):

    def _assert_cwd_rejected(self, cwd, needle="path_outside_whitelist"):
        eng = _new_engine()
        with mock.patch.object(za.subprocess, "Popen", mock.Mock()) as m:
            tid = eng.start({"action": "run_zcode", "prompt": "x", "cwd": cwd})
            st = eng.status(tid)
        self.assertEqual(st["status"], "failed")
        self.assertIn(needle, st["error"])
        m.assert_not_called()

    def test_dotdot_traversal_rejected(self):
        self._assert_cwd_rejected(r"..\..\evil")
        self._assert_cwd_rejected(r"sub\..\..\..\evil2")
        # 被拒的目录必须真的没被创建（工作区每次 rmtree 重建，无残留假阳性）
        self.assertFalse((WS / "evil").exists())
        self.assertFalse((WS.parent / "evil2").exists())

    def test_absolute_outside_rejected(self):
        self._assert_cwd_rejected(r"D:\Windows\system32")
        self._assert_cwd_rejected(str(WS / "outside"))

    def test_sandbox_root_itself_allowed(self):
        # 与 LocalAdapter 的差异：沙箱根本身就是默认 --cwd（"锁定到沙箱根"）
        eng = _new_engine()
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK))
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["cwd_path"], eng.root)
        self.assertEqual(proc.kwargs.get("cwd"), str(eng.root))


# ---------------------------------------------------------------- 3.1-d 强制 flag 注入

class TestForcedFlags(unittest.TestCase):

    def test_mode_edit_and_disallowed_tools_forced(self):
        eng = _new_engine()
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK))
        self.assertIsNotNone(proc)
        cmd = proc.cmd
        # cli 前缀原样 + 五个强制 flag（Day9 实测名）逐一在位
        self.assertEqual(cmd[:2], FAKE_CLI)
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("--mode") + 1], "edit")
        self.assertEqual(cmd[cmd.index("--cwd") + 1], str(rec["cwd_path"]))
        self.assertEqual(cmd[cmd.index("--disallowed-tools") + 1], "Bash,WebFetch,Write")
        self.assertIn("--json", cmd)
        # 不存在的 flag 一个都不许出现（strict 解析器会 exit 1）
        for banned in ("--allowed-tools", "--permission-mode", "--headless", "-C",
                       "--api-key", "--base-url", "--model", "yolo"):
            self.assertNotIn(banned, cmd)

    def test_task_mode_yolo_overridden_to_edit(self):
        # 任务 2.2-b：任务体想提权到 yolo → 一律压回 edit
        eng = _new_engine()
        task = dict(VALID_TASK, mode="yolo")
        _, rec, proc, _ = _run_sync(eng, task)
        cmd = proc.cmd
        self.assertEqual(cmd[cmd.index("--mode") + 1], "edit")
        self.assertNotIn("yolo", cmd)
        self.assertEqual(rec["status"], "completed")

    def test_prompt_goes_through_inbox_file(self):
        # 任务 2.2-e [10-05 形态]：完整提示词落盘沙箱 inbox/prompt_<task_id>.md，
        # argv 只带短 Read 指令（命令行绝不带任务正文）；stdin 起进程即关、全程为空
        eng = _new_engine()
        prompt = "帮我修 bug & 跑测试 | 输出 > 报告 \"引用\""   # 专挑 cmd.exe 转义地狱字符
        _, rec, proc, _ = _run_sync(eng, dict(VALID_TASK, prompt=prompt))
        inbox_files = list((rec["cwd_path"] / "inbox").glob("prompt_*.md"))
        self.assertEqual(len(inbox_files), 1, "提示词必须恰好一份落盘进沙箱")
        self.assertEqual(inbox_files[0].read_text(encoding="utf-8"), prompt)
        self.assertEqual(proc.stdin.getvalue(), b"", "stdin 关闭即空，绝不写提示词")
        instr = proc.cmd[proc.cmd.index("-p") + 1]
        self.assertTrue(instr.startswith("Read inbox/prompt_"), instr)
        self.assertTrue(instr.endswith(" and do exactly what it says."), instr)
        self.assertNotIn(prompt, " ".join(proc.cmd))
        # Popen 形态：三管道 + shell=False + 独立进程组无窗口（win32）
        self.assertIs(proc.kwargs.get("stdin"), subprocess.PIPE)
        self.assertIs(proc.kwargs.get("stdout"), subprocess.PIPE)
        self.assertIs(proc.kwargs.get("stderr"), subprocess.PIPE)
        self.assertFalse(proc.kwargs.get("shell"))
        if sys.platform == "win32":
            self.assertEqual(proc.kwargs.get("creationflags"), 0x00000200 | 0x08000000)

    def test_provider_config_env_injection(self):
        # c.3 修订：认证走配置文件+环境变量注入（无 --api-key/--base-url flag）
        eng = _new_engine()
        cfg_path = str(WS / "provider.json")
        with mock.patch.dict(os.environ, {"MORAY_ZCODE_PROVIDER_CONFIG": cfg_path}):
            _, rec, proc, _ = _run_sync(eng, dict(VALID_TASK))
        env = proc.kwargs.get("env") or {}
        self.assertEqual(env.get("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"), cfg_path)
        self.assertEqual(env.get("ZCODE_MODEL_TELEMETRY_ENABLED"), "0")
        self.assertEqual(rec["status"], "completed")


# ---------------------------------------------------------------- 3.1-e 生命周期基本行为

class TestLifecycle(unittest.TestCase):

    def test_unknown_task_id_semantics(self):
        eng = _new_engine()
        with self.assertRaises(KeyError):
            eng.status("no-such-task")
        with self.assertRaises(KeyError):
            eng.result("no-such-task")
        self.assertFalse(eng.cancel("no-such-task"))   # 契约：未知任务 → False

    def test_successful_run_completes_with_result(self):
        eng = _new_engine()
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK))
        st = eng.status(tid)
        self.assertEqual(st, {"task_id": tid, "status": "completed", "error": None})
        res = eng.result(tid)
        self.assertEqual(res["status"], "completed")
        self.assertIsNone(res["error"])
        self.assertIsNotNone(res["created_at"])
        self.assertEqual(res["result"]["exit_code"], 0)
        self.assertIsNotNone(res["result"]["duration_sec"])
        self.assertEqual(res["result"]["cwd"], str(eng.root))
        # usage 拿不到就是 None（诚实原则，绝不编造）
        self.assertIsNone(res["result"]["usage"])
        self.assertIsNone(res["result"]["metadata"]["zcode_session"])

    def test_usage_parsed_only_when_really_returned(self):
        eng = _new_engine()
        out = b'{"sessionId":"sess_abc123","usage":{"input_tokens":5,"output_tokens":7}}\n'
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK), stdout=out)
        res = eng.result(tid)
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["result"]["usage"], {"input_tokens": 5, "output_tokens": 7})
        self.assertEqual(res["result"]["metadata"]["zcode_session"], "sess_abc123")

    def test_empty_stdout_marks_failed(self):
        # 守卫（C2b）：rc=0 + 空 stdout → prompt_not_delivered → failed。
        # _run_sync 默认改最小载荷后，全组只剩本用例显式喂 b""——结构上钉死
        # "空输出→failed" 契约：将来若绕过/删除 _prompt_idle_reason，本用例必红，不许静默退化。
        eng = _new_engine()
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK), stdout=b"")
        self.assertEqual(rec["status"], "failed")
        self.assertIn("prompt_not_delivered", rec.get("error", ""))

    def test_nonzero_exit_code_fails(self):
        eng = _new_engine()
        tid, rec, _, _ = _run_sync(eng, dict(VALID_TASK), rc=2)
        st = eng.status(tid)
        self.assertEqual(st["status"], "failed")
        self.assertEqual(st["error"], "exit_code_2")       # d.2 的 error 口径
        self.assertIsNone(eng.result(tid)["result"])       # 契约：失败 result=None

    def test_startup_failure_converges_failed(self):
        eng = _new_engine()
        tid, rec, proc, _ = _run_sync(eng, dict(VALID_TASK),
                                      popen_raises=OSError("zcode.cmd not found"))
        self.assertIsNone(proc)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("startup_failed", rec["error"])
        self.assertEqual(eng.status(tid)["status"], "failed")

    def test_timeout_kills_tree_and_converges_failed(self):
        eng = _new_engine()
        tid, rec, proc, taskkill_calls = _run_sync(eng, dict(VALID_TASK),
                                                   poll_forever=True, timeout_sec=0.3)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["error"], "timeout")          # timeout→failed 收口（Day5 惯例）
        self.assertTrue(proc.killed)
        # taskkill /T /F 收树（agent_runner 同构）
        self.assertTrue(taskkill_calls)
        self.assertEqual(taskkill_calls[0][:4], ["taskkill", "/T", "/F", "/PID"])
        self.assertEqual(eng.status(tid)["error"], "timeout")

    def test_cancel_pending_then_idempotent(self):
        eng = _new_engine()
        with mock.patch.object(ZCodeAdapter, "_launch", lambda self, rec: None):
            tid = eng.start(dict(VALID_TASK))
        self.assertEqual(eng.status(tid)["status"], "pending")
        self.assertTrue(eng.cancel(tid))
        st = eng.status(tid)
        self.assertEqual(st["status"], "failed")           # cancel→failed（Day7 契约）
        self.assertEqual(st["error"], "canceled")
        self.assertFalse(eng.cancel(tid))                  # 已终态 → False

    def test_cancel_completed_task_returns_false(self):
        eng = _new_engine()
        tid, rec, _, _ = _run_sync(eng, dict(VALID_TASK))
        self.assertEqual(rec["status"], "completed")
        self.assertFalse(eng.cancel(tid))


# ---------------------------------------------------------------- 2.5 注册验证

class TestEnginesListRegistration(unittest.TestCase):

    def test_engines_list_contains_zcode(self):
        data = engines_list()
        self.assertTrue(data["ok"])
        names = [e["name"] for e in data["engines"]]
        self.assertEqual(names, ["local", "zcode", "laya"])
        zc = next(e for e in data["engines"] if e["name"] == "zcode")
        self.assertTrue(zc["description"])
        self.assertEqual(zc["capabilities"], {
            "can_read": False, "can_write": True,
            "can_exec": False, "can_network": False, "is_isolated": True,
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
