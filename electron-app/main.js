/* ============================================================
 * MoRay 全生态工作台 - Electron 主进程 (main.js)
 *
 * 桌面端形态（方案 C · 混合）2026-10-09：
 *   1) 壳不再自己 spawn 服务——启动时无条件调用一次
 *      D:\MoRay\start_all_services.ps1（v36 一键启动器，自带幂等，
 *      已在听端口跳过、没起的补齐；冷启动约 13.6s，幂等约 0.3s）
 *   2) 就绪判定：轮询 GET http://127.0.0.1:8000/api/health（30s 预算），
 *      其余标签页地址并行补探，供侧边栏状态灯展示
 *   3) 关壳三选对话框：完全退出（按端口反查杀 6 服务，不含 Ollama 11434）
 *      / 保留后台自转（壳退出、服务不动）/ 取消；
 *      勾"记住我的选择"后落 userData\shell-prefs.json，下次不再问；
 *      托盘菜单「关窗时询问我」可解除记忆
 *   4) 无边框磨砂窗口 + 系统托盘 + 全局快捷键 Alt+M + 单实例锁
 *
 * 核心原则：只给现有网页套原生窗口外壳，不修改任何原有前端页面；
 *           不修改 v36 启动器本身——只调用。
 * ============================================================ */

'use strict';

const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, dialog } = require('electron');
const { spawn, exec } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

/* ============================================================
 *  顶部配置区（所有路径 / 端口 / 参数都在这里，改这里即可）
 * ============================================================ */

// 后端服务统一监听地址（127.0.0.1 = 仅本机）
const LISTEN_HOST = '127.0.0.1';

// 一键启动器（v36，本文件只调用、不修改）
const LAUNCHER_PS1 = 'D:\\MoRay\\start_all_services.ps1';

// 壳与启动器的日志目录；启动器输出汇入 electron_launcher.log
const LOG_DIR = 'D:\\MoRay\\logs';
const LAUNCHER_LOG = path.join(LOG_DIR, 'electron_launcher.log');

// 就绪等待参数
const READY_TIMEOUT_MS = 30_000;    // 8000 就绪预算（冷启动 13.6s + 一倍余量）
const READY_INTERVAL_MS = 1_000;    // 健康轮询间隔
const EXTRA_PAGE_TIMEOUT_MS = 12_000; // 8000 就绪后，其余标签页地址的补探预算

// 全局快捷键：显示 / 隐藏主窗口
const HOTKEY_TOGGLE = 'Alt+M';

// Win11 磨砂玻璃材质（'acrylic' / 'mica'，false = 关闭）
const BG_MATERIAL = false;

// 调试开关：true = 启动时自动打开开发者工具（方案 C 定稿关闭，排查时再开）
const OPEN_DEVTOOLS = false;

// 「完全退出」时按端口反查杀掉的 6 个服务端口。
// 裁定（2026-10-09）：不含 Ollama 11434——Ollama 可能以用户自己的
// 桌面版/服务形态在跑，壳无权带走它。
const KILL_PORTS = [8000, 8001, 8004, 8005, 8010, 9301];

// 壳内的标签页清单（只管展示与健康探测；服务由 v36 启动器负责拉起）
//   id / name / port / healthPath / url —— url 即 webview 加载地址
const PAGES = [
  {
    id: 'moray',
    name: '中枢工作台',
    port: 8000,
    healthPath: '/api/health',
    url: 'http://127.0.0.1:8000/'
  },
  {
    id: 'laya',
    name: 'Laya 引擎',
    port: 8001,
    healthPath: '/health',
    // Laya 无独立 UI，展示 FastAPI 自带接口文档
    url: 'http://127.0.0.1:8001/docs'
  },
  {
    id: 'argus',
    name: 'Argus 情报',
    port: 8010,
    healthPath: '/api/detective/health',
    url: 'http://127.0.0.1:8010/'
  },
  // [2026-10-09 摘除] Iris 感官标签已删：8004/9301 为纯 API（只回 application/json），
  // webview 嵌入帧不渲染 JSON → 白屏；Iris 工作台本就内嵌于中枢(8000)，此标签冗余。
  // 侧栏状态灯与本标签是同一 DOM 项，拆不开——灯随标签一起摘除。
  // 注：不影响服务启停——v36 启动器与 KILL_PORTS(含 8004/9301)均不读本数组。
];

/* ============================================================
 *  运行时状态
 * ============================================================ */

// 每个标签页的当前状态：'starting' | 'running' | 'failed'
const statuses = {};
PAGES.forEach((p) => { statuses[p.id] = 'starting'; });

let mainWindow = null;
let tray = null;
let isQuitting = false;         // true = 已通过对话框决策，close 事件放行
let closeDialogBusy = false;    // 防止连点 ✕ 弹出多个对话框
let launcherChild = null;       // 当前在跑的 v36 启动器进程（防重复 spawn）

// 关窗偏好：{ closeBehavior: 'quit-all' | 'keep-bg' | null, remember: boolean }
const prefs = { closeBehavior: null, remember: false };

/* ============================================================
 *  关窗偏好持久化（userData\shell-prefs.json，内置 fs，无新依赖）
 * ============================================================ */

function prefsPath() {
  return path.join(app.getPath('userData'), 'shell-prefs.json');
}

function loadPrefs() {
  try {
    const j = JSON.parse(fs.readFileSync(prefsPath(), 'utf8'));
    if (j.closeBehavior === 'quit-all' || j.closeBehavior === 'keep-bg') {
      prefs.closeBehavior = j.closeBehavior;
    }
    prefs.remember = !!j.remember && !!prefs.closeBehavior;
  } catch (err) {
    // 文件不存在 / 损坏 → 默认每次询问
  }
}

function savePrefs() {
  try {
    fs.writeFileSync(prefsPath(), JSON.stringify(prefs, null, 2), 'utf8');
  } catch (err) {
    console.warn('[prefs] shell-prefs.json 写入失败：', err.message);
  }
}

/* ============================================================
 *  通用探测工具
 * ============================================================ */

/**
 * 对单个 URL 发 GET 请求，状态码 200 即成功
 */
function httpGetOnce(url, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

/**
 * 轮询等待某个标签页地址健康，超时返回 false
 */
function waitForHealth(page, timeoutMs) {
  const url = `http://${LISTEN_HOST}:${page.port}${page.healthPath}`;
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tick = async () => {
      if (await httpGetOnce(url, 2000)) {
        resolve(true);
        return;
      }
      if (Date.now() > deadline) {
        resolve(false);
        return;
      }
      setTimeout(tick, READY_INTERVAL_MS);
    };
    tick();
  });
}

/* ============================================================
 *  v36 一键启动器：调用面（不修改脚本本身）
 * ============================================================ */

/** 往 electron_launcher.log 追加一行壳侧记录 */
function launcherLog(msg) {
  try {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    fs.appendFileSync(LAUNCHER_LOG, `[${new Date().toISOString()}] [shell] ${msg}\r\n`);
  } catch (err) {
    console.warn('[launcher] 日志写入失败：', err.message);
  }
}

/**
 * 无条件调起一次 v36 启动器（脚本自带幂等：已在听端口跳过、缺失补齐）。
 * 关键点：
 *   - windowsHide:true —— 不闪 PowerShell 控制台窗
 *   - 不 detached —— 服务存活不依赖 ps1；ps1 跑完自己退出，
 *     它拉起的 7 个服务是无主进程，壳退出不影响（"保留后台自转"的根据）
 *   - 禁 shell:true —— 直调 powershell.exe，不经 cmd
 *   - stdio pipe 到 electron_launcher.log —— ps1 的状态表全部留档
 *   - 同一时刻只允许一个启动器在跑（launcherChild 守卫）
 */
function spawnLauncher() {
  if (launcherChild) {
    launcherLog('启动器已在运行，跳过重复 spawn');
    return;
  }
  if (!fs.existsSync(LAUNCHER_PS1)) {
    launcherLog(`找不到一键启动器：${LAUNCHER_PS1}`);
    return;
  }
  launcherLog(`==== spawn: powershell -File ${LAUNCHER_PS1} ====`);
  const stream = fs.createWriteStream(LAUNCHER_LOG, { flags: 'a' });
  const child = spawn('powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', LAUNCHER_PS1],
    {
      cwd: 'D:\\MoRay',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    });
  child.stdout.pipe(stream, { end: false });
  child.stderr.pipe(stream, { end: false });
  child.on('exit', (code) => {
    launcherLog(`启动器进程退出 code=${code}`);
    if (launcherChild === child) launcherChild = null;
    try { stream.end(`\r\n[${new Date().toISOString()}] [shell] launcher exit code=${code}\r\n`); } catch (err) {}
  });
  child.on('error', (err) => {
    launcherLog(`启动器 spawn 失败：${err.message}`);
    if (launcherChild === child) launcherChild = null;
    try { stream.end(); } catch (err2) {}
  });
  launcherChild = child;
}

/* ============================================================
 *  「完全退出」：按端口反查 PID -> taskkill /T /F
 * ============================================================ */

/**
 * netstat -ano 反查在指定端口 LISTENING 的全部 PID（去重，一个端口可能多进程——
 * 尤其 9301 的编排器链）。解析失败/无匹配一律返回空数组。
 */
function getPidsListeningOnPort(port) {
  return new Promise((resolve) => {
    exec('netstat -ano', { windowsHide: true, maxBuffer: 10 * 1024 * 1024 }, (err, stdout) => {
      if (err) { resolve([]); return; }
      const pids = new Set();
      for (const line of String(stdout).split(/\r?\n/)) {
        const m = line.trim().match(/^TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$/i);
        if (m && Number(m[1]) === port) pids.add(Number(m[2]));
      }
      resolve([...pids]);
    });
  });
}

/** 结束本次壳 spawn 的 v36 启动器进程树（它经 cmd 链拉起的服务也在树内） */
function killLauncherTree() {
  const child = launcherChild;
  if (!child || child.killed) return;
  try {
    spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
  } catch (err) {
    console.error('[launcher] taskkill 失败：', err.message);
  }
}

/**
 * 杀 6 个服务端口上的监听进程（KILL_PORTS，不含 Ollama 11434）。
 * 不依赖 children map：服务是 v36 拉起的无主进程，只能按端口找。
 */
async function killAllServicePorts() {
  killLauncherTree();
  for (const port of KILL_PORTS) {
    const pids = await getPidsListeningOnPort(port);
    for (const pid of pids) {
      await new Promise((resolve) => {
        exec(`taskkill /PID ${pid} /T /F`, { windowsHide: true }, () => resolve());
      });
    }
    if (pids.length) console.log(`[kill] 端口 ${port}: PID ${pids.join(', ')} -> taskkill /T /F`);
  }
  // 给端口释放留一点时间（重启服务前尤其需要）
  await new Promise((resolve) => setTimeout(resolve, 1000));
}

/* ============================================================
 *  启动编排：调起启动器 -> 等 8000 -> 补探其余标签页
 * ============================================================ */

function setStatus(page, status) {
  statuses[page.id] = status;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('service-status', { id: page.id, status });
  }
}

async function launchAll() {
  PAGES.forEach((p) => setStatus(p, 'starting'));
  spawnLauncher();

  const moray = PAGES.find((p) => p.id === 'moray');
  const ok = await waitForHealth(moray, READY_TIMEOUT_MS);
  setStatus(moray, ok ? 'running' : 'failed');
  launcherLog(`8000 ${ok ? '就绪' : '未在预算内就绪'}（预算 ${READY_TIMEOUT_MS / 1000}s）`);

  // 其余标签页并行补探（启动器按顺序拉起，此刻多数已就位）
  await Promise.all(PAGES.filter((p) => p.id !== 'moray').map(async (p) => {
    const healthy = await waitForHealth(p, EXTRA_PAGE_TIMEOUT_MS);
    setStatus(p, healthy ? 'running' : 'failed');
  }));
}

/* ============================================================
 *  关闭决策：对话框三选 + 记忆
 * ============================================================ */

/** 完全退出：杀 6 服务（不含 Ollama）后退出壳 */
async function doQuitAll() {
  isQuitting = true;
  await killAllServicePorts();
  app.quit();
}

/** 保留后台自转：服务不动（它们是无主进程，壳退出不带走），壳直接退出 */
function doQuitKeep() {
  isQuitting = true;
  app.quit();
}

/**
 * 关壳入口（标题栏 ✕ / 托盘「退出程序」共用同一条路）：
 *   - 已记住选择 → 直接执行，不弹框
 *   - 未记住   → 三选框（默认项 = 保留后台自转；取消 = 什么都不做）
 *   - 勾选"记住我的选择"且选了 0/1 → 写 shell-prefs.json
 */
async function handleShellClose() {
  if (isQuitting) return;

  if (prefs.remember && prefs.closeBehavior) {
    if (prefs.closeBehavior === 'quit-all') await doQuitAll();
    else doQuitKeep();
    return;
  }

  if (closeDialogBusy || !mainWindow || mainWindow.isDestroyed()) return;
  closeDialogBusy = true;
  try {
    const { response, checkboxChecked } = await dialog.showMessageBox(mainWindow, {
      type: 'question',
      title: '退出 MoRay',
      message: '要退出桌面端。七个本地服务怎么处理？',
      detail: '「完全退出」停掉除 Ollama 外的 6 个本地服务；「保留后台自转」则服务继续跑，只关壳。',
      buttons: ['完全退出（停止所有服务）', '保留后台自转（服务继续跑）', '取消'],
      defaultId: 1,
      cancelId: 2,
      checkboxLabel: '记住我的选择，不再询问',
      checkboxChecked: false,
      noLink: true
    });

    if (response === 0 || response === 1) {
      const behavior = response === 0 ? 'quit-all' : 'keep-bg';
      if (checkboxChecked) {
        prefs.closeBehavior = behavior;
        prefs.remember = true;
        savePrefs();
      }
      if (behavior === 'quit-all') await doQuitAll();
      else doQuitKeep();
    }
    // response === 2（取消）：什么都不做，壳不关
  } finally {
    closeDialogBusy = false;
  }
}

/* ============================================================
 *  窗口（无边框 + Win11 磨砂）
 * ============================================================ */

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    frame: false,                  // 无边框，使用自定义标题栏
    show: false,
    backgroundColor: '#f4f5f8',    // [UI 2.0 步骤7] 对齐浅色玻璃底：启动瞬间不闪黑
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,      // 上下文隔离（安全）
      nodeIntegration: false,      // 渲染进程不直接开 Node
      webviewTag: true,            // 启用 <webview> 原样嵌入现有网页
      spellcheck: false
    }
  });

  // Win11 磨砂玻璃材质（Electron 30+；非 Win11 或关闭时自动跳过）
  if (BG_MATERIAL) {
    try {
      mainWindow.setBackgroundMaterial(BG_MATERIAL);
    } catch (err) {
      console.log('当前系统不支持磨砂材质，已降级为普通深色窗口。');
    }
  }

  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));

  // 调试：启动时自动打开开发者工具（F12 可手动切换）
  if (OPEN_DEVTOOLS) {
    mainWindow.webContents.once('did-finish-load', () => {
      mainWindow.webContents.openDevTools({ mode: 'detach' });
    });
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // [壳修复 2026-10-10] 渲染进程崩了 → 置 null，让下次唤醒走重建分支
  mainWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[moray-shell] render-process-gone:', JSON.stringify(details));
    // [壳修复守卫 2026-10-10] 事件源必须是当前 mainWindow 的 webContents
    if (mainWindow && mainWindow.webContents !== _e.sender) {
      return;
    }
    // [壳修复补丁 2026-10-10] 崩溃即刻毁旧窗，防"死窗挂屏"
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.destroy();
    }
    mainWindow = null;
  });

  // 方案 C：点 ✕ 不再"悄悄隐藏到托盘"，弹三选对话框由用户决策；
  // 想隐藏窗口走托盘或 Alt+M。isQuitting 时放行，真正退出。
  mainWindow.on('close', (event) => {
    if (isQuitting) return;
    event.preventDefault();
    handleShellClose();
  });
}

/* ============================================================
 *  系统托盘
 * ============================================================ */

function createTray() {
  const iconPath = path.join(__dirname, 'assets', 'tray.png');
  let icon = nativeImage.createFromPath(iconPath);
  if (!icon.isEmpty()) {
    icon = icon.resize({ width: 16, height: 16 });
  }
  tray = new Tray(icon);
  tray.setToolTip('MoRay 全生态工作台');

  buildTrayMenu();

  // 左键单击：显示 / 隐藏
  tray.on('click', () => {
    toggleWindow();
  });
}

function buildTrayMenu() {
  // 开机自启仅在打包后可用（开发模式下指向的是 electron.exe，没意义）
  const openAtLogin = app.isPackaged ? app.getLoginItemSettings().openAtLogin : false;
  const menu = Menu.buildFromTemplate([
    { label: '打开工作台', click: () => showWindow() },
    {
      label: '最小化到托盘',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide();
      }
    },
    {
      label: '开机自启',
      type: 'checkbox',
      checked: openAtLogin,
      enabled: app.isPackaged,
      click: (item) => setAutoLaunch(item.checked)
    },
    { type: 'separator' },
    {
      // 改主意出口：解除"记住我的选择"，恢复每次关窗询问
      label: '关窗时询问我',
      type: 'checkbox',
      checked: !prefs.remember,
      click: () => {
        prefs.remember = false;
        savePrefs();
        buildTrayMenu();
      }
    },
    { label: '退出程序', click: () => handleShellClose() }
  ]);
  tray.setContextMenu(menu);
}

/**
 * 设置开机自启（NSIS 安装包，无更新框架，参数留空即可）
 */
function setAutoLaunch(enabled) {
  if (!app.isPackaged) return;
  app.setLoginItemSettings({
    openAtLogin: enabled,
    path: process.execPath,
    args: []
  });
  buildTrayMenu();
}

/* ============================================================
 *  窗口显示 / 隐藏
 * ============================================================ */

function showWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function toggleWindow() {
  if (mainWindow && mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    showWindow();
  }
}

/* ============================================================
 *  IPC：渲染进程（标题栏 / 侧边栏 / 重启按钮）
 * ============================================================ */

// 返回全部标签页配置与实时状态（渲染进程据此画侧边栏）
ipcMain.handle('app:get-services', () => {
  return PAGES.map((p) => ({
    id: p.id,
    name: p.name,
    port: p.port,
    url: p.url,
    status: statuses[p.id]
  }));
});

// 标题栏按钮
ipcMain.on('win:minimize', () => {
  if (mainWindow) mainWindow.minimize();
});
ipcMain.on('win:toggle-maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});
ipcMain.on('win:close', () => {
  // 走 'close' 事件 → 三选对话框
  if (mainWindow) mainWindow.close();
});

// 开机自启开关（渲染进程内也可扩展）
ipcMain.handle('app:set-autostart', (_event, enabled) => {
  setAutoLaunch(Boolean(enabled));
  return app.isPackaged ? app.getLoginItemSettings().openAtLogin : false;
});

// 侧边栏「重启全部服务」/ splash「重试」：按端口杀 6 服务 -> 重调 v36 启动器
ipcMain.handle('app:restart', async () => {
  await killAllServicePorts();
  await launchAll();
  return true;
});

/* ============================================================
 *  日志中心（读取 D:\MoRay\logs 下各服务的运行日志）
 *  说明：方案 C 后服务日志由 v36 启动器决定（多数服务无文件日志），
 *  本区保留原样：有 electron_<id>.*.log 就读得到，没有则显示为空。
 * ============================================================ */

// 各日志文件的已读偏移（key = `${svcId}:out` / `${svcId}:err`）
const logOffsets = {};
// 首次打开日志时最多回放多少字节（避免大文件一次性拉满）
const LOG_FIRST_READ_BYTES = 256 * 1024;

/**
 * 增量读取单个日志文件：返回自上次读取之后新增的内容。
 * 若文件被截断（大小比记录值小），自动从 0 重读。
 */
function readLogIncremental(filePath, key) {
  if (!fs.existsSync(filePath)) return '';
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch (err) {
    return '';
  }
  const total = stat.size;
  let start = logOffsets[key] || 0;
  if (start > total) start = 0;                       // 文件被重置/截断
  if (start === 0 && total > LOG_FIRST_READ_BYTES) {
    start = total - LOG_FIRST_READ_BYTES;             // 首屏只回放最近一段
  }
  if (total <= start) { logOffsets[key] = total; return ''; }

  const len = total - start;
  const fd = fs.openSync(filePath, 'r');
  const buf = Buffer.alloc(len);
  fs.readSync(fd, buf, 0, len, start);
  fs.closeSync(fd);
  logOffsets[key] = total;
  return buf.toString('utf8');
}

// 日志中心侧边栏列表
ipcMain.handle('logs:list', () => {
  return PAGES.map((p) => ({ id: p.id, name: p.name }));
});

// 增量读取某服务的 out + err 日志（渲染进程轮询调用）
ipcMain.handle('logs:read', (_event, svcId) => {
  if (!PAGES.find((p) => p.id === svcId)) return { text: '' };
  const out = readLogIncremental(path.join(LOG_DIR, `electron_${svcId}.out.log`), `${svcId}:out`);
  const err = readLogIncremental(path.join(LOG_DIR, `electron_${svcId}.err.log`), `${svcId}:err`);
  return { text: out + err };
});

/* ============================================================
 *  [L7.7 加性] Laya 调试面板：导出执行日志 / 结果到本地文件
 *  - 只暴露"另存为文本"一个能力，渲染进程（contextIsolation）不碰 fs
 * ============================================================ */
ipcMain.handle('laya:save', async (_event, text, defaultName) => {
  const safeName = String(defaultName || 'laya-log.txt').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120);
  const win = mainWindow || undefined;
  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    title: '导出 Laya 执行记录',
    defaultPath: path.join(app.getPath('downloads'), safeName),
    filters: [{ name: '文本文件', extensions: ['txt', 'log', 'md', 'json'] }]
  });
  if (canceled || !filePath) return { ok: false, error: 'cancelled' };
  try {
    fs.writeFileSync(filePath, String(text == null ? '' : text), 'utf8');
    return { ok: true, path: filePath };
  } catch (err) {
    return { ok: false, error: String((err && err.message) || err) };
  }
});

/* ============================================================
 *  应用生命周期
 * ============================================================ */

// 单实例锁：重复打开时只显示已有窗口
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // [壳修复 2026-10-10] 窗口对象已毁 → 置 null，showWindow() 走重建分支
    if (mainWindow && mainWindow.isDestroyed()) {
      mainWindow = null;
    }
    showWindow();
  });

  app.whenReady().then(() => {
    // [M4b 收尾 2026-10-02] 壳启动时清 persist:moray 分区 HTTP 缓存，
    // 保证 webview 加载时 HTML 走网络拿最新版，触发 SW 更新检查。
    // 仅清 HTTP disk cache，不动 SW CacheStorage / IndexedDB / LocalStorage。
    try {
      const { session } = require('electron');
      session.fromPartition('persist:moray').clearCache().then(
        () => console.log('[MoRay Shell] persist:moray HTTP cache cleared'),
        (e) => console.warn('[MoRay Shell] clearCache failed:', e)
      );
    } catch (e) {
      console.warn('[MoRay Shell] clearCache failed:', e);
    }

    loadPrefs();
    createWindow();
    createTray();

    // 注册全局快捷键 Alt+M
    const registered = globalShortcut.register(HOTKEY_TOGGLE, () => {
      toggleWindow();
    });
    if (!registered) {
      console.log(`全局快捷键 ${HOTKEY_TOGGLE} 注册失败（可能被其它程序占用）。`);
    }

    // 无条件调一次 v36 启动器（幂等）并等待就绪
    launchAll().catch((e) => console.error('[launchAll] 异常：', e));
  });

  // 关闭窗口不退出（托盘常驻）；真正退出口是标题栏 ✕ / 托盘「退出程序」
  // 走 handleShellClose 的三选决策
  app.on('window-all-closed', () => {
    // 不执行默认退出逻辑
  });

  // [方案 C] 这里不再做任何无条件进程清理：
  //   - "完全退出"的 kill 已在对话框分支里按端口完成（doQuitAll）
  //   - "保留后台自转"要求服务绝不被壳带走——旧版 before-quit / 崩溃兜底
  //     的无条件 taskkill 与之冲突，已全部移除
  app.on('will-quit', () => {
    globalShortcut.unregisterAll();
  });

  // [壳修复 2026-10-10] render-process-gone 已移入 createWindow 内按 webContents 挂监听：
  //   崩溃即置 mainWindow = null，下次唤醒（second-instance / 托盘 / Alt+M）走重建分支；
  //   崩溃仍只记日志、不杀服务（服务归 v36 / 端口世界管）
  app.on('child-process-gone', (_e, details) => {
    console.error('Electron 子进程 gone：', details.type, details.reason);
  });
  process.on('uncaughtException', (err) => {
    console.error('主进程未捕获异常：', err);
  });
}
