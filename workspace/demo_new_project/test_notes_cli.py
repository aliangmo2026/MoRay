# -*- coding: utf-8 -*-
"""notes CLI 测试（全部数据只落在 demo_new_project/_test_data 内，测完自清理）。"""
import io
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_test_data")
sys.path.insert(0, HERE)

from notes_store import NoteError, NotesStore  # noqa: E402


class NotesStoreTests(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(DATA, ignore_errors=True)
        self.store = NotesStore(DATA)

    def tearDown(self):
        shutil.rmtree(DATA, ignore_errors=True)

    def test_01_create_and_read(self):
        name = self.store.create("会议纪要", "决定：下周发布。")
        path = os.path.join(DATA, name + ".md")
        self.assertTrue(os.path.isfile(path))
        text = io.open(path, encoding="utf-8").read()
        self.assertIn("# 会议纪要", text)
        self.assertIn("决定：下周发布。", text)
        self.assertEqual(self.store.read(name), text)

    def test_02_title_collision_gets_suffix(self):
        a = self.store.create("想法")
        b = self.store.create("想法")
        c = self.store.create("想法")
        self.assertEqual((a, b, c), ("想法", "想法-2", "想法-3"))
        self.assertEqual(len(self.store.list_notes()), 3)

    def test_03_list_sorted_newest_first(self):
        self.store.create("旧的")
        import time as t
        t.sleep(0.05)
        self.store.create("新的")
        names = [x["name"] for x in self.store.list_notes()]
        self.assertEqual(names, ["新的", "旧的"])

    def test_04_empty_title_rejected(self):
        with self.assertRaises(NoteError):
            self.store.create("   ")

    def test_05_read_missing_raises(self):
        with self.assertRaises(NoteError):
            self.store.read("不存在的笔记")

    def test_06_traversal_blocked(self):
        for bad in ("..\\evil", "../evil", "a/b", "a\\b", "。.."):
            with self.assertRaises(NoteError):
                self.store.read(bad)
        # 越界文件绝不能被创建出来
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(DATA), "evil.md")))

    def test_07_slug_for_weird_titles(self):
        name = self.store.create("Hello / World!! 你好")
        self.assertEqual(name, "Hello-World-你好")
        self.assertTrue(os.path.isfile(os.path.join(DATA, name + ".md")))


class CliTests(unittest.TestCase):
    """CLI 子进程级测试：cwd 锁定在项目目录，-d 指向项目内 _test_data。"""

    def setUp(self):
        shutil.rmtree(DATA, ignore_errors=True)
        os.makedirs(DATA, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(DATA, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "notes_cli.py")] + list(args) + ["-d", DATA],
            capture_output=True, text=True, encoding="utf-8", cwd=HERE, timeout=30)

    def test_11_create_then_list_then_read(self):
        r1 = self._run("create", "购物清单", "--body", "牛奶、鸡蛋")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("created: 购物清单", r1.stdout)

        r2 = self._run("list")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("购物清单", r2.stdout)
        self.assertIn("title", r2.stdout)  # 表头

        r3 = self._run("read", "购物清单")
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("# 购物清单", r3.stdout)
        self.assertIn("牛奶、鸡蛋", r3.stdout)

    def test_12_read_missing_exit_code_1(self):
        r = self._run("read", "没有这篇")
        self.assertEqual(r.returncode, 1)
        self.assertIn("笔记不存在", r.stderr)

    def test_13_list_empty_library(self):
        r = self._run("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("空笔记库", r.stdout)

    def test_14_read_by_unique_title_fallback(self):
        self._run("create", "唯一标题甲", "--body", "内容甲")
        r = self._run("read", "唯一标题甲")   # 传标题（带空格）也能读
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("内容甲", r.stdout)

    def test_15_read_by_ambiguous_title_rejected(self):
        # 标题带空格 = 不是合法 slug → 必走"标题回退"路径；两篇同名 → 多义拒绝
        self._run("create", "同名 题", "--body", "A")
        self._run("create", "同名 题", "--body", "B")
        r = self._run("read", "同名 题")
        self.assertEqual(r.returncode, 1)     # 多义标题确定性拒绝，绝不猜


if __name__ == "__main__":
    unittest.main()
