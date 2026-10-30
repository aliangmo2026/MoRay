# -*- coding: utf-8 -*-
"""L5.2+L5.3 一键回滚/再应用（双向演练脚本，范式对齐 scripts/l51_rollback.py）。

状态目录：
  backups/l5253/*_l5253_<TS>.bak          阶段0 改动前快照（MANIFEST.md 有哈希表）
  backups/l5253/post/*_l5253_post.bak     本脚本 snapshot-post 存的改动后快照
  backups/l5253/post/NEWFILES.txt         新增文件清单（回滚=挪走，再应用=挪回）

用法（venv python -X utf8）：
  scripts/l5253_rollback.py snapshot-post   # 演练第一步：把当前（含 L5.2+L5.3）状态存档
  scripts/l5253_rollback.py rollback        # 回到 L5.1：改动文件还原 + 新文件挪走 + 重建前端
  scripts/l5253_rollback.py verify-pre      # 断言改动文件哈希 == 阶段0 MANIFEST
  scripts/l5253_rollback.py reapply         # 恢复 L5.2+L5.3：post 快照回填 + 新文件归位 + 重建前端
  scripts/l5253_rollback.py verify-post     # 断言 == post 快照

说明：
  * moray-workbench.html 是 assemble.py 构建产物——两个方向都紧跟一次 assemble 重建，
    产物一致即可证可逆（不单独存哈希）。
  * server/.env / .env.example 的 MONITOR_* 块：.env 有阶段0 备份直接还原；
    .env.example 无备份（改动=纯追加），rollback 时按确定性规则切掉 MONITOR 块。
"""
import glob
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\MoRay")
BDIR = ROOT / "backups" / "l5253"
POST = BDIR / "post"

# 阶段0 备份的既有文件（rel → 备份文件名通配）
MODIFIED = {
    "server/app/config.py": "server_app_config.py",
    "server/app/api.py": "server_app_api.py",
    "server/app/main.py": "server_app_main.py",
    "knowledge/experience_base.py": "knowledge_experience_base.py",
    "assemble.py": "assemble.py",
    "server/.env": "server_.env",
}
# 新增文件（回滚挪走 / 再应用挪回）
NEW = [
    "server/app/log_monitor.py",
    "server/app/monitor_routes.py",
    "parts/91_log_monitor.js",
    "tests/l5253/test_l5253_monitor.py",
    "tests/l5253/test_l5253_experience_xfer.py",
    "tests/l5253/test_l5253_monitor_routes.py",
]
PY = str(ROOT / "server" / ".venv" / "Scripts" / "python.exe")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def die(msg: str) -> None:
    print("ROLLBACK-GATE FAIL:", msg)
    sys.exit(1)


def _bak_name(rel_flat: str) -> Path:
    hits = sorted(glob.glob(str(BDIR / ("%s_l5253_*.bak" % rel_flat))))
    if not hits:
        die("阶段0 备份缺失：%s" % rel_flat)
    return Path(hits[0])


def _post_bak(rel: str) -> Path:
    return POST / (rel.replace("/", "_").replace("\\", "_") + "_l5253_post.bak")


def _strip_env_example() -> None:
    """.env.example：删除 MONITOR_* 追加块（rollback 方向）/ 不存在则按原样（reapply 从 post 还原）。"""
    p = ROOT / "server" / ".env.example"
    txt = p.read_text(encoding="utf-8")
    cut = re.split(r"\n# ---- \[L5\.2 主动日志监控", txt)[0].rstrip() + "\n"
    p.write_text(cut, encoding="utf-8")


def _rebuild_frontend() -> None:
    r = subprocess.run([PY, "-X", "utf8", "assemble.py"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    tail = (r.stdout or "") + (r.stderr or "")
    if "assembled OK" not in tail:
        die("assemble 重建失败：\n" + tail[-500:])
    print("frontend rebuilt")


def snapshot_post() -> int:
    POST.mkdir(parents=True, exist_ok=True)
    for rel in list(MODIFIED) + NEW + ["server/.env.example"]:
        src = ROOT / rel
        if not src.is_file():
            die("snapshot-post 缺文件：%s" % rel)
        dst = _post_bak(rel)
        shutil.copy2(src, dst)
        print("post-saved:", dst.name)
    (POST / "NEWFILES.txt").write_text("\n".join(NEW) + "\n", encoding="utf-8")
    print("NEWFILES.txt written (%d)" % len(NEW))
    return 0


def rollback() -> int:
    if not POST.is_dir():
        die("rollback 前必须先 snapshot-post（否则当前改动无处找回）")
    stash = BDIR / "rollback_stash"
    stash.mkdir(parents=True, exist_ok=True)
    for rel, flat in MODIFIED.items():
        shutil.copy2(_bak_name(flat), ROOT / rel)
        print("restored:", rel)
    _strip_env_example()
    print("restored: server/.env.example (MONITOR 块切除)")
    for rel in NEW:
        p = ROOT / rel
        if p.is_file():
            shutil.move(str(p), stash / rel.replace("/", "_"))
            print("stashed:", rel)
    ms = ROOT / "work" / "monitor_state"
    if ms.is_dir():
        shutil.move(str(ms), stash / "monitor_state")
        print("stashed: work/monitor_state")
    _rebuild_frontend()
    return 0


def reapply() -> int:
    for rel in list(MODIFIED) + ["server/.env.example"]:
        src = _post_bak(rel)
        if not src.is_file():
            die("post 快照缺失：%s" % src)
        shutil.copy2(src, ROOT / rel)
        print("applied:", rel)
    for rel in NEW:
        src = POST / (rel.replace("/", "_") + "_l5253_post.bak")
        if not src.is_file():
            die("post 快照缺失：%s" % src)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("applied:", rel)
    _rebuild_frontend()
    return 0


def verify(mode: str) -> int:
    ok = True
    for rel, flat in MODIFIED.items():
        p = ROOT / rel
        if not p.is_file():
            print("[%s] missing:" % mode, rel)
            ok = False
            continue
        got = sha256(p)
        want = sha256(_bak_name(flat)) if mode == "pre" else sha256(_post_bak(rel))
        status = "MATCH" if got == want else "MISMATCH"
        ok = ok and status == "MATCH"
        print("[%s] %-36s %s %s" % (mode, rel, got[:16], status))
    if mode == "post":
        for rel in NEW:
            if not (ROOT / rel).is_file():
                print("[post] missing new file:", rel)
                ok = False
        for rel in ["server/.env.example"]:
            if sha256(ROOT / rel) != sha256(_post_bak(rel)):
                print("[post] MISMATCH", rel)
                ok = False
    else:
        for rel in NEW:
            if (ROOT / rel).exists():
                print("[pre] new file still present:", rel)
                ok = False
    print("VERIFY-%s: %s" % (mode.upper(), "ALL-MATCH" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"snapshot-post": snapshot_post, "rollback": rollback, "reapply": reapply,
     "verify-pre": lambda: verify("pre"),
     "verify-post": lambda: verify("post")}.get(
        cmd, lambda: die("用法: snapshot-post|rollback|verify-pre|reapply|verify-post"))()
