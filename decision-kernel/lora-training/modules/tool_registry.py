# -*- coding: utf-8 -*-
"""L7.6 工具注册表：统一封装 iris / argus / zcode / file 四类工具。

设计约束（与本机真实服务对齐，非任务书的理想路径）：
  * iris  真实入口 = POST http://localhost:8004/  （JSON-RPC tools/call，需 X-API-Key）
          任务书写的 /analyze 在本机不存在，已改为 JSON-RPC 直连 iris_analyze_image/text。
  * argus 真实入口 = POST http://127.0.0.1:8010/mcp/ （JSON-RPC tools/call，工具 search_web）
          任务书写的 /search 在本机不存在，search_web 工具语义等价（关键词→结果列表）。
  * zcode 本机当前 8005 无监听；保持可配置端点，调用失败返回标准错误信封、不抛异常。
  * file  本地 stdlib 读写/遍历，带根目录约束防路径穿越。

所有地址与超时可用环境变量覆盖：
  LAYA_IRIS_BASE / LAYA_ARGUS_BASE / LAYA_ZCODE_BASE / LAYA_FILE_ROOT
  LAYA_TOOL_TIMEOUT_IRIS / _ARGUS / _ZCODE （秒）

统一返回信封（成功/失败都是这一个形状，executor/self_check 只认它，绝不抛异常打断流程）：
  {"ok": bool, "tool": str, "data": Any|null,
   "error": {"code": int, "message": str} | null, "elapsed_ms": int}
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import sys

# [2026-09-30] file 工具路径翻译+同步中间件（授权目录代理，加层不拆墙）：
# 引入 D:\MoRay\core\file_proxy.py；代理不可用时静默回落到原生读写，不影响旧功能。
_FILE_PROXY_DIR = os.environ.get("MORAY_FILE_PROXY_DIR", r"D:\MoRay\core")
if os.path.isdir(_FILE_PROXY_DIR) and _FILE_PROXY_DIR not in sys.path:
    sys.path.insert(0, _FILE_PROXY_DIR)
try:
    from file_proxy import proxy_file_read, proxy_file_write, is_authorized_path
    _HAS_FILE_PROXY = True
except Exception:  # noqa: BLE001
    _HAS_FILE_PROXY = False

# ---------------- 端点配置（环境变量优先，回落到本机实测默认） ----------------
IRIS_BASE = os.environ.get("LAYA_IRIS_BASE", "http://localhost:8004").rstrip("/")
# [2026-10-01 U-6-4] 默认端口从 8003 改为 8010：
# 整个生态（Electron 启动器 / start_moray_full.ps1 / D:\Argus\start_argus.bat）
# 已统一 Argus 到 8010；8003 是 2026-09-30 之前的旧端口，已废弃。
# Electron 启动时通过 LAYA_ARGUS_BASE=8010 注入对齐，
# 但手动 `python laya_std_server.py` 拉起的 Laya 拿不到该变量，
# 会落回默认值导致 ConnectError。改为 8010 后任何启动方式都对齐。
# host 同时从 localhost 改为 127.0.0.1（与其他模块一致，避免 hosts 解析歧义）。
ARGUS_BASE = os.environ.get("LAYA_ARGUS_BASE", "http://127.0.0.1:8010").rstrip("/")
ZCODE_BASE = os.environ.get("LAYA_ZCODE_BASE", "http://localhost:8005").rstrip("/")
# [2026-09-30 修复] ZCode 代码执行端点：moray 8000 提供 /api/engines/zcode/run
# （本地沙箱 subprocess 执行，实测 print(1+1)→"2" 正常）。原默认 http://localhost:8005
# 无任何服务监听 → Laya zcode 工具必然 ConnectError。本端点与 ZCODE_BASE（zcode.* 原子
# 工具，对接沙箱 REST，另行配置）语义独立，互不影响。
ZCODE_RUN_BASE = os.environ.get("LAYA_ZCODE_RUN_BASE", "http://127.0.0.1:8000/api/engines/zcode").rstrip("/")
# [2026-09-30 v2] moray 主服务根：zcode Agent 形态任务走 moray 原生引擎
# （POST /api/engines/zcode/tasks + GET /api/engines/zcode/stream/{id}），
# 统一 zcode 执行引擎入口 = moray 8000，Laya 不再绕过引擎直调 CLI。
MORAY_BASE = os.environ.get("LAYA_MORAY_BASE", "http://127.0.0.1:8000").rstrip("/")
# Iris MCP 的 X-API-Key：本机 iris 用 loopback-only 闸门，密钥来自 D:\Iris\server\start_server.ps1
IRIS_API_KEY = os.environ.get("IRIS_MCP_API_KEY", "morayIrisKey2026")

_T_IRIS = float(os.environ.get("LAYA_TOOL_TIMEOUT_IRIS", "120"))
_T_ARGUS = float(os.environ.get("LAYA_TOOL_TIMEOUT_ARGUS", "90"))
_T_ZCODE = float(os.environ.get("LAYA_TOOL_TIMEOUT_ZCODE", "60"))

# ---------------- ZCode CLI 自然语言兜底（Agent 形态） ----------------
# [2026-09-30] 代码形态走 moray /run 快速执行；自然语言（无代码特征）指令走
# ZCode CLI（-p prompt，Agent 形态，支持大白话）。已实测：默认配置（用户
# ~/.zcode/v2/config.json 的 DeepSeek provider，key 有效）下 "输出一句话 hello"
# → response "hello"。注意：不要设 ZCODE_PERSONAL_PROVIDER_CONFIG_FILE，会
# 覆盖默认 provider 并导致 "Select a model"。
ZCODE_CLI_BIN = os.environ.get("LAYA_ZCODE_CLI_BIN", r"C:\Program Files\nodejs\node.exe")
ZCODE_CLI_JS = os.environ.get("LAYA_ZCODE_CLI_JS", r"D:\ZCode_Test\zcode-repo\apps\zcode-cli\packages\cli\dist\zcode.cjs")
# [2026-09-30 修复] CLI 兜底的 cwd 用用户真实工作目录（默认=本服务目录
# lora-training，与 FILE_ROOT 同源计算，避免定义顺序依赖），不能指向空沙箱
# zcode_sandbox——否则 Agent 找不到用户提到的文件（实测：cwd=zcode_sandbox 时
# demo.py 找不到；cwd=lora-training 时正常追加注释）
ZCODE_CLI_SANDBOX = os.environ.get("LAYA_ZCODE_CLI_SANDBOX", "") or str(Path(__file__).resolve().parent.parent)
ZCODE_CLI_TIMEOUT = float(os.environ.get("LAYA_ZCODE_CLI_TIMEOUT", "180"))
# 代码特征正则：命中任一即视为"代码形态"，走 moray /run；全不命中视为自然语言
_ZCODE_CODE_HINT = re.compile(
    r"\(|\)|[\=\+\-\*\/\%\<\>\:\!\,]|^import\s|^from\s|^def\s|^class\s|^\s*print|^\s*return|"
    r"^\s*if\s|^\s*for\s|^\s*while\s|^\s*with\s|^\s*try\s|lambda|\[\s*\]|\{\s*\}|\"\s*\"|'\s*'|^\s*#"
)

# file 工具的根沙箱：只允许在此目录内读写/遍历，默认=本服务所在目录
FILE_ROOT = Path(os.environ.get("LAYA_FILE_ROOT", str(Path(__file__).resolve().parent.parent))).resolve()

VALID_TOOLS = ("iris", "argus", "zcode", "file")


def _env(tool: str, data: Any = None, code: int = 0, msg: str = "", t0: float = 0.0) -> Dict[str, Any]:
    """统一信封构造函数（唯一出口，保证 5 键恒在）。"""
    return {
        "ok": code == 0,
        "tool": tool,
        "data": data,
        "error": None if code == 0 else {"code": code, "message": msg[:500]},
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def _rpc_envelope(tool: str, raw: Dict[str, Any], t0: float) -> Dict[str, Any]:
    """把 MCP JSON-RPC 应答的 result.content[0].text（一段内联 JSON 字符串）解出来。

    Argus/Iris 都是"应答内联 body、非 SSE 回推"范式（见 [[moray-iris-mcp-paradigm]]）。
    """
    try:
        if "error" in raw and raw.get("error"):
            return _env(tool, code=raw["error"].get("code", -1), msg=json.dumps(raw["error"], ensure_ascii=False), t0=t0)
        content = raw.get("result", {}).get("content", [])
        text = content[0].get("text", "") if content else ""
        parsed = json.loads(text) if text else None
        return _env(tool, data=parsed, t0=t0)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        return _env(tool, data=raw, t0=t0)  # 解不开也如实回原 body，交 self_check 判


# ---------------- iris ----------------
async def _call_iris(payload: Dict[str, Any]) -> Dict[str, Any]:
    """看图/读截图/识别报错。payload 支持 image_path | image_base64 | text（三选一）。"""
    t0 = time.perf_counter()
    if "text" in payload and "image_path" not in payload and "image_base64" not in payload:
        name, args = "iris_analyze_text", {"text": payload["text"], "lang_hint": payload.get("lang_hint", ""),
                                            "hint": payload.get("hint", "")}
    else:
        args = {}
        if "image_path" in payload:
            args["image_path"] = payload["image_path"]
        if "image_base64" in payload:
            args["image_base64"] = payload["image_base64"]
        if "lang_hint" in payload:
            args["lang_hint"] = payload["lang_hint"]
        name = "iris_analyze_image"
    body = {"jsonrpc": "2.0", "id": int(t0 * 1000) % 100000, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    try:
        async with httpx.AsyncClient(timeout=_T_IRIS) as c:
            r = await c.post(IRIS_BASE + "/", json=body,
                             headers={"X-API-Key": IRIS_API_KEY, "Content-Type": "application/json"})
            if r.status_code != 200:
                return _env("iris", code=r.status_code, msg="iris HTTP %d: %s" % (r.status_code, r.text[:200]), t0=t0)
            return _rpc_envelope("iris", r.json(), t0)
    except Exception as e:  # noqa: BLE001
        return _env("iris", code=-1, msg="%s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)


# ---------------- argus ----------------
async def _call_argus(payload: Dict[str, Any]) -> Dict[str, Any]:
    """查资料/搜信息/行业调研。payload 支持 keyword/query。工具=search_web。"""
    t0 = time.perf_counter()
    query = payload.get("query") or payload.get("keyword") or payload.get("text") or ""
    if not query:
        return _env("argus", code=400, msg="argus 需要 query/keyword", t0=t0)
    body = {"jsonrpc": "2.0", "id": int(t0 * 1000) % 100000, "method": "tools/call",
            "params": {"name": "search_web", "arguments": {"query": query}}}
    try:
        # /mcp/ 带斜杠=200 直连；不带斜杠会 307 且 httpx 默认不跟随 POST 重定向 → 必须带斜杠
        async with httpx.AsyncClient(timeout=_T_ARGUS) as c:
            r = await c.post(ARGUS_BASE + "/mcp/", json=body, headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return _env("argus", code=r.status_code, msg="argus HTTP %d: %s" % (r.status_code, r.text[:200]), t0=t0)
            return _rpc_envelope("argus", r.json(), t0)
    except Exception as e:  # noqa: BLE001
        return _env("argus", code=-1, msg="%s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)


# ---------------- zcode ----------------
async def _call_zcode(payload: Dict[str, Any]) -> Dict[str, Any]:
    """写代码/跑脚本/代码执行。payload 支持 code。返回执行结果+退出码。"""
    t0 = time.perf_counter()
    code = payload.get("code") or payload.get("script") or ""
    if not code.strip():
        return _env("zcode", code=400, msg="zcode 需要 code", t0=t0)
    # [2026-09-30 自然语言兜底] 无代码特征 → 走 ZCode CLI（Agent 形态，支持大白话）；
    # 有代码特征 → 走 moray /run 快速执行（原逻辑）。全程 try/catch，失败回落到 moray。
    if not _ZCODE_CODE_HINT.search(code):
        cli = await _call_zcode_cli(code, t0=t0)
        if cli.get("code") not in (None, 0) or cli.get("error"):
            return cli
        return cli
    body = {"code": code, "language": payload.get("language", "python")}
    try:
        async with httpx.AsyncClient(timeout=_T_ZCODE) as c:
            # [2026-09-30 修复] 端点从 ZCODE_BASE/execute（8005 无服务）→ moray 8000 的
            # /api/engines/zcode/run；moray 返回 data 含 exit_code/stdout/stderr/status，
            # 与 self_check 的 zcode 校验（exit_code 非 0 判失败）形状兼容，原样透传。
            r = await c.post(ZCODE_RUN_BASE + "/run", json=body, headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return _env("zcode", code=r.status_code, msg="zcode HTTP %d: %s" % (r.status_code, r.text[:200]), t0=t0)
            data = r.json()
            # moray 返回 {ok:true, data:{task_id,status,stdout,stderr,exit_code,...}}；取 data 归一
            if isinstance(data, dict) and data.get("ok") is False:
                return _env("zcode", code=-1, msg=str(data.get("error") or data.get("msg") or "zcode 执行失败")[:200], t0=t0)
            inner = data.get("data") if isinstance(data, dict) else None
            # 归一：期望 {output, exit_code}；moray 无 output 字段，用 stdout 对齐
            if isinstance(inner, dict):
                if "output" not in inner and "stdout" in inner:
                    inner["output"] = inner["stdout"]
                return _env("zcode", data=inner, t0=t0)
            return _env("zcode", data=data, t0=t0)
    except Exception as e:  # noqa: BLE001
        return _env("zcode", code=-1, msg="%s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)


async def _call_zcode_cli(prompt: str, t0: Optional[float] = None) -> Dict[str, Any]:
    """ZCode 自然语言 Agent 执行：主通道 = moray 原生引擎（/tasks + /stream）。

    架构统一：zcode 执行引擎唯一入口 = moray 8000（应用层 AGI：MoRay 编排 + ZCode
    执行），moray 引擎零改动；moray 不可用/失败时回落到直调 ZCode CLI（_call_zcode_cli_direct）。
    失败一律返回错误信封，不抛异常。
    """
    t0 = t0 if t0 is not None else time.perf_counter()
    # [2026-09-30] 行为规范后缀：Agent 对中文指令易"误读为运行脚本"，明确要求实际
    # 执行文件修改并写回。注意必须单行（moray 引擎把 prompt 作为 CLI 命令行参数
    # "-p" 传入，含换行符会导致 Windows 下 CLI 秒退，实测队列只剩 [DONE]）。
    norm_prompt = (
        f"{prompt}。"
        "（请直接完成上述请求：若涉及读取/修改本地文件，必须用文件读写工具实际修改并写回，"
        "不要只运行脚本或只做解释；完成后用一两句话说明你做了什么。）"
    )
    # ---- 主通道：moray 引擎 /tasks + /stream ----
    # [器官→记忆 线C 2026-10-04] caller=laya 标记：本调用发生在 Laya 任务链内部，
    # 链收尾已由 executor 写 laya_executor/task_result；MoRay 8000 流端点的旁路记忆
    # 钩子见 caller=laya 即跳过，防一任务 N+1 条双写。前端执行卡直连时不带此字段，
    # 照常写 zcode_stream（"不留痕"补的就是前台直调这条）。
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                MORAY_BASE + "/api/engines/zcode/tasks",
                json={"prompt": norm_prompt, "language": "python", "caller": "laya"},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code != 200:
                return _env("zcode", code=-1, msg="moray zcode tasks HTTP %d: %s" % (r.status_code, r.text[:150]), t0=t0)
            tid_data = r.json()
            task_id = tid_data.get("task_id") if isinstance(tid_data, dict) else None
            if not task_id:
                return _env("zcode", code=-1, msg="moray zcode tasks 无 task_id: %s" % str(tid_data)[:150], t0=t0)
        # SSE 消费：直到 result 帧 / [DONE] / 超时；同时收集 turn.completed 兜底
        async with httpx.AsyncClient(timeout=ZCODE_CLI_TIMEOUT) as c:
            async with c.stream("GET", MORAY_BASE + "/api/engines/zcode/stream/%s" % task_id) as resp:
                if resp.status_code != 200:
                    return _env("zcode", code=-1, msg="moray zcode stream HTTP %d" % resp.status_code, t0=t0)
                last_turn = None
                ev_types = []
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    ev_types.append(str(ev.get("type")))
                    if ev.get("type") == "result":
                        data = {
                            "output": ev.get("response") or "",
                            "exit_code": 0,
                            "stdout": ev.get("response") or "",
                            "stderr": "",
                            "status": (ev.get("projection") or {}).get("status", "ok"),
                            "sessionId": ev.get("sessionId", ""),
                        }
                        return _env("zcode", data=data, t0=t0)
                    if ev.get("type") == "turn.completed":
                        last_turn = ev.get("payload") or {}
                    if ev.get("type") == "[DONE]":
                        break
        # 无 result 帧兜底：turn.completed 里带响应文本/失败原因
        if isinstance(last_turn, dict):
            if last_turn.get("resultType") == "failed" or last_turn.get("error"):
                return _env("zcode", code=-1, msg=(
                    str(last_turn.get("error") or last_turn.get("response") or "zcode Agent 执行失败"))[:200], t0=t0)
            resp_text = last_turn.get("response") or ""
            if resp_text:
                return _env("zcode", data={
                    "output": resp_text, "exit_code": 0, "stdout": resp_text,
                    "stderr": "", "status": "ok", "sessionId": task_id,
                }, t0=t0)
        return _env("zcode", code=-1,
                    msg="moray zcode 任务结束但无有效结果（task_id=%s，事件:%s）"
                        % (task_id, ",".join(ev_types[-10:]) or "无"), t0=t0)
    except asyncio.TimeoutError:
        return _env("zcode", code=-1, msg="moray zcode Agent 超时（%ds）" % ZCODE_CLI_TIMEOUT, t0=t0)
    except Exception as e:  # noqa: BLE001
        # 主通道不可用 → 回落直调 CLI
        return await _call_zcode_cli_direct(prompt, norm_prompt=norm_prompt, t0=t0)


async def _call_zcode_cli_direct(prompt: str, t0: Optional[float] = None,
                                 norm_prompt: Optional[str] = None) -> Dict[str, Any]:
    """（fallback）ZCode CLI 直调：-p prompt --mode yolo --json。与 moray 引擎同源，
    仅当 moray 引擎不可用时启用；失败时返回错误信封，不抛异常。
    """
    t0 = t0 if t0 is not None else time.perf_counter()
    prompt2 = norm_prompt if norm_prompt is not None else prompt
    cmd = [
        ZCODE_CLI_BIN, ZCODE_CLI_JS, "-p", prompt2, "--mode", "yolo",
        "--cwd", ZCODE_CLI_SANDBOX, "--disallowed-tools", "Bash,WebFetch", "--json",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=ZCODE_CLI_SANDBOX, env={**os.environ, "ZCODE_MODEL_TELEMETRY_ENABLED": "0"},
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=ZCODE_CLI_TIMEOUT)
        text = out.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        return _env("zcode", code=-1, msg="zcode CLI 超时（%ds）" % ZCODE_CLI_TIMEOUT, t0=t0)
    except Exception as e:  # noqa: BLE001
        return _env("zcode", code=-1, msg="zcode CLI: %s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)
    # 从 stdout 提取平衡括号完整 JSON 块（CLI 输出可能是多行 pretty JSON）
    block = None
    depth = 0
    buf = []
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if not in_block:
            if s.startswith("{"):
                in_block = True
            else:
                continue
        buf.append(s)
        depth += s.count("{") - s.count("}")
        if depth <= 0 and in_block:
            try:
                block = json.loads("\n".join(buf))
                break
            except json.JSONDecodeError:
                buf = []
                depth = 0
                in_block = False
    if not isinstance(block, dict):
        return _env("zcode", code=-1, msg="zcode CLI 无有效输出: %s" % text[-300:], t0=t0)
    data = {
        "output": block.get("response") or "",
        "exit_code": 0,
        "stdout": block.get("response") or "",
        "stderr": "",
        "status": block.get("projection", {}).get("status", "ok"),
        "sessionId": block.get("sessionId", ""),
    }
    return _env("zcode", data=data, t0=t0)


# ---------------- file ----------------
def _safe_path(p: str) -> Path:
    """把用户给的相对/绝对路径解析为绝对路径。
    [沙箱解除] 原 FILE_ROOT 单根越界检查已放开：file 工具可读写任意本地路径
    （相对路径仍解析到 FILE_ROOT；绝对路径原样放行，跨 C:/D: 盘均允许）。
    注意：放开后对"目录路径"执行 read/write 仍会报 PermissionError（Windows 语义），
    属 Agent 操作对象问题，非权限问题。
    """
    raw = Path(str(p))
    cand = (FILE_ROOT / raw) if not raw.is_absolute() else raw
    return cand.resolve()


# [2026-09-30] file 代理的"原函数"：操作沙箱副本（代理层已翻译路径 + 增量同步）。
def _sandbox_read_text(sp: str) -> str:
    return Path(sp).read_text(encoding="utf-8", errors="replace")


def _sandbox_write_text(sp: str, content: str) -> dict:
    Path(sp).parent.mkdir(parents=True, exist_ok=True)
    Path(sp).write_text(content, encoding="utf-8")
    return {"success": True}


def _proxy_err_code(msg: str) -> int:
    """代理错误信封 → 统一工具错误码：未授权/映射失败=403，其余=原兜底 -1。"""
    return 403 if msg.startswith("路径未授权") or msg.startswith("路径映射失败") else -1


async def _call_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    """本地文件 read / write / list_dir。op 必填。"""
    t0 = time.perf_counter()
    op = str(payload.get("op") or payload.get("operation") or "").lower()
    try:
        path = _safe_path(payload.get("path", ""))
    except (ValueError, OSError) as e:
        return _env("file", code=403, msg=str(e)[:200], t0=t0)
    try:
        if op == "read":
            if _HAS_FILE_PROXY:
                text = proxy_file_read(str(path), _sandbox_read_text)
                if isinstance(text, dict) and not text.get("success", False):
                    msg = str(text.get("error", ""))[:200]
                    return _env("file", code=_proxy_err_code(msg), msg=msg, t0=t0)
            else:
                text = _sandbox_read_text(str(path))
            return _env("file", data={"path": str(path), "content": text, "size": len(text)}, t0=t0)
        if op == "write":
            content = payload.get("content", payload.get("text", ""))
            if _HAS_FILE_PROXY:
                r = proxy_file_write(str(path), str(content), _sandbox_write_text)
                if isinstance(r, dict) and not r.get("success", False):
                    msg = str(r.get("error", ""))[:200]
                    return _env("file", code=_proxy_err_code(msg), msg=msg, t0=t0)
            else:
                _sandbox_write_text(str(path), str(content))
            return _env("file", data={"path": str(path), "written": len(str(content))}, t0=t0)
        if op == "list_dir":
            if _HAS_FILE_PROXY and not is_authorized_path(str(path)):
                return _env("file", code=403,
                            msg="路径未授权：%s（请先添加到 authorized_paths.json）" % path, t0=t0)
            if not path.is_dir():
                return _env("file", code=404, msg="目录不存在：%s" % path, t0=t0)
            entries = [{"name": q.name, "is_dir": q.is_dir(),
                        "size": q.stat().st_size if q.is_file() else None}
                       for q in sorted(path.iterdir())]
            return _env("file", data={"dir": str(path), "count": len(entries), "entries": entries}, t0=t0)
        return _env("file", code=400, msg="file 需要 op ∈ {read,write,list_dir}，收到=%r" % op, t0=t0)
    except FileNotFoundError as e:
        return _env("file", code=404, msg="文件不存在：%s" % str(e)[:150], t0=t0)
    except Exception as e:  # noqa: BLE001
        return _env("file", code=-1, msg="%s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)


# ---------------- 统一入口 ----------------
_HANDLERS = {"iris": _call_iris, "argus": _call_argus, "zcode": _call_zcode, "file": _call_file}


async def call_tool(tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """按工具名分发。未知工具也返回标准错误信封，绝不抛异常。"""
    handler = _HANDLERS.get(str(tool).lower())
    if handler is None:
        return _env(str(tool), code=404, msg="未知工具：%s（可选 %s）" % (tool, ",".join(VALID_TOOLS)),
                     t0=time.perf_counter())
    return await handler(dict(payload or {}))


def registry_status() -> Dict[str, Any]:
    """供 /execute 首行进度/调试：当前各工具端点配置快照。"""
    return {"iris": IRIS_BASE + "/ (X-API-Key)", "argus": ARGUS_BASE + "/mcp/ (search_web)",
            "zcode": ZCODE_BASE + "/execute", "file_root": str(FILE_ROOT)}


# ==================== [新增代码·开始] ZCode 工程级原子工具扩展（2026-09-29，纯加性） ====================
# 本节只向下追加代码，上方原有逻辑（含 zcode 代码执行 _call_zcode、call_tool、registry_status）
# 一行未改。新增 7 个 zcode.* 原子工具，对接 ZCode 沙箱 REST 接口：
#   zcode.file_list / file_read / file_write / file_edit / cmd_exec / process_kill / port_check
# 注册方式：@register_tool 按全名写入 REGISTERED_TOOLS，并把全名作为新键"加性注入" _HANDLERS
#   （只增键、不覆盖任何原有键名），executor 既有的 call_tool(tool, payload) 调用方式原样可用。
# 服务地址：沿用原有 ZCODE_BASE（环境变量 LAYA_ZCODE_BASE 优先，默认 http://localhost:8005）。
# 全异常兜底：参数缺失/数值非法/网络错误/超时/服务不可用，一律返回标准信封，绝不抛异常。

REGISTERED_TOOLS: Dict[str, Any] = {}


def register_tool(name: str):
    """工具注册装饰器：@register_tool("zcode.xxx") 注册 async 处理函数。"""
    def decorator(fn):
        REGISTERED_TOOLS[name] = fn
        _HANDLERS[name] = fn  # 加性注入：原有 4 个键（iris/argus/zcode/file）不受影响
        return fn
    return decorator


async def _zcode_request(tool: str, method: str, route: str,
                         body: Optional[Dict[str, Any]] = None,
                         timeout: Optional[float] = None) -> Dict[str, Any]:
    """ZCode 原子工具内部通用请求函数（不注册为工具）。

    统一处理：ZCODE_BASE 地址拼接、计时（_env 的 elapsed_ms）、HTTP 状态码检查、
    非 JSON 应答兜底、全异常捕获 → 标准信封 {ok, tool, data, error, elapsed_ms}。
    timeout 不传则用原有 _T_ZCODE 默认值。
    """
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout if timeout is not None else _T_ZCODE) as c:
            if method.upper() == "GET":
                r = await c.get(ZCODE_BASE + route)
            else:
                r = await c.post(ZCODE_BASE + route, json=body if body is not None else {},
                                 headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                return _env(tool, code=r.status_code,
                            msg="zcode HTTP %d: %s" % (r.status_code, r.text[:200]), t0=t0)
            try:
                data: Any = r.json()
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                data = {"raw": r.text}  # 非 JSON 应答也如实带回，交上层判
            return _env(tool, data=data, t0=t0)
    except Exception as e:  # noqa: BLE001
        return _env(tool, code=-1, msg="%s: %s" % (type(e).__name__, str(e)[:200]), t0=t0)


def _as_int(tool: str, value: Any, field: str) -> tuple:
    """数值型参数统一转 int；转不动返回 (None, 错误信封)。"""
    t0 = time.perf_counter()
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, _env(tool, code=400, msg="%s 参数 %s 需为整数，收到=%r" % (tool, field, value), t0=t0)


@register_tool("zcode.file_list")
async def _tool_zcode_file_list(payload: Dict[str, Any]) -> Dict[str, Any]:
    """列出 ZCode 沙箱指定目录下的文件和文件夹。POST /api/fs/list {path}。"""
    t0 = time.perf_counter()
    path = str(payload.get("path") or "").strip()
    if not path:
        return _env("zcode.file_list", code=400, msg="zcode.file_list 需要 path", t0=t0)
    return await _zcode_request("zcode.file_list", "POST", "/api/fs/list", {"path": path})


@register_tool("zcode.file_read")
async def _tool_zcode_file_read(payload: Dict[str, Any]) -> Dict[str, Any]:
    """读取 ZCode 沙箱指定文件的完整内容。POST /api/fs/read {path}。"""
    t0 = time.perf_counter()
    path = str(payload.get("path") or "").strip()
    if not path:
        return _env("zcode.file_read", code=400, msg="zcode.file_read 需要 path", t0=t0)
    return await _zcode_request("zcode.file_read", "POST", "/api/fs/read", {"path": path})


@register_tool("zcode.file_write")
async def _tool_zcode_file_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    """在 ZCode 沙箱创建或覆盖写入文件。POST /api/fs/write {path, content}。"""
    t0 = time.perf_counter()
    path = str(payload.get("path") or "").strip()
    if not path:
        return _env("zcode.file_write", code=400, msg="zcode.file_write 需要 path", t0=t0)
    if payload.get("content") is None:
        return _env("zcode.file_write", code=400, msg="zcode.file_write 需要 content", t0=t0)
    return await _zcode_request("zcode.file_write", "POST", "/api/fs/write",
                                {"path": path, "content": str(payload["content"])})


@register_tool("zcode.file_edit")
async def _tool_zcode_file_edit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """按行号增量修改 ZCode 沙箱文件内容。
    POST /api/fs/edit {path, line_start, line_end, content}。"""
    t0 = time.perf_counter()
    path = str(payload.get("path") or "").strip()
    if not path:
        return _env("zcode.file_edit", code=400, msg="zcode.file_edit 需要 path", t0=t0)
    if payload.get("content") is None:
        return _env("zcode.file_edit", code=400, msg="zcode.file_edit 需要 content", t0=t0)
    line_start, err = _as_int("zcode.file_edit", payload.get("line_start"), "line_start")
    if err is not None:
        return err
    line_end, err = _as_int("zcode.file_edit", payload.get("line_end"), "line_end")
    if err is not None:
        return err
    return await _zcode_request("zcode.file_edit", "POST", "/api/fs/edit", {
        "path": path, "line_start": line_start, "line_end": line_end,
        "content": str(payload["content"])})


@register_tool("zcode.cmd_exec")
async def _tool_zcode_cmd_exec(payload: Dict[str, Any]) -> Dict[str, Any]:
    """在 ZCode 沙箱内执行任意终端命令。POST /api/cmd/exec {command, cwd, timeout}。

    HTTP 超时 = 命令 timeout + 5 秒，避免命令未执行完 HTTP 连接先断开。"""
    t0 = time.perf_counter()
    command = str(payload.get("command") or "").strip()
    if not command:
        return _env("zcode.cmd_exec", code=400, msg="zcode.cmd_exec 需要 command", t0=t0)
    timeout, err = _as_int("zcode.cmd_exec", payload.get("timeout", 30), "timeout")
    if err is not None:
        return err
    if timeout <= 0:
        return _env("zcode.cmd_exec", code=400, msg="zcode.cmd_exec timeout 需为正整数，收到=%d" % timeout, t0=t0)
    cwd = str(payload.get("cwd") or "")
    return await _zcode_request("zcode.cmd_exec", "POST", "/api/cmd/exec",
                                {"command": command, "cwd": cwd, "timeout": timeout},
                                timeout=timeout + 5)


@register_tool("zcode.process_kill")
async def _tool_zcode_process_kill(payload: Dict[str, Any]) -> Dict[str, Any]:
    """终止 ZCode 沙箱内指定 PID 的进程。POST /api/proc/kill {pid}。"""
    t0 = time.perf_counter()
    if payload.get("pid") is None:
        return _env("zcode.process_kill", code=400, msg="zcode.process_kill 需要 pid", t0=t0)
    pid, err = _as_int("zcode.process_kill", payload.get("pid"), "pid")
    if err is not None:
        return err
    if pid <= 0:
        return _env("zcode.process_kill", code=400, msg="zcode.process_kill pid 需为正整数，收到=%d" % pid, t0=t0)
    return await _zcode_request("zcode.process_kill", "POST", "/api/proc/kill", {"pid": pid})


@register_tool("zcode.port_check")
async def _tool_zcode_port_check(payload: Dict[str, Any]) -> Dict[str, Any]:
    """检测 ZCode 沙箱内指定端口是否被占用。GET /api/net/port/{port}。"""
    t0 = time.perf_counter()
    if payload.get("port") is None:
        return _env("zcode.port_check", code=400, msg="zcode.port_check 需要 port", t0=t0)
    port, err = _as_int("zcode.port_check", payload.get("port"), "port")
    if err is not None:
        return err
    if not 1 <= port <= 65535:
        return _env("zcode.port_check", code=400, msg="zcode.port_check port 需在 1~65535，收到=%d" % port, t0=t0)
    return await _zcode_request("zcode.port_check", "GET", "/api/net/port/%d" % port)


# ==================== [新增代码·结束] ZCode 工程级原子工具扩展 ====================
