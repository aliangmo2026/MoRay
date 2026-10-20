/* ============================================================
   [Day26 · chat-rails v1 · 2026-10-09] 对话页右侧标签面板：面板壳 + 记忆召回标签
   ------------------------------------------------------------
   设计定稿（第一段侦察裁定）：
     · 形态：固定 400px + 可折叠 + 默认收起；推式（flex 兄弟），非遮罩浮层。
     · 标签：记忆召回 / 工具链 / 浏览器 三个 tab —— 本波只实现"记忆召回"，
       另两个 tab 显示"下一轮落地"占位（工具链、浏览器各在后续轮次）。
     · 快捷键 Ctrl+J（中栏 Ctrl+B 的对偶；已核实无既有绑定）。
     · 89 面板互斥：右栏展开时 body.chat-rails-open，CSS 强制隐藏
       #morayZcodeStreamPanel（89 分片零改动，工单改动4·方案A）。
   本分片红线（工单第二段）：
     · 完全不改 moray-workbench.html 骨架、不改 30_chat.js / 89_zcode_stream.js。
     · 包裹只发生在 #page-chat 一层：把 #chat-normal / #chat-compare 原样移入
       #chatRailsStage（flex-row）——#chat-normal 内部结构零变动，因此
       30_chat applyEmptyStateLayout 的 chatNormal.lastElementChild=输入台 契约
       （30_chat.js:2498）、135 §23 `#chat-normal:has(#welcomeScreen) > #chatNormalContent`
       直接子选择器（135_ui_polish.js:1489-1490）、135:1113 `#page-chat > header`
       全部保持成立；.chat-mode 模式切换走全局后代选择器（product:5078）亦不受影响。
     · 记忆召回数据源 = AppState.messages 中每条 AI 消息的
       msg.memoryRecall = {items, injectedCount}（与气泡底部胶囊同源，IndexedDB
       持久化，30_chat.js:1437/1676）；渲染复用 window.memoryRecallHtml
       （30_chat.js:957，同脚本顶层函数声明即全局）。纯前端读取，零后端调用。
     · 与气泡胶囊共存（裁定4）：胶囊=当轮就地看，面板=本会话汇总，不删不改胶囊。
   ------------------------------------------------------------
   第二段落地（2026-10-09 · 工具链标签，本会话口径 = 与记忆召回一致）：
     · 数据源同 AppState.messages 纯前端读，零后端调用：
       - 本机 Agent 轮次 = assistantMsg.toolCalls（30_chat.js:1837 落库，
         步骤形 [{name,label,args,status,resultPreview,ms}]，125_tools.js:222）；
       - ZCode 轮次 = kind='zcode-execution' 消息的 msg.execution.steps
         （92_laya_agent.js，步骤形 {l,c,s,i,t,fp}）。
     · Agent 卡体复用全局 renderToolSteps（125_tools.js:733；其折叠/展开交互为
       文档级委托 125:357，pane 内即插即用），但剥离"重试该步"重放按钮——
       本 tab 是只读回顾视图，不出现任何真执行入口；复制按钮保留。
     · ZCode 卡体自写紧凑行（l/c/s/i 色码缩进语义；粒度=msg.execution.steps，
       审计级明细归 138）；renderToolSteps 不可用时 Agent 行同样降级自写。
     · 刷新：setTab / setOpen / MutationObserver 三处统一 refreshActive() 按 _tab
       分发；renderTools 检出 running 轮次挂 2s 轻轮询（签名无变化 4 分钟或
       终态/收起/切 tab 即摘，防僵尸 interval）。
     · 89/92/30/125/后端零改动；浏览器 tab 仍为占位（下一轮）。
   ------------------------------------------------------------
   第三段落地（2026-10-09 · 浏览器标签 · 路线 A iframe 最小雏形）：
     · pane 内 iframe + 地址栏（回车/打开 → 校验 http/https → src）+ ↻ 重载；
     · 非法协议（javascript:/data:/file:/其他 scheme）拒绝 + 红字提示；无 scheme
       自动补 http://；外站被 X-Frame-Options 拒为 iframe 固有限制（接受，不硬修）。
     · sandbox = allow-scripts allow-same-origin allow-forms allow-popups；
       不加 allow-top-navigation（防逃逸）；保留 allow-same-origin（:8000 内嵌
       自身服务页需同源 cookie）。
     · pane DOM 常驻 → 切 tab 不清 iframe（URL/滚动状态天然保留）。
     · 本段不含：快捷按钮/历史/收藏/标签页、"系统浏览器打开"（要改壳）、webview。
   ============================================================ */
(function () {
  'use strict';

  var STYLE_ID = 'morayChatRailsStyle';
  var STAGE_ID = 'chatRailsStage';
  var RAILS_ID = 'chatRails';
  var TOGGLE_BTN_ID = 'chatRailsToggleBtn';
  var LS_KEY = 'moray.chatRails.collapsed';
  var OPEN_CLASS = 'chat-rails-open';
  var TABS = [
    { id: 'memory', label: '记忆召回' },
    { id: 'tools', label: '工具链' },
    { id: 'browser', label: '浏览器' }
  ];

  /** 当前激活 tab（内存态，不持久化——本波约定每次展开回到记忆召回） @type {string} */
  var _tab = 'memory';

  /* ---------------- 样式（一次性注入；令牌化颜色 + 回退值，同 140 模式） ---------------- */

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var el = document.createElement('style');
    el.id = STYLE_ID;
    el.textContent = [
      /* 舞台：#page-chat 的 flex 子项，行向排列 = 对话区 + 右栏 */
      '#' + STAGE_ID + '{display:flex;flex-direction:row;flex:1;min-height:0;min-width:0}',
      '#' + STAGE_ID + '>.chat-mode{flex:1;min-width:0}',
      /* 右栏：400px 固定宽，默认 display:none（收起），body.chat-rails-open 展开 */
      '#' + RAILS_ID + '{display:none;width:400px;flex-shrink:0;flex-direction:column;',
      'min-height:0;border-left:1px solid var(--color-line-ghost,rgba(255,255,255,.08));',
      'background:var(--color-bg-panel,rgba(255,255,255,.02));color:var(--color-text-primary,#e5e5e5)}',
      'body.' + OPEN_CLASS + ' #' + RAILS_ID + '{display:flex}',
      /* 改动4·方案A：右栏展开时 89 任务流浮层（position:fixed;right:0;320px）收起，
         右栏收起时自动恢复——只在本分片加 CSS，89 分片零改动 */
      'body.' + OPEN_CLASS + ' #morayZcodeStreamPanel{display:none!important}',
      /* 栏内头部：三 tab + 收起按钮 */
      '.cr-head{display:flex;align-items:center;gap:6px;padding:10px 12px;flex-shrink:0;',
      'border-bottom:1px solid var(--color-line-ghost,rgba(255,255,255,.08))}',
      '.cr-tabs{display:flex;align-items:center;gap:4px;flex:1;min-width:0;flex-wrap:wrap}',
      '.cr-tab{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;',
      'font-size:11px;cursor:pointer;color:var(--color-text-secondary,#a3a3a3);',
      'background:transparent;border:1px solid var(--color-line-ghost,rgba(255,255,255,.08));',
      'transition:border-color .15s ease,color .15s ease}',
      '.cr-tab:hover{border-color:var(--color-brand,#E5734A)}',
      '.cr-tab.active{color:var(--color-brand,#E5734A);border-color:var(--color-brand,#E5734A);',
      'background:var(--color-brand-subtle,rgba(229,115,74,.08))}',
      '.cr-collapse{width:22px;height:22px;flex-shrink:0;border:1px solid var(--color-line-ghost,rgba(255,255,255,.12));',
      'border-radius:6px;background:transparent;color:var(--color-text-tertiary,#666);cursor:pointer;',
      'display:inline-flex;align-items:center;justify-content:center;padding:0}',
      '.cr-collapse:hover{color:var(--color-text-primary,#e5e5e5)}',
      /* 栏体与面板 */
      '.cr-body{flex:1;min-height:0;overflow-y:auto;padding:10px 12px 14px}',
      '.cr-pane{display:none}.cr-pane.active{display:block}',
      '.cr-placeholder{padding:32px 8px;text-align:center;font-size:12px;',
      'color:var(--color-text-tertiary,#666)}',
      /* 记忆召回轮次卡 */
      '.cr-round{border:1px solid var(--color-line-ghost,rgba(255,255,255,.08));border-radius:8px;',
      'padding:8px 10px;margin-bottom:8px;background:var(--color-bg-hover,rgba(255,255,255,.02))}',
      '.cr-round-head{display:flex;align-items:baseline;gap:8px;margin-bottom:2px}',
      '.cr-round-time{font-size:10px;color:var(--color-text-tertiary,#666);flex-shrink:0;',
      'font-family:ui-monospace,Consolas,monospace}',
      '.cr-round-q{font-size:11px;color:var(--color-text-secondary,#a3a3a3);min-width:0;',
      'overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.cr-empty{padding:36px 8px;text-align:center;font-size:12px;color:var(--color-text-tertiary,#666);',
      'line-height:1.8}',
      '.cr-hint{font-size:10px;color:var(--color-text-tertiary,#666);opacity:.8}',
      /* 工具链标签：来源徽章 / 卡头 meta 行 / details 外卡 / ZCode 紧凑行 / 错误条 */
      '.cr-src{font-size:10px;padding:1px 6px;border-radius:999px;flex-shrink:0;border:1px solid}',
      '.cr-src-agent{color:var(--color-brand-cyan,#22d3ee);border-color:rgba(34,211,238,.35);background:rgba(34,211,238,.08)}',
      '.cr-src-zcode{color:var(--color-brand-violet,#8b5cf6);border-color:rgba(139,92,246,.4);background:rgba(139,92,246,.08)}',
      '.cr-round-meta{font-size:10px;color:var(--color-text-tertiary,#666);margin-left:auto;flex-shrink:0;',
      'font-family:ui-monospace,Consolas,monospace}',
      'details.cr-round summary.cr-round-head{cursor:pointer;list-style:none}',
      'details.cr-round summary.cr-round-head::-webkit-details-marker{display:none}',
      '.cr-zsteps{margin-top:4px;border:1px solid var(--color-line-ghost,rgba(255,255,255,.08));',
      'border-radius:6px;max-height:240px;overflow-y:auto}',
      '.cr-zrow{display:flex;align-items:baseline;gap:7px;padding:3px 8px;min-width:0;font-size:11px;',
      'border-top:1px solid var(--color-line-ghost,rgba(255,255,255,.06))}',
      '.cr-zrow:first-child{border-top:none}',
      '.cr-zrow.is-ind{padding-left:20px}',
      '.cr-zt{color:var(--color-text-tertiary,#666);font-size:10px;flex-shrink:0;',
      'font-family:ui-monospace,Consolas,monospace}',
      '.cr-zl{color:var(--color-text-primary,#e5e5e5);flex-shrink:0}',
      '.cr-zrow.zc-dim .cr-zl{color:var(--color-text-tertiary,#8a8a8a)}',
      '.cr-zrow.zc-green .cr-zl{color:#4ade80}',
      '.cr-zrow.zc-red .cr-zl{color:#F87171}',
      '.cr-zs{color:var(--color-text-tertiary,#8a8a8a);min-width:0;word-break:break-all;',
      'font-family:ui-monospace,Consolas,monospace;font-size:11px}',
      '.cr-zerr{margin:4px 0 0;border-left:2px solid #F87171;color:#F87171;padding:2px 0 2px 8px;',
      'font-size:10px;word-break:break-all}',
      /* 浏览器标签（第三段·iframe 雏形）：地址栏行 / 提示行 / iframe 区 */
      '.cr-browser{display:flex;flex-direction:column;height:100%;min-height:0}',
      '.cr-browser-bar{display:flex;align-items:center;gap:6px;flex-shrink:0}',
      '.cr-browser-url{flex:1;min-width:0;background:var(--color-bg-app,rgba(0,0,0,.14));',
      'border:1px solid var(--color-line-ghost,rgba(255,255,255,.12));border-radius:6px;padding:5px 8px;',
      'font-size:11px;color:var(--color-text-primary,#e5e5e5);outline:none;',
      'font-family:ui-monospace,Consolas,monospace}',
      '.cr-browser-url:focus{border-color:var(--color-brand,#E5734A)}',
      '.cr-browser-go,.cr-browser-reload{font-size:11px;padding:4px 10px;border-radius:6px;flex-shrink:0;',
      'cursor:pointer;border:1px solid var(--color-line-ghost,rgba(255,255,255,.12));background:transparent;',
      'color:var(--color-text-secondary,#a3a3a3)}',
      '.cr-browser-go:hover,.cr-browser-reload:hover{color:var(--color-brand,#E5734A);border-color:var(--color-brand,#E5734A)}',
      '.cr-browser-hint{display:none;font-size:10px;color:var(--color-text-tertiary,#666);margin-top:4px;flex-shrink:0}',
      '.cr-browser-hint.is-bad{color:#F87171}',
      '.cr-browser-frame-wrap{position:relative;flex:1;min-height:0;margin-top:8px;overflow:hidden;',
      'border:1px solid var(--color-line-ghost,rgba(255,255,255,.08));border-radius:8px;background:#202022}',
      '.cr-browser-frame{position:absolute;inset:0;width:100%;height:100%;border:0}',
      '.cr-browser-empty{position:absolute;inset:0;display:flex;flex-direction:column;gap:6px;align-items:center;',
      'justify-content:center;text-align:center;padding:0 24px;font-size:12px;line-height:1.8;',
      'color:var(--color-text-tertiary,#9a9a9a);background:#202022}',
      '.cr-browser-frame-wrap.has-src .cr-browser-empty{display:none}',
      /* 浏览器 tab 激活时栏体转全高：栏体自身成 flex 列（.cr-pane 若只写 height:100%，
         父级 .cr-body 高度来自 flex:1（computed auto）→ % 高度解析为 auto，iframe 区塌成内容高。
         这里用 flex 链贯通：cr-body 定高 → pane flex:1 → cr-browser height:100% 生效） */
      '#' + RAILS_ID + '.cr-browser-tab .cr-body{overflow:hidden;padding:8px 10px 10px;display:flex;flex-direction:column}',
      '#' + RAILS_ID + '.cr-browser-tab .cr-pane.active{display:flex;flex-direction:column;flex:1;min-height:0}'
    ].join('');
    document.head.appendChild(el);
  }

  /* ---------------- 骨架装配（幂等） ---------------- */

  /** 把 #chat-normal / #chat-compare 原样移入新建的行向舞台，并挂上右栏。
   * @returns {boolean} 装配就绪（或此前已就绪） */
  function buildStageAndRails() {
    if (document.getElementById(RAILS_ID)) return true;
    var pageChat = document.getElementById('page-chat');
    var normal = document.getElementById('chat-normal');
    if (!pageChat || !normal) return false;
    ensureStyle();

    var stage = document.getElementById(STAGE_ID);
    if (!stage) {
      stage = document.createElement('div');
      stage.id = STAGE_ID;
      pageChat.insertBefore(stage, normal);           // header 之后、对话区原位
      stage.appendChild(normal);                       // 自身结构零变动的"搬家"
      var compare = document.getElementById('chat-compare');
      if (compare) stage.appendChild(compare);
    }

    var rails = document.createElement('aside');
    rails.id = RAILS_ID;
    rails.setAttribute('aria-label', '对话页右侧标签面板');
    rails.innerHTML =
      '<div class="cr-head"><div class="cr-tabs">' +
      TABS.map(function (t) {
        return '<button class="cr-tab" data-cr-tab="' + t.id + '" type="button">' + t.label + '</button>';
      }).join('') +
      '</div><button class="cr-collapse" id="chatRailsCollapseBtn" type="button" title="收起右栏（Ctrl+J）">' +
      '<i data-lucide="panel-left-close" class="w-4 h-4"></i></button></div>' +
      '<div class="cr-body">' +
      '<div class="cr-pane" id="chatRailsPane-memory" data-cr-pane="memory"></div>' +
      '<div class="cr-pane" id="chatRailsPane-tools" data-cr-pane="tools"></div>' +
      '<div class="cr-pane" id="chatRailsPane-browser" data-cr-pane="browser">' +
      '<div class="cr-browser"><div class="cr-browser-bar">' +
      '<input class="cr-browser-url" type="text" spellcheck="false" placeholder="输入 URL（http/https）" />' +
      '<button class="cr-browser-go" type="button">打开</button>' +
      '<button class="cr-browser-reload" type="button" title="刷新">↻</button></div>' +
      '<div class="cr-browser-hint" style="display:none"></div>' +
      '<div class="cr-browser-frame-wrap">' +
      '<iframe class="cr-browser-frame" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>' +
      '<div class="cr-browser-empty">输入本机服务页或外站 URL<br>' +
      '<span class="cr-hint">外站可能被 X-Frame-Options 拒绝加载（iframe 固有限制）</span></div>' +
      '</div></div></div>' +
      '</div>';
    stage.appendChild(rails);

    /* 事件：tab 切换 */
    rails.addEventListener('click', function (e) {
      var tabBtn = e.target.closest('.cr-tab');
      if (tabBtn && rails.contains(tabBtn)) { setTab(tabBtn.getAttribute('data-cr-tab')); return; }
      if (e.target.closest('#chatRailsCollapseBtn')) setOpen(false, true);
    });

    bindHeaderToggle();
    bindBrowserPane(rails);
    return true;
  }

  /** 页头右上按钮组注入展开/收起按钮（tool-btn 同款；图标名复用产品已有的
   * panel-left-open / panel-left-close，assemble 图标计数保持 124）。
   * @returns {void} */
  function bindHeaderToggle() {
    if (document.getElementById(TOGGLE_BTN_ID)) return;
    var anchor = document.getElementById('chatSettingsBtn');
    var group = anchor && anchor.parentElement;
    if (!group) return;
    var btn = document.createElement('button');
    btn.className = 'tool-btn';
    btn.id = TOGGLE_BTN_ID;
    btn.type = 'button';
    btn.title = '右侧面板（Ctrl+J）';
    group.insertBefore(btn, anchor);
    btn.addEventListener('click', function () { setOpen(!isOpen(), true); });
    paintToggleIcon();
  }

  /** 按当前展开态刷新页头按钮图标。@returns {void} */
  function paintToggleIcon() {
    var btn = document.getElementById(TOGGLE_BTN_ID);
    if (!btn) return;
    btn.innerHTML = '<i data-lucide="' + (isOpen() ? 'panel-left-close' : 'panel-left-open') + '" class="w-4 h-4"></i>';
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 展开/收起与持久化 ---------------- */

  /** @returns {boolean} 是否展开 */
  function isOpen() { return document.body.classList.contains(OPEN_CLASS); }

  /** 切换展开态。@param {boolean} open @param {boolean} persist 是否写 localStorage
   * @returns {void} */
  function setOpen(open, persist) {
    if (open) document.body.classList.add(OPEN_CLASS);
    else document.body.classList.remove(OPEN_CLASS);
    if (persist) { try { localStorage.setItem(LS_KEY, open ? '0' : '1'); } catch (e) { /* 隐私模式写失败不致命 */ } }
    paintToggleIcon();
    /* 渲染入口收敛到 setTab→refreshActive（收起态由 refreshActive 门控并摘轮询） */
    if (open) { setTab(_tab); } else { syncToolsTicker(false); }
  }

  /** 激活某个 tab。@param {string} id @returns {void} */
  function setTab(id) {
    var rails = document.getElementById(RAILS_ID);
    if (!rails) return;
    var found = TABS.some(function (t) { return t.id === id; });
    _tab = found ? id : 'memory';
    rails.querySelectorAll('.cr-tab').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-cr-tab') === _tab);
    });
    rails.querySelectorAll('.cr-pane').forEach(function (p) {
      p.classList.toggle('active', p.getAttribute('data-cr-pane') === _tab);
    });
    /* 浏览器 tab：栏体转全高布局（CSS 靠 rails 上此类）；pane DOM 常驻 → iframe 不被清空 */
    rails.classList.toggle('cr-browser-tab', _tab === 'browser');
    refreshActive();
  }

  /* ---------------- 记忆召回标签（改动2：数据源 AppState.messages，纯前端读取） ---------------- */

  /** 聚合本会话所有"有召回记录"的轮次。
   * @returns {Array<{user:(Object|null), ai:Object, rec:Object}>} 新轮在前 */
  function collectRounds() {
    var rounds = [];
    try {
      var msgs = (typeof AppState !== 'undefined' && AppState && Array.isArray(AppState.messages)) ? AppState.messages : [];
      var lastUser = null;
      for (var i = 0; i < msgs.length; i++) {
        var m = msgs[i];
        if (!m || typeof m !== 'object') continue;
        if (m.role === 'user' && m.kind !== 'zcode-execution') { lastUser = m; continue; }
        if (m.role === 'assistant' && m.memoryRecall && Array.isArray(m.memoryRecall.items) && m.memoryRecall.items.length) {
          rounds.push({ user: lastUser, ai: m, rec: m.memoryRecall });
        }
      }
    } catch (e) { /* AppState 不可达时返回空集，面板显示空态 */ }
    return rounds.reverse();
  }

  /** 时间戳显示（当日 HH:MM，跨日补 MM-DD）。@param {number} ts @returns {string} */
  function fmtTime(ts) {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    var hm = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    var now = new Date();
    var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return sameDay ? hm : ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2) + ' ' + hm;
  }

  /** 提问摘录：压空白、截 42 字。@param {string} text @returns {string} */
  function excerpt(text) {
    var s = String(text || '').replace(/\s+/g, ' ').trim();
    return s.length > 42 ? s.slice(0, 42) + '…' : s;
  }

  /** 渲染记忆召回面板。@returns {void} */
  function renderMemory() {
    var pane = document.getElementById('chatRailsPane-memory');
    if (!pane) return;
    var rounds = collectRounds();
    if (!rounds.length) {
      pane.innerHTML = '<div class="cr-empty">本会话暂无记忆召回<br>' +
        '<span class="cr-hint">召回总开关开启后（设置 → 记忆），发送消息即产生数据；旧气泡底部胶囊与此面板同源共存</span></div>';
      return;
    }
    var esc = (typeof escapeHtml === 'function') ? escapeHtml : function (s) { return String(s); };
    pane.innerHTML = rounds.map(function (r) {
      var time = fmtTime(r.ai.createdAt || r.ai.created_at || (r.user && r.user.createdAt) || Date.now());
      var q = r.user && r.user.content ? esc(excerpt(r.user.content)) : '（无提问记录）';
      /* 明细渲染直接复用气泡胶囊的 memoryRecallHtml（30_chat.js:957，全局函数）：
         同一个 details 结构、同一套 排名/score/kind/source/预览/原料id 行格式 */
      var detail = '';
      try {
        if (typeof window.memoryRecallHtml === 'function') detail = window.memoryRecallHtml(r.ai) || '';
      } catch (e) { detail = ''; }
      if (!detail) {
        detail = '<div class="cr-hint" style="margin-top:4px">召回 ' + r.rec.items.length + ' 条 · 注入 ' +
          (parseInt(r.rec.injectedCount, 10) || 0) + ' 条（明细渲染器不可用）</div>';
      }
      return '<div class="cr-round"><div class="cr-round-head">' +
        '<span class="cr-round-time">' + time + '</span>' +
        '<span class="cr-round-q" title="' + esc(String(r.user && r.user.content || '')) + '">' + q + '</span>' +
        '</div>' + detail + '</div>';
    }).join('');
  }

  /* ---------------- 当前 tab 分发刷新（三处刷新点统一走这里：setTab / setOpen / MutationObserver） ---------------- */

  /** 重绘当前激活标签：memory → 记忆召回，tools → 工具链，browser → 占位无操作。
   *  收起态零开销并摘除 running 轮询。@returns {void} */
  function refreshActive() {
    if (!isOpen()) { syncToolsTicker(false); return; }
    if (_tab === 'tools') renderTools();
    else { renderMemory(); syncToolsTicker(false); }
  }

  /* ---------------- 工具链标签（第二段：数据源同 AppState.messages，纯前端读取，零后端调用） ----------------
     · 本机 Agent 轮次 = assistantMsg.toolCalls（30_chat.js:1837 落库 → IndexedDB → 重建后仍在）；
     · ZCode 轮次 = kind='zcode-execution' 消息的 msg.execution.steps（92_laya_agent.js 落库）；
     · 与记忆召回同口径（本会话）、同语言（.cr-round 卡）、同新→旧排序。 */

  var _toolsTimer = 0;   // running 时挂 2s 轻量轮询；终态/收起/切 tab/停滞即摘（防泄漏）
  var _toolsTicks = 0;   // 连续"签名无变化"轮数（僵尸 running 停滞保护）
  var _toolsSig = null;  // 上次渲染的数据签名

  /** 轮级状态文案（msg.execution.status 与 agent 轮派生态共用）。 */
  var _roundStatusText = { running: '执行中', success: '已完成', failed: '失败', cancelled: '已取消' };

  /** 步骤状态文案（agent 轮 toolCalls 的 step.status，取值域对齐 125_tools.js 步骤卡）。 */
  var _stepStatusText = { ok: '成功', error: '失败', timeout: '超时', rejected: '拒绝', denied: '已拒绝',
    needs_approval: '待审批', pending: '待审批', running: '执行中', cancelled: '已取消' };

  /** agent 轮整体状态 + 总耗时派生：running/pending 步存在 → 执行中；有失败步 → 失败；否则成功。
   * @param {Array} steps @returns {{status:string, ms:number}} */
  function agentRoundMeta(steps) {
    var busy = false, bad = false, ms = 0;
    for (var i = 0; i < steps.length; i++) {
      var s = String((steps[i] && steps[i].status) || '');
      if (s === 'running' || s === 'pending') busy = true;
      else if (s === 'error' || s === 'timeout' || s === 'rejected' || s === 'denied' || s === 'needs_approval') bad = true;
      ms += parseInt(steps[i] && steps[i].ms, 10) || 0;
    }
    return { status: busy ? 'running' : (bad ? 'failed' : 'success'), ms: ms };
  }

  /** 遍历本会话消息，聚合工具链轮次（agent + zcode 混排，新→旧）。
   * @returns {Array<Object>} AppState 不可达 / 无数据 → 空数组（不抛错） */
  function collectTools() {
    var rounds = [];
    try {
      var msgs = (typeof AppState !== 'undefined' && AppState && Array.isArray(AppState.messages)) ? AppState.messages : [];
      var lastUser = null;
      for (var i = 0; i < msgs.length; i++) {
        var m = msgs[i];
        if (!m || typeof m !== 'object') continue;
        if (m.role === 'user' && m.kind !== 'zcode-execution') { lastUser = m; continue; }
        var time = m.createdAt || m.created_at || 0;
        if (m.role === 'assistant' && Array.isArray(m.toolCalls) && m.toolCalls.length) {
          var am = agentRoundMeta(m.toolCalls);
          rounds.push({ kind: 'agent', time: time, user: lastUser, steps: m.toolCalls, status: am.status, ms: am.ms });
        } else if (m.kind === 'zcode-execution' && m.execution && Array.isArray(m.execution.steps)) {
          var ex = m.execution;
          rounds.push({ kind: 'zcode', time: time, user: lastUser, ex: ex, steps: ex.steps,
            status: ex.status || '', ms: ex.durationMs, totalTokens: ex.totalTokens });
        }
      }
    } catch (e) { /* AppState 不可达时返回空集，面板显示空态 */ }
    rounds.sort(function (a, b) { return (b.time || 0) - (a.time || 0); });
    return rounds;
  }

  /** 渲染签名：轮数 + 每轮步数/状态/耗时；agent 轮再拼各步 status（就地更新也计变化）。
   * @param {Array} rounds @returns {string} */
  function toolsSig(rounds) {
    var parts = [];
    for (var i = 0; i < rounds.length; i++) {
      var r = rounds[i], tail = '';
      if (r.kind === 'agent') {
        for (var j = 0; j < r.steps.length; j++) tail += String((r.steps[j] && r.steps[j].status) || '') + ',';
      }
      parts.push(r.kind + '@' + (r.time || 0) + '#' + r.steps.length + '#' + r.status + '#' + (r.ms || 0) + '#' + tail);
    }
    return parts.join('|');
  }

  /** 耗时显示：≥1s 走秒，否则毫秒。@param {number} ms @returns {string} */
  function fmtDur(ms) {
    ms = parseInt(ms, 10) || 0;
    return ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms + 'ms';
  }

  /** ZCode steps → 紧凑行 HTML：保留 92 的 l/c/s/i 缩进与色码语义（存储即纯文本，渲染仍转义）。
   * @param {Array} steps @param {Function} esc @returns {string} */
  function zcodeStepsHtml(steps, esc) {
    var rows = [];
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i] || {};
      rows.push('<div class="cr-zrow' + (s.i ? ' is-ind' : '') + (s.c ? ' zc-' + esc(s.c) : '') + '">' +
        '<span class="cr-zt">' + esc(s.t || '') + '</span>' +
        '<span class="cr-zl">' + esc(s.l || '') + '</span>' +
        (s.s ? '<span class="cr-zs">' + esc(s.s) + '</span>' : '') +
        '</div>');
    }
    return '<div class="cr-zsteps">' + rows.join('') + '</div>';
  }

  /** Agent 降级渲染（renderToolSteps 不可用时）：一步一行（名 + 参数摘要 + 状态 + 耗时）。
   * @param {Array} steps @param {Function} esc @returns {string} */
  function agentStepsFallback(steps, esc) {
    var rows = steps.map(function (s) {
      var argS = '';
      try { var j = JSON.stringify(s.args || {}); argS = (j === '{}' || j === 'null') ? '' : j; } catch (e) { argS = String(s.args || ''); }
      if (argS.length > 80) argS = argS.slice(0, 80) + '…';
      var ms = parseInt(s.ms, 10) || 0;
      var tail = (_stepStatusText[s.status] || s.status || '') + (ms ? ' · ' + fmtDur(ms) : '');
      return '<div class="cr-zrow">' +
        '<span class="cr-zl">' + esc(s.label || s.name || 'tool') + '</span>' +
        (argS ? '<span class="cr-zs">' + esc(argS) + '</span>' : '') +
        (tail ? '<span class="cr-zt">' + esc(tail) + '</span>' : '') +
        '</div>';
    }).join('');
    return '<div class="cr-zsteps">' + rows + '</div>';
  }

  /** 渲染工具链面板。外层卡复用 .cr-round（details 折叠，默认收起，防长列表爆屏）；
   *  卡体：Agent 轮 = 全局 renderToolSteps（125_tools.js:733，折叠摘要+展开时间线自带），
   *  但剥离"重试该步"重放按钮 —— 本 tab 只读，绝不出现真执行入口（复制按钮保留）；
   *  ZCode 轮 = 自写紧凑行。签名未变化不重刷 DOM。
   * @returns {void} */
  function renderTools() {
    var pane = document.getElementById('chatRailsPane-tools');
    if (!pane) { syncToolsTicker(false); return; }
    var rounds = collectTools();
    var running = false;
    for (var i = 0; i < rounds.length; i++) { if (rounds[i].status === 'running') { running = true; break; } }
    var sig = toolsSig(rounds);
    if (sig !== _toolsSig) {
      _toolsSig = sig; _toolsTicks = 0;
      if (!rounds.length) {
        pane.innerHTML = '<div class="cr-empty">本会话暂无工具调用<br>' +
          '<span class="cr-hint">输入台开「本机工具」跑 Agent 工具、或发起 ZCode 任务后即产生数据；点开卡片看该轮步骤时间线</span></div>';
      } else {
        var esc = (typeof escapeHtml === 'function') ? escapeHtml : function (s) { return String(s); };
        pane.innerHTML = rounds.map(function (r) {
          var time = fmtTime(r.time || Date.now());
          var q = r.user && r.user.content ? esc(excerpt(r.user.content)) : '（无提问记录）';
          var meta = r.steps.length + ' 步' + (r.ms ? ' · ' + fmtDur(r.ms) : '');
          if (r.kind === 'zcode' && r.totalTokens != null) meta += ' · ' + r.totalTokens + ' tok';
          meta += ' · ' + (_roundStatusText[r.status] || r.status || '未知');
          var detail = '';
          if (r.kind === 'agent') {
            try { if (typeof window.renderToolSteps === 'function') detail = window.renderToolSteps(r.steps) || ''; } catch (e) { detail = ''; }
            /* 只读剥离：去掉重试按钮（真执行入口），复制/折叠交互保留 */
            detail = detail.replace(/<button[^>]*data-tool-retry[^>]*>[\s\S]*?<\/button>/g, '');
            if (!detail) detail = agentStepsFallback(r.steps, esc);
          } else {
            detail = zcodeStepsHtml(r.steps, esc);
            if (r.ex && r.ex.error) detail += '<div class="cr-zerr">' + esc(String(r.ex.error).slice(0, 200)) + '</div>';
          }
          return '<details class="cr-round"><summary class="cr-round-head">' +
            '<span class="cr-round-time">' + time + '</span>' +
            '<span class="cr-src cr-src-' + r.kind + '">' + (r.kind === 'agent' ? 'Agent' : 'ZCode') + '</span>' +
            '<span class="cr-round-q" title="' + esc(String(r.user && r.user.content || '')) + '">' + q + '</span>' +
            '<span class="cr-round-meta">' + esc(meta) + '</span></summary>' + detail + '</details>';
        }).join('');
        if (typeof refreshIcons === 'function') { try { refreshIcons(); } catch (e) { /* 图标刷新失败不致命 */ } }
      }
    } else {
      _toolsTicks++;
      /* 停滞保护：running 消息的源头若在页面刷新中途断掉，IndexedDB 状态不再前进 ——
         签名连续 120 轮（4 分钟）无变化即摘表；重开 tab / 消息区变化会重新挂上 */
      if (_toolsTicks > 120) { syncToolsTicker(false); return; }
    }
    syncToolsTicker(running);
  }

  /** running 轻量轮询同步：有 running 轮次挂 2s interval，终态/收起/切 tab/停滞一律摘。
   * @param {boolean} need @returns {void} */
  function syncToolsTicker(need) {
    if (need) {
      if (!_toolsTimer) _toolsTimer = setInterval(renderTools, 2000);
    } else if (_toolsTimer) {
      clearInterval(_toolsTimer); _toolsTimer = 0;
    }
  }

  /* ---------------- 浏览器标签（第三段·路线 A iframe 最小雏形：本机服务页 + URL 输入框） ----------------
     · 只读外壳：无快捷按钮/历史/收藏/标签页；"用系统浏览器打开"要调 shell（下档）；
     · 切 tab 不重载不清空（pane DOM 常驻，src 天然保留）；
     · 外站被 X-Frame-Options 拒是 iframe 固有限制，接受不硬修。 */

  var _browserUrl = '';      // 当前已加载 URL（内存态；页面刷新丢失，可接受）
  var _browserHintTimer = 0; // 提示行自动隐藏

  /** URL 校验/规范化：无 scheme 补 http://；仅放行 http/https
   * （javascript: / data: / file: / 其他 scheme 一律拒）。
   * @param {string} raw @returns {{ok:boolean, url?:string, msg?:string}} */
  function browserSafeUrl(raw) {
    var s = String(raw || '').trim();
    if (!s) return { ok: false, msg: '先输入 URL 再打开' };
    if (/\s/.test(s)) return { ok: false, msg: 'URL 含空白，无法打开' };
    if (!/^[a-z][a-z0-9+.\-]*:/i.test(s)) s = 'http://' + s;
    if (!/^https?:\/\//i.test(s)) {
      return { ok: false, msg: '仅支持 http/https，已阻止 ' + s.slice(0, s.indexOf(':') + 1) + ' 协议' };
    }
    try {
      var u = new URL(s);
      if (!u.hostname) return { ok: false, msg: 'URL 缺主机名，无法打开' };
      return { ok: true, url: u.href };
    } catch (e) { return { ok: false, msg: 'URL 格式不合法' }; }
  }

  /** 地址栏下方临时提示（4s 自动隐藏，重复调用会覆盖）。
   * @param {HTMLElement} rails @param {string} msg @param {boolean} bad @returns {void} */
  function browserHint(rails, msg, bad) {
    var el = rails.querySelector('.cr-browser-hint');
    if (!el) return;
    el.textContent = msg || '';
    el.classList.toggle('is-bad', !!bad);
    el.style.display = msg ? 'block' : 'none';
    clearTimeout(_browserHintTimer);
    if (msg) _browserHintTimer = setTimeout(function () { el.style.display = 'none'; }, 4000);
  }

  /** 浏览器 pane 接线（buildStageAndRails 幂等守卫内调用一次）。
   * @param {HTMLElement} rails @returns {void} */
  function bindBrowserPane(rails) {
    var input = rails.querySelector('.cr-browser-url');
    var frame = rails.querySelector('.cr-browser-frame');
    var wrap = rails.querySelector('.cr-browser-frame-wrap');
    var goBtn = rails.querySelector('.cr-browser-go');
    var reloadBtn = rails.querySelector('.cr-browser-reload');
    if (!input || !frame || !wrap || !goBtn || !reloadBtn) return;
    function nav() {
      var v = browserSafeUrl(input.value);
      if (!v.ok) { browserHint(rails, v.msg, true); return; }
      frame.src = v.url;
      _browserUrl = v.url;
      input.value = v.url;               // 规范化后的 URL 回填地址栏（可再编辑）
      wrap.classList.add('has-src');     // 收起空态浮层
      browserHint(rails, '', false);
    }
    goBtn.addEventListener('click', nav);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); nav(); } });
    reloadBtn.addEventListener('click', function () {
      if (!_browserUrl) { browserHint(rails, '还没有加载任何页面', true); return; }
      frame.src = _browserUrl;           // src 自赋值 = 强制重载当前 URL
    });
  }

  /* ---------------- 数据变化监听（工单：优先 MutationObserver，不动 30_chat 核心） ---------------- */

  var _mo = null;
  var _refreshTimer = 0;

  /** 挂观察：#chatMessagesNormal 子树增删（发送/流式收尾/切会话都会整区重建）→ 防抖重绘。
   * @returns {void} */
  function bindObserver() {
    if (_mo) return;
    var box = document.getElementById('chatMessagesNormal');
    if (!box) return;
    _mo = new MutationObserver(function () {
      if (!isOpen()) return;                 // 收起态零开销
      clearTimeout(_refreshTimer);
      _refreshTimer = setTimeout(refreshActive, 400);
    });
    _mo.observe(box, { childList: true });
  }

  /* ---------------- Ctrl+J（中栏 Ctrl+B 的对偶；已核实全库无既有绑定） ---------------- */

  function bindKeys() {
    document.addEventListener('keydown', function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      if (String(e.key).toLowerCase() !== 'j') return;
      var page = document.getElementById('page-chat');
      if (!page || page.classList.contains('hidden')) return;   // 仅对话页生效
      var rails = document.getElementById(RAILS_ID);
      if (!rails) return;
      e.preventDefault();
      setOpen(!isOpen(), true);
    });
  }

  /* ---------------- boot（30_chat 同款双路：readyState 即跑 + 延迟补拍，幂等） ---------------- */

  function tryInstall() {
    if (!buildStageAndRails()) return false;
    bindObserver();
    /* 折叠态按 localStorage 记忆：默认收起（'1'）；'0' = 展开 */
    var collapsed = '1';
    try { collapsed = localStorage.getItem(LS_KEY) || '1'; } catch (e) { /* 读失败退回默认收起 */ }
    setTab('memory');
    if (collapsed !== '0') { paintToggleIcon(); } else { setOpen(true, false); }
    return true;
  }

  function boot() { if (!tryInstall()) setTimeout(boot, 600); }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); }
  else { boot(); }
  bindKeys();

  /* 调试出口（89 导出 MorayZcodeStream 同款惯例） */
  window.MorayChatRails = {
    isOpen: isOpen,
    setOpen: setOpen,
    refresh: refreshActive,
    render: renderMemory,
    renderTools: renderTools
  };
})();
