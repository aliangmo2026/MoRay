// MoRay 桌面端入口（Tauri 2）
// 原生能力：系统托盘 / 全局快捷键 / 窗口管理 / 文件读写 / 系统通知 / 自动更新 / Ollama 服务管理
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

// ============================================================
// Ollama 服务管理
// ============================================================

/// 检测本地 Ollama 是否安装（PATH 中可找到 ollama 可执行文件）
#[tauri::command]
fn detect_ollama() -> Result<bool, String> {
    // Windows: where ollama；Unix: which ollama
    let output = if cfg!(target_os = "windows") {
        std::process::Command::new("where").arg("ollama").output()
    } else {
        std::process::Command::new("which").arg("ollama").output()
    };
    match output {
        Ok(o) => Ok(o.status.success()),
        Err(e) => Err(e.to_string()),
    }
}

/// 启动 Ollama 服务（ollama serve， detached）
#[tauri::command]
fn start_ollama(app: tauri::AppHandle) -> Result<bool, String> {
    use tauri_plugin_shell::ShellExt;
    let shell = app.shell();
    let result = if cfg!(target_os = "windows") {
        shell.command("cmd").args(["/C", "start", "/B", "ollama", "serve"]).spawn()
    } else {
        shell.command("ollama").arg("serve").spawn()
    };
    match result {
        Ok(_) => Ok(true),
        Err(e) => Err(format!("启动 Ollama 失败: {e}")),
    }
}

/// 停止 Ollama 服务
#[tauri::command]
fn stop_ollama(app: tauri::AppHandle) -> Result<bool, String> {
    use tauri_plugin_shell::ShellExt;
    let shell = app.shell();
    let result = if cfg!(target_os = "windows") {
        shell.command("taskkill").args(["/F", "/IM", "ollama.exe"]).spawn()
    } else {
        shell.command("pkill").arg("ollama").spawn()
    };
    match result {
        Ok(_) => Ok(true),
        Err(e) => Err(format!("停止 Ollama 失败: {e}")),
    }
}

/// 端口冲突检测：检查 11434 是否已被占用
#[tauri::command]
fn check_port_11434() -> Result<bool, String> {
    // 尝试连接本地 11434，成功说明已有服务监听
    match std::net::TcpStream::connect("127.0.0.1:11434") {
        Ok(_) => Ok(true),  // 端口被占用（Ollama 已在运行）
        Err(_) => Ok(false), // 端口空闲
    }
}

// ============================================================
// [补全] 文件读写命令
// ============================================================

/// 读取文本文件内容
/// @param path 文件绝对路径
#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path).map_err(|e| format!("读取文件失败 [{path}]: {e}"))
}

/// 写入文本文件（覆盖）
/// @param path 文件绝对路径
/// @param content 文本内容
#[tauri::command]
fn write_text_file(path: String, content: String) -> Result<bool, String> {
    std::fs::write(&path, content.as_bytes())
        .map(|_| true)
        .map_err(|e| format!("写入文件失败 [{path}]: {e}"))
}

/// 追加文本到文件末尾
/// @param path 文件绝对路径
/// @param content 要追加的内容
#[tauri::command]
fn append_text_file(path: String, content: String) -> Result<bool, String> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开文件失败 [{path}]: {e}"))?;
    file.write_all(content.as_bytes())
        .map(|_| true)
        .map_err(|e| format!("追加文件失败 [{path}]: {e}"))
}

/// 判断文件/目录是否存在
#[tauri::command]
fn path_exists(path: String) -> bool {
    std::path::Path::new(&path).exists()
}

/// 创建目录（递归创建父目录）
#[tauri::command]
fn create_directory(path: String) -> Result<bool, String> {
    std::fs::create_dir_all(&path)
        .map(|_| true)
        .map_err(|e| format!("创建目录失败 [{path}]: {e}"))
}

/// 列出目录下的文件和子目录（返回 JSON 字符串数组）
/// @param path 目录路径
#[tauri::command]
fn list_directory(path: String) -> Result<Vec<FileEntry>, String> {
    let mut entries = Vec::new();
    let rd = std::fs::read_dir(&path).map_err(|e| format!("读取目录失败 [{path}]: {e}"))?;
    for entry in rd.flatten() {
        let meta = entry.metadata();
        let is_dir = meta.as_ref().map(|m| m.is_dir()).unwrap_or(false);
        let size = meta.as_ref().map(|m| m.len()).unwrap_or(0);
        let name = entry.file_name().to_string_lossy().to_string();
        entries.push(FileEntry {
            name,
            is_dir,
            size,
            path: entry.path().to_string_lossy().to_string(),
        });
    }
    // 目录在前、文件在后，各自按名称排序
    entries.sort_by(|a, b| {
        b.is_dir
            .cmp(&a.is_dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    Ok(entries)
}

/// 目录条目结构（返回给前端）
#[derive(serde::Serialize)]
struct FileEntry {
    name: String,
    is_dir: bool,
    size: u64,
    path: String,
}

/// 删除文件或空目录
#[tauri::command]
fn delete_path(path: String) -> Result<bool, String> {
    let p = std::path::Path::new(&path);
    if p.is_dir() {
        std::fs::remove_dir(&path)
    } else {
        std::fs::remove_file(&path)
    }
    .map(|_| true)
    .map_err(|e| format!("删除失败 [{path}]: {e}"))
}

// ============================================================
// [补全] 窗口管理命令
// ============================================================

/// 切换窗口置顶状态
/// @param pinned true=置顶 false=取消置顶
#[tauri::command]
fn toggle_always_on_top(app: tauri::AppHandle, pinned: bool) -> Result<bool, String> {
    let win = app
        .get_webview_window("main")
        .ok_or("找不到主窗口")?;
    win.set_always_on_top(pinned)
        .map(|_| pinned)
        .map_err(|e| format!("切换置顶失败: {e}"))
}

/// 切换窗口无边框/有边框（装饰）
/// @param borderless true=无边框 false=恢复边框
#[tauri::command]
fn toggle_borderless(app: tauri::AppHandle, borderless: bool) -> Result<bool, String> {
    let win = app
        .get_webview_window("main")
        .ok_or("找不到主窗口")?;
    // decorations(false) = 无边框
    win.set_decorations(!borderless)
        .map(|_| borderless)
        .map_err(|e| format!("切换边框失败: {e}"))
}

/// 最小化窗口
#[tauri::command]
fn minimize_window(app: tauri::AppHandle) -> Result<(), String> {
    let win = app.get_webview_window("main").ok_or("找不到主窗口")?;
    win.minimize().map_err(|e| format!("最小化失败: {e}"))
}

/// 最大化/还原切换
#[tauri::command]
fn toggle_maximize(app: tauri::AppHandle) -> Result<bool, String> {
    let win = app.get_webview_window("main").ok_or("找不到主窗口")?;
    if win.is_maximized().unwrap_or(false) {
        win.unmaximize().map_err(|e| format!("还原失败: {e}"))?;
        Ok(false)
    } else {
        win.maximize().map_err(|e| format!("最大化失败: {e}"))?;
        Ok(true)
    }
}

/// 设置窗口透明度（0.0 - 1.0）
#[tauri::command]
fn set_window_opacity(app: tauri::AppHandle, opacity: f64) -> Result<(), String> {
    let win = app.get_webview_window("main").ok_or("找不到主窗口")?;
    let v = opacity.clamp(0.3, 1.0);
    win.set_opacity(v).map_err(|e| format!("设置透明度失败: {e}"))
}

// ============================================================
// [补全] 系统通知命令
// ============================================================

/// 发送系统桌面通知
/// @param title 标题
/// @param body 正文
#[tauri::command]
fn send_system_notification(app: tauri::AppHandle, title: String, body: String) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;
    app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show()
        .map_err(|e| format!("发送通知失败: {e}"))
}

/// 获取应用版本号
#[tauri::command]
fn get_app_version(app: tauri::AppHandle) -> String {
    app.package_info().version.to_string()
}

// ============================================================
// 应用入口
// ============================================================

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_window_state::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            // 全局快捷键：Ctrl/Cmd+Shift+A 显示/隐藏主窗口
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state == tauri_plugin_global_shortcut::ShortcutState::Pressed {
                        if let Some(window) = app.get_webview_window("main") {
                            if window.is_visible().unwrap_or(false) {
                                let _ = window.hide();
                            } else {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                    }
                    let _ = shortcut;
                })
                .build(),
        )
        .setup(|app| {
            // 注册全局快捷键
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut};
                let shortcut: Shortcut = "CmdOrCtrl+Shift+A".parse().unwrap_or(
                    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyA),
                );
                app.global_shortcut().register(shortcut)?;
            }

            // 系统托盘
            let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let pin = MenuItem::with_id(app, "pin", "窗口置顶", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &pin, &quit])?;
            TrayIconBuilder::with_id("moray-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("MoRay — 本地AI开发者工作台")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "pin" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let cur = window.is_always_on_top().unwrap_or(false);
                            let _ = window.set_always_on_top(!cur);
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            // 启动时检测 Ollama，未运行则自动拉起
            std::thread::spawn(|| match check_port_11434() {
                Ok(false) => {
                    let _ = start_ollama_stub();
                }
                _ => {}
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // Ollama 管理
            detect_ollama,
            start_ollama,
            stop_ollama,
            check_port_11434,
            // 文件读写
            read_text_file,
            write_text_file,
            append_text_file,
            path_exists,
            create_directory,
            list_directory,
            delete_path,
            // 窗口管理
            toggle_always_on_top,
            toggle_borderless,
            minimize_window,
            toggle_maximize,
            set_window_opacity,
            // 系统通知与信息
            send_system_notification,
            get_app_version
        ])
        .run(tauri::generate_context!())
        .expect("error while running MoRay");
}

/// 后台拉起 Ollama 的桩函数（不阻塞启动）
fn start_ollama_stub() {
    let result = if cfg!(target_os = "windows") {
        std::process::Command::new("cmd")
            .args(["/C", "start", "/B", "ollama", "serve"])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn()
    } else {
        std::process::Command::new("ollama").arg("serve").spawn()
    };
    if let Err(e) = result {
        eprintln!("auto-start ollama failed: {e}");
    }
}

#[cfg(windows)]
trait CreationFlags {
    fn creation_flags(&mut self, flags: u32) -> &mut Self;
}

#[cfg(windows)]
impl CreationFlags for std::process::Command {
    fn creation_flags(&mut self, flags: u32) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(flags)
    }
}
