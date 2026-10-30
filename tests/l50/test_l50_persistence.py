# -*- coding: utf-8 -*-
"""L5.0 阶段4 单元测试：任务状态持久化（work/task_state/{task_id}.json）。

覆盖任务书 6.1：写入/读取/24h 清理 + unfinished 端点 + conversation_id 归属 +
重启回填（running 残档诚实改判 failed）。
STATE_DIR 全程指到临时目录，绝不碰生产 work/task_state。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]          # D:\MoRay
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app import task_routes
from tests.l4_min.test_l4_tasks import FakeRunner, _Outcome  # noqa: F401  复用 L4 替身


TMP = ROOT / "work" / "l50_test"


class L50StateDirCase(unittest.TestCase):
    """公共夹具：隔离 STATE_DIR / _TASKS / 工作目录。"""

    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self._saved_dir = task_routes.STATE_DIR
        task_routes.STATE_DIR = Path(tempfile.mkdtemp(prefix="l50_state_"))
        self._saved = dict(task_routes._TASKS)
        task_routes._TASKS.clear()
        task_routes._ORDER.clear()

    def tearDown(self):
        shutil.rmtree(task_routes.STATE_DIR, ignore_errors=True)
        task_routes.STATE_DIR = self._saved_dir
        task_routes._TASKS.clear()
        task_routes._TASKS.update(self._saved)
        task_routes._ORDER.clear()


class WriteReadCleanupTest(L50StateDirCase):
    def test_persist_write_and_read(self):
        rec = {"task_id": "abc123", "template": "text_fix_flow", "status": "running",
               "t0": time.time(), "started": "now", "steps": [], "work_dir": "w",
               "step_order": ["a"], "report": "", "diff": "", "error": "",
               "human_intervention": None, "tokens_spent": 0,
               "conversation_id": "conv-9", "ended_at": None}
        task_routes._persist(rec)
        f = task_routes.STATE_DIR / "abc123.json"
        self.assertTrue(f.is_file())
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["conversation_id"], "conv-9")
        # 原子写：不留 .tmp 残file
        self.assertFalse((task_routes.STATE_DIR / "abc123.json.tmp").exists())

    def test_task_id_sanitized_in_filename(self):
        rec = {"task_id": "../evil", "template": "t", "status": "completed",
               "t0": 0, "started": "-", "steps": [], "work_dir": "w", "step_order": [],
               "conversation_id": "", "ended_at": 0}
        task_routes._persist(rec)
        names = [p.name for p in task_routes.STATE_DIR.glob("*.json")]
        self.assertEqual(names, ["evil.json"])            # 穿越段被清洗成安全名
        self.assertEqual(list(task_routes.STATE_DIR.parent.glob("evil.json")), [])

    def test_cleanup_removes_old_terminal_keeps_running_and_fresh(self):
        old = time.time() - 25 * 3600
        rec_t = {"task_id": "done_old", "template": "t", "status": "completed",
                 "t0": old, "started": "-", "steps": [], "work_dir": "w", "step_order": [],
                 "conversation_id": "", "ended_at": old}
        rec_r = dict(rec_t, task_id="run_old", status="running")
        rec_f = dict(rec_t, task_id="done_new", status="failed", ended_at=time.time())
        for r in (rec_t, rec_r, rec_f):
            task_routes._persist(r)
        for tid in ("done_old", "run_old"):
            p = task_routes.STATE_DIR / (tid + ".json")
            os.utime(p, (old, old))                       # 把 mtime 拨回 25h 前
        task_routes._cleanup_state()
        self.assertFalse((task_routes.STATE_DIR / "done_old.json").exists())  # 终态+超24h → 删
        self.assertTrue((task_routes.STATE_DIR / "run_old.json").exists())    # running 永不删
        self.assertTrue((task_routes.STATE_DIR / "done_new.json").exists())   # 新鲜终态不删

    def test_load_state_marks_running_as_failed(self):
        stale = {"task_id": "zombie", "template": "error_fix_flow", "status": "running",
                 "t0": time.time() - 600, "started": "-", "steps": [], "work_dir": "w",
                 "step_order": [], "conversation_id": "", "ended_at": None}
        done = {"task_id": "ok", "template": "test_run_flow", "status": "completed",
                "t0": time.time() - 60, "started": "-", "steps": [], "work_dir": "w",
                "step_order": [], "conversation_id": "c1", "ended_at": time.time(),
                "report": "r", "diff": "", "error": "", "tokens_spent": 5,
                "human_intervention": None}
        (task_routes.STATE_DIR / "zombie.json").write_text(json.dumps(stale), encoding="utf-8")
        (task_routes.STATE_DIR / "ok.json").write_text(json.dumps(done), encoding="utf-8")
        n = task_routes._load_state()
        self.assertEqual(n, 2)
        z = task_routes._TASKS["zombie"]
        self.assertEqual(z["status"], "failed")            # 诚实改判，不假装续跑
        self.assertIn("中断", z["human_intervention"])
        self.assertEqual(task_routes._TASKS["ok"]["status"], "completed")  # 终态原样回填
        # 改判结果回写磁盘（下次重启不再看到假 running）
        zz = json.loads((task_routes.STATE_DIR / "zombie.json").read_text(encoding="utf-8"))
        self.assertEqual(zz["status"], "failed")


class L50EndpointTest(L50StateDirCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(task_routes.router)
        self.client_ctx = TestClient(app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        super().tearDown()

    def _submit(self, runner_box):
        def factory(*a, **kw):
            fr = FakeRunner(*a, **kw)
            runner_box.append(fr)
            return fr
        with mock.patch.object(task_routes, "TaskTemplateRunner", factory):
            r = self.client.post("/api/tasks/run-error-fix",
                                 json={"error_text": "boom", "project_dir": str(TMP),
                                       "conversation_id": "conv-42"})
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()["data"]["task_id"]

    def test_running_persisted_and_unfinished_lists_conv(self):
        box = []
        tid = self._submit(box)
        # 受理即建档（不等终态）
        f = task_routes.STATE_DIR / (tid + ".json")
        deadline = time.time() + 8
        while time.time() < deadline and not f.exists():
            time.sleep(0.05)
        self.assertTrue(f.exists(), "提交后状态文件应立刻存在（受理即写）")
        d = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(d["conversation_id"], "conv-42")
        u = self.client.get("/api/tasks/unfinished").json()
        self.assertTrue(u["ok"])
        ids = [t["task_id"] for t in u["data"]["tasks"]]
        self.assertIn(tid, ids)
        item = [t for t in u["data"]["tasks"] if t["task_id"] == tid][0]
        self.assertEqual(item["conversation_id"], "conv-42")
        # 视图里也有 conversation_id（刷新恢复靠它认领）
        v = self.client.get("/api/tasks/" + tid).json()["data"]
        self.assertEqual(v["conversation_id"], "conv-42")
        box[0].release()
        # 终态：文件更新 + unfinished 里消失
        while time.time() < deadline:
            st = self.client.get("/api/tasks/" + tid).json()["data"]["status"]
            if st != "running":
                break
            time.sleep(0.05)
        self.assertEqual(st, "completed")
        d2 = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "completed")
        self.assertIsNotNone(d2.get("ended_at"))
        ids2 = [t["task_id"] for t in self.client.get("/api/tasks/unfinished").json()["data"]["tasks"]]
        self.assertNotIn(tid, ids2)
        # duration_sec 冻结在 ended_at（不是墙上时钟差）
        v2 = self.client.get("/api/tasks/" + tid).json()["data"]
        self.assertLessEqual(v2["duration_sec"], 10)

    def test_unfinished_route_not_shadowed_by_task_id(self):
        r = self.client.get("/api/tasks/unfinished")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tasks", r.json()["data"])          # 没被 /{task_id} 通配吞成 404

    def test_step_hook_persists_steps(self):
        box = []
        tid = self._submit(box)
        fr = box[0]
        fr.release()
        deadline = time.time() + 8
        while time.time() < deadline:
            d = self.client.get("/api/tasks/" + tid).json()["data"]
            if d["status"] != "running":
                break
            time.sleep(0.05)
        snap = json.loads((task_routes.STATE_DIR / (tid + ".json")).read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(snap["steps"]), 1)    # on_step 落盘过步骤进度

    def test_run_review_flow_validation(self):
        r2 = self.client.post("/api/tasks/run-review-flow",
                              json={"project_dir": str(TMP / "nope_xyz")})
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["code"], "path_not_found")
        r3 = self.client.post("/api/tasks/run-review-flow",
                              content=b"{bad", headers={"Content-Type": "application/json"})
        self.assertEqual(r3.status_code, 400)
        self.assertEqual(r3.json()["code"], "bad_json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
