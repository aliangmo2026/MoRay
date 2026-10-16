# -*- coding: utf-8 -*-
"""[桥三事实卡 2026-10-15] render_self_card 纯函数单测（任务书 5.2：至少 3 条）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.app.self_card import render_self_card  # noqa: E402

STATE = {
    "memory_total": 90,
    "memory_by_kind": {"insight": 12, "self": 10},
    "conversations": 77,
    "messages": 571,
    "self_observation": 37,
    "recent_self": "自我模型样例行" * 30,
    "uptime_hours": 23.5,
}


class TestRenderSelfCard(unittest.TestCase):

    def test_render_is_pure(self):
        """同一 state 连调 3 次 → 输出逐字相同（方案 3.1 确定性红线）。"""
        a = render_self_card(STATE)
        b = render_self_card(STATE)
        c = render_self_card(STATE)
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        # 结构拷贝输入也必须逐字一致
        self.assertEqual(a, render_self_card(dict(STATE)))

    def test_no_hardcoded_lines(self):
        """state 数字全改 → 对应行全变（每行必须是 state 的函数，禁硬编码）。"""
        base = render_self_card(STATE)
        mutated = {
            **STATE,
            "memory_total": 91,
            "conversations": 78,
            "messages": 572,
            "self_observation": 38,
            "recent_self": "另一行自我模型",
            "uptime_hours": 1.0,
        }
        out = render_self_card(mutated)
        self.assertNotEqual(base, out)
        self.assertIn("累计记忆：91 条", out)
        self.assertIn("对话数：78 条", out)
        self.assertIn("消息数：572 条", out)
        self.assertIn("自省观察：38 条", out)
        self.assertIn("最近自我模型：另一行自我模型", out)
        self.assertIn("本次运行：1.0 小时", out)

    def test_forbidden_phrases(self):
        """事实卡输出永不含『仅限本会话』『被动响应式』（回执判据的单测级锁）。"""
        for phrase in ("仅限本会话", "被动响应式"):
            self.assertNotIn(phrase, render_self_card(STATE))
            self.assertNotIn(phrase, render_self_card({}))

    def test_empty_state_degrades_to_zeros(self):
        """单源全失败（空 state）→ 卡仍成形、零值兜底，可变行全部省略。"""
        out = render_self_card({})
        self.assertTrue(out.startswith("你是 MoRay。"))
        self.assertIn("累计记忆：0 条", out)
        self.assertIn("对话数：0 条", out)
        self.assertIn("消息数：0 条", out)
        self.assertNotIn("按类型", out)
        self.assertNotIn("最近自我模型", out)
        self.assertNotIn("本次运行", out)
        self.assertIn("禁止外推", out)


if __name__ == "__main__":
    unittest.main()
