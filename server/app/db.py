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

-- [Kernel 阶段1 预埋 / 国庆冲刺 Day2 补齐] 事件溯源快照：记录关键事件发生时的状态，供「回放」使用。
-- Day2 起由 agent_tools._audit 统一写入（每次工具调用一条，不再允许漏写）；
-- 新增 7 个可查询维度列（session_id/tool_name/status/duration_ms/error_msg/input_json/output_json），
-- 完整快照本体仍在 snapshot_json（保持既有回放契约的键形状不变）。
CREATE TABLE IF NOT EXISTS event_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT, -- = event_id
  event_type    TEXT NOT NULL,           -- 事件类型：tool_call / operation / conversation_created / message_sent / model_changed / settings_changed 等
  entity_id     TEXT NOT NULL DEFAULT '', -- 关联实体 ID（如 tool 调用关联 agent_tool_log.id，会话关联 conversations.id）
  source_log_id INTEGER,                  -- 关联 agent_tool_log.id（如果是工具调用事件）
  snapshot_json TEXT NOT NULL DEFAULT '{}', -- 事件发生时的状态快照（JSON 字符串）
  created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), -- = timestamp
  session_id    TEXT NOT NULL DEFAULT '', -- [Day2] 会话归属（缺失归 "default"，与 llm_proxy 熔断同约定）
  tool_name     TEXT NOT NULL DEFAULT '', -- [Day2] 工具/事件名（与 snapshot_json.tool 同步）
  status        TEXT NOT NULL DEFAULT '', -- [Day2] ok / error / timeout / rejected / needs_approval / denied
  duration_ms   INTEGER NOT NULL DEFAULT 0, -- [Day2] 执行耗时（与 snapshot_json.ms 同步）
  error_msg     TEXT NOT NULL DEFAULT '', -- [Day2] 失败/拒绝原因（成功时为空串）
  input_json    TEXT NOT NULL DEFAULT '', -- [Day2] 完整入参 JSON
  output_json   TEXT NOT NULL DEFAULT '', -- [Day2] 完整执行结果 JSON（未执行/失败为空串）
  FOREIGN KEY (source_log_id) REFERENCES agent_tool_log(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_type ON event_snapshots(event_type);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_entity ON event_snapshots(entity_id);
CREATE INDEX IF NOT EXISTS idx_event_snapshots_created ON event_snapshots(created_at);

-- [国庆冲刺 Day3] Agent 会话状态表：session_id（Day1/Day2 同一词表与解析链）→ 聚合状态。
-- 状态机 pending → running → completed / failed；重启遗留的 pending/running 由启动钩子标
-- interrupted（断点恢复只做状态查询，不自动续跑）。流转规则的实现见 crud.transition_agent_session。
CREATE TABLE IF NOT EXISTS agent_sessions (
  session_id    TEXT PRIMARY KEY,
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed', 'interrupted')),
  created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  last_event_id INTEGER,                   -- 该会话最近一次事件（event_snapshots.id，Day2 事件流）
  error_msg     TEXT NOT NULL DEFAULT ''   -- failed/interrupted 的原因；回到 running/completed 时清空
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status);

-- [真实数据链路 2026-10-26] 对话级真实用量账本：llm_proxy 流式/非流式收尾各写一行。
-- tokens 为上游真值（Ollama eval_count / OpenAI 兼容 usage 块）；cost_cny 后端无价目表
-- 暂记 0.0（金额由前端 CostEngine 按其人民币价目表换算，后端只记事实）。
CREATE TABLE IF NOT EXISTS usage_log (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  date              TEXT NOT NULL,              -- 本地日期 YYYY-MM-DD（按日聚合键）
  model             TEXT NOT NULL DEFAULT '',   -- 实际应答模型（非请求模型）
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  cost_cny          REAL NOT NULL DEFAULT 0.0,  -- 预留：后端价目表接入前恒 0.0
  ms                INTEGER NOT NULL DEFAULT 0, -- 端到端耗时（首字到收尾按实现口径）
  ts                INTEGER NOT NULL DEFAULT 0  -- epoch 毫秒
);
CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_log_date ON usage_log(date);
"""

# [国庆冲刺 Day2] 幂等迁移：event_snapshots 为既有库补列。
# SQLite 的 ADD COLUMN 带常量 DEFAULT 合法且不动既有行；已存在的列跳过 → 可反复执行。
_MIGRATION_COLUMNS = [
    ("event_snapshots", "session_id", "TEXT NOT NULL DEFAULT ''"),
    ("event_snapshots", "tool_name", "TEXT NOT NULL DEFAULT ''"),
    ("event_snapshots", "status", "TEXT NOT NULL DEFAULT ''"),
    ("event_snapshots", "duration_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("event_snapshots", "error_msg", "TEXT NOT NULL DEFAULT ''"),
    ("event_snapshots", "input_json", "TEXT NOT NULL DEFAULT ''"),
    ("event_snapshots", "output_json", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补 Day2 新列（PRAGMA 查列 → 缺哪列 ADD 哪列，幂等且保留全部既有数据）"""
    for table, column, decl in _MIGRATION_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
    """建表初始化（幂等）+ 列迁移（旧库自动补齐 Day2 事件维度列，不动既有数据）"""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
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
