# MoRay 桌面端（Tauri 2）

将 `moray-workbench.html` 打包为原生桌面应用。

## 前置要求

1. **Rust**（stable，含 cargo）：https://rustup.rs
2. **Node.js 18+**（用于 @tauri-apps/cli）
3. **Tauri CLI**：`npm i -g @tauri-apps/cli` 或 `cargo install tauri-cli`
4. 平台依赖：
   - Windows：WebView2（Win11 自带；低版本由安装器内嵌引导）
   - macOS：Xcode Command Line Tools
   - Linux：`libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf`

## 开发调试

```bash
# 在项目根目录（D:/ai工具台）执行
cargo tauri dev
```

## 构建安装包

```bash
# Windows：生成 .msi 与 NSIS .exe（位于 src-tauri/target/release/bundle/）
cargo tauri build

# 指定目标
cargo tauri build --bundles msi
cargo tauri build --bundles nsis

# macOS（在 macOS 上执行）
cargo tauri build --bundles dmg

# Linux
cargo tauri build --bundles appimage deb
```

## 替换正式图标

当前 `icons/` 内为程序生成的占位图标。替换为正式图标：

```bash
# 准备一张 1024x1024 的 app-icon.png，然后：
cargo tauri icon path/to/app-icon.png
```

## 已实现的原生能力

| 能力 | 说明 |
|------|------|
| 系统托盘 | 显示主窗口 / 退出 |
| 全局快捷键 | `Ctrl/Cmd+Shift+A` 显示/隐藏主窗口 |
| 窗口管理 | 记忆窗口状态（tauri-plugin-window-state），可配置置顶/无边框（tauri.conf.json） |
| 系统通知 | tauri-plugin-notification（前端经 `window.__TAURI__` 调用） |
| 文件系统 | tauri-plugin-fs（导入导出本地文件） |
| 自动更新 | tauri-plugin-updater（需在 tauri.conf.json 配置 endpoints 与 pubkey） |
| Ollama 集成 | 检测安装 / 启动 / 停止 / 11434 端口冲突检测（Rust commands） |

## 前端调用桌面命令示例

```js
// 在 moray-workbench.html 中（桌面端运行时 window.__TAURI__ 可用）
const { invoke } = window.__TAURI__.core;
const installed = await invoke('detect_ollama');        // 是否安装 Ollama
const running = await invoke('check_port_11434');      // 11434 是否被占用
await invoke('start_ollama');                          // 启动服务
await invoke('stop_ollama');                           // 停止服务
```

## 自动更新服务器

`tauri.conf.json -> plugins.updater` 中替换：

1. `endpoints`：指向你的静态更新清单（JSON），约定 `{{target}}/{{current_version}}` 模板
2. `pubkey`：`cargo tauri signer generate -w ~/.tauri/moray.key` 生成的公钥

## 已知限制

- 占位图标需替换后方可发布
- 更新服务器为占位配置，发布前必须配置真实 endpoint 与签名密钥
- `frontendDist: "../"` 直接引用 HTML 所在目录；如需严格产物隔离，可改为构建步骤把 HTML 复制到 `dist/`
