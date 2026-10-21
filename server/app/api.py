"""业务 CRUD 路由（里程碑 M3：会话/消息/设置/KV）

- 全部参数化查询（见 crud.py）
- 统一返回 {ok:true, data} 或 {ok:false, code, message}
- 非法 JSON / 缺字段 → 400；不存在 → 404
"""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import crud

router = APIRouter()


def _ok(data):
    return {"ok": True, "data": data}


def _bad(code: str, message: str, status: int = 400):
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


async def _read_json(request: Request) -> dict:
    """读 JSON body，非法 → 抛 ValueError"""
    try:
        return await request.json()
    except Exception as e:  # noqa: BLE001
        raise ValueError("请求体不是合法 JSON") from e


# ---------------- conversations ----------------

@router.get("/api/conversations")
def conv_list(limit: int | None = None):
    return _ok(crud.list_conversations(limit))


@router.post("/api/conversations")
async def conv_create(request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    if not body.get("id"):
        return _bad("bad_request", "缺少 id 字段")
    try:
        return _ok(crud.upsert_conversation(body))
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"写入失败：{e}", 500)


@router.get("/api/conversations/{conv_id}")
def conv_detail(conv_id: str):
    conv = crud.get_conversation(conv_id)
    if not conv:
        return _bad("not_found", f"会话不存在：{conv_id}", 404)
    conv["messages"] = crud.list_messages(conv_id)
    return _ok(conv)


@router.put("/api/conversations/{conv_id}")
async def conv_update(conv_id: str, request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    try:
        updated = crud.update_conversation(conv_id, body)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"更新失败：{e}", 500)
    if updated is None:
        return _bad("not_found", f"会话不存在：{conv_id}", 404)
    return _ok(updated)


@router.delete("/api/conversations/{conv_id}")
def conv_delete(conv_id: str):
    try:
        ok = crud.delete_conversation(conv_id)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"删除失败：{e}", 500)
    if not ok:
        return _bad("not_found", f"会话不存在：{conv_id}", 404)
    return _ok({"deleted": conv_id})


# ---------------- messages ----------------

@router.get("/api/conversations/{conv_id}/messages")
def msg_list(conv_id: str):
    """宽松语义：会话不存在也返回 200 + 空数组（历史行为；删除/清理竞态下不报错）"""
    if not crud.get_conversation(conv_id):
        return _ok([])
    return _ok(crud.list_messages(conv_id))


@router.post("/api/conversations/{conv_id}/messages")
async def msg_create(conv_id: str, request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    if not body.get("id"):
        return _bad("bad_request", "缺少 id 字段")
    body["conversationId"] = body.get("conversationId") or conv_id
    try:
        return _ok(crud.upsert_message(body))
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"写入失败：{e}", 500)


@router.post("/api/conversations/batch-messages")
async def msg_batch(request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    msgs = body.get("messages") if isinstance(body, dict) else body
    if not isinstance(msgs, list) or not msgs:
        return _bad("bad_request", "messages 必须是非空数组")
    try:
        n = crud.upsert_messages(msgs)
        return _ok({"written": n})
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"批量写入失败：{e}", 500)


@router.delete("/api/messages/{msg_id}")
def msg_delete(msg_id: str):
    """单条消息删除（前端删除消息同步用）"""
    try:
        ok = crud.delete_message(msg_id)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"删除失败：{e}", 500)
    if not ok:
        return _bad("not_found", f"消息不存在：{msg_id}", 404)
    return _ok({"deleted": msg_id})


# ---------------- settings / kv ----------------

@router.get("/api/settings")
def settings_get():
    return _ok(crud.get_settings())


@router.put("/api/settings")
async def settings_put(request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    if not isinstance(body, dict):
        return _bad("bad_request", "settings 必须是 key/value 对象")
    try:
        n = crud.put_settings(body)
        return _ok({"written": n})
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"写入失败：{e}", 500)


@router.get("/api/kv/{key}")
def kv_get(key: str):
    return _ok({"key": key, "value": crud.kv_get(key)})


@router.put("/api/kv/{key}")
async def kv_put(key: str, request: Request):
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    value = body.get("value")
    if value is None:
        return _bad("bad_request", "缺少 value 字段")
    crud.kv_put(key, str(value))
    return _ok({"key": key, "value": str(value)})


# ---------------- [2026-10-03 旧链退役] v3.21 一键自动指挥 Codex（agent_runner.py）与 ----------------
# ---------------- v3.22 编排工头（agent_orchestrator.py）已整体退役删除：执行统一走新链 ----------------
# ---------------- （聊天 Agent → Laya 8001 /execute → zcode.* → 8005 沙箱）。原两处 router ----------------
# ---------------- 挂载随之移除；宿主路径存在性检查已迁入 agent_tools 的 GET /api/agent/path_exists。 ----------------

# ---------------- [Kernel 预埋] 事件溯源快照 ----------------
# 只读查询 + 写入钩子骨架（供未来「回放」UI 使用）。当前阶段无人自动写入：
# 写入入口是 agent_tools._audit_snapshot()，由未来回放系统在关键事件点调用。
# 注意：本段不修改任何既有路由与契约；GET /api/agent/log 保持原样。

@router.get("/api/events/snapshots")
def event_snapshots_list(event_type: str | None = None, entity_id: str | None = None, limit: int = 100, offset: int = 0):
    """分页查询事件快照（只读，供未来回放 UI 使用）"""
    try:
        rows, total = crud.list_event_snapshots(event_type, entity_id, limit, offset)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"读取快照失败：{e}", 500)
    return _ok({
        "rows": rows,
        "total": total,
        "limit": min(max(int(limit or 100), 1), 500),
        "offset": max(int(offset or 0), 0),
    })


@router.get("/api/events/snapshots/{snapshot_id}")
def event_snapshot_detail(snapshot_id: int):
    """查询单条事件快照详情"""
    row = crud.get_event_snapshot(snapshot_id)
    if not row:
        return _bad("not_found", f"快照不存在：{snapshot_id}", 404)
    return _ok(row)


@router.post("/api/events/snapshots")
async def event_snapshot_create(request: Request):
    """写入事件快照（当前为预埋钩子，仅内部调用；未来回放系统启用后开放）。
    注意：当前阶段此接口仅用于开发测试，生产环境应限制为仅本地回环调用（已由 HOST=127.0.0.1 保证）。"""
    try:
        body = await _read_json(request)
    except ValueError as e:
        return _bad("bad_json", str(e))
    if not isinstance(body, dict) or not str(body.get("event_type") or "").strip():
        return _bad("bad_request", "缺少 event_type 字段")
    snapshot = body.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, (dict, list, str, int, float, bool)):
        return _bad("bad_request", "snapshot 必须是 JSON 对象（或可序列化标量）")
    source_log_id = body.get("source_log_id")
    if source_log_id is not None:
        try:
            source_log_id = int(source_log_id)
        except (TypeError, ValueError):
            return _bad("bad_request", "source_log_id 必须是整数")
    try:
        new_id = crud.append_event_snapshot(
            str(body.get("event_type")).strip(),
            str(body.get("entity_id") or ""),
            source_log_id,
            snapshot if isinstance(snapshot, dict) else None,
        )
        return _ok({"id": new_id})
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"写入失败：{e}", 500)


@router.delete("/api/events/snapshots")
def event_snapshots_clear(event_type: str | None = None):
    """清空事件快照（带确认参数，前端二次确认后调用）"""
    try:
        n = crud.clear_event_snapshots(event_type)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", f"清空失败：{e}", 500)
    return _ok({"cleared": n})


# ---------------- [Iris P0] ZCode 沙箱真实执行接口（2026-09-27） ----------------
# 授权与依据：D:\Iris\docs\p0_audit.md（红线4"仅在现有 api.py 路由框架内新增 1 个路由"；
# 红线3"仅在现有适配器层封装接口"；既有行零修改，本节为文件尾纯追加）。
# 设计：复用 engines 引擎单例（local_adapter._default_zcode_engine）的 run_code()
# ——四态状态机/沙箱白名单/进程管理/审计钩子全复用（p0_audit §1/§5）。
# 预算熔断适配：接入 llm_proxy 既有内存计数，独立桶 "iris:zcode"（跨日清零/上限
# MAX_LLM_CALLS_PER_SESSION/429 响应体形状与 llm_proxy 一致）；不修改 llm_proxy 任何
# 代码，仅防御式只读+计数（getattr），访问失败放行并留 warning（执行不消耗 LLM token）。
# 同步执行（红线4 仅 1 路由 → 无独立轮询路由，p0_audit §6 勘误2）：asyncio.to_thread
# 调度，超时上限 30s，事件循环不被阻塞；业务结果（exit_code!=0/timeout）随 200 返回，
# 由前端按 status/error 呈现。
@router.post("/api/engines/zcode/run")
async def zcode_run(request: Request):
    import asyncio
    import logging
    import shutil

    from . import config
    from . import llm_proxy
    from .engines.local_adapter import _default_zcode_engine

    # ---- 1. 入参解析与校验（code 必填；language 默认 python；timeout 默认 10，最大 30）----
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        return _bad("bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _bad("bad_request", "请求体必须是 JSON 对象")
    code = body.get("code")
    if not isinstance(code, str) or not code.strip():
        return _bad("bad_request", "缺少 code 字段（要执行的代码）")
    language = str(body.get("language") or "python").strip().lower()
    try:
        timeout = int(float(body.get("timeout"))) if body.get("timeout") is not None else 10
    except (TypeError, ValueError):
        timeout = 10
    timeout = max(1, min(30, timeout))

    # ---- 2. 预算熔断适配（独立桶 iris:zcode；被拒请求同样继续累计——与 llm_proxy 惯例一致）----
    try:
        bucket = getattr(llm_proxy, "_session_calls", None)
        if bucket is not None:
            roll = getattr(llm_proxy, "_roll_budget_day", None)
            if callable(roll):
                roll()
            bucket["iris:zcode"] = bucket.get("iris:zcode", 0) + 1
            if bucket["iris:zcode"] > config.MAX_LLM_CALLS_PER_SESSION:
                return JSONResponse(status_code=429, content={
                    "ok": False,
                    "error": "session_budget_exceeded",
                    "msg": "ZCode 执行桶（iris:zcode）已达今日上限（%d次）" % config.MAX_LLM_CALLS_PER_SESSION,
                    "message": "ZCode 执行次数已达今日上限（%d 次），明日自动清零后可继续" % config.MAX_LLM_CALLS_PER_SESSION,
                })
    except Exception as e:  # noqa: BLE001 - 熔断读取失败不阻断执行（如实留痕）
        logging.getLogger("moray.zcode").warning("zcode budget gate read failed: %s", e)

    # ---- 3. 语言白名单前置校验（引擎内同样校验，双保险；解释器缺失明确报错）----
    if language not in {"python", "python3", "javascript", "js"}:
        return _bad("unsupported_language", "不支持的语言：%r（支持 python / javascript）" % language)
    if language in ("javascript", "js") and not shutil.which("node"):
        return _bad("interpreter_not_found", "未在 PATH 找到 Node.js，无法执行 javascript")

    # ---- 4. 沙箱内真实执行（引擎受理校验/审计/状态机全复用；失败只影响本请求）----
    result = await asyncio.to_thread(_default_zcode_engine.run_code, code, language, timeout)
    return _ok(result)


# ---- [p_mcp 阶段4] 加性挂载外部适配路由 /api/adapters/*（偏差 D6）----
# 挂载方式先例 = 本文件旧版 agent_runner/orchestrator 的 include 折叠（该两模块 2026-10-03 已退役删除）；
# 仅追加以下两行，上方任何一行未动。逻辑全在 D:\MoRay\adapters\（独立新目录）。
from adapters.routes import router as _adapters_router  # noqa: E402
router.include_router(_adapters_router)

# ---- [L4 最小验证 2026-09-28] 加性挂载对话触发任务编排 /api/tasks/*（同一偏差 D6 折叠先例）----
# 仅追加以下两行；逻辑全在 server/app/task_routes.py（纯追加新模块），上方任何一行未动。
from .task_routes import router as _tasks_router  # noqa: E402
router.include_router(_tasks_router)


# ---------------- [引擎解耦 v1 2026-10-21] 通用引擎任务派发（收编 Day24b 手写 ZCode 流式管线） ----------------
# Day24b 曾在此手写「受理 + SSE 直通 + kill + files」四件套（自持 _ZCODE_STREAM_TASKS/_PROCS
# 双表 + reader 线程，绕过 ExecutionEngine 契约）。现已整体收编进 engines/zcode_adapter.py
# （ZCodeAdapter.start_stream / stream_events / kill_stream_task / read_task_file 四方法，
# 错误契约 StreamTaskError 定义在 engines/base.py）。
# 本节改为通用 /api/engines/{engine_name}/* 派发：引擎经 local_adapter.get_engine_by_name
# 从注册表（all_engines）解析；缺流式扩展面的引擎回 engine_unsupported；未知引擎 404。
# 历史兼容（红线）：ZCode 原路径 /api/engines/zcode/* 由 {engine_name} 通配天然覆盖，
# 前端（89/92/125）与 Laya tool_registry 的 URL 零改动。
# 事件形状基线（Day24a 真机样本标定，docs/day24a_streamjson_schema.md）：白名单 5 类、
# data: 原样透传不重序列化、[DONE] 哨兵——语义全部保留在 adapter 侧，逐字未动。

from .engines.base import StreamTaskError  # noqa: E402
from .engines.local_adapter import get_engine_by_name  # noqa: E402
from .engines.zcode_adapter import (  # noqa: E402  兼容别名：tests/organ_memory_selftest.py 直引
    _ZCODE_STREAM_PROCS,
    _ZCODE_STREAM_TASKS,
    _zcode_stream_reader,
)


def _stream_engine_or_bad(engine_name: str):
    """通用派发前置：解析引擎 + 校验流式扩展面四方法。

    返回 (engine, None) 或 (None, _bad 响应)。缺方法的引擎（如 LocalAdapter）如实回
    engine_unsupported——准入在派发前判掉，不让任务跑一半才发现不支持（base.py 设计要点）。
    """
    engine = get_engine_by_name(engine_name)
    if engine is None:
        return None, _bad("engine_not_found", f"未知执行引擎：{engine_name}", 404)
    missing = [m for m in ("start_stream", "stream_events",
                           "kill_stream_task", "read_task_file")
               if not callable(getattr(engine, m, None))]
    if missing:
        return None, _bad("engine_unsupported",
                          f"引擎 {engine_name} 缺少流式扩展方法：{', '.join(missing)}", 400)
    return engine, None


@router.post("/api/engines/{engine_name}/tasks")
async def create_engine_task(engine_name: str, request: Request):
    """[引擎解耦 v1] 通用流式任务受理：POST /api/engines/<name>/tasks。

    引擎经注册表（local_adapter.all_engines）解析；出参按 Day24b 契约逐字（扁平形，
    有意不走 _ok(data) 包裹——与 GET /stream 的 SSE 契约配对，前端按 task_id 直读）；
    语义收编在 ZCodeAdapter.start_stream（spawn CLI stream-json 即刻返回）。
    ZCode 原路径 /api/engines/zcode/tasks 由 {engine_name} 通配覆盖，前端/Laya 零改动。
    """
    engine, err = _stream_engine_or_bad(engine_name)
    if err is not None:
        return err
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _bad("bad_request", "请求体必须是 JSON 对象")
    try:
        return engine.start_stream(body)
    except StreamTaskError as e:
        if e.body is not None:
            return JSONResponse(status_code=e.status, content=e.body)
        return _bad(e.code, e.message, e.status)


@router.post("/api/engines/{engine_name}/tasks/{task_id}/kill")
async def kill_engine_stream_task(engine_name: str, task_id: str):
    """[引擎解耦 v1] 通用流式任务终止：POST /api/engines/<name>/tasks/<id>/kill。

    语义收编在 ZCodeAdapter.kill_stream_task（查常登表收进程树、poll 非空不误杀、
    kill 与弹表解耦）；出参与原路由逐字一致。
    """
    engine, err = _stream_engine_or_bad(engine_name)
    if err is not None:
        return err
    try:
        return await engine.kill_stream_task(task_id)
    except StreamTaskError as e:
        if e.body is not None:
            return JSONResponse(status_code=e.status, content=e.body)
        return _bad(e.code, e.message, e.status)


@router.get("/api/engines/{engine_name}/stream/{task_id}")
async def engine_stream_events(engine_name: str, task_id: str):
    """[引擎解耦 v1] 通用 SSE 订阅：GET /api/engines/<name>/stream/<id>。

    data: <原始行>\\n\\n 逐条透传，[DONE] 后关闭；语义收编在 ZCodeAdapter.stream_events
    （15s keep-alive、流结束剔登记表、未知任务 raise StreamTaskError("not_found")）。
    响应头沿用 llm_proxy.py 既有 SSE 惯例（Cache-Control no-cache + X-Accel-Buffering no）。
    """
    from fastapi.responses import StreamingResponse

    engine, err = _stream_engine_or_bad(engine_name)
    if err is not None:
        return err
    try:
        gen = engine.stream_events(task_id)
    except StreamTaskError as e:
        if e.body is not None:
            return JSONResponse(status_code=e.status, content=e.body)
        return _bad(e.code, e.message, e.status)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------- [引擎解耦 v1] 任务产物预览：GET /api/engines/{engine_name}/files/{task_id}/{filename} ----------------
# 安全设计（spec 逐条：穿越前置拒绝/uuid4.hex 校验/realpath 包含性/保留设备名/512KB 闸门/
# MIME 按扩展名/全程不 base64）全部收编在 ZCodeAdapter.read_task_file，本路由只做派发与
# Response 组装；413 spec 逐字 body 经 StreamTaskError.body 透传。

@router.get("/api/engines/{engine_name}/files/{task_id}/{filename}")
async def engine_task_file(engine_name: str, task_id: str, filename: str):
    """[引擎解耦 v1] 通用任务产物预览：读取任务沙箱内单个文件（原始内容直出）。

    成功 (media_type, content)：图片 content 为 bytes、文本为 str（UTF-8 replace）。
    """
    from fastapi.responses import Response

    engine, err = _stream_engine_or_bad(engine_name)
    if err is not None:
        return err
    try:
        media, content = engine.read_task_file(task_id, filename)
    except StreamTaskError as e:
        if e.body is not None:
            return JSONResponse(status_code=e.status, content=e.body)
        return _bad(e.code, e.message, e.status)
    return Response(content=content, media_type=media)



# ---- [L5.2 2026-09-28] 加性挂载主动日志监控 /api/monitor/*（同一偏差 D6 折叠先例）----
# 仅追加以下两行；逻辑全在 server/app/log_monitor.py + monitor_routes.py，上方未动。
from .monitor_routes import router as _monitor_router  # noqa: E402
router.include_router(_monitor_router)

# ---- [L6.0 2026-09-29] 加性挂载自我评估 /api/self/*（monitor 同款两行折叠）----
from .self_routes import router as _self_router  # noqa: E402
router.include_router(_self_router)

# ---- [L7.0 2026-09-29] 加性挂载任务自治引擎 /api/autonomous/*（同款折叠；总闸默认关）----
from .planner_routes import router as _planner_router  # noqa: E402
router.include_router(_planner_router)

# ---- [L7.5 2026-09-29] 加性挂载 Laya 联动上行回调 /api/laya/*（同款两行折叠；总闸默认关）----
# 逻辑全在 server/app/laya_event_handler.py + engine/laya_connector.py，上方任何一行未动。
from .laya_event_handler import router as _laya_router  # noqa: E402
router.include_router(_laya_router)

# ---- [M1 2026-10-02] 加性挂载 MemoryBridge 记忆模块 /api/memory/*（同款两行折叠）----
# 逻辑全在 server/app/memory_routes.py + memory_db.py（独立记忆库，主库 db.py 零改动），上方任何一行未动。
from .memory_routes import router as _memory_router  # noqa: E402
router.include_router(_memory_router)

# ---- [反思MVP 2026-10-04] 加性挂载反思循环 /api/reflection/*（同款两行折叠；总闸 MORAY_REFLECTION 默认关）----
# 逻辑全在 server/app/reflection.py：读 task_result → Ollama 提炼 → 写回 insight。
# memory_routes.insert_memory 已抽为共用落库函数（save 端点行为不变），其余上方任何一行未动。
from .reflection import router as _reflection_router  # noqa: E402
router.include_router(_reflection_router)

# ---- [Synapse v2 2026-10-06] 加性挂载神经总线只读端点 /api/synapse/*（同款两行折叠）----
# 逻辑全在 server/app/synapse_routes.py（独立总线库 synapse.sqlite3 的只读查询，不查总闸——
# 关闸时表为空端点如实返回零值），上方任何一行未动。
from .synapse_routes import router as _synapse_router  # noqa: E402
router.include_router(_synapse_router)
