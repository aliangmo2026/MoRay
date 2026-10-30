# -*- coding: utf-8 -*-
"""tests/test_fence_guard.py — [fence_guard 2026-10-03] decision 提示词字段级约束 + 剥围栏保险回归。

运行（cwd 必须是 lora-training，保证 import 命中工程内模块）：
    cd /d D:\\MoRay\\decision-kernel\\lora-training
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_fence_guard.py -v

背景：moray-laya-final（qwen2.5:1.5b 基座）做 decision 步时把 ```lang 围栏包进
fixed_content 值（JSON 仍合法、外层"不要代码块"管不到值内）或把值写成"改法说明"。
治本 = 三范式 + 兜底 system 加字段级裸文本约束；保险 = 入缓存前 _strip_code_fence 剥围栏。

覆盖：
  * _strip_code_fence 六形态（```lang 闭合 / 无 lang 闭合 / 未闭合只剥首行 /
    无围栏原样 / 值中间 ``` 不动 / 非纯围栏首行不剥）
  * _update_context_store 接入点：decision 输出带围栏 → 入缓存已剥（file_edit 兜底注入同享）
  * 提示词回归钉：三范式 system 与 _DECISION_SYSTEM_DEFAULT 均含裸文本约束，
    三处 user 模板 fixed_content 描述带（裸文本）标注、batch verify_cmd 带裸命令行标注
    （防提示词回退——2026-10-03 治本改动一旦被改丢，这里立刻红）
"""
from modules.executor import (_DECISION_SYSTEM_DEFAULT, _strip_code_fence,
                              _update_context_store)
from modules.task_planner import TASK_PATTERNS


def _decision_step(key):
    steps = TASK_PATTERNS[key]["steps"]
    return [s for s in steps if s.get("tool") == "decision"][0]


# ---- 1-6：_strip_code_fence 形态 ----

def test_01_closed_fence_with_lang():
    assert _strip_code_fence("```python\nprint(1/0)\n```") == "print(1/0)"


def test_02_closed_fence_no_lang():
    assert _strip_code_fence("```\nprint(1)\n```") == "print(1)"


def test_03_unclosed_fence_strips_head_only():
    assert _strip_code_fence("```python\nprint(1/0)") == "print(1/0)"


def test_04_no_fence_unchanged():
    assert _strip_code_fence("print(1/0)\n") == "print(1/0)\n"


def test_05_inner_fence_untouched():
    src = "```python\n# ```\nprint(1)\n```"
    assert _strip_code_fence(src) == "# ```\nprint(1)"


def test_06_code_on_fence_line_not_stripped():
    # 首行 "```python print(1)"（围栏与代码同行）不是纯围栏行 → 原样返回
    src = "```python print(1)\n```"
    assert _strip_code_fence(src) == src


# ---- 7-8：_update_context_store 接入点 ----

def test_07_store_strips_fence_from_decision_fixed_content():
    store = _update_context_store({}, "decision", {
        "ok": True,
        "data": '{"problem_path": "a.py", "analysis": "根因", '
                '"fixed_content": "```python\\nprint(1/0)\\n```"}',
    })
    assert store["fixed_content"] == "print(1/0)"


def test_08_store_keeps_plain_fixed_content():
    store = _update_context_store({}, "decision", {
        "ok": True,
        "data": '{"fixed_content": "print(1/0)"}',
    })
    assert store["fixed_content"] == "print(1/0)"


# ---- 9-13：提示词回归钉 ----

def test_09_all_paradigm_systems_have_bare_text_rule():
    for key in ("batch", "debug", "code_fix"):
        sy = _decision_step(key)["input"]["system"]
        assert "完整裸文本内容本身" in sy, key
        assert "```" in sy and "markdown" in sy, key


def test_10_debug_and_codefix_mark_free_text_fields():
    assert "analysis 才是说明文字" in _decision_step("debug")["input"]["system"]
    assert "summary 才是说明文字" in _decision_step("code_fix")["input"]["system"]


def test_11_user_templates_mark_fixed_content_bare():
    assert "处理后的完整文件内容（裸文本）" in _decision_step("batch")["input"]["user"]
    assert "修复后的完整文件内容（裸文本）" in _decision_step("debug")["input"]["user"]
    assert "修改后的完整文件内容（裸文本）" in _decision_step("code_fix")["input"]["user"]


def test_12_batch_verify_cmd_marked_bare():
    # code_fix 的校验命令由规划期 _derive_verify_cmd 预生成，decision 不输出 → 只钉 batch
    assert "批量校验命令（裸命令行，无 markdown 围栏）" in _decision_step("batch")["input"]["user"]
    assert "verify_cmd" not in _decision_step("code_fix")["input"]["user"]


def test_13_default_system_has_bare_text_rule():
    assert "完整裸文本内容本身" in _DECISION_SYSTEM_DEFAULT
    assert "```" in _DECISION_SYSTEM_DEFAULT


# ---- 14-15：链路级桩测——decision 带围栏 → 占位符主路径写出的 file_edit 输入已剥净 ----
# 主路径 = {step_3_output.fixed_content} 占位符（_replace_placeholders/_lookup_output，
# 直接取 decision 原始 JSON），不经 context_store——故必须在 decision 结果装配处剥。

import asyncio  # noqa: E402

from modules import executor as _exec  # noqa: E402
from modules import self_check as _sc  # noqa: E402
from modules import tool_registry as _tr  # noqa: E402
from modules.task_planner import _render_pat_steps  # noqa: E402

_FENCED = "```python\nprint(1/0)\n```"
_PLAIN = "print(1/0)"


def _debug_steps():
    known = {"target_path": r"D:\t\app.py", "target_dir": r"D:\t",
             "run_cmd": "python app.py", "goal": "fix", "verify_cmd": "python -m py_compile app.py"}
    return _render_pat_steps(TASK_PATTERNS["debug"]["steps"], known)


def _stub_tools():
    """打桩：decision 恒回围栏样例（并钉住 executor 必须传 fmt="json"）；原子工具恒 ok。"""
    async def fake_llm(system, user, fmt=None):
        assert fmt == "json", "executor decision 步必须启用 Ollama format=json 语法约束"
        return {"problem_path": r"D:\t\app.py", "analysis": "根因",
                "fixed_content": _FENCED, "expected_exit_code": 0}

    async def fake_call_tool(tool, params):
        if tool.endswith("cmd_exec"):
            return {"ok": True, "tool": tool, "data": {"exit_code": 0, "stdout": "", "stderr": ""},
                    "error": None, "elapsed_ms": 1}
        if tool.endswith("file_read"):
            return {"ok": True, "tool": tool, "data": {"path": params.get("path", ""), "content": "old"},
                    "error": None, "elapsed_ms": 1}
        return {"ok": True, "tool": tool, "data": "ok", "error": None, "elapsed_ms": 1}

    return fake_llm, fake_call_tool


def _run_chain():
    events = []

    async def collect():
        async for ev in _exec._execute_multi_steps("stubfence", _debug_steps(), goal="fix"):
            events.append(ev)

    asyncio.run(collect())
    return events


def _edit_inputs(events):
    return [ev.get("input") or {} for ev in events
            if ev.get("event") == "step_start" and str(ev.get("tool", "")).endswith("file_edit")]


def test_14_chain_placeholder_path_strips_fence(monkeypatch):
    fake_llm, fake_call_tool = _stub_tools()
    monkeypatch.setattr(_sc, "_llm_json", fake_llm)
    monkeypatch.setattr(_tr, "call_tool", fake_call_tool)
    events = _run_chain()
    edits = _edit_inputs(events)
    assert edits, "链路未产生 file_edit 步"
    assert edits[0].get("content") == _PLAIN          # 占位符主路径：围栏已剥
    final = [ev for ev in events if ev.get("event") == "final"][-1]
    assert final["status"] == "success"


def test_15_chain_plain_content_untouched(monkeypatch):
    fake_llm, fake_call_tool = _stub_tools()

    async def plain_llm(system, user, fmt=None):
        d = await fake_llm(system, user, fmt)
        d["fixed_content"] = _PLAIN
        return d

    monkeypatch.setattr(_sc, "_llm_json", plain_llm)
    monkeypatch.setattr(_tr, "call_tool", fake_call_tool)
    events = _run_chain()
    edits = _edit_inputs(events)
    assert edits and edits[0].get("content") == _PLAIN  # 无围栏原样，不受保险影响
