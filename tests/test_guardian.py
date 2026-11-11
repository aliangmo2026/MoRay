# -*- coding: utf-8 -*-
r"""Guardian Agent 单元测试（2026-11-11）。

验证口径（离线确定性，不依赖 Ollama——语义审查层 mock 掉）：
  1. 低危直通：search_web 普通查询 → 放行，且 1.5B 模型零调用；
  2. 硬规则：rm -rf D:\ → 确定性拦截（不依赖模型）；
  3. 高危工具 + 语义 unsafe → 拦截；语义 safe → 放行；
  4. fail-open：模型抛异常 / 输出不可解析 → 放行（守卫绝不阻塞主链路）。

跑法：D:/python3.12/python.exe -m pytest tests/test_guardian.py -v
（cwd=仓库根；也可 python tests/test_guardian.py 直跑）
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import guardian  # noqa: E402


def _fake_verdict(safe: bool, reason: str):
    """造一个替代 _llm_verdict 的 async 假审查器。"""

    async def _fake(tool_name, args_blob):
        return safe, reason

    return _fake


# ---------------- 1. 低危直通（零模型开销） ----------------

def test_low_risk_passthrough_no_llm(monkeypatch):
    called = {"n": 0}

    async def _spy(tool_name, args_blob):
        called["n"] += 1
        return True, "should not be called"

    monkeypatch.setattr(guardian, "_llm_verdict", _spy)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "search_web", {"query": "MoRay MCP 网关冒烟"}))
    assert ok is True
    assert called["n"] == 0, "低危工具绝不能发起模型调用"
    assert "low-risk" in reason or "passthrough" in reason


# ---------------- 2. 硬规则：rm -rf D:\ 确定性拦截 ----------------

def test_hard_block_rm_rf_drive_root(monkeypatch):
    async def _must_not_run(tool_name, args_blob):
        raise AssertionError("硬规则命中时绝不允许调模型")

    monkeypatch.setattr(guardian, "_llm_verdict", _must_not_run)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "list_files", {"path": "D:\\", "command": "rm -rf D:\\"}))
    assert ok is False, "rm -rf D:\\ 必须被确定性拦截"
    assert "hard-block" in reason


def test_hard_block_format_disk(monkeypatch):
    async def _must_not_run(tool_name, args_blob):
        raise AssertionError("硬规则命中时绝不允许调模型")

    monkeypatch.setattr(guardian, "_llm_verdict", _must_not_run)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "list_files", {"command": "format D:"}))
    assert ok is False
    assert "hard-block" in reason


# ---------------- 3. 高危工具 × 语义审查 ----------------

def test_high_risk_tool_llm_unsafe_blocks(monkeypatch):
    monkeypatch.setattr(guardian, "_llm_verdict", _fake_verdict(False, "计划会删除用户目录"))
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "execute_plan", {"plan": "重建 docs 目录"}))
    assert ok is False
    assert "semantic block" in reason
    assert "删除用户目录" in reason


def test_high_risk_tool_llm_safe_passes(monkeypatch):
    monkeypatch.setattr(guardian, "_llm_verdict", _fake_verdict(True, "常规文档操作"))
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "execute_plan", {"plan": "重建 docs 目录"}))
    assert ok is True
    assert "semantic pass" in reason


# ---------------- 4. fail-open（守卫绝不阻塞主链路） ----------------

def test_fail_open_on_model_exception(monkeypatch):
    async def _broken(tool_name, args_blob):
        raise RuntimeError("ollama down (simulated)")

    monkeypatch.setattr(guardian, "_llm_verdict", _broken)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "execute_plan", {"plan": "whatever"}))
    assert ok is True, "模型挂了必须 fail-open 放行"
    assert "fail-open" in reason


def test_fail_open_on_garbage_verdict(monkeypatch):
    async def _garbage(tool_name, args_blob):
        raise ValueError("guardian verdict unparseable: 之类散文")

    monkeypatch.setattr(guardian, "_llm_verdict", _garbage)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "execute_safe_python", {"code": "print(1)"}))
    assert ok is True
    assert "fail-open" in reason


def test_fail_open_on_disabled(monkeypatch):
    monkeypatch.setattr(guardian, "GUARDIAN_ENABLED", False)
    ok, reason = asyncio.run(guardian.check_tool_safety(
        "execute_plan", {"plan": "x"}))
    assert ok is True
    assert "disabled" in reason


# ---------------- 5. 解析器单元 ----------------

def test_parse_verdict_plain_fenced_junk():
    assert guardian._parse_verdict('{"safe": false, "reason": "危险"}') == (False, "危险")
    assert guardian._parse_verdict('```json\n{"safe": true, "reason": "ok"}\n```') == (True, "ok")
    assert guardian._parse_verdict("完全不是 JSON 的散文输出") == (None, "")


# ---------------- 直跑入口（与 test_skill_engine_e2e 同款习惯） ----------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
