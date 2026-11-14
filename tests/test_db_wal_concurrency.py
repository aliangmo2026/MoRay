# -*- coding: utf-8 -*-
r"""主库并发卫生回归测试（体检修复 2026-11-14）。

修复前实证行为（体检静态审查 + 压测窗口）：
  * db.connect() 无 timeout（busy_timeout=0）→ 写锁竞争立即抛 "database is locked"；
  * 无 WAL → 读写互斥。
修复后断言：
  1. journal_mode = WAL；
  2. busy_timeout = 5000ms；
  3. 写者持 BEGIN IMMEDIATE 锁 1s 期间，另一连接的写操作排队等锁成功（不炸）。

跑法：D:/python3.12/python.exe -m pytest tests/test_db_wal_concurrency.py -v
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import config, db  # noqa: E402


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """把 config.SQLITE_PATH 指向本测试专属临时库（db.connect 按调用时属性取路径）。"""
    p = tmp_path / "unit_moray.sqlite3"
    monkeypatch.setattr(config, "SQLITE_PATH", p)
    db.init_db()
    return p


def test_wal_mode_enabled(tmp_db):
    conn = db.connect()
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        assert mode.lower() == "wal", "修复后主库连接必须落 WAL（修复前为 delete）"
    finally:
        conn.close()


def test_busy_timeout_set(tmp_db):
    conn = db.connect()
    try:
        bt = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        assert bt == 5000, "修复后 busy_timeout 必须为 5000ms（修复前为 0）"
    finally:
        conn.close()


def test_writer_contention_waits_not_explodes(tmp_db):
    """写者1（主线程）持 BEGIN IMMEDIATE 锁 ~1.2s；写者2 在自己的线程里连接并 INSERT——
    必须排队等锁成功（busy_timeout=5000），而不是 busy_timeout=0 时代立即抛
    database is locked。每个连接整线程自用（check_same_thread 纪律）。"""
    c1 = db.connect()
    result = {"elapsed": None, "error": None}
    try:
        c1.execute("BEGIN IMMEDIATE")
        c1.execute(
            "INSERT INTO kv (key, value) VALUES ('lock_holder', '1')")

        def contender():
            try:
                c2 = db.connect()
                try:
                    time.sleep(0.3)  # 确保写者1已持锁
                    t0 = time.monotonic()
                    c2.execute(
                        "INSERT INTO kv (key, value) VALUES ('contender', '1')")
                    c2.commit()
                    result["elapsed"] = time.monotonic() - t0
                finally:
                    c2.close()
            except Exception as e:  # noqa: BLE001 - 失败记入 result 供主线程断言
                result["error"] = "%s: %s" % (type(e).__name__, e)

        t = threading.Thread(target=contender)
        t.start()
        time.sleep(1.2)  # 写者1 持锁窗口
        c1.commit()
        t.join(timeout=10.0)

        assert result["error"] is None, (
            "修复前 busy_timeout=0 会立即抛 database is locked；实际=%r" % result["error"])
        assert result["elapsed"] is not None and result["elapsed"] >= 0.5, (
            "写者2 应排队等锁 ~1s（实际 elapsed=%r）" % result["elapsed"])
    finally:
        c1.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
