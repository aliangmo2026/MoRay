# -*- coding: utf-8 -*-
"""/api/adapters/* 薄路由（p_mcp 迭代·任务3 阶段4.2，信封对齐 argus_proxy 范式：
{ok:true,data} / {ok:false,code,message}）。

端点：
  GET  /api/adapters/health        Iris MCP 探活 + 配置回显（不含 Key）
  POST /api/adapters/log-intent    模糊排错意图 {text, project_root?}
       → 命中关键词 → LocalLogAdapter.collect() → 有数据：iris_analyze_text 送分析；
         无数据：友好提示"请提供截图或贴入报错文本"（任务书 4.2 原文要求）。

挂载方式：server/app/api.py 文件尾 2 行 include_router（api.py:187-199 折叠先例，偏差 D6）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .iris_mcp_client import IrisMCPClient
from .local_log_adapter import LocalLogAdapter, summarize_for_prompt

logger = logging.getLogger("moray-adapters")
router = APIRouter()

# 任务书 4.2 关键词清单 + 常见口语变体（中文，无词边界问题）
_CRASH_INTENT_RE = re.compile(
    r"(报错|崩了|崩溃|跑不起来|跑不动|出错了|出错|挂掉|挂了|闪退|起不来|启动失败|"
    r"异常退出|炸了|fatal|traceback|exception)", re.IGNORECASE)


def matches_crash_intent(text: str) -> bool:
    return bool(_CRASH_INTENT_RE.search(str(text or "")))


def _ok(data: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data})


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


@router.get("/api/adapters/health")
async def adapters_health():
    cli = IrisMCPClient()
    h = await cli.health()
    cfg = cli.cfg
    return _ok({"iris": h, "enabled": bool(cfg.get("enabled")),
                "url": cfg.get("url"), "api_key_configured": bool(cfg.get("api_key"))})


@router.post("/api/adapters/log-intent")
async def log_intent(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    text = str(body.get("text") or "").strip()
    project_root = str(body.get("project_root") or "").strip()
    if not text:
        return _err(400, "bad_text", "text 不能为空。")

    if not matches_crash_intent(text):
        return _err(400, "no_intent", "文本未命中排错意图关键词（报错了/崩了/跑不起来/出错了…）。")

    adapter = LocalLogAdapter()
    collected = adapter.collect(intent_hint=text, project_root=project_root)
    summary = summarize_for_prompt(collected)

    if not collected["has_error_data"] or not summary:
        return _ok({
            "matched": True, "has_data": False,
            "message": "本机暂未采集到可用的报错信息（项目 logs/、终端缓冲、git 记录均为空）。"
                       "请提供截图或贴入报错文本。",
            "sources_probed": {
                "logs_file": collected["logs"].get("file"),
                "terminal_buffer_lines": len(collected["terminal_buffer"]["lines"]),
                "git": bool(collected.get("git_changes")),
                "project_root": collected.get("project_root") or "(未定位)",
            },
            "notes": collected.get("notes", []),
        })

    cli = IrisMCPClient()
    if not cli.cfg.get("enabled"):
        return _err(503, "iris_disabled", "Iris MCP 已在配置中禁用（MORAY_IRIS_MCP_ENABLED=0）。")
    env = await cli.call_tool("iris_analyze_text", {"text": summary})
    if env.get("code") != "OK":
        code = str(env.get("code", "iris_error"))
        msg = str((env.get("data") or {}).get("message") or "Iris 分析失败")
        status = 503 if code in ("IRIS_UNREACHABLE", "IRIS_TIMEOUT") else 502
        return _err(status, code, msg)
    data = env["data"]
    return _ok({
        "matched": True, "has_data": True,
        "collected": {
            "source": collected.get("source"),
            "logs_file": collected["logs"].get("file"),
            "error_line_count": len(collected["logs"].get("error_lines") or []),
            "stack_trace_blocks": len(collected.get("stack_traces") or []),
            "git_commit": (collected.get("git_changes") or {}).get("commit"),
        },
        "analysis": {k: data.get(k) for k in
                     ("error_type", "severity", "file_path", "line_number",
                      "root_cause_candidates", "diagnosis")},
    })
