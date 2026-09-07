/* ============================================================
   模块：代码片段库（任务五）/ 文档知识库 RAG（任务六）
   ============================================================ */

/* ===================== [任务五] 代码片段库 ===================== */

/** 支持的语言清单（含高亮映射） */
const SNIPPET_LANGUAGES = ['javascript', 'typescript', 'python', 'java', 'go', 'rust', 'c', 'cpp', 'csharp', 'php', 'ruby', 'swift', 'kotlin', 'sql', 'html', 'css', 'shell', 'yaml', 'json', 'markdown', 'text'];

/** 根据代码内容启发式识别语言
 * @param {string} code - 代码文本
 * @returns {string|null} 语言标识 */
function detectLanguage(code) {
  if (!code) return null;
  const c = code.slice(0, 2000);
  if (/^\s*(import|export|const|let|var|function|=>|async\s)/m.test(c) && /[{;]/.test(c)) return 'javascript';
  if (/\b(def|import)\s+\w+|__init__|self\.|elif\b/.test(c)) return 'python';
  if (/\bpublic\s+(static\s+)?(class|void)\b/.test(c)) return 'java';
  if (/\bpackage\s+main\b|func\s+\w+\(/.test(c)) return 'go';
  if (/\bfn\s+\w+\(|println!/.test(c)) return 'rust';
  if (/#include\s*<|std::/.test(c)) return 'cpp';
  if (/\bSELECT\b.*\bFROM\b/i.test(c)) return 'sql';
  if (/^#!\s*\/bin\/(ba)?sh|^\s*(echo|sudo|apt|npm|pip)\s/m.test(c)) return 'shell';
  if (/^\s*[{[]/.test(c) && /"\w+"\s*:/.test(c)) return 'json';
  if (/<\/?[a-z][\w-]*[^>]*>/.test(c) && /<!DOCTYPE|<html|<div/i.test(c)) return 'html';
  if (/^\s*[.#@][\w-]+\s*\{/m.test(c)) return 'css';
  if (/^\s*---\s*$/m.test(c) && /^\s*\w+:\s*/m.test(c)) return 'yaml';
  if (/^#{1,6}\s+\S/m.test(c) && /^\s*\S+/m.test(c) && !/[;{}]/.test(c)) return 'markdown';
  return null;
}

/** 片段库应用 */
const SnippetsApp = {
  /** 全部片段 @type {Array} */
  cache: [],
  /** 语言筛选 @type {string} */
  language: '全部',
  /** 搜索词 @type {string} */
  query: '',

  /** 加载渲染
   * @returns {Promise<void>} */
  async reload() {
    try { this.cache = await DB.listSnippets(); } catch (e) { this.cache = []; console.error(e); }
    this.render();
    this.renderSidebar();
  },

  /** 渲染语言侧栏
   * @returns {void} */
  renderSidebar() {
    const el = document.getElementById('snippetLangList');
    if (!el) return;
    const counts = {};
    this.cache.forEach(s => { const l = s.language || 'text'; counts[l] = (counts[l] || 0) + 1; });
    const langs = Array.from(new Set([...SNIPPET_LANGUAGES, ...Object.keys(counts)])).filter(l => counts[l]);
    let html = `
      <div class="list-item text-sm flex items-center justify-between ${this.language === '全部' ? 'active' : ''}" data-lang="全部"><span>全部片段</span><span class="text-[10px] text-text-tertiary">${this.cache.length}</span></div>
      <div class="list-item text-sm flex items-center justify-between ${this.language === '★' ? 'active' : ''}" data-lang="★"><span>★ 收藏</span><span class="text-[10px] text-text-tertiary">${this.cache.filter(s => s.isFavorite).length}</span></div>`;
    langs.forEach(l => {
      html += `<div class="list-item text-sm flex items-center justify-between ${this.language === l ? 'active' : ''}" data-lang="${escapeHtml(l)}"><span>${escapeHtml(l)}</span><span class="text-[10px] text-text-tertiary">${counts[l]}</span></div>`;
    });
    el.innerHTML = html;
    el.querySelectorAll('[data-lang]').forEach(item => {
      item.addEventListener('click', () => { this.language = item.dataset.lang; this.__limit = 50; this.render(); this.renderSidebar(); });
    });
  },

  /** 渲染片段列表
   * @returns {void} */
  render() {
    const list = document.getElementById('snippetsList') || document.querySelector('#page-snippets .flex-1.overflow-y-auto');
    if (!list) return;
    if (list.id !== 'snippetsList') list.id = 'snippetsList';
    let items = this.cache.slice();
    if (this.language === '★') items = items.filter(s => s.isFavorite);
    else if (this.language !== '全部') items = items.filter(s => (s.language || 'text') === this.language);
    if (this.query) {
      const q = this.query.toLowerCase();
      items = items.filter(s => (s.title || '').toLowerCase().includes(q) || (s.code || '').toLowerCase().includes(q) || (s.tags || []).some(t => t.toLowerCase().includes(q)));
    }
    items.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    // [夜间优化1.2] 增量分页
    this.__hasMore = 0;
    if (this.__limit && items.length > this.__limit) {
      this.__hasMore = items.length - this.__limit;
      items = items.slice(0, this.__limit);
    }

    if (!items.length) {
      // [V3 P2.5] 统一空状态
      list.innerHTML = renderEmptyState({
        icon: 'code-2', title: this.query ? '没有匹配的片段' : '收藏常用代码片段',
        desc: this.query ? '换个关键词试试' : '高频代码一处沉淀，@ 引用直达对话', actionText: this.query ? '' : '新增片段'
      });
      if (!this.query) bindEmptyAction(list, () => this.openEditor());
      refreshIcons();
      return;
    }
    // [阶段三] 按文件夹分组（无文件夹的排在后面）
    const folderOf = s => s.folder || '';
    items.sort((a, b) => (folderOf(a) || '~').localeCompare(folderOf(b) || '~') || (b.updatedAt || 0) - (a.updatedAt || 0));
    let lastFolder = null;
    const withHeaders = [];
    items.forEach(s => {
      const f = folderOf(s);
      if (f !== lastFolder) {
        lastFolder = f;
        withHeaders.push({ header: f || '未分类' });
      }
      withHeaders.push({ snippet: s });
    });
    list.innerHTML = withHeaders.map(it => {
      if (it.header) return `<div class="text-[10px] text-text-tertiary px-1 pt-3 pb-1 font-semibold uppercase tracking-wider flex items-center gap-1"><i data-lucide="folder" class="w-3 h-3"></i>${escapeHtml(it.header)}</div>`;
      const s = it.snippet;
      const lines = (s.code || '').split('\n').length;
      return `
      <div class="glass-card rounded-lg overflow-hidden border border-line-ghost/60 hover-lift snippet-card" data-snip-id="${s.id}">
        <div class="px-4 py-3 flex items-center justify-between border-b border-line-ghost/50">
          <div class="flex items-center gap-2 min-w-0">
            <button class="star-btn ${s.isFavorite ? 'starred' : ''}" data-op="fav" title="收藏"><i data-lucide="star" class="w-3.5 h-3.5"></i></button>
            <h3 class="text-sm font-medium text-text-primary truncate">${escapeHtml(s.title)}</h3>
            <span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt">${escapeHtml(s.language || 'text')}</span>
            <span class="tag-pill bg-surface-floating text-text-tertiary">${lines} 行</span>
          </div>
          <div class="flex items-center gap-1 flex-shrink-0">
            ${s.language === 'javascript' ? `<button class="tool-btn" data-op="run" title="沙箱运行(JS)"><i data-lucide="play" class="w-3.5 h-3.5"></i></button>` : ''}
            <button class="tool-btn" data-op="share" title="分享链接"><i data-lucide="share-2" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="use" title="在对话中引用"><i data-lucide="corner-down-left" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="copy" title="复制"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="edit" title="编辑"><i data-lucide="edit-3" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
        <div class="code-preview"><div class="code-block rounded-none border-0" style="max-height:150px;overflow:hidden"><div class="code-header">
          <div class="code-dots"><span></span><span></span><span></span></div><span class="code-lang">${escapeHtml(s.language || 'text')}</span></div>
          <pre class="text-text-secondary text-[12px]" style="padding:12px 14px;margin:0"><code class="language-${escapeHtml(s.language || 'text')}">${escapeHtml((s.code || '').slice(0, 1200))}</code></pre>
        </div></div>
        <div class="px-4 py-2 flex items-center justify-between border-t border-line-ghost/50">
          <div class="flex items-center gap-2 flex-wrap">${(s.tags || []).map(t => `<span class="tag-pill bg-surface-floating text-text-tertiary">${escapeHtml(t)}</span>`).join('')}</div>
          <div class="flex items-center gap-2 flex-shrink-0">
            <span class="text-[10px] text-text-tertiary">${relTime(s.updatedAt)}</span>
            <button class="text-xs text-brand-cobalt hover:text-brand-cyan flex items-center gap-1" data-op="expand" title="展开/收起完整代码">
              展开查看 <i data-lucide="chevron-down" class="w-3 h-3 snippet-expand-icon"></i>
            </button>
          </div>
        </div>
      </div>`;
    }).join('');
    // 高亮预览
    list.querySelectorAll('pre code').forEach(el => { try { if (window.hljs) hljs.highlightElement(el); } catch (e) { /* 忽略 */ } });
    refreshIcons();
  },

  /** 事件委托（[零摆设 P0-2] 挂到稳定的页面容器，避免 boot 时 #snippetsList 尚不存在导致委托挂空）
   * @returns {void} */
  bindEvents() {
    const list = document.getElementById('snippetsList') || document.querySelector('#page-snippets');
    if (list) {
      list.addEventListener('click', (e) => {
        const card = e.target.closest('.snippet-card');
        if (!card) return;
        const id = card.dataset.snipId;
        const btn = e.target.closest('[data-op]');
        const op = btn ? btn.dataset.op : 'copy';
        if (op === 'fav') this.toggleFav(id);
        else if (op === 'edit') this.openEditor(id);
        else if (op === 'delete') this.remove(id);
        else if (op === 'use') this.useInChat(id);
        else if (op === 'run') { const sn = this.cache.find(x => x.id === id); if (sn) openCodeRunner(sn.code); }
        else if (op === 'share') { const sn = this.cache.find(x => x.id === id); if (sn) shareSnippetLink(sn); }
        else if (op === 'copy') this.copy(id);
        else if (op === 'expand') this.expandCode(id, card);
      });
    }
    const search = document.querySelector('#page-snippets input[type="text"]');
    if (search) search.addEventListener('input', debounce(() => { this.query = search.value; this.__limit = 50; this.render(); }, 150));
    const addBtn = document.querySelector('#page-snippets .btn-primary');
    if (addBtn) addBtn.addEventListener('click', () => this.openEditor());
    // 侧栏新建文件夹按钮复用为"导入"
    const importBtn = document.getElementById('newSnippetFolderBtn');
    if (importBtn) {
      importBtn.title = '从文件导入片段';
      importBtn.innerHTML = '<i data-lucide="upload" class="w-4 h-4"></i>';
      importBtn.addEventListener('click', () => {
        const inp = document.createElement('input');
        inp.type = 'file'; inp.accept = '.json'; inp.multiple = true;
        inp.onchange = () => { Array.from(inp.files).forEach(f => this.importFrom(f)); };
        inp.click();
      });
    }
  },

  /** 切换收藏
   * @param {string} id - 片段ID
   * @returns {Promise<void>} */
  async toggleFav(id) {
    const s = this.cache.find(x => x.id === id);
    if (!s) return;
    s.isFavorite = !s.isFavorite;
    await DB.updateSnippet(id, { isFavorite: s.isFavorite });
    this.render(); this.renderSidebar();
  },

  /** 删除片段
   * @param {string} id - 片段ID
   * @returns {Promise<void>} */
  async remove(id) {
    const s = this.cache.find(x => x.id === id);
    showConfirm('删除片段', `确定删除「${s ? s.title : ''}」？`, async () => {
      await DB.deleteSnippet(id);
      this.cache = this.cache.filter(x => x.id !== id);
      this.render(); this.renderSidebar();
      showNotification('已删除', '片段已删除', 'success', 1500);
    }, { danger: true, okText: '删除' });
  },

  /** 复制代码
   * @param {string} id - 片段ID
   * @returns {Promise<void>} */
  async copy(id) {
    const s = this.cache.find(x => x.id === id);
    if (!s) return;
    const ok = await copyToClipboard(s.code);
    showNotification(ok ? '已复制' : '复制失败', s.title, ok ? 'success' : 'error', 1600);
  },

  /** [零摆设 P0-2] 展开/收起完整代码（注入完整代码并高亮，箭头旋转，再点收起）
   * @param {string} id - 片段ID
   * @param {HTMLElement} card - 片段卡 */
  expandCode(id, card) {
    const s = this.cache.find(x => x.id === id);
    if (!s || !card) return;
    const preview = card.querySelector('.code-preview');
    const icon = card.querySelector('.snippet-expand-icon');
    const btn = card.querySelector('[data-op="expand"]');
    const isOpen = preview && preview.dataset.expanded === '1';
    if (isOpen) {
      if (preview) { preview.dataset.expanded = '0'; preview.querySelector('.code-block').style.maxHeight = '150px'; }
      if (icon) icon.style.transform = '';
      if (btn) btn.firstChild.textContent = '展开查看';
      return;
    }
    // 注入完整代码（先截断后恢复为全量）并重新高亮
    const codeEl = preview ? preview.querySelector('pre code') : null;
    if (codeEl) {
      codeEl.textContent = s.code || '';
      try { if (window.hljs) hljs.highlightElement(codeEl); } catch (e) { /* 忽略 */ }
    }
    if (preview) { preview.dataset.expanded = '1'; preview.querySelector('.code-block').style.maxHeight = 'none'; }
    if (icon) icon.style.transform = 'rotate(180deg)';
    if (btn) btn.firstChild.textContent = '收起';
  },

  /** 在对话中引用（插入代码块引用）
   * @param {string} id - 片段ID
   * @returns {void} */
  async useInChat(id) {
    const s = this.cache.find(x => x.id === id);
    if (!s) return;
    const text = '请帮我分析/优化以下代码（片段：' + s.title + '）：\n\n```' + (s.language || '') + '\n' + s.code + '\n```';
    PromptsApp.insertToInput(text);
    showNotification('已引用', '片段「' + s.title + '」已插入对话输入框', 'success', 2000);
  },

  /** 编辑器（新建/编辑）
   * @param {string} [id] - 片段ID
   * @returns {void} */
  openEditor(id) {
    const s = id ? this.cache.find(x => x.id === id) : null;
    const box = showModal(`
      <div class="form-row form-inline">
        <div style="flex:2">
          <label class="form-label">标题 *</label>
          <input type="text" id="seTitle" class="form-input" value="${escapeHtml(s ? s.title : '')}" placeholder="例如：防抖函数 debounce">
        </div>
        <div>
          <label class="form-label">语言</label>
          <select id="seLang" class="form-select">${SNIPPET_LANGUAGES.map(l => `<option ${s && s.language === l ? 'selected' : ''}>${l}</option>`).join('')}</select>
        </div>
      </div>
      <div class="form-row">
        <label class="form-label">代码 *<button class="text-[10px] text-brand-cobalt ml-2" id="seDetect">自动识别语言</button><button class="text-[10px] text-brand-cobalt ml-2" id="seFormat">Prettier 格式化</button></label>
        <textarea id="seCode" class="form-textarea" style="min-height:180px" placeholder="粘贴代码...">${escapeHtml(s ? s.code : '')}</textarea>
      </div>
      <div class="form-row">
        <label class="form-label">描述</label>
        <input type="text" id="seDesc" class="form-input" value="${escapeHtml(s ? s.description : '')}" placeholder="用途说明">
      </div>
      <div class="form-row form-inline">
        <div style="flex:2">
          <label class="form-label">标签（逗号分隔）</label>
          <input type="text" id="seTags" class="form-input" value="${escapeHtml(s ? (s.tags || []).join(', ') : '')}" placeholder="工具函数, 性能">
        </div>
        <div>
          <label class="form-label">文件夹</label>
          <input type="text" id="seFolder" class="form-input" value="${escapeHtml(s ? (s.folder || '') : '')}" placeholder="如：算法/排序">
        </div>
      </div>`,
      {
        title: s ? '编辑片段' : '新增片段', icon: 'code-2',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-se-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="seSave">保存</button>`
      });
    on(box.querySelector('[data-se-cancel]'), 'click', closeModal);
    on(box.querySelector('#seFormat'), 'click', async () => {
      const ta = box.querySelector('#seCode');
      try {
        ta.value = await formatWithPrettier(ta.value, box.querySelector('#seLang').value);
        showNotification('格式化完成', 'Prettier 已应用', 'success', 1500);
      } catch (e) { showNotification('格式化失败', e.message, 'error', 2500); }
    });
    on(box.querySelector('#seDetect'), 'click', () => {
      const lang = detectLanguage(box.querySelector('#seCode').value);
      if (lang) { box.querySelector('#seLang').value = lang; showNotification('已识别', '语言：' + lang, 'success', 1400); }
      else showNotification('无法识别', '请手动选择语言', 'warning', 1500);
    });
    on(box.querySelector('#seSave'), 'click', async () => {
      const title = box.querySelector('#seTitle').value.trim();
      const code = box.querySelector('#seCode').value;
      if (!title || !code) { showNotification('请填写完整', '标题和代码为必填项', 'warning'); return; }
      const data = {
        title, code,
        language: box.querySelector('#seLang').value,
        description: box.querySelector('#seDesc').value.trim(),
        folder: box.querySelector('#seFolder').value.trim(),
        tags: box.querySelector('#seTags').value.split(/[,，]/).map(x => x.trim()).filter(Boolean)
      };
      if (s) await DB.updateSnippet(s.id, data);
      else await DB.createSnippet(data);
      closeModal();
      await this.reload();
      showNotification(s ? '已更新' : '已创建', title, 'success', 1600);
    });
  },

  /** 从 JSON 文件导入片段（支持 MoRay 格式与 {snippets:[...]}）
   * @param {File} file - 文件
   * @returns {Promise<void>} */
  async importFrom(file) {
    try {
      const data = JSON.parse(await file.text());
      const items = Array.isArray(data) ? data : (data.snippets || []);
      let count = 0;
      for (const raw of items) {
        if (!raw.code && !raw.snippet) continue;
        await DB.createSnippet({
          title: raw.title || raw.name || '导入片段',
          code: raw.code || raw.snippet,
          language: raw.language || detectLanguage(raw.code || '') || 'text',
          description: raw.description || '',
          tags: raw.tags || []
        });
        count++;
      }
      await this.reload();
      showNotification('导入完成', `成功导入 ${count} 个片段`, 'success');
    } catch (e) {
      showNotification('导入失败', e.message, 'error', 3000);
    }
  }
};

/* ===================== [任务六] 文档知识库 RAG ===================== */

/** 本地哈希 TF 向量（Ollama embedding 不可用时的离线回退）
 * 将文本词袋哈希到 256 维，词频加权并归一化。
 * @param {string} text - 文本
 * @returns {number[]} 256维向量 */
function localHashVector(text) {
  const DIM = 256;
  const vec = new Array(DIM).fill(0);
  const tokens = String(text || '').toLowerCase()
    .replace(/[^\w\u4e00-\u9fa5]+/g, ' ').split(/\s+/).filter(Boolean);
  // 英文 2-gram + 中文单字，提升区分度
  const grams = [];
  tokens.forEach(t => {
    grams.push(t);
    if (/^[\u4e00-\u9fa5]+$/.test(t)) { for (let i = 0; i < t.length; i++) grams.push(t[i]); }
  });
  grams.forEach(g => {
    let h = 2166136261;
    for (let i = 0; i < g.length; i++) { h ^= g.charCodeAt(i); h = Math.imul(h, 16777619); }
    vec[Math.abs(h) % DIM] += 1;
  });
  const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
  return vec.map(v => v / norm);
}

/** 余弦相似度
 * @param {number[]} a - 向量A
 * @param {number[]} b - 向量B
 * @returns {number} 相似度（-1~1） */
function cosineSimilarity(a, b) {
  if (!a || !b || a.length !== b.length) return 0;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}

/** 文本分块：优先按段落/标题语义切分，超长段落按固定大小+重叠切分
 * @param {string} text - 全文
 * @param {number} chunkSize - 目标块大小（字符）
 * @param {number} overlap - 重叠字符数
 * @returns {Array<{text:string, meta:string}>} 分块列表 */
function chunkText(text, chunkSize, overlap) {
  const chunks = [];
  const paras = String(text || '').split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  let buf = '';
  paras.forEach(p => {
    // 标题行作为独立 meta
    if (p.length > chunkSize) {
      if (buf) { chunks.push({ text: buf, meta: '' }); buf = ''; }
      // 滑窗切分长段落
      for (let i = 0; i < p.length; i += chunkSize - overlap) {
        chunks.push({ text: p.slice(i, i + chunkSize), meta: '' });
      }
      return;
    }
    if ((buf + '\n\n' + p).length > chunkSize && buf) {
      chunks.push({ text: buf, meta: '' });
      buf = p;
    } else {
      buf = buf ? buf + '\n\n' + p : p;
    }
  });
  if (buf) chunks.push({ text: buf, meta: '' });
  return chunks;
}

/** 文档库应用 */
const DocsApp = {
  /** 全部文档 @type {Array} */
  cache: [],
  /** 是否正在索引 @type {boolean} */
  indexing: false,

  /** 重建文档库页面 UI（接管 Coming Soon 占位）
   * @returns {void} */
  build() {
    const page = document.getElementById('page-docs');
    if (!page) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2">
          <i data-lucide="book-open" class="w-4 h-4 text-brand-violet"></i>
          <span class="text-sm font-medium text-text-primary">文档知识库</span>
          <span class="tag-pill bg-brand-violet/15 text-brand-violet" id="docCountPill">0 篇文档</span>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="ragSettingsBtn">
            <i data-lucide="settings-2" class="w-3.5 h-3.5"></i>知识库设置
          </button>
          <div class="relative w-56">
            <i data-lucide="search" class="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-tertiary"></i>
            <input type="text" id="docSearchInput" placeholder="语义检索文档内容..." class="w-full h-8 pl-9 pr-3 rounded-lg bg-surface-card border border-line-ghost text-xs text-text-primary placeholder:text-text-tertiary focus:outline-none">
          </div>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-4xl mx-auto space-y-4">
          <!-- 上传区 -->
          <div class="upload-zone p-8" id="docUploadZone">
            <input type="file" id="docFileInput" multiple accept=".pdf,.md,.markdown,.txt,.docx,.js,.ts,.py,.java,.go,.rs,.c,.cpp,.h,.css,.html,.json,.yaml,.yml,.sh" style="display:none">
            <i data-lucide="upload-cloud" class="w-8 h-8 mx-auto mb-3 text-brand-cobalt"></i>
            <div class="text-sm text-text-primary mb-1">拖拽文件到此处，或点击选择</div>
            <div class="text-[11px] text-text-tertiary">支持 PDF / Markdown / TXT / DOCX / 代码文件 · 多文件 · 完全本地解析</div>
            <div class="mt-3 max-w-md mx-auto" id="uploadProgressWrap" style="display:none">
              <div class="progress-thin"><div id="uploadProgressBar" style="width:0%"></div></div>
              <div class="text-[10px] text-text-tertiary mt-1" id="uploadProgressText"></div>
            </div>
          </div>
          <!-- 语义检索结果 -->
          <div id="ragSearchResults" class="space-y-2" style="display:none">
            <div class="text-xs font-medium text-text-secondary flex items-center gap-2"><i data-lucide="search" class="w-3.5 h-3.5 text-brand-cyan"></i>语义检索结果 <button class="text-[10px] text-text-tertiary ml-2" id="ragClearBtn">关闭</button></div>
            <div id="ragResultList" class="space-y-2"></div>
          </div>
          <!-- 文档列表 -->
          <div>
            <h3 class="text-xs font-medium text-text-secondary mb-3">文档列表</h3>
            <div class="space-y-2" id="docList"></div>
          </div>
        </div>
      </div>`;
    // 事件
    const zone = document.getElementById('docUploadZone');
    const fileInput = document.getElementById('docFileInput');
    zone.addEventListener('click', (e) => { if (e.target === zone || e.target.closest('#docUploadZone') && !e.target.closest('button')) fileInput.click(); });
    fileInput.addEventListener('change', () => { this.uploadFiles(Array.from(fileInput.files || [])); fileInput.value = ''; });
    // 拖拽上传（区域级）
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault(); e.stopPropagation();
      zone.classList.remove('dragover');
      this.uploadFiles(Array.from(e.dataTransfer.files || []));
    });
    // 语义搜索
    const search = document.getElementById('docSearchInput');
    search.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && search.value.trim()) this.semanticSearch(search.value.trim());
    });
    on(document.getElementById('ragClearBtn'), 'click', () => {
      document.getElementById('ragSearchResults').style.display = 'none';
      search.value = '';
    });
    on(document.getElementById('ragSettingsBtn'), 'click', () => this.openSettings());
    refreshIcons();
  },

  /** 加载并渲染
   * @returns {Promise<void>} */
  async reload() {
    try { this.cache = await DB.listDocuments(); } catch (e) { this.cache = []; console.error(e); }
    this.renderList();
    this.renderDocSidebar();
  },

  /** 渲染文档列表
   * @returns {void} */
  renderList() {
    const list = document.getElementById('docList');
    if (!list) return;
    const pill = document.getElementById('docCountPill');
    if (pill) pill.textContent = this.cache.length + ' 篇文档';
    if (!this.cache.length) {
      // [V3 P2.5] 统一空状态
      list.innerHTML = renderEmptyState({
        icon: 'book-open', title: '上传文档构建知识库', desc: 'PDF / Markdown / TXT / DOCX 自动解析、分块并向量化', actionText: '上传文档', dragHint: true
      });
      bindEmptyAction(list, () => { const inp = document.getElementById('docFileInput'); if (inp) inp.click(); });
      refreshIcons();
      return;
    }
    const sorted = this.cache.slice().sort((a, b) => b.createdAt - a.createdAt);
    list.innerHTML = sorted.map(d => {
      const statusCls = d.status === 'ready' ? 'ready' : d.status === 'error' ? 'error' : 'indexing';
      const statusText = d.status === 'ready' ? '已索引' : d.status === 'error' ? '解析失败' : '处理中';
      const idx = (d.chunks || []).length;
      const embedded = (d.chunks || []).filter(c => c.embedding).length;
      return `
      <div class="doc-card p-4" data-doc-id="${d.id}">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-3 min-w-0">
            <div class="w-9 h-9 rounded-lg bg-brand-violet/12 flex items-center justify-center flex-shrink-0">
              <i data-lucide="${d.type === 'pdf' ? 'file-text' : d.type === 'code' ? 'file-code' : 'file-text'}" class="w-4 h-4 text-brand-violet"></i>
            </div>
            <div class="min-w-0">
              <div class="text-sm text-text-primary truncate">${escapeHtml(d.name)}</div>
              <div class="text-[10px] text-text-tertiary">${formatBytes(d.size)} · ${idx} 块${d.status === 'ready' ? ' · 已向量化 ' + embedded + ' 块' : ''} · ${relTime(d.createdAt)}</div>
            </div>
          </div>
          <div class="flex items-center gap-2 flex-shrink-0">
            <span class="doc-status ${statusCls}">${statusText}</span>
            <button class="tool-btn" data-doc-op="preview" title="预览"><i data-lucide="eye" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-doc-op="reindex" title="重新索引"><i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-doc-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
        ${d.status === 'error' && d.error ? `<div class="text-[10px] text-danger mt-2">${escapeHtml(d.error)}</div>` : ''}
      </div>`;
    }).join('');
    refreshIcons();
  },

  /** 渲染文档侧栏（最近文档快捷入口）
   * @returns {void} */
  renderDocSidebar() {
    const el = document.getElementById('docSidebarList');
    if (!el) return;
    if (!this.cache.length) {
      el.innerHTML = `<div class="empty-state"><i data-lucide="inbox"></i>
        <div class="empty-title">暂无文档</div><div class="empty-hint">在上方粘贴或导入 Markdown / PDF，即可开始构建知识库</div></div>`;
      refreshIcons();
      return;
    }
    el.innerHTML = this.cache.slice(0, 20).map(d => `
      <div class="list-item text-sm flex items-center justify-between" data-sidebar-doc="${d.id}">
        <span class="truncate">${escapeHtml(d.name)}</span>
        <span class="text-[10px] text-text-tertiary">${(d.chunks || []).length}</span>
      </div>`).join('');
    el.querySelectorAll('[data-sidebar-doc]').forEach(item => {
      item.addEventListener('click', () => {
        document.querySelector('.nav-icon-btn[data-page="docs"]').click();
        this.openPreview(item.dataset.sidebarDoc, 0);
      });
    });
  },

  /** 文档列表事件委托
   * @returns {void} */
  bindEvents() {
    const list = document.getElementById('docList');
    if (list) {
      list.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-doc-op]');
        const card = e.target.closest('.doc-card');
        if (!card) return;
        const id = card.dataset.docId;
        const op = btn ? btn.dataset.docOp : 'preview';
        if (op === 'preview') this.openPreview(id, 0);
        else if (op === 'reindex') this.reindex(id);
        else if (op === 'delete') this.remove(id);
      });
    }
  },

  /**
   * 上传并解析文件（批量）
   * @param {File[]} files - 文件数组
   * @returns {Promise<void>} */
  async uploadFiles(files) {
    if (!files || !files.length) return;
    const wrap = document.getElementById('uploadProgressWrap');
    const bar = document.getElementById('uploadProgressBar');
    const text = document.getElementById('uploadProgressText');
    if (wrap) wrap.style.display = '';
    let done = 0;
    for (const f of files) {
      done++;
      if (text) text.textContent = `解析 ${f.name}（${done}/${files.length}）...`;
      if (bar) bar.style.width = Math.round(done / files.length * 100) + '%';
      try {
        await this.parseAndIndex(f);
      } catch (e) {
        console.error('[MoRay] doc parse error:', e);
        await DB.putDocument({
          id: uid('doc'), name: f.name, size: f.size, type: this.docType(f.name),
          status: 'error', error: String(e.message || e), chunks: [], createdAt: Date.now(), tags: []
        });
      }
    }
    if (text) text.textContent = `完成：${files.length} 个文件已处理`;
    setTimeout(() => { if (wrap) wrap.style.display = 'none'; if (bar) bar.style.width = '0%'; }, 2000);
    await this.reload();
    showNotification('文档已处理', `${files.length} 个文件解析完成并建立索引`, 'success');
  },

  /** 文件类型判断
   * @param {string} name - 文件名
   * @returns {'pdf'|'docx'|'code'|'text'|'markdown'} 类型 */
  docType(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return 'pdf';
    if (ext === 'docx') return 'docx';
    if (['js', 'ts', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'css', 'html', 'json', 'yaml', 'yml', 'sh'].includes(ext)) return 'code';
    if (['md', 'markdown'].includes(ext)) return 'markdown';
    return 'text';
  },

  /**
   * 解析单个文件 -> 分块 -> 建索引
   * @param {File} file - 文件
   * @returns {Promise<Object>} 文档记录 */
  async parseAndIndex(file) {
    const type = this.docType(file.name);
    let text = '';
    if (type === 'pdf') text = await this.parsePdf(file);
    else if (type === 'docx') text = await this.parseDocx(file);
    else text = await file.text();

    const { chunkSize, chunkOverlap } = { chunkSize: MoraySettings.get('chunkSize'), chunkOverlap: MoraySettings.get('chunkOverlap') };
    const chunks = chunkText(text, chunkSize, chunkOverlap).map((c, i) => ({ id: uid('chunk'), idx: i, text: c.text, meta: c.meta, embedding: null }));
    const doc = {
      id: uid('doc'), name: file.name, size: file.size, type,
      status: 'ready', chunks, createdAt: Date.now(), tags: [], error: ''
    };
    await DB.putDocument(doc);
    this.cache.push(doc);
    this.renderList();
    // 异步向量化（不阻塞 UI）
    this.embedDocument(doc).catch(e => console.warn('[MoRay] embed failed:', e));
    return doc;
  },

  /**
   * PDF 解析（懒加载本地 pdf.js，vendor/ 内无 CDN）
   * @param {File} file - PDF 文件
   * @returns {Promise<string>} 提取文本 */
  async parsePdf(file) {
    if (!window.pdfjsLib) {
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'vendor/pdf.min.js';
        s.onload = resolve; s.onerror = () => reject(new Error('pdf.js 本地加载失败，请确认 vendor/ 目录完整'));
        document.head.appendChild(s);
      });
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdf.worker.min.js';
    }
    const buf = await file.arrayBuffer();
    const pdf = await window.pdfjsLib.getDocument({ data: buf }).promise;
    let text = '';
    const maxPages = Math.min(pdf.numPages, 120); // 大文档上限保护
    for (let i = 1; i <= maxPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      text += content.items.map(it => it.str).join(' ') + '\n\n';
    }
    return text;
  },

  /**
   * DOCX 解析（懒加载本地 mammoth.js，vendor/ 内无 CDN）
   * @param {File} file - DOCX 文件
   * @returns {Promise<string>} 提取文本 */
  async parseDocx(file) {
    if (!window.mammoth) {
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'vendor/mammoth.browser.min.js';
        s.onload = resolve; s.onerror = () => reject(new Error('mammoth.js 本地加载失败，请确认 vendor/ 目录完整'));
        document.head.appendChild(s);
      });
    }
    const buf = await file.arrayBuffer();
    const result = await window.mammoth.extractRawText({ arrayBuffer: buf });
    return result.value || '';
  },

  /**
   * 向量化文档分块：优先 Ollama embedding，失败回退本地哈希向量
   * @param {Object} doc - 文档对象
   * @returns {Promise<void>} */
  async embedDocument(doc) {
    doc.status = 'indexing';
    await DB.putDocument(doc);
    this.renderList();
    // [阶段三] 后端链：auto=Ollama优先 -> Transformers.js -> Hash；显式指定则强制
    const backend = MoraySettings.get('embeddingBackend') || 'auto';
    let mode = 'hash';
    if (backend === 'transformers') {
      mode = (await TransformersEmbedder.load()) ? 'transformers' : 'hash';
    } else if (backend === 'auto') {
      if (AI.backend === 'ollama') {
        try { await AI.embed(MoraySettings.get('embeddingModel'), 'test'); mode = 'ollama'; } catch (e) { /* 探测失败 */ }
      }
      if (mode === 'hash') mode = (await TransformersEmbedder.load()) ? 'transformers' : 'hash';
    }
    if (mode === 'hash') {
      // 哈希向量批量走 Web Worker（不阻塞 UI），不可用回退主线程
      const need = doc.chunks.filter(c => !c.embedding);
      const vectors = (typeof HashVectorWorker !== 'undefined')
        ? await HashVectorWorker.embedBatch(need.map(c => c.text))
        : need.map(c => localHashVector(c.text));
      need.forEach((c, i) => { c.embedding = vectors[i]; });
    } else {
      for (const chunk of doc.chunks) {
        if (chunk.embedding) continue;
        try {
          chunk.embedding = mode === 'ollama'
            ? await AI.embed(MoraySettings.get('embeddingModel'), chunk.text)
            : await TransformersEmbedder.embed(chunk.text);
        } catch (e) { chunk.embedding = localHashVector(chunk.text); }
        // 每 8 块落盘一次
        if (chunk.idx % 8 === 0) await DB.putDocument(doc);
      }
    }
    doc.status = 'ready';
    await DB.putDocument(doc);
    this.renderList();
    this.renderDocSidebar();
  },

  /**
   * 语义检索：查询向量化 -> 余弦 Top-K
   * @param {string} query - 查询文本
   * @returns {Promise<Array<{doc:Object, chunk:Object, score:number}>>} 结果 */
  async semanticSearch(query) {
    if (!query) return [];
    let qvec = null;
    if (AI.backend === 'ollama') {
      try { qvec = await AI.embed(MoraySettings.get('embeddingModel'), query); } catch (e) { /* 回退 */ }
    }
    if (!qvec) qvec = localHashVector(query);
    const results = [];
    for (const doc of this.cache) {
      (doc.chunks || []).forEach(chunk => {
        if (!chunk.embedding) return;
        const score = cosineSimilarity(qvec, chunk.embedding);
        if (score > 0.01) results.push({ doc, chunk, score });
      });
    }
    results.sort((a, b) => b.score - a.score);
    const topK = MoraySettings.get('ragTopK') || 4;
    const top = results.slice(0, topK);
    this.renderSearchResults(query, top);
    return top;
  },

  /** 渲染语义检索结果（高亮命中词）
   * @param {string} query - 查询
   * @param {Array} results - 检索结果
   * @returns {void} */
  renderSearchResults(query, results) {
    const wrapEl = document.getElementById('ragSearchResults');
    const list = document.getElementById('ragResultList');
    if (!wrapEl || !list) return;
    wrapEl.style.display = '';
    if (!results.length) {
      list.innerHTML = `<div class="text-xs text-text-tertiary">未找到相关内容。文档可能还在索引中，稍后重试。</div>`;
      return;
    }
    const terms = query.split(/\s+/).filter(t => t.length > 1).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
    list.innerHTML = results.map(r => {
      let text = escapeHtml(r.chunk.text.slice(0, 260));
      terms.forEach(t => { text = text.replace(new RegExp(t, 'gi'), m => `<mark>${m}</mark>`); });
      return `<div class="chunk-item" data-search-doc="${r.doc.id}" data-search-chunk="${r.chunk.idx}" style="cursor:pointer">
        <div class="flex items-center justify-between mb-1">
          <span class="text-brand-cyan">${escapeHtml(r.doc.name)}</span>
          <span class="text-text-tertiary">相似度 ${(r.score * 100).toFixed(1)}%</span>
        </div>
        <div>${text}…</div>
      </div>`;
    }).join('');
    list.querySelectorAll('[data-search-doc]').forEach(el => {
      el.addEventListener('click', () => this.openPreview(el.dataset.searchDoc, parseInt(el.dataset.searchChunk, 10)));
    });
  },

  /**
   * RAG 检索（供对话问答注入上下文）
   * @param {string} query - 用户问题
   * @returns {Promise<{text:string, citations:Array}|null>} 上下文与引用 */
  async retrieveContext(query) {
    const results = await this.semanticSearchRaw(query);
    if (!results.length) return null;
    const k = MoraySettings.get('ragTopK') || 4;
    const top = results.slice(0, k);
    const citations = top.map(r => ({ docId: r.doc.id, docName: r.doc.name, chunkIdx: r.chunk.idx, meta: r.chunk.meta || '' }));
    const ctx = '请基于以下知识库片段回答用户问题。如果片段不足以回答，请明确说明。\n\n' +
      top.map((r, i) => `[片段${i + 1} · 来源:${r.doc.name}${r.chunk.meta ? ' · ' + r.chunk.meta : ''}]\n${r.chunk.text}`).join('\n\n---\n\n');
    return { text: ctx, citations };
  },

  /** 原始检索（不渲染UI）
   * @param {string} query - 查询
   * @returns {Promise<Array>} 结果 */
  async semanticSearchRaw(query) {
    if (!this.cache.length) return [];
    let qvec = null;
    if (AI.backend === 'ollama') {
      try { qvec = await AI.embed(MoraySettings.get('embeddingModel'), query); } catch (e) { /* 回退 */ }
    }
    if (!qvec) qvec = localHashVector(query);
    const results = [];
    for (const doc of this.cache) {
      (doc.chunks || []).forEach(chunk => {
        if (!chunk.embedding) return;
        results.push({ doc, chunk, score: cosineSimilarity(qvec, chunk.embedding) });
      });
    }
    // [夜间优化4.4] 混合检索：向量相似度 70% + 关键词重合度 30%（轻量重排）
    results.forEach(r => {
      const kw = typeof keywordOverlap === 'function' ? keywordOverlap(query, r.chunk.text) : 0;
      r.score = r.score * 0.7 + kw * 0.3;
    });
    results.sort((a, b) => b.score - a.score);
    return results;
  },

  /** 文档预览（分块浏览，可跳转到指定块）
   * @param {string} docId - 文档ID
   * @param {number} [chunkIdx] - 初始块索引
   * @returns {Promise<void>} */
  async openPreview(docId, chunkIdx) {
    const doc = this.cache.find(d => d.id === docId) || await DB.getDocument(docId);
    if (!doc) { showNotification('文档不存在', '', 'error'); return; }
    const chunks = doc.chunks || [];
    const box = showModal(`
      <div class="space-y-2" id="chunkPreviewList" style="max-height:60vh;overflow-y:auto">
        ${chunks.map(c => `
          <div class="chunk-item" data-chunk-idx="${c.idx}" style="${c.idx === chunkIdx ? 'border-color:var(--color-brand-cyan)' : ''}">
            <div class="flex items-center justify-between mb-1">
              <span class="text-text-tertiary">块 ${c.idx + 1}/${chunks.length} ${c.meta ? '· ' + escapeHtml(c.meta) : ''}</span>
              <span class="text-text-tertiary">${c.text.length} 字</span>
            </div>
            <div>${escapeHtml(c.text.slice(0, 400))}${c.text.length > 400 ? '…' : ''}</div>
          </div>`).join('') || '<div class="text-xs text-text-tertiary">该文档没有解析出内容</div>'}
      </div>`,
      { title: doc.name, icon: 'file-text', wide: true, footer: false });
    const target = box.querySelector(`[data-chunk-idx="${chunkIdx}"]`);
    if (target) target.scrollIntoView({ block: 'center' });
  },

  /** 重新索引
   * @param {string} docId - 文档ID
   * @returns {Promise<void>} */
  async reindex(docId) {
    const doc = this.cache.find(d => d.id === docId);
    if (!doc) return;
    doc.chunks.forEach(c => { c.embedding = null; });
    this.embedDocument(doc);
    showNotification('重新索引中', doc.name, 'info', 1800);
  },

  /** 删除文档（连同向量）
   * @param {string} docId - 文档ID
   * @returns {Promise<void>} */
  async remove(docId) {
    const doc = this.cache.find(d => d.id === docId);
    showConfirm('删除文档', `确定删除「${doc ? doc.name : ''}」？其全部分块与向量将一并删除。`, async () => {
      await DB.deleteDocument(docId);
      this.cache = this.cache.filter(d => d.id !== docId);
      this.renderList(); this.renderDocSidebar();
      showNotification('已删除', '文档已删除', 'success', 1500);
    }, { danger: true, okText: '删除' });
  },

  /** 知识库设置（chunk/overlap/topK/embedding 模型）
   * @returns {void} */
  openSettings() {
    const box = showModal(`
      <div class="form-row">
        <label class="form-label">Embedding 模型（Ollama）</label>
        <input type="text" id="rkEmbedModel" class="form-input" value="${escapeHtml(MoraySettings.get('embeddingModel'))}" placeholder="nomic-embed-text">
        <div class="form-hint">不可用时自动回退为本地哈希向量检索（离线可用）</div>
      </div>
      <div class="form-row form-inline">
        <div><label class="form-label">Chunk Size（字符）</label><input type="number" id="rkChunkSize" class="form-input" value="${MoraySettings.get('chunkSize')}" min="200" max="2000" step="50"></div>
        <div><label class="form-label">Overlap（字符）</label><input type="number" id="rkOverlap" class="form-input" value="${MoraySettings.get('chunkOverlap')}" min="0" max="400" step="10"></div>
        <div><label class="form-label">Top-K</label><input type="number" id="rkTopK" class="form-input" value="${MoraySettings.get('ragTopK')}" min="1" max="20"></div>
      </div>`,
      {
        title: '知识库设置', icon: 'settings-2',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-rk-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="rkSave">保存</button>`
      });
    on(box.querySelector('[data-rk-cancel]'), 'click', closeModal);
    on(box.querySelector('#rkSave'), 'click', async () => {
      await MoraySettings.set({
        embeddingModel: box.querySelector('#rkEmbedModel').value.trim() || 'nomic-embed-text',
        chunkSize: parseInt(box.querySelector('#rkChunkSize').value, 10) || 600,
        chunkOverlap: parseInt(box.querySelector('#rkOverlap').value, 10) || 100,
        ragTopK: parseInt(box.querySelector('#rkTopK').value, 10) || 4
      });
      closeModal();
      showNotification('已保存', '知识库设置已更新', 'success', 1500);
    });
  }
};

/** 打开文档预览（供对话引用来源跳转）
 * @param {string} docId - 文档ID
 * @param {number} chunkIdx - 块索引
 * @returns {Promise<void>} */
function openDocPreview(docId, chunkIdx) { return DocsApp.openPreview(docId, chunkIdx); }

/* ===================== [零摆设 P0-2] 静态占位「展开查看」兜底绑定 ===================== */
/** 静态骨架里的「展开查看」按钮在动态渲染前短暂可见：绑定真实展开/收起（与动态卡同一行为）。
 * 动态渲染（render）会整体替换这些占位卡，此绑定仅作渲染前兜底，保证任何时刻都不是死按钮。 */
(function bindStaticExpandButtons() {
  document.querySelectorAll('#page-snippets .glass-card').forEach(card => {
    const btn = card.querySelector('button');
    if (!btn || btn.dataset.expandBound) return;
    const texts = Array.from(card.querySelectorAll('button')).map(b => (b.textContent || '').trim());
    if (!texts.some(t => t.includes('展开查看'))) return;
    btn.dataset.expandBound = '1';
    btn.addEventListener('click', () => {
      const mask = card.querySelector('.code-preview-mask');
      const icon = btn.querySelector('[data-lucide="chevron-down"]');
      const isOpen = mask && mask.dataset.expanded === '1';
      if (isOpen) {
        mask.dataset.expanded = '0';
        mask.style.maxHeight = '140px';
        if (icon) icon.style.transform = '';
        btn.firstChild.textContent = '展开查看';
      } else if (mask) {
        mask.dataset.expanded = '1';
        mask.style.maxHeight = 'none';
        if (icon) icon.style.transform = 'rotate(180deg)';
        btn.firstChild.textContent = '收起';
      }
    });
  });
})();
