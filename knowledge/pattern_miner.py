# -*- coding: utf-8 -*-
"""PatternMiner：L6.0 自我进化引擎 · 错误模式自动发现（阶段3，2026-09-29）。

现状（审计 §3）：根因分类靠人工规则（_EXC_CAUSE 23 + _KW_RULES 13），没见过的错误
归"未分类"/原文截断键。本模块无监督兜底：

  * mine(force=False)：扫描经验 JSONL 里 normalize_cause 走到**兜底分支**的行
    （业务描述型，聚类文本源只用 error_type+root_cause_category——L5.1 教训：
    掺 error_snippet 会把不同根因过度收敛），difflib.SequenceMatcher 贪心聚类
    （阈值 SIM_THRESHOLD，种子=首成员）。
  * 聚类 ≥ MIN_CLUSTER(3) 条 → 生成**候选关键词规则**（共享信息词，剔除全局高频泛词）
    写入候选库 `pattern_candidates.json`（路径随经验库同目录，MORAY_PATTERN_CANDIDATES 可覆盖）。
  * 默认只生成建议（status="candidate"，不参与分类）；MORAY_PATTERN_MINER_AUTO=1 时
    新候选自动 status="active"（=自动生效）。人工确认 promote(pattern) 同样转 active。
  * 模式衰减：last_ts 超 DECAY_DAYS(30) 未再出现 → status="decayed"，退出运行时叠加表。
  * runtime_rules()：normalize_cause 兜底分支的叠加规则表（仅 active），按文件 mtime 缓存。

触发：ExperienceBase.record() 后 maybe_mine() 懒触发——新增记录 ≥ MINE_EVERY(20) 条或距上次
挖矿 ≥ MINE_DAYS(7) 天才真跑；全 try/except，绝不影响记库主链路。
闸：MORAY_PATTERN_MINER（默认开，关=只用人工规则）；MORAY_PATTERN_MINER_AUTO（默认关）。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "l60-1"
SIM_THRESHOLD = 0.55     # 聚类相似度阈值（SequenceMatcher.ratio）
MIN_CLUSTER = 3          # 生成候选规则的最小簇大小（任务书 3.3：出现 3 次以上）
MINE_EVERY = 20          # 懒触发：新增 20 条记录挖一次
MINE_DAYS = 7            # 懒触发：距上次 7 天
DECAY_DAYS = 30          # 模式衰减：30 天未出现 → decayed
MAX_TOKENS_PER_RULE = 4


def miner_on() -> bool:
    return str(os.environ.get("MORAY_PATTERN_MINER", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def auto_apply_on() -> bool:
    return str(os.environ.get("MORAY_PATTERN_MINER_AUTO", "0")).strip().lower() \
        in ("1", "true", "yes", "on")


def candidates_path() -> Path:
    p = os.environ.get("MORAY_PATTERN_CANDIDATES", "").strip()
    if p:
        return Path(p)
    db = os.environ.get("MORAY_EXPERIENCE_DB", "").strip()
    base = Path(db) if db else (Path(__file__).resolve().parent / "experience_base.jsonl")
    return base.with_name("pattern_candidates.json")


def _empty() -> Dict[str, Any]:
    return {"version": VERSION, "updated_at": "", "last_mine": None, "candidates": []}


def load_candidates() -> Dict[str, Any]:
    try:
        p = candidates_path()
        if not p.is_file():
            return _empty()
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == VERSION \
                and isinstance(data.get("candidates"), list):
            data.setdefault("last_mine", None)
            return data
        return _empty()
    except (OSError, json.JSONDecodeError, ValueError):
        return _empty()


def save_candidates(data: Dict[str, Any]) -> None:
    try:
        data["version"] = VERSION
        data["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        p = candidates_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


# ---------------- 聚类与规则生成 ----------------

def _row_text(row: Dict[str, Any]) -> str:
    t = re.sub(r"\s+", " ", "%s %s" % (str(row.get("error_type") or ""),
                                       str(row.get("root_cause_category") or ""))).strip().lower()
    return t


def _unclassified_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """normalize_cause 走到兜底分支的行（命中异常表/关键词规则的都不算未分类）。"""
    from knowledge.experience_base import normalize_cause
    out = []
    for row in rows:
        key = normalize_cause(row)
        if key in ("未分类",) or key == re.sub(r"\s+", " ", str(row.get("root_cause_category")
                                                                or "")).strip()[:60]:
            out.append(row)
    return out


def _cluster(texts: List[str]) -> List[int]:
    """贪心聚类：种子=首成员，ratio≥阈值入簇，否则自立簇。返回每条所属簇号。"""
    seeds: List[str] = []
    assign: List[int] = []
    for t in texts:
        hit = None
        for i, s in enumerate(seeds):
            if difflib.SequenceMatcher(None, s, t).ratio() >= SIM_THRESHOLD:
                hit = i
                break
        if hit is None:
            seeds.append(t)
            hit = len(seeds) - 1
        assign.append(hit)
    return assign


def _bigrams(text: str) -> set:
    """中文友好 shingle：双字组（中文无空格分词，\\w 会把整句吞成一个 token——实测教训）。"""
    t = re.sub(r"\s+", "", text)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _shared_phrases(members: List[Dict[str, Any]], gbg: Dict[str, int],
                    total: int) -> List[str]:
    """簇内共享短语：双字组达 2/3 成员频次、且在**簇外**行里不算泛 shingle → 拼成连续短语。
    （过滤只看簇外行——共享信号必然出现在簇内全部行里，按全量算会把目标簇自己杀掉，实测教训。）
    短语比散 shingle 更像"关键词规则"（订单金额分摊 vs 订|单|金|额）。"""
    need = max(2, int(len(members) * 2 / 3 + 0.999))
    freq: Dict[str, int] = {}
    for m in members:
        for bg in _bigrams(_row_text(m)):
            freq[bg] = freq.get(bg, 0) + 1
    other_rows = max(0, total - len(members))
    other_cap = max(2, int(other_rows * 0.6))
    shared = {bg for bg, n in freq.items()
              if n >= need and (gbg.get(bg, 0) - n) < other_cap}
    if not shared:
        return []
    seed = re.sub(r"\s+", "", _row_text(members[0]))
    phrases, run, covered = [], [], False
    for i in range(len(seed)):
        hit = i < len(seed) - 1 and seed[i:i + 2] in shared
        if hit:
            if not covered:
                run = [seed[i], seed[i + 1]] if i + 1 < len(seed) else [seed[i]]
                covered = True
            else:
                run.append(seed[i + 1])
        else:
            if covered and len(run) >= 2:
                phrases.append("".join(run))
            covered = False
    if covered and len(run) >= 2:
        phrases.append("".join(run))
    phrases = sorted(set(phrases), key=len, reverse=True)[:MAX_TOKENS_PER_RULE]
    if phrases:
        return phrases
    # 兜底：没有连续短语就取共享 shingle 按簇内频次前 N
    tops = sorted(shared, key=lambda b: (-freq[b], b))[:MAX_TOKENS_PER_RULE]
    return tops


def mine(force: bool = False) -> Dict[str, Any]:
    """跑一轮挖掘（懒触发判断在 maybe_mine）。返回摘要 dict，绝不抛异常。"""
    result = {"mined": False, "unclassified": 0, "clusters": 0,
              "candidates_new": 0, "decayed": 0, "reason": ""}
    try:
        from knowledge.experience_base import ExperienceBase
        rows = ExperienceBase()._load()
        data = load_candidates()
        lm = data.get("last_mine") or {}
        if force:
            pass
        elif lm:
            since = len(rows) - int(lm.get("records") or 0)
            age = time.time() - float(lm.get("ts") or 0)
            if since < MINE_EVERY and age < MINE_DAYS * 86400:
                result["reason"] = "未达触发阈值（新增 %d 条 / %.1f 天）" % (since, age / 86400)
                return result
        elif len(rows) < MINE_EVERY:
            result["reason"] = "冷启动：记录 %d 条未达 %d 条阈值" % (len(rows), MINE_EVERY)
            return result
        unc = _unclassified_rows(rows)
        result["unclassified"] = len(unc)
        # 全局泛 shingle 频（用于剔除哪簇都有的双字组）
        gbg: Dict[str, int] = {}
        for r in unc:
            for bg in _bigrams(_row_text(r)):
                gbg[bg] = gbg.get(bg, 0) + 1
        assign = _cluster([_row_text(r) for r in unc]) if unc else []
        groups: Dict[int, List[Dict[str, Any]]] = {}
        for i, g in enumerate(assign):
            groups.setdefault(g, []).append(unc[i])
        known = {c.get("pattern") for c in data["candidates"]}
        auto = auto_apply_on()
        new = 0
        for members in groups.values():
            if len(members) < MIN_CLUSTER:
                continue
            toks = _shared_phrases(members, gbg, len(unc))
            if not toks:
                continue
            pattern = "|".join(re.escape(t) for t in toks)
            if pattern in known:
                continue
            key = re.sub(r"\s+", " ", str(members[0].get("root_cause_category") or "")).strip()[:40] \
                or "自发现模式%d" % (len(data["candidates"]) + 1)
            last_ts = max(str(m.get("timestamp") or "") for m in members)
            data["candidates"].append({
                "pattern": pattern, "suggested_key": key, "tokens": toks,
                "times": len(members), "sample": _row_text(members[0])[:120],
                "first_ts": min(str(m.get("timestamp") or "") for m in members),
                "last_ts": last_ts, "status": "active" if auto else "candidate",
                "auto": auto,
            })
            known.add(pattern)
            new += 1
        # 模式衰减：候选/生效中的规则 30 天未再出现 → decayed（退出叠加表，不删，诚实留痕）
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        decayed = 0
        for c in data["candidates"]:
            if c.get("status") in ("candidate", "active") and str(c.get("last_ts") or ""):
                try:
                    age_days = (datetime.now(timezone.utc).astimezone()
                                - datetime.fromisoformat(c["last_ts"])).days
                except ValueError:
                    continue
                if age_days > DECAY_DAYS:
                    c["status"] = "decayed"
                    decayed += 1
        data["last_mine"] = {"records": len(rows), "ts": time.time(), "ts_iso": now_iso}
        save_candidates(data)
        result.update({"mined": True, "clusters": len([g for g in groups.values()
                                                       if len(g) >= MIN_CLUSTER]),
                       "candidates_new": new, "decayed": decayed})
        return result
    except Exception as e:  # noqa: BLE001
        result["reason"] = "%s: %s" % (type(e).__name__, e)
        return result


def maybe_mine() -> Dict[str, Any]:
    """ExperienceBase.record() 后的懒触发入口（闸关闭 = no-op）。"""
    if not miner_on():
        return {"mined": False, "reason": "gate_off"}
    return mine(force=False)


def promote(pattern: str) -> bool:
    """人工确认：候选 → active（进运行时叠加表）。找不到该 pattern 返回 False。"""
    data = load_candidates()
    hit = False
    for c in data["candidates"]:
        if c.get("pattern") == pattern and c.get("status") == "candidate":
            c["status"] = "active"
            hit = True
    if hit:
        save_candidates(data)
    return hit


# ---------------- 运行时叠加表（normalize_cause 兜底分支消费） ----------------

_RULES_CACHE: Dict[str, Any] = {"mtime": None, "rules": []}


def runtime_rules() -> List[Dict[str, str]]:
    """active 状态的自动模式规则 [{"pattern","key"}]；文件未变时走缓存。异常 → 空表。"""
    try:
        if not miner_on():
            return []
        p = candidates_path()
        if not p.is_file():
            return []
        mt = p.stat().st_mtime
        if _RULES_CACHE["mtime"] == mt:
            return _RULES_CACHE["rules"]
        rules = [{"pattern": c["pattern"], "key": c["suggested_key"]}
                 for c in load_candidates()["candidates"]
                 if c.get("status") == "active" and c.get("pattern") and c.get("suggested_key")]
        _RULES_CACHE["mtime"] = mt
        _RULES_CACHE["rules"] = rules
        return rules
    except Exception:  # noqa: BLE001
        return []
