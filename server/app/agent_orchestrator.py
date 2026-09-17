"""MoRay v3.22.0 —— 「编排工头」后端引擎（新文件）

定位：把 v3.21 的「单任务执行器」升级为「工头」——
用户只给一个大任务，MoRay 用**真实模型**拆解成多个子任务，按类型分配给不同工人，
**串行**排队执行，最后给出汇总（每个子任务状态/耗时/成本 + 总成本）。

设计约束（与既有代码风格一致）：
- 工人抽象：`worker: 子任务 → {status, output, usage, elapsed_sec}`。本版两名工人：
  * codex 工人 —— 直接复用 agent_runner 的 `Run` + `_worker`（真 codex exec / 真 JSONL usage /
    真 taskkill 进程树），**不注册**到 agent_runner 的运行表（计划的锁令牌已占用执行闸门，
    注册会覆盖 _ACTIVE，导致第一个子任务结束后闸门被误释放）；
  * ollama 工人 —— 后端直调 `POST <ollama>/api/chat`（stream=false），本地推理成本恒为 ¥0。
- 单机串行：计划运行期间**拒绝** v3.21 的单任务 run，反之亦然。实现方式见 `_PlanToken` 注释 ——
  复用 agent_runner 的运行注册表当执行闸门，不改动 agent_runner.py。
- 诚实原则（铁律）：
  * codex 成本口径 = 真实 usage × 前端 CostEngine 单价（后端只回传原始 usage，不自己算钱，
    避免价格表两处漂移）；ollama 成本 = 常量 0.0（真实：本地推理无费用）；
  * 拿不到 usage 就存 NULL，前端显示"未能获取"，**绝不编造 token 数或金额**；
  * 拆解失败 / 工人不可用一律返回明确错误 + 原始输出，**不静默降级成假成功**；
  * 计划状态只由真实子任务结果决定；后端重启后仍标 running 的计划如实标「应用重启导致中断」。

协议（与 agent_tools.py / agent_runner.py 一致的 {ok,data} / {ok:false,code,message} 信封）：
  GET  /api/orchestrator/preflight                 工人可用性 + 占用状态（按钮禁用原因用）
  POST /api/orchestrator/decompose                 大任务 → 子任务（真调 Ollama，免费）
  GET  /api/orchestrator/plans?limit=N             计划列表（页面刷新后恢复展示）
  POST /api/orchestrator/plans                     创建计划
  GET  /api/orchestrator/plans/{plan_id}?offset=N  计划详情 + 子任务进度 + 当前子任务增量日志
  POST /api/orchestrator/plans/{plan_id}/run       启动串行队列
  POST /api/orchestrator/plans/{plan_id}/stop      停止（杀当前子任务进程树，未开始 → canceled）
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import agent_runner, config, db

_logger = logging.getLogger("moray.orchestrator")

router = APIRouter()

# ---- 常量 ----
DEFAULT_TIMEOUT = 600                 # 每个子任务默认超时（秒），计划级可配
MAX_SUBTASKS = 12                     # 计划最多子任务数（含用户手工添加）
DECOMPOSE_MAX = 8                     # 拆解最多产出子任务数（题目要求）
LOG_KEEP = 4000                       # 内存保留的子任务日志行数（与 agent_runner 一致）
OUTPUT_KEEP = 20000                   # 子任务输出入库上限（字符）
OUTPUT_PREVIEW = 1500                 # 轮询响应里的输出预览上限（字符；展开时再取全文）
OUTPUT_FULL = 8000                    # full=1 时的输出上限（字符）
PLAN_HISTORY = 100                    # 内存保留的计划对象数
OLLAMA_URL_DEFAULT = "http://127.0.0.1:11434"
DECOMPOSE_MODELS = ("qwen3.5:9b", "qwen2.5:7b")   # 拆解模型：默认 → 降级（题目指定）
DECOMPOSE_TIMEOUT = 180               # 拆解单次请求超时（秒）；本地 9b 模型较慢，给足
OLLAMA_CHAT_TIMEOUT = 120             # ollama 工人单次请求超时（秒）

PLAN_STATUS = ("pending", "running", "completed", "canceled", "interrupted")
SUB_STATUS = ("pending", "running", "completed", "failed", "timeout", "canceled")
_AUTO_INDEX = -2               # log_index=-2：自动挑"日志最多的那个子任务"（前端重新展开时用）

# 工人推荐规则（worker=auto 时）：先看"代码/文件/命令"类词，再看"总结/解释"类词，都判不了 → codex（保守）
_CODE_WORDS = (
    "代码", "文件", "命令", "修复", "实现", "重构", "测试", "脚本", "配置", "部署", "编译", "调试",
    "修改", "编写", "新增", "创建", "生成", "目录", "工程", "接口", "函数", "数据库", "安装",
    "运行", "打包", "构建", "迁移", "清理", "排查", "bug", "python", "js", "sql", "json", "api",
)
_TEXT_WORDS = (
    "总结", "解释", "改写", "问答", "翻译", "说明", "概括", "归纳", "描述", "回答", "建议", "评估",
    "分析", "对比", "梳理", "罗列", "整理", "阅读", "讲解", "提炼",
)


# ---------------------------------------------------------------- 工具函数

def _err(status_code: int, code: str, message: str, extra=None):
    content = {"ok": False, "code": code, "message": message}
    if isinstance(extra, dict):
        content.update(extra)
    return JSONResponse(status_code=status_code, content=content)


def _clamp_timeout(v) -> int:
    try:
        return agent_runner._clamp_timeout(v)
    except Exception:  # noqa: BLE001 - 兜底：调用方异常不该让接口 500
        return DEFAULT_TIMEOUT


def _now() -> float:
    return time.time()


def _norm_worker(v) -> str:
    w = str(v or "").strip().lower()
    return w if w in ("auto", "codex", "ollama") else "auto"


def recommend_worker(title: str, detail: str = "") -> str:
    """worker=auto 时的推荐规则（与题目约定一致，可被用户手工覆盖）。

    - 含"代码/文件/命令/修复/实现/重构/测试"等词 → codex（要动文件、跑命令）
    - 含"总结/解释/改写/问答/翻译"等词 → ollama（只读材料出文字）
    - 判不了 → codex（保守：代码任务更可靠）
    """
    text = (str(title or "") + " " + str(detail or "")).lower()
    if any(w in text for w in _CODE_WORDS):
        return "codex"
    if any(w in text for w in _TEXT_WORDS):
        return "ollama"
    return "codex"


def _effective_worker(sub: dict, pool: str) -> str:
    """子任务最终由谁执行。

    工人池（pool）是硬约束：pool='codex'/'ollama' 时全部子任务都用该工人（前端会锁住下拉，
    不会出现"改了没生效"的静默行为）；pool='both' 时才看子任务的 worker 字段（auto → 推荐规则）。
    """
    pool = str(pool or "both").lower()
    if pool in ("codex", "ollama"):
        return pool
    w = _norm_worker(sub.get("worker"))
    return recommend_worker(sub.get("title"), sub.get("detail")) if w == "auto" else w


# ---------------------------------------------------------------- 持久化（自有表）
# db.py 的 SCHEMA 不在这里改；本模块自带幂等 DDL，连接走 db.connect()（MORAY_DB 隔离依然生效）。

DDL = """
CREATE TABLE IF NOT EXISTS orchestrator_plans (
  id              TEXT PRIMARY KEY,
  title           TEXT NOT NULL DEFAULT '',
  goal            TEXT NOT NULL DEFAULT '',
  project_path    TEXT NOT NULL DEFAULT '',
  worker_pool     TEXT NOT NULL DEFAULT 'both',
  fail_fast       INTEGER NOT NULL DEFAULT 0,
  timeout_sec     INTEGER NOT NULL DEFAULT 600,
  status          TEXT NOT NULL DEFAULT 'pending',
  decompose_model TEXT NOT NULL DEFAULT '',
  codex_command   TEXT NOT NULL DEFAULT '',
  ollama_url      TEXT NOT NULL DEFAULT '',
  ollama_model    TEXT NOT NULL DEFAULT '',
  fail_fast_tripped INTEGER NOT NULL DEFAULT 0,
  note            TEXT NOT NULL DEFAULT '',
  created_at      REAL NOT NULL DEFAULT 0,
  started_at      REAL,
  ended_at        REAL,
  duration_sec    REAL,
  updated_at      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orchestrator_subtasks (
  id            TEXT PRIMARY KEY,
  plan_id       TEXT NOT NULL,
  idx           INTEGER NOT NULL,
  title         TEXT NOT NULL DEFAULT '',
  detail        TEXT NOT NULL DEFAULT '',
  worker        TEXT NOT NULL DEFAULT 'auto',
  status        TEXT NOT NULL DEFAULT 'pending',
  output        TEXT NOT NULL DEFAULT '',
  error         TEXT NOT NULL DEFAULT '',
  usage         TEXT,
  local_tokens  TEXT,
  changed_files TEXT,
  log_path      TEXT NOT NULL DEFAULT '',
  elapsed_sec   REAL,
  started_at    REAL,
  ended_at      REAL,
  cost_cny      REAL,
  updated_at    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orch_sub_plan ON orchestrator_subtasks(plan_id, idx);
"""

_tables_ready = False
_ddl_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _tables_ready
    conn = db.connect()
    if not _tables_ready:
        with _ddl_lock:
            if not _tables_ready:
                conn.executescript(DDL)
                conn.commit()
                _tables_ready = True
    return conn


def _json_or_none(v):
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _loads_or_none(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def _save_plan_row(plan: dict) -> None:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO orchestrator_plans
                 (id, title, goal, project_path, worker_pool, fail_fast, timeout_sec, status,
                  decompose_model, codex_command, ollama_url, ollama_model, fail_fast_tripped, note,
                  created_at, started_at, ended_at, duration_sec, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, goal=excluded.goal, project_path=excluded.project_path,
                 worker_pool=excluded.worker_pool, fail_fast=excluded.fail_fast,
                 timeout_sec=excluded.timeout_sec, status=excluded.status,
                 decompose_model=excluded.decompose_model, codex_command=excluded.codex_command,
                 ollama_url=excluded.ollama_url, ollama_model=excluded.ollama_model,
                 fail_fast_tripped=excluded.fail_fast_tripped,
                 note=excluded.note, started_at=excluded.started_at, ended_at=excluded.ended_at,
                 duration_sec=excluded.duration_sec, updated_at=excluded.updated_at""",
            (
                plan["id"], plan.get("title", ""), plan.get("goal", ""), plan.get("project_path", ""),
                plan.get("worker_pool", "both"), 1 if plan.get("fail_fast") else 0,
                int(plan.get("timeout_sec") or DEFAULT_TIMEOUT), plan.get("status", "pending"),
                plan.get("decompose_model", ""), plan.get("codex_command", ""),
                plan.get("ollama_url", ""), plan.get("ollama_model", ""),
                1 if plan.get("fail_fast_tripped") else 0,
                str(plan.get("note", ""))[:2000],
                float(plan.get("created_at") or 0), plan.get("started_at"), plan.get("ended_at"),
                plan.get("duration_sec"), _now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _save_subtask_row(plan_id: str, st: dict) -> None:
    conn = _conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO orchestrator_subtasks
                 (id, plan_id, idx, title, detail, worker, status, output, error, usage,
                  local_tokens, changed_files, log_path, elapsed_sec, started_at, ended_at,
                  cost_cny, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, detail=excluded.detail, worker=excluded.worker,
                 idx=excluded.idx, status=excluded.status, output=excluded.output,
                 error=excluded.error, usage=excluded.usage, local_tokens=excluded.local_tokens,
                 changed_files=excluded.changed_files, log_path=excluded.log_path,
                 elapsed_sec=excluded.elapsed_sec, started_at=excluded.started_at,
                 ended_at=excluded.ended_at, cost_cny=excluded.cost_cny, updated_at=excluded.updated_at""",
            (
                st["id"], plan_id, int(st.get("idx") or 0), str(st.get("title", ""))[:300],
                str(st.get("detail", "")), str(st.get("worker", "auto")), st.get("status", "pending"),
                str(st.get("output") or "")[:OUTPUT_KEEP], str(st.get("error") or "")[:2000],
                _json_or_none(st.get("usage")), _json_or_none(st.get("local_tokens")),
                _json_or_none(st.get("changed_files")), str(st.get("log_path") or "")[:500],
                st.get("elapsed_sec"), st.get("started_at"), st.get("ended_at"),
                st.get("cost_cny"), _now(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_subtask(row) -> dict:
    return {
        "id": row["id"], "idx": row["idx"], "title": row["title"], "detail": row["detail"],
        "worker": row["worker"], "status": row["status"], "output": row["output"],
        "error": row["error"], "usage": _loads_or_none(row["usage"]),
        "local_tokens": _loads_or_none(row["local_tokens"]),
        "changed_files": _loads_or_none(row["changed_files"]) or [],
        "log_path": row["log_path"], "elapsed_sec": row["elapsed_sec"],
        "started_at": row["started_at"], "ended_at": row["ended_at"], "cost_cny": row["cost_cny"],
    }


def _row_to_plan(row, subtasks: list) -> dict:
    """DB 行 → 计划 dict。**字段形状与 _Plan.summary() 保持一致**（前端不必区分来源）。"""
    pool = row["worker_pool"]
    for s in subtasks:
        s["effective_worker"] = _effective_worker(s, pool)
    done = sum(1 for s in subtasks if s["status"] == "completed")
    failed = sum(1 for s in subtasks if s["status"] in ("failed", "timeout"))
    canceled = sum(1 for s in subtasks if s["status"] == "canceled")
    running = sum(1 for s in subtasks if s["status"] == "running")
    return {
        "id": row["id"], "title": row["title"], "goal": row["goal"],
        "project_path": row["project_path"], "worker_pool": pool,
        "fail_fast": bool(row["fail_fast"]), "timeout_sec": row["timeout_sec"],
        "status": row["status"], "decompose_model": row["decompose_model"],
        "codex_command": row["codex_command"], "ollama_url": row["ollama_url"],
        "ollama_model": row["ollama_model"],
        "fail_fast_tripped": bool(row["fail_fast_tripped"]), "note": row["note"],
        "created_at": row["created_at"], "started_at": row["started_at"],
        "ended_at": row["ended_at"], "duration_sec": row["duration_sec"],
        "subtask_count": len(subtasks), "done_count": done, "failed_count": failed,
        "canceled_count": canceled, "running_count": running,
        "current_index": next((s["idx"] for s in subtasks if s["status"] == "running"), None),
        "subtasks": subtasks,
    }


def load_plan_from_db(plan_id: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM orchestrator_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        subs = conn.execute(
            "SELECT * FROM orchestrator_subtasks WHERE plan_id = ? ORDER BY idx ASC", (plan_id,)
        ).fetchall()
        return _row_to_plan(row, [_row_to_subtask(s) for s in subs])
    finally:
        conn.close()


def list_plans_from_db(limit: int = 30) -> list:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM orchestrator_plans ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),)
        ).fetchall()
        out = []
        for r in rows:
            subs = conn.execute(
                "SELECT * FROM orchestrator_subtasks WHERE plan_id = ? ORDER BY idx ASC", (r["id"],)
            ).fetchall()
            out.append(_row_to_plan(r, [_row_to_subtask(s) for s in subs]))
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- 计划 / 子任务对象

def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def _new_subtask(idx: int, raw: dict, pool: str) -> dict:
    st = {
        "id": _new_id("s"), "idx": idx,
        "title": str(raw.get("title") or "").strip()[:300],
        "detail": str(raw.get("detail") or raw.get("content") or "").strip(),
        "worker": _norm_worker(raw.get("worker")),
        "status": "pending", "output": "", "error": "", "usage": None, "local_tokens": None,
        "changed_files": [], "log_path": "", "elapsed_sec": None,
        "started_at": None, "ended_at": None, "cost_cny": None,
    }
    st["effective_worker"] = _effective_worker(st, pool)
    return st


def _public_subtask(s: dict, full: bool = False) -> dict:
    out = str(s.get("output") or "")
    limit = OUTPUT_FULL if full else OUTPUT_PREVIEW
    return {
        "id": s["id"], "idx": s["idx"], "title": s["title"], "detail": s["detail"],
        "worker": s["worker"],
        "effective_worker": s.get("effective_worker") or _effective_worker(s, "both"),
        "status": s["status"], "error": s.get("error", ""),
        "output": out[:limit], "output_len": len(out), "output_truncated": len(out) > limit,
        "usage": s.get("usage"), "local_tokens": s.get("local_tokens"),
        "changed_files": s.get("changed_files") or [], "log_path": s.get("log_path", ""),
        "elapsed_sec": s.get("elapsed_sec"), "started_at": s.get("started_at"),
        "ended_at": s.get("ended_at"), "cost_cny": s.get("cost_cny"),
    }


class _Plan:
    """一次编排计划（大任务 + 子任务队列 + 运行期状态）"""

    def __init__(self, data: dict):
        self.id = data["id"]
        self.title = data.get("title", "")
        self.goal = data.get("goal", "")
        self.project_path = data.get("project_path", "")
        self.worker_pool = data.get("worker_pool", "both")
        self.fail_fast = bool(data.get("fail_fast"))
        self.timeout_sec = int(data.get("timeout_sec") or DEFAULT_TIMEOUT)
        self.status = data.get("status", "pending")
        self.decompose_model = data.get("decompose_model", "")
        self.codex_command = data.get("codex_command", "")
        self.ollama_url = data.get("ollama_url", "")
        self.ollama_model = data.get("ollama_model", "")
        self.fail_fast_tripped = bool(data.get("fail_fast_tripped"))
        self.note = data.get("note", "")
        self.created_at = data.get("created_at") or _now()
        self.started_at = data.get("started_at")
        self.ended_at = data.get("ended_at")
        self.duration_sec = data.get("duration_sec")
        self.subtasks = list(data.get("subtasks") or [])
        self._codex_path = ""             # 运行前 preflight 解析出的 codex 可执行绝对路径

        self.stopped = False              # 用户点了停止（volatile）
        self.current_index = None         # 正在跑的子任务 idx
        self.logs = {}                    # 子任务 idx → {"lines": [...], "dropped": n}；-1 = 计划级
        self.active_run = None            # 当前 codex 子任务的 agent_runner.Run（停止时杀它）
        self.active_client = None         # 当前 ollama 子任务的 httpx.Client（停止时关它）
        self.lock = threading.RLock()
        self.thread = None
        self.token = None                 # 共用执行闸门令牌

    # ---- 日志 ----
    def log(self, idx, line: str):
        key = -1 if idx is None else int(idx)
        for raw in str(line).splitlines():
            raw = raw.rstrip("\r\n")
            if raw == "":
                continue
            with self.lock:
                buf = self.logs.setdefault(key, {"lines": [], "dropped": 0})
                buf["lines"].append(raw)
                if len(buf["lines"]) > LOG_KEEP:
                    drop = len(buf["lines"]) - LOG_KEEP
                    del buf["lines"][:drop]
                    buf["dropped"] += drop

    def log_snapshot(self, idx, offset: int = 0) -> dict:
        key = -1 if idx is None else int(idx)
        with self.lock:
            if key == _AUTO_INDEX:
                # log_index=-2：挑"日志最多的那个子任务"（刷新/重新展开时前端不知道哪个有内容）
                best, best_n = -1, len((self.logs.get(-1) or {"lines": []})["lines"])
                for k, buf in self.logs.items():
                    if isinstance(k, int) and k >= 0 and len(buf["lines"]) > best_n:
                        best, best_n = k, len(buf["lines"])
                key = best
            buf = self.logs.get(key)
            if buf is None:
                return {"lines": [], "offset": 0, "total_lines": 0, "dropped": 0,
                        "truncated": False, "index": None if key == -1 else key}
            total = buf["dropped"] + len(buf["lines"])
            off = max(0, int(offset or 0))
            if off >= total:
                chunk = []
            else:
                start = max(0, off - buf["dropped"])
                chunk = list(buf["lines"][start:])
            return {"lines": chunk, "offset": off, "total_lines": total,
                    "dropped": buf["dropped"], "truncated": off < buf["dropped"],
                    "index": None if key == -1 else key}

    # ---- 序列化 ----
    def summary(self, full: bool = False) -> dict:
        with self.lock:
            subs = [_public_subtask(s, full) for s in self.subtasks]
            cur = self.current_index
        done = sum(1 for s in subs if s["status"] == "completed")
        failed = sum(1 for s in subs if s["status"] in ("failed", "timeout"))
        canceled = sum(1 for s in subs if s["status"] == "canceled")
        running = sum(1 for s in subs if s["status"] == "running")
        return {
            "id": self.id, "title": self.title, "goal": self.goal,
            "project_path": self.project_path, "worker_pool": self.worker_pool,
            "fail_fast": self.fail_fast, "fail_fast_tripped": self.fail_fast_tripped,
            "timeout_sec": self.timeout_sec, "status": self.status,
            "decompose_model": self.decompose_model, "codex_command": self.codex_command,
            "ollama_url": self.ollama_url, "ollama_model": self.ollama_model, "note": self.note,
            "created_at": self.created_at, "started_at": self.started_at,
            "ended_at": self.ended_at, "duration_sec": self.duration_sec,
            "subtask_count": len(subs), "done_count": done, "failed_count": failed,
            "canceled_count": canceled, "running_count": running,
            "current_index": cur,
            "subtasks": subs,
        }

    def to_db(self) -> dict:
        return {
            "id": self.id, "title": self.title, "goal": self.goal,
            "project_path": self.project_path, "worker_pool": self.worker_pool,
            "fail_fast": self.fail_fast, "timeout_sec": self.timeout_sec, "status": self.status,
            "decompose_model": self.decompose_model, "codex_command": self.codex_command,
            "ollama_url": self.ollama_url, "ollama_model": self.ollama_model,
            "fail_fast_tripped": self.fail_fast_tripped,
            "note": self.note, "created_at": self.created_at, "started_at": self.started_at,
            "ended_at": self.ended_at, "duration_sec": self.duration_sec,
        }

    def save_plan(self):
        try:
            _save_plan_row(self.to_db())
        except Exception as e:  # noqa: BLE001 - 持久化失败不影响执行，但要如实记录
            _logger.warning("orchestrator plan save failed: %s", e)
            self.log(None, "[MoRay] 计划状态入库失败：%s" % e)

    def save_sub(self, s: dict):
        try:
            _save_subtask_row(self.id, s)
        except Exception as e:  # noqa: BLE001
            _logger.warning("orchestrator subtask save failed: %s", e)
            self.log(s.get("idx"), "[MoRay] 子任务状态入库失败：%s" % e)


# ---------------------------------------------------------------- 共用执行闸门
# 借用 agent_runner 的运行注册表当"单机串行"闸门：
#  * 计划在跑 → agent_runner._busy_run() 返回本令牌 → v3.21 的单任务入口自动 409 busy；
#  * 单任务在跑 → 本模块检查 _busy_run() → 拒绝启动计划。
# 这样两边共用同一把锁，且**不需要改动 agent_runner.py**。

class _PlanToken(agent_runner.Run):
    """编排计划的执行闸门令牌（不是真实子进程，只是一个诚实的占位运行对象）"""

    def __init__(self, plan_id: str, title: str, project_path: str, timeout_sec: int):
        super().__init__(task_id="编排计划：" + (title or plan_id), prompt="", project_path=project_path,
                         command_path="", command_display="orchestrator", timeout_sec=timeout_sec,
                         model=None)
        self.plan_id = plan_id

    def summary(self) -> dict:
        d = dict(super().summary())
        d["kind"] = "orchestrator_plan"
        d["plan_id"] = self.plan_id
        d["error"] = "编排计划占用执行闸门（本机串行）"
        return d


_PLANS = {}          # plan_id -> _Plan（运行中 + 最近 PLAN_HISTORY 条）
_PLAN_ORDER = []
_PLAN_LOCK = threading.RLock()


def _register_plan(plan: _Plan):
    with _PLAN_LOCK:
        _PLANS[plan.id] = plan
        if plan.id not in _PLAN_ORDER:
            _PLAN_ORDER.append(plan.id)
        while len(_PLAN_ORDER) > PLAN_HISTORY:
            old = _PLAN_ORDER.pop(0)
            keep = _PLANS.get(old)
            if keep is not None and keep.status != "running" and old != plan.id:
                _PLANS.pop(old, None)


def _live_gate():
    """当前占用执行闸门的运行对象 + 它的归属计划（如果这把闸门是本模块的计划令牌）。

    返回 (run|None, plan|None)：run 一定属于"仍在跑"的占用者；
    plan 非 None 说明占用者是编排计划（可能是**已在收尾**的取消计划）。
    """
    run = agent_runner._busy_run()
    if run is None:
        return None, None
    with _PLAN_LOCK:
        for p in _PLANS.values():
            if p.token is run:
                return run, p
    return run, None


def _active_plan():
    """当前正在执行的编排计划：① 持有闸门的那个（含正在收尾的）；② 状态仍是 running 的"""
    _run, owner = _live_gate()
    if owner is not None:
        return owner
    with _PLAN_LOCK:
        items = list(_PLANS.values())
    for p in items:
        if p.status == "running":
            return p
    return None


def _busy_other():
    """占用执行闸门的**单任务**运行（编排计划自己的令牌不算）"""
    run, owner = _live_gate()
    return None if owner is not None else run


def _acquire_gate(plan: _Plan):
    """取闸门。

    注意：必须把"计划令牌仍活着但计划已标取消（正在收尾）"也当成忙 ——
    否则新计划会在旧计划的令牌还挂在 _ACTIVE 上时抢到闸门，导致互斥出现空洞。
    """
    run, owner = _live_gate()
    if owner is not None:
        return None, "已有编排计划在执行中（「%s」正在收尾）" % (owner.title or owner.id)
    if run is not None:
        return None, "已有单任务在执行中（task_id=%s，run_id=%s）" % (run.task_id, run.run_id)
    token = _PlanToken(plan.id, plan.title, plan.project_path, plan.timeout_sec)
    plan.token = token                 # 先挂在计划上，_live_gate 才能认出这把闸门的归属
    agent_runner._register(token)
    return token, ""


def _release_gate(plan: _Plan):
    token, plan.token = plan.token, None
    if token is not None:
        agent_runner._release(token)


# ---------------------------------------------------------------- Ollama 调用

def _ollama_base(url: str = "") -> str:
    u = str(url or "").strip() or OLLAMA_URL_DEFAULT
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    return u.rstrip("/")


def _ollama_tags(base: str, timeout: float = 4.0):
    """探测 Ollama 是否在线 + 已安装模型名。返回 (ok, models|err_text)"""
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=min(3.0, timeout), read=timeout,
                                                write=timeout, pool=timeout)) as c:
            r = c.get(_ollama_base(base) + "/api/tags")
            if r.status_code != 200:
                return False, "Ollama 返回 HTTP %s" % r.status_code
            data = r.json()
            models = [str(m.get("name") or "") for m in (data.get("models") or []) if isinstance(m, dict)]
            return True, models
    except Exception as e:  # noqa: BLE001 - 连不上就如实说连不上
        return False, "%s: %s" % (type(e).__name__, e)


def _ollama_chat(base: str, model: str, messages: list, timeout: int, plan: _Plan = None,
                 think=None):
    """直调 Ollama /api/chat（stream=false）。

    返回 (ok, content_or_err, meta)。meta 含 Ollama 自己上报的真实 token 计数
    （prompt_eval_count / eval_count —— 真实数据，仅作展示，不参与计费：本地推理成本恒为 ¥0）。

    `think=False`：本机 qwen3.5 是**思考型**模型，实测拆解这种结构化任务时会把 token 预算全花在
    reasoning 上，最终 `message.content` 为空（HTTP 仍是 200）——真机复现过（115.8s 后空内容），
    导致拆解白白降级到下一个模型。关掉思考后同一提示词 19.9s 返回完整 JSON 数组，可靠性明显更好。
    若该 Ollama/模型不接受 `think` 参数，会去掉该参数重试一次（不影响功能）。

    为了支持「停止」能真中断：把 client 挂在 plan 上，停止时 close() 会打断在途请求。
    """
    def _post(client, with_think: bool):
        body = {"model": model, "messages": messages, "stream": False, "keep_alive": "10m"}
        if with_think:
            body["think"] = False
        return client.post(_ollama_base(base) + "/api/chat", json=body)

    client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=float(timeout),
                                                write=60.0, pool=5.0))
    if plan is not None:
        with plan.lock:
            plan.active_client = client
    try:
        r = _post(client, think is False)
        if think is False and r.status_code != 200 and re.search(r"think", r.text or "", re.I):
            # 该模型/该版本 Ollama 不认识 think 参数 → 去掉重试一次
            r = _post(client, False)
        if r.status_code != 200:
            return False, "Ollama 返回 HTTP %s：%s" % (r.status_code, r.text[:300]), {}
        data = r.json()
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        content = str((msg or {}).get("content") or "")
        think_txt = str((msg or {}).get("thinking") or "")
        meta = {
            "model": data.get("model") or model,
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "thinking_chars": len(think_txt),
        }
        if not content.strip():
            if think_txt.strip():
                # 思考型模型把内容全放进了 reasoning：如实说明来源后拿来用（真实模型输出，不编造）
                meta["from_thinking"] = True
                return True, think_txt, meta
            return False, "Ollama 返回了空内容（模型 %s 无输出）" % model, meta
        return True, content, meta
    except Exception as e:  # noqa: BLE001 - 连接拒绝/超时/被停止打断都走这里
        return False, "%s: %s" % (type(e).__name__, e), {}
    finally:
        if plan is not None:
            with plan.lock:
                if plan.active_client is client:
                    plan.active_client = None
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 工人实现
# 统一接口：输入子任务 → 输出 {status, output, usage, local_tokens, changed_files, error, log_path}

def _codex_prompt(plan: _Plan, st: dict) -> str:
    n = len(plan.subtasks)
    return "\n".join([
        "# 子任务 %d/%d：%s" % (st["idx"] + 1, n, st["title"]),
        "",
        "## 大任务目标（你只负责上面这个子任务，不要越界做别的）",
        plan.goal or plan.title,
        "",
        "## 要做的事",
        st.get("detail") or st["title"],
        "",
        "## 工作目录",
        plan.project_path,
        "",
        "## 输出要求",
        "- 该改文件就改，能跑自测就跑；改了哪些文件请如实列出。",
        "- 最后用一段话说明这个子任务的结论与结果；做不到的部分如实说明，不要假装完成。",
    ])


def _extract_codex_output(lines: list, limit: int = OUTPUT_KEEP) -> str:
    """codex 的结论性输出 = JSONL 里 agent_message 事件（日志中带 🤖 前缀）"""
    out = [l[2:] for l in lines if l.startswith("🤖 ")]
    txt = "\n".join(out).strip()
    return txt[-limit:] if len(txt) > limit else txt


def _run_codex_worker(plan: _Plan, st: dict) -> dict:
    """codex 工人：复用 agent_runner 的单任务执行逻辑（真 codex exec + 真 usage + 真 taskkill）"""
    task_id = "orch-%s-%s" % (plan.id, st["idx"])
    try:
        run = agent_runner.Run(
            task_id=task_id, prompt=_codex_prompt(plan, st), project_path=plan.project_path,
            command_path=plan._codex_path,
            command_display=plan.codex_command or agent_runner.DEFAULT_COMMAND,
            timeout_sec=plan.timeout_sec, model=agent_runner._codex_model(),
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "output": "", "usage": None,
                "error": "无法创建 codex 运行对象：%s" % e}
    with plan.lock:
        plan.active_run = run
    try:
        # 注意：**不** agent_runner._register(run) —— 计划的锁令牌已占用执行闸门；
        # 注册会覆盖 _ACTIVE，导致第一个子任务结束后闸门被误释放。
        agent_runner._worker(run)      # 阻塞直到 codex 结束（含超时/被杀）
    finally:
        with plan.lock:
            if plan.active_run is run:
                plan.active_run = None

    snap = run.snapshot(0)
    lines = snap.get("lines") or []
    for l in lines:
        plan.log(st["idx"], l)
    output = _extract_codex_output(lines)
    changed = []
    for l in lines:
        m = re.match(r"^✎ 文件改动：(.+?)（(.+?)）$", l)
        if m and not any(c["path"] == m.group(1) for c in changed):
            changed.append({"path": m.group(1), "kind": m.group(2)})
    status = snap.get("status") or "failed"
    err = snap.get("error")
    if status == "completed" and not output:
        err = err or "codex 正常退出但没有返回文本结论（详见日志）"
    return {
        "status": status, "output": output, "usage": snap.get("usage"),
        "local_tokens": None, "changed_files": changed, "error": err,
        "log_path": snap.get("log_path") or "",
    }


def _run_ollama_worker(plan: _Plan, st: dict) -> dict:
    """ollama 工人：本地模型只做文字工作（总结/解释/改写/问答），成本恒为 ¥0"""
    model = plan.ollama_model or DECOMPOSE_MODELS[-1]
    content = "\n".join([
        "你正在协助完成一个大任务下面的一个子任务。",
        "",
        "## 大任务目标",
        plan.goal or plan.title,
        "",
        "## 你的子任务：%s" % st["title"],
        st.get("detail") or st["title"],
        "",
        "## 要求",
        "直接给出这个子任务的结果，不要复述任务，也不要只写“我将会做什么”。",
        "如果这个子任务需要读写文件或运行命令，而你只能输出文字，请明确说明这一点。",
    ])
    plan.log(st["idx"], "▶ 调用本地 Ollama：%s（模型 %s，stream=false）"
             % (_ollama_base(plan.ollama_url), model))
    ok, res, meta = _ollama_chat(plan.ollama_url, model, [{"role": "user", "content": content}],
                                 min(plan.timeout_sec, OLLAMA_CHAT_TIMEOUT), plan=plan, think=False)
    if not ok:
        return {"status": "failed", "output": "", "usage": None, "local_tokens": None,
                "error": "本地 Ollama 调用失败：%s" % res}
    if isinstance(meta, dict) and meta.get("from_thinking"):
        plan.log(st["idx"], "（该模型把内容全部放在了 reasoning 里，已如实取其思考文本作为输出）")
    for l in str(res).splitlines():
        plan.log(st["idx"], "🤖 " + l)
    local_tokens = None
    if isinstance(meta, dict) and (meta.get("prompt_eval_count") is not None
                                   or meta.get("eval_count") is not None):
        # Ollama 自己报的真实 token 计数（仅展示用；本地推理不产生费用，故不参与成本计算）
        local_tokens = {"prompt_eval_count": meta.get("prompt_eval_count"),
                        "eval_count": meta.get("eval_count"), "model": meta.get("model")}
        plan.log(st["idx"], "— 本地模型真实用量（仅供展示，不计费）：输入 %s / 输出 %s tokens"
                 % (meta.get("prompt_eval_count"), meta.get("eval_count")))
    return {"status": "completed", "output": str(res)[:OUTPUT_KEEP], "usage": None,
            "local_tokens": local_tokens, "changed_files": [], "error": None, "log_path": ""}


def _dispatch(plan: _Plan, st: dict) -> dict:
    if (st.get("effective_worker") or "codex") == "ollama":
        return _run_ollama_worker(plan, st)
    return _run_codex_worker(plan, st)


# ---------------------------------------------------------------- 队列执行

def _mark_canceled(plan: _Plan, st: dict, why: str):
    st["status"] = "canceled"
    st["error"] = why
    st["ended_at"] = st.get("ended_at") or _now()
    if st.get("elapsed_sec") is None:
        st["elapsed_sec"] = 0.0
    plan.save_sub(st)


def _plan_worker(plan: _Plan):
    """后台线程：严格串行跑完所有子任务（一个跑完，无论成败 → 下一个）"""
    try:
        if plan.status != "canceled":
            plan.status = "running"
        plan.started_at = _now()
        plan.ended_at = None
        plan.duration_sec = None
        plan.save_plan()
        plan.log(None, "—— 编排计划「%s」开始执行：%d 个子任务 · 工人池 %s · 失败策略 %s"
                 % (plan.title, len(plan.subtasks), plan.worker_pool,
                    "失败即停" if plan.fail_fast else "失败继续"))
        for st in plan.subtasks:
            if plan.stopped:
                _mark_canceled(plan, st, "计划已停止，未开始执行")
                continue
            if st["status"] not in ("pending", "canceled"):
                plan.log(None, "—— 跳过子任务 %d（状态已为 %s）" % (st["idx"] + 1, st["status"]))
                continue
            st["status"] = "running"
            st["started_at"] = _now()
            st["ended_at"] = None
            st["elapsed_sec"] = None
            st["error"] = ""
            plan.current_index = st["idx"]
            plan.save_sub(st)
            plan.log(st["idx"], "—— 子任务 %d/%d 开始 · 工人 %s · 超时 %ss"
                     % (st["idx"] + 1, len(plan.subtasks), st.get("effective_worker"), plan.timeout_sec))
            t0 = _now()
            try:
                res = _dispatch(plan, st)
            except Exception as e:  # noqa: BLE001 - 工人内部异常算这个子任务失败，不炸整个计划
                _logger.exception("orchestrator worker crashed")
                res = {"status": "failed", "output": "", "usage": None, "local_tokens": None,
                       "changed_files": [], "error": "工人执行异常：%s: %s" % (type(e).__name__, e)}
            res = res if isinstance(res, dict) else {}
            el = round(_now() - t0, 1)
            st["elapsed_sec"] = el
            st["ended_at"] = _now()
            if plan.stopped:
                # 用户已停止：这一条子任务无论工人报什么，真实结论就是"被取消"
                res["status"] = "canceled"
                res["error"] = res.get("error") or st.get("error") or "用户停止计划"
            st["status"] = res.get("status") if res.get("status") in SUB_STATUS else "failed"
            st["output"] = str(res.get("output") or "")[:OUTPUT_KEEP]
            st["error"] = str(res.get("error") or "")[:2000]
            st["usage"] = res.get("usage")
            st["local_tokens"] = res.get("local_tokens")
            st["changed_files"] = res.get("changed_files") or []
            st["log_path"] = res.get("log_path") or st.get("log_path") or ""
            # 成本：ollama 是真实的 0（本地推理无费用）；codex 交前端按 CostEngine 单价算，后端存 NULL
            st["cost_cny"] = 0.0 if st.get("effective_worker") == "ollama" else None
            plan.save_sub(st)
            plan.log(st["idx"], "—— 子任务 %d 结束：%s（耗时 %ss）"
                     % (st["idx"] + 1, st["status"], el))
            if plan.stopped:
                plan.status = "canceled"
                break
            if st["status"] != "completed" and plan.fail_fast:
                plan.fail_fast_tripped = True
                plan.status = "canceled"
                plan.note = "失败即停：子任务 %d「%s」%s，剩余子任务已取消" % (
                    st["idx"] + 1, st["title"], st["status"])
                plan.log(None, "—— %s" % plan.note)
                break
        # 收尾：没跑到的子任务落一个明确结论（不留看不懂的 pending）
        for st in plan.subtasks:
            if st["status"] == "pending":
                if plan.fail_fast_tripped:
                    _mark_canceled(plan, st, "失败即停：前序子任务未成功，本子任务未执行")
                elif plan.stopped:
                    _mark_canceled(plan, st, "计划已停止，未开始执行")
                else:
                    _mark_canceled(plan, st, "未执行（计划已结束）")
        if plan.stopped or plan.fail_fast_tripped:
            plan.status = "canceled"
        elif plan.status == "running":
            plan.status = "completed"
    except Exception as e:  # noqa: BLE001 - 计划线程绝不能静默死掉
        _logger.exception("orchestrator plan thread crashed")
        plan.status = "canceled"
        plan.note = (plan.note + " 计划线程异常：%s: %s" % (type(e).__name__, e)).strip()
    finally:
        plan.current_index = None
        plan.ended_at = _now()
        plan.duration_sec = round(plan.ended_at - (plan.started_at or plan.ended_at), 1)
        try:
            plan.save_plan()
        except Exception:  # noqa: BLE001
            pass
        done = sum(1 for s in plan.subtasks if s["status"] == "completed")
        failed = sum(1 for s in plan.subtasks if s["status"] in ("failed", "timeout"))
        canceled = sum(1 for s in plan.subtasks if s["status"] == "canceled")
        plan.log(None, "—— 计划结束：%s（完成 %d / 失败 %d / 取消 %d，总耗时 %ss）"
                 % (plan.status, done, failed, canceled, plan.duration_sec))
        _audit_plan(plan)
        _release_gate(plan)


def _audit_plan(plan: _Plan):
    try:
        from . import crud
        ok = sum(1 for s in plan.subtasks if s["status"] == "completed")
        failed = sum(1 for s in plan.subtasks if s["status"] in ("failed", "timeout"))
        detail = "%s · 完成 %d/失败 %d · 耗时 %ss" % (plan.status, ok, failed, plan.duration_sec)
        crud.append_agent_log(
            "orchestrator_plan",
            "plan=%s title=%s path=%s" % (plan.id, plan.title[:60], plan.project_path[:120]),
            True,
            {"completed": "ok", "canceled": "error", "interrupted": "error"}.get(plan.status, "error"),
            int((plan.duration_sec or 0) * 1000), detail[:200])
    except Exception as e:  # noqa: BLE001
        _logger.warning("orchestrator audit failed: %s", e)


# ---------------------------------------------------------------- 拆解

DECOMPOSE_SYSTEM = "你是任务拆解器。你只输出 JSON 数组，不输出任何解释、说明或 markdown 代码块。"


def _decompose_prompt(goal: str, project_path: str, max_n: int) -> str:
    return "\n".join([
        "把一个「大任务」拆成若干可以独立执行的子任务。",
        "",
        "## 大任务",
        goal,
        "",
        "## 项目路径",
        project_path or "（未指定）",
        "",
        "## 拆解规则",
        "1. 子任务按执行先后排序（后面的可以用前面的产出），彼此不重复。",
        "2. 每个子任务要具体、可验证；标题不超过 20 个字。",
        "3. worker 字段取值：codex（需要读写文件 / 跑命令 / 改代码）、"
        "ollama（只读材料做总结、解释、翻译、问答）、auto（不确定）。",
        "4. 最多 %d 个；任务本身很小就只拆 1-2 个，不要硬凑。" % max_n,
        "",
        "## 输出格式（只输出这个 JSON 数组，不要任何其它文字）",
        '[{"title":"短标题","detail":"具体做什么，1-3 句","worker":"auto"}]',
    ])


def _parse_subtask_array(text: str):
    """从模型输出里稳健地取出 JSON 数组。返回 (list|None, 失败原因)"""
    s = str(text or "").strip()
    if not s:
        return None, "模型返回空内容"
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    candidates = [s]
    i, j = s.find("["), s.rfind("]")
    if i >= 0 and j > i:
        candidates.append(s[i:j + 1])
    # 兜底：模型只吐了一个对象（有时受 format=json 影响）→ 若它长得像子任务就当 1 个元素的数组
    oi, oj = s.find("{"), s.rfind("}")
    if oi >= 0 and oj > oi:
        candidates.append("[" + s[oi:oj + 1] + "]")
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, dict):
            for key in ("subtasks", "tasks", "items", "list"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if isinstance(data, list):
            return data, ""
    return None, "模型输出不是合法 JSON 数组"


def _normalize_subtasks(raw_list: list) -> list:
    out = []
    for it in raw_list or []:
        if len(out) >= DECOMPOSE_MAX:
            break
        if isinstance(it, str):
            title, detail, worker = it.strip(), "", "auto"
        elif isinstance(it, dict):
            title = str(it.get("title") or it.get("name") or "").strip()
            detail = str(it.get("detail") or it.get("content") or it.get("desc") or "").strip()
            worker = _norm_worker(it.get("worker"))
        else:
            continue
        if not title and not detail:
            continue
        out.append({"title": (title or detail[:20]), "detail": detail, "worker": worker})
    return out


def _pick_decompose_model(base: str, requested: str):
    """挑拆解模型：优先请求指定 → DECOMPOSE_MODELS 顺序；在线时跳过未安装的模型（如实说明）"""
    ok, models = _ollama_tags(base)
    installed = set(models) if ok and isinstance(models, list) else None
    order = []
    for m in ([requested] if requested else []) + list(DECOMPOSE_MODELS):
        m = str(m or "").strip()
        if m and m not in order:
            order.append(m)
    if installed:
        filtered = [m for m in order if m in installed]
        if filtered:
            return filtered[0], "", order
        return order[0], ("Ollama 已安装模型里没有 %s（已安装：%s），仍按首选模型尝试"
                          % ("/".join(order), ", ".join(sorted(installed)[:8]))), order
    return order[0], "", order


@router.post("/api/orchestrator/decompose")
async def orchestrator_decompose(request: Request):
    """大任务 → 子任务列表（真实调用本地 Ollama，免费；失败返回明确错误 + 原始输出）"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象")
    goal = str(body.get("goal") or "").strip()
    if not goal:
        return _err(400, "bad_request", "缺少 goal（大任务描述为空，拒绝拆解）")
    base = _ollama_base(str(body.get("ollama_url") or ""))
    requested = str(body.get("model") or "").strip()
    project_path = str(body.get("project_path") or "").strip().strip('"')

    ok, models = _ollama_tags(base)
    if not ok:
        return _err(503, "ollama_unavailable",
                    "拆解模型不可用：连不上本地 Ollama（%s）。%s" % (base, models),
                    {"ollama_url": base})
    model, note, order = _pick_decompose_model(base, requested)

    attempts = []
    last_why = ""
    for m in order:
        t0 = _now()
        ok2, res, _meta = _ollama_chat(base, m, [
            {"role": "system", "content": DECOMPOSE_SYSTEM},
            {"role": "user", "content": _decompose_prompt(goal, project_path, DECOMPOSE_MAX)},
        ], DECOMPOSE_TIMEOUT, think=False)
        el = round(_now() - t0, 1)
        if not ok2:
            attempts.append({"model": m, "error": str(res), "elapsed_sec": el})
            last_why = str(res)
            continue
        arr, why = _parse_subtask_array(res)
        if arr is None:
            attempts.append({"model": m, "error": why, "raw": str(res)[:1500], "elapsed_sec": el})
            last_why = why
            continue
        subs = _normalize_subtasks(arr)
        if not subs:
            attempts.append({"model": m, "error": "模型返回了空数组（没有可用子任务）",
                             "raw": str(res)[:1500], "elapsed_sec": el})
            last_why = "模型返回了空数组"
            continue
        for s in subs:
            s["effective_worker"] = (recommend_worker(s["title"], s["detail"])
                                     if s["worker"] == "auto" else s["worker"])
        return {"ok": True, "data": {
            "goal": goal, "model": m, "model_note": note, "elapsed_sec": el,
            "subtasks": subs, "raw": str(res)[:4000], "attempts": attempts,
            "installed_models": models if isinstance(models, list) else [],
        }}
    return _err(502, "decompose_failed",
                "拆解失败：候选模型都没能给出合法的子任务 JSON（最后原因：%s）" % last_why,
                {"attempts": attempts, "ollama_url": base, "tried_models": order})


# ---------------------------------------------------------------- 工人可用性

def _codex_available(command: str):
    path, note = agent_runner._resolve_command(command)
    return bool(path), path, note


def _plan_requires(plan_subs: list, pool: str) -> set:
    return {_effective_worker(s, pool) for s in (plan_subs or [])}


def preflight_data(ollama_url: str = "", command: str = "") -> dict:
    ok, models = _ollama_tags(_ollama_base(ollama_url))
    cmd_ok, cmd_path, cmd_note = _codex_available(command)
    active = _active_plan()
    other = _busy_other()
    return {
        "codex": {"ok": cmd_ok, "path": cmd_path or "",
                  "command": (command or "").strip() or agent_runner.DEFAULT_COMMAND,
                  "note": cmd_note, "model": agent_runner._codex_model()},
        "ollama": {"ok": bool(ok), "url": _ollama_base(ollama_url),
                   "models": models if isinstance(models, list) else [],
                   "note": "" if ok else str(models),
                   "decompose_models": list(DECOMPOSE_MODELS)},
        "busy": bool(active or other),
        "active_plan_id": active.id if active else None,
        "active_plan_title": active.title if active else None,
        "active_task_id": other.task_id if other else None,
        "timeout_default": DEFAULT_TIMEOUT,
        "max_subtasks": MAX_SUBTASKS,
        "decompose_max": DECOMPOSE_MAX,
        "worker_pools": ["both", "codex", "ollama"],
        "build": config.BUILD,
    }


@router.get("/api/orchestrator/preflight")
def orchestrator_preflight(ollama_url: str = "", command: str = ""):
    """工人可用性 + 占用状态（前端据此禁用按钮并把原因写进 tooltip）"""
    return {"ok": True, "data": preflight_data(ollama_url, command)}


# ---------------------------------------------------------------- 计划 CRUD

def _annotate_stale(data: dict) -> dict:
    """从库里读出来的计划：若状态仍是 running 而进程里没有对应执行线程 → 如实标「应用重启导致中断」"""
    if data.get("status") != "running":
        return data
    data = dict(data)
    data["status"] = "interrupted"
    data["note"] = (str(data.get("note") or "") + " 应用重启导致中断：计划运行期间后端退出了。").strip()
    for s in data.get("subtasks") or []:
        if s.get("status") in ("running", "pending"):
            s["status"] = "canceled"
            s["error"] = s.get("error") or "应用重启导致中断（后端退出时该子任务未结束）"
    try:
        _save_plan_row(dict(data, status="interrupted"))
        for s in data.get("subtasks") or []:
            _save_subtask_row(data["id"], s)
    except Exception as e:  # noqa: BLE001
        _logger.warning("interrupted plan persist failed: %s", e)
    return data


def _get_live_or_db(plan_id: str):
    with _PLAN_LOCK:
        p = _PLANS.get(plan_id)
    if p is not None:
        return p, None
    data = load_plan_from_db(plan_id)
    if data is None:
        return None, None
    return None, _annotate_stale(data)


@router.get("/api/orchestrator/plans")
def orchestrator_plans(limit: int = 30):
    """计划列表（新→旧）。运行中的计划取内存实时态，其余从 SQLite 读（刷新/重启后仍在）"""
    try:
        n = max(1, min(int(limit or 30), 200))
    except (TypeError, ValueError):
        n = 30
    active = _active_plan()
    rows = list_plans_from_db(n)
    with _PLAN_LOCK:
        live = {p.id: p for p in _PLANS.values() if p.status == "running"}
    out = []
    for r in rows:
        p = live.get(r["id"])
        out.append(p.summary() if p is not None else _annotate_stale(r))
    if active is not None and all(x["id"] != active.id for x in out):
        out.insert(0, active.summary())
    return {"ok": True, "data": {"plans": out, "active_plan_id": active.id if active else None,
                                 "total": len(out)}}


@router.post("/api/orchestrator/plans")
async def orchestrator_create(request: Request):
    """创建计划（body 含拆解后的子任务列表；可同时带上 goal / 项目路径 / 工人池）"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象")
    title = str(body.get("title") or "").strip()
    goal = str(body.get("goal") or "").strip()
    if not title:
        return _err(400, "bad_request", "缺少 title（大任务名称）")
    raw_subs = body.get("subtasks")
    if not isinstance(raw_subs, list) or not raw_subs:
        return _err(400, "bad_request", "subtasks 必须是非空数组（先拆解或手工添加子任务）")
    if len(raw_subs) > MAX_SUBTASKS:
        return _err(400, "too_many_subtasks", "子任务最多 %d 个（当前 %d 个）" % (MAX_SUBTASKS, len(raw_subs)))
    pool = str(body.get("worker_pool") or "both").strip().lower()
    if pool not in ("both", "codex", "ollama"):
        return _err(400, "bad_request", "worker_pool 只能是 both / codex / ollama")
    raw_path = str(body.get("project_path") or "").strip().strip('"')
    project_path = ""
    if raw_path:
        p = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        if not p.exists():
            return _err(400, "path_not_found", "项目路径不存在：%s" % p)
        if not p.is_dir():
            return _err(400, "path_not_dir", "项目路径不是目录：%s" % p)
        project_path = str(p.resolve())
    pool_probe = []
    for it in raw_subs:
        if isinstance(it, dict):
            pool_probe.append({"title": it.get("title"), "detail": it.get("detail"),
                               "worker": it.get("worker")})
        elif isinstance(it, str):
            pool_probe.append({"title": it, "worker": "auto"})
    need = _plan_requires(pool_probe, pool)
    command = str(body.get("command") or "").strip()
    codex_path = ""
    if "codex" in need:
        codex_ok, codex_path, codex_note = _codex_available(command)
        if not codex_ok:
            return _err(400, "command_not_found", "Codex 工人不可用：%s" % codex_note)
        if not project_path:
            return _err(400, "bad_request",
                        "含 codex 工人的计划必须提供 project_path（本功能只在指定目录下干活）")

    subs = [_new_subtask(i, it if isinstance(it, dict) else {"title": str(it)}, pool)
            for i, it in enumerate(raw_subs)]
    for s in subs:
        if not s["title"]:
            s["title"] = s["detail"][:30] or "子任务 %d" % (s["idx"] + 1)
    plan = _Plan({
        "id": _new_id("p"), "title": title, "goal": goal, "project_path": project_path,
        "worker_pool": pool, "fail_fast": bool(body.get("fail_fast")),
        "timeout_sec": _clamp_timeout(body.get("timeout_sec")),
        "status": "pending", "decompose_model": str(body.get("decompose_model") or "")[:120],
        "codex_command": command or agent_runner.DEFAULT_COMMAND,
        "ollama_url": _ollama_base(str(body.get("ollama_url") or "")),
        "ollama_model": str(body.get("ollama_model") or "").strip(),
        "created_at": _now(), "subtasks": subs,
    })
    plan._codex_path = codex_path or ""
    plan.save_plan()
    for s in subs:
        plan.save_sub(s)
    _register_plan(plan)
    _logger.info("orchestrator plan created: %s (%d subtasks)", plan.id, len(subs))
    return {"ok": True, "data": plan.summary()}


@router.get("/api/orchestrator/plans/{plan_id}")
def orchestrator_detail(plan_id: str, offset: int = 0, log_index: int = -1, full: int = 0):
    """计划详情 + 子任务进度 + 当前子任务的增量日志（前端 1.5s 轮询；full=1 取完整输出）"""
    plan, stale = _get_live_or_db(plan_id)
    if plan is None and stale is None:
        return _err(404, "not_found", "计划不存在：%s" % plan_id)
    if plan is None:
        return {"ok": True, "data": dict(stale, log={"subtask_index": None, "lines": [],
                                                     "offset": 0, "total_lines": 0,
                                                     "dropped": 0, "truncated": False},
                                         live=False)}
    data = plan.summary(full=bool(full))
    idx = plan.current_index
    if idx is None and log_index is not None and int(log_index) >= 0:
        idx = int(log_index)          # 计划已结束：仍允许按指定子任务读它的日志
    if idx is None and int(log_index or 0) == _AUTO_INDEX:
        idx = _AUTO_INDEX             # 自动挑有内容的那个（-2）
    snap = plan.log_snapshot(idx, offset)
    snap["subtask_index"] = snap.get("index") if idx == _AUTO_INDEX else idx
    data["log"] = snap
    data["live"] = True
    return {"ok": True, "data": data}


@router.post("/api/orchestrator/plans/{plan_id}/run")
def orchestrator_run(plan_id: str):
    """启动串行队列：一个子任务跑完（无论成败）→ 下一个"""
    plan, stale = _get_live_or_db(plan_id)
    if plan is None and stale is None:
        return _err(404, "not_found", "计划不存在：%s" % plan_id)
    if plan is None:
        return _err(409, "not_running_state",
                    "该计划是从数据库恢复出来的（后端重启过），当前状态 %s，不能继续执行；请重新创建计划"
                    % stale.get("status"))
    with plan.lock:
        if plan.status == "running":
            return _err(409, "busy", "该计划已经在执行中")
        if plan.status not in ("pending", "canceled", "interrupted"):
            return _err(409, "not_running_state", "计划状态为 %s，不能重复执行；请新建计划" % plan.status)
        for s in plan.subtasks:
            if s["status"] in ("running", "completed"):
                return _err(409, "not_running_state",
                            "子任务 %d 已是 %s 状态：为避免重复执行同一子任务，请新建计划"
                            % (s["idx"] + 1, s["status"]))
        missing = []
        for w in sorted(_plan_requires(plan.subtasks, plan.worker_pool)):
            if w == "codex":
                ok, path, note = _codex_available(plan.codex_command)
                if ok:
                    plan._codex_path = path
                else:
                    missing.append("codex（%s）" % note)
            elif w == "ollama":
                ok, models = _ollama_tags(plan.ollama_url)
                if not ok:
                    missing.append("ollama（%s：%s）" % (plan.ollama_url, models))
        if missing:
            return _err(400, "worker_unavailable", "工人不可用：%s" % "；".join(missing))
        token, why = _acquire_gate(plan)
        if token is None:
            return _err(409, "busy", "%s：本产品单机串行，请先停止或等它跑完" % why)
        plan.stopped = False
        plan.status = "running"
        plan.started_at = _now()
        plan.ended_at = None
        plan.duration_sec = None
        plan.fail_fast_tripped = False
        plan.current_index = None
        plan.save_plan()
        t = threading.Thread(target=_plan_worker, args=(plan,), name="moray-orch-%s" % plan.id, daemon=True)
        plan.thread = t
        t.start()
    _logger.info("orchestrator plan started: %s", plan.id)
    return {"ok": True, "data": plan.summary()}


@router.post("/api/orchestrator/plans/{plan_id}/stop")
def orchestrator_stop(plan_id: str):
    """停止：杀当前子任务进程树 → 未开始的标 canceled → 计划 canceled

    这里**立刻**把当前与未开始的子任务标为 canceled 并落库（用户点停止后的真实语义就是"取消"），
    执行线程随后收尾时会把同一条子任务的结论收敛到同一个值（见 _plan_worker 的 plan.stopped 分支）。
    """
    plan, stale = _get_live_or_db(plan_id)
    if plan is None and stale is None:
        return _err(404, "not_found", "计划不存在：%s" % plan_id)
    if plan is None:
        return _err(409, "not_running_state", "该计划不在执行中（数据库状态：%s）" % stale.get("status"))
    with plan.lock:
        if plan.status != "running":
            return {"ok": True, "data": plan.summary()}
        plan.stopped = True
        plan.status = "canceled"
        run = plan.active_run
        client = plan.active_client
        cur = plan.current_index
        # 立即落状态：当前子任务 → canceled；还没开始的 → canceled（原因写明是"计划已停止"）
        for st in plan.subtasks:
            if st["status"] == "running":
                st["status"] = "canceled"
                st["error"] = "用户停止计划（进程树正在被终止）"
                st["ended_at"] = st.get("ended_at") or _now()
                if st.get("elapsed_sec") is None:
                    st["elapsed_sec"] = round(_now() - (st.get("started_at") or _now()), 1)
            elif st["status"] == "pending":
                st["status"] = "canceled"
                st["error"] = "计划已停止，未开始执行"
                st["ended_at"] = st.get("ended_at") or _now()
                st["elapsed_sec"] = 0.0
            plan.save_sub(st)
    plan.log(cur, "[MoRay] 用户点击「停止计划」")
    if run is not None:
        was_running = False
        with run._lock:
            if run.status == "running":
                run.status = "canceled"
                was_running = True
        if was_running:
            ok = run.kill_tree("用户停止（编排计划）")
            plan.log(cur, "[MoRay] taskkill 进程树：%s（pid %s）" % ("已发出并成功" if ok else "已发出（结果未确认）", run.pid()))
    if client is not None:
        # ollama 工人没有进程树：关掉在途 HTTP 连接来打断请求（真实中断，不假装）
        try:
            client.close()
            plan.log(cur, "[MoRay] 已关闭在途 Ollama 连接")
        except Exception as e:  # noqa: BLE001
            plan.log(cur, "[MoRay] 关闭 Ollama 连接异常：%s" % e)
    plan.save_plan()
    return {"ok": True, "data": plan.summary()}


# ---------------------------------------------------------------- 自检出口（自动化验证用）

def _selftest_reset(plan_id: str = ""):
    """测试专用：从内存注册表移除计划（不动数据库），便于同一进程内跑多场景"""
    with _PLAN_LOCK:
        if plan_id:
            _PLANS.pop(plan_id, None)
            if plan_id in _PLAN_ORDER:
                _PLAN_ORDER.remove(plan_id)
        else:
            _PLANS.clear()
            _PLAN_ORDER.clear()


__all__ = ["router", "recommend_worker", "preflight_data", "load_plan_from_db",
           "list_plans_from_db", "_selftest_reset", "DECOMPOSE_MODELS", "MAX_SUBTASKS"]
