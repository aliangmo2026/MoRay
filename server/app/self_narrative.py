# -*- coding: utf-8 -*-
"""自我叙事 self_narrative（2026-10-10 桥三 v1-stage1 骨架；2026-10-21 v1-stage2 叙事版）：
自省第三步——素材（self_observation + insight + self）攒够后，调本地 LLM 熔炼成
一篇第一人称自我叙事，经 insert_memory 落库 kind='self_narrative'（source 同名，
写入矩阵 2026-10-14 已预埋 memory_routes._SOURCE_KIND_MATRIX），供自省面板展示。

级别边界（与桥一 self_check、桥二 self_reflection、桥三 v0 self_model 同口径，勿扩）：
- 对 memory_bank：读素材只 SELECT；写入仅经 insert_memory 追加自己的
  kind='self_narrative' 行，绝不改任何其他 kind 的行；
- fail-open：读不到 / LLM 失败 / 解析失败 / 异常一律转 reason，绝不把异常抛给调用方；
- prompt 中文，只放事实，永不出现"应该/建议/需要"；
- LLM 调用零新增配置面：复用 reflection 同组 config
  （REFLECTION_API / BASE_URL / MODEL / TIMEOUT / NUM_CTX）。

就绪两条件（check_readiness）：
  ① self_observation 条数 >= _SELF_NARRATIVE_MIN_SAMPLES；
  ② 距上次生成 >= _SELF_NARRATIVE_COOLDOWN_SEC；库里无 self_narrative 行
     = 从未生成，条件②视为满足。
"""
import datetime
import logging

import httpx
from fastapi import APIRouter

from . import config
from .memory_routes import insert_memory

logger = logging.getLogger("moray.self_narrative")

#: 冷却（秒）：v1-stage2 两条 self_narrative 生成的最小间隔，7 天硬编不进 config/env。
#: 与 self_model 的进程内 monotonic 冷却不同口径：本冷却读的是库内
#: created_at 挂钟，跨进程重启依然成立（stage2 生成后写入方同款复用）。
_SELF_NARRATIVE_COOLDOWN_SEC = 604800
#: 素材门槛：self_observation 至少够 _SELF_NARRATIVE_MIN_SAMPLES 条才谈得上成叙事
_SELF_NARRATIVE_MIN_SAMPLES = 10

_NARRATIVE_KIND = "self_narrative"  # 产物 kind（stage2 起真实写入；source 同名=矩阵预埋对）
_OBS_KIND = "self_observation"
_INSIGHT_KIND = "insight"
_SELF_KIND = "self"
#: 喂给 stage2 的近期 insight 条数上限
_INSIGHTS_LIMIT = 30
#: 喂给 stage2 的观察条数上限：ASC 序取尾部即最新 _NARRATIVE_OBS_LIMIT 条
_NARRATIVE_OBS_LIMIT = 100
#: user prompt 总字符预算（reflection._MAX_PROMPT_CHARS 同款实证值：8192 ctx 安全）
_NARRATIVE_PROMPT_CHARS = 12000
#: 叙事落库截断——insert_memory 不截 content，必须自设（800 字产出给 5 倍余量）
_NARRATIVE_MAX_CONTENT = 4000
#: 产出最短字数——低于此视为空转/垃圾，拒写不落库
_NARRATIVE_MIN_CONTENT = 50


def _read_narrative_sources() -> dict:
    """读素材（只读，不写）：
       - 全部 self_observation（按 created_at 升序）
       - 近期 insight（最多 30 条，按 created_at 降序）
       - 最后一条 self（仪表盘）
    成功返回 {"status": "ok", "observations": [...], "insights": [...], "last_self": {...}}；
    读不到 → {"status": "no_data", "reason": "..."}。
    不抛异常。"""
    try:
        from . import memory_db  # 惰性 import（与 self_model 同款惯例）
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败也是 no_data
        logger.warning("[self_narrative] import memory_db 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "import memory_db failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    try:
        conn = memory_db.connect()
        try:
            observations = [
                {"id": r[0], "created_at": r[1], "content": r[2]}
                for r in conn.execute(
                    "SELECT id, created_at, content FROM memory_bank "
                    "WHERE kind = ? ORDER BY created_at ASC",
                    (_OBS_KIND,)).fetchall()]
            insights = [
                {"id": r[0], "created_at": r[1], "content": r[2]}
                for r in conn.execute(
                    "SELECT id, created_at, content FROM memory_bank "
                    "WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                    (_INSIGHT_KIND, _INSIGHTS_LIMIT)).fetchall()]
            last_self_row = conn.execute(
                "SELECT id, created_at, content FROM memory_bank "
                "WHERE kind = ? ORDER BY created_at DESC LIMIT 1",
                (_SELF_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败也是 no_data
        logger.warning("[self_narrative] 读素材异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "read narrative sources failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    return {
        "status": "ok",
        "observations": observations,
        "insights": insights,
        "last_self": ({"id": last_self_row[0], "created_at": last_self_row[1],
                       "content": last_self_row[2]}
                      if last_self_row is not None else None),
    }


def _seconds_since_last_generation():
    """距上次生成（最新一条 kind='self_narrative' 的 created_at）的秒数（挂钟）。
    无记录 / 读库失败 / 时间解析失败一律返回 None——冷却条件②按"满足"处理
    （fail-open；stage2 落库时自会建立基线）。"""
    try:
        from . import memory_db
        conn = memory_db.connect()
        try:
            # [桥三 stage2 2026-10-21] 产物定案=写入矩阵预埋的 kind='self_narrative'；
            # 旧探测（kind='self' AND tags LIKE）源于 10-10 陈旧注释，对真实产物
            # 永远盲→冷却失效，已废（_SELF_KIND 仍由 _read_narrative_sources 使用）。
            row = conn.execute(
                "SELECT created_at FROM memory_bank WHERE kind = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (_NARRATIVE_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：探测失败不拦路
        logger.warning("[self_narrative] 冷却探测异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    if row is None or not row[0]:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(row[0]))
    except Exception:  # noqa: BLE001 - fail-open：解析失败当从未生成
        return None
    if dt.tzinfo is None:
        return (datetime.datetime.now() - dt).total_seconds()
    return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()


def check_readiness() -> dict:
    """检查桥三 v1-stage2 是否可以启动：
       条件：
         ① self_observation 条数 >= _SELF_NARRATIVE_MIN_SAMPLES
         ② 距上次生成 >= _SELF_NARRATIVE_COOLDOWN_SEC
    返回 {"ready": bool, "reason": str, "observation_count": int, "threshold": int}。
    吞掉一切异常转 reason，绝不抛。"""
    try:
        sources = _read_narrative_sources()
        if sources.get("status") != "ok":
            return {"ready": False,
                    "reason": "not_ready: sources no_data: %s" % sources.get("reason"),
                    "observation_count": 0,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        count = len(sources["observations"])
        if count < _SELF_NARRATIVE_MIN_SAMPLES:
            return {"ready": False,
                    "reason": "not_ready: observation_count %d < %d"
                              % (count, _SELF_NARRATIVE_MIN_SAMPLES),
                    "observation_count": count,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        elapsed = _seconds_since_last_generation()
        if elapsed is not None and elapsed < _SELF_NARRATIVE_COOLDOWN_SEC:
            return {"ready": False,
                    "reason": "not_ready: cooldown %.0fs < %ds since last generation"
                              % (elapsed, _SELF_NARRATIVE_COOLDOWN_SEC),
                    "observation_count": count,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        return {"ready": True, "reason": "ready",
                "observation_count": count,
                "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_narrative] check_readiness 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ready": False,
                "reason": "not_ready: exception: %s: %s"
                          % (type(e).__name__, str(e)[:120]),
                "observation_count": 0,
                "threshold": _SELF_NARRATIVE_MIN_SAMPLES}


# ---- stage2 叙事生成（2026-10-21）：LLM 熔炼 → 落库。范式=reflection.py 同款 ----

_SYSTEM_PROMPT = """你是 MoRay。请基于以下观察，用第一人称写一篇自我叙事，不超过 800 字。只放事实，不出现"应该/建议"等词。
要求：
1. 归纳而不是复述：写多条记录合起来才成立的规律与状态，不逐条转述观察。
2. 中文纯文本输出：不要 JSON、不要代码块标记、不要小标题与序号。
3. 绝不编造观察里不存在的依据。"""

#: 在飞闸（进程内存活，重启清零）：生成中不重复触发，端点与循环两入口共用
_narrative_inflight = False


def build_narrative_messages(sources: dict):
    """素材 dict → (messages, basis_ids)。纯函数零 IO（test_self_card 同款可单测惯例）：
    - observations（ASC 序）取尾部最新 _NARRATIVE_OBS_LIMIT 条，[n] 编号进 prompt；
    - 编号→真实 memory_id 的映射随 basis_ids 返回，落库时进 tags 溯源（uuid hex 恰
      32 字符=tags 单元截断上限，reflection 同款）。不把 uuid 直写 prompt：
      100 条 × 32 字符 ≈ 3.2k 字符，在 8192 ctx 下纯烧预算；
    - insights / last_self 作附加段；user 总字符钳 _NARRATIVE_PROMPT_CHARS
      （reflection 同款截断口径，超长可能截在行中——预算闸优先于行完整性）。"""
    obs = sources.get("observations") or []
    recent = obs[-_NARRATIVE_OBS_LIMIT:]
    basis_ids = [str(o.get("id") or "") for o in recent if o.get("id")]
    lines = ["[观察记录（按时间序）]"]
    for i, o in enumerate(recent, 1):
        lines.append("[%d] %s" % (i, str(o.get("content") or "")))
    insights = sources.get("insights") or []
    if insights:
        lines.append("")
        lines.append("[近期洞见（反思产出）]")
        for it in insights:
            lines.append("- %s" % str(it.get("content") or ""))
    last = sources.get("last_self")
    if last:
        lines.append("")
        lines.append("[最近自我仪表盘]")
        lines.append(str(last.get("content") or ""))
    user = "\n".join(lines)[:_NARRATIVE_PROMPT_CHARS]
    messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user}]
    return messages, basis_ids


def _strip_code_fence(text: str) -> str:
    """剥模型输出的 ``` 围栏（含 ```text 语言行）→ 纯文本；无围栏原样返回。"""
    t = (text or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl >= 0 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


async def _llm_narrate(messages: list):
    """喂 messages 要纯文本叙事；任何异常/非 200/空应答返回 (None, 原因)。
    复用 reflection 同组 config（REFLECTION_API/BASE_URL/MODEL/TIMEOUT/NUM_CTX），
    双协议口径与其 _llm_reflect 完全一致；差异仅在输出语义：自由文本而非 JSON 数组。"""
    if config.REFLECTION_API == "openai":
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/v1/chat/completions"
        payload = {"model": config.REFLECTION_MODEL, "stream": False,
                   "temperature": 0.0, "messages": messages}
    else:
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/api/chat"
        payload = {"model": config.REFLECTION_MODEL, "stream": False, "temperature": 0.0,
                   "options": {"num_ctx": config.REFLECTION_NUM_CTX},
                   "messages": messages}
    try:
        async with httpx.AsyncClient(timeout=config.REFLECTION_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            return None, "%s HTTP %s（model=%s url=%s）" % (
                config.REFLECTION_API, r.status_code, config.REFLECTION_MODEL, url)
        body = r.json()
        if config.REFLECTION_API == "openai":
            raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        else:
            raw = (body.get("message") or {}).get("content", "")
        text = _strip_code_fence(raw)
        if not text:
            return None, "推理服务应答为空"
        return text, ""
    except Exception as e:  # noqa: BLE001 - fail-open：异常转错误原因返回，不上抛
        logger.warning("[self_narrative] 推理调用异常：%s: %s",
                       type(e).__name__, str(e)[:200])
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])


async def generate_narrative() -> dict:
    """[桥三 v1-stage2] 生成一篇自我叙事并落库（单条 kind='self_narrative'）。
    防跑飞三闸（全在 LLM 调用之前判定，被拒调用零推理开销）：
      ① 在飞闸：_narrative_inflight 生成中不重复触发（端点/循环两入口共用）；
      ② readiness：observation 够量 + 7 天冷却（check_readiness，stage1 闸沿用）；
      ③ 素材非空。
    产出侧两道：过短拒写（<_NARRATIVE_MIN_CONTENT）、落库截断（_NARRATIVE_MAX_CONTENT）。
    写入走 insert_memory（矩阵预埋 source='self_narrative'→kind='self_narrative'；
    主表+FTS 双写 + 同 source 归一化判重幂等均白得）；tags=basis 原料 memory_id 溯源。
    fail-open：任何失败返回 {"ran": False, "reason": ...}，绝不抛。"""
    global _narrative_inflight
    if _narrative_inflight:
        return {"ran": False, "reason": "generation_already_in_progress"}
    readiness = check_readiness()
    if not readiness.get("ready"):
        return {"ran": False,
                "reason": readiness.get("reason") or "not_ready",
                "readiness": readiness}
    sources = _read_narrative_sources()
    if sources.get("status") != "ok":
        return {"ran": False,
                "reason": "sources no_data: %s" % sources.get("reason"),
                "readiness": readiness}
    messages, basis_ids = build_narrative_messages(sources)
    if not basis_ids:
        return {"ran": False, "reason": "no observations to narrate",
                "readiness": readiness}
    _narrative_inflight = True
    try:
        text, err = await _llm_narrate(messages)
        if text is None:
            logger.warning("[self_narrative] LLM 阶段失败：%s", err)
            return {"ran": False, "reason": "llm_failed: %s" % err,
                    "readiness": readiness}
        if len(text) < _NARRATIVE_MIN_CONTENT:
            logger.warning("[self_narrative] 拒写（过短 %d 字）：%r", len(text), text[:40])
            return {"ran": False, "reason": "rejected_too_short: %d chars" % len(text),
                    "readiness": readiness}
        text = text[:_NARRATIVE_MAX_CONTENT]
        mem_id, db_err = insert_memory(
            text, kind=_NARRATIVE_KIND, source=_NARRATIVE_KIND, tags=basis_ids)
        if db_err:
            logger.warning("[self_narrative] 写库失败：%s", db_err)
            return {"ran": False, "reason": "insert_failed: %s" % db_err,
                    "readiness": readiness}
        logger.warning("[self_narrative] 叙事已落库 id=%s chars=%d basis=%d",
                       mem_id, len(text), len(basis_ids))
        return {"ran": True, "memory_id": mem_id, "chars": len(text),
                "basis_count": len(basis_ids), "readiness": readiness}
    finally:
        _narrative_inflight = False


async def self_narrative_maybe() -> dict:
    """主入口（stage2）：ready 且过冷却 → 真生成落库；否则原样带回 reason。
    返回形状与 stage1 契约一致（{"ran", "reason", "readiness"}）。
    本函数吞掉一切异常转 reason——挂进 self_check_loop 也绝不中断循环
    （CancelledError 继承 BaseException，不被 except Exception 捕获，照常上抛）。"""
    try:
        return await generate_narrative()
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_narrative] self_narrative_maybe 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False,
                "reason": "exception: %s: %s" % (type(e).__name__, str(e)[:120]),
                "readiness": {}}


router = APIRouter()


@router.get("/self_narrative/readiness")
def self_narrative_readiness() -> dict:
    """[桥三 v1-stage1] 只读准备度端点：GET /api/self_narrative/readiness。
    直接返回 check_readiness()——只 SELECT，不写库、不调 LLM。"""
    return check_readiness()


@router.post("/self_narrative/generate")
async def self_narrative_generate() -> dict:
    """[桥三 v1-stage2] 手动触发一次叙事生成：POST /api/self_narrative/generate。
    内部判据与循环入口完全同款（在飞闸 + readiness 冷却闸）：生成中重复触发、
    冷却未过、素材不够都如实带回 reason、零推理开销。fail-open 不抛 500。"""
    return {"ok": True, **(await generate_narrative())}


@router.get("/self_narrative/latest")
def self_narrative_latest() -> dict:
    """[桥三 v1-stage2] 只读最新叙事：GET /api/self_narrative/latest（自省面板第九卡数据源）。
    返回 {ok, found, content?, created_at?, chars?}；无记录 found=false；
    读库失败 fail-open 转 ok=false+reason。参数化 SQL，只 SELECT。"""
    try:
        from . import memory_db
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT content, created_at FROM memory_bank "
                "WHERE kind = ? ORDER BY created_at DESC LIMIT 1",
                (_NARRATIVE_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败不炸端点
        logger.warning("[self_narrative] latest 读库异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ok": False, "found": False,
                "reason": "read latest failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    if row is None:
        return {"ok": True, "found": False}
    return {"ok": True, "found": True, "content": row[0],
            "created_at": row[1], "chars": len(row[0] or "")}
