"""intent_router 单元测试（任务一交付物，2026-09-30）。

运行：
    python -m pytest tests/test_intent_router.py -v

全部离线可跑：
  * 规则路径（test_01~06）零网络；
  * 分类器路径（test_07~09、11、12）全部 mock _classify_raw / 启用开关，零网络；
  * test_10 监控 socket 连接，只放行 127.0.0.1/::1/localhost，断言无非本机出站。
"""
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import intent_router
from intent_router import IntentInput, IntentResult, decide  # noqa: F401

# ≥30 字、无命令动词/路径/代码块 → 绕过 Level 2，落到 Level 3 分类器
_LEVEL3_TEXT = "帮我进一步展开这个想法，" * 3


class ExplicitOverrideTests(unittest.TestCase):
    def test_01_explicit_agent_short_circuits(self):
        result = decide(IntentInput(text="随便什么内容", explicit_mode="agent"))
        self.assertIsInstance(result, IntentResult)
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "explicit")
        self.assertEqual(result.confidence, 1.0)

    def test_02_explicit_chat_overrides_agent_verb(self):
        # "创建"是命令动词，规则层会判 agent；显式 chat 必须短路优先
        result = decide(IntentInput(text="创建文件", explicit_mode="chat"))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "explicit")


class RuleLayerTests(unittest.TestCase):
    def test_03_short_greeting_is_chat_by_rule(self):
        result = decide(IntentInput(text="你好"))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "rule")

    def test_04_code_fence_is_agent(self):
        result = decide(IntentInput(text="```python\nprint(1)\n```"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")

    def test_05_absolute_path_is_agent(self):
        result = decide(IntentInput(text="创建 D:\\MoRay\\workspace\\a.py"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")

    def test_06_refactor_verb_is_agent(self):
        result = decide(IntentInput(text="帮我重构这个函数"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")


class ClassifierTests(unittest.TestCase):
    def _enable(self):
        return mock.patch.object(intent_router, "INTENT_CLASSIFIER_ENABLED", True)

    def test_07_classifier_timeout_falls_back_to_chat(self):
        with self._enable(), mock.patch.object(
                intent_router, "_classify_raw", side_effect=socket.timeout("timed out")):
            result = decide(IntentInput(text=_LEVEL3_TEXT))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "fallback")
        self.assertEqual(result.confidence, 0.0)

    def test_08_illegal_json_falls_back_to_chat(self):
        with self._enable(), mock.patch.object(
                intent_router, "_classify_raw",
                return_value="好的，我的看法是……（这段话不是JSON）"):
            result = decide(IntentInput(text=_LEVEL3_TEXT))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "fallback")

    def test_09_low_confidence_falls_back_to_chat(self):
        raw = '{"mode":"agent","intent_type":"code","confidence":0.5}'
        with self._enable(), mock.patch.object(intent_router, "_classify_raw", return_value=raw):
            result = decide(IntentInput(text=_LEVEL3_TEXT))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "fallback")
        self.assertEqual(result.confidence, 0.0)

    def test_11_high_confidence_agent_is_adopted(self):
        raw = '{"mode":"agent","intent_type":"code","confidence":0.9}'
        with self._enable(), mock.patch.object(intent_router, "_classify_raw", return_value=raw):
            result = decide(IntentInput(text=_LEVEL3_TEXT))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "classifier")
        self.assertEqual(result.confidence, 0.9)

    def test_12_disabled_classifier_falls_back_to_chat(self):
        with mock.patch.object(intent_router, "INTENT_CLASSIFIER_ENABLED", False):
            result = decide(IntentInput(text=_LEVEL3_TEXT))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "fallback")


class OutboundGuardTests(unittest.TestCase):
    def test_10_no_non_localhost_outbound(self):
        outside = []
        original_connect = socket.socket.connect

        def guarded(sock, address):
            if isinstance(address, tuple) and address and isinstance(address[0], str):
                if address[0] not in ("127.0.0.1", "::1", "localhost"):
                    outside.append(address)
            return original_connect(sock, address)

        with mock.patch.object(socket.socket, "connect", guarded):
            for text in ("你好", "1+1", "创建 D:\\tmp\\a.py", _LEVEL3_TEXT):
                decide(IntentInput(text=text))
        self.assertEqual(outside, [])


class ConfigDefaultsTests(unittest.TestCase):
    """任务A v2（2026-10-01）：单槽位互踢修复后的默认配置回归（读模块常量，不 mock）。"""

    def test_13_default_classifier_model_is_9b(self):
        # v4：L3 默认模型改回 qwen3.5:9b（与主对话共用零互踢、泛化强）；
        # v3 实测 final 二分类仅 12/20（SFT 零二分类标注，分布外任务）
        self.assertEqual(intent_router.INTENT_CLASSIFIER_MODEL, "qwen3.5:9b")

    def test_14_default_classifier_timeout_3000ms(self):
        # 默认超时必须给 L3 模型冷加载留足余量（v4 起为 9b 6.6GB，比 final 更慢）
        self.assertEqual(intent_router.INTENT_CLASSIFIER_TIMEOUT_MS, 3000)


class RuleV4VerbNounTests(unittest.TestCase):
    """任务A v4：L2 动词+名词组合规则——覆盖 v3 侦察端点层判错的 agent 样本。"""

    def test_15_query_policy_search_verb_is_agent(self):
        # U-6-3（2026-10-01）：搜索动词强信号规则生效——"查一下"单命中即 agent（0.85），
        # 无需名词配合。v4 时本样本依赖"政策有意不进名词表"判 chat；U-6-3 认定
        # "查一下+领域词"是明确的搜索任务，必须走 Laya 搜索链路，故翻转为 agent
        result = decide(IntentInput(text="查一下最新的新能源补贴政策"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(result.intent_type, "search")

    def test_16_fix_bug_is_agent(self):
        # 修 + 代码/bug → 动词+名词组合升 agent（v3 端点层被短文本规则吞掉）
        result = decide(IntentInput(text="把这段代码里的 bug 修一下"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")
        self.assertEqual(result.confidence, 0.9)

    def test_17_write_script_is_agent(self):
        # 写 + 脚本
        result = decide(IntentInput(text="帮我写个 Python 脚本批量重命名文件"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")

    def test_18_analyze_log_is_agent(self):
        # 分析 + 日志/错误
        result = decide(IntentInput(text="分析一下这个日志里的错误原因"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")

    def test_19_design_schema_is_agent(self):
        # 设计 + 数据库
        result = decide(IntentInput(text="帮我设计一个用户登录的数据库表结构"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")

    def test_20_look_file_is_agent(self):
        # 看看 + 文件
        result = decide(IntentInput(text="看看我桌面上那个文件里写了什么"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")


class V5ThinkAndNounsTests(unittest.TestCase):
    """任务A v5：payload think:False 修复 + 名词表移除"需求"的回归锁定。"""

    def test_21_nouns_exclude_requirement(self):
        # "需求"太抽象非可操作对象；移除后"帮我看看这个需求怎么实现"回归 chat
        self.assertNotIn("需求", intent_router._AGENT_OBJECT_NOUNS)

    def test_22_classifier_payload_has_think_false(self):
        # thinking 模型吃光 num_predict=64 → content 恒空 → 恒降级（v4 补充验证实锤）；
        # payload 顶层必须显式 "think": False（源码字面检查，任务书认可的方式）
        import inspect
        src = inspect.getsource(intent_router._classify_raw)
        self.assertIn('"think": False', src)


class U63SearchVerbTests(unittest.TestCase):
    """U-6-3（2026-10-01）：L2 搜索动词强信号——修"查一下 2026 新能源补贴政策"误判 chat。

    用户实况：该句因 U-4 移除"政策"不命中动名组合、<30 字被短文本规则短路成 chat，
    本地聊天若选中判定专用模型 moray-laya-final，用户直接收到裸工具调用 JSON。
    新规则：文本含 _SEARCH_VERBS 任一 → agent 0.85，intent_type=search（前端据此走
    Laya Agent 浮层搜索链路，而非 ZCode 代码沙箱）。
    """

    def test_23_query_latest_policy_is_agent(self):
        # 用户实况原句：搜索动词单命中 → agent 0.85，intent_type=search
        result = decide(IntentInput(text="查一下 2026 年最新的新能源补贴政策"))
        self.assertEqual(result.mode, "agent")
        self.assertEqual(result.decided_by, "rule")
        self.assertEqual(result.confidence, 0.85)
        self.assertEqual(result.intent_type, "search")

    def test_24_search_verb_variants_are_agent(self):
        # 覆盖"帮我查/调研/检索"等变体；样本均不命中名词表，锁定"无需名词配合"语义
        for text in ("帮我查一下最近的 AI 新闻",
                     "调研一下开源大模型的现状",
                     "检索一下量子计算的最新进展"):
            result = decide(IntentInput(text=text))
            self.assertEqual(result.mode, "agent", text)
            self.assertEqual(result.decided_by, "rule", text)
            self.assertEqual(result.confidence, 0.85, text)

    def test_25_short_chat_without_search_verb_stays_chat(self):
        # 回归：不含搜索动词的短闲聊不受新规则影响，仍走 L2 短文本 chat
        result = decide(IntentInput(text="这个想法挺好的"))
        self.assertEqual(result.mode, "chat")
        self.assertEqual(result.decided_by, "rule")


if __name__ == "__main__":
    unittest.main()
