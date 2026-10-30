# -*- coding: utf-8 -*-
"""ExecutionScheduler：L7.0 任务自治引擎 · 执行调度与动态决策（阶段3+4，2026-09-29）。

设计原则（任务书 3.3 / 红线 2）：
  * 原子单元一个字节不改——调度器只调 `TaskTemplateRunner(tpl, params).run()` 公开接口
    （runner_factory 可注入 → 单测全离线）与 log_monitor 公开方法；
    L6.5 全部钩子（知识注入/收割/匹配/源标记/建议步）在 runner 内部自动继承。
  * 调度器只做编排与决策，不做执行逻辑。

动态决策（阶段4，全部留痕 decisions[]：at/type/reason/basis/result）：
  * 审查发现严重问题（高严重度的新审查发现）→ 自动插修复；无重大问题 → 跳过修复直测；
  * 测试通过 → 出报告；失败 → 自动排错修复后重测；
  * 循环控制：修复→测试 最多 MAX_FIX_LOOP(2) 轮，达限如实出报告（不假装通过）；
  * 风险判断：修复 diff 涉及核心文件（复用 strategy_optimizer.is_core_file）→ 强制验证。
总闸：MORAY_TASK_PLANNER 默认关（task_planner.planner_on）。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from engine.task_planner import planner_on
from engine.task_runner import TaskTemplateRunner, default_test_cmd
from engine import laya_connector as laya_conn   # [L7.5 2026-09-29] 旁路引用（纯追加导入，未动上行两行）

MAX_FIX_LOOP = 2               # 修复→测试 最大自动循环轮数
MAX_AUTO_TASKS = 12            # 动态插入的总任务上限（防御失控计划）
VERIFY_DEFAULT = "python -m py_compile main.py && python main.py"

_TPL_OF = {"review": "code_review_flow", "fix": "text_fix_flow", "test": "test_run_flow"}


def _default_factory(tpl_name: str, params: Dict[str, Any], work_dir: str) -> TaskTemplateRunner:
    return TaskTemplateRunner(str(Path(__file__).resolve().parents[1] / "templates"
                                  / (tpl_name + ".yaml")),
                              params=params, work_dir=work_dir)


class AutonomousRun:
    """自治任务状态容器（统一管理所有子任务状态/进度/结果/决策，任务书 3.2）。"""

    def __init__(self, plan: Dict[str, Any], work_dir: Optional[str] = None):
        self.plan = plan
        self.plan_id = str(plan.get("plan_id") or "unknown")
        self.goal = str(plan.get("goal") or "")
        self.status = "pending"          # pending → running → completed | failed
        self.outcome = ""                # all_ok | partial | failed（终态语义）
        self.subtasks: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.report = ""
        self.error = ""
        self.t0 = time.time()
        self.ended_at: Optional[float] = None
        self.work_dir = Path(work_dir or (Path(__file__).resolve().parents[1] / "work"
                                          / "autonomous" / self.plan_id))
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def add_decision(self, dtype: str, reason: str, basis: str, result: str) -> None:
        """决策留痕（任务书 4.3：原因/依据/结果，可回溯可审计）。"""
        self.decisions.append({
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "type": dtype, "reason": reason, "basis": basis[:400], "result": result[:200]})

    def view(self) -> Dict[str, Any]:
        """统一出参快照（轮询端点/前端复用任务卡范式）。"""
        return {
            "plan_id": self.plan_id, "goal": self.goal,
            "status": self.status, "outcome": self.outcome,
            "plan_source": (self.plan or {}).get("source", ""),
            "subtasks": self.subtasks, "decisions": self.decisions,
            "report": self.report[:12000], "error": self.error,
            "duration_sec": round((self.ended_at or time.time()) - self.t0, 1),
            "work_dir": str(self.work_dir),
        }

    def persist(self) -> None:
        try:
            tmp = self.work_dir / "run.json.tmp"
            tmp.write_text(json.dumps(self.view(), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.work_dir / "run.json")
        except OSError:
            pass  # 状态落盘失败绝不影响执行


class ExecutionScheduler:
    def __init__(self, runner_factory: Optional[Callable[..., Any]] = None,
                 max_fix_loop: int = MAX_FIX_LOOP):
        self._factory = runner_factory or _default_factory
        self.max_fix_loop = max(1, int(max_fix_loop))
        self._auto_seq = 0            # 动态插入任务的唯一序号（id 不碰撞）

    def _next_auto_id(self, kind: str) -> str:
        self._auto_seq += 1
        return "auto_%s_%d" % (kind, self._auto_seq)

    # ---------------- 主循环（拓扑执行 + 动态决策） ----------------

    async def run_plan(self, plan: Dict[str, Any],
                       on_event: Optional[Callable[[Dict[str, Any]], None]] = None
                       ) -> AutonomousRun:
        run = AutonomousRun(plan)
        run.status = "running"
        self._emit(run, on_event)
        pid = str(((plan.get("context") or {}).get("project_id")) or "")
        pdir = str(((plan.get("context") or {}).get("project_dir")) or "")
        queue: List[Dict[str, Any]] = [dict(t) for t in (plan.get("tasks") or [])]
        fix_loops = 0
        test_done = False
        fix_done = False
        while queue:
            if len(run.subtasks) >= MAX_AUTO_TASKS:
                run.add_decision("guard", "任务数达到安全上限，停止自动扩展",
                                 "subtasks=%d" % len(run.subtasks), "跳出循环出报告")
                break
            task = queue.pop(0)
            ttype = str(task.get("type") or "")
            if ttype == "report":
                continue                      # 终结标记：整合器统一兑现
            if ttype == "review":
                # 快照：审查前已存在的审查发现（决策时只认"本次新增"）
                task["_known_issues"] = _snapshot_review_issues(pid)
            info = await self._execute(ttype, task, run, pid, pdir)
            run.subtasks.append(info)
            self._emit(run, on_event)
            # ---- 阶段4：逐类动态决策（改队列 = 改后续计划） ----
            if ttype == "review":
                queue = self._decide_after_review(task, info, run, queue, pid,
                                                  fix_done, test_done)
            elif ttype == "fix":
                fix_done = True
                queue, fix_loops = self._decide_after_fix(task, info, run, queue,
                                                          fix_loops, test_done)
            elif ttype == "test":
                test_done = True
                queue, fix_loops = self._decide_after_test(task, info, run, queue,
                                                           fix_loops, pdir, pid)
            elif ttype == "logs":
                run.add_decision("logs", "日志扫描完成，进入报告整合",
                                 "%d 条待处理告警" % int(info.get("alerts") or 0), "出报告")
            self._emit(run, on_event)
        # ---- 阶段5：结果整合与经验沉淀 ----
        try:
            from engine.report_integrator import integrate, sediment
            run.report = integrate(plan, run)
            sediment(plan, run)
            oks = [s for s in run.subtasks if s.get("status") == "ok"]
            run.outcome = ("all_ok" if oks and len(oks) == len(run.subtasks)
                           else ("partial" if oks else "failed"))
            run.status = "completed"
        except Exception as e:  # noqa: BLE001
            run.status = "failed"
            run.error = "整合失败：%s: %s" % (type(e).__name__, e)
        run.ended_at = time.time()
        run.persist()
        self._emit(run, on_event)
        return run

    def _emit(self, run: AutonomousRun, on_event) -> None:
        if on_event:
            try:
                on_event(run.view())
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 阶段3：类型 → 原子单元映射与执行 ----------------

    async def _execute(self, ttype: str, task: Dict[str, Any], run: AutonomousRun,
                       pid: str, pdir: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {"id": task.get("id"), "type": ttype,
                                "title": task.get("title") or ttype,
                                "status": "failed", "auto_inserted": bool(task.get("_auto")),
                                "summary": "", "duration_ms": 0}
        t0 = time.monotonic()
        try:
            if ttype in ("review", "fix", "test"):
                params: Dict[str, Any] = {"project_dir": pdir, "project_id": pid}
                if ttype == "fix":
                    params["error_text"] = str(task.get("error_text")
                                               or task.get("title") or "")[:3000]
                    params["verify_cmd"] = str(task.get("verify_cmd") or VERIFY_DEFAULT)
                if ttype == "test":
                    params["test_cmd"] = str(task.get("test_cmd")
                                             or default_test_cmd(Path(pdir)))
                if await laya_conn.bypass(ttype, task, run, pid, pdir, params, info, self._factory, self._summarize_progress): return info  # noqa: E501 [L7.5 2026-09-29 唯一一行旁路调用：False=下面原生逻辑逐字节照旧]
                runner = self._factory(_TPL_OF[ttype], params,
                                       str(run.work_dir / ("sub_%s" % (task.get("id") or "x"))))
                progress = await runner.run()
                info.update(self._summarize_progress(ttype, progress))
            elif ttype == "logs":
                info.update(await self._run_logs())
            else:
                info["error"] = "未知子任务类型：%s" % ttype
        except Exception as e:  # noqa: BLE001
            info["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        info["duration_ms"] = int((time.monotonic() - t0) * 1000)
        info.setdefault("summary", "%s（%s）" % (info.get("title") or ttype,
                                                info.get("status")))
        return info

    @staticmethod
    def _summarize_progress(ttype: str, progress: Dict[str, Any]) -> Dict[str, Any]:
        """progress dict → 子任务摘要（状态/报告文本/diff/测试退出码）。"""
        steps = progress.get("steps") or []
        out: Dict[str, Any] = {"status": "ok" if progress.get("status") == "completed"
                               and not progress.get("human_intervention") else "failed",
                               "engine_summary": "、".join(
                                   "%s:%s" % (s.get("id"), s.get("status"))
                                   for s in steps[:8])}
        rep = ""
        for s in steps:
            rp = (s.get("output") or {}).get("path") or ""
            if s.get("type") == "report" and rp:
                try:
                    rep += Path(rp).read_text(encoding="utf-8", errors="replace")[:4000]
                except OSError:
                    pass
        out["report_text"] = rep[:6000]
        if ttype == "fix":
            diff = ""
            for s in steps:
                diff = (s.get("output") or {}).get("diff") or diff
            out["diff"] = str(diff or "")[:4000]
        if ttype == "test":
            exit_code = None
            for s in steps:
                if s.get("id") == "run_tests":
                    exit_code = (s.get("output") or {}).get("exit_code")
            out["test_exit_code"] = exit_code
            out["tests_passed"] = (exit_code == 0)
            # 模板 progress 恒 completed（continue_on_fail 设计）——子任务状态必须按
            # 测试真实结果判定，否则"测试失败"会被 outcome 记成 all_ok（实测教训）。
            out["status"] = "ok" if out["tests_passed"] else "failed"
            tail = ""
            for s in steps:
                if s.get("id") == "run_tests":
                    o = s.get("output") or {}
                    tail = "%s\n%s" % (str(o.get("stdout") or "")[-1200:],
                                       str(o.get("stderr") or "")[-800:])
            out["fail_text"] = tail.strip()[:2000]
        if progress.get("human_intervention"):
            out["human_intervention"] = str(progress["human_intervention"])[:300]
        return out

    @staticmethod
    async def _run_logs() -> Dict[str, Any]:
        """查日志原子动作：scan_once + pending 告警摘要（Iris 分析结果已在告警内）。"""
        from server.app import log_monitor as lm
        await lm.manager.scan_once()
        alerts = lm.manager.pending_alerts()
        brief = []
        for a in alerts[:3]:
            ana = a.get("analysis") or {}
            brief.append({"first_line": str(a.get("first_line") or "")[:200],
                          "error_type": ana.get("error_type"),
                          "severity": ana.get("severity")})
        return {"status": "ok", "alerts": len(alerts),
                "summary": "；".join("%s（%s/%s）" % (b["first_line"][:80],
                                                     b.get("error_type") or "?",
                                                     b.get("severity") or "?")
                                     for b in brief) or "未发现待处理告警",
                "brief": brief}

    # ---------------- 阶段4：分支决策 ----------------

    def _decide_after_review(self, task, info, run, queue, pid,
                             fix_done: bool, test_done: bool) -> List[Dict[str, Any]]:
        """审查后：本次新增的严重问题→插修复；无重大问题→跳过修复直测（测试缺失才补）。
        只认"本次新增"（审查前快照 _known_issues），历史遗留发现不重复触发修复。"""
        severe, new_total = _split_new_issues(task, pid)
        has_pending_fix = any(t.get("type") == "fix" for t in queue)
        has_pending_test = any(t.get("type") == "test" for t in queue)
        if severe and not fix_done and not has_pending_fix:
            queue.insert(0, {"type": "fix", "id": self._next_auto_id("fix"),
                             "title": "修复审查发现的严重问题", "error_text":
                                 "\n".join("- %s" % s for s in severe[:5]),
                             "_auto": True})
            run.add_decision("review→fix", "审查发现 %d 个高严重度问题，自动进入修复" % len(severe),
                             "；".join(severe[:3]), "已插入修复任务")
            if not has_pending_test:
                queue.insert(1, {"type": "test", "id": self._next_auto_id("test"),
                                 "title": "修复后运行测试验证", "_auto": True})
        elif severe:
            run.add_decision("review→fix", "发现严重问题但修复已在本计划中",
                             "；".join(severe[:3]), "沿用既有计划")
        else:
            run.add_decision("review→skip-fix", "审查无新增高严重度问题，跳过修复",
                             "本次新发现 %d 条（均非高危）" % new_total,
                             "直接测试" if not test_done else "直接报告")
            if not has_pending_test and not test_done:
                queue.insert(0, {"type": "test", "id": self._next_auto_id("test"),
                                 "title": "运行测试确认现状", "_auto": True})
        return queue

    def _decide_after_fix(self, task, info, run, queue, fix_loops: int,
                          test_done: bool):
        """修复后：成功→测试验证（核心文件强制；修完必须可重测——"修完重测"语义，
        不受此前是否测过影响）；失败→限内重试，限外交报告。"""
        has_pending_test = any(t.get("type") == "test" for t in queue)
        if info.get("status") == "ok":
            core_files = _core_files_in_diff(info.get("diff") or "")
            if core_files:
                run.add_decision("fix→test(风险)", "修复涉及核心文件，强制验证",
                                 "核心文件：%s" % "、".join(core_files[:3]), "必须跑测试")
            else:
                run.add_decision("fix→test", "修复完成，验证修复效果",
                                 "非核心文件（简化确认）" if not core_files else "核心文件",
                                 "运行测试")
            if not has_pending_test:
                queue.insert(0, {"type": "test", "id": self._next_auto_id("test"),
                                 "title": "修复后验证", "_auto": True})
            return queue, fix_loops
        # 修复失败
        fix_loops += 1
        if fix_loops <= self.max_fix_loop:
            retry = dict(task)
            retry["_auto"] = True
            queue.insert(0, retry)
            run.add_decision("fix→retry", "修复失败，自动重试（第 %d/%d 轮）"
                             % (fix_loops, self.max_fix_loop),
                             str(info.get("error") or info.get("human_intervention") or "")[:300],
                             "重试修复")
        else:
            run.add_decision("fix→report", "修复重试达上限，如实出报告",
                             "已重试 %d 轮" % (fix_loops - 1), "跳过后续修复/测试")
            queue[:] = [t for t in queue if t.get("type") in ("report",)]
        return queue, fix_loops

    def _decide_after_test(self, task, info, run, queue, fix_loops: int,
                           pdir: str, pid: str):
        """测试后：通过→报告；失败→排错修复（限内），修完自动重测。"""
        if info.get("tests_passed"):
            run.add_decision("test→report", "测试全部通过，进入最终交付",
                             "exit=0", "出报告")
            return queue, fix_loops
        fail_text = str(info.get("fail_text") or "")
        fix_loops += 1
        if fail_text and fix_loops <= self.max_fix_loop:
            queue.insert(0, {"type": "fix", "id": self._next_auto_id("fix"),
                             "title": "排错修复（测试失败驱动，第 %d 轮）" % fix_loops,
                             "error_text": fail_text[:3000], "_auto": True})
            run.add_decision("test→fix→retest", "测试失败，自动进入排错，修完重测",
                             fail_text[:300], "插入修复+复测（轮次 %d/%d）"
                             % (fix_loops, self.max_fix_loop))
        else:
            run.add_decision("test→report", "测试失败且不再自动重试，如实交付",
                             "轮次 %d 达上限或无失败文本" % fix_loops, "出报告（含失败详情）")
        return queue, fix_loops


def _snapshot_review_issues(pid: str) -> set:
    """审查前的"已存在审查发现"快照（描述文本集合）。异常 → 空集（保守：全算新增）。"""
    try:
        from knowledge import issue_base as _ib
        return {str(r.get("root_cause_category") or "")
                for r in _ib.IssueBase().open_review_issues(pid, limit=50)}
    except Exception:  # noqa: BLE001
        return set()


def _split_new_issues(task, pid: str):
    """本次审查新增的（严重, 全部）发现列表（与快照差分）。"""
    severe: List[str] = []
    total: List[str] = []
    try:
        from knowledge import issue_base as _ib
        before = set(task.get("_known_issues") or [])
        for r in _ib.IssueBase().open_review_issues(pid, limit=50):
            desc = str(r.get("root_cause_category") or "")
            if not desc or desc in before:
                continue
            total.append(desc)
            if str(r.get("issue_kind") or "") in ("高", "high", "critical"):
                severe.append(desc)
    except Exception:  # noqa: BLE001
        pass
    return severe, len(total)


def _core_files_in_diff(diff: str) -> List[str]:
    """修复 diff 涉及的核心文件清单（风险判断依据；复用 L6.0 判定）。"""
    try:
        from knowledge import strategy_optimizer as _so
        files = sorted(set(re.findall(r"[\w.\-/\\]+\.(?:py|js|ts|jsx|tsx|go|rs|java)",
                                      str(diff or ""))))
        return [f for f in files if _so.is_core_file(f)][:5]
    except Exception:  # noqa: BLE001
        return []
