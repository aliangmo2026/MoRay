# -*- coding: utf-8 -*-
"""[Day17] 只读取证：查询真库中 Day17 zcode 事件与会话状态（不改任何数据）。"""
import sqlite3

SID = "zcode_day17_ppipe_fix"

conn = sqlite3.connect(r"file:D:/MoRay/server/data/moray.sqlite3?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=== zcode_task events (day17 session) ===")
for r in conn.execute(
    "SELECT id, event_type, session_id, status, duration_ms, error_msg,"
    " source_log_id, entity_id FROM event_snapshots"
    " WHERE session_id = ? ORDER BY id", (SID,)
):
    print(dict(r))

print()
print("=== started+finished pairing for day17 session ===")
for r in conn.execute(
    "SELECT event_type, COUNT(*) AS n FROM event_snapshots"
    " WHERE session_id = ? AND event_type LIKE 'zcode_task%'"
    " GROUP BY event_type", (SID,)
):
    print(dict(r))

print()
print("=== agent_sessions day17 ===")
for r in conn.execute(
    "SELECT session_id, status, error_msg, last_event_id, created_at, updated_at"
    " FROM agent_sessions WHERE session_id = ?", (SID,)
):
    print(dict(r))

print()
print("=== event ids referenced by last_event_id (day17) ===")
for r in conn.execute(
    "SELECT s.session_id, s.status, s.last_event_id, e.event_type, e.status AS ev_status"
    " FROM agent_sessions s LEFT JOIN event_snapshots e ON e.id = s.last_event_id"
    " WHERE s.session_id = ?", (SID,)
):
    print(dict(r))

print()
print("=== finished event: status/duration_ms/output 契约列 ===")
for r in conn.execute(
    "SELECT id, event_type, status, duration_ms, error_msg, output_json"
    " FROM event_snapshots WHERE session_id = ? AND event_type='zcode_task_finished'",
    (SID,)
):
    d = dict(r)
    print("id=%s status=%s duration_ms=%s error_msg=%r" %
          (d["id"], d["status"], d["duration_ms"], d["error_msg"]))
    print("output_json =", (d["output_json"] or "")[:1200])

print()
print("=== started event snapshot_json 契约字段（tool/args/approved/ms/...）===")
row = conn.execute(
    "SELECT snapshot_json FROM event_snapshots"
    " WHERE session_id = ? AND event_type='zcode_task_started'", (SID,)
).fetchone()
if row and row["snapshot_json"]:
    import json
    snap = json.loads(row["snapshot_json"])
    print("keys =", sorted(snap.keys()))
    print("tool =", snap.get("tool"), "| tool_name =", snap.get("tool_name"),
          "| approved =", snap.get("approved"), "| ms =", snap.get("ms"),
          "| session_id =", snap.get("session_id"),
          "| schema_version =", snap.get("schema_version"))

print()
print("=== totals ===")
print("event_snapshots total =", conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0])
print("zcode_task events =", conn.execute(
    "SELECT COUNT(*) FROM event_snapshots WHERE event_type LIKE 'zcode_task%'").fetchone()[0])
print("agent_sessions total =", conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0])
print("agent_tool_log total =", conn.execute("SELECT COUNT(*) FROM agent_tool_log").fetchone()[0])
conn.close()
