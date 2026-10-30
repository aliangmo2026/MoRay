# -*- coding: utf-8 -*-
"""/api/self/* —— L6.0 阶段5 自我评估仪表盘薄路由（2026-09-29）。

信封范式 = task_routes/monitor_routes：{ok,data} / {ok,code,message}。
挂载 = api.py 尾部两行折叠（monitor 同款先例），main.py 零改动。

端点：
  GET /api/self/stats   自我评估：全局/项目级成功率与耗时、Top5 高频与最难错误、
                        经验库规模、进化策略生效情况（闸状态 + 权重 Top + 跳过项 +
                        修复档位统计 + 模式发现计数）。只读聚合，无副作用。

数据源（全只读）：ExperienceBase 元数据（ensure_meta，缺/坏自愈重建）+
knowledge/strategy_optimizer 的 sidecar + knowledge/pattern_miner 的候选库。
任何一块异常 → 该块降级为 {"error": ...}，绝不 500（仪表盘宁缺勿炸）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from knowledge.experience_base import ExperienceBase

router = APIRouter()
logger = logging.getLogger("moray-self")

TOP_N = 5
MIN_TIMES_FOR_HARD = 2   # "最难错误"最少出现次数（1 次的样本无统计意义）


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _gates() -> Dict[str, bool]:
    """全部进化闸的当前状态（阶段6 验收"关闭后行为一致"的可视化依据）。"""
    try:
        import os
        from knowledge import pattern_miner as pm
        from knowledge import strategy_optimizer as so
        from knowledge.experience_base import _evolution_on, _rank_on, _global_on
        def _on(name: str) -> bool:
            return str(os.environ.get(name, "")).strip().lower() \
                not in ("0", "false", "off", "no")
        return {
            "experience_evolution": _evolution_on(),
            "cause_rank": _rank_on(),
            "experience_global": _global_on(),
            "strategy_optimize": so.strategy_on(),
            "fix_adaptive": so.fix_adaptive_on(),
            "pattern_miner": pm.miner_on(),
            "pattern_miner_auto": pm.auto_apply_on(),
            # 兜底键原样透出（诊断"为什么看起来没生效"时有用）
            "raw_env": {k: str(os.environ.get(k) or "")
                        for k in ("MORAY_STRATEGY_OPTIMIZE", "MORAY_FIX_ADAPTIVE",
                                  "MORAY_PATTERN_MINER", "MORAY_PATTERN_MINER_AUTO")},
        }
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


def _aggregate(meta: Dict[str, Any]) -> Dict[str, Any]:
    """项目桶 → 全局/项目级统计 + Top5 高频/最难。"""
    projects = meta.get("projects") or {}
    per_project: List[Dict[str, Any]] = []
    all_causes: Dict[str, Dict[str, Any]] = {}
    g_rec = g_ok = 0
    g_dur = g_dur_n = 0.0
    for pid, proj in projects.items():
        rec = int(proj.get("records") or 0)
        ok = int(proj.get("successes") or 0)
        dn = int(proj.get("duration_n") or 0)
        sdn = int(proj.get("success_duration_n") or 0)
        g_rec += rec
        g_ok += ok
        g_dur += float(proj.get("duration_sum") or 0)
        g_dur_n += dn
        top = (proj.get("top_root_causes") or [{}])[0]
        per_project.append({
            "project_id": pid,
            "records": rec,
            "successes": ok,
            "success_rate": round(ok / rec, 3) if rec else 0.0,
            "avg_duration_sec": round(float(proj.get("duration_sum") or 0) / dn, 1) if dn else None,
            "success_avg_duration_sec": (round(float(proj.get("success_duration_sum") or 0) / sdn, 1)
                                         if sdn else None),
            "top_cause": (top or {}).get("root_cause_category", ""),
        })
    per_project.sort(key=lambda x: -x["records"])
    for pid, proj in projects.items():
        for key, c in (proj.get("causes") or {}).items():
            a = all_causes.setdefault(key, {"cause_key": key, "times": 0, "successes": 0,
                                            "projects": 0})
            a["times"] += int(c.get("times") or 0)
            a["successes"] += int(c.get("successes") or 0)
            a["projects"] += 1
    rows = []
    for a in all_causes.values():
        a["success_rate"] = round(a["successes"] / a["times"], 3) if a["times"] else 0.0
        rows.append(a)
    top_freq = sorted(rows, key=lambda x: (-x["times"], x["cause_key"]))[:TOP_N]
    hard_pool = [a for a in rows if a["times"] >= MIN_TIMES_FOR_HARD]
    hardest = sorted(hard_pool, key=lambda x: (x["success_rate"], -x["times"]))[:TOP_N]
    return {
        "global": {
            "records": g_rec,
            "successes": g_ok,
            "success_rate": round(g_ok / g_rec, 3) if g_rec else 0.0,
            "avg_duration_sec": round(g_dur / g_dur_n, 1) if g_dur_n else None,
            "projects": len(projects),
        },
        "per_project": per_project[:20],
        "top_errors": top_freq,
        "hardest_errors": hardest,
    }


def _evolution_view() -> Dict[str, Any]:
    """进化策略生效情况：权重 Top、连败跳过项、修复档位统计、模式发现计数。"""
    out: Dict[str, Any] = {}
    try:
        from knowledge import strategy_optimizer as so
        st = so.load_stats()
        weights: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        tiers: List[Dict[str, Any]] = []
        for pid, proj in (st.get("projects") or {}).items():
            for key, b in (proj.get("causes") or {}).items():
                row = {"project_id": pid, "cause_key": key,
                       "tries": int(b.get("tries") or 0), "wins": int(b.get("wins") or 0),
                       "consec_fail": int(b.get("consec_fail") or 0)}
                if row["consec_fail"] >= so.SKIP_CONSEC_FAIL:
                    skipped.append(row)
                else:
                    row["weight"] = so.weight(pid, key)
                    weights.append(row)
        weights.sort(key=lambda x: -x.get("weight", 0.0))
        for pid, t in (st.get("fix_tiers") or {}).items():
            for tier, b in t.items():
                tries = int(b.get("tries") or 0)
                tiers.append({"project_id": pid, "tier": tier,
                              "label": so.TIER_LABELS.get(tier, tier),
                              "tries": tries, "wins": int(b.get("wins") or 0),
                              "success_rate": round(int(b.get("wins") or 0) / tries, 3)
                              if tries else 0.0,
                              "consec_fail": int(b.get("consec_fail") or 0)})
        out["strategy"] = {"stats_path": str(so.stats_path()),
                           "tracked_causes": len(weights) + len(skipped),
                           "top_weights": weights[:TOP_N],
                           "skipped_causes": skipped[:TOP_N]}
        out["fix_tiers"] = tiers
    except Exception as e:  # noqa: BLE001
        out["strategy"] = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        from knowledge import pattern_miner as pm
        data = pm.load_candidates()
        cands = data.get("candidates") or []
        out["patterns"] = {
            "candidates_path": str(pm.candidates_path()),
            "total": len(cands),
            "candidate": sum(1 for c in cands if c.get("status") == "candidate"),
            "active": sum(1 for c in cands if c.get("status") == "active"),
            "decayed": sum(1 for c in cands if c.get("status") == "decayed"),
            "last_mine": data.get("last_mine"),
            "suggestions": [{"tokens": c.get("tokens"), "suggested_key": c.get("suggested_key"),
                             "times": c.get("times"), "status": c.get("status")}
                            for c in cands[:TOP_N]],
        }
    except Exception as e:  # noqa: BLE001
        out["patterns"] = {"error": "%s: %s" % (type(e).__name__, e)}
    return out


@router.get("/api/self/stats")
def self_stats():
    """自我评估仪表盘（只读聚合；各数据块独立降级，绝不 500）。"""
    data: Dict[str, Any] = {"gates": _gates()}
    try:
        eb = ExperienceBase()
        meta = eb.ensure_meta()
        data.update(_aggregate(meta))
        data["experience"] = {
            "path": str(eb.path),
            "records": int(meta.get("records") or 0),
            "projects": len(meta.get("projects") or {}),
            "causes_total": sum(len((p or {}).get("causes") or {})
                                for p in (meta.get("projects") or {}).values()),
            "global_causes": len(((meta.get("global") or {}).get("causes") or {})),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[self] experience aggregate failed: %s", e)
        data["experience"] = {"error": "%s: %s" % (type(e).__name__, e)}
    data.update(_evolution_view())
    return _ok(data)
