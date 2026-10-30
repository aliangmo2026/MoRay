# -*- coding: utf-8 -*-
"""TaskPlanner：L7.0 任务自治引擎 · 规划层（目标理解与任务拆解，2026-09-29）。

纯旁路：不碰 task_runner/模板/调度既有逻辑，只在其上产出结构化计划。

职责（任务书 2.2）：
  * 目标解析：自然语言大目标 → 有序子任务列表（调用现有 LLM 网关通道，与 task_runner
    同款 env：IRIS_GATEWAY_URL / MORAY_TASK_BUCKET；llm_call 可注入 → 单测全离线）。
  * 任务类型识别：review（代码审查）/ fix（修复）/ test（运行测试）/ logs（查日志）/
    report（出报告，仅作终结标记，由整合器兑现）。
  * 依赖编排：按执行顺序构线性依赖链（DAG 的退化形，天然无环；LLM 顺序即依赖序）。
  * 拆解依据：统一知识库（IssueBase）+ 项目画像 + 历史高频根因 + 审查待处理项，
    注入规划 prompt 与 plan.context。
  * 输出：结构化计划 dict（plan_id/goal/source/context/tasks[{id,type,title,why,deps,
    params_hint,expected}]）。
  * 规则兜底：LLM 不可用/超时(15s)/坏 JSON → 关键词启发式拆解（诚实标 source="rule"），
    无 LLM 也能出可用计划。

总闸：MORAY_TASK_PLANNER **默认关闭**（红线 7）——planner_on() False 时 decompose 直接
返回 {"enabled": False}，不调 LLM 不建计划；开启后才生效。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 与 engine/task_runner.py 同源的网关契约（降级通道/预算同款 env，不改其模块）
GATEWAY_URL = os.environ.get("IRIS_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/")
PLANNER_SESSION = os.environ.get("MORAY_PLANNER_BUCKET", "moray:planner")
_CLOUD_HINT = "本请求是云端高精度推理任务（研究级精度，非本地架构简答）。"

TASK_TYPES = ("review", "fix", "test", "logs", "report")
MAX_TASKS = 6
LLM_TIMEOUT_SEC = 15.0

_TYPE_DESC = {
    "review": "代码审查（code_review_flow，发现并沉淀问题）",
    "fix": "修复问题（text_fix_flow，按报错文本修复）",
    "test": "运行测试（test_run_flow，跑测试并分析）",
    "logs": "查日志（扫描本地日志并分析异常）",
    "report": "出报告（整合全部结果为最终交付）",
}


def planner_on() -> bool:
    """总闸：默认关闭（红线 7），用户显式开启（MORAY_TASK_PLANNER=1/true）才生效。"""
    return str(os.environ.get("MORAY_TASK_PLANNER", "0")).strip().lower() \
        in ("1", "true", "yes", "on")


def new_plan_id() -> str:
    return uuid.uuid4().hex[:16]


# ---------------- 拆解依据（统一知识库 + 画像） ----------------

def build_context(project_id: str, project_dir: str = "") -> Dict[str, Any]:
    """规划上下文：画像语言/成功率 + 高频根因 Top5 + 审查待处理项。全只读，异常降级。"""
    ctx: Dict[str, Any] = {"project_id": project_id, "project_dir": project_dir}
    try:
        from knowledge import issue_base as _ib
        ibx = _ib.IssueBase()
        prof = ibx.eb.read_profile(project_id) or {}
        ctx["language"] = str(prof.get("high_freq_language") or "unknown")
        ctx["history_success_rate"] = prof.get("success_rate")
        ctx["top_issues"] = [
            {"cause": r.get("root_cause_category"), "times": r.get("times"),
             "rate": r.get("success_rate")}
            for r in (ibx.eb.top_meta(project_id, None, 5) or []) if r.get("score", 0) > 0]
        open_rows = ibx.open_review_issues(project_id, limit=5)
        ctx["open_review_issues"] = [str(r.get("root_cause_category") or "")[:80]
                                     for r in open_rows]
    except Exception as e:  # noqa: BLE001
        ctx["context_error"] = "%s: %s" % (type(e).__name__, e)
    return ctx


# ---------------- LLM 拆解（可注入） ----------------

async def _gateway_call(prompt: str) -> str:
    """现有 LLM 网关通道（/api/llm/chat，与 task_runner._gateway_chat 同契约）。"""
    import httpx
    payload = {"messages": [{"role": "system", "content": _CLOUD_HINT},
                            {"role": "user", "content": prompt}],
               "stream": False, "session_id": PLANNER_SESSION, "temperature": 0.0}
    async with httpx.AsyncClient(timeout=httpx.Timeout(LLM_TIMEOUT_SEC, connect=5)) as c:
        r = await c.post(GATEWAY_URL + "/api/llm/chat", json=payload)
        data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("message") or data.get("error") or "gateway rejected"))
    return str(data.get("content") or "")


def _plan_prompt(goal: str, ctx: Dict[str, Any]) -> str:
    lines = [
        "你是任务规划器（研究级精度）。把用户的宏观目标拆解为有序的原子子任务序列。",
        "只输出一个 JSON 对象，不要任何多余文字，格式：",
        '{"tasks": [{"type": "review|fix|test|logs|report", "title": "简短标题", "why": "为什么需要"}]}',
        "约束：按执行顺序排列；最多 %d 个；必须以 report 结尾；" % MAX_TASKS,
        "类型含义：" + "；".join("%s=%s" % (k, v) for k, v in _TYPE_DESC.items()) + "。",
        "依赖关系自动按顺序串联（后一个消费前一个的产出），无需单独表达。",
        "—— 项目上下文（拆解依据）——",
        "项目：%s（语言 %s）" % (ctx.get("project_id"), ctx.get("language")),
    ]
    if ctx.get("history_success_rate") is not None:
        lines.append("历史修复成功率：%s" % ctx["history_success_rate"])
    if ctx.get("top_issues"):
        lines.append("历史高频问题：" + "；".join(
            "%s（%s 次）" % (t["cause"], t["times"]) for t in ctx["top_issues"]))
    if ctx.get("open_review_issues"):
        lines.append("历史审查待处理：" + "；".join(ctx["open_review_issues"]))
    lines.append("—— 用户目标 ——")
    lines.append(str(goal)[:400])
    return "\n".join(lines)


def _validate_llm_tasks(data: Any) -> List[Dict[str, Any]]:
    """LLM 输出 → 合法子任务列表（类型白名单/去空/截断 MAX_TASKS）。"""
    from engine.task_runner import _extract_json_obj
    obj = data if isinstance(data, dict) else _extract_json_obj(str(data or ""))
    if not isinstance(obj, dict):
        raise ValueError("not a json object")
    raw = obj.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise ValueError("empty tasks")
    out: List[Dict[str, Any]] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        ttype = str(t.get("type") or "").strip().lower()
        if ttype not in TASK_TYPES:
            continue
        out.append({"type": ttype, "title": str(t.get("title") or ttype)[:40],
                    "why": str(t.get("why") or "")[:160]})
        if len(out) >= MAX_TASKS:
            break
    if not out:
        raise ValueError("no valid task types")
    if out[-1]["type"] != "report":
        out.append({"type": "report", "title": "整合交付", "why": "计划收尾"})
    return out


# ---------------- 规则兜底拆解 ----------------

def _rule_tasks(goal: str) -> List[Dict[str, Any]]:
    g = str(goal or "")
    has = lambda *ws: any(w in g for w in ws)  # noqa: E731
    seq: List[str] = []
    if has("状态", "日志", "服务"):
        seq += ["logs"]
    if has("审查", "review", "体检", "全面", "整体", "优化", "治理", "大扫除", "彻底"):
        seq += ["review"]
    if has("修", "fix", "问题", "报错", "崩"):
        seq += ["fix"]
    if has("测试", "test", "用例") or "review" in seq or "fix" in seq:
        seq += ["test"]
    if not seq:
        seq = ["review", "test"]
    if seq and seq[0] == "logs" and len(seq) == 1:
        seq += ["report"]
    if "report" not in seq:
        seq += ["report"]
    titles = {"logs": "扫描并分析本地日志", "review": "审查项目近期代码",
              "fix": "修复发现的问题", "test": "运行测试验证", "report": "整合交付"}
    return [{"type": t, "title": titles[t], "why": "规则拆解（关键词命中）"} for t in seq]


# ---------------- 主入口 ----------------

async def decompose(goal: str, project_id: str, project_dir: str = "",
                    llm_call=None, plan_id: Optional[str] = None) -> Dict[str, Any]:
    """大目标 → 结构化计划（async：LLM 调用可等待；测试用 asyncio.run 驱动）。
    绝不抛异常：闸关 → {"enabled": False}；LLM 失败 → 规则兜底（source=rule）。
    plan_id 可由受理端点预生成传入（202 语义：先给凭证后规划）。"""
    plan: Dict[str, Any] = {"plan_id": str(plan_id or new_plan_id()),
                            "goal": str(goal or "")[:500],
                            "enabled": planner_on(),
                            "created_at": datetime.now(timezone.utc).astimezone()
                            .isoformat(timespec="seconds")}
    if not plan["enabled"]:
        plan["source"] = "disabled"
        plan["tasks"] = []
        return plan
    ctx = build_context(project_id, project_dir)
    plan["context"] = ctx
    tasks: Optional[List[Dict[str, Any]]] = None
    source = "rule"
    note = ""
    call = llm_call or _gateway_call
    try:
        raw = await asyncio.wait_for(call(_plan_prompt(goal, ctx)),
                                     timeout=LLM_TIMEOUT_SEC)
        tasks = _validate_llm_tasks(raw)
        source = "llm"
    except Exception as e:  # noqa: BLE001
        note = "LLM 拆解不可用，走规则兜底：%s: %s" % (type(e).__name__, str(e)[:120])
    if tasks is None:
        tasks = _rule_tasks(goal)
    # 线性依赖链（顺序即依赖；天然无环）
    for i, t in enumerate(tasks):
        t["id"] = "t%d" % (i + 1)
        t["deps"] = ["t%d" % i] if i else []
        t["expected"] = _TYPE_DESC.get(t["type"], t["type"])
    plan["tasks"] = tasks
    plan["source"] = source
    plan["notes"] = note or ("拆解依据：项目画像 + 统一知识库高频根因 + 审查待处理项（%d 条）"
                             % len(ctx.get("open_review_issues") or []))
    return plan


def summarize_plan(plan: Dict[str, Any]) -> str:
    """人话一行摘要（前端确认卡「我理解为xxx」用）。"""
    names = " → ".join(t.get("title") or t.get("type") for t in (plan.get("tasks") or []))
    return "计划（%s）：%s" % (plan.get("source") or "?", names or "空")
