# -*- coding: utf-8 -*-
"""digital_twin 测试（v1 切块摄入回归 + v2 hybrid 召回，2026-10-24）。

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_digital_twin.py -q

隔离：MORAY_MEMORY_DB 指向 test_digital_twin_workspace 临时库（同 test_memory_embed
手法，不碰生产库）；零真实网络——嵌入面（get_embedding / search_similar）全 mock，
不依赖 Ollama 在线；MORAY_EMBED_AUTO=0 关在线补嵌线程。
"""
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_digital_twin_workspace"
TEST_DIR.mkdir(exist_ok=True)
# 必须在导入 server.app.* 之前设置（config/memory_db 在导入期读 env）
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "memory.sqlite3")
os.environ["MORAY_EMBED_AUTO"] = "0"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app import memory_db, memory_embed  # noqa: E402
from server.app import digital_twin as dt  # noqa: E402
from server.app.memory_routes import insert_memory  # noqa: E402

memory_db.init_memory_db()

_app = FastAPI()
_app.include_router(dt.router)
client = TestClient(_app)


def _u() -> str:
    """短随机串：掺进语料内容防跨运行判重命中（判重键 = source+归一化内容）。"""
    return uuid.uuid4().hex[:8]


class ChunkTextTest(unittest.TestCase):
    """chunk_text 纯函数回归（v1 行为钉死）。"""

    def test_short_text_single_chunk(self):
        self.assertEqual(dt.chunk_text("短文本"), ["短文本"])

    def test_empty_text_no_chunk(self):
        self.assertEqual(dt.chunk_text(""), [])

    def test_overlap_split_and_tail_kept(self):
        chunks = dt.chunk_text("x" * 2500, chunk_size=1000, overlap=100)
        self.assertEqual(len(chunks), 3)  # step=900：0/900/1800，尾块 700 > overlap 保留
        self.assertTrue(all(len(c) <= 1000 for c in chunks))

    def test_tail_within_overlap_dropped(self):
        # n=1850：pos 0/900/1800 三块，尾块 [1800:1850] 长 50 ≤ overlap 100
        # → 必是前块纯后缀，丢弃，剩 2 块
        self.assertEqual(len(dt.chunk_text("y" * 1850, chunk_size=1000, overlap=100)), 2)

    def test_bad_params_raise(self):
        with self.assertRaises(ValueError):
            dt.chunk_text("abc", chunk_size=0)
        with self.assertRaises(ValueError):
            dt.chunk_text("abc", chunk_size=100, overlap=100)


class CorpusBase(unittest.TestCase):
    """公共底座：每测清空 corpus 面。

    只直删 memory_bank（kind='corpus'）；memory_fts 孤儿行由 _fetch_rows 的
    JOIN memory_bank 天然排除，不影响断言。本测试文件绝不产生 embedding 行
    （嵌入面全 mock），故无 vec 残留问题。
    """

    def setUp(self):
        memory_db.init_memory_db()
        conn = memory_db.connect()
        try:
            conn.execute("DELETE FROM memory_bank WHERE kind = 'corpus'")
            conn.commit()
        finally:
            conn.close()

    def _ingest(self, text: str, name: str) -> str:
        res = dt.chunk_and_insert(text, name)
        self.assertFalse(res["failed"], res["errors"])
        self.assertGreaterEqual(res["inserted"], 1)
        return res["ids"][0]


class HybridRecallTest(CorpusBase):
    """_hybrid_recall 双通道融合（嵌入面全 mock，零真实网络）。"""

    def _seed(self):
        """三块语料：A 全 gram 命中、B 单 gram「风格偏」命中、C 与 query 零交集。"""
        id_a = self._ingest("主人的代码风格偏好是简洁优先，命名见名知义。", "文档A")
        id_b = self._ingest("主人的注释风格偏幽默，常写冷笑话。", "文档B")
        id_c = self._ingest("主人爱吃的川菜包括麻婆豆腐和回锅肉。", "文档C")
        return id_a, id_b, id_c

    def test_fts_only_when_no_embedding(self):
        id_a, id_b, id_c = self._seed()
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        ids = [it["id"] for it in items]
        self.assertIn(id_a, ids)
        self.assertIn(id_b, ids)      # B 含 gram「风格偏」→ FTS 命中
        self.assertNotIn(id_c, ids)   # C 与 query 零 gram 交集
        for it in items:
            self.assertEqual(it["matches"], ["fts"])
            self.assertIsNone(it["vec_score"])
            self.assertIsNotNone(it["fts_score"])

    def test_dual_channel_hit_ranks_first(self):
        id_a, id_b, id_c = self._seed()
        with mock.patch.object(memory_embed, "get_embedding", lambda t: [0.1] * 4), \
             mock.patch.object(memory_embed, "search_similar",
                               lambda vec, k=10: [(id_b, 0.9)]):
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        self.assertEqual(items[0]["id"], id_b)      # B 双通道命中 → RRF 第一
        self.assertEqual(items[0]["matches"], ["fts", "vec"])
        self.assertAlmostEqual(items[0]["vec_score"], 0.9, places=6)
        self.assertIn(id_a, [it["id"] for it in items])
        self.assertNotIn(id_c, [it["id"] for it in items])
        # 融合分 = 1/(60+fts_rank) + 1/(60+vec_rank)，严格大于纯 FTS 单通道分
        self.assertGreater(items[0]["score"], items[1]["score"])

    def test_vec_only_hit_included(self):
        id_a, _id_b, id_c = self._seed()
        with mock.patch.object(memory_embed, "get_embedding", lambda t: [0.2] * 4), \
             mock.patch.object(memory_embed, "search_similar",
                               lambda vec, k=10: [(id_c, 0.8)]):
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        by_id = {it["id"]: it for it in items}
        self.assertIn(id_c, by_id)
        self.assertEqual(by_id[id_c]["matches"], ["vec"])
        self.assertIsNone(by_id[id_c]["fts_score"])
        self.assertAlmostEqual(by_id[id_c]["vec_score"], 0.8, places=6)
        self.assertIn(id_a, by_id)  # FTS 面不受向量通道影响
        self.assertEqual(by_id[id_a]["matches"], ["fts"])

    def test_search_similar_raises_degrades_to_fts(self):
        id_a, _id_b, _id_c = self._seed()
        with mock.patch.object(memory_embed, "get_embedding", lambda t: [0.3] * 4), \
             mock.patch.object(memory_embed, "search_similar",
                               side_effect=RuntimeError("vec down")), \
             mock.patch.object(dt.logger, "warning"):  # 静音降级日志
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        ids = [it["id"] for it in items]
        self.assertIn(id_a, ids)
        for it in items:
            self.assertEqual(it["matches"], ["fts"])

    def test_get_embedding_raises_degrades_to_fts(self):
        id_a, _id_b, _id_c = self._seed()
        with mock.patch.object(memory_embed, "get_embedding",
                               side_effect=RuntimeError("embed down")), \
             mock.patch.object(dt.logger, "warning"):
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        self.assertIn(id_a, [it["id"] for it in items])

    def test_empty_corpus_returns_empty(self):
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            self.assertEqual(dt._hybrid_recall("代码风格偏好", limit=3), [])

    def test_limit_respected(self):
        for i in range(4):
            self._ingest("代码风格偏好条目%d_%s" % (i, _u()), "批量%d" % i)
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            items = dt._hybrid_recall("代码风格偏好", limit=2)
        self.assertEqual(len(items), 2)

    def test_non_corpus_kind_excluded_both_channels(self):
        # 同内容落成 kind='fact'（用户记忆面）：FTS（kind 过滤）与向量（回表过滤）
        # 两通道都必须排除——数字分身语料与用户记忆的结构隔离实证。
        content = "代码风格偏好干扰项_%s" % _u()
        mem_id, err = insert_memory(content, kind="fact", source="manual", tags=["干扰"])
        self.assertFalse(err, err)
        with mock.patch.object(memory_embed, "get_embedding", lambda t: [0.4] * 4), \
             mock.patch.object(memory_embed, "search_similar",
                               lambda vec, k=10: [(mem_id, 0.9)]):
            items = dt._hybrid_recall("代码风格偏好", limit=5)
        self.assertNotIn(mem_id, [it["id"] for it in items])


class TwinEndpointsTest(CorpusBase):
    """端点契约（TestClient + 最小 app；嵌入面 mock，不依赖 Ollama）。"""

    def test_ingest_endpoint(self):
        r = client.post("/api/twin/ingest",
                        json={"text": "端到端烟测语料，验证切块入库。", "name": "烟测"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body["data"]["chunks"], 1)
        self.assertGreaterEqual(body["data"]["inserted"], 1)

    def test_recall_default_hybrid_top3(self):
        for i in range(4):
            self._ingest("代码风格偏好条目%d_%s" % (i, _u()), "批量%d" % i)
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            r = client.post("/api/twin/recall", json={"query": "代码风格偏好"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body["data"]["mode"], "hybrid")
        self.assertEqual(len(body["data"]["items"]), 3)  # 默认 top-3
        for it in body["data"]["items"]:
            self.assertIn("matches", it)

    def test_recall_fts_mode_v1_compat(self):
        self._ingest("主人的代码风格偏好是简洁优先。", "文档A")
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            r = client.post("/api/twin/recall",
                            json={"query": "代码风格偏好", "mode": "fts", "limit": 2})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(body["data"]["mode"], "fts")
        self.assertLessEqual(len(body["data"]["items"]), 2)
        for it in body["data"]["items"]:
            self.assertNotIn("matches", it)  # v1 字段形状零增量
            self.assertNotIn("fts_score", it)

    def test_recall_short_query_400(self):
        r = client.post("/api/twin/recall", json={"query": "ab"})
        self.assertEqual(r.status_code, 400)
        body = r.json()
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("code"), "bad_request")

    def test_ingest_then_recall_roundtrip(self):
        r = client.post("/api/twin/ingest",
                        json={"text": "主人的代码风格偏好是先写测试再实现。", "name": "闭环文档"})
        self.assertEqual(r.status_code, 200)
        with mock.patch.object(memory_embed, "get_embedding", lambda t: None):
            r2 = client.post("/api/twin/recall", json={"query": "代码风格偏好"})
        self.assertEqual(r2.status_code, 200)
        items = r2.json()["data"]["items"]
        self.assertTrue(items)
        self.assertIn("代码风格偏好", items[0]["content"])
        self.assertEqual(items[0]["source"], "digital_twin")


if __name__ == "__main__":
    unittest.main()
