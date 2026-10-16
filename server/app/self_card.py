# -*- coding: utf-8 -*-
"""[桥三事实卡 2026-10-15] 纯函数 render_self_card——零 LLM，可单测。

方案依据：《MoRay 自我模型 · 从 0 到 1 实施方案 v1.0》第 1 行 + 3.1 约束。
但只引用 MoRay 现有字段——方案原版的 db.telemetry() / self_consistency() 等在
MoRay 里不存在，不硬塞（委托信已标三处硬伤）。
"""
import datetime
import json
import os
import sqlite3


def render_self_card(state: dict) -> str:
    """纯函数：db_state → 事实卡字符串。

    每行必须是 state 的确定性函数，禁硬编码（方案 3.1 红线）。
    同一 state 连调 N 次必须输出一致。
    """
    lines = ["你是 MoRay。"]
    lines.append(f"- 累计记忆：{state.get('memory_total', 0)} 条")
    by_kind = state.get("memory_by_kind", {})
    if by_kind:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
        lines.append(f"  按类型：{parts}")
    lines.append(f"- 对话数：{state.get('conversations', 0)} 条")
    lines.append(f"- 消息数：{state.get('messages', 0)} 条")
    obs = state.get("self_observation", 0)
    lines.append(
        f"- 自省观察：{obs} 条" + ("（桥一/桥二后台自省循环产出）" if obs > 0 else "")
    )
    recent = state.get("recent_self")
    if recent:
        lines.append(f"- 最近自我模型：{recent[:120]}")
    up = state.get("uptime_hours")
    if up is not None:
        lines.append(f"- 本次运行：{up:.1f} 小时")
    lines.append("")
    lines.append("被问及自身时，只复述以上事实，禁止外推。")
    return "\n".join(lines)


def collect_db_state() -> dict:
    """读三处，拼成 render_self_card 的输入。IO 与渲染分离。"""
    from . import config
    state = {}

    # 1. memory_bank
    # [侦察修正 2026-10-15] config 实名 = MEMORY_DB（str）；spec 原稿字段名照抄
    # + 源级容错：单源失败不拖垮整卡（与下方主库计数同模式）。
    mem_db = getattr(config, "MEMORY_DB", None)
    if mem_db and os.path.exists(mem_db):
        try:
            conn = sqlite3.connect(mem_db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM memory_bank")
                state["memory_total"] = cur.fetchone()[0]
                cur.execute("SELECT kind, COUNT(*) FROM memory_bank GROUP BY kind")
                state["memory_by_kind"] = dict(cur.fetchall())
                cur.execute(
                    "SELECT COUNT(*) FROM memory_bank WHERE kind='self_observation'"
                )
                state["self_observation"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT content FROM memory_bank WHERE kind='self' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    state["recent_self"] = row[0]
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass

    # 2. moray 主库（对话数 / 消息数）
    # [侦察修正 2026-10-15] config 实名 = SQLITE_PATH（Path）；spec 原稿猜的
    # MORAY_DB / DB_PATH 皆不存在。固定 SQL 字符串（Mimosa 闸：禁 f-string 拼表名）。
    moray_db = getattr(config, "SQLITE_PATH", None)
    if moray_db and os.path.exists(moray_db):
        try:
            conn = sqlite3.connect(moray_db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM conversations")
                state["conversations"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM messages")
                state["messages"] = cur.fetchone()[0]
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            pass

    # 3. 进程启动时间 → uptime_hours
    try:
        import psutil  # 若装了
        import time
        pid = os.getpid()
        p = psutil.Process(pid)
        state["uptime_hours"] = (time.time() - p.create_time()) / 3600.0
    except Exception:
        # 退化：用模块导入时间近似
        if not hasattr(collect_db_state, "_boot"):
            collect_db_state._boot = datetime.datetime.now()
        state["uptime_hours"] = (
            datetime.datetime.now() - collect_db_state._boot
        ).total_seconds() / 3600.0

    return state
