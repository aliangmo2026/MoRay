# -*- coding: utf-8 -*-
"""[v6] 脚本生成强制绝对路径：verify_cmd 相对文件引用硬拦截的回归网。

背景：验证子进程的 cwd 由服务端钉在工程根，目标卡却常按"目标目录"写相对路径
（`type data.txt`、`python scripts\\check.py`、`open('data.txt')`），命令会在
别处读写——历史真实失败是文件明明存在却报 not found。v6 在落卡前的静态体检里
把"带已知扩展名的相对文件 token"判成硬缺陷 relative_path：先带反馈重生成一次，
仍不合格则拒发卡（不发注定判不动的卡）。

覆盖：
  1. relative_path 的命中范围（裸文件名 / 相对子路径 / python -c 字面量）；
  2. 绝对路径不误伤（盘符、raw 字符串、通配符、只读 git 命令）；
  3. relative_path 优先于 no_path，且进硬缺陷集合；
  4. 反馈消息把"必须用绝对路径"写回给模型；
  5. 只读白名单分类不会把相对路径命令当成可自跑；
  6. generate_goal 端到端：相对路径 → 重生成 → 仍不合格则拒发卡。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import self_driven as sd  # noqa: E402


# ── 1. 相对路径命中 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    r"type reports\daily.txt",
    r"python scripts\check.py",
    "type data.txt",
    "python -c \"open('data.txt')\"",
    r"python -m pytest tests/test_self_driven_v4.py -q",
    # 绝对路径 + 相对参数混用：相对的那一半同样是硬缺陷
    r"python D:\MoRay\tests\x.py --config cfg.json",
    r"findstr x data.json",
    r"dir ..\out\report.md",
])
def test_relative_refs_rejected(cmd):
    assert sd.verify_cmd_problem(cmd) == "relative_path"


# ── 2. 绝对路径不误伤 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    r"dir D:\MoRay\server\app\self_driven.py",
    r"type C:\Windows\System32\drivers\etc\hosts",
    "python -c \"open(r'D:\\MoRay\\ok.txt')\"",
    r"dir D:\MoRay\out\*.py",
    r"git -C D:\MoRay\server status --porcelain",
    r"findstr /C:def D:\MoRay\server\app\self_driven.py",
])
def test_absolute_paths_not_flagged(cmd):
    assert sd.verify_cmd_problem(cmd) != "relative_path"


# ── 3. 原因码优先级与硬缺陷集合 ─────────────────────────────────────────────

def test_relative_beats_no_path():
    # 旧逻辑会把 `type data.txt` 报成 no_path（软缺陷、照发卡）；v6 必须报相对路径
    assert sd.verify_cmd_problem("type data.txt") == "relative_path"


def test_relative_is_hard_defect():
    assert "relative_path" in sd._VERIFY_HARD_PROBLEMS


# ── 4. 反馈消息 ─────────────────────────────────────────────────────────────

def test_fix_message_explains_absolute_path():
    msg = sd._build_fix_message({"verify_cmd": "type data.txt"},
                                "relative_path", None)
    assert msg["role"] == "user"
    assert "相对路径" in msg["content"]
    assert "绝对路径" in msg["content"]


# ── 5. 只读白名单分类 ───────────────────────────────────────────────────────

def test_readonly_classify_rejects_relative():
    r = sd.classify_card_action("核对内容", "内容为 1", "type data.txt")
    assert r["readonly"] is False
    assert r["reason"] == "verify:relative_path"


# ── 6. 端到端：重生成 + 仍不合格拒发卡 ──────────────────────────────────────

@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sd, "STATE_FILE", tmp_path / "self_driven_state.json")
    monkeypatch.setattr(sd, "_goal_inflight", False)
    monkeypatch.setattr(sd, "_fetch_mental_state",
                        lambda: {"last_result": "success", "engine": "zcode"})
    # 只读卡会走自动派发；单测里绝不 spawn 真引擎
    monkeypatch.setattr(sd, "_maybe_auto_dispatch",
                        lambda card: {"ok": False, "reason": "test"})
    return tmp_path / "self_driven_state.json"


def _payload(goal, verify):
    return json.dumps({"goal": goal, "claim": "断言", "prediction": "观测 1",
                       "verify_cmd": verify, "target_engine": "zcode"},
                      ensure_ascii=False)


def test_generate_fixes_relative_path(tmp_state, monkeypatch):
    calls = []

    async def fake_llm(messages):
        calls.append(messages)
        if len(calls) == 1:
            return _payload("核对 data.txt 内容", "type data.txt"), ""
        return _payload("核对 data.txt 内容", r"type D:\MoRay\utf_test.txt"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = asyncio.run(sd.generate_goal())
    assert card is not None
    assert len(calls) == 2, "相对路径必须触发一次带反馈重生成"
    assert card["verify_problem"] == ""
    assert card["verify_cmd"].startswith("type D:")
    assert "相对路径" in calls[1][-1]["content"]


def test_generate_rejects_persistent_relative_path(tmp_state, monkeypatch):
    async def fake_llm(messages):
        return _payload("核对 data.txt 内容", "type data.txt"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    assert asyncio.run(sd.generate_goal()) is None, "硬缺陷卡不应发出去"


# ── 7. 脚本生成侧（派发指令 / 重试提示词）同样强制绝对路径 ─────────────────

def test_dispatch_prompt_requires_absolute_path():
    p = sd._build_dispatch_prompt({"goal": "写一个读取配置的脚本",
                                   "claim": "配置为 12 字节",
                                   "prediction": "输出 12",
                                   "verify_cmd": r"dir D:\MoRay\cfg.json"})
    assert "路径硬约束" in p
    assert "绝对路径" in p and "禁止相对路径" in p
    assert "data.txt" in p and "scripts\\run.py" in p


def test_dispatch_prompt_keeps_readonly_rule():
    p = sd._build_dispatch_prompt({"goal": "核对内容", "claim": "内容为 1",
                                   "verify_cmd": r"type D:\MoRay\a.txt",
                                   "readonly_class": True})
    assert "只读约束" in p and "路径硬约束" in p


def test_dispatch_prompt_empty_card_stays_empty():
    # 路由侧按 empty_prompt 拒发：空卡不能被"路径约束"撑成非空指令
    assert sd._build_dispatch_prompt({}) == ""


def test_retry_prompt_forbids_relative_path():
    p = sd._RETRY_SYSTEM_PROMPT
    assert "所有文件读写一律用 Windows 盘符绝对路径" in p
    assert "用绝对路径或" not in p, "重试提示词不得再放行相对路径"
    assert "会被服务端判为硬缺陷" in p


# ── 8. [v6.1] 正斜杠绝对路径：prompt 明确允许 D:/...，体检器不得判成 POSIX ──

@pytest.mark.parametrize("cmd", [
    r"type D:/MoRay/utf_test.txt",
    r"dir D:/MoRay",
    r"python -c \"print(open('D:/MoRay/utf_test.txt').read())\"",
    r"python D:/MoRay/tests/test_self_driven_v6.py",
])
def test_forward_slash_absolute_never_posix(cmd):
    # 旧逻辑把 /MoRay/... 当 POSIX 路径 → posix_path（硬缺陷）→ 好卡被判死
    assert sd.verify_cmd_problem(cmd) != "posix_path"


def test_forward_slash_absolute_under_root_accepted():
    # 正斜杠路径也要能匹配 _VERIFY_ROOTS 前缀（D:/MoRay 下待产出文件不算幽灵）
    assert sd.verify_cmd_problem("dir D:/MoRay/__not_created_yet__.txt") == ""
    assert sd.verify_cmd_problem("type D:/MoRay/utf_test.txt") == ""


def test_forward_slash_ghost_outside_roots():
    assert sd.verify_cmd_problem("dir Z:/nope/x.txt") == "ghost_path"


def test_http_url_not_treated_as_drive_path():
    # `http://` 里的 `p:/` 不能被盘符分支吃掉（否则 example.com 检查前就被当成路径）
    assert sd.verify_cmd_problem("curl http://127.0.0.1:8000/api/health") == "placeholder" \
        or sd.verify_cmd_problem("curl http://127.0.0.1:8000/api/health") == "no_path"


# ── 9. [v6.1] 补齐漏网的相对引用形态（盘符相对/根相对/父目录相对/裸文件名）──

@pytest.mark.parametrize("cmd", [
    r"type C:file.txt",
    r"type D:relative.txt",
    r"python -c \"open('D:data.json')\"",
    r"type \rootfile.txt",
    r"type ..\README",
    r"python ..\scripts\run",
    r"type .\out.txt",
])
def test_more_relative_forms_rejected(cmd):
    assert sd.verify_cmd_problem(cmd) == "relative_path"


@pytest.mark.parametrize("cmd", [
    r"type %USERPROFILE%\x.txt",
    r"python -c \"print('a\ntext with dot.py suffix')\"",
])
def test_env_var_and_escape_sequences_not_relative(cmd):
    # %VAR%\... 会被 cmd 展开成绝对路径；字符串转义 \n 不是根相对路径
    assert sd.verify_cmd_problem(cmd) != "relative_path"


def test_quoted_absolute_path_with_spaces_kept_whole():
    got = sd._extract_verify_paths(r'type "D:\Program Files\Python\python.exe"')
    assert got == [r"D:\Program Files\Python\python.exe"]

