"""会话/消息/设置/KV 的 SQLite CRUD（里程碑 M3）

- 全部参数化查询（? 占位，禁止字符串拼 SQL）
- 写操作 BEGIN/COMMIT，出错 ROLLBACK
- 会话删除在事务内级联删除其 messages（messages 表无外键，必须手动删）
- 统一返回 {ok:true, data} 或 {ok:false, code, message}
"""
import json
import logging
import sqlite3
import time
from typing import Any, Optional

from . import config, db as _db

_logger = logging.getLogger("moray.crud")

_TS = lambda: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())  # noqa: E731


def _conn() -> sqlite3.Connection:
    conn = _db.connect()
    conn.row_factory = sqlite3.Row
    return conn


# ---------------- conversations ----------------

def list_conversations(limit: Optional[int] = None) -> list[dict]:
    limit = min(max(int(limit or 500), 1), 2000)  # 默认/最大封顶
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conv_id: str) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def upsert_conversation(conv: dict) -> dict:
    """按 id 幂等 upsert（不报错不重复建）"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        now = conv.get("updated_at") or conv.get("created_at") or _TS()
        # [契约修复] 兼容两处字段写法：后端同步层按 snake 发送（user_picked_model），
        # 老调用方/导入数据可能用 camel（userPickedModel）。任一带真值即视为用户手选锁定。
        user_picked = 1 if (conv.get("userPickedModel") or conv.get("user_picked_model")) else 0
        conn.execute(
            """INSERT INTO conversations (id, title, model, system_prompt, user_picked_model, pinned, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, model=excluded.model, system_prompt=excluded.system_prompt,
                 user_picked_model=excluded.user_picked_model, pinned=excluded.pinned, updated_at=excluded.updated_at""",
            (
                conv.get("id", ""),
                conv.get("title", ""),
                conv.get("model", ""),
                conv.get("system_prompt", ""),
                user_picked,
                1 if conv.get("pinned") else 0,
                conv.get("created_at") or now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv.get("id", ""),)).fetchone()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_conversation(conv_id: str, patch: dict) -> Optional[dict]:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            conn.rollback()
            return None
        cur = dict(row)
        fields = ["title", "model", "system_prompt", "user_picked_model", "pinned"]
        sets = []
        vals = []
        for f in fields:
            if f in patch:
                v = patch[f]
                if f in ("user_picked_model", "pinned"):
                    v = 1 if v else 0
                sets.append(f"{f} = ?")
                vals.append(v)
        sets.append("updated_at = ?")
        vals.append(_TS())
        vals.append(conv_id)
        if sets:
            conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_conversation(conv_id: str) -> bool:
    """事务内级联删除会话及其全部 messages"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            conn.rollback()
            return False
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- messages ----------------

def list_messages(conv_id: str) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, id ASC", (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_message(msg: dict) -> dict:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        msg_id = msg.get("id", "")
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, reasoning, attachments, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 conversation_id=excluded.conversation_id, role=excluded.role, content=excluded.content,
                 reasoning=excluded.reasoning, attachments=excluded.attachments, model=excluded.model,
                 created_at=excluded.created_at""",
            (
                msg_id,
                msg.get("conversationId", ""),
                msg.get("role", ""),
                msg.get("content", ""),
                msg.get("reasoning", ""),
                None,
                msg.get("model", ""),
                msg.get("createdAt") or _TS(),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_messages(msgs: list[dict]) -> int:
    """批量 upsert（单事务）"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        n = 0
        for msg in msgs:
            conn.execute(
                """INSERT INTO messages (id, conversation_id, role, content, reasoning, attachments, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     conversation_id=excluded.conversation_id, role=excluded.role, content=excluded.content,
                     reasoning=excluded.reasoning, attachments=excluded.attachments, model=excluded.model,
                     created_at=excluded.created_at""",
                (
                    msg.get("id", ""),
                    msg.get("conversationId", ""),
                    msg.get("role", ""),
                    msg.get("content", ""),
                    msg.get("reasoning", ""),
                    None,
                    msg.get("model", ""),
                    msg.get("createdAt") or _TS(),
                ),
            )
            n += 1
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_message(msg_id: str) -> bool:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- settings / kv ----------------

def get_settings() -> dict:
    conn = _conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def put_settings(items: dict) -> int:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        n = 0
        for k, v in items.items():
            conn.execute(
                """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (str(k), v if isinstance(v, str) else __import__("json").dumps(v, ensure_ascii=False), _TS()),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def kv_get(key: str) -> Optional[str]:
    conn = _conn()
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def kv_put(key: str, value: str) -> None:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _TS()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- agent_tool_log（本机工具审计） ----------------

def append_agent_log(tool: str, args_summary: str, approved: bool, status: str, ms: int = 0, detail: str = "") -> int:
    """写一条本机工具审计（参数化插入，单事务）。返回新记录 id。

    status 取值：ok（执行成功）/ error（执行失败）/ timeout（命令超时）/
    rejected（安全层拒绝：越界/危险后缀/白名单外命令等）/
    needs_approval（副作用工具未带 approved）/ denied（客户端明确拒绝）
    """
    conn = _conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "INSERT INTO agent_tool_log (tool, args_summary, approved, status, ms, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(tool)[:64],
                str(args_summary)[:500],
                1 if approved else 0,
                str(status)[:24],
                int(ms or 0),
                str(detail)[:1000],
            ),
        )
        new_id = int(cur.lastrowid or 0)
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_agent_log(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    """审计日志分页（新→旧）。返回 (rows, total)。
    每行额外包含 snapshot_id 字段（关联 event_snapshots.id，用于重放功能）。"""
    limit = min(max(int(limit or 50), 1), 500)
    offset = max(int(offset or 0), 0)
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM agent_tool_log").fetchone()["c"]
        rows = conn.execute(
            """SELECT l.*, (SELECT MIN(s.id) FROM event_snapshots s WHERE s.source_log_id = l.id) AS snapshot_id
               FROM agent_tool_log l ORDER BY l.id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def clear_agent_log() -> int:
    """清空审计日志（设置页手动清空用，前端带确认）。返回删除条数"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute("DELETE FROM agent_tool_log")
        conn.commit()
        return cur.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- event_snapshots（事件溯源快照，Kernel 阶段1 预埋） ----------------
# 只增不改：本段不触碰既有 5 张表的读写路径；表结构见 db.py SCHEMA 末尾。

def _decode_snapshot_row(row: sqlite3.Row) -> dict:
    """行 → dict，并把 snapshot_json 反序列化为 snapshot 字段（解析失败保留原始字符串）"""
    d = dict(row)
    raw = d.get("snapshot_json")
    if raw is None:
        d["snapshot"] = {}
        return d
    try:
        d["snapshot"] = json.loads(raw)
    except (TypeError, ValueError):
        d["snapshot"] = raw
    return d


def append_event_snapshot(event_type: str, entity_id: str = "", source_log_id: int | None = None, snapshot: dict | None = None) -> int:
    """写入一条事件快照。返回新记录 id。
    snapshot 为 dict，自动 json.dumps(ensure_ascii=False) 序列化。"""
    if isinstance(snapshot, dict):
        payload = snapshot
    elif snapshot is None:
        payload = {}
    else:
        payload = {"value": snapshot}
    text = json.dumps(payload, ensure_ascii=False)
    conn = _conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "INSERT INTO event_snapshots (event_type, entity_id, source_log_id, snapshot_json) VALUES (?, ?, ?, ?)",
            (
                str(event_type or "")[:64],
                str(entity_id or "")[:128],
                int(source_log_id) if source_log_id is not None else None,
                text,
            ),
        )
        new_id = int(cur.lastrowid or 0)
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_event_snapshots(event_type: str | None = None, entity_id: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """按事件类型/实体 ID 分页查询快照。返回 (rows, total)。
    snapshot_json 自动反序列化为 dict（解析失败时保留原始字符串）。"""
    limit = min(max(int(limit or 100), 1), 500)
    offset = max(int(offset or 0), 0)
    where: list[str] = []
    vals: list[Any] = []
    if event_type:
        where.append("event_type = ?")
        vals.append(str(event_type))
    if entity_id:
        where.append("entity_id = ?")
        vals.append(str(entity_id))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = _conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM event_snapshots" + clause, tuple(vals)
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM event_snapshots" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(vals) + (limit, offset),
        ).fetchall()
        return [_decode_snapshot_row(r) for r in rows], int(total)
    finally:
        conn.close()


def get_event_snapshot(snapshot_id: int) -> dict | None:
    """按 id 查单条快照。"""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM event_snapshots WHERE id = ?", (int(snapshot_id),)).fetchone()
        if not row:
            return None
        return _decode_snapshot_row(row)
    finally:
        conn.close()


def clear_event_snapshots(event_type: str | None = None) -> int:
    """清空快照（可按事件类型过滤）。返回删除条数。"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        if event_type:
            cur = conn.execute("DELETE FROM event_snapshots WHERE event_type = ?", (str(event_type),))
        else:
            cur = conn.execute("DELETE FROM event_snapshots")
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- agent_sessions（国庆冲刺 Day3：会话状态机 + 断点恢复） ----------------
# 词表与流转规则见 db.py 的 agent_sessions 建表注释。状态机是「记录者」不是「拦截器」：
# 非法流转只 warning 并返回当前行，绝不抛异常打断工具执行（调用方本也应包在 try 里）。

SESSION_STATUSES = ("pending", "running", "completed", "failed", "interrupted")

# 合法流转表（2.2）：pending → running → completed / failed 为主线；
# 同一会话的多步任务允许 completed / failed / interrupted → running 再入；
# running → running 幂等放行（连续调用都算在执行中）。
_SESSION_TRANSITIONS = {
    "pending": {"running"},
    "running": {"running", "completed", "failed"},
    "completed": {"running"},
    "failed": {"running"},
    "interrupted": {"running"},
}


def get_agent_session(session_id: str) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_sessions WHERE session_id = ?", (str(session_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def latest_event_id_for_session(session_id: str) -> Optional[int]:
    """该会话在 event_snapshots 里的最新事件 id（last_event_id 列的来源，Day2 保证工具调用必有事件）。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT MAX(id) AS e FROM event_snapshots WHERE session_id = ?",
            (str(session_id),)).fetchone()
        return int(row["e"]) if row and row["e"] is not None else None
    finally:
        conn.close()


def transition_agent_session(session_id: str, new_status: str,
                             error_msg: Optional[str] = None,
                             last_event_id: Optional[int] = None) -> Optional[dict]:
    """状态机 upsert：未知会话先建为 pending（独立提交，即使后续流转非法也留下 pending 记录），
    再按 _SESSION_TRANSITIONS 推进到 new_status。

    - error_msg=None → 保留原值；传 ""/文本 → 覆盖（completed/running 用 "" 清空）；
    - last_event_id=None → 保留原值（允许只流转不带事件指针）；
    - 非法流转：warning，不改动状态，返回当前行。
    """
    sid = str(session_id)
    if new_status not in SESSION_STATUSES:
        raise ValueError(f"非法会话状态：{new_status!r}（可用：{', '.join(SESSION_STATUSES)}）")
    conn = _conn()
    try:
        # 第一步：确保行存在（INSERT OR IGNORE 幂等；单独提交，新会话的 pending 记录不因非法流转被回滚吞掉）
        conn.execute("INSERT OR IGNORE INTO agent_sessions (session_id, status) VALUES (?, 'pending')", (sid,))
        conn.commit()
        # 第二步：按规则流转
        conn.execute("BEGIN")
        cur_status = conn.execute(
            "SELECT status FROM agent_sessions WHERE session_id = ?", (sid,)).fetchone()["status"]
        if new_status != cur_status and new_status not in _SESSION_TRANSITIONS.get(cur_status, set()):
            conn.rollback()
            _logger.warning("会话状态非法流转被忽略：%s %s → %s", sid, cur_status, new_status)
            return get_agent_session(sid)
        conn.execute(
            """UPDATE agent_sessions
               SET status = ?, updated_at = datetime('now', 'localtime'),
                   error_msg = CASE WHEN ? IS NULL THEN error_msg ELSE ? END,
                   last_event_id = COALESCE(?, last_event_id)
               WHERE session_id = ?""",
            (new_status, error_msg, str(error_msg or "")[:500], last_event_id, sid),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_agent_session(sid)


def mark_stale_sessions_interrupted() -> int:
    """断点恢复（3.1）：新进程启动时把上个进程留下的 pending/running 一律标 interrupted。
    在 main.py lifespan（init_db 之后）调用。返回被标记的会话数。"""
    conn = _conn()
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            """UPDATE agent_sessions
               SET status = 'interrupted', updated_at = datetime('now', 'localtime'),
                   error_msg = '进程重启：上一进程退出时该会话仍处于 ' || status
               WHERE status IN ('pending', 'running')""")
        conn.commit()
        return int(cur.rowcount or 0)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------- 计数（health 用） ----------------

def counts() -> dict:
    conn = _conn()
    try:
        convs = conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"]
        msgs = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {"conversations": convs, "messages": msgs}
    finally:
        conn.close()
