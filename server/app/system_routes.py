# -*- coding: utf-8 -*-
"""系统感知只读路由（2026-11-01）：实时器官探活 + 拦截事件流。

背景（真实断链）：7 端口探活跑在 self_check 的 30 分钟循环里，结果只作为一份
缓存快照对外（GET /api/self_check/report）；监控大屏要的是"现在这一秒谁活着"。
本模块补两条只读通道：

  GET /api/system/organs          10 端口实时 TCP 探活（复用 self_check._probe_port），
                                  1 秒 TTL 缓存 + 每口真实 latency_ms。**不读**那份
                                  30 分钟旧报告——旧报告是快照，不是探活结果。
  GET /api/system/events?since=   llm_proxy 拦截事件（物理剥夺 / 预算阻断）增量读取。

设计纪律（与全仓既有惯例一致）：
- 端口表不在本模块复制：运行时向 self_check 取 _PROBE_PORTS（self_check.py:57 是
  唯一定义处）。自检那边增删端口，这里自动跟上，不会两边漂移。
- 探针目标 = **写死的环回地址 + 模块常量端口**，请求方无法传入 host/port/URL，
  本模块不接受任何 URL 参数，不存在 SSRF 面。
- 拦截事件环形缓冲是进程内的（deque maxlen=200）：重启即清零，如实语义——它回答
  "本次运行周期内拦截了什么"；持久事实仍在 agent_tool_log / event_snapshots。
- fail-open：_emit_intercept 绝不抛、绝不阻塞（拦截日志失败不能反噬 LLM 主链路）；
  探活单口失败如实记 up=false，不让整张表变成 500。
"""
import asyncio
import collections
import threading
import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import self_check

router = APIRouter()

#: 1 秒 TTL 缓存时长。大屏按 1-2s 轮询时，同秒内的重复请求共用一轮探活结果。
_CACHE_TTL_S = 1.0
#: 单口探活超时（秒）。与 self_check._probe_port 默认值同量级：10 口并发，最坏
#: 1s 收敛，不会让前端轮询堆积。
_PROBE_TIMEOUT_S = 1.0

#: 端口 → (短 id, 中文名)。角色表只用于展示；端口集合本身以 self_check 为准。
_ORGAN_META = {
    8000: ("moray", "中枢薄后端"),
    8001: ("laya", "Laya 决策编排"),
    8005: ("zcode", "ZCode 沙箱"),
    8010: ("argus", "Argus 情报"),
    8004: ("iris-jsonrpc", "Iris 感官（给 Laya）"),
    9301: ("iris-sse", "Iris 感官（主服务）"),
    # [第3晚 2026-11-06] 三器官 HTTP 触手
    9310: ("scribe", "Scribe 文档器官"),
    9311: ("surgeon", "Surgeon 重构器官"),
    9312: ("aiops", "AIOps 运维器官"),
    11434: ("ollama", "Ollama 本地模型"),
}


def _probe_ports():
    """运行时取 self_check 的端口表（单一定义处），拿不到返回空元组。

    只读耦合私有常量有先例：self_check._collect_gate 就用 getattr 读
    synapse_bus._gate_state。相比在两边各写一份端口表，复制才是真正的漂移源。"""
    ports = getattr(self_check, "_PROBE_PORTS", ())
    return tuple(ports) if isinstance(ports, (tuple, list)) else ()


async def _probe_one(port: int) -> dict:
    """单口探活 + 真实耗时（毫秒）。连不上不是异常：up=False 本身就是事实。"""
    started = time.perf_counter()
    try:
        up = await self_check._probe_port(port, timeout=_PROBE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - 单口探针异常按"不可达"记，不炸整张表
        up = False
    latency = round((time.perf_counter() - started) * 1000.0, 1)
    return {"port": port, "up": bool(up), "latency_ms": latency}


#: TTL 缓存：{"ts": monotonic, "payload": dict}。刻意不加锁——探活本身是
#: fail-open 的只读动作，并发重复探活最多多开几条环回 socket（每轮 ≤7 条），
#: 而模块级 asyncio.Lock 会绑定事件循环，重复建 app 的测试场景反而容易炸。
_cache = {"ts": 0.0, "payload": None}


async def collect_organs() -> dict:
    """跑一轮实时探活（1 秒内命中缓存）。"""
    now = time.monotonic()
    payload = _cache.get("payload")
    if payload is not None and now - float(_cache.get("ts") or 0.0) < _CACHE_TTL_S:
        return {**payload, "cached": True}

    ports = _probe_ports()
    started = time.perf_counter()
    rows = await asyncio.gather(*(_probe_one(p) for p in ports))
    elapsed = round((time.perf_counter() - started) * 1000.0, 1)
    organs = []
    for row in rows:
        pid, name = _ORGAN_META.get(row["port"], ("", "未登记器官"))
        organs.append({
            "port": row["port"],
            "id": pid,
            "name": name,
            "up": row["up"],
            "latency_ms": row["latency_ms"],
        })
    payload = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_ms": elapsed,
        "up": sum(1 for o in organs if o["up"]),
        "total": len(organs),
        "organs": organs,
    }
    _cache["ts"] = time.monotonic()
    _cache["payload"] = payload
    return {**payload, "cached": False}


@router.get("/api/system/organs")
async def system_organs():
    """实时器官面板：7 端口存活 + 每口真实握手耗时（毫秒）。

    cached=True 表示落在 1 秒 TTL 内（复用了上一次探活结果，ts 仍是那一次的时刻）。
    """
    try:
        return {"ok": True, **(await collect_organs())}
    except Exception as e:  # noqa: BLE001 - 整轮异常如实报，绝不伪造全绿
        return JSONResponse(status_code=500, content={
            "ok": False, "code": "probe_failed",
            "message": "%s: %s" % (type(e).__name__, str(e)[:200])})


# ---------------- 拦截事件环形缓冲（物理剥夺 / 预算阻断） ----------------

#: 环形缓冲上限：满了丢最旧（self_check.ErrorRingBuffer 同哲学；这里 200 是因为
#: 拦截事件罕见——要的是"最近发生了什么"，不是全量审计）
_EVENT_MAX = 200
_EVENTS = collections.deque(maxlen=_EVENT_MAX)
_seq_lock = threading.Lock()
_seq = 0


def _emit_intercept(event_type: str, msg: str) -> None:
    """记一次拦截（进程内，最多 200 条）。

    红线：本函数绝不抛、绝不阻塞——它是旁路观测，失败绝不能反噬被观测的 LLM
    主链路（organ_memory / synapse_bus.send 同款 fail-open 约定）。
    字段：seq（单调自增，供 since 增量拉取）/ ts（本地 ISO 8601）/ type / msg。"""
    global _seq
    try:
        with _seq_lock:
            _seq += 1
            seq = _seq
        _EVENTS.append({
            "seq": seq,
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "type": str(event_type or "")[:40],
            "msg": str(msg or "")[:300],
        })
    except Exception:  # noqa: BLE001 - 观测失败绝不影响被观测的主流程
        pass


@router.get("/api/system/events")
async def system_events(since: int = 0, limit: int = 50):
    """拦截事件增量读取：只返回 seq > since 的记录（最新在前）。

    gap=True 表示 since 之后的事件已被环形缓冲挤掉（进程内不留全量），调用方
    据此提示"中间有丢失"，而不是假装连续。"""
    since = max(0, int(since or 0))
    limit = max(1, min(int(limit or 50), _EVENT_MAX))
    snapshot = list(_EVENTS)
    items = [dict(r) for r in snapshot if int(r.get("seq") or 0) > since]
    oldest = snapshot[0]["seq"] if snapshot else 0
    gap = bool(snapshot) and since + 1 < int(oldest)
    items = items[-limit:]
    items.reverse()  # 最新在前：消费侧不必再排序
    return {"ok": True, "items": items, "count": len(items),
            "total": len(snapshot), "last_seq": _seq,
            "gap": gap, "buffer_max": _EVENT_MAX}
