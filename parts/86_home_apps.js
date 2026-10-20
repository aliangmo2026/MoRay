/* ============================================================
   [Day24.12] 应用中心 —— 独立主页 + 7 张生态卡（基础版）
   - 架构决策：应用中心 = MoRay 打开的第一屏（bootApp 末步 mount 后切入）
   - 本轮点击行为：MoRay/Argus 卡 → 对话页（普通模式；Argus 模式切换在 Day24.13 补）；
     ZCode 卡 → 设置页「执行引擎」卡；其余 4 张 = 灰态「待开发」提示
   - 实现约束：不动骨架 —— 导航按钮（nav 首位）与页面容器（page-cost 之后）均由本分片
     运行时注入，与 68/69 同一做法；页面切换依赖 68 的 document 级收敛监听兜底
     （幽灵页面/双高亮），自带 switchTo 供启动期程序化进入（69 同款实现）。
   - [M4a 2026-10-02] MemoryBridge 卡翻绿 → 跳「记忆库」页（MorayMemoryBrowser，
     bootApp memory-browser 步挂载；按钮缺失时如实提示模块未挂载）。
   - [Synapse v2 2026-10-06] Synapse 卡翻绿 → 跳「Synapse 群聊」页（SynapseChatApp 在
     140 分片 DOMContentLoaded 自挂载；按钮缺失时如实提示模块未挂载，goMemoryCard 同款）。
   - 样式：全部走 00_theme 令牌（--color-* / --radius-* / --space-*），深浅主题自适应；
     hover 只改 1px 边框色（暖橙），禁外发光。
   ============================================================ */

const HomeAppsApp = (() => {

  const PAGE = 'apps';

  /** 7 张生态卡。status: 'ok'=已接入(绿) / 'soon'=待开发(灰)。
      action: 'chat'=回对话页 | 'engines'=设置页引擎面板 | null=待开发 */
  const APPS = [
    { id: 'moray',        name: 'MoRay',        icon: 'bot',       status: 'ok',
      desc: '本地 AI 开发者工作台：对话、多模型对比、提示词库与工作流', action: 'chat' },
    { id: 'argus',        name: 'Argus',        icon: 'telescope', status: 'ok',
      desc: '深度研究智能体：自动检索、交叉验证并产出报告', action: 'argus' },
    { id: 'zcode',        name: 'ZCode',        icon: 'terminal',  status: 'ok',
      desc: '编码执行引擎：Docker 隔离沙箱内执行本机任务', action: 'engines' },
    { id: 'iris',         name: 'Iris',         icon: 'eye',       status: 'ok',
      desc: '报错截图修复：OCR 识别 · 根因诊断 · 修复代码', action: 'iris' },
    { id: 'synapse',      name: 'Synapse',      icon: 'share-2',   status: 'ok',
      desc: '多智能体协同与消息总线', action: 'synapse' },
    { id: 'cloudpilot',   name: 'CloudPilot',   icon: 'cloud',     status: 'soon', desc: '云端资源编排与一键部署', action: null },
    { id: 'memorybridge', name: 'MemoryBridge', icon: 'brain',     status: 'ok',   desc: '跨应用长期记忆同步', action: 'memory' }
  ];

  /* ---------------- 样式（本页私有；令牌化，深浅主题自适应） ---------------- */

  function ensureStyle() {
    if (document.getElementById('morayHomeAppsStyle')) return;
    const el = document.createElement('style');
    el.id = 'morayHomeAppsStyle';
    // 中栏收敛与 135 对 #page-settings 的做法同款（body:has 一行），
    // 目标状态互斥（apps / settings 不同时可见），与 135 规则无级联冲突。
    el.textContent = `
body:has(#page-apps:not(.hidden)) #middleSidebar { display: none; }
.homeapps-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: var(--space-card-gap, 12px); }
.homeapps-card {
  display: flex; flex-direction: column; align-items: flex-start; gap: 8px;
  padding: 16px; text-align: left; font: inherit; color: inherit;
  background: var(--color-bg-card, #1a1a1a);
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  border-radius: var(--radius-card, 8px);
  cursor: pointer; transition: border-color 0.15s ease;
}
.homeapps-card:not(.homeapps-card--soon):hover,
.homeapps-card:not(.homeapps-card--soon):focus-visible {
  border-color: var(--color-brand, #E5734A); outline: none;
}
.homeapps-card--soon { cursor: not-allowed; opacity: 0.75; }
.homeapps-icon {
  width: 36px; height: 36px; border-radius: var(--radius-control, 6px);
  display: flex; align-items: center; justify-content: center;
  background: var(--color-brand-subtle, rgba(229,115,74,0.08));
  color: var(--color-brand, #E5734A);
}
.homeapps-card--soon .homeapps-icon {
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  color: var(--color-text-tertiary, #666666);
}
.homeapps-name { font-size: var(--font-size-md, 14px); font-weight: 600; color: var(--color-text-primary, #ededed); }
.homeapps-desc { font-size: var(--font-size-xs, 12px); line-height: 1.5; color: var(--color-text-secondary, #a3a3a3); min-height: 2.9em; }
.homeapps-status { display: inline-flex; align-items: center; gap: 8px; margin-top: auto; font-size: 11px; color: var(--color-text-secondary, #a3a3a3); }
.homeapps-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.homeapps-dot--ok { background: var(--color-success, #4ade80); }
.homeapps-dot--soon { background: var(--color-text-tertiary, #666666); }
.homeapps-flash { border-color: var(--color-brand, #E5734A) !important; }
.argus-mode-pill {
  display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0;
  padding: 2px 12px; border-radius: 999px; font-size: 11px; font-weight: 500;
  color: var(--color-brand, #E5734A);
  background: var(--color-brand-subtle, rgba(229,115,74,0.08));
  border: 1px solid var(--color-brand, #E5734A);
  cursor: pointer; transition: background 0.15s ease; font-family: inherit;
}
.argus-mode-pill:hover { background: rgba(229, 115, 74, 0.16); }
.argus-mode-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-brand, #E5734A); flex-shrink: 0; }
`;
    document.head.appendChild(el);
  }

  /* ---------------- 注入：导航按钮（nav 首位）+ 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    // [A-1] 插入骨架「工作台」分组容器、对话之前；容器缺失时回退旧的 chat 同级插入
    const group = document.querySelector('.nav-group[data-nav-group="work"]');
    const chatBtn = document.querySelector('.nav-icon-btn[data-page="chat"]');
    const nav = group || (chatBtn ? chatBtn.parentElement : null);
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', '应用中心');
    btn.innerHTML = '<i data-lucide="layout-grid" class="w-4 h-4"></i><span class="nav-icon-label">应用中心</span>';
    if (chatBtn && chatBtn.parentElement === nav) nav.insertBefore(btn, chatBtn);
    else nav.insertBefore(btn, nav.firstChild);
    btn.addEventListener('click', () => { switchTo(PAGE); });
  }

  function ensurePage() {
    if (document.getElementById('page-' + PAGE)) return;
    const ref = document.getElementById('page-cost');
    if (!ref || !ref.parentElement) return;
    const div = document.createElement('div');
    div.id = 'page-' + PAGE;
    div.className = 'page-panel hidden flex-1 flex flex-col min-h-0';
    ref.parentElement.insertBefore(div, ref.nextSibling);
  }

  /* ---------------- 页面渲染 ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page || page.querySelector('#homeAppsGrid')) return;
    const okCount = APPS.filter(a => a.status === 'ok').length;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="layout-grid" class="w-4 h-4 text-brand-cobalt flex-shrink-0"></i>
          <span class="text-sm font-medium text-text-primary">应用中心</span>
          <span class="text-[11px] text-text-tertiary truncate">本地生态 · ${okCount} / ${APPS.length} 个已接入</span>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-5xl mx-auto">
          <div class="text-[11px] text-text-tertiary mb-3">点击卡片进入对应应用</div>
          <div class="homeapps-grid" id="homeAppsGrid"></div>
        </div>
      </div>`;
    const grid = page.querySelector('#homeAppsGrid');
    grid.innerHTML = APPS.map(appHtml).join('');
  }

  /** 单卡 HTML。待开发卡保持可点击（拿 pointer 展示提示），仅视觉灰态 + aria-disabled */
  function appHtml(app) {
    const soon = app.status === 'soon';
    return '<button type="button" class="homeapps-card' + (soon ? ' homeapps-card--soon' : '') + '"' +
      ' data-app-id="' + app.id + '"' + (soon ? ' aria-disabled="true"' : '') + '>' +
      '<span class="homeapps-icon"><i data-lucide="' + app.icon + '" class="w-5 h-5"></i></span>' +
      '<span class="homeapps-name">' + app.name + '</span>' +
      '<span class="homeapps-desc">' + app.desc + '</span>' +
      '<span class="homeapps-status"><span class="homeapps-dot homeapps-dot--' + app.status + '"></span>' +
      (soon ? '待开发' : '已接入') + '</span>' +
      '</button>';
  }

  function bindCardEvents() {
    const grid = document.getElementById('homeAppsGrid');
    if (!grid || grid.dataset.bound === '1') return;
    grid.dataset.bound = '1';
    grid.addEventListener('click', (e) => {
      const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
      let card = null;
      for (let i = 0; i < path.length; i++) {
        const n = path[i];
        if (n && n.nodeType === 1 && n.classList && n.classList.contains('homeapps-card')) { card = n; break; }
      }
      if (!card) return;
      const app = APPS.find(a => a.id === card.dataset.appId);
      if (!app) return;
      if (!app.action) {
        if (typeof showNotification === 'function') {
          showNotification('待开发', app.name + ' 尚在规划中，敬请期待', 'info', 2200);
        }
        return;
      }
      if (app.action === 'chat') goChat();
      else if (app.action === 'argus') enterArgusFromCard();
      else if (app.action === 'engines') goEnginesCard();
      else if (app.action === 'memory') goMemoryCard();
      else if (app.action === 'synapse') goSynapseCard();
    });
  }

  /* ---------------- 跳转行为 ---------------- */

  /** 回对话页（普通模式）。走导航按钮 click：骨架处理器 + 68 收敛监听都会正确生效。
      [Day24.13] 若 Argus 模式激活则先退出（MoRay 卡 = 明确回普通对话） */
  function goChat() {
    if (window.MorayArgusMode && window.MorayArgusMode.isActive()) window.MorayArgusMode.exit('moray-card');
    chatNavClick();
  }

  /** 裸跳对话页（不碰 Argus 模式；Argus 卡的"进模式+跳页"用这条） */
  function chatNavClick() {
    const btn = document.querySelector('.nav-icon-btn[data-page="chat"]');
    if (btn) btn.click();
  }

  /** [Day24.13] Argus 卡：进 Argus 模式 + 跳对话页 */
  function enterArgusFromCard() {
    if (window.MorayArgusMode) window.MorayArgusMode.enter('home-card');
    chatNavClick();
  }

  /** 跳设置页并定位「执行引擎」卡。设置页与引擎卡在 bootApp 即构建（70 step settings-ui /
      enhance），通常已存在；保险起见轮询 ≤3s，出现即平滑滚动 + 1.5s 边框高亮（只改 1px
      边框色，禁外发光）。 */
  function goEnginesCard() {
    const btn = document.querySelector('.nav-icon-btn[data-page="settings"]');
    if (!btn) return;
    btn.click();
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      const card = document.getElementById('enginesSettingsCard');
      if (card) {
        clearInterval(timer);
        const reduce = (typeof MoraySettings !== 'undefined' && MoraySettings.get('reduceMotion'));
        card.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
        card.classList.add('homeapps-flash');
        setTimeout(() => card.classList.remove('homeapps-flash'), 1500);
        return;
      }
      if (tries >= 20) clearInterval(timer);
    }, 150);
  }

  /** [M4a] MemoryBridge 卡：跳「记忆库」页（MorayMemoryBrowser 在 bootApp memory-browser
      步挂载，正常时先于用户点击就绪）。按钮缺失 = 模块挂载失败，如实提示，不假装成功 */
  function goMemoryCard() {
    const btn = document.querySelector('.nav-icon-btn[data-page="memory"]');
    if (btn) { btn.click(); return; }
    if (typeof showNotification === 'function') {
      showNotification('记忆库不可用', '记忆浏览器模块未挂载（查看启动日志 memory-browser 步）', 'warning', 3000);
    }
  }

  /** [Synapse v2] Synapse 卡：跳「Synapse 群聊」页（SynapseChatApp 在 140 分片
      DOMContentLoaded 自挂载，正常时先于用户点击就绪）。按钮缺失 = 模块挂载失败，
      如实提示，不假装成功（goMemoryCard 同款）。 */
  function goSynapseCard() {
    const btn = document.querySelector('.nav-icon-btn[data-page="synapse"]');
    if (btn) { btn.click(); return; }
    if (typeof showNotification === 'function') {
      showNotification('Synapse 不可用', '群聊页模块未挂载（检查 parts/140_synapse.js 是否参与构建）', 'warning', 3000);
    }
  }

  /* ---------------- 页面切换（69 同款实现；供启动期程序化进入） ---------------- */

  function switchTo(page) {
    document.querySelectorAll('.nav-icon-btn[data-page]').forEach(b => {
      b.classList.toggle('active', b.dataset.page === page);
    });
    document.querySelectorAll('.page-panel').forEach(p => {
      p.classList.add('hidden');
      p.classList.remove('flex');
    });
    const target = document.getElementById('page-' + page);
    if (target) {
      target.classList.remove('hidden');
      target.classList.add('flex');
      void target.offsetWidth;
    }
    document.querySelectorAll('.sidebar-panel').forEach(p => p.classList.add('hidden'));
    const sb = document.getElementById('sidebar-' + page);
    if (sb) { sb.classList.remove('hidden'); sb.classList.add('flex'); }
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 挂载（70 bootApp 末步调用；幂等） ---------------- */

  /** [Day24.14] opts.landing：启动落点（'apps' 默认 | 'chat' 跳过首屏）。
      仅决定最后的 switchTo 目标——导航入口/页面容器/卡片等核心挂载逻辑照常执行，
      应用中心在任何取值下都保持可达。 */
  function mount(opts) {
    ensureStyle();
    ensureNavButton();
    ensurePage();
    buildPage();
    bindCardEvents();
    if (typeof refreshIcons === 'function') refreshIcons();
    const landing = (opts && opts.landing === 'chat') ? 'chat' : PAGE;
    switchTo(landing);
  }

  return { mount: mount, switchTo: switchTo, goChat: goChat, PAGE: PAGE };
})();
window.HomeAppsApp = HomeAppsApp;

/* ============================================================
   [Day24.13] Argus 模式 —— 应用中心 Argus 卡的"进模式"体验
   - 状态：内存布尔（刷新即回普通模式，无持久化）
   - 进入：Argus 卡 / enter()；退出：点顶栏标签 / 输入 /exit / MoRay 卡
   - 发送路由：sendUserMessage（30_chat）在用户消息落库后、调主 LLM 前查
     isActive() → launchResearch() 直接 ToolRegistry.run('detective_research')，
     复用 125 的工具体（同问去重/orchestrate）与 Day24.5 的轮询面板+自动贴报告。
   - 依赖时序：本分片在 125_tools 之前加载，ToolRegistry / ArgusWatcher /
     showNotification 一律在函数体内运行时引用（挂载/点击时均已就绪）。
   ============================================================ */

const MorayArgusMode = (() => {

  let active = false;
  let pillEl = null;

  /** 顶栏模式标签：插在 #chatTitle 之后（header 左组，tokenMeter 之前）。
      只建一次；点击 = 退出模式。 */
  function ensurePill() {
    if (pillEl && pillEl.isConnected) return pillEl;
    const title = document.getElementById('chatTitle');
    if (!title || !title.parentElement) return null;
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.id = 'argusModePill';
    pill.className = 'argus-mode-pill';
    pill.title = 'Argus 模式已开启：发送的问题将直接交给 Argus 深度研究。点击退出';
    pill.innerHTML = '<span class="argus-mode-dot"></span>Argus 模式';
    pill.addEventListener('click', () => { exit('pill'); });
    title.insertAdjacentElement('afterend', pill);
    pillEl = pill;
    return pill;
  }

  /** 按激活态同步标签可见性（内联 style，避开 Tailwind .hidden 的级联歧义） */
  function syncPill() {
    const pill = ensurePill();
    if (pill) pill.style.display = active ? 'inline-flex' : 'none';
  }

  /** 进入 Argus 模式
      @param {string} [from] - 触发来源（home-card 等，仅用于日志） */
  function enter(from) {
    if (active) { chatNavFocus(); return; }
    active = true;
    syncPill();
    if (typeof showNotification === 'function') {
      showNotification('已进入 Argus 模式',
        '接下来发送的问题会直接交给 Argus 深度研究（无需再写"用 Argus"）。点顶部标签或输入 /exit 可退出',
        'info', 5000);
    }
    console.log('[MoRay] Argus 模式已开启' + (from ? '（来源：' + from + '）' : ''));
  }

  /** 退出 Argus 模式
      @param {string} [from] - 触发来源（pill / slash / moray-card，仅用于日志） */
  function exit(from) {
    if (!active) return;
    active = false;
    syncPill();
    if (typeof showNotification === 'function') {
      showNotification('已退出 Argus 模式', '回普通对话', 'info', 2200);
    }
    console.log('[MoRay] Argus 模式已退出' + (from ? '（来源：' + from + '）' : ''));
  }

  /** 模式是否激活
      @returns {boolean} */
  function isActive() { return active; }

  /** 兼容重复点击 Argus 卡：已在模式里时只把页切回对话页 */
  function chatNavFocus() {
    const btn = document.querySelector('.nav-icon-btn[data-page="chat"]');
    if (btn) btn.click();
  }

  /** [改动 C] Argus 模式下的发送路由：绕过主 LLM，直接构造 detective_research 调用。
      用户消息此时已落库并渲染（由 sendUserMessage 完成），本函数只负责起研究 +
      即时反馈；进度面板与报告自动贴入由 Day24.5 的 ArgusWatcher 链路承接。
      @param {Object} conv - 当前会话
      @param {string} question - 用户消息原文（即研究问题）
      @returns {Promise<void>} */
  async function launchResearch(conv, question) {
    const q = String(question || '').trim();
    if (!q) return;
    // 工具总开关关闭时 run() 会返回「未启用」——提前给出可执行指引，不让用户猜
    if (typeof ToolRegistry === 'undefined' || !ToolRegistry.isEnabled('detective_research')) {
      if (typeof showNotification === 'function') {
        showNotification('无法启动 Argus 研究',
          '需要先开启「工具调用」总开关（输入台扳手图标或设置 → AI 工具），再重新发送',
          'warning', 6000);
      }
      return;
    }
    AppState.generating = true; // 复用发送守卫：直发期间不允许连发（genController 保持 null，停止按钮为安全空操作）
    try {
      const res = await ToolRegistry.run('detective_research', { question: q }, {});
      if (res && res.ok && res.data && res.data.session_id) {
        const reused = res.data.reused === true;
        if (typeof showNotification === 'function') {
          showNotification(reused ? '该问题已有研究在进行' : 'Argus 研究已启动',
            (reused ? '复用进行中的研究：' : '研究已在后台开始：') + '「' + q.slice(0, 30) + (q.length > 30 ? '…' : '') + '」。进度见输入台上方面板，报告完成后自动贴入本对话',
            'success', 5000);
        }
      } else if (res && res.ok && res.data && res.data.denied) {
        if (typeof showNotification === 'function') {
          showNotification('研究请求被拒绝', String((res.data && res.data.reason) || '未说明原因'), 'warning', 5000);
        }
      } else {
        if (typeof showNotification === 'function') {
          showNotification('研究启动失败', String((res && res.error) || '未知错误'), 'error', 5000);
        }
      }
    } catch (e) {
      if (typeof showNotification === 'function') {
        showNotification('研究启动失败', String((e && e.message) || e).slice(0, 120), 'error', 5000);
      }
    } finally {
      AppState.generating = false;
    }
  }

  return { enter: enter, exit: exit, isActive: isActive, launchResearch: launchResearch };
})();
window.MorayArgusMode = MorayArgusMode;
