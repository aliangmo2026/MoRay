# -*- coding: utf-8 -*-
r"""Memory Extractor 离线验收（路线二 2026-11-07）

直接调用 memory_extractor.extract_memory_from_chat（不经 8000），验证：
  用例A 闲聊零入库（"你好/天气"类对话 → facts=[] 且零写库）
  用例B 样例对话提取落库（"我是一名前端开发者，喜欢用 TypeScript 和 React"）
  用例C 同对话重放 → KNN 去重（cos>0.9 → skipped_duplicate，零新行）
  用例D 同义改写 → 0.7~0.9 档（updated 或 skipped_duplicate，零新行）
  用例E 异域新事实 → 正常新增（cos<0.7 → inserted）
  附加  corpus 零污染（全库 kind 无 'corpus'）+ 召回白名单可见 auto_*

隔离铁律：MORAY_MEMORY_DB 指向 %TEMP% 临时库（导入前设置，load_dotenv 不覆盖
已有环境变量），真库 server/data/memory.sqlite3 全程不被触碰。

运行（必须用 8000 真身解释器，bash 默认 python 不是它）：
  cd /d D:\MoRay && D:\python3.12\python.exe scripts\test_memory_extractor.py
前置：本机 Ollama (11434) 在线，含 qwen2.5:1.5b 与 nomic-embed-text。
"""
import os
import sys
import tempfile
import time
import urllib.request

# ---- 隔离必须在导入任何 server 模块之前 ----
_TMP_DB = os.path.join(tempfile.gettempdir(), "moray_memextract_test.sqlite3")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
os.environ["MORAY_MEMORY_DB"] = _TMP_DB

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLLAMA = "http://localhost:11434"


def _ollama_alive() -> bool:
    # Mimosa SSRF 纪律：探测 URL 必须整串字面量（变量拼 URL 会被拦）
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _wait_embedding(timeout_s: float = 150.0) -> bool:
    """等待 spawn_embed 后台线程把用例B首条 auto_ 行嵌完（nomic 冷加载实测 83s）。"""
    from server.app import memory_db
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM memory_bank "
                "WHERE source='memory_extractor' AND embedding IS NOT NULL").fetchone()
        finally:
            conn.close()
        if row and row["c"] >= 1:
            return True
        time.sleep(2.0)
    return False


def _bank_rows():
    from server.app import memory_db
    conn = memory_db.connect()
    try:
        return conn.execute(
            "SELECT id, kind, content, source, session_id, tags, "
            "       (embedding IS NOT NULL) AS has_vec, created_at "
            "FROM memory_bank ORDER BY created_at ASC").fetchall()
    finally:
        conn.close()


PASS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    PASS.append(cond)
    print("  [%s] %s%s" % (tag, name, ("  <" + detail + ">") if detail else ""))


def main() -> int:
    print("=" * 72)
    print("Memory Extractor 离线验收  (temp DB = %s)" % _TMP_DB)
    print("=" * 72)

    if not _ollama_alive():
        print("FAIL: Ollama (%s) 不在线——提炼与嵌入都依赖它，请先起 Ollama" % OLLAMA)
        return 1

    from server.app import config, memory_db
    from server.app.memory_extractor import extract_memory_from_chat
    memory_db.init_memory_db()
    print("隔离库: %s" % config.MEMORY_DB)
    print("提炼模型: %s   Ollama: %s   维度: %d" % (
        config.MEMORY_EXTRACT_MODEL, config.OLLAMA_BASE_URL, memory_db.EMBED_DIM))

    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def run(coro):
        return loop.run_until_complete(coro)

    # ---- 用例A：闲聊零入库 ----
    print("\n[用例A] 纯寒暄闲聊（你好/天气）→ 零提炼零入库")
    chitchat = [
        {"role": "user", "content": "你好呀"},
        {"role": "assistant", "content": "你好！有什么可以帮你？"},
        {"role": "user", "content": "今天天气如何"},
        {"role": "assistant", "content": "我查不了实时天气，不过你可以看看窗外 :)"},
    ]
    st = run(extract_memory_from_chat(chitchat, "test_chitchat"))
    check("闲聊零事实（facts=[] 或护栏拦下）", not st["facts"],
          "reason=%s facts=%s" % (st.get("skipped_reason") or st.get("error"), st["facts"]))
    check("闲聊零写库", len(_bank_rows()) == 0, "rows=%d" % len(_bank_rows()))

    # ---- 用例B：样例对话提取落库 ----
    print("\n[用例B] 样例对话（前端开发者 + TypeScript/React）→ 提取落库")
    sample = [
        {"role": "user", "content": "我是一名前端开发者，喜欢用 TypeScript 和 React"},
        {"role": "assistant", "content": "了解！TS 的类型系统和 React 的组件模型确实很搭，需要我帮你看看项目代码吗？"},
    ]
    st = run(extract_memory_from_chat(sample, "test_sample"))
    rows = _bank_rows()
    auto_rows = [r for r in rows if r["source"] == "memory_extractor"]
    print("    提炼: %s  耗时: %dms" % (st["facts"], st["elapsed_ms"]))
    for r in st["results"]:
        print("    -> %s %s" % (r["action"], r))
    check("提炼出 ≥1 条事实", len(st["facts"]) >= 1, "facts=%s" % st["facts"])
    check("落库 ≥1 行 auto_*", len(auto_rows) >= 1,
          "rows=%d" % len(auto_rows))
    check("kind 全在 {auto_fact, auto_pref}",
          all(r["kind"] in ("auto_fact", "auto_pref") for r in auto_rows),
          "kinds=%s" % [r["kind"] for r in auto_rows])
    check("source=memory_extractor（corpus 鱼目不混珠）",
          all(r["source"] == "memory_extractor" for r in auto_rows)
          and not any(r["kind"] == "corpus" for r in rows))

    # ---- 等 spawn_embed 补嵌完成（用例C/D 依赖向量面） ----
    print("\n等待后台补嵌（nomic 冷加载可能 ~83s）...", end="", flush=True)
    ok = _wait_embedding()
    print(" %s" % ("完成" if ok else "超时（去重档位验证将不可靠）"))

    n_before = len(_bank_rows())

    # ---- 用例C：同对话重放 → cos>0.9 跳过 ----
    print("\n[用例C] 同对话重放 → KNN 去重跳过（cos>0.9）")
    st = run(extract_memory_from_chat(sample, "test_sample"))
    print("    提炼: %s" % st["facts"])
    for r in st["results"]:
        print("    -> %s cos=%s" % (r["action"], r.get("cos")))
    n_after = len(_bank_rows())
    check("零新行（去重生效）", n_after == n_before,
          "before=%d after=%d" % (n_before, n_after))
    check("动作全为 skipped_duplicate",
          all(r["action"] in ("skipped_duplicate",) for r in st["results"]) if st["results"] else ok,
          "actions=%s" % [r["action"] for r in st["results"]])

    # ---- 用例D：同义改写 → 0.7~0.9 更新档 ----
    "我做前端开发的，平时写 React 和 TypeScript 居多"  # noqa - 占位防误删说明
    print("\n[用例D] 同义改写（前端/React/TS 换个说法）→ updated 或 skipped")
    para = [
        {"role": "user", "content": "我做前端开发的，平时写 React 和 TypeScript 居多"},
        {"role": "assistant", "content": "好嘞，那咱们后面就按 React + TS 的路子来。"},
    ]
    st = run(extract_memory_from_chat(para, "test_para"))
    print("    提炼: %s" % st["facts"])
    for r in st["results"]:
        print("    -> %s cos=%s" % (r["action"], r.get("cos")))
    check("零新行（近似去重生效）", len(_bank_rows()) == n_after,
          "rows=%d" % len(_bank_rows()))
    if st["results"]:
        check("动作 ∈ {updated, skipped_duplicate}",
              all(r["action"] in ("updated", "skipped_duplicate") for r in st["results"]),
              "actions=%s" % [r["action"] for r in st["results"]])

    # ---- 用例E：异域新事实 → 正常新增 ----
    print("\n[用例E] 异域新事实（MoRay 项目背景）→ 正常新增")
    other = [
        {"role": "user", "content": "我最近在做 MoRay 项目，一个本地优先的个人 AI 助手，后端是 Python FastAPI"},
        {"role": "assistant", "content": "本地优先是个好路线，隐私和延迟都占优。需要我帮你过一遍哪个模块？"},
    ]
    st = run(extract_memory_from_chat(other, "test_other"))
    print("    提炼: %s" % st["facts"])
    for r in st["results"]:
        print("    -> %s" % r["action"])
    check("新增 ≥1 行（cos<0.7 档）", len(_bank_rows()) > n_after,
          "rows %d -> %d" % (n_after, len(_bank_rows())))

    # ---- 召回白名单可见性（长记性要能被召回） ----
    print("\n[附加] 召回白名单收编验证（/api/memory/recall 口径）")
    conn = memory_db.connect()
    try:
        hit = conn.execute(
            "SELECT content FROM memory_bank WHERE kind IN "
            "('fact','task_result','insight','preference','auto_fact','auto_pref') "
            "AND source='memory_extractor' LIMIT 1").fetchone()
    finally:
        conn.close()
    check("auto_* 行进入召回白名单口径", hit is not None,
          hit["content"][:40] if hit else "")

    # ---- 终局清单 ----
    print("\n" + "=" * 72)
    print("临时库 memory_bank 全量（应全为 memory_extractor / auto_*）：")
    for r in _bank_rows():
        print("  [%s] kind=%-10s vec=%s  %s" % (
            r["created_at"], r["kind"], "Y" if r["has_vec"] else "N", r["content"][:64]))
    total, passed = len(PASS), sum(1 for x in PASS if x)
    print("=" * 72)
    print("RESULT: %s  (%d/%d)" % ("ALL GREEN" if passed == total else "HAS FAIL",
                                   passed, total))
    loop.close()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
