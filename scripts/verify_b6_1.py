# -*- coding: utf-8 -*-
"""#1 验证：GET messages 会话不存在 → 200 空数组"""
import os
import sys
import tempfile

os.environ["MORAY_DB"] = os.path.join(tempfile.gettempdir(), "moray_b6_test.sqlite3")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    # 1) 创建会话 → GET messages 200
    client.post("/api/conversations", json={"id": "c1", "title": "t"})
    client.post("/api/conversations/c1/messages", json={"id": "m1", "conversationId": "c1", "role": "user", "content": "hi"})
    r1 = client.get("/api/conversations/c1/messages")
    print('1) existing ->', r1.status_code, 'msgs:', len(r1.json()["data"]))
    # 2) 删除后 GET → 200 空数组
    client.delete("/api/conversations/c1")
    r2 = client.get("/api/conversations/c1/messages")
    print('2) after delete ->', r2.status_code, r2.json())
    # 3) 从未存在 → 200 空数组
    r3 = client.get("/api/conversations/ghost_xyz/messages")
    print('3) never existed ->', r3.status_code, r3.json())
    # 4) 其余接口不变
    r4 = client.delete("/api/conversations/ghost_xyz")
    print('4) delete ghost ->', r4.status_code)
    assert r1.status_code == 200 and len(r1.json()["data"]) == 1
    assert r2.status_code == 200 and r2.json() == {"ok": True, "data": []}
    assert r3.status_code == 200 and r3.json() == {"ok": True, "data": []}
    assert r4.status_code == 404
    print('#1 VERIFIED')
try:
    os.remove(os.environ["MORAY_DB"])
except OSError:
    pass
