# -*- coding: utf-8 -*-
"""StrategyOptimizer：L6.0 自我进化引擎 · 策略层（阶段2 诊断 + 阶段4 修复档位，2026-09-29）。

设计约束（任务书 + docs/l60_audit.md §5）：
  * 纯逻辑零 LLM、零新依赖（stdlib）；全旁路式——无数据/关闸/异常 = 调用方行为与 L5 逐字节一致。
  * **不改 ExperienceBase.rank_candidates 内部**（L5.1/L5.3 测试直接钉死其桶序）：
    本模块在 runner 侧对其结果做**桶内**权重重排（桶间语义保持：优先验证恒先、置末恒后）。
  * 状态 sidecar `strategy_stats.json`（路径随经验库 JSONL 同目录，env MORAY_STRATEGY_STATS 可覆盖）：
    只存"经验库推导不出来的增量状态"（连续成败计数、档位成败）；损坏 → 就地重建空表（可自愈，
    权重本体随时可从经验库元数据重算）。tmp→replace 原子写，写失败静默（绝不影响主链路）。

阶段2（诊断策略自适应，闸 MORAY_STRATEGY_OPTIMIZE，默认开）：
  * weight(project_id, cause_key)：成功率 × log2(1+频次) × 时间衰减（半衰期 14 天）
    —— 不是简单频次排序；经验数据取自 ExperienceBase 元数据（ensure_meta）。
  * rank_with_strategy(...)：L5.1 桶**内**按 weight 重排；连续 SKIP_CONSEC_FAIL(3) 次验证失败的
    根因 → 标 strategy_action="skip" 并移到列表最末（不物理删，诚实留痕）。
  * classify_complexity / hint_top_n：简单错误（语法/缩进/除零/键值类）→ top_n 收敛 2；
    复杂错误（依赖/配置/网络/超时/环境）→ 放宽到 5；其余维持调用方基准值。
  * feedback(project_id, cause_key, success)：任务收尾（knowledge_write）反向调整连续成败计数。

阶段4（修复档位自适应，闸 MORAY_FIX_ADAPTIVE，默认开）：
  * 现状是两级降级链（审计 §2）：规范档 = real:zcode（真实引擎），快速档 = llm_patch（降级通道）。
  * pick_fix_tier(project_id, target_file)：
      核心文件（入口/配置类名正则）→ 恒规范档（风险感知）；
      该项目快速档历史成功率显著更高（≥2 次且高于规范档）→ 快速档；
      规范档连续 2 次失败且快速档未连续失败 → 快速档（回退加速）；
      快速档连续 2 次失败 → 回默认链（先真引擎）。
    返回 None = 维持默认链（先真引擎，模板 fallback 不动）。
  * feedback_fix(project_id, tier, success)：修完（验证出结果）后记档位成败。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATS_VERSION = "l60-1"
SKIP_CONSEC_FAIL = 3          # 阶段2：连续 3 次验证失败 → 下次排查自动跳过（置末+标注）
FALLBACK_FAST_CONSEC_FAIL = 2  # 阶段4：某档连续 2 次失败 → 跳过该档
HALF_LIFE_DAYS = 14.0         # 阶段2 时间衰减半衰期
CORE_FILE_RE = re.compile(
    r"(^|[\\/])(main|app|server|config|settings|index|core|run|start|bootstrap|"
    r"__init__|wsgi|asgi|manage|setup|conftest)\.(py|js|ts|tsx|jsx|go|java|rs)$", re.IGNORECASE)

SIMPLE_RE = re.compile(
    r"语法|缩进|除零|名称未定义|类型错误|映射键缺失|索引越界|取值不合法|空值|"
    r"syntaxerror|indentation|zerodivision|nameerror|typeerror|keyerror|indexerror|valueerror", re.IGNORECASE)
COMPLEX_RE = re.compile(
    r"依赖|导入|配置|环境|超时|网络|连接|权限|循环导入|文件或路径|"
    r"modulenotfound|importerror|timeout|connection|permission|circular|环境不一致", re.IGNORECASE)

_EXC_HINT = {
    "SyntaxError": "simple", "IndentationError": "simple", "ZeroDivisionError": "simple",
    "NameError": "simple", "TypeError": "simple", "KeyError": "simple",
    "IndexError": "simple", "ValueError": "simple",
    "ModuleNotFoundError": "complex", "ImportError": "complex", "TimeoutError": "complex",
    "ConnectionError": "complex", "ConnectionRefusedError": "complex", "PermissionError": "complex",
}


def strategy_on() -> bool:
    return str(os.environ.get("MORAY_STRATEGY_OPTIMIZE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def fix_adaptive_on() -> bool:
    return str(os.environ.get("MORAY_FIX_ADAPTIVE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


# ---------------- sidecar 状态 ----------------

def default_jsonl_path() -> Path:
    p = os.environ.get("MORAY_EXPERIENCE_DB", "").strip()
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "experience_base.jsonl"


def stats_path() -> Path:
    p = os.environ.get("MORAY_STRATEGY_STATS", "").strip()
    if p:
        return Path(p)
    return default_jsonl_path().with_name("strategy_stats.json")


def _empty_stats() -> Dict[str, Any]:
    return {"version": STATS_VERSION, "updated_at": "", "projects": {}, "fix_tiers": {}}


def load_stats() -> Dict[str, Any]:
    try:
        p = stats_path()
        if not p.is_file():
            return _empty_stats()
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == STATS_VERSION \
                and isinstance(data.get("projects"), dict) \
                and isinstance(data.get("fix_tiers"), dict):
            return data
        return _empty_stats()
    except (OSError, json.JSONDecodeError, ValueError):
        return _empty_stats()


def save_stats(stats: Dict[str, Any]) -> None:
    try:
        stats["version"] = STATS_VERSION
        stats["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        p = stats_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass  # 状态写失败绝不影响主链路（下次 feedback 再写）


def _bump(bin_: Dict[str, Any], success: bool) -> None:
    bin_["tries"] = int(bin_.get("tries", 0)) + 1
    bin_["wins"] = int(bin_.get("wins", 0)) + (1 if success else 0)
    bin_["consec_fail"] = 0 if success else int(bin_.get("consec_fail", 0)) + 1
    bin_["consec_ok"] = int(bin_.get("consec_ok", 0)) + 1 if success else 0
    bin_["last_ts"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------- 阶段2：诊断策略 ----------------

def _meta_cause(project_id: str, cause_key: str) -> Optional[Dict[str, Any]]:
    """经验库元数据里的该根因聚合行（无/异常 → None，权重退化为中性）。"""
    try:
        from knowledge.experience_base import ExperienceBase
        meta = ExperienceBase().ensure_meta()
        c = ((meta.get("projects") or {}).get(str(project_id)) or {}).get("causes") or {}
        row = c.get(str(cause_key))
        return row if isinstance(row, dict) else None
    except Exception:  # noqa: BLE001
        return None


def weight(project_id: str, cause_key: str, now: Optional[float] = None) -> float:
    """成功率 × log2(1+频次) × 时间衰减；sidecar 连续失败达阈值 → 0（跳过）。"""
    row = _meta_cause(project_id, cause_key)
    times = int((row or {}).get("times") or 0)
    wins = int((row or {}).get("successes") or 0)
    last_ts = str((row or {}).get("last_ts") or "")
    w = 0.0
    if times > 0:
        rate = wins / times
        w = rate * math.log2(1.0 + times)
        if last_ts:
            try:
                t = datetime.fromisoformat(last_ts).timestamp()
            except ValueError:
                t = 0.0
            if t > 0:
                days = max(0.0, (now if now is not None else time.time()) - t) / 86400.0
                w *= math.pow(0.5, days / HALF_LIFE_DAYS)
    st = load_stats()
    bin_ = (((st.get("projects") or {}).get(str(project_id)) or {}).get("causes") or {}) \
        .get(str(cause_key)) or {}
    if int(bin_.get("consec_fail", 0)) >= SKIP_CONSEC_FAIL:
        return 0.0
    return round(w, 4)


def rank_with_strategy(project_id: str, candidates: List[Dict[str, Any]],
                       keys: Optional[List[str]] = None
                       ) -> Tuple[List[Dict[str, Any]], str]:
    """对 L5.1 rank_candidates 的输出做**桶内**权重重排 + 连败跳过标注。

    输入候选需带 experience_match.cause_key（L5.1 已命中历史者）；未命中的候选权重按 0.5
    中性值参与桶内排序（历史无记录 = 不奖励也不惩罚，保持 LLM 相对序优先）。
    返回 (新列表, 说明)；无变化返回 (原列表拷贝, "")。绝不抛异常。
    """
    try:
        if not candidates:
            return list(candidates or []), ""
        keyed = []
        for i, c in enumerate(candidates):
            ck = str(((c or {}).get("experience_match") or {}).get("cause_key")
                     or (keys[i] if keys and i < len(keys) else "") or "")
            keyed.append((ck, i, c))
        weights = {ck: weight(project_id, ck) for ck, _, _ in keyed if ck}
        stats = load_stats()
        causes = ((stats.get("projects") or {}).get(str(project_id)) or {}).get("causes") or {}
        # 稳定排序：Python sort 稳定 → 同权重保持传入相对序（LLM 序不丢）。
        # 组 0 = 权重>0（有成功历史，按权重降序）；组 1 = 无成功历史/无记录
        # （无记录按中性 0.5 排在"0 成功"之前——LLM 序优先，不因无数据受罚）。
        order = sorted(range(len(keyed)),
                       key=lambda i: (0 if (keyed[i][0] and weights.get(keyed[i][0], 0.0) > 0)
                                      else 1,
                                      -(weights.get(keyed[i][0], 0.5) if keyed[i][0] else 0.5),
                                      i))
        # 连败跳过：无论排到哪，统一压到列表最末并标注（置末之后，最不浪费时间）
        out: List[Dict[str, Any]] = []
        skipped = 0
        for i in order:
            ck = keyed[i][0]
            cc = causes.get(ck) or {}
            if ck and int(cc.get("consec_fail", 0)) >= SKIP_CONSEC_FAIL:
                d = dict(candidates[i])
                d["strategy_action"] = "skip"
                d["strategy_note"] = "跳过（该项目连续 %d 次验证失败，本次仅兜底参考）" % SKIP_CONSEC_FAIL
                skipped += 1
                out.append(d)
            else:
                out.append(candidates[i])   # 原对象保留（未变时调用方判等零成本）
        if order == list(range(len(candidates))) and not skipped:
            return list(candidates), ""
        moved = sum(1 for pos, i in enumerate(order) if i != pos)
        note = ("L6 策略重排：桶内按权重（成功率×频次×时间衰减）调整 %d 项"
                % (moved + skipped))
        if skipped:
            note += "；跳过连败根因 %d 项" % skipped
        return out, note
    except Exception:  # noqa: BLE001
        import traceback as _tb
        if os.environ.get("MORAY_L60_DEBUG"):
            _tb.print_exc()
        return list(candidates or []), ""


def classify_complexity(error_type: str = "", cause_text: str = "") -> str:
    """错误复杂度三分类：simple（少排查）/ complex（深排查）/ normal。"""
    et = str(error_type or "").strip()
    if et in _EXC_HINT:
        return _EXC_HINT[et]
    text = "%s %s" % (et, cause_text)
    if COMPLEX_RE.search(text):
        return "complex"
    if SIMPLE_RE.search(text):
        return "simple"
    return "normal"


def hint_top_n(error_type: str = "", cause_text: str = "", base: int = 3) -> int:
    """深度自适应：简单错误收敛注入面（2），复杂错误放宽（5），其余维持 base。"""
    kind = classify_complexity(error_type, cause_text)
    if kind == "simple":
        return min(int(base), 2)
    if kind == "complex":
        return max(int(base), 5)
    return max(1, int(base))


def feedback(project_id: str, cause_key: str, success: bool) -> None:
    """任务收尾反馈：按 cause_key 累计连续成败（阶段2 反馈闭环的落库点）。"""
    if not (strategy_on() and project_id and cause_key):
        return
    try:
        st = load_stats()
        proj = st["projects"].setdefault(str(project_id), {})
        bin_ = proj.setdefault("causes", {}).setdefault(str(cause_key),
                                                        {"tries": 0, "wins": 0,
                                                         "consec_fail": 0, "consec_ok": 0,
                                                         "last_ts": ""})
        _bump(bin_, bool(success))
        save_stats(st)
    except Exception:  # noqa: BLE001
        pass


# ---------------- 阶段4：修复档位自适应 ----------------

TIER_STANDARD = "real:zcode"   # 规范档：真实 ZCode 引擎
TIER_FAST = "llm_patch"        # 快速档：LLM 整文件替换（降级通道）

TIER_LABELS = {TIER_STANDARD: "规范档（真实引擎）", TIER_FAST: "快速档（llm_patch）"}


def tier_of_engine(engine: str) -> Optional[str]:
    """StepOutcome.engine → 档位键（"real:zcode…" / "fallback:llm_patch(…)" → 归一）。"""
    e = str(engine or "")
    if e.startswith("real:zcode"):
        return TIER_STANDARD
    if "llm_patch" in e:
        return TIER_FAST
    return None


def is_core_file(target_file: str) -> bool:
    """风险感知：入口/配置类文件名 → 核心文件（恒走规范档）。"""
    return bool(CORE_FILE_RE.search(str(target_file or "").strip()))


def pick_fix_tier(project_id: str, target_file: str = "") -> Optional[Dict[str, Any]]:
    """修复前策略咨询：返回 None = 维持默认链（先真引擎，模板 fallback 语义不动）。"""
    if not (fix_adaptive_on() and project_id):
        return None
    try:
        st = load_stats()
        tiers = ((st.get("fix_tiers") or {}).get(str(project_id)) or {})
        fast = tiers.get(TIER_FAST) or {}
        std = tiers.get(TIER_STANDARD) or {}
        fast_rate = (fast.get("wins") / fast["tries"]) if fast.get("tries") else None
        std_rate = (std.get("wins") / std["tries"]) if std.get("tries") else None
        # 风险感知：核心文件恒规范档
        if is_core_file(target_file) and (fast.get("tries") or 0) > 0:
            return {"tier": TIER_STANDARD, "reason": "风险感知：核心文件恒走规范档（真实引擎）"}
        # 回退加速：规范档连败且快速档未连败 → 直接快速档
        if (int(std.get("consec_fail", 0)) >= FALLBACK_FAST_CONSEC_FAIL
                and int(fast.get("consec_fail", 0)) < FALLBACK_FAST_CONSEC_FAIL):
            return {"tier": TIER_FAST, "reason": "回退加速：真实引擎连续 %d 次失败，跳过直走快速档"
                    % int(std.get("consec_fail", 0))}
        # 档位偏好：快速档历史成功率显著更高（≥2 次且高于规范档/规范档无数据）
        if (fast_rate is not None and fast.get("tries", 0) >= 2 and fast_rate > 0
                and fast.get("consec_fail", 0) == 0
                and (std_rate is None or fast_rate > std_rate)):
            return {"tier": TIER_FAST, "reason": "档位偏好：本项目快速档成功率 %.0f%% 更高"
                    % (fast_rate * 100)}
        return None
    except Exception:  # noqa: BLE001
        return None


def feedback_fix(project_id: str, tier: Optional[str], success: bool) -> None:
    """修复收尾反馈：记档位成败（tier None = 非修复链路，忽略）。"""
    if not (fix_adaptive_on() and project_id and tier in (TIER_STANDARD, TIER_FAST)):
        return
    try:
        st = load_stats()
        proj = st["fix_tiers"].setdefault(str(project_id), {})
        bin_ = proj.setdefault(tier, {"tries": 0, "wins": 0,
                                      "consec_fail": 0, "consec_ok": 0, "last_ts": ""})
        _bump(bin_, bool(success))
        save_stats(st)
    except Exception:  # noqa: BLE001
        pass
