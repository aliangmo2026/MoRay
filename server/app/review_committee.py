# -*- coding: utf-8 -*-
r"""多智能体"代码审查委员会"（路线一 2026-11-07）

Surgeon 动手 → Scribe 审查 → ZCode 验证 → 汇总裁决 的确定性闭环（任务书四步），
失败把错误打回修改方重试（最多 2 次），全程逐事件上轴 synapse 总线——战术轨迹
悬浮窗（moray-panels agent-trace）的 synapse 适配器按 proxy_source 第一段渲染
agent 身份，surgeon / scribe / zcode / committee 四个身份自然出现，零前端改动。

四步的真实器官契约（2026-11-07 实测）：
  * Surgeon  execute_plan(plan_json, repo_path)：对 git 仓库（须有 ≥1 commit）
    应用 RefactorPlan JSON，自验通过才 commit、失败整体回滚；intent 模式走
    refactor_smart（Surgeon 内部 Ollama 规划，可吸收审查反馈重试）。
  * Scribe   scribe_scan(path)：仓库/目录扫描，语法错误在此暴露；产物
    symbol-graph.json 落 out/，委员会读它做逐符号 diff（符号丢失=审查失败）。
  * ZCode    ZCodeAdapter.start(run_zcode)：edit 模式（写白名单沙箱、Bash 被禁、
    can_exec=False 是隔离设计）——委员会把目标文件快照投进沙箱 stage，让 ZCode
    写独立语法校验脚本与 RESULT.txt；权威判据仍是委员会自己对**真实目标文件**
    跑 py_compile（ZCode 是独立第二意见，不是唯一闸门）。

红线：
  * 拒绝把 D:\MoRay 自身当仓库（防 Surgeon 对本系统误 commit，preset-coder 铁律同源）；
  * 一切 organ 调用走 mcp_gateway.execute_chat_tool（信封 + agent_tool_log 审计
    + 分级超时现成）；
  * 所有文件操作 io.open + 字面量前缀 join（Mimosa 写盘纪律）；
  * synapse 关闸时轨迹事件自动 no-op（synapse_bus.send 首行自检），委员会照常跑。
"""
import asyncio
import io
import json
import logging
import os
import py_compile
import subprocess
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import synapse_bus
from .engines.base import STATUS_COMPLETED, STATUS_FAILED
from .engines.zcode_adapter import SANDBOX_ROOT, ZCodeAdapter

logger = logging.getLogger("moray.review_committee")

router = APIRouter()

#: 失败重试上限（任务书：最多重试 2 次 → 总尝试 ≤ 3）
MAX_RETRIES_DEFAULT = 2
#: ZCode 验证步整体预算（任务 timeout_sec 240 + 轮询余量）
ZCODE_VERIFY_BUDGET = 300.0
#: stage 根（与 zcode_adapter.SANDBOX_ROOT 恒等；字面量以满足 Mimosa 写盘纪律，
#: import 时不等即 fail-loud）
_STAGE_ROOT = r"D:\MoRay\workspace\zcode_sandbox\committee"
if str(SANDBOX_ROOT).rstrip("\\") != os.path.dirname(_STAGE_ROOT):
    raise RuntimeError("review_committee: SANDBOX_ROOT 漂移（%s != %s）——两处必须同源"
                       % (SANDBOX_ROOT, _STAGE_ROOT))

#: 内存作业表（job_id → rec）；重启即清，权威留痕在 synapse 台账与各器官审计
_JOBS: dict = {}


def _trace(event_type: str, content: str) -> None:
    """上轴 synapse（战术轨迹浮窗 synapse 适配器消费）。任何失败不扰主流程。"""
    try:
        synapse_bus.send(event_type, content, from_member="review_committee")
    except Exception as e:  # noqa: BLE001 - 轨迹面缺失不影响审查
        logger.warning("[committee] 轨迹事件发送失败（忽略）：%s: %s",
                       type(e).__name__, str(e)[:120])


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- 基础设施步

def _git(*args: str, cwd: str) -> dict:
    """只读 git 探测（rev-parse）。返回 {ok, out/err}；绝不做任何写操作。"""
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=30, encoding="utf-8", errors="replace")
        return {"ok": p.returncode == 0, "out": (p.stdout or "")[:400],
                "err": (p.stderr or "")[:400]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "out": "", "err": "%s: %s" % (type(e).__name__, str(e)[:160])}


def _validate_target(target_file: str) -> tuple:
    r"""前置闸：.py / 存在 / 在 git 仓库内（有 HEAD）/ 不在 D:\MoRay 自身。
    返回 (拒绝原因 or "", 仓库根 or "")。"""
    tf = str(target_file or "").strip()
    if not tf.lower().endswith(".py"):
        return "target_file 必须是 .py 文件", ""
    if not os.path.isfile(tf):
        return "目标文件不存在：%s" % tf, ""
    repo = os.path.dirname(tf)
    head = _git("rev-parse", "--show-toplevel", cwd=repo)
    if not head["ok"]:
        return "目标不在 git 仓库内（Surgeon 要求仓库有 HEAD）：" + head["err"][:120], ""
    top = head["out"].strip()
    if top.rstrip("\\").lower() == r"d:\moray":
        return "拒绝把 MoRay 自身当审查对象（防 Surgeon 对本系统误 commit）", ""
    if not _git("rev-parse", "HEAD", cwd=repo)["ok"]:
        return "仓库没有任何 commit（Surgeon 要求至少 1 个 HEAD）", ""
    return "", top


async def _scribe_symbols(repo_dir: str) -> dict:
    """Scribe 扫描 → {ok, lost_checked, symbols, graph_path, summary, error}。
    symbols = {(file, kind, name)}；graph 落 out/（Scribe 产物惯例：目标父目录/out）。"""
    env = await _call("scribe", "scribe_scan", {"path": str(repo_dir)})
    base = {"ok": False, "symbols": set(), "graph_path": "", "summary": "", "error": ""}
    if str(env.get("code")) != "OK":
        base["error"] = str((env.get("data") or {}).get("message") or env.get("code"))[:200]
        return base
    data = env.get("data") or {}
    base["ok"] = True
    base["summary"] = json.dumps(data, ensure_ascii=False)[:300]
    gp = str(data.get("out_path") or "")
    base["graph_path"] = gp
    if gp and os.path.isfile(gp):
        try:
            with io.open(gp, "r", encoding="utf-8") as f:
                graph = json.load(f)
            syms = set()
            for mod in graph if isinstance(graph, list) else []:
                for s in (mod.get("symbols") or []):
                    syms.add((str(s.get("file") or ""), str(s.get("kind") or ""),
                              str(s.get("name") or ""), int(s.get("lineno") or 0)))
            base["symbols"] = syms
        except Exception as e:  # noqa: BLE001 - graph 读不到只降级为计数审查
            base["error"] = "symbol-graph 解析失败：%s: %s" % (type(e).__name__, str(e)[:120])
    return base


async def _call(server: str, tool: str, args: dict) -> dict:
    """统一 organ 调用面（execute_chat_tool：信封 + 审计 + 分级超时）。"""
    from . import mcp_gateway  # 惰性 import 防环（mcp_gateway 不 import 本模块，防御性）
    return await mcp_gateway.execute_chat_tool(server, tool, args)


# ---------------------------------------------------------------- 三步动作

async def _step_surgeon(repo_dir: str, plan_json: str, intent: str) -> dict:
    """Step 1：Surgeon 动手。plan 模式=execute_plan（确定性）；intent 模式=
    refactor_smart（Surgeon 内部 Ollama 规划，可吸收反馈）。
    [2026-11-07 实测] Surgeon 的 MCP 信封恒 code=OK（HTTP 层成功），业务成败只看
    返回文案前缀：Success! = 落盘并 commit；Failed. Rolled back. / Error: / Invalid
    plan format / Nothing to do = 全部按失败处理（feedback 打回重试）。"""
    if plan_json:
        env = await _call("surgeon", "execute_plan",
                          {"plan_json": plan_json, "repo_path": str(repo_dir)})
        mode = "execute_plan"
    else:
        env = await _call("surgeon", "refactor_smart",
                          {"intent": intent, "repo_path": str(repo_dir)})
        mode = "refactor_smart"
    data = env.get("data") or {}
    text = str(data.get("text") or data.get("message") or json.dumps(data, ensure_ascii=False))
    ok = str(env.get("code")) == "OK" and text.startswith("Success")
    return {"ok": ok, "mode": mode, "detail": text[:600],
            "error": "" if ok else text[:300]}


def _symbol_diff(baseline: set, current: set) -> dict:
    """符号三方 diff：lost（真丢失）/ renamed（同定义位点换名=合法重构，不算丢失）/
    gained。配对键 = (file, kind, lineno)——rename_symbol 只换名不挪行，一对一名额
    逐个消耗。丢失未配对的才算审查失败。"""
    lost = baseline - current
    gained = current - baseline
    by_site: dict = {}
    for f, k, n, ln in gained:
        by_site.setdefault((f, k, ln), []).append(n)
    real_lost, renamed = [], []
    for f, k, n, ln in sorted(lost):
        names = by_site.get((f, k, ln)) or []
        if names:
            renamed.append("%s %s→%s" % (k, n, names[0]))
            by_site[(f, k, ln)].remove(names[0])
        else:
            real_lost.append("%s:%s:%s" % (f, k, n))
    return {"lost": sorted(real_lost), "renamed": sorted(renamed),
            "gained": sorted("%s:%s:%s" % (t[0], t[1], t[2]) for t in gained)}


async def _step_scribe_audit(repo_dir: str, baseline: dict) -> dict:
    """Step 2：Scribe 审查。语法/扫描错误即失败；符号 diff 出现**真丢失**才失败
    （rename 配对不算）。"""
    cur = await _scribe_symbols(repo_dir)
    diff = _symbol_diff(baseline.get("symbols") or set(), cur.get("symbols") or set())
    ok = bool(cur.get("ok")) and not diff["lost"]
    detail = {"scanned": cur.get("summary") or "", "lost": diff["lost"][:8],
              "renamed": diff["renamed"][:8], "gained": diff["gained"][:8]}
    return {"ok": ok, "detail": json.dumps(detail, ensure_ascii=False)[:500],
            "error": "" if ok else ("scribe 扫描失败：" + str(cur.get("error"))
                                    if not cur.get("ok") else "符号丢失：" + ",".join(diff["lost"][:6]))}


async def _step_zcode_verify(job_id: str, target_file: str) -> dict:
    """Step 3：ZCode 验证。委员会把目标文件快照投进沙箱 stage → ZCode（edit 模式）
    写独立校验脚本与 RESULT.txt → 委员会对**真实目标文件**跑 py_compile（权威闸）。
    ZCode 不可用时诚实降级：zcode_ran=False，仅以委员会 py_compile 判定。"""
    stage = os.path.join(_STAGE_ROOT, str(job_id))
    res = {"ok": False, "zcode_ran": False, "zcode_detail": "", "compile_error": ""}
    try:
        os.makedirs(stage, exist_ok=True)
    except OSError as e:
        res["compile_error"] = "stage 目录创建失败：%s" % e
        return res
    # 快照投喂（io.open 纪律）：ZCode 只见 target.py 副本，真实文件不受它影响
    try:
        with io.open(str(target_file), "r", encoding="utf-8") as f:
            src = f.read()
        with io.open(os.path.join(stage, "target.py"), "w", encoding="utf-8") as f:
            f.write(src)
    except (OSError, UnicodeError) as e:
        res["compile_error"] = "目标文件读取失败：%s" % e
        return res

    adapter = ZCodeAdapter()
    reason = adapter.unavailable_reason()
    if not reason:
        res["zcode_ran"] = True
        prompt = (
            "目录下有 target.py。请创建 verify_syntax.py：读取 target.py 源码并用 "
            "ast.parse 做语法解析；解析成功就把 RESULT.txt 写成 OK，失败写成 "
            "FAIL:<原因>。不要修改 target.py，完成即结束。")
        task = {"action": "run_zcode", "prompt": prompt, "cwd": stage, "timeout_sec": 240}
        try:
            tid = await asyncio.to_thread(adapter.start, task)
            deadline = time.monotonic() + ZCODE_VERIFY_BUDGET
            status = ""
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                st = await asyncio.to_thread(adapter.status, tid)
                status = str(st.get("status") or "")
                if status in (STATUS_COMPLETED, STATUS_FAILED):
                    break
            rec = await asyncio.to_thread(adapter.result, tid)
            status = str(rec.get("status") or status)
            result_path = os.path.join(stage, "RESULT.txt")
            zc = ""
            if os.path.isfile(result_path):
                with io.open(result_path, "r", encoding="utf-8", errors="replace") as f:
                    zc = (f.read() or "").strip()[:200]
            res["zcode_detail"] = "status=%s exit=%s RESULT=%s" % (
                status, rec.get("exit_code"), zc or "(未产出)")
            if status != STATUS_COMPLETED or zc.upper().startswith("FAIL"):
                res["error_zcode"] = "ZCode 验证未通过：%s" % res["zcode_detail"]
        except Exception as e:  # noqa: BLE001 - ZCode 面失败降级为委员会单判
            res["error_zcode"] = "ZCode 任务异常：%s: %s" % (type(e).__name__, str(e)[:160])
    else:
        res["zcode_detail"] = "zcode_unavailable: %s" % reason

    # 权威闸：委员会自己对真实目标文件跑 py_compile（ZCode 只是独立第二意见）
    cfile = os.path.join(stage, "commit_check.pyc")
    try:
        await asyncio.to_thread(py_compile.compile, str(target_file), cfile=cfile, doraise=True)
    except py_compile.PyCompileError as e:
        res["compile_error"] = str(e)[:400]
    except Exception as e:  # noqa: BLE001
        res["compile_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
    res["ok"] = not res["compile_error"] and not res.get("error_zcode")
    return res


# ---------------------------------------------------------------- 闭环编排

async def multi_agent_code_review(job_id: str, target_file: str,
                                  plan_json: str = "", intent: str = "",
                                  max_retries: int = MAX_RETRIES_DEFAULT) -> dict:
    """任务书同名编排函数：Surgeon 动手 → Scribe 审查 → ZCode 验证 → 汇总，
    失败打回修改方（intent 模式反馈给 Surgeon 的规划 LLM）重试 ≤ max_retries。
    返回完整报告（写入 _JOBS 供 GET 轮询；事件逐条上轴 synapse）。"""
    report = {"job_id": job_id, "target_file": target_file, "mode": "plan" if plan_json else "intent",
              "max_retries": max_retries, "attempts": [], "ok": False,
              "verdict": "", "created_at": _now(), "finished_at": ""}
    repo_dir = ""
    bad, repo_dir = await asyncio.to_thread(_validate_target, target_file)
    if bad:
        report["verdict"] = "rejected: " + bad
        _trace("committee.review.failed", "审查委员会拒绝受理 %s：%s" % (target_file, bad))
        return report

    baseline = await _scribe_symbols(repo_dir)
    if not baseline.get("ok"):
        report["verdict"] = "rejected: scribe 基线扫描失败 " + str(baseline.get("error"))[:120]
        _trace("committee.review.failed", "审查委员会基线扫描失败：%s" % report["verdict"])
        return report

    _trace("committee.review.started",
           "审查委员会开审 %s（%s 模式，最多 %d 轮；基线符号 %d 个）"
           % (target_file, report["mode"], max_retries + 1, len(baseline.get("symbols") or ())))

    feedback = ""
    for attempt_no in range(1, max(1, int(max_retries)) + 2):
        attempt = {"attempt": attempt_no, "surgeon": {}, "scribe": {}, "zcode": {},
                   "verdict": ""}
        _trace("surgeon.refactor.started",
               "[第%d轮] Surgeon 动手：%s" % (attempt_no, (intent or "执行计划")[:80]))
        the_intent = intent
        if feedback and intent:
            the_intent = "%s\n\n上一轮审查未通过，请修正：%s" % (intent, feedback[:400])
        surgeon = await _step_surgeon(repo_dir, plan_json, the_intent)
        attempt["surgeon"] = {"ok": surgeon["ok"], "mode": surgeon["mode"],
                              "detail": surgeon["detail"][:300], "error": surgeon["error"]}
        _trace("surgeon.refactor.%s" % ("succeeded" if surgeon["ok"] else "failed"),
               "[第%d轮] Surgeon %s：%s" % (attempt_no, "完成" if surgeon["ok"] else "失败",
                                            (surgeon["error"] or surgeon["detail"])[:150]))
        if not surgeon["ok"]:
            attempt["verdict"] = "surgeon_failed"
            feedback = "Surgeon 执行失败：" + surgeon["error"]
            report["attempts"].append(attempt)
            continue

        _trace("scribe.audit.started", "[第%d轮] Scribe 审查：符号/语法比对" % attempt_no)
        scribe = await _step_scribe_audit(repo_dir, baseline)
        attempt["scribe"] = {"ok": scribe["ok"], "detail": scribe["detail"][:300],
                             "error": scribe["error"]}
        _trace("scribe.audit.%s" % ("succeeded" if scribe["ok"] else "failed"),
               "[第%d轮] Scribe %s：%s" % (attempt_no, "通过" if scribe["ok"] else "不通过",
                                           (scribe["error"] or scribe["detail"])[:150]))

        zcode = {"ok": True, "detail": "skipped(surgeon/scribe 未过)"}
        if scribe["ok"]:
            _trace("zcode.verify.started",
                   "[第%d轮] ZCode 验证：沙箱内写校验脚本 + 委员会 py_compile" % attempt_no)
            zcode = await _step_zcode_verify(job_id, target_file)
            attempt["zcode"] = {"ok": zcode["ok"], "zcode_ran": zcode["zcode_ran"],
                                "detail": (zcode.get("zcode_detail") or "")[:200],
                                "compile_error": (zcode.get("compile_error") or "")[:200]}
            _trace("zcode.verify.%s" % ("succeeded" if zcode["ok"] else "failed"),
                   "[第%d轮] ZCode %s：%s" % (attempt_no, "通过" if zcode["ok"] else "不通过",
                                              (zcode.get("error_zcode") or zcode.get("compile_error")
                                               or zcode.get("zcode_detail") or "")[:150]))

        if surgeon["ok"] and scribe["ok"] and zcode["ok"]:
            attempt["verdict"] = "pass"
            report["attempts"].append(attempt)
            report["ok"] = True
            report["verdict"] = "第 %d 轮通过：Surgeon/Scribe/ZCode 三票全绿" % attempt_no
            _trace("committee.review.succeeded",
                   "审查委员会裁决通过（第 %d 轮）：%s" % (attempt_no, target_file))
            break
        attempt["verdict"] = "fail"
        report["attempts"].append(attempt)
        feedback = "; ".join(x for x in (
            "" if surgeon["ok"] else "Surgeon:" + surgeon["error"],
            "" if scribe["ok"] else "Scribe:" + scribe["error"],
            "" if zcode["ok"] else "ZCode:" + str(zcode.get("error_zcode") or zcode.get("compile_error")),
        ) if x)
        if plan_json:
            # plan 模式无规划 LLM 可打回：仅当上一轮 Surgeon 自身没跑成才值得重试
            if surgeon["ok"]:
                break
    if not report["ok"]:
        report["verdict"] = report["verdict"] or (
            "重试 %d 轮后未通过：%s" % (len(report["attempts"]), feedback[:300]))
        _trace("committee.review.failed", "审查委员会裁决不通过：%s（%s）"
               % (target_file, report["verdict"][:200]))
    report["finished_at"] = _now()
    return report


# ---------------------------------------------------------------- HTTP 面

def _spawn(job_id: str, target_file: str, plan_json: str, intent: str,
           max_retries: int) -> None:
    """后台跑委员会（asyncio 任务强引用防 GC；收尾把报告回存作业表）。"""
    rec = _JOBS.get(job_id) or {}

    def _on_done(t: "asyncio.Task") -> None:
        rec = _JOBS.get(job_id) or {}
        rec.pop("_task", None)
        try:
            rec["report"] = t.result()
        except Exception as e:  # noqa: BLE001 - 编排函数任何异常都落报告不吞
            rec["report"] = {"ok": False, "verdict": "committee_crash: %s: %s"
                             % (type(e).__name__, str(e)[:200])}
        rec["status"] = "done"

    task = asyncio.get_running_loop().create_task(multi_agent_code_review(
        job_id, target_file, plan_json=plan_json, intent=intent, max_retries=max_retries))
    rec["_task"] = task
    task.add_done_callback(_on_done)


@router.post("/api/review/committee")
async def review_committee_start(request: Request):
    """受理一次审查（任务书高层工具入口）：{target_file, plan_json?|intent?, max_retries?}"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "message": "请求体必须是 JSON"})
    target = str(body.get("target_file") or "").strip()
    plan_json = str(body.get("plan_json") or "").strip()
    intent = str(body.get("intent") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"ok": False, "message": "target_file 必填"})
    if not plan_json and not intent:
        return JSONResponse(status_code=400, content={
            "ok": False, "message": "plan_json（确定性计划）与 intent（Surgeon LLM 规划）至少给一个"})
    try:
        max_retries = max(0, min(2, int(body.get("max_retries", MAX_RETRIES_DEFAULT))))
    except (TypeError, ValueError):
        max_retries = MAX_RETRIES_DEFAULT
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {"job_id": job_id, "target_file": target, "status": "running",
                     "report": None, "created_at": _now()}
    _spawn(job_id, target, plan_json, intent, max_retries)
    return {"ok": True, "data": {"job_id": job_id, "status": "running"}}


@router.get("/api/review/committee/{job_id}")
async def review_committee_status(job_id: str):
    rec = _JOBS.get(job_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": "job 不存在"})
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    if rec.get("_task") and not rec["_task"].done():
        out["status"] = "running"
    elif out.get("status") == "running":
        out["status"] = "done"
    return {"ok": True, "data": out}


@router.get("/api/review/committee")
async def review_committee_list():
    items = [{k: v for k, v in rec.items() if not k.startswith("_") and k != "report"}
             for rec in _JOBS.values()]
    items.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"ok": True, "data": {"jobs": items[:20]}}
