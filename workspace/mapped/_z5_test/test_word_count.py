#!/usr/bin/env python3
"""Tests for word_count.py.

Run from this directory with:

    python -m unittest test_word_count -v

The suite covers the normal counting paths and the failure paths
(missing file, directory given instead of a file, unreadable file,
unreadable bytes for the chosen encoding, and invalid command lines).
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import word_count  # noqa: E402  (import after sys.path is fixed up)

SCRIPT = os.path.join(HERE, "word_count.py")


def run_cli(*args):
    """Run word_count.py in a subprocess and capture its output."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


class BaseTest(unittest.TestCase):
    """Gives each test a scratch directory inside this project folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="_wc_tmp_", dir=HERE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(text.encode(encoding))
        return path


class CountTextTest(BaseTest):
    """Unit tests for the pure counting function."""

    def test_typical_text(self):
        self.assertEqual(word_count.count_text("one two\nthree\n"), (2, 3, 14))

    def test_empty_text(self):
        self.assertEqual(word_count.count_text(""), (0, 0, 0))

    def test_final_line_without_newline_still_counts(self):
        self.assertEqual(word_count.count_text("a\nb"), (2, 2, 3))

    def test_blank_lines_count_but_do_not_add_words(self):
        self.assertEqual(word_count.count_text("a\n\n\nb\n"), (4, 2, 6))

    def test_whitespace_runs_are_a_single_separator(self):
        self.assertEqual(word_count.count_text("  a \t b  \n"), (1, 2, 10))

    def test_returns_named_fields(self):
        counts = word_count.count_text("hi there\n")
        self.assertEqual(counts.lines, 1)
        self.assertEqual(counts.words, 2)
        self.assertEqual(counts.chars, 9)


class CountFileTest(BaseTest):

    def test_counts_real_file(self):
        path = self.write("alpha.txt", "hello world\nfoo bar baz\n")
        self.assertEqual(word_count.count_file(path), (2, 5, 24))

    def test_empty_file(self):
        path = self.write("empty.txt", "")
        self.assertEqual(word_count.count_file(path), (0, 0, 0))

    def test_count_bytes_option(self):
        path = self.write("uni.txt", "h\u00e9llo w\u00f6rld\n")
        self.assertEqual(word_count.count_file(path), (1, 2, 12))
        self.assertEqual(word_count.count_file(path, count_bytes=True), (1, 2, 14))

    def test_alternate_encoding(self):
        path = self.write("latin.txt", "caf\u00e9\n", encoding="latin-1")
        self.assertEqual(word_count.count_file(path, encoding="latin-1"), (1, 1, 5))

    def test_missing_file_raises_file_error(self):
        with self.assertRaises(word_count.FileError) as ctx:
            word_count.count_file(os.path.join(self.tmp, "nope.txt"))
        self.assertIn("no such file", str(ctx.exception))

    def test_directory_raises_file_error(self):
        with self.assertRaises(word_count.FileError) as ctx:
            word_count.count_file(self.tmp)
        self.assertIn("is a directory", str(ctx.exception))

    def test_permission_denied_raises_file_error(self):
        path = self.write("locked.txt", "secret\n")
        with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(word_count.FileError) as ctx:
                word_count.count_file(path)
        self.assertIn("permission denied", str(ctx.exception).lower())

    def test_undecodable_bytes_raise_file_error(self):
        path = os.path.join(self.tmp, "bad.bin")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00 not utf-8")
        with self.assertRaises(word_count.FileError) as ctx:
            word_count.count_file(path)
        self.assertIn("cannot decode", str(ctx.exception))

    def test_unknown_encoding_raises_file_error(self):
        path = self.write("a.txt", "hi\n")
        with self.assertRaises(word_count.FileError) as ctx:
            word_count.count_file(path, encoding="not-a-real-codec")
        self.assertIn("unknown encoding", str(ctx.exception))


class MainInProcessTest(BaseTest):
    """main() is called directly so the returned exit code can be checked."""

    def call_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = word_count.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_success_returns_zero(self):
        path = self.write("a.txt", "one two\n")
        code, out, err = self.call_main([path])
        self.assertEqual(code, word_count.EXIT_OK)
        self.assertEqual(out, "{}: 1 lines, 2 words, 8 chars\n".format(path))
        self.assertEqual(err, "")

    def test_multiple_files_each_get_one_line_in_order(self):
        first = self.write("first.txt", "a b c\n")
        second = self.write("second.txt", "d\n")
        code, out, _ = self.call_main([first, second])
        self.assertEqual(code, word_count.EXIT_OK)
        self.assertEqual(
            out.splitlines(),
            [
                "{}: 1 lines, 3 words, 6 chars".format(first),
                "{}: 1 lines, 1 words, 2 chars".format(second),
            ],
        )

    def test_missing_file_returns_one_and_reports_on_stderr(self):
        missing = os.path.join(self.tmp, "ghost.txt")
        code, out, err = self.call_main([missing])
        self.assertEqual(code, word_count.EXIT_FILE_ERROR)
        self.assertEqual(out, "")
        self.assertIn("word_count.py: error:", err)
        self.assertIn(missing, err)
        self.assertIn("no such file", err)

    def test_good_files_are_still_reported_when_one_fails(self):
        good = self.write("good.txt", "keep going\n")
        missing = os.path.join(self.tmp, "ghost.txt")
        code, out, err = self.call_main([missing, good])
        self.assertEqual(code, word_count.EXIT_FILE_ERROR)
        self.assertEqual(out.splitlines(), ["{}: 1 lines, 2 words, 11 chars".format(good)])
        self.assertIn("no such file", err)

    def test_permission_denied_returns_one(self):
        path = self.write("locked.txt", "x\n")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = word_count.main([path])
        self.assertEqual(code, word_count.EXIT_FILE_ERROR)
        self.assertIn("permission denied", err.getvalue().lower())

    def test_bytes_flag_changes_the_unit(self):
        path = self.write("uni.txt", "h\u00e9llo\n")
        code, out, _ = self.call_main(["--bytes", path])
        self.assertEqual(code, word_count.EXIT_OK)
        self.assertEqual(out, "{}: 1 lines, 1 words, 7 bytes\n".format(path))


class CommandLineTest(BaseTest):
    """End-to-end tests through a real subprocess."""

    def test_help_is_friendly_and_exits_zero(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertIn("Count lines, words and characters", result.stdout)
        self.assertIn("exit codes:", result.stdout)
        self.assertIn("--encoding", result.stdout)
        self.assertIn("--bytes", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_version_exits_zero(self):
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("word_count.py", result.stdout)

    def test_no_arguments_is_a_usage_error(self):
        result = run_cli()
        self.assertEqual(result.returncode, word_count.EXIT_USAGE_ERROR)
        self.assertIn("usage:", result.stderr)
        self.assertIn("required", result.stderr)

    def test_unknown_option_is_a_usage_error(self):
        result = run_cli("--frobnicate", "x.txt")
        self.assertEqual(result.returncode, word_count.EXIT_USAGE_ERROR)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_unknown_encoding_is_a_usage_error(self):
        path = self.write("a.txt", "hi\n")
        result = run_cli("--encoding", "not-a-real-codec", path)
        self.assertEqual(result.returncode, word_count.EXIT_USAGE_ERROR)
        self.assertIn("unknown encoding", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_file_exits_one(self):
        result = run_cli(os.path.join(self.tmp, "ghost.txt"))
        self.assertEqual(result.returncode, word_count.EXIT_FILE_ERROR)
        self.assertIn("no such file", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_directory_argument_exits_one(self):
        result = run_cli(self.tmp)
        self.assertEqual(result.returncode, word_count.EXIT_FILE_ERROR)
        self.assertIn("is a directory", result.stderr)

    def test_undecodable_file_exits_one(self):
        path = os.path.join(self.tmp, "bad.bin")
        with open(path, "wb") as handle:
            handle.write(b"\xff\xfe\x00 not utf-8")
        result = run_cli(path)
        self.assertEqual(result.returncode, word_count.EXIT_FILE_ERROR)
        self.assertIn("cannot decode", result.stderr)

    def test_successful_run_exits_zero(self):
        first = self.write("one.txt", "alpha beta\n")
        second = self.write("two.txt", "gamma\ndelta\n")
        result = run_cli(first, second)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "{}: 1 lines, 2 words, 11 chars".format(first),
                "{}: 2 lines, 2 words, 12 chars".format(second),
            ],
        )
        self.assertEqual(result.stderr, "")

    def test_option_after_paths_is_accepted(self):
        path = self.write("word.txt", "abc\n")
        result = run_cli(path, "--encoding", "utf-8")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1 lines, 1 words", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
