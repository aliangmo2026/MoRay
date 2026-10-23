"""数字分身底座 v1（2026-10-23）：语料切块摄入 + corpus 专属检索。

职责（预研定案 2026-10-23）：
- 长文本/外部长文档自动切块 → 逐块落 memory_bank（kind='corpus'，source='digital_twin'），
  与用户记忆（fact/preference/insight/task_result）靠 source×kind 矩阵隔离；
- 专属检索端点只查 kind='corpus'，memory_routes 原有召回逻辑零改动
  （其 recall 白名单不含 corpus，主聊天召回零污染）。

硬约束：
- 复用 memory_routes.insert_memory 唯一 INSERT 汇聚点（判重/写入校验矩阵/拒绝留痕全继承）；
- 复用 memory_routes._fts_expr 双模式安全化（recall AND → recall_or 兜底，同款两段式）；
- SQL 全部参数化：语句为 execute 调用处的内联字面量（零变量承载、零拼接、零格式化），
  外部输入只经 ? 占位符绑定；MATCH 表达式先做非法字符剔除重建才允许成为绑定参数。

已知语义边界（insert_memory 既有判重语义，非本模块引入）：
- 判重键 = (source, 归一化内容全等)，不含 tags——两份不同文档若含相同块，第二份该块
  命中判重返回第一份的 memory_id（tags 归属第一份），按幂等语义接受。
"""
import re

from fastapi import APIRouter, Request

from . import memory_db
from .memory_routes import _bad, _fts_expr, _ok, insert_memory

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


def _fetch_rows(conn, expr: str, lim: int):
    """参数化执行 corpus 检索：SQL 为内联字面量（零拼接），expr/lim 只经 ? 绑定。"""
    return conn.execute(
        "SELECT m.id, m.kind, m.content, m.tags, m.source, m.entity_id, m.session_id, m.created_at, bm25(memory_fts) AS bm25_rank FROM memory_fts JOIN memory_bank m ON m.id = memory_fts.memory_id WHERE memory_fts MATCH ? AND m.kind = 'corpus' ORDER BY bm25_rank ASC LIMIT ?",
        (expr, lim)).fetchall()


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


def chunk_and_insert(text: str, source_name: str) -> dict:
    """切块 → 逐块 insert_memory(kind='corpus', source='digital_twin', tags=[source_name])。

    返回 {"name", "chunks", "inserted", "failed", "ids", "errors"}：
    - chunks  = 切出的块数；inserted = 成功入库块数（含判重幂等命中的已有 id）；
    - failed/errors = 单块落库失败计数与原因（不中断后续块，批内隔离）。
    """
    name = str(source_name or "").strip() or "unnamed"
    chunks = chunk_text(text)
    ids = []
    errors = []
    failed = 0
    for c in chunks:
        mem_id, err = insert_memory(
            c, kind="corpus", source="digital_twin", tags=[name])
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
    """corpus 专属检索：POST {"query": "..."} → 只查 kind='corpus' 的 FTS 命中块。

    检索口径与 memory_recall 同构（trigram ≥3 字下限、AND 查空 → recall_or 兜底），
    区别仅在白名单：此处恒 kind='corpus'，不带时间衰减（文档语料不随时间贬值），
    按 bm25 相关度排序。items 字段形状与 /api/memory/recall 对齐，调用方零适配。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    query = str(body.get("query") or "").strip()
    if len(query) < 3:
        return _bad("bad_request", "query 长度必须 ≥ 3（trigram 下限）", 400)
    match_expr = _safe_match_expr(query, "recall")
    if not match_expr:
        return _bad("bad_request", "query 无有效检索词（每个词需 ≥3 字符，trigram 下限）", 400)
    try:
        limit = int(body.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 50))

    conn = memory_db.connect()
    try:
        rows = _fetch_rows(conn, match_expr, limit)
        if not rows:
            # 两段式兜底同 memory_recall（P4 切词修复）：AND 零命中 → 词间 OR 重查一次
            fallback_expr = _safe_match_expr(query, "recall_or")
            if fallback_expr and fallback_expr != match_expr:
                rows = _fetch_rows(conn, fallback_expr, limit)
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
    return _ok({"items": items, "query": query})
