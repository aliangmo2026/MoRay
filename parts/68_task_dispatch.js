/* ===================== [3.20 阶段3.2] 任务分派面板（A2A 子集） =====================
 * 能力边界（诚实说明，不做"假装"功能）：
 *  - 本面板**不与外部 agent 建立实时通信通道**。真实的 A2A（agent 之间互相调用/回传）
 *    需要对方平台开放接口与鉴权，MoRay 目前不具备，所以这里只做能真正工作的三件事：
 *    ① 结构化任务指令生成（项目路径/背景/要求/验收标准模板）+ 一键复制到剪贴板；
 *    ② 任务状态管理（待办/进行中/已完成/失败/已超时/已取消）+ 备注 + 删除，localStorage 持久化；
 *    ③ [v3.21.0] **一键自动指挥 Codex**：本地后端用 `codex exec` 在任务的项目目录里真正干活，
 *       日志流式回传、状态按真实退出码流转、token usage 折算成本（拿不到就写"未能获取"，不编造）。
 *  - 若设置里已配置 OpenAI 兼容云端 API，"直接执行"按钮才出现（未配置时**不渲染**，
 *    而不是渲染一个点不动的灰按钮）；点击后用该 API 跑这条任务并把结果回填到任务备注。
 *
 * 实现约束：不动 moray-workbench.html 骨架 —— 导航按钮与页面容器均由本分片在运行时注入，
 * 并自带与骨架一致的页面切换逻辑（骨架的切换脚本在解析期只绑定已存在的按钮）。
 */
(function () {
  const LS_KEY = 'moray_task_dispatch_v1';
  const PAGE = 'tasks';

  /** 目标 agent 预设（自定义项由用户填写名称） */
  const AGENTS = [
    { id: 'zcode', name: 'ZCode' },
    { id: 'codex', name: 'Codex' },
    { id: 'doubao', name: '豆包' },
    { id: 'claude-code', name: 'Claude Code' },
    { id: 'cursor', name: 'Cursor' },
    { id: 'custom', name: '自定义…' }
  ];
  const STATUS = [
    { id: 'todo', name: '待办', cls: 'bg-text-tertiary/15 text-text-tertiary' },
    { id: 'doing', name: '进行中', cls: 'bg-brand-cyan/15 text-brand-cyan' },
    { id: 'done', name: '已完成', cls: 'bg-success/15 text-success' },
    { id: 'failed', name: '失败', cls: 'bg-danger/15 text-danger' },
    // [v3.21.0 自动执行] 新增两种终态：超时（强杀）与取消（用户停止）
    { id: 'timeout', name: '已超时', cls: 'bg-warning/15 text-warning' },
    { id: 'canceled', name: '已取消', cls: 'bg-text-tertiary/15 text-text-tertiary' }
  ];
  const DEFAULT_ACCEPT = '1. 功能真实可用（没有假按钮 / 假数据 / 假状态）\n2. 通过项目现有测试或自测命令\n3. 给出改动文件清单与关键实现说明';

  /** [v3.21.0] 自动执行相关常量 */
  const DEFAULT_CODEX_CMD = 'codex.cmd';         // 默认 codex CLI（cmd 包装器，规避 PowerShell 执行策略）
  const DEFAULT_WORKDIR = 'D:\\ai工具台';          // 默认工作目录（本机工作台工程目录；设置页可改）
  const DEFAULT_TIMEOUT = 600;                    // 默认任务超时（秒）
  const LOG_KEEP = 400;                           // 本地保留日志行数（防止 localStorage 膨胀）
  const LOG_TAIL_RECORD = 200;                    // 执行记录里保留的日志尾行数
  const PATH_TTL = 20000;                         // 项目路径存在性校验缓存（ms）

  /** 本地状态（localStorage 持久化；结构损坏时安全回退为空） */
  let state = { tasks: [], seq: 1, projectPath: '', customAgent: '', lastForm: null, records: [] };
  /** 当前是否有任务正在执行（云端直接执行用；自动执行以后端为准） */
  let running = false;
  /** 自动执行中的任务 → 轮询定时器 @type {Object<string, number>} */
  const pollers = {};
  /** 每个任务已消费到的日志全局行号 @type {Object<string, number>} */
  const offsets = {};
  /** 项目路径校验缓存 path → {ok, note, at} @type {Object<string, Object>} */
  const pathCache = {};
  /** 自动执行是否可用（本地后端在线） */
  let backendOnline = false;

  function load() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const o = JSON.parse(raw);
      if (o && Array.isArray(o.tasks)) {
        state = {
          tasks: o.tasks.filter(t => t && t.id),
          seq: parseInt(o.seq, 10) || (o.tasks.length + 1),
          projectPath: typeof o.projectPath === 'string' ? o.projectPath : '',
          customAgent: typeof o.customAgent === 'string' ? o.customAgent : '',
          lastForm: o.lastForm || null,
          records: Array.isArray(o.records) ? o.records.filter(r => r && r.taskId).slice(0, 50) : []
        };
      }
    } catch (e) { /* 损坏数据不阻塞面板 */ }
    // [v3.21.0] 上次关页面时还在"运行中"的任务：先按"与后端失联"兜底（install 后再按后端记录校正）
    state.tasks.forEach(t => {
      if (t.status !== 'doing') return;
      const r = t.run || {};
      if (r.status && r.status !== 'running') return;
      const run = Object.assign({}, r, {
        status: 'failed',
        error: '应用重启导致中断（页面关闭/后端重启时任务尚未结束）',
        endedAt: r.endedAt || Date.now()
      });
      t.run = run;
      t.status = 'failed';
      t.log = appendLog(t.log, '—— 页面重载：该任务此前处于运行中，且未查到后端仍有对应的运行记录，标注为「应用重启导致中断」');
    });
  }

  function save() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(state)); return true; }
    catch (e) { return false; }
  }

  function uid() { return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  /** 追加日志行（按上限裁剪，防止 localStorage 写爆） */
  function appendLog(arr, line) {
    const out = Array.isArray(arr) ? arr.slice() : [];
    String(line == null ? '' : line).split('\n').forEach(l => { if (l !== '') out.push(l); });
    return out.length > LOG_KEEP ? out.slice(out.length - LOG_KEEP) : out;
  }

  function esc(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(s == null ? '' : s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function agentName(id) {
    if (id === 'custom') return state.customAgent || '自定义 Agent';
    const a = AGENTS.find(x => x.id === id);
    return a ? a.name : id;
  }

  function statusMeta(id) { return STATUS.find(s => s.id === id) || STATUS[0]; }

  /* ---------------- [v3.21.0] 自动执行：配置读取 / 后端调用 / 展示辅助 ---------------- */

  /** 读设置（未配置时回落到内置默认；默认值不写库，符合"设置项无默认值不写库"） */
  function cfgStr(key, fallback) {
    try {
      if (typeof MoraySettings === 'undefined') return fallback;
      const v = MoraySettings.get(key);
      return (typeof v === 'string' && v.trim()) ? v.trim() : fallback;
    } catch (e) { return fallback; }
  }
  function cfgNum(key, fallback) {
    try {
      if (typeof MoraySettings === 'undefined') return fallback;
      const n = parseInt(MoraySettings.get(key), 10);
      return (isFinite(n) && n > 0) ? n : fallback;
    } catch (e) { return fallback; }
  }
  function cfgCodexPath() { return cfgStr('agentRunCodexPath', DEFAULT_CODEX_CMD); }
  function cfgWorkdir() { return cfgStr('agentRunWorkdir', DEFAULT_WORKDIR); }
  function cfgTimeout() { return Math.max(10, Math.min(3600, cfgNum('agentRunTimeoutSec', DEFAULT_TIMEOUT))); }

  /** 后端 origin（复用全局唯一事实源，file:// 直开回退默认端口） */
  function apiBase() {
    if (typeof agentBackendBase === 'function') {
      try { return agentBackendBase(); } catch (e) { /* 继续回退 */ }
    }
    try {
      if (window.MorayBackend && window.MorayBackend.origin) return window.MorayBackend.origin;
      if (typeof window.resolveBackendOrigin === 'function') return window.resolveBackendOrigin();
    } catch (e) { /* 忽略 */ }
    return 'http://127.0.0.1:8000';
  }

  /** 后端是否在线（自动执行的前置条件；纯静态打开时按钮禁用并说明原因） */
  function isBackendOnline() {
    try {
      if (typeof agentBackendBase === 'function' && window.MorayBackend) {
        return !!window.MorayBackend.connected;
      }
      return !!(window.MorayBackend && window.MorayBackend.connected);
    } catch (e) { return false; }
  }

  /** 前端 → 后端统一请求；后端离线/超时/非 JSON 都给出可读错误
   * @param {string} method - HTTP 方法
   * @param {string} path - 以 /api 开头的路径
   * @param {Object} [body] - JSON body
   * @param {number} [timeoutMs] - 超时
   * @returns {Promise<{ok:boolean, status:number, data?:Object, code?:string, message?:string}>} */
  async function apiCall(method, path, body, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
    try {
      // [v3.21.0] 时间戳破坏缓存：SW 对固定 URL 的 GET 曾有"缓存优先"行为（见 sw_template.js 的 /api 例外），
      // cache:'no-store' 挡不住 SW —— 双保险，保证轮询/对齐读到的都是后端当下状态
      const url = apiBase() + path + (path.indexOf('?') >= 0 ? '&' : '?') + '_t=' + Date.now();
      const opt = { method, signal: ctrl.signal, cache: 'no-store', headers: {} };
      if (body !== undefined) {
        opt.headers['Content-Type'] = 'application/json';
        opt.body = JSON.stringify(body);
      }
      const res = await fetch(url, opt);
      let j = null;
      try { j = await res.json(); } catch (e) { j = null; }
      if (!res.ok) {
        return {
          ok: false, status: res.status,
          code: (j && j.code) || 'http_' + res.status,
          message: (j && (j.message || j.error)) || ('本地后端返回 ' + res.status)
        };
      }
      if (!j || j.ok !== true) {
        return { ok: false, status: res.status, code: (j && j.code) || 'bad_response', message: (j && (j.message || j.error)) || '后端返回格式异常' };
      }
      return { ok: true, status: res.status, data: j.data };
    } catch (e) {
      const aborted = e && e.name === 'AbortError';
      return {
        ok: false, status: 0, code: aborted ? 'timeout' : 'offline',
        message: aborted ? '请求本地后端超时' : '连不上本地后端（需要启动 MoRay 后端）'
      };
    } finally {
      clearTimeout(timer);
    }
  }

  /** 项目路径校验（带 20s 缓存）：决定「自动执行」按钮是否可用及其 tooltip */
  async function checkPath(path) {
    const p = String(path || '').trim();
    if (!p) return { ok: false, note: '未填写项目路径' };
    const hit = pathCache[p];
    if (hit && Date.now() - hit.at < PATH_TTL) return hit;
    const r = await apiCall('GET', '/api/agent/run/preflight?project_path=' + encodeURIComponent(p) +
      '&command=' + encodeURIComponent(cfgCodexPath()));
    const out = r.ok
      ? { ok: !!(r.data.path_ok && r.data.command_ok), note: !r.data.command_ok ? r.data.command_note : (r.data.path_note || ''), at: Date.now() }
      : { ok: false, note: r.message, at: Date.now() };
    pathCache[p] = out;
    return out;
  }

  /** 运行状态 → 中文标签与配色（与 STATUS 的 id 对齐） */
  const RUN_LABEL = {
    running: ['运行中', 'text-brand-cyan'],
    completed: ['已完成', 'text-success'],
    failed: ['失败', 'text-danger'],
    timeout: ['已超时', 'text-warning'],
    canceled: ['已取消', 'text-text-tertiary']
  };

  /** 运行状态 → 任务状态 id */
  function runStatusToTaskStatus(s) {
    return { running: 'doing', completed: 'done', failed: 'failed', timeout: 'timeout', canceled: 'canceled' }[s] || 'todo';
  }

  function fmtDuration(sec) {
    const v = Number(sec);
    if (!isFinite(v) || v <= 0) return '';
    if (v < 60) return v.toFixed(1) + 's';
    return Math.floor(v / 60) + 'm' + Math.round(v % 60) + 's';
  }

  function fmtWhen(ts) {
    try { return ts ? new Date(ts).toLocaleString('zh-CN') : ''; } catch (e) { return ''; }
  }

  /** 本次运行的估算成本（真实 usage + 成本中心既有单价；拿不到 usage 就返回 null，绝不编造）
   * @param {Object} run - 运行对象
   * @returns {Object|null} {cny, text, title} */
  function runCost(run) {
    const u = run && run.usage;
    if (!u || typeof u.input_tokens !== 'number') return null;
    if (typeof CostEngine === 'undefined') return null;
    const model = (run && run.model) || '';
    const prompt = Number(u.input_tokens) || 0;
    const cache = Number(u.cached_input_tokens) || 0;
    const out = Number(u.output_tokens) || 0;
    const price = CostEngine.resolvePrice(model);
    const cny = CostEngine.estimateCost(model, prompt, out, cache);
    return {
      cny,
      text: '本次估算成本 ' + CostEngine.formatMoney(cny),
      title: '按成本中心价格表估算（非厂商账单）：' + (model || '模型未知') + ' · 单价 ' + price.note +
        ' · 输入 ¥' + price.input + ' / 输出 ¥' + price.output + ' / 缓存命中 ¥' + price.cacheHit + ' 每百万 token' +
        ' · 实际用量：输入 ' + prompt + '（含缓存 ' + cache + '）· 输出 ' + out +
        (u.reasoning_output_tokens ? '（含推理 ' + u.reasoning_output_tokens + '）' : '')
    };
  }

  /** 生成结构化任务指令（含项目路径/背景/要求/验收标准模板）
   * @param {Object} t - 任务对象
   * @returns {string} 指令全文（纯文本，便于直接粘贴到任何 agent 的输入框） */
  function buildInstruction(t) {
    const lines = [];
    lines.push('# 任务：' + (t.name || '（未命名任务）'));
    lines.push('');
    lines.push('## 目标 Agent');
    lines.push(agentName(t.agent));
    lines.push('');
    lines.push('## 项目路径');
    lines.push(t.projectPath ? t.projectPath : '（未填写：请在目标 agent 中打开对应工程目录后执行）');
    lines.push('');
    lines.push('## 背景');
    lines.push(t.background ? t.background : '（未填写）');
    lines.push('');
    lines.push('## 需要完成的事情');
    lines.push(t.requirements ? t.requirements : '（未填写）');
    lines.push('');
    lines.push('## 验收标准');
    lines.push(t.acceptance ? t.acceptance : DEFAULT_ACCEPT);
    lines.push('');
    lines.push('## 交付要求');
    lines.push('- 完成后给出改动文件清单与关键实现说明（文件路径 + 行号）。');
    lines.push('- 给出可复现的运行/自测命令与结果；测试不通过要如实说明。');
    lines.push('- 做不到的部分要诚实写进结论，不要假装完成。');
    return lines.join('\n');
  }

  /** 复制到剪贴板：优先 Clipboard API，非安全上下文回退 textarea + execCommand
   * @param {string} text - 待复制文本
   * @returns {Promise<boolean>} 是否复制成功 */
  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) { /* 继续回退 */ }
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return !!ok;
    } catch (e) { return false; }
  }

  /* ---------------- 注入：导航按钮 + 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    const nav = document.querySelector('.nav-icon-btn[data-page="cost"]') ?
      document.querySelector('.nav-icon-btn[data-page="cost"]').parentElement : null;
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', '任务分派');
    btn.innerHTML = '<i data-lucide="send" class="w-[18px] h-[18px]"></i>';
    nav.appendChild(btn);
    // 骨架的页面切换脚本在解析期就绑定了当时存在的按钮，这里补一个等价的切换逻辑
    btn.addEventListener('click', () => {
      switchTo(PAGE);
      const page = document.getElementById('page-' + PAGE);
      const formCard = page && page.querySelector('#taskFormCard');
      const formOpen = formCard && formCard.style.display !== 'none';
      if (page && !formOpen) buildPage(); // 重新计算「直接执行」入口是否可用（设置里可能刚配好云端 API）
    });
  }

  function ensurePage() {
    if (document.getElementById('page-' + PAGE)) return;
    const ref = document.getElementById('page-cost');
    if (!ref || !ref.parentElement) return;
    const div = document.createElement('div');
    div.id = 'page-' + PAGE;
    div.className = 'page-panel hidden flex-1 flex flex-col min-h-0';
    ref.parentElement.insertBefore(div, ref.nextSibling);
  }

  /** 与骨架一致的页面切换（本页无独立侧栏，故只切 page-panel 与导航激活态） */
  function switchTo(page) {
    document.querySelectorAll('.nav-icon-btn[data-page]').forEach(b => {
      b.classList.toggle('active', b.dataset.page === page);
    });
    document.querySelectorAll('.page-panel').forEach(p => {
      p.classList.add('hidden');
      p.classList.remove('flex');
    });
    const target = document.getElementById('page-' + page);
    if (target) {
      target.classList.remove('hidden');
      target.classList.add('flex');
      void target.offsetWidth;
    }
    document.querySelectorAll('.sidebar-panel').forEach(p => p.classList.add('hidden'));
    const sb = document.getElementById('sidebar-' + page);
    if (sb) { sb.classList.remove('hidden'); sb.classList.add('flex'); }
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 渲染 ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const cloudReady = !!(typeof MoraySettings !== 'undefined' && MoraySettings.get('openaiEnabled') &&
      MoraySettings.get('openaiBaseURL'));
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2">
          <i data-lucide="send" class="w-4 h-4 text-brand-cobalt"></i>
          <span class="text-sm font-medium text-text-primary">任务分派</span>
          <span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt" id="taskCountPill">0 个任务</span>
          <span class="text-[11px] text-text-tertiary" title="真实 A2A 需要对方平台开放接口；本面板负责指令生成、状态管理与「一键自动指挥 Codex（本机 codex CLI）」">指令生成 + 状态管理 + Codex 自动执行</span>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="taskOrchBtn" title="编排工头：把一个大任务拆成子任务，按类型分给 codex / 本地 Ollama 串行执行，最后汇总成本">
            <i data-lucide="network" class="w-3.5 h-3.5"></i>编排计划
          </button>
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="taskClearDoneBtn" title="删除全部已完成任务">
            <i data-lucide="eraser" class="w-3.5 h-3.5"></i>清理已完成
          </button>
          <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5" id="taskNewBtn">
            <i data-lucide="plus" class="w-3.5 h-3.5"></i>新建任务
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-4xl mx-auto space-y-4">
          <div class="rounded-lg border border-line-ghost/60 px-3 py-2 text-[11px] flex items-start gap-2" id="taskBackendHint"></div>

          <div class="glass-card rounded-xl p-4 space-y-3" id="taskFormCard" style="display:none">
            <div class="text-xs font-medium text-text-secondary">新建任务</div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div class="form-row"><label class="form-label">任务名称</label>
                <input type="text" id="taskName" class="form-input" placeholder="例如：给 MoRay 增加壁纸联动主题"></div>
              <div class="form-row"><label class="form-label">目标 Agent</label>
                <select id="taskAgent" class="form-input">${AGENTS.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></div>
              <div class="form-row" id="taskCustomAgentRow" style="display:none"><label class="form-label">自定义 Agent 名称</label>
                <input type="text" id="taskCustomAgent" class="form-input" placeholder="例如：某公司内部 Agent"></div>
              <div class="form-row"><label class="form-label">项目路径</label>
                <input type="text" id="taskProjectPath" class="form-input" placeholder="例如：D:\\ai工具台"></div>
            </div>
            <div class="form-row"><label class="form-label">背景</label>
              <textarea id="taskBackground" class="form-input" rows="2" placeholder="这个任务的来龙去脉、当前状态、相关约束"></textarea></div>
            <div class="form-row"><label class="form-label">需要完成的事情</label>
              <textarea id="taskRequirements" class="form-input" rows="3" placeholder="一条一条写清楚要做的事"></textarea></div>
            <div class="form-row"><label class="form-label">验收标准</label>
              <textarea id="taskAcceptance" class="form-input" rows="3" placeholder="留空则使用默认三条（真实可用 / 通过测试 / 改动清单）"></textarea></div>
            <div class="flex items-center gap-2">
              <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5" id="taskSaveBtn">
                <i data-lucide="check" class="w-3.5 h-3.5"></i>保存并生成指令</button>
              <button class="btn-ghost px-3 h-8 rounded-lg text-xs border border-line-ghost" id="taskCancelBtn">取消</button>
            </div>
          </div>
          <div class="glass-card rounded-xl p-4 space-y-2" id="taskPreviewCard" style="display:none">
            <div class="flex items-center justify-between">
              <div class="text-xs font-medium text-text-secondary">生成的指令</div>
              <button class="btn-ghost px-2.5 h-7 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1" id="taskCopyBtn">
                <i data-lucide="copy" class="w-3 h-3"></i>复制到剪贴板</button>
            </div>
            <textarea id="taskPreview" class="form-input font-mono text-[11px]" rows="12" readonly></textarea>
            <div class="text-[10px] text-text-tertiary">把这段指令整段粘贴到目标 agent 的会话里即可；它包含项目路径、背景、要做的事与验收标准。</div>
          </div>
          <div>
            <h3 class="text-xs font-medium text-text-secondary mb-3">任务列表</h3>
            <div class="space-y-2" id="taskList"></div>
          </div>
        </div>
      </div>`;
    bindForm(cloudReady);
    renderList(cloudReady);
    renderBackendHint();
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /** 顶部后端提示：自动执行必须由本地后端执行（纯静态打开时禁用入口并说明原因） */
  function renderBackendHint() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const el = page.querySelector('#taskBackendHint');
    if (!el) return;
    backendOnline = isBackendOnline();
    if (backendOnline) {
      el.className = 'rounded-lg border border-line-ghost/60 px-3 py-2 text-[11px] flex items-start gap-2';
      el.innerHTML = '<i data-lucide="terminal" class="w-3.5 h-3.5 text-success flex-shrink-0 mt-0.5"></i>' +
        '<span class="text-text-secondary">自动执行已就绪：本地后端在线，MoRay 会在任务的项目目录里拉起 <code class="font-mono">' + esc(cfgCodexPath()) +
        '</code>。命令路径 / 默认工作目录 / 超时秒数在 <b>设置 → Agent 编排</b> 里改。</span>';
    } else {
      el.className = 'rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] flex items-start gap-2';
      el.innerHTML = '<i data-lucide="plug-zap" class="w-3.5 h-3.5 text-warning flex-shrink-0 mt-0.5"></i>' +
        '<span class="text-text-secondary"><b>自动执行需要本地后端</b>：当前后端未连接（纯静态打开或后端没启动）。' +
        '用「启动MoRay.bat」启动后自动可用；其它功能（指令生成 / 复制 / 状态管理）不受影响。</span>';
    }
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  function bindForm(cloudReady) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };
    // [v3.22.0] 编排工头入口（面板由 69_orchestrator.js 注入；未安装时给出明确提示而不是静默失效）
    on(page.querySelector('#taskOrchBtn'), 'click', () => {
      if (window.__taskOrchestrator && typeof window.__taskOrchestrator.open === 'function') {
        window.__taskOrchestrator.open();
      } else if (typeof showNotification === 'function') {
        showNotification('编排面板未就绪', '编排工头分片未安装（请确认构建产物完整：python assemble.py）', 'warning', 3200);
      }
    });
    on(page.querySelector('#taskNewBtn'), 'click', () => {
      const card = page.querySelector('#taskFormCard');
      card.style.display = '';
      const f = state.lastForm || {};
      page.querySelector('#taskName').value = '';
      page.querySelector('#taskAgent').value = f.agent || 'zcode';
      page.querySelector('#taskCustomAgent').value = state.customAgent || '';
      page.querySelector('#taskProjectPath').value = f.projectPath || state.projectPath || cfgWorkdir();
      page.querySelector('#taskBackground').value = '';
      page.querySelector('#taskRequirements').value = '';
      page.querySelector('#taskAcceptance').value = '';
      syncCustomAgentRow();
    });
    on(page.querySelector('#taskCancelBtn'), 'click', () => { page.querySelector('#taskFormCard').style.display = 'none'; });
    on(page.querySelector('#taskAgent'), 'change', syncCustomAgentRow);
    on(page.querySelector('#taskSaveBtn'), 'click', () => {
      const name = page.querySelector('#taskName').value.trim();
      if (!name) { if (typeof showNotification === 'function') showNotification('缺少任务名称', '请先填写任务名称', 'warning', 2500); return; }
      const agent = page.querySelector('#taskAgent').value;
      const custom = page.querySelector('#taskCustomAgent').value.trim();
      const projectPath = page.querySelector('#taskProjectPath').value.trim();
      if (agent === 'custom' && !custom) {
        if (typeof showNotification === 'function') showNotification('缺少自定义 Agent 名称', '请填写自定义 Agent 名称', 'warning', 2500);
        return;
      }
      state.customAgent = custom;
      state.projectPath = projectPath;
      const t = {
        id: uid(), name, agent, projectPath,
        background: page.querySelector('#taskBackground').value.trim(),
        requirements: page.querySelector('#taskRequirements').value.trim(),
        acceptance: page.querySelector('#taskAcceptance').value.trim(),
        status: 'todo', note: '', result: '', createdAt: Date.now(), updatedAt: Date.now()
      };
      state.tasks.unshift(t);
      state.seq += 1;
      state.lastForm = { agent, projectPath };
      const persisted = save();
      page.querySelector('#taskFormCard').style.display = 'none';
      renderList(cloudReady);
      showPreview(t);
      if (typeof showNotification === 'function') {
        showNotification('任务已创建', persisted ? '指令已生成，可一键复制' : '本地存储写入失败（可能空间不足），本次任务仅在当前页面有效', persisted ? 'success' : 'warning', 2600);
      }
    });
    on(page.querySelector('#taskCopyBtn'), 'click', async () => {
      const txt = page.querySelector('#taskPreview').value;
      const ok = await copyText(txt);
      if (typeof showNotification === 'function') {
        showNotification(ok ? '已复制' : '复制失败', ok ? '指令已复制到剪贴板，去粘贴给目标 agent 即可' : '当前环境不允许自动复制，请手动全选文本框内容', ok ? 'success' : 'warning', 2600);
      }
    });
    on(page.querySelector('#taskClearDoneBtn'), 'click', () => {
      const done = state.tasks.filter(t => t.status === 'done');
      if (!done.length) { if (typeof showNotification === 'function') showNotification('没有已完成任务', '先把任务状态标记为「已完成」再清理', 'info', 2200); return; }
      const doClear = () => {
        state.tasks = state.tasks.filter(t => t.status !== 'done');
        save(); renderList(cloudReady);
        if (typeof showNotification === 'function') showNotification('已清理', `删除 ${done.length} 个已完成任务`, 'success', 1800);
      };
      if (typeof showConfirm === 'function') showConfirm('清理已完成任务', `确定删除 ${done.length} 个已完成任务？`, doClear, { okText: '删除' });
      else doClear();
    });
  }

  function syncCustomAgentRow() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const row = page.querySelector('#taskCustomAgentRow');
    const sel = page.querySelector('#taskAgent');
    if (row && sel) row.style.display = (sel.value === 'custom') ? '' : 'none';
  }

  function showPreview(t) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    page.querySelector('#taskPreviewCard').style.display = '';
    page.querySelector('#taskPreview').value = buildInstruction(t);
    page.querySelector('#taskPreview').dataset.taskId = t.id;
  }

  function renderList(cloudReady) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const list = page.querySelector('#taskList');
    const pill = page.querySelector('#taskCountPill');
    if (pill) pill.textContent = state.tasks.length + ' 个任务';
    if (!state.tasks.length) {
      list.innerHTML = `<div class="glass-card rounded-xl p-6 text-center">
        <i data-lucide="clipboard-list" class="w-7 h-7 mx-auto mb-2 text-text-tertiary"></i>
        <div class="text-sm text-text-primary mb-1">还没有任务</div>
        <div class="text-[11px] text-text-tertiary">点右上角「新建任务」：填好名称、目标 agent 与要求，MoRay 会生成一份可直接粘贴的结构化指令。</div>
      </div>`;
      if (typeof refreshIcons === 'function') refreshIcons();
      return;
    }
    list.innerHTML = state.tasks.map(t => {
      const sm = statusMeta(t.status);
      const run = t.run || {};
      const isRunning = run.status === 'running';
      const canPath = !!(t.projectPath && t.projectPath.trim());
      // 自动执行按钮：唯一不满足条件的原因写进 tooltip（不渲染"点不动的假按钮"给用户猜）
      const autoDisabled = isRunning ? false : (!backendOnline || !canPath);
      const autoTip = !backendOnline ? '需要本地后端：用「启动MoRay.bat」启动后可用'
        : !canPath ? '该任务没填项目路径：本功能只在该目录下干活，先在设置里补上或编辑任务'
        : 'MoRay 会在「' + t.projectPath + '」目录下拉起 ' + cfgCodexPath() + ' exec 干这个任务，日志实时回传';
      return `<div class="doc-card p-4" data-task-id="${esc(t.id)}">
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="text-sm text-text-primary flex items-center gap-2 flex-wrap">
              <span class="truncate">${esc(t.name)}</span>
              <span class="tag-pill ${sm.cls}">${esc(sm.name)}</span>
              <span class="tag-pill bg-brand-violet/15 text-brand-violet">${esc(agentName(t.agent))}</span>
            </div>
            <div class="text-[10px] text-text-tertiary mt-0.5">创建于 ${new Date(t.createdAt).toLocaleString('zh-CN')}${t.projectPath ? ' · ' + esc(t.projectPath) : ''}</div>
            ${t.note ? `<div class="text-[11px] text-text-secondary mt-1 whitespace-pre-wrap">${esc(t.note).slice(0, 300)}</div>` : ''}
            ${t.result ? `<div class="text-[11px] text-brand-cyan mt-1 whitespace-pre-wrap max-h-24 overflow-auto">执行结果：${esc(t.result).slice(0, 500)}</div>` : ''}
          </div>
          <div class="flex items-center gap-1.5 flex-shrink-0">
            <select class="h-7 rounded-lg bg-surface-card border border-line-ghost text-[11px] text-text-primary px-1" data-task-op="status">
              ${STATUS.map(s => `<option value="${s.id}" ${s.id === t.status ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}
            </select>
            <button class="tool-btn" data-task-op="copy" title="复制指令"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-task-op="note" title="备注"><i data-lucide="pencil-line" class="w-3.5 h-3.5"></i></button>
            ${cloudReady ? '<button class="tool-btn" data-task-op="run" title="用已配置的云端 API 直接执行"><i data-lucide="play" class="w-3.5 h-3.5 text-brand-cyan"></i></button>' : ''}
            ${isRunning
              ? '<button class="tool-btn" data-task-op="stop" title="停止：杀掉整棵 codex 进程树"><i data-lucide="square" class="w-3.5 h-3.5 text-danger"></i></button>'
              : `<button class="tool-btn" data-task-op="auto" title="${esc(autoTip)}" ${autoDisabled ? 'disabled style="opacity:.4;cursor:not-allowed"' : ''}><i data-lucide="rocket" class="w-3.5 h-3.5 ${autoDisabled ? '' : 'text-brand-cyan'}"></i></button>`}
            <button class="tool-btn" data-task-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
        ${runPanelHtml(t)}
      </div>`;
    }).join('');
    list.querySelectorAll('[data-task-id]').forEach(card => {
      const id = card.dataset.taskId;
      const t = state.tasks.find(x => x.id === id);
      if (!t) return;
      const sel = card.querySelector('[data-task-op="status"]');
      if (sel) sel.addEventListener('change', () => {
        t.status = sel.value;
        t.updatedAt = Date.now();
        save(); renderList(cloudReady);
      });
      card.querySelectorAll('[data-task-op]').forEach(btn => {
        const op = btn.dataset.taskOp;
        if (op === 'status') return;
        btn.addEventListener('click', () => {
          if (op === 'copy') copyText(buildInstruction(t)).then(ok => {
            if (typeof showNotification === 'function') showNotification(ok ? '已复制' : '复制失败', ok ? '指令已复制到剪贴板' : '请手动打开预览后复制', ok ? 'success' : 'warning', 2200);
          });
          if (op === 'note') editNote(t, cloudReady);
          if (op === 'run') runTask(t, cloudReady);
          if (op === 'auto') startAutoRun(t, cloudReady);
          if (op === 'stop') stopAutoRun(t, cloudReady);
          if (op === 'delete') {
            const del = () => {
              stopPoll(t.id);
              state.tasks = state.tasks.filter(x => x.id !== id);
              save(); renderList(cloudReady);
              if (typeof showNotification === 'function') showNotification('已删除', t.name, 'success', 1600);
            };
            if (typeof showConfirm === 'function') showConfirm('删除任务', `确定删除「${t.name}」？`, del, { danger: true, okText: '删除' });
            else del();
          }
        });
      });
    });
    bindRunPanels();
    // 路径/命令存在性校验：异步补一次，只更新对应那张卡的按钮态（不整体重绘，避免打散正在滚动的日志）
    if (backendOnline) state.tasks.forEach(t => {
      if (!t.projectPath || (t.run && t.run.status === 'running')) return;
      const card = page.querySelector('#taskList [data-task-id="' + t.id + '"]');
      const btn = card ? card.querySelector('[data-task-op="auto"]') : null;
      if (!btn) return;
      checkPath(t.projectPath).then(r => {
        if (r.ok) return;
        btn.disabled = true;
        btn.style.opacity = '.4';
        btn.style.cursor = 'not-allowed';
        btn.title = '项目路径/命令不可用：' + r.note;
      });
    });
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- [v3.21.0] 自动执行：日志面板 ---------------- */

  /** 展开中的日志面板（按任务 id 记录，重绘后保持展开态） @type {Object<string, boolean>} */
  const expanded = {};
  /** 轮询活动标记 + 连续失败计数 @type {Object<string, number>} */
  const fails = {};

  function stopPoll(taskId) {
    if (pollers[taskId]) { clearTimeout(pollers[taskId]); }
    delete pollers[taskId];
    delete fails[taskId];
  }

  /** 运行日志面板的 HTML（无运行记录时返回空串，不占位） */
  function runPanelHtml(t) {
    const run = t.run || {};
    if (!run.status) return '';
    const open = !!expanded[t.id] || run.status === 'running';
    const cost = runCost(run);
    const bits = [];
    const lm = RUN_LABEL[run.status] || [run.status, 'text-text-secondary'];
    bits.push('<span class="' + lm[1] + '">' + esc(lm[0]) + '</span>');
    if (run.startedAt) bits.push('<span>开始 ' + esc(fmtWhen(run.startedAt)) + '</span>');
    if (run.durationSec) bits.push('<span>耗时 ' + esc(fmtDuration(run.durationSec)) + '</span>');
    if (run.exitCode !== undefined && run.exitCode !== null) bits.push('<span>退出码 ' + esc(run.exitCode) + '</span>');
    if (cost) bits.push('<span class="text-brand-cyan" title="' + esc(cost.title) + '">' + esc(cost.text) + '</span>');
    else bits.push('<span title="模型侧没有返回 usage 事件，MoRay 不编造数字">成本：未能获取（模型侧未返回 usage）</span>');
    if (run.logPath) bits.push('<span class="font-mono truncate max-w-[220px]" title="' + esc(run.logPath) + '">日志 ' + esc(run.logPath) + '</span>');
    return `<div class="mt-3 rounded-lg border border-line-ghost/60 bg-surface-panel/40" data-run-panel>
      <div class="flex items-center justify-between gap-2 px-3 py-2 flex-wrap">
        <div class="text-[10px] text-text-tertiary flex items-center gap-2 flex-wrap" data-run-bits>${bits.join('<span class="opacity-40">·</span>')}</div>
        <div class="flex items-center gap-1.5">
          <button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" data-run-op="toggle">${open ? '收起日志 ▴' : '展开日志 ▾'}</button>
          <button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" data-run-op="copylog">复制日志</button>
        </div>
      </div>
      ${run.error ? `<div class="px-3 pb-1 text-[10px] text-danger">${esc(run.error)}</div>` : ''}
      ${(run.warnings && run.warnings.length) ? `<div class="px-3 pb-1 text-[10px] text-warning">⚠ ${esc(run.warnings.join('；'))}</div>` : ''}
      <pre data-run-log class="font-mono text-[11px] leading-relaxed whitespace-pre-wrap px-3 pb-3 text-text-secondary overflow-auto${open ? '' : ' hidden'}" style="max-height:288px;word-break:break-all">${esc((t.log || []).join('\n'))}</pre>
    </div>`;
  }

  /** 局部更新（运行中轮询用）：只改这一张卡的徽标与日志，保住滚动位置与展开状态 */
  function updateRunPanel(t) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const card = page.querySelector('#taskList [data-task-id="' + t.id + '"]');
    const panel = card ? card.querySelector('[data-run-panel]') : null;
    if (!panel) { renderList(cloudReadyNow()); return; }   // 面板还不存在（例如刚启动）→ 整体重绘一次
    const pre = panel.querySelector('[data-run-log]');
    if (pre) {
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
      pre.textContent = (t.log || []).join('\n');
      if (atBottom) pre.scrollTop = pre.scrollHeight;   // 自动滚到底（用户往上翻时不打断）
    }
    const bits = panel.querySelector('[data-run-bits]');
    if (bits) {
      const run = t.run || {};
      const cost = runCost(run);
      const lm = RUN_LABEL[run.status] || [run.status, 'text-text-secondary'];
      const out = ['<span class="' + lm[1] + '">' + esc(lm[0]) + '</span>'];
      if (run.startedAt) out.push('<span>开始 ' + esc(fmtWhen(run.startedAt)) + '</span>');
      if (run.durationSec) out.push('<span>耗时 ' + esc(fmtDuration(run.durationSec)) + '</span>');
      if (run.exitCode !== undefined && run.exitCode !== null) out.push('<span>退出码 ' + esc(run.exitCode) + '</span>');
      out.push(cost ? '<span class="text-brand-cyan">' + esc(cost.text) + '</span>'
        : '<span>成本：未能获取（模型侧未返回 usage）</span>');
      bits.innerHTML = out.join('<span class="opacity-40">·</span>');
    }
  }

  /** 绑定日志面板的展开/复制（整体重绘后重新绑） */
  function bindRunPanels() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    page.querySelectorAll('#taskList [data-task-id]').forEach(card => {
      const t = state.tasks.find(x => x.id === card.dataset.taskId);
      if (!t) return;
      const panel = card.querySelector('[data-run-panel]');
      if (!panel) return;
      const toggle = panel.querySelector('[data-run-op="toggle"]');
      const pre = panel.querySelector('[data-run-log]');
      if (toggle && pre) toggle.addEventListener('click', () => {
        const willOpen = pre.classList.contains('hidden');
        pre.classList.toggle('hidden', !willOpen);
        expanded[t.id] = willOpen;
        toggle.textContent = willOpen ? '收起日志 ▴' : '展开日志 ▾';
        if (willOpen) pre.scrollTop = pre.scrollHeight;
      });
      const copyBtn = panel.querySelector('[data-run-op="copylog"]');
      if (copyBtn) copyBtn.addEventListener('click', async () => {
        const head = '任务：' + t.name + '\n运行状态：' + ((t.run || {}).status || '') +
          '\n退出码：' + (((t.run || {}).exitCode === undefined || (t.run || {}).exitCode === null) ? '-' : (t.run || {}).exitCode) +
          '\n耗时：' + (fmtDuration((t.run || {}).durationSec) || '-') + '\n\n';
        const ok = await copyText(head + (t.log || []).join('\n'));
        if (typeof showNotification === 'function') showNotification(ok ? '日志已复制' : '复制失败', ok ? '含状态、退出码、耗时与全部日志行' : '当前环境不允许自动复制', ok ? 'success' : 'warning', 2200);
      });
    });
  }

  /* ---------------- [v3.21.0] 自动执行：启动 / 停止 / 轮询 ---------------- */

  /** 自动执行提示词 = 生成的指令 + 执行上下文说明（手动"复制指令"的文本保持原样不变） */
  function autoPrompt(t) {
    return buildInstruction(t) + '\n\n## 执行方式（MoRay 自动执行）\n' +
      '你正被 MoRay 以非交互方式（codex exec）拉起，工作根就是上面的项目路径。请直接动手完成要求：' +
      '该改文件就改，能跑自测就跑；最后用一段话说明改了什么、结果如何；做不到的部分如实说明，不要假装完成。';
  }

  /** 启动自动执行（唯一入口：用户点击任务卡的 🚀 按钮） */
  async function startAutoRun(t, cloudReady) {
    if (!isBackendOnline()) {
      if (typeof showNotification === 'function') showNotification('需要本地后端', '自动执行由本地后端拉起 codex：请用「启动MoRay.bat」启动后重试', 'warning', 3600);
      return;
    }
    if (!t.projectPath || !t.projectPath.trim()) {
      if (typeof showNotification === 'function') showNotification('缺少项目路径', '本功能只在指定目录下干活：请编辑任务或到设置里填默认工作目录', 'warning', 3600);
      return;
    }
    if (t.id in pollers) return;
    const other = state.tasks.find(x => x.id !== t.id && x.run && x.run.status === 'running');
    if (other) {
      if (typeof showNotification === 'function') showNotification('已有任务在执行', '本产品单机串行：先停止「' + other.name + '」再启动本条', 'warning', 3600);
      return;
    }
    const pathOk = await checkPath(t.projectPath);
    if (!pathOk.ok) {
      if (typeof showNotification === 'function') showNotification('项目路径不可用', pathOk.note || '路径校验未通过', 'warning', 4000);
      return;
    }
    if (typeof showNotification === 'function') showNotification('已提交自动执行', '本地后端正在拉起 ' + cfgCodexPath() + ' …', 'info', 2400);
    const r = await apiCall('POST', '/api/agent/run', {
      task_id: t.id,
      prompt: autoPrompt(t),
      project_path: t.projectPath,
      timeout_sec: cfgTimeout(),
      command: cfgCodexPath()
    }, 20000);
    if (!r.ok) {
      const msg = r.code === 'busy' ? '后端已有任务在执行（单机串行）：请先停止它' : r.message;
      if (typeof showNotification === 'function') showNotification('启动失败', msg, 'error', 4200);
      return;
    }
    const d = r.data || {};
    t.run = {
      runId: d.run_id, status: 'running', startedAt: (d.started_at || 0) * 1000,
      endedAt: null, durationSec: null, exitCode: null, usage: null,
      model: d.model || '', logPath: d.log_path || '', timeoutSec: d.timeout_sec || cfgTimeout(), error: null, warnings: []
    };
    t.log = (d.lines || []).slice(-LOG_KEEP);
    offsets[t.id] = d.total_lines || t.log.length;
    t.status = 'doing';
    t.updatedAt = Date.now();
    expanded[t.id] = true;
    save(); renderList(cloudReady);
    schedulePoll(t.id, cloudReady);
  }

  /** 停止：后端杀整棵进程树 → 状态 canceled */
  async function stopAutoRun(t, cloudReady) {
    const r = await apiCall('POST', '/api/agent/tasks/' + encodeURIComponent(t.id) + '/stop', {}, 10000);
    if (!r.ok) {
      if (typeof showNotification === 'function') showNotification('停止失败', r.message, 'error', 3600);
      return;
    }
    if (typeof showNotification === 'function') showNotification('已发送停止', '正在终止 codex 进程树', 'info', 2400);
    schedulePoll(t.id, cloudReady);   // 兜底：确认状态落地
  }

  /** 轮询增量日志（800ms；连续失败 5 次判定与后端失联，如实标注） */
  function schedulePoll(taskId, cloudReady) {
    if (taskId in pollers) return;
    pollers[taskId] = 1;
    fails[taskId] = 0;
    const tick = async () => {
      if (!(taskId in pollers)) return;
      const t = state.tasks.find(x => x.id === taskId);
      if (!t) { stopPoll(taskId); return; }
      const off = offsets[taskId] || 0;
      const r = await apiCall('GET', '/api/agent/tasks/' + encodeURIComponent(taskId) + '?offset=' + off, undefined, 8000);
      if (!(taskId in pollers)) return;
      if (!r.ok) {
        fails[taskId] = (fails[taskId] || 0) + 1;
        if (fails[taskId] >= 5) {
          t.log = appendLog(t.log, '—— 连续 5 次拉取失败：' + r.message + '（按失联处理，标注为失败）');
          t.run = Object.assign({}, t.run, { status: 'failed', error: '与本地后端失去联系：' + r.message, endedAt: Date.now() });
          finishRun(t, cloudReady, '与本地后端失去联系');
          return;
        }
      } else {
        fails[taskId] = 0;
        const d = r.data || {};
        if (d.lines && d.lines.length) {
          t.log = appendLog(t.log, d.lines.join('\n'));
          offsets[taskId] = d.total_lines || (off + d.lines.length);
        }
        t.run = Object.assign({}, t.run, {
          runId: d.run_id, status: d.status,
          startedAt: (d.started_at || 0) * 1000,
          endedAt: d.ended_at ? d.ended_at * 1000 : null,
          durationSec: d.duration_sec, exitCode: d.exit_code,
          usage: d.usage || null, model: d.model || (t.run || {}).model || '',
          warnings: d.warnings || [], logPath: d.log_path || (t.run || {}).logPath || '',
          error: d.error || null, timeoutSec: d.timeout_sec
        });
        if (d.status !== 'running') { finishRun(t, cloudReady); return; }
        t.status = 'doing';
        t.updatedAt = Date.now();
        save();
        updateRunPanel(t);
      }
      pollers[taskId] = setTimeout(tick, 800);
    };
    pollers[taskId] = setTimeout(tick, 400);
  }

  /** 收尾：状态流转 + 落执行记录 + 重绘（成本/耗时都来自真实回传） */
  function finishRun(t, cloudReady, why) {
    stopPoll(t.id);
    const run = t.run || {};
    t.status = runStatusToTaskStatus(run.status);
    t.updatedAt = Date.now();
    const cost = runCost(run);
    state.records.unshift({
      taskId: t.id, taskName: t.name, runId: run.runId, status: run.status,
      startedAt: run.startedAt, endedAt: run.endedAt, durationSec: run.durationSec,
      exitCode: (run.exitCode === undefined ? null : run.exitCode),
      usage: run.usage || null, model: run.model || '',
      costCNY: cost ? cost.cny : null,
      costNote: cost ? cost.title : '模型侧未返回 usage，未能估算成本（MoRay 不编造数字）',
      logTail: (t.log || []).slice(-LOG_TAIL_RECORD)
    });
    state.records = state.records.slice(0, 50);
    save(); renderList(cloudReady);
    const lm = RUN_LABEL[run.status] || [run.status];
    const costText = cost ? ('估算成本 ' + cost.text.replace('本次估算成本 ', '')) : '成本未能获取（无 usage）';
    if (typeof showNotification === 'function') {
      const type = run.status === 'completed' ? 'success' : (run.status === 'failed' ? 'error' : 'warning');
      showNotification(why ? why : ('自动执行' + lm[0]),
        '「' + t.name + '」' + lm[0] + ' · 耗时 ' + (fmtDuration(run.durationSec) || '-') +
        (run.exitCode === null || run.exitCode === undefined ? '' : ' · 退出码 ' + run.exitCode) + ' · ' + costText,
        type, 4600);
    }
  }

  /** 页面刷新后与后端对齐：
   *  ① 后端仍在跑 → 重新接上日志流（不谎报"中断"）；
   *  ② 后端有更新的真实结果 → 以它为准修正状态；
   *  ③ 后端没有记录 → 保持 load() 里标注的「应用重启导致中断」。 */
  async function reattachRuns(cloudReady) {
    if (!isBackendOnline()) return;
    const r = await apiCall('GET', '/api/agent/runs?limit=50', undefined, 8000);
    if (!r.ok) return;
    const runs = (r.data && r.data.runs) || [];
    let touched = false;
    for (const d of runs) {
      const t = state.tasks.find(x => x.id === d.task_id);
      if (!t) continue;
      const local = t.run || {};
      const backendNewer = !local.startedAt || (d.started_at * 1000) >= local.startedAt - 1000;
      if (d.status === 'running') {
        if (t.id in pollers) continue;
        t.run = Object.assign({}, local, {
          runId: d.run_id, status: 'running', startedAt: (d.started_at || 0) * 1000,
          endedAt: null, durationSec: null, exitCode: null, usage: null,
          model: d.model || '', logPath: d.log_path || '', timeoutSec: d.timeout_sec, error: null, warnings: []
        });
        t.status = 'doing';
        expanded[t.id] = true;
        offsets[t.id] = 0;
        const detail = await apiCall('GET', '/api/agent/tasks/' + encodeURIComponent(t.id) + '?offset=0', undefined, 8000);
        if (detail.ok) {
          const dd = detail.data || {};
          t.log = (dd.lines || []).slice(-LOG_KEEP);
          offsets[t.id] = dd.total_lines || t.log.length;
        }
        schedulePoll(t.id, cloudReady);
        touched = true;
      } else if (backendNewer && (local.status === 'running' || /应用重启导致中断/.test(local.error || ''))) {
        t.run = Object.assign({}, local, {
          runId: d.run_id, status: d.status, startedAt: (d.started_at || 0) * 1000,
          endedAt: d.ended_at ? d.ended_at * 1000 : null, durationSec: d.duration_sec,
          exitCode: d.exit_code, usage: d.usage || null, model: d.model || '',
          logPath: d.log_path || '', error: d.error || null, warnings: d.warnings || []
        });
        t.status = runStatusToTaskStatus(d.status);
        const detail = await apiCall('GET', '/api/agent/tasks/' + encodeURIComponent(t.id) + '?offset=0', undefined, 8000);
        if (detail.ok && detail.data && detail.data.lines) t.log = detail.data.lines.slice(-LOG_KEEP);
        touched = true;
      }
    }
    if (touched) { save(); renderList(cloudReady); }
  }


  function editNote(t, cloudReady) {
    if (typeof showModal !== 'function') return;
    const box = showModal(`
      <div class="form-row"><label class="form-label">备注 / 执行记录</label>
        <textarea id="taskNoteInput" class="form-input" rows="6" placeholder="记录这个任务的进展、阻塞、对方 agent 的回报等">${esc(t.note || '')}</textarea></div>`,
      { title: '任务备注 · ' + t.name, icon: 'pencil-line',
        footer: '<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-note-cancel>取消</button>' +
                '<button class="btn-primary px-4 py-2 rounded-lg text-sm" id="taskNoteSave">保存</button>' });
    const close = typeof closeModal === 'function' ? closeModal : () => {};
    box.querySelector('[data-note-cancel]').addEventListener('click', close);
    box.querySelector('#taskNoteSave').addEventListener('click', () => {
      t.note = box.querySelector('#taskNoteInput').value;
      t.updatedAt = Date.now();
      save(); close(); renderList(cloudReady);
    });
  }

  /** 用已配置的 OpenAI 兼容 API 直接执行该任务（未配置时不渲染入口）
   * @param {Object} t - 任务
   * @param {boolean} cloudReady - 云端 API 是否就绪
   * @returns {Promise<void>} */
  async function runTask(t, cloudReady) {
    if (!cloudReady || running) return;
    if (typeof AI === 'undefined') return;
    running = true;
    if (typeof showNotification === 'function') showNotification('开始执行', '正在调用你配置的云端模型…', 'info', 2200);
    try {
      if (AI.backend !== 'openai') {
        try { await AI.detectBackend(); } catch (e) { /* 忽略 */ }
      }
      if (AI.backend !== 'openai') {
        if (typeof showNotification === 'function') showNotification('云端 API 不可用', '设置里的 OpenAI 兼容接口未连通，请先在设置页测试连接', 'warning', 3200);
        return;
      }
      const model = MoraySettings.get('openaiModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
      const prompt = buildInstruction(t) + '\n\n## 你的输出\n请直接给出这个任务的完整可执行方案与关键实现要点（若超出单次回答能力，请给出分步计划）。';
      const r = await AI.chat({ model, messages: [{ role: 'user', content: prompt }], temperature: 0.2 });
      t.result = String((r && r.content) || '').slice(0, 4000);
      t.status = t.status === 'todo' ? 'doing' : t.status;
      t.updatedAt = Date.now();
      save(); renderList(cloudReady);
      if (typeof showNotification === 'function') showNotification('执行完成', '结果已回填到任务卡片', 'success', 2600);
    } catch (e) {
      if (typeof showNotification === 'function') showNotification('执行失败', String((e && e.message) || e).slice(0, 160), 'error', 3600);
    } finally {
      running = false;
    }
  }

  /* ---------------- [v3.21.0] 设置页：Agent 编排 配置组 ---------------- */

  /** 云端"直接执行"入口是否可用（与 buildPage 中原逻辑一致，抽出来给运行流程复用） */
  function cloudReadyNow() {
    try {
      return !!(typeof MoraySettings !== 'undefined' && MoraySettings.get('openaiEnabled') && MoraySettings.get('openaiBaseURL'));
    } catch (e) { return false; }
  }

  /** 最近用过的项目路径（设置页下拉候选：任务里出现过的 + 上次新建表单填的） */
  function recentPaths() {
    const out = [];
    const push = (p) => {
      const v = String(p || '').trim();
      if (v && out.indexOf(v) === -1) out.push(v);
    };
    state.tasks.forEach(t => push(t.projectPath));
    push(state.projectPath);
    return out.slice(0, 12);
  }

  /** 设置页「Agent 编排」卡（幂等：已存在则先移除再插，保证数值最新） */
  function appendRunSettingsCard() {
    const container = document.querySelector('#page-settings .max-w-2xl');
    if (!container) return;
    const old = container.querySelector('#agentRunSettingsCard');
    if (old) old.remove();
    const codexPath = cfgCodexPath();
    const workdir = cfgWorkdir();
    const timeout = cfgTimeout();
    const online = isBackendOnline();
    const listId = 'agentRunWorkdirList';
    const card = document.createElement('div');
    card.id = 'agentRunSettingsCard';
    card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
    card.innerHTML = [
      '<h3 class="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2"><i data-lucide="rocket" class="w-4 h-4 text-brand-cyan"></i>Agent 编排（一键自动指挥 Codex）</h3>',
      '<p class="text-[10px] text-text-tertiary mb-3">任务分派面板点 🚀 后，由<b>本地后端</b>在该任务的项目目录里拉起 codex CLI 干活：日志实时回传、状态按真实退出码流转、token usage 折算成本。' +
      '参数固定为 <code class="font-mono">exec --json --skip-git-repo-check -s workspace-write -C &lt;项目目录&gt;</code>，指令走 stdin（规避 cmd 转义）；超时/停止会通过 <code class="font-mono">taskkill /T /F</code> 杀掉整棵进程树。</p>',
      online
        ? '<div class="flex items-center gap-1.5 mb-3 text-[10px] text-success"><span class="beacon-dot success"></span>本地后端已连接：自动执行可用</div>'
        : '<div class="flex items-center gap-1.5 mb-3 text-[10px] text-warning"><span class="beacon-dot warn-dot"></span>本地后端离线：自动执行不可用（纯静态打开时按钮置灰）</div>',
      '<div class="space-y-3">',
      '<div><label class="form-label">Codex 命令路径</label>' +
      '<input type="text" id="setAgentRunCodexPath" class="form-input font-mono" style="font-size:12px" value="' + esc(codexPath) + '" placeholder="' + esc(DEFAULT_CODEX_CMD) + ' 或绝对路径">' +
      '<div class="form-hint">默认 <code class="font-mono">codex.cmd</code>（从 PATH 找；也可填绝对路径，例如 C:\\Users\\你\\AppData\\Roaming\\npm\\codex.cmd）</div></div>',
      '<div><label class="form-label">默认工作目录</label>' +
      '<input type="text" id="setAgentRunWorkdir" class="form-input font-mono" style="font-size:12px" list="' + listId + '" value="' + esc(workdir) + '" placeholder="' + esc(DEFAULT_WORKDIR) + '">' +
      '<datalist id="' + listId + '">' + recentPaths().map(p => '<option value="' + esc(p) + '"></option>').join('') + '</datalist>' +
      '<div class="form-hint">新建任务时「项目路径」的默认值（也是「测试命令与路径」的校验目录）；下拉可选最近用过的项目</div></div>',
      '<div class="flex items-center justify-between"><span class="text-xs text-text-secondary">任务超时（秒，10-3600）</span>' +
      '<input type="number" min="10" max="3600" step="10" value="' + timeout + '" id="setAgentRunTimeout" class="form-input" style="width:96px;padding:2px 6px;font-size:12px"></div>',
      '<div class="flex items-center justify-between"><span class="text-xs text-text-secondary">最大并发</span>' +
      '<span class="text-[11px] text-text-tertiary">1（固定 · 单机串行；同时只跑一个任务，第二个请求会被后端拒绝并提示）</span></div>',
      '<div class="flex items-center gap-2 pt-1 border-t border-line-ghost/40">' +
      '<button id="agentRunTestBtn" class="btn-ghost px-3 py-1.5 rounded-lg text-[11px] border border-line-ghost">测试命令与路径</button>' +
      '<span class="text-[10px] text-text-tertiary" id="agentRunTestResult"></span></div>',
      '</div>'
    ].join('');
    container.insertBefore(card, container.querySelector('#agentSettingsCard') || container.lastElementChild);
    if (typeof refreshIcons === 'function') refreshIcons();

    const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };
    const codexInput = card.querySelector('#setAgentRunCodexPath');
    const workdirInput = card.querySelector('#setAgentRunWorkdir');
    const timeoutInput = card.querySelector('#setAgentRunTimeout');
    on(codexInput, 'change', async () => {
      await MoraySettings.set('agentRunCodexPath', codexInput.value.trim());
      Object.keys(pathCache).forEach(k => delete pathCache[k]);
      if (typeof showNotification === 'function') showNotification('已保存', 'Codex 命令路径已更新', 'success', 1600);
    });
    on(workdirInput, 'change', async () => {
      await MoraySettings.set('agentRunWorkdir', workdirInput.value.trim());
      if (typeof showNotification === 'function') showNotification('已保存', '默认工作目录已更新', 'success', 1600);
    });
    on(timeoutInput, 'change', async () => {
      const v = Math.max(10, Math.min(3600, parseInt(timeoutInput.value, 10) || DEFAULT_TIMEOUT));
      timeoutInput.value = v;
      await MoraySettings.set('agentRunTimeoutSec', v);
      if (typeof showNotification === 'function') showNotification('已保存', '任务超时 ' + v + ' 秒', 'success', 1600);
    });
    on(card.querySelector('#agentRunTestBtn'), 'click', async () => {
      const out = card.querySelector('#agentRunTestResult');
      out.textContent = '校验中…';
      const cmd = (codexInput.value || '').trim() || DEFAULT_CODEX_CMD;
      const dir = (workdirInput.value || '').trim() || DEFAULT_WORKDIR;
      const r = await apiCall('GET', '/api/agent/run/preflight?project_path=' + encodeURIComponent(dir) +
        '&command=' + encodeURIComponent(cmd), undefined, 8000);
      if (!r.ok) { out.textContent = '✘ ' + r.message; return; }
      const d = r.data;
      out.textContent = (d.command_ok ? '✓ 命令 ' + d.command_path : '✘ ' + d.command_note) +
        ' · ' + (d.path_ok ? '✓ 目录可用' : '✘ ' + d.path_note) +
        ' · 模型（成本口径）' + (d.model || '未知') + (d.busy ? ' · 当前有任务在执行' : '');
    });
  }

  /* ---------------- 安装 ---------------- */
  function install() {
    load();
    ensureNavButton();
    ensurePage();
    buildPage();
    appendRunSettingsCard();
    // 设置页改了云端 API 后，"直接执行"入口需要跟着出现/消失（不刷新页面也生效）
    window.addEventListener('moray-settings-changed', () => {
      const page = document.getElementById('page-' + PAGE);
      if (page) buildPage();
      appendRunSettingsCard();
    });
    // 后端探测完成后：刷新"自动执行"可用性，并把页面上的运行态与后端真实记录对齐
    window.addEventListener('moray-backend-probed', () => {
      backendOnline = isBackendOnline();
      const page = document.getElementById('page-' + PAGE);
      if (page) buildPage();
      reattachRuns(cloudReadyNow());
    });
    // 首屏若后端已在线，直接对齐一次（覆盖"运行中刷新页面"的场景）
    if (isBackendOnline()) setTimeout(() => reattachRuns(cloudReadyNow()), 600);
  }

  // 延迟到本脚本（含全部 parts）执行完毕再安装：安装时要用到 escapeHtml / refreshIcons /
  // showNotification 等由后续分片定义的绑定，脚本内同步调用会踩到 TDZ。
  function bootInstall() { try { install(); } catch (e) { console.warn('[MoRay] 任务分派面板安装失败:', e); } }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootInstall);
  else setTimeout(bootInstall, 0);

  /** 调试/自检出口（只读状态，便于自动化验证） */
  window.__taskDispatch = {
    page: PAGE,
    state() { return JSON.parse(JSON.stringify(state)); },
    buildInstruction,
    switchTo,
    /** 面板是否已注入到 DOM（导航按钮 + 页面容器都存在） */
    installed() {
      return !!(document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]') && document.getElementById('page-' + PAGE));
    },
    // [v3.21.0] 自动执行：自检出口（自动化验证用；仓库外的调用方也能检查配置与成本口径）
    cfg: () => ({ codexPath: cfgCodexPath(), workdir: cfgWorkdir(), timeoutSec: cfgTimeout(), backendOnline: isBackendOnline() }),
    recentPaths,
    appendRunSettingsCard,
    costOf: (taskId) => {
      const t = state.tasks.find(x => x.id === taskId);
      return t ? runCost(t.run) : null;
    },
    logOf: (taskId) => {
      const t = state.tasks.find(x => x.id === taskId);
      return t ? (t.log || []).join('\n') : '';
    },
    records: () => JSON.parse(JSON.stringify(state.records || []))
  };
})();
