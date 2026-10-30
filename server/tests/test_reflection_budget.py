# -*- coding: utf-8 -*-
"""算力预算闸单测（2026-10-07 v4）：只测闸本身，不碰 LLM、不碰库。

闸是 run_reflection 的第一段——放行后用空 _recent_task_results 走"无原料"早退，
全程零 LLM 调用、零库访问、毫秒级。闸状态经 monkeypatch 注入/复位（teardown
自动还原模块级变量，测试间互不污染）。运行方式：仓库根目录下
`server/.venv/Scripts/python.exe -m pytest server/tests/test_reflection_budget.py -v`
（python -m 把 cwd=D:\\MoRay 注入 sys.path，`server.app` 包方可导入）。
"""
import asyncio
import time
from datetime import date

from server.app import reflection


def _reset(monkeypatch, **over):
    """闸状态复位（默认清零）；over 以模块变量名为键注入初值。"""
    monkeypatch.setattr(reflection, "_reflect_last_global", over.get("last_global", 0.0))
    monkeypatch.setattr(reflection, "_reflect_last_trace", over.get("last_trace", {}))
    monkeypatch.setattr(reflection, "_reflect_daily_count", over.get("daily_count", 0))
    monkeypatch.setattr(reflection, "_reflect_daily_date", over.get("daily_date", ""))


def _no_items(monkeypatch):
    """放行后立即早退：无 task_result 原料 → 不进 LLM、不进写库。"""
    monkeypatch.setattr(reflection, "_recent_task_results", lambda limit: [])


def _run(**kw):
    return asyncio.run(reflection.run_reflection(**kw))


def _passed(r) -> bool:
    """闸放行 = note 不含 throttled（后续走"无原料"早退，processed=0）。"""
    return "throttled" not in (r.get("note") or "")


def test_first_call_passes(monkeypatch):
    _reset(monkeypatch)
    _no_items(monkeypatch)
    r = _run(trace_id="t1")
    assert _passed(r) and r["processed"] == 0
    assert reflection._reflect_daily_count == 1  # 放行才计数


def test_second_immediate_call_global_cooldown(monkeypatch):
    _reset(monkeypatch)
    _no_items(monkeypatch)
    _run(trace_id="t1")
    r2 = _run(trace_id="t2")  # 换 trace 也拦——全局冷却不区分链
    assert r2["note"] == "throttled: global_cooldown"
    assert reflection._reflect_daily_count == 1  # 被拒不消耗日限


def test_trace_cooldown_blocks_after_global_elapsed(monkeypatch):
    _reset(monkeypatch)
    _no_items(monkeypatch)
    _run(trace_id="t1")
    reflection._reflect_last_global = time.monotonic() - 100.0  # 注入：全局冷却已过
    r = _run(trace_id="t1")
    assert r["note"] == "throttled: trace_cooldown"
    assert reflection._reflect_daily_count == 1


def test_trace_cooldown_elapsed_passes(monkeypatch):
    _reset(monkeypatch)
    _no_items(monkeypatch)
    _run(trace_id="t1")
    reflection._reflect_last_global = time.monotonic() - 100.0
    reflection._reflect_last_trace["t1"] = time.monotonic() - 61.0  # 同链冷却已过
    r = _run(trace_id="t1")
    assert _passed(r)
    assert reflection._reflect_daily_count == 2


def test_daily_cap(monkeypatch):
    _reset(monkeypatch, daily_count=100, daily_date=date.today().isoformat())
    _no_items(monkeypatch)
    r = _run(trace_id="t1")
    assert r["note"] == "throttled: daily_cap"


def test_daily_reset_crosses_day(monkeypatch):
    _reset(monkeypatch, daily_count=100, daily_date="2000-01-01")  # 注入"昨天"
    _no_items(monkeypatch)
    r = _run(trace_id="t1")
    assert _passed(r)  # 跨天重置后放行
    assert reflection._reflect_daily_date == date.today().isoformat()
    assert reflection._reflect_daily_count == 1


def test_none_trace_skips_trace_gate(monkeypatch):
    _reset(monkeypatch)
    _no_items(monkeypatch)
    _run(trace_id="t1")
    reflection._reflect_last_global = time.monotonic() - 100.0  # 全局冷却已过
    r = _run()  # trace_id=None：不查 trace 闸 → 放行（早退）
    assert _passed(r)
    reflection._reflect_last_global = time.monotonic() - 100.0
    r2 = _run(trace_id="t1")  # 对照：同一时刻带 trace 则被同链冷却拦
    assert r2["note"] == "throttled: trace_cooldown"
