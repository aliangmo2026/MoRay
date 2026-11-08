# -*- coding: utf-8 -*-
"""Iris MCP 客户端 —— **DEPRECATED（第2晚 2026-11-06）**：已退化为通用网关的兼容垫片。

历史（p_mcp 迭代·任务1 阶段2.3）本模块手写 MCP-SSE 三步直连 Iris 9301；第2晚起
MoRay 统一走 ``server/app/mcp_gateway.py``（Argus/Iris 同一套传输、鉴权、信封、防循环），
本文件仅保留 ``IrisMCPClient`` 公开形状，内部全部转调网关，确保三个既有消费方零改动：
  * adapters/routes.py          （/api/adapters/health、/api/adapters/log-intent）
  * engine/task_runner.py       （mcp_call 步骤：Iris 工具调用）
  * server/app/log_monitor.py   （analyze_error 根因分析）
Laya 8001 侧不走本模块（独立进程），改调 8000 的 ``POST /api/mcp/gateway/call``，
见 decision-kernel/lora-training/modules/tool_registry.py::_call_iris。

契约保持（与旧实现逐项对齐）：
  * ``call_tool`` 恒返回器官原生 ``{code,data}`` 信封；Iris 业务码（IRIS_TIMEOUT /
    IRIS_INVALID_INPUT / …）原样上浮；网关传输码映射回旧前缀
    （MCP_UNREACHABLE/MCP_AUTH_FAILED → IRIS_UNREACHABLE，MCP_INTERNAL → IRIS_INTERNAL），
    保证 adapters/routes.py 的 503/502 分流语义不变；
  * ``health()`` 仍是 GET /health 探针（不是 MCP 工具调用，5s 超时）；
  * ``cfg`` / ``server_config`` / ``expand_value`` / ``_base_from_sse_url`` 保持可用（含测试）。

新代码请直接 ``from server.app.mcp_gateway import MCPGateway``（或 execute_chat_tool），
不要再引用本模块。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Dict, Optional, Tuple

import httpx

# 独立脚本（python adapters/iris_mcp_client.py）场景：sys.path 只有 adapters/，
# 需要把仓库根放进来才能 import server.app.mcp_gateway；包内导入时此操作是幂等的。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:  # 正常路径：转调统一网关
    from server.app import mcp_gateway as _gateway_mod
    _GATEWAY_IMPORT_ERROR = ""
except Exception as e:  # noqa: BLE001 - 独立环境缺依赖时给出可读错误而非 ImportError 崩溃
    _gateway_mod = None
    _GATEWAY_IMPORT_ERROR = "%s: %s" % (type(e).__name__, e)

_logger = logging.getLogger("moray.adapters.iris")
_DEPRECATION_LOGGED = False

# 网关传输码 → 旧 Iris 客户端码（只影响"非器官原生信封"的失败；业务码原样上浮）
_GATEWAY_CODE_MAP = {
    "MCP_UNREACHABLE": "IRIS_UNREACHABLE",
    "MCP_AUTH_FAILED": "IRIS_UNREACHABLE",   # 旧实现 401/403 也归 IRIS_UNREACHABLE
    "MCP_INTERNAL": "IRIS_INTERNAL",
    "MCP_PARSE_FAILED": "IRIS_PARSE_FAILED",
    "MCP_TOOL_ERROR": "IRIS_INTERNAL",
}

# 占位符展开：仅处理 "${NAME}"（与 docker-compose 惯例对齐，历史行为保留）
_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def expand_value(v: Any) -> Any:
    if isinstance(v, str):
        m = _PLACEHOLDER_RE.match(v.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return v


def server_config(name: str = "iris") -> Dict[str, Any]:
    """读 MCP_SERVERS[name] 并展开占位（优先走网关实现，保持单一真源）。

    独立脚本/配置不可导入时回落到历史默认（transport=sse, 9301），绝不因 import
    失败让旧调用方直接崩。
    """
    if _gateway_mod is not None:
        try:
            return _gateway_mod.get_server_config(name)
        except Exception:  # noqa: BLE001 - 回落历史默认
            pass
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
    """('http://127.0.0.1:9301/sse') → (基址, SSE 路径 '/sse')。历史工具函数，保留。"""
    from urllib.parse import urlsplit
    sp = urlsplit(url)
    base = f"{sp.scheme}://{sp.netloc}"
    path = sp.path or "/sse"
    return base, path


class IrisMCPClient:
    """DEPRECATED：形状兼容旧调用方，内部全部转调 server/app/mcp_gateway.py。"""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        global _DEPRECATION_LOGGED
        self.cfg = cfg or server_config("iris")
        self.base, self.sse_path = _base_from_sse_url(str(self.cfg.get("url") or ""))
        self.timeout = float(self.cfg.get("timeout") or 30)
        self.api_key = str(self.cfg.get("api_key") or "")
        if not _DEPRECATION_LOGGED:
            _DEPRECATION_LOGGED = True
            _logger.warning("[deprecated] adapters/iris_mcp_client.py 已并入 mcp_gateway，"
                            "本次起所有 Iris 工具调用经统一网关（本提示只打一次）")

    # ---------------- 对外方法（旧契约） ----------------

    def _gateway(self):
        if _gateway_mod is None:
            return None
        return _gateway_mod.MCPGateway("iris", self.cfg)

    async def health(self) -> Dict[str, Any]:
        """GET <base>/health 探针（非 MCP 工具调用；旧端点/旧超时/旧返回形状不变）。"""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                r = await c.get(self.base + "/health", headers=self._headers())
                return r.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "code": "IRIS_UNREACHABLE", "message": str(e)}

    async def list_tools(self) -> list:
        gw = self._gateway()
        if gw is None:
            _logger.warning("[deprecated] 网关不可用：%s", _GATEWAY_IMPORT_ERROR)
            return []
        return await gw.list_tools()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具，恒返回 {code,data} 信封；实际传输由统一网关完成。"""
        gw = self._gateway()
        if gw is None:
            return {"code": "IRIS_UNREACHABLE",
                    "data": {"message": "统一网关不可用：%s" % _GATEWAY_IMPORT_ERROR}}
        if not bool(self.cfg.get("enabled", True)):
            return {"code": "IRIS_UNREACHABLE",
                    "data": {"message": "Iris MCP 已禁用（MORAY_IRIS_MCP_ENABLED=0）"}}
        # 旧客户端所有工具共用 cfg.timeout；网关新增按工具分级（视觉链 120s），
        # 取两者较大值 = 既不缩短旧行为，又让视觉工具拿满长窗。
        try:
            per_tool = float(_gateway_mod.tool_timeout(name))
        except Exception:  # noqa: BLE001
            per_tool = self.timeout
        env = await gw.call_tool(name, dict(arguments or {}),
                                 timeout=max(self.timeout, per_tool))
        code = str(env.get("code") or "IRIS_INTERNAL")
        if code in _GATEWAY_CODE_MAP:
            return {"code": _GATEWAY_CODE_MAP[code], "data": env.get("data")}
        return {"code": code, "data": env.get("data")}

    # ---------------- 内部（历史形状保留） ----------------

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h


async def _selfcheck() -> None:  # 手工冒烟：python adapters/iris_mcp_client.py
    import asyncio
    cli = IrisMCPClient()
    print("health:", json.dumps(await cli.health(), ensure_ascii=False))
    tools = await cli.list_tools()
    print("tools:", [t["name"] for t in tools])
    img = r"D:\Iris\tests\assets\p1_terminal.png"
    r = await cli.call_tool("iris_ocr_text", {"image_path": img})
    print("ocr code:", r["code"], "text head:", (r.get("data") or {}).get("text", "")[:80])
    bad = await cli.call_tool("iris_analyze_image", {"image_path": r"D:\不存在.png"})
    print("bad-input code:", bad["code"])


if __name__ == "__main__":
    import asyncio
    asyncio.run(_selfcheck())
