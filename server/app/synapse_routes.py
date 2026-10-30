# -*- coding: utf-8 -*-
"""Synapse 神经总线路由（2026-10-06 v2 起只读；[v5 第六条 2026-10-09] 增潜水员发言 POST，
文件题"只读"自此条起解除——但四个 GET 的行为零改动）

授权：Synapse v2 任务书（后端读路由 + 前端群聊页，只读不插话）。三端点：
  GET /api/synapse/threads?limit=20&offset=0     线程列表（created_at DESC，附 total）
  GET /api/synapse/messages?thread_id=&limit=50&offset=0&since=
                                                 事件消息分页/增量拉取（created_at DESC，附 total；
                                                 [v5 第四条] 增 trace_id= 单链过滤参数）
  GET /api/synapse/overview                      页头统计（事件总数 + 线程数 + 按 proxy_source 分布
                                                 + 成员级聚合 by_member [v5 step3]）
第四端点（[v5 第四条 2026-10-08] 链状态只读暴露，为 v5-7 链视图供数）：
  GET /api/synapse/chains                        内存五闸链跟踪快照 + service_started_at（零落库）
写端点（[v5 第六条 2026-10-09] 潜水员插话，本文件唯一 POST）：
  POST /api/synapse/messages                     用户发言 → synapse_bus.send(event_type=
                                                 'user.message', from_member='user', mentions
                                                 硬置空) → 落库回查整行，返回与 GET messages
                                                 items[0] 同构（前端乐观上屏直接用回行）。
  裁定（第六条第一段侦察定稿·方案 A）：不加认证 / 不加 CSRF——与全后端 31 个 POST 先例
  一致，门禁 = loopback CORS（main.py:227-233 只放行 null/file:///:8000 本机源）。
  user.message 在 synapse_rules 表无行 → consumer 消费即空转，不触发任何器官（红线：
  本条不 seed 规则、不新增 handler）。总闸关 = send() no-op 返回 "" → 显式 _bad
  bus_disabled 503，绝不静默丢话。trace_id 可选幂等键（前端 uuid，网络重试同键被 send
  判重吞掉不双写；缺省 uuid4 自生成——每条发言自成单事件链，不成任务链）。

风格照抄 memory_routes.py 全套：模块自包含（monitor/self/memory 先例），router = APIRouter()
+ _ok/_bad 信封（全后端零 Pydantic 的既有惯例）+ 每请求 _conn() 新开连接（用完即关）。
只读端点：不查总闸 SYNAPSE_ENABLED——闸关时表为空，端点如实返回空列表/零计数（前端据此
渲染空态），与 send() 关闸 no-op 的"事实永远在库里"哲学一致。

created_at 时区裁定（v2）：库内值由 SQLite datetime('now','localtime') 生成，是 naive 的
"YYYY-MM-DD HH:MM:SS" 本地时区字符串——直接透传给前端会有 Safari Date 解析坑（侦察实锤）。
故所有 created_at 在返回前经 _iso() 转 ISO 8601 带本地偏移（本机即 +08:00）。since 入参
接受本端点返回的 ISO 8601 或库内原始格式，经 _norm_since() 归一回库内格式再进 SQL
（库列是 naive 字符串，直接拿 ISO 串做字典序比较会错）。

增量语义裁定：since 匹配用 created_at >= ? 而非 >（任务书原文为 >）——同秒事件在下一次
轮询时会因 > 而永久漏掉（created_at 秒级精度，同秒后到的事件 > 上次最大值不成立）；
>= 配合前端按 id 幂等去重（140 分片 Merge 去重），重取尾部零成本、同秒不丢。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import synapse_db
# [v5 第四条 2026-10-08] 链状态只读端点需要内存快照源（bus 只读导出，本文件不落任何写）
from . import synapse_bus

router = APIRouter()


def _ok(data):
    return {"ok": True, "data": data}


def _bad(code, message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


def _conn():
    return synapse_db.connect()


def _iso(ts) -> str:
    """库内 'YYYY-MM-DD HH:MM:SS'（naive 本地时区）→ ISO 8601 带本地偏移。

    naive datetime 调 astimezone() 即按运行环境本地时区解释并附加偏移（本机 +08:00）。
    解析失败原样返回（不炸端点；正常路径下库内值恒为 SQLite localtime 格式）。"""
    s = str(ts or "")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").astimezone().isoformat(timespec="seconds")
    except ValueError:
        return s


def _norm_since(raw: str) -> str:
    """since 入参归一回库内 naive 格式，供 SQL 字符串比较。

    接受两种形态：本端点返回的 ISO 8601（含时区偏移 → 先转本地再去掉 tzinfo）
    或库内原始 "YYYY-MM-DD HH:MM:SS"。无法解析抛 ValueError（调用方转 _bad——
    静默当空串会让轮询窗口意外拉全量，显式报错好过假装成功）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    dt = datetime.fromisoformat(s)  # 兼容 "T"/空格 分隔与可选时区
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _meta_dict(raw) -> dict:
    """meta 列（JSON 字符串，synapse_bus.send 落库）→ dict；坏 JSON 归空 dict。"""
    try:
        v = json.loads(str(raw or "") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _row_to_item(row) -> dict:
    """[v5 第六条] synapse_messages 单行 → 前端消息结构。

    与 GET /api/synapse/messages 的 items[0] 逐字段同构（那边内联 dict 即口径来源），
    POST 返回走此函数——前端乐观上屏两条路一个形状，不分叉。"""
    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "from_member": row["from_member"],
        "content": row["content"],
        "mentions": [m for m in (row["mentions"] or "").split(",") if m],
        "trace_id": row["trace_id"],
        "proxy_source": row["proxy_source"],
        "meta": _meta_dict(row["meta"]),
        "created_at": _iso(row["created_at"]),
    }


@router.get("/api/synapse/threads")
async def synapse_threads(limit: int = 20, offset: int = 0):
    """线程列表（created_at DESC + total；分页口径与 GET /api/memory/list 同款）"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, room_id, parent_thread_id, created_at "
            "FROM synapse_threads ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM synapse_threads").fetchone()["c"]
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    items = [{
        "id": r["id"],
        "room_id": r["room_id"],
        "parent_thread_id": r["parent_thread_id"],
        "created_at": _iso(r["created_at"])
    } for r in rows]
    return _ok({"items": items, "total": total})


@router.get("/api/synapse/messages")
async def synapse_messages(thread_id: str = "", limit: int = 50, offset: int = 0, since: str = "",
                           trace_id: str | None = None):
    """事件消息分页/增量拉取（created_at DESC + total）。

    - thread_id 非空则按线程过滤（群聊页 v1 拉全量不过滤，端点能力预留）；
    - trace_id 非空则按链过滤（[v5 第四条] 链视图单链事件流用，与 thread_id/since
      AND 组合；等值匹配 trace_id 列，返回结构不变）；
    - since 非空则 created_at >= ?（归一后比较；>= 的裁定见模块头注）；
    - meta 列 JSON 解析为 dict 返回（前端不踩二次 parse），created_at 转 ISO 8601；
    - total 为满足 WHERE 的全量计数（不含 limit/offset），前端页头与增量完整性校验用。
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    thread_id = str(thread_id or "").strip()
    trace_id = str(trace_id or "").strip()

    since_norm = ""
    if str(since or "").strip():
        try:
            since_norm = _norm_since(since)
        except ValueError:
            return _bad("bad_request", "since 无法解析（接受 ISO 8601 或 YYYY-MM-DD HH:MM:SS）", 400)

    sql = ("SELECT id, thread_id, from_member, content, mentions, trace_id, "
           "       proxy_source, meta, created_at "
           "FROM synapse_messages WHERE 1=1 ")
    count_sql = "SELECT COUNT(*) AS c FROM synapse_messages WHERE 1=1 "
    params = []
    if thread_id:
        sql += "AND thread_id = ? "
        count_sql += "AND thread_id = ? "
        params.append(thread_id)
    if trace_id:
        sql += "AND trace_id = ? "
        count_sql += "AND trace_id = ? "
        params.append(trace_id)
    if since_norm:
        sql += "AND created_at >= ? "
        count_sql += "AND created_at >= ? "
        params.append(since_norm)
    sql += "ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params = params + [limit, offset]

    conn = _conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute(count_sql, params[:-2]).fetchone()["c"]
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    items = [{
        "id": r["id"],
        "thread_id": r["thread_id"],
        "from_member": r["from_member"],
        "content": r["content"],
        "mentions": [m for m in (r["mentions"] or "").split(",") if m],
        "trace_id": r["trace_id"],
        "proxy_source": r["proxy_source"],
        "meta": _meta_dict(r["meta"]),
        "created_at": _iso(r["created_at"])
    } for r in rows]
    return _ok({"items": items, "total": total})


@router.get("/api/synapse/overview")
async def synapse_overview():
    """页头统计：事件总数 + 线程数 + 按 proxy_source 分布 + by_member 成员级聚合
    （count DESC；空库返回零值/空数组不报错。[v5 step3] by_member 供器官态势板全库
    口径：一条 (成员, 事件类型) 粒度 SQL 聚出 count 求和 / last_seen / types 分布；
    last_seen 与其余 created_at 同走 _iso() 裁定；system 不剔除——如实聚合，显示
    取舍归前端。不加 from_member 索引——表量级千行内，全表扫与既有三条 SQL 同成本）"""
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM synapse_messages").fetchone()["c"]
        threads = conn.execute("SELECT COUNT(*) AS c FROM synapse_threads").fetchone()["c"]
        rows = conn.execute(
            "SELECT proxy_source, COUNT(*) AS n FROM synapse_messages "
            "GROUP BY proxy_source ORDER BY n DESC, proxy_source ASC"
        ).fetchall()
        member_rows = conn.execute(
            "SELECT from_member, proxy_source, COUNT(*) AS n, MAX(created_at) AS last_seen "
            "FROM synapse_messages GROUP BY from_member, proxy_source"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    by_source = [{"source": r["proxy_source"], "count": r["n"]} for r in rows]
    # by_member 组装：聚 (成员, 类型) 粒度行到成员级——count 求和；last_seen 取组内
    # 字符串最大（库内恒为 'YYYY-MM-DD HH:MM:SS' 定长格式，字典序=时间序）；types 按
    # count 降序、同数按 source 升序（照抄 by_source 排序口径）。成员间 count 降序、
    # 同数按 member 升序——输出确定；态势板另有前端比较器（计数降序→最近时刻降序）。
    grouped = {}
    for r in member_rows:
        g = grouped.setdefault(str(r["from_member"]), {"count": 0, "last_seen": "", "types": []})
        g["count"] += r["n"]
        if str(r["last_seen"]) > g["last_seen"]:
            g["last_seen"] = str(r["last_seen"])
        g["types"].append({"source": str(r["proxy_source"]), "count": r["n"]})
    by_member = []
    for member, g in grouped.items():
        g["types"].sort(key=lambda t: (-t["count"], t["source"]))
        by_member.append({
            "member": member,
            "count": g["count"],
            "last_seen": _iso(g["last_seen"]),
            "types": g["types"],
        })
    by_member.sort(key=lambda m: (-m["count"], m["member"]))
    return _ok({"total": total, "threads": threads, "by_source": by_source,
                "by_member": by_member})


@router.get("/api/synapse/chains")
async def synapse_chains():
    """[v5 第四条 2026-10-08] 链状态只读快照：synapse_bus._gate_state（内存五闸跟踪）
    的导出视图，为 v5-7"时间线/流转路径"供数。纯内存零落库——服务重启即清零，
    service_started_at 如实标出状态有效起点（未启动/总闸关 = null + items 空，
    与"事实永远在库里、内存只管防环"的既有裁定一致，前端据此区分"真没链"与"刚重启"）。

    字段口径（见 synapse_bus 头注 v3 五闸）：chain=链内已消费事件数（闸②扩展
    MAX_REPLY_ROUNDS=5 计数）、turns=handler 已调用数（闸④ MAX_TURNS=3 计数）、
    last=最近消费时刻（ISO 8601 带本地偏移，_iso() 同款；从未消费过的条目为 null）、
    notified=已落系统消息的闸名列表（set→sorted list，输出确定）。
    排序：last 降序（最近活跃在前，前端"最近完成于 N 前"口径）；last=null 恒后，
    同值按 trace_id 升序——输出确定。"""
    items = synapse_bus.snapshot_chains()
    # 双趟稳定排序=复合键：先 trace 升序打底，再 last 降序。ISO 串定长带同偏移，
    # 字典序即时间序；null 转空串在降序中恒垫底（Python sort 稳定，趟序即键优先级）。
    items.sort(key=lambda c: c["trace_id"])
    items.sort(key=lambda c: c["last"] or "", reverse=True)
    return _ok({"service_started_at": synapse_bus.state_started_at(), "items": items})


# ---- [v5 第六条 2026-10-09] 潜水员插话：唯一写端点（第一段侦察裁定 · 方案 A）----
# 用户发言与器官发言同路走 synapse_bus.send，不绕总线直 INSERT——"不触发器官"由
# user.message 在 synapse_rules 无规则保证（消费空转、五闸正常计数），而非绕总线；
# 将来若放开 @器官 叫醒，此路零改造即被规则消费。mentions 硬置空 = 防自弹保险第一道
# （第二道在 140_synapse.js renderUserWake 的 from_member==='user' 跳过）。
# content 上限 2000 字符：超限拒 400 而非截断——截断让"落库事实"与"用户原话"不一致，
# 违背本仓"事实永远在库里"哲学；前端 composer 同步 maxlength=2000，超限在键入侧就挡住。
_MAX_USER_CONTENT = 2000


@router.post("/api/synapse/messages")
async def synapse_user_message(request: Request):
    """潜水员发言：落一条 user.message 上总线，回查整行返回（同 GET items[0] 同构）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON 显式报错（memory_routes 同款惯例）
        return _bad("bad_json", "请求体必须是 JSON", 400)
    if not isinstance(body, dict):
        return _bad("bad_request", "请求体必须是 JSON 对象", 400)

    content = str(body.get("content") or "").strip()
    if not content:
        return _bad("bad_request", "content 必填且非空", 400)
    if len(content) > _MAX_USER_CONTENT:
        return _bad("bad_request", "content 过长（上限 %d 字符）" % _MAX_USER_CONTENT, 400)

    meta: dict = {}
    trace_id = str(body.get("trace_id") or "").strip()
    if trace_id:
        # 只借作 send 幂等判重键（等值匹配），不校验 uuid 形状；超长截断防 absurd 键值
        meta["trace_id"] = trace_id[:64]

    msg_id = synapse_bus.send("user.message", content, mentions=[], thread_id=None,
                              meta=meta, from_member="user")
    if not msg_id:
        # send 返回 ""：总闸关（MORAY_SYNAPSE=0，no-op）或落库异常（send 自吞）。
        # 显式报错让前端把话留在输入框，绝不假装"已发送"。
        return _bad("bus_disabled", "神经总线总闸未开（MORAY_SYNAPSE=0）或落库异常，发言未受理", 503)

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, thread_id, from_member, content, mentions, trace_id, "
            "       proxy_source, meta, created_at "
            "FROM synapse_messages WHERE id = ?", (msg_id,)
        ).fetchone()
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()
    if row is None:
        # send 已返回 id 却读不回行（库被并发清理等病态）：如实报错，不编造返回
        return _bad("db_error", "发言已落库但回查失败（id=%s），请刷新台账核对" % msg_id[:8], 500)
    return _ok(_row_to_item(row))
