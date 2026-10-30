# -*- coding: utf-8 -*-
"""L4.5 一键回滚 / 重应用 / 校验（阶段4.4 + 交付件 l45_rollback 的实现体）。

用法（server\\.venv\\Scripts\\python.exe -X utf8 scripts\\l45_rollback.py <mode>）：
  stage     演练前置：把当前 L4.5 状态文件全量存档到 backups/l45_applied/（含新增文件）
  rollback  恢复阶段0备份（backups/l45/，L4.5 之前的原状）+ 删除 L4.5 新增文件
  reapply   从 backups/l45_applied/ 恢复 L4.5 终态（stage 的逆操作）
  verify    逐一比对受管文件 SHA256 与指定档案目录的 MANIFEST，全等输出 ALL-MATCH

受管清单（功能文件 7 改 + 2 增）：
  改：server/app/config.py · server/app/task_routes.py · parts/88_local_log_intent.js
      engine/task_runner.py · server/.env · server/.env.example · moray-workbench.html
  增：templates/test_run_flow.yaml · tests/l4_min/test_l45.py
  未动（零修改承诺）：api.py · assemble.py · main.py · templates/ 既有两 yaml
"""
import hashlib
import io
import os
import shutil
import sys

ROOT = r"D:\MoRay"
BAK = os.path.join(ROOT, "backups", "l45")            # L4.5 之前的原状（阶段0）
APPLIED = os.path.join(ROOT, "backups", "l45_applied")  # L4.5 终态（演练前 stage）

# rel_path -> (l45 阶段0备份文件名 | None=新增文件, applied 存档文件名)
FILES = {
    r"server\app\config.py":        ("config.py_20260928_l45.bak", "config.py.applied"),
    r"server\app\task_routes.py":   ("task_routes.py_20260928_l45.bak", "task_routes.py.applied"),
    r"parts\88_local_log_intent.js": ("88_local_log_intent.js_20260928_l45.bak", "88_local_log_intent.js.applied"),
    r"engine\task_runner.py":       ("task_runner.py_20260928_l45.bak", "task_runner.py.applied"),
    r"server\.env":                 (".env_20260928_l45.bak", ".env.applied"),
    r"server\.env.example":         (".env.example_20260928_l45.bak", ".env.example.applied"),
    r"moray-workbench.html":        ("moray-workbench.html_20260928_l45.bak", "moray-workbench.html.applied"),
    r"templates\test_run_flow.yaml": (None, "test_run_flow.yaml.applied"),
    r"tests\l4_min\test_l45.py":    (None, "test_l45.py.applied"),
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(d):
    return os.path.join(d, "MANIFEST.md" if d == BAK else "MANIFEST_APPLIED.md")


def mode_stage():
    os.makedirs(APPLIED, exist_ok=True)
    lines = ["# L4.5 终态存档（stage 于 %s）\n" % os.environ.get("DATE", "?")]
    ok = True
    for rel, (_, ap) in FILES.items():
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            print("MISSING", rel); ok = False; continue
        shutil.copy2(src, os.path.join(APPLIED, ap))
        lines.append("| %s | %s |" % (rel, sha256(src)))
        print("STAGED", rel)
    with io.open(manifest_path(APPLIED), "w", encoding="utf-8") as f:
        f.write("\n".join(["| 文件 | SHA256 |", "|---|---|"] + lines))
    print("STAGE-DONE" if ok else "STAGE-PARTIAL")
    return 0 if ok else 1


def mode_rollback():
    for rel, (bak, _) in FILES.items():
        dst = os.path.join(ROOT, rel)
        if bak is None:
            if os.path.exists(dst):
                os.remove(dst)
                print("REMOVED(new-file)", rel)
            continue
        src = os.path.join(BAK, bak)
        if not os.path.exists(src):
            print("MISSING-BACKUP", rel); return 1
        shutil.copy2(src, dst)
        print("RESTORED", rel)
    print("ROLLBACK-DONE")
    return 0


def mode_reapply():
    for rel, (_, ap) in FILES.items():
        src = os.path.join(APPLIED, ap)
        if not os.path.exists(src):
            print("MISSING-ARCHIVE", rel); return 1
        shutil.copy2(src, os.path.join(ROOT, rel))
        print("REAPPLIED", rel)
    print("REAPPLY-DONE")
    return 0


def mode_verify(arcdir):
    """arcdir = 'l45'（回滚态应==阶段0备份）或 'applied'（重应用态应==stage存档）。"""
    d, suffix = (BAK, "l45") if arcdir == "l45" else (APPLIED, "applied")
    man = {}
    if os.path.exists(manifest_path(d)):
        for ln in io.open(manifest_path(d), encoding="utf-8"):
            parts = [x.strip() for x in ln.split("|")]
            if len(parts) >= 4 and len(parts[3]) == 64:
                man[parts[1]] = parts[3]
    bad = 0
    for rel, (bak, ap) in FILES.items():
        cur = os.path.join(ROOT, rel)
        if arcdir == "l45":
            if bak is None:
                ok = not os.path.exists(cur)
                print("%-50s %s (absent expected)" % (rel, "OK" if ok else "FAIL"))
            else:
                hs = sha256(cur) if os.path.exists(cur) else "-"
                hb = sha256(os.path.join(d, bak)) if os.path.exists(os.path.join(d, bak)) else "?"
                ok = hs == hb
                print("%-50s %s %s" % (rel, "OK" if ok else "FAIL", hs[:16]))
        else:
            hs = sha256(cur) if os.path.exists(cur) else "-"
            ha = sha256(os.path.join(d, ap)) if os.path.exists(os.path.join(d, ap)) else "?"
            ok = hs == ha
            print("%-50s %s %s" % (rel, "OK" if ok else "FAIL", hs[:16]))
        if not ok:
            bad += 1
    print("ALL-MATCH" if bad == 0 else "MISMATCH=%d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else ""
    rc = {"stage": mode_stage, "rollback": mode_rollback, "reapply": mode_reapply,
          "verify": lambda: mode_verify(sys.argv[2])}.get(m, lambda: (print(__doc__), 2))()
    sys.exit(rc if isinstance(rc, int) else 0)
