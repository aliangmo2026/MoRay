/* ============================================================
   MoRay 夜间优化 · 第二阶段（性能）+ 第三阶段（UX 精选）
   任务2.1 渲染性能：IO懒高亮 / 长代码折叠 / 大Markdown分片
   任务2.2 内存：Worker闲置终止 / 会话清理提示（见30）/ 页面隐藏暂停
   任务2.3 加载：CDN预连接预加载
   任务3.1 对话体验：时间分隔线 / 实时速度 / 引用回复 / 转发 / 收藏
   任务3.4 a11y：aria-label自动补全 / prefers-reduced-motion
   ============================================================ */

/* ===================== [2.1] 代码块懒高亮 + 长块折叠 ===================== */

/** 懒高亮器：代码块滚动进入可视区域才执行 hljs（IntersectionObserver） */
const LazyHighlighter = {
  /** @type {IntersectionObserver|null} */
  _io: null,

  /** 观察一个 code 元素（进入视口时高亮）
   * @param {HTMLElement} codeEl - code 元素
   * @param {string} lang - 语言提示
   * @returns {void} */
  observe(codeEl, lang) {
    if (!window.hljs) return;
    if (!('IntersectionObserver' in window)) {
      // 降级：立即高亮
      try { if (!lang || hljs.getLanguage(lang)) hljs.highlightElement(codeEl); else hljs.highlightElement(codeEl); } catch (e) { /* 忽略 */ }
      return;
    }
    if (!this._io) {
      this._io = new IntersectionObserver((entries) => {
        entries.forEach(en => {
          if (!en.isIntersecting) return;
          this._io.unobserve(en.target);
          const el = en.target;
          try { hljs.highlightElement(el); } catch (e) { /* 高亮失败不影响显示 */ }
        });
      }, { rootMargin: '200px' }); // 提前200px预热
    }
    this._io.observe(codeEl);
  }
};

/* ===================== [2.1] 大 Markdown 分片渲染 ===================== */

/**
 * 分片渲染大文本到容器（requestIdleCallback/rAF 切片，避免长任务阻塞）
 * @param {HTMLElement} el - 目标容器
 * @param {string} fullMd - 完整 Markdown
 * @param {number} [chunkSize] - 每片字符数
 * @returns {void} */
function renderMarkdownChunked(el, fullMd, chunkSize) {
  chunkSize = chunkSize || 8000;
  if (!fullMd || fullMd.length <= chunkSize * 2) {
    el.innerHTML = renderMarkdown(fullMd);
    enhanceCodeBlocks(el, false);
    return;
  }
  // 首片立即渲染（用户先看到开头），余片空闲时分段追加
  let pos = 0;
  el.innerHTML = renderMarkdown(fullMd.slice(0, chunkSize));
  enhanceCodeBlocks(el, false);
  const renderNext = () => {
    if (pos >= fullMd.length) return;
    pos = Math.min(fullMd.length, pos + chunkSize);
    el.innerHTML = renderMarkdown(fullMd.slice(0, pos));
    enhanceCodeBlocks(el, false);
    if (pos < fullMd.length) {
      (window.requestIdleCallback || requestAnimationFrame)(renderNext);
    }
  };
  (window.requestIdleCallback || requestAnimationFrame)(renderNext);
}

/* ===================== [2.2] Worker 闲置回收 + 页面隐藏暂停 ===================== */

/** 安装性能守护（页面隐藏暂停轮询/Worker闲置终止）
 * @returns {void} */
function installPerfGuards() {
  // 页面隐藏时暂停监控图表刷新（回到前台恢复）
  document.addEventListener('visibilitychange', () => {
    window.__morayHidden = document.hidden;
    const live = document.getElementById('monitorLiveText');
    if (live && document.hidden) live.textContent = '后台已暂停';
  });
  // 原 5s 图表定时器感知隐藏状态：包一层 generateMonitorChart
  if (typeof generateMonitorChart === 'function' && !generateMonitorChart.__hiddenAware) {
    const orig = generateMonitorChart;
    window.generateMonitorChart = function () {
      if (window.__morayHidden) return;
      return orig.apply(this, arguments);
    };
    window.generateMonitorChart.__hiddenAware = true;
  }
  // Worker 闲置 60s 自动终止（下次使用自动重建）
  if (typeof HashVectorWorker !== 'undefined') {
    const origEmbed = HashVectorWorker.embedBatch.bind(HashVectorWorker);
    HashVectorWorker.embedBatch = async function (texts) {
      const r = await origEmbed(texts);
      clearTimeout(this._idleTimer);
      this._idleTimer = setTimeout(() => {
        if (this.worker) { this.worker.terminate(); this.worker = null; }
      }, 60000);
      return r;
    };
  }
}

/* ===================== [3.1] 对话体验 ===================== */

/**
 * 向消息容器追加带时间分隔线的消息节点（相邻消息间隔>5分钟时插入分隔线）
 * @param {HTMLElement} box - 消息容器
 * @param {Object[]} messages - 消息数组
 * @param {Function} buildEl - 消息构建器
 * @returns {void} */
function appendWithTimeSeparators(box, messages, buildEl) {
  let prevTs = 0;
  const frag = document.createDocumentFragment();
  messages.forEach(m => {
    if (prevTs && m.createdAt - prevTs > 5 * 60 * 1000) {
      const sep = document.createElement('div');
      sep.className = 'time-separator';
      sep.innerHTML = `<span>${new Date(m.createdAt).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>`;
      frag.appendChild(sep);
    }
    frag.appendChild(buildEl(m, false));
    prevTs = m.createdAt;
  });
  box.appendChild(frag);
}

/** 更新生成中的实时速度显示
 * @param {HTMLElement} el - 消息容器
 * @param {string} text - 状态文本
 * @returns {void} */
function updateGenStatus(el, text) {
  const header = el.querySelector('.gen-status-text');
  if (header) header.textContent = text;
}

/** 引用回复：把消息以引用格式填入输入框
 * @param {Object} msg - 被引用消息
 * @returns {void} */
function quoteReply(msg) {
  const input = document.getElementById('chatInputNormal');
  if (!input || !msg) return;
  const who = msg.role === 'user' ? '我' : shortModelName(msg.model);
  const quoted = (msg.content || '').split('\n').slice(0, 4).map(l => '> ' + l).join('\n');
  input.value = `> 引用 ${who}：\n${quoted}\n\n`;
  input.focus();
  if (typeof updateInputStats === 'function') updateInputStats(input.value);
}

/** 转发消息到新会话
 * @param {Object} msg - 消息
 * @returns {Promise<void>} */
async function forwardToNewConversation(msg) {
  if (!msg) return;
  const conv = await DB.createConversation({ title: '转发 · ' + (msg.content || '').slice(0, 12), model: msg.model || '' });
  const copy = Object.assign({}, msg, { id: uid('msg'), conversationId: conv.id });
  await DB.putMessage(copy);
  AppState.conversations.unshift(conv);
  AppState._convMsgIndex[conv.id] = [copy];
  renderSessionList();
  await openConversation(conv.id);
  showNotification('已转发', '消息已复制到新会话', 'success', 1800);
}

/** 切换消息收藏（星标）
 * @param {Object} msg - 消息
 * @param {HTMLElement} btn - 星标按钮
 * @returns {Promise<void>} */
async function toggleStarMessage(msg, btn) {
  if (!msg) return;
  msg.starred = !msg.starred;
  await persistMessage(msg);
  if (btn) btn.classList.toggle('active', !!msg.starred);
  showNotification(msg.starred ? '已收藏' : '已取消收藏', '', 'success', 1200);
}

/** 打开收藏消息列表面板（跨会话汇总）
 * @returns {Promise<void>} */
async function openStarredMessages() {
  const starred = [];
  for (const conv of AppState.conversations) {
    const msgs = (AppState._convMsgIndex && AppState._convMsgIndex[conv.id]) || [];
    msgs.forEach(m => { if (m.starred) starred.push({ conv, m }); });
  }
  if (!starred.length) { showNotification('暂无收藏', '点击 AI 消息操作栏的星标即可收藏', 'info', 2200); return; }
  const box = showModal(`<div class="space-y-2" style="max-height:60vh;overflow-y:auto">
    ${starred.map(({ conv, m }) => `
      <div class="chunk-item" data-star-jump="${conv.id}" style="cursor:pointer">
        <div class="flex items-center justify-between mb-1">
          <span class="text-brand-cyan truncate">${escapeHtml(conv.title)}</span>
          <span class="text-text-tertiary text-[10px]">${relTime(m.createdAt)}</span>
        </div>
        <div>${escapeHtml((m.content || '').slice(0, 120))}…</div>
      </div>`).join('')}
  </div>`, { title: '收藏的消息', icon: 'star', wide: true, footer: false });
  box.querySelectorAll('[data-star-jump]').forEach(el => {
    el.addEventListener('click', () => { closeModal(); openConversation(el.dataset.starJump); });
  });
}

/* ===================== [3.4] 可访问性 ===================== */

/** 自动为带 title 的可交互元素补 aria-label（事件委托友好的无障碍补全）
 * @returns {void} */
function enhanceAccessibility() {
  const apply = () => {
    document.querySelectorAll('button[title]:not([aria-label])').forEach(b => { b.setAttribute('aria-label', b.title); });
    document.querySelectorAll('.nav-icon-btn[data-tooltip]:not([aria-label])').forEach(b => { b.setAttribute('aria-label', b.dataset.tooltip); });
  };
  apply();
  // SPA 动态渲染：MutationObserver 低频补全
  if ('MutationObserver' in window) {
    const mo = new MutationObserver(debounce(apply, 300));
    mo.observe(document.body, { childList: true, subtree: true });
  }
}

/** 粘贴智能处理：粘贴代码自动开启代码模式
 * @param {ClipboardEvent} e - 粘贴事件
 * @returns {void} */
function handleSmartPaste(e) {
  const text = e.clipboardData && e.clipboardData.getData('text');
  if (!text || text.length < 200) return;
  // 图片粘贴另有处理；这里处理大段代码
  const lang = detectLanguage(text);
  if (lang && lang !== 'markdown') {
    // 仅在未开启代码模式时提示并开启（morayCodeMode 由第一阶段脚本维护）
    if (!window.morayCodeMode && typeof toggleCodeMode === 'function') {
      toggleCodeMode();
      showNotification('检测到代码粘贴', '已自动开启代码模式（语言：' + lang + '）', 'info', 2200);
    }
  }
}
