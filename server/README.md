# MoRay 本地薄后端（产品 v1.0.0 · 构建 3.18.0）

本地优先架构的核心服务：FastAPI + SQLite（标准库 sqlite3，参数化查询，无 ORM），仅监听 127.0.0.1。
功能全景：健康检查 / 云端 LLM 代理（Key 只存 server/.env）/ 会话/消息/设置 CRUD 与前后端同步 /
静态托管前端（http://127.0.0.1:8000/ 直接打开应用；file:// 双击直开仍可用）。

## 目录结构

```
server/
├── requirements.txt        # 依赖：fastapi、uvicorn[standard]（兼容 Python 3.12 / 3.13）
├── README.md               # 本文件
├── data/                   # SQLite 数据目录（首次启动自动创建）
│   └── moray.sqlite3       # 数据库文件（自动生成）
└── app/
    ├── __init__.py         # 包声明 + 版本
    ├── config.py           # 主机/端口/SQLite 路径/版本常量（MORAY_PORT 覆盖端口）
    ├── db.py               # sqlite3 连接与建表（conversations/messages/settings/kv）+ ping()
    └── main.py             # FastAPI 应用：/api/health、CORS、全局异常兜底、/
```

## 安装与启动

### Windows（一键脚本）

```bat
scripts\start_backend.bat
```

脚本自动完成：创建 `server\.venv` 虚拟环境 → `pip install -r requirements.txt` →
在 `server/` 目录启动 uvicorn（`app.main:app`，127.0.0.1:8000）。

### macOS / Linux（跨平台手动）

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${MORAY_PORT:-8000}"
```

### 健康检查自测

启动后浏览器或 curl 访问：

```
http://127.0.0.1:8000/api/health
```

预期返回：

```json
{
  "ok": true,
  "service": "moray-backend",
  "version": "0.1.0",
  "time": "2026-08-31T07:00:00.000000+00:00",
  "db": "ok"
}
```

`db` 字段来自 `db.ping()`（SQLite 实际读写验证）；`server/data/moray.sqlite3`
首次启动自动创建，含 conversations / messages / settings / kv 四张空表骨架。

## 端口占用怎么办

默认 8000 被占用时，用环境变量 `MORAY_PORT` 换端口：

```bat
set MORAY_PORT=8123
scripts\start_backend.bat
```

```bash
MORAY_PORT=8123 ./scripts/start_backend.sh   # 或直接 uvicorn --port 8123
```

前端探测默认访问 8000；换端口后请以查询参数指定（如 `moray-workbench.html?backend=8123`）。

### 一键启动脚本说明

- `scripts/start_backend.bat`（Windows）：自动找 Python（python / py -3）→ 建 venv →
  装依赖（官方源失败自动回退阿里云镜像 `mirrors.aliyun.com`）→ 端口占用检测（友好提示换端口）→
  启动 uvicorn → 延迟 2 秒自动打开浏览器到 `/api/health`；窗口保持，关闭窗口即停止服务；
- `scripts/start_backend.sh`（macOS / Linux）：等价逻辑（python3 + server/.venv）；
- 依赖已安装时二次运行秒级启动，不重复联网安装。

## CORS 说明（file:// 场景）

前端可能直接以 `file://` 双击打开（Origin 为字符串 `"null"`），也可能跑在
`http://127.0.0.1` / `http://localhost` 任意端口。后端用 `allow_origin_regex` 同时放行：
`^(null|file://.*|http://(127\.0\.0\.1|localhost)(:\d+)?)$`，方法/头全部允许；
未使用 `allow_origins=['*']` 与 credentials 的错误组合。

## 前端集成（探测 + 同步 + 代理）

- 页面启动后静默探测 `/api/health`（AbortController 2000ms 超时，全部异常被捕获；后端地址默认
  跟随同源 location.origin，`?backend=` 或 localStorage `moray_backend_origin` 可覆盖，file:// 回退
  http://127.0.0.1:8000）；
- 底部状态栏状态项：`本地后端 ● 已连接`（success 色）/ `本地后端 ○ 离线模式`（tertiary 色）；
- 后端同步（useBackendSync 默认开）：在线双写 SQLite、离线自动回退 IndexedDB 并标记"待同步"、
  一键同步（设置 → 数据管理）；
- 云端 Key 代理：设置 → 云端 API → 「经本地后端代理」，Key 只存在 server/.env，不进入浏览器；
- 后端未启动时前端与现在完全一致（纯前端 IndexedDB 照常工作，不白屏、不报错、不禁用功能）。

## 后续路线

- 定时工作流后端调度（当前定时任务仅页面打开时运行）
- 附件（图片）DataURL 入 SQLite 同步
- 可选：鉴权 / 多用户 / 云账号
