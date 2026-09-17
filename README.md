# MoRay · 本地优先的 AI 开发者工作台

> **让 AI Agent 在你的电脑上安全地干活——数据不出本机、操作可回放、能力可被外部调用。**
> 一份产物、两种用法：双击即用的纯前端单文件，或 FastAPI + SQLite 的本地全栈应用；兼容 **Ollama 本地模型**与任意 **OpenAI 兼容云端**（DeepSeek 等）。

<p align="left">
  <b>🌐 在线体验（BYOK）：</b><a href="https://moray1.pages.dev">moray1.pages.dev</a>
  &nbsp;｜&nbsp; <b>💻 本地使用：</b>双击 <code>moray-workbench.html</code>
  &nbsp;｜&nbsp; <b>📦 许可证：</b>MIT
</p>

---

## 三大核心亮点

### 🔒 1. 本地安全沙箱——七层路径防护，AI 碰不到你的系统文件

MoRay 的本机 Agent 运行在**受控工作区**（默认 `D:\MoRayWorkspace`）内，七层路径防护层层把关：

- **工作区根锁定**：所有路径强制解析到工作区内，绝对路径 / `..` 越界 / 跨盘符一律拒绝
- **符号链接逃逸拦截**：`resolve()` 解析真实路径，防止通过 symlink 跳出工作区
- **危险后缀拒绝**：`.exe` / `.bat` / `.ps1` 等可执行文件写入被拦截
- **命令白名单**：`run_command` 只允许 `git status/log/diff/branch`、`python/node/npm --version`、`where`，无 shell、无管道、无重定向
- **副作用人工审批**：写文件 / 新建 / 移动 / 执行命令 / 编辑前必须人工确认，展示 **unified diff** 红绿对比
- **全量审计落库**：每次工具调用（含被拒绝的）都记录时间、参数、审批状态、耗时，可在「飞行记录仪」时间轴中回溯
- **Windows 保留设备名拦截**：`CON` / `NUL` / `COM1` 等非法路径名拒绝

### ⏪ 2. 确定性回放（Event Sourcing）——每一步操作都能重来

基于事件溯源架构，MoRay 把每次工具调用的**完整入参、出参、上下文状态**序列化为事件快照（`event_snapshots` 表），实现：

- **时间轴 UI**：「飞行记录仪」可视化展示所有工具调用，带时间刻度、状态颜色（成功/失败/待审批/被拒绝）、可点击展开详情
- **修改并重跑**：对失败或超时的步骤，可直接修改参数后重新执行——比如 `read_file` 读了不存在的文件，改成正确路径一键重跑
- **安全校验不变**：重放仍然经过完整的七层路径防护和审批逻辑，不是"绕过安全直接执行"
- **审计可追溯**：重放产生新的审计记录，原始记录保留，完整操作链可追溯

### 🔌 3. MCP 协议兼容——你的 11 个本机工具，外部 AI 也能调用

MoRay 实现了标准的 **MCP（Model Context Protocol）Server**（SSE 传输），让 Claude Desktop、Cursor、Cherry Studio 等外部 AI 客户端可以安全地调用你的本机工具：

- **11 个工具全部暴露**：列目录、读文件、写文件、新建文件、移动文件、执行命令、查找文件、全文搜索、编辑文件、查看进程、系统信息
- **默认只读**：开箱即用状态下，外部客户端只能调用 6 个只读工具；5 个副作用工具（写/新建/移动/命令/编辑）默认禁止，需显式配置白名单
- **安全同源**：MCP 调用与前端调用共用同一个执行入口（`_execute_tool_internal`），七层路径防护、审批逻辑、审计日志完全一致，绝不另起一套放宽校验
- **零新增依赖**：用 FastAPI 原生 `StreamingResponse` + `asyncio.Queue` 实现，不引入重型 MCP SDK

---

## 快速开始

### Windows 一键全栈（推荐 · 完整能力）
1. 安装 [Python 3.12/3.13](https://www.python.org/downloads/)（勾选 *Add to PATH*）
2. 安装 [Ollama](https://ollama.com) 并拉取模型（Agent 任务推荐 `qwen2.5:7b`）
3. 双击 **`启动MoRay.bat`**：自动建虚拟环境 → 装依赖 → 起后端 → 打开浏览器

### 纯前端免后端
直接双击 `moray-workbench.html`（数据存 IndexedDB）。本机 Agent / MCP Server 需要本地后端。

### MCP 连接（外部 AI 客户端）
后端启动后，MCP SSE 地址为：
```
http://127.0.0.1:8000/api/mcp/sse
```
在 Claude Desktop / Cursor 的 MCP 配置中填入此地址即可。

---

## 技术架构

```
┌──────────────────────────────────────────────────────────┐
│  浏览器：moray-workbench.html（单文件，零 CDN）              │
│  · 纯前端模式：IndexedDB 存储                             │
│  · 后端模式：经 http://127.0.0.1:8000 访问                │
└───────────────┬──────────────────────────┬───────────────┘
                │                          │
┌───────────────▼──────────────────────────▼───────────────┐
│  server/  FastAPI 薄后端（仅监听 127.0.0.1）                │
│  · /api/agent/*  本机 Agent：七层安全层 + 11 工具 + 审批    │
│  · /api/mcp/sse  MCP Server：SSE + JSON-RPC 2.0           │
│  · /api/agent/replay  确定性回放：事件快照 + 修改重跑        │
│  · agent_tool_log / event_snapshots  审计 + 事件溯源        │
└───────────────┬──────────────────────────┬───────────────┘
                │                          │
       SQLite（server/data）         工作区（D:\MoRayWorkspace）
        + Ollama（本机 11434）       + 云端 LLM（Key 在 .env）
```

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | 原生 JavaScript（21 分片组装）、Tailwind 风格 CSS、Lucide 图标（全部本地化，零 CDN） |
| 前端存储 | IndexedDB（含禁用时内存降级） |
| 后端 | FastAPI + Uvicorn（Python 3.12），标准库 sqlite3 参数化查询、无 ORM |
| MCP | 原生 SSE + JSON-RPC 2.0（零新增依赖） |
| 模型接入 | Ollama + 任意 OpenAI 兼容云端，SSE 流式 |

## License

[MIT](LICENSE)。本项目仅为客户端 / 本地工具，不内置模型、不提供 API 额度。
