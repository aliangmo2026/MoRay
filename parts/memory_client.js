/* ============================================================
   [M3b 2026-10-02] MemoryBridge 前端薄封装（主聊天长期记忆召回 + 记忆列表）
   ------------------------------------------------------------
   - 总闸：MoraySettings.get('memoryRecallEnabled')，未设置=falsy=默认关
   - query <3 字符直接返回 []（trigram 下限，避免必败 400）
   - 超时 800ms（AbortController）；任何失败静默返回 []（fail-open，绝不阻塞发送）
   - 端点：POST {后端}/api/memory/recall（MemoryBridge M1/M3a，body {query, limit}）
   - 基址复用 agentBackendBase()（125_tools.js），兜底 http://127.0.0.1:8000
   - [M4a 2026-10-02] list(opts)：GET /api/memory/list（用户主动操作，失败显式抛错，不 fail-open）
   - [M4b 2026-10-02] forget(memoryId)：POST /api/memory/forget，只按 memory_id 删单条
     （前端不提供 query/tags 批量删入口）；save(payload)：POST /api/memory/save 手动新增
     （content 前端必填校验，空则不发请求）。两方法均照 list 的显式抛错边界，绝不 fail-open
   - [M5-P2 2026-10-06] recallWithItems(query, opts)：一次 API 调用 limit=max(注入数,10)
     （显示用全池 top-K），内部切前 3 注入、全量返回供"记忆召回"胶囊展示，返回
     {items, injectedCount, promptText}；注入文本剔除 tags 中的 basis uuid（32 位 hex，
     反思溯源原料），uuid 只留在 items 供显示。旧 recallForPrompt 签名不变，内部委托本方法
   ============================================================ */
window.MorayMemory = (function () {
  'use strict';
  const TIMEOUT_MS = 800;
  const DEFAULT_LIMIT = 3;
  const DISPLAY_LIMIT = 10; // [M5-P2] 显示用全池 top-K（胶囊展示用，不影响注入条数）

  function _base() {
    try {
      if (typeof agentBackendBase === 'function') return agentBackendBase();
    } catch (e) { /* 忽略，走兜底 */ }
    return 'http://127.0.0.1:8000';
  }

  /** 召回原始条目（fail-open：非 200/超时/断网/JSON 坏 → 一律 []，绝不阻塞发送）
   * @param {string} query - 用户原句（后端 _fts_expr 自做 CJK 3-gram 滑窗，前端不预处理）
   * @param {Object} [opts] - {limit:number}
   * @returns {Promise<Array<{id,content,score,tags,kind,source,created_at}>>} */
  async function recall(query, opts) {
    try {
      const q = String(query || '').trim();
      if (q.length < 3) return [];
      if (typeof MoraySettings === 'undefined' || !MoraySettings.get('memoryRecallEnabled')) return [];
      const limit = Math.max(1, Math.min(50, parseInt((opts || {}).limit, 10) || DEFAULT_LIMIT));
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      let resp;
      try {
        resp = await fetch(_base() + '/api/memory/recall', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q, limit: limit }),
          signal: ctrl.signal
        });
      } finally {
        clearTimeout(timer);
      }
      if (!resp.ok) return [];
      const body = await resp.json();
      if (!body || body.ok !== true || !body.data || !Array.isArray(body.data.items)) return [];
      // [M5-P2] 原"无条件 console.log 命中条数"已上移至 recallWithItems：routingDebug
      // 条件下输出含 items 明细的结构化 JSON（那里同时知道注入数，日志信息才完整）
      return body.data.items;
    } catch (e) {
      return [];
    }
  }

  /** basis uuid 判定：反思回写把原料 memory_id（32 位 hex）塞进 tags（reflection.py:12）。
   * 这些 uuid 对模型无意义且耗 token，拼注入文本前剔除，只留在 items 供胶囊显示。
   * @param {string} t - 单个 tag
   * @returns {boolean} */
  function _isBasisUuid(t) {
    return /^[0-9a-f]{32}$/i.test(String(t || ''));
  }

  /** 拼装注入 system prompt 的【长期记忆】块（取 items 前 injectLimit 条；无命中返回 ''）
   * [M5-P2] tags 中的 basis uuid（32 位 hex）不进 prompt，只保留真实业务标签。
   * @param {Array<{content:string,tags:string[]}>} items - 已按 score DESC 排序的召回条目
   * @param {number} injectLimit - 注入条数上限
   * @returns {string} */
  function buildPromptBlock(items, injectLimit) {
    const picked = (items || []).slice(0, Math.max(0, injectLimit));
    if (!picked.length) return '';
    const lines = picked.map(function (it, i) {
      let line = (i + 1) + '. ' + String(it.content || '').trim();
      const tags = (Array.isArray(it.tags) ? it.tags : []).filter(function (t) { return t && !_isBasisUuid(t); });
      if (tags.length) line += '（标签：' + tags.join('/') + '）';
      return line;
    });
    return '【长期记忆】以下是记忆库中与本次提问相关的既往偏好与事实（可能过时或不相关，仅在确有帮助时参考，不要凭空引用）：\n' + lines.join('\n');
  }

  /** [M5-P2] 召回 + 可观测：一次 API 调用（limit = max(injectLimit, DISPLAY_LIMIT)），
   * 内部切前 injectLimit 条注入、全量 items 供"记忆召回"胶囊显示——单次往返保证注入
   * 文本与显示排名同源一致，也避免两次调用叠加超出 800ms 超时预算。
   * 与 recall 同边界：关闸/<3 字符/任何失败 → items=[]、promptText=''（fail-open，绝不阻塞发送）。
   * routingDebug 开启时输出结构化召回日志（排名/是否注入/score/kind/source/tags/内容预览）。
   * @param {string} query - 用户原句
   * @param {Object} [opts] - {limit:number=注入条数上限，默认 3（与旧 recallForPrompt 语义一致）}
   * @returns {Promise<{items:Array<Object>, injectedCount:number, promptText:string}>} */
  async function recallWithItems(query, opts) {
    const injectLimit = Math.max(1, Math.min(50, parseInt((opts || {}).limit, 10) || DEFAULT_LIMIT));
    const items = await recall(query, { limit: Math.max(injectLimit, DISPLAY_LIMIT) });
    const injectedCount = Math.min(items.length, injectLimit);
    if (items.length && typeof MoraySettings !== 'undefined' && MoraySettings.get('routingDebug')) {
      console.log('[MoRay 记忆] routingDebug', JSON.stringify({
        query: String(query || '').slice(0, 60),
        fetched: items.length,
        injectedCount: injectedCount,
        items: items.map(function (it, i) {
          return {
            rank: i + 1,
            injected: (i + 1) <= injectedCount,
            score: it.score,
            kind: it.kind,
            source: it.source,
            tags: it.tags,
            content: String(it.content || '').slice(0, 80)
          };
        })
      }));
    }
    return { items: items, injectedCount: injectedCount, promptText: buildPromptBlock(items, injectLimit) };
  }

  /** 拼装注入 system prompt 的【长期记忆】块（未开闸/无命中返回 ''，调用方零分支）
   * 签名不变（M5-P2 起内部委托 recallWithItems；行为差异仅注入文本剔除 basis uuid）。
   * @param {string} query - 用户原句
   * @param {Object} [opts]
   * @returns {Promise<string>} */
  async function recallForPrompt(query, opts) {
    return (await recallWithItems(query, opts)).promptText;
  }

  /** 列出记忆（M4a 记忆浏览器数据源）。与 recall 不同：本方法是用户主动操作，
   * 失败必须显式抛错（网络异常原样上抛、HTTP 状态/后端 message 透传），绝不 fail-open 静默。
   * @param {Object} [opts] - {limit:number(1-200, 默认200), offset:number(≥0)}
   * @returns {Promise<{items:Array<Object>, total:number}>} */
  async function list(opts) {
    const o = opts || {};
    const limit = Math.max(1, Math.min(200, parseInt(o.limit, 10) || 200));
    const offset = Math.max(0, parseInt(o.offset, 10) || 0);
    const resp = await fetch(_base() + '/api/memory/list?limit=' + limit + '&offset=' + offset, { cache: 'no-store' });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try {
        const j = await resp.json();
        if (j && j.message) msg = String(j.message);
      } catch (e) { /* 非 JSON 响应：保留 HTTP 状态码信息 */ }
      throw new Error('记忆列表加载失败（' + msg + '）');
    }
    const body = await resp.json();
    if (!body || body.ok !== true || !body.data || !Array.isArray(body.data.items)) {
      throw new Error('记忆列表响应结构异常');
    }
    return {
      items: body.data.items,
      total: (typeof body.data.total === 'number') ? body.data.total : body.data.items.length
    };
  }

  /** 按 memory_id 删除单条记忆（M4b 记忆浏览器写操作）。与 list 同边界：用户主动操作，
   * 失败显式抛错，绝不 fail-open。只按 id 删——本方法不收 query/tags 参数，前端永不批量删除
   * （后端三选一能力存在，但前端入口只给 memory_id 一种）。
   * @param {string} memoryId - 记忆 id（后端主键 uuid hex）
   * @returns {Promise<{deleted:number}>} 后端实际删除行数（0 = 该 id 不存在） */
  async function forget(memoryId) {
    const id = String(memoryId || '').trim();
    if (!id) throw new Error('缺少 memory_id，已拦截未发送');
    const resp = await fetch(_base() + '/api/memory/forget', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ memory_id: id })
    });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try {
        const j = await resp.json();
        if (j && j.message) msg = String(j.message);
      } catch (e) { /* 非 JSON 响应：保留 HTTP 状态码信息 */ }
      throw new Error('记忆删除失败（' + msg + '）');
    }
    const body = await resp.json();
    if (!body || body.ok !== true || !body.data || typeof body.data.deleted !== 'number') {
      throw new Error('记忆删除响应结构异常');
    }
    return { deleted: body.data.deleted };
  }

  /** 手动保存一条记忆（M4b 记忆浏览器写操作）。与 list 同边界：用户主动操作，失败显式抛错。
   * content 前端校验非空（与后端 400 契约一致），空则不发请求直接抛错。
   * @param {Object} payload - {content:string 必填, kind?:string（留空由后端记 fact，截 32 字）,
   *   tags?:string[]（数组必须，逐元素截 32 字）, source?:string（默认 manual，截 32 字）}
   * @returns {Promise<{memory_id:string}>} */
  async function save(payload) {
    const p = payload || {};
    const content = String(p.content == null ? '' : p.content).trim();
    if (!content) throw new Error('content 必填且非空');
    const out = {
      content: content,
      source: String(p.source || 'manual').trim().slice(0, 32) || 'manual'
    };
    const kind = String(p.kind || '').trim().slice(0, 32);
    if (kind) out.kind = kind;
    if (p.tags != null) {
      if (!Array.isArray(p.tags)) throw new Error('tags 必须是数组');
      const tags = p.tags.map(function (t) { return String(t || '').trim().slice(0, 32); }).filter(Boolean);
      if (tags.length) out.tags = tags;
    }
    const resp = await fetch(_base() + '/api/memory/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(out)
    });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try {
        const j = await resp.json();
        if (j && j.message) msg = String(j.message);
      } catch (e) { /* 非 JSON 响应：保留 HTTP 状态码信息 */ }
      throw new Error('记忆保存失败（' + msg + '）');
    }
    const body = await resp.json();
    if (!body || body.ok !== true || !body.data || !body.data.memory_id) {
      throw new Error('记忆保存响应结构异常');
    }
    return { memory_id: String(body.data.memory_id) };
  }

  return { recall: recall, recallWithItems: recallWithItems, recallForPrompt: recallForPrompt, list: list, forget: forget, save: save };
})();
