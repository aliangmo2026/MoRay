# -*- coding: utf-8 -*-
"""自我模型 self_model（2026-10-10 桥三 v0）：自我仪表盘首落地——把 memory_bank
各 kind 条数 + reflection 影子计数器拼成模板，写 kind='self' 记忆行（source='self_model'）。

级别边界 1-2（记录 + 标记，与桥一 self_check、桥二 self_reflection 同口径，勿扩）：
- 纯模板拼数字、不调 LLM；内容只放事实，永不出现"应该/建议/需要"；
- 对 memory_bank 只 SELECT 汇总、只维护自己那一条 kind='self' 行——绝不改任何其他 kind 的行；
- 对 reflection 只 getattr 读、零写入（v1.5 budget_gate / 桥二 B2 同款探测纪律）；
- fail-open：读不到 / 查重失败 / 写库失败一律 ran=False + reason，绝不把异常
  抛给 self_check_loop；
- 三重防重复行：24 小时冷却（硬编）+ 快照 no_change 判定 + 最近一条内容比对。

[单行语义 2026-10-31] 库里只保留最近一条 kind='self'：内容变化时就地更新这一条，
不再 insert 新行堆日记（修复前 kind='self' 已积压 70 条几乎重复的日记）。
为什么不能只写 `UPDATE memory_bank SET content=?`：实测真实库 sqlite_master 里
**trigger 总数 0**（memory_bank / memory_fts 上一个触发器都没有），memory_fts 全靠
应用层手工双写（memory_routes.insert_memory 的 INSERT 双写、memory_forget 的三表
同步删是同款先例）——裸改正文会让全文索引继续指向旧文本。故更新走单事务三处同步：
memory_bank 正文/tags/updated_at + memory_fts 同事务 UPDATE（sqlite 3.43.1 的 fts5
支持 UPDATE，trigram 倒排随之重建，已实测旧词不命中、新词命中）+ memory_vec 行删除
（向量按旧正文算，必须作废）+ embedding 清空交给后台重嵌。任何一处失败整体回滚，
旧行原样保留，绝不出现"正文改了索引没改"的半截状态。

B1 隔离面声明：kind='self' 行与 self_observation 同款是"给自己的日记"，不进
用户侧召回面。召回白名单已治本（memory_routes.py [白名单治本 2026-10-10]）：
`kind IN ('fact','task_result','insight','preference')`，'self' 结构上不进召回面。
"""
import logging
import time
from datetime import datetime

from . import memory_db

logger = logging.getLogger("moray.self_model")


def _ts() -> str:
    """与 memory_routes._ts 同款时间戳（库内 created_at/updated_at 一律此形态）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

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


def _read_verdict_stats() -> dict:
    """[v5 2026-10-31] 读自驱判据台账（self_driven.verdict_stats，7 天窗口）。

    为什么自我模型要读它：在此之前"自我模型"只数消息条数——它知道自己说了
    多少话，不知道**自己哪句话被现实判过真假**。台帐是唯一的外部判据来源，
    不接这一路，自我模型就永远学不到"我上周的预测命中率是多少"。
    fail-open：读不到写"未读到"，绝不编 0（编 0 会被误当成"我很准"或"我很差"）。
    """
    try:
        from . import self_driven
        st = self_driven.verdict_stats(7)
        if not isinstance(st, dict):
            return {"status": "no_data", "reason": "stats not dict"}
        return {"status": "ok", "judged": int(st.get("judged") or 0),
                "confirmed": int(st.get("confirmed") or 0),
                "falsified": int(st.get("falsified") or 0),
                "unverifiable": int(st.get("unverifiable") or 0),
                "accuracy": st.get("accuracy"),
                "active_days": int(st.get("active_days") or 0),
                "streak": int(st.get("streak") or 0)}
    except Exception as e:  # noqa: BLE001 - 台账读不到不影响自我模型主路径
        logger.warning("[self_model] 读判据台账异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _build_self_content(counts: dict, counters: dict,
                        verdicts: dict | None = None) -> str:
    """模板化中文、纯数字事实——记录级（1 级）内容，无建议无归因，不写日期
    （created_at 字段已带时间）。计数读不到写"未读到"，不编 0。
    counts 已过 no_data 闸（status=ok），GROUP BY 缺键即该 kind 真 0 条，如实写 0。"""
    if counters.get("status") == "ok":
        daily = "%s/%s" % (counters["daily_count"], counters["daily_cap"])
        g_glob = str(counters["throttled_global"])
        g_trace = str(counters["throttled_trace"])
    else:
        daily = g_glob = g_trace = "未读到"
    base = ("自我模型（截至本日）：我累计处理 %s 个任务，产出 %s 条洞察，"
            "%s 条自省观察。反思日计数 %s，全局冷却拦截累计 %s 次，"
            "trace 冷却拦截累计 %s 次。") % (
        counts.get("task_result", 0), counts.get("insight", 0),
        counts.get("self_observation", 0), daily, g_glob, g_trace)
    if not verdicts:
        return base
    if verdicts.get("status") != "ok":
        return base + "预测对账：未读到。"
    acc = verdicts.get("accuracy")
    acc_txt = "%s%%" % round(float(acc) * 100) if acc is not None else "样本不足"
    return base + ("预测对账（近7天）：被现实判过 %s 条（成立 %s / 证伪 %s / 命令不可判 %s），"
                   "命中率 %s，有判据 %s 天，连续 %s 天。") % (
        verdicts.get("judged", 0), verdicts.get("confirmed", 0),
        verdicts.get("falsified", 0), verdicts.get("unverifiable", 0),
        acc_txt, verdicts.get("active_days", 0), verdicts.get("streak", 0))


def _read_latest_self_row() -> tuple:
    """取最近一条 kind='self' 行（created_at DESC, rowid DESC 兜同秒并列）。
    返回 (id, content)；一条都没有 → (None, None)。
    读失败不在这里吞：由 self_model_maybe 的 fail-open 兜成 exception reason。
    硬约束 WHERE kind = ?（参数化）——绝不看其他 kind 的行。"""
    conn = memory_db.connect()
    try:
        row = conn.execute(
            "SELECT id, content FROM memory_bank WHERE kind = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (_SELF_KIND,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, None
    return row[0], row[1]


def _update_self_row(mem_id: str, content: str, tags: str) -> str:
    """就地更新一条 kind='self' 行（主表 + FTS + 向量三处同步，单事务）。
    成功返回 ""，失败返回错误串（fail-open：调用方转 reason，异常绝不上抛）。

    为何是三条语句而不是一条：库内零触发器，memory_fts/memory_vec 全由应用层手工
    维护（memory_routes.memory_forget 同款三表同步先例）。主表 UPDATE 带
    `AND kind = ?` 硬闸 + rowcount 校验：id 命中但 kind 不是 'self' 时整体回滚，
    结构上不可能动到别的 kind 的行。内存侧 embedding 一并清空（旧向量作废），
    后台由 spawn_embed 重嵌。"""
    conn = memory_db.connect()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE memory_bank SET content = ?, tags = ?, embedding = NULL, "
            "embedding_model = '', updated_at = ? WHERE id = ? AND kind = ?",
            (content, tags, _ts(), mem_id, _SELF_KIND))
        if (cur.rowcount or 0) == 0:
            conn.rollback()
            return "row vanished or kind mismatch: %s" % mem_id
        conn.execute(
            "UPDATE memory_fts SET content = ?, tags = ? WHERE memory_id = ?",
            (content, tags, mem_id))
        if memory_db.VEC_AVAILABLE:
            conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mem_id,))
        conn.commit()
    except Exception as e:  # noqa: BLE001 - fail-open：更新失败只报，不抛
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "%s: %s" % (type(e).__name__, str(e)[:160])
    finally:
        conn.close()
    return ""


def _spawn_reembed(mem_id: str, content: str) -> None:
    """正文变更后异步补嵌（与 insert_memory 尾部同款；MORAY_EMBED_AUTO=0 时内部自跳过）。"""
    try:
        from . import memory_embed
        memory_embed.spawn_embed(mem_id, content, _SELF_KIND)
    except Exception as e:  # noqa: BLE001 - 嵌入是旁路，绝不阻主流程
        logger.warning("[self_model] spawn_embed 调度异常（mem_id=%s）：%s: %s",
                       mem_id, type(e).__name__, str(e)[:160])


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
        # [v5] 判据台账也进变化指纹：现实给出新判据（成立/证伪数变了）就该触发
        # 自我模型重写——这正是"被现实修正"的最小实现，而不是只数自己说了多少话。
        verdicts = _read_verdict_stats()
        if verdicts.get("status") == "ok":
            current = {**current, "verdict_judged": verdicts.get("judged"),
                       "verdict_confirmed": verdicts.get("confirmed"),
                       "verdict_falsified": verdicts.get("falsified"),
                       "verdict_streak": verdicts.get("streak")}
        if not _has_changed(_last_snapshot, current):
            return {"ran": False, "reason": "no_change",
                    "memory_id": None, "content": None}
        content = _build_self_content(counts, counters, verdicts)
        prev_id, prev_content = _read_latest_self_row()
        if prev_id is not None and prev_content == content:
            # 与最近一条逐字相同 → 不写库（原 dup 语义原样保留：不消耗冷却、不动快照）
            return {"ran": False, "reason": "dup",
                    "memory_id": None, "content": None}
        if prev_id is not None:
            # 有旧行但内容变了 → 就地更新那一条（不新增行），语义 = 单行自我模型
            upd_err = _update_self_row(prev_id, content, _SELF_SOURCE)
            if upd_err:
                logger.warning("[self_model] 更新失败（id=%s）：%s", prev_id, upd_err)
                return {"ran": False, "reason": "update_failed",
                        "memory_id": None, "content": content}
            _spawn_reembed(prev_id, content)
            _last_self_model_monotonic = now
            _last_snapshot = current
            logger.warning("[self_model] 就地更新 self：%s", prev_id)
            return {"ran": True, "reason": "updated", "memory_id": prev_id,
                    "content": content}
        # 一条都没有 → 走唯一写库汇聚点 insert（source×kind 矩阵 + FTS 双写 + 补嵌）
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
        logger.warning("[self_model] 首写 self：%s", mem_id)
        return {"ran": True, "reason": "ok", "memory_id": mem_id,
                "content": content}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_model] 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False, "reason": "exception: %s: %s"
                % (type(e).__name__, str(e)[:120]),
                "memory_id": None, "content": None}
