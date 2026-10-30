# -*- coding: utf-8 -*-
"""L5.2+L5.3 改动统计：对阶段0备份做 unified diff，输出 +/-/行数 供交付文档引用。"""
import difflib
import glob
from pathlib import Path

ROOT = Path(r"D:\MoRay")
BDIR = ROOT / "backups" / "l5253"
PAIRS = {
    "knowledge/experience_base.py": "knowledge_experience_base.py",
    "server/app/config.py": "server_app_config.py",
    "server/app/api.py": "server_app_api.py",
    "server/app/main.py": "server_app_main.py",
    "assemble.py": "assemble.py",
    "server/.env": "server_.env",
}
NEW = [
    "server/app/log_monitor.py",
    "server/app/monitor_routes.py",
    "parts/91_log_monitor.js",
    "tests/l5253/test_l5253_monitor.py",
    "tests/l5253/test_l5253_experience_xfer.py",
    "tests/l5253/test_l5253_monitor_routes.py",
]


def lines(p):
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


print("== 改动文件（vs 阶段0 备份） ==")
for rel, flat in PAIRS.items():
    baks = sorted(glob.glob(str(BDIR / (flat + "_l5253_*.bak"))))
    old = lines(Path(baks[0]))
    new = lines(ROOT / rel)
    d = list(difflib.unified_diff(old, new, lineterm=""))
    plus = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
    hunks = sum(1 for l in d if l.startswith("@@"))
    print("%-38s +%d / -%d  hunks=%d  (%d -> %d 行)" % (rel, plus, minus, hunks, len(old), len(new)))

print("== 新增文件 ==")
tot = 0
for rel in NEW:
    n = len(lines(ROOT / rel))
    tot += n
    print("%-38s %d 行" % (rel, n))
print("新增合计：%d 行" % tot)

print("== 未改动声明（fc 直证） ==")
import subprocess
for rel, flat in [("server/app/task_routes.py", "server_app_task_routes.py"),
                  ("engine/task_runner.py", "engine_task_runner.py"),
                  ("adapters/local_log_adapter.py", "adapters_local_log_adapter.py"),
                  ("parts/88_local_log_intent.js", "parts_88_local_log_intent.js"),
                  ("templates/error_fix_flow.yaml", "templates_error_fix_flow.yaml"),
                  ("templates/text_fix_flow.yaml", "templates_text_fix_flow.yaml"),
                  ("templates/test_run_flow.yaml", "templates_test_run_flow.yaml"),
                  ("templates/code_review_flow.yaml", "templates_code_review_flow.yaml")]:
    baks = sorted(glob.glob(str(BDIR / (flat + "_l5253_*.bak"))))
    r = subprocess.run(["fc", "/b", str(baks[0]), str(ROOT / rel)],
                       capture_output=True, text=True, encoding="gbk", errors="replace")
    same = "FC:" in r.stdout or "找不到差异" in r.stdout or (r.returncode == 0 and "no differences" in r.stdout.lower())
    print("%-38s %s" % (rel, "UNCHANGED" if r.returncode == 0 else "CHANGED!!"))
