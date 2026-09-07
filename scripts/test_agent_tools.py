# -*- coding: utf-8 -*-
"""阶段B 后端安全单测：起真实 uvicorn（127.0.0.1 + MORAY_DB 指向 %TEMP%），httpx 直打 /api/agent/*。

进程A：不设 MORAY_WORKSPACE → 断言默认工作区 D:\\MoRayWorkspace 自动创建。
进程B：MORAY_WORKSPACE=%TEMP%\\moray_agent_ws_<ts> → 功能 + 全部安全用例。
测试结束清理：杀进程、删临时库与临时工作区。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(HERE, "..", "server")
VENV_PY = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")

import httpx  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append((name, detail))
        print("FAIL", name, "->", detail)


def start_server(port, extra_env):
    env = dict(os.environ)
    env.update(extra_env)
    env["MORAY_DB"] = os.path.join(tempfile.gettempdir(), "moray_agent_test_%d.sqlite3" % port)
    proc = subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=SERVER_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = "http://127.0.0.1:%d" % port
    for _ in range(60):
        try:
            r = httpx.get(base + "/api/health", timeout=1)
            if r.status_code == 200:
                return proc, base
        except Exception:
            time.sleep(0.3)
    proc.kill()
    raise RuntimeError("server on port %d failed to start" % port)


def post(base, path, body):
    r = httpx.post(base + path, json=body, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:200]}


def get(base, path):
    r = httpx.get(base + path, timeout=15)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:200]}


def main():
    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp = tempfile.gettempdir()
    procs = []

    # ============ 进程A：不设 MORAY_WORKSPACE → 默认工作区 + 设置修改生效 ============
    pa, ba = start_server(8010, {})
    procs.append(pa)
    sc, sj = get(ba, "/api/agent/config")
    check("A1 config 200", sc == 200, sj)
    ws_default = None
    if sc == 200 and sj.get("ok"):
        ws_default = sj["data"]["workspace"]
    check("A2 默认工作区 = D:\\MoRayWorkspace", ws_default is not None and os.path.normcase(ws_default) == os.path.normcase(r"D:\MoRayWorkspace"), sj)
    check("A3 默认工作区目录自动创建", bool(ws_default) and os.path.isdir(ws_default), ws_default)
    # 设置修改工作区（无 env 时 kv 生效）
    wsA2 = os.path.join(tmp, "moray_agent_wsA2_" + ts)
    sc, sj = post(ba, "/api/agent/config", {"workspace": wsA2})
    check("A4 设置工作区 ok", sc == 200 and sj["ok"] and os.path.normcase(sj["data"]["workspace"]) == os.path.normcase(wsA2), sj)
    sc, sj = get(ba, "/api/agent/config")
    check("A5 config 反映新工作区", sc == 200 and sj["ok"] and os.path.normcase(sj["data"]["workspace"]) == os.path.normcase(wsA2), sj)
    sc, sj = post(ba, "/api/agent/tool", {"name": "write_file", "args": {"path": "inwsA2.txt", "content": "v2"}, "approved": True})
    check("A6 新工作区可写", sc == 200 and sj["ok"] and os.path.isfile(os.path.join(wsA2, "inwsA2.txt")), sj)
    # 无效工作区（不可写路径）
    bad_ws = "Z:\\no_such_drive_" + ts + "\\x"
    sc, sj = post(ba, "/api/agent/config", {"workspace": bad_ws})
    check("A7 不可写工作区拒绝", sc == 400 and sj.get("ok") is False, sj)
    pa.kill(); pa.wait()

    # ============ 进程B：临时工作区（全部安全用例） ============
    ws = os.path.join(tmp, "moray_agent_ws_" + ts)
    pb, b = start_server(8011, {"MORAY_WORKSPACE": ws})
    procs.append(pb)

    # ---- 只读工具：正常路径 ----
    sc, sj = get(b, "/api/agent/config")
    check("B1 config ok", sc == 200 and sj["ok"] and os.path.normcase(sj["data"]["workspace"]) == os.path.normcase(ws), sj)

    sc, sj = post(b, "/api/agent/tool", {"name": "list_directory", "args": {}})
    check("B2 list_directory 根 ok", sc == 200 and sj["ok"] and sj["data"]["path"] == ".", sj)

    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "sub/hello.txt", "content": "你好 MoRay"}, "approved": True})
    check("B3 write_file 自动建父目录", sc == 200 and sj["ok"] and os.path.isfile(os.path.join(ws, "sub", "hello.txt")), sj)
    real = open(os.path.join(ws, "sub", "hello.txt"), encoding="utf-8").read()
    check("B4 写入内容 UTF-8 正确", real == "你好 MoRay", repr(real))

    sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "sub/hello.txt"}})
    check("B5 read_file 正常", sc == 200 and sj["ok"] and sj["data"]["content"] == "你好 MoRay" and sj["data"]["truncated"] is False, sj)

    sc, sj = post(b, "/api/agent/tool", {"name": "list_directory", "args": {"path": "sub"}})
    check("B6 list_directory 子目录", sc == 200 and sj["ok"] and any(i["name"] == "hello.txt" and i["type"] == "file" for i in sj["data"]["items"]), sj)

    # ---- 截断 ----
    big100k = "x" * 100 * 1024
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "big.txt", "content": big100k}, "approved": True})
    check("B7 写入 100KB 成功", sc == 200 and sj["ok"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "big.txt"}})
    check("B8 默认 64KB 截断生效", sc == 200 and sj["ok"] and sj["data"]["truncated"] is True and len(sj["data"]["content"]) <= 64 * 1024, sj)

    sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "big.txt", "maxBytes": 4096}})
    check("B9 maxBytes=4096 截断", sc == 200 and sj["ok"] and sj["data"]["truncated"] is True and len(sj["data"]["content"]) == 4096, sj)

    lines3000 = "\n".join("line%d" % i for i in range(3000))
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "manylines.txt", "content": lines3000}, "approved": True})
    check("B10 写入 3000 行成功", sc == 200 and sj["ok"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "manylines.txt"}})
    check("B11 2000 行截断", sc == 200 and sj["ok"] and sj["data"]["truncated"] is True and sj["data"]["content"].count("\n") < 2000, sj)

    # ---- 越界/绝对路径/符号链接逃逸 ----
    for name, pth in [
        ("B12 ../../ 越界", "../../secret.txt"),
        ("B13 sub/../../secret 越界", "sub/../../secret.txt"),
        ("B14 盘符绝对路径 C:/Windows/win.ini", "C:/Windows/win.ini"),
        ("B15 盘符绝对路径反斜杠", "C:\\Windows\\win.ini"),
        ("B16 UNC 路径", "\\\\127.0.0.1\\c$\\x"),
        ("B17 根斜杠路径", "\\windows\\x"),
        ("B18 drive-relative C:secret.txt", "C:secret.txt"),
        ("B19 .. 逃逸带空目录", ".\\..\\..\\x"),
    ]:
        sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": pth}})
        check(name, sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    # junction 逃逸：ws\escape -> 外部目录
    outside = os.path.join(tmp, "moray_agent_outside_" + ts)
    os.makedirs(os.path.join(outside, "inner"), exist_ok=True)
    open(os.path.join(outside, "inner", "secret.txt"), "w", encoding="utf-8").write("top secret")
    junc = os.path.join(ws, "escape_junction")
    junc_ok = False
    try:
        import _winapi
        _winapi.CreateJunction(outside, junc)
        junc_ok = True
    except Exception as e:
        print("  (junction 创建跳过:", e, ")")
    if junc_ok:
        sc, sj = post(b, "/api/agent/tool", {"name": "list_directory", "args": {"path": "escape_junction"}})
        check("B20 junction 逃逸拒绝(list)", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
        sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "escape_junction/inner/secret.txt"}})
        check("B21 junction 逃逸拒绝(read)", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    # 文件符号链接逃逸（Windows 可能需要特权；失败则跳过）
    symlink_ok = False
    try:
        os.symlink(os.path.join(outside, "inner", "secret.txt"), os.path.join(ws, "escape_link.txt"))
        symlink_ok = True
    except Exception as e:
        print("  (symlink 创建跳过:", e, ")")
    if symlink_ok:
        sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "escape_link.txt"}})
        check("B22 文件符号链接逃逸拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
        sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "escape_link.txt", "content": "x"}, "approved": True})
        check("B23 符号链接写逃逸拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    # ---- 未审批副作用拒绝 ----
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "noapprove.txt", "content": "x"}})
    check("B24 write 未审批 -> needsApproval", sc == 200 and sj.get("needsApproval") is True and "summary" in sj, sj)
    check("B24b 未审批未写盘", not os.path.exists(os.path.join(ws, "noapprove.txt")), "")
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "python", "args": ["--version"]}})
    check("B25 run_command 未审批 -> needsApproval", sc == 200 and sj.get("needsApproval") is True, sj)

    # ---- 危险后缀 ----
    for ext in [".exe", ".bat", ".PS1", ".cmd", ".reg", ".vbs", ".msi"]:
        fname = "evil" + ext
        sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": fname, "content": "x"}, "approved": True})
        check("B26 危险后缀拒绝 " + fname, sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
        check("B26b 危险文件未落盘 " + fname, not os.path.exists(os.path.join(ws, fname)), "")

    # ---- 保留设备名 ----
    for fname in ["CON", "nul.txt", "com1.dat"]:
        sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": fname, "content": "x"}, "approved": True})
        check("B27 保留设备名拒绝 " + fname, sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    # ---- write_file 其他守卫 ----
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "sub/hello.txt", "content": "overwrite"}, "approved": True})
    check("B28 默认 overwrite 覆盖成功", sc == 200 and sj["ok"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "sub/hello.txt", "content": "x", "overwrite": False}, "approved": True})
    check("B29 overwrite=false 已存在拒绝", sc == 200 and sj.get("ok") is False, sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "fresh_keep.txt", "content": "x", "overwrite": False}, "approved": True})
    check("B30 overwrite=false 不存在成功", sc == 200 and sj["ok"], sj)
    huge = "y" * (5 * 1024 * 1024 + 10)
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "huge.txt", "content": huge}, "approved": True})
    check("B31 超 5MB 内容拒绝", sc == 200 and sj.get("ok") is False, sj)

    # ---- 二进制/非 UTF-8 ----
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "bin.dat", "content": "a\u0000b\u0001c"}, "approved": True})
    check("B32 写入含 NUL 内容成功(字节文件)", sc == 200 and sj["ok"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "read_file", "args": {"path": "bin.dat"}})
    check("B33 二进制读明确拒绝不乱码", sc == 200 and sj.get("ok") is False and sj.get("code") == "binary_file", sj)

    # ---- run_command ----
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "python", "args": ["--version"]}, "approved": True})
    check("B34 python --version 白名单执行", sc == 200 and sj["ok"] and sj["data"]["exitCode"] == 0 and "Python" in sj["data"]["stdout"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "dir"}, "approved": True})
    check("B35 非白名单命令拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "git", "args": ["status"]}, "approved": True})
    if sj.get("ok") is False and "找不到" in str(sj.get("message", "")):
        check("B36 git 缺失时明确提示", True, sj)
    else:
        check("B36 git status 白名单执行", sc == 200 and sj["ok"] and "exitCode" in sj["data"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "git", "args": ["rm", "x"]}, "approved": True})
    check("B37 git 非白名单子命令拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "git", "args": ["status", "&&", "echo"]}, "approved": True})
    check("B38 参数含 shell 元字符拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "python", "args": ["-c", "print(1)"]}, "approved": True})
    check("B39 python 仅允许 --version", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "where", "args": ["python"]}, "approved": True})
    check("B40 where python 白名单执行", sc == 200 and sj["ok"] and sj["data"]["exitCode"] == 0, sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "run_command", "args": {"command": "node", "args": ["--version"]}, "approved": True})
    if sj.get("ok") is False and "找不到" in str(sj.get("message", "")):
        check("B41 node 缺失时明确提示", True, sj)
    else:
        check("B41 node --version 白名单执行", sc == 200 and sj["ok"] and sj["data"]["exitCode"] == 0, sj)

    # ---- 客户端拒绝上报 ----
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "denied.txt", "content": "x"}, "decision": "denied"})
    check("B42 denied 上报 ok 且不执行", sc == 200 and sj["ok"] and sj["data"]["denied"] is True, sj)
    check("B42b denied 未写盘", not os.path.exists(os.path.join(ws, "denied.txt")), "")

    # ---- 大目录截断（直接 OS 层建 510 文件，绕开 API 写） ----
    bulk = os.path.join(ws, "bulk")
    os.makedirs(bulk, exist_ok=True)
    for i in range(510):
        open(os.path.join(bulk, "f%03d.txt" % i), "w").write("x")
    sc, sj = post(b, "/api/agent/tool", {"name": "list_directory", "args": {"path": "bulk"}})
    check("B43 list 超 500 条截断", sc == 200 and sj["ok"] and sj["data"]["count"] == 500 and sj["data"]["truncated"] is True, sj)

    # ---- 审计 ----
    sc, sj = get(b, "/api/agent/log?limit=500")
    rows = sj["data"]["rows"] if sc == 200 and sj.get("ok") else []
    total = sj["data"]["total"] if sc == 200 and sj.get("ok") else 0
    statuses = [r["status"] for r in rows]
    tools = {r["tool"] for r in rows}
    check("B47 审计落库 total>10", sc == 200 and total > 10, sj)
    check("B48 审计含 ok 记录", "ok" in statuses, statuses[:20])
    check("B49 审计含 rejected 记录", "rejected" in statuses, statuses[:20])
    check("B50 审计含 needs_approval 记录", "needs_approval" in statuses, statuses[:20])
    check("B51 审计含 denied 记录", "denied" in statuses, statuses[:20])
    check("B52 审计工具集合覆盖四工具", {"list_directory", "read_file", "write_file", "run_command"} <= tools, tools)
    wr_rows = [r for r in rows if r["tool"] == "write_file" and r["status"] == "ok"]
    check("B53 审计字段齐全", bool(wr_rows) and "ts" in wr_rows[0] and "args_summary" in wr_rows[0] and "approved" in wr_rows[0] and "ms" in wr_rows[0], wr_rows[0] if wr_rows else None)
    check("B54 审计 args_summary 含路径", bool(wr_rows) and "path=" in wr_rows[0]["args_summary"], wr_rows[0] if wr_rows else None)

    # ---- 未知工具 ----
    sc, sj = post(b, "/api/agent/tool", {"name": "rm_rf", "args": {}})
    check("B55 未知工具 400", sc == 400 and sj.get("ok") is False, sj)

    # ==== [阶段1 M2] find_files / search_text / edit_file ====
    # 本脚本自己的预置（e2e 脚本的 a.txt 不在此环境）
    open(os.path.join(ws, "m2_a.txt"), "w", encoding="utf-8").write("内容A素材")
    open(os.path.join(ws, "m2_b.txt"), "w", encoding="utf-8").write("内容B素材")
    sc, sj = post(b, "/api/agent/tool", {"name": "find_files", "args": {"pattern": "*.txt"}})
    f_names = [i["path"] for i in sj["data"]["items"]] if sj.get("ok") else []
    check("M2-F1 find *.txt 命中预置文件", sc == 200 and sj["ok"] and "m2_a.txt" in f_names and "big.txt" in f_names, {"count": sj.get("data", {}).get("count"), "has_a": "m2_a.txt" in f_names})
    sc, sj = post(b, "/api/agent/tool", {"name": "find_files", "args": {"path": "sub", "pattern": "*"}})
    sub_only = [i["path"] for i in sj["data"]["items"]] if sj.get("ok") else []
    check("M2-F2 path 限定子目录", sc == 200 and sj["ok"] and all(p.startswith("sub/") for p in sub_only) and len(sub_only) >= 1, sub_only)
    sc, sj = post(b, "/api/agent/tool", {"name": "find_files", "args": {"path": "../../etc"}})
    check("M2-F3 find 越界拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    # 1005 文件截断（OS 层直建）
    bulk2 = os.path.join(ws, "bulk2")
    os.makedirs(bulk2, exist_ok=True)
    for i in range(1005):
        open(os.path.join(bulk2, "g%04d.dat" % i), "w").write("x")
    sc, sj = post(b, "/api/agent/tool", {"name": "find_files", "args": {"path": "bulk2"}})
    check("M2-F4 find 1000 条截断", sc == 200 and sj["ok"] and sj["data"]["count"] == 1000 and sj["data"]["truncated"] is True, sj)

    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "内容A素材"}})
    s_hits = sj["data"]["items"] if sj.get("ok") else []
    check("M2-S1 search 命中 m2_a.txt", sc == 200 and sj["ok"] and any(h["file"] == "m2_a.txt" and h["line"] == 1 for h in s_hits), s_hits)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "内容[AB]素材", "regex": True}})
    s_files = {h["file"] for h in (sj["data"]["items"] or [])} if sj.get("ok") else set()
    check("M2-S2 正则模式命中 a+b", sc == 200 and sj["ok"] and {"m2_a.txt", "m2_b.txt"} <= s_files, s_files)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "[ unclosed", "regex": True}})
    check("M2-S3 非法正则明确报错", sc == 200 and sj.get("ok") is False and sj.get("code") == "tool_error" and "正则" in sj.get("message", ""), sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "内容", "glob": "*.md"}})
    check("M2-S4 glob 过滤无命中", sc == 200 and sj["ok"] and sj["data"]["count"] == 0, sj)
    manyl = os.path.join(ws, "manyhits.txt")
    with open(manyl, "w", encoding="utf-8") as f:
        for i in range(250):
            f.write("第%d行 包含针词\n" % i)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "针词"}})
    check("M2-S5 命中 200 条截断", sc == 200 and sj["ok"] and sj["data"]["count"] == 200 and sj["data"]["truncated"] is True, sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "x", "glob": "*.dat"}})
    check("M2-S6 二进制文件跳过计数", sc == 200 and sj["ok"] and sj["data"]["skipped_binary"] >= 1, sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "search_text", "args": {"query": "x", "path": "..\\..\\win"}})
    check("M2-S7 search 越界拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)

    etxt = "hello world foo"
    sc, sj = post(b, "/api/agent/tool", {"name": "write_file", "args": {"path": "edit_t.txt", "content": etxt}, "approved": True})
    check("M2-E0 预置编辑目标", sc == 200 and sj["ok"], sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "edit_t.txt", "old_str": "world", "new_str": "WORLD"}, "approved": True})
    e_after = open(os.path.join(ws, "edit_t.txt"), encoding="utf-8").read() if sj.get("ok") else ""
    check("M2-E1 唯一匹配替换成功", sc == 200 and sj["ok"] and sj["data"]["replacements"] == 1 and e_after == "hello WORLD foo", (sj, e_after))
    check("M2-E1b before/after 片段返回", "before_snippet" in sj.get("data", {}) and "after_snippet" in sj.get("data", {}), sj.get("data"))
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "edit_t.txt", "old_str": "不存在的文本", "new_str": "x"}, "approved": True})
    check("M2-E2 0 匹配报错", sc == 200 and sj.get("ok") is False and "未找到" in sj.get("message", ""), sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "edit_t.txt", "old_str": "o", "new_str": "0"}, "approved": True})
    check("M2-E3 多匹配未 replace_all 报错", sc == 200 and sj.get("ok") is False and "不唯一" in sj.get("message", ""), sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "edit_t.txt", "old_str": "o", "new_str": "0", "replace_all": True}, "approved": True})
    e_all = open(os.path.join(ws, "edit_t.txt"), encoding="utf-8").read() if sj.get("ok") else ""
    check("M2-E4 replace_all 替换全部", sc == 200 and sj["ok"] and sj["data"]["replacements"] >= 3 and "0" in e_all and e_after != e_all, (sj.get("data", {}).get("replacements"), e_all))
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "evil.exe", "old_str": "a", "new_str": "b"}, "approved": True})
    check("M2-E5 危险后缀拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "bin.dat", "old_str": "a", "new_str": "b"}, "approved": True})
    check("M2-E6 二进制明确拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "binary_file", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "..\\..\\x.txt", "old_str": "a", "new_str": "b"}, "approved": True})
    check("M2-E7 越界拒绝", sc == 200 and sj.get("ok") is False and sj.get("code") == "unsafe", sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "no_approve_e.txt", "old_str": "a", "new_str": "b"}})
    check("M2-E8 edit 未审批 needsApproval", sc == 200 and sj.get("needsApproval") is True and not os.path.exists(os.path.join(ws, "no_approve_e.txt")), sj)
    sc, sj = post(b, "/api/agent/tool", {"name": "edit_file", "args": {"path": "ghost.txt", "old_str": "a", "new_str": "b"}, "approved": True})
    check("M2-E9 不存在文件报错引导 write_file", sc == 200 and sj.get("ok") is False and "write_file" in sj.get("message", ""), sj)
    sc, sj = get(b, "/api/agent/log?limit=100")
    m2_logs = [x for x in (sj["data"]["rows"] if sj.get("ok") else []) if x["tool"] == "edit_file"]
    check("M2-E10 edit_file 审计落库", any(x["status"] == "ok" for x in m2_logs) and any(x["status"] == "rejected" for x in m2_logs), [(x["status"]) for x in m2_logs][:8])

    pb.kill(); pb.wait()

    # 清理
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    for d in (ws, wsA2, outside):
        shutil.rmtree(d, ignore_errors=True)
    for port in (8010, 8011):
        try:
            os.remove(os.path.join(tmp, "moray_agent_test_%d.sqlite3" % port))
        except OSError:
            pass
    try:
        os.remove(os.path.join(tmp, "moray_agent_import.sqlite3"))
    except OSError:
        pass

    print("\n==== 结果: %d 通过, %d 失败 ====" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, d in FAIL:
            print("  FAILED:", n, "|", str(d)[:300])
        sys.exit(1)
    print("全部通过 ✔")
    sys.exit(0)


if __name__ == "__main__":
    main()
