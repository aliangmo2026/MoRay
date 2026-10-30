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
 *
 * [方案2 2026-10-29] Harness 接管：
 *   默认窗口不再加载旧版工作台壳，而是由本进程 spawn dsh web、从输出里抓
 *   "dsh web: http://127.0.0.1:3080/?token=..." 的完整地址，直接 loadURL。
 *   token 每次 dsh 重启都会重生成，所以每次启动都重新抓（不缓存 token）。
 *   旧版工作台壳保留为逃生舱：MORAY_SHELL_LEGACY=1 / MORAY_HARNESS=0。
 * ============================================================ */

'use strict';

const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, dialog, shell } = require('electron');
const { spawn, exec, execFile } = require('child_process');
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

/* ============================================================
 *  [方案2 2026-10-29] Harness 前台：壳自己拉起 dsh web 并抓 token
 *
 *  - 默认：窗口直接加载 dsh web（Harness，MoRay 换皮版），旧版工作台壳退役
 *  - 逃生舱：MORAY_SHELL_LEGACY=1 或 MORAY_HARNESS=0 回到旧版工作台窗口
 *  - dsh 的输出 tee 到 HARNESS_LOG，token 每轮重启都会变，所以每轮都重新抓
 * ============================================================ */

const HARNESS_DISABLED = process.env.MORAY_HARNESS === '0';
const LEGACY_SHELL = HARNESS_DISABLED || process.env.MORAY_SHELL_LEGACY === '1';
const HARNESS_CWD = process.env.MORAY_HARNESS_CWD || 'D:\\moray-v2';
const HARNESS_PORT = 3080;
const HARNESS_LOG = path.join(HARNESS_CWD, 'logs', 'dsh-web.log');
const HARNESS_BOOT_TIMEOUT_MS = 60_000;   // 机器忙/冷启动时 dsh 可能 >30s，给足预算
const HARNESS_TOKEN_RE = /(http:\/\/127\.0\.0\.1:3080\/\?token=[A-Za-z0-9_-]+)/;
const SPLASH_FILE = path.join(__dirname, 'src', 'harness-splash.html');

// [2026-10-30] 固定 dsh 入口：只认这个版本，不再经 npx 解析 dist-tag。
// 裸名 latest 解析到更旧的 0.2.1-rc.5，必须钉死 alpha 对应的 0.2.2-alpha.2。
const DSH_ENTRY_VERSION = '0.2.2-alpha.2';
// 自有 runtime：优先级高于 npm 缓存（缓存目录可能被 npm 回收）
const DSH_RUNTIME_DIR = path.join(HARNESS_CWD, 'runtime', 'dsh');
const DSH_RECOVERY_PREFIX = DSH_RUNTIME_DIR;
// 首选 spawn 的 node 绝对路径：字面量直用，保证日志里写的路径就是真正执行的那条
const NODE_EXE_ABSOLUTE = 'C:\\Program Files\\nodejs\\node.exe';
// [2026-10-30] MoRay 专用 dsh home：与原版 DeepSeek Harness 的会话/凭据/配置分家。
// 只写进子进程环境，不做系统级设置；删掉 childEnv 里那行即回滚。
const DSH_HOME_DIR = 'D:\\moray-home';

/* ============================================================
 *  [2026-10-30 沉浸式无边框] Harness 页面注入层
 *  - 拖拽区：主列 <header>（dsh 顶栏）+ 侧栏落款行（*_logoRow——CSS module
 *    哈希前缀会随构建变、_logoRow 后缀稳定，故用 [class*=] 匹配）
 *  - 顶栏内所有可交互元素 no-drag，防"点按钮变成拖窗口"
 *  - 右上角注入 _ □ × 三钮（#moray-win-controls），经 preload-harness.js
 *    桥到 win:minimize / win:toggle-maximize / win:close（三选对话框照走）
 *  - header 右侧预留 152px 内边距给控制条（原 28px，右上按钮簇整体左移）
 *  - [2026-10-30 重叠修复] 右侧 tab 面板顶栏（_tabHostHeader_*，非 <header> 标签，
 *    吃不到上面的 header padding）在分栏视图下，其"分栏/全屏/收起右侧边栏"按钮簇
 *    与右上角控制条物理重叠。补一条 [class*="tabHostHeader"] padding-right，
 *    让右侧所有面板顶栏统一给控制条让位（实测 132px 后最右按钮右缘 1142 < 控制条
 *    左界 1154，完全避开；tab 标题/关闭钮不受影响）。
 *  选择器与几何实测于 1280×720（header 76px 高、最右按钮 x=1268），
 *  dsh 前端大改版时需重新探针复核。
 * ============================================================ */
const FRAME_CSS = `
header { -webkit-app-region: drag; }
[class*="logoRow"] { -webkit-app-region: drag; }
header button, header a, header input, header select, header textarea, header label,
header [role="button"], header [role="tab"], header [role="combobox"],
header [role="menuitem"], header [role="tablist"] { -webkit-app-region: no-drag; }
[class*="logoRow"] button, [class*="logoRow"] a, [class*="logoRow"] input,
[class*="logoRow"] [role="button"] { -webkit-app-region: no-drag; }
header { padding-right: 152px !important; }
/* 右侧 tab 面板顶栏（分栏/全屏/收起按钮簇）给右上角控制条让位；tab 标题区不受影响。 */
[class*="tabHostHeader"] { padding-right: 132px !important; }
#moray-win-controls { position: fixed; top: 0; right: 0; width: 126px; height: 40px;
  display: flex; align-items: stretch; z-index: 2147483000; -webkit-app-region: no-drag;
  font-family: "Segoe UI", system-ui, sans-serif; }
#moray-win-controls button { flex: 1 1 0; min-width: 0; border: 0; margin: 0; padding: 0;
  border-radius: 0; background: transparent; color: #9aa3b5; cursor: default;
  display: flex; align-items: center; justify-content: center; -webkit-app-region: no-drag; }
#moray-win-controls button svg { width: 13px; height: 13px; display: block; }
#moray-win-controls button:hover { background: rgba(50, 114, 237, 0.22); color: #e8ecf6; }
#moray-win-controls button[data-act="close"]:hover { background: #e5484d; color: #fff; }
`;
const FRAME_JS = `(function(){
  if (window.__morayFrameControls) return { already: true };
  window.__morayFrameControls = true;
  var bar = document.createElement('div');
  bar.id = 'moray-win-controls';
  var mk = function (act, label, d) {
    var b = document.createElement('button');
    b.setAttribute('data-act', act);
    b.setAttribute('title', label);
    b.setAttribute('aria-label', label);
    b.innerHTML = '<svg viewBox="0 0 12 12" fill="none" aria-hidden="true">' +
      '<path d="' + d + '" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    return b;
  };
  bar.appendChild(mk('minimize', '最小化', 'M1.5 6h9'));
  bar.appendChild(mk('maximize', '最大化 / 还原', 'M1.8 1.8h8.4v8.4H1.8z'));
  bar.appendChild(mk('close', '关闭', 'M2 2l8 8M10 2l-8 8'));
  bar.addEventListener('click', function (e) {
    var b = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!b || !window.morayWindow) return;
    var act = b.getAttribute('data-act');
    if (act === 'minimize') window.morayWindow.minimize();
    else if (act === 'maximize') window.morayWindow.toggleMaximize();
    else if (act === 'close') window.morayWindow.close();
  });
  document.body.appendChild(bar);
  return { injected: true, buttons: bar.children.length };
})()`;

// 注入拖拽区与窗口控制（仅对 3080 页面生效；splash 是 file: 自动跳过）。
// did-finish-load 每次触发都会先撤旧 CSS 再重注，F5 刷新后依然在。
let frameCssKey = null;
async function applyFrameOverlay() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const wc = mainWindow.webContents;
  const url = wc.getURL() || '';
  if (!url.startsWith('http://127.0.0.1:3080')) return;
  try {
    if (frameCssKey) {
      try { await wc.removeInsertedCSS(frameCssKey); } catch (err) { /* 旧页已导航时忽略 */ }
      frameCssKey = null;
    }
    frameCssKey = await wc.insertCSS(FRAME_CSS);
    const r = await wc.executeJavaScript(FRAME_JS, true);
    harnessLog(`frame overlay injected ${JSON.stringify(r)}`);
  } catch (err) {
    harnessLog(`frame overlay 注入失败：${err.message}`);
  }
}

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
  // [2026-10-23 摘除] Laya 引擎标签已删：中枢工作台(8000)已是唯一入口，底层引擎
  // 在聊天框切换（多引擎热插拔），侧栏独立切页按钮冗余。Laya 8001 服务照常由
  // v36 启动器拉起——KILL_PORTS 含 8001，且 v36 与 KILL_PORTS 均不读本数组。
  // {
  //   id: 'laya',
  //   name: 'Laya 引擎',
  //   port: 8001,
  //   healthPath: '/health',
  //   // Laya 无独立 UI，展示 FastAPI 自带接口文档
  //   url: 'http://127.0.0.1:8001/docs'
  // },
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
 *  Harness（dsh web）生命周期：拉起 -> 抓 token -> 交给窗口
 * ============================================================ */

let dshChild = null;   // 壳自己拉起的 dsh 进程（node -> dsh/lib/bin.js）
// 入口解析失败时的可读原因：splash 文案与对话框共用，避免白屏
let harnessBootError = null;

function harnessLog(msg) {
  launcherLog('[harness] ' + msg);
}

/** 用参数数组结束进程树，完全不经过 shell */
function taskkillTree(pid) {
  return new Promise((resolve) => {
    execFile('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true }, () => resolve());
  });
}

/** 结束监听某端口的进程树，并等待端口释放 */
async function killPort(port) {
  const pids = await getPidsListeningOnPort(port);
  for (const pid of pids) await taskkillTree(pid);
  if (pids.length) {
    harnessLog(`端口 ${port}: PID ${pids.join(', ')} -> taskkill /T /F`);
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

/** 停掉壳自己拉起的 dsh 进程树 + 3080 上的监听者 */
async function stopHarness() {
  const child = dshChild;
  dshChild = null;
  if (child && !child.killed) await taskkillTree(child.pid);
  await killPort(HARNESS_PORT);
}

/**
 * 解析 node.exe 供日志参考：只做存在性检查，不参与 spawn 命令拼接。
 * 实际 spawn 用字面量 node.exe 走 PATH（显式前置标准安装目录，见 spawnHarness）。
 */
function describeNodeRuntime() {
  const candidates = [
    'C:\\Program Files\\nodejs\\node.exe',
    'C:\\Program Files (x86)\\nodejs\\node.exe'
  ];
  for (const candidate of candidates) {
    try { if (fs.existsSync(candidate)) return candidate; } catch (err) {}
  }
  return 'node.exe (PATH)';
}

/** MORAY_DSH_ENTRY 覆盖入口（env 读取单独成函数，路径处理不碰 env） */
function dshOverridePath() {
  const raw = process.env.MORAY_DSH_ENTRY;
  if (!raw) return null;
  try { return path.resolve(raw); } catch (err) { return null; }
}

/** %LOCALAPPDATA%\npm-cache\_npx 的规范绝对路径（env 读取单独成函数） */
function dshCacheRoot() {
  const localAppData = process.env.LOCALAPPDATA
    || path.join(process.env.USERPROFILE || '', 'AppData', 'Local');
  return path.resolve(localAppData, 'npm-cache', '_npx');
}

/**
 * 把 base + 若干段拼成绝对路径，校验结果仍以 base 为前缀（禁止 .. 逃逸）。
 * 越界立即返回 null，绝不把越界路径交给调用方。
 */
function containPath(base, ...segments) {
  const root = path.resolve(base);
  const target = path.normalize(path.join(root, ...segments));
  if (!target.startsWith(root + path.sep)) return null;
  return target;
}

/** dsh 包目录必须是 <某个哈希目录>/node_modules/@deepseek-ai/dsh 这种规范形状 */
function isDshPackageDir(dir) {
  const marker = path.join('node_modules', '@deepseek-ai', 'dsh');
  return path.resolve(dir).endsWith(path.sep + marker);
}

/**
 * 解析固定的 dsh 入口（lib/bin.js 绝对路径），全程不碰 npx / dist-tag / 网络：
 *   a. MORAY_DSH_ENTRY 指定绝对路径 -> 校验形状后直接采用
 *   b. 扫 %LOCALAPPDATA%\npm-cache\_npx\*\node_modules\@deepseek-ai\dsh，
 *      只接受 version === DSH_ENTRY_VERSION；多个命中取 package.json mtime 最新的
 *   c. 一个都没命中 -> null（调用方拒绝启动，不再有任何回退）
 * @returns {{ bin: string, version: string, source: string } | null}
 */
function resolveDshEntry() {
  const override = dshOverridePath();
  if (override) {
    // 只接受常规 npm 包入口 <pkg>/lib/bin.js，拒绝含 .. 段或非常规层级的外部输入
    const parts = override.split(path.sep);
    if (!path.isAbsolute(override) || parts.includes('..')
        || path.basename(override) !== 'bin.js' || path.basename(path.dirname(override)) !== 'lib'
        || !isDshPackageDir(path.dirname(path.dirname(override)))) {
      harnessLog(`MORAY_DSH_ENTRY 不是合法的 dsh 入口（需 <pkg>/lib/bin.js）：${override}`);
      return null;
    }
    // 覆盖入口直接采用，版本只作日志参考（读不到记 unknown）
    const overridePkgDir = path.resolve(path.dirname(path.dirname(override)));
    let version = 'unknown';
    const overridePkgJson = path.normalize(path.join(overridePkgDir, 'package.json'));
    if (overridePkgJson.startsWith(overridePkgDir + path.sep)) {
      try {
        const pkg = JSON.parse(fs.readFileSync(overridePkgJson, 'utf8'));
        if (typeof pkg.version === 'string') version = pkg.version;
      } catch (err) { /* 版本读不到不影响启动 */ }
    }
    harnessLog(`dsh 入口：MORAY_DSH_ENTRY=${override} version=${version}`);
    return { bin: override, version, source: 'MORAY_DSH_ENTRY' };
  }

  // b. 自有 runtime：<HARNESS_CWD>\runtime\dsh\node_modules\@deepseek-ai\dsh
  const runtimePkgDir = containPath(DSH_RUNTIME_DIR, 'node_modules', '@deepseek-ai', 'dsh');
  if (runtimePkgDir) {
    const runtimeBin = containPath(runtimePkgDir, 'lib', 'bin.js');
    const runtimePkgJson = path.normalize(path.join(runtimePkgDir, 'package.json'));
    let runtimeVersion = null;
    if (runtimeBin && fs.existsSync(runtimeBin) && runtimePkgJson.startsWith(runtimePkgDir + path.sep)) {
      try {
        const pkg = JSON.parse(fs.readFileSync(runtimePkgJson, 'utf8'));
        if (typeof pkg.version === 'string') runtimeVersion = pkg.version;
      } catch (err) { runtimeVersion = null; }
    }
    if (runtimeVersion === DSH_ENTRY_VERSION) {
      harnessLog(`dsh 入口：runtime=${runtimeBin} version=${runtimeVersion}`);
      return { bin: runtimeBin, version: runtimeVersion, source: 'runtime' };
    }
    harnessLog(`dsh 入口：runtime 不可用（version=${runtimeVersion || '缺失'}，需要 ${DSH_ENTRY_VERSION}），回退 _npx`);
  }

  // c. 最后兜底：npm 缓存里的 _npx（保留现有逻辑，不做删除）
  const npxRoot = dshCacheRoot();
  let hashDirs = [];
  try {
    hashDirs = fs.readdirSync(npxRoot, { withFileTypes: true }).filter((d) => d.isDirectory());
  } catch (err) {
    harnessLog(`npx 缓存目录不可读：${npxRoot}（${err.message}）`);
    return null;
  }

  const seen = [];
  const matched = [];
  for (const dir of hashDirs) {
    // 目录名先过白名单：只允许 npx 生成的十六进制哈希形状，杜绝 .. / 分隔符注入
    if (!/^[0-9a-f]{16}$/.test(dir.name)) continue;
    const pkgDir = containPath(npxRoot, dir.name, 'node_modules', '@deepseek-ai', 'dsh');
    if (!pkgDir || !fs.existsSync(pkgDir) || !isDshPackageDir(pkgDir)) continue;
    const bin = containPath(pkgDir, 'lib', 'bin.js');
    if (!bin) continue;
    const pkgJson = path.normalize(path.join(pkgDir, 'package.json'));
    if (!pkgJson.startsWith(pkgDir + path.sep)) continue;
    let version = null;
    let mtime = 0;
    try {
      const pkg = JSON.parse(fs.readFileSync(pkgJson, 'utf8'));
      if (typeof pkg.version === 'string') version = pkg.version;
      mtime = fs.statSync(pkgJson).mtimeMs;
    } catch (err) {
      continue;   // 包清单读不出 -> 这个候选不可用
    }
    const candidate = { bin, version, mtime, hash: dir.name, hasBin: fs.existsSync(bin) };
    seen.push(candidate);
    if (candidate.hasBin && version === DSH_ENTRY_VERSION) matched.push(candidate);
  }
  harnessLog(`dsh 入口扫描：${seen.length
    ? seen.map((c) => `${c.hash}=${c.version || '?'}${c.hasBin ? '' : '(缺 bin.js)'}`).join(', ')
    : '无'}`);
  if (!matched.length) {
    harnessLog(`dsh 入口：没有 version=${DSH_ENTRY_VERSION} 的可用候选`);
    return null;
  }
  matched.sort((a, b) => b.mtime - a.mtime);
  harnessLog(`dsh 入口候选 ${matched.length} 个：${matched.map((c) => c.bin).join(' | ')}`);
  const best = matched[0];
  harnessLog(`dsh 入口选中：${best.bin} version=${best.version}（_npx/${best.hash}）`);
  return { bin: best.bin, version: best.version, source: `_npx/${best.hash}` };
}

/**
 * 启动 dsh web，监听 stdout/stderr 抓首行带 token 的地址。
 * 输出同时 tee 到 HARNESS_LOG，壳外排查只看一个文件。
 * @returns {Promise<string|null>} token URL；超时/提前退出返回 null
 */
function spawnHarness() {
  return new Promise((resolve) => {
    if (!fs.existsSync(HARNESS_CWD)) {
      harnessLog(`Harness 工作区不存在：${HARNESS_CWD}`);
      resolve(null);
      return;
    }
    const entry = resolveDshEntry();
    if (!entry) {
      const recovery = `npm i @deepseek-ai/dsh@${DSH_ENTRY_VERSION} --prefix "${DSH_RECOVERY_PREFIX}"`;
      harnessBootError = {
        message: `找不到 dsh@${DSH_ENTRY_VERSION}，已拒绝启动（不再经 npx 下载）。`,
        detail: `恢复命令：${recovery}\n`
          + `然后设 MORAY_DSH_ENTRY=<目录>\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js 指向入口\n`
          + `日志：${HARNESS_LOG}`
      };
      harnessLog(harnessBootError.message);
      harnessLog(`找不到 dsh@${DSH_ENTRY_VERSION}，恢复命令：${recovery}`);
      resolve(null);
      return;
    }
    harnessBootError = null;
    try { fs.mkdirSync(path.dirname(HARNESS_LOG), { recursive: true }); } catch (err) {}
    const stream = fs.createWriteStream(HARNESS_LOG, { flags: 'a' });
    // 启动前断言绝对路径存在；否则回退 PATH 解析，并把"回退"写进日志
    const nodeAbsoluteExists = fs.existsSync(NODE_EXE_ABSOLUTE);
    const nodeCommandLabel = nodeAbsoluteExists
      ? NODE_EXE_ABSOLUTE
      : `node.exe（回退：${NODE_EXE_ABSOLUTE} 不存在，走 PATH 解析）`;
    const spawnLine = `==== spawn: ${nodeCommandLabel} ${entry.bin} (dsh@${entry.version}, ${entry.source}) web --no-open ====`;
    harnessLog(spawnLine);
    harnessLog(`node command (actual spawn): ${nodeCommandLabel}`);
    harnessLog(`node runtime probe (reference only, not used): ${describeNodeRuntime()}`);
    // 同一行也落进 dsh-web.log：壳外只看一个文件就能确认本次用的入口与版本
    stream.write(`[moray-shell] ${spawnLine}\n`);

    let settled = false;
    let carry = '';
    const finish = (url) => {
      if (settled) return;
      settled = true;
      resolve(url);
    };

    const childEnv = Object.assign({}, process.env, {
      // MoRay 网关只校验这个占位凭据存在；真实云端 key 只在 server/.env。
      // 允许用户用自己的环境变量覆盖，源码不落任何真实凭据。
      MORAY_LOCAL_KEY: process.env.MORAY_LOCAL_KEY || 'moray-local',
      // 遥测进程级总闸（cordis.patch.yml 已拔根，此处双保险）。
      DSH_TELEMETRY_DISABLED: '1',
      // 命令名用字面量 node.exe（dsh 要求 node>=24.9，Electron 内置 Node 20 不能用），
      // 这里显式前置标准安装目录，防 Explorer 启动时 PATH 被裁剪导致解析不到。
      PATH: 'C:\\Program Files\\nodejs;' + (process.env.PATH || ''),
      // [2026-10-30] 数据分家：本实例只读写 D:\moray-home，原版 Harness 继续用 ~/.dsh。
      DSH_HOME: DSH_HOME_DIR
    });
    // 日志取 childEnv 实际值：按回滚步骤删掉上面这行后，这里会如实打印 unset
    harnessLog(`DSH_HOME=${childEnv.DSH_HOME || '(unset)'} home=${childEnv.DSH_HOME ? path.resolve(childEnv.DSH_HOME) : '(default ~/.dsh)'}`);

    const spawnArgs = [entry.bin, 'web', '--no-open'];
    const spawnOpts = {
      cwd: HARNESS_CWD,
      env: childEnv,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    };
    // 命令名用字面量：nodeAbsoluteExists 只决定走哪个分支，日志与执行严格同源
    const child = nodeAbsoluteExists
      ? spawn('C:\\Program Files\\nodejs\\node.exe', spawnArgs, spawnOpts)
      : spawn('node.exe', spawnArgs, spawnOpts);
    dshChild = child;

    const scan = (chunk) => {
      const text = chunk.toString('utf8');
      stream.write(text);
      const matched = (carry + text).match(HARNESS_TOKEN_RE);
      carry = (carry + text).slice(-512);   // token 行可能被 chunk 切断
      if (!matched) return;
      // 只接受本机 Harness 地址：协议/主机/端口/token 全部校验后再规范化为新 URL，
      // 绝不把子进程输出原样当导航目标。
      let target = null;
      try {
        const parsed = new URL(matched[1]);
        const token = parsed.searchParams.get('token');
        if (parsed.protocol === 'http:' && parsed.hostname === '127.0.0.1'
            && parsed.port === String(HARNESS_PORT) && token) {
          target = parsed.toString();
        }
      } catch (err) {
        target = null;
      }
      if (!target) return;
      if (!settled) {
        harnessLog('token URL captured');
        finish(target);
      } else if (mainWindow && !mainWindow.isDestroyed()
          && mainWindow.webContents.getURL().startsWith('file:')) {
        // [2026-10-29] 超时后 token 才到：窗口还停在 splash 就自动切过去。
        harnessLog('token URL captured (late)');
        mainWindow.loadURL(target).catch((err) => harnessLog(`迟到 token 加载失败：${err.message}`));
      }
    };
    child.stdout.on('data', scan);
    child.stderr.on('data', scan);
    child.on('exit', (code) => {
      harnessLog(`dsh 进程退出 code=${code}`);
      if (dshChild === child) dshChild = null;
      try { stream.end(); } catch (err) {}
      finish(null);
    });
    child.on('error', (err) => {
      harnessLog(`dsh spawn 失败：${err.message}`);
      if (dshChild === child) dshChild = null;
      try { stream.end(); } catch (err2) {}
      finish(null);
    });
    setTimeout(() => {
      if (!settled) harnessLog(`token 抓取超时（${HARNESS_BOOT_TIMEOUT_MS / 1000}s）`);
      finish(null);
    }, HARNESS_BOOT_TIMEOUT_MS);
  });
}

/** 杀掉旧实例 -> 重新拉起 -> 返回 token URL（null = 失败） */
async function ensureHarnessReady() {
  if (LEGACY_SHELL) return null;
  await stopHarness();
  return spawnHarness();
}

/** 主窗口 Harness 启动流程：splash 已显示，抓到地址后整页切过去 */
async function bootHarnessWindow() {
  const url = await ensureHarnessReady();
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (url) {
    try {
      await mainWindow.loadURL(url);
      harnessLog('Harness 已加载');
      // 一行 DPI/视口诊断：高分屏缩放异常时先看这里
      try {
        const diag = await mainWindow.webContents.executeJavaScript(
          '({innerWidth:window.innerWidth, dpr:window.devicePixelRatio, scrollWidth:document.documentElement.scrollWidth})'
        );
        harnessLog(`viewport ${JSON.stringify(diag)} content ${JSON.stringify(mainWindow.getContentBounds())} zoom ${mainWindow.webContents.getZoomFactor()}`);
      } catch (err) { /* 诊断失败不影响主流程 */ }
      return;
    } catch (err) {
      harnessLog(`Harness loadURL 失败：${err.message}`);
    }
  }
  await showHarnessFallback();
}

/** 兜底：留在 splash 上给出可读原因（入口缺失 / 超时），并弹窗提示 */
async function showHarnessFallback() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const bootError = harnessBootError;
  const statusText = bootError ? bootError.message : '没有拿到访问令牌，请查看日志：' + HARNESS_LOG;
  const hintText = bootError ? bootError.detail : '';
  try {
    await mainWindow.webContents.executeJavaScript(
      "(function(){var s=document.getElementById('splash-status');" +
      'if(s){s.textContent=' + JSON.stringify(statusText) + ';}' +
      "var h=document.getElementById('splash-hint');if(h){h.hidden=false;" +
      'h.textContent=' + JSON.stringify(hintText) + ';}})()'
    );
  } catch (err) { /* splash 未就绪时忽略 */ }
  dialog.showMessageBox(mainWindow, {
    type: 'warning',
    title: bootError ? 'dsh 入口未找到' : 'Harness 未就绪',
    message: bootError ? bootError.message : '没有在限定时间内拿到 dsh web 的访问令牌。',
    detail: bootError ? bootError.detail
      : `日志：${HARNESS_LOG}\n手动取出 "dsh web: http://127.0.0.1:3080/?token=..." 那一行，\n或按 F5 重试（会重新拉起 dsh）。`,
    buttons: ['知道了']
  }).then(
    (r) => harnessLog(`fallback 对话框关闭：${JSON.stringify(r)}`),
    (err) => harnessLog(`fallback 对话框失败：${(err && err.message) || err}`)
  );
}

/** 托盘「重启 Harness」：重抓 token 后原地刷新 */
async function restartHarness() {
  const url = await ensureHarnessReady();
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (url) {
    try { await mainWindow.loadURL(url); } catch (err) { harnessLog(`重启后加载失败：${err.message}`); }
  } else {
    await showHarnessFallback();
  }
}

/** 托盘「重启全部服务」：停 6 服务 + Harness -> 并行拉起 -> 加载新 token 页 */
async function restartEverything() {
  await killAllServicePorts();
  if (LEGACY_SHELL) {
    await launchAll();
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.reloadIgnoringCache();
    return;
  }
  const launch = launchAll();          // 8000 与 dsh 并行：dsh 的模型目录来自配置声明，不依赖 8000 先就绪
  const url = await ensureHarnessReady();
  await launch;
  if (mainWindow && !mainWindow.isDestroyed() && url) {
    try { await mainWindow.loadURL(url); } catch (err) { harnessLog(`重载失败：${err.message}`); }
  }
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

/** 完全退出：杀 6 服务（不含 Ollama）+ dsh，然后退出壳 */
async function doQuitAll() {
  isQuitting = true;
  await stopHarness();
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
      message: '要退出桌面端。本地服务与 Harness 怎么处理？',
      detail: '「完全退出」停掉除 Ollama 外的 6 个本地服务和 Harness；「保留后台自转」则它们继续跑，只关壳。',
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
    // [2026-10-30 品牌换装] 赛博鳐鱼 Logo：窗口/任务栏/Alt-Tab 图标。
    // PNG 256 由 assets/render 管线（Edge headless 栅格化 moray-logo.svg）生成；
    // 安装包 exe 图标另见 build/icon.ico（同源四档 ICO）。
    icon: path.join(__dirname, 'assets', 'icon-256.png'),
    // [2026-10-30 沉浸式] Harness 模式隐藏原生标题栏：titleBarStyle 'hidden'
    // 保留 DWM 阴影/圆角/边缘缩放；拖拽区与最小/最大/关闭按钮由
    // did-finish-load 注入层接管（FRAME_CSS + FRAME_JS + preload-harness.js）。
    frame: LEGACY_SHELL ? false : true,
    titleBarStyle: LEGACY_SHELL ? 'default' : 'hidden',
    title: 'MoRay',
    show: false,
    backgroundColor: LEGACY_SHELL ? '#f4f5f8' : '#0a0a0a',
    webPreferences: LEGACY_SHELL
      ? {
          preload: path.join(__dirname, 'preload.js'),
          contextIsolation: true,      // 上下文隔离（安全）
          nodeIntegration: false,      // 渲染进程不直接开 Node
          webviewTag: true,            // 启用 <webview> 原样嵌入现有网页
          spellcheck: false
        }
      : {
          // Harness 是普通网页：不开 Node、不开 webview；preload 只桥接
          // 窗口控制三通道（沉浸式无边框需要），页面其余能力照旧隔离。
          preload: path.join(__dirname, 'preload-harness.js'),
          contextIsolation: true,
          nodeIntegration: false,
          webviewTag: false,
          sandbox: true,
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

  if (LEGACY_SHELL) {
    mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
  } else {
    // 先上 splash（秒开），token 抓到后整页切 Harness
    mainWindow.loadFile(SPLASH_FILE);
    mainWindow.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const key = String(input.key || '').toLowerCase();
      if (key === 'f5' || (input.control && key === 'r')) {
        event.preventDefault();
        // splash 阶段（file:）F5 = 重新拉起 dsh；Harness 已加载 = 普通刷新
        if (mainWindow.webContents.getURL().startsWith('file:')) {
          restartHarness().catch((err) => harnessLog(`F5 重试失败：${err.message}`));
        } else {
          mainWindow.webContents.reload();
        }
      } else if (key === 'f12') {
        event.preventDefault();
        mainWindow.webContents.toggleDevTools();
      }
    });
    // 同源（3080）弹窗放行；外链交给系统浏览器
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\/(127\.0\.0\.1|localhost):3080\//i.test(url)) return { action: 'allow' };
      if (/^https?:\/\//i.test(url)) { shell.openExternal(url).catch(() => {}); }
      return { action: 'deny' };
    });
    // [2026-10-30 沉浸式] 页面每次加载完成后注入拖拽区 + 窗口控制（F5 刷新也在内）
    mainWindow.webContents.on('did-finish-load', () => {
      applyFrameOverlay().catch(() => {});
    });
    bootHarnessWindow().catch((err) => console.error('[harness] 启动异常：', err));
  }

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
  const items = [
    { label: LEGACY_SHELL ? '打开工作台' : '打开 MoRay', click: () => showWindow() }
  ];
  if (!LEGACY_SHELL) {
    items.push({
      label: '重新加载界面',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.reload();
      }
    });
    items.push({
      label: '重启 Harness（重新抓 token）',
      click: () => { restartHarness().catch((err) => console.error('[harness] 重启失败：', err)); }
    });
  }
  items.push(
    {
      label: '重启全部服务',
      click: () => { restartEverything().catch((err) => console.error('[restart] 失败：', err)); }
    },
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
  );
  tray.setContextMenu(Menu.buildFromTemplate(items));
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
  await restartEverything();
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
    // Harness 模式不需要 Electron 默认的 File/Edit/View 菜单条：原生标题栏已够用
    if (!LEGACY_SHELL) Menu.setApplicationMenu(null);

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
