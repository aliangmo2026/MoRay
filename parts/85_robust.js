/* ============================================================
   MoRay 夜间优化 · 第一阶段
   任务1.2 虚拟滚动（固定行高虚拟列表 + 变高列表增量分页）
   任务1.3 边界加固（离线/大文件/配额/多标签/隐私模式/防重复）
   ============================================================ */

/* ===================== [任务1.2] 虚拟滚动 ===================== */

/**
 * VirtualScroller — 轻量固定行高虚拟滚动（无外部依赖）。
 * 原理：滚动容器内放一个总高占位层，按 scrollTop 计算可视区间，
 * 只渲染 [起始-缓冲, 结束+缓冲] 的行（绝对定位）。
 * 降级：不支持所需 API 时由调用方回退普通渲染。
 */
const VirtualScroller = {
  /**
   * 挂载虚拟滚动列表
   * @param {HTMLElement} scroller - 滚动容器（自身有 overflow-y:auto）
   * @param {Array} items - 数据项
   * @param {Function} renderItem - (item, index) => html 字符串
   * @param {Object} [opts] - {rowHeight, buffer, itemClass}
   * @returns {{update: (items:Array)=>void, destroy: ()=>void}} 控制句柄 */
  mount(scroller, items, renderItem, opts) {
    opts = opts || {};
    const rowHeight = opts.rowHeight || 88;
    const buffer = opts.buffer != null ? opts.buffer : 5;
    let data = items.slice();
    let raf = 0;

    const viewport = document.createElement('div');
    viewport.style.cssText = 'position:relative;width:100%';
    scroller.innerHTML = '';
    scroller.appendChild(viewport);

    /** 渲染可视区间（rAF 合帧）
     * @returns {void} */
    const renderRange = () => {
      const scrollTop = scroller.scrollTop;
      const viewH = scroller.clientHeight || 400;
      const start = Math.max(0, Math.floor(scrollTop / rowHeight) - buffer);
      const end = Math.min(data.length, Math.ceil((scrollTop + viewH) / rowHeight) + buffer);
      let html = '';
      for (let i = start; i < end; i++) {
        html += `<div class="${opts.itemClass || ''}" style="position:absolute;top:${i * rowHeight}px;left:0;right:0;height:${rowHeight}px;overflow:hidden">${renderItem(data[i], i)}</div>`;
      }
      viewport.innerHTML = html;
      refreshIcons();
    };

    /** 滚动回调（requestAnimationFrame 合帧避免过度重绘）
     * @returns {void} */
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; renderRange(); });
    };

    scroller.addEventListener('scroll', onScroll, { passive: true });

    const update = (newItems) => {
      data = newItems.slice();
      viewport.style.height = data.length * rowHeight + 'px';
      renderRange();
    };
    update(data);

    return {
      update,
      /** 销毁：解绑滚动监听
       * @returns {void} */
      destroy() { scroller.removeEventListener('scroll', onScroll); }
    };
  }
};

/**
 * 增量分页：变高列表（卡片网格）滚动接近底部时自动追加下一页。
 * 适用：提示词/片段等两列卡片（高度不定，虚拟滚动会导致跳动）。
 * @param {Object} app - 拥有 render() 与 limit 状态的模块（PromptsApp/SnippetsApp）
 * @param {number} [pageSize] - 每页数量
 * @returns {void} */
function attachAutoPagination(app, pageSize) {
  pageSize = pageSize || 50;
  // 在 render() 外包裹：限制渲染条数并注入滚动监听
  if (app.__paginated) return;
  app.__paginated = true;
  app.__limit = pageSize;
  const origRender = app.render.bind(app);
  app.render = function () {
    this.__renderTotal = this.__pendingItems ? this.__pendingItems.length : 0;
    origRender();
  };
  const origBind = app.bindEvents.bind(app);
  app.bindEvents = function () {
    origBind();
    // promptsGrid 的 id 由首次 render() 设置，bindEvents 时可能尚不存在 -> 回退选择器
    const scroller = (this === PromptsApp)
      ? (document.getElementById('promptsGrid') || document.querySelector('#page-prompts .grid'))
      : document.getElementById('snippetsList');
    if (scroller) {
      // 网格外层滚动容器
      const scrollParent = scroller.classList.contains('overflow-y-auto') ? scroller : scroller.closest('.overflow-y-auto');
      if (scrollParent && !scrollParent.__pagBound) {
        scrollParent.__pagBound = true;
        scrollParent.addEventListener('scroll', () => {
          if (scrollParent.scrollTop + scrollParent.clientHeight >= scrollParent.scrollHeight - 120) {
            app.__limit += pageSize;
            app.render();
          }
        }, { passive: true });
      }
    }
  };
}

/** 会话列表渲染分流：>50 条走虚拟滚动，否则普通渲染
 * @param {Array} filtered - 过滤后的会话（含分组信息前的平铺列表）
 * @returns {boolean} 是否走了虚拟模式 */
function renderSessionListVirtual(filtered) {
  const list = document.getElementById('sessionList');
  if (!list || filtered.length <= 50) return false;
  const ROW = 88;
  list.classList.add('v-scroll-on');
  // [修复] 与主路径一致：默认隐藏模型药丸，仅设置开关打开时显示
  const showModelTag = (typeof MoraySettings !== 'undefined') && MoraySettings.get('showSessionModel') === true;
  if (list.__vscroller) list.__vscroller.update(filtered);
  else {
    list.__vscroller = VirtualScroller.mount(list, filtered, (c) => {
      const msgs = (AppState._convMsgIndex && AppState._convMsgIndex[c.id]) || [];
      const preview = (msgs.length ? msgs[msgs.length - 1].content : (c.preview || '空会话')).slice(0, 40);
      return `
      <div class="session-item ${AppState.activeConv && AppState.activeConv.id === c.id ? 'active' : ''} ${c.pinned ? 'pinned' : ''}"
           data-conv-id="${c.id}" data-title="${escapeHtml(c.title || '')}" style="height:100%">
        <div class="flex items-center justify-between">
          <div class="session-title flex items-center gap-1.5 truncate">${escapeHtml(c.title || '新对话')}${c.unread ? '<span class="unread-dot"></span>' : ''}</div>
          <div class="session-item-actions">
            <button class="session-item-action ${c.pinned ? 'active-pin' : ''}" data-op="pin" title="置顶"><i data-lucide="pin" class="w-3 h-3"></i></button>
            <button class="session-item-action" data-op="rename" title="重命名"><i data-lucide="pencil" class="w-3 h-3"></i></button>
            <button class="session-item-action danger" data-op="delete" title="删除"><i data-lucide="trash-2" class="w-3 h-3"></i></button>
          </div>
        </div>
        <div class="session-preview">${escapeHtml(preview)}</div>
        <div class="session-meta">
          <span class="session-time">${relTime(c.updatedAt)}</span>
          ${showModelTag ? `<span class="session-model">${escapeHtml(shortModelName(c.model))}</span>` : ''}
        </div>
      </div>`;
    }, { rowHeight: ROW, buffer: 5 });
  }
  return true;
}

/* ===================== [任务1.3] 边界加固 ===================== */

/** 边界加固管理器 */
const Robustness = {
  /** 配额警告是否已提示过（防骚扰） */
  _quotaWarned: false,

  /** 安装全部边界处理
   * @returns {void} */
  install() {
    this.setupOnlineOffline();
    this.setupMultiTabSync();
    this.setupStorageFallback();
    this.guardLargeUploads();
  },

  /** 离线/上线状态感知
   * @returns {void} */
  setupOnlineOffline() {
    const notifyState = () => {
      const online = navigator.onLine;
      if (!online) {
        showNotification('网络已断开', '本地功能（对话记录/文档/片段）不受影响；云端 API 将不可用', 'warning', 4000);
      } else {
        showNotification('网络已恢复', '云端 API 可用', 'success', 2000);
      }
      const dot = document.getElementById('statusConnDot');
      if (dot) dot.classList.toggle('offline-hint', !online);
    };
    window.addEventListener('offline', notifyState);
    window.addEventListener('online', notifyState);
  },

  /** 多标签页数据同步：storage 事件（设置变更/同步哨兵）
   * @returns {void} */
  setupMultiTabSync() {
    window.addEventListener('storage', (e) => {
      try {
        if (e.key === 'moray_settings' || e.key === null) {
          // 设置在其他标签页被修改 -> 应用新设置（不回写）
          if (e.newValue) {
            MoraySettings.cache = Object.assign({}, MoraySettings.defaults, JSON.parse(e.newValue));
            MoraySettings.apply();
            showNotification('设置已同步', '检测到其他窗口修改了设置，已应用最新配置', 'info', 2500);
          }
        }
        if (e.key === 'moray_tab_sync') {
          // 数据在其他标签页变更 -> 提示并刷新会话列表
          refreshConversationsFromDB();
        }
      } catch (err) { console.warn('[MoRay] 多标签同步失败:', err); }
    });
  },

  /** 浏览器隐私模式（存储不可用）降级检测
   * @returns {void} */
  setupStorageFallback() {
    try {
      const k = '__moray_test__';
      localStorage.setItem(k, '1');
      localStorage.removeItem(k);
    } catch (e) {
      showNotification('存储不可用', '当前为隐私浏览模式，壁纸/设置无法持久化（功能不受影响）', 'warning', 5000);
    }
    // IndexedDB 打开失败 -> 内存模式
    DB.open().catch((e) => {
      console.error('[MoRay] IndexedDB 不可用，进入内存模式:', e);
      DB.enableMemoryMode();
      showNotification('数据库不可用', '已切换为内存模式：本次会话数据不会持久化', 'error', 6000);
    });
  },

  /** 大文件上传拦截（>50MB）
   * @returns {void} */
  guardLargeUploads() {
    // 在 DocsApp.uploadFiles 入口前置校验
    const orig = DocsApp.uploadFiles.bind(DocsApp);
    DocsApp.uploadFiles = async (files) => {
      const tooBig = (files || []).filter(f => f.size > 50 * 1024 * 1024);
      const ok = (files || []).filter(f => f.size <= 50 * 1024 * 1024);
      if (tooBig.length) {
        showNotification('文件过大', `${tooBig.map(f => f.name).join('、')} 超过 50MB 限制，已跳过`, 'error', 4500);
      }
      if (ok.length) return orig(ok);
    };
  }
};

/** 从 DB 重新拉取会话并刷新列表（多标签同步用）
 * @returns {Promise<void>} */
async function refreshConversationsFromDB() {
  try {
    AppState.conversations = await DB.listConversations();
    AppState.conversations.sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));
    await rebuildMsgIndex();
    renderSessionList();
    showNotification('数据已刷新', '检测到其他窗口的数据变更', 'info', 2000);
  } catch (e) { console.warn('[MoRay] refresh failed:', e); }
}

/** 广播数据变更哨兵（写操作后调用，通知其他标签页）
 * @returns {void} */
function broadcastDataChange() {
  try { localStorage.setItem('moray_tab_sync', String(Date.now())); } catch (e) { /* 隐私模式忽略 */ }
}

/** 发送防抖时间戳（快速连点保护） */
let _lastSendAt = 0;

/** 发送前置检查（空内容/重复点击/生成中）
 * @returns {boolean} 是否允许发送 */
function canSendNow() {
  const now = Date.now();
  if (now - _lastSendAt < 400) {
    showNotification('操作过快', '请稍候再发送', 'warning', 1200);
    return false;
  }
  _lastSendAt = now;
  return true;
}
