# -*- coding: utf-8 -*-
"""
工作一：样本清洗与筛选
扫描 ../samples/ 目录，仅保留 decision_origin = "llm" 的有效样本，
剔除所有 dry-run（rule-engine-dryrun）、规则回退（rule-engine-fallback）样本。

输出：
  - output/cleaned_samples.jsonl  （每行一个清洗后的完整样本对象）
  - output/cleaning_report.md     （清洗统计报告）
纯标准库实现，零依赖。
"""
import json
import os
import glob
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES_DIR = os.path.normpath(os.path.join(HERE, os.pardir, "samples"))
OUT_DIR = os.path.join(HERE, "output")

# 仅保留该来源；其余来源视为 dry-run / 规则回退并剔除
KEEP_ORIGIN = "llm"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(SAMPLES_DIR, "*.json")))

    total = 0
    kept = []
    dropped_origin = {}          # 各非 llm 来源计数
    dropped_bad = 0              # 解析失败 / 结构不完整
    dropped_dup = 0              # 指纹去重丢弃（防 train/val 泄漏）
    kept_fingerprints = set()

    for path in files:
        total += 1
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                s = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            dropped_bad += 1
            continue

        if not isinstance(s, dict):
            dropped_bad += 1
            continue

        origin = s.get("decision_origin")
        if origin != KEEP_ORIGIN:
            key = origin if isinstance(origin, str) and origin else "<missing>"
            dropped_origin[key] = dropped_origin.get(key, 0) + 1
            continue

        # 有效样本必备结构：request.query / standard_decision / scenario.id
        req = s.get("request")
        sd = s.get("standard_decision")
        sc = s.get("scenario")
        if not (isinstance(req, dict) and isinstance(req.get("query"), str) and req.get("query")):
            dropped_bad += 1
            continue
        if not (isinstance(sd, dict) and sd):
            dropped_bad += 1
            continue
        if not (isinstance(sc, dict) and isinstance(sc.get("id"), str) and sc.get("id")):
            dropped_bad += 1
            continue

        fp = s.get("dedup_fingerprint") or os.path.basename(path)
        if fp in kept_fingerprints:   # 跨文件指纹去重，防 train/val 泄漏
            dropped_dup += 1
            continue
        kept_fingerprints.add(fp)
        s["_source_file"] = os.path.basename(path)
        kept.append(s)

    jsonl_path = os.path.join(OUT_DIR, "cleaned_samples.jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for s in kept:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 清洗报告
    dropped_non_llm = sum(dropped_origin.values())
    lines = []
    lines.append("# 样本清洗报告（工作一）")
    lines.append("")
    lines.append("- 扫描目录：`%s`" % SAMPLES_DIR)
    lines.append("- 扫描文件总数：**%d**" % total)
    lines.append("- 保留（decision_origin = llm，去重后）：**%d**" % len(kept))
    lines.append("- 剔除·非 llm 来源（dry-run / 规则回退）：**%d**" % dropped_non_llm)
    for k in sorted(dropped_origin):
        lines.append("    - `%s`：%d" % (k, dropped_origin[k]))
    lines.append("- 剔除·结构不完整 / 解析失败：**%d**" % dropped_bad)
    lines.append("- 剔除·指纹去重：**%d**" % dropped_dup)
    lines.append("")
    lines.append("输出文件：`output/cleaned_samples.jsonl`（%d 条）" % len(kept))
    lines.append("")

    # 各场景保留分布
    cat = Counter(s["scenario"]["id"] for s in kept)
    catname = {}
    for s in kept:
        catname[s["scenario"]["id"]] = s["scenario"].get("category", "")
    lines.append("## 清洗后各场景分布")
    lines.append("")
    lines.append("| 场景 | 类别 | 保留数 |")
    lines.append("|------|------|--------|")
    for sid in sorted(cat):
        lines.append("| %s | %s | %d |" % (sid, catname.get(sid, ""), cat[sid]))
    lines.append("| **合计** |  | **%d** |" % len(kept))

    with open(os.path.join(OUT_DIR, "cleaning_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("[OK] cleaned=%d  non-llm_dropped=%d  bad_dropped=%d  -> output/cleaned_samples.jsonl"
          % (len(kept), dropped_non_llm, dropped_bad))


if __name__ == "__main__":
    main()
