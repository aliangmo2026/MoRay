# -*- coding: utf-8 -*-
"""[v5.3] 只读核对白名单测试（用户 2026-10-31 22:25 授权面对应的回归网）。

覆盖四块：
  1. readonly_action_problem：命令级只读判定（动词白名单 / 重定向 / 写词 / python 写调用）；
  2. classify_card_action：卡级分类（判据只读 + 目标无写意图）；
  3. 台账拆分：_verdict_log_append 的 auto 标 + verdict_stats 的 人批/预授权 两个数；
  4. 自动派发闸：_maybe_auto_dispatch 的四道闸（非只读/开关关/超上限/正常）。
不依赖真引擎：_dispatch_card_impl 被 monkeypatch 掉，只验闸门逻辑本身。
"""
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import self_driven as sd  # noqa: E402


# ── 1. 命令级只读判定 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    r"dir D:\MoRay\utf_test.txt",
    r"type D:\MoRay\utf_test.txt",
    r"findstr /c:MoRay D:\MoRay\server\app\self_driven.py",
    r"git -C D:\MoRay\server status --porcelain",
    r"Get-ChildItem D:\MoRay\server\app\*.py",
    r"Test-Path D:\MoRay\utf_test.txt",
])
def test_readonly_ok(cmd):
    assert sd.readonly_action_problem(cmd) == ""


@pytest.mark.parametrize("cmd,reason", [
    ("", "empty"),
    ("echo hi", "not_readonly_verb"),
    (r"dir D:\MoRay > D:\MoRay\out.txt", "redirect"),
    (r"dir D:\MoRay | Set-Content D:\MoRay\x.txt", "write_token:set-content"),
    ("Set-Content D:\\MoRay\\a.txt hi", "not_readonly_verb"),
    ("git commit -m x", "write_token:git commit"),
    ("find /tmp -name x", "not_readonly_verb"),
    # 无绝对路径 = 静态体检 no_path：不危险，但判据落不到具体文件，仍归人批
    ("where python", "no_path"),
    ("dir /tmp", "no_path"),
])
def test_readonly_reject(cmd, reason):
    got = sd.readonly_action_problem(cmd)
    assert got, "应被判为非只读：%r" % (cmd,)
    assert reason in got or got == reason, "原因不符：%s vs %s" % (got, reason)


def test_pipes_and_outfile_are_write():
    # 管道 + Out-File 没有 `>`，只能靠写词表拦（历史漏网形态）
    assert sd.readonly_action_problem(
        r"type D:\MoRay\a.txt | Out-File D:\MoRay\b.txt").startswith("write_token:out-file")


def test_python_write_call_rejected():
    # 无路径的 python -c 先被静态体检拦下（no_path），也是"不白名单"的合法原因
    assert sd.readonly_action_problem('python -c "open(1)"') == "no_path"
    assert sd.readonly_action_problem(
        "python -c \"open(r'D:/x.txt','w').write('1')\"") == "py_write"
    assert sd.readonly_action_problem("python -c \"import shutil\"") == "py_write"


def test_write_token_blocked_even_with_readonly_verb():
    # git 是只读动词白名单内，但 commit 属于写操作 ⇒ 必须拒
    assert sd.readonly_action_problem(
        "git commit -m x") .startswith("write_token:")
    assert sd.readonly_action_problem("python -m pip install x") == "write_token:pip install"


# ── 2. 卡级分类 ─────────────────────────────────────────────────────────────

def test_classify_readonly_card():
    r = sd.classify_card_action("核对 utf_test.txt 的字节数", "文件为 12 字节",
                                r"dir D:\MoRay\utf_test.txt")
    assert r == {"readonly": True, "reason": ""}


def test_classify_write_intent_card():
    r = sd.classify_card_action("修复 utf_test.txt 编码问题", "文件被改写为 UTF-8",
                                r"dir D:\MoRay\utf_test.txt")
    assert r["readonly"] is False
    assert r["reason"].startswith("write_intent:")


def test_classify_write_intent_in_claim_only():
    r = sd.classify_card_action("核对编码", "应当生成文件并落盘",
                                r"dir D:\MoRay")
    assert r["readonly"] is False
    assert "落盘" in r["reason"]


def test_classify_non_readonly_verify():
    r = sd.classify_card_action("核对编码", "文件为 12 字节", r"del D:\MoRay\x.txt")
    assert r["readonly"] is False
    assert r["reason"].startswith("verify:")


# ── 3. 台账拆分 ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sd, "STATE_FILE", tmp_path / "state.json")
    return tmp_path


def test_verdict_log_auto_flag_and_stats(tmp_state):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    state = {"queue": [], "verdict_log": []}
    sd._verdict_log_append(state, "a1", "confirmed", now, auto=True)
    sd._verdict_log_append(state, "h1", "falsified", now, auto=False)
    assert state["verdict_log"][0]["auto"] is True
    assert state["verdict_log"][1]["auto"] is False
    assert sd._save_state(state)

    st = sd.verdict_stats(7)
    assert st["judged"] == 2
    assert st["judged_auto"] == 1
    assert st["judged_human"] == 1
    day = st["day_list"][0]
    assert (day["auto"], day["human"], day["judged"]) == (1, 1, 2)


def test_remeasure_keeps_auto_flag(tmp_state):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    state = {"queue": [], "verdict_log": []}
    sd._verdict_log_append(state, "a1", "unverifiable", now, auto=True)
    sd._verdict_log_append(state, "a1", "confirmed", now, auto=False)
    log = state["verdict_log"]
    assert len(log) == 1 and log[0]["auto"] is True
    assert log[0]["remeasured"] == 1


def test_auto_approved_today_count(tmp_state):
    today = sd._today()
    state = {"queue": [
        {"task_id": "1", "auto_approved": True, "created_at": today + "T10:00:00"},
        {"task_id": "2", "auto_approved": True, "created_at": "2020-01-01T10:00:00"},
        {"task_id": "3"},
    ]}
    assert sd._auto_approved_today(state) == 1


# ── 4. 自动派发四道闸 ───────────────────────────────────────────────────────

def test_auto_dispatch_rejects_non_readonly(tmp_state, monkeypatch):
    called = []
    monkeypatch.setattr(sd, "_dispatch_card_impl",
                        lambda tid, auto=False: called.append(tid) or {"ok": True})
    res = sd._maybe_auto_dispatch({"task_id": "x", "readonly_class": False})
    assert res["reason"] == "not_readonly"
    assert called == []


def test_auto_dispatch_disabled(tmp_state, monkeypatch):
    monkeypatch.setenv(sd._AUTO_APPROVE_ENV, "0")
    called = []
    monkeypatch.setattr(sd, "_dispatch_card_impl",
                        lambda tid, auto=False: called.append(tid) or {"ok": True})
    res = sd._maybe_auto_dispatch({"task_id": "x", "readonly_class": True})
    assert res["reason"] == "disabled"
    assert called == []


def test_auto_dispatch_cap(tmp_state, monkeypatch):
    monkeypatch.delenv(sd._AUTO_APPROVE_ENV, raising=False)
    today = sd._today()
    queue = [{"task_id": str(i), "auto_approved": True,
              "created_at": today + "T09:0%d:00" % i}
             for i in range(sd._AUTO_APPROVE_DAILY_CAP)]
    assert sd._save_state({"queue": queue})
    called = []
    monkeypatch.setattr(sd, "_dispatch_card_impl",
                        lambda tid, auto=False: called.append(tid) or {"ok": True})
    res = sd._maybe_auto_dispatch({"task_id": "x", "readonly_class": True})
    assert res["reason"] == "cap_reached"
    assert called == []


def test_auto_dispatch_happy_path(tmp_state, monkeypatch):
    monkeypatch.delenv(sd._AUTO_APPROVE_ENV, raising=False)
    assert sd._save_state({"queue": []})
    seen = {}

    def fake_impl(tid, auto=False):
        seen["tid"], seen["auto"] = tid, auto
        return {"ok": True, "task_id": tid, "status": "dispatched"}

    monkeypatch.setattr(sd, "_dispatch_card_impl", fake_impl)
    res = sd._maybe_auto_dispatch({"task_id": "x", "readonly_class": True})
    assert res["ok"] is True
    assert seen == {"tid": "x", "auto": True}


def test_auto_approve_enabled_default_true(tmp_state, monkeypatch):
    monkeypatch.delenv(sd._AUTO_APPROVE_ENV, raising=False)
    assert sd._save_state({"queue": []})
    assert sd.auto_approve_enabled() is True
    assert sd._save_state({"queue": [], "auto_approve_readonly": False})
    assert sd.auto_approve_enabled() is False


# ── 5. 派发 prompt 只读护栏 ─────────────────────────────────────────────────

def test_dispatch_prompt_readonly_guard():
    card = {"goal": "核对字节数", "claim": "为 12 字节",
            "verify_cmd": r"dir D:\MoRay\utf_test.txt", "readonly_class": True}
    p = sd._build_dispatch_prompt(card)
    assert "只读约束" in p and "禁止创建/修改/删除" in p
    card2 = dict(card, readonly_class=False)
    assert "只读约束" not in sd._build_dispatch_prompt(card2)


def test_mark_dispatched_auto_flag(tmp_state):
    tid = "card-1"
    assert sd._save_state({"queue": [
        {"task_id": tid, "status": "pending_approval",
         "created_at": sd._today() + "T10:00:00"}]})
    card = sd.mark_dispatched(tid, "eng-1", auto=True)
    assert card["auto_approved"] is True
    assert card["auto_reason"] == "readonly_whitelist"
    assert card["status"] == "dispatched"


def test_dispatch_impl_refuses_auto_on_non_readonly(tmp_state):
    tid = "card-2"
    assert sd._save_state({"queue": [
        {"task_id": tid, "status": "pending_approval", "readonly_class": False}]})
    res = sd._dispatch_card_impl(tid, auto=True)
    assert res == {"ok": False, "reason": "not_readonly"}
