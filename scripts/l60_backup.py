# -*- coding: utf-8 -*-
"""L6.0 阶段0：全量备份 → D:\\MoRay\\backups\\l60\\，SHA256 逐一校验，写 MANIFEST.md。

范式对齐 scripts/l5253_backup.py；范围按 L6 任务书 0.1（经验库/任务引擎/模板/配置/
L5.2.5 测试/路由 + 含 diagnose/fix/strategy 逻辑的接线文件 + 前端接管分片）。
"""
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\MoRay")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DEST = ROOT / "backups" / "l60"

FILES = [
    # 经验库核心（L6 主要改这里）
    "knowledge/experience_base.py",
    "knowledge/__init__.py",
    "knowledge/experience_base.jsonl",
    # 任务引擎（策略自适应接线点）
    "engine/task_runner.py",
    # 四个模板
    "templates/error_fix_flow.yaml",
    "templates/test_run_flow.yaml",
    "templates/text_fix_flow.yaml",
    "templates/code_review_flow.yaml",
    # 配置与路由
    "server/app/config.py",
    "server/app/api.py",
    "server/app/main.py",
    "server/app/task_routes.py",
    "server/app/monitor_routes.py",
    # 诊断/适配链路（只读备份，防审计遗漏）
    "adapters/routes.py",
    "adapters/iris_mcp_client.py",
    "adapters/local_log_adapter.py",
    # L5.2/5.3 回归测试（红线5：不破坏）
    "tests/l5253/test_l5253_monitor.py",
    "tests/l5253/test_l5253_experience_xfer.py",
    "tests/l5253/test_l5253_monitor_routes.py",
    "tests/l51/test_l51_evolution.py",
    "tests/l51/test_l51_e2e.py",
    "tests/mcp_phase/test_04_experience.py",
    # 前端接管分片（阶段5 卡片落点）+ 构建器
    "parts/88_local_log_intent.js",
    "assemble.py",
]
OPTIONAL = [
    ".env", "server/.env", "server/.env.example",
    "knowledge/experience_stats.json",
    "work/monitor_state",        # 目录 copytree
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            missing.append(rel)
            continue
        flat = rel.replace("/", "_").replace("\\", "_")
        dst = DEST / ("%s_l60_%s.bak" % (flat, TS))
        shutil.copy2(src, dst)
        ok = sha256(src) == sha256(dst)
        rows.append((rel, dst.name, sha256(src), "OK" if ok else "MISMATCH"))
        if not ok:
            missing.append(rel + " (hash mismatch)")
    for rel in OPTIONAL:
        src = ROOT / rel
        if not src.exists():
            continue
        flat = rel.replace("/", "_").replace("\\", "_")
        if src.is_dir():
            dst = DEST / (flat + "_l60_%s.dir" % TS)
            shutil.copytree(src, dst)
            rows.append((rel, dst.name + "/", "(dir)", "OK"))
        else:
            dst = DEST / ("%s_l60_%s.bak" % (flat, TS))
            shutil.copy2(src, dst)
            ok = sha256(src) == sha256(dst)
            rows.append((rel, dst.name, sha256(src), "OK" if ok else "MISMATCH"))
            if not ok:
                missing.append(rel + " (hash mismatch)")

    man = DEST / "MANIFEST.md"
    lines = [
        "# L6.0 自我进化引擎 备份清单 (MANIFEST)",
        "",
        "- 时间戳：%s" % TS,
        "- 备份目录：%s" % DEST,
        "- 文件数：%d" % len(rows),
        "- 回滚：scripts/l60_rollback.py（rollback/reapply/verify-pre/verify-post），"
        "哈希表见下（回滚目标 = 本表 SHA256）",
        "",
        "| 原路径 | 备份文件 | SHA256(源) | 校验 |",
        "|---|---|---|---|",
    ]
    lines += ["| %s | %s | %s | %s |" % r for r in rows]
    man.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("backed up %d files -> %s" % (len(rows), DEST))
    print("manifest: %s" % man)
    # 抽 3 个文件做备份↔源 双向哈希复核（任务书 0.2）
    import random
    file_rows = [r for r in rows if r[3] == "OK" and not r[1].endswith("/")]
    picks = file_rows[:3] if len(file_rows) <= 3 else random.Random(7).sample(file_rows, 3)
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
