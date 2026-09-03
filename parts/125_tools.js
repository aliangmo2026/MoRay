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

  /** 某工具当前是否启用（总开关 + 独立开关）
   * @param {string} name - 工具名 */
  isEnabled(name) {
    const t = this.tools[name];
    if (!t) return false;
    if (!MoraySettings.get('toolsEnabled')) return false;
    const disabled = MoraySettings.get('toolsDisabled') || [];
    return !disabled.includes(name);
  },

  /** 请求体可用的协议描述（关闭总开关时返回空数组 → 请求体绝无 tools 字段）
   * @returns {Array<{type:string, function:Object}>} */
  listForRequest() {
    if (!MoraySettings.get('toolsEnabled')) return [];
    const disabled = MoraySettings.get('toolsDisabled') || [];
    return Object.values(this.tools)
      .filter(t => !disabled.includes(t.name))
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
    const timer = setTimeout(() => {
      timedOut = true;
      if (!internal.signal.aborted) internal.abort(); // 协作式取消底层工具
    }, 5000);
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
      if (internal.signal.aborted && timedOut) return { ok: false, error: '工具执行超时（5s）', ms: Math.round(performance.now() - started), timeout: true };
      if (e && e.name === 'AbortError') throw e;
      return { ok: false, error: String((e && e.message) || e).slice(0, 200), ms: Math.round(performance.now() - started) };
    } finally {
      // [修复] 所有结束路径清理定时器与外部监听，杜绝泄漏与延迟异常
      clearTimeout(timer);
      if (ctx && ctx.signal) ctx.signal.removeEventListener('abort', onOuterAbort);
    }
  },

  /** 单步事件 → 步骤卡 HTML（进行中脉冲 / 成功 / 失败 + 可折叠结果预览）
   * @param {Object} evt - {index, name, label, args, status, resultPreview, ms}
   * @returns {string} HTML */
  stepHtml(evt) {
    const iconMap = {
      get_current_time: 'clock', calculator: 'calculator', query_knowledge_base: 'library',
      save_snippet: 'save', run_workflow: 'workflow', get_cost_usage: 'wallet'
    };
    const icon = iconMap[evt.name] || 'wrench';
    const label = evt.label || evt.name;
    let argText = '';
    try { argText = (typeof evt.args === 'string' ? evt.args : JSON.stringify(evt.args || {})).slice(0, 60); } catch (e) { argText = ''; }
    const msText = evt.ms != null ? (evt.ms >= 1000 ? (evt.ms / 1000).toFixed(1) + 's' : Math.round(evt.ms) + 'ms') : '';
    let badge, color;
    if (evt.status === 'running') { badge = '<span class="animate-pulse text-[10px] text-brand-cyan">执行中…</span>'; color = 'text-brand-cyan'; }
    else if (evt.status === 'error') { badge = '<span class="text-[10px] text-danger">失败</span>'; color = 'text-danger'; }
    else { badge = '<span class="text-[10px] text-success">成功</span>'; color = 'text-success'; }
    const preview = evt.resultPreview != null ? String(evt.resultPreview).slice(0, 300) : '';
    // 完整结果（复制用，不截断；预览仍截断展示）
    const full = evt.resultFull != null ? String(evt.resultFull) : preview;
    const open = evt.status === 'running' ? ' open' : '';
    return '<details class="tool-step rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2" data-tool-step="' + evt.index + '"' + open + '>' +
      '<summary class="flex items-center gap-2 cursor-pointer list-none text-[11px] select-none">' +
      '<i data-lucide="' + icon + '" class="w-3.5 h-3.5 ' + color + '"></i>' +
      '<span class="text-text-primary font-medium">' + escapeHtml(label) + '</span>' +
      (argText ? '<span class="text-text-tertiary truncate">' + escapeHtml(argText) + '</span>' : '') +
      (msText ? '<span class="text-text-tertiary">' + msText + '</span>' : '') + badge +
      (full ? '<button class="tool-step-copy ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-text-tertiary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" data-tool-copy title="复制完整结果" data-full="' + escapeHtml(full) + '"><i data-lucide="copy" class="w-3 h-3"></i>复制</button>' : '') +
      '</summary>' +
      (preview ? '<div class="text-[10px] text-text-tertiary mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap break-all">' + escapeHtml(preview) + '</div>' : '') +
      '</details>';
  },

  /** 消息持久化 steps → 静态步骤卡 HTML（重开会话渲染用）
   * @param {Array} toolCalls - [{name,label,args,status,resultPreview,ms}]
   * @returns {string} HTML */
  renderSteps(toolCalls) {
    if (!toolCalls || !toolCalls.length) return '';
    return '<div class="tool-steps space-y-1.5 my-2">' + toolCalls.map((s, i) => this.stepHtml(Object.assign({}, s, { index: i }))).join('') + '</div>';
  },

  /** 实时步骤卡：事件驱动（首个事件建容器于最终回复之前，后续按 index 原位更新）
   * 同时管理"工具执行中"发送按钮状态（running 进入 / 终态退出）
   * @param {HTMLElement} bubbleEl - 消息气泡
   * @param {Object} evt - 步骤事件 */
  renderLiveStep(bubbleEl, evt) {
    if (!bubbleEl) return;
    if (evt.status === 'running') this.enterToolBusy();
    else this.exitToolBusy();
    let wrap = bubbleEl.querySelector('.tool-steps');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'tool-steps space-y-1.5 my-2';
      const inner = bubbleEl.querySelector('.ai-msg-inner');
      if (inner) inner.parentElement.insertBefore(wrap, inner); // 最终回复之前
      else bubbleEl.appendChild(wrap);
    }
    const old = wrap.querySelector('[data-tool-step="' + evt.index + '"]');
    if (old) old.outerHTML = this.stepHtml(evt);
    else wrap.insertAdjacentHTML('beforeend', this.stepHtml(evt));
    refreshIcons();
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

  /** 步骤结果复制：事件委托（复制完整结果而非预览截断）
   * @returns {void} */
  _installCopyHandler() {
    if (this.__copyBound) return;
    this.__copyBound = true;
    document.addEventListener('click', (e) => {
      const b = e.target.closest('[data-tool-copy]');
      if (!b) return;
      e.stopPropagation(); // 防止折叠 details
      const full = b.getAttribute('data-full') || '';
      const done = (ok) => showNotification(ok ? '已复制工具结果' : '复制失败', ok ? '完整结果已复制到剪贴板' : '', ok ? 'success' : 'error', 1600);
      if (typeof copyToClipboard === 'function') copyToClipboard(full).then(done, () => done(false));
      else if (navigator.clipboard) navigator.clipboard.writeText(full).then(() => done(true), () => done(false));
    });
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
  const toolRows = Object.values(ToolRegistry.tools).map(t => {
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
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">最大工具轮数</span><input type="number" min="1" max="10" value="' + (MoraySettings.get('toolsMaxRounds') || 3) + '" id="setToolsMaxRounds" class="form-input" style="width:64px;padding:2px 6px;font-size:11px"></div>',
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
