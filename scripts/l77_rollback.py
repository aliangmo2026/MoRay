# -*- coding: utf-8 -*-
"""L7.7 Agent模式前端接入 + Laya调试面板 + 体验打磨 —— 双向回滚脚本。

用法：
  server\\.venv\\Scripts\\python.exe -X utf8 scripts\\l77_rollback.py restore    # 回到 L7.7 之前
  server\\.venv\\Scripts\\python.exe -X utf8 scripts\\l77_rollback.py reapply    # 再应用 L7.7
  server\\.venv\\Scripts\\python.exe -X utf8 scripts\\l77_rollback.py verify     # 只校验当前=哪一态

范式 = l60/l65 双向 ALL-MATCH：还原/再应用后逐文件与快照比对；
新增文件（parts/92、test_l77）restore 时必须不存在、reapply 时必须存在。
本脚本自身不在回滚范围内（不进快照目录）。
restore/reapply 后都会重跑 assemble.py 重建 moray-workbench.html（前端链路要求）。
"""
import filecmp
import os
import shutil
import subprocess
import sys

ROOT = r"D:\MoRay"
BK = os.path.join(ROOT, "backup", "l77_agent_20260929")
PY = os.path.join(ROOT, "server", ".venv", "Scripts", "python.exe")

# (目标相对路径, pre 快照名, post 快照名)；快照名 None = 该态不存在此文件（新增/删除）
FILES = [
    (r"decision-kernel\lora-training\laya_std_server.py", "laya_std_server.pre_l77.py", "laya_std_server.l77.py"),
    (r"decision-kernel\lora-training\modules\task_planner.py", "task_planner.pre_l77.py", "task_planner.l77.py"),
    (r"decision-kernel\lora-training\modules\executor.py", "executor.pre_l77.py", "executor.l77.py"),
    (r"assemble.py", "assemble.pre_l77.py", "assemble.l77.py"),
    (r"electron-app\src\index.html", "index.pre_l77.html", "index.l77.html"),
    (r"electron-app\src\renderer.js", "renderer.pre_l77.js", "renderer.l77.js"),
    (r"electron-app\src\style.css", "style.pre_l77.css", "style.l77.css"),
    (r"electron-app\preload.js", "preload.pre_l77.js", "preload.l77.js"),
    (r"electron-app\main.js", "main.pre_l77.js", "main.l77.js"),
    (r"parts\92_laya_agent.js", None, "92_laya_agent.js"),
    (r"decision-kernel\lora-training\test_l77_agent_polish.py", None, "test_l77_agent_polish.py"),
    (r"moray-workbench.html", "moray-workbench.pre_l77.html", "moray-workbench.l77.html"),
]

# l51_e2e 的 pre 快照在备份时被后态覆盖 → pre 用确定性替换重建（唯一改动=3 行探针 mock）
L51_REL = r"tests\l51\test_l51_e2e.py"
L51_POST_BLOCK = (
    "        with mock.patch.object(tr, \"IrisMCPClient\", FakeMCP), \\\n"
    "             mock.patch.object(httpx, \"AsyncClient\", FakeAsyncClient), \\\n"
    "             mock.patch.object(tr, \"_docker_available\", lambda: False), \\\n"
    "             mock.patch.object(tr, \"_zcode_probe\",\n"
    "                               lambda: \"l51_e2e 前提钉死：zcode 判不可用 → llm_patch 降级链\"):\n")
L51_PRE_BLOCK = (
    "        with mock.patch.object(tr, \"IrisMCPClient\", FakeMCP), \\\n"
    "             mock.patch.object(httpx, \"AsyncClient\", FakeAsyncClient), \\\n"
    "             mock.patch.object(tr, \"_docker_available\", lambda: False):\n")


def _assemble():
    p = subprocess.run([PY, "-X", "utf8", "assemble.py"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("assemble 失败:\n" + (p.stdout or "") + (p.stderr or ""))
    print("assemble 重建完成")


def apply(mode: str):
    snap_suffix = "pre" if mode == "restore" else "l77"
    for rel, pre, post in FILES:
        dst = os.path.join(ROOT, rel)
        snap = pre if mode == "restore" else post
        if snap is None:
            if os.path.exists(dst):
                os.remove(dst)
                print("删除(本态不存在):", rel)
            continue
        shutil.copy2(os.path.join(BK, snap), dst)
    # l51_e2e：以 l77 版为基，restore 时换回 pre 块
    l51 = os.path.join(ROOT, L51_REL)
    shutil.copy2(os.path.join(BK, "test_l51_e2e.l77.py"), l51)
    if mode == "restore":
        with open(l51, encoding="utf-8") as f:
            t = f.read()
        if L51_POST_BLOCK not in t:
            raise SystemExit("l51_e2e 找不到 post 块，无法重建 pre")
        with open(l51, "w", encoding="utf-8", newline="") as f:
            f.write(t.replace(L51_POST_BLOCK, L51_PRE_BLOCK))
    _assemble()


def verify(mode: str):
    ok = True
    for rel, pre, post in FILES:
        dst = os.path.join(ROOT, rel)
        snap = pre if mode == "restore" else post
        if snap is None:
            if os.path.exists(dst):
                print("FAIL 应不存在但存在:", rel); ok = False
            else:
                print("OK   不存在:", rel)
            continue
        if not os.path.exists(dst):
            print("FAIL 缺文件:", rel); ok = False; continue
        same = filecmp.cmp(dst, os.path.join(BK, snap), shallow=False)
        print(("OK   " if same else "FAIL ") + rel + (" == " + snap if same else " != " + snap))
        ok = ok and same
    # l51_e2e 内容判定
    with open(os.path.join(ROOT, L51_REL), encoding="utf-8") as f:
        t = f.read()
    if mode == "restore":
        good = L51_PRE_BLOCK in t and L51_POST_BLOCK not in t
    else:
        good = L51_POST_BLOCK in t
    print(("OK   " if good else "FAIL ") + L51_REL + (" (l51 块判定)" if good else " (l51 块判定不符)"))
    ok = ok and good
    print("ALL-MATCH" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if arg in ("restore", "reapply"):
        apply("restore" if arg == "restore" else "reapply")
        sys.exit(verify("restore" if arg == "restore" else "reapply"))
    elif arg == "verify":
        r1 = verify("reapply")
        r0 = verify("restore")
        if r1 == 0:
            print("当前 = L7.7 应用态")
        elif r0 == 0:
            print("当前 = L7.7 之前(pre)态")
        else:
            sys.exit(2)
    else:
        raise SystemExit(__doc__)
