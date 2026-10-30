# -*- coding: utf-8 -*-
"""LayaDispatch：L7.5.1 Laya联动 · /api/v1/task/* 下行调度入口（架构评审补充版）。

新接口（经 laya_std_server.py 追加块挂进 8001 标准服务，路由零侵入）：
  * POST /api/v1/task/dispatch —— MoRay 原子任务调度决策（路由判定+风险分级+结果缓存）。
      请求（评审 一.1 补全）：task_id, task_type, task_content, project_context,
        enable_cache, confidence_threshold(默认0.7), x_hop_count(默认0), context_snapshot
      响应（评审 一.2 补全）：code, route_result, risk_level, cache_hit, template_name,
        result_data, decision_id, confidence, is_fast_path, fallback_reason,
        rule_version, model_version, latency_ms（全键恒在，便于 MoRay 严校验）
      校验（评审 一.3）：缺必填 task_id/task_type/task_content → HTTP 400 标准错误码
        + code:400 信封（MoRay 侧收到非 200 自动降级原生）。
  * POST /api/v1/task/outcome —— MoRay 成功结果回流，喂结果缓存（只存 status=ok）。
  * GET  /api/v1/task/rules    —— 规则库/缓存/版本/上报器状态快照。

风险机制（评审 二）：
  * 二.1 防循环：请求 x_hop_count >= 2 → 拒绝决策，回 native + fallback_reason=hop_limit；
    MoRay 侧同样硬拦（双侧防御）；本服务从不调用 MoRay /api/autonomous/*（纯 sink）。
  * 二.3 高风险：risk=high → is_fast_path=False、跳过缓存，回 native 由 MoRay 原生链路
    + 原生风控执行（旁路不绕过权限管控）。
  * 二.4 结果缓存：LRU 容量（LAYA_RESULT_CACHE_MAX 默认1000）+ TTL 过期
    （LAYA_RESULT_CACHE_TTL 默认600s）；命中即续期位置；每 256 次决策全量清扫过期项。

决策为纯内存规则 + 缓存，毫秒级出结果，绝不调模型；不确定场景如实上报
unknown_scenario 事件并回 route_result=native，不做硬猜路由。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel

from event_reporter import EventReporter, SPOOL_PATH, CALLBACK_URL

logger = logging.getLogger("laya.dispatch")

router = APIRouter()

RULE_VERSION = "l75-1.1"      # 契约补全版（评审 一.2）
MAX_HOPS = 2                  # 同内容最多穿过 Laya 2 次（评审 二.1，与 MoRay 侧同值）

# ---------------- 4 类原子任务规则库（任务书 二.3 + 评审补充） ----------------
# template: 路由目标原子模板（None = 本地扫描动作不下放模板，路由=原生）；
# confidence: 规则命中置信度（与 RULE_MAP 0.99 惯例同源）。
ATOMIC_RULES: Dict[str, Dict[str, Any]] = {
    "review": {"template": "code_review_flow", "confidence": 0.99,
               "risk_keywords": ("生产", "线上", "全量", "核心模块")},
    "fix":    {"template": "text_fix_flow", "confidence": 0.99,
               "risk_keywords": ("删除", "数据库", "配置变更", "权限", "重启", "生产")},
    "test":   {"template": "test_run_flow", "confidence": 0.99,
               "risk_keywords": ("删除", "生产", "线上")},
    "logs":   {"template": None, "confidence": 0.90,
               "risk_keywords": ("权限",)},
}

# 高风险词：复用现有 RULE_MAP 的"高风险类"同一词表（评审：复用现有规则引擎）
_FALLBACK_HIGH_RISK = ("生产", "线上", "删除", "重启", "权限", "配置变更", "nginx", "数据库")


def _high_risk_words() -> tuple:
    try:
        std = __import__("laya_std_server")
        rule = std.RULE_MAP[3]          # 现表：高风险类 = RULE_MAP 第 4 条
        return tuple(rule[0]) if isinstance(rule[0], (list, tuple)) else _FALLBACK_HIGH_RISK
    except Exception:  # noqa: BLE001 - 独立跑测/词表漂移 → 回落硬编码副本
        return _FALLBACK_HIGH_RISK


def _model_version() -> str:
    """决策虽走纯规则层不上模型，版本仍如实上报（评审 一.2 可观测性）。"""
    try:
        return str(__import__("laya_std_server").MODEL_NAME)
    except Exception:  # noqa: BLE001
        return "moray-laya-rules-only"


# ---- 结果缓存（评审 二.4：LRU 容量 + TTL 过期 + 周期清扫，防内存膨胀） ----
_RESULT_TTL = float(os.environ.get("LAYA_RESULT_CACHE_TTL", "600"))
_RESULT_MAX = max(1, int(os.environ.get("LAYA_RESULT_CACHE_MAX", "1000")))
_PURGE_EVERY = 256
_result_cache: "OrderedDict[str, tuple]" = OrderedDict()
_purge_ctr = [0]

_stats = {"decisions": 0, "cache_hits": 0, "high_risk": 0, "unknown": 0,
          "outcomes": 0, "fallback_native": 0, "hop_rejects": 0,
          "low_confidence_rejects": 0, "invalid_requests": 0}
_reporter = EventReporter()
_last_stats_at = time.time()
_STATS_INTERVAL = float(os.environ.get("LAYA_STATS_INTERVAL_S", "60"))

ROUTE_RESULTS = ("cache", "template", "native")


class DispatchRequest(BaseModel):
    task_id: str
    task_type: str
    task_content: Union[Dict[str, Any], str, None] = None
    project_context: Optional[Dict[str, Any]] = None
    enable_cache: bool = True
    # ---- 评审 一.1 新增契约字段（带默认，向后兼容旧调用方） ----
    confidence_threshold: float = 0.7
    x_hop_count: int = 0
    context_snapshot: Optional[Dict[str, Any]] = None


class OutcomeRequest(BaseModel):
    decision_id: str = ""
    task_id: str = ""
    task_type: str
    task_content: Union[Dict[str, Any], str, None] = None
    result_data: Optional[Dict[str, Any]] = None


def _canon(content: Any) -> str:
    """任务内容规范化——与 MoRay 连接器 _canonical 同规则（sort_keys + ensure_ascii=False）。"""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(content)


def _cache_key(task_type: str, content: Any) -> str:
    return "%s:%s" % (task_type, hashlib.sha1(_canon(content).encode("utf-8")).hexdigest())


def _purge_expired() -> None:
    now = time.time()
    dead = [k for k, (_, exp) in _result_cache.items() if exp <= now]
    for k in dead:
        _result_cache.pop(k, None)


def _validate_request(req: DispatchRequest) -> Optional[str]:
    """评审 一.3 严格入参校验：必填 task_id/task_type/task_content 且类型正确。"""
    if not str(req.task_id or "").strip():
        return "缺少必填字段: task_id"
    if not str(req.task_type or "").strip():
        return "缺少必填字段: task_type"
    if req.task_content is None or (isinstance(req.task_content, str)
                                    and not req.task_content.strip()):
        return "缺少必填字段: task_content"
    if req.x_hop_count < 0:
        return "x_hop_count 不得为负"
    return None


def _risk_level(task_type: str, probe_text: str) -> str:
    """风险分级：content + 上下文快照一起过词表（评审 一.1 的 context_snapshot 用途）。"""
    low = probe_text.lower()
    for kw in _high_risk_words():
        if kw.lower() in low:
            return "high"
    rule = ATOMIC_RULES.get(task_type) or {}
    for kw in rule.get("risk_keywords") or ():
        if kw.lower() in low:
            return "high"
    return "low"


def _resp(decision_id: str, route_result: str, risk: str, cache_hit: bool,
           template_name: Optional[str], result_data: Any, t0: float,
           confidence: float, is_fast_path: bool, fallback_reason: Optional[str],
           code: int = 0, msg: str = "") -> Dict[str, Any]:
    """统一响应信封：评审 一.2 全部字段恒在（MoRay 侧按 11 必填键严校）。"""
    out = {"code": code, "route_result": route_result, "risk_level": risk,
           "cache_hit": bool(cache_hit), "template_name": template_name,
           "result_data": result_data, "decision_id": decision_id,
           "confidence": round(float(confidence), 2),
           "is_fast_path": bool(is_fast_path),
           "fallback_reason": fallback_reason,
           "rule_version": RULE_VERSION, "model_version": _model_version(),
           "mode": "cache" if cache_hit else ("rule" if code == 0 else "error"),
           "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    if msg:
        out["msg"] = msg[:200]
    return out


def _report(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        _reporter.report(event_type, payload)
    except Exception as e:  # noqa: BLE001 - 上报失败绝不影响本次决策返回
        logger.warning("[laya-dispatch] 事件上报入队失败（忽略）：%s", str(e)[:150])


def _maybe_stats_report() -> None:
    """周期 stats_report（评审 三.1 四类事件之一）：距上次 ≥窗口且有决策才发。"""
    global _last_stats_at
    now = time.time()
    if now - _last_stats_at < _STATS_INTERVAL or _stats["decisions"] == 0:
        return
    _last_stats_at = now
    _report("stats_report", {"window_s": int(_STATS_INTERVAL), "counters": dict(_stats),
                             "cache_size": len(_result_cache),
                             "cache_max": _RESULT_MAX, "cache_ttl_s": _RESULT_TTL,
                             "rule_version": RULE_VERSION,
                             "reporter": _reporter_status()})


def _reporter_status() -> Dict[str, int]:
    return {"sent": _reporter.sent, "spooled": _reporter.spooled,
            "replayed": _reporter.replayed, "dropped": _reporter.dropped}


@router.post("/api/v1/task/dispatch")
async def dispatch(req: DispatchRequest) -> Dict[str, Any]:
    """下行调度入口：校验 → 防循环 → 风险分级 → 缓存 → 模板路由 → 原生兜底。永不抛 500。"""
    from fastapi.responses import JSONResponse
    t0 = time.perf_counter()
    decision_id = uuid.uuid4().hex[:16]
    # ---- 0. 评审 一.3：严格入参校验 → 标准错误码 400（MoRay 侧自动降级） ----
    why = _validate_request(req)
    if why:
        _stats["invalid_requests"] += 1
        return JSONResponse(status_code=400, content=_resp(
            decision_id, "native", "low", False, None, None, t0,
            confidence=0.0, is_fast_path=False, fallback_reason="invalid_request",
            code=400, msg=why))
    _stats["decisions"] += 1
    # 半周期清扫过期缓存（评审 二.4）
    _purge_ctr[0] += 1
    if _purge_ctr[0] >= _PURGE_EVERY:
        _purge_ctr[0] = 0
        _purge_expired()
    try:
        # ---- 1. 防循环（评审 二.1）：x_hop_count 达上限 → 拒判，禁止继续穿 Laya ----
        if int(req.x_hop_count) >= MAX_HOPS:
            _stats["hop_rejects"] += 1
            return _resp(decision_id, "native", "low", False, None, None, t0,
                         confidence=0.95, is_fast_path=False,
                         fallback_reason="hop_limit_exceeded")
        text = _canon(req.task_content)
        probe_text = text if not req.context_snapshot else \
            "%s %s" % (text, _canon(req.context_snapshot))   # 快照纳入风险判定（评审 一.1）
        ctx = req.project_context or {}
        # ---- 2. 未知任务类型 → 如实上报 unknown_scenario，回原生（不硬猜路由） ----
        rule = ATOMIC_RULES.get(req.task_type)
        if rule is None:
            _stats["unknown"] += 1
            _report("unknown_scenario", {
                "task_id": req.task_id, "task_type": req.task_type,
                "scenario": text[:500],
                "plan_id": ctx.get("plan_id", ""), "project_id": ctx.get("project_id", "")})
            return _resp(decision_id, "native", "low", False, None, None, t0,
                         confidence=0.5, is_fast_path=False,
                         fallback_reason="unknown_scenario")
        # ---- 3. 高风险强制降级（评审 二.3）：禁快速通路+禁缓存，交回原生链路+原生风控 ----
        risk = _risk_level(req.task_type, probe_text)
        if risk == "high":
            _stats["high_risk"] += 1
            _report("high_risk_detected", {
                "task_id": req.task_id, "task_type": req.task_type,
                "reason": "高风险关键词命中", "is_fast_path": False,
                "plan_id": ctx.get("plan_id", ""), "project_id": ctx.get("project_id", "")})
            return _resp(decision_id, "native", "high", False, None, None, t0,
                         confidence=0.99, is_fast_path=False, fallback_reason="high_risk")
        # ---- 4. 结果缓存：低风险 + enable_cache；命中置信度 0.95（fast_path） ----
        if req.enable_cache and risk == "low":
            key = _cache_key(req.task_type, req.task_content)
            hit = _result_cache.get(key)
            if hit:
                entry, expire = hit
                if expire > time.time():
                    _result_cache.move_to_end(key)
                    _stats["cache_hits"] += 1
                    if 0.95 >= float(req.confidence_threshold or 0.7):
                        return _resp(decision_id, "cache", risk, True,
                                     entry.get("template_name"), entry.get("result_data"), t0,
                                     confidence=0.95, is_fast_path=True, fallback_reason=None)
                    return _resp(decision_id, "native", risk, False, None, None, t0,
                                 confidence=0.95, is_fast_path=False,
                                 fallback_reason="low_confidence")
                _result_cache.pop(key, None)
        # ---- 5. 原子模板路由；logs(template=None) → 原生本地扫描 ----
        conf = float(rule.get("confidence") or 0.9)
        tpl = rule.get("template")
        if tpl:
            if conf < float(req.confidence_threshold or 0.7):   # 请求方门槛更严 → 如实放弃
                _stats["low_confidence_rejects"] += 1
                return _resp(decision_id, "native", risk, False, None, None, t0,
                             confidence=conf, is_fast_path=False,
                             fallback_reason="low_confidence")
            return _resp(decision_id, "template", risk, False, tpl, None, t0,
                         confidence=conf, is_fast_path=True, fallback_reason=None)
        _stats["fallback_native"] += 1
        return _resp(decision_id, "native", risk, False, None, None, t0,
                     confidence=conf, is_fast_path=False, fallback_reason="local_action_native")
    except Exception as e:  # noqa: BLE001 - Laya 自身故障回 code:500，MoRay 自动降级+计熔断
        _report("task_exception", {
            "task_id": req.task_id, "plan_id": (req.project_context or {}).get("plan_id", ""),
            "error_text": "%s: %s" % (type(e).__name__, str(e)[:500])})
        return _resp(decision_id, "native", "low", False, None, None, t0,
                     confidence=0.0, is_fast_path=False, fallback_reason="internal_error",
                     code=500, msg=str(e))
    finally:
        _maybe_stats_report()


@router.post("/api/v1/task/outcome")
async def outcome(req: OutcomeRequest) -> Dict[str, Any]:
    """MoRay 成功结果回流 → 结果缓存（LRU+TTL）。只有 status=ok 的结果可缓存。"""
    if req.task_type not in ATOMIC_RULES or not isinstance(req.result_data, dict):
        return {"code": 400, "stored": False, "reason": "invalid",
                "rule_version": RULE_VERSION}
    if str(req.result_data.get("status") or "") != "ok":
        return {"code": 0, "stored": False, "reason": "only_ok_cached",
                "rule_version": RULE_VERSION}
    key = _cache_key(req.task_type, req.task_content)
    _result_cache[key] = ({"decision_id": req.decision_id, "task_id": req.task_id,
                           "template_name": (ATOMIC_RULES.get(req.task_type) or {}).get("template"),
                           "result_data": req.result_data},
                          time.time() + _RESULT_TTL)
    _result_cache.move_to_end(key)
    while len(_result_cache) > _RESULT_MAX:          # 评审 二.4：容量硬上限，LRU 逐出
        _result_cache.popitem(last=False)
    _stats["outcomes"] += 1
    return {"code": 0, "stored": True, "cache_size": len(_result_cache),
            "cache_max": _RESULT_MAX, "rule_version": RULE_VERSION}


@router.get("/api/v1/task/rules")
async def rules() -> Dict[str, Any]:
    """规则库快照 + 版本 + 缓存/上报器状态（调试面板/接线对齐用）。"""
    _purge_expired()
    return {"code": 0, "data": {
        "rule_version": RULE_VERSION, "model_version": _model_version(),
        "max_hops": MAX_HOPS,
        "atomic_rules": {k: {kk: vv for kk, vv in v.items()} for k, v in ATOMIC_RULES.items()},
        "high_risk_words": list(_high_risk_words()),
        "stats": dict(_stats),
        "result_cache": {"size": len(_result_cache), "ttl_s": _RESULT_TTL,
                         "max": _RESULT_MAX, "purge_every": _PURGE_EVERY},
        "reporter": {"callback": CALLBACK_URL, "spool": str(SPOOL_PATH),
                     **_reporter_status()},
    }}
