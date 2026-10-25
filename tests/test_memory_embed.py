# -*- coding: utf-8 -*-
"""memory_embed 单元测试（2026-10-24 深海阶段一：向量召回）。

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_embed.py -v

隔离：MORAY_MEMORY_DB 指向 test_memory_embed_workspace 临时库（不入库不碰生产
数据）；零真实网络——嵌入调用全走 mock（get_embedding fail-open 三态：网络炸/
非 200/维度不符），不依赖 Ollama 在线。
"""
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_memory_embed_workspace"
TEST_DIR.mkdir(exist_ok=True)
# 必须在导入 server.app.config 之前设置（config 在导入期读 env）
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "memory.sqlite3")

from server.app import config, memory_db, memory_embed  # noqa: E402

memory_db.init_memory_db()
DIM = memory_db.EMBED_DIM


def _unit_vec(idx: int) -> list:
    """第 idx 维为 1 的单位向量（768 维，余弦测试用正交基）。"""
    v = [0.0] * DIM
    v[idx % DIM] = 1.0
    return v


class PackUnpackTest(unittest.TestCase):
    def test_roundtrip(self):
        vec = _unit_vec(3)
        blob = memory_embed.pack_vector(vec)
        self.assertEqual(len(blob), DIM * 4)  # float32 = 4 字节/维
        back = memory_embed.unpack_vector(blob)
        self.assertEqual(back, vec)

    def test_unpack_bad_blob_returns_none(self):
        self.assertIsNone(memory_embed.unpack_vector(None))
        self.assertIsNone(memory_embed.unpack_vector(b""))
        self.assertIsNone(memory_embed.unpack_vector(b"\x00" * 7))  # 长度不符
        self.assertIsNone(memory_embed.unpack_vector("not-bytes"))


class CosineTest(unittest.TestCase):
    def test_identical_is_one(self):
        v = [0.5] * DIM
        self.assertAlmostEqual(memory_embed.cosine(v, v), 1.0, places=5)

    def test_orthogonal_is_zero(self):
        self.assertAlmostEqual(
            memory_embed.cosine(_unit_vec(0), _unit_vec(1)), 0.0, places=6)

    def test_zero_and_mismatch_are_zero(self):
        self.assertEqual(memory_embed.cosine([0.0] * DIM, _unit_vec(0)), 0.0)
        self.assertEqual(memory_embed.cosine([0.1, 0.2], [0.1, 0.2, 0.3]), 0.0)


class _FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class _FakeClient:
    """httpx.Client 桩：post 返回预置响应或抛预置异常。"""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, *args, **kwargs):
        resp = getattr(_FakeClient, "next_response", None)
        if isinstance(resp, Exception):
            raise resp
        return resp


class GetEmbeddingFailOpenTest(unittest.TestCase):
    """get_embedding 三态 fail-open：网络炸 / 非 200 / 维度不符 → 一律 None 不抛。"""

    def setUp(self):
        self._patcher = mock.patch.object(memory_embed, "httpx")
        fake_httpx = self._patcher.start()
        fake_httpx.Client = _FakeClient

    def tearDown(self):
        self._patcher.stop()

    def test_empty_text_is_none(self):
        self.assertIsNone(memory_embed.get_embedding(""))

    def test_network_error_is_none(self):
        _FakeClient.next_response = RuntimeError("net down")
        self.assertIsNone(memory_embed.get_embedding("任意文本"))

    def test_non200_is_none(self):
        _FakeClient.next_response = _FakeResponse(status=500, body={})
        self.assertIsNone(memory_embed.get_embedding("任意文本"))

    def test_dim_mismatch_is_none(self):
        _FakeClient.next_response = _FakeResponse(
            status=200, body={"embedding": [0.1, 0.2]})
        self.assertIsNone(memory_embed.get_embedding("任意文本"))

    def test_ok_returns_vec(self):
        vec = [0.25] * DIM
        _FakeClient.next_response = _FakeResponse(
            status=200, body={"embedding": vec})
        self.assertEqual(memory_embed.get_embedding("任意文本"), vec)


class IndexAndSearchTest(unittest.TestCase):
    """index_memory + search_similar 全链路（隔离库；嵌入 mock 成单位向量）。"""

    def setUp(self):
        memory_db.init_memory_db()
        self._conn = memory_db.connect()
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "INSERT INTO memory_bank (id, kind, content) VALUES (?, ?, ?)",
                ("test-mem-1", "insight", "引号嵌套错误的修复要点是先剥外层引号"))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
        finally:
            self._conn.close()
        self._patcher = mock.patch.object(
            memory_embed, "get_embedding",
            lambda text: _unit_vec(5) if text else None)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_index_then_search_hits(self):
        res = memory_embed.index_memory("test-mem-1", "引号嵌套错误的修复要点",
                                        kind="insight")
        self.assertTrue(res.get("ok"), res)
        hits = memory_embed.search_similar(_unit_vec(5), k=5)
        self.assertIn("test-mem-1", [hid for hid, _score in hits])
        # 分值形状：vec 路径 1/(1+d) ≤ 1；兜底路径余弦 ≤ 1
        score = dict(hits)["test-mem-1"]
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0 + 1e-6)

    def test_kind_filter_pushed_down(self):
        """[vec kind 分区 2026-10-25] kind 下推：同 k 下正确 kind 命中、错 kind
        零命中（vec0 元数据列真过滤实证）。"""
        res = memory_embed.index_memory("test-mem-1", "引号嵌套错误的修复要点",
                                        kind="insight")
        self.assertTrue(res.get("ok"), res)
        hits_hit = memory_embed.search_similar(_unit_vec(5), k=5, kind="insight")
        self.assertIn("test-mem-1", [hid for hid, _s in hits_hit])
        hits_miss = memory_embed.search_similar(_unit_vec(5), k=5, kind="corpus")
        self.assertNotIn("test-mem-1", [hid for hid, _s in hits_miss])
        # vec 行的 kind 列确实落库
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT kind FROM memory_vec WHERE memory_id = ?",
                ("test-mem-1",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["kind"], "insight")

    def test_index_row_gone_is_failopen(self):
        res = memory_embed.index_memory("no-such-row", "任意文本")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("reason"), "row_gone")


class VecKindMigrationTest(unittest.TestCase):
    """[vec kind 分区 2026-10-25] _migrate_vec_kind 幂等：旧 schema（无 kind 列）
    首次 connect 自动 DROP+重建+回灌；第二次 connect 零改动行数不变。
    用独立临时库 + 裸 sqlite3 造旧世界（绕开 connect 的自动建表）。"""

    def test_migrate_kind_idempotent(self):
        db_path = TEST_DIR / "memory_migrate.sqlite3"
        if db_path.exists():
            db_path.unlink()
        import sqlite_vec
        raw = sqlite3.connect(str(db_path))
        try:
            raw.enable_load_extension(True)
            sqlite_vec.load(raw)
            raw.enable_load_extension(False)
            raw.executescript(memory_db.SCHEMA)  # bank/FTS/self_state_log，无 vec
            raw.execute(
                "CREATE VIRTUAL TABLE memory_vec USING vec0("
                "memory_id TEXT PRIMARY KEY, embedding float[768])")  # 旧 DDL
            raw.execute("INSERT INTO memory_bank (id, kind, content) VALUES (?, ?, ?)",
                        ("mig-1", "corpus", "语料A带嵌入"))
            raw.execute("INSERT INTO memory_bank (id, kind, content) VALUES (?, ?, ?)",
                        ("mig-2", "fact", "备忘B无嵌入"))
            raw.execute("UPDATE memory_bank SET embedding = ? WHERE id = ?",
                        (memory_embed.pack_vector([0.5] * DIM), "mig-1"))
            raw.commit()
        finally:
            raw.close()

        # 第一次 connect：sniff 旧 schema → 迁移。强制 VEC 探测重跑（进程内
        # 可能已被其他测试置位）。
        with mock.patch.object(config, "MEMORY_DB", str(db_path)), \
             mock.patch.object(memory_db, "VEC_AVAILABLE", False), \
             mock.patch.object(memory_db, "_VEC_PROBED", False):
            conn = memory_db.connect()
            try:
                self.assertTrue(memory_db.VEC_AVAILABLE)
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'memory_vec'"
                ).fetchone()[0]
                self.assertIn("kind", sql)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0], 1)
                row = conn.execute(
                    "SELECT memory_id, kind FROM memory_vec").fetchone()
                self.assertEqual(row["memory_id"], "mig-1")
                self.assertEqual(row["kind"], "corpus")
            finally:
                conn.close()

            # 第二次 connect：幂等——新 schema 直接跳过，行数不变零报错
            conn = memory_db.connect()
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0], 1)
                sql2 = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'memory_vec'"
                ).fetchone()[0]
                self.assertIn("kind", sql2)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
