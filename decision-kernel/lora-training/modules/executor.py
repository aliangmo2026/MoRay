# -*- coding: utf-8 -*-
"""L7.6 执行引擎：按计划串行执行步骤，实时产出进度事件，支持取消与自检重试。

进度协议 = 异步生成器逐事件 yield dict（由主服务序列化为 SSE `data:` 行）：
  start → (step_start → step_result → check → [retry ...])* → final | cancelled
final.status ∈ {success, failed, cancelled}；failed 必带 error（哪一步、几次尝试、原因）。
[L7.7 事件字段细化]：所有事件带 ts（毫秒时间戳）；step_result/check 带 retry_count
（0=首次尝试）；step_result 补 tool/description/status；final 回显 execution_id；
retry 事件（L7.6 已有）另带 retry_count/tool/description。既有字段全部保留、纯加性。

上下文串联（任务书 三.3）：
  * 上一步实际输出以两条通道喂给下一步：
    1) 入参占位符 "{prev_output}"（字符串值内出现即替换）；
    2) 关键字段自动注入：file.write 缺 content、argus 缺 query、iris 缺 text 时
       用上一步输出填充；所有工具入参都追加 _context（目标+最近输出摘要）。

取消（任务书 三.3"支持取消执行"）：
  * 每次执行注册 execution_id → asyncio.Event；主服务 /execute 首帧回显该 id，
    POST /execute/cancel {execution_id} 置位。步骤边界与每次重试前都会检查。
"""
from __future__ import annotations

import asyncio
import ast
import copy
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from . import self_check, tool_registry


def _ts() -> int:
    """[L7.7] 毫秒级 Unix 时间戳：所有事件统一携带 ts 字段，供前端计时/耗时展示。"""
    return int(time.time() * 1000)


# execution_id → 取消信号
_ACTIVE: Dict[str, asyncio.Event] = {}

_CTX_CAP = 4000  # _context 注入摘要的字符上限
_DATA_CAP = 6000  # step_result 事件里回显 data 的字符上限

#: [v4 熔断 2026-10-23] 连败熔断阈值（ZCode 侧硬约束注入同值 3）
_SELF_STATE_FUSE_CF = 3


def _self_state_fused() -> bool:
    """[v4 熔断 2026-10-23] 主服务 self_state_log 最新连败 >= 3 → True（防撞墙熔断）。

    取数方式：只读直查 SQLite（两仓进程隔离不互 import；也不走内网 HTTP——
    安全约束禁服务端请求环回/私网地址）。库路径 MORAY_MEMORY_DB 环境变量优先
    （与主服务同名同义），缺省按仓相对布局推算 <MoRay>/server/data/memory.sqlite3。
    任何异常/无库/无表/无行 → False（fail-open：状态读不到绝不拦任务）。
    """
    try:
        db = os.environ.get("MORAY_MEMORY_DB", "").strip()
        if not db:
            db = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "..", "server", "data", "memory.sqlite3"))
        if not os.path.isfile(db):
            return False
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT consecutive_failures FROM self_state_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return row is not None and int(row[0] or 0) >= _SELF_STATE_FUSE_CF
    except Exception:  # noqa: BLE001 - 状态读取失败绝不拦任务
        return False


def cancel(execution_id: str) -> bool:
    """外部取消入口：命中返回 True。"""
    ev = _ACTIVE.get(str(execution_id))
    if ev is None:
        return False
    ev.set()
    return True


def running_ids() -> List[str]:
    return list(_ACTIVE.keys())


def _shrink(obj: Any, cap: int = _DATA_CAP) -> str:
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:cap]


def _last_output(ctx: List[Dict[str, Any]]) -> Optional[str]:
    return str(ctx[-1]["output"]) if ctx else None


def _materialize(step: Dict[str, Any], goal: str, ctx: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把计划里的原始入参 + 执行上下文合成实际调用入参。"""
    inp = copy.deepcopy(dict(step.get("input") or {}))
    prev = _last_output(ctx)
    # [修复 2026-09-30] file read 的上一步输出是 data 信封（{"path","content","size"}），
    # {prev_output} 注入前先提取纯文本 content，避免把字典结构写进目标文件。
    prev_raw = prev
    if prev:
        try:
            _obj = ast.literal_eval(prev)
            if isinstance(_obj, dict) and _obj.get("content") is not None:
                prev_raw = str(_obj["content"])
        except Exception:  # noqa: BLE001 - 非字典字符串（argus/iris/zcode 输出）原样使用
            prev_raw = prev
    for k, v in list(inp.items()):
        if isinstance(v, str) and "{prev_output}" in v:
            inp[k] = v.replace("{prev_output}", prev_raw or "")
    tool = str(step.get("tool") or "").lower()
    if prev:
        if tool == "file" and inp.get("op") == "write" and not inp.get("content"):
            inp["content"] = prev                       # 上一步输出直接落盘（最常见串联）
        if tool == "argus" and not (inp.get("query") or inp.get("keyword")):
            inp["query"] = prev[:200]
        if tool == "iris" and not (inp.get("image_path") or inp.get("image_base64") or inp.get("text")):
            inp["text"] = prev
    inp["_context"] = {"goal": goal[:500], "prev_output": _shrink(prev, _CTX_CAP) if prev else None}
    return inp


# ===================== [新增代码·开始] M2 任务成功自动写记忆（2026-10-02，纯加性） =====================
# 任务成功收尾的旁路钩子：fire-and-forget POST 到 MoRay 8000 /api/memory/save（M1 端点）。
# 只在 execute_stream / _execute_multi_steps 的 final success yield 前各调一次（两点互斥，
# 每个成功任务恰好触发一次；execute_collect 迭代 execute_stream 自动继承，不另加以免双写）。
# 失败/取消路径一律不调。总闸 MORAY_MEMORY_HOOK（语义同 self_check.JUDGE_ENABLED：默认
# 开，"0" 关；调用时读取，翻转后重启 8001 生效）。线程内 lazy import httpx（先例
# event_reporter.py:119），模块 import 保持零副作用。

_MEM_LOG = logging.getLogger("laya.executor")


def _memory_post_worker(goal: str, records: List[Dict[str, Any]], final_result: Any,
                        execution_id: str) -> None:
    """daemon 线程体：构造任务摘要并同步 POST /api/memory/save，任何异常全吞只记 warning。"""
    try:
        import httpx  # lazy import：同 event_reporter.py:119 先例
        tools: List[str] = []
        for rec in records or []:
            name = str((rec or {}).get("tool") or "").strip()
            if name and name not in tools:
                tools.append(name)
        content = "任务：%s\n工具链：%s\n结果：%s" % (
            str(goal or "")[:200],
            " → ".join(tools) if tools else "无",
            _shrink(final_result, 500))
        body = {"content": content, "kind": "task_result",
                "tags": ["laya_task"] + tools, "source": "laya_executor",
                "entity_id": str(execution_id or ""), "session_id": "default"}
        # 与 tool_registry 同源复用 MORAY_BASE（LAYA_MORAY_BASE 可覆盖，默认即 127.0.0.1:8000）
        base = str(getattr(tool_registry, "MORAY_BASE", "http://127.0.0.1:8000")).rstrip("/")
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(base + "/api/memory/save", json=body)
        if resp.status_code != 200:
            _MEM_LOG.warning("[memory-hook] /api/memory/save 非 200：status=%s body=%s",
                             resp.status_code, resp.text[:200])
    except Exception as e:  # noqa: BLE001 - 旁路写入失败绝不影响主流程
        _MEM_LOG.warning("[memory-hook] 写入失败：execution_id=%s err=%s: %s",
                         execution_id, type(e).__name__, str(e)[:200])


def _write_memory_on_success(goal: str, steps: List[Dict[str, Any]],
                             records: List[Dict[str, Any]], final_result: Any,
                             execution_id: str) -> None:
    """任务成功的旁路记忆钩子：总闸开着就起 daemon 线程 POST，函数体异常全吞。

    steps 为任务书签名的对称参数（工具链实际从 records 取，含重试后的真实工具名）。
    """
    try:
        if os.environ.get("MORAY_MEMORY_HOOK", "1") != "0":
            threading.Thread(target=_memory_post_worker, name="moray-memory-hook",
                             args=(goal, records, final_result, execution_id),
                             daemon=True).start()
    except Exception as e:  # noqa: BLE001 - 钩子启动失败绝不影响任务收尾
        _MEM_LOG.warning("[memory-hook] 启动失败：execution_id=%s err=%s: %s",
                         execution_id, type(e).__name__, str(e)[:200])
# ===================== [新增代码·结束] M2 任务成功自动写记忆 =====================


async def execute_stream(goal: str, steps: List[Dict[str, Any]],
                         max_retry: int = 5,
                         execution_id: Optional[str] = None,
                         memory_context: Optional[str] = None) -> AsyncIterator[Dict[str, Any]]:
    """主生成器：逐事件产出进度。消费方（SSE 端点/测试）迭代到 final 即可。

    [M3b 2026-10-02] memory_context：主服务 /execute 开闸时召回的【长期记忆】文本块
    （None=未开闸/召回失败/零命中），仅 L7.8 多步链路的 decision 步注入，不污染 goal。
    """
    goal = str(goal or "")
    execution_id = str(execution_id or uuid.uuid4().hex[:12])
    cancel_ev = asyncio.Event()
    _ACTIVE[execution_id] = cancel_ev
    max_retry = max(0, int(max_retry))
    ctx: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    try:
        yield {"event": "start", "execution_id": execution_id, "goal": goal[:300],
               "total_steps": len(steps), "max_retry": max_retry, "ts": _ts(),
               "endpoints": tool_registry.registry_status()}
        if not steps:
            yield {"event": "final", "status": "failed", "error": "计划为空，无可执行步骤",
                   "ts": _ts(), "execution_id": execution_id, "steps": records}
            return
        # ---- [L7.8 入口加判断·纯加性] 工程范式多步编排链路 ----
        # 仅当 步骤数>1 且步骤工具集中出现 L7.8 原子/decision 工具（范式计划特征）才走新链路；
        # 步骤数=1、以及原有四工具（含 L7.7 复合链）的任意计划，完全走下方原有
        # 自检重试逻辑，一行不动——重试机制与上下文串联彼此解耦（不动 self_check.py）。
        if len(steps) > 1 and any(
                str(s.get("tool") or "").lower() in _PATTERN_TOOLS
                for s in steps if isinstance(s, dict)):
            async for _ev in _execute_multi_steps(execution_id, steps, goal,
                                                  memory_context=memory_context):
                yield _ev
            return
        # ---- [L7.8 分支结束] ----
        for raw_step in steps:
            step = copy.deepcopy(raw_step)   # 重试修正只动本地副本，不污染原计划
            if cancel_ev.is_set():
                yield {"event": "final", "status": "cancelled",
                       "error": "用户在 step %s 之前取消" % step.get("step_id"),
                       "ts": _ts(), "execution_id": execution_id, "steps": records}
                return
            attempt = 0
            final_ok = False
            last_verdict: Dict[str, Any] = {}
            while True:
                attempt += 1
                payload = _materialize(step, goal, ctx)
                yield {"event": "step_start", "step_id": step["step_id"], "tool": step["tool"],
                       "attempt": attempt, "ts": _ts(),
                       "description": step.get("description", ""),
                       "input": json.loads(json.dumps(payload, ensure_ascii=False, default=str))}
                result = await tool_registry.call_tool(step["tool"], payload)
                # [L7.7] 进度细化：retry_count（当前重试次数，0=首次尝试）+ 毫秒 ts
                #        + 补齐 step_id/description/tool/status/data/error 全量事件字段
                yield {"event": "step_result", "step_id": step["step_id"], "attempt": attempt,
                       "retry_count": attempt - 1, "ts": _ts(),
                       "tool": step["tool"], "description": step.get("description", ""),
                       "status": "ok" if result["ok"] else "failed",
                       "ok": result["ok"], "elapsed_ms": result["elapsed_ms"],
                       "data": _shrink(result["data"]),
                       "error": result["error"]}
                verdict = await self_check.check(step, result)
                last_verdict = verdict
                yield {"event": "check", "step_id": step["step_id"], "attempt": attempt,
                       "retry_count": attempt - 1, "ts": _ts(),
                       "success": bool(verdict["success"]), "mode": verdict.get("mode"),
                       "reason": verdict.get("reason", "")}
                if verdict["success"]:
                    final_ok = True
                    ctx.append({"step_id": step["step_id"], "output": result["data"]})
                    break
                if cancel_ev.is_set():
                    yield {"event": "final", "status": "cancelled",
                           "error": "step %s 重试前被取消" % step["step_id"],
                           "ts": _ts(), "execution_id": execution_id, "steps": records}
                    return
                if attempt > max_retry:      # 首次 + max_retry 次重试后放弃
                    break
                # [v4 熔断 2026-10-23] 防撞墙：连败达阈值不再试错，直接失败收场。
                # 事件形态同 cancelled 的 mid-loop 终态帧，不 raise——本函数是异步
                # 生成器（SSE 事件协议），抛异常只会打烂事件流而非"终止重试"。
                if _self_state_fused():
                    _MEM_LOG.warning("[v4] 自我状态熔断：连败>=%d，step %s 终止重试",
                                     _SELF_STATE_FUSE_CF, step["step_id"])
                    yield {"event": "final", "status": "failed",
                           "error": "心智过载熔断：连续失败达到 %d 次，终止重试（step %s）"
                                    % (_SELF_STATE_FUSE_CF, step["step_id"]),
                           "ts": _ts(), "execution_id": execution_id, "steps": records}
                    return
                yield {"event": "retry", "step_id": step["step_id"], "attempt": attempt,
                       "retry_count": attempt, "ts": _ts(),
                       "tool": step["tool"], "description": step.get("description", ""),
                       "fix": verdict.get("fix", "")}
                step["input"] = await self_check.revise_input(step, verdict)
            rec = {"step_id": step["step_id"], "tool": step["tool"],
                   "description": step.get("description", ""),
                   "status": "ok" if final_ok else "failed",
                   "attempts": attempt, "check_mode": last_verdict.get("mode"),
                   "output": _shrink(ctx[-1]["output"], 1500) if final_ok and ctx else None,
                   "error": None if final_ok else last_verdict.get("reason")}
            records.append(rec)
            if not final_ok:
                yield {"event": "final", "status": "failed",
                       "error": "step %s（%s）重试 %d 次仍失败：%s"
                                % (step["step_id"], step["tool"], max_retry,
                                   last_verdict.get("reason", "")),
                       "ts": _ts(), "execution_id": execution_id,
                       "failed_step": rec, "steps": records}
                return
        # ---- [M2 2026-10-02] 任务成功 → 旁路写记忆（fire-and-forget，失败全吞不影响收尾）----
        _write_memory_on_success(goal, steps, records,
                                 _shrink(ctx[-1]["output"]) if ctx else None,
                                 execution_id)
        yield {"event": "final", "status": "success",
               "result": _shrink(ctx[-1]["output"]) if ctx else None,
               "ts": _ts(), "execution_id": execution_id,
               "steps": records, "note": "全部 %d 步成功" % len(records)}
    finally:
        _ACTIVE.pop(execution_id, None)


async def execute_collect(goal: str, steps: List[Dict[str, Any]], max_retry: int = 5,
                          execution_id: Optional[str] = None) -> Dict[str, Any]:
    """非流式便捷口：跑完全部事件，返回 {"events": [...], "final": {...}}（测试/内嵌调用用）。"""
    events: List[Dict[str, Any]] = []
    final: Dict[str, Any] = {}
    async for ev in execute_stream(goal, steps, max_retry, execution_id):
        events.append(ev)
        if ev.get("event") == "final":
            final = ev
    return {"events": events, "final": final}


# ===================== [新增代码·开始] L7.8 多步编排：上下文自动串联（2026-09-29，纯加性） =====================
# 本节只向下追加，上方原有单步/串行执行、自检重试、取消逻辑一行未改未删；
# 对原函数的唯一触达 = execute_stream() 里"步骤数>1 且含原子/decision 工具 → 转新链路"的
# if 分支（已就地标注）。零新增 import：占位符解析用纯字符串扫描，decision 复用
# self_check._llm_json 模型调用机制，原子工具复用 tool_registry 已注册的 zcode.* 全名。
#
# 事件兼容：新链路仍产出 start（独立调用时）/step_start/step_result/final，
# 并在每步 step_result 之后镜像一条同构的 step_end（任务书 L7.8 命名要求）；
# final 沿用原有 status/error/execution_id/steps 字段，失败时额外携带
# failed_step_index / completed_steps / total_steps（均为可选新增键，无新增必填字段）。
# 取消：复用 _ACTIVE[execution_id] 信号与原有 cancel()，步骤边界生效，cancel() 本体未动。
# 重试：新链路严格串行、不重试不走 self_check——与原有链路的自检重试机制互不影响（解耦）。

# 原子工具别名表：范式步骤里的裸名 → tool_registry 已注册全名（zcode.*）。
# decision 不在表内 = 特殊本地步，由 self_check._llm_json 执行。
_ATOMIC_TOOL_ALIASES = {
    "file_list": "zcode.file_list",
    "file_read": "zcode.file_read",
    "file_write": "zcode.file_write",
    "file_edit": "zcode.file_edit",
    "cmd_exec": "zcode.cmd_exec",
    "process_kill": "zcode.process_kill",
    "port_check": "zcode.port_check",
}
_DECISION_TOOL = "decision"
_PATTERN_TOOLS = frozenset(_ATOMIC_TOOL_ALIASES) | frozenset(_ATOMIC_TOOL_ALIASES.values()) \
    | frozenset({_DECISION_TOOL})

_DECISION_SYSTEM_DEFAULT = (
    "你是工程任务决策引擎。基于给出的需求与上下文，产出可直接执行的决策结论。"
    "只输出纯JSON对象（字段按用户要求），不要解释、不要代码块标记。"
    "fixed_content 的值必须是目标文件的完整裸文本内容本身："
    "禁止用 ``` 围栏或任何 markdown 包裹，禁止解释、注释、前后缀。")

# decision 输出里可作为 file_edit/file_write content 注入的候选键（按优先级）
_FIX_CONTENT_KEYS = ("fixed_content", "content", "patch", "code")
# decision 输出里可作为后续 file_read/file_edit path 注入的候选键（按优先级）
_PATH_KEYS = ("problem_path", "file_path", "path")

# [fence_guard 2026-10-03] decision 字段值剥围栏保险：小模型可能把 ```lang 围栏包进
# fixed_content 值里（JSON 仍合法、外层"不要代码块"管不到值内），写文件前在此兜底剥除。
_FENCE_OPEN_RE = re.compile(r"\A```[ \t]*[A-Za-z0-9_+\-.#]*[ \t]*\r?\n")
_FENCE_CLOSE_RE = re.compile(r"\r?\n```[ \t]*\Z")


def _strip_code_fence(text: str) -> str:
    """剥掉字段值首尾被误包的代码围栏；非围栏形态一律原样返回，值中间的 ``` 不动。

    只认"首行为纯 ``` 或 ```lang 围栏行"的形态：闭合则首尾都剥，未闭合只剥首行
    （截断输出保住已生成部分）；analysis/summary 等自由文本不走本函数。
    """
    s = text.strip()
    if not s.startswith("```"):
        return text
    opened = _FENCE_OPEN_RE.sub("", s, count=1)
    if opened == s:      # 首行不是纯围栏行（如 ```python 与代码同行）→ 不剥
        return text
    return _FENCE_CLOSE_RE.sub("", opened, count=1)


# ---- 占位符解析（纯字符串扫描，零 import） ----

def _ph_expr(text: str, i: int):
    """从 text[i]=='{' 起解析占位符表达式，合法返回 (expr, 结束下标)，否则 (None, i)。

    合法形态：首字符字母/下划线，其后仅字母/数字/下划线/点，且点分段非空——
    这样模型输出示例里带引号/空格的 {"fixed_content": ...} 不会被误认成占位符。
    """
    j = i + 1
    while j < len(text) and (text[j].isalnum() or text[j] in "_."):
        j += 1
    if j < len(text) and text[j] == "}" and j > i + 1:
        expr = text[i + 1:j]
        if (expr[0].isalpha() or expr[0] == "_") and not expr.endswith(".") and ".." not in expr:
            return expr, j + 1
    return None, i


def _lookup_output(expr: str, step_outputs: Dict[Any, Any], current_idx: int) -> Optional[str]:
    """解析 {prev_output} / {step_N_output} / {prev_output.attr} / {target_path}。

    step_outputs：int 键=1 基步骤序号→该步输出文本；"target_path" 键=上下文文件路径。
    嵌套属性：值先尝试 json.loads 再按 . 逐段走查（dict 键 / list 下标）；解不出返回 None。
    """
    try:
        base, sep, rest = expr.partition(".")
        if base == "prev_output":
            value = step_outputs.get(current_idx - 1)
        elif base.startswith("step_") and base.endswith("_output"):
            num = base[5:-len("_output")]
            value = step_outputs.get(int(num)) if num.isdigit() else None
        elif base == "target_path":
            value = step_outputs.get("target_path")
        else:
            return None
        if value is None:
            return None
        if not sep:
            return value if isinstance(value, str) else _shrink(value)
        cur: Any = json.loads(value) if isinstance(value, str) else value
        for part in rest.split("."):
            if isinstance(cur, dict):
                if part not in cur:
                    return None
                cur = cur[part]
            elif isinstance(cur, list):
                if not part.isdigit() or int(part) >= len(cur):
                    return None
                cur = cur[int(part)]
            else:
                return None
        if cur is None:
            return None
        return cur if isinstance(cur, str) else _shrink(cur)
    except Exception:  # noqa: BLE001 - JSON 解不开/类型怪异 → 视为未命中，保留原占位符
        return None


def _ph_replace(text: str, step_outputs: Dict[Any, Any], current_idx: int) -> Any:
    """整串单占位符 → 直接替换为解析值；内嵌占位符 → 逐处文本替换，未命中的原样保留。"""
    if "{" not in text:
        return text
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
        expr, end = _ph_expr(stripped, 0)
        if expr is not None and end == len(stripped):
            got = _lookup_output(expr, step_outputs, current_idx)
            return text if got is None else got
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            expr, end = _ph_expr(text, i)
            if expr is not None:
                got = _lookup_output(expr, step_outputs, current_idx)
                out.append(got if got is not None else text[i:end])
                i = end
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _replace_placeholders(params: Any, step_outputs: Dict[Any, Any],
                          current_idx: int) -> Any:
    """占位符替换入口：递归处理 dict/list/str 参数（任务书 二.2）。永不抛异常。"""
    try:
        if isinstance(params, dict):
            return {k: _replace_placeholders(v, step_outputs, current_idx) for k, v in params.items()}
        if isinstance(params, list):
            return [_replace_placeholders(v, step_outputs, current_idx) for v in params]
        if isinstance(params, str):
            return _ph_replace(params, step_outputs, current_idx)
        return params
    except Exception:  # noqa: BLE001 - 替换失败保留原始参数，降级手动模式
        return params


def _first_path_in(obj: Any) -> Optional[str]:
    """从 file_list 返回结构里找首个可读文件路径：dict.path → dir+entries[0].name 拼接。"""
    try:
        if isinstance(obj, dict):
            for key in ("path", "file_path"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip() and not isinstance(obj.get("entries"), list):
                    return val
            dirp = obj.get("dir") or obj.get("directory")
            entries = obj.get("entries") or obj.get("files") or obj.get("items")
            if isinstance(dirp, str) and isinstance(entries, list):
                for ent in entries:
                    if isinstance(ent, dict) and ent.get("is_dir") is not True:
                        name = ent.get("name") or ent.get("path") or ent.get("file_path")
                        if isinstance(name, str) and name:
                            return dirp.rstrip("\\/") + "/" + name.strip("/\\")
            for val in obj.values():
                got = _first_path_in(val)
                if got:
                    return got
        elif isinstance(obj, list):
            for val in obj:
                got = _first_path_in(val)
                if got:
                    return got
    except Exception:  # noqa: BLE001
        return None
    return None


# ---- 全局上下文缓存（任务书 二.4）：任何异常只放弃更新，绝不打断执行 ----

def _update_context_store(context_store: Dict[str, Any], tool: str,
                          result: Dict[str, Any]) -> Dict[str, Any]:
    """成功步骤 → 更新缓存：file_read 内容/路径、cmd_exec stdout/stderr、
    file_list 首个文件路径、decision 的修复内容与问题路径。"""
    try:
        if not isinstance(result, dict) or not result.get("ok"):
            return context_store
        t = str(tool or "").lower()
        data = result.get("data")
        if t in ("zcode.file_read", "file_read"):
            if isinstance(data, dict):
                if data.get("content") is not None:
                    context_store["file_content"] = str(data.get("content"))
                if data.get("path"):
                    context_store["file_path"] = str(data.get("path"))
            elif isinstance(data, str) and data:
                context_store["file_content"] = data
        elif t in ("zcode.cmd_exec", "cmd_exec", "zcode"):
            if isinstance(data, dict):
                so = data.get("stdout", data.get("output", data.get("result")))
                se = data.get("stderr", data.get("error_output"))
                if so is not None:
                    context_store["stdout"] = so if isinstance(so, str) else _shrink(so)
                if se is not None:
                    context_store["stderr"] = se if isinstance(se, str) else _shrink(se)
            elif data is not None:
                context_store["stdout"] = data if isinstance(data, str) else _shrink(data)
        elif t in ("zcode.file_list", "file_list"):
            fp = _first_path_in(data)
            if fp:
                context_store["first_file_path"] = fp
        elif t == _DECISION_TOOL and isinstance(data, str):
            try:
                parsed = json.loads(data)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                for key in _FIX_CONTENT_KEYS:
                    if parsed.get(key) is not None:
                        # [fence_guard 2026-10-03] 入缓存前剥围栏（只此一处入口，file_edit 兜底注入同享）
                        context_store["fixed_content"] = _strip_code_fence(str(parsed[key]))
                        break
                for key in _PATH_KEYS:
                    if parsed.get(key):
                        context_store["file_path"] = str(parsed[key])
                        break
                # [exit_check 2026-10-03] decision 可声明期望退出码（供后续 cmd_exec 按值校验）；
                # 仅收整数（模型输出非数值按未声明处理，回落模板静态兜底）
                _exp = parsed.get("expected_exit_code")
                if isinstance(_exp, int) and not isinstance(_exp, bool):
                    context_store["expected_exit_code"] = _exp
    except Exception:  # noqa: BLE001 - 缓存更新失败不影响主流程
        return context_store
    return context_store


# ---- 上下文自动注入（任务书 二.3）：注入失败不中断，降级为手动参数模式 ----

def _is_unfilled(value: Any) -> bool:
    """空/缺失/仍含未解析占位符 → 需要上下文注入兜底。"""
    if value is None:
        return True
    if not isinstance(value, str):
        return True
    return (not value.strip()) or ("{" in value and "}" in value)


def _auto_inject_context(params: Dict[str, Any], tool: str,
                         context_store: Dict[str, Any]) -> Dict[str, Any]:
    """按工具类型补齐参数：
    file_read/编辑写类 path|content 缺失 → 用缓存的文件路径/内容/decision 修复内容；
    decision.user 追加文件内容与 cmd stdout/stderr 上下文块（已包含则不重复追加）；
    [M3b] 另追加 context_store.memory_context（【长期记忆】块，置于 extra 尾部作背景参考）。
    """
    try:
        p = copy.deepcopy(dict(params or {}))
        t = str(tool or "").lower()
        if t in ("zcode.file_read", "file_read"):
            if _is_unfilled(p.get("path")):
                p["path"] = context_store.get("file_path") or context_store.get("first_file_path")
        elif t in ("zcode.file_edit", "file_edit", "zcode.file_write", "file_write"):
            if _is_unfilled(p.get("path")):
                p["path"] = context_store.get("file_path") or context_store.get("first_file_path")
            if _is_unfilled(p.get("content")):
                p["content"] = context_store.get("fixed_content") or context_store.get("file_content")
            if t in ("zcode.file_edit", "file_edit"):
                # 范式生成的是"整文件重写"语义：缺行号时默认覆盖全文（1..1e9）
                if p.get("line_start") is None:
                    p["line_start"] = 1
                if p.get("line_end") is None:
                    p["line_end"] = 10 ** 9
        elif t == _DECISION_TOOL:
            user = str(p.get("user") or p.get("text") or "")
            extra: List[str] = []
            fc = context_store.get("file_content")
            if fc and str(fc)[:120] not in user:
                extra.append("【当前文件内容 %s】\n%s" % (context_store.get("file_path") or "",
                                                        _shrink(fc, _CTX_CAP)))
            for key, label in (("stdout", "【标准输出】"), ("stderr", "【错误输出】")):
                val = context_store.get(key)
                if val and str(val)[:120] not in user:
                    extra.append("%s\n%s" % (label, _shrink(val, _CTX_CAP)))
            # [auto_repair 2026-10-03] 返修反馈块：置于 stdout/stderr 之后、长期记忆之前
            # （最新失败输出优先于背景参考）。repair_feedback 由失败出口写入，重放轮注入。
            rf = context_store.get("repair_feedback")
            if isinstance(rf, dict) and rf:
                extra.append("【第 %s 轮返修】上轮修复后验证仍失败（%s），请据此给出新的修复内容：\n%s"
                             % (rf.get("round"), rf.get("reason") or "验证未通过", rf.get("detail") or ""))
            mc = context_store.get("memory_context")   # [M3b] 长期记忆块放 extra 尾部（背景参考）
            if mc and str(mc)[:120] not in user:
                extra.append(str(mc))
            if extra:
                p["user"] = user + "\n\n" + "\n\n".join(extra)
        return p
    except Exception:  # noqa: BLE001 - 注入失败降级为手动参数模式，不中断任务
        return params


# ---- 多步串行编排核心（任务书 二.1）----

async def _execute_multi_steps(task_id: str, steps: List[Dict[str, Any]],
                               goal: str = "",
                               memory_context: Optional[str] = None) -> AsyncIterator[Dict[str, Any]]:
    """工程范式多步链路：严格串行、上一步输出自动注入下一步、任一失败即终止后续。

    由 execute_stream() 的 L7.8 分支调用（此时 _ACTIVE 已注册，不再重复发 start）；
    也可独立调用（自注册/自清理 _ACTIVE 并补发 start 帧），事件结构完全兼容原有 SSE 协议。
    """
    steps = list(steps or [])
    total = len(steps)
    task_id = str(task_id or uuid.uuid4().hex[:12])
    owns_slot = task_id not in _ACTIVE
    if owns_slot:
        _ACTIVE[task_id] = asyncio.Event()
    cancel_ev = _ACTIVE.get(task_id)
    step_outputs: Dict[Any, Any] = {}     # int→该步输出文本；"target_path"→上下文路径
    context_store: Dict[str, Any] = {}    # 全局上下文缓存
    if memory_context:                    # [M3b] 【长期记忆】块随链透传，decision 步注入用
        context_store["memory_context"] = str(memory_context)
    records: List[Dict[str, Any]] = []
    completed: List[Dict[str, Any]] = []
    try:
        if owns_slot:
            yield {"event": "start", "execution_id": task_id, "goal": str(goal)[:300],
                   "total_steps": total, "max_retry": 0, "ts": _ts(),
                   "endpoints": tool_registry.registry_status()}
        if not steps:
            yield {"event": "final", "status": "failed", "error": "计划为空，无可执行步骤",
                   "ts": _ts(), "execution_id": task_id, "failed_step_index": 0,
                   "completed_steps": [], "total_steps": 0, "steps": records}
            return
        # ---- [auto_repair 2026-10-03] 自动返修：while 游标替代 for 枚举（步处理逻辑原样） ----
        # last_decision_idx = 链中最后一个 decision 步序号（0=无 decision → 永不返修）；
        # 仅当退出码校验失败（code=-3）且轮次未耗尽时，从该步起重放一轮：decision 借
        # 【第 N 轮返修】反馈块看到上轮失败输出，产出新 fixed_content 再验证。
        # 轮数 = MORAY_REPAIR_ROUNDS（默认 1，"0"=关闭；语义同 MORAY_EXIT_CHECK/MEMORY_HOOK）。
        last_decision_idx = 0
        for _i, _s in enumerate(steps, 1):
            if isinstance(_s, dict) and str(_s.get("tool") or "").lower() == _DECISION_TOOL:
                last_decision_idx = _i
        repair_rounds_total = max(0, int(os.environ.get("MORAY_REPAIR_ROUNDS", "1")))
        repair_round = 0
        idx = 1
        while idx <= total:
            raw = steps[idx - 1]
            if cancel_ev is not None and cancel_ev.is_set():
                yield {"event": "final", "status": "cancelled",
                       "error": "用户在 step %d 之前取消" % idx,
                       "ts": _ts(), "execution_id": task_id,
                       "completed_steps": completed, "total_steps": total, "steps": records}
                return
            step = copy.deepcopy(raw)
            tool_name = str(step.get("tool") or "").lower()
            resolved = _DECISION_TOOL if tool_name == _DECISION_TOOL \
                else _ATOMIC_TOOL_ALIASES.get(tool_name, tool_name)
            description = str(step.get("description") or "")
            # 上下文串联三连：占位符替换 → 缺失参数注入 → （成功后）缓存更新
            if context_store.get("file_path"):
                step_outputs["target_path"] = context_store["file_path"]
            params = _replace_placeholders(dict(step.get("input") or {}), step_outputs, idx)
            params = _auto_inject_context(params, resolved, context_store)
            try:
                ev_input = json.loads(json.dumps(params, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                ev_input = {"_repr": _shrink(params)}
            yield {"event": "step_start", "step_id": step.get("step_id", idx), "tool": resolved,
                   "attempt": 1, "ts": _ts(), "description": description, "input": ev_input}
            if resolved == _DECISION_TOOL:
                t0 = time.perf_counter()
                parsed = await self_check._llm_json(
                    str(params.get("system") or _DECISION_SYSTEM_DEFAULT),
                    str(params.get("user") or params.get("text") or goal or description),
                    fmt="json")
                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                if parsed is None:
                    result = {"ok": False, "tool": resolved, "data": None,
                              "error": {"code": -2,
                                        "message": "decision 决策模型无应答或输出非JSON（降级终止，不改走重试）"},
                              "elapsed_ms": elapsed}
                else:
                    if isinstance(parsed, dict):
                        # [fence_guard 2026-10-03] 裸文本候选键就地剥围栏：
                        # {step_N_output.fixed_content} 占位符取值走本条 JSON（主路径），
                        # 注入兜底走 _update_context_store——两条路同享剥过的干净值。
                        for _k in _FIX_CONTENT_KEYS:
                            if isinstance(parsed.get(_k), str):
                                parsed[_k] = _strip_code_fence(parsed[_k])
                    text = parsed if isinstance(parsed, str) \
                        else json.dumps(parsed, ensure_ascii=False, default=str)
                    result = {"ok": True, "tool": resolved, "data": text,
                              "error": None, "elapsed_ms": elapsed}
            else:
                result = await tool_registry.call_tool(resolved, params)
            ok = bool(result.get("ok"))
            output_text = None
            if ok:
                data = result.get("data")
                output_text = data if isinstance(data, str) else _shrink(data)
            # [exit_check 2026-10-03] 空心绿修复：8005 命令失败仍回 HTTP 200（exit_code 在应答体），
            # tool_registry 的 ok 只是传输级语义 → cmd_exec 应答体里的退出码必须按值校验。
            # 期望值优先级：decision 输出（context_store）> 模板静态兜底（步骤字段）；
            # 两者皆无或应答体无整数 exit_code → 不校验（兼容旧格式；step2 复现错误步
            # 运行在 decision 之前且模板未给静态字段，天然不受影响）。总闸 MORAY_EXIT_CHECK=0 关。
            if ok and os.environ.get("MORAY_EXIT_CHECK", "1") != "0" \
                    and resolved in ("zcode.cmd_exec", "cmd_exec"):
                _data = result.get("data")
                _expected = context_store.get("expected_exit_code")
                # [promptfix 2026-10-03] 末步静态优先：模板 expected_exit_code 定义"任务成功"
                # 语义（三范式末步均=0），decision 不得在末步重定义成功——否则坏修复可
                # 自 declared 非零期望码"洗绿"（e2e 实证：坏修复 declared ec=1 且真退出
                # 1 → 伪 success）。中间步维持 decision 值优先不变。
                if idx == total and step.get("expected_exit_code") is not None:
                    _expected = step.get("expected_exit_code")
                if _expected is None:
                    _expected = step.get("expected_exit_code")
                _actual = _data.get("exit_code") if isinstance(_data, dict) else None
                if isinstance(_expected, int) and not isinstance(_expected, bool) \
                        and isinstance(_actual, int) and not isinstance(_actual, bool) \
                        and _actual != _expected:
                    ok = False
                    result["ok"] = False
                    result["error"] = {"code": -3,
                                       "message": "命令超时（超时限制内未完成）" if _actual == -1
                                                  else "退出码 %s ≠ 期望 %s" % (_actual, _expected)}
            err = result.get("error") or {}
            yield {"event": "step_result", "step_id": step.get("step_id", idx), "attempt": 1,
                   "retry_count": 0, "ts": _ts(), "tool": resolved, "description": description,
                   "status": "ok" if ok else "failed", "ok": ok,
                   "elapsed_ms": result.get("elapsed_ms"),
                   "data": _shrink(output_text), "error": result.get("error")}
            # [L7.8] 任务书要求的 step_end 事件：与 step_result 同构镜像，两套消费方都兼容
            yield {"event": "step_end", "step_id": step.get("step_id", idx), "attempt": 1,
                   "retry_count": 0, "ts": _ts(), "tool": resolved, "description": description,
                   "status": "ok" if ok else "failed", "ok": ok,
                   "elapsed_ms": result.get("elapsed_ms"),
                   "data": _shrink(output_text), "error": result.get("error")}
            reason = None if ok else "code=%s %s" % (err.get("code"), err.get("message", ""))
            rec = {"step_id": step.get("step_id", idx), "tool": resolved,
                   "description": description, "status": "ok" if ok else "failed",
                   "attempts": 1, "check_mode": None,
                   "round": repair_round,
                   "output": _shrink(output_text, 1500) if ok else None, "error": reason}
            records.append(rec)
            if ok:
                step_outputs[idx] = output_text
                _update_context_store(context_store, resolved, result)
                if context_store.get("file_path"):
                    step_outputs["target_path"] = context_store["file_path"]
                completed.append({"step_id": rec["step_id"], "tool": resolved,
                                  "description": description})
                idx += 1
                continue
            # ---- [auto_repair 2026-10-03] 失败出口：可返修则从最后一个 decision 步重放 ----
            # 触发窄条件：仅退出码校验失败（code=-3）+ 有前置 decision + 轮次未耗尽；
            # 传输失败 / decision 无应答（code=-2）/ 文件步失败一律原样终止，不返修。
            _repairable = (resolved in ("zcode.cmd_exec", "cmd_exec")
                           and isinstance(err, dict) and err.get("code") == -3
                           and 0 < last_decision_idx < idx
                           and repair_round < repair_rounds_total)
            if _repairable:
                # [v4 熔断 2026-10-23] 防撞墙：连败达阈值终止返修重放（同 execute_stream
                # 重试环口径，终态帧形状沿用下方"返修仍失败"分支的完整字段）
                if _self_state_fused():
                    _MEM_LOG.warning("[v4] 自我状态熔断：连败>=%d，step %s 返修重放终止",
                                     _SELF_STATE_FUSE_CF, rec["step_id"])
                    yield {"event": "final", "status": "failed",
                           "error": "心智过载熔断：连续失败达到 %d 次，终止返修（step %s）：%s"
                                    % (_SELF_STATE_FUSE_CF, rec["step_id"], reason),
                           "ts": _ts(), "execution_id": task_id,
                           "failed_step_index": idx, "failed_step": rec,
                           "completed_steps": completed, "total_steps": total, "steps": records}
                    return
                repair_round += 1
                # 失败反馈：失败步的实际 stdout/stderr/exit_code 显式入 context
                # （否则重跑 decision 只能看到此前成功步的旧输出，返修会瞎修）
                _fd = result.get("data") if isinstance(result.get("data"), dict) else {}
                context_store["repair_feedback"] = {
                    "round": repair_round,
                    "reason": str((err or {}).get("message") or ""),
                    "detail": "命令：%s\n退出码：%s\nstdout：%s\nstderr：%s" % (
                        str((params or {}).get("command") or "")[:300],
                        _fd.get("exit_code"),
                        str(_fd.get("stdout") or "")[:1500],
                        str(_fd.get("stderr") or "")[:1500]),
                }
                # 清残留：上轮 decision 的期望值不带入下一轮（防旧值污染新判定）
                context_store.pop("expected_exit_code", None)
                _dec_raw = steps[last_decision_idx - 1]
                # 状态帧复用 retry 事件：前端 rowMark 按 step_id 原地更新，零改动可见
                yield {"event": "retry", "step_id": _dec_raw.get("step_id", last_decision_idx),
                       "retry_count": repair_round, "ts": _ts(), "tool": _DECISION_TOOL,
                       "description": str(_dec_raw.get("description") or ""),
                       "fix": "第 %d 轮返修：从 step %s 重放：%s"
                              % (repair_round, last_decision_idx, (err or {}).get("message") or "")}
                idx = last_decision_idx      # 游标重置 → while 从 decision 步重放
                continue
            # 终止：经返修仍失败 / 不可返修失败，文案区分（前端 failed 分支直显原文）
            if repair_round > 0:
                yield {"event": "final", "status": "failed",
                       "error": "返修 %d 轮后仍失败：step %s（%s）：%s"
                                % (repair_round, rec["step_id"], resolved, reason),
                       "ts": _ts(), "execution_id": task_id,
                       "failed_step_index": idx, "failed_step": rec,
                       "completed_steps": completed, "total_steps": total, "steps": records}
            else:
                yield {"event": "final", "status": "failed",
                       "error": "step %s（%s）失败，已终止后续 %d 步：%s"
                                % (rec["step_id"], resolved, total - idx, reason),
                       "ts": _ts(), "execution_id": task_id,
                       "failed_step_index": idx, "failed_step": rec,
                       "completed_steps": completed, "total_steps": total, "steps": records}
            return
        # ---- [M2 2026-10-02] 任务成功 → 旁路写记忆（与 execute_stream 出口互斥，各触发一次）----
        _write_memory_on_success(goal, steps, records,
                                 _shrink(step_outputs.get(total)), task_id)
        yield {"event": "final", "status": "success", "result": _shrink(step_outputs.get(total)),
               "ts": _ts(), "execution_id": task_id,
               "steps": records, "completed_steps": completed, "total_steps": total,
               "note": "全部 %d 步成功（L7.8 多步编排链路，不走自检重试）" % total}
    finally:
        if owns_slot:
            _ACTIVE.pop(task_id, None)
# ===================== [新增代码·结束] L7.8 多步编排：上下文自动串联 =====================
