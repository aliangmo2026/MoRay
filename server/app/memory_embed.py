# -*- coding: utf-8 -*-
"""向量召回 v1（2026-10-24 深海阶段一）：memory_bank.embedding 的写入与检索执行层。

分工（与 memory_routes / memory_db 的边界）：
- 本模块 = embedding 的唯一写入/检索面。Ollama /api/embeddings 出 768 维向量
  （MORAY_EMBED_MODEL 默认 nomic-embed-text；维度 memory_db.EMBED_DIM 可环境变量
  覆盖），序列化约定 little-endian float32（struct '<{dim}f'）。
- 源数据真身 = memory_bank.embedding / embedding_model；vec0 虚表 memory_vec
  （memory_db 按需自动建）只做 KNN 索引，可随时删表重建。
- 写入路径：insert_memory 提交后 spawn_embed 后台 daemon 线程补嵌——嵌入走 HTTP
  （本地 Ollama 也可能被 chat 推理排队到秒级），绝不能同步等在事件循环里；
  线程内短事务（busy_timeout 5s）与主写者串行，失败仅记日志、行照常入库
  （fail-open：向量面缺失由 FTS 路兜底，backfill_embeddings 随时可补）。
- 检索路径：search_similar——VEC_AVAILABLE 时 vec0 KNN（L2 距离换算 1/(1+d)），
  否则纯 Python 余弦暴力扫（零 numpy 兜底，任务书要求的死胡同防护）。
- kind 白名单过滤由调用方（memory_routes.recall）回表执行，本模块只管向量面。

fail-open 总纪律：本模块任何失败都不允许影响记忆主链路（入库/删源/召回 FTS 面）。
"""
import logging
import math
import os
import struct
import threading
import time

import httpx

from . import config, memory_db

logger = logging.getLogger("moray.memory_embed")

#: 嵌入模型（任务书点名 nomic-embed-text；768 维）
EMBED_MODEL = os.environ.get("MORAY_EMBED_MODEL", "").strip() or "nomic-embed-text"
_EMBED_DIM = memory_db.EMBED_DIM
#: 单次嵌入超时（本地推理；冷启动模型装载可达秒级，20s 余量）
_EMBED_TIMEOUT = 20.0
#: 送嵌文本字符钳制（nomic ctx 8192 token；中文约 1.5 char/token，6000 字符安全）
_EMBED_TEXT_MAX = 6000

_STRUCT_FMT = "<%df" % _EMBED_DIM


def pack_vector(vec: list) -> bytes:
    """float 列表 → little-endian float32 BLOB（memory_bank.embedding 落盘形态）。"""
    return struct.pack(_STRUCT_FMT, *[float(x) for x in vec])


def unpack_vector(blob) -> list | None:
    """BLOB → float 列表；None/长度不符（换过模型维度）返回 None 调用方跳过。"""
    if not blob:
        return None
    try:
        vals = struct.unpack(_STRUCT_FMT, bytes(blob))
    except (struct.error, TypeError, ValueError):
        return None
    return [float(x) for x in vals]


def cosine(a: list, b: list) -> float:
    """纯 Python 余弦相似度（零 numpy 兜底路径的核）。零向量按 0 处理不抛。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def get_embedding(text: str) -> list | None:
    """文本 → 768 维向量；任何异常/非 200/维度不符/空文本一律 None（fail-open，
    绝不抛——调用方都在主链路上）。模型经 MORAY_EMBED_MODEL 可换。"""
    t = str(text or "").strip()
    if not t:
        return None
    url = str(config.OLLAMA_BASE_URL or "").rstrip("/") + "/api/embeddings"
    payload = {"model": EMBED_MODEL, "prompt": t[:_EMBED_TEXT_MAX]}
    try:
        with httpx.Client(timeout=_EMBED_TIMEOUT) as c:
            r = c.post(url, json=payload)
        if r.status_code != 200:
            logger.warning("[memory_embed] 嵌入 HTTP %s（model=%s url=%s）",
                           r.status_code, EMBED_MODEL, url)
            return None
        vec = (r.json() or {}).get("embedding")
        if not isinstance(vec, list) or len(vec) != _EMBED_DIM:
            got = len(vec) if isinstance(vec, list) else type(vec).__name__
            logger.warning("[memory_embed] 嵌入维度不符（got %s want %s）——模型与 "
                           "MORAY_EMBED_DIM 不匹配？", got, _EMBED_DIM)
            return None
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_embed] 嵌入调用异常：%s: %s",
                       type(e).__name__, str(e)[:160])
        return None


def index_memory(mem_id: str, text: str) -> dict:
    """同步嵌入并落库（回填脚本/测试用；在线路径走 spawn_embed）：
    UPDATE memory_bank.embedding + vec 表删旧插新，单事务。
    行已不存在（并发删除）→ 回滚并清 vec 残留，返回 ok=False。"""
    vec = get_embedding(text)
    if vec is None:
        return {"ok": False, "reason": "embedding_unavailable"}
    blob = pack_vector(vec)
    try:
        conn = memory_db.connect()
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[memory_embed] 连接异常：%s: %s",
                       type(e).__name__, str(e)[:160])
        return {"ok": False, "reason": str(e)[:200]}
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE memory_bank SET embedding = ?, embedding_model = ? WHERE id = ?",
            (blob, EMBED_MODEL, mem_id))
        if (cur.rowcount or 0) == 0:
            conn.rollback()  # 行已被并发删除：本次嵌入无处可挂，顺手清 vec 残留
            if memory_db.VEC_AVAILABLE:
                try:
                    conn.execute("BEGIN")
                    conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mem_id,))
                    conn.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[memory_embed] 清 vec 残留异常（mem_id=%s）：%s",
                                   mem_id, str(e)[:120])
            return {"ok": False, "reason": "row_gone"}
        if memory_db.VEC_AVAILABLE:
            conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mem_id,))
            conn.execute("INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                         (mem_id, blob))
        conn.commit()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001 - fail-open
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("[memory_embed] 索引写库异常（mem_id=%s）：%s: %s",
                       mem_id, type(e).__name__, str(e)[:160])
        return {"ok": False, "reason": str(e)[:200]}
    finally:
        conn.close()


# ---- 在线异步补嵌（insert_memory 调用；daemon 线程 + 在飞去重） ----

_embed_inflight = set()
_embed_lock = threading.Lock()


def spawn_embed(mem_id: str, text: str) -> None:
    """调度后台补嵌（fire-and-forget）：同 mem_id 在飞去重；线程异常只记日志。
    MORAY_EMBED_AUTO=0 时整体跳过（隔离测试/关停嵌入面的总闸；召回面由
    backfill_embeddings 补）。"""
    import os
    if os.environ.get("MORAY_EMBED_AUTO", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    mid = str(mem_id or "").strip()
    if not mid or not str(text or "").strip():
        return
    with _embed_lock:
        if mid in _embed_inflight:
            return
        _embed_inflight.add(mid)

    def _work() -> None:
        try:
            res = index_memory(mid, text)
            if not res.get("ok"):
                logger.warning("[memory_embed] 后台补嵌未成（mem_id=%s）：%s",
                               mid, res.get("reason"))
        except Exception as e:  # noqa: BLE001 - 线程内兜底，绝不留未捕获
            logger.warning("[memory_embed] 后台补嵌异常（mem_id=%s）：%s: %s",
                           mid, type(e).__name__, str(e)[:160])
        finally:
            with _embed_lock:
                _embed_inflight.discard(mid)

    try:
        threading.Thread(target=_work, name="moray-memory-embed", daemon=True).start()
    except Exception as e:  # noqa: BLE001 - 起线程失败只记日志（嵌入面缺失可回填补）
        with _embed_lock:
            _embed_inflight.discard(mid)
        logger.warning("[memory_embed] 起线程失败（mem_id=%s）：%s: %s",
                       mid, type(e).__name__, str(e)[:160])


def search_similar(query_vec: list, k: int = 10) -> list:
    """query 向量 → [(memory_id, 相关度分)]，分越大越相关（vec 路径 1/(1+L2)，
    兜底路径=余弦）。vec 可用走 KNN；任何环境异常降级纯 Python 暴力扫；
    全挂返回 []（调用方降级纯 FTS）。kind 白名单由调用方回表过滤。"""
    if not query_vec:
        return []
    if memory_db.VEC_AVAILABLE:
        try:
            conn = memory_db.connect()
            try:
                rows = conn.execute(
                    "SELECT memory_id, distance FROM memory_vec "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (pack_vector(query_vec), int(k)),
                ).fetchall()
            finally:
                conn.close()
            return [(r["memory_id"], 1.0 / (1.0 + float(r["distance"]))) for r in rows]
        except Exception as e:  # noqa: BLE001 - 降级暴力扫
            logger.warning("[memory_embed] vec KNN 失败降级暴力扫：%s: %s",
                           type(e).__name__, str(e)[:160])
    # 纯 Python 余弦兜底（零 numpy；量级 10^4 行内瞬时）
    try:
        conn = memory_db.connect()
        try:
            rows = conn.execute(
                "SELECT id, embedding FROM memory_bank WHERE embedding IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        scored = []
        for r in rows:
            v = unpack_vector(r["embedding"])
            if not v:
                continue
            scored.append((r["id"], cosine(query_vec, v)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:int(k)]
    except Exception as e:  # noqa: BLE001
        logger.warning("[memory_embed] 暴力扫描异常：%s: %s",
                       type(e).__name__, str(e)[:160])
        return []


def backfill_embeddings(batch_pause: float = 0.05) -> dict:
    """存量回填：embedding IS NULL 的行逐条补嵌（幂等，可重复跑）。
    CLI：python -c "from server.app import memory_embed; ..."；返回统计 dict。"""
    conn = memory_db.connect()
    try:
        rows = conn.execute(
            "SELECT id, content FROM memory_bank WHERE embedding IS NULL "
            "ORDER BY created_at ASC").fetchall()
    finally:
        conn.close()
    done = 0
    failed = 0
    for r in rows:
        res = index_memory(r["id"], r["content"])
        if res.get("ok"):
            done += 1
        else:
            failed += 1
        if batch_pause:
            time.sleep(batch_pause)
    stats = {"total": len(rows), "embedded": done, "failed": failed,
             "model": EMBED_MODEL, "dim": _EMBED_DIM}
    logger.warning("[memory_embed] 回填完成：%s", stats)
    return stats
