# -*- coding: utf-8 -*-
r"""自省循环 self_check（2026-10-09 桥一）：进程内定时自检。级别边界 1-2——只记录 + 标记。

红线（任务书裁定，勿扩）：
- 不动手、不建议、不自动改任何东西——报告字段只放事实，永远不放"应该改 X"；
- 被观测模块（reflection / synapse_bus）只 getattr 读状态，零写入、零 import 副作用；
- 任何采集失败 fail-open 成 {"status": "no_data", "reason": ...}：不抛异常、不阻塞循环；
- 间隔硬编码 1800s，不进 config/env。

四项采集：
  ① ports   —— 7 端口 TCP 探活（并发 gather，超时 1s）；
  ② gate    —— 算力闸：先探 reflection._gate_state（2026-10-09 侦察实测该名字不在
                reflection.py，那边算力预算闸叫 _reflect_*），拿不到退同名模块级变量
                synapse_bus._gate_state（防环五闸活动状态，synapse_bus 的任务书定位
                本就是"只 import 读状态"）；source 字段如实写来源；
  ③ backlog —— synapse_bus.snapshot_chains()（纯只读导出）数"未闭合链"。判据如实：
                _gate_state 无显式终态字段，synapse_bus 自己的"闭合"定义 = _gate_sweep
                静默 5 分钟清扫，故快照里现存条目 ≡ 已起始且未被清扫的链；
  ④ errors  —— ErrorRingBuffer 30 分钟窗口内 ERROR 条数（读时过滤，非写时截断）。
                v1.5 追加 recent_items：同一条环里取最近 10 条 ERROR 摘要（时间倒序，
                msg 截 200 字符，零命中 []），不另起数据源。

v1.5 追加 ⑤ budget_gate —— reflection 算力预算闸（2026-10-07 v4）状态。真名以
2026-10-09 侦察为准（reflection.py L63-72）：全局冷却起点 _reflect_last_global、
per-trace 冷却表实名 _reflect_last_trace（≠v45 快照所记 _reflect_last_by_trace）、
日上限 _REFLECT_DAILY_CAP、当日计数 _reflect_daily_count + _reflect_daily_date。
dir()+getattr 只读探测，读不到降级 no_data 并报具体缺失变量。注意：
_reflect_last_global 是 time.monotonic() 基准，报告里用
global_cooldown_last_clock 字段如实标时钟，不冒充 epoch。

v1.6 追加（ERROR 落盘，补进程内环形缓冲的重启盲区）：get_error_file_handler() 把
ERROR+ 一行一条 append 进 logs/moray_errors.log（mode="a" 只追加永不截断；
不自动清、不轮转——轮转=按大小切=老数据丢。这是 MoRay"自我同一性"的第一份
磁盘记忆）；install_error_handlers() 统一把 ErrorRingBuffer + FileHandler 挂到
"moray" logger，幂等，main.py lifespan 调它替代原先的直接 addHandler。

输出：_LAST_REPORT（GET /api/self_check/report 数据源）+ 追加
D:\MoRay\logs\self_check.log（一行一 JSON，UTF-8，ensure_ascii=False）。
日志级别口径沿用本仓惯例：自定义 logger 的 INFO 不上控制台，需要被看见的一律 warning。
"""
import asyncio
import collections
import datetime
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger("moray.self_check")

#: 定时间隔：30 分钟，硬编码（任务书裁定不读 config）
_SELF_CHECK_INTERVAL = 1800
#: 探活端口：MoRay 8000 / 其余为本机生态（8001/8005/8010/8004/9301/Ollama 11434）
_PROBE_PORTS = (8000, 8001, 8005, 8010, 8004, 9301, 11434)
#: 输出文件：__file__ 解析（server/app/ 的上上级 = 工程根；main.py:8 "路径全部基于
#: __file__ 解析"同款惯例），实际即 D:\MoRay\logs\self_check.log
_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "self_check.log"

#: v1.6 ERROR 落盘文件：与 _LOG_PATH 同款 __file__ 解析（实际即
#: D:\MoRay\logs\moray_errors.log）。红线：只 append 永不截断，
#: 不自动清、不轮转——到需要轮转那天再说，先攒真历史。
_ERROR_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "moray_errors.log"


def read_recent_error_lines(marker: str, max_bytes: int = 512 * 1024, limit: int = 200):
    """[v1 批3 2026-10-13] 从 errors.log 尾部有界读、过滤含 marker 的行。
    只读工具——格式知识留本文件（formatter 与 parser 同居）。
    返回 (lines: list[str], scanned_bytes: int, truncated: bool)。
    """
    import os
    p = _ERROR_LOG_PATH
    if not p or not os.path.exists(p):
        return [], 0, False
    size = os.path.getsize(p)
    truncated = size > max_bytes
    # 二进制读：text 模式不支持端相对非零 seek（io.UnsupportedOperation:
    # can't do nonzero end-relative seeks），截断分支会直接抛；解码交
    # errors="replace"（可能从半个多字节字符起，残字节退化成替换符）。
    with open(p, "rb") as f:
        if truncated:
            f.seek(-max_bytes, os.SEEK_END)
            f.readline()  # 丢首行残段
        chunk = f.read()
    out = [ln for ln in chunk.decode("utf-8", errors="replace").splitlines() if marker in ln]
    return out[-limit:], min(size, max_bytes), truncated


#: 最近一次报告（只读端点数据源；首跑前为 None）
_LAST_REPORT: dict | None = None
#: ERROR 环形缓冲单例（main.py 挂 handler 与本模块读取必须是同一对象）
_ERROR_HANDLER: "ErrorRingBuffer | None" = None
#: v1.6 ERROR FileHandler 单例（install 与重取必须同一对象，防双开文件句柄）
_ERROR_FILE_HANDLER: "logging.FileHandler | None" = None


class ErrorRingBuffer(logging.Handler):
    """进程内 ERROR 攒 30 分钟窗口：(epoch, levelname, message) 三元组进 deque。

    level=ERROR 只收错误及以上；maxlen=2000 是内存上界、不是窗口——30 分钟窗口
    在读侧过滤（任务书裁定"读的时候过滤，不是写的时候"）。emit 吞掉一切异常：
    日志 handler 绝不能反过来砸断 record 链。"""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._ring: collections.deque = collections.deque(maxlen=2000)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ring.append((record.created, record.levelname, record.getMessage()))
        except Exception:  # noqa: BLE001 - 见类 docstring：吞异常是 handler 的本分
            pass


def get_error_handler() -> ErrorRingBuffer:
    """模块级单例。main.py lifespan 里 addHandler 用的就是这个对象。"""
    global _ERROR_HANDLER
    if _ERROR_HANDLER is None:
        _ERROR_HANDLER = ErrorRingBuffer()
    return _ERROR_HANDLER


class _ISO8601LineFormatter(logging.Formatter):
    """一行一条：ISO8601 本地时区 | levelname | message。

    astimezone() 不带参数 = 系统本地时区带偏移（与报告 ts 同款口径）。
    换行统一转义成字面 \\n——带异常栈/多行 message 的记录仍严格一行一条，
    不断尾、不破 grep；栈信息本身不丢。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(
            record.created).astimezone().isoformat(timespec="seconds")
        msg = record.getMessage()
        if record.exc_info:
            msg = "%s | %s" % (msg, self.formatException(record.exc_info))
        if record.stack_info:
            msg = "%s | %s" % (msg, self.formatStack(record.stack_info))
        return "%s | %s | %s" % (ts, record.levelname, msg.replace("\n", "\\n"))


def get_error_file_handler() -> logging.FileHandler:
    """v1.6 模块级单例：ERROR+ 落盘 logs/moray_errors.log。

    mode="a" 是红线——append，绝不 truncate；不自动清、不轮转。
    打开失败（目录缺失/占用）直接抛给调用方：这是启动期事实，
    不静默——同一性记忆挂不上就该被看见。"""
    global _ERROR_FILE_HANDLER
    if _ERROR_FILE_HANDLER is None:
        handler = logging.FileHandler(filename=str(_ERROR_LOG_PATH),
                                      mode="a", encoding="utf-8")
        handler.setLevel(logging.ERROR)
        handler.setFormatter(_ISO8601LineFormatter())
        _ERROR_FILE_HANDLER = handler
    return _ERROR_FILE_HANDLER


def install_error_handlers() -> None:
    """v1.6：把 ErrorRingBuffer（进程内 30 分钟窗口）+ FileHandler（磁盘记忆）
    统一挂到 "moray" logger，main.py lifespan 调这一处即可。

    幂等：addHandler 前先查 logger.handlers——重复调用不重复挂，
    同 handler 挂两遍 = 每条 ERROR 落盘双份。"""
    moray = logging.getLogger("moray")
    for handler in (get_error_handler(), get_error_file_handler()):
        if handler not in moray.handlers:
            moray.addHandler(handler)


def _collect_recent_errors(window_sec: int = _SELF_CHECK_INTERVAL) -> int:
    """窗口内 ERROR 条数（created >= now - window 且 levelname == 'ERROR'）。"""
    cutoff = time.time() - window_sec
    return sum(1 for ts, levelname, _msg in get_error_handler()._ring
               if ts >= cutoff and levelname == "ERROR")


#: v1.5 ERROR 摘要条数上限：硬编码（任务书裁定不做配置）
_ERROR_ITEMS_MAX = 10


def _collect_recent_error_items(window_sec: int = _SELF_CHECK_INTERVAL) -> list:
    """窗口内最近 10 条 ERROR 摘要，时间倒序（最新在前），零命中 []。

    读 ErrorRingBuffer 同一条环（不另起数据源）；msg 截断到 200 字符、
    超出加 "..."；ts 转 ISO8601。"""
    cutoff = time.time() - window_sec
    hits = [(ts, levelname, msg) for ts, levelname, msg in get_error_handler()._ring
            if ts >= cutoff and levelname == "ERROR"]
    items = []
    for ts, levelname, msg in reversed(hits[-_ERROR_ITEMS_MAX:]):
        text = str(msg)
        if len(text) > 200:
            text = text[:200] + "..."
        items.append({
            "ts": datetime.datetime.fromtimestamp(ts).astimezone().isoformat(
                timespec="seconds"),
            "level": levelname,
            "msg": text,
        })
    return items


async def _probe_port(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """TCP connect 探活：1 秒内握手成功 = True；超时/拒绝/任何 OSError = False。"""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:  # noqa: BLE001 - 关闭失败不影响"连通过"这个事实
        pass
    return True


def _collect_gate() -> dict:
    """② 算力闸。dir() 探测，两级回退，全程 getattr 只读、绝不抛。"""
    try:
        from . import reflection
        if "_gate_state" in dir(reflection):
            gs = getattr(reflection, "_gate_state", None)
            if isinstance(gs, dict):
                return {"status": "ok", "source": "reflection._gate_state",
                        "traces": len(gs)}
    except Exception as e:  # noqa: BLE001
        logger.warning("[self_check] 探 reflection._gate_state 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
    # 退同名模块级变量：synapse_bus._gate_state（防环五闸的活动链状态）
    try:
        from . import synapse_bus
        gs = getattr(synapse_bus, "_gate_state", None)
        if isinstance(gs, dict):
            return {
                "status": "ok",
                "source": "synapse_bus._gate_state",
                "reflection_probe": "reflection._gate_state not accessible",
                "traces": len(gs),
                "limits": {
                    "max_depth": getattr(synapse_bus, "_MAX_DEPTH", None),
                    "event_ttl_s": getattr(synapse_bus, "_EVENT_TTL_S", None),
                    "max_turns": getattr(synapse_bus, "_MAX_TURNS", None),
                    "max_reply_rounds": getattr(synapse_bus, "_MAX_REPLY_ROUNDS", None),
                },
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("[self_check] 探 synapse_bus._gate_state 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
    return {"status": "no_data", "reason": "reflection._gate_state not accessible"}


#: v1.5 预算闸探测名：2026-10-09 侦察 reflection.py L63-72 实测所得。
#: 注意 per-trace 表实名 _reflect_last_trace——v45 快照所记
#: _reflect_last_by_trace 在代码里不存在，勿再按那个名字找。
_BUDGET_GATE_NAMES = (
    "_reflect_last_global",   # L69 全局冷却起点（time.monotonic 基准）
    "_reflect_last_trace",    # L70 trace→最近放行时刻 dict（per-trace 冷却表）
    "_reflect_daily_count",   # L71 当日计数
    "_reflect_daily_date",    # L72 计数归属日期（跨天首调清零）
    "_REFLECT_DAILY_CAP",     # L67 日上限常量
)


def _collect_budget_gate() -> dict:
    """⑤ 算力预算闸（reflection 2026-10-07 v4）：dir()+getattr 只读探测，
    全程不写 reflection 任何变量、不抛异常。缺哪个变量，no_data reason 如实列哪个。"""
    try:
        from . import reflection
    except Exception as e:  # noqa: BLE001 - fail-open，不阻塞循环
        logger.warning("[self_check] import reflection（budget_gate）异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "import reflection failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    _absent = object()
    vals: dict = {}
    missing: list = []
    for name in _BUDGET_GATE_NAMES:
        v = getattr(reflection, name, _absent)
        if v is _absent:
            missing.append(name)
        else:
            vals[name] = v
    if missing:
        return {"status": "no_data",
                "reason": "reflection missing: %s" % ", ".join(missing)}
    last_trace = vals["_reflect_last_trace"]
    return {
        "status": "ok",
        "global_cooldown_last": vals["_reflect_last_global"],
        # 如实标时钟：reflection 记账用 time.monotonic()，此值是 monotonic 读数、
        # 非 epoch——直接当时间戳解读会错，进程重启即清零。
        "global_cooldown_last_clock": "time.monotonic",
        "daily_limit": vals["_REFLECT_DAILY_CAP"],
        "daily_used": vals["_reflect_daily_count"],
        "daily_date": vals["_reflect_daily_date"],
        "per_trace_count": len(last_trace) if isinstance(last_trace, dict) else None,
        "probed_names": list(_BUDGET_GATE_NAMES),
    }


def _collect_backlog() -> dict:
    """③ 事件积压：synapse_bus.snapshot_chains() 数未闭合链（判据见模块 docstring）。"""
    try:
        from . import synapse_bus
        chains = synapse_bus.snapshot_chains()
    except Exception as e:  # noqa: BLE001
        logger.warning("[self_check] 读 snapshot_chains 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "synapse_bus.snapshot_chains not accessible: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    return {"status": "ok", "open_chains": len(chains),
            "chains": chains[:20], "truncated": len(chains) > 20}


async def self_check_once() -> dict:
    """跑一轮，返回报告 dict（顺带更新 _LAST_REPORT 并落 self_check.log）。

    level 口径（事实性标记，无建议）：任一端口 down / 窗口内 ERROR>0 /
    gate、backlog 或 budget_gate 读不到 → "warn"（镜子照不到那里本身就是要标记的事）；
    全绿 → "info"。"""
    global _LAST_REPORT
    ups = await asyncio.gather(*(_probe_port(p) for p in _PROBE_PORTS))
    ports = {str(p): {"port": p, "up": up} for p, up in zip(_PROBE_PORTS, ups)}
    gate = _collect_gate()
    budget_gate = _collect_budget_gate()
    backlog = _collect_backlog()
    err_count = _collect_recent_errors()
    err_items = _collect_recent_error_items()
    anomalous = (any(not v["up"] for v in ports.values())
                 or err_count > 0
                 or gate.get("status") != "ok"
                 or backlog.get("status") != "ok"
                 or budget_gate.get("status") != "ok")
    report = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "ports": ports,
        "gate": gate,
        "budget_gate": budget_gate,
        "backlog": backlog,
        "errors": {"recent_30min": err_count, "recent_items": err_items},
        "level": "warn" if anomalous else "info",
    }
    _LAST_REPORT = report
    write_self_check_log(report)
    logger.warning("[self_check] level=%s ports_down=%d errors_30min=%d open_chains=%s",
                   report["level"],
                   sum(1 for v in ports.values() if not v["up"]),
                   err_count, backlog.get("open_chains", "no_data"))
    return report


async def self_check_loop() -> None:
    """定时任务体——任务书节奏 `while True: sleep; once()`（先睡后跑）：
    起手已在循环外跑过一轮，循环内若也先跑，启动即连写两行、每轮间隔实际为 0
    （2026-10-09 首测实测复现，已改回）。sleep 恒在 try 之外：CancelledError
    不被 trapping，teardown 绝不因"睡满整周期"挂起（main.py:84-86 同款纪律）；
    tick 内未捕获异常 → warning 后继续，循环不死。"""
    from . import self_reflection  # [桥二B2] 惰性导入（_collect_* 同款防环惯例）
    try:
        await self_check_once()  # 起手先跑一轮：启动后端点立刻有东西
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("[self_check] 首跑失败", exc_info=True)
    self_reflection.self_reflection_maybe()  # [桥二B2] 自省第二步：写 self_observation（函数内自守卫；不 trap 进上块防 CancelledError 口径）
    try:
        from . import self_model  # [桥三v0] 惰性导入（_collect_* 同款防环惯例）
        self_model.self_model_maybe()  # 自我仪表盘：写 kind='self'（函数内自守卫三重防重复；不 trap 进上块防 CancelledError 口径）
    except Exception:  # noqa: BLE001 - self_model 失败不拖循环
        logger.warning("[self_check] self_model_maybe 异常", exc_info=True)
    try:
        from . import self_narrative  # [桥三 stage2] 惰性导入（self_model 同款防环惯例）
        await self_narrative.self_narrative_maybe()  # 自我叙事：ready+冷却过 → LLM 熔炼写 kind='self_narrative'（函数内自守卫；不 trap 进上块防 CancelledError 口径）
    except Exception:  # noqa: BLE001 - self_narrative 失败不拖循环
        logger.warning("[self_check] self_narrative_maybe 异常", exc_info=True)
    try:
        from . import memory_hippocampus  # [海马体 v1] 惰性导入（self_narrative 同款防环惯例）
        await memory_hippocampus.memory_hippocampus_maybe()  # 睡眠压缩：旧 self_observation 浓缩为 memory_digest 并物理删源（函数内自守卫；不 trap 进上块防 CancelledError 口径）
    except Exception:  # noqa: BLE001 - 海马体失败不拖循环
        logger.warning("[self_check] memory_hippocampus_maybe 异常", exc_info=True)
    while True:
        await asyncio.sleep(_SELF_CHECK_INTERVAL)
        try:
            await self_check_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 循环绝不因单轮失败退出
            logger.warning("[self_check] 轮次失败", exc_info=True)
        self_reflection.self_reflection_maybe()  # [桥二B2] 每轮尾同款
        try:
            from . import self_model  # [桥三v0.1] 第二挂点：每轮尾同款（起手块 L346-350 镜像；24h 冷却自然拦本轮）
            self_model.self_model_maybe()  # 自我仪表盘：写 kind='self'（函数内自守卫三重防重复；不 trap 进上块防 CancelledError 口径）
        except Exception:  # noqa: BLE001 - self_model 失败不拖循环
            logger.warning("[self_check] self_model_maybe 异常", exc_info=True)
        try:
            from . import self_narrative  # [桥三 stage2] 第二挂点：每轮尾同款（起手块镜像；7 天冷却自然拦本轮）
            await self_narrative.self_narrative_maybe()  # 自我叙事：ready+冷却过 → LLM 熔炼写 kind='self_narrative'（函数内自守卫；不 trap 进上块防 CancelledError 口径）
        except Exception:  # noqa: BLE001 - self_narrative 失败不拖循环
            logger.warning("[self_check] self_narrative_maybe 异常", exc_info=True)
        try:
            from . import memory_hippocampus  # [海马体 v1] 第二挂点：每轮尾同款（起手块镜像；1h 冷却+素材闸自然拦本轮）
            await memory_hippocampus.memory_hippocampus_maybe()  # 睡眠压缩：旧 self_observation 浓缩为 memory_digest 并物理删源（函数内自守卫；不 trap 进上块防 CancelledError 口径）
        except Exception:  # noqa: BLE001 - 海马体失败不拖循环
            logger.warning("[self_check] memory_hippocampus_maybe 异常", exc_info=True)


def write_self_check_log(report: dict) -> None:
    """追加一行 JSON 到 logs/self_check.log（UTF-8, ensure_ascii=False）。
    fail-open：写失败只 warning，不影响 _LAST_REPORT 与下一轮；不 mkdir——
    logs/ 目录侦察已确认存在，若哪天不在，写失败本身就是该被看见的事实。"""
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("[self_check] 落盘失败：%s: %s", type(e).__name__, str(e)[:200])


router = APIRouter()


@router.get("/self_check/report")
async def self_check_report():
    """只读端点：返回最近一次报告。首跑未完成前 503（如实 not_ready，不伪造数据）。"""
    if _LAST_REPORT is None:
        return JSONResponse(status_code=503, content={
            "ok": False, "code": "not_ready",
            "message": "self_check 首轮尚未完成（起手跑在 lifespan 内，通常 <2s）"})
    return {"ok": True, **_LAST_REPORT}
