# -*- coding: utf-8 -*-
"""self_driven v5 单测：验证执行器（verify_cmd 真的被执行）+ 判定口径 + 补跑候选。

背景：v4 之前 verify_cmd 只被当文本体检，后端零处执行它 —— 闭环的"观测/误差"
两环是空的。本文件锁死 v5 的三件事：
  1. 命令真的跑、exit code 真的被抓；
  2. 判定口径不骗自己（命令本身坏 → unverifiable，不冒充 falsified）；
  3. 结论真的写回卡（verified_at/verdict/result_note），且幂等、不重复烧命令。

跑法：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_self_driven_verify.py -q
"""
import json
import sys
import time

import pytest

sys.path.insert(0, r"D:\MoRay")

from server.app import self_driven as sd  # noqa: E402

PY = sys.executable


# ---------------- 夹具：状态文件隔离 ----------------

@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """把模块级 STATE_FILE 指到临时文件（绝不打真实队列）。"""
    f = tmp_path / "self_driven_state.json"
    f.write_text(json.dumps({"daily": {}, "queue": []}, ensure_ascii=False),
                 encoding="utf-8")
    monkeypatch.setattr(sd, "STATE_FILE", f)
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    return f


def _seed(state_file, card):
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["queue"].append(card)
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return card


def _card(**kw):
    base = {"task_id": "t-1", "status": "done", "goal": "g",
            "verify_cmd": '%s -c "print(1)"' % PY, "created_at": "2026-10-31T18:00:00"}
    base.update(kw)
    return base


# ---------------- 1. 命令真的被执行 ----------------

def test_run_verify_exit_zero():
    obs = sd.run_verify_command('%s -c "print(42)"' % PY)
    assert obs["ok"] is True
    assert obs["exit"] == 0
    assert "42" in obs["stdout"]


def test_run_verify_nonzero_exit_is_captured():
    obs = sd.run_verify_command('%s -c "raise SystemExit(3)"' % PY)
    assert obs["ok"] is False
    assert obs["exit"] == 3


def test_run_verify_timeout_is_marked():
    t0 = time.time()
    obs = sd.run_verify_command('%s -c "import time; time.sleep(10)"' % PY, timeout=1)
    assert obs["timed_out"] is True
    assert obs["exit"] is None
    assert time.time() - t0 < 8  # 真的被掐断，不是等满 10s


def test_run_verify_empty_and_garbage_never_raise():
    assert sd.run_verify_command("")["error"] == "empty"
    for weird in (None, 0, [], {"a": 1}):
        obs = sd.run_verify_command(weird)
        assert isinstance(obs, dict) and obs["ok"] is False


def test_run_verify_stderr_captured():
    obs = sd.run_verify_command('%s -c "import sys; sys.stderr.write(\'boom\'); raise SystemExit(1)"' % PY)
    assert obs["ok"] is False and "boom" in obs["stderr"]


# ---------------- 2. 判定口径（不骗自己）----------------

@pytest.mark.parametrize("obs,expected", [
    ({"ok": True, "exit": 0, "problem": ""}, "confirmed"),
    ({"ok": False, "exit": 1, "problem": ""}, "falsified"),
    ({"ok": False, "exit": 1, "problem": "posix_tool"}, "unverifiable"),
    ({"ok": False, "exit": 1, "problem": "syntax"}, "unverifiable"),
    ({"ok": True, "exit": 0, "problem": "ghost_path"}, "unverifiable"),
    ({"ok": False, "timed_out": True, "problem": ""}, "unverifiable"),
    ({"ok": False, "exit": None, "error": "boom", "problem": ""}, "unverifiable"),
    ({"ok": True, "exit": 0, "problem": "no_path"}, "confirmed"),  # 软问题不降级
])
def test_verdict_never_self_deceiving(obs, expected):
    assert sd._verdict_of(obs) == expected


# ---------------- 3. 写回卡：闭环真的闭合 ----------------

def test_verify_card_writes_verdict(state_file):
    _seed(state_file, _card(task_id="t-ok"))
    res = sd.verify_goal_card("t-ok")
    assert res["ok"] is True and res["verdict"] == "confirmed" and res["exit"] == 0
    card = json.loads(state_file.read_text(encoding="utf-8"))["queue"][0]
    assert card["verdict"] == "confirmed"
    assert card["verify_exit"] == 0
    assert card["verify_ok"] is True
    assert card["verified_at"]
    assert card["result_note"].startswith("验证 exit=0 预测成立")


def test_verify_card_falsified_keeps_engine_note(state_file):
    _seed(state_file, _card(task_id="t-bad", result_note="ZCode：我做得很好",
                            verify_cmd='%s -c "raise SystemExit(1)"' % PY))
    sd.verify_goal_card("t-bad")
    card = json.loads(state_file.read_text(encoding="utf-8"))["queue"][0]
    assert card["verdict"] == "falsified"
    # 引擎自述不能冒充判据：判据在前，自述压后
    assert card["result_note"].startswith("验证 exit=1 预测被证伪")
    assert "我做得很好" in card["result_note"]


def test_verify_card_idempotent(state_file):
    _seed(state_file, _card(task_id="t-idem"))
    first = sd.verify_goal_card("t-idem")
    second = sd.verify_goal_card("t-idem")
    assert first["ok"] is True and second.get("skipped") == "already_verified"


def test_verify_card_force_reruns(state_file):
    _seed(state_file, _card(task_id="t-force"))
    sd.verify_goal_card("t-force")
    forced = sd.verify_goal_card("t-force", force=True)
    assert forced["ok"] is True and "skipped" not in forced


def test_verify_card_skips_pending_approval(state_file):
    _seed(state_file, _card(task_id="t-pend", status="pending_approval"))
    res = sd.verify_goal_card("t-pend")
    assert res["ok"] is False and res["reason"] == "not_dispatched"


def test_verify_card_broken_command_is_unverifiable(state_file):
    _seed(state_file, _card(task_id="t-posix", verify_cmd="grep -c def ./x.py"))
    res = sd.verify_goal_card("t-posix")
    assert res["verdict"] == "unverifiable"
    card = json.loads(state_file.read_text(encoding="utf-8"))["queue"][0]
    assert card["verify_problem"] == "posix_tool"


def test_verify_card_missing_and_empty(state_file):
    assert sd.verify_goal_card("nope")["reason"] == "not_found"
    assert sd.verify_goal_card("")["reason"] == "task_id_required"
    _seed(state_file, _card(task_id="t-nocmd", verify_cmd=""))
    assert sd.verify_goal_card("t-nocmd")["reason"] == "no_verify_cmd"


# ---------------- 4. 补跑候选 ----------------

def test_verify_candidates(state_file):
    """[v5] 抢救判龄从 dispatched_at 算，不是 created_at。

    早先用 created_at：一张一小时前排队的卡刚派发就被判"过期"，引擎还在跑
    就抢跑量判据 → 量到行动之前的世界。老卡没有 dispatched_at 一律不抢救。
    """
    now = time.time()
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 3600))
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 10))
    _seed(state_file, _card(task_id="need", status="done", created_at=old))
    _seed(state_file, _card(task_id="done-verified", status="done",
                            verified_at="2026-10-31T19:00:00"))
    _seed(state_file, _card(task_id="pend", status="pending_approval"))
    # 排队很久但**刚派发**的卡：不许抢救
    _seed(state_file, _card(task_id="just-dispatched", status="dispatched",
                            created_at=old, dispatched_at=fresh))
    # 派发后确实卡住超过阈值：抢救
    _seed(state_file, _card(task_id="stale", status="dispatched",
                            created_at=old, dispatched_at=old))
    # 老卡没有 dispatched_at：不猜，交给 close_loop
    _seed(state_file, _card(task_id="legacy-no-ts", status="dispatched",
                            created_at=old))
    assert set(sd._verify_candidates()) == {"need", "stale"}


def test_verify_goal_card_refuses_inflight(state_file):
    """dispatched 且离派发很近 → 拒验（抢跑）；force=True 才允许人手动重跑。"""
    now = time.time()
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 5))
    _seed(state_file, _card(task_id="t-fly", status="dispatched",
                            dispatched_at=fresh,
                            verify_cmd='python -c "print(1)"'))
    res = sd.verify_goal_card("t-fly")
    assert res["ok"] is False and res["reason"] == "inflight_not_settled"
    # 人手动重跑：真去跑命令
    res = sd.verify_goal_card("t-fly", force=True)
    assert res["ok"] is True
    assert res["verdict"] == "confirmed"


def test_verify_candidates_survives_broken_state(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sd, "STATE_FILE", bad)
    assert sd._verify_candidates() == []


# ---------------- 5. 生成侧：POSIX 命令必须被拦 ----------------

@pytest.mark.parametrize("cmd", [
    'find . -name "*.py" -exec grep -c def {} \\;',
    'type D:\\MoRay\\server\\app\\self_driven.py | grep -c def',
    'echo $(date) > D:\\MoRay\\out\\x.txt',
    'mkdir -p D:\\MoRay\\out\\deep',
    'awk "{print $1}" D:\\MoRay\\out\\x.txt',
])
def test_posix_tool_detected(cmd):
    assert sd.verify_cmd_problem(cmd) == "posix_tool"


@pytest.mark.parametrize("cmd", [
    r'python -c "print(open(r\'D:\MoRay\setup.py\').read()[:10])"',
    r'findstr /C:def D:\MoRay\server\app\self_driven.py',
    r'dir D:\MoRay\out\report.md',
])
def test_windows_native_commands_still_pass(cmd):
    assert sd.verify_cmd_problem(cmd) == ""


# ---------------- 6. 判据台账（"连续 7 天"的度量衡）----------------

def test_ledger_written_by_verify(state_file):
    _seed(state_file, _card(task_id="t-l1"))
    sd.verify_goal_card("t-l1")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    log = data.get("verdict_log") or []
    assert len(log) == 1
    assert log[0]["task_id"] == "t-l1" and log[0]["verdict"] == "confirmed"


def test_ledger_same_card_same_day_keeps_last(state_file):
    """[v5.1] 同一张卡同一天重跑 N 次，台账只留最后一条判决。

    按次追加会把"一次重跑"记成"多判了一件新事"，`命令不可判` 从 1 涨到 3，
    台账口径当场被自己糊掉。重测痕迹留在卡上的 remeasured 计数里。
    """
    _seed(state_file, _card(task_id="t-r1", verify_cmd='python -c "print(1)"'))
    assert sd.verify_goal_card("t-r1")["verdict"] == "confirmed"
    assert sd.verify_goal_card("t-r1", force=True)["verdict"] == "confirmed"
    assert sd.verify_goal_card("t-r1", force=True)["verdict"] == "confirmed"
    log = json.loads(state_file.read_text(encoding="utf-8")).get("verdict_log") or []
    assert len(log) == 1, log
    assert log[0]["task_id"] == "t-r1" and log[0]["remeasured"] == 2
    st = sd.verdict_stats(7)
    assert (st["judged"], st["confirmed"]) == (1, 1)


def test_stats_empty_is_not_fake_accuracy(state_file):
    st = sd.verdict_stats(7)
    assert st["judged"] == 0 and st["accuracy"] is None and st["streak"] == 0


def test_stats_accuracy_excludes_unverifiable(state_file):
    import datetime
    today = datetime.date.today().isoformat()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["verdict_log"] = [
        {"ts": today + "T10:00:00", "task_id": "a", "verdict": "confirmed"},
        {"ts": today + "T10:01:00", "task_id": "b", "verdict": "confirmed"},
        {"ts": today + "T10:02:00", "task_id": "c", "verdict": "falsified"},
        {"ts": today + "T10:03:00", "task_id": "d", "verdict": "unverifiable"},
    ]
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    st = sd.verdict_stats(7)
    assert (st["judged"], st["confirmed"], st["falsified"], st["unverifiable"]) == (3, 2, 1, 1)
    assert st["accuracy"] == 0.667          # 1/3 那条不可判的没进分母
    assert st["streak"] == 1 and st["streak_end_today"] is True


def test_stats_streak_counts_consecutive_days(state_file):
    import datetime
    today = datetime.date.today()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["verdict_log"] = [
        {"ts": (today - datetime.timedelta(days=i)).isoformat() + "T09:00:00",
         "task_id": "s%d" % i, "verdict": "falsified"} for i in range(3)
    ]
    # 第 4 天只有 unverifiable → 不算"活过一天"，streak 断在那里
    data["verdict_log"].append(
        {"ts": (today - datetime.timedelta(days=3)).isoformat() + "T09:00:00",
         "task_id": "x", "verdict": "unverifiable"})
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    st = sd.verdict_stats(7)
    assert st["streak"] == 3 and st["active_days"] == 3
    assert st["judged"] == 3 and st["accuracy"] == 0.0


def test_stats_ignores_out_of_window(state_file):
    import datetime
    old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["verdict_log"] = [{"ts": old + "T09:00:00", "task_id": "o",
                            "verdict": "confirmed"}]
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert sd.verdict_stats(7)["judged"] == 0
    assert sd.verdict_stats(60)["judged"] == 1


def test_stats_never_raises_on_broken_ledger(state_file):
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["verdict_log"] = ["garbage", None, {"ts": "not-a-date", "verdict": "confirmed"},
                           {"ts": "2026-10-31T10:00:00", "verdict": "weird"}]
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    st = sd.verdict_stats(7)
    assert st["judged"] == 0


def test_ledger_capped(state_file):
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["verdict_log"] = [{"ts": "2026-10-31T10:00:00", "task_id": str(i),
                            "verdict": "confirmed"} for i in range(sd._VERDICT_LOG_MAX + 50)]
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _seed(state_file, _card(task_id="t-cap"))
    sd.verify_goal_card("t-cap")
    log = json.loads(state_file.read_text(encoding="utf-8"))["verdict_log"]
    assert len(log) == sd._VERDICT_LOG_MAX
    assert log[-1]["task_id"] == "t-cap"
