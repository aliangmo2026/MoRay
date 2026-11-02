# -*- coding: utf-8 -*-
"""外部触达 v1（2026-11-01）：OneBot v11 出站客户端。

职责边界（只做"把一段文本发回 IM"，不做消息解析、不碰队列）：
- send_text(scope, chat_id, text)：群聊 → /send_group_msg，私聊 → /send_private_msg；
- 失败重试最多 3 次（指数退避 1s / 2s），单次超时 10 秒；全部失败返回 False 并留 WARNING；
- build_greeting_text()：v2 主动问候的占位骨架，本阶段不实现（恒返回空串）。

安全边界（两层）：
- 请求 URL 不接受任何调用方传入，只来自 server/.env 的 MORAY_BOT_API_BASE（可信边界）；
- 即便如此仍钉死回环主机（127.0.0.1 / localhost / ::1）：即使有人把 .env 改成公网地址，
  startup() 也直接拒绝启动，避免本模块被当成任意主机的 HTTP 代理（SSRF 面归零）。
本机对接是本阶段的设计前提：OneBot 实现（NapCat / Lagrange）与 MoRay 同机部署。

日志纪律：本仓自定义 logger 的 INFO 不上控制台，需要被看见的节点一律 warning。
"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from . import config

logger = logging.getLogger("moray.bot_client")

#: 单次 HTTP 超时（秒）。OneBot 本地 HTTP API 是毫秒级响应，10s 只兜异常。
_TIMEOUT_S = 10.0
#: 失败重试次数（含首次）。总耗时上限约 3×10s + 3s 退避，不无限拖住 worker。
_MAX_ATTEMPTS = 3
#: 指数退避基数（秒）：第 1 次失败睡 1s，第 2 次睡 2s，第 3 次不再睡。
_BACKOFF_S = 1.0
#: 单条文本上限。QQ 单条消息有长度上限，留余量截断（超长消息由调用方自行分段）。
_TEXT_MAX = 2000
#: 允许的出站基址主机。只认回环——见模块 docstring 的安全边界。
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
#: scope → (OneBot API 路径, payload 里的会话 id 键)
_SCOPE_ENDPOINT = {
    "group": ("/send_group_msg", "group_id"),
    "private": ("/send_private_msg", "user_id"),
}

#: 模块级长连接客户端：startup() 建、shutdown() 关（main.py lifespan 内成对调用）。
_client: httpx.AsyncClient | None = None


def _install_bot_log_handlers() -> None:
    """给 bot 命名空间补一个 WARNING+ 的 stderr handler（幂等）。

    为什么必须补：main.py 装到 "moray" logger 上的两个 handler 都是 ERROR 级
    （self_check.get_error_handler / get_error_file_handler）；一旦它们挂上，
    logging 的 lastResort 不再兜底，WARNING 级节点日志会被静默丢弃（2026-11-01
    实测：worker 启动了、任务处理了，stderr 里一条 bot 日志都没有）。
    bot 的运行证据（入队/落库/回推重试）全是 WARNING，必须自己挂一个。
    """
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    for name in ("moray.bot", "moray.bot_client"):
        lg = logging.getLogger(name)
        if any(getattr(h, "_moray_bot_handler", False) for h in lg.handlers):
            continue
        handler = logging.StreamHandler()
        handler.setLevel(logging.WARNING)
        handler.setFormatter(fmt)
        handler._moray_bot_handler = True  # 自定义幂等标记，重复 import 不重复挂
        lg.addHandler(handler)


_install_bot_log_handlers()


def _validate_base(raw: str) -> str:
    """校验 OneBot API 基址：仅 http/https + 回环主机 + 根路径。

    返回规范化后的基址（去掉尾部斜杠）。任何不合规取值 raise ValueError，
    由 startup() 捕获后降级为"客户端不启动"（不炸整个进程）。
    """
    text = str(raw or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError as e:
        raise ValueError("BOT_API_BASE 解析失败：%s" % e) from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("BOT_API_BASE 仅允许 http/https：%r" % text)
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("BOT_API_BASE 必须是回环地址（OneBot 同机部署），拒绝 %r" % host)
    try:
        parts.port  # 访问一次触发非法端口校验（ValueError 在此抛出）
    except ValueError:
        raise ValueError("BOT_API_BASE 端口非法：%r" % text) from None
    if parts.path not in ("", "/"):
        raise ValueError("BOT_API_BASE 只写协议+主机+端口，不要带路径：%r" % text)
    return text.rstrip("/")


def _auth_headers() -> dict:
    """出站鉴权头：OneBot 未开 token 时留空，绝不发送空 Bearer。"""
    token = str(config.BOT_API_TOKEN or "").strip()
    return {"Authorization": "Bearer " + token} if token else {}


async def startup() -> bool:
    """建出站长连接。返回 True=就绪，False=配置非法/未启动（调用方据此决定是否起 worker）。"""
    global _client
    try:
        base = _validate_base(config.BOT_API_BASE)
    except ValueError as e:
        logger.warning("[bot] 出站客户端未启动：%s", e)
        return False
    _client = httpx.AsyncClient(base_url=base, timeout=_TIMEOUT_S, trust_env=False)
    logger.warning("[bot] 出站客户端就绪 base=%s timeout=%.0fs", base, _TIMEOUT_S)
    return True


async def shutdown() -> None:
    """关闭长连接。未启动时是安全 no-op（关闸/配置非法两种路径共用）。"""
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
        logger.warning("[bot] 出站客户端已关闭")
    except Exception as e:  # noqa: BLE001 - 收尾失败只留痕，绝不挡住进程退出
        logger.warning("[bot] 出站客户端关闭异常：%s: %s", type(e).__name__, str(e)[:120])
    finally:
        _client = None


async def send_text(scope: str, chat_id: str, text: str) -> bool:
    """把一段文本发回 OneBot（群聊/私聊）。成功 True，三次都失败 False。

    纯出站：不解析消息、不写库、不抛异常（网络失败是常态，由调用方决定后续）。
    """
    route = _SCOPE_ENDPOINT.get(str(scope or "").strip().lower())
    if route is None:
        logger.warning("[bot] 回推拒绝：未知 scope=%r", scope)
        return False
    endpoint, id_key = route
    target = str(chat_id or "").strip()
    if not target.isdigit():
        logger.warning("[bot] 回推拒绝：chat_id 非数字 %r", target)
        return False
    body = str(text or "").strip()
    if not body:
        logger.warning("[bot] 回推拒绝：空文本 scope=%s chat=%s", scope, target)
        return False
    if _client is None:
        logger.warning("[bot] 回推失败：客户端未启动（BOT_ENABLED=false 或基址非法）")
        return False

    payload = {id_key: int(target), "message": body[:_TEXT_MAX]}
    headers = _auth_headers()
    last = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await _client.post(endpoint, json=payload, headers=headers)
            try:
                data = resp.json()
            except ValueError:
                data = None
            ok = resp.status_code == 200 and (
                not isinstance(data, dict) or data.get("status", "ok") == "ok")
            if ok:
                logger.warning("[bot] 回推成功 scope=%s chat=%s attempt=%d chars=%d",
                               scope, target, attempt, len(body))
                return True
            last = "HTTP %s body=%s" % (resp.status_code, str(data)[:160])
        except httpx.TimeoutException as e:
            last = "Timeout(%s)" % type(e).__name__
        except httpx.HTTPError as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:120])
        except Exception as e:  # noqa: BLE001 - 出站失败不抛给 worker 主循环
            last = "%s: %s" % (type(e).__name__, str(e)[:120])
        logger.warning("[bot] 回推失败 scope=%s chat=%s attempt=%d/%d：%s",
                       scope, target, attempt, _MAX_ATTEMPTS, last)
        if attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_BACKOFF_S * (2 ** (attempt - 1)))
    logger.warning("[bot] 回推最终失败 scope=%s chat=%s attempts=%d last=%s",
                   scope, target, _MAX_ATTEMPTS, last)
    return False


def build_greeting_text() -> str:
    """v2 主动问候文本（骨架，本阶段不实现，恒返回空串）。

    预留实现方向（2026-11-01 侦察报告 Q1.4）：
    - self_state.get_current_state() 取 mental_score / consecutive_failures；
    - id is None（状态流水为空）时必须走中性语气，绝不拿基线 100 冒充"我今天状态很好"
      （与 self_reflection.py 的"不拿基线冒充健康"同口径）；
    - 分档：>=80 轻快 / >=50 平稳 / <50 克制，不吹漂亮话。
    返回空串 = 调用方不得发送（fail-close：没有真实状态就不凭空造问候）。
    """
    return ""
