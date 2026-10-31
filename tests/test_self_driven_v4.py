"""self_driven v4 单测：verify_cmd 静态体检 + 主题去重 + 带反馈重生成。

全部离线：真 LLM 用 monkeypatch 顶掉，状态文件用 tmp_path，绝不打真实
memory_bank / 队列文件。跑法：
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_self_driven_v4.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\MoRay")

from server.app import self_driven as sd  # noqa: E402


# ---------------- 1. verify_cmd 静态体检 ----------------

@pytest.mark.parametrize("cmd,expected", [
    ("", "empty"),
    ("   ", "empty"),
    # v3 真实踩坑的那条：Windows 上没有 /tmp
    ("grep -c 'os\\.path\\.abspath' /tmp/zcode_test_script.py", "posix_path"),
    ("cat /var/log/syslog", "posix_path"),
    ("curl http://example.com/health", "placeholder"),
    ("python check.py path/to/file", "placeholder"),
    ("python -c \"print(1)\"", "no_path"),
    ("dir Z:\\nope\\x.txt", "ghost_path"),
    # 已存在的真路径 → 合格
    (r"dir D:\MoRay\server\app\self_driven.py", ""),
    (r"findstr /C:def D:\MoRay\server\app\self_driven.py", ""),
    (r"type C:\Windows\System32\drivers\etc\hosts", ""),
    # 未来产物但根合法 → 合格
    (r"dir D:\MoRay\out\future_report.md", ""),
    (r"dir E:\MoRay OS\out\future_report.md", ""),
])
def test_verify_cmd_problem(cmd, expected):
    assert sd.verify_cmd_problem(cmd) == expected


def test_verify_cmd_problem_never_raises():
    for weird in (None, 0, [], {"a": 1}, "\x00"):
        assert isinstance(sd.verify_cmd_problem(weird), str)


# ---------------- 2. 主题去重 ----------------

def test_goal_similarity_and_repeat():
    a = "修正脚本生成逻辑，强制使用绝对路径处理文件读写操作。"
    b = "修正脚本生成逻辑，强制使用绝对路径处理文件读写。"
    c = "把记忆库的向量召回改成两阶段重排。"
    assert sd.goal_similarity(a, b) > sd._REPEAT_SIMILARITY
    assert sd.goal_similarity(a, c) < sd._REPEAT_SIMILARITY
    recent = [{"task_id": "t1", "goal": a}]
    assert (sd._find_repeat(b, recent) or {}).get("task_id") == "t1"
    assert sd._find_repeat(c, recent) is None
    assert sd._find_repeat(a, None) is None
    assert sd._find_repeat(a, ["坏数据", 42]) is None


# ---------------- 3. prompt 注入 ----------------

def test_build_goal_messages_injects_recent():
    ms = {"last_result": "success", "engine": "zcode"}
    recent = [{"goal": "强制绝对路径"}, {"goal": "捕获 stderr"}]
    sys_text = sd.build_goal_messages(ms, recent)[0]["content"]
    assert "{recent_goals}" not in sys_text
    assert "{mental_state}" not in sys_text
    assert "强制绝对路径" in sys_text and "捕获 stderr" in sys_text
    assert "禁止重复" in sys_text
    # 不传 recent 也要能跑（向后兼容旧调用/旧测试）
    plain = sd.build_goal_messages(ms)[0]["content"]
    assert "（无）" in plain
    # 只喂前 3 条
    many = [{"goal": "目标%d" % i} for i in range(6)]
    over = sd.build_goal_messages(ms, many)[0]["content"]
    assert "目标0" in over and "目标3" not in over


def test_build_fix_message_lists_reasons():
    msg = sd._build_fix_message({"verify_cmd": "/tmp/x"}, "posix_path",
                               {"goal": "强制绝对路径"})
    assert msg["role"] == "user"
    assert "Linux 路径" in msg["content"]
    assert "主题重复" in msg["content"]


# ---------------- 4. 端到端：带反馈重生成 ----------------

@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """把状态文件重定向到 tmp_path，避免污染真实队列/配额。"""
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sd, "STATE_FILE", tmp_path / "self_driven_state.json")
    monkeypatch.setattr(sd, "_goal_inflight", False)
    monkeypatch.setattr(sd, "_fetch_mental_state",
                        lambda: {"last_result": "success", "engine": "zcode"})
    return tmp_path / "self_driven_state.json"


def _payload(goal, verify, claim="断言", pred="预期数值 1"):
    return json.dumps({"goal": goal, "claim": claim, "prediction": pred,
                       "verify_cmd": verify, "target_engine": "zcode"},
                      ensure_ascii=False)


def test_retry_fixes_posix_path(tmp_state, monkeypatch):
    calls = []

    async def fake_llm(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _payload("修绝对路径", "/tmp/zcode_test_script.py"), ""
        return _payload("修绝对路径", r"dir D:\MoRay\server\app\self_driven.py"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = __import__("asyncio").run(sd.generate_goal())
    assert card is not None
    assert len(calls) == 2, "体检不合格必须重生成一次"
    assert card["verify_problem"] == "", "重生成后应合格"
    assert card["verify_cmd"].startswith("dir D:")
    # 反馈消息真的进了第二次调用
    assert calls[1][-1]["role"] == "user"
    assert "Linux 路径" in calls[1][-1]["content"]


def test_retry_still_bad_hard_defect_not_shipped(tmp_state, monkeypatch):
    """[v5 契约变更] 硬缺陷（跑不起来的命令）改为一律不发卡。

    v4 的契约是"重生成后仍不合格就照发卡、只打 verify_problem 标"。v5 起改成
    不发：那张卡在真机上必然只能判成 unverifiable，既拿不到真实误差、又白烧
    一份日配额——发它等于生产装饰品。软缺陷（placeholder / no_path）仍然照发。
    """
    async def fake_llm(messages):
        return _payload("还在用 /tmp", "/tmp/still_bad.py"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = __import__("asyncio").run(sd.generate_goal())
    assert card is None, "硬缺陷卡不应发出去"
    # 不烧配额：一张卡都没记，状态文件甚至没被创建（也就没有 daily.count +1）
    assert not tmp_state.exists() or json.loads(
        tmp_state.read_text(encoding="utf-8")).get("queue") == []
    if tmp_state.exists():
        assert (json.loads(tmp_state.read_text(encoding="utf-8"))
                .get("daily") or {}).get("count", 0) == 0


def test_retry_still_bad_soft_defect_still_shipped(tmp_state, monkeypatch):
    """软缺陷照发：`python -c "print(1)"` 没有文件路径（no_path），但能真跑，
    exit code 依旧是硬判据。"""
    async def fake_llm(messages):
        return _payload("跑个退出码", 'python -c "print(1)"'), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = __import__("asyncio").run(sd.generate_goal())
    assert card is not None
    assert card["verify_problem"] == "no_path"
    assert "python -c" in card["verify_cmd"]


def test_repeat_goal_flagged(tmp_state, monkeypatch):
    # 先塞一张卡进队列
    state = {"daily": {"date": sd._today(), "count": 1},
             "queue": [{"task_id": "old-1", "status": "pending_approval",
                        "goal": "修正脚本生成逻辑，强制使用绝对路径处理文件读写操作。"}]}
    sd._save_state(state)
    seen = []

    async def fake_llm(messages):
        seen.append(messages[0]["content"])
        return _payload("修正脚本生成逻辑，强制使用绝对路径处理文件读写操作。",
                        r"dir D:\MoRay\server\app\self_driven.py"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = __import__("asyncio").run(sd.generate_goal())
    assert card is not None
    assert card["repeat_of"] == "old-1", "撞车必须记录撞的是哪张卡"
    assert "修正脚本生成逻辑" in seen[0], "近期目标必须进 prompt"


def test_quota_still_enforced(tmp_state, monkeypatch):
    sd._save_state({"daily": {"date": sd._today(), "count": sd._DAILY_GOAL_LIMIT},
                    "queue": []})

    async def boom(messages):
        raise AssertionError("配额已满时不该调用 LLM")

    monkeypatch.setattr(sd, "_llm_goal", boom)
    assert __import__("asyncio").run(sd.generate_goal()) is None


def test_fetch_recent_goals_newest_first(tmp_state, monkeypatch):
    sd._save_state({"daily": {"date": sd._today(), "count": 3},
                    "queue": [{"goal": "旧%s" % i} for i in range(5)]})
    got = sd._fetch_recent_goals()
    assert [g["goal"] for g in got] == ["旧4", "旧3", "旧2"]


# ---------------- 5. 派发闸门（只测拒绝分支，绝不触发真 spawn） ----------------

class _Req:
    def __init__(self, payload=None, boom=False):
        self._payload = payload
        self._boom = boom

    async def json(self):
        if self._boom:
            raise ValueError("not json")
        return self._payload


def _call(payload=None, boom=False):
    import asyncio
    return asyncio.run(sd.self_driven_dispatch(_Req(payload, boom)))


def test_dispatch_rejects_bad_input(tmp_state):
    assert _call(boom=True)["reason"] == "bad_json"
    assert _call({})["reason"] == "task_id_required"
    assert _call({"task_id": "nope"})["reason"] == "not_found"


def test_dispatch_rejects_non_pending(tmp_state):
    sd._save_state({"daily": {"date": sd._today(), "count": 1},
                    "queue": [{"task_id": "c1", "status": "dispatched",
                               "goal": "已经派过的卡", "target_engine": "zcode"}]})
    out = _call({"task_id": "c1"})
    assert out["ok"] is False
    assert out["reason"] == "not_pending"
    assert out["status"] == "dispatched"


def test_build_dispatch_prompt_carries_three_fields():
    p = sd._build_dispatch_prompt({"goal": "写脚本", "claim": "断言X",
                                   "prediction": "观测Y", "verify_cmd": r"dir D:\MoRay"})
    assert "写脚本" in p and "断言X" in p and "观测Y" in p and r"dir D:\MoRay" in p
    assert sd._build_dispatch_prompt({}) == ""


# ---------------- 6. 命令可编译性（回归自真实坏卡 4004b7bf） ----------------

#: 2026-10-31 18:22:26 真实生成的目标卡原文。Windows 路径塞进非 raw 字符串，
#: `\u` + `tf_` → unicodeescape 语法错：命令编译不过，永远为假的预言机。
_BAD_REAL_CMD = (
    "python -c \"exit(0 if open('D:\\MoRay\\utf_test.txt','r',encoding='utf-8')"
    ".read()=='Hello 世界' else 1)\""
)


def test_real_bad_card_cmd_is_now_flagged():
    assert sd.verify_cmd_problem(_BAD_REAL_CMD) == "syntax"


def test_python_c_escape_variants():
    cases = [
        # 无路径 → no_path（契约照旧，不是转义问题）
        ("python -c \"print(1)\"", "no_path"),
        # raw 字符串，路径合法 → 放行
        ("python -c \"open(r'D:\\MoRay\\ok.txt')\"", ""),
        # \u 转义错 → 判死
        ("python -c \"open('D:\\MoRay\\utf_test.txt')\"", "syntax"),
        # \x 截断 → 判死
        ("python -c \"open('D:\\MoRay\\x0')\"", "syntax"),
        # 非 python -c：不做转义体检，只看路径
        ("dir D:\\MoRay\\out\\*", ""),
    ]
    for cmd, want in cases:
        assert sd.verify_cmd_problem(cmd) == want, cmd


def test_syntax_check_does_not_mask_other_reasons():
    # 编译不过但路径本身是 Linux 的，仍然先报 posix_path（信息更准）
    assert sd.verify_cmd_problem(r"""python -c "open('/tmp/a\ub.txt')" """.strip()) == "posix_path"


def test_generate_route_reports_inflight_not_failed(tmp_state, monkeypatch):
    """回归：后台桥在生成时手点 generate，曾报"generation_failed/LLM 失败"，
    真实原因是排队中。必须报 inflight。"""
    import asyncio
    monkeypatch.setattr(sd, "_goal_inflight", True)
    monkeypatch.setattr(sd, "_fetch_mental_state", lambda: {"x": 1})
    out = asyncio.run(sd.self_driven_generate())
    assert out["ok"] is False and out["reason"] == "inflight"
