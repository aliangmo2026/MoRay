'use strict';
/* ============================================================
   MoRay 第二阶段应用层
   模块：任务一 IndexedDB / 通用工具 / Markdown 渲染管线
   ============================================================ */

/* ===================== 通用工具 ===================== */

/** [交互加固] 安全事件绑定：元素存在才绑定，缺失时静默跳过（返回元素以便链式使用）。
 * 单个元素缺失绝不影响同作用域内其它控件的事件绑定。
 * @param {Element|null} el - 目标元素
 * @param {string} type - 事件类型
 * @param {Function} fn - 处理器
 * @param {Object} [opt] - addEventListener 选项
 * @returns {Element|null} 原元素 */
function on(el, type, fn, opt) {
  if (el) el.addEventListener(type, fn, opt);
  return el;
}

/** 生成唯一ID
 * @param {string} prefix - ID前缀
 * @returns {string} 形如 prefix_<base36时间>_<随机串> 的唯一ID */
function uid(prefix) {
  return (prefix || 'id') + '_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
}

/** HTML转义（防XSS的第一道防线）
 * @param {string} s - 原始字符串
 * @returns {string} 转义后的安全字符串 */
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** 防抖：停止触发 wait 毫秒后执行最后一次
 * @param {Function} fn - 目标函数
 * @param {number} wait - 等待毫秒数
 * @returns {Function} 防抖包装函数 */
function debounce(fn, wait) {
  let t = null;
  return function () { const a = arguments, c = this; clearTimeout(t); t = setTimeout(() => fn.apply(c, a), wait); };
}

/** 节流：每 wait 毫秒最多执行一次
 * @param {Function} fn - 目标函数
 * @param {number} wait - 间隔毫秒数
 * @returns {Function} 节流包装函数 */
function throttle(fn, wait) {
  let last = 0, t = null;
  return function () {
    const now = Date.now(), a = arguments, c = this;
    if (now - last >= wait) { last = now; fn.apply(c, a); }
    else { clearTimeout(t); t = setTimeout(() => { last = Date.now(); fn.apply(c, a); }, wait - (now - last)); }
  };
}

/** 字数 / token 估算（中文约1.5字/token，英文约4字符/token）
 * @param {string} text - 文本
 * @returns {{chars:number, tokens:number}} 统计结果 */
function estimateTokens(text) {
  const chars = (text || '').length;
  const cn = ((text || '').match(/[\u4e00-\u9fa5]/g) || []).length;
  return { chars, tokens: Math.ceil(cn / 1.5 + (chars - cn) / 4) };
}

/** 字节数格式化
 * @param {number} bytes - 字节数
 * @returns {string} 如 "4.35 GB" */
function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(2)) + ' ' + units[i];
}

/** 相对时间格式化（刚刚 / n分钟前 / n小时前 / 昨天 HH:MM / n天前 / 日期）
 * @param {number|string|Date} ts - 时间戳
 * @returns {string} 可读时间 */
function relTime(ts) {
  if (!ts) return '';
  const d = ts instanceof Date ? ts : new Date(ts);
  if (isNaN(d.getTime())) return '';
  const diff = Date.now() - d.getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return min + '分钟前';
  const hr = Math.floor(min / 60);
  if (hr < 24) return hr + '小时前';
  const day = Math.floor(hr / 24);
  if (day === 1) return '昨天 ' + d.toTimeString().slice(0, 5);
  if (day < 7) return day + '天前';
  return (d.getMonth() + 1) + '/' + d.getDate();
}

/** 时钟时间 HH:MM
 * @param {number|string|Date} ts - 时间戳
 * @returns {string} HH:MM */
function clockTime(ts) {
  const d = ts instanceof Date ? ts : new Date(ts || Date.now());
  return d.toTimeString().slice(0, 5);
}

/** 时间分组标签：今天 / 昨天 / 更早（含具体日期）
 * @param {number} ts - 时间戳
 * @returns {string} 分组名 */
function timeGroup(ts) {
  const d = new Date(ts);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (ts >= startOfToday) return '今天';
  if (ts >= startOfToday - 86400000) return '昨天';
  if (ts >= startOfToday - 7 * 86400000) return '7天内';
  return '更早';
}

/** 复制文本到剪贴板（优先 Clipboard API，带回退）
 * @param {string} text - 文本
 * @returns {Promise<boolean>} 是否成功 */
async function copyToClipboard(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.cssText = 'position:fixed;opacity:0';
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand('copy'); ta.remove();
      return ok;
    } catch (e2) { return false; }
  }
}

/** 下载文本文件
 * @param {string} filename - 文件名
 * @param {string} text - 内容
 * @param {string} [mime] - MIME类型
 * @returns {void} */
function downloadText(filename, text, mime) {
  const blob = new Blob([text], { type: mime || 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 500);
}

/** 下载JSON文件
 * @param {string} filename - 文件名
 * @param {*} data - 可序列化数据
 * @returns {void} */
function downloadJson(filename, data) {
  downloadText(filename, JSON.stringify(data, null, 2), 'application/json;charset=utf-8');
}

/** 简单混淆编码（API Key 本地存储用；注意：单文件应用的混淆不等于加密）
 * @param {string} text - 明文
 * @returns {string} 混淆串 */
function obfuscate(text) {
  if (!text) return '';
  const key = 'MoRay_LOCAL_KEY';
  let out = '';
  for (let i = 0; i < text.length; i++) out += String.fromCharCode(text.charCodeAt(i) ^ key.charCodeAt(i % key.length) ^ 0x5A);
  return btoa(unescape(encodeURIComponent(out)));
}

/** 混淆密钥表：首位为当前密钥，其后为历史版本密钥（旧数据解密兼容） */
const CIPHER_KEYS = ['MoRay_LOCAL_KEY', 'AETHER_LOCAL_KEY'];

/** 混淆解码（依次尝试各历史密钥，含控制字符判定）
 * @param {string} data - 混淆串
 * @returns {string} 明文 */
function deobfuscate(data) {
  if (!data) return '';
  for (const key of CIPHER_KEYS) {
    try {
      const raw = decodeURIComponent(escape(atob(data)));
      let out = '';
      for (let i = 0; i < raw.length; i++) out += String.fromCharCode(raw.charCodeAt(i) ^ key.charCodeAt(i % key.length) ^ 0x5A);
      // 用错密钥会产生控制字符；无控制字符视为解码成功
      if (!/[\x00-\x08\x0e-\x1f]/.test(out)) return out;
    } catch (e) { /* 尝试下一个密钥 */ }
  }
  return '';
}

/* ===================== [任务一] MorayDB — IndexedDB 持久化层 ===================== */

/** 全部对象仓库（内存模式初始化 / 清空 / 迁移共用，禁止各写一份硬编码列表）
 * @type {string[]} */
const DB_STORES = ['conversations', 'messages', 'prompts', 'snippets', 'workflows', 'documents', 'settings', 'apicache'];

/**
 * MorayDB — 本地 IndexedDB 封装。
 * 数据库：moray_db（版本1）
 * stores: conversations / messages / prompts / snippets / workflows / documents / settings
 */
class MorayDB {
  /** @param {string} [dbName] 数据库名 @param {number} [version] 版本 */
  constructor(dbName, version) {
    this.dbName = dbName || 'moray_db';
    this.version = version || 2; // v2: 新增 apicache（API 智能网关缓存）
    /** @type {IDBDatabase|null} */
    this.db = null;
  }

  /** 打开数据库并建立 schema（已打开则直接返回；IndexedDB 不可用时自动降级内存模式）
   * @returns {Promise<IDBDatabase|null>} 数据库实例（内存模式返回 null） */
  open() {
    if (this.db) return Promise.resolve(this.db);
    if (this.memoryMode) return Promise.resolve(null);
    return new Promise((resolve, reject) => {
      let req;
      try {
        req = indexedDB.open(this.dbName, this.version);
      } catch (e) {
        // 被禁用/隐私模式：直接降级内存模式，不报错不白屏
        this._enterMemoryFallback();
        resolve(null);
        return;
      }
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        // conversations 会话
        if (!db.objectStoreNames.contains('conversations')) {
          const s = db.createObjectStore('conversations', { keyPath: 'id' });
          s.createIndex('byCreatedAt', 'createdAt');
          s.createIndex('byUpdatedAt', 'updatedAt');
        }
        // messages 消息
        if (!db.objectStoreNames.contains('messages')) {
          const s = db.createObjectStore('messages', { keyPath: 'id' });
          s.createIndex('byConversationId', 'conversationId');
          s.createIndex('byRole', 'role');
          s.createIndex('byCreatedAt', 'createdAt');
        }
        // prompts 提示词
        if (!db.objectStoreNames.contains('prompts')) {
          const s = db.createObjectStore('prompts', { keyPath: 'id' });
          s.createIndex('byCategory', 'category');
          s.createIndex('byIsFavorite', 'isFavorite');
        }
        // snippets 代码片段
        if (!db.objectStoreNames.contains('snippets')) {
          const s = db.createObjectStore('snippets', { keyPath: 'id' });
          s.createIndex('byLanguage', 'language');
          s.createIndex('byCategory', 'category');
        }
        // workflows 工作流
        if (!db.objectStoreNames.contains('workflows')) {
          const s = db.createObjectStore('workflows', { keyPath: 'id' });
          s.createIndex('byStatus', 'status');
        }
        // documents 文档
        if (!db.objectStoreNames.contains('documents')) {
          const s = db.createObjectStore('documents', { keyPath: 'id' });
          s.createIndex('byStatus', 'status');
        }
        // settings 设置（keyPath = key）
        if (!db.objectStoreNames.contains('settings')) {
          db.createObjectStore('settings', { keyPath: 'key' });
        }
        // [V2] apicache：API 智能网关缓存（精确/语义缓存 + 元数据）
        if (!db.objectStoreNames.contains('apicache')) {
          const s = db.createObjectStore('apicache', { keyPath: 'hash' });
          s.createIndex('byTime', 'createdAt');
          s.createIndex('byModel', 'model');
        }
      };
      req.onsuccess = () => { this.db = req.result; resolve(this.db); };
      req.onerror = () => {
        // [修复] 打开/建库失败（隐私模式/被禁用/配额异常）→ 自动进入内存模式
        if (!this.memoryMode) { this._enterMemoryFallback(); resolve(null); }
        else reject(req.error);
      };
    });
  }

  /** IndexedDB 不可用 → 内存模式降级（数据不持久化，控制台 + 状态栏提示）
   * @returns {void} */
  _enterMemoryFallback() {
    if (this.memoryMode) return;
    this.enableMemoryMode();
    try { showNotification('已进入内存模式', '浏览器存储不可用（隐私模式/被禁用），刷新后数据不保留', 'warning', 6000); } catch (e) { /* 通知失败不阻断 */ }
  }

  /** 内部通用请求包装（含存储配额告警）
   * @template T
   * @param {IDBRequest<T>} request - IDB请求
   * @returns {Promise<T>} 结果 */
  _wrap(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => {
        // [夜间优化] 配额不足：一次性友好提示
        if (request.error && /Quota/i.test(request.error.name || '')) this._warnQuota();
        reject(request.error);
      };
    });
  }

  /** 配额不足一次性提示
   * @returns {void} */
  _warnQuota() {
    if (this._quotaWarned) return;
    this._quotaWarned = true;
    try {
      showNotification('存储空间不足', '浏览器存储配额已满，建议导出数据后清理旧会话/文档', 'error', 8000);
    } catch (e) { console.warn('[MoRay] quota warn failed:', e); }
  }

  /** 事务内执行（内存模式时路由到内存模拟层）
   * @param {string|string[]} stores - store名
   * @param {string} mode - 'readonly'|'readwrite'
   * @param {Function} fn - (tx) => IDBRequest
   * @returns {Promise<*>} 请求结果 */
  async _tx(stores, mode, fn) {
    if (this.memoryMode) return this._memTx(stores, fn);
    await this.open();
    // [修复] open 期间可能已降级内存模式（双保险）
    if (this.memoryMode) return this._memTx(stores, fn);
    const tx = this.db.transaction(stores, mode);
    const req = fn(tx);
    return this._wrap(req);
  }

  /** [降级] 启用内存模式（IndexedDB 不可用时的兜底，数据不持久化）
   * @returns {void} */
  enableMemoryMode() {
    if (this.memoryMode) return;
    this.memoryMode = true;
    this._mem = new Map();
    DB_STORES.forEach(s => this._mem.set(s, new Map()));
    this._memSettings = new Map();
    console.warn('[MoRay] DB 已进入内存模式（刷新后数据不保留）');
  }

  /** 内存模式：取 store 的 Map（惰性创建，杜绝 get(name) 为 undefined 后 .get/.set 抛错）
   * @param {string} name - store 名
   * @returns {Map} store 数据 */
  _memStore(name) {
    let m = this._mem.get(name);
    if (!m) { m = new Map(); this._mem.set(name, m); }
    return m;
  }

  /** 索引名 -> 字段名映射（内存模式过滤用）
   * @param {string} indexName - 索引名
   * @returns {string|null} 字段名 */
  _indexField(indexName) {
    const map = { byConversationId: 'conversationId', byCreatedAt: 'createdAt', byRole: 'role', byCategory: 'category', byIsFavorite: 'isFavorite', byLanguage: 'language', byStatus: 'status', byUpdatedAt: 'updatedAt' };
    return map[indexName] || null;
  }

  /** 内存模式事务模拟（拦截 getAll/get/put/delete/clear/index 常用操作）
   * @param {string|string[]} stores - store名
   * @param {Function} fn - (tx) => 任意
   * @returns {Promise<*>} 结果 */
  async _memTx(stores, fn) {
    const storeOf = (name) => this._memStore(name); // 惰性创建，防 undefined 崩溃
    const list = (name) => Array.from(storeOf(name).values());
    const tx = {
      objectStore: (name) => ({
        getAll: () => Promise.resolve(list(name)),
        get: (k) => Promise.resolve(storeOf(name).get(k)),
        put: (row) => { storeOf(name).set(row.id || row.key, row); return Promise.resolve(row); },
        delete: (k) => { storeOf(name).delete(k); return Promise.resolve(); },
        clear: () => { this._mem.set(name, new Map()); return Promise.resolve(); },
        count: () => Promise.resolve(list(name).length),
        index: (indexName) => {
          const field = this._indexField(indexName);
          return {
            getAll: (v) => Promise.resolve(list(name).filter(r => v == null || r[field] === v)),
            openCursor: () => Promise.resolve(null) // 内存模式游标不适用（调用方已有显式分支）
          };
        }
      })
    };
    return Promise.resolve(fn(tx));
  }

  /* ---------- conversations ---------- */
  /** 列出全部会话（按置顶+更新时间排序由调用方处理）
   * @returns {Promise<Array>} 会话数组 */
  async listConversations() { return this._tx('conversations', 'readonly', tx => tx.objectStore('conversations').getAll()); }
  /** 获取会话
   * @param {string} id - 会话ID
   * @returns {Promise<Object|undefined>} 会话 */
  async getConversation(id) { return this._tx('conversations', 'readonly', tx => tx.objectStore('conversations').get(id)); }
  /** 写入/更新会话
   * @param {Object} conv - 会话对象
   * @returns {Promise<void>} */
  async putConversation(conv) { return this._tx('conversations', 'readwrite', tx => tx.objectStore('conversations').put(conv)); }
  /** 创建会话
   * @param {Object} data - 初始字段
   * @returns {Promise<Object>} 已入库的会话 */
  async createConversation(data) {
    const conv = Object.assign({ id: uid('conv'), title: '新对话', createdAt: Date.now(), updatedAt: Date.now(), pinned: false, archived: false, model: '', systemPrompt: '', order: Date.now() }, data);
    await this.putConversation(conv);
    return conv;
  }
  /** 更新会话（部分字段）
   * @param {string} id - 会话ID
   * @param {Object} patch - 变更字段
   * @returns {Promise<Object|undefined>} 更新后的会话 */
  async updateConversation(id, patch) {
    const conv = await this.getConversation(id);
    if (!conv) return undefined;
    Object.assign(conv, patch, { updatedAt: Date.now() });
    await this.putConversation(conv);
    return conv;
  }
  /** 删除会话（连同其消息）
   * @param {string} id - 会话ID
   * @returns {Promise<void>} */
  async deleteConversation(id) {
    if (this.memoryMode) {
      this._mem.get('conversations').delete(id);
      for (const [mid, m] of Array.from(this._mem.get('messages'))) {
        if (m.conversationId === id) this._mem.get('messages').delete(mid);
      }
      return;
    }
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(['conversations', 'messages'], 'readwrite');
      tx.objectStore('conversations').delete(id);
      const idx = tx.objectStore('messages').index('byConversationId');
      idx.openCursor(IDBKeyRange.only(id)).onsuccess = (e) => {
        const cursor = e.target.result;
        if (cursor) { cursor.delete(); cursor.continue(); }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }
  /** 清空会话
   * @returns {Promise<void>} */
  async clearConversations() { return this._tx('conversations', 'readwrite', tx => tx.objectStore('conversations').clear()); }

  /* ---------- messages ---------- */
  /** 按会话列出消息（按创建时间升序）
   * @param {string} conversationId - 会话ID
   * @returns {Promise<Array>} 消息数组 */
  async listMessagesByConversation(conversationId) {
    const all = await this._tx('messages', 'readonly', tx => tx.objectStore('messages').index('byConversationId').getAll(conversationId));
    return all.sort((a, b) => a.createdAt - b.createdAt);
  }
  /** 获取消息
   * @param {string} id - 消息ID
   * @returns {Promise<Object|undefined>} 消息 */
  async getMessage(id) { return this._tx('messages', 'readonly', tx => tx.objectStore('messages').get(id)); }
  /** 写入消息
   * @param {Object} msg - 消息对象
   * @returns {Promise<void>} */
  async putMessage(msg) { return this._tx('messages', 'readwrite', tx => tx.objectStore('messages').put(msg)); }
  /** 创建消息
   * @param {Object} data - 字段
   * @returns {Promise<Object>} 已入库消息 */
  async createMessage(data) {
    const msg = Object.assign({ id: uid('msg'), conversationId: '', role: 'user', content: '', reasoning: '', model: '', createdAt: Date.now(), tokens: 0, stats: null, feedback: 0, attachments: [] }, data);
    await this.putMessage(msg);
    return msg;
  }
  /** 更新消息
   * @param {string} id - 消息ID
   * @param {Object} patch - 变更字段
   * @returns {Promise<Object|undefined>} 更新后消息 */
  async updateMessage(id, patch) {
    const msg = await this.getMessage(id);
    if (!msg) return undefined;
    Object.assign(msg, patch);
    await this.putMessage(msg);
    return msg;
  }
  /** 批量写入消息（单事务，减少开销——分支会话/导入时使用）
   * @param {Object[]} rows - 消息数组
   * @returns {Promise<void>} */
  async putMessages(rows) {
    if (!rows || !rows.length) return;
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('messages', 'readwrite');
      const store = tx.objectStore('messages');
      rows.forEach(r => store.put(r));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }
  /** 删除消息
   * @param {string} id - 消息ID
   * @returns {Promise<void>} */
  async deleteMessage(id) { return this._tx('messages', 'readwrite', tx => tx.objectStore('messages').delete(id)); }
  /** 清空某会话的消息
   * @param {string} conversationId - 会话ID
   * @returns {Promise<void>} */
  async clearMessagesByConversation(conversationId) {
    if (this.memoryMode) {
      for (const [mid, m] of Array.from(this._mem.get('messages'))) {
        if (m.conversationId === conversationId) this._mem.get('messages').delete(mid);
      }
      return;
    }
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('messages', 'readwrite');
      const idx = tx.objectStore('messages').index('byConversationId');
      idx.openCursor(IDBKeyRange.only(conversationId)).onsuccess = (e) => {
        const c = e.target.result; if (c) { c.delete(); c.continue(); }
      };
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  /* ---------- prompts ---------- */
  /** 列出提示词
   * @returns {Promise<Array>} */
  async listPrompts() { return this._tx('prompts', 'readonly', tx => tx.objectStore('prompts').getAll()); }
  /** 获取提示词
   * @param {string} id - ID
   * @returns {Promise<Object|undefined>} */
  async getPrompt(id) { return this._tx('prompts', 'readonly', tx => tx.objectStore('prompts').get(id)); }
  /** 创建提示词
   * @param {Object} data - 字段
   * @returns {Promise<Object>} 已入库对象 */
  async createPrompt(data) {
    const p = Object.assign({ id: uid('prompt'), title: '', content: '', category: '其他', tags: [], description: '', isFavorite: false, useCount: 0, createdAt: Date.now(), updatedAt: Date.now() }, data);
    await this._tx('prompts', 'readwrite', tx => tx.objectStore('prompts').put(p));
    return p;
  }
  /** 更新提示词
   * @param {string} id - ID
   * @param {Object} patch - 变更
   * @returns {Promise<Object|undefined>} */
  async updatePrompt(id, patch) {
    const p = await this.getPrompt(id);
    if (!p) return undefined;
    Object.assign(p, patch, { updatedAt: Date.now() });
    await this._tx('prompts', 'readwrite', tx => tx.objectStore('prompts').put(p));
    return p;
  }
  /** 删除提示词
   * @param {string} id - ID
   * @returns {Promise<void>} */
  async deletePrompt(id) { return this._tx('prompts', 'readwrite', tx => tx.objectStore('prompts').delete(id)); }
  /** 切换收藏
   * @param {string} id - ID
   * @returns {Promise<boolean>} 新的收藏状态 */
  async togglePromptFavorite(id) {
    const p = await this.getPrompt(id);
    if (!p) return false;
    p.isFavorite = !p.isFavorite;
    await this._tx('prompts', 'readwrite', tx => tx.objectStore('prompts').put(p));
    return p.isFavorite;
  }

  /* ---------- snippets ---------- */
  /** 列出片段
   * @returns {Promise<Array>} */
  async listSnippets() { return this._tx('snippets', 'readonly', tx => tx.objectStore('snippets').getAll()); }
  /** 获取片段
   * @param {string} id - ID
   * @returns {Promise<Object|undefined>} */
  async getSnippet(id) { return this._tx('snippets', 'readonly', tx => tx.objectStore('snippets').get(id)); }
  /** 创建片段
   * @param {Object} data - 字段
   * @returns {Promise<Object>} */
  async createSnippet(data) {
    const s = Object.assign({ id: uid('snip'), title: '', code: '', language: 'text', description: '', tags: [], folder: '', isFavorite: false, createdAt: Date.now(), updatedAt: Date.now() }, data);
    await this._tx('snippets', 'readwrite', tx => tx.objectStore('snippets').put(s));
    return s;
  }
  /** 更新片段
   * @param {string} id - ID
   * @param {Object} patch - 变更
   * @returns {Promise<Object|undefined>} */
  async updateSnippet(id, patch) {
    const s = await this.getSnippet(id);
    if (!s) return undefined;
    Object.assign(s, patch, { updatedAt: Date.now() });
    await this._tx('snippets', 'readwrite', tx => tx.objectStore('snippets').put(s));
    return s;
  }
  /** 删除片段
   * @param {string} id - ID
   * @returns {Promise<void>} */
  async deleteSnippet(id) { return this._tx('snippets', 'readwrite', tx => tx.objectStore('snippets').delete(id)); }

  /* ---------- workflows ---------- */
  /** 列出工作流
   * @returns {Promise<Array>} */
  async listWorkflows() { return this._tx('workflows', 'readonly', tx => tx.objectStore('workflows').getAll()); }
  /** 获取工作流
   * @param {string} id - ID
   * @returns {Promise<Object|undefined>} */
  async getWorkflow(id) { return this._tx('workflows', 'readonly', tx => tx.objectStore('workflows').get(id)); }
  /** 写入工作流
   * @param {Object} wf - 工作流对象
   * @returns {Promise<void>} */
  async putWorkflow(wf) { return this._tx('workflows', 'readwrite', tx => tx.objectStore('workflows').put(wf)); }
  /** 创建工作流
   * @param {Object} data - 字段
   * @returns {Promise<Object>} */
  async createWorkflow(data) {
    const wf = Object.assign({ id: uid('wf'), name: '', description: '', icon: 'workflow', accent: '#5B8CFF', nodes: [], isTemplate: false, runs: [], runCount: 0, lastRunAt: 0, status: 'idle', createdAt: Date.now(), updatedAt: Date.now() }, data);
    await this.putWorkflow(wf);
    return wf;
  }
  /** 更新工作流
   * @param {string} id - ID
   * @param {Object} patch - 变更
   * @returns {Promise<Object|undefined>} */
  async updateWorkflow(id, patch) {
    const wf = await this.getWorkflow(id);
    if (!wf) return undefined;
    Object.assign(wf, patch, { updatedAt: Date.now() });
    await this.putWorkflow(wf);
    return wf;
  }
  /** 删除工作流
   * @param {string} id - ID
   * @returns {Promise<void>} */
  async deleteWorkflow(id) { return this._tx('workflows', 'readwrite', tx => tx.objectStore('workflows').delete(id)); }

  /* ---------- documents ---------- */
  /** 列出文档
   * @returns {Promise<Array>} */
  async listDocuments() { return this._tx('documents', 'readonly', tx => tx.objectStore('documents').getAll()); }
  /** 获取文档
   * @param {string} id - ID
   * @returns {Promise<Object|undefined>} */
  async getDocument(id) { return this._tx('documents', 'readonly', tx => tx.objectStore('documents').get(id)); }
  /** 写入文档
   * @param {Object} doc - 文档对象
   * @returns {Promise<void>} */
  async putDocument(doc) { return this._tx('documents', 'readwrite', tx => tx.objectStore('documents').put(doc)); }
  /** 更新文档
   * @param {string} id - ID
   * @param {Object} patch - 变更
   * @returns {Promise<Object|undefined>} */
  async updateDocument(id, patch) {
    const d = await this.getDocument(id);
    if (!d) return undefined;
    Object.assign(d, patch);
    await this.putDocument(d);
    return d;
  }
  /** 删除文档
   * @param {string} id - ID
   * @returns {Promise<void>} */
  async deleteDocument(id) { return this._tx('documents', 'readwrite', tx => tx.objectStore('documents').delete(id)); }

  /* ---------- settings ---------- */
  /** 读取设置项
   * @param {string} key - 键
   * @param {*} [fallback] - 缺省值
   * @returns {Promise<*>} 值 */
  async getSetting(key, fallback) {
    const row = await this._tx('settings', 'readonly', tx => tx.objectStore('settings').get(key));
    return row ? row.value : fallback;
  }
  /** 写入设置项
   * @param {string} key - 键
   * @param {*} value - 值
   * @returns {Promise<void>} */
  async setSetting(key, value) { return this._tx('settings', 'readwrite', tx => tx.objectStore('settings').put({ key, value })); }
  /** 删除设置项
   * @param {string} key - 键
   * @returns {Promise<void>} */
  async removeSetting(key) { return this._tx('settings', 'readwrite', tx => tx.objectStore('settings').delete(key)); }

  /* ---------- 全量导出/导入 ---------- */
  /** 导出全部数据
   * @returns {Promise<Object>} 各store数据包 */
  async exportAll() {
    const [conversations, messages, prompts, snippets, workflows, documents] = await Promise.all([
      this.listConversations(), this._tx('messages', 'readonly', tx => tx.objectStore('messages').getAll()),
      this.listPrompts(), this.listSnippets(), this.listWorkflows(), this.listDocuments()
    ]);
    return { version: 2, exportedAt: new Date().toISOString(), conversations, messages, prompts, snippets, workflows, documents };
  }
  /** 从数据包导入（合并写入）
   * @param {Object} data - exportAll 的数据包
   * @param {boolean} [replace] - true 时先清空
   * @returns {Promise<{imported:number}>} 导入条数统计 */
  async importAll(data, replace) {
    let count = 0;
    // 导入范围仅业务数据（设置与 API 缓存不属于用户数据包）
    const stores = DB_STORES.filter(s => s !== 'settings' && s !== 'apicache');
    if (replace) {
      await this._tx(stores, 'readwrite', tx => { stores.forEach(s => tx.objectStore(s).clear()); });
    }
    for (const store of stores) {
      const rows = data[store];
      if (!Array.isArray(rows)) continue;
      for (const row of rows) { await this._tx(store, 'readwrite', tx => tx.objectStore(store).put(row)); count++; }
    }
    return { imported: count };
  }
  /** 按类型清空数据
   * @param {string[]} stores - 要清空的store名数组
   * @returns {Promise<void>} */
  async clearStores(stores) {
    if (this.memoryMode) {
      stores.forEach(s => this._memStore(s).clear());
      return;
    }
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(stores, 'readwrite');
      stores.forEach(s => tx.objectStore(s).clear());
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }
}

/** 全局数据库实例 @type {MorayDB} */
const DB = new MorayDB();

/* ===================== Markdown 渲染管线 ===================== */

/** 将 Markdown 文本渲染为安全的 HTML（marked + DOMPurify + hljs）
 * @param {string} md - Markdown 源文本
 * @returns {string} 消毒后的 HTML */
function renderMarkdown(md) {
  if (!md) return '';
  let html = '';
  try {
    if (window.marked) {
      const renderer = new window.marked.Renderer();
      // 链接默认新窗口打开
      renderer.link = (href, title, text) => {
        const safeHref = typeof href === 'string' ? href : (href && href.href) || '#';
        return `<a href="${escapeHtml(safeHref)}" title="${escapeHtml(title || '')}" target="_blank" rel="noopener noreferrer">${text}</a>`;
      };
      window.marked.setOptions({ breaks: true, gfm: true, renderer });
      html = window.marked.parse(md);
    } else {
      // CDN 加载失败时的纯文本回退
      html = '<p>' + escapeHtml(md).replace(/\n/g, '<br>') + '</p>';
    }
  } catch (e) {
    console.warn('[MoRay] markdown parse error:', e);
    html = '<p>' + escapeHtml(md) + '</p>';
  }
  if (window.DOMPurify) {
    html = window.DOMPurify.sanitize(html, {
      ADD_ATTR: ['target'],
      FORBID_TAGS: ['style', 'form', 'iframe', 'object', 'embed'],
      FORBID_ATTR: ['onerror', 'onclick', 'onload']
    });
  }
  return html;
}

/** 增强消息容器内的代码块：语言标签 / 复制按钮 / 行号 / 全屏，并做语法高亮
 * @param {HTMLElement} container - 消息DOM容器
 * @param {boolean} [withLineNumbers] - 是否添加行号
 * @returns {void} */
function enhanceCodeBlocks(container, withLineNumbers) {
  if (!container) return;
  container.querySelectorAll('pre > code').forEach(codeEl => {
    if (codeEl.dataset.enhanced) return;
    codeEl.dataset.enhanced = '1';
    const pre = codeEl.parentElement;
    // 识别语言：marked 输出 code.language-xxx
    const langMatch = (codeEl.className || '').match(/language-([\w+-]+)/);
    const lang = langMatch ? langMatch[1] : '';
    // [夜间优化2.1] 语法高亮懒执行：滚动进入可视区域才高亮（IntersectionObserver）
    if (typeof LazyHighlighter !== 'undefined') LazyHighlighter.observe(codeEl, lang);
    else {
      try { if (window.hljs) hljs.highlightElement(codeEl); } catch (e) { /* 忽略 */ }
    }
    // 组装代码块外壳
    const wrapper = document.createElement('div');
    wrapper.className = 'code-block overflow-hidden my-2';
    wrapper.innerHTML = `
      <div class="code-header">
        <div class="code-dots"><span></span><span></span><span></span></div>
        <span class="code-lang">${escapeHtml(lang || 'text')}</span>
        <span style="flex:1"></span>
        <button class="text-[10px] text-text-tertiary hover:text-brand-cobalt flex items-center gap-1 transition-colors code-line-toggle" title="切换行号" style="display:none">
          <i data-lucide="list" class="w-3 h-3"></i>行号
        </button>
        <button class="text-[10px] text-text-tertiary hover:text-brand-cobalt flex items-center gap-1 transition-colors code-fullscreen-btn" title="全屏查看">
          <i data-lucide="maximize-2" class="w-3 h-3"></i>
        </button>
        <button class="text-[10px] text-text-tertiary hover:text-brand-cobalt flex items-center gap-1 transition-colors code-copy-btn">
          <i data-lucide="copy" class="w-3 h-3"></i>复制
        </button>
      </div>
      <div class="code-with-lines"></div>`;
    try { pre.replaceWith(wrapper); } catch (e) { return; }
    wrapper.querySelector('.code-with-lines').appendChild(pre);
    pre.className = 'text-text-secondary text-[12px] m-0';
    pre.style.cssText = 'padding:12px 14px;margin:0;overflow-x:auto;max-height:420px;overflow-y:auto';

    const rawText = codeEl.textContent;
    // 行数多时显示行号开关
    const lineCount = rawText.split('\n').length;
    // [夜间优化2.1] 长代码块（>30行）默认折叠
    if (lineCount > 30) {
      pre.style.maxHeight = '200px';
      pre.classList.add('code-collapsed');
      const expandBar = document.createElement('div');
      expandBar.className = 'code-expand-bar';
      expandBar.innerHTML = `<button type="button">展开全部 ${lineCount} 行 <i data-lucide="chevron-down" class="w-3 h-3 inline"></i></button>`;
      wrapper.appendChild(expandBar);
      on(expandBar.querySelector('button'), 'click', () => {
        const collapsed = pre.classList.toggle('code-collapsed');
        pre.style.maxHeight = collapsed ? '200px' : 'none';
        expandBar.querySelector('button').innerHTML = collapsed
          ? `展开全部 ${lineCount} 行 <i data-lucide="chevron-down" class="w-3 h-3 inline"></i>`
          : '收起 <i data-lucide="chevron-up" class="w-3 h-3 inline"></i>';
        refreshIcons();
      });
    }
    if (lineCount > 3) {
      const toggle = wrapper.querySelector('.code-line-toggle');
      toggle.style.display = '';
      let linesOn = false;
      toggle.addEventListener('click', () => {
        linesOn = !linesOn;
        applyLineNumbers(pre, codeEl, linesOn);
        toggle.classList.toggle('text-brand-cobalt', linesOn);
      });
    }
    // 复制
    on(wrapper.querySelector('.code-copy-btn'), 'click', async function () {
      const ok = await copyToClipboard(rawText);
      if (ok) {
        this.classList.add('copied');
        this.innerHTML = '<i data-lucide="check" class="w-3 h-3"></i>已复制';
        refreshIcons();
        setTimeout(() => { this.classList.remove('copied'); this.innerHTML = '<i data-lucide="copy" class="w-3 h-3"></i>复制'; refreshIcons(); }, 2000);
      }
    });
    // 全屏查看
    on(wrapper.querySelector('.code-fullscreen-btn'), 'click', () => {
      openCodeFullscreen(String(rawText), lang);
    });
  });
  refreshIcons();
}

/** 在代码块 pre 上应用/移除行号列
 * @param {HTMLElement} pre - pre元素
 * @param {HTMLElement} codeEl - code元素
 * @param {boolean} on - 是否开启
 * @returns {void} */
function applyLineNumbers(pre, codeEl, on) {
  const old = pre.parentElement.querySelector('.code-line-numbers');
  if (old) old.remove();
  if (!on) {
    pre.style.paddingLeft = '14px';
    return;
  }
  const lines = codeEl.textContent.split('\n').length;
  const gutter = document.createElement('div');
  gutter.className = 'code-line-numbers';
  gutter.style.cssText = 'padding:12px 8px 12px 12px;user-select:none';
  let html = '';
  for (let i = 1; i <= lines; i++) html += i + '<br>';
  gutter.innerHTML = html;
  pre.style.paddingLeft = '4px';
  pre.parentElement.insertBefore(gutter, pre);
  pre.parentElement.style.display = 'flex';
}

/** 全屏查看代码
 * @param {string} code - 代码文本
 * @param {string} lang - 语言
 * @returns {void} */
function openCodeFullscreen(code, lang) {
  const html = `<div class="h-full flex flex-col">
    <div class="code-block flex-1 overflow-auto rounded-none border-0">
      <div class="code-header"><div class="code-dots"><span></span><span></span><span></span></div><span class="code-lang">${escapeHtml(lang || 'text')}</span></div>
      <pre class="text-text-secondary text-[13px]" style="padding:16px;margin:0"><code>${escapeHtml(code)}</code></pre>
    </div></div>`;
  showModal(html, { title: '代码查看', icon: 'code-2', wide: true });
  try { const el = document.querySelector('#modalBox pre code'); if (el && window.hljs) window.hljs.highlightElement(el); } catch (e) { /* 忽略 */ }
}


/* ===================== [兼容迁移] 旧版 aether_db -> moray_db =====================
   以下 'aether' 字符串为刻意的旧版数据源引用，仅用于一次性迁移，不属于品牌遗漏。 */

/** 迁移旧版 IndexedDB 数据（moray_db 为空且旧库 aether_db 有数据时执行）
 * @returns {Promise<boolean>} 是否发生了迁移 */
async function migrateLegacyDB() {
  try {
    // [运行体检修复] IndexedDB 被禁用/隐私模式时 indexedDB 本身可能为 undefined：
    // 先判 typeof 再访问 .databases，避免降级路径额外抛 TypeError 并刷 console.warn。
    if (typeof indexedDB === 'undefined' || !indexedDB || typeof indexedDB.databases !== 'function') return false;
    const dbs = await indexedDB.databases();
    if (!(dbs || []).some(d => d.name === 'aether_db')) return false;
    await DB.open();
    const existing = await DB.listConversations();
    if (existing.length > 0) return false; // 新库已有数据，不覆盖
    const legacy = await new Promise((resolve, reject) => {
      const req = indexedDB.open('aether_db');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    const stores = DB_STORES; // [修复] 统一常量（补 apicache）
    let copied = 0;
    for (const s of stores) {
      if (!legacy.objectStoreNames.contains(s)) continue;
      const rows = await new Promise((resolve, reject) => {
        const tx = legacy.transaction(s, 'readonly');
        const rq = tx.objectStore(s).getAll();
        rq.onsuccess = () => resolve(rq.result);
        rq.onerror = () => reject(rq.error);
      });
      for (const row of rows) {
        await DB._tx(s, 'readwrite', tx => tx.objectStore(s).put(row));
        copied++;
      }
    }
    legacy.close();
    // 旧库保留作为备份，不自动删除（数据安全优先）
    if (copied) console.log('[MoRay] 已从旧库 aether_db 迁移', copied, '条记录');
    return copied > 0;
  } catch (e) {
    console.warn('[MoRay] legacy DB migration failed:', e);
    return false;
  }
}
