"""PM Agent —— 需求澄清与 PRD 生成（产品经理前置闸，2026-11-14）。

定位：主聊天流派发给执行链路（Agent 工具环 / Laya 任务）**之前**的一道需求质检闸。
模糊的复杂任务（"帮我优化一下项目"）先反问 1-3 个关键问题；用户答完之后把需求
整理成结构化任务书（背景/目标/涉及文件/验收标准）再放行——执行层拿到的不再是
一句口号，而是一份可验收的任务书。

红线纪律（与 memory_extractor / skill_extractor 同源）：
- fail-open：模型超时 / Ollama 不可达 / JSON 解析失败 / 任何异常 → 一律透传原始
  输入，绝不阻断聊天（闸的代价是多一次澄清，闸坏的代价是系统不可用）；
- 本地优先：PM 判定只用本地 Ollama（默认主对话模型），绝不为此调云端计费；
- 事件循环卫生：同步重活（httpx 模型调用 / usage 落库）全部 asyncio.to_thread，
  async 入口自身零阻塞；
- 一轮封顶：上下文里出现过澄清标记 → 强制 PRD 模式并钳掉 questions，绝不连环
  反问刷屏（确定性钳制 + 提示词双重保险）。

三层过滤（从便宜到贵）：
  ① 总闸 env MORAY_PM_AGENT（0/false/no/off = 整体下线）；
  ② 启发式预筛 should_clarify_candidate()：纯寒暄 / 太短 / 太长 / 带明确文件
     路径 / 纯提问 / 无模糊范围词 → 0ms 透传，不碰模型（绝大多数消息止步于此）；
  ③ 本地模型判定：候选消息才调模型，按系统提示词输出 JSON（need_clarify /
     questions / prd_draft）。

对 llm_proxy 的契约（clarify_requirement 返回 dict）：
  {"need_clarify": bool,          # True = 需要反问（调用方短路返回 reply_text）
   "questions": [...],            # 1-3 个反问（need_clarify=False 时恒 []）
   "prd_draft": str,              # 任务书文本（need_clarify=False 时尽量非空）
   "refined_prompt": str,         # 透传用 refined prompt（= prd_draft 或原输入）
   "reply_text": str,             # 需要反问时直接给用户看的完整 Markdown 文本
   "taskbook_text": str,          # 放行时注入到用户消息尾部的任务书文本（可空）
   "mode": "clarify"|"passthrough"|"taskbook",
   "rounds": int,                 # 本上下文已发生的澄清轮数
   "note": str}                   # 失败/降级原因（成功时为空）
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

from . import config
from . import db as _db

logger = logging.getLogger("moray.pm_agent")


# ---- 日志自挂 handler（moray 根 logger 只放行 ERROR，INFO 会被吞——
#      同 memory_extractor 教训：模块必须自挂专属文件 handler 才留得下痕） ----
class _PmFileHandler(logging.Handler):
    """PM Agent 专属文件日志（logs/pm_agent.log，INFO 起 append）。

    写不进仓库 logs 目录（只读盘 / 权限 / 磁盘满）时静默降级为空转，
    绝不让日志拖垮聊天主链路。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        self._path: Path | None = None
        try:
            path = Path(__file__).resolve().parents[2] / "logs" / "pm_agent.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = path
        except Exception:  # noqa: BLE001 - fail-open
            self._path = None

    def emit(self, record: logging.LogRecord) -> None:
        if self._path is None:
            return
        try:
            line = self.format(record) + "\n"
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:  # noqa: BLE001 - 日志旁路永不抛
            pass


def _ensure_pm_logger() -> None:
    if any(isinstance(h, _PmFileHandler) for h in logger.handlers):
        return
    logger.setLevel(logging.INFO)
    logger.addHandler(_PmFileHandler())
    # 控制台只放 WARNING+（moray 根 logger 对 INFO 静默，重复挂会双写）
    _console = logging.StreamHandler()
    _console.setLevel(logging.WARNING)
    _console.setFormatter(logging.Formatter("[pm_agent] %(levelname)s %(message)s"))
    logger.addHandler(_console)
    logger.propagate = False


_ensure_pm_logger()


# ---- 配置（env > .env > 内置默认；与 config.py 同款 MORAY_ 前缀约定） ----
def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off")


PM_ENABLED = lambda: _flag("MORAY_PM_AGENT")  # noqa: E731 - 总闸（进程内读 env，测试可覆盖）
PM_MODEL = os.environ.get("MORAY_PM_MODEL", "").strip() or config.OLLAMA_MODEL
PM_TIMEOUT = float(os.environ.get("MORAY_PM_TIMEOUT", "25"))
PM_KEEP_ALIVE = os.environ.get("MORAY_PM_KEEP_ALIVE", "10m").strip()
PM_CONTEXT_MSGS = int(os.environ.get("MORAY_PM_CONTEXT_MSGS", "8"))
PM_CTX_CHARS = int(os.environ.get("MORAY_PM_CTX_CHARS", "4000"))
PM_MAX_QUESTIONS = 3
PM_FORCE_FINAL_ROUNDS = int(os.environ.get("MORAY_PM_FORCE_FINAL_ROUNDS", "1"))

#: 澄清标记：出现在给用户的回复正文里，同时是"这轮对话已经澄清过"的判定锚点
#: （前端 moray-panels 也靠它检测要不要弹快速选择条——改文案必须三处同步：
#: 这里 / llm_proxy 无关 / panels PmClarifyBar）。
CLARIFY_MARKER = "产品经理 · 需求澄清"


# ---------------- ① 启发式预筛（0ms，不碰模型） ----------------

#: 模糊范围动词：命中才可能是"模糊的复杂任务"。刻意收窄——闸的意义是拦住
#: "优化一下项目"这类方向性口号，而不是给每句话加一道模型税。
_VAGUE_SCOPE_PAT = re.compile(
    r"优化|改进|改善|完善|重构|升级|加个|加一个|加一下|添加|增加|接入|新增|"
    r"做一个|做个|实现|开发|搭建|搞一个|搞一下|弄一个|弄一下|写一个|写个|"
    r"整体|全面|系统地",
    re.IGNORECASE)

#: 明确文件引用：出现具体路径/文件名/代码引用 = 需求已落到具体对象，不拦
_FILE_REF_PAT = re.compile(
    r"[\w./\\-]+\.(py|ts|tsx|js|jsx|md|json|yaml|yml|toml|html|css|sql|bat|ps1|sh)\b"
    r"|[A-Za-z]:[\\/]|(^|\s)(server/|src/|tests?/|docs?/)", re.IGNORECASE)

#: 纯寒暄/短问句：整句问候（ memory_extractor._PURE_GREETING 同款思路，整句锚定）
_PURE_GREETING_PAT = re.compile(
    r"^(你好|您好|哈喽|嗨|hi|hello|hey|谢谢|多谢|thanks|再见|拜拜|早[上安]?|晚安"
    r"|辛苦了|好的|收到|嗯+|哦+|哈哈+)[\s!！？?。.，,~～]*$", re.IGNORECASE)

#: 夜班前缀（已被夜班链路处理过，别再叠一道闸）
_NIGHT_PREFIX_PAT = re.compile(r"^\s*\[夜班模式\]")

#: 预筛放行的上下界（经验值：太短=寒暄/点名，太长=贴了大段材料，都够具体）
PM_MIN_CHARS = 8
PM_MAX_CHARS = 1200


def last_user_text(messages: list) -> str:
    """messages → 最后一条 user 消息的纯文本（多模态分片只取文本片）。

    没有任何 user 消息（纯 system 调用等）返回空串。"""
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # OpenAI 多模态形状：只拼文本片
            return " ".join(
                str(p.get("text") or "") for p in content
                if isinstance(p, dict)).strip()
        return ""
    return ""


def is_pm_candidate(user_input: str) -> bool:
    """启发式预筛：这条输入值不值得花一次本地模型调用去判定？

    False = 明确透传（闲聊 / 明确指令 / 带文件路径 / 纯提问 / 太短太长）。
    True  = 模糊复杂任务候选，交给模型判定。
    刻意保守：宁可漏放几个模糊需求（模型层还有兜底），不可误拦明确指令。"""
    t = str(user_input or "").strip()
    if not t or len(t) < PM_MIN_CHARS or len(t) > PM_MAX_CHARS:
        return False
    if _PURE_GREETING_PAT.match(t):
        return False
    if _NIGHT_PREFIX_PAT.match(t):
        return False
    if _FILE_REF_PAT.search(t):
        return False
    # 短问句（<60 字且以问号收尾）= 用户在提问，不是在派任务
    if len(t) < 60 and t[-1] in "？?":
        return False
    return _VAGUE_SCOPE_PAT.search(t) is not None


def count_clarify_rounds(messages: list) -> int:
    """上下文里已出现的澄清轮数（assistant 消息含 CLARIFY_MARKER 计 1 轮）。

    用于一轮封顶：用户答完反问之后，PM 必须直接出任务书，不许连环反问。"""
    n = 0
    for m in messages or []:
        if (isinstance(m, dict) and m.get("role") == "assistant"
                and CLARIFY_MARKER in str(m.get("content") or "")):
            n += 1
    return n


# ---------------- ② 提示词构建 ----------------

_PM_SYSTEM_PROMPT = (
    "你是 MoRay 的产品经理（PM Agent）。用户的需求可能比较模糊。"
    "如果需求缺少具体文件路径、验收标准或技术栈，请生成 1-3 个反问问题。"
    "如果需求已经足够清晰，请将其转换为一份结构化任务书（包含背景、目标、"
    "涉及文件、验收标准）。只输出 JSON。\n"
    "\n"
    "输出格式（必须是合法 JSON，禁止 Markdown 代码块、禁止解释文字）：\n"
    '{"need_clarify": true, "questions": ["问题1", "问题2"], "prd_draft": ""}\n'
    "或\n"
    '{"need_clarify": false, "questions": [], "prd_draft": "任务书全文"}\n'
    "\n"
    "规则：\n"
    "1. need_clarify=true：questions 给 1-3 个反问。每个问题尽量在末尾用括号给出"
    "2-3 个候选项，格式严格为：你想改哪个模块？（A) 后端接口 / B) 前端交互 / "
    "C) 全部）——不适合给候选的问题就不要括号。\n"
    "2. need_clarify=false：questions 必须是空数组，prd_draft 写完整任务书，"
    "包含【背景】【目标】【涉及文件】【验收标准】四个小节；验收标准必须可验证。\n"
    "3. 任务书用中文，简洁、可执行。\n"
    "4. 用户消息就是当前需求（可能带历史对话），只输出 JSON。"
)

_PM_FORCE_FINAL_CLAUSE = (
    "\n5. 【强制】历史对话里已经出现过一轮产品经理反问、用户已作回答。"
    "禁止再反问：必须 need_clarify=false，基于已有信息直接输出任务书；"
    "信息仍不足的部分在任务书的【假设】小节里写明假设即可。"
)


def _build_transcript(messages: list, current_input: str) -> str:
    """最近几轮 user/assistant 对话稿（预算内从最新往回收，同 memory_extractor）。"""
    turns = []
    budget = PM_CTX_CHARS
    count = 0
    for m in reversed(messages or []):
        if count >= PM_CONTEXT_MSGS or budget <= 0:
            break
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        line = ("%s: %s" % ("用户" if role == "user" else "MoRay",
                            content[:600]))
        if len(line) > budget:
            break
        turns.append(line)
        budget -= len(line)
        count += 1
    turns.reverse()
    turns.append("用户（本次需求）: %s" % str(current_input or "").strip()[:600])
    return "\n".join(turns)


# ---------------- ③ 模型调用与解析（同步，to_thread 调用） ----------------

def _call_pm_model(transcript: str, force_final: bool) -> str:
    """对话稿 → PM 模型原始输出。/api/chat ollama 协议（memory_extractor 同款形状）。

    keep_alive 默认 10m 而非 0：澄清判定之后紧跟主对话调用，让这次调用顺手把
    模型焐热，而不是用完即卸逼主对话吃冷加载。"""
    system_prompt = _PM_SYSTEM_PROMPT + (_PM_FORCE_FINAL_CLAUSE if force_final else "")
    url = str(config.OLLAMA_BASE_URL or "").rstrip("/") + "/api/chat"
    payload = {
        "model": PM_MODEL,
        "stream": False,
        "keep_alive": PM_KEEP_ALIVE,
        "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 700},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript},
        ],
    }
    with httpx.Client(timeout=PM_TIMEOUT) as c:
        r = c.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError("pm_model HTTP %s (model=%s)" % (r.status_code, PM_MODEL))
    return str(((r.json() or {}).get("message") or {}).get("content") or "")


def _extract_json(raw: str) -> dict:
    """模型输出 → dict（裸 JSON / Markdown 围栏 / 混入散文时截 {..} 兜底）。

    三层全失败 → 抛 ValueError（调用方 fail-open 透传）。"""
    t = str(raw or "").strip()
    if not t:
        raise ValueError("empty pm output")
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t).strip()
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - 落到花括号截取兜底
        pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        data = json.loads(t[s:e + 1])  # 仍失败就让异常上抛 → fail-open
        if isinstance(data, dict):
            return data
    raise ValueError("pm output is not a JSON object")


def _clean_questions(data: dict) -> list:
    """questions 字段清洗：字符串数组、去空、去重、截到 3 条。"""
    qs = data.get("questions")
    if not isinstance(qs, list):
        return []
    out, seen = [], set()
    for q in qs:
        if not isinstance(q, str):
            continue
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        out.append(q[:200])
        if len(out) >= PM_MAX_QUESTIONS:
            break
    return out


def format_clarify_reply(questions: list) -> str:
    """questions → 直接给用户看的澄清 Markdown（marker 是前端检测锚点，别改文案）。"""
    lines = [
        "🧭 **%s**" % CLARIFY_MARKER,
        "",
        "这个需求方向有了，但缺几个关键信息。我先确认清楚，再把它整理成"
        "可执行、可验收的任务书：",
        "",
    ]
    for i, q in enumerate(questions, 1):
        lines.append("%d. %s" % (i, q))
    lines += [
        "",
        "直接回复编号 + 你的答案（例如：1. 后端接口；2. 查询 <100ms），"
        "或补充任何背景。回答后我会生成结构化任务书并继续推进。",
    ]
    return "\n".join(lines)


def format_taskbook_text(prd_draft: str, user_input: str) -> str:
    """放行时注入到用户消息尾部的任务书块（角色交替不变，只追加文本）。"""
    body = str(prd_draft or "").strip() or str(user_input or "").strip()
    return (
        "\n\n[产品经理任务书 · 需求澄清已补全，系统自动附加]\n"
        + body
        + "\n（执行以任务书为准；用户消息本身是原始意图，两者冲突时以任务书的"
          "验收标准为准。）"
    )


def _passthrough(user_input: str, reason: str, rounds: int = 0) -> dict:
    return {
        "need_clarify": False,
        "questions": [],
        "prd_draft": "",
        "refined_prompt": user_input,
        "reply_text": "",
        "taskbook_text": "",
        "mode": "passthrough",
        "rounds": rounds,
        "note": reason,
    }


# ---------------- 进程内判决缓存（同一条消息的并发请求共用同一判决） ----------------
# 背景（真机 2026-11-14）：dsh harness 对同一轮可能并发打两发 llm_chat（86ms 间隔），
# 各自跑一次 9B 判定会 (a) 白烧一次本地算力 (b) 两判决可能不一致（一个要澄清一个
# 放行）互相打架。缓存 key = 输入+上下文指纹，TTL 30s：同进程内同消息恒同判决。
_VERDICT_CACHE: dict = {}
_VERDICT_TTL = 30.0


def _verdict_cache_key(user_input: str, context: list) -> str:
    import hashlib
    tail = ""
    for m in reversed(context or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            tail = str(m.get("role")) + ":" + str(m.get("content") or "")[:120]
            break
    raw = "%s|%d|%s" % (str(user_input)[:200], len(context or []), tail)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def _cache_get(key: str):
    hit = _VERDICT_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _VERDICT_TTL:
        return hit[1]
    if hit:
        _VERDICT_CACHE.pop(key, None)
    return None


def _cache_put(key: str, verdict: dict) -> None:
    _VERDICT_CACHE[key] = (time.time(), verdict)
    if len(_VERDICT_CACHE) > 64:  # 顺手清过期项，防长跑膨胀
        now = time.time()
        for k in [k for k, (t, _) in _VERDICT_CACHE.items() if now - t >= _VERDICT_TTL]:
            _VERDICT_CACHE.pop(k, None)


# ---------------- 同步内核（线程内执行，绝不触碰事件循环） ----------------

def clarify_requirement_sync(user_input: str, context: list) -> dict:
    """clarify_requirement 的同步内核：预筛 → 模型 → 解析 → 钳制 → 组装。

    所有失败路径都收敛为 passthrough（fail-open），绝不抛异常出函数。"""
    if not PM_ENABLED():
        return _passthrough(str(user_input or ""), "pm_disabled")
    user_input = str(user_input or "").strip()
    context = context if isinstance(context, list) else []
    rounds = count_clarify_rounds(context)
    force_final = rounds >= PM_FORCE_FINAL_ROUNDS

    # 判决缓存：同一消息的并发/紧邻重发共用同一判决（30s TTL，进程内）。
    # 位置刻意在总闸/预筛之前——缓存本身就是 0 成本短路的最高一层。
    _ck = _verdict_cache_key(user_input, context)
    _cached = _cache_get(_ck)
    if _cached is not None:
        logger.info("[pm] 命中判决缓存（%s）：%s", _cached.get("mode"), user_input[:50])
        return dict(_cached)

    if not user_input:
        return _passthrough("", "empty_input", rounds)
    if force_final:
        # 一轮封顶路径：上一轮 PM 已经反问、用户这轮的回答就是要整理的素材。
        # 回答通常不含模糊动词（"1. 数据库；2. 查询 <100ms"），完整预筛会把
        # 第二轮拦死——这里只保留最低卫生线（非空 / 非纯寒暄 / 非超长粘贴）。
        if (len(user_input) < 2 or len(user_input) > PM_MAX_CHARS * 4
                or _PURE_GREETING_PAT.match(user_input)):
            return _passthrough(user_input, "prefilter_pass", rounds)
    elif not is_pm_candidate(user_input):
        logger.info("[pm] 透传（预筛不命中）：%s", user_input[:60])
        return _passthrough(user_input, "prefilter_pass", rounds)

    started = time.time()
    try:
        transcript = _build_transcript(context, user_input)
        raw = _call_pm_model(transcript, force_final)
        data = _extract_json(raw)
    except Exception as e:  # noqa: BLE001 - fail-open：模型/解析失败绝不阻断聊天
        logger.warning("[pm] 模型判定失败，透传原输入：%s: %s",
                       type(e).__name__, str(e)[:160])
        _verdict = _passthrough(user_input, "model_fail:%s" % type(e).__name__,
                                rounds)
        _cache_put(_ck, _verdict)
        return _verdict
    finally:
        # 用量旁路记账（tokens 取 Ollama 真值需要响应体，这里按耗时粗记兜底；
        # 精确 token 在 _call_pm_model 内不可达——保持旁路纪律：有则记，无则 0）
        _record_pm_usage(int((time.time() - started) * 1000))

    questions = _clean_questions(data)
    need_clarify = bool(data.get("need_clarify")) and not force_final
    prd = str(data.get("prd_draft") or "").strip()
    if force_final:
        # 确定性钳制：一轮封顶。模型不听话还想反问 → 全部钳掉，按透传放行。
        questions = []
        need_clarify = False

    if need_clarify and questions:
        reply_text = format_clarify_reply(questions)
        logger.info("[pm] 触发澄清（rounds=%d，%d 问）：%s",
                    rounds + 1, len(questions), user_input[:60])
        _verdict = {
            "need_clarify": True,
            "questions": questions,
            "prd_draft": prd,
            "refined_prompt": user_input,
            "reply_text": reply_text,
            "taskbook_text": "",
            "mode": "clarify",
            "rounds": rounds + 1,
            "note": "",
        }
        _cache_put(_ck, _verdict)
        return _verdict

    # 不需要澄清：有任务书 → 注入放行；没有 → 纯透传
    if prd:
        logger.info("[pm] 出任务书放行（rounds=%d，%d 字）：%s",
                    rounds, len(prd), user_input[:60])
        _verdict = {
            "need_clarify": False,
            "questions": [],
            "prd_draft": prd,
            "refined_prompt": prd,
            "reply_text": "",
            "taskbook_text": format_taskbook_text(prd, user_input),
            "mode": "taskbook",
            "rounds": rounds,
            "note": "",
        }
        _cache_put(_ck, _verdict)
        return _verdict
    logger.info("[pm] 模型判定清晰，纯透传（rounds=%d）：%s", rounds, user_input[:60])
    return _passthrough(user_input, "model_pass", rounds)


def _record_pm_usage(ms: int) -> None:
    """PM 调用旁路记账（usage_log，model 名带 pm/ 前缀便于区分主对话）。
    SQL 字面量 + ? 绑定；任何异常只落 DEBUG，绝不影响响应。"""
    try:
        conn = _db.connect()
        try:
            conn.execute(
                "INSERT INTO usage_log (date, model, prompt_tokens, completion_tokens, cost_cny, ms, ts) "
                "VALUES (?, ?, 0, 0, 0.0, ?, ?)",
                (time.strftime("%Y-%m-%d"), "pm/" + str(PM_MODEL)[:110],
                 max(0, int(ms or 0)), int(time.time() * 1000)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 账本旁路失败不影响响应
        logger.debug("[usage_log] PM 记账失败（不影响响应）：%s: %s",
                     type(e).__name__, e)


# ---------------- async 主入口（llm_proxy 唯一调用点） ----------------

async def clarify_requirement(user_input: str, context: list) -> dict:
    """主聊天流前置闸入口。

    返回 dict 契约见模块头注。总闸关闭 / 任何失败 → 透传原输入（fail-open）。
    同步重活（模型 HTTP + 记账）整体在 to_thread 工作线程，事件循环零阻塞；
    调用方（llm_chat）本就挂在 _cancel_on_disconnect 竞速语义下，客户端断连
    会取消整个 await——线程内残余执行无害（结果被丢弃）。"""
    if not PM_ENABLED():
        return _passthrough(str(user_input or ""), "pm_disabled")
    return await asyncio.to_thread(clarify_requirement_sync, user_input, context)
