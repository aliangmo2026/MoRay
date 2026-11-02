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

[真实感知 2026-11-01] 窗口事实之外再拼两段**外部真实状态**：self_check 最近一次
报告（level / 下线端口 / 窗口内 ERROR 数）与 self_state 实时流水（mental_score /
连续失败次数）。此前自我观察只数"我说了多少话"，看不到"我做砸了什么"；接上这
两路之后，痛觉才进得了自省原料池。取不到就写"读取失败"并带原因码，绝不补 0/100。

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


def _read_self_check_facts() -> dict:
    """[真实感知 2026-11-01] 读 self_check 最近一次报告，只提取三件事实：
    level / 下线端口 / 窗口内 ERROR 数。

    只读——不触发新一轮探活、不写任何模块状态（报告本身是 30 分钟粒度的
    缓存快照，口径与自检循环一致，这里不另立探活）。任何环节缺失都
    status=unavailable + 具体原因，让调用方如实写"读取失败"，不编全绿。"""
    try:
        from . import self_check
        report = self_check.get_last_report()
    except Exception as e:  # noqa: BLE001 - fail-open：取不到是事实，绝不抛
        logger.warning("[self_reflection] 读 self_check 报告异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "unavailable",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}
    if not isinstance(report, dict):
        return {"status": "unavailable", "reason": "首轮自检尚未完成"}
    ports = report.get("ports")
    errors = report.get("errors")
    if not isinstance(ports, dict) or not isinstance(errors, dict):
        return {"status": "unavailable", "reason": "报告缺 ports/errors 字段"}
    err_count = errors.get("recent_30min")
    if isinstance(err_count, bool) or not isinstance(err_count, int):
        return {"status": "unavailable", "reason": "errors.recent_30min 非整数"}
    down = [str(p) for p, v in ports.items()
            if isinstance(v, dict) and v.get("up") is False]
    return {"status": "ok",
            "level": str(report.get("level") or "unknown"),
            "ts": str(report.get("ts") or ""),
            "ports_total": len(ports),
            "down_ports": down,
            "errors_30min": err_count}


def _read_state_facts() -> dict:
    """[真实感知 2026-11-01] 读实时状态机最新一行（memory.sqlite3 的 self_state_log）。

    id 为 None = 该表还没有任何行（进程内尚无成败事件）→ 如实 unavailable。
    刻意不把 get_current_state() 的基线默认值 100/0 当成"我很健康"：那正是
    "自欺"的另一种写法。字段非整数同样如实报失败。"""
    try:
        from . import self_state
        cur = self_state.get_current_state()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[self_reflection] 读 self_state 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "unavailable",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:80])}
    if not isinstance(cur, dict):
        return {"status": "unavailable", "reason": "get_current_state 返回非 dict"}
    if cur.get("id") is None:
        return {"status": "unavailable", "reason": "状态流水为空"}
    ms = cur.get("mental_score")
    cf = cur.get("consecutive_failures")
    if (isinstance(ms, bool) or not isinstance(ms, int)
            or isinstance(cf, bool) or not isinstance(cf, int)):
        return {"status": "unavailable", "reason": "mental_score 非整数"}
    return {"status": "ok",
            "mental_score": ms,
            "consecutive_failures": cf,
            "engine": str(cur.get("engine") or ""),
            "event": str(cur.get("event") or "")}


def _render_perception(self_check_facts: dict | None, state_facts: dict | None) -> str:
    """真实感知两段（自检快照 + 实时状态流水），只放事实，不出现建议/归因。

    任一段读不到 → 明写"读取失败"并附原因；绝不用 0 / 100 冒充健康。两段都
    拿不到时仍输出两行失败告知——"镜子照不到自己"本身就是要被记录的事实。"""
    parts = []
    sc = self_check_facts if isinstance(self_check_facts, dict) else {}
    if sc.get("status") == "ok":
        down = sc.get("down_ports") or []
        down_txt = "无" if not down else "、".join(str(p) for p in down)
        parts.append(
            "近 30 分钟自检 level=%s（报告时刻 %s）：%d 个端口下线（%s）；窗口内 ERROR %d 条"
            % (sc.get("level") or "unknown", sc.get("ts") or "未知",
               len(down), down_txt, sc.get("errors_30min")))
    else:
        parts.append("自检快照读取失败（%s）" % (sc.get("reason") or "未取到报告"))

    st = state_facts if isinstance(state_facts, dict) else {}
    if st.get("status") == "ok":
        parts.append(
            "实时状态：mental_score=%d，连续失败 %d 次（最近事件 %s/%s）"
            % (st.get("mental_score"), st.get("consecutive_failures"),
               st.get("engine") or "未知", st.get("event") or "未知"))
    else:
        parts.append("实时状态读取失败（%s）" % (st.get("reason") or "未取到状态流水"))
    return "；" + "；".join(parts) + "。"


def _build_content(current: dict, snapshot: dict,
                   self_check_facts: dict | None = None,
                   state_facts: dict | None = None) -> str:
    """[丙改法 2026-10-14] 窗口增量模板——内容必带 [窗口 时间戳] 保证逐行唯一
    （dup / insert P1 双查重天然通过），因此不再需要 no_change 拦闸。
    - 空快照（进程重启后首次）→ 基线行：只写绝对值，不编 delta、不写时长；
    - 反思日计数日切/重启会使 delta 为负 → 取重置后的当日值（重置前尾数
      不可考，如实取可考部分）；计数为负（forget 删行）是真实净变化，如实写。
    纯数字事实、无建议无归因；时长 = now - 上次观察时刻（epoch 秒，挂钟）。
    [真实感知 2026-11-01] 尾部追加 self_check / self_state 两段真实状态。"""
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    if not snapshot.get("last_observed_at"):
        base = ("[窗口 %s] 基线（本进程首次观察）：当前 task_result %d 条、"
                "insight %d 条、库内总计 %d 条") % (
            ts, current["task_result_count"], current["insight_count"],
            current["total_count"])
    else:
        dur = max(0, int(time.time() - snapshot["last_observed_at"]))
        n1 = current["task_result_count"] - snapshot["task_result_count"]
        n2 = current["insight_count"] - snapshot["insight_count"]
        n3 = current["reflection_triggers"] - snapshot["reflection_triggers"]
        if n3 < 0:
            n3 = current["reflection_triggers"]  # 日计数归零/重置：取当日已触发数
        base = ("[窗口 %s] 自上次观察 %dh%dm：净增 task_result %d 条、insight %d 条、"
                "反思触发 %d 次；当前库内总计 %d 条") % (
            ts, dur // 3600, (dur % 3600) // 60, n1, n2, n3,
            current["total_count"])
    return base + _render_perception(self_check_facts, state_facts)


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
        content = _build_content(current, _last_snapshot,
                                 _read_self_check_facts(), _read_state_facts())
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
