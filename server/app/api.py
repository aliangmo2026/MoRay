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
