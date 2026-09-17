"""配置：主机/端口/SQLite 路径/版本号/云端 LLM 代理配置（里程碑 M1 + M2）

- 主机固定 127.0.0.1（仅本机访问，本地优先）
- 端口默认 8000，可用环境变量 MORAY_PORT 覆盖
- SQLite 库位于 server/data/moray.sqlite3，目录不存在时自动创建
- M2 云端 LLM：优先读 server/.env（python-dotenv），其次环境变量；缺失不报错
"""
import os
from pathlib import Path

# server/ 目录（本文件位于 server/app/config.py）
SERVER_DIR = Path(__file__).resolve().parent.parent

HOST = "127.0.0.1"
PORT = int(os.environ.get("MORAY_PORT", "8000"))

DATA_DIR = SERVER_DIR / "data"
# [M5修复] 测试隔离：设置 MORAY_DB 环境变量可把 SQLite 指向临时文件（自测专用，严禁往用户主库灌 mock）
_env_db = os.environ.get("MORAY_DB", "").strip()
SQLITE_PATH = Path(_env_db).expanduser() if _env_db else (DATA_DIR / "moray.sqlite3")

SERVICE_NAME = "moray-backend"
VERSION = "1.0.0"      # 产品版本（对外，与前端 MORAY_VERSION 一致）
BUILD = "3.22.0"      # 内部构建号（对应 CHANGELOG 迭代序号，随发布更新）
# 对外产品版本权威字段（assemble 构建期校验 MORAY_VERSION 用；与内部 BUILD 数值不同属正常）
PRODUCT_VERSION = "1.0.0"

# ---- [阶段0 本机 Agent] 受控工作区根 ----
# 优先级：MORAY_WORKSPACE 环境变量 > kv 表 agent_workspace（设置页可改，运行时解析）
# 默认 D:\\MoRayWorkspace；目录不存在时由 agent_tools 自动创建
WORKSPACE_DEFAULT = os.environ.get("MORAY_WORKSPACE", r"D:\MoRayWorkspace").strip() or r"D:\MoRayWorkspace"

# ---- M2 云端 LLM 代理配置（.env 缺失时静默降级为空值，不报错）----
try:
    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / ".env")
except Exception:  # noqa: BLE001 - dotenv 缺失时仅用环境变量
    pass

LLM_BASE_URL = os.environ.get("MORAY_LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("MORAY_LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("MORAY_LLM_MODEL", "").strip()

# ---- [Kernel 阶段3] MCP（Model Context Protocol）兼容层 ----
# 把 11 个本机工具暴露为标准 MCP Server（SSE 传输），供 Claude Desktop / Cursor 等外部客户端调用。
# 实现见 server/app/mcp_server.py；零新增依赖（FastAPI StreamingResponse + asyncio.Queue）。
MCP_ENABLED = os.environ.get("MORAY_MCP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
MCP_SSE_PATH = "/api/mcp/sse"
MCP_SERVER_NAME = "moray-mcp"

# 只读工具：MCP 调用不需要审批，恒放行（不属于白名单管控范围）
MCP_READONLY_TOOLS = (
    "list_directory", "read_file", "find_files",
    "search_text", "list_processes", "system_info",
)

# 副作用工具（与 agent_tools.SIDE_EFFECT_TOOLS 一致）：默认【禁止】MCP 调用，
# 必须在 MCP_ALLOWED_TOOLS 里显式放行；放行后 MCP 调用自动 approved=true（仍受 _safe_path 等全部防护）。
MCP_SIDE_EFFECT_TOOLS = (
    "write_file", "create_file", "move_file", "run_command", "edit_file",
)
MCP_WRITE_TOOLS = MCP_SIDE_EFFECT_TOOLS  # 兼容别名

# MCP 白名单：默认只含只读工具（即"外部客户端只读"）。要授权写操作就把它加进来，例如：
#   set MORAY_MCP_ALLOWED_TOOLS=read_file,write_file,create_file
# 运行期还可在 kv 表写入 mcp_allowed_tools（JSON 数组或逗号串）覆盖本默认值 ——
# 优先级：kv(mcp_allowed_tools) > 本配置项（含环境变量）> 默认只读。
_env_mcp_allowed = os.environ.get("MORAY_MCP_ALLOWED_TOOLS", "").strip()
MCP_ALLOWED_TOOLS = (
    tuple(x.strip() for x in _env_mcp_allowed.split(",") if x.strip())
    if _env_mcp_allowed else MCP_READONLY_TOOLS
)
