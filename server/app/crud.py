"""会话/消息/设置/KV 的 SQLite CRUD（里程碑 M3）

- 全部参数化查询（? 占位，禁止字符串拼 SQL）
- 写操作 BEGIN/COMMIT，出错 ROLLBACK
- 会话删除在事务内级联删除其 messages（messages 表无外键，必须手动删）
- 统一返回 {ok:true, data} 或 {ok:false, code, message}
"""
import sqlite3
import time
from typing import Any, Optional

from . import config, db as _db

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


# ---------------- 计数（health 用） ----------------

def counts() -> dict:
    conn = _conn()
    try:
        convs = conn.execute("SELECT COUNT(*) AS c FROM conversations").fetchone()["c"]
        msgs = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {"conversations": convs, "messages": msgs}
    finally:
        conn.close()
