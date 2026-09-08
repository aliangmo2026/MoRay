/* ============================================================
   [工具调用] ToolRegistry —— 内置 Function Calling 工具闭环
   - 协议：OpenAI/DeepSeek 兼容 tools / tool_calls（纯前端内置，无 MCP/子进程/CDN）
   - 工具：get_current_time / calculator（阶段A）；query_knowledge_base / save_snippet /
           run_workflow / get_cost_usage（阶段B，复用现有能力，零重写）
   - 安全：calculator 自写词法 + 递归下降解析器，禁 eval/Function/字符串执行
   - 加载顺序：gateway 之后；运行时引用一律 typeof 守卫
   ============================================================ */

/** 内置工具注册表 */
const ToolRegistry = {
  tools: {},

  /** 注册工具描述 {name, label, description, parameters, enabledByDefault, run}
   * @param {Object} tool - 工具定义 */
  register(tool) {
    if (!tool || !tool.name || typeof tool.run !== 'function') return;
    this.tools[tool.name] = tool;
  },

  /** 按名取工具定义
   * @param {string} name - 工具名 */
  get(name) { return this.tools[name] || null; },

  /** 某工具当前是否启用（总开关 + 独立开关 + [阶段0] 本机工具门）
   * @param {string} name - 工具名 */
  isEnabled(name) {
    const t = this.tools[name];
    if (!t) return false;
    if (!MoraySettings.get('toolsEnabled')) return false;
    // [阶段0 本机 Agent] native 工具须输入台显式开启"本机工具/Agent"才可用（默认关）
    if (t.native && !MoraySettings.get('nativeToolsEnabled')) return false;
    const disabled = MoraySettings.get('toolsDisabled') || [];
    return !disabled.includes(name);
  },

  /** 请求体可用的协议描述（关闭总开关时返回空数组 → 请求体绝无 tools 字段）
   * 各工具经 isEnabled 统一过滤（含独立禁用与 native 门）
   * @returns {Array<{type:string, function:Object}>} */
  listForRequest() {
    if (!MoraySettings.get('toolsEnabled')) return [];
    return Object.values(this.tools)
      .filter(t => this.isEnabled(t.name))
      .map(t => ({
        type: 'function',
        function: { name: t.name, description: t.description, parameters: t.parameters || { type: 'object', properties: {} } }
      }));
  },

  /** 执行工具：5s 超时 + try/catch，单工具失败绝不崩整轮；支持 ctx.signal 立即中止
   * @param {string} name - 工具名
   * @param {Object} args - 参数
   * @param {Object} [ctx] - 上下文（可含 signal）
   * @returns {Promise<{ok:boolean, data?:*, error?:string, ms:number}>} */
  async run(name, args, ctx) {
    const t = this.tools[name];
    if (!t) return { ok: false, error: '未知工具：' + name, ms: 0 };
    if (!this.isEnabled(name)) return { ok: false, error: '工具「' + t.label + '」未启用', ms: 0 };
    const started = performance.now();
    // [修复] 内部 AbortController：合并"外部取消（用户停止）"与"5s 超时"为单一 internal.signal 供工具检查；
    // 绝不对外部传入的 signal 调 abort（AbortSignal 没有 abort 方法）
    const internal = new AbortController();
    const onOuterAbort = () => { if (!internal.signal.aborted) internal.abort(); };
    if (ctx && ctx.signal) {
      if (ctx.signal.aborted) internal.abort();
      else ctx.signal.addEventListener('abort', onOuterAbort, { once: true });
    }
    let timedOut = false;
    // [阶段0] 工具级超时：native 工具含人工审批等待与后端执行（run_command 最长 15s），
    // 按工具定义 timeoutMs 放宽（默认仍 5s，与改造前一致）；审批等待也纳入但不误杀
    const tmo = Math.max(1000, parseInt(t.timeoutMs || 5000, 10) || 5000);
    const timer = setTimeout(() => {
      timedOut = true;
      if (!internal.signal.aborted) internal.abort(); // 协作式取消底层工具
    }, tmo);
    try {
      // 传给具体工具的 ctx：signal 换成合并后的 internal.signal（工具检查 aborted 即覆盖"外部取消+超时"）
      const toolCtx = Object.assign({}, ctx || {}, { signal: internal.signal });
      const abortErr = () => Object.assign(new Error('aborted'), { name: 'AbortError' });
      const abortP = new Promise((_, rej) => {
        if (internal.signal.aborted) rej(abortErr());
        else internal.signal.addEventListener('abort', () => rej(abortErr()), { once: true });
      });
      const work = Promise.resolve().then(() => t.run(args || {}, toolCtx));
      const data = await Promise.race([work, abortP]);
      return { ok: true, data, ms: Math.round(performance.now() - started) };
    } catch (e) {
      // 三类结果可辨：超时=超时文案（不传播，工具循环继续）；用户取消=AbortError（传播，立即停止）；工具错误=原始错误
      if (internal.signal.aborted && timedOut) return { ok: false, error: '工具执行超时（' + (tmo / 1000) + 's）', ms: Math.round(performance.now() - started), timeout: true };
      if (e && e.name === 'AbortError') throw e;
      return { ok: false, error: String((e && e.message) || e).slice(0, 200), ms: Math.round(performance.now() - started) };
    } finally {
      // [修复] 所有结束路径清理定时器与外部监听，杜绝泄漏与延迟异常
      clearTimeout(timer);
      if (ctx && ctx.signal) ctx.signal.removeEventListener('abort', onOuterAbort);
    }
  },

  /** 参数可读化（步骤卡摘要行/展开区共用；write 显示目标路径，command 显示完整命令）
   * @param {string} name - 工具名
   * @param {Object} args - 参数
   * @returns {string} 可读参数文本 */
  formatArgs(name, args) {
    const a = args || {};
    try {
      if (name === 'write_file') {
        const bytes = (typeof a.content === 'string') ? a.content.length : JSON.stringify(a.content || '').length;
        return '写入 ' + (a.path || '') + '（' + bytes + ' 字符' + (a.overwrite === false ? '，不覆盖' : '') + '）';
      }
      if (name === 'run_command') {
        return '$ ' + String(a.command || '') + (Array.isArray(a.args) && a.args.length ? ' ' + a.args.join(' ') : '');
      }
      if (name === 'read_file') return '读取 ' + (a.path || '');
      if (name === 'list_directory') return '列出 ' + (a.path || '工作区根');
      if (name === 'find_files') return '查找「' + (a.pattern || '*') + '」' + (a.path ? '于 ' + a.path : '（全工作区）');
      if (name === 'search_text') return '搜索「' + String(a.query || '').slice(0, 40) + '」' + (a.regex ? '（正则）' : '') + (a.path ? '于 ' + a.path : '');
      if (name === 'edit_file') return '编辑 ' + (a.path || '') + '（' + String(a.old_str || '').length + '→' + String(a.new_str || '').length + ' 字符）';
      const s = JSON.stringify(a);
      return s.length > 120 ? s.slice(0, 120) + '…' : s;
    } catch (e) {
      return String(JSON.stringify(a)).slice(0, 120);
    }
  },

  /** 单步事件 → 步骤卡 HTML（进行中脉冲 / 待审批 / 成功 / 失败(可重试) / 已拒绝
   * 摘要行：完整可读入参 + 耗时 + 状态；展开区：完整结果或真实错误，不撑爆会话）
   * @param {Object} evt - {index, name, label, args, status, resultPreview, ms, resultFull}
   * @returns {string} HTML */
  stepHtml(evt) {
    const iconMap = {
      get_current_time: 'clock', calculator: 'calculator', query_knowledge_base: 'library',
      save_snippet: 'save', run_workflow: 'workflow', get_cost_usage: 'wallet',
      // [阶段0 本机 Agent] 本机工具图标
      list_directory: 'folder', read_file: 'file-text', write_file: 'file-pen-line', run_command: 'terminal',
      // [阶段1 M2] 高价值三工具
      find_files: 'file-search', search_text: 'text-search', edit_file: 'square-pen'
    };
    const icon = iconMap[evt.name] || 'wrench';
    const label = evt.label || evt.name;
    const argFull = this.formatArgs(evt.name, evt.args);
    const argShort = argFull.length > 52 ? argFull.slice(0, 52) + '…' : argFull;    const msText = evt.ms != null ? (evt.ms >= 1000 ? (evt.ms / 1000).toFixed(1) + 's' : Math.round(evt.ms) + 'ms') : '';
    let badge, color;
    if (evt.status === 'running') { badge = '<span class="animate-pulse text-[10px] text-brand-cyan">执行中…</span>'; color = 'text-brand-cyan'; }
    // [阶段0] 审批态：pending=等待用户授权（warning 脉冲），denied=用户拒绝（warning 稳态）
    else if (evt.status === 'pending') { badge = '<span class="animate-pulse text-[10px] text-warning">待审批</span>'; color = 'text-warning'; }
    else if (evt.status === 'denied') { badge = '<span class="text-[10px] text-warning">已拒绝</span>'; color = 'text-warning'; }
    else if (evt.status === 'error') { badge = '<span class="text-[10px] text-danger">失败</span>'; color = 'text-danger'; }
    else { badge = '<span class="text-[10px] text-success">成功</span>'; color = 'text-success'; }
    // [阶段0.5] 展开区内容：完整结果优先（不截断文本，CSS 限高滚动不撑爆会话）；
    // 失败步骤显示后端真实错误 + "重试该步"（重新执行同一工具，走完整审批/安全层）
    const full = evt.resultFull != null ? String(evt.resultFull) : (evt.resultPreview != null ? String(evt.resultPreview) : '');
    const body = '';
    if (evt.status === 'error') {
      body = '<div class="text-[10px] text-danger mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono" data-tool-err>' + escapeHtml(full || '未知错误') + '</div>';
    } else if (evt.status === 'denied') {
      body = '<div class="text-[10px] text-warning mt-1.5">' + escapeHtml(full || '用户拒绝了该操作') + '</div>';
    } else if (full) {
      body = '<div class="text-[10px] text-text-tertiary mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all" data-tool-full>' + escapeHtml(full) + '</div>';
    }
    // [批次修复 #7] 重放标记：该步被“重试该步”重放过（仅重新执行工具，不自动改写已生成回复）
    if (evt.replayed) {
      const changedNote = evt.replayed.changed ? '结果与上次不同（已更新）' : '结果与上次一致';
      body = '<div class="flex items-center gap-1.5 mt-1.5 text-[10px] text-warning"><i data-lucide="rotate-cw" class="w-3 h-3"></i>' +
        '已重放该步（仅重新执行工具，不自动改写已生成回复）· ' + escapeHtml(changedNote) +
        ' · 可点回复右上「重新生成」让模型基于最新结果作答</div>' + body;
    }
    const retry = evt.status === 'error' && evt.name
      ? '<button data-tool-retry="' + evt.index + '" class="ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-text-tertiary hover:text-warning hover:bg-surface-panel flex items-center gap-1 text-[10px]" title="重新执行本步骤（走完整安全层与审批）"><i data-lucide="rotate-cw" class="w-3 h-3"></i>重试该步</button>'
      : '';
    const copyBtn = full && evt.status !== 'error'
      ? '<button class="tool-step-copy ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-text-tertiary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" data-tool-copy title="复制完整结果" data-full="' + escapeHtml(full) + '"><i data-lucide="copy" class="w-3 h-3"></i>复制</button>'
      : '';
    const actions = (copyBtn || retry)
      ? '<span class="ml-auto flex items-center gap-1">' + copyBtn + retry + '</span>'
      : '';
    const open = evt.status === 'running' || evt.status === 'pending' ? ' open' : '';
    return '<details class="tool-step rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2" data-tool-step="' + evt.index + '" data-status="' + (evt.status || '') + '" data-ms="' + (evt.ms || 0) + '"' + open + '>' +
      '<summary class="flex items-center gap-2 cursor-pointer list-none text-[11px] select-none">' +
      '<i data-lucide="' + icon + '" class="w-3.5 h-3.5 ' + color + '"></i>' +
      '<span class="text-text-primary font-medium">' + escapeHtml(label) + '</span>' +
      (argShort ? '<span class="text-text-tertiary truncate font-mono" title="' + escapeHtml(argFull) + '">' + escapeHtml(argShort) + '</span>' : '') +
      (msText ? '<span class="text-text-tertiary shrink-0">' + msText + '</span>' : '') + badge + actions +
      '</summary>' + body +
      '</details>';
  },

  /** 步骤时间线整体摘要文本："共 N 步 · 总耗时 Xs"
   * @param {HTMLElement|NodeList|Array} stepEls - .tool-step 元素集合
   * @returns {string} 摘要文本 */
  stepsSummaryText(stepEls) {
    const arr = Array.from(stepEls || []);
    let ms = 0;
    arr.forEach(el => { ms += parseInt(el.getAttribute('data-ms') || '0', 10) || 0; });
    return '共 ' + arr.length + ' 步 · 总耗时 ' + (ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms');
  },

  /** 消息持久化 steps → 静态步骤卡 HTML（重开会话渲染用）
   * [阶段0.5] 默认折叠为一行"共 N 步 · 总耗时 Xs"，点击展开完整时间线
   * @param {Array} toolCalls - [{name,label,args,status,resultPreview,ms}]
   * @returns {string} HTML */
  renderSteps(toolCalls) {
    if (!toolCalls || !toolCalls.length) return '';
    const stepsHtml = toolCalls.map((s, i) => this.stepHtml(Object.assign({}, s, { index: i }))).join('');
    const tmp = document.createElement('div');
    tmp.innerHTML = stepsHtml;
    const summaryText = this.stepsSummaryText(tmp.querySelectorAll('.tool-step'));
    return '<div class="tool-steps-wrap my-2">' +
      '<div class="tool-steps-summary rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2 text-[11px] text-text-secondary flex items-center gap-2 cursor-pointer select-none hover:border-line-ghost" data-steps-toggle title="点击展开完整时间线">' +
      '<i data-lucide="list-tree" class="w-3.5 h-3.5 text-brand-cobalt"></i>' +
      '<span data-steps-summary-text>' + escapeHtml(summaryText) + '</span>' +
      '<span class="text-[10px] text-text-tertiary ml-auto" data-steps-hint>展开</span></div>' +
      '<div class="tool-steps space-y-1.5" style="display:none">' + stepsHtml + '</div></div>';
  },

  /** [阶段0.5] 全部步骤到终态 → 把时间线折叠为摘要行（不改变 DOM 结构，只切换可见性）
   * @param {HTMLElement} wrapEl - .tool-steps-wrap 容器
   * @returns {void} */
  maybeCollapseSteps(wrapEl) {
    if (!wrapEl || !wrapEl.isConnected) return;
    const summaryEl = wrapEl.querySelector('.tool-steps-summary');
    const list = wrapEl.querySelector('.tool-steps');
    if (!summaryEl || !list) return;
    const steps = list.querySelectorAll('.tool-step[data-status]');
    if (!steps.length) return;
    const busy = Array.from(steps).some(el => el.getAttribute('data-status') === 'running' || el.getAttribute('data-status') === 'pending');
    if (busy) return;
    const textEl = summaryEl.querySelector('[data-steps-summary-text]');
    if (textEl) textEl.textContent = this.stepsSummaryText(steps);
    const hint = summaryEl.querySelector('[data-steps-hint]');
    if (hint) hint.textContent = '展开';
    summaryEl.style.display = '';
    list.style.display = 'none';
  },

  /** 实时步骤卡：事件驱动（首个事件建容器于最终回复之前，后续按 index 原位更新）
   * 同时管理"工具执行中"发送按钮状态（running 进入 / 终态退出）
   * [阶段0.5] 全部步骤到终态后自动折叠为"共 N 步 · 总耗时 Xs"摘要行（可点击展开）
   * @param {HTMLElement} bubbleEl - 消息气泡
   * @param {Object} evt - 步骤事件 */
  renderLiveStep(bubbleEl, evt) {
    if (!bubbleEl) return;
    if (evt.status === 'running' || evt.status === 'pending') this.enterToolBusy();
    else this.exitToolBusy();
    let wrap = bubbleEl.querySelector('.tool-steps-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'tool-steps-wrap my-2';
      wrap.innerHTML = '<div class="tool-steps-summary rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2 text-[11px] text-text-secondary flex items-center gap-2 cursor-pointer select-none hover:border-line-ghost" data-steps-toggle title="点击展开完整时间线" style="display:none">' +
        '<i data-lucide="list-tree" class="w-3.5 h-3.5 text-brand-cobalt"></i>' +
        '<span data-steps-summary-text></span>' +
        '<span class="text-[10px] text-text-tertiary ml-auto" data-steps-hint>展开</span></div>' +
        '<div class="tool-steps space-y-1.5"></div>';
      const inner = bubbleEl.querySelector('.ai-msg-inner');
      if (inner) inner.parentElement.insertBefore(wrap, inner); // 最终回复之前
      else bubbleEl.appendChild(wrap);
    }
    // 新事件到来 → 保持展开态（从摘要折叠恢复）
    const summaryEl = wrap.querySelector('.tool-steps-summary');
    const list = wrap.querySelector('.tool-steps');
    if (summaryEl && list) {
      summaryEl.style.display = 'none';
      list.style.display = '';
    }
    const old = list.querySelector('[data-tool-step="' + evt.index + '"]');
    if (old) old.outerHTML = this.stepHtml(evt);
    else list.insertAdjacentHTML('beforeend', this.stepHtml(evt));
    refreshIcons();
    // 终态事件 → 延迟检查整条时间线是否全部完成，完成则折叠为摘要
    if (evt.status !== 'running' && evt.status !== 'pending') {
      setTimeout(() => this.maybeCollapseSteps(wrap), 250);
    }
  },

  /** 工具执行中计数 @type {number} */
  _busy: 0,
  /** 发送按钮原始状态缓存 @type {Object|null} */
  _busyOrig: null,

  /** 工具执行中：发送按钮切换为"工具执行中…"，点击可中止（复用 AbortController）
   * @returns {void} */
  enterToolBusy() {
    if (this._busy++ > 0) return;
    const ta = document.getElementById('chatInputNormal');
    const wrap = ta && ta.closest('.flex-1') ? ta.closest('.flex-1').parentElement : null;
    const btn = wrap ? wrap.querySelector('button') : null;
    if (!btn || this._busyOrig) return;
    this._busyOrig = { html: btn.innerHTML, onclick: btn.getAttribute('onclick') };
    btn.removeAttribute('onclick');
    btn.style.width = 'auto';
    btn.style.padding = '0 10px';
    btn.title = '工具执行中，点击中止';
    btn.innerHTML = '<span class="text-[10px] whitespace-nowrap animate-pulse">工具执行中…</span>';
    btn.onclick = () => { if (typeof AppState !== 'undefined' && AppState.genController) AppState.genController.abort(); };
  },

  /** 工具终态：计数递减，全部结束后恢复发送按钮
   * @returns {void} */
  exitToolBusy() {
    if (this._busy <= 0) return;
    if (--this._busy > 0) return;
    this.restoreSendBtn();
  },

  /** 恢复发送按钮为原始状态
   * @returns {void} */
  restoreSendBtn() {
    const ta = document.getElementById('chatInputNormal');
    const wrap = ta && ta.closest('.flex-1') ? ta.closest('.flex-1').parentElement : null;
    const btn = wrap ? wrap.querySelector('button') : null;
    if (btn && this._busyOrig) {
      btn.innerHTML = this._busyOrig.html;
      btn.onclick = null;
      if (this._busyOrig.onclick) btn.setAttribute('onclick', this._busyOrig.onclick);
      else btn.removeAttribute('onclick');
      btn.style.width = '';
      btn.style.padding = '';
      btn.title = '';
    }
    this._busyOrig = null;
  },

  /** 生成流程结束（完成/中止/失败）统一复位工具中状态
   * @returns {void} */
  resetToolUI() {
    this._busy = 0;
    this.restoreSendBtn();
  },

  /** 步骤结果复制 / 时间线摘要折叠切换 / 失败步骤重试：事件委托（加载即装，幂等）
   * @returns {void} */
  _installCopyHandler() {
    if (this.__copyBound) return;
    this.__copyBound = true;
    document.addEventListener('click', (e) => {
      // [阶段0.5] 时间线摘要行：展开/收起完整时间线
      const toggle = e.target.closest('[data-steps-toggle]');
      if (toggle) {
        const wrapEl = toggle.closest('.tool-steps-wrap');
        const list = wrapEl ? wrapEl.querySelector('.tool-steps') : null;
        if (!list) return;
        const expanded = list.style.display !== 'none';
        list.style.display = expanded ? 'none' : '';
        const hint = toggle.querySelector('[data-steps-hint]');
        if (hint) hint.textContent = expanded ? '展开' : '收起';
        return;
      }
      // [阶段0.5] 失败步骤"重试该步"：重新执行同一工具（走完整安全层与审批），就地更新结果
      const retryBtn = e.target.closest('[data-tool-retry]');
      if (retryBtn) {
        e.stopPropagation();
        this.retryStep(retryBtn);
        return;
      }
      const b = e.target.closest('[data-tool-copy]');
      if (!b) return;
      e.stopPropagation(); // 防止折叠 details
      const full = b.getAttribute('data-full') || '';
      const done = (ok) => showNotification(ok ? '已复制工具结果' : '复制失败', ok ? '完整结果已复制到剪贴板' : '', ok ? 'success' : 'error', 1600);
      if (typeof copyToClipboard === 'function') copyToClipboard(full).then(done, () => done(false));
      else if (navigator.clipboard) navigator.clipboard.writeText(full).then(() => done(true), () => done(false));
    });
  },

  /** [阶段0.5] 重试失败步骤：按消息持久化的 name/args 重新执行（不经模型，走完整
   * 安全层与审批），就地更新该步骤卡与消息持久化数据。不改变已生成的模型回复——
   * 重试成功后提示用户可用"重新生成"让模型基于最新结果作答。
   * @param {HTMLElement} btn - [data-tool-retry] 按钮
   * @returns {Promise<void>} */
  async retryStep(btn) {
    if (this.__retrying) return;
    const idx = parseInt(btn.getAttribute('data-tool-retry'), 10);
    const bubble = btn.closest('.msg-bubble');
    const msgId = bubble && bubble.dataset ? bubble.dataset.msgId : null;
    const msg = (typeof AppState !== 'undefined' && msgId) ? (AppState.messages || []).find(m => m.id === msgId) : null;
    const steps = msg && Array.isArray(msg.toolCalls) ? msg.toolCalls : null;
    const step = steps ? steps[idx] : null;
    if (!step || !step.name) {
      showNotification('无法重试', '未找到该步骤的原始参数', 'error', 2200);
      return;
    }
    this.__retrying = true;
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader-2" class="w-3 h-3 animate-spin"></i>重试中';
    refreshIcons();
    try {
      const res = await ToolRegistry.run(step.name, step.args || {}, {});
      step.status = res.ok ? 'success' : 'error';
      step.ms = res.ms;
      const text = res.ok ? (typeof res.data === 'string' ? res.data : JSON.stringify(res.data)) : res.error;
      const prevFull = step.resultFull || '';
      const newFull = (res.ok ? text : ('错误：' + res.error)).slice(0, 10000);
      // [批次修复 #7] 重放标记：显式告知“仅重放工具、不自动改写回复”，并标注结果是否变化
      step.replayed = { at: Date.now(), changed: !!prevFull && prevFull !== newFull };
      step.resultPreview = text;
      step.resultFull = newFull;
      const card = btn.closest('.tool-step');
      if (card && card.isConnected) card.outerHTML = this.stepHtml(Object.assign({}, step, { index: idx }));
      refreshIcons();
      if (res.ok) showNotification('重试成功', '已重放该步（仅重新执行工具）——点「重新生成」可让模型基于最新结果作答', 'success', 3800);
      else showNotification('重试失败', String(res.error || '').slice(0, 140), 'error', 3000);
      if (msg && typeof persistMessage === 'function') persistMessage(msg).catch(() => {});
    } catch (e) {
      showNotification('重试失败', String((e && e.message) || e).slice(0, 140), 'error', 3000);
    } finally {
      this.__retrying = false;
    }
  }
};

/** 安全算术求值：仅数字 + - * / % ( ) , 与白名单 Math 函数
 * 词法 + 递归下降解析，禁 eval/Function/任意标识符/字符串执行
 * @param {string} expr - 表达式
 * @returns {number} 结果（非法输入抛错） */
function safeCalc(expr) {
  const FUNCS = { sqrt: 1, pow: 2, abs: 1, round: 1, floor: 1, ceil: 1, min: -1, max: -1 }; // -1=可变参数
  if (typeof expr !== 'string' || !expr.trim()) throw new Error('空表达式');
  let pos = 0;
  const n = expr.length;
  const err = (m) => { throw new Error('表达式非法：' + m + '（仅支持数字、+ - * / % ( ) 与 ' + Object.keys(FUNCS).join('/') + '）'); };
  const skipWs = () => { while (pos < n && (expr[pos] === ' ' || expr[pos] === '\t' || expr[pos] === '\n')) pos++; };
  const peek = () => { skipWs(); return pos < n ? expr[pos] : ''; };
  const take = () => { skipWs(); if (pos >= n) err('表达式意外结束'); return expr[pos++]; };

  function parseNumber() {
    let s = '';
    while (pos < n && /[0-9.]/.test(expr[pos])) s += expr[pos++];
    if (!s) err('期望数字，遇到「' + expr[pos] + '」');
    if ((s.match(/\./g) || []).length > 1 || s === '.') err('数字格式错误：' + s);
    const v = Number(s);
    if (!isFinite(v)) err('数字过大：' + s);
    return v;
  }
  function parseName() {
    let s = '';
    while (pos < n && /[a-zA-Z]/.test(expr[pos])) s += expr[pos++];
    return s;
  }
  function parsePrimary() {
    const c = peek();
    if (c === '(') {
      take();
      const v = parseExpr();
      if (take() !== ')') err('缺少右括号');
      return v;
    }
    if (/[0-9.]/.test(c)) return parseNumber();
    if (/[a-zA-Z]/.test(c)) {
      const name = parseName();
      if (!(name in FUNCS)) err('不允许的标识符「' + name + '」');
      if (take() !== '(') err('函数 ' + name + ' 后必须跟括号');
      const args = [];
      if (peek() !== ')') {
        for (;;) {
          args.push(parseExpr());
          if (peek() === ')') break;
          if (take() !== ',') err('函数参数需用逗号分隔');
        }
      }
      take(); // 消费 )
      const arity = FUNCS[name];
      if (arity > 0 && args.length !== arity) err('函数 ' + name + ' 需要 ' + arity + ' 个参数');
      if (arity < 0 && args.length < 1) err('函数 ' + name + ' 至少 1 个参数');
      return Math[name].apply(Math, args);
    }
    err('不允许的字符「' + (c || '结尾') + '」');
  }
  function parseUnary() {
    const c = peek();
    if (c === '+' || c === '-') { take(); const v = parseUnary(); return c === '-' ? -v : v; }
    return parseFactor();
  }
  function parseFactor() {
    let v = parsePrimary();
    for (;;) {
      const c = peek();
      if (c === '*') { take(); v *= parsePrimary(); }
      else if (c === '/') {
        take();
        const d = parsePrimary();
        if (d === 0) err('除数为 0');
        v /= d;
      } else if (c === '%') {
        take();
        const d = parsePrimary();
        if (d === 0) err('模数为 0');
        v %= d;
      } else break;
    }
    return v;
  }
  function parseTerm() {
    let v = parseUnary();
    for (;;) {
      const c = peek();
      if (c === '+') { take(); v += parseUnary(); }
      else if (c === '-') { take(); v -= parseUnary(); }
      else break;
    }
    return v;
  }
  function parseExpr() { return parseTerm(); }

  const r = parseExpr();
  skipWs();
  if (pos < n) err('多余的字符「' + expr[pos] + '」');
  if (!isFinite(r)) err('计算结果不是有限数值');
  return r;
}

/** 内置工具清单（{name, label, description, parameters, enabledByDefault, run}） */
const BUILTIN_TOOLS = [
  {
    name: 'get_current_time', label: '获取当前时间',
    description: '获取当前日期、星期与时间戳。当用户询问现在几点、今天几号、星期几时使用。',
    parameters: { type: 'object', properties: {} },
    run() {
      const d = new Date();
      const week = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      const pad = (v) => String(v).padStart(2, '0');
      return {
        date: d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()),
        weekday: '星期' + week,
        time: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
        timestamp: d.getTime()
      };
    }
  },
  {
    name: 'calculator', label: '计算器',
    description: '安全计算数学表达式，支持 + - * / % 与括号，以及 sqrt/pow/abs/round/floor/ceil/min/max 函数。如 "1+2*3"、"(128+256)*2"、"pow(2,10)"。',
    parameters: { type: 'object', properties: { expression: { type: 'string', description: '要计算的数学表达式' } }, required: ['expression'] },
    run(args) {
      if (typeof args.expression !== 'string' || !args.expression.trim()) throw new Error('缺少 expression 参数');
      return { expression: args.expression, result: safeCalc(args.expression) };
    }
  },
  {
    name: 'query_knowledge_base', label: '查询知识库',
    description: '在本地知识库（文档库）中按语义检索相关片段，返回文档标题与内容摘要。当用户询问"知识库里关于 X 的内容"时使用。',
    parameters: {
      type: 'object',
      properties: { query: { type: 'string', description: '检索关键词或问题' }, topK: { type: 'integer', description: '返回条数（默认 3）' } },
      required: ['query']
    },
    async run(args, ctx) {
      if (typeof DocsApp === 'undefined' || typeof DocsApp.semanticSearchRaw !== 'function') throw new Error('知识库组件未加载');
      const q = String(args.query || '').trim();
      if (!q) throw new Error('缺少 query 参数');
      const topK = Math.max(1, Math.min(10, parseInt(args.topK, 10) || 3));
      // [修复] 可中断工具：执行前检查取消信号
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const results = await DocsApp.semanticSearchRaw(q);
      // 关键 await 后复查：中止后不再继续（不组装结果、不写成功态）
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      if (!results || !results.length) return { query: q, count: 0, items: [] };
      return {
        query: q, count: Math.min(results.length, topK),
        items: results.slice(0, topK).map(r => ({
          title: (r.doc && r.doc.name) ? r.doc.name : '(未命名文档)',
          excerpt: String((r.chunk && r.chunk.text) || '').slice(0, 400)
        }))
      };
    }
  },
  {
    name: 'save_snippet', label: '保存代码片段',
    description: '把一段代码或文本保存到本地代码片段库。当用户说"把这段代码存为片段 / 保存这段代码"时使用。',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: '片段标题' }, code: { type: 'string', description: '代码或文本内容' },
        language: { type: 'string', description: '语言（默认 text）' }, tags: { type: 'array', items: { type: 'string' }, description: '标签（可选）' }
      },
      required: ['title', 'code']
    },
    async run(args, ctx) {
      const title = String(args.title || '').trim().slice(0, 100);
      const code = String(args.code || '').trim().slice(0, 20000);
      if (!title) throw new Error('缺少 title 参数');
      if (!code) throw new Error('缺少 code 参数');
      // [修复] 可中断工具：写入前检查取消信号（写入已发起则无法回滚，故只保证写入前可取消）
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const language = (String(args.language || 'text').trim().slice(0, 30)) || 'text';
      const tags = Array.isArray(args.tags) ? args.tags.map(String).map(s => s.trim()).filter(Boolean).slice(0, 8) : [];
      const snip = await DB.createSnippet({ title, code, language, tags: tags.length ? tags : ['工具'] });
      return { id: snip.id, title: snip.title, language: snip.language, tags: snip.tags, message: '已保存到片段库' };
    }
  },
  {
    name: 'run_workflow', label: '运行自动化工作流',
    description: '按名称或 ID 查找并触发一个已有的自动化工作流。当用户说"运行工作流 XXX / 执行自动化 XX"时使用；不带名称时列出全部工作流。',
    parameters: {
      type: 'object',
      properties: { name: { type: 'string', description: '工作流名称或 ID（省略则列出全部）' }, input: { type: 'string', description: '运行时输入（可选）' } }
    },
    async run(args, ctx) {
      if (typeof WorkflowEngine === 'undefined' || typeof DB.listWorkflows !== 'function') throw new Error('工作流组件未加载');
      // [修复] 可中断工具：执行前检查取消信号
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const all = await DB.listWorkflows();
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const name = String(args.name || '').trim();
      if (!name) return { count: all.length, workflows: all.map(w => ({ id: w.id, name: w.name })) };
      const wf = all.find(w => w.name === name || w.id === name);
      if (!wf) throw new Error('未找到工作流「' + name + '」（现有：' + (all.map(w => w.name).join('、') || '无') + '）');
      if (WorkflowEngine.current) throw new Error('已有工作流在执行，请稍后再试');
      if (AI.backend === 'none') throw new Error('未连接 AI 后端，工作流无法运行');
      const input = args.input != null ? String(args.input) : undefined;
      // [运行体检修复] 工具超时/用户中止必须真正停止底层工作流：
      // 订阅 ctx.signal 中止事件 → WorkflowEngine.requestStop()（引擎在节点边界退出，
      // 不依赖 5s 超时后仍让工作流继续后台空跑）；收尾统一移除监听。
      const onToolAbort = () => {
        try {
          if (typeof WorkflowEngine !== 'undefined' && WorkflowEngine.current &&
              WorkflowEngine.current.wf && WorkflowEngine.current.wf.id === wf.id) {
            WorkflowEngine.requestStop();
          }
        } catch (e) { /* 忽略 */ }
      };
      if (ctx && ctx.signal) {
        if (ctx.signal.aborted) onToolAbort();
        else ctx.signal.addEventListener('abort', onToolAbort, { once: true });
      }
      try {
        await WorkflowEngine.run(wf, input);
      } finally {
        if (ctx && ctx.signal) ctx.signal.removeEventListener('abort', onToolAbort);
      }
      // 中止/超时后不再执行“读取运行记录并返回成功”的尾部逻辑
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      // run() 内部捕获错误 → 从最近运行记录读最终状态
      const fresh = await DB.getWorkflow(wf.id);
      const last = fresh && fresh.runs && fresh.runs[0];
      return { id: wf.id, name: wf.name, status: last ? last.status : 'started', note: '运行记录见「自动化」页' };
    }
  },
  {
    name: 'get_cost_usage', label: '查询费用用量',
    description: '查询今日/本月 API 花费与节省（人民币 ¥ 口径，与成本中心一致）与请求次数。当用户问"今天花了多少钱 / 本月费用"时使用。',
    parameters: { type: 'object', properties: {} },
    async run() {
      if (typeof UsageTracker === 'undefined' || typeof CostEngine === 'undefined') throw new Error('用量组件未加载');
      const usage = await gatewayMetaGet('__usage__', { byDay: {} });
      const prefix = dayKey().slice(0, 7);
      const todayKey = dayKey();
      let tCost = 0, tSaved = 0, tReq = 0, mCost = 0, mSaved = 0, mReq = 0;
      Object.keys(usage.byDay || {}).forEach(k => {
        const d = usage.byDay[k];
        const cost = (d.costCNY != null ? d.costCNY : d.cost) || 0;
        const saved = (d.savedCNY != null ? d.savedCNY : 0) || 0;
        if (k === todayKey) { tCost += cost; tSaved += saved; tReq += d.requests || 0; }
        if (k.startsWith(prefix)) { mCost += cost; mSaved += saved; mReq += d.requests || 0; }
      });
      return {
        today: CostEngine.formatMoney(tCost), month: CostEngine.formatMoney(mCost),
        todaySaved: CostEngine.formatMoney(tSaved), monthSaved: CostEngine.formatMoney(mSaved),
        todayRequests: tReq, monthRequests: mReq, note: '人民币（¥）口径，与成本中心一致'
      };
    }
  }
];
BUILTIN_TOOLS.forEach(t => ToolRegistry.register(t));

/** 历史消息的工具步骤卡（消息重建时调用；旧消息无 toolCalls 返回空串）
 * @param {Array} [toolCalls] - 消息上的步骤数组
 * @returns {string} HTML */
function renderToolSteps(toolCalls) {
  return ToolRegistry.renderSteps(toolCalls);
}

/** 追加"AI 工具（Function Calling）"设置卡片（幂等，参照 appendCostSettings 模式）
 * @returns {Promise<void>} */
async function appendToolsSettings() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container || container.querySelector('#toolsSettingsCard')) return;
  const disabled = MoraySettings.get('toolsDisabled') || [];
  const nativeOn = !!MoraySettings.get('nativeToolsEnabled');
  const toolRows = Object.values(ToolRegistry.tools).map(t => {
    // [阶段0] native 工具开关由输入台"本机工具/Agent"统一控制，此处只读展示（不渲染误导性独立开关）
    if (t.native) {
      return '<div class="flex items-center justify-between py-1.5 border-b border-line-ghost/30 last:border-0">' +
        '<div class="flex-1 pr-3"><div class="text-xs text-text-primary">' + escapeHtml(t.label) +
        ' <span class="font-mono text-[10px] text-text-tertiary">' + t.name + '</span>' +
        '<span class="ml-1 text-[9px] px-1.5 py-0.5 rounded bg-brand-violet/10 text-brand-violet border border-brand-violet/20">本机工具</span></div>' +
        '<div class="text-[10px] text-text-tertiary mt-0.5">' + escapeHtml(t.description.length > 90 ? t.description.slice(0, 90) + '…' : t.description) + '</div></div>' +
        '<div class="text-[10px] text-text-tertiary shrink-0 ' + (nativeOn ? 'text-success' : '') + '">' + (nativeOn ? '已由输入台开启' : '由输入台「本机工具」开关控制') + '</div></div>';
    }
    const on = !disabled.includes(t.name);
    return '<div class="flex items-center justify-between py-1.5 border-b border-line-ghost/30 last:border-0">' +
      '<div class="flex-1 pr-3"><div class="text-xs text-text-primary">' + escapeHtml(t.label) +
      ' <span class="font-mono text-[10px] text-text-tertiary">' + t.name + '</span></div>' +
      '<div class="text-[10px] text-text-tertiary mt-0.5">' + escapeHtml(t.description.length > 90 ? t.description.slice(0, 90) + '…' : t.description) + '</div></div>' +
      '<div class="toggle-track ' + (on ? 'active' : '') + '" data-tool-toggle="' + t.name + '"><div class="toggle-thumb"></div></div></div>';
  }).join('');
  const card = document.createElement('div');
  card.id = 'toolsSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = [
    '<h3 class="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2"><i data-lucide="wrench" class="w-4 h-4 text-brand-violet"></i>AI 工具（Function Calling）</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">内置工具闭环：模型可调用时间、计算器、知识库检索、片段保存、工作流与费用查询；仅 OpenAI 兼容后端生效，Ollama 自动跳过。</p>',
    '<div class="flex items-center gap-4 mb-3">',
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">启用工具调用</span><div class="toggle-track ' + (MoraySettings.get('toolsEnabled') ? 'active' : '') + '" id="setToolsEnabled"><div class="toggle-thumb"></div></div></div>',
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">最大工具轮数</span><input type="number" min="1" max="10" value="' + (MoraySettings.get('toolsMaxRounds') || 3) + '" id="setToolsMaxRounds" class="form-input" style="width:64px;padding:2px 6px;font-size:12px"></div>',
    '</div>',
    '<div class="text-[10px] text-text-tertiary mb-1">工具开关（关闭总开关时请求体不携带 tools 字段）</div>',
    '<div id="toolsToolRows">' + toolRows + '</div>'
  ].join('');
  container.insertBefore(card, container.querySelector('#gatewaySettingsCard') || container.querySelector('#enhanceSettingsCard') || container.lastElementChild);
  refreshIcons();

  on(card.querySelector('#setToolsEnabled'), 'click', async function () {
    this.classList.toggle('active');
    await MoraySettings.set('toolsEnabled', this.classList.contains('active'));
  });
  on(card.querySelector('#setToolsMaxRounds'), 'change', () => {
    const v = Math.max(1, Math.min(10, parseInt(card.querySelector('#setToolsMaxRounds').value, 10) || 3));
    card.querySelector('#setToolsMaxRounds').value = v;
    MoraySettings.set('toolsMaxRounds', v);
  });
  card.querySelectorAll('[data-tool-toggle]').forEach(t => t.addEventListener('click', async function () {
    this.classList.toggle('active');
    const name = this.dataset.toolToggle;
    const list = MoraySettings.get('toolsDisabled') || [];
    const idx = list.indexOf(name);
    if (this.classList.contains('active')) { if (idx >= 0) list.splice(idx, 1); }
    else if (idx < 0) list.push(name);
    await MoraySettings.set('toolsDisabled', list);
  }));
}

/* ===================== [阶段0 本机 Agent] 设置卡 + 审计日志 ===================== */

/** [阶段1.5 E] 审计日志查看（表格呈现：时间/工具/参数摘要/审批/状态色/耗时；分页/导出/清空带确认）
 * @param {number} [page] - 页码（从 1 起）
 * @returns {Promise<void>} */
async function showAgentAuditLog(page) {
  page = Math.max(1, page || 1);
  const pageSize = 25;
  const base = agentBackendBase();
  let rows = [], total = 0;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    const res = await fetch(base + '/api/agent/log?limit=' + pageSize + '&offset=' + ((page - 1) * pageSize), { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(timer);
    const j = await res.json();
    if (j && j.ok) { rows = j.data.rows || []; total = j.data.total || 0; }
  } catch (e) { /* 离线 → 下方提示 */ }
  const statusLabel = {
    ok: ['成功', 'text-success'], error: ['失败', 'text-danger'], timeout: ['超时', 'text-danger'],
    rejected: ['安全拒绝', 'text-danger'], needs_approval: ['未审批', 'text-warning'], denied: ['用户拒绝', 'text-warning']
  };
  const esc = (s) => escapeHtml(String(s == null ? '' : s));
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const body = rows.length
    ? '<div class="overflow-auto max-h-[52vh] rounded-lg border border-line-ghost/50"><table class="w-full text-[10px]">' +
      '<thead><tr class="text-text-tertiary bg-surface-panel/70 sticky top-0">' +
      '<th class="text-left px-2 py-1.5 font-medium">时间</th><th class="text-left px-2 py-1.5 font-medium">工具</th>' +
      '<th class="text-left px-2 py-1.5 font-medium">参数摘要</th><th class="text-left px-2 py-1.5 font-medium">审批</th>' +
      '<th class="text-left px-2 py-1.5 font-medium">状态</th><th class="text-right px-2 py-1.5 font-medium">耗时</th></tr></thead><tbody>' +
      rows.map(r => {
        const st = statusLabel[r.status] || [r.status, 'text-text-tertiary'];
        return '<tr class="border-t border-line-ghost/40 hover:bg-surface-panel/50">' +
          '<td class="px-2 py-1.5 text-text-tertiary whitespace-nowrap">' + esc(r.ts) + '</td>' +
          '<td class="px-2 py-1.5 font-mono text-text-primary whitespace-nowrap">' + esc(r.tool) + '</td>' +
          '<td class="px-2 py-1.5 text-text-tertiary max-w-[260px] truncate" title="' + esc(r.args_summary) + '">' + esc(r.args_summary) + '</td>' +
          '<td class="px-2 py-1.5 ' + (r.approved ? 'text-success' : 'text-text-tertiary') + ' whitespace-nowrap">' + (r.approved ? '是' : '否') + '</td>' +
          '<td class="px-2 py-1.5 ' + st[1] + ' whitespace-nowrap">' + st[0] + '</td>' +
          '<td class="px-2 py-1.5 text-text-tertiary text-right whitespace-nowrap">' + (r.ms ? r.ms + 'ms' : '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>'
    : '<div class="empty-state"><i data-lucide="scroll-text"></i><div class="empty-title">暂无审计记录</div><div class="empty-hint">审计存于本地后端 SQLite；后端离线时无法读取</div></div>';
  const html = '<div class="space-y-2">' +
    '<div class="flex items-center gap-2 text-[10px] text-text-tertiary">' +
    '<span>共 ' + total + ' 条 · 第 ' + page + '/' + totalPages + ' 页</span>' +
    '<span class="ml-auto flex items-center gap-1.5">' +
    '<button data-audit-page="prev" class="btn-ghost px-2 py-0.5 rounded-md border border-line-ghost" ' + (page <= 1 ? 'disabled' : '') + '>上一页</button>' +
    '<button data-audit-page="next" class="btn-ghost px-2 py-0.5 rounded-md border border-line-ghost" ' + (page >= totalPages ? 'disabled' : '') + '>下一页</button>' +
    '<button data-audit-export class="btn-ghost px-2 py-0.5 rounded-md border border-line-ghost">导出 JSON</button>' +
    '<button data-audit-clear class="px-2 py-0.5 rounded-md text-[10px] bg-danger/10 text-danger border border-danger/30">清空</button>' +
    '</span></div>' + body + '</div>';
  const box = showModal(html, { title: '本机工具审计日志', icon: 'scroll-text', wide: true, footer: false });
  box.querySelector('[data-audit-page="prev"]')?.addEventListener('click', () => { closeModal(); showAgentAuditLog(page - 1); });
  box.querySelector('[data-audit-page="next"]')?.addEventListener('click', () => { closeModal(); showAgentAuditLog(page + 1); });
  box.querySelector('[data-audit-export]')?.addEventListener('click', () => {
    downloadJson('moray_agent_audit.json', { exportedAt: new Date().toISOString(), total, page, rows });
    showNotification('已导出', '本页审计记录已导出为 JSON', 'success', 2000);
  });
  box.querySelector('[data-audit-clear]')?.addEventListener('click', () => {
    showConfirm('清空审计日志', '将删除全部 ' + total + ' 条审计记录，此操作不可恢复。确定？', async () => {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 5000);
        const res = await fetch(base + '/api/agent/log', { method: 'DELETE', signal: ctrl.signal });
        clearTimeout(timer);
        const j = await res.json();
        if (!res.ok || !j.ok) throw new Error(j.message || ('后端返回 ' + res.status));
        showNotification('已清空', '审计日志已清空（' + j.data.cleared + ' 条）', 'success', 2200);
        closeModal();
        showAgentAuditLog(1);
      } catch (e) {
        showNotification('清空失败', String((e && e.message) || e).slice(0, 140), 'error', 3000);
      }
    }, { danger: true, okText: '清空' });
  });
  refreshIcons();
}

/** [阶段0] 设置页"本机 Agent"卡：后端连接/工作区根修改/审批策略/最大轮次/审计入口
 * @returns {Promise<void>} */
async function appendAgentSettings() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container || container.querySelector('#agentSettingsCard')) return;
  const cfg = await agentBackendConfig(true); // 强刷（避免设置页打开时读到陈旧缓存）
  const online = !!cfg;
  const approval = MoraySettings.get('agentApprovalMode') || 'readonly_auto';
  const rounds = Math.max(1, Math.min(12, parseInt(MoraySettings.get('toolsMaxRounds') || 8, 10) || 8));
  const card = document.createElement('div');
  card.id = 'agentSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = [
    '<h3 class="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2"><i data-lucide="square-terminal" class="w-4 h-4 text-brand-violet"></i>本机 Agent（受控工作区工具）</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">本机动作由本地后端执行并审计：列目录/读文件自动执行，写文件与只读命令需人工授权；全部操作限制在工作区内，越界/危险后缀/白名单外命令会被安全层拒绝。开关在输入台「本机工具」按钮。</p>',
    online
      ? '<div class="flex items-center gap-1.5 mb-3 text-[10px] text-success"><span class="beacon-dot success"></span>本地后端已连接 · 版本 ' + escapeHtml(cfg.version || '') + ' · 构建 ' + escapeHtml(cfg.build || '') + '</div>'
      : '<div class="flex items-center gap-1.5 mb-3 text-[10px] text-warning"><span class="beacon-dot warn-dot"></span>本地后端离线：本机工具不可用（需启动后端，默认 http://127.0.0.1:8000）</div>',
    '<div class="rounded-lg border border-line-ghost/60 p-3 mb-3">',
    '<div class="flex items-center justify-between mb-2"><span class="text-xs text-text-secondary">工作区根</span>' +
    (cfg && cfg.fromEnv ? '<span class="text-[9px] px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/20">环境变量 MORAY_WORKSPACE 锁定</span>' : '') + '</div>',
    '<div class="flex gap-2">',
    '<input type="text" id="agentWorkspaceInput" value="' + escapeHtml(cfg ? cfg.workspace : '') + '" placeholder="D:\\MoRayWorkspace"' + (cfg && cfg.fromEnv ? ' disabled' : '') + ' class="form-input flex-1 font-mono" style="font-size:12px;padding:4px 8px">',
    '<button id="agentWorkspaceSave" class="btn-ghost px-3 py-1.5 rounded-lg text-[10px] border border-line-ghost" ' + (cfg && cfg.fromEnv ? 'disabled' : '') + '>保存并校验</button>',
    '</div>',
    (cfg && cfg.fromEnv ? '<div class="text-[10px] text-text-tertiary mt-1.5">环境变量优先于此处设置；如需修改请调整 MORAY_WORKSPACE 后重启后端</div>' : '<div class="text-[10px] text-text-tertiary mt-1.5">修改后立即生效并写入后端（自动创建目录并校验可写；仅影响本机 Agent 工具）</div>'),
    '</div>',
    '<div class="space-y-2 text-xs">',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">审批策略</span>' +
    '<select id="agentApprovalSelect" class="form-select" style="width:auto;padding:4px 8px;font-size:12px">' +
    '<option value="readonly_auto"' + (approval === 'readonly_auto' ? ' selected' : '') + '>只读自动执行（写文件/命令需审批）</option>' +
    '<option value="all"' + (approval === 'all' ? ' selected' : '') + '>全部工具需人工审批</option></select></div>',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">最大工具轮数（1-12）</span>' +
    '<input type="number" min="1" max="12" value="' + rounds + '" id="agentMaxRoundsInput" class="form-input" style="width:64px;padding:2px 6px;font-size:12px"></div>',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">审计日志（后端 SQLite，含未审批与安全拒绝）</span>' +
    '<button id="agentAuditViewBtn" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">查看日志</button></div>',
    // [阶段1 M1] 上次 Agent 自检结果
    (() => {
      const sc = MoraySettings.get('agentSelfCheck');
      if (!sc || !sc.model) return '';
      const when = sc.at ? new Date(sc.at).toLocaleString() : '';
      return '<div class="flex items-center justify-between"><span class="text-text-secondary">上次模型自检</span>' +
        '<span class="text-[10px] ' + (sc.ok ? 'text-success' : 'text-warning') + '">' + escapeHtml(sc.model) + ' · ' + (sc.ok ? '✓ 支持工具调用' : '✘ 未返回工具调用') + ' · ' + escapeHtml(when) + '</span></div>';
    })(),
    // [阶段0.5 E] 本会话已信任操作（默认 0=每次都询问；勾选信任后累积；run_command 永不入表）
    '<div class="flex items-center justify-between"><span class="text-text-secondary">已信任操作（本会话，相同工具+参数免重复确认）</span>' +
    '<div class="flex items-center gap-2"><span class="font-mono text-[10px] text-text-tertiary" id="agentTrustedCount">' + agentTrustedCount() + '</span>' +
    '<button id="agentTrustedClear" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">清除</button></div></div>',
    // [阶段1 M5] Agent 系统提示：查看内置 / 自定义覆盖
    (() => {
      const override = (MoraySettings.get('agentSysPromptOverride') || '').trim();
      const effective = override || PlanTracker.SYSTEM_PROMPT;
      return '<div class="pt-2 mt-2 border-t border-line-ghost/40">' +
        '<div class="text-xs text-text-secondary mb-1.5">Agent 系统提示（多步任务先列计划、工具时机、工作区边界）</div>' +
        '<textarea id="agentSysPromptInput" class="form-input w-full font-mono" rows="6" style="font-size:10px;line-height:1.6" placeholder="留空 = 使用内置规范">' + escapeHtml(effective) + '</textarea>' +
        '<div class="flex items-center gap-2 mt-1.5">' +
        '<button id="agentSysPromptSave" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">保存覆盖</button>' +
        '<button id="agentSysPromptReset" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">恢复内置</button>' +
        '<span class="text-[9px] text-text-tertiary">仅在本机工具开启时追加到系统提示</span></div></div>';
    })(),
    '<div class="flex items-center justify-between pt-1 border-t border-line-ghost/40"><span class="text-[10px] text-text-tertiary">工具：列目录 / 读文件（自动）；写文件 / 只读命令（审批）。命令白名单：git status/log/diff/branch、python/node/npm --version、where &lt;名&gt;</span></div>',
    '</div>'
  ].join('');
  container.insertBefore(card, container.querySelector('#gatewaySettingsCard') || container.querySelector('#enhanceSettingsCard') || container.lastElementChild);
  refreshIcons();
  // ---- 事件 ----
  on(card.querySelector('#agentApprovalSelect'), 'change', async function () {
    await MoraySettings.set('agentApprovalMode', this.value);
    showNotification('审批策略已更新', this.value === 'all' ? '全部工具需人工审批' : '只读工具自动执行', 'success', 1600);
  });
  const roundsInput = card.querySelector('#agentMaxRoundsInput');
  on(roundsInput, 'change', async () => {
    const v = Math.max(1, Math.min(12, parseInt(roundsInput.value, 10) || 8));
    roundsInput.value = v;
    await MoraySettings.set('toolsMaxRounds', v);
    showNotification('已保存', '最大工具轮数 = ' + v, 'success', 1500);
  });
  on(card.querySelector('#agentAuditViewBtn'), 'click', () => showAgentAuditLog());
  // [阶段0.5 E] 清除本会话已信任操作（相同工具+参数将重新询问）
  const trustedClear = card.querySelector('#agentTrustedClear');
  if (trustedClear) on(trustedClear, 'click', () => {
    const n = agentTrustedCount();
    __agentTrustedOps.clear();
    const cnt = card.querySelector('#agentTrustedCount');
    if (cnt) cnt.textContent = String(agentTrustedCount());
    showNotification('已清除', '本会话已信任操作已清空（' + n + ' 条），之后全部重新询问', 'success', 2200);
  });
  // [阶段1 M5] Agent 系统提示保存/恢复
  const sysInput = card.querySelector('#agentSysPromptInput');
  const sysSave = card.querySelector('#agentSysPromptSave');
  const sysReset = card.querySelector('#agentSysPromptReset');
  if (sysSave) on(sysSave, 'click', async () => {
    const v = String(sysInput.value || '').trim();
    if (!v) { showNotification('不能为空', '留空请用「恢复内置」；覆盖内容不能为空', 'warning', 2600); return; }
    await MoraySettings.set('agentSysPromptOverride', v);
    showNotification('已保存', '自定义 Agent 系统提示已生效（本机工具开启时追加）', 'success', 2600);
  });
  if (sysReset) on(sysReset, 'click', async () => {
    await MoraySettings.set('agentSysPromptOverride', '');
    sysInput.value = PlanTracker.SYSTEM_PROMPT;
    showNotification('已恢复内置', 'Agent 系统提示回到内置规范', 'success', 2200);
  });
  const saveBtn = card.querySelector('#agentWorkspaceSave');
  const wsInput = card.querySelector('#agentWorkspaceInput');
  if (saveBtn) on(saveBtn, 'click', async () => {
    const v = String(wsInput.value || '').trim();
    if (!v) { showNotification('未修改', '请输入新的工作区路径', 'warning', 1800); return; }
    saveBtn.textContent = '校验中…';
    saveBtn.disabled = true;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 8000);
      const res = await fetch(agentBackendBase() + '/api/agent/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workspace: v }), signal: ctrl.signal
      });
      clearTimeout(timer);
      const j = await res.json().catch(() => null);
      if (!res.ok || !j || !j.ok) throw new Error((j && j.message) || ('后端返回 ' + res.status));
      showNotification('工作区已更新', j.data.workspace, 'success', 2500);
      card.querySelector('#agentWorkspaceInput').value = j.data.workspace;
    } catch (e) {
      showNotification('工作区修改失败', String((e && e.message) || e).slice(0, 200), 'error', 3500);
    } finally {
      saveBtn.textContent = '保存并校验';
      saveBtn.disabled = false;
    }
  });
}

// 复制事件委托（加载即装，幂等）
ToolRegistry._installCopyHandler();

/** 对比模式提示（由 boot 在 CompareApp.build 之后调用，避免被 innerHTML 重建清掉；幂等）
 * @returns {void} */
function installCompareToolsTip() {
  const cmpInput = document.querySelector('#chat-compare textarea');
  if (!cmpInput) return;
  const wrap = cmpInput.closest('.flex') ? cmpInput.closest('.flex').parentElement : cmpInput.parentElement;
  if (wrap && !wrap.querySelector('.compare-tools-tip')) {
    const tip = document.createElement('div');
    tip.className = 'compare-tools-tip text-[10px] text-text-tertiary mt-1';
    tip.textContent = '对比模式不启用工具调用';
    wrap.appendChild(tip);
  }
}

/* ============================================================
   [阶段0 本机 Agent] 本机工具（受控工作区文件/只读命令）
   - 后端执行：本机动作必须由本地后端（127.0.0.1）执行，浏览器只发受控请求
   - 协议：POST /api/agent/tool {name, args, approved} → {ok,data} / {ok:false,error} / {needsApproval}
   - 审批：write_file / run_command 属副作用 → 先弹人工审批（允许一次/拒绝）；
     拒绝不上报执行，把"用户拒绝了该操作"作为 tool 结果回灌模型
   - 开关：nativeToolsEnabled=false 时（输入台未开启）native 工具不进入 listForRequest
   - 降级：后端离线/未配置时工具执行给出明确中文错误，绝不白屏/卡死
   ============================================================ */

/** 后端 origin（与 MorayBackend 探测/后端同步同一事实源；file:// 回退默认端口）
 * @returns {string} 后端 origin（无尾斜杠） */
function agentBackendBase() {
  try {
    const mb = window.MorayBackend;
    if (mb && mb.origin) return mb.origin;
  } catch (e) { /* 忽略 */ }
  if (typeof window.resolveBackendOrigin === 'function') {
    try { return window.resolveBackendOrigin(); } catch (e) { /* 忽略 */ }
  }
  return 'http://127.0.0.1:8000';
}

/** 后端 /api/agent/config 探测（10s 短缓存，供开关/提示/设置页复用）
 * @param {boolean} [force] - 强制刷新
 * @returns {Promise<Object|null>} 配置（null=后端离线/未提供本机 Agent） */
let __agentCfgCache = { at: 0, data: null };
async function agentBackendConfig(force) {
  const now = Date.now();
  if (!force && __agentCfgCache.data && now - __agentCfgCache.at < 10000) return __agentCfgCache.data;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(agentBackendBase() + '/api/agent/config', { signal: ctrl.signal, cache: 'no-store' });
    if (!res.ok) { __agentCfgCache = { at: now, data: null }; return null; }
    const j = await res.json();
    __agentCfgCache = { at: now, data: (j && j.ok) ? j.data : null };
    return __agentCfgCache.data;
  } catch (e) {
    __agentCfgCache = { at: now, data: null };
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** [阶段0.5 E] 参数稳定序列化（键排序，保证"相同参数"指纹稳定）
 * @param {*} v - 任意 JSON 值
 * @returns {string} 稳定 JSON 串 */
function agentStableStringify(v) {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map(x => agentStableStringify(x)).join(',') + ']';
  const keys = Object.keys(v).sort();
  return '{' + keys.map(k => JSON.stringify(k) + ':' + agentStableStringify(v[k])).join(',') + '}';
}

/** [阶段0.5 E] 本会话已信任操作表（内存态，刷新即失效；默认空=每次都询问）
 * 指纹 = 工具名 + 规范化参数 JSON；run_command 永不入表（命令永远每次审批）
 * @type {Map<string, boolean>} */
const __agentTrustedOps = new Map();

/** 操作指纹
 * @param {string} name - 工具名
 * @param {Object} args - 参数
 * @returns {string} 指纹 */
function agentFingerprint(name, args) {
  return name + '::' + agentStableStringify(args || {});
}

/** [批次修复 #8] 审批用指纹：写类工具（write_file/edit_file）额外并入“目标文件当前内容哈希”——
 * 文件内容已变化时，即使工具+参数相同也必须重新弹审批；read 类只读工具不受影响。
 * @param {Object} def - 工具定义
 * @param {Object} args - 参数
 * @returns {Promise<string>} 指纹 */
async function agentFingerprintForApproval(def, args) {
  let fp = agentFingerprint(def.name, args || {});
  if (def.name === 'write_file' || def.name === 'edit_file') {
    const path = String((args && args.path) || '');
    try {
      const data = await wsFetchTool('read_file', { path, maxBytes: 65536 });
      const content = (data && data.content != null) ? String(data.content) : '';
      fp += '::h' + (typeof fnvHash64 === 'function' ? fnvHash64(content) : String(content.length));
    } catch (e) {
      fp += '::missing';
    }
  }
  return fp;
}

/** 已信任操作数（设置页展示/清除用）
 * @returns {number} 数量 */
function agentTrustedCount() {
  return __agentTrustedOps.size;
}

/** 人工审批弹窗：展示工具名 + 参数（写文件=目标路径+内容预览；命令=完整命令），
 * "允许一次 / 拒绝"两个明确按钮；X/遮罩点击/ESC/生成停止一律收敛为"拒绝"（安全默认）。
 * [阶段0.5 E] 非 run_command 工具可勾选"本会话对完全相同的工具+参数不再询问"（默认不勾）。
 * 挂载期间若 modalOverlay 失去 active（任何途径关闭）→ 按拒绝处理，绝不留悬挂 Promise。
 * @param {Object} def - 工具定义
 * @param {Object} args - 参数
 * @param {Object} [ctx] - 上下文（signal 中止时关闭并按拒绝处理，上层据此抛 AbortError）
 * @returns {Promise<{allowed:boolean, trusted:boolean}>} allowed=用户允许一次 */
function agentApproval(def, args, ctx) {
  return (async () => {
    let settled = false;
    let resolveFn = null;
    const promise = new Promise((resolve) => { resolveFn = resolve; });
    let done = (v, trusted) => {
      if (settled) return;
      settled = true;
      try { obs.disconnect(); } catch (e) { /* 忽略 */ }
      try { if (ctx && ctx.signal) ctx.signal.removeEventListener('abort', onAbort); } catch (e) { /* 忽略 */ }
      closeModal();
      resolveFn({ allowed: v, trusted: !!trusted });
    };
    const onAbort = () => done(false, false);
    if (ctx && ctx.signal) {
      if (ctx.signal.aborted) { setTimeout(() => done(false, false), 0); return promise; }
      ctx.signal.addEventListener('abort', onAbort, { once: true });
    }
    // [阶段1 M4] write_file 审批带 unified diff：异步取旧文件内容（新文件=全 +；过大截取 64KB）
    let writeDiff = '';
    if (def.name === 'write_file') {
      const p = String((args && args.path) || '').trim();
      try {
        const probe = await fetch(agentBackendBase() + '/api/agent/tool', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'read_file', args: { path: p, maxBytes: 65536 } })
        });
        const pj = await probe.json().catch(() => null);
        const oldContent = (pj && pj.ok && pj.data && pj.data.content != null) ? pj.data.content : '';
        const newContent = String((args && args.content) || '');
        if (oldContent) {
          writeDiff = buildApprovalDiff(oldContent, newContent, 400) +
            (pj.data.truncated ? '<div class="text-[9px] text-warning mt-1">原文件超过 64KB，diff 仅基于前 64KB</div>' : '');
        }
      } catch (e) { /* 读不到旧内容（新文件/离线）→ 不渲染 diff */ }
    }
    // 参数卡展示（写文件=路径+预览；命令=完整命令；其余=参数 JSON）
    let argCards = '';
    const cell = 'rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2';
    if (def.name === 'write_file') {
      const c = String(args.content || '');
      const diffBlock = writeDiff
        ? '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1.5">变更预览（- 原内容 / + 新内容）</div>' + writeDiff + '</div>'
        : '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1">内容预览（前 300 字符）</div>' +
          '<div class="text-[10px] text-text-primary whitespace-pre-wrap break-all max-h-28 overflow-auto font-mono">' + (c ? escapeHtml(c.slice(0, 300)) + (c.length > 300 ? '<span class="text-warning"> …（已截断，共 ' + c.length + ' 字符）</span>' : '') : '<span class="text-text-tertiary">（空内容）</span>') + '</div></div>';
      argCards = '<div class="rounded-lg bg-surface-panel/60 border border-line-ghost/50 p-3">' +
        '<div class="text-[10px] text-text-tertiary mb-1.5">目标文件（工作区内，相对路径）</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.path || '')) + '</div>' +
        (args.overwrite === false ? '<div class="text-[10px] text-warning mt-1">仅新建，不覆盖已有文件</div>' : '<div class="text-[10px] text-text-tertiary mt-1">已有同名文件将被覆盖</div>') +
        '</div>' + diffBlock;
    } else if (def.name === 'run_command') {
      const parts = [String(args.command || '')].concat(Array.isArray(args.args) ? args.args.map(String) : []);
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1.5">将执行的只读白名单命令</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">$ ' + escapeHtml(parts.join(' ')) + '</div>' +
        '<div class="text-[10px] text-text-tertiary mt-1.5">命令只读、15s 超时，在工作区内执行（git status/log/diff/branch、python/node/npm --version、where &lt;命令名&gt;）</div></div>';
    } else if (def.name === 'edit_file') {
      const oldS = String(args.old_str || '');
      const newS = String(args.new_str || '');
      const diffHtml = (typeof buildApprovalDiff === 'function')
        ? buildApprovalDiff(oldS, newS, 400)
        : '<div class="text-[10px] text-text-primary font-mono whitespace-pre-wrap break-all max-h-40 overflow-auto">' + escapeHtml(oldS.slice(0, 200)) + '</div>';
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1.5">编辑目标（工作区内）</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.path || '')) + '</div>' +
        (args.replace_all === true ? '<div class="text-[10px] text-warning mt-1">replace_all：替换文件中全部匹配处</div>' : '<div class="text-[10px] text-text-tertiary mt-1">仅替换唯一匹配处（不唯一将被拒绝）</div>') +
        '</div>' +
        '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1.5">变更预览（- 原内容 / + 新内容）</div>' + diffHtml + '</div>';
    } else {
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1.5">参数</div>' +
        '<div class="font-mono text-[10px] text-text-primary break-all max-h-24 overflow-auto">' + escapeHtml(JSON.stringify(args || {})) + '</div></div>';
    }
    // [阶段0.5 E] 信任勾选：仅非 run_command 工具提供（命令永远每次审批）；默认不勾选
    const trustRow = def.name !== 'run_command'
      ? '<label class="flex items-start gap-2 pt-1 cursor-pointer select-none">' +
        '<input type="checkbox" data-agent-trust class="mt-0.5 accent-brand-cobalt">' +
        '<span class="text-[10px] text-text-tertiary leading-relaxed">本会话对完全相同的工具+参数不再询问（刷新后失效；可在设置→本机 Agent 清除）</span></label>'
      : '';
    const html = '<div class="space-y-2.5 text-xs">' +
      '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1">工具（' + (def.sideEffect ? '副作用，需要你授权' : '只读') + '）</div>' +
      '<div class="flex items-center gap-1.5 font-medium text-text-primary">' +
      '<i data-lucide="' + (def.name === 'run_command' ? 'terminal' : def.name === 'write_file' ? 'file-pen-line' : 'folder-search') + '" class="w-3.5 h-3.5 text-warning"></i>' +
      escapeHtml(def.label) + ' <span class="font-mono text-[10px] text-text-tertiary">' + def.name + '</span></div></div>' +
      argCards +
      trustRow +
      '<div class="flex gap-2 justify-end pt-1">' +
      '<button data-agent-approve-no class="px-4 py-2 rounded-lg text-[12px] border border-line-ghost text-text-secondary hover:bg-surface-panel">拒绝</button>' +
      '<button data-agent-approve-ok class="px-4 py-2 rounded-lg text-[12px] btn-primary">允许一次</button>' +
      '</div></div>';
    let box;
    try { box = showModal(html, { title: '本机工具需要你的授权', icon: 'shield-alert', footer: false }); }
    catch (e) { done(false, false); return; }
    const okBtn = box.querySelector('[data-agent-approve-ok]');
    const noBtn = box.querySelector('[data-agent-approve-no]');
    if (okBtn) okBtn.addEventListener('click', async () => {
      const trust = box.querySelector('[data-agent-trust]');
      const trusted = !!(trust && trust.checked);
      if (trusted) {
        // [批次修复 #8] 写类工具信任指纹含目标内容哈希（async 取当前文件内容）
        __agentTrustedOps.set(await agentFingerprintForApproval(def, args || {}), true);
      }
      done(true, trusted);
    });
    if (noBtn) noBtn.addEventListener('click', () => done(false, false));
    // 任何途径关闭（X/遮罩/ESC，由 20_ai 全局监听移除 active）→ 一律按拒绝
    const overlay = document.getElementById('modalOverlay');
    const obs = overlay ? new MutationObserver(() => {
      if (!overlay.classList.contains('active') && !settled) done(false, false);
    }) : { disconnect() {} };
    if (overlay) obs.observe(overlay, { attributes: true, attributeFilter: ['class'] });
    // [阶段1.5 F] 键盘：Enter=允许一次 / Esc=拒绝（Esc 由全局监听关 modal → 按"拒绝"收敛，
    // 这里补 Enter 快捷键；避免输入元素聚焦时误触）
    const keyHandler = (e) => {
      if (settled) return;
      const tag = (document.activeElement && document.activeElement.tagName) || '';
      if (tag === 'TEXTAREA' || (tag === 'INPUT' && document.activeElement.type !== 'checkbox')) return;
      if (e.key === 'Enter') { e.preventDefault(); done(true, !!(box.querySelector('[data-agent-trust]') || {}).checked); }
    };
    document.addEventListener('keydown', keyHandler);
    const _origDone = done;
    done = function (v, trusted) { document.removeEventListener('keydown', keyHandler); _origDone(v, trusted); };
    refreshIcons();
    return promise;
  })();
}

/** 拒绝上报（仅写审计，后端绝不执行）
 * @param {string} name - 工具名
 * @param {Object} args - 参数 */
function agentReportDenied(name, args) {
  return fetch(agentBackendBase() + '/api/agent/tool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, args: args || {}, decision: 'denied' })
  }).catch(() => null);
}

/** native 工具通用执行体：
 * [阶段0.5 E] 相同操作指纹本会话免重复确认（默认关：仅当用户在审批卡勾选信任后才入表；
 * run_command 永不享受）→ 审批（副作用工具或"全部审批"策略，未信任时）→ approved:true →
 * POST /api/agent/tool
 * @param {Object} def - 工具定义
 * @param {Object} args - 参数
 * @param {Object} [ctx] - 上下文（signal 取消 + onStatus('pending') 通知步骤卡）
 * @returns {Promise<Object>} 后端 data */
async function agentNativeRun(def, args, ctx) {
  // [批次修复 #8] 审批判断用“含内容哈希”的指纹（写类工具；read 类仅工具+参数）
  const fp = await agentFingerprintForApproval(def, args || {});
  // run_command 永不入信任表：指纹命中也照样弹审批
  const trusted = def.name !== 'run_command' && __agentTrustedOps.has(fp);
  const needApprove = (def.sideEffect === true || MoraySettings.get('agentApprovalMode') === 'all') && !trusted;
  if (needApprove) {
    // 步骤卡先转"待审批"（时间线可见等待授权）
    if (ctx && typeof ctx.onStatus === 'function') { try { ctx.onStatus('pending'); } catch (e) { /* 忽略 */ } }
    const verdict = await agentApproval(def, args || {}, ctx);
    // 生成被停止（AbortController）→ 抛 AbortError 交由循环整体中止
    if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    if (!verdict || !verdict.allowed) {
      // 用户拒绝：绝不执行；上报审计 + 把拒绝作为 tool 结果回灌模型（模型据此回应）
      agentReportDenied(def.name, args || {}).catch(() => {});
      return { denied: true, message: '用户拒绝了该操作（' + def.label + ' 未执行）' };
    }
  }
  let res;
  try {
    res = await fetch(agentBackendBase() + '/api/agent/tool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: def.name, args: args || {}, approved: true }),
      signal: (ctx && ctx.signal) || undefined
    });
  } catch (e) {
    // 网络层失败：fetch 抛 TypeError（后端离线）或 AbortError（用户停止）
    if (e && e.name === 'AbortError') throw e;
    throw new Error('本机工具需要启动本地后端（未连接 127.0.0.1），' + def.label + ' 未执行');
  }
  let j = null;
  try { j = await res.json(); } catch (e) { /* 兜底 */ }
  if (!res.ok) throw new Error((j && j.message) || ('本机后端返回 ' + res.status));
  if (j && j.needsApproval) throw new Error('后端要求人工审批，请求未执行（' + def.label + '）');
  if (!j || j.ok !== true) throw new Error((j && j.message) || '本机工具执行失败');
  // [阶段0.5 D] 写文件成功 → 通知工作区文件树刷新（面板开着时自动刷新并高亮新文件）
  if (def.name === 'write_file' && j.data && j.data.path) {
    try { window.dispatchEvent(new CustomEvent('moray:ws-file-written', { detail: { path: j.data.path } })); } catch (e) { /* 忽略 */ }
  }
  return j.data;
}

/** [阶段0] 四个本机工具定义（enabledByDefault=false 语义由 ToolRegistry.isEnabled 的
 * nativeToolsEnabled 门实现；native=true 的工具默认不进入 listForRequest） */
const NATIVE_TOOLS = [
  {
    name: 'list_directory', label: '列出目录', native: true, sideEffect: false, timeoutMs: 30000,
    description: '列出受控工作区内指定目录的文件与子目录（名称/类型/大小/修改时间，单层最多 500 条）。当用户问"工作区里有什么文件 / 目录里有哪些内容 / 看看 X 文件夹"时使用；path 省略表示工作区根。工作区是本地后端隔离目录（默认 D:\\MoRayWorkspace），与聊天记录无关。',
    parameters: {
      type: 'object',
      properties: { path: { type: 'string', description: '相对工作区根的目录路径（省略 = 工作区根）' } }
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'read_file', label: '读取文件', native: true, sideEffect: false, timeoutMs: 30000,
    description: '读取工作区内文本文件内容（UTF-8，默认最多 64KB，可用 maxBytes 调大，上限 1MB；二进制或不可解码文件返回明确错误）。当用户说"读一下 X 文件 / X 文件里写了什么 / 看看这段配置"时使用；path 为相对工作区根的文件路径。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '相对工作区根的文件路径（必填）' },
        maxBytes: { type: 'integer', description: '最大读取字节数（默认 65536，上限 1048576）' }
      },
      required: ['path']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'write_file', label: '写文件', native: true, sideEffect: true, timeoutMs: 300000,
    description: '在工作区内创建或覆盖一个文本文件（UTF-8，需用户授权；父目录自动创建；禁止写入可执行/危险后缀）。当用户要求"把内容保存成文件 / 新建 X 文件写入… / 把结果写到 Y"时使用；path 必须相对工作区根，禁止越界访问工作区以外的路径。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '相对工作区根的目标文件路径（必填）' },
        content: { type: 'string', description: '文件内容（UTF-8）' },
        overwrite: { type: 'boolean', description: '已存在时是否覆盖（默认 true）' }
      },
      required: ['path', 'content']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'run_command', label: '运行只读命令', native: true, sideEffect: true, timeoutMs: 300000,
    description: '在工作区内执行只读白名单命令并返回输出（需用户授权）。仅支持：git status/log/diff/branch、python --version、node --version、npm --version、where <命令名>；15 秒超时；禁止 shell 管道与危险参数。当用户想"查看目录是不是 git 仓库 / git 状态 / 环境里有没有装 python/node / 某个命令在哪"时使用。',
    parameters: {
      type: 'object',
      properties: {
        command: { type: 'string', description: '白名单命令名：git / python / node / npm / where' },
        args: { type: 'array', items: { type: 'string' }, description: '命令参数数组（如 ["--version"] 或 git 的 ["status"]）' }
      },
      required: ['command']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'find_files', label: '查找文件', native: true, sideEffect: false, timeoutMs: 30000,
    description: '在工作区内按文件名通配模式递归查找文件（如 "*.txt"、"*report*"、"config*"），返回匹配路径/大小/修改时间，最多 1000 条。当用户说"找到所有 txt 文件 / 哪里有 config 文件 / 找一下名字带 report 的文件"时使用；不确定路径时先用它定位再 read_file。pattern 省略为列出全部文件。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '起始目录（相对工作区根，省略为全工作区）' },
        pattern: { type: 'string', description: '文件名通配模式（如 *.txt、*report*；省略为全部文件）' }
      }
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'search_text', label: '全文搜索', native: true, sideEffect: false, timeoutMs: 30000,
    description: '在工作区文本文件内容中搜索关键词或正则，返回 [{文件, 行号, 行内容}]，默认最多 200 条。当用户说"哪个文件里有 XXX / 搜一下 TODO / 找到包含某函数名的代码"时使用；比 find_files（按文件名）更准。regex=true 时 query 按正则解释；二进制/非 UTF-8 文件自动跳过。',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '要搜索的文本或正则表达式（必填）' },
        path: { type: 'string', description: '起始目录（相对工作区根，省略为全工作区）' },
        glob: { type: 'string', description: '文件名过滤（如 *.py，只搜这类文件）' },
        regex: { type: 'boolean', description: 'query 是否为正则表达式（默认 false 按普通文本）' }
      },
      required: ['query']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'edit_file', label: '编辑文件', native: true, sideEffect: true, timeoutMs: 300000,
    description: '对工作区内已有文本文件做精确字符串替换（需用户授权）：old_str 必须与文件内容完全一致且唯一（不唯一时报错，应先 read_file 获取更多上下文；replace_all=true 替换全部）。当用户要求"把 X 改成 Y / 修改文件中的某处"时优先使用它而不是 write_file 整体重写；修改前建议先 read_file。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '相对工作区根的目标文件路径（必填，文件必须已存在）' },
        old_str: { type: 'string', description: '要被替换的精确文本（必填，非空；需与文件内容逐字符一致）' },
        new_str: { type: 'string', description: '替换后的文本（可为空串表示删除该段）' },
        replace_all: { type: 'boolean', description: 'old_str 出现多次时是否全部替换（默认 false，不唯一时报错）' }
      },
      required: ['path', 'old_str', 'new_str']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    // [阶段1 M3] 计划编排：纯前端工具（不调后端），native 门控（随"本机工具"开关进入请求）
    name: 'submit_plan', label: '提交任务计划', native: true, sideEffect: false, timeoutMs: 5000,
    description: '把多步任务的执行计划提交给用户查看（每步含简短意图与预计使用的工具名）。规则：任务需要 2 个以上步骤时，在开始任何文件操作前先调用本工具登记计划；然后按计划顺序逐步执行；全部完成后用一段话总结。steps 最多 12 步。',
    parameters: {
      type: 'object',
      properties: {
        steps: {
          type: 'array',
          description: '有序步骤数组（2-12 项）',
          items: {
            type: 'object',
            properties: {
              title: { type: 'string', description: '这一步做什么（简短一句话）' },
              tool: { type: 'string', description: '预计使用的工具名（如 find_files / read_file / edit_file）' }
            },
            required: ['title']
          }
        }
      },
      required: ['steps']
    },
    run(args) { return PlanTracker.submit(args); }
  }
];
NATIVE_TOOLS.forEach(t => ToolRegistry.register(t));

/* ============================================================
   [阶段1 M3] 计划-执行编排（复用现有 Agent Loop 与 onToolStep 时间线）
   - submit_plan 为纯前端工具：模型多步任务先提交计划 → PlanTracker 登记 → 计划卡渲染
     在时间线之上；随后每次真实工具事件（onToolStep）按"工具名+最早待办"匹配推进步骤状态
   - 单步失败：真实错误由工具结果回灌模型自行重试（同一步骤失败满 3 次标记"已跳过"防死循环；
     maxRounds 兜底）；全部步骤结束由模型输出总结
   ============================================================ */

const PlanTracker = {
  /** [阶段1 M5] 内置 Agent 系统提示（设置→本机 Agent 可自定义覆盖）
   * @type {string} */
  SYSTEM_PROMPT: [
    '# 本机 Agent 工作规范（受控工作区内操作）',
    '- 工具与时机：list_directory 列目录；read_file 读文本文件；find_files 按文件名通配查找（不确定路径时先用它定位）；search_text 全文搜索（按内容找，支持正则）；write_file 新建或整体覆盖文件；edit_file 精确替换已有文件的某一段（修改已有文件优先用 edit_file，不要整体重写）；run_command 只读白名单命令（git status/log/diff/branch、python/node/npm --version、where）。',
    '- 工作区边界：所有 path 都必须是相对工作区根的路径；你无法访问工作区以外的任何文件；禁止臆造不存在的路径——不确定就先用 find_files / list_directory 确认。',
    '- 多步任务：需要 2 个以上步骤时，先调用 submit_plan 提交编号计划（每步含意图与预计工具），然后按计划逐步执行；某步失败就修正参数重试（最多 2 次），全部完成后用一段话总结结果。',
    '- 授权与拒绝：write_file / edit_file / run_command 会先请求用户授权；被拒绝时不要重复尝试相同操作，改用文字向用户说明。',
    '- 文件内容为 UTF-8 文本；二进制或非 UTF-8 文件会收到明确错误，不要反复尝试。',
    '- edit_file 的 old_str 必须与文件内容逐字符一致且唯一；先 read_file 再编辑，匹配不唯一时补充上下文或用 replace_all。'
  ].join('\n'),

  /** 当前会话活动计划 @type {null|{steps:Array, startedAt:number}} */
  current: null,

  /** submit_plan 工具入口：登记计划（数据），渲染交给 observe 的 success 事件
   * @param {Object} args - {steps:[{title,tool}]}
   * @returns {Object} 确认与执行指引 */
  submit(args) {
    const raw = Array.isArray(args && args.steps) ? args.steps : [];
    const steps = raw.slice(0, 12).map(s => ({
      title: String((s && s.title) || '').trim().slice(0, 100),
      tool: String((s && s.tool) || '').trim().slice(0, 40),
      status: 'todo', // todo|running|done|failed|skipped
      fails: 0
    })).filter(s => s.title);
    if (!steps.length) throw new Error('计划至少需要一个有效步骤（steps[].title）');
    this.current = { steps, startedAt: Date.now() };
    return {
      registered: steps.length,
      message: '计划已登记并展示给用户（共 ' + steps.length + ' 步）。请按顺序逐步执行：' +
        '每步用对应的工具完成，完成一步再进行下一步；若某步失败请修正参数重试' +
        '（同一失败两次以上请调整方案）；全部完成后用一段话向用户总结结果。'
    };
  },

  /** onToolStep 观察入口：submit_plan 自身事件只负责渲染，真实工具事件推进步骤状态
   * @param {Object} evt - {name, status, ...}
   * @param {HTMLElement} [el] - 当前助手气泡 */
  observe(evt, el) {
    if (!evt || !evt.name) return;
    if (evt.name === 'submit_plan') {
      if (evt.status === 'success' && this.current && el) {
        // 计划挂到消息持久化（会话重建时经 renderStatic 恢复展示）
        try {
          const msgId = el.dataset ? el.dataset.msgId : null;
          const msg = (typeof AppState !== 'undefined' && msgId) ? (AppState.messages || []).find(m => m.id === msgId) : null;
          if (msg) {
            msg.plan = { steps: this.current.steps.map(s => ({ title: s.title, tool: s.tool, status: s.status })) };
            if (typeof persistMessage === 'function') persistMessage(msg).catch(() => {});
          }
        } catch (e) { /* 持久化失败不影响展示 */ }
        this.render(el);
      }
      return;
    }
    if (!this.current) return;
    const steps = this.current.steps;
    // 匹配：最早的 待办/进行中 且 工具匹配（计划未指定工具则任意工具可匹配）
    const idx = steps.findIndex(s => (s.status === 'todo' || s.status === 'running') && (!s.tool || s.tool === evt.name));
    if (idx < 0) return;
    const s = steps[idx];
    if (evt.status === 'running' || evt.status === 'pending') {
      if (s.status === 'todo') s.status = 'running';
    } else if (evt.status === 'success') {
      s.status = 'done';
    } else if (evt.status === 'error' || evt.status === 'denied') {
      s.fails = (s.fails || 0) + 1;
      s.status = s.fails >= 3 ? 'skipped' : 'failed';
      // 回到待办让模型重试（skipped 不再匹配）
      if (s.status === 'failed') s.status = 'todo';
    }
    // [阶段1.6] 状态流转同步到消息持久化（会话重建时计划卡显示最新状态）
    try {
      const msgId = el && el.dataset ? el.dataset.msgId : null;
      const msg = (typeof AppState !== 'undefined' && msgId) ? (AppState.messages || []).find(m => m.id === msgId) : null;
      if (msg && msg.plan && msg.plan.steps[idx]) {
        msg.plan.steps[idx].status = s.status;
        if (typeof persistMessage === 'function') persistMessage(msg).catch(() => {});
      }
    } catch (e) { /* 忽略 */ }
    if (el) this.render(el);
  },

  /** 计划卡 HTML（时间线之上的汇总层；[阶段1.5 F] 头部可点击整体折叠，进行步高亮）
   * @param {HTMLElement} el - 当前助手气泡
   * @returns {void} */
  render(el) {
    if (!this.current || !el) return;
    const stMap = {
      todo: ['待办', 'text-text-tertiary', ''],
      running: ['进行中', 'text-brand-cyan', 'animate-pulse'],
      done: ['完成', 'text-success', ''],
      failed: ['失败', 'text-danger', ''],
      skipped: ['已跳过', 'text-warning', '']
    };
    const stIcon = { todo: 'circle', running: 'loader-2', done: 'check-circle-2', failed: 'x-circle', skipped: 'skip-forward' };
    const rows = this.current.steps.map((s, i) => {
      const [label, color, pulse] = stMap[s.status] || stMap.todo;
      const icon = stIcon[s.status] || 'circle';
      return '<div class="plan-step-row flex items-center gap-2 py-0.5 px-1' + (s.status === 'running' ? ' running' : '') + '">' +
        '<span class="font-mono text-[10px] text-text-tertiary w-4">' + (i + 1) + '.</span>' +
        '<i data-lucide="' + icon + '" class="w-3 h-3 ' + color + (s.status === 'running' ? ' animate-spin' : '') + '"></i>' +
        '<span class="text-[11px] ' + color + (pulse ? ' ' + pulse : '') + ' shrink-0">' + label + '</span>' +
        '<span class="text-[11px] text-text-primary truncate flex-1">' + escapeHtml(s.title) + '</span>' +
        (s.tool ? '<span class="font-mono text-[9px] text-text-tertiary shrink-0">' + escapeHtml(s.tool) + '</span>' : '') +
        '</div>';
    }).join('');
    const doneN = this.current.steps.filter(s => s.status === 'done').length;
    const html = '<div class="plan-card rounded-lg bg-surface-panel/60 border border-brand-violet/25 px-3 py-2 my-2" data-plan-card>' +
      '<div class="plan-head flex items-center gap-1.5 mb-1" data-plan-toggle title="点击折叠/展开计划">' +
      '<i data-lucide="list-checks" class="w-3.5 h-3.5 text-brand-violet"></i>' +
      '<span class="text-[11px] font-medium text-text-primary">任务计划</span>' +
      '<span class="text-[10px] text-text-tertiary ml-auto">' + doneN + '/' + this.current.steps.length + ' 完成</span>' +
      '<i data-lucide="chevron-down" class="w-3 h-3 text-text-tertiary"></i></div>' +
      '<div class="plan-steps space-y-0.5">' + rows + '</div></div>';
    let wrap = el.querySelector('.tool-steps-wrap');
    const box = el.querySelector('[data-plan-card]');
    if (box) box.outerHTML = html;
    else if (wrap) wrap.insertAdjacentHTML('beforebegin', html);
    else {
      const inner = el.querySelector('.ai-msg-inner');
      if (inner) inner.parentElement.insertAdjacentHTML('beforebegin', html);
      else el.insertAdjacentHTML('afterbegin', html);
    }
    const head = el.querySelector('[data-plan-toggle]:last-of-type') || el.querySelector('[data-plan-toggle]');
    if (head && !head._planToggleBound) {
      head._planToggleBound = true;
      head.addEventListener('click', () => head.closest('.plan-card')?.classList.toggle('collapsed'));
    }
    refreshIcons();
  },

  /** 历史消息静态计划卡
   * @param {Object} plan - {steps:[{title,tool,status}]}
   * @returns {string} HTML */
  renderStatic(plan) {
    if (!plan || !Array.isArray(plan.steps) || !plan.steps.length) return '';
    const fake = { current: { steps: plan.steps } };
    const save = this.current;
    this.current = fake.current;
    let html = '';
    try {
      // 复用 render 的行生成（无气泡时返回字符串）
      const stMap = { todo: '待办', running: '进行中', done: '完成', failed: '失败', skipped: '已跳过' };
      const rows = plan.steps.map((s, i) => {
        const color = { todo: 'text-text-tertiary', running: 'text-brand-cyan', done: 'text-success', failed: 'text-danger', skipped: 'text-warning' }[s.status] || 'text-text-tertiary';
        return '<div class="flex items-center gap-2 py-0.5">' +
          '<span class="font-mono text-[10px] text-text-tertiary w-4">' + (i + 1) + '.</span>' +
          '<span class="text-[11px] ' + color + '">' + (stMap[s.status] || s.status || '待办') + '</span>' +
          '<span class="text-[11px] text-text-primary truncate flex-1">' + escapeHtml(s.title || '') + '</span>' +
          (s.tool ? '<span class="font-mono text-[9px] text-text-tertiary shrink-0">' + escapeHtml(s.tool) + '</span>' : '') +
          '</div>';
      }).join('');
      const doneN = plan.steps.filter(s => s.status === 'done').length;
      html = '<div class="plan-card rounded-lg bg-surface-panel/60 border border-brand-violet/25 px-3 py-2 my-2" data-plan-card>' +
        '<div class="flex items-center gap-1.5 mb-1"><i data-lucide="list-checks" class="w-3.5 h-3.5 text-brand-violet"></i>' +
        '<span class="text-[11px] font-medium text-text-primary">任务计划</span>' +
        '<span class="text-[10px] text-text-tertiary ml-auto">' + doneN + '/' + plan.steps.length + ' 完成</span></div>' + rows + '</div>';
    } finally {
      this.current = save;
    }
    return html;
  }
};

/* ============================================================
   [阶段0.5 D] 工作区文件树面板（只读）
   - 数据全部来自现有只读接口：list_directory（展开）/ read_file（文本预览），
     不新增任何写能力；Agent write_file 成功后经 moray:ws-file-written 事件刷新+高亮 2s
   - 入口：会话区顶部"本机工具已开启"轻提示条内的「文件树」按钮（仅在线时显示）
   ============================================================ */

let __wsTree = null; // {dirCache:{path:items}, expanded:Set, highlight:path}

/** 只读工具直调（list_directory/read_file 无需审批，与工具执行同一后端端点）
 * @param {string} name - 工具名
 * @param {Object} args - 参数
 * @returns {Promise<Object>} data */
async function wsFetchTool(name, args) {
  const res = await fetch(agentBackendBase() + '/api/agent/tool', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, args: args || {} })
  });
  const j = await res.json().catch(() => null);
  if (!res.ok || !j || j.ok !== true) throw new Error((j && j.message) || ('本机后端返回 ' + res.status));
  return j.data;
}

/** [阶段1 M4] 工作区文件树：改为可收起的常驻侧栏（fixed 浮层，打开期间仍可正常聊天，
 * 解决阶段0.5 模态弹窗与聊天互斥的问题）；渲染逻辑与容器查询按 class 共享。 */
function openWorkspaceTree() {
  toggleWsSidebar(true);
}

function ensureWsSidebar() {
  let bar = document.getElementById('wsSidebar');
  if (bar) return bar;
  bar = document.createElement('div');
  bar.id = 'wsSidebar';
  bar.innerHTML = '<div class="ws-resize-handle" title="拖拽调整宽度"></div>' +
    '<div class="flex items-center gap-1.5 mb-1.5">' +
    '<i data-lucide="folder-tree" class="w-3.5 h-3.5 text-brand-violet"></i>' +
    '<span class="text-[11px] font-medium text-text-primary">工作区</span>' +
    '<span class="text-[9px] text-text-tertiary">只读</span>' +
    '<button data-ws-sidebar-refresh class="ml-auto text-text-tertiary hover:text-brand-cyan" title="刷新" aria-label="刷新文件树"><i data-lucide="refresh-cw" class="w-3 h-3"></i></button>' +
    '<button data-ws-sidebar-collapse class="text-text-tertiary hover:text-brand-cyan" title="收起侧栏（可从顶部提示条再打开）" aria-label="收起侧栏"><i data-lucide="chevrons-right" class="w-3.5 h-3.5"></i></button></div>' +
    '<div class="ws-tree-list rounded-lg border border-line-ghost/50 p-1.5 flex-1 overflow-auto text-[11px]"></div>' +
    '<div class="ws-tree-preview hidden rounded-lg border border-line-ghost/50 p-2 bg-surface-panel/40 mt-1.5 max-h-48 overflow-auto text-[10px]"></div>';
  (document.getElementById('page-chat') || document.body).appendChild(bar);
  bar.querySelector('[data-ws-sidebar-refresh]').addEventListener('click', () => { if (__wsTree) __wsTree.dirCache = {}; wsTreeRender(); });
  bar.querySelector('[data-ws-sidebar-collapse]').addEventListener('click', () => toggleWsSidebar(false));
  // [阶段1.5 F] 左缘拖拽调宽（220–460px）
  const wsHandle = bar.querySelector('.ws-resize-handle');
  if (wsHandle) {
    wsHandle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = bar.getBoundingClientRect().width;
      const move = (ev) => {
        const w = Math.min(460, Math.max(220, startW + (startX - ev.clientX)));
        bar.style.width = w + 'px';
      };
      const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }
  if (!document.getElementById('wsSidebarStyle')) {
    const st = document.createElement('style');
    st.id = 'wsSidebarStyle';
    st.textContent = '#wsSidebar{position:fixed;top:74px;right:14px;bottom:158px;width:292px;z-index:26;display:flex;flex-direction:column;padding:10px;border-radius:12px;background:rgba(15,18,28,.94);border:1px solid rgba(120,140,180,.25);box-shadow:0 8px 28px rgba(0,0,0,.4)}' +
      '#wsSidebar.collapsed{display:none}' +
      '[data-theme="light"] #wsSidebar{background:rgba(247,249,252,.96);border-color:rgba(90,110,150,.25)}';
    document.head.appendChild(st);
  }
  return bar;
}

/** 打开/收起侧栏（force 缺省=切换）
 * @param {boolean} [force] - true=打开 false=收起
 * @returns {void} */
function toggleWsSidebar(force) {
  const bar = ensureWsSidebar();
  const wasOpen = !bar.classList.contains('collapsed');
  const show = (force != null) ? force : !wasOpen;
  bar.classList.toggle('collapsed', !show);
  if (show) {
    closeModal(); // 若 modal 开着（如审批外的弹窗）先收起，避免遮挡
    if (!__wsTree) __wsTree = { dirCache: {}, expanded: new Set(), highlight: null };
    wsTreeRender();
    refreshIcons();
  }
}

/** 渲染整棵树（按 expanded 懒加载展开；highlight 高亮 2s）
 * @returns {Promise<void>} */
async function wsTreeRender() {
  const box = document.querySelector('.ws-tree-list');
  if (!box) return;
  box.innerHTML = '<div class="text-[10px] text-text-tertiary py-3 text-center">加载中…</div>';
  try {
    const html = await wsTreeRenderDir('.', 0);
    box.innerHTML = html || '<div class="ws-empty"><i data-lucide="folder-open"></i><div class="empty-title">工作区是空的</div><div class="empty-hint">让 Agent 帮你新建第一个文件，例如“新建 hello.txt 写入你好”</div></div>';
  } catch (e) {
    box.innerHTML = '<div class="text-[10px] text-danger py-2">加载失败：' + escapeHtml(String((e && e.message) || e)) + '</div>';
  }
  refreshIcons();
}

/** [阶段1.5 F] 按文件扩展名取小图标（文件类型可视化）
 * @param {string} name - 文件名
 * @returns {string} lucide 图标名 */
function wsFileTypeIcon(name) {
  const n = String(name || '').toLowerCase();
  if (/\.(md|txt|log)$/.test(n)) return 'file-text';
  if (/\.(js|ts|mjs|cjs|jsx|tsx)$/.test(n)) return 'file-code';
  if (/\.(py)$/.test(n)) return 'file-code';
  if (/\.(json|ya?ml|toml|ini|cfg|conf)$/.test(n)) return 'file-cog';
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/.test(n)) return 'file-image';
  if (/\.(zip|7z|rar|tar|gz)$/.test(n)) return 'file-archive';
  if (/\.(exe|dll|bat|cmd|ps1|msi)$/.test(n)) return 'file-warning';
  return 'file';
}

/** 递归渲染目录（dirCache 缓存目录列表，展开/折叠不重复请求）
 * @param {string} dirPath - 目录相对路径
 * @param {number} depth - 层级
 * @returns {Promise<string>} HTML */
async function wsTreeRenderDir(dirPath, depth) {
  if (!__wsTree) return '';
  if (!__wsTree.dirCache[dirPath]) {
    const data = await wsFetchTool('list_directory', { path: dirPath });
    __wsTree.dirCache[dirPath] = (data && data.items) || [];
  }
  const items = __wsTree.dirCache[dirPath];
  let html = '';
  for (const it of items) {
    const rel = (dirPath === '.' || dirPath === '') ? it.name : (dirPath + '/' + it.name);
    const isDir = it.type === 'dir';
    const expanded = isDir && __wsTree.expanded.has(rel);
    const hl = __wsTree.highlight === rel ? ' ring-1 ring-brand-cyan bg-brand-cyan/10' : '';
    const typeIcon = isDir ? (expanded ? 'folder-open' : 'folder') : wsFileTypeIcon(it.name);
    const sizeText = isDir ? '' : (' · ' + (it.size >= 1024 ? (it.size / 1024).toFixed(1) + 'KB' : it.size + 'B'));
    html += '<div class="ws-tree-row flex items-center gap-1.5 py-1 rounded px-1 hover:bg-surface-panel cursor-pointer' + hl + '" style="padding-left:' + (depth * 14 + 4) + 'px" data-ws-path="' + escapeHtml(rel) + '" data-ws-type="' + it.type + '" title="' + escapeHtml(rel) + '">' +
      (isDir ? '<i data-lucide="chevron-right" class="ws-caret w-2.5 h-2.5 text-text-tertiary' + (expanded ? ' open' : '') + '"></i>' : '<span class="w-2.5"></span>') +
      '<i data-lucide="' + typeIcon + '" class="w-3.5 h-3.5 ' + (isDir ? 'text-brand-cobalt' : 'text-text-tertiary') + '"></i>' +
      '<span class="text-text-primary truncate">' + escapeHtml(it.name) + '</span>' +
      '<span class="text-[9px] text-text-tertiary shrink-0">' + sizeText + '</span></div>';
    if (isDir && expanded) html += await wsTreeRenderDir(rel, depth + 1);
  }
  return html;
}

/** 树行点击：目录展开/折叠（懒加载），文件只读预览
 * @param {string} path - 相对路径
 * @param {string} type - dir|file
 * @returns {Promise<void>} */
async function wsTreeToggle(path, type) {
  if (!__wsTree) return;
  if (type === 'dir') {
    if (__wsTree.expanded.has(path)) __wsTree.expanded.delete(path);
    else __wsTree.expanded.add(path);
    await wsTreeRender();
  } else {
    await wsTreePreviewFile(path);
  }
}

/** 文本文件只读预览（read_file，前 64KB；二进制/越界等错误原样显示）
 * @param {string} path - 相对路径
 * @returns {Promise<void>} */
async function wsTreePreviewFile(path) {
  const prev = document.querySelector('.ws-tree-preview');
  if (!prev) return;
  const head = (note, cls) => '<div class="flex items-center justify-between mb-1">' +
    '<span class="text-[10px] text-text-tertiary font-mono break-all">' + escapeHtml(path) + (note ? ' · ' + note : '') + '</span>' +
    '<button class="text-[10px] text-text-tertiary hover:text-brand-cyan shrink-0" data-ws-prev-close>关闭</button></div>';
  prev.classList.remove('hidden');
  prev.innerHTML = head('只读预览（前 64KB）') + '<div class="text-[10px] text-text-tertiary">加载中…</div>';
  let closeBtn = prev.querySelector('[data-ws-prev-close]');
  if (closeBtn) closeBtn.onclick = () => prev.classList.add('hidden');
  try {
    const data = await wsFetchTool('read_file', { path, maxBytes: 65536 });
    const body = data && data.content != null ? data.content : '';
    const note = data && data.truncated ? '内容已截断' : '';
    prev.innerHTML = head(note, '') +
      '<pre class="text-[10px] font-mono text-text-primary whitespace-pre-wrap break-all max-h-56 overflow-auto">' + escapeHtml(body) + '</pre>';
  } catch (e) {
    prev.innerHTML = head('预览失败') + '<div class="text-[10px] text-danger">' + escapeHtml(String((e && e.message) || e)) + '</div>';
  }
  closeBtn = prev.querySelector('[data-ws-prev-close]');
  if (closeBtn) closeBtn.onclick = () => prev.classList.add('hidden');
}

/** 文件树行点击/入口按钮委托 + Agent 写入事件（只装一次）
 * @returns {void} */
function installWsTreeHandlers() {
  if (window.__wsTreeBound) return;
  window.__wsTreeBound = true;
  document.addEventListener('click', async (e) => {
    // [批次修复 #1] 本机 Agent 入口离线守卫：点击时给出明确原因提示（不执行动作）
    const agentBtn = e.target.closest('[data-ws-tree-btn], [data-agent-selfcheck-btn], [data-ws-sample-btn]');
    if (agentBtn) {
      const reason = (typeof agentOfflineReason === 'function') ? agentOfflineReason() : '';
      if (reason) {
        e.preventDefault();
        showNotification('本机 Agent 暂不可用', reason, 'warning', 5200);
        return;
      }
    }
    const entry = e.target.closest('[data-ws-tree-btn]');
    if (entry) { e.preventDefault(); toggleWsSidebar(); return; }
    // [阶段1 M1] Agent 自检入口
    const sc = e.target.closest('[data-agent-selfcheck-btn]');
    if (sc) { e.preventDefault(); runAgentSelfCheck(); return; }
    // [阶段1.6 M3] 示例工作区载入
    const sample = e.target.closest('[data-ws-sample-btn]');
    if (sample) {
      e.preventDefault();
      loadSampleWorkspace().catch(err => showNotification('示例载入失败', String((err && err.message) || err).slice(0, 140), 'error', 3000));
      return;
    }
    const row = e.target.closest('.ws-tree-row[data-ws-path]');
    if (row && document.querySelector('.ws-tree-list')) {
      await wsTreeToggle(row.getAttribute('data-ws-path'), row.getAttribute('data-ws-type'));
    }
  });
  // Agent write_file 成功 → 面板开着时刷新并高亮新文件 2s
  window.addEventListener('moray:ws-file-written', async (ev) => {
    if (!__wsTree || !document.querySelector('.ws-tree-list')) return;
    const p = ev.detail && ev.detail.path;
    if (!p) { __wsTree.dirCache = {}; await wsTreeRender(); return; }
    const parts = String(p).split('/');
    for (let i = 1; i < parts.length; i++) __wsTree.expanded.add(parts.slice(0, i).join('/'));
    __wsTree.dirCache = {};
    __wsTree.highlight = p;
    await wsTreeRender();
    setTimeout(() => {
      if (__wsTree && __wsTree.highlight === p) {
        __wsTree.highlight = null;
        wsTreeRender();
      }
    }, 2000);
  });
}
installWsTreeHandlers();

/** [阶段1 M4] 行级 diff → unified 风格 HTML（- 原内容红 / + 新内容绿 / 上下文灰）。
 * 简易 LCS（限制行数防 O(n²) 爆炸，超限截取头部）；零外部依赖。
 * @param {string} oldText - 原文本
 * @param {string} newText - 新文本
 * @param {number} [maxLines] - 渲染行数上限（默认 400）
 * @returns {string} HTML */
function buildApprovalDiff(oldText, newText, maxLines) {
  maxLines = maxLines || 400;
  const a = String(oldText == null ? '' : oldText).split('\n');
  const b = String(newText == null ? '' : newText).split('\n');
  if (a.length > maxLines) a.length = maxLines;
  if (b.length > maxLines) b.length = maxLines;
  const n = a.length, m = b.length;
  // LCS DP（n*m ≤ 160k 可接受）
  const dp = [];
  for (let i = 0; i <= n; i++) dp.push(new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = (a[i] === b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = []; // {t:'-'|'+'|' ', line}
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ t: ' ', line: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: '-', line: a[i] }); i++; }
    else { ops.push({ t: '+', line: b[j] }); j++; }
  }
  while (i < n) { ops.push({ t: '-', line: a[i] }); i++; }
  while (j < m) { ops.push({ t: '+', line: b[j] }); j++; }
  // 压缩纯上下文区：只保留变更行前后 2 行
  const keep = new Array(ops.length).fill(false);
  ops.forEach((o, k) => { if (o.t !== ' ') { for (let x = Math.max(0, k - 2); x <= Math.min(ops.length - 1, k + 2); x++) keep[x] = true; } });
  let html = '';
  let skip = 0;
  ops.forEach((o, k) => {
    if (!keep[k]) { skip++; if (skip === 1) html += '<div class="text-[9px] text-text-tertiary px-1">⋯</div>'; return; }
    skip = 0;
    const cls = o.t === '-' ? 'text-danger bg-danger/10' : o.t === '+' ? 'text-success bg-success/10' : 'text-text-tertiary';
    html += '<div class="font-mono text-[10px] whitespace-pre-wrap break-all px-1 ' + cls + '">' + escapeHtml((o.t === ' ' ? '  ' : o.t) + o.line) + '</div>';
  });
  if (!html) html = '<div class="text-[10px] text-text-tertiary px-1">（内容无差异）</div>';
  return '<div class="rounded border border-line-ghost/50 bg-surface-panel/40 p-1.5 max-h-44 overflow-auto">' + html + '</div>';
}

/** [阶段1.6 M3] 载入示例工作区：写入 3 个中文自洽示例文件（notes/a.txt、notes/b.txt、todo.md）。
 * 只创建工作区内新文件——已存在的同名文件一律跳过（绝不覆盖用户文件）；按钮点击即用户授权。
 * @returns {Promise<void>} */
async function loadSampleWorkspace() {
  const samples = [
    { path: 'notes/a.txt', content: '项目启动会要点（素材A）\n- 目标：两周内完成 Agent 工作区演示\n- 分工：小王负责检索，小李负责汇总\n- 风险：模型偶发漏调工具，需自检兜底' },
    { path: 'notes/b.txt', content: '用户调研记录（素材B）\n- 用户希望一键载入示例数据\n- 汇总报告要保留原始出处\n- 编辑文件前先看清原文再替换' },
    { path: 'todo.md', content: '# 待办清单\n- [ ] 整理 notes/ 下的会议与调研要点\n- [ ] 把两个素材的结论写入 summary.md\n- [ ] 检查 Agent 时间线每一步的状态\n- [ ] 演示前运行一次模型自检' }
  ];
  let created = 0, skipped = 0;
  for (const s of samples) {
    try {
      const data = await wsFetchTool('read_file', { path: s.path, maxBytes: 64 });
      if (data && data.content != null) { skipped++; continue; } // 已存在 → 跳过
    } catch (e) { /* 不存在 → 创建 */ }
    const res = await fetch(agentBackendBase() + '/api/agent/tool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'write_file', args: { path: s.path, content: s.content, overwrite: false }, approved: true })
    });
    const j = await res.json().catch(() => null);
    if (j && j.ok) created++;
  }
  // 事件驱动文件树刷新（若开着）
  try { window.dispatchEvent(new CustomEvent('moray:ws-file-written', { detail: { path: 'notes/a.txt' } })); } catch (e) { /* 忽略 */ }
  showNotification('示例工作区已就绪',
    '新建 ' + created + ' 个文件' + (skipped ? '，跳过已存在 ' + skipped + ' 个' : '') + '。试试：“列出计划，找到所有 txt 并汇总要点到 summary.md”',
    'success', 6000);
}

/** [批次修复 #2] 工具调用最稳模型推荐（真机实测：qwen2.5:7b 非流式/流式均 3/3）
 * @returns {string} 本机可用的推荐模型名（无则空串） */
function agentRecommendedModel() {
  if (typeof AI === 'undefined' || !Array.isArray(AI.models)) return '';
  const wish = ['qwen2.5:7b', 'qwen3.5:9b', 'qwen3.5:4b'];
  for (const w of wish) {
    const hit = (AI.models || []).find(m => m.name === w || String(m.name || '').indexOf(w) === 0);
    if (hit) return hit.name;
  }
  return '';
}

/** [批次修复 #2] Agent 模式默认模型：当前模型自检通过则维持原默认；
 * 否则（未自检/未通过）切到推荐模型——不强制锁定，用户仍可手动切换。
 * @returns {void} */
function agentApplyRecommendedModel() {
  try {
    const sc = MoraySettings.get('agentSelfCheck');
    const cur = (typeof activeModelName === 'function') ? activeModelName() : MoraySettings.get('defaultModel');
    if (sc && sc.ok === true && sc.model === cur) return; // 自检通过 → 维持原默认
    const rec = agentRecommendedModel();
    if (!rec || cur === rec) return;
    MoraySettings.set('defaultModel', rec).then(() => {
      try {
        if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
        if (typeof updateStatusBar === 'function') updateStatusBar();
        showNotification('Agent 默认模型已切换', rec + '（真机实测工具调用最稳 3/3）；仍可在输入台手动切换', 'info', 4500);
      } catch (e) { /* 忽略 */ }
    }).catch(() => {});
  } catch (e) { /* 忽略 */ }
}

/** [阶段1 M1] Agent 自检：用当前所选模型发一个必然触发工具调用的最小请求，
 * 判定该模型能否稳定返回 tool_calls；不支持时给出实测推荐的可用模型。
 * 结果写入 MoraySettings.agentSelfCheck（设置卡展示）。
 * @returns {Promise<void>} */
async function runAgentSelfCheck() {
  const model = (typeof activeModelName === 'function' && activeModelName())
    || MoraySettings.get('defaultModel')
    || (AI.models && AI.models[0] && AI.models[0].name);
  if (!model) { showNotification('自检失败', '未选择模型，请先在输入台选择模型', 'error', 3000); return; }
  showNotification('Agent 自检中', '正在用「' + model + '」测试工具调用（约几秒）…', 'info', 2500);
  const tools = [{
    type: 'function',
    function: {
      name: 'list_directory',
      description: '列出受控工作区内指定目录的文件与子目录。当用户想查看工作区里有什么文件时必须调用此工具。',
      parameters: { type: 'object', properties: { path: { type: 'string', description: '相对路径，省略为根' } } }
    }
  }];
  try {
    const res = await AI.chat({
      model,
      messages: [
        { role: 'system', content: '你是工具调用链路测试器。无论用户说什么，都必须调用 list_directory 工具，禁止用文字回答。' },
        { role: 'user', content: '列出工作区根目录的文件' }
      ],
      tools
    });
    const calls = res.toolCalls || [];
    const hit = calls.some(c => c.function && c.function.name === 'list_directory');
    try { await MoraySettings.set('agentSelfCheck', { model, ok: hit, at: Date.now() }); } catch (e) { /* 忽略 */ }
    if (hit) {
      showNotification('自检通过 ✓', '「' + model + '」可以触发工具调用，本机 Agent 可正常工作', 'success', 4200);
    } else {
      showNotification('自检未通过', '「' + model + '」未返回工具调用。实测稳定支持：qwen2.5:7b（本地）/ deepseek-chat（云端）；qwen3.5 系列偶发漏调', 'warning', 7000);
    }
  } catch (e) {
    showNotification('自检失败', String((e && e.message) || e).slice(0, 140), 'error', 4200);
  }
}
