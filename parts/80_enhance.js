/* ============================================================
   MoRay 阶段三增强模块
   性能：图片压缩 / Web Worker 批量向量 / 窗口化消息渲染 / 批量事务
   UX：打字指示器 / 错误卡片 / 空状态 / 会话切换淡入 / 骨架屏
   功能：@引用文档 / Transformers.js 向量化 / 对比报告导出 / 自定义快捷键
   安全：加密导出（WebCrypto AES-GCM + 回退）/ 隐私模式
   ============================================================ */

/** 增强功能命名空间 */
const Enhance = {
  /** 消息窗口化渲染的每页数量 */
  MSG_PAGE_SIZE: 100,
  /** 当前窗口起始索引（配合 renderMessagesWindowed） */
  msgWindowStart: 0
};

/* ===================== 性能：图片压缩 ===================== */

/**
 * 压缩图片文件（canvas 重采样，最长边限制 + JPEG 质量）
 * @param {File|Blob} file - 图片文件
 * @param {number} [maxDim] - 最长边像素上限（默认1920）
 * @param {number} [quality] - JPEG质量（默认0.85）
 * @returns {Promise<string>} 压缩后的 dataURL */
function compressImageFile(file, maxDim, quality) {
  maxDim = maxDim || 1920; quality = quality || 0.85;
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('图片解码失败'));
      img.onload = () => {
        try {
          let { width, height } = img;
          if (Math.max(width, height) > maxDim) {
            const scale = maxDim / Math.max(width, height);
            width = Math.round(width * scale); height = Math.round(height * scale);
          }
          const canvas = document.createElement('canvas');
          canvas.width = width; canvas.height = height;
          canvas.getContext('2d').drawImage(img, 0, 0, width, height);
          resolve(canvas.toDataURL('image/jpeg', quality));
        } catch (e) { reject(e); }
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

/* ===================== 性能：Web Worker 批量哈希向量 ===================== */

/** 哈希向量 Worker 管理器（内联 Blob Worker，保持单文件约束） */
const HashVectorWorker = {
  /** @type {Worker|null} */
  worker: null,

  /** 惰性创建 Worker（把 localHashVector 源码注入 Worker 环境）
   * @returns {Worker|null} Worker 实例，创建失败返回 null */
  ensure() {
    if (this.worker) return this.worker;
    if (typeof Worker === 'undefined') return null;
    try {
      const src = `(${localHashVector.toString()});
        onmessage = (e) => {
          const { texts, token } = e.data;
          const vectors = texts.map(t => localHashVector(t));
          postMessage({ vectors, token });
        };`;
      const blob = new Blob([src], { type: 'application/javascript' });
      this.worker = new Worker(URL.createObjectURL(blob));
      this.worker.onmessage = (e) => {
        const waiting = this._waiting.get(e.data.token);
        if (waiting) { this._waiting.delete(e.data.token); waiting.resolve(e.data.vectors); }
      };
      this.worker.onerror = () => { this.worker = null; };
      this._waiting = new Map();
      return this.worker;
    } catch (e) { console.warn('[MoRay] Worker 创建失败，回退主线程:', e); return null; }
  },

  /** 待回复的 Promise 表 */
  _waiting: new Map(),
  _token: 0,

  /**
   * 批量计算哈希向量（Worker 可用时后台执行，否则主线程）
   * @param {string[]} texts - 文本数组
   * @returns {Promise<number[][]>} 向量数组 */
  embedBatch(texts) {
    const w = this.ensure();
    if (!w) return Promise.resolve(texts.map(t => localHashVector(t)));
    const token = ++this._token;
    return new Promise((resolve) => {
      this._waiting.set(token, { resolve });
      w.postMessage({ texts, token });
      // 兜底超时：10s 后回退主线程
      setTimeout(() => {
        if (this._waiting.has(token)) {
          this._waiting.delete(token);
          resolve(texts.map(t => localHashVector(t)));
        }
      }, 10000);
    });
  }
};

/* ===================== 功能：Transformers.js 本地向量 ===================== */

/** Transformers.js 嵌入器（CDN 懒加载，全本地推理，作为 Ollama 之外的备选） */
const TransformersEmbedder = {
  /** @type {boolean} */
  loading: false,
  /** @type {boolean} */
  ready: false,
  /** @type {Function|null} */
  extractor: null,

  /** 模型标识 */
  MODEL: 'Xenova/all-MiniLM-L6-v2',

  /**
   * 懒加载 CDN 与模型（首次约下载 30MB，带进度通知）
   * @returns {Promise<boolean>} 是否就绪 */
  async load() {
    if (this.ready) return true;
    if (this.loading) return false;
    this.loading = true;
    try {
      showNotification('加载本地向量模型', '首次使用需下载约 30MB 模型（仅一次）', 'info', 5000);
      if (!window.transformers) {
        await new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = 'https://cdn.jsdelivr.net/npm/@xenova/transformers@2.17.2/dist/transformers.min.js';
          s.onload = resolve; s.onerror = () => reject(new Error('Transformers.js CDN 加载失败'));
          document.head.appendChild(s);
        });
      }
      const pipe = await window.transformers.pipeline('feature-extraction', this.MODEL, {
        progress_callback: (p) => {
          if (p.status === 'progress' && p.total) {
            const pct = Math.round((p.loaded || 0) / p.total * 100);
            if (pct % 25 === 0) console.log('[MoRay] 模型下载', pct + '%');
          }
        }
      });
      this.extractor = pipe;
      this.ready = true;
      showNotification('本地向量模型就绪', 'Transformers.js 向量化已可用', 'success', 2500);
      return true;
    } catch (e) {
      console.warn('[MoRay] Transformers 加载失败:', e);
      showNotification('向量模型加载失败', '将回退为本地哈希向量检索', 'warning', 3500);
      return false;
    } finally { this.loading = false; }
  },

  /**
   * 生成向量（mean pooling + L2 归一化）
   * @param {string} text - 文本
   * @returns {Promise<number[]>} 归一化向量 */
  async embed(text) {
    if (!this.ready) throw new Error('Transformers 未就绪');
    const out = await this.extractor(String(text || '').slice(0, 2000), { pooling: 'mean', normalize: true });
    return Array.from(out.data);
  }
};

/* ===================== 安全：加密导出 / 隐私模式 ===================== */

/** 加密工具（优先 WebCrypto AES-GCM；不可用时回退 XOR） */
const CryptoBox = {
  /**
   * 由口令派生 AES 密钥（PBKDF2-SHA256, 120k 迭代）
   * @param {string} password - 口令
   * @param {Uint8Array} salt - 盐
   * @returns {Promise<CryptoKey>} AES-GCM 密钥 */
  async deriveKey(password, salt) {
    const enc = new TextEncoder();
    const base = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations: 120000, hash: 'SHA-256' },
      base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  },

  /**
   * 加密文本（WebCrypto 可用时 AES-GCM，否则 XOR 回退）
   * @param {string} text - 明文
   * @param {string} password - 口令
   * @returns {Promise<string>} 加密载荷（自描述 JSON） */
  async encrypt(text, password) {
    if (crypto && crypto.subtle) {
      try {
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const key = await this.deriveKey(password, salt);
        const buf = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(text));
        return JSON.stringify({ fmt: 'moray-enc-aesgcm', v: 1, salt: btoa(String.fromCharCode(...salt)), iv: btoa(String.fromCharCode(...iv)), data: btoa(String.fromCharCode(...new Uint8Array(buf))) });
      } catch (e) { console.warn('[MoRay] WebCrypto 加密失败，回退 XOR:', e); }
    }
    // XOR 回退
    let out = '';
    for (let i = 0; i < text.length; i++) out += String.fromCharCode(text.charCodeAt(i) ^ password.charCodeAt(i % password.length) ^ 0x5A);
    return JSON.stringify({ fmt: 'moray-enc-xor', v: 1, data: btoa(unescape(encodeURIComponent(out))) });
  },

  /**
   * 解密文本（自动识别格式）
   * @param {string} payload - 加密载荷 JSON
   * @param {string} password - 口令
   * @returns {Promise<string>} 明文 */
  async decrypt(payload, password) {
    const j = JSON.parse(payload);
    if (j.fmt === 'moray-enc-aesgcm' && crypto && crypto.subtle) {
      const salt = Uint8Array.from(atob(j.salt), ch => ch.charCodeAt(0));
      const iv = Uint8Array.from(atob(j.iv), ch => ch.charCodeAt(0));
      const data = Uint8Array.from(atob(j.data), ch => ch.charCodeAt(0));
      const key = await this.deriveKey(password, salt);
      const buf = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, data);
      return new TextDecoder().decode(buf);
    }
    if (j.fmt === 'moray-enc-xor') {
      const raw = decodeURIComponent(escape(atob(j.data)));
      let out = '';
      for (let i = 0; i < raw.length; i++) out += String.fromCharCode(raw.charCodeAt(i) ^ password.charCodeAt(i % password.length) ^ 0x5A);
      return out;
    }
    throw new Error('未知的加密格式');
  }
};

/** 是否处于隐私模式
 * @returns {boolean} 开关状态 */
function isPrivacyMode() { return !!MoraySettings.get('privacyMode'); }

/** 隐私模式消息落库（惰性判断：开启隐私模式时不发起写库）
 * @param {Object} msg - 消息对象
 * @returns {Promise} 隐私模式下返回已解决的 Promise */
function persistMessage(msg) {
  if (isPrivacyMode()) return Promise.resolve();
  return DB.putMessage(msg).then(() => { if (typeof broadcastDataChange === 'function') broadcastDataChange(); });
}

/** 更新隐私模式指示徽章
 * @returns {void} */
function updatePrivacyBadge() {
  const meter = document.getElementById('tokenMeter');
  if (!meter) return;
  let badge = document.getElementById('privacyBadge');
  if (isPrivacyMode()) {
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'privacyBadge';
      badge.className = 'ref-chip';
      badge.style.marginLeft = '6px';
      badge.innerHTML = '<i data-lucide="eye-off" class="w-3 h-3"></i>隐私模式';
      meter.parentElement.insertBefore(badge, meter.nextSibling);
      refreshIcons();
    }
    badge.style.display = '';
  } else if (badge) badge.style.display = 'none';
}

/* ===================== UX：打字指示器 / 错误卡片 ===================== */

/** 在容器内显示打字指示器（三个跳动点）
 * @param {HTMLElement} container - 消息气泡内容容器
 * @returns {HTMLElement} 指示器元素 */
function showTypingIndicator(container) {
  const el = document.createElement('div');
  el.className = 'typing-indicator';
  el.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-label">正在生成…</span>';
  container.appendChild(el);
  return el;
}

/** 构建错误卡片（带重试按钮）
 * @param {string} title - 错误标题
 * @param {string} message - 错误详情
 * @param {Function} onRetry - 重试回调
 * @returns {HTMLElement} 卡片元素 */
function buildErrorCard(title, message, onRetry) {
  const card = document.createElement('div');
  card.className = 'error-card';
  card.innerHTML = `
    <div class="error-card-title"><i data-lucide="alert-triangle" class="w-3.5 h-3.5"></i>${escapeHtml(title)}</div>
    <div class="error-card-msg">${escapeHtml(message)}</div>`;
  const retry = document.createElement('button');
  retry.className = 'btn-ghost px-3 py-1.5 rounded-lg text-[11px] border border-line-ghost mt-2 flex items-center gap-1';
  retry.innerHTML = '<i data-lucide="rotate-ccw" class="w-3 h-3"></i>重试';
  retry.addEventListener('click', onRetry);
  card.appendChild(retry);
  refreshIcons();
  return card;
}

/* ===================== 性能：窗口化消息渲染 ===================== */

/**
 * 窗口化渲染消息列表：超过阈值时只渲染最近一页，顶部提供"加载更早"。
 * @param {Object[]} messages - 全部消息（时间升序）
 * @param {Function} buildEl - (msg, animate) => HTMLElement 构建器
 * @param {Function} scrollToBottom - 滚动到底函数
 * @returns {number} 实际渲染条数 */
function renderMessagesWindowed(messages, buildEl, scrollToBottom) {
  const box = document.getElementById('chatMessagesNormal');
  if (!box) return 0;
  box.innerHTML = '';
  Enhance.msgWindowStart = Math.max(0, messages.length - Enhance.MSG_PAGE_SIZE);

  const renderSlice = () => {
    box.innerHTML = '';
    // 顶部"加载更早"按钮
    if (Enhance.msgWindowStart > 0) {
      const more = document.createElement('div');
      more.className = 'text-center py-2';
      more.innerHTML = `<button class="text-[11px] text-text-tertiary hover:text-brand-cobalt px-3 py-1 rounded-full border border-line-ghost">
        加载更早消息（还有 ${Enhance.msgWindowStart} 条）</button>`;
      on(more.querySelector('button'), 'click', () => {
        Enhance.msgWindowStart = Math.max(0, Enhance.msgWindowStart - Enhance.MSG_PAGE_SIZE);
        const prevHeight = box.scrollHeight;
        renderSlice();
        box.parentElement.scrollTop = box.scrollHeight - prevHeight;
      });
      box.appendChild(more);
    }
    const frag = document.createDocumentFragment();
    for (let i = Enhance.msgWindowStart; i < messages.length; i++) {
      frag.appendChild(buildEl(messages[i], false));
    }
    box.appendChild(frag);
    refreshIcons();
  };
  renderSlice();
  scrollToBottom();
  return messages.length - Enhance.msgWindowStart;
}

/* ===================== 功能：@引用文档 ===================== */

/** 对话中的文档引用状态 */
const ChatRefs = {
  /** 待引用的文档ID列表 @type {string[]} */
  pending: [],

  /** 渲染输入台上方的引用 chips
   * @returns {void} */
  renderChips() {
    const container = document.getElementById('coreInputContainer');
    if (!container) return;
    let wrap = document.getElementById('refChipsWrap');
    if (!this.pending.length) { if (wrap) wrap.remove(); return; }
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'refChipsWrap';
      wrap.className = 'ref-chips px-4';
      container.insertBefore(wrap, container.querySelector('.flex.items-center.gap-3.px-4'));
    }
    wrap.innerHTML = this.pending.map(id => {
      const doc = DocsApp.cache.find(d => d.id === id);
      return `<span class="ref-chip" data-ref-id="${escapeHtml(id)}">
        <i data-lucide="file-text" class="w-3 h-3"></i>${escapeHtml(doc ? doc.name : id)}
        <span class="ref-chip-x" data-ref-remove="${escapeHtml(id)}"><i data-lucide="x" class="w-3 h-3"></i></span></span>`;
    }).join('');
    refreshIcons();
  },

  /**
   * 显示文档选择弹层（@ 触发）
   * @param {string} query - @ 后的过滤词
   * @returns {void} */
  showPopover(query) {
    const container = document.getElementById('coreInputContainer');
    const input = document.getElementById('chatInputNormal');
    if (!container || !input) return;
    let pop = document.getElementById('docRefPopover');
    if (!pop) {
      pop = document.createElement('div');
      pop.id = 'docRefPopover';
      pop.className = 'model-popover docref-popover';
      container.querySelector('.flex-1.relative').appendChild(pop);
      // 点击外部关闭
      document.addEventListener('click', (e) => {
        if (pop && !pop.contains(e.target) && e.target !== input) this.hidePopover();
      });
    }
    const q = (query || '').toLowerCase();
    const docs = DocsApp.cache.filter(d => !q || d.name.toLowerCase().includes(q)).slice(0, 8);
    if (!docs.length) {
      pop.innerHTML = '<div class="text-xs text-text-tertiary text-center py-4">暂无文档，先到文档库上传</div>';
    } else {
      pop.innerHTML = docs.map(d => `
        <div class="model-pop-item" data-docref-id="${d.id}">
          <span class="truncate">📄 ${escapeHtml(d.name)}</span>
          <span class="doc-meta">${(d.chunks || []).length} 块</span>
        </div>`).join('');
      pop.querySelectorAll('[data-docref-id]').forEach(item => {
        item.addEventListener('click', () => {
          this.add(item.dataset.docrefId);
          this.hidePopover();
          // 移除输入框中的 @query
          const caret = input.selectionStart;
          const before = input.value.slice(0, caret).replace(/@[\w\u4e00-\u9fa5-]*$/, '');
          input.value = before + input.value.slice(caret);
          input.focus();
          if (typeof updateInputStats === 'function') updateInputStats(input.value);
        });
      });
    }
    pop.classList.add('active');
  },

  /** 隐藏文档选择弹层
   * @returns {void} */
  hidePopover() {
    const pop = document.getElementById('docRefPopover');
    if (pop) pop.classList.remove('active');
  },

  /**
   * 添加待引用文档
   * @param {string} docId - 文档ID
   * @returns {void} */
  add(docId) {
    if (!this.pending.includes(docId)) this.pending.push(docId);
    this.renderChips();
  },

  /**
   * 移除待引用文档
   * @param {string} docId - 文档ID
   * @returns {void} */
  remove(docId) {
    this.pending = this.pending.filter(id => id !== docId);
    this.renderChips();
  },

  /** 清空引用
   * @returns {void} */
  clear() { this.pending = []; this.renderChips(); },

  /**
   * 汇总待引用文档的上下文（按当前输入语义检索各文档 Top-K）
   * @param {string} query - 检索词（用户输入）
   * @returns {Promise<{text:string, citations:Array}|null>} 上下文与引用 */
  async buildContext(query) {
    if (!this.pending.length) return null;
    const all = [];
    for (const docId of this.pending) {
      const doc = DocsApp.cache.find(d => d.id === docId);
      if (!doc) continue;
      const results = await DocsApp.semanticSearchRaw(query || doc.name);
      const fromDoc = results.filter(r => r.doc.id === docId).slice(0, Math.max(2, MoraySettingsGet_topK()));
      all.push(...fromDoc);
    }
    if (!all.length) return null;
    const citations = all.map(r => ({ docId: r.doc.id, docName: r.doc.name, chunkIdx: r.chunk.idx, meta: r.chunk.meta || '' }));
    const text = '请优先基于以下引用文档片段回答：\n\n' +
      all.map((r, i) => `[引用${i + 1} · ${r.doc.name}]\n${r.chunk.text}`).join('\n\n---\n\n');
    return { text, citations };
  }
};

/** 读取 RAG TopK（独立小函数避免循环依赖）
 * @returns {number} TopK */
function MoraySettingsGet_topK() { return MoraySettings.get('ragTopK') || 4; }

/** 输入框 @ 触发监听（输入事件中检测光标前的 @token）
 * @returns {void} */
function setupDocRefTrigger() {
  const input = document.getElementById('chatInputNormal');
  if (!input) return;
  input.addEventListener('input', function () {
    const caret = this.selectionStart;
    const before = this.value.slice(0, caret);
    const m = before.match(/(^|\s)@([\w\u4e00-\u9fa5-]*)$/);
    if (m) ChatRefs.showPopover(m[2]);
    else ChatRefs.hidePopover();
  });
  input.addEventListener('keydown', function (e) {
    const pop = document.getElementById('docRefPopover');
    if (pop && pop.classList.contains('active')) {
      if (e.key === 'Escape') { ChatRefs.hidePopover(); e.stopPropagation(); }
    }
  });
  // 引用 chip 移除（事件委托）
  document.addEventListener('click', (e) => {
    const x = e.target.closest('[data-ref-remove]');
    if (x) ChatRefs.remove(x.dataset.refRemove);
  });
}

/* ===================== 功能：自定义快捷键 ===================== */

/** 自定义快捷键管理 */
const CustomShortcuts = {
  /** 可绑定的动作定义 */
  ACTIONS: [
    { id: 'new-chat', name: '新建对话', def: 'Ctrl+N' },
    { id: 'palette', name: '命令面板', def: 'Ctrl+K' },
    { id: 'toggle-theme', name: '切换主题', def: 'Ctrl+Shift+T' },
    { id: 'focus-search', name: '搜索会话', def: 'Ctrl+F' },
    { id: 'clear-input', name: '清空输入框', def: 'Ctrl+L' }
  ],

  /**
   * 规范化键盘事件为快捷键字符串
   * @param {KeyboardEvent} e - 键盘事件
   * @returns {string|null} 如 "Ctrl+Shift+P"；纯修饰键返回 null */
  normalize(e) {
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) return null;
    const parts = [];
    if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
    if (e.shiftKey) parts.push('Shift');
    if (e.altKey) parts.push('Alt');
    parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
    return parts.join('+');
  },

  /**
   * 执行动作
   * @param {string} actionId - 动作ID
   * @returns {void} */
  run(actionId) {
    switch (actionId) {
      case 'new-chat': createNewConversation(); break;
      case 'palette': { const p = document.getElementById('commandPalette'); p.classList.contains('active') ? p.classList.remove('active') : (typeof openCommandPalette === 'function' && openCommandPalette()); break; }
      case 'toggle-theme': MoraySettings.set('theme', MoraySettings.get('theme') === 'light' ? 'dark' : 'light').then(() => SettingsApp.build()); break;
      case 'focus-search': { const s = document.getElementById('sessionSearchInput'); if (s) { s.focus(); s.select(); } break; }
      case 'clear-input': { const i = document.getElementById('chatInputNormal'); if (i) { i.value = ''; updateInputStats(''); } break; }
    }
  },

  /** 安装全局分发器（捕获阶段，先于内建处理器）
   * @returns {void} */
  install() {
    document.addEventListener('keydown', (e) => {
      const map = MoraySettings.get('customShortcuts') || {};
      const combo = this.normalize(e);
      if (!combo) return;
      for (const actionId in map) {
        if (map[actionId] === combo) {
          // 输入框内输入时不拦截无修饰键的绑定
          if (!e.ctrlKey && !e.metaKey && !e.altKey && document.activeElement &&
              /INPUT|TEXTAREA/.test(document.activeElement.tagName)) continue;
          e.preventDefault();
          e.stopPropagation();
          this.run(actionId);
          return;
        }
      }
    }, true);
  },

  /** 打开快捷键设置弹窗
   * @returns {void} */
  openSettings() {
    const map = MoraySettings.get('customShortcuts') || {};
    const rows = this.ACTIONS.map(a => {
      const cur = map[a.id] || a.def;
      return `<div class="flex items-center justify-between py-2 border-b border-line-ghost/40 last:border-0">
        <span class="text-xs text-text-secondary">${a.name}</span>
        <button class="btn-ghost shortcut-capture px-2 py-1 rounded-lg border border-line-ghost text-text-tertiary" data-sc-action="${a.id}" data-sc-def="${a.def}">${escapeHtml(cur)}</button>
      </div>`;
    }).join('');
    const box = showModal(`
      <div class="space-y-1">${rows}</div>
      <p class="text-[10px] text-text-tertiary mt-3">点击右侧按键后按下新的组合键即可重新绑定；Esc 取消捕获。自定义绑定与内建快捷键并存。</p>
      <div class="flex gap-2 mt-2">
        <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="scExport">导出配置</button>
        <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="scImport">导入配置</button>
      </div>`,
      { title: '快捷键自定义', icon: 'keyboard',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" id="scReset">恢复默认</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="scClose">完成</button>` });
    on(box.querySelector('#scClose'), 'click', closeModal);
    on(box.querySelector('#scExport'), 'click', () => {
      downloadJson('moray_shortcuts.json', { shortcuts: MoraySettings.get('customShortcuts') || {} });
      showNotification('已导出', '快捷键配置已下载', 'success', 1600);
    });
    on(box.querySelector('#scImport'), 'click', () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.json';
      inp.onchange = async () => {
        try {
          const data = JSON.parse(await inp.files[0].text());
          if (!data.shortcuts) throw new Error('格式不符');
          await MoraySettings.set('customShortcuts', data.shortcuts);
          closeModal();
          CustomShortcuts.openSettings();
          showNotification('导入成功', '快捷键配置已应用', 'success', 1800);
        } catch (e) { showNotification('导入失败', e.message, 'error', 2500); }
      };
      inp.click();
    });
    on(box.querySelector('#scReset'), 'click', async () => {
      await MoraySettings.set('customShortcuts', {});
      closeModal();
      this.openSettings();
      showNotification('已恢复默认', '快捷键绑定已重置', 'success', 1600);
    });
    box.querySelectorAll('[data-sc-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.classList.add('capturing');
        btn.textContent = '按下组合键…';
        const handler = (e) => {
          e.preventDefault(); e.stopPropagation();
          document.removeEventListener('keydown', handler, true);
          btn.classList.remove('capturing');
          if (e.key === 'Escape') { btn.textContent = btn.dataset.scDef; return; }
          const combo = this.normalize(e);
          btn.textContent = combo || btn.dataset.scDef;
          if (combo) {
            const next = Object.assign({}, MoraySettings.get('customShortcuts') || {});
            next[btn.dataset.scAction] = combo;
            MoraySettings.set('customShortcuts', next);
            showNotification('已绑定', combo + ' → ' + this.ACTIONS.find(a => a.id === btn.dataset.scAction).name, 'success', 1600);
          }
        };
        document.addEventListener('keydown', handler, true);
      });
    });
  }
};

/* ===================== 功能：多模型对比报告导出 ===================== */

/**
 * 导出多模型对比 Markdown 报告
 * @param {Object[]} results - CompareApp.lastResults
 * @param {string} prompt - 本轮提示词
 * @returns {void} */
function exportCompareReport(results, prompt) {
  if (!results || !results.length) { showNotification('无数据', '请先运行一轮对比', 'warning'); return; }
  let md = `# MoRay 多模型对比报告\n\n> 导出时间：${new Date().toLocaleString()}\n\n## 测试提示词\n\n${prompt || '（未记录）'}\n\n## 结果总览\n\n| 模型 | 质量 | 速度 (tok/s) | 用时 | Tokens |\n|------|------|------------|------|--------|\n`;
  results.forEach(r => {
    if (r.error) { md += `| ${r.model} | 失败 | - | - | - |\n`; return; }
    const s = r.stats || {};
    md += `| ${r.model} | ${r.quality} | ${s.tokPerSec || '-'} | ${(s.ms / 1000).toFixed(1)}s | ${s.tokens || '-'} |\n`;
  });
  md += '\n---\n\n';
  results.forEach(r => {
    md += `## ${r.model}\n\n${r.error ? '> ❌ ' + r.error : (r.content || '')}\n\n---\n\n`;
  });
  downloadText('moray_compare_report.md', md, 'text/markdown;charset=utf-8');
  showNotification('报告已导出', `${results.length} 个模型的对比报告`, 'success');
}

/* ===================== 设置页扩展区块 ===================== */

/** 在设置页追加：隐私模式 / Embedding 后端 / 自定义快捷键入口
 * @returns {void} */
function appendEnhanceSettings() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container || container.querySelector('#enhanceSettingsCard')) return;
  const c = MoraySettings.get();
  const card = document.createElement('div');
  card.id = 'enhanceSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = `
    <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
      <i data-lucide="shield-check" class="w-4 h-4 text-success"></i>隐私与增强
    </h3>
    <div class="space-y-3">
      <label class="flex items-center justify-between cursor-pointer">
        <div>
          <div class="text-xs text-text-secondary">隐私模式</div>
          <div class="text-[10px] text-text-tertiary">开启后对话不写入本地存储（仅内存保留，切换会话即消失）</div>
        </div>
        <input type="checkbox" id="setPrivacyMode" ${c.privacyMode ? 'checked' : ''} class="accent-brand-cobalt w-4 h-4">
      </label>
      <div class="flex items-center justify-between pt-2 border-t border-line-ghost/50">
        <div>
          <div class="text-xs text-text-secondary">向量化后端</div>
          <div class="text-[10px] text-text-tertiary">auto=优先 Ollama；local=浏览器内 Transformers.js；hash=纯本地词频</div>
        </div>
        <select id="setEmbedBackend" class="form-select" style="width:auto;padding:4px 10px;font-size:12px">
          <option value="auto" ${c.embeddingBackend === 'auto' ? 'selected' : ''}>auto</option>
          <option value="transformers" ${c.embeddingBackend === 'transformers' ? 'selected' : ''}>local (Transformers.js)</option>
          <option value="hash" ${c.embeddingBackend === 'hash' ? 'selected' : ''}>hash</option>
        </select>
      </div>
      <div class="flex items-center justify-between pt-2 border-t border-line-ghost/50">
        <div class="text-xs text-text-secondary">快捷键自定义</div>
        <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="openShortcutSettings">配置快捷键</button>
      </div>
      <div class="flex items-center justify-between pt-2 border-t border-line-ghost/50">
        <div>
          <div class="text-xs text-text-secondary">加密导出数据</div>
          <div class="text-[10px] text-text-tertiary">导出时使用口令加密（AES-GCM），导入时需输入相同口令</div>
        </div>
        <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="encryptedExportBtn">加密导出</button>
      </div>
    </div>`;
    container.appendChild(card);
    refreshIcons();

  on(card.querySelector('#setPrivacyMode'), 'change', async (e) => {
    await MoraySettings.set('privacyMode', e.target.checked);
    updatePrivacyBadge();
    showNotification(e.target.checked ? '隐私模式已开启' : '隐私模式已关闭',
      e.target.checked ? '对话不再写入本地存储' : '对话恢复正常持久化', 'info', 2500);
  });
  on(card.querySelector('#setEmbedBackend'), 'change', async (e) => {
    await MoraySettings.set('embeddingBackend', e.target.value);
    if (e.target.value === 'transformers') TransformersEmbedder.load();
    showNotification('已保存', '向量化后端：' + e.target.value, 'success', 1600);
  });
  on(card.querySelector('#openShortcutSettings'), 'click', () => CustomShortcuts.openSettings());
  on(card.querySelector('#encryptedExportBtn'), 'click', encryptedExportFlow);
}

/** 加密导出流程（口令 -> AES 加密全量数据）
 * @returns {Promise<void>} */
async function encryptedExportFlow() {
  const box = showModal(`
    <div class="form-row">
      <label class="form-label">加密口令（至少4位）</label>
      <input type="password" id="encPass" class="form-input" placeholder="输入导出口令">
    </div>
    <p class="text-[10px] text-danger">口令丢失将无法恢复数据，请妥善保管。</p>`,
    { title: '加密导出', icon: 'lock',
      footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-enc-cancel>取消</button>
               <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="encOk">导出</button>` });
  on(box.querySelector('[data-enc-cancel]'), 'click', closeModal);
  on(box.querySelector('#encOk'), 'click', async () => {
    const pass = box.querySelector('#encPass').value;
    if (pass.length < 4) { showNotification('口令过短', '至少 4 位字符', 'warning'); return; }
    const data = await DB.exportAll();
    const payload = await CryptoBox.encrypt(JSON.stringify(data), pass);
    downloadText('moray_backup_encrypted.json', payload, 'application/json;charset=utf-8');
    closeModal();
    showNotification('加密导出成功', '文件已下载，导入时需输入口令', 'success');
  });
}

/**
 * 加密数据导入流程（检测格式并索要口令）
 * @param {File} file - 备份文件
 * @returns {Promise<void>} */
async function encryptedImportFlow(file) {
  const raw = await file.text();
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { throw new Error('文件不是有效的 JSON'); }
  if (parsed && parsed.fmt && parsed.fmt.startsWith('moray-enc')) {
    // 加密备份：索要口令
    const box = showModal(`
      <div class="form-row">
        <label class="form-label">该备份已加密，请输入口令</label>
        <input type="password" id="decPass" class="form-input">
      </div>`,
      { title: '导入加密备份', icon: 'lock',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-dec-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="decOk">解密并导入</button>` });
    on(box.querySelector('[data-dec-cancel]'), 'click', closeModal);
    on(box.querySelector('#decOk'), 'click', async () => {
      try {
        const plain = await CryptoBox.decrypt(raw, box.querySelector('#decPass').value);
        const data = JSON.parse(plain);
        const r = await DB.importAll(data, false);
        closeModal();
        showNotification('导入完成', `共写入 ${r.imported} 条记录，刷新页面后生效`, 'success', 4000);
      } catch (e) {
        showNotification('解密失败', '口令错误或文件损坏', 'error', 3000);
      }
    });
  } else if (parsed && (parsed.conversations || parsed.prompts)) {
    const r = await DB.importAll(parsed, false);
    showNotification('导入完成', `共写入 ${r.imported} 条记录，刷新页面后生效`, 'success', 4000);
  } else {
    throw new Error('备份格式无法识别');
  }
}
