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

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config

router = APIRouter()
_logger = logging.getLogger("moray.llm")

# 连接/读取/写入/池化超时（读取放宽到 300s 支持长思考流式）
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


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


@router.post("/api/llm/chat")
async def llm_chat(request: Request):
    # ---- 1. 配置检查：未配置云端 key 时明确报错（健康检查可反映）----
    if not config.LLM_BASE_URL or not config.LLM_API_KEY:
        return _err(503, "cloud_not_configured",
                    "本地后端未配置云端 LLM：请复制 server/.env.example 为 server/.env 并填写 "
                    "MORAY_LLM_BASE_URL / MORAY_LLM_API_KEY 后重启后端。")

    # ---- 2. 入参解析（只取白名单字段；base_url 等一律忽略，防 SSRF）----
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _err(400, "bad_messages", "messages 必须是非空数组。")

    model = str(body.get("model") or config.LLM_MODEL or "").strip()
    if not model:
        return _err(400, "bad_model", "未指定 model 且后端未配置 MORAY_LLM_MODEL。")
    stream = body.get("stream", True) is not False

    payload: dict = {"model": model, "messages": messages, "stream": stream}
    if body.get("temperature") is not None:
        payload["temperature"] = float(body["temperature"])
    if body.get("max_tokens") is not None:
        payload["max_tokens"] = int(body["max_tokens"])
    # 推理参数：部分服务商（Ollama 兼容口等）支持；不支持的会自行忽略
    if body.get("think") is not None:
        payload["think"] = bool(body["think"])

    headers = {
        "Authorization": "Bearer " + config.LLM_API_KEY,
        "Content-Type": "application/json",
    }
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    _logger.info("llm proxy -> %s model=%s stream=%s (key %s)",
                 config.LLM_BASE_URL, model, stream, _mask_key(config.LLM_API_KEY))

    async def _non_stream():
        """非流式：一次性请求，返回统一结构"""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    try:
                        detail = resp.json()
                        msg = detail.get("error", {}).get("message") or detail.get("message") or ""
                    except Exception:  # noqa: BLE001
                        msg = resp.text[:200]
                    return _err(502, _upstream_code(resp.status_code),
                                f"上游错误（{resp.status_code}）：{msg}".strip())
                data = resp.json()
        except httpx.TimeoutException:
            return _err(504, "upstream_timeout", "上游请求超时，请稍后重试。")
        except httpx.HTTPError as e:
            _logger.warning("llm proxy upstream http error: %s", type(e).__name__)
            return _err(502, "upstream_error", "无法连接云端 LLM（网络错误）。")

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        return {
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

    async def _stream():
        """流式：SSE 逐块透传；前端断开（cancel）时 finally 中 aclose 同步中止上游"""
        client = httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            req = client.build_request("POST", url, json=payload, headers=headers)
            upstream = await client.send(req, stream=True)
            if upstream.status_code >= 400:
                try:
                    raw = await upstream.aread()
                    detail = json.loads(raw).get("error", {}).get("message", "") or raw[:200]
                except Exception:  # noqa: BLE001
                    detail = "上游错误"
                await upstream.aclose()
                yield f"data: {json.dumps({'ok': False, 'code': _upstream_code(upstream.status_code), 'message': f'上游错误（{upstream.status_code}）：{detail}'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except asyncio.CancelledError:
            # 前端断开：取消传播到 httpx 流，finally 收尾
            raise
        except Exception as e:  # noqa: BLE001
            _logger.warning("llm proxy stream error: %s", type(e).__name__)
            yield f"data: {json.dumps({'ok': False, 'code': 'upstream_error', 'message': '流式传输中断：' + type(e).__name__})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await client.aclose()

    if stream:
        return StreamingResponse(_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return await _non_stream()
