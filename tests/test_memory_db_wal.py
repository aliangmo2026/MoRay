# -*- coding: utf-8 -*-
r"""memory_db WAL 回归（体检修复2 2026-11-14）：记忆库连接必须落 WAL。

修复前实证：memory_db.connect 只有 busy_timeout=5000，journal_mode 默认 delete
（读写互斥）；与 db.py 同款修复——WAL 不可用介质退回 DELETE 不阻塞启动。

跑法：D:/python3.12/python.exe -m pytest tests/test_memory_db_wal.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import memory_db  # noqa: E402


def test_memory_db_wal_mode():
    conn = memory_db.connect()
    try:
        mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        assert mode.lower() == "wal", "修复前记忆库 journal_mode=delete（读写互斥）"
    finally:
        conn.close()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
