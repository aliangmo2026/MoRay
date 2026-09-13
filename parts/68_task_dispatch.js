/* ===================== [3.20 阶段3.2] 任务分派面板（A2A 子集） =====================
 * 能力边界（诚实说明，不做"假装"功能）：
 *  - 本面板**不与外部 agent 建立实时通信通道**。真实的 A2A（agent 之间互相调用/回传）
 *    需要对方平台开放接口与鉴权，MoRay 目前不具备，所以这里只做能真正工作的两件事：
 *    ① 结构化任务指令生成（项目路径/背景/要求/验收标准模板）+ 一键复制到剪贴板；
 *    ② 任务状态管理（待办/进行中/已完成/失败）+ 备注 + 删除，localStorage 持久化。
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
    { id: 'failed', name: '失败', cls: 'bg-danger/15 text-danger' }
  ];
  const DEFAULT_ACCEPT = '1. 功能真实可用（没有假按钮 / 假数据 / 假状态）\n2. 通过项目现有测试或自测命令\n3. 给出改动文件清单与关键实现说明';

  /** 本地状态（localStorage 持久化；结构损坏时安全回退为空） */
  let state = { tasks: [], seq: 1, projectPath: '', customAgent: '', lastForm: null };
  /** 当前是否有任务正在执行（防重复点击） */
  let running = false;

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
          lastForm: o.lastForm || null
        };
      }
    } catch (e) { /* 损坏数据不阻塞面板 */ }
  }

  function save() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(state)); return true; }
    catch (e) { return false; }
  }

  function uid() { return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

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
          <span class="text-[11px] text-text-tertiary" title="真实 A2A 需要对方平台开放接口；本面板负责指令生成与状态管理">指令生成 + 状态管理（非实时通信）</span>
        </div>
        <div class="flex items-center gap-2">
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
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  function bindForm(cloudReady) {
    const page = document.getElementById('page-' + PAGE);
    if (!page) return;
    const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };
    on(page.querySelector('#taskNewBtn'), 'click', () => {
      const card = page.querySelector('#taskFormCard');
      card.style.display = '';
      const f = state.lastForm || {};
      page.querySelector('#taskName').value = '';
      page.querySelector('#taskAgent').value = f.agent || 'zcode';
      page.querySelector('#taskCustomAgent').value = state.customAgent || '';
      page.querySelector('#taskProjectPath').value = f.projectPath || state.projectPath || '';
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
            <button class="tool-btn" data-task-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
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
          if (op === 'delete') {
            const del = () => {
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
    if (typeof refreshIcons === 'function') refreshIcons();
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

  /* ---------------- 安装 ---------------- */
  function install() {
    load();
    ensureNavButton();
    ensurePage();
    buildPage();
    // 设置页改了云端 API 后，"直接执行"入口需要跟着出现/消失（不刷新页面也生效）
    window.addEventListener('moray-settings-changed', () => {
      const page = document.getElementById('page-' + PAGE);
      if (page) buildPage();
    });
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
    }
  };
})();
