# -*- coding: utf-8 -*-
"""[自我模型 v4 2026-10-23] 实时状态机地基：self_state_log 流水读写。

- 表在 memory_db.SCHEMA（memory.sqlite3 内 self_state_log，init_memory_db 幂等建表）。
- 流水式快照：每次事件**追加一行**，consecutive_failures / mental_score
  由上一行推算（读-改-写单事务，BEGIN IMMEDIATE 立即拿写锁防并发丢更新）。
- 推算规则（任务书 2026-10-23）：
    success → consecutive_failures=0，mental_score=min(100, 上行+5)
    fail    → consecutive_failures=上行+1，mental_score=max(0, 上行-10)
  首行（无上行）基线：consecutive_failures=0，mental_score=100。
- SQL 一律字面量内联 + ? 参数绑定（Mimosa 闸：禁拼接/format/f-string 组装 SQL）。
- fail-open：任何读写异常只记 warning 返回 None/默认值，绝不反噬调用方
  （总线 handler / 端点），与 organ_memory.send 同款约定。
"""
import logging

from . import memory_db

logger = logging.getLogger("moray.self_state")

#: note 落库截断（事件正文可能很长，流水只留痕）
_NOTE_MAX = 200
#: engine 名截断（与 memory_routes kind/source 同级防御）
_ENGINE_MAX = 32


def get_current_state() -> dict:
    """最新一条状态记录（dict 形态）；空表/读失败返回基线默认值。

    返回键：id, ts, engine, event, consecutive_failures, mental_score, note。
    空表时 id/ts 为 None、engine/event/note 为 ""（形状恒定，调用方免判键）。
    """
    default = {"id": None, "ts": None, "engine": "", "event": "",
               "consecutive_failures": 0, "mental_score": 100, "note": ""}
    try:
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT id, ts, engine, event, consecutive_failures, mental_score, note "
                "FROM self_state_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 读失败降级为基线默认值
        logger.warning("[self_state] 状态读取失败：%s: %s", type(e).__name__, str(e)[:200])
        return default
    if row is None:
        return default
    return {
        "id": row["id"],
        "ts": row["ts"],
        "engine": row["engine"],
        "event": row["event"],
        "consecutive_failures": int(row["consecutive_failures"] or 0),
        "mental_score": int(row["mental_score"] or 0),
        "note": row["note"] or "",
    }


def bump_state(engine: str, success: bool, note: str = "") -> dict | None:
    """记一次成功/失败：读上一行推算新状态，追加一行，返回新状态 dict。

    失败/异常返回 None（fail-open，调用方不得据返回值分支业务）。
    """
    engine = str(engine or "").strip()[:_ENGINE_MAX]
    event = "success" if success else "failed"
    note = str(note or "").strip()[:_NOTE_MAX]
    try:
        conn = memory_db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT consecutive_failures, mental_score FROM self_state_log "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_cf = int(row["consecutive_failures"] or 0) if row is not None else 0
            prev_ms = int(row["mental_score"] or 0) if row is not None else 100
            if success:
                new_cf, new_ms = 0, min(100, prev_ms + 5)
            else:
                new_cf, new_ms = prev_cf + 1, max(0, prev_ms - 10)
            cur = conn.execute(
                "INSERT INTO self_state_log (engine, event, consecutive_failures, mental_score, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (engine, event, new_cf, new_ms, note))
            new_row = conn.execute(
                "SELECT id, ts, engine, event, consecutive_failures, mental_score, note "
                "FROM self_state_log WHERE id = ?",
                (cur.lastrowid,)).fetchone()
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 状态落库失败绝不影响调用方（总线/端点）
        logger.warning("[self_state] 状态落库失败：engine=%s success=%s err=%s: %s",
                       engine, success, type(e).__name__, str(e)[:200])
        return None
    logger.warning("[self_state] bump: engine=%s event=%s cf=%d ms=%d",
                   engine, event, new_cf, new_ms)
    return {
        "id": new_row["id"],
        "ts": new_row["ts"],
        "engine": new_row["engine"],
        "event": new_row["event"],
        "consecutive_failures": int(new_row["consecutive_failures"] or 0),
        "mental_score": int(new_row["mental_score"] or 0),
        "note": new_row["note"] or "",
    }
