"""Argus 深度研究代理（把 Argus 的研究能力接进前端工具链）

前端 ToolRegistry 里的 ``detective_research`` / ``detective_status`` 调用的是
**同源** 的 ``/api/argus/*``，由本模块转发到本机 Argus 服务（默认
``http://127.0.0.1:8010``）。为什么不让前端直连 Argus 的端口：

* **CORS**：Argus 侧没有 CORS 中间件（它只允许回环调用），而工作台页面无论来自
  ``http://127.0.0.1:8000`` 还是 ``file://``，直连 8010 都是跨源请求 → 浏览器会拦；
  走同源代理则一行都不用改 Argus；
* **单一出口**：模型/浏览器都不需要知道 Argus 的端口，改地址只动 ``server/.env``；
* **错误收敛**：Argus 没起时返回统一 ``{ok:false, code:"argus_offline"}``，
  前端工具把它作为"工具执行失败"回灌给模型，模型据此如实作答而不是卡死。

响应结构与既有 ``/api/agent/tool`` 一致（``{ok:true, data}`` / ``{ok:false, code, message}``），
前端可用同一套判定逻辑处理两类工具。

安全约束：目标地址只来自后端配置（``MORAY_ARGUS_BASE_URL`` / ``config.ARGUS_BASE_URL``），
请求体里指定的地址一律忽略（与 llm_proxy 同样的防 SSRF 策略）；服务只监听 127.0.0.1。
"""
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config
from . import organ_memory

router = APIRouter()
_logger = logging.getLogger("moray.argus")

# 研究启动是"建会话 + 起后台任务"，立即返回；状态查询是纯读库。
# 给 20s 足够宽松（本机毫秒级），又能让 Argus 假死时快速失败。
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
#: 健康探测用更短超时（探针不该拖慢调用方）
_PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)

#: 错误码 → 可读文案（前端把它作为工具失败原因回灌给模型）
#: 刻意不带任何本机绝对路径：开源用户的安装位置各不相同，提示只说明"该启动哪个服务、
#: 去哪儿改地址"，可执行信息由 MORAY_ARGUS_BASE_URL 承载。
_OFFLINE_HINT = (
    "本地研究服务未启动或不可达（{base}）。请先启动该服务（默认监听 127.0.0.1:8010）"
    "再重试；地址不同时改服务端 server/.env 的 MORAY_ARGUS_BASE_URL。"
)

# ---- [器官→记忆 线A] Argus 研究报告旁路写入（2026-10-04，纯加性） ----
# 写入点挂在 argus_report：报告正文（markdown/summary/citations）只在这里流经代理。
# Argus 不主动推送（main.py:40 的 3a 红线），服务端只能"被动看见"完成态。
# 去重：前端 ArgusWatcher 取报告失败会再试、多路轮询可能对同一 session_id 多次拉取，
# 进程内 _written_sids 保证每 sid 至多写一条记忆（页面关闭则报告不经代理 → 不写，
# 属已知边界：服务端无痕只发生在"从未被前台取回"的研究，见本轮回报未决问题）。
# content 首行"研究：{question}"所需的 question 不在 report 应答里，改由
# orchestrate/status（应答均带 question）顺手登记 _question_by_sid 取回。
_CACHE_MAX = 1000                 # 两表同上限（单机单用户远达不到，纯防无界增长）
_WRITTEN_SIDS: set = set()
_QUESTION_BY_SID: dict = {}       # session_id → question（截 200，供记忆 content 用）


def _remember_question(sid: str, question: str) -> None:
    """登记 sid→question。空值不登记；超上限整表清空重来（单用户场景代价可忽略）。"""
    sid = str(sid or "").strip()
    q = str(question or "").strip()
    if not sid or not q:
        return
    if len(_QUESTION_BY_SID) >= _CACHE_MAX:
        _QUESTION_BY_SID.clear()
    _QUESTION_BY_SID[sid] = q[:200]


def _first_report_for(sid: str) -> bool:
    """该 sid 的报告是否首次到达（True → 应写记忆；False → 已写过，跳过）。"""
    if sid in _WRITTEN_SIDS:
        return False
    if len(_WRITTEN_SIDS) >= _CACHE_MAX:
        _WRITTEN_SIDS.clear()
    _WRITTEN_SIDS.add(sid)
    return True


def _err(status_code: int, code: str, message: str):
    return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "message": message})


def _base_url() -> str:
    return (config.ARGUS_BASE_URL or "").rstrip("/")


async def _request(method: str, path: str, *, json_body: dict | None = None,
                   timeout: httpx.Timeout | None = None) -> tuple[dict | None, int | None, str, str]:
    """转发一次到 Argus。

    返回 ``(payload, status, transport_error, upstream_detail)``：

    * 成功 → ``(上游 JSON, 200, "", "")``；
    * 上游 4xx/5xx → ``(None, 状态码, "", 上游 detail)`` —— 上游**答了**，只是答的是错误；
    * 连不上/超时/后端没配地址 → ``(None, None, 原因, "")``。

    两个错误字段必须分开：调用方要能区分"Argus 没起来"（503 + 启动提示）
    与"Argus 说这个 session 不存在"（404 原样透传）。混在一起会把
    "查一个不存在的 id" 误报成"服务没启动"，模型据此让用户去开 Argus，是错的。
    """
    base = _base_url()
    if not base:
        return None, None, "后端未配置 ARGUS_BASE_URL", ""
    url = base + path
    try:
        async with httpx.AsyncClient(timeout=timeout or _TIMEOUT) as client:
            resp = await client.request(method, url, json=json_body)
    except httpx.TimeoutException:
        _logger.warning("argus proxy timeout: %s %s", method, path)
        return None, None, "请求 Argus 超时", ""
    except httpx.HTTPError as e:
        _logger.warning("argus proxy http error: %s (%s %s)", type(e).__name__, method, path)
        return None, None, "无法连接 Argus（%s）" % type(e).__name__, ""
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = str(body.get("detail") or body.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        _logger.info("argus upstream %s: %s %s", resp.status_code, method, path)
        return None, resp.status_code, "", detail
    try:
        return resp.json(), resp.status_code, "", ""
    except Exception:  # noqa: BLE001
        return None, resp.status_code, "", "Argus 返回了非 JSON 响应"


def _offline(base_hint: str = ""):
    """连不上 Argus 的统一响应（503 + 可执行的启动提示）"""
    return _err(503, "argus_offline", _OFFLINE_HINT.format(base=_base_url() or base_hint or "http://127.0.0.1:8010"))


@router.get("/api/argus/health")
async def argus_health():
    """Argus 可达性探针（前端/运维用；不做任何研究动作）"""
    base = _base_url()
    data, status, transport_error, _detail = await _request("GET", "/api/health", timeout=_PROBE_TIMEOUT)
    if transport_error:
        return {"ok": True, "data": {"up": False, "baseUrl": base, "reason": transport_error}}
    return {"ok": True, "data": {"up": True, "baseUrl": base, "upstream": data}}


@router.post("/api/argus/orchestrate")
async def argus_orchestrate(request: Request):
    """启动一次 Argus 深度研究，立即返回 ``session_id``（研究在 Argus 后台跑）。

    body: ``{question}`` —— 与 Argus ``POST /api/argus/orchestrate`` 同构。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    question = str(body.get("question") or "").strip()
    if not question:
        return _err(400, "bad_question", "缺少 question 字段（要研究的问题）")
    if len(question) > 2000:
        return _err(400, "bad_question", "question 过长（上限 2000 字符）")

    data, status, transport_error, detail = await _request(
        "POST", "/api/argus/orchestrate", json_body={"question": question})
    if transport_error:
        return _offline()
    if status and status >= 400:
        if status == 422:
            return _err(400, "bad_question", detail or "Argus 拒绝了该问题（question 需 1-2000 字符）")
        return _err(502, "argus_error", "Argus 返回 %s：%s" % (status, detail))
    session_id = str((data or {}).get("session_id") or "")
    if not session_id:
        return _err(502, "argus_bad_response", "Argus 未返回 session_id")
    _remember_question(session_id, question)          # [线A] report 无 question，先行登记
    _logger.info("argus orchestrate -> session=%s question=%s", session_id, question[:60])
    return {"ok": True, "data": {"session_id": session_id,
                                 "status": (data or {}).get("status") or "running",
                                 "question": question}}


def _safe_session_id(raw: str) -> str | None:
    """session_id 只允许字母数字与 ``-_``（Argus 生成的是十六进制串）。

    非空且合法才返回，否则 ``None`` —— 避免把奇怪的路径拼进上游 URL。
    """
    sid = str(raw or "").strip()
    if not sid or not all(ch.isalnum() or ch in "-_" for ch in sid):
        return None
    return sid


@router.get("/api/argus/status/{session_id}")
async def argus_status(session_id: str):
    """查询研究任务状态：``running`` / ``done`` / ``failed`` + 一句话进度（只读，不触发研究）"""
    sid = _safe_session_id(session_id)
    if sid is None:
        return _err(400, "bad_session_id", "缺少 session_id 或含非法字符")

    data, status, transport_error, detail = await _request("GET", "/api/argus/status/" + sid)
    if transport_error:
        return _offline()
    if status == 404:
        return _err(404, "argus_session_not_found", "Argus 上没有这个 session_id：%s" % sid)
    if status and status >= 400:
        return _err(502, "argus_error", "Argus 返回 %s：%s" % (status, detail))
    payload = data or {}
    payload.setdefault("session_id", sid)
    _remember_question(sid, payload.get("question") or "")   # [线A] 轮询顺带回填 question
    return {"ok": True, "data": {
        "session_id": payload.get("session_id") or sid,
        "status": payload.get("status") or "running",
        "progress": payload.get("progress") or "",
        "question": payload.get("question") or "",
        "event_count": payload.get("event_count") or 0,
        "report_ready": bool(payload.get("report_ready")),
    }}


@router.get("/api/argus/report/{session_id}")
async def argus_report(session_id: str):
    """取研究生成的报告（markdown + 引用）。

    Argus 侧对"还没生成"返回 ``ready:false`` 而不是 404，本路由原样透传 ——
    前端工具据此区分"还在跑"与"已完成但报告为空"。
    这是 ``detective_status`` 的补充：状态说 ``done`` 之后，模型还需要正文才能
    真正回答用户（否则只能报"研究完成"而说不出结论）。
    """
    sid = _safe_session_id(session_id)
    if sid is None:
        return _err(400, "bad_session_id", "缺少 session_id 或含非法字符")

    data, status, transport_error, detail = await _request("GET", "/api/sessions/" + sid + "/report")
    if transport_error:
        return _offline()
    if status == 404:
        return _err(404, "argus_session_not_found", "Argus 上没有这个 session_id：%s" % sid)
    if status and status >= 400:
        return _err(502, "argus_error", "Argus 返回 %s：%s" % (status, detail))
    payload = data or {}
    out = {"ok": True, "data": {
        "session_id": sid,
        "ready": bool(payload.get("ready")),
        "markdown": payload.get("markdown") or "",
        "summary": payload.get("summary") or "",
        "body": payload.get("body") or "",
        "citations": payload.get("citations") or [],
    }}
    # ---- [器官→记忆 线A] 研究完成（ready 且正文非空）→ 旁路写一条 task_result 记忆 ----
    # 失败/未完成（ready false 或 markdown 空）一律不写（对齐 Laya "成功才钩"）；
    # 同一 sid 多次拉取只写一次（_first_report_for）。任何异常绝不影响报告返回。
    try:
        markdown = out["data"]["markdown"]
        if out["data"]["ready"] and markdown.strip() and _first_report_for(sid):
            question = _QUESTION_BY_SID.get(sid) or ""
            citations = out["data"]["citations"]
            concl = (out["data"]["summary"] or markdown)[:500]
            content = "研究：%s\n结论：%s\n引用：%d条" % (
                question[:200], concl, len(citations))
            organ_memory.write_organ_memory(
                content, kind="task_result", tags=["argus_research"],
                source="argus_research", entity_id=sid)
            # ---- [Synapse v3-lite] 研究完成旁路发总线事件（api.py:433-441 同款形态）----
            # 判据与上方 organ_memory 写入完全同判据（ready 且正文非空且首次到达）；
            # 事件名对齐 zcode.task.succeeded 三段式；trace_id=sid——_first_report_for
            # 已保证每 sid 至多一次，总线 trace_id 判重是第二道保险；send 自吞异常。
            try:
                from . import synapse_bus
                synapse_bus.send(
                    "argus.research.succeeded", content,
                    meta={"trace_id": sid, "session_id": sid, "citations": len(citations)},
                    from_member="organ.argus")
            except Exception as e:  # noqa: BLE001 - 发事件失败绝不挡报告
                _logger.warning("[argus-synapse] 发总线事件异常：sid=%s err=%s: %s",
                                sid, type(e).__name__, str(e)[:200])
    except Exception as e:  # noqa: BLE001 - 旁路写入失败绝不挡住报告
        _logger.warning("[argus-memory] 写入钩子异常：sid=%s err=%s: %s",
                        sid, type(e).__name__, str(e)[:200])
    return out


# --------------------------------------------------------------------------
# 订阅（阶段3b）：/订阅 命令的后端出口。
# 调度器/投递器不走这两个路由（它们用 main.py 的直连客户端），这里只服务前端。
# --------------------------------------------------------------------------

#: 3b MVP 红线：单用户 user_id 写死；周期只支持 24h（后端定死，不信任前端传值——
#: 前端传错 user_id 会让投递器的过滤条件拉不到通知，闭环静默断裂）。
_SUBSCRIPTION_USER_ID = "local"
_SUBSCRIPTION_INTERVAL_HOURS = 24


@router.post("/api/argus/subscriptions")
async def argus_create_subscription(request: Request):
    """登记周期复查订阅。body 只取 ``question``，其余字段后端定死。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    question = str(body.get("question") or "").strip()
    if not question:
        return _err(400, "bad_question", "缺少 question 字段（要盯着复查的问题）")
    if len(question) > 2000:
        return _err(400, "bad_question", "question 过长（上限 2000 字符）")

    data, status, transport_error, detail = await _request(
        "POST", "/api/argus/subscriptions",
        json_body={"user_id": _SUBSCRIPTION_USER_ID,
                   "question": question,
                   "interval_hours": _SUBSCRIPTION_INTERVAL_HOURS})
    if transport_error:
        return _offline()
    if status and status >= 400:
        if status == 422:
            return _err(400, "bad_question", detail or "Argus 拒绝了该订阅")
        return _err(502, "argus_error", "Argus 返回 %s：%s" % (status, detail))
    _logger.warning("subscription created sub=%s question=%s", (data or {}).get("id"), question[:60])
    return {"ok": True, "data": data}


@router.get("/api/argus/subscriptions")
async def argus_list_subscriptions(due: bool = False):
    """订阅列表（``due=true`` 只列到期）。单用户场景不传 user_id=全量。

    ``_request`` 是裸拼 ``base + path``（不编码查询串），所以 ``?due=`` 直接进 path 安全。
    """
    path = "/api/argus/subscriptions?due=" + ("true" if due else "false")
    data, status, transport_error, detail = await _request("GET", path)
    if transport_error:
        return _offline()
    if status and status >= 400:
        return _err(502, "argus_error", "Argus 返回 %s：%s" % (status, detail))
    return {"ok": True, "data": data}
