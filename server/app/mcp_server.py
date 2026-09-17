"""MCP（Model Context Protocol）兼容层：把本机工具暴露为标准 MCP Server（SSE 传输）

用途：让 Claude Desktop / Cursor 等外部 MCP 客户端，通过标准协议调用 MoRay 的 11 个本机工具。

安全模型（与 /api/agent/tool 完全同源，绝不另起一套）：
1. **唯一执行入口**：所有工具调用都走 agent_tools._execute_tool_internal —— 与前端/HTTP 直调
   共用同一份 _safe_path 七层路径防护、DANGEROUS_EXTS 后缀拒绝、CMD_WHITELIST 命令白名单、
   SIDE_EFFECT_TOOLS 审批双保险、错误截断与审计落库。本模块不重新实现任何工具、不放宽任何校验。
2. **副作用工具默认禁止**：SIDE_EFFECT_TOOLS（write_file/create_file/move_file/run_command/edit_file）
   只有在白名单里才允许被 MCP 调用；白名单默认只含只读工具（config.MCP_ALLOWED_TOOLS），
   于是"开箱即用 = 外部客户端只能读，不能写"。白名单内的副作用工具调用自动 approved=true，
   但其路径/后缀/命令白名单等全部防护照旧生效。
3. **全量审计**：每次调用（含被白名单拒绝、参数不合法、安全拒绝、执行失败）都写 agent_tool_log，
   detail 前缀 "source=mcp"（不改变表结构与 GET /api/agent/log 契约），可在 MoRay 前端「飞行记录仪」
   时间轴里看到来源。
4. **不泄漏内部堆栈**：所有异常都转成可读文案；内部错误只落服务端日志。

传输（MCP SSE）：
    GET  /api/mcp/sse  → 建 SSE 长连接，先发 `event: endpoint` 告知 POST 地址（带 sessionId），
                         之后把该会话的 JSON-RPC 响应以 `event: message` 推送。
    POST /api/mcp/sse?sessionId=xxx → 收 JSON-RPC 请求，处理后经 SSE 推送响应，HTTP 返回 202。
    多客户端：_SESSIONS 字典管理 session_id → asyncio.Queue，互不干扰。
    便利模式：POST 未带 sessionId（或会话已失效）时，直接把 JSON-RPC 结果放在本次 HTTP 响应里返回 ——
    便于 curl/浏览器手工验证；标准客户端始终带 sessionId，行为不受影响。

协议：JSON-RPC 2.0 / MCP 2024-11-05（兼容 2025-03-26、2025-06-18）。
支持方法：initialize / notifications/initialized / notifications/cancelled / ping / tools/list / tools/call。
错误码：-32700 Parse error / -32600 Invalid Request / -32601 Method not found /
        -32602 Invalid params / -32603 Internal error。
"""
import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import agent_tools, config, crud

router = APIRouter()
_logger = logging.getLogger("moray.mcp")

# ---- 会话与传输参数 ----
# 单会话未消费消息上限：客户端断连但 POST 仍在写时，避免内存无限增长
_QUEUE_MAX = 256
# SSE 心跳间隔（秒）：长时间无消息时发一行注释保活，防止中间层掐断空闲连接
_KEEPALIVE_SECONDS = 15.0
_KV_ALLOWED_TOOLS = "mcp_allowed_tools"

# 支持的 MCP 协议版本（客户端请求的版本在其中则回显，否则回退默认版本）
_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
_DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# session_id → asyncio.Queue（每条为一条待推送的 JSON-RPC 响应字符串）
_SESSIONS: dict[str, asyncio.Queue] = {}


# ================================================================ 工具清单

# 每个工具的 MCP 描述与 JSON Schema。
# 参数名严格对齐 agent_tools 各 _tool_* 实现里真正读取的键（不是猜的），例如
# move_file 用 from/to（src/dst 为别名）、edit_file 用 old_str/new_str（不是 old_string/new_string）、
# read_file 有 maxBytes、write_file 有 overwrite、search_text 有 glob/regex。
_TOOL_SPECS: dict[str, dict] = {
    "list_directory": {
        "description": "列出工作区内某个目录的直接子项（文件名/类型/大小/修改时间）。path 省略或为空表示工作区根目录。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的目录路径，省略 = 工作区根目录；不允许绝对路径或 .. 越界"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "read_file": {
        "description": "读取工作区内一个 UTF-8 文本文件的内容（默认最多 64KB / 2000 行，超出会截断并标注）。二进制或非 UTF-8 文件会被拒绝。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径，例如 notes/a.txt"},
                "maxBytes": {"type": "integer", "minimum": 1024, "maximum": 1048576, "description": "读取字节上限（1024-1048576，默认 65536）"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    "write_file": {
        "description": "把文本写入工作区内的文件（父目录自动创建）。默认覆盖同名文件，overwrite=false 时仅新建。危险/可执行后缀（.exe/.bat/.ps1 等）会被拒绝。副作用工具：需要 MCP 白名单放行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的目标文件路径"},
                "content": {"type": "string", "description": "要写入的完整文本内容（UTF-8）"},
                "overwrite": {"type": "boolean", "description": "false = 文件已存在则拒绝（不覆盖）；默认 true"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    "create_file": {
        "description": "在工作区内新建文件，已存在则拒绝（绝不覆盖）。副作用工具：需要 MCP 白名单放行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的新建文件路径"},
                "content": {"type": "string", "description": "文件初始文本内容，可省略（默认空文件）"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    "move_file": {
        "description": "移动或重命名工作区内的文件/目录（目标已存在会被拒绝，不做静默覆盖）。副作用工具：需要 MCP 白名单放行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "源路径（相对工作区）；也可写作 src"},
                "to": {"type": "string", "description": "目标路径（相对工作区）；也可写作 dst"},
            },
            "required": ["from", "to"],
            "additionalProperties": False,
        },
    },
    "run_command": {
        "description": "执行一条只读白名单命令：git status/log/diff/branch、python|node|npm --version、where <名称>（无 shell、无管道、无重定向，单命令 15 秒超时）。副作用工具：需要 MCP 白名单放行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "命令名，必须在白名单内：git / python / python3 / node / npm / where"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "参数数组（逐个校验，含 shell 元字符会被拒绝）；例如 [\"status\"]"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "find_files": {
        "description": "在工作区内按文件名通配符递归查找（只读，最多 1000 条；不跟随符号链接/junction）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "文件名通配符，如 *.py（默认 *），大小写不敏感"},
                "path": {"type": "string", "description": "起始目录（相对工作区），省略 = 工作区根目录"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "search_text": {
        "description": "在工作区内做全文检索（只读，默认最多 200 条命中；二进制/非 UTF-8 文件跳过并计数）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的文本；regex=true 时按正则解释"},
                "path": {"type": "string", "description": "起始目录（相对工作区），省略 = 工作区根目录"},
                "glob": {"type": "string", "description": "只看匹配该通配符的文件名，如 *.md"},
                "regex": {"type": "boolean", "description": "true = query 按正则表达式处理（默认 false）"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "edit_file": {
        "description": "把文件中的 old_str 精确替换为 new_str（默认要求唯一匹配；replace_all=true 全部替换）。副作用工具：需要 MCP 白名单放行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对工作区的目标文件路径"},
                "old_str": {"type": "string", "description": "要被替换的原文片段（含空白换行都要一致；匹配 0 处或多处会报错）"},
                "new_str": {"type": "string", "description": "替换后的新内容（可为空字符串 = 删除该片段）"},
                "replace_all": {"type": "boolean", "description": "true = 替换全部匹配（默认 false，要求唯一匹配）"},
            },
            "required": ["path", "old_str", "new_str"],
            "additionalProperties": False,
        },
    },
    "list_processes": {
        "description": "查看本机进程列表（只读，按内存占用排序；只返回进程名/pid/内存，不含命令行）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "返回条数上限（1-200，默认 30）"},
                "sort": {"type": "string", "enum": ["memory", "pid"], "description": "排序方式：memory（内存降序，默认）或 pid"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    "system_info": {
        "description": "查看本机系统概览（只读）：操作系统/CPU 核数/内存/磁盘/工作区路径，不含用户名与环境变量。",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}


# ================================================================ 白名单

def allowed_tools() -> set[str]:
    """已放行的工具集合（只读工具恒在其中；副作用工具需显式配置）。

    优先级：kv(mcp_allowed_tools) > config.MCP_ALLOWED_TOOLS（含 MORAY_MCP_ALLOWED_TOOLS 环境变量）。
    kv 值支持 JSON 数组 ["read_file","write_file"] 或逗号串 "read_file,write_file"；
    未知工具名直接忽略（防手滑拼错造成"以为放行了其实没有"以外的不确定状态）。
    """
    names: list[str] = []
    raw = ""
    try:
        raw = str(crud.kv_get(_KV_ALLOWED_TOOLS) or "").strip()
    except Exception as e:  # noqa: BLE001 - kv 读取失败一律回落到配置默认值
        _logger.warning("mcp whitelist kv read failed: %s", e)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                names = [str(x) for x in parsed]
            elif isinstance(parsed, str):
                names = parsed.split(",")
        except (ValueError, TypeError):
            names = raw.split(",")
    if not names:
        names = [str(x) for x in config.MCP_ALLOWED_TOOLS]
    out = set()
    for n in names:
        n = str(n).strip()
        if n and n in agent_tools.TOOL_IMPLS:
            out.add(n)
    return out


def is_tool_allowed(name: str) -> bool:
    """该工具是否允许被 MCP 调用：只读工具恒允许；副作用工具必须在白名单内。"""
    if name not in agent_tools.SIDE_EFFECT_TOOLS:
        return True
    return name in allowed_tools()


def list_tools() -> list[dict]:
    """tools/list 的返回（11 个本机工具；副作用工具在描述里标注需白名单）。"""
    tools = []
    for name in agent_tools.TOOL_IMPLS:
        spec = _TOOL_SPECS.get(name) or {}
        side_effect = name in agent_tools.SIDE_EFFECT_TOOLS
        note = ("【副作用工具：默认禁止 MCP 调用，需在 MCP 白名单显式放行】" if side_effect
                else "【只读工具：可直接调用】")
        tools.append({
            "name": name,
            "description": (str(spec.get("description") or "") + " " + note).strip(),
            "inputSchema": spec.get("inputSchema") or {"type": "object", "properties": {}, "required": []},
            "annotations": {
                "readOnlyHint": not side_effect,
                "destructiveHint": side_effect,
                "idempotentHint": not side_effect,
                "openWorldHint": False,
            },
        })
    return tools


# ================================================================ JSON-RPC

def _rpc_result(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _rpc_error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": int(code), "message": str(message)}}


def _content_text(text: str, is_error: bool = False) -> dict:
    """MCP tools/call 结果体：{content: [{type:"text", text}], isError}"""
    return {"content": [{"type": "text", "text": str(text)}], "isError": bool(is_error)}


def _init_result(params: dict) -> dict:
    """initialize 握手结果：protocolVersion / capabilities / serverInfo / instructions"""
    want = str(params.get("protocolVersion") or "").strip()
    version = want if want in _PROTOCOL_VERSIONS else _DEFAULT_PROTOCOL_VERSION
    try:
        ws = str(agent_tools._workspace_root(create=False))
    except Exception:  # noqa: BLE001 - 工作区解析失败不影响握手
        ws = str(config.WORKSPACE_DEFAULT)
    allowed = sorted(allowed_tools())
    instructions = (
        "MoRay 本机工具（受控工作区）。工作区根目录：%s。"
        "全部路径限制在工作区内，越界、危险后缀与白名单外的命令会被安全层拒绝。"
        "只读工具可直接调用；副作用工具（写文件/新建/移动/执行命令/编辑）默认禁止 MCP 调用，"
        "需要在 MoRay 后端显式放行（config.MCP_ALLOWED_TOOLS 或 kv:mcp_allowed_tools）。"
        "所有调用都会写入审计日志（detail 标注 source=mcp）。"
        "当前放行的工具：%s。"
    ) % (ws, ", ".join(allowed) if allowed else "（无）")
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}, "logging": {}},
        "serverInfo": {
            "name": config.MCP_SERVER_NAME,
            "title": "MoRay 本机工具",
            "version": config.VERSION,
            "build": config.BUILD,
        },
        "instructions": instructions,
    }


def _call_tool(mid, params: dict) -> dict:
    """tools/call：白名单 → 唯一执行入口 → 结果转 MCP content。

    - 协议级错误（缺工具名 / arguments 非对象 / 未知工具）→ JSON-RPC -32602；
    - 工具级结果（白名单拒绝 / 安全拒绝 / 执行失败）→ result.isError = true（MCP 标准做法）。
    """
    name = params.get("name")
    if not isinstance(name, str) or not name.strip():
        return _rpc_error(mid, -32602, "Invalid params：tools/call 需要 name（工具名）")
    name = name.strip()
    if name not in agent_tools.TOOL_IMPLS:
        return _rpc_error(mid, -32602, "Invalid params：未知工具 %s（可用：%s）"
                          % (name, ", ".join(sorted(agent_tools.TOOL_IMPLS))))
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _rpc_error(mid, -32602, "Invalid params：arguments 必须是 JSON 对象")

    # 白名单闸门：副作用工具默认禁止（此处是 MCP 侧的第一道闸，第二道是 _execute_tool_internal 的审批双保险）
    if not is_tool_allowed(name):
        _logger.info("mcp tools/call blocked by whitelist: %s", name)
        # 被拒也要落审计（status=rejected = 安全层拒绝，与既有状态词表一致）
        agent_tools._audit(name, args, False, "rejected", 0,
                           "MCP 白名单未放行该副作用工具，未执行", source="mcp")
        return _rpc_result(mid, _content_text(
            "副作用工具需要审批，请在 MoRay 前端配置 MCP 白名单：工具 %s 不在已放行清单内。"
            "当前放行：%s。只读工具默认全部放行。" % (name, ", ".join(sorted(allowed_tools()))),
            True,
        ))

    # 唯一执行入口：与 /api/agent/tool 共用 _safe_path / 审批 / 审计
    approved = name in agent_tools.SIDE_EFFECT_TOOLS   # 走到这里说明已显式放行
    payload, status = agent_tools._execute_tool_internal(
        name, args, approved=approved, decision="", source="mcp",
    )
    if status != 200:
        # 未知工具在 MCP 侧已提前拦截；这里属兜底，不泄漏内部细节
        return _rpc_error(mid, -32602, str(payload.get("message") or "Invalid params"))

    if payload.get("ok") is True:
        if payload.get("needsApproval"):
            return _rpc_result(mid, _content_text(
                "副作用工具未获审批，未执行（请把 %s 加入 MCP 白名单后重试）。" % name, True))
        data = payload.get("data")
        if isinstance(data, dict) and data.get("denied"):
            return _rpc_result(mid, _content_text("客户端明确拒绝，未执行。", True))
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
        return _rpc_result(mid, _content_text(text, False))

    # 执行失败（安全拒绝 / 工具错误 / 内部错误）——只回可读文案，不回堆栈
    code = str(payload.get("code") or "tool_error")
    message = str(payload.get("message") or "工具执行失败")
    return _rpc_result(mid, _content_text("[%s] %s" % (code, message), True))


async def _handle_rpc(msg) -> dict | None:
    """处理单条 JSON-RPC 消息；通知类（notifications/*）返回 None（按协议不回响应）。"""
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "Invalid Request：消息必须是 JSON 对象")
    mid = msg.get("id")
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        return _rpc_error(mid, -32600, "Invalid Request：缺少 method")
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error(mid, -32602, "Invalid params：params 必须是 JSON 对象")

    # MCP 通知：不需要响应（即使带了 id 也按通知处理，避免客户端收到意外响应）
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return _rpc_result(mid, {})
    if method == "initialize":
        return _rpc_result(mid, _init_result(params))
    if method == "tools/list":
        return _rpc_result(mid, {"tools": list_tools()})
    if method == "tools/call":
        return _call_tool(mid, params)
    return _rpc_error(mid, -32601, "Method not found：%s" % method)


# ================================================================ SSE 传输

def _sse_event(event: str, data: str) -> str:
    """SSE 帧（data 恒为单行：JSON 序列化不会产生字面换行，URL 亦然）"""
    return "event: %s\ndata: %s\n\n" % (event, data)


async def _sse_stream(session_id: str, queue: asyncio.Queue, request: Request, endpoint: str):
    """SSE 生成器：先发 endpoint（告知 POST 地址），随后推送该会话的 JSON-RPC 响应。"""
    try:
        yield _sse_event("endpoint", endpoint)
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"   # 注释行：保活，不触发客户端事件
                continue
            yield _sse_event("message", str(payload))
    except asyncio.CancelledError:  # 客户端断连时 Starlette 取消生成器
        raise
    finally:
        _SESSIONS.pop(session_id, None)


def _disabled() -> JSONResponse:
    return JSONResponse(status_code=404, content={
        "ok": False, "code": "mcp_disabled",
        "message": "MCP 兼容层已关闭（MORAY_MCP_ENABLED=0）",
    })


@router.get(config.MCP_SSE_PATH)
async def mcp_sse_connect(request: Request):
    """建立 MCP SSE 长连接：分配 session_id，回 `event: endpoint` 告知 POST 地址。"""
    if not config.MCP_ENABLED:
        return _disabled()
    session_id = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _SESSIONS[session_id] = queue
    # 回执地址发【绝对 URL】（带 scheme/host/port）：MCP 规范允许相对地址，但绝对地址对所有客户端
    # 都无歧义（部分客户端不做 URL 拼接）；Host 头即客户端连接的地址，仅本机监听不受影响。
    base = str(request.base_url).rstrip("/")
    endpoint = "%s%s?sessionId=%s" % (base, config.MCP_SSE_PATH, session_id)
    _logger.info("mcp session opened: %s (total=%d)", session_id, len(_SESSIONS))
    return StreamingResponse(
        _sse_stream(session_id, queue, request, endpoint),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # 反代场景下禁用缓冲，保证逐条推送
        },
    )


@router.post(config.MCP_SSE_PATH)
async def mcp_sse_message(request: Request):
    """接收 JSON-RPC 请求：带有效 sessionId → 经 SSE 推送响应（HTTP 202）；
    未带 sessionId（或会话已失效）→ 直接把响应放在本次 HTTP 响应体（便于手工验证）。"""
    if not config.MCP_ENABLED:
        return _disabled()
    session_id = (request.query_params.get("sessionId")
                  or request.headers.get("MCP-Session-Id")
                  or "").strip()

    try:
        raw = await request.body()
        text = raw.decode("utf-8", errors="strict").strip() if raw else ""
    except UnicodeDecodeError:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, "Parse error：请求体不是 UTF-8"))
    if not text:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, "Parse error：请求体为空"))
    try:
        parsed = json.loads(text)
    except ValueError:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, "Parse error：请求体不是合法 JSON"))

    batch = parsed if isinstance(parsed, list) else [parsed]
    if not batch:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32600, "Invalid Request：空数组"))
    responses = []
    for msg in batch:
        try:
            resp = await _handle_rpc(msg)
        except Exception as e:  # noqa: BLE001 - 兜底：绝不把内部堆栈回给客户端
            _logger.exception("mcp handler crashed")
            resp = _rpc_error(msg.get("id") if isinstance(msg, dict) else None,
                              -32603, "Internal error：%s" % type(e).__name__)
        if resp is not None:
            responses.append(resp)

    # 纯通知（无响应）→ 202
    if not responses:
        return Response(status_code=202)

    queue = _SESSIONS.get(session_id)
    if queue is None:
        # 便利模式：无会话 → 内联返回（标准客户端始终带 sessionId，不会走到这里）
        return JSONResponse(content=responses[0] if len(responses) == 1 else responses)

    for resp in responses:
        try:
            queue.put_nowait(json.dumps(resp, ensure_ascii=False))
        except asyncio.QueueFull:
            # 客户端消费不过来：丢最旧一条再入队（保证本次响应不丢）
            try:
                queue.get_nowait()
                queue.put_nowait(json.dumps(resp, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                _logger.warning("mcp queue overflow, response dropped: %s", resp.get("id"))
    return Response(status_code=202)
