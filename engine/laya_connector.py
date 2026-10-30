# -*- coding: utf-8 -*-
"""LayaConnector：L7.5.1 Laya联动 · 下行调度连接器（旁路层，2026-09-29 架构评审补充版）。

位置（任务书 一.1）：ExecutionScheduler._execute 与 TaskTemplateRunner 之间的旁路层。
execution_scheduler 只有一行调用 `bypass(...)`，返回 False 时原生逻辑逐字节不变。

铁则映射：
  * 原子模板零修改：只经 runner_factory 公开接口建 runner、调 run()——与调度器同款
    姿势，L6.5/L6.0 模板内钩子自动继承，不替代任何原有逻辑；
  * 纯旁路：不进 execution_scheduler 主循环/决策分支；
  * 总开关默认关闭：config.yaml laya_link.enabled=false，关 = 零 HTTP/零文件/零留痕；
  * 自动降级熔断（评审 二.2 分级版）：
      单次异常 → 单次降级原生 + WARNING 日志（本仓 INFO 不落盘惯例）；
      连续 fuse_threshold(默认5) 次失败 → 临时熔断 fuse_duration_s(默认30s)，期内静默走原生；
      到期自动放行一次**试探**（半开），成功 → 熔断解除；失败 → 时长翻倍延长（封顶
      fuse_max_s 默认600s），绝不抛异常、绝不阻塞主流程；
  * 幂等 + 防循环（评审 二.1）：
      同一 (plan_id, task_id) 只调度一次（重试走原生，保"修完重测"语义）；
      同内容 (task_type+内容哈希) 全局经 Laya 每窗口最多 2 次，x_hop_count 随调度递增
      并随请求上送，>2 强制原生——杜绝 事件→再调度→事件 的环路；本层任何路径
      都不得回指 /api/autonomous/*（L7 编排入口），事件处理是纯汇（sink）。
  * 契约严校验（评审 一.3）：响应必须含 code/route_result/risk_level/cache_hit/
    decision_id/confidence/is_fast_path/fallback_reason/rule_version/model_version/
    latency_ms 全键且类型正确；缺键/坏类型 = 契约故障 → 降级原生 + 计入熔断。
  * 置信门槛（评审 一.1）：confidence < confidence_threshold(默认0.7) 的模板/缓存决策
    不采纳，降级原生。
  * 高风险强制降级（评审 二.3）：risk_level=high → 不执行任何快速通路（缓存/捷径全禁），
    强制原生链路 + 原生风控（L7.0 核心文件判定/token 预算/human_intervention 闸均在
    原生侧照常执行，旁路不绕过权限管控）。
  * 全链路留痕：Laya decision_id（+rule_version/model_version/confidence/fallback_reason）
    写入子任务 info["laya"]，与 MoRay plan_id:task_id 绑定，经 /api/autonomous/{plan_id} 审计。

上行联动（与 laya_event_handler.py 协作）：高风险等事件 → apply_directive() 落 DAG 指令
  （work/laya/directives.json，跨重启持久）→ 每个原子任务执行点消费：
    force_native = 该计划后续任务不再走 Laya 决策，强制原生；skip_task = 指定任务跳过。

配置：config.yaml laya_link（mtime 热加载）+ 环境变量覆盖（MORAY_LAYA_*，env 优先）；
非法值一律回落默认，绝不让坏配置阻断调度。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("moray.laya_connector")

MORAY_ROOT = Path(__file__).resolve().parents[1]

# 内置默认 = 任务书 laya_link 形状 + 评审补充键（config.yaml 缺失/坏 → 用它）
_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "host": "127.0.0.1",
    "port": 8001,
    "timeout_ms": 200,
    "fuse_threshold": 5,       # 评审 二.2：连续5次 → 熔断
    "fuse_duration_s": 30,     # 评审 二.2：临时熔断30秒
    "fuse_max_s": 600,         # 评审 二.2：试探失败翻倍延长的封顶
    "confidence_threshold": 0.7,   # 评审 一.1：置信度门槛（默认0.7）
    "auto_fix_on_exception": True,
}

# 模板白名单：Laya 只能派这 4 个原子模板；名单外/类型不匹配 = 坏决策 → 回落原生
TEMPLATE_WHITELIST = ("code_review_flow", "text_fix_flow", "error_fix_flow", "test_run_flow")
_TPL_ALLOWED_BY_TYPE = {
    "review": {"code_review_flow"},
    "fix": {"text_fix_flow", "error_fix_flow"},   # error_fix_flow 仅当 params 带截图才允许
    "test": {"test_run_flow"},
}

# 响应契约必填键（评审 一.2/一.3）：缺一键 = 契约故障
_ROUTE_REQUIRED_KEYS = ("code", "route_result", "risk_level", "cache_hit", "decision_id",
                        "confidence", "is_fast_path", "fallback_reason",
                        "rule_version", "model_version", "latency_ms")
_ROUTE_RESULT_ENUM = ("cache", "template", "native")
_RISK_ENUM = ("low", "medium", "high")

# 防循环（评审 二.1）：同内容经 Laya 每窗口最多 MAX_HOPS 次
MAX_HOPS = 2
_HOP_TTL = 600.0

# 结果缓存回流/采纳只带这些子任务字段
_INFO_CACHE_FIELDS = ("status", "summary", "report_text", "diff",
                      "test_exit_code", "tests_passed", "fail_text", "engine_summary")

_recent: Dict[str, float] = {}          # 幂等：已调度 (plan:task) → monotonic 时刻
_RECENT_TTL = 600.0
_hops: Dict[str, list] = {}             # 防循环：内容键 → [dispatch 次数, 最近时刻]
# 分级熔断状态机：fails=连败数；open_until=熔断截止(monotonic)；
# window_s=当前熔断窗口（试探失败翻倍；None=未升级，用配置基值）；probing=半开试探占用中
_fuse: Dict[str, Any] = {"fails": 0, "open_until": 0.0, "window_s": None, "probing": False}
_stats: Dict[str, int] = {"dispatches": 0, "cache_hits": 0, "template_routed": 0,
                          "native_fallback": 0, "high_risk_native": 0, "single_fallbacks": 0,
                          "fuse_opens": 0, "fuse_probe_fails": 0, "fuse_probe_ok": 0,
                          "dedup_skips": 0, "hop_blocked": 0, "contract_fails": 0,
                          "low_confidence": 0, "failures": 0}
_cfg_cache: Optional[Tuple[float, str, Dict[str, Any]]] = None   # (mtime, path, cfg)


# ---------------- 配置（config.yaml + env 覆盖，全链路防御） ----------------

def _cfg_path() -> Path:
    """测试/隔离实例可用 MORAY_LAYA_CONFIG 指向自己的 yaml，正式默认根目录 config.yaml。"""
    return Path(os.environ.get("MORAY_LAYA_CONFIG", "").strip()
                or (MORAY_ROOT / "config.yaml"))


def _as_bool(v: Any, dft: bool) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return dft


def _as_int(v: Any, dft: int, lo: int, hi: int) -> int:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return dft
    return min(hi, max(lo, n))


def _as_float(v: Any, dft: float, lo: float, hi: float) -> float:
    try:
        n = float(str(v).strip())
    except (TypeError, ValueError):
        return dft
    return min(hi, max(lo, n))


def cfg() -> Dict[str, Any]:
    """读取 laya_link 配置：yaml(mtime 热加载) ← env 覆盖 ← 非法回落默认。绝不抛异常。"""
    global _cfg_cache
    path = _cfg_path()
    mtime = 0.0
    raw = ""
    try:
        mtime = path.stat().st_mtime
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    if _cfg_cache and _cfg_cache[0] == mtime and _cfg_cache[1] == str(path):
        out = dict(_cfg_cache[2])
    else:
        out = dict(_DEFAULTS)
        try:
            import yaml
            block = ((yaml.safe_load(raw) or {}) if raw else {}).get("laya_link") or {}
            if isinstance(block, dict):
                if "enabled" in block:
                    out["enabled"] = _as_bool(block["enabled"], False)
                if "auto_fix_on_exception" in block:
                    out["auto_fix_on_exception"] = _as_bool(block["auto_fix_on_exception"], True)
                if block.get("host"):
                    out["host"] = str(block["host"]).strip()
                out["port"] = _as_int(block.get("port"), _DEFAULTS["port"], 1, 65535)
                out["timeout_ms"] = _as_int(block.get("timeout_ms"), _DEFAULTS["timeout_ms"], 20, 60000)
                out["fuse_threshold"] = _as_int(block.get("fuse_threshold"),
                                                _DEFAULTS["fuse_threshold"], 1, 100)
                out["fuse_duration_s"] = _as_int(block.get("fuse_duration_s"),
                                                 _DEFAULTS["fuse_duration_s"], 1, 86400)
                out["fuse_max_s"] = _as_int(block.get("fuse_max_s"),
                                            _DEFAULTS["fuse_max_s"], 1, 86400)
                out["confidence_threshold"] = _as_float(block.get("confidence_threshold"),
                                                        _DEFAULTS["confidence_threshold"], 0.0, 1.0)
        except Exception as e:  # noqa: BLE001 - yaml 缺失/坏文件 = 回落默认，不阻断调度
            logger.warning("[laya] config.yaml 解析失败回落默认: %s", str(e)[:200])
        _cfg_cache = (mtime, str(path), dict(out))
    # ---- 环境变量覆盖（优先级最高；隔离实例显式注入用） ----
    env_map = {
        "enabled": ("MORAY_LAYA_LINK", lambda v, d: _as_bool(v, d)),
        "host": ("MORAY_LAYA_HOST", lambda v, d: str(v).strip() or d),
        "port": ("MORAY_LAYA_PORT", lambda v, d: _as_int(v, d, 1, 65535)),
        "timeout_ms": ("MORAY_LAYA_TIMEOUT_MS", lambda v, d: _as_int(v, d, 20, 60000)),
        "fuse_threshold": ("MORAY_LAYA_FUSE_THRESHOLD", lambda v, d: _as_int(v, d, 1, 100)),
        "fuse_duration_s": ("MORAY_LAYA_FUSE_DURATION_S", lambda v, d: _as_int(v, d, 1, 86400)),
        "fuse_max_s": ("MORAY_LAYA_FUSE_MAX_S", lambda v, d: _as_int(v, d, 1, 86400)),
        "confidence_threshold": ("MORAY_LAYA_CONFIDENCE",
                                 lambda v, d: _as_float(v, d, 0.0, 1.0)),
        "auto_fix_on_exception": ("MORAY_LAYA_AUTO_FIX", lambda v, d: _as_bool(v, d)),
    }
    for k, (env, conv) in env_map.items():
        v = os.environ.get(env, "").strip()
        if v:
            out[k] = conv(v, out[k])
    return out


def laya_on() -> bool:
    """联动总闸（默认关，正向白名单，同 task_planner.planner_on 惯例）。"""
    return bool(cfg().get("enabled"))


def dispatch_url(c: Dict[str, Any]) -> str:
    return "http://%s:%d/api/v1/task/dispatch" % (c.get("host") or "127.0.0.1",
                                                  int(c.get("port") or 8001))


# ---------------- 分级熔断（评审 二.2） ----------------

def _fuse_phase() -> str:
    """closed=正常 / open=熔断期内 / probe=到期半开（放行一次试探）。"""
    until = float(_fuse.get("open_until") or 0.0)
    if until and time.monotonic() < until:
        return "open"
    if until:                          # 到期：进入半开试探
        return "probe" if not _fuse.get("probing") else "open"
    return "closed"


def _record_failure(reason: str) -> None:
    """分级熔断计数。
    半开试探失败（_fuse_phase()=probe，由 _fail 先清 probing 标志后落入此分支）
    → 熔断时长翻倍延长（封顶 fuse_max_s）；
    普通失败 → 单次降级 + WARNING 日志；连败达阈值 → 临时熔断。"""
    _stats["failures"] += 1
    _stats["single_fallbacks"] += 1
    if _fuse_phase() == "probe":
        base = float(_fuse.get("window_s") or _cfg_dur())
        _fuse["window_s"] = min(float(cfg().get("fuse_max_s")), base * 2)
        _fuse["open_until"] = time.monotonic() + _fuse["window_s"]
        _stats["fuse_probe_fails"] += 1
        logger.warning("[laya] 熔断试探失败（原因=%s）→ 延长熔断至 %ds（封顶 %ds）",
                       reason, _fuse["window_s"], int(cfg().get("fuse_max_s")))
        return
    _fuse["fails"] = int(_fuse["fails"]) + 1
    logger.warning("[laya] 单次异常降级原生：%s（连败 %d/%d）", reason,
                   _fuse["fails"], _fuse_threshold())
    if _fuse["fails"] >= _fuse_threshold() and _fuse_phase() == "closed":
        _fuse["window_s"] = _fuse.get("window_s") or _cfg_dur()
        _fuse["open_until"] = time.monotonic() + _fuse["window_s"]
        _stats["fuse_opens"] += 1
        logger.warning("[laya] 临时熔断开启 %ds（连续失败 %d 次，最后原因=%s）——期内静默走原生",
                       _fuse["window_s"], _fuse["fails"], reason)


def _reset_fuse() -> None:
    """任一成功 = 熔断解除；若此刻正持半开试探标志，如实计一次试探成功。
    窗口回落配置基值（window_s=None → 下次开熔断重新从基值起步）。"""
    if _fuse.get("probing"):
        _stats["fuse_probe_ok"] += 1
        logger.warning("[laya] 熔断试探成功，联动恢复")
    _fuse["fails"] = 0
    _fuse["open_until"] = 0.0
    _fuse["window_s"] = None
    _fuse["probing"] = False


def _fuse_threshold() -> int:
    return max(1, int(cfg().get("fuse_threshold") or 5))


def _cfg_dur() -> int:
    return max(1, int(cfg().get("fuse_duration_s") or 30))


def fuse_state() -> Dict[str, Any]:
    """状态探针（/api/laya/state 与测试断言）：阶段 + 连败 + 剩余 + 当前窗口（含延长后）。"""
    until = float(_fuse.get("open_until") or 0.0)
    return {"phase": _fuse_phase(),
            "open": _fuse_phase() == "open",
            "fails": int(_fuse["fails"]),
            "remain_s": round(max(0.0, until - time.monotonic()), 1),
            "window_s": _fuse.get("window_s"),
            "base_duration_s": _cfg_dur(),
            "threshold": _fuse_threshold(),
            "max_s": int(cfg().get("fuse_max_s"))}


# ---------------- DAG 指令（上行高风险事件 → 执行点干预） ----------------

def directives_path() -> Path:
    return Path(os.environ.get("MORAY_LAYA_DIRECTIVES", "").strip()
                or (MORAY_ROOT / "work" / "laya" / "directives.json"))


def _load_directives() -> list:
    try:
        data = json.loads(directives_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_directives(items: list) -> None:
    """tmp→replace 原子写（本仓 sidecar 惯例）；落盘失败只影响持久性，不影响本进程语义。"""
    try:
        p = directives_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-100:], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:  # noqa: BLE001
        logger.warning("[laya] 指令落盘失败（不影响执行）: %s", str(e)[:150])


def apply_directive(plan_id: str, kind: str, task_id: str = "", reason: str = "",
                    ttl_s: int = 3600) -> Dict[str, Any]:
    """登记 DAG 指令（laya_event_handler 的高风险事件入口调用）。幂等去重。"""
    d = {"plan_id": str(plan_id or ""), "kind": str(kind),
         "task_id": str(task_id or ""), "reason": str(reason or "")[:300],
         "at": time.time(),
         "at_iso": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
         "expires": time.time() + max(1, int(ttl_s))}
    items = [x for x in _load_directives()
             if not (x.get("plan_id") == d["plan_id"] and x.get("kind") == d["kind"]
                     and x.get("task_id") == d["task_id"] and x.get("expires", 0) > d["at"])]
    items.append(d)
    _save_directives(items)
    logger.warning("[laya] DAG 指令生效 plan=%s kind=%s task=%s（%s）",
                   d["plan_id"], d["kind"], d["task_id"] or "-", d["reason"][:80])
    return d


def pending_directives() -> list:
    """未过期指令（调试/测试用，纯读）。"""
    now = time.time()
    return [d for d in _load_directives() if float(d.get("expires") or 0) > now]


def _plan_force_native(plan_id: str) -> bool:
    now = time.time()
    return any(d.get("kind") == "force_native" and d.get("plan_id") == plan_id
               and float(d.get("expires") or 0) > now for d in _load_directives())


def _take_skip_directive(plan_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """消费一条匹配的 skip_task 指令（一次性：修复重试等二次执行该任务照常跑）。"""
    items = _load_directives()
    now = time.time()
    for i, d in enumerate(items):
        if (d.get("kind") == "skip_task" and d.get("plan_id") == plan_id
                and d.get("task_id") == task_id and float(d.get("expires") or 0) > now):
            _save_directives(items[:i] + items[i + 1:])
            return d
    return None


# ---------------- 幂等去重 + 防循环 hop 计数 ----------------

def _prune_recent(now: float) -> None:
    for k in [k for k, ts in _recent.items() if now - ts > _RECENT_TTL]:
        _recent.pop(k, None)


def _canonical(content: Any) -> str:
    """内容规范化串——与 Laya 侧 _canon 同规则（sort_keys + ensure_ascii=False）。"""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(content)


def _hop_key(ttype: str, content: Any) -> str:
    return "%s:%s" % (ttype, hashlib.sha1(_canonical(content).encode("utf-8")).hexdigest())


def _hop_count(hkey: str) -> int:
    """当前已穿过 Laya 的次数（含失败尝试）；过期窗口自动清零。"""
    rec = _hops.get(hkey)
    if not rec:
        return 0
    if time.monotonic() - rec[1] > _HOP_TTL:
        _hops.pop(hkey, None)
        return 0
    return int(rec[0])


def _hop_bump(hkey: str) -> None:
    rec = _hops.get(hkey)
    n = (int(rec[0]) + 1) if rec else 1
    _hops[hkey] = [n, time.monotonic()]
    if len(_hops) > 5000:                       # 防 hop 表自身内存膨胀
        cutoff = time.monotonic() - _HOP_TTL
        for k in [k for k, v in _hops.items() if v[1] < cutoff]:
            _hops.pop(k, None)


# ---------------- 下行契约：请求装配 / 响应严校 ----------------

def _task_content(ttype: str, task: Dict[str, Any], run: Any,
                  params: Dict[str, Any]) -> Dict[str, Any]:
    c: Dict[str, Any] = {"task_type": ttype, "title": str(task.get("title") or ""),
                         "goal": str(run.goal or "")[:500]}
    if ttype == "fix":
        c["error_text"] = str(params.get("error_text") or "")[:2000]
    if ttype == "test":
        c["test_cmd"] = str(params.get("test_cmd") or "")
    return c


def _context_snapshot(run: Any) -> Dict[str, Any]:
    """评审 一.1：上下文快照（执行到哪、上一步结果如何），供 Laya 规则层做更准的风险判定。"""
    snap: Dict[str, Any] = {"plan_status": getattr(run, "status", ""),
                            "subtasks_done": len(getattr(run, "subtasks", []) or []),
                            "decisions": len(getattr(run, "decisions", []) or [])}
    last = (getattr(run, "subtasks", []) or [{}])[-1] or {}
    snap["last_subtask"] = {"type": str(last.get("type") or ""),
                            "status": str(last.get("status") or ""),
                            "summary": str(last.get("summary") or "")[:150]}
    return snap


def _tpl_ok(ttype: str, tpl: str, params: Dict[str, Any]) -> bool:
    """模板决策合法性：白名单 + 类型匹配；error_fix_flow 必须有截图入参才能跑。"""
    if tpl not in TEMPLATE_WHITELIST or tpl not in _TPL_ALLOWED_BY_TYPE.get(ttype, set()):
        return False
    if tpl == "error_fix_flow" and not params.get("screenshot"):
        return False
    return True


def _validate_route(data: Any) -> Optional[str]:
    """响应契约严校验（评审 一.2/一.3）。合法返回 None，否则返回错误说明。"""
    if not isinstance(data, dict):
        return "非 JSON 对象"
    missing = [k for k in _ROUTE_REQUIRED_KEYS if k not in data]
    if missing:
        return "缺键 " + ",".join(missing)
    if str(data.get("route_result")) not in _ROUTE_RESULT_ENUM:
        return "route_result 非法: %r" % (data.get("route_result"),)
    if str(data.get("risk_level")) not in _RISK_ENUM:
        return "risk_level 非法: %r" % (data.get("risk_level"),)
    if not isinstance(data.get("cache_hit"), bool):
        return "cache_hit 非 bool"
    if not str(data.get("decision_id") or "").strip():
        return "decision_id 缺失/为空"
    try:
        conf = float(data.get("confidence"))
        if not (0.0 <= conf <= 1.0):
            return "confidence 越界: %s" % conf
    except (TypeError, ValueError):
        return "confidence 非数值"
    return None


# ---------------- 下行调度主入口（execution_scheduler 唯一被调点） ----------------

async def bypass(ttype: str, task: Dict[str, Any], run: Any, pid: str, pdir: str,
                 params: Dict[str, Any], info: Dict[str, Any],
                 factory: Callable[..., Any], summarize: Callable[..., Dict[str, Any]]
                 ) -> bool:
    """旁路调度一个原子任务。True = 本层已接管并填好 info；False = 原生链路照常走。

    任何内部异常 → return False（铁则4：静默降级，不抛、不阻塞）。
    """
    t0 = time.monotonic()
    try:
        c = cfg()
        if not c.get("enabled"):
            return False                      # 铁则3：总闸关 = 零 HTTP / 零文件 / 零留痕
        plan_id = str(getattr(run, "plan_id", "") or "")
        task_id = str(task.get("id") or "")
        key = "%s:%s" % (plan_id, task_id)

        # ---- 1. DAG 指令：skip_task（高风险事件干预后续任务） ----
        d = _take_skip_directive(plan_id, task_id)
        if d is not None:
            info["status"] = "skipped"
            info["summary"] = "已按 Laya 指令跳过（%s）" % (d.get("reason") or "高风险")[:120]
            run.add_decision("laya-directive", "Laya 高风险指令命中，跳过该子任务",
                             "directive=skip_task reason=%s" % str(d.get("reason") or "")[:200],
                             "状态标 skipped（如实计入 partial）")
            _finalize(info, t0)
            return True

        # ---- 2. DAG 指令：force_native（该计划止损，回原生但仍审计留痕） ----
        if _plan_force_native(plan_id):
            run.add_decision("laya-directive", "Laya 高风险指令生效：本任务强制原生执行",
                             "directive=force_native plan=%s" % plan_id[:32], "走原生链路")
            _stats["native_fallback"] += 1
            return False

        # ---- 3. 分级熔断：open 期内连 HTTP 都不发；probe 期放行一次半开试探 ----
        phase = _fuse_phase()
        if phase == "open":
            run.add_decision("laya-fuse", "Laya 熔断中，本任务静默走原生",
                             "fuse remain=%ss window=%ss" % (fuse_state()["remain_s"],
                                                             fuse_state()["window_s"]),
                             "降级原生")
            _stats["native_fallback"] += 1
            return False

        content = _task_content(ttype, task, run, params)
        hkey = _hop_key(ttype, content)

        # ---- 4. 幂等：同一 (plan,task) 只调度一次；重试用原生（保"修完重测"语义） ----
        now = time.monotonic()
        _prune_recent(now)
        if key in _recent:
            run.add_decision("laya-dedup", "任务已调度过，重复执行直接走原生（幂等）",
                             "task_key=%s" % key[:64], "降级原生")
            _stats["dedup_skips"] += 1
            return False

        # ---- 5. 防循环（评审 二.1）：同内容穿过 Laya 达 2 次 = 禁止第 3 次，强制原生 ----
        hops = _hop_count(hkey)
        if hops >= MAX_HOPS:
            run.add_decision("laya-hop", "同内容穿过 Laya 已达上限（防循环），强制原生",
                             "hops=%d>=MAX%d key=%s" % (hops, MAX_HOPS, hkey[:48]), "降级原生")
            _stats["hop_blocked"] += 1
            return False

        body = {"task_id": key, "task_type": ttype, "task_content": content,
                "project_context": {"plan_id": plan_id, "project_id": pid,
                                    "project_dir": pdir, "goal": str(run.goal or "")[:300]},
                "enable_cache": True,
                # ---- 评审 一.1 契约新增字段 ----
                "confidence_threshold": float(c.get("confidence_threshold")),
                "x_hop_count": hops,          # 本任务内容已穿过 Laya 的次数
                "context_snapshot": _context_snapshot(run)}
        _stats["dispatches"] += 1
        _hop_bump(hkey)                        # 穿过一次 Laya 即计数（成功/失败都算）
        route = await _call_dispatch(c, body, probing=(phase == "probe"))
        if route is None:                     # 不可达/超时/报错/契约坏 → 已计分级熔断
            return False
        _recent[key] = now

        info["laya"] = {k: route.get(k) for k in
                        ("decision_id", "route_result", "risk_level", "cache_hit",
                         "template_name", "confidence", "is_fast_path", "fallback_reason",
                         "rule_version", "model_version", "latency_ms")}
        decision_id = str(route.get("decision_id") or "")

        # ---- 6. 高风险强制降级（评审 二.3）：禁一切快速通路 → 原生链路+原生风控；
        #      权限管控（L7.0 核心文件判定/token 预算/human_intervention）在原生侧照常生效 ----
        if str(route.get("risk_level")) == "high":
            _stats["high_risk_native"] += 1
            run.add_decision("laya", "Laya 判定任务高风险：禁用快速通路，强制原生链路（不绕过权限管控）",
                             "decision_id=%s rule=%s fast=%s" % (decision_id[:24],
                                                                 route.get("rule_version"),
                                                                 route.get("is_fast_path")),
                             "原生执行+原生风控")
            return False

        rr = str(route.get("route_result"))

        # ---- 7. 置信门槛（评审 一.1）：低于阈值不采纳（防御性二次校验，Laya 侧同判） ----
        if rr in ("cache", "template") \
                and float(route.get("confidence")) < float(c.get("confidence_threshold")):
            _stats["low_confidence"] += 1
            run.add_decision("laya-lowconf", "Laya 决策置信度 %.2f < 门槛 %.2f，不采纳"
                             % (float(route.get("confidence")),
                                float(c.get("confidence_threshold"))),
                             "decision_id=%s fast=%s fallback=%s" % (decision_id[:24],
                                                                     route.get("is_fast_path"),
                                                                     route.get("fallback_reason")),
                             "降级原生")
            return False

        # ---- 8. 走缓存：outcome 回流过的同内容任务 → 直接采纳历史执行结果 ----
        if rr == "cache" and bool(route.get("cache_hit")) \
                and isinstance(route.get("result_data"), dict):
            for fld in _INFO_CACHE_FIELDS:
                if fld in route["result_data"]:
                    info[fld] = route["result_data"][fld]
            info["laya_cache"] = True
            _stats["cache_hits"] += 1
            run.add_decision("laya-cache", "Laya 结果缓存命中（fast_path），采纳历史执行结果",
                             "decision_id=%s conf=%s content=%s" % (
                                 decision_id[:24], route.get("confidence"),
                                 _canonical(content)[:200]),
                             "跳过执行（status=%s）" % info.get("status"))
            _finalize(info, t0)
            return True

        # ---- 9. 走对应模板：经 runner_factory 公开接口执行（= 调度器同款，钩子全继承） ----
        tpl = str(route.get("template_name") or "")
        if rr == "template" and _tpl_ok(ttype, tpl, params):
            work_dir = str(Path(run.work_dir) / ("sub_%s" % (task_id or "x")))
            runner = factory(tpl, params, work_dir)
            progress = await runner.run()
            info.update(summarize(ttype, progress))
            _stats["template_routed"] += 1
            run.add_decision("laya-route", "Laya 路由到原子模板执行（fast_path）",
                             "decision_id=%s template=%s conf=%s rule=%s model=%s" % (
                                 decision_id[:24], tpl, route.get("confidence"),
                                 route.get("rule_version"), route.get("model_version")),
                             "status=%s" % info.get("status"))
            _finalize(info, t0)
            if info.get("status") == "ok":    # 成功结果回流 Laya → 下次同内容可缓存命中
                _fire_outcome(c, body, info)
            return True

        # ---- 10. Laya 明确回原生 / 模板名非法 → 原生链路保持原样 ----
        _stats["native_fallback"] += 1
        run.add_decision("laya", "Laya 决策为原生（route=%s）" % rr,
                         "decision_id=%s fallback_reason=%s" % (decision_id[:24],
                                                                 route.get("fallback_reason")),
                         "原生执行")
        return False
    except Exception as e:  # noqa: BLE001 - 铁则4：旁路自身故障绝不影响主流程
        logger.warning("[laya] bypass 内部异常（静默降级原生）: %s: %s",
                       type(e).__name__, str(e)[:200])
        _fuse["probing"] = False          # 防试探标记泄漏（否则熔断会卡在探测态永不放行）
        _record_failure("internal:%s" % type(e).__name__)
        return False


async def _call_dispatch(c: Dict[str, Any], body: Dict[str, Any],
                         probing: bool = False) -> Optional[Dict[str, Any]]:
    """调 Laya /api/v1/task/dispatch。成功返回决策 dict；失败返回 None 并计入分级熔断。"""
    import httpx
    if probing:
        _fuse["probing"] = True               # 半开试探占用；下方任一出口（_fail/_reset）必清
    try:
        async with httpx.AsyncClient(timeout=float(c["timeout_ms"]) / 1000.0) as client:
            r = await client.post(dispatch_url(c), json=body)
        if r.status_code != 200:
            _fail(probing, "http_%d" % r.status_code)
            return None
        data = r.json()
        if not isinstance(data, dict):
            _fail(probing, "contract:not_object")
            _stats["contract_fails"] += 1
            return None
        if data.get("code") != 0:
            _fail(probing, "code_%s(%s)" % (data.get("code"), str(data.get("msg") or data.get("fallback_reason") or "")[:80]))
            return None
        why = _validate_route(data)           # 评审 一.3：契约严校验
        if why:
            _stats["contract_fails"] += 1
            _fail(probing, "contract:%s" % why)
            return None
        _reset_fuse()                         # 任一成功 = 半开试探通过，熔断解除
        return data
    except Exception as e:  # noqa: BLE001 - 不可达/超时/坏 JSON 统一算失败
        _fail(probing, "%s(%s)" % (type(e).__name__, str(e)[:80]))
        return None


def _fail(probing: bool, reason: str) -> None:
    """失败统一入口：试探失败走 _record_failure 的延长分支；普通失败走连败累计分支。
    试探期间 _fuse["probing"]=True，_fuse_phase() 已判 open→不会误计连败。"""
    if probing:
        _fuse["probing"] = False              # 让 _record_failure 识别出"到期后的半开"状态
    _record_failure(reason)


def _fire_outcome(c: Dict[str, Any], body: Dict[str, Any], info: Dict[str, Any]) -> None:
    """成功结果回流 Laya 结果缓存（fire-and-forget：失败只记日志，绝不影响本任务）。"""
    try:
        import asyncio

        async def _post() -> None:
            import httpx
            try:
                payload = {"decision_id": str((info.get("laya") or {}).get("decision_id") or ""),
                           "task_id": body["task_id"], "task_type": body["task_type"],
                           "task_content": body["task_content"],
                           "result_data": {k: info[k] for k in _INFO_CACHE_FIELDS if k in info}}
                url = "http://%s:%d/api/v1/task/outcome" % (c["host"], c["port"])
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.post(url, json=payload)
            except Exception as e:  # noqa: BLE001
                logger.warning("[laya] 结果回流失败（忽略）: %s", str(e)[:120])

        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        pass                                  # 无运行中事件循环 = 不回流，主流程照旧


def _finalize(info: Dict[str, Any], t0: float) -> None:
    """补齐原生收尾语义（duration_ms / summary），保证 info 形状与 _execute 出参一致。"""
    info["duration_ms"] = int((time.monotonic() - t0) * 1000)
    info.setdefault("summary", "%s（%s）" % (info.get("title") or info.get("type") or "",
                                            info.get("status")))


def stats() -> Dict[str, int]:
    """旁路计数器（/api/laya/stats 与测试断言用；进程内存态，重启归零——如实标注）。"""
    return dict(_stats)


def reset_runtime_state() -> None:
    """测试钩子：清空调度去重/hop/熔断/计数（生产链路绝不调用——仅供单测隔离）。"""
    _recent.clear()
    _hops.clear()
    _fuse["fails"] = 0
    _fuse["open_until"] = 0.0
    _fuse["window_s"] = None
    _fuse["probing"] = False
    for k in _stats:
        _stats[k] = 0
    global _cfg_cache
    _cfg_cache = None
