# -*- coding: utf-8 -*-
"""自我叙事 self_narrative（2026-10-10 桥三 v1-stage1）：自省第三步骨架——本阶段
只做"准备度检查"，不生成叙事：不调 LLM、不写 memory_bank、不新增 kind、
不挂 self_check_loop（v1-stage1 靠端点手动查）。

级别边界（与桥一 self_check、桥二 self_reflection、桥三 v0 self_model 同口径，勿扩）：
- 对 memory_bank 只 SELECT 只读，零写入；kind='self_narrative' 是 v1-stage2 的
  产物 kind，本模块只探测其最新 created_at 判冷却，绝不落库；
- fail-open：读不到 / 解析失败 / 异常一律转 reason，绝不把异常抛给调用方；
- 模板中文，只放事实，永不出现"应该/建议/需要"。

就绪两条件（check_readiness）：
  ① self_observation 条数 >= _SELF_NARRATIVE_MIN_SAMPLES；
  ② 距上次生成 >= _SELF_NARRATIVE_COOLDOWN_SEC；库里无 self_narrative 行
     = 从未生成，条件②视为满足。
"""
import datetime
import logging

from fastapi import APIRouter

logger = logging.getLogger("moray.self_narrative")

#: 冷却（秒）：v1-stage2 两条 self_narrative 生成的最小间隔，7 天硬编不进 config/env。
#: 与 self_model 的进程内 monotonic 冷却不同口径：本冷却读的是库内
#: created_at 挂钟，跨进程重启依然成立（stage2 生成后写入方同款复用）。
_SELF_NARRATIVE_COOLDOWN_SEC = 604800
#: 素材门槛：self_observation 至少够 _SELF_NARRATIVE_MIN_SAMPLES 条才谈得上成叙事
_SELF_NARRATIVE_MIN_SAMPLES = 10

_NARRATIVE_KIND = "self_narrative"  # stage2 产物 kind（stage1 只探测，绝不写入）
_OBS_KIND = "self_observation"
_INSIGHT_KIND = "insight"
_SELF_KIND = "self"
#: 喂给 stage2 的近期 insight 条数上限
_INSIGHTS_LIMIT = 30


def _read_narrative_sources() -> dict:
    """读素材（只读，不写）：
       - 全部 self_observation（按 created_at 升序）
       - 近期 insight（最多 30 条，按 created_at 降序）
       - 最后一条 self（仪表盘）
    成功返回 {"status": "ok", "observations": [...], "insights": [...], "last_self": {...}}；
    读不到 → {"status": "no_data", "reason": "..."}。
    不抛异常。"""
    try:
        from . import memory_db  # 惰性 import（与 self_model 同款惯例）
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败也是 no_data
        logger.warning("[self_narrative] import memory_db 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "import memory_db failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    try:
        conn = memory_db.connect()
        try:
            observations = [
                {"id": r[0], "created_at": r[1], "content": r[2]}
                for r in conn.execute(
                    "SELECT id, created_at, content FROM memory_bank "
                    "WHERE kind = ? ORDER BY created_at ASC",
                    (_OBS_KIND,)).fetchall()]
            insights = [
                {"id": r[0], "created_at": r[1], "content": r[2]}
                for r in conn.execute(
                    "SELECT id, created_at, content FROM memory_bank "
                    "WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                    (_INSIGHT_KIND, _INSIGHTS_LIMIT)).fetchall()]
            last_self_row = conn.execute(
                "SELECT id, created_at, content FROM memory_bank "
                "WHERE kind = ? ORDER BY created_at DESC LIMIT 1",
                (_SELF_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败也是 no_data
        logger.warning("[self_narrative] 读素材异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"status": "no_data",
                "reason": "read narrative sources failed: %s: %s"
                          % (type(e).__name__, str(e)[:120])}
    return {
        "status": "ok",
        "observations": observations,
        "insights": insights,
        "last_self": ({"id": last_self_row[0], "created_at": last_self_row[1],
                       "content": last_self_row[2]}
                      if last_self_row is not None else None),
    }


def _seconds_since_last_generation():
    """距上次生成（kind='self' 且 tags 含 self_narrative 的最新一条 created_at）的秒数（挂钟）。
    无记录 / 读库失败 / 时间解析失败一律返回 None——冷却条件②按"满足"处理
    （fail-open；stage1 本就不生成，stage2 落库时自会建立基线）。"""
    try:
        from . import memory_db
        conn = memory_db.connect()
        try:
            # [桥三 stage2 修正 2026-10-10] stage2 会写 kind='self' + tags=['self_narrative']，探测须对齐
            row = conn.execute(
                "SELECT created_at FROM memory_bank WHERE kind = ? "
                "AND tags LIKE '%self_narrative%' "
                "ORDER BY created_at DESC LIMIT 1",
                (_SELF_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：探测失败不拦路
        logger.warning("[self_narrative] 冷却探测异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    if row is None or not row[0]:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(row[0]))
    except Exception:  # noqa: BLE001 - fail-open：解析失败当从未生成
        return None
    if dt.tzinfo is None:
        return (datetime.datetime.now() - dt).total_seconds()
    return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()


def check_readiness() -> dict:
    """检查桥三 v1-stage2 是否可以启动：
       条件：
         ① self_observation 条数 >= _SELF_NARRATIVE_MIN_SAMPLES
         ② 距上次生成 >= _SELF_NARRATIVE_COOLDOWN_SEC
    返回 {"ready": bool, "reason": str, "observation_count": int, "threshold": int}。
    吞掉一切异常转 reason，绝不抛。"""
    try:
        sources = _read_narrative_sources()
        if sources.get("status") != "ok":
            return {"ready": False,
                    "reason": "not_ready: sources no_data: %s" % sources.get("reason"),
                    "observation_count": 0,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        count = len(sources["observations"])
        if count < _SELF_NARRATIVE_MIN_SAMPLES:
            return {"ready": False,
                    "reason": "not_ready: observation_count %d < %d"
                              % (count, _SELF_NARRATIVE_MIN_SAMPLES),
                    "observation_count": count,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        elapsed = _seconds_since_last_generation()
        if elapsed is not None and elapsed < _SELF_NARRATIVE_COOLDOWN_SEC:
            return {"ready": False,
                    "reason": "not_ready: cooldown %.0fs < %ds since last generation"
                              % (elapsed, _SELF_NARRATIVE_COOLDOWN_SEC),
                    "observation_count": count,
                    "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
        return {"ready": True, "reason": "ready",
                "observation_count": count,
                "threshold": _SELF_NARRATIVE_MIN_SAMPLES}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_narrative] check_readiness 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ready": False,
                "reason": "not_ready: exception: %s: %s"
                          % (type(e).__name__, str(e)[:120]),
                "observation_count": 0,
                "threshold": _SELF_NARRATIVE_MIN_SAMPLES}


def self_narrative_maybe() -> dict:
    """主入口（stage1 只做检查，不生成）：
       - 调 check_readiness()
       - ready=False → return {"ran": False, "reason": "not_ready: ...", "readiness": {...}}
       - ready=True → return {"ran": False, "reason": "ready_but_stage1_noop", "readiness": {...}}
         ★ stage1 不写库——留 stage2 真跑。
    本函数吞掉一切异常转 reason——未来挂进 self_check_loop 也绝不中断循环。"""
    try:
        readiness = check_readiness()
        if not readiness.get("ready"):
            return {"ran": False,
                    "reason": readiness.get("reason") or "not_ready",
                    "readiness": readiness}
        return {"ran": False, "reason": "ready_but_stage1_noop",
                "readiness": readiness}
    except Exception as e:  # noqa: BLE001 - fail-open：异常转 reason，绝不上抛
        logger.warning("[self_narrative] self_narrative_maybe 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False,
                "reason": "exception: %s: %s" % (type(e).__name__, str(e)[:120]),
                "readiness": {}}


router = APIRouter()


@router.get("/self_narrative/readiness")
def self_narrative_readiness() -> dict:
    """[桥三 v1-stage1] 只读准备度端点：GET /api/self_narrative/readiness。
    直接返回 check_readiness()——只 SELECT，不写库、不调 LLM。"""
    return check_readiness()
