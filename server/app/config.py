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
# Argus 通道走上面的单标量 ARGUS_BASE_URL；本块是第一个"MoRay 消费外部 MCP 服务"的注册表，
# 由 D:\MoRay\adapters\iris_mcp_client.py 读取（消费端做 ${VAR} 展开，见该文件头注）。
# 追加新外部服务 = 只在 MCP_SERVERS 里加键，零核心改动（设计文档 docs/p_mcp_audit.md §3.3）。
MCP_SERVERS: dict = {
    "iris": {
        "transport": "sse",
        "url": os.environ.get("MORAY_IRIS_MCP_URL", "http://127.0.0.1:9301/sse"),
        "api_key": "${IRIS_MCP_API_KEY}",
        "timeout": float(os.environ.get("MORAY_IRIS_MCP_TIMEOUT", "30")),
        "enabled": os.environ.get("MORAY_IRIS_MCP_ENABLED", "1").strip().lower()
                   not in ("0", "false", "no", "off"),
    }
}

# ---- [L4.5 阶段2.2 2026-09-28] 任务编排配置正式化（本块为文件尾纯追加，上方任何一项未动）----
# 背景：L4_min 把 IRIS_MCP_API_KEY / IRIS_GATEWAY_URL / MORAY_IRIS_MCP_TIMEOUT 三个变量
# 钉在隔离实例 8012 的重启脚本里（work/l4_restart8012.ps1），正式 8000 服务没有这些配置，
# 8000 一调 Iris 分析就是 401/网关不通。本块把它们收口进正式配置：
#   * 值写 server/.env（load_dotenv 已在上方生效，进程环境变量优先于 .env，
#     隔离实例 8012 继续用脚本显式注入，互不干扰）；
#   * .env.example 同步补文档（含此前完全缺失的 IRIS_GATEWAY_URL 条目）。
# 读取方（均为 env 直读，代码零改动）：
#   IRIS_GATEWAY_URL     engine/task_runner.py:58（llm_patch 降级通道网关，默认 8000）
#   IRIS_MCP_API_KEY     adapters/iris_mcp_client.py:56（消费端展开 MCP_SERVERS.iris.api_key）
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
