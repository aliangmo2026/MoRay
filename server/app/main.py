"""FastAPI 应用（里程碑 M1-M5）

- M1: /api/health + CORS + 全局异常兜底
- M2: /api/llm/chat 云端代理
- M3: /api/conversations 等业务 CRUD
- M5: 静态托管前端（http://127.0.0.1:8000/ 直接打开应用；file:// 双击直开仍可用）

路径全部基于 __file__ 解析，中文/空格路径可用。
"""
import asyncio
import datetime
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import config, crud, db, memory_db, synapse_bus, synapse_db
from . import self_check  # [自省循环 2026-10-09 桥一] 30 分钟定时自检：只记录+标记，级别 1-2
from . import self_narrative  # [桥三 v1-stage1 2026-10-10] 自我叙事骨架：只做准备度检查，不调 LLM、不写库
from . import self_state  # [自我模型 v4 2026-10-23] 实时状态机：self_state_log 流水读写（bump_state/get_current_state）
from .llm_proxy import router as llm_router, startup_budget_note
from .api import router as api_router
from .agent_tools import router as agent_tools_router
from .argus_proxy import router as argus_router
from .mcp_server import router as mcp_router
from .crud import counts as db_counts, mark_stale_sessions_interrupted

# 工程根（server/ 的上一级；moray-workbench.html / vendor/ 所在处）
PROJECT_ROOT = config.SERVER_DIR.parent
FRONTEND_HTML = PROJECT_ROOT / "moray-workbench.html"
VENDOR_DIR = PROJECT_ROOT / "vendor"
SW_FILE = PROJECT_ROOT / "sw.js"
MANIFEST_FILE = PROJECT_ROOT / "manifest.webmanifest"

logger = logging.getLogger("moray")

# ============ [阶段3b] Argus 订阅调度器 + 投递器 ============
# 调度在 MoRay（3a 红线：Argus 不内置 cron、不主动推送）：
#   - 每 5 分钟问一次 Argus"哪些订阅到期"（GET ?due=true → 逐个 POST run）；
#   - 每 1 分钟拉一次未投递通知，写进 SQLite 的固定通知对话，成功后 ack。
# 直连 config.ARGUS_BASE_URL（8010），**不绕自己的 /api/argus 代理**——
# 代理路由的入站匹配在前端请求里，调度器走同源代理等于自己调自己。
# 日志级别说明：本仓自定义 logger 的 INFO 不上控制台（Day24.11 实测），
# 需要被看见的节点日志一律 warning。
_argus_client: httpx.AsyncClient | None = None
_SCHEDULER_INTERVAL = 300   # 秒：订阅到期检查
_DELIVER_INTERVAL = 60     # 秒：通知投递检查

#: 通知对话固定 id。messages.conversation_id 无外键约束（只有索引），
#: 不建 conversations 行也能插消息、不报错——但前端侧边栏从 conversations
#: 表列对话（BackendStore.pull 合并进 IndexedDB），不建行用户永远看不到通知。
#: 删除该对话是安全的：投递器发现行不在会重新建（自愈）。
NOTIF_CONVERSATION_ID = "argus-notifications"
NOTIF_CONVERSATION_TITLE = "🔔 Argus 订阅通知"

#: Argus 侧用户 id（单机单用户，3b 红线：写死）
_ARGUS_USER_ID = "local"


async def _scheduler_tick() -> None:
    """一轮调度：拉到期订阅 → 逐个触发（fire-and-forget，Argus 端点立即返回）。"""
    assert _argus_client is not None
    r = await _argus_client.get("/api/argus/subscriptions", params={"due": "true"})
    if r.status_code != 200:
        logger.warning("[subscription] scheduler: list due failed HTTP %s", r.status_code)
        return
    subs = r.json().get("subscriptions", [])
    for sub in subs:
        try:
            rr = await _argus_client.post(f"/api/argus/subscriptions/{sub['id']}/run")
            logger.warning("[subscription] scheduler: triggered %s (HTTP %s)", sub["id"], rr.status_code)
        except Exception as e:  # noqa: BLE001
            logger.warning("[subscription] scheduler: trigger %s failed: %s", sub["id"], e)


async def _scheduler_loop() -> None:
    while True:
        try:
            await _scheduler_tick()
        except httpx.ConnectError as e:
            # [降噪 2026-10-11] Argus 冷启动 / 暂不可达——良性，降 warning，避免淹真 ERROR
            logger.warning("[subscription] scheduler tick skipped: Argus unreachable (%s)", str(e)[:80])
        except Exception:  # noqa: BLE001
            logger.exception("[subscription] scheduler tick failed")
        # sleep 必须在 finally 之外：CancelledError 不是 Exception，若放 finally，
        # 取消发生在 tick 里时 sleep 仍会睡满整周期（TestClient/优雅关停实测挂 300s）。
        await asyncio.sleep(_SCHEDULER_INTERVAL)


def _ensure_notification_conversation() -> None:
    """通知对话不存在才创建（已存在不覆盖——用户可能改过标题；
    upsert_conversation 是全字段覆盖语义，绝不能用它做'INSERT OR IGNORE'）。"""
    if crud.get_conversation(NOTIF_CONVERSATION_ID):
        return
    crud.upsert_conversation({
        "id": NOTIF_CONVERSATION_ID,
        "title": NOTIF_CONVERSATION_TITLE,
        "model": "",
        "system_prompt": "",
        "pinned": 1,
    })
    logger.warning("[subscription] deliver: notification conversation created")


def _notification_to_message(n: dict) -> dict:
    """Argus 通知 → MoRay message 行。

    坑：crud.upsert_message 读的是 camelCase 键（conversationId/createdAt），
    传 conversation_id 会静默写成空串——消息落库但侧边栏永远看不到。
    created_at 不传，upsert_message 内部按后端统一的本地时间格式补。
    """
    return {
        "id": uuid.uuid4().hex[:32],
        "conversationId": NOTIF_CONVERSATION_ID,
        "role": "assistant",
        "content": "🔔 %s\n\n%s" % (n.get("title") or "订阅异动", n.get("body") or ""),
        "reasoning": "",
        "model": "argus-notifier",
    }


async def _deliver_tick() -> None:
    """一轮投递：拉未投递通知 → 写消息 → ack。
    at-least-once：先落库后 ack，中途失败不 ack，下一轮重投（重复可接受）。"""
    assert _argus_client is not None
    r = await _argus_client.get("/api/argus/notifications",
                                params={"user_id": _ARGUS_USER_ID, "limit": 20})
    if r.status_code != 200:
        logger.warning("[subscription] deliver: list notifications failed HTTP %s", r.status_code)
        return
    notifications = r.json().get("notifications", [])
    if not notifications:
        return
    _ensure_notification_conversation()
    for n in notifications:
        try:
            crud.upsert_message(_notification_to_message(n))
            await _argus_client.post(f"/api/argus/notifications/{n['id']}/ack")
            logger.warning("[subscription] deliver: delivered %s（%s）",
                           n["id"], (n.get("title") or "")[:40])
        except Exception as e:  # noqa: BLE001
            # 不 ack → 下次 tick 重试
            logger.warning("[subscription] deliver: %s failed: %s", n.get("id"), e)


async def _deliver_loop() -> None:
    while True:
        try:
            await _deliver_tick()
        except httpx.ConnectError as e:
            # [降噪 2026-10-11] Argus 冷启动 / 暂不可达——良性，降 warning，避免淹真 ERROR
            logger.warning("[subscription] deliver tick skipped: Argus unreachable (%s)", str(e)[:80])
        except Exception:  # noqa: BLE001
            logger.exception("[subscription] deliver tick failed")
        await asyncio.sleep(_DELIVER_INTERVAL)  # 同上：不可放 finally，见 _scheduler_loop 注释


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _argus_client
    db.init_db()
    memory_db.init_memory_db()  # [2026-10-02 M1] MemoryBridge 独立记忆库初始化（幂等，库文件独立于主库）
    # [2026-10-06 Synapse v1] 神经总线独立事件库初始化（幂等，memory_db 同款独立库惯例）。
    # 总闸 SYNAPSE_ENABLED（默认关）关时只建表不起 consumer，send() 亦是 no-op（双保险）。
    synapse_db.init_synapse_db()
    # [国庆冲刺 Day3] 断点恢复（3.1）：上个进程留下的 pending/running 会话一律标 interrupted
    # （与 agent_orchestrator 对计划的重启标记同一惯例）。只做状态查询，不自动续跑。
    n = mark_stale_sessions_interrupted()
    if n:
        logging.getLogger("moray").info("[session] startup: marked %d stale session(s) interrupted", n)
    # [Day24.11] 熔断计数状态报告：计数是纯内存 dict，新进程里残留恒为 0（如实报告，不伪造数字）
    startup_budget_note()
    # [阶段3b] 订阅调度 + 通知投递：两个 loop 共用一个直连 Argus 的客户端。
    # Argus 没起时 loop 只是每轮 warning，不炸启动、不影响其余功能。
    _argus_client = httpx.AsyncClient(base_url=config.ARGUS_BASE_URL.rstrip("/"), timeout=10.0)
    scheduler_task = asyncio.create_task(_scheduler_loop())
    deliver_task = asyncio.create_task(_deliver_loop())
    _app.state.scheduler_task = scheduler_task
    _app.state.deliver_task = deliver_task
    # [自省循环 2026-10-09 桥一] ERROR 环形缓冲挂到 moray logger（ERROR+ 才进环）；
    # [桥一 v1.6] 统一走 install_error_handlers()：环形缓冲 + moray_errors.log
    # FileHandler（append 落盘，补重启盲区）一并挂上，幂等。自检 loop 起手跑一轮
    # 后排 30 分钟周期。task 引用存 _app.state：asyncio 对 task 仅持弱引用，
    # 不留引用可能半路被 GC（scheduler/deliver 同款纪律）。
    self_check.install_error_handlers()
    self_check_task = asyncio.create_task(self_check.self_check_loop())
    _app.state.self_check_task = self_check_task
    # [2026-10-06 Synapse v1] 总线消费循环：总闸 SYNAPSE_ENABLED（默认关）开着才起
    # （MONITOR_ENABLED 同款门控惯例）。事件先落 synapse.sqlite3 再叫醒（Argus event_hub
    # 哲学：库是事实来源，队列只叫醒）；规则分发见 synapse_bus._RULES。
    if config.SYNAPSE_ENABLED:
        _app.state.synapse_task = synapse_bus.start_loop()
    logger.warning("[subscription] scheduler(%ss) + deliver(%ss) loops started, argus=%s",
                   _SCHEDULER_INTERVAL, _DELIVER_INTERVAL, config.ARGUS_BASE_URL)
    # [L5.2 2026-09-28] 主动日志监控（server/app/log_monitor.py）：
    # 红线 7——config.MONITOR_ENABLED 默认 false = 本钩子完全不执行；
    # 只有 .env 显式 true 才开机自启。运行期开/关走 POST /api/monitor/enable|disable，
    # 重启回关（诚实：不假装恢复用户手动开启的状态）。
    if config.MONITOR_ENABLED:
        from .log_monitor import manager as _monitor_manager  # noqa: E402
        await _monitor_manager.enable()
        _app.state.monitor_task = _monitor_manager._task
        logger.warning("[monitor] lifespan autostart: MONITOR_ENABLED=true")
    yield
    # [L5.2] 监控循环收尾：未启用时 disable 是安全 no-op（不养僵尸任务、不让 teardown 挂起）
    from .log_monitor import manager as _monitor_manager  # noqa: E402
    await _monitor_manager.disable()
    for t in (scheduler_task, deliver_task):
        t.cancel()
    for t in (scheduler_task, deliver_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    # [Synapse v1] 总线收尸：cancel consumer + 未决 handler（未启用时是安全 no-op）
    await synapse_bus.stop()
    await _argus_client.aclose()
    _argus_client = None


app = FastAPI(title="MoRay 本地薄后端", version=config.VERSION, lifespan=lifespan)

# M2 云端 LLM 代理路由 + M3 会话/消息/设置 CRUD 路由 + [阶段0] 本机 Agent 工具路由
app.include_router(llm_router)
app.include_router(api_router)
app.include_router(agent_tools_router)
# [Argus 深度研究] 同源代理：前端工具 → /api/argus/* → 本机 Argus（默认 8010）。
# 独立路径前缀，与既有 /api/* 不冲突；放在 mcp_router 之前保持既有匹配顺序不变。
app.include_router(argus_router)
# [Kernel 阶段3] MCP 兼容层：GET/POST /api/mcp/sse（SSE 长连接 + JSON-RPC 2.0）
# 独立模块自带 APIRouter（server/app/mcp_server.py）；路径 /api/mcp/* 与既有路由不冲突，
# 且本行放在最后 → 既有 /api/agent/* 的匹配顺序与行为完全不变。
app.include_router(mcp_router)
from .engines.local_adapter import LocalAdapter, router as engines_router  # noqa: F401,E402 [国庆冲刺 Day7] 通用执行引擎（LocalAdapter 兜底）
app.include_router(engines_router)  # [Day7] GET /api/engines/list 只读路由；独立路径，放最后不影响既有匹配顺序
app.include_router(self_check.router, prefix="/api")  # [自省循环 2026-10-09] GET /api/self_check/report 只读端点；独立路径，既有匹配顺序不变
app.include_router(self_narrative.router, prefix="/api")  # [桥三 v1-stage1 2026-10-10] GET /api/self_narrative/readiness 只读端点；追加在 self_check.router 之后，独立路径，既有匹配顺序不变
from .digital_twin import router as digital_twin_router  # noqa: E402 [数字分身 v1 2026-10-23]
app.include_router(digital_twin_router)  # [数字分身 v1 2026-10-23] POST /api/twin/ingest + /api/twin/recall；独立路径，既有匹配顺序不变

# 关键坑：前端可能以 file:// 打开（Origin 为字符串 "null"），也可能来自
# http://127.0.0.1 或 http://localhost 的任意端口 —— 正则统一放行
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(null|file://.*|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):  # noqa: BLE001
    """全局兜底：任何未捕获异常 → JSON，绝不返回 HTML 堆栈"""
    return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/")
def root():
    """[M5] 根路径直接返回前端应用（一个地址即用；file:// 双击直开仍可用）"""
    if FRONTEND_HTML.exists():
        return FileResponse(FRONTEND_HTML, media_type="text/html")
    return {
        "service": config.SERVICE_NAME,
        "version": config.VERSION,
        "message": "MoRay 本地薄后端。健康检查：GET /api/health（未找到 moray-workbench.html，请把前端文件放在后端上级目录）",
    }


@app.get("/api/status")
def status():
    """服务说明 JSON（原根路径说明迁移到此）"""
    return {
        "service": config.SERVICE_NAME,
        "version": config.VERSION,
        "message": "MoRay 本地薄后端。健康检查：GET /api/health；应用：GET /",
    }


# [自我模型 v4 2026-10-23] 实时状态机测试端点（独立路径，不影响既有匹配顺序）

@app.get("/api/self_state")
def self_state_current():
    """当前状态（self_state_log 最新一行；空表回基线默认值 cf=0/mental=100）"""
    return {"ok": True, "data": self_state.get_current_state()}


@app.post("/api/self_state/bump")
async def self_state_bump(request: Request):
    """手动记一次成功/失败（测试用）。body: {"engine": "zcode", "success": true, "note": "..."}"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_json",
                                                      "message": "请求体不是合法 JSON。"})
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_request",
                                                      "message": "须为 JSON 对象且 success 为布尔值。"})
    new_state = self_state.bump_state(
        str(payload.get("engine") or "manual"), payload["success"],
        str(payload.get("note") or ""))
    return {"ok": True, "data": new_state}


@app.get("/sw.js")
def service_worker():
    if SW_FILE.exists():
        return FileResponse(SW_FILE, media_type="application/javascript",
                            headers={"Service-Worker-Allowed": "/"})
    return JSONResponse(status_code=404, content={"ok": False, "code": "not_found", "message": "sw.js 缺失"})


@app.get("/manifest.webmanifest")
def webmanifest():
    if MANIFEST_FILE.exists():
        return FileResponse(MANIFEST_FILE, media_type="application/manifest+json")
    return JSONResponse(status_code=404, content={"ok": False, "code": "not_found", "message": "manifest 缺失"})


# [M5] vendor/ 静态目录（lucide/marked/highlight/purify/tailwind）；挂载在 API 路由之后、路径独立不冲突
from fastapi.staticfiles import StaticFiles  # noqa: E402

if VENDOR_DIR.exists():
    app.mount("/vendor", StaticFiles(directory=str(VENDOR_DIR)), name="vendor")

# [图片壁纸] wallpapers/ 静态目录（内置动漫壁纸，file:// 与 http 均可用相对路径）
WALLPAPERS_DIR = PROJECT_ROOT / "wallpapers"
if WALLPAPERS_DIR.exists():
    app.mount("/wallpapers", StaticFiles(directory=str(WALLPAPERS_DIR)), name="wallpapers")


@app.get("/api/health")
def health():
    db_ok, db_detail = db.ping()
    # M3：会话/消息计数
    try:
        c = db_counts()
    except Exception:  # noqa: BLE001
        c = {"conversations": 0, "messages": 0}
    return {
        "ok": True,
        "service": config.SERVICE_NAME,
        "version": config.VERSION,
        "build": config.BUILD,
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db": "ok" if db_ok else db_detail,
        # M2：云端 LLM 是否已配置（key 在 server/.env，不返回任何密钥信息）
        "cloud_configured": bool(config.LLM_BASE_URL and config.LLM_API_KEY),
        # M3：数据计数
        "counts": c,
    }
