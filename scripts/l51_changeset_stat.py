# -*- coding: utf-8 -*-
"""L5.1 改动统计：对阶段0备份做 unified diff，输出 +x/-y/行数 供交付文档引用。"""
import difflib
from pathlib import Path

ROOT = Path(r"D:\MoRay")
TS = "20260928_123425"
PAIRS = {
    "knowledge/experience_base.py": "knowledge_experience_base.py_l51_%s.bak" % TS,
    "engine/task_runner.py": "engine_task_runner.py_l51_%s.bak" % TS,
    "server/app/task_routes.py": "server_app_task_routes.py_l51_%s.bak" % TS,
    "tests/mcp_phase/test_04_experience.py": "tests_mcp_phase_test_04_experience.py_l51_%s.bak" % TS,
}
for rel, bak in PAIRS.items():
    a = (ROOT / "backups/l51/bak").exists() and [] or []
    before = (ROOT / "backups/l51" / bak).read_text(encoding="utf-8").splitlines()
    after = (ROOT / rel).read_text(encoding="utf-8").splitlines()
    d = list(difflib.unified_diff(before, after, lineterm=""))
    add = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
    rem = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
    hunks = sum(1 for x in d if x.startswith("@@"))
    print("%-45s +%d/-%d hunks=%d (now %d lines)" % (rel, add, rem, hunks, len(after)))
tot = 0
for f in Path(ROOT / "tests/l51").glob("*.py"):
    n = len(f.read_text(encoding="utf-8").splitlines())
    tot += n
    print("new: %-45s %d lines" % (f.relative_to(ROOT).as_posix(), n))
print("tests/l51 total %d lines" % tot)
