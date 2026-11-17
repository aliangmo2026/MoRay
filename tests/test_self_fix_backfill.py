# -*- coding: utf-8 -*-
"""[P2-2 2026-11-17] 幻影自愈沙箱修复回归：目录创建 + 历史 pending 卡补盘。

[体检 2026-11-16 实锤] 14 张 pending 卡指向不存在的 workspace/self_fix/。
补盘的三条铁律（本测试全守）：
1. 幂等：重复调用零重复写；
2. 越界防脏数据：fix_target 不在 _SELF_FIX_DIR 下绝不落盘；
3. 占位不假通过：存根内容不含 organ/修复草案标记 → verify_cmd 依旧失败，
   直到引擎真正执行该卡覆盖写真报告。
"""
import json

import pytest

from server.app import self_driven


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离自愈沙箱目录与状态文件到临时区。"""
    fix_dir = tmp_path / "workspace" / "self_fix"
    monkeypatch.setattr(self_driven, "_SELF_FIX_DIR", str(fix_dir))
    state_file = tmp_path / "self_driven_state.json"
    monkeypatch.setattr(self_driven, "STATE_FILE", state_file)
    return fix_dir, state_file


def _write_state(state_file, flog):
    state_file.write_text(json.dumps(
        {"queue": [], "daily": {}, "verdict_log": [],
         "self_fix_log": flog, "auto_approve_readonly": True},
        ensure_ascii=False), encoding="utf-8")


def test_backfill_creates_stub_without_markers(sandbox):
    fix_dir, state_file = sandbox
    target = str(fix_dir / "self_fix_20261107_fs.md")
    _write_state(state_file, [{
        "date": "2026-11-07", "organ": "fs", "streak": 5, "cause": "未知",
        "task_id": "t-123", "fix_target": target,
        "card_status": "pending_approval", "created_at": "2026-11-07T00:00:00",
    }])
    n = self_driven._backfill_self_fix_targets()
    assert n == 1
    p = fix_dir / "self_fix_20261107_fs.md"
    assert p.is_file()
    content = p.read_text(encoding="utf-8")
    # 占位不假通过：organ 与修复草案标记绝不出现（verify_cmd 三条件缺二必失败）
    assert "fs" not in content
    assert self_driven._SELF_FIX_PLAN_MARK not in content
    # 幂等：第二遍零重复写
    assert self_driven._backfill_self_fix_targets() == 0


def test_backfill_skips_out_of_sandbox_target(sandbox):
    fix_dir, state_file = sandbox
    _write_state(state_file, [{
        "date": "2026-11-07", "organ": "fs", "streak": 5, "cause": "未知",
        "task_id": "t-456", "fix_target": str(sandbox[1].parent / "evil.md"),
        "card_status": "pending_approval", "created_at": "2026-11-07T00:00:00",
    }])
    assert self_driven._backfill_self_fix_targets() == 0
    assert not (state_file.parent / "evil.md").exists()


def test_ensure_sandbox_writes_readme_once(sandbox):
    fix_dir, _ = sandbox
    assert self_driven._ensure_self_fix_sandbox() is True
    assert (fix_dir / "_README.md").is_file()
    # README 也不携带修复草案标记（防未来出现按目录全文扫标记的判据被白嫖）
    assert self_driven._SELF_FIX_PLAN_MARK not in \
        (fix_dir / "_README.md").read_text(encoding="utf-8")
    mtime1 = (fix_dir / "_README.md").stat().st_mtime
    assert self_driven._ensure_self_fix_sandbox() is True
    assert (fix_dir / "_README.md").stat().st_mtime == mtime1  # 已存在不重写
