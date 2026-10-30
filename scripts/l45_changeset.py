# L4.5 changeset: per-file diff line counts (backup vs current, unified-diff style).
import difflib, io, os

ROOT = r"D:\MoRay"
PAIRS = [
    (r"server\app\config.py", r"backups\l45\config.py_20260928_l45.bak", "mod"),
    (r"server\app\task_routes.py", r"backups\l45\task_routes.py_20260928_l45.bak", "mod"),
    (r"parts\88_local_log_intent.js", r"backups\l45\88_local_log_intent.js_20260928_l45.bak", "mod"),
    (r"engine\task_runner.py", r"backups\l45\task_runner.py_20260928_l45.bak", "mod"),
    (r"server\.env", r"backups\l45\.env_20260928_l45.bak", "mod"),
    (r"server\.env.example", r"backups\l45\.env.example_20260928_l45.bak", "mod"),
    (r"moray-workbench.html", r"backups\l45\moray-workbench.html_20260928_l45.bak", "mod"),
    (r"templates\test_run_flow.yaml", None, "new"),
    (r"tests\l4_min\test_l45.py", None, "new"),
    (r"tests\l4_min\test_l4_tasks.py", None, "untouched-check"),
]
tot_a = tot_d = 0
rows = []
for rel, bak, kind in PAIRS:
    cur = os.path.join(ROOT, rel)
    a = io.open(cur, encoding="utf-8", errors="replace").read().splitlines()
    if kind == "new":
        add, dele = len(a), 0
        patch = None
    elif kind == "untouched-check":
        add = dele = 0
        patch = None
    else:
        b = io.open(os.path.join(ROOT, bak), encoding="utf-8", errors="replace").read().splitlines()
        diff = list(difflib.unified_diff(b, a, bak, rel, lineterm=""))
        add = sum(1 for x in diff if x.startswith("+") and not x.startswith("+++"))
        dele = sum(1 for x in diff if x.startswith("-") and not x.startswith("---"))
        # 单文件补丁另存
        pf = os.path.join(ROOT, "docs", "delivery", "l45", "patches",
                          os.path.basename(rel) + ".patch")
        os.makedirs(os.path.dirname(pf), exist_ok=True)
        io.open(pf, "w", encoding="utf-8", newline="").write("\n".join(diff) + "\n")
    rows.append((rel, kind, add, dele))
    tot_a += add; tot_d += dele

print("%-42s %-10s %6s %6s" % ("文件", "类型", "+行", "-行"))
for rel, kind, a2, d2 in rows:
    print("%-42s %-10s %6d %6d" % (rel, kind, a2, d2))
print("TOTAL +%d / -%d" % (tot_a, tot_d))
