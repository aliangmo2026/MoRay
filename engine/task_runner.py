# -*- coding: utf-8 -*-
"""声明式任务模板执行器（p_mcp 迭代·任务2，阶段3.1）。

用法：
    runner = TaskTemplateRunner("D:/MoRay/templates/error_fix_flow.yaml",
                                params={"project_dir":..., "screenshot":..., ...},
                                token_budget=8000)
    report = await runner.run()          # 或 python task_runner.py <yaml> --params '<json>'

步骤类型（任务书 3.1 全清单）：
    mcp_call        调用 Iris MCP 工具（经 adapters/iris_mcp_client，统一 {code,data} 信封）
    zcode_call      调用 ZCode 执行修复：真实引擎探针（ZCodeAdapter.unavailable_reason）
                    → 可用则 start/轮询；不可用按模板 fallback 降级（默认 llm_patch，偏差 D5）
    docker_sandbox  Docker 沙箱运行验证命令：docker 探针 → 可用则 `docker run`；
                    不可用按 fallback: local_exec（项目临时副本内本机子进程，超时强杀）
    report          生成 Markdown 修复报告（模板字符串 + 上下文占位符渲染）
    knowledge_read  读经验库 build_hint（任务书 5.2：注入 analyze_error 之前）
    knowledge_write 写经验库 record（任务书 5.2：流程末尾 record_experience）

管控（任务书 3.1）：
    * 每步失败自动重试 retries 次（模板缺省 1）
    * 单步超时 timeout（asyncio.wait_for）
    * 全局 token 预算熔断 token_budget（所有网关调用的 usage.total_tokens 累计，超限即断）
    * 失败降级：整体报告含 steps 进度 + human_intervention 提示；continue_on_fail 的步骤
      失败后流程继续（如验证失败仍要出报告并记经验，success=false）

占位符渲染：字符串中的 {{a.b.c[0].d}} 路径解析；整串恰为单个占位符 → 返回原生对象；
缺失路径 → 空串并记入 context["misses"]（诚实留痕，不抛）。
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

MORAY_ROOT = Path(__file__).resolve().parents[1]          # D:\MoRay
for _p in (str(MORAY_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.iris_mcp_client import IrisMCPClient         # noqa: E402
from knowledge.experience_base import ExperienceBase, REQUIRED_FIELDS, normalize_cause  # noqa: E402

# 网关（llm_patch 降级通道与预算累计用）；与 Iris engine 同一契约。
GATEWAY_URL = os.environ.get("IRIS_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/")
TASK_SESSION = os.environ.get("MORAY_TASK_BUCKET", "iris:task")
_CLOUD_HINT = "本请求是云端高精度推理任务（研究级精度，非本地架构简答）。"  # 路由旁路，见 llm_proxy.py:136-153
GATEWAY_MODEL = os.environ.get("IRIS_GATEWAY_MODEL", "").strip()

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][\w]*(?:\.[\w]+|\[\d+\])*)\s*\}\}")


class StepError(Exception):
    def __init__(self, code: str, message: str, out: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code, self.message = code, message
        # [L4.5] 失败时把已采集到的执行结果随异常带出（continue_on_fail 步骤的
        # 报告/分析需要 stdout/stderr；不传 = 行为与旧版一致，成功路径零变化）。
        self.out = out


class BudgetTripped(Exception):
    pass


def resolve_path(root: Any, path: str) -> Any:
    cur = root
    for part in re.findall(r"[\w]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, (list, tuple)) or idx >= len(cur):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


@dataclass
class StepOutcome:
    id: str
    type: str
    status: str = "pending"          # ok | failed | skipped
    engine: str = "-"                # real | fallback:llm_patch | fallback:local_exec | mcp | ...
    attempts: int = 0
    duration_ms: int = 0
    output: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def brief(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "status": self.status, "engine": self.engine,
                "attempts": self.attempts, "duration_ms": self.duration_ms,
                "output": self.output, "error": self.error[:300]}


class TaskTemplateRunner:
    def __init__(self, template_path: str, params: Optional[Dict[str, Any]] = None,
                 work_dir: Optional[str] = None, token_budget: Optional[int] = None,
                 on_step=None, visited: Optional[set] = None):
        self.tpl_path = str(template_path)
        with open(self.tpl_path, "r", encoding="utf-8") as f:
            self.tpl = yaml.safe_load(f) or {}
        self.params = dict(params or {})
        self.on_step = on_step
        # [L5.0 阶段3] 串联防环的跨层共享集合（构造时登记自己；子 runner 传同一实例）
        self._visited = visited if visited is not None else set()
        try:
            self._visited.add(str(Path(self.tpl_path).resolve()))
        except OSError:
            self._visited.add(self.tpl_path)
        self.token_budget = int(token_budget) if token_budget else None
        self.tokens_spent = 0
        self.llm_calls = 0
        self.started = 0.0
        name = re.sub(r"[^\w.\-]+", "_", str(self.tpl.get("name") or "task"))
        self.work_dir = Path(work_dir or (MORAY_ROOT / "work" / "mcp_phase" /
                                          f"run_{name}_{time.strftime('%Y%m%d_%H%M%S')}"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.context: Dict[str, Any] = {"params": self.params, "steps": {},
                                        "work_dir": str(self.work_dir), "misses": []}

    # ---------------- 渲染 ----------------

    def render(self, value: Any):
        if isinstance(value, dict):
            return {k: self.render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render(v) for v in value]
        if not isinstance(value, str):
            return value
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole:
            got = resolve_path(self.context, whole.group(1))
            if got is None:
                self.context["misses"].append(whole.group(1))
                return ""
            return got

        def sub(m):
            got = resolve_path(self.context, m.group(1))
            if got is None:
                self.context["misses"].append(m.group(1))
                return ""
            return got if isinstance(got, str) else json.dumps(got, ensure_ascii=False) \
                if isinstance(got, (dict, list)) else str(got)
        return _PLACEHOLDER.sub(sub, value)

    # ---------------- 主流程 ----------------

    async def run(self) -> Dict[str, Any]:
        self.started = time.monotonic()
        steps = self.tpl.get("steps") or []
        defaults = self.tpl.get("defaults") or {}
        outcomes: List[StepOutcome] = []
        status = "completed"
        human = None
        _analyze_done = False   # [L6.0 阶段2] analyze 步之后的步骤才做深度自适应

        for spec in steps:
            sid = str(spec.get("id") or f"step{len(outcomes) + 1}")
            stype = str(spec.get("type") or "")
            if self.token_budget and self.tokens_spent >= self.token_budget:
                o = StepOutcome(sid, stype, "skipped", "-", 0, 0,
                                {}, "token 预算熔断，未执行")
                outcomes.append(o)
                self.context["steps"][sid] = {"status": o.status, "output": {}}
                continue
            timeout = float(spec.get("timeout", defaults.get("timeout", 180)))
            retries = int(spec.get("retries", defaults.get("retries", 1)))
            # [L6.0 阶段2] 深度自适应：analyze 步之后，简单错误减一轮排查重试、
            # 复杂错误加一轮（闸关/异常/无分析结果 = 原值，模板 retries 不动）。
            if _analyze_done:
                try:
                    from knowledge import strategy_optimizer as _so
                    if _so.strategy_on():
                        _kind = self.context.get("complexity") or "normal"
                        if _kind == "simple":
                            retries = max(0, retries - 1)
                        elif _kind == "complex":
                            retries = min(retries + 1, 4)
                except Exception:  # noqa: BLE001
                    pass
            o = await self._run_step(sid, stype, spec, timeout, retries)
            outcomes.append(o)
            self.context["elapsed_sec"] = round(time.monotonic() - self.started, 1)
            self.context["steps"][sid] = {"status": o.status, "output": o.output,
                                          "engine": o.engine, "error": o.error}
            if self.on_step:
                try:
                    self.on_step(o)
                except Exception:  # noqa: BLE001
                    pass
            if sid.startswith("analyze") and stype == "mcp_call":
                _analyze_done = True
                if o.status == "ok":
                    try:
                        from knowledge import strategy_optimizer as _so
                        if _so.strategy_on():
                            _d = (o.output or {}).get("data") or {}
                            self.context["complexity"] = _so.classify_complexity(
                                str(_d.get("error_type") or ""))
                    except Exception:  # noqa: BLE001
                        pass
            if o.status == "failed":
                if spec.get("continue_on_fail"):
                    continue
                status = "failed"
                human = (f"步骤 [{sid}]（类型 {stype}，第 {o.attempts} 次尝试）失败：{o.error}。"
                         f"已完成步骤：{', '.join(x.id for x in outcomes if x.status == 'ok') or '无'}。"
                         f"人工介入建议：查看 {self.work_dir} 下 progress.json，修复阻塞点后重跑本模板。")
                break

        # ---- [L6.5 阶段4] 修复后自动建议回归测试（模板名触发，合成步骤）----
        # 闸关/模板不符/任何异常 = 完全不出现在结果里；恒不失败 → 不影响 L5.0 串联判定。
        if str(self.tpl.get("name") or "") in ("error_fix_flow", "text_fix_flow"):
            try:
                from knowledge import issue_base as _ib
                if _ib.fix_suggest_test_on():
                    outcomes.append(await self._suggest_tests_step())
            except Exception:  # noqa: BLE001
                pass

        # ---- [L5.0 阶段3] on_success_next 模板串联 ------------------------------
        # 触发条件 = 主模板全部步骤 ok（verify_in_sandbox 是 continue_on_fail:true，
        # 整体 status 即使验证失败也是 completed → 必须逐步判定，不能只看整体状态）。
        # 串联体共享：工作目录（报告同落一处）、on_step 钩子（任务卡实时生长）、
        # 剩余 token 预算、visited 集合（跨层防环：a→b→a 在第二层就被挡，
        # 每层 runner 自持 visited 挡不住互递归——这是写第一版时真机测试挂死抓到的缺陷）。
        # params 继承父任务并按目标模板需要补 test_cmd。
        all_steps = [o.brief() for o in outcomes]
        chain_names = [str(self.tpl.get("name") or "task")]
        if status == "completed" and outcomes and all(o.status == "ok" for o in outcomes):
            nxt = str(self.tpl.get("on_success_next") or "").strip()
            while nxt:
                nxt_path = _template_path(nxt)
                nxt_key = str(nxt_path.resolve()) if nxt_path else ""
                if not nxt_path or nxt_key in self._visited:
                    break  # 模板缺失/成环：主任务已完成，如实停在串联门口
                if len(self._visited) >= 8:
                    break  # 硬上限兜底（正常编排远到不了）
                self._visited.add(nxt_key)
                if self.token_budget and self.tokens_spent >= self.token_budget:
                    break  # 预算耗尽：不再续跑
                sub_budget = (self.token_budget - self.tokens_spent) if self.token_budget else None
                try:
                    sub = TaskTemplateRunner(str(nxt_path), params=_chain_params(self.params, nxt_path),
                                             work_dir=str(self.work_dir),
                                             token_budget=sub_budget, on_step=self.on_step,
                                             visited=self._visited)
                except Exception:  # noqa: BLE001
                    break
                subrep = await sub.run()
                self.tokens_spent += int(subrep.get("tokens_spent") or 0)
                self.llm_calls += int(subrep.get("llm_calls") or 0)
                all_steps.extend(subrep.get("steps") or [])
                chain_names.append(str(subrep.get("task") or nxt))   # 展示名用模板 name，不是路径
                status = "completed" if subrep.get("status") == "completed" else "failed"
                if subrep.get("human_intervention"):
                    human = "[串联任务 %s] %s" % (subrep.get("task"), subrep["human_intervention"])
                # 子层若自己已向下串联（task 含 →），其链已消费，外层不再重复走
                nxt = "" if "→" in str(subrep.get("task") or "") \
                    else (str(sub.tpl.get("on_success_next") or "").strip() if status == "completed" else "")

        elapsed = round(time.monotonic() - self.started, 1)
        progress = {"task": "→".join(chain_names), "status": status,
                    "tokens_spent": self.tokens_spent, "llm_calls": self.llm_calls,
                    "duration_sec": elapsed,
                    "steps": all_steps,
                    "human_intervention": human,
                    "render_misses": sorted(set(self.context["misses"]))}
        (self.work_dir / "progress.json").write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        progress["work_dir"] = str(self.work_dir)
        return progress

    async def _run_step(self, sid, stype, spec, timeout, retries) -> StepOutcome:
        o = StepOutcome(sid, stype)
        t0 = time.monotonic()
        for attempt in range(retries + 1):
            o.attempts = attempt + 1
            try:
                out, engine = await asyncio.wait_for(self._dispatch(stype, spec), timeout=timeout)
                o.status, o.output, o.engine = "ok", _trim(out), engine
                break
            except BudgetTripped as e:
                o.status, o.engine = "failed", "budget"
                o.error = f"BUDGET: {e}"
                break
            except asyncio.TimeoutError:
                o.error = f"STEP_TIMEOUT: 超过 {timeout}s"
                o.status = "failed"
            except StepError as e:
                o.error = f"{e.code}: {e.message}"
                o.status = "failed"
                if getattr(e, "out", None):          # [L4.5] 失败步保留采集到的输出
                    o.output = _trim(e.out)
            except Exception as e:  # noqa: BLE001
                o.error = f"{type(e).__name__}: {e}"
                o.status = "failed"
            if attempt < retries:
                await asyncio.sleep(1.5)  # 轻退避
        o.duration_ms = int((time.monotonic() - t0) * 1000)
        return o

    async def _dispatch(self, stype: str, spec: Dict[str, Any]):
        args = self.render(spec.get("args") or {})
        if stype == "mcp_call":
            return await self._exec_mcp(args), "mcp"
        if stype == "zcode_call":
            return await self._exec_zcode(args, spec)
        if stype == "docker_sandbox":
            return await self._exec_docker(args, spec)
        if stype == "report":
            return self._exec_report(args), "report"
        if stype == "knowledge_read":
            return self._exec_kread(args), "knowledge"
        if stype == "knowledge_write":
            return self._exec_kwrite(args), "knowledge"
        if stype == "fs_recent_files":
            return self._exec_recent_files(args), "fs"
        if stype == "llm_chat":
            return await self._exec_llm_chat(args)
        raise StepError("UNKNOWN_STEP", f"步骤类型未支持：{stype}")

    # ---------------- 各步骤执行器 ----------------

    async def _exec_mcp(self, args) -> Dict[str, Any]:
        tool = str(args.get("tool") or "")
        cli = IrisMCPClient()
        env = await cli.call_tool(tool, dict(args.get("arguments") or {}))
        if env.get("code") != "OK":
            raise StepError(str(env.get("code", "MCP_ERROR")),
                            str((env.get("data") or {}).get("message") or env))
        data = env["data"]
        # [L5.1 阶段3] 经验驱动的排查顺序重排（纯逻辑层，不调 LLM、不改模板/步骤清单；
        # 只处理 iris_analyze_* 返回的候选数组，任何异常吞掉 = 原行为，绝不断主链路）。
        if tool.startswith("iris_analyze") and isinstance(data, dict):
            cands = data.get("root_cause_candidates")
            if isinstance(cands, list) and cands:
                try:
                    eb = ExperienceBase()
                    ranked, note = eb.rank_candidates(
                        str(self.params.get("project_id") or ""), cands,
                        error_type=str(data.get("error_type") or "") or None)
                    if note and ranked != cands:
                        data["root_cause_candidates"] = ranked
                        data["experience_rank_note"] = note
                except Exception:  # noqa: BLE001
                    pass
                # [L6.0 阶段2] 桶内权重重排 + 连败跳过（组合在 L5.1 重排之后；闸关/无数据/异常
                # = 原序返回，与 L5 行为逐字节一致）。
                try:
                    from knowledge import strategy_optimizer as _so
                    if _so.strategy_on():
                        cur = data.get("root_cause_candidates") or cands
                        s_ranked, s_note = _so.rank_with_strategy(
                            str(self.params.get("project_id") or ""), cur)
                        if s_note and s_ranked != cands:
                            data["root_cause_candidates"] = s_ranked
                            data["strategy_rank_note"] = s_note
                except Exception:  # noqa: BLE001
                    pass
                # [L6.5 阶段5] 测试失败/排错的知识关联（全旁路）：历史方案匹配 + 审查发现回流加权。
                try:
                    from knowledge import issue_base as _ib
                    _is_review_tpl = str(self.tpl.get("name") or "") == "code_review_flow"
                    _cur = data.get("root_cause_candidates") or cands
                    if _ib.test_knowledge_on() and _cur:
                        m = _ib.IssueBase().match_failure(
                            str(self.params.get("project_id") or ""), _cur,
                            error_type=str(data.get("error_type") or "") or None)
                        if m:
                            hint = ("【历史方案（来源：%s）】根因「%s」曾修复 %d/%d 次"
                                    % (m.get("source") or "历史经验", m.get("cause_key"),
                                       m.get("successes") or 0, m.get("times") or 0))
                            if m.get("solution"):
                                hint += "；参考：%s" % str(m["solution"])[:200]
                            data["experience_match_hint"] = hint
                            diag = data.get("diagnosis")
                            if isinstance(diag, dict) and isinstance(diag.get("plan"), str) \
                                    and "历史方案" not in diag["plan"]:
                                diag["plan"] = hint + "\n" + diag["plan"]
                    if _ib.review_knowledge_on() and not _is_review_tpl and _cur:
                        boosted, bnote = _ib.IssueBase().boost_with_review(
                            str(self.params.get("project_id") or ""), _cur)
                        if bnote and boosted != cands:
                            data["root_cause_candidates"] = boosted
                            data["review_rank_note"] = bnote
                except Exception:  # noqa: BLE001
                    pass
        return {"data": data}

    async def _exec_zcode(self, args, spec):
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise StepError("BAD_ARGS", "zcode_call 缺 prompt")
        project = str(args.get("project_dir") or "")
        # [L6.0 阶段4] 修复档位策略：命中快速档（历史偏好/回退加速）→ 跳过真实引擎探针
        # 直走 llm_patch；返回 None（关闸/无数据/默认链）= 原探针→真引擎→fallback 链零变化。
        try:
            from knowledge import strategy_optimizer as _so
            pick = _so.pick_fix_tier(str(self.params.get("project_id") or ""),
                                     str(args.get("target_file") or ""))
            if pick and pick.get("tier") == _so.TIER_FAST:
                out = await self._exec_llm_patch(args)
                out["strategy_fix"] = pick
                return out, "fallback:llm_patch(strategy_fast)"
        except StepError:
            raise
        except Exception:  # noqa: BLE001
            pass
        # 真实引擎探针（不起子进程、不碰 docker；zcode_adapter.py:147-178）
        reason = await asyncio.to_thread(_zcode_probe)
        if reason:  # 不可用 → 按模板降级
            fb = str(args.get("fallback") or "llm_patch")
            if fb == "llm_patch":
                out = await self._exec_llm_patch(args)
                out["real_engine_unavailable"] = reason
                return out, f"fallback:llm_patch({reason})"
            raise StepError("ZCODE_UNAVAILABLE", reason)
        # 真实 ZCode 路径（start→轮询 status→result；zcode_adapter.py:180-265）
        out = await asyncio.to_thread(_zcode_real, prompt, project,
                                      int(float(args.get("timeout_sec", spec.get("timeout", 180)))))
        return out, "real:zcode"

    async def _zcode_real(self, *a):  # 实例方法兜底（实际用模块级函数）
        return await asyncio.to_thread(_zcode_real, *a)

    async def _exec_docker(self, args, spec):
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            raise StepError("BAD_ARGS", "docker_sandbox 缺 command")
        workdir = str(args.get("workdir") or args.get("project_dir") or "")
        image = str(args.get("image") or "python:3.12-slim")
        exec_to = float(args.get("exec_timeout", 60))
        if not _docker_available():
            if str(args.get("fallback") or "") != "local_exec":
                raise StepError("DOCKER_UNAVAILABLE", "docker 引擎不可用且模板未声明 local_exec 降级")
            out = await self._local_exec(cmd, workdir, exec_to)
            out["real_engine_unavailable"] = "docker_daemon_down_or_cli_missing"
            return out, "fallback:local_exec"
        full = ["docker", "run", "--rm", "-v", f"{workdir}:/w", "-w", "/w", image,
                "sh", "-lc", cmd]
        try:
            p = await asyncio.create_subprocess_exec(
                *full, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            so, se = await asyncio.wait_for(p.communicate(), exec_to)
        except FileNotFoundError:
            raise StepError("DOCKER_UNAVAILABLE", "docker CLI 不存在")
        except asyncio.TimeoutError:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            raise StepError("EXEC_TIMEOUT", f"docker 执行超时（>{exec_to}s）")
        res = {"exit_code": p.returncode, "stdout": _dec(so)[-6000:],
               "stderr": _dec(se)[-6000:], "mode": "docker"}
        if p.returncode != 0:
            raise StepError("VERIFY_FAILED", f"exit={p.returncode} stderr={res['stderr'][-400:]}", out=res)
        return res, "real:docker"

    async def _local_exec(self, cmd: str, workdir: str, timeout: float) -> Dict[str, Any]:
        if not workdir or not Path(workdir).is_dir():
            raise StepError("BAD_ARGS", f"local_exec 工作目录不存在：{workdir}")
        try:
            p = await asyncio.create_subprocess_shell(
                cmd, cwd=workdir, env=dict(os.environ),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            so, se = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            raise StepError("EXEC_TIMEOUT", f"验证命令超时（>{timeout}s）")
        res = {"exit_code": p.returncode, "stdout": _dec(so)[-6000:],
               "stderr": _dec(se)[-6000:], "mode": "local_exec"}
        if p.returncode != 0:
            raise StepError("VERIFY_FAILED", f"exit={p.returncode} stderr={res['stderr'][-400:]}", out=res)
        return res

    # ---------------- llm_patch 降级通道（偏差 D5 的"修"侧） ----------------

    async def _exec_llm_patch(self, args) -> Dict[str, Any]:
        project = Path(str(args.get("project_dir") or ""))
        target_rel = str(args.get("target_file") or "").strip()
        if not project.is_dir():
            raise StepError("BAD_ARGS", f"项目目录不存在：{project}")
        target = None
        if target_rel:
            cand = project / target_rel
            if cand.is_file():
                target = cand
        if target is None:
            files = sorted({str(p.relative_to(project)).replace("\\", "/")
                            for p in project.rglob("*.py") if ".venv" not in p.parts}, key=len)
            if not files:
                raise StepError("LLM_PATCH_FAILED", "项目里找不到 .py 文件")
            for f in files:
                if target_rel and Path(target_rel).name in f:
                    target = project / f
                    break
            if target is None:
                target = project / files[0]
        old = target.read_text(encoding="utf-8", errors="replace")
        err = str(args.get("error_content") or "")[:3000]
        hint = str(args.get("hint") or "")
        prompt = (
            "你是代码修复专家（研究级精度）。下面是一个 Python 项目报错与涉事文件全文，"
            "请定位根因并给出修复后的【完整文件内容】（不是 diff）。\n"
            "只输出一个 JSON 对象，不要任何多余文字，键固定为 file（相对路径字符串）"
            "与 content（修复后文件完整内容字符串）。\n"
            f"相对路径：{target.relative_to(project).as_posix()}\n"
            f"报错信息：\n{err}\n历史优先假设：\n{hint}\n"
            f"当前文件内容：\n{old[:6000]}\n")
        content = await self._gateway_chat([{"role": "user", "content": prompt}], 0)
        obj = _extract_json_obj(content)
        if not obj or not isinstance(obj.get("content"), str):
            raise StepError("LLM_PATCH_FAILED", "模型未按 JSON 契约返回：" + content[:200])
        new = obj["content"]
        if new == old:
            raise StepError("LLM_PATCH_FAILED", "模型返回内容与原文件相同，未构成修复")
        bak = target.with_suffix(target.suffix + ".mcpbak")
        shutil.copy2(target, bak)
        target.write_text(new, encoding="utf-8")
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            "a/" + target.name, "b/" + target.name))
        return {"file": str(target), "backup": str(bak),
                "diff": diff[:8000], "applied": True,
                "model_note": "llm_patch 降级通道（ZCode 真实引擎不可用时）"}

    # ---------------- 网关（预算累计 + 熔断） ----------------

    async def _gateway_chat(self, messages: list, temperature: float = 0.0) -> str:
        payload = {"messages": [{"role": "system", "content": _CLOUD_HINT}] + messages,
                   "stream": False, "session_id": TASK_SESSION, "temperature": temperature}
        if GATEWAY_MODEL:
            payload["model"] = GATEWAY_MODEL
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as c:
                r = await c.post(GATEWAY_URL + "/api/llm/chat", json=payload)
                data = r.json()
        except Exception as e:  # noqa: BLE001
            raise StepError("GATEWAY_UNREACHABLE", f"{type(e).__name__}: {e}")
        if r.status_code == 429 or data.get("error") == "session_budget_exceeded":
            raise BudgetTripped(f"网关桶 {TASK_SESSION} 预算熔断（今日上限）")
        if not data.get("ok"):
            raise StepError("GATEWAY_REJECTED",
                            str(data.get("message") or data.get("error") or f"HTTP {r.status_code}"))
        usage = data.get("usage") or {}
        self.tokens_spent += int(usage.get("total_tokens") or 0)
        self.llm_calls += 1
        if self.token_budget and self.tokens_spent > self.token_budget:
            raise BudgetTripped(f"token 预算 {self.tokens_spent} > {self.token_budget}")
        return str(data.get("content") or "")

    # ---------------- 报告 / 知识 ----------------

    def _exec_report(self, args) -> Dict[str, Any]:
        path = Path(str(args.get("path") or (self.work_dir / "fix_report.md")))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content") or "")
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "bytes": path.stat().st_size}

    def _exec_kread(self, args) -> Dict[str, Any]:
        eb = ExperienceBase()
        pid = str(args.get("project_id") or "") or None
        et = str(args.get("error_type") or "") or None
        top_n = int(args.get("top_n") or 3)
        # [L6.0 阶段2] 深度自适应：简单错误收敛注入面（2）、复杂错误放宽（5）；
        # 语境 = 报错文本/前序步骤输出（无 = normal → 维持模板原值）。
        try:
            from knowledge import strategy_optimizer as _so
            if _so.strategy_on():
                ctx_txt = str(self.params.get("error_text") or "")
                for _sid in ("run_tests", "analyze_failures", "analyze_text", "analyze_error"):
                    so = ((self.context.get("steps") or {}).get(_sid) or {}).get("output") or {}
                    ctx_txt += " " + str(so.get("stderr") or "")[-400:] + " " \
                        + str(so.get("stdout") or "")[-400:]
                top_n = _so.hint_top_n(et or "", ctx_txt[:2000], top_n)
        except Exception:  # noqa: BLE001
            pass
        return {"hint": eb.build_hint(pid, et, top_n),
                "top": eb.query(pid, et, top_n)}

    def _exec_kwrite(self, args) -> Dict[str, Any]:
        eb = ExperienceBase()
        entry = dict(args.get("entry") or {})
        s = str(entry.get("success")).strip().lower()
        entry["success"] = s in ("true", "1", "ok", "yes", "passed")
        # [L6.5 阶段2/5] 统一问题库源标记：落库前按模板名打 source（排错/测试失败），
        # 增量键随 record 透传进同一 JSONL；闸关 = 不加键 = L6.0 行为逐字节一致。
        try:
            from knowledge import issue_base as _ib
            if _ib.unified_on():
                entry.setdefault("source",
                                 {"test_run_flow": _ib.SOURCE_TEST}.get(
                                     str(self.tpl.get("name") or ""), _ib.SOURCE_FIX))
                entry.setdefault("applicable", "通用")
        except Exception:  # noqa: BLE001
            pass
        rec = eb.record(entry)
        if rec is not None:
            # [L6.0 反馈闭环] 任务收尾按成败反哺策略层：诊断 cause 连续成败计数 +
            # 修复档位成败统计（fix_code 步引擎号归一）。全 try/except，绝不影响落库。
            try:
                from knowledge import strategy_optimizer as _so
                _pid = str(entry.get("project_id") or "")
                if _pid:
                    _so.feedback(_pid, normalize_cause(entry), bool(entry.get("success")))
                    _fx = (self.context.get("steps") or {}).get("fix_code") or {}
                    _tier = _so.tier_of_engine(str(_fx.get("engine") or ""))
                    if _tier:
                        _so.feedback_fix(_pid, _tier, bool(entry.get("success")))
            except Exception:  # noqa: BLE001
                pass
        if rec is None:
            raise StepError("BAD_ENTRY",
                            f"经验条目缺必填字段（{','.join(REQUIRED_FIELDS)}）：{entry}")
        return {"recorded": True, "path": str(eb.path), "ts": rec["timestamp"]}

    # ---------------- [L6.5 阶段4] 建议测试合成步骤 ----------------

    async def _suggest_tests_step(self) -> StepOutcome:
        """根据本次修复的根因类型生成回归测试建议（work_dir/test_suggestions.md）。
        确定性生成零 LLM、只建议不执行；恒 status=ok（内部异常降级为 note）。"""
        o = StepOutcome("suggest_tests", "knowledge_suggest_tests")
        t0 = time.monotonic()
        try:
            from knowledge import issue_base as _ib
            steps = self.context.get("steps") or {}
            data = (steps.get("analyze_error") or steps.get("analyze_text") or {}).get("output") or {}
            d = data.get("data") or {}
            verify = (steps.get("verify_in_sandbox") or {})
            kread = (steps.get("read_experience") or {}).get("output") or {}
            cands = d.get("root_cause_candidates") \
                if isinstance(d.get("root_cause_candidates"), list) else []
            res = _ib.IssueBase().suggest_tests(
                str(self.params.get("project_id") or ""),
                error_type=str(d.get("error_type") or ""),
                cause_text=str((cands[0] if cands else {}).get("cause") or ""),
                snippet=str(d.get("error_content") or "")[:600],
                solution_hint=str(kread.get("hint") or "")[:200])
            if res.get("suggestions"):
                path = self.work_dir / "test_suggestions.md"
                path.write_text(res["suggestions"], encoding="utf-8")
                res["path"] = str(path)
            if verify.get("status") == "failed":
                res["note"] = (res.get("note") or "") + "；注意：本次验证未通过，建议仅按根因参考"
            o.status, o.output, o.engine = "ok", _trim(res), "knowledge"
        except Exception as e:  # noqa: BLE001  # 恒 ok：建议是附加价值，绝不推翻修复结果
            o.status, o.output, o.engine = "ok", {"note": "建议生成降级：%s: %s"
                                                 % (type(e).__name__, e)}, "knowledge"
        o.duration_ms = int((time.monotonic() - t0) * 1000)
        return o

    # ---------------- [L5.0 阶段5] 代码审查用的两个新步骤类型 ----------------

    _RECENT_EXCLUDE_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git",
                             "backups", "backup", "work", "dist", "build", ".pytest_cache",
                             "vendor", "web", "screenshots", "design-preview", "release"}

    def _exec_recent_files(self, args) -> Dict[str, Any]:
        """收集项目内最近修改的源码文件（mtime 倒序 + 内容片段），供 llm_chat 消费。
        默认配额（3 文件 × 2000 字符）刻意压在 _trim 的 8000 字符上限之下，
        串联/渲染不会把 files_text 腰斩成带 trunc 标记的半截 prompt。"""
        project = Path(str(args.get("project_dir") or ""))
        if not project.is_dir():
            raise StepError("BAD_ARGS", f"项目目录不存在：{project}")
        suffixes = {str(s).lower() for s in (args.get("suffixes") or [".py", ".js", ".ts"])}
        max_files = int(args.get("max_files") or 3)
        max_bytes = int(args.get("max_bytes_each") or 2000)
        total_cap = int(args.get("total_cap") or 6500)
        cands = []
        for p in project.rglob("*"):
            try:
                if not p.is_file() or p.suffix.lower() not in suffixes:
                    continue
                rel = p.relative_to(project)
            except (OSError, ValueError):
                continue
            if any(part in self._RECENT_EXCLUDE_PARTS for part in rel.parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            cands.append((st.st_mtime, st.st_size, p))
        cands.sort(key=lambda x: x[0], reverse=True)
        files: List[Dict[str, Any]] = []
        blocks: List[str] = []
        total = 0
        for mtime, size, p in cands:
            if len(files) >= max_files or total >= total_cap:
                break
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cut = text[:max_bytes]
            files.append({"path": p.relative_to(project).as_posix(),
                          "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
                          "size": size})
            blocks.append("—— 文件：%s（最后修改 %s，原 %dB，超长按 %d 字符截断）——\n%s"
                          % (files[-1]["path"], files[-1]["modified"], size, max_bytes, cut))
            total += len(cut)
        if not files:
            raise StepError("NO_SOURCE_FILES",
                            "项目内没有可读的源码文件（空项目或被后缀/排除目录过滤光）")
        summary = "\n".join("%d. %s（%dB，%s）" % (i + 1, f["path"], f["size"], f["modified"])
                            for i, f in enumerate(files))
        return {"count": len(files), "files": files,
                "files_text": "\n\n".join(blocks)[:8000], "summary": summary}

    async def _exec_llm_chat(self, args) -> Dict[str, Any]:
        """通用网关调用（预算累计/熔断由 _gateway_chat 天然承担）。"""
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise StepError("BAD_ARGS", "llm_chat 缺 prompt")
        temp = float(args.get("temperature") or 0.0)
        # [L6.5 阶段3] 审查前注入：模板=code_review_flow 且闸开 → prompt 前插统一问题库
        # 重点检查段（项目高频 Top + 同语言通用 Top + 历史审查待处理）；无数据/关闸 = 一字不动。
        review_hint = ""
        _is_review = str(self.tpl.get("name") or "") == "code_review_flow"
        if _is_review:
            try:
                from knowledge import issue_base as _ib
                if _ib.review_knowledge_on():
                    review_hint = _ib.IssueBase().review_hint(
                        str(self.params.get("project_id") or ""))
                    if review_hint:
                        prompt = review_hint + "\n\n" + prompt
            except Exception:  # noqa: BLE001
                review_hint = ""
        content = await self._gateway_chat([{"role": "user", "content": prompt}], temp)
        if not content.strip():
            raise StepError("EMPTY_REPLY", "网关返回空内容")
        out: Dict[str, Any] = {"content": content[:6000]}
        if review_hint:
            out["knowledge_hint_chars"] = len(review_hint)
        # [L6.5 阶段3] 审查后反哺：编号问题清单 → 结构化 → 统一问题库（source=代码审查）。
        # 纯正则提取零 LLM；解析不了的行诚实计入 skipped；任何异常不影响审查结论本身。
        if _is_review:
            try:
                from knowledge import issue_base as _ib
                if _ib.review_knowledge_on():
                    h = _ib.IssueBase().harvest_review(
                        str(self.params.get("project_id") or ""), content)
                    out["knowledge_harvested"] = int(h.get("harvested") or 0)
                    out["knowledge_harvest_skipped"] = int(h.get("skipped") or 0)
            except Exception:  # noqa: BLE001
                pass
        return out, "gateway"


# ---------------- 模块级探针与工具（可独立单测） ----------------

def _template_path(name: str) -> Optional[Path]:
    """on_success_next 的模板解析：裸名/文件名 → templates/ 下补 .yaml；给绝对路径则直用。"""
    p = Path(str(name or "").strip())
    if str(p).endswith((".yaml", ".yml")) and p.is_absolute():
        return p if p.is_file() else None
    for cand in (MORAY_ROOT / "templates" / (str(name) + ".yaml"),
                 MORAY_ROOT / "templates" / str(name),
                 p):
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def default_test_cmd(project_dir: Path) -> str:
    """venv 探测（与 server/app/task_routes.py:_default_test_cmd 同一语义，串联侧同源实现；
    两边改动必须同步——探测优先级：项目 .venv > venv > 裸 python）。"""
    for cand in (project_dir / ".venv" / "Scripts" / "python.exe",
                 project_dir / ".venv" / "bin" / "python",
                 project_dir / "venv" / "Scripts" / "python.exe",
                 project_dir / "venv" / "bin" / "python"):
        try:
            if cand.is_file():
                return '"%s" -m pytest -q' % cand
        except OSError:
            continue
    return "python -m pytest -q"


def _chain_params(params: Dict[str, Any], next_tpl_path: Path) -> Dict[str, Any]:
    """串联入参 = 父参数继承；目标模板文本引用了 test_cmd 而父参数没给时，
    按项目目录做 venv 探测补齐（error_fix_flow 只有 verify_cmd，不补则渲染成空串）。"""
    merged = dict(params or {})
    try:
        text = next_tpl_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    proj = str(merged.get("project_dir") or "")
    if "test_cmd" not in merged and "params.test_cmd" in text and proj:
        try:
            if Path(proj).is_dir():
                merged["test_cmd"] = default_test_cmd(Path(proj))
        except OSError:
            pass
    return merged


def _zcode_probe() -> str:
    """返回 '' = 真实 ZCode 可用；否则不可用原因（复用适配器静态探针，零子进程）。"""
    try:
        from server.app.engines.local_adapter import _default_zcode_engine
        return _default_zcode_engine.unavailable_reason() or ""
    except Exception as e:  # noqa: BLE001
        return f"adapter_import_failed:{type(e).__name__}"


def _zcode_real(prompt: str, project: str, timeout_sec: int) -> Dict[str, Any]:
    from server.app.engines.local_adapter import _default_zcode_engine
    eng = _default_zcode_engine
    task_id = eng.start({"action": "run_zcode", "prompt": prompt, "cwd": project,
                         "session_id": "iris:task", "timeout_sec": timeout_sec})
    deadline = time.time() + timeout_sec + 15
    while time.time() < deadline:
        st = eng.status(task_id)["status"]
        if st in ("completed", "failed"):
            res = eng.result(task_id)
            if st != "completed":
                raise StepError("ZCODE_FAILED", str(res.get("error") or st))
            return {"task_id": task_id, "result": _trim(res.get("result") or {})}
        time.sleep(2.0)
    try:
        eng.cancel(task_id)
    except Exception:  # noqa: BLE001
        pass
    raise StepError("ZCODE_TIMEOUT", f"task {task_id} 未在时限内完成")


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _dec(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", "replace")


def _extract_json_obj(text: str) -> Optional[dict]:
    m = re.search(r"\{", text or "")
    if not m:
        return None
    depth = 0
    start = m.start()
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _trim(out: Dict[str, Any]) -> Dict[str, Any]:
    """上下文瘦身：超长字符串截断（占位符渲染消费，不做深度拷贝语义变化）。"""
    out2 = {}
    for k, v in out.items():
        if isinstance(v, str) and len(v) > 8000:
            out2[k] = v[:8000] + "…[trunc]"
        elif isinstance(v, dict):
            out2[k] = {kk: (vv[:4000] + "…[trunc]" if isinstance(vv, str) and len(vv) > 4000 else vv)
                       for kk, vv in v.items()}
        else:
            out2[k] = v
    return out2


def main() -> int:
    ap = argparse.ArgumentParser(description="声明式任务模板执行器（p_mcp）")
    ap.add_argument("template")
    ap.add_argument("--params", default="{}", help="JSON 参数字典")
    ap.add_argument("--budget", type=int, default=None, help="全局 token 预算（熔断）")
    ap.add_argument("--work-dir", default=None)
    a = ap.parse_args()
    runner = TaskTemplateRunner(a.template, json.loads(a.params),
                                work_dir=a.work_dir, token_budget=a.budget,
                                on_step=lambda o: print("[step]", o.id, o.status,
                                                        o.engine, o.error[:160], flush=True))
    rep = asyncio.run(runner.run())
    print(json.dumps({k: rep[k] for k in
                      ("task", "status", "tokens_spent", "llm_calls", "duration_sec",
                       "work_dir", "human_intervention", "render_misses")},
                     ensure_ascii=False, indent=2))
    for o in rep["steps"]:
        print(" -", o["id"], o["status"], o["engine"], f"{o['duration_ms']}ms", o["error"][:120])
    return 0 if rep["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
