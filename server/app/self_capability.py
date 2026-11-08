# -*- coding: utf-8 -*-
"""自我能力图谱 self_capability（2026-11-06 第一战：让 MoRay 认清自己的能力边界）。

把散落三处的**真实**工具调用流水聚合成"各器官/各工具的成功率、平均耗时、失败
归因"，供 API / 自我叙事 / 自驱目标生成三方共用同一份事实（单一事实源，防三个
消费方各算各的、口径漂移）：

数据源（全只读 SELECT，绝不写任何库）：
  1. moray.sqlite3 ``agent_tool_log`` —— 内置工具审计 + [本战新增] MCP 网关
     结构化审计（mcp_gateway.execute_chat_tool 落 ``mcp__<organ>__<tool>`` 行，
     detail 带 ``source=mcp | code=<信封码>``）。此前 32 个 MCP 器官工具的调用
     只进 %TEMP% 文本日志与 night_shift 计数，能力图谱对它们是盲的——这是本战
     补上的写入侧缺口。
  2. moray.sqlite3 ``agent_tool_log.detail`` —— 失败归因（超时/鉴权/拦截/拒绝/
     预算熔断）的原始证据。
  3. synapse.sqlite3 ``synapse_messages`` —— 器官级任务成败事件
     （iris.fix.* / zcode.task.* / laya.task.* / argus.research.*），粒度比单次
     工具调用粗，作交叉印证（不同源、不同粒度，**不互相混算**）。

口径（写死在本模块，所有消费方只准从这里读）：
  * ``success_rate``          = ok / 全部尝试（含被拦截/被拒/待审批）——"我伸手
    去做一件事，做成了一件"的原始口径；拦截也是能力短板（例：Surgeon 被锁写
    拦截），所以算短板。
  * ``success_rate_executed`` = ok / 真正执行过的（ok+error+timeout）——纯执行
    能力口径，排除闸门噪声。
  * 弱器官（weak）= success_rate < 0.6 且样本 >= 5 的 organ（meta 闸门事件除外）。

安全边界（与 self_state / self_model / self_reflection 同款纪律）：
  - 对库只 SELECT；缓存是进程内 TTL 快照（60s），重启即重算——源数据本就落盘，
    这里不新增任何持久化面；
  - fail-open：任何一块读不到就如实带 {"status": "no_data", "reason"}，绝不编 0
    / 100 / 60%（编出来的分数会被叙事当成"我很差/我很准"的自我欺骗）；
  - SQL 一律字面量 + ? 参数绑定（禁拼接 / format / f-string 组装 SQL）；
  - 雷达文本只放事实数字，永不出现"应该/建议/需要"，也不带任何路径与密钥。
"""
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

from fastapi import APIRouter

logger = logging.getLogger("moray.self_capability")

# [坑复证 2026-11-06] moray 侧根 logger 只放行 ERROR 以上（mcp_gateway 同款教训），
# 本模块要输出 INFO/WARNING 就必须自挂 handler，真日志进 %TEMP% 的 moray 日志文件。
if not logger.handlers:  # 防重复初始化（reload 场景）
    try:
        from . import config as _cfg
        _h = logging.FileHandler(str(_cfg.LOG_DIR / "self_capability.log"),
                                 encoding="utf-8")
        _h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(_h)
    except Exception:  # noqa: BLE001 - 日志侧车挂不上不拦主流程
        pass
logger.setLevel(logging.INFO)

#: 统计窗口（天）：能力雷达默认看近 7 天——太短噪声大，太长就把"上上周的老毛病"
#: 也算在现在的自己头上。
_WINDOW_DAYS = 7
#: 缓存 TTL（秒）：叙事/反思/自驱/前端轮询共用，60s 足够新鲜且省 SQLite。
_CACHE_TTL_SEC = 60.0
#: 弱器官判定：成功率阈值（任务书口径 60%）
_WEAK_THRESHOLD = 0.6
#: 弱器官判定：最少样本（5 次以下不算"能力短板"，只算"没怎么用过"）
_WEAK_MIN_SAMPLES = 5
#: 拖油瓶榜：平均耗时 Top N（只统计真正执行过的调用）
_SLOWEST_TOP = 5

#: 工具名 → 器官归属（顺序即优先级；mcp__ 前缀按 server 段泛化，新器官零改动接入）
_ORGAN_RULES = (
    ("mcp__", "__mcp_server__"),   # 哨兵：mcp__<server>__<tool> → server 段
    ("read_file", "fs"), ("create_file", "fs"), ("write_file", "fs"),
    ("move_file", "fs"), ("delete_file", "fs"), ("find_files", "fs"),
    ("list_directory", "fs"),
    ("run_command", "shell"), ("run_pwsh", "shell"), ("run_codex", "shell"),
    ("run_python", "shell"), ("zcode_run", "zcode"),
    ("system_info", "system"), ("config_", "system"),
    ("budget_exceeded", "meta"),
)

#: 器官中文标签（雷达与叙事里露脸的名字；未知 organ 原样展示）
_ORGAN_LABELS = {
    "argus": "Argus 情报侦察",
    "iris": "Iris 视觉诊断",
    "scribe": "Scribe 档案扫描",
    "surgeon": "Surgeon 代码手术",
    "aiops": "AIOps 运维体检",
    "ghost": "Ghost 幽灵代理",
    "zcode": "ZCode 编码引擎",
    "laya": "Laya 任务编排",
    "fs": "本地文件操作",
    "shell": "命令行执行",
    "system": "系统配置",
    "meta": "闸门事件（非能力）",
}

#: 失败归因 → 中文标签（叙事里点名用的词）
_CAUSE_LABELS = {
    "timeout": "超时",
    "auth": "鉴权失败",
    "rejected": "安全拦截",
    "denied": "用户拒绝",
    "approval": "等待审批未执行",
    "budget": "预算/次数熔断",
    "error": "执行错误",
}

#: synapse 事件后缀 → 成败归类（organ 级交叉印证用）
_SYNAPSE_SUCCESS_SUFFIX = (".succeeded", ".completed", ".ok", ".delivered")
_SYNAPSE_FAIL_SUFFIX = (".failed", ".error", ".timeout", ".fused", ".rejected")

#: 进程内 TTL 缓存（重启清零即重算，源数据在库、零持久化面）
_cache_mono: float = 0.0
_cache_data: dict = {}


def organ_of(tool: str) -> str:
    """工具名 → 器官名。mcp__<server>__<tool> 取 server 段（泛化，新器官自动进
    图谱）；内置工具按 _ORGAN_RULES 前缀匹配；都不中 → "other"。纯函数零 IO。"""
    t = str(tool or "")
    if t.startswith("mcp__"):
        parts = t.split("__")
        if len(parts) >= 3 and parts[1]:
            return parts[1]
        return "other"
    for prefix, organ in _ORGAN_RULES:
        if prefix == "__mcp_server__":
            continue
        if t.startswith(prefix):
            return organ
    return "other"


def organ_label(organ: str) -> str:
    """器官名 → 中文标签（未知原样返回）。纯函数零 IO。"""
    return _ORGAN_LABELS.get(str(organ or ""), str(organ or "未知"))


def classify_failure(status: str, detail: str) -> str:
    """一次失败调用 → 归因码（_CAUSE_LABELS 的键）。纯函数零 IO。

    判定顺序即优先级：超时 > 鉴权 > 用户拒绝 > 安全拦截 > 等审批 > 预算 > 执行错误。
    证据来源：agent_tool_log.status 原值 + detail 文本（MCP 行带 code=<信封码>）。
    """
    s = str(status or "").lower()
    d = str(detail or "")
    dl = d.lower()
    if s == "timeout" or "timeout" in dl or "超时" in d:
        return "timeout"
    if "auth_failed" in dl or "401" in dl or "鉴权" in d or "unauthorized" in dl:
        return "auth"
    if s == "denied":
        return "denied"
    if s == "rejected":
        return "rejected"
    if s == "needs_approval":
        return "approval"
    if "budget" in dl or "预算" in d or "limit_reached" in dl:
        return "budget"
    return "error"


def _local_cutoff(window_days: int) -> str:
    """窗口起点（本地时间串，与两张库的 datetime('now','localtime') 同格式）。"""
    return (datetime.now() - timedelta(days=int(window_days or _WINDOW_DAYS))
            ).strftime("%Y-%m-%d %H:%M:%S")


def _read_agent_tool_block(window_days: int) -> dict:
    """agent_tool_log → 按工具 × 状态聚合 + 器官卷积 + 失败归因卷积。

    返回 {"status":"ok","total","ok","executed","tools":[...],"organs":[...],
    "failure_causes":{...}}；读不到 → {"status":"no_data","reason"}。绝不抛。
    """
    cutoff = _local_cutoff(window_days)
    try:
        from . import crud  # 惰性 import；crud._conn 自带 row_factory=Row（按列名取值）
        conn = crud._conn()
        try:
            rows = conn.execute(
                "SELECT tool, status, COUNT(*) AS c, SUM(ms) AS ms_sum, "
                "MAX(ms) AS ms_max, MAX(ts) AS last_ts "
                "FROM agent_tool_log WHERE ts >= ? GROUP BY tool, status",
                (cutoff,)).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：这块盲了不影响其他块
        logger.warning("[capability] agent_tool_log 聚合失败：%s: %s",
                       type(e).__name__, str(e)[:160])
        return {"status": "no_data",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:120])}

    # 状态值语义（crud.append_agent_log docstring）：
    #   ok / error / timeout / rejected / needs_approval / denied
    # 执行口径：ok+error+timeout 才是"真正跑到能力上的调用"。
    tools: dict = {}
    for r in rows:
        tool = str(r["tool"] or "")
        status = str(r["status"] or "").lower()
        c = int(r["c"] or 0)
        ms_sum = int(r["ms_sum"] or 0)
        ms_max = int(r["ms_max"] or 0)
        slot = tools.setdefault(tool, {
            "tool": tool, "organ": organ_of(tool),
            "total": 0, "ok": 0, "executed": 0,
            "status_counts": {}, "failure_causes": {},
            "ms_sum": 0, "ms_max": 0, "last_ts": "",
        })
        slot["total"] += c
        slot["status_counts"][status] = slot["status_counts"].get(status, 0) + c
        if status == "ok":
            slot["ok"] += c
        if status in ("ok", "error", "timeout"):
            slot["executed"] += c
            slot["ms_sum"] += ms_sum
            slot["ms_max"] = max(slot["ms_max"], ms_max)
        if status != "ok":
            cause = classify_failure(status, "")
            slot["failure_causes"][cause] = \
                slot["failure_causes"].get(cause, 0) + c
        slot["last_ts"] = max(slot["last_ts"], str(r["last_ts"] or ""))

    tool_rows = []
    for slot in tools.values():
        total = slot["total"]
        executed = slot["executed"]
        slot["success_rate"] = round(slot["ok"] / total, 3) if total else None
        slot["success_rate_executed"] = (
            round(slot["ok"] / executed, 3) if executed else None)
        slot["avg_ms"] = round(slot["ms_sum"] / executed, 1) if executed else None
        tool_rows.append(slot)
    tool_rows.sort(key=lambda x: (-x["total"], x["tool"]))

    # 器官卷积（工具 → 器官）：成功率用原始口径（拦截也算短板），耗时用执行口径
    organs: dict = {}
    for slot in tool_rows:
        o = organs.setdefault(slot["organ"], {
            "organ": slot["organ"], "label": organ_label(slot["organ"]),
            "tools_count": 0, "total": 0, "ok": 0, "executed": 0,
            "status_counts": {}, "failure_causes": {},
            "ms_sum": 0, "last_ts": "",
        })
        o["tools_count"] += 1
        o["total"] += slot["total"]
        o["ok"] += slot["ok"]
        o["executed"] += slot["executed"]
        o["ms_sum"] += slot["ms_sum"]
        for k, v in slot["status_counts"].items():
            o["status_counts"][k] = o["status_counts"].get(k, 0) + v
        for k, v in slot["failure_causes"].items():
            o["failure_causes"][k] = o["failure_causes"].get(k, 0) + v
        o["last_ts"] = max(o["last_ts"], slot["last_ts"])
    organ_rows = []
    for o in organs.values():
        o["success_rate"] = round(o["ok"] / o["total"], 3) if o["total"] else None
        o["success_rate_executed"] = (
            round(o["ok"] / o["executed"], 3) if o["executed"] else None)
        o["avg_ms"] = round(o["ms_sum"] / o["executed"], 1) if o["executed"] else None
        top_cause = (max(o["failure_causes"].items(), key=lambda kv: kv[1])
                     if o["failure_causes"] else None)
        o["top_failure"] = ({"cause": top_cause[0],
                             "label": _CAUSE_LABELS.get(top_cause[0], top_cause[0]),
                             "count": top_cause[1]} if top_cause else None)
        organ_rows.append(o)
    organ_rows.sort(key=lambda x: (-x["total"], x["organ"]))

    causes_total: dict = {}
    for slot in tool_rows:
        for k, v in slot["failure_causes"].items():
            causes_total[k] = causes_total.get(k, 0) + v
    total = sum(t["total"] for t in tool_rows)
    ok_total = sum(t["ok"] for t in tool_rows)
    return {"status": "ok", "total": total, "ok": ok_total,
            "executed": sum(t["executed"] for t in tool_rows),
            "tools": tool_rows, "organs": organ_rows,
            "failure_causes": causes_total}


def _read_synapse_block(window_days: int) -> dict:
    """synapse_messages → 器官级任务成败交叉印证（proxy_source 前缀 = 器官）。

    粒度与 agent_tool_log 不同（任务级 vs 调用级），只并列展示、不互相混算。
    读不到 → {"status":"no_data","reason"}。绝不抛。"""
    cutoff = _local_cutoff(window_days)
    try:
        from . import synapse_db
        conn = synapse_db.connect()
        try:
            rows = conn.execute(
                "SELECT proxy_source, COUNT(*) AS c FROM synapse_messages "
                "WHERE created_at >= ? AND proxy_source != '' "
                "GROUP BY proxy_source", (cutoff,)).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[capability] synapse 聚合失败：%s: %s",
                       type(e).__name__, str(e)[:160])
        return {"status": "no_data",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:120])}
    organs: dict = {}
    for r in rows:
        src = str(r["proxy_source"] or "")
        c = int(r["c"] or 0)
        organ = src.split(".", 1)[0] if "." in src else src
        slot = organs.setdefault(organ, {
            "organ": organ, "label": organ_label(organ),
            "success": 0, "fail": 0, "other": 0, "events": {},
        })
        low = src.lower()
        if low.endswith(_SYNAPSE_SUCCESS_SUFFIX):
            slot["success"] += c
        elif low.endswith(_SYNAPSE_FAIL_SUFFIX):
            slot["fail"] += c
        else:
            slot["other"] += c
        slot["events"][src] = slot["events"].get(src, 0) + c
    out = []
    for o in organs.values():
        judged = o["success"] + o["fail"]
        o["success_rate"] = round(o["success"] / judged, 3) if judged else None
        out.append(o)
    out.sort(key=lambda x: (-(x["success"] + x["fail"]), x["organ"]))
    return {"status": "ok", "organs": out}


def _pick_weakest(agent_block: dict) -> list:
    """从 agent 块挑弱器官：success_rate < 0.6 且样本 >= 5，meta（闸门事件）除外。
    纯函数零 IO。没有弱器官如实返回 []——空列表就是"没有短板"的事实。"""
    if agent_block.get("status") != "ok":
        return []
    weak = []
    for o in agent_block.get("organs") or []:
        if o.get("organ") == "meta":
            continue
        rate = o.get("success_rate")
        if rate is None:
            continue
        if o["total"] >= _WEAK_MIN_SAMPLES and rate < _WEAK_THRESHOLD:
            weak.append({
                "organ": o["organ"], "label": o["label"],
                "success_rate": rate, "total": o["total"], "ok": o["ok"],
                "executed": o["executed"],
                "top_failure": o.get("top_failure"),
            })
    weak.sort(key=lambda x: x["success_rate"])
    return weak


def _pick_slowest(agent_block: dict) -> list:
    """拖油瓶榜：真正执行过的调用里平均耗时 Top N（毫秒）。纯函数零 IO。"""
    if agent_block.get("status") != "ok":
        return []
    rows = [t for t in agent_block.get("tools") or []
            if t.get("avg_ms") is not None and t.get("executed")]
    rows.sort(key=lambda x: -x["avg_ms"])
    return [{"tool": t["tool"], "organ": t["organ"], "avg_ms": t["avg_ms"],
             "ms_max": t["ms_max"], "executed": t["executed"]}
            for t in rows[:_SLOWEST_TOP]]


def _build_insights(agent_block: dict, syn_block: dict, weakest: list,
                    slowest: list) -> list:
    """模板化事实短句（叙事/自驱 prompt 里直接引用；纯数字事实，无建议）。"""
    lines: list = []
    if agent_block.get("status") == "ok" and agent_block.get("total"):
        total, ok = agent_block["total"], agent_block["ok"]
        lines.append("近%d天工具调用 %d 次，成功 %d 次（原始成功率 %.0f%%）"
                     % (_WINDOW_DAYS, total, ok, ok * 100.0 / total))
        causes = agent_block.get("failure_causes") or {}
        if causes:
            parts = ["%s %d 次" % (_CAUSE_LABELS.get(k, k), v)
                     for k, v in sorted(causes.items(), key=lambda kv: -kv[1])]
            lines.append("失败归因：" + "、".join(parts))
    for w in weakest[:3]:
        cause = (w.get("top_failure") or {}).get("label") or "未知"
        lines.append("弱器官：%s 成功率 %.0f%%（样本 %d，主因 %s）"
                     % (w["label"], w["success_rate"] * 100.0,
                        w["total"], cause))
    for s in slowest[:2]:
        lines.append("耗时最高：%s 平均 %.0fms（%d 次）"
                     % (s["tool"], s["avg_ms"], s["executed"]))
    if syn_block.get("status") == "ok":
        for o in (syn_block.get("organs") or [])[:3]:
            judged = (o["success"] or 0) + (o["fail"] or 0)
            if not judged:
                continue
            lines.append("任务级（%s）：判过 %d 件事，成 %d / 败 %d"
                         % (o["label"], judged, o["success"], o["fail"]))
    if not lines:
        lines.append("近%d天没有可统计的工具调用记录" % _WINDOW_DAYS)
    return lines


def capability_snapshot(window_days: int = _WINDOW_DAYS, force: bool = False) -> dict:
    """能力图谱主入口：三块聚合 + 弱器官 + 拖油瓶 + 事实短句，60s TTL 缓存。

    force=True 跳过缓存（验收时刚制造完调用要看即时数字时用）。
    任何一块失败照常返回其余块（fail-open，块级 status=no_data 带原因）。"""
    global _cache_mono, _cache_data
    now = time.monotonic()
    if (not force and _cache_data and now - _cache_mono < _CACHE_TTL_SEC):
        return _cache_data
    agent_block = _read_agent_tool_block(window_days)
    syn_block = _read_synapse_block(window_days)
    weakest = _pick_weakest(agent_block)
    slowest = _pick_slowest(agent_block)
    snap = {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "window_days": int(window_days or _WINDOW_DAYS),
        "threshold": _WEAK_THRESHOLD,
        "min_samples": _WEAK_MIN_SAMPLES,
        "agent_tools": agent_block,
        "synapse_organs": syn_block,
        "weakest": weakest,
        "slowest_tools": slowest,
        "insights": _build_insights(agent_block, syn_block, weakest, slowest),
    }
    _cache_mono, _cache_data = now, snap
    logger.info("[capability] 快照就绪：tools=%s organs=%s weakest=%d",
                len(agent_block.get("tools") or []) if agent_block.get("status") == "ok" else -1,
                len(agent_block.get("organs") or []) if agent_block.get("status") == "ok" else -1,
                len(weakest))
    return snap


def radar_text(max_chars: int = 1200, force: bool = False) -> str:
    """能力雷达 → 紧凑中文事实文本（叙事/反思/自驱 prompt 注入用）。

    只放事实数字，不含路径/参数/密钥；钳 max_chars。读不到如实写
    "能力雷达读取失败（原因）"，绝不编数字。fail-open 绝不抛。"""
    try:
        snap = capability_snapshot(force=force)
        lines = ["[能力雷达（近%d天真实工具调用统计）]" % snap["window_days"]]
        lines.extend("- %s" % s for s in snap.get("insights") or [])
        return "\n".join(lines)[:max(200, int(max_chars or 1200))]
    except Exception as e:  # noqa: BLE001 - fail-open：雷达盲了不拦消费方
        logger.warning("[capability] radar_text 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return "能力雷达读取失败（%s: %s）" % (type(e).__name__, str(e)[:80])


def weak_organs(threshold: float = _WEAK_THRESHOLD,
                min_samples: int = _WEAK_MIN_SAMPLES) -> list:
    """弱器官清单（self_driven 目标生成用）：success_rate < threshold 且
    total >= min_samples 的 organ（meta 除外）。异常返回 []（fail-open）。"""
    try:
        snap = capability_snapshot()
        if snap.get("agent_tools", {}).get("status") != "ok":
            return []
        out = []
        for o in snap["agent_tools"].get("organs") or []:
            if o.get("organ") == "meta" or o.get("success_rate") is None:
                continue
            if o["total"] >= int(min_samples) and o["success_rate"] < float(threshold):
                out.append({"organ": o["organ"], "label": o["label"],
                            "success_rate": o["success_rate"],
                            "total": o["total"],
                            "top_failure": o.get("top_failure")})
        out.sort(key=lambda x: x["success_rate"])
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[capability] weak_organs 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return []


def organ_recent_outcomes(limit: int = 200) -> dict:
    """各器官最近执行结果序列 + 尾部连续失败数（[第二战 2026-11-07] self_fix_mode 触发源）。

    只统计**真正执行过**的调用（ok/error/timeout——被拦/被拒/待审批不进序列，
    闸门噪声不能当能力连败的证据），按 id 倒序取最近 limit 条再分组。
    返回 {organ: {"recent": ["ok","error",…]（旧→新）, "streak": 尾部连续失败数}}；
    读库失败返回 {}（fail-open，调用方按"无可自愈项"处理）。绝不抛。"""
    try:
        from . import crud
        conn = crud._conn()
        try:
            rows = conn.execute(
                "SELECT tool, status FROM agent_tool_log "
                "WHERE status IN ('ok', 'error', 'timeout') "
                "ORDER BY id DESC LIMIT ?", (max(10, int(limit or 200)),)).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[capability] 连败序列读取失败：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {}
    seqs: dict = {}
    for r in reversed(rows):  # 库里新→旧，反转成旧→新
        organ = organ_of(str(r["tool"] or ""))
        seqs.setdefault(organ, []).append(str(r["status"] or ""))
    out: dict = {}
    for organ, seq in seqs.items():
        streak = 0
        for s in reversed(seq):
            if s == "ok":
                break
            streak += 1
        out[organ] = {"recent": seq, "streak": streak}
    return out


router = APIRouter()


@router.get("/self_model/capabilities")
def self_model_capabilities(window_days: int = _WINDOW_DAYS, refresh: bool = False):
    """GET /api/self_model/capabilities?window_days=7&refresh=1 —— 能力图谱 API（前端拉取）。

    只读聚合 + TTL 缓存（60s）；window_days 允许 1~90；refresh=1 跳过缓存
    （验收刚制造完调用要看即时数字时用）。块级失败如实带 no_data 原因，
    绝不 500（仪表盘宁缺勿炸，self_routes 同款）。"""
    try:
        wd = min(max(int(window_days or _WINDOW_DAYS), 1), 90)
    except (ValueError, TypeError):
        wd = _WINDOW_DAYS
    snap = capability_snapshot(window_days=wd, force=(bool(refresh) or wd != _WINDOW_DAYS))
    snap.pop("ok", None)
    return {"ok": True, **snap}
