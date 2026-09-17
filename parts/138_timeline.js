/* ============================================================
   [飞行记录仪] TimelineViewer —— 工具调用审计时间轴可视化
   - 数据来源：GET /api/agent/log（现有接口，不可修改）
   - 功能：时间轴可视化 / 状态筛选 / 工具筛选 / 点击展开详情 /
           自动刷新 / 滚动加载更多 / 空状态 / 错误重试 /
           修改并重放（Kernel 第二阶段：Event Sourcing 确定性回放）
   - 零新增依赖：仅用已有 lucide 图标 + Tailwind 样式
   - 加载顺序：135_ui_polish 之后（所有依赖已就绪）
   - 入口：左侧导航栏「飞行记录仪」按钮 + 设置页「本机 Agent」卡内按钮
   ============================================================ */

/** 工具调用审计的右侧滑出时间轴面板。
 * 纯前端视图层：只读 GET /api/agent/log；不触碰审批/安全/审计写入逻辑。
 * @namespace TimelineViewer */
const TimelineViewer = {
  /** 每页条数（与后端 limit 上限 500 兼容） */
  PAGE_SIZE: 50,
  /** 自动刷新间隔（毫秒） */
  AUTO_REFRESH_MS: 10000,

  /** 视图状态（关闭面板保留：筛选条件 + 自动刷新开关 + 已加载数据；
   * 重置：展开项、滚动位置） */
  state: {
    rows: [],           // 已加载的所有记录
    total: 0,           // 后端总条数
    offset: 0,          // 当前分页偏移
    loading: false,     // 是否正在加载
    expandedId: null,   // 当前展开的记录 id
    filterStatus: 'all',// 状态筛选
    filterTool: 'all',  // 工具筛选
    autoRefresh: false, // 自动刷新开关
    refreshTimer: null, // 自动刷新定时器
    open: false,        // 面板是否打开
    error: null,        // { title, hint, detail } 或 null
    updatedAt: null,    // 最近一次成功加载的时刻
    replayRow: null     // 当前重放对话框对应的记录
  },

  /** 工具名 → 中文标签映射（与 125_tools.js 的工具 label 完全一致，另有历史/后端专用工具） */
  TOOL_LABELS: {
    list_directory: '列出目录',
    read_file: '读取文件',
    write_file: '写文件',
    create_file: '新建文件',
    edit_file: '编辑文件',
    move_file: '移动文件',
    rename_file: '重命名文件',
    run_command: '运行只读命令',
    find_files: '查找文件',
    search_text: '全文搜索',
    list_processes: '查看进程',
    system_info: '系统信息',
    submit_plan: '提交任务计划',
    get_current_time: '获取当前时间',
    calculator: '计算器',
    query_knowledge_base: '查询知识库',
    save_snippet: '保存代码片段',
    run_workflow: '运行自动化工作流',
    get_cost_usage: '查询费用用量',
    config_set_workspace: '修改工作区根',
    config_clear_audit: '清空审计日志'
  },

  /** 工具名 → lucide 图标名（节点图标；未知工具回退 circle-dot） */
  TOOL_ICONS: {
    write_file: 'file-plus',
    create_file: 'file-plus',
    read_file: 'file-text',
    list_directory: 'folder',
    run_command: 'terminal',
    edit_file: 'file-edit',
    move_file: 'folder-input',
    rename_file: 'folder-input',
    find_files: 'search',
    search_text: 'text-search',
    list_processes: 'cpu',
    system_info: 'info',
    submit_plan: 'list-filter',
    query_knowledge_base: 'book-open',
    save_snippet: 'save',
    run_workflow: 'workflow',
    get_cost_usage: 'piggy-bank',
    config_set_workspace: 'folder-cog',
    config_clear_audit: 'trash-2'
  },

  /** 状态 → 中文标签 + 节点配色 + 徽章配色（统一使用项目设计令牌） */
  STATUS_MAP: {
    ok: { label: '成功', node: 'text-success border-success/30', badge: 'bg-success/10 text-success border-success/20' },
    error: { label: '失败', node: 'text-danger border-danger/30', badge: 'bg-danger/10 text-danger border-danger/20' },
    timeout: { label: '超时', node: 'text-warning border-warning/30', badge: 'bg-warning/10 text-warning border-warning/20' },
    rejected: { label: '安全拒绝', node: 'text-warning border-warning/30', badge: 'bg-warning/10 text-warning border-warning/20' },
    needs_approval: { label: '待审批', node: 'text-danger border-danger/30', badge: 'bg-danger/10 text-danger border-danger/20' },
    denied: { label: '已拒绝', node: 'text-text-tertiary border-line-ghost', badge: 'bg-surface-panel text-text-tertiary border-line-ghost' }
  },

  /** 状态筛选下拉框的项目（顺序固定；动态出现的未知状态会追加） */
  STATUS_FILTERS: [
    { value: 'all', label: '全部状态' },
    { value: 'ok', label: '成功' },
    { value: 'error', label: '失败' },
    { value: 'timeout', label: '超时' },
    { value: 'rejected', label: '安全拒绝' },
    { value: 'needs_approval', label: '待审批' },
    { value: 'denied', label: '已拒绝' }
  ],

  /* ===================== 初始化 / 入口 ===================== */

  /** 初始化：安装导航入口、全局按键与可见性监听（幂等，可重复调用）
   * @returns {void} */
  init() {
    if (!this.installNavButton()) {
      setTimeout(() => { try { this.installNavButton(); } catch (e) { /* 静默 */ } }, 0);
    }
    if (this._inited) return;
    this._inited = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (this._replayDialogOpen()) { this.closeReplayDialog(); return; }
        if (this.state.open) { this.close(); }
      }
    });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && this.state.open && this.state.autoRefresh) this.load(false);
    });
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-tl-replay]');
      if (btn) {
        e.stopPropagation();
        const id = Number(btn.dataset.tlReplay);
        const row = this.state.rows.filter(r => Number(r.id) === id)[0];
        if (row) this.openReplayDialog(row);
      }
    });
  },

  /** 左侧导航栏入口按钮（不设 data-page —— 本面板是右侧浮层，不参与页面切换）
   * @returns {boolean} 是否已就绪 */
  installNavButton() {
    if (document.getElementById('navTimelineBtn')) return true;
    const cost = document.querySelector('.nav-icon-btn[data-page="cost"]');
    const nav = cost ? cost.parentElement : document.querySelector('aside nav');
    if (!nav) return false;
    const btn = document.createElement('button');
    btn.id = 'navTimelineBtn';
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-tooltip', '飞行记录仪（工具调用时间轴）');
    btn.setAttribute('aria-label', '飞行记录仪（工具调用时间轴）');
    btn.innerHTML = '<i data-lucide="activity" class="w-[18px] h-[18px]"></i>';
    if (cost) cost.insertAdjacentElement('afterend', btn); else nav.appendChild(btn);
    btn.addEventListener('click', () => this.toggle());
    refreshIcons();
    return true;
  },

  /** 开关切换（导航按钮用）
   * @returns {void} */
  toggle() { this.state.open ? this.close() : this.open(); },

  /** 打开时间轴面板（首次调用创建 DOM；重复调用只做滑入）
   * @returns {void} */
  open() {
    const panel = this._ensurePanel();
    if (!panel) return;
    this.state.open = true;
    this.state.expandedId = null;
    panel.classList.remove('hidden');
    panel.classList.add('flex');
    const ov = document.getElementById('timelineOverlay');
    if (ov) ov.classList.remove('hidden');
    void panel.offsetWidth;
    if (ov) ov.classList.remove('opacity-0');
    panel.classList.remove('translate-x-full');
    const body = panel.querySelector('#timelineBody');
    if (body) body.scrollTop = 0;
    this.render();
    this.load(false);
    if (this.state.autoRefresh) this.startAutoRefresh();
  },

  /** 关闭面板（滑出后隐藏；已加载数据/筛选条件保留，展开项与滚动位置重置）
   * @returns {void} */
  close() {
    if (!this.state.open) return;
    this.state.open = false;
    this.stopAutoRefresh();
    this.state.expandedId = null;
    const panel = document.getElementById('timelinePanel');
    const ov = document.getElementById('timelineOverlay');
    void (panel ? panel.offsetWidth : 0);
    if (ov) ov.classList.add('opacity-0');
    if (panel) panel.classList.add('translate-x-full');
    setTimeout(() => {
      if (this.state.open) return;
      if (ov) ov.classList.add('hidden');
      if (panel) { panel.classList.add('hidden'); panel.classList.remove('flex'); }
    }, 320);
  },

  /* ===================== DOM 构建 ===================== */

  /** 懒创建遮罩 + 滑出面板（幂等）
   * @returns {HTMLElement|null} 面板元素
   * @private */
  _ensurePanel() {
    let panel = document.getElementById('timelinePanel');
    if (panel) return panel;

    const overlay = document.createElement('div');
    overlay.id = 'timelineOverlay';
    overlay.className = 'fixed inset-0 z-[9500] hidden bg-black/50 backdrop-blur-sm opacity-0 transition-opacity duration-300';
    overlay.addEventListener('click', () => this.close());
    document.body.appendChild(overlay);

    panel = document.createElement('aside');
    panel.id = 'timelinePanel';
    panel.className = 'fixed inset-y-0 right-0 z-[9501] hidden w-[480px] max-w-[90vw] flex-col bg-surface-deep border-l border-line-ghost translate-x-full transition-transform duration-300 ease-out shadow-2xl';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', '飞行记录仪：工具调用审计时间轴');
    panel.innerHTML = '' +
      '<div class="flex items-start gap-2 px-4 py-3 border-b border-line-ghost/60 flex-shrink-0">' +
        '<div class="flex items-center gap-2 min-w-0 flex-1">' +
          '<i data-lucide="activity" class="w-4 h-4 text-brand-cyan flex-shrink-0"></i>' +
          '<div class="min-w-0">' +
            '<div class="text-sm font-medium text-text-primary">飞行记录仪</div>' +
            '<div class="text-[10px] text-text-tertiary">工具调用审计 · 后端 SQLite 持久化</div>' +
          '</div>' +
        '</div>' +
        '<button id="timelineCloseBtn" class="btn-ghost p-1.5 rounded-lg border border-line-ghost" aria-label="关闭面板" title="关闭（Esc）">' +
          '<i data-lucide="x" class="w-4 h-4"></i>' +
        '</button>' +
      '</div>' +
      '<div class="flex items-center gap-2 px-4 py-2 border-b border-line-ghost/50 flex-shrink-0 flex-wrap">' +
        '<select id="timelineStatusSel" class="form-select" style="width:auto;padding:3px 8px;font-size:11px" aria-label="状态筛选"></select>' +
        '<select id="timelineToolSel" class="form-select" style="width:auto;max-width:150px;padding:3px 8px;font-size:11px" aria-label="工具筛选"></select>' +
        '<button id="timelineRefreshBtn" class="btn-ghost px-2.5 h-7 rounded-lg text-[10px] flex items-center gap-1.5 border border-line-ghost" title="重新从后端拉取最新记录">' +
          '<i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>刷新' +
        '</button>' +
        '<div class="ml-auto flex items-center gap-1.5 text-[10px] text-text-secondary" title="开启后每 10 秒自动刷新（页面隐藏时暂停）">自动刷新' +
          '<div class="toggle-track" id="timelineAutoToggle" role="switch" aria-checked="false" aria-label="自动刷新" tabindex="0"><div class="toggle-thumb"></div></div>' +
        '</div>' +
      '</div>' +
      '<div class="flex items-center gap-2 px-4 py-1.5 text-[10px] text-text-tertiary border-b border-line-ghost/40 flex-shrink-0">' +
        '<span id="timelineStats">显示 0 / 共 0 条</span>' +
        '<span id="timelineUpdated" class="ml-auto font-mono"></span>' +
      '</div>' +
      '<div id="timelineError" class="hidden mx-4 mt-3 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 flex-shrink-0">' +
        '<div class="flex items-start gap-2">' +
          '<i data-lucide="alert-triangle" class="w-4 h-4 text-danger flex-shrink-0 mt-0.5"></i>' +
          '<div class="flex-1 min-w-0">' +
            '<div id="timelineErrorMsg" class="text-[11px] text-danger">无法连接后端</div>' +
            '<div id="timelineErrorHint" class="text-[10px] text-text-tertiary mt-1">请确认 MoRay 后端已启动（双击 启动MoRay.bat）</div>' +
          '</div>' +
          '<button id="timelineRetryBtn" class="btn-ghost px-2 py-0.5 rounded-md text-[10px] border border-danger/40 text-danger flex-shrink-0">重试</button>' +
        '</div>' +
      '</div>' +
      '<div id="timelineBody" class="flex-1 overflow-y-auto p-4 min-h-0">' +
        '<div id="timelineList" class="space-y-5"></div>' +
        '<div id="timelineFooter" class="py-3 text-center text-[10px] text-text-tertiary"></div>' +
      '</div>';

    document.body.appendChild(panel);

    const closeBtn = panel.querySelector('#timelineCloseBtn');
    if (closeBtn) closeBtn.addEventListener('click', () => this.close());

    const statusSel = panel.querySelector('#timelineStatusSel');
    if (statusSel) statusSel.addEventListener('change', () => {
      this.state.filterStatus = statusSel.value;
      this.render();
    });
    const toolSel = panel.querySelector('#timelineToolSel');
    if (toolSel) toolSel.addEventListener('change', () => {
      this.state.filterTool = toolSel.value;
      this.render();
    });
    const refreshBtn = panel.querySelector('#timelineRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => this.load(false));
    const retryBtn = panel.querySelector('#timelineRetryBtn');
    if (retryBtn) retryBtn.addEventListener('click', () => this.load(false));

    const autoTog = panel.querySelector('#timelineAutoToggle');
    if (autoTog) {
      const flip = () => {
        this.state.autoRefresh = !this.state.autoRefresh;
        autoTog.classList.toggle('active', this.state.autoRefresh);
        autoTog.setAttribute('aria-checked', this.state.autoRefresh ? 'true' : 'false');
        if (this.state.autoRefresh) {
          this.startAutoRefresh();
          showNotification('自动刷新已开启', '每 10 秒自动拉取最新审计记录（页面隐藏时暂停）', 'info', 2200);
        } else {
          this.stopAutoRefresh();
        }
      };
      autoTog.addEventListener('click', flip);
      autoTog.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); }
      });
    }

    const list = panel.querySelector('#timelineList');
    if (list) {
      list.addEventListener('click', (e) => {
        if (e.target.closest('[data-tl-detail]')) return;
        const card = e.target.closest('[data-tl-id]');
        if (card) this.toggleExpand(card.dataset.tlId);
      });
      list.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        if (e.target.closest('[data-tl-detail]')) return;
        const card = e.target.closest('[data-tl-id]');
        if (card) { e.preventDefault(); this.toggleExpand(card.dataset.tlId); }
      });
    }
    const body = panel.querySelector('#timelineBody');
    if (body) {
      body.addEventListener('scroll', () => {
        if (!this.state.open || this.state.loading) return;
        if (this.state.rows.length >= this.state.total) return;
        if (body.scrollTop + body.clientHeight >= body.scrollHeight - 200) this.load(true);
      });
    }
    const footer = panel.querySelector('#timelineFooter');
    if (footer) footer.addEventListener('click', (e) => {
      if (e.target.closest('#timelineMoreBtn')) this.load(true);
    });

    refreshIcons();
    return panel;
  },

  /* ===================== 数据加载 ===================== */

  /** 从后端加载审计记录
   * @param {boolean} [append] - true=追加下一页；false=从头刷新
   * @returns {Promise<void>} */
  async load(append = false) {
    if (this.state.loading) return;
    const panel = this._ensurePanel();
    if (!panel) return;
    this.state.loading = true;
    const offset = append ? this.state.rows.length : 0;
    this._setRefreshing(true);
    this._updateFooter();
    if (!append && !this.state.rows.length) {
      const list = panel.querySelector('#timelineList');
      if (list) list.innerHTML = this._loadingHtml();
      refreshIcons();
    }
    let ok = false;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 6000);
      let res;
      try {
        res = await fetch(agentBackendBase() + '/api/agent/log?limit=' + this.PAGE_SIZE + '&offset=' + offset,
          { signal: ctrl.signal, cache: 'no-store' });
      } finally {
        clearTimeout(timer);
      }
      const j = await res.json().catch(() => null);
      if (!res.ok || !j || !j.ok) throw new Error((j && (j.message || j.error)) || ('后端返回 HTTP ' + res.status));
      const rows = (j.data && j.data.rows) || [];
      const total = Number((j.data && j.data.total) || 0);
      this.state.total = total;
      this.state.rows = append ? this.state.rows.concat(rows) : rows;
      this.state.offset = this.state.rows.length;
      this.state.error = null;
      this.state.updatedAt = new Date();
      ok = true;
    } catch (e) {
      const msg = String((e && e.message) || e);
      this.state.error = {
        title: /Failed to fetch|NetworkError|aborted|AbortError|Load failed/i.test(msg)
          ? '无法连接后端，请确认 MoRay 后端已启动（双击 启动MoRay.bat）'
          : '读取审计记录失败',
        hint: '若后端未启动：双击工程根目录的「启动MoRay.bat」；已启动请确认端口与页面一致（默认 127.0.0.1:8000）。',
        detail: msg
      };
      if (!append) {
        if (!this.state.rows.length) { this.state.total = 0; }
      }
    } finally {
      this.state.loading = false;
      this._setRefreshing(false);
      this.render();
    }
    if (!ok && append) showNotification('加载更多失败', String(this.state.error && this.state.error.detail || '').slice(0, 120), 'error', 3000);
  },

  /** 渲染整条时间轴（分组 + 卡片 + 统计 + 筛选项目；保持展开项）
   * @returns {void} */
  render() {
    const panel = this._ensurePanel();
    if (!panel) return;
    const list = panel.querySelector('#timelineList');
    if (!list) return;
    this._renderError();
    this._updateFilterOptions();
    const rows = this.getFilteredRows();
    if (!this.state.rows.length) {
      list.innerHTML = this.state.error ? this._offlineHtml() : this._emptyHtml();
    } else if (!rows.length) {
      list.innerHTML = '<div class="flex flex-col items-center justify-center py-14 text-text-tertiary">' +
        '<i data-lucide="list-filter" class="w-8 h-8 opacity-40 mb-2"></i>' +
        '<div class="text-[11px] text-text-secondary mb-1">当前筛选条件下无记录</div>' +
        '<div class="text-[10px]">已加载 ' + this.state.rows.length + ' 条，可切换筛选或刷新</div></div>';
    } else {
      const groups = [];
      const index = {};
      rows.forEach(r => {
        const d = this._parseTs(r.ts);
        const key = d ? this._dayKey(d) : 'unknown';
        if (index[key] == null) { index[key] = groups.length; groups.push({ key: key, ts: r.ts, rows: [] }); }
        groups[index[key]].rows.push(r);
      });
      list.innerHTML = groups.map(g => this._renderDayGroup(g)).join('');
    }
    this._updateStats(rows.length);
    this._updateFooter();
    refreshIcons();
    if (this.state.expandedId != null) {
      const stillThere = rows.some(r => Number(r.id) === Number(this.state.expandedId));
      if (stillThere) this._expand(this.state.expandedId, true);
      else this.state.expandedId = null;
    }
    this._syncCards();
  },

  /** 渲染一天的组（日期标签 + 竖线 + 该天卡片）
   * @param {{key:string, ts:string, rows:Array}} group - 分组数据
   * @returns {string} HTML
   * @private */
  _renderDayGroup(group) {
    const label = this.formatDateGroup(group.ts);
    return '' +
      '<section class="tl-day">' +
        '<div class="sticky top-0 z-[1] -mx-1 px-1 py-1 mb-1 flex items-center gap-2 bg-surface-deep/95 backdrop-blur-sm rounded">' +
          '<span class="w-1.5 h-1.5 rounded-full bg-brand-cyan/80 flex-shrink-0"></span>' +
          '<span class="text-[11px] font-medium text-text-secondary">' + escapeHtml(label) + '</span>' +
          '<span class="text-[10px] text-text-tertiary">· ' + group.rows.length + ' 条</span>' +
          '<span class="flex-1 h-px bg-line-ghost/40"></span>' +
        '</div>' +
        '<div class="relative pl-7">' +
          '<span class="absolute left-[11px] top-3 bottom-3 w-px bg-brand-cobalt/30" aria-hidden="true"></span>' +
          '<div class="space-y-2">' + group.rows.map(r => this.renderEventCard(r)).join('') + '</div>' +
        '</div>' +
      '</section>';
  },

  /** 渲染单条事件卡片（折叠态 + 空详情容器）
   * @param {Object} row - 审计记录
   * @returns {string} HTML
   */
  renderEventCard(row) {
    const st = this.statusInfo(row.status);
    const isOpen = Number(this.state.expandedId) === Number(row.id);
    const summary = this._truncate(String(row.args_summary || ''), 120);
    const approved = Number(row.approved) === 1;
    return '' +
      '<div class="relative">' +
        '<span class="absolute -left-7 top-3 w-6 h-6 rounded-full border bg-surface-panel flex items-center justify-center ' + st.node + '">' +
          '<i data-lucide="' + this.toolIcon(row.tool) + '" class="w-3 h-3"></i>' +
        '</span>' +
        '<div class="tl-card rounded-lg border bg-surface-card/50 hover:border-brand-cobalt/40 transition-colors cursor-pointer ' +
          (isOpen ? 'border-brand-cobalt/50' : 'border-line-ghost/50') + '"' +
          ' data-tl-id="' + Number(row.id) + '" tabindex="0" role="button" aria-expanded="' + (isOpen ? 'true' : 'false') + '">' +
          '<div class="px-3 py-2.5">' +
            '<div class="flex items-center gap-2 mb-1">' +
              '<span class="text-xs font-medium text-text-primary">' + escapeHtml(this.toolLabel(row.tool)) + '</span>' +
              '<span class="text-[9px] px-1.5 py-0.5 rounded border ' + st.badge + '">' + escapeHtml(st.label) + '</span>' +
              '<span class="ml-auto font-mono text-[10px] text-text-tertiary">' + escapeHtml(this._timeOnly(row.ts)) + '</span>' +
              '<i data-lucide="chevron-down" class="tl-caret w-3 h-3 text-text-tertiary transition-transform' + (isOpen ? ' rotate-180' : '') + '"></i>' +
            '</div>' +
            '<div class="font-mono text-[10px] text-text-tertiary break-all leading-relaxed">' + escapeHtml(summary || '（无参数摘要）') + '</div>' +
            '<div class="flex items-center gap-3 mt-1.5 text-[10px]">' +
              '<span class="text-text-tertiary">耗时 <span class="font-mono">' + escapeHtml(this.formatMs(row.ms)) + '</span></span>' +
              (approved
                ? '<span class="text-success">已审批</span>'
                : '<span class="text-text-tertiary">未审批/自动</span>') +
              '<span class="ml-auto font-mono text-[9px] opacity-70">#' + Number(row.id) + '</span>' +
            '</div>' +
          '</div>' +
          '<div class="overflow-hidden" data-tl-detail="' + Number(row.id) + '" style="max-height:0"></div>' +
        '</div>' +
      '</div>';
  },

  /** 渲染详情展开区（完整参数摘要 / before-after diff / detail / 时间戳 / 记录 ID / 修改并重放按钮）
   * @param {Object} row - 审计记录
   * @returns {string} HTML
   */
  renderDetail(row) {
    const st = this.statusInfo(row.status);
    const detailText = String(row.detail || '');
    const diffHtml = this._diffHtml(row, detailText);
    // 可重放的状态：失败/超时/安全拒绝/待审批（成功和已拒绝不提供重放）
    const replayable = ['error', 'timeout', 'rejected', 'needs_approval'].indexOf(String(row.status)) >= 0;
    const hasSnapshot = row.snapshot_id != null && Number(row.snapshot_id) > 0;
    return '' +
      '<div class="border-t border-line-ghost/40 bg-surface-panel/40 px-3 py-2.5 space-y-2">' +
        '<div>' +
          '<div class="text-[10px] text-text-tertiary mb-1">参数摘要（完整）</div>' +
          '<div class="font-mono text-xs text-text-secondary bg-surface-deep rounded-lg border border-line-ghost/50 p-2 break-all whitespace-pre-wrap">' +
            escapeHtml(row.args_summary || '（无）') + '</div>' +
        '</div>' +
        (diffHtml
          ? '<div><div class="text-[10px] text-text-tertiary mb-1">变更内容（before → after）</div>' + diffHtml + '</div>'
          : '') +
        '<div>' +
          '<div class="text-[10px] text-text-tertiary mb-1">执行详情</div>' +
          '<div class="font-mono text-xs ' + (String(row.status) === 'error' ? 'text-danger' : 'text-text-secondary') +
            ' bg-surface-deep rounded-lg border border-line-ghost/50 p-2 break-all whitespace-pre-wrap">' +
            (detailText ? escapeHtml(detailText) : '<span class="text-text-tertiary">（无附加信息）</span>') + '</div>' +
        '</div>' +
        '<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-text-tertiary">' +
          '<span>工具 <span class="font-mono text-text-secondary">' + escapeHtml(String(row.tool || '')) + '</span></span>' +
          '<span>耗时 <span class="font-mono text-text-secondary">' + escapeHtml(this.formatMs(row.ms)) + '</span></span>' +
          '<span>时间 <span class="font-mono text-text-secondary">' + escapeHtml(String(row.ts || '')) + '</span></span>' +
          '<span>状态 <span class="' + st.node.split(' ')[0] + '">' + escapeHtml(st.label) + '</span></span>' +
          '<span>记录 <span class="font-mono text-text-secondary">#' + Number(row.id) + '</span></span>' +
        '</div>' +
        (replayable && hasSnapshot
          ? '<div class="pt-1 border-t border-line-ghost/30">' +
              '<button data-tl-replay="' + Number(row.id) + '"' +
                ' class="btn-primary px-3 py-1.5 rounded-lg text-[11px] flex items-center gap-1.5 w-full justify-center">' +
                '<i data-lucide="rotate-ccw" class="w-3.5 h-3.5"></i>' +
                '修改并重放此步骤' +
              '</button>' +
              '<div class="text-[9px] text-text-tertiary text-center mt-1">重放将经过完整安全校验（路径防护 + 副作用审批）</div>' +
            '</div>'
          : '') +
        (replayable && !hasSnapshot
          ? '<div class="pt-1 border-t border-line-ghost/30">' +
              '<div class="flex items-center justify-center gap-1.5 text-[10px] text-text-tertiary py-1.5">' +
                '<i data-lucide="info" class="w-3 h-3"></i>' +
                '该记录无事件快照，暂不支持重放（新产生的记录将自动支持）' +
              '</div>' +
            '</div>'
          : '') +
      '</div>';
  },

  /* ===================== Kernel 第二阶段：事件溯源重放 ===================== */

  /** 判断重放对话框是否打开
   * @returns {boolean}
   * @private */
  _replayDialogOpen() {
    return !!document.getElementById('tlReplayDialog');
  },

  /** 打开重放对话框（修改参数并重放历史工具调用）
   * @param {Object} row - 审计记录
   * @returns {void} */
  openReplayDialog(row) {
    if (this._replayDialogOpen()) this.closeReplayDialog();
    this.state.replayRow = row;
    let argsJson = '{}';
    const isSideEffect = ['write_file', 'create_file', 'move_file', 'run_command', 'edit_file'].indexOf(String(row.tool)) >= 0;
    const dialog = document.createElement('div');
    dialog.id = 'tlReplayDialog';
    dialog.className = 'fixed inset-0 z-[9600] flex items-center justify-center bg-black/60 backdrop-blur-sm';
    dialog.innerHTML = '' +
      '<div class="w-[520px] max-w-[92vw] max-h-[85vh] flex flex-col bg-surface-deep rounded-xl border border-line-ghost shadow-2xl">' +
        '<div class="flex items-center gap-2 px-4 py-3 border-b border-line-ghost/60 flex-shrink-0">' +
          '<i data-lucide="rotate-ccw" class="w-4 h-4 text-brand-cyan flex-shrink-0"></i>' +
          '<div class="min-w-0 flex-1">' +
            '<div class="text-sm font-medium text-text-primary">修改并重放</div>' +
            '<div class="text-[10px] text-text-tertiary">' + escapeHtml(this.toolLabel(row.tool)) + ' · 记录 #' + Number(row.id) + '</div>' +
          '</div>' +
          '<button id="tlReplayClose" class="btn-ghost p-1.5 rounded-lg border border-line-ghost" aria-label="关闭">' +
            '<i data-lucide="x" class="w-4 h-4"></i>' +
          '</button>' +
        '</div>' +
        '<div class="flex-1 overflow-y-auto p-4 space-y-3">' +
          '<div>' +
            '<div class="text-[10px] text-text-tertiary mb-1">工具名（只读）</div>' +
            '<div class="font-mono text-xs text-text-secondary bg-surface-panel rounded-lg border border-line-ghost/50 px-3 py-2">' +
              escapeHtml(String(row.tool || '')) + '</div>' +
          '</div>' +
          '<div>' +
            '<div class="text-[10px] text-text-tertiary mb-1">原始参数摘要</div>' +
            '<div class="font-mono text-[11px] text-text-tertiary bg-surface-panel/50 rounded-lg border border-line-ghost/30 px-3 py-2 break-all whitespace-pre-wrap max-h-24 overflow-auto">' +
              escapeHtml(String(row.args_summary || '（无）')) + '</div>' +
          '</div>' +
          '<div>' +
            '<div class="text-[10px] text-text-tertiary mb-1">重放参数（JSON，可修改）</div>' +
            '<textarea id="tlReplayArgs" class="w-full font-mono text-xs bg-surface-deep rounded-lg border border-line-ghost/50 px-3 py-2 text-text-secondary resize-y" ' +
              'rows="8" spellcheck="false" placeholder="{\n  &quot;path&quot;: &quot;example.txt&quot;,\n  &quot;content&quot;: &quot;hello&quot;\n}">' + escapeHtml(argsJson) + '</textarea>' +
            '<div id="tlReplayArgsErr" class="hidden text-[10px] text-danger mt-1">JSON 格式错误，请检查括号和引号</div>' +
          '</div>' +
          (isSideEffect
            ? '<div class="rounded-lg border border-warning/30 bg-warning/5 px-3 py-2">' +
                '<div class="flex items-start gap-2">' +
                  '<i data-lucide="alert-triangle" class="w-3.5 h-3.5 text-warning flex-shrink-0 mt-0.5"></i>' +
                  '<div class="text-[10px] text-text-secondary leading-relaxed">' +
                    '这是副作用工具（写文件/执行命令等），重放需要勾选下方「确认执行」才会真正执行。' +
                    '所有操作仍经过完整的路径越界防护和安全校验。' +
                  '</div>' +
                '</div>' +
              '</div>' +
              '<label class="flex items-center gap-2 cursor-pointer select-none">' +
                '<input type="checkbox" id="tlReplayApprove" class="w-3.5 h-3.5 accent-brand-cobalt">' +
                '<span class="text-[11px] text-text-secondary">确认执行此副作用操作</span>' +
              '</label>'
            : '') +
        '</div>' +
        '<div class="flex items-center gap-2 px-4 py-3 border-t border-line-ghost/60 flex-shrink-0">' +
          '<button id="tlReplayCancel" class="btn-ghost px-4 py-1.5 rounded-lg text-[11px] border border-line-ghost">取消</button>' +
          '<div class="flex-1"></div>' +
          '<button id="tlReplayRun" class="btn-primary px-4 py-1.5 rounded-lg text-[11px] flex items-center gap-1.5">' +
            '<i data-lucide="play" class="w-3.5 h-3.5"></i>执行重放' +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(dialog);
    refreshIcons();
    const closeBtn = dialog.querySelector('#tlReplayClose');
    if (closeBtn) closeBtn.addEventListener('click', () => this.closeReplayDialog());
    const cancelBtn = dialog.querySelector('#tlReplayCancel');
    if (cancelBtn) cancelBtn.addEventListener('click', () => this.closeReplayDialog());
    dialog.addEventListener('click', (e) => { if (e.target === dialog) this.closeReplayDialog(); });
    const runBtn = dialog.querySelector('#tlReplayRun');
    if (runBtn) runBtn.addEventListener('click', () => this.doReplay());
    const ta = dialog.querySelector('#tlReplayArgs');
    if (ta) ta.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); this.doReplay(); }
    });
  },

  /** 执行重放（调用后端 /api/agent/replay）
   * @returns {Promise<void>} */
  async doReplay() {
    const dialog = document.getElementById('tlReplayDialog');
    const row = this.state.replayRow;
    if (!dialog || !row) return;
    const ta = dialog.querySelector('#tlReplayArgs');
    const errEl = dialog.querySelector('#tlReplayArgsErr');
    const runBtn = dialog.querySelector('#tlReplayRun');
    if (!ta) return;
    // 解析 JSON
    let modifiedArgs = {};
    const raw = ta.value.trim();
    if (raw) {
      try {
        modifiedArgs = JSON.parse(raw);
        if (typeof modifiedArgs !== 'object' || modifiedArgs === null || Array.isArray(modifiedArgs)) {
          throw new Error('必须是 JSON 对象');
        }
      } catch (e) {
        if (errEl) { errEl.classList.remove('hidden'); errEl.textContent = 'JSON 格式错误：' + String(e.message || e); }
        return;
      }
    }
    if (errEl) errEl.classList.add('hidden');
    // 防御性检查：snapshot_id 必须存在且有效
    if (!row.snapshot_id || Number(row.snapshot_id) <= 0) {
      showNotification('无法重放', '该记录没有关联的事件快照，暂不支持重放', 'warning', 3000);
      this.closeReplayDialog();
      return;
    }
    // 副作用工具需要确认
    const isSideEffect = ['write_file', 'create_file', 'move_file', 'run_command', 'edit_file'].indexOf(String(row.tool)) >= 0;
    const approved = isSideEffect ? !!(dialog.querySelector('#tlReplayApprove') && dialog.querySelector('#tlReplayApprove').checked) : true;
    if (isSideEffect && !approved) {
      showNotification('需要确认', '副作用工具请先勾选「确认执行」', 'warning', 2500);
      return;
    }
    if (runBtn) { runBtn.disabled = true; runBtn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>执行中…'; refreshIcons(); }
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 15000);
      let res;
      try {
        res = await fetch(agentBackendBase() + '/api/agent/replay', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            snapshot_id: row.snapshot_id,
            modified_args: modifiedArgs,
            approved: approved
          }),
          signal: ctrl.signal
        });
      } finally {
        clearTimeout(timer);
      }
      const j = await res.json().catch(() => null);
      if (!res.ok || !j) throw new Error((j && (j.message || j.error)) || ('后端返回 HTTP ' + res.status));
      const replayInfo = j.replay || {};
      const status = String(replayInfo.status || 'ok');
      if (j.ok && status === 'ok') {
        showNotification('重放成功', this.toolLabel(row.tool) + ' 重放执行成功（' + this.formatMs(replayInfo.ms) + '）', 'success', 3000);
      } else if (j.needsApproval) {
        showNotification('需要审批', '该操作需要审批后才能执行', 'warning', 3000);
      } else {
        showNotification('重放完成', '状态：' + status + (j.message ? '（' + String(j.message).slice(0, 60) + '）' : ''),
          status === 'rejected' ? 'warning' : 'error', 3500);
      }
      this.closeReplayDialog();
      this.load(false);
    } catch (e) {
      const msg = String((e && e.message) || e);
      showNotification('重放失败', msg.slice(0, 100), 'error', 3500);
    } finally {
      if (runBtn) { runBtn.disabled = false; runBtn.innerHTML = '<i data-lucide="play" class="w-3.5 h-3.5"></i>执行重放'; refreshIcons(); }
    }
  },

  /** 关闭重放对话框
   * @returns {void} */
  closeReplayDialog() {
    const dialog = document.getElementById('tlReplayDialog');
    if (dialog) dialog.remove();
    this.state.replayRow = null;
  },

  /* ===================== 展开/收起 ===================== */

  /** 切换展开/收起（手风琴：展开新卡片时自动收起上一张；再次点击同一张收起）
   * @param {number|string} id - 记录 id
   * @returns {void} */
  toggleExpand(id) {
    const nid = Number(id);
    if (this.state.expandedId != null && Number(this.state.expandedId) === nid) {
      this._collapse(nid);
      this.state.expandedId = null;
      this._syncCards();
      return;
    }
    if (this.state.expandedId != null) this._collapse(Number(this.state.expandedId));
    this.state.expandedId = nid;
    this._expand(nid);
    this._syncCards();
  },

  /** 展开某卡片（平滑高度动画；instant=true 直接展开不动画）
   * @param {number} id - 记录 id
   * @param {boolean} [instant] - 跳过动画
   * @returns {void}
   * @private */
  _expand(id, instant) {
    const card = document.querySelector('#timelineList [data-tl-id="' + id + '"]');
    if (!card) return;
    const box = card.querySelector('[data-tl-detail]');
    const row = this.state.rows.filter(r => Number(r.id) === id)[0];
    if (!box || !row) return;
    box.innerHTML = this.renderDetail(row);
    refreshIcons();
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (instant || reduce) { box.style.transition = ''; box.style.maxHeight = 'none'; return; }
    const h = box.scrollHeight;
    box.style.transition = 'max-height .28s ease-out';
    box.style.maxHeight = '0px';
    void box.offsetHeight;
    box.style.maxHeight = h + 'px';
    clearTimeout(box._tlTimer);
    box._tlTimer = setTimeout(() => {
      if (box.isConnected && box.style.maxHeight !== '0px') box.style.maxHeight = 'none';
    }, 340);
  },

  /** 收起某卡片（动画结束后清空内容，保持 DOM 轻量）
   * @param {number} id - 记录 id
   * @returns {void}
   * @private */
  _collapse(id) {
    const card = document.querySelector('#timelineList [data-tl-id="' + id + '"]');
    if (!card) return;
    const box = card.querySelector('[data-tl-detail]');
    if (!box) return;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    clearTimeout(box._tlTimer);
    if (reduce || box.style.maxHeight === '0px') { box.innerHTML = ''; box.style.transition = ''; box.style.maxHeight = '0px'; return; }
    const h = box.scrollHeight;
    box.style.transition = 'max-height .24s ease-out';
    box.style.maxHeight = h + 'px';
    void box.offsetHeight;
    box.style.maxHeight = '0px';
    box._tlTimer = setTimeout(() => {
      if (!box.isConnected) return;
      box.innerHTML = '';
      box.style.transition = '';
      box.style.maxHeight = '0px';
    }, 280);
  },

  /** 同步所有卡片的展开视觉态（边框高亮 / 箭头方向 / aria-expanded）
   * @returns {void}
   * @private */
  _syncCards() {
    const list = document.getElementById('timelineList');
    if (!list) return;
    list.querySelectorAll('[data-tl-id]').forEach(el => {
      const on = Number(this.state.expandedId) === Number(el.dataset.tlId);
      el.setAttribute('aria-expanded', on ? 'true' : 'false');
      el.classList.toggle('border-brand-cobalt/50', on);
      el.classList.toggle('border-line-ghost/50', !on);
      const caret = el.querySelector('.tl-caret');
      if (caret) caret.classList.toggle('rotate-180', on);
    });
  },

  /** 前端筛选（不发新请求）
   * @returns {Array<Object>} 过滤后的记录 */
  getFilteredRows() {
    const fs = String(this.state.filterStatus || 'all');
    const ft = String(this.state.filterTool || 'all');
    return this.state.rows.filter(r => {
      if (ft !== 'all' && String(r.tool) !== ft) return false;
      if (fs !== 'all' && String(r.status) !== fs) return false;
      return true;
    });
  },

  /* ===================== 自动刷新 ===================== */

  /** 开启自动刷新（每 10 秒一次；document.hidden 时跳过）
   * @returns {void} */
  startAutoRefresh() {
    this.stopAutoRefresh();
    this.state.refreshTimer = setInterval(() => {
      if (document.hidden) return;
      if (!this.state.open || this.state.loading) return;
      this.load(false);
    }, this.AUTO_REFRESH_MS);
  },

  /** 停止自动刷新（只清定时器；开关状态保留，面板重开时按开关恢复） */
  stopAutoRefresh() {
    if (this.state.refreshTimer) { clearInterval(this.state.refreshTimer); }
    this.state.refreshTimer = null;
  },

  /* ===================== 工具栏 / 状态渲染 ===================== */

  /** 状态筛选/工具筛选下拉框（工具项从已加载数据动态去重提取；保留当前选择）
   * @returns {void}
   * @private */
  _updateFilterOptions() {
    const panel = document.getElementById('timelinePanel');
    if (!panel) return;
    const statusSel = panel.querySelector('#timelineStatusSel');
    if (statusSel) {
      const known = {};
      this.STATUS_FILTERS.forEach(o => { known[o.value] = true; });
      const extra = [];
      this.state.rows.forEach(r => {
        const s = String(r.status || '');
        if (s && !known[s]) { known[s] = true; extra.push({ value: s, label: s }); }
      });
      const items = this.STATUS_FILTERS.concat(extra);
      const sig = items.map(o => o.value).join('|');
      if (statusSel.dataset.tlSig !== sig) {
        statusSel.dataset.tlSig = sig;
        statusSel.innerHTML = items.map(o =>
          '<option value="' + escapeHtml(o.value) + '">' + escapeHtml(o.label) + '</option>').join('');
      }
      if (statusSel.value !== this.state.filterStatus) statusSel.value = this.state.filterStatus;
    }
    const toolSel = panel.querySelector('#timelineToolSel');
    if (toolSel) {
      const seen = {};
      this.state.rows.forEach(r => { const t = String(r.tool || ''); if (t) seen[t] = true; });
      const tools = Object.keys(seen).sort((a, b) => this.toolLabel(a).localeCompare(this.toolLabel(b), 'zh-Hans-CN'));
      const sig = tools.join('|');
      if (toolSel.dataset.tlSig !== sig) {
        toolSel.dataset.tlSig = sig;
        toolSel.innerHTML = '<option value="all">全部工具</option>' + tools.map(t =>
          '<option value="' + escapeHtml(t) + '">' + escapeHtml(this.toolLabel(t)) + ' · ' + escapeHtml(t) + '</option>').join('');
      }
      if (toolSel.value !== this.state.filterTool) toolSel.value = this.filterTool;
    }
  },

  /** 统计行：显示 X / 共 Y 条（+ 筛选后条数 + 最近更新时间）
   * @param {number} filteredCount - 筛选后条数
   * @returns {void}
   * @private */
  _updateStats(filteredCount) {
    const panel = document.getElementById('timelinePanel');
    if (!panel) return;
    const stats = panel.querySelector('#timelineStats');
    if (stats) {
      const loaded = this.state.rows.length;
      const filtered = (typeof filteredCount === 'number') ? filteredCount : this.getFilteredRows().length;
      let text = '显示 ' + loaded + ' / 共 ' + this.state.total + ' 条';
      if (this.state.filterStatus !== 'all' || this.state.filterTool !== 'all') text += '（筛选后 ' + filtered + ' 条）';
      stats.textContent = text;
    }
    const upd = panel.querySelector('#timelineUpdated');
    if (upd) {
      upd.textContent = this.state.updatedAt
        ? '已更新 ' + this._timeOfDay(this.state.updatedAt)
        : (this.state.loading ? '加载中…' : '');
    }
  },

  /** 底部状态：加载指示器 / 加载更多 / 已加载全部
   * @returns {void}
   * @private */
  _updateFooter() {
    const panel = document.getElementById('timelinePanel');
    if (!panel) return;
    const f = panel.querySelector('#timelineFooter');
    if (!f) return;
    if (this.state.error) { f.innerHTML = ''; return; }
    if (!this.state.rows.length) { f.innerHTML = this.state.loading ? this._dotsHtml() + '加载中…' : ''; return; }
    if (this.state.loading) { f.innerHTML = this._dotsHtml() + '加载中…'; return; }
    const hasMore = this.state.rows.length < this.state.total;
    if (hasMore) {
      f.innerHTML = '<button id="timelineMoreBtn" class="btn-ghost px-3 py-1 rounded-lg border border-line-ghost text-[10px]">' +
        '加载更多（已加载 ' + this.state.rows.length + ' / ' + this.state.total + ' 条）</button>' +
        '<div class="mt-1 opacity-70">滚动到底部也会自动加载</div>';
      refreshIcons();
      return;
    }
    f.innerHTML = '<span class="inline-flex items-center gap-1.5"><i data-lucide="check-circle-2" class="w-3 h-3 opacity-60"></i>已加载全部记录</span>';
    refreshIcons();
  },

  /** 错误提示条（红底 + 排查建议 + 重试按钮）
   * @returns {void}
   * @private */
  _renderError() {
    const panel = document.getElementById('timelinePanel');
    if (!panel) return;
    const box = panel.querySelector('#timelineError');
    if (!box) return;
    if (!this.state.error) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    const msg = panel.querySelector('#timelineErrorMsg');
    const hint = panel.querySelector('#timelineErrorHint');
    if (msg) msg.textContent = this.state.error.title + (this.state.error.detail ? '（' + this.state.error.detail + '）' : '');
    if (hint) hint.textContent = this.state.error.hint;
    refreshIcons();
  },

  /** 刷新按钮旋转 + 禁用（加载中）
   * @param {boolean} on - 是否加载中
   * @returns {void}
   * @private */
  _setRefreshing(on) {
    const btn = document.getElementById('timelineRefreshBtn');
    if (!btn) return;
    btn.disabled = !!on;
    const svg = btn.querySelector('svg');
    if (svg) svg.classList.toggle('animate-spin', !!on);
    const i = btn.querySelector('i[data-lucide]');
    if (i) i.classList.toggle('animate-spin', !!on);
  },

  /* ===================== 小组件 / 格式化 ===================== */

  /** 加载中的三点跳动指示器
   * @returns {string} HTML
   * @private */
  _dotsHtml() {
    return '<span class="inline-flex items-center gap-1 mr-2 align-middle">' +
      '<span class="w-1.5 h-1.5 rounded-full bg-brand-cobalt animate-bounce" style="animation-delay:0ms"></span>' +
      '<span class="w-1.5 h-1.5 rounded-full bg-brand-cobalt animate-bounce" style="animation-delay:150ms"></span>' +
      '<span class="w-1.5 h-1.5 rounded-full bg-brand-cobalt animate-bounce" style="animation-delay:300ms"></span>' +
      '</span>';
  },

  /** 首屏居中加载指示
   * @returns {string} HTML
   * @private */
  _loadingHtml() {
    return '<div class="flex flex-col items-center justify-center py-16 text-text-tertiary">' +
      this._dotsHtml() + '<div class="text-[11px] mt-1">正在读取审计记录…</div></div>';
  },

  /** 后端不可用时的中性占位（区别于「确无记录」）
   * @returns {string} HTML
   * @private */
  _offlineHtml() {
    return '<div class="flex flex-col items-center justify-center py-20 text-text-tertiary">' +
      '<i data-lucide="alert-triangle" class="w-8 h-8 opacity-40 mb-3"></i>' +
      '<div class="text-[11px] text-text-secondary mb-1">后端不可用：无法读取审计记录</div>' +
      '<div class="text-[10px] text-center leading-relaxed">启动后端后点上方「重试」重新加载</div></div>';
  },

  /** 空状态（无任何记录）
   * @returns {string} HTML
   * @private */
  _emptyHtml() {
    return '<div class="flex flex-col items-center justify-center py-20 text-text-tertiary">' +
      '<i data-lucide="inbox" class="w-10 h-10 opacity-40 mb-3"></i>' +
      '<div class="text-xs text-text-secondary mb-1">暂无工具调用记录</div>' +
      '<div class="text-[10px] text-center leading-relaxed">开启本机工具后，所有操作将在此记录<br>（含未审批与安全拒绝）</div></div>';
  },

  /** write_file / edit_file / create_file 的 before→after 结构化 diff
   * 优先复用 125_tools.js 的 buildApprovalDiff（行级 LCS）；detail 非结构化文本时返回 ''（纯文本展示）
   * @param {Object} row - 审计记录
   * @param {string} detailText - detail 原文
   * @returns {string} diff HTML 或 ''
   * @private */
  _diffHtml(row, detailText) {
    if (!detailText) return '';
    const tool = String(row.tool || '');
    if (['write_file', 'edit_file', 'create_file', 'rename_file', 'move_file'].indexOf(tool) < 0) return '';
    let before = '', after = '', found = false;
    const trimmed = detailText.trim();
    if (trimmed.charAt(0) === '{' || trimmed.charAt(0) === '[') {
      try {
        const obj = JSON.parse(trimmed);
        const b = obj && (obj.before_snippet != null ? obj.before_snippet : obj.before);
        const a = obj && (obj.after_snippet != null ? obj.after_snippet : obj.after);
        if (b != null || a != null) {
          before = b == null ? '' : String(b);
          after = a == null ? '' : String(a);
          found = true;
        }
      } catch (e) { /* 非 JSON：继续按文本标记解析 */ }
    }
    if (!found) {
      const mb = detailText.match(/before_snippet["']?\s*[:=]\s*(["'])([\s\S]*?)\1/);
      const ma = detailText.match(/after_snippet["']?\s*[:=]\s*(["'])([\s\S]*?)\1/);
      if (mb || ma) { before = mb ? mb[2] : ''; after = ma ? ma[2] : ''; found = true; }
    }
    if (!found) return '';
    if (typeof buildApprovalDiff === 'function') return buildApprovalDiff(before, after, 400);
    return this._plainDiff(before, after);
  },

  /** 无 buildApprovalDiff 时的降级 diff（红=变更前 / 绿=变更后）
   * @param {string} before - 变更前片段
   * @param {string} after - 变更后片段
   * @returns {string} HTML
   * @private */
  _plainDiff(before, after) {
    const block = (text, cls, sign) => String(text || '').split('\n')
      .map(l => '<div class="font-mono text-[10px] whitespace-pre-wrap break-all px-1 ' + cls + '">' +
        escapeHtml(sign + l) + '</div>').join('');
    return '<div class="rounded border border-line-ghost/50 bg-surface-panel/40 p-1.5 max-h-44 overflow-auto">' +
      block(before, 'text-danger bg-danger/10', '-') + block(after, 'text-success bg-success/10', '+') + '</div>';
  },

  /** 格式化耗时（ms → 12ms / 1.2s / 1m12s）
   * @param {number} ms - 毫秒
   * @returns {string} 展示文本 */
  formatMs(ms) {
    const v = Number(ms);
    if (!isFinite(v) || v <= 0) return '—';
    if (v < 1000) return v + 'ms';
    const sec = v / 1000;
    if (sec < 60) return sec.toFixed(sec < 10 ? 1 : 0) + 's';
    return Math.floor(sec / 60) + 'm' + Math.round(sec % 60) + 's';
  },

  /** 格式化时间分组标签（今天 9月17日 / 昨天 9月16日 / 9月15日 / 2025年12月31日）
   * @param {string} ts - 时间戳
   * @returns {string} 日期标签 */
  formatDateGroup(ts) {
    const d = this._parseTs(ts);
    if (!d) return String(ts || '').slice(0, 10) || '未知时间';
    const md = (d.getMonth() + 1) + '月' + d.getDate() + '日';
    const now = new Date();
    if (this._dayKey(d) === this._dayKey(now)) return '今天 ' + md;
    if (this._dayKey(d) === this._dayKey(new Date(now.getTime() - 86400000))) return '昨天 ' + md;
    if (d.getFullYear() === now.getFullYear()) return md;
    return d.getFullYear() + '年' + md;
  },

  /** 工具中文标签（未登记的工具直接显示原始名）
   * @param {string} tool - 工具名
   * @returns {string} 中文标签 */
  toolLabel(tool) {
    const t = String(tool || '');
    return this.TOOL_LABELS[t] || t || '未知工具';
  },

  /** 工具节点图标（未登记 → circle-dot）
   * @param {string} tool - 工具名
   * @returns {string} lucide 图标名 */
  toolIcon(tool) {
    return this.TOOL_ICONS[String(tool || '')] || 'circle-dot';
  },

  /** 状态元信息（未登记状态按灰色降级展示）
   * @param {string} status - 状态值
   * @returns {{label:string, node:string, badge:string}} 状态元信息 */
  statusInfo(status) {
    return this.STATUS_MAP[String(status || '')] || {
      label: String(status || '未知'),
      node: 'text-text-tertiary border-line-ghost',
      badge: 'bg-surface-panel text-text-tertiary border-line-ghost'
    };
  },

  /* ===================== 时间解析工具 ===================== */

  /** 解析后端时间戳（兼容 "2026-09-17 14:30:22" / "2026-09-17T14:30:22" / ISO）
   * @param {string} ts - 时间戳字符串
   * @returns {Date|null} 解析后的 Date，失败返回 null
   * @private */
  _parseTs(ts) {
    if (!ts) return null;
    const s = String(ts).trim();
    if (!s) return null;
    const normalized = s.replace(' ', 'T');
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return null;
    return d;
  },

  /** 日期的天级 key（用于按天分组）
   * @param {Date} d - 日期
   * @returns {string} YYYY-MM-DD
   * @private */
  _dayKey(d) {
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  },

  /** 提取时间部分（HH:MM）
   * @param {string} ts - 时间戳
   * @returns {string} HH:MM
   * @private */
  _timeOnly(ts) {
    const d = this._parseTs(ts);
    if (!d) return String(ts || '').slice(11, 16) || '';
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getHours()) + ':' + p(d.getMinutes());
  },

  /** Date → HH:MM:SS（用于"已更新"显示）
   * @param {Date} d - 日期
   * @returns {string} HH:MM:SS
   * @private */
  _timeOfDay(d) {
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  },

  /** 截断字符串（超出加省略号）
   * @param {string} s - 文本
   * @param {number} n - 最大长度
   * @returns {string} 截断结果
   * @private */
  _truncate(s, n) {
    const t = String(s == null ? '' : s);
    return t.length > n ? t.slice(0, n) + '…' : t;
  }
};

/* 暴露到 window：便于控制台/自动化测试直接调用 */
window.TimelineViewer = TimelineViewer;

// DOMContentLoaded 后初始化（或 typeof 守卫延迟初始化）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function () { TimelineViewer.init(); });
} else {
  TimelineViewer.init();
}
