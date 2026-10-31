# -*- coding: utf-8 -*-
"""self_driven.retry_handler 单元测试（2026-10-24 深海阶段二：失败重试闭环）。

运行：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests/test_self_driven_retry.py -v

隔离：self_driven.STATE_FILE 钉到 test_self_driven_retry_workspace 临时文件
（原始值存档 / tearDown 恢复）；LLM 全 mock（_llm_goal 不真调 Ollama）；
asyncio.run 包异步 handler（venv 无 pytest-asyncio 惯例同款）。
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TEST_DIR = PROJECT_ROOT / "test_self_driven_retry_workspace"
TEST_DIR.mkdir(exist_ok=True)
STATE_PATH = TEST_DIR / "self_driven_state.json"

from server.app import self_driven  # noqa: E402


def _write_state(queue: list) -> None:
    STATE_PATH.write_text(
        json.dumps({"queue": queue, "daily": {"date": "2026-10-24", "count": 0}},
                   ensure_ascii=False), encoding="utf-8")


def _read_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _failed_card(engine_task_id: str, retry_count: int = 0,
                 task_id: str = "parent-card") -> dict:
    return {"task_id": task_id, "goal": "验证失败重试闭环", "target_engine": "zcode",
            "engine_task_id": engine_task_id, "status": "failed",
            "created_at": "2026-10-24T01:00:00", "retry_count": retry_count,
            "result_note": "失败：退出码 1"}


class RetryHandlerTest(unittest.TestCase):
    def setUp(self):
        self._orig = self_driven.STATE_FILE
        self_driven.STATE_FILE = STATE_PATH
        if STATE_PATH.exists():
            STATE_PATH.unlink()

    def tearDown(self):
        self_driven.STATE_FILE = self._orig

    def test_no_card_is_silent(self):
        """普通聊天任务的失败（无匹配卡）→ None，状态文件零改动。"""
        _write_state([_failed_card("etid-1")])
        res = asyncio.run(self_driven.retry_handler("unknown-etid", "boom"))
        self.assertIsNone(res)
        self.assertEqual(len(_read_state()["queue"]), 1)

    def test_done_card_skipped(self):
        _write_state([_failed_card("etid-1")])
        st = _read_state()
        st["queue"][0]["status"] = "done"
        _write_state(st["queue"])
        res = asyncio.run(self_driven.retry_handler("etid-1", "boom"))
        self.assertIsNone(res)

    def test_retry_card_generated_with_lineage(self):
        _write_state([_failed_card("etid-1", retry_count=0)])
        with mock.patch.object(self_driven, "_llm_goal",
                               mock.AsyncMock(return_value=(
                                   '{"goal": "先确认目录存在再写入", '
                                   '"claim": "目标目录已被写入", '
                                   '"verify_cmd": "dir D:\\\\MoRay\\\\utf_test.txt", '
                                   '"target_engine": "zcode"}', ""))):
            res = asyncio.run(self_driven.retry_handler("etid-1", "退出码 1：目录不存在"))
        self.assertIsNotNone(res)
        st = _read_state()
        self.assertEqual(len(st["queue"]), 2)
        child = st["queue"][-1]
        self.assertEqual(child["status"], "pending_approval")
        self.assertEqual(child["retry_of"], "parent-card")
        self.assertEqual(child["retry_count"], 1)
        self.assertIn("目录不存在", child["retry_reason"])
        self.assertEqual(child["goal"], "先确认目录存在再写入")
        # [v5.3] 目标含"写入"意图 ⇒ 必须人批，不许被只读白名单顺走
        self.assertFalse(child["readonly_class"])
        # 父卡保持 failed（close_loop 语义不被 retry 破坏）
        self.assertEqual(st["queue"][0]["status"], "failed")

    def test_retry_readonly_card_auto_dispatched(self):
        """[v5.3] 只读重试卡：过白名单 ⇒ 自动派发并打 auto 标（不占人批队列）。"""
        _write_state([_failed_card("etid-1", retry_count=0)])
        calls = []

        def fake_impl(tid, auto=False):
            calls.append((tid, auto))
            return {"ok": True, "task_id": tid, "engine_task_id": "eng-auto",
                    "status": "dispatched"}

        with mock.patch.object(self_driven, "_llm_goal",
                               mock.AsyncMock(return_value=(
                                   '{"goal": "核对 utf_test.txt 的字节数", '
                                   '"claim": "文件为 12 字节", '
                                   '"verify_cmd": "dir D:\\\\MoRay\\\\utf_test.txt", '
                                   '"target_engine": "zcode"}', ""))), \
             mock.patch.object(self_driven, "_dispatch_card_impl", fake_impl), \
             mock.patch.object(self_driven, "_auto_approved_today", lambda *a: 0):
            res = asyncio.run(self_driven.retry_handler("etid-1", "读不到文件"))
        self.assertIsNotNone(res)
        self.assertTrue(res["readonly_class"])
        self.assertEqual(res["status"], "dispatched")
        self.assertTrue(res["auto_approved"])
        self.assertEqual(calls, [(res["task_id"], True)])

    def test_retry_count_two_abandons(self):
        """retry_count 已达上限 2 → 不再生成新卡，父卡置 abandoned。"""
        _write_state([_failed_card("etid-1", retry_count=2)])
        res = asyncio.run(self_driven.retry_handler("etid-1", "再次失败"))
        self.assertIsNotNone(res)
        st = _read_state()
        self.assertEqual(len(st["queue"]), 1)  # 没有新卡
        self.assertEqual(st["queue"][0]["status"], "abandoned")
        self.assertIn("再次失败", st["queue"][0]["abandoned_reason"])

    def test_llm_empty_output_no_card(self):
        """LLM 产出为空 → fail-open 不发卡，父卡留在原状态。"""
        _write_state([_failed_card("etid-1", retry_count=0)])
        with mock.patch.object(self_driven, "_llm_goal",
                               mock.AsyncMock(return_value=("", "推理服务应答为空"))):
            res = asyncio.run(self_driven.retry_handler("etid-1", "boom"))
        self.assertIsNone(res)
        st = _read_state()
        self.assertEqual(len(st["queue"]), 1)
        self.assertEqual(st["queue"][0]["status"], "failed")

    def test_llm_plain_text_refused_since_v52(self):
        """[v5.2 行为变更] 模型退化输出没有 verify_cmd ⇒ 硬缺陷 empty ⇒ 拒发重试卡。

        v1/v2 时代这里会发一张"只有 goal 没有判据"的卡：审批一次也判不了真假，
        纯烧人的注意力。v5.2 改成宁可不重试也不发这种卡——本测试锁住这个口径，
        防止哪天为了"看起来重试了"把闸门又放开。
        """
        _write_state([_failed_card("etid-1", retry_count=0)])
        with mock.patch.object(self_driven, "_llm_goal",
                               mock.AsyncMock(return_value=("先检查磁盘空间再重试", ""))):
            res = asyncio.run(self_driven.retry_handler("etid-1", "磁盘满"))
        self.assertIsNone(res)
        st = _read_state()
        self.assertEqual(len(st["queue"]), 1)          # 没有新卡
        self.assertEqual(st["queue"][0]["status"], "failed")  # 父卡不动


if __name__ == "__main__":
    unittest.main()
