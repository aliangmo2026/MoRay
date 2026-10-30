# -*- coding: utf-8 -*-
"""/api/monitor/* —— L5.2 主动日志监控薄路由（2026-09-28）。

信封范式 = task_routes/argus_proxy：{ok,data} / {ok,code,message}。
挂载 = api.py 尾部两行折叠（:341-342/:346-347 先例），main.py 仅加两个小钩子。

端点（逻辑全在 server/app/log_monitor.py，本文件只做入参校验/出参整形）：
  GET  /api/monitor/status                开关/间隔/阈值/扫描目录/计数（前端"监控状态"卡）
  POST /api/monitor/enable                body {project_dir?, interval?} → 启动循环
  POST /api/monitor/disable               停止循环
  POST /api/monitor/scan                  立即扫一轮（E2E 用，不等下个周期；未开启=409）
  GET  /api/monitor/alerts                pending 告警队列（前端轮询注入告警卡）
  POST /api/monitor/alerts/{id}/ack       body {action: 'ignored'|'fixing'} → 标记已处理

红线：监控默认关闭（config.MONITOR_ENABLED=false），enable 只开内存循环、
不回写 .env——重启回关，用户对话里再说"开启监控"才再开（任务书 2.1 语义）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .log_monitor import manager

router = APIRouter()


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


@router.get("/api/monitor/status")
async def monitor_status():
    return _ok(manager.status_view())


@router.post("/api/monitor/enable")
async def monitor_enable(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")
    project_dir = str(body.get("project_dir") or "").strip().strip('"')
    if project_dir and not Path(project_dir).is_dir():
        return _err(400, "path_not_found",
                    "项目目录不存在：%s（可改由对话选择卡指定）" % project_dir)
    interval = None
    if body.get("interval") is not None:
        try:
            interval = int(body.get("interval"))
        except (TypeError, ValueError):
            return _err(400, "bad_interval", "interval 必须是秒数整数。")
    await manager.enable(project_dir=project_dir, interval=interval)
    return _ok(manager.status_view())


@router.post("/api/monitor/disable")
async def monitor_disable():
    await manager.disable()
    return _ok(manager.status_view())


@router.post("/api/monitor/scan")
async def monitor_scan():
    if not manager.enabled:
        return _err(409, "monitor_disabled", "监控未开启（先在对话里说\"开启监控\"）。")
    try:
        await manager.scan_once()
    except Exception as e:  # noqa: BLE001
        return _err(500, "scan_failed", "%s: %s" % (type(e).__name__, str(e)[:200]))
    return _ok({"last_scan": manager.last_scan,
                "last_error": manager.last_error,
                "pending": len(manager.pending_alerts())})


@router.get("/api/monitor/alerts")
async def monitor_alerts():
    return _ok({"alerts": manager.pending_alerts()})


@router.post("/api/monitor/alerts/{alert_id}/ack")
async def monitor_alert_ack(alert_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    action = str((body or {}).get("action") if isinstance(body, dict) else "") \
        or "ignored"
    if action not in ("ignored", "fixing"):
        return _err(400, "bad_action", "action 只能是 ignored 或 fixing。")
    rec = manager.ack(str(alert_id or "").strip(), action)
    if rec is None:
        return _err(404, "not_found", "告警不存在或已处理。")
    return _ok(rec)
