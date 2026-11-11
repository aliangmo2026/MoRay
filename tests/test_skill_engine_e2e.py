# -*- coding: utf-8 -*-
"""经验泛化引擎 E2E 验证（2026-11-10）

四段验证（需本机 Ollama 在线：nomic 嵌入 + qwen2.5:1.5b 提炼）：
  A. 纯单元：失败判定 / 规则解析护栏 / 注入文本渲染
  B. 提炼 E2E：插失败经验 → 扫描提炼 → skill_rule 落库（weight=0.8）
  C. 注入 E2E：相似任务匹配（cos>0.75）+ 注入记账（injections+1）
  D. 生命周期 E2E：连败 5 次 → 0.8-0.2=0.6；再连败 5 次 → 0.4<0.5 归档；
     成功结果清零连败。

跑法：D:/python3.12/python.exe tests/test_skill_engine_e2e.py   （cwd=仓库根）
"""
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import skill_extractor as se  # noqa: E402
from app import memory_db  # noqa: E402
from app.memory_routes import insert_memory  # noqa: E402

TEST_TAG = "skill_engine_test"
_PASS = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print("[%s] %s %s" % (tag, name, detail))
    _PASS.append(bool(cond))


def _ts(offset_sec: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=offset_sec)).strftime("%Y-%m-%dT%H:%M:%S")


def _insert_raw(content: str, kind: str, tags: list, created_at: str) -> str:
    """直接插 memory_bank + FTS（绕过 spawn_embed，测试可控时间戳）。"""
    mem_id = uuid.uuid4().hex
    conn = memory_db.connect()
    try:
        conn.execute(
            "INSERT INTO memory_bank (id, kind, content, tags, source, entity_id, session_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mem_id, kind, content, ",".join(tags), TEST_TAG, "", "default", created_at, created_at))
        conn.execute(
            "INSERT INTO memory_fts (memory_id, content, tags) VALUES (?, ?, ?)",
            (mem_id, content, ",".join(tags)))
        conn.commit()
    finally:
        conn.close()
    return mem_id


def _stats_row(rule_id: str) -> dict:
    conn = memory_db.connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS skill_rule_stats (rule_id TEXT PRIMARY KEY, injections INTEGER NOT NULL DEFAULT 0, fail_streak INTEGER NOT NULL DEFAULT 0, last_injected_at TEXT NOT NULL DEFAULT '', last_outcome_at TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
        row = conn.execute(
            "SELECT * FROM skill_rule_stats WHERE rule_id = ?", (rule_id,)).fetchone()
        return dict(row) if row is not None else {}
    finally:
        conn.close()


def _weight(rule_id: str) -> float:
    conn = memory_db.connect()
    try:
        row = conn.execute(
            "SELECT weight FROM memory_bank WHERE id = ?", (rule_id,)).fetchone()
        return float(row["weight"]) if row is not None and row["weight"] is not None else 1.0
    finally:
        conn.close()


def _cleanup_test_data() -> None:
    """清掉上一轮测试残留（带 TEST_TAG 的记忆/FTS/规则计数 + 孤儿 stats），保证本轮从零起。"""
    conn = memory_db.connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM memory_bank WHERE tags LIKE ?", ("%"+TEST_TAG+"%",)).fetchall()]
        for mid in ids:
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mid,))
            conn.execute("DELETE FROM skill_rule_stats WHERE rule_id = ?", (mid,))
            conn.execute("DELETE FROM memory_bank WHERE id = ?", (mid,))
        # 孤儿 stats 行（规则行已删但计数还在）一并清
        conn.execute("DELETE FROM skill_rule_stats WHERE rule_id NOT IN (SELECT id FROM memory_bank)")
        conn.commit()
        print("cleanup: removed %d test rows" % len(ids))
    finally:
        conn.close()


# ---- A. 纯单元 ----

def unit_tests() -> None:
    print("===== A. 纯单元 =====")
    _ok("失败判定:tags failure", se._is_failed_record("laya_task,failure,x", "随便"))
    _ok("失败判定:夜班标题", se._is_failed_record("", "【夜班任务失败】连续失败"))
    _ok("失败判定:error_type", se._is_failed_record("", '结果：{"error_type": "FileNotFoundError"}'))
    _ok("失败判定:退出码>0", se._is_failed_record("", "耗时：102ms·退出码：1"))
    _ok("失败判定:退出码0为成功", not se._is_failed_record("", "耗时：102ms·退出码：0"))
    _ok("失败判定:普通成功", not se._is_failed_record("laya_task", "任务完成，结果正常"))
    _ok("规则解析:裸数组", se._parse_rules('["调用 Scribe 前必须确认目录存在"]') == ["调用 Scribe 前必须确认目录存在"])
    _ok("规则解析:围栏", se._parse_rules('```json\n["执行 Windows 脚本前先检查路径空格"]\n```') == ["执行 Windows 脚本前先检查路径空格"])
    _ok("规则解析:垃圾话术弃", se._parse_rules('["无法提炼出通用规则"]') == [])
    _ok("规则解析:过长弃", se._parse_rules('["' + "长" * 80 + '"]') == [])
    _ok("规则解析:空输出", se._parse_rules("") == [])
    txt = se.render_injection_text([{"rule": "规则甲"}, {"rule": "规则乙"}])
    _ok("注入文本渲染", "【系统经验拦截】" in txt and "1. 规则甲" in txt and "2. 规则乙" in txt)


# ---- B. 提炼 E2E ----

def extraction_e2e() -> str:
    print("===== B. 提炼 E2E（真实小模型调用） =====")
    conn = memory_db.connect()
    try:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM memory_bank").fetchone()
        ck_before = int(row[0] or 0)
    finally:
        conn.close()
    exp = ("任务：执行脚本 D:\\My Projects\\run server.bat 启动本地服务 / 工具链：zcode / 结果："
           '{"error_type": "CommandNotFoundError", "error_content": "路径 D:\\My Projects 包含空格'
           '且未加引号，命令被拆分解析失败，进程以退出码 1 终止。"}')
    _insert_raw(exp, "task_result", [TEST_TAG, "failure", "zcode"], _ts(0))
    ins = ("ZCode 的 Read/Edit 工具在当前环境下不支持自动解析相对路径，执行文件操作时必须提供"
           "绝对路径以防出现文件不存在错误。")
    _insert_raw(ins, "insight", [TEST_TAG, "reflection"], _ts(1))
    # 测试产出的规则行也打 TEST_TAG（否则 cleanup 捞不到，残留会触发去重跳过）
    se._TAGS = ("skill_rule", "auto", TEST_TAG)
    # 回拨 checkpoint 让 scan_once 捡起新插的两条（生产由轮询 loop 自然推进）
    conn = memory_db.connect()
    try:
        se._ensure_tables(conn)
        se._set_checkpoint(conn, ck_before)
    finally:
        conn.close()
    stats = se.scan_once()
    print("scan_once:", {k: v for k, v in stats.items() if k != "extracted"},
          "| extracted:", [(d.get("action"), d.get("rule_id", "")[:8]) for d in stats.get("extracted", [])])
    inserted = [d for d in stats.get("extracted", []) if d.get("action") == "inserted"]
    _ok("提炼出规则并落库", len(inserted) >= 1,
        "%d 条 inserted" % len(inserted))
    if not inserted:
        return ""
    rule_id = inserted[0]["rule_id"]
    conn = memory_db.connect()
    try:
        row = conn.execute(
            "SELECT kind, source, weight, content FROM memory_bank WHERE id = ?", (rule_id,)).fetchone()
    finally:
        conn.close()
    _ok("kind=skill_rule / source=skill_extractor", row["kind"] == "skill_rule" and row["source"] == "skill_extractor",
        str(dict(row)))
    _ok("初始置信度 0.8", abs(float(row["weight"]) - 0.8) < 1e-6, "weight=%s" % row["weight"])
    return rule_id


# ---- C. 注入 E2E ----

def injection_e2e(rule_id: str) -> None:
    print("===== C. 注入 E2E =====")
    rules = se.match_and_record("执行一个路径包含空格的脚本")
    _ok("相似任务匹配命中", len(rules) >= 1, str([(r["cosine"], r["rule"][:24]) for r in rules]))
    _ok("相似度>0.75", all(r["cosine"] > 0.75 for r in rules) if rules else False)
    _ok("最多3条", len(rules) <= 3)
    s = _stats_row(rule_id)
    _ok("注入记账 injections>=1", int(s.get("injections", 0)) >= 1, str(s))
    # 不相关任务不应命中（低相似度闸）
    rules_unrel = se.match_rules_for_task("今天天气怎么样，适合出门散步吗")
    _ok("无关任务零注入", len(rules_unrel) == 0, "%d 条（应 0）" % len(rules_unrel))


# ---- D. 生命周期 E2E ----

def lifecycle_e2e(rule_id: str) -> None:
    print("===== D. 生命周期 E2E（模型调用打桩为 [] 提速） =====")
    se._call_extract_model = lambda t: "[]"  # 打桩：本阶段只考生命周期，不提炼
    s = _stats_row(rule_id)
    if not s.get("last_injected_at"):
        se.record_injection([rule_id])
        s = _stats_row(rule_id)
    injected_at = s["last_injected_at"]
    print("last_injected_at =", injected_at)
    # 第一轮：注入后连续 5 条失败结果 → 0.8 - 0.2 = 0.6（不归档）
    for i in range(1, 6):
        _insert_raw("任务：自测失败场景%d / 结果：boom 退出" % i, "task_result",
                    [TEST_TAG, "failure"], _ts(i * 3))
    se.scan_once()
    _ok("连败5次降信 0.8→0.6", abs(_weight(rule_id) - 0.6) < 1e-6, "weight=%.3f" % _weight(rule_id))
    st = _stats_row(rule_id)
    _ok("降信后连败计数清零", int(st.get("fail_streak", 0)) == 0, str(st))
    _ok("未归档", int(st.get("archived", 0)) == 0)
    # 成功结果 → 连败清零（覆盖成功重置分支）
    _insert_raw("任务：自测成功场景 / 耗时：55ms·退出码：0 / 结果：ok", "task_result",
                [TEST_TAG], _ts(20))
    _insert_raw("任务：自测失败场景6 / 结果：boom", "task_result", [TEST_TAG, "failure"], _ts(23))
    se.scan_once()
    st = _stats_row(rule_id)
    _ok("成功重置连败→本次失败 streak=1", int(st.get("fail_streak", 0)) == 1, str(st))
    # 第二轮：再连败 5 次 → 0.6-0.2=0.4 < 0.5 → 归档
    for i in range(7, 12):
        _insert_raw("任务：自测失败场景%d / 结果：boom" % i, "task_result",
                    [TEST_TAG, "failure"], _ts(30 + i * 3))
    se.scan_once()
    _ok("再连败5次降信 0.6→0.4", abs(_weight(rule_id) - 0.4) < 1e-6, "weight=%.3f" % _weight(rule_id))
    st = _stats_row(rule_id)
    _ok("置信度<0.5 自动归档", int(st.get("archived", 0)) == 1, str(st))
    rules = se.match_rules_for_task("执行一个路径包含空格的脚本")
    _ok("归档后不再注入", all(r["rule_id"] != rule_id for r in rules),
        "%d 条命中（不含归档规则）" % len(rules))


def cleanup_hint() -> None:
    print("\n清理提示：测试记忆带 tag=skill_engine_test，可在记忆库 UI 按 tag 过滤删除，或执行：")
    print("  DELETE FROM memory_bank WHERE tags LIKE '%skill_engine_test%'（含 FTS 同步删除）")


if __name__ == "__main__":
    t0 = time.time()
    _cleanup_test_data()
    unit_tests()
    rid = ""
    try:
        rid = extraction_e2e()
        if rid:
            injection_e2e(rid)
            lifecycle_e2e(rid)
        else:
            _ok("提炼 E2E（后续段依赖）", False, "无 inserted 规则，C/D 段跳过")
    finally:
        cleanup_hint()
    total = sum(1 for p in _PASS if p)
    print("\n===== 结果：%d/%d PASS（耗时 %.1fs）=====" % (total, len(_PASS), time.time() - t0))
    sys.exit(0 if total == len(_PASS) else 1)
