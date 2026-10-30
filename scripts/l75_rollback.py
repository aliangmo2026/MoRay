# -*- coding: utf-8 -*-
"""L7.5 Laya联动 一键回滚/再应用（双向演练脚本，范式对齐 scripts/l70_rollback.py）。

状态目录：
  backup/l75_laya_20260929/*.py| .env.example   阶段0 改动前快照（已 fc 逐字节验证）
  backup/l75_laya_20260929/post/*_l75_post.bak  本脚本 snapshot-post 存的改动后快照
  backup/l75_laya_20260929/post/NEWFILES.txt    新增文件清单（回滚=挪走，再应用=挪回）

用法（venv python -X utf8）：
  scripts/l75_rollback.py snapshot-post   # 第一步：把当前（含 L7.5）状态存档
  scripts/l75_rollback.py rollback        # 回到 L7.0：4 个改动文件还原 + 8 个新文件挪走
  scripts/l75_rollback.py verify-pre      # 断言改动文件哈希 == 阶段0 快照
  scripts/l75_rollback.py reapply         # 恢复 L7.5：post 快照回填 + 新文件归位
  scripts/l75_rollback.py verify-post     # 断言 == post 快照

说明：
  * 回滚后 = L7.0 逐字节状态（含正式 8001 的 laya_std_server 备份还原——若已部署新 Laya，
    需重启 8001 才生效；本脚本只管磁盘，运行实例如实标注）。
  * L7.5 未动 parts/*.js → 无需 assemble 前端重建（与 L7.0 不同）。
  * work/laya/、work/l75_e2e/ 是运行时/验收产物目录，rollback 不清理（诚实留痕），
    不参与哈希验证。
  * 本脚本自身绝不入 NEW 清单（教训：回滚脚本不能 stash 自己）。
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\MoRay")
BDIR = ROOT / "backup" / "l75_laya_20260929"
POST = BDIR / "post"

# 改动前快照（rel → 阶段0 备份文件名），已存在于 BDIR
MODIFIED = {
    "engine/execution_scheduler.py": "execution_scheduler.py",
    "server/app/api.py": "api.py",
    "server/.env.example": ".env.example",
    "decision-kernel/lora-training/laya_std_server.py": "laya_std_server.py",
}
# 新增文件（回滚挪走 / 再应用挪回）——不含本脚本自身
NEW = [
    "config.yaml",
    "engine/laya_connector.py",
    "server/app/laya_event_handler.py",
    "decision-kernel/lora-training/laya_dispatch.py",
    "decision-kernel/lora-training/event_reporter.py",
    "tests/l75/test_l75_connector.py",
    "tests/l75/test_l75_events.py",
    "scripts/l75_baseline.py",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def die(msg: str) -> None:
    print("ROLLBACK-GATE FAIL:", msg)
    sys.exit(1)


def _bak_name(rel: str) -> Path:
    p = BDIR / MODIFIED[rel]
    if not p.is_file():
        die("阶段0 备份缺失：%s" % p)
    return p


def _post_bak(rel: str) -> Path:
    return POST / (rel.replace("/", "_") + "_l75_post.bak")


def snapshot_post() -> int:
    POST.mkdir(parents=True, exist_ok=True)
    for rel in list(MODIFIED) + NEW:
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
    for rel in MODIFIED:
        shutil.copy2(_bak_name(rel), ROOT / rel)
        print("restored:", rel)
    for rel in NEW:
        p = ROOT / rel
        if p.is_file():
            shutil.move(str(p), stash / rel.replace("/", "_"))
            print("stashed:", rel)
    print("rollback done（L7.5 未动 parts → 无需 assemble 重建）")
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
    return 0


def verify(mode: str) -> int:
    ok = True
    for rel in MODIFIED:
        p = ROOT / rel
        if not p.is_file():
            print("[%s] missing:" % mode, rel)
            ok = False
            continue
        got = sha256(p)
        want = sha256(_bak_name(rel)) if mode == "pre" else sha256(_post_bak(rel))
        status = "MATCH" if got == want else "MISMATCH"
        ok = ok and status == "MATCH"
        print("[%s] %-46s %s %s" % (mode, rel, got[:16], status))
    for rel in NEW:
        exists = (ROOT / rel).is_file()
        if mode == "post" and not exists:
            print("[post] missing new file:", rel)
            ok = False
        if mode == "pre" and exists:
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
