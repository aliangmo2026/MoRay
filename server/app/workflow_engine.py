# -*- coding: utf-8 -*-
r"""路线三：轻量级可视化编排画布引擎（2026-11-08）

前端 moray-panels「工作流画布」面板把画布上的节点/连线收集成 JSON，
POST /api/workflow/run 受理 → 引擎按 depends_on（或 edges）拓扑序**串行**执行，
逐事件写入 _RUNS[run_id]["events"]，前端经 GET /api/workflow/stream/<run_id>
（SSE）实时着色：等待=灰 / 运行中=闪烁蓝 / 成功=绿 / 失败=红 / 被短路=暗灰。

设计红线（全部继承既有机房纪律）：
  * 器官调用一律走 mcp_gateway.execute_chat_tool（信封 + agent_tool_log 审计
    + 分级超时现成；与审查委员会同一条调用面，不另起 HTTP 直连）；
  * 节点动作目录是白名单硬编码（NODE_TOOLS），前端传什么 tool 名都必须在白名单内，
    绝不开任意 MCP 调用面（防把画布变成任意器官调用旁路）；
  * 拒绝把 D:\MoRay 自身当 Surgeon 写目标（preset-coder 铁律同源——本引擎的
    surgeon 节点默认动作 list_available_actions 只读零写盘，天然安全；
    会写盘的 execute_plan / refactor_smart 不进画布白名单）；
  * SSE 只读广播，零写库零写盘；运行记录只留内存（与审查委员会 _JOBS 同款），
    权威留痕在 agent_tool_log 与 %TEMP% mcp 日志；
  * 本模块日志自挂 handler（moray root logger 只放 ERROR，INFO 全被吞——
    memory_extractor 同款坑），落 logs/workflow_engine.log。
"""
import asyncio
import json
import logging
import logging.handlers
import os
import py_compile
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("moray.workflow_engine")

# ---- 日志自挂 handler（moray root logger 只放 ERROR；不自挂 INFO 全被吞）----
try:
    _LOG_DIR = r"D:\MoRay\logs\workflow_engine.log"
    os.makedirs(os.path.dirname(_LOG_DIR), exist_ok=True)
    if not any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "workflow_tag", False)
        for h in logger.handlers
    ):
        _tagged = logging.handlers.RotatingFileHandler(
            _LOG_DIR, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        _tagged.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _tagged.workflow_tag = True  # type: ignore[attr-defined]
        logger.addHandler(_tagged)
        logger.setLevel(logging.INFO)
except Exception:  # noqa: BLE001 - 日志面缺失不影响功能
    pass

router = APIRouter()

# ---------------------------------------------------------------- 节点动作目录（白名单）
# server="builtin" 的条目走内置处理器；其余走 mcp_gateway.execute_chat_tool。
NODE_TOOLS: dict = {
    "argus": {
        "label": "Argus 搜索",
        "desc": "search_web 真实联网检索（query/max_results 可在节点参数覆盖）",
        "server": "argus",
        "tool": "search_web",
        "default_params": {"query": "MoRay 自治智能体框架", "max_results": 3},
    },
    "surgeon": {
        "label": "Surgeon 重构",
        "desc": "list_available_actions 只读动作目录（零写盘）",
        "server": "surgeon",
        "tool": "list_available_actions",
        "default_params": {},
    },
    "scribe": {
        "label": "Scribe 审查",
        "desc": "scribe_scan 真实扫描目标目录 → out/symbol-graph.json",
        "server": "scribe",
        "tool": "scribe_scan",
        "default_params": {"path": r"D:\MoRay\workspace\workflow_demo\demo_calc",
                           "include": ["*.py"]},
    },
    "zcode": {
        "label": "ZCode 验证",
        "desc": "py_compile 独立语法闸（审查委员会同款权威判据，轻量模式）",
        "ro": True,
        "server": "builtin",
        "handler": "zcode_verify",
        "default_params": {
            "target_file": r"D:\MoRay\workspace\workflow_demo\demo_calc\calc.py"},
    },
}

#: 各节点参数允许覆盖的键（白名单内才接受前端覆盖，其余丢弃）
_OVERRIDABLE = {
    "argus": ("query", "max_results"),
    "scribe": ("path",),
    "zcode": ("target_file",),
}

# ---------------------------------------------------------------- 内存台账
_RUNS_MAX = 20
_RUNS: dict = {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _emit(rec: dict, ev: dict) -> None:
    """追加一条事件（SSE 广播面只消费 rec["events"]；list.append 单事件原子）。"""
    rec["events"].append(ev)


# ---------------------------------------------------------------- 归一化 + 拓扑序
def _normalize(payload: dict) -> tuple:
    """画布 JSON → (nodes_by_id, order, dep, 错误串)。

    接受两种依赖表达：edges=[{from,to}]（画布连线）或 node.depends_on（str|list）。
    order = Kahn 拓扑序的节点 id 列表（串行执行顺序）；检测循环依赖/未知工具/重复 id。
    """
    nodes = payload.get("nodes") or []
    edges_in = payload.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        return {}, (), {}, "nodes 必须是非空数组"
    nodes_by_id = {}
    id_list = []
    for n in nodes:
        if not isinstance(n, dict):
            return {}, (), {}, "nodes 元素必须是对象"
        nid = str(n.get("id") or "").strip()
        tool = str(n.get("tool") or "").strip()
        if not nid or nid in nodes_by_id:
            return {}, (), {}, "节点 id 缺失或重复：%s" % (nid or "(空)")
        if tool not in NODE_TOOLS:
            return {}, (), {}, "未知工具：%s（白名单=%s）" % (tool, ",".join(NODE_TOOLS))
        nodes_by_id[nid] = n
        id_list.append(nid)
    # 依赖边集：dep[to] = [from,...]（to 必须等全部 from 完成才轮到它）
    dep: dict = {}
    for n in nodes:
        d = n.get("depends_on")
        if d:
            deps = [d] if isinstance(d, str) else list(d or ())
            dep[str(n["id"])] = [str(x) for x in deps]
    for e in edges_in:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            dep.setdefault(str(e["to"]), []).append(str(e["from"]))
    all_ids = set(id_list)
    for to_id, froms in dep.items():
        if to_id not in all_ids:
            return {}, (), {}, "依赖目标不存在：%s" % to_id
        for f in froms:
            if f not in all_ids:
                return {}, (), {}, "依赖来源不存在：%s -> %s" % (f, to_id)
    # Kahn 拓扑序：indeg[to] = 去重后的依赖数；ready 队列按画布出现顺序。
    # 注意：Kahn 在 dep_work（工作副本）上消边，dep 原样返回——编排器的
    # 「依赖失败→短路」检查还要读原始依赖图，原地消边会把它清空。
    dep_work = {k: list(v) for k, v in dep.items()}
    indeg = {nid: len(set(dep_work.get(nid) or ())) for nid in id_list}
    ready = [nid for nid in id_list if indeg[nid] == 0]
    order = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for to_id, froms in dep_work.items():
            if nid in froms:
                froms.remove(nid)
                indeg[to_id] -= 1
                if indeg[to_id] == 0:
                    ready.append(to_id)
    if len(order) != len(id_list):
        return {}, (), {}, "依赖成环：拓扑序无法覆盖全部节点"
    return nodes_by_id, order, dep, ""


# ---------------------------------------------------------------- 节点执行步
async def _call_mcp(server: str, tool: str, args: dict) -> dict:
    """统一 organ 调用面（execute_chat_tool：信封+审计+分级超时）。惰性 import 防环。"""
    from . import mcp_gateway
    return await mcp_gateway.execute_chat_tool(server, tool, args)


def _detail_of(env: dict) -> str:
    """信封 → 一行人话 detail。"""
    data = env.get("data") if isinstance(env, dict) else None
    if not isinstance(data, dict):
        return json.dumps(env, ensure_ascii=False, default=str)[:300]
    if isinstance(data.get("results"), list):
        return "检索到 %d 条结果" % len(data["results"])
    text = str(data.get("text") or data.get("message") or "")
    out_path = str(data.get("out_path") or "")
    if out_path:
        return "产物 %s" % out_path
    return (text or json.dumps(data, ensure_ascii=False, default=str))[:300]


async def _run_mcp_node(tool_id: str, params: dict) -> dict:
    """MCP 器官节点：白名单目录取 server/tool，params 合并默认值后调用。"""
    spec = NODE_TOOLS[tool_id]
    args = dict(spec.get("default_params") or {})
    for k in _OVERRIDABLE.get(tool_id, ()):
        if k in params:
            args[k] = params[k]
    env = await _call_mcp(spec["server"], spec["tool"], args)
    ok = str(env.get("code")) == "OK"
    return {"ok": ok, "detail": _detail_of(env),
            "error": "" if ok else str(env.get("code")) + " " + _detail_of(env)[:200]}


async def _run_zcode_node(params: dict) -> dict:
    """ZCode 验证节点（内置处理器）：py_compile 独立语法闸，审查委员会同款权威判据。"""
    merged = dict(NODE_TOOLS["zcode"].get("default_params") or {})
    merged.update(params or {})
    tf = str(merged.get("target_file") or "").strip()
    if not tf:
        return {"ok": False, "detail": "", "error": "target_file 参数缺失"}
    if not os.path.isfile(tf):
        return {"ok": False, "detail": "", "error": "目标文件不存在：%s" % tf}
    stage = os.path.join(r"D:\MoRay\workspace\workflow_demo", "_verify_stage")
    os.makedirs(stage, exist_ok=True)
    cfile = os.path.join(stage, "check.pyc")
    try:
        await asyncio.to_thread(py_compile.compile, tf, cfile=cfile, doraise=True)
        return {"ok": True, "detail": "py_compile 通过：%s" % tf, "error": ""}
    except py_compile.PyCompileError as e:
        return {"ok": False, "detail": "", "error": "py_compile 失败：%s" % str(e)[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": "", "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


async def _exec_node(tool_id: str, params: dict) -> dict:
    """单节点执行分派：builtin→内置处理器，否则 MCP 器官。返回 {ok, detail, error}。"""
    if NODE_TOOLS[tool_id].get("server") == "builtin":
        return await _run_zcode_node(params)
    return await _run_mcp_node(tool_id, params)


# ---------------------------------------------------------------- 编排器
async def run_workflow(run_id: str, payload: dict) -> None:
    """编排一次运行：拓扑序逐节点执行，fail-fast，下游标 skipped；事件全量上台账。"""
    rec = _RUNS.get(run_id)
    if rec is None:
        return
    nodes_by_id, order, dep, err = _normalize(payload)
    if err:
        rec["status"] = "failed"
        _emit(rec, {"ts": _now(), "type": "workflow", "status": "failed", "verdict": err})
        return
    rec["order"] = list(order)
    _emit(rec, {"ts": _now(), "type": "workflow", "status": "started",
                "verdict": "串行执行 %d 个节点：%s" % (len(order), " → ".join(order))})

    for nid in order:
        node = nodes_by_id[nid]
        tool_id = str(node.get("tool"))
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        # 短路：任一依赖失败 → skipped
        bad_dep = [f for f in (dep.get(nid) or ()) if _node_status(rec, f) == "failed"]
        if bad_dep:
            rec["node_status"][nid] = "skipped"
            _emit(rec, {"ts": _now(), "type": "node", "node_id": nid,
                        "status": "skipped",
                        "detail": "依赖 %s 失败，被短路" % ",".join(bad_dep)})
            continue
        rec["node_status"][nid] = "running"
        _emit(rec, {"ts": _now(), "type": "node", "node_id": nid,
                    "status": "running", "detail": NODE_TOOLS[tool_id]["label"] + " 执行中"})
        t0 = time.monotonic()
        try:
            res = await _exec_node(tool_id, params)
        except Exception as e:  # noqa: BLE001 - 任何异常都落红不吞
            res = {"ok": False, "detail": "",
                   "error": "%s: %s" % (type(e).__name__, str(e)[:240])}
        ms = int((time.monotonic() - t0) * 1000)
        rec["node_status"][nid] = "success" if res["ok"] else "failed"
        ev = {"ts": _now(), "type": "node", "node_id": nid,
              "status": "success" if res["ok"] else "failed",
              "detail": (res.get("detail") or res.get("error") or "")[:400],
              "elapsed_ms": ms}
        _emit(rec, ev)
        logger.info("[wf %s] node %s(%s) -> %s %dms %s", run_id, nid, tool_id,
                    ev["status"], ms, ev["detail"][:120])

    ok_count = sum(1 for s in rec["node_status"].values() if s == "success")
    fail_count = sum(1 for s in rec["node_status"].values() if s == "failed")
    verdict = ("全部通过：%d/%d 节点绿" % (ok_count, len(order)) if fail_count == 0
               else "未全绿：%d 过 / %d 败（下游已短路）" % (ok_count, fail_count))
    rec["status"] = "done" if fail_count == 0 else "failed"
    rec["verdict"] = verdict
    _emit(rec, {"ts": _now(), "type": "workflow", "status": rec["status"], "verdict": verdict})
    logger.info("[wf %s] finished %s %s", run_id, rec["status"], verdict)


def _node_status(rec: dict, nid: str) -> str:
    return str((rec.get("node_status") or {}).get(nid) or "pending")


# ---------------------------------------------------------------- 后台任务
def _spawn(run_id: str, payload: dict) -> None:
    """asyncio 任务强引用防 GC；崩溃也落台账不吞。"""
    rec = _RUNS.get(run_id) or {}

    def _on_done(t: "asyncio.Task") -> None:
        r = _RUNS.get(run_id) or {}
        r.pop("_task", None)
        try:
            t.result()
        except Exception as e:  # noqa: BLE001
            r["status"] = "failed"
            _emit(r, {"ts": _now(), "type": "workflow", "status": "failed",
                      "verdict": "workflow_crash: %s: %s" % (type(e).__name__, str(e)[:200])})

    task = asyncio.get_running_loop().create_task(run_workflow(run_id, payload))
    rec["_task"] = task
    task.add_done_callback(_on_done)


# ---------------------------------------------------------------- HTTP 面
def _catalog() -> list:
    out = []
    for tid, spec in NODE_TOOLS.items():
        server = spec.get("server")
        out.append({"id": tid, "label": spec["label"], "desc": spec.get("desc", ""),
                    "server": ("zcode(py_compile)" if server == "builtin" else server),
                    "tool": spec.get("tool") or spec.get("handler"),
                    "default_params": spec.get("default_params") or {},
                    "readonly": bool(spec.get("ro"))})
    return out


@router.get("/api/workflow/tools")
async def workflow_tools():
    """节点动作目录（前端左侧栏数据源；带 default_params 供节点参数初始化）。"""
    return {"ok": True, "data": {"tools": _catalog()}}


@router.post("/api/workflow/run")
async def workflow_run(request: Request):
    """受理一次工作流运行：{name?, nodes:[{id,tool,label?,params?}], edges?|depends_on?}"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "message": "请求体必须是 JSON"})
    nodes_by_id, order, dep, err = _normalize(payload)
    if err:
        return JSONResponse(status_code=400, content={"ok": False, "message": err})
    run_id = uuid.uuid4().hex[:12]
    _RUNS[run_id] = {
        "run_id": run_id, "name": str(payload.get("name") or ""),
        "status": "running", "verdict": "",
        "node_status": {nid: "pending" for nid in order},
        "events": [], "created_at": _now(), "finished_at": "",
    }
    _spawn(run_id, payload)
    logger.info("[wf %s] accepted %d nodes", run_id, len(order))
    return {"ok": True, "data": {"run_id": run_id, "status": "running"}}


@router.get("/api/workflow/status/{run_id}")
async def workflow_status(run_id: str):
    """轮询兜底（SSE 不可用时前端可退化为 1s 轮询同构数据）。"""
    rec = _RUNS.get(run_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "run 不存在"})
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    return {"ok": True, "data": out}


@router.get("/api/workflow/stream/{run_id}")
async def workflow_stream(run_id: str):
    """SSE 实时事件流：先补发缓冲事件，再跟随直播；收尾事件后自动断流。"""
    rec = _RUNS.get(run_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "run 不存在"})
    if rec.get("_streaming"):
        return JSONResponse(status_code=409, content={"ok": False, "message": "该 run 已有订阅者"})
    rec["_streaming"] = True

    async def gen():
        try:
            cursor = 0
            last_out = time.monotonic()
            while True:
                events = rec.get("events") or []
                while cursor < len(events):
                    yield "data: %s\n\n" % json.dumps(events[cursor], ensure_ascii=False, default=str)
                    cursor += 1
                    last_out = time.monotonic()
                if str(rec.get("status")) != "running" and cursor >= len(events):
                    break
                if time.monotonic() - last_out > 15:
                    yield ": keepalive\n\n"
                    last_out = time.monotonic()
                await asyncio.sleep(0.4)
        finally:
            rec["_streaming"] = False

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
