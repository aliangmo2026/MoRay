# -*- coding: utf-8 -*-
"""MCP 网关：MoRay 8000 作为 MCP **Client** 连接外部器官 MCP Server。
第 1 晚（2026-11-05）试点 Argus；第 2 晚（2026-11-06）迁入 Iris（SSE @ 9301）。

传输范式：泛化自 adapters/iris_mcp_client.py 的手写 MCP 三步（见该文件头注 ①②③），
做到完全参数化（server 名 → 配置 → 传输自动分派）：
  * transport="sse"             → GET <url> 读 `event: endpoint` 帧拿 messages 路径 →
                                  POST 该路径发 JSON-RPC（应答直接读 HTTP body）→
                                  握手失败降级 streamable-HTTP（直接 POST 基址）；
  * transport="streamable-http" → 直接 POST <url>（JSON-RPC 2.0；Argus mount /mcp/ 的形态）。
配置源 = server/app/config.py 的 MCP_SERVERS 注册表（${VAR} 占位在消费端展开，与 iris
客户端同一约定）；鉴权头 = 有 api_key 就发 `X-API-Key`（Iris 同时接受 `?api_key=` 查询参数，
SSE 握手已附带）——Argus/Iris 两种器官鉴权语义一处覆盖。依赖只有 httpx + 标准库 ——
**刻意不引入官方 mcp SDK**（requirements.txt 头注的既有决策，本模块是"泛化"不是"换栈"）。

对外主方法：
    await mcp_gateway.list_tools("argus")                    -> list[dict]  # 原始 MCP 工具清单
    await mcp_gateway.call_tool("argus", "search_web", {...}) -> {"code": ..., "data": ...}

聊天链路集成（消费方 server/app/llm_proxy.py）：
    await prepare_chat_tools(request, existing_tools)   # 注入策略（organ 豁免 + opt-in 门禁）
    await run_server_tool_rounds(payload, url, headers, do_request, ...)  # 服务端工具执行环

跨进程集成（第 2 晚新增，消费方 Laya 8001 的 tool_registry._call_iris）：
    server/app/mcp_routes.py 把本模块暴露为 POST /api/mcp/gateway/call —— Laya 不再直连
    Iris 8004/9301，统一经 8000 网关（server/app/api.py 尾部挂载）。

信封契约：code ∈ OK / MCP_TOOL_ERROR / MCP_UNREACHABLE / MCP_AUTH_FAILED /
MCP_PARSE_FAILED / MCP_INTERNAL；传输层失败**绝不抛异常**，恒返回信封（fail-open 纪律：
网关故障不阻断聊天，只是模型拿不到那几个工具）。
[第2晚] 器官原生信封（Iris 服务端契约 {"code","data"}）原样上浮不二次包裹 ——
旧 IrisMCPClient.call_tool 就是 json.loads(text) 直接返回内层信封，3 个旧消费方零改动。

防循环（任务书红线：器官回调的请求绝不注入工具）：
  1) 带 `X-Moray-Source: organ` 头的请求硬豁免 —— llm_proxy 顶层 + 本模块双层；
     Iris engine.py / task_runner / task_planner 均主动打标；bot 用 X-Moray-Tools: none；
  2) [第4晚 2026-11-06] 注入默认开启（MCP_CHAT_INJECT_DEFAULT=True）：普通聊天直接获得
     白名单内全部只读工具；`X-Moray-Tools` 可收窄、`none` 可关闭、`=0` 环境变量一键退回；
  3) [第4晚] 一次请求内 MCP 工具执行硬上限 15：第 16 次前熔断，llm_proxy 直接返回
     `MCP_FUSE_MESSAGE`（防死循环终极保险）；写工具 execute_plan/refactor_smart 永不进
     默认注入（MCP_INJECT_FORBIDDEN_TOOLS），仅显式 API / env+advanced 预设可触发。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from . import config
from . import guardian

_logger = logging.getLogger("moray.mcp")

# [坑复证 2026-11-06] moray 侧根 logger 配置只放行 ERROR 以上（bot MVP 同款教训），
# 本模块的注入/执行环 INFO 是拦截墙验收的关键观测点 —— 自挂 handler 保证可见
# （stderr → %TEMP%/moray.err.log；propagate 保持 True，ERROR 级重复无害）。
if not _logger.handlers:  # 防重复初始化（reload 场景）
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    _logger.addHandler(_h)
_logger.setLevel(logging.INFO)

# 占位符展开：仅处理 "${NAME}"（与 docker-compose 惯例对齐，同 iris_mcp_client.py）
_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def expand_value(v: Any) -> Any:
    if isinstance(v, str):
        m = _PLACEHOLDER_RE.match(v.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return v


def get_server_config(name: str) -> Dict[str, Any]:
    """读 MCP_SERVERS[name] 并展开占位；api_key 展开为空时按注册表兜底 env 名再取一次。"""
    entry = dict(config.MCP_SERVERS.get(name) or {})
    entry.setdefault("transport", "streamable-http")
    entry.setdefault("url", "")
    entry.setdefault("api_key", "")
    entry.setdefault("timeout", config.MCP_TOOL_TIMEOUT_DEFAULT)
    entry.setdefault("enabled", True)
    entry["url"] = str(expand_value(entry["url"]))
    entry["api_key"] = str(expand_value(entry["api_key"]))
    if not entry["api_key"]:
        env_name = str((getattr(config, "MCP_ENV_KEY_FALLBACK", {}) or {}).get(name, "") or "")
        if env_name:
            entry["api_key"] = os.environ.get(env_name, "")
    return entry


class MCPGateway:
    """单个外部 MCP Server 的连接器（无状态短连接：每次 RPC 自建 AsyncClient，
    与 iris_mcp_client 同款 —— 器官均为本机 loopback 服务，握手成本毫秒级）。"""

    def __init__(self, name: str, cfg: Optional[Dict[str, Any]] = None):
        self.name = name
        self.cfg = cfg or get_server_config(name)
        self.url = str(self.cfg.get("url") or "")
        self.transport = str(self.cfg.get("transport") or "streamable-http")
        self.timeout = float(self.cfg.get("timeout") or config.MCP_TOOL_TIMEOUT_DEFAULT)
        self.api_key = str(self.cfg.get("api_key") or "")
        # [第2晚] MCP_SERVERS[name].enabled 此前只在旧 Iris 客户端里被消费；网关必须同语义，
        # 否则 MORAY_IRIS_MCP_ENABLED=0 对聊天注入无效。
        self.enabled = bool(self.cfg.get("enabled", True))
        # [安全] URL 只来自 config.MCP_SERVERS（运维静态注册表，绝不接受请求输送的 URL，
        # 结构上无 SSRF 面）。这里只做 scheme 白名单：拒绝 file:// / ftp:// 等非 HTTP 传输；
        # 本机器官（Iris 9301 / Argus 8010）走 loopback 是架构本身，不是用户可控地址。
        self._scheme_ok = urlsplit(self.url).scheme in ("http", "https")

    # ---------------- 对外方法 ----------------

    async def list_tools(self, timeout: Optional[float] = None) -> list:
        """tools/list：失败/禁用恒返回 []（调用方按"拿不到 = 不注入"处理）。"""
        if not self.enabled:
            _logger.info("[mcp-gw] %s 已禁用（MCP_SERVERS.%s.enabled=false）", self.name, self.name)
            return []
        env = await self._rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout)
        if env.get("code") != "OK":
            _logger.warning("[mcp-gw] %s tools/list 失败：%s", self.name, env.get("code"))
            return []
        tools = (env.get("data") or {}).get("tools")
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: Dict[str, Any],
                        timeout: Optional[float] = None) -> Dict[str, Any]:
        """tools/call：恒返回 {code,data} 信封。

        [第2晚 Iris 迁入] 结果归一化两条路：
          * 器官原生信封（text 解析出 {"code": str, "data": ...}，Iris 服务端契约）→
            原样上浮。旧 IrisMCPClient.call_tool 就是 json.loads(text) 直返内层信封，
            保持同一语义，adapters/routes.py、engine/task_runner.py、log_monitor.py 零改动。
          * 普通业务对象/纯文本（Argus）→ 包成 {"code":"OK","data":...}。
        isError 但非信封形状 → MCP_TOOL_ERROR（沿用第1晚）。
        """
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments or {}}}
        env = await self._rpc(rpc, timeout)
        if env.get("code") != "OK":
            return env
        result = env.get("data") or {}
        try:
            text = result["content"][0]["text"]
        except Exception:  # noqa: BLE001 - 非标准形状原样透传（信封仍为 OK）
            return {"code": "OK", "data": result}
        try:
            parsed = json.loads(text)
        except Exception:  # noqa: BLE001 - 纯文本结果（非 JSON）原样给
            parsed = None
        if (isinstance(parsed, dict) and isinstance(parsed.get("code"), str)
                and "data" in parsed):
            # 器官原生信封：IRIS_TIMEOUT / IRIS_INVALID_INPUT 等业务码原样交给调用方
            return parsed
        if result.get("isError"):
            return {"code": "MCP_TOOL_ERROR",
                    "data": {"message": str(text)[:400]}}
        if parsed is not None:
            return {"code": "OK", "data": parsed}
        return {"code": "OK", "data": {"text": text}}

    # ---------------- 传输层 ----------------

    def _base(self) -> str:
        sp = urlsplit(self.url)
        return "%s://%s" % (sp.scheme, sp.netloc)

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def _rpc(self, payload: Dict[str, Any],
                   timeout: Optional[float] = None) -> Dict[str, Any]:
        t = float(timeout) if timeout else self.timeout
        if not self._scheme_ok:
            return {"code": "MCP_UNREACHABLE",
                    "data": {"message": "%s MCP URL 非法（仅允许 http/https）：%r"
                                        % (self.name, self.url)}}
        if self.transport == "sse":
            endpoint = None
            try:
                endpoint = await self._sse_endpoint(t)
            except Exception as e:  # noqa: BLE001 - 降级链第一环失败不终止
                _logger.info("[mcp-gw] %s SSE 握手失败，降级 streamable-HTTP：%s",
                             self.name, type(e).__name__)
            if endpoint:
                try:
                    return await self._post_rpc(self._base() + endpoint, payload, t)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                    # 连接未建立 = 请求没送出去，降级基址直 POST 安全（原第1晚逻辑）
                    _logger.info("[mcp-gw] %s messages 连接失败，降级基址直 POST：%s",
                                 self.name, type(e).__name__)
                except Exception as e:  # noqa: BLE001
                    # [第2晚 2026-11-06 回归实证] ReadTimeout / WriteTimeout /
                    # RemoteProtocolError：请求很可能已被器官收下并在执行，此时重发会让
                    # 同一个 tools/call 执行两次（实测 Iris 慢诊断 120s 超时后网关又打一轮
                    # 基址 POST，总耗时 240s、诊断重复）。直接回信封，绝不重试。
                    _logger.warning("[mcp-gw] %s messages POST 超时/中断（不重试，防重复执行）：%s",
                                    self.name, type(e).__name__)
                    return {"code": "MCP_UNREACHABLE",
                            "data": {"message": "%s 工具调用超时/中断（%s，请求已发送不重试）：%s"
                                     % (self.name, type(e).__name__, e)}}
            try:
                return await self._post_rpc(self._base() + "/", payload, t)
            except Exception as e:  # noqa: BLE001
                return {"code": "MCP_UNREACHABLE",
                        "data": {"message": "%s MCP 不可达（%s）：%s: %s"
                                 % (self.name, self._base(), type(e).__name__, e)}}
        # streamable-http：<url> 就是 JSON-RPC POST 目标（Argus = http://127.0.0.1:8010/mcp/）
        try:
            return await self._post_rpc(self.url, payload, t)
        except Exception as e:  # noqa: BLE001
            return {"code": "MCP_UNREACHABLE",
                    "data": {"message": "%s MCP 不可达（%s）：%s: %s"
                             % (self.name, self.url, type(e).__name__, e)}}

    async def _sse_endpoint(self, timeout: float) -> str:
        """SSE 三步之①：GET <url> 读首帧 `event: endpoint`（api_key 附查询参数兼容
        EventSource 场景，同 iris_mcp_client._sse_endpoint）。"""
        url = self.url
        if self.api_key:
            url += ("&" if "?" in url else "?") + "api_key=" + self.api_key
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
            async with c.stream("GET", url, headers=self._headers()) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:200]
                    raise RuntimeError("SSE 握手被拒 HTTP %d：%s" % (r.status_code, body))
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

    async def _post_rpc(self, url: str, payload: Dict[str, Any],
                        timeout: float) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
            r = await c.post(url, json=payload, headers=self._headers())
            if r.status_code == 401:
                return {"code": "MCP_AUTH_FAILED",
                        "data": {"message": "%s MCP 鉴权失败（401）：请核对 MCP_ENV_KEY_FALLBACK"
                                            " 指向的 env Key 与服务端同值" % self.name}}
            if r.status_code == 403:
                return {"code": "MCP_UNREACHABLE",
                        "data": {"message": "%s MCP 拒绝非本机调用（403）" % self.name}}
            if r.status_code >= 400:
                return {"code": "MCP_UNREACHABLE",
                        "data": {"message": "%s MCP HTTP %s" % (self.name, r.status_code)}}
            data = r.json()
        if "error" in data:
            return {"code": "MCP_INTERNAL",
                    "data": {"message": "RPC 错误：" + str(data["error"])[:300]}}
        return {"code": "OK", "data": data.get("result") or {}}


# ================================================================ 客户端缓存

_CLIENTS: Dict[str, MCPGateway] = {}


def _gateway(name: str) -> MCPGateway:
    gw = _CLIENTS.get(name)
    if gw is None:
        gw = _CLIENTS[name] = MCPGateway(name)
    return gw


# ================================================================ 聊天链路注入

# tools/list 发现结果缓存：server 名 → (取到时刻, 工具清单)。TTL 内不重发发现请求。
_TOOLS_CACHE: Dict[str, Tuple[float, List[dict]]] = {}


async def _list_tools_cached(name: str) -> List[dict]:
    now = time.time()
    hit = _TOOLS_CACHE.get(name)
    if hit and now - hit[0] < float(getattr(config, "MCP_TOOLS_CACHE_TTL", 300.0)):
        return hit[1]
    try:
        # 发现是聊天请求的前置步骤：6s 硬预算，绝不让一次发现拖住整条聊天
        tools = await asyncio.wait_for(_gateway(name).list_tools(), timeout=6.0)
    except Exception as e:  # noqa: BLE001 - fail-open：拿不到就不注入
        _logger.warning("[mcp-gw] %s tools/list 超时/失败（本次不注入）：%s: %s",
                        name, type(e).__name__, e)
        return []
    if tools:  # [第2晚] 空清单不落缓存：服务刚起/暂时禁用时别让 5 分钟 TTL 卡住恢复
        _TOOLS_CACHE[name] = (now, tools)
    return tools


async def list_server_tools(name: str, timeout: Optional[float] = None) -> List[dict]:
    """[第2晚] 供 HTTP 面 / 旧垫片调用的发现入口（带 enabled 门 + 6s 预算，失败 []）。"""
    cfg = get_server_config(name)
    if not bool(cfg.get("enabled", True)):
        return []
    try:
        gw = MCPGateway(name, cfg)
        return await asyncio.wait_for(gw.list_tools(timeout), timeout=float(timeout or 6.0))
    except Exception as e:  # noqa: BLE001
        _logger.warning("[mcp-gw] %s 工具发现失败：%s: %s", name, type(e).__name__, e)
        return []


async def probe_server(name: str) -> Dict[str, Any]:
    """[第2晚] 单服务状态摘要（GET /api/mcp/gateway/status 用；不含任何 Key 明文）。"""
    cfg = get_server_config(name)
    enabled = bool(cfg.get("enabled", True))
    out: Dict[str, Any] = {
        "name": name,
        "enabled": enabled,
        "transport": cfg.get("transport"),
        "url": cfg.get("url"),
        "api_key_configured": bool(cfg.get("api_key")),
        "tools": [],
        "ok": False,
    }
    if not enabled:
        out["code"] = "disabled"
        return out
    tools = await _list_tools_cached(name)
    out["tools"] = sorted(str((t or {}).get("name") or "") for t in tools)
    out["ok"] = bool(tools)
    out["code"] = "OK" if tools else "unreachable_or_empty"
    return out


async def prepare_chat_tools(request, existing_tools: Any) -> List[dict]:
    """按注入策略返回 OpenAI function 形状的工具列表（空 = 不注入）。

    策略（防循环红线 + 全量注入口径，详见模块头注）：
      * `X-Moray-Source: organ` → 恒不注入（器官回调豁免；llm_proxy 顶层还有一道）；
      * [第4晚] 未点名时默认注入全部白名单服务（MCP_CHAT_INJECT_DEFAULT 默认 True）；
        `X-Moray-Tools: argus[,iris...]` 仍可显式收窄；`X-Moray-Tools: none` 显式关闭；
      * 工具集 = MCP_CHAT_INJECT_SERVERS[server]["tools"] 白名单 ∩ 器官 tools/list 实际返回；
      * 写工具硬红线：MCP_INJECT_FORBIDDEN_TOOLS（execute_plan/refactor_smart）永不进
        默认注入；只有 MORAY_MCP_ALLOW_WRITE_INJECT=1 + 头 `X-Moray-Tools-Mode: advanced`
        才放行（高级预设路径）；显式 API /api/mcp/gateway/call 不受此闸限制；
      * 命名空间 = `mcp__<server>__<raw>`（与 dsh mcp-client 公开名惯例一致，防与本机/前端工具撞名）；
      * 与请求自带工具重名的跳过；任何异常 fail-open 返回 []。
    """
    try:
        if str(request.headers.get("X-Moray-Source") or "").strip().lower() == "organ":
            _logger.info("[mcp] 注入跳过：器官回调（X-Moray-Source: organ）")
            return []
        want = str(request.headers.get("X-Moray-Tools") or "").strip().lower()
        if want == "none":
            _logger.info("[mcp] 注入跳过：调用方显式 X-Moray-Tools: none")
            return []
        if want:
            servers = [s.strip() for s in want.split(",") if s.strip()]
        elif getattr(config, "MCP_CHAT_INJECT_DEFAULT", True):
            servers = list((config.MCP_CHAT_INJECT_SERVERS or {}).keys())
        else:
            return []
        known = set(config.MCP_CHAT_INJECT_SERVERS or {})
        servers = [s for s in servers if s in known]
        if not servers:
            return []
        # [第4晚] 写工具硬红线：默认永不注入。高级预设 = 运维开 env 且请求方显式
        # 带 X-Moray-Tools-Mode: advanced；显式 API 走 /api/mcp/gateway/call 不受限。
        forbidden = set(getattr(config, "MCP_INJECT_FORBIDDEN_TOOLS", ()) or ())
        advanced = bool(getattr(config, "MCP_ALLOW_WRITE_INJECT", False)) and (
            str(request.headers.get("X-Moray-Tools-Mode") or "").strip().lower() == "advanced")
        have = set()
        if isinstance(existing_tools, list):
            for t in existing_tools:
                if isinstance(t, dict) and isinstance(t.get("function"), dict):
                    have.add(str(t["function"].get("name") or ""))
        out: List[dict] = []
        for s in servers:
            spec = config.MCP_CHAT_INJECT_SERVERS[s]
            if not bool(get_server_config(s).get("enabled", True)):
                _logger.info("[mcp] %s 已禁用（MCP_SERVERS.enabled=false），跳过注入", s)
                continue
            prefix = str(spec["prefix"])
            for t in await _list_tools_cached(s):
                raw = str((t or {}).get("name") or "")
                if raw not in (spec.get("tools") or ()):
                    continue
                if raw in forbidden and not advanced:
                    _logger.warning("[mcp] 红线拦截：%s.%s 属写操作工具，永不进默认聊天注入"
                                    "（仅显式 API 或 env+advanced 预设可触发）", s, raw)
                    continue
                fq = prefix + raw
                if fq in have:
                    continue
                out.append({"type": "function", "function": {
                    "name": fq,
                    "description": str(t.get("description") or "") or raw,
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                }})
        if out:
            _logger.info("[mcp] 注入 %d 个外部器官工具：%s", len(out),
                         ",".join(t["function"]["name"] for t in out))
        return out
    except Exception as e:  # noqa: BLE001 - 注入准备失败绝不影响聊天主链路
        _logger.warning("[mcp] 注入准备失败（fail-open 不注入）：%s: %s",
                        type(e).__name__, e)
        return []


def tool_timeout(raw_name: str) -> float:
    """按工具名取超时（detective 系长窗 120s，见 config.MCP_TOOL_TIMEOUTS 注释）。"""
    try:
        return float(config.MCP_TOOL_TIMEOUTS.get(raw_name, config.MCP_TOOL_TIMEOUT_DEFAULT))
    except Exception:  # noqa: BLE001
        return float(getattr(config, "MCP_TOOL_TIMEOUT_DEFAULT", 30.0))


async def execute_chat_tool(server: str, raw_name: str,
                            arguments: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个注入工具并返回信封（llm_proxy 工具环的执行点；失败=错误信封不抛）。

    [能力图谱 2026-11-06] 每次执行后写一条结构化审计进 agent_tool_log
    （tool=mcp__<server>__<raw_name>，detail 带 source=mcp | code=<信封码>）：
    此前 MCP 工具调用只进 %TEMP% 文本日志与 night_shift 计数，self_capability
    能力图谱对 32 个器官工具是盲的。这是它唯一的写入侧缺口，补上后
    GET /api/self_model/capabilities 即可覆盖全部器官工具。
    防线：入参**一个字都不落库**（args_summary 恒空串——器官参数里可能有大图
    base64 与用户正文，审计只需要"调了什么、成没成、多快"）；审计失败仅 warning，
    绝不影响执行结果返回。"""
    t0 = time.time()
    # [Guardian 2026-11-11] 语义安全守门员：执行前三级审查（低危直通 → 硬规则 →
    # 1.5B 语义审查；模型故障 fail-open 绝不阻塞主链路）。物理白名单
    # （MCP_INJECT_FORBIDDEN_TOOLS）只管"注不注入"，管不住显式 API 点名；
    # 这里是工具执行的唯一咽喉（工具环 + /api/mcp/gateway/call 都汇入），
    # 拦截在真正触达器官之前。
    try:
        ok, reason = await guardian.check_tool_safety(raw_name, arguments)
    except Exception as g_err:  # noqa: BLE001 - 守卫本身故障 = fail-open 放行
        ok, reason = True, "guardian fail-open: %s: %s" % (type(g_err).__name__, str(g_err)[:120])
    if not ok:
        _logger.warning("[mcp-gw] Guardian 拦截 %s.%s：%s", server, raw_name, reason)
        try:
            from .system_routes import _emit_intercept
            _emit_intercept("守卫拦截", "%s.%s：%s" % (server, raw_name, reason))
        except Exception:  # noqa: BLE001 - 观测旁路失败绝不反噬
            pass
        return {"code": "GUARDIAN_BLOCKED",
                "data": {"message": "【系统安全守卫】已拦截本次工具调用 %s.%s：%s"
                                    % (server, raw_name, reason)}}
    env = await _gateway(server).call_tool(raw_name, arguments, timeout=tool_timeout(raw_name))
    elapsed_ms = int((time.time() - t0) * 1000)
    _logger.info("[mcp-gw] %s.%s code=%s %.0fms", server, raw_name,
                 env.get("code"), (time.time() - t0) * 1000)
    # [能力图谱 2026-11-06] 结构化审计旁路（crud 同一 moray.sqlite3；惰性 import 防环）
    try:
        from . import crud
        code = str(env.get("code") or "")
        data = env.get("data")
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("message") or "")[:300]
        if code == "OK":
            status = "ok"
        elif "TIMEOUT" in code.upper():
            status = "timeout"
        else:
            status = "error"
        # [体检修复2 2026-11-14] 同步 sqlite 审计写离环（guardian 拦截后的必经路径）
        await asyncio.to_thread(
            crud.append_agent_log,
            "mcp__%s__%s" % (str(server)[:20], str(raw_name)[:36]),
            "", True, status, elapsed_ms,
            ("source=mcp | code=%s | %s" % (code, msg)).rstrip(" |")[:1000])
    except Exception as e:  # noqa: BLE001 - 审计失败绝不影响工具结果
        _logger.warning("[mcp-gw] 能力图谱审计写入失败（不影响执行结果）：%s: %s",
                        type(e).__name__, str(e)[:160])
    return env


# ================================================================ 服务端工具执行环

async def run_server_tool_rounds(payload: Dict[str, Any], upstream_url: str,
                                 upstream_headers: Dict[str, str], do_request,
                                 model: str = "", record_usage=None,
                                 max_rounds: Optional[int] = None,
                                 max_calls: Optional[int] = None) -> int:
    """服务端 MCP 工具执行环：上游返回 `mcp__<server>__*` 的 tool_calls 时就地执行、
    以 role:"tool" 回灌，然后由调用方的既有 _stream/_non_stream 出最终答案。

    [第4晚 2026-11-06] 防死循环终极保险：
      * 一次请求内 MCP 工具执行次数硬上限 ``MCP_TOOL_CALLS_MAX``（默认 15）：
        第 15 次照常执行；模型若再要第 16 次，立即熔断、不再执行、不再发起后续模型轮，
        payload["_moray_mcp_fused"]=True + payload["_moray_mcp_fuse_message"]=固定安全
        提示，由 llm_proxy 直接合成最终响应（流式/非流式都有）；
      * 每轮真实 usage/token 同步记账到 night_shift（非夜班 run 零副作用）；
      * 环结束（自然结束/轮数耗尽/熔断）都把注入工具从 payload["tools"] 摘除 ——
        最终出答那一次调用模型不再能发起新的 MCP 工具轮。

    纪律：
      * 上游 4xx/5xx、请求异常、无 tool_calls、只有前端工具 → 立即交回原链路；
      * 混合轮（我们的 + 前端的）只执行我们的并把前端工具从回灌消息里剔除；
      * 返回实际执行的工具轮数（int，兼容旧调用方）。
    """
    if max_rounds is None:
        max_rounds = int(getattr(config, "MCP_TOOL_ROUNDS", 20))
    if max_calls is None:
        max_calls = int(getattr(config, "MCP_TOOL_CALLS_MAX", 15))
    max_calls = max(1, int(max_calls))
    fuse_message = str(getattr(config, "MCP_FUSE_MESSAGE",
                               "系统安全限制：工具调用次数超限，已强制中断。"))
    prefixes: Dict[str, str] = {}
    injected_names = set()
    for server, spec in (config.MCP_CHAT_INJECT_SERVERS or {}).items():
        prefix = str(spec["prefix"])
        prefixes[prefix] = server
        for raw in (spec.get("tools") or ()):
            injected_names.add(prefix + str(raw))

    rounds = 0
    executed_calls = 0
    fused = False
    # ---- [P0 工具环总时限 2026-11-07] ----
    # [2026-11-07 实锤] 普通聊天被慢失败器官（Argus CLOSE_WAIT 形态）拖到 3 分钟：
    # 单工具有分级超时，但"模型轮 × N + 工具执行 × N"整体无上限，慢链路无限叠加。
    # 本预算是整个工具环的挂钟硬上限：模型轮与工具执行都包进 wait_for(剩余预算)，
    # 到点熔断——已完成工具的结果保留回灌，注入系统提示让模型基于现有信息作答，
    # 注入工具随后在 finally 被摘除（最终出答轮不可能再起新的工具轮）。
    total_budget = float(getattr(config, "MCP_CHAT_TOTAL_TIMEOUT", 120.0))
    deadline = time.monotonic() + max(1.0, total_budget)
    timed_out = False

    def _remaining() -> float:
        return deadline - time.monotonic()
    # [2026-11-06 流式 E2E 实证] 工具轮内部恒非流式：_do_request 走 resp.json()，若把
    # 客户端的 stream:true 原样带上，上游回 SSE 文本 → JSONDecodeError → 环提前退出。
    # 进环先钉 False，finally 恢复原值（最终出答轮由调用方按客户端意愿走流式）。
    _orig_stream = payload.get("stream")
    payload["stream"] = False
    # [2026-11-06 防退化] 工具轮生成封顶：即使指令冲突仍引发退化循环，单轮也被
    # max_tokens 截断（本地 9B ≈ 60s 内必然返回），不再吃满 300s read 超时。
    # 最终出答轮在 finally 恢复调用方原值。
    _orig_max_tokens = payload.get("max_tokens")
    try:
        _cap = int(getattr(config, "MCP_TOOL_ROUND_MAX_TOKENS", 1500))
        if not isinstance(_orig_max_tokens, int) or _orig_max_tokens > _cap:
            payload["max_tokens"] = _cap
    except Exception:  # noqa: BLE001
        pass
    try:
        for _ in range(max(1, int(max_rounds))):
            # [P0 总时限] 每轮开跑前先看预算：耗尽即熔断，不再发起模型轮
            if _remaining() <= 0.5:
                timed_out = True
                break
            try:
                status, data, _msg = await asyncio.wait_for(
                    do_request(upstream_url, upstream_headers, payload),
                    timeout=max(0.5, _remaining()))
            except asyncio.TimeoutError:
                timed_out = True
                _logger.warning("[mcp] 工具环总时限熔断：模型轮等待超限（预算 %.0fs）", total_budget)
                break
            except Exception as e:  # noqa: BLE001 - 交回原链路的重试/报错分支
                _logger.warning("[mcp] 工具环上游请求异常（交回原链路）：%s: %s",
                                type(e).__name__, e)
                break
            if status >= 400 or not isinstance(data, dict):
                break  # 交回 _non_stream/_stream 的既有错误链如实报错
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tcs = message.get("tool_calls")
            if not isinstance(tcs, list) or not tcs:
                break  # 模型给出最终答案 → 交回原链路（完整守卫链）
            _u = data.get("usage") or {}
            _pt = int(_u.get("prompt_tokens") or 0)
            _ct = int(_u.get("completion_tokens") or 0)
            # 工具轮也是真实消耗：usage 照记 usage_log（旁路，同 _record_usage_log 惯例）
            if record_usage is not None:
                try:
                    # [体检修复2 2026-11-14] usage 落库离环（同步 sqlite 不再阻塞事件循环）
                    await asyncio.to_thread(record_usage, model, _pt, _ct, 0)
                except Exception:  # noqa: BLE001
                    pass
            ours, others = [], []
            for tc in tcs:
                _name = str(((tc or {}).get("function") or {}).get("name") or "")
                (ours if _name in injected_names else others).append(tc)
            if not ours:
                if others:
                    _logger.info("[mcp] 本轮仅前端工具（%s），交回前端执行", ",".join(
                        str(((tc or {}).get("function") or {}).get("name") or "") for tc in others))
                break
            if others:
                _logger.warning("[mcp] 混合工具轮：前端工具 %s 本轮不执行（试点仅 MCP 工具）",
                                ",".join(str(((tc or {}).get("function") or {}).get("name") or "")
                                         for tc in others))
            tool_msgs: List[dict] = []
            kept_calls: List[dict] = []
            _round_failures = 0
            _round_names: List[str] = []
            for i, tc in enumerate(ours):
                # [第4晚] 第 16 次工具调用：不执行，直接熔断（第 15 次仍放行）
                if executed_calls >= max_calls:
                    fused = True
                    break
                # [P0 总时限] 预算耗尽：本工具不执行（不计 executed_calls，不回灌
                # tool_call —— assistant 消息此刻尚未落 payload，协议面零残缺）
                if _remaining() <= 0.5:
                    timed_out = True
                    break
                executed_calls += 1
                fn = (tc or {}).get("function") or {}
                fq = str(fn.get("name") or "")
                _round_names.append(fq)
                server, raw = "", ""
                for p, s in prefixes.items():
                    if fq.startswith(p):
                        server, raw = s, fq[len(p):]
                        break
                if not server:
                    env = {"code": "MCP_INTERNAL", "data": {"message": "未知 MCP 工具前缀：" + fq}}
                else:
                    try:
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            args = json.loads(args)
                    except Exception:  # noqa: BLE001 - 参数坏 → 空参（让工具自己报错）
                        args = None
                    # [P0 总时限] 工具执行包进剩余预算：到点取消在飞器官调用，回灌
                    # 超时信封（协议面完整——tool_call 有对应 tool 结果），随后熔断
                    try:
                        env = await asyncio.wait_for(
                            execute_chat_tool(server, raw, args if isinstance(args, dict) else {}),
                            timeout=max(0.5, _remaining()))
                    except asyncio.TimeoutError:
                        env = {"code": "MCP_TOTAL_TIMEOUT",
                               "data": {"message": "工具执行超出工具环总时限（%.0fs），已中止" % total_budget}}
                        timed_out = True
                if str(env.get("code")) != "OK":
                    _round_failures += 1
                call_id = str((tc or {}).get("id") or ("call_mcp_%d_%d" % (rounds, i)))
                content = json.dumps(env, ensure_ascii=False)
                _max = int(getattr(config, "MCP_TOOL_RESULT_MAX_CHARS", 6000))
                if len(content) > _max:
                    content = content[:_max] + "…[截断]"
                tool_msgs.append({"role": "tool", "tool_call_id": call_id,
                                  "name": fq, "content": content})
                # 回灌的 assistant.tool_calls 与 OpenAI 协议对齐：arguments 用 JSON 字符串
                kept_calls.append({"id": call_id, "type": "function",
                                   "function": {"name": fq,
                                                "arguments": json.dumps(
                                                    args if isinstance(args, dict) else {},
                                                    ensure_ascii=False)}})
                _logger.info("[mcp] 工具已执行并回喂：%s（code=%s）", fq, env.get("code"))
                # [P0 总时限] 本工具吃掉了最后一点预算：回灌已完成，立即熔断
                if timed_out:
                    break
            if kept_calls:
                payload["messages"] = list(payload.get("messages") or []) + [
                    {"role": "assistant", "content": str(message.get("content") or ""),
                     "tool_calls": kept_calls},
                ] + tool_msgs
                rounds += 1
                # [第4晚] 夜班记账：工具次数 + 本轮 token（无 active run 时零副作用）
                try:
                    from . import night_shift as _night
                    # [体检修复2 2026-11-14] 夜班记账含原子写盘（fsync），离环执行
                    await asyncio.to_thread(
                        _night.record_mcp_round,
                        tool_calls=len(kept_calls), prompt_tokens=_pt,
                        completion_tokens=_ct, failures=_round_failures,
                        detail=",".join(_round_names))
                except Exception as _ns_err:  # noqa: BLE001 - 记账旁路绝不反噬主链路
                    _logger.warning("[mcp] night_shift MCP 记账失败（忽略）：%s: %s",
                                    type(_ns_err).__name__, _ns_err)
            if timed_out:
                break
            if fused:
                _logger.warning("[mcp] 防死循环熔断：本请求工具调用已达 %d 次（上限 %d），"
                                "第 %d 次被强制中断", executed_calls, max_calls,
                                executed_calls + 1)
                break
        # [P0 总时限] 熔断收尾：已完成的工具结果保留在 messages 里，注入系统提示
        # 让模型基于现有信息作答；注入工具在 finally 被摘除 → 最终出答轮不可能再
        # 起新的工具轮。payload["_moray_mcp_timeout"] 供调用方/测试观测。
        if timed_out and not fused:
            payload["messages"] = list(payload.get("messages") or []) + [
                {"role": "system",
                 "content": str(getattr(config, "MCP_TOTAL_TIMEOUT_NOTE",
                                        "由于部分工具执行超时，请基于现有信息回答。"))}]
            payload["_moray_mcp_timeout"] = True
            _logger.warning("[mcp] 工具环总时限收尾：已回灌 %d 个工具结果，注入超时提示"
                            "（预算 %.0fs），交回原链路出最终答案", executed_calls, total_budget)
    finally:
        payload["stream"] = _orig_stream
        if _orig_max_tokens is None:
            payload.pop("max_tokens", None)
        else:
            payload["max_tokens"] = _orig_max_tokens
        if fused:
            payload["_moray_mcp_fused"] = True
            payload["_moray_mcp_fuse_message"] = fuse_message
        pt = payload.get("tools")
        if isinstance(pt, list):
            kept = [t for t in pt if not (isinstance(t, dict) and str(
                ((t.get("function") or {}).get("name") or "")) in injected_names)]
            if len(kept) != len(pt):
                if kept:
                    payload["tools"] = kept
                else:
                    payload.pop("tools", None)
    return rounds


async def _selfcheck(server: str = "argus") -> None:  # 手工冒烟：python -m / asyncio.run
    tools = await _list_tools_cached(server)
    print("tools:", [t.get("name") for t in tools])
    env = await execute_chat_tool(server, "search_web",
                                  {"query": "MoRay MCP 网关冒烟", "max_results": 2})
    print("code:", env.get("code"))
    print("data head:", json.dumps(env.get("data"), ensure_ascii=False)[:300])


if __name__ == "__main__":
    import sys
    asyncio.run(_selfcheck(sys.argv[1] if len(sys.argv) > 1 else "argus"))
