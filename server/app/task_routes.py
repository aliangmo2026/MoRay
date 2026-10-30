# -*- coding: utf-8 -*-
"""/api/tasks/* —— 对话触发任务编排（L4 最小验证，2026-09-28）。

端点（信封范式 = argus_proxy/log-intent：{ok,data} / {ok,code,message}）：
  POST /api/tasks/run-error-fix   提交一次报错修复任务（异步，202 受理）
      body: {error_image_base64?, error_text?, project_dir?, project_id?,
             verify_cmd?, token_budget?}
      有图 → 写临时文件走 templates/error_fix_flow.yaml（analyze 步骤硬依赖截图路径）；
      无图有文本 → 走 templates/text_fix_flow.yaml（analyze 用 iris_analyze_text，
      log-intent 已在用同一工具，adapters/routes.py:90 先例）。
      内部 = TaskTemplateRunner（engine/task_runner.py，零修改）+ asyncio.create_task
      后台执行 + on_step 钩子（task_runner.py:182 现成）实时写模块级状态表。
  GET /api/tasks/{task_id}        轮询：进行中返回步骤进度，终态返回报告/diff/人工介入提示。

先例对齐：
  * 提交+轮询 = agent_runner（POST /api/agent/run :663 + GET /api/agent/tasks/<id> :704）；
  * 内存状态表 = agent_runner._RUNS（单事件循环内同步变更，读侧原子快照）；
  * 挂载 = api.py 尾部折叠（:341-342 adapters 两行先例），main.py 零改动。

[L5.0 增量 2026-09-28]（本文件按任务书允许改动，见 docs/l50_audit.md §7）：
  * 阶段4 状态持久化：work/task_state/{task_id}.json（受理/每步/终态三处落盘，24h 懒清理，
    导入期回填；重启前 running 残档诚实改判 failed）+ conversation_id 归属 + GET /api/tasks/unfinished；
  * 阶段3 串联：_extract_final 支持多份报告拼接成综合报告；
  * 阶段5：POST /api/tasks/run-review-flow（code_review_flow 模板受理）。

约束（阶段0 红线）：不新增后端服务、不修改任何既有函数体（除上列 L5 增量声明项）；
⚠️ 降级通道 llm_patch 的网关地址在 engine/task_runner.py 模块级读取
   env IRIS_GATEWAY_URL（默认 127.0.0.1:8000）——隔离实例部署须传该环境变量。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine.task_runner import TaskTemplateRunner
from knowledge.experience_base import ExperienceBase
from . import config
from . import organ_memory

router = APIRouter()
logger = logging.getLogger("moray-tasks")

TEMPLATES_DIR = Path(config.SERVER_DIR).parent / "templates"
UPLOAD_DIR = Path(config.SERVER_DIR).parent / "work" / "mcp_phase" / "uploads"
# [L5.0 阶段4] 任务状态落盘目录（work/task_state/{task_id}.json，终态 24h 后懒清理）
STATE_DIR = Path(config.SERVER_DIR).parent / "work" / "task_state"
STATE_TTL_SEC = 24 * 3600

MAX_IMAGE_BYTES = 8 * 1024 * 1024          # base64 解码后的图片上限
MAX_TEXT_CHARS = 3000                       # 报错文本上限（与 llm_patch err 截断同量级）
DEFAULT_TOKEN_BUDGET = 8000                 # task_runner docstring 用法先例
RUN_HISTORY = 50                            # 状态表容量（超出丢最旧终态，agent_runner 同法）

# 步骤 id → 人话阶段名（卡片显示"分析中/修复中/验证中"，任务书 2.3）
STAGE_LABEL = {
    "read_experience": "读取经验",
    "analyze_error": "分析报错",
    "analyze_text": "分析报错",
    "analyze_failures": "分析失败",
    "fix_code": "修复代码",
    "run_tests": "运行测试",
    "verify_in_sandbox": "沙箱验证",
    "generate_report": "生成报告",
    "record_experience": "经验沉淀",
    "collect_recent": "收集近期代码",      # [L5.0 阶段5]
    "llm_review": "AI 代码审查",           # [L5.0 阶段5]
    "suggest_tests": "生成测试建议",        # [L6.5 阶段4] 合成步骤（runner 层，模板零改动）
}

# task_id → record（单事件循环线程内变更；GET 读的是同步构造的快照 dict）
_TASKS: Dict[str, Dict[str, Any]] = {}
_ORDER: List[str] = []

_DATA_URL_RE = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


# ---------------- [L5.0 阶段4] 状态落盘 / 懒清理 / 重启回填 ----------------
# 设计（docs/l50_audit.md §4）：受理即写、每步 on_step 更新、终态收尾；
# 模块导入时扫目录回填 _TASKS（重启前 running 无幸存协程 → 诚实标 failed 中断）；
# 24h 清理搭在每次写入后顺手做（**不加 lifespan 后台循环**，避开僵尸调度器陷阱）。

def _state_path(task_id: str) -> Path:
    # 纵深防御：task_id 正常是 uuid hex[:16]，但状态文件落盘名仍要防目录穿越
    # （"../evil" 过完字符白名单还剩 ".."——strip 掉边界点/下划线才安全；L5.0 单测抓出）。
    safe = re.sub(r"[^\w.\-]", "_", str(task_id)).strip("._-")
    return STATE_DIR / ("%s.json" % (safe or "task"))


def _cleanup_state() -> None:
    """终态且 mtime 超 24h 的文件删除；running 文件永不删（persist 会持续刷新 mtime）。"""
    try:
        if not STATE_DIR.is_dir():
            return
        now = time.time()
        for f in STATE_DIR.glob("*.json"):
            try:
                if now - f.stat().st_mtime <= STATE_TTL_SEC:
                    continue
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("status") != "running":
                    f.unlink()
            except (OSError, json.JSONDecodeError):
                continue
    except OSError as e:  # noqa: BLE001
        logger.warning("[tasks] state cleanup failed: %s", e)


def _persist(rec: Dict[str, Any]) -> None:
    """状态文件原子写（tmp→replace）。持久化失败绝不影响内存态与任务执行。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        final = _state_path(rec["task_id"])
        tmp = final.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        tmp.replace(final)
        _cleanup_state()
    except Exception as e:  # noqa: BLE001
        logger.warning("[tasks] persist %s failed: %s", rec.get("task_id"), e)


def _load_state() -> int:
    """导入期回填：终态记录 24h 窗口内 GET 仍可用；running 残档改判 failed。"""
    n = 0
    try:
        if not STATE_DIR.is_dir():
            return 0
        files = sorted(STATE_DIR.glob("*.json"),
                       key=lambda f: f.stat().st_mtime if f.is_file() else 0)
    except OSError:
        return 0
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rec, dict) or not rec.get("task_id"):
            continue
        if rec.get("status") == "running":
            rec["status"] = "failed"
            rec["error"] = "backend_restarted"
            rec["human_intervention"] = ("后端重启，任务中断（协程未幸存，不假装续跑）。"
                                         "请在对话里重新触发，或直接调提交端点重跑。")
            rec.setdefault("ended_at", time.time())
            _persist(rec)
        _TASKS[rec["task_id"]] = rec
        if rec["task_id"] not in _ORDER:
            _ORDER.append(rec["task_id"])
        n += 1
    while len(_ORDER) > RUN_HISTORY:
        old = _ORDER[0]
        if _TASKS.get(old, {}).get("status") == "running":
            break
        _ORDER.pop(0)
        _TASKS.pop(old, None)
    return n


_LOADED_AT_IMPORT = _load_state()


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "code": code, "message": message})


def _b64_to_png_bytes(raw: str) -> Optional[bytes]:
    """剥 dataURL 前缀（后端防御式，前端可传裸 base64）→ 解码；非法返回 None。"""
    import base64
    s = str(raw or "").strip()
    if not s:
        return None
    s = _DATA_URL_RE.sub("", s, count=1)
    try:
        data = base64.b64decode(s, validate=True)
    except Exception:  # noqa: BLE001
        return None
    return data if 0 < len(data) <= MAX_IMAGE_BYTES else None


def _summarize_step(o) -> Dict[str, Any]:
    return {"id": o.id, "type": o.type,
            "label": STAGE_LABEL.get(o.id, o.type),
            "status": o.status, "engine": o.engine,
            "attempts": o.attempts, "duration_ms": o.duration_ms,
            "error": o.error[:300]}


def _record_view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """GET 的统一出参（进行中/终态同一形状；终态多报告/diff/human_intervention）。"""
    current = None
    if rec["status"] == "running":
        # on_step 仅在步骤完成后回调 → 当前阶段=模板步序中第一个未完成的
        done = {s["id"] for s in rec["steps"] if s["status"] == "ok"}
        for sid in rec.get("step_order") or []:
            if sid not in done:
                current = STAGE_LABEL.get(sid, sid)
                break
    view = {
        "task_id": rec["task_id"],
        "template": rec["template"],
        "status": rec["status"],                       # running | completed | failed
        "started": rec["started"],
        # [L5.0 阶段4] 终态后计时冻结在 ended_at（重启回填的档案不会再"越查越久"）
        "duration_sec": round((rec.get("ended_at") or time.time()) - rec["t0"], 1),
        "steps": rec["steps"],
        "current_stage": current if rec["status"] == "running" else None,
        "work_dir": rec["work_dir"],
        "conversation_id": rec.get("conversation_id", ""),   # [L5.0 阶段4] 刷新恢复的归属线索
    }
    if rec["status"] != "running":
        view.update({
            "tokens_spent": rec.get("tokens_spent"),
            "report": rec.get("report", ""),
            "diff": rec.get("diff", ""),
            "human_intervention": rec.get("human_intervention"),
            "error": rec.get("error", ""),
        })
    return view


def _extract_final(rec: Dict[str, Any], rep: Dict[str, Any]) -> None:
    """终态 progress dict → record（报告读 fix_report.md；diff 取 fix_code 输出）。"""
    rec["tokens_spent"] = rep.get("tokens_spent")
    rec["human_intervention"] = rep.get("human_intervention")
    steps = rep.get("steps") or []
    if steps:                                          # 用权威终态覆盖增量摘要
        rec["steps"] = [dict(_summarize_step_from_brief(s),
                             label=STAGE_LABEL.get(s.get("id"), s.get("type", "")))
                        for s in steps]
    reports: List[str] = []
    for s in steps:
        out = s.get("output") or {}
        d = out.get("diff") or ""
        if d and not rec.get("diff"):
            rec["diff"] = str(d)[:6000]
        rp = out.get("path") or ""
        if s.get("type") == "report" and rp:
            try:
                reports.append(Path(rp).read_text(encoding="utf-8", errors="replace")[:4000])
            except Exception:  # noqa: BLE001
                reports.append("(报告文件读取失败：%s)" % rp)
    # [L5.0 阶段3] 串联会产出多份报告（fix_report + test_report）→ 综合成一份；
    # 单报告保持原文不加装饰（既有测试钉的是包含关系，行为零变化）。
    if reports:
        rec["report"] = reports[0] if len(reports) == 1 \
            else "\n\n════ 串联报告 · 下一段 ════\n\n".join(reports)


def _summarize_step_from_brief(s: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": s.get("id"), "type": s.get("type"),
            "status": s.get("status"), "engine": s.get("engine", "-"),
            "attempts": s.get("attempts", 0), "duration_ms": s.get("duration_ms", 0),
            "error": str(s.get("error") or "")[:300]}


def _register(rec: Dict[str, Any]) -> None:
    _TASKS[rec["task_id"]] = rec
    _ORDER.append(rec["task_id"])
    while len(_ORDER) > RUN_HISTORY:
        old = _ORDER[0]
        if old == rec["task_id"]:
            break
        if _TASKS.get(old, {}).get("status") == "running":
            break
        _ORDER.pop(0)
        _TASKS.pop(old, None)


#: [器官→记忆 线D] 只有这两个修复模板算"Iris 线留痕"；test_run/review 同走
#: _guarded_run 但不是器官产出，不写（避免记忆库混入非器官条目、标签失真）。
_ORGAN_FIX_TEMPLATES = ("error_fix_flow", "text_fix_flow")


# 写入数据源：rec 不落 params（project_dir/project_id），从 runner 取。
def _build_fix_memory_payload(task_id: str, rec: Dict[str, Any],
                              rep: Dict[str, Any], runner: Any) -> Dict[str, Any]:
    """[器官→记忆 线D] 修复任务成功 → organ_memory.write_organ_memory 的 kwargs。

    content 三行式（对齐 Laya M2 形态）：修复/诊断/补丁｜验证。数据源：
      * rec：template/diff（_extract_final 已收尾）；
      * rep["steps"]：analyze_* 步 output.data（error_type/file_path）、verify 步状态；
      * runner.params：project_dir/project_id（rec 不落 params，从 runner 取）。
    任何字段缺失兜底成占位文本，绝不 KeyError。"""
    params = getattr(runner, "params", None) or {}
    project_dir = str(params.get("project_dir") or rec.get("work_dir") or "")
    project_id = str(params.get("project_id") or "").strip()
    error_type = ""
    file_path = ""
    verify_status = ""
    for s in (rep.get("steps") or []):
        sid = str(s.get("id") or "")
        if sid.startswith("analyze") and not error_type:
            out = s.get("output")
            data = out.get("data") if isinstance(out, dict) else None
            if isinstance(data, dict):
                error_type = str(data.get("error_type") or "")
                file_path = str(data.get("file_path") or "")
        elif sid.startswith("verify") or sid.startswith("run_tests"):
            verify_status = str(s.get("status") or "")
    diagnosis = "/".join(x for x in (error_type, file_path) if x)[:200] or "未出诊断"
    content = "修复：%s·%s\n诊断：%s\n补丁：%s｜验证：%s" % (
        str(rec.get("template") or rep.get("task") or ""),
        Path(project_dir).name if project_dir else "?",
        diagnosis,
        str(rec.get("diff") or "")[:400],
        verify_status or "无验证步")
    tags = ["iris_fix"] + ([project_id] if project_id else [])
    return {"content": content, "kind": "task_result", "tags": tags,
            "source": "task_flow", "entity_id": str(task_id)}


def _verify_step_failed(rep: Dict[str, Any]) -> bool:
    """verify/run_tests 步是否 failed（写记忆闸收紧，2026-10-06 反思防污染）。

    completed 只代表流水线走完——verify_in_sandbox 带 continue_on_fail: true，
    verify 失败不拦终态（v29 实证：b93b8beb completed+验证：failed 照样入库，
    反思从"ok/failed 摇摆"学出反向 insight）。判据与 _build_fix_memory_payload
    的 verify_status 同源：rep["steps"] 里 id 以 verify/run_tests 开头的最后一个
    步的 status（task_runner.py:100 取值 ok|failed|skipped）；无 verify 步 →
    不算 failed，维持"无验证步"原行为。"""
    verify_status = ""
    for s in (rep.get("steps") or []):
        sid = str(s.get("id") or "")
        if sid.startswith("verify") or sid.startswith("run_tests"):
            verify_status = str(s.get("status") or "")
    return verify_status == "failed"


async def _guarded_run(task_id: str, runner: TaskTemplateRunner) -> None:
    rec = _TASKS.get(task_id)
    if rec is None:
        return
    try:
        rep = await runner.run()
        rec["status"] = "completed" if rep.get("status") == "completed" else "failed"
        _extract_final(rec, rep)
        rec["ended_at"] = time.time()               # [L5.0 阶段4] 终态冻结计时 + 收尾落盘
        _persist(rec)
        logger.warning("[tasks] %s finished: %s (%.1fs)", task_id, rec["status"],
                       rep.get("duration_sec") or 0)
        # ---- [器官→记忆 线D] 修复任务成功（completed 且报告非空且验证未失败）→ 旁路写一条记忆 ----
        # 仅 error_fix/text_fix 两个修复模板（_ORGAN_FIX_TEMPLATES）；失败/无报告一律
        # 不写（对齐 Laya "成功才钩"）；异常单独吞在此，绝不外翻已落定的终态判定。
        # [反思防污染 2026-10-06] verify 步 failed 的带伤 completed 不写（判据见
        # _verify_step_failed）；存量库不回溯清理。
        if (rec["status"] == "completed" and (rec.get("report") or "").strip()
                and str(rec.get("template") or "") in _ORGAN_FIX_TEMPLATES):
            if _verify_step_failed(rep):
                logger.warning("[iris-memory] task=%s verify 步 failed，带伤 completed 不写记忆",
                               task_id)
            else:
                try:
                    payload = _build_fix_memory_payload(task_id, rec, rep, runner)
                    organ_memory.write_organ_memory(**payload)
                    # [Synapse v4 第三步] 修复成功 → 总线事件（判据与记忆写入同源；
                    # send 总闸关/异常均自吞 no-op，绝不影响收尾）。
                    from . import synapse_bus
                    synapse_bus.send(
                        "iris.fix.succeeded", payload.get("content") or "",
                        thread_id=None,
                        meta={"trace_id": task_id, "task_id": task_id,
                              "template": str(rec.get("template") or ""),
                              "duration_ms": int(((rec.get("ended_at") or 0.0)
                                                  - (rec.get("t0") or 0.0)) * 1000)},
                        from_member="organ.iris")
                except Exception as e:  # noqa: BLE001 - 旁路写入失败绝不影响收尾
                    logger.warning("[iris-memory] 写入钩子异常：task=%s err=%s: %s",
                                   task_id, type(e).__name__, str(e)[:200])
    except Exception as e:  # noqa: BLE001
        rec["status"] = "failed"
        rec["error"] = "%s: %s" % (type(e).__name__, e)
        rec["ended_at"] = time.time()
        _persist(rec)
        logger.warning("[tasks] %s crashed: %s", task_id, e)


def _persisting_step_hook(rec: Dict[str, Any]):
    """on_step 钩子：内存追加 + 状态文件更新（阶段4刷新恢复的数据来源）。"""
    def hook(o) -> None:
        rec["steps"].append(_summarize_step(o))
        _persist(rec)
    return hook


@router.post("/api/tasks/run-error-fix")
async def run_error_fix(request: Request):
    """提交 error_fix_flow 任务（202 受理，后台执行，前端轮询 GET /api/tasks/{id}）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")

    image_b64 = str(body.get("error_image_base64") or "").strip()
    error_text = str(body.get("error_text") or "").strip()[:MAX_TEXT_CHARS]
    if not image_b64 and not error_text:
        return _err(400, "bad_request", "需提供报错截图（error_image_base64）或报错文本（error_text）。")

    img_bytes = _b64_to_png_bytes(image_b64) if image_b64 else None
    if image_b64 and img_bytes is None:
        return _err(400, "bad_image", "error_image_base64 解码失败或超过 8MB 上限。")

    project_dir_raw = str(body.get("project_dir") or "").strip().strip('"')
    project_dir = Path(project_dir_raw) if project_dir_raw else Path(config.WORKSPACE_DEFAULT)
    if not project_dir.is_dir():
        return _err(400, "path_not_found", "项目目录不存在：%s（可随请求传 project_dir 指定）" % project_dir)

    project_id = str(body.get("project_id") or "").strip() or project_dir.name
    verify_cmd = str(body.get("verify_cmd") or "").strip() or "python -m py_compile main.py && python main.py"
    try:
        budget = int(body.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    except (TypeError, ValueError):
        budget = DEFAULT_TOKEN_BUDGET
    # [L5.0 阶段4] 对话归属：前端带 AppState.activeConv.id，刷新后靠它找回任务卡
    conv_id = str(body.get("conversation_id") or "").strip()[:64]

    task_id = uuid.uuid4().hex[:16]
    template_name = "error_fix_flow" if img_bytes else "text_fix_flow"
    tpl_path = TEMPLATES_DIR / (template_name + ".yaml")
    if not tpl_path.is_file():
        return _err(500, "template_missing", "任务模板缺失：%s" % tpl_path)

    params: Dict[str, Any] = {
        "project_dir": str(project_dir.resolve()),
        "project_id": project_id,
        "verify_cmd": verify_cmd,
    }
    if img_bytes:
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            shot = UPLOAD_DIR / ("errfix_%s.png" % task_id)
            shot.write_bytes(img_bytes)
        except Exception as e:  # noqa: BLE001
            return _err(500, "upload_failed", "截图落盘失败：%s" % e)
        params["screenshot"] = str(shot)
    else:
        params["error_text"] = error_text

    try:
        runner = TaskTemplateRunner(str(tpl_path), params=params, token_budget=budget)
    except Exception as e:  # noqa: BLE001
        return _err(500, "template_load_failed", "模板加载失败：%s" % e)

    rec = {"task_id": task_id, "template": template_name, "status": "running",
           "t0": time.time(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "steps": [], "work_dir": str(runner.work_dir),
           "step_order": [str(s.get("id") or "") for s in (runner.tpl.get("steps") or [])],
           "report": "", "diff": "", "error": "",
           "human_intervention": None, "tokens_spent": 0,
           "conversation_id": conv_id, "ended_at": None}
    _register(rec)
    runner.on_step = _persisting_step_hook(rec)      # [L5.0 阶段4] 每步同步落状态文件
    _persist(rec)                                    # 受理即建档（重启/刷新恢复的最初快照）
    asyncio.create_task(_guarded_run(task_id, runner))
    logger.warning("[tasks] %s started template=%s work_dir=%s", task_id, template_name,
                   runner.work_dir)
    return _ok({"task_id": task_id, "status": "running", "template": template_name,
                "work_dir": str(runner.work_dir)}, status=202)


# ---- [L4.5 阶段3.3] 候选项目列表（供对话触发任务的"项目选择卡"展示）----
# ⚠️ 必须注册在 GET /api/tasks/{task_id} **之前**：FastAPI 同路径前缀按注册序匹配，
#    放后面会被 {task_id} 吞掉变成 404 not_found。
@router.get("/api/tasks/projects")
def tasks_projects():
    """返回配置的项目列表（config.MORAY_TASK_PROJECTS）+ 受控工作区兜底，
    不存在的目录过滤（与 run-error-fix 的 is_dir 校验同语义）。
    最近使用记录在浏览器 localStorage（裸键范式），后端保持无状态。"""
    items = [str(p) for p in (config.TASK_PROJECTS or ())]
    if config.WORKSPACE_DEFAULT not in items:
        items.append(config.WORKSPACE_DEFAULT)
    out = []
    for p in items:
        try:
            path = Path(p)
            if path.is_dir():
                out.append({"path": str(path.resolve()), "name": path.name})
        except OSError:  # 非法路径配置静默过滤（诚实：出参里不出现点不开的项）
            continue
    return _ok({"projects": out, "default": (out[0]["path"] if out else "")})


# ---- [L4.5 阶段3.3] POST /api/tasks/run-test-flow —— 第二任务模板受理端点 ----
# 镜像 run-error-fix：同一状态表/轮询/报告管线；差异仅在入参（test_cmd）与模板。
# 步骤名人话映射见 STAGE_LABEL（run_tests/analyze_failures 已补键）。
# [L4.5] DEFAULT_TEST_CMD 裸 "python -m pytest" 在多机上必挂（PATH 里的 python 常无 pytest）。
# 探测优先级：项目自带 .venv/venv（Windows/Linux 布局）> 裸 python。
def _default_test_cmd(project_dir: Path) -> str:
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


DEFAULT_TEST_CMD = "python -m pytest -q"   # 无 venv 探测命中时的最终兜底（单测引用此常量）


@router.post("/api/tasks/run-test-flow")
async def run_test_flow(request: Request):
    """提交 test_run_flow 任务（202 受理，后台执行，轮询共用 GET /api/tasks/{id}）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")

    # 项目目录：与 run-error-fix 同款解析（入参 > WORKSPACE_DEFAULT 兜底）；
    # "帮我跑下测试"允许不带任何附件/文本，缺目录时兜底到受控工作区。
    project_dir_raw = str(body.get("project_dir") or "").strip().strip('"')
    project_dir = Path(project_dir_raw) if project_dir_raw else Path(config.WORKSPACE_DEFAULT)
    if not project_dir.is_dir():
        return _err(400, "path_not_found", "项目目录不存在：%s（请随请求传 project_dir 指定）" % project_dir)

    project_id = str(body.get("project_id") or "").strip() or project_dir.name
    # 显式传入优先；缺省走 venv 探测（项目 .venv > 裸 python）——[L4.5]
    test_cmd = str(body.get("test_cmd") or "").strip() or _default_test_cmd(project_dir)
    try:
        budget = int(body.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    except (TypeError, ValueError):
        budget = DEFAULT_TOKEN_BUDGET
    # [L5.0 阶段4] 对话归属：前端带 AppState.activeConv.id，刷新后靠它找回任务卡
    conv_id = str(body.get("conversation_id") or "").strip()[:64]

    task_id = uuid.uuid4().hex[:16]
    tpl_path = TEMPLATES_DIR / "test_run_flow.yaml"
    if not tpl_path.is_file():
        return _err(500, "template_missing", "任务模板缺失：%s" % tpl_path)

    params: Dict[str, Any] = {
        "project_dir": str(project_dir.resolve()),
        "project_id": project_id,
        "test_cmd": test_cmd,
    }
    try:
        runner = TaskTemplateRunner(str(tpl_path), params=params, token_budget=budget)
    except Exception as e:  # noqa: BLE001
        return _err(500, "template_load_failed", "模板加载失败：%s" % e)

    rec = {"task_id": task_id, "template": "test_run_flow", "status": "running",
           "t0": time.time(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "steps": [], "work_dir": str(runner.work_dir),
           "step_order": [str(s.get("id") or "") for s in (runner.tpl.get("steps") or [])],
           "report": "", "diff": "", "error": "",
           "human_intervention": None, "tokens_spent": 0,
           "conversation_id": conv_id, "ended_at": None}
    _register(rec)
    runner.on_step = _persisting_step_hook(rec)
    _persist(rec)
    asyncio.create_task(_guarded_run(task_id, runner))
    logger.warning("[tasks] %s started template=test_run_flow project=%s work_dir=%s",
                   task_id, params["project_dir"], runner.work_dir)
    return _ok({"task_id": task_id, "status": "running", "template": "test_run_flow",
                "project_dir": params["project_dir"],
                "work_dir": str(runner.work_dir)}, status=202)


# ---- [L5.0 阶段5] POST /api/tasks/run-review-flow —— 第三模板（代码快速审查）受理端点 ----
# 镜像 run-test-flow：同一状态表/持久化/轮询管线；差异仅模板与入参（无 test_cmd）。
@router.post("/api/tasks/run-review-flow")
async def run_review_flow(request: Request):
    """提交 code_review_flow 任务（202 受理，后台执行，轮询共用 GET /api/tasks/{id}）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON。")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象。")

    project_dir_raw = str(body.get("project_dir") or "").strip().strip('"')
    project_dir = Path(project_dir_raw) if project_dir_raw else Path(config.WORKSPACE_DEFAULT)
    if not project_dir.is_dir():
        return _err(400, "path_not_found", "项目目录不存在：%s（请随请求传 project_dir 指定）" % project_dir)

    project_id = str(body.get("project_id") or "").strip() or project_dir.name
    try:
        budget = int(body.get("token_budget") or DEFAULT_TOKEN_BUDGET)
    except (TypeError, ValueError):
        budget = DEFAULT_TOKEN_BUDGET
    conv_id = str(body.get("conversation_id") or "").strip()[:64]

    task_id = uuid.uuid4().hex[:16]
    tpl_path = TEMPLATES_DIR / "code_review_flow.yaml"
    if not tpl_path.is_file():
        return _err(500, "template_missing", "任务模板缺失：%s" % tpl_path)

    params: Dict[str, Any] = {
        "project_dir": str(project_dir.resolve()),
        "project_id": project_id,
    }
    try:
        runner = TaskTemplateRunner(str(tpl_path), params=params, token_budget=budget)
    except Exception as e:  # noqa: BLE001
        return _err(500, "template_load_failed", "模板加载失败：%s" % e)

    rec = {"task_id": task_id, "template": "code_review_flow", "status": "running",
           "t0": time.time(), "started": time.strftime("%Y-%m-%d %H:%M:%S"),
           "steps": [], "work_dir": str(runner.work_dir),
           "step_order": [str(s.get("id") or "") for s in (runner.tpl.get("steps") or [])],
           "report": "", "diff": "", "error": "",
           "human_intervention": None, "tokens_spent": 0,
           "conversation_id": conv_id, "ended_at": None}
    _register(rec)
    runner.on_step = _persisting_step_hook(rec)
    _persist(rec)
    asyncio.create_task(_guarded_run(task_id, runner))
    logger.warning("[tasks] %s started template=code_review_flow project=%s work_dir=%s",
                   task_id, params["project_dir"], runner.work_dir)
    return _ok({"task_id": task_id, "status": "running", "template": "code_review_flow",
                "project_dir": params["project_dir"],
                "work_dir": str(runner.work_dir)}, status=202)


# ---- [L5.1 阶段4] GET /api/experience/profile —— 项目画像读出口 ----
# 画像文件由经验库 record() 聚类顺带产出（work/project_profiles/{hash}.json）；
# 新任务/对话页展示都走这里读，无画像 = found:false（调用方按旧行为降级）。
# 独立 /api/experience/* 前缀，不踩 /api/tasks/{task_id} 通配的注册顺序坑。
@router.get("/api/experience/profile")
def experience_profile(project_id: str = ""):
    pid = str(project_id or "").strip()
    if not pid:
        return _err(400, "bad_request", "project_id 不能为空。")
    eb = ExperienceBase()
    prof = eb.read_profile(pid)
    return _ok({"project_id": pid, "found": prof is not None, "profile": prof,
                "top_root_causes": eb.top_meta(pid, None, 5),
                "hint": eb.build_hint(pid, None, 5)})


# ---- [L5.0 阶段4] GET /api/tasks/unfinished —— 刷新恢复用 ----
# ⚠️ 同样必须注册在 /api/tasks/{task_id} 通配之前（projects 路由同教训）。
@router.get("/api/tasks/unfinished")
def tasks_unfinished():
    """内存表中所有 running 任务（重启回填时 running 残档已改判 failed，不会出现在这）。"""
    views = [_record_view(rec) for rec in _TASKS.values() if rec.get("status") == "running"]
    return _ok({"tasks": views})


@router.get("/api/tasks/{task_id}")
def task_status(task_id: str):
    rec = _TASKS.get(str(task_id or "").strip())
    if rec is None:
        return _err(404, "not_found", "没有该任务的运行记录（可能后端重启过）。")
    return _ok(_record_view(rec))
