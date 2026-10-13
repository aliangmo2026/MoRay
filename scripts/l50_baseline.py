# L5.0 全量回归基线：199（L4.5 新基线，逐文件跑法沿袭 l45_baseline.py）+ tests/l50/ 30 项 = 229。
import subprocess, sys, re, os

ROOT = r"D:\MoRay"
PY = os.path.join(ROOT, "server", ".venv", "Scripts", "python.exe")

GROUPS = {
    "baseline_root": ["test_budget.py", "test_budget_day24_11.py", "test_openai_compat.py", "test_security.py"],
    "baseline_tests": ["tests/test_engines.py", "tests/test_event_stream.py", "tests/test_mvp_e2e.py",
                        "tests/test_session_state.py",
                        "tests/test_zcode_adapter.py", "tests/test_zcode_adapter_hardened.py"],
    "mcp_phase": ["tests/mcp_phase/test_01_iris_server.py", "tests/mcp_phase/test_02_task_runner.py",
                   "tests/mcp_phase/test_03_adapters.py", "tests/mcp_phase/test_04_experience.py"],
    "l4_min": ["tests/l4_min/test_l4_tasks.py", "tests/l4_min/test_l45.py"],
    "l50": ["tests/l50/test_l50_chaining.py", "tests/l50/test_l50_persistence.py",
             "tests/l50/test_l50_review.py"],
}

pat = re.compile(r"(\d+) passed|(\d+) failed|(\d+) error")
total_pass = total_fail = 0
fails = []
for g, files in GROUPS.items():
    gp = gf = 0
    for f in files:
        if not os.path.exists(os.path.join(ROOT, f.replace("/", os.sep))):
            print(f"[{g}] {f}: (absent, skipped)")
            continue
        r = subprocess.run([PY, "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider", f],
                           cwd=ROOT, capture_output=True, text=True, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        passed = sum(int(m[0]) for m in pat.findall(out) if m[0])
        failed = sum(int(m[1]) or int(m[2]) for m in pat.findall(out) if m[1] or m[2])
        gp += passed; gf += failed
        status = "PASS" if r.returncode == 0 else "FAIL"
        print(f"[{g}] {f}: {passed} passed, {failed} failed/error -> {status}")
        if r.returncode != 0:
            fails.append((g, f, out[-1500:]))
    print(f"== {g}: {gp} passed, {gf} failed ==\n")
    total_pass += gp; total_fail += gf

print(f"TOTAL: {total_pass} passed, {total_fail} failed")
for g, f, tail in fails:
    print(f"\n----- FAILURE {g}/{f} -----\n{tail}")
sys.exit(1 if total_fail else 0)
