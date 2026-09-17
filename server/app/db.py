"""SQLite 连接与建表（标准库 sqlite3，里程碑 M1）

本步只建空表骨架（conversations / messages / settings / kv），供后续里程碑使用；
导出 ping() 供健康检查验证数据库可正常读写。
"""
import sqlite3
from . import config

# 四张空表骨架：主键 + 时间戳齐全，字段按后续业务预留（本步不做业务读写）
SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL DEFAULT '',
  model         TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL DEFAULT '',
  user_picked_model INTEGER NOT NULL DEFAULT 0,
  pinned        INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL DEFAULT '',
  reasoning     TEXT NOT NULL DEFAULT '',
  attachments   TEXT,
  model         TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
  key         TEXT PRIMARY KEY,
  value       TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kv (
  key         TEXT PRIMARY KEY,
  value       TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_tool_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  tool         TEXT NOT NULL,
  args_summary TEXT NOT NULL DEFAULT '',
  approved     INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT '',
  ms           INTEGER NOT NULL DEFAULT 0,
  detail       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_log_ts ON agent_tool_log(id DESC);

-- [Kernel 阶段1 预埋] 事件溯源快照：记录关键事件发生时的状态，供未来「回放」使用。
-- 当前阶段只建表 + 提供读写接口，不自动写入（写入钩子见 agent_tools._audit_snapshot）。
CREATE TABLE IF NOT EXISTS event_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type    TEXT NOT NULL,           -- 事件类型：tool_call / conversation_created / message_sent / model_changed / settings_changed 等
  entity_id     TEXT NOT NULL DEFAULT '', -- 关联实体 ID（如 tool 调用关联 agent_tool_log.id，会话关联 conversations.id）
  source_log_id INTEGER,                  -- 关联 agent_tool_log.id（如果是工具调用事件）
  snapshot_json TEXT NOT NULL DEFAULT '{}', -- 事件发生时的状态快照（JSON 字符串）
  created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  FOREIGN KEY (source_log_id) REFERENCES agent_tool_log(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_type ON event_snapshots(event_type);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_entity ON event_snapshots(entity_id);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_created ON event_snapshots(created_at);
"""


def connect() -> sqlite3.Connection:
    """建立连接（自动创建数据目录；MORAY_DB 指定时建其父目录），返回 row 工厂连接"""
    try:
        config.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建表初始化（幂等）"""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def ping() -> tuple[bool, str]:
    """健康检查：数据库能否正常读写。返回 (是否正常, 说明)"""
    try:
        conn = connect()
        try:
            conn.execute("SELECT 1")
            conn.commit()
        finally:
            conn.close()
        return True, "ok"
    except Exception as e:  # noqa: BLE001 - 健康检查需兜底所有异常
        return False, f"{type(e).__name__}: {e}"
