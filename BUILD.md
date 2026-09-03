# MoRay 构建指南（Tauri 2 桌面端）

## 前置要求

| 平台 | 依赖 |
|------|------|
| 全平台 | Rust stable（rustup）、Node.js 18+、`npm i -g @tauri-apps/cli` |
| Windows | WebView2 Runtime（Win11 自带；安装器已配置 embedBootstrapper 引导） |
| macOS | Xcode Command Line Tools（`xcode-select --install`） |
| Linux | `libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf` |

## 快速开始

```bash
# 开发模式（热重载）
cargo tauri dev

# 构建当前平台安装包（输出在 src-tauri/target/release/bundle/）
cargo tauri build
```

## 各平台构建

```bash
# Windows（.msi + NSIS .exe）
cargo tauri build --bundles msi nsis

# macOS（.dmg，需在 macOS 上执行）
cargo tauri build --bundles dmg

# Linux（.AppImage / .deb）
cargo tauri build --bundles appimage deb
```

也可使用仓库脚本：

```powershell
# Windows PowerShell
powershell -File scripts/build.ps1              # 当前平台默认
powershell -File scripts/build.ps1 msi nsis     # 指定格式
```

```bash
# macOS / Linux Shell
sh scripts/build.sh              # 当前平台默认
sh scripts/build.sh dmg          # 指定格式
```

## 图标替换

当前 `src-tauri/icons/` 为程序生成的占位图标，发布前替换：

```bash
cargo tauri icon path/to/app-icon.png   # 需 1024x1024 PNG
```

## 自动更新

`src-tauri/tauri.conf.json -> plugins.updater`：

1. `endpoints`：指向你的更新清单服务（含 `{{target}}/{{current_version}}` 模板）
2. `pubkey`：`cargo tauri signer generate -w ~/.tauri/moray.key` 生成的公钥

构建签名安装包：`cargo tauri build` 时设置环境变量 `TAURI_SIGNING_PRIVATE_KEY`（签名密钥路径）与 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。

## 常见问题

- **端口冲突**：应用启动时会检测 11434 端口（`check_port_11434`），Ollama 未运行时自动尝试拉起。
- **前端入口**：`tauri.conf.json -> build.frontendDist` 指向仓库根目录（`../`），修改 `moray-workbench.html` 后 `cargo tauri dev` 即时生效。
