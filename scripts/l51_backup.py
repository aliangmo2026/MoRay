# -*- coding: utf-8 -*-
"""L5.1 阶段0：全量备份 → D:\\MoRay\\backups\\l51\\，SHA256 校验，写 MANIFEST.md。"""
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\MoRay")
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DEST = ROOT / "backups" / "l51"

FILES = [
    "knowledge/experience_base.py",       # 经验库核心（本次要大改）
    "knowledge/__init__.py",              # 经验库存储说明
    "knowledge/experience_base.jsonl",    # 经验数据基线（运行时追加，备份为参考态）
    "server/app/task_routes.py",          # 任务路由
    "server/app/config.py",
    "engine/task_runner.py",              # knowledge_read/knowledge_write 唯一运行时消费者
    "templates/error_fix_flow.yaml",
    "templates/test_run_flow.yaml",
    "templates/text_fix_flow.yaml",
    "templates/code_review_flow.yaml",
    "tests/l50/test_l50_chaining.py",
    "tests/l50/test_l50_persistence.py",
    "tests/l50/test_l50_review.py",
    "tests/mcp_phase/test_04_experience.py",  # 钉死经验库既有 schema
]
OPTIONAL = [".env", "server/.env"]  # 存在则备


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
        dst = DEST / f"{flat}_l51_{TS}.bak"
        shutil.copy2(src, dst)
        h_src, h_dst = sha256(src), sha256(dst)
        ok = h_src == h_dst
        rows.append((rel, dst.name, h_src, "OK" if ok else "MISMATCH"))
        if not ok:
            missing.append(rel + " (hash mismatch)")

    man = DEST / "MANIFEST.md"
    lines = [
        f"# L5.1 备份清单 (MANIFEST)",
        "",
        f"- 时间戳：{TS}",
        f"- 备份目录：{DEST}",
        f"- 文件数：{len(rows)}",
        "- 回滚方法：将 `.bak` 去掉尾部 `_l51_{TS}.bak` 还原为原相对路径（见 l51_rollback.md / scripts/l51_rollback.py）",
        "",
        "| 原路径 | 备份文件 | SHA256(源) | 校验 |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r} | {b} | {h} | {s} |" for r, b, h, s in rows]
    man.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"backed up {len(rows)} files -> {DEST}")
    print(f"manifest: {man}")
    if missing:
        print("FAILED entries:")
        for m in missing:
            print("  -", m)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
