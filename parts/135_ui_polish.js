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
/* ---------- [批次修复 #4] 附件“仅存本机”角标 ---------- */
.local-only-badge {
  font-size: 9px; line-height: 1.2; padding: 1px 5px; border-radius: 8px;
  color: var(--color-text-secondary, #9aa3b8);
  border: 1px solid rgba(120,140,180,.3); background: rgba(120,140,180,.08);
  cursor: help; white-space: nowrap;
}

/* ---------- A. 通知：壁纸下可读 scrim / 状态色 / 进度条 / 右上安全边距 ---------- */
.notification-container { top: 16px; right: 16px; max-width: min(400px, calc(100vw - 32px)); }
.notification--polished {
  position: relative; overflow: hidden;
  background: linear-gradient(180deg, rgba(16,19,29,.92), rgba(12,14,22,.95));
  -webkit-backdrop-filter: blur(14px) saturate(1.2); backdrop-filter: blur(14px) saturate(1.2);
  border: 1px solid rgba(120,140,180,.28);
  border-left: 3px solid var(--color-text-tertiary, #616c82);
  border-radius: 12px; box-shadow: 0 10px 30px rgba(0,0,0,.45);
  padding-right: 14px;
}
[data-theme="light"] .notification--polished {
  background: linear-gradient(180deg, rgba(250,251,253,.94), rgba(240,243,248,.96));
  border-color: rgba(90,110,150,.3); box-shadow: 0 10px 28px rgba(30,40,70,.18);
}
.notification--polished.type-success { border-left-color: var(--color-success, #3fd68f); }
.notification--polished.type-error   { border-left-color: var(--color-danger, #ff5c6c); }
.notification--polished.type-warning { border-left-color: var(--color-warning, #fbbf24); }
.notification--polished.type-info    { border-left-color: var(--color-brand-cobalt, #5b8cff); }
.notification--polished .notification-icon.success { color: var(--color-success, #3fd68f); }
.notification--polished .notification-icon.error   { color: var(--color-danger, #ff5c6c); }
.notification--polished .notification-icon.warning { color: var(--color-warning, #fbbf24); }
.notification--polished .notification-icon.info    { color: var(--color-brand-cobalt, #5b8cff); }
.notification--polished .notification-close {
  background: transparent; border: 0; color: var(--color-text-tertiary, #616c82);
  cursor: pointer; padding: 4px; border-radius: 6px; line-height: 0;
}
.notification--polished .notification-close:hover { color: var(--color-text-primary, #e5e7eb); background: rgba(255,255,255,.08); }
.notification--polished .notification-progress {
  position: absolute; left: 0; bottom: 0; height: 2px; width: 100%;
  background: var(--color-brand-cyan, #3ad6e8); opacity: .55;
  animation-name: notif-progress; animation-timing-function: linear; animation-fill-mode: forwards;
}
.notification--polished.paused .notification-progress { animation-play-state: paused; }
@keyframes notif-progress { from { width: 100%; } to { width: 0%; } }
.notification--polished.closing { opacity: 0; transform: translateX(16px); transition: opacity .2s ease-out, transform .2s ease-out; }

/* ---------- B. 浮层在壁纸亮部的统一 scrim：模态/下拉/确认/命令面板/右键菜单 ---------- */
.modal-box, .confirm-box, .command-palette, .context-menu {
  background: linear-gradient(180deg, rgba(17,20,31,.94), rgba(13,15,24,.97)) !important;
  -webkit-backdrop-filter: blur(18px) saturate(1.25) !important; backdrop-filter: blur(18px) saturate(1.25) !important;
  border: 1px solid rgba(120,140,180,.28) !important;
}
[data-theme="light"] .modal-box, [data-theme="light"] .confirm-box,
[data-theme="light"] .command-palette, [data-theme="light"] .context-menu {
  background: linear-gradient(180deg, rgba(250,251,253,.96), rgba(241,244,249,.98)) !important;
}
.modal-overlay, .confirm-overlay { background: rgba(4,6,12,.55) !important; -webkit-backdrop-filter: blur(3px) !important; backdrop-filter: blur(3px) !important; }
[data-theme="light"] .modal-overlay, [data-theme="light"] .confirm-overlay { background: rgba(30,40,60,.35) !important; }
.input-toolbar, .input-container, .col-messages .msg-bubble > div { -webkit-backdrop-filter: blur(12px) !important; backdrop-filter: blur(12px) !important; }

/* ---------- B/G. 焦点环 / 滚动条 / 过渡统一 ---------- */
button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, [tabindex]:focus-visible, a:focus-visible {
  outline: 2px solid rgba(91,140,255,.65); outline-offset: 2px; border-radius: 6px;
}
* { scrollbar-width: thin; scrollbar-color: rgba(120,140,180,.35) transparent; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: rgba(120,140,180,.35); border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: rgba(120,140,180,.55); }
::-webkit-scrollbar-track { background: transparent; }
button, .toggle-track, input, select, textarea, .btn-ghost, .btn-primary { transition: background-color .16s ease-out, border-color .16s ease-out, color .16s ease-out, box-shadow .16s ease-out, opacity .16s ease-out; }
/* [UI3] 按下反馈：瞬时缩小+提亮（禁用不触发）；确认类按钮带 0.12s 过渡 */
button:not(:disabled):active, .btn:not(:disabled):active, [role="button"]:not(:disabled):active { transform: scale(.97); filter: brightness(1.12); transition: transform .12s ease-out, filter .12s ease-out; }
button:disabled, .btn:disabled, [role="button"][aria-disabled="true"] { opacity: .38; cursor: not-allowed; filter: grayscale(.35); }

/* ---------- C. 网格页自适应 + 内容区居中 ---------- */
#page-prompts .flex-1 > div, #page-snippets .flex-1 > div, #page-docs .flex-1 > div { max-width: 1560px; margin-left: auto; margin-right: auto; width: 100%; }
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

/* ---------- D. 输入台：分组分隔 / 横向滚动（UI6 三层化后 ≤640 折行接管，此处不再 !important 钉死） / ON 态 glow ---------- */
.input-toolbar { overflow-x: auto; overflow-y: hidden; scrollbar-width: none; flex-wrap: nowrap; }
.input-toolbar::-webkit-scrollbar { display: none; }
.input-toolbar > * { flex-shrink: 0; }
.native-agent-btn.native-on {
  box-shadow: inset 0 0 0 1px rgba(157,123,255,.5), 0 0 10px rgba(157,123,255,.28);
}
.native-agent-btn:not(.native-on) { opacity: .62; }
.native-agent-btn:not(.native-on):hover { opacity: 1; }
/* [批次修复 #1] 后端离线：本机 Agent 开关与入口置灰 */
.native-agent-btn.native-offline { opacity: .38; cursor: not-allowed; filter: grayscale(.4); }
.native-agent-btn.native-offline:hover { opacity: .45; }

/* ---------- F. Agent 组件精修 ---------- */
/* 欢迎页快捷卡片精修：图标底统一、hover 抬升、间距统一；Agent 引导卡高亮 */
.welcome-suggestions { gap: 10px; }
.welcome-suggestion {
  border: 1px solid rgba(120,140,180,.22);
  background: linear-gradient(180deg, rgba(20,24,36,.66), rgba(14,17,28,.78));
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  border-radius: 12px;
  transition: transform .16s ease-out, border-color .16s ease-out, box-shadow .16s ease-out;
}
[data-theme="light"] .welcome-suggestion { background: rgba(250,251,253,.9); border-color: rgba(90,110,150,.25); }
.welcome-suggestion:hover { transform: translateY(-2px); border-color: rgba(91,140,255,.5); box-shadow: 0 6px 20px rgba(40,60,120,.25); }
.welcome-suggestion--agent { border-color: rgba(63,214,143,.45); }
.welcome-suggestion--agent:hover { border-color: rgba(63,214,143,.8); box-shadow: 0 6px 20px rgba(40,120,60,.22); }
.welcome-suggestion-icon { display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0; }
/* Agent 开关“引导脉冲”（一次性高亮 2 秒） */
.native-agent-btn.native-on-pulse { animation: nativePulse 0.9s ease-out 2; }
@keyframes nativePulse {
  0%, 100% { box-shadow: inset 0 0 0 1px rgba(157,123,255,.5), 0 0 10px rgba(157,123,255,.28); }
  50% { box-shadow: inset 0 0 0 1px rgba(157,123,255,.85), 0 0 22px rgba(157,123,255,.55); }
}
/* 欢迎页垂直重心上移 */
.welcome-screen { padding-top: 6vh !important; justify-content: flex-start !important; }

/* 计划卡：整体可折叠（点击标题切换），进行步高亮由 JS 控制类 */
.plan-card { cursor: default; }
.plan-card .plan-steps { display: block; }
.plan-card.collapsed .plan-steps { display: none; }
.plan-card .plan-head { cursor: pointer; user-select: none; }
.plan-card .plan-step-row.running { background: rgba(91,140,255,.08); border-radius: 6px; }
/* 时间线：等宽字体块、耗时右对齐（.tool-step 结构由 125_tools.js 生成） */
.tool-step summary .text-text-tertiary:not(.shrink-0) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.tool-step { transition: box-shadow .18s ease-out; }
.tool-step[open] { box-shadow: 0 2px 12px rgba(0,0,0,.18); }
/* 审批卡：目标路径/命令与 diff 已为等宽；主次按钮层级 */
[data-agent-approve-ok] { min-width: 108px; }
[data-agent-approve-no] { min-width: 84px; }
/* 文件树侧栏：拖拽把手 */
#wsSidebar .ws-resize-handle {
  position: absolute; left: -5px; top: 0; bottom: 0; width: 10px; cursor: col-resize; z-index: 2;
}
#wsSidebar .ws-tree-row .ws-caret { transition: transform .16s ease-out; }
#wsSidebar .ws-tree-row .ws-caret.open { transform: rotate(90deg); }
#wsSidebar .ws-empty { text-align: center; padding: 26px 10px; color: var(--color-text-tertiary, #616c82); }
#wsSidebar .ws-empty .lucide { width: 26px; height: 26px; opacity: .5; margin-bottom: 8px; }

/* ---------- B. 空状态统一 ---------- */
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 16px; color: var(--color-text-tertiary, #616c82); }
.empty-state .lucide { width: 30px; height: 30px; opacity: .45; margin-bottom: 10px; }
.empty-state .empty-title { font-size: 12px; color: var(--color-text-secondary, #9aa3b8); margin-bottom: 4px; }
.empty-state .empty-hint { font-size: 10px; }

/* ---------- G. prefers-reduced-motion：关闭呼吸/脉冲/位移动画 ---------- */
@media (prefers-reduced-motion: reduce) {
  .animate-pulse, .animate-spin, .beacon-dot, .stream-cursor, .notification-progress,
  .plan-card .animate-pulse, [class*="animate-"] { animation: none !important; }
  .msg-enter, .msg-fade, .tool-step, .notification, .plan-card { transition: none !important; animation: none !important; }
  * { scroll-behavior: auto !important; }
}
/* ---------- [批次修复 #20] 窄屏/触控：fixed 背景退化时防布局溢出（随内容滚动） ---------- */
@media (max-width: 900px) {
  body, #app, .aurora-bg { background-attachment: scroll !important; }
}

/* ---------- [UI4] 顶栏窄屏适配（≤767px）：同步滚动文字隐藏（保留圆点/开关）、
   会话名省略截断、分段控制器不压缩保持完整可点 ---------- */
@media (max-width: 767px) {
  /* UI4: 对比模式同步滚动文字隐藏（保留圆点与开关） */
  #chat-compare .h-9 > div:last-child > span { display: none; }
  #chat-compare .h-9 > div:last-child { gap: 6px; }
  /* UI5: 主顶栏可换行两行布局——会话名（含 ellipsis）第一行，分段控制器独占第二行右对齐 */
  #page-chat > header {
    flex-wrap: wrap; height: auto; min-height: 56px;
    align-content: center; row-gap: 4px;
  }
  #chatTitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #page-chat > header > div:first-child { min-width: 0; flex: 1 1 auto; }
  .mode-segment {
    flex-basis: 100%; justify-content: flex-end; flex-shrink: 0;
    order: 2; margin-left: 0; padding-left: 0;
  }
}
@media (max-width: 900px) {
  body, #app, .aurora-bg { background-attachment: scroll !important; }
}
/* ---------- [UI5] 窄屏会话栏抽屉兜底：<1024px 时会话栏脱离文档流（fixed），
   顶栏获得完整宽度（骨架窄屏规则 computed 失效；!important 兜底，桌面不受影响） ---------- */
@media (max-width: 1023px) {
  body.narrow-screen aside:nth-of-type(2) {
    position: fixed !important;
    left: 0; top: 0; bottom: 0; z-index: 70;
    transform: translateX(-100%);
    transition: transform 200ms ease-in-out;
    box-shadow: 0 0 40px rgba(0,0,0,.5);
  }
  body.narrow-screen.drawer-open aside:nth-of-type(2) { transform: none; }
}

/* ---------- [UI6] 输入台三层结构（模型行 / 输入区 / 工具行） ---------- */
/* 层1 模型选择行：独立首行，左侧小标签（不再与工具按钮同排） */
#coreInputContainer > .input-toolbar-model {
  max-width: none; width: fit-content;
  margin: 3px 12px 0; padding: 2px 8px;
}
/* 层2 输入区：独占一行 */
#coreInputContainer > .ui6-input-row { padding: 2px 12px 6px; }
/* 层3 底部工具行：左=工具按钮 / 右=统计+发送；顶部分隔 */
#coreInputContainer > .input-toolbar {
  padding: 4px 12px 12px;
  border-top: 1px solid var(--color-line-ghost, rgba(120,140,180,.16));
  margin-top: 2px;
}
/* 统计（字/token）并入工具行右组 */
#coreInputContainer > .input-toolbar .input-stats-left {
  margin-left: auto; gap: 10px; font-size: 12px;
  color: var(--color-text-tertiary, #9aa3b8);
}
#coreInputContainer > .input-toolbar .input-stats-left .value {
  color: var(--color-text-secondary, #9aa3b8);
}
#coreInputContainer > .input-toolbar .input-stats-hint {
  margin-left: 6px; font-size: 12px;
  color: var(--color-text-tertiary, #9aa3b8); opacity: .72;
}
/* 代码模式徽章：激活时自然流显示于统计组左邻（原贴右规则在右组并入后改为 4px） */
#coreInputContainer > .input-toolbar .code-mode-badge { margin-left: 4px; }
/* 圆形发送按钮：36px，hover 提亮 + 钴蓝光晕 */
#coreInputContainer > .input-toolbar .ui6-send { margin-left: 4px; flex-shrink: 0; }
#coreInputContainer > .input-toolbar .ui6-send:hover {
  background: var(--color-brand-cobalt-hover, #6f9dff);
  box-shadow: 0 0 0 1px rgba(91,140,255,.55), 0 0 20px rgba(91,140,255,.45);
  transform: translateY(-1px);
}
/* 统计原容器（内容已并入工具行）空壳隐藏 */
#coreInputContainer > .input-stats { display: none; }
/* 极窄屏：隐藏 Enter 快捷键提示；工具行允许折行——工具一行，统计+发送右对齐换行，
   发送按钮始终保持在右下角完整可见可点（不裁剪） */
@media (max-width: 640px) {
  #coreInputContainer .input-stats-hint { display: none; }
  #coreInputContainer > .input-toolbar { flex-wrap: wrap; row-gap: 6px; }
}

/* ==================== [UI7 通宵精修：视觉打磨（D1-D6，纯样式）] ==================== */

/* D1 输入区：聚焦光核描边增强（代码模式激活态优先不被覆盖） */
#coreInputContainer { transition: box-shadow .25s ease; }
#coreInputContainer:not(.code-mode-active):focus-within {
  box-shadow: 0 0 0 1px rgba(91,140,255,.45), 0 0 26px rgba(91,140,255,.20);
}
#coreInputContainer > .input-toolbar .ui6-send {
  transition: background-color .15s ease, box-shadow .2s ease, transform .12s ease, filter .15s ease;
}
#coreInputContainer > .input-toolbar .ui6-send:active { transform: scale(.9); filter: brightness(1.18); }

/* D2 消息气泡：AI 气泡叠极淡品牌蓝底（区分层级；色卡质感保留），代码块复制钮 hover 常显 */
.msg-bubble__ai-box {
  background-image: linear-gradient(0deg, rgba(91,140,255,.05), rgba(91,140,255,.05));
}
.code-block .code-copy-btn { opacity: .85; transition: opacity .15s ease, color .15s ease; }
.code-block:hover .code-copy-btn { opacity: 1; }

/* D3 侧栏：会话项过渡顺滑（200ms ease），选中态内描边轻提 */
.session-item {
  transition: background-color .18s ease, padding-left .18s ease, border-color .18s ease;
}
.session-item.active {
  box-shadow: inset 0 0 0 1px rgba(91,140,255,.22);
}

/* D5 打字指示器：三点跳动改品牌钴蓝（动画 timing 沿用骨架 typingBounce） */
.typing-dot { background: #5B8CFF; }

/* D6 空状态：图标底圈衬底，更聚焦 */
.empty-state .lucide {
  padding: 8px; border-radius: 12px;
  background: rgba(120,140,180,.07); box-sizing: content-box;
}

/* D8 360px 功能页响应式补漏（实测溢出：snippets/docs/automation/settings/cost/prompts 头部或卡片） */
@media (max-width: 640px) {
  /* 功能页顶栏允许两行（解除 h-14 定高挤压） */
  #page-prompts > header, #page-snippets > header,
  #page-docs > .h-14, #page-automation > .h-14, #page-cost > .h-14 {
    height: auto; min-height: 56px; flex-wrap: wrap;
    align-content: center; row-gap: 2px;
  }
  #page-prompts > header { padding: 4px 12px; }
  #page-prompts > header > div:first-child { flex: 1 1 auto; min-width: 0; }
  #page-prompts > header > div:last-child { margin-left: auto; }
  /* snippets：搜索独占一行，语言选择与新增按钮第二行 */
  #page-snippets > header { padding: 4px 12px; row-gap: 4px; }
  #page-snippets > header > div:first-child { flex: 1 1 100%; flex-wrap: wrap; }
  #page-snippets > header .w-56 { width: 100%; }
  #page-snippets > header select { flex: 1 1 120px; max-width: 160px; }
  #page-snippets > header > button { margin-left: auto; }
  /* docs/cost/automation：右组收缩换行右对齐 */
  #page-docs > .h-14 > div:last-child, #page-cost > .h-14 > div:last-child,
  #page-automation > .h-14 > div:last-child {
    margin-left: auto; flex-wrap: wrap; justify-content: flex-end; row-gap: 4px;
  }
  #page-docs > .h-14 .w-56 { width: auto; min-width: 0; flex: 1 1 150px; }
  /* automation 卡片单列（卡内操作按钮不再被裁） */
  #page-automation .grid.grid-cols-2 { grid-template-columns: 1fr !important; }
  /* settings 行折行（标签与控件分两行，select 不被挤出视口） */
  #page-settings .glass-card .flex.items-center.justify-between,
  #page-settings .glass-card .flex.items-center.gap-2 { flex-wrap: wrap; row-gap: 6px; }
}
`;
  document.head.appendChild(st);
})();
