# MoRay · 本地优先的 AI 开发者工作台

> **数据不出本机、Key 不裸奔、按任务难度自动分流模型**的一体化 AI 工作台。
> 一份产物、两种用法：双击即用的纯前端单文件，或 FastAPI + SQLite 的本地全栈应用；同时兼容 **Ollama 本地模型**与任意 **OpenAI 兼容云端**（DeepSeek 等）。

<p align="left">
  <b>🌐 在线体验（BYOK，自带 Key）：</b><a href="https://moray1.pages.dev">moray1.pages.dev</a>
  &nbsp;｜&nbsp; <b>💻 本地使用：</b>双击 <code>moray-workbench.html</code>
  &nbsp;｜&nbsp; <b>📦 许可证：</b>MIT
</p>

> 本项目不内置任何大模型、也不提供 API 额度：本地接 Ollama 即可免费跑，云端用你自己的 Key（仅保存在你的设备上）。

<!--
截图位（拍好后放到 docs/screenshots/ 并在此引用，避免空裂图）：
1) 对话主界面（智能路由标签 + 思考折叠） 2) 多模型并排对比  3) 成本中心
4) 提示词/片段库  5) 设置 → API 智能网关  6) 自动化工作流编辑器
-->

---

## 一、为什么做这个

本地开源模型（Ollama）免费、隐私好，但多模型切换、对比、成本管理很割裂；云端大模型强但 token 贵、Key 要在各个网页里反复填、对话记录散落在各处。MoRay 想解决三件事：

1. **省钱省时**：简单问题自动丢给最小最快的本地模型，复杂推理才上大模型 / 开深度思考；
2. **隐私可控**：默认数据只在本机，云端 Key 可交给本地后端代理、永不进入浏览器；
3. **一个工作台闭环**：对话、多模型对比、提示词库、代码片段、自动化工作流、知识库（RAG）、成本核算全部在一个离线可用的界面里。

## 二、核心特性

**模型与智能路由**
- 🧠 同时接入 Ollama 本地模型与任意 OpenAI 兼容云端，界面内切换 / 测试 / 拉取模型；
- 🚦 **智能路由按难度分流**：简单任务自动选最小模型省成本，复杂任务自动选最强模型保正确，每条回复标注实际命中模型与路由原因；
- 🧯 **失败熔断 + 已驻留亲和**：连续失败的模型短期冷却（半开试探），优先复用已在显存中的模型，避免每句话都反复加载、反复失败；
- 🤔 深度思考 **auto / on / off** 三态，思考型模型展示可折叠推理过程；
- 🖥️ **多模型对比**：同一问题双模型并排流式输出、真·同步滚动、输入区钉底、可导出对比报告。

**本地优先与隐私**
- 🔒 基线存储为浏览器 IndexedDB，**断网 / 无后端也能完整使用**，不白屏、不报错；
- 🗄️ 可选 FastAPI + SQLite 后端：在线双写、离线自动回退并标记"待同步"，恢复后一键补传（冲突以较新者为准），支持多窗口 / 同局域网共享；
- 🛡️ 选择"本地后端代理"后，云端 Key 只存于 `server/.env`，浏览器请求仅发往 `127.0.0.1`；
- 🌐 在线版采用 **BYOK（Bring Your Own Key）**：访客填自己的 Key、只存自己浏览器，部署者不承担任何 token 费用。

**工作台能力**
- 提示词库、代码片段库（含受限 JS 运行器）、自动化工作流编排、知识库 RAG、收藏 / 分支 / 引用等完整消息操作；
- 成本中心：基于服务端 `usage` **精确计费**（而非按字数估算）、预算阈值、触顶自动降级、CSV 导出、自定义价格表；
- ⌘K 命令面板、深浅主题与自定义壁纸、PWA 可安装离线运行、1280/1024/768/390 响应式适配。

> **能力边界（如实声明）**：内置代码运行器基于 Web Worker，只隔离 DOM、并非完整安全沙箱；本地嵌入向量因体积较大按需联网加载。不夸大离线与安全边界。

## 三、技术架构

```
┌──────────────────────────────────────────────────────────┐
│  浏览器：moray-workbench.html（约 0.9MB 单文件，零 CDN）    │
│  · 纯前端模式：IndexedDB 存储，Ollama / 云端直连           │
│  · 后端模式：经 http://127.0.0.1:8000 访问                │
└───────────────┬──────────────────────────┬───────────────┘
                │ /api/*（同步 / 代理）       │ file:// 直开（无后端）
┌───────────────▼──────────────────────────┴───────────────┐
│  server/  FastAPI 薄后端（约 700 行，仅监听 127.0.0.1）     │
│  · /api/health 健康检查      · /api/llm/chat 云端代理透传   │
│  · conversations/messages/settings/kv 的 SQLite CRUD      │
│  · 同源托管前端：访问 http://127.0.0.1:8000/ 即完整应用    │
└───────────────┬──────────────────────────┬───────────────┘
                │                          │
       SQLite（server/data，自动建表）   云端 LLM（Key 在 server/.env）
                 + Ollama（本机 11434，直连）
```

**关键设计决策（也是主要工程难点）**

1. **双存储 + 离线降级**：以 IndexedDB 为不可缺的基线，后端在线时双写 SQLite；后端不可达自动回退本地并标记，恢复后按"较新者胜"补同步，保证任何网络状态下都不丢数据、不阻塞输入。
2. **一份产物、两种部署**：`parts/` 18 个分片经 `assemble.py` **幂等**拼装为单文件前端；同一文件既能 `file://` 双击直开，也能被 FastAPI 同源托管，静态站与本地全栈共用一份构建产物。
3. **智能路由与容错**：复杂度分级选模；为失败模型设计短期熔断与半开试探，结合 Ollama `/api/ps` 偏好已驻留模型；用户手动选定的模型不会被自动路由覆盖。
4. **成本精确可控**：优先采用服务端返回的 `usage.completion_tokens` 精确计量，叠加精确 / 语义缓存与预算触顶自动降级，把"本地免费、云端省钱"落到数字上。
5. **安全与诚实**：SQL 全部参数化（无 ORM）、CORS 用精确正则同时放行 `file://` 与本机端口（避免 `* + credentials` 错误组合）、Key 只落 `.env`，并对沙箱 / 离线能力做不夸大的边界说明。

## 四、快速开始

### 方式 ① Windows 一键全栈（推荐）
1. 安装 [Python 3.12/3.13](https://www.python.org/downloads/)（安装时勾选 *Add to PATH*）；
2. （可选，本地模型）安装 [Ollama](https://ollama.com) 并 `ollama pull` 一个模型；
3. 双击 **`启动MoRay.bat`**：自动建虚拟环境 → 装依赖 → 起后端 → 打开浏览器。
   首次 1–3 分钟（官方源慢会自动回退阿里云镜像），之后秒开；换端口：`set MORAY_PORT=8123` 再启动。

### 方式 ② macOS / Linux
```bash
chmod +x start_moray.sh
./start_moray.sh                   # 默认 8000
MORAY_PORT=8123 ./start_moray.sh   # 换端口
```

### 方式 ③ 纯前端免后端
直接双击 `moray-workbench.html`（数据存 IndexedDB）。本地 Ollama 对话、云端直连、智能路由、思考三态、多模型对比、成本统计均可用；SQLite 同步与"Key 不进浏览器"的后端代理需要方式 ①/②。

### 云端 Key（可选）
复制 `server/.env.example` 为 `server/.env`，填写后重启后端，并在「设置 → 云端 API」选择"经本地后端代理"：
```ini
MORAY_LLM_BASE_URL=https://api.deepseek.com/v1
MORAY_LLM_API_KEY=sk-你的key
MORAY_LLM_MODEL=你的模型名
```
Ollama 本地模型无需任何配置（自动探测 11434）。

## 五、在线部署（BYOK，零服务器成本）

`python assemble.py --web` 生成的 `web/` 是纯静态站，可直接拖拽部署到 **Cloudflare Pages / Vercel / Netlify / GitHub Pages**，无需构建配置；浏览器直连遇 CORS 时，可按 [`deploy/README.md`](deploy/README.md) 五步部署一个免费的 Cloudflare Worker 透传代理（不存储、不记录 Key）。

## 六、目录结构

```
MoRay/
├── moray-workbench.html   # 单文件前端（双击即用，构建产物）
├── 启动MoRay.bat / start_moray.sh   # 一键启动（Win / mac·Linux）
├── assemble.py            # 前端构建：parts/ 分片 → 单文件（幂等）
├── parts/                 # 前端源码：18 个功能分片
├── vendor/                # 运行依赖本地化（tailwind/marked/highlight/lucide…，零 CDN）
├── wallpapers/            # 内置壁纸
├── scripts/               # 后端启动、发布构建、布局/重复 id/括号校验脚本
├── server/                # FastAPI + SQLite 薄后端（app/、requirements、.env.example）
├── deploy/                # 在线版 Cloudflare Worker 跨域代理（可选）
├── web/                   # assemble --web 导出的静态部署目录
├── src-tauri/             # 桌面安装包壳（规划 / 进行中）
└── design-preview/        # 开场动效设计选型稿
```

## 七、技术栈

| 层 | 选型 |
|---|---|
| 前端 | 原生 JavaScript（18 分片组装为约 0.9MB 单文件）、Tailwind 风格 CSS、Lucide 图标、marked / DOMPurify / highlight（全部本地化，零 CDN） |
| 前端存储 | IndexedDB（本地优先，含禁用时的内存降级） |
| 后端 | FastAPI + Uvicorn（Python 3.12/3.13），标准库 sqlite3 参数化查询、无 ORM |
| 模型接入 | Ollama（`/api/chat` 与 OpenAI 兼容口）+ 任意 OpenAI 兼容云端，SSE 流式 |
| 工程化 | 分片幂等组装、四份产物哈希一致性校验、PWA（sw.js + manifest）、静态 / 全栈 / 桌面多形态 |

## 八、开发与构建

```bash
python assemble.py                 # parts/ → moray-workbench.html（幂等）
python assemble.py --web web       # 导出可部署的纯静态目录 web/
python scripts/build_release.py    # 生成干净发布包 release/（自动排除密钥/数据/备份）
```
`scripts/` 下另含重复 id 扫描、未闭合标签 / 括号平衡、布局与产物哈希校验等自检脚本。

## 九、路线图

- 定时工作流的后端调度；图片附件 DataURL 入 SQLite 同步；
- Tauri / 免 Python 的 Windows 便携安装包；
- 可选多用户与加密云同步（需自托管）。

## 十、常见问题

- **端口被占用**：`set MORAY_PORT=8123` 后重启，页面用 `moray-workbench.html?backend=8123` 指定。
- **Ollama 未连接**：启动 Ollama 并拉取模型，模型下拉点刷新。
- **云端 401 / 欠费 / 429**：检查 `server/.env` 的 Key 与余额，错误以中文提示且不会回显 Key。
- **PWA 缓存旧版**：Ctrl+Shift+R 硬刷新，或直接访问后端同源地址 `http://127.0.0.1:8000/`。

## License

[MIT](LICENSE)。本项目仅为客户端 / 本地工具，不内置模型、不提供 API 额度；各自接入的第三方模型与服务条款归对应厂商所有。
