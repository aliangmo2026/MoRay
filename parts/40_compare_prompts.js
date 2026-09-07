/* ============================================================
   模块：多模型对比（任务二.4）/ 提示词库（任务四）
   ============================================================ */

/* ===================== 多模型对比模式 ===================== */

/** 对比模式应用 */
const CompareApp = {
  /** 已选模型 @type {string[]} */
  selected: [],
  /** 各列控制器 @type {Array<AbortController>} */
  controllers: [],
  /** 生成中 @type {boolean} */
  running: false,

  /** 构建/重建对比模式 UI（接管静态原型）
   * @returns {void} */
  build() {
    const root = document.getElementById('chat-compare');
    if (!root) return;
    // [多模型对比修复] 读取持久化勾选时立即清洗（剔除占位串/失效模型），脏数据不再进入界面
    this.selected = this.cleanSelected(MoraySettings.get('compareModels'));
    root.innerHTML = `
      <div class="h-9 px-5 flex items-center justify-between border-b border-line-ghost/30 flex-shrink-0">
        <div class="flex items-center gap-3 text-[10px] text-text-tertiary" id="compareStatusText">勾选模型后输入提示词开始竞速</div>
        <div class="flex items-center gap-2 text-xs text-text-secondary">
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1" id="compareExportBtn" style="display:none">
            <i data-lucide="file-down" class="w-3 h-3"></i>导出报告
          </button>
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1" id="compareStopBtn" style="display:none">
            <i data-lucide="square" class="w-3 h-3"></i>停止
          </button>
          <span>同步滚动</span>
          <div class="toggle-track active" id="syncScrollToggle"><div class="toggle-thumb"></div></div>
        </div>
      </div>
      <div class="flex-1 flex min-h-0 divide-x divide-line-ghost" id="compareColumns"></div>
      <div class="px-5 pb-4 flex-shrink-0">
        <div class="core-input flex items-center gap-3 px-4 py-2.5 flex-wrap">
          <div class="flex items-center gap-3 text-xs text-text-secondary flex-shrink-0 flex-wrap" id="compareModelChecks"></div>
          <textarea rows="1" placeholder="对比模式输入... 同时发送给已勾选的模型（Enter 发送）" id="compareInput"
            class="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-tertiary resize-none focus:outline-none py-1 max-h-32"></textarea>
          <button class="w-9 h-9 rounded-full bg-brand-cobalt text-white flex items-center justify-center hover:bg-brand-cobalt-hover shadow-glow-cobalt transition-all flex-shrink-0 relative overflow-hidden" id="compareSendBtn" onclick="createRipple(event)">
            <i data-lucide="arrow-up" class="w-4 h-4"></i>
          </button>
        </div>
      </div>`;
    this.renderModelChecks();
    // 输入与发送
    const input = root.querySelector('#compareInput');
    on(root.querySelector('#compareSendBtn'), 'click', () => this.run(input.value));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.run(input.value); }
      input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 128) + 'px';
    });
    on(root.querySelector('#compareStopBtn'), 'click', () => this.stop());
    on(root.querySelector('#compareExportBtn'), 'click', () => exportCompareReport(this.lastResults, this.lastPrompt));
    // [零摆设] 同步滚动开关：真实实现双栏滚动联动 + 持久化（原骨架开关无绑定，属假开关）
    const syncT = root.querySelector('#syncScrollToggle');
    if (syncT) {
      syncT.classList.toggle('active', MoraySettings.get('compareSyncScroll') !== false);
      syncT.style.cursor = 'pointer';
      on(syncT, 'click', () => {
        const on = !syncT.classList.contains('active');
        syncT.classList.toggle('active', on);
        MoraySettings.set('compareSyncScroll', on);
        showNotification(on ? '已开启同步滚动' : '已关闭同步滚动', on ? '两栏内容将同步滚动' : '两栏独立滚动', 'info', 1400);
        if (on) this.attachSyncScroll();
        else this.detachSyncScroll();
      });
      if (MoraySettings.get('compareSyncScroll') !== false) this.attachSyncScroll();
    }
    refreshIcons();
  },

  /** 双栏滚动同步（互相同步 scrollTop，防循环）
   * @returns {void} */
  attachSyncScroll() {
    this.detachSyncScroll();
    const cols = document.getElementById('compareColumns');
    if (!cols) return;
    this._syncCols = cols.querySelectorAll('.col-messages');
    if (this._syncCols.length < 2) return;
    this._syncCols.forEach(col => {
      col.addEventListener('scroll', this._syncHandler = (e) => {
        const src = e.target;
        this._syncing = this._syncing || 0;
        if (this._syncing) return;
        this._syncing++;
        this._syncCols.forEach(other => { if (other !== src) other.scrollTop = src.scrollTop; });
        this._syncing--;
      });
    });
  },

  /** 解除双栏滚动同步
   * @returns {void} */
  detachSyncScroll() {
    if (this._syncCols) {
      this._syncCols.forEach(col => col.removeEventListener('scroll', this._syncHandler));
      this._syncCols = null;
    }
  },

  /** [多模型对比修复] 当前真实模型名列表（AI.models 的权威名）
   * @returns {string[]} */
  validModels() {
    return (AI && Array.isArray(AI.models) ? AI.models : [])
      .map(m => m && m.name)
      .filter(n => typeof n === 'string' && n.trim());
  },

  /** [多模型对比修复] 统一清洗勾选模型：
   * 剔除空名与历史占位串；模型列表非空时只保留真实存在的模型（失效/已删除模型不残留）。
   * @param {*} sel - 待清洗勾选（兼容非法/非数组持久化值）
   * @returns {string[]} */
  cleanSelected(sel) {
    const arr = Array.isArray(sel) ? sel : [];
    const valid = this.validModels();
    return arr.filter(n => typeof n === 'string' && n && n !== '（未检测到模型）' && (!valid.length || valid.includes(n)));
  },

  /** [多模型对比修复] 规范化选中集并回写存储：
   * 清洗后为空且有真实模型 → 默认选前 2 个；无模型 → 空数组；同时清除持久化脏数据。
   * @returns {string[]} */
  normalizeSelected() {
    let next = this.cleanSelected(this.selected);
    if (!next.length) {
      const valid = this.validModels();
      if (valid.length) next = valid.slice(0, 2);
    }
    this.selected = next;
    try { MoraySettings.set('compareModels', next); } catch (e) { /* 持久化失败不影响界面 */ }
    return next;
  },

  /** 渲染模型勾选区与列
   * @returns {void} */
  renderModelChecks() {
    const wrap = document.getElementById('compareModelChecks');
    if (!wrap) return;
    const models = this.validModels();
    const cols = document.getElementById('compareColumns');
    if (!models.length) {
      // [多模型对比修复] 模型未就绪：只显示禁用纯文字提示；
      // 不生成可勾选 checkbox、不 addColumn、不写 MoraySettings（占位串永不再进入界面与存储）
      this.selected = this.cleanSelected(this.selected);
      wrap.innerHTML = '<span class="text-xs text-text-tertiary select-none" style="opacity:.75">未检测到可用模型，请先连接 Ollama / 云端 API</span>';
      if (cols) cols.innerHTML = '';
      this.__lastModelsKey = '';
      return;
    }
    const clean = this.normalizeSelected();
    const allowed = new Set(clean);
    wrap.innerHTML = models.map(name => `
      <label class="flex items-center gap-1.5 cursor-pointer">
        <input type="checkbox" data-compare-model="${escapeHtml(name)}" ${allowed.has(name) ? 'checked' : ''} class="accent-brand-cobalt w-3.5 h-3.5">
        <span class="beacon-dot" style="width:6px;height:6px"></span>
        <span title="${escapeHtml(name)}">${escapeHtml(uniqueModelName(name))}</span>
      </label>`).join('');
    wrap.querySelectorAll('[data-compare-model]').forEach(cb => {
      cb.addEventListener('change', () => {
        const name = cb.dataset.compareModel;
        if (cb.checked) { if (!this.selected.includes(name)) this.selected.push(name); this.addColumn(name); }
        else {
          this.selected = this.selected.filter(n => n !== name);
          const col = document.querySelector(`#compareColumns [data-col-model="${CSS.escape(name)}"]`);
          if (col) col.remove();
        }
        MoraySettings.set('compareModels', this.selected);
      });
    });
    if (cols) {
      cols.innerHTML = '';
      this.selected.forEach(name => this.addColumn(name));
    }
    this.__lastModelsKey = models.join('\u0001');
  },

  /** 添加一列（模型通道）
   * @param {string} model - 模型名
   * @returns {void} */
  addColumn(model) {
    const cols = document.getElementById('compareColumns');
    if (!cols || cols.querySelector(`[data-col-model="${CSS.escape(model)}"]`)) return;
    const sec = document.createElement('section');
    sec.className = 'flex-1 flex flex-col min-w-0';
    sec.dataset.colModel = model;
    sec.innerHTML = `
      <div class="h-10 px-4 flex items-center gap-2 border-b border-line-ghost/50 flex-shrink-0">
        <span class="beacon-dot"></span>
        <span class="text-sm font-medium text-text-primary truncate" title="${escapeHtml(model)}">${escapeHtml(uniqueModelName(model))}</span>
      </div>
      <div class="flex-1 overflow-y-auto p-4 space-y-3 col-messages">
        <div class="text-center text-[11px] text-text-tertiary py-8">等待输入…</div>
      </div>
      <div class="h-auto px-4 py-2 border-t border-line-ghost/50 flex-shrink-0 col-stats" style="display:none">
        <div class="speed-race">
          <span class="text-[10px] text-text-tertiary w-8">速度</span>
          <div class="speed-bar-container"><div class="speed-bar-fill cobalt col-speed-fill" style="width:0%"></div></div>
          <span class="text-[10px] text-brand-cyan font-mono w-16 text-right col-speed-text">-- tok/s</span>
        </div>
        <div class="quality-score">
          <span class="text-[10px] text-text-tertiary w-8">质量</span>
          <div class="quality-bar"><div class="quality-bar-fill col-quality-fill" style="width:0%"></div></div>
          <span class="quality-score-text col-quality-text">--</span>
        </div>
        <div class="text-[10px] text-text-tertiary mt-1 col-summary"></div>
      </div>`;
    cols.appendChild(sec);
  },

  /**
   * 运行对比：向所有选中模型并发发送同一提示词
   * @param {string} prompt - 提示词
   * @returns {Promise<void>} */
  async run(prompt) {
    if (!prompt || !prompt.trim()) return;
    if (this.running) { showNotification('进行中', '上一轮对比尚未结束', 'warning'); return; }
    if (AI.backend === 'none') await AI.detectBackend();
    if (AI.backend === 'none') { showNotification('未连接后端', '请先在设置页配置 AI 服务', 'error', 4000); return; }
    // [多模型对比修复] 发送前最终校验：占位串/失效模型一律不得进入请求体（杜绝 400 invalid model name）
    const validNow = this.validModels();
    if (!validNow.length) {
      showNotification('未检测到可用模型', '未检测到可用模型，请先连接 Ollama/API 或勾选模型', 'warning');
      return;
    }
    const useModels = this.selected.filter(m => validNow.includes(m));
    if (!useModels.length) {
      showNotification('未选择模型', '请至少勾选一个模型', 'warning');
      return;
    }
    if (useModels.length !== this.selected.length) {
      this.selected = useModels;
      try { MoraySettings.set('compareModels', useModels); } catch (e) { /* 忽略 */ }
    }

    this.running = true;
    this.__stoppedByUser = false; // [阶段0.5] 停止标志：run 收尾不得覆盖 stop() 设置的状态文案
    const input = document.getElementById('compareInput');
    input.value = '';
    document.getElementById('compareStopBtn').style.display = '';
    document.getElementById('compareStatusText').textContent = '竞速中 · ' + useModels.length + ' 个模型并发';

    this.controllers = [];
    const results = [];
    // [体验优化] 对比页与普通对话统一走默认系统提示（优先级：用户自定义 rawSys > buildSystemPrompt；对比不带历史的设计不变）
    const rawSys = (typeof MoraySettings !== 'undefined' ? (MoraySettings.get('systemPrompt') || '') : '').trim();
    const sys = (typeof buildSystemPrompt === 'function') ? (rawSys || buildSystemPrompt()) : (rawSys || '');
    const msgs = sys ? [{ role: 'system', content: sys }, { role: 'user', content: prompt }] : [{ role: 'user', content: prompt }];
    // 先为所有列铺好本轮消息骨架：串行执行时用户也能看到全部待命列（不提前计时）
    const colHolders = new Map();
    useModels.forEach(model => {
      const sec = document.querySelector(`#compareColumns [data-col-model="${CSS.escape(model)}"]`);
      if (!sec) return;
      const msgBox = sec.querySelector('.col-messages');
      msgBox.innerHTML = `
        <div class="flex justify-end"><div class="max-w-[80%] rounded-xl px-3 py-2 bg-brand-cobalt/14 border border-brand-cobalt/20"><p class="text-xs text-text-primary">${escapeHtml(prompt)}</p></div></div>
        <div class="flex justify-start"><div class="max-w-[90%] rounded-xl px-3 py-2 bg-surface-card border border-line-ghost/60"><div class="md-body col-ai-content"></div></div></div>`;
      colHolders.set(model, { sec, contentEl: msgBox.querySelector('.col-ai-content') });
    });
    // [阶段0.5 C1] 待命列登记：串行停止时把"尚未轮到"的列明确标为已停止，不留空白占位
    this.__pendingCols = new Set(colHolders.keys());
    // 单模型一轮对比也正常（见 run() 顶部校验）；runOne 只负责本列自身请求，计时不含排队
    const runOne = async (model) => {
      if (this.__pendingCols) this.__pendingCols.delete(model);
      const holder = colHolders.get(model);
      if (!holder) return;
      const { sec, contentEl } = holder;
      const controller = new AbortController();
      this.controllers.push(controller);
      const started = performance.now(); // 串行场景：轮到本模型真正发起请求才开始计时
      let firstMs = 0, text = '';
      try {
        // [深度思考/常驻] 与普通对话完全一致的 think 决策（auto 档 qwen3.5 默认 false）；
        // keep_alive:30m 由 AI.chatStream 的 Ollama 分支统一附带，这里只透传 think
        const think = (typeof decideThinkFor === 'function') ? decideThinkFor(model, msgs) : false;
        const { promise } = AI.chatStream({
          model,
          messages: msgs,
          think,
          onChunk: throttle(({ content }) => {
            if (!content) return;
            if (!firstMs) firstMs = performance.now() - started;
            text += content;
            contentEl.innerHTML = renderMarkdown(text) + '<span class="stream-cursor"></span>';
          }, 100)
        });
        const result = await promise;
        contentEl.innerHTML = renderMarkdown(result.content || text);
        enhanceCodeBlocks(contentEl, false);
        const stats = result.stats || {};
        const quality = scoreQuality(result.content || text);
        const ms = stats.ms || (performance.now() - started);
        recordPerf(model, stats, true);
        results.push({ model, content: result.content, stats, quality });
        // 更新统计条
        const statsWrap = sec.querySelector('.col-stats');
        statsWrap.style.display = '';
        const tps = stats.tokPerSec || 0;
        sec.querySelector('.col-speed-text').textContent = (tps || '--') + ' tok/s';
        sec.querySelector('.col-speed-fill').style.width = Math.min(100, tps) + '%';
        sec.querySelector('.col-quality-fill').style.width = quality + '%';
        sec.querySelector('.col-quality-fill').style.background = 'linear-gradient(90deg, #5B8CFF, #3AD6E8)';
        sec.querySelector('.col-quality-text').textContent = quality;
        sec.querySelector('.col-summary').textContent = `${estimateTokens(result.content).chars} 字 · 用时 ${(ms / 1000).toFixed(1)}s · ${stats.tokens || 0} tokens`;
        updatePerfUI();
      } catch (e) {
        if (e && e.name === 'AbortError') { contentEl.innerHTML = '<span class="text-xs text-text-tertiary">已停止</span>'; }
        else {
          console.error('[MoRay] compare error:', e);
          contentEl.innerHTML = `<div class="text-xs text-danger">❌ ${escapeHtml(String(e.message || e))}</div>`;
          recordPerf(model, null, false);
          results.push({ model, error: String(e.message || e) });
        }
      }
    };
    // [单卡显存] 勾选全部为本地 Ollama 模型时改为串行：MAX_LOADED_MODELS=1 下避免反复换入换出；
    // 任一模型属云端后端则维持并发。串行队列等待不计入已完成列计时（started 在各自轮次才起表）。
    const allLocal = AI.backend === 'ollama' && useModels.every(m => (AI.models || []).some(x => x && x.name === m));
    if (allLocal && useModels.length > 1) {
      document.getElementById('compareStatusText').textContent = '本地模型将依次对比以避免显存抢占 · ' + useModels.length + ' 个';
      for (const m of useModels) {
        if (!this.running) break; // 用户点击停止后不再发起后续模型
        await runOne(m);
      }
    } else {
      document.getElementById('compareStatusText').textContent = '竞速中 · ' + useModels.length + ' 个模型并发';
      await Promise.all(useModels.map(runOne));
    }
    this.running = false;
    this.lastResults = results;
    this.lastPrompt = prompt;
    // [零摆设] 列重建后重新挂同步滚动
    if (MoraySettings.get('compareSyncScroll') !== false) this.attachSyncScroll();
    const exportBtn = document.getElementById('compareExportBtn');
    if (exportBtn) exportBtn.style.display = results.some(r => !r.error) ? '' : 'none';
    document.getElementById('compareStopBtn').style.display = 'none';
    // [阶段0.5 C1] 用户停止时保持"已停止"文案，不覆盖为"本轮完成"
    if (!this.__stoppedByUser) document.getElementById('compareStatusText').textContent = '本轮完成 · 可继续输入';
    if (results.length >= 2 && results.every(r => !r.error)) {
      const best = results.slice().sort((a, b) => (b.quality + Math.min(100, b.stats.tokPerSec || 0) / 2) - (a.quality + Math.min(100, a.stats.tokPerSec || 0) / 2))[0];
      showNotification('竞速结果', uniqueModelName(best.model) + ' 综合表现最佳（质量 ' + best.quality + '）', 'success', 3500);
    }
  },

  /** 停止全部生成
   * @returns {void} */
  stop() {
    this.controllers.forEach(c => { try { c.abort(); } catch (e) { /* 忽略 */ } });
    this.running = false;
    this.__stoppedByUser = true;
    const btn = document.getElementById('compareStopBtn');
    if (btn) btn.style.display = 'none';
    const st = document.getElementById('compareStatusText');
    if (st) st.textContent = '已停止';
    // [阶段0.5 C1] 串行队列中尚未轮到的列：明确显示"已停止（未开始）"，不留空白占位
    const pending = this.__pendingCols || new Set();
    this.__pendingCols = null;
    pending.forEach(model => {
      try {
        const sec = document.querySelector('#compareColumns [data-col-model="' + CSS.escape(model) + '"]');
        if (!sec) return;
        const contentEl = sec.querySelector('.col-ai-content');
        if (contentEl && !contentEl.firstChild) {
          contentEl.innerHTML = '<span class="text-xs text-text-tertiary">已停止（未开始）</span>';
        }
      } catch (e) { /* 忽略 */ }
    });
  }
};

/* ===================== [多模型对比修复] 自动重建勾选区 ===================== */

/** 对比页可见且模型列表确实变化时重建勾选区与列；
 * 正在竞速或结果未变时不重建，避免打断运行/清空已完成结果。
 * @returns {void} */
function refreshCompareChecks() {
  try {
    if (typeof CompareApp === 'undefined' || !CompareApp || CompareApp.running) return;
    const root = document.getElementById('chat-compare');
    const wrap = document.getElementById('compareModelChecks');
    if (!root || !wrap) return;
    // 仅当前停留在聊天页且对比模式可见时重建
    const pageChat = document.getElementById('page-chat');
    if (pageChat && (pageChat.classList.contains('hidden') || pageChat.offsetParent === null)) return;
    if (root.classList.contains('hidden')) return;
    const key = CompareApp.validModels().join('\u0001');
    if (key === CompareApp.__lastModelsKey && wrap.querySelector('[data-compare-model]')) return;
    CompareApp.renderModelChecks();
  } catch (e) { /* 自动重建失败不影响主流程 */ }
}

// 模型列表拉取成功（AI.detectBackend/listModels 成功分支）后自动重建
try { window.addEventListener('moray-models-updated', refreshCompareChecks); } catch (e) { /* 忽略 */ }
// 用户进入对比模式 / 切回聊天页时补一次刷新（覆盖模型列表更新发生在隐藏期间的场景）
try {
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const btn = t && t.closest ? t.closest('#chatModeSwitch button[data-mode="compare"], .nav-icon-btn[data-page="chat"]') : null;
    if (btn) setTimeout(refreshCompareChecks, 0);
  });
} catch (e) { /* 忽略 */ }

/** 启发式质量评分：长度 / 结构 / 代码块 / 列表
 * @param {string} text - 生成文本
 * @returns {number} 0-100 评分 */
function scoreQuality(text) {
  if (!text) return 0;
  const len = text.length;
  let score = 40;
  // 长度：200-2500 字最佳
  if (len >= 200) score += 10;
  if (len >= 600) score += 10;
  if (len > 4000) score -= 5;
  // 结构
  const headers = (text.match(/^#{1,4}\s/gm) || []).length;
  const lists = (text.match(/^\s*[-*\d]+[.、)]?\s/gm) || []).length;
  const codeBlocks = (text.match(/```/g) || []).length / 2;
  score += Math.min(12, headers * 4);
  score += Math.min(12, lists * 2);
  if (/```/.test(text)) score += 10;
  // 完整性（未截断：常见截断以逗号/空格结尾）
  if (!/[。！？.!?\n"`}$)]$/.test(text.trim())) score -= 6;
  return Math.max(5, Math.min(99, Math.round(score)));
}

/* ===================== [任务四] 提示词库 ===================== */

/** 提示词分类默认列表 */
const PROMPT_CATEGORIES = ['代码生成', '代码审查', '解释', '测试', '文档', '重构', '其他'];

/** 提示词库应用 */
const PromptsApp = {
  /** 全部提示词 @type {Array} */
  cache: [],
  /** 当前分类筛选 @type {string} */
  category: '全部',
  /** 搜索词 @type {string} */
  query: '',
  /** 收藏筛选 @type {boolean} */
  favoritesOnly: false,
  /** 排序 @type {string} */
  sortBy: 'useCount',

  /** 从库加载并渲染
   * @returns {Promise<void>} */
  async reload() {
    try { this.cache = await DB.listPrompts(); } catch (e) { this.cache = []; console.error(e); }
    this.render();
    this.renderSidebar();
    this.injectSlashCommands();
  },

  /** 提取 {{变量}} 占位符
   * @param {string} content - 提示词内容
   * @returns {string[]} 变量名列表（去重） */
  extractVariables(content) {
    const set = new Set();
    (String(content || '').match(/\{\{([^}]+)\}\}/g) || []).forEach(m => set.add(m.slice(2, -2).trim()));
    return Array.from(set);
  },

  /** 渲染分类侧栏
   * @returns {void} */
  renderSidebar() {
    const el = document.getElementById('promptCategoryList');
    if (!el) return;
    const counts = {};
    this.cache.forEach(p => { counts[p.category] = (counts[p.category] || 0) + 1; });
    const cats = Array.from(new Set([...PROMPT_CATEGORIES, ...Object.keys(counts)])).filter(Boolean);
    const row = (name, count) => `
      <div class="list-item text-sm flex items-center justify-between ${this.category === name ? 'active' : ''}" data-cat="${escapeHtml(name)}">
        <span class="truncate">${escapeHtml(name)}</span>
        <span class="text-[10px] text-text-tertiary flex-shrink-0 ml-2">${count}</span>
      </div>`;
    let html = row('全部', this.cache.length) + row('★ 收藏', this.cache.filter(p => p.isFavorite).length);
    cats.forEach(c => { if (counts[c]) html += row(c, counts[c]); });
    html += `<div class="list-item text-sm flex items-center gap-2 text-text-tertiary mt-2" id="addCategoryBtn"><i data-lucide="plus" class="w-3.5 h-3.5"></i><span>新建分类</span></div>`;
    el.innerHTML = html;
    refreshIcons();
    el.querySelectorAll('[data-cat]').forEach(item => {
      item.addEventListener('click', () => { this.category = item.dataset.cat; this.__limit = 50; this.render(); this.renderSidebar(); });
    });
    const addBtn = el.querySelector('#addCategoryBtn');
    if (addBtn) addBtn.addEventListener('click', () => this.newCategory());
  },

  /** 渲染提示词卡片网格
   * @returns {void} */
  render() {
    const grid = document.querySelector('#page-prompts .grid');
    if (!grid) return;
    grid.id = 'promptsGrid';
    let items = this.cache.slice();
    if (this.category === '★ 收藏') items = items.filter(p => p.isFavorite);
    else if (this.category !== '全部') items = items.filter(p => p.category === this.category);
    if (this.favoritesOnly) items = items.filter(p => p.isFavorite);
    if (this.query) {
      const q = this.query.toLowerCase();
      items = items.filter(p => (p.title || '').toLowerCase().includes(q) || (p.content || '').toLowerCase().includes(q) || (p.tags || []).some(t => t.toLowerCase().includes(q)));
    }
    items.sort((a, b) => this.sortBy === 'useCount' ? (b.useCount || 0) - (a.useCount || 0) : (b.updatedAt || 0) - (a.updatedAt || 0));
    // [夜间优化1.2] 增量分页：变高卡片列表滚动加载更多
    this.__hasMore = 0;
    if (this.__limit && items.length > this.__limit) {
      this.__hasMore = items.length - this.__limit;
      items = items.slice(0, this.__limit);
    }

    if (!items.length) {
      // [V3 P2.5] 统一空状态
      grid.innerHTML = `<div class="col-span-2">${renderEmptyState({
        icon: 'wand-sparkles', title: this.query ? '没有匹配的提示词' : '建立你的提示词库',
        desc: this.query ? '换个关键词试试' : '沉淀高频任务的优质指令，一键复用', actionText: this.query ? '' : '新建提示词'
      })}</div>`;
      bindEmptyAction(grid, () => this.openEditor());
      refreshIcons();
      return;
    }
    grid.innerHTML = items.map(p => {
      const vars = this.extractVariables(p.content);
      return `
      <div class="glass-card rounded-lg p-4 hover-lift cursor-pointer border border-line-ghost/60 relative group prompt-card" data-prompt-id="${p.id}">
        <div class="flex items-start justify-between mb-2">
          <h3 class="text-sm font-medium text-text-primary truncate">${escapeHtml(p.title)}</h3>
          <div class="flex items-center gap-1 flex-shrink-0">
            <button class="star-btn ${p.isFavorite ? 'starred' : ''}" data-op="fav" title="收藏"><i data-lucide="star" class="w-3.5 h-3.5"></i></button>
            ${vars.length ? `<span class="var-chip" title="包含变量">${vars.length} 个变量</span>` : ''}
            <span class="tag-pill bg-brand-violet/15 text-brand-violet">${escapeHtml(p.category || '其他')}</span>
          </div>
        </div>
        <p class="text-xs text-text-secondary leading-relaxed line-clamp-2 mb-3">${escapeHtml((p.description || p.content || '').slice(0, 90))}</p>
        <div class="flex items-center justify-between pt-2 border-t border-line-ghost/40">
          <div class="flex items-center gap-3 text-[10px] text-text-tertiary">
            <span class="flex items-center gap-1"><i data-lucide="zap" class="w-3 h-3"></i>${p.useCount || 0} 次</span>
            <span class="flex items-center gap-1"><i data-lucide="clock" class="w-3 h-3"></i>${relTime(p.updatedAt)}</span>
          </div>
          <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button class="tool-btn" data-op="use" title="使用"><i data-lucide="corner-down-left" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="copy" title="复制内容"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="edit" title="编辑"><i data-lucide="edit-3" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
      </div>`;
    }).join('');
    refreshIcons();
  },

  /** 网格事件委托
   * @returns {void} */
  bindEvents() {
    const grid = document.getElementById('promptsGrid') || document.querySelector('#page-prompts .grid');
    if (!grid) return;
    grid.addEventListener('click', (e) => {
      const card = e.target.closest('.prompt-card');
      if (!card) return;
      const id = card.dataset.promptId;
      const btn = e.target.closest('[data-op]');
      const op = btn ? btn.dataset.op : 'use';
      if (op === 'fav') this.toggleFav(id);
      else if (op === 'edit') this.openEditor(id);
      else if (op === 'delete') this.remove(id);
      else if (op === 'copy') this.copyContent(id);
      else if (op === 'use') this.usePrompt(id);
    });
    // 搜索框
    const search = document.querySelector('#page-prompts input[type="text"]');
    if (search) search.addEventListener('input', debounce(() => { this.query = search.value; this.__limit = 50; this.render(); }, 150));
    // 新增按钮
    const addBtn = document.querySelector('#page-prompts .btn-primary');
    if (addBtn) addBtn.addEventListener('click', () => this.openEditor());
    // [夜间优化4.2] 提示词市场
    const marketBtn = document.getElementById('promptMarketBtn');
    if (marketBtn) marketBtn.addEventListener('click', openPromptMarket);
    // [V2 功能10] A/B 测试
    const abBtn = document.getElementById('promptABBtn');
    if (abBtn) abBtn.addEventListener('click', () => openPromptABTest());
  },

  /** 切换收藏
   * @param {string} id - 提示词ID
   * @returns {Promise<void>} */
  async toggleFav(id) {
    const p = this.cache.find(x => x.id === id);
    if (!p) return;
    p.isFavorite = await DB.togglePromptFavorite(id);
    this.render(); this.renderSidebar();
  },

  /** 删除提示词
   * @param {string} id - 提示词ID
   * @returns {Promise<void>} */
  async remove(id) {
    const p = this.cache.find(x => x.id === id);
    showConfirm('删除提示词', `确定删除「${p ? p.title : ''}」？`, async () => {
      await DB.deletePrompt(id);
      this.cache = this.cache.filter(x => x.id !== id);
      this.render(); this.renderSidebar();
      showNotification('已删除', '提示词已删除', 'success', 1500);
    }, { danger: true, okText: '删除' });
  },

  /** 复制内容
   * @param {string} id - 提示词ID
   * @returns {Promise<void>} */
  async copyContent(id) {
    const p = this.cache.find(x => x.id === id);
    if (!p) return;
    const ok = await copyToClipboard(p.content);
    showNotification(ok ? '已复制' : '复制失败', p.title, ok ? 'success' : 'error', 1600);
  },

  /** 新建分类
   * @returns {void} */
  newCategory() {
    const box = showModal(`
      <div class="form-row">
        <label class="form-label">分类名称</label>
        <input type="text" id="newCatName" class="form-input" placeholder="例如：数据分析">
      </div>`,
      { title: '新建提示词分类', icon: 'folder-plus', footer: `<button class="btn-primary px-4 py-2 rounded-lg text-sm" id="newCatOk">创建</button>` });
    const input = box.querySelector('#newCatName');
    input.focus();
    const doCreate = () => {
      const name = input.value.trim();
      if (!name) return;
      if (!PROMPT_CATEGORIES.includes(name)) PROMPT_CATEGORIES.push(name);
      closeModal();
      this.category = name;
      this.render(); this.renderSidebar();
      showNotification('分类已创建', name, 'success', 1500);
    };
    on(box.querySelector('#newCatOk'), 'click', doCreate);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doCreate(); });
  },

  /** 打开编辑器（新建/编辑）
   * @param {string} [id] - 提示词ID，缺省为新建
   * @returns {void} */
  openEditor(id) {
    const p = id ? this.cache.find(x => x.id === id) : null;
    const cats = Array.from(new Set([...PROMPT_CATEGORIES, ...(p ? [p.category] : [])])).filter(Boolean);
    const box = showModal(`
      <div class="form-row form-inline">
        <div>
          <label class="form-label">标题 *</label>
          <input type="text" id="peTitle" class="form-input" value="${escapeHtml(p ? p.title : '')}" placeholder="例如：Python 代码优化专家">
        </div>
        <div>
          <label class="form-label">分类</label>
          <select id="peCategory" class="form-select">${cats.map(c => `<option ${p && p.category === c ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('')}</select>
        </div>
      </div>
      <div class="form-row">
        <label class="form-label">内容 *（支持 {{变量名}} 占位符）</label>
        <textarea id="peContent" class="form-textarea" style="min-height:160px" placeholder="你是一位资深工程师，请帮我 {{任务描述}}...">${escapeHtml(p ? p.content : '')}</textarea>
        <div class="form-hint">检测到的变量将以 <span class="var-chip">{{变量}}</span> 形式展示，使用时可填入</div>
        <div class="var-preview" id="pePreview"></div>
      </div>
      ${p && p.history && p.history.length ? `<div class="form-row">
        <label class="form-label">版本历史（${p.history.length} 版）</label>
        <div class="flex gap-2">
          <select id="peHistory" class="form-select" style="flex:1">${p.history.map((h, i) => `<option value="${i}">${new Date(h.savedAt).toLocaleString()} · ${escapeHtml(h.content.slice(0, 24))}…</option>`).join('')}</select>
          <button class="btn-ghost px-3 rounded-lg text-xs border border-line-ghost whitespace-nowrap" id="peRollback">回滚此版本</button>
        </div>
      </div>` : ''}
      <div class="form-row">
        <label class="form-label">描述</label>
        <input type="text" id="peDesc" class="form-input" value="${escapeHtml(p ? p.description : '')}" placeholder="一句话描述用途">
      </div>
      <div class="form-row">
        <label class="form-label">标签（逗号分隔）</label>
        <input type="text" id="peTags" class="form-input" value="${escapeHtml(p ? (p.tags || []).join(', ') : '')}" placeholder="python, 优化">
      </div>`,
      {
        title: p ? '编辑提示词' : '新增提示词', icon: 'file-text',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-pe-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="peSave">保存</button>`
      });
    on(box.querySelector('[data-pe-cancel]'), 'click', closeModal);
    // [阶段三] 变量实时预览：{{变量}} 高亮为插槽
    const contentTa = box.querySelector('#peContent');
    const previewEl = box.querySelector('#pePreview');
    const updatePreview = () => {
      previewEl.innerHTML = escapeHtml(contentTa.value || '（暂无内容）')
        .replace(/\{\{([^}]+)\}\}/g, '<span class="var-slot">&lt;&lt;$1&gt;&gt;</span>');
    };
    contentTa.addEventListener('input', updatePreview);
    updatePreview();
    // [夜间优化4.2] 版本回滚
    const histSel = box.querySelector('#peHistory');
    if (histSel) {
      on(box.querySelector('#peRollback'), 'click', () => {
        const h = p.history[parseInt(histSel.value, 10)];
        if (h) { contentTa.value = h.content; updatePreview(); showNotification('已回滚到历史版本', '记得点击保存生效', 'info', 2000); }
      });
    }
    on(box.querySelector('#peSave'), 'click', async () => {
      const title = box.querySelector('#peTitle').value.trim();
      const content = box.querySelector('#peContent').value.trim();
      if (!title || !content) { showNotification('请填写完整', '标题和内容为必填项', 'warning'); return; }
      const data = {
        title, content,
        category: box.querySelector('#peCategory').value,
        description: box.querySelector('#peDesc').value.trim(),
        tags: box.querySelector('#peTags').value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
      };
      if (p) {
        // [夜间优化4.2] 版本历史（保留最近5版）
        data.history = pushPromptHistory(p, data.content);
        await DB.updatePrompt(p.id, data);
      }
      else await DB.createPrompt(data);
      closeModal();
      await this.reload();
      showNotification(p ? '已更新' : '已创建', title, 'success', 1600);
    });
  },

  /** 使用提示词：有变量先弹表单，然后填入对话输入框
   * @param {string} id - 提示词ID
   * @returns {Promise<void>} */
  async usePrompt(id) {
    const p = this.cache.find(x => x.id === id);
    if (!p) return;
    await DB.updatePrompt(id, { useCount: (p.useCount || 0) + 1 });
    p.useCount = (p.useCount || 0) + 1;
    const vars = this.extractVariables(p.content);
    if (!vars.length) { this.insertToInput(p.content); return; }
    const box = showModal(`
      <div class="space-y-3">
        <p class="text-xs text-text-tertiary">该提示词包含 ${vars.length} 个变量，填写后将插入对话输入框</p>
        ${vars.map(v => `
          <div class="form-row">
            <label class="form-label">{{${escapeHtml(v)}}}</label>
            <input type="text" class="form-input var-input" data-var="${escapeHtml(v)}" placeholder="输入 ${escapeHtml(v)} 的值">
          </div>`).join('')}
      </div>`,
      { title: '填写变量', icon: 'variable',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-var-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="varOk">插入对话</button>` });
    on(box.querySelector('[data-var-cancel]'), 'click', closeModal);
    on(box.querySelector('#varOk'), 'click', () => {
      let out = p.content;
      box.querySelectorAll('.var-input').forEach(inp => {
        const v = inp.dataset.var;
        out = out.split('{{' + v + '}}').join(inp.value || '{{' + v + '}}');
      });
      closeModal();
      this.insertToInput(out);
    });
  },

  /** 将文本填入对话输入框并跳回对话页
   * @param {string} text - 文本
   * @returns {void} */
  insertToInput(text) {
    const input = document.getElementById('chatInputNormal');
    if (input) {
      input.value = text;
      input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 128) + 'px';
      if (typeof updateInputStats === 'function') updateInputStats(text);
      input.focus();
    }
    document.querySelector('.nav-icon-btn[data-page="chat"]').click();
  },

  /** 将提示词注入 / 快捷菜单（支持 /提示词名 直接调用）
   * @returns {void} */
  injectSlashCommands() {
    const menu = document.getElementById('slashMenu');
    if (!menu || menu.querySelector('[data-cmd^="prompt:"]')) {
      // 已注入则先清除旧条目再重注
      if (menu) menu.querySelectorAll('[data-cmd^="prompt:"]').forEach(n => n.remove());
      if (!menu) return;
    }
    const frag = document.createDocumentFragment();
    if (this.cache.length) {
      const head = document.createElement('div');
      head.className = 'slash-menu-header';
      head.textContent = '提示词（/名称 调用）';
      frag.appendChild(head);
    }
    this.cache.slice(0, 12).forEach(p => {
      const item = document.createElement('div');
      item.className = 'slash-menu-item';
      item.dataset.cmd = 'prompt:' + p.id;
      item.innerHTML = `
        <div class="slash-menu-item-icon" style="background:rgba(157,123,255,0.12);color:#9D7BFF;"><i data-lucide="file-text" class="w-4 h-4"></i></div>
        <div class="slash-menu-item-text">
          <div class="slash-menu-item-name">${escapeHtml(p.title)}</div>
          <div class="slash-menu-item-desc">${escapeHtml((p.description || p.content || '').slice(0, 30))}</div>
        </div>
        <div class="slash-menu-item-shortcut">/${escapeHtml(p.title.slice(0, 8))}</div>`;
      frag.appendChild(item);
    });
    menu.insertBefore(frag, menu.firstChild);
    refreshIcons();
  },

  /** / 指令选择接管：支持提示词条目
   * @param {HTMLElement} item - 菜单项
   * @returns {boolean} 是否已处理 */
  handleSlashSelection(item) {
    const cmd = item.dataset.cmd || '';
    if (!cmd.startsWith('prompt:')) return false;
    const id = cmd.slice(7);
    const menu = document.getElementById('slashMenu');
    if (menu) menu.classList.remove('active');
    const input = document.getElementById('chatInputNormal');
    if (input) input.value = '';
    this.usePrompt(id);
    return true;
  },

  /** 导出全部提示词
   * @returns {void} */
  exportAll() {
    if (!this.cache.length) { showNotification('无数据', '暂无提示词可导出', 'warning'); return; }
    downloadJson('moray_prompts.json', { version: 2, prompts: this.cache });
    showNotification('导出成功', `共 ${this.cache.length} 条提示词`, 'success');
  },

  /**
   * 导入提示词（支持 MoRay / ChatGPT / Claude 常见格式）
   * ChatGPT 格式：{title, prompt} 或 [{name, prompt}]
   * Claude 格式：{name, description, template}
   * @param {File} file - JSON 文件
   * @returns {Promise<void>} */
  async importFrom(file) {
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      let items = [];
      if (Array.isArray(data)) items = data;
      else if (data.prompts && Array.isArray(data.prompts)) items = data.prompts;
      else if (data.title || data.name) items = [data];
      let count = 0;
      for (const raw of items) {
        const title = raw.title || raw.name || '未命名';
        const content = raw.content || raw.prompt || raw.template || '';
        if (!content) continue;
        await DB.createPrompt({
          title, content,
          category: raw.category || '其他',
          description: raw.description || '',
          tags: raw.tags || []
        });
        count++;
      }
      await this.reload();
      showNotification('导入完成', `成功导入 ${count} 条提示词`, 'success');
    } catch (e) {
      console.error(e);
      showNotification('导入失败', '文件解析出错：' + e.message, 'error', 4000);
    }
  }
};


/* ===================== [V2 功能10] 提示词 A/B 测试 ===================== */

/** A/B 测试历史（持久化在 settings.abTests，最近50条） */
const ABTest = {
  /** 获取历史
   * @returns {Array} 记录 */
  history() { return MoraySettings.get('abTests') || []; },

  /**
   * 记录一次评分
   * @param {Object} rec - {aTitle, bTitle, model, winner:'a'|'b'|'tie', input}
   * @returns {Promise<void>} */
  async record(rec) {
    const list = this.history();
    list.unshift(Object.assign({ at: Date.now() }, rec));
    await MoraySettings.set('abTests', list.slice(0, 50));
  },

  /**
   * 统计某提示词的胜率
   * @param {string} title - 提示词标题
   * @returns {{wins:number, losses:number, ties:number, total:number, winRate:number}} 统计 */
  statsFor(title) {
    const list = this.history();
    let wins = 0, losses = 0, ties = 0;
    list.forEach(r => {
      if (r.aTitle !== title && r.bTitle !== title) return;
      if (r.winner === 'tie') { ties++; return; }
      const won = (r.winner === 'a' && r.aTitle === title) || (r.winner === 'b' && r.bTitle === title);
      won ? wins++ : losses++;
    });
    const total = wins + losses + ties;
    return { wins, losses, ties, total, winRate: wins + losses ? Math.round(wins / (wins + losses) * 100) : 0 };
  },

  /** 打开 A/B 测试面板
   * @returns {void} */
  open() {
    if (!PromptsApp.cache.length) { showNotification('暂无提示词', '请先创建两个提示词再测试', 'warning'); return; }
    const opts = PromptsApp.cache.map(p => `<option value="${p.id}">${escapeHtml(p.title)}</option>`).join('');
    const box = showModal(`
      <div class="form-row form-inline">
        <div><label class="form-label">变体 A</label><select id="abSelA" class="form-select">${opts}</select></div>
        <div><label class="form-label">变体 B</label><select id="abSelB" class="form-select">${opts}</select></div>
        <div><label class="form-label">模型</label><select id="abModel" class="form-select">
          ${AI.models.map(m => `<option value="${escapeHtml(m.name)}">${escapeHtml(uniqueModelName(m.name))}</option>`).join('') || '<option value="">默认</option>'}
        </select></div>
      </div>
      <div class="form-row">
        <label class="form-label">测试输入（将替换 {{测试内容}}/{{input}}/{{代码}} 变量，或附加到末尾）</label>
        <textarea id="abInput" class="form-textarea" style="min-height:70px" placeholder="例如：写一个防抖函数"></textarea>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="text-[11px] text-brand-cobalt mb-1 flex items-center justify-between">变体 A <span id="abStatA" class="text-text-tertiary"></span></div>
          <div class="chunk-item ab-col" id="abColA" style="min-height:180px;max-height:300px;overflow-y:auto">待运行</div>
        </div>
        <div>
          <div class="text-[11px] text-brand-violet mb-1 flex items-center justify-between">变体 B <span id="abStatB" class="text-text-tertiary"></span></div>
          <div class="chunk-item ab-col" id="abColB" style="min-height:180px;max-height:300px;overflow-y:auto">待运行</div>
        </div>
      </div>
      <div class="flex items-center justify-between mt-3">
        <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="abHistoryBtn">历史与胜率</button>
        <div class="flex gap-2">
          <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="abScoreTie" disabled>平局</button>
          <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-brand-cobalt/40 text-brand-cobalt" id="abScoreA" disabled>A 更好</button>
          <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-brand-violet/40 text-brand-violet" id="abScoreB" disabled>B 更好</button>
          <button class="btn-primary px-4 py-1.5 rounded-lg text-xs" id="abRunBtn">运行测试</button>
        </div>
      </div>`,
      { title: '提示词 A/B 测试', icon: 'flask-round', wide: true, footer: false });

    const getPrompt = (id) => PromptsApp.cache.find(p => p.id === id);
    const buildContent = (p, input) => {
      let out = p.content;
      ['{{测试内容}}', '{{input}}', '{{代码}}', '{{代码}}'].forEach(v => { out = out.split(v).join(input); });
      // 仍有未填变量时追加输入
      if (input && /\{\{/.test(out)) out += '\n\n' + input;
      return out;
    };

    let lastRun = null;
    on(box.querySelector('#abRunBtn'), 'click', async () => {
      if (AI.backend === 'none') await AI.detectBackend();
      if (AI.backend === 'none') { showNotification('未连接后端', 'A/B 测试需要真实 AI 后端', 'error', 3000); return; }
      const pA = getPrompt(box.querySelector('#abSelA').value);
      const pB = getPrompt(box.querySelector('#abSelB').value);
      const input = box.querySelector('#abInput').value.trim();
      const model = box.querySelector('#abModel').value || MoraySettings.get('defaultModel');
      if (!pA || !pB) return;
      const colA = box.querySelector('#abColA'), colB = box.querySelector('#abColB');
      colA.innerHTML = '<span class="stream-cursor"></span>'; colB.innerHTML = '<span class="stream-cursor"></span>';
      let textA = '', textB = '', doneA = false, doneB = false;
      ['abScoreA', 'abScoreB', 'abScoreTie'].forEach(id => box.querySelector('#' + id).disabled = true);
      const renderCols = () => {
        colA.innerHTML = renderMarkdown(textA) + (doneA ? '' : '<span class="stream-cursor"></span>');
        colB.innerHTML = renderMarkdown(textB) + (doneB ? '' : '<span class="stream-cursor"></span>');
      };
      const throttleA = throttle(renderCols, 120), throttleB = throttle(renderCols, 120);
      const runOne = async (p, onChunk) => {
        // [工具调用] A/B 对比看裸回答：显式关闭工具闭环；模型为测试显式指定 → 锁定不参与路由
        const g = Gateway.chatStream({ model, messages: [{ role: 'user', content: buildContent(p, input) }], bypassCache: true, noTools: true, _userPicked: true, onChunk });
        return g.promise;
      };
      lastRun = { aTitle: pA.title, bTitle: pB.title, model, input };
      showNotification('A/B 测试运行中', '两个变体并发请求（绕过缓存）', 'info', 2000);
      runOne(pA, c => { if (c.content) { textA += c.content; throttleA(); } }).then(() => { doneA = true; renderCols(); }).catch(e => { doneA = true; textA = '❌ ' + (e.message || e); renderCols(); });
      runOne(pB, c => { if (c.content) { textB += c.content; throttleB(); } }).then(() => { doneB = true; renderCols(); }).catch(e => { doneB = true; textB = '❌ ' + (e.message || e); renderCols(); });
      // 双方完成后解锁评分
      const waitDone = setInterval(() => {
        if (doneA && doneB) {
          clearInterval(waitDone);
          lastRun.aContent = textA; lastRun.bContent = textB;
          ['abScoreA', 'abScoreB', 'abScoreTie'].forEach(id => box.querySelector('#' + id).disabled = false);
          const sa = ABTest.statsFor(pA.title), sb = ABTest.statsFor(pB.title);
          box.querySelector('#abStatA').textContent = sa.total ? `胜率 ${sa.winRate}%` : '';
          box.querySelector('#abStatB').textContent = sb.total ? `胜率 ${sb.winRate}%` : '';
        }
      }, 400);
    });

    const score = (winner) => {
      if (!lastRun) return;
      ABTest.record(Object.assign({ winner }, lastRun));
      showNotification('评分已记录', winner === 'tie' ? '平局' : (winner === 'a' ? 'A' : 'B') + ' 更好', 'success', 1600);
    };
    on(box.querySelector('#abScoreA'), 'click', () => score('a'));
    on(box.querySelector('#abScoreB'), 'click', () => score('b'));
    on(box.querySelector('#abScoreTie'), 'click', () => score('tie'));
    on(box.querySelector('#abHistoryBtn'), 'click', () => {
      const list = ABTest.history();
      showModal(`<div class="space-y-2" style="max-height:60vh;overflow-y:auto">${list.length ? list.map(r => `
        <div class="chunk-item"><div class="flex justify-between mb-1">
          <span>${escapeHtml(r.aTitle)} <span class="text-text-tertiary">vs</span> ${escapeHtml(r.bTitle)}</span>
          <span class="${r.winner === 'a' ? 'text-brand-cobalt' : r.winner === 'b' ? 'text-brand-violet' : 'text-text-tertiary'}">${r.winner === 'a' ? 'A 胜' : r.winner === 'b' ? 'B 胜' : '平局'}</span></div>
        <div class="text-text-tertiary text-[10px]">${escapeHtml((r.input || '').slice(0, 50))} · ${relTime(r.at)} · ${escapeHtml(r.model || '')}</div></div>`).join('')
        : '<div class="text-xs text-text-tertiary text-center py-6">暂无测试记录</div>'}</div>`,
        { title: 'A/B 测试历史', icon: 'history', wide: true, footer: false });
    });
  },

  /** 导出测试报告
   * @returns {void} */
  exportReport() {
    const list = this.history();
    if (!list.length) { showNotification('无数据', '先运行几轮 A/B 测试', 'warning'); return; }
    let md = '# MoRay 提示词 A/B 测试报告\n\n> 导出时间：' + new Date().toLocaleString() + '\n\n| 时间 | 变体A | 变体B | 模型 | 结果 |\n|---|---|---|---|---|\n';
    list.forEach(r => {
      md += `| ${new Date(r.at).toLocaleString()} | ${r.aTitle} | ${r.bTitle} | ${r.model || '-'} | ${r.winner === 'a' ? 'A 胜' : r.winner === 'b' ? 'B 胜' : '平局'} |\n`;
    });
    // 各提示词胜率
    md += '\n## 胜率统计\n\n';
    const titles = Array.from(new Set(list.flatMap(r => [r.aTitle, r.bTitle])));
    titles.forEach(t => {
      const s = this.statsFor(t);
      md += `- **${t}**：${s.wins}胜 ${s.losses}负 ${s.ties}平（胜率 ${s.winRate}%）\n`;
    });
    downloadText('moray_ab_test_report.md', md, 'text/markdown;charset=utf-8');
    showNotification('报告已导出', 'moray_ab_test_report.md', 'success');
  }
};

/** 打开 A/B 测试入口（供按钮绑定） */
function openPromptABTest() { ABTest.open(); }
