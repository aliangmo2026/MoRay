"""FastAPI 应用（里程碑 M1-M5）

- M1: /api/health + CORS + 全局异常兜底
- M2: /api/llm/chat 云端代理
- M3: /api/conversations 等业务 CRUD
- M5: 静态托管前端（http://127.0.0.1:8000/ 直接打开应用；file:// 双击直开仍可用）

路径全部基于 __file__ 解析，中文/空格路径可用。
"""
import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import config, db
from .llm_proxy import router as llm_router
from .api import router as api_router
from .agent_tools import router as agent_tools_router
from .mcp_server import router as mcp_router
from .crud import counts as db_counts

# 工程根（server/ 的上一级；moray-workbench.html / vendor/ 所在处）
PROJECT_ROOT = config.SERVER_DIR.parent
FRONTEND_HTML = PROJECT_ROOT / "moray-workbench.html"
VENDOR_DIR = PROJECT_ROOT / "vendor"
SW_FILE = PROJECT_ROOT / "sw.js"
MANIFEST_FILE = PROJECT_ROOT / "manifest.webmanifest"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="MoRay 本地薄后端", version=config.VERSION, lifespan=lifespan)

# M2 云端 LLM 代理路由 + M3 会话/消息/设置 CRUD 路由 + [阶段0] 本机 Agent 工具路由
app.include_router(llm_router)
app.include_router(api_router)
app.include_router(agent_tools_router)
# [Kernel 阶段3] MCP 兼容层：GET/POST /api/mcp/sse（SSE 长连接 + JSON-RPC 2.0）
# 独立模块自带 APIRouter（server/app/mcp_server.py）；路径 /api/mcp/* 与既有路由不冲突，
# 且本行放在最后 → 既有 /api/agent/* 的匹配顺序与行为完全不变。
app.include_router(mcp_router)

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
