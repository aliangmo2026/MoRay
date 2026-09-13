/* ============================================================
   模块：任务二 MorayAI 后端对接层 / 设置系统 / UI 基础组件
   ============================================================ */

/* ===================== [任务九] 设置系统（v2） ===================== */

/** 应用设置对象（内存缓存，落库到 IndexedDB settings store） */
const MoraySettings = {
  /** 默认配置 */
  defaults: {
    theme: 'dark',                 // dark | light
    accent: 'cobalt',              // cobalt | cyan | violet | green | orange
    wallpaper: 'aurora',
    sendOnEnter: true,
    autoScroll: true,
    streamOutput: true,
    fontSize: 'md',                // sm | md | lg
    bubbleStyle: 'round',          // round | square
    reduceMotion: false,
    systemPrompt: '',
    contextLimit: 8192,
    temperature: 0.7,
    topP: 0.9,
    maxTokens: 2048,
    ollamaURL: 'http://localhost:11434',
    openaiBaseURL: '',
    openaiAPIKey: '',              // 存储时混淆（见 obfuscate）
    openaiModel: '',
    openaiEnabled: false,
    defaultModel: '',
    compareModels: [],
    compareSyncScroll: true,          // [零摆设] 对比双栏同步滚动（真实现，可持久化）
    embeddingModel: 'nomic-embed-text',
    chunkSize: 600,
    chunkOverlap: 100,
    ragTopK: 4,
    historyRetention: 100,         // 每会话保留消息数上限
    privacyMode: false,            // 隐私模式：对话不落盘
    embeddingBackend: 'auto',      // 向量化后端：auto | transformers | hash
    customShortcuts: {},           // 自定义快捷键 {actionId: 'Ctrl+Shift+X'}
    // ---- [V2] API 智能网关 ----
    gatewayEnabled: true,          // 网关总开关
    cacheEnabled: true,            // 请求缓存
    cacheTTL: 24,                  // 缓存有效期（小时，0=永久）
    cacheMaxMB: 100,               // 缓存容量上限（MB，0=无限制）
    semanticCache: false,           // [缓存根治] 语义缓存默认关闭（避免短问候跨会话误命中；用户可手动开启）
    semanticThreshold: 0.95,       // 语义相似度阈值
    routingEnabled: true,          // 智能路由开关
    routingConfig: { simple: {}, medium: {}, complex: {} },
    truncationStrategy: 'recent',  // 上下文截断策略：recent | starred | summary
    messageCleaning: 'whitespace', // 消息清洗：off | whitespace
    // [体验优化] 默认系统提示（简洁直答）与思考过程展示
    defaultSysPromptEnabled: true, // 启用默认系统提示（false 时请求不携带默认 system）
    defaultSysPrompt: '',           // 默认系统提示自定义文本（空 = 内置简洁直答文案）
    thinkingDisplay: 'folded',      // 思考过程展示：folded 折叠一行 | auto 流式展开 | hidden 完全隐藏
    // [深度思考] 三态开关：auto 复杂任务自动开 | on 强制思考 | off 强制不思考（默认 auto）
    thinkMode: 'auto',
    // [M2] 云端通道：true=经本地后端代理（key 存 server/.env，不进浏览器）；false=前端直连（现有方式）
    useBackendProxy: false,
    // [M3] 后端同步：true=在线时双写后端 SQLite（可关，关后纯前端与改造前一致）
    useBackendSync: true,
    // [M5.1安全] 自定义 JS 代码节点默认禁用（new Function 主线程执行有安全风险；开启需明确同意）
    codeNodesEnabled: false,
    // [统一人民币体系 v3.6] 定价与预算仅存于 CostEngine（PRICE_TABLE/priceOverrides）与 costBudgetCNY
    priceOverrides: {},            // 用户价格覆盖 {model: {input, output, cacheHit}}（¥/百万token）
    pauseAPI: false,               // 预算触顶后暂停云端 API
    requestTimeout: 30,            // 请求超时（秒）
    // ---- [工具调用] Function Calling ----
    toolsEnabled: false,           // 工具调用总开关（默认关：请求体不含 tools，链路与之前完全一致）
    toolsDisabled: [],             // 独立禁用的工具名列表
    toolsMaxRounds: 3,             // 工具循环最大轮数（1-12）
    // ---- [阶段0 本机 Agent] 本机工具（受控工作区文件操作，需本地后端） ----
    nativeToolsEnabled: false,     // 本机工具/Agent 开关（输入台显式开启；开启后才进入 listForRequest）
    agentApprovalMode: 'readonly_auto', // 审批策略：readonly_auto=只读工具自动执行 | all=全部工具需人工审批
    agentWorkspaceHint: '',        // 最近一次探测到的工作区根（仅展示缓存，权威在工作区在后端）
    // ---- [V3] 体验打磨 ----
    autoNaming: true,              // AI 自动命名会话
    namingStyle: '简洁',            // 命名风格：简洁 | 专业 | 活泼
    // ---- [FIX_ROUTING_UX v3.15.13] 路由体验新设置 ----
    routingPreferLoaded: true,      // 已驻留本地模型亲和（Ollama /api/ps，够用则复用）
    circuitBreakerCooldownSec: 90,  // 模型失败熔断冷却（秒，范围 10-600）
    metaDirectAnswer: true,         // 模型身份元问题由前端确定性直答（不依赖小模型）
    routingDebug: false             // 对话内平铺完整路由链路（默认折叠成“路由详情”胶囊）
  },
  /** 内存缓存 @type {Object} */
  cache: null,

  /** [3.20.1] 工具开关的 localStorage 镜像键。
   *  背景：工具开关状态原本只存 IndexedDB；当浏览器 IndexedDB 不可用（隐私模式/站点数据被清）时
   *  DB 会切到内存回退，设置刷新即丢失 —— 用户看到"打开过的开关刷新后又变回关"。
   *  这里给"工具开关"这类开关额外写一份 localStorage 镜像（localStorage 在该场景下通常仍可用）。
   *
   *  为什么判定是"镜像存在即以其为准"，而不是"仅当 IndexedDB 缺该键才回填"：
   *  启动流程里存在与开关无关的设置写入（如版本升级一次性清缓存标志），它会把**整份 settings**
   *  落库一次，于是 IndexedDB 里很快就有了 `toolsEnabled = false`（其实是默认值，不是用户选择）。
   *  只按"键是否存在"判断会被这个默认值挡住，镜像永远用不上。而镜像只在用户真正点击开关时写入，
   *  因此它才是"用户最后一次显式选择"的可靠来源。
   *  安全性：代码中没有任何路径会把这两个开关**程序化置 false**（只有联动开启方向），
   *  所以"镜像存在即生效"不会误复活用户没选过的状态；两端都为空时保持默认关闭。
   *  @type {Object<string,string>} 设置键 → localStorage 键 */
  MIRROR_KEYS: { toolsEnabled: 'moray_tools_enabled', nativeToolsEnabled: 'moray_native_tools_enabled' },

  /** 初始化：从 IndexedDB 加载并应用
   * @returns {Promise<Object>} 生效配置 */
  async init() {
    let stored = {};
    try { stored = (await DB.getSetting('app', {})) || {}; } catch (e) { console.warn('[MoRay] settings load failed:', e); }
    this.cache = Object.assign({}, this.defaults, stored);
    // 兼容第一阶段 localStorage 温度设置
    try {
      const legacy = window.loadSettings ? window.loadSettings() : {};
      if (legacy.temperature !== undefined && stored.temperature === undefined) this.cache.temperature = legacy.temperature;
      if (legacy.defaultModel && !stored.defaultModel) this.cache.defaultModel = legacy.defaultModel;
    } catch (e) { /* 忽略 */ }
    // [3.20.1] 工具开关：镜像存在即回填为镜像值（= 用户最后一次显式选择），并尽力回写 IndexedDB（自愈）
    let healed = false;
    Object.keys(this.MIRROR_KEYS).forEach(k => {
      let v = null;
      try { v = localStorage.getItem(this.MIRROR_KEYS[k]); } catch (e) { v = null; }
      if (v !== '1' && v !== '0') return; // 从未点过 → 保持默认（关闭）
      const want = (v === '1');
      if (this.cache[k] !== want) { this.cache[k] = want; healed = true; }
    });
    if (healed) { try { await DB.setSetting('app', this.cache); } catch (e) { /* 内存模式/写入失败不影响本次生效 */ } }
    this.apply();
    return this.cache;
  },

  /** 读取（未初始化时返回默认值）
   * @param {string} [key] - 键，缺省返回整个配置
   * @returns {*} 值 */
  get(key) {
    if (!this.cache) return key ? this.defaults[key] : Object.assign({}, this.defaults);
    return key ? this.cache[key] : this.cache;
  },

  /** 写入配置并落库 + 应用到 UI
   * @param {Object|string} patch - 变更对象或单键名
   * @param {*} [value] - 单键值
   * @returns {Promise<void>} */
  async set(patch, value) {
    const obj = typeof patch === 'string' ? { [patch]: value } : patch;
    if (!this.cache) this.cache = Object.assign({}, this.defaults);
    // API Key 混淆存储
    if ('openaiAPIKey' in obj && obj.openaiAPIKey && !obj.openaiAPIKey.startsWith('obf:')) {
      obj.openaiAPIKeyStored = 'obf:' + obfuscate(obj.openaiAPIKey);
      delete obj.openaiAPIKey;
      this.cache.openaiAPIKeyStored = obj.openaiAPIKeyStored;
    }
    Object.assign(this.cache, obj);
    // [3.20.1] 工具开关同步写 localStorage 镜像（IndexedDB 不可用时唯一能跨刷新保留的地方）
    Object.keys(this.MIRROR_KEYS).forEach(k => {
      if (!(k in obj)) return;
      try { localStorage.setItem(this.MIRROR_KEYS[k], obj[k] ? '1' : '0'); } catch (e) { /* 忽略 */ }
    });
    try { await DB.setSetting('app', this.cache); } catch (e) { console.warn('[MoRay] settings save failed:', e); }
    this.apply();
  },

  /** 获取解密后的 API Key
   * @returns {string} 明文 key */
  getAPIKey() {
    const stored = this.get('openaiAPIKeyStored');
    return stored ? deobfuscate(String(stored).replace(/^obf:/, '')) : '';
  },

  /** 将当前配置应用到 document（主题/强调色/动效/气泡/字号；theme=auto 时跟随系统）
   * @returns {void} */
  apply() {
    const c = this.cache;
    const html = document.documentElement;
    // [V2 优化4] auto 模式跟随系统深浅色偏好
    if (c.theme === 'auto' && window.matchMedia) {
      html.dataset.theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } else {
      html.dataset.theme = c.theme === 'light' ? 'light' : 'dark';
    }
    html.dataset.accent = c.accent;
    html.dataset.bubble = c.bubbleStyle;
    html.dataset.fontsize = c.fontSize;
    html.classList.toggle('reduce-motion', !!c.reduceMotion);
    // [V2 优化4] 系统主题变化时自动切换（仅 auto 模式）
    if (window.matchMedia && !this._sysThemeBound) {
      this._sysThemeBound = true;
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (this.get('theme') === 'auto') this.apply();
      });
    }
  }
};

/* ===================== [任务二] MorayAI — 后端对接层 ===================== */

/**
 * MorayAI — Ollama / OpenAI 兼容 API 客户端。
 * - 自动探测后端：优先 Ollama，其次 OpenAI 兼容
 * - 流式（SSE/NDJSON）+ 非流式回退
 * - 每次请求返回控制器句柄，支持取消
 */
class MorayAI {
  constructor() {
    /** 当前使用的后端类型 @type {'ollama'|'openai'|'none'} */
    this.backend = 'none';
    /** 已探测的模型列表 @type {Array<{name:string,size:number,details:Object}>} */
    this.models = [];
    /** 后端健康状态 @type {'checking'|'ok'|'down'} */
    this.health = 'checking';
  }

  /** Ollama 服务地址
   * @returns {string} 形如 http://localhost:11434 */
  get ollamaURL() { return (MoraySettings.get('ollamaURL') || 'http://localhost:11434').replace(/\/+$/, ''); }

  /** OpenAI 兼容 baseURL
   * @returns {string} 去尾斜杠 */
  get openaiBase() { return (MoraySettings.get('openaiBaseURL') || '').replace(/\/+$/, ''); }

  /** [P0] 默认模型为空时自动选定首个可用模型，并刷新输入台标签（仅探测成功后调用）
   * @returns {Promise<void>} */
  async autoSelectDefaultModel() {
    const cur = MoraySettings.get('defaultModel');
    if (!cur && this.models && this.models.length) {
      await MoraySettings.set('defaultModel', this.models[0].name);
      if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
    }
  }

  /** [多模型对比修复] 模型列表刷新成功后通知依赖方（对比页据此自动重建勾选区/列）
   * @returns {void} */
  _notifyModelsUpdated() {
    try {
      window.dispatchEvent(new CustomEvent('moray-models-updated', {
        detail: { backend: this.backend, count: (this.models || []).length }
      }));
    } catch (e) { /* 无监听方时安全忽略 */ }
  }

  /** [速度优化] 后台预热指定模型：空载极短请求让 Ollama 提前加载并驻留 30m；失败静默
   * @param {string} model - 模型名 */
  async warmupModel(model) {
    try {
      if (this.backend !== 'ollama' || !model) return;
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2500);
      const res = await fetch(this.ollamaURL + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: ctrl.signal,
        body: JSON.stringify({
          model: String(model),
          messages: [{ role: 'user', content: 'hi' }],
          stream: false,
          keep_alive: '30m',
          options: { num_predict: 1, temperature: 0 }
        })
      });
      clearTimeout(timer);
      if (!res.ok) return;
      await res.arrayBuffer();
    } catch (e) { /* 预热失败静默，不影响主流程 */ }
  }

  /** 探测后端可用性：先 Ollama 后 OpenAI
   * @returns {Promise<'ollama'|'openai'|'none'>} 后端类型 */
  async detectBackend() {
    this.health = 'checking';
    const d0 = performance.now();
    // 1) Ollama
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2500);
      const res = await fetch(this.ollamaURL + '/api/tags', { signal: ctrl.signal });
      clearTimeout(timer);
      if (res.ok) {
        this.__lastTagsMs = performance.now() - d0;
        this.__lastDetectMs = this.__lastTagsMs;
        const data = await res.json();
        this.models = (data.models || []).map(m => ({ name: m.name, size: m.size, details: m.details || {}, modifiedAt: m.modified_at }));
        this.backend = 'ollama';
        this.health = 'ok';
        // [P0] 默认模型为空时自动选定首个模型
        await this.autoSelectDefaultModel();
        // [多模型对比修复] 通知依赖方：模型列表已就绪
        this._notifyModelsUpdated();
        return this.backend;
      }
    } catch (e) { /* Ollama 不可达 */ }
    // 2) OpenAI 兼容
    if (MoraySettings.get('openaiEnabled') && this.openaiBase) {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 4000);
        const res = await fetch(this.openaiBase + '/models', {
          headers: this._openaiHeaders(),
          signal: ctrl.signal
        });
        clearTimeout(timer);
        if (res.ok) {
          const data = await res.json();
          this.models = (data.data || []).map(m => ({ name: m.id, size: 0, details: {} }));
          this.backend = 'openai';
          this.health = 'ok';
          // [P0] 默认模型为空时自动选定首个模型
          await this.autoSelectDefaultModel();
          // [多模型对比修复] 通知依赖方：模型列表已就绪
          this._notifyModelsUpdated();
          return this.backend;
        }
        if (res.status === 401) throw new Error('API Key 无效（401）');
      } catch (e) {
        console.warn('[MoRay] OpenAI probe failed:', e);
      }
    }
    this.backend = 'none';
    this.health = 'down';
    return this.backend;
  }

  /** OpenAI 请求头
   * @returns {Object} headers */
  _openaiHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const key = MoraySettings.getAPIKey();
    if (key) h['Authorization'] = 'Bearer ' + key;
    return h;
  }

  /** 拉取模型列表（刷新缓存）
   * @returns {Promise<Array>} 模型数组 */
  async listModels() {
    await this.detectBackend();
    return this.models;
  }

  /**
   * 流式聊天补全。
   * @param {Object} opts - 请求参数
   * @param {string} opts.model - 模型名
   * @param {Array<{role:string,content:string}>} opts.messages - 消息数组
   * @param {number} [opts.temperature] - 温度
   * @param {number} [opts.top_p] - top_p
   * @param {number} [opts.max_tokens] - 最大生成 token
   * @param {Function} [opts.onChunk] - 增量回调 ({content, reasoning, done})
   * @returns {Promise<{controller:AbortController, promise:Promise<{content:string, reasoning:string, stats:Object}>}>}
   */
  chatStream(opts) {
    const controller = new AbortController();
    const state = { content: '', reasoning: '', toolAcc: { map: {}, order: [] } }; // [阶段1 M1] 流式 tool_calls 聚合器
    const startedAt = performance.now();
    let firstChunkAt = 0;
    const self = this;

    const promise = (async () => {
      const temperature = opts.temperature != null ? opts.temperature : MoraySettings.get('temperature');
      const top_p = opts.top_p != null ? opts.top_p : MoraySettings.get('topP');
      const max_tokens = opts.max_tokens != null ? opts.max_tokens : MoraySettings.get('maxTokens');

      if (self.backend === 'ollama') {
        // ---- Ollama /api/chat（NDJSON 流）----
        const res = await fetch(self.ollamaURL + '/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            model: opts.model,
            messages: opts.messages,
            stream: true,
            keep_alive: '30m', // [速度优化] 显式常驻 30 分钟，避免逐请求冷加载
            // [深度思考] think 是 /api/chat 顶层字段（与 model/messages 平级），不是 options 采样参数；
            // 仅思考型模型且显式指定 think 才带（qwen2.x 等非思考模型绝不含 think）
            ...(isThinkingModel(opts.model) && opts.think !== undefined ? { think: !!opts.think } : {}),
            options: { temperature, top_p, num_predict: max_tokens }
          })
        });
        if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw self._normalizeError(res.status, text);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const j = JSON.parse(line);
              if (!firstChunkAt) firstChunkAt = performance.now();
              const piece = (j.message && j.message.content) || '';
              const think = (j.message && j.message.thinking) || '';
              if (piece) state.content += piece;
              if (think) state.reasoning += think;
              // [阶段1 M1] Ollama 流式 tool_calls 聚合（一次性/分片兼容，真机实测 qwen2.5:7b/qwen3.5 一次性全量）
              if (j.message && j.message.tool_calls && j.message.tool_calls.length) {
                accToolCallDeltas(state.toolAcc, j.message.tool_calls);
              }
              if (opts.onChunk) opts.onChunk({ content: piece, reasoning: think, done: !!j.done });
              if (j.done) {
                state.toolCalls = accToolCallsFinalize(state.toolAcc);
                return self._finish(state, startedAt, firstChunkAt, j.eval_count, j.eval_duration);
              }
            } catch (e) { /* 跳过不完整行 */ }
          }
        }
        return self._finish(state, startedAt, firstChunkAt, 0, 0);
      } else if (self.backend === 'openai') {
        // ---- OpenAI 兼容 /v1/chat/completions（SSE 流）----
        const model = opts.model || MoraySettings.get('openaiModel');
        // [M2] 经本地后端代理：key 存于 server/.env，浏览器请求仅到 127.0.0.1（无 key 泄漏）
        const proxyUrl = self._useProxy();
        if (proxyUrl) {
          const pres = await fetch(proxyUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({
              model,
              messages: opts.messages,
              stream: true,
              temperature, top_p, max_tokens,
              think: opts.think
            })
          });
          if (!pres.ok || !pres.body) {
            let msg = '云端代理请求失败（' + pres.status + '）';
            try { const j = await pres.json(); if (j && j.message) msg = j.message; } catch (e) { /* 保留默认 */ }
            throw new Error(msg);
          }
          const preader = pres.body.getReader();
          const pdecoder = new TextDecoder();
          let pbuf = '';
          while (true) {
            const { done, value } = await preader.read();
            if (done) break;
            pbuf += pdecoder.decode(value, { stream: true });
            const plines = pbuf.split('\n');
            pbuf = plines.pop();
            for (const pline of plines) {
              const pt = pline.trim();
              if (!pt.startsWith('data:')) continue;
              const ppayload = pt.slice(5).trim();
              if (ppayload === '[DONE]') return self._finish(state, startedAt, firstChunkAt, 0, 0);
              let pj = null;
              try { pj = JSON.parse(ppayload); } catch (e) { continue; }
              // 代理错误帧（后端转发的上游错误）→ 抛出友好消息
              if (pj && pj.ok === false) throw new Error((pj.message || '云端代理错误') + (pj.code ? '（' + pj.code + '）' : ''));
              if (!pj) continue;
              if (!firstChunkAt) firstChunkAt = performance.now();
              if (pj.usage) {
                const pdet = pj.usage.prompt_tokens_details || {};
                state.cachedTokens = pdet.cached_tokens || 0;
                if (pj.usage.prompt_tokens) state.promptTokens = pj.usage.prompt_tokens;
                if (pj.usage.completion_tokens) state.completionTokens = pj.usage.completion_tokens;
              }
              const pdelta = pj.choices && pj.choices[0] && pj.choices[0].delta || {};
              const pthink = pdelta.reasoning_content || pdelta.reasoning || '';
              const ppiece = pdelta.content || '';
              if (ppiece) state.content += ppiece;
              if (pthink) state.reasoning += pthink;
              // [阶段1 M1] 云端代理流式 delta.tool_calls 增量聚合（llm_proxy SSE 透传上游分片）
              if (pdelta.tool_calls && pdelta.tool_calls.length) {
                accToolCallDeltas(state.toolAcc, pdelta.tool_calls);
              }
              if ((ppiece || pthink) && opts.onChunk) opts.onChunk({ content: ppiece, reasoning: pthink, done: false });
            }
          }
          state.toolCalls = accToolCallsFinalize(state.toolAcc);
          return self._finish(state, startedAt, firstChunkAt, 0, 0);
        }
        const res = await fetch(self.openaiBase + '/chat/completions', {
          method: 'POST',
          headers: self._openaiHeaders(),
          signal: controller.signal,
          body: JSON.stringify({
            model,
            messages: opts.messages,
            temperature, top_p, max_tokens,
            stream: true,
            stream_options: { include_usage: true }
          })
        });
        if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw self._normalizeError(res.status, text);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop();
          for (const line of lines) {
            const t = line.trim();
            if (!t.startsWith('data:')) continue;
            const payload = t.slice(5).trim();
            if (payload === '[DONE]') return self._finish(state, startedAt, firstChunkAt, 0, 0);
            try {
              const j = JSON.parse(payload);
              if (!firstChunkAt) firstChunkAt = performance.now();
              // [任务0] 捕获 usage：cached_tokens 供 CostEngine 缓存价折扣
              if (j.usage) {
                const det = j.usage.prompt_tokens_details || {};
                state.cachedTokens = det.cached_tokens || 0;
                if (j.usage.prompt_tokens) state.promptTokens = j.usage.prompt_tokens;
                // [修复] 捕获服务端精确 completion_tokens（成本中心按精确值计费，不再依赖文本长度估算）
                if (j.usage.completion_tokens) state.completionTokens = j.usage.completion_tokens;
              }
              const delta = j.choices && j.choices[0] && j.choices[0].delta || {};
              // 兼容 thinking 字段（部分兼容网关提供 reasoning_content）
              const think = delta.reasoning_content || delta.reasoning || '';
              const piece = delta.content || '';
              if (piece) state.content += piece;
              if (think) state.reasoning += think;
              if ((piece || think) && opts.onChunk) opts.onChunk({ content: piece, reasoning: think, done: false });
            } catch (e) { /* 忽略解析失败 */ }
          }
        }
        return self._finish(state, startedAt, firstChunkAt, 0, 0);
      }
      throw new Error('没有可用的 AI 后端。请在设置中配置 Ollama 或 OpenAI 兼容 API。');
    })();

    return { controller, promise };
  }

  /** 工具参数序列化/解析统一：OpenAI 协议 arguments=JSON 字符串，Ollama 原生为对象
   * @param {*} a - 参数
   * @returns {Object} 对象 */
  toolArgsToObject(a) {
    if (a == null) return {};
    if (typeof a === 'object') return a;
    try { return JSON.parse(a); } catch (e) { return {}; }
  }

  /** 工具参数转协议字符串
   * @param {*} a - 参数
   * @returns {string} JSON 字符串 */
  toolArgsToString(a) {
    if (a == null) return '{}';
    if (typeof a === 'string') return a;
    try { return JSON.stringify(a); } catch (e) { return '{}'; }
  }

  /** [阶段0] Ollama 协议消息规范化：assistant.tool_calls[].function.arguments 由
   * OpenAI 形态（字符串）转为 Ollama 原生形态（对象）；role:"tool" 消息保持不变
   * @param {Array} msgs - 消息
   * @returns {Array} 转换后消息 */
  _ollamaMessages(msgs) {
    return (msgs || []).map(m => {
      if (!m) return m;
      // Ollama tool 消息仅需 role+content（多余字段由 struct 解析忽略，这里显式收敛）
      if (m.role === 'tool') return { role: 'tool', content: m.content || '' };
      if (m.role === 'assistant' && Array.isArray(m.tool_calls) && m.tool_calls.length) {
        return Object.assign({}, m, {
          content: m.content || '',
          tool_calls: m.tool_calls.map(c => Object.assign({}, c, {
            function: {
              name: (c.function || {}).name || '',
              arguments: this.toolArgsToObject((c.function || {}).arguments)
            }
          }))
        });
      }
      return m;
    });
  }

  /** 非流式聊天（流式失败时回退）
   * @param {Object} opts - 同 chatStream
   * @returns {Promise<{content:string, reasoning:string, stats:Object}>} 结果 */
  async chat(opts) {
    const temperature = opts.temperature != null ? opts.temperature : MoraySettings.get('temperature');
    if (this.backend === 'ollama') {
      const obody = {
        model: opts.model,
        messages: this._ollamaMessages(opts.messages),
        stream: false,
        keep_alive: '30m', // [速度优化] 非流式（工具轮/工作流/摘要）同样常驻
        // [深度思考] 同流式：think 顶层下发，options 不含 think（非思考模型行为与之前完全一致）
        ...(isThinkingModel(opts.model) && opts.think !== undefined ? { think: !!opts.think } : {}),
        options: { temperature, top_p: MoraySettings.get('topP'), num_predict: MoraySettings.get('maxTokens') }
      };
      // [阶段0] Ollama 原生 tools：请求体带 tools 才下发（无 tools 的旧请求行为完全一致）
      if (opts.tools && opts.tools.length) obody.tools = opts.tools;
      const res = await fetch(this.ollamaURL + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(obody)
      });
      if (!res.ok) throw this._normalizeError(res.status, await res.text().catch(() => ''));
      const j = await res.json();
      const mc = j.message || {};
      // [阶段1 M1] 解析经 sanitize 容错：空调用过滤、arguments 对象↔字符串统一、截断补全
      const toolCalls = sanitizeToolCalls(mc.tool_calls);
      return {
        content: mc.content || '',
        reasoning: mc.thinking || '',
        toolCalls,
        promptTokens: j.prompt_eval_count || 0,
        stats: {
          tokPerSec: j.eval_count && j.eval_duration ? +(j.eval_count / (j.eval_duration / 1e9)).toFixed(1) : null,
          ms: 0, tokens: j.eval_count || 0, promptTokens: j.prompt_eval_count || 0
        }
      };
    }
    if (this.backend === 'openai') {
      // [M2] 经本地后端代理（非流式：工具轮/摘要等场景）
      const proxyUrl = this._useProxy();
      if (proxyUrl) {
        const pbody = {
          model: opts.model || MoraySettings.get('openaiModel'),
          messages: opts.messages, stream: false,
          temperature, top_p: MoraySettings.get('topP'),
          max_tokens: MoraySettings.get('maxTokens'),
          think: opts.think
        };
        // [阶段0] 云端代理透传 tools（本地 llm_proxy 白名单同步放行；无 tools 的旧请求零变化）
        if (opts.tools && opts.tools.length) pbody.tools = opts.tools;
        const pres = await fetch(proxyUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pbody)
        });
        if (!pres.ok) {
          let msg = '云端代理请求失败（' + pres.status + '）';
          try { const j = await pres.json(); if (j && j.message) msg = j.message; } catch (e) { /* 保留默认 */ }
          throw new Error(msg);
        }
        const pj = await pres.json();
        return {
          content: pj.content || '',
          reasoning: pj.reasoning || '',
          // [阶段1 M1] 代理统一结构 tool_calls 经 sanitize 容错（空调用过滤/截断补全）
          toolCalls: sanitizeToolCalls(pj.tool_calls),
          promptTokens: (pj.usage && pj.usage.prompt_tokens) || 0,
          stats: { tokPerSec: null, ms: 0, tokens: (pj.usage && pj.usage.completion_tokens) || 0, promptTokens: (pj.usage && pj.usage.prompt_tokens) || 0 }
        };
      }
      const body = {
        model: opts.model || MoraySettings.get('openaiModel'),
        messages: opts.messages, temperature, top_p: MoraySettings.get('topP'),
        max_tokens: MoraySettings.get('maxTokens'), stream: false
      };
      // [工具调用] 请求体带 tools（未提供时行为与之前完全一致）
      if (opts.tools && opts.tools.length) body.tools = opts.tools;
      const res = await fetch(this.openaiBase + '/chat/completions', {
        method: 'POST',
        headers: this._openaiHeaders(),
        body: JSON.stringify(body)
      });
      if (!res.ok) throw this._normalizeError(res.status, await res.text().catch(() => ''));
      const j = await res.json();
      const msg = j.choices && j.choices[0] && j.choices[0].message || {};
      return {
        content: msg.content || '', reasoning: msg.reasoning_content || '',
        toolCalls: sanitizeToolCalls(msg.tool_calls), // [阶段1 M1] 容错解析
        promptTokens: (j.usage && j.usage.prompt_tokens) || 0,
        stats: { tokPerSec: null, ms: 0, tokens: (j.usage && j.usage.completion_tokens) || 0 }
      };
    }
    throw new Error('没有可用的 AI 后端');
  }

  /** Ollama embeddings（不可用时抛错，由调用方回退到本地哈希向量）
   * @param {string} model - embedding 模型名
   * @param {string|string[]} input - 文本
   * @returns {Promise<number[]>} 向量（多条输入时仅返回第一条） */
  async embed(model, input) {
    if (this.backend !== 'ollama') throw new Error('embedding 需要 Ollama 后端');
    const res = await fetch(this.ollamaURL + '/api/embeddings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, prompt: Array.isArray(input) ? input[0] : input })
    });
    if (!res.ok) throw new Error('embedding 请求失败: ' + res.status);
    const j = await res.json();
    if (!j.embedding) throw new Error('embedding 响应无效');
    return j.embedding;
  }

  /** 拉取（下载）新模型，流式进度（[V3] 支持取消与速度统计）
   * @param {string} modelName - 模型名
   * @param {Function} [onProgress] - ({status, percent, done, total, speed})
   * @param {AbortSignal} [signal] - 取消信号
   * @returns {Promise<void>} 完成后 resolve */
  async pullModel(modelName, onProgress, signal) {
    if (this.backend !== 'ollama') throw new Error('拉取模型需要 Ollama 后端');
    const res = await fetch(this.ollamaURL + '/api/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
      body: JSON.stringify({ name: modelName, stream: true })
    });
    if (!res.ok) throw new Error('拉取请求失败: ' + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const j = JSON.parse(line);
          if (j.error) throw new Error(j.error);
          // [V3 P1.3] 下载速度：按相邻两次 completed 差值计算
          let speed = 0;
          if (j.completed != null && this._lastCompleted != null && this._lastAt) {
            const dt = (Date.now() - this._lastAt) / 1000;
            if (dt > 0.2) speed = Math.max(0, (j.completed - this._lastCompleted) / dt);
          }
          if (j.completed != null) { this._lastCompleted = j.completed; this._lastAt = Date.now(); }
          if (onProgress) onProgress({ status: j.status || '', percent: j.total ? Math.round((j.completed || 0) / j.total * 100) : 0, done: j.total || 0, total: j.total || 0, speed });
          if (j.status === 'success') return;
        } catch (e) { if (e.message && !/JSON/.test(e.message)) throw e; }
      }
    }
  }

  /** 删除本地模型
   * @param {string} modelName - 模型名
   * @returns {Promise<void>} */
  async deleteModel(modelName) {
    if (this.backend !== 'ollama') throw new Error('需要 Ollama 后端');
    const res = await fetch(this.ollamaURL + '/api/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: modelName })
    });
    if (!res.ok) throw new Error('删除失败: ' + res.status);
  }

  /** 查看本地模型详情
   * @param {string} modelName - 模型名
   * @returns {Promise<Object>} show 接口返回 */
  async showModel(modelName) {
    if (this.backend !== 'ollama') throw new Error('需要 Ollama 后端');
    const res = await fetch(this.ollamaURL + '/api/show', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: modelName })
    });
    if (!res.ok) throw new Error('获取详情失败: ' + res.status);
    return res.json();
  }

  /** [M2/M5] 是否启用本地后端代理（useBackendProxy 且后端在线；离线时明确提示并自动回退直连）
   * 代理端点以 MorayBackend.origin 为事实源（任意 host/端口，与探测/同步同一来源）
   * @returns {string|null} 代理端点 URL 或 null */
  _useProxy() {
    try {
      if (MoraySettings.get('useBackendProxy') !== true) return null;
      const mb = window.MorayBackend;
      if (!mb || !mb.connected || !mb.origin) {
        // 离线：明确提示 + 自动回退直连（10 秒内不重复提示）
        const now = Date.now();
        if (!window.__proxyOfflineNoticeAt || now - window.__proxyOfflineNoticeAt > 10000) {
          window.__proxyOfflineNoticeAt = now;
          MoraySettings.set('useBackendProxy', false).catch(() => {});
          try {
            showNotification('本地后端离线', '代理模式已自动回退为前端直连；启动后端后可在设置中重新开启', 'warning', 3500);
          } catch (e) { /* 忽略 */ }
        }
        return null;
      }
      return mb.origin + '/api/llm/chat';
    } catch (e) { return null; }
  }

  /** 统一错误转换：状态码 + 原始文本 -> 友好错误
   * @param {number} status - HTTP状态码
   * @param {string} body - 响应体
   * @returns {Error} 错误对象（message 为用户可读文案） */
  _normalizeError(status, body) {
    let detail = '';
    try { const j = JSON.parse(body); detail = j.error || j.message || ''; } catch (e) { detail = (body || '').slice(0, 160); }
    if (status === 401) return new Error('API Key 无效或已过期（401）' + (detail ? '：' + detail : ''));
    if (status === 400) return new Error('请求参数错误（400）' + (detail ? '：' + detail : '。请检查模型名是否有效'));
    if (status === 404) return new Error('接口或模型不存在（404）' + (detail ? '：' + detail : '。请检查模型名拼写'));
    if (status === 500) return new Error('后端服务错误（500）' + (detail ? '：' + detail : ''));
    return new Error('请求失败（' + status + '）' + (detail ? '：' + detail : ''));
  }

  /** 汇总生成统计
   * @param {Object} state - {content, reasoning}
   * @param {number} startedAt - 开始时刻（performance.now）
   * @param {number} firstChunkAt - 首字时刻
   * @param {number} evalCount - Ollama 生成 token 数
   * @param {number} evalDuration - Ollama 生成耗时（纳秒）
   * @returns {{content:string, reasoning:string, stats:Object}} 结果 */
  _finish(state, startedAt, firstChunkAt, evalCount, evalDuration) {
    const totalMs = performance.now() - startedAt;
    state.cachedTokens = state.cachedTokens || 0; // 任务0：OpenAI usage 捕获；Ollama 默认0
    state.promptTokens = state.promptTokens || 0;
    state.completionTokens = state.completionTokens || 0;
    const est = estimateTokens(state.content);
    let tokPerSec;
    if (evalCount && evalDuration) tokPerSec = +(evalCount / (evalDuration / 1e9)).toFixed(1);
    else tokPerSec = totalMs > 200 ? +((est.tokens || state.content.length / 4) / (totalMs / 1000)).toFixed(1) : null;
    // [修复] 服务端精确 token 优先：completion 用 usage.completion_tokens → Ollama eval_count → 文本估算兜底
    const completionTokens = state.completionTokens || evalCount || est.tokens;
    return {
      content: state.content,
      reasoning: state.reasoning,
      // [阶段1 M1] 流式 tool_calls 聚合结果（无工具调用时为 null，向后兼容）
      toolCalls: state.toolCalls || null,
      stats: { tokPerSec, ms: Math.round(totalMs), firstMs: firstChunkAt ? Math.round(firstChunkAt - startedAt) : null, tokens: completionTokens, cachedTokens: state.cachedTokens, promptTokens: state.promptTokens || est.tokens }
    };
  }
}

/** [阶段1 M1] 工具调用 JSON 截断补全：补齐未闭合的字符串引号与括号（流式分片/模型截断容错）
 * @param {string} s - 可能被截断的 JSON 文本
 * @returns {string} 补全后的文本 */
function tryCompleteJson(s) {
  let t = String(s || '').trim();
  if (!t) return t;
  try { JSON.parse(t); return t; } catch (e) { /* 继续补全 */ }
  t = t.replace(/\\+$/, ''); // 去掉截断的尾部不完整转义
  let inStr = false, esc = false;
  const stack = [];
  for (const ch of t) {
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') stack.push('}');
    else if (ch === '[') stack.push(']');
    else if (ch === '}' || ch === ']') stack.pop();
  }
  if (inStr) t += '"';
  while (stack.length) t += stack.pop();
  return t;
}

/** [阶段1 M1] tool_calls 解析容错：过滤空/无名调用、arguments 对象↔字符串统一、
 * JSON 截断尝试补全。兼容 Ollama 对象形态与 OpenAI 字符串形态。
 * @param {Array} rawCalls - 原始 tool_calls
 * @returns {Array|null} 规范化 [{id,type,function:{name,arguments:string}}] 或 null */
function sanitizeToolCalls(rawCalls) {
  if (!Array.isArray(rawCalls)) return null;
  const out = [];
  for (const c of rawCalls) {
    if (!c || typeof c !== 'object') continue;
    const fn = c.function || {};
    const name = String(fn.name || '').trim();
    if (!name) continue; // 空调用过滤
    let args = fn.arguments;
    if (args == null) args = {};
    if (typeof args === 'object') {
      try { args = JSON.stringify(args); } catch (e) { args = '{}'; }
    } else if (typeof args === 'string') {
      args = tryCompleteJson(args); // 截断尝试补全；解析失败保留原文（工具层再容错）
    } else {
      args = '{}';
    }
    out.push({
      id: c.id || ('call_' + out.length + '_' + Date.now()),
      type: 'function',
      function: { name, arguments: args }
    });
  }
  return out.length ? out : null;
}

/** [阶段1 M1] 流式 delta.tool_calls 聚合（OpenAI 兼容增量协议 + Ollama 对象形态兼容）。
 * acc 由调用方持有：{map:{}, order:[]}；Ollama 无 index 时按到达顺序累加。
 * @param {Object} acc - 聚合器
 * @param {Array} deltas - delta.tool_calls 数组
 * @returns {void} */
function accToolCallDeltas(acc, deltas) {
  if (!acc || !Array.isArray(deltas)) return;
  for (const d of deltas) {
    if (!d || typeof d !== 'object') continue;
    const idx = (d.index != null) ? d.index : acc.order.length;
    let slot = acc.map[idx];
    if (!slot) { slot = acc.map[idx] = { id: '', name: '', arguments: '' }; acc.order.push(idx); }
    if (d.id) slot.id = slot.id ? slot.id : String(d.id);
    const fn = d.function || {};
    if (fn.name) {
      // Ollama 一次性给全名；OpenAI 增量给分片——两者用"未设置即赋值、已设置且不同则拼接"兼容
      slot.name = slot.name ? (slot.name === fn.name ? slot.name : slot.name + fn.name) : fn.name;
    }
    if (fn.arguments != null) {
      if (typeof fn.arguments === 'string') slot.arguments += fn.arguments;
      else { try { slot.arguments = JSON.stringify(fn.arguments); } catch (e) { /* 忽略坏分片 */ } }
    }
  }
}

/** [阶段1 M1] 聚合器 → 规范 tool_calls（经 sanitize 容错）
 * @param {Object} acc - accToolCallDeltas 的聚合器
 * @returns {Array|null} */
function accToolCallsFinalize(acc) {
  if (!acc || !acc.order.length) return null;
  const calls = acc.order.map(idx => {
    const s = acc.map[idx];
    return { id: s.id, type: 'function', function: { name: s.name, arguments: s.arguments || '{}' } };
  });
  return sanitizeToolCalls(calls);
}

/** [深度思考] 是否思考型模型（大小写不敏感，集中一处便于维护）。
 * qwen3 / deepseek-r1 / r1 / think / reason / o1 / o3 / glm-z1 等 → true；
 * qwen2 / qwen2.5 / qwen2.5-coder 等非思考系列 → 一律 false（先显式排除再匹配）。
 * @param {string} name - 模型名
 * @returns {boolean} */
function isThinkingModel(name) {
  const n = String(name || '').trim().toLowerCase();
  if (!n) return false;
  if (/^qwen2(\.\d+)?([-_.:\s]|$)/.test(n)) return false; // qwen2.x 系列非思考（含 - : . _ 空白等分隔）
  return /(qwen3|deepseek[- _]?r1|\br1\b|think|reason|\bo1\b|\bo3\b|glm[- _]?z1)/.test(n);
}

/** 全局 AI 客户端 @type {MorayAI} */
const AI = new MorayAI();

/** 请求性能历史（供监控图表），最多保留 40 条
 * @type {Array<{model:string, tokPerSec:number, firstMs:number, ok:boolean, ts:number}>} */
const PerfHistory = [];

/** 记录一次请求性能
 * @param {string} model - 模型名
 * @param {Object} stats - stats 对象
 * @param {boolean} ok - 是否成功
 * @returns {void} */
function recordPerf(model, stats, ok) {
  PerfHistory.push({ model: model || '?', tokPerSec: (stats && stats.tokPerSec) || 0, firstMs: (stats && stats.firstMs) || 0, ok, ts: Date.now() });
  if (PerfHistory.length > 40) PerfHistory.shift();
}

/* ===================== UI 基础组件：模态框 / 确认框 ===================== */

/** 打开通用模态框
 * @param {string} bodyHtml - 主体HTML（受信内容，调用方负责转义用户数据）
 * @param {Object} [opts] - {title, icon, wide, footer}
 * @returns {HTMLElement} modalBox 元素 */
function showModal(bodyHtml, opts) {
  opts = opts || {};
  const overlay = document.getElementById('modalOverlay');
  const box = document.getElementById('modalBox');
  box.className = 'modal-box' + (opts.wide ? ' ' : ' ');
  box.style.width = opts.wide ? '860px' : '';
  box.innerHTML = `
    <div class="modal-header">
      <h3 class="modal-title">${opts.icon ? `<i data-lucide="${opts.icon}" class="w-4 h-4 text-brand-cobalt"></i>` : ''}${escapeHtml(opts.title || 'MoRay')}</h3>
      <button class="tool-btn" data-modal-close><i data-lucide="x" class="w-4 h-4"></i></button>
    </div>
    <div class="modal-body">${bodyHtml}</div>
    ${opts.footer === false ? '' : `<div class="modal-footer">${opts.footer || ''}</div>`}`;
  overlay.classList.add('active');
  // [交互加固] 弹窗打开时把焦点收进弹窗（防止焦点遗留背景元素）
  box.setAttribute('tabindex', '-1');
  try { box.focus({ preventScroll: true }); } catch (e) { /* 焦点失败不阻断 */ }
  refreshIcons();
  // 关闭事件
  on(box.querySelector('[data-modal-close]'), 'click', closeModal);
  return box;
}

/** 关闭通用模态框
 * @returns {void} */
function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
}

/** 确认对话框
 * @param {string} title - 标题
 * @param {string} message - 说明
 * @param {Function} onOk - 确认回调（异步支持）
 * @param {Object} [opts] - {okText, danger}
 * @returns {void} */
function showConfirm(title, message, onOk, opts) {
  opts = opts || {};
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmMessage').textContent = message;
  const okBtn = document.getElementById('confirmOkBtn');
  okBtn.textContent = opts.okText || '确认';
  okBtn.className = 'px-4 py-2 rounded-lg text-sm relative overflow-hidden ' +
    (opts.danger ? 'bg-danger text-white hover:opacity-90' : 'btn-primary');
  okBtn.onclick = async () => {
    try { await onOk(); } catch (e) { console.error(e); showNotification('操作失败', e.message || String(e), 'error'); }
    closeConfirm();
  };
  document.getElementById('confirmOverlay').classList.add('active');
  refreshIcons();
}

/** 关闭确认框
 * @returns {void} */
function closeConfirm() {
  document.getElementById('confirmOverlay').classList.remove('active');
}
window.closeConfirm = closeConfirm;

// 模态框点击遮罩关闭 / ESC 关闭
document.addEventListener('click', (e) => {
  if (e.target.id === 'modalOverlay') closeModal();
  if (e.target.id === 'confirmOverlay') closeConfirm();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.getElementById('modalOverlay').classList.contains('active')) closeModal();
    else if (document.getElementById('confirmOverlay').classList.contains('active')) closeConfirm();
  }
});
