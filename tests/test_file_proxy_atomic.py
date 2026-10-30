# -*- coding: utf-8 -*-
"""test_file_proxy_atomic — file_proxy 重构（原子写 + 诊断异常映射）测试（任务三 10 条 + U-3 幂等守卫 2 条）。"""
import builtins
import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, r"D:\MoRay")

import pytest  # noqa: E402

from core import file_proxy, zcode_proxy  # noqa: E402
from core.path_diagnostics import (  # noqa: E402
    FileLocked,
    ReadOnlyFile,
)


def _sandbox_write(sp: str, content: str) -> dict:
    """与 tool_registry._sandbox_write_text 同构的"原写入函数"：操作沙箱副本。"""
    p = Path(sp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"success": True}


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """临时白名单环境：CONFIG_PATH 指向临时配置，authorized_dirs/sandbox_root 全在 tmp_path 下。"""
    root = tmp_path / "ws"
    root.mkdir()
    sandbox = tmp_path / "mapped"
    sandbox.mkdir()
    cfg = {"authorized_dirs": [str(root)], "sandbox_root": str(sandbox)}
    cfg_path = tmp_path / "authorized_paths.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(file_proxy, "CONFIG_PATH", str(cfg_path))
    return {"root": root, "sandbox": sandbox, "cfg_path": cfg_path}


# ---------- 1. 正常写入成功，内容正确 ----------
def test_01_normal_write(auth_env):
    user_path = auth_env["root"] / "a.txt"
    r = file_proxy.proxy_file_write(str(user_path), "hello", _sandbox_write)
    assert isinstance(r, dict) and r.get("success") is True
    assert user_path.read_text(encoding="utf-8") == "hello"


# ---------- 2. 父目录不存在时自动创建并写入成功 ----------
def test_02_auto_mkdir(auth_env):
    user_path = auth_env["root"] / "deep" / "dir" / "x.txt"
    r = file_proxy.proxy_file_write(str(user_path), "deep", _sandbox_write)
    assert r.get("success") is True
    assert user_path.exists() and user_path.read_text(encoding="utf-8") == "deep"


# ---------- 3. 写入过程抛异常 → 不留半截文件，tmp 被清理（mock open 失败）----------
def test_03_tmp_cleaned_on_failure(auth_env, tmp_path, monkeypatch):
    user_path = auth_env["root"] / "a.txt"
    user_path.write_text("old", encoding="utf-8")

    real_open = builtins.open

    def fake_open(f, *a, **k):
        if str(f).endswith(".tmp"):
            raise OSError("boom: 模拟写入中断")
        return real_open(f, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(OSError, match="boom"):
        file_proxy.proxy_file_write(str(user_path), "new", _sandbox_write)
    # 半截文件断言：原内容完好 + 无 tmp 残留
    assert user_path.read_text(encoding="utf-8") == "old"
    leftovers = list(user_path.parent.glob(".*.tmp")) + list(user_path.parent.glob("*.tmp"))
    assert leftovers == []


# ---------- 4. PermissionError 转成对应异常，不被吞 ----------
def test_04_permission_error_converted(auth_env, monkeypatch):
    user_path = auth_env["root"] / "a.txt"
    user_path.write_text("old", encoding="utf-8")

    real_replace = os.replace

    def fake_replace(src, dst):
        if os.path.normcase(str(dst)) == os.path.normcase(str(user_path)):
            raise PermissionError(13, "另一个程序正在使用此文件", None, 32)  # ERROR_SHARING_VIOLATION
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)
    with pytest.raises(FileLocked):  # 绝不允许裸 PermissionError 穿透，也不允许被吞
        file_proxy.proxy_file_write(str(user_path), "new", _sandbox_write)
    leftovers = list(user_path.parent.glob(".*.tmp"))
    assert leftovers == []


# ---------- 5. 逃逸路径被拒绝 ----------
def test_05_unauthorized_rejected(auth_env, tmp_path):
    outside = tmp_path / "outside" / "x.txt"
    r = file_proxy.proxy_file_write(str(outside), "x", _sandbox_write)
    assert isinstance(r, dict) and r.get("success") is False
    assert "路径未授权" in r["error"]


# ---------- 6. 原子替换：原文件被完整替换（st_ino / mtime 断言）----------
def test_06_atomic_replacement(auth_env):
    user_path = auth_env["root"] / "a.txt"
    file_proxy.proxy_file_write(str(user_path), "v1", _sandbox_write)
    st1 = user_path.stat()
    file_proxy.proxy_file_write(str(user_path), "v2", _sandbox_write)
    st2 = user_path.stat()
    assert user_path.read_text(encoding="utf-8") == "v2"
    assert st2.st_ino != st1.st_ino, "os.replace 应换掉原文件对象（新 inode）"
    assert st2.st_mtime_ns >= st1.st_mtime_ns
    leftovers = list(user_path.parent.glob(".*.tmp"))
    assert leftovers == []


# ---------- 7. 现有公开函数签名未被破坏 ----------
def test_07_signatures_preserved():
    assert list(inspect.signature(file_proxy.proxy_file_read).parameters) == [
        "user_path", "original_read_func"]
    assert list(inspect.signature(file_proxy.proxy_file_write).parameters) == [
        "user_path", "content", "original_write_func"]
    assert list(inspect.signature(file_proxy.sync_file_back).parameters) == [
        "sandbox_path", "user_path"]
    assert list(inspect.signature(file_proxy.is_authorized_path).parameters) == [
        "user_path"]
    assert list(inspect.signature(file_proxy.translate_to_sandbox).parameters) == [
        "user_path"]


# ---------- 8. zcode_proxy 调用 file_proxy 的路径仍通（mock 一次 sub_agent 的路径预检）----------
def test_08_zcode_proxy_link(auth_env, tmp_path, monkeypatch):
    import core.path_diagnostics as pd

    work = auth_env["root"] / "proj"
    work.mkdir()

    calls = {"diagnose": 0, "run": None}

    # 直接包一层真函数，避免依赖内部实现细节
    real_diagnose = pd.diagnose

    def spy(p, mode, roots):
        calls["diagnose"] += 1
        return real_diagnose(p, mode, roots)

    monkeypatch.setattr(pd, "diagnose", spy)

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # 只拦 zcode CLI 调用；diagnose 内部的 icacls 探测等仍走真 subprocess
        if isinstance(cmd, list) and "--cwd" in cmd:
            calls["run"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=b"[modified]\n- demo.py: ok", stderr=b"")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(zcode_proxy.subprocess, "run", fake_run)
    monkeypatch.setattr(zcode_proxy, "load_scene_prompt", lambda scene="general": "SYS")

    r = zcode_proxy.ZCodeProxy.sub_agent("do it", str(work))
    assert r["success"] is True
    assert r["failed_files"] == []
    assert calls["diagnose"] >= 1  # sub_agent 预检确实走了 diagnose
    cwd_arg = calls["run"][calls["run"].index("--cwd") + 1]
    assert os.path.normcase(cwd_arg) == os.path.normcase(str(work))


# ---------- 9.（决策点 1 映射表）逃逸路径返回 dict，不 raise ----------
def test_09_escape_returns_dict_not_raise(auth_env, tmp_path, monkeypatch):
    outside = tmp_path / "outside" / "x.txt"
    monkeypatch.setattr(file_proxy, "is_authorized_path", lambda p: True)
    monkeypatch.setattr(file_proxy, "translate_to_sandbox",
                        lambda p: str(auth_env["sandbox"] / "x.txt"))
    r = file_proxy.proxy_file_write(str(outside), "x", _sandbox_write)
    assert isinstance(r, dict) and r.get("success") is False
    assert "路径逃逸" in r["error"]


# ---------- 10.（决策点 1 另一侧）只读文件 raise ReadOnlyFile ----------
def test_10_readonly_raises(auth_env):
    f = auth_env["root"] / "ro.txt"
    f.write_text("seed", encoding="utf-8")
    os.chmod(str(f), stat.S_IREAD)  # 等效 attrib +r
    try:
        with pytest.raises(ReadOnlyFile):
            file_proxy.proxy_file_write(str(f), "new", _sandbox_write)
    finally:
        os.chmod(str(f), stat.S_IWRITE)
        f.unlink(missing_ok=True)


@pytest.fixture
def stacking_env(tmp_path, monkeypatch):
    """U-3 专用：复刻真实拓扑 —— sandbox_root 是授权目录的**子目录**
    （真实配置里 D:\\MoRay\\workspace\\mapped ⊂ 授权目录 D:\\MoRay），
    这正是"已映射路径再次命中授权被叠加"的成因；auth_env 里两者是兄弟关系，造不出叠加。"""
    root = tmp_path / "authroot"                  # 授权目录（对应真实 D:\MoRay）
    sandbox = root / "workspace" / "mapped"       # 沙箱映射根（对应 D:\MoRay\workspace\mapped）
    sandbox.mkdir(parents=True)
    cfg = {"authorized_dirs": [str(root)], "sandbox_root": str(sandbox)}
    cfg_path = tmp_path / "authorized_paths.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(file_proxy, "CONFIG_PATH", str(cfg_path))
    return {"root": root, "sandbox": sandbox}


# ---------- 11.（U-3 幂等守卫）输入已在 sandbox_root 内 → 原样返回，不再叠加 ----------
def test_11_idempotent_inside_sandbox(stacking_env):
    inside = stacking_env["sandbox"] / "_ui_test"
    r = file_proxy.translate_to_sandbox(str(inside))
    assert r is not None
    assert os.path.normcase(r) == os.path.normcase(str(inside))


# ---------- 12.（U-3 幂等守卫）连续映射不叠加：第二次被守卫拦下 ----------
def test_12_double_mapping_no_stacking(stacking_env):
    user_path = stacking_env["root"] / "zcode_sandbox" / "_ui_test"
    p1 = file_proxy.translate_to_sandbox(str(user_path))
    assert p1 is not None
    # 首次映射确实发生（排守卫把翻译整个弄成恒等函数的假阳性）
    assert os.path.normcase(p1) != os.path.normcase(str(user_path))
    p2 = file_proxy.translate_to_sandbox(p1)
    assert os.path.normcase(p2) == os.path.normcase(p1)
