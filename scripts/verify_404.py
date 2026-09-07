# -*- coding: utf-8 -*-
"""#10 验证：GET 不存在会话的 messages → 404"""
import os
import sys
import tempfile

os.environ["MORAY_DB"] = os.path.join(tempfile.gettempdir(), "moray_404_test.sqlite3")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    r = client.get("/api/conversations/nonexistent_conv_xyz/messages")
    print('GET messages for missing conv ->', r.status_code, r.json())
    # 存在会话 → 200
    c = client.post("/api/conversations", json={"id": "c1", "title": "t"}).json()
    r2 = client.get("/api/conversations/c1/messages")
    print('existing conv messages ->', r2.status_code, r2.json())
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert r2.status_code == 200
    print('#10 VERIFIED')
try:
    os.remove(os.environ["MORAY_DB"])
except OSError:
    pass
