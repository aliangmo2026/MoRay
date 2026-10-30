# -*- coding: utf-8 -*-
"""L5.0 一键回滚 / 重应用 / 校验（照 scripts/l45_rollback.py 先例，双向演练过才算数）。

用法（server\\.venv\\Scripts\\python.exe -X utf8 scripts\\l50_rollback.py <mode>）：
  stage     演练前置：把当前 L5.0 终态全量存档到 backups/l50_applied/（含新增文件）
  rollback  恢复阶段0备份（backups/l50/，L5.0 之前的原状）+ 删除 L5.0 新增文件 + 重建 workbench
  reapply   从 backups/l50_applied/ 恢复 L5.0 终态 + 重建 workbench
  verify l50|applied   逐一比对受管文件 SHA256，全等输出 ALL-MATCH

受管清单（5 改 + 5 增）：
  改：engine/task_runner.py · server/app/task_routes.py · parts/88_local_log_intent.js
      templates/error_fix_flow.yaml · moray-workbench.html（产物，重建时再装配）
  增：templates/code_review_flow.yaml · tests/l50/（3 文件）· scripts/l50_baseline.py
  零修改承诺：api.py · main.py · config.py · .env · assemble.py · 其余 templates
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

ROOT = r"D:\MoRay"
TS = "20260928_111123"                        # 阶段0备份时间戳（见 backups/l50/MANIFEST.md）
BAK = os.path.join(ROOT, "backups", "l50")
APPLIED = os.path.join(ROOT, "backups", "l50_applied")
PY = os.path.join(ROOT, "server", ".venv", "Scripts", "python.exe")

# rel -> (l50 阶段0备份文件名 | None=新增, applied 存档文件名)
FILES = {
    r"engine\task_runner.py":          ("task_runner_l50_%s.py.bak" % TS, "task_runner.applied"),
    r"server\app\task_routes.py":      ("task_routes_l50_%s.py.bak" % TS, "task_routes.applied"),
    r"parts\88_local_log_intent.js":   ("88_local_log_intent_l50_%s.js.bak" % TS, "88_local_log_intent.applied"),
    r"templates\error_fix_flow.yaml":  ("error_fix_flow_l50_%s.yaml.bak" % TS, "error_fix_flow.applied.yaml"),
    r"moray-workbench.html":           ("moray-workbench_l50_%s.html.bak" % TS, "moray-workbench.applied.html"),
    r"templates\code_review_flow.yaml": (None, "code_review_flow.applied.yaml"),
    r"tests\l50\test_l50_chaining.py": (None, "test_l50_chaining.applied.py"),
    r"tests\l50\test_l50_persistence.py": (None, "test_l50_persistence.applied.py"),
    r"tests\l50\test_l50_review.py":   (None, "test_l50_review.applied.py"),
    r"scripts\l50_baseline.py":        (None, "l50_baseline.applied.py"),
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rebuild():
    r = subprocess.run([PY, "assemble.py"], cwd=ROOT, capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()[-1:] if r.stdout else []
    print("REBUILD", "OK" if r.returncode == 0 else "FAIL", tail)
    return r.returncode


def mode_stage():
    os.makedirs(APPLIED, exist_ok=True)
    rows = ["| 文件 | SHA256 |", "|---|---|"]
    ok = True
    for rel, (_, ap) in FILES.items():
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            print("MISSING", rel); ok = False; continue
        shutil.copy2(src, os.path.join(APPLIED, ap))
        rows.append("| %s | %s |" % (rel, sha256(src)))
        print("STAGED", rel)
    with io.open(os.path.join(APPLIED, "MANIFEST_APPLIED.md"), "w", encoding="utf-8") as f:
        f.write("# L5.0 终态存档\n\n" + "\n".join(rows) + "\n")
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
    if rebuild() != 0:
        print("ROLLBACK-REBUILD-FAIL"); return 1
    print("ROLLBACK-DONE")
    return 0


def mode_reapply():
    for rel, (_, ap) in FILES.items():
        src = os.path.join(APPLIED, ap)
        if not os.path.exists(src):
            print("MISSING-ARCHIVE", rel); return 1
        os.makedirs(os.path.dirname(os.path.join(ROOT, rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(ROOT, rel))
        print("REAPPLIED", rel)
    if rebuild() != 0:
        print("REAPPLY-REBUILD-FAIL"); return 1
    print("REAPPLY-DONE")
    return 0


def mode_verify(arcdir):
    d = BAK if arcdir == "l50" else APPLIED
    man_file = os.path.join(d, "MANIFEST.md" if arcdir == "l50" else "MANIFEST_APPLIED.md")
    man = {}
    if os.path.exists(man_file):
        for ln in io.open(man_file, encoding="utf-8"):
            parts = [x.strip().strip("`") for x in ln.split("|")]
            if len(parts) >= 5 and len(parts[3]) == 64 and parts[1] in FILES:
                man[parts[1]] = parts[3]
    bad = 0
    for rel, (bak, ap) in FILES.items():
        cur = os.path.join(ROOT, rel)
        if arcdir == "l50" and bak is None:
            ok = not os.path.exists(cur)
            print("%-48s %s (absent expected)" % (rel, "OK" if ok else "FAIL"))
        else:
            arc = os.path.join(d, bak if arcdir == "l50" and bak else ap)
            hs = sha256(cur) if os.path.exists(cur) else "-"
            ha = sha256(arc) if os.path.exists(arc) else "?"
            ok = hs == ha and hs != "-"
            print("%-48s %s %s" % (rel, "OK" if ok else "FAIL", hs[:16]))
        if not ok:
            bad += 1
    print("ALL-MATCH" if bad == 0 else "MISMATCH=%d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else ""
    rc = {"stage": mode_stage, "rollback": mode_rollback, "reapply": mode_reapply,
          "verify": lambda: mode_verify(sys.argv[2] if len(sys.argv) > 2 else "l50")} \
        .get(m, lambda: (print(__doc__), 2))()
    sys.exit(rc if isinstance(rc, int) else 0)
