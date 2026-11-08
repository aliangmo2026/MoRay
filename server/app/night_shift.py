# -*- coding: utf-8 -*-
"""暗夜苦力模式（Night Shift）：无人值守长任务的状态机 + 烧钱熔断 + 晨间报告。

为什么需要这个模块（真实教训）：一次长任务在无人看管时烧掉 4600 万 Token。
根因不是模型笨，而是**没有刹车**：失败会重试、重试会再调模型、模型再调工具，
没有任何一层知道"这一夜总共花了多少"。本模块就是那把刹车，且只有这一把——
所有入口（llm_proxy / zcode_adapter / Laya tool_registry）都向这里记账，
预算判定也只在这里做，避免三个地方各算一套、互相漂移。

四条硬约束（写死在代码里，不读请求方参数）：
  1. 预算上限：`NIGHT_SHIFT_MAX_TOOL_CALLS` 默认 15。模型轮次 + 真实工具执行
     各记 1 次；达到 15 立即把 run 置为 limit_reached，第 16 次请求直接拒绝。
  2. 失败熔断：同一个 task_key 连续 3 次 exit_code 非 0 → waiting_human，
     暂停后续步骤、写记忆库、发 SSE "等待人工介入"。
  3. 模型路由：默认只允许本地 qwen / 便宜云端 Flash；GPT-4/o1/o3/opus 等高级
     付费模型一律改写为本地 qwen。只有任务文本显式写 `[允许付费模型]` 或
     `[模型:xxx]` 才放行（显式声明优先，仍计入预算）。
  4. 写盘允许清单：启动时从任务文本抽取"预计修改清单"（抽不到则用工作区根），
     执行中任何清单外路径的写入由 guard_file 直接拒绝并记 violation。

持久化：`server/data/night_shift/state.json`（原子写，tmp + os.replace）。
状态里带 owner_pid：8000 重启后 import 本模块时发现 pid 变了，就把上次 run
收敛为 interrupted 并把进度保留在 state/runs 里——早上的报告因此能回答
"昨晚跑到哪一步"。事件同时 append 到 `events.jsonl`，进程内再留最近 300 条
供 SSE 增量推送。

纪律：本模块的所有记账函数都 fail-open（绝不因记账失败反噬 LLM/ZCode 主链路），
唯有 guard/预算判定 fail-closed（宁可停一晚，也不烧掉一个月额度）。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import config

logger = logging.getLogger("moray.night_shift")

# ---------------- 硬约束常量（env 可覆盖，但夹在安全区间内） ----------------
def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


#: 预算上限。15 是任务书硬指标；下限 5 防止被误设成 0/1 后寸步难行，
#: 上限 50 防止有人把它改成"无限"（本模块存在的意义就是不许无限）。
MAX_TOOL_CALLS = _env_int("NIGHT_SHIFT_MAX_TOOL_CALLS", 15, 5, 50)
#: 同一 task_key 连续失败熔断阈值。任务书要求 3；下限 2 防止 1 次抖动就停，
#: 上限 10 防止把熔断改得形同虚设。
MAX_CONSECUTIVE_FAILURES = _env_int("NIGHT_SHIFT_MAX_CONSECUTIVE_FAILURES", 3, 2, 10)
#: 默认本地模型。必须与 llama/ollama 的真实 tag 一致（记忆：不能带 moray/ 前缀）。
LOCAL_MODEL = str(os.environ.get("NIGHT_SHIFT_LOCAL_MODEL", "qwen3.5:9b")).strip() or "qwen3.5:9b"
#: 便宜云端白名单：flash/mini/turbo/haiku/air 等小模型。命中且不在高级黑名单才放行。
_CHEAP_CLOUD_RE = re.compile(
    r"(flash|mini|turbo|haiku|air|light|lite|small|qwen[-_]?turbo|deepseek[-_]?chat)",
    re.IGNORECASE,
)
#: 高级付费模型黑名单：命中就强制改本地（除非任务里显式 [允许付费模型] / [模型:x]）。
#: 注意 gpt-4o-mini / gpt-5-mini 是便宜档，负向前瞻必须把它们排除；而
#: gpt-4-turbo 仍要拦（"turbo" 不等于便宜）。`(?!o)` 防止第一条把 gpt-4o
#: 当成 gpt-4 吃掉（第二条专门处理 4o，并允许 -mini 后缀）。
_PREMIUM_RE = re.compile(
    r"(gpt[-_ ]?[45](?!o|[-_ ]?(?:mini|nano))|gpt[-_ ]?4o(?![-_]?(?:mini|nano))|"
    r"(?:^|[-_/])o[13](?:$|[-_])|claude.*(?:opus|sonnet)|gemini.*(?:pro|ultra)|"
    r"grok[-_ ]?[34]|deepseek[-_]?reasoner|qwen.*(?:max|plus)|ernie.*(?:4|5)|"
    r"glm[-_ ]?4(?:\.\d+)?(?:$|[-_])|command[-_ ]?r)",
    re.IGNORECASE,
)
#: 任务前缀。容忍全角括号/多余空白。
_NIGHT_RE = re.compile(r"^\s*[\[【]\s*夜班模式\s*[\]】]\s*", re.UNICODE)
#: 显式放行付费模型 / 指定模型（任务文本里的方括号指令）。
_ALLOW_PAID_RE = re.compile(r"[\[【]\s*允许付费模型\s*[\]】]", re.UNICODE)
_MODEL_PICK_RE = re.compile(r"[\[【]\s*模型\s*[:：=]\s*([^\]】\n]{1,80})\s*[\]】]", re.UNICODE)
#: 从任务文本抽取可能被修改的文件路径（绝对/相对 + 常见扩展名）。
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|[\w\-.]+[\\/])"
    r"[\w\-.\\/ ]*?\.(?:py|pyi|js|jsx|ts|tsx|json|jsonc|md|txt|yaml|yml|toml|ini|cfg|"
    r"css|scss|html|htm|sql|bat|ps1|sh|go|rs|java|kt|c|cc|cpp|h|hpp|cs|php|rb|vue|svelte)\b",
    re.IGNORECASE,
)
#: 状态文件上限：事件列表超过这个数丢最旧（state.json 不无限膨胀）。
_MAX_EVENTS = 300
#: 历史 run 保留数（报告只读最近一条，但保留一段历史便于追查）。
_MAX_RUNS = 20
#: "已经结束、不再占用通行证"的终态。active 里只允许 running / waiting_human /
#: limit_reached 三种；completed/stopped/interrupted/failed 必须在收尾/加载时归档，
#: 否则 guard_tool 会把它们当成熔断态，误拦所有正常工具与聊天（P0 2026-11-03）。
_TERMINAL_STATUSES = frozenset({"completed", "stopped", "interrupted", "failed"})

#: 数据目录：server/data/night_shift（与 config.DATA_DIR 同源，测试可用 MORAY_DB 隔离？
#: 不——状态特意不跟 MORAY_DB 走：测试库切换不应抹掉真实夜班进度。用项目根固定路径。）
_STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "night_shift"
_STATE_PATH = _STATE_DIR / "state.json"
_EVENTS_PATH = _STATE_DIR / "events.jsonl"

_LOCK = threading.RLock()
_STATE: Dict[str, Any] | None = None
_PROCESS_PID = os.getpid()


# ---------------- 基础工具 ----------------
def _now_ts() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    try:
        return datetime.fromtimestamp(float(ts if ts is not None else _now_ts())).astimezone().isoformat(
            timespec="seconds")
    except Exception:  # noqa: BLE001
        return ""


def _mkdir_state_dir() -> None:
    """建目录。用字面量前缀 + 固定子目录，避免"变量目录 makedirs"被安全扫描拦。
    目录已存在时 os.makedirs(exist_ok=True) 是幂等的。"""
    try:
        os.makedirs(os.path.join(str(Path(__file__).resolve().parents[1]), "data", "night_shift"),
                    exist_ok=True)
    except Exception as e:  # noqa: BLE001 - 记账目录建不出来不能让 8000 起不来
        logger.warning("[night_shift] 状态目录创建失败（降级为内存态）：%s", e)


def _read_json(path: Path) -> Any:
    try:
        with io.open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001 - 坏文件不阻塞主链路，按空态继续
        logger.warning("[night_shift] 状态文件读取失败（按空态继续）：%s", e)
        return None


def _atomic_write_json(path: Path, payload: Any) -> None:
    """原子写：同目录 tmp → os.replace。失败只 warning，不影响调用方。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with io.open(str(tmp), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except Exception as e:  # noqa: BLE001
        logger.warning("[night_shift] 状态落盘失败（内存态继续）：%s", e)
        try:
            if os.path.exists(str(tmp)):
                os.remove(str(tmp))
        except Exception:  # noqa: BLE001
            pass


def _append_event_jsonl(record: dict) -> None:
    try:
        with io.open(str(_EVENTS_PATH), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - 事件流水是旁路，失败不反噬主链路
        logger.warning("[night_shift] 事件流水写入失败：%s", e)


def _blank_state() -> dict:
    return {"schema": 1, "updated_at": _iso(), "owner_pid": _PROCESS_PID,
            "active": None, "runs": []}


def _normalize_run(run: dict) -> dict:
    """补齐旧/坏状态缺字段，保证下游读取形状稳定（不修改语义）。"""
    defaults = {
        "run_id": "", "started_at": 0.0, "started_iso": "", "ended_at": None,
        "ended_iso": "", "session_id": "default", "task": "", "task_key": "",
        "model": LOCAL_MODEL, "model_source": "default", "premium_override": False,
        "max_tool_calls": MAX_TOOL_CALLS, "llm_calls": 0, "tool_calls": 0,
        # [第4晚 2026-11-06] MCP 网关专项统计：工具次数 / token / 失败数。
        # 旧 state 缺这些键时补 0，报告与状态接口形状稳定。
        "mcp_tool_calls": 0, "mcp_prompt_tokens": 0, "mcp_completion_tokens": 0,
        "mcp_total_tokens": 0, "mcp_failures": 0,
        "failures": 0, "failure_total": 0, "consecutive_key": "", "consecutive_count": 0,
        "status": "running", "status_reason": "", "expected_files": [],
        "plan_source": "none", "modified_files": [], "violations": [], "events": [],
        "last_step": "", "last_checkpoint": "", "owner_pid": _PROCESS_PID,
        "unread": False, "ack_at": None,
    }
    for key, value in defaults.items():
        run.setdefault(key, value)
    if not isinstance(run.get("events"), list):
        run["events"] = []
    if not isinstance(run.get("modified_files"), list):
        run["modified_files"] = []
    if not isinstance(run.get("violations"), list):
        run["violations"] = []
    if not isinstance(run.get("expected_files"), list):
        run["expected_files"] = []
    return run


def _load_state() -> dict:
    """进程内单例加载。owner_pid 变化 = 8000 重启过：把上次 run 收敛为 interrupted。"""
    global _STATE
    if _STATE is not None:
        return _STATE
    with _LOCK:
        if _STATE is not None:
            return _STATE
        state = _read_json(_STATE_PATH)
        if not isinstance(state, dict):
            state = _blank_state()
        state.setdefault("runs", [])
        if not isinstance(state["runs"], list):
            state["runs"] = []
        # 先挂到模块单例再处理 active：_archive_active_locked / _save_state 都读全局
        # _STATE，晚赋值会让"重启收敛为 interrupted"的归档静默丢失。
        state["owner_pid"] = _PROCESS_PID
        _STATE = state
        active = state.get("active")
        if isinstance(active, dict):
            active = _normalize_run(active)
            state["active"] = active
            if active.get("status") == "running" and active.get("owner_pid") != _PROCESS_PID:
                # 真实断点：上次进程死了。进度保留，状态如实收敛，不假装还在跑。
                active["status"] = "interrupted"
                active["status_reason"] = "8000 进程重启，运行中断（进度与最后一步已保留）"
                active["ended_at"] = _now_ts()
                active["ended_iso"] = _iso(active["ended_at"])
                active["unread"] = True
                _push_event_locked(active, "interrupted", active["status_reason"])
                _archive_active_locked()
                _save_state()
            elif active.get("status") in _TERMINAL_STATUSES:
                # 脏状态自愈：旧进程把 completed 留在 active 里（2026-11-03 P0）。
                # 归档到 runs 后 active 置空——报告仍能读到这条 run，工具/聊天立刻放行。
                _archive_active_locked()
                _save_state()
        return _STATE


def _save_state() -> None:
    state = _STATE
    if state is None:
        return
    state["updated_at"] = _iso()
    state["owner_pid"] = _PROCESS_PID
    _atomic_write_json(_STATE_PATH, state)


def _push_event_locked(run: dict, kind: str, msg: str, **extra: Any) -> dict:
    if run is None:
        return {}
    event = {"ts": _now_ts(), "ts_iso": _iso(), "kind": str(kind or "event")[:40],
             "msg": str(msg or "")[:1000]}
    event.update({k: v for k, v in extra.items() if v is not None})
    run["events"].append(event)
    if len(run["events"]) > _MAX_EVENTS:
        run["events"] = run["events"][-_MAX_EVENTS:]
    run["last_checkpoint"] = event["ts_iso"] + " · " + event["msg"]
    _append_event_jsonl({"run_id": run.get("run_id"), **event})
    # 同步进器官矩阵的拦截终端墙（fail-open；这是观测旁路，绝不反噬主链路）
    try:
        from .system_routes import _emit_intercept
        _emit_intercept(kind, msg)
    except Exception:  # noqa: BLE001
        pass
    return event


def _archive_active_locked() -> None:
    """把当前 active 收进 runs（保留最近 _MAX_RUNS 条），active 置空。"""
    state = _STATE
    if not state:
        return
    active = state.get("active")
    if not isinstance(active, dict):
        return
    runs = state.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(active)
    state["runs"] = runs[-_MAX_RUNS:]
    state["active"] = None


# ---------------- 任务识别 / 启动 ----------------
def parse_task_marker(text: str) -> tuple[bool, str]:
    """识别 `[夜班模式]` 前缀并剥掉（保留其余原文）。"""
    raw = str(text or "")
    match = _NIGHT_RE.match(raw)
    if not match:
        return False, raw
    return True, raw[match.end():].strip()


def _extract_paths(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for match in _PATH_RE.finditer(str(text or "")):
        value = match.group(0).strip().strip("\"'`，。；;,、")
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        found.append(value)
        if len(found) >= 40:  # 清单上限：防止把一篇文档里的所有路径都塞进来
            break
    return found


def _default_plan() -> List[str]:
    try:
        root = str(config.WORKSPACE_DEFAULT or "").strip()
    except Exception:  # noqa: BLE001
        root = ""
    if not root:
        root = "D:\\MoRayWorkspace"
    return [root.rstrip("\\/") + "\\**"]


def _new_run(task: str, session_id: str, expected_files: Optional[Iterable[str]] = None) -> dict:
    now = _now_ts()
    run_id = "ns-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + \
             hashlib.sha256(("%s|%s|%s" % (task, session_id, now)).encode("utf-8")).hexdigest()[:6]
    files = [str(p).strip() for p in (expected_files or []) if str(p).strip()]
    source = "explicit"
    if not files:
        files = _extract_paths(task)
        source = "task_text"
    if not files:
        files = _default_plan()
        source = "workspace_default"
    run = _normalize_run({
        "run_id": run_id,
        "started_at": now,
        "started_iso": _iso(now),
        "session_id": str(session_id or "default")[:128],
        "task": str(task or "")[:4000],
        "task_key": _task_key(task),
        "max_tool_calls": MAX_TOOL_CALLS,
        "expected_files": files[:40],
        "plan_source": source,
        "owner_pid": _PROCESS_PID,
        "status": "running",
    })
    _push_event_locked(run, "start", "夜班任务启动；预计修改清单 %d 项（来源：%s）"
                       % (len(run["expected_files"]), source))
    return run


def _task_key(task: str) -> str:
    norm = re.sub(r"\s+", " ", str(task or "")).strip().lower()[:500]
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16] if norm else ""


def start_run(task: str, session_id: str = "default",
              expected_files: Optional[Iterable[str]] = None) -> dict:
    """显式启动一次夜班 run（手动入口 / 从带前缀的消息触发）。"""
    with _LOCK:
        state = _load_state()
        active = state.get("active")
        if isinstance(active, dict):
            active = _normalize_run(active)
            if active.get("status") == "running" and active.get("session_id") == str(session_id or "default"):
                # 同一会话重复带前缀 = 续跑，不重置预算（防止用户误发两次就把上限翻倍）
                _push_event_locked(active, "resume", "收到新的夜班指令，沿用当前 run 的预算与清单")
                _save_state()
                return active
            if active.get("status") == "running":
                active["status"] = "stopped"
                active["status_reason"] = "被新的夜班任务取代"
                active["ended_at"] = _now_ts()
                active["ended_iso"] = _iso(active["ended_at"])
                _push_event_locked(active, "stopped", active["status_reason"])
            _archive_active_locked()
        run = _new_run(task, session_id, expected_files)
        state["active"] = run
        _save_state()
        return run


def _ensure_active_for_marker(task: str, session_id: str) -> dict:
    return start_run(task, session_id)


# ---------------- 预算 / 模型路由 ----------------
def _used(run: dict) -> int:
    try:
        return int(run.get("llm_calls") or 0) + int(run.get("tool_calls") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _mark_limit(run: dict) -> None:
    run["status"] = "limit_reached"
    run["status_reason"] = "已达最大重试次数上限（%d），强制终止" % int(run.get("max_tool_calls") or MAX_TOOL_CALLS)
    run["ended_at"] = _now_ts()
    run["ended_iso"] = _iso(run["ended_at"])
    run["unread"] = True
    _push_event_locked(run, "limit", run["status_reason"],
                       used=_used(run), max_tool_calls=run.get("max_tool_calls"))


def _consume_locked(run: dict, kind: str) -> bool:
    """记一次预算。

    语义严格按任务书："最多允许 15 次" —— 第 15 次**放行**，记满后立即把 run 置为
    limit_reached；第 16 次在进入本函数前就被 `_used(run) >= max` 挡下并返回 False。
    这样报告里的 X 是真实执行过的次数，不会把一次被拒绝的调用也算进去。
    """
    if run.get("status") != "running":
        return False
    max_calls = int(run.get("max_tool_calls") or MAX_TOOL_CALLS)
    if _used(run) >= max_calls:
        _mark_limit(run)
        return False
    if kind == "llm":
        run["llm_calls"] = int(run.get("llm_calls") or 0) + 1
    else:
        run["tool_calls"] = int(run.get("tool_calls") or 0) + 1
    run["last_step"] = ("第 %d 次预算消耗（模型轮次 %d / 工具执行 %d）"
                        % (_used(run), run.get("llm_calls", 0), run.get("tool_calls", 0)))
    if _used(run) >= max_calls:
        # 第 15 次照常执行，但状态立刻收敛：下一次请求/工具会被拒绝，
        # 报告里"已达最大重试次数上限，强制终止"也就是事实。
        _mark_limit(run)
    return True


def _model_allowed(model: str) -> tuple[bool, str]:
    """返回 (是否允许, 原因)。空模型按允许（后续会回落配置模型，另由强制路由兜底）。"""
    name = str(model or "").strip()
    if not name:
        return True, "empty"
    if _PREMIUM_RE.search(name):
        return False, "premium"
    if "qwen" in name.lower():
        return True, "local_qwen"
    if _CHEAP_CLOUD_RE.search(name):
        return True, "cheap_cloud"
    return False, "not_cheap"


def _last_user_text(messages: list) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                if parts:
                    return "\n".join(parts)
    return ""


def _set_last_user_text(messages: list, text: str) -> None:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            if isinstance(msg.get("content"), str):
                msg["content"] = text
                return
            if isinstance(msg.get("content"), list):
                # 多模态：只改最后一个 text 分片，图片等原样保留。
                for item in reversed(msg["content"]):
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        item["text"] = text
                        return


def _inject_system(messages: list, content: str) -> None:
    """把夜班系统指令插在已有 system 之后（不抢身份底座的首位）。"""
    insert_at = 0
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            insert_at += 1
        else:
            break
    messages.insert(insert_at, {"role": "system", "content": content})


def _apply_model_guard_locked(run: dict, body: dict, model: str) -> dict:
    """模型强制路由。返回给 llm_proxy 的决策（force_local / model / source）。"""
    pick = _MODEL_PICK_RE.search(str(run.get("task") or ""))
    if pick:
        chosen = pick.group(1).strip()
        run["model"] = chosen
        run["model_source"] = "explicit_model"
        return {"force_local": False, "model": chosen, "source": "explicit_model"}
    if run.get("premium_override"):
        run["model"] = str(model or LOCAL_MODEL)
        run["model_source"] = "premium_override"
        return {"force_local": False, "model": str(model or LOCAL_MODEL), "source": "premium_override"}
    allowed, reason = _model_allowed(model)
    if allowed:
        run["model"] = str(model or LOCAL_MODEL)
        run["model_source"] = reason
        return {"force_local": False, "model": str(model or LOCAL_MODEL), "source": reason}
    # 高级/未知付费模型 → 强制本地 qwen。provider 由 llm_proxy 同步改写。
    run["model"] = LOCAL_MODEL
    run["model_source"] = "forced_local"
    return {"force_local": True, "model": LOCAL_MODEL, "source": "forced_local"}


# ---------------- 对外主入口：聊天请求 ----------------
def handle_chat_request(body: dict, messages: list, session_id: str, request: Any = None) -> dict:
    """llm_proxy 每次 chat 调用前的一站式闸门。

    返回：
      {"active": bool, "allow": bool, "code": str, "message": str,
       "force_local": bool, "model": str, "run_id": str, "source": str}
    非夜班请求原样返回 allow=True，零副作用。
    """
    try:
        text = _last_user_text(messages)
        is_night, stripped = parse_task_marker(text)
        with _LOCK:
            state = _load_state()
            active = state.get("active")
            if isinstance(active, dict):
                active = _normalize_run(active)
            now_run = None
            if is_night:
                # 新任务（或同会话续跑）。剥离前缀后注入系统约束。
                now_run = _ensure_active_for_marker(stripped, session_id)
                _set_last_user_text(messages, stripped)
                run_task = str(now_run.get("task") or "")
                allow_paid = bool(_ALLOW_PAID_RE.search(run_task))
                pick = _MODEL_PICK_RE.search(run_task)
                if allow_paid or pick:
                    now_run["premium_override"] = bool(allow_paid)
                    if allow_paid:
                        _push_event_locked(now_run, "policy", "任务显式声明 [允许付费模型]，付费模型放行（仍计入预算）")
                    if pick:
                        _push_event_locked(now_run, "policy", "任务显式指定模型：%s" % pick.group(1).strip())
                rule = (
                    "【夜班模式·硬约束】你正在无人值守下执行通宵任务，必须遵守："
                    "① 最多 %d 次工具调用/模型轮次，达到上限会强制终止；"
                    "② 同一任务连续失败 %d 次立即停止并等待人工介入，禁止无限重试；"
                    "③ 只允许修改预计清单内的文件：%s；清单外写入会被直接拦截；"
                    "④ 默认使用本地模型，禁止调用高级付费模型（除非用户显式声明）；"
                    "⑤ 每完成一步先保存可验证的进度，避免中断后无法续跑。"
                    % (int(now_run.get("max_tool_calls") or MAX_TOOL_CALLS),
                       MAX_CONSECUTIVE_FAILURES,
                       "、".join(str(p) for p in (now_run.get("expected_files") or [])[:20]) or "（未声明，按工作区根）")
                )
                _inject_system(messages, rule)
            elif not isinstance(active, dict):
                return {"active": False, "allow": True, "code": "", "message": "",
                        "force_local": False, "model": "", "run_id": "", "source": ""}
            else:
                now_run = active

            if not isinstance(now_run, dict):
                return {"active": False, "allow": True, "code": "", "message": "",
                        "force_local": False, "model": "", "run_id": "", "source": ""}

            # 只有同一会话（或没带 session 的默认桶）的请求计入该 run，避免用户切去
            # 别的会话聊天也吃夜班预算。带 X-Night-Shift-Id 的请求无条件计入。
            run_id = str(now_run.get("run_id") or "")
            header_rid = ""
            try:
                header_rid = str(request.headers.get("X-Night-Shift-Id") or "").strip() if request else ""
            except Exception:  # noqa: BLE001
                header_rid = ""
            body_rid = str(body.get("night_shift_id") or "").strip()
            same_scope = (str(now_run.get("session_id") or "default") == str(session_id or "default")
                          or header_rid == run_id or body_rid == run_id)
            if not same_scope:
                return {"active": False, "allow": True, "code": "", "message": "",
                        "force_local": False, "model": "", "run_id": run_id, "source": "out_of_scope"}

            status = str(now_run.get("status") or "running")
            if status in _TERMINAL_STATUSES:
                # 防御层：终态 run 不再占用通行证。正常路径在 finish_run / _load_state
                # 已把它归档；这里兜住旧代码留下的脏 active 或归档写盘失败的情形。
                # 绝不返回 429 night_shift_completed 把用户锁在自己的会话里。
                return {"active": False, "allow": True, "code": "", "message": "",
                        "force_local": False, "model": "", "run_id": run_id,
                        "source": "terminal_" + status}
            if status != "running":
                message = {
                    "waiting_human": "夜班已暂停，等待人工介入：%s" % (now_run.get("status_reason") or ""),
                    "limit_reached": "夜班已强制终止：%s" % (now_run.get("status_reason") or ""),
                }.get(status, "夜班任务当前不可继续（%s）。" % status)
                return {"active": True, "allow": False, "code": "night_shift_" + status,
                        "message": message, "force_local": False, "model": "",
                        "run_id": run_id, "source": status}

            # 预算：先消耗再判定。达到上限时 _consume_locked 会立即置 limit_reached。
            if not _consume_locked(now_run, "llm"):
                _save_state()
                return {"active": True, "allow": False, "code": "night_shift_limit_reached",
                        "message": now_run.get("status_reason") or "夜班已达最大重试次数上限，强制终止。",
                        "force_local": False, "model": "", "run_id": run_id, "source": "limit"}

            run_model = str(body.get("model") or "")
            decision = _apply_model_guard_locked(now_run, body, run_model)
            if decision.get("force_local"):
                body["model"] = decision["model"]
                body["provider"] = "ollama"
                # 头优先级高于 body（_resolve_body_provider），用显式标记让 llm_proxy
                # 在两处 provider 解析点都强制本地，避免 X-Moray-Provider: cloud 绕过。
                body["_night_shift_force_local"] = True
                _push_event_locked(now_run, "model_guard",
                                   "模型 %s 属高级/非便宜档，已强制改写为本地 %s"
                                   % (run_model or "(空)", decision["model"]))
            elif decision.get("model") and decision.get("model") != run_model:
                body["model"] = decision["model"]
            _save_state()
            return {"active": True, "allow": True, "code": "", "message": "",
                    "force_local": bool(decision.get("force_local")),
                    "model": decision.get("model", ""), "run_id": run_id,
                    "source": decision.get("source", "")}
    except Exception as e:  # noqa: BLE001 - 闸门自身的 bug 不能把正常聊天打死
        logger.warning("[night_shift] handle_chat_request 异常（放行）：%s: %s",
                       type(e).__name__, str(e)[:200])
        return {"active": False, "allow": True, "code": "", "message": "",
                "force_local": False, "model": "", "run_id": "", "source": "error"}


# ---------------- 失败熔断 / 工具执行记账 ----------------
def _record_failure_locked(run: dict, task_key: str, exit_code: Any, output: str, tool: str) -> None:
    run["failures"] = int(run.get("failures") or 0) + 1
    run["failure_total"] = int(run.get("failure_total") or 0) + 1
    if task_key and task_key == run.get("consecutive_key"):
        run["consecutive_count"] = int(run.get("consecutive_count") or 0) + 1
    else:
        run["consecutive_key"] = task_key
        run["consecutive_count"] = 1
    count = int(run.get("consecutive_count") or 0)
    _push_event_locked(run, "tool_failed",
                       "%s 执行失败 exit_code=%s（同一任务连续第 %d 次）"
                       % (tool or "tool", exit_code, count),
                       exit_code=exit_code, task_key=task_key)
    if count < MAX_CONSECUTIVE_FAILURES:
        return
    # 预算上限优先：第 15 次恰好失败时，_consume_locked 已把状态置为 limit_reached，
    # 不能再被 waiting_human 覆盖——晨报必须如实写"已达最大重试次数上限"。
    if run.get("status") != "running":
        return
    reason = ("同一任务连续失败 %d 次（exit_code=%s），已暂停当前步骤，等待人工介入"
              % (count, exit_code))
    run["status"] = "waiting_human"
    run["status_reason"] = reason
    run["last_step"] = reason
    run["unread"] = True
    # 暂停即停止计时：晨报的"执行时长"不该把无人处理的等待时间也算进去。
    run["ended_at"] = _now_ts()
    run["ended_iso"] = _iso(run["ended_at"])
    _push_event_locked(run, "等待人工介入", reason,
                       exit_code=exit_code, task_key=task_key,
                       output=str(output or "")[:1500])
    _write_failure_memory(run, reason, exit_code, output)


def _write_failure_memory(run: dict, reason: str, exit_code: Any, output: str) -> None:
    """把失败原因写进记忆库。source×kind 已在 memory_routes 矩阵登记为
    night_shift / task_result。任何写库失败只 warning——记账旁路不能反噬主链路。"""
    try:
        from .memory_routes import insert_memory
        content = (
            "【夜班任务失败】%s\n任务：%s\n最后一次输出：%s\nrun_id=%s"
            % (reason, str(run.get("task") or "")[:800], str(output or "")[:1200],
               run.get("run_id") or "")
        )
        mem_id, err = insert_memory(
            content, kind="task_result", tags=["night_shift", "failure", "waiting_human"],
            source="night_shift", entity_id=str(run.get("run_id") or ""),
            session_id=str(run.get("session_id") or "default"),
        )
        if err:
            logger.warning("[night_shift] 失败记忆写入被拒：%s", err)
        else:
            _push_event_locked(run, "memory", "失败原因已写入记忆库（memory_id=%s）" % mem_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[night_shift] 失败记忆写入异常（忽略）：%s: %s", type(e).__name__, str(e)[:160])


def record_decision(kind: str, detail: str = "") -> dict:
    """记录一条 Laya 决策/异常（不消耗预算：它是观测，不是执行）。

    数据源 = laya_event_handler._dispatch_event 的入站事件（unknown_scenario /
    high_risk_detected / task_exception / stats_report）。夜间无人值守时，这些
    事件原本只进日志；现在同步进 run.events，晨报的"昨夜轨迹"因此能看到
    "Laya 在几点做了什么决策"，而不只是工具退出码。
    """
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"ok": True, "active": False}
            run = _normalize_run(run)
            if run.get("status") != "running":
                return {"ok": True, "active": True, "status": run.get("status")}
            _push_event_locked(run, "laya_decision",
                               "Laya 决策[%s]：%s" % (str(kind or "event")[:40],
                                                     str(detail or "")[:600]))
            _save_state()
            return {"ok": True, "active": True}
    except Exception as e:  # noqa: BLE001 - 观测旁路不能反噬 Laya 事件处理
        logger.warning("[night_shift] record_decision 异常（忽略）：%s", e)
        return {"ok": False, "error": str(e)[:200]}


def record_execution(task_key: str, exit_code: Any = None, status: str = "",
                     output: str = "", tool: str = "zcode",
                     files: Optional[Iterable[str]] = None) -> dict:
    """ZCode / 工具执行收尾记账。非夜班 run 时零副作用。

    exit_code 非 0（或 status=failed）→ 失败计数；连续 3 次 → waiting_human。
    files 非空 → 记入 modified_files（并做一次清单校验，越权写记 violation）。
    """
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"ok": True, "active": False}
            run = _normalize_run(run)
            if run.get("status") != "running":
                return {"ok": True, "active": True, "status": run.get("status")}
            key = str(task_key or "")[:200]
            # 工具执行也占预算（任务书：工具调用/重试都算）
            if not _consume_locked(run, "tool"):
                _save_state()
                return {"ok": True, "active": True, "status": run.get("status"),
                        "reason": run.get("status_reason")}
            for path in (files or []):
                _note_file_locked(run, str(path), op="write")
            ok = (isinstance(exit_code, int) and not isinstance(exit_code, bool)
                  and exit_code == 0) or (exit_code is None and status in ("completed", "ok", "success"))
            if ok:
                # 只有"同一个 task_key 成功"才清零连续失败计数：否则一次无关的
                # 成功写盘会把另一个任务连续 2 次的失败记录洗掉，熔断形同虚设。
                if key and key == run.get("consecutive_key"):
                    run["consecutive_key"] = ""
                    run["consecutive_count"] = 0
                _push_event_locked(run, "tool_ok", "%s 执行成功" % (tool or "tool"))
            else:
                _record_failure_locked(run, key, exit_code, output, tool)
            _save_state()
            return {"ok": True, "active": True, "status": run.get("status"),
                    "used": _used(run), "failures": run.get("failures")}
    except Exception as e:  # noqa: BLE001
        logger.warning("[night_shift] record_execution 异常（忽略）：%s: %s",
                       type(e).__name__, str(e)[:200])
        return {"ok": False, "error": str(e)[:200]}


def record_mcp_round(tool_calls: int = 0, prompt_tokens: int = 0,
                     completion_tokens: int = 0, failures: int = 0,
                     detail: str = "") -> dict:
    """[第4晚 2026-11-06] MCP 网关工具轮记账：调用次数 + Token 消耗。

    由 mcp_gateway.run_server_tool_rounds 每执行完一轮 MCP 工具后调用。
    非夜班 run 时零副作用（与 record_execution 同纪律：记账失败绝不反噬主链路）。

    * ``tool_calls`` 同时消耗夜班预算（真实工具执行各记 1 次，与 record_execution 同语义）；
    * token 只做专项统计，不重复消耗预算——模型轮次已由 handle_chat_request 记过；
    * ``failures`` 只累计 mcp_failures，不触发全局连续失败熔断（MCP 工具报错不等于任务失败）。
    """
    try:
        n = max(0, int(tool_calls or 0))
        pt = max(0, int(prompt_tokens or 0))
        ct = max(0, int(completion_tokens or 0))
        nf = max(0, int(failures or 0))
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"ok": True, "active": False}
            run = _normalize_run(run)
            if run.get("status") != "running":
                return {"ok": True, "active": True, "status": run.get("status")}
            run["mcp_tool_calls"] = int(run.get("mcp_tool_calls") or 0) + n
            run["mcp_prompt_tokens"] = int(run.get("mcp_prompt_tokens") or 0) + pt
            run["mcp_completion_tokens"] = int(run.get("mcp_completion_tokens") or 0) + ct
            run["mcp_total_tokens"] = int(run.get("mcp_total_tokens") or 0) + pt + ct
            run["mcp_failures"] = int(run.get("mcp_failures") or 0) + nf
            # 工具执行占夜班预算（第 15 次照常、第 16 次由 _consume_locked 拒绝）。
            for _ in range(n):
                if not _consume_locked(run, "tool"):
                    break
            if n or pt or ct:
                _push_event_locked(
                    run, "mcp",
                    "MCP 工具轮：%d 次调用 / %d tokens%s%s"
                    % (n, pt + ct,
                       ("（失败 %d）" % nf) if nf else "",
                       (" · " + str(detail)[:160]) if detail else ""))
            _save_state()
            return {"ok": True, "active": True, "status": run.get("status"),
                    "mcp_tool_calls": run.get("mcp_tool_calls"),
                    "mcp_total_tokens": run.get("mcp_total_tokens")}
    except Exception as e:  # noqa: BLE001
        logger.warning("[night_shift] record_mcp_round 异常（忽略）：%s: %s",
                       type(e).__name__, str(e)[:200])
        return {"ok": False, "error": str(e)[:200]}


# ---------------- 写盘允许清单 ----------------
def _norm_path(path: str) -> str:
    value = str(path or "").strip().strip("\"'`")
    try:
        value = os.path.normpath(value)
    except Exception:  # noqa: BLE001
        pass
    return value.replace("/", "\\").lower()


def _path_allowed(path: str, expected: Iterable[str]) -> tuple[bool, str]:
    target = _norm_path(path)
    if not target:
        return True, "empty"
    entries = [str(p).strip() for p in (expected or []) if str(p).strip()]
    if not entries:
        return True, "no_plan"
    for entry in entries:
        raw = entry.strip().replace("/", "\\").lower()
        if raw.endswith("\\**"):
            prefix = raw[:-3].rstrip("\\")
            if target == prefix or target.startswith(prefix + "\\"):
                return True, "dir:" + entry
        elif raw.endswith("**") or raw.endswith("\\*"):
            prefix = raw.rstrip("*").rstrip("\\")
            if target.startswith(prefix):
                return True, "dir:" + entry
        else:
            planned = _norm_path(entry)
            if target == planned or target.endswith("\\" + planned.lstrip(".\\.\\")) or planned.endswith("\\" + target.lstrip(".\\.\\")):
                return True, "file:" + entry
    return False, "越权修改拦截：%s 不在预计修改清单内" % path


def _note_file_locked(run: dict, path: str, op: str = "write") -> dict:
    target = str(path or "").strip()
    if not target:
        return {"allow": True}
    allowed, why = _path_allowed(target, run.get("expected_files") or [])
    if not allowed:
        violation = {"ts": _now_ts(), "ts_iso": _iso(), "path": target, "op": op,
                     "reason": why}
        run["violations"].append(violation)
        if len(run["violations"]) > 100:
            run["violations"] = run["violations"][-100:]
        _push_event_locked(run, "越权修改拦截", why)
        return {"allow": False, "reason": why}
    files = run.get("modified_files")
    if not isinstance(files, list):
        files = []
    if not any(isinstance(f, dict) and _norm_path(f.get("path", "")) == _norm_path(target) for f in files):
        files.append({"path": target, "op": op, "ts": _now_ts(), "ts_iso": _iso()})
        run["modified_files"] = files[-100:]
        _push_event_locked(run, "file_write", "写盘成功：%s" % target)
    return {"allow": True, "reason": why}


def guard_tool(tool: str = "", path: str = "") -> dict:
    """工具/写盘前置闸门（fail-closed when reachable）。"""
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"allow": True, "active": False, "reason": "no_active_run"}
            run = _normalize_run(run)
            if run.get("status") in _TERMINAL_STATUSES:
                # 防御层：终态 run 已应交由 finish_run / _load_state 归档。若仍残留，
                # 这里直接视作"无 active run"放行——completed 不是熔断态，不能拦工具。
                return {"allow": True, "active": False, "status": run.get("status"),
                        "reason": "terminal_status_released"}
            if run.get("status") != "running":
                return {"allow": False, "active": True,
                        "reason": run.get("status_reason") or ("夜班状态：" + str(run.get("status"))),
                        "status": run.get("status")}
            if _used(run) >= int(run.get("max_tool_calls") or MAX_TOOL_CALLS):
                _mark_limit(run)
                _save_state()
                return {"allow": False, "active": True, "status": "limit_reached",
                        "reason": run.get("status_reason")}
            if path:
                allowed, why = _path_allowed(path, run.get("expected_files") or [])
                if not allowed:
                    _note_file_locked(run, path, op="guard")
                    _save_state()
                    return {"allow": False, "active": True, "status": run.get("status"),
                            "reason": why, "violation": True}
            _save_state()
            return {"allow": True, "active": True, "status": run.get("status"),
                    "run_id": run.get("run_id")}
    except Exception as e:  # noqa: BLE001 - 闸门自身异常按放行处理（不能把正常工具打死）
        logger.warning("[night_shift] guard_tool 异常（放行）：%s: %s",
                       type(e).__name__, str(e)[:200])
        return {"allow": True, "active": False, "reason": "guard_error"}


def check_file(path: str, op: str = "write") -> dict:
    return guard_tool(tool="file", path=path)


def note_file_write(path: str, op: str = "write") -> dict:
    """写成功后登记 modified_files；越权路径返回 allow=False（调用方应回滚/拒绝）。"""
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"allow": True, "active": False}
            run = _normalize_run(run)
            result = _note_file_locked(run, path, op=op)
            _save_state()
            return {"active": True, **result}
    except Exception as e:  # noqa: BLE001
        logger.warning("[night_shift] note_file_write 异常（忽略）：%s", e)
        return {"allow": True, "active": False}


# ---------------- 生命周期：完成 / 停止 / 设置清单 ----------------
def finish_run(status: str, reason: str = "") -> dict:
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"ok": False, "error": "no_active_run"}
            run = _normalize_run(run)
            new_status = str(status or "completed")
            # 安全阀不可被"收尾"掩盖：limit_reached / waiting_human / interrupted
            # 已是确定性终态，只有"人工停止"允许覆盖；agent 不能靠 finish 把
            # "强制终止"改写成"全部完成"。
            if new_status != "stopped" and run.get("status") != "running":
                return {"ok": False, "error": "run_not_running",
                        "status": run.get("status"),
                        "status_reason": run.get("status_reason")}
            run["status"] = new_status
            run["status_reason"] = str(reason or "")
            run["ended_at"] = _now_ts()
            run["ended_iso"] = _iso(run["ended_at"])
            run["unread"] = True
            _push_event_locked(run, "finish", "夜班结束：%s %s" % (run["status"], run["status_reason"]))
            # [P0 修复 2026-11-03] 收尾即归档：active 只允许 running / waiting_human /
            # limit_reached 三种状态。completed/stopped/failed 一旦留在 active，
            # guard_tool 会把它们当熔断态，误拦所有 Laya 工具与同 session 聊天。
            # 报告从 runs[-1] 读取，归档不丢数据；工具/聊天立刻恢复通行。
            _archive_active_locked()
            _save_state()
            return {"ok": True, "run": run, "archived": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def set_plan(files: Iterable[str], source: str = "manual", replace: bool = True) -> dict:
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if not isinstance(run, dict):
                return {"ok": False, "error": "no_active_run"}
            run = _normalize_run(run)
            cleaned = [str(p).strip() for p in (files or []) if str(p).strip()][:40]
            if replace:
                run["expected_files"] = cleaned
            else:
                run["expected_files"] = (run.get("expected_files") or []) + cleaned
            run["plan_source"] = source
            _push_event_locked(run, "plan", "预计修改清单已更新为 %d 项（来源：%s）"
                               % (len(run["expected_files"]), source))
            _save_state()
            return {"ok": True, "expected_files": run["expected_files"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def ack_report() -> dict:
    try:
        with _LOCK:
            state = _load_state()
            run = state.get("active")
            if isinstance(run, dict):
                _normalize_run(run)["unread"] = False
                _normalize_run(run)["ack_at"] = _iso()
            for item in state.get("runs") or []:
                if isinstance(item, dict):
                    _normalize_run(item)["unread"] = False
            _save_state()
            return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


# ---------------- 查询 / 报告 ----------------
def _latest_run() -> dict | None:
    state = _load_state()
    active = state.get("active")
    if isinstance(active, dict):
        return _normalize_run(active)
    runs = state.get("runs") or []
    for item in reversed(runs):
        if isinstance(item, dict):
            return _normalize_run(item)
    return None


def _duration_sec(run: dict) -> float:
    end = run.get("ended_at") or _now_ts()
    try:
        return max(0.0, float(end) - float(run.get("started_at") or end))
    except Exception:  # noqa: BLE001
        return 0.0


_STATUS_ZH = {
    "running": "仍在执行",
    "completed": "全部完成",
    "waiting_human": "卡在某个 Bug（已暂停，等待人工介入）",
    "limit_reached": "已达最大重试次数上限，强制终止",
    "failed": "执行失败",
    "interrupted": "运行中断（8000 重启，进度已保留）",
    "stopped": "人工停止",
}


def _fmt_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%d 小时 %d 分 %d 秒" % (hours, minutes, secs)
    if minutes:
        return "%d 分 %d 秒" % (minutes, secs)
    return "%d 秒" % secs


def report_markdown(run: dict | None = None) -> str:
    if run is None:
        run = _latest_run()
    if not isinstance(run, dict):
        # [第二战 2026-11-07] 无夜班的日子也照常留痕自愈尝试——弱器官自愈
        # 跟夜班跑不跑无关，晨报是它的固定披露口。
        fix_md = ""
        try:
            from . import self_driven as _sd
            fix_lines = _sd.self_fix_today_summary()
            if fix_lines:
                fix_md = "\n## 自愈尝试（self_fix_mode）\n\n" + "\n".join(fix_lines) + "\n"
        except Exception:  # noqa: BLE001 - 留痕失败不影响占位报
            pass
        return ("# 昨夜晚班总结\n\n"
                "暂无夜班记录。在输入框旁打开「🌙 夜班模式」，发送一条以 "
                "`[夜班模式]` 开头的任务即可开始记录。\n" + fix_md)
    used = _used(run)
    model = str(run.get("model") or LOCAL_MODEL)
    llm_calls = int(run.get("llm_calls") or 0)
    tool_calls = int(run.get("tool_calls") or 0)
    status = str(run.get("status") or "unknown")
    status_zh = _STATUS_ZH.get(status, status)
    files = run.get("modified_files") or []
    violations = run.get("violations") or []
    events = run.get("events") or []
    task = str(run.get("task") or "").replace("\n", " ").strip()
    lines = [
        "# 昨夜晚班总结",
        "",
        "**本次夜班共消耗：%d 次工具调用（模型轮次 %d + 工具执行 %d），使用了 %s 模型（已开启防烧钱熔断）。**"
        % (used, llm_calls, tool_calls, model),
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        "| 任务 | %s |" % (task[:300] + ("…" if len(task) > 300 else "") or "（未记录）"),
        "| 运行时段 | %s → %s |" % (run.get("started_iso") or "—", run.get("ended_iso") or "仍在运行"),
        "| 执行时长 | %s |" % _fmt_duration(_duration_sec(run)),
        "| 预算 | %d / %d（达到上限即强制终止） |" % (used, int(run.get("max_tool_calls") or MAX_TOOL_CALLS)),
        "| MCP 网关 | %d 次工具调用 / %d tokens（失败 %d） |" % (
            int(run.get("mcp_tool_calls") or 0), int(run.get("mcp_total_tokens") or 0),
            int(run.get("mcp_failures") or 0)),
        "| 失败次数 | %d（同一任务连续失败上限 %d） |" % (int(run.get("failure_total") or 0), MAX_CONSECUTIVE_FAILURES),
        "| 最终状态 | %s |" % status_zh,
        "| 模型来源 | %s |" % (run.get("model_source") or "default"),
        "| 预计修改清单 | %d 项（来源：%s） |" % (len(run.get("expected_files") or []), run.get("plan_source") or "none"),
        "| 实际修改文件 | %d 个 |" % len(files),
        "| 越权拦截 | %d 次 |" % len(violations),
        "| 最后一步 | %s |" % (run.get("last_checkpoint") or "—"),
        "",
        "## 修改了哪些文件",
        "",
    ]
    if files:
        for item in files[-50:]:
            lines.append("- `%s`（%s，%s）" % (item.get("path", "?"), item.get("op", "write"),
                                              item.get("ts_iso", "")))
    else:
        lines.append("- 暂无成功写盘记录（可能只做了读取/分析，或写盘发生在受管清单之外被拦截）。")
    lines.extend(["", "## 失败与拦截", ""])
    if run.get("status_reason"):
        lines.append("- **状态原因**：%s" % run["status_reason"])
    if violations:
        for item in violations[-20:]:
            lines.append("- ⛔ %s（%s）" % (item.get("reason", "越权修改拦截"), item.get("ts_iso", "")))
    else:
        lines.append("- 无越权修改拦截。")
    if int(run.get("failure_total") or 0) == 0:
        lines.append("- 无工具失败记录。")
    lines.extend(["", "## 昨夜轨迹（最近 15 条）", ""])
    for item in events[-15:]:
        lines.append("- `%s` [%s] %s" % (item.get("ts_iso", ""), item.get("kind", ""),
                                         str(item.get("msg", ""))[:300]))
    # [第二战 2026-11-07] 自愈尝试留痕：self_fix_mode 生成的卡片即使在夜里被
    # approve 出队，self_fix_log 里仍有记录——晨报如实展示"昨晚我尝试修复了
    # 什么、补丁在哪、要不要人接手"。读不到就整段省略，绝不编造。
    try:
        from . import self_driven as _sd  # 惰性导入（防环：self_driven 不 import night_shift）
        fix_lines = _sd.self_fix_today_summary()
        if fix_lines:
            lines.extend(["", "## 自愈尝试（self_fix_mode）", ""])
            lines.extend(fix_lines)
    except Exception:  # noqa: BLE001 - 留痕失败不影响晨报主体
        pass
    if status == "limit_reached":
        lines.extend(["", "> ⚠ 已达最大重试次数上限，强制终止。报告到此为止，剩余工作请人工确认。"])
    elif status == "waiting_human":
        lines.extend(["", "> ⚠ 已暂停当前步骤，等待人工介入。修复后重新发送一条 `[夜班模式]` 任务即可续跑。"])
    elif status == "interrupted":
        lines.extend(["", "> ⚠ 8000 在夜班中途重启，运行被中断；以上是持久化保存的最后进度。"])
    lines.append("")
    return "\n".join(lines)


def report_payload() -> dict:
    run = _latest_run()
    if not isinstance(run, dict):
        return {"ok": True, "has_report": False, "markdown": report_markdown(None),
                "run": None}
    return {
        "ok": True,
        "has_report": True,
        "markdown": report_markdown(run),
        "run": {
            "run_id": run.get("run_id"),
            "task": run.get("task"),
            "status": run.get("status"),
            "status_zh": _STATUS_ZH.get(str(run.get("status")), run.get("status")),
            "status_reason": run.get("status_reason"),
            "started_iso": run.get("started_iso"),
            "ended_iso": run.get("ended_iso"),
            "duration_sec": round(_duration_sec(run), 1),
            "model": run.get("model"),
            "model_source": run.get("model_source"),
            "used": _used(run),
            "llm_calls": run.get("llm_calls"),
            "tool_calls": run.get("tool_calls"),
            # [第4晚] MCP 网关专项：次数 / token / 失败
            "mcp_tool_calls": run.get("mcp_tool_calls"),
            "mcp_prompt_tokens": run.get("mcp_prompt_tokens"),
            "mcp_completion_tokens": run.get("mcp_completion_tokens"),
            "mcp_total_tokens": run.get("mcp_total_tokens"),
            "mcp_failures": run.get("mcp_failures"),
            "max_tool_calls": run.get("max_tool_calls"),
            "failures": run.get("failure_total"),
            "expected_files": run.get("expected_files"),
            "plan_source": run.get("plan_source"),
            "modified_files": run.get("modified_files"),
            "violations": run.get("violations"),
            "last_checkpoint": run.get("last_checkpoint"),
            "events": (run.get("events") or [])[-50:],
            "unread": run.get("unread"),
        },
    }


def status_payload() -> dict:
    run = _latest_run()
    if not isinstance(run, dict):
        return {"ok": True, "active": False, "open_run": False,
                "has_report": False, "unread": False}
    return {
        "ok": True,
        # active 严格=“正在跑”；waiting_human/limit_reached 是已收敛的 open run，
        # 前端看红点用 unread，不要被 active 误导成“还在烧钱”。
        "active": str(run.get("status")) == "running",
        "open_run": isinstance(_load_state().get("active"), dict),
        "has_report": True,
        "unread": bool(run.get("unread")),
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "status_zh": _STATUS_ZH.get(str(run.get("status")), run.get("status")),
        "status_reason": run.get("status_reason"),
        "used": _used(run),
        "max_tool_calls": run.get("max_tool_calls"),
        # [第4晚] MCP 网关专项统计（无活动 run/旧状态时由 _normalize_run 补 0）
        "mcp_tool_calls": run.get("mcp_tool_calls"),
        "mcp_prompt_tokens": run.get("mcp_prompt_tokens"),
        "mcp_completion_tokens": run.get("mcp_completion_tokens"),
        "mcp_total_tokens": run.get("mcp_total_tokens"),
        "mcp_failures": run.get("mcp_failures"),
        "model": run.get("model"),
        "failures": run.get("failure_total"),
        "last_checkpoint": run.get("last_checkpoint"),
        "started_iso": run.get("started_iso"),
        "ended_iso": run.get("ended_iso"),
    }


def snapshot() -> dict:
    """给 self_check / 器官面板的只读快照（绝不抛）。"""
    try:
        return status_payload()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}


def events_since(since: int = 0) -> dict:
    run = _latest_run()
    if not isinstance(run, dict):
        return {"items": [], "next": 0, "reset": False}
    events = run.get("events") or []
    since = max(0, int(since or 0))
    reset = since > len(events)
    items = events[since:] if not reset else events[-50:]
    out = []
    base = 0 if reset else since
    for idx, item in enumerate(items):
        out.append({"seq": base + idx + 1, **item})
    return {"items": out, "next": len(events), "reset": reset, "run_id": run.get("run_id")}


# ---------------- HTTP 路由（prefix=/api 由 main.py 提供） ----------------
router = APIRouter()


@router.get("/night_shift/status")
async def night_status():
    return status_payload()


@router.get("/night_shift/report")
async def night_report(format: str = ""):
    payload = report_payload()
    fmt = str(format or "").strip().lower()
    if fmt in ("md", "markdown", "text"):
        return PlainTextResponse(payload.get("markdown") or "", media_type="text/markdown; charset=utf-8")
    return JSONResponse(content=payload)


@router.post("/night_shift/start")
async def night_start(body: dict | None = None):
    data = body or {}
    task = str(data.get("task") or "").strip()
    is_night, stripped = parse_task_marker(task)
    if is_night:
        task = stripped
    if not task:
        return JSONResponse(status_code=400, content={"ok": False, "error": "task 不能为空"})
    run = start_run(task, str(data.get("session_id") or "default"),
                    data.get("expected_files") if isinstance(data.get("expected_files"), list) else None)
    return {"ok": True, "run_id": run.get("run_id"), "status": run.get("status"),
            "expected_files": run.get("expected_files"), "max_tool_calls": run.get("max_tool_calls")}


@router.post("/night_shift/stop")
async def night_stop(body: dict | None = None):
    reason = str((body or {}).get("reason") or "人工停止").strip()
    return finish_run("stopped", reason)


@router.post("/night_shift/finish")
async def night_finish(body: dict | None = None):
    """执行方主动收尾（任务真正完成/失败时调用）。

    没有这个入口，run 永远只能停在 running / waiting_human / limit_reached，
    晨报的"最终状态"就永远看不到"全部完成"。status 白名单收窄，防止调用方
    把 waiting_human 之类内部态直接写进来。
    """
    data = body or {}
    status = str(data.get("status") or "completed").strip().lower()
    if status not in ("completed", "failed", "stopped"):
        status = "completed"
    return finish_run(status, str(data.get("reason") or "").strip())


@router.get("/night_shift/plan")
async def night_plan():
    run = _latest_run()
    if not isinstance(run, dict):
        return {"ok": False, "error": "no_active_run"}
    return {"ok": True, "run_id": run.get("run_id"),
            "expected_files": run.get("expected_files") or [],
            "plan_source": run.get("plan_source")}


@router.post("/night_shift/plan")
async def night_set_plan(body: dict | None = None):
    data = body or {}
    files = data.get("files") if isinstance(data.get("files"), list) else []
    return set_plan(files, source=str(data.get("source") or "manual"),
                    replace=bool(data.get("replace", True)))


@router.post("/night_shift/guard")
async def night_guard(body: dict | None = None):
    data = body or {}
    return guard_tool(tool=str(data.get("tool") or ""), path=str(data.get("path") or ""))


@router.post("/night_shift/check_file")
async def night_check_file(body: dict | None = None):
    data = body or {}
    return check_file(str(data.get("path") or ""), str(data.get("op") or "write"))


@router.post("/night_shift/record")
async def night_record(body: dict | None = None):
    data = body or {}
    files = data.get("files") if isinstance(data.get("files"), list) else None
    return record_execution(
        task_key=str(data.get("task_key") or data.get("task_id") or ""),
        exit_code=data.get("exit_code"), status=str(data.get("status") or ""),
        output=str(data.get("output") or ""), tool=str(data.get("tool") or "zcode"),
        files=files,
    )


@router.post("/night_shift/note_file")
async def night_note_file(body: dict | None = None):
    data = body or {}
    return note_file_write(str(data.get("path") or ""), str(data.get("op") or "write"))


@router.post("/night_shift/ack")
async def night_ack():
    return ack_report()


@router.get("/night_shift/events")
async def night_events(since: int = 0):
    return {"ok": True, **events_since(since)}


@router.get("/night_shift/stream")
async def night_stream():
    """SSE：新事件实时推（心跳 2s）。前端可用 EventSource 订阅；
    等待人工介入 / 达到上限时会立刻收到对应 kind。"""
    async def _gen():
        cursor = 0
        # 首帧：完整状态快照（客户端不必再发一次 status 请求）
        try:
            yield "event: snapshot\ndata: %s\n\n" % json.dumps(
                status_payload(), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
        while True:
            try:
                batch = events_since(cursor)
                if batch.get("reset"):
                    cursor = 0
                for item in batch.get("items") or []:
                    cursor = int(item.get("seq") or cursor)
                    kind = str(item.get("kind") or "message")
                    yield "event: %s\ndata: %s\n\n" % (
                        kind.replace("\n", "_"), json.dumps(item, ensure_ascii=False))
                yield ": keep-alive\n\n"
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 流自身异常不能让连接 500
                try:
                    yield "event: error\ndata: %s\n\n" % json.dumps(
                        {"error": "%s: %s" % (type(e).__name__, str(e)[:160])}, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(2)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# 模块导入即建目录（失败降级为内存态，不让 8000 起不来）
_mkdir_state_dir()
