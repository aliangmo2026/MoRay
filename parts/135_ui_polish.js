/* ============================================================
   [阶段1.5] 全局 UI/UX 精修（纯界面层，不改功能与安全边界）
   - A. 通知系统重写：队列（同屏≤3）/分类时长/hover 暂停/图标+状态色/深色 scrim/手动关闭
   - B/G. 壁纸下浮层可读性 scrim 统一 / 焦点环 / 滚动条 / 过渡统一 / prefers-reduced-motion
   - C. 网格页自适应（≥1600 3~4 列 / 中屏 2~3 列 / 窄屏 1 列 + 内容区 max-width 居中）
   - D. 输入台分组分隔与窄屏横向滚动 / 本机工具 ON 态品牌描边+轻 glow
   - F. 计划卡/时间线/审批卡/文件树侧栏 的视觉精修（结构 JS 在 125_tools.js）
   说明：showNotification 原实现位于静态骨架第一阶段脚本（不可改区），此处以同名覆盖方式升级。
   ============================================================ */

/* ===================== A. 通知系统（覆盖实现） ===================== */

/** 通知运行时（队列/计时/暂停） */
const NotificationRuntime = {
  /** 同屏上限 @type {number} */
  MAX_ONSCREEN: 3,
  /** 类型默认时长 ms */
  DURATION: { success: 3500, info: 5000, warning: 8000, error: 10000 },
  /** 当前队列元素 @type {Array<HTMLElement>} */
  live: [],
  /** [阶段1.6 M1] 启动窗口：加载后 12s 内的启动类通知只允许一条（其余静默丢弃并
   * 把后端状态/⌘K 引导并入首条），根治静态骨架与各 boot 路径的启动通知连叠 */
  bootWindowUntil: Date.now() + 12000,
  bootShown: false,
  bootEl: null,

  /** 判定是否启动类通知（标题含"已就绪/一切就绪"或消息含命令面板引导/后端状态摘要）
   * @param {string} title - 标题
   * @param {string} message - 消息
   * @returns {boolean} */
  isBootLike(title, message) {
    const t = String(title || '');
    const m = String(message || '');
    return /已就绪|一切就绪/.test(t) || /⌘K|Ctrl\+K/.test(m) || /本地同步后端未启动|本地后端已连接/.test(m);
  },

  /** 启动类通知合并：首条正常显示（自动并入 ⌘K 引导），后续把后端状态并入首条后丢弃 */
  mergeBoot(title, message, type) {
    if (!this.bootShown) {
      this.bootShown = true;
      let m = String(message || '');
      if (!/⌘K|Ctrl\+K/.test(m)) m = (m ? m + ' · ' : '') + '按 ⌘K 打开命令面板';
      return { title, message: m, type };
    }
    // 已有首条：把新通知携带的后端状态并入首条 message 后丢弃
    if (this.bootEl && this.bootEl.isConnected) {
      const statusPart = /本地同步后端未启动|本地后端已连接/.test(String(message || '')) ? String(message) : '';
      if (statusPart) {
        const msgEl = this.bootEl.querySelector('.notification-message');
        if (msgEl && !/本地同步后端未启动|本地后端已连接/.test(msgEl.textContent || '')) {
          msgEl.textContent = statusPart;
        }
      }
    }
    return null;
  },

  /** 按类型取图标
   * @param {string} type - success|error|warning|info
   * @returns {string} lucide 图标名 */
  icon(type) {
    return { success: 'check-circle-2', error: 'x-circle', warning: 'alert-triangle', info: 'info' }[type] || 'info';
  },

  /** 队列超员时顶掉最旧的一条
   * @returns {void} */
  evictOldest() {
    while (this.live.length >= this.MAX_ONSCREEN) {
      const oldest = this.live.shift();
      if (oldest && oldest.isConnected) this.dismiss(oldest, true);
    }
  },

  /** 关闭一条通知（带退场动画）
   * @param {HTMLElement} el - 通知元素
   * @param {boolean} [instant] - 立即移除
   * @returns {void} */
  dismiss(el, instant) {
    if (!el || !el.isConnected) return;
    this.live = this.live.filter(x => x !== el);
    if (el._hideTimer) { clearTimeout(el._hideTimer); el._hideTimer = null; }
    if (instant) { el.remove(); return; }
    el.classList.add('closing');
    setTimeout(() => el.remove(), 220);
  }
};

/** 通知（覆盖静态骨架同名实现）：同屏≤3 条新顶旧、类型时长（成功 3.5s/信息 5s/警告与错误
 * 更久）、hover 暂停倒计时、类型图标+状态色、深色 scrim 保证壁纸下可读、右上安全边距。
 * @param {string} title - 标题
 * @param {string} [message] - 说明
 * @param {string} [type] - success|error|warning|info
 * @param {number} [duration] - 自定义时长 ms（0=不自动消失） */
window.showNotification = function (title, message, type = 'info', duration) {
  const container = document.getElementById('notificationContainer');
  if (!container) return;
  type = ['success', 'error', 'warning', 'info'].includes(type) ? type : 'info';
  // [阶段1.6 M1] 启动窗口合并：整个启动流程只出现一条启动类通知
  if (Date.now() < NotificationRuntime.bootWindowUntil &&
      NotificationRuntime.isBootLike(title, message) && (type === 'success' || type === 'info')) {
    const merged = NotificationRuntime.mergeBoot(title, message, type);
    if (!merged) return; // 后续启动类静默丢弃（状态已并入首条）
    title = merged.title;
    message = merged.message;
    type = merged.type;
  }
  const ms = (duration != null && duration > 0) ? duration : (NotificationRuntime.DURATION[type] || 5000);
  NotificationRuntime.evictOldest();

  const el = document.createElement('div');
  el.className = 'notification notification--polished type-' + type;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  el.innerHTML =
    '<div class="notification-icon ' + type + '"><i data-lucide="' + NotificationRuntime.icon(type) + '" class="w-4 h-4"></i></div>' +
    '<div class="notification-content">' +
    '<div class="notification-title">' + escapeHtml(String(title == null ? '' : title)) + '</div>' +
    (message ? '<div class="notification-message">' + escapeHtml(String(message)) + '</div>' : '') +
    '</div>' +
    '<button class="notification-close" aria-label="关闭通知" title="关闭"><i data-lucide="x" class="w-3 h-3"></i></button>' +
    '<div class="notification-progress" style="animation-duration:' + ms + 'ms"></div>';
  container.appendChild(el);
  refreshIcons();
  NotificationRuntime.live.push(el);
  // [阶段1.6 M1] 记录首条启动摘要（后续启动类通知的状态并入它）
  if (NotificationRuntime.bootShown && !NotificationRuntime.bootEl &&
      /已就绪/.test(String(title || ''))) {
    NotificationRuntime.bootEl = el;
  }

  // 手动关闭
  const closeBtn = el.querySelector('.notification-close');
  if (closeBtn) closeBtn.addEventListener('click', (e) => { e.stopPropagation(); NotificationRuntime.dismiss(el); });
  // hover 暂停倒计时（暂停进度条动画，移出后按剩余时间继续）
  let remaining = ms;
  let startedAt = Date.now();
  const armTimer = () => {
    el._hideTimer = setTimeout(() => NotificationRuntime.dismiss(el), remaining);
  };
  if (ms > 0) armTimer();
  el.addEventListener('mouseenter', () => {
    if (el._hideTimer) { clearTimeout(el._hideTimer); el._hideTimer = null; }
    remaining = Math.max(1000, remaining - (Date.now() - startedAt));
    el.classList.add('paused');
  });
  el.addEventListener('mouseleave', () => {
    startedAt = Date.now();
    el.classList.remove('paused');
    if (ms > 0) armTimer();
  });
};


/* ===================== [UI6] 输入台三层结构（模型行 / 输入区 / 工具行） ===================== */

/** UI6: 输入台重排为豆包式三层——模型选择行（独立首行）→ 输入区 → 底部工具行
 * （左=工具图标按钮，右=Token/字数统计 + 圆形发送按钮）。仅移动既有 DOM 节点
 * （事件监听随节点走，id/绑定原样保留）与改写 placeholder 属性；不新增/删除功能元素。
 * @returns {void} */
function installUi6Layout() {
  const core = document.getElementById('coreInputContainer');
  if (!core || core.__ui6Layout) return;
  const model = core.querySelector('.input-toolbar-model');
  const stats = core.querySelector(':scope > .input-stats');
  const toolbar = model && model.parentElement &&
    model.parentElement.classList.contains('input-toolbar') ? model.parentElement : null;
  const ta = document.getElementById('chatInputNormal');
  // 输入行 = #chatInputNormal 向上回溯到 core 的直接子行
  let inputRow = ta ? ta.parentElement : null;
  while (inputRow && inputRow.parentElement !== core) inputRow = inputRow.parentElement;
  if (!model || !stats || !toolbar || !ta || !inputRow || inputRow === core) return; // 结构不符放弃，保持原布局
  // 1) 模型选择行 → 独立首行
  core.insertBefore(model, core.firstChild);
  // 2) 圆形发送按钮（onclick=createRipple+sendChatMessage）从输入行移入工具行
  const send = inputRow.querySelector('button[onclick*="sendChatMessage"]');
  if (send) toolbar.appendChild(send);
  // 3) 统计（字/token）与 Enter 提示移入工具行右组（发送前）
  const statsLeft = stats.querySelector('.input-stats-left');
  const hint = stats.querySelector('.input-stats-hint');
  if (statsLeft) toolbar.insertBefore(statsLeft, send || null);
  if (hint) toolbar.insertBefore(hint, send || null);
  // 4) 工具行整体置于输入行之后（第三层）
  core.insertBefore(toolbar, stats);
  // 5) placeholder 缩短（默认态；骨架原长文案不改动）
  ta.setAttribute('placeholder', '输入消息，/ 呼出快捷指令');
  // 6) 纯 CSS 挂钩类
  inputRow.classList.add('ui6-input-row');
  if (send) send.classList.add('ui6-send');
  core.__ui6Layout = true;
}

/** 包装骨架 toggleCodeMode：关闭代码模式后把 placeholder 收回 UI6 短文案
 * （骨架原实现退出时会恢复超长默认文案；包装不改动其功能逻辑） */
(function ui6GuardCodeModePlaceholder() {
  const orig = window.toggleCodeMode;
  if (typeof orig !== 'function') return;
  window.toggleCodeMode = function () {
    const r = orig.apply(this, arguments);
    if (!window.morayCodeMode) {
      const ta = document.getElementById('chatInputNormal');
      if (ta && ta.getAttribute('placeholder') !== '输入消息，/ 呼出快捷指令') {
        ta.setAttribute('placeholder', '输入消息，/ 呼出快捷指令');
      }
    }
    return r;
  };
})();

/* 执行（最后分片，DOM 与骨架导出均已就绪；失败则下一个宏任务重试一次） */
(function ui6Go() {
  const core = document.getElementById('coreInputContainer');
  if (core && !core.__ui6Layout) {
    try { installUi6Layout(); } catch (e) { /* 结构未就绪 */ }
    if (!core.__ui6Layout) setTimeout(installUi6Layout, 0);
  }
})();

/* ===================== B/G/D/F/C. 全局精修样式（一次注入） ===================== */

(function injectUiPolishCss() {
  if (document.getElementById('uiPolishStyle')) return;
  const st = document.createElement('style');
  st.id = 'uiPolishStyle';
  st.textContent = `
/* ============================================================
   [视觉重构 v2] 暖棕极简（Claude 式安静质感）
   边界：纯样式层。不改 HTML 结构 / class / id / data-* / JS 逻辑。
   覆盖顺序：本 style 在 110 的 wallpaperThroughStyle 之后注入，同权重后定义者胜。
   ============================================================ */

/* ---------- 0) 设计令牌：深色默认（深空星尘）/ 浅色覆盖（月光） ---------- */
:root {
  /* 背景（从深到浅） */
  --bg-app: #0a0a0a;
  --bg-panel: #111111;
  --bg-card: #1a1a1a; /* [Day21d] #161616 → #1a1a1a：与 --bg-app 拉开可见层级（对标 zcode 卡片） */
  --bg-elevated: #1c1c1c;
  --bg-hover: rgba(255, 255, 255, 0.04);
  --bg-active: rgba(255, 255, 255, 0.08);
  --bg-code: #0c0c0c;
  /* 边框：极淡，克制 */
  --border: rgba(255, 255, 255, 0.08);
  --border-hover: rgba(255, 255, 255, 0.16);
  --border-subtle: rgba(255, 255, 255, 0.05);
  /* 文字层级 */
  --text-primary: #ededed;
  --text-secondary: #a3a3a3;
  --text-tertiary: #666666;
  --text-inverse: #0a0a0a;
  /* 强调色（极光蓝）：--brand 用于实底，--brand-text 用于深/浅底上的文字与图标。
     --glow-blue 是"光"的意象的唯一来源：现在只用于发送按钮 hover 的那一层极淡内发光，
     其余任何地方（尤其是输入框聚焦）都不要再做外发光。 */
  --brand: #E5734A;
  --brand-hover: #D9663B;
  --brand-light: rgba(229, 115, 74, 0.08);
  --brand-text: #E5734A;
  --brand-on: #ffffff;
  --glow-blue: rgba(229, 115, 74, 0.25);
  /* 功能色 */
  --success: #4ade80;
  --warning: #fbbf24;
  --danger: #ef4444;
  /* 阴影：克制的投影，无光晕
     （名字沿用 --ui-shadow-*：--shadow-* 是 Tailwind v4 的命名空间，重定义会和工具类互相覆盖） */
  --ui-shadow-sm: none;
  --ui-shadow-md: none;
  --ui-shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.5);
  /* 圆角语义别名（骨架 --radius-xs..2xl 保持不动，避免与 Tailwind rounded-* 取值分裂） */
  --radius-bubble: 8px;
  --radius-bubble-tail: 4px;
  --radius-input: 16px; /* [UI 2.0 步骤2] 6 → 16：输入台大圆角（与 --radius-card 观感对齐） */
  --radius-item: 6px;
  --radius-pop: 8px;
  --radius-btn: 12px; /* [UI 2.0] 6 → 12：按钮大圆角（与 00_theme --radius-control 对齐） */
  /* [A-3b] --space-1..8 已删：全站零消费方的死令牌 */
  /* 过渡：统一 200ms ease */
  --transition-fast: 200ms ease;
  --transition-normal: 200ms ease;
  --transition-slow: 200ms ease;
  /* 兼容别名：旧令牌名一律指向当前品牌色，任何漏改的引用都不会残留上一个主题的色相 */
  --brand-cobalt: var(--brand);
  --brand-cobalt-hover: var(--brand-hover);
  --brand-cyan: var(--brand-text);
  --brand-purple: var(--brand-text);
  --bg-app-dark: var(--bg-app);
  --bg-panel-dark: var(--bg-panel);
  --bg-card-dark: var(--bg-card);
  --border-dark: var(--border);
  --border-strong-dark: var(--border-hover);
  --bg-app-light: #f6f8fa;
  --bg-panel-light: #ffffff;
  --bg-card-light: #ffffff;
  --border-light: #d0d7de;
  --border-strong-light: #afb8c1;
  --border-subtle-light: #e6eaee;
  --text-primary-light: #1f2328;
  --text-secondary-light: #59636e;
  --text-tertiary-light: #8b949e;
  --ui-shadow-sm-light: 0 1px 2px rgba(31, 35, 40, 0.04);
  --ui-shadow-md-light: 0 4px 12px rgba(31, 35, 40, 0.08);
  --ui-shadow-lg-light: 0 12px 32px rgba(31, 35, 40, 0.12);
}
/* 浅色覆盖写在后（html[data-theme] 权重 (0,1,1) > :root 的 (0,1,0)，深色不会被抢回） */
html[data-theme="light"] {
  /* [UI 2.0] 浅色新体系：偏冷浅灰底 + 半透明白面板 + 极淡描边 + Apple 灰字 */
  --bg-app: #f4f5f7;
  --bg-panel: rgba(255, 255, 255, 0.7);
  --bg-card: rgba(255, 255, 255, 0.85);
  --bg-elevated: #ffffff;
  --bg-hover: #f0f3f6;
  --bg-active: #e6eaee;
  --bg-code: #f0f3f6;
  --border: rgba(0, 0, 0, 0.04);
  --border-hover: rgba(0, 0, 0, 0.10);
  --border-subtle: rgba(0, 0, 0, 0.03);
  --text-primary: #1d1d1f;
  --text-secondary: #86868b;
  --text-tertiary: #8b949e;
  --text-inverse: #ffffff;
  --brand: #E5734A;
  --brand-hover: #D9663B;
  --brand-light: rgba(229, 115, 74, 0.08);
  --brand-text: #D9663B;
  --brand-on: #ffffff;
  --glow-blue: rgba(229, 115, 74, 0.15);
  --success: #1a7f37;
  --warning: #9a6700;
  --danger: #cf222e;
  --ui-shadow-sm: var(--ui-shadow-sm-light);
  --ui-shadow-md: var(--ui-shadow-md-light);
  --ui-shadow-lg: var(--ui-shadow-lg-light);
}

/* ---------- 0b) 接管骨架 @theme 令牌：品牌色 / 表面 / 文字 / 投影 ----------
   必要性：骨架里有一批规则把 #E5734A、rgba(229, 115, 74,…) 直接写死在声明里，
   这些字面量不跟令牌走。这里是第一层（令牌级）替换，覆盖不到的字面量在第 10 / 14 / 15 节逐条接管。 */
:root, html[data-theme="dark"] {
  --color-surface-void: #0a0a0a;
  --color-surface-deep: #111111;
  --color-surface-panel: #111111;
  --color-surface-card: #161616;
  --color-surface-floating: #1c1c1c;
  --color-surface-hover: #202020;
  --color-line-ghost: rgba(255, 255, 255, 0.08);
  --color-text-primary: #ededed;
  --color-text-secondary: #a3a3a3;
  --color-text-tertiary: #666666;
  --color-code-bg: #0c0c0c;
  --color-brand-cobalt: #E5734A;
  --color-brand-cobalt-hover: #D9663B;
  --color-brand-violet: #E5734A;
  --color-brand-cyan: #E5734A;
  --color-danger: #ef4444;
  --color-success: #4ade80;
  --color-warning: #fbbf24;
  /* [A-3b] 骨架 @theme 的 shadow-glove / shadow-glow-* 光晕令牌已随源头退役删除，
     本节的对应归零/改写随之移除；唯一的微光仍是发送按钮 hover 的 --glow-blue 内发光。 */
  --shadow-card: none;
}
html[data-theme="light"] {
  /* [UI 2.0] 与 0 节浅色新体系同调（void/deep 随 --bg-app，面板/卡片半透明，线随 --border） */
  --color-surface-void: #f4f5f7;
  --color-surface-deep: #f4f5f7;
  --color-surface-panel: rgba(255, 255, 255, 0.7);
  --color-surface-card: rgba(255, 255, 255, 0.85);
  --color-surface-floating: #ffffff;
  --color-surface-hover: #f0f3f6;
  --color-line-ghost: rgba(0, 0, 0, 0.06);
  --color-text-primary: #1d1d1f;
  --color-text-secondary: #86868b;
  --color-text-tertiary: #8b949e;
  --color-code-bg: #f0f3f6;
  --color-brand-cobalt: #E5734A;
  --color-brand-cobalt-hover: #D9663B;
  --color-brand-violet: #E5734A;
  --color-brand-cyan: #E5734A;
  --color-danger: #cf222e;
  --color-success: #1a7f37;
  --color-warning: #9a6700;
  --shadow-card: 0 1px 2px rgba(31, 35, 40, 0.04);
}

/* ---------- 1) 全局背景与三栏 ---------- */
body { background-color: var(--bg-app); color: var(--text-primary); }
#app { background-color: var(--bg-app); }
/* 侧栏毛玻璃（浅色单独一套）：壁纸模式下 110 的档位规则权重更高，由 --wall-blur 统一到 20px
   [UI 2.0] 三栏全部毛玻璃化（对齐 WorkBuddy 原型三栏玻璃）：blur 走 --backdrop-blur 令牌（24px），
   无壁纸闸门——本段规则始终生效；中栏沿用 [A-1.5] 的 flex 定宽结构，仅底色从透明改为半透明白。
   内层卡片/气泡不加 blur（§9 已有 none 守卫，防嵌套掉帧） */
#app > aside.bg-surface-deep {
  background-color: rgba(17, 17, 17, 0.85);
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.15);
  backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.15);
  border-right-color: var(--border);
}
#middleSidebar {
  background-color: transparent;
  border-right-color: var(--border-subtle);
}
html[data-theme="light"] #app > aside.bg-surface-deep {
  background-color: rgba(255, 255, 255, 0.65);
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
  backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
  border-right-color: var(--border);
}
html[data-theme="light"] #middleSidebar {
  background-color: rgba(255, 255, 255, 0.5);
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
  backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
  border-right-color: var(--border-subtle);
}
/* [UI 2.0] 主内容区玻璃底（浅色）：main 的 padding box == 视口剩余区域，
   其内唯一的 position:fixed 元素 #wsSidebar 锚点偏移为 0（侦察已验），可安全加 blur */
html[data-theme="light"] main.bg-surface-void {
  background-color: rgba(255, 255, 255, 0.45);
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
  backdrop-filter: blur(var(--backdrop-blur, 20px)) saturate(1.2);
}
/* 壁纸模式：三栏模糊统一 20px（110 用 --wall-blur 消费，改这一个变量即可） */
#app[data-wallpaper] { --wall-blur: 20px; }

/* 主内容区居中：消息列与输入台同一中轴，768px 上限（Claude 式留白） */
/* 底距写全、不要退回 "padding: 24px 20px" 这种省略底值的三值简写（会让底距掉回 20px、视觉上"贴住"输入台）：
   消息列 28px + #chatNormalContent 的 py-5(20px) = 滚动到底时最后一条气泡与输入台之间 48px 呼吸位，
   落在 40-60px 目标区间。输入台是 #chat-normal 的 flex 兄弟、不是浮层（见骨架 chatNormalContent 收尾注释），
   所以无需为它预留整台高度；110 里那条 padding-bottom:96px 是浮层时代的旧值，本轮已清掉。 */
#chatMessagesNormal {
  max-width: 768px; margin-left: auto; margin-right: auto;
  padding: 24px 20px 28px;
}
#chatNormalContent .welcome-screen { max-width: 768px; margin-left: auto; margin-right: auto; }
@media (max-width: 640px) {
  #chatMessagesNormal { padding: 16px 4px 24px; }
}

/* 网格页内容区居中（沿用原上限） */
#page-prompts .flex-1 > div, #page-snippets .flex-1 > div, #page-docs .flex-1 > div {
  max-width: 1560px; margin-left: auto; margin-right: auto; width: 100%;
}

/* ---------- 2) 侧栏与会话列表 ---------- */
.session-item {
  padding: 12px 12px; border-radius: var(--radius-item); margin-bottom: 2px;
  transition: background-color var(--transition-fast), box-shadow var(--transition-fast);
}
.session-item:hover { background-color: var(--bg-hover); }
/* 激活项：整块浅底 + 内描边。骨架原用 border-left:2px + padding-left:10px 实现，
   会在切换会话时让同一项的文字横向跳动 2px，故改为不占位的内描边。 */
.session-item.active {
  background-color: var(--brand-light);
  border-left: none;
  padding-left: 12px;
  box-shadow: inset 2px 0 0 var(--brand);
}
.session-item .session-title { color: var(--text-secondary); font-weight: 500; }
.session-item.active .session-title { color: var(--text-primary); }
.session-item .session-preview { color: var(--text-tertiary); }
.session-item .session-time { color: var(--text-tertiary); }
.session-item .session-model { background-color: var(--brand-light); color: var(--brand-text); }
.session-item.pinned { border: 1px solid var(--border-hover); }
.session-item-action { width: 22px; height: 22px; border-radius: 6px; color: var(--text-tertiary); }
.session-item-action:hover { background-color: var(--bg-active); color: var(--text-primary); }
.session-item-action.danger:hover { color: var(--danger); }
.session-item-action.active-pin { color: var(--warning); }
.session-item.dragging { opacity: 0.4; }
.session-item.drop-above { box-shadow: inset 0 2px 0 var(--brand); }
.session-item.drop-below { box-shadow: inset 0 -2px 0 var(--brand); }
.unread-dot { background-color: var(--brand); }
.session-group-chip.active { background-color: var(--brand-light); color: var(--brand-text); }
.session-group-chip:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.session-search-input:focus { border-color: var(--brand) !important; box-shadow: none !important; }
.session-checkbox.checked { background-color: var(--brand); border-color: var(--brand); }
.batch-actions-bar { background-color: var(--bg-elevated); border-color: var(--border); }
.session-item-group-tag { background-color: var(--bg-hover); color: var(--text-tertiary); }

/* 新建对话主按钮：去渐变去光晕 */
#newChatMainBtn.new-chat-main-btn {
  background-image: none; background-color: var(--brand); color: var(--brand-on);
  box-shadow: none;
}
#newChatMainBtn.new-chat-main-btn:hover {
  background-color: var(--brand-hover); box-shadow: none; transform: none;
}

/* ---------- 3) 消息（Claude 式：AI 纯文本流 + 用户浅底小卡片） ---------- */
/* 真实结构：.msg-bubble > div.max-w-[75%|85%] > [元信息行] + .msg-bubble__user-box|__ai-box
   宽度约束必须改在 **外层 div** 上：内层盒子再宽也被 75%/85% 的 wrapper 卡住。
   AI 消息去气泡：底色/描边/阴影/圆角全部归零，只留垂直留白，做成文档式文本流。
   !important 是必需的：骨架在 .msg-bubble__ai-box 上内联了 bg-surface-card / border-line-ghost/60，
   且 110 在四款图片壁纸作用域给外层 div 挂了高权重的底色覆盖（见 110 第 1713-1746 行）。 */
#chatMessagesNormal .msg-bubble.flex.justify-end > div { max-width: 70% !important; }
/* AI 侧只给 max-width 不够：.msg-bubble 本身是 flex 容器，wrapper 作为 flex 项是"收缩到内容宽"的，
   max-width 只封顶不拉伸，正文会缩成窄条而不是撑满消息列 —— 必须显式给 width:100%。
   [Day22a] 选择器由 "> div" 收窄为 "> .msg-ai-body"：AI 气泡现在有两个直接子级
   （24px .msg-avatar + 正文 wrapper），"> div" 会把头像也撑成 100% 宽（avatar 的 24px 与
   本节 width:100%!important 冲突时 !important 胜出，头像会被拉成一条通栏）。 */
#chatMessagesNormal .msg-bubble.flex.justify-start > .msg-ai-body { max-width: 100% !important; width: 100%; min-width: 0; }
#chatMessagesNormal .msg-bubble__ai-box {
  background: transparent !important;
  background-image: none !important;
  border: none !important;
  border-radius: 0 !important;
  padding: 8px 0 !important;
  max-width: 100% !important;
  box-shadow: none !important;
  -webkit-backdrop-filter: none !important;
  backdrop-filter: none !important;
  color: var(--text-primary) !important;
  transition: none !important;
}
#chatMessagesNormal .msg-bubble__ai-box:hover { border-color: transparent !important; box-shadow: none !important; }
/* 正文行高：必须落在 .md-body 上 —— 它自己有 line-height:1.7，继承不到的父级 1.75 不会生效 */
#chatMessagesNormal .msg-bubble__ai-box .md-body { line-height: 1.75; }
/* 用户消息：由实色品牌蓝降级为浅底细边小卡片（白字同步换成主文字色，否则浅底白字会看不见） */
#chatMessagesNormal .msg-bubble__user-box {
  background: var(--brand-light) !important;
  background-image: none !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius-bubble) !important;
  padding: 12px 16px !important;
  box-shadow: none !important;
  -webkit-backdrop-filter: none !important;
  backdrop-filter: none !important;
  color: var(--text-primary) !important;
  transition: none !important;
}
#chatMessagesNormal .msg-bubble__user-box .md-body,
#chatMessagesNormal .msg-bubble__user-box .md-body p,
#chatMessagesNormal .msg-bubble__user-box .md-body li { color: var(--text-primary) !important; }
#chatMessagesNormal .msg-bubble__user-box .md-body a { color: var(--brand-text) !important; border-bottom-color: var(--border-hover) !important; }
/* 消息之间的垂直间距（与容器 space-y-4 的 16px 相邻外边距折叠，取大值 24px） */
#chatMessagesNormal .msg-bubble { margin-bottom: 24px; }
/* 元信息行（"你 · 时间" / 模型名 · 时间）与统计行 */
#chatMessagesNormal .msg-bubble .text-text-tertiary { color: var(--text-tertiary); }
#chatMessagesNormal .msg-bubble .beacon-dot { background-color: var(--brand); }
/* 正文排版：行高 1.7、段落间距 16px */
.md-body { line-height: 1.7; }
.md-body p { margin: 16px 0; }
.md-body h1, .md-body h2, .md-body h3, .md-body h4 { margin: 20px 0 12px; }
.md-body ul, .md-body ol { margin: 12px 0; }
.md-body li { margin: 4px 0; }
.md-body blockquote {
  margin: 12px 0; padding: 8px 16px;
  border-left: 3px solid var(--border-hover); background: var(--bg-hover);
  color: var(--text-secondary); border-radius: 0 var(--radius-xs) var(--radius-xs) 0;
}
.md-body a { color: var(--brand-text); border-bottom: 1px dashed var(--border-hover); }
.md-body a:hover { color: var(--brand-hover); border-bottom-color: var(--brand-hover); }
.md-body code:not(.hljs) {
  color: var(--text-primary); background: var(--bg-code); border-radius: 4px;
}
/* 气泡内代码块：去深黑罩、去磨砂 */
#chatMessagesNormal .msg-bubble pre,
#chatMessagesNormal .msg-bubble code {
  background: var(--bg-code) !important; color: var(--text-primary) !important; border-radius: 6px !important;
}
#chatMessagesNormal .msg-bubble .code-block { background: var(--bg-code); border: 1px solid var(--border); border-radius: var(--radius-btn); }
#chatMessagesNormal .msg-bubble .code-header { background: var(--bg-hover); border-bottom: 1px solid var(--border); }
#chatMessagesNormal .msg-bubble .code-header .code-dots span { opacity: 0.5; }
.code-copy-btn { opacity: 0.85; transition: opacity var(--transition-fast), color var(--transition-fast); }
.code-block:hover .code-copy-btn { opacity: 1; }
/* 打字指示器 */
.typing-dot { background: var(--brand); }
.typing-cursor { background: var(--brand); }
/* 思考面板 */
.thinking-indicator { background: var(--bg-hover); color: var(--text-secondary); }
.thinking-panel, .thinking-content { background: var(--bg-hover); border-color: var(--border); }
.thinking-header { background: var(--bg-hover); }
.thinking-header:hover { background: var(--bg-active); }
.thinking-title { color: var(--text-secondary); }
.thinking-step-icon { color: var(--brand-text); }
/* 消息操作按钮 */
.msg-action-btn.active { background: var(--brand-light); color: var(--brand-text); }

/* 对比模式两列气泡（另一套渲染路径，没有 .msg-bubble__* 类） */
#compareColumns .col-messages > div.flex.justify-end > div {
  background-image: none; background-color: var(--brand);
  border-color: var(--brand);
  border-radius: var(--radius-bubble) var(--radius-bubble-tail) var(--radius-bubble) var(--radius-bubble);
  box-shadow: none;
}
#compareColumns .col-messages > div.flex.justify-end > div > p,
#compareColumns .col-messages > div.flex.justify-end > div .md-body { color: #ffffff; }
#compareColumns .col-messages > div.flex.justify-end > div a { color: #ffffff; border-bottom-color: rgba(255, 255, 255, 0.5); }
#compareColumns .col-messages > div.flex.justify-start > div {
  background-color: var(--bg-card);
  border-radius: var(--radius-bubble) var(--radius-bubble) var(--radius-bubble) var(--radius-bubble-tail);
  border-color: var(--border);
  box-shadow: var(--ui-shadow-sm);
}

/* ---------- 4) 输入台（大圆角、无渐变、无光晕） ---------- */
#coreInputContainer,
.core-input {
  border-radius: var(--radius-input);
  border: 1px solid var(--border);
  /* !important：壁纸模式下 110 给输入台挂了一圈 2px 半透明蓝环 + 10px 投影，这里压平成纯边框 */
  box-shadow: none !important;
  background-color: var(--bg-card);
  -webkit-backdrop-filter: blur(16px) saturate(1.1);
  backdrop-filter: blur(16px) saturate(1.1);
  transition: border-color 200ms ease;
}
/* 聚焦 = 只换 1px 边框色，不做任何外发光（!important：壁纸模式下 110 自带一条带 30px 光晕的同名规则） */
#app[data-wallpaper] #coreInputContainer:focus-within,
#app #coreInputContainer:focus-within,
#coreInputContainer:focus-within,
#app .core-input:focus-within,
.core-input:focus-within {
  border-color: var(--brand) !important;
  box-shadow: none !important;
}
/* 对比模式输入框同样只换边框色（它被排除在通用输入规则之外） */
#compareInput:focus {
  border-color: var(--brand) !important;
  box-shadow: none !important;
  outline: none;
}
.core-input.code-mode-active {
  border-color: var(--brand-text);
  box-shadow: none !important;
}
#chatInputNormal { color: var(--text-primary); }
#chatInputNormal::placeholder { color: var(--text-tertiary); }
/* 输入台居中，与消息列同一中轴：768 - 20*2 = 728 */
#coreInputContainer { max-width: calc(768px - 40px); margin-left: auto; margin-right: auto; }
/* 三层结构（UI6）间距与分隔 */
#coreInputContainer > .input-toolbar-model { margin: 4px 12px 0; padding: 2px 8px; }
#coreInputContainer > .ui6-input-row { padding: 2px 12px 8px; }
#coreInputContainer > .input-toolbar {
  padding: 4px 12px 12px;
  border-top: 1px solid var(--border-subtle);
  margin-top: 2px;
}
#coreInputContainer > .input-toolbar .input-stats-left { margin-left: auto; gap: 12px; font-size: 12px; color: var(--text-tertiary); }
#coreInputContainer > .input-toolbar .input-stats-left .value { color: var(--text-secondary); }
#coreInputContainer > .input-toolbar .input-stats-hint { margin-left: 8px; font-size: 12px; color: var(--text-tertiary); opacity: 0.72; }
#coreInputContainer > .input-toolbar .code-mode-badge { margin-left: 4px; }
#coreInputContainer > .input-stats { display: none; }
/* 工具按钮：hover 只变底色，不位移不发光 */
.input-toolbar { overflow-x: auto; overflow-y: hidden; scrollbar-width: none; flex-wrap: nowrap; }
.input-toolbar::-webkit-scrollbar { display: none; }
.input-toolbar > * { flex-shrink: 0; }
.input-toolbar-btn { color: var(--text-tertiary); }
.input-toolbar-btn:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.input-toolbar-btn.active, .input-toolbar-btn.active:hover { background-color: var(--brand-light); color: var(--brand-text); }
.input-toolbar-model:hover { background-color: var(--bg-hover); }
.input-toolbar-model span { color: var(--text-secondary); }
.input-toolbar-divider { background-color: var(--border-subtle); }
.core-mode-badge, .code-mode-badge { background-color: var(--brand-light); color: var(--brand-text); }
/* 发送按钮：品牌实底圆形，去渐变去光晕，不位移 */
#coreInputContainer > .input-toolbar .ui6-send {
  margin-left: 4px; flex-shrink: 0;
  background-image: none; background-color: var(--brand);
  box-shadow: none !important;
  color: var(--brand-on);
  transition: background-color var(--transition-fast), transform var(--transition-fast);
}
#coreInputContainer > .input-toolbar .ui6-send:hover {
  background-image: none; background-color: var(--brand-hover);
  box-shadow: inset 0 0 8px var(--glow-blue) !important; transform: none;
}
#coreInputContainer > .input-toolbar .ui6-send:active {
  background-color: var(--brand); box-shadow: none !important;
  transform: scale(0.96); filter: brightness(0.85);
}
@media (max-width: 640px) {
  #coreInputContainer .input-stats-hint { display: none; }
  #coreInputContainer > .input-toolbar { flex-wrap: wrap; row-gap: 8px; }
}
/* 本机 Agent 开关 */
.native-agent-btn.native-on { box-shadow: inset 0 0 0 1px var(--border-hover); }
.native-agent-btn:not(.native-on) { opacity: 0.62; }
.native-agent-btn:not(.native-on):hover { opacity: 1; }
.native-agent-btn.native-offline { opacity: 0.38; cursor: not-allowed; filter: grayscale(0.4); }
.native-agent-btn.native-offline:hover { opacity: 0.45; }
.native-agent-btn.native-on-pulse { animation: nativePulse 0.9s ease-out 2; }
@keyframes nativePulse {
  0%, 100% { box-shadow: inset 0 0 0 1px var(--border-hover); }
  50% { box-shadow: inset 0 0 0 1px var(--brand); }
}

/* ---------- 5) 浮层：下拉 / 弹窗 / 命令面板 / 右键菜单 / 通知（保留毛玻璃） ---------- */
.model-popover {
  z-index: 9999;
  background-color: rgba(22, 22, 22, 0.86);
  -webkit-backdrop-filter: blur(20px) saturate(1.2);
  backdrop-filter: blur(20px) saturate(1.2);
  border: 1px solid var(--border);
  border-radius: var(--radius-pop);
  box-shadow: var(--ui-shadow-lg);
  padding: 8px;
}
html[data-theme="light"] .model-popover {
  background-color: rgba(255, 255, 255, 0.88);
  border-color: var(--border);
}
.model-pop-item { border-radius: var(--radius-btn); color: var(--text-secondary); }
.model-pop-item:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.model-pop-item.selected { background-color: var(--brand-light); color: var(--brand-text); }
.modal-box, .confirm-box, .command-palette, .context-menu, .surface-floating, .slash-menu {
  background-color: rgba(22, 22, 22, 0.90);
  -webkit-backdrop-filter: blur(20px) saturate(1.2);
  backdrop-filter: blur(20px) saturate(1.2);
  border: 1px solid var(--border);
  color: var(--text-primary);
}
html[data-theme="light"] .modal-box, html[data-theme="light"] .confirm-box,
html[data-theme="light"] .command-palette, html[data-theme="light"] .context-menu,
html[data-theme="light"] .surface-floating, html[data-theme="light"] .slash-menu {
  background-color: rgba(255, 255, 255, 0.92);
}
.modal-overlay, .confirm-overlay { background: rgba(1, 4, 9, 0.60); -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px); }
html[data-theme="light"] .modal-overlay, html[data-theme="light"] .confirm-overlay { background: rgba(31, 35, 40, 0.28); }
.command-item:hover, .command-item.selected, .context-menu-item:hover, .slash-menu-item:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.slash-menu-item.selected { background-color: var(--brand-light); color: var(--brand-text); }
.slash-menu-header { color: var(--text-tertiary); border-bottom-color: var(--border-subtle); }
.command-item-shortcut kbd, .shortcut-keys kbd {
  background: var(--bg-hover); border: 1px solid var(--border); color: var(--text-secondary);
}
#newChatMoreMenu .command-item:hover { background-color: var(--bg-hover); }
/* 通知：去渐变与 14px 磨砂 → 实色卡 + 保留左侧状态色语义 */
.notification-container { top: 16px; right: 16px; max-width: min(400px, calc(100vw - 32px)); }
.notification--polished {
  position: relative; overflow: hidden;
  background: rgba(22, 22, 22, 0.92);
  -webkit-backdrop-filter: blur(20px) saturate(1.2); backdrop-filter: blur(20px) saturate(1.2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--text-tertiary);
  border-radius: var(--radius-pop); box-shadow: var(--ui-shadow-lg);
  padding-right: 16px;
}
html[data-theme="light"] .notification--polished {
  background: rgba(255, 255, 255, 0.94);
  border-color: var(--border);
  box-shadow: var(--ui-shadow-lg-light);
}
.notification--polished.type-success { border-left-color: var(--success); }
.notification--polished.type-error   { border-left-color: var(--danger); }
.notification--polished.type-warning { border-left-color: var(--warning); }
.notification--polished.type-info    { border-left-color: var(--brand); }
.notification--polished .notification-icon.success { color: var(--success); }
.notification--polished .notification-icon.error   { color: var(--danger); }
.notification--polished .notification-icon.warning { color: var(--warning); }
.notification--polished .notification-icon.info    { color: var(--brand-text); }
.notification--polished .notification-title { color: var(--text-primary); }
.notification--polished .notification-message { color: var(--text-secondary); }
.notification--polished .notification-close {
  background: transparent; border: 0; color: var(--text-tertiary);
  cursor: pointer; padding: 4px; border-radius: 6px; line-height: 0;
}
.notification--polished .notification-close:hover { color: var(--text-primary); background: var(--bg-hover); }
.notification--polished .notification-progress {
  position: absolute; left: 0; bottom: 0; height: 2px; width: 100%;
  background: var(--brand); opacity: 0.55;
  animation-name: notif-progress; animation-timing-function: linear; animation-fill-mode: forwards;
}
.notification--polished.paused .notification-progress { animation-play-state: paused; }
@keyframes notif-progress { from { width: 100%; } to { width: 0%; } }
.notification--polished.closing { opacity: 0; transform: translateX(16px); transition: opacity 200ms ease-out, transform 200ms ease-out; }
.notification { background-color: rgba(22, 22, 22, 0.92); border-color: var(--border); box-shadow: var(--ui-shadow-lg); }
html[data-theme="light"] .notification { background-color: rgba(255, 255, 255, 0.94); }

/* ---------- 6) 按钮与交互态 ---------- */
/* 主按钮：品牌实底，无渐变无光晕，hover 只换底色
   [A-3b] box-shadow/transform 中和已随骨架 12/20px 晕源头删除而移除 */
.btn-primary {
  background: var(--brand); background-image: none; color: var(--brand-on);
}
.btn-primary:hover { background-color: var(--brand-hover); background-image: none; }
.btn-ghost { background: transparent; color: var(--text-secondary); }
.btn-ghost:hover { background-color: var(--bg-hover); color: var(--text-primary); border-color: var(--border-hover); }
.tool-btn { color: var(--text-secondary); }
.tool-btn:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.tool-btn.active { background-color: var(--bg-hover); color: var(--brand-text); }
.tool-btn.primary { background: var(--brand); color: var(--brand-on); }
.tool-btn.primary:hover { background-color: var(--brand-hover); color: var(--brand-on); }
.tool-btn.secondary { border-color: var(--border); color: var(--text-secondary); }
.tool-btn.secondary:hover { background-color: var(--bg-hover); color: var(--text-primary); border-color: var(--border-hover); }
.tool-btn.danger { color: var(--danger); }
.tool-btn.danger:hover { background-color: var(--bg-hover); color: var(--danger); }
/* v41 修复：图标化 .tool-btn 在 border-box 下被 padding 挤成 0 宽
   （骨架 :352 的 padding:8px 16px 未被骨架 :1313 的 32×32 图标钮定义重置，
     左右 padding 吃光 border-box → 内容盒 0 → lucide 16px 图标 flex 压缩到 0 宽不可见。
     作用域精确：只清顶栏/中栏图标钮的 padding，消息区 .tool-btn-row 文字按钮不受影响） */
#page-chat header .tool-btn,
#sidebar-chat .tool-btn { padding: 0; }
/* 顺带恢复 .tool-btn-row 里文字按钮的尺寸语义（它本就不是图标钮） */
.tool-btn-row .tool-btn {
  width: auto;
  height: auto;
  padding: 8px 16px;  /* 保留原文字按钮 padding */
}
/* 过渡与状态：统一 200ms ease，hover 不做位移，按下只做轻微缩放 */
button, a, [role="button"], .toggle-track, input, select, textarea {
  transition: background-color var(--transition-fast), border-color var(--transition-fast),
              color var(--transition-fast), box-shadow var(--transition-fast),
              opacity var(--transition-fast), transform var(--transition-fast);
}
button:not(:disabled):hover, [role="button"]:not([aria-disabled="true"]):hover { transform: none; }
button:not(:disabled):active, .btn:not(:disabled):active, [role="button"]:not(:disabled):active {
  transform: scale(0.97); filter: none;
}
button:disabled, .btn:disabled, [role="button"][aria-disabled="true"] { opacity: 0.38; cursor: not-allowed; filter: grayscale(0.35); }
.toggle-track.active { background-color: var(--brand); }
/* 分段控制器 */
.mode-segment { background-color: var(--bg-panel); border-color: var(--border); }
.mode-segment button { color: var(--text-tertiary); }
.mode-segment button.active { background-color: var(--bg-card); color: var(--text-primary); box-shadow: var(--ui-shadow-sm); }
/* 导航图标条（A-1 宽栏行式）：淡底 + 左缘内嵌指示条 */
.nav-icon-btn { transition: background-color var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast); }
.nav-icon-btn::before {
  content: ''; position: absolute; left: 0; top: 50%;
  transform: translateY(-50%) scaleY(0);
  width: 3px; height: 16px; border-radius: 0 2px 2px 0;
  background: var(--brand);
  transition: transform var(--transition-normal);
}
.nav-icon-btn:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.nav-icon-btn.active { background-color: var(--brand-light); color: var(--brand-text); }
.nav-icon-btn.active::before { transform: translateY(-50%) scaleY(1); background: var(--brand); }
/* 列表项（设置页子导航 / 提示词分类 / 片段语言 / 文档侧栏共用） */
.list-item { color: var(--text-secondary); }
.list-item:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.list-item.active { background-color: var(--brand-light); color: var(--brand-text); border-left: 2px solid var(--brand); }
html[data-theme="light"] .list-item.active { background: var(--brand-light); color: var(--text-primary); }
html[data-theme="light"] .list-item:hover { background: var(--bg-hover); color: var(--text-primary); }
/* 表单控件：实色底 + 统一描边与聚焦环 */
:is(input:not([type="checkbox"], [type="radio"], [type="range"], [type="file"]), textarea, select):where(:not(#chatInputNormal, #compareInput)) {
  background-color: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm, 8px);
  color: var(--text-primary);
}
:is(input:not([type="checkbox"], [type="radio"], [type="range"], [type="file"]), textarea, select):where(:not(#chatInputNormal, #compareInput)):focus {
  border-color: var(--brand) !important;
  box-shadow: none !important;
  outline: none;
}
.form-input:focus, .form-textarea:focus, .form-select:focus,
.tool-input-area:focus, .group-modal-add input:focus {
  border-color: var(--brand) !important; box-shadow: none !important;
}

/* ---------- 7) 焦点环 / 滚动条 / 选中文本 ---------- */
/* 键盘焦点环只保留给按钮/链接/自定义控件；输入类元素一律 outline: none，
   聚焦反馈由上面那条 1px 边框色规则承担（点输入框不会再冒出一个蓝圈）。 */
button:focus-visible, [tabindex]:focus-visible, a:focus-visible {
  outline: 2px solid var(--brand-text); outline-offset: 2px; border-radius: 6px;
}
input:focus-visible, textarea:focus-visible, select:focus-visible { outline: none; }
::selection { background: var(--brand-light); color: var(--text-primary); }
* { scrollbar-width: thin; scrollbar-color: var(--border-hover) transparent; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: var(--border-hover); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-tertiary); }
::-webkit-scrollbar-track { background: transparent; }

/* ---------- 8) 装饰元素归零：彩色渐变 / 旋转光线 / 呼吸光带 ---------- */
.gradient-text {
  background: none !important;
  -webkit-background-clip: border-box !important;
  background-clip: border-box !important;
  -webkit-text-fill-color: var(--brand-text) !important;
  color: var(--brand-text) !important;
}
.welcome-logo {
  background-image: none; background-color: var(--bg-card);
  border: 1px solid var(--border); border-radius: 8px; /* [A-1] 18px→8px 圆角归一 */
}
.welcome-logo::before { display: none; }
.welcome-screen .welcome-logo::after { display: none; }
aside .w-9.h-9.rounded-lg:hover { transform: none; filter: none; }
.aurora-edge { display: none; }
/* [A-1] .bring-ripple 规则已删（涟漪随降噪移除，骨架空壳 createRipple 不再产生节点） */
.beacon-dot { background-color: var(--brand); box-shadow: none; }
.roadmap-timeline::before, .roadmap-item::before { background: var(--border); }
.roadmap-item.current::before { background: var(--brand); }
.roadmap-status.current { color: var(--brand-text); }
.progress-ring-fill { stroke: var(--brand); }
.speed-bar-fill.cobalt { background: var(--brand); }
.speed-bar-fill.violet { background: var(--brand-text); }
.monitor-bar { background: var(--brand); }
.running-workflow-progress-fill { background: var(--brand); }
.running-step.active { color: var(--brand-text); }
.workflow-run-btn { background: var(--brand); color: var(--brand-on); }
.workflow-run-btn:hover { background: var(--brand-hover); }
.workflow-card::before { background: var(--brand); }
.regex-flag.active { background: var(--brand-light); color: var(--brand-text); }
.regex-match { background: var(--brand-light); }
.auto-action-chip:hover { background: var(--bg-hover); border-color: var(--border-hover); }
.drop-overlay { background: var(--bg-hover); }
.drop-overlay.active, .drop-overlay-icon { color: var(--brand-text); }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--brand); background: var(--brand-light); }
.hover-lift:hover { transform: none; border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); }
.workflow-card:hover, .doc-card:hover { transform: none; border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); }
.scroll-bottom-btn:hover { color: var(--brand-text); border-color: var(--brand); background-color: var(--bg-hover); }
.code-expand-bar button:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.input-glow:focus { border-color: var(--brand) !important; } /* [A-3b] box-shadow 中和已随骨架 28px 晕源头删除而移除 */
/* 图标底色：骨架与运行时都用**内联 style** 写死亮蓝 #5B8CFF / 亮紫 #9D7BFF / 亮青 #3AD6E8
   （内联非 !important 值可被 CSS !important 覆盖），按属性子串精确接管为暖棕；
   绿 #3FD68F / 黄 #F2B24C / 红 #FF5C6C 属语义色，保留不接管。 */
.welcome-suggestion-icon[style*="#5B8CFF"], .welcome-suggestion-icon[style*="#9D7BFF"], .welcome-suggestion-icon[style*="#3AD6E8"],
.slash-menu-item-icon[style*="#5B8CFF"], .slash-menu-item-icon[style*="#9D7BFF"], .slash-menu-item-icon[style*="#3AD6E8"],
.command-item-icon[style*="#5B8CFF"], .command-item-icon[style*="#9D7BFF"], .command-item-icon[style*="#3AD6E8"],
.workflow-icon[style*="#5B8CFF"], .workflow-icon[style*="#9D7BFF"], .workflow-icon[style*="#3AD6E8"],
.welcome-suggestion-icon[style*="91,140,255"], .slash-menu-item-icon[style*="91,140,255"], .workflow-icon[style*="91,140,255"] {
  background-color: var(--brand-light) !important;
  color: var(--brand-text) !important;
}
.empty-state-icon, .empty-art .empty-icon, .thinking-step-icon {
  background-color: var(--brand-light) !important;
  color: var(--brand-text) !important;
}

/* ---------- 9) 卡片 / 空状态 / 时间轴 / 飞行记录仪 ---------- */
.glass-card, .doc-card, .workflow-card, .timestamp-card, .model-detail-card, .error-card,
.card, .tl-card, .prompt-card, .snippet-card {
  border-radius: var(--radius-card, 8px);
  box-shadow: var(--ui-shadow-sm);
}
/* 去磨砂：卡片与气泡一律纯色（侧栏与浮层的毛玻璃保留在第 1/5 节） */
.glass-card, .doc-card, .workflow-card, .timestamp-card, .model-detail-card, .error-card,
#app[data-wallpaper] main .glass-card, #app[data-wallpaper] main .bg-surface-card {
  -webkit-backdrop-filter: none; backdrop-filter: none;
}
.glass-card, .doc-card, .workflow-card { border: 1px solid var(--border); }
/* 卡片 hover：只换描边与浅投影，不位移不出光 */
.glass-card:not(.hover-lift):hover, .doc-card:not(.hover-lift):hover,
.workflow-card:not(.hover-lift):hover, .timestamp-card:not(.hover-lift):hover,
.model-detail-card:not(.hover-lift):hover, .error-card:not(.hover-lift):hover {
  box-shadow: var(--ui-shadow-md); transform: none;
}
html[data-theme] :is(.glass-card, .card, .doc-card, .workflow-card, .timestamp-card,
  .model-detail-card, .error-card, .tl-card, .welcome-suggestion) {
  transition: background-color var(--transition-normal), border-color var(--transition-normal),
              color var(--transition-normal), box-shadow var(--transition-fast), transform var(--transition-fast);
}
@keyframes cardEnter { from { opacity: 0; } to { opacity: 1; } }
.glass-card:not(.prompt-card):not(.snippet-card) { animation: cardEnter 200ms ease; }
/* 欢迎页快捷卡片 */
.welcome-suggestions { gap: 12px; }
.welcome-suggestion {
  border: 1px solid var(--border); background: var(--bg-card);
  border-radius: var(--radius-card, 8px);
  transition: border-color var(--transition-fast), background-color var(--transition-fast);
}
.welcome-suggestion:hover { transform: none; border-color: var(--border-hover); box-shadow: none; background: var(--bg-hover); }
.welcome-suggestion--agent { border-color: var(--border-hover); }
.welcome-suggestion--agent:hover { border-color: var(--success); box-shadow: none; }
.welcome-suggestion-icon { display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0; }
.welcome-screen { padding-top: 6vh !important; justify-content: flex-start !important; }
/* 空状态 */
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 16px; color: var(--text-tertiary); }
.empty-state .lucide { width: 30px; height: 30px; opacity: 0.45; margin-bottom: 12px; padding: 8px; border-radius: 8px; background: var(--bg-hover); box-sizing: content-box; }
.empty-state .empty-title { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.empty-state .empty-hint { font-size: 10px; }
.empty-art { color: var(--text-tertiary); }
/* 计划卡 / 时间线 */
.plan-card { cursor: default; }
.plan-card .plan-steps { display: block; }
.plan-card.collapsed .plan-steps { display: none; }
.plan-card .plan-head { cursor: pointer; user-select: none; }
.plan-card .plan-step-row.running { background: var(--brand-light); border-radius: 6px; }
.tool-step { transition: box-shadow var(--transition-fast); }
.tool-step[open] { box-shadow: var(--ui-shadow-sm); }
.tool-step summary .text-text-tertiary:not(.shrink-0) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
[data-agent-approve-ok] { min-width: 108px; }
[data-agent-approve-no] { min-width: 84px; }
/* 文件树侧栏 */
#wsSidebar .ws-resize-handle { position: absolute; left: -5px; top: 0; bottom: 0; width: 10px; cursor: col-resize; z-index: 2; }
#wsSidebar .ws-tree-row .ws-caret { transition: transform var(--transition-fast); }
#wsSidebar .ws-tree-row .ws-caret.open { transform: rotate(90deg); }
#wsSidebar .ws-empty { text-align: center; padding: 24px 12px; color: var(--text-tertiary); }
#wsSidebar .ws-empty .lucide { width: 26px; height: 26px; opacity: 0.5; margin-bottom: 8px; }
/* 本地附件角标 */
.local-only-badge {
  font-size: 10px; line-height: 1.2; padding: 1px 4px; border-radius: 8px;
  color: var(--text-secondary);
  border: 1px solid var(--border); background: var(--bg-hover);
  cursor: help; white-space: nowrap;
}
/* 飞行记录仪（body 级浮层，保留毛玻璃） */
#timelinePanel {
  background-color: rgba(22, 22, 22, 0.88);
  -webkit-backdrop-filter: blur(20px) saturate(1.2); backdrop-filter: blur(20px) saturate(1.2);
  border-left-color: var(--border);
  box-shadow: -8px 0 32px rgba(0, 0, 0, 0.4);
}
html[data-theme="light"] #timelinePanel {
  background-color: rgba(255, 255, 255, 0.90);
  border-left-color: var(--border);
  box-shadow: -8px 0 24px rgba(31, 30, 28, 0.12);
}
#timelinePanel .tl-day > div:first-child { background-color: rgba(22, 22, 22, 0.86); }
html[data-theme="light"] #timelinePanel .tl-day > div:first-child { background-color: rgba(255, 255, 255, 0.9); }
#timelinePanel .tl-card { background-color: var(--bg-card); border-color: var(--border); border-radius: var(--radius-card, 8px); }
#timelinePanel .tl-card:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-sm); transform: none; }
#timelinePanel .tl-card[aria-expanded="true"] { border-color: var(--brand); box-shadow: var(--ui-shadow-sm); }
html[data-theme="light"] #timelinePanel .tl-card { background-color: #ffffff; border-color: var(--border); }
html[data-theme="light"] #timelinePanel .tl-card:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-sm-light); }
html[data-theme="light"] #timelinePanel .tl-card[aria-expanded="true"] { border-color: var(--brand); }
#timelinePanel [data-tl-detail] > div { background-color: var(--bg-hover); border-top-color: var(--border); }
html[data-theme="light"] #timelinePanel [data-tl-detail] > div { background-color: #f6f8fa; border-top-color: var(--border); }
#tlReplayDialog > div {
  background-color: rgba(22, 22, 22, 0.90);
  -webkit-backdrop-filter: blur(20px) saturate(1.2); backdrop-filter: blur(20px) saturate(1.2);
  border-color: var(--border); box-shadow: var(--ui-shadow-lg);
}
html[data-theme="light"] #tlReplayDialog > div {
  background-color: rgba(255, 255, 255, 0.96);
  border-color: var(--border); box-shadow: var(--ui-shadow-lg-light);
}

/* ---------- 10) 蓝色字面量归零：骨架 CSS 里有 160 处 #5B8CFF / rgba(91,140,255,…) 写死的声明，
   不跟令牌走；以下逐条接管，确保全站无残留蓝色 ---------- */
.input-toolbar-btn:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.input-toolbar-model:hover { background-color: var(--bg-hover); }
.input-toolbar-btn.active, .input-toolbar-btn.active:hover { background-color: var(--brand-light); color: var(--brand-text); }
.core-input.code-mode-active { border-color: var(--brand-text); box-shadow: none !important; }
.welcome-suggestion:hover { border-color: var(--border-hover); }
.toolbox-tab.active { background-color: var(--bg-hover); color: var(--text-primary); }
.tool-input-area:focus { border-color: var(--brand) !important; box-shadow: none !important; }
.tool-btn.primary { background: var(--brand); }
.tool-btn.primary:hover { background: var(--brand-hover); }
.tool-btn.secondary:hover { border-color: var(--border-hover); color: var(--text-primary); }
.regex-flag.active { background: var(--brand-light); color: var(--brand-text); }
.regex-match { background: var(--brand-light); }
.auto-action-chip:hover { background-color: var(--bg-hover); border-color: var(--border-hover); }
.command-item:hover, .command-item.selected { background-color: var(--bg-hover); color: var(--text-primary); }
.context-menu-item:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.speed-bar-fill.cobalt { background: var(--brand); }
.speed-bar-fill.violet { background: var(--brand-text); }
.model-detail-card { border-color: var(--border); }
.modal-box { border-color: var(--border); }
.doc-card:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--brand); background: var(--brand-light); }
.model-pop-item.selected { background-color: var(--brand-light); color: var(--brand-text); }
.msg-action-btn.active { background: var(--brand-light); color: var(--brand-text); }
.empty-art .empty-icon { color: var(--brand-text); }
.scroll-bottom-btn:hover { color: var(--brand-text); border-color: var(--brand); }
.code-expand-bar button:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.mode-segment button.active { background-color: var(--bg-card); color: var(--text-primary); box-shadow: var(--ui-shadow-sm); }
.nav-icon-btn.active { background-color: var(--brand-light); color: var(--text-primary); }
.nav-icon-btn.active::before { background: var(--brand); }
.list-item.active { background: var(--brand-light); color: var(--brand-text); border-left: 2px solid var(--brand); }
.session-item.active { background-color: var(--brand-light); box-shadow: inset 2px 0 0 var(--brand); border-left: none; }
.session-item .session-model { background-color: var(--brand-light); color: var(--brand-text); }
.new-chat-main-btn:hover { box-shadow: none; }
.workflow-card:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); transform: none; }
.workflow-card::before { background: var(--brand); }
.running-step.active { color: var(--brand-text); }
.thinking-indicator { background: var(--bg-hover); color: var(--text-secondary); }
.count-badge { background: var(--bg-hover); color: var(--text-secondary); }
.typing-cursor { background: var(--brand); }
.thinking-header:hover { background-color: var(--bg-active); }
.progress-ring-fill { stroke: var(--brand); }
.monitor-bar { background: var(--brand); }
.roadmap-item.current::before { background: var(--brand); }
.roadmap-status.current { color: var(--brand-text); }
.session-search-input:focus { border-color: var(--brand) !important; box-shadow: none !important; }
.session-group-chip:hover { background-color: var(--bg-hover); color: var(--text-primary); }
.session-group-chip.active { background-color: var(--brand-light); color: var(--brand-text); }
.group-modal-add input:focus { border-color: var(--brand) !important; box-shadow: none !important; }
.beacon-dot { background-color: var(--brand); box-shadow: none; }
/* [A-1] 1047 原 .bring-ripple 接管规则已随涟漪移除删除 */
.welcome-logo { border: 1px solid var(--border); }
.hover-lift:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); transform: none; }
.core-input:focus-within { border-color: var(--brand) !important; box-shadow: none !important; }
html[data-theme="light"] .core-input:focus-within { border-color: var(--brand) !important; box-shadow: none !important; }
html[data-theme="light"] .input-toolbar-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
html[data-theme="light"] .input-toolbar-model:hover { background: var(--bg-hover); }
html[data-theme="light"] .nav-icon-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
html[data-theme="light"] .workflow-card:hover { box-shadow: var(--ui-shadow-md-light); }
html[data-theme="light"] .md-body tr:nth-child(even) td { background: var(--bg-hover); }

/* ---------- 11) 基础态（未选图片壁纸）配色：暖灰 / 暖白 ----------
   作用域说明（沿用原实现）：data-wallpaper 挂在 #app 上，body 上从来没有该属性，
   因此一律以 #app 为作用域，并显式排除四款图片壁纸（它们自带 110 的穿透档位与深色外观）。 */
html[data-theme="dark"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) {
  background-color: var(--bg-app) !important;
}
html[data-theme="dark"] #app:not([data-wallpaper]) { background-image: none !important; }
html[data-theme="dark"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) aside.bg-surface-deep {
  /* [UI 2.0 步骤2] 拆封印：0.85 实色 → 0.55 半透 + blur（深色档略厚保对比度） */
  background-color: rgba(17, 17, 17, 0.55) !important;
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  border-right-color: var(--border) !important;
}
/* [A-1.5] #middleSidebar 已从本组摘除：中栏非壁纸态走上面的透明底 + --border-subtle 分隔，
   不再吃 0.85 面板底（否则 !important 会压过降级规则，双栏并排感复发） */
html[data-theme="dark"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) main.bg-surface-void {
  /* [UI 2.0 步骤2] 拆封印：不透明 --bg-app → 0.55 半透 + blur（对称处理，原为 var(--bg-app) 实底） */
  background-color: rgba(10, 10, 10, 0.55) !important; background-image: none !important;
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
}
html[data-theme="dark"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) .glass-card,
html[data-theme="dark"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) main .bg-surface-card {
  background-color: var(--bg-card) !important;
  border-color: var(--border) !important;
  box-shadow: var(--ui-shadow-sm) !important;
}
html[data-theme="light"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) {
  background-color: var(--bg-app) !important;
}
html[data-theme="light"] #app:not([data-wallpaper]) { background-image: none !important; }
html[data-theme="light"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) aside.bg-surface-deep {
  /* [UI 2.0 步骤2] 拆封印：0.85 实色 → 0.45 半透 + blur（任务书指定值） */
  background-color: rgba(255, 255, 255, 0.45) !important;
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  background-image: none !important;
  border-right-color: var(--border) !important;
}
/* [A-1.5] #middleSidebar 已从本组摘除（同深色侧注释） */
html[data-theme="light"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) main.bg-surface-void {
  /* [UI 2.0 步骤2] 拆封印：不透明 --bg-app → 0.45 半透 + blur（与浅色侧栏同值成对） */
  background-color: rgba(255, 255, 255, 0.45) !important; background-image: none !important;
  -webkit-backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
  backdrop-filter: blur(var(--backdrop-blur, 24px)) saturate(1.2) !important;
}
html[data-theme="light"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) .glass-card,
html[data-theme="light"] #app:not(:is([data-wallpaper="anime-starry"], [data-wallpaper="anime-aurora"], [data-wallpaper="anime-window"], [data-wallpaper="custom"])) main .bg-surface-card {
  background-color: #ffffff !important;
  border-color: var(--border) !important;
  box-shadow: var(--ui-shadow-sm-light) !important;
}
#page-settings .glass-card:hover { border-color: var(--border-hover); box-shadow: var(--ui-shadow-md); }

/* ---------- 12) 窄屏与响应式（结构与交互规则，逐条保留） ---------- */
@media (min-width: 1600px) {
  #page-prompts .grid, #page-snippets .grid, #page-docs .grid { grid-template-columns: repeat(4, minmax(0, 1fr)) !important; }
}
@media (min-width: 1280px) and (max-width: 1599px) {
  #page-prompts .grid, #page-snippets .grid, #page-docs .grid { grid-template-columns: repeat(3, minmax(0, 1fr)) !important; }
}
@media (min-width: 768px) and (max-width: 1279px) {
  #page-prompts .grid, #page-snippets .grid, #page-docs .grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
}
@media (max-width: 767px) {
  #page-prompts .grid, #page-snippets .grid, #page-docs .grid { grid-template-columns: 1fr !important; }
}
#page-prompts .grid > *, #page-snippets .grid > * { min-width: 0; overflow: hidden; }
@media (max-width: 900px) {
  body, #app { background-attachment: scroll !important; } /* [A-1] .aurora-bg 已删 */
}
@media (max-width: 767px) {
  #chat-compare .h-9 > div:last-child > span { display: none; }
  #chat-compare .h-9 > div:last-child { gap: 8px; }
  #page-chat > header { flex-wrap: wrap; height: auto; min-height: 56px; align-content: center; row-gap: 4px; }
  #chatTitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #page-chat > header > div:first-child { min-width: 0; flex: 1 1 auto; }
  .mode-segment { flex-basis: 100%; justify-content: flex-end; flex-shrink: 0; order: 2; margin-left: 0; padding-left: 0; }
}
@media (max-width: 1023px) {
  body.narrow-screen aside:nth-of-type(2) {
    position: fixed !important; left: 0; top: 0; bottom: 0; z-index: 70;
    transform: translateX(-100%); transition: transform 200ms ease-in-out;
    box-shadow: 0 0 40px rgba(0, 0, 0, 0.5);
  }
  body.narrow-screen.drawer-open aside:nth-of-type(2) { transform: none; }
}
@media (max-width: 640px) {
  #page-prompts > header, #page-snippets > header,
  #page-docs > .h-14, #page-automation > .h-14, #page-cost > .h-14 {
    height: auto; min-height: 56px; flex-wrap: wrap; align-content: center; row-gap: 2px;
  }
  #page-prompts > header { padding: 4px 12px; }
  #page-prompts > header > div:first-child { flex: 1 1 auto; min-width: 0; }
  #page-prompts > header > div:last-child { margin-left: auto; }
  #page-snippets > header { padding: 4px 12px; row-gap: 4px; }
  #page-snippets > header > div:first-child { flex: 1 1 100%; flex-wrap: wrap; }
  #page-snippets > header .w-56 { width: 100%; }
  #page-snippets > header select { flex: 1 1 120px; max-width: 160px; }
  #page-snippets > header > button { margin-left: auto; }
  #page-docs > .h-14 > div:last-child, #page-cost > .h-14 > div:last-child,
  #page-automation > .h-14 > div:last-child { margin-left: auto; flex-wrap: wrap; justify-content: flex-end; row-gap: 4px; }
  #page-docs > .h-14 .w-56 { width: auto; min-width: 0; flex: 1 1 150px; }
  #page-automation .grid.grid-cols-2 { grid-template-columns: 1fr !important; }
  #page-settings .glass-card .flex.items-center.justify-between,
  #page-settings .glass-card .flex.items-center.gap-2 { flex-wrap: wrap; row-gap: 8px; }
}

/* ---------- 13) 壁纸联动色相接管 ----------
   110 的 wallpaperLinkStyle 会按壁纸色调写 #app[data-wall-tone="x"] { --wall-glow-rgb / --wall-accent-rgb }，
   壁纸模式下三栏光晕、激活图标、控件描边全部消费这两个变量（默认档是青 rgb(229, 115, 74)，
   与暖棕主题冲突）。这里用 (1,2,0) 权重 + 后注入覆盖为暖棕。
   代价：设置页「主题色调」切换将不再改变强调色相；如需恢复，删掉本节即可。 */
#app[data-wall-tone][data-wall-tone],
#app[data-wallpaper] {
  --wall-glow-rgb: 229, 115, 74;
  --wall-accent-rgb: 229, 115, 74;
  --wall-tint-color: rgb(229, 115, 74);
}
.aurora-edge { background-image: none; }
/* 对比页质量条：运行时由 JS 内联写入 linear-gradient(90deg,#5B8CFF,#3AD6E8)，用 !important 收口为纯色 */
.quality-bar-fill { background-image: none !important; background-color: var(--brand) !important; }
.quality-bar { background-color: var(--bg-hover); }

/* ---------- 14) 冷色收口：骨架里以类选择器/关键帧写死的蓝 · 紫 · 青 ---------- */
/* 命令面板图标：骨架 .command-item-icon 写死蓝底蓝字（无内联样式，类规则可覆盖） */
.command-item-icon { background-color: var(--brand-light); color: var(--brand-text); }
/* 信标点脉冲环：beacon 关键帧里写死 rgba(91,140,255,…)，而动画值会压过普通 box-shadow 声明，
   所以必须重定义同名关键帧（后定义的同名 @keyframes 生效），否则状态点仍泛蓝光。 */
@keyframes beacon {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(229, 115, 74, 0.45); }
  50%      { opacity: 0.65; box-shadow: 0 0 0 4px rgba(229, 115, 74, 0); }
}
.beacon-dot.violet { background-color: var(--brand-text); box-shadow: none; }
.beacon-dot.cyan { background-color: var(--success); box-shadow: none; }
/* 弹窗 / 命令面板：骨架 box-shadow 带一层蓝色外发光 → 普通投影 */
.modal-box, .confirm-box, .command-palette, .context-menu { box-shadow: var(--ui-shadow-lg); }
html[data-theme="light"] .modal-box, html[data-theme="light"] .confirm-box,
html[data-theme="light"] .command-palette, html[data-theme="light"] .context-menu { box-shadow: var(--ui-shadow-lg-light); }
/* 工作流运行按钮：蓝底蓝框蓝字 → 暖棕描边，hover 才实底 */
.workflow-run-btn { background: var(--brand-light); border-color: var(--border-hover); color: var(--brand-text); }
.workflow-run-btn:hover { background: var(--brand); border-color: var(--brand); color: var(--brand-on); }
/* 模板变量 chip：紫 → 暖棕 */
.var-chip { background: var(--brand-light); color: var(--brand-text); border-color: var(--border-hover); }
/* 会话分组 chip 选中态：蓝底蓝字蓝框 → 暖棕 */
.session-group-chip.active { background: var(--brand-light); color: var(--brand-text); border-color: var(--brand); }
/* 空状态大图标：蓝紫渐变底 → 纯色（shorthand 同时清掉 background-image） */
.empty-art .empty-icon { background: var(--bg-hover); color: var(--brand-text); }
/* 拖拽上传遮罩图标 */
.drop-overlay-icon { background-color: var(--brand-light); color: var(--brand-text); }
/* 壁纸选择器：选中描边 + 自定义上传虚线框 */
.wallpaper-option.active { border-color: var(--brand); box-shadow: 0 0 0 2px var(--brand-light); }
.wallpaper-option.custom-upload { border-color: var(--border-hover); }
html[data-theme="light"] .wallpaper-option.custom-upload { border-color: var(--border); }

/* ---------- 15) 冷色收口（续）：通知图标底 / 空状态图标框 / 对比页发送键 ---------- */
.notification-icon.info { background-color: var(--brand-light); }
.empty-icon, .empty-art .empty-icon { border-color: var(--border); }
#compareSendBtn { box-shadow: var(--ui-shadow-sm); }

/* ---------- 16) Tailwind 光晕工具类收口 ----------
   [A-3b] 光晕令牌已在骨架 @theme 源头退役（--shadow-glow-* / --shadow-glove 已删，Tailwind
   不再生成对应工具类）；但 4 个发送按钮的 class 里仍留着 shadow-glow-cobalt 字符串，
   本条保留为它们唯一的阴影供给（深色 --ui-shadow-sm = none，浅色 = 细投影），删掉会让
   浅色主题发送按钮失去投影。原 [class*="shadow-glove"] 一条全站无元素匹配，属死中和，已删。 */
[class*="shadow-glow-"] { box-shadow: var(--ui-shadow-sm) !important; }

/* ---------- 17) 动效降级 ---------- */
@media (prefers-reduced-motion: reduce) {
  .animate-pulse, .animate-spin, .beacon-dot, .stream-cursor, .notification-progress,
  .plan-card .animate-pulse, [class*="animate-"] { animation: none !important; }
  .msg-enter, .msg-fade, .tool-step, .notification, .plan-card, .glass-card { transition: none !important; animation: none !important; }
  .nav-icon-btn::before { transition: none !important; }
  button:not(:disabled):hover, [role="button"]:hover,
  button:not(:disabled):active, [role="button"]:active,
  #coreInputContainer > .input-toolbar .ui6-send:hover,
  #timelinePanel .tl-card:hover { transform: none !important; }
  * { scroll-behavior: auto !important; }
}

/* ---------- 18) [Day20] 表面收口（架构师授权：仅 padding + 圆角，不动颜色）----------
   双重类选择器（.glass-card.p-5 等，特异性 (0,2,0)）稳定压过 Tailwind 工具类，不依赖样式表顺序。 */
/* 卡片 padding 统一 20px 16px（覆盖 p-4 紧凑卡 / p-5 标准卡 / p-6 空态卡；p-6+ 即"不要 24px+"的收口对象） */
.glass-card, .glass-card.p-4, .glass-card.p-5, .glass-card.p-6, .glass-card.p-8 { padding: 20px 16px; }
/* 输入框/下拉/文本域圆角统一 6px（骨架以元素限定选择器定义半径，双类 .x.x 特异性 (0,2,0) 顺序无关稳压） */
.form-input.form-input, .form-select.form-select, .form-textarea.form-textarea { border-radius: 6px; }
/* 按钮圆角：[UI 2.0] 字面量 6px → --radius-btn 令牌（12px），只命中声明了 rounded-lg 的 button（rounded-full 圆形钮/胶囊钮天然不在选择器内） */
button.rounded-lg, .btn-ghost.btn-ghost { border-radius: var(--radius-btn, 12px); }
/* 卡片圆角：[UI 2.0] 字面量 8px → --radius-card 令牌（16px），双类压过 rounded-xl/2xl 工具类的职责不变 */
.glass-card, .glass-card.rounded-xl, .glass-card.rounded-lg, .glass-card.rounded-2xl { border-radius: var(--radius-card, 16px); }

/* ---------- 19) [Day20] 对话页视觉重心（架构师授权：快捷按钮边框改灰，不碰 JS 逻辑） ----------
   橙色只留给「新建对话」（橙底白字，Day19 令牌已达标）与发送键；快捷建议卡边框去橙归灰，
   hover 仅微亮一档灰。110:2244 以 #app[data-wall-tone] main .welcome-suggestion (1,3,0) 写壁纸橙描边，
   这里以同特异性 + 后注入胜出。 */
#app[data-wall-tone] main .welcome-suggestion,
#app[data-wallpaper] main .welcome-suggestion,
#app .welcome-suggestion.welcome-suggestion { border-color: var(--border, rgba(255, 255, 255, 0.08)); }
#app[data-wall-tone] main .welcome-suggestion:hover,
#app[data-wallpaper] main .welcome-suggestion:hover,
#app .welcome-suggestion.welcome-suggestion:hover { border-color: var(--border-hover, rgba(255, 255, 255, 0.16)); }

/* ---------- 20) [Day21b/c] 设置页两栏导航（内容区左侧 sticky，替换骨架 #sidebar-settings）----------
   骨架卡级子导航由 70 build() 以内联 display:none 隐藏（切页脚本对它的 hidden/flex 类切换压不过内联）。
   本节职责：①导航本体（180px sticky）②四组分组标题 + 带图标的导航项（[Day21c]：13px / 16px 单色图标继承文字色 /
   active 暖橙 + 左 2px 橙条 + 橙色底）③设置页激活时 260px 中栏收敛为 0（#sidebar-settings 已隐藏，
   不收敛会留空条；:has() 不支持时优雅退化：仅多一条空栏，功能不受影响）。
   色值取任务书字面量并配同值主题令牌回退（--color-text-* / --bg-hover / --brand 深浅两套都有定义），
   故浅色主题下 hover 不会出现白底白字。 */
#settingsSideNav {
  width: 180px; flex-shrink: 0; align-self: flex-start;
  position: sticky; top: 0;
  max-height: calc(100vh - 96px); overflow-y: auto;
  padding: 2px 0 8px;
}
#settingsSideNav .nav-group { margin-bottom: 16px; }
#settingsSideNav .nav-group:last-child { margin-bottom: 0; }
#settingsSideNav .nav-group-title {
  font-size: 11px; color: var(--color-text-tertiary, #666);
  text-transform: none; padding: 4px 12px; margin-bottom: 4px;
  letter-spacing: 0.02em;
}
#settingsSideNav a {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: 4px;
  color: var(--color-text-secondary, #a3a3a3); font-size: 13px;
  cursor: pointer; user-select: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: color .15s ease, background-color .15s ease;
}
#settingsSideNav a svg { width: 16px; height: 16px; flex-shrink: 0; }
#settingsSideNav a span { overflow: hidden; text-overflow: ellipsis; }
#settingsSideNav a:hover { background: var(--bg-hover, rgba(255, 255, 255, 0.04)); color: var(--color-text-primary, #ededed); }
#settingsSideNav a.active {
  color: var(--brand, #E5734A);
  background: rgba(229, 115, 74, 0.06);
  box-shadow: inset 2px 0 0 var(--brand, #E5734A);
}
@media (max-width: 860px) { #settingsSideNav { display: none; } }
body:has(#page-settings:not(.hidden)) #middleSidebar { display: none; }

/* ---------- 21) [Day21d/21e] 卡片视觉重心与层级（只动 CSS，对标 zcode 卡片）----------
   ① 卡片底色 #161616 → #1a1a1a。深色令牌已在 §0 改（--bg-card），本处补图片壁纸路径：
      壁纸模式卡片底色是 110 的字面量 rgba(22,22,22,α)（110 不在允许改动范围），
      此处以同特异性选择器 + 后注入胜出（135 的 style 在 110 之后注入），不动 110 一个字符。
      [Day21e] 透明度 0.62 → 0.9：卡片更实、边界清晰（不再吃 --wall-card-alpha，见 §5 遗留）。
   ② 卡片标题 15px/600/#ededed；标题左侧图标 18px、与标题同色（h3 自带 gap-2 = 8px，已达要求不动）。
      svg 的类名 w-4/h-4/text-brand-* 是 Tailwind 工具类（(0,1,0)），本条 (0,1,2) 稳压。
   ③ 统计卡主数字 22px/600（数据洞察 6 宫格 / 成本中心 8 宫格 / 自动化监控 3 宫格）——
      [Day21e] 由 28px 收回：28px 在 6/8 宫格里过大、卡片显空；22px 仍明显大于 12px 标签（1.83x）。
      次要标签 12px/#666。数字**不改颜色**：三处统计数字当前已是 text-text-primary(#ededed)
      或语义色（节省=绿、命中率=橙），强制改灰会丢掉语义，字号字重已足够拉开层级。
   ④ 卡片内说明文字 12px → 13px；其中原 tertiary(#666) 的一档提到 secondary(#a3a3a3)，
      只命中 .text-xs.text-text-tertiary 组合，避免把 text-danger/success/warning 的语义色染灰。
   ⑤ 卡片内输入框统一 32px、按钮统一 32px（[Day21e] 输入框由 36px 收回，与按钮同高）。
      按钮同时清零上下 padding 并靠 <button> 自带的内容垂直居中——固定高度 + 原 py-2/py-2
      会把文字挤在 32px 盒子里。
   ⑥ [Day21e] 紧凑化（修正 Day21d 过松）：
      · 卡片 padding 20px 16px → 16px 20px（选择器镜像 §18 的完整列表，否则 .p-4/.p-5 的
        (0,2,0) 会压过单类 (0,1,0) 的覆写）；
      · 卡内元素间距 16px → 12px：h3 的 mb-4、卡内 .space-y-4、以及 mb-4/mb-5/mb-6/mt-4 工具类。
        Tailwind v4 的 space-y-* 用 **margin-bottom 加在非末子**实现（实测首子 16px / 末子 0），
        故同时把同胞的 margin-top 归零，两种实现下间距都恒为 12px、不会叠加；
      · 卡片间距：成本中心外层 space-y-4 与图表网格 gap-4 收到 12px（设置页 #settingsBody
        本就是 space-y-3 = 12px，未动）。 */
#app[data-wallpaper] main .glass-card { background: rgba(26, 26, 26, 0.9); }
.glass-card, .glass-card.p-4, .glass-card.p-5, .glass-card.p-6, .glass-card.p-8 { padding: 16px 20px; }
.glass-card h3 { font-size: 15px; font-weight: 600; color: var(--color-text-primary, #ededed); margin-bottom: 12px; }
.glass-card h3 svg { width: 18px; height: 18px; color: var(--color-text-primary, #ededed); }
.glass-card .space-y-4 > :not(:last-child) { margin-bottom: 12px; }
.glass-card .space-y-4 > * + * { margin-top: 0; }
.glass-card [class*="mb-4"], .glass-card [class*="mb-5"], .glass-card [class*="mb-6"] { margin-bottom: 12px; }
.glass-card [class*="mt-4"], .glass-card [class*="mt-5"], .glass-card [class*="mt-6"] { margin-top: 12px; }
#page-cost .max-w-5xl.space-y-4 > :not(:last-child) { margin-bottom: 12px; }
#page-cost .max-w-5xl.space-y-4 > * + * { margin-top: 0; }
#page-cost .grid.gap-4 { gap: 12px; }
#page-settings [id^="insNum"],
#costStatCards > div > div:first-child,
.monitor-stat-value { font-size: 22px; font-weight: 600; line-height: 1.2; }
#page-settings [id^="insNum"] + div,
#costStatCards > div > div:last-child,
.monitor-stat-label { font-size: 12px; color: var(--color-text-tertiary, #666); }
.glass-card .text-xs { font-size: 13px; }
.glass-card .text-xs.text-text-tertiary { color: var(--color-text-secondary, #a3a3a3); }
.glass-card .form-input, .glass-card .form-select { height: 32px; }
.glass-card button, .glass-card .btn-ghost, .glass-card .btn-primary { height: 32px; padding-top: 0; padding-bottom: 0; }

/* ---------- 22) [Day22a] 对话页视觉升级（对标 zcode / DSH；只动 CSS + 30_chat 渲染 HTML）----------
   22.1 空状态紧凑化：标题 20px、副标题 13px/--text-secondary、4 个快捷入口由"大卡"降为一行小按钮
        （32px 高 / 6px 12px 内边距 / 灰边框 / hover 变亮），整块由正中央改为垂直居中偏上。
        内边距取 6px 12px 而非任务书字面量 8px 16px：4 个按钮在 768px 列宽内必须落在一行，
        实测 8px 16px 会在 728px 可用宽下挤成 3+1（详见下方 padding 两处注释）。
        快捷卡图标底色统一归零为单色灰字（Day20"去强调色"方向的延续）——否则 4 个胶囊里
        3 个已被 §8 收成暖橙、1 个黄色被当语义色保留，一行之内会出现 1 个异色。
        本节对所有 .welcome-* 一律以 #chatNormalContent 作用域限定：骨架欢迎页与 30_chat
        动态重建的欢迎页都是它的子节点，两条渲染路径共用同一套样式，不会出现两种观感。
   22.2 AI 消息：左侧 24px 圆形头像（图标非图片）；顶部信息行 11px。
   22.3 用户消息：品牌浅橙底 → 中性浅底 rgba(255,255,255,0.06)；圆角 8px / padding 12px 16px；
        气泡下方与顶部信息行重复的时间戳隐去（保留"仅存本机"安全角标）。
   22.4 消息操作栏：默认只露 复制 / 重新生成 / 删除 三个主按钮，其余 8 个按钮收进 .msg-actions-more，
        悬停操作栏时横向展开。**不删按钮**是有意的：这 8 项在对话页的唯一入口就是这里
        （右键菜单只覆盖 copy/edit/regenerate/branch/delete），删掉即功能回归。
        展开用 max-width 过渡且整条仍是一级 flex 行，不产生纵向跳动；min-width:0 必须写 ——
        flex 项的 min-width:auto 会压过 max-width，不写则收不起来。
   22.5 输入台：容器描边（§4 已达标，见注）、附件/工具图标、圆形橙底 36px 发送键。 */

/* -- 22.1 空状态紧凑化 -- */
#chatNormalContent .welcome-screen {
  /* 垂直居中偏上：不是正中央（原 justify-content:center 会让标题落在视觉重心之上、下方一片空） */
  justify-content: flex-start !important;
  padding-top: clamp(40px, 12vh, 120px) !important;
  padding-bottom: 24px;
  /* 左右内边距归零：.welcome-screen 上限 768px 且骨架带 padding: 40px 20px，
     快捷按钮行可用宽只剩 728px，4 个按钮（实测 725px + 3×8px gap）会被挤到 3+1 换行。
     归零后可用 768px，一行放得下；纵向留白仍由 #chatNormalContent 的 px-6 提供。 */
  padding-left: 0; padding-right: 0;
}
#chatNormalContent .welcome-logo { width: 44px; height: 44px; border-radius: 8px; margin-bottom: 12px; }
#chatNormalContent .welcome-logo .text-3xl { font-size: 20px; }
#chatNormalContent .welcome-title { font-size: 20px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
#chatNormalContent .welcome-subtitle {
  font-size: 13px; color: var(--text-secondary); line-height: 1.6;
  margin-bottom: 20px; max-width: 420px;
}
/* 4 个快捷入口：2×2 大卡 → 一行小按钮 */
#chatNormalContent .welcome-suggestions {
  display: flex; flex-wrap: wrap; justify-content: center;
  gap: 8px; max-width: none; width: auto;
}
#chatNormalContent .welcome-suggestion {
  display: inline-flex; align-items: center; gap: 8px;
  min-height: 32px; padding: 8px 12px;
  background: transparent; border-radius: 8px; white-space: nowrap;
}
#chatNormalContent .welcome-suggestion:hover { background: var(--bg-hover); }
/* 图标：14px 单色灰字，去掉彩色底块（!important 为压过 §8 的内联 style 接管与骨架 28px 底块） */
#chatNormalContent .welcome-suggestion-icon {
  width: 14px; height: 14px; border-radius: 0;
  background: none !important; background-color: transparent !important;
  color: var(--text-tertiary) !important;
}
#chatNormalContent .welcome-suggestion-icon svg,
#chatNormalContent .welcome-suggestion-icon i { width: 14px; height: 14px; }
#chatNormalContent .welcome-suggestion:hover .welcome-suggestion-icon { color: var(--text-secondary) !important; }
/* 12px 主标签 / 11px 说明：说明降一档既拉开层级，又让 4 个按钮稳定落在一行（实测省 ~30px） */
#chatNormalContent .welcome-suggestion-text { font-size: 11px; color: var(--text-tertiary); line-height: 1.2; }
#chatNormalContent .welcome-suggestion-text strong {
  display: inline; margin: 0 8px 0 0; font-size: 12px; font-weight: 500; color: var(--text-primary);
}
@media (max-width: 640px) { #chatNormalContent .welcome-suggestion { white-space: normal; } }

/* -- 22.2 AI 消息头像 + 顶部信息行 -- */
#chatMessagesNormal .msg-avatar {
  flex: 0 0 24px; width: 24px; height: 24px; max-width: 24px; border-radius: 50%;
  align-self: flex-start; margin: 1px 12px 0 0;
  display: flex; align-items: center; justify-content: center;
  background: #1a1a1a; color: #a3a3a3;
}
/* 图标尺寸对 i / svg 都写：refreshIcons() 把 <i data-lucide> 换成 <svg>，只写一种会有首帧跳变 */
#chatMessagesNormal .msg-avatar svg,
#chatMessagesNormal .msg-avatar i { width: 14px; height: 14px; }
/* 头像已在，信息行开头的品牌色 beacon 圆点即冗余 → 只在带头像的 AI 气泡内隐去 */
#chatMessagesNormal .msg-avatar + .msg-ai-body .beacon-dot { display: none; }
/* 信息行 11px。骨架类 text-[10px] 必须留在 DOM 上（finalizeAssistantUI 用它取头部节点回写
   模型名/时间），故此处用并存的 .msg-meta-line 提字号，靠后注入的同权重规则胜出。 */
#chatMessagesNormal .msg-meta-line { font-size: 11px; color: var(--text-tertiary); }

/* -- 22.3 用户消息浅色卡片 -- */
#chatMessagesNormal .msg-bubble__user-box {
  background: rgba(255, 255, 255, 0.06) !important;
  background-image: none !important;
  border-radius: 8px !important;
  padding: 12px 16px !important;
}
html[data-theme="light"] #chatMessagesNormal .msg-bubble__user-box {
  background: rgba(0, 0, 0, 0.045) !important;
}
/* 气泡下方的时间戳与顶部信息行"你 · 时间"重复：隐去时钟图标与裸时间文本，
   .local-only-badge（"仅存本机"隐私角标）保留 —— 它是 span 且有 class，不被 :not([class]) 命中。
   注意图标必须同时写 i 与 svg：refreshIcons() 会把 <i data-lucide> 就地替换成 <svg class="lucide …">，
   只写 i 选择器在首帧有效、图标渲染完就失效（实测漏网）。 */
#chatMessagesNormal .msg-timestamp { margin-top: 4px; }
#chatMessagesNormal .msg-timestamp > i,
#chatMessagesNormal .msg-timestamp > svg,
#chatMessagesNormal .msg-timestamp > span:not([class]) { display: none; }

/* -- 22.4 消息操作栏：3 主按钮常显 + 其余悬停展开 -- */
#chatMessagesNormal .msg-actions { gap: 4px; margin-top: 8px; }
#chatMessagesNormal .msg-action-btn { width: 22px; height: 22px; border-radius: 4px; color: var(--text-tertiary); }
#chatMessagesNormal .msg-action-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
#chatMessagesNormal .msg-actions-main { display: flex; align-items: center; gap: 4px; }
#chatMessagesNormal .msg-actions-more {
  display: flex; align-items: center; gap: 4px;
  min-width: 0; max-width: 0; overflow: hidden;
  opacity: 0; pointer-events: none;
  transition: max-width 200ms ease, opacity 160ms ease;
}
#chatMessagesNormal .msg-actions-more .msg-action-btn { flex: 0 0 22px; }
#chatMessagesNormal .msg-actions:hover .msg-actions-more,
#chatMessagesNormal .msg-actions:focus-within .msg-actions-more {
  max-width: 240px; opacity: 1; pointer-events: auto;
}

/* -- 22.5 输入台：工具图标 + 发送键尺寸 --
   容器 1px 描边 rgba(255,255,255,0.08) 已由 §4 的 "border: 1px solid var(--border)" 覆盖
   （--border 深色主题即该字面量），此处不重复声明；壁纸主题下该边框由 110 的壁纸专属规则接管，
   属既有设计（110 不在允许改动范围），不在此处对抗。
   附件（paperclip / image）与模型选择均已在骨架与 UI6 三层布局中就位，本日只补一个工具图标。 */
#coreInputContainer .input-toolbar .tools-toggle-btn { position: relative; }
#coreInputContainer .input-toolbar .tools-toggle-btn.tools-on {
  background: var(--brand-light); color: var(--brand-text);
  box-shadow: inset 0 0 0 1px rgba(229, 115, 74, 0.28);
}
#coreInputContainer .input-toolbar .tools-toggle-btn.tools-on:hover {
  background: var(--brand-light); color: var(--brand-text);
}
/* 发送键：圆形品牌橙底 36px（骨架 w-9 h-9 rounded-full + §4 品牌实底已达标，此处锁死尺寸，
   避免任何后到的工具类把它带回方角/异尺寸） */
#coreInputContainer > .input-toolbar .ui6-send {
  width: 36px; height: 36px; min-width: 36px; border-radius: 50%;
}

/* -- 22.6 顶栏模型状态小字（#chatTitle 右侧）--
   [Day22b] 该元素已下线（30_chat.js 的 installChatHeaderStatus 改为"只移除"），
   模型身份改在输入台模型行显示；本节规则保留为惰性（元素不存在即不生效），便于回退。 */
.chat-header-status {
  font-size: 12px; color: var(--text-tertiary);
  white-space: nowrap; flex-shrink: 0;
  overflow: hidden; text-overflow: ellipsis; max-width: 260px;
  display: inline-flex; align-items: center;
}
html[data-theme="light"] .chat-header-status { color: var(--text-tertiary); }

/* ---------- 23) [Day22b] 对话页空状态重构（对标 zcode：输入框驱动，而非欢迎页驱动）----------
   目标形态：欢迎语一句话 → 输入台 → 快捷入口行，三者同一视觉中轴；
   有消息时输入台回到页面底部（本节所有规则都以 :has(#welcomeScreen) 为前置，非空状态一律不生效）。

   23.1 整列居中：空状态判据 = #welcomeScreen 存在（骨架静态欢迎页与 30_chat 动态重建同源）。
        #chatNormalContent 由 flex:1 改为自适应高度，否则它撑满剩余空间、输入台被顶在底部。
   23.2 欢迎语：24px/600/#ededed；大图标与副标题视觉删除（骨架 HTML 不改，只 display:none）。
        这里必须用 !important 覆盖 §22.1 的 padding-top: clamp(40px,12vh,120px) !important
        —— 那是 Day22a"长卷 + 垂直居中偏上"形态的值；Day22b 整块已居中，欢迎语只需紧贴输入台。
   23.3 快捷入口行：由 30_chat 从欢迎页移进输入台底部容器（CSS 不能跨父节点移位）并挂 id，
        紧贴输入框下方 12px。chip 视觉与 §22.1 一致（§22.1 仍管首帧未移动时的静态欢迎页）；
        文字按任务书 13px（§22.1 的 11/12px 是为"塞进 728px 一行"做的妥协，移到输入框下方后
        整行宽可用，不再需要压缩）。边框不在此声明：骨架 .welcome-suggestion 自带 1px solid，
        颜色由 §19 接管为 --border（= rgba(255,255,255,0.08)），hover 为 --border-hover（变亮），
        正是本日要求的"1px rgba(255,255,255,0.08) + hover 变亮"。
   23.4 工具栏三组：组间 8px（附件组后 / 输入模式组前），组内沿用骨架 2px gap；
        顺序由 30_chat 的 installToolbarGroups() 重排为 附件·图片 → 代码·思考·工具·本机 → 麦克风。 */
#chat-normal:has(#welcomeScreen) { justify-content: center; padding-bottom: 4vh; }
#chat-normal:has(#welcomeScreen) > #chatNormalContent { flex: 0 0 auto; overflow: visible; padding-bottom: 0; }
#chat-normal:has(#welcomeScreen) #chatNormalContent .welcome-screen {
  height: auto; padding-top: 0 !important; padding-bottom: 0; margin-bottom: 20px;
}
#chatNormalContent .welcome-logo,
#chatNormalContent .welcome-subtitle { display: none; }
#chatNormalContent .welcome-title { font-size: 24px; font-weight: 600; color: var(--text-primary, #ededed); margin-bottom: 0; }
#welcomeQuickRow { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 12px; max-width: none; width: auto; }
#welcomeQuickRow .welcome-suggestion {
  display: inline-flex; align-items: center; gap: 8px;
  min-height: 32px; padding: 8px 12px;
  background: transparent; border-radius: 8px; white-space: nowrap;
}
#welcomeQuickRow .welcome-suggestion:hover { background: var(--bg-hover); }
#welcomeQuickRow .welcome-suggestion-icon {
  width: 14px; height: 14px; border-radius: 0;
  background: none !important; background-color: transparent !important;
  color: var(--text-tertiary) !important;
}
#welcomeQuickRow .welcome-suggestion-icon svg,
#welcomeQuickRow .welcome-suggestion-icon i { width: 14px; height: 14px; }
#welcomeQuickRow .welcome-suggestion:hover .welcome-suggestion-icon { color: var(--text-secondary) !important; }
#welcomeQuickRow .welcome-suggestion-text { font-size: 13px; color: var(--text-tertiary); line-height: 1.2; }
#welcomeQuickRow .welcome-suggestion-text strong {
  display: inline; margin: 0 8px 0 0; font-size: 13px; font-weight: 500; color: var(--text-primary);
}
@media (max-width: 640px) { #welcomeQuickRow .welcome-suggestion { white-space: normal; } }
#coreInputContainer .input-toolbar #attachImageBtn2 { margin-right: 8px; }
#coreInputContainer .input-toolbar #voiceInputBtn { margin-left: 8px; }

/* ---------- 24) [Day22c] 输入框与背景的视觉关系（边框中性化 + 层级拉开 + 压缩为两层）----------
   24.1 边框/底色/微光。实测出的赢家链（改前现状）：
        · 底色 rgba(12,15,25,.90) —— 110 的 wallpaperThroughStyle：壁纸选择器 + #coreInputContainer（(2,1,0)，无 !important）
        · 边框 rgba(229,115,74,.28) 橙色 —— 同一条 110 规则（--wall-glow-rgb = 229,115,74 品牌橙）
        · 聚焦边框 rgb(229,115,74) + 无阴影 —— 135 自己的 §13 壁纸段规则
          （#app[data-wallpaper] #coreInputContainer:focus-within，特异性 20200 + !important，是本文件里最高的一条）
        因此本组用 (2,1,1)=20101（常态）与 (2,2,1)=20201（聚焦）压过上述全部；任务书只允许"用更高特异性在 135 里压住 110"，不改 110。
        色值取主题令牌并配任务书字面量回退：--border 深色即 rgba(255,255,255,0.08)、--border-hover 深色即 rgba(255,255,255,0.16)，
        浅色主题下自动换成深灰描边，不会出现"白底白边"。
   24.2 两层结构：模型行并入工具栏行最左（CSS order + flex-wrap，纯样式层，不改 DOM）。
        实测输入台高度 129px → 89px；窄窗（<1024px）退回原三层堆叠，避免工具栏被挤到破行。
   24.3 顶部 1px 内微光 = 模拟"浮起"（只做内阴影，不外发光，延续"去光晕"约束）。 */
html #app #coreInputContainer, html #coreInputContainer.core-input {
  border: 1px solid var(--day22d-input-border, rgba(255, 255, 255, 0.16)) !important;
  background-color: var(--day22d-input-fill, rgba(22, 22, 22, 0.92)) !important;
  background-image: none !important;
  -webkit-backdrop-filter: blur(12px) saturate(1.1) !important;
  backdrop-filter: blur(12px) saturate(1.1) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06) !important;
}
html #app #coreInputContainer:focus-within,
html #app[data-wallpaper] #coreInputContainer:focus-within,
html #coreInputContainer.core-input:focus-within {
  border-color: var(--day22d-input-border-focus, rgba(255, 255, 255, 0.20)) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06) !important;
}
/* 代码模式是功能状态指示（不是聚焦态），保留其既有强调边框 */
html #app #coreInputContainer.code-mode-active { border-color: var(--brand-text) !important; }
@media (min-width: 1024px) {
  #coreInputContainer { display: flex; flex-wrap: wrap; align-items: center; }
  #coreInputContainer > .ui6-input-row { order: 1; flex: 0 0 100%; padding: 0 12px; }
  #coreInputContainer > .input-toolbar-model { order: 2; flex: 0 1 auto; margin: 0 2px 0 12px; padding: 1px 8px; }
  #coreInputContainer > .input-toolbar-model .input-model-name { font-size: 11px; opacity: 0.8; }
  #coreInputContainer > .input-toolbar { order: 3; flex: 1 1 auto; border-top: none; margin-top: 0; padding: 0 12px 8px 0; }
  /* "Enter 发送 · Shift+Enter 换行"与输入框 placeholder 重复，让位给模型行 */
  #coreInputContainer > .input-toolbar .input-stats-hint { display: none; }
  #coreInputContainer > .input-stats { order: 4; }
}

/* ---------- 25) [Day22d] 深色壁纸态输入框强化 + 浅色主题层级系统 ----------
   25.1 输入框三个新令牌（深色默认值）：边框 .08→.12、聚焦 .16→.20、填充 →rgba(22,22,22,.92)，
        §24 的规则统一改为消费它们，深浅色只需换令牌值。blur(12px) 增强壁纸前的"实感"
        （§4 原有 blur(16px)，此处按本日任务书的取值收敛到 12px；机制本就存在，无新增依赖）。
   25.2 浅色令牌：一次把两套命名空间都改到同一套层级——
        · --color- 前缀一套：供 Tailwind 工具类（bg-surface- 系列 / text-text- 系列）与骨架消费；
        · --bg- / --border / --text- 一套：供 135 自身与各分片消费。
        （本条注释切勿写成"星号紧跟斜杠"的形式：会被 CSS 解析成注释提前结束，
          从而吞掉紧随其后的第一条规则——Day22e 排查到的真实故障。）
        改前实测：--color-bg-app 在浅色下仍是 #0a0a0a（00_theme 只有单一 :root，无浅色覆盖），
        --border 是 #d0d7de（偏蓝冷灰），主文字 #1f2328 偏深、三级文字 #8b949e 偏冷。
        本日改为中性灰阶：背景 #fafafa / 面板与卡片 #ffffff / 边框 rgba(0,0,0,.08) /
        边框强调 rgba(0,0,0,.16) / 主文字 #171717 / 次文字 #525252 / 三级文字 #8a8a8a。
        --color-border-hover、--color-border-strong 一并补上（改前浅色下不存在，00_theme 里也只有深色值）。
   25.3 浅色专属规则：欢迎语、快捷按钮边框与 hover、侧栏选中项（暖橙浅底）。
        输入框的浅色值走 25.1 的令牌（#ffffff 填充 / 黑 12% 与 20% 描边），与任务书一致。 */
/* 深色三态令牌不另立 :root 块：§24 的回退值即深色默认（边框 .16 / 聚焦 .20 / 填充 rgba(22,22,22,.92)），
   浅色值由下面的 html[data-theme="light"] 块覆盖即可（实测该块在深色下不参与、在浅色下生效）。
   注：本文件里"注释后紧跟的第一条规则"在浏览器 CSSOM 中会丢失（Day22e 实测：§25 的 :root、§26 的边框规则
   都被吞），故把跨主题的默认值写成 §24 的回退值，避免依赖会被吞掉的规则。 */
html[data-theme="light"] {
  --day22d-input-border: rgba(0, 0, 0, 0.12);
  --day22d-input-border-focus: rgba(0, 0, 0, 0.20);
  --day22d-input-fill: #ffffff;
  --color-bg-app: #fafafa;
  --color-bg-panel: #ffffff;
  --color-bg-card: #ffffff;
  --color-border: rgba(0, 0, 0, 0.08);
  --color-border-hover: rgba(0, 0, 0, 0.16);
  --color-border-strong: rgba(0, 0, 0, 0.16);
  --color-text-primary: #171717;
  --color-text-secondary: #525252;
  --color-text-tertiary: #8a8a8a;
  --bg-app: #fafafa;
  --bg-panel: #ffffff;
  --bg-card: #ffffff;
  --border: rgba(0, 0, 0, 0.08);
  --border-hover: rgba(0, 0, 0, 0.16);
  --border-subtle: rgba(0, 0, 0, 0.05);
  --text-primary: #171717;
  --text-secondary: #525252;
  --text-tertiary: #8a8a8a;
  --text-inverse: #ffffff;
}
html[data-theme="light"] #coreInputContainer,
html[data-theme="light"] #coreInputContainer.core-input {
  box-shadow: inset 0 1px 0 rgba(0, 0, 0, 0.03) !important;
}
html[data-theme="light"] #chatNormalContent .welcome-title { color: #171717; }
html[data-theme="light"] .welcome-suggestion,
html[data-theme="light"] #app .welcome-suggestion.welcome-suggestion { border-color: rgba(0, 0, 0, 0.08); }
html[data-theme="light"] .welcome-suggestion:hover,
html[data-theme="light"] #app .welcome-suggestion.welcome-suggestion:hover { border-color: rgba(0, 0, 0, 0.16); }
html[data-theme="light"] .session-item.active,
html[data-theme="light"] .list-item.active { background: rgba(229, 115, 74, 0.08); }
html[data-theme="dark"] #app[data-wallpaper="pure"] #coreInputContainer,
html[data-theme="dark"] #app[data-wallpaper="pure"] #coreInputContainer.core-input,
html[data-theme="dark"] #app:not([data-wallpaper="anime-starry"]):not([data-wallpaper="anime-aurora"]):not([data-wallpaper="anime-window"]):not([data-wallpaper="custom"]) #coreInputContainer {
  background-color: #1c1c1c !important;
}
/* ↑ 纯色态输入框不透明填充放在这里（紧跟一条已生效规则之后）：Day22e 实测"注释后紧跟的第一条规则"
   会从 CSSOM 里丢失，故把需要生效的规则排在规则之后、而不是注释之后。 */

/* ---------- 26) [Day22e] 纯色态层级感（输入框浮起 + 快捷按钮有边界）----------
   26.1 输入框填充：**纯色态**（非图片壁纸）改用不透明 #1c1c1c —— 在 #0a0a0a 底上抬两档，
        比半透明的 rgba(22,22,22,.92) 更"实"，也没有透出壁纸的问题。
        图片壁纸（anime 系列与 custom）刻意保留半透明 + blur(12px)：那里"浮起"靠的是毛玻璃（§24/§25），
        换成不透明会把 Day22d 的 backdrop-filter 变成死代码。
        边框已在 §24 的回退值里从 .12 提到 .16（两种深色态都受益）。
        选择器沿用 §11 的图片壁纸排除写法，并补到 (2,1,1)=20101 以压过 §24 的 (2,0,1)=20001。
        注意：注释里不要出现连续的星号加斜杠（会被当成注释提前结束、把紧随其后的规则吞掉）。
   26.2 快捷按钮（欢迎页 4 个入口）边框：深色态 .08 → .12、hover → .20，让它们"有边界"而不是
        飘在背景里的文字。用 html[data-theme="dark"] 前缀与本日 §25 的浅色规则各管一态，互不干扰；
        浅色态维持 Day22d 的黑 8% / hover 黑 16%。
        注：纯色态输入框的 #1c1c1c 填充写在 §25 末尾（规则紧跟在规则之后），原因见那里的注释。 */
html[data-theme="dark"] #app[data-wall-tone] main .welcome-suggestion,
html[data-theme="dark"] #app[data-wallpaper] main .welcome-suggestion,
html[data-theme="dark"] #app .welcome-suggestion.welcome-suggestion { border-color: rgba(255, 255, 255, 0.12); }
html[data-theme="dark"] #app[data-wall-tone] main .welcome-suggestion:hover,
html[data-theme="dark"] #app[data-wallpaper] main .welcome-suggestion:hover,
html[data-theme="dark"] #app .welcome-suggestion.welcome-suggestion:hover { border-color: rgba(255, 255, 255, 0.20); }

/* ---------- 27) [Day22f] AI 回复统一为纯文本流（修 §11 带来的两态外观不一致）----------
   背景：§11「基础态（未选图片壁纸）配色」把 .bg-surface-card 涂成卡片色
        （深色 var(--bg-card)，浅色 #ffffff，都是 !important），而它的作用域排除了图片壁纸
        → 同一个 AI 消息体在"纯色态"是灰底/白底卡片、在"壁纸态"是纯文本流，两态不一致。
   本条：对话页 AI 消息体（.msg-bubble 内的 .bg-surface-card，即 .msg-bubble__ai-box）在任何主题下透明，
        与 Day22a 定的「AI 纯文本流」形态统一。用户消息气泡走 .msg-bubble__user-box 自己的规则，不在范围内。
   特异性 (2,3,2)=20302，压过 §11 两条 (1,3,2)=10302 与 110 的壁纸态 ai-box 规则 (1,2,1)，同为 !important。
   范围核实（Day22f 实测）：一段含 AI 回复的会话里，该选择器只命中 1 个元素（AI 消息体），
        用户气泡不匹配；气泡内没有其它 .bg-surface-card 卡片。 */
html[data-theme] #app main #chatMessagesNormal .msg-bubble .bg-surface-card {
  background-color: transparent !important;
  /* [Day22g] 描边也透明：壁纸态下该元素描边色是 rgb(237,237,237) 的亮色细线（既有规则给的），
     与"纯文本流"目标矛盾；三态统一去掉。用户气泡是 .msg-bubble__user-box，不在本选择器范围内。 */
  border-color: transparent !important;
}

/* ---------- 28) [Day23] 设置页「执行引擎」卡（parts/85_engines.js 的展示层）----------
   28.1 每引擎一行 = 状态点 + 名称 + 原始 id + 能力标签 +（后端给了数字时才有）任务数。
        状态点色值按任务书固定：可用 #4ade80 / 不可用 #666 / 忙碌 #E5734A（脉冲）。
        刻意不复用骨架 .beacon-dot：本文件多处以同权重把 .beacon-dot 的 background 改成
        var(--brand)（857 / 1046 / 1179 / 1180 行），会盖掉状态色，故另立 .engine-dot。
   28.2 深浅两态：行填充走 --day23-engine-fill 令牌（深色回退 rgba(255,255,255,.03)，
        浅色在 html[data-theme="light"] 覆写为 rgba(0,0,0,.02)）；边框与文字沿用 §0 的
        --border / --text-* 系列，随主题自动切换，无需第二套规则。
   28.3 描述两行截断：zcode 的 description 含 Docker 隔离说明、较长，用 -webkit-line-clamp
        收敛为 2 行，完整文本留在 title 属性里（悬停可读，信息不丢）。
   28.4 动效降级：脉冲动画不在 §17 的 [class*="animate-"] 命中范围内，故就近自带
        prefers-reduced-motion 守卫，不改 §17 既有规则。 */
.engine-list { display: flex; flex-direction: column; gap: 8px; }
.engine-row {
  border: 1px solid var(--border);
  border-radius: var(--radius-item, 6px);
  padding: 8px 8px;
  background: var(--day23-engine-fill, rgba(255, 255, 255, 0.03));
}
.engine-head { display: flex; align-items: center; gap: 8px; }
.engine-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; flex-shrink: 0; }
.engine-dot.is-ok { background: #4ade80; }
.engine-dot.is-off { background: #666; }
.engine-dot.is-busy { background: #E5734A; animation: engineDotPulse 1.2s ease-in-out infinite; }
@keyframes engineDotPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
.engine-name { font-size: 12px; font-weight: 500; color: var(--text-primary); }
.engine-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; color: var(--text-tertiary); }
.engine-state { font-size: 10px; color: var(--text-tertiary); }
.engine-count { margin-left: auto; font-size: 10px; color: var(--text-tertiary); }
.engine-caps { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.engine-tag {
  /* [Day23b] 13px + 灰底为 Day23b 任务书指定参数；深浅两态沿用 28.2 的令牌覆写法 */
  font-size: 13px; line-height: 1; padding: 4px 8px; border-radius: 4px;
  border: 1px solid var(--border); color: var(--text-secondary);
  background: var(--day23-engine-tag-fill, rgba(255, 255, 255, 0.06));
}
.engine-tag--none { color: var(--text-tertiary); }
.engine-desc {
  margin-top: 8px; font-size: 10px; line-height: 1.6; color: var(--text-tertiary);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.engine-empty, .engine-loading {
  display: flex; align-items: center; gap: 8px; font-size: 11px;
  color: var(--text-secondary); padding: 8px 0;
}
.engine-hint { margin-top: 8px; font-size: 10px; line-height: 1.6; color: var(--text-tertiary); }
.engine-foot { display: flex; justify-content: flex-end; margin-top: 12px; }
/* [Day23c] 展开/收起：头部默认只留 状态点+引擎名+箭头，明细区由 85 的 .engine-detail 承载、
   is-open 类驱动显隐。箭头用纯 CSS 边框画，规避 lucide 把 i 换成 svg 的选择器陷阱；
   过渡只动 transform（合成器友好，不占主线程）。 */
.engine-head { cursor: pointer; user-select: none; }
.engine-chevron {
  margin-left: auto; width: 7px; height: 7px; flex-shrink: 0;
  border-right: 1.5px solid var(--text-tertiary); border-bottom: 1.5px solid var(--text-tertiary);
  transform: rotate(45deg); transition: transform 0.15s ease;
}
.engine-row.is-open .engine-chevron { transform: rotate(225deg); }
.engine-detail { display: none; }
.engine-row.is-open .engine-detail { display: block; }
.engine-meta { display: flex; align-items: center; gap: 12px; margin-top: 8px; flex-wrap: wrap; }
.engine-meta .engine-count { margin-left: 0; }
.engine-last { font-size: 10px; color: var(--text-tertiary); }
/* [Day23c] 刷新加载态（图标自旋）与失败红字；红色取 Tailwind red-500，深浅两主题均可读。 */
#enginesRefreshBtn.is-loading svg, #enginesRefreshBtn.is-loading i { animation: engineSpin 0.8s linear infinite; }
@keyframes engineSpin { to { transform: rotate(360deg); } }
.engine-error { margin-top: 8px; font-size: 10px; color: #ef4444; }
html[data-theme="light"] .engine-row { --day23-engine-fill: rgba(0, 0, 0, 0.02); }
html[data-theme="light"] .engine-tag { --day23-engine-tag-fill: rgba(0, 0, 0, 0.04); }
@media (prefers-reduced-motion: reduce) {
  .engine-dot.is-busy { animation: none; }
  .engine-chevron { transition: none; }
  #enginesRefreshBtn.is-loading svg, #enginesRefreshBtn.is-loading i { animation: none; }
}

/* ---------- 29) [Day24.5] Argus 研究进度条（确定性轮询的可见层）----------
   29.1 宿主与理由：面板由 parts/125_tools.js 的 ArgusWatcher 动态插到 #chat-normal 内、
        输入台 wrapper 之前（骨架 HTML 不改）。为什么不放进消息流：
        ① 进度是过程信息，不该落库成消息、也不进消息索引；
        ② 消息区会被 renderMessages 整表重绘，注入的节点随时会被抹掉；
        ③ 终态才由 _emit 贴报告（复用既有 buildMessageEl 管线），面板届时消失。
        因此在 DOM 上它是输入台的兄弟、滚动区之外——**始终可见**，不随消息滚动跑掉。
   29.2 三段式：头部一行（雷达图标 + 标题 + 问题 + 并行条数 + 已用时长/事件数）、
        中间一行真实进度文案（Argus 的 status.progress）、底部 2px 不定进度条。
   29.3 % 从哪来：Argus 只给"一句话进度 + 事件数"，没有百分比 —— 所以进度条做**不确定态**
        （横向滑动），不用假百分比骗人。这正是"不卡死"的视觉表达：一直在动。
   29.4 取色全部走 §0 令牌（--bg-card / --border / --text-* / --bg-active / --brand），
        深浅色与壁纸态自动跟随，本节的第二条规则只做浅色下的描边微调。
   29.5 动效降级：两条动画自带 prefers-reduced-motion 守卫（同 §28 的理由）。 */
#argusProgressPanel {
  flex: 0 0 auto;
  margin: 0 20px 8px;
  padding: 8px 12px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-btn, 6px);
  background-color: var(--bg-card);
  -webkit-backdrop-filter: blur(12px) saturate(1.1);
  backdrop-filter: blur(12px) saturate(1.1);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}
#argusProgressPanel .argus-progress-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
#argusProgressPanel .argus-progress-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; flex: 0 0 16px; color: var(--brand-text);
  animation: argusProgressPulse 1.8s ease-in-out infinite;
}
#argusProgressPanel .argus-progress-icon svg,
#argusProgressPanel .argus-progress-icon i { width: 14px; height: 14px; }
#argusProgressPanel .argus-progress-title { font-size: 11px; font-weight: 500; color: var(--text-primary); flex: 0 0 auto; }
#argusProgressPanel .argus-progress-question {
  font-size: 11px; color: var(--text-secondary); min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
#argusProgressPanel .argus-progress-more { font-size: 10px; color: var(--text-tertiary); flex: 0 0 auto; }
#argusProgressPanel .argus-progress-meta {
  margin-left: auto; font-size: 10px; color: var(--text-tertiary);
  font-variant-numeric: tabular-nums; flex: 0 0 auto;
}
#argusProgressPanel .argus-progress-text {
  margin-top: 4px; font-size: 11px; color: var(--text-tertiary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
#argusProgressPanel .argus-progress-bar {
  position: relative; margin-top: 8px; height: 2px; border-radius: 4px;
  background: var(--bg-active); overflow: hidden;
}
#argusProgressPanel .argus-progress-bar-fill {
  position: absolute; top: 0; left: 0; height: 100%; width: 32%; border-radius: 4px;
  background: var(--brand); animation: argusProgressSlide 1.6s ease-in-out infinite;
}
@keyframes argusProgressSlide { 0% { left: -32%; } 100% { left: 100%; } }
@keyframes argusProgressPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
html[data-theme="light"] #argusProgressPanel { box-shadow: none; }
@media (prefers-reduced-motion: reduce) {
#argusProgressPanel .argus-progress-icon { animation: none; }
#argusProgressPanel .argus-progress-bar-fill { animation: none; width: 100%; opacity: 0.5; }
}

/* ---------- 30) [A-1→A-3a] 圆角档位归一（4/6/8 + 999 胶囊） ----------
   A-3a 已在源头把骨架 @theme 的 --radius-xs..2xl 归到 4/6/6/8/8/8，rounded-xl / rounded-2xl
   工具类由源头生效 8px，这两条 !important 补丁退役（2026-10-02）。 */

/* ---------- 31) [A-1.5] 导航图标统一 18px ----------
   A-1 把骨架与 5 个运行时注入器的按钮图标都写成了 w-4(16px)；任务组/记忆库等注入器分片
   不在本轮改动清单内，故统一在 CSS 层收口回 18px（lucide 替换后的 svg 与未替换的 i 都盖住）。 */
aside .nav-icon-btn svg, aside .nav-icon-btn i { width: 18px !important; height: 18px !important; }

/* ---------- 32) [A-1.6] 中栏可折叠（默认收起；状态与按钮逻辑在 110 installMiddleSidebarCollapse） ----------
   body[data-mid-collapsed] 三态：'1' 收起 / '0' 展开 / 无属性 非五页（维持 A-1.5 现状）。
   宽度用 !important 压过 110 拖宽写的内联 width/flex-basis；overflow:hidden 令收起态把手不可见不可点。 */
#middleSidebar { position: relative; transition: width 0.15s ease, flex-basis 0.15s ease; }
body[data-mid-collapsed="1"] #middleSidebar {
  width: 0 !important; flex-basis: 0 !important;
  overflow: hidden; border-right-width: 0; pointer-events: none;
}
/* 折叠/展开按钮（lucide 图标，样式对齐 tool-btn：淡底+描边，无外发光） */
.mid-collapse-btn {
  position: absolute; z-index: 60; width: 32px; height: 32px; top: 12px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  border-radius: 6px; border: 1px solid var(--border); background: var(--bg-hover);
  color: var(--text-secondary); cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.mid-collapse-btn:hover { background: var(--bg-active); color: var(--text-primary); border-color: var(--border-hover); }
.mid-collapse-btn svg { width: 16px; height: 16px; }
#midExpandBtn { left: 208px; }                              /* 全局导航 200px + 8px 间隙 */
#midCollapseBtn { right: 10px; }
.mid-collapse-btn { display: none; } /* 默认隐藏，两态各开一个 */
/* 状态显隐：☰ 仅收起态、✕ 仅展开态；无属性（非五页）两个都不出现 */
body[data-mid-collapsed="1"] #midExpandBtn { display: flex; }
body[data-mid-collapsed="0"] #midCollapseBtn { display: flex; }
/* ☰ 悬浮在页头区左上：给页头内容让位（page 页头既有 <header> 也有 cost 运行时重建的 div.h-14） */
body[data-mid-collapsed="1"] .page-panel:not(.hidden) > header:first-child,
body[data-mid-collapsed="1"] .page-panel:not(.hidden) > .h-14:first-child { padding-left: 48px; }
/* ✕ 悬浮在中栏头部右上：给各侧栏面板头部的 +/上传按钮让位 */
#middleSidebar > .sidebar-panel > header:first-child { padding-right: 48px; }

/* ---------- 33) [A-2] 知识库单页 tab 化：tab 条样式 + 包壳后的避让补丁 ----------
   page-prompts/page-snippets 移入 #page-knowledge（含 #knowledgeTabBar 首行），
   sidebar-prompts/sidebar-snippets 移入 #sidebar-knowledge。原 pane/sidebar 类与 id
   均保留（110 A-1.6 折叠识别、40/50 渲染依赖），本节只补壳带来的一阶差异。 */
#knowledgeTabBar { height: 40px; padding: 0 20px; gap: 4px; flex-shrink: 0;
  border-bottom: 1px solid var(--border); }
.kb-tab { height: 26px; padding: 0 16px; border-radius: 6px; border: none;
  background: transparent; font-size: 12px; font-weight: 500; cursor: pointer;
  color: var(--text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease; }
.kb-tab:hover { background: var(--bg-hover); color: var(--text-primary); }
.kb-tab.active { background: var(--brand-light); color: var(--brand-text); font-weight: 600; }
html[data-theme="light"] .kb-tab.active { color: var(--text-primary); }
/* ☰ 悬浮在 tab 行左上时让位（对齐 §32 页头 48px 规则；tab 条非 header/.h-14 不被覆盖） */
body[data-mid-collapsed="1"] #page-knowledge > #knowledgeTabBar { padding-left: 48px; }
/* 侧栏包壳后 ✕ 避让规则（原为 #middleSidebar > .sidebar-panel > header 直子选择器）落不到
   内层面板头部，按 §32 原值补齐 */
#sidebar-knowledge > .sidebar-panel > header:first-child { padding-right: 48px; }
/* 会话搜索框放大镜重叠修复：骨架 .session-search { padding:8px 8px } 压过 Tailwind pl-8/pr-7
   （未分层静态样式赢 @layer 工具类 + Tailwind 运行时 style 在 head 中先于静态块注入），
   实际左距 8px < 放大镜右缘 24px（absolute left-2.5 + w-3.5）→ 文字钻镜下；右侧清除钮同理压 pr-7。
   ID 选择器（1-0-0）稳压 .session-search（0-1-0），恢复工具类原意；底边色为同源次生症状一并收。 */
#sessionSearchInput {
  padding-left: 32px;    /* = pl-8 原意；镜占 10~24px，留 8px 空隙 */
  padding-right: 28px;   /* = pr-7 原意；清除钮占 8~20px，留 8px 空隙 */
  border-bottom-color: var(--color-line-ghost, rgba(255,255,255,.08));
}

/* ---------- 34) [UI 2.0 步骤3] 输入台换肤：WorkBuddy 形态 ----------
   形态来源：根目录 ai-workbench-tailwind-orange.html L176-216（玻璃面板 32px 大圆角、
   文本域在上、工具行在下、胶囊模型选择器、42px 圆形渐变发送键、右对齐字数）。
   颜色按拍板走 MoRay 品牌橙（--brand #E5734A / --brand-hover #D9663B），不引入 Tailwind CDN。
   压盖策略（沿用 §24 的特异性战）：本节位于 135 样式字符串末尾，与 §24/§4 同档时后置胜；
   浅色/深色各用主题前缀升一档；深色背景要压过 §25 的 :not 长链 (2,5,1)，故用双 id 提到 (3,1,1)。
   双 id 只用于背景/描边/阴影；聚焦与代码模式紧随其后用同档规则补回品牌描边，功能态不丢。
   文本域只加 min-height，不动 rows（30_chat 的 input 监听仍用内联 height 自动增高）。 */

/* 34.0 工具行分隔线对齐原型（h-[18px] mx-2）；放在节首承接注释后可能丢失的首条规则 */
#coreInputContainer .input-toolbar-divider { height: 18px !important; margin: 0 8px !important; }

/* 34.1 形状层（跨主题）：32px 大圆角 + 玻璃模糊 + 过渡。背景色不在此层，避免无 data-theme 时闪白 */
html #app #coreInputContainer,
html #coreInputContainer.core-input {
  border-radius: 32px !important;
  background-image: none !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  transition: border-color 200ms ease, background-color 300ms ease, box-shadow 300ms ease;
}

/* 34.2 浅色玻璃：65% 白填充 + 白色高光描边 + 悬浮阴影（(2,1,1) 压过 §24 的 (2,0,1)） */
html[data-theme="light"] #app #coreInputContainer,
html[data-theme="light"] #coreInputContainer.core-input {
  border: 1px solid rgba(255, 255, 255, 0.40) !important;
  background-color: rgba(255, 255, 255, 0.65) !important;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.60) !important;
}

/* 34.3 深色玻璃：65% 深灰填充（双 id 压 §25 的不透明 #1c1c1c） */
html[data-theme="dark"] #app #coreInputContainer#coreInputContainer,
html[data-theme="dark"] #coreInputContainer#coreInputContainer.core-input {
  border: 1px solid rgba(255, 255, 255, 0.10) !important;
  background-color: rgba(30, 30, 30, 0.65) !important;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.30), inset 0 1px 0 rgba(255, 255, 255, 0.05) !important;
}

/* 34.4 功能态：聚焦/代码模式必须压过 34.3 的双 id，否则深色下聚焦边框不橙 */
html #app #coreInputContainer#coreInputContainer:focus-within,
html #coreInputContainer#coreInputContainer.core-input:focus-within {
  border-color: var(--brand) !important;
}
html #app #coreInputContainer#coreInputContainer.code-mode-active {
  border-color: var(--brand-text) !important;
}

/* 34.5 文本域：透明无框、15px/1.6、初始 72px（rows 不动，内联 height 自动增高保留） */
#chatInputNormal {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  font-size: 15px !important;
  line-height: 1.6 !important;
  min-height: 72px !important;
}

/* 34.6 工具行：去背景/去上边框（原型工具行与输入区同面板） */
#coreInputContainer .input-toolbar {
  background: transparent !important;
  border-top: none !important;
}
@media (max-width: 1023px) {
  #coreInputContainer > .input-toolbar { margin-top: 8px !important; }
}

/* 34.7 模型选择器：胶囊化（浅色白 80% / 深色白 10%） */
#coreInputContainer .input-toolbar-model {
  border-radius: 999px !important;
  background: rgba(255, 255, 255, 0.80) !important;
  border: 1px solid rgba(0, 0, 0, 0.06) !important;
  padding: 4px 12px !important;
  gap: 7px !important;
}
#coreInputContainer .input-toolbar-model:hover { background: #ffffff !important; }
html[data-theme="dark"] #coreInputContainer .input-toolbar-model {
  background: rgba(255, 255, 255, 0.10) !important;
  border-color: rgba(255, 255, 255, 0.06) !important;
}
html[data-theme="dark"] #coreInputContainer .input-toolbar-model:hover {
  background: rgba(255, 255, 255, 0.16) !important;
}

/* 34.8 工具按钮：34px 圆角 11px（原型 icon-btn） */
#coreInputContainer .input-toolbar-btn {
  width: 34px !important; height: 34px !important; border-radius: 11px !important;
}

/* 34.9 发送键：42px 渐变圆钮。width/height 不加 !important：
   125_tools 的工具忙碌态会写内联 width:auto + padding 显示"工具执行中…"，重要声明会压死它 */
#coreInputContainer > .input-toolbar .ui6-send,
#coreInputContainer button[onclick*="sendChatMessage"] {
  width: 42px;
  height: 42px;
  min-width: 42px;
  border-radius: 999px !important;
  background-image: linear-gradient(135deg, var(--brand), var(--brand-hover)) !important;
  color: var(--brand-on) !important;
  border: none !important;
  box-shadow: 0 4px 12px rgba(229, 115, 74, 0.30) !important;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}
#coreInputContainer > .input-toolbar .ui6-send:hover,
#coreInputContainer button[onclick*="sendChatMessage"]:hover {
  background-image: linear-gradient(135deg, var(--brand-hover), var(--brand)) !important;
  box-shadow: 0 6px 16px rgba(229, 115, 74, 0.40) !important;
  transform: translateY(-2px) scale(1.05) !important;
}

/* 34.10 字数统计：tabular-nums、弱化右对齐 */
#coreInputContainer .input-stats,
#coreInputContainer > .input-toolbar .input-stats-left { font-variant-numeric: tabular-nums; }
#coreInputContainer .input-stats { color: var(--text-tertiary) !important; margin-right: 12px; }

/* 34.11 桌面端把模型胶囊并回工具行（原型为同一行左组）；纯 order，不改 DOM。
   margin-bottom 14px 与工具行 padding-bottom 对齐，避免胶囊与 42px 发送键错位 */
@media (min-width: 1024px) {
  #coreInputContainer > .ui6-input-row { padding: 12px 8px 2px !important; }
  #coreInputContainer > .input-toolbar-model { order: 2; margin: 0 8px 14px 20px !important; }
  #coreInputContainer > .input-toolbar { order: 2; flex: 1 1 auto; padding: 0 20px 14px 0 !important; margin-top: 0 !important; }
  #coreInputContainer > .input-toolbar .input-toolbar-divider { order: -1; }
  #coreInputContainer > .input-stats { order: 4; }
}

/* ---------- 35) [UI 2.0 步骤4] 紧凑输入台 + 三栏玻璃化 ----------
   35.1 输入台压扁：textarea 72 → 44px，重排各层 padding。任务书给的
        #coreInputContainer { padding:12px 20px 8px 20px } 不能字面落地——容器本身无 padding，
        内层 §34.11 已按原型留边（行 8px + 内层 px-4 = 24px 左距、工具栏右 20px），
        再加 20px 会把文字左距推到 44px、面板反而变高；故容器 padding 归零，
        只在子层收紧（行 8px、工具栏右/下 16/10、胶囊左 16）。
   35.2 侧栏玻璃化权重：左栏被 §11 的 :not 长链 (1,6,2) !important 钉住，中栏被 110 的
        --wall-panel-alpha 0.96（pure 态）涂成实色；本节用 #app#app 提到 (2,*,*)、
        #middleSidebar 提到 (3,*,*) 才压得住。背景图一并清掉，避免壁纸层叠色。 */

/* 35.1 输入台紧凑化 */
#chatInputNormal {
  min-height: 44px !important;
  padding-top: 12px !important;
  padding-bottom: 4px !important;
}
#coreInputContainer { padding: 0 !important; }

/* 35.2 左侧栏 + 中栏：半透明玻璃（浅色 35% 白 / 深色 35% 深灰）。
   两个选择器分别覆盖 aside 与 #middleSidebar；border 只改颜色时保留原细线观感 */
html[data-theme="light"] #app#app > aside.bg-surface-deep,
html[data-theme="light"] #app#app > aside#middleSidebar {
  background-color: rgba(255, 255, 255, 0.35) !important;
  background-image: none !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  border-right: 1px solid rgba(0, 0, 0, 0.04) !important;
}
html[data-theme="dark"] #app#app > aside.bg-surface-deep,
html[data-theme="dark"] #app#app > aside#middleSidebar {
  background-color: rgba(20, 20, 20, 0.35) !important;
  background-image: none !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  border-right: 1px solid rgba(255, 255, 255, 0.05) !important;
}

/* 35.3 选中会话卡片：浅色改用半透明白，避免实色盖住玻璃 */
html[data-theme="light"] .session-item.active {
  background: rgba(255, 255, 255, 0.6) !important;
}

/* 35.4 桌面端同步收紧 §34.11 的留白（不改 order/DOM）；胶囊 margin-bottom 与工具栏
   padding-bottom 取同值，保持与 42px 发送键垂直居中 */
@media (min-width: 1024px) {
  #coreInputContainer > .ui6-input-row { padding: 8px 8px 0 !important; }
  #coreInputContainer > .input-toolbar-model { margin: 0 8px 10px 16px !important; }
  #coreInputContainer > .input-toolbar { padding: 0 16px 10px 0 !important; }
}
/* 窄屏堆叠态才需要把工具行从输入行下方拉开一点点；桌面端与胶囊同一行，不能加 top margin */
@media (max-width: 1023px) {
  #coreInputContainer > .input-toolbar { margin-top: 4px !important; }
}

/* ---------- 36) [UI 2.0 步骤5] 高级淡彩光晕底（纯色态） ----------
   36.1 作用域：只落在 data-wallpaper="pure"（或未设置）的 #app 上。
        任务书原始选择器 html[data-theme] #app 会连图片壁纸一起覆盖——background-image
        !important 会盖掉 110 的 url(wallpapers/wp*.jpg)，等于废掉星夜/极光/窗边；
        故显式收窄到纯色态，图片壁纸仍走原规则。
   36.2 权重：#app#app 提到 (2,2,1)，压过 §11 的 #app:not(...) (1,2,1) !important 底色。
   36.3 光晕直接画在 #app 上（它自带 relative/overflow），项目里没有独立遮罩层。 */

html[data-theme="light"] #app#app[data-wallpaper="pure"],
html[data-theme="light"] #app#app:not([data-wallpaper]) {
  background-color: #f4f5f8 !important;
  background-image:
    radial-gradient(circle at 15% 50%, rgba(200, 215, 255, 0.4), transparent 40%),
    radial-gradient(circle at 85% 30%, rgba(255, 230, 210, 0.4), transparent 40%),
    radial-gradient(circle at 50% 80%, rgba(240, 240, 245, 0.6), transparent 50%) !important;
  background-attachment: fixed !important;
}

html[data-theme="dark"] #app#app[data-wallpaper="pure"],
html[data-theme="dark"] #app#app:not([data-wallpaper]) {
  background-color: #121215 !important;
  background-image:
    radial-gradient(circle at 15% 50%, rgba(40, 50, 90, 0.4), transparent 40%),
    radial-gradient(circle at 85% 30%, rgba(90, 50, 40, 0.3), transparent 40%),
    radial-gradient(circle at 50% 80%, rgba(20, 20, 25, 0.8), transparent 50%) !important;
  background-attachment: fixed !important;
}

/* 36.4 纯色态若未来出现独立遮罩层（当前骨架无 .wallpaper-layer / #wallpaperContainer），
   一律透明让光晕透出；现有 DOM 不匹配，属防御性空规则 */
#app[data-wallpaper="pure"] .wallpaper-layer,
#app[data-wallpaper="pure"] #wallpaperContainer {
  opacity: 0 !important;
  pointer-events: none !important;
}

/* ---------- 37) [UI 2.0 步骤6] 去边框化与纯净化 ----------
   37.1 竖线：§35 用 html[data-theme=...] #app#app > aside... (2,2,2)/(3,1,2) !important
        写了 border-right；这里必须同档后置才压得动（低权重的 #app > aside 会被 §35 反压）。
   37.2 顶栏：真实结构是 .page-panel > header（border-b 半透明线），任务书猜的
        #app > header / .top-bar / .tab-bar / #tabsContainer 在项目里都不存在；
        中栏 .sidebar-panel > header 同理去线，展开时与主区一致。
   37.3 顶部"Tab 栏"真身是 .mode-segment（普通对话/多模型对比分段器），去容器底色，
        active 药丸保留（否则失去当前模式指示）。
   37.4 快捷指令按钮真身是 #chatNormalContent .welcome-suggestion（不是 suggestion/quick 类）；
        按真实类名精准替换，禁用 [class*=quick] 通配以免误伤其他按钮。 */

/* 37.1 侧栏/主区去竖线；主区用极淡投影替代边框 */
html[data-theme] #app#app > aside.bg-surface-deep,
html[data-theme] #app#app > aside#middleSidebar {
  border-right: none !important;
  border-left: none !important;
}
#app > main { border-right: none !important; border-left: none !important; }
html[data-theme="light"] #app > main { box-shadow: -4px 0 16px rgba(0, 0, 0, 0.02) !important; }
html[data-theme="dark"] #app > main { box-shadow: -4px 0 16px rgba(0, 0, 0, 0.20) !important; }

/* 37.2 顶部 header / 中栏面板头：去底色、去分界线 */
#app .page-panel > header,
#middleSidebar .sidebar-panel > header {
  background: transparent !important;
  border-bottom: none !important;
  box-shadow: none !important;
}

/* 37.3 顶部分段器去容器底色（active 药丸保留） */
#app .mode-segment {
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
}

/* 37.4 输入台模型胶囊：纯文字化（去 §34.7 的白底与描边）。
   暗色 §34.7 是 (1,2,0)，必须同档后置才压得住 */
html[data-theme="light"] #coreInputContainer .input-toolbar-model,
html[data-theme="dark"] #coreInputContainer .input-toolbar-model {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}
#coreInputContainer .input-toolbar-model:hover {
  background: rgba(0, 0, 0, 0.04) !important;
}
html[data-theme="dark"] #coreInputContainer .input-toolbar-model:hover {
  background: rgba(255, 255, 255, 0.06) !important;
}

/* 37.5 快捷指令：玻璃底 + 微投影，去硬边框。
   真身：30_chat 把欢迎页的 .welcome-suggestions 搬进输入台底部容器并挂 id=welcomeQuickRow，
   所以不在 #chatNormalContent 里；两处都写上，兼容未搬移的旧渲染路径 */
#welcomeQuickRow .welcome-suggestion,
#chatNormalContent .welcome-suggestion {
  background: rgba(255, 255, 255, 0.80) !important;
  border: none !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
}
#welcomeQuickRow .welcome-suggestion:hover,
#chatNormalContent .welcome-suggestion:hover {
  background: rgba(255, 255, 255, 0.95) !important;
}
html[data-theme="dark"] #welcomeQuickRow .welcome-suggestion,
html[data-theme="dark"] #chatNormalContent .welcome-suggestion {
  background: rgba(255, 255, 255, 0.10) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.20) !important;
}
html[data-theme="dark"] #welcomeQuickRow .welcome-suggestion:hover,
html[data-theme="dark"] #chatNormalContent .welcome-suggestion:hover {
  background: rgba(255, 255, 255, 0.16) !important;
}

/* 37.6 浅色态输入文字加深，提高对比度 */
html[data-theme="light"] #chatInputNormal {
  color: #111111 !important;
}

/* ---------- 38) [UI 2.0 步骤9] 品牌色全局切换（WorkBuddy 蓝） ----------
   38.1 令牌层：主色 #2f7cf6。任务书只覆盖 brand/hover/text/on/accent，漏了
        --brand-light 与 --glow-blue（仍是橙色 rgba(229,115,74,...)）——不补的话
        所有选中底、徽章、发送键内发光会继续发橙。
   38.2 深色档 --brand-text 用 #60a5fa：任务书统一给 #1e40af，深底深蓝看不清；
        其自己的深色侧栏规则也是 #60a5fa，故按该意图分主题。
   38.3 其余为写死橙色的高频可见落点：发送键渐变/阴影、纯色光晕、slash 菜单橙色图标底。 */

/* 38.1 令牌：浅色 / 默认 */
:root,
html[data-theme="light"] {
  --brand: #2f7cf6 !important;
  --brand-hover: #1d4ed8 !important;
  --brand-light: rgba(47, 124, 246, 0.10) !important;
  --brand-text: #1e40af !important;
  --brand-on: #ffffff !important;
  --accent: #2f7cf6 !important;
  --glow-blue: rgba(47, 124, 246, 0.25) !important;
  /* Tailwind v4 运行时把这三个工具类色钉成橙色字面量（--brand-text 变了它们不跟），
     必须直接覆盖 --color-brand-* 才能让 text-brand-cyan/violet、bg-brand-cyan/15 等变蓝 */
  --color-brand-cobalt: var(--brand-text) !important;
  --color-brand-cobalt-hover: var(--brand-hover) !important;
  --color-brand-violet: var(--brand-text) !important;
  --color-brand-cyan: var(--brand-text) !important;
}
/* 38.1b 令牌：深色（文字提亮） */
html[data-theme="dark"] {
  --brand: #2f7cf6 !important;
  --brand-hover: #1d4ed8 !important;
  --brand-light: rgba(47, 124, 246, 0.18) !important;
  --brand-text: #60a5fa !important;
  --brand-on: #ffffff !important;
  --accent: #2f7cf6 !important;
  --glow-blue: rgba(47, 124, 246, 0.35) !important;
  --color-brand-cobalt: var(--brand-text) !important;
  --color-brand-cobalt-hover: var(--brand-hover) !important;
  --color-brand-violet: var(--brand-text) !important;
  --color-brand-cyan: var(--brand-text) !important;
}

/* 38.2 发送键：蓝色渐变 + 蓝光（覆盖 §34.9 的橙色） */
#coreInputContainer button[onclick*="sendChatMessage"],
#coreInputContainer > .input-toolbar .ui6-send {
  background-image: linear-gradient(135deg, #2f7cf6, #1d4ed8) !important;
  box-shadow: 0 4px 12px rgba(47, 124, 246, 0.30) !important;
}
#coreInputContainer button[onclick*="sendChatMessage"]:hover,
#coreInputContainer > .input-toolbar .ui6-send:hover {
  background-image: linear-gradient(135deg, #1d4ed8, #2f7cf6) !important;
  box-shadow: 0 6px 16px rgba(47, 124, 246, 0.40) !important;
}

/* 38.3 侧栏选中态：真实类名 .nav-icon-btn.active；任务书的 #app > aside .active 一并保留 */
html[data-theme="light"] #app > aside .nav-icon-btn.active,
html[data-theme="light"] #app > aside .active,
html[data-theme="light"] .session-item.active {
  background: rgba(47, 124, 246, 0.10) !important;
  color: #1e40af !important;
}
html[data-theme="dark"] #app > aside .nav-icon-btn.active,
html[data-theme="dark"] #app > aside .active,
html[data-theme="dark"] .session-item.active {
  background: rgba(47, 124, 246, 0.20) !important;
  color: #60a5fa !important;
}

/* 38.4 纯色光晕改冷蓝（覆盖 §36，作用域保持一致） */
html[data-theme="light"] #app#app[data-wallpaper="pure"],
html[data-theme="light"] #app#app:not([data-wallpaper]) {
  background-image:
    radial-gradient(circle at 15% 50%, rgba(200, 215, 255, 0.5), transparent 40%),
    radial-gradient(circle at 85% 30%, rgba(47, 124, 246, 0.1), transparent 40%),
    radial-gradient(circle at 50% 80%, rgba(240, 240, 245, 0.6), transparent 50%) !important;
}
html[data-theme="dark"] #app#app[data-wallpaper="pure"],
html[data-theme="dark"] #app#app:not([data-wallpaper]) {
  background-image:
    radial-gradient(circle at 15% 50%, rgba(30, 64, 175, 0.2), transparent 40%),
    radial-gradient(circle at 85% 30%, rgba(47, 124, 246, 0.1), transparent 40%),
    radial-gradient(circle at 50% 80%, rgba(20, 20, 25, 0.8), transparent 50%) !important;
}

/* 38.5 slash 菜单里写死橙底的图标：只改橙，保留黄/绿/红命令色 */
#coreInputContainer .slash-menu-item-icon[style*="229,115,74"] {
  background: rgba(47, 124, 246, 0.12) !important;
}

/* 38.6 Firefox 滚动条通道色（Electron 走 ::-webkit-scrollbar，不受影响） */
* { scrollbar-color: rgba(47, 124, 246, 0.25) transparent; }

/* 38.7 beacon 呼吸环：动画值会压过普通 box-shadow，必须重定义同名关键帧（后者生效），
   否则状态点本体是蓝的、脉冲环仍是橙色 */
@keyframes beacon {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(47, 124, 246, 0.45); }
  50%      { opacity: 0.65; box-shadow: 0 0 0 4px rgba(47, 124, 246, 0); }
}

/* ---------- 39) [UI 2.0 最后补丁] 输入台 ON 态按钮转蓝 ----------
   39.1 真凶：125_tools.js 在运行时 append 一个 <style>，把 .native-agent-btn.native-on
        与 .think-mode-btn.think-on 写成橙色 rgba(229,115,74,.15)/#E5734A/描边 .35；
        它是运行时插入的，普通类选择器压不住，必须带 #coreInputContainer 升权 + !important。
   39.2 任务书把 .restart-btn / #restartAllServices 也写在本节——那是 Electron 壳的元素
        （index.html #btn-restart），网页端不存在；已在 electron-app/src/style.css 落地。 */

#coreInputContainer .native-agent-btn.native-on,
#coreInputContainer .think-mode-btn.think-on,
#coreInputContainer .tools-toggle-btn.tools-on,
#coreInputContainer .input-toolbar-btn.active {
  background: rgba(47, 124, 246, 0.15) !important;
  color: #2f7cf6 !important;
  box-shadow: inset 0 0 0 1px rgba(47, 124, 246, 0.35) !important;
}
html[data-theme="dark"] #coreInputContainer .native-agent-btn.native-on,
html[data-theme="dark"] #coreInputContainer .think-mode-btn.think-on,
html[data-theme="dark"] #coreInputContainer .tools-toggle-btn.tools-on,
html[data-theme="dark"] #coreInputContainer .input-toolbar-btn.active {
  background: rgba(47, 124, 246, 0.22) !important;
  color: #60a5fa !important;
  box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.40) !important;
}

/* ---------- 40) [UI 2.0 步骤11] 页面级残留橙色清理 ----------
   40.1 根因：应用中心（86_home_apps.js）与 Synapse（140_synapse.js）的图标/胶囊/徽章
        全部消费 var(--color-brand, #E5734A) 与 var(--color-brand-subtle, rgba(229,115,74,.08))；
        而 --color-brand 在主题块里被定义成橙色字面量（组装产物 L5383），
        改 --brand / --brand-text 它们不跟。直接覆盖这两个变量即可让两页整体转蓝。
   40.2 仅剩一处写死字面量：.argus-mode-pill:hover 的 rgba(229,115,74,.16)，单独兜底。
   40.3 状态色（成功绿/警告黄/失败红）不动，只清品牌橙。 */

:root,
html[data-theme="light"] {
  --color-brand: #2f7cf6 !important;
  --color-brand-hover: #1d4ed8 !important;
  --color-brand-subtle: rgba(47, 124, 246, 0.10) !important;
}
html[data-theme="dark"] {
  --color-brand: #2f7cf6 !important;
  --color-brand-hover: #1d4ed8 !important;
  --color-brand-subtle: rgba(47, 124, 246, 0.18) !important;
}

/* 40.2 写死橙色兜底（应用中心 Argus 模式胶囊 hover） */
.argus-mode-pill:hover { background: rgba(47, 124, 246, 0.16) !important; }
`;
  document.head.appendChild(st);
})();
