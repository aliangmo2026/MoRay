# -*- coding: utf-8 -*-
"""L6.5 阶段4 单元测试：修复后回归测试建议（确定性银行 / pytest 格式 / 根因匹配 / 闸）。

运行：server\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/l65/test_l65_suggest_tests.py -q
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="l65_st_ut_"))

from knowledge import issue_base as ib   # noqa: E402


class L65StBase(unittest.TestCase):
    def setUp(self):
        os.environ["MORAY_EXPERIENCE_DB"] = str(TMP / "exp.jsonl")
        os.environ["MORAY_EXPERIENCE_STATS"] = str(TMP / "experience_stats.json")
        os.environ["MORAY_STRATEGY_STATS"] = str(TMP / "strategy_stats.json")
        os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(TMP / "profiles")
        os.environ["MORAY_PATTERN_CANDIDATES"] = str(TMP / "pattern_candidates.json")
        for k in ("MORAY_EXPERIENCE_EVOLUTION", "MORAY_CAUSE_RANK", "MORAY_EXPERIENCE_GLOBAL",
                  "MORAY_STRATEGY_OPTIMIZE", "MORAY_FIX_ADAPTIVE", "MORAY_PATTERN_MINER",
                  "MORAY_UNIFIED_ISSUE", "MORAY_REVIEW_KNOWLEDGE",
                  "MORAY_FIX_SUGGEST_TEST", "MORAY_TEST_KNOWLEDGE"):
            os.environ.pop(k, None)
        for f in TMP.glob("*"):
            if f.is_file():
                f.unlink()
        self.ibx = ib.IssueBase()

    def _seed(self, pid="P"):
        for _ in range(2):
            self.ibx.record_issue({"project_id": pid, "error_type": "ZeroDivisionError",
                                   "root_cause_category": "空集合均值",
                                   "solution": "除法前判空", "success": True},
                                  source=ib.SOURCE_FIX)


class SuggestTestsTest(L65StBase):
    def test_python_pytest_format_by_root_cause(self):
        """任务书 6.2-3：修复完成 → 生成 pytest 格式建议，根因匹配。"""
        self._seed()
        r = self.ibx.suggest_tests("P", error_type="ZeroDivisionError",
                                   cause_text="空集合均值", snippet="division by zero")
        self.assertEqual(r["language"], "python")
        self.assertGreaterEqual(r["count"], 2)
        self.assertLessEqual(r["count"], 3)
        self.assertIn("import pytest", r["suggestions"])
        self.assertIn("ZeroDivisionError", r["suggestions"])
        self.assertIn("历史参考方案：除法前判空", r["suggestions"], "必须引用历史同类方案")

    def test_dependency_cause_suggests_import_test(self):
        r = self.ibx.suggest_tests("P", error_type="ModuleNotFoundError",
                                   cause_text="虚拟环境未安装依赖",
                                   snippet="No module named 'requests'")
        self.assertIn("test_dependency_imports", r["suggestions"])
        self.assertIn('"requests"', r["suggestions"])

    def test_syntax_cause_suggests_compile_test(self):
        r = self.ibx.suggest_tests("P", error_type="SyntaxError", cause_text="语法错误")
        self.assertIn("py_compile", r["suggestions"])

    def test_non_python_checklist(self):
        r = self.ibx.suggest_tests("P", error_type="ERR_MODULE",
                                   cause_text="依赖模块未安装", snippet="npm ERR!")
        self.assertEqual(r["language"], "javascript")
        self.assertGreaterEqual(r["count"], 2)
        self.assertNotIn("import pytest", r["suggestions"])

    def test_generic_fallback(self):
        r = self.ibx.suggest_tests("P", error_type="MNE", cause_text="业务自定义问题")
        self.assertGreaterEqual(r["count"], 2)

    def test_gate_off_no_generation(self):
        os.environ["MORAY_FIX_SUGGEST_TEST"] = "0"
        try:
            r = self.ibx.suggest_tests("P", error_type="ZeroDivisionError",
                                       cause_text="空集合均值")
            self.assertEqual(r["count"], 0)
            self.assertEqual(r["suggestions"], "")
            self.assertIn("关闭", r["note"])
        finally:
            os.environ.pop("MORAY_FIX_SUGGEST_TEST", None)

    def test_no_execution_by_design(self):
        """只建议不执行：输出是文本与文件路径，绝不含执行结果字段。"""
        r = self.ibx.suggest_tests("P", error_type="ZeroDivisionError", cause_text="空集合均值")
        for forbidden in ("exit_code", "stdout", "stderr", "passed"):
            self.assertNotIn(forbidden, r)


if __name__ == "__main__":
    unittest.main()
