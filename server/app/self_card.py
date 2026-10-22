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

#: [心智状态机 2026-10-22] 心智状态来源 kind（self_narrative stage2 落库产物，
#: content=JSON 串 {"narrative","mental_state"}；10-14 写入矩阵预埋）
_NARRATIVE_KIND = "self_narrative"


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
    ms = state.get("mental_state")
    if isinstance(ms, dict) and (ms.get("focus_shift") or ms.get("recent_failures")):
        # [心智状态机 2026-10-22] 独立标题框住：状态属"背景事实"，措辞自证仍禁外推，
        # 不给卡尾"禁止外推"红线开缺口。定长截断保确定性（test_self_card 纯函数红线）。
        lines.append("- 心智状态（自省叙事产出·背景事实，仍禁外推）：")
        focus = str(ms.get("focus_shift") or "").strip()[:120]
        fails = ms.get("recent_failures")
        fail_parts = []
        if isinstance(fails, list):
            for x in fails[:3]:
                s = str(x).strip()[:40]
                if s:
                    fail_parts.append(s)
        lines.append("  最近关注点：%s（历史失败：%s）"
                     % (focus or "无", "、".join(fail_parts) or "无"))
    up = state.get("uptime_hours")
    if up is not None:
        lines.append(f"- 本次运行：{up:.1f} 小时")
    lines.append("")
    lines.append("被问及自身时，只复述以上事实，禁止外推。")
    return "\n".join(lines)


def render_mental_context(state: dict) -> str:
    """[v3 心智反哺 2026-10-23] 纯函数：db_state → 执行引擎心智块；无可用心智返回 ""。

    与 render_self_card 姿态相反：事实卡收在"仅复述禁外推"（被动），执行引擎在干活，
    要的是行为约束（引以为戒）——预研红线：直接复用事实卡反而抑制行动，故独立渲染。
    返回 "" 时调用方零注入（ZCode prompt 文件逐字保持原样，保住逐字断言）。
    定长截断保确定性（同 render_self_card 红线）。
    """
    ms = state.get("mental_state")
    if not isinstance(ms, dict):
        return ""
    focus = str(ms.get("focus_shift") or "").strip()[:120]
    fails = ms.get("recent_failures")
    fail_parts = []
    if isinstance(fails, list):
        for x in fails[:3]:
            s = str(x).strip()[:40]
            if s:
                fail_parts.append(s)
    if not focus and not fail_parts:
        return ""
    return ("[背景状态]\n你最近的心智关注点是：%s（历史失败：%s）。"
            "在执行任务时，请引以为戒。"
            % (focus or "无", "、".join(fail_parts) or "无"))


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
                # [心智状态机 2026-10-22] 最新一条 self_narrative → JSON 解析取
                # mental_state；无行/解析失败/无该键一律不置键（render 条件行自然
                # 不出现，fail-open 与上方同款）。参数绑定，禁拼接 SQL。
                cur.execute(
                    "SELECT content FROM memory_bank WHERE kind = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (_NARRATIVE_KIND,)
                )
                nar_row = cur.fetchone()
                if nar_row and nar_row[0]:
                    try:
                        nar_obj = json.loads(nar_row[0])
                    except ValueError:
                        nar_obj = None
                    if isinstance(nar_obj, dict):
                        ms = nar_obj.get("mental_state")
                        if isinstance(ms, dict) and ms:
                            state["mental_state"] = ms
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
