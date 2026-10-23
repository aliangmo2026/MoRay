# -*- coding: utf-8 -*-
"""Synapse 进程内总线（2026-10-06 v1）：事件落库 + 队列消费 + 规则分发。

设计裁定（侦察 2026-10-06 定稿，先例全部实读核对）：
- A类 send()：一行调用——总闸检查 → 同步落库单行 → 叫醒 consumer，绝不做重活
  （重活在 consumer 侧：反思 LLM 调用跑 1-2 分钟，在 handler 里）。
- 哲学抄 Argus event_hub（D:\\Argus\\argus\\core\\event_hub.py 头注）：
  数据库是唯一事实来源，队列只负责叫醒——叫醒丢失/队列满只退化消费延迟，不丢事实。
- 跨线程叫醒抄 api.py:374-378：send 可能从 ZCode reader 线程（非事件循环）调用，
  必须 loop.call_soon_threadsafe；loop 未起/已关 → 只落库不叫醒（静默，绝不炸调用方）。
- consumer fire-and-forget：逐规则 spawn 独立 task（main.py _scheduler_tick 逐个
  触发的同款风格），绝不串行 await——否则一条反思阻塞后续事件 120s。
- handler 自查业务闸：reflection.run 直调绕不过端点闸（reflection.py:241 的
  REFLECTION_ENABLED 检查只在端点里），handler 里自查，侦察已预警。
- 全部异常吞掉只记 warning（organ_memory 同款 fail-open，但失败可见——
  本仓自定义 logger 的 INFO 不上控制台，main.py:45-46，故一律 warning）。
- v3 防环五闸（2026-10-07）：循环风险 = handler 自己 send 新事件再被消费（v4 代理接入后
  成为现实路径）。五闸全内存跟踪、只拦"处理"不拦"落库"（事实永远在库里，Argus 同款）：
  ① depth：meta.depth 缺省 0（原始事件）；处理 depth=d 的事件即产生第 d+1 代，超过 3 层
    不处理（即 depth>=3 拦，第 4 代起进不来——任务书"> 3 不处理"按"处理后层数"计）；
  ② trace_id 幂等（v1 send 判重）扩展 MAX_REPLY_ROUNDS=5：同 trace 链内第 6 条起不处理；
  ③ TTL=30s：created_at 距今超 30s 不处理（陈年积压不回放；created_at 解析失败 fail-open
    放行——循环自喂事件恒为 send 新落库、时间戳必然合法，坏值只可能来自人工改库）；
  ④ MAX_TURNS=3：同 trace 的 handler 调用第 4 次起拦（被拦不计数，幂等拦）；
  ⑤ 闸触发落系统消息 synapse.throttled（from_member=system）：只落库不叫醒——
    synapse.throttled 无规则可跑，叫醒只会让"闸触发闸"递归；同链同闸只报一次（防刷屏）。
- 链跟踪 _gate_state[trace_id]（chain/turns/last/notified）全内存，静默 5 分钟懒清扫
  （真循环秒级自喂、last 持续刷新，绝不静默 5 分钟；清扫后旧 trace 再来陈年事件有 TTL
  闸兜底、新事件从零重计——无环风险）。不落库、零 schema 改动（任务书裁定）。
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime

from . import config, synapse_db

logger = logging.getLogger("moray.synapse")

#: 队列上限（mcp_server._QUEUE_MAX = 256 同款量级）；满了丢叫醒不丢事实（库里还在）
_QUEUE_MAX = 256

# ---------------- v3 防环五闸常量（任务书定值，不进 env——改值即改代码，与 _QUEUE_MAX 同级） ----------------
_MAX_DEPTH = 3          #: 闸① 派生深度：处理 depth=d 的事件产生第 d+1 代，>3 层不处理（depth>=3 拦）
_EVENT_TTL_S = 30.0     #: 闸③ 事件 TTL：created_at 距今超 30s 不处理（陈年积压不回放）
_MAX_TURNS = 3          #: 闸④ 同 trace handler 调用上限：第 4 次起拦
_MAX_REPLY_ROUNDS = 5   #: 闸②扩展 同 trace 链内事件数上限：第 6 条起拦（闸②本身 = v1 send 幂等判重）
_GATE_SWEEP_S = 300.0   #: 链静默 5 分钟即清内存跟踪（真循环秒级自喂，绝不静默 5 分钟）

# ---------------- 模块级总线状态（lifespan 起停管理） ----------------
_loop: asyncio.AbstractEventLoop | None = None
_queue: asyncio.Queue | None = None
_consumer_task: asyncio.Task | None = None
#: fire-and-forget 的 handler task 登记（防 GC 回收 + stop 时统一收尸不悬挂 teardown）
_pending: set = set()

#: v3 链跟踪（全内存，任务书裁定不落库）：trace_id → {"chain": 链内已消费事件数,
#: "turns": handler 已调用数, "last": 最近活动 epoch 秒（sweep 用）,
#: "notified": 已落过系统消息的闸名集合（同链同闸只报一次，防闸风暴刷屏）}
_gate_state: dict = {}
#: [v5 第四条 2026-10-08] 链状态起始时刻（epoch 秒，start_loop 记）——内存态只从
#: 本次服务启动起有效，重启清零；只读端点据此如实导出 service_started_at。
_state_started_at: float | None = None

# ---------------- 规则来源（v3-lite 落库：synapse_rules 表，改库即时生效） ----------------
# v1 的硬编码 _RULES 已落库（synapse_db.SCHEMA 的 synapse_rules，init 时 seed 两条），
# 规则增删/禁用直接改库即生效——消费端每事件查表（不启动缓存），与"库是唯一事实
# 来源"哲学一致；handler 名 → 函数映射仍走内存 _HANDLERS（名字可审，函数不序列化）。

#: handler 名 → 协程函数（规则表里存名字不存引用，硬编码字典同样可读可审）
_HANDLERS: dict = {}


def _handler_reflection_run(event: dict) -> None:
    """handler: reflection.run —— 第一链"ZCode 成功 → 反思"。

    自查业务闸（端点闸拦不住直调，见模块头注）；fire-and-forget 由 consumer 保证，
    本函数只管把 run_reflection 跑完并如实记日志（reflection 内部 fail-open，
    LLM 失败也不抛——返回 dict 带 error 字段）。"""
    if not config.REFLECTION_ENABLED:
        logger.warning("[synapse] handler=reflection.run 跳过：MORAY_REFLECTION=0（业务闸关）")
        return

    from .reflection import run_reflection  # 函数内懒导入：避免 reflection 链的环

    async def _run() -> None:
        # trace_id 供算力预算闸做同 trace 冷却（v4 2026-10-07）；.get 缺失返回
        # None → 退化为仅全局+日限，不报错（event 恒带 trace_id 列，见 _load_event）
        result = await run_reflection(trace_id=event.get("trace_id"))
        if result.get("error"):
            logger.warning("[synapse] reflection.run 完成带错误：%s（created=%s）",
                           result["error"], result.get("insights_created", 0))
        else:
            logger.warning("[synapse] reflection.run 完成：processed=%s insights=%s",
                           result.get("processed", 0), result.get("insights_created", 0))

    return _run()


_HANDLERS["reflection.run"] = _handler_reflection_run


# ---------------- [自我模型 v4 2026-10-23] 实时状态机 handler ----------------
def _handler_self_state_bump(event: dict, success: bool):
    """handler 内核：任务成败事件 → self_state.bump_state（追加一行状态流水）。

    engine 从事件类型取前缀（zcode.task.failed → zcode）；note 留事件正文痕。
    bump_state 自身 fail-open（吞异常返 None），这里不再加业务闸——
    状态流水是"事实记录"，总线开着就记。"""
    from . import self_state  # 函数内懒导入：与 reflection.run 同款防环

    async def _run() -> None:
        engine = str(event.get("proxy_source") or "").split(".", 1)[0] or "unknown"
        note = str(event.get("content") or "")
        new_state = self_state.bump_state(engine, success, note)
        if new_state is None:
            logger.warning("[synapse] self_state bump 失败留痕：event=%s trace=%s",
                           event.get("proxy_source"), event.get("trace_id"))

    return _run()


def _handler_self_state_bump_fail(event: dict):
    return _handler_self_state_bump(event, False)


def _handler_self_state_bump_success(event: dict):
    return _handler_self_state_bump(event, True)


_HANDLERS["self_state.bump_fail"] = _handler_self_state_bump_fail
_HANDLERS["self_state.bump_success"] = _handler_self_state_bump_success


# ---------------- [自驱引擎 v2 2026-10-23] 执行闭环 handler ----------------
#: 闭环规则（handler 注册内存 _HANDLERS，规则行落 synapse_rules）：引擎任务终态
#: 事件 → 按 meta.task_id 匹配自驱目标卡 engine_task_id → 置 done/failed 并写一条
#: self_observation。规则行在 start_loop 幂等补种（任务书指定本模块承载，不动
#: synapse_db.SEED_RULES；INSERT OR IGNORE 与 init_synapse_db 的 seed 同语义——
#: 手工 enabled=0 的行重启仍保持禁用）。
_SELF_DRIVEN_RULES = (
    ("zcode.task.succeeded", "self_driven.close_loop"),
    ("zcode.task.failed", "self_driven.close_loop"),
    ("laya.task.completed", "self_driven.close_loop"),
)


def _handler_self_driven_close_loop(event: dict):
    """handler 内核：引擎终态事件 → 自驱目标卡闭环（v2）。

    成败判定：zcode.task.succeeded=成 / zcode.task.failed=败 /
    laya.task.completed 看 meta.status=="success"（cancelled 归败）。
    匹配键 = meta.task_id ↔ 目标卡 engine_task_id；无匹配卡（普通聊天任务/
    编排工具子任务）静默跳过。卡状态由 self_driven.close_dispatched_goal 流转
    （幂等，只动 dispatched 卡）——命中新终态才写 self_observation，重复事件
    不二写。记忆回写失败不影响卡状态已落盘的闭环事实。"""
    async def _run() -> None:
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        tid = str(meta.get("task_id") or "").strip()
        if not tid:
            return
        src = str(event.get("proxy_source") or "")
        if src == "zcode.task.succeeded":
            success = True
        elif src == "zcode.task.failed":
            success = False
        else:  # laya.task.completed
            success = str(meta.get("status") or "") == "success"
        from . import self_driven  # 函数内懒导入（reflection.run 同款防环）
        card = self_driven.close_dispatched_goal(
            tid, success, note=str(event.get("content") or ""))
        if not card:
            return
        content = "我执行了自驱目标：%s，结果是：%s" % (
            str(card.get("goal") or "")[:200], "成功" if success else "失败")
        try:
            from .memory_routes import insert_memory  # 函数内懒导入：记忆链防环
            mem_id, err = insert_memory(content, kind="self_observation",
                                        tags=["self_driven"], source="self_driven",
                                        entity_id=tid)
            if err:
                logger.warning("[synapse] self_observation 回写失败：%s（engine_task_id=%s）",
                               err[:120], tid)
            else:
                logger.warning("[synapse] 自驱闭环完成：engine_task_id=%s status=%s mem_id=%s",
                               tid, card.get("status"), mem_id)
        except Exception as e:  # noqa: BLE001 - 记忆回写失败不影响卡状态已落盘
            logger.warning("[synapse] self_observation 回写异常：%s: %s",
                           type(e).__name__, str(e)[:200])

    return _run()


_HANDLERS["self_driven.close_loop"] = _handler_self_driven_close_loop


def _ensure_self_driven_rules() -> None:
    """[自驱 v2] 闭环规则幂等补种（synapse_db.SEED_RULES 同款 INSERT OR IGNORE，
    ? 参数绑定）。失败只记 warning 不影响总线启动——规则缺位退化为闭环不回写，
    绝不反噬 consumer。"""
    try:
        conn = synapse_db.connect()
        try:
            for event_type, handler in _SELF_DRIVEN_RULES:
                conn.execute(
                    "INSERT OR IGNORE INTO synapse_rules "
                    "(event_type, handler, enabled, params_json) VALUES (?, ?, 1, NULL)",
                    (event_type, handler))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[synapse] 自驱闭环规则落库失败：%s: %s",
                       type(e).__name__, str(e)[:200])


# ---------------- A类 send() ----------------
def send(event_type, content, mentions=[], thread_id=None, meta={}, from_member="system") -> str:
    """A类一行 send：总闸关 = 完全 no-op；开着 = 同步落库单行 + 叫醒 consumer。

    - event_type：事件类型，落 proxy_source 列（synapse_bus 规则分发匹配键）；
    - meta：事件负载 dict（JSON 落库）。meta["trace_id"] 可显式指定幂等键
      （如 ZCode 线传 task_id），缺省自动生成 uuid——同 trace_id 重发只落一次库，
      幂等返回已有 id（五闸之一，v1 基本判重：只拦同键重发，不拦内容相似）；
    - 返回消息 id（关闸/空内容/异常返回 ""）。调用方不得据返回值分支业务
      （organ_memory.write_organ_memory 同款 fail-open 约定）。
    """
    try:
        if not config.SYNAPSE_ENABLED:
            return ""
        text = str(content or "")
        if not text.strip():
            return ""
        payload = dict(meta or {})
        trace_id = str(payload.pop("trace_id", "") or "").strip() or uuid.uuid4().hex
        payload["event_type"] = str(event_type or "")
        conn = synapse_db.connect()
        try:
            row = conn.execute(
                "SELECT id FROM synapse_messages WHERE trace_id = ? LIMIT 1", (trace_id,)
            ).fetchone()
            if row is not None:
                return row["id"]
            msg_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO synapse_messages "
                "(id, thread_id, from_member, content, mentions, trace_id, proxy_source, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (msg_id, str(thread_id or ""), str(from_member or "system"), text,
                 ",".join(str(m) for m in (mentions or []) if m),
                 trace_id, str(event_type or ""),
                 json.dumps(payload, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        _wake(msg_id)
        logger.warning("[synapse] send: event=%s trace=%s id=%s", event_type, trace_id, msg_id)
        return msg_id
    except Exception as e:  # noqa: BLE001 - 事件发送失败绝不影响调用方（器官收尾/流收尾）
        logger.warning("[synapse] send 异常：event=%s err=%s: %s",
                       event_type, type(e).__name__, str(e)[:200])
        return ""


def _wake(event_id: str) -> None:
    """叫醒 consumer（线程安全桥，api.py:374-378 同款）。

    loop 未起（总闸关/未到 lifespan）→ 静默返回；loop 已关 → RuntimeError 静默；
    队列满 → 丢叫醒（事实已落库，Argus event_hub 同款取舍）。
    局部捕获 loop/queue：stop() 置空全局后，已在途的叫醒仍落在还活着的旧对象上，
    不在 loop 线程里炸 AttributeError。"""
    loop, queue = _loop, _queue
    if loop is None or queue is None:
        return

    def _put() -> None:
        try:
            queue.put_nowait(event_id)
        except asyncio.QueueFull:
            pass

    try:
        loop.call_soon_threadsafe(_put)
    except RuntimeError:  # noqa: BLE001 - 事件循环已关闭（停机）：丢弃即可
        pass


# ---------------- consumer ----------------
def _load_rules(event_type: str) -> list:
    """按事件类型查规则表（v3-lite 热更新核心：每事件查库，规则变更免重启）。

    只取 enabled=1 的行，返回 [{handler, params}, ...]（enabled 已在 SQL 过滤，
    调用方不再判）；params_json 坏 JSON 归 None（handler 现不消费参数，字段前瞻）。
    查询失败返回 [] 并记 warning：规则读失败只退化为"本条事件不触发 handler"，
    绝不反噬 consumer 循环（_load_event 同款 fail-open）。"""
    try:
        conn = synapse_db.connect()
        try:
            rows = conn.execute(
                "SELECT handler, params_json FROM synapse_rules "
                "WHERE event_type = ? AND enabled = 1",
                (str(event_type or ""),)
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 规则读取失败不影响消费循环
        logger.warning("[synapse] 规则读取失败：event=%s err=%s: %s",
                       event_type, type(e).__name__, str(e)[:200])
        return []
    rules = []
    for r in rows:
        try:
            params = json.loads(r["params_json"]) if r["params_json"] else None
        except ValueError:
            params = None
        rules.append({"handler": str(r["handler"] or ""), "params": params})
    return rules


def _count_enabled_rules() -> int:
    """启动日志用：enabled=1 规则总数；读失败返回 -1（日志如实显示，不假装 0）。"""
    try:
        conn = synapse_db.connect()
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM synapse_rules WHERE enabled = 1").fetchone()[0])
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[synapse] 规则计数失败：%s: %s", type(e).__name__, str(e)[:200])
        return -1


def _gate_sweep(now: float) -> None:
    """链跟踪懒清扫：静默超 _GATE_SWEEP_S 的 trace 整条清掉（防 _gate_state 无界增长）。

    安全性：真循环的 handler→send→消费周期是秒级，last 持续刷新不会被误清；
    清扫后旧 trace 若再来陈年事件有 TTL 闸兜底，新事件则从零重计——两路都无环风险。"""
    if len(_gate_state) < 64:
        return
    for tid in [t for t, s in _gate_state.items() if now - s.get("last", 0.0) > _GATE_SWEEP_S]:
        _gate_state.pop(tid, None)


# ---------------- [v5 第四条 2026-10-08] 链状态只读导出（v5-7 链视图数据源） ----------------
# 纯只读快照：不改 _gate_state、不 pop、不清扫——清扫语义归 _gate_sweep/五闸，本区零介入。
# 线程安全：_gate_state 只在事件循环线程读写（consumer 协程 + 本模块全部写入点），
# routes 的 async 端点同在循环线程内做同步拷贝，无锁需求。ISO 口径与
# synapse_routes._iso() 一致（ISO 8601 带本地偏移、秒精度）；bus 不能反向 import
# routes（routes→bus 单向依赖，倒过来即环），故本地实现。


def _epoch_iso(epoch) -> str | None:
    """epoch 秒（float）→ ISO 8601 带本地偏移；无效/未活动（<=0）归 None。"""
    try:
        if epoch is None or float(epoch) <= 0:
            return None
        return datetime.fromtimestamp(float(epoch)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def state_started_at() -> str | None:
    """链状态起始时刻的 ISO 8601（本地偏移）；bus 未启动时 None。"""
    return _epoch_iso(_state_started_at)


def snapshot_chains() -> list[dict]:
    """遍历 _gate_state 导出链状态快照（同步拷贝，顺序不定，排序归调用方）。

    notified 为 set，导出时 sorted 转 list（输出确定）；last<=0（_notify_throttled
    setdefault 建过但未消费的条目）导出 None——"从未有事件消费"如实表达，
    绝不导出 1970 假时刻。"""
    out = []
    for tid, s in _gate_state.items():
        out.append({
            "trace_id": str(tid),
            "chain": int(s.get("chain", 0)),
            "turns": int(s.get("turns", 0)),
            "last": _epoch_iso(s.get("last")),
            "notified": sorted(s.get("notified") or []),
        })
    return out


def _notify_throttled(trace_id: str, gate: str, reason: str, detail: dict) -> None:
    """闸触发 → 落一条系统消息（from_member=system / proxy_source=synapse.throttled）。

    只落库**不叫醒**：synapse.throttled 无规则可跑，叫醒只会让"闸触发闸"递归成环
    （头注⑤）；群聊页轮询读库，照样可见。同链同闸只报一次（_gate_state.notified，
    防同类事件连番触发把群聊刷成告警墙）。fail-open：落库失败只记 warning，
    绝不影响 consumer 循环，也绝不掩盖"闸已拦下事件"这个事实（warning 日志恒在）。"""
    state = _gate_state.setdefault(trace_id, {"chain": 0, "turns": 0, "last": 0.0, "notified": set()})
    if gate in state["notified"]:
        return
    state["notified"].add(gate)
    payload = {"event_type": "synapse.throttled", "gate": gate, "reason": reason,
               "blocked_trace_id": trace_id}
    payload.update(detail)
    try:
        conn = synapse_db.connect()
        try:
            conn.execute(
                "INSERT INTO synapse_messages "
                "(id, thread_id, from_member, content, mentions, trace_id, proxy_source, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, "", "system",
                 "[系统] 因 %s 已停止（trace_id=%s）" % (reason, trace_id),
                 "", trace_id, "synapse.throttled",
                 json.dumps(payload, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        logger.warning("[synapse] 闸触发：gate=%s trace=%s（已落系统消息）", gate, trace_id)
    except Exception as e:  # noqa: BLE001 - 系统消息落库失败不影响拦截本身（warning 已记录）
        logger.warning("[synapse] 系统消息落库失败：gate=%s trace=%s err=%s: %s",
                       gate, trace_id, type(e).__name__, str(e)[:200])


def _load_event(event_id: str) -> dict | None:
    """按 id 读事件行（库是事实来源，队列里只传 id）；行没了返回 None。"""
    conn = synapse_db.connect()
    try:
        row = conn.execute(
            "SELECT id, thread_id, from_member, content, mentions, trace_id, "
            "proxy_source, meta, created_at FROM synapse_messages WHERE id = ?",
            (event_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    event = dict(row)
    try:
        event["meta"] = json.loads(event.get("meta") or "{}")
    except ValueError:
        event["meta"] = {}
    return event


async def _safe_handler(name: str, coro) -> None:
    """fire-and-forget 的安全壳：handler 异常只记日志，绝不反噬 consumer 循环。"""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - 单个 handler 失败不影响总线与其余规则
        logger.warning("[synapse] handler=%s 异常：%s: %s", name, type(e).__name__, str(e)[:200])


async def _consumer_loop() -> None:
    """常驻消费：get 事件 id → 读行 → 五闸 → 查规则表 → 逐规则 spawn handler。

    spawn 即走（fire-and-forget），consumer 立刻回去等下一条——反思跑多久都与队列无关。
    规则来自 synapse_rules 表（每事件查库，热更新）：enabled 由 SQL 过滤，悬空
    handler（规则在、_HANDLERS 无此名）警告后跳过，不影响其余规则。
    五闸（v3，全内存，只拦"处理"不拦"落库"，触发落 synapse.throttled 系统消息）：
    链计数/深度/TTL 三道在事件级（进规则循环之前），MAX_TURNS 在 handler 级
    （每次 spawn 前查）——一条事件命中多条规则时逐规则各计一次。"""
    while True:
        event_id = await _queue.get()
        try:
            event = _load_event(event_id)
        except Exception:  # noqa: BLE001 - 读行失败不影响循环（事实还在库里可人工查）
            logger.exception("[synapse] 事件读取失败：id=%s", event_id)
            continue
        if event is None:
            logger.warning("[synapse] 事件行不存在（库被清过？）：id=%s", event_id)
            continue

        # ---- v3 防环五闸（顺序：链计数 → 链上限 → depth → TTL；turn 闸在规则循环内）----
        now = time.time()
        trace_id = str(event.get("trace_id") or "")
        if trace_id:
            state = _gate_state.setdefault(
                trace_id, {"chain": 0, "turns": 0, "last": 0.0, "notified": set()})
            state["chain"] += 1
            state["last"] = now
            _gate_sweep(now)
            # 闸②扩展 MAX_REPLY_ROUNDS：链内事件数 >5 停（第 6 条起拦）
            if state["chain"] > _MAX_REPLY_ROUNDS:
                _notify_throttled(trace_id, "max_reply_rounds",
                                  "链内事件数超限（MAX_REPLY_ROUNDS=%d）" % _MAX_REPLY_ROUNDS,
                                  {"chain": state["chain"], "event_type": event.get("proxy_source")})
                continue
        # 闸① depth：处理 depth=d 的事件即产生第 d+1 代，>3 层不处理（depth>=3 拦；
        # meta 缺省 0 = 原始事件，正常链永不触闸）
        try:
            depth = int(event["meta"].get("depth") or 0)
        except (TypeError, ValueError):
            depth = 0
            logger.warning("[synapse] meta.depth 非整数按 0 处理：trace=%s depth=%r",
                           trace_id, event["meta"].get("depth"))
        if depth >= _MAX_DEPTH:
            _notify_throttled(trace_id, "depth",
                              "派生深度超限（处理将产生第 %d 层，上限 3 层）" % (depth + 1),
                              {"depth": depth, "event_type": event.get("proxy_source")})
            continue
        # 闸③ TTL：created_at 距今 >30s 不处理（陈年积压不回放）；解析失败 fail-open 放行
        # （头注③：循环自喂事件恒为新落库、时间戳必然合法，坏值只可能来自人工改库）
        try:
            age = now - datetime.strptime(str(event.get("created_at") or ""),
                                          "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            logger.warning("[synapse] created_at 无法解析按新事件放行：trace=%s created_at=%r",
                           trace_id, event.get("created_at"))
            age = 0.0
        if age > _EVENT_TTL_S:
            _notify_throttled(trace_id, "ttl",
                              "事件过期（TTL %ds，距今 %.0fs）" % (int(_EVENT_TTL_S), age),
                              {"age_s": round(age, 1), "event_type": event.get("proxy_source")})
            continue

        for rule in _load_rules(event["proxy_source"]):
            handler = _HANDLERS.get(str(rule.get("handler") or ""))
            if handler is None:
                logger.warning("[synapse] 未知 handler：%r（规则悬空）", rule.get("handler"))
                continue
            # 闸④ MAX_TURNS：同 trace handler 调用第 4 次起拦（被拦不计数，幂等拦——
            # 同一事件命中多条规则时逐规则各占一个调用名额）
            if trace_id:
                state = _gate_state.setdefault(
                    trace_id, {"chain": 0, "turns": 0, "last": 0.0, "notified": set()})
                if state["turns"] >= _MAX_TURNS:
                    _notify_throttled(trace_id, "max_turns",
                                      "handler 调用次数超限（MAX_TURNS=%d）" % _MAX_TURNS,
                                      {"turns": state["turns"], "handler": rule.get("handler"),
                                       "event_type": event.get("proxy_source")})
                    continue
                state["turns"] += 1
            coro = handler(event)
            if coro is None:
                continue
            task = asyncio.ensure_future(_safe_handler(str(rule.get("handler")), coro))
            _pending.add(task)
            task.add_done_callback(_pending.discard)


# ---------------- lifespan 起停 ----------------
def start_loop() -> asyncio.Task:
    """lifespan 启动：捕获当前事件循环，建队列 + 起 consumer task（返回供 lifespan 收尸）。"""
    global _loop, _queue, _consumer_task, _state_started_at
    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _consumer_task = asyncio.ensure_future(_consumer_loop())
    _state_started_at = time.time()  # [v5 第四条] 内存链状态从此刻起有效（重启即重记）
    _ensure_self_driven_rules()  # [自驱 v2] 闭环规则幂等补种（先于日志，rules 计数才准）
    logger.warning("[synapse] bus started: queue_max=%d rules=%d（规则落库改库即生效；"
                   "防环五闸 depth>=3/TTL%ds/turns<=%d/chain<=%d）",
                   _QUEUE_MAX, _count_enabled_rules(),
                   int(_EVENT_TTL_S), _MAX_TURNS, _MAX_REPLY_ROUNDS)
    return _consumer_task


async def stop() -> None:
    """lifespan 收尸：cancel consumer + 未决 handler 并 await（main.py teardown 同款纪律）。

    未启用/未启动时是安全 no-op（MONITOR disable 同款：不养僵尸任务、不让 teardown 挂起）。"""
    global _loop, _queue, _consumer_task
    task = _consumer_task
    _consumer_task = None
    _loop = None
    _queue = None
    for t in [task] + list(_pending):
        if t is not None and not t.done():
            t.cancel()
    for t in [task] + list(_pending):
        if t is None:
            continue
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - 收尸阶段任何异常都不让 teardown 挂起
            pass
    _pending.clear()
