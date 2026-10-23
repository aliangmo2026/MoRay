"""Synapse 神经总线独立事件库连接与建表（2026-10-06 v1）

- 库文件独立于主库（config.SYNAPSE_DB，默认 server/data/synapse.sqlite3），
  与 MEMORY_DB / MORAY_BUDGET_DB 同一"独立库"惯例：总线数据可单独剥离/备份/迁移。
- 结构照抄 memory_db.py（connect + SCHEMA + init），零依赖主库 db.py（不 import、不改其 SCHEMA）。
- 五表（v3-lite 增第五表）：rooms / threads / messages / summaries / rules。
  字段语义裁定（v1）：
    * synapse_messages.proxy_source 存事件类型（如 zcode.task.succeeded）——
      synapse_bus 规则分发按它匹配，等价于 Matrix 的 m.room.message 类型位；
    * mentions 存逗号串（memory_db.tags 同款惯例）；
    * meta 存 JSON 字符串（事件负载：task_id / duration_ms / exit_code 等）；
    * trace_id 是幂等键：同 trace_id 不重复发（synapse_bus.send 基本判重用）；
    * synapse_rules 是规则表（v3-lite 落库，v1 硬编码 _RULES 的继任）：
      复合主键 (event_type, handler) 天然防重；enabled 0/1 消费端 SQL 过滤；
      params_json 预留（handler 现不消费参数）；消费端每事件查表 → 改库即时生效。
- 消费哲学（Argus event_hub 同款）：库是唯一事实来源，队列只负责叫醒——
  叫醒丢了只退化消费延迟，事实永远在库里。
"""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS synapse_rooms (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS synapse_threads (
  id               TEXT PRIMARY KEY,
  room_id          TEXT NOT NULL DEFAULT '',
  parent_thread_id TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_syn_threads_room ON synapse_threads(room_id);

CREATE TABLE IF NOT EXISTS synapse_messages (
  id           TEXT PRIMARY KEY,
  thread_id    TEXT NOT NULL DEFAULT '',
  from_member  TEXT NOT NULL DEFAULT 'system',
  content      TEXT NOT NULL DEFAULT '',
  mentions     TEXT NOT NULL DEFAULT '',
  trace_id     TEXT NOT NULL DEFAULT '',
  proxy_source TEXT NOT NULL DEFAULT '',
  meta         TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_syn_messages_thread ON synapse_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_syn_messages_trace ON synapse_messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_syn_messages_source ON synapse_messages(proxy_source);
CREATE INDEX IF NOT EXISTS idx_syn_messages_created ON synapse_messages(created_at);

CREATE TABLE IF NOT EXISTS synapse_summaries (
  id         TEXT PRIMARY KEY,
  thread_id  TEXT NOT NULL DEFAULT '',
  summary    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_syn_summaries_thread ON synapse_summaries(thread_id);

CREATE TABLE IF NOT EXISTS synapse_rules (
  event_type  TEXT NOT NULL,
  handler     TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  params_json TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  PRIMARY KEY (event_type, handler)
);
CREATE INDEX IF NOT EXISTS idx_syn_rules_event ON synapse_rules(event_type);
"""


def connect() -> sqlite3.Connection:
    """建立 synapse 库连接（自动创建父目录；返回 row 工厂连接）"""
    try:
        Path(str(config.SYNAPSE_DB)).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    conn = sqlite3.connect(str(config.SYNAPSE_DB))
    conn.row_factory = sqlite3.Row
    return conn


#: v3-lite 规则 seed（init 时 INSERT OR IGNORE，幂等）：与 v1 硬编码 _RULES 等价的
#: 第一链 + 新增"argus 研究完成 → 反思"（v4 代理接入的前置）。存量行不动——
#: 手工 enabled=0 的禁用规则重启后保持禁用。
SEED_RULES = [
    ("zcode.task.succeeded", "reflection.run"),
    ("argus.research.succeeded", "reflection.run"),
    # [自我模型 v4 2026-10-23] 失败/成功 → 实时状态机（self_state_log 流水）；
    # 同一事件可命中多条规则（succeeded → reflection.run + bump_success 并行）
    ("zcode.task.failed", "self_state.bump_fail"),
    ("laya.task.failed", "self_state.bump_fail"),
    ("zcode.task.succeeded", "self_state.bump_success"),
    ("laya.task.succeeded", "self_state.bump_success"),
]


def init_synapse_db() -> None:
    """建表初始化（幂等）。executescript 全部 CREATE IF NOT EXISTS，重复跑安全；
    v3-lite 起顺带 seed 规则表（INSERT OR IGNORE，复合主键天然防重）；
    v1 四表无虚拟表/无迁移需求，与 memory_db 同款最小形态。"""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for event_type, handler in SEED_RULES:
            conn.execute(
                "INSERT OR IGNORE INTO synapse_rules (event_type, handler, enabled, params_json) "
                "VALUES (?, ?, 1, NULL)",
                (event_type, handler))
        conn.commit()
    finally:
        conn.close()
