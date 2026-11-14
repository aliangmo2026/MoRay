# -*- coding: utf-8 -*-
r"""Guardian 绕过面回归测试（体检修复 2026-11-14）。

体检实证的三个绕过面 + 一个 Tier1 缺口，修复后必须确定性拦死（全部离线、零模型依赖）：
  1. `rm -fr`（旗标 f 在 r 前）旧式漏拦 → Tier1 补第二旗标序（fr，勿伤 --force）；
  2. 全角 ｒｍ/-ｒｆ → NFKC 规范化后进 Tier1；
  3. 零宽垫字 r+\u200b+m → 剥零宽后进 Tier1；
  4. `echo <b64> | base64 -d | sh`（低危工具名）→ b64 就地解码 + 通用管道特征：
     解码出 rm -rf C:/ 直接 Tier1；无害解码走软特征进 Tier2（不再是 Tier0 直通）。
另含 utf-16-le（powershell -enc 惯例）解码与安全对照（echo hello 仍 Tier0 直通）。

跑法：D:/python3.12/python.exe -m pytest tests/test_guardian_bypass.py -v
"""
import asyncio
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import guardian  # noqa: E402

if not guardian.GUARDIAN_HARD_BLOCK:
    pytest.skip("MORAY_GUARDIAN_HARD_BLOCK 被关闭，硬规则用例不适用", allow_module_level=True)

B64_RM_RF = base64.b64encode("rm -rf C:/".encode("utf-8")).decode()
B64_RM_RF_UTF16 = base64.b64encode("rm -rf C:/".encode("utf-16-le")).decode()
B64_ECHO = base64.b64encode("echo hello".encode("utf-8")).decode()


def _check(tool, args):
    return asyncio.run(guardian.check_tool_safety(tool, args))


@pytest.fixture
def model_never_called(monkeypatch):
    async def _must_not_run(tool_name, args_blob):
        raise AssertionError("硬规则命中时绝不允许调模型")

    monkeypatch.setattr(guardian, "_llm_verdict", _must_not_run)


def test_rm_fr_flag_order_hard_block(model_never_called):
    ok, reason = _check("execute", {"command": "rm -fr C:/"})
    assert ok is False, "rm -fr（f 在前）必须 Tier1 确定性拦截"
    assert "hard-block" in reason


def test_rm_force_still_not_recursive(monkeypatch):
    async def _pass(tool_name, args_blob):
        return True, "ok"

    monkeypatch.setattr(guardian, "_llm_verdict", _pass)
    ok, reason = _check("execute", {"command": "rm --force D:/data/report.txt"})
    assert ok is True, "--force 含 f..r 但不是递归删，不得误触发 Tier1"
    assert "hard-block" not in reason
    assert "semantic" in reason, "rm --force 应走软特征 → Tier2（模型放行）"


def test_fullwidth_nfkc_hard_block(model_never_called):
    ok, reason = _check("execute", {"command": "ｒｍ -ｒｆ C:/"})
    assert ok is False, "全角同形字 NFKC 归一后必须 Tier1 确定性拦截"
    assert "hard-block" in reason


def test_zwsp_hard_block(model_never_called):
    ok, reason = _check("execute", {"command": "r​m -rf C:/"})  # r 与 m 之间实为 U+200B
    assert ok is False, "零宽垫字剥离后必须 Tier1 确定性拦截"
    assert "hard-block" in reason


def test_b64_pipe_decoded_hard_block(model_never_called):
    cmd = "echo " + B64_RM_RF + " | base64 -d | sh"
    ok, reason = _check("search_web", {"query": "moray-hc", "command": cmd})
    assert ok is False, "b64 解码产物 rm -rf C:/ 必须进 Tier1（此前 Tier0 0ms 直通）"
    assert "hard-block" in reason


def test_powershell_enc_utf16_decoded_hard_block(model_never_called):
    cmd = "powershell -enc " + B64_RM_RF_UTF16
    ok, reason = _check("search_web", {"query": "moray-hc", "command": cmd})
    assert ok is False, "powershell -enc（utf-16-le）解码产物必须进 Tier1"
    assert "hard-block" in reason


def test_b64_pipe_harmless_now_reaches_tier2(monkeypatch):
    async def _pass(tool_name, args_blob):
        return True, "ok"

    monkeypatch.setattr(guardian, "_llm_verdict", _pass)
    cmd = "echo " + B64_ECHO + " | base64 -d | sh"
    ok, reason = _check("search_web", {"query": "moray-hc", "command": cmd})
    assert ok is True
    assert "passthrough" not in reason, "通用管道特征必须触发软特征（不再 Tier0 直通）"
    assert "semantic" in reason


def test_high_risk_fail_close_when_model_dead(monkeypatch):
    async def _dead(tool_name, args_blob):
        raise RuntimeError("ollama down (simulated)")

    monkeypatch.setattr(guardian, "_llm_verdict", _dead)
    ok, reason = _check("execute", {"command": "echo hello"})
    assert ok is False, "高危工具 + 审查不可得 → fail-close"
    assert "fail-close" in reason


def test_regression_plain_variants_still_hard_block(model_never_called):
    for cmd in ("rm -rf C:/", "rm -rf D:\\", "rm -rf /", "rm -r -f C:/", "rd /s /q C:\\"):
        ok, reason = _check("execute", {"command": cmd})
        assert ok is False, "回归：%s 必须仍被 Tier1 拦截" % cmd
        assert "hard-block" in reason


def test_regression_safe_echo_tier0(model_never_called):
    ok, reason = _check("search_web", {"query": "moray-hc", "command": "echo hello"})
    assert ok is True
    assert "passthrough" in reason


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
