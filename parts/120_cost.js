/* ============================================================
   [成本中心] CostEngine —— 价格表 + 精确成本核算（纯函数，不依赖 DOM）
   阶段一：模型价格表 / 估算与基线 / 金额格式化
   阶段四：预算告警与自动降级（设置持久化，被 Gateway 读取）
   ============================================================ */

/**
 * 模型价格表（每 1M token，单位：人民币元）
 * 注意：价格为默认值、可能变动、以各厂商官方定价为准；用户可在 设置→成本 中覆盖。
 * match 规则：模型名小写后，与任一前缀/别名做 startsWith 或全等匹配；'default' 为兜底档。
 */
const PRICE_TABLE = [
  { match: ['llama', 'qwen2.5', 'qwen3', 'gemma', 'mistral', 'phi', 'deepseek-coder', 'deepseek-r1:',
           'nomic', 'llava', 'bakllava', 'mxbai', 'bge', 'snowflake', 'tinyllama', 'smollm', 'granite'],
    prices: { input: 0, output: 0, cacheHit: 0 }, currency: 'CNY', note: '本地 Ollama 模型，本地推理免费' },
  { match: ['deepseek-chat', 'deepseek-v3', 'deepseek/'], prices: { input: 2, output: 8, cacheHit: 0.5 }, currency: 'CNY', note: 'DeepSeek V3 官方价（元/百万token）' },
  { match: ['deepseek-reasoner', 'deepseek-r1'], prices: { input: 4, output: 16, cacheHit: 1 }, currency: 'CNY', note: 'DeepSeek R1 官方价' },
  { match: ['qwen-plus', 'qwen-turbo', 'qwen-max', 'qwen-vl'], prices: { input: 0.8, output: 2, cacheHit: 0.2 }, currency: 'CNY', note: '通义千问（估算）' },
  { match: ['glm-4', 'glm-4v', 'glm-z1', 'glm-4.5'], prices: { input: 1, output: 2, cacheHit: 0.25 }, currency: 'CNY', note: '智谱 GLM 系列（估算）' },
  { match: ['moonshot', 'kimi'], prices: { input: 12, output: 24, cacheHit: 3 }, currency: 'CNY', note: 'Moonshot Kimi（估算）' },
  { match: ['gpt-4o'], prices: { input: 17.5, output: 70, cacheHit: 4.4 }, currency: 'CNY', note: 'GPT-4o（按官方价折合约¥估算）' },
  { match: ['gpt-4-turbo', 'gpt-4'], prices: { input: 21, output: 105, cacheHit: 21 }, currency: 'CNY', note: 'GPT-4 系列（估算）' },
  { match: ['gpt-3.5', 'gpt-4o-mini'], prices: { input: 1.4, output: 4.2, cacheHit: 0.35 }, currency: 'CNY', note: 'GPT-3.5/4o-mini（估算）' },
  { match: ['gpt-'], prices: { input: 17.5, output: 70, cacheHit: 4.4 }, currency: 'CNY', note: 'OpenAI 其他模型（默认档估算）' },
  { match: ['claude-3-5', 'claude-3', 'claude-sonnet'], prices: { input: 21, output: 105, cacheHit: 2.1 }, currency: 'CNY', note: 'Claude 3.5 Sonnet（估算）' },
  { match: ['claude'], prices: { input: 21, output: 105, cacheHit: 2.1 }, currency: 'CNY', note: 'Claude 系列（估算）' },
  { match: ['default'], prices: { input: 2, output: 8, cacheHit: 0.5 }, currency: 'CNY', note: '未匹配默认档（估算，可在设置中调整）' }
];

/** 成本中心命名空间（纯函数，不引用 DOM） */
const CostEngine = {
  /** 解析模型价格（含用户覆盖；Ollama 后端恒为 0）
   * @param {string} model - 模型名
   * @returns {{input:number, output:number, cacheHit:number, currency:string, note:string}} */
  resolvePrice(model) {
    const name = String(model || '').toLowerCase();
    const overrides = MoraySettings.get('priceOverrides') || {};
    if (overrides[name]) return Object.assign({ currency: 'CNY', note: '用户自定义价格' }, overrides[name]);
    for (const rule of PRICE_TABLE) {
      if (rule.match.includes('default')) continue;
      if (rule.match.some(m => name === m || name.startsWith(m))) {
        return { currency: rule.currency, note: rule.note, ...rule.prices };
      }
    }
    const def = PRICE_TABLE.find(r => r.match.includes('default'));
    return { currency: def.currency, note: def.note, ...def.prices };
  },

  /** 是否为本地模型（Ollama 后端或价格表本地档）
   * @param {string} model - 模型名
   * @returns {boolean} */
  isLocal(model) {
    if (AI && AI.backend === 'ollama') return true;
    const p = this.resolvePrice(model);
    return p.input === 0 && p.output === 0;
  },

  /** 估算实际花费（元，4位小数）
   * @param {string} model - 模型名
   * @param {number} promptTokens - 输入 token
   * @param {number} completionTokens - 输出 token
   * @param {number} [cacheTokens] - 缓存命中按缓存价计费的输入 token
   * @returns {number} */
  estimateCost(model, promptTokens, completionTokens, cacheTokens) {
    const p = this.resolvePrice(model);
    cacheTokens = Math.max(0, Number(cacheTokens) || 0);
    const prompt = Math.max(0, Number(promptTokens) || 0);
    const completion = Math.max(0, Number(completionTokens) || 0);
    const cachePart = cacheTokens / 1e6 * p.cacheHit;
    const promptPart = Math.max(0, prompt - cacheTokens) / 1e6 * p.input;
    const completionPart = completion / 1e6 * p.output;
    return this.round(promptPart + cachePart + completionPart);
  },

  /** 估算"不做优化"的对照花费
   * 注意：promptTokens 已含缓存命中部分（调用方传入的是完整输入 token），
   * 基线 = 全部输入按正常输入价 + 全部输出按输出价，不再重复累加 cacheTokens。
   * @param {string} model - 模型名
   * @param {number} promptTokens - 输入 token（已含缓存命中部分）
   * @param {number} completionTokens - 输出 token
   * @returns {number} */
  estimateBaseline(model, promptTokens, completionTokens) {
    const p = this.resolvePrice(model);
    const prompt = Math.max(0, Number(promptTokens) || 0);
    const completion = Math.max(0, Number(completionTokens) || 0);
    return this.round(prompt / 1e6 * p.input + completion / 1e6 * p.output);
  },

  /** 四舍五入到 4 位小数
   * @param {number} v - 数值
   * @returns {number} */
  round(v) { return Math.round(v * 10000) / 10000; },

  /** 金额格式化（元 → 元/分）
   * @param {number} cny - 金额（元）
   * @param {string} [mode] - 'yuan' | 'fen'
   * @returns {string} */
  formatMoney(cny, mode) {
    const v = Number(cny) || 0;
    if (mode === 'fen') return Math.round(v * 100) + '分';
    if (v === 0) return '¥0';
    if (v < 0.01) return '<¥0.01';
    return '¥' + v.toFixed(v >= 1 ? 2 : 4);
  },

  /** 单次请求的完整成本核算
   * @param {Object} opts - {model, promptTokens, completionTokens, cacheTokens, fromCache}
   * @returns {{cost:number, baseline:number, saved:number, model:string, currency:string}} */
  calculate(opts) {
    const model = opts.model || '';
    const prompt = Number(opts.promptTokens) || 0;
    const completion = Number(opts.completionTokens) || 0;
    const cacheTokens = Number(opts.cacheTokens) || 0;
    if (this.isLocal(model)) {
      return { cost: 0, baseline: 0, saved: 0, model, currency: 'CNY', local: true };
    }
    const actual = opts.fromCache ? 0 : this.estimateCost(model, prompt, completion, cacheTokens);
    const baseline = this.estimateBaseline(model, prompt, completion);
    return { cost: this.round(actual), baseline: this.round(baseline), saved: this.round(baseline - actual), model, currency: 'CNY' };
  },

  /** 按当前价格重算一批消息的成本（实时算，不依赖历史快照）
   * @param {Array<Object>} messages - 消息（含 model/promptTokens/tokens/cacheInfo）
   * @returns {{cost:number, baseline:number, saved:number}} */
  aggregateMessages(messages) {
    let cost = 0, baseline = 0, saved = 0;
    (messages || []).forEach(m => {
      if (m.role !== 'assistant') return;
      const r = this.calculate({
        model: m.model,
        promptTokens: m.promptTokens || 0,
        completionTokens: m.tokens || 0,
        cacheTokens: 0,
        fromCache: !!(m.cacheInfo && m.cacheInfo.from)
      });
      cost += r.cost; baseline += r.baseline; saved += r.saved;
    });
    return { cost: this.round(cost), baseline: this.round(baseline), saved: this.round(saved) };
  }
};

/* ============================================================
   [成本中心] 阶段二~五：消息成本标签 / 成本中心页 / 预算告警 / 示例包
   ============================================================ */

/** 渲染单条消息的成本小标签（按当前价格实时计算）
 * @param {Object} msg - 消息
 * @returns {string} HTML 药丸标签 */
function renderCostPills(msg) {
  if (!msg || typeof CostEngine === 'undefined') return '';
  const model = msg.model || '';
  const tokens = msg.tokens || 0;
  const cached = !!(msg.cacheInfo && msg.cacheInfo.from);
  const pills = [];
  pills.push('<span class="cost-pill">' + (tokens || 0) + ' tok</span>');
  if (model) {
    const r = CostEngine.calculate({
      model: model,
      promptTokens: msg.promptTokens || 0,
      completionTokens: tokens,
      cacheTokens: 0,
      fromCache: cached
    });
    if (r.local) {
      pills.push('<span class="cost-pill local">本地 ¥0</span>');
    } else {
      pills.push('<span class="cost-pill">' + CostEngine.formatMoney(r.cost) + '</span>');
      if (cached) pills.push('<span class="cost-pill hit" title="命中自有缓存，本次未调用 API">缓存命中 省' + CostEngine.formatMoney(r.saved) + '</span>');
      else if (r.saved > 0.0001) pills.push('<span class="cost-pill save">省' + CostEngine.formatMoney(r.saved) + '</span>');
    }
  }
  if (msg.routeReason) pills.push('<span class="cost-pill route" title="' + escapeHtml(msg.routeReason) + '">已路由</span>');
  if ((msg.compressionSaved || 0) > 0) pills.push('<span class="cost-pill comp" title="系统提示词清洗与上下文截断">压缩省' + msg.compressionSaved + ' tok</span>');
  return pills.join('');
}

/** CNY 预算管理（设置持久化，被 Gateway 与成本中心读取） */
const CostBudget = {
  /** 本月实际花费（元，实时聚合）
   * @returns {Promise<number>} */
  async monthCNY() {
    const usage = await gatewayMetaGet('__usage__', { byDay: {} });
    let total = 0;
    const prefix = dayKey().slice(0, 7);
    Object.keys(usage.byDay || {}).forEach(k => {
      if (k.startsWith(prefix)) total += (usage.byDay[k].costCNY || 0);
    });
    return Math.round(total * 10000) / 10000;
  },
  /** 是否已超预算（开启自动降级时由 Gateway 读取）
   * @returns {boolean} */
  isOver() {
    const budget = MoraySettings.get('costBudgetCNY') || 0;
    return budget > 0 && this._monthCache >= budget;
  },
  /** 预警检查（50/80/100% 各档每日一次）
   * @returns {Promise<void>} */
  async afterRecord() {
    const budget = MoraySettings.get('costBudgetCNY') || 0;
    if (budget <= 0) return;
    const month = await this.monthCNY();
    this._monthCache = month;
    const pct = month / budget;
    const level = pct >= 1 ? 100 : pct >= 0.8 ? 80 : pct >= 0.5 ? 50 : 0;
    const key = level + ':' + dayKey();
    if (!level || this._notified === key) return;
    this._notified = key;
    if (level === 100) {
      // [统一人民币 v3.6] 唯一预算触顶弹窗：可继续（本期不再提醒）或暂停云端 API（pauseAPI）
      const box = showModal('<p class="text-sm text-text-secondary">本月 API 费用已达预算上限：<span class="text-danger font-mono">¥' + month.toFixed(2) + '</span> / ¥' + budget + '</p><p class="text-xs text-text-tertiary mt-2">缓存命中与本地 Ollama 不受影响。可继续使用或暂停云端 API；也可在 成本与省钱 开启"超预算自动降级"。</p>',
        { title: '月度预算已用完', icon: 'wallet', footer: false });
      const foot = document.createElement('div');
      foot.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--color-line-ghost)';
      foot.innerHTML = '<button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost" id="cnyBudgetContinue">继续使用</button><button class="px-3 py-2 rounded-lg text-xs bg-danger text-white" id="cnyBudgetPause">暂停云端 API</button>';
      box.appendChild(foot);
      on(foot.querySelector('#cnyBudgetContinue'), 'click', () => { closeModal(); showNotification('已选择继续', '本期不再重复提醒', 'info', 2000); });
      on(foot.querySelector('#cnyBudgetPause'), 'click', async () => {
        await MoraySettings.set('pauseAPI', true);
        closeModal();
        showNotification('云端 API 已暂停', '可在设置 → API 智能网关 中恢复', 'warning', 3500);
      });
      refreshIcons();
    } else {
      showNotification('预算预警', '本月已花费 ¥' + month.toFixed(2) + '（' + level + '%）', 'warning', 5000);
    }
  }
};

/** 成本中心页应用 */
const CostCenterApp = {
  /** 时间范围筛选 @type {string} */
  range: 'month',

  /** 构建成本中心页面（幂等）
   * @returns {Promise<void>} */
  async build() {
    const page = document.getElementById('page-cost');
    if (!page) return;
    page.innerHTML = [
      '<div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">',
      '  <div class="flex items-center gap-2"><i data-lucide="piggy-bank" class="w-4 h-4 text-brand-cyan"></i>',
      '    <span class="text-sm font-medium text-text-primary">成本与省钱中心</span>',
      '    <span class="tag-pill bg-brand-cyan/15 text-brand-cyan">按当前价格估算 · 价格可在设置→成本调整</span></div>',
      '  <div class="flex items-center gap-2">',
      ['today', 'week', 'month', 'all'].map(r => '<button class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost ' + (this.range === r ? 'text-brand-cyan border-brand-cyan/40' : 'text-text-tertiary') + '" data-cost-range="' + r + '">' + (r === 'today' ? '今天' : r === 'week' ? '本周' : r === 'month' ? '本月' : '全部') + '</button>').join(''),
      '    <button class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1" id="costExportBtn"><i data-lucide="download" class="w-3 h-3"></i>导出CSV</button></div>',
      '</div>',
      '<div class="flex-1 overflow-y-auto p-5"><div class="max-w-5xl mx-auto space-y-4">',
      '<div class="grid grid-cols-4 gap-2" id="costStatCards"></div>',
      '<div class="grid grid-cols-2 gap-4">',
      '  <div class="glass-card rounded-xl p-4 border border-line-ghost/60"><div class="text-xs font-medium text-text-secondary mb-2">近14天 花费 vs 若不优化对照（¥）</div><div id="costTrendChart" style="height:160px"></div></div>',
      '  <div class="glass-card rounded-xl p-4 border border-line-ghost/60"><div class="text-xs font-medium text-text-secondary mb-2">模型 token 占比</div><div id="costModelChart" style="height:160px"></div></div>',
      '</div>',
      '<div class="grid grid-cols-2 gap-4">',
      '  <div class="glass-card rounded-xl p-4 border border-line-ghost/60"><div class="text-xs font-medium text-text-secondary mb-2">缓存命中率</div><div id="costCacheChart" style="height:80px"></div></div>',
      '  <div class="glass-card rounded-xl p-4 border border-line-ghost/60"><div class="text-xs font-medium text-text-secondary mb-2">智能路由策略（写回设置，被 Gateway 实时读取）</div><div id="costRoutePanel"></div></div>',
      '</div>',
      '<div class="glass-card rounded-xl p-4 border border-line-ghost/60">',
      '  <div class="flex items-center justify-between mb-2"><span class="text-xs font-medium text-text-secondary">本月预算</span><span class="text-[11px] text-text-tertiary" id="costBudgetText"></span></div>',
      '  <div class="progress-thin" style="height:8px"><div id="costBudgetFill" style="width:0%"></div></div>',
      '</div>',
      '<div class="glass-card rounded-xl p-4 border border-line-ghost/60">',
      '  <div class="text-xs font-medium text-text-secondary mb-2">明细（按日聚合）</div><div class="space-y-1" id="costDetailList"></div>',
      '</div>',
      '</div></div>'
    ].join('');
    refreshIcons();
    this.bind();
    await this.refresh();
  },

  /** 绑定事件
   * @returns {void} */
  bind() {
    const page = document.getElementById('page-cost');
    if (!page) return;
    page.querySelectorAll('[data-cost-range]').forEach(b => b.addEventListener('click', async () => {
      this.range = b.dataset.costRange;
      await this.build();
    }));
    const exportBtn = page.querySelector('#costExportBtn');
    if (exportBtn) exportBtn.addEventListener('click', () => this.exportCSV());
  },

  /** 汇总区间用量
   * @returns {Promise<Object>} {rows, sum, byModel} */
  async aggregate() {
    const usage = await gatewayMetaGet('__usage__', { byDay: {} });
    const days = Object.keys(usage.byDay).sort();
    const now = Date.now();
    const rangeMs = { today: 1, week: 7, month: 31, all: Infinity }[this.range] * 86400e3;
    const sum = { cost: 0, baseline: 0, saved: 0, tokens: 0, requests: 0 };
    const byModel = {};
    const filtered = days.filter(k => {
      if (this.range === 'all') return true;
      if (this.range === 'month') return k.startsWith(dayKey().slice(0, 7));
      const d = new Date(k + 'T00:00:00').getTime();
      return now - d < rangeMs;
    });
    const rows = filtered.map(k => {
      const d = usage.byDay[k];
      const cost = d.costCNY || 0;
      sum.cost += cost;
      sum.baseline += (d.baselineCNY || 0);
      sum.saved += (d.savedCNY || 0);
      sum.tokens += (d.prompt || 0) + (d.completion || 0);
      sum.requests += d.requests || 0;
      Object.keys(d.byModel || {}).forEach(m => {
        byModel[m] = byModel[m] || 0;
        byModel[m] += (d.byModel[m].completion || 0) + (d.byModel[m].prompt || 0);
      });
      return { day: k, cost: cost, baseline: d.baselineCNY || 0, saved: d.savedCNY || 0, tokens: (d.prompt || 0) + (d.completion || 0), requests: d.requests || 0 };
    });
    return { rows: rows, sum: sum, byModel: byModel };
  },

  /** 刷新统计与图表
   * @returns {Promise<void>} */
  async refresh() {
    const page = document.getElementById('page-cost');
    if (!page) return;
    const agg = await this.aggregate();
    const rows = agg.rows, sum = agg.sum, byModel = agg.byModel;
    const cacheStats = await GatewayCache.stats();
    const usage = await gatewayMetaGet('__usage__', { byDay: {} });
    const monthPrefix = dayKey().slice(0, 7);
    let monthCost = 0, monthSaved = 0;
    Object.keys(usage.byDay || {}).forEach(k => {
      if (k.startsWith(monthPrefix)) { monthCost += (usage.byDay[k].costCNY || 0); monthSaved += (usage.byDay[k].savedCNY || 0); }
    });
    const todayCost = (usage.byDay[dayKey()] || {}).costCNY || 0;
    const compSaved = Object.values(AppState._convMsgIndex || {}).reduce((s, arr) =>
      s + arr.reduce((a, m) => a + (m.compressionSaved || 0), 0), 0);
    const perf = PerfHistory || [];
    const okPerf = perf.filter(p => p.ok);
    const avgFirst = okPerf.length ? Math.round(okPerf.reduce((s, p) => s + (p.firstMs || 0), 0) / okPerf.length) : 0;
    const avgTps = okPerf.length ? Math.round(okPerf.reduce((s, p) => s + (p.tokPerSec || 0), 0) / okPerf.length) : 0;
    const totalReqs = cacheStats.hits + cacheStats.misses;
    const hitRate = totalReqs ? Math.round(cacheStats.hits / totalReqs * 100) : 0;

    const cards = [
      ['今日花费', CostEngine.formatMoney(todayCost), 'text-text-primary'],
      ['本月花费', CostEngine.formatMoney(monthCost), 'text-text-primary'],
      ['累计花费', CostEngine.formatMoney(sum.cost), 'text-text-primary'],
      ['累计节省', CostEngine.formatMoney(sum.saved), 'text-success'],
      ['缓存命中率', hitRate + '%', 'text-brand-cyan'],
      ['压缩节省', compSaved + ' tok', 'text-brand-violet'],
      ['平均首token', avgFirst ? avgFirst + 'ms' : '--', 'text-text-secondary'],
      ['平均速度', avgTps ? avgTps + ' tok/s' : '--', 'text-text-secondary']
    ];
    page.querySelector('#costStatCards').innerHTML = cards.map(c => [
      '<div class="rounded-lg bg-surface-panel/60 py-3 px-3 text-center">',
      '<div class="text-sm font-mono ' + c[2] + '">' + c[1] + '</div>',
      '<div class="text-[10px] text-text-tertiary mt-0.5">' + c[0] + '</div></div>'
    ].join('')).join('');

    const last14 = rows.slice(-14);
    const maxV = Math.max(0.0001, ...last14.map(r => Math.max(r.cost, r.baseline)));
    page.querySelector('#costTrendChart').innerHTML = last14.length
      ? '<div class="flex items-end gap-1" style="height:100%">' + last14.map(r => [
          '<div class="flex-1 flex flex-col items-center justify-end gap-px" title="' + r.day + '：实际 ¥' + r.cost.toFixed(4) + ' / 对照 ¥' + r.baseline.toFixed(4) + '">',
          '<div style="width:70%;max-width:16px;height:' + Math.max(2, r.baseline / maxV * 110) + 'px;background:var(--color-surface-hover);border-radius:2px"></div>',
          '<div style="width:70%;max-width:16px;height:' + Math.max(2, r.cost / maxV * 110) + 'px;background:linear-gradient(180deg,var(--color-brand-cyan),var(--color-brand-cobalt));border-radius:2px"></div>',
          '</div>'
        ].join('')).join('') + '</div>' +
        '<div class="flex gap-3 text-[9px] text-text-tertiary mt-1"><span class="flex items-center gap-1"><span style="width:8px;height:8px;background:var(--color-brand-cobalt);display:inline-block;border-radius:2px"></span>实际花费</span><span class="flex items-center gap-1"><span style="width:8px;height:8px;background:var(--color-surface-hover);display:inline-block;border-radius:2px"></span>不优化对照</span></div>'
      : '<div class="text-[11px] text-text-tertiary text-center py-10">暂无数据，发一条消息后这里会显示每日花费对比</div>';

    const models = Object.keys(byModel);
    const mTotal = models.reduce((s, m) => s + byModel[m], 0) || 1;
    const COLORS = ['#5B8CFF', '#9D7BFF', '#3AD6E8', '#3FD68F', '#F2B24C', '#FF5C6C'];
    let acc = 0;
    const donut = models.map((m, i) => {
      const frac = byModel[m] / mTotal;
      const seg = '<circle cx="40" cy="40" r="30" fill="none" stroke="' + COLORS[i % COLORS.length] + '" stroke-width="12" stroke-dasharray="' + (frac * 188.5).toFixed(1) + ' 188.5" stroke-dashoffset="' + (-acc * 188.5).toFixed(1) + '" transform="rotate(-90 40 40)"><title>' + escapeHtml(m) + '：' + byModel[m] + ' tokens</title></circle>';
      acc += frac;
      return seg;
    }).join('');
    page.querySelector('#costModelChart').innerHTML = models.length
      ? '<div class="flex items-center gap-4 h-full"><svg viewBox="0 0 80 80" style="width:110px;height:110px;flex-shrink:0">' + donut + '</svg><div class="text-[10px] text-text-tertiary space-y-1">' +
        models.slice(0, 6).map((m, i) => '<div class="flex items-center gap-1"><span style="width:8px;height:8px;border-radius:2px;background:' + COLORS[i % COLORS.length] + ';display:inline-block"></span>' + escapeHtml(shortModelName(m)) + ' · ' + Math.round(byModel[m] / mTotal * 100) + '%</div>').join('') + '</div></div>'
      : '<div class="text-[11px] text-text-tertiary text-center py-10">暂无模型用量</div>';

    const hitPct = totalReqs ? Math.round(cacheStats.hits / totalReqs * 100) : 0;
    page.querySelector('#costCacheChart').innerHTML = [
      '<div class="flex h-5 rounded overflow-hidden">',
      '<div style="width:' + hitPct + '%;background:linear-gradient(90deg,var(--color-brand-cobalt),var(--color-brand-cyan))" title="命中 ' + cacheStats.hits + ' 次"></div>',
      '<div style="width:' + (100 - hitPct) + '%;background:var(--color-surface-hover)" title="未命中 ' + cacheStats.misses + ' 次"></div></div>',
      '<div class="flex justify-between text-[10px] text-text-tertiary mt-1"><span>命中 ' + cacheStats.hits + ' 次 · 省 ' + cacheStats.tokensSaved + ' tokens</span><span>未命中 ' + cacheStats.misses + ' 次</span></div>'
    ].join('');

    const rc = MoraySettings.get('routingConfig') || {};
    const routeRows = [['simple', '简单任务'], ['medium', '中等任务'], ['complex', '复杂任务']].map(t => {
      const conf = rc[t[0]] || {};
      return '<div class="flex items-center justify-between py-1"><span class="text-[10px] text-text-tertiary">' + t[1] + '</span><span class="text-[10px] text-text-secondary">' + (conf.primary ? escapeHtml(shortModelName(conf.primary)) : '默认') + ' → ' + (conf.fallback ? escapeHtml(shortModelName(conf.fallback)) : '备用未设') + ' → 降级</span></div>';
    }).join('');
    page.querySelector('#costRoutePanel').innerHTML = [
      '<div class="space-y-1">' + routeRows + '</div>',
      '<div class="flex items-center justify-between pt-2 mt-1 border-t border-line-ghost/40"><span class="text-[10px] text-text-tertiary">本地优先（Ollama 可用时优先）</span><div class="toggle-track ' + (MoraySettings.get('routingLocalFirst') ? 'active' : '') + '" id="costLocalFirst"><div class="toggle-thumb"></div></div></div>',
      '<div class="flex items-center justify-between pt-2 mt-1 border-t border-line-ghost/40"><span class="text-[10px] text-text-tertiary">超预算自动降级到最便宜模型</span><div class="toggle-track ' + (MoraySettings.get('autoDegrade') ? 'active' : '') + '" id="costAutoDegrade"><div class="toggle-thumb"></div></div></div>',
      '<div class="flex items-center justify-between pt-2 mt-1 border-t border-line-ghost/40"><span class="text-[10px] text-text-tertiary">语义缓存阈值</span><input type="range" min="0.90" max="0.99" step="0.01" value="' + (MoraySettings.get('semanticThreshold') || 0.95) + '" id="costSemTh" class="w-24 accent-brand-cobalt"></div>'
    ].join('');
    on(page.querySelector('#costLocalFirst'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('routingLocalFirst', this.classList.contains('active')); });
    on(page.querySelector('#costAutoDegrade'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('autoDegrade', this.classList.contains('active')); });
    const semTh = page.querySelector('#costSemTh');
    semTh.addEventListener('change', () => MoraySettings.set('semanticThreshold', parseFloat(semTh.value)));

    const budget = MoraySettings.get('costBudgetCNY') || 0;
    const fill = page.querySelector('#costBudgetFill');
    const pct = budget ? Math.min(100, monthCost / budget * 100) : 0;
    fill.style.width = pct + '%';
    fill.style.background = pct >= 100 ? 'var(--color-danger)' : pct >= 80 ? 'var(--color-warning)' : '';
    page.querySelector('#costBudgetText').textContent = budget ? '¥' + monthCost.toFixed(2) + ' / ¥' + budget + '（' + Math.round(pct) + '%）' : '未设置（设置→成本）';

    page.querySelector('#costDetailList').innerHTML = rows.length ? rows.slice().reverse().map(r => [
      '<div class="flex items-center justify-between px-3 py-1.5 rounded-lg bg-surface-panel/40 text-[11px]">',
      '<span class="text-text-secondary">' + r.day + '</span>',
      '<span class="text-text-tertiary">' + r.tokens + ' tok · ' + r.requests + ' 次请求</span>',
      '<span class="font-mono">¥' + r.cost.toFixed(4) + (r.saved > 0 ? ' <span class="text-success">省¥' + r.saved.toFixed(4) + '</span>' : '') + '</span></div>'
    ].join('')).join('') : '<div class="text-[11px] text-text-tertiary text-center py-6">所选范围暂无请求记录</div>';
    refreshIcons();
  },

  /** 导出 CSV（按日明细 + 汇总）
   * @returns {Promise<void>} */
  async exportCSV() {
    const agg = await this.aggregate();
    let csv = '日期,tokens,请求数,实际花费(元),对照花费(元),节省(元)\n';
    agg.rows.forEach(r => { csv += r.day + ',' + r.tokens + ',' + r.requests + ',' + r.cost.toFixed(4) + ',' + r.baseline.toFixed(4) + ',' + r.saved.toFixed(4) + '\n'; });
    csv += '合计,' + agg.sum.tokens + ',' + agg.sum.requests + ',' + agg.sum.cost.toFixed(4) + ',' + agg.sum.baseline.toFixed(4) + ',' + agg.sum.saved.toFixed(4) + '\n';
    downloadText('moray_cost_report.csv', csv, 'text/csv;charset=utf-8');
    showNotification('报表已导出', 'moray_cost_report.csv', 'success', 1800);
  }
};

/** 设置页成本卡片（价格表编辑 + 预算 + 覆盖新增）
 * @returns {Promise<void>} */
async function appendCostSettings() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container || container.querySelector('#costSettingsCard')) return;
  const overrides = MoraySettings.get('priceOverrides') || {};
  const card = document.createElement('div');
  card.id = 'costSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  const priceRows = PRICE_TABLE.map((rule, i) => {
    const key = rule.match.includes('default') ? '默认档' : rule.match[0];
    const ov = overrides[key];
    const inp = (f) => '<input type="number" step="0.1" min="0" value="' + (ov ? ov[f] : rule.prices[f]) + '" data-price-row="' + i + '" data-field="' + f + '" class="form-input" style="width:64px;padding:2px 6px;font-size:10px">';
    return '<div class="flex items-center gap-1.5"><span class="text-[10px] text-text-tertiary w-24 truncate" title="' + escapeHtml(rule.note) + '">' + escapeHtml(key) + '</span>' + inp('input') + inp('output') + inp('cacheHit') + (rule.prices.input === 0 ? '<span class="text-[9px] text-success">本地免费</span>' : '') + '</div>';
  }).join('');
  card.innerHTML = [
    '<h3 class="text-sm font-semibold text-text-primary mb-2 flex items-center gap-2"><i data-lucide="coins" class="w-4 h-4 text-brand-cyan"></i>成本与省钱</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">价格为默认值、可能变动、以官方定价为准；成本中心按当前价格实时估算。</p>',
    '<div class="flex items-center justify-between mb-3">',
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">月度预算（元）</span><input type="number" min="0" step="1" value="' + (MoraySettings.get('costBudgetCNY') || 0) + '" id="setCostBudget" class="form-input" style="width:90px;padding:4px 8px;font-size:11px"></div>',
    '<div class="flex items-center gap-2"><span class="text-xs text-text-secondary">超预算自动降级</span><div class="toggle-track ' + (MoraySettings.get('autoDegrade') ? 'active' : '') + '" id="setAutoDegrade"><div class="toggle-thumb"></div></div></div></div>',
    '<div class="text-[10px] text-text-tertiary mb-1">价格表（元/百万token · 输入/输出/缓存命中）</div>',
    '<div class="space-y-1 mb-2" id="costPriceRows">' + priceRows + '</div>',
    '<div class="flex gap-1.5 items-center"><input type="text" id="costNewModel" class="form-input" style="flex:1;padding:4px 8px;font-size:11px" placeholder="自定义模型名（如 my-model）"><button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="costAddOverride">新增自定义价格</button></div>',
    '<button class="text-[10px] text-danger mt-2 hover:opacity-80" id="costResetOverrides">清除全部自定义价格</button>'
  ].join('');
  container.insertBefore(card, container.querySelector('#gatewaySettingsCard') || container.querySelector('#enhanceSettingsCard') || container.lastElementChild);
  refreshIcons();

  const save = async () => {
    const ov = {};
    card.querySelectorAll('[data-price-row]').forEach(inp => {
      const rule = PRICE_TABLE[parseInt(inp.dataset.priceRow, 10)];
      const key = rule.match.includes('default') ? '默认档' : rule.match[0];
      ov[key] = ov[key] || {};
      ov[key][inp.dataset.field] = parseFloat(inp.value) || 0;
    });
    const customs = MoraySettings.get('priceOverrides') || {};
    Object.keys(customs).forEach(k => { if (!ov[k]) ov[k] = customs[k]; });
    await MoraySettings.set('priceOverrides', ov);
  };
  card.querySelectorAll('[data-price-row]').forEach(inp => inp.addEventListener('change', save));
  on(card.querySelector('#setCostBudget'), 'change', () => MoraySettings.set('costBudgetCNY', parseFloat(card.querySelector('#setCostBudget').value) || 0));
  on(card.querySelector('#setAutoDegrade'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('autoDegrade', this.classList.contains('active')); });
  on(card.querySelector('#costAddOverride'), 'click', async () => {
    const name = card.querySelector('#costNewModel').value.trim().toLowerCase();
    if (!name) return;
    const ov = MoraySettings.get('priceOverrides') || {};
    ov[name] = { input: 2, output: 8, cacheHit: 0.5 };
    await MoraySettings.set('priceOverrides', ov);
    card.querySelector('#costNewModel').value = '';
    card.remove();
    await appendCostSettings();
  });
  on(card.querySelector('#costResetOverrides'), 'click', async () => {
    await MoraySettings.set('priceOverrides', {});
    card.remove();
    await appendCostSettings();
    showNotification('已重置', '价格恢复默认档', 'success', 1500);
  });
}

/** 内置示例包（sample:true 标记便于一键清空） */
const SAMPLE_PROMPTS = [
  { title: '五档代码审查', category: '代码审查', tags: ['审查'], content: '请以五档评分（A/B/C/D/F）审查以下代码：\n1. 正确性与边界 2. 安全隐患 3. 性能瓶颈 4. 可读性与命名 5. 测试建议\n\n代码：\n{{代码}}' },
  { title: '费曼讲解', category: '解释', tags: ['学习'], content: '请用费曼学习法讲解{{概念}}：先给一句大白话，再分层展开，每个要点配一个类比和一个例子。' },
  { title: 'SQL 生成', category: '代码生成', tags: ['SQL'], content: '根据表结构与需求生成 SQL，附执行计划说明与索引建议。\n\n表结构：{{表结构}}\n需求：{{需求}}' },
  { title: '正则专家', category: '代码生成', tags: ['正则'], content: '为需求编写正则：给出正则 + 逐段解释 + 3个匹配示例 + 2个不匹配示例。\n\n需求：{{需求}}' },
  { title: 'API 文档', category: '文档', tags: ['API'], content: '为接口生成文档：功能说明、方法路径、参数表、请求/响应示例、错误码表。\n\n接口：{{接口}}' },
  { title: '表驱动测试', category: '测试', tags: ['测试'], content: '为函数生成表驱动单元测试：覆盖正常值、边界值、异常输入。\n\n函数：{{代码}}' },
  { title: '小步重构', category: '重构', tags: ['重构'], content: '对代码提出小步可验证的重构方案：每步给出改什么、为什么、风险、验证方法。\n\n代码：{{代码}}' },
  { title: '错误翻译官', category: '其他', tags: ['调试'], content: '解释错误信息：含义、最可能的3个原因（按概率）、每个原因的验证步骤、最终修复建议。\n\n错误：{{错误}}' },
  { title: '方案对比表', category: '其他', tags: ['架构'], content: '对比 {{方案A}} 与 {{方案B}}：从学习成本、生态、性能、运维、社区五个维度打分（1-5），给出结论。' },
  { title: '周报生成', category: '其他', tags: ['效率'], content: '把流水账整理成周报：【本周完成/数据亮点/问题风险/下周计划】，要点化、量化优先。\n\n流水账：{{内容}}' }
];
const SAMPLE_SNIPPETS = [
  { title: '防抖函数', code: 'function debounce(fn, delay = 300) {\n  let timer = null;\n  return function (...args) {\n    if (timer) clearTimeout(timer);\n    timer = setTimeout(() => fn.apply(this, args), delay);\n  };\n}', language: 'javascript', tags: ['工具函数'] },
  { title: '深拷贝', code: 'function deepClone(obj, hash = new WeakMap()) {\n  if (obj === null || typeof obj !== "object") return obj;\n  if (hash.has(obj)) return hash.get(obj);\n  const clone = new obj.constructor();\n  hash.set(obj, clone);\n  for (const key in obj) {\n    if (Object.prototype.hasOwnProperty.call(obj, key)) clone[key] = deepClone(obj[key], hash);\n  }\n  return clone;\n}', language: 'javascript', tags: ['工具函数'] },
  { title: 'LRU 缓存', code: 'from collections import OrderedDict\n\nclass LRUCache:\n    def __init__(self, capacity: int):\n        self.cache = OrderedDict()\n        self.cap = capacity\n\n    def get(self, key: int) -> int:\n        if key not in self.cache:\n            return -1\n        self.cache.move_to_end(key)\n        return self.cache[key]\n\n    def put(self, key: int, value: int) -> None:\n        if key in self.cache:\n            self.cache.move_to_end(key)\n        self.cache[key] = value\n        if len(self.cache) > self.cap:\n            self.cache.popitem(last=False)', language: 'python', tags: ['算法'] },
  { title: '二分查找', code: 'def binary_search(arr, target):\n    lo, hi = 0, len(arr) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if arr[mid] == target:\n            return mid\n        if arr[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1', language: 'python', tags: ['算法'] },
  { title: '渐变文字', code: '.gradient-text {\n  background: linear-gradient(135deg, #5B8CFF, #9D7BFF, #3AD6E8);\n  -webkit-background-clip: text;\n  -webkit-text-fill-color: transparent;\n  background-clip: text;\n}', language: 'css', tags: ['样式'] },
  { title: '查询月度销售', code: "SELECT DATE_TRUNC('month', order_date) AS month,\n       SUM(amount) AS total_sales\nFROM orders\nWHERE order_date >= NOW() - INTERVAL '12 months'\nGROUP BY 1\nORDER BY 1 DESC;", language: 'sql', tags: ['SQL'] }
];

/** 载入示例包（幂等：已有样本则跳过）
 * @returns {Promise<number>} 写入条数 */
async function loadSamplePack() {
  let count = 0;
  const existingPrompts = await DB.listPrompts();
  for (const p of SAMPLE_PROMPTS) {
    if (existingPrompts.some(x => x.title === p.title && x.sample)) continue;
    await DB.createPrompt(Object.assign({}, p, { sample: true, useCount: 0 }));
    count++;
  }
  const existingSnips = await DB.listSnippets();
  for (const s of SAMPLE_SNIPPETS) {
    if (existingSnips.some(x => x.title === s.title && x.sample)) continue;
    await DB.createSnippet(Object.assign({}, s, { sample: true }));
    count++;
  }
  const wfs = await DB.listWorkflows();
  if (!wfs.some(w => w.name === '代码审查' && w.sample)) {
    const tpl = builtinWorkflowTemplates().find(t => t.key === 'code-review');
    if (tpl) {
      await DB.createWorkflow({ name: tpl.name, description: tpl.description, icon: tpl.icon, accent: tpl.accent, nodes: tpl.nodes, isTemplate: true, sample: true });
      count++;
    }
  }
  if (count) {
    await PromptsApp.reload(); await SnippetsApp.reload(); await WorkflowsApp.reload();
  }
  return count;
}

/** 清空示例数据（仅删除 sample:true 条目）
 * @returns {Promise<number>} 删除条数 */
async function clearSamplePack() {
  let count = 0;
  for (const p of await DB.listPrompts()) { if (p.sample) { await DB.deletePrompt(p.id); count++; } }
  for (const s of await DB.listSnippets()) { if (s.sample) { await DB.deleteSnippet(s.id); count++; } }
  for (const w of await DB.listWorkflows()) { if (w.sample) { await DB.deleteWorkflow(w.id); count++; } }
  if (count) {
    await PromptsApp.reload(); await SnippetsApp.reload(); await WorkflowsApp.reload();
  }
  return count;
}
