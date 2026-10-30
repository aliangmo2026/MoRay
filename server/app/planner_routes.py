# -*- coding: utf-8 -*-
"""/api/autonomous/* —— L7.0 任务自治引擎路由（2026-09-29）。

信封范式 = task_routes/planner 同源：{ok,data} / {ok,code,message}。
挂载 = api.py 尾部两行折叠（monitor/self_routes 同款先例），main.py 零改动。

端点（⚠️ /status 必须注册在 /{plan_id} 通配之前——projects/unfinished 同款注册序坑）：
  POST /api/autonomous/run     提交大目标（202 受理，后台 规划→调度→整合）
                               body {goal, project_dir?, project_id?}
  GET  /api/autonomous/status  总闸可见性（默认关；前端意图分支探测用）
  GET  /api/autonomous/{plan_id}  轮询：计划/子任务/决策日志/最终报告

总闸：MORAY_TASK_PLANNER 默认 false（红线 7）——run 直接 409 planner_disabled，
status 如实报 enabled:false（前端不接管）。重启恢复：内存表丢失时按 work/autonomous/
{plan_id}/run.json 从磁盘回读（诚实快照，不假装续跑）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config

router = APIRouter()
logger = logging.getLogger("moray-planner")

MAX_GOAL_CHARS = 500
RUN_KEEP = 30

# plan_id → 壳记录 {plan_id, goal, status, view?, error?}（单事件循环内同步变更）
_RUNS: Dict[str, Dict[str, Any]] = {}
_ORDER: list = []


def _work_root() -> Path:
    return Path(config.SERVER_DIR).parent / "work" / "autonomous"


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "code": code,
                                                     "message": message})


def _disk_view(plan_id: str) -> Dict[str, Any]:
    try:
        p = _work_root() / re.sub(r"[^\w.\-]", "_", str(plan_id)).strip("._-") / "run.json"
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("plan_id"):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _register(plan_id: str, rec: Dict[str, Any]) -> None:
    _RUNS[plan_id] = rec
    _ORDER.append(plan_id)
    while len(_ORDER) > RUN_KEEP:
        old = _ORDER[0]
        if old == plan_id:
            break
        _ORDER.pop(0)
        _RUNS.pop(old, None)


@router.get("/api/autonomous/status")
async def autonomous_status():
    """总闸可见性 + 运行计数（前端意图分支用；闸关时 run 返回 409）。"""
    from engine.task_planner import planner_on
    active = sum(1 for r in _RUNS.values()
                 if r.get("status") in ("planning", "running"))
    return _ok({"enabled": planner_on(), "runs": len(_RUNS), "active": active,
                "max_goal_chars": MAX_GOAL_CHARS})


@router.post("/api/autonomous/run")
async def autonomous_run(request: Request):
    """提交大目标：202 受理 → 后台 规划（LLM/规则兜底）→ 调度执行 → 整合沉淀。"""
    from engine.task_planner import planner_on, new_plan_id
    if not planner_on():
        return _err(409, "planner_disabled",
                    "自治引擎未开启（MORAY_TASK_PLANNER 默认关闭；开启后重启后端生效）。")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")
    goal = str(body.get("goal") or "").strip()[:MAX_GOAL_CHARS]
    if not goal:
        return _err(400, "bad_request", "goal 不能为空（一句话描述你的大目标）。")

    project_dir_raw = str(body.get("project_dir") or "").strip().strip('"')
    project_dir = Path(project_dir_raw) if project_dir_raw else Path(config.WORKSPACE_DEFAULT)
    if not project_dir.is_dir():
        return _err(400, "path_not_found", "项目目录不存在：%s" % project_dir)
    project_id = str(body.get("project_id") or "").strip() or project_dir.name

    plan_id = new_plan_id()
    rec = {"plan_id": plan_id, "goal": goal, "project_id": project_id,
           "project_dir": str(project_dir.resolve()),
           "status": "planning", "view": None, "error": "",
           "t0": time.time()}
    _register(plan_id, rec)
    asyncio.create_task(_guarded_run(plan_id, goal, project_id,
                                     str(project_dir.resolve())))
    logger.warning("[planner] %s accepted goal=%r project=%s", plan_id, goal[:80], project_id)
    return _ok({"plan_id": plan_id, "status": "planning", "goal": goal,
                "project_id": project_id}, status=202)


async def _guarded_run(plan_id: str, goal: str, project_id: str, project_dir: str) -> None:
    from engine import task_planner, execution_scheduler
    rec = _RUNS.get(plan_id)
    try:
        plan = await task_planner.decompose(goal, project_id, project_dir,
                                            plan_id=plan_id)
        if rec is not None:
            rec["plan_source"] = plan.get("source")
        def on_event(view: Dict[str, Any]) -> None:
            if rec is not None:
                rec["status"] = str(view.get("status") or rec["status"])
                rec["view"] = view
        scheduler = execution_scheduler.ExecutionScheduler()
        run = await scheduler.run_plan(plan, on_event=on_event)
        if rec is not None:
            rec["status"] = run.status
            rec["view"] = run.view()
        logger.warning("[planner] %s finished: %s (%.1fs)", plan_id, run.status,
                       (run.ended_at or time.time()) - run.t0)
    except Exception as e:  # noqa: BLE001
        if rec is not None:
            rec["status"] = "failed"
            rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        logger.warning("[planner] %s crashed: %s", plan_id, e)


@router.get("/api/autonomous/{plan_id}")
async def autonomous_view(plan_id: str):
    """轮询：优先内存态；重启后按磁盘 run.json 回读（诚实快照）。"""
    plan_id = str(plan_id or "").strip()
    rec = _RUNS.get(plan_id)
    if rec is not None:
        if rec.get("view"):
            return _ok(rec["view"])
        return _ok({"plan_id": plan_id, "goal": rec.get("goal", ""),
                    "status": rec.get("status", "planning"),
                    "plan_source": rec.get("plan_source", ""),
                    "subtasks": [], "decisions": [], "report": "",
                    "error": rec.get("error", ""), "duration_sec":
                        round(time.time() - rec.get("t0", time.time()), 1),
                    "work_dir": str(_work_root() / plan_id)})
    disk = _disk_view(plan_id)
    if disk:
        return _ok(disk)
    return _err(404, "not_found", "没有该自治任务的记录（可能后端重启且无落盘快照）。")
