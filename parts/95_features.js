/* ============================================================
   MoRay 夜间优化 · 第四阶段功能增强
   任务4.1 多模态图片问答 / 语音输入 / 对话场景模板
   任务4.2 提示词市场 / 版本历史
   任务4.3 JS代码运行器 / Prettier格式化 / 分享链接
   任务4.4 混合检索（向量+关键词）
   任务4.5 工作流新模板 / 定时调度 / 完成通知
   任务4.6 参数预设
   ============================================================ */

/* ===================== [4.1] 多模态：图片附件 ===================== */

/** 对话图片附件管理 */
const ChatAttachments = {
  /**
   * 添加图片（压缩后入列）
   * @param {File|Blob} file - 图片文件
   * @returns {Promise<void>} */
  async add(file) {
    if (!file.type.startsWith('image/')) { showNotification('仅支持图片', '对话中可附加图片文件', 'warning', 2000); return; }
    if (file.size > 20 * 1024 * 1024) { showNotification('图片过大', '请选择 20MB 以内的图片', 'error', 2500); return; }
    try {
      const dataUrl = await compressImageFile(file, 1280, 0.8);
      AppState.attachments.push({ name: file.name || '粘贴图片.png', dataUrl });
      this.renderChips();
      showNotification('图片已附加', file.name || '粘贴图片', 'success', 1500);
    } catch (e) { showNotification('图片处理失败', e.message, 'error', 2500); }
  },

  /** 渲染附件 chips
   * @returns {void} */
  renderChips() {
    const container = document.getElementById('coreInputContainer');
    if (!container) return;
    let wrap = document.getElementById('attachChipsWrap');
    if (!AppState.attachments.length) { if (wrap) wrap.remove(); return; }
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'attachChipsWrap';
      wrap.className = 'ref-chips px-4';
      container.insertBefore(wrap, container.querySelector('.flex.items-center.gap-3.px-4'));
    }
    wrap.innerHTML = AppState.attachments.map((a, i) => `
      <span class="ref-chip"><i data-lucide="image" class="w-3 h-3"></i>${escapeHtml(a.name)}
        <span class="ref-chip-x" data-attach-remove="${i}"><i data-lucide="x" class="w-3 h-3"></i></span></span>`).join('');
    refreshIcons();
  },

  /** 清空附件
   * @returns {void} */
  clear() { AppState.attachments = []; this.renderChips(); },

  /** 安装：附件按钮 / 粘贴图片 / 拖拽到输入台
   * @returns {void} */
  install() {
    // [零摆设 P0-3] 附件按钮按稳定 id 接线（原 title="添加附件" 选择器与实际按钮 title 不匹配，等于没绑）
    const bindImageBtn = (btn) => {
      if (!btn || btn.__imgBound) return;
      btn.__imgBound = true;
      btn.onclick = () => {
        const inp = document.createElement('input');
        inp.type = 'file'; inp.accept = 'image/*'; inp.multiple = true;
        inp.onchange = () => Array.from(inp.files || []).forEach(f => this.add(f));
        inp.click();
      };
      btn.removeAttribute('onclick');
    };
    bindImageBtn(document.getElementById('attachImageBtn'));
    bindImageBtn(document.getElementById('attachImageBtn2'));
    // 兼容旧版（无 id 时按 title 回退）
    if (!document.getElementById('attachImageBtn')) {
      const legacy = document.querySelector('.input-toolbar-btn[title="添加图片（多模态）"], .input-toolbar-btn[title="图片"]');
      if (legacy) { legacy.title = '添加图片（多模态）'; bindImageBtn(legacy); }
    }
    // 粘贴图片
    const input = document.getElementById('chatInputNormal');
    if (input) {
      input.addEventListener('paste', (e) => {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (const item of items) {
          if (item.type.startsWith('image/')) {
            e.preventDefault();
            this.add(item.getAsFile());
            return;
          }
        }
      });
    }
    // 附件 chip 移除（委托）
    document.addEventListener('click', (e) => {
      const x = e.target.closest('[data-attach-remove]');
      if (x) { AppState.attachments.splice(parseInt(x.dataset.attachRemove, 10), 1); this.renderChips(); }
    });
    // 拖拽图片到输入台
    const core = document.getElementById('coreInputContainer');
    if (core) {
      core.addEventListener('dragover', (e) => { e.preventDefault(); });
      core.addEventListener('drop', (e) => {
        const imgs = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith('image/'));
        if (imgs.length) { e.preventDefault(); e.stopPropagation(); imgs.forEach(f => this.add(f)); }
      });
    }
  }
};

/* ===================== [4.1] 语音输入（Web Speech API） ===================== */

/** 语音输入管理 */
const VoiceInput = {
  /** @type {any} */
  rec: null,
  /** @type {boolean} */
  active: false,

  /** 是否受支持
   * @returns {boolean} */
  supported() { return 'webkitSpeechRecognition' in window || 'SpeechRecognition' in window; },

  /** 切换听写
   * @returns {void} */
  toggle() {
    if (this.active) { this.stop(); return; }
    if (!this.supported()) {
      showNotification('不支持语音输入', '当前浏览器不支持 Web Speech API（建议 Chrome/Edge）', 'warning', 3000);
      // [零摆设 P0-4] 不支持时按钮禁用并诚实标注
      const btn = document.getElementById('voiceInputBtn');
      if (btn) { btn.disabled = true; btn.style.opacity = '0.4'; btn.title = '当前浏览器不支持语音输入（建议 Chrome/Edge）'; }
      return;
    }
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.rec = new Rec();
    this.rec.lang = 'zh-CN';
    this.rec.interimResults = true;
    this.rec.continuous = false;
    const input = document.getElementById('chatInputNormal');
    const base = input ? input.value : '';
    this.rec.onresult = (e) => {
      let text = '';
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      if (input) { input.value = (base ? base + ' ' : '') + text; if (typeof updateInputStats === 'function') updateInputStats(input.value); }
    };
    this.rec.onend = () => { this.active = false; this._btnState(false); };
    this.rec.onerror = (e) => {
      this.active = false; this._btnState(false);
      showNotification('语音识别失败', e.error === 'not-allowed' ? '麦克风权限被拒绝' : String(e.error), 'error', 3000);
    };
    try { this.rec.start(); this.active = true; this._btnState(true); showNotification('语音输入中', '请对着麦克风说话…', 'info', 1800); }
    catch (e) { showNotification('启动失败', e.message, 'error', 2500); }
  },

  /** 停止听写
   * @returns {void} */
  stop() { if (this.rec) { try { this.rec.stop(); } catch (e) { /* 忽略 */ } } this.active = false; this._btnState(false); },

  /** 麦克风按钮状态
   * @param {boolean} on - 是否录音中
   * @returns {void} */
  _btnState(on) {
    const btn = document.getElementById('voiceInputBtn');
    if (btn) btn.classList.toggle('active', on);
  }
};

/* ===================== [4.1] 对话场景模板 ===================== */

/** 预设对话场景模板 */
const CONVERSATION_TEMPLATES = [
  { id: 'code-review', icon: 'scan-text', name: '代码审查', desc: '逐行审查代码，指出问题并给出改进', systemPrompt: '你是一位严谨的资深代码审查专家。审查代码时从【正确性、安全性、性能、可读性】四个维度分析，每个维度给出具体问题与改进建议，必要时给出修复代码。', welcome: '请把需要审查的代码发给我，我会给出结构化的审查意见。' },
  { id: 'tutor', icon: 'graduation-cap', name: '学习辅导', desc: '费曼式讲解，由浅入深配示例', systemPrompt: '你是一位擅长费曼学习法的导师。讲解概念时先给一句大白话总结，再分层展开，每个概念配一个生活化类比和一个小例子，最后用三个问题检验理解。', welcome: '今天想弄懂什么概念？告诉我主题和你当前的水平即可。' },
  { id: 'writer', icon: 'pen-line', name: '写作助手', desc: '结构化写作与润色', systemPrompt: '你是一位专业的中文写作助手。先确认写作目标与受众，给出大纲后再逐段成文；润色时保持原意，提升表达密度与节奏感。', welcome: '需要写什么？告诉我主题、受众和期望的风格。' },
  { id: 'translator', icon: 'languages', name: '翻译专家', desc: '中英互译，保留术语与格式', systemPrompt: '你是一位专业译者。中译英与英译中时保持术语准确、格式一致；遇到歧义句给出多个译法并说明差异。', welcome: '发送需要翻译的内容，注明目标语言（默认中译英）。' }
];

/** 打开对话模板选择器（新建会话的另一种方式）
 * @returns {void} */
function openConversationTemplates() {
  const box = showModal(`<div class="grid grid-cols-2 gap-3">
    ${CONVERSATION_TEMPLATES.map(t => `
      <div class="doc-card p-4 cursor-pointer" data-tpl-id="${t.id}">
        <div class="flex items-center gap-2 mb-1.5">
          <div class="w-8 h-8 rounded-lg bg-brand-cobalt/12 flex items-center justify-center">
            <i data-lucide="${t.icon}" class="w-4 h-4 text-brand-cobalt"></i>
          </div>
          <span class="text-sm text-text-primary">${t.name}</span>
        </div>
        <div class="text-[11px] text-text-tertiary">${t.desc}</div>
      </div>`).join('')}
  </div>`, { title: '从模板新建对话', icon: 'layout-template', footer: false });
  box.querySelectorAll('[data-tpl-id]').forEach(el => {
    el.addEventListener('click', async () => {
      const tpl = CONVERSATION_TEMPLATES.find(t => t.id === el.dataset.tplId);
      closeModal();
      const conv = await DB.createConversation({ title: tpl.name, model: MoraySettings.get('defaultModel') || '', systemPrompt: tpl.systemPrompt });
      AppState.conversations.unshift(conv);
      AppState._convMsgIndex[conv.id] = [];
      await openConversation(conv.id);
      if (tpl.welcome) {
        const w = await DB.createMessage({ conversationId: conv.id, role: 'assistant', content: tpl.welcome, model: '模板' });
        AppState.messages.push(w);
        AppState._convMsgIndex[conv.id].push(w);
        const bx = document.getElementById('chatMessagesNormal');
        bx.classList.remove('hidden');
        removeWelcomeView();
        bx.appendChild(buildMessageEl(w, true));
        refreshIcons();
      }
      showNotification('模板已应用', tpl.name + '（含专属系统提示词）', 'success', 2200);
    });
  });
}

/* ===================== [4.2] 提示词市场 + 版本历史 ===================== */

/** 本地提示词市场（精选模板，一键导入） */
const PROMPT_MARKET = [
  { title: '五档代码审查', category: '代码审查', tags: ['审查', '质量'], content: '请以五档评分（A/B/C/D/F）审查以下代码：\n1. 正确性与边界\n2. 安全隐患\n3. 性能瓶颈\n4. 可读性与命名\n5. 测试建议\n\n最后给出总分与最关键的3条改进。\n\n代码：\n{{代码}}' },
  { title: '苏格拉底导师', category: '解释', tags: ['学习'], content: '请用苏格拉底式提问帮我理解{{概念}}：不要直接给答案，每次只问一个问题，根据我的回答逐步引导，直到我能自己讲清楚这个概念。' },
  { title: 'SQL 生成器', category: '代码生成', tags: ['SQL'], content: '根据以下表结构和需求生成 SQL，要求：使用参数化占位、附执行计划说明、标注索引建议。\n\n表结构：\n{{表结构}}\n\n需求：\n{{需求}}' },
  { title: '正则表达式专家', category: '代码生成', tags: ['正则'], content: '为以下需求编写正则表达式：给出【正则 + 逐段解释 + 3个匹配示例 + 2个不匹配示例 + 常见误用提醒】。\n\n需求：{{需求}}' },
  { title: 'API 文档撰写', category: '文档', tags: ['API'], content: '为以下接口生成文档：包含功能说明、请求方法与路径、参数表（名称/类型/必填/说明）、请求示例、响应示例、错误码表。\n\n接口信息：\n{{接口}}' },
  { title: '单元测试生成（表驱动）', category: '测试', tags: ['测试'], content: '为以下函数生成表驱动风格的单元测试：覆盖正常值、边界值、异常输入；每个用例附一行意图注释。\n\n函数：\n{{代码}}' },
  { title: '重构建议（小步快跑）', category: '重构', tags: ['重构'], content: '对以下代码提出重构方案，要求按【小步可验证】的顺序给出每一步：改什么、为什么、风险、验证方法。不要求一次到位。\n\n代码：\n{{代码}}' },
  { title: '错误信息翻译官', category: '其他', tags: ['调试'], content: '解释以下错误信息：1) 逐词/逐段解释含义 2) 最可能的3个原因（按概率排序）3) 每个原因的验证命令或步骤 4) 最终修复建议。\n\n错误：\n{{错误}}' },
  { title: '技术方案对比表', category: '其他', tags: ['架构'], content: '对比 {{方案A}} 与 {{方案B}}：从学习成本、生态、性能、运维、社区活跃度五个维度打分（1-5），给出适用场景结论与选择建议。' },
  { title: '周报生成器', category: '其他', tags: ['效率'], content: '把以下工作流水账整理成周报：【本周完成 / 数据亮点 / 问题与风险 / 下周计划】四段，每段用要点列表，量化优先，不超过300字。\n\n流水账：\n{{内容}}' }
];

/** 打开提示词市场
 * @returns {void} */
function openPromptMarket() {
  const cats = Array.from(new Set(PROMPT_MARKET.map(p => p.category)));
  const box = showModal(`
    <div class="flex gap-2 mb-3 flex-wrap" id="marketCats">
      <button class="tag-pill bg-brand-cobalt/15 text-brand-cobalt" data-mcat="全部">全部</button>
      ${cats.map(c => `<button class="tag-pill bg-surface-floating text-text-tertiary" data-mcat="${escapeHtml(c)}">${escapeHtml(c)}</button>`).join('')}
    </div>
    <div class="space-y-2" id="marketList" style="max-height:56vh;overflow-y:auto"></div>`,
    { title: '提示词市场（本地精选）', icon: 'store', wide: true, footer: false });

  const renderList = (cat) => {
    const list = box.querySelector('#marketList');
    const items = PROMPT_MARKET.filter(p => cat === '全部' || p.category === cat);
    list.innerHTML = items.map((p, i) => `
      <div class="doc-card p-3">
        <div class="flex items-center justify-between mb-1">
          <div class="flex items-center gap-2"><span class="text-sm text-text-primary">${escapeHtml(p.title)}</span>
            <span class="tag-pill bg-brand-violet/15 text-brand-violet">${escapeHtml(p.category)}</span></div>
          <button class="btn-primary px-2.5 py-1 rounded-lg text-[11px]" data-market-import="${PROMPT_MARKET.indexOf(p)}">导入</button>
        </div>
        <div class="text-[11px] text-text-tertiary line-clamp-2">${escapeHtml(p.content.slice(0, 110))}…</div>
      </div>`).join('');
    list.querySelectorAll('[data-market-import]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const p = PROMPT_MARKET[parseInt(btn.dataset.marketImport, 10)];
        await DB.createPrompt({ title: p.title, content: p.content, category: p.category, tags: p.tags, description: '来自本地市场' });
        await PromptsApp.reload();
        btn.textContent = '已导入';
        btn.disabled = true;
        showNotification('已导入', p.title, 'success', 1500);
      });
    });
  };
  renderList('全部');
  box.querySelectorAll('[data-mcat]').forEach(b => {
    b.addEventListener('click', () => {
      box.querySelectorAll('[data-mcat]').forEach(x => { x.className = 'tag-pill bg-surface-floating text-text-tertiary'; });
      b.className = 'tag-pill bg-brand-cobalt/15 text-brand-cobalt';
      renderList(b.dataset.mcat);
    });
  });
}

/**
 * 保存提示词版本历史（编辑器保存时调用，保留最近5版）
 * @param {Object} p - 提示词
 * @param {string} newContent - 新内容
 * @returns {Object[]} 更新后的历史数组 */
function pushPromptHistory(p, newContent) {
  const history = Array.isArray(p.history) ? p.history.slice() : [];
  if (p.content && p.content !== newContent) {
    history.unshift({ content: p.content, savedAt: Date.now() });
    return history.slice(0, 5);
  }
  return history;
}

/* ===================== [4.3] 代码运行器 / 格式化 / 分享 ===================== */

/** JS 代码沙箱运行器（Web Worker 隔离，无 DOM/网络）
 * @param {string} code - JavaScript 代码
 * @returns {Promise<{logs:string[], result:string, ms:number}>} 运行结果 */
function runJavaScriptSandbox(code) {
  return new Promise((resolve) => {
    const logs = [];
    const src = `
      // [加固] 网络能力禁用：常用网络 API 全部置为抛错（尽力加固，非安全边界）
      self.fetch = () => { throw new Error('沙箱已禁用网络'); };
      self.XMLHttpRequest = function () { throw new Error('沙箱已禁用网络'); };
      self.WebSocket = function () { throw new Error('沙箱已禁用网络'); };
      self.EventSource = function () { throw new Error('沙箱已禁用网络'); };
      self.importScripts = () => { throw new Error('沙箱已禁用网络'); };
      try { self.navigator.sendBeacon = () => { throw new Error('沙箱已禁用网络'); }; } catch (e) {}
      // [加固] 额外置空函数构造器（尽力收窄逃逸面；仅作为加固，不代表绝对安全）
      try { globalThis.Function = globalThis.AsyncFunction = globalThis.GeneratorFunction = undefined; } catch (e) {}
      const __logs = [];
      const __fmt = (v) => { try { return typeof v === 'string' ? v : JSON.stringify(v, null, 1); } catch (e) { return String(v); } };
      console.log = (...a) => __logs.push(a.map(__fmt).join(' '));
      console.info = console.log; console.warn = console.log; console.error = console.log;
      self.onerror = (msg) => { __logs.push('❌ ' + msg); };
      let __result;
      try { __result = eval(${JSON.stringify(code)}); } catch (e) { __logs.push('❌ ' + (e.stack || e.message)); }
      postMessage({ logs: __logs, result: typeof __result === 'function' ? '[Function]' : __fmt(__result) });
    `;
    const blob = new Blob([src], { type: 'application/javascript' });
    const url = URL.createObjectURL(blob);
    const worker = new Worker(url);
    const started = performance.now();
    const timer = setTimeout(() => { worker.terminate(); URL.revokeObjectURL(url); resolve({ logs, result: '⏱ 超时（5s）', ms: 5000 }); }, 5000);
    worker.onmessage = (e) => {
      clearTimeout(timer);
      worker.terminate();
      URL.revokeObjectURL(url); // [夜间优化2.2] 及时释放
      resolve({ logs: e.data.logs, result: String(e.data.result), ms: Math.round(performance.now() - started) });
    };
    worker.onerror = (e) => {
      clearTimeout(timer); worker.terminate(); URL.revokeObjectURL(url);
      resolve({ logs: ['❌ ' + e.message], result: '运行出错', ms: Math.round(performance.now() - started) });
    };
  });
}

/** 打开代码运行结果弹窗
 * @param {string} code - 代码
 * @returns {Promise<void>} */
async function openCodeRunner(code) {
  const box = showModal(`<div><div class="skeleton" style="height:80px;border-radius:8px"></div><p class="text-[11px] text-text-tertiary mt-2">正在受限执行环境中运行（Worker 隔离 · 屏蔽网络 API，5 秒超时）…</p></div>`,
    { title: '代码运行结果', icon: 'play', footer: false });
  const r = await runJavaScriptSandbox(code);
  box.innerHTML = `
    <div class="modal-header"><h3 class="modal-title"><i data-lucide="terminal" class="w-4 h-4 text-success"></i>运行完成（${r.ms}ms）</h3>
      <button class="tool-btn" data-modal-close><i data-lucide="x" class="w-4 h-4"></i></button></div>
    <div class="modal-body">
      ${r.logs.length ? `<div class="form-label">控制台输出</div><pre class="chunk-item" style="white-space:pre-wrap;max-height:200px;overflow-y:auto">${escapeHtml(r.logs.join('\n'))}</pre>` : ''}
      <div class="form-label mt-2">返回值</div>
      <pre class="chunk-item" style="white-space:pre-wrap">${escapeHtml(r.result || '（无返回值）')}</pre>
      <p class="text-[10px] text-text-tertiary mt-2">受限执行环境：在独立 Worker 线程运行，与页面 DOM 隔离并屏蔽常用网络 API，适合运行你自己信任的纯计算 JS；它不是安全边界，无法保证拦截恶意代码，请勿运行来源不明的代码。</p>
    </div>`;
  on(box.querySelector('[data-modal-close]'), 'click', closeModal);
  refreshIcons();
}

/** Prettier 懒加载格式化
 * @param {string} code - 代码
 * @param {string} lang - 语言
 * @returns {Promise<string>} 格式化后代码 */
async function formatWithPrettier(code, lang) {
  const parserMap = { javascript: 'babel', typescript: 'typescript', css: 'css', html: 'html', json: 'json' };
  const parser = parserMap[lang];
  if (!parser) throw new Error('Prettier 暂不支持 ' + lang + '（支持 JS/TS/CSS/HTML/JSON）');
  if (!window.prettier || !window.prettierPlugins) {
    showNotification('加载 Prettier', '首次使用需加载格式化引擎（本地 vendor/）', 'info', 2000);
    await new Promise((resolve, reject) => {
      const base = 'vendor/prettier';
      const files = parser === 'typescript'
        ? ['standalone.js', 'plugins/babel.js', 'plugins/estree.js', 'plugins/typescript.js']
        : parser === 'css' ? ['standalone.js', 'plugins/postcss.js']
        : parser === 'html' ? ['standalone.js', 'plugins/babel.js', 'plugins/estree.js', 'plugins/postcss.js', 'plugins/html.js']
        : ['standalone.js', 'plugins/babel.js', 'plugins/estree.js'];
      let chain = Promise.resolve();
      files.forEach(f => {
        chain = chain.then(() => new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = base + '/' + f;
          s.onload = res; s.onerror = () => rej(new Error('Prettier 本地加载失败，请确认 vendor/prettier 目录完整'));
          document.head.appendChild(s);
        }));
      });
      chain.then(resolve).catch(reject);
    });
  }
  const plugins = (window.prettierPlugins || []).filter(p =>
    parser === 'typescript' ? /typescript|estree|babel/.test(p.name || '')
    : parser === 'html' ? true
    : parser === 'css' ? /postcss/.test(p.name || '')
    : /babel|estree/.test(p.name || ''));
  return window.prettier.format(code, { parser, plugins: plugins.length ? plugins : window.prettierPlugins || [] });
}

/** 生成片段分享链接（内容编码进 URL hash，纯前端无服务器）
 * @param {Object} snip - 片段
 * @returns {Promise<void>} */
async function shareSnippetLink(snip) {
  const payload = { t: snip.title, c: snip.code, l: snip.language };
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
  const url = location.origin + location.pathname + '#snippet=' + encoded;
  const ok = await copyToClipboard(url);
  showNotification(ok ? '分享链接已复制' : '复制失败', '链接含完整片段内容（Base64），无服务器中转', ok ? 'success' : 'error', 3000);
}

/** 检查 URL hash 中的分享片段（启动时调用）
 * @returns {Promise<void>} */
async function checkSharedSnippet() {
  const m = location.hash.match(/#snippet=([A-Za-z0-9+/=]+)/);
  if (!m) return;
  try {
    const data = JSON.parse(decodeURIComponent(escape(atob(m[1]))));
    showConfirm('收到分享片段', `导入分享的片段「${data.t}」（${(data.c || '').split('\n').length} 行）？`, async () => {
      await DB.createSnippet({ title: data.t || '分享片段', code: data.c || '', language: data.l || detectLanguage(data.c) || 'text', tags: ['分享'] });
      await SnippetsApp.reload();
      showNotification('已导入', '分享片段已加入片段库', 'success');
    }, { okText: '导入' });
    history.replaceState(null, '', location.pathname); // 用后即清
  } catch (e) { console.warn('[MoRay] 分享片段解析失败:', e); }
}

/* ===================== [4.4] 混合检索（向量 + 关键词融合） ===================== */

/**
 * 关键词重合度打分（0~1，用于与向量相似度融合）
 * @param {string} query - 查询
 * @param {string} text - 目标文本
 * @returns {number} 重合度 */
function keywordOverlap(query, text) {
  const terms = String(query || '').toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, ' ').split(/\s+/).filter(t => t.length > 1);
  if (!terms.length) return 0;
  const lower = String(text || '').toLowerCase();
  let hit = 0;
  terms.forEach(t => { if (lower.includes(t)) hit++; });
  return hit / terms.length;
}

/* ===================== [4.5] 工作流调度 + 通知 ===================== */

/** 工作流调度器（每分钟检查一次到期任务） */
const WorkflowScheduler = {
  /** @type {number|null} */
  timer: null,

  /** 启动调度（每 60s 检查）
   * @returns {void} */
  start() {
    if (this.timer) return;
    this.timer = setInterval(() => this.tick(), 60000);
    setTimeout(() => this.tick(), 15000); // 启动15秒后首查
  },

  /** 解析到期状态
   * @param {Object} wf - 工作流（schedule: 'off'|'interval:分钟'|'daily:HH:MM'）
   * @returns {boolean} 是否到期 */
  due(wf) {
    if (!wf.schedule || wf.schedule === 'off') return false;
    const now = new Date();
    if (wf.schedule.startsWith('interval:')) {
      const mins = parseInt(wf.schedule.split(':')[1], 10) || 60;
      const last = wf.lastRunAt || 0;
      return Date.now() - last >= mins * 60000;
    }
    if (wf.schedule.startsWith('daily:')) {
      const [h, m] = wf.schedule.split(':')[1].split(':').map(Number);
      const last = wf.lastRunAt || 0;
      const todayAt = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h || 0, m || 0).getTime();
      return now.getTime() >= todayAt && last < todayAt;
    }
    return false;
  },

  /** 检查所有工作流
   * @returns {Promise<void>} */
  async tick() {
    if (document.hidden) return; // 后台不跑
    if (WorkflowEngine.current) return; // 有任务在执行
    for (const wf of WorkflowsApp.cache) {
      if (this.due(wf)) {
        showNotification('定时工作流启动', wf.name, 'info', 2500);
        WorkflowEngine.run(wf);
        return; // 一次只跑一个
      }
    }
  }
};

/** 桌面通知（工作流完成等），首次使用请求权限
 * @param {string} title - 标题
 * @param {string} body - 正文
 * @returns {void} */
function desktopNotify(title, body) {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') {
    try { new Notification(title, { body, icon: 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22%3E%3Ctext y=%2242%22 font-size=%2232%22%3E%E2%97%88%3C/text%3E%3C/svg%3E' }); } catch (e) { /* 忽略 */ }
  } else if (Notification.permission === 'default') {
    Notification.requestPermission().then(p => { if (p === 'granted') desktopNotify(title, body); });
  }
}

/* ===================== [4.6] 模型参数预设 ===================== */

/** 参数预设（一键应用到全局生成参数） */
const PARAM_PRESETS = [
  { name: '创意', temp: 1.0, topP: 0.95, desc: '头脑风暴/文案/命名' },
  { name: '平衡', temp: 0.7, topP: 0.9, desc: '日常对话/通用任务' },
  { name: '精确', temp: 0.2, topP: 0.8, desc: '代码/数学/事实问答' }
];

/** 打开参数预设选择
 * @returns {void} */
function applyParamPreset(preset) {
  MoraySettings.set({ temperature: preset.temp, topP: preset.topP }).then(() => {
    SettingsApp.build();
    showNotification('参数预设已应用', `${preset.name}（temp ${preset.temp} · top_p ${preset.topP}）`, 'success', 2000);
  });
}
