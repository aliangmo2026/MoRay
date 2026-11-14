# -*- coding: utf-8 -*-
"""Guardian Agent —— MCP 工具调用语义安全守门员（2026-11-11 体检夜落地）。

背景：32 个 MCP 工具接入后，物理白名单（MCP_INJECT_FORBIDDEN_TOOLS 只管"注不注入"）
管不住显式 API（POST /api/mcp/gateway/call 可以点名任何注册工具名）。物理层之外
需要语义层防线：高危工具/危险参数 → qwen2.5:1.5b 本地小模型语义审查，判 unsafe 拦截。

三级防线（算力卫生：低危零模型开销）：
  Tier 0 低危直通 —— 工具不在高危名单、参数无危险特征 → 直接放行，模型零调用；
  Tier 1 确定性拦截 —— 参数命中"不可辩驳"的毁灭性特征（rm -rf 盘根/通配、format 盘符、
          mkfs/dd、reg delete 等）→ 不依赖小模型直接拦（1.5B 判断力有限，毁灭性操作
          必须确定性拦死）；这一层保证"rm -rf D:\\"必拦；
  Tier 2 语义审查 —— 高危工具或中度危险参数 → 调 1.5B 审查；判 unsafe 拦截；
          [体检修复 2026-11-14] 判定不可得（超时/异常/不可解析）时**按工具风险分级**：
          高危工具 fail-close（拦截，不留"守卫失明窗口"）；非高危软特征 fail-open
          （模型故障不反噬正常业务）。

纪律：
  * 主链路零阻塞：Tier 2 总预算 GUARDIAN_TIMEOUT（默认 15s），超时按上面分级处理；
  * 算力显存卫生：keep_alive=0（不占显存；审查是罕见事件，冷载几秒可接受）；
  * 决策全量落 logs/guardian.log（moray 根 logger 只放 ERROR 的教训：模块自挂 handler）；
  * 模型名/开关/超时/硬规则开关全部 env 可覆写（MORAY_GUARDIAN_*），不改 config.py。
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx

from . import config

logger = logging.getLogger("moray.guardian")

#: [体检修复2 2026-11-14] 单线程后台写盘池：审查热路径（async check_tool_safety）的
#: 日志落盘离环——emit 只做 submit（µs 级），磁盘 open/write 在后台串行执行。
_log_writer_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="guardian-log")


class _GuardianFileHandler(logging.Handler):
    """logs/guardian.log（moray 根 logger 只放 ERROR 的教训：模块自挂 handler）。
    [体检修复2 2026-11-14] emit 改为向 _log_writer_pool 投递，不再在事件循环上做
    同步 open/append/close（体检静态审查 P2：每次工具调用至少一次同步磁盘 IO）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    @staticmethod
    def _write_line(line: str) -> None:
        try:
            base = Path(__file__).resolve().parents[2] / "logs"
            base.mkdir(parents=True, exist_ok=True)
            with open(base / "guardian.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001 - 日志面缺失不扰主链路
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _log_writer_pool.submit(self._write_line, self.format(record))
        except Exception:  # noqa: BLE001 - 日志面缺失不扰主链路
            pass


def _install_guardian_logger() -> None:
    if any(isinstance(h, _GuardianFileHandler) for h in logger.handlers):
        return
    logger.setLevel(logging.INFO)
    logger.addHandler(_GuardianFileHandler())
    _console = logging.StreamHandler()
    _console.setLevel(logging.WARNING)
    _console.setFormatter(logging.Formatter("[guardian] %(message)s"))
    logger.addHandler(_console)


_install_guardian_logger()

# ---------------- 配置（env 可覆写，不改 config.py） ----------------

GUARDIAN_MODEL = os.environ.get("MORAY_GUARDIAN_MODEL", "qwen2.5:1.5b")
GUARDIAN_ENABLED = os.environ.get("MORAY_GUARDIAN_ENABLED", "1").strip().lower() \
    in ("1", "true", "yes", "on")
GUARDIAN_TIMEOUT = float(os.environ.get("MORAY_GUARDIAN_TIMEOUT", "15"))
#: Tier 1 硬规则开关（紧急逃生门：env 一键退回纯语义审查）
GUARDIAN_HARD_BLOCK = os.environ.get("MORAY_GUARDIAN_HARD_BLOCK", "1").strip().lower() \
    in ("1", "true", "yes", "on")

# ---------------- 高危工具与危险特征 ----------------

#: 高危工具名单（Tier 2 触发器之一）。与 config.MCP_INJECT_FORBIDDEN_TOOLS
#: （execute_plan / refactor_smart）同源，另加已知"会写盘/会跑代码"的器官工具；
#: 命名模式兜底见 HIGH_RISK_NAME_RE（exec/write/delete 系）。
HIGH_RISK_TOOLS = frozenset({
    "execute_plan", "refactor_smart", "execute_safe_python", "scribe_doc",
})

#: 高危工具命名模式兜底（防显式 API 点名任意工具名绕过名单）
HIGH_RISK_NAME_RE = re.compile(
    r"(exec|run_|shell|terminal|_write|write_|_delete|delete_|remove|"
    r"refactor|patch|deploy|commit|kill|restart|shutdown|format)", re.I)

# ---- Tier 1 硬规则（确定性拦截，不依赖小模型） ----

#: 递归强删特征：rm -r/-rf/--recursive、rm -fr（f 在前变体，体检修复 2026-11-14：
#: 旧式仅要求旗标 r 开头，`rm -fr` 漏拦掉进 Tier2 慢路径）、del /q /s、rd /s、rmdir /s、
#: Remove-Item -Recurse
_RECURSIVE_DELETE_RE = re.compile(
    r"(\brm\s+(?:-{1,2}[\w-]+\s+)*-{1,2}r[a-z]*f?[a-z]*\b"
    r"|\brm\s+(?:-{1,2}[\w-]+\s+)*-{1,2}fr[a-z]*\b"
    r"|\bdel\s+/[a-z]\b|\b(?:rd|rmdir)\s+/s\b"
    r"|\bremove-item\b.*-\w*recurse)", re.I)

#: 盘根/家目录/顶层通配目标（递归强删 + 这些目标 = 毁灭，Tier 1 直接拦）
_ROOT_TARGET_RE = re.compile(
    r"^(?:[a-z]:[\\/]?|[/~]+|/[a-z]\*?|[a-z]:[\\/]\*?|[a-z]:?[/\\]\*)$", re.I)

#: 不可辩驳毁灭特征（无需目标分析，命中即拦）
_CATASTROPHIC_RE = re.compile(
    r"(\bformat\s+[a-z]:|\bmkfs\b|\bdd\s+if=.*of=/dev/|\breg\s+delete\b|"
    r"\bdiskpart\b|\bcipher\s+/w\b|\bshutdown\s+(/[sr]|-[sf])?\s*($|-t\s*0))", re.I)

#: Tier 2 软特征：中度危险（触发语义审查，不直接拦）
#: [体检修复 2026-11-14] 新增通用"管道喂解释器"特征（此前只认 curl/wget 前缀，
#: `echo <b64> | base64 -d | sh` 这类变形在低危工具名下整体穿透 Tier0 —— 体检 E2E 实锤）
_SOFT_DANGER_RE = re.compile(
    r"(\brm\b|\bdel\s|\brd\s|\brmdir\b|\bremove-item\b|\bunlink\b"
    r"|\breg\s+(add|import)\b|\bregedit\b"
    r"|\bformat\b|\bmkfs\b|\bdd\s+if=|\bdiskpart\b|\bcipher\b"
    r"|\btaskkill\b|\bstop-process\b"
    r"|\bsudo\b|\bchmod\b|\bchown\b"
    r"|\biex\b|\binvoke-expression\b|\bpowershell\s+-enc"
    r"|curl[^\n]*\|\s*(ba)?sh|wget[^\n]*\|\s*(ba)?sh"
    r"|\|\s*(ba)?sh\b|\|\s*python3?\b|\|\s*pwsh\b|\|\s*powershell\b)", re.I)


def _iter_strings(obj: Any):
    """递归抽取参数里所有字符串（含嵌套 dict/list），供危险特征匹配。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _matched_fragment(re_obj, text: str) -> str:
    m = re_obj.search(text)
    return m.group(0)[:120] if m else ""


def _root_target_present(blob: str) -> bool:
    """递归强删命令的删除目标是否盘根/家目录/顶层通配（"rm -rf D:\\"的真身）。"""
    m = _RECURSIVE_DELETE_RE.search(blob)
    if not m:
        return False
    seg = re.split(r"[&|;]", blob[m.end():])[0]
    toks = [t.strip("\"'") for t in seg.split()]
    return any(_ROOT_TARGET_RE.match(t) for t in toks)


#: base64 特征 token（≥8 位合法 b64 字符集；误报由"解码成功 + 可打印"双重过滤兜底。
#: 阈值不能高——"rm -rf C:/" 的 b64 只有 14 字符，{16,} 会漏掉短致命载荷）
_B64_TOKEN_RE = re.compile(r"[A-Za-z0-9+/]{8,}={0,2}")
_B64_MAX_TOKENS = 8
_B64_MAX_CHARS = 4096
#: 零宽/软连字符（NFKC 不归一、命令里永远无意义，纯绕过垫字）
_ZWSP_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")


def _decoded_base64_payloads(text: str) -> str:
    """[体检修复 2026-11-14] 就地解码扫描文本里的 base64 载荷（体检 E2E 实锤：
    `echo <b64> | base64 -d | sh` 在低危工具名下 Tier0 0ms 直通——守卫看不懂变形）。
    utf-8 优先；utf-16-le 走 ASCII 位模式启发（powershell -enc 惯例）。
    解码失败/不可打印一律静默跳过，解码面任何异常不反噬判定。"""
    out = []
    try:
        for tok in _B64_TOKEN_RE.findall(text)[:_B64_MAX_TOKENS]:
            padded = tok + "=" * (-len(tok) % 4)
            try:
                raw = base64.b64decode(padded, validate=True)
            except Exception:  # noqa: BLE001 - 非 b64 token 静默跳过
                continue
            dec = None
            try:
                d8 = raw.decode("utf-8")
                if d8.isprintable():
                    dec = d8
            except Exception:  # noqa: BLE001 - 非 utf-8 文本
                dec = None
            if dec is None:
                try:
                    if len(raw) >= 4 and raw[1::2].count(0) >= len(raw) // 4:
                        d16 = raw.decode("utf-16-le")
                        if d16.isprintable():
                            dec = d16
                except Exception:  # noqa: BLE001 - 非 utf-16 文本
                    dec = None
            if dec:
                out.append(dec.strip()[:_B64_MAX_CHARS])
    except Exception:  # noqa: BLE001 - 解码面任何异常不反噬判定
        pass
    return "\n".join(out)


def _augment_scan_text(blob: str) -> str:
    """[体检修复 2026-11-14] 三级共享扫描面增强：
      1. Unicode NFKC 规范化（全角 ｒｍ/-ｒｆ 等同形字符归一，体检单元矩阵实证
         全角载荷此前只能靠不可靠的 Tier2 兜底）；
      2. 零宽字符剥离（U+200B/200C/200D/BOM/软连字符——NFKC 不归一的绕过垫字）；
      3. base64 就地解码追加（解码产物一并进 Tier0/Tier1 匹配面）。
    任何异常回退原始 blob（守卫自身故障绝不反噬主链路）。"""
    try:
        text = unicodedata.normalize("NFKC", str(blob or ""))
        text = _ZWSP_RE.sub("", text)
        extra = _decoded_base64_payloads(text)
        if extra:
            text = text + "\n" + extra
        return text
    except Exception:  # noqa: BLE001
        return str(blob or "")


def _parse_verdict(raw: str) -> Tuple[Optional[bool], str]:
    """小模型输出 → (safe, reason)；解析不出 → (None, "")，交给上层 fail-open。"""
    t = str(raw or "").strip()
    if not t:
        return None, ""
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t).strip()
    data = None
    try:
        data = json.loads(t)
    except Exception:  # noqa: BLE001 - 散文包 JSON 截取兜底
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            try:
                data = json.loads(t[s:e + 1])
            except Exception:  # noqa: BLE001
                data = None
    if isinstance(data, dict) and isinstance(data.get("safe"), bool):
        return bool(data["safe"]), str(data.get("reason") or "")[:300]
    return None, ""


async def _llm_verdict(tool_name: str, args_blob: str) -> Tuple[bool, str]:
    """1.5B 小模型语义审查（/api/chat ollama 协议；keep_alive=0 算力卫生）。
    返回 (safe, reason)；任何失败向上抛，由 check_tool_safety 统一 fail-open。"""
    prompt = (
        "你是系统安全守卫。用户请求调用工具 %s，参数为 %s。请判断该操作是否有"
        "破坏系统、删除核心文件或注入恶意的风险。\n"
        "判据：只有毁灭性操作（格式化盘符、删除盘根或系统目录、批量强删、改注册表、"
        "注入恶意代码、外传数据）才判 unsafe；正常的文件编辑、代码修改、文档更新等"
        "合法任务判 safe，不确定时才倾向 unsafe。\n"
        "示例：\n"
        '{"safe": false, "reason": "删除系统目录属毁灭性操作"}\n'
        '{"safe": true, "reason": "正常的文档更新任务"}\n'
        "只返回 JSON: "
        '{"safe": true/false, "reason": "xxx"}'
        % (tool_name, args_blob)
    )
    payload = {
        "model": GUARDIAN_MODEL,
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 200},
        "messages": [{"role": "user", "content": prompt}],
    }
    url = str(config.OLLAMA_BASE_URL or "").rstrip("/") + "/api/chat"
    async with httpx.AsyncClient(timeout=GUARDIAN_TIMEOUT) as c:
        r = await c.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError("guardian model HTTP %s" % r.status_code)
    content = str(((r.json() or {}).get("message") or {}).get("content") or "")
    safe, reason = _parse_verdict(content)
    if safe is None:
        raise ValueError("guardian verdict unparseable: %s" % content[:120])
    return safe, reason


async def check_tool_safety(tool_name: str, args: dict) -> Tuple[bool, str]:
    """守门员主入口：工具执行前调用。返回 (放行?, 理由)。

    低危直通零模型开销；硬规则确定性拦截。
    [体检修复 2026-11-14] 语义审查判定不可得（超时/异常/不可解析）时按工具风险分级：
    高危工具 fail-close（拦截，绝不给 execute/refactor 系留"守卫失明窗口"）；
    非高危软特征保持 fail-open（模型故障不反噬正常业务）。顶层异常仍 fail-open
    （守卫自身代码故障绝不 brick MCP 主链路——Tier2 的模型失败不会走到顶层）。"""
    t0 = time.time()
    try:
        if not GUARDIAN_ENABLED:
            return True, "guardian disabled (env)"
        if not isinstance(args, dict):
            args = {"_raw": str(args)[:500]}
        blob = " | ".join(_iter_strings(args))[:1500]
        # [体检修复 2026-11-14] 三级共享扫描面：NFKC 规范化 + 零宽剥离 + b64 解码追加
        scan = _augment_scan_text(blob)
        high_risk = bool(tool_name in HIGH_RISK_TOOLS) \
            or bool(HIGH_RISK_NAME_RE.search(tool_name))
        # ---- Tier 0：低危直通（零模型开销） ----
        if not high_risk and not _SOFT_DANGER_RE.search(scan):
            return True, "low-risk passthrough"
        # ---- Tier 1：硬规则确定性拦截（不依赖小模型） ----
        if GUARDIAN_HARD_BLOCK and (
                _CATASTROPHIC_RE.search(scan)
                or (_RECURSIVE_DELETE_RE.search(scan) and _root_target_present(scan))):
            frag = _matched_fragment(_CATASTROPHIC_RE, scan) \
                or "recursive delete on root/wildcard"
            logger.info("HARD-BLOCK %s %.0fms frag=%s",
                        tool_name, (time.time() - t0) * 1000, frag[:80])
            return False, "guardian hard-block: %s" % frag
        # ---- Tier 2：1.5B 语义审查（判定不可得时按风险分级） ----
        try:
            safe, reason = await asyncio.wait_for(
                _llm_verdict(tool_name, scan[:600]),
                timeout=GUARDIAN_TIMEOUT + 5.0)
            logger.info("SEMANTIC %s safe=%s %.0fms reason=%s",
                        tool_name, safe, (time.time() - t0) * 1000, reason[:80])
            if not safe:
                return False, "guardian semantic block: %s" % (reason[:200] or "unsafe")
            return True, "guardian semantic pass: %s" % (reason[:120] or "ok")
        except asyncio.TimeoutError:
            if high_risk:
                logger.warning("FAIL-CLOSE %s %.0fms reason=model timeout",
                               tool_name, (time.time() - t0) * 1000)
                return False, ("guardian fail-close: semantic review unavailable "
                               "(model timeout >%.0fs)" % GUARDIAN_TIMEOUT)
            return True, "guardian fail-open: model timeout (>%.0fs)" % GUARDIAN_TIMEOUT
        except Exception as e:  # noqa: BLE001 - Tier-2 失败按工具风险分级
            if high_risk:
                logger.warning("FAIL-CLOSE %s %.0fms reason=%s: %s",
                               tool_name, (time.time() - t0) * 1000,
                               type(e).__name__, str(e)[:120])
                return False, ("guardian fail-close: semantic review unavailable "
                               "(%s: %s)" % (type(e).__name__, str(e)[:120]))
            return True, "guardian fail-open: %s: %s" % (type(e).__name__, str(e)[:160])
    except Exception as top:  # noqa: BLE001 - 顶层 fail-open
        try:
            logger.warning("GUARDIAN top-level fail-open: %s: %s",
                           type(top).__name__, str(top)[:160])
        except Exception:  # noqa: BLE001
            pass
        return True, "guardian fail-open: %s: %s" % (type(top).__name__, str(top)[:160])
