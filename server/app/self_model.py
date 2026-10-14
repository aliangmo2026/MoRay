# -*- coding: utf-8 -*-
"""自我模型 self_model（2026-10-10 桥三 v0）：自我仪表盘首落地——把 memory_bank
各 kind 条数 + reflection 影子计数器拼成模板，写 kind='self' 记忆行（source='self_model'）。

级别边界 1-2（记录 + 标记，与桥一 self_check、桥二 self_reflection 同口径，勿扩）：
- 纯模板拼数字、不调 LLM；内容只放事实，永不出现"应该/建议/需要"；
- 对 memory_bank 只 SELECT 汇总、只追加自己的 kind='self' 行——绝不改任何其他 kind 的行；
- 对 reflection 只 getattr 读、零写入（v1.5 budget_gate / 桥二 B2 同款探测纪律）；
- fail-open：读不到 / 查重失败 / 写库失败一律 ran=False + reason，绝不把异常
  抛给 self_check_loop；
- 三重防重复行：24 小时冷却（硬编）+ 快照 no_change 判定 + 落库前内容级查重。

B1 隔离面声明：kind='self' 行与 self_observation 同款是"给自己的日记"，不进
用户侧召回面。召回白名单已治本（memory_routes.py [白名单治本 2026-10-10]）：
`kind IN ('fact','task_result','insight','preference')`，'self' 结构上不进召回面。
"""
import logging
import time

from . import memory_db

logger = logging.getLogger("moray.self_model")

#: 冷却（秒）：任意两条 kind='self' 的最小间隔，24 小时硬编不进 config/env。
#: 注意 time.monotonic 是本机开机基准：刚重启的机器开机不足 24h 时首写会等
#: 冷却自然到点——与 reflection 算力闸"进程内单调钟"同款如实语义。
_SELF_MODEL_COOLDOWN_SEC = 86400
_SELF_KIND = "self"
_SELF_SOURCE = "self_model"

#: 最近成功写入时刻（time.monotonic 基准，进程重启即清零）
_last_self_model_monotonic: float = 0.0
#: 上次成功写入时的计数器快照；_has_changed 以此拦 no_change
_last_snapshot: dict = {}

#: 读取映射：本模块键名 → reflection 模块级变量实名（与 self_reflection 同源）
_COUNTER_ATTRS = (
    ("daily_count", "_reflect_daily_count"),
    ("daily_cap", "_REFLECT_DAILY_CAP"),
    ("throttled_daily_cap", "_reflect_throttled_daily_cap"),
    ("throttled_global", "_reflect_throttled_global"),
    ("throttled_trace", "_reflect_throttled_trace"),
)


def _read_memory_counts() -> dict:
    """从 memory.sqlite3 数各 kind 条数（只读 SELECT，不碰任何行）。
    成功返回 {"task_result": N, "insight": M, ..., "status": "ok"}；
    读不到 → {"status": "no_data", "reason": ...}，不抛异常。"""
    try:
        conn = memory_db.connect()
        try:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM memory_bank GROUP BY kind").fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败也是 no_data
        logger.warning("[self_model] 读 memory_bank 计数异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "read memory_bank failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    out = {row[0]: row[1] for row in rows}
    out["status"] = "ok"
    return out


def _read_reflection_counters() -> dict:
    """从 reflection 模块级状态 getattr 读五项（四计数 + 日上限）。
    缺任一即 {"status": "no_data", "reason": ...}；绝不写 reflection，绝不抛。"""
    try:
        from . import reflection
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败也是 no_data
        logger.warning("[self_model] import reflection 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "import reflection failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    _absent = object()
    vals: dict = {}
    missing: list = []
    for key, attr in _COUNTER_ATTRS:
        v = getattr(reflection, attr, _absent)
        if v is _absent:
            missing.append(attr)
        else:
            vals[key] = v
    if missing:
        return {"status": "no_data",
                "reason": "reflection missing: %s" % ", ".join(missing)}
    vals["status"] = "ok"
    return vals


def _has_changed(snapshot: dict, current: dict) -> bool:
    """两次快照任一值不等 → True；全等 → False（防重复写）。
    首轮 _last_snapshot={}，键集必然不等——每周期首条必过本关（再靠冷却/查重兜底）。"""
    for k in set(snapshot) | set(current):
        if snapshot.get(k) != current.get(k):
            return True
    return False


def _build_self_content(counts: dict, counters: dict) -> str:
    """模板化中文、纯数字事实——记录级（1 级）内容，无建议无归因，不写日期
    （created_at 字段已带时间）。计数读不到写"未读到"，不编 0。
    counts 已过 no_data 闸（status=ok），GROUP BY 缺键即该 kind 真 0 条，如实写 0。"""
    if counters.get("status") == "ok":
        daily = "%s/%s" % (counters["daily_count"], counters["daily_cap"])
        g_glob = str(counters["throttled_global"])
        g_trace = str(counters["throttled_trace"])
    else:
        daily = g_glob = g_trace = "未读到"
    return ("自我模型（截至本日）：我累计处理 %s 个任务，产出 %s 条洞察，"
            "%s 条自省观察。反思日计数 %s，全局冷却拦截累计 %s 次，"
            "trace 冷却拦截累计 %s 次。") % (
        counts.get("task_result", 0), counts.get("insight", 0),
        counts.get("self_observation", 0), daily, g_glob, g_trace)


def self_model_maybe() -> dict:
    """主入口（self_check_loop 起手轮 self_reflection_maybe() 之后调用）。返回
    {"ran": bool, "reason": str, "memory_id": str|None, "content": str|None}。

    顺序即守卫：冷却 → 读数(no_data) → 快照(no_change) → 库内查重(dup) → 写库。
    只有写库成功才更新冷却时刻与快照（失败不消耗冷却，下轮重试）。
    本函数吞掉一切异常转 reason——调用方的循环绝不因它中断。"""
    global _last_self_model_monotonic, _last_snapshot
    try:
        now = time.monotonic()
        if now - _last_self_model_monotonic < _SELF_MODEL_COOLDOWN_SEC:
            return {"ran": False, "reason": "cooldown",
                    "memory_id": None, "content": None}
        counts = _read_memory_counts()
        counters = _read_reflection_counters()
        if counts.get("status") == "no_data":
            return {"ran": False, "reason": "no_data: %s" % counts.get("reason"),
                    "memory_id": None, "content": None}
        current = {**counts, **counters}
        if not _has_changed(_last_snapshot, current):
            return {"ran": False, "reason": "no_change",
                    "memory_id": None, "content": None}
        content = _build_self_content(counts, counters)
        conn = memory_db.connect()
        try:
            hit = conn.execute(
                "SELECT content FROM memory_bank WHERE kind = ? AND content = ?",
                (_SELF_KIND, content)).fetchone()
        finally:
            conn.close()
        if hit is not None:
            return {"ran": False, "reason": "dup",
                    "memory_id": None, "content": None}
        from .memory_routes import insert_memory
        mem_id, err = insert_memory(
            content,
            kind=_SELF_KIND,
            source=_SELF_SOURCE,
            tags=[_SELF_SOURCE],
        )
        if err or not mem_id:
            logger.warning("[self_model] 写库失败：%s", err or "empty id")
            return {"ran": False, "reason": "insert_failed",
                    "memory_id": None, "content": content}
        _last_self_model_monotonic = now
        _last_snapshot = current
        logger.warning("[self_model] 写入 self：%s", mem_id)
        return {"ran": True, "reason": "ok", "memory_id": mem_id, "content": content}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_model] 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False, "reason": "exception: %s: %s"
                % (type(e).__name__, str(e)[:120]),
                "memory_id": None, "content": None}
