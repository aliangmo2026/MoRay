# -*- coding: utf-8 -*-
"""LayaEventHandler：L7.5 Laya联动 · 上行事件处理器（2026-09-29）。

Laya 侧 event_reporter 异步 POST 本模块回调（默认 /api/laya/events，4 类事件）。
接收即返回 200、后台 asyncio.create_task 处理——绝不阻塞 Laya 上报（任务书 一.2）。

4 类事件 → 现有能力对接（全部旁路，不改任何原模块）：
  * unknown_scenario    → 统一问题库 IssueBase.record_issue(source="Laya引擎")；
  * high_risk_detected  → 动态决策引擎的 DAG 干预入口 laya_connector.apply_directive
                          （该计划后续原子任务强制原生执行；执行点消费+决策留痕）；
  * task_exception      → 现有异常处理流程 = L4 run-error-fix 服务函数直调
                          （task_routes.run_error_fix，同前端"立即修复"链路；受
                          auto_fix_on_exception 子闸约束，默认开、总闸关则整体无效）；
  * stats_report        → 全局性能统计 sidecar work/laya/stats.json（tmp→replace 原子写）。

铁则：联动总闸关闭（config.yaml laya_link.enabled=false 且无 env 覆盖）时——
端点仍回 200（对 Laya 友好）但 accepted=false、零处理、零落盘，完全回退 L7.0 原生。
任何单事件处理异常只记 WARNING（本仓 INFO 不落盘惯例），绝不向外抛 500。

评审 三.3 补全：处理失败 → 内存指数退避重试（1/2/4s）→ 仍失败落盘
work/laya/events_spool.jsonl（不丢事件）；后续新事件到达时懒补报；
Laya 补报重发同 event_id → 去重表拦截（不重复入库）。

挂载：api.py 尾部两行折叠（L5.2/L6.0/L7.0 同款先例），main.py 零改动。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("moray.laya_events")

router = APIRouter()

MORAY_ROOT = Path(__file__).resolve().parents[2]
EVENT_TYPES = ("unknown_scenario", "high_risk_detected", "task_exception", "stats_report")

# Laya 上报事件的合法类型白名单外一律忽略（防脏数据灌库）；上限保护
_MAX_PAYLOAD_CHARS = 6000

# 评审 三.3：事件处理失败 → 指数退避重试（内存 1/2/4s 三连）→ 仍失败落盘 spool，
# 由后续新事件到达或 /api/laya/state 观察后手动 flush 补报（无 lifespan 循环，本仓惯例）
_RETRY_DELAYS: Tuple[float, ...] = (1.0, 2.0, 4.0)
_SPOOL_MAX = 500
_SEEN_MAX = 500
_seen: "OrderedDict[str, bool]" = OrderedDict()      # event_id 去重（Laya 补报会重发）


# ---------------- 信封（planner_routes 同源范式） ----------------

def _ok(data: Dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"ok": False, "code": code, "message": message})


def _stats_path() -> Path:
    """性能统计 sidecar 路径（测试/隔离实例可用 MORAY_LAYA_STATS 指到临时目录）。"""
    return Path(os.environ.get("MORAY_LAYA_STATS", "").strip()
                or (MORAY_ROOT / "work" / "laya" / "stats.json"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class _FakeRequest:
    """task_routes.run_error_fix(request) 只 await request.json()——最小适配直调复用。"""

    def __init__(self, body: Dict[str, Any]):
        self._body = body

    async def json(self) -> Dict[str, Any]:
        return self._body


def _plan_and_task(payload: Dict[str, Any]) -> (str, str):
    """从事件载荷解析 (plan_id, task_id)：显式字段优先，否则拆 "plan:sub" 形态的 task_id。"""
    task_id = str(payload.get("task_id") or "")
    plan_id = str(payload.get("plan_id") or "")
    if not plan_id and ":" in task_id:
        plan_id = task_id.split(":", 1)[0]
    return plan_id, task_id


# ---------------- 4 类事件处理（全部可单测的公开 async 函数） ----------------

async def handle_unknown_scenario(payload: Dict[str, Any]) -> None:
    """未知场景 → 统一问题库，来源标「Laya引擎」（复用 L6.5 组合式入口，零迁移同库）。"""
    from knowledge import issue_base as _ib
    text = str(payload.get("scenario") or payload.get("task_content")
               or payload.get("input_text") or payload.get("text") or "")[:_MAX_PAYLOAD_CHARS]
    if not text.strip():
        return
    ibx = _ib.IssueBase()
    ibx.record_issue(
        {"project_id": str(payload.get("project_id") or "Laya").strip()[:64] or "Laya",
         "error_type": "laya-unknown-scenario",
         "root_cause_category": ("未知场景（Laya 规则库未覆盖）：" + text)[:200],
         "error_snippet": str(payload.get("task_type") or "")[:80],
         "solution": ""},
        source="Laya引擎", issue_kind="未知场景", applicable="通用")
    logger.warning("[laya] unknown_scenario 已入统一问题库: %s", text[:80])


async def handle_high_risk(payload: Dict[str, Any]) -> None:
    """高风险 → 推送动态决策引擎：落 DAG 指令，后续原子任务在执行点强制原生。"""
    from engine import laya_connector as _conn
    plan_id, task_id = _plan_and_task(payload)
    reason = str(payload.get("reason") or payload.get("rule") or "Laya 高风险判定")[:300]
    if not plan_id:
        logger.warning("[laya] high_risk 事件缺 plan_id/task_id，无法定位计划，仅留日志: %s", reason)
        return
    _conn.apply_directive(plan_id, "force_native", task_id="", reason=reason)
    logger.warning("[laya] high_risk_detected → DAG 指令 force_native plan=%s task=%s",
                   plan_id, task_id or "-")


async def handle_task_exception(payload: Dict[str, Any]) -> None:
    """异常 → 接入现有异常处理流程（L4 run-error-fix 服务函数直调，同前端"立即修复"）。"""
    from engine import laya_connector as _conn
    text = str(payload.get("error_text") or payload.get("exception") or "")[:3000].strip()
    if not text:
        logger.warning("[laya] task_exception 事件无 error_text，忽略")
        return
    # [Synapse v4 第三步] 异常上总线（挂在 auto_fix 闸前：闸只管"怎么处置"，
    # 留痕是"事实"，闸开/关都留痕）。事件名用 failed 不用 exception——
    # 前端徽章正则 failed|error|throttl 显红，exception 会掉进中性色。
    # trace_id=event_id：Laya 补报重发同 id 天然幂等（空则由 send 自生成 uuid）。
    plan_id, task_id = _plan_and_task(payload)
    try:
        from server.app import synapse_bus as _sb   # 函数内延迟 import，防环（同下方 _tr）
        _sb.send(
            "laya.task.failed", text, mentions=['user'], thread_id=None,  # [v5 step2] 补报异常叫醒用户（@user 发送侧）
            meta={"trace_id": str(payload.get("event_id") or ""),
                  "event_id": str(payload.get("event_id") or ""),
                  "task_id": task_id, "plan_id": plan_id,
                  "project_id": str(payload.get("project_id") or ""),
                  "auto_fix": bool(_conn.cfg().get("auto_fix_on_exception"))},
            from_member="organ.laya")
    except Exception as e:  # noqa: BLE001 - 总线留痕失败不反噬异常接入链（不触发重试）
        logger.warning("[laya] task_exception 发总线失败：%s: %s",
                       type(e).__name__, str(e)[:150])
    if not _conn.cfg().get("auto_fix_on_exception"):
        logger.warning("[laya] task_exception 已收（auto_fix 关闭，仅留痕）: %s", text[:80])
        return
    from server.app import task_routes as _tr   # 函数内延迟 import，防环
    body: Dict[str, Any] = {"error_text": text}
    for k in ("project_dir", "project_id", "verify_cmd", "conversation_id"):
        if payload.get(k):
            body[k] = str(payload[k])
    resp = await _tr.run_error_fix(_FakeRequest(body))
    logger.warning("[laya] task_exception 已接入 run-error-fix（http=%s）: %s",
                   getattr(resp, "status_code", "?"), text[:80])


async def handle_stats_report(payload: Dict[str, Any]) -> None:
    """统计数据 → 更新全局性能统计 sidecar（Laya 上报块 + MoRay 旁路计数快照）。"""
    from engine import laya_connector as _conn
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    prev: Dict[str, Any] = {}
    try:
        prev = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(prev, dict):
            prev = {}
    except (OSError, ValueError):
        pass
    merged = {
        "updated_at": _now_iso(),
        "laya": payload if isinstance(payload, dict) else {"raw": str(payload)[:2000]},
        "moray_connector": _conn.stats(),
        "fuse": _conn.fuse_state(),
        "history": (prev.get("history") or [])[-23:] +
                   [{"at": _now_iso(),
                     "dispatches": _conn.stats().get("dispatches", 0),
                     "cache_hits": _conn.stats().get("cache_hits", 0)}],
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    logger.warning("[laya] stats_report 已并入全局性能统计 %s", p)


_HANDLERS = {
    "unknown_scenario": handle_unknown_scenario,
    "high_risk_detected": handle_high_risk,
    "task_exception": handle_task_exception,
    "stats_report": handle_stats_report,
}


async def _dispatch_event(evt: Dict[str, Any]) -> str:
    """单事件分派内核。返回 processed / ignored / failed（failed=处理器抛错）。"""
    etype = str(evt.get("event_type") or evt.get("type") or "")
    payload = evt.get("payload")
    if not isinstance(payload, dict):
        payload = {k: v for k, v in evt.items() if k not in ("event_type", "type")}
    fn = _HANDLERS.get(etype)
    if fn is None:
        return "ignored"
    # [2026-11-03 夜班模式] Laya 决策进 run.events（观测旁路，不消耗预算）。
    # 夜间无人值守时这些事件原本只进日志，晨报的"昨夜轨迹"看不到"Laya 在几点
    # 改了什么计划"；这里同步一份进 night_shift，任何异常都吞掉，绝不影响处理。
    try:
        from . import night_shift as _night
        _detail = str(payload.get("reason") or payload.get("decision")
                      or payload.get("message") or payload.get("summary")
                      or payload.get("scenario") or "")
        if not _detail:
            _detail = json.dumps(payload, ensure_ascii=False, default=str)[:400]
        _night.record_decision(etype, _detail)
    except Exception:  # noqa: BLE001 - 观测旁路失败不能反噬事件分派
        pass
    try:
        await fn(payload)
        return "processed"
    except Exception as e:  # noqa: BLE001 - 失败不外溢，进重试/落盘通道
        logger.warning("[laya] 事件 %s 处理失败: %s: %s", etype, type(e).__name__, str(e)[:200])
        return "failed"


async def process_event(evt: Dict[str, Any]) -> Optional[str]:
    """公开兼容入口（单测直调）：成功返回 event_type，未知/失败返回 None。"""
    r = await _dispatch_event(evt)
    return str(evt.get("event_type") or evt.get("type") or "") if r == "processed" else None


# ---------------- 评审 三.3：失败重试 + 落盘暂存 + 补报（不丢事件） ----------------

def spool_path() -> Path:
    return Path(os.environ.get("MORAY_LAYA_EVENT_SPOOL", "").strip()
                or (MORAY_ROOT / "work" / "laya" / "events_spool.jsonl"))


def _spool_load() -> list:
    try:
        lines = spool_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            d = json.loads(ln)
            if isinstance(d, dict) and d.get("event_type"):
                out.append(d)
        except ValueError:
            pass
    return out


def _spool_append(evt: Dict[str, Any]) -> None:
    try:
        p = spool_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        items = _spool_load()
        if len(items) >= _SPOOL_MAX:                    # 超限丢最旧，保最新可达
            items = items[-(_SPOOL_MAX - 1):]
        items.append(evt)
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in items) + "\n",
                       encoding="utf-8")
        tmp.replace(p)
    except OSError as e:  # noqa: BLE001
        logger.warning("[laya] 事件 spool 落盘失败（丢弃告警）: %s", str(e)[:150])


def _spool_write(items: list) -> None:
    p = spool_path()
    try:
        if not items:
            if p.is_file():
                p.unlink()
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in items) + "\n",
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


async def _retry_chain(evt: Dict[str, Any], delays: Optional[Iterable[float]] = None) -> bool:
    """内存指数退避重试；全部失败 → 落盘 spool（不丢事件）。测试可直接 await 本函数。"""
    for d in (delays if delays is not None else _RETRY_DELAYS):
        await asyncio.sleep(float(d))
        if await _dispatch_event(evt) == "processed":
            logger.warning("[laya] 事件重试成功（退避 %.0fs 后恢复）: %s", float(d),
                           str(evt.get("event_type") or ""))
            return True
    _spool_append(evt)
    logger.warning("[laya] 事件重试用尽 → 已落盘暂存待补报: %s id=%s",
                   str(evt.get("event_type") or ""), str(evt.get("event_id") or "")[:16])
    return False


async def flush_spool_once(limit: int = 50) -> Dict[str, int]:
    """补报：把 spool 里的事件重新处理一遍；成功出队、仍失败保留（顺序不丢）。"""
    items = _spool_load()
    if not items:
        return {"replayed": 0, "kept": 0}
    head, tail = items[:limit], items[limit:]
    done, kept = 0, []
    for e in head:
        if await _dispatch_event(e) == "processed":
            done += 1
        else:
            kept.append(e)
    _spool_write(kept + tail)
    if done:
        logger.warning("[laya] spool 补报成功 %d 条（残留 %d 条）", done, len(kept) + len(tail))
    return {"replayed": done, "kept": len(kept) + len(tail)}


def _mark_seen(event_id: str) -> bool:
    """event_id 去重；返回 True = 重复（应跳过处理）。总闸关时不会被调用（零残留）。"""
    if not event_id:
        return False
    if event_id in _seen:
        return True
    _seen[event_id] = True
    while len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)
    return False


def clear_seen_events() -> None:
    """测试钩子：清空 event_id 去重表。"""
    _seen.clear()


async def _guarded(evt: Dict[str, Any]) -> None:
    """后台处理链：单次失败即进 重试退避 → spool 落盘（评审 三.3 全链）。"""
    if await _dispatch_event(evt) == "failed":
        await _retry_chain(evt)


# ---------------- 回调路由 ----------------

@router.post("/api/laya/events")
async def laya_events(request: Request):
    """Laya event_reporter 回调入口：接收即 200，处理放后台 task（不阻塞上报方）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")
    etype = str(body.get("event_type") or body.get("type") or "")
    if etype not in EVENT_TYPES:
        # 脏事件：如实拒收但不算故障（200 + accepted=false，防 Laya 重试风暴）
        return _ok({"accepted": False, "reason": "unknown_event_type",
                    "event_type": etype[:40]})
    from engine import laya_connector as _conn
    if not _conn.laya_on():
        # 铁则3：总闸关 = 零处理零落盘（对 Laya 仍 200，避免其退避重试堆积）
        return _ok({"accepted": False, "reason": "laya_link_disabled",
                    "event_type": etype})
    eid = str(body.get("event_id") or "")[:64]
    if _mark_seen(eid):
        # Laya 补报重发同 event_id → 接收即 200、不重复处理（不丢也不重）
        return _ok({"accepted": True, "duplicated": True, "event_type": etype,
                    "event_id": eid}, status=200)
    try:
        # 懒补报：每次新事件到达顺带冲一次积压 spool（无 lifespan 循环，本仓惯例）
        asyncio.create_task(flush_spool_once(limit=20))
    except RuntimeError:
        pass
    asyncio.create_task(_guarded(body))       # 后台处理：失败自动进 重试退避→spool 链
    return _ok({"accepted": True, "event_type": etype, "event_id": eid}, status=200)


@router.get("/api/laya/stats")
def laya_stats():
    """全局性能统计（只读）：sidecar（Laya 上报）+ 连接器内存计数。各块独立降级。"""
    from engine import laya_connector as _conn
    sidecar: Any = None
    try:
        sidecar = json.loads(_stats_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return _ok({"enabled": _conn.laya_on(), "connector": _conn.stats(),
                "fuse": _conn.fuse_state(), "laya_report": sidecar})


@router.get("/api/laya/state")
def laya_state():
    """接线诊断（只读）：配置生效值 + 熔断态 + 未过期 DAG 指令 + 事件 spool 积压。"""
    from engine import laya_connector as _conn
    c = _conn.cfg()
    return _ok({"config": {**c, "url": _conn.dispatch_url(c)},
                "fuse": _conn.fuse_state(),
                "directives": _conn.pending_directives()[:10],
                "event_spool": {"backlog": len(_spool_load()), "path": str(spool_path())},
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S")})
