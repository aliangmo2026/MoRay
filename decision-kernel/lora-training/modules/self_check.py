# -*- coding: utf-8 -*-
"""L7.6 自检重试器：每步执行后判定 success/failed + 失败原因 + 修正参数。

判定策略（两级，保证模型不可用时闭环仍能跑完）：
  1) 规则前置层：工具信封 ok=False → 直接 failed（原因=error.message）；
     file/argus/zcode 有"结构可验证"字段时先做廉价校验（退出码/写回内容非空）。
  2) 小模型判断层：把"步骤预期 + 实际结果"喂给 Ollama 的 moray-laya-final
     （与主服务 /api/decide 同一模型、同一单槽位，不引入第二个模型互踢问题，
     见 [[moray-argus-link-and-ollama-single-slot]]）。要求只输出 JSON：
        {"success": true/false, "reason": "...", "fix": "..."}
     解析失败/超时 → 退回规则结论（ok=True 即 success），并如实记 mode。

修正策略 revise_input：失败时把"原输入 + 失败原因 + 修改建议"交小模型改写出新
输入 JSON；模型不可用则按 fix 文本做兜底修补（如 zcode 失败重试原代码、
file 失败重试同参数）。是否值得重试由 executor 决定（max_retry 封顶）。

开关：LAYA_SELFCHECK_LLM=0 关闭模型判断（纯规则，测试/降级用，默认开）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import httpx
import json_repair

# [2026-10-01 任务A v2] A2 观测：补 logger，静默降级分支全部落 WARNING（返回值语义不变）
logger = logging.getLogger("laya.self_check")

OLLAMA_URL = os.environ.get("LAYA_JUDGE_URL", "http://localhost:11434/api/chat")
JUDGE_MODEL = os.environ.get("LAYA_JUDGE_MODEL", "moray-laya-final")
JUDGE_ENABLED = os.environ.get("LAYA_SELFCHECK_LLM", "1") != "0"
_JUDGE_TIMEOUT = float(os.environ.get("LAYA_JUDGE_TIMEOUT", "8"))

_JUDGE_SYSTEM = """你是任务执行结果自检器。输入给你"步骤预期"和"实际执行结果"。
只输出纯JSON，不要任何解释、不要代码块标记。格式严格为：
{"success": true或false, "reason": "一句话", "fix": "若失败给出具体修改建议，成功则空字符串"}
判断标准：结果是否真实满足预期；空结果、报错、答非所问都算失败。"""

_REVISE_SYSTEM = """你是任务参数修正器。给定步骤的原输入、失败原因、修改建议，
请输出修正后的新输入。只输出纯JSON对象（与原输入同构的字段集），不要解释、不要代码块。"""


def _rule_verdict(result: Dict[str, Any]) -> Dict[str, Any]:
    """规则前置层：信封硬信号直接判，不浪费模型调用。"""
    if not result.get("ok"):
        err = result.get("error") or {}
        return {"success": False,
                "reason": "工具调用失败：code=%s %s" % (err.get("code"), err.get("message", "")),
                "fix": "检查该工具服务是否在线/入参是否正确",
                "mode": "rule"}
    data = result.get("data")
    # 结构可验证的廉价信号
    if isinstance(data, dict):
        if data.get("code") not in (None, "OK", 0, "ok") and "error" in json.dumps(data, ensure_ascii=False)[:2000].lower():
            return {"success": False, "reason": "工具应答体内含错误码", "fix": "按应答错误码修正入参", "mode": "rule"}
        if result.get("tool") == "file" and str(data.get("op", "")) == "read" and not data.get("content"):
            return {"success": False, "reason": "读取结果为空", "fix": "确认路径/编码后重试", "mode": "rule"}
        if result.get("tool") == "zcode" and data.get("exit_code") not in (None, 0):
            return {"success": False, "reason": "脚本退出码=%s" % data.get("exit_code"),
                    "fix": "根据 stderr/输出修代码后重试", "mode": "rule"}
    return {"success": None, "reason": "", "fix": "", "mode": "rule"}  # 规则层放行→需模型判


async def _llm_json(system: str, user: str, fmt: str | None = None) -> Dict[str, Any] | None:
    """小模型出一段 JSON；任何异常/解析失败返回 None（调用方走兜底）。

    [promptfix 2026-10-03] fmt="json" 启用 Ollama format=json 语法约束（仅 executor
    decision 步传入，其余调用方默认 None 行为不变）：1.5b 在长上下文 user（文件内容+
    stdout/stderr+清单多重注入块）下会弃守 JSON 契约直接输出裸围栏代码（e2e 实证
    6/6 复现、措辞提醒与 num_ctx 提升均无效），语法约束使该形态在解码层不可能出现。
    """
    try:
        payload = {"model": JUDGE_MODEL, "stream": False, "temperature": 0.0,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        if fmt:
            payload["format"] = fmt
        async with httpx.AsyncClient(timeout=_JUDGE_TIMEOUT) as c:
            r = await c.post(OLLAMA_URL, json=payload)
            if r.status_code != 200:
                logger.warning("[self_check] Ollama 非 200：status=%s model=%s", r.status_code, JUDGE_MODEL)
                return None
            raw = r.json().get("message", {}).get("content", "")
            if not raw:
                logger.warning("[self_check] 应答为空：model=%s", JUDGE_MODEL)
                return None
            try:
                fixed = json_repair.repair_json(raw)  # 主服务同款容错
                # [M3b-shimfix 2026-10-02] 两态兼容：真库返回 JSON 字符串 → json.loads；历史 shim 返回 dict → 直返
                if isinstance(fixed, dict):
                    return fixed
                return json.loads(fixed)
            except (ValueError, TypeError):
                logger.warning("[self_check] JSON 解析失败：raw=%r", raw[:200])
                return None
    except Exception as e:  # noqa: BLE001 - 判断层失败必须静默降级，不许炸闭环
        logger.warning("[self_check] 调用异常：%s: %s", type(e).__name__, str(e)[:200])
        return None


async def check(step: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """单步自检：返回 {"success": bool, "reason": str, "fix": str, "mode": str}。"""
    rv = _rule_verdict(result)
    if rv["success"] is not None:      # 规则层已有硬结论（失败信号）
        return rv
    if not JUDGE_ENABLED:
        return {"success": True, "reason": "规则层通过（模型判断已关闭）", "fix": "", "mode": "rule_only"}
    user = json.dumps({"step_id": step.get("step_id"), "预期": step.get("expected_output"),
                       "工具": result.get("tool"),
                       "实际结果": (result.get("data") or "") if isinstance(result.get("data"), str)
                                  else _shrink(result.get("data"))},
                      ensure_ascii=False)[:6000]
    verdict = await _llm_json(_JUDGE_SYSTEM, user)
    if verdict is None or "success" not in verdict:
        return {"success": True, "reason": "模型判断不可用，按规则层通过处理", "fix": "", "mode": "rule_fallback"}
    return {"success": bool(verdict["success"]), "reason": str(verdict.get("reason", ""))[:300],
            "fix": str(verdict.get("fix", ""))[:500], "mode": "llm"}


async def revise_input(step: Dict[str, Any], verdict: Dict[str, Any]) -> Dict[str, Any]:
    """失败→修正该步输入。模型可用时重写参数；否则原样返回（executor 仍会重试）。"""
    base = dict(step.get("input") or {})
    if not JUDGE_ENABLED:
        return base
    user = json.dumps({"原输入": _shrink(base), "失败原因": verdict.get("reason", ""),
                       "修改建议": verdict.get("fix", ""),
                       "步骤描述": step.get("description", "")}, ensure_ascii=False)[:4000]
    new = await _llm_json(_REVISE_SYSTEM, user)
    if isinstance(new, dict) and new:
        return new
    return base


def _shrink(obj: Any, limit: int = 3000) -> Any:
    """截断大结果，防止 prompt 爆掉（模型只有 150~512 输出预算，输入也要克制）。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:limit]
