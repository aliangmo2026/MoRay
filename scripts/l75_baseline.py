# -*- coding: utf-8 -*-
"""L7.5 Laya联动 回归基线：范式 = scripts/l70_baseline.py（逐文件 pytest，末导入者赢老坑）。

组成：L7.0 权威 413 + l75 37（connector 18 + events 19）。
用法：venv python -X utf8 scripts/l75_baseline.py [out.txt]
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\MoRay")
PY = str(ROOT / "server" / ".venv" / "Scripts" / "python.exe")

SUITES = [
    ("root", ["test_budget.py", "test_budget_day24_11.py", "test_openai_compat.py", "test_security.py"]),
    ("tests", ["tests/test_engines.py", "tests/test_event_stream.py", "tests/test_mvp_e2e.py",
               "tests/test_session_state.py",
               "tests/test_zcode_adapter.py", "tests/test_zcode_adapter_hardened.py"]),
    ("mcp_phase", ["tests/mcp_phase/test_01_iris_server.py", "tests/mcp_phase/test_02_task_runner.py",
                   "tests/mcp_phase/test_03_adapters.py", "tests/mcp_phase/test_04_experience.py"]),
    ("l4_min", ["tests/l4_min/test_l4_tasks.py", "tests/l4_min/test_l45.py"]),
    ("l50", ["tests/l50/test_l50_chaining.py", "tests/l50/test_l50_persistence.py",
             "tests/l50/test_l50_review.py"]),
    ("l51", ["tests/l51/test_l51_evolution.py", "tests/l51/test_l51_e2e.py"]),
    ("l5253", ["tests/l5253/test_l5253_monitor.py", "tests/l5253/test_l5253_experience_xfer.py",
               "tests/l5253/test_l5253_monitor_routes.py"]),
    ("l60", ["tests/l60/test_l60_strategy.py", "tests/l60/test_l60_pattern_miner.py",
             "tests/l60/test_l60_fix_adaptive.py", "tests/l60/test_l60_runner_hooks.py",
             "tests/l60/test_l60_self_stats.py"]),
    ("l65", ["tests/l65/test_l65_issue_base.py", "tests/l65/test_l65_review_knowledge.py",
             "tests/l65/test_l65_suggest_tests.py", "tests/l65/test_l65_runner_hooks.py",
             "tests/l65/test_l65_gates.py"]),
    ("l70", ["tests/l70/test_l70_planner.py", "tests/l70/test_l70_scheduler.py",
             "tests/l70/test_l70_decisions.py", "tests/l70/test_l70_report.py",
             "tests/l70/test_l70_routes.py"]),
    ("l75", ["tests/l75/test_l75_connector.py", "tests/l75/test_l75_events.py"]),
]

PASSED_RE = re.compile(r"(\d+) passed")
FAILED_RE = re.compile(r"(\d+) failed|(\d+) errors?|(\d+) error")


def run_one(rel: str):
    p = subprocess.run([PY, "-X", "utf8", "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    passed = sum(int(m.group(1)) for m in PASSED_RE.finditer(out) if m.group(1))
    failed = sum(int(x) for m in FAILED_RE.finditer(out) for x in m.groups() if x)
    return passed, failed, out


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work" / "l75_baseline.txt"
    lines, grand_p, grand_f = [], 0, 0
    for name, files in SUITES:
        sp, sf = 0, 0
        for rel in files:
            if not os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))):
                print(f"[{name}] {rel}: (absent, skipped)")
                continue
            passed, failed, out = run_one(rel)
            sp += passed
            sf += failed
            status = "PASS" if failed == 0 and passed > 0 else "FAIL"
            lines.append(f"[{name}] {rel}: {passed} passed, {failed} failed/error -> {status}")
            if status == "FAIL":
                tail = "\n".join(out.splitlines()[-25:])
                lines.append("---- output tail ----\n" + tail + "\n---------------------")
        grand_p += sp
        grand_f += sf
        lines.append(f"== {name}: {sp} passed, {sf} failed ==")
        lines.append("")
    lines.append(f"TOTAL: {grand_p} passed, {grand_f} failed")
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    print(text)
    return 0 if grand_f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
