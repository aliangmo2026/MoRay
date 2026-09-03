/* ============================================================
   [任务1] ⌘K 统一全局搜索 + 命令面板（PaletteSearch）
   - 数据源：会话/提示词/片段/工作流/文档（DB 打开时按需异步读取）+ 静态动作 + 设置直达
   - 匹配：多关键词 AND + 打分（标题连续命中>词首/标签>内容）+ <mark> 高亮
   - 键盘：跨分组上下移动、Enter 执行、Esc 关闭；竞态保护（序号比对）
   ============================================================ */

/** 统一搜索面板 */
const PaletteSearch = {
  /** 每次查询序号（竞态保护：只采纳最后一次） @type {number} */
  seq: 0,
  /** 防抖计时器 @type {number|null} */
  _timer: null,
  /** 当前选中索引（跨分组） @type {number} */
  selectedIdx: 0,
  /** 当前渲染结果 @type {Array} */
  results: [],
  /** 本次打开时加载的数据源缓存 @type {Object|null} */
  _data: null,
  /** 最近选中记录 @type {Array} */
  recents: [],
  /** 分组顺序 */
  GROUP_ORDER: ['动作', '会话', '提示词', '片段', '工作流', '文档', '设置'],

  /** 初始化：接管打开、输入、键盘、点击
   * @returns {void} */
  init() {
    // 接管 openCommandPalette（function 声明可被 window 覆盖）
    const origOpen = window.openCommandPalette;
    window.openCommandPalette = function () {
      if (origOpen) origOpen();
      setTimeout(() => PaletteSearch.onOpen(), 30);
    };
    const input = document.getElementById('commandInput');
    if (!input) return;
    input.addEventListener('input', (e) => {
      clearTimeout(this._timer);
      const q = e.target.value;
      this._timer = setTimeout(() => this.search(q), 150);
    });
    input.addEventListener('keydown', (e) => {
      const items = this.results;
      if (!items.length && e.key !== 'Escape') return;
      if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); this.move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); this.move(-1); }
      else if (e.key === 'Enter') {
        e.preventDefault(); e.stopPropagation();
        const item = items[this.selectedIdx];
        if (item) this.activate(item);
      }
    });
    const list = document.getElementById('commandList');
    if (list) list.addEventListener('click', (e) => {
      const el = e.target.closest('.command-item');
      if (el && el.dataset.resultIndex !== undefined) {
        this.activate(this.results[parseInt(el.dataset.resultIndex, 10)]);
      }
    });
    try { this.recents = JSON.parse(localStorage.getItem('moray_palette_recents') || '[]') || []; } catch (err) { this.recents = []; }
  },

  /** 面板打开：异步装载数据并渲染默认视图
   * @returns {Promise<void>} */
  async onOpen() {
    this.seq++;
    const mySeq = this.seq;
    const data = await this.loadData();
    if (mySeq !== this.seq) return;
    this._data = data;
    const q = document.getElementById('commandInput').value;
    await this.search(q || '');
  },

  /** 异步装载全部数据源（各自 try/catch，失败分组静默缺省）
   * @returns {Promise<Object>} {conversations, prompts, snippets, workflows, docs} */
  async loadData() {
    const convs = [];
    try {
      const all = (await DB.listConversations()).slice().sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
      for (const c of all.slice(0, 30)) {
        let last = '';
        const idxMsgs = AppState._convMsgIndex && AppState._convMsgIndex[c.id];
        if (idxMsgs && idxMsgs.length) last = idxMsgs[idxMsgs.length - 1].content || '';
        else if (convs.length < 10) {
          try {
            const msgs = await DB.listMessagesByConversation(c.id);
            last = msgs.length ? msgs[msgs.length - 1].content || '' : '';
          } catch (err) { last = ''; }
        }
        convs.push({ type: 'conversation', id: c.id, title: c.title || '新对话', subtitle: last.slice(0, 80), icon: 'message-square' });
      }
    } catch (err) { /* 会话源失败：静默缺省 */ }
    let prompts = [];
    try { prompts = (await DB.listPrompts()).map(p => ({ type: 'prompt', id: p.id, title: p.title, subtitle: (p.description || p.content || '').slice(0, 80), tags: p.tags || [], content: p.content || '', icon: 'file-text' })); } catch (err) { /* 缺省 */ }
    let snippets = [];
    try { snippets = (await DB.listSnippets()).map(s => ({ type: 'snippet', id: s.id, title: s.title, subtitle: (s.description || s.language || '').slice(0, 60), tags: s.tags || [], content: (s.code || '').slice(0, 400), icon: 'code-2' })); } catch (err) { /* 缺省 */ }
    let workflows = [];
    try { workflows = (await DB.listWorkflows()).map(w => ({ type: 'workflow', id: w.id, title: w.name, subtitle: (w.description || '').slice(0, 80), icon: 'workflow' })); } catch (err) { /* 缺省 */ }
    let docs = [];
    try {
      const all = await DB.listDocuments();
      docs = all.map(d => ({ type: 'doc', id: d.id, title: d.name, subtitle: (d.chunks && d.chunks[0] ? d.chunks[0].text : '').slice(0, 80), icon: 'book-open' }));
    } catch (err) { /* 缺省 */ }
    return { conversations: convs, prompts: prompts, snippets: snippets, workflows: workflows, docs: docs };
  },

  /** 静态动作与设置直达（对应现有全部命令项，不另造第二套实现）
   * @returns {Array} */
  staticItems() {
    const acts = [
      { type: 'action', action: 'new-chat', title: '新建对话', subtitle: '创建一个新的 AI 对话会话', icon: 'plus', shortcut: '⌘N' },
      { type: 'action', action: 'toggle-compare', title: '切换多模型对比', subtitle: '在普通对话和对比模式间切换', icon: 'columns-2', shortcut: '⌘⇧V' },
      { type: 'action', action: 'goto-prompts', title: '打开提示词库', subtitle: '查看和管理你的提示词模板', icon: 'file-text', shortcut: '⌘2' },
      { type: 'action', action: 'goto-snippets', title: '打开代码片段', subtitle: '查看和管理你的代码片段库', icon: 'code-2', shortcut: '⌘3' },
      { type: 'action', action: 'switch-model', title: '切换模型', subtitle: '选择当前使用的 AI 模型', icon: 'cpu' },
      { type: 'action', action: 'goto-settings', title: '打开设置', subtitle: '配置模型、界面和快捷键', icon: 'settings', shortcut: '⌘4' },
      { type: 'action', action: 'goto-docs', title: '文档库', subtitle: '管理本地 RAG 文档知识库', icon: 'book-open', shortcut: '⌘5' },
      { type: 'action', action: 'goto-automation', title: '自动化工作流', subtitle: '运行和管理 AI 工作流', icon: 'workflow', shortcut: '⌘6' },
      { type: 'action', action: 'goto-cost', title: '成本与省钱中心', subtitle: '查看花费、节省与预算', icon: 'piggy-bank' },
      { type: 'action', action: 'export-data', title: '导出全部数据', subtitle: '备份会话/提示词/片段/文档到 JSON', icon: 'download' },
      { type: 'action', action: 'toggle-theme', title: '切换深浅主题', subtitle: '在深色与浅色主题间切换', icon: 'sun-moon' }
    ];
    const settings = [
      { type: 'setting', target: 'model', title: '模型配置', subtitle: 'Ollama 服务 / 云端 API / 默认模型', icon: 'cpu' },
      { type: 'setting', target: 'apikey', title: 'API Key 配置', subtitle: 'OpenAI 兼容 API 的密钥设置', icon: 'key-round' },
      { type: 'setting', target: 'ui', title: '壁纸与主题', subtitle: '界面设置：主题、壁纸、字号、气泡', icon: 'palette' },
      { type: 'setting', target: 'cost', title: '预算与成本价格', subtitle: '月度预算、价格表与自动降级', icon: 'coins' },
      { type: 'setting', target: 'gateway', title: '缓存与路由', subtitle: 'API 智能网关：缓存、路由、用量', icon: 'zap' },
      { type: 'setting', target: 'shortcuts', title: '快捷键设置', subtitle: '自定义快捷键绑定与导入导出', icon: 'keyboard' },
      { type: 'setting', target: 'data', title: '数据管理', subtitle: '导出 / 导入 / 自动备份 / 清空', icon: 'database' },
      { type: 'setting', target: 'about', title: '关于 MoRay', subtitle: '版本、新手引导、错误日志', icon: 'info' }
    ];
    return { actions: acts, settings: settings };
  },

  /** 打分与过滤（多关键词 AND；标题连续 > 词首/标签 > 内容）
   * @param {Object} item - 结果项
   * @param {string[]} tokens - 关键词
   * @returns {number} 分数（-1=不匹配） */
  scoreItem(item, tokens) {
    const title = String(item.title || '').toLowerCase();
    const subtitle = String(item.subtitle || '').toLowerCase();
    const tags = (item.tags || []).join(' ').toLowerCase();
    const content = String(item.content || '').toLowerCase();
    let score = 0;
    for (const tok of tokens) {
      if (title.includes(tok)) {
        score += 4;
        if (title.startsWith(tok)) score += 2;
      } else if (tags.includes(tok)) score += 2;
      else if (subtitle.includes(tok)) score += 1;
      else if (content.includes(tok)) score += 1;
      else return -1; // AND：任一关键词完全不命中即排除
    }
    return score;
  },

  /** 执行搜索并渲染
   * @param {string} query - 查询词
   * @returns {Promise<void>} */
  async search(query) {
    this.seq++;
    const mySeq = this.seq;
    const q = (query || '').trim().toLowerCase().replace(/\s+/g, ' ');
    if (!this._data) await this.loadData();
    if (mySeq !== this.seq) return;
    const data = this._data;
    const stat = this.staticItems();
    const all = {
      '动作': stat.actions,
      '会话': data.conversations,
      '提示词': data.prompts,
      '片段': data.snippets,
      '工作流': data.workflows,
      '文档': data.docs,
      '设置': stat.settings
    };
    let results = [];
    const tokens = q ? q.split(' ') : [];
    if (!tokens.length) {
      // 空查询：最近5个会话 + 常用动作
      const recentIds = this.recents.filter(r => r.type === 'conversation').map(r => r.id);
      const recentConvs = recentIds.map(id => data.conversations.find(c => c.id === id)).filter(Boolean);
      const restConvs = data.conversations.filter(c => !recentIds.includes(c.id)).slice(0, 5 - recentConvs.length);
      results = results.concat(
        recentConvs.concat(restConvs).map(c => Object.assign({ group: '会话', score: 10 }, c)),
        stat.actions.map(a => Object.assign({ group: '动作', score: 5 }, a))
      );
    } else {
      for (const group of this.GROUP_ORDER) {
        const items = all[group] || [];
        const scored = [];
        for (const item of items) {
          const s = this.scoreItem(item, tokens);
          if (s >= 0) scored.push(Object.assign({ group: group, score: s }, item));
        }
        scored.sort((a, b) => b.score - a.score);
        results = results.concat(scored.slice(0, 6));
      }
      results.sort((a, b) => b.score - a.score);
      results = results.slice(0, 40);
    }
    if (mySeq !== this.seq) return;
    this.results = results;
    this.selectedIdx = 0;
    this.render(results, q);
  },

  /** 高亮关键词
   * @param {string} text - 原文
   * @param {string[]} tokens - 关键词
   * @returns {string} 高亮HTML */
  highlight(text, tokens) {
    let t = escapeHtml(text);
    if (tokens.length) {
      tokens.forEach(tok => {
        const re = new RegExp('(' + tok.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
        t = t.replace(re, '<mark class="cmd-mark">$1</mark>');
      });
    }
    return t;
  },

  /** 渲染结果列表
   * @param {Array} results - 结果
   * @param {string} q - 查询词
   * @returns {void} */
  render(results, q) {
    const list = document.getElementById('commandList');
    if (!list) return;
    const tokens = q ? q.split(' ') : [];
    if (!results.length) {
      list.innerHTML = [
        '<div class="command-group-title">搜索结果</div>',
        '<div class="command-empty">',
        '<i data-lucide="search-x" class="w-6 h-6 mx-auto mb-2 opacity-50"></i>',
        '<div class="text-xs text-text-secondary mb-2">未找到匹配内容</div>',
        '<div class="flex gap-2 justify-center">',
        '<button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" data-empty-action="new-chat">新建会话</button>',
        '<button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" data-empty-action="docs">搜索文档</button>',
        '</div></div>'
      ].join('');
      refreshIcons();
      on(list.querySelector('[data-empty-action="new-chat"]'), 'click', () => { this.activate({ type: 'action', action: 'new-chat' }); });
      on(list.querySelector('[data-empty-action="docs"]'), 'click', () => { this.activate({ type: 'action', action: 'goto-docs' }); });
      return;
    }
    const groupCounts = {};
    results.forEach(r => { groupCounts[r.group] = (groupCounts[r.group] || 0) + 1; });
    let html = '<div class="command-group-title">' + results.length + ' 条结果' + Object.keys(groupCounts).map(g => ' · ' + g + ' ' + groupCounts[g]).join('') + '</div>';
    let lastGroup = '';
    results.forEach((r, i) => {
      if (r.group !== lastGroup) {
        lastGroup = r.group;
        html += '<div class="command-group-title">' + escapeHtml(r.group) + '</div>';
      }
      const shortcut = r.shortcut ? '<div class="command-item-shortcut"><kbd>' + escapeHtml(r.shortcut) + '</kbd></div>' : '';
      html += [
        '<div class="command-item ' + (i === this.selectedIdx ? 'selected' : '') + '" data-result-index="' + i + '">',
        '<div class="command-item-icon"><i data-lucide="' + escapeHtml(r.icon || 'circle') + '" class="w-4 h-4"></i></div>',
        '<div class="command-item-text">',
        '<div class="command-item-name">' + this.highlight(r.title, tokens) + '</div>',
        r.subtitle ? '<div class="command-item-desc">' + this.highlight(r.subtitle, tokens) + '</div>' : '',
        '</div>' + shortcut + '</div>'
      ].join('');
    });
    list.innerHTML = html;
    refreshIcons();
  },

  /** 上下移动选中（跨分组）
   * @param {number} dir - 方向 ±1
   * @returns {void} */
  move(dir) {
    if (!this.results.length) return;
    this.selectedIdx = (this.selectedIdx + dir + this.results.length) % this.results.length;
    this.updateSelection();
  },

  /** 更新 selected 高亮并滚动可见
   * @returns {void} */
  updateSelection() {
    const list = document.getElementById('commandList');
    if (!list) return;
    list.querySelectorAll('.command-item').forEach((el, i) => {
      el.classList.toggle('selected', i === this.selectedIdx);
    });
    const sel = list.querySelector('.command-item.selected');
    if (sel) sel.scrollIntoView({ block: 'nearest' });
  },

  /** 记录最近选中（localStorage 单键，最多5条）
   * @param {Object} item - 结果项
   * @returns {void} */
  recordRecent(item) {
    if (item.type !== 'conversation') return;
    this.recents = this.recents.filter(r => !(r.type === item.type && r.id === item.id));
    this.recents.unshift({ type: item.type, id: item.id, label: item.title });
    this.recents = this.recents.slice(0, 5);
    try { localStorage.setItem('moray_palette_recents', JSON.stringify(this.recents)); } catch (err) { /* 忽略 */ }
  },

  /** 选中后执行动作（全部复用现有函数；执行后统一关闭面板）
   * @param {Object} item - 结果项
   * @returns {void} */
  activate(item) {
    if (!item) return;
    const close = () => { try { closeCommandPalette(); } catch (err) { document.getElementById('commandPalette').classList.remove('active'); } };
    try {
      switch (item.type) {
        case 'conversation':
          this.recordRecent(item);
          document.querySelector('.nav-icon-btn[data-page="chat"]').click();
          setTimeout(() => openConversation(item.id), 150);
          break;
        case 'prompt': {
          const p = PromptsApp.cache.find(x => x.id === item.id);
          if (p) PromptsApp.insertToInput(p.content);
          else { document.querySelector('.nav-icon-btn[data-page="prompts"]').click(); }
          break;
        }
        case 'snippet': {
          const s = SnippetsApp.cache.find(x => x.id === item.id);
          document.querySelector('.nav-icon-btn[data-page="snippets"]').click();
          if (s) {
            copyToClipboard(s.code).then(ok => showNotification(ok ? '已复制' : '复制失败', s.title, ok ? 'success' : 'error', 1600));
          }
          break;
        }
        case 'workflow': {
          document.querySelector('.nav-icon-btn[data-page="automation"]').click();
          setTimeout(() => {
            const card = document.querySelector('#workflowGrid [data-wf-id="' + item.id + '"]');
            if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); card.style.boxShadow = '0 0 0 2px rgba(91,140,255,0.4)'; setTimeout(() => { card.style.boxShadow = ''; }, 1200); }
          }, 250);
          break;
        }
        case 'doc':
          openDocPreview(item.id, 0);
          break;
        case 'setting':
          document.querySelector('.nav-icon-btn[data-page="settings"]').click();
          setTimeout(() => this.scrollToSetting(item.target), 300);
          break;
        case 'action':
        default:
          this.runAction(item.action);
          break;
      }
    } catch (err) {
      console.warn('[MoRay] palette activate failed:', err);
    }
    close();
  },

  /** 滚动并高亮设置卡片
   * @param {string} target - 目标标识
   * @returns {void} */
  scrollToSetting(target) {
    let el = null;
    if (target === 'model') el = document.getElementById('card-model');
    else if (target === 'apikey') { const t = document.getElementById('setOpenaiEnabled'); el = t ? t.closest('.glass-card') : null; }
    else if (target === 'ui') el = document.getElementById('card-ui');
    else if (target === 'cost') el = document.getElementById('costSettingsCard');
    else if (target === 'gateway') el = document.getElementById('gatewaySettingsCard');
    else if (target === 'shortcuts') el = document.getElementById('enhanceSettingsCard');
    else if (target === 'data') el = document.getElementById('card-data');
    else if (target === 'about') el = document.getElementById('card-about');
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      el.style.boxShadow = '0 0 0 2px rgba(91,140,255,0.35)';
      setTimeout(() => { el.style.boxShadow = ''; }, 900);
    }
  },

  /** 执行动作（复用现有 executeCommand 覆盖项 + 三个扩展项）
   * @param {string} action - 动作名
   * @returns {void} */
  runAction(action) {
    if (typeof executeCommand === 'function' && ['new-chat', 'toggle-compare', 'goto-prompts', 'goto-snippets', 'switch-model', 'goto-settings', 'goto-docs'].includes(action)) {
      executeCommand(action);
      return;
    }
    switch (action) {
      case 'goto-automation':
        document.querySelector('.nav-icon-btn[data-page="automation"]').click();
        break;
      case 'goto-cost':
        document.querySelector('.nav-icon-btn[data-page="cost"]').click();
        break;
      case 'export-data':
        DB.exportAll().then(data => { downloadJson('moray_backup.json', data); showNotification('导出成功', '备份文件已下载', 'success'); });
        break;
      case 'toggle-theme':
        MoraySettings.set('theme', MoraySettings.get('theme') === 'light' ? 'dark' : 'light').then(() => SettingsApp.build());
        break;
      default:
        showNotification('动作', action, 'info', 1200);
    }
  }
};
