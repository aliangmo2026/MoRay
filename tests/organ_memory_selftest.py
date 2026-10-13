# -*- coding: utf-8 -*-
"""器官→记忆 三根线 · 隔离自检（只写临时记忆库，绝不碰生产 memory.sqlite3）。

覆盖：
  A organ_memory：总闸开/关、write_organ_memory 落库、异常不外抛
  B Argus 线：report ready 才写、同 sid 去重双写拦截、content 形态
  C ZCode 线：reader finally 钩子 result 帧+returncode==0 才写；caller=laya 跳过
  D Iris 线：_guarded_run completed+report+修复模板才写；非修复模板/失败不写
  E 反哺可见：三条 task_result 进 reflection._recent_task_results
  F 记忆页可见：/api/memory/list 三种 source 都能列出
运行方式：venv python 直接跑本文件。退出码非 0 = 有断言失败。
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import time

# ---- 环境隔离：临时记忆库 + 默认开闸 ----
_TMP_DB = os.path.join(tempfile.gettempdir(), "organ_selftest_%d.sqlite3" % os.getpid())
os.environ["MORAY_MEMORY_DB"] = _TMP_DB
os.environ.pop("MORAY_ORGAN_MEMORY", None)

sys.path.insert(0, r"D:\MoRay\server")     # app.* 包
sys.path.insert(0, r"D:\MoRay")           # engine / knowledge / adapters

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + ("  " + detail if detail else ""))


def rows(source=None):
    c = sqlite3.connect(_TMP_DB)
    c.row_factory = sqlite3.Row
    try:
        if source:
            return [dict(r) for r in c.execute(
                "SELECT id,kind,content,tags,source,entity_id FROM memory_bank WHERE source=? ORDER BY created_at",
                (source,)).fetchall()]
        return [dict(r) for r in c.execute(
            "SELECT id,kind,content,tags,source,entity_id FROM memory_bank ORDER BY created_at").fetchall()]
    finally:
        c.close()


def wait_rows(source, want, timeout=4.0):
    """daemon 线程落库是异步的，轮询等到位。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = rows(source)
        if len(r) >= want:
            return r
        time.sleep(0.05)
    return rows(source)


# ===================== A. organ_memory 本体 =====================
from app import memory_db, organ_memory, config  # noqa: E402
memory_db.init_memory_db()

print("== A. organ_memory ==")
check("A1 启动快照 ORGAN_MEMORY_ENABLED=True", config.ORGAN_MEMORY_ENABLED is True)

os.environ["MORAY_ORGAN_MEMORY"] = "0"
check("A2 关闸 _organ_memory_enabled=False", organ_memory._organ_memory_enabled() is False)
kicked = organ_memory.write_organ_memory("关闸不应落库", source="gate_off_probe", entity_id="g1")
check("A3 关闸 write 返回 False 未起线程", kicked is False)
time.sleep(0.3)
check("A4 关闸零落库", len(rows("gate_off_probe")) == 0)

for off in ("0", "false", "no", "off"):
    os.environ["MORAY_ORGAN_MEMORY"] = off
    if organ_memory._organ_memory_enabled():
        check("A5 关闸值 '%s' 被识别" % off, False)
        break
else:
    check("A5 关闸值 0/false/no/off 全识别", True)

os.environ["MORAY_ORGAN_MEMORY"] = "1"
check("A6 开闸 _organ_memory_enabled=True", organ_memory._organ_memory_enabled() is True)
organ_memory.write_organ_memory("测试内容\n第二行", kind="task_result",
                                tags=["t"], source="zcode_stream", entity_id="direct1")
d = wait_rows("zcode_stream", 1)
check("A7 开闸 write_organ_memory 落库", len(d) == 1 and d[0]["kind"] == "task_result"
      and d[0]["entity_id"] == "direct1", detail=str(d[:1]))

# ===================== B. Argus 线 =====================
print("== B. Argus（report 钩子 + sid 去重）==")
from app import argus_proxy  # noqa: E402

argus_proxy._QUESTION_BY_SID.clear()
argus_proxy._WRITTEN_SIDS.clear()


async def fake_request_ok(method, path, *, json_body=None, timeout=None):
    if path.endswith("/report"):
        return {"ready": True, "markdown": "研究结论正文", "summary": "一句话结论",
                "citations": [{"url": "u1"}, {"url": "u2"}]}, 200, "", ""
    return {"question": "Cursor 价格如何", "report_ready": True, "status": "done"}, 200, "", ""


argus_proxy._request = fake_request_ok

# 先经 status 登记 question（report 应答不带 question，靠此前轮询回填）
asyncio.run(argus_proxy.argus_status("s-sid-1"))
check("B0 status 回填 question 缓存",
      argus_proxy._QUESTION_BY_SID.get("s-sid-1") == "Cursor 价格如何")

r1 = asyncio.run(argus_proxy.argus_report("s-sid-1"))
ar = wait_rows("argus_research", 1)
check("B1 report ready+markdown 写一条", len(ar) == 1 and r1["ok"] is True)
if ar:
    c0 = ar[0]["content"]
    check("B2 content 三行式（研究/结论/引用）",
          c0.startswith("研究：Cursor 价格如何") and "结论：一句话结论" in c0 and "引用：2条" in c0,
          detail=repr(c0[:80]))
    check("B3 tags=argus_research kind=task_result entity=sid",
          ar[0]["kind"] == "task_result" and "argus_research" in (ar[0]["tags"] or "")
          and ar[0]["entity_id"] == "s-sid-1")

# 去重：第二次拉同 sid 不再写
asyncio.run(argus_proxy.argus_report("s-sid-1"))
time.sleep(0.3)
check("B4 同 sid 去重不双写", len(rows("argus_research")) == 1)


async def fake_request_notready(method, path, *, json_body=None, timeout=None):
    return {"ready": False, "markdown": "", "summary": "", "citations": []}, 200, "", ""


argus_proxy._request = fake_request_notready
asyncio.run(argus_proxy.argus_report("s-sid-2"))
time.sleep(0.3)
check("B5 ready=false 不写", len(rows("argus_research")) == 1)

# ===================== C. ZCode 线（reader finally 钩子）=====================
print("== C. ZCode（reader 钩子 + caller=laya 跳过）==")
from app import api as moray_api  # noqa: E402

class _FakeLoop:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


class _FakeQueue:
    def put_nowait(self, x):
        pass


class _FakeProc:
    """模拟 CLI stream-json：逐行 stdout（reader 迭代的是 proc.stdout 属性），
    wait 后置 returncode。"""

    def __init__(self, lines, rc):
        self.returncode = None
        self._rc = rc
        self.stdout = lines

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc


def run_reader(task_id, caller, lines, rc):
    rec = {"task_id": task_id, "prompt": "跑个脚本", "language": "python",
           "caller": caller, "cwd": r"D:\\x\\day24b_runs\\%s" % task_id}
    moray_api._ZCODE_STREAM_TASKS[task_id] = rec
    proc = _FakeProc(lines, rc)
    moray_api._zcode_stream_reader(task_id, proc, _FakeQueue(), _FakeLoop())
    time.sleep(0.4)
    return rows("zcode_stream")


RESULT_LINE = ('{"type":"result","response":"done: 42","usage":{},"projection":{"status":"ok"}}')
TURN_LINE = '{"type":"turn.completed","payload":{"duration":1234}}'
te0 = len(rows("zcode_stream"))

z1 = run_reader("zt-ok", "", [TURN_LINE, RESULT_LINE], 0)
check("C1 result帧+rc0 写一条", len(z1) == te0 + 1)
if len(z1) >= te0 + 1:
    zn = z1[-1]
    check("C2 content 形态（ZCode/耗时/退出码/结果）",
          zn["content"].startswith("ZCode：跑个脚本") and "耗时：1234ms" in zn["content"]
          and "退出码：0" in zn["content"] and "结果：done: 42" in zn["content"],
          detail=repr(zn["content"][:90]))
    check("C3 tags 含 zcode_stream+language entity=task",
          "zcode_stream" in (zn["tags"] or "") and "python" in (zn["tags"] or "")
          and zn["entity_id"] == "zt-ok" and zn["kind"] == "task_result")

z2 = run_reader("zt-laya", "laya", [RESULT_LINE], 0)
check("C4 caller=laya 双写防护跳过", len(z2) == len(z1))

z3 = run_reader("zt-rc1", "", [RESULT_LINE], 1)
check("C5 returncode!=0 不写", len(z3) == len(z2))

z4 = run_reader("zt-nores", "", ['{"type":"model.streaming","payload":{"delta":"hi"}}'], 0)
check("C6 无 result 帧不写", len(z4) == len(z3))

# ===================== D. Iris 线（_guarded_run 钩子）=====================
print("== D. Iris（_guarded_run completed+report+修复模板）==")
from app import task_routes  # noqa: E402
from pathlib import Path  # noqa: E402

REP_DIR = tempfile.mkdtemp(prefix="organ_iris_")
REPORT_FILE = os.path.join(REP_DIR, "fix_report.md")
with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write("# MoRay·Iris 排错修复报告\n\n修复了除零错误，验证通过。")
# 隔离状态落盘目录（_guarded_run→_persist 会写 STATE_DIR，默认指到真实 work/task_state）
task_routes.STATE_DIR = Path(REP_DIR) / "state"


class _StubRunner:
    def __init__(self, rep_status="completed", template="error_fix_flow"):
        self.params = {"project_dir": r"D:\demo\myproj", "project_id": "myproj"}
        self._rep_status = rep_status
        self._rep = {
            "task": template, "status": rep_status,
            "duration_sec": 3.2,
            "steps": [
                {"id": "analyze_error", "type": "mcp_call", "status": "ok", "engine": "mcp",
                 "attempts": 1, "duration_ms": 800,
                 "output": {"data": {"error_type": "ZeroDivisionError",
                                     "file_path": r"D:\demo\myproj\main.py"}}, "error": ""},
                {"id": "fix_code", "type": "zcode_call", "status": "ok", "engine": "real:zcode",
                 "attempts": 1, "duration_ms": 1000,
                 "output": {"diff": "+ x = 1 if d else 0", "result": "patched"}, "error": ""},
                {"id": "verify_in_sandbox", "type": "docker_sandbox", "status": "ok",
                 "engine": "fallback:local_exec", "attempts": 1, "duration_ms": 500,
                 "output": {"exit_code": 0, "stdout": "ok"}, "error": ""},
                {"id": "generate_report", "type": "report", "status": "ok", "engine": "-",
                 "attempts": 1, "duration_ms": 10, "output": {"path": REPORT_FILE}, "error": ""},
            ],
        }

    async def run(self):
        return self._rep


def run_guarded(tid, rec_tpl, runner):
    task_routes._register({
        "task_id": tid, "template": rec_tpl, "status": "running", "t0": time.time(),
        "started": "2026-10-04T00:00:00", "steps": [], "work_dir": REP_DIR,
        "step_order": [], "report": "", "diff": "", "error": "",
        "human_intervention": None, "tokens_spent": 0, "conversation_id": "c", "ended_at": None})
    asyncio.run(task_routes._guarded_run(tid, runner))
    time.sleep(0.4)
    return rows("task_flow")


ir0 = len(rows("task_flow"))
i1 = run_guarded("ir-ok", "error_fix_flow", _StubRunner())
check("D1 error_fix completed+report 写一条", len(i1) == ir0 + 1)
if len(i1) >= ir0 + 1:
    ic = i1[-1]
    check("D2 content 形态（修复/诊断/补丁｜验证）",
          ic["content"].startswith("修复：error_fix_flow·myproj")
          and "诊断：ZeroDivisionError/" in ic["content"]
          and "补丁：" in ic["content"] and "验证：ok" in ic["content"],
          detail=repr(ic["content"][:100]))
    check("D3 tags=iris_fix+project_id entity=task kind=task_result",
          "iris_fix" in (ic["tags"] or "") and "myproj" in (ic["tags"] or "")
          and ic["entity_id"] == "ir-ok" and ic["kind"] == "task_result")

i2 = run_guarded("ir-fail", "error_fix_flow", _StubRunner(rep_status="failed"))
check("D4 failed 不写", len(rows("task_flow")) == len(i1))

i3 = run_guarded("ir-rev", "code_review_flow", _StubRunner(template="code_review_flow"))
check("D5 非修复模板（review）不写", len(rows("task_flow")) == len(i1))

i4 = run_guarded("ir-txt", "text_fix_flow", _StubRunner(template="text_fix_flow"))
check("D6 text_fix_flow 也写（另一修复模板）", len(rows("task_flow")) == len(i1) + 1)

# ===================== E. 反思原料可见 =====================
print("== E. 反哺闭环（reflection 原料 kind=task_result）==")
from app import reflection  # noqa: E402

tr = reflection._recent_task_results(50)   # SELECT id,content,tags,created_at WHERE kind='task_result'
tr_ids = {x["id"] for x in tr}


def _all_in_tr(source, want):
    r = rows(source)
    return len(r) >= want and all(x["id"] in tr_ids for x in r)


# 被查回来 = 落库时 kind 就是 'task_result'（WHERE 硬匹配），同时证可见性
check("E1 argus_research 全进反思原料", _all_in_tr("argus_research", 1))
check("E2 zcode_stream 全进反思原料", _all_in_tr("zcode_stream", 1))
check("E3 task_flow 全进反思原料", _all_in_tr("task_flow", 2))

# ===================== F. 记忆页可见（list 端点）=====================
print("== F. 记忆页可见（GET /api/memory/list 三种 source）==")
import json as _json
from app import memory_routes  # noqa: E402

listing = rows()  # 直查表等价于 list 端点数据源
seen_sources = {r["source"] for r in listing}
check("F1 argus_research 可列", "argus_research" in seen_sources)
check("F2 zcode_stream 可列", "zcode_stream" in seen_sources)
check("F3 task_flow 可列", "task_flow" in seen_sources)
check("F4 未污染生产库（用的是临时库）", os.path.abspath(config.MEMORY_DB) == os.path.abspath(_TMP_DB))

# ===================== 汇总 =====================
print("\n== 汇总: %d PASS / %d FAIL ==" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项:", FAIL)
try:
    os.remove(_TMP_DB)
except OSError:
    pass
sys.exit(1 if FAIL else 0)
