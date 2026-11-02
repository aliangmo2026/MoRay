# -*- coding: utf-8 -*-
"""外部触达 v1（2026-11-01）：OneBot v11 入站路由 + 后台消费 worker。

链路（同机部署，反向上报）：
    OneBot(NapCat/Lagrange) --POST /api/bot/webhook--> MoRay
      ├─ ① 总闸/鉴权：BOT_ENABLED=false → 503；X-Signature(HMAC-SHA1) 或
      │     Bearer 不匹配 → 401（fail-close）。X-Signature 用原始 body 验签，
      │     绝不能用 json 解析后重新序列化的字节（NapCat 接入头号坑）。
      ├─ ② 归一化：只收 message 事件（群聊/私聊），剥 CQ 码取纯文本
      ├─ ③ 防重：message_id 内存 LRU（上限 2000），重复直接回 duplicate
      ├─ ④ 入队：asyncio.Queue(maxsize=100)，**立即 200**（绝不在请求里 await LLM）
      └─ ⑤ bot_worker（单并发）：回环调 /api/llm/chat(stream=false) → 落库 → 回推 OneBot

为什么必须 stream=false：llm_proxy.py:904 的默认值是 True（SSE），漏了这个字段
拿回的是 text/event-stream，json 解析直接失败——这是本模块的头号避坑点。

为什么必须独立 session_id：llm_proxy.py:745-766 按 session_id 计数，
不带就落 "default" 桶与浏览器聊天共用 MAX_LLM_CALLS_PER_SESSION（默认 30 次），
一条群聊就能把用户自己的对话配额刷爆。

落库约定（UI 侧边栏可见）：conversation_id 固定 "bot:qq:<scope>:<chat_id>"；
crud.upsert_message 只认 camelCase 键 conversationId（传 conversation_id 会静默写空串，
main.py:115-118 有同款踩坑注释）。

安全：本模块不接受任何 URL 入参；LLM 回环地址写死 127.0.0.1 + config.PORT 字面量。
"""
import asyncio
import collections
import hashlib
import html
import hmac
import logging
import re
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import bot_client, config, crud

logger = logging.getLogger("moray.bot")

router = APIRouter()

#: 入队上限。满了不阻塞 webhook（返回 queued=false），由 OneBot 侧重投兜底。
_QUEUE_MAX = 100
#: 去重 LRU 上限（message_id）。只防 OneBot 超时重投的短窗口重复，不做永久账本。
_LRU_MAX = 2000
#: 单条文本上限（与 bot_client._TEXT_MAX 同口径；超长截断，不整条丢弃）。
_TEXT_MAX = 2000
#: 回环 LLM 调用超时。llm_proxy 内部 read 超时是 300s，这里给 180s：
#: 本地 9B 冷加载通常 10-60s，180s 兜住慢盘但不让 worker 无限挂住。
_LLM_TIMEOUT_S = 180.0
#: CQ 码（字符串格式消息）。段列表格式在 _text_from_message 里按 type 过滤，不靠正则。
_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")
#: 回环目标写死字面量（不接受任何外部输入，不存在 SSRF 面）。
_LLM_URL = "http://127.0.0.1:%d/api/llm/chat" % config.PORT
#: IM 场景的回复风格约束。聊天窗口不是文档编辑器：短、口语、不甩 Markdown。
_BOT_SYSTEM_PROMPT = (
    "你是 MoRay，正在通过即时通讯软件与用户对话。这是聊天窗口，不是文档编辑器："
    "回复要短、口语化，通常 1-3 句；不要输出 Markdown 表格、代码块、标题；"
    "除非用户明确要求，不要长篇解释。"
)
#: LLM 不可用时的兜底回复（宁可给一句人话，也不让用户以为消息被吞了）。
_FALLBACK_REPLY = "（我这边模型暂时没接上，稍后再试一次。）"

#: 全局队列与去重表。队列惰性创建：import 时可能还没有运行中的事件循环。
_queue: asyncio.Queue | None = None
_seen: "collections.OrderedDict[str, float]" = collections.OrderedDict()
_seen_lock = threading.Lock()


# ---------------- 归一化 / 防重 / 入队 ----------------

def _verify_signature(raw_body: bytes, header_value: str, secret: str) -> bool:
    """校验 X-Signature：sha1=<hex>（也兼容不带前缀的裸 hex，大小写不敏感）。

    签名对象必须是**原始请求字节**：NapCat 按发送时的 body 逐字节计算，
    任何 json.loads→json.dumps 往返都会改空格/键序/转义，签名必然对不上。

    算法说明：SHA-1 是 NapCat OneBot HTTP 反向推送的既定协议，这里用于
    HMAC 消息认证（不是抗碰撞哈希）；算法由上游决定，不能自行升级成 SHA-256，
    否则与 NapCat 的签名对不上。遇到非 sha1 前缀一律拒绝（fail-close，
    不静默降级到其它算法）。
    """
    got = str(header_value or "").strip()
    if not got:
        return False
    if "=" in got:
        algo, _, digest = got.partition("=")
        if algo.strip().lower() != "sha1":
            return False  # 本阶段只认 HMAC-SHA1（NapCat 现行协议）
        got = digest.strip()
    if not got:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, got.lower())


async def _auth_ok(request: Request) -> bool:
    """入站鉴权：X-Signature（NapCat HMAC-SHA1）优先，Authorization: Bearer 兜底。

    - X-Signature 存在 → 只认签名，签名错直接 401（不再回落 Bearer，避免
      "带了错误签名却靠另一个头混进来"的歧义）；
    - X-Signature 不存在 → 走标准 Bearer（兼容通用 OneBot 实现/手工调试）；
    - BOT_TOKEN 为空 → 永远不通过（fail-close）。

    必须用 await request.body() 的原始字节验签；Starlette 会缓存 body，
    后续 request.json() 复用同一份字节，不会二次读取或改变内容。
    """
    secret = str(config.BOT_TOKEN or "").strip()
    if not secret:
        return False

    signature = request.headers.get("x-signature")
    if signature is not None:
        try:
            raw_body = await request.body()
        except Exception:  # noqa: BLE001 - 读体失败按鉴权失败处理
            return False
        return _verify_signature(raw_body, signature, secret)

    got = str(request.headers.get("authorization") or "").strip()
    if not got.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(got[7:].strip(), secret)


def _strip_cq(raw: str) -> str:
    """字符串消息 → 纯文本：反转义 HTML → 剥 CQ 码 → 空白归一。"""
    text = html.unescape(str(raw or ""))
    text = _CQ_RE.sub("", text)
    return " ".join(text.split())


def _text_from_message(message) -> str:
    """OneBot message 字段 → 纯文本。

    两种合法形态：
    - 数组（段列表）：只取 type=text 的 data.text；image/face/at/reply 等本阶段一律丢弃；
    - 字符串（CQ 码格式）：整串反转义后剥 CQ 码。
    本阶段只支持纯文本——图片/语音不做 OCR/ASR，避免静默吞掉非文本内容后装作理解了。
    """
    if isinstance(message, str):
        return _strip_cq(message)
    if not isinstance(message, list):
        return ""
    parts = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") != "text":
            continue
        data = seg.get("data")
        if isinstance(data, dict):
            parts.append(str(data.get("text") or ""))
    return _strip_cq("".join(parts))


def _normalize(ev: dict) -> dict | None:
    """OneBot 事件 → 内部任务。不是可处理的消息返回 None（调用方回 ignored）。"""
    if ev.get("post_type") != "message":
        return None
    scope = str(ev.get("message_type") or "").strip().lower()
    if scope not in ("group", "private"):
        return None  # channel/guild 等非 QQ 形态不在本阶段范围
    if str(ev.get("user_id") or "") == str(ev.get("self_id") or ""):
        return None  # 自己发的消息不回环
    chat_id = ev.get("group_id") if scope == "group" else ev.get("user_id")
    if chat_id in (None, ""):
        return None
    text = _text_from_message(ev.get("message"))
    if not text:
        # 段列表为空时退回 raw_message（部分实现只填 raw_message）
        raw = ev.get("raw_message")
        text = _strip_cq(raw) if isinstance(raw, str) else ""
    text = text[:_TEXT_MAX]
    if not text:
        return None  # 纯图片/纯表情：本阶段不回，避免幻觉式回答
    try:
        ts = int(ev.get("time") or time.time())
    except (TypeError, ValueError):
        ts = int(time.time())
    return {
        "scope": scope,
        "chat_id": str(chat_id),
        "user_id": str(ev.get("user_id") or ""),
        "message_id": str(ev.get("message_id") or ""),
        "text": text,
        "ts": ts,
    }


def _is_duplicate(message_id: str) -> bool:
    """是否已处理过该 message_id（命中则刷新 LRU 热度）。"""
    mid = str(message_id or "").strip()
    if not mid:
        return False  # 没有 message_id 就没法去重，放行（不因缺字段吞消息）
    with _seen_lock:
        if mid in _seen:
            _seen.move_to_end(mid)
            return True
    return False


def _mark_seen(message_id: str) -> None:
    """入队成功后记账（先入队再记账：队列满被拒的消息不该占用去重位，
    否则 OneBot 重投那条消息会被误判 duplicate 永久丢掉）。"""
    mid = str(message_id or "").strip()
    if not mid:
        return
    with _seen_lock:
        _seen[mid] = time.time()
        while len(_seen) > _LRU_MAX:
            _seen.popitem(last=False)


def _get_queue() -> asyncio.Queue:
    """惰性建队列（asyncio.Queue 不绑循环，但延迟到首个 async 调用最稳）。"""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    return _queue


# ---------------- 入站路由 ----------------

@router.post("/api/bot/webhook")
async def bot_webhook(request: Request):
    """OneBot 事件入口：验签 → 归一化 → 去重 → 入队 → 立即返回。

    返回契约：
      200 {"ok": true, "queued": true}
      200 {"ok": true, "duplicate": true}        （message_id 重复）
      200 {"ok": true, "ignored": true}          （非消息事件/纯非文本/自己发的）
      200 {"ok": true, "queued": false, "reason": "queue_full"}
      400 bad_json / 401 unauthorized / 503 bot_disabled
    """
    if not config.BOT_ENABLED:
        return JSONResponse(status_code=503, content={
            "ok": False, "code": "bot_disabled",
            "message": "外部触达未启用（MORAY_BOT_ENABLED=false）。"})
    if not await _auth_ok(request):
        logger.warning("[bot] 入站拒绝：X-Signature / Authorization 缺失或不匹配")
        return JSONResponse(status_code=401, content={
            "ok": False, "code": "unauthorized",
            "message": "X-Signature 或 Bearer token 缺失/不匹配。"})

    try:
        ev = await request.json()
    except Exception:  # noqa: BLE001 - 坏 JSON 转契约错误，不上抛
        return JSONResponse(status_code=400, content={
            "ok": False, "code": "bad_json", "message": "请求体不是合法 JSON。"})
    if not isinstance(ev, dict):
        return JSONResponse(status_code=400, content={
            "ok": False, "code": "bad_json", "message": "请求体必须是 JSON 对象。"})

    job = _normalize(ev)
    if job is None:
        return {"ok": True, "ignored": True}
    if _is_duplicate(job["message_id"]):
        logger.warning("[bot] 重复消息拦截 message_id=%s", job["message_id"])
        return {"ok": True, "duplicate": True}

    try:
        _get_queue().put_nowait(job)
    except asyncio.QueueFull:
        logger.warning("[bot] 队列已满(%d)，拒绝入队 message_id=%s",
                       _QUEUE_MAX, job["message_id"])
        return {"ok": True, "queued": False, "reason": "queue_full"}
    _mark_seen(job["message_id"])
    logger.warning("[bot] 入队 scope=%s chat=%s message_id=%s chars=%d",
                   job["scope"], job["chat_id"], job["message_id"], len(job["text"]))
    return {"ok": True, "queued": True}


# ---------------- 后台 worker ----------------

def _ensure_conversation(conv_id: str, scope: str, chat_id: str) -> None:
    """会话行不存在才建（upsert_conversation 是全字段覆盖，不能当 INSERT OR IGNORE 用）。

    与 main.py:97-109 的通知对话同款纪律：用户可能改过标题，存在就绝不覆盖。
    """
    if crud.get_conversation(conv_id):
        return
    crud.upsert_conversation({
        "id": conv_id,
        "title": ("QQ 群 " if scope == "group" else "QQ 私聊 ") + chat_id,
        "model": "qq-bot",
        "system_prompt": "",
        "pinned": 0,
    })
    logger.warning("[bot] 新建 IM 会话 conv=%s", conv_id)


def _persist(conv_id: str, role: str, content: str, model: str) -> None:
    """写一条消息到 UI 主库。id 生成规则与 main.py:120 同款（32 位 hex）。"""
    crud.upsert_message({
        "id": uuid.uuid4().hex[:32],
        "conversationId": conv_id,  # 必须 camelCase（crud.upsert_message 契约）
        "role": role,
        "content": content,
        "reasoning": "",
        "model": model,
    })


async def _ask_llm(job: dict) -> tuple[str, str]:
    """回环调 /api/llm/chat（stream=false）→ (回复文本, 模型名)。失败给兜底回复。

    trust_env=False：显式绕开系统/环境变量代理，保证 127.0.0.1 直连本进程。
    """
    payload = {
        "messages": [
            {"role": "system", "content": _BOT_SYSTEM_PROMPT},
            {"role": "user", "content": job["text"]},
        ],
        # [硬约束] 必须显式 false：llm_proxy.py:904 默认 True=SSE
        "stream": False,
        # [硬约束] 独立会话桶：不要和浏览器共用 "default"（llm_proxy.py:745-766）
        "session_id": "bot:qq:%s:%s" % (job["scope"], job["chat_id"]),
        # IM 场景禁思考链：默认 thinking 会让 9B 在 8G 卡上单条聊数分钟，聊天也不需要
        # 推理过程。实测 think:false 被 Ollama 忽略，reasoning_effort="none" 才真正关
        # （2026-10-16 探针结论；llm_proxy 只对 ollama/custom_27b 透传该字段）。
        "think": False,
        "reasoning_effort": "none",
    }
    try:
        async with httpx.AsyncClient(timeout=_LLM_TIMEOUT_S, trust_env=False) as client:
            resp = await client.post(_LLM_URL, json=payload)
        data = resp.json()
    except Exception as e:  # noqa: BLE001 - 回环失败按兜底回复处理，worker 不崩
        logger.warning("[bot] LLM 回环调用失败 message_id=%s：%s: %s",
                       job["message_id"], type(e).__name__, str(e)[:160])
        return _FALLBACK_REPLY, "bot-fallback"
    if not isinstance(data, dict) or not data.get("ok"):
        code = data.get("code") if isinstance(data, dict) else ""
        msg = data.get("message") if isinstance(data, dict) else ""
        logger.warning("[bot] LLM 返回失败 message_id=%s HTTP=%s code=%s msg=%s",
                       job["message_id"], resp.status_code, code, str(msg)[:120])
        return _FALLBACK_REPLY, "bot-fallback"
    content = str(data.get("content") or "").strip()
    if not content:
        logger.warning("[bot] LLM 返回空正文 message_id=%s", job["message_id"])
        return _FALLBACK_REPLY, "bot-fallback"
    return content, str(data.get("model") or "moray")


async def _handle_job(job: dict) -> None:
    """单个任务：落用户消息 → 调 LLM → 落回复 → 回推 OneBot。"""
    scope, chat_id = job["scope"], job["chat_id"]
    conv_id = "bot:qq:%s:%s" % (scope, chat_id)
    _ensure_conversation(conv_id, scope, chat_id)
    _persist(conv_id, "user", job["text"], "qq-inbound")

    reply, model = await _ask_llm(job)
    # 先落库再回推：即使 3000 没起，UI 里也能看到这轮对话（投递失败不等于回复丢失）
    _persist(conv_id, "assistant", reply, model)
    logger.warning("[bot] 回复已落库 conv=%s model=%s chars=%d message_id=%s",
                   conv_id, model, len(reply), job["message_id"])

    delivered = await bot_client.send_text(scope, chat_id, reply)
    logger.warning("[bot] 任务完成 message_id=%s delivered=%s queue=%d",
                   job["message_id"], delivered, _get_queue().qsize())


async def bot_worker() -> None:
    """单并发消费循环（本地 8G 显存只能一次一个 9B 推理，刻意不并发）。

    CancelledError 照常上抛（继承 BaseException，不被 except Exception 捕获），
    main.py 的 lifespan 收尾可以干净 cancel；单任务异常只 warning，循环不死。
    """
    queue = _get_queue()
    logger.warning("[bot] worker 已启动 queue_max=%d concurrency=1 llm=%s",
                   _QUEUE_MAX, _LLM_URL)
    while True:
        job = await queue.get()
        try:
            await _handle_job(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - 单任务失败不拖死整个 worker
            logger.warning("[bot] 任务异常 message_id=%s：%s: %s",
                           job.get("message_id"), type(e).__name__, str(e)[:200])
        finally:
            queue.task_done()
