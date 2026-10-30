# -*- coding: utf-8 -*-
"""L6.5 阶段0：全量备份 → D:\\MoRay\\backups\\l65\\，SHA256 逐一校验，写 MANIFEST.md。

范式对齐 scripts/l60_backup.py；范围按 L6.5 任务书 0.1
（knowledge/ 全目录 + 四模板 + task_runner + 配置 + tests/l60 + task_routes）。
"""
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\MoRay")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DEST = ROOT / "backups" / "l65"

FILES = [
    # knowledge/ 全目录（统一问题库的主要扩展面）
    "knowledge/experience_base.py",
    "knowledge/strategy_optimizer.py",
    "knowledge/pattern_miner.py",
    "knowledge/__init__.py",
    "knowledge/experience_base.jsonl",
    # 四模板（重点：code_review / test_run / error_fix / text_fix）
    "templates/error_fix_flow.yaml",
    "templates/test_run_flow.yaml",
    "templates/text_fix_flow.yaml",
    "templates/code_review_flow.yaml",
    # 任务引擎与路由
    "engine/task_runner.py",
    "server/app/task_routes.py",
    "server/app/config.py",
    "server/app/self_routes.py",
    "server/app/api.py",
    # L6.0 回归测试（红线5：不破坏）
    "tests/l60/test_l60_strategy.py",
    "tests/l60/test_l60_pattern_miner.py",
    "tests/l60/test_l60_fix_adaptive.py",
    "tests/l60/test_l60_runner_hooks.py",
    "tests/l60/test_l60_self_stats.py",
]
OPTIONAL = [
    ".env", "server/.env", "server/.env.example",
    "knowledge/experience_stats.json",
    "knowledge/strategy_stats.json",
    "knowledge/pattern_candidates.json",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for rel in FILES + OPTIONAL:
        src = ROOT / rel
        if not src.exists():
            if rel in OPTIONAL:
                continue
            missing.append(rel)
            continue
        flat = rel.replace("/", "_").replace("\\", "_")
        dst = DEST / ("%s_l65_%s.bak" % (flat, TS))
        shutil.copy2(src, dst)
        ok = sha256(src) == sha256(dst)
        rows.append((rel, dst.name, sha256(src), "OK" if ok else "MISMATCH"))
        if not ok:
            missing.append(rel + " (hash mismatch)")

    man = DEST / "MANIFEST.md"
    lines = [
        "# L6.5 跨任务知识迁移 备份清单 (MANIFEST)",
        "",
        "- 时间戳：%s" % TS,
        "- 备份目录：%s" % DEST,
        "- 文件数：%d" % len(rows),
        "- 回滚：scripts/l65_rollback.py（rollback/reapply/verify-pre/verify-post），"
        "哈希表见下（回滚目标 = 本表 SHA256）",
        "",
        "| 原路径 | 备份文件 | SHA256(源) | 校验 |",
        "|---|---|---|---|",
    ]
    lines += ["| %s | %s | %s | %s |" % r for r in rows]
    man.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("backed up %d files -> %s" % (len(rows), DEST))
    print("manifest: %s" % man)
    import random
    file_rows = [r for r in rows if r[3] == "OK"]
    picks = file_rows[:3] if len(file_rows) <= 3 else random.Random(11).sample(file_rows, 3)
    print("spot check (3 files):")
    spot_ok = True
    for rel, bak, h, _ in picks:
        got = sha256(DEST / bak)
        same = got == h and sha256(ROOT / rel) == h
        spot_ok = spot_ok and same
        print("  %-44s %s %s" % (rel, h[:16], "OK" if same else "FAIL"))
    if missing:
        print("FAILED entries:")
        for m in missing:
            print("  -", m)
        return 1
    return 0 if spot_ok else 1


if __name__ == "__main__":
    sys.exit(main())
