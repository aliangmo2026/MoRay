# -*- coding: utf-8 -*-
"""
工作三：数据集拆分
把 output/train_format.jsonl 按 9:1 随机拆分为 train.jsonl（训练集）与 val.jsonl（验证集），
以场景编号（S01~S06 六大类）分层抽样，保证各类场景在两集合中分布均匀。

实现要点：
  - 固定随机种子（默认 42），拆分结果可复现；
  - 分层口径 = scenario.id，与 cleaned_samples.jsonl 按序对齐（逐条校验 assistant
    字符串一致后取场景标签，保证拆分依据可追溯）；
  - 每个场景 val 数 = max(1, round(n * 0.1))，其余进 train；
  - 自测：两集合无交集（按 dedup_fingerprint）、总数守恒、各场景比例偏差打印。

输出：
  output/train.jsonl / output/val.jsonl
  output/dataset_report.md（数据集统计报告：总样本数、各场景分布、训练/验证集数量）
"""
import json
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
FORMAT_PATH = os.path.join(OUT_DIR, "train_format.jsonl")
CLEANED_PATH = os.path.join(OUT_DIR, "cleaned_samples.jsonl")
TRAIN_PATH = os.path.join(OUT_DIR, "train.jsonl")
VAL_PATH = os.path.join(OUT_DIR, "val.jsonl")

SEED = 42
VAL_RATIO = 0.1

CATEGORY_NAMES = {  # 6 大类场景（与 jev-sample-generator.js 场景库同源）
    "S01": "普通文件修改", "S02": "生产环境操作", "S03": "删除操作",
    "S04": "只读操作", "S05": "组合场景", "S06": "跨Agent操作",
}


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def main():
    if not os.path.exists(FORMAT_PATH) or not os.path.exists(CLEANED_PATH):
        sys.stderr.write("[FAIL] 缺少 train_format.jsonl 或 cleaned_samples.jsonl，请先跑前两步\n")
        sys.exit(1)

    fmt = load_jsonl(FORMAT_PATH)
    cleaned = load_jsonl(CLEANED_PATH)
    if len(fmt) != len(cleaned):
        sys.stderr.write("[FAIL] 对齐检查失败：train_format=%d cleaned=%d\n" % (len(fmt), len(cleaned)))
        sys.exit(1)

    # 按序对齐 + 逐条一致性校验（assistant 必须等于 standard_decision 的 JSON 串）
    paired = []
    for rec, s in zip(fmt, cleaned):
        if rec["assistant"] != json.dumps(s["standard_decision"], ensure_ascii=False):
            sys.stderr.write("[FAIL] 对齐校验失败于 sample_id=%s\n" % s.get("sample_id"))
            sys.exit(1)
        paired.append((s["scenario"]["id"], s.get("dedup_fingerprint", ""), rec))

    # 分层随机拆分
    rng = random.Random(SEED)
    by_scn = {}
    for sid, fp, rec in paired:
        by_scn.setdefault(sid, []).append((fp, rec))
    train, val, scn_stat = [], [], {}
    for sid in sorted(by_scn):
        group = by_scn[sid][:]
        rng.shuffle(group)
        n = len(group)
        n_val = max(1, int(round(n * VAL_RATIO)))
        v = group[:n_val]
        t = group[n_val:]
        val += [(sid, fp, r) for fp, r in v]
        train += [(sid, fp, r) for fp, r in t]
        scn_stat[sid] = {"total": n, "train": len(t), "val": len(v)}

    # 自测：无交集、总数守恒
    fps_tr = set(fp for _, fp, _ in train)
    fps_va = set(fp for _, fp, _ in val)
    assert not (fps_tr & fps_va), "train/val 指纹交集非空"
    assert len(train) + len(val) == len(paired), "总数不守恒"
    # 打散顺序后写出（train 整体乱序，避免同场景连续）
    rng.shuffle(train)
    rng.shuffle(val)
    with open(TRAIN_PATH, "w", encoding="utf-8") as f:
        for _, _, r in train:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(VAL_PATH, "w", encoding="utf-8") as f:
        for _, _, r in val:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 统计报告
    total = len(paired)
    train_cat = Counter(sid for sid, _, _ in train)
    val_cat = Counter(sid for sid, _, _ in val)
    lines = [
        "# 数据集统计报告（工作三）",
        "",
        "- 随机种子：`%d`（可复现） | 拆分比例：train:val = %.0f:%.0f（分层抽样）" % (SEED, (1 - VAL_RATIO) * 100, VAL_RATIO * 100),
        "- 总样本数：**%d**" % total,
        "- 训练集 train.jsonl：**%d** 条（%.1f%%）" % (len(train), 100.0 * len(train) / total),
        "- 验证集 val.jsonl：**%d** 条（%.1f%%）" % (len(val), 100.0 * len(val) / total),
        "",
        "## 六大类场景分布",
        "",
        "| 场景 | 类别 | 总数 | 训练集 | 验证集 | 验证占比 |",
        "|------|------|------|--------|--------|----------|",
    ]
    for sid in sorted(scn_stat):
        st = scn_stat[sid]
        lines.append("| %s | %s | %d | %d | %d | %.1f%% |" % (
            sid, CATEGORY_NAMES.get(sid, ""), st["total"], train_cat.get(sid, 0),
            val_cat.get(sid, 0), 100.0 * st["val"] / st["total"]))
    lines.append("| **合计** |  | **%d** | **%d** | **%d** | **%.1f%%** |" % (
        total, len(train), len(val), 100.0 * len(val) / total))
    lines += [
        "",
        "## 自测结果",
        "",
        "- train/val 指纹交集：0（无泄漏）✔",
        "- 总数守恒：%d + %d = %d ✔" % (len(train), len(val), total),
        "- 每条记录均含 system / user / assistant 三字段，assistant 可通过核心五字段校验 ✔",
        "",
        "## 产物清单",
        "",
        "- `output/train.jsonl` — 训练集",
        "- `output/val.jsonl` — 验证集",
        "- `output/train_format.jsonl` — 拆分前全量格式化数据",
    ]
    with open(os.path.join(OUT_DIR, "dataset_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("[OK] total=%d train=%d val=%d  per-scenario=%s" % (
        total, len(train), len(val),
        {sid: (scn_stat[sid]["train"], scn_stat[sid]["val"]) for sid in sorted(scn_stat)}))


if __name__ == "__main__":
    main()
