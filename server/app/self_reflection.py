# -*- coding: utf-8 -*-
"""自我观察 self_reflection（2026-10-09 桥二 B2；2026-10-14 丙改法=窗口增量版）：
自省循环第二步——把"自上次观察以来"的窗口增量（memory_bank 计数变化 + 反思触发
数）写成 kind='self_observation' 记忆行。

级别边界 1-2（记录 + 标记，与桥一 self_check、桥三 self_model 同口径，勿扩）：
- 纯模板拼数字、不调 LLM；内容只放事实，永不出现"应该/建议/需要"；
- 对 memory_bank 只 SELECT 汇总、对 reflection 只 getattr 读、零写入（v1.5
  budget_gate / 桥二 B2 同款探测纪律）；
- fail-open：读不到 / 查重失败 / 写库失败一律 ran=False + reason，绝不把异常
  抛给 self_check_loop；
- 双重防重复行：1 小时冷却（硬编）+ 落库前内容级查重。[丙改法 2026-10-14]
  原"五键快照全等 → no_change"闸已删：它拦下的正是计数器静止期，而静默期
  正是素材缺口的根；窗口内容必带 [窗口 时间戳] → 逐行唯一，双查重天然通过，
  静默期窗口也如实落行。

与 B1 的分工：本模块写出的行是"给自己的日记"，不是"给用户捞的记忆"——
召回白名单已治本（memory_routes.py [白名单治本 2026-10-10]）：
kind IN ('fact','task_result','insight','preference')，
'self_observation' 结构上不进召回面，用户侧天然隔离。
"""
import datetime
import logging
import time

from . import memory_db

logger = logging.getLogger("moray.self_reflection")

#: 冷却（秒）：任意两条 self_observation 的最小间隔，硬编不进 config/env
_SELF_REFLECT_COOLDOWN_SEC = 3600
_OBS_KIND = "self_observation"
_OBS_SOURCE = "self_reflection"

#: 最近成功写入时刻（time.monotonic 基准，进程重启即清零——"本次运行周期"
#: 语义与 reflection 算力闸同款：读的是本进程状态，重启归零属如实）
_last_self_reflect_monotonic: float = 0.0
#: 上次成功写入时的窗口快照（[丙改法] 只作下次增量的基线，不再参与拦闸）
_last_snapshot: dict = {}

#: 读取映射：本模块键名 → reflection 模块级变量实名（A 阶段影子计数器 + 日计数）
_COUNTER_ATTRS = (
    ("daily_count", "_reflect_daily_count"),
    ("throttled_daily_cap", "_reflect_throttled_daily_cap"),
    ("throttled_global", "_reflect_throttled_global"),
    ("throttled_trace", "_reflect_throttled_trace"),
)

#: [丙改法 2026-10-14] 窗口快照五键 = memory 三计数 + 反思触发 + 上次观察时刻
#: （epoch 秒；进程重启清零 → 下轮走基线行，不编 delta）
_SNAPSHOT_KEYS = ("task_result_count", "insight_count", "reflection_triggers",
                  "total_count", "last_observed_at")


def _read_reflection_counters() -> dict:
    """从 reflection 模块级状态读五项计数。getattr 逐个探测，缺任一即
    {"status": "no_data", "reason": ...}；绝不写 reflection，绝不抛异常。"""
    try:
        from . import reflection
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败也是 no_data
        logger.warning("[self_reflection] import reflection 异常：%s: %s",
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
    last_trace = getattr(reflection, "_reflect_last_trace", _absent)
    if last_trace is _absent:
        missing.append("_reflect_last_trace")
    elif not isinstance(last_trace, dict):
        return {"status": "no_data", "reason": "_reflect_last_trace is not a dict"}
    if missing:
        return {"status": "no_data",
                "reason": "reflection missing: %s" % ", ".join(missing)}
    out = dict(vals)
    out["active_traces"] = len(last_trace)
    out["status"] = "ok"
    return out


def _read_memory_counts() -> dict:
    """[丙改法] 从 memory.sqlite3 数窗口素材（只读 SELECT，不碰任何行）：
    task_result / insight 条数 + 全库总行数（GROUP BY 后求和，与
    memory_routes.memory_stats 同构）。成功返回
    {"task_result_count": N, "insight_count": M, "total_count": T, "status": "ok"}；
    读不到 → {"status": "no_data", "reason": ...}，不抛异常。"""
    try:
        conn = memory_db.connect()
        try:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM memory_bank GROUP BY kind").fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败也是 no_data
        logger.warning("[self_reflection] 读 memory_bank 计数异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "read memory_bank failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    counts = {row[0]: row[1] for row in rows}
    return {"status": "ok",
            "task_result_count": counts.get("task_result", 0),
            "insight_count": counts.get("insight", 0),
            "total_count": sum(counts.values())}


def _build_content(current: dict, snapshot: dict) -> str:
    """[丙改法 2026-10-14] 窗口增量模板——内容必带 [窗口 时间戳] 保证逐行唯一
    （dup / insert P1 双查重天然通过），因此不再需要 no_change 拦闸。
    - 空快照（进程重启后首次）→ 基线行：只写绝对值，不编 delta、不写时长；
    - 反思日计数日切/重启会使 delta 为负 → 取重置后的当日值（重置前尾数
      不可考，如实取可考部分）；计数为负（forget 删行）是真实净变化，如实写。
    纯数字事实、无建议无归因；时长 = now - 上次观察时刻（epoch 秒，挂钟）。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    if not snapshot.get("last_observed_at"):
        return ("[窗口 %s] 基线（本进程首次观察）：当前 task_result %d 条、"
                "insight %d 条、库内总计 %d 条") % (
            ts, current["task_result_count"], current["insight_count"],
            current["total_count"])
    dur = max(0, int(time.time() - snapshot["last_observed_at"]))
    n1 = current["task_result_count"] - snapshot["task_result_count"]
    n2 = current["insight_count"] - snapshot["insight_count"]
    n3 = current["reflection_triggers"] - snapshot["reflection_triggers"]
    if n3 < 0:
        n3 = current["reflection_triggers"]  # 日计数归零/重置：取当日已触发数
    return ("[窗口 %s] 自上次观察 %dh%dm：净增 task_result %d 条、insight %d 条、"
            "反思触发 %d 次；当前库内总计 %d 条") % (
        ts, dur // 3600, (dur % 3600) // 60, n1, n2, n3,
        current["total_count"])


def self_reflection_maybe() -> dict:
    """主入口（self_check_loop 每轮尾调用）。返回
    {"ran": bool, "reason": str, "memory_id": str|None, "content": str|None}。

    顺序即守卫：冷却 → 读数(no_data ×2) → 库内查重(dup) → 写库。
    [丙改法 2026-10-14] no_change（五键全等）闸已删——窗口内容必带时间戳
    必唯一，1h 冷却仍是唯一节流，静默期窗口也如实落行（素材缺口的根闸）。
    只有写库成功才更新冷却时刻与窗口快照（失败不消耗冷却，下轮重试）。
    本函数吞掉一切异常转 reason——调用方的循环绝不因它中断。"""
    global _last_self_reflect_monotonic, _last_snapshot
    try:
        now = time.monotonic()
        if now - _last_self_reflect_monotonic < _SELF_REFLECT_COOLDOWN_SEC:
            return {"ran": False, "reason": "cooldown",
                    "memory_id": None, "content": None}
        mem = _read_memory_counts()
        if mem.get("status") != "ok":
            return {"ran": False, "reason": "no_data: %s" % mem.get("reason"),
                    "memory_id": None, "content": None}
        counters = _read_reflection_counters()
        if counters.get("status") != "ok":
            return {"ran": False, "reason": "no_data: %s" % counters.get("reason"),
                    "memory_id": None, "content": None}
        current = {
            "task_result_count": mem["task_result_count"],
            "insight_count": mem["insight_count"],
            "total_count": mem["total_count"],
            "reflection_triggers": counters["daily_count"],
        }
        content = _build_content(current, _last_snapshot)
        conn = memory_db.connect()
        try:
            hit = conn.execute(
                "SELECT content FROM memory_bank WHERE kind = ? AND content = ?",
                (_OBS_KIND, content)).fetchone()
        finally:
            conn.close()
        if hit is not None:
            return {"ran": False, "reason": "dup",
                    "memory_id": None, "content": None}
        from .memory_routes import insert_memory
        mem_id, err = insert_memory(
            content,
            kind=_OBS_KIND,
            tags=[_OBS_SOURCE],
            source=_OBS_SOURCE,
        )
        if err or not mem_id:
            logger.warning("[self_reflection] 写库失败：%s", err or "empty id")
            return {"ran": False, "reason": "insert_failed",
                    "memory_id": None, "content": content}
        _last_self_reflect_monotonic = now
        current["last_observed_at"] = time.time()
        _last_snapshot = {k: current[k] for k in _SNAPSHOT_KEYS}
        logger.warning("[self_reflection] 写入 self_observation：%s", mem_id)
        return {"ran": True, "reason": "ok", "memory_id": mem_id, "content": content}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_reflection] 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False, "reason": "exception: %s: %s"
                % (type(e).__name__, str(e)[:120]),
                "memory_id": None, "content": None}
