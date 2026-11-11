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
BUILD = "3.25.0"      # 内部构建号（对应 CHANGELOG 迭代序号，随发布更新）
# 对外产品版本权威字段（assemble 构建期校验 MORAY_VERSION 用；与内部 BUILD 数值不同属正常）
PRODUCT_VERSION = "1.0.0"

# ---- 本机 Agent 受控工作区根 ----
# 优先级：MORAY_WORKSPACE 环境变量 > kv 表 agent_workspace（设置页可改，运行时解析）> 本默认值。
# 默认目录的选取原则：**已存在就沿用，不存在就用跨平台的安全位置**——
# 既不让老用户的数据"搬家"（Windows 上历史上常用 D:\MoRayWorkspace，已存在则继续用），
# 也不把 Windows 盘符路径硬塞给陌生机器（没有 D 盘的机器会建目录失败）。
def _default_workspace() -> str:
    legacy = "D:\\MoRayWorkspace"
    if os.path.isdir(legacy):
        return legacy
    return str(Path.home() / "MoRayWorkspace")


WORKSPACE_DEFAULT = os.environ.get("MORAY_WORKSPACE", "").strip() or _default_workspace()

# ---- M2 云端 LLM 代理配置（.env 缺失时静默降级为空值，不报错）----
try:
    from dotenv import load_dotenv
    load_dotenv(SERVER_DIR / ".env")
except Exception:  # noqa: BLE001 - dotenv 缺失时仅用环境变量
    pass

LLM_BASE_URL = os.environ.get("MORAY_LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("MORAY_LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("MORAY_LLM_MODEL", "").strip()

# 上游网关**真正接受**的模型名白名单（逗号分隔）。
# 前端是在本机 Ollama 的模型列表里挑默认模型的（qwen2.5:7b 等），这些名字打到云端网关会被 400；
# llm_proxy 对不在本名单里的名字做兜底改写 → MORAY_LLM_MODEL，保证主对话始终可用。
# 换服务商时只改这一项（server/.env 的 MORAY_LLM_MODELS），不用动代码：
#   MORAY_LLM_MODELS=deepseek-flash,deepseek-v4-pro
# 留空 = 保持内置默认（兼容 deepseek-chat / deepseek-reasoner 两个实测可用的别名）。
DEFAULT_LLM_MODELS = ("deepseek-flash", "deepseek-v4-pro",
                      "deepseek-chat", "deepseek-reasoner")
_env_llm_models = os.environ.get("MORAY_LLM_MODELS", "").strip()
LLM_MODELS = tuple(x.strip() for x in _env_llm_models.split(",") if x.strip()) or DEFAULT_LLM_MODELS

# ---- 单 session LLM 调用次数熔断 ----
# 由 llm_proxy 在内存里按 session_id 计数（不落库、不跨重启，重启即清零）；
# 超过本上限后 /api/llm/chat 直接 429。可用环境变量 / server/.env 覆盖默认 30。
# [2026-10-30 解除死限制] 设 0（或负数）= 不限次数：多智能体（Argus/Iris 预设）单轮
# 常超 30 次工具调用，硬熔断会锁死整会话且切模型也无法恢复。llm_proxy 侧以
# `limit > 0 and count > limit` 判定，故 0 安全地表示"不限"。
MAX_LLM_CALLS_PER_SESSION: int = 30
try:
    MAX_LLM_CALLS_PER_SESSION = int(os.environ.get("MAX_LLM_CALLS_PER_SESSION", "30"))
except ValueError:  # .env 写了非数字时回落默认值，绝不让导入 config 炸掉整个后端
    MAX_LLM_CALLS_PER_SESSION = 30

# ---- Argus 深度研究服务地址（前端工具 detective_research 的转发目标）----
# 独立的本地研究服务，默认监听 8010；改用别的端口/主机时写 server/.env 的
# MORAY_ARGUS_BASE_URL 即可，前端一行都不用动（见 argus_proxy.py）。
# 与 LLM_BASE_URL 不同：这一项**有默认值**（本机约定端口），缺失时按这个默认探测比
# "未配置"更有用；探不到由代理返回 argus_offline。
ARGUS_BASE_URL = os.environ.get("MORAY_ARGUS_BASE_URL", "http://127.0.0.1:8010").strip()

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

# ---- [p_mcp 迭代·任务1] 外部 MCP 服务声明式注册表（本块为文件尾纯追加，不改上面任何既有项）----
# 说明：server/app/mcp_server.py 是 MoRay **自身作为 MCP 服务端**（供外部客户端调用 MoRay）；
# Argus 通道走上面的单标量 ARGUS_BASE_URL；本块是"MoRay 消费外部 MCP 服务"的注册表。
# [MCP 网关第2晚 2026-11-06] 统一消费方 = server/app/mcp_gateway.py（通用网关）；
# adapters/iris_mcp_client.py 已退化为指向网关的兼容垫片（deprecated），不再直连器官。
# 追加新外部服务 = 只在 MCP_SERVERS 里加键，零核心改动（设计文档 docs/p_mcp_audit.md §3.3）。
MCP_SERVERS: dict = {
    "iris": {
        "transport": "sse",
        "url": os.environ.get("MORAY_IRIS_MCP_URL", "http://127.0.0.1:9301/sse"),
        "api_key": "${IRIS_MCP_API_KEY}",
        "timeout": float(os.environ.get("MORAY_IRIS_MCP_TIMEOUT", "30")),
        "enabled": os.environ.get("MORAY_IRIS_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    },
    # ---- [MCP 网关试点 2026-11-05] Argus 深度研究器官（streamable-HTTP 直连 /mcp/）----
    # 消费方 = server/app/mcp_gateway.py（泛化自 adapters/iris_mcp_client.py，零新依赖）。
    # api_key 占位由网关消费端展开；ARGUS_API_KEY 写 server/.env。鉴权翻牌前（生产 8010
    # 尚未设 Key），带 Key 请求按服务端"未设 Key 降级仅 loopback"语义放行（与 Iris 同约定）。
    "argus": {
        "transport": "streamable-http",
        "url": os.environ.get("MORAY_ARGUS_MCP_URL", "http://127.0.0.1:8010/mcp/"),
        "api_key": "${ARGUS_API_KEY}",
        "timeout": float(os.environ.get("MORAY_ARGUS_MCP_TIMEOUT", "30")),
        "enabled": os.environ.get("MORAY_ARGUS_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    },
    # ---- [MCP 网关第3晚 2026-11-06] Scribe / Surgeon / AIOps HTTP 触手 ----
    # 三个器官已从 stdio 改造为 loopback streamable-HTTP（stateless_http + json_response：
    # 无 initialize/Mcp-Session-Id 握手，POST 直接回 application/json，网关零解析改动）。
    #   Scribe  E:\moray-scribe  :9310 /mcp  ← CWD 必须是项目根（out/ 相对 CWD）
    #   Surgeon E:\moray-surgeon :9311 /mcp
    #   AIOps   E:\moray-aio     :9312 /mcp  ← FastAPI 包手写 JSON-RPC 分发器
    # 均无鉴权头（仅绑 127.0.0.1）；api_key 留空，不引入新凭据字面量。
    "scribe": {
        "transport": "streamable-http",
        "url": os.environ.get("MORAY_SCRIBE_MCP_URL", "http://127.0.0.1:9310/mcp"),
        "api_key": "",
        "timeout": float(os.environ.get("MORAY_SCRIBE_MCP_TIMEOUT", "120")),
        "enabled": os.environ.get("MORAY_SCRIBE_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    },
    "surgeon": {
        "transport": "streamable-http",
        "url": os.environ.get("MORAY_SURGEON_MCP_URL", "http://127.0.0.1:9311/mcp"),
        "api_key": "",
        "timeout": float(os.environ.get("MORAY_SURGEON_MCP_TIMEOUT", "120")),
        "enabled": os.environ.get("MORAY_SURGEON_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    },
    "aiops": {
        "transport": "streamable-http",
        "url": os.environ.get("MORAY_AIOPS_MCP_URL", "http://127.0.0.1:9312/mcp"),
        "api_key": "",
        "timeout": float(os.environ.get("MORAY_AIOPS_MCP_TIMEOUT", "60")),
        "enabled": os.environ.get("MORAY_AIOPS_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    },
}

# ---- [MCP 网关试点 2026-11-05；第2晚 Iris；第3晚 Scribe/Surgeon/AIOps；第4晚默认开启] ----
# 消费方 server/app/mcp_gateway.py。防循环三道闸（任务书红线：器官回调的请求绝不注入）：
#   1) X-Moray-Source: organ 请求头硬豁免（llm_proxy 顶层 + prepare_chat_tools 双层）；
#   2) [第4晚] 默认开启注入：MORAY_MCP_CHAT_INJECT 默认 1，普通聊天无需点名即获得
#      白名单内全部只读工具；MORAY_MCP_CHAT_INJECT=0 可一键退回 opt-in；
#   3) 一次性请求内 MCP 工具执行硬上限 MCP_TOOL_CALLS_MAX（15 次）→ 熔断见 mcp_gateway。
# Iris 等器官回调继续由 1) 豁免；内部回环调用（task_runner/task_planner/bot）已同步打标。
MCP_CHAT_INJECT_DEFAULT: bool = os.environ.get("MORAY_MCP_CHAT_INJECT", "1").strip().lower() \
    in ("1", "true", "yes", "on")

# [第4晚 2026-11-06] 写操作工具硬红线：这两个会改代码并 git commit，**永不进入默认聊天
# 注入白名单**（即使未来有人误把它们写进 MCP_CHAT_INJECT_SERVERS 也会被 prepare_chat_tools
# 过滤掉）。仅允许两条显式通道：① POST /api/mcp/gateway/call（显式 API）；② 运维同时
# 打开 MORAY_MCP_ALLOW_WRITE_INJECT=1 且请求头 X-Moray-Tools-Mode: advanced（高级预设）。
MCP_INJECT_FORBIDDEN_TOOLS: tuple = ("execute_plan", "refactor_smart")
MCP_ALLOW_WRITE_INJECT: bool = os.environ.get("MORAY_MCP_ALLOW_WRITE_INJECT", "0").strip().lower() \
    in ("1", "true", "yes", "on")

# 可注入的外部工具白名单（server 名 → 命名空间前缀 + 只读工具集）。
# Argus 四件套为任务书钉死的只读工具；detective_research（会再烧 8000 的异步研究发起器）
# 刻意不注入，只留 status/report 查询面。
MCP_CHAT_INJECT_SERVERS: dict = {
    "argus": {
        "prefix": "mcp__argus__",
        "tools": ("search_web", "fetch_url", "detective_status", "detective_report"),
    },
    # ---- [MCP 网关第2晚 2026-11-06] Iris 视觉/OCR 器官（9301 SSE）----
    # 工具名以 Iris 服务端实际 tools/list 为准（D:\Iris\server\mcp_server.py:51-111）：
    #   iris_analyze_image  报错截图一键分析（OCR → 特征 → 诊断）
    #   iris_ocr_text       纯 OCR
    #   iris_analyze_text   纯文本报错诊断（日志/Traceback）
    # iris_read_pdf 刻意不注入：它按任意本机路径读 PDF，属"文件读取面"而非视觉诊断面，
    # 与 Argus 的 detective_research 同一取舍（只注入只读、窄语义工具）。
    "iris": {
        "prefix": "mcp__iris__",
        "tools": ("iris_analyze_image", "iris_ocr_text", "iris_analyze_text"),
    },
    # ---- [MCP 网关第3晚 2026-11-06] Scribe / Surgeon / AIOps ----
    # 注入纪律同前：只暴露只读/窄语义工具，写盘、改代码、提交、跑 LLM 的一律不进聊天工具面。
    # 三个器官的 tools/list 仍分别返回 6/3/12（验收口径），这里只是可注入子集。
    "scribe": {
        "prefix": "mcp__scribe__",
        # 5 个只读/基础工具：health / scan / graph / read_artifact / list_symbols。
        # scribe_doc 会调 Ollama 生成 ARCHITECTURE.md 并写盘 —— 不注入。
        "tools": ("scribe_health", "scribe_scan", "scribe_graph",
                  "scribe_read_artifact", "scribe_list_symbols"),
    },
    "surgeon": {
        "prefix": "mcp__surgeon__",
        # 只暴露动作目录。execute_plan / refactor_smart 会改代码并 git commit —— 绝不注入。
        "tools": ("list_available_actions",),
    },
    "aiops": {
        "prefix": "mcp__aiops__",
        # 8 个基础只读探针。execute_safe_python（沙箱执行代码）/ k8s_probe / cluster_status
        # / log_rolling_tail 属于偏重或需要集群凭据的路径，暂不进聊天工具面。
        "tools": ("port_check", "process_list", "disk_usage", "network_connections",
                  "windows_service_status", "log_tail", "log_grep", "list_files"),
    },
}

# 工具超时分级（秒）——[第4晚 2026-11-06 按任务书重排]
#   默认 30s；fetch_url / iris_ocr_text 90s；
#   detective_research / iris_analyze_image 240s；
#   Surgeon execute_plan 300s、Scribe scan/graph 300s、Scribe doc 600s、refactor_smart 600s。
# 说明：超时只对"网关直调/显式 API/高级预设"有意义；Surgeon 写工具与 scribe_doc 不进
# 默认聊天注入面（见 MCP_INJECT_FORBIDDEN_TOOLS 与白名单注释）。
MCP_TOOL_TIMEOUT_DEFAULT: float = 30.0
MCP_TOOL_TIMEOUTS: dict = {
    "search_web": 60.0,
    "fetch_url": 90.0,
    "detective_research": 240.0,
    "detective_status": 120.0,
    "detective_report": 120.0,
    "iris_analyze_image": 240.0,
    "iris_ocr_text": 90.0,
    "iris_analyze_text": 240.0,
    "iris_read_pdf": 60.0,
    # [第3晚 2026-11-06] Scribe：scan/graph 走 libcst 扫全仓；第4晚按任务书提到 300s。
    # doc 会跑 Ollama 生成文档，600s 长窗（不注入聊天面，仅网关直调时有意义）。
    "scribe_health": 30.0,
    "scribe_scan": 300.0,
    "scribe_graph": 300.0,
    "scribe_doc": 600.0,
    "scribe_read_artifact": 30.0,
    "scribe_list_symbols": 30.0,
    # Surgeon：动作目录即时返回；execute_plan 含改码+验证+commit，refactor_smart 含
    # 最多 3 轮 LLM 规划，两者都不注入聊天面，仅给网关直调留长窗。
    "list_available_actions": 30.0,
    "execute_plan": 300.0,
    "refactor_smart": 600.0,
    # AIOps：本地探针/读日志默认 30s 足够；日志 grep 与 rolling tail 给 60s。
    "port_check": 30.0,
    "process_list": 30.0,
    "disk_usage": 30.0,
    "network_connections": 30.0,
    "windows_service_status": 30.0,
    "log_tail": 60.0,
    "log_grep": 60.0,
    "list_files": 30.0,
    "log_rolling_tail": 60.0,
    "k8s_probe": 120.0,
    "cluster_status": 120.0,
    "execute_safe_python": 60.0,
}

# twin RAG 强命中时要"物理剥夺"的外部搜索工具名（llm_proxy 消费）。
# [第2晚配置化] 原为 llm_proxy 内硬编码 "detective_research"；行为等价，改成配置项方便
# 后续把 Argus 之外的外部检索工具一并纳入，无需再改代理主链路。
MCP_TWIN_DEPRIVED_TOOLS: tuple = ("detective_research",)

# [第4晚 2026-11-06] 防死循环终极闸：一次对话请求内 MCP 工具执行次数硬上限。
# 语义：第 15 次照常执行；第 16 次之前立即熔断（不再发起模型轮/不再执行工具），
# llm_proxy 直接返回 MCP_FUSE_MESSAGE。与夜班模式 15 次预算同量级，便于对齐。
MCP_TOOL_CALLS_MAX: int = max(1, int(os.environ.get("MORAY_MCP_TOOL_CALLS_MAX", "15")))
MCP_FUSE_MESSAGE: str = "系统安全限制：工具调用次数超限，已强制中断。"

# tools/list 发现结果缓存 TTL（秒）+ 服务端工具环轮数上限 + 回喂给模型的结果截断长度
# MCP_TOOL_ROUND_MAX_TOKENS = 工具环内每轮生成封顶（防指令冲突退化循环吃满 300s read 超时）
# [第4晚] 轮数上限从 3 提到 20：真正的刹车改为 MCP_TOOL_CALLS_MAX=15 的按调用计数；
# 轮数只是兜底（模型每轮 1 次调用时也能走到熔断，而不是被 3 轮提前截断）。
MCP_TOOLS_CACHE_TTL: float = 300.0
MCP_TOOL_ROUNDS: int = max(1, int(os.environ.get("MORAY_MCP_TOOL_ROUNDS", "20")))
MCP_TOOL_RESULT_MAX_CHARS: int = 6000
MCP_TOOL_ROUND_MAX_TOKENS: int = 1500

# ---- [P0 工具环总时限 2026-11-07] ----
# [2026-11-07 实锤] 普通聊天被慢失败器官（Argus 8010 CLOSE_WAIT 形态）拖到 3 分钟：
# 单工具虽有 MCP_TOOL_TIMEOUTS 分级超时，但工具环整体（模型轮 × N + 工具执行 × N）
# 无总时限，慢链路可无限叠加。MCP_CHAT_TOTAL_TIMEOUT = 整个工具环的挂钟硬上限：
# 模型轮与工具执行都包进 wait_for(剩余预算)，到点即熔断——已完成的工具结果保留回灌，
# 注入系统提示让模型基于现有信息直接作答（消费方 mcp_gateway.run_server_tool_rounds）。
# 权衡：search_web(60s)+fetch_url(90s) 的合法深研链最坏 150s，120s 默认会截断其尾部
# ——这是设计使然（用户拍板"绝对不允许一个聊天请求在后台挂死"），深研场景可经
# server/.env 的 MORAY_MCP_CHAT_TOTAL_TIMEOUT 调大，不动代码。
MCP_CHAT_TOTAL_TIMEOUT: float = 120.0
try:
    MCP_CHAT_TOTAL_TIMEOUT = float(os.environ.get("MORAY_MCP_CHAT_TOTAL_TIMEOUT", "120"))
except ValueError:
    MCP_CHAT_TOTAL_TIMEOUT = 120.0
#: 超时后注入给模型的系统提示（任务书原文措辞），消费方 mcp_gateway
MCP_TOTAL_TIMEOUT_NOTE: str = (
    "由于部分工具执行超时，请基于现有信息回答。不要提及内部实现细节，"
    "也不要声称会继续调用工具。")

# MCP_SERVERS 各服务 api_key 占位展开为空时的 env 兜底名（mcp_gateway 消费端读取）
MCP_ENV_KEY_FALLBACK: dict = {"iris": "IRIS_MCP_API_KEY", "argus": "ARGUS_API_KEY"}

# ---- [L4.5 阶段2.2 2026-09-28] 任务编排配置正式化（本块为文件尾纯追加，上方任何一项未动）----
# 背景：L4_min 把 IRIS_MCP_API_KEY / IRIS_GATEWAY_URL / MORAY_IRIS_MCP_TIMEOUT 三个变量
# 钉在隔离实例 8012 的重启脚本里（work/l4_restart8012.ps1），正式 8000 服务没有这些配置，
# 8000 一调 Iris 分析就是 401/网关不通。本块把它们收口进正式配置：
#   * 值写 server/.env（load_dotenv 已在上方生效，进程环境变量优先于 .env，
#     隔离实例 8012 继续用脚本显式注入，互不干扰）；
#   * .env.example 同步补文档（含此前完全缺失的 IRIS_GATEWAY_URL 条目）。
# 读取方（均为 env 直读，代码零改动）：
#   IRIS_GATEWAY_URL     engine/task_runner.py:58（llm_patch 降级通道网关，默认 8000）
#   IRIS_MCP_API_KEY     server/app/mcp_gateway.py（get_server_config 展开 MCP_SERVERS.iris.api_key；
#                        旧垫片 adapters/iris_mcp_client.py 已改为转调网关，不再自读 Key）
#   MORAY_IRIS_MCP_TIMEOUT 本文件上方 MCP_SERVERS（默认 30；实测视觉分析 69.7s → .env 给 120）

# 任务编排的候选项目列表（对话触发任务时"项目选择卡"展示；绝对路径，英文分号/逗号分隔）。
#   MORAY_TASK_PROJECTS=D:\MoRay;D:\SomeProj
# 留空 = 只暴露 WORKSPACE_DEFAULT 一个兜底项（不新增配置也能跑，选择卡仍可手动输路径）。
# 不存在的目录由 /api/tasks/projects 出参时过滤（is_dir 校验，与 run-error-fix 入参校验同语义）。
_env_task_projects = os.environ.get("MORAY_TASK_PROJECTS", "").strip()
TASK_PROJECTS: tuple = tuple(
    x.strip().strip('"') for x in _env_task_projects.replace(";", ",").split(",") if x.strip()
) if _env_task_projects else ()

# ---- [L5.2 主动日志监控 2026-09-28]（本块为文件尾纯追加，上方任何一项未动）----
# 红线 7：主动监控必须有开关且**默认关闭**。四键全部 env 可覆盖（server/.env），
# 非法值一律回落默认（绝不让 .env 写错炸导入）。消费端：server/app/log_monitor.py。
MONITOR_ENABLED_DEFAULTS = {"interval": 60, "alert_level": "ERROR", "log_dir": "logs"}

# 开机自启开关（默认 false）：false = 后端进程不自动跑监控循环，
# 只有用户在对话里说"开启监控"（→ POST /api/monitor/enable）才运行；重启即回关。
MONITOR_ENABLED = os.environ.get("MONITOR_ENABLED", "false").strip().lower() \
    in ("1", "true", "yes", "on")

try:
    MONITOR_INTERVAL = max(2, int(os.environ.get("MONITOR_INTERVAL", "").strip()
                                  or MONITOR_ENABLED_DEFAULTS["interval"]))
except ValueError:
    MONITOR_INTERVAL = MONITOR_ENABLED_DEFAULTS["interval"]

# 监控的日志目录（相对路径按 D:\MoRay 根解析；分号/逗号可列多个）。目录不存在 = 每轮如实跳过。
_env_monitor_log_dir = os.environ.get("MONITOR_LOG_DIR", "").strip().strip('"') \
    or MONITOR_ENABLED_DEFAULTS["log_dir"]
MONITOR_LOG_DIRS: tuple = tuple(
    x.strip().strip('"') for x in _env_monitor_log_dir.replace(";", ",").split(",") if x.strip()
)

# 告警阈值：ERROR = ERROR/FATAL/CRITICAL 都告；FATAL = 仅 FATAL/CRITICAL 告。
MONITOR_ALERT_LEVEL = os.environ.get("MONITOR_ALERT_LEVEL", "").strip().upper() \
    or MONITOR_ENABLED_DEFAULTS["alert_level"]
if MONITOR_ALERT_LEVEL not in ("ERROR", "FATAL"):
    MONITOR_ALERT_LEVEL = MONITOR_ENABLED_DEFAULTS["alert_level"]

# ---- [2026-09-30 本地优先强制 + 云端熔断]（本块为文件尾纯追加，上方任何一项未动）----
# 背景：MoRay 是本地优先架构。此前主对话默认路由云端 DeepSeek、且本地失败自动降级云端，
# 造成"本地有 Ollama 却偷偷调云端"的账单问题。本块收口为强约束：
#   * LLM_PROVIDER 默认 ollama（除非环境变量显式指明其它值，否则绝不默认云端）；
#   * 本地上游 = Ollama（http://localhost:11434），默认模型 qwen3.5:9b
#     （实测本机 Ollama 模型列表：qwen3.5:9b / qwen2.5:7b / qwen2.5:1.5b / qwen3.5:4b /
#       deepseek-r1:7b / moray-laya-*；**Ollama 里没有 bonsai2-27b**——那是 8080 端口的
#       独立本地推理服务（Ternary-Bonsai-2-27B-PQ2_0），非 Ollama 模型）；
#   * 云端熔断：任何 deepseek/openai 类云端调用，只有 ENABLE_CLOUD_API 显式为 true 才放行；
#     否则 llm_proxy 直接拒绝（cloud_api_disabled），从源头断掉偷跑云端。
LLM_PROVIDER = os.environ.get("MORAY_LLM_PROVIDER", "ollama").strip().lower()
OLLAMA_BASE_URL = os.environ.get("MORAY_OLLAMA_BASE_URL", "http://localhost:11434").strip()
OLLAMA_MODEL = os.environ.get("MORAY_OLLAMA_MODEL", "qwen3.5:9b").strip()
# [2026-10-01 任务A v2] OLLAMA_MODELS（复数）当前无任何消费者，
# 是 2026-09-30 本地优先强制时预留的配置，尚未接线。
# 真正生效的是 OLLAMA_MODEL（单数）。若将来接线，需在 llm_proxy 增加校验。
OLLAMA_MODELS = tuple(
    x.strip() for x in os.environ.get("MORAY_OLLAMA_MODELS", "qwen3.5:9b,bonsai2-27b").split(",") if x.strip()
) or ("qwen3.5:9b",)
ENABLE_CLOUD_API = os.environ.get("ENABLE_CLOUD_API", "").strip().lower() in ("1", "true", "yes", "on")

# ---- [2026-09-30b 外部 27B 本地服务（provider=custom_27b）]（本块为文件尾纯追加）----
# 独立于 Ollama 的本地 27B 推理服务（Ternary-Bonsai-2-27B，OpenAI 兼容 /v1/chat/completions）。
# 它不是 Ollama 模型，而是独立 HTTP 服务（默认 8080）。切换方式：
#   * server/.env 设 MORAY_LLM_PROVIDER=custom_27b（全局切换）；
#   * 地址/模型名可用环境变量覆盖：MORAY_27B_BASE_URL / MORAY_27B_MODEL_NAME；
#   * 前端模型选择器也可在请求体里带 provider:"custom_27b" 临时切换（见 llm_proxy.py）。
LOCAL_27B_BASE_URL = os.environ.get("MORAY_27B_BASE_URL", "http://127.0.0.1:8080/v1").strip()
LOCAL_27B_MODEL_NAME = os.environ.get("MORAY_27B_MODEL_NAME", "bonsai2-27b").strip()
# 27B 服务的 API Key：默认空（本服务实测要求 Bearer 认证，任意占位会被 401 拒）。
# **不要写进 config.py（项目要开源）**：真实 key 放 server/.env 的 MORAY_27B_API_KEY（.env 已被 .gitignore 忽略）。
# 为空时请求不带 Authorization（服务端会返回 401，错误提示会如实说明）。
LOCAL_27B_API_KEY = os.environ.get("MORAY_27B_API_KEY", "").strip()

# ---- [2026-09-30c 云端三层开关 + 金额预算闸门（任务二）]（本块为文件尾纯追加，上方任何一项未动）----
# 三层语义：L0 = ENABLE_CLOUD_API=false（默认，保持不动，云端不存在于调度链）；
#   L1 = ENABLE_CLOUD_API=true 且 ENABLE_CLOUD_FALLBACK=false（云端仅可被请求体显式 provider=cloud 调用）；
#   L2 = 两者皆 true（本次仍不实现自动 fallback，届时必须走 cloud_budget_guard.acquire_fallback 预算硬约束）。
# 金额预扣/结算/熔断状态持久化在独立 SQLite（MORAY_BUDGET_DB），由 server/app/cloud_budget_guard.py
# 全权管理（自带建表，db.py 零改动）。数值项非法值回落默认（与上方 MAX_LLM_CALLS_PER_SESSION 同惯例）。
ENABLE_CLOUD_FALLBACK = os.environ.get("ENABLE_CLOUD_FALLBACK", "false").strip().lower() in ("1", "true", "yes", "on")

# [2026-10-01 Z-3] 从 1.0 调到 5.0：ZCode 30 轮任务约 $0.03-0.15，
# 1.0 在多个任务并发时会中途熔断。5.0 ≈ 35 元/天，用户拍板。
DAILY_BUDGET_USD: float = 5.0
try:
    DAILY_BUDGET_USD = float(os.environ.get("DAILY_BUDGET_USD", "5.0"))
except ValueError:
    DAILY_BUDGET_USD = 5.0

# [2026-10-01 Z-3] 从 50 调大：ZCode 单任务 10-30 轮云端调用，
# 50 会在第二次任务中途 429。金额闸 DAILY_BUDGET_USD 仍是主要约束。
DAILY_MAX_CALLS: int = 500
try:
    DAILY_MAX_CALLS = int(os.environ.get("DAILY_MAX_CALLS", "500"))
except ValueError:
    DAILY_MAX_CALLS = 500

PER_CALL_BUDGET_USD: float = 0.1
try:
    PER_CALL_BUDGET_USD = float(os.environ.get("PER_CALL_BUDGET_USD", "0.1"))
except ValueError:
    PER_CALL_BUDGET_USD = 0.1

MORAY_BUDGET_DB = os.environ.get("MORAY_BUDGET_DB", r"D:\MoRay\data\cloud_budget.db").strip()

# ---- [2026-10-01 Z-3] ZCode 云端专用开关（本块为文件尾纯追加，上方任何一项未动）----
# ZCode 是否允许走云端。默认 false。
# 与 ENABLE_CLOUD_API 独立：后者是全局云端总闸，
# 前者是专给 ZCode 的二次确认。两者都为 true 时，ZCode 才会走云端。
# 注意：本次只加配置字段，不接线；接线（zcode 链路注入 X-Moray-Provider: cloud 的
# 放行判定）留给 Z-4。消费方届时读本字段，ZCode 的 provider 来源 = X-Moray-Provider 头
# （见 llm_proxy._resolve_body_provider）。
ENABLE_ZCODE_CLOUD = os.environ.get("ENABLE_ZCODE_CLOUD", "").strip().lower() in ("1", "true", "yes", "on")

# ---- [2026-10-02 M1] MemoryBridge 独立记忆库路径（本块为文件尾纯追加，上方任何一项未动）----
# 记忆数据与主库（moray.sqlite3）物理隔离：用户个人数据可单独剥离/备份/迁移；
# 独立库先例 = MORAY_BUDGET_DB（cloud_budget.db）。测试用 MORAY_MEMORY_DB 指向临时文件隔离。
MEMORY_DB = os.environ.get(
    "MORAY_MEMORY_DB",
    str(DATA_DIR / "memory.sqlite3")
)

# ---- [2026-10-04 反思MVP] 反思循环配置（本块为文件尾纯追加，上方任何一项未动）----
# 总闸 MORAY_REFLECTION 默认关：MONITOR_ENABLED / MORAY_MEMORY_HOOK 同款 env 惯例，
# 翻转需重启 8000。反射逻辑全在 server/app/reflection.py（手动端点触发，无定时循环）。
REFLECTION_ENABLED = os.environ.get("MORAY_REFLECTION", "0").strip().lower() not in ("0", "false", "no", "off")
# 提炼模型默认与主对话同款（qwen3.5:9b）——同模型不互踢；如需常驻换小模型在此覆盖
# （Ollama 单槽位惯例见 self_check.py 头注 [[moray-argus-link-and-ollama-single-slot]]）。
REFLECTION_MODEL = os.environ.get("MORAY_REFLECTION_MODEL", "").strip() or OLLAMA_MODEL
# 反思 prompt 带最多 50 条任务记录、要求输出多条 JSON，远长于 self_check 单步判定，
# 超时按分钟级给预算（self_check 默认 8s 不够用）。
REFLECTION_TIMEOUT = float(os.environ.get("MORAY_REFLECTION_TIMEOUT", "120"))
# 反思上下文窗口：默认 8192（2026-10-04 实测 4096 会截断——长 prompt 下推理模型
# 思考链耗尽上下文，导致 content 为空）。Ollama 单请求用 options.num_ctx 覆盖。
try:
    REFLECTION_NUM_CTX = int(os.environ.get("MORAY_REFLECTION_NUM_CTX", "8192"))
except ValueError:
    REFLECTION_NUM_CTX = 8192
# 推理协议：ollama（默认，/api/chat）| openai（/v1/chat/completions）。
# openai 先例场景：本机 Ollama 引擎损坏（2026-10-04 实锤 lib/ollama 缺 llama-server.exe）
# 时，用 Bonsai llama-server 加载同一个 gguf blob——模型不变，只换协议。
REFLECTION_API = os.environ.get("MORAY_REFLECTION_API", "ollama").strip().lower()
# 推理服务基址：缺省沿用 OLLAMA_BASE_URL；openai 模式指向 llama-server 等兼容服务。
REFLECTION_BASE_URL = os.environ.get("MORAY_REFLECTION_BASE_URL", "").strip() or OLLAMA_BASE_URL

# ---- [2026-10-04 器官→记忆 三根线] 器官旁路写记忆总闸（本块为文件尾纯追加，上方任何一项未动）----
# 消费点 = server/app/organ_memory.py（Argus 研究报告 / ZCode 流式任务 / Iris 修复任务
# 三条线成功收尾各写一条 kind=task_result 记忆进 MemoryBridge）。
# organ_memory._organ_memory_enabled() 在调用时直读 env（executor MORAY_MEMORY_HOOK
# 同款惯例：默认开，翻转后重启 8000 生效）；本常量是同一 env 的启动快照
# （MORAY_REFLECTION 同款表达式），供其他消费方/测试直读，两处语义恒等。
# 此值仅供参考，生效以调用时 env 为准（organ_memory._organ_memory_enabled 调用时读）
ORGAN_MEMORY_ENABLED = os.environ.get("MORAY_ORGAN_MEMORY", "1").strip().lower() not in ("0", "false", "no", "off")

# ---- [2026-10-06 Synapse v1] 神经总线独立事件库 + 总闸（本块为文件尾纯追加，上方任何一项未动）----
# synapse.sqlite3 与主库（moray.sqlite3）/记忆库（memory.sqlite3）物理隔离：
# 同 MORAY_BUDGET_DB / MORAY_MEMORY_DB 独立库惯例，总线数据可单独剥离/备份/迁移。
# 消费哲学同 Argus event_hub：库是唯一事实来源，队列只叫醒（见 synapse_bus.py 头注）。
# 测试用 MORAY_SYNAPSE_DB 指向临时文件隔离。
# 总闸默认关（MONITOR_ENABLED / MORAY_REFLECTION 同款 env 惯例），翻转需重启 8000；
# 关闸时 send() 完全 no-op（synapse_bus.send 首行检查），bus consumer 也不起（main.py 门控）。
SYNAPSE_DB = os.environ.get("MORAY_SYNAPSE_DB", str(DATA_DIR / "synapse.sqlite3"))
SYNAPSE_ENABLED = os.environ.get("MORAY_SYNAPSE", "0").strip().lower() not in ("0", "false", "no", "off")

# ---- [外部触达 v1 2026-11-01] OneBot 同机对接配置（本块为文件尾纯追加，上方任何一项未动）----
# 总闸默认关：BOT_ENABLED=false 时 POST /api/bot/webhook 一律 503、后台 bot_worker 不起。
# 属性名用 BOT_*，env 前缀沿用仓库惯例 MORAY_*（映射见 server/.env.example）：
#   BOT_ENABLED   <- MORAY_BOT_ENABLED
#   BOT_TOKEN     <- MORAY_BOT_TOKEN
#   BOT_API_BASE  <- MORAY_BOT_API_BASE
#   BOT_API_TOKEN <- MORAY_BOT_API_TOKEN
BOT_ENABLED = os.environ.get("MORAY_BOT_ENABLED", "0").strip().lower() not in ("0", "false", "no", "off")
# Webhook 入站鉴权（OneBot access-token 同款，Authorization: Bearer <token>）。
# 留空 = 拒绝一切入站（fail-close）：没配 token 的 webhook 等于把本机 LLM 白送。
BOT_TOKEN = os.environ.get("MORAY_BOT_TOKEN", "").strip()
# OneBot 实现（NapCat / Lagrange）的 HTTP API 根地址。bot_client.startup 只放行回环主机，
# 即使 .env 被改成公网地址也会在启动期拒绝（本地 Bot 网关不得被当成任意主机代理）。
BOT_API_BASE = os.environ.get("MORAY_BOT_API_BASE", "http://127.0.0.1:3000").strip().rstrip("/")
# OneBot HTTP API 出站鉴权（可选；与入站 BOT_TOKEN 是两个独立凭据，可相同也可不同）。
BOT_API_TOKEN = os.environ.get("MORAY_BOT_API_TOKEN", "").strip()

# ---- [路线二·长效语义记忆 2026-11-07] Memory Extractor（本块为文件尾纯追加，上方任何一项未动）----
# Mem0 式对话后自动提炼：消费方 = server/app/memory_extractor.py，触发点 = llm_proxy
# /api/llm/chat 收尾（非流式生成完成 / 流式 [DONE] 吐完），fire-and-forget 绝不阻塞聊天。
# 总闸默认开（任务书拍板）；MORAY_MEMORY_EXTRACT_ENABLED=0 一键关停（重启 8000 生效，
# MORAY_REFLECTION / MONITOR_ENABLED 同款 env 惯例）。
MEMORY_EXTRACT_ENABLED = os.environ.get("MORAY_MEMORY_EXTRACT_ENABLED", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# 提炼模型：最便宜的本地小模型（本机 Ollama 实测列表含 qwen2.5:1.5b，见上方 2026-09-30
# 本地优先块注释）。换模型只改 server/.env 的 MORAY_MEMORY_EXTRACT_MODEL，不动代码。
MEMORY_EXTRACT_MODEL = os.environ.get("MORAY_MEMORY_EXTRACT_MODEL", "").strip() or "qwen2.5:1.5b"
# 单次提炼推理超时（秒）：小模型 + 短上下文常态 <5s，120s 已含 Ollama 冷加载余量
# （nomic 嵌入冷加载 83s 的前车之鉴，见 memory_embed._EMBED_TIMEOUT 注释）。
MEMORY_EXTRACT_TIMEOUT = float(os.environ.get("MORAY_MEMORY_EXTRACT_TIMEOUT", "120"))
# 同 session 提炼冷却窗（秒）：防 dsh/ZCode 工具环连发聊天把提炼刷成洪水（KNN 去重
# 是正确性兜底，冷却只是省算力的第一道闸）；0 = 关闭冷却。
MEMORY_EXTRACT_COOLDOWN_SEC = float(os.environ.get("MORAY_MEMORY_EXTRACT_COOLDOWN_SEC", "15"))

# ---- [经验泛化引擎 2026-11-10] skill_extractor（文件尾纯追加，上方任何一项未动）----
# 失败经验 → 通用行为规则 → 相关任务注入【系统经验拦截】→ 连败降信 → 归档。
# 消费方 = server/app/skill_extractor.py（轮询 loop 挂 lifespan）+ llm_proxy 注入点。
# 提炼模型复用 MEMORY_EXTRACT_MODEL（同一枚本地小模型旋钮，qwen2.5:1.5b）。
SKILL_EXTRACT_ENABLED = os.environ.get("MORAY_SKILL_EXTRACT_ENABLED", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# 注入总闸（llm_proxy 消费；提炼照跑，A/B 对照用）
SKILL_RULE_INJECT_ENABLED = os.environ.get("MORAY_SKILL_RULE_INJECT", "1").strip().lower() \
    not in ("0", "false", "no", "off")
# 轮询间隔（秒）：监听记忆库新增失败经验（60s 与 self_check 快照间隔同量级）
SKILL_EXTRACT_INTERVAL_SEC = float(os.environ.get("MORAY_SKILL_EXTRACT_INTERVAL_SEC", "60"))
# 单次经验提炼推理超时（秒）：与 MEMORY_EXTRACT_TIMEOUT 同口径
SKILL_EXTRACT_TIMEOUT = float(os.environ.get("MORAY_SKILL_EXTRACT_TIMEOUT", "120"))
# 注入匹配阈值：任务描述与规则的余弦相似度 > 0.75 才注入（任务书钉死）
SKILL_RULE_SIM_THRESHOLD = float(os.environ.get("MORAY_SKILL_RULE_SIM", "0.75"))
# 单次注入最多规则条数（任务书钉死 3）
SKILL_RULE_MAX_INJECT = int(os.environ.get("MORAY_SKILL_RULE_MAX_INJECT", "3"))
# 新规则初始置信度（任务书钉死 0.8；落 memory_bank.weight）
SKILL_RULE_INIT_CONFIDENCE = float(os.environ.get("MORAY_SKILL_RULE_INIT_CONF", "0.8"))
# 注入后连续失败结果次数达限 → 置信度 -SKILL_RULE_CONF_PENALTY（任务书：连败 5 次 -0.2）
SKILL_RULE_FAIL_STREAK_LIMIT = int(os.environ.get("MORAY_SKILL_RULE_FAIL_STREAK", "5"))
SKILL_RULE_CONF_PENALTY = float(os.environ.get("MORAY_SKILL_RULE_CONF_PENALTY", "0.2"))
# 置信度低于 0.5 → 自动归档（不再注入；任务书钉死）
SKILL_RULE_MIN_CONFIDENCE = float(os.environ.get("MORAY_SKILL_RULE_MIN_CONF", "0.5"))
