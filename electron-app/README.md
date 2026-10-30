# MoRay 全生态工作台 - Electron 桌面端（方案 C · 混合）

只给现有 MoRay / Laya / Argus / Iris 网页**套一个原生窗口外壳**：
原有网页的界面、样式、功能 100% 原样显示，本程序不注入任何脚本或样式。
服务不在壳里逐个 spawn——启动时壳**委托 v36 一键启动器**
（`D:\MoRay\start_all_services.ps1`，幂等，7 服务全管）拉起全套。

## 一、功能

- 双击壳 → 无条件调用一次 v36 启动器（已在听端口自动跳过，缺失服务补齐；
  冷启动约 13.6s，服务全在时约 0.3s 即进工作台）
- 就绪判定：轮询 `GET http://127.0.0.1:8000/api/health`，30s 预算；
  超时后 splash 给「进入工作台 / 重试」两个选择
- 启动器输出全部留档 `D:\MoRay\logs\electron_launcher.log`
- 无边框自定义标题栏（拖动 / 最小化 / 最大化 / 关闭）
- Win11 磨砂玻璃 + 深色主题 + 品牌橙强调色（旧系统自动降级）
- 点 ✕ / 托盘「退出程序」弹三选对话框：
  - **完全退出**：按端口反查杀 6 个服务（8000/8001/8004/8005/8010/9301，
    不含 Ollama 11434）后退出壳
  - **保留后台自转**：服务继续跑（v36 拉起的是无主进程，壳退出不带走），只关壳
  - **取消**：什么都不做
  - 勾选「记住我的选择，不再询问」→ 落 `userData\shell-prefs.json`；
    改主意：托盘菜单「关窗时询问我」解除记忆
- 系统托盘常驻：打开工作台 / **最小化到托盘** / 开机自启（仅安装包可用）/
  关窗时询问我 / 退出程序；左键单击显示/隐藏；全局快捷键 `Alt+M`
- 侧边栏 4 个导航项带状态指示灯：绿=运行中、黄=启动中、红=失败
- 右侧多标签页，每页用 `<webview>` 原样加载对应网页；标签可关闭
- 侧边栏底部「重启全部服务」= 按端口停 6 服务 → 重调 v36 启动器

## 二、本地运行测试

日常入口：**双击 `D:\MoRay\MoRay桌面壳.lnk`**（指向本目录开发版 Electron，图标为应用图标）。

前置条件：已安装 Node.js 18+（https://nodejs.org ，LTS 版即可）。

```powershell
cd D:\MoRay\electron-app

# 1. 安装依赖（首次会下载 Electron，约 1~3 分钟）
npm install

# 2. 启动（开发模式）
npm start
```

说明：

- 启动后先显示启动遮罩，v36 启动器把 7 个服务拉齐（幂等，已跑的跳过），
  8000 就绪 + 其余标签页地址补探完成后自动进入主界面；
  若有失败，可点「进入工作台」查看，或点「重试」（= 重启全部服务）。
- 8005（ZCode 沙箱）现由 v36 以 `zcode_sandbox_server.py` 进程形态管理，
  壳内旧的 Docker 联动逻辑已整体拆除（收口到 v36 单一来源）。
- 若 PowerShell 提示执行策略问题，壳调用启动器时已自带
  `-ExecutionPolicy Bypass`，无需改系统策略；直接 `npm start` 则不涉及。

## 三、打包成 exe 安装包

```powershell
cd D:\MoRay\electron-app

# 1) 打包 NSIS 安装包（推荐，带安装向导、桌面快捷方式）
npm run dist
# 产物：D:\MoRay\electron-app\dist\MoRay Workbench Setup 1.0.0.exe

# 2) 或打包单文件免安装绿色版
npm run dist:portable
# 产物：D:\MoRay\electron-app\dist\MoRayWorkbench-portable-1.0.0.exe

# 3) 只解包不压缩（打包调试用）
npm run dist:dir
```

打包后的安装包行为：

- 安装时可选择安装目录，自动创建桌面 / 开始菜单快捷方式
- 托盘菜单「开机自启」在安装版中可用（开发模式下自动禁用）
- 卸载不会删除项目目录与日志

## 四、配置位置（后续修改）

所有路径、端口、参数都集中在 `main.js` 顶部：

- `LAUNCHER_PS1`：v36 一键启动器路径（壳只调用、不修改；服务清单以脚本为准）
- `KILL_PORTS`：「完全退出」按端口反查杀的 6 个端口（裁定：不含 Ollama 11434）
- `PAGES`：壳内标签页清单（id / 显示名 / 端口 / 健康路径 / webview 地址），
  只负责展示与健康探测，不再携带 spawn 配置
- `READY_TIMEOUT_MS`：8000 就绪预算（默认 30s = 冷启动 13.6s + 一倍余量）
- `LISTEN_HOST`：监听地址（默认 `127.0.0.1`）
- `LOG_DIR`：日志目录（启动器输出 → `electron_launcher.log`）
- `HOTKEY_TOGGLE`：全局快捷键（默认 `Alt+M`）

新增组件（例如 OMH / MemoryBridge）：先让 v36 启动器管起它的端口，
再在 `PAGES` 数组中按同样格式增加一项，侧边栏与标签页会自动出现，
无需改动渲染进程代码。

## 五、目录结构

```
electron-app/
├─ package.json          # 依赖与 electron-builder 打包配置
├─ main.js               # 主进程：委托 v36 拉服务 / 窗口 / 托盘 / 关窗三选框 / 按端口清理
├─ preload.js            # 渲染进程 IPC 通信桥
├─ assets/
│  ├─ tray.png           # 托盘图标
│  └─ icon-256.png       # 图标原图
├─ build/
│  └─ icon.ico           # 打包用应用图标
└─ src/
   ├─ index.html         # 外壳界面框架（标题栏 / 侧边栏 / 标签区 / 遮罩）
   ├─ style.css          # 深色磨砂样式
   └─ renderer.js        # 交互逻辑（导航 / 标签 / webview / 状态）
```
