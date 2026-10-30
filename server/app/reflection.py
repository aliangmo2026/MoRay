# -*- coding: utf-8 -*-
"""反思循环 MVP（2026-10-04）：回看最近 task_result → 本地 LLM 提炼 → 写回 kind='insight'。

背景：MemoryBridge M1-M4b 只有"写入+召回"，记忆静态躺着等被召回（侦察 2026-10-04：
19 条记忆零回看）。本模块补"回看→提炼→反哺"最小闭环的第一段：

  手动端点 POST /api/reflection/run 触发 →
  读最近 N 条 kind='task_result' →
  组 prompt 喂本地 Ollama（self_check._llm_json 同款调用范式：/api/chat、
  stream=False、temperature=0、json_repair 容错、任何失败返回 None）→
  跨记录提炼"可复用策略/模式" → 逐条经 memory_routes.insert_memory 写回
  （kind='insight'，source='reflection'，tags 带 basis 原料 memory_id 可溯源）。

边界（本轮 MVP 裁定）：
- 不做定时（无 lifespan 循环）；总闸 MORAY_REFLECTION 默认关，翻转需重启 8000。
- 不开读闸：insight 写回后是否进 prompt 由既有读闸决定
  （读闸只在前端：MoraySettings.memoryRecallEnabled，memory_client.js 召回总闸，出厂默认关；
  后端 /api/memory/recall 无开关校验，不存在 LAYA_MEMORY_RECALL 读闸——2026-10-06 M5-P2 复核）。
- 不碰 Laya/executor；不新增子系统；推理服务基址/协议/模型/超时均 env 可配
  （默认 Ollama /api/chat + qwen3.5:9b；MORAY_REFLECTION_API=openai 可切 llama-server）。
- fail-open：LLM 不可用/解析失败/写库失败都不抛 500、不炸服务——结果与错误
  如实返回调用方并落 warning 日志（self_check 同款静默降级，但不吞：可见）。
- 防跑飞闸：单次最多写 _MAX_INSIGHTS 条；写回前双重查重（归一化全等 +
  basis 原料集合相同——同一批原料只提炼一次，防 LLM 措辞微差绕过全等）；
  输出侧两道质量闸（CJK 占比 ≥30% 拒语言漂移、basis 空拒写强溯源——
  2026-10-06 反思防污染：v29 带伤 completed 学出英文反向 insight 的三层修之一，
  配套 task_routes 写入侧 verify 闸与本 prompt 收紧）。
"""
import json
import logging
import time
from datetime import date

import httpx
from fastapi import APIRouter, Request

from . import config, memory_db
from .memory_routes import insert_memory

logger = logging.getLogger("moray.reflection")

#: 独立 APIRouter（不能复用 memory_routes.router——api.py 已挂载后者，
#: 这里再引用同一对象会把 /api/memory/* 四条路由重复注册一遍）
router = APIRouter()

_INSIGHT_KIND = "insight"
_INSIGHT_SOURCE = "reflection"
#: 单次反思最多写回条数（防跑飞第一道闸；prompt 同步要求模型最多产 5 条）
_MAX_INSIGHTS = 5
#: 单条 insight 落库截断（与 executor._shrink(final_result, 500) 同档）
_MAX_CONTENT = 500
#: user prompt 总字符预算（20 条 task_result ≈ 14k 字符，钳到 12k 防 prompt 爆）
_MAX_PROMPT_CHARS = 12000
#: insight 最短长度——低于此视为复述/垃圾丢弃
_MIN_CONTENT_LEN = 6
#: 语言闸阈值（CJK 字符 / 非空白字符）——历史中文 insight 实测占比 ≥40%，
#: 纯英文产出≈0（v29 实证 835207da）；30% 给模板名/错误码等英文技术词留足余量
_MIN_CJK_RATIO = 0.3

# ---- 算力预算闸（2026-10-07 v4）：限频三闸，端点与 synapse handler 两入口共用，
# 全部在 LLM 调用之前判定——被拒调用零推理开销、不写库。----
#: 全局冷却（秒）：任意两轮反思的最小间隔——拦 bus 连发事件叠出的并发 LLM 调用
_REFLECT_GLOBAL_COOLDOWN_SEC = 10.0
#: 同 trace 冷却（秒）：同一任务链冷却期内不重复提炼——trace_id 由 synapse_bus 传入
_REFLECT_TRACE_COOLDOWN_SEC = 60.0
#: 日上限：进程内计数、跨天重置；独立于 DAILY_MAX_CALLS（那是云代理预算闸的配额）
_REFLECT_DAILY_CAP = 100
#: 闸状态（进程内存活，重启清零；不落库）：最近全局放行时刻 / trace→最近放行时刻 / 当日计数与日期
_reflect_last_global: float = 0.0
_reflect_last_trace: dict[str, float] = {}
_reflect_daily_count: int = 0
_reflect_daily_date: str = ""
_reflect_throttled_daily_cap: int = 0
_reflect_throttled_global: int = 0
_reflect_throttled_trace: int = 0

try:
    import json_repair  # self_check 同款容错；venv 已装但 requirements 未声明，故 try-import
except ImportError:  # noqa: BLE001 - 无 json_repair 时走 _parse_json_array 手写兜底
    json_repair = None

_SYSTEM_PROMPT = """你是 MoRay 记忆库的反思器。输入是若干条任务执行记录（含任务目标、工具链、最终输出）。
任务：跨记录提炼可复用的策略/模式/教训，供以后处理同类任务时参考。
要求：
1. 用中文输出 insight 内容。
2. 归纳而不是复述：提炼单条记录里看不到、多条记录合起来才成立的东西（同类任务反复出现的形态、重复出现的失败原因、有效与无效的处理路径、工具链使用偏好）。
3. 每条 insight 一句话、具体可执行；禁止空话（如"要仔细检查代码"），禁止逐条转述单条记录。
4. 数量 0-5 条：若确实没有清晰模式，可输出空数组 []，不要为凑数强行提炼；绝不编造记录里不存在的依据。
5. 优先对比成功记录之间的共性；失败记录的共因可作为警示，但需明确标注为"失败教训"，不得提炼为通用规律。
6. 忽略探针任务、测试任务、明显重复的记录；只从有实质执行结果的记录中提炼。
只输出 JSON 数组，不要解释、不要代码块标记。元素格式：
{"insight": "一句话策略", "basis": [1,3]}
basis 是支撑该结论的记录编号（输入里的 [n] 序号）。insight 文本内不要出现 [n] 编号引用，编号只放在 basis 字段。
措辞风格示例（仅示意风格，内容以实际记录为准）：{"insight": "同类脚本任务反复失败时，先提取多次 stderr 的公共错误行定位共因，再一次性修复", "basis": [1,2,3]}"""


def _recent_task_results(limit: int) -> list:
    """读最近 N 条 task_result（新→旧），带 id 供 insight tags 溯源。"""
    conn = memory_db.connect()
    try:
        rows = conn.execute(
            "SELECT id, content, tags, created_at FROM memory_bank "
            "WHERE kind = 'task_result' ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _norm_text(s: str) -> str:
    """查重归一化：去空白与常见中英标点。LLM 同输入下两次输出常有措辞微差
    （实测 run#1/#2 仅"开头/首行"一词之差），裸全等拦不住。"""
    for ch in "，。；：、！？（）【】《》“”‘’—…·,.;:!?()[]{}<>\"' \t\r\n":
        s = s.replace(ch, "")
    return s


def _cjk_ratio(text: str) -> float:
    """CJK 字符占非空白字符比例——输出侧语言闸判据（配套 _MIN_CJK_RATIO）。"""
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return 0.0
    return sum(1 for ch in chars if "\u4e00" <= ch <= "\u9fff") / len(chars)


def _find_duplicate(content: str, basis_ids: list) -> str:
    """已有 insight 与本次产出视为重复时返回原因说明，不重复返回空串。

    两道闸（防跑飞；库无唯一约束，重复反思必然撞重复）：
    ① 归一化后全等——拦措辞微差的同义条目；
    ② basis 原料集合完全相同——同一批原料只提炼一次（反思幂等语义的最小
       实现；tags 里存的原料 memory_id 即比对键）。"""
    basis_set = {t for t in basis_ids if t}
    conn = memory_db.connect()
    try:
        rows = conn.execute(
            "SELECT content, tags FROM memory_bank WHERE kind = ?", (_INSIGHT_KIND,)
        ).fetchall()
    finally:
        conn.close()
    norm = _norm_text(content)
    for r in rows:
        if _norm_text(r["content"]) == norm:
            return "同文（归一化后）insight 已存在"
        existing = {t for t in (r["tags"] or "").split(",") if t and t != _INSIGHT_SOURCE}
        if basis_set and existing and basis_set == existing:
            return "basis 原料集合相同的 insight 已存在"
    return ""


def _parse_json_array(raw: str):
    """模型输出 → list；失败返回 None。json_repair 优先，缺失时手写兜底：
    剥围栏 → 取首个 '[' 到末个 ']' → json.loads。"""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if json_repair is not None:
            fixed = json_repair.repair_json(text)
            data = json.loads(fixed) if isinstance(fixed, str) else fixed
        else:
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end <= start:
                return None
            data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else None
    except (ValueError, TypeError):
        return None


async def _llm_reflect(items: list):
    """喂 N 条记录要 JSON 数组；任何异常/非 200/解析失败返回 (None, 原因)。

    双协议（config.REFLECTION_API）：ollama=/api/chat（默认，self_check 同款）；
    openai=/v1/chat/completions（本机 Ollama 引擎损坏时用 llama-server 等兼容
    服务加载同一模型的替代运行时，模型与 prompt 不变）。"""
    numbered = "\n".join("[%d] %s" % (i, it["content"]) for i, it in enumerate(items, 1))
    user = numbered[:_MAX_PROMPT_CHARS]
    messages = [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user}]
    if config.REFLECTION_API == "openai":
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/v1/chat/completions"
        payload = {"model": config.REFLECTION_MODEL, "stream": False, "temperature": 0.0,
                   "messages": messages}
    else:
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/api/chat"
        payload = {"model": config.REFLECTION_MODEL, "stream": False, "temperature": 0.0,
                   "options": {"num_ctx": config.REFLECTION_NUM_CTX},
                   "messages": messages}
    try:
        async with httpx.AsyncClient(timeout=config.REFLECTION_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            return None, "%s HTTP %s（model=%s api=%s url=%s）" % (
                config.REFLECTION_API, r.status_code, config.REFLECTION_MODEL,
                config.REFLECTION_API, url)
        body = r.json()
        if config.REFLECTION_API == "openai":
            raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        else:
            raw = (body.get("message") or {}).get("content", "")
        if not raw:
            return None, "推理服务应答为空"
        data = _parse_json_array(raw)
        if data is None:
            return None, "JSON 解析失败：raw=%r" % raw[:120]
        return data, ""
    except Exception as e:  # noqa: BLE001 - fail-open：异常转错误原因返回，不上抛
        logger.warning("[reflection] 推理调用异常：%s: %s", type(e).__name__, str(e)[:200])
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])


async def run_reflection(limit: int = 20, trace_id: str | None = None) -> dict:
    """一轮反思：读 → 提炼 → 写回。返回 dict 由端点并入 {ok:true, ...} 响应。

    任何失败都到这里为止（fail-open）：LLM 失败 → processed 带回、created=0、
    error 说明原因；单条写库失败 → 跳过该条并记入 errors，其余继续。
    算力预算闸（2026-10-07 v4）最先判定：日上限 / 全局冷却 / 同 trace 冷却
    （trace_id=None = 非链上调用，只受前两道）；拒绝一律返回
    {"processed": 0, "insights_created": 0, "insights": [], "note": "throttled: <原因>"}，
    不碰 LLM、不写库。
    """
    global _reflect_last_global, _reflect_daily_count, _reflect_daily_date, \
           _reflect_throttled_daily_cap, _reflect_throttled_global, _reflect_throttled_trace
    now = time.monotonic()
    today = date.today().isoformat()
    if _reflect_daily_date != today:  # 跨天首调：当日计数清零
        _reflect_daily_count = 0
        _reflect_daily_date = today
        _reflect_throttled_daily_cap = 0
        _reflect_throttled_global = 0
        _reflect_throttled_trace = 0
    if _reflect_daily_count >= _REFLECT_DAILY_CAP:
        _reflect_throttled_daily_cap += 1
        return {"processed": 0, "insights_created": 0, "insights": [],
                "note": "throttled: daily_cap"}
    if now - _reflect_last_global < _REFLECT_GLOBAL_COOLDOWN_SEC:
        _reflect_throttled_global += 1
        return {"processed": 0, "insights_created": 0, "insights": [],
                "note": "throttled: global_cooldown"}
    if trace_id is not None:
        lt = _reflect_last_trace.get(trace_id, 0.0)
        if now - lt < _REFLECT_TRACE_COOLDOWN_SEC:
            _reflect_throttled_trace += 1
            return {"processed": 0, "insights_created": 0, "insights": [],
                    "note": "throttled: trace_cooldown"}
    # 放行才记账（被拒调用不消耗任何预算）；顺手清掉已出冷却期的 trace 记录防膨胀
    _reflect_last_global = now
    _reflect_daily_count += 1
    if trace_id is not None:
        _reflect_last_trace[trace_id] = now
        for k in [k for k, t in _reflect_last_trace.items()
                  if now - t >= _REFLECT_TRACE_COOLDOWN_SEC]:
            del _reflect_last_trace[k]

    items = _recent_task_results(limit)
    if not items:
        return {"processed": 0, "insights_created": 0, "insights": [],
                "note": "记忆库中无 task_result 记录，无可提炼原料"}

    data, err = await _llm_reflect(items)
    if data is None:
        logger.warning("[reflection] LLM 阶段失败：%s", err)
        return {"processed": len(items), "insights_created": 0, "insights": [], "error": err}

    created, errors = [], []
    # [可观测 2026-10-06] skipped=查重幂等跳过（正常态）；errors=其余拒写/丢弃。
    # 只计数供汇总日志，不改变 errors[] 内容与返回结构。
    n_skipped = n_rejected = 0
    for entry in data[:_MAX_INSIGHTS]:
        if not isinstance(entry, dict):
            n_rejected += 1
            logger.warning("[reflection] 丢弃非 dict 条目：%r", str(entry)[:60])
            continue
        text = str(entry.get("insight") or "").strip()
        if len(text) < _MIN_CONTENT_LEN:
            errors.append("丢弃过短条目：%r" % text[:40])
            n_rejected += 1
            logger.warning("[reflection] 拒写（过短复述）：%r", text[:40])
            continue
        # [反思防污染 2026-10-06] 语言闸：库面向中文，纯英文产出拒写（errors 通道可见）
        ratio = _cjk_ratio(text)
        if ratio < _MIN_CJK_RATIO:
            errors.append("拒写（语言漂移：CJK 占比 %.0f%%）：%s…" % (ratio * 100, text[:40]))
            n_rejected += 1
            logger.warning("[reflection] 拒写（语言漂移）insight=%r ratio=%.0f%% basis=%s",
                           text[:60], ratio * 100, entry.get("basis"))
            continue
        # basis 编号 → 原料 memory_id 进 tags（uuid hex 恰 32 字符 = tags 单元截断上限）
        basis_ids = []
        for n in (entry.get("basis") or []):
            try:
                idx = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(items):
                basis_ids.append(items[idx]["id"])
        # [反思防污染 2026-10-06] 溯源闸：无 basis = 无从核对依据，拒写
        if not basis_ids:
            errors.append("拒写（无溯源依据）：%s…" % text[:40])
            n_rejected += 1
            logger.warning("[reflection] 拒写（无溯源）insight=%r basis=%s",
                           text[:60], entry.get("basis"))
            continue
        dup = _find_duplicate(text, basis_ids)
        if dup:
            errors.append("跳过（%s）：%s…" % (dup, text[:40]))
            n_skipped += 1
            # 查重属正常幂等，但 info 级会被 lastResort（WARNING 门槛）吞掉——
            # 全服务端无 logging 配置（2026-10-06 核），用 warning + "跳过"字样区分
            logger.warning("[reflection] 跳过（%s）insight=%r", dup, text[:60])
            continue
        text = text[:_MAX_CONTENT]
        mem_id, db_err = insert_memory(
            text, kind=_INSIGHT_KIND,
            tags=[_INSIGHT_SOURCE] + basis_ids,
            source=_INSIGHT_SOURCE,
        )
        if db_err:
            errors.append("写库失败：%s" % db_err)
            n_rejected += 1
            logger.warning("[reflection] 写库失败：%s", db_err)
            continue
        created.append({"memory_id": mem_id, "content": text, "basis": basis_ids})

    # [可观测 2026-10-06] 无条件汇总——Synapse 自动触发时无人读 HTTP 响应，
    # processed/created/skipped/errors 必须落日志（created=0 也要打）
    logger.warning("[reflection] processed=%d created=%d skipped=%d errors=%d",
                   len(items), len(created), n_skipped, n_rejected)
    if created:
        logger.warning("[reflection] %d 条 task_result → %d 条 insight 写回", len(items), len(created))
    return {"processed": len(items), "insights_created": len(created),
            "insights": created, "errors": errors}


@router.post("/api/reflection/run")
async def reflection_run(request: Request):
    """[反思MVP] 手动触发一次反思。总闸 MORAY_REFLECTION（默认 0=关）。

    成功：{ok, processed, insights_created, insights[, errors]}
    LLM/解析失败：仍 ok=true（fail-open，端点本身没坏），error 字段说明原因。
    关闸：403 {ok:false, code:"disabled"}。
    """
    if not config.REFLECTION_ENABLED:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={
            "ok": False, "code": "disabled",
            "message": "反思循环未启用（MORAY_REFLECTION=0）。设 MORAY_REFLECTION=1 并重启 8000 后可用。"
        })
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - body 可选，空体 = 默认参数
        body = {}
    try:
        limit = int(body.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 50))
    result = await run_reflection(limit)
    return {"ok": True, **result}
