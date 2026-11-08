"""MemoryBridge 记忆模块路由（2026-10-02 M1）

授权：M1 任务书（MemoryBridge MVP）。七端点：
  POST /api/memory/save        保存一条记忆（主表 + FTS 双写，单事务）
  POST /api/memory/recall      FTS5 trigram 召回 + bm25 相关性 × 时间衰减 × weight 重排
  POST /api/memory/forget      按 memory_id / query / tags 三选一删除（主表 + FTS 同步删）
  GET  /api/memory/list        分页列出（created_at DESC）
  GET  /api/memory/stats       各 kind 条数统计（只读；自省面板「记忆」卡，v1.1）
  GET  /api/memory/failures    失败档案（只读关键词候选；自省面板第七卡，v1）
  GET  /api/memory/rejections  写入拒绝（读 errors.log 尾部；自省面板第八卡，v1 批3）

风格：模块自包含（monitor_routes/self_routes 先例），手解 JSON + _ok/_bad 信封
（全后端零 Pydantic 的既有惯例）；事务范式照抄 crud.py（BEGIN → commit / rollback / close）。
FTS 表达式经 _fts_expr 双模式安全化（M3a）：recall 模式下含中文的词先做 3-gram
滑窗、各 gram 加引号后 OR 连接（词间 AND，解决中文整句被当短语要求连续子串的
漏召回）；forget 模式保持旧逐词短语 AND（删除面零扩大）。共同规则：逐 gram/词
加引号防 `"` `(` `)` `*` `^` `:` 等保留字语法错误，<3 字符词丢弃（trigram 下限，
2 字中文词属固有损失）。

[P4切词修复 2026-10-06] recall 端点改两段式：词间 AND 式查空 → 同一 SQL、词间改
OR（_fts_expr mode="recall_or"）兜底重查一次——口语整句常带零交集 token，AND
任一 token 零命中即整句全灭（P4 压测实锤）。只降级不加阈值，真零交集仍空 list。

时间衰减：score = |bm25| × exp(-age_days/decay_days) × weight，查询期现算（无后台任务）。
bm5 更负 = 更相关 → 取 abs 后越大越好，与衰减因子、weight 同向相乘后 DESC 排序。
"""
import json
import logging
import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import memory_db, memory_embed

logger = logging.getLogger("moray.memory")

router = APIRouter()


def _ok(data):
    return {"ok": True, "data": data}


def _bad(code, message, status=400):
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


#: [v1 写入校验 2026-10-13] source × kind 许可矩阵（实测 8 源；manual 另许 fact）
_SOURCE_KIND_MATRIX = {
    "manual": {"preference", "fact"},
    "reflection": {"insight"},
    "zcode_stream": {"task_result"},
    "laya_executor": {"task_result"},
    "task_flow": {"task_result"},
    "argus_research": {"task_result"},
    "self_model": {"self"},
    "self_reflection": {"self_observation"},
    "self_narrative": {"self_narrative"},  # [桥三 stage2 预埋 2026-10-14] 叙事产物专属 source×kind
    "memory_hippocampus": {"memory_digest"},  # [海马体 v1 2026-10-22] 睡眠压缩产物专属 source×kind
    "digital_twin": {"corpus"},  # [数字分身 v1 2026-10-23] 语料切块摄入专属 source×kind（recall 白名单不含 corpus，主召回零污染）
    "self_driven": {"self_observation"},  # [自驱 v2 2026-10-23] 执行闭环回写「我执行了自驱目标…」专属 source×kind
    "night_shift": {"task_result"},  # [夜班模式 2026-11-03] 连续失败熔断的失败原因专属 source×kind
    "memory_extractor": {"auto_fact", "auto_pref"},  # [路线二 2026-11-07] 对话后自动提炼的客观事实/偏好专属 source×kind（corpus 永不沾）
}

#: 写入校验拒绝的错误前缀——端点据此判定 HTTP code
_REJECT_PREFIX = "写入校验拒绝："


def _conn():
    return memory_db.connect()


def _ts():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


#: [M3a] 3-gram 滑窗钳制：每 token 最多 32 gram、去重后整体最多 64 gram，
#: 超出截断（保留靠前的），防超长 query 生成巨型 OR 表达式。
_FTS_GRAM_PER_TOKEN = 32
_FTS_GRAM_TOTAL = 64


def _fts_expr(query: str, mode: str = "recall") -> str:
    """用户 query → 安全 FTS5 MATCH 表达式（M3a 双模式 + recall_or 兜底）。

    mode="recall"（默认）：召回面优先。含 CJK 的词（≥3 字符）先做 3-gram 滑窗，
    各 gram 加引号后 OR 连接（任一 gram 命中即可），词与词之间 AND——解决中文
    整句被当成一个短语（要求记忆含连续子串）的漏召回：query "代码风格偏好"
    可召回 "用户喜欢简洁代码风格"（共享 gram "代码风"），旧逻辑两者零命中。
    mode="forget"：保持旧逐词短语逻辑（整词加引号、AND），删除面零扩大——
    滑窗 OR 会让"按 query 删除"退化成"共享任一 3-gram 即删"。
    mode="recall_or"（[P4切词修复 2026-10-06]）：与 recall 完全同构（同一批 gram、
    同一引号规则、同一钳制），仅最后词间连接改 OR——供 recall 端点在 AND 式查空
    后做第二发兜底。forget 在上方提前返回，不经过本模式；调用方仅 recall 兜底分支。

    共同规则：逐 gram/逐词加引号（FTS5 保留字防误解析 + 嵌入引号 doubling）；
    <3 字符词丢弃（trigram 数学下限——2 字中文词如"修复""引号"无法参与匹配，
    属 trigram 选型的固有损失）。

    已知边界（2026-10-02 探针实锤，SQLite 3.43.1）：
    - trigram 无法做语义召回："帮我改"与"修复"零 3-gram 交集。
      语义召回需 embedding（memory_bank 已预留 embedding 字段）。
    - trigram 无法跨越内容缺口："修复引号" 召不回 "修复 f-string 引号错误"。
    """
    tokens = [t for t in str(query or "").split() if len(t) >= 3]
    if mode == "forget":
        return " AND ".join('"%s"' % t.replace('"', '""') for t in tokens)

    def _quote(s: str) -> str:
        return '"%s"' % s.replace('"', '""')

    clauses = []
    seen_grams = set()
    for t in tokens:
        if not any('\u4e00' <= ch <= '\u9fff' for ch in t):
            clauses.append(_quote(t))    # 非中文词：整词短语，与旧逻辑一致
            continue
        grams = [t] if len(t) == 3 else [t[i:i + 3] for i in range(len(t) - 2)]
        picked = []
        for g in grams[:_FTS_GRAM_PER_TOKEN]:
            if g in seen_grams:
                continue
            if len(seen_grams) >= _FTS_GRAM_TOTAL:
                break
            seen_grams.add(g)
            picked.append(g)
        if len(picked) == 1:
            clauses.append(_quote(picked[0]))
        elif picked:
            clauses.append("(" + " OR ".join(_quote(g) for g in picked) + ")")
    return (" OR " if mode == "recall_or" else " AND ").join(clauses)


def insert_memory(content: str, kind: str = "fact", tags: list | None = None,
                  source: str = "manual", entity_id: str = "", session_id: str = "default"):
    """落库一条记忆（主表 + FTS 单事务双写）。[反思MVP 2026-10-04] 从 memory_save
    抽出的唯一 INSERT 汇聚点：save 端点与 reflection.py 共用，避免第二处裸 INSERT。

    [P1精确判重 2026-10-06] INSERT 前查重：同 source 且 _norm_text(content) 全等
    的记录已存在 → 不 INSERT，幂等返回 (已有 id, "")。只拦逐字重复（标点/空白
    归一化后全等），不做模糊相似度（近似文案是合法并存，time_decay_order 用例
    依赖）；键不含 kind/entity_id——entity_id 是执行实例维度（每次重跑换新
    uuid，demo_bug 同 goal 6 次执行 = 6 个不同 entity_id 实锤），按它判重挡不住
    同一任务反复执行，键语义修正属 P2。比对在 Python 侧现算（_norm_text 纯
    Python 逻辑，SQL 无法表达；只拉同 source 行，走 idx_memory_source 索引）。

    参数语义与 save 端点一致（kind/source 截 32 字、tags 逐元素截 32 字逗号串）。
    返回 (memory_id, "") 或 ("", 错误信息)——错误原样上抛给调用方自行处置（端点
    转 _bad，reflection 走 fail-open 记入 errors），本函数不吞异常。命中判重时
    返回已有 memory_id 且 err 为空（幂等语义：save 端点 200 返回已有 id、
    organ_memory 不误报写库失败、reflection 不污染 errors——报错式返回会同时
    误导三方调用方）。
    """
    kind = str(kind or "fact").strip()[:32]
    tags_list = tags or []
    tags = ",".join(str(t).strip()[:32] for t in tags_list if t)
    source = str(source or "manual").strip()[:32]
    entity_id = str(entity_id or "").strip()[:128]
    session_id = str(session_id or "default").strip()[:128]

    # [v1 写入校验 2026-10-13] source × kind 矩阵：未知 source 或越界 kind 一律拒绝
    # [P0可用性修复 2026-10-28] source 支持"命名空间:子标识"形态（如 digital_twin:文档名），
    # 矩阵按命名空间根键校验，既保住许可矩阵语义，又让语料可按文档隔离入库/检索。
    # 含":"的 source 才走根键回退，纯 source（manual/reflection/…）行为零变化。
    _ns_key = source.split(":", 1)[0] if ":" in source else source
    if kind not in _SOURCE_KIND_MATRIX.get(_ns_key, ()):
        # [v1 批3 2026-10-13] 拒绝留痕：ERROR → "moray" logger → logs/moray_errors.log，
        # 本行即 GET /api/memory/rejections 的唯一数据源（拒绝不再静默消失）
        logger.error("[memory_reject] %s", json.dumps(
            {"source": source, "kind": kind, "entity_id": entity_id,
             "session_id": session_id, "content": str(content)[:120]},
            ensure_ascii=False))
        return "", _REJECT_PREFIX + f"source={source} kind={kind} 不在许可矩阵"

    # [P1精确判重 2026-10-06] norm 非空才查重：全标点/空白内容不互相挡（空键
    # 会把所有"无有效字符"的内容互判为重复）。lazy import 而非顶部 import：
    # reflection.py:33 顶部反向依赖本模块 insert_memory，顶部 import 会循环
    # （executor.py:119 函数内 lazy import 同款先例）。
    from .reflection import _norm_text
    norm = _norm_text(str(content or ""))
    if norm:
        dup_conn = _conn()
        try:
            dup_rows = dup_conn.execute(
                "SELECT id, content FROM memory_bank WHERE source = ?",
                (source,)).fetchall()
        finally:
            dup_conn.close()
        for dup_id, dup_content in dup_rows:
            if _norm_text(dup_content) == norm:
                return dup_id, ""

    mem_id = uuid.uuid4().hex
    now = _ts()

    conn = _conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO memory_bank (id, kind, content, tags, source, entity_id, session_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mem_id, kind, content, tags, source, entity_id, session_id, now, now)
        )
        conn.execute(
            "INSERT INTO memory_fts (memory_id, content, tags) VALUES (?, ?, ?)",
            (mem_id, content, tags)
        )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return "", str(e)[:200]
    finally:
        conn.close()

    # [向量召回 v1 2026-10-24] 异步补嵌（后台 daemon 线程——嵌入走 HTTP 绝不上
    # 事件循环）。失败只记日志不影响入库：FTS 路兜底召回，backfill_embeddings
    # 随时补存量。spawn 内部全 fail-open，此处再兜一层保 insert 契约（id, ""）。
    try:
        memory_embed.spawn_embed(mem_id, content, kind)
    except Exception as e:  # noqa: BLE001
        logger.warning("[memory] spawn_embed 调度异常（mem_id=%s）：%s: %s",
                       mem_id, type(e).__name__, str(e)[:160])
    return mem_id, ""


@router.post("/api/memory/save")
async def memory_save(request: Request):
    """保存一条记忆"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    content = str(body.get("content") or "").strip()
    if not content:
        return _bad("bad_request", "content 必填且非空", 400)

    tags_list = body.get("tags") or []
    if not isinstance(tags_list, list):
        return _bad("bad_request", "tags 必须是数组", 400)

    mem_id, err = insert_memory(
        content,
        kind=str(body.get("kind") or "fact"),
        tags=tags_list,
        source=str(body.get("source") or "manual"),
        entity_id=str(body.get("entity_id") or ""),
        session_id=str(body.get("session_id") or "default"),
    )
    if err:
        code, status = ("rejected", 400) if err.startswith(_REJECT_PREFIX) else ("db_error", 500)
        return _bad(code, err, status)

    return _ok({"memory_id": mem_id})


def _recall_item(row, score: float) -> dict:
    """memory_bank 行 → 召回 item 形状（[向量召回 v1] FTS/向量两通道共用；
    字段与 M1 契约逐字一致，新增 fts_score/vec_score/matches 由融合段补）。"""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "content": row["content"],
        "tags": [t for t in (row["tags"] or "").split(",") if t],
        "source": row["source"],
        "entity_id": row["entity_id"],
        "session_id": row["session_id"],
        "score": round(score, 6),
        "created_at": row["created_at"]
    }


@router.post("/api/memory/recall")
async def memory_recall(request: Request):
    """按 query 召回记忆（FTS5 + 时间衰减排序；AND 查空 → 词间 OR 兜底重查一次）"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    query = str(body.get("query") or "").strip()
    if len(query) < 3:
        return _bad("bad_request", "query 长度必须 ≥ 3（trigram 下限）", 400)
    match_expr = _fts_expr(query, mode="recall")
    if not match_expr:
        return _bad("bad_request", "query 无有效检索词（每个词需 ≥3 字符，trigram 下限）", 400)

    kind = str(body.get("kind") or "").strip() or None
    source = str(body.get("source") or "").strip() or None
    tags_filter = body.get("tags") or []
    if not isinstance(tags_filter, list):
        return _bad("bad_request", "tags 必须是数组", 400)
    try:
        limit = int(body.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 50))
    try:
        decay_days = float(body.get("decay_days") or 30.0)
    except (TypeError, ValueError):
        decay_days = 30.0
    if decay_days <= 0:
        decay_days = 30.0

    conn = _conn()
    try:
        # FTS5 MATCH 查询（bm25 rank）
        sql = (
            "SELECT m.id, m.kind, m.content, m.tags, m.source, m.entity_id, "
            "       m.session_id, m.weight, m.created_at, "
            "       bm25(memory_fts) AS bm25_rank "
            "FROM memory_fts "
            "JOIN memory_bank m ON m.id = memory_fts.memory_id "
            "WHERE memory_fts MATCH ? "
            # [白名单治本 2026-10-10] 召回面收白名单：只允许用户可见的 kind 进；
            # 新 kind（self_observation / self / 未来）默认不进，结构上防污染。
            # 注：'fact' 是 schema 默认 kind，当前库中无数据，预留。
            # [路线二 2026-11-07] 收编 auto_fact/auto_pref（Memory Extractor 对话后
            # 自动提炼的用户事实/偏好）——自动记忆被召回是"长记性"的全部意义；
            # corpus 依旧不在白名单（与数字分身语料的隔离口径不变）。
            "AND m.kind IN ('fact', 'task_result', 'insight', 'preference', 'auto_fact', 'auto_pref') "
        )
        params = [match_expr]
        if kind:
            sql += "AND m.kind = ? "
            params.append(kind)
        if source:
            sql += "AND m.source = ? "
            params.append(source)
        if tags_filter:
            # tags 逗号分隔存储，逐个 LIKE 匹配（AND 语义）
            for t in tags_filter:
                sql += "AND m.tags LIKE ? "
                params.append("%" + str(t).strip() + "%")
        sql += "ORDER BY bm25_rank ASC LIMIT ?"
        params.append(limit * 3)  # 多取一些，后面按衰减重排

        rows = conn.execute(sql, params).fetchall()
        if not rows:
            # [P4切词修复 2026-10-06] OR 兜底（路由层两段式）：第一发词间 AND，口语
            # 整句常带零交集 token，任一 token 零命中即整句全灭。查空时第二发词间改
            # OR 重查一次——sql 与 kind/source/tags 过滤、LIMIT 全部原样复用，仅换
            # params[0] 的 match 表达式（params[1:] 即过滤与 limit 参数）。只在 0 命中
            # 触发（命中不付代价）；不加阈值/条数上限（先纯降级，数据说话）；单 token
            # 时 OR 式与 AND 式相同，跳过重查。mode="forget" 不经过该分支。
            fallback_expr = _fts_expr(query, mode="recall_or")
            if fallback_expr and fallback_expr != match_expr:
                rows = conn.execute(sql, [fallback_expr] + params[1:]).fetchall()
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    # 时间衰减重排（[向量召回 v1] item 组装抽 _recall_item 供双通道共用）
    now_dt = datetime.now()
    items = []
    for row in rows:
        try:
            created = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%S")
            age_days = (now_dt - created).total_seconds() / 86400.0
        except Exception:  # noqa: BLE001 - 时间解析失败按"新记忆"处理
            age_days = 0.0
        # bm25 越负越相关；取 abs 后越大越相关
        bm25_score = abs(float(row["bm25_rank"]))
        # 时间衰减因子（指数半衰）
        decay_factor = math.exp(-age_days / decay_days)
        final_score = bm25_score * decay_factor * (row["weight"] or 1.0)
        items.append(_recall_item(row, final_score))

    # 按 final_score DESC 排序
    items.sort(key=lambda x: x["score"], reverse=True)

    # ---- [向量召回 v1 2026-10-24] 语义通道（请求级 opt-in：body.mode=="hybrid"）。
    # 默认 mode="fts" 与 M1 契约逐字节一致（零命中=空列表、条数/排序不变——
    # test_memory_bridge 旧断言与既有调用方零感知）；前端 memory_client 显式带
    # mode:"hybrid" 走融合。语义通道 = query 嵌入 → 相似扫描（sqlite-vec KNN /
    # 纯 Python 余弦兜底）→ 与 FTS 面 RRF 融合（k=60）。向量通道任何异常只降级
    # 为纯 FTS（与 AND→OR 兜底同哲学：只降级不加阈值）。回表用"嵌行全扫 +
    # Python 过滤"而非 IN 拼接（参数绑定纪律；嵌行规模=库内带 embedding 的行数）。
    mode = str(body.get("mode") or "fts").strip().lower()
    # [路线二 2026-11-07] 收编 auto_fact/auto_pref，与上方 SQL 白名单同口径，改一处必改两处
    _RECALL_KINDS = ("fact", "task_result", "insight", "preference", "auto_fact", "auto_pref")
    vec_items = []
    if mode == "hybrid":
        try:
            q_vec = memory_embed.get_embedding(query)
            if q_vec:
                # [vec kind 分区 2026-10-25] kind=None：召回白名单是 4 种 kind，
                # vec0 元数据下推只支持单值等值，多 kind 白名单仍走回表过滤。
                cand = dict(memory_embed.search_similar(q_vec, k=max(limit * 3, 15), kind=None))
                if cand:
                    # [P1-5 修复 2026-11-01] 回表改按 top-k id 定点取：json_each 绑定，
                    # SQL 字面量零拼接（Mimosa 纪律），行数 ≤ k（=limit*3），
                    # 不再全表扫全部嵌入行（原实现把整库 BLOB 拉进 Python）。
                    conn2 = _conn()
                    try:
                        vrows = conn2.execute(
                            "SELECT id, kind, content, tags, source, entity_id, session_id, weight, created_at "
                            "FROM memory_bank WHERE embedding IS NOT NULL"
                            " AND id IN (SELECT value FROM json_each(?))",
                            (json.dumps(list(cand.keys())),)
                        ).fetchall()
                    finally:
                        conn2.close()
                    for row in vrows:
                        vs = cand.get(row["id"])
                        if vs is None or row["kind"] not in _RECALL_KINDS:
                            continue
                        try:
                            created = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%S")
                            age_days = (now_dt - created).total_seconds() / 86400.0
                        except Exception:  # noqa: BLE001 - 同 FTS 面：解析失败按新记忆
                            age_days = 0.0
                        vec_final = (float(vs) * math.exp(-age_days / decay_days)
                                     * (row["weight"] or 1.0))
                        vec_items.append(_recall_item(row, vec_final))
                    vec_items.sort(key=lambda x: x["score"], reverse=True)
                    vec_items = vec_items[:limit]
        except Exception as e:  # noqa: BLE001 - 向量通道失败只降级，绝不拖垮召回
            logger.warning("[memory] 向量召回通道降级（不影响 FTS 结果）：%s: %s",
                           type(e).__name__, str(e)[:160])
            vec_items = []

    if mode == "hybrid":
        # RRF 融合（k=60）：item["score"] 换成融合分；fts_score/vec_score/matches
        # 留痕哪路命中（前端命中明细兼容旧字段，新增字段为纯增量）。
        fused = {}
        for rank, it in enumerate(items[:limit], 1):
            it["fts_score"] = it["score"]
            it["vec_score"] = None
            it["matches"] = ["fts"]
            fused[it["id"]] = [it, 1.0 / (60.0 + rank)]
        for rank, it in enumerate(vec_items, 1):
            cur = fused.get(it["id"])
            if cur is not None:
                cur[0]["vec_score"] = it["score"]
                cur[0]["matches"].append("vec")
                cur[1] += 1.0 / (60.0 + rank)
            else:
                it["fts_score"] = None
                it["vec_score"] = it["score"]
                it["matches"] = ["vec"]
                fused[it["id"]] = [it, 1.0 / (60.0 + rank)]
        merged = sorted(fused.values(), key=lambda t: t[1], reverse=True)[:limit]
        for it, rrf in merged:
            it["score"] = round(rrf, 6)
        return _ok({"items": [it for it, _rrf in merged], "mode": "hybrid"})

    return _ok({"items": items[:limit], "mode": "fts"})


@router.post("/api/memory/forget")
async def memory_forget(request: Request):
    """删除记忆（按 id / query / tags 三选一）"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _bad("bad_json", "请求体必须是 JSON", 400)

    mem_id = str(body.get("memory_id") or "").strip()
    query = str(body.get("query") or "").strip()
    tags_filter = body.get("tags") or []

    if not mem_id and not query and not tags_filter:
        return _bad("bad_request", "必须指定 memory_id / query / tags 之一", 400)

    conn = _conn()
    try:
        conn.execute("BEGIN")
        deleted = 0
        if mem_id:
            cur = conn.execute("DELETE FROM memory_bank WHERE id = ?", (mem_id,))
            deleted = cur.rowcount or 0
            conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mem_id,))
            if memory_db.VEC_AVAILABLE:
                conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mem_id,))
        elif query:
            if len(query) < 3:
                conn.rollback()
                return _bad("bad_request", "query 长度必须 ≥ 3", 400)
            match_expr = _fts_expr(query, mode="forget")
            if not match_expr:
                conn.rollback()
                return _bad("bad_request", "query 无有效检索词（每个词需 ≥3 字符）", 400)
            # 先查命中的 id，再双表同步删
            rows = conn.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ?",
                (match_expr,)
            ).fetchall()
            ids = [r["memory_id"] for r in rows]
            for mid in ids:
                conn.execute("DELETE FROM memory_bank WHERE id = ?", (mid,))
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mid,))
                if memory_db.VEC_AVAILABLE:
                    conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mid,))
                deleted += 1
        else:
            if not isinstance(tags_filter, list):
                conn.rollback()
                return _bad("bad_request", "tags 必须是数组", 400)
            # 按 tags LIKE 匹配（逐个 tag AND）
            sql = "SELECT id FROM memory_bank WHERE 1=1 "
            params = []
            for t in tags_filter:
                sql += "AND tags LIKE ? "
                params.append("%" + str(t).strip() + "%")
            rows = conn.execute(sql, params).fetchall()
            ids = [r["id"] for r in rows]
            for mid in ids:
                conn.execute("DELETE FROM memory_bank WHERE id = ?", (mid,))
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mid,))
                if memory_db.VEC_AVAILABLE:
                    conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (mid,))
                deleted += 1
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    return _ok({"deleted": deleted})


@router.get("/api/memory/list")
async def memory_list(limit: int = 50, offset: int = 0):
    """分页列出记忆（created_at DESC）"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, kind, content, tags, source, entity_id, session_id, weight, created_at "
            "FROM memory_bank ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM memory_bank").fetchone()["c"]
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
            "weight": row["weight"],
            "created_at": row["created_at"]
        })

    return _ok({"items": items, "total": total})


@router.get("/api/memory/stats")
async def memory_stats():
    """[自省面板 v1.1 2026-10-10] 各 kind 的条数统计——只读，供前端展示「记忆状态」。

    只读 SELECT（与 memory_list 同款 _conn()/finally close 范式），不写库、不碰
    召回白名单逻辑；信封按任务书定样平铺 {ok, counts, total}（消费侧与
    /api/self_check/report 同惯例，故不套本文件 _ok 的 data 嵌套）。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) AS c FROM memory_bank GROUP BY kind"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()
    counts = {row["kind"]: row["c"] for row in rows}
    return {"ok": True, "counts": counts, "total": sum(counts.values())}


# ── 失败档案 v1（2026-10-11）────────────────────────────────────────────────
#
# 只读扫描端点：把 memory_bank 里 kind='task_result' 且 content 命中失败
# 关键词的行捞出来，标成一层可检索的候选，供自省面板「失败档案」卡展示。
#
# v1 边界（窗户哲学：先让它被看见，再谈提炼）：
#   · 零写库、零 kind 改动——不引入 failure_archive kind（仅命名预留），
#     不自动改写/迁移任何既有记录；
#   · 宽口径关键词命中 = 候选标记，不判真伪——命中里可能混有「修复成功
#     的报错」（task_flow 的 ｜验证：ok）与「任务描述含报错字样」，如实带
#     matched / verdict_excerpt 供人眼复核；
#   · 不调 LLM、不碰召回白名单；SQL 侧 LIKE 与 Python 侧复扫共用下方
#     _FAILURE_KEYWORDS 单一来源（SQL 粗筛是候选层，Python in 精确标命中词）。

_FAILURE_KEYWORDS = ("error", "failed", "失败", "Error", "Traceback", "exception")


@router.get("/api/memory/failures")
async def memory_failures(limit: int = 50):
    """失败档案 v1：扫 task_result 中 content 命中失败关键词的行（只读）。

    平铺信封 {ok, items, total, matched_note}（与 memory_stats 同惯例——
    消费侧同为本面板，不套 _ok 的 data 嵌套）。每条：
      matched         = Python 侧复扫 content 的命中词列表（区分大小写）
      summary         = summary 列非空用之，否则截 content 前 80 字
      verdict_excerpt = 「结果：」行首摘录，或行内「验证：」起的后缀（task_flow
                        的「补丁：｜验证：failed」行尾形态，全/半角冒号都认），无则空串
    只读 SELECT，零写库、零 kind 改动。"""
    limit = max(1, min(limit, 200))

    kw_sql = " OR ".join(["content LIKE ?"] * len(_FAILURE_KEYWORDS))
    where_params = [f"%{kw}%" for kw in _FAILURE_KEYWORDS]

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, kind, content, summary, tags, source, created_at "
            f"FROM memory_bank WHERE kind='task_result' AND ({kw_sql}) "
            "ORDER BY created_at DESC LIMIT ?",
            where_params + [limit]
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM memory_bank WHERE kind='task_result' AND ({kw_sql})",
            where_params
        ).fetchone()["c"]
    except Exception as e:  # noqa: BLE001
        return _bad("db_error", str(e)[:200], 500)
    finally:
        conn.close()

    items = []
    for row in rows:
        content = row["content"] or ""
        matched = [kw for kw in _FAILURE_KEYWORDS if kw in content]
        verdict_excerpt = ""
        for line in content.splitlines():
            s = line.strip()
            if s.startswith(("结果：", "结果:")):
                verdict_excerpt = s[:80]
                break
            idx = s.find("验证：")
            if idx < 0:
                idx = s.find("验证:")
            if idx >= 0:
                verdict_excerpt = s[idx:][:80]
                break
        items.append({
            "id": row["id"],
            "kind": row["kind"],
            "source": row["source"],
            "tags": [t for t in (row["tags"] or "").split(",") if t],
            "created_at": row["created_at"],
            "summary": (row["summary"] or "").strip() or content[:80],
            "matched": matched,
            "verdict_excerpt": verdict_excerpt
        })

    return {
        "ok": True,
        "items": items,
        "total": total,
        "matched_note": "宽口径关键词命中=候选标记，不判真伪；「验证：/结果：」行摘录见 verdict_excerpt"
    }


# ── 写入拒绝 v1 批3（2026-10-13）────────────────────────────────────────────
#
# 只读回读端点：拒绝留痕已在 insert_memory 里落进 logs/moray_errors.log
# （ERROR 级 [memory_reject] 行，见上方矩阵守卫分支）。本端点只从该文件尾部
# 有界读，零写库、零新数据源——拒绝与错误共用同一份磁盘记忆，格式知识留在
# self_check.py（formatter 与 parser 同居：| 分隔由 _ISO8601LineFormatter 定义）。
@router.get("/api/memory/rejections")
async def memory_rejections(limit: int = 50):
    """[v1 批3 2026-10-13] 读 errors.log 尾部 [memory_reject] 行——只读，零写库。"""
    limit = max(1, min(int(limit or 50), 200))
    from .self_check import read_recent_error_lines
    lines, scanned, truncated = read_recent_error_lines("[memory_reject]", limit=limit)
    items = []
    for ln in reversed(lines):
        parts = ln.split(" | ", 2)
        if len(parts) < 3:
            continue
        ts, lvl, msg = parts
        payload = msg.strip()
        if payload.startswith("[memory_reject] "):
            payload = payload[len("[memory_reject] "):]
        try:
            obj = json.loads(payload)
        except Exception:  # noqa: BLE001 - 非 JSON 行如实降级成 raw，不炸端点
            obj = {"raw": payload}
        obj["ts"] = ts
        items.append(obj)
    return {"ok": True, "items": items, "total": len(items),
            "scanned_bytes": scanned, "truncated": truncated}
