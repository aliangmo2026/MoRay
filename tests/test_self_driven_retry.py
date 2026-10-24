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
        # 父卡保持 failed（close_loop 语义不被 retry 破坏）
        self.assertEqual(st["queue"][0]["status"], "failed")

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

    def test_llm_plain_text_degrades_to_goal(self):
        """模型不按 JSON 说话 → _parse_goal_payload 退化整段文本当目标（v2 同款）。"""
        _write_state([_failed_card("etid-1", retry_count=0)])
        with mock.patch.object(self_driven, "_llm_goal",
                               mock.AsyncMock(return_value=("先检查磁盘空间再重试", ""))):
            res = asyncio.run(self_driven.retry_handler("etid-1", "磁盘满"))
        self.assertIsNotNone(res)
        child = _read_state()["queue"][-1]
        self.assertIn("磁盘空间", child["goal"])
        self.assertEqual(child["target_engine"], "zcode")  # 白名单回落默认


if __name__ == "__main__":
    unittest.main()
