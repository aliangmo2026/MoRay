"""MemoryBridge 独立记忆库连接与建表（2026-10-02 M1）

- 库文件独立于主库（config.MEMORY_DB，默认 server/data/memory.sqlite3），
  与 MORAY_BUDGET_DB 同一"独立库"惯例：用户个人数据可单独剥离/备份/迁移。
- 结构参考 db.py（connect + SCHEMA + init），但零依赖主库 db.py（不 import、不改其 SCHEMA）。
- FTS5 trigram tokenizer（SQLite ≥3.34；生产 venv 3.43.1 已实测）：
  中文按 3 字滑窗建倒排，substring 语义召回可用；unicode61 对连续中文整段成 token，
  子串召回失效，故选 trigram。代价：检索词每段需 ≥3 字符。
- embedding / embedding_model / summary / weight 为预留升级字段（MVP 不写入，
  embedding 留给未来向量检索，summary 留给未来摘要压缩）。
"""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_bank (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL DEFAULT 'fact',
  content         TEXT NOT NULL,
  summary         TEXT NOT NULL DEFAULT '',
  tags            TEXT NOT NULL DEFAULT '',
  source          TEXT NOT NULL DEFAULT '',
  entity_id       TEXT NOT NULL DEFAULT '',
  session_id      TEXT NOT NULL DEFAULT '',
  weight          REAL NOT NULL DEFAULT 1.0,
  embedding       BLOB,
  embedding_model TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_bank(kind);
CREATE INDEX IF NOT EXISTS idx_memory_source ON memory_bank(source);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_bank(created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  memory_id UNINDEXED, content, tags, tokenize='trigram'
);

-- [自我模型 v4 2026-10-23] 实时状态机流水：每次事件追加一行（快照式，
-- 不 UPDATE 旧行），consecutive_failures/mental_score 由 self_state.bump_state
-- 读上一行推算后写入本行。
CREATE TABLE IF NOT EXISTS self_state_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
  engine TEXT NOT NULL,
  event TEXT NOT NULL,
  consecutive_failures INTEGER DEFAULT 0,
  mental_score INTEGER DEFAULT 100,
  note TEXT
);
"""


def connect() -> sqlite3.Connection:
    """建立记忆库连接（自动创建父目录；返回 row 工厂连接）"""
    try:
        Path(str(config.MEMORY_DB)).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    conn = sqlite3.connect(str(config.MEMORY_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db() -> None:
    """建表初始化（幂等）。executescript 支持 CREATE VIRTUAL TABLE IF NOT EXISTS，
    旧库升级到本版本时同样安全；FTS 虚拟表不支持 ADD COLUMN，无需 _migrate 机制。"""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
