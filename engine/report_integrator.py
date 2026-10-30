# -*- coding: utf-8 -*-
"""ReportIntegrator：L7.0 任务自治引擎 · 结果整合与经验沉淀（阶段5，2026-09-29）。

* integrate(plan, run)：全部子任务 → 一份五段式最终报告
  （总览 → 执行过程 → 发现的问题 → 处理结果 → 建议），Markdown 纯文本。
* sediment(plan, run)：全流程经验沉淀到统一知识库（source="自治任务"，success 按 outcome）
  + 计划摘要落盘 work/autonomous/{id}/summary.json（自我评估统计经经验库自然反映）。
零 LLM、零新依赖；任何异常由调用方（scheduler）兜底。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

SOURCE_AUTONOMOUS = "自治任务"


def integrate(plan: Dict[str, Any], run) -> str:
    """五段式最终报告（run: AutonomousRun）。"""
    plan_id = run.plan_id
    goal = run.goal or "（未记录）"
    subs = run.subtasks
    oks = [s for s in subs if s.get("status") == "ok"]
    outcome_label = {"all_ok": "全部成功", "partial": "部分成功",
                     "failed": "未成功", "": "进行中"}.get(run.outcome, run.outcome or "—")
    L: list = []
    L.append("# MoRay 自治任务最终报告 · %s" % plan_id)
    L.append("")
    L.append("## 1. 总览")
    L.append("- 目标：%s" % goal)
    L.append("- 计划来源：%s（%s）" % ((plan or {}).get("source") or "?",
                                      " → ".join(t.get("title") or t.get("type") or "?"
                                                 for t in (plan or {}).get("tasks") or [])))
    L.append("- 执行：%d/%d 个子任务成功，总耗时 %ss，结论：**%s**"
             % (len(oks), len(subs), run.view().get("duration_sec"), outcome_label))
    if run.decisions:
        L.append("- 动态决策 %d 次（详见执行过程）" % len(run.decisions))

    L.append("")
    L.append("## 2. 执行过程")
    for i, s in enumerate(subs, 1):
        mark = "✅" if s.get("status") == "ok" else "❌"
        L.append("%d. %s %s（%sms）%s" % (i, mark, s.get("summary") or s.get("title"),
                                          s.get("duration_ms") or 0,
                                          ("　自动插入" if s.get("auto_inserted") else "")))
        if s.get("error"):
            L.append("   - 错误：%s" % str(s["error"])[:200])
        if s.get("human_intervention"):
            L.append("   - 人工介入提示：%s" % str(s["human_intervention"])[:200])
    if run.decisions:
        L.append("")
        L.append("### 分支决策留痕")
        for d in run.decisions:
            L.append("- [%s] %s　依据：%s　→ %s"
                     % (d.get("type"), d.get("reason"),
                        str(d.get("basis") or "")[:120], d.get("result")))

    L.append("")
    L.append("## 3. 发现的问题")
    problems = _collect_problems(run)
    if problems:
        for p in problems[:10]:
            L.append("- %s" % p)
    else:
        L.append("- 未发现需要处理的问题。")

    L.append("")
    L.append("## 4. 处理结果")
    for s in subs:
        if s.get("type") == "fix":
            L.append("- 修复（%s）：%s" % (s.get("status"),
                                           str(s.get("diff") or "")[:200] or "见子任务报告"))
        if s.get("type") == "test":
            L.append("- 测试：%s（exit=%s）" % ("通过" if s.get("tests_passed") else "未通过/未跑",
                                               s.get("test_exit_code")))
        if s.get("type") == "logs":
            L.append("- 日志：%s" % (s.get("summary") or "（无摘要）"))
        if s.get("type") == "review" and s.get("report_text"):
            L.append("- 审查报告（节选）：%s" % str(s["report_text"])[:300].replace("\n", " "))

    L.append("")
    L.append("## 5. 建议")
    L.extend(_suggestions(run))
    L.append("")
    L.append("—— 由 MoRay 任务自治引擎（engine/execution_scheduler.py + report_integrator.py）自动生成")
    return "\n".join(L)


def _collect_problems(run) -> list:
    """从子任务结果聚合"发现的问题"行（审查新发现 + 测试失败 + 日志告警）。"""
    out: list = []
    try:
        from knowledge import issue_base as _ib
        pid = str(((run.plan or {}).get("context") or {}).get("project_id") or "")
        for r in _ib.IssueBase().open_review_issues(pid, limit=10):
            out.append("[审查] %s（%s）" % (str(r.get("root_cause_category") or "")[:120],
                                            r.get("issue_kind") or "?"))
    except Exception:  # noqa: BLE001
        pass
    for s in run.subtasks:
        if s.get("type") == "test" and s.get("tests_passed") is False:
            out.append("[测试失败] %s" % str(s.get("fail_text") or "")[:160].replace("\n", " "))
        if s.get("type") == "logs":
            for b in (s.get("brief") or []):
                out.append("[日志] %s（%s/%s）" % (str(b.get("first_line") or "")[:120],
                                                   b.get("error_type") or "?",
                                                   b.get("severity") or "?"))
    return out


def _suggestions(run) -> list:
    out: list = []
    if run.outcome == "all_ok":
        out.append("- 全链路成功：建议把本次修复模式沉淀的根因纳入常规回归。")
    elif run.outcome == "partial":
        out.append("- 存在未完全成功的子任务：优先查看执行过程里的 ❌ 项与错误详情。")
    else:
        out.append("- 全链路未成功：建议把目标拆小后重试，或改用单一模板任务人工跟进。")
    if any(d.get("type") == "test→report" and "不再自动重试" in str(d.get("reason"))
           for d in run.decisions):
        out.append("- 自动重试已达上限：测试失败详情见第 3/4 节，建议人工定位。")
    out.append("- 本次执行已沉淀到统一问题库（来源：自治任务），后续排错/审查/测试自动受益。")
    return out


def sediment(plan: Dict[str, Any], run) -> Dict[str, Any]:
    """全流程经验沉淀：一条自治任务级经验 + 计划摘要落盘。绝不抛异常。"""
    out: Dict[str, Any] = {"recorded": False, "summary_path": ""}
    try:
        from knowledge import issue_base as _ib
        pid = str(((plan or {}).get("context") or {}).get("project_id") or "")
        causes = []
        for s in run.subtasks:
            if s.get("type") == "review" and s.get("report_text"):
                causes.append("审查完成")
            if s.get("type") == "test":
                causes.append("测试通过" if s.get("tests_passed") else "测试未通过")
            if s.get("type") == "fix":
                causes.append("修复%s" % ("成功" if s.get("status") == "ok" else "失败"))
        rec = _ib.IssueBase().record_issue(
            {"project_id": pid or "unknown",
             "error_type": "autonomous-run",
             "root_cause_category": "自治任务：%s（%s）" % (
                 str(run.goal or "")[:80], "、".join(causes) or "无子任务"),
             "error_snippet": "；".join(d.get("type") or "" for d in run.decisions)[:400],
             "solution": str(run.report or "")[:1500],
             "duration_sec": round((run.ended_at or 0) - run.t0, 1) if run.ended_at else None,
             "success": run.outcome == "all_ok"},
            source=SOURCE_AUTONOMOUS, issue_kind=run.outcome or "partial",
            applicable="通用", file_type="")
        out["recorded"] = rec is not None
        summary = {"plan_id": run.plan_id, "goal": run.goal,
                   "outcome": run.outcome, "status": run.status,
                   "subtasks": [{"type": s.get("type"), "status": s.get("status")}
                                for s in run.subtasks],
                   "decisions": len(run.decisions),
                   "duration_sec": run.view().get("duration_sec"),
                   "recorded_at": datetime.now(timezone.utc).astimezone()
                   .isoformat(timespec="seconds")}
        p = run.work_dir / "summary.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(__import__("json").dumps(summary, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
        out["summary_path"] = str(p)
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
