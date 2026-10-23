"""记忆海马体 v1（2026-10-22）：睡眠压缩——旧素材（self_observation +
argus_research 研究结论）浓缩为 1 条 memory_digest，并物理删除被吸收的源行。

范式参照（只加不改，零旧逻辑改动）：
- self_narrative.py：LLM 双协议调用（REFLECTION_* config 组）、在飞闸、冷却、fail-open；
- self_reflection.py：守卫顺序（素材 → LLM → 写库 → 成功才更新冷却）；
- memory_routes.forget 端点底层：memory_bank + memory_fts 双表同步删除（参数绑定）。

机制：
- 素材闸：吸收范围（self_observation + argus_research 研究结论）总数 ≥ _MIN_OBS_TOTAL（100）才动手；
- 批次：每次只压最旧 _COMPACT_BATCH（50）条，压后剩 ≥50 条，攒批天然节流；
- 产物：insert_memory(kind='memory_digest', source='memory_hippocampus')（矩阵许可对），
  content=纯文本摘要（约 200 字），tags=被吸收行的 memory_id 溯源（self_narrative
  basis_ids 同款墓碑语义：源行删了，ids 留在 digest 的 tags 里可追）；
- 删除：INSERT 成功后物理删除源行（双表同步）；删除失败则回滚刚写入的 digest
  （批次原子性：要么"浓缩+删旧"都成，要么整批无事发生，绝不留半态）。

fail-open：一切异常转 {"ran": False, "reason": ...}，绝不拖垮 self_check_loop
（CancelledError 继承 BaseException，except Exception 不捕，照常上抛）。
"""
import logging
import time

import httpx

from . import config
from .memory_routes import insert_memory

logger = logging.getLogger("moray.memory_hippocampus")

#: 产物对（memory_routes._SOURCE_KIND_MATRIX 预埋许可；source×kind 唯一入口）
_DIGEST_KIND = "memory_digest"
_DIGEST_SOURCE = "memory_hippocampus"
#: 素材 kind（self_reflection._OBS_KIND 同值；写入方=self_reflection，此处只读+删）
_OBS_KIND = "self_observation"
#: 追加吸收：Argus 研究结论（argus_proxy 写入 kind='task_result'/source='argus_research'）。
#: 刻意不吸全部 task_result：zcode_stream/laya_executor/task_flow 是任务流水账
#: 非长期教训，误压缩会丢任务档案（失败档案端点还靠它们），故谓词钉 source。
_RESULT_KIND = "task_result"
_ARGUS_SOURCE = "argus_research"

#: 素材闸：self_observation 总数达到该值才压缩（v1 钉死 100）
_MIN_OBS_TOTAL = 100
#: 每批压缩条数：只动最旧的这么多条
_COMPACT_BATCH = 50
#: 摘要目标长度（prompt 语义目标，非硬闸）与产出钳制
_DIGEST_TARGET_CHARS = 200
_DIGEST_MIN_CONTENT = 20      # 过短=空转/垃圾，拒写且绝不删源数据
_DIGEST_MAX_CONTENT = 800     # 200 字目标 ×4 余量（self_narrative 5 倍余量惯例）
#: prompt 总字符预算（self_narrative._NARRATIVE_PROMPT_CHARS 同款实证值：8192 ctx 安全）
_PROMPT_CHARS = 12000
#: 单条观察进 prompt 的行截断（reflection 同款口径：预算闸优先于行完整性）。
#: 200 字/条 × 50 条 + 时间戳 ≈ 11k < 12k 预算，正常批次全装得下；超预算时丢尾部（较新）条目。
_OBS_LINE_CHARS = 200
#: 冷却（秒）：成功（写库+删源都成）才更新；失败不消耗、下轮重试（self_reflection 同款）
_HIPPO_COOLDOWN_SEC = 3600

_hippo_inflight = False
_last_hippo_monotonic = 0.0


def _read_old_observations() -> dict:
    """读素材：吸收范围（self_observation + source='argus_research' 的 task_result）
    总数 + 最旧 _COMPACT_BATCH 条（id/kind/content/created_at）。
    只 SELECT；排序 created_at ASC, id ASC（同刻稳定序）。fail-open：异常转 status=error。"""
    try:
        from . import memory_db  # 惰性 import（self_model/self_narrative 同款惯例）
        conn = memory_db.connect()
        try:
            # 吸收谓词（两查询共用）：kind='self_observation'
            # OR (kind='task_result' AND source='argus_research')——
            # ★ 任务书分支：不吸无关 task_result（zcode_stream/laya_executor/task_flow）
            total = conn.execute(
                "SELECT COUNT(*) FROM memory_bank WHERE kind = ? "
                "OR (kind = ? AND source = ?)",
                (_OBS_KIND, _RESULT_KIND, _ARGUS_SOURCE),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, kind, content, created_at FROM memory_bank "
                "WHERE kind = ? OR (kind = ? AND source = ?) "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (_OBS_KIND, _RESULT_KIND, _ARGUS_SOURCE, _COMPACT_BATCH),
            ).fetchall()
        finally:
            conn.close()
        items = [{"id": r["id"], "kind": r["kind"] or "", "content": r["content"] or "",
                  "created_at": r["created_at"]} for r in rows]
        return {"status": "ok", "total": int(total or 0), "items": items}
    except Exception as e:  # noqa: BLE001
        logger.warning("[memory_hippocampus] 读素材异常：%s: %s",
                       type(e).__name__, str(e)[:200])
        return {"status": "error",
                "reason": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _build_digest_messages(items: list) -> list:
    """组压缩 prompt（任务书语义：浓缩为 1 条约 200 字的长期教训/摘要，纯文本输出；
    必须涵盖研究结论——Argus 报告的核心结论不得在浓缩中丢失）。
    逐行截 _OBS_LINE_CHARS、总预算 _PROMPT_CHARS 钳制（超预算丢尾部较新条目——
    批次语义就是最旧优先，与取批口径一致）。"""
    lines = []
    used = 0
    for i, it in enumerate(items, 1):
        label = "研究结论" if it.get("kind") == _RESULT_KIND else "自省观察"
        line = "%d. [%s][%s] %s" % (i, it.get("created_at") or "?", label,
                                    str(it.get("content") or "")[:_OBS_LINE_CHARS])
        if used + len(line) > _PROMPT_CHARS:
            break
        lines.append(line)
        used += len(line)
    user = ("请将以下 %d 条记忆素材（自省观察与 Argus 研究结论）浓缩为 1 条约 %d 字的"
            "长期教训/摘要，必须保留其中重要的研究结论。纯文本输出。\n\n%s"
            % (len(lines), _DIGEST_TARGET_CHARS, "\n".join(lines)))
    return [
        {"role": "system",
         "content": "你是长期记忆压缩器。只输出浓缩后的纯文本摘要，不要任何解释或前缀。"
                    "摘要须涵盖素材中的研究结论。"},
        {"role": "user", "content": user},
    ]


async def _llm_digest(messages: list):
    """喂 messages 要纯文本摘要；任何异常/非 200/空应答返回 (None, 原因)。
    复用 reflection 同组 config（REFLECTION_API/BASE_URL/MODEL/TIMEOUT/NUM_CTX），
    双协议口径与 self_narrative._llm_narrate 完全一致；差异仅在输出语义：纯文本。"""
    if config.REFLECTION_API == "openai":
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/v1/chat/completions"
        payload = {"model": config.REFLECTION_MODEL, "stream": False,
                   "temperature": 0.0, "messages": messages}
    else:
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/api/chat"
        payload = {"model": config.REFLECTION_MODEL, "stream": False, "temperature": 0.0,
                   "options": {"num_ctx": config.REFLECTION_NUM_CTX},
                   "messages": messages}
    try:
        async with httpx.AsyncClient(timeout=config.REFLECTION_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            return None, "%s HTTP %s（model=%s url=%s）" % (
                config.REFLECTION_API, r.status_code, config.REFLECTION_MODEL, url)
        body = r.json()
        if config.REFLECTION_API == "openai":
            raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        else:
            raw = (body.get("message") or {}).get("content", "")
        if not (raw or "").strip():
            return None, "推理服务应答为空"
        return raw.strip(), ""
    except Exception as e:  # noqa: BLE001 - fail-open：异常转错误原因返回，不上抛
        logger.warning("[memory_hippocampus] 推理调用异常：%s: %s",
                       type(e).__name__, str(e)[:200])
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])


def _delete_rows(ids: list) -> tuple:
    """物理删除：memory_bank + memory_fts 双表同步删（forget 端点底层 SQL 同款，参数绑定）。
    单事务全成或全不成。返回 (deleted, "") 或 (0, 原因)。"""
    if not ids:
        return 0, ""
    try:
        from . import memory_db  # 惰性 import（_read_old_observations 同款）
        conn = memory_db.connect()
        try:
            conn.execute("BEGIN")
            for mid in ids:
                conn.execute("DELETE FROM memory_bank WHERE id = ?", (mid,))
                conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (mid,))
            conn.commit()
            return len(ids), ""
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            return 0, str(e)[:200]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, str(e)[:200])


async def compress_once() -> dict:
    """压缩一批：素材闸 → LLM → 写 digest → 删源行（删失败自回滚 digest）。
    防跑飞三闸（全在 LLM 调用之前判定，被拒调用零推理开销）：
      ① 在飞闸：_hippo_inflight 压缩中不重复触发（两挂点/未来端点共用）；
      ② 冷却：1h 进程内 monotonic，成功才更新（失败不消耗，下轮重试）；
      ③ 素材闸：吸收范围（self_observation + argus_research）总数 < 100 不动手。
    fail-open：任何失败返回 {"ran": False, "reason": ...}，绝不抛。"""
    global _hippo_inflight, _last_hippo_monotonic
    if _hippo_inflight:
        return {"ran": False, "reason": "compression_already_in_progress"}
    now = time.monotonic()
    if _last_hippo_monotonic and now - _last_hippo_monotonic < _HIPPO_COOLDOWN_SEC:
        return {"ran": False, "reason": "cooldown"}
    src = _read_old_observations()
    if src.get("status") != "ok":
        return {"ran": False, "reason": "sources error: %s" % src.get("reason")}
    total = src.get("total", 0)
    items = src.get("items") or []
    if total < _MIN_OBS_TOTAL:
        return {"ran": False,
                "reason": "insufficient: total=%d < %d" % (total, _MIN_OBS_TOTAL)}
    if not items:
        return {"ran": False, "reason": "no observations to compact", "total": total}
    _hippo_inflight = True
    try:
        messages = _build_digest_messages(items)
        text, err = await _llm_digest(messages)
        if text is None:
            logger.warning("[memory_hippocampus] LLM 阶段失败：%s", err)
            return {"ran": False, "reason": "llm_failed: %s" % err, "total": total}
        digest = text.strip()
        if len(digest) < _DIGEST_MIN_CONTENT:
            logger.warning("[memory_hippocampus] 拒写（过短 %d 字）：%r",
                           len(digest), digest[:40])
            return {"ran": False,
                    "reason": "rejected_too_short: %d chars" % len(digest),
                    "total": total}
        digest = digest[:_DIGEST_MAX_CONTENT]
        basis_ids = [it["id"] for it in items]
        mem_id, db_err = insert_memory(
            digest, kind=_DIGEST_KIND, source=_DIGEST_SOURCE, tags=basis_ids)
        if db_err:
            logger.warning("[memory_hippocampus] 写库失败：%s", db_err)
            return {"ran": False, "reason": "insert_failed: %s" % db_err, "total": total}
        deleted, del_err = _delete_rows(basis_ids)
        if del_err:
            # 批次原子性：删源失败 → 回滚刚写入的 digest，宁可整批无事发生也不留半态
            rb, _rb_err = _delete_rows([mem_id])
            logger.warning(
                "[memory_hippocampus] 删源失败已回滚 digest（rollback_deleted=%d）：%s",
                rb, del_err)
            return {"ran": False, "reason": "delete_failed: %s" % del_err, "total": total}
        _last_hippo_monotonic = time.monotonic()
        logger.warning("[memory_hippocampus] 压缩完成 digest=%s 摘要=%d字 吸收=%d条 total_before=%d",
                       mem_id, len(digest), deleted, total)
        return {"ran": True, "memory_id": mem_id, "digest_chars": len(digest),
                "deleted": deleted, "total_before": total}
    finally:
        _hippo_inflight = False


async def memory_hippocampus_maybe() -> dict:
    """主入口（挂 self_check_loop 用）：吞掉一切异常转 reason——挂进循环绝不中断
    （CancelledError 继承 BaseException，不被 except Exception 捕获，照常上抛）。"""
    try:
        return await compress_once()
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[memory_hippocampus] memory_hippocampus_maybe 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False,
                "reason": "unhandled: %s: %s" % (type(e).__name__, str(e)[:120])}
