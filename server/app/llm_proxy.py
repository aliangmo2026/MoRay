"""云端 LLM API 代理（里程碑 M2）

把前端的 OpenAI 兼容请求转发到后端 .env 配置的云端地址（DeepSeek 等兼容协议），
API key 只存在 server/.env，绝不进入浏览器。

安全约束：
- 转发目标 base_url 只来自后端配置（server/.env / 环境变量），前端请求体指定的一律忽略 → 防 SSRF/开放代理；
- 只监听 127.0.0.1（config.HOST）；CORS 沿用现有正则；
- 上游错误转 {ok:false, code, message} JSON，不泄漏堆栈/请求头/key；
- 日志打印 key 最多掩码 sk-***后4位。
"""
import asyncio
import json
import logging
import os
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config
from . import cloud_budget_guard

router = APIRouter()
_logger = logging.getLogger("moray.llm")

# 连接/读取/写入/池化超时（读取放宽到 300s 支持长思考流式）
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)

# 单 session LLM 调用熔断计数：纯内存 dict，不落库、不跨重启（重启即清零）。
# key = session_id（前端请求体 session_id 字段或 X-Session-Id 头，缺失归入 "default"）。
_session_calls: dict[str, int] = {}

# [Day24.11] 按天重置：跨自然日整表清零（惰性检查，无定时器 / 无后台任务 / 无新存储）。
_budget_day: str = ""

# [Day24.11] "default" 桶告警节流：每进程只提示一次，不刷屏
_default_bucket_warned: bool = False

# [批次二·任务E] 27B 冷启动预热提示：每进程只吼一次。用 WARNING 级（本仓 root logger
# 无 handler，INFO 会被静默丢弃——startup_budget_note 同一教训）；仅记日志，不碰响应体。
_warmup_hint_logged: bool = False


def _roll_budget_day() -> None:
    """[Day24.11] 跨自然日时清零全部会话桶（在计数前调用）。

    「重启即清零」是刻意的设计取舍（与 engines/local_adapter._tasks 同一惯例），但**只有重启能清零**
    会让长跑进程的桶只增不减：Day24.10 事故里 default 桶累到 104/30 之后，用户唯一的解药就是重启 Moray。
    按天清零把这条死路堵上；熔断器防的是秒级失控循环，跨日不构成绕过，所以不削弱保护力。
    惰性实现：不做定时任务，每天首个请求顺手比对日期。"""
    global _budget_day
    today = time.strftime("%Y-%m-%d")
    if _budget_day == today:
        return
    previous, _budget_day = _budget_day, today
    if not previous:
        return  # 进程内首次调用：没有"上一日"可跨越
    n, total = len(_session_calls), sum(_session_calls.values())
    _session_calls.clear()
    _logger.warning("[budget] 跨日重置（%s → %s）：已清零 %d 个会话桶、共 %d 次计数",
                    previous, today, n, total)


def _warn_default_bucket() -> None:
    """[Day24.11] 请求没带 session_id 时告警一次（每进程一次）。

    这正是 Day24.10 事故的充要条件：任何不带 id 的调用方都会与其它调用方共享同一个只增不减的桶，
    彼此互相消耗额度。只吼一次不刷屏；重启后若仍有人不带 id，会再次提醒。"""
    global _default_bucket_warned
    if _default_bucket_warned:
        return
    _default_bucket_warned = True
    _logger.warning("[budget] 有请求未携带 session_id，已计入共享的 default 桶：多个互不相关的调用方"
                    "会互相消耗同一份额度（上限 %d 次/会话/天）。前端请求体带 session_id、"
                    "或调用方带 X-Session-Id 头即可按会话隔离。", config.MAX_LLM_CALLS_PER_SESSION)


def startup_budget_note() -> None:
    """[Day24.11] 启动时报告熔断计数状态（由 main.py 的 lifespan 调用）。

    如实说明：计数是纯内存 dict，"重启即清零"是刻意取舍，因此新进程里**残留恒为 0**——这里不伪造
    一个非 0 数字。历史事故（Day24.10：74 次被拒、最深 104/30）的根因不是残留，而是**调用方不带
    session_id 全挤进 default 桶**；真正的守卫是 _warn_default_bucket()。

    级别用 WARNING 是刻意的：本仓没有任何 logging 配置（root logger 无 handler），只有 WARNING 及以上
    才会经 logging.lastResort 打到控制台，INFO 会被静默丢弃（main.py 现有的启动 INFO 就看不到）。
    宁可级别偏重，也不能让这条提示不可见。"""
    global _default_bucket_warned
    residue = sum(_session_calls.values())
    if residue:
        _logger.warning("[budget] 检测到 default 桶残留 %d 次计数，已清零（进程内计数，共 %d 个会话桶）",
                        residue, len(_session_calls))
        _session_calls.clear()
    _default_bucket_warned = False
    _logger.warning("[budget] 熔断计数已就绪：default 桶残留 0 次 · 上限 %d 次/会话/天 · 按会话隔离"
                    "（请求体 session_id / X-Session-Id 头）· 跨自然日自动清零",
                    config.MAX_LLM_CALLS_PER_SESSION)
    # [2026-09-30c 任务二] 云端金额预算闸门当日状态（只读报告；按校准 3：读不到=如实报告，不抛异常
    # ——fail-closed 只用于 acquire_explicit / acquire_fallback 路径）。
    try:
        _cloud_state = cloud_budget_guard.get_daily_state()
    except cloud_budget_guard.BudgetStateUnavailable as e:
        _logger.warning("[budget] 云端金额预算状态不可读（MORAY_BUDGET_DB=%s）：%s",
                        config.MORAY_BUDGET_DB, e)
    else:
        _logger.warning("[budget] 云端金额预算当日状态：date=%s total_usd=%.4f/%.4f call_count=%d/%d "
                        "tripped=%s（三层开关 ENABLE_CLOUD_API=%s ENABLE_CLOUD_FALLBACK=%s）",
                        _cloud_state["date"], _cloud_state["total_usd"], config.DAILY_BUDGET_USD,
                        _cloud_state["call_count"], config.DAILY_MAX_CALLS, _cloud_state["tripped"],
                        config.ENABLE_CLOUD_API, config.ENABLE_CLOUD_FALLBACK)


def _audit_budget_exceeded(session_id: str, count: int, limit: int):
    """超限事件写入既有审计机制（agent_tools._audit → agent_tool_log 表），
    记录 type=budget_exceeded。懒加载导入避免拖慢正常请求路径；
    审计写入失败只 warning，绝不影响熔断响应的返回。"""
    try:
        from . import agent_tools
        agent_tools._audit("budget_exceeded", {"session_id": session_id},
                           False, "rejected", 0,
                           f"session {session_id} exceeded: {count}/{limit}",
                           source="llm_proxy")
    except Exception as e:  # noqa: BLE001 - 审计失败不影响熔断本身
        _logger.warning("budget audit write failed: %s", e)

#: 上游网关**真正接受**的模型名白名单（默认值见 config.DEFAULT_LLM_MODELS）。
#
# 前端是在**本机模型列表**里挑默认模型的（本地 Ollama 的 qwen2.5:7b 等，见前端
# ``agentRecommendedModel``），这些名字原样打到云端网关会直接 400，实测报文：
# ``The supported API model names are deepseek-flash, deepseek-v4-pro,
# but you passed qwen2.5:7b.`` —— 主对话整条链路因此不可用。
# 所以这里做一次兜底改写：不在名单里的名字一律回落到后端配置的模型（MORAY_LLM_MODEL）。
#
# 名单本身**不硬编码**：换服务商时改 server/.env 的 ``MORAY_LLM_MODELS``（逗号分隔）即可，
# 不用动代码；留空则用 config 里的内置默认（含 deepseek-chat / deepseek-reasoner
# 两个实测被网关接受的别名）。
_UPSTREAM_MODELS = tuple(config.LLM_MODELS)

# ===== 本地上游（[2026-09-30 本地优先强制] 默认 = Ollama 11434） =====
# 背景：此前本地上游是 8080 端口的独立 Bonsai2 服务（Ternary-Bonsai-2-27B-PQ2_0），
# 且"默认路由云端 + 本地失败自动降级云端"导致偷偷调用 DeepSeek。
# 现在 LLM_PROVIDER 默认 ollama（config.LLM_PROVIDER）：本地上游恒为 Ollama，
# 默认模型 qwen3.5:9b（config.OLLAMA_MODEL）；云端仅 ENABLE_CLOUD_API=true 时可用。
_LOCAL_BASE_URL = config.OLLAMA_BASE_URL.rstrip("/") + "/v1"
_LOCAL_API_KEY = "ollama"          # Ollama 不校验 key，OpenAI 兼容口任意占位
_LOCAL_MODEL = config.OLLAMA_MODEL
_LOCAL_HEALTH_URL = config.OLLAMA_BASE_URL.rstrip("/") + "/api/version"
_LOCAL_RETRY_MAX = 2               # 本地失败重试上限（重试仍是本地）；本地失败绝不降级云端（批次二·5 任务1 红线）


def _cloud_gate() -> str | None:
    """[2026-09-30 云端熔断] ENABLE_CLOUD_API 未显式置 true → 拒绝任何云端调用。

    返回 None = 云端放行；返回非空字符串 = 不可用的原因文案（调用方直接拒绝）。
    这是从源头断掉"偷跑云端"的强制闸门：不依赖调用方自觉，也不依赖路由偶然性。
    """
    if config.ENABLE_CLOUD_API:
        return None
    return ("云端 LLM 已被熔断（ENABLE_CLOUD_API 未设为 true）。MoRay 本地优先："
            "请改用本地 Ollama（默认模型 %s，地址 %s）。确需云端时，在 server/.env "
            "设置 ENABLE_CLOUD_API=true 并重启后端。" % (config.OLLAMA_MODEL, config.OLLAMA_BASE_URL))


#: [2026-10-01 Z-3] X-Moray-Provider 头的允许值：只认这三个，白名单外（含空/任意值）一律忽略。
_HEADER_PROVIDER_ALLOWLIST = ("cloud", "ollama", "custom_27b")


def _resolve_body_provider(request: Request, body: dict) -> tuple[str, str]:
    """[2026-10-01 Z-3] provider 请求级来源决策：X-Moray-Provider 头 > body.provider > 全局。

    背景：ZCode CLI 是 OpenAI 兼容客户端，无法在请求体里塞 provider 字段，只能带
    自定义 HTTP 头（其 provider config 支持 api.headers）。白名单外的头值一律回落
    body.provider；两者皆空回落全局 config.LLM_PROVIDER（此前由调用方拼兜底）。

    返回 (provider, source)，source = "header" | "body" | "global"（[2026-10-01 Z-4]
    新增第二元）：ENABLE_ZCODE_CLOUD 二次保险只对 source=="header" 生效——body 显式
    provider=cloud 是前端手动路径，不受该开关约束（test_12 / test_14 钉死差异）。

    安全约束：header 只改 provider **来源**，不改 _cloud_gate() 的 ENABLE_CLOUD_API
    检查——provider=cloud（无论来自头还是体）仍要过一级预检闸门 + 上游选择段闸门 +
    acquire_explicit 预算预扣，header 无法绕过云端熔断。

    /api/llm/chat 的两处消费点（一级预检 + 上游选择）共用本函数，保证两处 provider
    值恒同源（此前是两行重复字面量，本次一并收口）。"""
    header_provider = str(request.headers.get("X-Moray-Provider") or "").strip().lower()
    if header_provider in _HEADER_PROVIDER_ALLOWLIST:
        return header_provider, "header"
    body_provider = str(body.get("provider") or "").strip().lower()
    if body_provider:
        return body_provider, "body"
    return config.LLM_PROVIDER, "global"


async def _local_health_ok() -> bool:
    """快速检查本地 Ollama 是否存活（2秒超时）。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
            r = await c.get(_LOCAL_HEALTH_URL)
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _model_hint() -> str:
    """模型相关错误时给用户的可执行提示（不含任何本机路径/服务商私有信息）"""
    names = "、".join(config.LLM_MODELS) or "（未配置）"
    fallback = config.LLM_MODEL or "（未设置 MORAY_LLM_MODEL）"
    return ("上游网关不接受该模型名。可选模型：" + names +
            "；也可把 server/.env 的 MORAY_LLM_MODEL 设为其中之一作为兜底。"
            "当前兜底模型：" + fallback)


def _mask_key(key: str) -> str:
    """日志掩码：sk-***后4位（绝不打印完整 key）"""
    if not key:
        return ""
    return (key[:3] + "***" + key[-4:]) if len(key) > 7 else "***"


def _upstream_code(status: int) -> str:
    return {
        401: "invalid_key",
        402: "billing_error",
        429: "rate_limited",
        500: "upstream_error",
        502: "upstream_error",
        503: "upstream_error",
        504: "upstream_timeout",
    }.get(status, "upstream_error")


def _err(status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


#: [2026-09-30d 任务二点五] 云端每百万 token 美元单价（成本价目表）。
#: 后端当前没有价目表（tokens→美元定价在前端 CostEngine）→ None = 换算不可用，
#: settle 一律按 0.0 全额退款（任务书口径：宁可少记，不可多扣）。
_CLOUD_COST_PER_MTOKEN_USD: float | None = None


def _cloud_actual_cost_usd(data: dict) -> float:
    """上游 OpenAI 兼容响应 → 实际美元成本：usage.total_tokens × 价目表单价。

    usage 缺失/类型异常/价目表缺失（None）→ 0.0 全额退款（宁可少记，不可多扣）。
    """
    try:
        usage = (data or {}).get("usage") or {}
        total_tokens = float(usage.get("total_tokens") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    if not total_tokens >= 0:
        return 0.0
    price = _CLOUD_COST_PER_MTOKEN_USD
    if price is None:
        return 0.0
    return total_tokens / 1e6 * float(price)


@router.post("/api/llm/chat")
async def llm_chat(request: Request):
    # ---- 1. 入参解析（[2026-09-30d 任务二点五] 前移到配置检查之前：检查需读 body.provider；
    #         只取白名单字段；base_url 等一律忽略，防 SSRF）----
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")

    # ---- 2. 配置检查（[2026-09-30 本地优先强制] provider=ollama/custom_27b 不要求云端 key；
    #         provider=cloud 需云端 key，且必须过云端熔断闸门）----
    # [2026-09-30d 任务二点五] 补一级检查缺口：provider 生效值 = body.provider > config.LLM_PROVIDER
    # （与下方上游选择段同源）。既有用例钉死了两种来源的**不同优先级**（不可统一）：
    #   * body 显式 provider="cloud"：先闸门后配置（test_openai_compat.test_c3 钉死闸门错优先），
    #     过闸后补 LLM_BASE_URL/API_KEY 空值检查——此前 body 指 cloud 会绕过配置检查直达上游段；
    #   * config.LLM_PROVIDER="cloud"：维持既有 else 分支顺序（配置错优先，test_f4 钉死）。
    # 预检失败在任何 session 计数/预算预扣之前返回，拒绝不消耗额度。
    # [2026-10-01 Z-3] header 判定优先于 body.provider 与全局配置（见 _resolve_body_provider）：
    # X-Moray-Provider 在白名单内时直接生效，否则回落 body.provider。两处 provider
    # 消费点（此处一级预检 + 下方上游选择）恒同源。
    body_provider, provider_source = _resolve_body_provider(request, body)
    effective_provider = body_provider or config.LLM_PROVIDER
    if effective_provider == "cloud" and provider_source in ("header", "body"):
        _gate_err_early = _cloud_gate()  # 幂等；下方 cloud 分支的原有调用点原样保留
        if _gate_err_early:
            return _err(503, "cloud_api_disabled", _gate_err_early)
        # [2026-10-01 Z-4] header 触发的云端（ZCode 链路）二次确认开关：总闸放行后还要
        # ENABLE_ZCODE_CLOUD=true。body 显式 provider=cloud 是前端手动路径，不受此约束
        # （test_12 / test_14 钉死这对差异）。顺序刻意在总闸之后：闸门关时仍报
        # cloud_api_disabled（test_08 语义不变）。
        if provider_source == "header" and not config.ENABLE_ZCODE_CLOUD:
            return _err(503, "cloud_zcode_disabled",
                        "ZCode 云端调用已被禁用（ENABLE_ZCODE_CLOUD 未设为 true）。"
                        "如需 ZCode 走云端，请在 server/.env 设置 ENABLE_ZCODE_CLOUD=true "
                        "并重启后端。")
        if not config.LLM_BASE_URL or not config.LLM_API_KEY:
            return _err(503, "cloud_not_configured", "云端未配置：LLM_BASE_URL 或 LLM_API_KEY 为空")
    if config.LLM_PROVIDER == "ollama":
        if not config.OLLAMA_BASE_URL:
            return _err(503, "local_not_configured",
                        "服务端未配置本地 Ollama 地址（MORAY_OLLAMA_BASE_URL，默认 "
                        "http://localhost:11434）。")
    elif config.LLM_PROVIDER == "custom_27b":
        if not config.LOCAL_27B_BASE_URL or not config.LOCAL_27B_MODEL_NAME:
            return _err(503, "local_27b_not_configured",
                        "服务端未配置 27B 本地服务（MORAY_27B_BASE_URL / MORAY_27B_MODEL_NAME，"
                        "默认 http://127.0.0.1:8080/v1 + bonsai2-27b）。")
    else:
        if not config.LLM_BASE_URL or not config.LLM_API_KEY:
            return _err(503, "cloud_not_configured",
                        "服务端未配置云端 LLM。二选一：① 在前端「设置 → OpenAI 兼容 API」"
                        "填写服务商接口地址与 API Key（或改用本机 Ollama）；"
                        "② 复制 server/.env.example 为 server/.env，填写 MORAY_LLM_BASE_URL / "
                        "MORAY_LLM_API_KEY 后重启后端（Key 不进浏览器）。")
        _gate_err = _cloud_gate()
        if _gate_err:
            return _err(503, "cloud_api_disabled", _gate_err)

    # ---- 2.1 单 session LLM 调用次数熔断（内存计数，超限 429）----
    # session_id 优先取请求体字段，其次 X-Session-Id 头，都没有归入 "default"。
    # 先计数再判断：第 limit+1 次请求即被拒；被拒请求也继续累计（日志能反映超限深度）。
    session_id = str(body.get("session_id") or request.headers.get("X-Session-Id") or "").strip() or "default"
    limit = config.MAX_LLM_CALLS_PER_SESSION
    _roll_budget_day()  # [Day24.11] 跨自然日先清零，避免"只能重启才能解锁"
    if session_id == "default":
        _warn_default_bucket()  # [Day24.11] 不带 session_id 的调用方共用一个桶 → 告警一次
    _session_calls[session_id] = _session_calls.get(session_id, 0) + 1
    count = _session_calls[session_id]
    if count > limit:
        _logger.warning("[budget] session %s exceeded: %d/%d", session_id, count, limit)
        _audit_budget_exceeded(session_id, count, limit)
        return JSONResponse(status_code=429, content={
            "ok": False,
            "error": "session_budget_exceeded",
            "msg": f"本次会话已达到 LLM 调用次数上限（{limit}次）",
            # [Day24.11b] 人话字段：前端代理分支与 Argus _moray_error_text 都优先读
            # message（此前两键皆空，用户只看到兜底文案/原始 JSON）。error/msg 原值
            # 保留为 Day1 契约（向后兼容；_map_legacy_error_body 与旧测试不受影响）。
            "message": f"本次会话已达调用次数上限（{limit} 次），请新建对话后继续",
        })

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _err(400, "bad_messages", "messages 必须是非空数组。")

    # [桥三事实卡 2026-10-15] system 层注入（调 LLM 之前；非对话流，user/assistant
    # 轮零改动）：每次请求前置拼接确定性事实卡——被问及自身时只复述库内事实、禁外推。
    # render_self_card 为纯函数（tests/test_self_card.py 钉死）；collect 单源失败自降级。
    # [2026-10-16 总闸] MORAY_SELF_CARD 默认开；设 0/false/no/off = 完全跳过注入，
    # 供"无卡对照重跑"等 A/B 场景（进程 env 优先于 .env，load_dotenv 不覆盖既有 env）。
    if os.environ.get("MORAY_SELF_CARD", "1").strip().lower() not in ("0", "false", "no", "off"):
        from .self_card import collect_db_state, render_self_card

        _card_state = collect_db_state()
        _card_text = render_self_card(_card_state)
        _card_marker = "[系统事实卡·岗位①] 仅复述，禁外推。\n\n"
        _sys_msg = next((m for m in messages if isinstance(m, dict)
                         and m.get("role") == "system"), None)
        if _sys_msg is not None and not isinstance(_sys_msg.get("content"), list):
            # str/缺省路径与 spec 草图逐字等价；list（多模态分片）不拼接防 TypeError，
            # 落到下方独立插入分支。
            _sys_msg["content"] = _card_marker + _card_text + "\n\n" + (_sys_msg.get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": _card_marker + _card_text})

    # 模型名解析（[2026-09-30 本地优先强制；2026-09-30b 支持 custom_27b]）：
    # provider=ollama → 直接透传请求模型名（Ollama 接受任意模型名），缺省回落 OLLAMA_MODEL；
    # provider=custom_27b → 透传，缺省回落 LOCAL_27B_MODEL_NAME；
    # provider=cloud → 维持原白名单兜底（云端网关只认 _UPSTREAM_MODELS）。
    requested = str(body.get("model") or "").strip()
    provider, provider_source = _resolve_body_provider(request, body)
    if provider == "ollama":
        model = requested or config.OLLAMA_MODEL
    elif provider == "custom_27b":
        model = requested or config.LOCAL_27B_MODEL_NAME
    elif requested in _UPSTREAM_MODELS:
        model = requested
    else:
        if requested:
            _logger.info("模型 %r 不在上游支持列表，回落到后端配置模型 %r",
                         requested, config.LLM_MODEL)
        model = str(config.LLM_MODEL or "").strip()
    if not model:
        return _err(400, "bad_model",
                    "未指定模型且服务端未配置 MORAY_LLM_MODEL。请在 server/.env 里设置"
                    "默认模型（取值见 server/.env.example），或在前端指定一个上游支持的模型名。")
    stream = body.get("stream", True) is not False

    payload: dict = {"model": model, "messages": messages, "stream": stream}
    if body.get("temperature") is not None:
        payload["temperature"] = float(body["temperature"])
    if body.get("max_tokens") is not None:
        payload["max_tokens"] = int(body["max_tokens"])
    # 推理参数：部分服务商（Ollama 兼容口等）支持；不支持的会自行忽略
    if body.get("think") is not None:
        payload["think"] = bool(body["think"])
    # [阶段0 本机 Agent] 工具调用协议透传：仅请求体显式携带时才透传（无 tools 的旧请求行为完全不变）
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        payload["tools"] = tools
    if body.get("tool_choice") is not None:
        payload["tool_choice"] = body["tool_choice"]
    # [2026-10-21 keep_alive 透传] 前端 ollama 请求统一走本代理后，keep_alive:'30m' 需后端接力
    # 下发，否则 Ollama 按默认 5 分钟卸载模型，间隔稍久的下一条消息要等 GGUF 重新加载。
    # 仅请求体显式携带且 provider 为本地两类时透传（云端网关无此概念，白名单外字段绝不下发）。
    keep_alive = body.get("keep_alive")
    if keep_alive and provider in ("ollama", "custom_27b"):
        payload["keep_alive"] = keep_alive
    # [2026-10-03 27B挂死修复] reasoning_effort 仅对 custom_27b 透传：llama.cpp chat API 原生
    # 字段，27B 服务端模板缺省 xhigh（实测热模型 5.3 t/s 下超长 think 是单轮数分钟的放大器），
    # 前端默认下发 low；模板只接受 low/medium/xhigh，其余值服务端 raise_exception。
    # [2026-10-16 桥三探针实测] ollama 分支同透传：qwen3.5 系在当前 runner 下 think:false
    # 被忽略（compat 与 native 皆隐性烧 thinking），实测 reasoning_effort="none" 才真正关
    # （60 token 预算 0.4s 干净出词）。仍只在请求体显式携带时透传——不带该字段的既有
    # 调用（前端/Ollama 路由默认流量）payload 零变化；云端分支维持不加。
    if provider in ("custom_27b", "ollama") and body.get("reasoning_effort") is not None:
        payload["reasoning_effort"] = str(body["reasoning_effort"])

    # ===== 上游选择（[2026-09-30 本地优先；2026-09-30b provider 三态：ollama / custom_27b / cloud） =====
    _cloud_permit = None  # 仅 provider=cloud 分支持有；_non_stream/_stream 闭包据此判断是否结算
    if provider == "ollama":
        # Ollama：先做存活预检；失败则本轮直接拒绝（绝不自动降级云端）
        if not await _local_health_ok():
            return _err(503, "local_upstream_error",
                        f"本地 Ollama 未就绪（{config.OLLAMA_BASE_URL}）。请确认 Ollama 已启动，"
                        f"或切换 provider=custom_27b 使用 27B 本地服务。")
        use_local = True
        upstream_url = _LOCAL_BASE_URL.rstrip("/") + "/chat/completions"
        upstream_headers = {
            "Authorization": "Bearer " + _LOCAL_API_KEY,
            "Content-Type": "application/json",
        }
        upstream_model = model
        _logger.info("[router] 本地路由 → Ollama %s (%s)", upstream_model, config.OLLAMA_BASE_URL)
    elif provider == "custom_27b":
        # 27B 独立服务：不做预检，直接请求；失败在下方失败段按红线给出明确错误（绝不降云端）
        global _warmup_hint_logged  # noqa: F824 - 模块级一次性开关
        if not _warmup_hint_logged:
            _warmup_hint_logged = True
            # [批次二·任务E] 冷启动提示走日志（首次请求需加载 GGUF，实测约 37 秒）
            _logger.warning("[27B] 模型预热中，请稍候（首次请求需加载本地 GGUF 模型，"
                            "约 30-40 秒；后续请求恢复常速）。")
        use_local = True
        upstream_url = config.LOCAL_27B_BASE_URL.rstrip("/") + "/chat/completions"
        upstream_headers = {
            "Content-Type": "application/json",
        }
        if config.LOCAL_27B_API_KEY:
            upstream_headers["Authorization"] = "Bearer " + config.LOCAL_27B_API_KEY
        # 未配置 key 时不带 Authorization：服务实测会 401，错误提示会如实说明（见降级段）
        upstream_model = model
        _logger.info("[router] 27B 路由 → %s (%s)", upstream_model, config.LOCAL_27B_BASE_URL)
    else:
        # 云端分支：先过熔断闸门（任何云端企图都必须放行确认）
        _gate_err = _cloud_gate()
        if _gate_err:
            return _err(503, "cloud_api_disabled", _gate_err)
        # [2026-10-01 Z-4] header 触发的云端在预检段已拦一次，此处同判是防御性二次确认
        # （与预检段同源同语义；body 显式 provider=cloud 不受此约束）。
        if provider_source == "header" and not config.ENABLE_ZCODE_CLOUD:
            return _err(503, "cloud_zcode_disabled",
                        "ZCode 云端调用已被禁用（ENABLE_ZCODE_CLOUD 未设为 true）。"
                        "如需 ZCode 走云端，请在 server/.env 设置 ENABLE_ZCODE_CLOUD=true "
                        "并重启后端。")
        # [2026-09-30c 任务二 + 2026-09-30d 任务二点五] 显式 provider=cloud 必须过金额预算闸门
        # （fail-closed 预扣；三层开关不引入自动 fallback，下方"本地失败绝不降级云端"未动）。
        # 预扣额 = min(调用方申报 est_cost_usd, PER_CALL_BUDGET_USD)；申报缺省/非法/负数/NaN
        # 一律回落 PER_CALL_BUDGET_USD（不可信输入不进账本）。
        try:
            _est_requested = float(body.get("est_cost_usd"))
        except (TypeError, ValueError):
            _est_requested = float(config.PER_CALL_BUDGET_USD)
        if not _est_requested >= 0:
            _est_requested = float(config.PER_CALL_BUDGET_USD)
        try:
            _cloud_permit = cloud_budget_guard.acquire_explicit(
                trace_id=uuid.uuid4().hex,
                est_cost_usd=min(_est_requested, float(config.PER_CALL_BUDGET_USD)))
        except cloud_budget_guard.CloudBudgetError as e:
            _logger.warning("[router] 云端预算闸门拒绝：%s", e)
            if isinstance(e, cloud_budget_guard.BudgetExceeded):
                return _err(429, "cloud_budget_exceeded", str(e))
            if isinstance(e, cloud_budget_guard.CloudDisabled):
                return _err(503, "cloud_api_disabled", str(e))
            if isinstance(e, cloud_budget_guard.CloudNotForFallback):
                return _err(503, "cloud_not_for_fallback", str(e))
            return _err(503, "cloud_budget_state_unavailable", str(e))
        _logger.info("[router] 云端预算预扣 permit=%s est=%.4f",
                     _cloud_permit.permit_id, _cloud_permit.reserved_usd)
        use_local = False
        upstream_url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
        upstream_headers = {
            "Authorization": "Bearer " + config.LLM_API_KEY,
            "Content-Type": "application/json",
        }
        upstream_model = model
        _logger.info("[router] 云端路由 → %s (ENABLE_CLOUD_API=true)", config.LLM_BASE_URL)

    payload["model"] = upstream_model

    async def _do_request(url: str, headers: dict, payload: dict):
        """发一次请求，返回 (status_code, data_or_error_text)。"""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                    msg = detail.get("error", {}).get("message") or detail.get("message") or ""
                except Exception:  # noqa: BLE001
                    msg = resp.text[:200]
                return resp.status_code, None, msg
            return 200, resp.json(), None

    async def _non_stream():
        """非流式：[2026-09-30 本地优先强制] 本地失败只重试本地；云端降级必须过熔断闸门"""
        # 首次请求
        try:
            status, data, msg = await _do_request(upstream_url, upstream_headers, payload)
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            status, data, msg = 599, None, type(e).__name__

        # 本地失败 → 重试本地（上限 _LOCAL_RETRY_MAX 次），不碰云端
        attempts = 0
        while status >= 400 and use_local and attempts < _LOCAL_RETRY_MAX:
            attempts += 1
            _logger.warning("[router] 本地第%d次失败(status=%s), 重试...", attempts, status)
            try:
                status, data, msg = await _do_request(upstream_url, upstream_headers, payload)
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                status, data, msg = 599, None, type(e).__name__

        # 本地彻底失败 → [批次二·任务E + 批次二·5 任务1 红线] 本地 provider **无论熔断闸门状态
        # 都绝不降级云端**（本地失败静默切云端 = 偷偷计费 + 数据出本地机）；只如实报错：
        # custom_27b → "27B 服务未启动，请先启动独立服务"；ollama → "本地 Ollama 服务不可用"。
        if status >= 400 and use_local:
            if provider == "custom_27b":
                _logger.warning("[router] 27B 失败(%s)，按红线不降级云端", status)
                return _err(503, "local_upstream_error",
                            f"27B 服务未启动，请先启动独立服务。（{config.LOCAL_27B_BASE_URL}，模型 "
                            f"{config.LOCAL_27B_MODEL_NAME}；失败原因：{msg}。"
                            f"也可切换回 provider=ollama。）")
            _logger.warning("[router] Ollama 失败(%s)，按红线不降级云端", status)
            return _err(503, "local_upstream_error",
                        f"本地 Ollama 服务不可用（{config.OLLAMA_BASE_URL}，模型 {model}；"
                        f"失败原因：{msg}）。请确认 Ollama 已启动后重试。")

        if status >= 400:
            # [2026-10-01 Z-3] 云端失败回冲：本地 provider 在上方 use_local 分支已提前返回，
            # 能走到这里的 status>=400 必是云端（_cloud_permit 持有预扣）→ 全额回冲后再报错。
            # refund 异常只记日志，绝不吞掉原始上游错误响应。
            if _cloud_permit is not None:
                try:
                    cloud_budget_guard.refund(_cloud_permit,
                                              reason="upstream_error_status_%d" % status)
                except Exception as e:  # noqa: BLE001 - 账本旁路失败不影响错误响应
                    _logger.warning("[llm_proxy] refund 失败（不影响响应）：%s", e)
            if status == 400 and msg and "model" in msg.lower():
                msg = (msg + " ｜ " + _model_hint()).strip()
            return _err(502, _upstream_code(status),
                        f"上游错误（{status}）：{msg}".strip())

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        out = {
            "ok": True,
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning_content") or message.get("thinking") or "",
            "model": data.get("model") or model,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        # [阶段0 本机 Agent] 工具轮：上游返回 tool_calls 时原样透传（无 tools 的旧请求不含此键，行为不变）
        if message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        # [2026-09-30d 任务二点五] 结算：actual 从上游 usage 提取（无价目表 → 0.0 全额退款）；
        # 结算失败保留预扣计提（偏保守方向）并落 WARNING，绝不影响已成功的响应。
        if _cloud_permit is not None:
            try:
                cloud_budget_guard.settle(_cloud_permit, _cloud_actual_cost_usd(data))
            except Exception as e:  # noqa: BLE001 - 账本旁路失败不影响响应（同 _audit_budget_exceeded 惯例）
                _logger.warning("[router] 云端预算结算失败（保留预扣 %.4f）：%s: %s",
                                _cloud_permit.reserved_usd, type(e).__name__, e)
        return out

    async def _stream():
        """流式：SSE 逐块透传；本地连接失败自动降级云端"""
        # 首次尝试本地（如果路由到本地）
        attempt_url = upstream_url
        attempt_headers = upstream_headers
        attempt_payload = payload
        local_tried = not use_local

        client = httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            for _ in range(_LOCAL_RETRY_MAX + 2):  # 本地重试余量（批次二·5 后本地失败直接报错，不再切云端）
                req = client.build_request("POST", attempt_url, json=attempt_payload, headers=attempt_headers)
                try:
                    upstream = await client.send(req, stream=True)
                except (httpx.ConnectError, httpx.TimeoutException):
                    if not local_tried:
                        # [批次二·任务E + 批次二·5 任务1 红线] 本地 provider 连接失败/超时：
                        # 无论熔断闸门状态都绝不降级云端，如实报错。
                        if provider == "custom_27b":
                            _logger.warning("[router] 流式 27B 连接失败(%s)，按红线不降级云端",
                                            type(e).__name__)
                            msg_27b = (f"27B 服务未启动，请先启动独立服务。（{config.LOCAL_27B_BASE_URL}，"
                                       f"模型 {config.LOCAL_27B_MODEL_NAME}；失败原因：{type(e).__name__}。"
                                       f"也可切换回 provider=ollama。）")
                            yield f"data: {json.dumps({'ok': False, 'code': 'local_upstream_error', 'message': msg_27b})}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        _logger.warning("[router] 流式 Ollama 连接失败(%s)，按红线不降级云端",
                                        type(e).__name__)
                        msg_local = (f"本地 Ollama 服务不可用（{config.OLLAMA_BASE_URL}，模型 {model}；"
                                     f"失败原因：{type(e).__name__}）。请确认 Ollama 已启动后重试。")
                        yield f"data: {json.dumps({'ok': False, 'code': 'local_upstream_error', 'message': msg_local})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    raise

                if upstream.status_code >= 400:
                    try:
                        raw = await upstream.aread()
                        detail = json.loads(raw).get("error", {}).get("message", "") or raw[:200]
                    except Exception:  # noqa: BLE001
                        detail = "上游错误"
                    await upstream.aclose()

                    if not local_tried:
                        if provider == "custom_27b":
                            # [批次二·任务E 红线] 27B HTTP 错误：无论熔断闸门状态，绝不降级云端
                            _logger.warning("[router] 流式 27B HTTP %s，按红线不降级云端",
                                            upstream.status_code)
                            msg_27b = (f"27B 服务未启动，请先启动独立服务。（{config.LOCAL_27B_BASE_URL}，"
                                       f"模型 {config.LOCAL_27B_MODEL_NAME}；失败原因：{detail}。"
                                       f"也可切换回 provider=ollama。）")
                            yield f"data: {json.dumps({'ok': False, 'code': 'local_upstream_error', 'message': msg_27b})}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        # [批次二·5 任务1 红线] Ollama HTTP 错误同样绝不降级云端
                        _logger.warning("[router] 流式 Ollama HTTP %s，按红线不降级云端",
                                        upstream.status_code)
                        msg_local = (f"本地 Ollama 服务不可用（{config.OLLAMA_BASE_URL}，模型 {model}；"
                                     f"失败原因：{detail}）。请确认 Ollama 已启动后重试。")
                        yield f"data: {json.dumps({'ok': False, 'code': 'local_upstream_error', 'message': msg_local})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    # [2026-10-01 Z-3] 流式云端 HTTP 错误回冲：local_tried=True（云端）才会
                    # 走到这里；本地 provider 在上方"绝不降级云端"分支已提前返回。
                    if _cloud_permit is not None:
                        try:
                            cloud_budget_guard.refund(
                                _cloud_permit,
                                reason="upstream_error_status_%d" % upstream.status_code)
                        except Exception as e:  # noqa: BLE001 - 账本旁路失败不影响错误事件
                            _logger.warning("[llm_proxy] refund 失败（不影响响应）：%s", e)
                    if upstream.status_code == 400 and "model" in str(detail).lower():
                        detail = str(detail) + " ｜ " + _model_hint()
                    yield f"data: {json.dumps({'ok': False, 'code': _upstream_code(upstream.status_code), 'message': f'上游错误（{upstream.status_code}）：{detail}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                # [2026-09-30d 任务二点五] 流式透传无法在不重序列化的前提下解析 usage →
                # 按任务书口径结算 0.0（全额退款，宁可少记）；结算失败保留预扣并落 WARNING。
                # 仅插入在成功透传之前，上方"绝不降级云端"各错误分支零改动。
                if _cloud_permit is not None:
                    try:
                        cloud_budget_guard.settle(_cloud_permit, 0.0)
                    except Exception as e:  # noqa: BLE001 - 账本旁路失败不影响流式透传
                        _logger.warning("[router] 云端预算结算失败（流式，保留预扣 %.4f）：%s: %s",
                                        _cloud_permit.reserved_usd, type(e).__name__, e)
                async for chunk in upstream.aiter_bytes():
                    yield chunk
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _logger.warning("llm proxy stream error: %s", type(e).__name__)
            # [2026-10-01 Z-3] 流式云端连接失败/传输中断回冲：本地 provider 的 _cloud_permit
            # 恒为 None（自然跳过）。若 settle 已成功而流随后中断，refund 的 settled 幂等
            # 保护会拒绝二次回冲并落 WARNING——这正是 refund 幂等语义存在的场景。
            if _cloud_permit is not None:
                try:
                    cloud_budget_guard.refund(_cloud_permit,
                                              reason="stream_error_%s" % type(e).__name__)
                except Exception as refund_err:  # noqa: BLE001 - 账本旁路失败不影响错误事件
                    _logger.warning("[llm_proxy] refund 失败（不影响响应）：%s", refund_err)
            yield f"data: {json.dumps({'ok': False, 'code': 'upstream_error', 'message': '流式传输中断：' + type(e).__name__})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await client.aclose()

    if stream:
        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return await _non_stream()


# ---------------------------------------------------------------------------
# OpenAI 兼容入口 POST /v1/chat/completions
#
# 背景：外部客户端（Claude Desktop / Cursor 等 OpenAI 兼容调用方，含本地 CLI 适配器）
# 只认 OpenAI wire format：往 baseUrl + /chat/completions 发标准请求、期望
# {choices, usage} / SSE delta chunk；而 /api/llm/chat 是自有协议
# （{ok, content, ...}），两侧协议面对不齐。
#
# 实现约束（红线：不改 /api/llm/chat 任何现有代码路径）→ 本路由是**纯协议
# 转换层**：重组请求体后直接内调 llm_chat，100% 复用其配置检查、预算熔断
# （MAX_LLM_CALLS_PER_SESSION）、模型名兜底（_UPSTREAM_MODELS）、字段白名单、
# httpx 上游调用、SSE 透传与审计，不重写任何上游请求逻辑。上游本身是
# OpenAI 兼容端点，流式透传 chunk 天然就是 chat.completion.chunk 形状，
# 只需把 llm_chat 自产的自有错误事件映射成 OpenAI {"error": {...}}。
#
# 已知边界：
# - 参数白名单沿用现有路由（model/messages/stream/temperature/max_tokens/
#   think/tools/tool_choice/session_id），其余 OpenAI 参数（n/stop/top_p 等）被丢弃；
# - OpenAI 规范 stream 缺省为 false（现有路由缺省 true），转换层补齐该语义差；
# - session_id 解析链不变：body.session_id > X-Session-Id 头 > "default"，
#   两路由共享同一 _session_calls 计数桶；
# - 自有协议不透出 finish_reason，按 tool_calls 有无推断（tool_calls/stop）；
# - 错误状态码语义保持（503/400/429/502/504），仅 body 转 OpenAI error 形状。
#
# 双路径注册（同一 handler，零重复）：
# - POST /v1/chat/completions —— OpenAI 标准路径（README"对接 Claude Desktop /
#   Cursor"及外部客户端默认行为）；
# - POST /chat/completions —— 别名。事实依据：部分 OpenAI 兼容客户端（本机 CLI
#   适配器实测）会在配置的 baseUrl 后**自行追加 /chat/completions**，而容器/代理
#   场景下 baseUrl 常按 http://<host>:8000 给出（不带 /v1）→ 实际打到的就是本别名。
#   没有它，这类配置会 404。
# ---------------------------------------------------------------------------

#: 自有错误 code → OpenAI error.type 映射（code 原值透传，MoRay 语义不丢）
_OAI_ERROR_TYPE = {
    "bad_json": "invalid_request_error",
    "bad_messages": "invalid_request_error",
    "bad_model": "invalid_request_error",
    "cloud_not_configured": "api_error",
    "invalid_key": "authentication_error",
    "billing_error": "insufficient_quota",
    "rate_limited": "rate_limit_error",
    "session_budget_exceeded": "rate_limit_error",
    "upstream_error": "api_error",
    "upstream_timeout": "api_error",
}


def _oai_error_body(code: str, message: str) -> dict:
    return {"error": {"message": message,
                      "type": _OAI_ERROR_TYPE.get(code, "api_error"),
                      "code": code}}


def _oai_error(status: int, code: str, message: str):
    """OpenAI 风格错误响应：{"error": {message, type, code}}（状态码保持 MoRay 语义）"""
    return JSONResponse(status_code=status, content=_oai_error_body(code, message))


def _map_legacy_error_body(data: dict) -> dict:
    """自有错误 body → OpenAI error body。兼容两种既有形状：
    {ok:false, code, message}（常规错误）与 {ok:false, error:"session_budget_exceeded", msg}（熔断）。"""
    if "error" in data and "msg" in data:  # 熔断形状（Day1 规格键名 error/msg）
        return _oai_error_body(str(data["error"]), str(data["msg"]))
    code = str(data.get("code") or "upstream_error")
    return _oai_error_body(code, str(data.get("message") or ""))


def _to_openai_completion(out: dict) -> dict:
    """llm_chat 非流式自有结构 {ok, content, reasoning, model, usage, tool_calls?}
    → 标准 OpenAI chat.completion。reasoning 还原为 DeepSeek 系客户端认的
    message.reasoning_content 扩展键（空则不带，与上游习惯一致）。"""
    message: dict = {"role": "assistant", "content": out.get("content") or ""}
    if out.get("reasoning"):
        message["reasoning_content"] = out["reasoning"]
    if out.get("tool_calls"):
        message["tool_calls"] = out["tool_calls"]
    usage = out.get("usage") or {}
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": out.get("model") or "",
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "tool_calls" if out.get("tool_calls") else "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


async def _oai_stream_wrapper(inner_iter):
    """流式映射层：上游透传 chunk（已是 OpenAI chunk 格式）逐字节原样转发；
    只把 llm_chat 自产的自有错误事件（data: {ok:false,...}）转成 OpenAI error 事件。
    自有错误事件恒为单条完整 `data: {...}\\n\\n` 原子 yield（见 _stream 实现），
    无需跨块重组；上游 chunk 即使被字节切分，解析失败也只会走原样透传分支。"""
    async for chunk in inner_iter:
        text = chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        stripped = text.strip()
        if stripped.startswith("data: {"):
            try:
                evt = json.loads(stripped[len("data:"):].strip())
            except Exception:  # noqa: BLE001 - 非完整 JSON（上游被切分的 chunk）→ 透传
                evt = None
            if isinstance(evt, dict) and evt.get("ok") is False:
                yield f"data: {json.dumps(_map_legacy_error_body(evt), ensure_ascii=False)}\n\n"
                continue
        yield chunk  # 原对象透传，字节级不动


@router.post("/v1/chat/completions")
@router.post("/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions 兼容入口（协议转换层，逻辑全量复用 llm_chat）"""
    # ---- 1. 请求体解析（OpenAI 客户端恒发 JSON 对象）----
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _oai_error(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _oai_error(400, "bad_json", "请求体必须是 JSON 对象。")

    # ---- 2. OpenAI 规范：stream 缺省为非流式（现有路由缺省 true，语义差在此补齐）----
    body = dict(body)
    body.setdefault("stream", False)

    # ---- 3. 内调 llm_chat：构造替换 body 的 receive 通道，scope 原样保留
    #         （X-Session-Id 头等全部可见），熔断/兜底/白名单/审计零重复实现 ----
    body_bytes = json.dumps(body).encode("utf-8")

    async def _receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    inner_result = await llm_chat(Request(dict(request.scope), _receive))

    # ---- 4a. 流式：外包一层 SSE 事件映射（透传内容不动，仅错误事件转形）----
    if isinstance(inner_result, StreamingResponse):
        return StreamingResponse(
            _oai_stream_wrapper(inner_result.body_iterator),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 4b. 错误 JSONResponse：状态码不变，body 转 OpenAI error 形状 ----
    if isinstance(inner_result, JSONResponse):
        try:
            data = json.loads(inner_result.body)
        except Exception:  # noqa: BLE001 - 防御：非预期 body 也要给 OpenAI 形状
            return JSONResponse(status_code=inner_result.status_code,
                                content=_oai_error_body("upstream_error", "内部错误。"))
        return JSONResponse(status_code=inner_result.status_code,
                            content=_map_legacy_error_body(data))

    # ---- 4c. 非流式成功（dict）→ 标准 OpenAI chat.completion ----
    return _to_openai_completion(inner_result)
