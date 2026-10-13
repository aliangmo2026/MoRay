# -*- coding: utf-8 -*-
"""MemoryBridge MVP 自动化测试（2026-10-02 M1）

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_bridge.py -v

隔离（照抄 test_cloud_budget_guard / test_budget 惯例）：
- MORAY_MEMORY_DB / MORAY_DB / MORAY_WORKSPACE 全部指向 test_memory_bridge_workspace\\，
  必须在导入 server.app.config 之前设置（config 在导入期读 env）；
- 每条用例独立临时记忆库文件（setUp 创建 / tearDown 删除），互不残留；
- 自建 FastAPI app 只挂 memory_routes.router（test_budget 模式），
  不触发 main.py lifespan（不碰 Argus 订阅循环、不碰主库 init_db 之外的钩子）；
- 零真实网络。
"""
import asyncio
import os
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_memory_bridge_workspace"
TEST_DIR.mkdir(exist_ok=True)
# 必须在导入 server.app.config 之前设置（config 在导入期读 env）
os.environ["MORAY_WORKSPACE"] = str(TEST_DIR)
os.environ["MORAY_DB"] = str(TEST_DIR / "events.sqlite3")
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "bootstrap_memory.sqlite3")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import config, memory_db, memory_routes

app = FastAPI()
app.include_router(memory_routes.router)


class MemoryBridgeTestBase(unittest.TestCase):
    """每条用例：独立临时记忆库 + 显式钉死 config.MEMORY_DB（防 import 顺序漂移）。"""

    def setUp(self):
        self._saved_memory_db = config.MEMORY_DB
        self.mem_db = TEST_DIR / ("mem_%s.sqlite3" % uuid.uuid4().hex[:8])
        config.MEMORY_DB = str(self.mem_db)
        memory_db.init_memory_db()  # 自建 app 不跑 lifespan，建表在此显式做
        self.client = TestClient(app)

    def tearDown(self):
        config.MEMORY_DB = self._saved_memory_db
        self.client.close()
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = Path(str(self.mem_db) + suffix)
            if p.exists():
                p.unlink()

    # ---- 公共助手 ----

    def _save(self, content, **kw):
        body = {"content": content}
        body.update(kw)
        return self.client.post("/api/memory/save", json=body)

    def _recall(self, query, **kw):
        body = {"query": query}
        body.update(kw)
        return self.client.post("/api/memory/recall", json=body)


class TestMemorySave(MemoryBridgeTestBase):

    def test_save_ok(self):
        """1. save 一条 → 200 + ok + 32 位 hex memory_id"""
        r = self._save("用户喜欢简洁的代码风格", kind="preference", tags=["style"])
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["data"]["memory_id"])
        self.assertEqual(len(data["data"]["memory_id"]), 32)

    def test_save_empty_content_400(self):
        """2. content 空 → 400 bad_request"""
        r = self._save("   ")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_request")

    def test_save_bad_json(self):
        """11.（附加）请求体非 JSON → 400 bad_json"""
        r = self.client.post("/api/memory/save", content=b"{bad json",
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_json")


class TestMemoryRecall(MemoryBridgeTestBase):

    def test_recall_hit(self):
        """3. save "修复 f-string 引号嵌套错误" → recall "f-string" → 命中"""
        self._save("修复 f-string 引号嵌套错误", tags=["python"])
        r = self._recall("f-string")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertIn("f-string", items[0]["content"])
        self.assertEqual(items[0]["tags"], ["python"])

    def test_recall_chinese_trigram(self):
        """4. save "中文记忆测试" → recall "中文记忆" → 命中（trigram 中文滑窗）"""
        self._save("中文记忆测试")
        r = self._recall("中文记忆")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "中文记忆测试")

    def test_recall_no_hit(self):
        """5. recall 不相关 query → 空列表"""
        self._save("完全无关的内容甲")
        self._save("完全无关的内容乙")
        r = self._recall("zxq9nonexistent")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["items"], [])

    def test_recall_query_too_short(self):
        """6. query < 3 字符 → 400"""
        r = self._recall("ab")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_request")

    def test_recall_kind_filter(self):
        """13.（附加）kind 过滤：命中 / 不命中两向"""
        self._save("偏好检索测试偏好词", kind="preference")
        hit = self._recall("偏好检索测试", kind="preference")
        self.assertEqual(len(hit.json()["data"]["items"]), 1)
        miss = self._recall("偏好检索测试", kind="fact")
        self.assertEqual(miss.json()["data"]["items"], [])

    def test_recall_tags_filter(self):
        """14.（附加）tags 过滤：命中 / 不命中两向"""
        self._save("标签过滤测试标签词", tags=["alpham1"])
        hit = self._recall("标签过滤测试", tags=["alpham1"])
        self.assertEqual(len(hit.json()["data"]["items"]), 1)
        miss = self._recall("标签过滤测试", tags=["omegam1"])
        self.assertEqual(miss.json()["data"]["items"], [])

    def test_time_decay_order(self):
        """9. 新旧两条同一关键词 → 新条目 score 更高（旧条目回拨 60 天，无 sleep 依赖）"""
        id_old = self._save("旧条目 keywordzeta 部署手册").json()["data"]["memory_id"]
        id_new = self._save("新条目 keywordzeta 部署手册").json()["data"]["memory_id"]
        # 直接改库回拨旧条目 created_at（同秒写入无法用真实时间差区分，回拨是确定性手段）
        old_ts = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
        conn = sqlite3.connect(str(self.mem_db))
        try:
            conn.execute("UPDATE memory_bank SET created_at = ? WHERE id = ?", (old_ts, id_old))
            conn.commit()
        finally:
            conn.close()
        r = self._recall("keywordzeta", limit=10)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], id_new)
        self.assertEqual(items[1]["id"], id_old)
        self.assertGreater(items[0]["score"], items[1]["score"])

    def test_recall_chinese_long_query(self):
        """16.（M3a）中文长句滑窗：query 不是 content 连续子串，旧短语逻辑必 miss，
        仅 3-gram 滑窗 OR 可命中（共享 gram "引号嵌"/"号嵌套"）。
        样本经裁定修正：原样本 "帮我改那个引号问题" 与该记忆零 3-gram 交集，
        任何 trigram 方案都无法命中（2026-10-02 探针实锤）。"""
        self._save("修复 f-string 引号嵌套错误", tags=["bug"])
        r = self._recall("帮我改那个引号嵌套问题")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "修复 f-string 引号嵌套错误")

    def test_recall_chinese_partial_match(self):
        """17.（M3a）中文部分匹配：query "代码风格偏好" 召回 "用户喜欢简洁代码风格"
        （共享 3-gram "代码风"/"码风格"；旧短语逻辑 miss）。"""
        self._save("用户喜欢简洁代码风格")
        r = self._recall("代码风格偏好")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "用户喜欢简洁代码风格")

    def test_recall_short_word_skipped(self):
        """18.（M3a）单字 query "我" < 3 字符（trigram 数学下限）→ 400 bad_request"""
        r = self._recall("我")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_request")

    def test_recall_english_still_works(self):
        """19.（M3a）纯英文 query 行为不变：非中文词整词短语匹配照常命中。"""
        self._save("fix f-string nested quotes bug")
        r = self._recall("f-string")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertIn("f-string", items[0]["content"])

    def test_recall_multi_token_and_hit(self):
        """20.（P4切词修复 2026-10-06）多 token 词间 AND 保旧语义：query 两 token
        均可命中时，只返回同时含两 token 的条目（只含其一的被 AND 排除），AND 命中
        即返回、不触发 OR 兜底。样本说明：任务书原样本 query "Python 脚本" 的
        "脚本"仅 2 字符，会被切词行丢弃退化成单 token，测不到词间 AND，故改用
        4 字 "脚本执行"（2026-10-06 探针实锤，与 16 号用例样本裁定同惯例）。"""
        id_both = self._save("Python 脚本执行报错处理").json()["data"]["memory_id"]
        self._save("Python 环境配置完全无关词")
        r = self._recall("Python 脚本执行")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], id_both)

    def test_recall_multi_token_or_fallback(self):
        """21.（P4切词修复 2026-10-06）AND 全灭 → OR 兜底：query 两 token、content
        只含其一，第一发词间 AND 零命中，第二发词间 OR 兜底命中 >0 条。样本说明：
        任务书原样本 query "修复 不存在的词" 的"修复"仅 2 字符被切词行丢弃，退化成
        单 token，OR 式与 AND 式相同、兜底无从生效，故"修复"侧改用 4 字 token
        （2026-10-06 探针实锤）。"""
        self._save("修复引号嵌套问题")
        r = self._recall("引号嵌套 完全无关的词")
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertGreater(len(items), 0)
        self.assertEqual(items[0]["content"], "修复引号嵌套问题")


class TestMemoryForget(MemoryBridgeTestBase):

    def test_forget_by_id(self):
        """7. save → forget by id → recall 不再命中"""
        mem_id = self._save("待删除的独有内容 forgettingx").json()["data"]["memory_id"]
        r = self.client.post("/api/memory/forget", json={"memory_id": mem_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["deleted"], 1)
        self.assertEqual(self._recall("forgetingx").json()["data"]["items"], [])

    def test_forget_by_tags(self):
        """8. save 3 条带 tag → forget tags → 删 3 条"""
        for i in range(3):
            self._save("批量删除测试条目 %d batchdelx" % i, tags=["tempm1x"])
        r = self.client.post("/api/memory/forget", json={"tags": ["tempm1x"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["deleted"], 3)
        lst = self.client.get("/api/memory/list", params={"limit": 50}).json()["data"]
        self.assertEqual(lst["total"], 0)

    def test_forget_by_query(self):
        """12.（附加）forget by query：FTS 命中的双表同步删"""
        self._save("查询删除测试 forgetqword 独有词")
        self._save("另一条 forgetqword 记录")
        r = self.client.post("/api/memory/forget", json={"query": "forgetqword"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["deleted"], 2)
        self.assertEqual(self._recall("forgetqword").json()["data"]["items"], [])
        lst = self.client.get("/api/memory/list", params={"limit": 50}).json()["data"]
        self.assertEqual(lst["total"], 0)

    def test_forget_requires_criteria(self):
        """15.（附加）三选一都不给 → 400"""
        r = self.client.post("/api/memory/forget", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "bad_request")

    def test_recall_forget_unaffected(self):
        """22.（P4切词修复 2026-10-06）forget 端点行为不变：mode="forget" 在
        _fts_expr 内早退、不经过 recall_or 新模式，按 query 删除仍要求全部 token
        命中（逐词 AND）——只共享部分词的条目不被连带删，删除面零扩大。"""
        self._save("forgetqword zetaword 双词共存记录")
        self._save("zetaword 单独存在记录")
        r = self.client.post("/api/memory/forget", json={"query": "forgetqword zetaword"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["deleted"], 1)
        lst = self.client.get("/api/memory/list", params={"limit": 50}).json()["data"]
        self.assertEqual(lst["total"], 1)
        self.assertIn("zetaword", lst["items"][0]["content"])


class TestMemoryList(MemoryBridgeTestBase):

    def test_list_pagination(self):
        """10. save 5 条 → list(limit=2) → 返回 2 条，total 记全量"""
        for i in range(5):
            self._save("分页测试条目 %d pagetagx" % i)
        r = self.client.get("/api/memory/list", params={"limit": 2, "offset": 0})
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["total"], 5)
        # offset 翻页：第二页两条，且与第一页不重叠
        r2 = self.client.get("/api/memory/list", params={"limit": 2, "offset": 2})
        ids_page1 = {it["id"] for it in data["items"]}
        ids_page2 = {it["id"] for it in r2.json()["data"]["items"]}
        self.assertEqual(len(ids_page2), 2)
        self.assertFalse(ids_page1 & ids_page2)


class TestMemoryDedup(MemoryBridgeTestBase):
    """[P1精确判重 2026-10-06] insert_memory 写前查重：同 source + 归一化 content
    全等 → 幂等返回已有 id、不新增行。只拦逐字重复，不做模糊相似度。"""

    def test_insert_exact_duplicate_rejected(self):
        """23.（P1精确判重）同 content 同 source save 两次 → 第二次 200 返回已有
        memory_id（幂等，不报错），TOTAL 不变——不新增行。"""
        c = "精确判重测试内容 dedupwordx"
        id1 = self._save(c).json()["data"]["memory_id"]
        r2 = self._save(c)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["data"]["memory_id"], id1)
        lst = self.client.get("/api/memory/list", params={"limit": 50}).json()["data"]
        self.assertEqual(lst["total"], 1)

    def test_insert_similar_but_not_duplicate_kept(self):
        """24.（P1精确判重）两条仅差 2 字（复刻 9 号 time_decay_order 形态）→
        都保留——精确判重不做模糊相似度，近似文案不误杀。"""
        self._save("旧条目 similarwordx 部署手册")
        self._save("新条目 similarwordx 部署手册")
        r = self._recall("similarwordx", limit=10)
        self.assertEqual(r.status_code, 200)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual({it["content"] for it in items},
                         {"旧条目 similarwordx 部署手册", "新条目 similarwordx 部署手册"})

    def test_insert_same_content_different_source_kept(self):
        """25.（P1精确判重）同 content 不同 source → 都保留——source 在判重键里，
        跨 source 不互挡（manual 与 organ 线可各自留档同一文本）。"""
        c = "跨source判重测试 srcdupwordx"
        self._save(c)  # source 默认 manual
        self._save(c, source="laya_executor", kind="task_result")
        lst = self.client.get("/api/memory/list", params={"limit": 50}).json()["data"]
        self.assertEqual(lst["total"], 2)


def _import_task_routes_isolated():
    """task_routes 导入期即执行 _load_state() 扫真实 work/task_state（running 残档
    会被改判 failed 并落盘）——先把 config.SERVER_DIR 暂指测试工作区再导入，
    STATE_DIR/TEMPLATES_DIR 随之落在隔离目录，真实任务档案零接触。"""
    saved = config.SERVER_DIR
    config.SERVER_DIR = str(TEST_DIR / "server")
    try:
        from server.app import task_routes
    finally:
        config.SERVER_DIR = saved
    return task_routes


class TestReflectionOutputGuard(MemoryBridgeTestBase):
    """[反思防污染 2026-10-06] run_reflection 输出侧两道闸：语言漂移 / 空 basis 拒写。
    mock _llm_reflect 绕开真实 LLM，只验写入闸本身。"""

    def _seed_task_results(self, n):
        from server.app.memory_routes import insert_memory
        for i in range(n):
            mem_id, err = insert_memory(
                "修复：text_fix_flow·fixture%d\n诊断：AssertionError\n补丁：示例diff｜验证：ok" % i,
                kind="task_result", source="task_flow")
            self.assertFalse(err)

    def _run_with_mock_llm(self, data):
        from server.app import reflection

        # [v1 批2] 重置全局冷却闸——同进程两测背靠背时防第二次被 10s 冷却拦（预存顺序效应）
        reflection._reflect_last_global = 0.0

        async def fake_llm(items):
            return data, ""

        orig = reflection._llm_reflect
        reflection._llm_reflect = fake_llm
        try:
            return asyncio.run(reflection.run_reflection(20))
        finally:
            reflection._llm_reflect = orig

    def _insight_count(self):
        conn = sqlite3.connect(str(self.mem_db))
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM memory_bank WHERE kind='insight'").fetchone()[0]
        finally:
            conn.close()

    def test_reflection_rejects_english_insight(self):
        """26.（反思防污染）mock LLM 返纯英文 insight → 语言闸拒写，errors 带
        "语言漂移"，库零新增（复刻 v29 835207da 形态）。"""
        self._seed_task_results(2)
        result = self._run_with_mock_llm([
            {"insight": "For text_fix_flow tasks, patch verification results can "
                        "vary between success and failure",
             "basis": [1]},
        ])
        self.assertEqual(result["insights_created"], 0)
        self.assertTrue(any("语言漂移" in e for e in result["errors"]),
                        result["errors"])
        self.assertEqual(self._insight_count(), 0)

    def test_reflection_rejects_empty_basis(self):
        """27.（反思防污染）mock LLM 返无 basis 的中文 insight → 溯源闸拒写，
        errors 带"无溯源依据"，库零新增。"""
        self._seed_task_results(2)
        result = self._run_with_mock_llm([
            {"insight": "同类修复任务先读取文件初始状态再修改，可避免累积脏改动",
             "basis": []},
        ])
        self.assertEqual(result["insights_created"], 0)
        self.assertTrue(any("无溯源依据" in e for e in result["errors"]),
                        result["errors"])
        self.assertEqual(self._insight_count(), 0)


class TestGuardedRunMemoryGate(unittest.TestCase):
    """[反思防污染 2026-10-06] _guarded_run 写记忆闸：verify 步 failed 的带伤
    completed 不写记忆；对照 verify ok 照写一次，证明闸生效而非路径没走到。"""

    def test_guarded_run_skips_write_on_verify_failed(self):
        tr = _import_task_routes_isolated()
        from unittest.mock import patch

        report = TEST_DIR / "fix_report_gate_test.md"
        report.write_text("# 修复报告（闸测试）", encoding="utf-8")
        try:
            def make_rec(task_id):
                rec = {"task_id": task_id, "template": "text_fix_flow",
                       "status": "running", "t0": 0.0, "started": "", "steps": [],
                       "work_dir": "",
                       "step_order": ["analyze_text", "verify_in_sandbox",
                                      "generate_report"],
                       "report": "", "diff": "", "error": "",
                       "human_intervention": None, "tokens_spent": 0,
                       "conversation_id": "", "ended_at": None}
                tr._TASKS[task_id] = rec
                tr._ORDER.append(task_id)
                return rec

            def make_runner(verify_status):
                class _FakeRunner:
                    params = {"project_dir": str(TEST_DIR / "proj"),
                              "project_id": "iris_fixture"}

                    async def run(self):
                        return {"status": "completed", "duration_sec": 1.0,
                                "tokens_spent": 0, "human_intervention": None,
                                "steps": [
                                    {"id": "analyze_text", "type": "mcp_call",
                                     "status": "ok",
                                     "output": {"data": {"error_type": "AssertionError",
                                                         "file_path": "main.py"}}},
                                    {"id": "verify_in_sandbox",
                                     "type": "docker_sandbox",
                                     "status": verify_status, "output": {}},
                                    {"id": "generate_report", "type": "report",
                                     "status": "ok",
                                     "output": {"path": str(report)}},
                                ]}
                return _FakeRunner()

            rec_fail = make_rec("gatetestfail01")
            make_rec("gatetestok001")
            try:
                with patch.object(tr, "_persist", lambda r: None), \
                     patch.object(tr.organ_memory, "write_organ_memory") as wmock:
                    asyncio.run(tr._guarded_run("gatetestfail01",
                                                make_runner("failed")))
                    self.assertEqual(rec_fail["status"], "completed")
                    self.assertEqual(wmock.call_count, 0)
                    asyncio.run(tr._guarded_run("gatetestok001",
                                                make_runner("ok")))
                    self.assertEqual(wmock.call_count, 1)
            finally:
                for tid in ("gatetestfail01", "gatetestok001"):
                    tr._TASKS.pop(tid, None)
                    if tid in tr._ORDER:
                        tr._ORDER.remove(tid)
        finally:
            report.unlink(missing_ok=True)


class TestMemoryMatrixGuard(MemoryBridgeTestBase):
    """[v1 写入校验 2026-10-13] source × kind 矩阵守卫测

    结构钉死：矩阵不匹配 → insert_memory 返回 ("", "写入校验拒绝：...")
    这组测防"矩阵被静默改坏"——改一处必红。
    """

    def _try_write(self, source, kind, content):
        """直调 insert_memory，返回 (mem_id, err)"""
        from server.app.memory_routes import insert_memory
        return insert_memory(content, kind=kind, source=source)

    def test_manual_can_write_preference(self):
        """manual × preference → 放行"""
        mid, err = self._try_write("manual", "preference", "守卫测：manual 可写 preference")
        self.assertEqual(err, "", f"manual×preference 应放行，实际 err={err!r}")
        self.assertTrue(mid, "应返回非空 mem_id")

    def test_manual_can_write_fact(self):
        """manual × fact → 放行"""
        mid, err = self._try_write("manual", "fact", "守卫测：manual 可写 fact")
        self.assertEqual(err, "")
        self.assertTrue(mid)

    def test_manual_cannot_write_task_result(self):
        """manual × task_result → 拒绝（用户不该直写任务结果）"""
        mid, err = self._try_write("manual", "task_result", "守卫测：manual 不应写 task_result")
        self.assertEqual(mid, "", "应拒绝，mem_id 应为空")
        self.assertIn("写入校验拒绝", err)

    def test_zcode_stream_cannot_write_preference(self):
        """zcode_stream × preference → 拒绝（★ 核心：工具写不了偏好）"""
        mid, err = self._try_write("zcode_stream", "preference", "守卫测：工具不应写偏好")
        self.assertEqual(mid, "", "工具绝不能写偏好——窗户会因此说谎")
        self.assertIn("写入校验拒绝", err)

    def test_self_model_can_write_self(self):
        """self_model × self → 放行"""
        mid, err = self._try_write("self_model", "self", "守卫测：self_model 可写 self")
        self.assertEqual(err, "")
        self.assertTrue(mid)

    def test_unknown_source_rejected(self):
        """未知 source × 任意 kind → 拒绝（fail-closed）"""
        mid, err = self._try_write("__nonexistent_source__", "fact", "守卫测：未知 source 应拒")
        self.assertEqual(mid, "")
        self.assertIn("写入校验拒绝", err)


if __name__ == "__main__":
    unittest.main()
