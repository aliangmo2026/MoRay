# -*- coding: utf-8 -*-
"""自驱引擎 v1（目标生成器）self_driven（2026-10-23）：

自驱引擎第一步——读最近一次自我叙事落库的心智状态（memory_bank 中
kind='self_narrative' 行 content JSON 里的 mental_state 字段），让本地 LLM
据此生成一个简短改进目标，封装成结构化目标卡进待审批队列（pending_approval）。

级别边界（与 self_narrative 同口径，勿扩）：
- 对 memory_bank：只 SELECT 读心智状态，绝不写、绝不改；
- 产物不落 memory_bank：目标卡自带 status 流转语义（pending_approval），
  memory_bank 只追加无 UPDATE，装不下审批状态机 → 落本模块私有状态文件
  （config.DATA_DIR/self_driven_state.json，与 memory.sqlite3/synapse.sqlite3
  同目录惯例），每日配额与目标队列同文件；
- fail-open：读不到心智状态 / LLM 失败 / 产出为空一律返回 None（"没生成"），
  事实原因只进日志，绝不把异常抛给调用方；唯熔断落盘失败 fail-close（见下）；
- 零自动执行（硬约束）：目标只生成到 pending_approval，绝不接入执行链/自省
  循环/启动钩子；唯一入口 = POST /api/self_driven/generate 手动触发；
- LLM 调用零新增配置面：复用 reflection 同组 config（REFLECTION_API/BASE_URL/
  MODEL/TIMEOUT/NUM_CTX），与 self_narrative._llm_narrate 完全同款 httpx 双协议。

安全熔断（防跑飞，两道）：
- 每日限额 _DAILY_GOAL_LIMIT = 3（挂钟日历日）：配额计数落状态文件持久化，
  跨进程重启依然成立——进程级计数器重启即清零，等于没有熔断；
- 在飞闸 _goal_inflight（self_narrative._narrative_inflight 同款）：生成中不
  重复触发。check/set 同步无 await，事件循环单线程内天然原子；也因此
  **状态文件互斥锁绝不跨 await**——async 里持锁等 await 会把整个事件循环
  冻死，_STATE_LOCK 只包 _record_goal 这种纯同步段。
配额完整性 fail-close：_record_goal 落盘失败/超限 → generate_goal 返回 None
不发卡——配额没记上却发卡，限额会被绕穿，熔断必须宁可不生成。
"""
import datetime
import json
import logging
import threading
import uuid

import httpx
from fastapi import APIRouter

from . import config

logger = logging.getLogger("moray.self_driven")

#: 心智状态来源 kind（self_narrative 落库产物：content JSON 的 mental_state 字段）
_NARRATIVE_KIND = "self_narrative"
#: 每日目标生成上限（安全熔断核心：挂钟日历日，落盘计数跨重启成立）
_DAILY_GOAL_LIMIT = 3
#: 心智状态注入 prompt 的字符上限（防跑飞；正常 mental_state 才几百字符）
_MENTAL_STATE_CHARS = 2000
#: 目标文本字符上限（prompt 要求 50 字，这里只当跑马护栏给 4 倍余量）
_GOAL_TEXT_MAX = 200

#: 状态文件（每日配额 + 待审批队列单文件）。
#: 路径/原子写完全照 log_monitor.STATE_FILE 与 task_routes 落盘范式：
#: 模块常量 Path + with_suffix('.json.tmp') 临时文件 + replace 原子换名。
STATE_DIR = config.DATA_DIR
STATE_FILE = STATE_DIR / "self_driven_state.json"

#: 目标生成 prompt（任务书原样）。{mental_state} 用 str.replace 注入——
#: 模板与 JSON 花括号会跟 str.format 打架，replace 最稳。
_SYSTEM_PROMPT = (
    "你是 MoRay。这是你当前的心智状态：{mental_state}。"
    "请根据你最近的失败或不足，给自己生成一个简短的改进目标（不超过 50 字）。"
    "只输出目标本身，不要解释。"
)
_USER_PROMPT = "生成我的下一步目标。"

#: 在飞闸（进程内存活，重启清零）：生成中不重复触发（self_narrative 同款）
_goal_inflight = False
#: 状态文件读改写互斥（只包纯同步段，绝不跨 await——见模块 docstring）
_STATE_LOCK = threading.Lock()


# ---- 状态文件（配额 + 待审批队列，JSON 单文件） ----

def _load_state() -> dict:
    """读状态文件 → {"daily": {...}, "queue": [...]}。
    缺失/损坏/形状不对一律回默认空状态（fail-open，不抛）。"""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            queue = data.get("queue")
            daily = data.get("daily")
            return {"queue": queue if isinstance(queue, list) else [],
                    "daily": daily if isinstance(daily, dict) else {}}
    except (OSError, ValueError):
        pass  # 从未写过（FileNotFoundError）/坏 JSON（JSONDecodeError）= 空状态
    return {"queue": [], "daily": {}}


def _save_state(state: dict) -> bool:
    """原子写状态文件（log_monitor._save_state 同款：临时文件写完再 replace，
    防拦腰截断出坏 JSON）。失败返回 False，不抛。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(STATE_FILE)
        return True
    except Exception as e:  # noqa: BLE001 - 调用方按 fail-close 处理，这里只记账
        logger.warning("[self_driven] 状态文件写入异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return False


def _today() -> str:
    """挂钟日历日（本地时区，与 memory_bank localtime 口径一致）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _quota_info(state: dict) -> dict:
    """今日配额水位 {"date", "generated_today", "limit"}。
    换日自动归零（不写盘，写盘只发生在 _record_goal）——状态文件里的旧
    日期就是天然的"上一次生成日"记录。"""
    daily = state.get("daily") if isinstance(state.get("daily"), dict) else {}
    date = _today()
    count = 0
    if daily.get("date") == date:
        try:
            count = int(daily.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
    return {"date": date, "generated_today": count, "limit": _DAILY_GOAL_LIMIT}


def _record_goal(card: dict) -> bool:
    """配额 +1 并把目标卡追加进队列，原子写回状态文件。成功 True / 失败 False。
    熔断双重保险：写盘前再查一次今日限额（generate_goal 的 LLM 前检查拦
    浪费推理开销，这里守最终一致性——配额没记上就发卡 = 限额可被绕穿）。
    纯同步段：_STATE_LOCK 绝不跨 await。"""
    with _STATE_LOCK:
        state = _load_state()
        quota = _quota_info(state)
        if quota["generated_today"] >= quota["limit"]:
            logger.warning("[self_driven] 拒记：今日配额已满 %d/%d",
                           quota["generated_today"], quota["limit"])
            return False
        state["daily"] = {"date": quota["date"],
                          "count": quota["generated_today"] + 1}
        state["queue"] = list(state.get("queue") or []) + [card]
        if not _save_state(state):
            logger.warning("[self_driven] 拒发卡：配额/队列落盘失败（熔断 fail-close）")
            return False
        return True


# ---- 心智状态读取（memory_bank 只读） ----

def _fetch_mental_state():
    """读 memory_bank 最新一条 kind='self_narrative'，解析 content JSON 取
    mental_state（dict）。无记录/非 JSON/mental_state 空/读库失败一律返回
    None（fail-open，不抛）。只 SELECT，绝不写库。"""
    try:
        from . import memory_db  # 惰性 import（self_narrative 同款惯例）
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败当没有
        logger.warning("[self_driven] import memory_db 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    try:
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT content FROM memory_bank WHERE kind = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (_NARRATIVE_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败当没有
        logger.warning("[self_driven] 读心智状态异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    if row is None or not row[0]:
        return None
    try:
        doc = json.loads(row[0])
    except (ValueError, TypeError):
        return None  # stage1 旧纯文本行/坏 JSON：当没有心智状态
    ms = doc.get("mental_state") if isinstance(doc, dict) else None
    return ms if isinstance(ms, dict) and ms else None


# ---- LLM 目标生成（self_narrative._llm_narrate 同款 httpx 双协议） ----

def build_goal_messages(mental_state: dict):
    """心智状态 dict → messages。纯函数零 IO（build_narrative_messages 同款
    可单测惯例）：mental_state 序列化紧凑 JSON 后替换进 system 模板，
    整体钳 _MENTAL_STATE_CHARS 防跑飞。"""
    ms_text = json.dumps(mental_state, ensure_ascii=False,
                         separators=(",", ":"))[:_MENTAL_STATE_CHARS]
    system = _SYSTEM_PROMPT.replace("{mental_state}", ms_text)
    return [{"role": "system", "content": system},
            {"role": "user", "content": _USER_PROMPT}]


def _clean_goal_text(raw: str) -> str:
    """模型输出 → 目标单行文本：剥代码围栏/成对引号、空白归一、钳长。
    纯函数零 IO。清理后为空 = 模型空转，调用方按"没生成"处理。"""
    t = (raw or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl >= 0 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    pairs = {'"': '"', "'": "'", "“": "”", "「": "」"}
    if len(t) >= 2 and t[0] in pairs and t[-1] == pairs[t[0]]:
        t = t[1:-1].strip()
    t = " ".join(t.split())  # 目标是单行：换行/多空白归一成空格
    return t[:_GOAL_TEXT_MAX]


async def _llm_goal(messages: list):
    """喂 messages 要一句目标文本；任何异常/非 200/空应答返回 (None, 原因)。
    与 self_narrative._llm_narrate 同一套 httpx 配置（REFLECTION_API/BASE_URL/
    MODEL/TIMEOUT/NUM_CTX）与双协议口径；差异仅输出语义：纯文本非 JSON。"""
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
        if not (raw or "").strip():
            return None, "推理服务应答为空"
        return raw, ""
    except Exception as e:  # noqa: BLE001 - fail-open：异常转错误原因返回，不上抛
        logger.warning("[self_driven] 推理调用异常：%s: %s",
                       type(e).__name__, str(e)[:200])
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])


# ---- 目标生成主流程 ----

async def generate_goal():
    """生成一个待办目标卡 → {"task_id", "goal", "status", "created_at"}。
    无心智状态/今日配额耗尽/LLM 失败/产出为空/落盘失败一律返回 None
    （fail-close 到"没生成"，原因进日志），绝不抛、绝不自动执行目标。

    防跑飞闸（LLM 调用前全判完，被拒调用零推理开销）：
      ① 在飞闸 _goal_inflight：生成中不重复触发；
      ② 心智状态非空（_fetch_mental_state）；
      ③ 每日配额 < _DAILY_GOAL_LIMIT（安全熔断）。
    产出侧：文本清洗（_clean_goal_text，空产出拒发卡）→ 发卡前 _record_goal
    再守一次限额并原子落盘，落盘失败不发卡（熔断 fail-close）。"""
    global _goal_inflight
    if _goal_inflight:
        logger.warning("[self_driven] 拒绝：上一次生成还在飞")
        return None
    mental_state = _fetch_mental_state()
    if not mental_state:
        logger.warning("[self_driven] 拒绝：无可用心智状态（memory_bank 无 "
                       "kind='%s' 或 mental_state 为空）", _NARRATIVE_KIND)
        return None
    quota = _quota_info(_load_state())
    if quota["generated_today"] >= quota["limit"]:
        logger.warning("[self_driven] 拒绝：今日配额已满 %d/%d（安全熔断）",
                       quota["generated_today"], quota["limit"])
        return None
    messages = build_goal_messages(mental_state)
    _goal_inflight = True
    try:
        raw, err = await _llm_goal(messages)
        if raw is None:
            logger.warning("[self_driven] LLM 阶段失败：%s", err)
            return None
        goal = _clean_goal_text(raw)
        if not goal:
            logger.warning("[self_driven] 拒发卡（产出为空）：%r", str(raw)[:60])
            return None
        card = {"task_id": str(uuid.uuid4()),
                "goal": goal,
                "status": "pending_approval",
                "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
        if not _record_goal(card):  # 超限/落盘失败：不发卡（见模块 docstring）
            return None
        logger.warning("[self_driven] 目标已进待审批队列 task_id=%s goal=%s",
                       card["task_id"], goal[:40])
        return card
    finally:
        _goal_inflight = False


# ---- API（POST /api/self_driven/generate + GET /api/self_driven/queue） ----

router = APIRouter()


@router.post("/self_driven/generate")
async def self_driven_generate() -> dict:
    """[自驱引擎 v1] 手动触发一次目标生成（测试入口）：POST /api/self_driven/generate。
    前置廉价检查（心智状态/今日配额）给可诊断 reason、零推理开销；generate_goal
    内部同款强制再查（熔断在生成点执行，不信调用方）。
    成功 {"ok": True, **目标卡}；被拦/失败 {"ok": False, "reason": ...}。"""
    if _fetch_mental_state() is None:
        return {"ok": False, "reason": "no_mental_state",
                "detail": "memory_bank 无可用心智状态，先生成自我叙事（"
                          "POST /api/self_narrative/generate）再来"}
    quota = _quota_info(_load_state())
    if quota["generated_today"] >= quota["limit"]:
        return {"ok": False, "reason": "daily_limit_reached", **quota}
    card = await generate_goal()
    if card is None:
        return {"ok": False, "reason": "generation_failed",
                "detail": "LLM 失败/产出为空/配额落盘失败，详见服务端日志"}
    return {"ok": True, **card}


@router.get("/self_driven/queue")
def self_driven_queue() -> dict:
    """[自驱引擎 v1] 只读待审批目标队列：GET /api/self_driven/queue。
    返回 status='pending_approval' 的目标卡（新→旧）+ 今日配额水位。
    只读状态文件，不调 LLM、不写任何库。"""
    state = _load_state()
    items = [g for g in (state.get("queue") or [])
             if isinstance(g, dict) and g.get("status") == "pending_approval"]
    return {"ok": True, "count": len(items), "items": list(reversed(items)),
            **_quota_info(state)}
