# -*- coding: utf-8 -*-
"""L6.5 一键回滚/再应用（双向演练脚本，范式对齐 scripts/l60_rollback.py）。

状态目录：
  backups/l65/*_l65_<TS>.bak          阶段0 改动前快照（MANIFEST.md 有哈希表）
  backups/l65/post/*_l65_post.bak     本脚本 snapshot-post 存的改动后快照
  backups/l65/post/NEWFILES.txt       新增文件清单（回滚=挪走，再应用=挪回）

用法（venv python -X utf8）：
  scripts/l65_rollback.py snapshot-post   # 第一步：把当前（含 L6.5）状态存档
  scripts/l65_rollback.py rollback        # 回到 L6.0：改动文件还原 + 新文件挪走 + （前端未动，无需重建）
  scripts/l65_rollback.py verify-pre      # 断言改动文件哈希 == 阶段0 MANIFEST
  scripts/l65_rollback.py reapply         # 恢复 L6.5：post 快照回填 + 新文件归位
  scripts/l65_rollback.py verify-post     # 断言 == post 快照

说明：
  * L6.5 未改前端 parts/* 与 assemble.py（模板 YAML 也零改动）→ 无需 assemble 重建步骤。
  * server/.env.example 的 L6.5 块：有阶段0备份直接还原（本阶段0已列入 OPTIONAL）。
  * 统一库运行时状态与 experience_base.jsonl 同体（增量键），回滚后旧读路径天然兼容，
    无需单独清理；strategy/pattern sidecar 属 L6.0 状态，本脚本不碰。
"""
import glob
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\MoRay")
BDIR = ROOT / "backups" / "l65"
POST = BDIR / "post"

# 阶段0 备份的既有文件（rel → 备份文件名通配）
MODIFIED = {
    "engine/task_runner.py": "engine_task_runner.py",
    "server/app/task_routes.py": "server_app_task_routes.py",
    "server/.env.example": "server_.env.example",
}
# 新增文件（回滚挪走 / 再应用挪回）——不含本脚本自身（工具随 post 快照保存，
# stash 掉自己会让后续 verify/reapply 无从执行，实测教训）
NEW = [
    "knowledge/issue_base.py",
    "scripts/l65_backup.py",
    "scripts/l65_baseline.py",
    "tests/l65/test_l65_issue_base.py",
    "tests/l65/test_l65_review_knowledge.py",
    "tests/l65/test_l65_suggest_tests.py",
    "tests/l65/test_l65_runner_hooks.py",
    "tests/l65/test_l65_gates.py",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def die(msg: str) -> None:
    print("ROLLBACK-GATE FAIL:", msg)
    sys.exit(1)


def _bak_name(rel_flat: str) -> Path:
    hits = sorted(glob.glob(str(BDIR / ("%s_l65_*.bak" % rel_flat))))
    if not hits:
        die("阶段0 备份缺失：%s" % rel_flat)
    return Path(hits[0])


def _post_bak(rel: str) -> Path:
    return POST / (rel.replace("/", "_").replace("\\", "_") + "_l65_post.bak")


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
    for rel in NEW:
        p = ROOT / rel
        if p.is_file():
            shutil.move(str(p), stash / rel.replace("/", "_"))
            print("stashed:", rel)
    print("frontend untouched by L6.5 — no assemble needed")
    return 0


def reapply() -> int:
    for rel in list(MODIFIED) + NEW:
        src = _post_bak(rel)
        if not src.is_file():
            die("post 快照缺失：%s" % src)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("applied:", rel)
    print("frontend untouched by L6.5 — no assemble needed")
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
