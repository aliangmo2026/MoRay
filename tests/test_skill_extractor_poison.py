# -*- coding: utf-8 -*-
r"""skill_extractor.scan_once 毒丸/事务纪律回归测试（体检修复 2026-11-14）。

修复前实证行为（体检静态审查 skill_extractor.py:459-502）：
  * 外层连接横跨模型调用持有未提交写事务 → 与 _store_rule 的第二连接撞 RESERVED 锁；
  * 单行异常被毒丸 except 吞掉后 checkpoint 照常前移 → 该行规则**永久丢失**。
修复后断言：
  1. 毒丸行（提炼模型抛异常）→ checkpoint 不前移、failed_rows 记账、error 置位；
  2. 对账 DML 先行提交：毒丸行自己的 fail_streak 已落库（游标幂等防重算）；
  3. 模型恢复后重扫：毒丸行正常通过、checkpoint 推进、规则落库不丢。

跑法：D:/python3.12/python.exe -m pytest tests/test_skill_extractor_poison.py -v
（库隔离：conftest 兜底 + 本文件把 checkpoint 钳到自播行，互不污染）
"""
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import memory_db, skill_extractor  # noqa: E402

memory_db.init_memory_db()  # 临时库幂等建表（connect 不自动建表）
_ck_conn = memory_db.connect()
try:
    skill_extractor._ensure_tables(_ck_conn)  # 本模块专属表（stats/state）不归 memory_db 建
finally:
    _ck_conn.close()


def _max_rowid(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM memory_bank").fetchone()
    return int(row[0] or 0)


def _seed_row(kind: str, content: str, tags: str = "", created_at: str = "") -> tuple:
    """直插一条 memory_bank（只动测试临时库），返回 (id, rowid)。"""
    rid = uuid.uuid4().hex
    ts = created_at or time.strftime("%Y-%m-%dT%H:%M:%S")
    conn = memory_db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_bank (id, kind, content, summary, tags, source, entity_id,"
            " session_id, weight, embedding, embedding_model, created_at, updated_at)"
            " VALUES (?, ?, ?, '', ?, 'poison-test', '', 'poison-test', 1.0, '', '', ?, ?)",
            (rid, kind, content, tags, ts, ts))
        conn.commit()
        rowid = int(conn.execute(
            "SELECT rowid FROM memory_bank WHERE id = ?", (rid,)).fetchone()[0])
    finally:
        conn.close()
    return rid, rowid


def _set_checkpoint(v: int) -> None:
    conn = memory_db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO skill_extractor_state (key, value) VALUES ('scan_rowid', ?)",
            (str(v),))
        conn.commit()
    finally:
        conn.close()


def _get_checkpoint() -> int:
    conn = memory_db.connect()
    try:
        row = conn.execute(
            "SELECT value FROM skill_extractor_state WHERE key = 'scan_rowid'").fetchone()
        return int(row["value"]) if row else 0
    finally:
        conn.close()


def _stats_row(rule_id: str) -> tuple:
    conn = memory_db.connect()
    try:
        row = conn.execute(
            "SELECT injections, fail_streak, last_outcome_at FROM skill_rule_stats"
            " WHERE rule_id = ?", (rule_id,)).fetchone()
        return (row["injections"], row["fail_streak"], row["last_outcome_at"]) if row else None
    finally:
        conn.close()


def test_poison_row_does_not_advance_checkpoint(monkeypatch):
    conn = memory_db.connect()
    ck_base = _max_rowid(conn)
    conn.close()
    rid_ok, rid_ok_row = _seed_row("task_result", "poison-test 成功任务 rc=0", tags="ok")
    rid_bad, rid_bad_row = _seed_row("insight", "poison-test 失败经验：应先备份再改配置")
    _set_checkpoint(ck_base)

    def _boom(text):
        raise RuntimeError("model down (simulated)")

    monkeypatch.setattr(skill_extractor, "_call_extract_model", _boom)
    stats = skill_extractor.scan_once()

    assert stats["failed_rows"], "毒丸行必须记入 failed_rows"
    assert stats["error"], "stats.error 必须置位"
    ck_after = _get_checkpoint()
    assert ck_after < rid_bad_row, (
        "修复前 checkpoint 会越过毒丸行（%d >= %d）——规则永久丢失；修复后必须停在它之前"
        % (ck_after, rid_bad_row))
    assert ck_after == rid_ok_row, "前一行（成功 task_result）应正常完成并前移 checkpoint"
    assert stats["accounted"] >= 1, "前一行对账应已提交"


def test_recovery_after_poison(monkeypatch):
    rid, rid_row = _seed_row("insight", "poison-test 恢复场景：重启后提炼应成功")
    _set_checkpoint(rid_row - 1)

    def _ok(text):
        return "[]"

    monkeypatch.setattr(skill_extractor, "_call_extract_model", _ok)
    stats = skill_extractor.scan_once()

    assert not stats["failed_rows"], "模型恢复后不应再有 failed_rows"
    assert _get_checkpoint() >= rid_row, "checkpoint 必须推进过毒丸行"


def test_rule_survives_and_lands_in_bank(monkeypatch):
    rid, rid_row = _seed_row("task_result", "poison-test 失败：路径含空格未加引号导致 404",
                             tags="failure")
    _set_checkpoint(rid_row - 1)

    def _ok(text):
        return json.dumps(["在执行包含空格的路径时，确保路径以引号包围。"], ensure_ascii=False)

    monkeypatch.setattr(skill_extractor, "_call_extract_model", _ok)
    stats = skill_extractor.scan_once()

    assert _get_checkpoint() >= rid_row, "提炼成功后 checkpoint 必须前移"
    landed = [d for d in stats["extracted"] if d.get("source_record_id") == rid]
    assert landed, "提炼出的规则必须出现在 stats.extracted"
    assert landed[0]["action"] in ("inserted", "reinforced", "skipped_duplicate")


def test_accounting_survives_model_failure(monkeypatch):
    rule_id = uuid.uuid4().hex
    ts0 = "2026-11-13T08:00:00"
    ts1 = "2026-11-13T09:00:00"
    conn = memory_db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_bank (id, kind, content, summary, tags, source, entity_id,"
            " session_id, weight, embedding, embedding_model, created_at, updated_at)"
            " VALUES (?, 'skill_rule', 'poison-test 对账用规则', '', 'skill_rule',"
            " 'skill_extractor', '', 'poison-test', 0.8, '', '', ?, ?)",
            (rule_id, ts0, ts0))
        conn.execute(
            "INSERT INTO skill_rule_stats (rule_id, injections, fail_streak,"
            " last_injected_at, last_outcome_at, archived) VALUES (?, 1, 0, ?, '', 0)",
            (rule_id, ts0))
        conn.commit()
    finally:
        conn.close()
    rid, rid_row = _seed_row("task_result", "poison-test 失败任务：退出码：1", tags="failure",
                             created_at=ts1)
    _set_checkpoint(rid_row - 1)

    def _boom(text):
        raise RuntimeError("model down (simulated)")

    monkeypatch.setattr(skill_extractor, "_call_extract_model", _boom)
    stats = skill_extractor.scan_once()

    assert stats["failed_rows"], "该行提炼必然失败（毒丸）"
    row = _stats_row(rule_id)
    assert row is not None, "统计行不应消失"
    assert int(row[1]) == 1, (
        "对账 DML 在模型调用前已提交：fail_streak 必须已 +1（不随毒丸丢失），实际=%r" % (row[1],))
    assert row[2] >= ts1, "last_outcome_at 游标必须已推进（幂等防重算）"
    assert _get_checkpoint() < rid_row, "checkpoint 仍不得越过毒丸行"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
