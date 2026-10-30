# -*- coding: utf-8 -*-
"""L5.2+L5.3 阶段0：全量备份 → D:\\MoRay\\backups\\l5253\\，SHA256 逐一校验，写 MANIFEST.md。

范式对齐 scripts/l51_backup.py，范围按本次任务书 0.1 扩到审计点名的全部文件
（日志适配器 / 意图路由 / 前端接管分片 / 经验库数据与元数据 / 回归测试）。
"""
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\MoRay")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DEST = ROOT / "backups" / "l5253"

# 必备份：审计点名的经验/任务/适配器/前端/测试文件
FILES = [
    # 经验库（L5.1 刚改、本次 L5.3 还要改）
    "knowledge/experience_base.py",
    "knowledge/__init__.py",
    "knowledge/experience_base.jsonl",
    # 任务编排与路由
    "server/app/task_routes.py",
    "engine/task_runner.py",
    # 配置
    "server/app/config.py",
    "server/app/api.py",
    "server/app/main.py",
    # 日志采集 / MCP 适配（L5.2 复用其规则）
    "adapters/local_log_adapter.py",
    "adapters/routes.py",
    "adapters/iris_mcp_client.py",
    "adapters/__init__.py",
    # 模板全量
    "templates/error_fix_flow.yaml",
    "templates/test_run_flow.yaml",
    "templates/text_fix_flow.yaml",
    "templates/code_review_flow.yaml",
    # 回归测试（L5.0/L5.1 基线钉死者）
    "tests/l51/test_l51_evolution.py",
    "tests/l51/test_l51_e2e.py",
    "tests/l50/test_l50_chaining.py",
    "tests/l50/test_l50_persistence.py",
    "tests/l50/test_l50_review.py",
    "tests/mcp_phase/test_02_task_runner.py",
    "tests/mcp_phase/test_03_adapters.py",
    "tests/mcp_phase/test_04_experience.py",
    # 前端接管分片（主动介入落点）
    "parts/88_local_log_intent.js",
]
# 存在则备（运行时产物/凭据文件，不作为失败项）
OPTIONAL = [
    ".env", "server/.env",
    "knowledge/experience_stats.json",
    "assemble.py", "start_moray.bat",
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for rel in FILES + OPTIONAL:
        src = ROOT / rel
        if not src.is_file():
            if rel in OPTIONAL:
                continue
            missing.append(rel)
            continue
        flat = rel.replace("/", "_").replace("\\", "_")
        dst = DEST / ("%s_l5253_%s.bak" % (flat, TS))
        shutil.copy2(src, dst)
        h_src, h_dst = sha256(src), sha256(dst)
        ok = h_src == h_dst
        rows.append((rel, dst.name, h_src, "OK" if ok else "MISMATCH"))
        if not ok:
            missing.append(rel + " (hash mismatch)")

    man = DEST / "MANIFEST.md"
    lines = [
        "# L5.2+L5.3 备份清单 (MANIFEST)",
        "",
        "- 时间戳：%s" % TS,
        "- 备份目录：%s" % DEST,
        "- 文件数：%d" % len(rows),
        "- 回滚方法：按本表 SHA256 还原（见 scripts/l5253_rollback.py 与 docs/delivery/l5253/l5253_rollback.md）",
        "",
        "| 原路径 | 备份文件 | SHA256(源) | 校验 |",
        "|---|---|---|---|",
    ]
    lines += ["| %s | %s | %s | %s |" % r for r in rows]
    man.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("backed up %d files -> %s" % (len(rows), DEST))
    print("manifest: %s" % man)
    if missing:
        print("FAILED entries:")
        for m in missing:
            print("  -", m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
