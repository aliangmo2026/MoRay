/* ============================================================
 * MoRay 全生态工作台 - 渲染进程交互 (renderer.js)
 *
 * 负责：
 *   - 侧边栏导航（含状态指示灯）
 *   - 右侧多标签页：每个标签页是一个 <webview>，原样加载现有网页
 *   - 启动遮罩、重启全部服务
 *   - 自定义标题栏按钮
 *
 * 注意：本文件不向任何 webview 注入脚本或样式，
 *       原有网页界面 100% 原样显示。
 * ============================================================ */

'use strict';

/* ---------------- 元素引用 ---------------- */

const navEl = document.getElementById('nav');
const tabsEl = document.getElementById('tabs');
const viewsEl = document.getElementById('views');
const emptyHintEl = document.getElementById('empty-hint');
const splashEl = document.getElementById('splash');
const splashListEl = document.getElementById('splash-list');
const btnEnterEl = document.getElementById('btn-enter');
const btnRetryEl = document.getElementById('btn-retry');
const btnRestartEl = document.getElementById('btn-restart');

/* ---------------- 运行时状态 ---------------- */

let services = [];
let activeId = null;
const openedIds = new Set();

/* ---------------- 各导航项的简易图标 ---------------- */

const ICONS = {
  // 中枢：四宫格
  moray:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  // Laya：闪电
  laya:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  // Argus：放大镜（情报官）
  argus:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/></svg>',
  // Iris：眼睛（多模态感官）
  iris:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  // 日志中心：终端
  logs:
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
};

/* ---------------- 侧边栏渲染 ---------------- */

function renderNav() {
  navEl.innerHTML = '';
  services.forEach((svc) => {
    const item = document.createElement('div');
    item.className = 'nav-item';
    item.dataset.id = svc.id;
    item.innerHTML =
      '<span class="nav-icon">' + (ICONS[svc.id] || '') + '</span>' +
      '<span class="nav-label">' + svc.name + '</span>' +
      '<span class="status-dot ' + svc.status + '"></span>';
    item.addEventListener('click', () => openTab(svc.id));
    navEl.appendChild(item);
  });

  // 在 Iris 下面追加「日志中心」导航项（内部页面，不是 webview）
  appendLogNavItem();
  // [L7.7 加性] 「日志中心」下方追加「Laya调试」导航项（内部页面，图标 ⚡）
  appendLayaDebugNavItem();
}

/** 追加「日志中心」侧边栏项 */
function appendLogNavItem() {
  const item = document.createElement('div');
  item.className = 'nav-item';
  item.dataset.id = 'logs';
  item.innerHTML =
    '<span class="nav-icon">' + ICONS.logs + '</span>' +
    '<span class="nav-label">日志中心</span>' +
    '<span class="status-dot"></span>';
  item.addEventListener('click', () => openTab('logs'));
  navEl.appendChild(item);
}

/** [L7.7] 追加「Laya调试」侧边栏项（与日志中心同技术栈：内部标签页，非 webview） */
function appendLayaDebugNavItem() {
  const item = document.createElement('div');
  item.className = 'nav-item';
  item.dataset.id = 'laya-debug';
  item.innerHTML =
    '<span class="nav-icon laya-dbg-icon">⚡</span>' +
    '<span class="nav-label">Laya调试</span>' +
    '<span class="status-dot"></span>';
  item.addEventListener('click', () => openTab('laya-debug'));
  navEl.appendChild(item);
}

function updateNavStatus(id, status) {
  const item = navEl.querySelector('.nav-item[data-id="' + id + '"]');
  if (item) {
    const dot = item.querySelector('.status-dot');
    dot.className = 'status-dot ' + status;
  }
  // 同步标签页上的小圆点
  const tabDot = tabsEl.querySelector('.tab[data-id="' + id + '"] .tab-dot');
  if (tabDot) {
    tabDot.style.background =
      status === 'running' ? 'var(--ok)' :
      status === 'starting' ? 'var(--warn)' :
      status === 'failed' ? 'var(--err)' : 'var(--text-dim)';
  }
}

/* ---------------- 启动遮罩渲染 ---------------- */

function renderSplashList() {
  splashListEl.innerHTML = '';
  services.forEach((svc) => {
    const row = document.createElement('div');
    row.className = 'splash-item';
    row.dataset.id = svc.id;
    row.innerHTML =
      '<span class="status-dot ' + svc.status + '"></span>' +
      '<span>' + svc.name + '</span>' +
      '<span class="splash-state" style="margin-left:auto;font-size:11px;color:var(--text-dim)">' +
        stateText(svc.status) +
      '</span>';
    splashListEl.appendChild(row);
  });
}

function updateSplashRow(id, status) {
  const row = splashListEl.querySelector('.splash-item[data-id="' + id + '"]');
  if (row) {
    row.querySelector('.status-dot').className = 'status-dot ' + status;
    row.querySelector('.splash-state').textContent = stateText(status);
  }
}

function stateText(status) {
  if (status === 'running') return '运行中';
  if (status === 'starting') return '启动中…';
  if (status === 'failed') return '启动失败';
  return '等待中';
}

/**
 * 检查是否全部服务已落定（运行中 / 失败）。
 * 全部落定后：全绿则自动进入，否则给出“进入工作台”按钮。
 */
function checkSettled() {
  if (services.length === 0) return;
  const settled = services.every((s) => s.status === 'running' || s.status === 'failed');
  if (!settled) return;

  const allRunning = services.every((s) => s.status === 'running');
  if (allRunning) {
    hideSplash();
  } else {
    btnEnterEl.hidden = false;
    btnRetryEl.hidden = false;
  }
}

function showSplash() {
  splashEl.classList.remove('hidden');
  btnEnterEl.hidden = true;
  btnRetryEl.hidden = true;
}

function hideSplash() {
  splashEl.classList.add('hidden');
}

/* ---------------- 标签页与 webview ---------------- */

/**
 * 打开（或切换到）一个标签页
 */
function openTab(id) {
  // 日志中心：内部页面，走单独的创建流程
  if (id === 'logs') {
    openLogTab();
    return;
  }
  // [L7.7 加性] Laya 调试面板：同为内部页面（非 webview），技术栈与日志中心一致
  if (id === 'laya-debug') {
    openLayaDebugTab();
    return;
  }

  const svc = services.find((s) => s.id === id);
  if (!svc) return;

  let created = false;
  if (!openedIds.has(id)) {
    openedIds.add(id);
    created = true;
    createTabElement(svc);
    createWebview(svc);   // 先创建元素（此时不设 src，元素处于 display:none）
  }

  // 先激活，让 webview 以完整尺寸参与布局
  activateTab(id);

  if (created) {
    const wv = viewsEl.querySelector('.webview[data-id="' + id + '"]');
    if (wv) {
      // 强制一次布局：确保元素此时已拿到完整尺寸（如 744px 高）
      void wv.getBoundingClientRect();
      // 然后再让访客页面开始加载——访客视口就是完整尺寸，不会被压缩
      wv.src = svc.url;
    }
  }
}

function createTabElement(svc) {
  const tab = document.createElement('div');
  tab.className = 'tab';
  tab.dataset.id = svc.id;
  tab.innerHTML =
    '<span class="tab-dot" style="background:var(--text-dim)"></span>' +
    '<span class="tab-name">' + svc.name + '</span>' +
    '<span class="tab-close" title="关闭标签">✕</span>';
  tab.addEventListener('click', () => activateTab(svc.id));
  tab.querySelector('.tab-close').addEventListener('click', (event) => {
    event.stopPropagation();
    closeTab(svc.id);
  });
  tabsEl.appendChild(tab);
}

/**
 * 创建 webview：原样嵌入现有网页。
 * 只创建元素并设置属性，【src 在 openTab 中布局完成后再设置】，
 * 防止访客页面在元素零尺寸/隐藏时启动、视口被压缩成一小条。
 * 不注入任何脚本/样式。
 */
function createWebview(svc) {
  const wv = document.createElement('webview');
  wv.className = 'webview';
  wv.dataset.id = svc.id;
  wv.setAttribute('allowpopups', '');
  wv.setAttribute('partition', 'persist:moray');
  // 注意：此处不设置 src
  viewsEl.appendChild(wv);

  // [壳主题联动 2026-10-13] 只有 moray 标签有主题系统：轮询读 guest 的 data-theme，
  // 同步到壳自己的 html[data-shell-theme]（只读，不注入任何脚本/样式）
  if (svc.id === 'moray') {
    const _syncShellTheme = () => {
      if (!wv.isConnected) return;                 // 标签已关闭：停手
      try {
        wv.executeJavaScript("document.documentElement.dataset.theme || 'dark'")
          .then((t) => {
            if (t === 'light' || t === 'dark') {
              // [UI 2.0 步骤8] 仅主题变化时写 DOM，避免 200ms 轮询造成无意义重绘
              if (document.documentElement.dataset.shellTheme !== t) {
                document.documentElement.dataset.shellTheme = t;
              }
            }
          })
          .catch(() => {});
      } catch (e) { /* dom-ready 之前调用会同步抛错，忽略 */ }
    };
    wv.addEventListener('did-finish-load', _syncShellTheme);
    // [UI 2.0 步骤8] 1.5s → 200ms：主题切换接近即时；配合上面的变化守卫，空转时不写 DOM
    setInterval(_syncShellTheme, 200);
  }
}

function activateTab(id) {
  activeId = id;

  // 导航高亮
  navEl.querySelectorAll('.nav-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  // 标签高亮
  tabsEl.querySelectorAll('.tab').forEach((el) => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  // 只显示对应内容（webview 与内部视图如日志中心）
  viewsEl.querySelectorAll('[data-id]').forEach((el) => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  emptyHintEl.classList.add('hidden');
}

function closeTab(id) {
  openedIds.delete(id);

  // 关闭日志中心：停掉轮询
  if (id === 'logs') {
    stopLogPolling();
  }

  // [L7.7 加性] 关闭 Laya 调试标签页：断开 SSE、清理资源
  if (id === 'laya-debug') {
    destroyLayaDebug();
  }

  const tab = tabsEl.querySelector('.tab[data-id="' + id + '"]');
  if (tab) tab.remove();

  // 通用移除视图（webview 或内部视图如日志中心）
  const view = viewsEl.querySelector('[data-id="' + id + '"]');
  if (view) view.remove();

  // 关闭的是当前标签：切到最后一个剩余标签
  if (activeId === id) {
    const remaining = Array.from(openedIds);
    if (remaining.length > 0) {
      activateTab(remaining[remaining.length - 1]);
    } else {
      activeId = null;
      navEl.querySelectorAll('.nav-item').forEach((el) => el.classList.remove('active'));
      emptyHintEl.classList.remove('hidden');
    }
  }
}

/* ---------------- 日志中心（内置页面，非 webview） ----------------
 *  - 顶部 4 个按钮切换查看 MoRay / Laya / Argus / Iris 的运行日志
 *  - 每 1.5s 向主进程拉取增量日志，自动滚动到底部
 *  - 日志来源：D:\MoRay\logs\electron_<id>.out.log / .err.log
 * ------------------------------------------------------------------ */

let logPollTimer = null;
let logCurrentSvc = null;
let logBodyEl = null;

/** 打开日志中心标签页（已开则切过去） */
function openLogTab() {
  if (!openedIds.has('logs')) {
    openedIds.add('logs');
    createTabElement({ id: 'logs', name: '日志中心' });
    createLogView();
  }
  activateTab('logs');
}

/** 创建日志中心内部视图：服务切换条 + 日志正文区 */
function createLogView() {
  const view = document.createElement('div');
  view.className = 'log-view';
  view.dataset.id = 'logs';
  view.innerHTML =
    '<div class="log-toolbar"></div>' +
    '<pre class="log-body"></pre>';
  viewsEl.appendChild(view);

  const toolbar = view.querySelector('.log-toolbar');
  logBodyEl = view.querySelector('.log-body');

  // 从主进程拿可查看日志的服务列表，生成切换按钮
  window.moray.logs.list().then((list) => {
    list.forEach((svc) => {
      const btn = document.createElement('button');
      btn.className = 'log-svc-btn';
      btn.textContent = svc.name;
      btn.dataset.id = svc.id;
      btn.addEventListener('click', () => switchLogService(svc.id));
      toolbar.appendChild(btn);
    });
    if (list.length > 0) switchLogService(list[0].id);
  });
}

/** 切换查看某个服务的日志：清空正文并重新开始轮询 */
function switchLogService(svcId) {
  logCurrentSvc = svcId;
  document.querySelectorAll('.log-svc-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.id === svcId);
  });
  if (logBodyEl) logBodyEl.textContent = '';
  startLogPolling();
}

function startLogPolling() {
  if (logPollTimer) clearInterval(logPollTimer);
  logPollTimer = setInterval(async () => {
    if (!logCurrentSvc || !logBodyEl) return;
    try {
      const { text } = await window.moray.logs.read(logCurrentSvc);
      if (text) {
        logBodyEl.appendChild(document.createTextNode(text));
        logBodyEl.scrollTop = logBodyEl.scrollHeight; // 自动滚到底部
      }
    } catch (err) {
      // 读取失败忽略，下一拍再试
    }
  }, 1500);
}

function stopLogPolling() {
  if (logPollTimer) {
    clearInterval(logPollTimer);
    logPollTimer = null;
  }
  logCurrentSvc = null;
  logBodyEl = null;
}

/* ---------------- [L7.7 加性] Laya 调试面板（内置页面，非 webview） ----------------
 *  - 顶部操作区：目标输入（多行）+ 最大重试（默认5）+ 拆解/执行/取消 三按钮
 *  - 结果区：左「拆解步骤」列表；右「执行事件流」按事件类型着色滚动
 *      step_start 蓝 / step_result 白 / check 橙 / final 绿 / 错误与失败 红
 *  - 底部辅助：清空日志 / 复制最终结果 / 导出执行日志（走主进程另存为）
 *  - 接口地址可配置，默认 http://localhost:8001（localStorage 记忆）
 *  - 关闭标签页自动 abort SSE，清理全部资源
 * ------------------------------------------------------------------ */

let dbgState = null; // {abort, executionId, logLines[], lastFinal, running}

const LAYA_DBG_DEFAULT_BASE = 'http://localhost:8001';

function dbgBase() {
  let v = '';
  try { v = localStorage.getItem('moray_laya_base') || ''; } catch (e) {}
  return (v || LAYA_DBG_DEFAULT_BASE).replace(/\/+$/, '');
}

/** 打开 Laya 调试标签页（已开则切过去） */
function openLayaDebugTab() {
  if (!openedIds.has('laya-debug')) {
    openedIds.add('laya-debug');
    createTabElement({ id: 'laya-debug', name: 'Laya调试' });
    createLayaDebugView();
  }
  activateTab('laya-debug');
}

function dbgLog(cls, text) {
  const body = document.getElementById('layaDbgLog');
  if (!body) return;
  if (!dbgState) dbgState = { abort: null, executionId: '', logLines: [], lastFinal: '', running: false };
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const line = '[' + time + '] ' + text;
  dbgState.logLines.push(line);
  const el = document.createElement('div');
  el.className = 'laya-dbg-line ' + (cls || '');
  el.textContent = line;
  body.appendChild(el);
  body.scrollTop = body.scrollHeight;
}

function dbgSetStatus(text, cls) {
  const s = document.getElementById('layaDbgRunState');
  if (s) { s.textContent = text; s.className = 'laya-dbg-state ' + (cls || ''); }
}

function dbgButtons(running) {
  if (!dbgState) dbgState = { abort: null, executionId: '', logLines: [], lastFinal: '', running: false };
  dbgState.running = !!running;
  const plan = document.getElementById('layaDbgPlanBtn');
  const run = document.getElementById('layaDbgRunBtn');
  const cancel = document.getElementById('layaDbgCancelBtn');
  if (plan) plan.disabled = !!running;
  if (run) run.disabled = !!running;
  if (cancel) cancel.disabled = !running;
}

/** 创建 Laya 调试内部视图 */
function createLayaDebugView() {
  dbgState = { abort: null, executionId: '', logLines: [], lastFinal: '', running: false };

  const view = document.createElement('div');
  view.className = 'laya-debug-view';
  view.dataset.id = 'laya-debug';
  view.innerHTML = [
    '<div class="laya-dbg-bar">',
    '  <input id="layaDbgBase" class="laya-dbg-base" title="Laya 接口地址（可配置）" spellcheck="false">',
    '  <label class="laya-dbg-label">最大重试',
    '    <input id="layaDbgRetry" class="laya-dbg-retry" type="number" min="0" max="9" value="5">',
    '  </label>',
    '  <button id="layaDbgPlanBtn" class="laya-dbg-btn">拆解任务</button>',
    '  <button id="layaDbgRunBtn" class="laya-dbg-btn primary">执行任务</button>',
    '  <button id="layaDbgCancelBtn" class="laya-dbg-btn danger" disabled>取消执行</button>',
    '  <span id="layaDbgRunState" class="laya-dbg-state">空闲</span>',
    '</div>',
    '<textarea id="layaDbgGoal" class="laya-dbg-goal" rows="3"',
    '  placeholder="任务目标，例：搜索最新大模型微调资讯，然后写入 laya_news.md"></textarea>',
    '<div class="laya-dbg-cols">',
    '  <div class="laya-dbg-col">',
    '    <div class="laya-dbg-col-title">拆解步骤</div>',
    '    <div id="layaDbgPlan" class="laya-dbg-plan"><div class="laya-dbg-hint">点「拆解任务」调 /planner，步骤列表显示在这里。</div></div>',
    '  </div>',
    '  <div class="laya-dbg-col grow">',
    '    <div class="laya-dbg-col-title">执行事件流（/execute · SSE）</div>',
    '    <div id="layaDbgLog" class="laya-dbg-log"></div>',
    '  </div>',
    '</div>',
    '<div class="laya-dbg-foot">',
    '  <button id="layaDbgClearBtn" class="laya-dbg-btn small">清空日志</button>',
    '  <button id="layaDbgCopyBtn" class="laya-dbg-btn small">复制最终结果</button>',
    '  <button id="layaDbgExportBtn" class="laya-dbg-btn small">导出执行日志</button>',
    '</div>'
  ].join('');
  viewsEl.appendChild(view);

  const baseInput = view.querySelector('#layaDbgBase');
  baseInput.value = dbgBase();
  baseInput.addEventListener('change', () => {
    try { localStorage.setItem('moray_laya_base', baseInput.value.trim()); } catch (e) {}
    dbgLog('ev', '接口地址已改为 ' + dbgBase());
  });

  view.querySelector('#layaDbgPlanBtn').addEventListener('click', dbgPlan);
  view.querySelector('#layaDbgRunBtn').addEventListener('click', dbgExecute);
  view.querySelector('#layaDbgCancelBtn').addEventListener('click', dbgCancel);
  view.querySelector('#layaDbgClearBtn').addEventListener('click', () => {
    const body = document.getElementById('layaDbgLog');
    if (body) body.innerHTML = '';
    if (dbgState) dbgState.logLines = [];
  });
  view.querySelector('#layaDbgCopyBtn').addEventListener('click', dbgCopyFinal);
  view.querySelector('#layaDbgExportBtn').addEventListener('click', dbgExportLog);
}

function dbgGoalText() {
  const g = document.getElementById('layaDbgGoal');
  return g ? g.value.trim() : '';
}

/** 拆解任务：POST /planner → 步骤列表 */
async function dbgPlan() {
  const goal = dbgGoalText();
  if (!goal) { dbgLog('err', '任务目标为空'); return; }
  dbgLog('ev', 'POST /planner — ' + goal);
  try {
    const r = await fetch(dbgBase() + '/planner', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal })
    });
    const j = await r.json();
    if (!r.ok || (j && j.code !== 0)) {
      dbgLog('err', '拆解失败：HTTP ' + r.status + ' ' + ((j && (j.error || j.msg)) || ''));
      return;
    }
    const steps = (j.steps || []);
    const box = document.getElementById('layaDbgPlan');
    box.innerHTML = '';
    steps.forEach((s) => {
      const row = document.createElement('div');
      row.className = 'laya-dbg-step';
      row.innerHTML =
        '<div class="laya-dbg-step-head"><span class="laya-dbg-step-no">' + s.step_id + '</span>' +
        '<span class="laya-dbg-step-tool tool-' + s.tool + '">' + s.tool + '</span></div>' +
        '<div class="laya-dbg-step-desc"></div>';
      row.querySelector('.laya-dbg-step-desc').textContent = s.description || '';
      box.appendChild(row);
    });
    dbgLog('ev', '拆解完成：' + steps.length + ' 步（mode=' + (j.mode || '?') + '）');
  } catch (e) {
    dbgLog('err', '拆解请求异常：' + ((e && e.message) || e) + '（Laya 是否在 ' + dbgBase() + ' 运行？）');
  }
}

/** 执行任务：POST /execute（SSE），事件流着色滚动 */
async function dbgExecute() {
  if (dbgState && dbgState.running) return;
  const goal = dbgGoalText();
  if (!goal) { dbgLog('err', '任务目标为空'); return; }
  const retryEl = document.getElementById('layaDbgRetry');
  const maxRetry = Math.max(0, Math.min(9, parseInt((retryEl && retryEl.value) || '5', 10) || 5));

  const ctrl = new AbortController();
  dbgState = dbgState || { abort: null, executionId: '', logLines: [], lastFinal: '', running: false };
  dbgState.abort = ctrl;
  dbgState.executionId = '';
  dbgState.lastFinal = '';
  dbgButtons(true);
  dbgSetStatus('执行中', 'run');
  dbgLog('ev', 'POST /execute — ' + goal + '（max_retry=' + maxRetry + '）');

  try {
    const resp = await fetch(dbgBase() + '/execute', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal, max_retry: maxRetry }),
      signal: ctrl.signal
    });
    if (!resp.ok || !resp.body) {
      let detail = '';
      try { detail = await resp.text(); } catch (e) {}
      throw new Error('HTTP ' + resp.status + ' ' + detail.slice(0, 200));
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder('utf-8');
    let bufStr = '';
    let gotFinal = false;
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bufStr += dec.decode(chunk.value, { stream: true });
      const frames = bufStr.split('\n\n');
      bufStr = frames.pop();
      for (const frame of frames) {
        for (const line of frame.split('\n')) {
          if (line.indexOf('data:') !== 0) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let ev;
          try { ev = JSON.parse(payload); } catch (e) { continue; }
          if (ev.event === 'final') gotFinal = true;
          dbgHandleEvent(ev);
        }
      }
    }
    if (!gotFinal) {
      dbgLog('err', 'SSE 连接意外断开（未收到 final 帧）');
      dbgSetStatus('断开', 'fail');
    }
  } catch (e) {
    if (e && e.name === 'AbortError') {
      dbgLog('warn', '本地已中止 SSE 连接');
      dbgSetStatus('已中止', 'warn');
    } else {
      dbgLog('err', '执行异常：' + ((e && e.message) || e) + '（Laya 是否在 ' + dbgBase() + ' 运行？）');
      dbgSetStatus('失败', 'fail');
    }
  } finally {
    dbgButtons(false);
    if (dbgState) dbgState.abort = null;
  }
}

function dbgHandleEvent(ev) {
  const id = ev.execution_id || '';
  switch (ev.event) {
    case 'start':
      dbgState.executionId = ev.execution_id || '';
      dbgLog('ev', 'start · execution_id=' + (ev.execution_id || '?') + ' 总步数=' + (ev.total_steps || 0));
      break;
    case 'step_start':
      dbgLog('step', 'step_start #' + ev.step_id + ' [' + (ev.tool || '?') + '] ' + (ev.description || '') +
        (Number(ev.attempt) > 1 ? '（重试第 ' + (ev.attempt - 1) + ' 次）' : ''));
      break;
    case 'step_result':
      if (ev.ok === false) {
        dbgLog('fail', 'step_result #' + ev.step_id + ' 失败 ' + JSON.stringify(ev.error || '') +
          '（retry_count=' + (ev.retry_count != null ? ev.retry_count : '?') + '）');
      } else {
        let d = String(ev.data == null ? '' : ev.data);
        if (d.length > 160) d = d.slice(0, 160) + '…';
        dbgLog('res', 'step_result #' + ev.step_id + ' 完成 ' + d + '（' + (ev.elapsed_ms || '?') + 'ms）');
      }
      break;
    case 'check':
      dbgLog('check', 'check #' + ev.step_id + ' success=' + ev.success + ' mode=' + (ev.mode || '?') +
        (ev.reason ? ' · ' + ev.reason : ''));
      break;
    case 'retry':
      dbgLog('check', 'retry #' + ev.step_id + ' 正在重试第 ' + (ev.retry_count != null ? ev.retry_count : ev.attempt) + ' 次' +
        (ev.fix ? ' · fix=' + ev.fix : ''));
      break;
    case 'final':
      if (ev.status === 'success') {
        dbgState.lastFinal = String(ev.result || '');
        dbgLog('final', 'final · success（' + (ev.note || '') + '）');
        dbgSetStatus('成功', 'ok');
      } else if (ev.status === 'cancelled') {
        dbgState.lastFinal = '（已取消）';
        dbgLog('warn', 'final · cancelled ' + (ev.error || ''));
        dbgSetStatus('已取消', 'warn');
      } else {
        dbgState.lastFinal = '（失败）' + String(ev.error || '');
        dbgLog('fail', 'final · failed ' + (ev.error || ''));
        dbgSetStatus('失败', 'fail');
      }
      break;
    default:
      dbgLog('res', (ev.event || '事件') + ' ' + JSON.stringify(ev).slice(0, 200));
  }
}

/** 取消执行：POST /execute/cancel（生效点在步骤边界） */
async function dbgCancel() {
  if (!dbgState || !dbgState.executionId) {
    if (dbgState && dbgState.abort) { dbgState.abort.abort(); }
    dbgLog('warn', '取消：无 execution_id，已本地中止连接');
    return;
  }
  dbgLog('ev', 'POST /execute/cancel — ' + dbgState.executionId);
  try {
    const r = await fetch(dbgBase() + '/execute/cancel', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ execution_id: dbgState.executionId })
    });
    const j = await r.json();
    if (j && j.code === 0 && j.cancelled) {
      dbgLog('warn', '取消已受理：' + dbgState.executionId + '（将在当前步骤边界生效）');
      dbgSetStatus('取消中', 'warn');
    } else {
      dbgLog('err', '取消未生效：' + JSON.stringify(j || {}) + '（任务可能已结束）');
    }
  } catch (e) {
    dbgLog('err', '取消请求异常：' + ((e && e.message) || e));
  }
}

/** 复制最终结果 */
async function dbgCopyFinal() {
  const t = dbgState && dbgState.lastFinal ? dbgState.lastFinal : '';
  if (!t) { dbgLog('warn', '复制：暂无最终结果'); return; }
  try {
    await navigator.clipboard.writeText(t);
    dbgLog('ev', '已复制最终结果（' + t.length + ' 字符）');
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = t; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); dbgLog('ev', '已复制最终结果（兼容路径）'); }
    catch (e2) { dbgLog('err', '复制失败：' + ((e && e.message) || e)); }
    ta.remove();
  }
}

/** 导出执行日志：主进程另存为 */
async function dbgExportLog() {
  const lines = dbgState && dbgState.logLines ? dbgState.logLines : [];
  if (!lines.length) { dbgLog('warn', '导出：日志为空'); return; }
  const name = 'laya-debug-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.log';
  try {
    const res = await window.moray.laya.save(lines.join('\n'), name);
    if (res && res.ok) dbgLog('ev', '已导出：' + res.path);
    else if (res && res.error !== 'cancelled') dbgLog('err', '导出失败：' + res.error);
  } catch (e) {
    dbgLog('err', '导出异常：' + ((e && e.message) || e));
  }
}

/** 关闭标签页时清理：断开 SSE、释放状态 */
function destroyLayaDebug() {
  try {
    if (dbgState && dbgState.abort) dbgState.abort.abort();
  } catch (e) {}
  dbgState = null;
}

/* ---------------- 侧边栏自动展开 / 收起（VS Code 风格） ----------------
 *  - 鼠标移入：立即加 .expanded，CSS 0.2s 平滑展开到 200px
 *  - 鼠标移出：延迟 300ms 再移除 .expanded，避免快速划过时频繁抖动
 *  - 收起态仅 48px，保留图标 + 状态灯；底部按钮在收起态隐藏
 * --------------------------------------------------------------------- */

let sidebarCollapseTimer = null;

function setupSidebarHover() {
  const sidebarEl = document.querySelector('.sidebar');
  if (!sidebarEl) return;

  // 鼠标移入：若有待收起计时器，取消；立即展开
  sidebarEl.addEventListener('mouseenter', () => {
    if (sidebarCollapseTimer) {
      clearTimeout(sidebarCollapseTimer);
      sidebarCollapseTimer = null;
    }
    sidebarEl.classList.add('expanded');
  });

  // 鼠标移出：300ms 后自动收起（短暂移开不误触）
  sidebarEl.addEventListener('mouseleave', () => {
    sidebarCollapseTimer = setTimeout(() => {
      sidebarEl.classList.remove('expanded');
      sidebarCollapseTimer = null;
    }, 300);
  });
}

/* ---------------- 静态控件绑定（重启 / 标题栏） ----------------
 * 统一收进函数，等 DOM 就绪后（见文件底部）再调用，
 * 防止页面元素尚未解析就绑定事件导致失效。
 * --------------------------------------------------------- */

function bindStaticControls() {
  /* ---- 侧边栏自动展开 / 收起 ---- */
  setupSidebarHover();

  /* ---- 重启全部服务（侧边栏按钮 / splash 失败态「重试」共用） ---- */
  async function restartAllFlow(btnEl) {
    if (!btnEl || btnEl.classList.contains('busy')) return;
    btnEl.classList.add('busy');
    // 重置界面状态为“启动中”
    services.forEach((s) => {
      s.status = 'starting';
      updateNavStatus(s.id, 'starting');
    });
    showSplash();
    renderSplashList();

    // 等待主进程：按端口停服务 -> 重调 v36 一键启动器 -> 等就绪
    await window.moray.restartServices();

    btnEl.classList.remove('busy');
    checkSettled();
  }
  btnRestartEl.addEventListener('click', () => restartAllFlow(btnRestartEl));
  btnRetryEl.addEventListener('click', () => restartAllFlow(btnRetryEl));

  /* ---- 标题栏按钮 ---- */
  document.getElementById('btn-min').addEventListener('click', () => {
    window.moray.minimize();
  });

  document.getElementById('btn-max').addEventListener('click', () => {
    window.moray.toggleMaximize();
  });

  document.getElementById('btn-close').addEventListener('click', () => {
    // 主进程会将其转为“隐藏到托盘”
    window.moray.close();
  });

  // 双击标题栏拖动区：最大化 / 还原
  document.querySelector('.titlebar-drag').addEventListener('dblclick', () => {
    window.moray.toggleMaximize();
  });

  // 遮罩上的“进入工作台”
  btnEnterEl.addEventListener('click', hideSplash);
}

/* ---------------- 初始化（DOM 就绪后执行） ---------------- */

async function init() {
  // 先绑定静态控件
  bindStaticControls();

  services = await window.moray.getServices();
  renderNav();
  renderSplashList();

  // 订阅主进程状态推送
  window.moray.onStatusChange(({ id, status }) => {
    const svc = services.find((s) => s.id === id);
    if (!svc) return;
    svc.status = status;
    updateNavStatus(id, status);
    updateSplashRow(id, status);
    checkSettled();
  });

  // 若启动时服务已全部就绪，直接落定遮罩
  checkSettled();
}

// DOM 已就绪（脚本在 body 末尾时 readyState 通常为 'interactive'）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
