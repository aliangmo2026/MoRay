# -*- coding: utf-8 -*-
"""Iris MCP 客户端（p_mcp 迭代·任务1 阶段2.3 的 MoRay 消费侧）。

传输范式 = Argus 同款手写 MCP-SSE（审计 §1.4）：
  ① GET <url> 建立 SSE，读首个 `event: endpoint` 帧拿 messages 路径（含 session_id）；
  ② POST <messages路径> 发 JSON-RPC tools/call，应答直接读 HTTP body（Argus 便利模式，
     其 SSE 流不回推响应，见 Argus main.py:1071-1078）；
  ③ 握手失败时降级 streamable-HTTP（直接 POST 基址，Iris 服务端同时支持，main 等价
     Argus main.py:1081-1089）。

配置来源 = server/app/config.py 尾部 MCP_SERVERS['iris']（任务书 2.3 的字面形状）：
  {"transport":"sse","url":"http://127.0.0.1:9301/sse",
   "api_key":"${IRIS_MCP_API_KEY}","timeout":30,"enabled":True}
  本客户端负责对 "${VAR}" 占位做唯一一处展开（消费端行为，不动 config.py）。

返回契约：call_tool 恒返回统一信封 dict {"code": ..., "data": ...}，
  传输层失败映射为客户端伪码 IRIS_UNREACHABLE（IRIS_* 枚举是服务端契约，
  客户端不可达不冒充服务端错误语义；消费方统一按 code != "OK" 处理即可）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx

# 占位符展开：仅处理 "${NAME}"（与 docker-compose 惯例对齐，见审计 §3.3）
_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def expand_value(v: Any) -> Any:
    if isinstance(v, str):
        m = _PLACEHOLDER_RE.match(v.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return v


def server_config(name: str = "iris") -> Dict[str, Any]:
    """读 MCP_SERVERS[name] 并展开占位。config 不可导入时（独立脚本场景）给同名默认。"""
    try:
        from server.app import config
        entry = dict(config.MCP_SERVERS.get(name) or {})
    except Exception:  # noqa: BLE001
        entry = {}
    entry.setdefault("transport", "sse")
    entry.setdefault("url", "http://127.0.0.1:9301/sse")
    entry.setdefault("api_key", "")
    entry.setdefault("timeout", 30)
    entry.setdefault("enabled", True)
    entry["url"] = str(expand_value(entry["url"]))
    entry["api_key"] = str(expand_value(entry["api_key"]))
    if not entry["api_key"]:  # 未配置/未展开为空时直接读 env（两侧同值约定）
        entry["api_key"] = os.environ.get("IRIS_MCP_API_KEY", "")
    return entry


def _base_from_sse_url(url: str) -> Tuple[str, str]:
    """('http://127.0.0.1:9301/sse') → (基址, SSE 路径 '/sse')。"""
    from urllib.parse import urlsplit
    sp = urlsplit(url)
    base = f"{sp.scheme}://{sp.netloc}"
    path = sp.path or "/sse"
    return base, path


class IrisMCPClient:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or server_config("iris")
        self.base, self.sse_path = _base_from_sse_url(self.cfg["url"])
        self.timeout = float(self.cfg.get("timeout") or 30)
        self.api_key = str(self.cfg.get("api_key") or "")

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def health(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                r = await c.get(self.base + "/health", headers=self._headers())
                return r.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "IRIS_UNREACHABLE", "message": str(e)}

    async def list_tools(self) -> list:
        env = await self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        if env.get("code") == "OK":
            return env["data"].get("tools", [])
        return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具，恒返回 {code,data} 信封（详见模块头注的契约）。"""
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
        env = await self._rpc(rpc)
        if env.get("code") != "OK":
            return env
        result = env["data"]
        try:
            text = result["content"][0]["text"]
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            return {"code": "IRIS_PARSE_FAILED",
                    "data": {"message": f"MCP 结果非预期形状：{type(e).__name__}: {e}"}}

    # ---------------- 内部：握手 + RPC ----------------

    async def _rpc(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            endpoint = await self._sse_endpoint()
        except Exception as e:  # noqa: BLE001
            endpoint = None
            last_err = e
        else:
            try:
                return await self._post_rpc(self.base + endpoint, payload)
            except Exception as e:  # noqa: BLE001
                last_err = e
        # 降级：streamable-HTTP 直接 POST 基址
        try:
            return await self._post_rpc(self.base + "/", payload)
        except Exception as e:  # noqa: BLE001
            return {"code": "IRIS_UNREACHABLE",
                    "data": {"message": f"Iris MCP 不可达（{self.base}）：{type(e).__name__}: {e}"}}

    async def _sse_endpoint(self) -> str:
        """GET /sse 读首帧 endpoint 路径（带 api_key 查询参数以兼容 EventSource 场景）。"""
        url = self.base + self.sse_path
        if self.api_key:
            url += ("&" if "?" in url else "?") + "api_key=" + self.api_key
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=5.0)) as c:
            async with c.stream("GET", url, headers=self._headers()) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:200]
                    raise RuntimeError(f"SSE 握手被拒 HTTP {r.status_code}：{body}")
                event: Optional[str] = None
                async for line in r.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event == "endpoint":
                        return line.split(":", 1)[1].strip()
                    elif line.startswith(":"):
                        continue
                raise RuntimeError("SSE 未出现 endpoint 帧")

    async def _post_rpc(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=5.0)) as c:
            r = await c.post(url, json=payload, headers=self._headers())
            if r.status_code == 401:
                return {"code": "IRIS_UNREACHABLE",
                        "data": {"message": "Iris MCP 鉴权失败：请核对 IRIS_MCP_API_KEY 两侧同值"}}
            if r.status_code == 403:
                return {"code": "IRIS_UNREACHABLE",
                        "data": {"message": "Iris MCP 拒绝非本机调用"}}
            data = r.json()
        if "error" in data:
            return {"code": "IRIS_INTERNAL",
                    "data": {"message": "RPC 错误：" + str(data["error"])}}
        return {"code": "OK", "data": data.get("result") or {}}


async def _selfcheck() -> None:  # 手工冒烟：python -m 或在 tests 里 asyncio.run
    import asyncio
    cli = IrisMCPClient()
    print("health:", json.dumps(await cli.health(), ensure_ascii=False))
    tools = await cli.list_tools()
    print("tools:", [t["name"] for t in tools])
    r = await cli.call_tool("iris_ocr_text", {"image_path": r"D:\Iris\tests\assets\python_error.png"})
    print("ocr code:", r["code"], "text head:", (r.get("data") or {}).get("text", "")[:80])
    bad = await cli.call_tool("iris_analyze_image", {"image_path": r"D:\不存在.png"})
    print("bad-input code:", bad["code"])


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selfcheck())
