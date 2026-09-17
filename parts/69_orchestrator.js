/* ===================== [3.22.0] 编排工头（大任务 → 子任务 → 分工 → 串行队列 → 成本汇总） =====================
 * 能力边界（诚实说明，不做"假装"功能）：
 *  - 本面板是**真实编排**：子任务由后端用**真模型**（本地 Ollama，免费）拆解出来，用户可逐条
 *    增删改；点「开始执行」后由本地后端**串行**跑队列（codex 工人 = 真 codex exec 子进程；
 *    ollama 工人 = 真调本地 Ollama /api/chat），进度/日志/状态全部来自后端真实回传。
 *  - 成本口径与 v3.21 一致：codex 子任务用**真实 usage** × 成本中心 PRICE_TABLE 单价；
 *    ollama 子任务成本恒为 ¥0（本地推理，真实的 0）；拿不到 usage 就写"未能获取"，**不编数字**。
 *  - 无后端（纯静态打开）时整块面板禁用并说明原因；不做并行 / 子任务依赖 / 定时自动执行。
 *
 * 实现约束：不动 moray-workbench.html 骨架 —— 导航按钮与页面容器由本分片运行时注入，
 * 并自带与骨架一致的页面切换逻辑（与 68 分片同一做法）。分片排在 68 之后（见 assemble.py PARTS）。
 */
(function () {
  const LS_KEY = 'moray_orchestrator_v1';
  const PAGE = 'orchestrator';
  const POLL_MS = 1500;              // 运行时轮询间隔（题目约定）
  const IDLE_POLL_MS = 8000;         // 没有运行中的计划时的低频兜底轮询
  const PATH_TTL = 20000;            // 路径/命令校验缓存
  const LOG_KEEP = 400;              // 本地保留日志行数（防 localStorage 膨胀）
  const PLAN_KEEP = 30;              // 本地保留计划条数
  const OUT_KEEP = 3000;             // 本地快照里保留的输出字符数

  const DEFAULT_CODEX_CMD = 'codex.cmd';
  const DEFAULT_WORKDIR = 'D:\\ai工具台';
  const DEFAULT_TIMEOUT = 600;
  const MAX_SUBTASKS = 12;

  const POOLS = [
    { id: 'both', name: '两者都要（auto 按规则推荐）' },
    { id: 'codex', name: '只用 Codex（会改文件/跑命令，付费）' },
    { id: 'ollama', name: '只用本地 Ollama（免费，只出文字）' }
  ];

  const PLAN_LABEL = {
    pending: ['待执行', 'bg-text-tertiary/15 text-text-tertiary'],
    running: ['执行中', 'bg-brand-cyan/15 text-brand-cyan'],
    completed: ['已完成', 'bg-success/15 text-success'],
    canceled: ['已取消', 'bg-text-tertiary/15 text-text-tertiary'],
    interrupted: ['应用重启导致中断', 'bg-warning/15 text-warning']
  };
  const SUB_LABEL = {
    pending: ['待执行', 'bg-text-tertiary/15 text-text-tertiary'],
    running: ['执行中', 'bg-brand-cyan/15 text-brand-cyan'],
    completed: ['已完成', 'bg-success/15 text-success'],
    failed: ['失败', 'bg-danger/15 text-danger'],
    timeout: ['已超时', 'bg-warning/15 text-warning'],
    canceled: ['已取消', 'bg-text-tertiary/15 text-text-tertiary']
  };
  const WORKER_LABEL = {
    codex: ['Codex', 'bg-brand-violet/15 text-brand-violet', '真 codex exec 子进程（DeepSeek，按真实 usage 计费）'],
    ollama: ['本地', 'bg-brand-cyan/15 text-brand-cyan', '本地 Ollama /api/chat（免费，只做文字工作）']
  };

  /** 本地状态（localStorage 持久化；后端在线时以后端为准） */
  let state = { plans: [], expanded: null, draft: null, form: null };
  /** 日志缓冲（只放内存，不进 localStorage —— 避免把 localStorage 写爆） @type {Object<string,string[]>} */
  let logBufs = {};
  /** 展开中的子任务输出（plan:idx → bool） @type {Object<string,boolean>} */
  const openOutputs = {};
  /** 后端可用性/工人状态缓存 */
  let pf = { online: false, data: null, at: 0 };
  /** 轮询定时器（同一时刻只轮询一个计划） */
  let pollTimer = null;
  let pollPlanId = null;
  /** 日志游标：index = 想读哪个子任务的日志（null = 计划级），offset = 已消费到的总行号 */
  let offsets = { index: null, offset: 0 };
  /** 计划结束时置位：下一轮按 offset=0 把当前子任务的日志**完整**重拉一次（防止中途切换索引漏掉开头几行） */
  let refillPending = false;
  let busyLocal = false;              // 本页面正在发起「启动/停止/创建」请求（防重复点击）

  function uid() { return 'p' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  function esc(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(s == null ? '' : s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function planMeta(id) { return PLAN_LABEL[id] || [id, 'bg-text-tertiary/15 text-text-tertiary']; }
  function subMeta(id) { return SUB_LABEL[id] || [id, 'bg-text-tertiary/15 text-text-tertiary']; }

  /* ---------------- 持久化 ---------------- */

  function trimPlan(p) {
    const o = JSON.parse(JSON.stringify(p || {}));
    (o.subtasks || []).forEach(s => { if (typeof s.output === 'string') s.output = s.output.slice(0, OUT_KEEP); });
    return o;
  }

  function load() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const o = JSON.parse(raw);
      if (o && Array.isArray(o.plans)) {
        state = {
          plans: o.plans.filter(p => p && p.id).slice(0, PLAN_KEEP),
          expanded: typeof o.expanded === 'string' ? o.expanded : null,
          draft: (o.draft && Array.isArray(o.draft.subtasks)) ? o.draft : null,
          form: o.form || null
        };
      }
    } catch (e) { /* 损坏数据不阻塞面板 */ }
    logBufs = {};
    // [诚实标注] 上次关页面时还在"执行中"的计划：先按"与后端失联"兜底，install 后再按后端真实记录校正
    state.plans.forEach(p => {
      if (p.status !== 'running') return;
      p.status = 'interrupted';
      p.note = ((p.note || '') + ' 应用重启导致中断（页面关闭时该计划尚未结束，正在与后端核对）').trim();
    });
  }

  function save() {
    try {
      state.plans = state.plans.slice(0, PLAN_KEEP).map(trimPlan);
      localStorage.setItem(LS_KEY, JSON.stringify(state));   // logBufs 刻意不入库
      return true;
    } catch (e) { return false; }
  }

  /* ---------------- 后端调用（与 68 分片同一套约定） ---------------- */

  function apiBase() {
    if (typeof agentBackendBase === 'function') {
      try { return agentBackendBase(); } catch (e) { /* 继续回退 */ }
    }
    try {
      if (window.MorayBackend && window.MorayBackend.origin) return window.MorayBackend.origin;
    } catch (e) { /* 忽略 */ }
    return 'http://127.0.0.1:8000';
  }

  function isBackendOnline() {
    try { return !!(window.MorayBackend && window.MorayBackend.connected); } catch (e) { return false; }
  }

  async function apiCall(method, path, body, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
    try {
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
          message: (j && (j.message || j.error)) || ('本地后端返回 ' + res.status),
          data: j || null
        };
      }
      if (!j || j.ok !== true) {
        return { ok: false, status: res.status, code: (j && j.code) || 'bad_response',
                 message: (j && (j.message || j.error)) || '后端返回格式异常', data: j || null };
      }
      return { ok: true, status: res.status, data: j.data };
    } catch (e) {
      const aborted = e && e.name === 'AbortError';
      return { ok: false, status: 0, code: aborted ? 'timeout' : 'offline',
               message: aborted ? '请求本地后端超时' : '连不上本地后端（需要启动 MoRay 后端）' };
    } finally {
      clearTimeout(timer);
    }
  }

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

  /** 工人可用性（20s 缓存；force=true 强制刷新） */
  async function preflight(force) {
    if (!isBackendOnline()) { pf = { online: false, data: null, at: Date.now() }; return pf; }
    if (!force && pf.data && Date.now() - pf.at < PATH_TTL) return pf;
    const r = await apiCall('GET', '/api/orchestrator/preflight?command=' + encodeURIComponent(cfgCodexPath()), undefined, 8000);
    pf = r.ok ? { online: true, data: r.data, at: Date.now() }
      : { online: false, data: null, at: Date.now(), error: r.message };
    return pf;
  }

  /* ---------------- 成本（真实来源；拿不到就明说） ---------------- */

  /** 子任务成本：ollama 恒为 ¥0；codex 用真实 usage × 成本中心单价 */
  function subCost(sub, plan) {
    if (sub.effective_worker === 'ollama') {
      return { cny: 0, text: '¥0', local: true, title: '本地 Ollama 推理：不产生 API 费用（成本恒为 ¥0）' };
    }
    const u = sub.usage;
    if (!u || typeof u.input_tokens !== 'number') return null;
    if (typeof CostEngine === 'undefined') return null;
    const model = sub.model || (plan && plan.codex_model) || '';
    const prompt = Number(u.input_tokens) || 0;
    const cache = Number(u.cached_input_tokens) || 0;
    const out = Number(u.output_tokens) || 0;
    const price = CostEngine.resolvePrice(model);
    const cny = CostEngine.estimateCost(model, prompt, out, cache);
    return {
      cny,
      text: CostEngine.formatMoney(cny),
      title: '按成本中心价格表估算（非厂商账单）：' + (model || '模型未知') + ' · 单价 ' + price.note +
        ' · 输入 ¥' + price.input + ' / 输出 ¥' + price.output + ' / 缓存命中 ¥' + price.cacheHit + ' 每百万 token' +
        ' · 实际用量：输入 ' + prompt + '（含缓存 ' + cache + '）· 输出 ' + out
    };
  }

  /** 计划汇总成本：各子任务之和；有取不到 usage 的 codex 子任务时如实标注未计入 */
  function planCost(plan) {
    let total = 0, unknown = 0, codexCny = 0;
    (plan.subtasks || []).forEach(s => {
      const c = subCost(s, plan);
      if (!c) { if (s.status === 'completed' || s.status === 'failed' || s.status === 'timeout') unknown += 1; return; }
      total += c.cny;
      if (s.effective_worker !== 'ollama') codexCny += c.cny;
    });
    return { total, unknown, codexCny, ollamaCny: 0 };
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

  /* ---------------- 注入：导航按钮 + 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    const taskBtn = document.querySelector('.nav-icon-btn[data-page="tasks"]');
    const nav = taskBtn ? taskBtn.parentElement
      : (document.querySelector('.nav-icon-btn[data-page="cost"]') ?
         document.querySelector('.nav-icon-btn[data-page="cost"]').parentElement : null);
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', '编排工头');
    btn.innerHTML = '<i data-lucide="network" class="w-[18px] h-[18px]"></i>';
    nav.appendChild(btn);
    btn.addEventListener('click', () => { switchTo(PAGE); onEnterPage(); });
  }

  function ensurePage() {
    if (document.getElementById('page-' + PAGE)) return;
    const ref = document.getElementById('page-tasks') || document.getElementById('page-cost');
    if (!ref || !ref.parentElement) return;
    const div = document.createElement('div');
    div.id = 'page-' + PAGE;
    div.className = 'page-panel hidden flex-1 flex flex-col min-h-0';
    ref.parentElement.insertBefore(div, ref.nextSibling);
  }

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

  /* ---------------- 页面骨架 ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="network" class="w-4 h-4 text-brand-cyan flex-shrink-0"></i>
          <span class="text-sm font-medium text-text-primary">编排工头</span>
          <span class="tag-pill bg-brand-cyan/15 text-brand-cyan" id="orchPlanPill">0 个计划</span>
          <span class="text-[11px] text-text-tertiary truncate" title="后端用真模型把大任务拆成子任务 → 按类型分配给 codex / 本地 Ollama → 串行执行 → 汇总成本">大任务拆解 · 分工 · 串行队列 · 成本汇总</span>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="orchRefreshBtn" title="重新从后端拉取计划列表与工人状态">
            <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>刷新
          </button>
          <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5" id="orchNewBtn">
            <i data-lucide="plus" class="w-3.5 h-3.5"></i>新建大任务
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-4xl mx-auto space-y-4">
          <div class="rounded-lg border border-line-ghost/60 px-3 py-2 text-[11px] flex items-start gap-2" id="orchBackendHint"></div>

          <div class="glass-card rounded-xl p-4 space-y-3" id="orchFormCard" style="display:none">
            <div class="text-xs font-medium text-text-secondary">新建编排计划</div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div class="form-row"><label class="form-label">大任务名称</label>
                <input type="text" id="orchTitle" class="form-input" placeholder="例如：整理 MoRay 工程的 Python 代码"></div>
              <div class="form-row"><label class="form-label">项目路径</label>
                <input type="text" id="orchProjectPath" class="form-input font-mono" style="font-size:12px"
                  list="orchPathList" placeholder="例如：D:\\ai工具台">
                <datalist id="orchPathList"></datalist></div>
            </div>
            <div class="form-row"><label class="form-label">目标描述（拆解的输入）</label>
              <textarea id="orchGoal" class="form-input" rows="4" placeholder="例如：整理 MoRay 项目里所有 python 文件并总结每个文件的职责，最后给出一份工程结构说明。"></textarea></div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div class="form-row"><label class="form-label">工人池</label>
                <select id="orchPool" class="form-input">${POOLS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></div>
              <div class="form-row"><label class="form-label">失败策略</label>
                <select id="orchFailFast" class="form-input">
                  <option value="0">失败继续跑下一个</option>
                  <option value="1">失败即停（剩余取消）</option>
                </select></div>
              <div class="form-row"><label class="form-label">每个子任务超时（秒）</label>
                <input type="number" min="10" max="3600" step="10" id="orchTimeout" class="form-input"></div>
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5" id="orchDecomposeBtn">
                <i data-lucide="split" class="w-3.5 h-3.5"></i>拆解为子任务</button>
              <span class="text-[10px] text-text-tertiary" id="orchDecomposeHint"></span>
              <button class="btn-ghost px-3 h-8 rounded-lg text-xs border border-line-ghost ml-auto" id="orchFormCancelBtn">取消</button>
            </div>
          </div>

          <div class="glass-card rounded-xl p-4 space-y-3" id="orchDraftCard" style="display:none">
            <div class="flex items-center justify-between gap-2 flex-wrap">
              <div class="text-xs font-medium text-text-secondary" id="orchDraftTitle">拆解结果（可编辑）</div>
              <div class="text-[10px] text-text-tertiary" id="orchDraftMeta"></div>
            </div>
            <div class="space-y-2" id="orchDraftList"></div>
            <div class="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[11px] text-danger" id="orchDecomposeError" style="display:none"></div>
            <div class="flex items-center gap-2 flex-wrap">
              <button class="btn-ghost px-3 h-8 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="orchAddSubBtn">
                <i data-lucide="plus" class="w-3.5 h-3.5"></i>添加子任务</button>
              <span class="text-[10px] text-text-tertiary" id="orchDraftCount"></span>
              <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 ml-auto" id="orchCreateRunBtn">
                <i data-lucide="rocket" class="w-3.5 h-3.5"></i>创建并开始执行</button>
              <button class="btn-ghost px-3 h-8 rounded-lg text-xs border border-line-ghost" id="orchCreateBtn">仅创建计划</button>
              <button class="btn-ghost px-3 h-8 rounded-lg text-xs border border-line-ghost" id="orchDraftCancelBtn">放弃</button>
            </div>
          </div>

          <div>
            <h3 class="text-xs font-medium text-text-secondary mb-3">编排计划</h3>
            <div class="space-y-2" id="orchPlanList"></div>
          </div>
        </div>
      </div>`;
    bindPage();
    renderAll();
  }

  function onEnterPage() {
    if (isBackendOnline()) { preflight(true).then(() => { renderAll(); }); }
    refreshFromBackend();
  }

  /* ---------------- 渲染：后端提示 ---------------- */

  function renderBackendHint() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const el = page.querySelector('#orchBackendHint');
    if (!el) return;
    const d = pf.data;
    if (!isBackendOnline() || !d) {
      el.className = 'rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-[11px] flex items-start gap-2';
      el.innerHTML = '<i data-lucide="plug-zap" class="w-3.5 h-3.5 text-warning flex-shrink-0 mt-0.5"></i>' +
        '<span class="text-text-secondary"><b>编排需要本地后端</b>：当前后端未连接（纯静态打开或后端没启动）。' +
        '用「启动MoRay.bat」启动后端后本面板自动可用；编排的每一环都在本机执行，不会把任务发到云端之外。</span>';
      if (typeof refreshIcons === 'function') refreshIcons();
      return;
    }
    const codex = d.codex || {}, ollama = d.ollama || {};
    const bits = [];
    bits.push(codex.ok
      ? '<span class="text-success">✓ Codex 工人就绪</span><span class="text-text-tertiary font-mono">' + esc(codex.path || '') + '</span>'
      : '<span class="text-danger">✘ Codex 工人不可用：' + esc(codex.note || '') + '</span>');
    bits.push(ollama.ok
      ? '<span class="text-success">✓ 本地 Ollama 就绪（' + (ollama.models || []).length + ' 个模型）</span>'
      : '<span class="text-danger">✘ 本地 Ollama 不可用：' + esc(ollama.note || '') + '</span>');
    if (d.busy) {
      bits.push('<span class="text-warning">⚠ 本机已有' + (d.active_plan_id ? '计划' : '任务') + '在执行（单机串行）：' +
        esc(d.active_plan_title || d.active_task_id || '') + '</span>');
    }
    el.className = 'rounded-lg border border-line-ghost/60 px-3 py-2 text-[11px] flex flex-wrap items-center gap-x-3 gap-y-1';
    el.innerHTML = '<i data-lucide="terminal" class="w-3.5 h-3.5 text-success flex-shrink-0"></i>' +
      '<span class="text-text-tertiary">工人状态：</span>' + bits.join('<span class="opacity-40">·</span>') +
      '<span class="text-text-tertiary ml-auto">拆解用本地模型：' + esc((ollama.decompose_models || []).join(' → ') || 'qwen3.5:9b') + '</span>';
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 渲染：表单 / 拆解草稿 ---------------- */

  function renderForm() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const f = state.form || {};
    const el = (id) => page.querySelector(id);
    if (el('#orchTimeout') && !el('#orchTimeout').value) el('#orchTimeout').value = f.timeoutSec || cfgTimeout();
    if (el('#orchPool') && f.pool) el('#orchPool').value = f.pool;
    if (el('#orchFailFast') && f.failFast !== undefined) el('#orchFailFast').value = f.failFast ? '1' : '0';
    const dl = el('#orchPathList');
    if (dl) dl.innerHTML = recentPaths().map(p => '<option value="' + esc(p) + '"></option>').join('');
  }

  function recentPaths() {
    const out = [];
    const push = (p) => {
      const v = String(p || '').trim();
      if (v && out.indexOf(v) === -1) out.push(v);
    };
    state.plans.forEach(p => push(p.project_path));
    push((state.form || {}).projectPath);
    push(cfgWorkdir());
    return out.slice(0, 12);
  }

  function renderDraft() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const card = page.querySelector('#orchDraftCard');
    if (!card) return;
    const d = state.draft;
    if (!d || !Array.isArray(d.subtasks) || !d.subtasks.length) { card.style.display = 'none'; return; }
    card.style.display = '';
    page.querySelector('#orchDraftTitle').textContent = '拆解结果（可编辑）：' + (d.title || '未命名大任务');
    page.querySelector('#orchDraftMeta').textContent =
      '拆解模型 ' + (d.model || '-') + ' · 耗时 ' + (fmtDuration(d.elapsed_sec) || '-') +
      (d.model_note ? ' · ' + d.model_note : '');
    page.querySelector('#orchDraftCount').textContent = d.subtasks.length + ' 个子任务（最多 ' + MAX_SUBTASKS + ' 个）';
    const list = page.querySelector('#orchDraftList');
    list.innerHTML = d.subtasks.map((s, i) => {
      const pool = d.pool || 'both';
      const locked = pool !== 'both';
      const eff = locked ? pool : (s.worker === 'auto' ? 'auto' : s.worker);
      const effText = locked ? ('工人池已锁定：' + eff)
        : (s.worker === 'auto' ? ('auto → 将用于 ' + recommendHint(s.title, s.detail)) : ('指定 ' + s.worker));
      return `<div class="rounded-lg border border-line-ghost/60 p-3 space-y-2" data-draft-idx="${i}">
        <div class="flex items-center gap-2">
          <span class="text-[10px] text-text-tertiary w-4 flex-shrink-0">${i + 1}</span>
          <input type="text" class="form-input flex-1" style="font-size:12px" data-draft-field="title"
            value="${esc(s.title)}" placeholder="子任务标题（<=20 字）">
          <select class="h-7 rounded-lg bg-surface-card border border-line-ghost text-[11px] text-text-primary px-1"
            data-draft-field="worker" ${locked ? 'disabled style="opacity:.5"' : ''}>
            <option value="auto" ${s.worker === 'auto' ? 'selected' : ''}>auto（推荐）</option>
            <option value="codex" ${s.worker === 'codex' ? 'selected' : ''}>codex</option>
            <option value="ollama" ${s.worker === 'ollama' ? 'selected' : ''}>ollama</option>
          </select>
          <button class="tool-btn" data-draft-op="up" title="上移" ${i === 0 ? 'disabled style="opacity:.3"' : ''}><i data-lucide="arrow-up" class="w-3.5 h-3.5"></i></button>
          <button class="tool-btn" data-draft-op="down" title="下移" ${i === d.subtasks.length - 1 ? 'disabled style="opacity:.3"' : ''}><i data-lucide="arrow-down" class="w-3.5 h-3.5"></i></button>
          <button class="tool-btn" data-draft-op="del" title="删除这个子任务"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
        </div>
        <textarea class="form-input" rows="2" style="font-size:12px" data-draft-field="detail"
          placeholder="具体做什么（1-3 句）">${esc(s.detail)}</textarea>
        <div class="text-[10px] text-text-tertiary" data-draft-eff>${esc(effText)}</div>
      </div>`;
    }).join('');
    list.querySelectorAll('[data-draft-idx]').forEach(row => {
      const i = parseInt(row.dataset.draftIdx, 10);
      row.querySelectorAll('[data-draft-field]').forEach(el => {
        const field = el.dataset.draftField;
        const ev = el.tagName === 'SELECT' ? 'change' : 'input';
        el.addEventListener(ev, () => {
          state.draft.subtasks[i][field] = el.value;
          save();
          if (field !== 'worker') return;
          const hint = row.querySelector('[data-draft-eff]');
          if (hint) {
            hint.textContent = el.value === 'auto'
              ? ('auto → 将用于 ' + recommendHint(state.draft.subtasks[i].title, state.draft.subtasks[i].detail))
              : ('指定 ' + el.value);
          }
        });
      });
      row.querySelectorAll('[data-draft-op]').forEach(btn => {
        const op = btn.dataset.draftOp;
        if (btn.disabled) return;
        btn.addEventListener('click', () => {
          const arr = state.draft.subtasks;
          if (op === 'del') arr.splice(i, 1);
          if (op === 'up' && i > 0) { const t = arr[i - 1]; arr[i - 1] = arr[i]; arr[i] = t; }
          if (op === 'down' && i < arr.length - 1) { const t = arr[i + 1]; arr[i + 1] = arr[i]; arr[i] = t; }
          save(); renderDraft();
        });
      });
    });
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /** 前端展示用的推荐提示（与后端 agent_orchestrator.recommend_worker 同一套规则） */
  const CODE_WORDS = ['代码', '文件', '命令', '修复', '实现', '重构', '测试', '脚本', '配置', '部署', '编译', '调试',
    '修改', '编写', '新增', '创建', '生成', '目录', '工程', '接口', '函数', '数据库', '安装', '运行', '打包', '构建',
    '迁移', '清理', '排查', 'bug', 'python', 'js', 'sql', 'json', 'api'];
  const TEXT_WORDS = ['总结', '解释', '改写', '问答', '翻译', '说明', '概括', '归纳', '描述', '回答', '建议', '评估',
    '分析', '对比', '梳理', '罗列', '整理', '阅读', '讲解', '提炼'];
  function recommendHint(title, detail) {
    const t = (String(title || '') + ' ' + String(detail || '')).toLowerCase();
    if (CODE_WORDS.some(w => t.indexOf(w) >= 0)) return 'codex';
    if (TEXT_WORDS.some(w => t.indexOf(w) >= 0)) return 'ollama';
    return 'codex（无法判断，保守走 codex）';
  }

  /* ---------------- 渲染：计划列表 ---------------- */

  function renderAll() {
    renderBackendHint();
    renderForm();
    renderDraft();
    renderPlans();
  }

  function renderPlans() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const list = page.querySelector('#orchPlanList');
    const pill = page.querySelector('#orchPlanPill');
    if (pill) pill.textContent = state.plans.length + ' 个计划';
    if (!state.plans.length) {
      list.innerHTML = `<div class="glass-card rounded-xl p-6 text-center">
        <i data-lucide="network" class="w-7 h-7 mx-auto mb-2 text-text-tertiary"></i>
        <div class="text-sm text-text-primary mb-1">还没有编排计划</div>
        <div class="text-[11px] text-text-tertiary">点右上角「新建大任务」：写下目标 → MoRay 用本地模型拆成子任务（可逐条改）→ 分配给 codex / 本地 Ollama → 串行执行并汇总成本。</div>
      </div>`;
      if (typeof refreshIcons === 'function') refreshIcons();
      return;
    }
    list.innerHTML = state.plans.map(p => planCardHtml(p)).join('');
    bindPlanCards();
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  function planCardHtml(p) {
    const pm = planMeta(p.status);
    const cost = planCost(p);
    const total = p.subtask_count || (p.subtasks || []).length;
    const done = p.done_count !== undefined ? p.done_count : (p.subtasks || []).filter(s => s.status === 'completed').length;
    const running = p.status === 'running';
    const d = pf.data || {};
    const codexOk = !!(d.codex && d.codex.ok);
    // 「开始执行」不可用的唯一原因写进 tooltip（不渲染"点不动的假按钮"让用户猜）
    let runTip = '串行执行全部子任务：codex 子任务会拉起真 codex 进程，本地子任务调用 Ollama';
    let runDisabled = false;
    if (busyLocal) { runDisabled = true; runTip = '正在提交请求…'; }
    else if (!isBackendOnline()) { runDisabled = true; runTip = '需要本地后端：用「启动MoRay.bat」启动后可用'; }
    else if (running) { runDisabled = true; runTip = '该计划正在执行中'; }
    else if (p.status === 'completed' || p.status === 'interrupted') {
      runDisabled = true;
      runTip = p.status === 'completed' ? '该计划已执行完成；要重跑请新建计划（避免重复执行同一子任务）'
        : '该计划在后端重启时中断，不能续跑；请新建计划';
    } else if (d.busy && d.active_plan_id !== p.id) {
      runDisabled = true;
      runTip = '本机已有' + (d.active_plan_id ? '计划' : '任务') + '在执行（单机串行）：请先停止它';
    } else if (!codexOk && needsCodex(p)) {
      runDisabled = true;
      runTip = 'Codex 工人不可用：' + ((d.codex && d.codex.note) || '未找到 codex 命令') + '（可改工人池或修好 codex）';
    }
    const runnable = ['pending', 'canceled'].indexOf(p.status) >= 0;
    const expanded = state.expanded === p.id;
    return `<div class="doc-card p-4" data-plan-id="${esc(p.id)}">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="text-sm text-text-primary flex items-center gap-2 flex-wrap">
            <span class="truncate">${esc(p.title)}</span>
            <span class="tag-pill ${pm[1]}">${esc(pm[0])}</span>
            <span class="tag-pill bg-brand-violet/15 text-brand-violet">进度 ${done}/${total}</span>
            <span class="tag-pill bg-text-tertiary/15 text-text-tertiary">${esc(poolName(p.worker_pool))}</span>
            ${p.fail_fast ? '<span class="tag-pill bg-warning/15 text-warning">失败即停</span>' : ''}
          </div>
          <div class="text-[10px] text-text-tertiary mt-0.5 truncate">
            创建于 ${esc(fmtWhen((p.created_at || 0) * 1000))}${p.project_path ? ' · ' + esc(p.project_path) : ''}
            ${p.started_at ? ' · 开始 ' + esc(fmtWhen(p.started_at * 1000)) : ''}
            ${p.duration_sec ? ' · 总耗时 ' + esc(fmtDuration(p.duration_sec)) : ''}
            · 总成本 ${costHtml(p)}
          </div>
          ${p.note ? `<div class="text-[11px] text-text-secondary mt-1 whitespace-pre-wrap">${esc(p.note)}</div>` : ''}
        </div>
        <div class="flex items-center gap-1.5 flex-shrink-0">
          <button class="tool-btn" data-plan-op="toggle" title="${expanded ? '收起子任务与日志' : '展开子任务与日志'}">
            <i data-lucide="${expanded ? 'chevron-up' : 'chevron-down'}" class="w-3.5 h-3.5"></i></button>
          <button class="tool-btn" data-plan-op="copy" title="复制这个计划的汇总（含子任务清单与成本）"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
          ${running
            ? '<button class="tool-btn" data-plan-op="stop" title="停止：杀掉当前子任务进程树，未开始的子任务标记取消"><i data-lucide="square" class="w-3.5 h-3.5 text-danger"></i></button>'
            : `<button class="tool-btn" data-plan-op="run" title="${esc(runTip)}" ${(runDisabled || !runnable) ? 'disabled style="opacity:.4;cursor:not-allowed"' : ''}><i data-lucide="rocket" class="w-3.5 h-3.5 ${(runDisabled || !runnable) ? '' : 'text-brand-cyan'}"></i></button>`}
          <button class="tool-btn" data-plan-op="forget" title="从本页面移除这条记录（不动后端数据）"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
        </div>
      </div>
      ${expanded ? planDetailHtml(p) : ''}
    </div>`;
  }

  function needsCodex(p) {
    return (p.subtasks || []).some(s => (s.effective_worker || s.worker) === 'codex');
  }

  function poolName(id) {
    return ({ both: '工人池：自动', codex: '工人池：仅 Codex', ollama: '工人池：仅本地' })[id] || ('工人池：' + id);
  }

  function costHtml(p) {
    const c = planCost(p);
    if (!c.total && !c.unknown) return '¥0';
    const parts = [CostEngineText(c.total)];
    if (c.unknown) parts.push('<span class="text-warning">' + c.unknown + ' 个子任务成本未能获取</span>');
    return parts.join(' ');
  }

  function CostEngineText(cny) {
    if (typeof CostEngine !== 'undefined') return CostEngine.formatMoney(cny);
    return '¥' + Number(cny || 0).toFixed(4);
  }

  function planDetailHtml(p) {
    const subs = p.subtasks || [];
    const c = planCost(p);
    const rows = subs.map(s => {
      const sm = subMeta(s.status);
      const w = WORKER_LABEL[s.effective_worker] || WORKER_LABEL.codex;
      const sc = subCost(s, p);
      const out = String(s.output || '');
      const openKey = (state.expanded === p.id) && openOutputs[p.id + ':' + s.idx];
      return `<div class="rounded-lg border border-line-ghost/60 p-3" data-sub-idx="${s.idx}">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0 flex-1">
            <div class="text-[12px] text-text-primary flex items-center gap-2 flex-wrap">
              <span class="text-text-tertiary text-[10px]">#${s.idx + 1}</span>
              <span>${esc(s.title)}</span>
              <span class="tag-pill ${w[1]}" title="${esc(w[2])}">${esc(w[0])}</span>
              <span class="tag-pill ${sm[1]}">${esc(sm[0])}</span>
              ${s.elapsed_sec ? '<span class="text-[10px] text-text-tertiary">耗时 ' + esc(fmtDuration(s.elapsed_sec)) + '</span>' : ''}
              ${sc ? '<span class="text-[10px] text-brand-cyan" title="' + esc(sc.title) + '">' + esc(sc.text) + '</span>'
                   : '<span class="text-[10px] text-warning" title="模型侧没有返回 usage 事件，MoRay 不编造数字">成本未能获取</span>'}
            </div>
            ${s.detail ? `<div class="text-[10px] text-text-tertiary mt-0.5 whitespace-pre-wrap">${esc(String(s.detail).slice(0, 400))}</div>` : ''}
            ${s.error ? `<div class="text-[10px] text-danger mt-0.5">${esc(s.error)}</div>` : ''}
            ${(s.changed_files && s.changed_files.length) ? `<div class="text-[10px] text-text-secondary mt-0.5">改动文件：${esc(s.changed_files.map(f => f.path).join('、'))}</div>` : ''}
            ${s.local_tokens ? `<div class="text-[10px] text-text-tertiary mt-0.5">本地模型真实用量（不计费）：输入 ${esc(s.local_tokens.prompt_eval_count)} / 输出 ${esc(s.local_tokens.eval_count)} tokens</div>` : ''}
          </div>
          <div class="flex items-center gap-1.5 flex-shrink-0">
            ${out ? `<button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" data-sub-op="out">${openKey ? '收起输出 ▴' : '查看输出 ▾'}</button>` : ''}
            ${s.log_path ? `<button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" data-sub-op="logpath" title="${esc(s.log_path)}">日志路径</button>` : ''}
          </div>
        </div>
        ${openKey ? `<pre class="font-mono text-[11px] leading-relaxed whitespace-pre-wrap mt-2 text-text-secondary overflow-auto" style="max-height:260px;word-break:break-all">${esc(out)}</pre>` : ''}
      </div>`;
    }).join('');
    const logIdx = currentLogIndex(p);
    const logLines = logBufs[p.id] || [];
    return `<div class="mt-3 space-y-2">
      <div class="rounded-lg border border-line-ghost/60 p-3 space-y-2">
        <div class="text-[11px] text-text-secondary font-medium">子任务（按执行顺序串行）</div>
        ${rows || '<div class="text-[11px] text-text-tertiary">没有子任务</div>'}
      </div>
      <div class="rounded-lg border border-brand-cyan/25 bg-brand-cyan/5 p-3">
        <div class="text-[11px] text-text-secondary font-medium mb-1">汇总</div>
        <div class="text-[11px] text-text-secondary flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>总耗时 <b>${esc(fmtDuration(p.duration_sec) || '-')}</b></span>
          <span>总成本 <b class="text-brand-cyan">${CostEngineText(c.total)}</b></span>
          <span>Codex 子任务 ${CostEngineText(c.codexCny)}</span>
          <span>本地 Ollama ¥0（本地推理）</span>
          <span>完成 ${p.done_count || 0}/${p.subtask_count || 0}</span>
          <span class="${(p.failed_count || 0) ? 'text-danger' : ''}">失败 ${p.failed_count || 0}</span>
          <span>取消 ${p.canceled_count || 0}</span>
        </div>
        ${c.unknown ? '<div class="text-[10px] text-warning mt-1">有 ' + c.unknown + ' 个 codex 子任务没拿到 usage，未计入总成本（MoRay 不编造金额）。</div>' : ''}
        ${p.fail_fast_tripped ? '<div class="text-[10px] text-warning mt-1">失败即停已生效：剩余子任务未执行。</div>' : ''}
      </div>
      <div class="rounded-lg border border-line-ghost/60 bg-surface-panel/40">
        <div class="flex items-center justify-between gap-2 px-3 py-2 flex-wrap">
          <div class="text-[10px] text-text-tertiary">
            ${logIdx === null ? '执行日志（计划级）' : '执行日志 · 子任务 #' + (logIdx + 1)}
            ${p.status === 'running' ? ' · 运行中实时刷新（1.5s）' : ' · 已结束'}
          </div>
          <button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" data-plan-op="copylog">复制日志</button>
        </div>
        <pre data-plan-log class="font-mono text-[11px] leading-relaxed whitespace-pre-wrap px-3 pb-3 text-text-secondary overflow-auto" style="max-height:288px;word-break:break-all">${esc(logLines.join('\n'))}</pre>
      </div>
    </div>`;
  }

  /** 日志区显示哪个子任务：运行中跟随 current_index，否则留在最后跑过的那个 */
  function currentLogIndex(p) {
    if (p.current_index !== null && p.current_index !== undefined) return p.current_index;
    const subs = p.subtasks || [];
    for (let i = subs.length - 1; i >= 0; i--) {
      if (subs[i].status && subs[i].status !== 'pending') return subs[i].idx;
    }
    return null;
  }

  /** 结构指纹：状态/进度/耗时没变时不要整体重绘（保住日志滚动位置与展开态） */
  function planSig(p) {
    return (p.subtasks || []).map(s => s.status).join(',') + '|' + p.status + '|' +
      (p.duration_sec || 0) + '|' + (p.total_cost_marker || '');
  }

  /** 原地更新日志（不重绘整页）：用户没往上翻时自动滚到底 */
  function updateLogInPlace(planId) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return false;
    const pre = page.querySelector('#orchPlanList [data-plan-id="' + planId + '"] [data-plan-log]');
    if (!pre) return false;
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 24;
    pre.textContent = (logBufs[planId] || []).join('\n');
    if (atBottom) pre.scrollTop = pre.scrollHeight;
    return true;
  }

  /* ---------------- 绑定 ---------------- */

  function bindPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };
    on(page.querySelector('#orchNewBtn'), 'click', () => openForm());
    on(page.querySelector('#orchFormCancelBtn'), 'click', () => {
      page.querySelector('#orchFormCard').style.display = 'none';
    });
    on(page.querySelector('#orchRefreshBtn'), 'click', async () => {
      await preflight(true);
      await refreshFromBackend(true);
      if (typeof showNotification === 'function') showNotification('已刷新', '计划列表与工人状态已与后端对齐', 'success', 1800);
    });
    on(page.querySelector('#orchDecomposeBtn'), 'click', doDecompose);
    on(page.querySelector('#orchAddSubBtn'), 'click', () => {
      if (!state.draft) return;
      if (state.draft.subtasks.length >= MAX_SUBTASKS) {
        if (typeof showNotification === 'function') showNotification('数量已达上限', '最多 ' + MAX_SUBTASKS + ' 个子任务', 'warning', 2600);
        return;
      }
      state.draft.subtasks.push({ title: '', detail: '', worker: 'auto' });
      save(); renderDraft();
    });
    on(page.querySelector('#orchCreateBtn'), 'click', () => createPlan(false));
    on(page.querySelector('#orchCreateRunBtn'), 'click', () => createPlan(true));
    on(page.querySelector('#orchDraftCancelBtn'), 'click', () => {
      state.draft = null; save(); renderDraft();
    });
  }

  function openForm() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const card = page.querySelector('#orchFormCard');
    card.style.display = '';
    const f = state.form || {};
    page.querySelector('#orchTitle').value = f.title || '';
    page.querySelector('#orchGoal').value = f.goal || '';
    page.querySelector('#orchProjectPath').value = f.projectPath || cfgWorkdir();
    page.querySelector('#orchPool').value = f.pool || 'both';
    page.querySelector('#orchFailFast').value = f.failFast ? '1' : '0';
    page.querySelector('#orchTimeout').value = f.timeoutSec || cfgTimeout();
    renderForm();
    if (!isBackendOnline()) {
      if (typeof showNotification === 'function') showNotification('需要本地后端', '拆解与执行都由本地后端完成：请先启动 MoRay 后端', 'warning', 3600);
    } else {
      preflight(false).then(() => renderAll());
    }
    card.scrollIntoView({ block: 'nearest' });
  }

  /* ---------------- 拆解 ---------------- */

  async function doDecompose() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const title = page.querySelector('#orchTitle').value.trim();
    const goal = page.querySelector('#orchGoal').value.trim();
    const projectPath = page.querySelector('#orchProjectPath').value.trim();
    const pool = page.querySelector('#orchPool').value;
    const failFast = page.querySelector('#orchFailFast').value === '1';
    const timeoutSec = Math.max(10, Math.min(3600, parseInt(page.querySelector('#orchTimeout').value, 10) || cfgTimeout()));
    if (!isBackendOnline()) {
      notify2('需要本地后端', '拆解由本地后端调用本机 Ollama 完成：请先启动 MoRay 后端', 'warning', 4000);
      return;
    }
    if (!goal) {
      notify2('缺少目标描述', '先在「目标描述」里写清楚这个大任务要做什么，MoRay 才能拆解', 'warning', 3600);
      return;
    }
    const d = pf.data || {};
    if (d.ollama && !d.ollama.ok) {
      notify2('本地 Ollama 不可用', '拆解必须由本地模型完成（' + (d.ollama.note || '连接失败') + '）：请先启动 Ollama 后重试', 'error', 5000);
      return;
    }
    state.form = { title, goal, projectPath, pool, failFast, timeoutSec };
    save();
    const btn = page.querySelector('#orchDecomposeBtn');
    const hint = page.querySelector('#orchDecomposeHint');
    btn.disabled = true; btn.style.opacity = '.5';
    hint.textContent = '正在用本地模型拆解（首次加载模型可能较慢，最多等 180s）…';
    hideDecomposeError();
    let r = null;
    try {
      r = await apiCall('POST', '/api/orchestrator/decompose', {
        goal, project_path: projectPath, ollama_url: (pf.data && pf.data.ollama && pf.data.ollama.url) || ''
      }, 190000);
    } finally {
      btn.disabled = false; btn.style.opacity = '';
      hint.textContent = '';
    }
    if (!r.ok) {
      showDecomposeError('拆解失败：' + r.message, r.data);
      notify2('拆解失败', r.code === 'ollama_unavailable' ? '本地 Ollama 连不上，拆解未开始（没有静默降级）' : '详见页面上的错误详情', 'error', 5200);
      return;
    }
    const dd = r.data || {};
    state.draft = {
      title: title || (goal.slice(0, 30)),
      goal, projectPath, pool, failFast, timeoutSec,
      model: dd.model || '', model_note: dd.model_note || '', elapsed_sec: dd.elapsed_sec || 0,
      raw: dd.raw || '', installed: dd.installed_models || [],
      subtasks: (dd.subtasks || []).map(s => ({ title: s.title, detail: s.detail, worker: s.worker }))
    };
    save();
    renderDraft();
    notify2('拆解完成', '本地模型 ' + (dd.model || '') + ' 拆出 ' + state.draft.subtasks.length + ' 个子任务（可逐条修改后再执行）', 'success', 3600);
  }

  function showDecomposeError(msg, data) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const card = page.querySelector('#orchDraftCard');
    const box = page.querySelector('#orchDecomposeError');
    card.style.display = '';
    box.style.display = '';
    const attempts = (data && data.attempts) || [];
    const details = attempts.map(a => '模型 ' + a.model + '：' + a.error +
      (a.raw ? '\n原始输出：' + String(a.raw).slice(0, 800) : '')).join('\n\n');
    box.innerHTML = '<div class="font-medium mb-1">' + esc(msg) + '</div>' +
      (details ? '<pre class="font-mono text-[10px] whitespace-pre-wrap max-h-40 overflow-auto">' + esc(details) + '</pre>'
        : '<div class="text-[10px]">后端没有返回可展示的原始输出。</div>');
  }

  function hideDecomposeError() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const box = page.querySelector('#orchDecomposeError');
    if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  }

  /* ---------------- 创建 / 执行 / 停止 ---------------- */

  async function createPlan(autoRun) {
    if (busyLocal) return;
    const d = state.draft;
    if (!d) return;
    const subs = (d.subtasks || []).filter(s => String(s.title || '').trim() || String(s.detail || '').trim());
    if (!subs.length) {
      notify2('没有子任务', '至少保留一个子任务才能创建计划', 'warning', 3000);
      return;
    }
    if (!isBackendOnline()) {
      notify2('需要本地后端', '计划必须由本地后端创建与执行', 'warning', 3600);
      return;
    }
    let created = null;
    busyLocal = true;
    try {
      const r = await apiCall('POST', '/api/orchestrator/plans', {
        title: d.title || d.goal.slice(0, 30),
        goal: d.goal,
        project_path: d.projectPath || '',
        worker_pool: d.pool || 'both',
        fail_fast: !!d.failFast,
        timeout_sec: d.timeoutSec || cfgTimeout(),
        command: cfgCodexPath(),
        decompose_model: d.model || '',
        subtasks: subs.map(s => ({ title: s.title, detail: s.detail, worker: s.worker }))
      }, 20000);
      if (!r.ok) {
        notify2('创建计划失败', r.message, 'error', 5000);
        return;
      }
      created = r.data;
      state.plans = [trimPlan(created)].concat(state.plans.filter(x => x.id !== created.id));
      state.expanded = created.id;
      state.draft = null;
      const page = document.getElementById('page-' + PAGE);
      if (page) { page.querySelector('#orchFormCard').style.display = 'none'; hideDecomposeError(); }
      save(); renderAll();
      notify2('计划已创建', '「' + created.title + '」共 ' + created.subtask_count + ' 个子任务', 'success', 2600);
      await preflight(true);
    } finally {
      busyLocal = false;
    }
    // 注意：必须在放开 busyLocal 之后再启动队列（runPlan 有同一把防重入闸）
    if (autoRun && created) await runPlan(created.id);
  }

  async function runPlan(planId) {
    if (busyLocal) return;
    const plan = state.plans.find(p => p.id === planId);
    if (!plan) return;
    busyLocal = true;
    try {
      const r = await apiCall('POST', '/api/orchestrator/plans/' + encodeURIComponent(planId) + '/run', {}, 20000);
      if (!r.ok) {
        notify2('启动失败', r.code === 'busy' ? '本机已有任务/计划在执行（单机串行）：请先停止它' : r.message, 'error', 5000);
        return;
      }
      mergePlan(r.data);
      state.expanded = planId;
      save(); renderAll();
      notify2('开始执行', '串行队列已启动：一个子任务跑完（无论成败）→ 下一个', 'info', 3200);
      await preflight(true);
      startPoll(planId);
    } finally {
      busyLocal = false;
    }
  }

  async function stopPlan(planId) {
    const plan = state.plans.find(p => p.id === planId);
    const doStop = async () => {
      const r = await apiCall('POST', '/api/orchestrator/plans/' + encodeURIComponent(planId) + '/stop', {}, 15000);
      if (!r.ok) { notify2('停止失败', r.message, 'error', 4000); return; }
      mergePlan(r.data);
      save(); renderAll();
      notify2('已发送停止', '当前子任务进程树正在被终止，未开始的子任务标记为取消', 'info', 3200);
      // 停止会往当前子任务的日志里追加若干行（用户点击停止 / taskkill / 收尾），
      // 增量游标在这种"中途插入"的场景下容易错位 → 强制按 offset=0 完整重拉一次，保证日志不漏行
      logBufs[planId] = [];
      offsets = { index: offsets.index, offset: 0 };
      refillPending = true;
      startPoll(planId);
    };
    if (typeof showConfirm === 'function') {
      showConfirm('停止编排计划', '确定停止「' + ((plan && plan.title) || '') + '」？\n当前子任务会被终止（codex 走 taskkill 进程树），未开始的子任务标记为已取消。', doStop, { danger: true, okText: '停止' });
    } else { await doStop(); }
  }

  function mergePlan(p) {
    if (!p || !p.id) return;
    const i = state.plans.findIndex(x => x.id === p.id);
    const merged = trimPlan(p);
    if (i >= 0) state.plans[i] = merged; else state.plans.unshift(merged);
  }

  /* ---------------- 轮询 ---------------- */

  function startPoll(planId, immediate) {
    if (pollPlanId !== planId) {
      offsets = { index: null, offset: 0 };
      logBufs[planId] = [];
      refillPending = false;
    }
    pollPlanId = planId;
    if (pollTimer) return;
    const tick = async () => {
      pollTimer = null;
      const id = pollPlanId;
      if (!id) return;
      const before = state.plans.find(p => p.id === id);
      const sigBefore = before ? planSig(before) : '';
      const asked = offsets.index;                       // 本次要读的日志索引（null = 计划级）
      const reqOffset = offsets.offset || 0;             // 本次请求实际用的偏移（复用前要判断它是否还适用）
      const url = '/api/orchestrator/plans/' + encodeURIComponent(id) +
        '?offset=' + reqOffset + '&log_index=' + (asked === null ? -1 : asked);
      const r = await apiCall('GET', url, undefined, 8000);
      if (!r.ok || !r.data) {
        // 拉取失败不谎报状态：等下一轮再试（连续失败交给上层提示）
        if (pollPlanId === id) pollTimer = setTimeout(tick, POLL_MS);
        return;
      }
      const d = r.data;
      const lg = d.log || {};
      const got = (lg.subtask_index === undefined) ? null : lg.subtask_index;
      if (got !== asked) {
        if (got === null && asked !== null) {
          // 计划刚结束：后端 current_index 已清空 → 继续钉在原索引上读（后端会按 log_index 回放）
        } else {
          offsets = { index: got, offset: 0 };           // 换子任务 → 日志重新从头拉
          logBufs[id] = [];
          if (reqOffset !== 0) {
            // 这份响应是用**旧游标**取回的，不能拿来填新索引的缓冲（否则开头几行永久丢失，
            // 而游标随后会被推进到 total，再也补不回来）→ 丢弃并立刻按新游标重拉一次
            pollTimer = setTimeout(tick, 250);
            return;
          }
        }
      }
      if (lg.lines && lg.lines.length) {
        const buf = (logBufs[id] || []).concat(lg.lines);
        logBufs[id] = buf.length > LOG_KEEP ? buf.slice(buf.length - LOG_KEEP) : buf;
        offsets.offset = lg.total_lines || (reqOffset + lg.lines.length);
      }
      const snapshot = trimPlan(d);
      delete snapshot.log;
      mergePlan(snapshot);
      save();
      if (state.expanded === id && sigBefore === planSig(d)) updateLogInPlace(id);
      else renderPlans();
      const finishedNow = before && before.status === 'running' && d.status !== 'running';
      if (finishedNow) {
        onPlanFinished(d);
        preflight(true).then(() => renderAll());
        // 收尾：把当前正在看的子任务日志按 offset=0 完整重拉一次（保证用户看到的是完整日志）
        logBufs[id] = [];
        offsets = { index: offsets.index, offset: 0 };
        refillPending = true;
      }
      if (!pollPlanId) return;
      if (refillPending) {
        refillPending = false;
        pollTimer = setTimeout(tick, 250);
        return;
      }
      const stillRunning = d.status === 'running';
      if (!stillRunning && state.expanded !== id && !state.plans.some(p => p.status === 'running')) {
        pollPlanId = null;
        return;
      }
      pollTimer = setTimeout(tick, stillRunning ? POLL_MS : IDLE_POLL_MS);
    };
    pollTimer = setTimeout(tick, immediate === false ? POLL_MS : 200);
  }

  function onPlanFinished(d) {
    const cost = planCost(d);
    const failed = d.failed_count || 0;
    const done = d.done_count || 0;
    notify2(failed ? '编排结束（有失败）' : '编排完成',
      '「' + d.title + '」完成 ' + done + '/' + (d.subtask_count || 0) + ' 个子任务' +
      (failed ? '（' + failed + ' 个失败）' : '') +
      ' · 总耗时 ' + (fmtDuration(d.duration_sec) || '-') + ' · 总成本 ' + CostEngineText(cost.total) +
      (cost.unknown ? '（' + cost.unknown + ' 个子任务成本未能获取）' : ''),
      failed ? 'warning' : 'success', 6000);
  }

  /** 刷新后与后端对齐：列表以后端为准；后端仍在跑 → 重新接上日志流（不谎报中断） */
  async function refreshFromBackend(force) {
    if (!isBackendOnline()) { renderAll(); return; }
    const r = await apiCall('GET', '/api/orchestrator/plans?limit=30', undefined, 8000);
    if (!r.ok) { renderAll(); return; }
    const plans = (r.data && r.data.plans) || [];
    const localById = {};
    state.plans.forEach(p => { localById[p.id] = p; });
    state.plans = plans.map(p => {
      const local = localById[p.id];
      const merged = trimPlan(p);
      if (local && local.status === 'running' && p.status !== 'running') {
        // 后端给了终态：以后端为准（避免页面停留在"运行中"）
        merged.note = p.note || '';
      }
      return merged;
    });
    const activeId = (r.data && r.data.active_plan_id) || null;
    if (activeId) {
      const active = state.plans.find(p => p.id === activeId);
      if (active && active.status === 'running') { state.expanded = activeId; startPoll(activeId); }
    }
    save(); renderAll();
  }

  /* ---------------- 事件委托（计划卡 / 子任务） ---------------- */

  function bindPlanCards() {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    page.querySelectorAll('#orchPlanList [data-plan-id]').forEach(card => {
      const id = card.dataset.planId;
      const p = state.plans.find(x => x.id === id);
      if (!p) return;
      card.querySelectorAll('[data-plan-op]').forEach(btn => {
        const op = btn.dataset.planOp;
        if (btn.disabled && op !== 'toggle' && op !== 'copy' && op !== 'forget') return;
        btn.addEventListener('click', () => {
          if (op === 'toggle') {
            state.expanded = state.expanded === id ? null : id;
            save(); renderPlans();
            if (state.expanded === id && p.status === 'running') startPoll(id);
            else if (state.expanded === id) loadLogOnce(id);
          }
          if (op === 'run') runPlan(id);
          if (op === 'stop') stopPlan(id);
          if (op === 'copy') copyPlanSummary(p);
          if (op === 'copylog') {
            copyText((logBufs[id] || []).join('\n') || '（暂无日志）').then(ok => {
              notify2(ok ? '日志已复制' : '复制失败', ok ? '共 ' + (logBufs[id] || []).length + ' 行' : '当前环境不允许自动复制', ok ? 'success' : 'warning', 2200);
            });
          }
          if (op === 'forget') {
            const del = () => {
              state.plans = state.plans.filter(x => x.id !== id);
              delete logBufs[id];
              if (state.expanded === id) state.expanded = null;
              save(); renderPlans();
              notify2('已从本页移除', '后端数据库里的计划与执行记录没有被删除（仍可通过 /api/orchestrator/plans 查到）', 'info', 3600);
            };
            if (typeof showConfirm === 'function') {
              showConfirm('移除记录', '只从本页面列表移除「' + p.title + '」，不会删除后端数据；数据库里的计划与执行记录保持不变。', del, { okText: '移除' });
            } else { del(); }
          }
        });
      });
      card.querySelectorAll('[data-sub-idx]').forEach(row => {
        const idx = parseInt(row.dataset.subIdx, 10);
        row.querySelectorAll('[data-sub-op]').forEach(btn => {
          btn.addEventListener('click', () => {
            const op = btn.dataset.subOp;
            if (op === 'out') {
              const key = id + ':' + idx;
              openOutputs[key] = !openOutputs[key];
              renderPlans();
            }
            if (op === 'logpath') {
              const s = (p.subtasks || []).find(x => x.idx === idx);
              copyText((s && s.log_path) || '').then(ok => notify2(ok ? '已复制日志路径' : '复制失败', (s && s.log_path) || '', ok ? 'success' : 'warning', 3000));
            }
          });
        });
      });
    });
  }

  async function loadLogOnce(planId) {
    // 重新展开一个已结束的计划：让后端挑"日志最多的那个子任务"（frontend 无法凭状态判断谁有日志，
    // 之前取"最后一个非 pending 子任务"会在它恰好没日志时把已经拉到手的日志清空 —— 真实缺陷，已修）
    const r = await apiCall('GET', '/api/orchestrator/plans/' + encodeURIComponent(planId) +
      '?offset=0&log_index=-2', undefined, 8000);
    if (!r.ok || !r.data) return;
    const d = r.data || {};
    const lg = d.log || {};
    const lines = lg.lines || [];
    const got = (lg.subtask_index === undefined) ? null : lg.subtask_index;
    // 只有"确实拿到内容"或"当前缓冲本来就是空的"才替换，绝不拿空结果覆盖已有日志
    if (lines.length || !(logBufs[planId] || []).length) {
      logBufs[planId] = lines;
      offsets = { index: got, offset: lg.total_lines || 0 };
    }
    renderPlans();
  }

  async function copyPlanSummary(p) {
    const c = planCost(p);
    const lines = [];
    lines.push('# 编排计划：' + p.title);
    lines.push('状态：' + planMeta(p.status)[0] + ' · 进度 ' + (p.done_count || 0) + '/' + (p.subtask_count || 0) +
      ' · 总耗时 ' + (fmtDuration(p.duration_sec) || '-') + ' · 总成本 ' + CostEngineText(c.total));
    if (p.project_path) lines.push('项目路径：' + p.project_path);
    if (p.goal) lines.push('目标：' + p.goal);
    lines.push('');
    (p.subtasks || []).forEach(s => {
      const sc = subCost(s, p);
      lines.push('[' + (s.status) + '] #' + (s.idx + 1) + ' ' + s.title + '（' + (s.effective_worker || s.worker) + '，' +
        (fmtDuration(s.elapsed_sec) || '-') + '，' + (sc ? sc.text : '成本未能获取') + '）');
      if (s.error) lines.push('    错误：' + s.error);
      if (s.output) lines.push('    输出摘要：' + String(s.output).replace(/\s+/g, ' ').slice(0, 200));
    });
    lines.push('');
    lines.push('成本口径：codex 子任务 = 真实 usage × 成本中心单价；本地 Ollama 子任务恒为 ¥0（本地推理）。');
    const ok = await copyText(lines.join('\n'));
    notify2(ok ? '已复制计划汇总' : '复制失败', ok ? '含每个子任务的状态/工人/耗时/成本' : '当前环境不允许自动复制', ok ? 'success' : 'warning', 2600);
  }

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

  function notify2(title, msg, type, ms) {
    if (typeof showNotification === 'function') showNotification(title, msg, type, ms);
  }

  /* ---------------- 设置页：编排参数（工人池说明 + 本地模型名） ---------------- */

  function appendOrchSettingsCard() {
    const container = document.querySelector('#page-settings .max-w-2xl');
    if (!container) return;
    const old = container.querySelector('#orchSettingsCard');
    if (old) old.remove();
    const card = document.createElement('div');
    card.id = 'orchSettingsCard';
    card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
    const d = pf.data || {};
    const ollama = d.ollama || {};
    card.innerHTML = [
      '<h3 class="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2"><i data-lucide="network" class="w-4 h-4 text-brand-cyan"></i>编排工头（大任务拆解与多工人串行）</h3>',
      '<p class="text-[10px] text-text-tertiary mb-3">「编排工头」页把一个大任务交给<b>本地模型拆解</b>成子任务，再按类型分给两个工人串行执行：' +
      '<b>codex 工人</b>（真 codex exec 子进程，按真实 usage 计费）与 <b>本地 Ollama 工人</b>（真调 /api/chat，成本恒为 ¥0）。' +
      '单机同时只允许 1 个计划在跑（与单任务自动执行共用一把闸门）。</p>',
      '<div class="space-y-2 text-[11px] text-text-secondary">',
      '<div>拆解模型（本地，免费）：<code class="font-mono">' + esc((ollama.decompose_models || ['qwen3.5:9b', 'qwen2.5:7b']).join(' → ')) + '</code>' +
      '（失败自动降级；都失败会返回明确错误，不会假装成功）</div>',
      '<div>Ollama 地址：<code class="font-mono">' + esc(ollama.url || 'http://127.0.0.1:11434') + '</code>' +
      (ollama.ok ? ' · 已连接，可用模型 ' + esc((ollama.models || []).join('、')) : ' · 未连接：' + esc(ollama.note || '')) + '</div>',
      '<div>codex 工人命令：<code class="font-mono">' + esc((d.codex && d.codex.path) || cfgCodexPath()) + '</code>' +
      '（在「Agent 编排」里改；成本口径读 ~/.codex/config.toml 的 model）</div>',
      '<div>每个子任务超时：默认 ' + esc(cfgTimeout()) + ' 秒（与单任务一致，计划创建时可单独设置）</div>',
      '</div>'
    ].join('');
    container.appendChild(card);
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 安装 ---------------- */

  function install() {
    load();
    ensureNavButton();
    ensurePage();
    buildPage();
    appendOrchSettingsCard();
    window.addEventListener('moray-settings-changed', () => {
      const page = document.getElementById('page-' + PAGE);
      if (page) buildPage();
      appendOrchSettingsCard();
    });
    window.addEventListener('moray-backend-probed', () => {
      preflight(true).then(() => { renderAll(); appendOrchSettingsCard(); });
      refreshFromBackend();
    });
    if (isBackendOnline()) {
      setTimeout(() => {
        preflight(true).then(() => { renderAll(); appendOrchSettingsCard(); });
        refreshFromBackend();
      }, 700);
    }
  }

  function bootInstall() {
    try { install(); } catch (e) { console.warn('[MoRay] 编排工头面板安装失败:', e); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bootInstall);
  else setTimeout(bootInstall, 0);

  /** 被任务分派面板的「编排计划」入口按钮调用 */
  function openFromTaskPanel() {
    switchTo(PAGE);
    onEnterPage();
  }

  /** 调试/自检出口（只读状态，便于自动化验证） */
  window.__taskOrchestrator = {
    page: PAGE,
    open: openFromTaskPanel,
    switchTo,
    installed() {
      return !!(document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]') && document.getElementById('page-' + PAGE));
    },
    state: () => JSON.parse(JSON.stringify({ plans: state.plans, expanded: state.expanded, draft: state.draft })),
    preflight: () => JSON.parse(JSON.stringify(pf)),
    refreshPreflight: () => preflight(true),
    refresh: (force) => refreshFromBackend(force),
    /** 自动化用：填表 → 拆解（真实调用本地模型） */
    setForm: (o) => {
      const page = document.getElementById('page-' + PAGE);
      if (!page) return false;
      page.querySelector('#orchFormCard').style.display = '';
      if (o.title !== undefined) page.querySelector('#orchTitle').value = o.title;
      if (o.goal !== undefined) page.querySelector('#orchGoal').value = o.goal;
      if (o.projectPath !== undefined) page.querySelector('#orchProjectPath').value = o.projectPath;
      if (o.pool !== undefined) page.querySelector('#orchPool').value = o.pool;
      if (o.failFast !== undefined) page.querySelector('#orchFailFast').value = o.failFast ? '1' : '0';
      if (o.timeoutSec !== undefined) page.querySelector('#orchTimeout').value = o.timeoutSec;
      return true;
    },
    draft: () => JSON.parse(JSON.stringify(state.draft)),
    /** 自动化用：直接注入一份"人工确认后的子任务列表"（走的是与拆解结果完全相同的渲染/提交路径） */
    injectDraft: (d) => {
      if (!d || !Array.isArray(d.subtasks)) return false;
      state.draft = Object.assign({
        title: '', goal: '', projectPath: '', pool: 'both', failFast: false,
        timeoutSec: cfgTimeout(), model: '', model_note: '', elapsed_sec: 0, subtasks: []
      }, d);
      state.draft.subtasks = state.draft.subtasks.map(s => ({
        title: s.title || '', detail: s.detail || '', worker: s.worker || 'auto'
      }));
      save(); renderDraft();
      return true;
    },
    setDraftSubtasks: (arr) => {
      if (!state.draft) return false;
      state.draft.subtasks = arr.map(s => ({ title: s.title || '', detail: s.detail || '', worker: s.worker || 'auto' }));
      save(); renderDraft();
      return true;
    },
    createPlan: (autoRun) => createPlan(!!autoRun),
    run: (planId) => runPlan(planId),
    stop: (planId) => stopPlan(planId),
    logs: (planId) => (logBufs[planId] || []).join('\n'),
    /** 自检：轮询游标与日志缓冲状态（排查增量拉取时用） */
    debug: (planId) => ({
      pollPlanId, offsets, expanded: state.expanded,
      logLen: (logBufs[planId] || []).length,
      logAll: (logBufs[planId] || []),
      pollTimer: !!pollTimer
    }),
    costOf: (planId) => {
      const p = state.plans.find(x => x.id === planId);
      return p ? planCost(p) : null;
    },
    planOf: (planId) => {
      const p = state.plans.find(x => x.id === planId);
      return p ? JSON.parse(JSON.stringify(p)) : null;
    }
  };
})();
