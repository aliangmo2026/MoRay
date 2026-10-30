# -*- coding: utf-8 -*-
"""[Day16] 只读取证：查询真库中 zcode 事件与会话状态（不改任何数据）。"""
import sqlite3

conn = sqlite3.connect(r"file:D:/MoRay/server/data/moray.sqlite3?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=== zcode_task events (all) ===")
for r in conn.execute(
    "SELECT id, event_type, session_id, status, duration_ms, error_msg,"
    " source_log_id, entity_id FROM event_snapshots"
    " WHERE event_type LIKE 'zcode_task%' ORDER BY id"
):
    print(dict(r))

print()
print("=== agent_sessions zcode% ===")
for r in conn.execute("SELECT * FROM agent_sessions WHERE session_id LIKE 'zcode%'"):
    d = dict(r)
    print({k: d[k] for k in ("session_id", "status", "error_msg", "last_event_id",
                             "created_at", "updated_at")})

print()
print("=== event ids referenced by last_event_id ===")
for r in conn.execute(
    "SELECT s.session_id, s.status, s.last_event_id, e.event_type, e.status AS ev_status"
    " FROM agent_sessions s LEFT JOIN event_snapshots e ON e.id = s.last_event_id"
    " WHERE s.session_id LIKE 'zcode%'"
):
    print(dict(r))

print()
print("=== totals ===")
print("event_snapshots total =", conn.execute("SELECT COUNT(*) FROM event_snapshots").fetchone()[0])
print("zcode_task events =", conn.execute(
    "SELECT COUNT(*) FROM event_snapshots WHERE event_type LIKE 'zcode_task%'").fetchone()[0])
print("agent_sessions total =", conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0])
print("agent_tool_log total =", conn.execute("SELECT COUNT(*) FROM agent_tool_log").fetchone()[0])

print()
print("=== finished event output_json excerpt (clean run) ===")
row = conn.execute(
    "SELECT output_json FROM event_snapshots"
    " WHERE event_type='zcode_task_finished' AND session_id='zcode_day16_e2e_clean'"
).fetchone()
if row:
    print((row["output_json"] or "")[:600])
conn.close()
