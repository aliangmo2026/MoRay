"""数字分身底座 v1（2026-10-23）→ v2 混合检索（2026-10-24）：语料切块摄入 + corpus 专属检索。

职责（预研定案 2026-10-23）：
- 长文本/外部长文档自动切块 → 逐块落 memory_bank（kind='corpus'，source='digital_twin'），
  与用户记忆（fact/preference/insight/task_result）靠 source×kind 矩阵隔离；
- 专属检索端点只查 kind='corpus'，memory_routes 原有召回逻辑零改动
  （其 recall 白名单不含 corpus，主聊天召回零污染）。
- v2（2026-10-24）：/api/twin/recall 默认 hybrid = FTS + 向量 RRF(k=60) 融合
  （memory_routes.recall hybrid 同款；向量通道 fail-open 降级纯 FTS），
  并作为 llm_proxy /api/llm/chat 的 twin RAG 注入检索核（MORAY_TWIN_RAG 总闸）。

硬约束：
- 复用 memory_routes.insert_memory 唯一 INSERT 汇聚点（判重/写入校验矩阵/拒绝留痕全继承）；
- 复用 memory_routes._fts_expr 双模式安全化（recall AND → recall_or 兜底，同款两段式）；
- SQL 全部参数化：语句为 execute 调用处的内联字面量（零变量承载、零拼接、零格式化），
  外部输入只经 ? 占位符绑定；MATCH 表达式先做非法字符剔除重建才允许成为绑定参数。

已知语义边界（insert_memory 既有判重语义，非本模块引入）：
- 判重键 = (source, 归一化内容全等)，不含 tags——两份不同文档若含相同块，第二份该块
  命中判重返回第一份的 memory_id（tags 归属第一份），按幂等语义接受。
"""
import json
import logging
import re

from fastapi import APIRouter, Request

from . import memory_db, memory_embed
from .memory_routes import _bad, _fts_expr, _ok, insert_memory

logger = logging.getLogger("moray.twin")

#: 切块参数：每块字符数 / 块间重叠字符数（重叠防切断语义）
CHUNK_SIZE = 1000
OVERLAP = 100

#: MATCH 绑定参数非法字符剔除：保留词字符（含中文）、空白、双引号、括号，
#: 其余（分号/注释符/点号等一切非白名单字符）全部删除。
_STRIP_BAD_RE = re.compile(r'[^\w"() \t]')

router = APIRouter()


def _safe_match_expr(query: str, mode: str) -> str:
    """query → 经非法字符剔除重建的 FTS5 MATCH 绑定参数（不合法返回空串）。

    _fts_expr 已对每段检索词逐 gram 加引号转义（FTS5 保留字防误解析 + 嵌入引号
    doubling）；此处再做产物字符剔除重建，通过后的字符串仅作为 ? 绑定参数
    传入 _fetch_rows，绝不拼接进 SQL 文本。
    """
    expr = _STRIP_BAD_RE.sub("", str(_fts_expr(query, mode=mode) or ""))
    if not expr or not expr.strip():
        return ""
    return expr


def _fetch_rows(conn, expr: str, lim: int, source: str = ""):
    """参数化执行 corpus 检索：SQL 为内联字面量（零拼接），expr/lim/source 只经 ? 绑定。

    [P0可用性修复 2026-10-28] 新增 source 过滤（按文档隔离）：非空时用 source = ? 精确
    匹配命名空间化文档名（'digital_twin:<文档名>'）；为空保持原行为（全 corpus）。
    """
    if source:
        return conn.execute(
            "SELECT m.id, m.kind, m.content, m.tags, m.source, m.entity_id, m.session_id, m.created_at, bm25(memory_fts) AS bm25_rank FROM memory_fts JOIN memory_bank m ON m.id = memory_fts.memory_id WHERE memory_fts MATCH ? AND m.kind = 'corpus' AND m.source = ? ORDER BY bm25_rank ASC LIMIT ?",
            (expr, source, lim)).fetchall()
    return conn.execute(
        "SELECT m.id, m.kind, m.content, m.tags, m.source, m.entity_id, m.session_id, m.created_at, bm25(memory_fts) AS bm25_rank FROM memory_fts JOIN memory_bank m ON m.id = memory_fts.memory_id WHERE memory_fts MATCH ? AND m.kind = 'corpus' ORDER BY bm25_rank ASC LIMIT ?",
        (expr, lim)).fetchall()


#: RRF 融合常数（与 memory_routes.recall hybrid 同款 k=60：单通道第 1 名贡献
#: 1/61，双通道同命中 ≈ 2/61 级别，双通道命中天然压过单通道）
_RRF_K = 60.0


def _hybrid_recall(query: str, limit: int = 3, source: str = "") -> list:
    """FTS + 向量双通道召回 kind='corpus' 块，RRF(k=60) 融合（[twin RAG 2026-10-24]）。

    - FTS 通道：trigram ≥3 字、AND 查空 → recall_or 兜底（同 v1 两段式），bm25 序；
    - 向量通道：query 嵌入 → memory_embed.search_similar（vec0 KNN / 纯 Python
      余弦兜底）→ 回表过滤 kind='corpus'；文档不贬值，两通道均无时间衰减；
    - 融合：RRF 逆排名融合（memory_routes.recall hybrid 同款 k=60），score=融合分；
    - fail-open：向量通道任何异常/无嵌入环境只降级纯 FTS（绝不抛、绝不拖垮 FTS 面）。
    [P0可用性修复 2026-10-28] 新增 source 过滤（按文档隔离）：非空时两通道都只取
    该命名空间化文档名的块；为空保持原行为（全 corpus，调用方零适配）。
    [P0可用性修复 2026-10-28] 向量通道熔断：memory_embed 冷却期内 get_embedding 直接
    返 None，此处自动跳过向量通道走纯 FTS（不再每条消息硬等一个注定超时的嵌入调用）。
    返回 items 形状与 v1 对齐（score=RRF 融合分），fts_score/vec_score/matches
    为纯增量观测字段（matches ⊆ ["fts", "vec"]）。本函数只 SELECT，调用方
    （recall 端点 / llm_proxy 注入段）各自兜底连接级异常。
    """
    src = str(source or "").strip()
    conn = memory_db.connect()
    try:
        # ---- FTS 通道（bm25 相关度序）----
        expr = _safe_match_expr(query, "recall")
        fts_rows = []
        if expr:
            fts_rows = _fetch_rows(conn, expr, limit, src)
            if not fts_rows:
                fb_expr = _safe_match_expr(query, "recall_or")
                if fb_expr and fb_expr != expr:
                    fts_rows = _fetch_rows(conn, fb_expr, limit, src)
        fts_hits = [(r["id"], abs(float(r["bm25_rank"]))) for r in fts_rows]

        # ---- 向量通道（相关度降序；任何失败降级为空，不影响 FTS 面）----
        # [vec kind 分区 2026-10-25] kind='corpus' 下推进 KNN 真过滤（先滤后取
        # top-k，语料不再被非语料挤出榜），k 由 limit*3 收窄到 limit*2；
        # 回表 corpus 过滤保留作双保险。
        vec_hits = []
        try:
            q_vec = memory_embed.get_embedding(query)
            if q_vec:
                cand = dict(memory_embed.search_similar(
                    q_vec, k=max(limit * 2, 10), kind="corpus"))
                if cand:
                    # [P1-5 修复 2026-11-01] 回表改按 top-k id 定点取：json_each 绑定，
                    # SQL 保持字面量零拼接（Mimosa 纪律），行数 ≤ k（=limit*2），
                    # 不再全表扫 corpus——10 万条量级下这是 O(N)→O(k) 的关键收敛。
                    # [P0可用性修复 2026-10-28] 按文档隔离：非空 source 时只留该文档的块
                    if src:
                        vrows = conn.execute(
                            "SELECT id FROM memory_bank WHERE kind = 'corpus' AND source = ?"
                            " AND id IN (SELECT value FROM json_each(?))",
                            (src, json.dumps(list(cand.keys())))).fetchall()
                    else:
                        vrows = conn.execute(
                            "SELECT id FROM memory_bank WHERE kind = 'corpus'"
                            " AND id IN (SELECT value FROM json_each(?))",
                            (json.dumps(list(cand.keys())),)).fetchall()
                    corpus_ids = {r["id"] for r in vrows}
                    vec_hits = [(mid, sc) for mid, sc in cand.items() if mid in corpus_ids]
                    vec_hits.sort(key=lambda t: t[1], reverse=True)
        except Exception as e:  # noqa: BLE001 - 向量通道失败只降级
            logger.warning("[twin] 向量通道降级（不影响 FTS 结果）：%s: %s",
                           type(e).__name__, str(e)[:160])
            vec_hits = []

        # ---- RRF 融合（k=60，逆排名：两通道名次贡献相加）----
        fused = {}
        for rank, (mid, _s) in enumerate(fts_hits[:limit], 1):
            fused[mid] = fused.get(mid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, (mid, _s) in enumerate(vec_hits[:limit], 1):
            fused[mid] = fused.get(mid, 0.0) + 1.0 / (_RRF_K + rank)
        if not fused:
            return []
        ordered = sorted(fused.items(), key=lambda t: t[1], reverse=True)[:limit]

        # ---- 回表取全字段（字面量 SQL + kind 过滤 + Python id 过滤，零 IN 拼接）----
        want = {mid for mid, _s in ordered}
        by_id = {}
        if want:
            rows = conn.execute(
                "SELECT id, kind, content, tags, source, entity_id, session_id, created_at "
                "FROM memory_bank WHERE kind = 'corpus'").fetchall()
            by_id = {r["id"]: r for r in rows if r["id"] in want}

        fts_map = dict(fts_hits[:limit])
        vec_map = dict(vec_hits[:limit])
        items = []
        for mid, rrf in ordered:
            row = by_id.get(mid)
            if row is None:
                continue  # 融合窗口内行被并发删除：静默丢弃（只读面，无补偿动作）
            matches = []
            fts_score = None
            vec_score = None
            if mid in fts_map:
                matches.append("fts")
                fts_score = fts_map[mid]
            if mid in vec_map:
                matches.append("vec")
                vec_score = vec_map[mid]
            items.append({
                "id": row["id"],
                "kind": row["kind"],
                "content": row["content"],
                "tags": [t for t in (row["tags"] or "").split(",") if t],
                "source": row["source"],
                "entity_id": row["entity_id"],
                "session_id": row["session_id"],
                "score": round(rrf, 6),
                "created_at": row["created_at"],
                "fts_score": None if fts_score is None else round(fts_score, 6),
                "vec_score": None if vec_score is None else round(vec_score, 6),
                "matches": matches,
            })
        return items
    finally:
        conn.close()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list:
    """长文本按字符数切块。

    尾块若长度 ≤ overlap 则丢弃：此时前一块右端已越过文本尾，尾块必是前一块的
    纯后缀，留着就是重复块。首块不足 chunk_size 时整体作为单块返回。
    """
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("需 0 <= overlap < chunk_size")
    s = str(text or "")
    n = len(s)
    if n == 0:
        return []
    if n <= chunk_size:
        return [s]
    step = chunk_size - overlap
    chunks = []
    pos = 0
    while pos < n:
        chunks.append(s[pos:pos + chunk_size])
        pos += step
    if len(chunks) > 1 and len(chunks[-1]) <= overlap:
        chunks.pop()
    return chunks


def _doc_source(name: str) -> str:
    """[P0可用性修复 2026-10-28] 文档名 → 命名空间化 source。

    此前 source 硬编码 'digital_twin'，所有文档混进同一语料池、无法按文档隔离检索
    （真机实测：投喂 150 块后按文档名查 0 行，且新文档被旧语料的无关结果淹没）。
    改为 'digital_twin:<文档名>'：矩阵按命名空间根键校验（memory_routes 同批放行），
    检索可按 source 前缀过滤；无名/非法名回退裸 'digital_twin'（兼容存量行）。
    """
    clean = str(name or "").strip()
    if not clean:
        return "digital_twin"
    # source 列宽 32（memory_routes 截断口径）：根键 12 + 冒号 1 → 子标识最多 19
    return ("digital_twin:" + clean)[:32]


def chunk_and_insert(text: str, source_name: str) -> dict:
    """切块 → 逐块 insert_memory(kind='corpus', source='digital_twin:<文档名>', tags=[source_name])。

    [P0可用性修复 2026-10-28] source 由硬编码 'digital_twin' 改为命名空间化文档名，
    实现按文档隔离入库；检索侧 _fetch_rows / _hybrid_recall 同批支持 source 过滤。

    返回 {"name", "chunks", "inserted", "failed", "ids", "errors"}：
    - chunks  = 切出的块数；inserted = 成功入库块数（含判重幂等命中的已有 id）；
    - failed/errors = 单块落库失败计数与原因（不中断后续块，批内隔离）。
    """
    name = str(source_name or "").strip() or "unnamed"
    src = _doc_source(name)
    chunks = chunk_text(text)
    ids = []
    errors = []
    failed = 0
    for c in chunks:
        mem_id, err = insert_memory(
            c, kind="corpus", source=src, tags=[name])
        if err:
            failed += 1
            errors.append(str(err)[:200])
        else:
            ids.append(mem_id)
    return {"name": name, "chunks": len(chunks), "inserted": len(ids),
            "failed": failed, "ids": ids, "errors": errors}


@router.post("/api/twin/ingest")
async def twin_ingest(request: Request):
    """语料摄入：POST {"text": "...", "name": "文档名"} → 切块入库，返回成功插入块数。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    text = str(body.get("text") or "")
    if not text.strip():
        return _bad("bad_request", "text 必填且非空", 400)
    name = str(body.get("name") or "").strip()

    result = chunk_and_insert(text, name)
    return _ok(result)


@router.post("/api/twin/recall")
async def twin_recall(request: Request):
    """corpus 专属检索（[twin RAG 2026-10-24] 默认 hybrid）。

    body {"query": "...", "limit": 3, "mode": "hybrid" | "fts", "source": ""}：
    - hybrid（默认）：FTS 通道 + 向量通道 RRF(k=60) 融合，top-N 返回。文档语料
      不贬值，两通道均无时间衰减。向量通道任何异常/无嵌入环境自动降级纯 FTS
      （fail-open，items 契约不变，只是 matches 里没有 "vec"）。
    - mode="fts"：v1 纯 FTS 路径原样保留（bm25 排序、无时间衰减、无增量字段），
      v1 调用方零适配兼容。
    - [P0可用性修复 2026-10-28] source：按文档隔离检索（命名空间化文档名
      'digital_twin:<文档名>'，传裸文档名亦可自动补前缀）；为空 = 全 corpus（原行为）。
    默认 limit=3（聊天注入 3 块足够；body 可调 1..50）。检索白名单恒
    kind='corpus'，与主聊天召回（白名单不含 corpus）结构互斥零污染。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    query = str(body.get("query") or "").strip()
    if len(query) < 3:
        return _bad("bad_request", "query 长度必须 ≥ 3（trigram 下限）", 400)
    try:
        limit = int(body.get("limit") or 3)
    except (TypeError, ValueError):
        limit = 3
    limit = max(1, min(limit, 50))
    mode = str(body.get("mode") or "hybrid").strip().lower()
    if mode not in ("hybrid", "fts"):
        mode = "hybrid"
    # [P0可用性修复 2026-10-28] source 归一：裸文档名自动补命名空间前缀
    src = str(body.get("source") or "").strip()
    if src and not src.startswith("digital_twin"):
        src = _doc_source(src)

    if mode == "hybrid":
        try:
            items = _hybrid_recall(query, limit, src)
        except Exception as e:  # noqa: BLE001
            return _bad("db_error", str(e)[:200], 500)
        return _ok({"items": items, "mode": "hybrid", "query": query, "source": src})

    # ---- mode="fts"：v1 纯 FTS 路径（原样保留） ----
    match_expr = _safe_match_expr(query, "recall")
    if not match_expr:
        return _bad("bad_request", "query 无有效检索词（每个词需 ≥3 字符，trigram 下限）", 400)
    conn = memory_db.connect()
    try:
        rows = _fetch_rows(conn, match_expr, limit, src)
        if not rows:
            # 两段式兜底同 memory_recall（P4 切词修复）：AND 零命中 → 词间 OR 重查一次
            fallback_expr = _safe_match_expr(query, "recall_or")
            if fallback_expr and fallback_expr != match_expr:
                rows = _fetch_rows(conn, fallback_expr, limit, src)
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    items = []
    for row in rows:
        items.append({
            "id": row["id"],
            "kind": row["kind"],
            "content": row["content"],
            "tags": [t for t in (row["tags"] or "").split(",") if t],
            "source": row["source"],
            "entity_id": row["entity_id"],
            "session_id": row["session_id"],
            "score": round(abs(float(row["bm25_rank"])), 6),
            "created_at": row["created_at"],
        })
    return _ok({"items": items, "mode": "fts", "query": query, "source": src})
