# -*- coding: utf-8 -*-
"""Mem0 式长效语义记忆提炼器（路线二 2026-11-07）

职责：每次 /api/llm/chat 对话结束后（非流式生成完成 / 流式 [DONE] 吐完），
fire-and-forget 地用本地小模型（config.MEMORY_EXTRACT_MODEL，默认 qwen2.5:1.5b）
分析最近几轮对话，提炼 1-3 条关于用户的客观事实/技术偏好，经 KNN 向量去重后
写入 memory_bank（kind=auto_fact / auto_pref，source=memory_extractor）。

三道红线（任务书原文，逐条落地）：
1. 绝不阻塞主聊天通道：llm_proxy 只调 schedule_extract_after_chat()——
   create_task 即返；提炼全链路（模型调用 + KNN + 写库）跑在 asyncio.to_thread
   工作线程，事件循环零阻塞；任何失败只写本模块日志，绝不报错给前端。
2. 绝不污染 corpus：kind 恒为 auto_fact / auto_pref，写入面被
   memory_routes._SOURCE_KIND_MATRIX["memory_extractor"] 许可矩阵闸死
   （越界 kind 直接拒绝落库）；召回白名单显式收编这两个 kind，corpus 依旧
   排除在外（与数字分身语料的隔离口径不变）。
3. 闲聊零入库：提示词明示"无重要信息返回 []"，产出侧再加本地护栏
   （长度 / 字符集 / 纯寒暄黑名单过滤），双层防线。

去重三档（任务书）：对每条候选事实先 nomic 嵌入，再在 auto_fact / auto_pref
两个 kind 分区内各做 KNN 检索（vec0 元数据单值等值下推，memory_embed 现成），
候选回表后用真实余弦判档——vec0 路径的 1/(1+L2) 换算分与"相似度 0.9"的余弦
语义不对应，故判档一律 cosine 现算（余弦兜底路径同函数，口径恒一）：
   cos > 0.9            → 重复，跳过（skipped_duplicate）
   0.7 ≤ cos ≤ 0.9      → 同一事实的新表述，原地 UPDATE 旧行
                          （content / updated_at / FTS / 向量全刷新）
   cos < 0.7            → 新事实，insert_memory 正常入库（spawn_embed 自动补嵌）

算力卫生（本机 8G 显存单槽位惯例）：
- 模型调用带 keep_alive=0：用完即卸，防 1.5B 常驻挤兑主对话 9B 的显存
  （Iris 双模型挤兑冷载 69s 的教训）；num_ctx 2048 / num_predict 300 控制单次开销。
- 同 session 冷却窗（config.MEMORY_EXTRACT_COOLDOWN_SEC，默认 15s）+ 全局
  在飞上限 2：防 dsh/ZCode 工具环连发聊天把提炼刷成洪水。

fail-open 总纪律：本模块任何失败都不影响聊天主链路与记忆既有读写面。
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import config, memory_db, memory_embed

logger = logging.getLogger("moray.memory_extractor")


class _ExtractFileHandler(logging.Handler):
    """提炼器专属文件日志（logs/memory_extractor.log，INFO 起 append）。

    [2026-11-07 实测教训] "moray" 根 logger 只挂 ERROR 级 handler，模块 INFO/WARNING
    全被吞——提炼链路静默失败时零痕迹可查（本战真机 E2E 排障时亲身踩坑）。故本模块
    自挂 handler（self_check.install_error_handlers 同款"模块自养日志"先例），导入时
    幂等安装；目录/文件创建失败则跳过（日志面缺失不影响提炼主链路，fail-open）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            path = Path(__file__).resolve().parents[2] / "logs" / "memory_extractor.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:  # noqa: BLE001 - 日志面缺失不扰主链路
            pass


def _install_extract_logger() -> None:
    """幂等安装：文件 handler + stderr（WARNING 起，与 bot_client 同款可达性）。"""
    if any(isinstance(h, _ExtractFileHandler) for h in logger.handlers):
        return
    logger.setLevel(logging.INFO)
    logger.addHandler(_ExtractFileHandler())
    _console = logging.StreamHandler()
    _console.setLevel(logging.WARNING)
    _console.setFormatter(logging.Formatter("[memory_extractor] %(message)s"))
    logger.addHandler(_console)


_install_extract_logger()

#: 写库 source（与 memory_routes._SOURCE_KIND_MATRIX["memory_extractor"] 成对）
SOURCE = "memory_extractor"
#: tags：自动记忆统一打 auto 标（前端记忆库列表可一键过滤/批量清理）
_AUTO_TAGS = ("auto",)

#: 参与提炼的对话轮数与字符预算（长会话只看最近几轮；工具环消息 content 多为空，
#: 拼接时自然被过滤）
_TRANSCRIPT_MAX_MESSAGES = 8
_TRANSCRIPT_MAX_CHARS = 4000
_PER_MSG_CHARS = 600

#: 事实护栏：过短是残句、过长是叙事，都不配进记忆库
_FACT_MIN_CHARS = 6
_FACT_MAX_CHARS = 300
#: 纯寒暄/客套黑名单（命中即弃——提示词拦大面，这里拦漏网）
_CHITCHAT_PAT = re.compile(
    r"^(你好|您好|哈喽|嗨|hi|hello|hey|谢谢|多谢|thanks|thank you|再见|拜拜"
    r"|早|晚安|辛苦了|好的|收到|嗯+|哦+|哈哈+|今天天气)", re.IGNORECASE)
#: 整句纯寒暄（问候词后最多跟标点/语气符，再无实义内容）——用户行前置闸用。
#: 刻意整句锚定：不能拦"你好，帮我写个爬虫"这类以问候开头的正经请求。
_PURE_GREETING = re.compile(
    r"^(你好|您好|哈喽|嗨|hi|hello|hey|谢谢|多谢|thanks|再见|拜拜|早[上安]?|晚安"
    r"|辛苦了|好的|收到|嗯+|哦+|哈哈+)[\s!！？?。.，,~～]*$", re.IGNORECASE)
#: 天气/时间类一次性问句（问了不构成"用户的事实"——1.5B 实测会把"今天天气如何"
#: 幻觉成"用户是一名希望了解天气的普通用户"，必须前置闸+事实护栏双拦）
_WEATHER_QUERY = re.compile(
    r"^(今天|明天|后天|现在|当地)?(天气|气温|温度|几点|时间)(如何|怎么样|怎样|呢|吗|？|\?|$)"
    r"|^(查|看|问|了解|说)(一?下)?(今天的?)?(天气|气温|时间)")
#: 事实级垃圾模式：把一次性提问包装成"用户事实"的幻觉产物
_FACT_JUNK_PAT = re.compile(r"(了解|问|询问|关心|聊|提到)(一?下)?(今天|当地)?(天气|气温|时间)"
                            r"|(今天|明天)?天气(如何|怎么样|怎样|相关|情况)")
#: 含至少一个 CJK 字符或 ASCII 字母才算有效事实（纯标点/纯数字/emoji 弃）
_HAS_WORD = re.compile(r"[\u4e00-\u9fffA-Za-z]")

#: 偏好类关键词（命中 → kind=auto_pref，否则 auto_fact）。口语宽匹配，宁错 pref
#: 不冒充 fact 的代价为零（两者同权重、同召回白名单，仅标签语义差异）。
_PREF_HINTS = ("喜欢", "爱用", "偏好", "常用", "习惯用", "习惯", "倾向", "最喜欢",
               "最喜欢用", "不喜欢", "讨厌", "优先用", "一直用", "prefer", "favorite")

#: 去重三档阈值（任务书钉死）
_SIM_SKIP = 0.90     # > 0.9 → 重复，跳过
_SIM_UPDATE = 0.70   # 0.7 ~ 0.9 → 原地更新；< 0.7 → 新增

_SYSTEM_PROMPT = (
    "你是一个记忆提炼器。分析下面的对话，提炼出关于「用户」的 1-3 条客观事实或"
    "技术偏好（例如职业、技术栈、常用工具、项目背景、明确表达的喜好）。\n"
    "规则：\n"
    "1. 只提炼对话中明确出现的信息，禁止推测、禁止编造。\n"
    "2. 每条为一句话，主语用「用户」。\n"
    "3. 闲聊、问候、天气、与用户本人无关的内容一律不要提炼。\n"
    "4. 如果没有值得记住的信息，输出 []。\n"
    "5. 只输出一个 JSON 字符串数组，例如 [\"用户是一名前端开发者\"]，"
    "不要输出任何解释或 Markdown 代码块。\n"
    "6. 判断标准：只记「长期有效」的信息（职业、技术栈、项目、长期偏好）；"
    "一次性的提问（问天气、问时间、寒暄客套）不构成用户的事实，绝不能提炼——"
    "对话里全是这类内容时必须输出 []。"
)


# ---- 上下文构建（线程内执行） ----

def _build_transcript(messages: list) -> str:
    """messages → 最近几轮 user/assistant 纯文本对话稿（预算内从最新往回收）。"""
    turns = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        turns.append(("用户" if role == "user" else "MoRay", content[:_PER_MSG_CHARS]))
    lines = []
    budget = _TRANSCRIPT_MAX_CHARS
    for tag, content in reversed(turns):
        line = "%s: %s" % (tag, content)
        if budget - len(line) < 0:
            break
        lines.append(line)
        budget -= len(line)
        if len(lines) >= _TRANSCRIPT_MAX_MESSAGES:
            break
    return "\n".join(reversed(lines))


# ---- 模型调用与解析（线程内执行） ----

def _call_extract_model(transcript: str) -> str:
    """对话稿 → 小模型原始输出。/api/chat ollama 协议（self_narrative._llm_narrate
    同款 payload 形状）；keep_alive=0 用完即卸（算力卫生见模块头注）。"""
    url = str(config.OLLAMA_BASE_URL or "").rstrip("/") + "/api/chat"
    payload = {
        "model": config.MEMORY_EXTRACT_MODEL,
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 300},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    }
    with httpx.Client(timeout=config.MEMORY_EXTRACT_TIMEOUT) as c:
        r = c.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError("extract_model HTTP %s (model=%s)" % (
            r.status_code, config.MEMORY_EXTRACT_MODEL))
    return str(((r.json() or {}).get("message") or {}).get("content") or "")


def _parse_facts(raw: str) -> list:
    """模型输出 → 清洗后的事实数组（最多 3 条）。双层护栏：
    ① JSON 解析（裸数组 / Markdown 围栏 / 混入散文时截 [..] 兜底）；
    ② 逐条护栏：长度 6~300、含 CJK 或字母、不命中寒暄黑名单。"""
    t = str(raw or "").strip()
    if not t:
        return []
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t).strip()
    data = None
    try:
        data = json.loads(t)
    except Exception:  # noqa: BLE001 - 小模型常见"散文包数组"，截取兜底
        s, e = t.find("["), t.rfind("]")
        if s != -1 and e > s:
            try:
                data = json.loads(t[s:e + 1])
            except Exception:  # noqa: BLE001
                return []
    if isinstance(data, dict):  # 模型输出对象形态时兼容 facts/memories 键
        data = data.get("facts") or data.get("memories") or []
    if not isinstance(data, list):
        return []
    facts = []
    for item in data:
        if isinstance(item, dict):
            item = item.get("fact") or item.get("content") or item.get("text") or ""
        s = str(item or "").strip().strip('"')
        if not (_FACT_MIN_CHARS <= len(s) <= _FACT_MAX_CHARS):
            continue
        if not _HAS_WORD.search(s):
            continue
        if _CHITCHAT_PAT.match(s) or _FACT_JUNK_PAT.search(s):
            continue
        facts.append(s)
        if len(facts) >= 3:
            break
    return facts


def _classify_kind(fact: str) -> str:
    """事实 → auto_pref（命中偏好关键词）/ auto_fact。lower 后宽匹配。"""
    low = str(fact or "").lower()
    return "auto_pref" if any(h in low for h in _PREF_HINTS) else "auto_fact"


# ---- 写入与去重（线程内执行） ----

def _best_auto_match(vec: list) -> tuple:
    """事实向量 → (best_id, best_cos, best_kind)：在 auto_fact / auto_pref 两个
    kind 分区各做 KNN 取候选（vec0 单值等值下推），回表后用真实余弦取最优。
    向量面任何异常返回 ("", 0.0, "")——调用方 fail-open 直接入库。"""
    cand_ids = []
    for kind in ("auto_fact", "auto_pref"):
        try:
            for mid, _s in memory_embed.search_similar(vec, k=8, kind=kind):
                cand_ids.append(mid)
        except Exception as e:  # noqa: BLE001 - fail-open
            logger.warning("[memory_extractor] KNN 候选检索异常：%s: %s",
                           type(e).__name__, str(e)[:160])
    if not cand_ids:
        return "", 0.0, ""
    best_id, best_cos, best_kind = "", 0.0, ""
    try:
        conn = memory_db.connect()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_extractor] 记忆库连接异常：%s: %s",
                       type(e).__name__, str(e)[:160])
        return "", 0.0, ""
    try:
        for mid in dict.fromkeys(cand_ids):  # 去重保序，行数 ≤ 16
            row = conn.execute(
                "SELECT id, kind, embedding FROM memory_bank WHERE id = ?",
                (mid,)).fetchone()
            if row is None:
                continue
            stored = memory_embed.unpack_vector(row["embedding"])
            if not stored:
                continue
            c = memory_embed.cosine(vec, stored)
            if c > best_cos:
                best_id, best_cos, best_kind = row["id"], c, row["kind"]
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_extractor] 候选回表异常：%s: %s",
                       type(e).__name__, str(e)[:160])
    finally:
        conn.close()
    return best_id, best_cos, best_kind


def _update_memory(mem_id: str, new_content: str) -> bool:
    """0.7~0.9 档：原地 UPDATE 旧行（content + updated_at + FTS 重写），随后
    同步刷新向量面（本函数已运行在后台线程，同步嵌入不挡任何人）。FTS5 无
    UPDATE 语义，走 DELETE+INSERT 重写（memory_routes forget 同款手法）。
    向量刷新失败不算更新失败（backfill_embeddings 随时兜底）。"""
    ok = False
    try:
        conn = memory_db.connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT tags FROM memory_bank WHERE id = ?", (mem_id,)).fetchone()
            if row is not None:
                now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                conn.execute(
                    "UPDATE memory_bank SET content = ?, updated_at = ? WHERE id = ?",
                    (new_content, now, mem_id))
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mem_id,))
                conn.execute(
                    "INSERT INTO memory_fts (memory_id, content, tags) VALUES (?, ?, ?)",
                    (mem_id, new_content, row["tags"] or ""))
                conn.commit()
                ok = True
            else:
                conn.rollback()  # 行已被并发删除：按新增处理（调用方 fail-open）
        except Exception:  # noqa: BLE001
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_extractor] 记忆更新异常（mem_id=%s）：%s: %s",
                       mem_id, type(e).__name__, str(e)[:160])
        return False
    if ok:
        try:
            memory_embed.index_memory(mem_id, new_content)
        except Exception as e:  # noqa: BLE001 - 向量面失败不影响内容更新
            logger.warning("[memory_extractor] 更新后向量刷新失败（mem_id=%s）：%s",
                           mem_id, str(e)[:120])
    return ok


def _store_fact(fact: str, session_id: str) -> dict:
    """单条事实 → 去重三档判档 → 落库。返回动作明细（测试与日志消费）。"""
    kind = _classify_kind(fact)
    vec = None
    try:
        vec = memory_embed.get_embedding(fact)
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_extractor] 事实嵌入异常：%s: %s",
                       type(e).__name__, str(e)[:160])
    if vec:
        best_id, best_cos, _best_kind = _best_auto_match(vec)
        if best_id and best_cos > _SIM_SKIP:
            logger.info("[memory_extractor] 去重命中跳过（cos=%.3f id=%s）：%s",
                        best_cos, best_id[:8], fact[:50])
            return {"action": "skipped_duplicate", "kind": kind,
                    "similar_id": best_id, "cos": round(best_cos, 4)}
        if best_id and best_cos >= _SIM_UPDATE:
            updated = _update_memory(best_id, fact)
            logger.info("[memory_extractor] 近似更新（cos=%.3f id=%s）：%s",
                        best_cos, best_id[:8], fact[:50])
            return {"action": "updated" if updated else "error", "kind": kind,
                    "similar_id": best_id, "cos": round(best_cos, 4)}
    # 新事实（或向量面不可用 fail-open：放弃 KNN 去重仍入库，精确判重由
    # insert_memory 的同 source 逐字查重兜底）
    mem_id, err = _insert(fact, kind, session_id)
    action = "inserted" if mem_id else ("rejected" if err.startswith("写入校验拒绝") else "error")
    if err and action == "error":
        logger.warning("[memory_extractor] 落库失败：%s", err[:160])
    return {"action": action, "kind": kind, "memory_id": mem_id,
            "error": err[:160] if err else ""}


def _insert(content: str, kind: str, session_id: str) -> tuple:
    """统一走 memory_routes.insert_memory（唯一 INSERT 汇聚点：许可矩阵 /
    精确判重 / FTS 双写 / spawn_embed 全在里面）。lazy import 防循环。"""
    from .memory_routes import insert_memory
    return insert_memory(content, kind=kind, tags=list(_AUTO_TAGS),
                         source=SOURCE, session_id=session_id)


def _user_line_substantive(line: str) -> bool:
    """用户行是否含实质内容（闲聊前置闸的判据）：内容 ≥6 字符、非整句纯寒暄、
    非天气/时间一次性问句。三者全过才算实质——"你好，帮我写个爬虫"属实质
    （纯寒暄整句锚定不会误拦），"今天天气如何"属闲聊。"""
    content = str(line or "")
    if content.startswith("用户:"):
        content = content[len("用户:"):].strip()
    if len(content) < _FACT_MIN_CHARS:
        return False
    if _PURE_GREETING.match(content) or _WEATHER_QUERY.match(content):
        return False
    return True


# ---- 主入口（async；重活全在 to_thread） ----

async def extract_memory_from_chat(messages: list, session_id: str = "default") -> dict:
    """对话消息 → 提炼事实 → KNN 去重 → 落库。任何失败静默降级（返回含
    error/skipped_reason 的统计 dict，绝不抛）。任务书指定的核心函数。"""
    stats = {"enabled": True, "transcript_chars": 0, "facts": [],
             "results": [], "elapsed_ms": 0, "error": "", "skipped_reason": ""}
    t0 = time.monotonic()
    if not config.MEMORY_EXTRACT_ENABLED:
        stats["enabled"] = False
        stats["skipped_reason"] = "disabled"
        return stats
    transcript = _build_transcript(messages)
    stats["transcript_chars"] = len(transcript)
    # 本地护栏前置：没有实质用户发言（全空/全工具帧/纯寒暄/纯天气问句）连模型都不叫
    # [1.5B 实测 2026-11-07] 小模型会把"今天天气如何"幻觉成事实，闲聊必须在闸内拦截
    if not any(_user_line_substantive(line) for line in transcript.split("\n")):
        stats["skipped_reason"] = "no_user_content"
        return stats
    try:
        stats.update(await asyncio.to_thread(_extract_sync, transcript, session_id, stats))
    except Exception as e:  # noqa: BLE001 - fire-and-forget 纪律：只记日志不上抛
        stats["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        logger.warning("[memory_extractor] 提炼失败（session=%s）：%s",
                       session_id, stats["error"])
    stats["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return stats


def _extract_sync(transcript: str, session_id: str, stats: dict) -> dict:
    """线程内的同步提炼主体：调模型 → 解析 → 逐条去重落库。"""
    raw = _call_extract_model(transcript)
    facts = _parse_facts(raw)
    stats["facts"] = facts
    if not facts:
        stats["skipped_reason"] = "no_facts_extracted"
        logger.info("[memory_extractor] 无可提炼事实（session=%s, %d 字对话稿）",
                    session_id, len(transcript))
        return stats
    results = []
    for fact in facts:
        try:
            results.append(_store_fact(fact, session_id))
        except Exception as e:  # noqa: BLE001 - 单条失败不影响其余
            logger.warning("[memory_extractor] 单条落库异常：%s: %s",
                           type(e).__name__, str(e)[:160])
            results.append({"action": "error", "kind": _classify_kind(fact),
                            "error": "%s: %s" % (type(e).__name__, str(e)[:120])})
    stats["results"] = results
    logger.info("[memory_extractor] 提炼完成（session=%s）：%d/%d 条落库",
                session_id, sum(1 for r in results if r.get("action") in ("inserted", "updated")),
                len(facts))
    return stats


# ---- fire-and-forget 调度（llm_proxy 唯一入口） ----

#: create_task 的强引用集（asyncio 文档要求：任务无人持引会被 GC 掐死）
_EXTRACT_TASKS: set = set()
#: 每 session 最近一次调度时刻（冷却窗记账；monotonic 时钟）
_SESSION_LAST: dict = {}


def schedule_extract_after_chat(messages: list, session_id: str = "default",
                                *, skip: bool = False) -> None:
    """对话收尾调度提炼（llm_proxy 消费）：本函数**永不抛、永不等待**——
    skip（organ 回环 / 标题请求）、总闸、冷却窗、在飞上限任一命中即静默返回，
    否则 create_task 即返。提炼失败只写日志（extract_memory_from_chat 内兜）。"""
    try:
        if skip or not config.MEMORY_EXTRACT_ENABLED or not isinstance(messages, list):
            return
        if len(_EXTRACT_TASKS) >= 2:  # 全局在飞上限：绝不与主对话抢算力
            return
        sid = str(session_id or "default").strip() or "default"
        cooldown = float(config.MEMORY_EXTRACT_COOLDOWN_SEC or 0)
        now = time.monotonic()
        if cooldown > 0 and now - _SESSION_LAST.get(sid, 0.0) < cooldown:
            return
        loop = asyncio.get_running_loop()
        task = loop.create_task(extract_memory_from_chat(messages, sid))
        _SESSION_LAST[sid] = now
        _EXTRACT_TASKS.add(task)
        task.add_done_callback(_EXTRACT_TASKS.discard)
    except Exception:  # noqa: BLE001 - fire-and-forget 纪律
        pass
