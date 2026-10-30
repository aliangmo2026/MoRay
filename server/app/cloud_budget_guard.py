# -*- coding: utf-8 -*-
"""cloud_budget_guard — 云端 LLM 金额预算闸门（任务二新增，2026-09-30）。

三层开关语义（任务书 + 校准 2）：
  L0  ENABLE_CLOUD_API=false（默认）         → 云端不存在于调度链，acquire 一律 CloudDisabled
  L1  ENABLE_CLOUD_API=true, FALLBACK=false  → 云端仅可被请求体显式 provider=cloud 调用
  L2  两者皆 true                             → 未来才允许自动 fallback（本次不实现），届时必须走 acquire_fallback

职责边界（本次只做闸门与预扣，不做自动降级）：
  * acquire_explicit : 显式云端调用入口 = ENABLE_CLOUD_API + 预算硬约束（llm_proxy 已接线）
  * acquire_fallback : 未来 fallback 入口 = 再加 ENABLE_CLOUD_FALLBACK 检查（已实现+单测，未接线）
  * settle           : 实际费用真值化（已实现+单测；llm_proxy 响应路径本次不接）
  * reset_circuit    : 手动/测试重置当日熔断
  * get_daily_state  : 当日状态快照（startup_budget_note 只读报告用）

fail-closed 边界（校准 3）：预算库打不开/事务失败 → BudgetStateUnavailable，绝不放行；
  该语义只用于 acquire_explicit / acquire_fallback 路径。get_daily_state 允许抛
  BudgetStateUnavailable 由调用方（startup_budget_note）如实报告，不炸启动。

状态持久化：独立 SQLite（config.MORAY_BUDGET_DB，默认 D:\\MoRay\\data\\cloud_budget.db）。
建表在本模块自带（CREATE TABLE IF NOT EXISTS，与 db.py SCHEMA 同风格）——按校准 1，
db.py 零改动；禁令：不得用内存变量存熔断状态（全部在 budget_daily / budget_reservations 表内）。

日期语义：budget_daily 按 date 一行。当日 tripped=1 闩死（即便额度恢复也要 reset_circuit()）；
跨自然日自然产生新行 = 额度与熔断一起清零（任务书测试 5/8 的两条语义由此同时成立）。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from . import config

logger = logging.getLogger("moray.cloud_budget")

__all__ = ["CloudPermit", "CloudBudgetError", "CloudDisabled", "CloudNotForFallback",
           "BudgetExceeded", "BudgetStateUnavailable",
           "acquire_explicit", "acquire_fallback", "settle", "refund",
           "reset_circuit", "get_daily_state"]


class CloudBudgetError(Exception):
    """云端预算闸门异常基类。"""


class CloudDisabled(CloudBudgetError):
    """ENABLE_CLOUD_API=false：云端 LLM 不存在于调度链（L0）。"""


class CloudNotForFallback(CloudBudgetError):
    """ENABLE_CLOUD_FALLBACK=false：云端不可作为 fallback 目标（L1）。"""


class BudgetExceeded(CloudBudgetError):
    """预算超限或当日熔断已打开。"""


class BudgetStateUnavailable(CloudBudgetError):
    """预算状态不可读/事务失败（fail-closed：acquire 路径绝不放行）。"""


@dataclass
class CloudPermit:
    permit_id: str
    reserved_usd: float
    trace_id: str


# --------------------------------------------------------------------------
# 存储层：独立预算库（校准 1：db.py 零改动，本模块自建表）
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_daily (
  date       TEXT PRIMARY KEY,
  total_usd  REAL NOT NULL DEFAULT 0,
  call_count INTEGER NOT NULL DEFAULT 0,
  tripped    INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_reservations (
  permit_id    TEXT PRIMARY KEY,
  trace_id     TEXT NOT NULL,
  reserved_usd REAL NOT NULL,
  settled_usd  REAL,
  status       TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
"""


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _budget_db_path() -> str:
    path = str(config.MORAY_BUDGET_DB or "").strip()
    if not path:
        # 配置缺失也按 fail-closed 处理（宁可拒绝云端，不可无账本放行）
        raise BudgetStateUnavailable("MORAY_BUDGET_DB 未配置，预算账本不可用（fail-closed）")
    return path


def _connect() -> sqlite3.Connection:
    path = _budget_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # isolation_level=None：显式 BEGIN IMMEDIATE / COMMIT / ROLLBACK，杜绝隐式事务歧义
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """事务清理路径：ROLLBACK 本身失败只落 warning（原异常会带上下文继续上抛，不吞）。"""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error as e:
        logger.warning("[cloud_budget] ROLLBACK 失败（清理路径）：%s", e)


def _emit_event(event_type: str, payload: dict) -> None:
    """事件写主库 event_snapshots（复用 crud.append_event_snapshot，签名实测匹配）。

    预算事务在此之前已 COMMIT，事件属可观测性旁路：写失败只 warning 不影响预算判定
    ——与 llm_proxy._audit_budget_exceeded（L101-112）同一既有惯例，非吞异常。
    """
    try:
        from . import crud  # 懒加载：与 llm_proxy._audit_budget_exceeded 同款，避免拖轻量导入
        crud.append_event_snapshot(event_type,
                                   entity_id=str(payload.get("trace_id") or "")[:128],
                                   snapshot=payload)
    except Exception as e:  # noqa: BLE001 - 审计旁路失败不影响预算主流程（同 _audit_budget_exceeded 惯例）
        logger.warning("[cloud_budget] 事件 %s 写入失败（预算状态不受影响）：%s: %s",
                       event_type, type(e).__name__, e)


# --------------------------------------------------------------------------
# 预扣 / 结算 / 熔断
# --------------------------------------------------------------------------

def acquire_explicit(trace_id: str, est_cost_usd: float) -> CloudPermit:
    """显式云端调用入口（校准 2）：检查 ENABLE_CLOUD_API + 预算，不检查 fallback 开关。"""
    return _acquire_common(trace_id, est_cost_usd, require_fallback_flag=False)


def acquire_fallback(trace_id: str, est_cost_usd: float) -> CloudPermit:
    """未来 fallback 入口（校准 2）：再加 ENABLE_CLOUD_FALLBACK 检查。本次实现+单测，不接线。"""
    return _acquire_common(trace_id, est_cost_usd, require_fallback_flag=True)


def _acquire_common(trace_id: str, est_cost_usd: float, require_fallback_flag: bool) -> CloudPermit:
    if not config.ENABLE_CLOUD_API:
        raise CloudDisabled("ENABLE_CLOUD_API=false：云端 LLM 不存在于调度链（L0）")
    if require_fallback_flag and not config.ENABLE_CLOUD_FALLBACK:
        raise CloudNotForFallback("ENABLE_CLOUD_FALLBACK=false：云端不可作为 fallback 目标（L1）")
    try:
        est = float(est_cost_usd)
    except (TypeError, ValueError) as e:
        raise ValueError("est_cost_usd 必须是数字，实得 %r" % (est_cost_usd,)) from e
    if not est >= 0:  # 同时拦截负数与 NaN（NaN 参与任何比较均为 False）
        raise ValueError("est_cost_usd 必须 >= 0，实得 %r" % (est_cost_usd,))

    today = _today()
    try:
        conn = _connect()
    except (sqlite3.Error, OSError) as e:
        raise BudgetStateUnavailable("预算库打不开（fail-closed）：%s: %s" % (type(e).__name__, e)) from e
    try:
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
        except (sqlite3.Error, OSError) as e:
            raise BudgetStateUnavailable("预算库初始化/加锁失败（fail-closed）：%s: %s"
                                         % (type(e).__name__, e)) from e
        try:
            permit = _acquire_tx(conn, today, trace_id, est)
        except BudgetExceeded:
            raise  # 事务已在判定路径内 COMMIT/ROLLBACK，直接上抛给调用方
        except (sqlite3.Error, OSError) as e:
            _safe_rollback(conn)
            raise BudgetStateUnavailable("预算库事务失败（fail-closed）：%s: %s"
                                         % (type(e).__name__, e)) from e
    finally:
        conn.close()
    state = get_daily_state()
    _emit_event("CloudCallReserved", {
        "trace_id": trace_id, "permit_id": permit.permit_id,
        "est_cost_usd": est, "actual_cost_usd": None,
        "daily_total": state["total_usd"], "call_count": state["call_count"],
        "tripped": state["tripped"]})
    return permit


def _acquire_tx(conn: sqlite3.Connection, today: str, trace_id: str, est: float) -> CloudPermit:
    """BEGIN IMMEDIATE 事务体：读当日行 → 熔断闩检查 → 三重限额检查 → 预扣。"""
    row = conn.execute("SELECT total_usd, call_count, tripped FROM budget_daily WHERE date = ?",
                       (today,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO budget_daily (date, total_usd, call_count, tripped, updated_at) "
                     "VALUES (?, 0, 0, 0, ?)", (today, _now()))
        total_usd, call_count, tripped = 0.0, 0, 0
    else:
        total_usd, call_count, tripped = float(row["total_usd"]), int(row["call_count"]), int(row["tripped"])

    if tripped:
        conn.execute("ROLLBACK")
        raise BudgetExceeded("今日云端预算熔断已打开（budget_daily.tripped=1，date=%s）："
                             "需 reset_circuit() 或等跨日" % today)

    reasons = []
    if est > float(config.PER_CALL_BUDGET_USD):
        reasons.append("单次预估 %.4f 超过 PER_CALL_BUDGET_USD=%.4f"
                       % (est, float(config.PER_CALL_BUDGET_USD)))
    if total_usd + est > float(config.DAILY_BUDGET_USD):
        reasons.append("今日累计 %.4f + 本次预估 %.4f 超过 DAILY_BUDGET_USD=%.4f"
                       % (total_usd, est, float(config.DAILY_BUDGET_USD)))
    if call_count >= int(config.DAILY_MAX_CALLS):
        reasons.append("今日调用次数 %d 已达 DAILY_MAX_CALLS=%d"
                       % (call_count, int(config.DAILY_MAX_CALLS)))
    if reasons:
        conn.execute("UPDATE budget_daily SET tripped = 1, updated_at = ? WHERE date = ?",
                     (_now(), today))
        conn.execute("COMMIT")
        _emit_event("CloudBudgetTripped", {
            "trace_id": trace_id, "permit_id": None,
            "est_cost_usd": est, "actual_cost_usd": None,
            "daily_total": total_usd, "call_count": call_count, "tripped": True,
            "reason": "; ".join(reasons)})
        raise BudgetExceeded("; ".join(reasons))

    permit_id = uuid.uuid4().hex
    conn.execute("INSERT INTO budget_reservations "
                 "(permit_id, trace_id, reserved_usd, settled_usd, status, created_at) "
                 "VALUES (?, ?, ?, NULL, 'reserved', ?)", (permit_id, trace_id, est, _now()))
    conn.execute("UPDATE budget_daily SET total_usd = ?, call_count = ?, updated_at = ? WHERE date = ?",
                 (total_usd + est, call_count + 1, _now(), today))
    conn.execute("COMMIT")
    return CloudPermit(permit_id=permit_id, reserved_usd=est, trace_id=trace_id)


def settle(permit: CloudPermit, actual_cost_usd: float) -> None:
    """按实际费用真值化：total_usd 反映 actual 而非 reserved（测试 9）。

    语义（任务二点五钉死）：actual=0.0 合法，表示全额退款（total 扣回全部预扣）；
    actual > reserved 时差额追加计入当日 total（多发生的成本要补记），total_usd
    恒有下限 0.0 不为负。结算后 budget_reservations.status='settled'、settled_usd=actual。
    差额计入结算当日（budget_daily 当日行）：同日预扣-结算是主路径；
    跨日结算的差额也落在结算当日（本口径已在注释钉死，避免隐式歧义）。
    """
    if not isinstance(permit, CloudPermit):
        raise CloudBudgetError("settle 需要 CloudPermit，实得 %r" % (permit,))
    try:
        actual = float(actual_cost_usd)
    except (TypeError, ValueError) as e:
        raise ValueError("actual_cost_usd 必须是数字，实得 %r" % (actual_cost_usd,)) from e
    if not actual >= 0:
        raise ValueError("actual_cost_usd 必须 >= 0，实得 %r" % (actual_cost_usd,))

    today = _today()
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            res = conn.execute("SELECT status FROM budget_reservations WHERE permit_id = ?",
                               (permit.permit_id,)).fetchone()
            if res is None:
                conn.execute("ROLLBACK")
                raise CloudBudgetError("结算失败：permit %s 不存在" % permit.permit_id)
            if str(res["status"]) == "settled":
                conn.execute("ROLLBACK")
                raise CloudBudgetError("结算失败：permit %s 已结算（不可重复结算）" % permit.permit_id)
            row = conn.execute("SELECT total_usd, call_count, tripped FROM budget_daily WHERE date = ?",
                               (today,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO budget_daily (date, total_usd, call_count, tripped, updated_at) "
                             "VALUES (?, 0, 0, 0, ?)", (today, _now()))
                total_usd, call_count, tripped = 0.0, 0, 0
            else:
                total_usd, call_count, tripped = float(row["total_usd"]), int(row["call_count"]), bool(int(row["tripped"]))
            new_total = max(0.0, total_usd - float(permit.reserved_usd) + actual)  # 账面不为负
            conn.execute("UPDATE budget_daily SET total_usd = ?, updated_at = ? WHERE date = ?",
                         (new_total, _now(), today))
            conn.execute("UPDATE budget_reservations SET settled_usd = ?, status = 'settled' "
                         "WHERE permit_id = ?", (actual, permit.permit_id))
            conn.execute("COMMIT")
        except CloudBudgetError:
            raise
        except (sqlite3.Error, OSError) as e:
            _safe_rollback(conn)
            raise BudgetStateUnavailable("结算事务失败：%s: %s" % (type(e).__name__, e)) from e
    finally:
        conn.close()
    _emit_event("CloudCallSettled", {
        "trace_id": permit.trace_id, "permit_id": permit.permit_id,
        "est_cost_usd": float(permit.reserved_usd), "actual_cost_usd": actual,
        "daily_total": new_total, "call_count": call_count, "tripped": tripped})


def refund(permit: CloudPermit, reason: str) -> None:
    """[2026-10-01 Z-3] 上游失败时回冲预扣。与 settle 的区别：
    settle 是成功路径（actual_cost 可能 >0，差额补记），refund 是失败路径（全额回冲）。
    回冲内容：budget_daily.total_usd -= permit.reserved_usd（下限 0.0，与 settle 同口径），
              budget_reservations.status = 'refunded', settled_usd = 0.0。
    不回冲 call_count（失败也计入次数，防刷）。
    写事件 CloudCallRefunded（复用 crud.append_event_snapshot，与 CloudCallSettled 同款旁路）。

    幂等保证（两态都只生效一次）：
      * permit 已 refunded → 直接返回（不重复回冲）；
      * permit 已 settled  → 不回冲，写 WARNING 后返回。真实场景：llm_proxy._stream
        先 settle 成功、随后 aiter_bytes 中断进外层异常 → 再调 refund 不得二次回冲。
    事务范式与 settle 完全一致（BEGIN IMMEDIATE / _safe_rollback / fail-closed 上抛）。"""
    if not isinstance(permit, CloudPermit):
        raise CloudBudgetError("refund 需要 CloudPermit，实得 %r" % (permit,))
    reason_text = str(reason or "").strip() or "unspecified"

    today = _today()
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            res = conn.execute("SELECT status FROM budget_reservations WHERE permit_id = ?",
                               (permit.permit_id,)).fetchone()
            if res is None:
                conn.execute("ROLLBACK")
                raise CloudBudgetError("回冲失败：permit %s 不存在" % permit.permit_id)
            status = str(res["status"])
            if status == "refunded":
                conn.execute("ROLLBACK")
                logger.info("[cloud_budget] refund 幂等：permit %s 已回冲，忽略重复调用",
                            permit.permit_id)
                return
            if status == "settled":
                conn.execute("ROLLBACK")
                logger.warning("[cloud_budget] refund 幂等保护：permit %s 已结算（settle 在先），"
                               "不重复回冲", permit.permit_id)
                return
            row = conn.execute("SELECT total_usd, call_count, tripped FROM budget_daily WHERE date = ?",
                               (today,)).fetchone()
            if row is None:
                # 跨日回冲：当日行缺失则新建（差额落结算当日——与 settle 钉死的跨日口径一致）
                conn.execute("INSERT INTO budget_daily (date, total_usd, call_count, tripped, updated_at) "
                             "VALUES (?, 0, 0, 0, ?)", (today, _now()))
                total_usd, call_count, tripped = 0.0, 0, 0
            else:
                total_usd, call_count, tripped = float(row["total_usd"]), int(row["call_count"]), bool(int(row["tripped"]))
            new_total = max(0.0, total_usd - float(permit.reserved_usd))  # 账面不为负
            conn.execute("UPDATE budget_daily SET total_usd = ?, updated_at = ? WHERE date = ?",
                         (new_total, _now(), today))
            conn.execute("UPDATE budget_reservations SET settled_usd = 0.0, status = 'refunded' "
                         "WHERE permit_id = ?", (permit.permit_id,))
            conn.execute("COMMIT")
        except CloudBudgetError:
            raise
        except (sqlite3.Error, OSError) as e:
            _safe_rollback(conn)
            raise BudgetStateUnavailable("回冲事务失败：%s: %s" % (type(e).__name__, e)) from e
    finally:
        conn.close()
    _emit_event("CloudCallRefunded", {
        "trace_id": permit.trace_id, "permit_id": permit.permit_id,
        "est_cost_usd": float(permit.reserved_usd), "actual_cost_usd": 0.0,
        "daily_total": new_total, "call_count": call_count, "tripped": tripped,
        "reason": reason_text})
    logger.info("[cloud_budget] 回冲完成 permit=%s reserved=%.4f reason=%s（call_count 不回冲）",
                permit.permit_id, float(permit.reserved_usd), reason_text)


def reset_circuit() -> None:
    """手动重置当日熔断（仅测试与人工介入用；跨日由日期行自然清零，无需调用）。"""
    today = _today()
    try:
        conn = _connect()
    except (sqlite3.Error, OSError) as e:
        raise BudgetStateUnavailable("预算库打不开：%s: %s" % (type(e).__name__, e)) from e
    try:
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE budget_daily SET tripped = 0, updated_at = ? WHERE date = ?",
                         (_now(), today))
            conn.execute("COMMIT")
        except (sqlite3.Error, OSError) as e:
            _safe_rollback(conn)
            raise BudgetStateUnavailable("熔断重置事务失败：%s: %s" % (type(e).__name__, e)) from e
    finally:
        conn.close()
    logger.warning("[cloud_budget] 云端预算熔断已手动重置（date=%s）", today)


def get_daily_state() -> dict:
    """当日状态快照 {date, total_usd, call_count, tripped}，供 startup_budget_note 只读报告。

    读失败抛 BudgetStateUnavailable（校准 3：由调用方如实报告，不炸启动）。
    """
    today = _today()
    conn = _connect()
    try:
        try:
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT total_usd, call_count, tripped FROM budget_daily "
                               "WHERE date = ?", (today,)).fetchone()
        except (sqlite3.Error, OSError) as e:
            raise BudgetStateUnavailable("预算库不可读：%s: %s" % (type(e).__name__, e)) from e
    finally:
        conn.close()
    if row is None:
        return {"date": today, "total_usd": 0.0, "call_count": 0, "tripped": False}
    return {"date": today, "total_usd": float(row["total_usd"]),
            "call_count": int(row["call_count"]), "tripped": bool(int(row["tripped"]))}
