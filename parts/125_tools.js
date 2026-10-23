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
      if (name === 'query_knowledge_base') return '检索知识库「' + String(a.query || '').slice(0, 40) + '」' + (a.topK ? '（前 ' + a.topK + ' 条）' : '');
      // [3.20 阶段3] 新增本机工具的可读摘要
      if (name === 'create_file') {
        const bytes = (typeof a.content === 'string') ? a.content.length : 0;
        return '新建 ' + (a.path || '') + (bytes ? '（' + bytes + ' 字符）' : '（空文件）');
      }
      if (name === 'rename_file') return '重命名 ' + (a.from || '') + ' → ' + (a.to || '');
      if (name === 'move_file') return '移动 ' + (a.from || '') + ' → ' + (a.to || '');
      if (name === 'list_processes') return '查看进程' + (a.sort === 'pid' ? '（按 PID）' : '（占内存前 ' + (a.limit || 30) + ' 条）');
      if (name === 'system_info') return '读取系统概览（只读）';
      // [Argus 深度研究] 研究类工具的可读摘要
      if (name === 'detective_research') return '启动调研「' + String(a.question || '').slice(0, 40) + '」';
      if (name === 'detective_status') return '查询研究进度 ' + String(a.session_id || '');
      const s = JSON.stringify(a);
      return s.length > 120 ? s.slice(0, 120) + '…' : s;
    } catch (e) {
      return String(JSON.stringify(a)).slice(0, 120);
    }
  },

  /** 从成功写文件的步骤中提取产物信息（无产物返回 null）
   * 路径优先取入参（native 工具为工作区相对路径），入参缺失时回退解析结果 JSON；
   * 仅用于展示层，不做任何文件系统访问（读取由 previewArtifact 走只读工具）。
   * @param {Object} evt - 步骤事件 {name,args,status,resultFull}
   * @returns {{path:string, bytes:number|null, action:string}|null} */
  artifactFromStep(evt) {
    if (!evt || evt.status !== 'success') return null;
    if (evt.name !== 'create_file' && evt.name !== 'write_file') return null;
    const a = evt.args || {};
    const raw = evt.resultFull != null ? String(evt.resultFull) : '';
    let data = null;
    if (raw.charAt(0) === '{') {
      try { data = JSON.parse(raw); } catch (e) { data = null; }
    }
    const path = String((a && a.path) || (data && data.path) || '').trim();
    if (!path) return null;
    let bytes = (data && data.bytes != null) ? Number(data.bytes) : null;
    if (bytes == null && typeof a.content === 'string') bytes = a.content.length;
    if (bytes != null && (!isFinite(bytes) || bytes < 0)) bytes = null;
    const action = (data && data.created) ? '新建'
      : ((data && data.written) ? '写入' : (evt.name === 'create_file' ? '新建' : '写入'));
    return { path, bytes, action };
  },

  /** 产物卡片 HTML（成功写文件时替代原始 JSON 结果；原始结果保留在折叠区）
   * @param {Object} art - artifactFromStep 的返回值
   * @returns {string} HTML */
  artifactCardHtml(art) {
    const path = String((art && art.path) || '');
    if (!path) return '';
    const size = art.bytes != null
      ? (art.bytes >= 1024 ? (art.bytes / 1024).toFixed(1) + ' KB' : art.bytes + ' B')
      : '';
    const meta = [art.action || '', size].filter(Boolean).join(' · ');
    return '<div class="artifact-card flex items-center gap-2 mt-2 rounded-lg border border-brand-cobalt/25 bg-brand-cobalt/15 px-3 py-2">' +
      '<i data-lucide="file-code" class="w-4 h-4 text-brand-cobalt shrink-0"></i>' +
      '<div class="min-w-0 flex-1">' +
      '<div class="text-[11px] text-text-primary font-medium truncate" title="' + escapeHtml(path) + '">' + escapeHtml(path) + '</div>' +
      (meta ? '<div class="text-[10px] text-text-tertiary">' + escapeHtml(meta) + '</div>' : '') +
      '</div>' +
      '<button type="button" data-preview-path="' + escapeHtml(path) + '" class="shrink-0 px-2.5 py-1 rounded-md text-[10px] font-medium text-brand-cobalt bg-brand-cobalt/15 border border-brand-cobalt/25 hover:opacity-90">预览代码</button>' +
      '</div>';
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
      // [Laya v3] ZCode 执行引擎/子智能体
      zcode_run: 'play', zcode_sub_agent: 'bot',
      // [阶段1 M2] 高价值三工具
      find_files: 'file-search', search_text: 'text-search', edit_file: 'square-pen',
      // [3.20 阶段3] 新增本机工具
      create_file: 'file-plus-2', rename_file: 'file-pen', move_file: 'folder-input',
      list_processes: 'activity', system_info: 'cpu',
      // [Argus 深度研究] 研究工具图标
      detective_research: 'telescope', detective_status: 'radar'
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
    const artifact = this.artifactFromStep(evt);
    // [3.20 修复] 这里必须是 let：下面 4 个分支都要对 body 重新赋值，
    // 原来是 const → 每次步骤卡渲染都抛 "Assignment to constant variable"，
    // 进而让 emit() 抛出、Gateway.runWithTools 中断并回退普通流式（工具轮直接失效）
    let body = '';
    if (evt.status === 'error') {
      body = '<div class="text-[10px] text-danger mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono" data-tool-err>' + escapeHtml(full || '未知错误') + '</div>';
    } else if (evt.status === 'denied') {
      body = '<div class="text-[10px] text-warning mt-2">' + escapeHtml(full || '用户拒绝了该操作') + '</div>';
    } else if (artifact) {
      // 成功写文件：默认展示产物卡片，原始 JSON 收进折叠区（排查用，不再占屏）
      body = this.artifactCardHtml(artifact) +
        (full ? '<details class="mt-2"><summary class="text-[10px] text-text-tertiary cursor-pointer select-none">查看原始结果</summary>' +
          '<div class="text-[10px] text-text-tertiary mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono" data-tool-full>' + escapeHtml(full) + '</div></details>' : '');
    } else if (full) {
      body = '<div class="text-[10px] text-text-tertiary mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all" data-tool-full>' + escapeHtml(full) + '</div>';
    }
    // [批次修复 #7] 重放标记：该步被“重试该步”重放过（仅重新执行工具，不自动改写已生成回复）
    if (evt.replayed) {
      const changedNote = evt.replayed.changed ? '结果与上次不同（已更新）' : '结果与上次一致';
      body = '<div class="flex items-center gap-2 mt-2 text-[10px] text-warning"><i data-lucide="rotate-cw" class="w-3 h-3"></i>' +
        '已重放该步（仅重新执行工具，不自动改写已生成回复）· ' + escapeHtml(changedNote) +
        ' · 可点回复右上「重新生成」让模型基于最新结果作答</div>' + body;
    }
    const retry = evt.status === 'error' && evt.name
      ? '<button data-tool-retry="' + evt.index + '" class="ml-auto shrink-0 rounded-md px-2 py-1 text-text-tertiary hover:text-warning hover:bg-surface-panel flex items-center gap-1 text-[10px]" title="重新执行本步骤（走完整安全层与审批）"><i data-lucide="rotate-cw" class="w-3 h-3"></i>重试该步</button>'
      : '';
    const copyBtn = full && evt.status !== 'error'
      ? '<button class="tool-step-copy ml-auto shrink-0 rounded-md px-2 py-1 text-text-tertiary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" data-tool-copy title="复制完整结果" data-full="' + escapeHtml(full) + '"><i data-lucide="copy" class="w-3 h-3"></i>复制</button>'
      : '';
    const actions = (copyBtn || retry)
      ? '<span class="ml-auto flex items-center gap-1">' + copyBtn + retry + '</span>'
      : '';
    const open = (evt.status === 'running' || evt.status === 'pending' || artifact) ? ' open' : '';
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
      '<div class="tool-steps space-y-2" style="display:none">' + stepsHtml + '</div></div>';
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
        '<div class="tool-steps space-y-2"></div>';
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
      // 产物卡片 / Laya 摘要：点击路径 → 应用内只读预览。
      // 用 composedPath 而非 e.target.closest：refreshIcons 会把 <i> 换成 <svg>，
      // 被点节点可能已游离，closest 会返回 null（本仓库既有踩坑）。
      const evPath = (typeof e.composedPath === 'function') ? e.composedPath() : [];
      for (let i = 0; i < evPath.length; i++) {
        const n = evPath[i];
        if (n && n.nodeType === 1 && n.hasAttribute && n.hasAttribute('data-preview-path')) {
          e.preventDefault();
          e.stopPropagation();
          const p = n.getAttribute('data-preview-path');
          if (p) previewArtifact(p);
          return;
        }
      }
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

/** 文件扩展名 → highlight.js 语言标识（未知返回空串）
 * @param {string} name - 文件名或路径
 * @returns {string} 语言标识 */
function artifactLangForFile(name) {
  const ext = (String(name || '').split('.').pop() || '').toLowerCase();
  const langMap = {
    py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
    json: 'json', html: 'html', htm: 'html', css: 'css', md: 'markdown', markdown: 'markdown', svg: 'xml',
    yaml: 'yaml', yml: 'yaml', sh: 'bash', bat: 'batch', ps1: 'powershell',
    txt: 'text', c: 'c', h: 'c', cpp: 'cpp', java: 'java', go: 'go', rs: 'rust', sql: 'sql'
  };
  return langMap[ext] || '';
}

/** 文件扩展名（小写，不含点） */
function artifactExt(name) {
  const s = String(name || '');
  const i = s.lastIndexOf('.');
  return i >= 0 ? s.slice(i + 1).toLowerCase() : '';
}

/** 预览分流用的后缀集合（raster 走图片预览；其余图片后缀提示不支持；二进制不尝试文本解码） */
const ARTIFACT_RASTER_EXTS = { png: 1, jpg: 1, jpeg: 1, gif: 1, webp: 1 };
const ARTIFACT_IMAGE_EXTS = { png: 1, jpg: 1, jpeg: 1, gif: 1, webp: 1, bmp: 1, ico: 1, avif: 1 };
const ARTIFACT_BINARY_EXTS = {
  zip: 1, gz: 1, tar: 1, '7z': 1, rar: 1, exe: 1, dll: 1, bin: 1, pyc: 1, class: 1, jar: 1,
  pdf: 1, doc: 1, docx: 1, xls: 1, xlsx: 1, ppt: 1, pptx: 1, db: 1, sqlite: 1, sqlite3: 1,
  woff: 1, woff2: 1, ttf: 1, otf: 1, mp3: 1, mp4: 1, mov: 1, avi: 1, iso: 1
};

/** 按扩展名/Content-Type 决定预览类型
 * @param {string} name - 文件名或路径
 * @param {string} [contentType] - 响应 Content-Type
 * @returns {'html'|'markdown'|'svg'|'image'|'image-unsupported'|'binary'|'text'} */
function artifactKindForName(name, contentType) {
  const ext = artifactExt(name);
  if (ext === 'html' || ext === 'htm') return 'html';
  if (ext === 'md' || ext === 'markdown') return 'markdown';
  if (ext === 'svg') return 'svg';
  if (ARTIFACT_RASTER_EXTS[ext]) return 'image';
  if (ARTIFACT_IMAGE_EXTS[ext]) return 'image-unsupported';
  if (ARTIFACT_BINARY_EXTS[ext]) return 'binary';
  if (/^image\//i.test(String(contentType || ''))) return 'image';
  return 'text';
}

/** 复用一个 object URL（换图/换预览时 revoke 旧的，防泄漏） */
let __artifactImageUrl = null;
function artifactObjectUrl(blob) {
  if (__artifactImageUrl) {
    try { URL.revokeObjectURL(__artifactImageUrl); } catch (e) { /* 忽略 */ }
    __artifactImageUrl = null;
  }
  __artifactImageUrl = URL.createObjectURL(blob);
  return __artifactImageUrl;
}

/** Tab 按钮 HTML（激活态由 mountArtifactTabs 统一设置） */
function artifactTabButtonHtml(id, label) {
  return '<button type="button" data-artifact-tab="' + escapeHtml(id) + '" ' +
    'style="padding:3px 10px;border-radius:6px;font-size:11px;cursor:pointer;border:1px solid transparent;' +
    'background:transparent;color:var(--color-text-secondary,#a3a3a3)">' + escapeHtml(label) + '</button>';
}

/** 挂载【预览】/【代码】切换（每个模态框独立绑定，无全局状态） */
function mountArtifactTabs(body, defaultTab) {
  if (!body) return;
  const tabs = Array.from(body.querySelectorAll('[data-artifact-tab]'));
  const panels = Array.from(body.querySelectorAll('[data-artifact-panel]'));
  const activate = (id) => {
    tabs.forEach((t) => {
      const on = t.getAttribute('data-artifact-tab') === id;
      t.style.background = on ? 'var(--color-brand-cobalt,#2563EB)' : 'transparent';
      t.style.color = on ? '#fff' : 'var(--color-text-secondary,#a3a3a3)';
      t.style.borderColor = on ? 'transparent' : 'var(--color-line-ghost,rgba(127,127,127,.28))';
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panels.forEach((p) => {
      p.style.display = p.getAttribute('data-artifact-panel') === id ? '' : 'none';
    });
  };
  tabs.forEach((t) => t.addEventListener('click', () => activate(t.getAttribute('data-artifact-tab'))));
  if (tabs.length) activate(defaultTab || tabs[0].getAttribute('data-artifact-tab'));
}

/** 统一渲染产物预览体：元信息 + Tab + 预览/代码面板
 * @param {HTMLElement} body - 模态框 .modal-body
 * @param {Object} opts - {name, meta, kind, content, imageUrl, lang, code, defaultTab, previewLabel, messageHtml}
 * @returns {void} */
function renderArtifactResult(body, opts) {
  if (!body) return;
  const o = opts || {};
  const kind = String(o.kind || 'text');
  const content = (o.content != null ? String(o.content) : '').replace(/^\uFEFF/, '');
  if (__artifactImageUrl && kind !== 'image' && kind !== 'svg') {
    try { URL.revokeObjectURL(__artifactImageUrl); } catch (e) { /* 忽略 */ }
    __artifactImageUrl = null;
  }
  const name = String(o.name || '');
  const meta = String(o.meta || '');
  const canPreview = kind === 'html' || kind === 'markdown' || kind === 'svg' ||
    kind === 'image' || kind === 'image-unsupported' || kind === 'binary' || kind === 'message';
  const canCode = o.code !== false && (kind === 'html' || kind === 'markdown' || kind === 'svg' || kind === 'text');
  const defaultTab = o.defaultTab || (canPreview ? 'preview' : 'code');
  const metaHtml = '<div class="flex items-center gap-2 mb-2 text-[10px] text-text-tertiary">' +
    '<i data-lucide="file-code" class="w-3.5 h-3.5 text-brand-cobalt"></i>' +
    '<span class="font-mono break-all">' + escapeHtml(name) + '</span>' +
    (meta ? '<span class="ml-auto shrink-0">' + escapeHtml(meta) + '</span>' : '') +
    '</div>';
  const tabHtml = (canPreview && canCode)
    ? '<div class="flex items-center gap-2 mb-2" style="border-bottom:1px solid var(--color-line-ghost,rgba(127,127,127,.25));padding:2px 0 6px">' +
      artifactTabButtonHtml('preview', o.previewLabel || '预览') +
      artifactTabButtonHtml('code', '代码') +
      '</div>'
    : '';
  let previewHtml = '';
  if (kind === 'html') {
    previewHtml = '<div data-artifact-frame-host></div>' +
      '<div class="text-[10px] text-text-tertiary mt-1">HTML 在沙箱 iframe 中运行（sandbox=allow-scripts allow-modals，无同源权限）</div>';
  } else if (kind === 'markdown') {
    previewHtml = '<div class="md-body" style="max-height:60vh;overflow:auto;padding:4px 2px">' +
      (typeof renderMarkdown === 'function' ? renderMarkdown(content) : escapeHtml(content).replace(/\n/g, '<br>')) +
      '</div>';
  } else if (kind === 'svg' || kind === 'image') {
    const src = String(o.imageUrl || '');
    previewHtml = src
      ? '<div style="text-align:center;background:var(--color-bg-app,#0b0e14);border-radius:8px;padding:8px">' +
        '<img src="' + escapeHtml(src) + '" alt="' + escapeHtml(name) + '" style="display:block;margin:0 auto;max-width:100%;max-height:60vh;border-radius:6px">' +
        '</div>'
      : '<div class="text-[11px] text-text-tertiary">图片内容不可用</div>';
  } else if (kind === 'image-unsupported') {
    previewHtml = '<div class="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] text-warning">' +
      '<div class="font-medium mb-1">该图片暂不支持预览</div>' +
      '<div class="text-[10px] text-text-tertiary">本机工作区的 PNG/JPG/GIF/WebP 需要后端二进制流接口；SVG 与 ZCode 沙箱图片可直接预览。</div>' +
      '<div class="mt-2 text-[10px] text-text-tertiary font-mono break-all">' + escapeHtml(name) + '</div>' +
      '</div>';
  } else if (kind === 'binary') {
    previewHtml = '<div class="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-[11px] text-warning">' +
      '<div class="font-medium mb-1">该文件是二进制格式，暂不支持预览</div>' +
      '<div class="mt-2 text-[10px] text-text-tertiary font-mono break-all">' + escapeHtml(name) + '</div>' +
      '</div>';
  } else if (kind === 'message') {
    previewHtml = String(o.messageHtml || '');
  }
  let codeHtml = '';
  if (canCode) {
    const lang = o.lang || artifactLangForFile(name);
    codeHtml = '<pre class="text-[12px] m-0" style="padding:12px 16px;max-height:56vh;overflow:auto">' +
      '<code' + (lang ? ' class="language-' + escapeHtml(lang) + '"' : '') + '>' + escapeHtml(content) + '</code></pre>';
  }
  body.innerHTML = metaHtml + tabHtml +
    (canPreview ? '<div data-artifact-panel="preview"' + (defaultTab === 'preview' ? '' : ' style="display:none"') + '>' + previewHtml + '</div>' : '') +
    (canCode ? '<div data-artifact-panel="code"' + (defaultTab === 'code' ? '' : ' style="display:none"') + '>' + codeHtml + '</div>' : '');
  if (typeof refreshIcons === 'function') refreshIcons();
  if (kind === 'html') {
    const host = body.querySelector('[data-artifact-frame-host]');
    if (host) {
      const frame = document.createElement('iframe');
      frame.setAttribute('sandbox', 'allow-scripts allow-modals');
      frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.title = name || 'HTML 预览';
      frame.style.cssText = 'width:100%;height:60vh;border:0;border-radius:8px;background:#fff;display:block';
      frame.srcdoc = content;
      host.appendChild(frame);
    }
  }
  if (canCode) {
    const codePanel = body.querySelector('[data-artifact-panel="code"]');
    if (codePanel && typeof enhanceCodeBlocks === 'function') enhanceCodeBlocks(codePanel, true);
  }
  if (kind === 'markdown') {
    const previewPanel = body.querySelector('[data-artifact-panel="preview"]');
    if (previewPanel && typeof enhanceCodeBlocks === 'function') enhanceCodeBlocks(previewPanel, false);
  }
  mountArtifactTabs(body, defaultTab);
  const codeTab = body.querySelector('[data-artifact-tab="code"]');
  if (codeTab) {
    codeTab.addEventListener('click', () => {
      const codeEl = body.querySelector('[data-artifact-panel="code"] pre > code');
      if (codeEl && window.hljs && !codeEl.dataset.highlighted) {
        try { window.hljs.highlightElement(codeEl); } catch (e) { /* 忽略 */ }
      }
    });
  }
  if (typeof refreshIcons === 'function') refreshIcons();
}

/** 应用内预览写文件产物（只读：复用本机 Agent 的 read_file 端点，不启动外部程序）
 * 绝对路径先尝试折算到本机 Agent 工作区内；跨工作区/越界/不可读时给出明确提示。
 * 由步骤卡 [data-preview-path] 与 Laya 摘要的可点击路径共用。
 * @param {string} rawPath - 卡片/摘要里的文件路径
 * @returns {Promise<void>} */
async function previewArtifact(rawPath) {
  const path = String(rawPath == null ? '' : rawPath).trim();
  if (!path) return;
  if (typeof showModal !== 'function') {
    if (typeof showNotification === 'function') showNotification('无法预览', path, 'warning', 2600);
    return;
  }
  // ZCode 沙箱产物：路径编码为 zcode:<task_id>:<encodeURIComponent(filename)>
  // （task_id 为后端签发的 32 位 uuid4.hex；文件名取 ZCode 事件里的 basename）
  const zcodeMatch = /^zcode:([0-9a-f]{32}):(.+)$/i.exec(path);
  if (zcodeMatch) {
    let zcodeName = zcodeMatch[2];
    try { zcodeName = decodeURIComponent(zcodeName); } catch (e) { /* 保留原编码串 */ }
    return previewZcodeArtifact(zcodeMatch[1], zcodeName);
  }
  const box = showModal(
    '<div class="flex items-center gap-2 text-[11px] text-text-tertiary">' +
    '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>' +
    '<span>正在读取</span><span class="font-mono break-all">' + escapeHtml(path) + '</span></div>',
    { title: '预览产物', icon: 'file-code', wide: true, footer: false }
  );
  const body = box ? box.querySelector('.modal-body') : null;
  const setBody = (html) => {
    if (!body) return;
    body.innerHTML = html;
    if (typeof refreshIcons === 'function') refreshIcons();
  };
  try {
    let rel = path.replace(/^file:\/\/\//i, '').replace(/\\/g, '/');
    const isAbs = /^[a-zA-Z]:\//.test(rel) || rel.charAt(0) === '/';
    if (isAbs) {
      const cfg = (typeof agentBackendConfig === 'function') ? await agentBackendConfig(true) : null;
      const ws = (cfg && cfg.workspace) ? String(cfg.workspace).replace(/\\/g, '/').replace(/\/+$/, '') : '';
      if (!ws) throw new Error('无法读取本机 Agent 工作区配置，不能预览绝对路径');
      const lowRel = rel.toLowerCase(), lowWs = ws.toLowerCase();
      if (lowRel === lowWs) throw new Error('目标路径是工作区目录，不是文件');
      if (lowRel.indexOf(lowWs + '/') !== 0) {
        throw new Error('该文件不在本机 Agent 工作区（' + ws + '）内，应用内预览暂不支持跨工作区路径');
      }
      rel = rel.slice(ws.length + 1);
    }
    const kind = artifactKindForName(rel, '');
    if (kind === 'image' || kind === 'image-unsupported') {
      renderArtifactResult(body, { name: rel, kind: 'image-unsupported', code: false });
      return;
    }
    if (kind === 'binary') {
      renderArtifactResult(body, { name: rel, kind: 'binary', code: false });
      return;
    }
    const data = await wsFetchTool('read_file', { path: rel, maxBytes: 262144 });
    const content = (data && data.content != null) ? String(data.content) : '';
    const size = (data && data.bytes != null) ? Number(data.bytes)
      : ((data && data.size != null) ? Number(data.size) : content.length);
    const sizeText = size >= 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';
    const imageUrl = kind === 'svg'
      ? artifactObjectUrl(new Blob([content], { type: 'image/svg+xml' }))
      : null;
    renderArtifactResult(body, {
      name: rel,
      meta: sizeText + (data && data.truncated ? ' · 已截断' : ''),
      kind,
      content,
      imageUrl,
      lang: artifactLangForFile(rel),
      defaultTab: (kind === 'html' || kind === 'markdown' || kind === 'svg') ? 'preview' : 'code'
    });
  } catch (e) {
    setBody(
      '<div class="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">' +
      '<div class="font-medium mb-1">无法预览该文件</div>' +
      '<div class="break-all">' + escapeHtml(String((e && e.message) || e)) + '</div>' +
      '<div class="mt-2 text-[10px] text-text-tertiary font-mono break-all">' + escapeHtml(path) + '</div>' +
      '</div>'
    );
  }
}

/** ZCode 沙箱产物预览：task_id + 顶层文件名 → GET /api/engines/zcode/files/<task_id>/<filename>
 * 与 125 本机文件预览共用同一个模态框与代码高亮；只读，不执行任何东西。
 * @param {string} taskId - ZCode 任务 id（32 位 hex）
 * @param {string} filename - 任务目录顶层文件名
 * @returns {Promise<void>} */
async function previewZcodeArtifact(taskId, filename) {
  const tid = String(taskId || '');
  const name = String(filename || '');
  if (!tid || !name) {
    if (typeof showNotification === 'function') showNotification('无法预览', '缺少 ZCode 任务或文件名', 'warning', 2600);
    return;
  }
  const box = showModal(
    '<div class="flex items-center gap-2 text-[11px] text-text-tertiary">' +
    '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>' +
    '<span>正在读取 ZCode 产物</span><span class="font-mono break-all">' + escapeHtml(name) + '</span></div>',
    { title: '预览 ZCode 产物', icon: 'file-code', wide: true, footer: false }
  );
  const body = box ? box.querySelector('.modal-body') : null;
  const setBody = (html) => {
    if (!body) return;
    body.innerHTML = html;
    if (typeof refreshIcons === 'function') refreshIcons();
  };
  try {
    const url = agentBackendBase() + '/api/engines/zcode/files/' +
      encodeURIComponent(tid) + '/' + encodeURIComponent(name);
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      let msg = 'HTTP ' + res.status;
      try { const j = await res.json(); msg = (j && (j.message || j.error)) || msg; } catch (e) { /* 保留 HTTP 码 */ }
      if (res.status === 413) msg = '文件过大，无法预览（上限 512KB）';
      throw new Error(msg);
    }
    const ct = String(res.headers.get('Content-Type') || '');
    const kind = artifactKindForName(name, ct);
    const lengthHeader = Number(res.headers.get('Content-Length'));
    if (kind === 'image') {
      const blob = await res.blob();
      const imageUrl = artifactObjectUrl(blob);
      const sizeText = blob.size >= 1024 ? (blob.size / 1024).toFixed(1) + ' KB' : blob.size + ' B';
      renderArtifactResult(body, {
        name: 'ZCode · ' + name,
        meta: sizeText + ' · 任务 ' + tid.slice(0, 8),
        kind: 'image', imageUrl, code: false
      });
      return;
    }
    if (kind === 'image-unsupported') {
      renderArtifactResult(body, {
        name: 'ZCode · ' + name,
        meta: '任务 ' + tid.slice(0, 8),
        kind: 'image-unsupported', code: false
      });
      return;
    }
    if (kind === 'binary') {
      renderArtifactResult(body, {
        name: 'ZCode · ' + name,
        meta: '任务 ' + tid.slice(0, 8),
        kind: 'binary', code: false
      });
      return;
    }
    const content = await res.text();
    const size = (isFinite(lengthHeader) && lengthHeader > 0) ? lengthHeader : content.length;
    const sizeText = size >= 1024 ? (size / 1024).toFixed(1) + ' KB' : size + ' B';
    const imageUrl = kind === 'svg'
      ? artifactObjectUrl(new Blob([content], { type: 'image/svg+xml' }))
      : null;
    renderArtifactResult(body, {
      name: 'ZCode · ' + name,
      meta: sizeText + ' · 任务 ' + tid.slice(0, 8),
      kind,
      content,
      imageUrl,
      lang: artifactLangForFile(name),
      defaultTab: (kind === 'html' || kind === 'markdown' || kind === 'svg') ? 'preview' : 'code'
    });
  } catch (e) {
    setBody(
      '<div class="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] text-danger">' +
      '<div class="font-medium mb-1">无法预览 ZCode 产物</div>' +
      '<div class="break-all">' + escapeHtml(String((e && e.message) || e)) + '</div>' +
      '<div class="mt-2 text-[10px] text-text-tertiary font-mono break-all">任务 ' + escapeHtml(tid.slice(0, 8)) + ' · ' + escapeHtml(name) + '</div>' +
      '</div>'
    );
  }
}
if (typeof window !== 'undefined') window.morayPreviewZcodeArtifact = previewZcodeArtifact;
if (typeof window !== 'undefined') window.morayPreviewArtifact = previewArtifact;

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

/** [3.20 阶段2] 知识库来源收集器：query_knowledge_base 每次检索把来源登记到这里，
 * 对话层在回答生成完成后取走（drain），写入消息的 citations 并在消息中渲染
 * 「[来源：文档名·第N段]」标注。工具与对话层因此解耦，不需要改工具返回协议。 */
const RagSources = {
  /** 本次请求累计的来源 @type {Array<{docId:string, docName:string, chunkIdx:number, meta:string, score:number}>} */
  items: [],
  /** 登记一批来源（自动去重：同一文档同一段只留最高分）
   * @param {Array} list - 来源数组
   * @returns {void} */
  add(list) {
    (list || []).forEach(it => {
      if (!it || !it.docId) return;
      const key = it.docId + '#' + it.chunkIdx;
      const old = this.items.find(x => x.docId + '#' + x.chunkIdx === key);
      if (old) { if ((it.score || 0) > (old.score || 0)) old.score = it.score; return; }
      this.items.push(it);
    });
  },
  /** 取走全部来源（按相关度降序） @returns {Array} 来源列表 */
  drain() {
    const out = this.items.slice().sort((a, b) => (b.score || 0) - (a.score || 0));
    this.items = [];
    return out;
  },
  /** 清空（每次新请求开始前调用，避免上一轮来源串到本轮） @returns {void} */
  clear() { this.items = []; }
};
window.RagSources = RagSources;

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
    name: 'query_knowledge_base', label: '查询知识库', timeoutMs: 30000,
    description: '在本地知识库（文档库）中检索相关片段，返回文档标题、片段内容摘要与相关度。'
      + '当用户询问"知识库里关于 X 的内容 / 文档里怎么写的"时使用。'
      + '若你的回答用到了返回的片段，请在相应句子后标注来源，格式为 [来源：文档名·第N段]（N 为返回的 segment 字段）。',
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
      const mode = (DocsApp._retrieval && DocsApp._retrieval.mode) || 'keyword';
      if (!results || !results.length) {
        return { query: q, mode, count: 0, items: [], note: '知识库中没有命中内容（可能尚未导入文档，或问法与文档内容差异较大）' };
      }
      // 按相关度降序取前 topK，并把来源登记到 RagSources（供消息来源标注使用）
      const top = results.slice(0, topK).map(r => ({
        title: (r.doc && r.doc.name) ? r.doc.name : '(未命名文档)',
        segment: (r.chunk && typeof r.chunk.idx === 'number') ? r.chunk.idx + 1 : 1,
        section: (r.chunk && r.chunk.meta) || '',
        score: Number((r.score || 0).toFixed(4)),
        snippet: String((r.chunk && r.chunk.text) || '').slice(0, 400)
      }));
      RagSources.add(top.map((it, i) => ({
        docId: (results[i].doc && results[i].doc.id) || '',
        docName: it.title,
        chunkIdx: it.segment - 1,
        meta: it.section,
        score: it.score
      })));
      return { query: q, mode, count: top.length, items: top };
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
  const container = document.querySelector('#settingsBody, #page-settings .max-w-2xl');
  if (!container || container.querySelector('#toolsSettingsCard')) return;
  const disabled = MoraySettings.get('toolsDisabled') || [];
  const nativeOn = !!MoraySettings.get('nativeToolsEnabled');
  const toolRows = Object.values(ToolRegistry.tools).map(t => {
    // [阶段0] native 工具开关由输入台"本机工具/Agent"统一控制，此处只读展示（不渲染误导性独立开关）
    if (t.native) {
      return '<div class="flex items-center justify-between py-2 border-b border-line-ghost/30 last:border-0">' +
        '<div class="flex-1 pr-3"><div class="text-xs text-text-primary">' + escapeHtml(t.label) +
        ' <span class="font-mono text-[10px] text-text-tertiary">' + t.name + '</span>' +
        '<span class="ml-1 text-[10px] px-2 py-1 rounded bg-brand-violet/10 text-brand-violet border border-brand-violet/20">本机工具</span>' +
        (t.sideEffect
          ? '<span class="ml-1 text-[10px] px-2 py-1 rounded bg-warning/10 text-warning border border-warning/25">需审批</span>'
          : '<span class="ml-1 text-[10px] px-2 py-1 rounded bg-success/10 text-success border border-success/20">只读免审批</span>') +
        '</div>' +
        '<div class="text-[10px] text-text-tertiary mt-1">' + escapeHtml(t.description.length > 90 ? t.description.slice(0, 90) + '…' : t.description) + '</div></div>' +
        '<div class="text-[10px] text-text-tertiary shrink-0 ' + (nativeOn ? 'text-success' : '') + '">' + (nativeOn ? '已由输入台开启' : '由输入台「本机工具」开关控制') + '</div></div>';
    }
    const on = !disabled.includes(t.name);
    return '<div class="flex items-center justify-between py-2 border-b border-line-ghost/30 last:border-0">' +
      '<div class="flex-1 pr-3"><div class="text-xs text-text-primary">' + escapeHtml(t.label) +
      ' <span class="font-mono text-[10px] text-text-tertiary">' + t.name + '</span></div>' +
      '<div class="text-[10px] text-text-tertiary mt-1">' + escapeHtml(t.description.length > 90 ? t.description.slice(0, 90) + '…' : t.description) + '</div></div>' +
      '<div class="toggle-track ' + (on ? 'active' : '') + '" data-tool-toggle="' + t.name + '"><div class="toggle-thumb"></div></div></div>';
  }).join('');
  const card = document.createElement('div');
  card.id = 'toolsSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = [
    '<div class="settings-group-tag" style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span style="width:3px;height:10px;border-radius:2px;background:var(--brand,#E5734A);flex-shrink:0"></span><span style="font-size:10px;font-weight:500;color:var(--color-text-secondary,#a3a3a3);letter-spacing:0.05em">Agent 与工具</span></div>',
    '<h3 class="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2"><i data-lucide="wrench" class="w-4 h-4 text-brand-violet"></i>AI 工具（Function Calling）</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">内置工具闭环：模型可调用时间、计算器、知识库检索、片段保存、工作流与费用查询；仅 OpenAI 兼容后端生效，Ollama 自动跳过。最大工具轮数在「本机 Agent」卡统一设置（全部工具循环共用，1-12）。</p>',
    '<div class="flex items-center gap-4 mb-3">',
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">启用工具调用</span><div class="toggle-track ' + (MoraySettings.get('toolsEnabled') ? 'active' : '') + '" id="setToolsEnabled"><div class="toggle-thumb"></div></div></div>',
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
      '<th class="text-left px-2 py-2 font-medium">时间</th><th class="text-left px-2 py-2 font-medium">工具</th>' +
      '<th class="text-left px-2 py-2 font-medium">参数摘要</th><th class="text-left px-2 py-2 font-medium">审批</th>' +
      '<th class="text-left px-2 py-2 font-medium">状态</th><th class="text-right px-2 py-2 font-medium">耗时</th></tr></thead><tbody>' +
      rows.map(r => {
        const st = statusLabel[r.status] || [r.status, 'text-text-tertiary'];
        return '<tr class="border-t border-line-ghost/40 hover:bg-surface-panel/50">' +
          '<td class="px-2 py-2 text-text-tertiary whitespace-nowrap">' + esc(r.ts) + '</td>' +
          '<td class="px-2 py-2 font-mono text-text-primary whitespace-nowrap">' + esc(r.tool) + '</td>' +
          '<td class="px-2 py-2 text-text-tertiary max-w-[260px] truncate" title="' + esc(r.args_summary) + '">' + esc(r.args_summary) + '</td>' +
          '<td class="px-2 py-2 ' + (r.approved ? 'text-success' : 'text-text-tertiary') + ' whitespace-nowrap">' + (r.approved ? '是' : '否') + '</td>' +
          '<td class="px-2 py-2 ' + st[1] + ' whitespace-nowrap">' + st[0] + '</td>' +
          '<td class="px-2 py-2 text-text-tertiary text-right whitespace-nowrap">' + (r.ms ? r.ms + 'ms' : '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>'
    : '<div class="empty-state"><i data-lucide="scroll-text"></i><div class="empty-title">暂无审计记录</div><div class="empty-hint">审计存于本地后端 SQLite；后端离线时无法读取</div></div>';
  const html = '<div class="space-y-2">' +
    '<div class="flex items-center gap-2 text-[10px] text-text-tertiary">' +
    '<span>共 ' + total + ' 条 · 第 ' + page + '/' + totalPages + ' 页</span>' +
    '<span class="ml-auto flex items-center gap-2">' +
    '<button data-audit-page="prev" class="btn-ghost px-2 py-1 rounded-md border border-line-ghost" ' + (page <= 1 ? 'disabled' : '') + '>上一页</button>' +
    '<button data-audit-page="next" class="btn-ghost px-2 py-1 rounded-md border border-line-ghost" ' + (page >= totalPages ? 'disabled' : '') + '>下一页</button>' +
    '<button data-audit-export class="btn-ghost px-2 py-1 rounded-md border border-line-ghost">导出 JSON</button>' +
    '<button data-audit-clear class="px-2 py-1 rounded-md text-[10px] bg-danger/10 text-danger border border-danger/30">清空</button>' +
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
  const container = document.querySelector('#settingsBody, #page-settings .max-w-2xl');
  if (!container || container.querySelector('#agentSettingsCard')) return;
  const cfg = await agentBackendConfig(true); // 强刷（避免设置页打开时读到陈旧缓存）
  const online = !!cfg;
  const approval = MoraySettings.get('agentApprovalMode') || 'readonly_auto';
  const rounds = Math.max(1, Math.min(12, parseInt(MoraySettings.get('toolsMaxRounds') || 3, 10) || 3));
  const card = document.createElement('div');
  card.id = 'agentSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = [
    '<div class="settings-group-tag" style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span style="width:3px;height:10px;border-radius:2px;background:var(--brand,#E5734A);flex-shrink:0"></span><span style="font-size:10px;font-weight:500;color:var(--color-text-secondary,#a3a3a3);letter-spacing:0.05em">Agent 与工具</span></div>',
    '<h3 class="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2"><i data-lucide="square-terminal" class="w-4 h-4 text-brand-violet"></i>本机 Agent（受控工作区工具）</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">本机动作由本地后端执行并审计：列目录/读文件自动执行，写文件与只读命令需人工授权；全部操作限制在工作区内，越界/危险后缀/白名单外命令会被安全层拒绝。开关在输入台「本机工具」按钮。</p>',
    online
      ? '<div class="flex items-center gap-2 mb-3 text-[10px] text-success"><span class="beacon-dot success"></span>本地后端已连接 · 版本 ' + escapeHtml(cfg.version || '') + ' · 构建 ' + escapeHtml(cfg.build || '') + '</div>'
      : '<div class="flex items-center gap-2 mb-3 text-[10px] text-warning"><span class="beacon-dot warn-dot"></span>本地后端离线：本机工具不可用（需启动后端，默认 http://127.0.0.1:8000）</div>',
    '<div class="rounded-lg border border-line-ghost/60 p-3 mb-3">',
    '<div class="flex items-center justify-between mb-2"><span class="text-xs text-text-secondary">工作区根</span>' +
    (cfg && cfg.fromEnv ? '<span class="text-[10px] px-2 py-1 rounded bg-warning/10 text-warning border border-warning/20">环境变量 MORAY_WORKSPACE 锁定</span>' : '') + '</div>',
    '<div class="flex gap-2">',
    '<input type="text" id="agentWorkspaceInput" value="' + escapeHtml(cfg ? cfg.workspace : '') + '" placeholder="D:\\MoRayWorkspace"' + (cfg && cfg.fromEnv ? ' disabled' : '') + ' class="form-input flex-1 font-mono" style="font-size:12px;padding:4px 8px">',
    '<button id="agentWorkspaceSave" class="btn-ghost px-3 py-2 rounded-lg text-[10px] border border-line-ghost" ' + (cfg && cfg.fromEnv ? 'disabled' : '') + '>保存并校验</button>',
    '</div>',
    (cfg && cfg.fromEnv ? '<div class="text-[10px] text-text-tertiary mt-2">环境变量优先于此处设置；如需修改请调整 MORAY_WORKSPACE 后重启后端</div>' : '<div class="text-[10px] text-text-tertiary mt-2">修改后立即生效并写入后端（自动创建目录并校验可写；仅影响本机 Agent 工具）</div>'),
    '</div>',
    '<div class="space-y-2 text-xs">',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">审批策略</span>' +
    '<select id="agentApprovalSelect" class="form-select" style="width:auto;padding:4px 8px;font-size:12px">' +
    '<option value="readonly_auto"' + (approval === 'readonly_auto' ? ' selected' : '') + '>只读自动执行（写文件/命令需审批）</option>' +
    '<option value="all"' + (approval === 'all' ? ' selected' : '') + '>全部工具需人工审批</option></select></div>',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">最大工具轮数（AI 工具与本机 Agent 共用，1-12）</span>' +
    '<input type="number" min="1" max="12" value="' + rounds + '" id="agentMaxRoundsInput" class="form-input" style="width:64px;padding:2px 8px;font-size:12px"></div>',
    '<div class="flex items-center justify-between"><span class="text-text-secondary">审计日志（后端 SQLite，含未审批与安全拒绝）</span>' +
    '<div class="flex items-center gap-2">' +
    '<button id="agentAuditViewBtn" class="btn-ghost px-3 py-1 rounded-lg text-[10px] border border-line-ghost">查看日志</button>' +
    // [Kernel 阶段1] 飞行记录仪（时间轴）：与审计表格同一数据源（GET /api/agent/log），改视图不改数据
    '<button id="agentTimelineBtn" class="btn-ghost px-3 py-1 rounded-lg text-[10px] border border-line-ghost flex items-center gap-1" title="按天分组的时间轴视图：状态/工具筛选、展开详情、自动刷新">' +
    '<i data-lucide="activity" class="w-3 h-3"></i>飞行记录仪</button>' +
    '</div></div>',
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
    '<button id="agentTrustedClear" class="btn-ghost px-3 py-1 rounded-lg text-[10px] border border-line-ghost">清除</button></div></div>',
    // [阶段1 M5] Agent 系统提示：查看内置 / 自定义覆盖
    (() => {
      const override = (MoraySettings.get('agentSysPromptOverride') || '').trim();
      const effective = override || PlanTracker.SYSTEM_PROMPT;
      return '<div class="pt-2 mt-2 border-t border-line-ghost/40">' +
        '<div class="text-xs text-text-secondary mb-2">Agent 系统提示（多步任务先列计划、工具时机、工作区边界）</div>' +
        '<textarea id="agentSysPromptInput" class="form-input w-full font-mono" rows="6" style="font-size:10px;line-height:1.6" placeholder="留空 = 使用内置规范">' + escapeHtml(effective) + '</textarea>' +
        '<div class="flex items-center gap-2 mt-2">' +
        '<button id="agentSysPromptSave" class="btn-ghost px-3 py-1 rounded-lg text-[10px] border border-line-ghost">保存覆盖</button>' +
        '<button id="agentSysPromptReset" class="btn-ghost px-3 py-1 rounded-lg text-[10px] border border-line-ghost">恢复内置</button>' +
        '<span class="text-[10px] text-text-tertiary">仅在本机工具开启时追加到系统提示</span></div></div>';
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
    const v = Math.max(1, Math.min(12, parseInt(roundsInput.value, 10) || 3));
    roundsInput.value = v;
    await MoraySettings.set('toolsMaxRounds', v);
    showNotification('已保存', '最大工具轮数 = ' + v, 'success', 1500);
  });
  on(card.querySelector('#agentAuditViewBtn'), 'click', () => showAgentAuditLog());
  // [Kernel 阶段1] 飞行记录仪入口（时间轴视图在 138_timeline.js；此处只转发，不改审计表格功能）
  on(card.querySelector('#agentTimelineBtn'), 'click', () => {
    if (typeof TimelineViewer !== 'undefined' && TimelineViewer.open) TimelineViewer.open();
    else showNotification('时间轴未就绪', '前端分片未加载完成，请刷新页面后重试', 'warning', 2600);
  });
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
  } else if (def.name === 'create_file') {
    // [3.20 阶段3] 目标文件状态参与指纹：同名文件出现/消失都要重新授权（防呆）
    const path = String((args && args.path) || '');
    try {
      const data = await wsFetchTool('read_file', { path, maxBytes: 4096 });
      fp += '::exists' + (typeof fnvHash64 === 'function' ? fnvHash64(String(data && data.content || '')) : '');
    } catch (e) {
      fp += '::absent';
    }
  } else if (def.name === 'move_file' || def.name === 'rename_file') {
    // 源文件内容变化 / 目标位置被占用 → 指纹变化，必须重新授权
    const src = String((args && (args.from || args.src)) || '');
    const dst = String((args && (args.to || args.dst)) || '');
    try {
      const d = await wsFetchTool('read_file', { path: src, maxBytes: 4096 });
      fp += '::src' + (typeof fnvHash64 === 'function' ? fnvHash64(String(d && d.content || '')) : '');
    } catch (e) {
      fp += '::srcmissing';
    }
    try {
      await wsFetchTool('read_file', { path: dst, maxBytes: 64 });
      fp += '::dsttaken';
    } catch (e) {
      fp += '::dstfree';
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
            (pj.data.truncated ? '<div class="text-[10px] text-warning mt-1">原文件超过 64KB，diff 仅基于前 64KB</div>' : '');
        }
      } catch (e) { /* 读不到旧内容（新文件/离线）→ 不渲染 diff */ }
    }
    // 参数卡展示（写文件=路径+预览；命令=完整命令；其余=参数 JSON）
    let argCards = '';
    const cell = 'rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2';
    if (def.name === 'write_file') {
      const c = String(args.content || '');
      const diffBlock = writeDiff
        ? '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-2">变更预览（- 原内容 / + 新内容）</div>' + writeDiff + '</div>'
        : '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1">内容预览（前 300 字符）</div>' +
          '<div class="text-[10px] text-text-primary whitespace-pre-wrap break-all max-h-28 overflow-auto font-mono">' + (c ? escapeHtml(c.slice(0, 300)) + (c.length > 300 ? '<span class="text-warning"> …（已截断，共 ' + c.length + ' 字符）</span>' : '') : '<span class="text-text-tertiary">（空内容）</span>') + '</div></div>';
      argCards = '<div class="rounded-lg bg-surface-panel/60 border border-line-ghost/50 p-3">' +
        '<div class="text-[10px] text-text-tertiary mb-2">目标文件（工作区内，相对路径）</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.path || '')) + '</div>' +
        (args.overwrite === false ? '<div class="text-[10px] text-warning mt-1">仅新建，不覆盖已有文件</div>' : '<div class="text-[10px] text-text-tertiary mt-1">已有同名文件将被覆盖</div>') +
        '</div>' + diffBlock;
    } else if (def.name === 'create_file') {
      // [3.20 阶段3] 新建文件：明确"只新建不覆盖"，并给出内容预览
      const c = String(args.content || '');
      argCards = '<div class="' + cell + '">' +
        '<div class="text-[10px] text-text-tertiary mb-2">新建文件（工作区内，相对路径）</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.path || '')) + '</div>' +
        '<div class="text-[10px] text-success mt-1">只新建：若同名文件已存在会被拒绝，不会覆盖</div>' +
        '</div>' +
        '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1">内容预览（前 300 字符）</div>' +
        '<div class="text-[10px] text-text-primary whitespace-pre-wrap break-all max-h-28 overflow-auto font-mono">' +
        (c ? escapeHtml(c.slice(0, 300)) + (c.length > 300 ? '<span class="text-warning"> …（已截断，共 ' + c.length + ' 字符）</span>' : '') : '<span class="text-text-tertiary">（空文件）</span>') +
        '</div></div>';
    } else if (def.name === 'rename_file' || def.name === 'move_file') {
      // [3.20 阶段3] 移动/重命名：源 → 目标，并提示"目标存在即拒绝"
      argCards = '<div class="' + cell + '">' +
        '<div class="text-[10px] text-text-tertiary mb-2">' + (def.name === 'rename_file' ? '重命名（工作区内）' : '移动（工作区内）') + '</div>' +
        '<div class="font-mono text-[11px] text-text-tertiary break-all">' + escapeHtml(String(args.from || '')) + '</div>' +
        '<div class="text-[10px] text-text-tertiary my-1">↓</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.to || '')) + '</div>' +
        '<div class="text-[10px] text-text-tertiary mt-2">目标已存在会拒绝，不做静默覆盖；父目录不存在会自动创建</div>' +
        '</div>';
    } else if (def.name === 'run_command') {
      const parts = [String(args.command || '')].concat(Array.isArray(args.args) ? args.args.map(String) : []);
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-2">将执行的只读白名单命令</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">$ ' + escapeHtml(parts.join(' ')) + '</div>' +
        '<div class="text-[10px] text-text-tertiary mt-2">命令只读、15s 超时，在工作区内执行（git status/log/diff/branch、python/node/npm --version、where &lt;命令名&gt;）</div></div>';
    } else if (def.name === 'edit_file') {
      const oldS = String(args.old_str || '');
      const newS = String(args.new_str || '');
      const diffHtml = (typeof buildApprovalDiff === 'function')
        ? buildApprovalDiff(oldS, newS, 400)
        : '<div class="text-[10px] text-text-primary font-mono whitespace-pre-wrap break-all max-h-40 overflow-auto">' + escapeHtml(oldS.slice(0, 200)) + '</div>';
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-2">编辑目标（工作区内）</div>' +
        '<div class="font-mono text-[11px] text-text-primary break-all">' + escapeHtml(String(args.path || '')) + '</div>' +
        (args.replace_all === true ? '<div class="text-[10px] text-warning mt-1">replace_all：替换文件中全部匹配处</div>' : '<div class="text-[10px] text-text-tertiary mt-1">仅替换唯一匹配处（不唯一将被拒绝）</div>') +
        '</div>' +
        '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-2">变更预览（- 原内容 / + 新内容）</div>' + diffHtml + '</div>';
    } else {
      argCards = '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-2">参数</div>' +
        '<div class="font-mono text-[10px] text-text-primary break-all max-h-24 overflow-auto">' + escapeHtml(JSON.stringify(args || {})) + '</div></div>';
    }
    // [阶段0.5 E] 信任勾选：仅非 run_command 工具提供（命令永远每次审批）；默认不勾选
    const trustRow = def.name !== 'run_command'
      ? '<label class="flex items-start gap-2 pt-1 cursor-pointer select-none">' +
        '<input type="checkbox" data-agent-trust class="mt-1 accent-brand-cobalt">' +
        '<span class="text-[10px] text-text-tertiary leading-relaxed">本会话对完全相同的工具+参数不再询问（刷新后失效；可在设置→本机 Agent 清除）</span></label>'
      : '';
    const html = '<div class="space-y-3 text-xs">' +
      '<div class="' + cell + '"><div class="text-[10px] text-text-tertiary mb-1">工具（' + (def.sideEffect ? '副作用，需要你授权' : '只读') + '）</div>' +
      '<div class="flex items-center gap-2 font-medium text-text-primary">' +
      '<i data-lucide="' + (def.name === 'run_command' ? 'terminal' : def.name === 'write_file' ? 'file-pen-line' : 'folder-search') + '" class="w-3.5 h-3.5 text-warning"></i>' +
      escapeHtml(def.label) + ' <span class="font-mono text-[10px] text-text-tertiary">' + def.name + '</span></div></div>' +
      argCards +
      trustRow +
      '<div class="text-[10px] text-text-tertiary leading-relaxed">提示：授权只在本弹窗内点击「允许一次」才生效；在聊天框输入「同意/授权」不会生效，关闭弹窗或按 ESC 一律视为拒绝。</div>' +
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
    // [3.20 阶段3] 只新建不覆盖：语义上比 write_file 更安全，适合"创建新文件"场景
    name: 'create_file', label: '新建文件', native: true, sideEffect: true, timeoutMs: 300000,
    description: '在工作区内新建一个文本文件（需用户授权）。文件已存在时会被拒绝，绝不覆盖任何现有文件——需要覆盖已有文件请改用 write_file 并单独确认。父目录不存在会自动创建。当用户说"新建一个 X 文件 / 创建一个脚本文件"时使用它。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '相对工作区根的新文件路径（必填，如 notes/todo.md）' },
        content: { type: 'string', description: '文件内容（UTF-8 文本，可为空串创建空文件）' }
      },
      required: ['path']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'rename_file', label: '重命名文件', native: true, sideEffect: true, timeoutMs: 60000,
    description: '在工作区内重命名文件或目录（需用户授权，可理解为同一目录内的移动）。目标已存在时会被拒绝，不会覆盖。当用户说"把 a.txt 改名成 b.txt"时使用。',
    parameters: {
      type: 'object',
      properties: {
        from: { type: 'string', description: '相对工作区根的原路径（必填，必须存在）' },
        to: { type: 'string', description: '重命名后的相对路径（必填，通常与 from 同目录）' }
      },
      required: ['from', 'to']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    name: 'move_file', label: '移动文件', native: true, sideEffect: true, timeoutMs: 60000,
    description: '在工作区内把文件或目录移动到另一个位置（需用户授权）。目标已存在时会被拒绝，不会覆盖；父目录不存在会自动创建。当用户说"把 X 移到 Y 目录"时使用。',
    parameters: {
      type: 'object',
      properties: {
        from: { type: 'string', description: '相对工作区根的源路径（必填，必须存在）' },
        to: { type: 'string', description: '相对工作区根的目标路径（必填）' }
      },
      required: ['from', 'to']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    // [3.20 阶段3] 只读：进程列表（按内存倒序），无需审批
    name: 'list_processes', label: '查看进程', native: true, sideEffect: false, timeoutMs: 15000,
    description: '列出本机正在运行的进程（pid / 进程名 / 占用内存），默认按内存占用倒序取前 30 条。只读，不结束也不修改任何进程。当用户问"现在有哪些进程在占内存 / 电脑卡是不是某个程序"时使用。',
    parameters: {
      type: 'object',
      properties: {
        limit: { type: 'integer', description: '返回条数（默认 30，上限 200）' },
        sort: { type: 'string', description: 'memory（默认，按内存倒序）或 pid' }
      }
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    // [3.20 阶段3] 只读：系统概览，无需审批
    name: 'system_info', label: '系统信息', native: true, sideEffect: false, timeoutMs: 15000,
    description: '读取本机系统只读概览：操作系统版本、CPU 核数与负载、内存总量与可用量、工作区所在磁盘容量。不含任何用户名/环境变量/文件内容。当用户问"这台机器配置怎么样 / 内存还剩多少 / 磁盘够不够"时使用。',
    parameters: { type: 'object', properties: {} },
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
  },
  {
    // [Laya v3] ZCode 执行引擎：运行本地脚本 + 输出捕获 + 预览识别（后端 core/zcode_proxy.py）
    name: 'zcode_run', label: '运行脚本', native: true, sideEffect: true, timeoutMs: 330000,
    description: '执行本地磁盘上的代码脚本文件，支持 Python、Node.js 等多语言运行。当用户给出本地文件路径并要求运行、执行、启动脚本时使用，直接传入文件路径即可执行，无需搜索联网。',
    parameters: {
      type: 'object',
      properties: {
        file_path: { type: 'string', description: '本地脚本文件的真实路径（须在授权目录或工作区内）' },
        args: { type: 'string', description: '传给脚本的命令行参数（可省略）' },
        lang: { type: 'string', description: '运行方式：auto（默认按扩展名）/ python / javascript / batch' }
      },
      required: ['file_path']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  },
  {
    // [Laya v3] ZCode 原生子智能体：按场景注入专用提示词，限定工作目录内自主完成
    name: 'zcode_sub_agent', label: '派发ZCode子智能体', native: true, sideEffect: true, timeoutMs: 660000,
    description: '调用本地子智能体完成代码修复、功能开发、排错调试任务。当用户需要修改本地代码、修复bug、开发新功能时使用，工作目录限定在指定路径内，自动完成读写+运行验证全流程。',
    parameters: {
      type: 'object',
      properties: {
        task: { type: 'string', description: '子任务指令（目标文件路径 + 需求 + 验收标准）' },
        work_dir: { type: 'string', description: '工作目录真实路径（授权目录内，子智能体不得越界）' },
        scene: { type: 'string', description: '场景：bug_fix / feature_dev / debug / general（默认 general）' }
      },
      required: ['task', 'work_dir']
    },
    run(args, ctx) { return agentNativeRun(this, args, ctx); }
  }
];
NATIVE_TOOLS.forEach(t => ToolRegistry.register(t));

/* ============================================================
   [Argus 深度研究] 研究工具（detective_research / detective_status）
   - 链路：模型 tool_calls → Gateway.runWithTools → 本工具 run() →
           同源 POST/GET /api/argus/*（server/app/argus_proxy.py 转发到 8010）
   - 为什么经后端转发而不是前端直连 8010：Argus 没有 CORS 中间件，页面来自
           http://127.0.0.1:8000 或 file:// 都是跨源，直连会被浏览器拦；同源转发
           则 Argus 一行都不用改
   - 非 native：不进"本机工具/Agent"那道门，随「工具调用」总开关进入 listForRequest，
           因此描述里必须写清"何时使用"，否则模型不会主动调
   - sideEffect=false：启动研究只建会话 + 起后台任务，立即返回 session_id，
           不需要人工审批（审批卡是给写文件/执行命令那类不可逆操作准备的）
   ============================================================ */

/** Argus 工具通用请求体：同源后端 → Argus。
 * 后端统一信封 {ok, data|code|message}；失败抛出可读文案，由工具循环作为"工具失败"
 * 回灌给模型（模型据此如实说明，而不是把超时当成没有能力）。
 * @param {string} path - /api/argus/... 路径
 * @param {Object} [opts] - {method, body, signal}
 * @returns {Promise<Object>} data */
async function argusToolRun(path, opts) {
  const o = opts || {};
  let res;
  try {
    res = await fetch(agentBackendBase() + path, {
      method: o.method || 'GET',
      headers: o.body ? { 'Content-Type': 'application/json' } : undefined,
      body: o.body ? JSON.stringify(o.body) : undefined,
      signal: o.signal || undefined
    });
  } catch (e) {
    if (e && e.name === 'AbortError') throw e;
    throw new Error('Argus 工具需要本地后端（未连接 127.0.0.1:8000），本次调用未发出');
  }
  let j = null;
  try { j = await res.json(); } catch (e) { /* 兜底 */ }
  if (!j || j.ok !== true) throw new Error((j && j.message) || ('Argus 接口返回 ' + res.status));
  return j.data;
}

/** 报告正文回灌上限：研报可能上万字，截断以免单条 tool 消息撑爆上下文 */
const ARGUS_REPORT_CHARS = 6000;

/** [Argus] 进度查询去重：session_id → 上次查询时间戳。
 *
 * 研究要跑几分钟，而小模型会把"好了吗"问成循环（调一次侦探状态 → 还在跑 → 再调一次…），
 * 把工具轮刷满、回退普通流式后只能凭记忆编一个 session_id。同一 session 短时间内重复查
 * 第二次起就在结果里带 ``argusStopAsking``，由工具循环把 detective_status 从后续轮次摘掉
 * （见 Gateway.runWithTools）。新一轮用户消息会重新问，不受影响。
 * @type {Object<string,number>} */
const ARGUS_STATUS_ASKS = {};
/** 同一 session 两次查询间隔小于这个值即视为工具轮循环（ms） */
const ARGUS_STATUS_REPEAT_MS = 120000;

/** [Argus] 本会话"最近启动/查询过哪条研究"的记账（上下文感知兜底的判定条件 2）。
 *
 * 为什么不直接用 ArgusWatcher._tasks：那张表是**跟踪用**的，超时（15 分钟）/离线/取报告
 * 失败时会被 _stop 删掉——而用户正是在那些时候按提示回来问"进度如何"，此时跟踪表里
 * 已经没有条目，兜底就哑了。这张表只回答"这个对话最近碰过哪个 session_id"，TTL 内一直留着。
 * @type {Object<string,{sid:string,convId:(string|null),question:string,startedAt:number,touchedAt:number}>} */
const ARGUS_SESSION_LOG = {};

/** [Argus] 登记一条研究会话（detective_research / detective_status 的 run() 都会调）
 * @param {string} sessionId - Argus 返回的 session_id
 * @param {string} [question] - 研究问题
 * @returns {void} */
function argusNoteSession(sessionId, question) {
  try {
    const sid = String(sessionId || '').trim();
    if (!/^[A-Za-z0-9_-]{6,}$/.test(sid)) return;
    const prev = ARGUS_SESSION_LOG[sid] || null;
    const convId = (typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null;
    ARGUS_SESSION_LOG[sid] = {
      sid,
      convId: convId || null,
      question: String(question || (prev && prev.question) || ''),
      startedAt: (prev && prev.startedAt) || Date.now(),
      touchedAt: Date.now()
    };
  } catch (e) { /* 记账失败不影响主链路 */ }
}

/** [Argus] 汇总"已知的研究会话"：本会话记账 + 跟踪表（后者覆盖刷新后续跑的任务）
 * @returns {Array<Object>} 记录数组（形状见 argusPickActiveSession） */
function argusSessionRecords() {
  const rows = Object.keys(ARGUS_SESSION_LOG).map(k => ARGUS_SESSION_LOG[k]);
  try {
    const tasks = (typeof ArgusWatcher !== 'undefined' && ArgusWatcher._tasks) ? ArgusWatcher._tasks : null;
    if (tasks) {
      Object.keys(tasks).forEach(sid => {
        const t = tasks[sid] || {};
        rows.push({
          sid, convId: t.convId, question: t.question,
          startedAt: t.startedAt, touchedAt: t.startedAt
        });
      });
    }
  } catch (e) { /* 跟踪表不可用就只用记账 */ }
  return rows;
}

/** [Argus] 两个研究工具定义 */
const ARGUS_TOOLS = [
  {
    name: 'detective_research', label: 'Argus 深度研究',
    description: '强制优先调用：只要用户要求查资料、做调研、对比产品/价格、了解行业信息（包括提到 "Argus"、"深度研究"、"帮我查一下"），就调用本工具，不要先用自己的记忆回答。调用 Argus 深度研究模块，自动搜索、抓取网页、交叉验证，生成带引用的研究报告。返回的 session_id 是研究任务的唯一凭证 —— 只有本工具返回的 id 才是真的。',
    parameters: {
      type: 'object',
      properties: {
        question: { type: 'string', description: "用户的研究问题，比如 'Cursor 价格' 或 'AI编程助手对比'" }
      },
      required: ['question']
    },
    sideEffect: false, timeoutMs: 30000,
    async run(args, ctx) {
      const question = String((args && args.question) || '').trim();
      if (!question) throw new Error('缺少 question 参数（要研究的问题）');
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      // 同会话同问题已有研究在跑 → 复用，不再新建（小模型会把本工具连着调好几次，起一堆重复研究）
      const running = (typeof ArgusWatcher !== 'undefined') ? ArgusWatcher.findRunning(question) : null;
      if (running) {
        argusNoteSession(running.sid, question); // 上下文感知兜底：本轮问进度时认得这个 id
        return {
          session_id: running.sid, status: 'running', question, reused: true, argusStopAsking: true,
          note: '这个问题已经有正在进行的 Argus 研究（session_id = ' + running.sid + '）。' +
            '**不要再调用 detective_research**：把 session_id 告诉用户、说明还需要几分钟即可，' +
            '报告生成后会自动出现在本轮对话里。'
        };
      }
      const d = await argusToolRun('/api/argus/orchestrate', {
        method: 'POST', body: { question }, signal: ctx && ctx.signal
      });
      // 上下文感知兜底：记下"这次对话刚启动了哪条研究"，下一轮问"进度如何"时由代码直接查
      argusNoteSession(d.session_id, d.question || question);
      // [Argus 深度研究] 起完研究就把后半程交给跟踪器：报告一出就自动追加进本会话，
      // 不依赖模型是否愿意再调一次 detective_status
      if (typeof ArgusWatcher !== 'undefined') ArgusWatcher.track(d.session_id, { question });
      return {
        session_id: d.session_id,
        status: d.status || 'running',
        question: d.question || question,
        // 研究已经在跑，本轮再调本工具没有意义（再调只会起重复研究）
        argusStopAsking: true,
        note: '研究已在 Argus 后台启动（session_id = ' + d.session_id + '）。**不要再调用 detective_research**：' +
          '报告生成后会自动出现在本轮对话里；现在把上面的 session_id 告诉用户、说明研究通常需要几分钟即可。' +
          '用户主动问进度时才需要 detective_status（带这个 session_id）。'
      };
    }
  },
  {
    name: 'detective_status', label: 'Argus 研究进度',
    description: '强制优先调用：只要用户问某个 Argus 研究任务的进度或结果（"好了吗 / 进度如何 / 查到了什么 / 结果出来没"），就调用本工具查询，不要凭记忆或想象回答。查询 Argus 研究任务的当前状态和进度；报告就绪时会一并返回报告正文与引用，价格/结论/来源只能引用该正文。',
    parameters: {
      type: 'object',
      properties: {
        session_id: { type: 'string', description: '研究任务的 session id' }
      },
      required: ['session_id']
    },
    sideEffect: false, timeoutMs: 30000,
    async run(args, ctx) {
      const sidRaw = String((args && args.session_id) || '').trim();
      if (!sidRaw) throw new Error('缺少 session_id 参数（detective_research 返回的那个）');
      // 容错：模型有时会把 "session_id=abc" 或全角字符一起塞进来，只取其中的 id 本体
      const sid = (sidRaw.match(/[A-Za-z0-9_-]{6,}/) || [sidRaw])[0];
      if (ctx && ctx.signal && ctx.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      const d = await argusToolRun('/api/argus/status/' + encodeURIComponent(sid), { signal: ctx && ctx.signal });
      // 上下文感知兜底：查过一次也算"这个对话认得这条研究"，续问同一个 id 时继续命中
      argusNoteSession(sid, d.question);
      // 同一 session 短时间内的重复查询 = 小模型的工具轮循环 → 第二次起请工具循环摘掉本工具
      const asked = ARGUS_STATUS_ASKS[sid] || 0;
      const repeated = (Date.now() - asked) < ARGUS_STATUS_REPEAT_MS;
      ARGUS_STATUS_ASKS[sid] = Date.now();
      const out = {
        session_id: d.session_id || sid,
        status: d.status,
        progress: d.progress,
        report_ready: !!d.report_ready,
        question: d.question || ''
      };
      if (repeated) out.argusStopAsking = true; // 本轮已问过同一个任务，别再问了
      // 研究完成 → 顺带取回报告正文：只报"已完成"而说不出结论，对用户没有价值
      if (d.report_ready) {
        out.argusStopAsking = true; // 正文已经在手，直接回答用户，不需要再查
        const r = await argusToolRun('/api/argus/report/' + encodeURIComponent(sid), { signal: ctx && ctx.signal });
        const md = String(r.markdown || r.body || '');
        out.report = md.length > ARGUS_REPORT_CHARS ? md.slice(0, ARGUS_REPORT_CHARS) + '\n…（报告过长已截断）' : md;
        out.report_truncated = md.length > ARGUS_REPORT_CHARS;
        const cites = Array.isArray(r.citations) ? r.citations : [];
        out.citations = cites.slice(0, 20);
        // 报告已经由跟踪器自动贴进对话 → 让模型总结要点，别把几万字正文再抄一遍
        const autoShown = (typeof ArgusWatcher !== 'undefined')
          && ArgusWatcher._alreadyShown(sid, (typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null);
        out.note = autoShown
          ? '研究已完成，报告正文已经自动出现在本轮对话（"Argus 深度研究"那条消息）。' +
            '请直接基于它用中文总结关键结论与来源，不要重复粘贴全文。'
          : '研究已完成。请基于 report 正文用中文回答用户的问题，并按 citations 标注来源；' +
            '报告过长时说明只呈现了要点（完整报告见 Argus 页面）。';
      } else {
        // 还没完成 → 交给跟踪器盯着，出报告时自动贴进对话（用户不必再来问一次）
        if (d.status !== 'failed' && typeof ArgusWatcher !== 'undefined') ArgusWatcher.track(sid, { question: d.question });
        out.note = d.status === 'failed'
          ? '研究失败（Argus 侧报错）。请如实告知用户，不要编造结论。'
          : '研究仍在进行中，可直接把 progress 告诉用户；报告生成后会自动出现在本轮对话里，稍等即可。';
      }
      return out;
    }
  }
];
ARGUS_TOOLS.forEach(t => ToolRegistry.register(t));

/* ============================================================
   [Argus 深度研究] 研究任务自动跟踪（轮询状态 → 拉报告 → 追加助手消息）
   要解决的问题：detective_research 只建会话 + 起后台任务，立即返回 session_id
   （研究本身要跑几分钟）。此前链路到此为止 —— 模型除了说一句"已启动，请稍后再查"
   什么也做不了，报告永远回不到对话里；用户必须自己再问一次"好了吗"，而且
   小模型经常连这一步都不愿意调工具。
   本跟踪器把后半程补成确定性的代码行为（Argus / Moray 后端零改动，只读既有三个接口）：
     起研究 → 登记 (session_id, 会话) → 定时 GET /api/argus/status/{sid}
       → report_ready（或 done/completed）→ GET /api/argus/report/{sid}
       → data.markdown 作为一条 assistant 消息追加进原会话
   - 渲染走既有管线（buildMessageEl → renderMarkdown），不新增任何样式；
   - 完成时若用户正在生成：只**追加本节点**，绝不整表重绘（重绘会丢掉流式气泡）；
   - 刷新后凭 localStorage 续跑；同一 session_id 只追加一次。
   [Day24.5] 轮询不再是"后台静默"：每轮把 status.progress / event_count / 已用时长
   画进输入台上方的一块进度面板（PANEL_ID），超时/断网/取报告失败也**落一条消息**
   而不是只弹通知。为什么面板挂在消息流之外（#chat-normal 里、输入台之前）：
   ① 进度是过程信息，不该落库成消息、也不进消息索引；
   ② 消息区会被 renderMessages 整表重绘，注入进去的节点随时会被抹掉；
   ③ 终态时报告才作为消息落库（既有行为不变），面板消失。
   ============================================================ */
const ArgusWatcher = {
  /** 轮询起步间隔（ms）：任务书建议 3-5s，取上限——起步就能看到进度在动 */
  POLL_MS: 4000,
  /** [Day24.5] 轮询退避档位：{after,ms} = 已跟踪超过 after(ms) 后改用 ms 间隔。
   * 长任务没必要每 4s 敲一次：15 分钟窗口内约 85 次请求，恒定 4s 要 225 次。 */
  POLL_STEPS: [
    { after: 0, ms: 4000 },
    { after: 2 * 60 * 1000, ms: 8000 },
    { after: 8 * 60 * 1000, ms: 15000 }
  ],
  /** 单个任务最长跟踪时长（ms）：[Day24.5] 30 → 15 分钟（任务书要求）。
   * 超时只停"自动取报告"，Argus 后台的研究不受影响；ARGUS_SESSION_LOG 的保鲜期
   * （60 分钟）宽于它，所以用户按提示问「进度如何」照样能命中状态兜底并重新开始跟踪。 */
  MAX_WAIT_MS: 15 * 60 * 1000,
  /** 连续失败上限：后端没起/断网时不能无限重试 */
  MAX_FAILS: 6,
  /** 刷新后续跑用的 localStorage 键 */
  STORE_KEY: 'moray_argus_watch',
  /** 报告消息在气泡头部显示的"模型名"（这不是某次模型推理，别显示成 qwen…） */
  LABEL: 'Argus 深度研究',
  /** [Day24.5] 进度面板的 DOM id（挂在 #chat-normal 内、输入台之前） */
  PANEL_ID: 'argusProgressPanel',

  /** 在跟踪的任务
   * @type {Object<string,{convId:(string|null),question:string,startedAt:number,fails:number,reportedAt:number,progress:string,eventCount:number}}> */
  _tasks: {},
  /** 定时器句柄 @type {Object<string,number>} */
  _timers: {},
  /** 秒表定时器句柄（只在面板存在时跑，面板消失即自停） @type {number} */
  _panelTimer: 0,
  /** 面板当前展示的 session_id @type {string} */
  _panelSid: '',

  /** 登记研究任务并开始轮询（同一 session_id 重复登记幂等）
   * @param {string} sessionId - Argus 研究会话 id（detective_research 返回的那个）
   * @param {Object} [opts] - {convId, question, startedAt}
   * @returns {boolean} 是否已开始跟踪 */
  track(sessionId, opts) {
    try {
      const sid = String(sessionId || '').trim();
      if (!sid || !/^[A-Za-z0-9_-]{6,}$/.test(sid)) return false;
      const o = opts || {};
      const prev = this._tasks[sid];
      if (prev && prev.reportedAt) return false; // 这条会话的报告已经展示过
      const activeId = (typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null;
      const convId = o.convId || (prev && prev.convId) || activeId || null;
      if (!convId) return false; // 没有归属会话：报告无处可放（不做无主追加）
      if (this._alreadyShown(sid, convId)) return false;
      this._tasks[sid] = {
        convId,
        question: String(o.question || (prev && prev.question) || ''),
        startedAt: Number(o.startedAt || (prev && prev.startedAt)) || Date.now(),
        fails: 0,
        reportedAt: 0,
        progress: String(o.progress || (prev && prev.progress) || ''),
        eventCount: Number(o.eventCount || (prev && prev.eventCount)) || 0
      };
      this._persist();
      this._schedule(sid, 1500); // 首次稍等，避开刚返回的那次请求
      this._panelPaint(); // [Day24.5] 立刻亮出进度面板，别让用户对着空气等
      console.log('[MoRay] Argus 研究跟踪已启动 session=' + sid);
      return true;
    } catch (e) {
      console.warn('[MoRay] Argus 跟踪登记失败:', e);
      return false;
    }
  },

  /** 找同一会话里仍在跑、且问题相同/相近的研究任务。
   *
   * 为什么需要：detective_research 的描述写着"强制优先调用"，而 7B 级小模型会把工具轮
   * 刷满——实测一次"用 Argus 查一下 Cursor 价格"被连着调了 7 次，起了 7 个同一个问题的
   * 研究（各自都会出一份报告）。重复起研究对用户没有任何价值，所以在**前端**兜住：
   * 同会话同问题已有任务在跑就复用那个 session_id，不再新建。
   * @param {string} question - 本次要研究的问题
   * @param {string} [convId] - 会话 id（缺省取当前会话）
   * @returns {{sid:string,question:string,startedAt:number}|null} 命中的在跑任务 */
  findRunning(question, convId) {
    try {
      const q = this._normQuestion(question);
      if (!q) return null;
      const cid = convId || ((typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null);
      let found = null;
      Object.keys(this._tasks).forEach(sid => {
        const t = this._tasks[sid];
        if (!t || t.reportedAt) return;
        if (cid && t.convId && t.convId !== cid) return;
        const tq = this._normQuestion(t.question);
        // 相等或互相包含即视为同一件事（"Cursor 价格" vs "Cursor 价格查询"）
        if (tq === q || tq.indexOf(q) >= 0 || q.indexOf(tq) >= 0) {
          if (!found || t.startedAt < found.startedAt) found = { sid, question: t.question, startedAt: t.startedAt };
        }
      });
      return found;
    } catch (e) {
      return null;
    }
  },

  /** 问题归一化（忽略大小写、空白与标点）
   * @param {string} s - 原始问题
   * @returns {string} */
  _normQuestion(s) {
    return String(s || '').toLowerCase().replace(/[\s，。,.、!！?？:：;；"'“”‘’()（）[\]【】]+/g, '');
  },

  /** 在消息索引 + 当前消息表里扫一遍（报告去重与提示去重共用）
   * @param {string|null} convId - 会话 id
   * @param {Function} pred - (msg)=>boolean
   * @returns {boolean} 命中任一即 true */
  _scanMessages(convId, pred) {
    try {
      const lists = [];
      const idx = (typeof AppState !== 'undefined' && AppState._convMsgIndex) ? AppState._convMsgIndex : null;
      if (idx && convId && idx[convId]) lists.push(idx[convId]);
      if (typeof AppState !== 'undefined' && Array.isArray(AppState.messages)) lists.push(AppState.messages);
      return lists.some(list => list.some(m => {
        try { return !!(m && pred(m)); } catch (e) { return false; }
      }));
    } catch (e) {
      return false;
    }
  },

  /** 该 session 的报告是否已经作为消息存在（防刷新/重复登记导致的双份报告）
   * [Day24.5] 提示类消息（argusNotice）不算报告：超时/断网提示过之后，
   * 研究真出了报告仍然必须能贴进来。
   * @param {string} sid - session_id
   * @param {string|null} convId - 会话 id
   * @returns {boolean} */
  _alreadyShown(sid, convId) {
    return this._scanMessages(convId, m => m.argusSessionId === sid && !m.argusNotice);
  },

  /** 同一 session 是否已经贴过同原因的提示（超时/断网/取报告失败）——反复重试时不刷屏
   * @param {string} sid - session_id
   * @param {string} reason - timeout | offline | error
   * @param {string|null} convId - 会话 id
   * @returns {boolean} */
  _noticeShown(sid, reason, convId) {
    return this._scanMessages(convId, m => m.argusSessionId === sid && m.argusNotice === reason);
  },

  /** 当前该用的轮询间隔：按已跟踪时长退避（见 POLL_STEPS）
   * @param {number} elapsedMs - 已跟踪时长（ms）
   * @returns {number} 间隔（ms） */
  _intervalFor(elapsedMs) {
    const e = Math.max(0, Number(elapsedMs) || 0);
    let ms = this.POLL_MS;
    (this.POLL_STEPS || []).forEach(s => { if (s && e >= s.after) ms = s.ms; });
    return ms;
  },

  /** 安排下一次轮询（同一 sid 只保留一个定时器；缺省按退避档位计时）
   * @param {string} sid - session_id
   * @param {number} [delay] - 延迟（ms；省略即取退避档位）
   * @returns {void} */
  _schedule(sid, delay) {
    if (this._timers[sid]) clearTimeout(this._timers[sid]);
    const task = this._tasks[sid] || {};
    const step = this._intervalFor(Date.now() - (task.startedAt || Date.now()));
    const wait = Math.max(300, Number(delay) || step);
    this._timers[sid] = setTimeout(() => {
      delete this._timers[sid];
      this._poll(sid).catch(e => console.warn('[MoRay] Argus 轮询异常:', e));
    }, wait);
  },

  /** 轮询一次状态：running 继续等（顺带刷新进度面板）；done/completed → 取报告；failed → 如实告知
   * @param {string} sid - session_id
   * @returns {Promise<void>} */
  async _poll(sid) {
    const task = this._tasks[sid];
    if (!task || task.reportedAt) return;
    if (Date.now() - task.startedAt > this.MAX_WAIT_MS) { await this._stopNotice(sid, 'timeout'); return; }
    let d = null;
    try {
      d = await argusToolRun('/api/argus/status/' + encodeURIComponent(sid));
    } catch (e) {
      task.fails = (task.fails || 0) + 1;
      task.progress = '查询进度失败（' + task.fails + '/' + this.MAX_FAILS + '）：' +
        String((e && e.message) || e).slice(0, 80);
      this._persist();
      this._panelPaint();
      if (task.fails >= this.MAX_FAILS) { await this._stopNotice(sid, 'offline', String((e && e.message) || e)); return; }
      this._schedule(sid, this.POLL_MS * Math.min(task.fails, 3)); // 退避重试
      return;
    }
    task.fails = 0;
    const status = String((d && d.status) || '').toLowerCase();
    // [Day24.5] 每轮的真实进度都存在任务上，由面板重绘（不落库、不进消息流）
    const prog = String((d && d.progress) || '').trim();
    if (prog) task.progress = prog;
    const evts = Number((d && d.event_count) || 0);
    if (evts) task.eventCount = evts;
    if (status === 'failed' || status === 'error') { await this._deliverFailure(sid, d); return; }
    if ((d && d.report_ready) || status === 'done' || status === 'completed') {
      task.progress = '研究完成，正在取报告…';
      this._panelPaint();
      await this._deliver(sid);
      return;
    }
    if (!task.progress) task.progress = '研究进行中…';
    this._panelPaint();
    this._schedule(sid);
  },

  /** 终态成功：拉报告正文并追加为助手消息
   * @param {string} sid - session_id
   * @returns {Promise<void>} */
  async _deliver(sid) {
    const task = this._tasks[sid];
    if (!task || task.reportedAt) return;
    let r = null;
    try {
      r = await argusToolRun('/api/argus/report/' + encodeURIComponent(sid));
    } catch (e) {
      // 状态说完成、报告接口却报错：再等两轮，仍失败就交代清楚，别静默消失
      task.fails = (task.fails || 0) + 1;
      task.progress = '报告读取失败（' + task.fails + '/3），重试中…';
      this._panelPaint();
      if (task.fails >= 3) { await this._stopNotice(sid, 'error', String((e && e.message) || e)); return; }
      this._schedule(sid);
      return;
    }
    const md = String((r && (r.markdown || r.body)) || '').trim();
    if (!md) {
      // 还查不到报告正文（ready=false）：再等一轮；确认为空则如实说明
      if (!r || r.ready !== true) { this._schedule(sid); return; }
      await this._emit(sid, '> ⚠️ Argus 研究已完成，但报告正文为空（session_id: `' + sid + '`）。');
      return;
    }
    await this._emit(sid, md);
  },

  /** 终态失败：追加一条简短说明（不编造结论，也不让用户干等）
   * @param {string} sid - session_id
   * @param {Object} d - 状态响应
   * @returns {Promise<void>} */
  async _deliverFailure(sid, d) {
    const task = this._tasks[sid];
    if (!task || task.reportedAt) return;
    const progress = String((d && d.progress) || '').trim();
    await this._emit(sid, '> ⚠️ Argus 研究任务失败：' + (progress || 'Argus 侧报错') +
      '（session_id: `' + sid + '`）。可以重新发起一次研究。');
  },

  /** 追加一条助手消息（报告正文 / 提示说明），并写库
   * @param {string} sid - session_id
   * @param {string} content - Markdown 正文
   * @param {Object} [opts] - {notice:'timeout'|'offline'|'error'}
   *   notice 非空表示这是"跟踪没走完"的提示消息而不是报告：打 argusNotice 标记
   *   （既不占用"报告已展示"的判定，见 _alreadyShown；也用于同原因去重），
   *   且**不设 reportedAt / 不自动收尾** —— 收尾要带原因（弹对应通知），见 _stopNotice。
   * @returns {Promise<void>} */
  async _emit(sid, content, opts) {
    const o = opts || {};
    const task = this._tasks[sid] || {};
    const convId = task.convId ||
      ((typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null);
    if (!convId) { this._stop(sid, 'no-conv'); return; }
    const active = !!(typeof AppState !== 'undefined' && AppState.activeConv && AppState.activeConv.id === convId);
    const prevMsg = active && Array.isArray(AppState.messages) && AppState.messages.length
      ? AppState.messages[AppState.messages.length - 1] : null;
    const msg = {
      id: uid('msg'), conversationId: convId, role: 'assistant', content,
      model: this.LABEL, createdAt: Date.now(), tokens: 0, stats: null, feedback: 0,
      citations: [], think: false,
      argusSessionId: sid, argusAuto: true, argusQuestion: task.question || ''
    };
    // [Day24.5] 提示类消息（超时/断网/取报告失败）打标记：不占"报告已展示"的判定，
    // 同原因不重复贴（见 _noticeShown），也不会挡住后续真报告的自动贴入
    if (o.notice) msg.argusNotice = String(o.notice);
    // _convMsgIndex 在启动时由 rebuildMsgIndex 建好；这里兜一下空值，避免异常状态下追加失败
    if (!AppState._convMsgIndex) AppState._convMsgIndex = {};
    (AppState._convMsgIndex[convId] = AppState._convMsgIndex[convId] || []).push(msg);
    if (active) AppState.messages.push(msg);
    try {
      if (typeof persistMessage === 'function') await persistMessage(msg);
    } catch (e) { console.warn('[MoRay] Argus 报告落库失败:', e); }
    if (active) this._paint(msg, prevMsg);
    else if (typeof showNotification === 'function') {
      const conv = ((AppState.conversations || []).find(c => c.id === convId)) || null;
      showNotification(o.notice ? 'Argus 研究跟踪已停止' : 'Argus 研究报告已生成',
        '已追加到会话「' + ((conv && conv.title) || '未命名') + '」',
        o.notice ? 'warning' : 'info', 5000);
    }
    if (typeof renderSessionList === 'function') renderSessionList();
    if (typeof updateTokenMeter === 'function') updateTokenMeter();
    if (o.notice) { this._panelPaint(); return; } // 提示消息不设 reportedAt、不在此收尾
    task.reportedAt = Date.now();
    this._stop(sid, 'done');
  },

  /** 把消息节点追加到消息区（复用既有渲染：buildMessageEl → renderMarkdown）
   * @param {Object} msg - 消息对象
   * @param {Object|null} prevMsg - 追加前的最后一条消息（用于时间分隔线）
   * @returns {void} */
  _paint(msg, prevMsg) {
    const box = document.getElementById('chatMessagesNormal');
    if (!box) { if (typeof renderMessages === 'function') renderMessages(); return; }
    const nearBottom = this._nearBottom(box);
    box.classList.remove('hidden');
    if (typeof removeWelcomeView === 'function') removeWelcomeView();
    // 与既有渲染一致：与上一条消息间隔 >5 分钟才画时间分隔线
    if (prevMsg && msg.createdAt - (prevMsg.createdAt || 0) > 5 * 60 * 1000) {
      const sep = document.createElement('div');
      sep.className = 'time-separator';
      sep.innerHTML = '<span>' + new Date(msg.createdAt).toLocaleString('zh-CN',
        { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) + '</span>';
      box.appendChild(sep);
    }
    box.appendChild(buildMessageEl(msg, true));
    refreshIcons();
    if (MoraySettings.get('autoScroll') && nearBottom) scrollToMsgBottom();
  },

  /** 追加前判断用户是否贴着底部（贴底才自动滚动，翻旧消息时不打扰）
   * @param {HTMLElement} box - 消息容器
   * @returns {boolean} */
  _nearBottom(box) {
    try {
      const sp = box.parentElement;
      if (!sp) return true;
      return (sp.scrollHeight - sp.scrollTop - sp.clientHeight) < 160;
    } catch (e) {
      return true;
    }
  },

  /** 结束跟踪并清掉定时器
   * @param {string} sid - session_id
   * @param {string} reason - done | timeout | offline | error | no-conv
   * @param {string} [detail] - 失败原因（写日志）
   * @returns {void} */
  _stop(sid, reason, detail) {
    if (this._timers[sid]) { clearTimeout(this._timers[sid]); delete this._timers[sid]; }
    const task = this._tasks[sid];
    delete this._tasks[sid];
    this._persist();
    this._panelPaint(); // [Day24.5] 收尾即收起/改画面板（还有别的任务在跟踪就继续显示）
    if (reason === 'done') return;
    console.warn('[MoRay] Argus 跟踪结束(' + reason + ') session=' + sid, detail || '');
    // 中途放弃必须给用户一个交代：否则"等报告"这件事悄无声息地没了
    if (typeof showNotification === 'function') {
      if (reason === 'timeout') {
        showNotification('Argus 研究仍在进行', '已跟踪 ' + Math.round(this.MAX_WAIT_MS / 60000) +
          ' 分钟仍未出报告，先停止自动取报告；稍后问我"进度如何"即可。', 'info', 7000);
      } else if (reason === 'offline') {
        showNotification('Argus 跟踪中断', '连不上本地后端，已停止自动取报告：' +
          String(detail || '').slice(0, 60), 'warning', 7000);
      } else if (reason === 'error') {
        showNotification('Argus 报告拉取失败', '研究已完成但报告读取失败：' +
          String(detail || '').slice(0, 60), 'warning', 7000);
      }
    }
  },

  /** [Day24.5] 中途放弃要**留痕**：先在会话里追加一条说明消息，再按原因收尾。
   *
   * 为什么不能只弹通知：用户此刻可能切到了别的会话 / 别的页面，7 秒的通知他看不到，
   * 切回来又什么都没有——"报告会自己回来"这件事就悄无声息地没了（验收第 4 条要的
   * 正是这个交代）。同原因的提示只贴一次（反复重试/反复问进度不刷屏）。
   * @param {string} sid - session_id
   * @param {string} reason - timeout | offline | error
   * @param {string} [detail] - 失败原因（进正文与日志）
   * @returns {Promise<void>} */
  async _stopNotice(sid, reason, detail) {
    const task = this._tasks[sid];
    if (!task) return;
    const why = detail ? '（' + String(detail).slice(0, 80) + '）' : '';
    const tail = '（session_id: `' + sid + '`）。稍后问「进度如何」可继续查询，' +
      '报告就绪时仍会自动回到本对话。';
    const body = reason === 'timeout'
      ? '> ⏱️ Argus 研究已跟踪 ' + Math.round(this.MAX_WAIT_MS / 60000) +
        ' 分钟仍未出报告，已停止自动取报告（研究本身可能仍在 Argus 后台跑）。' + tail
      : reason === 'offline'
        ? '> ⚠️ 连续 ' + this.MAX_FAILS + ' 次查不到研究状态，已停止自动取报告' + why + '。' + tail
        : '> ⚠️ 研究已完成，但报告读取连续失败' + why + '。' + tail;
    if (this._noticeShown(sid, reason, task.convId)) {
      // 同原因提示已经贴过（后端反复断、用户反复问）→ 不刷屏，但收尾与通知照旧
      this._stop(sid, reason, detail);
      return;
    }
    await this._emit(sid, body, { notice: reason });
    this._stop(sid, reason, detail); // _emit 的提示分支不会收尾，这里带上原因收尾
  },

  /** 持久化在跟踪的任务（刷新后续跑；隐私模式下不写盘）
   * @returns {void} */
  _persist() {
    try {
      if (typeof isPrivacyMode === 'function' && isPrivacyMode()) return;
      const rows = Object.keys(this._tasks).map(sid => Object.assign({ sid }, this._tasks[sid]));
      localStorage.setItem(this.STORE_KEY, JSON.stringify(rows));
    } catch (e) { /* 存储不可用不影响本次会话内跟踪 */ }
  },

  /** 启动时接续未完成的任务（刷新/重开页面后报告照样会自动出现）
   * @returns {number} 接续的任务数 */
  resumePending() {
    let rows = [];
    try { rows = JSON.parse(localStorage.getItem(this.STORE_KEY) || '[]') || []; } catch (e) { rows = []; }
    if (!Array.isArray(rows)) return 0;
    const now = Date.now();
    let n = 0;
    rows.forEach(r => {
      const sid = r && r.sid ? String(r.sid) : '';
      if (!sid || !/^[A-Za-z0-9_-]{6,}$/.test(sid)) return;
      if (r.reportedAt) return;
      if (now - (Number(r.startedAt) || 0) > this.MAX_WAIT_MS) return; // 超期不再续跑
      if (this.track(sid, {
        convId: r.convId, question: r.question, startedAt: r.startedAt,
        progress: r.progress, eventCount: r.eventCount
      })) n++;
    });
    this._persist(); // 回写：过期的条目就此清掉
    if (n) console.log('[MoRay] Argus 续跑 ' + n + ' 个未完成研究任务');
    return n;
  },

  /* ---------- [Day24.5] 进度面板：确定性轮询的可见层 ----------
     位置：#chat-normal 内、输入台 wrapper 之前（JS 动态插入，不进消息流）。
     为什么不进消息流：① 进度是过程信息，不该落库成消息；② 消息区会被
     renderMessages 整表重绘，注入的节点随时被抹掉；③ 终态才由 _emit 贴报告。
     每次轮询结束调用一次 _panelPaint()，它自己处理"该显示谁/该不该收起"。 */

  /** 面板宿主：对话页 + 输入台的插入锚点
   * @returns {{chat:HTMLElement,dockWrap:(HTMLElement|null)}|null} */
  _panelHost() {
    const chat = document.getElementById('chat-normal') || document.getElementById('page-chat');
    if (!chat) return null;
    const dock = document.getElementById('coreInputContainer');
    const dockWrap = (dock && dock.parentElement && dock.parentElement.parentElement === chat)
      ? dock.parentElement : null;
    return { chat, dockWrap };
  },

  /** 当前会话该显示哪条跟踪任务（取最近启动的一条；没有则 null）
   * 只说"当前会话"：跨会话的进度不该出现在别人的对话页上（那种情况走通知）。
   * @returns {string} session_id；无则空串 */
  _panelPick() {
    try {
      const activeId = (typeof AppState !== 'undefined' && AppState.activeConv) ? AppState.activeConv.id : null;
      let best = '', bestAt = -1;
      Object.keys(this._tasks).forEach(sid => {
        const t = this._tasks[sid];
        if (!t || t.reportedAt) return;
        if (activeId && t.convId && t.convId !== activeId) return;
        const at = Number(t.startedAt) || 0;
        if (at > bestAt) { bestAt = at; best = sid; }
      });
      return best;
    } catch (e) {
      return '';
    }
  },

  /** 建面板骨架（幂等；已存在且还在文档里就直接复用）
   * @returns {HTMLElement|null} */
  _panelEnsure() {
    let el = document.getElementById(this.PANEL_ID);
    if (el && el.isConnected) return el;
    const host = this._panelHost();
    if (!host) return null;
    el = document.createElement('div');
    el.id = this.PANEL_ID;
    el.className = 'argus-progress';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML = [
      '<div class="argus-progress-head">',
      '<span class="argus-progress-icon"><i data-lucide="radar" class="w-3.5 h-3.5"></i></span>',
      '<span class="argus-progress-title">Argus 研究进行中</span>',
      '<span class="argus-progress-question" data-argus-q></span>',
      '<span class="argus-progress-more" data-argus-more></span>',
      '<span class="argus-progress-meta" data-argus-meta></span>',
      '</div>',
      '<div class="argus-progress-text" data-argus-text>已启动，正在获取进度…</div>',
      '<div class="argus-progress-bar"><span class="argus-progress-bar-fill"></span></div>'
    ].join('');
    if (host.dockWrap) host.chat.insertBefore(el, host.dockWrap);
    else host.chat.appendChild(el);
    // 图标只在这里换一次（refreshIcons 会把 <i> 换成 <svg>，之后所有更新都改文本节点）
    if (typeof refreshIcons === 'function') refreshIcons();
    return el;
  },

  /** 收起面板（连同秒表；轮询不受影响，报告该来还是会来）
   * @returns {void} */
  _panelClear() {
    if (this._panelTimer) { clearInterval(this._panelTimer); this._panelTimer = 0; }
    this._panelSid = '';
    const el = document.getElementById(this.PANEL_ID);
    if (el && el.parentElement) el.parentElement.removeChild(el);
  },

  /** 按当前跟踪状态重绘面板（无任务/不在本会话 → 收起）
   * @returns {void} */
  _panelPaint() {
    try {
      const sid = this._panelPick();
      if (!sid) { this._panelClear(); return; }
      const task = this._tasks[sid] || {};
      const box = this._panelEnsure();
      if (!box) return;
      this._panelSid = sid;
      const q = box.querySelector('[data-argus-q]');
      if (q) {
        const full = String(task.question || '研究任务');
        q.textContent = full.length > 42 ? full.slice(0, 42) + '…' : full;
        q.title = full;
      }
      const txt = box.querySelector('[data-argus-text]');
      if (txt) {
        const line = String(task.progress || '已启动，正在获取进度…');
        if (txt.textContent !== line) txt.textContent = line;
      }
      const more = box.querySelector('[data-argus-more]');
      if (more) {
        const n = Object.keys(this._tasks).filter(k => {
          const t = this._tasks[k];
          return t && !t.reportedAt && (!task.convId || t.convId === task.convId);
        }).length;
        more.textContent = n > 1 ? '（+' + (n - 1) + ' 条并行）' : '';
      }
      this._panelMeta();
      if (!this._panelTimer) {
        // 秒表：只有面板活着的时候跑，面板一收就自停（零后台开销）
        this._panelTimer = setInterval(() => {
          if (!document.getElementById(this.PANEL_ID)) { clearInterval(this._panelTimer); this._panelTimer = 0; return; }
          this._panelMeta();
        }, 1000);
      }
    } catch (e) { /* 面板失败不影响轮询与报告投递 */ }
  },

  /** 刷新面板右侧元信息（已用时长 + 事件数）
   * @returns {void} */
  _panelMeta() {
    try {
      const sid = this._panelSid;
      const task = sid ? this._tasks[sid] : null;
      if (!task) return;
      const el = document.getElementById(this.PANEL_ID);
      const meta = el ? el.querySelector('[data-argus-meta]') : null;
      if (!meta) return;
      const sec = Math.max(0, Math.floor((Date.now() - (Number(task.startedAt) || Date.now())) / 1000));
      const mm = Math.floor(sec / 60), ss = sec % 60;
      const time = mm + ':' + (ss < 10 ? '0' : '') + ss;
      meta.textContent = time + (task.eventCount ? ' · 事件 ' + task.eventCount : '');
    } catch (e) { /* 忽略 */ }
  }
};

/** [Argus 深度研究] 显式点名 Argus 的请求 → 直接给出研究问题（否则 null）。
 *
 * 为什么需要它：需求 2.1 的闭环是"模型返回 tool_calls → 我们执行"，这对大模型没问题，
 * 但实测本机 7B 级模型（qwen2.5:7b）在"帮我查一下 X"这类请求上经常跳过工具调用、
 * 直接把 System Prompt 里描述的结果（"已启动研究任务，session_id=…"）背出来，
 * 编出一个不存在的 id（3 次里 2 次如此；换个措辞又变成 0/4）——这是模型能力问题，
 * 光靠提示词按不住。所以**只在用户明确点名 Argus 时**由代码直接发起，
 * 其余"调研/查资料"类请求仍走模型自主调用（工具描述里已写明优先调用）。
 *
 * 触发条件（2026-09-21 收紧，见 argusShouldLaunchResearch）：**提到 "Argus" 只是必要条件**，
 * 还要含动作词白名单里的词、且不是"你认识 / 是什么 / 介绍 / 了解"这类问法、也不是在问进度。
 * 此前只判 "argus" 出现过，结果"你认识 Argus 吗"这种闲聊也会起一个研究任务。
 * @param {Array} messages - 本次请求的消息数组
 * @returns {string|null} 研究问题 */
function argusExplicitQuestion(messages) {
  try {
    const lastUser = (() => {
      const list = Array.isArray(messages) ? messages : [];
      for (let i = list.length - 1; i >= 0; i--) {
        const m = list[i];
        if (m && m.role === 'user' && typeof m.content === 'string' && m.content.trim()) return m.content.trim();
      }
      return null;
    })();
    // [Argus 触发收紧] 否决层 + 动作词白名单 + 排除进度询问，三道门都在纯函数里（有单测）。
    // 仅仅出现 "Argus" 不再构成触发理由。
    if (!argusShouldLaunchResearch(lastUser)) return null;
    // 去掉"用 Argus"这类外壳（含常见前置词组合），再剥离开头的请求动词，留下真正的研究问题。
    // 清完太短（≤1 字）就退回原话——宁可问得啰嗦，也不要问一个空问题。
    let s = lastUser.replace(/(?:用|使用|通过|让|叫|请|帮我|麻烦|给我|替我)*\s*argus\s*(?:深度研究|研究|调研)?/gi, ' ');
    for (let i = 0; i < 3; i++) {
      const before = s;
      s = s.replace(/^\s*(?:帮我|请|麻烦|你|去|给我|替我)?\s*(?:查一下|查一查|查查|查|搜一下|搜一搜|搜搜|搜|搜索|调研一下|调研|研究一下|研究|了解一下|了解|看看|看)\s*(?:一下|下|个)?\s*/i, '');
      if (s === before) break;
    }
    s = s.replace(/^[：:，,、\s]+|[：:，,。!！?？\s]+$/g, '')
      .replace(/^(?:一下|下|个)\s*/, '')
      .trim();
    return s.length > 1 ? s : lastUser;
  } catch (e) {
    return null;
  }
}

/* ============================================================
   [Argus 深度研究] 上下文感知兜底：问进度时由代码直接查状态
   要解决的问题：用户拿到 session_id 后紧接着问"进度如何"，qwen2.5:7b 不会去调
   detective_status，只用"研究正在进行中，请稍候"这类废话敷衍（实测）。
   与"显式点名 Argus"那条兜底同一思路：**判断不由模型做**——命中条件由代码判定，
   代码直接调接口，把真实进度作为 role:"tool" 回灌，模型只负责用自然语言转述。
   两个条件必须同时满足：
     1) 最后一条用户消息在问进度/状态/结果（argusAsksProgress）；
     2) 当前对话里有一条"最近碰过、还没出报告"的研究会话（argusPickActiveSession）。
   条件 2 决定这条兜底不会外溢：没有任何研究在跑的对话里，命中率恒为 0。

   本段是**纯函数**（无 DOM、无网络、无全局状态），测试直接从这两个标记之间
   取出来跑：见 argus/tests/test_moray_argus_progress_fallback.py。
   ============================================================ */

/* @argus-progress-fallback:pure:begin */

/** [Argus] 进度询问关键词（判定条件 1）。刻意只收"问这个任务怎么样了"的说法，
 * 不做通用意图识别——"状态/进度"这类词单独出现时的误命中由条件 2 兜住。
 * @type {RegExp} */
const ARGUS_PROGRESS_RE = /进度|进展|状态|怎么样了|怎么样子|怎样了|如何了|好了吗|好了没|好了没有|完了吗|完成了吗|完成了没|完成没有|搞定了吗|出来了吗|出来了没|出来没|结果出来|有结果|结束了吗|还要多久|得多久|多久|跑到哪|到哪了|到哪一步|查到什么|查到了吗|查到了什么|什么情况/;

/** [Argus] 研究会话记录的保鲜期（ms）：超过这个时长就不再认它是"当前在跑的研究"。
 * 取得比 ArgusWatcher.MAX_WAIT_MS（15 分钟）宽三倍，让"跟踪已超时、用户按提示回来
 * 问进度"这条路径也能命中（见 ARGUS_SESSION_LOG 的注释）。 */
const ARGUS_SESSION_TTL_MS = 60 * 60 * 1000;

/** [Argus] 取最后一条有正文的用户消息
 * @param {Array} messages - 消息数组
 * @returns {string} 无则空串 */
function argusLastUserText(messages) {
  const list = Array.isArray(messages) ? messages : [];
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    if (m && m.role === 'user' && typeof m.content === 'string' && m.content.trim()) return m.content.trim();
  }
  return '';
}

/** [Argus] 这条用户消息是不是在问研究进度（判定条件 1）
 * @param {string} text - 用户消息正文
 * @returns {boolean} */
function argusAsksProgress(text) {
  return ARGUS_PROGRESS_RE.test(String(text || ''));
}

/** [Argus 触发] 动作词白名单：只有"Argus + 明确的动作词"才算让它干活。
 * @type {RegExp} */
const ARGUS_ACTION_RE = /查|搜|搜索|研究|找|对比|浏览|调研|进度|状态/;

/** [Argus 触发] 定义/疑问句式：出现这些词就是在**问 Argus 是什么**，不是在给它派活。
 * 注意 ``了解`` / ``介绍`` 也在列——它们不是动作词白名单里的词，出现即不触发。
 * @type {RegExp} */
const ARGUS_VETO_RE = /你认识|认不认识|是什么|什么是|啥是|介绍一下|介绍下|介绍|了解一下|了解下|了解/;

/** [Argus 触发] 存在动作词
 * @param {string} text - 用户消息正文
 * @returns {boolean} */
function argusHasActionWord(text) {
  return ARGUS_ACTION_RE.test(String(text || ''));
}

/** [Argus 触发] 否决函数：这条消息**禁止**触发研究。
 *
 * 为什么单列一层："你认识 Argus 吗"曾是真实误触发（用户只是问一句，系统却起了
 * 一个研究任务）。这个判断刻意放在最前面——只要命中定义/疑问句式就直接否决，
 * 不再看别的条件，免得后面任何一条规则把结论翻回去。
 * @param {string} text - 用户消息正文
 * @returns {boolean} true = 不许触发 */
function shouldNotTriggerArgus(text) {
  const s = String(text || '');
  if (!s || !/argus/i.test(s)) return false;
  return ARGUS_VETO_RE.test(s);
}

/** [Argus 触发] 这条用户消息该不该由代码直接发起研究——三道门同时放行才行：
 *   1) 提到 Argus（否则跟它无关）；
 *   2) 没被 shouldNotTriggerArgus 否决（"是什么 / 你认识 / 介绍 / 了解"这类问法）；
 *   3) 含动作词白名单里的词（查 / 搜索 / 研究 / 找 / 对比 / 浏览 / 调研 / 进度 / 状态）；
 *   4) 不是在问进度——问进度交给状态兜底，起新研究只会多出一堆重复任务。
 * **仅仅出现 "Argus" 不再构成任何触发理由**（这就是本条修正要解决的问题）。
 * @param {string} text - 最后一条用户消息
 * @returns {boolean} true = 由代码发起研究 */
function argusShouldLaunchResearch(text) {
  const s = String(text || '');
  if (!/argus/i.test(s)) return false;
  if (shouldNotTriggerArgus(s)) return false;
  if (!argusHasActionWord(s)) return false;
  if (argusAsksProgress(s)) return false;
  return true;
}

/** [Argus] 挑出"当前对话最近一次在跑的研究"（判定条件 2）。
 * 记录形状 {sid|session_id, convId, touchedAt|startedAt}；归属会话不符、无时间戳、
 * 或超过保鲜期的一律不算——宁可漏触发，也不要跨会话串进度、或把一小时前的老任务
 * 当成"刚才那条"。
 * @param {Object|Array} records - 记录表（对象按 sid 索引，或记录数组）
 * @param {string|null} convId - 当前会话 id
 * @param {number} now - 当前时间戳（ms）
 * @returns {string|null} session_id；不满足返回 null */
function argusPickActiveSession(records, convId, now) {
  try {
    if (!convId) return null;
    const rows = Array.isArray(records)
      ? records
      : Object.keys(records || {}).map(k => records[k]);
    const t = Number(now) || Date.now();
    let best = null, bestAt = -1;
    rows.forEach(r => {
      if (!r || typeof r !== 'object') return;
      const sid = String(r.sid || r.session_id || '').trim();
      if (!/^[A-Za-z0-9_-]{6,}$/.test(sid)) return;
      if (String(r.convId || '') !== String(convId)) return;
      const at = Number(r.touchedAt || r.startedAt) || 0;
      if (!at || (t - at) > ARGUS_SESSION_TTL_MS) return;
      if (at > bestAt) { bestAt = at; best = sid; }
    });
    return best;
  } catch (e) {
    return null;
  }
}

/** [Argus] 上下文感知兜底的判定入口：两个条件同时满足才返回 session_id。
 * 这是"要不要由代码替模型查一次状态"的唯一判据，Gateway.runWithTools 照此执行。
 * @param {Array} messages - 本次请求的消息数组
 * @param {Object|Array} records - 已知的研究会话记录（argusSessionRecords() 的产物）
 * @param {string|null} convId - 当前会话 id
 * @param {number} [now] - 当前时间戳（缺省取 Date.now()）
 * @returns {string|null} 要查的 session_id；不该兜底返回 null */
function argusProgressFallbackSession(messages, records, convId, now) {
  try {
    if (!argusAsksProgress(argusLastUserText(messages))) return null;
    return argusPickActiveSession(records, convId, Number(now) || Date.now());
  } catch (e) {
    return null;
  }
}

/* @argus-progress-fallback:pure:end */

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
    '- 【工具调用强制规则】\n1. 指令中出现「调用xx工具」「使用xx工具」的明确格式时，直接调用对应工具执行任务，禁止触发搜索、联网等其他工具，无需额外推理。\n2. 指令包含本地磁盘路径（C:\\、D:\\ 等）+ 代码文件后缀（.py/.js/.go 等）+ 运行/执行/启动/跑 等关键词时，优先调用 zcode_run 工具执行代码，禁止调用搜索工具。\n3. 代码修改、bug修复、功能开发类需求，优先匹配 file + zcode 系列工具，不触发通用搜索。',
    '# 本机 Agent 工作规范（受控工作区内操作）',
    '- 工具与时机：list_directory 列目录；read_file 读文本文件；find_files 按文件名通配查找（不确定路径时先用它定位）；search_text 全文搜索（按内容找，支持正则）；create_file 新建文件（已存在即拒绝，绝不覆盖）；write_file 新建或整体覆盖文件；edit_file 精确替换已有文件的某一段（修改已有文件优先用 edit_file，不要整体重写）；rename_file 重命名；move_file 移动；list_processes 看进程占用；system_info 看系统与磁盘概览；run_command 只读白名单命令（git status/log/diff/branch、python/node/npm --version、where）。',
    '- 工作区边界：所有 path 都必须是相对工作区根的路径；默认无法访问工作区以外的文件；禁止臆造不存在的路径——不确定就先用 find_files / list_directory 确认。例外：授权目录（D:\\MoRay\\config\\authorized_paths.json 内的 C:/D: 盘目录）允许按真实路径直接读写，不受工作区根限制。',
    '- 多步任务：需要 2 个以上步骤时，先调用 submit_plan 提交编号计划（每步含意图与预计工具），然后按计划逐步执行；某步失败就修正参数重试（最多 2 次），全部完成后用一段话总结结果。',
    '- 授权与拒绝：create_file / write_file / edit_file / rename_file / move_file / run_command 会先请求用户授权；被拒绝时不要重复尝试相同操作，改用文字向用户说明。',
    '- 文件内容为 UTF-8 文本；二进制或非 UTF-8 文件会收到明确错误，不要反复尝试。',
    '- edit_file 的 old_str 必须与文件内容逐字符一致且唯一；先 read_file 再编辑，匹配不唯一时补充上下文或用 replace_all。',
    '- 【本地代码开发模式】支持读写用户授权的本地目录（C/D 盘均可），所有文件操作优先使用真实路径；执行文件操作前默认路径已通过授权校验，直接调用 file 工具即可；若返回"路径未授权"错误，告知用户将目录添加到 D:\\MoRay\\config\\authorized_paths.json 配置中；代码运行、文件修改严格基于用户真实目录结构，操作完成后自动同步回原路径。',
    '- 【全栈代码开发默认规则 · 全能力版】\n1. 核心执行原则：用户给出本地文件路径+开发/修改/排查/运行需求后，自动多步执行直到任务闭环，中途不得停止。标准执行链路：读取目标文件 → 分析需求 → 增量修改 → 写回原文件 → 自动调用执行引擎验证 → 提取运行结果/预览资源 → 输出最终结论。全程无需用户额外指令，不向用户输出工具调用步骤、底层路径映射、中间调试信息。\n2. 文件读写规则：直接使用用户提供的原始磁盘路径调用 file 工具，不得擅自修改、拼接路径。修改前必须先读取文件确认内容与结构，仅修改需求涉及的代码片段，禁止全量覆盖无关内容。修改完成后自动调用 file 工具将变更写回原文件，无需用户确认保存。涉及多文件操作时，按依赖顺序逐个处理，全部完成后统一汇总。\n3. ZCode 执行引擎调度规则：代码修改完成后，若涉及可运行逻辑，自动调用 zcode 工具执行对应脚本验证。运行成功：提取核心输出结果；运行报错：返回完整堆栈，自动排查修复，最多迭代修复 3 轮。自动识别输出中的服务端口、生成图片、静态页面、导出文件，提取对应的预览链接或文件路径一并返回。涉及依赖缺失的报错，自动确认后安装对应依赖，重新运行验证。\n4. 子智能体调度规则：单文件修复、单功能开发、独立bug排查类任务，直接派发 ZCode 子智能体自主完成全流程。跨文件重构、架构级改动、多模块联动类复杂任务，拆解为独立子任务后，逐个派发子智能体执行，统一校验汇总。子智能体严格限定在指定工作目录内执行，不得越界操作。\n5. 输出规范：最终仅输出四类核心信息：修改点说明、运行结果、预览链接/生成文件、最终结论。不输出内部步骤编号、工具调用过程、底层实现细节。报错场景同步输出根因分析与修复方案，不堆砌无效日志。'
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
      return '<div class="plan-step-row flex items-center gap-2 py-1 px-1' + (s.status === 'running' ? ' running' : '') + '">' +
        '<span class="font-mono text-[10px] text-text-tertiary w-4">' + (i + 1) + '.</span>' +
        '<i data-lucide="' + icon + '" class="w-3 h-3 ' + color + (s.status === 'running' ? ' animate-spin' : '') + '"></i>' +
        '<span class="text-[11px] ' + color + (pulse ? ' ' + pulse : '') + ' shrink-0">' + label + '</span>' +
        '<span class="text-[11px] text-text-primary truncate flex-1">' + escapeHtml(s.title) + '</span>' +
        (s.tool ? '<span class="font-mono text-[10px] text-text-tertiary shrink-0">' + escapeHtml(s.tool) + '</span>' : '') +
        '</div>';
    }).join('');
    const doneN = this.current.steps.filter(s => s.status === 'done').length;
    const html = '<div class="plan-card rounded-lg bg-surface-panel/60 border border-brand-violet/25 px-3 py-2 my-2" data-plan-card>' +
      '<div class="plan-head flex items-center gap-2 mb-1" data-plan-toggle title="点击折叠/展开计划">' +
      '<i data-lucide="list-checks" class="w-3.5 h-3.5 text-brand-violet"></i>' +
      '<span class="text-[11px] font-medium text-text-primary">任务计划</span>' +
      '<span class="text-[10px] text-text-tertiary ml-auto">' + doneN + '/' + this.current.steps.length + ' 完成</span>' +
      '<i data-lucide="chevron-down" class="w-3 h-3 text-text-tertiary"></i></div>' +
      '<div class="plan-steps space-y-1">' + rows + '</div></div>';
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
        return '<div class="flex items-center gap-2 py-1">' +
          '<span class="font-mono text-[10px] text-text-tertiary w-4">' + (i + 1) + '.</span>' +
          '<span class="text-[11px] ' + color + '">' + (stMap[s.status] || s.status || '待办') + '</span>' +
          '<span class="text-[11px] text-text-primary truncate flex-1">' + escapeHtml(s.title || '') + '</span>' +
          (s.tool ? '<span class="font-mono text-[10px] text-text-tertiary shrink-0">' + escapeHtml(s.tool) + '</span>' : '') +
          '</div>';
      }).join('');
      const doneN = plan.steps.filter(s => s.status === 'done').length;
      html = '<div class="plan-card rounded-lg bg-surface-panel/60 border border-brand-violet/25 px-3 py-2 my-2" data-plan-card>' +
        '<div class="flex items-center gap-2 mb-1"><i data-lucide="list-checks" class="w-3.5 h-3.5 text-brand-violet"></i>' +
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
    '<div class="flex items-center gap-2 mb-2">' +
    '<i data-lucide="folder-tree" class="w-3.5 h-3.5 text-brand-violet"></i>' +
    '<span class="text-[11px] font-medium text-text-primary">工作区</span>' +
    '<span class="text-[10px] text-text-tertiary">只读</span>' +
    '<button data-ws-sidebar-refresh class="ml-auto text-text-tertiary hover:text-brand-cyan" title="刷新" aria-label="刷新文件树"><i data-lucide="refresh-cw" class="w-3 h-3"></i></button>' +
    '<button data-ws-sidebar-collapse class="text-text-tertiary hover:text-brand-cyan" title="收起侧栏（可从顶部提示条再打开）" aria-label="收起侧栏"><i data-lucide="chevrons-right" class="w-3.5 h-3.5"></i></button></div>' +
    '<div class="ws-tree-list rounded-lg border border-line-ghost/50 p-2 flex-1 overflow-auto text-[11px]"></div>' +
    '<div class="ws-tree-preview hidden rounded-lg border border-line-ghost/50 p-2 bg-surface-panel/40 mt-2 max-h-48 overflow-auto text-[10px]"></div>';
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
    st.textContent = '#wsSidebar{position:fixed;top:74px;right:14px;bottom:158px;width:292px;z-index:26;display:flex;flex-direction:column;padding:12px;border-radius:12px;background:rgba(15,18,28,.94);border:1px solid rgba(120,140,180,.25);box-shadow:0 8px 28px rgba(0,0,0,.4)}' +
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
    html += '<div class="ws-tree-row flex items-center gap-2 py-1 rounded px-1 hover:bg-surface-panel cursor-pointer' + hl + '" style="padding-left:' + (depth * 14 + 4) + 'px" data-ws-path="' + escapeHtml(rel) + '" data-ws-type="' + it.type + '" title="' + escapeHtml(rel) + '">' +
      (isDir ? '<i data-lucide="chevron-right" class="ws-caret w-2.5 h-2.5 text-text-tertiary' + (expanded ? ' open' : '') + '"></i>' : '<span class="w-2.5"></span>') +
      '<i data-lucide="' + typeIcon + '" class="w-3.5 h-3.5 ' + (isDir ? 'text-brand-cobalt' : 'text-text-tertiary') + '"></i>' +
      '<span class="text-text-primary truncate">' + escapeHtml(it.name) + '</span>' +
      '<span class="text-[10px] text-text-tertiary shrink-0">' + sizeText + '</span></div>';
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
    if (!keep[k]) { skip++; if (skip === 1) html += '<div class="text-[10px] text-text-tertiary px-1">⋯</div>'; return; }
    skip = 0;
    const cls = o.t === '-' ? 'text-danger bg-danger/10' : o.t === '+' ? 'text-success bg-success/10' : 'text-text-tertiary';
    html += '<div class="font-mono text-[10px] whitespace-pre-wrap break-all px-1 ' + cls + '">' + escapeHtml((o.t === ' ' ? '  ' : o.t) + o.line) + '</div>';
  });
  if (!html) html = '<div class="text-[10px] text-text-tertiary px-1">（内容无差异）</div>';
  return '<div class="rounded border border-line-ghost/50 bg-surface-panel/40 p-2 max-h-44 overflow-auto">' + html + '</div>';
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
  // [2026-10-01 U-7-1] 首推模型从 qwen2.5:7b 改为 qwen3.5:9b（更强）；qwen2.5:7b 降为末位兜底
  const wish = ['qwen3.5:9b', 'qwen3.5:4b', 'qwen2.5:7b'];
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
    // [2026-10-01 U-7-1] 用户手选过模型后，不再自动覆盖
    if (typeof AppState !== 'undefined' && AppState.activeConv && AppState.activeConv.userPickedModel === true) return;
    const sc = MoraySettings.get('agentSelfCheck');
    const cur = (typeof activeModelName === 'function') ? activeModelName() : MoraySettings.get('defaultModel');
    if (sc && sc.ok === true && sc.model === cur) return; // 自检通过 → 维持原默认
    const rec = agentRecommendedModel();
    if (!rec || cur === rec) return;
    MoraySettings.set('defaultModel', rec).then(() => {
      try {
        if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
        if (typeof updateStatusBar === 'function') updateStatusBar();
        showNotification('Agent 默认模型已切换', rec + '（Agent 推荐模型）；仍可在输入台手动切换', 'info', 4500);
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
