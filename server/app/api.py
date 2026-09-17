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


# ---------------- [v3.21.0] 一键自动指挥 Codex ----------------
# 编排逻辑单独成文件（server/app/agent_runner.py），自带 APIRouter：
#   POST /api/agent/run · GET /api/agent/tasks/<id> · POST /api/agent/tasks/<id>/stop
#   GET /api/agent/run/preflight · GET /api/agent/runs
# 这里并入本模块 router（main.py 已注册 api_router），避免改动 main.py 的路由注册顺序。
from .agent_runner import router as _agent_runner_router  # noqa: E402

router.include_router(_agent_runner_router)

# ---------------- [v3.22.0] 编排工头（大任务拆解 → 分工 → 串行队列 → 成本汇总） ----------------
# 编排逻辑单独成文件（server/app/agent_orchestrator.py），自带 APIRouter：
#   GET  /api/orchestrator/preflight · POST /api/orchestrator/decompose
#   GET/POST /api/orchestrator/plans · GET /api/orchestrator/plans/<id>
#   POST /api/orchestrator/plans/<id>/run · POST /api/orchestrator/plans/<id>/stop
# 与 v3.21 的单任务入口共用同一把「单机串行」执行闸门（详见 agent_orchestrator._PlanToken）。
from .agent_orchestrator import router as _orchestrator_router  # noqa: E402

router.include_router(_orchestrator_router)

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

