from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import httpx
import json
import json_repair
import time
from collections import OrderedDict

app = FastAPI(title="Laya高速决策引擎标准服务")

# ---- [L7.7 2026-09-29 Agent模式前端接入] 加性：CORS 中间件 ----
# MoRay 网页(8000)与 Electron 调试面板需从浏览器直连本机 8001 的 /planner /execute；
# 纯加性挂载，不改任何既有接口的请求/应答体。本机服务、仅放开浏览器跨源读取。
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
# ---- [L7.7 加性块结束] ----

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "moray-laya-final"

# 结果缓存：最多存1000条，1分钟过期
cache = OrderedDict()
CACHE_TTL = 60  # 秒
MAX_CACHE = 1000

# 未知场景落盘日志
unknown_log = open("unknown_scenarios.log", "a", encoding="utf-8")

SYSTEM_PROMPT = """
你是Laya高速决策引擎，只输出结构化JSON，不做任何解释和对话。
规则：
1. 严格按照请求指定的字段输出，不新增、不遗漏
2. 仅输出纯JSON字符串，禁止任何前缀、后缀、代码块标记```、说明文字
3. 置信度保留2位小数，取值范围0-1
4. 输出尽可能简短
"""

class DecisionRequest(BaseModel):
    task_type: str
    input_text: str
    output_fields: dict

# 规则前置层：最确定的场景直接返回，不调用模型，1ms出结果
RULE_MAP = [
    # 代码类 → ZCode
    (["写代码", "写个", "脚本", "bug", "接口", "函数", "命令行", "代码", "报错代码", "修代码", "Python", "脚本", "代码"], "ZCode", "low", False),
    # 资料类 → Argus
    (["查一下", "查最新", "政策", "资讯", "行情", "资料", "报告", "调研", "新闻", "最近的", "新能源", "补贴"], "Argus", "low", False),
    # 视觉类 → Iris
    (["截图", "图片", "看图", "长啥样", "帮我看这张", "界面", "海报", "报错截图"], "Iris", "low", False),
    # 高风险类 → full_model + high
    (["生产", "线上", "删除", "重启", "权限", "配置变更", "nginx", "数据库"], "full_model", "high", True),
    # 复杂方案类 → full_model
    (["方案", "设计", "规划", "排期", "上线"], "full_model", "low", False),
]

def rule_match(text: str):
    text = text.lower()
    for keywords, engine, risk, upgrade in RULE_MAP:
        if any(k.lower() in text for k in keywords):
            return {
                "target_engine": engine,
                "risk_level": risk,
                "should_upgrade": upgrade,
                "confidence": 0.99,
                "reason": "关键词规则命中"
            }
    return None

@app.post("/api/decide")
async def decide(request: DecisionRequest):
    start_time = time.time()
    # 1. 先查缓存
    cache_key = f"{request.task_type}:{request.input_text}"
    now = time.time()
    if cache_key in cache:
        result, expire = cache[cache_key]
        if expire > now:
            return {**result, "mode": "cache", "latency_ms": int((time.time()-start_time)*1000)}
        else:
            del cache[cache_key]

    # 2. 过规则层
    if request.task_type == "intent_route":
        rule_result = rule_match(request.input_text)
        if rule_result:
            result = {"code": 0, "data": rule_result, "mode": "rule", "latency_ms": int((time.time()-start_time)*1000)}
            cache[cache_key] = (result, now + CACHE_TTL)
            if len(cache) > MAX_CACHE:
                cache.popitem(last=False)
            return result

    # 3. 未知场景落盘，后续补规则
    if request.task_type == "intent_route":
        unknown_log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {request.input_text}\n")
        unknown_log.flush()

    prompt = f"""
任务类型：{request.task_type}
输入内容：{request.input_text}
输出字段要求：{json.dumps(request.output_fields, ensure_ascii=False)}
仅输出纯JSON，不要用代码块包裹。
"""
    payload = {
        "model": MODEL_NAME,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 150}
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            raw = resp.json()["response"].strip()
            # 用json_repair自动去掉代码块、修标点
            parsed = json_repair.repair_json(raw)
            if isinstance(parsed, str):
                # [M3b-shimfix 2026-10-02] 真库 0.63.5 返回 JSON 字符串 → 剥成 dict，维持 /api/decide 原 dict 语义
                parsed = json.loads(parsed)
            return {"code": 0, "data": parsed, "mode": "fast", "latency_ms": int(resp.elapsed.total_seconds()*1000)}
    except Exception as e:
        # 失败直接返回降级信号，MoRay自动走大模型
        # [2026-10-01 任务A v2] A2 观测：补 WARNING 日志（原先静默降级零日志）；返回值一字不变
        import logging as _logging
        _logging.getLogger("laya.decide").warning(
            "[decide] Ollama 调用失败：model=%s err=%s: %s",
            MODEL_NAME, type(e).__name__, str(e)[:200])
        return {"code": 500, "fallback": True, "msg": str(e), "mode": "fallback"}

@app.get("/health")
def health():
    return {"status": "running", "port": 8001, "model": MODEL_NAME}

# ---- [L7.5 2026-09-29 MoRay联动] 加性挂载 /api/v1/task/*（dispatch/outcome/rules）----
# 纯追加块：上方既有行未动；逻辑全在 laya_dispatch.py + event_reporter.py（同目录）。
# 事件上报目标由环境变量 LAYA_MORAY_CALLBACK_URL 指定（默认 MoRay 8000 /api/laya/events）。
from laya_dispatch import router as _task_router  # noqa: E402
app.include_router(_task_router)

# ---- [L7.6 2026-09-29 任务编排闭环] 加性挂载 /planner + /execute(SSE) + /execute/cancel ----
# 插入位置=本行原在 `if __name__ == "__main__":` 之前，上方既有行零改动。
# 逻辑全在 modules/ 四文件（task_planner/tool_registry/executor/self_check），本块只做接口薄壳。
# 工具端点按本机实态适配：iris=8004 JSON-RPC(带X-API-Key) argus=8003 /mcp/ search_web
# zcode=8005 /execute（本机暂未监听，走标准错误信封）；file=本地沙箱(FILE_ROOT)。
import asyncio  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from modules import executor as _executor  # noqa: E402
from modules import task_planner as _planner  # noqa: E402


class GoalRequest(BaseModel):
    goal: str


class ExecuteRequest(BaseModel):
    goal: str
    max_retry: int = 5
    execution_id: str | None = None


class CancelRequest(BaseModel):
    execution_id: str


@app.post("/planner")
async def planner(req: GoalRequest):
    """只拆解、不执行：{"goal"} → {"steps":[{step_id,description,tool,input,expected_output}]}。"""
    if not str(req.goal or "").strip():
        return JSONResponse(status_code=400, content={"code": 400, "steps": [], "error": "goal 不能为空"})
    planned = await _planner.plan(req.goal)
    return {"code": 0, "steps": planned["steps"], "mode": planned["mode"]}


def _sse(gen):
    """executor 事件流 → SSE 行。生成器自身异常也要变成 final/failed 帧，不断流报错。"""
    async def wrap():
        try:
            async for ev in gen:
                yield "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            yield "data: " + json.dumps({"event": "final", "status": "failed",
                                          "error": "internal_error: %s: %s" % (type(e).__name__, str(e)[:300])},
                                         ensure_ascii=False) + "\n\n"
    return wrap()


# ---- [M3b 2026-10-02 记忆召回] /execute 结果先行召回长期记忆（MemoryBridge）----
# 总闸 LAYA_MEMORY_RECALL（默认 "0" 关，攒数据后 set 1 + 重启生效）。命中 ≥1 条 →
# 拼好的【长期记忆】文本块作为 memory_context 传给 executor（decision 步注入用，不污染 goal）。
# 形态照 executor._memory_post_worker（httpx 同步 Client + tool_registry.MORAY_BASE），
# 但召回需"结果先行"，故用 asyncio.to_thread 包同步调用（同下方 /api/intent/decide 先例），
# 不用 daemon 线程（拿不到返回值）。任何失败 → 返回 None，执行链零感知（fail-open）。
import os as _os  # noqa: E402
import logging as _m3b_logging  # noqa: E402

_M3B_LOG = _m3b_logging.getLogger("laya.server")


def _memory_recall_sync(query: str) -> str | None:
    """同步召回体：POST /api/memory/recall，命中 ≥1 条 → 【长期记忆】块；否则 None。"""
    try:
        if not str(query or "").strip():
            return None
        base = str(getattr(_executor.tool_registry, "MORAY_BASE",
                           "http://127.0.0.1:8000")).rstrip("/")
        with httpx.Client(timeout=1.5) as client:
            resp = client.post(base + "/api/memory/recall",
                               json={"query": str(query).strip(), "limit": 3})
        if resp.status_code != 200:
            _M3B_LOG.warning("[memory-recall] /api/memory/recall 非 200：status=%s body=%s",
                             resp.status_code, resp.text[:200])
            return None
        items = ((resp.json() or {}).get("data") or {}).get("items") or []
        if not items:
            return None
        lines = []
        for i, it in enumerate(items, 1):
            line = "%d. %s" % (i, str((it or {}).get("content") or "").strip())
            tags = [t for t in ((it or {}).get("tags") or []) if t]
            if tags:
                line += "（标签：%s）" % "/".join(str(t) for t in tags)
            lines.append(line)
        return ("【长期记忆】以下是记忆库中与本次任务相关的既往偏好与事实"
                "（可能过时或不相关，仅在确有帮助时参考，不要凭空引用）：\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001 - 召回失败绝不影响执行链
        _M3B_LOG.warning("[memory-recall] 召回失败（fail-open）：err=%s: %s",
                         type(e).__name__, str(e)[:200])
        return None
# ---- [M3b 记忆召回块结束] ----


# ---- [v3 心智反哺 2026-10-23] 执行引擎读到"自己最近的失败和关注点"（自我模型 v3）----
# 落点与 M3b 记忆召回同构：自调主服务 GET /api/self_narrative/latest → 提取 mental_state
# → 【心智状态】块，与记忆块并入同一 memory_context 通道（decision 步 prompt extra 尾部
# 注入，不污染 goal、executor.py 零改动——goal 有持久化/幂等判重/规则链三处连坐，禁塞）。
# 总闸 LAYA_MENTAL_CONTEXT（默认 "1" 开，0/false/no/off=关断，同 MORAY_SELF_CARD 词表）。
# 渲染格式镜像 server/app/self_card.render_mental_context（两服务进程隔离不能 import，
# 各自本地渲染；措辞变化两处同步改）。任何失败 → None，执行链零感知（fail-open）。
def _mental_state_sync() -> str | None:
    """同步心智块：GET /api/self_narrative/latest → mental_state → 【心智状态】块；否则 None。"""
    try:
        base = str(getattr(_executor.tool_registry, "MORAY_BASE",
                           "http://127.0.0.1:8000")).rstrip("/")
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(base + "/api/self_narrative/latest")
        if resp.status_code != 200:
            _M3B_LOG.warning("[mental-state] /api/self_narrative/latest 非 200：status=%s body=%s",
                             resp.status_code, resp.text[:200])
            return None
        data = resp.json() or {}
        if not (data.get("ok") and data.get("found")):
            return None
        obj = json.loads(str(data.get("content") or ""))
        ms = (obj or {}).get("mental_state") if isinstance(obj, dict) else None
        if not isinstance(ms, dict):
            return None
        focus = str(ms.get("focus_shift") or "").strip()[:120]
        fails = ms.get("recent_failures")
        fail_parts = []
        if isinstance(fails, list):
            for x in fails[:3]:
                s = str(x).strip()[:40]
                if s:
                    fail_parts.append(s)
        if not focus and not fail_parts:
            return None
        return ("【心智状态】以下是自我模型最近的自省产出（历史事实，仅作背景参考）："
                "你最近的心智关注点是：%s（历史失败：%s）。执行任务时请引以为戒。"
                % (focus or "无", "、".join(fail_parts) or "无"))
    except Exception as e:  # noqa: BLE001 - 心智块失败绝不影响执行链
        _M3B_LOG.warning("[mental-state] 取心智失败（fail-open）：err=%s: %s",
                         type(e).__name__, str(e)[:200])
        return None
# ---- [v3 心智反哺块结束] ----


@app.post("/execute")
async def execute(req: ExecuteRequest):
    """目标→拆解→串行执行→自检重试→SSE 实时进度，末帧 event=final 带最终结果。"""
    if not str(req.goal or "").strip():
        return JSONResponse(status_code=400, content={"code": 400, "error": "goal 不能为空"})
    planned = await _planner.plan(req.goal)
    # [M3b 2026-10-02] 开闸（LAYA_MEMORY_RECALL=1）时结果先行召回一次长期记忆；
    # 关闸/失败/零命中 → None，executor 与执行链零感知。to_thread 包同步 httpx（不阻塞事件循环）。
    memory_context = None
    if _os.environ.get("LAYA_MEMORY_RECALL", "0") == "1":
        memory_context = await asyncio.to_thread(_memory_recall_sync, req.goal)
    # [v3 心智反哺 2026-10-23] 心智块（LAYA_MENTAL_CONTEXT 默认开）：有块则并入
    # memory_context 同通道（goal 三处连坐禁塞，decision 步 extra 尾部注入）；
    # 心智块紧随记忆块之后——失败教训压轴收尾，最后读到的最容易被采纳。
    if _os.environ.get("LAYA_MENTAL_CONTEXT", "1").strip().lower() not in ("0", "false", "no", "off"):
        mental_block = await asyncio.to_thread(_mental_state_sync)
        if mental_block:
            memory_context = (memory_context + "\n\n" + mental_block) if memory_context else mental_block
    return StreamingResponse(
        _sse(_executor.execute_stream(req.goal, planned["steps"],
                                      max_retry=max(0, int(req.max_retry)),
                                      execution_id=req.execution_id,
                                      memory_context=memory_context)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/execute/cancel")
async def execute_cancel(req: CancelRequest):
    """取消入口（执行引擎'支持取消'功能的外呼通道）：置位信号，下一步骤边界生效。"""
    return {"code": 0, "cancelled": _executor.cancel(req.execution_id),
            "running": _executor.running_ids()}
# ---- [L7.6 加性块结束] ----


# ---- [L7.8 2026-10-01 意图判定端点] 加性：/api/intent/decide ----
# 接线 intent_router.decide()。注意 decide() 是同步函数，
# 内部 urllib 调 Ollama 最长 3000ms，必须 asyncio.to_thread 包裹，否则阻塞事件循环。
from intent_router import IntentInput, decide as _intent_decide  # noqa: E402
from typing import Optional as _Optional  # noqa: E402

class IntentDecideRequest(BaseModel):
    text: str = ""
    history: list = []
    attachments: list = []
    explicit_mode: _Optional[str] = None

@app.post("/api/intent/decide")
async def intent_decide_endpoint(req: IntentDecideRequest):
    import time as _time
    t0 = _time.perf_counter()
    try:
        inp = IntentInput(
            text=req.text,
            history=req.history,
            attachments=req.attachments,
            explicit_mode=req.explicit_mode,
        )
        result = await asyncio.to_thread(_intent_decide, inp)
        return {
            "code": 0,
            "mode": result.mode,
            "intent_type": result.intent_type,
            "confidence": result.confidence,
            "tools_hint": result.tools_hint,
            "reason": result.reason,
            "decided_by": result.decided_by,
            "latency_ms": int((_time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                "mode": "chat",
                "decided_by": "fallback",
                "latency_ms": int((_time.perf_counter() - t0) * 1000),
            },
        )
# ---- [L7.8 加性块结束] ----


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="error", access_log=False)
