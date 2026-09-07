/* ============================================================
   MoRay API 智能网关层（V2 核心）
   功能1 缓存层：精确缓存 + 语义缓存 + LRU/TTL + 管理统计
   功能2 智能路由：任务分类 + 模型优先级 + 自动降级
   功能3 提示词压缩：清洗 / 系统提示词优化 / 上下文智能截断
   功能4 用量监控：token/费用统计 + 预算提醒 + 报表导出
   功能5 批量优化：去重复用 + QPS 限速 + 费用预估确认
   功能6 健康检查：定时探活 + 指数退避重试 + 端点降级
   ============================================================ */

/* ===================== 工具：哈希与日期 ===================== */

/** FNV-1a 双种子 64 位哈希（十六进制串）
 * @param {string} str - 输入
 * @returns {string} 16位hex */
function fnvHash64(str) {
  let h1 = 0x811c9dc5, h2 = 0x1b873593;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    h1 ^= ch; h1 = Math.imul(h1, 16777619);
    h2 ^= ch + i; h2 = Math.imul(h2, 2246822519);
  }
  return (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0');
}

/** 本地日期键 YYYY-MM-DD
 * @param {number} [ts] - 时间戳
 * @returns {string} 日期键 */
function dayKey(ts) {
  const d = ts ? new Date(ts) : new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

/* ===================== [功能1] 缓存存储层 ===================== */

/** API 缓存仓库（IndexedDB apicache store，DB v2） */
const CacheStore = {
  /** 读取一条缓存
   * @param {string} hash - 请求哈希
   * @returns {Promise<Object|undefined>} 记录 */
  async get(hash) { return DB._tx('apicache', 'readonly', tx => tx.objectStore('apicache').get(hash)); },
  /** 写入缓存
   * @param {Object} rec - 缓存记录
   * @returns {Promise<void>} */
  async put(rec) { return DB._tx('apicache', 'readwrite', tx => tx.objectStore('apicache').put(rec)); },
  /** 删除
   * @param {string} hash - 哈希
   * @returns {Promise<void>} */
  async del(hash) { return DB._tx('apicache', 'readwrite', tx => tx.objectStore('apicache').delete(hash)); },
  /** 全量列表
   * @returns {Promise<Array>} 记录数组 */
  async all() { return DB._tx('apicache', 'readonly', tx => tx.objectStore('apicache').getAll()); },
  /** 清空
   * @returns {Promise<void>} */
  async clear() { return DB._tx('apicache', 'readwrite', tx => tx.objectStore('apicache').clear()); }
};

/** 缓存元数据读写（命中统计/用量统计，存于 apicache 的特殊记录）
 * @param {string} key - '__meta__' | '__usage__'
 * @param {*} [fallback] - 缺省结构
 * @returns {Promise<Object>} 元数据 */
async function gatewayMetaGet(key, fallback) {
  const row = await CacheStore.get(key);
  return row ? row.data : (fallback || {});
}
/** 写入网关元数据
 * @param {string} key - 键
 * @param {*} data - 数据
 * @returns {Promise<void>} */
async function gatewayMetaSet(key, data) {
  await CacheStore.put({ hash: key, data, updatedAt: Date.now() });
}

/* ===================== [功能1] GatewayCache ===================== */

/** 智能缓存管理器 */
const GatewayCache = {
  /** 计算请求精确哈希
   * @param {Object} opts - {model, temperature, messages}
   * @returns {string} 哈希 */
  requestHash(opts) {
    const sys = (opts.messages || []).filter(m => m.role === 'system').map(m => m.content).join('|');
    const rest = (opts.messages || []).filter(m => m.role !== 'system').map(m => m.role + ':' + m.content).join('|');
    // [深度思考] think 值纳入哈希：同一请求开/关思考是不同的生成，不得互用缓存
    return fnvHash64([opts.model, opts.temperature, opts.think === undefined ? '' : String(opts.think), sys, rest].join('§'));
  },

  /** 记录估算体积（字节）
   * @param {Object} rec - 记录
   * @returns {number} 字节数 */
  recSize(rec) {
    try { return JSON.stringify(rec).length * 2; } catch (e) { return 1024; }
  },

  /** 元数据自增
   * @param {string} type - 'hit'|'miss'
   * @param {Object} [extra] - {tokens, cost, similarity}
   * @returns {Promise<void>} */
  async bumpMeta(type, extra) {
    try {
      const meta = await gatewayMetaGet('__meta__', { hits: 0, misses: 0, tokensSaved: 0, costSaved: 0, byDay: {} });
      const day = dayKey();
      meta.byDay = meta.byDay || {};
      meta.byDay[day] = meta.byDay[day] || { hits: 0, queries: 0 };
      if (type === 'hit') {
        meta.hits++;
        meta.byDay[day].hits++;
        meta.tokensSaved += (extra && extra.tokens) || 0;
        meta.costSaved += (extra && extra.cost) || 0;
      } else {
        meta.misses++;
        meta.byDay[day].queries++;
      }
      await gatewayMetaSet('__meta__', meta);
    } catch (e) { console.warn('[MoRay] cache meta bump failed:', e); }
  },

  /** 精确查找（含TTL校验 + 命中计数）
   * @param {string} hash - 请求哈希
   * @returns {Promise<Object|null>} 缓存记录 */
  async lookupExact(hash) {
    const rec = await CacheStore.get(hash);
    if (!rec) return null;
    if (rec.expiresAt && Date.now() > rec.expiresAt) {
      CacheStore.del(hash).catch(() => {});
      return null;
    }
    rec.hits = (rec.hits || 0) + 1;
    rec.lastHitAt = Date.now();
    CacheStore.put(rec).catch(() => {});
    await this.bumpMeta('hit', { tokens: rec.tokens, cost: rec.cost });
    return rec;
  },

  /**
   * 语义查找：对用户最新消息做向量，余弦相似度超阈值则取最优
   * @param {Object} opts - 请求
   * @returns {Promise<{rec:Object, similarity:number}|null>} 结果 */
  /** [缓存根治] 语义命中上下文指纹：最近一轮对话摘要 + 系统提示（防止跨会话/跨上下文误命中）
   * @param {Array} messages - 请求消息
   * @returns {string} 指纹串 */
  semanticFingerprint(messages) {
    const arr = messages || [];
    const sys = (arr.find(m => m.role === 'system') || {}).content || '';
    const userMsgs = arr.filter(m => m.role === 'user').slice(-3).map(m => String(m.content).slice(0, 60)).join('|');
    const asstLast = arr.filter(m => m.role === 'assistant').slice(-1).map(m => String(m.content).slice(0, 40)).join('');
    return (sys.slice(0, 80) + '::' + userMsgs + '::' + asstLast).slice(0, 400);
  },

  /** 语义查找：短消息直接拒绝；命中必须上下文指纹一致（防跨会话/跨上下文误回放）
   * @param {Object} opts - 请求
   * @returns {Promise<{rec:Object, similarity:number}|null>} 结果 */
  async lookupSemantic(opts) {
    if (!MoraySettings.get('semanticCache')) return null;
    const lastUser = (opts.messages || []).filter(m => m.role === 'user').pop();
    if (!lastUser) return null;
    const text = String(lastUser.content || '').trim();
    // [缓存根治] 短问候/单词/极短指令（你好/在吗/继续等）绝不走语义缓存
    const cjk = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
    const words = text.split(/\s+/).filter(Boolean).length;
    if (cjk > 0 ? cjk < 8 : words < 3) return null;
    // [缓存根治] 上下文指纹：最近轮次 + 系统提示不一致不得命中
    const fp = this.semanticFingerprint(opts.messages || []);
    if (!fp) return null;
    const qvec = await getEmbeddingVector(lastUser.content);
    if (!qvec) return null;
    const rows = (await CacheStore.all()).filter(r => r.hash && r.hash !== '__meta__' && r.hash !== '__usage__' && r.etype === qvec.type);
    if (!rows.length) return null;
    // 过期剔除 + 找最优（指纹一致才候选）
    let best = null, bestScore = 0;
    const threshold = MoraySettings.get('semanticThreshold') || 0.95;
    for (const r of rows) {
      if (r.expiresAt && Date.now() > r.expiresAt) continue;
      if (!r.qvec) continue;
      if (r.ctxFingerprint && r.ctxFingerprint !== fp) continue; // [缓存根治] 指纹不一致不命中
      const score = cosineSimilarity(qvec.vec, r.qvec.vec);
      if (score > bestScore) { bestScore = score; best = r; }
    }
    if (best && bestScore >= threshold) {
      best.hits = (best.hits || 0) + 1;
      best.lastHitAt = Date.now();
      CacheStore.put(best).catch(() => {});
      await this.bumpMeta('hit', { tokens: best.tokens, cost: best.cost });
      return { rec: best, similarity: bestScore };
    }
    return null;
  },

  /** 写入缓存（LRU 容量控制）
   * @param {Object} opts - 请求参数
   * @param {Object} result - {content, reasoning, stats}
   * @returns {Promise<void>} */
  async save(opts, result) {
    try {
      if (!result || !result.content) return;
      const now = Date.now();
      const ttlHours = MoraySettings.get('cacheTTL');
      const lastUser = (opts.messages || []).filter(m => m.role === 'user').pop();
      // [运行体检修复] 语义缓存默认关闭时绝不发起 embedding 请求：
      // 此前每次完成回复都会调用 getEmbeddingVector（Ollama 缺 embedding 模型时产生
      // /api/embeddings 404 控制台错误，且白白增加一次向量请求）。语义缓存开启时才需要向量。
      let qvec = null;
      if (MoraySettings.get('semanticCache')) {
        qvec = lastUser ? await getEmbeddingVector(lastUser.content) : null;
      }
      const tokens = (result.stats && result.stats.tokens) || estimateTokens(result.content).tokens;
      const cost = UsageTracker.estimateCost(opts.model, (result.stats && result.stats.promptTokens) || 0, tokens);
      const rec = {
        hash: this.requestHash(opts),
        model: opts.model, temperature: opts.temperature,
        request: (opts.messages || []).slice(-4), // 仅存尾部消息，控制体积
        reply: result.content, reasoning: result.reasoning || '',
        tokens, cost,
        // [缓存根治] 上下文指纹：语义命中时必须一致（防止跨会话/跨上下文误回放）
        ctxFingerprint: this.semanticFingerprint(opts.messages || []),
        qvec: qvec ? { type: qvec.type, vec: qvec.vec } : null,
        etype: qvec ? qvec.type : null,
        createdAt: now, expiresAt: ttlHours > 0 ? now + ttlHours * 3600e3 : 0,
        hits: 0, lastHitAt: now
      };
      await CacheStore.put(rec);
      // LRU 容量控制
      await this.enforceLimit();
    } catch (e) { console.warn('[MoRay] cache save failed:', e); }
  },

  /** 容量限制（超过 maxMB 按最近命中时间淘汰）
   * @returns {Promise<void>} */
  async enforceLimit() {
    const maxMB = MoraySettings.get('cacheMaxMB');
    if (!maxMB || maxMB <= 0) return;
    const rows = (await CacheStore.all()).filter(r => r.hash && r.hash !== '__meta__' && r.hash !== '__usage__');
    let total = rows.reduce((s, r) => s + this.recSize(r), 0);
    if (total <= maxMB * 1024 * 1024) return;
    rows.sort((a, b) => (a.lastHitAt || 0) - (b.lastHitAt || 0));
    for (const r of rows) {
      if (total <= maxMB * 1024 * 1024) break;
      total -= this.recSize(r);
      await CacheStore.del(r.hash);
    }
  },

  /** 统计摘要（管理界面用）
   * @returns {Promise<Object>} {count, sizeMB, hits, misses, hitRate, tokensSaved, costSaved, byDay} */
  async stats() {
    const rows = (await CacheStore.all()).filter(r => r.hash && r.hash !== '__meta__' && r.hash !== '__usage__');
    const meta = await gatewayMetaGet('__meta__', { hits: 0, misses: 0, tokensSaved: 0, costSaved: 0, byDay: {} });
    const total = meta.hits + meta.misses;
    return {
      count: rows.length,
      sizeMB: +(rows.reduce((s, r) => s + this.recSize(r), 0) / 1048576).toFixed(2),
      hits: meta.hits, misses: meta.misses,
      hitRate: total ? Math.round(meta.hits / total * 100) : 0,
      tokensSaved: meta.tokensSaved,
      costSaved: meta.costSaved,
      byDay: meta.byDay || {}
    };
  },

  /** 导出缓存（JSON）
   * @returns {Promise<void>} */
  async exportAll() {
    const rows = await CacheStore.all();
    downloadJson('moray_apicache.json', { version: 1, exportedAt: new Date().toISOString(), rows });
    showNotification('缓存已导出', `${rows.length} 条记录`, 'success');
  },

  /** 导入缓存（合并）
   * @param {File} file - JSON 文件
   * @returns {Promise<void>} */
  async importAll(file) {
    try {
      const data = JSON.parse(await file.text());
      const rows = data.rows || [];
      for (const r of rows) { if (r && r.hash) await CacheStore.put(r); }
      showNotification('缓存导入完成', `${rows.length} 条记录`, 'success');
    } catch (e) { showNotification('导入失败', e.message, 'error', 3000); }
  }
};

/** 获取文本向量（用于语义缓存/会话摘要判断）：Ollama -> Transformers -> 本地哈希
 * @param {string} text - 文本
 * @returns {Promise<{type:string, vec:number[]}|null>} 向量（type标识来源） */
async function getEmbeddingVector(text) {
  // Ollama 优先（真实语义）
  if (AI.backend === 'ollama') {
    try {
      const vec = await AI.embed(MoraySettings.get('embeddingModel'), text);
      return { type: 'ollama', vec };
    } catch (e) { /* 回退 */ }
  }
  // Transformers.js（浏览器本地推理）
  if (typeof TransformersEmbedder !== 'undefined' && TransformersEmbedder.ready) {
    try { return { type: 'transformers', vec: await TransformersEmbedder.embed(text) }; } catch (e) { /* 回退 */ }
  }
  // 本地哈希（兜底，区分度有限但可用）
  if (typeof localHashVector === 'function') return { type: 'hash', vec: localHashVector(text) };
  return null;
}

/* ===================== [功能2] 智能路由 ===================== */

/** [路由修复] 从模型名提取参数量（B 单位），如 qwen3.5:9b→9、qwen2.5:1.5b→1.5；解析不到返回 null（纯函数便于单测）
 * @param {string} name - 模型名
 * @returns {number|null} */
function modelSizeB(name) {
  const m = String(name || '').match(/(\d+(?:\.\d+)?)\s*b\b/i);
  return m ? parseFloat(m[1]) : null;
}

/** [显示统一] 是否本地模型（与 CostEngine 单一口径：Ollama 后端或价格为 0）。
 * 云端模型绝不能参与“最小/居中/最大本地模型”启发式，reason 也不得称其为本地。 */
function isLocalModelName(name) {
  try {
    if (AI && AI.backend === 'ollama') return true;
    if (typeof CostEngine !== 'undefined' && typeof CostEngine.isLocal === 'function') {
      return !!CostEngine.isLocal(name);
    }
  } catch (e) { /* 回退：无法判断时按云端处理 */ }
  return false;
}

/** 当前后端真正可用的“本地模型”名数组（去重保序） */
function localModelNames() {
  const seen = {};
  const out = [];
  (AI.models || []).forEach(m => {
    const n = m && m.name;
    if (n && isLocalModelName(n) && !seen[n]) { seen[n] = true; out.push(n); }
  });
  return out;
}

/** [本地优先防御] 出口类型守卫：模型名必须是单个非空字符串。
 * 若意外传入数组则取首个元素；仍非法时回退默认/首个可用模型，绝不让数组流到发请求层。 */
function ensureStringModel(value, fallback) {
  let v = value;
  if (Array.isArray(v)) v = v[0];
  if (typeof v !== 'string' || !v.trim()) {
    v = fallback || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
  }
  return v;
}

/* ===================== [v3.15.13] 模型失败熔断 + 已驻留模型亲和 ===================== */

/** 失败模型短期熔断（内存态 + [批次修复 #9] localStorage 持久化：键名带版本前缀防脏数据，
 * 刷新后保留对失败模型的临时禁用直到冷却期结束）。 */
const GatewayBreaker = {
  _fails: {},
  _persistKey() {
    return 'moray_breaker_' + ((typeof window !== 'undefined' && window.MORAY_BUILD) || 'dev');
  },
  _load() {
    try {
      const raw = localStorage.getItem(this._persistKey());
      if (raw) {
        const j = JSON.parse(raw);
        if (j && typeof j === 'object') this._fails = j;
      }
    } catch (e) { /* 忽略 */ }
  },
  _save() {
    try { localStorage.setItem(this._persistKey(), JSON.stringify(this._fails)); } catch (e) { /* 忽略 */ }
  },
  cooldownMs() {
    let s = parseInt(MoraySettings.get('circuitBreakerCooldownSec') || 90, 10);
    if (!isFinite(s) || s <= 0) s = 90;
    return Math.max(10, Math.min(600, s)) * 1000;
  },
  /** 仍在冷却返回剩余秒数；到期自动清除并返回 0（half-open 语义由路由再次尝试触发） */
  cooling(model) {
    const f = this._fails[model];
    if (!f) return 0;
    const left = this.cooldownMs() - (Date.now() - f.at);
    if (left <= 0) { delete this._fails[model]; this._save(); return 0; }
    return Math.ceil(left / 1000);
  },
  record(model, reason) {
    if (!model) return;
    const prev = this._fails[model] || {};
    this._fails[model] = {
      at: Date.now(),
      reason: String(reason || '').slice(0, 400),
      consec: (prev.consec || 0) + 1
    };
    this._save();
  },
  clear(model) { delete this._fails[model]; this._save(); },
  clearAll() { this._fails = {}; this._save(); },
  snapshot() {
    return Object.keys(this._fails).map(model => ({
      model,
      left: this.cooling(model),
      reason: this._fails[model] ? this._fails[model].reason : '',
      consec: this._fails[model] ? this._fails[model].consec : 0
    })).filter(x => x.left > 0);
  }
};
GatewayBreaker._load(); // [批次修复 #9] 启动时恢复持久化熔断（冷却期内的失败模型保留临时禁用）

/** Ollama 当前驻留模型名（GET /api/ps，5 秒短缓存；失败静默返回空，不报错不阻塞路由） */
let __psCache = { at: 0, list: null };
let __lastPsMs = 0;
async function ollamaLoadedModels() {
  if (!AI || AI.backend !== 'ollama') return [];
  const now = Date.now();
  if (__psCache.list && now - __psCache.at < 5000) return __psCache.list;
  try {
    const p0 = performance.now();
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch(AI.ollamaURL + '/api/ps', { signal: ctrl.signal });
    clearTimeout(timer);
    __lastPsMs = performance.now() - p0;
    if (!res.ok) return [];
    const j = await res.json();
    __psCache = { at: now, list: ((j.models || []).map(m => m && m.name)).filter(Boolean) };
    return __psCache.list;
  } catch (e) { /* 静默退化按参数量选择 */ return []; }
}

/** 冷却期内自动路由不应选择的模型，返回 {names, cooling} */
function routingEligibleLocalNames() {
  const all = localModelNames();
  return {
    names: all.filter(n => !GatewayBreaker.cooling(n)),
    cooling: all.filter(n => !!GatewayBreaker.cooling(n))
  };
}

/** 渲染“冷却中的模型”面板（设置→API 智能网关；数据来自 GatewayBreaker） */
function renderBreakerStatus() {
  const wrap = document.getElementById('breakerStatusWrap');
  if (!wrap) return;
  const snap = GatewayBreaker.snapshot();
  wrap.innerHTML = snap.length
    ? snap.map(s => `<div class="flex items-center justify-between">
        <span>${escapeHtml(s.model)} · 剩余 ${s.left}s · ${escapeHtml((s.reason || '').slice(0, 60))}</span>
        <button class="text-[10px] text-brand-cobalt hover:text-brand-cyan" data-breaker-clear="${escapeHtml(s.model)}">清除</button></div>`).join('')
    : '<div>无（全部可用）</div>';
  wrap.querySelectorAll('[data-breaker-clear]').forEach(btn => btn.addEventListener('click', () => {
    GatewayBreaker.clear(btn.dataset.breakerClear);
    renderBreakerStatus();
    showNotification('已清除冷却', btn.dataset.breakerClear + ' 恢复可用', 'success', 1500);
  }));
}

/** 模型来源文案：本地 / 云端（供路由 reason、回退标注与元问题上下文共用） */
function modelSourceLabel(name) {
  return isLocalModelName(name) ? '本地' : '云端';
}

/** 是否“当前用的什么模型 / 是否已切换模型 / 是不是某模型”类元问题。
 * 命中后由网关注入“实际模型事实”上下文，禁止小模型背诵固定人设。 */
function isModelMetaQuestion(text) {
  const t = String(text || '').trim();
  if (!t) return false;
  if (/(现在|当前|目前|这次|刚刚|最近).{0,5}(用|用着|使用的|跑|调用|换|切换).{0,8}(什么|哪个|哪款|啥)\s*(模型|ai|llm|引擎)?/.test(t)) return true;
  if (/(什么|哪个|哪款)\s*(模型|ai|llm)\s*$/.test(t)) return true;
  if (/(换|切|改)(成|到)?.{0,8}(模型|deepseek|qwen|gpt|claude|ollama|本地|云端)/.test(t)) return true;
  if (/(是不是|你是|你是不是|是否|基于|用的是).{0,10}(deepseek|qwen|gpt|claude|ollama|本地|云端)/i.test(t)) return true;
  if (/(模型|后端).{0,6}(是什么|是哪个|换成什么)/.test(t)) return true;
  return false;
}

/** [元问题] 以“系统事实”注入当前实际模型，可被后续回退重注入替换 */
const MODEL_META_TAG = '【系统事实·当前实际模型】';
function injectModelMetaNote(messages, model) {
  const arr = (messages || []).slice();
  const note = {
    role: 'system',
    content: MODEL_META_TAG + model + '（' + modelSourceLabel(model) + '）。' +
      '若用户询问“现在用的什么模型 / 是否已切换 / 是不是某模型”，请直接依据上面这个事实，' +
      '用一句话按“模型名（本地/云端）”的格式如实回答，例如“当前使用 qwen2.5:1.5b（本地）”。' +
      '不要复述身份设定，不要编造不存在的模型名。'
  };
  const idx = arr.findIndex(m => m && m.role === 'system' && String(m.content || '').indexOf(MODEL_META_TAG) === 0);
  if (idx >= 0) arr.splice(idx, 1, note);
  else {
    // 放在既有 system 之后（晚出现的 system 指令优先级更高），避免被默认人设覆盖
    let at = 0;
    while (at < arr.length && arr[at] && arr[at].role === 'system') at++;
    arr.splice(at, 0, note);
  }
  return arr;
}

/** 任务复杂度分类器（轻量规则） */
const TaskRouter = {
  /**
   * 分类消息的任务复杂度
   * @param {Array<{role:string,content:string}>} messages - 消息
   * @returns {'simple'|'medium'|'complex'} 复杂度 */
  classify(messages) {
    const lastUser = (messages || []).filter(m => m.role === 'user').pop();
    const text = lastUser ? String(lastUser.content) : '';
    const len = text.length;
    const codeLen = (text.match(/```[\s\S]*?```/g) || []).join('').length;
    // 复杂：架构/推理/长文档/大段代码；[深度思考] 补强数学/逻辑/推理信号（证明/推导/方程/算法/为什么/原理等）
    if (/(架构|设计方案|权衡|选型|根因分析|性能剖析|迁移方案|推理|逻辑|数学|几何|概率|证明|推导|求解|方程|不等式|算法|复杂度|为什么|原理|悖论|假设|归纳|演绎|反例|边界条件)/.test(text) || len > 1500 || codeLen > 2500) return 'complex';
    // [V2] 代码生成类短语优先归为中等任务（避免"写个函数"被判为简单）
    if (/(写|生成|实现|创建|编写|开发).{0,10}(代码|函数|脚本|组件|类|接口|测试|查询)/.test(text)) return 'medium';
    // [深度思考] 中等：排查/报错/计算/优化类（比寒暄重，比证明题轻）
    if (/(debug|排查|报错|根因|最优|优化|计算|修复|解释|说明)/.test(text)) return 'medium';
    // 简单：翻译/格式化/短问答/寒暄
    if (/(^|[\s，。])翻译|格式化|排版|错别字|摘要总结|你好|在吗|谢谢|继续/.test(text) || (len < 80 && codeLen === 0)) return 'simple';
    return 'medium';
  },

  /**
   * 按复杂度选择模型（首选 -> 备用链）
   * @param {'simple'|'medium'|'complex'} type - 任务类型
   * @returns {{model:string, fallbacks:string[], reason:string}} 路由决策 */
  route(type) {
    const cfg = MoraySettings.get('routingConfig') || {};
    const conf = cfg[type] || {};
    const available = AI.models.map(m => m.name);
    const pick = (name) => name && (available.includes(name) || AI.backend === 'openai') ? name : '';
    const primary = pick(conf.primary);
    const fallback = pick(conf.fallback);
    if (primary) {
      // 显式配置首选/备用：命中即用（可能为云端），reason 只写“首选模型”，不写“本地”。
      return { model: primary, fallbacks: fallback ? [fallback] : [], reason: `${type}任务 → 首选模型` };
    }
    // [路由修复] 未配置 primary 时的启发式：
    // 1) 候选先用 CostEngine 过滤为真正的本地 Ollama 模型，云端模型绝不参与；
    // 2) simple 分支不允许“名字不含 Nb”的模型充当“最小本地模型”；
    // 3) 本地候选为空 → 返回空决策，由 Gateway.route 回退默认/可用模型并如实说明。
    const elig = routingEligibleLocalNames();
    const localAvailable = elig.names;
    const coolingNote = elig.cooling.length ? '（冷却跳过 ' + elig.cooling.join('、') + '）' : '';
    if (localAvailable.length) {
      const withSize = localAvailable.map((n) => ({ n, size: modelSizeB(n) }));
      const sized = withSize.filter(x => x.size !== null).sort((a, b) => a.size - b.size);
      const unsized = withSize.filter(x => x.size === null);
      const allowUnsized = type !== 'simple'; // simple 禁用无参数量模型（防“最小”名不副实）
      const candidates = allowUnsized ? sized.concat(unsized) : sized;
      if (candidates.length) {
        const sorted = candidates.map(x => x.n);
        const idx = type === 'simple' ? 0
          : type === 'complex' ? Math.max(0, sorted.length - 1)
          : Math.min(Math.floor(sorted.length / 2), sorted.length - 1);
        const model = sorted[idx];
        const sizeDesc = modelSizeB(model);
        const label = sizeDesc != null ? shortModelName(model) + ':' + sizeDesc + 'b' : shortModelName(model);
        const why = type === 'simple' ? '最小本地模型'
          : type === 'complex' ? '参数量最大本地模型'
          : '居中模型';
        return { model, fallbacks: sorted.filter(n => n !== model).slice(0, 1), reason: `${type}任务 → ${why} ${label}${coolingNote}` };
      }
    }
    if (elig.cooling.length && !localAvailable.length) {
      return { model: '', fallbacks: [], reason: `${type}任务 → 本地模型全部处于冷却（${elig.cooling.join('、')}）` };
    }
    return { model: '', fallbacks: [], reason: `${type}任务 → 无本地模型` };
  }
};

/* ===================== [功能4] 用量监控 ===================== */

/** Token 用量与费用追踪 */
const UsageTracker = {
  /** 预算提醒档位（每日一次）
   * @type {Object} */
  _budgetNotified: {},

  /**
   * 估算费用（人民币，委托 CostEngine 唯一定价源）
   * @param {string} model - 模型名
   * @param {number} promptTokens - 输入token
   * @param {number} completionTokens - 输出token
   * @returns {number} 人民币（元） */
  estimateCost(model, promptTokens, completionTokens) {
    if (typeof CostEngine !== 'undefined') return CostEngine.estimateCost(model, promptTokens, completionTokens);
    return 0;
  },

  /**
   * 记录一次请求用量
   * @param {string} model - 模型
   * @param {Object} stats - {promptTokens, tokens, tokPerSec}
   * @param {boolean} [fromCache] - 是否缓存命中
   * @param {Object} [costInfo] - {costCNY, savedCNY, baselineCNY}
   * @returns {Promise<void>} */
  async record(model, stats, fromCache, costInfo) {
    try {
      const usage = await gatewayMetaGet('__usage__', { byDay: {} });
      const day = dayKey();
      const d = usage.byDay[day] = usage.byDay[day] || { prompt: 0, completion: 0, cost: 0, requests: 0, byModel: {} };
      const pt = (stats && stats.promptTokens) || 0;
      const ct = (stats && stats.tokens) || 0;
      const cost = this.estimateCost(model, pt, ct);
      d.prompt += pt; d.completion += ct; d.cost += cost; d.requests++;
      // [成本中心] 新增聚合字段（向后兼容：旧数据缺失按 0）
      d.costCNY = (d.costCNY || 0) + ((costInfo && costInfo.costCNY) || 0);
      d.savedCNY = (d.savedCNY || 0) + ((costInfo && costInfo.savedCNY) || 0);
      d.baselineCNY = (d.baselineCNY || 0) + ((costInfo && costInfo.baselineCNY) || 0);
      d.byModel[model] = d.byModel[model] || { prompt: 0, completion: 0, cost: 0, costCNY: 0 };
      d.byModel[model].prompt += pt; d.byModel[model].completion += ct; d.byModel[model].cost += cost;
      d.byModel[model].costCNY = (d.byModel[model].costCNY || 0) + ((costInfo && costInfo.costCNY) || cost);
      await gatewayMetaSet('__usage__', usage);
      // [统一人民币预算] 唯一预算入口：CostBudget（含 autoDegrade 降级与 pauseAPI 暂停）
      if (typeof CostBudget !== 'undefined') CostBudget.afterRecord().catch(() => {});
      updateGatewayStatusBar();
    } catch (e) { console.warn('[MoRay] usage record failed:', e); }
  },

  /** 汇总区间用量
   * @param {'today'|'week'|'month'} range - 区间
   * @returns {Promise<{prompt:number, completion:number, cost:number, requests:number, byModel:Object}>} 汇总 */
  async summarize(range) {
    const usage = await gatewayMetaGet('__usage__', { byDay: {} });
    const now = new Date();
    const start = range === 'today'
      ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
      : range === 'week' ? now.getTime() - 7 * 86400e3
      : new Date(now.getFullYear(), now.getMonth(), 1).getTime();
    const startKey = dayKey(start);
    const sum = { prompt: 0, completion: 0, cost: 0, requests: 0, byModel: {} };
    Object.keys(usage.byDay).forEach(k => {
      if (k < startKey && range !== 'month') return;
      if (range === 'month' && !k.startsWith(dayKey().slice(0, 7))) return;
      const d = usage.byDay[k];
      sum.prompt += d.prompt || 0; sum.completion += d.completion || 0;
      // [统一人民币] 优先 costCNY，历史日（无 costCNY）回退 d.cost
      sum.cost += (d.costCNY != null ? d.costCNY : d.cost) || 0;
      sum.requests += d.requests || 0;
      Object.keys(d.byModel || {}).forEach(m => {
        sum.byModel[m] = sum.byModel[m] || { prompt: 0, completion: 0, cost: 0 };
        const b = d.byModel[m];
        sum.byModel[m].prompt += b.prompt; sum.byModel[m].completion += b.completion;
        sum.byModel[m].cost += (b.costCNY != null ? b.costCNY : b.cost) || 0;
      });
    });
    return sum;
  },

  /** 导出用量报表（CSV + JSON）
   * @returns {Promise<void>} */
  async exportReport() {
    const usage = await gatewayMetaGet('__usage__', { byDay: {} });
    let csv = 'date,prompt_tokens,completion_tokens,cost_cny,requests\n';
    Object.keys(usage.byDay).sort().forEach(k => {
      const d = usage.byDay[k];
      csv += k + ',' + d.prompt + ',' + d.completion + ',' + ((d.costCNY != null ? d.costCNY : d.cost) || 0).toFixed(4) + ',' + d.requests + '\n';
    });
    downloadText('moray_usage.csv', csv, 'text/csv;charset=utf-8');
    downloadJson('moray_usage.json', usage);
    showNotification('用量报表已导出', 'CSV + JSON 双格式', 'success');
  }
};

/* ===================== [功能3] 提示词压缩 ===================== */

/** 提示词压缩工具 */
const PromptCompression = {
  /**
   * 消息清洗：多余空行/重复标点/行尾空白
   * @param {string} text - 原文
   * @returns {{text:string, savedTokens:number}} 清洗结果 */
  clean(text) {
    if (MoraySettings.get('messageCleaning') === 'off') return { text, savedTokens: 0 };
    const before = estimateTokens(text).tokens;
    const cleaned = String(text || '')
      .replace(/\n{3,}/g, '\n\n')
      .replace(/([，。！？,.!?])\1{2,}/g, '$1')
      .replace(/[ \t]+$/gm, '')
      .replace(/^\s+|\s+$/g, '');
    return { text: cleaned, savedTokens: Math.max(0, before - estimateTokens(cleaned).tokens) };
  },

  /** 高效系统提示词模板（较常规写法短30-50%） */
  EFFICIENT_SYSTEM_PROMPTS: [
    { name: '精简代码助手', content: '资深工程师。直接给代码+关键注释，先答案后解释，不用客套。不确定就说明假设。' },
    { name: '精简审查', content: '代码审查：按正确性/安全/性能/可读性列问题，每条附修复代码，最后1-10评分。' },
    { name: '精简翻译', content: '专业译者。只输出译文，保留格式与术语；歧义句给出2个译法。' },
    { name: '精简问答', content: '直接回答问题。要点化，每点一行。不知道就说不知道，不编造。' }
  ],

  /**
   * 规则化精简系统提示词（去客套/并句/去冗余空白）
   * @param {string} prompt - 原提示词
   * @returns {{text:string, saved:number}} 优化结果（saved为节省token数） */
  optimizeSystemPrompt(prompt) {
    const before = estimateTokens(prompt).tokens;
    let out = String(prompt || '')
      .replace(/(请你?|麻烦你?|谢谢你?|辛苦你?)/g, '')
      .replace(/(非常重要|千万要?注意|务必记住)[，,：:]?/g, '注意：')
      .replace(/\n{3,}/g, '\n\n')
      .replace(/^[ \t]+|[ \t]+$/gm, '')
      .replace(/^\s+|\s+$/g, '');
    // 合并完全重复的行
    const seen = new Set();
    out = out.split('\n').filter(line => {
      const k = line.trim();
      if (!k) return true;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    }).join('\n');
    return { text: out, saved: Math.max(0, before - estimateTokens(out).tokens) };
  },

  /**
   * 上下文智能截断（策略C：系统+标星+最近；策略B在 Gateway 层做AI摘要）
   * 在 buildRequestMessages 的预算裁剪前调用，返回保留索引集合
   * @param {Object[]} history - 历史消息
   * @param {number} budgetTokens - token预算
   * @returns {number[]|null} 保留的消息索引（null=走默认策略A） */
  selectByStrategy(history, budgetTokens) {
    const strategy = MoraySettings.get('truncationStrategy') || 'recent';
    if (strategy !== 'starred') return null;
    // 策略C：从最新往回收集，标星消息优先无条件保留
    const picked = [];
    let used = 0;
    for (let i = history.length - 1; i >= 0; i--) {
      const t = estimateTokens(history[i].content).tokens;
      if (history[i].starred || used + t <= budgetTokens) {
        picked.push(i);
        used += t;
      }
    }
    return picked.reverse();
  }
};

/* ===================== [功能6] 健康检查 ===================== */

/** API 端点健康监控 */
const HealthMonitor = {
  /** @type {{ollama:{status:string, latency:number, fails:number}, openai:{...}}} */
  state: {
    ollama: { status: 'unknown', latency: 0, fails: 0 },
    openai: { status: 'unknown', latency: 0, fails: 0 }
  },

  /** 启动定时探活（5分钟）
   * @returns {void} */
  start() {
    if (this._timer) return;
    this._timer = setInterval(() => { if (!document.hidden) this.checkAll(); }, 5 * 60000);
    this.checkAll();
  },

  /** 探活单个端点
   * @param {'ollama'|'openai'} name - 端点名
   * @returns {Promise<void>} */
  async checkOne(name) {
    const st = this.state[name];
    const started = performance.now();
    try {
      let ok = false;
      if (name === 'ollama') {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 3000);
        const res = await fetch(AI.ollamaURL + '/api/tags', { signal: ctrl.signal });
        clearTimeout(t);
        ok = res.ok;
      } else if (MoraySettings.get('openaiEnabled') && AI.openaiBase) {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 5000);
        const res = await fetch(AI.openaiBase + '/models', { headers: AI._openaiHeaders(), signal: ctrl.signal });
        clearTimeout(t);
        ok = res.ok;
      } else {
        st.status = 'unknown';
        return;
      }
      st.latency = Math.round(performance.now() - started);
      st.status = st.latency < 1200 ? 'ok' : st.latency < 3000 ? 'slow' : 'degraded';
      if (ok) st.fails = 0; else throw new Error('HTTP 非 200');
    } catch (e) {
      st.latency = 0;
      st.fails++;
      // 连续失败3次标记不可用
      st.status = st.fails >= 3 ? 'down' : 'degraded';
    }
  },

  /** 探活全部端点
   * @returns {Promise<void>} */
  async checkAll() {
    await Promise.all([this.checkOne('ollama'), this.checkOne('openai')]);
    renderGatewayHealth();
  },

  /** 状态点样式类
   * @param {string} status - ok|slow|degraded|down|unknown
   * @returns {string} CSS类 */
  dotClass(status) {
    return status === 'ok' ? 'success' : status === 'down' ? 'danger' : status === 'unknown' ? '' : 'warn-dot';
  }
};

/* ===================== 网关门面（统一入口） ===================== */

/** MoRay API 网关：缓存 → 路由 → 重试 → 用量 */
const Gateway = {
  /** 路由降级日志（最近20条）
   * @type {Array} */
  fallbackLog: [],
  /** 最近一次“最终模型预热”状态（串行：不同模型先等上一轮结束，禁止并发争显存） */
  __warm: null,

  /** [首字延迟修复] 路由定稿后的对齐预热：
   * 仅对“最终要用的模型”在真实请求前预热；已驻留则直接跳过；
   * 若已有其它模型预热进行中，先等其结束（OLLAMA_MAX_LOADED_MODELS=1 下不同模型会互踢）。
   * 失败静默；预热与真实对话串行，绝不并发。
   * @param {string} model - 最终模型名 */
  async ensureWarmup(model) {
    try {
      if (!model || !AI || AI.backend !== 'ollama' || typeof AI.warmupModel !== 'function') return;
      const loaded = await ollamaLoadedModels();
      if (loaded.includes(model)) return;
      // 若正在预热不同模型，先等它结束（避免两个模型同时加载互踢）
      if (this.__warm && this.__warm.p && this.__warm.model !== model) {
        await this.__warm.p.catch(() => {});
      }
      if (this.__warm && this.__warm.p && this.__warm.model === model) {
        await this.__warm.p.catch(() => {});
        return;
      }
      const p = AI.warmupModel(model);
      this.__warm = { model, p };
      await p.catch(() => {});
    } catch (e) { /* 预热失败静默 */ }
  },

  /**
   * 是否允许云端调用（预算/暂停/网关开关）
   * @returns {Promise<void>} 阻止时 throw */
  async guardCloud() {
    if (MoraySettings.get('pauseAPI') && AI.backend === 'openai') {
      throw new Error('云端 API 已因预算暂停。可在 设置 → API 智能网关 中恢复，或切换到本地模型。');
    }
  },

  /**
   * 智能选择模型（路由层）
   * @param {Object} opts - 请求（含 _userPicked：会话内显式手选模型时才为 true）
   * @returns {Promise<{model:string, routeInfo:Object|null}>} 决策 */
  async route(opts) {
    // [修复] 显式锁定语义：仅会话内手选模型（_userPicked）才短路；传入 defaultModel 仅作兜底候选
    if (!MoraySettings.get('routingEnabled') || opts._userPicked === true) return { model: opts.model || '', routeInfo: null };
    const type = TaskRouter.classify(opts.messages);
    let decision = TaskRouter.route(type);
    // [修复] 路由未配置时回退默认模型/首个可用模型，绝不返回空模型
    if (!decision || !decision.model) {
      const fb = opts.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
      const useDefault = !!(opts.model || MoraySettings.get('defaultModel'));
      const why = (!AI.models || !AI.models.length) ? '无可用模型'
        : '无本地模型';
      const src = useDefault ? '默认模型' : '首个可用模型';
      const label = fb ? shortModelName(fb) : '';
      return { model: fb, routeInfo: { type, model: fb, fallbacks: [], reason: type + '任务 → ' + why + '，使用' + src + (label ? ' ' + label : '') } };
    }
    // [v3.15.13] 候选：冷却过滤后的本地模型（与 TaskRouter.route 一致）
    const eligBase = routingEligibleLocalNames();
    const localNames = eligBase.names;
    let affinityApplied = false;
    // 已驻留模型亲和：simple/medium 且已驻留模型仍可用时优先复用（不依赖 routingLocalFirst）
    if (MoraySettings.get('routingPreferLoaded') && localNames.length) {
      const loaded = await ollamaLoadedModels();
      const loadedCandidates = loaded.filter(n => localNames.includes(n));
      // 已驻留模型选法：simple/medium 直接复用任一驻留模型；
      // complex 仅在驻留模型是本地最大参数量时复用（否则仍按原策略选最大，叠加冷却过滤）。
      let loadedPick = null;
      if (loadedCandidates.length) {
        if (type === 'complex') {
          const maxSize = Math.max.apply(null, localNames.map(n => modelSizeB(n) || 0));
          loadedPick = loadedCandidates.find(n => modelSizeB(n) === maxSize) || null;
        } else {
          loadedPick = loadedCandidates[0];
        }
      }
      if (loadedPick && loadedPick === decision.model) {
        // 决策模型本就驻留：标记亲和命中，阻止后续“本地优先”再切成理论最小造成重载
        affinityApplied = true;
      } else if (loadedPick) {
        const fbSet = [];
        [decision.model].concat(localNames.filter(n => n !== loadedPick)).forEach(n => {
          if (typeof n === 'string' && n.trim() && !fbSet.includes(n)) fbSet.push(n);
        });
        decision = { model: loadedPick, fallbacks: fbSet, reason: type + '任务 → 复用已驻留模型，避免重载（' + loadedPick + '）' };
        affinityApplied = true;
      }
    }
    // [成本中心] 本地优先：未命中亲和且开启时取参数量最小本地模型
    if (MoraySettings.get('routingLocalFirst') && !affinityApplied && localNames.length) {
      // [本地优先修复] 排序后只取“参数量最小的单个本地模型”字符串：
      // ranked 是候选数组，localBest 必须 ranked[0]；严禁把整个数组赋给 decision.model。
      const withSize = localNames.map(n => ({ n, size: modelSizeB(n) }));
      const sized = withSize.filter(x => x.size !== null).sort((a, b) => a.size - b.size);
      const unsized = withSize.filter(x => x.size === null);
      const ranked = sized.concat(unsized).map(x => x.n);
      const localBest = ranked[0];
      if (typeof localBest === 'string' && localBest.trim() && localBest !== decision.model) {
        // [速度优化] complex 任务保持“参数量最大”策略，不被本地优先降级成最小模型
        const complexKeepMax = type === 'complex' &&
          (modelSizeB(decision.model) || 0) >= (modelSizeB(localBest) || 0);
        if (!complexKeepMax) {
          const extra = ranked.slice(1).filter(n => typeof n === 'string' && n.trim() && n !== localBest);
          const fbSet = [];
          [decision.model].concat(extra).forEach(n => {
            if (typeof n === 'string' && n.trim() && !fbSet.includes(n)) fbSet.push(n);
          });
          const cooling = eligBase.cooling.length ? '；冷却跳过 ' + eligBase.cooling.join('、') : '';
          decision = { model: localBest, fallbacks: fbSet, reason: type + '任务 → 本地优先（' + localBest + '）' + cooling };
        }
      }
    }
    // [路由修复] 兜底：decision.model 必须非空合法字符串，否则回退默认模型
    if (!decision || !decision.model) {
      const fb = opts.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
      const useDefault = !!(opts.model || MoraySettings.get('defaultModel'));
      const why = (!AI.models || !AI.models.length) ? '无可用模型' : '无本地模型';
      const src = useDefault ? '默认模型' : '首个可用模型';
      const label = fb ? shortModelName(fb) : '';
      decision = { model: fb, fallbacks: [], reason: type + '任务 → ' + why + '，使用' + src + (label ? ' ' + label : '') };
    }
    // [成本中心] 超预算自动降级：选最便宜可用模型（cheapest 始终为字符串，初始为当前决策模型）
    if (MoraySettings.get('autoDegrade') && typeof CostBudget !== 'undefined' && CostBudget.isOver()) {
      const available = AI.models.map(m => m.name);
      if (available.length) {
        let cheapest = decision.model, minCost = Infinity;
        available.forEach(n => {
          const p = CostEngine.resolvePrice(n);
          const total = p.input + p.output;
          if (total < minCost) { minCost = total; cheapest = n; }
        });
        if (cheapest !== decision.model) {
          decision = { model: cheapest, fallbacks: [decision.model].filter(Boolean), reason: type + '任务 → 已超预算，自动降级到最便宜模型（' + shortModelName(cheapest) + '）' };
        }
      }
    }
    const info = { type, ...decision };
    // 路由统计
    try {
      const meta = await gatewayMetaGet('__meta__', { hits: 0, misses: 0, tokensSaved: 0, costSaved: 0, byDay: {} });
      meta.routeStats = meta.routeStats || { byType: {}, byModel: {} };
      meta.routeStats.byType[type] = (meta.routeStats.byType[type] || 0) + 1;
      if (info.model) meta.routeStats.byModel[info.model] = (meta.routeStats.byModel[info.model] || 0) + 1;
      await gatewayMetaSet('__meta__', meta);
    } catch (e) { /* 统计失败不阻断 */ }
    // [本地优先防御] 最终出口类型守卫：model 永远是单个字符串（数组取首个、非法则回退默认模型）
    const finalModel = ensureStringModel(
      info.model || opts.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name),
      ''
    );
    if (info.model !== finalModel) info = Object.assign({}, info, { model: finalModel });
    return { model: finalModel, routeInfo: info };
  },

  /**
   * 带指数退避的重试包装
   * @param {Function} fn - 每次尝试执行的工厂 () => Promise
   * @param {number} [maxRetries] - 最大重试次数
   * @param {Function} [onRetry] - 重试回调 (attempt, total)
   * @returns {Promise<*>} 结果 */
  async withRetry(fn, maxRetries, onRetry) {
    maxRetries = maxRetries != null ? maxRetries : 2;
    let attempt = 0;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      try {
        return await fn();
      } catch (e) {
        const abort = e && (e.name === 'AbortError');
        const retryable = !abort && (!e || !e.noRetry);
        if (!retryable || attempt >= maxRetries) throw e;
        attempt++;
        const delay = Math.min(8000, 1000 * Math.pow(2, attempt - 1));
        if (onRetry) onRetry(attempt, maxRetries + 1);
        showNotification('请求失败，正在重试', `第 ${attempt}/${maxRetries + 1} 次 · ${delay / 1000}s 后自动重试`, 'warning', 2000);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  },

};

// 回放需要 onChunk：包装 _playback 的正确实现（覆盖上面的占位循环）
Gateway._playback = function (rec, controller, info) {
  const opts = this._currentOpts || {};
  const promise = (async () => {
    const chunkSize = 80;
    const text = rec.reply || '';
    for (let i = 0; i < text.length; i += chunkSize) {
      if (controller.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      if (opts.onChunk) opts.onChunk({ content: text.slice(i, i + chunkSize), reasoning: '', done: false });
      await new Promise(r => setTimeout(r, 24));
    }
    if (opts.onChunk) opts.onChunk({ content: '', reasoning: '', done: true });
    return {
      content: text, reasoning: rec.reasoning || '',
      stats: { tokPerSec: null, ms: 0, firstMs: 0, tokens: rec.tokens || 0 },
      _cache: { from: info.from, similarity: info.similarity, tokens: rec.tokens || 0, cost: rec.cost || 0 },
      // [成本中心] 命中自有缓存：实际花费0；基线按"该请求若真实调用"估算
      _cost: (typeof CostEngine !== 'undefined') ? (() => {
        const promptTokens = estimateTokens(JSON.stringify((opts.messages || []).slice(0, 2000))).tokens;
        const base = CostEngine.calculate({ model: opts.model, promptTokens, completionTokens: rec.tokens || 0, cacheTokens: 0, fromCache: true });
        return Object.assign({ promptTokens, completionTokens: rec.tokens || 0, cacheTokens: 0, fromCache: true, routeReason: '' }, base);
      })() : null
    };
  })();
  return { controller, promise };
};

/**
 * [工作流修复] 非流式聊天入口：工作流 AI 节点等曾调用 Gateway.chat 但方法从未定义，
 * 导致“Gateway.chat is not a function”。薄封装 = 网关门面路由 → AI.chat → 备用链 → 用量记录。
 * @param {Object} opts - {model, messages, ...}（同 AI.chat）
 * @returns {Promise<{content:string, stats:Object}>} */
Gateway.chat = function (opts) {
  const self = this;
  const promise = (async () => {
    await self.guardCloud();
    const routed = await self.route(opts);
    const eff = Object.assign({}, opts, { model: routed.model });
    let result;
    try {
      result = await AI.chat(eff);
    } catch (e) {
      const fbs = (routed.routeInfo && routed.routeInfo.fallbacks) || [];
      if (e && e.name === 'AbortError') throw e;
      if (fbs.length && fbs[0] !== eff.model) {
        result = await AI.chat(Object.assign({}, eff, { model: fbs[0] }));
        eff.model = fbs[0];
      } else {
        throw e;
      }
    }
    try {
      if (result && result.stats && typeof UsageTracker !== 'undefined') {
        UsageTracker.record(eff.model, result.stats, false).catch(() => {});
      }
    } catch (e) { /* 用量记录失败不阻断 */ }
    return result;
  })();
  return promise;
};

/**
 * 网关流式聊天（修正版）：缓存命中时把 onChunk 交给回放器
 * @param {Object} opts - 请求参数
 * @returns {{controller:AbortController, promise:Promise<Object>}} 句柄 */
Gateway.chatStream = function (opts) {
  const controller = new AbortController();
  this._currentOpts = opts;
  const self = this;
  const promise = (async () => {
    const T0 = performance.now();
    const timing = { startMs: 0 };
    await self.guardCloud();
    const routed = await self.route(opts);
    timing.routeMs = performance.now() - T0; // 含路由决策 + /api/ps(缓存) + 冷却/亲和判断
    timing.psMs = (typeof __lastPsMs === 'number') ? __lastPsMs : 0;
    timing.tagsMs = (AI && typeof AI.__lastTagsMs === 'number') ? AI.__lastTagsMs : 0;
    // [小补丁] 路由结果早期回调：加载态气泡立即刷新为实际命中模型/reason（不等流式结束）
    if (typeof opts.onRoute === 'function') {
      try { opts.onRoute(routed); } catch (e) { /* 回调异常不影响主链路 */ }
    }
    // [元问题] 识别“现在用的什么模型/是否已切换”类问题：注入实际模型事实，且不走缓存回放
    const _lastUser = (opts.messages || []).filter(m => m && m.role === 'user').pop();
    const metaModelQ = !!(_lastUser && isModelMetaQuestion(String(_lastUser.content || '')));
    const effective = Object.assign({}, opts, {
      model: routed.model,
      temperature: opts.temperature != null ? opts.temperature : MoraySettings.get('temperature'),
      bypassCache: !!(opts.bypassCache || metaModelQ)
    });
    if (metaModelQ) effective.messages = injectModelMetaNote(effective.messages, routed.model);
    const canCache = MoraySettings.get('cacheEnabled') && !effective.bypassCache && !effective.hasImages;
    // [工具调用] 仅 openai/ollama 后端 + 总开关 + 有可用工具时启用；Ollama 走前端直连原生 tools；
    // 不支持/报错自动回退普通流式；对比等场景可传 noTools 强制关闭
    const useTools = !!(!opts.noTools && MoraySettings.get('toolsEnabled') &&
      (AI.backend === 'openai' || AI.backend === 'ollama') &&
      typeof ToolRegistry !== 'undefined' && ToolRegistry.listForRequest().length > 0);
    if (canCache) {
      try {
        const exact = await GatewayCache.lookupExact(GatewayCache.requestHash(effective));
        // [修复] 必须返回回放的 promise（否则外层会把 {controller,promise} 句柄当作结果）
        if (exact) return self._playback(exact, controller, { from: 'exact', similarity: 1 }).promise;
        const sem = await GatewayCache.lookupSemantic(effective);
        if (sem) return self._playback(sem.rec, controller, { from: 'semantic', similarity: sem.similarity }).promise;
        await GatewayCache.bumpMeta('miss');
      } catch (e) { console.warn('[MoRay] cache lookup failed:', e); }
    }
    // [首字延迟修复] 路由已定稿：若本地模型未驻留，先串行预热最终模型再发真实请求
    if (AI.backend === 'ollama' && effective.model) {
      const w0 = performance.now();
      await self.ensureWarmup(effective.model);
      timing.warmMs = performance.now() - w0;
    }
    // TTFT 埋点：首个 token 到达时间（routingDebug=true 或 ?ttft=1 时输出）
    let firstTokenAt = 0;
    const origChunk = opts.onChunk;
    effective.onChunk = function (...args) {
      if (!firstTokenAt) {
        firstTokenAt = performance.now();
        timing.firstTokenMs = firstTokenAt - T0;
        if (MoraySettings.get('routingDebug') || (typeof location !== 'undefined' && /ttft=1/.test(location.search))) {
          console.log('[MoRay TTFT]', JSON.stringify(timing));
        }
      }
      if (typeof origChunk === 'function') origChunk.apply(this, args);
    };
    let abortWasTimeout = false;
    const attemptFn = (model) => {
      const timeoutMs = (MoraySettings.get('requestTimeout') || 30) * 1000;
      const timer = setTimeout(() => { abortWasTimeout = true; controller.abort('timeout'); }, timeoutMs);
      const stream = AI.chatStream(Object.assign({}, effective, { model }));
      controller.signal.addEventListener('abort', () => { try { stream.controller.abort(); } catch (e) { /* 忽略 */ } }, { once: true });
      stream.promise.finally(() => clearTimeout(timer)).catch(() => {});
      return stream.promise;
    };
    let result;
    // [工具兜底] 标志位：工具模式失败后最多做一次"去 tools 普通流式"重试，防死循环
    let toolsFallbackTried = false;
    let usedFallback = false; // [显示统一] 是否真的发生了备用模型回退
    timing.requestStartMs = performance.now() - T0;
    try {
      if (useTools) {
        // [工具调用] 工具闭环（内部逐轮 record 成本；异常回退下方兜底/降级链路）
        // [深度思考] 透传 effective（含 think），工具轮内 AI.chat 一并下发
        result = await self.runWithTools(Object.assign({}, effective, { onChunk: opts.onChunk, signal: controller.signal }), opts.onToolStep);
      } else {
        result = await self.withRetry(() => attemptFn(effective.model), 2);
      }
    } catch (primaryErr) {
      // [v3.15.13] 失败熔断：真实失败（HTTP/加载/超时）才记录；用户主动停止(AbortError)不计
      const isUserAbort = !!(primaryErr && primaryErr.name === 'AbortError' && !abortWasTimeout);
      if (primaryErr && !isUserAbort) {
        GatewayBreaker.record(effective.model, (primaryErr && primaryErr.message) || String(primaryErr));
      }
      // [工具兜底] 后端不支持 tools / 工具轮超上限 → 去掉 tools 改用普通流式重试一次；用户主动中止（AbortError）不兜底
      if (useTools && !toolsFallbackTried && result === undefined && !isUserAbort) {
        toolsFallbackTried = true;
        showNotification('当前后端不支持工具调用', '已切换普通对话', 'info', 2500);
        try {
          result = await self.withRetry(() => attemptFn(effective.model), 2);
        } catch (fbErr) {
          primaryErr = fbErr; // 兜底也失败 → 继续走原有备用模型/报错链路
        }
      }
      if (result === undefined) {
        const fallbacks = routed.routeInfo ? routed.routeInfo.fallbacks : [];
        if (fallbacks.length && effective.model !== fallbacks[0]) {
          const primaryWasCloud = !isLocalModelName(effective.model);
          const fallbackIsLocal = isLocalModelName(fallbacks[0]);
          usedFallback = true;
          const errText = String((primaryErr && primaryErr.message) || primaryErr || '');
          // 区分失败类型给用户可读文案；同类回退 10s 内只提示一次，避免每句刷屏
          const typeLabel = /load|加载/i.test(errText) ? '模型加载中'
            : /resource|内存|memory/i.test(errText) ? '资源不足'
            : /timeout|超时/i.test(errText) ? '请求超时'
            : /status|error|失败/i.test(errText) ? '模型返回错误'
            : '请求失败';
          const now = Date.now();
          if (!self.__lastFallbackNotice || self.__lastFallbackNotice.key !== (effective.model + '>' + fallbacks[0]) || now - self.__lastFallbackNotice.at > 10000) {
            self.__lastFallbackNotice = { key: effective.model + '>' + fallbacks[0], at: now };
            showNotification('首选模型不可用', `${typeLabel}，已切换到备用模型 ${shortModelName(fallbacks[0])}`, 'warning', 3000);
          }
          self.fallbackLog.unshift({ at: now, from: effective.model, to: fallbacks[0], type: typeLabel, error: errText.slice(0, 500) });
          self.fallbackLog = self.fallbackLog.slice(0, 20);
          if (metaModelQ) effective.messages = injectModelMetaNote(effective.messages, fallbacks[0]);
          try {
            // 备用模型同样先串行预热再发起（保持 MAX_LOADED=1 下不并发）
            if (AI.backend === 'ollama') {
              const w1 = performance.now();
              await self.ensureWarmup(fallbacks[0]);
              timing.fallbackWarmMs = performance.now() - w1;
            }
            result = await self.withRetry(() => attemptFn(fallbacks[0]), MoraySettings.get('routingFallbacks') || 2);
          } catch (fbErr) {
            GatewayBreaker.record(fallbacks[0], (fbErr && fbErr.message) || String(fbErr));
            throw fbErr;
          }
          // [显示统一] 回退成功后同步路由说明：云端失败 → 已回退本地（气泡小字可见）
          const fallbackNote = (primaryWasCloud && fallbackIsLocal)
            ? '云端失败→已回退本地 ' + shortModelName(fallbacks[0])
            : '失败→已切换备用模型 ' + shortModelName(fallbacks[0]);
          routed.routeInfo = routed.routeInfo
            ? Object.assign({}, routed.routeInfo, { fallback: true, reason: routed.routeInfo.reason + '；' + fallbackNote })
            : null;
          effective.model = fallbacks[0];
        } else {
          throw primaryErr;
        }
      }
    }
    const promptTokens = (result.stats && result.stats.promptTokens) || estimateTokens(JSON.stringify(effective.messages || [])).tokens;
    const completionTokens = (result.stats && result.stats.tokens) || 0;
    // [任务0] 缓存命中 token 来自 usage.prompt_tokens_details.cached_tokens（取不到为0）
    const cachedTokens = (result.stats && result.stats.cachedTokens) || 0;
    // [成本中心] 精确成本核算（按当前价格实时计算）；工具模式已由 runWithTools 逐轮核算并记录
    const costInfo = (useTools && result._cost)
      ? result._cost
      : ((typeof CostEngine !== 'undefined')
        ? CostEngine.calculate({ model: effective.model, promptTokens, completionTokens, cacheTokens: cachedTokens, fromCache: false })
        : { cost: 0, baseline: 0, saved: 0 });
    if (!useTools) {
      UsageTracker.record(effective.model, Object.assign({ promptTokens }, result.stats), false, {
        costCNY: costInfo.cost, savedCNY: costInfo.saved, baselineCNY: costInfo.baseline
      }).catch(() => {});
    }
    // [工具调用] 工具轮结果不写缓存（时间/库内容敏感，避免过时快照）
    if (canCache && !useTools) GatewayCache.save(effective, result).catch(() => {});
    if (MoraySettings.get('routingDebug') || (typeof location !== 'undefined' && /ttft=1/.test(location.search))) {
      if (!timing.firstTokenMs) timing.firstTokenMs = performance.now() - T0;
      console.log('[MoRay TTFT] done', JSON.stringify(timing));
    }
    return Object.assign({}, result, {
      _timing: timing,
      _routed: routed.routeInfo ? {
        model: effective.model,
        type: routed.routeInfo.type,
        reason: routed.routeInfo.reason,
        fallback: usedFallback === true
      } : null,
      _cost: Object.assign({ promptTokens, completionTokens, cacheTokens: cachedTokens, fromCache: false, routeReason: routed.routeInfo ? routed.routeInfo.reason : '' }, costInfo)
    });
  })();
  return { controller, promise };
};

/* ===================== [工具调用] 工具闭环 ===================== */

/**
 * 工具闭环：非流式请求带 tools → 解析 tool_calls → 逐个 ToolRegistry.run →
 * assistant(tool_calls) + role:"tool" 消息按协议回灌 → 再请求 → 模型给出最终答时
 * 分段模拟流式输出给 onChunk（体验与普通流式一致）。
 * 每轮 token 均经 CostEngine 核算 + UsageTracker 记录；单工具失败不中断整轮；
 * 后端不支持 tools / 网络错误 / 轮数超限 → 抛错由上层回退普通流式。
 * @param {Object} opts - {model, messages, onChunk, signal}
 * @param {Function} [onToolStep] - 步骤事件回调 {index,name,label,args,status,resultPreview,ms}
 * @returns {Promise<{content:string, reasoning:string, stats:Object, _toolSteps:Array, _cost:Object}>} */
Gateway.runWithTools = async function (opts, onToolStep) {
  const model = opts.model;
  const tools = ToolRegistry.listForRequest();
  // [阶段0] 轮次默认 8、上限 12（设置页本机 Agent 分区与 AI 工具卡可调）
  const maxRounds = Math.max(1, Math.min(12, MoraySettings.get('toolsMaxRounds') || 8));
  const msgs = (opts.messages || []).map(m => Object.assign({}, m));
  const steps = [];
  let promptTotal = 0, completionTotal = 0;
  let costTotal = 0, savedTotal = 0, baselineTotal = 0;
  const emit = (evt) => { steps[evt.index] = Object.assign({}, evt); if (onToolStep) onToolStep(Object.assign({}, evt)); };

  for (let round = 0; round < maxRounds; round++) {
    if (opts.signal && opts.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    let r;
    try {
      r = await AI.chat(Object.assign({}, opts, { model, messages: msgs, tools }));
    } catch (e) {
      // 后端不支持 tools / 网络失败 → 抛给上层走现有重试/降级/普通流式
      throw e;
    }
    const pt = r.promptTokens || 0;
    const ct = (r.stats && r.stats.tokens) || 0;
    promptTotal += pt; completionTotal += ct;
    // [成本] 工具轮同样经 CostEngine 核算并记录（当轮真实值；await 防并发覆盖）
    try {
      let c = null;
      if (typeof CostEngine !== 'undefined') {
        c = CostEngine.calculate({ model, promptTokens: pt, completionTokens: ct, cacheTokens: 0, fromCache: false });
        costTotal += c.cost; savedTotal += c.saved; baselineTotal += c.baseline;
      }
      await UsageTracker.record(model, { promptTokens: pt, tokens: ct }, false, {
        costCNY: c ? c.cost : 0, savedCNY: c ? c.saved : 0, baselineCNY: c ? c.baseline : 0
      });
    } catch (e) { /* 成本记录失败不阻断 */ }
    const calls = r.toolCalls;
    if (!calls || !calls.length) {
      // 最终答：分段模拟流式，保持用户看到的流式体验
      const text = r.content || '';
      if (opts.onChunk) {
        const CH = 80;
        for (let i = 0; i < text.length; i += CH) {
          if (opts.signal && opts.signal.aborted) break;
          opts.onChunk({ content: text.slice(i, i + CH), reasoning: '', done: false });
          await new Promise(res => setTimeout(res, 24));
        }
        opts.onChunk({ content: '', reasoning: '', done: true });
      }
      return {
        content: text, reasoning: r.reasoning || '',
        stats: { tokPerSec: null, ms: 0, firstMs: 0, tokens: completionTotal, promptTokens: promptTotal },
        _toolSteps: steps,
        _cost: Object.assign({ promptTokens: promptTotal, completionTokens: completionTotal, cacheTokens: 0, fromCache: false, routeReason: '' }, { cost: costTotal, saved: savedTotal, baseline: baselineTotal, model, currency: 'CNY' })
      };
    }
    // 执行本轮全部 tool_calls：逐个执行 + 逐条回灌协议消息
    for (let ci = 0; ci < calls.length; ci++) {
      const call = calls[ci] || {};
      const fn = call.function || {};
      const name = fn.name || '';
      const callId = call.id || ('call_' + steps.length);
      let args = {};
      // [阶段0] arguments 兼容字符串（OpenAI）与对象（Ollama 原生形态统一为协议字符串后仍可被解析）
      try {
        args = (typeof AI !== 'undefined' && typeof AI.toolArgsToObject === 'function')
          ? AI.toolArgsToObject(fn.arguments)
          : JSON.parse(fn.arguments || '{}');
      } catch (e) { args = {}; }
      const toolDef = ToolRegistry.get(name);
      const label = toolDef ? toolDef.label : name;
      const t0 = performance.now();
      const idx = steps.length;
      emit({ index: idx, name, label, args, status: 'running', ms: 0, resultPreview: '' });
      // [修复] 每次工具执行创建可中止 AbortController：外部停止（用户点停止）触发即 abort；
      // 5s 超时由 ToolRegistry.run 内部负责（超时=失败结果并协作取消，不中断循环）
      const toolCtl = new AbortController();
      const onOuterAbort = () => { if (!toolCtl.signal.aborted) toolCtl.abort(); };
      if (opts.signal) {
        if (opts.signal.aborted) onOuterAbort();
        else opts.signal.addEventListener('abort', onOuterAbort, { once: true });
      }
      let res;
      try {
        res = await ToolRegistry.run(name, args, {
          model, signal: toolCtl.signal,
          // [阶段0] 审批过程事件：native 工具等待人工授权时把本步骤转"待审批"（时间线实时可见）
          onStatus: (st) => emit({ index: idx, name, label, args, status: st, ms: Math.round(performance.now() - t0), resultPreview: '' })
        });
      } finally {
        if (opts.signal) opts.signal.removeEventListener('abort', onOuterAbort);
      }
      const ms = Math.round(performance.now() - t0);
      // [阶段0] 用户拒绝（data.denied）：步骤卡显示"已拒绝"，回灌内容让模型如实回应
      const denied = !!(res.ok && res.data && res.data.denied);
      const stepStatus = denied ? 'denied' : (res.ok ? 'success' : 'error');
      const preview = res.ok ? (typeof res.data === 'string' ? res.data : JSON.stringify(res.data)) : res.error;
      const resultFull = (res.ok ? (typeof res.data === 'string' ? res.data : JSON.stringify(res.data)) : ('错误：' + res.error)).slice(0, 10000);
      emit({ index: idx, name, label, args, status: stepStatus, ms, resultPreview: preview || '', resultFull });
      msgs.push({ role: 'assistant', content: '', tool_calls: [{ id: callId, type: 'function', function: { name, arguments: fn.arguments || '{}' } }] });
      msgs.push({ role: 'tool', tool_call_id: callId, name, content: res.ok ? JSON.stringify(res.data).slice(0, 4000) : ('错误：' + res.error) });
    }
  }
  throw new Error('工具调用轮数超过上限（' + maxRounds + '），已回退普通流式');
};

/** 更新底部状态栏的今日用量
 * @returns {Promise<void>} */
async function updateGatewayStatusBar() {
  const el = document.getElementById('statusSpeedText');
  try {
    const today = await UsageTracker.summarize('today');
    const cache = await GatewayCache.stats();
    if (el) {
      // [批次修复 #16] 纯前端（无后端）模式下 token 为前端估算，标注“估算”
      const mb = window.MorayBackend;
      const offline = !(mb && mb.connected);
      el.textContent = '今日 ' + ((today.prompt + today.completion) / 1000).toFixed(1) + 'k tok' + (offline ? '（估算）' : '') +
        ' · ' + (typeof CostEngine !== 'undefined' ? CostEngine.formatMoney(today.cost) : ('¥' + today.cost.toFixed(2))) +
        (cache.hits ? ' · 省' + cache.tokensSaved + ' tok' : '');
    }
  } catch (e) { /* 忽略 */ }
}

/** 渲染网关健康状态点到设置页
 * @returns {void} */
function renderGatewayHealth() {
  const wrap = document.getElementById('gatewayHealthDots');
  if (!wrap) return;
  const st = HealthMonitor.state;
  wrap.innerHTML = `
    <span class="flex items-center gap-1.5"><span class="beacon-dot ${HealthMonitor.dotClass(st.ollama.status)}"></span>
      <span class="text-[10px] text-text-tertiary">Ollama ${st.ollama.status}${st.ollama.latency ? ' ' + st.ollama.latency + 'ms' : ''}</span></span>
    <span class="flex items-center gap-1.5"><span class="beacon-dot ${HealthMonitor.dotClass(st.openai.status)}"></span>
      <span class="text-[10px] text-text-tertiary">OpenAI ${st.openai.status}${st.openai.latency ? ' ' + st.openai.latency + 'ms' : ''}</span></span>`;
}

/** 追加"API 智能网关"设置卡片（含缓存管理/路由/用量仪表盘）
 * @returns {Promise<void>} */
async function appendGatewaySettings() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container || container.querySelector('#gatewaySettingsCard')) { renderGatewayHealth(); return; }
  const c = MoraySettings.get();
  const cacheStats = await GatewayCache.stats();
  const today = await UsageTracker.summarize('today');
  const month = await UsageTracker.summarize('month');
  const meta = await gatewayMetaGet('__meta__', { routeStats: { byType: {}, byModel: {} } });
  const rs = meta.routeStats || { byType: {}, byModel: {} };
  const models = AI.models.map(m => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)}</option>`).join('');
  const rc = c.routingConfig || {};
  const sel = (type, field, val) => `<select class="form-select" style="padding:4px 8px;font-size:11px" data-route-${type}-${field}>
    <option value="">（未配置）</option>${models}</select>`.replace(`data-route-${type}-${field}>`, `data-route-${type}-${field}>`).replace('</select>', '</select>');
  const routeSelect = (type, field, current) => `<select class="form-select" style="padding:4px 8px;font-size:11px" data-route="${type}.${field}">
    <option value="">（未配置）</option>${AI.models.map(m => `<option value="${escapeHtml(m.name)}" ${current === m.name ? 'selected' : ''}>${escapeHtml(m.name)}</option>`).join('')}</select>`;

  const maxDay = Math.max(1, ...Object.values(cacheStats.byDay || {}).map(d => Math.max(d.hits || 0, d.queries || 0)));
  const last7 = Object.keys(cacheStats.byDay).sort().slice(-7);

  const card = document.createElement('div');
  card.id = 'gatewaySettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = `
    <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
      <i data-lucide="zap" class="w-4 h-4 text-brand-cyan"></i>API 智能网关
      <div class="ml-auto flex items-center gap-3" id="gatewayHealthDots"></div>
      <div class="toggle-track ${c.gatewayEnabled !== false ? 'active' : ''}" id="setGatewayEnabled" title="网关总开关"><div class="toggle-thumb"></div></div>
    </h3>

    <!-- 缓存管理 -->
    <div class="rounded-lg border border-line-ghost p-4 mb-4">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-medium text-text-primary flex items-center gap-1.5"><i data-lucide="database-zap" class="w-3.5 h-3.5 text-brand-cobalt"></i>请求缓存</span>
        <div class="toggle-track ${c.cacheEnabled !== false ? 'active' : ''}" id="setCacheEnabled"><div class="toggle-thumb"></div></div>
      </div>
      <div class="grid grid-cols-4 gap-2 mb-3 text-center">
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-text-primary">${cacheStats.count}</div><div class="text-[10px] text-text-tertiary">缓存条目</div></div>
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-text-primary">${cacheStats.sizeMB}MB</div><div class="text-[10px] text-text-tertiary">占用</div></div>
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-success">${cacheStats.hitRate}%</div><div class="text-[10px] text-text-tertiary">命中率</div></div>
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-brand-cyan">${cacheStats.tokensSaved}</div><div class="text-[10px] text-text-tertiary">节省 tokens</div></div>
      </div>
      <div class="flex items-end gap-1 mb-3" style="height:48px" id="cacheTrendChart">
        ${last7.map(day => {
          const d = cacheStats.byDay[day];
          const hr = (d.hits + d.queries) ? Math.round(d.hits / (d.hits + d.queries) * 100) : 0;
          return `<div class="flex-1 flex flex-col items-center gap-1" title="${day}：命中 ${d.hits}/${d.hits + d.queries}">
            <div style="width:100%;max-width:26px;height:${Math.max(4, hr * 0.32)}px;background:linear-gradient(180deg,var(--color-brand-cyan),var(--color-brand-cobalt));border-radius:3px"></div>
            <span class="text-[9px] text-text-tertiary">${day.slice(5)}</span></div>`;
        }).join('') || '<span class="text-[10px] text-text-tertiary">暂无数据，命中后显示近7天命中率</span>'}
      </div>
      <div class="space-y-2 text-xs">
        <div class="flex items-center justify-between"><span class="text-text-secondary">缓存有效期</span>
          <select class="form-select" style="width:auto;padding:4px 8px;font-size:11px" id="setCacheTTL">
            ${[[1, '1小时'], [6, '6小时'], [24, '24小时'], [168, '7天'], [0, '永久']].map(([v, n]) => `<option value="${v}" ${c.cacheTTL === v ? 'selected' : ''}>${n}</option>`).join('')}
          </select></div>
        <div class="flex items-center justify-between"><span class="text-text-secondary">缓存大小限制</span>
          <select class="form-select" style="width:auto;padding:4px 8px;font-size:11px" id="setCacheMaxMB">
            ${[[50, '50MB'], [100, '100MB'], [500, '500MB'], [0, '无限制']].map(([v, n]) => `<option value="${v}" ${c.cacheMaxMB === v ? 'selected' : ''}>${n}</option>`).join('')}
          </select></div>
        <div class="flex items-center justify-between"><span class="text-text-secondary">语义缓存（相似请求命中）</span>
          <div class="flex items-center gap-2">
            <input type="range" min="0.90" max="0.99" step="0.01" value="${c.semanticThreshold || 0.95}" id="setSemanticThreshold" class="w-24 accent-brand-cobalt">
            <span class="font-mono text-[10px] text-text-tertiary" id="semanticThresholdVal">${(c.semanticThreshold || 0.95).toFixed(2)}</span>
            <div class="toggle-track ${c.semanticCache !== false ? 'active' : ''}" id="setSemanticCache"><div class="toggle-thumb"></div></div>
          </div></div>
        <div class="flex items-center justify-between pt-2 border-t border-line-ghost/50">
          <div class="flex gap-2">
            <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="cacheExportBtn">导出</button>
            <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="cacheImportBtn">导入</button>
          </div>
          <button class="px-2.5 py-1 rounded-lg text-[10px] bg-danger/10 text-danger border border-danger/30" id="cacheClearBtn">清空缓存</button>
        </div>
      </div>
    </div>

    <!-- 智能路由 -->
    <div class="rounded-lg border border-line-ghost p-4 mb-4">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-medium text-text-primary flex items-center gap-1.5"><i data-lucide="git-branch" class="w-3.5 h-3.5 text-brand-violet"></i>智能路由（按任务复杂度选模型）</span>
        <div class="toggle-track ${c.routingEnabled !== false ? 'active' : ''}" id="setRoutingEnabled"><div class="toggle-thumb"></div></div>
      </div>
      <div class="space-y-2 text-xs">
        ${[['simple', '简单任务（翻译/格式化/短问答）'], ['medium', '中等任务（代码生成/解释/摘要）'], ['complex', '复杂任务（架构/推理/长文档）']].map(([type, label]) => `
          <div class="flex items-center gap-2">
            <span class="text-text-secondary w-56 flex-shrink-0">${label}</span>
            ${routeSelect(type, 'primary', (rc[type] || {}).primary)}
            ${routeSelect(type, 'fallback', (rc[type] || {}).fallback)}
          </div>`).join('')}
        <div class="flex items-center justify-between pt-2">
          <span class="text-text-secondary">降级重试上限</span>
          <input type="number" min="1" max="5" value="${MoraySettings.get('routingFallbacks') || 2}" id="setRoutingFallbacks" class="form-input" style="width:64px;padding:4px 8px;font-size:11px">
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="viewFallbackLogBtn">降级日志</button>
        </div>
        <div class="flex gap-4 pt-2 border-t border-line-ghost/50">
          <div class="flex-1"><div class="text-[10px] text-text-tertiary mb-1">任务类型分布</div>
            <div class="flex h-3 rounded overflow-hidden">${(() => {
              const t = rs.byType || {}; const total = (t.simple || 0) + (t.medium || 0) + (t.complex || 0);
              if (!total) return '<span class="text-[10px] text-text-tertiary">暂无数据</span>';
              return ['simple', 'medium', 'complex'].map(k => `<div style="width:${(t[k] || 0) / total * 100}%;background:${k === 'simple' ? 'var(--color-brand-cyan)' : k === 'medium' ? 'var(--color-brand-cobalt)' : 'var(--color-brand-violet)'}"></div>`).join('');
            })()}</div></div>
        </div>
      </div>
      <div class="flex items-center justify-between pt-2 border-t border-line-ghost/50">
        <span class="text-text-secondary">复用已驻留本地模型（亲和）</span>
        <div class="toggle-track ${c.routingPreferLoaded !== false ? 'active' : ''}" id="setRoutingPreferLoaded"><div class="toggle-thumb"></div></div>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-text-secondary">失败熔断冷却（秒，10-600）</span>
        <input type="number" min="10" max="600" value="${parseInt(c.circuitBreakerCooldownSec || 90, 10)}" id="setCircuitBreakerCooldownSec" class="form-input" style="width:80px;padding:4px 8px;font-size:11px">
      </div>
      <div class="flex items-center justify-between">
        <span class="text-text-secondary">模型身份元问题前端直答</span>
        <div class="toggle-track ${c.metaDirectAnswer !== false ? 'active' : ''}" id="setMetaDirectAnswer"><div class="toggle-thumb"></div></div>
      </div>
      <div class="flex items-center justify-between">
        <span class="text-text-secondary">对话内平铺完整路由日志（routingDebug）</span>
        <div class="toggle-track ${c.routingDebug ? 'active' : ''}" id="setRoutingDebug"><div class="toggle-thumb"></div></div>
      </div>
      <div class="pt-2 border-t border-line-ghost/50">
        <div class="flex items-center justify-between mb-1">
          <span class="text-text-secondary">冷却中的模型</span>
          <button class="btn-ghost px-2 py-1 rounded-lg text-[10px] border border-line-ghost" id="breakerClearBtn">手动清除冷却</button>
        </div>
        <div id="breakerStatusWrap" class="text-[10px] text-text-tertiary space-y-1"></div>
      </div>
    </div>

    <!-- 用量与预算 -->
    <div class="rounded-lg border border-line-ghost p-4">
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs font-medium text-text-primary flex items-center gap-1.5"><i data-lucide="gauge" class="w-3.5 h-3.5 text-warning"></i>用量与预算</span>
        <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="usageExportBtn">导出报表</button>
      </div>
      <div class="grid grid-cols-3 gap-2 mb-3 text-center">
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-text-primary">${((today.prompt + today.completion) / 1000).toFixed(1)}k</div><div class="text-[10px] text-text-tertiary">今日 tokens</div></div>
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-text-primary">${CostEngine.formatMoney(today.cost)}</div><div class="text-[10px] text-text-tertiary">今日费用（¥）</div></div>
        <div class="rounded-lg bg-surface-panel/60 py-2"><div class="text-sm font-mono text-success">${CostEngine.formatMoney(cacheStats.costSaved || 0)}</div><div class="text-[10px] text-text-tertiary">缓存已节省</div></div>
      </div>
      <div class="space-y-2 text-xs">
        <div class="flex items-center justify-between"><span class="text-text-secondary">本月费用 / 预算（¥，唯一预算入口在 成本与省钱）</span>
          <div class="flex items-center gap-2">
            <div class="progress-thin w-28"><div id="budgetBarFill" style="width:${(c.costBudgetCNY || 0) ? Math.min(100, month.cost / c.costBudgetCNY * 100) : 0}%"></div></div>
            <span class="font-mono text-[10px] text-text-tertiary">¥${month.cost.toFixed(2)} / ${(c.costBudgetCNY || 0) ? '¥' + c.costBudgetCNY : '未设'}</span>
            <button class="text-[10px] text-brand-cobalt hover:text-brand-cyan" id="gotoCostCardBtn">设置预算</button>
          </div></div>
        <div class="flex items-center justify-between"><span class="text-text-secondary">请求超时（秒）</span>
          <input type="number" min="10" max="120" value="${c.requestTimeout || 30}" id="setRequestTimeout" class="form-input" style="width:90px;padding:4px 8px;font-size:11px"></div>
        ${c.pauseAPI ? `<div class="flex items-center justify-between rounded-lg bg-danger/10 border border-danger/30 px-3 py-2">
          <span class="text-[11px] text-danger">云端 API 已暂停</span>
          <button class="text-[10px] text-text-secondary underline" id="resumeCloudBtn">恢复</button></div>` : ''}
      </div>
    </div>`;
  container.insertBefore(card, container.querySelector('#enhanceSettingsCard'));
  refreshIcons();
  renderGatewayHealth();

  // ---- 事件绑定 ----
  const $ = (id) => card.querySelector('#' + id);
  on($('setGatewayEnabled'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('gatewayEnabled', this.classList.contains('active')); });
  on($('setCacheEnabled'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('cacheEnabled', this.classList.contains('active')); });
  on($('setCacheTTL'), 'change', () => MoraySettings.set('cacheTTL', parseInt($('setCacheTTL').value, 10)));
  on($('setCacheMaxMB'), 'change', () => MoraySettings.set('cacheMaxMB', parseInt($('setCacheMaxMB').value, 10)));
  on($('setSemanticCache'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('semanticCache', this.classList.contains('active')); });
  on($('setSemanticThreshold'), 'input', function () { $('semanticThresholdVal').textContent = parseFloat(this.value).toFixed(2); });
  on($('setSemanticThreshold'), 'change', () => MoraySettings.set('semanticThreshold', parseFloat($('setSemanticThreshold').value)));
  on($('cacheClearBtn'), 'click', () => showConfirm('清空缓存', '将删除全部 ' + cacheStats.count + ' 条 API 缓存记录，确定？', async () => {
    await CacheStore.clear();
    showNotification('已清空', 'API 缓存已清空', 'success');
    appendGatewaySettings();
  }, { danger: true, okText: '清空' }));
  on($('cacheExportBtn'), 'click', () => GatewayCache.exportAll());
  on($('cacheImportBtn'), 'click', () => {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = '.json';
    inp.onchange = () => GatewayCache.importAll(inp.files[0]);
    inp.click();
  });
  on($('setRoutingEnabled'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('routingEnabled', this.classList.contains('active')); });
  on($('setRoutingPreferLoaded'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('routingPreferLoaded', this.classList.contains('active')); });
  on($('setCircuitBreakerCooldownSec'), 'change', () => {
    const v = Math.max(10, Math.min(600, parseInt($('setCircuitBreakerCooldownSec').value, 10) || 90));
    $('setCircuitBreakerCooldownSec').value = v;
    MoraySettings.set('circuitBreakerCooldownSec', v);
    renderBreakerStatus();
    showNotification('已保存', '熔断冷却 = ' + v + 's', 'success', 1500);
  });
  on($('setMetaDirectAnswer'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('metaDirectAnswer', this.classList.contains('active')); });
  on($('setRoutingDebug'), 'click', async function () { this.classList.toggle('active'); await MoraySettings.set('routingDebug', this.classList.contains('active')); });
  on($('breakerClearBtn'), 'click', () => { GatewayBreaker.clearAll(); renderBreakerStatus(); showNotification('已清除', '全部模型冷却已清除', 'success', 1500); });
  renderBreakerStatus();
  card.querySelectorAll('[data-route]').forEach(sel => {
    sel.addEventListener('change', async () => {
      const [type, field] = sel.dataset.route.split('.');
      const cfg = Object.assign({ simple: {}, medium: {}, complex: {} }, MoraySettings.get('routingConfig'));
      cfg[type] = cfg[type] || {};
      cfg[type][field] = sel.value;
      await MoraySettings.set('routingConfig', cfg);
      showNotification('路由已更新', `${type} ${field === 'primary' ? '首选' : '备用'}：${sel.value || '未配置'}`, 'success', 1600);
    });
  });
  on($('viewFallbackLogBtn'), 'click', () => {
    const log = Gateway.fallbackLog;
    showModal(`<div class="space-y-2">${log.length ? log.map(l => `
      <div class="chunk-item"><div class="flex justify-between mb-1"><span>${escapeHtml(shortModelName(l.from))} → ${escapeHtml(shortModelName(l.to))}</span><span class="text-text-tertiary">${relTime(l.at)}</span></div>
      <div class="text-text-tertiary">${escapeHtml(l.error)}</div></div>`).join('') : '<div class="text-xs text-text-tertiary text-center py-6">暂无降级记录</div>'}</div>`,
      { title: '路由降级日志', icon: 'git-branch', footer: false });
  });
  on($('usageExportBtn'), 'click', () => UsageTracker.exportReport());
  const gotoCostBtn = card.querySelector('#gotoCostCardBtn');
  if (gotoCostBtn) gotoCostBtn.addEventListener('click', () => {
    const costCard = document.getElementById('costSettingsCard');
    if (costCard) { costCard.scrollIntoView({ behavior: 'smooth', block: 'start' }); costCard.style.boxShadow = '0 0 0 2px rgba(91,140,255,0.35)'; setTimeout(() => { costCard.style.boxShadow = ''; }, 900); }
  });
  on($('setRequestTimeout'), 'change', () => MoraySettings.set('requestTimeout', parseInt($('setRequestTimeout').value, 10) || 30));
  // [体检] 降级重试上限输入框真实绑定（原无绑定，属死输入框）
  on($('setRoutingFallbacks'), 'change', () => {
    const v = Math.max(1, Math.min(5, parseInt($('setRoutingFallbacks').value, 10) || 2));
    $('setRoutingFallbacks').value = v;
    MoraySettings.set('routingFallbacks', v);
    showNotification('已保存', '降级重试上限 = ' + v, 'success', 1500);
  });
  const resumeBtn = $('resumeCloudBtn');
  if (resumeBtn) resumeBtn.addEventListener('click', async () => { await MoraySettings.set('pauseAPI', false); appendGatewaySettings(); showNotification('云端 API 已恢复', '', 'success', 1800); });
}


/** 判断模型是否具备视觉能力（按名称启发式）
 * @param {string} model - 模型名
 * @returns {boolean} 是否疑似视觉模型 */
function isVisionModel(model) {
  return /llava|bakllava|vision|-vl|vl-|moondream|minicpm-v|gpt-4o|gpt-4-turbo|claude-3|gemini|pixtral|qwen2.*vl/i.test(model || '');
}
