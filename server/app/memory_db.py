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
import logging
import sqlite3
from pathlib import Path

from . import config

_logger = logging.getLogger("moray.memory_db")

#: [向量召回 v1 2026-10-24] 嵌入维度。nomic-embed-text=768 维；换模型=换维时
#: 本常量与 _load_vec 里 memory_vec 建表 DDL 的 float[768] 字面量一起改（vec0
#: 建表定维；重建=删 memory_vec 虚表重开，源数据真身 memory_bank.embedding 由
#: memory_embed 重写）。
EMBED_DIM = 768

#: [向量召回 v1] sqlite-vec 可用性（进程级探测一次）：可加载 → 之后所有 connect()
#: 出的连接都带 vec 扩展，并自动 CREATE memory_vec 虚表（IF NOT EXISTS 幂等）；
#: 加载失败 → VEC_AVAILABLE 恒 False，检索侧由 memory_embed.search_similar 降级
#: 纯 Python 余弦（任务书兜底要求），数据面（bank/FTS 双写）零影响。
VEC_AVAILABLE = False
try:
    import sqlite_vec as _sqlite_vec  # type: ignore
except ImportError:  # noqa: BLE001 - 未安装 sqlite-vec：走纯 Python 兜底
    _sqlite_vec = None

_VEC_PROBED = False


def _load_vec(conn: sqlite3.Connection) -> None:
    """单连接装载 vec 扩展 + 幂等建虚表（DDL 内联字面量；维度与 EMBED_DIM 同步改）。
    首次失败即永久放弃（环境性缺 DLL/权限不会自愈，反复试只刷日志）；成功后每次
    连接都静默重装（扩展是连接级作用域）。"""
    global VEC_AVAILABLE, _VEC_PROBED
    if not VEC_AVAILABLE and _VEC_PROBED:
        return
    try:
        conn.enable_load_extension(True)
        _sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(memory_id TEXT PRIMARY KEY, embedding float[768])")
        conn.commit()
        VEC_AVAILABLE = True
    except Exception as e:  # noqa: BLE001 - 降级纯 Python，不阻连接
        VEC_AVAILABLE = False
        _logger.warning("[memory_db] sqlite-vec 加载失败，向量检索降级纯 Python "
                        "余弦（bank 数据面不受影响）：%s: %s",
                        type(e).__name__, str(e)[:160])
    finally:
        _VEC_PROBED = True

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
    """建立记忆库连接（自动创建父目录；返回 row 工厂连接）。
    [向量召回 v1] 附带 busy_timeout=5s（后台嵌入线程与主线程并发写让一让）；
    sqlite-vec 可装则每连接装载扩展并幂等建 memory_vec（失败降级见上）。"""
    try:
        Path(str(config.MEMORY_DB)).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    conn = sqlite3.connect(str(config.MEMORY_DB))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error:
        pass
    if _sqlite_vec is not None:
        _load_vec(conn)
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
