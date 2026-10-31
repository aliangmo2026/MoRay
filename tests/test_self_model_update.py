# -*- coding: utf-8 -*-
"""self_model 单行语义回归测试（2026-10-31 修复 kind='self' 堆日记 bug）

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_self_model_update.py -x -q

隔离（照抄 test_memory_bridge 惯例）：
- MORAY_EMBED_AUTO=0 关掉后台补嵌（否则嵌入线程摸真 Ollama、还可能握住临时库文件）；
- 每条用例把 config.MEMORY_DB 钉到 pytest tmp_path 下的临时库，绝不碰真实
  D:\\MoRay\\server\\data\\memory.sqlite3；
- 三重守卫里的进程内状态（_last_self_model_monotonic / _last_snapshot）逐次用
  monkeypatch 清零：不清冷却会被 24h 硬闸挡在写库之前，不清快照会被 no_change
  挡在 dup 判定之前（那样测不到本 bug）。
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 必须在导入 server.app.config 之前设置
os.environ["MORAY_EMBED_AUTO"] = "0"

from server.app import config, memory_db  # noqa: E402
from server.app import self_model as sm  # noqa: E402


def _init_db(tmp_path, monkeypatch):
    """临时库建表 + 钉死 config.MEMORY_DB（防 import 顺序漂移）。"""
    db_path = tmp_path / "mem.sqlite3"
    monkeypatch.setattr(config, "MEMORY_DB", str(db_path))
    memory_db.init_memory_db()
    return db_path


def _reset_guards(monkeypatch):
    """冷却清零（设在很久以前）+ 快照清空——否则 no_change/cooldown 先返回。"""
    monkeypatch.setattr(sm, "_last_self_model_monotonic", time.monotonic() - 10 ** 7)
    monkeypatch.setattr(sm, "_last_snapshot", {})


def _read(db_path):
    """(self 行列表, 非 self 行数, fts 行列表) —— 只读。"""
    conn = sqlite3.connect(str(db_path))
    try:
        self_rows = conn.execute(
            "SELECT id, content FROM memory_bank WHERE kind = 'self'").fetchall()
        others = conn.execute(
            "SELECT COUNT(*) FROM memory_bank WHERE kind <> 'self'").fetchone()[0]
        fts_rows = conn.execute(
            "SELECT memory_id, content FROM memory_fts").fetchall()
    finally:
        conn.close()
    return self_rows, others, fts_rows


def _add_task_result(db_path, n=1):
    """直接插 kind='task_result' 行，把 _read_memory_counts 的计数顶上去 → 内容必变。"""
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO memory_bank (id, kind, content, tags, source, entity_id, "
                "session_id, created_at, updated_at) VALUES (?, 'task_result', ?, '', "
                "'zcode_stream', '', 'default', datetime('now','localtime'), "
                "datetime('now','localtime'))",
                ("tr%08d" % i, "任务结果 %d：占位内容" % i))
        conn.commit()
    finally:
        conn.close()


def test_1_insert_when_no_self_row(tmp_path, monkeypatch):
    """①库里没有 kind='self' 行 → 插入一条，reason='ok'，恰好 1 行。"""
    db = _init_db(tmp_path, monkeypatch)
    _reset_guards(monkeypatch)

    res = sm.self_model_maybe()

    assert res["ran"] is True, res
    assert res["reason"] == "ok", res
    self_rows, _others, fts_rows = _read(db)
    assert len(self_rows) == 1, self_rows
    assert self_rows[0][1] == res["content"]
    # insert 路径必须双写 FTS（库内零触发器，靠应用层手工写）
    assert fts_rows == [(self_rows[0][0], res["content"])], fts_rows


def test_2_update_in_place_keeps_single_row(tmp_path, monkeypatch):
    """②内容变化再跑 → 行数仍为 1、正文已更新、id 不变、FTS 同步、非 self 行零改动。"""
    db = _init_db(tmp_path, monkeypatch)
    _reset_guards(monkeypatch)
    first = sm.self_model_maybe()
    assert first["reason"] == "ok", first
    before_rows, _others, _fts = _read(db)
    before_id, before_content = before_rows[0]

    _add_task_result(db, 1)          # 计数变化 → 内容必然变
    _reset_guards(monkeypatch)       # 绕开冷却与 no_change
    second = sm.self_model_maybe()

    assert second["ran"] is True, second
    assert second["reason"] == "updated", second
    assert second["content"] != before_content
    self_rows, others, fts_rows = _read(db)
    assert len(self_rows) == 1, self_rows              # ← bug 断言：不再堆新行
    assert self_rows[0][0] == before_id                # 就地更新，id 不变
    assert self_rows[0][1] == second["content"]        # 正文已更新
    assert others == 1                                 # 非 self 行没被碰
    # FTS 必须跟着改（裸 UPDATE memory_bank 会留下旧正文的倒排）
    assert fts_rows == [(before_id, second["content"])], fts_rows


def test_3_dup_when_content_unchanged(tmp_path, monkeypatch):
    """③内容不变再跑 → reason='dup'、行数仍为 1、正文不动。"""
    db = _init_db(tmp_path, monkeypatch)
    _reset_guards(monkeypatch)
    assert sm.self_model_maybe()["reason"] == "ok"
    _add_task_result(db, 1)
    _reset_guards(monkeypatch)
    updated = sm.self_model_maybe()
    assert updated["reason"] == "updated", updated

    _reset_guards(monkeypatch)       # 只清守卫：计数没变 → 内容与库内逐字相同
    third = sm.self_model_maybe()

    assert third["ran"] is False, third
    assert third["reason"] == "dup", third
    self_rows, _others, fts_rows = _read(db)
    assert len(self_rows) == 1, self_rows
    assert self_rows[0][1] == updated["content"]
    assert fts_rows == [(self_rows[0][0], updated["content"])], fts_rows
