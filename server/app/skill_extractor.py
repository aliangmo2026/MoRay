# -*- coding: utf-8 -*-
"""经验泛化引擎（Experience Generalization Engine，2026-11-10）

闭环：失败经验（insight 全量 + 失败 task_result）→ qwen2.5:1.5b 提炼通用规则
→ memory_bank kind='skill_rule'（置信度写 weight，初始 0.8）→ llm_proxy 工具调用轮
按相似度>0.75 注入【系统经验拦截】→ 注入后连败 5 次结果 → 置信度-0.2 → <0.5 归档。

红线：主链路零阻塞（llm_proxy 侧 to_thread + 全模块 fail-open，任何失败只写
logs/skill_extractor.log）；写入面被许可矩阵闸死（source='skill_extractor' ×
kind='skill_rule'），不进 _RECALL_KINDS 通用召回（corpus 同款隔离）；算力卫生
keep_alive=0；organ 回环与 dsh 标题请求豁免注入。游标/计数表建在本模块专属表
（单行字面量 DDL，幂等）。
"""
import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from . import config, memory_db, memory_embed

logger = logging.getLogger("moray.skill_extractor")


class _SkillFileHandler(logging.Handler):
    """logs/skill_extractor.log（根 logger 只放 ERROR 的教训：模块自挂 handler）。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            base = Path(__file__).resolve().parents[2] / "logs"
            base.mkdir(parents=True, exist_ok=True)
            with open(base / "skill_extractor.log", "a", encoding="utf-8") as f:
                f.write(self.format(record) + "\n")
        except Exception:  # noqa: BLE001 - 日志面缺失不扰主链路
            pass


def _install_skill_logger() -> None:
    if any(isinstance(h, _SkillFileHandler) for h in logger.handlers):
        return
    logger.setLevel(logging.INFO)
    logger.addHandler(_SkillFileHandler())
    _console = logging.StreamHandler()
    _console.setLevel(logging.WARNING)
    _console.setFormatter(logging.Formatter("[skill_extractor] %(message)s"))
    logger.addHandler(_console)


_install_skill_logger()

SOURCE = "skill_extractor"
_KIND = "skill_rule"
_TAGS = ("skill_rule", "auto")

_SCAN_BATCH_LIMIT = 100
_EXPERIENCE_MAX_CHARS = 800

_RULE_MIN_CHARS = 8
_RULE_MAX_CHARS = 60
_HAS_WORD = re.compile(r"[\u4e00-\u9fffA-Za-z]")
_RULE_JUNK_PAT = re.compile(
    r"无法提炼|无法生成|没有(可提炼|通用|规则)|没有值得|无法总结|信息不足|无通用规则")

_FAILURE_TAG = re.compile(r"(^|,)failure(,|$)")
_NIGHT_FAIL_MARK = "【夜班任务失败】"
_ERROR_TYPE_PAT = re.compile(r'"error_type"\s*:\s*"')
_EXIT_CODE_PAT = re.compile(r"退出码[：:]\s*(\d+)")

_SIM_SKIP = 0.90
_SIM_REINFORCE = 0.70
_REINFORCE_STEP = 0.05
_REINFORCE_CAP = 0.95

_INJECTION_HEADER = "【系统经验拦截】根据你曾经踩过的坑，本次执行必须遵守以下规则："

# 单行字面量 DDL（Mimosa 红线：execute 的 SQL 必须是单行字面量 + 全量参数化）
_STATS_DDL = "CREATE TABLE IF NOT EXISTS skill_rule_stats (rule_id TEXT PRIMARY KEY, injections INTEGER NOT NULL DEFAULT 0, fail_streak INTEGER NOT NULL DEFAULT 0, last_injected_at TEXT NOT NULL DEFAULT '', last_outcome_at TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))"
_STATE_DDL = "CREATE TABLE IF NOT EXISTS skill_extractor_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"

_CK_KEY = "scan_rowid"

_SYSTEM_PROMPT = (
    "你是一个经验规则提炼器。分析下面的失败经验，提炼出最多 3 条通用的、可执行的"
    "系统指令规则（每条不超过 50 字，用祈使句），用于避免未来犯同类错误。\n"
    "规则：\n"
    "1. 只基于经验文本提炼，禁止推测、禁止编造。\n"
    "2. 每条必须是一句可执行的指令，例如：在执行 Windows 脚本前，先检查当前目录是否包含空格。\n"
    "3. 一次性的、只对这一个案例有效的结论不配成为规则；无法提炼出通用规则时输出 []。\n"
    "4. 只输出一个 JSON 字符串数组，不要输出任何解释或 Markdown 代码块。"
)


def _ensure_tables(conn) -> None:
    """幂等建表（DDL 单行字面量内联，零变量拼接，Mimosa 红线零风险）。"""
    conn.execute("CREATE TABLE IF NOT EXISTS skill_rule_stats (rule_id TEXT PRIMARY KEY, injections INTEGER NOT NULL DEFAULT 0, fail_streak INTEGER NOT NULL DEFAULT 0, last_injected_at TEXT NOT NULL DEFAULT '', last_outcome_at TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
    conn.execute("CREATE TABLE IF NOT EXISTS skill_extractor_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    conn.commit()


def _get_checkpoint(conn) -> int:
    row = conn.execute(
        "SELECT value FROM skill_extractor_state WHERE key = ?", (_CK_KEY,)).fetchone()
    try:
        return int(row["value"] or 0)
    except (TypeError, ValueError):
        return 0


def _set_checkpoint(conn, ck: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO skill_extractor_state (key, value) VALUES (?, ?)",
        (_CK_KEY, str(ck)))
    conn.commit()


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _is_failed_record(tags: str, content: str) -> bool:
    """task_result 失败判定：tags failure / 夜班失败标题 / 结果 JSON error_type / 退出码>0。"""
    t = str(tags or "")
    c = str(content or "")
    if _FAILURE_TAG.search(t):
        return True
    if _NIGHT_FAIL_MARK in c:
        return True
    if _ERROR_TYPE_PAT.search(c):
        return True
    m = _EXIT_CODE_PAT.search(c)
    return bool(m and int(m.group(1)) > 0)


def _call_extract_model(experience_text: str) -> str:
    """失败经验文本 → 小模型原始输出（/api/chat ollama 协议；keep_alive=0 算力卫生）。"""
    url = str(config.OLLAMA_BASE_URL or "").rstrip("/") + "/api/chat"
    payload = {
        "model": config.MEMORY_EXTRACT_MODEL,
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 300},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": experience_text},
        ],
    }
    timeout = float(getattr(config, "SKILL_EXTRACT_TIMEOUT", 120.0))
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, json=payload)
    if r.status_code != 200:
        raise RuntimeError("skill extract model HTTP %s" % r.status_code)
    return str(((r.json() or {}).get("message") or {}).get("content") or "")


def _parse_rules(raw: str) -> list:
    """模型输出 → 清洗后规则数组（最多 3 条）：JSON 解析（裸数组/围栏/散文截取兜底）
    + 逐条护栏（8~60 字、含 CJK/字母、垃圾话术弃）。"""
    t = str(raw or "").strip()
    if not t:
        return []
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t).strip()
    data = None
    try:
        data = json.loads(t)
    except Exception:  # noqa: BLE001 - 散文包数组截取兜底
        s, e = t.find("["), t.rfind("]")
        if s != -1 and e > s:
            try:
                data = json.loads(t[s:e + 1])
            except Exception:  # noqa: BLE001
                return []
    if isinstance(data, dict):
        data = data.get("rules") or data.get("memories") or []
    if not isinstance(data, list):
        return []
    rules = []
    for item in data:
        if isinstance(item, dict):
            item = item.get("rule") or item.get("content") or item.get("text") or ""
        s = str(item or "").strip().strip('"')
        if not (_RULE_MIN_CHARS <= len(s) <= _RULE_MAX_CHARS):
            continue
        if not _HAS_WORD.search(s):
            continue
        if _RULE_JUNK_PAT.search(s):
            continue
        rules.append(s)
        if len(rules) >= 3:
            break
    return rules


def _get_confidence(rule_id: str) -> float:
    """规则置信度真身 = memory_bank.weight（缺行回落初始值）。"""
    try:
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT weight FROM memory_bank WHERE id = ?", (rule_id,)).fetchone()
            w = row["weight"] if row is not None else None
            return float(w) if w is not None else float(config.SKILL_RULE_INIT_CONFIDENCE)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - fail-open 回初始值
        return float(config.SKILL_RULE_INIT_CONFIDENCE)


def _update_rule_confidence(rule_id: str, new_conf: float, conn=None) -> None:
    """置信度唯一写点：UPDATE memory_bank.weight + updated_at。conn 由调用方传入时
    复用外层连接（嵌套开第二连接会撞扫描事务的写锁——真机实测 database is locked）。"""
    if conn is not None:
        conn.execute(
            "UPDATE memory_bank SET weight = ?, updated_at = ? WHERE id = ?",
            (float(new_conf), _ts(), rule_id))
        return
    c2 = memory_db.connect()
    try:
        c2.execute(
            "UPDATE memory_bank SET weight = ?, updated_at = ? WHERE id = ?",
            (float(new_conf), _ts(), rule_id))
        c2.commit()
    finally:
        c2.close()


def _best_rule_match(vec) -> tuple:
    """规则向量 → (best_id, best_cos)：skill_rule 分区 KNN 候选 + 回表真余弦取最优
    （vec0 换算分不作数，cosine 现算——memory_extractor 铁律）。向量面异常返回
    ("", 0.0)：调用方 fail-open 直接入库（insert_memory 精确判重兜底）。"""
    cand_ids = []
    conn = None
    try:
        for mid, _s in memory_embed.search_similar(vec, k=12, kind=_KIND):
            cand_ids.append(mid)
        conn = memory_db.connect()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("KNN 候选检索/连接异常：%s: %s", type(e).__name__, str(e)[:160])
        return "", 0.0
    best_id, best_cos = "", 0.0
    try:
        _ensure_tables(conn)
        for mid in dict.fromkeys(cand_ids):
            row = conn.execute(
                "SELECT id, weight, embedding FROM memory_bank WHERE id = ?", (mid,)).fetchone()
            if row is None or not row["embedding"]:
                continue
            stored = memory_embed.unpack_vector(row["embedding"])
            if not stored:
                continue
            c = memory_embed.cosine(vec, stored)
            if c > best_cos:
                best_id, best_cos = row["id"], c
    finally:
        conn.close()
    return best_id, best_cos


def _insert(content: str, session_id: str) -> tuple:
    """统一走 memory_routes.insert_memory（唯一 INSERT 汇聚点）。lazy import 防循环。"""
    from .memory_routes import insert_memory
    return insert_memory(content, kind=_KIND, tags=list(_TAGS), source=SOURCE,
                         session_id=session_id,
                         weight=float(config.SKILL_RULE_INIT_CONFIDENCE))


def _store_rule(rule: str, session_id: str) -> dict:
    """单条规则 → skill_rule 分区 KNN 去重三档 → 落库。返回动作明细（测试消费）。

    三档：cos>0.9 重复跳过；0.7~0.9 近似教训再现 → 置信度强化 +0.05（上限 0.95）；
    <0.7 新规则 insert_memory（weight=初始置信度）。"""
    vec = None
    try:
        vec = memory_embed.get_embedding(rule)
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("规则嵌入异常：%s: %s", type(e).__name__, str(e)[:160])
    if vec:
        best_id, best_cos = _best_rule_match(vec)
        if best_id and best_cos > _SIM_SKIP:
            logger.info("去重命中跳过（cos=%.3f id=%s）：%s", best_cos, best_id[:8], rule[:50])
            return {"action": "skipped_duplicate", "cos": round(best_cos, 4), "similar_id": best_id}
        if best_id and best_cos >= _SIM_REINFORCE:
            new_conf = min(_REINFORCE_CAP, _get_confidence(best_id) + _REINFORCE_STEP)
            _update_rule_confidence(best_id, new_conf)
            logger.info("近似教训再现：置信度强化 → %.2f（cos=%.3f id=%s）", new_conf, best_cos, best_id[:8])
            return {"action": "reinforced", "cos": round(best_cos, 4), "similar_id": best_id, "confidence": round(new_conf, 4)}
    mem_id, err = _insert(rule, session_id)
    action = "inserted" if mem_id else ("rejected" if err.startswith("写入校验拒绝：") else "error")
    if err and action == "error":
        logger.warning("落库失败：%s", str(err)[:160])
    return {"action": action, "rule_id": mem_id, "confidence": float(config.SKILL_RULE_INIT_CONFIDENCE),
            "error": str(err)[:160] if err else ""}


# ---- 注入匹配（llm_proxy 消费入口） ----

def render_injection_text(rules: list) -> str:
    """规则数组 → 【系统经验拦截】强制指令文本（llm_proxy 插 system 消息用）。"""
    lines = [_INJECTION_HEADER]
    for i, r in enumerate(rules, 1):
        lines.append("%d. %s" % (i, str(r.get("rule") or "")))
    return "\n".join(lines)


def match_rules_for_task(task_text: str) -> list:
    """任务描述 → 相似度>0.75 的活跃规则 top-3（不记账）。纯同步函数（to_thread 调用）。"""
    task_text = str(task_text or "").strip()
    if len(task_text) < 4:
        return []
    try:
        vec = memory_embed.get_embedding(task_text)
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("任务嵌入异常：%s: %s", type(e).__name__, str(e)[:160])
        return []
    if not vec:
        return []
    cand_ids = []
    try:
        for mid, _s in memory_embed.search_similar(vec, k=12, kind=_KIND):
            cand_ids.append(mid)
        conn = memory_db.connect()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("规则候选检索异常：%s: %s", type(e).__name__, str(e)[:160])
        return []
    matched = []
    try:
        _ensure_tables(conn)
        min_conf = float(config.SKILL_RULE_MIN_CONFIDENCE)
        for mid in dict.fromkeys(cand_ids):
            row = conn.execute(
                "SELECT id, content, weight, embedding FROM memory_bank WHERE id = ? AND kind = ?",
                (mid, _KIND)).fetchone()
            if row is None:
                continue
            conf = float(row["weight"] if row["weight"] is not None else config.SKILL_RULE_INIT_CONFIDENCE)
            stats = conn.execute(
                "SELECT archived FROM skill_rule_stats WHERE rule_id = ?", (mid,)).fetchone()
            if stats is not None and int(stats["archived"] or 0) == 1:
                continue  # 归档规则永不注入
            if conf < min_conf:
                continue
            stored = memory_embed.unpack_vector(row["embedding"])
            if not stored:
                continue
            c = memory_embed.cosine(vec, stored)
            if c > config.SKILL_RULE_SIM_THRESHOLD:
                matched.append({"rule_id": row["id"], "rule": str(row["content"]),
                                "confidence": round(conf, 4), "cosine": round(c, 4)})
        matched.sort(key=lambda x: x["cosine"], reverse=True)
    finally:
        conn.close()
    return matched[:int(config.SKILL_RULE_MAX_INJECT)]


def record_injection(rule_ids: list) -> None:
    """注入记账：injections +1、last_injected_at=now（幂等懒建 stats 行）。"""
    if not rule_ids:
        return
    now = _ts()
    conn = memory_db.connect()
    try:
        _ensure_tables(conn)
        for rid in rule_ids:
            # UPDATE-first：命中行 +1，未命中（首次注入）插 injections=1——恒 +1，
            # 不像 "INSERT OR IGNORE(1) + UPDATE(+1)" 那样首次注入双计
            cur = conn.execute(
                "UPDATE skill_rule_stats SET injections = injections + 1, last_injected_at = ?, updated_at = ? WHERE rule_id = ?",
                (now, now, rid))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO skill_rule_stats (rule_id, injections, last_injected_at) VALUES (?, 1, ?)",
                    (rid, now))
        conn.commit()
        logger.info("注入记账 %d 条规则：%s", len(rule_ids),
                    "；".join(str(r)[:8] for r in rule_ids))
    finally:
        conn.close()


def match_and_record(task_text: str) -> list:
    """llm_proxy 唯一入口：匹配 + 注入记账二合一（在 to_thread 内跑）。"""
    rules = match_rules_for_task(task_text)
    if rules:
        try:
            record_injection([r["rule_id"] for r in rules])
        except Exception as e:  # noqa: BLE001 - 记账失败不影响注入
            logger.warning("注入记账异常：%s: %s", type(e).__name__, str(e)[:160])
    return rules


# ---- 生命周期（连败降信 / 归档） ----

#: 生命周期判定用的失败判据函数引用（成功=非失败；insight 不参与对账，防 reflection
#: 从 task_result 提炼的 insight 与原 task_result 双重计数）
_LIFECYCLE_KINDS = ("task_result",)


def _apply_outcome(conn, rule_id: str, failed: bool, occurred_at: str) -> None:
    """单条规则 × 单条结果记录对账：失败 fail_streak+1（达限降信、<0.5 归档），
    成功清零连败。last_outcome_at 游标防同一条结果重复计数。"""
    row = conn.execute(
        "SELECT fail_streak, last_outcome_at FROM skill_rule_stats WHERE rule_id = ?",
        (rule_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO skill_rule_stats (rule_id, fail_streak, last_outcome_at) VALUES (?, 0, ?)",
            (rule_id, occurred_at))
        row = {"fail_streak": 0, "last_outcome_at": ""}
    if str(row["last_outcome_at"] or "") >= occurred_at:
        return  # 游标已越过该结果（幂等，防重复对账）
    if failed:
        streak = int(row["fail_streak"] or 0) + 1
        if streak >= int(config.SKILL_RULE_FAIL_STREAK_LIMIT):
            new_conf = max(0.0, _get_confidence(rule_id) - float(config.SKILL_RULE_CONF_PENALTY))
            _update_rule_confidence(rule_id, new_conf, conn=conn)
            streak = 0
            logger.warning("规则连败 %d 次降信 → %.2f（id=%s）",
                           config.SKILL_RULE_FAIL_STREAK_LIMIT, new_conf, rule_id[:8])
            if new_conf < float(config.SKILL_RULE_MIN_CONFIDENCE):
                conn.execute(
                    "UPDATE skill_rule_stats SET archived = 1 WHERE rule_id = ?", (rule_id,))
                logger.warning("规则置信度 %.2f < %.2f，已归档（id=%s）",
                               new_conf, config.SKILL_RULE_MIN_CONFIDENCE, rule_id[:8])
        last_streak = streak
    else:
        streak = 0
        last_streak = 0
    conn.execute(
        "UPDATE skill_rule_stats SET fail_streak = ?, last_outcome_at = ?, updated_at = ? WHERE rule_id = ?",
        (streak, occurred_at, _ts(), rule_id))
    logger.info("结果对账（rule=%s failed=%s occurred_at=%s streak=%d）",
                rule_id[:8], failed, occurred_at, last_streak)


def _rules_needing_outcome(conn, occurred_at: str) -> list:
    """在结果记录发生前被注入过、且游标未越过该结果的所有活跃规则 id。"""
    rows = conn.execute(
        "SELECT rule_id FROM skill_rule_stats WHERE archived = 0 AND last_injected_at != '' "
        "AND last_injected_at < ? AND last_outcome_at < ?",
        (occurred_at, occurred_at)).fetchall()
    return [r["rule_id"] for r in rows]


def scan_once() -> dict:
    """轮询单轮主体（线程内执行）：checkpoint(rowid 游标) → 新 insight/失败 task_result
    提炼规则；task_result 全量做生命周期对账。每行独立 try/except（毒丸行不阻塞队列），
    checkpoint 恒前移。首次运行（无 checkpoint）冷启动=只从当前最大 rowid 起监听。"""
    stats = {"scanned": 0, "extracted": [], "accounted": 0, "error": ""}
    conn = memory_db.connect()
    try:
        _ensure_tables(conn)
        ck = _get_checkpoint(conn)
        if ck == 0:
            row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM memory_bank").fetchone()
            max_rowid = int(row[0] or 0)
            _set_checkpoint(conn, max_rowid)
            stats["cold_start_rowid"] = max_rowid
            logger.info("冷启动：checkpoint 初始化到 rowid=%d（只监听此后新增记录）", max_rowid)
            return stats
        rows = conn.execute(
            "SELECT rowid, id, kind, tags, content, created_at, source, session_id FROM memory_bank "
            "WHERE kind IN ('insight', 'task_result') AND rowid > ? ORDER BY rowid ASC LIMIT ?",
            (ck, _SCAN_BATCH_LIMIT)).fetchall()
        stats["scanned"] = len(rows)
        new_ck = ck
        for row in rows:
            try:
                kind = str(row["kind"] or "")
                content = str(row["content"] or "")
                tags = str(row["tags"] or "")
                created_at = str(row["created_at"] or "")
                failed = _is_failed_record(tags, content)
                if kind == "task_result":
                    _lifecycle_account(conn, row["id"], failed, created_at)
                    stats["accounted"] += 1
                if kind == "insight" or failed:
                    experience = content[:_EXPERIENCE_MAX_CHARS]
                    raw = _call_extract_model(experience)
                    rules = _parse_rules(raw)
                    for rule in rules:
                        detail = _store_rule(rule, str(row["session_id"] or "default"))
                        detail["source_record_id"] = row["id"]
                        stats["extracted"].append(detail)
                    logger.info("经验提炼（kind=%s id=%s 失败=%s）：%d 条规则 | %s",
                                kind, row["id"][:8], failed, len(rules),
                                "；".join(rules[:2]))
            except Exception as e:  # noqa: BLE001 - 毒丸行不阻塞队列
                logger.warning("单条记录处理异常（id=%s）：%s: %s",
                               str(row["id"])[:8], type(e).__name__, str(e)[:160])
            new_ck = int(row["rowid"])
        if rows:
            _set_checkpoint(conn, new_ck)
        return stats
    finally:
        conn.close()


def _lifecycle_account(conn, memory_id: str, failed: bool, occurred_at: str) -> None:
    """一条 task_result 结果 → 对所有"注入过且游标未越过"的活跃规则做对账。"""
    try:
        for rid in _rules_needing_outcome(conn, occurred_at):
            _apply_outcome(conn, rid, failed, occurred_at)
    except Exception as e:  # noqa: BLE001 - 生命周期面失败不影响提炼
        logger.warning("生命周期对账异常（mem=%s）：%s: %s",
                       str(memory_id)[:8], type(e).__name__, str(e)[:160])


async def skill_extract_loop() -> None:
    """轮询主环（lifespan 挂载的独立 task；总闸关时秒退）。"""
    if not config.SKILL_EXTRACT_ENABLED:
        logger.warning("SKILL_EXTRACT_ENABLED=false，经验泛化引擎不启动")
        return
    conn = memory_db.connect()
    _ensure_tables(conn)
    conn.close()
    await asyncio.to_thread(scan_once)  # 起手先跑一轮（含冷启动 checkpoint 初始化）
    interval = float(getattr(config, "SKILL_EXTRACT_INTERVAL_SEC", 60.0))
    logger.warning("经验泛化引擎启动（interval=%ss, model=%s）", interval, config.MEMORY_EXTRACT_MODEL)
    while True:
        await asyncio.sleep(interval)
        try:
            s = await asyncio.to_thread(scan_once)
            if s.get("extracted") or s.get("accounted"):
                logger.info("轮询完成：%d 条新记录，提炼 %d 条规则，对账 %d 条结果",
                            s.get("scanned", 0), len(s.get("extracted") or []), s.get("accounted", 0))
        except Exception as e:  # noqa: BLE001 - 单轮失败下轮照常
            logger.warning("轮询异常（下轮照常）：%s: %s", type(e).__name__, str(e)[:160])
