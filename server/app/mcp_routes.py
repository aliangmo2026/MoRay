# -*- coding: utf-8 -*-
"""MCP 网关 HTTP 面（第 2 晚 2026-11-06）：把 server/app/mcp_gateway.py 暴露给跨进程消费方。

为什么需要这一层：
  * Laya（8001）是独立进程，不能 import server.app.mcp_gateway（解释器/依赖/cwd 都不同）；
  * 第2晚要求"Laya 侧和主服务统一走网关"——Laya 的 tool_registry._call_iris 从直连
    Iris 8004 改为 POST 本文件的 /api/mcp/gateway/call，鉴权/传输/信封全部由网关统一处理。

端点（均只挂 MoRay 8000，HOST 固定 127.0.0.1）：
  GET  /api/mcp/gateway/status                各注册 server 的 enabled/url/工具摘要（无 Key 明文）
  POST /api/mcp/gateway/list   {"server": x}  tools/list 发现
  POST /api/mcp/gateway/call   {"server": x, "tool": y, "arguments": {...}}
       → {"ok": bool, "server": x, "tool": y, "code": "OK|IRIS_*|MCP_*", "data": ...}

安全边界：
  * server 只接受 config.MCP_SERVERS 注册表里的名字 —— 请求无法输送任意 URL，结构上无 SSRF；
  * tool 名做字符白名单（RPC 参数是 JSON，无注入面，此校验防脏数据）；
  * arguments 仅 JSON object，序列化上限 256KB；
  * 与 /api/night_shift/guard 同款先例：本机回环 + 无鉴权（进程间调用，服务不对外）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config
from . import mcp_gateway

logger = logging.getLogger("moray.mcp.routes")
router = APIRouter()

_MAX_ARGS_BYTES = 256 * 1024
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data})


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "code": code, "message": message})


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise ValueError("请求体不是合法 JSON：%s" % type(e).__name__) from e
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON object")
    return body


def _registered() -> Dict[str, Any]:
    return dict(getattr(config, "MCP_SERVERS", {}) or {})


@router.get("/api/mcp/gateway/status")
async def gateway_status():
    """注册表全景（含工具清单）。发现结果走 mcp_gateway 的 TTL 缓存，不会每次打器官。"""
    names = list(_registered().keys())
    raw = await asyncio.gather(*(mcp_gateway.probe_server(n) for n in names),
                               return_exceptions=True)
    servers = []
    for n, item in zip(names, raw):
        if isinstance(item, Exception):
            servers.append({"name": n, "ok": False, "code": "probe_failed",
                            "message": "%s: %s" % (type(item).__name__, item)})
        else:
            servers.append(item)
    return _ok({
        "servers": servers,
        "inject_default": bool(getattr(config, "MCP_CHAT_INJECT_DEFAULT", False)),
        "inject_servers": {k: {"prefix": v.get("prefix"), "tools": list(v.get("tools") or ())}
                           for k, v in (getattr(config, "MCP_CHAT_INJECT_SERVERS", {}) or {}).items()},
    })


@router.post("/api/mcp/gateway/list")
async def gateway_list(request: Request):
    try:
        body = await _json_body(request)
    except ValueError as e:
        return _err(400, "bad_json", str(e))
    server = str(body.get("server") or "").strip()
    if server not in _registered():
        return _err(400, "bad_server",
                    "server 必须是 MCP_SERVERS 注册表里的名字：%s" % ",".join(_registered()))
    cfg = mcp_gateway.get_server_config(server)
    if not bool(cfg.get("enabled", True)):
        return _ok({"server": server, "enabled": False, "tools": []})
    tools = await mcp_gateway.list_server_tools(server)
    return _ok({"server": server, "enabled": True, "tools": tools})


@router.post("/api/mcp/gateway/call")
async def gateway_call(request: Request):
    try:
        body = await _json_body(request)
    except ValueError as e:
        return _err(400, "bad_json", str(e))
    server = str(body.get("server") or "").strip()
    tool = str(body.get("tool") or "").strip()
    arguments = body.get("arguments")
    if arguments is None:
        arguments = {}
    if server not in _registered():
        return _err(400, "bad_server",
                    "server 必须是 MCP_SERVERS 注册表里的名字：%s" % ",".join(_registered()))
    if not _TOOL_NAME_RE.match(tool):
        return _err(400, "bad_tool", "tool 名非法（仅字母数字与 _ . : -，最长 128）")
    if not isinstance(arguments, dict):
        return _err(400, "bad_arguments", "arguments 必须是 JSON object")
    try:
        size = len(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
    except Exception:  # noqa: BLE001 - 不可序列化对象直接拒绝
        return _err(400, "bad_arguments", "arguments 不可 JSON 序列化")
    if size > _MAX_ARGS_BYTES:
        return _err(413, "arguments_too_large",
                    "arguments 体积 %d 字节超过上限 %d" % (size, _MAX_ARGS_BYTES))
    cfg = mcp_gateway.get_server_config(server)
    if not bool(cfg.get("enabled", True)):
        return JSONResponse(status_code=503,
                            content={"ok": False, "server": server, "tool": tool,
                                     "code": "server_disabled",
                                     "message": "MCP_SERVERS.%s.enabled=false" % server})
    try:
        env = await mcp_gateway.execute_chat_tool(server, tool, arguments)
    except Exception as e:  # noqa: BLE001 - execute 本该不抛，兜底仍给信封
        logger.warning("[mcp-routes] %s.%s 执行异常：%s: %s", server, tool,
                       type(e).__name__, e)
        return JSONResponse(status_code=502,
                            content={"ok": False, "server": server, "tool": tool,
                                     "code": "gateway_error",
                                     "message": "%s: %s" % (type(e).__name__, e)})
    return JSONResponse({
        "ok": str(env.get("code")) == "OK",
        "server": server,
        "tool": tool,
        "code": env.get("code"),
        "data": env.get("data"),
    })
