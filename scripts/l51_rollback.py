# -*- coding: utf-8 -*-
"""L5.1 一键回滚/再应用（双向演练脚本，范式对齐 scripts/l50_rollback.py）。

状态目录：
  backups/l51/*_l51_<TS>.bak          阶段0 改动前快照（MANIFEST.md 有哈希表）
  backups/l51/post/*_l51_post.bak     本脚本 snapshot-post 存的改动后快照
  backups/l51/post/NEWFILES.txt       L5.1 新增文件清单（回滚=挪走，再应用=挪回）

用法（venv python -X utf8）：
  scripts/l51_rollback.py snapshot-post   # 演练第一步：把当前（含 L5.1）状态存档
  scripts/l51_rollback.py rollback        # 回到 L5.0：4 个代码文件还原 + 新文件挪走
  scripts/l51_rollback.py verify-pre      # 断言 4 文件哈希 == 阶段0 MANIFEST
  scripts/l51_rollback.py reapply         # 恢复 L5.1：post 快照回填 + 新文件归位
  scripts/l51_rollback.py verify-post     # 断言 4 文件哈希 == post 快照
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\MoRay")
BDIR = ROOT / "backups" / "l51"
POST = BDIR / "post"
TS = "20260928_123425"   # 阶段0 时间戳（MANIFEST.md）

# rel → 阶段0 备份文件名（已改动/待还原文 4 件）
CODE = {
    "knowledge/experience_base.py": "knowledge_experience_base.py_l51_%s.bak" % TS,
    "engine/task_runner.py": "engine_task_runner.py_l51_%s.bak" % TS,
    "server/app/task_routes.py": "server_app_task_routes.py_l51_%s.bak" % TS,
    "tests/mcp_phase/test_04_experience.py":
        "tests_mcp_phase_test_04_experience.py_l51_%s.bak" % TS,
}
# L5.1 新增文件（回滚挪走 / 再应用挪回；不存在则跳过）
NEW = [
    "tests/l51/test_l51_evolution.py",
    "tests/l51/test_l51_e2e.py",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def die(msg: str) -> None:
    print("ROLLBACK-GATE FAIL:", msg)
    sys.exit(1)


def snapshot_post() -> int:
    POST.mkdir(parents=True, exist_ok=True)
    for rel in list(CODE) + NEW:
        src = ROOT / rel
        if not src.is_file():
            die("snapshot-post 缺文件：%s" % rel)
        dst = POST / (rel.replace("/", "_").replace("\\", "_") + "_l51_post.bak")
        shutil.copy2(src, dst)
        print("post-saved:", dst.name)
    (POST / "NEWFILES.txt").write_text("\n".join(NEW) + "\n", encoding="utf-8")
    print("NEWFILES.txt written (%d)" % len(NEW))
    return 0


def rollback() -> int:
    stash = BDIR / "rollback_stash"
    stash.mkdir(parents=True, exist_ok=True)
    for rel, bak in CODE.items():
        src, cur = ROOT / rel, POST / (rel.replace("/", "_") + "_l51_post.bak")
        if not cur.is_file():
            die("post 快照缺失（先跑 snapshot-post）：%s" % cur)
        shutil.copy2(BDIR / bak, src)
        print("restored:", rel)
    for rel in NEW:
        p = ROOT / rel
        if p.is_file():
            shutil.move(str(p), stash / rel.replace("/", "_"))
            print("stashed:", rel)
    # 空目录 tests/l51 留着无害（pytest 收集 0 项）；正式库 sidecar 若存在则挪走
    meta = ROOT / "knowledge" / "experience_stats.json"
    if meta.is_file():
        shutil.move(str(meta), stash / "experience_stats.json")
        print("stashed: knowledge/experience_stats.json")
    return 0


def reapply() -> int:
    for rel in list(CODE) + NEW:
        src = POST / (rel.replace("/", "_") + "_l51_post.bak")
        if not src.is_file():
            die("post 快照缺失：%s" % src)
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("applied:", rel)
    return 0


def _manifest_hashes():
    out = {}
    txt = (BDIR / "MANIFEST.md").read_text(encoding="utf-8")
    for line in txt.splitlines():
        if line.startswith("| knowledge/experience_base.py") or \
           line.startswith("| engine/task_runner.py") or \
           line.startswith("| server/app/task_routes.py") or \
           line.startswith("| tests/mcp_phase/test_04_experience.py"):
            cols = [c.strip() for c in line.split("|")]
            out[cols[1]] = cols[3]
    return out


def verify(mode: str) -> int:
    hs = _manifest_hashes()
    if set(hs) != set(CODE):
        die("MANIFEST 解析到的文件集与 CODE 不一致：%s" % sorted(hs))
    ok = True
    for rel, bak in CODE.items():
        p = ROOT / rel
        if mode == "pre":
            want = hs[rel]
        else:
            want = sha256(POST / (rel.replace("/", "_") + "_l51_post.bak"))
        got = sha256(p)
        status = "MATCH" if got == want else "MISMATCH"
        if status == "MISMATCH":
            ok = False
        print("[%s] %-45s %s %s" % (mode, rel, got[:16], status))
    if mode == "post":
        for rel in NEW:
            if not (ROOT / rel).is_file():
                print("[post] missing new file:", rel)
                ok = False
    print("VERIFY-%s: %s" % (mode.upper(), "ALL-MATCH" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"snapshot-post": snapshot_post, "rollback": rollback, "reapply": reapply,
     "verify-pre": lambda: verify("pre"), "verify-post": lambda: verify("post")}.get(
        cmd, lambda: die("用法: snapshot-post|rollback|verify-pre|reapply|verify-post"))()
