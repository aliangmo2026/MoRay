# -*- coding: utf-8 -*-
"""test_path_diagnostics — 诊断层单元测试（任务三，11 条）。"""
import os
import stat
import sys

sys.path.insert(0, r"D:\MoRay")

import pytest  # noqa: E402

from core import path_diagnostics  # noqa: E402
from core.path_diagnostics import (  # noqa: E402
    AclDenied,
    FileLocked,
    FileProxyError,
    InvalidPathName,
    ParentNotWritable,
    PathEscapeError,
    PathTooLong,
    ReadOnlyFile,
    TargetIsDirectory,
    VolumePermissionDenied,
    diagnose,
    diagnose_report,
)


# ---------- 1. 正常路径 → 返回 Path，内容 == 输入规范化 ----------
def test_01_normal_path_returns_normalized(tmp_path):
    target = tmp_path / "a.txt"
    p = diagnose(str(target), "w", [str(tmp_path)])
    assert isinstance(p, type(tmp_path))  # pathlib.Path
    assert os.path.normcase(str(p)) == os.path.normcase(str(target))


# ---------- 2. "../../etc/passwd" 逃逸 → PathEscapeError ----------
def test_02_relative_escape_rejected(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    with pytest.raises(PathEscapeError):
        diagnose("../../etc/passwd", "r", [str(tmp_path)])


# ---------- 3. 超长路径 → PathTooLong ----------
def test_03_too_long_rejected(tmp_path):
    long_name = "x" * 300
    target = tmp_path / long_name
    assert len(str(target)) > 260
    with pytest.raises(PathTooLong):
        diagnose(str(target), "r", [str(tmp_path)])


# ---------- 4. "CON" / "aux.txt" / "a. " / "a." → InvalidPathName ----------
def test_04_reserved_and_trailing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for bad in ("CON", "aux.txt", "a. ", "a."):
        with pytest.raises(InvalidPathName, match=bad.strip()):
            diagnose(bad, "r", [str(tmp_path)])


# ---------- 5. 目标是已存在目录 → TargetIsDirectory ----------
def test_05_target_is_directory(tmp_path):
    d = tmp_path / "conflict"
    d.mkdir()
    with pytest.raises(TargetIsDirectory):
        diagnose(str(d), "w", [str(tmp_path)])


# ---------- 6. 只读文件（attrib +r 等效只读位）→ ReadOnlyFile ----------
def test_06_readonly_file(tmp_path):
    f = tmp_path / "ro.txt"
    f.write_text("seed", encoding="utf-8")
    os.chmod(str(f), stat.S_IREAD)  # Windows 上置 FILE_ATTRIBUTE_READONLY（与 attrib +r 同位）
    try:
        st = f.stat()
        assert st.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY  # 前置自检
        with pytest.raises(ReadOnlyFile):
            diagnose(str(f), "w", [str(tmp_path)])
    finally:
        os.chmod(str(f), stat.S_IWRITE)


# ---------- 7. 父目录不存在且不可创建 → ParentNotWritable ----------
def test_07_parent_not_writable(tmp_path, monkeypatch):
    # Windows 实测 os.access 对 chmod 0o555 目录仍返回 True（_waccess 局限），
    # 故此处 mock os.access 验证诊断分支；真实 ACL 拒绝由 AclDenied(icacls) 与
    # 写入期 convert_oserror 兜底。
    f = tmp_path / "missing_dir" / "child" / "f.txt"  # 祖先链上最近存在者 = tmp_path
    monkeypatch.setattr(path_diagnostics.os, "access", lambda p, m: False)
    with pytest.raises(ParentNotWritable):
        diagnose(str(f), "w", [str(tmp_path)])


# ---------- 8. 所有异常都是 FileProxyError 子类 ----------
def test_08_all_subclass_of_base():
    for cls in (PathEscapeError, PathTooLong, InvalidPathName, TargetIsDirectory,
                ReadOnlyFile, ParentNotWritable, AclDenied, FileLocked,
                VolumePermissionDenied):
        assert issubclass(cls, FileProxyError)
        assert issubclass(cls, Exception)


# ---------- 9. diagnose_report 在同样输入下 ok=False 且 error_type 正确 ----------
def test_09_report_matches_exceptions(tmp_path):
    d = tmp_path / "conflict"
    d.mkdir()
    rep = diagnose_report(str(d), "w", [str(tmp_path)])
    assert rep["ok"] is False
    assert rep["error_type"] == "TargetIsDirectory"
    assert rep["error_message"]
    assert isinstance(rep["checks"], list) and rep["checks"]
    # 正常路径的报告
    ok_rep = diagnose_report(str(tmp_path / "fine.txt"), "w", [str(tmp_path)])
    assert ok_rep["ok"] is True and ok_rep["error_type"] is None


# ---------- 10. 白名单边界：allowed="…\a"，路径 "…\ab\x" 应逃逸（防前缀误匹配）----------
def test_10_whitelist_boundary_no_prefix_match(tmp_path):
    allowed = tmp_path / "a"
    sneaky = tmp_path / "ab" / "x"
    with pytest.raises(PathEscapeError):
        diagnose(str(sneaky), "r", [str(allowed)])


# ---------- 11. PathEscapeError 是 FileProxyError 子类 + 报告逃逸形态 ----------
def test_11_escape_error_class_and_report(tmp_path, monkeypatch):
    assert issubclass(PathEscapeError, FileProxyError)
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    rep = diagnose_report("../../etc/passwd", "r", [str(tmp_path)])
    assert rep["ok"] is False
    assert rep["error_type"] == "PathEscapeError"
    assert "allowed_roots" in rep["error_message"]
