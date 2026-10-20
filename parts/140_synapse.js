/* ============================================================
   [Synapse v2 2026-10-06] Synapse 事件总线页（只读）—— 神经总线事件台账
   [v4-form 2026-10-08] 形态重构（诊断报告「丙」路径，五条清单）：
     ① 去气泡——等价移植 135 §22 主聊天页去气泡裁定到 #page-synapse 作用域
       （原规则被 #chatMessagesNormal 选择器圈死，本页吃不到）；
     ② 台账三列——时间槽 HH:MM:SS（mono）/ 成员列 / event_type 徽章 + 耗时/退出码
       （mono）；徽章数据源 = 顶层 proxy_source（live 实证 meta.event_type 与顶层
       可不一致，不信 meta）；
     ③ beacon-dot 装饰脉冲点删除（逐消息核对无状态语义）；
     ④ 成员栏改「器官态势板」——诚实口径「最近完成于 N 前」，不称在线/活跃/进行
       中（真状态属 C 档无数据源，v5 待账，见诊断报告红线）；
     ⑤ 页头/空态措辞从「群聊」落回「事件总线台账」定位（UI_DIRECTION §九判据）。
     边界：零新增数据源、零新图标、改动限于本文件；系统消息居中灰条保留（有语义）。
   ------------------------------------------------------------
   [v5-step3 2026-10-08] 器官态势板改吃后端全库聚合——renderMemberRail 主源切到
     GET /api/synapse/overview 的 by_member（成员级 count/last_seen/types 后端全库
     组装）；_items 客户端聚合整段保留作降级路径；排序/显示名/诚实措辞零改动。
   ------------------------------------------------------------
   [v5-step6 2026-10-09] 潜水员插话——页面底部新增「指令插话栏」（composer 行）：
     发言走 POST /api/synapse/messages（from_member='user' / proxy_source=
     'user.message'，mentions 端点侧硬置空），成功即用服务端回行乐观 append +
     renderAll——不等 3s 轮询；下一拍 pollIncrement 若重拉到同 id，被现有 id 去重
     吞掉，游标用服务端 created_at 前移不脱节。失败输入原文保留、hint 条报错。
     renderUserWake 加 from_member==='user' 跳过（防自弹第二道保险）。user.message
     在 synapse_rules 无行 = 纯落账、零器官反应（第六条红线：不 seed 规则、不含
     器官回应）。形态纪律：令牌化单行输入 + 发送钮、行宽对齐 max-w-3xl 台账——
     工作台插话栏，非 ChatGPT composer（UI_DIRECTION §四/§九判据）。
     轮询/退避/态势板/筛选行/四个 GET 消费路径零改动。
   ------------------------------------------------------------
   [v5-step7 2026-10-08] 时间线/流转路径——任务链视图（形态丁·侧栏抽屉 + 甲一分·
     行内链徽章）：纯前端零后端——"链" = 同 meta.task_id 的事件集，从台账已拉回的
     _items 现算（_chainIdx Map，renderAll 每拍重建），GET 消费路径/轮询/v37 trace
     分键/后端文件零接触（v39 决策 185"不动 v37"由此自动成立）。台账徽章行加
     「链·N·前8位」入口（N≥2 才出现；单事件链与无 task_id 行——user/system/argus/
     laya 补报——不加，避免噪音）；点击开页面右侧抽屉：该 task 全部事件按时间升序、
     逐行复用 synapseBuildEl（inDrawer=true 压掉徽章与深链丸，入口单向零递归）；
     抽屉头 = task 前 8 位 + 状态徽章（succeeded/failed/进行中——纯从库行终态推导，
     绝不吃 /chains 内存端点，重启清零语义与本视图无关；无 started 的老链如实标
     「未记录起点」，不回填）；✕/遮罩/Esc 三通道关闭。证据链接深链收口（v39 表
     第 7 项后半个）：取证胶囊内 task_id 丸在 N≥2 时升级为可点深链，与行徽章同源
     同行为。顺带收第六条观察项：态势板 user 行「最近完成于」→「最近发言于」
     （display 一行条件化，数据层零动）。
   ------------------------------------------------------------
   - 授权：Synapse v2 任务书 + [v5 第六条 2026-10-09] 潜水员插话。v2 边界（硬）
     「只读观测——不放输入框、不挂发送链、『潜水员插话』属 v3」中的插话一项，由
     v5 任务书授权解除（见上 step6 块）；其余边界不变：不做 SSE，3s 轮询起步
     （document.hidden 时 tick 自守卫跳过 + 回到前台立即补一拍，等效暂停/恢复；
     后端离线时指数退避 ≤30s）。
   - 模式照抄 memory_browser.js 自注入四件套（ensureStyle/ensureNavButton/
     ensurePage/buildPage）+ 其 switchTo；导航按钮插「工作台」组（86 同款锚点），
     页面容器插 #page-cost 之后；默认隐藏不抢启动落点，86 goSynapseCard 点
     导航按钮进入。
   - 数据客户端：window.MoraySynapse = { start, stop, refresh }；基座
     agentBackendBase()（125_tools.js:1043，memory_client.js:24-29 同款兜底链）。
     数据源 GET /api/synapse/*（synapse_routes.py，created_at 已在后端转
     ISO 8601 +08:00，前端 new Date(iso) 直解析）。
   - 复用清单（30_chat/10_db/90_perf 全局函数，直调零改动）：renderMarkdown /
     escapeHtml / refreshIcons / clockTime（接受 ISO 串，10_db.js:101）/
     appendWithTimeSeparators（>5 分钟时间分隔线，读 m.createdAt 毫秒，故映射层
     同步备 createdAt）/ memoryRecallHtml（M5 胶囊，meta.recall 形状对得上时复用）。
   - 执行轨迹胶囊 synapseTraceHtml 为新写：内联样式抄 memoryRecallHtml
     （30_chat.js:962-987），数据取 meta 的 task_id / duration_ms / exit_code 三项。
   - 挂载：DOMContentLoaded 自挂载（不等 70 bootApp；挂载失败 console.warn 不中断
     组装块后续代码）。幂等：按钮/页面/构建均先查再建。
   ============================================================ */

const SynapseChatApp = (() => {

  const PAGE = 'synapse';
  /** 轮询间隔（任务书钉子③：3s 起步；后端连续失败时指数退避，上限 30s） */
  const POLL_MS = 3000;
  const POLL_MAX_MS = 30000;

  /** from_member → 显示名（v3-lite：显示名 ≠ 存储值）。organ.* 前缀是语义——
   * 区分"器官自发"与 v4 代理转发，存储值不改（历史行照旧），只在展示层映射；
   * 未登记的值原样上屏（未知成员不吃掉）。图标判定不走此表（/zcode/i 正则兼容）。 */
  const MEMBER_DISPLAY = {
    'organ.zcode': 'ZCode',
    'organ.argus': 'Argus',
    'organ.iris':  'Iris',
    'organ.laya':  'Laya',
    'moRay':       'MoRay',
    'reflection':  '反思',
    'system':      '系统',
    'user':        '用户',   // [v5 step2] @user 发送侧的展示名（提及值，非 from_member）
  };

  /** B 类代理成员（v4 侦察裁定：B 类只有 Argus）——只能被拉、经 Synapse 代理转发，
   *  信息行标「经 Synapse 代理」；A 类器官（ZCode/Iris/Laya）主动推，不标。 */
  const PROXY_MEMBERS = new Set(['organ.argus']);

  let _timer = null;        // setTimeout 句柄（单链调度，非 setInterval：间隔可退避）
  let _pollMs = POLL_MS;
  let _inFlight = false;    // 请求在途守卫（防重入叠加）
  let _sending = false;     // [v5-step6] 发言在途守卫（Enter 连击防双发，与 _inFlight 同纪律）
  let _items = [];          // 展示用消息数组（升序 旧→新；行字段 + createdAt(ms)）
  let _notifiedIds = new Set();  // [v5 step2] @user 已弹通知的 id（页级去重：3s 轮询全量重绘不重弹）
  let _wakeSeeded = false;       // [v5 step2] 首批带数据渲染只登记不弹（历史事件不回放轰炸）
  let _since = '';          // 增量游标：最近一条 created_at（ISO 串，原样回传后端）
  let _filter = '';         // proxy_source 筛选（'' = 全部）
  let _overview = null;     // /api/synapse/overview 结果（页头计数 + 筛选 chips）
  let _loaded = false;      // 首屏是否已成功拉取（失败保持 false，下次激活重试）
  let _errStreak = 0;       // 连续失败次数（退避用；成功即清零）
  let _chainIdx = new Map(); // [v5-step7] task_id → 同任务事件数组（renderAll 每拍从 _items 重建）
  let _drawerTask = '';      // [v5-step7] 抽屉展开中的 task_id（'' = 关闭）

  /* ---------------- 样式（本页私有；令牌化，深浅主题自适应） ---------------- */

  function ensureStyle() {
    if (document.getElementById('moraySynapseStyle')) return;
    const el = document.createElement('style');
    el.id = 'moraySynapseStyle';
    // 独立页不占中栏（memory_browser/86 同款 body:has 一行）；
    el.textContent = `
body:has(#page-synapse:not(.hidden)) #middleSidebar { display: none; }
.synapse-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-family: inherit;
  color: var(--color-text-secondary, #a3a3a3); cursor: pointer;
  background: transparent; border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  transition: border-color 0.15s ease;
}
.synapse-chip:hover { border-color: var(--color-brand, #E5734A); }
.synapse-chip.active {
  color: var(--color-brand, #E5734A);
  border-color: var(--color-brand, #E5734A);
  background: var(--color-brand-subtle, rgba(229,115,74,0.08));
}
.synapse-agent-tag {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 1px 8px; border-radius: 999px;
  font-size: 10px; color: var(--color-text-tertiary, #666666);
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
}
/* 器官态势板（工作台态势感知：诚实口径——事件只在任务完成时落库，
   故显示「最近完成于 N 前」而非「活跃/在线」；真状态属 C 档无数据源） */
.synapse-member-rail {
  width: 224px; flex-shrink: 0; overflow-y: auto;
  border-left: 1px solid var(--color-border, rgba(255,255,255,0.08));
  background: var(--color-bg-panel, #111111);
}
.synapse-member-rail-head {
  padding: 10px 14px 8px;
  font-size: 10px; letter-spacing: 0.06em;
  color: var(--color-text-tertiary, #666666);
  border-bottom: 1px solid var(--color-border, rgba(255,255,255,0.08));
}
.synapse-member-row {
  display: flex; flex-direction: column; gap: 3px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--color-border, rgba(255,255,255,0.08));
}
.synapse-member-row:last-child { border-bottom: none; }
.synapse-member-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.synapse-member-name {
  font-size: 11px; color: var(--color-text-primary, #ededed);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.synapse-member-meta { font-size: 10px; color: var(--color-text-tertiary, #666666); flex-shrink: 0; }
.synapse-member-sub { font-size: 10px; color: var(--color-text-tertiary, #666666); }
.synapse-member-types {
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace);
  line-height: 1.5; word-break: break-all;
}
.synapse-member-empty { padding: 12px 14px; font-size: 10px; color: var(--color-text-tertiary, #666666); }
/* ---- [v4-form①] 去气泡（等价移植 135 §22 的 #chatMessagesNormal 裁定到
   #page-synapse 作用域）：事件正文背景/描边/圆角/阴影归零，文档式流 ---- */
#page-synapse .msg-bubble__ai-box {
  background: transparent; border: none; border-radius: 0;
  padding: 0; box-shadow: none;
}
/* ---- [v4-form②] 台账三列：时间槽 / 成员列 / 徽章+主体（等宽数字对齐） ---- */
#page-synapse .synapse-row {
  display: flex; align-items: flex-start; gap: 12px;
  margin: 0 -8px; padding: 10px 8px; border-radius: 6px;
}
#page-synapse .synapse-row:hover { background: var(--color-bg-hover, rgba(255,255,255,0.04)); }
#page-synapse .synapse-time {
  flex: 0 0 68px; padding-top: 2px;
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace);
  font-size: 10px; color: var(--color-text-tertiary, #666666);
  font-variant-numeric: tabular-nums;
}
#page-synapse .synapse-who {
  flex: 0 0 84px; padding-top: 1px;
  font-size: 11px; color: var(--color-text-primary, #ededed);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
#page-synapse .synapse-main { flex: 1 1 auto; min-width: 0; }
#page-synapse .synapse-flags {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 4px;
}
#page-synapse .synapse-badge {
  display: inline-flex; align-items: center;
  padding: 1px 8px; border-radius: 999px;
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace); font-size: 10px;
  color: var(--color-text-secondary, #a3a3a3);
  border: 1px solid var(--color-border-strong, rgba(255,255,255,0.16));
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
}
#page-synapse .synapse-badge--ok {
  color: var(--color-success, #4ade80); border-color: var(--color-success, #4ade80);
}
#page-synapse .synapse-badge--warn {
  color: var(--color-warning, #fbbf24); border-color: var(--color-warning, #fbbf24);
}
#page-synapse .synapse-badge--bad {
  color: var(--color-error, #ef4444); border-color: var(--color-error, #ef4444);
}
#page-synapse .synapse-stat {
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace);
  font-size: 10px; color: var(--color-text-tertiary, #666666);
}
#page-synapse .synapse-mention--user {
  color: var(--color-brand, #E5734A); font-weight: 600;
}
/* ---- [v5-step6] 指令插话栏（composer）：工作台式发言条——令牌化描边、6px 方角
   （同台账行克制），行宽对齐 max-w-3xl；刻意避开 ChatGPT 式大圆角气泡 composer
   （UI_DIRECTION §四「不抄聊天框」）。#page-synapse 前缀压过 135 的全局 input
   泛用样式（那边 :is(...):where(...) 特异度低，ID 前缀恒胜）。 ---- */
#page-synapse .synapse-composer { display: flex; align-items: stretch; gap: 8px; }
#page-synapse .synapse-composer-input {
  flex: 1 1 auto; min-height: 34px; max-height: 120px;
  padding: 8px 10px; resize: none; overflow-y: auto;
  border-radius: 6px; font-family: inherit; font-size: 12px; line-height: 1.5;
  color: var(--color-text-primary, #ededed);
  background: var(--color-bg-panel, #111111);
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
}
#page-synapse .synapse-composer-input:focus {
  outline: none; border-color: var(--color-brand, #E5734A);
}
#page-synapse .synapse-composer-input::placeholder {
  color: var(--color-text-tertiary, #666666);
}
#page-synapse .synapse-composer-send {
  flex-shrink: 0; padding: 0 16px; border-radius: 6px; cursor: pointer;
  font-size: 11px; color: var(--color-text-secondary, #a3a3a3);
  background: transparent;
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  transition: border-color 0.15s ease;
}
#page-synapse .synapse-composer-send:hover:not(:disabled) {
  color: var(--color-brand, #E5734A); border-color: var(--color-brand, #E5734A);
}
#page-synapse .synapse-composer-send:disabled { opacity: 0.45; cursor: default; }
#page-synapse .synapse-composer-hint {
  margin-top: 6px; min-height: 12px;
  font-size: 10px; color: var(--color-text-tertiary, #666666);
}
#page-synapse .synapse-composer-hint--error {
  color: var(--color-error, #ef4444);
}
/* ---- [v5-step7] 链徽章（甲一分入口）+ 侧栏抽屉（形态丁）。徽章：中性描边胶囊、
   hover 品牌橙（与 chips 同纪律）；只挂在有 ≥2 事件的 task 行上。抽屉：fixed 右缘
   覆盖层——不依赖 page-panel 布局（群聊页无中栏，切页时必须程序化关闭，见 switchTo），
   遮罩点击/✕/Esc 三通道关。z-index 压过页面一切浮层。 ---- */
#page-synapse .synapse-chain-tag {
  display: inline-flex; align-items: center;
  padding: 1px 8px; border-radius: 999px; cursor: pointer;
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace); font-size: 10px;
  color: var(--color-text-tertiary, #666666);
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  transition: border-color 0.15s ease, color 0.15s ease;
}
#page-synapse .synapse-chain-tag:hover {
  color: var(--color-brand, #E5734A); border-color: var(--color-brand, #E5734A);
}
.synapse-drawer-overlay {
  position: fixed; inset: 0; z-index: 1500; background: rgba(0, 0, 0, 0.45);
}
.synapse-drawer {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 1501;
  width: min(480px, 92vw);
  flex-direction: column;
  background: var(--color-bg-panel, #111111);
  border-left: 1px solid var(--color-border, rgba(255,255,255,0.08));
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.35);
}
/* 坑（内容验收实锤）：基类里写 display:flex 会盖过 tailwind .hidden 的
   display:none（ensureStyle 注入点在 head 尾、加载序在后）——关着的抽屉
   视觉上常开、白占右缘 480px。开态声明收进 :not(.hidden)：带 hidden 时本
   规则不匹配，display 只剩 .hidden 的 none；不带 hidden 时 (0,2,0) 特异性
   恒胜 (0,1,0)。遮罩基类不声明 display，无此冲突，维持 hidden 即 none。 */
.synapse-drawer:not(.hidden) { display: flex; }
.synapse-drawer-head {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; border-bottom: 1px solid var(--color-border, rgba(255,255,255,0.08));
}
.synapse-drawer-title {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono-ui, ui-monospace, Consolas, monospace);
  font-size: 11px; color: var(--color-text-primary, #ededed);
}
.synapse-drawer-close {
  flex-shrink: 0; padding: 1px 8px; border-radius: 6px; cursor: pointer; font-size: 12px;
  color: var(--color-text-secondary, #a3a3a3);
  background: transparent; border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  transition: border-color 0.15s ease, color 0.15s ease;
}
.synapse-drawer-close:hover { color: var(--color-brand, #E5734A); border-color: var(--color-brand, #E5734A); }
.synapse-drawer-note {
  padding: 6px 14px; font-size: 10px; color: var(--color-text-tertiary, #666666);
  border-bottom: 1px solid var(--color-border, rgba(255,255,255,0.08));
}
.synapse-drawer-body { flex: 1 1 auto; overflow-y: auto; padding: 6px 10px; }
`;
    document.head.appendChild(el);
  }

  /* ---------------- 注入：导航按钮（工作台组）+ 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    // 插「工作台」组、对话之前（86 同款锚点）：顺序落在 应用中心 之后 → 应用中心, Synapse, 对话
    const group = document.querySelector('.nav-group[data-nav-group="work"]');
    const chatBtn = document.querySelector('.nav-icon-btn[data-page="chat"]');
    const nav = group || (chatBtn ? chatBtn.parentElement : null);
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', 'Synapse 事件总线');
    btn.innerHTML = '<i data-lucide="share-2" class="w-4 h-4"></i><span class="nav-icon-label">Synapse</span>';
    if (chatBtn && chatBtn.parentElement === nav) nav.insertBefore(btn, chatBtn);
    else nav.insertBefore(btn, nav.firstChild);
    btn.addEventListener('click', () => {
      switchTo(PAGE);
      ensureLoaded();
    });
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

  /* ---------------- 页面 DOM ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page || page.querySelector('#synapseMessages')) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="share-2" class="w-4 h-4 text-brand-cobalt flex-shrink-0"></i>
          <span class="text-sm font-medium text-text-primary">Synapse 事件总线</span>
          <span class="text-[11px] text-text-tertiary truncate" id="synapseHeadStatus">加载事件…</span>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <button id="synapseRefreshBtn" class="text-[11px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="立即重新拉取全部事件">
            <i data-lucide="refresh-cw" class="w-3 h-3"></i>刷新
          </button>
        </div>
      </div>
      <div class="px-5 py-2 border-b border-line-ghost/50 flex items-center gap-2 flex-wrap flex-shrink-0" id="synapseFilterRow"></div>
      <div class="flex-1 flex min-h-0">
        <div class="flex-1 min-w-0 overflow-y-auto p-5" id="synapseScroll">
          <div class="max-w-3xl mx-auto">
            <div id="synapseMessages"></div>
            <div id="synapseEmpty" class="hidden"></div>
          </div>
        </div>
        <div id="synapseMemberRail" class="synapse-member-rail"></div>
      </div>
      <div class="flex-shrink-0 border-t border-line-ghost/50 px-5 py-3">
        <div class="max-w-3xl mx-auto synapse-composer">
          <textarea id="synapseComposerInput" class="synapse-composer-input" rows="1" maxlength="2000"
            autocomplete="off" spellcheck="false"
            placeholder="对总线说一句…（Enter 发送 · Shift+Enter 换行）"></textarea>
          <button type="button" id="synapseComposerSend" class="synapse-composer-send"
            title="发言落总线（user.message）：只记入台账，不触发任何器官">发送</button>
        </div>
        <div class="max-w-3xl mx-auto synapse-composer-hint" id="synapseComposerHint" aria-live="polite"></div>
      </div>
      <div id="synapseDrawerOverlay" class="synapse-drawer-overlay hidden"></div>
      <aside id="synapseDrawer" class="synapse-drawer hidden" aria-label="任务链抽屉">
        <div class="synapse-drawer-head">
          <span id="synapseDrawerTitle" class="synapse-drawer-title">链</span>
          <span id="synapseDrawerStatus"></span>
          <button type="button" id="synapseDrawerClose" class="synapse-drawer-close" title="关闭链视图（或按 Esc）">✕</button>
        </div>
        <div id="synapseDrawerNote" class="synapse-drawer-note"></div>
        <div id="synapseDrawerBody" class="synapse-drawer-body"></div>
      </aside>`;
    // refresh-cw 在 30_chat 操作条已用（构建期图标校验白名单内），share-2 即 86 卡片图标
    const refreshBtn = page.querySelector('#synapseRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => { refresh(); });
  }

  function bindEvents() {
    const row = document.getElementById('synapseFilterRow');
    if (!row || row.dataset.synapseBound === '1') return;
    row.dataset.synapseBound = '1';
    // 筛选 chips：点击委托（chips 由 renderFilterRow 随 overview 重建）
    row.addEventListener('click', (e) => {
      const chip = e.target.closest && e.target.closest('.synapse-chip');
      if (!chip) return;
      _filter = chip.dataset.source || '';
      renderAll();
    });
    // [v5-step7] 链徽章/胶囊深链丸：统一 [data-task] 委托——挂 #synapseMessages
    // 容器本身（renderAll 只重建其 innerHTML，容器常驻），全生命周期绑一次。
    const box = document.getElementById('synapseMessages');
    if (box && !box.dataset.synapseBound) {
      box.dataset.synapseBound = '1';
      box.addEventListener('click', (e) => {
        const t = e.target.closest && e.target.closest('[data-task]');
        if (t) openChainDrawer(String(t.getAttribute('data-task') || ''));
      });
    }
    // [v5-step6] 指令插话栏：Enter 发送 / Shift+Enter 换行（e.isComposing 挡中文
    // IME 组字）；input 时清 hint 并自适应高度（34→120px 封顶）
    const ta = document.getElementById('synapseComposerInput');
    if (ta && !ta.dataset.synapseBound) {
      ta.dataset.synapseBound = '1';
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          sendUserMessage();
        }
      });
      ta.addEventListener('input', () => {
        setComposerHint('');
        ta.style.height = 'auto';
        ta.style.height = Math.min(120, Math.max(34, ta.scrollHeight)) + 'px';
      });
    }
    const sendBtn = document.getElementById('synapseComposerSend');
    if (sendBtn) {
      sendBtn.dataset.synapseBound = '1';
      sendBtn.addEventListener('click', () => sendUserMessage());
    }
    // [v5-step7] 抽屉关闭三通道：✕ / 遮罩 / Esc（本函数经 row.dataset 守卫只跑
    // 一次，document 级 keydown 不会叠挂；Esc 仅抽屉开着时生效，不劫持其他页）
    const ov = document.getElementById('synapseDrawerOverlay');
    if (ov) ov.addEventListener('click', () => closeChainDrawer());
    const dc = document.getElementById('synapseDrawerClose');
    if (dc) dc.addEventListener('click', () => closeChainDrawer());
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && _drawerTask) closeChainDrawer();
    });
    // [v5-step7] 主程序切页只翻 .page-panel 的 .hidden 类、不经本文件 switchTo
    // （实测：nav 按钮切到对话页，fixed 抽屉+遮罩悬在别页上）。保险改为观察本页
    // class 变化：一离开可见立即收抽屉。只盯 #page-synapse 单目标，attributes 过滤
    // 单类，成本可忽略（A-1.6 逐 .page-panel 观察的同款精神，范围更窄）。
    const pageEl = document.getElementById('page-' + PAGE);
    if (pageEl && window.MutationObserver) {
      new MutationObserver(() => {
        if (!pageVisible() && _drawerTask) closeChainDrawer();
      }).observe(pageEl, { attributes: true, attributeFilter: ['class'] });
    }
  }

  /* ---------------- [v5-step7] 任务链抽屉（形态丁；数据 = _chainIdx 现算） ---------------- */

  /** 链状态推导：纯从库行终态来，绝不吃 /chains 内存端点（重启清零语义与视图无关）。
   *  succeeded 事件存在 → succeeded；failed/error → failed；都没有 → 进行中。
   *  （同 task 双终态在分键幂等下不可能：裸键与 :failed 键各至多一行。） */
  function chainStatusOf(evs) {
    if (evs.some(m => /succeeded|completed/.test(String(m.proxy_source || '')))) return 'succeeded';
    if (evs.some(m => /failed|error/.test(String(m.proxy_source || '')))) return 'failed';
    return 'running';
  }

  function openChainDrawer(taskId) {
    const chain = _chainIdx.get(taskId);
    if (!chain || chain.length < 2) return;   // 单事件链无从展开（徽章也不会出现）
    _drawerTask = taskId;
    renderChainDrawer();
    const ov = document.getElementById('synapseDrawerOverlay');
    const dr = document.getElementById('synapseDrawer');
    if (ov) ov.classList.remove('hidden');
    if (dr) dr.classList.remove('hidden');
  }

  function closeChainDrawer() {
    _drawerTask = '';
    const ov = document.getElementById('synapseDrawerOverlay');
    const dr = document.getElementById('synapseDrawer');
    if (ov) ov.classList.add('hidden');
    if (dr) dr.classList.add('hidden');
  }

  /** 抽屉渲染：头（task 前 8 位 + 状态徽章）+ 注（事件数·起止·无起点如实标）+
   *  体（同 task 事件按时间升序，逐行复用 synapseBuildEl——形态与台账一致；
   *  inDrawer=true 压掉链徽章与深链丸，抽屉内绝不出现再入口，递归面为零）。
   *  抽屉开着时新事件到达（renderAll 每拍调本函数）→ 内容实时跟新。 */
  function renderChainDrawer() {
    const body = document.getElementById('synapseDrawerBody');
    if (!body) return;
    const chain = _chainIdx.get(_drawerTask) || [];
    if (!chain.length) { closeChainDrawer(); return; }   // 链数据消失（理论不发生）：如实收抽屉
    const title = document.getElementById('synapseDrawerTitle');
    if (title) {
      title.textContent = '链 · ' + _drawerTask.slice(0, 8) + ' · ' + chain.length + ' 个事件';
      title.title = _drawerTask;   // 全量 task_id 进悬停（取证深链的"链"即这条）
    }
    const st = chainStatusOf(chain);
    const stBox = document.getElementById('synapseDrawerStatus');
    if (stBox) {
      const cls = st === 'succeeded' ? ' synapse-badge--ok'
        : st === 'failed' ? ' synapse-badge--bad' : ' synapse-badge--warn';
      stBox.innerHTML = '<span class="synapse-badge' + cls + '">'
        + escapeHtml(st === 'running' ? '进行中' : st) + '</span>';
    }
    const note = document.getElementById('synapseDrawerNote');
    if (note) {
      const hasStart = chain.some(m => /(^|\.)started$/.test(String(m.proxy_source || '')));
      note.textContent = clockTime(chain[0].createdAt || chain[0].created_at) + ' → '
        + clockTime(chain[chain.length - 1].createdAt || chain[chain.length - 1].created_at)
        + (hasStart ? '' : ' · 未记录起点（生命周期事件上线前结束，不回填）');
    }
    const frag = document.createDocumentFragment();
    chain.forEach(m => frag.appendChild(synapseBuildEl(m, false, true)));
    body.innerHTML = '';
    body.appendChild(frag);
    refreshIcons();
  }

  /* ---------------- 数据客户端（window.MoraySynapse） ---------------- */

  function _base() {
    try {
      if (typeof agentBackendBase === 'function') return agentBackendBase();
    } catch (e) { /* 忽略，走兜底（memory_client.js 同款兜底链） */ }
    return (window.MorayBackend && window.MorayBackend.origin) || 'http://127.0.0.1:8000';
  }

  /** 后端行 → 展示行：createdAt(ms) 供 clockTime/appendWithTimeSeparators（ISO 供增量游标） */
  function toLocal(row) {
    const ms = new Date(row.created_at).getTime();
    return Object.assign({}, row, { createdAt: isNaN(ms) ? 0 : ms });
  }

  /** GET /api/synapse/messages。失败显式抛错（本页是用户主动观测页，绝不 fail-open
   *  吞成空列表假装没事件——轮询层的退避由 tick 的 catch 负责） */
  async function fetchMessages(opts) {
    const o = opts || {};
    let url = _base() + '/api/synapse/messages?limit=' + Math.max(1, Math.min(200, o.limit || 100));
    if (o.since) url += '&since=' + encodeURIComponent(o.since);
    const resp = await fetch(url, { cache: 'no-store' });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try { const j = await resp.json(); if (j && j.message) msg = String(j.message); } catch (e) { /* 非 JSON 响应保留状态码 */ }
      throw new Error('Synapse 消息拉取失败（' + msg + '）');
    }
    const body = await resp.json();
    if (!body || body.ok !== true || !body.data || !Array.isArray(body.data.items)) {
      throw new Error('Synapse 消息响应结构异常');
    }
    return body.data;
  }

  /** GET /api/synapse/overview。失败抛错（调用方决定是否降级——页头计数回退 _items.length） */
  async function fetchOverview() {
    const resp = await fetch(_base() + '/api/synapse/overview', { cache: 'no-store' });
    if (!resp.ok) throw new Error('Synapse 概览拉取失败（HTTP ' + resp.status + '）');
    const body = await resp.json();
    if (!body || body.ok !== true || !body.data) throw new Error('Synapse 概览响应结构异常');
    return body.data;
  }

  async function refreshOverview() {
    try {
      _overview = await fetchOverview();
    } catch (e) { /* 页头计数回退 _items.length，错误态由 tick 的消息拉取统一呈现 */ }
    renderAll();
  }

  /** 首屏全量（最近 100 条，升序）。失败保持 _loaded=false，由轮询/下次激活重试 */
  async function loadInitial() {
    const data = await fetchMessages({ limit: 100 });
    _items = (data.items || []).map(toLocal).reverse(); // 后端 DESC → 展示 ASC
    _since = _items.length ? _items[_items.length - 1].created_at : '';
    _loaded = true;
    renderAll();
    refreshOverview();
  }

  /** 增量轮询一拍：since=游标（后端 >= 语义，同秒事件不漏，按 id 幂等去重） */
  async function pollIncrement() {
    const data = await fetchMessages({ since: _since, limit: 100 });
    const fresh = (data.items || [])
      .map(toLocal)
      .filter(n => !_items.some(m => m.id === n.id));
    if (!fresh.length) return;
    fresh.sort((a, b) => (a.createdAt - b.createdAt) || String(a.id).localeCompare(String(b.id)));
    _items = _items.concat(fresh);
    _since = _items[_items.length - 1].created_at;
    renderAll();
    refreshOverview();
  }

  /* ---------------- 轮询调度（3s；hidden 暂停；离线退避） ---------------- */

  function pageVisible() {
    const p = document.getElementById('page-' + PAGE);
    return !!(p && !p.classList.contains('hidden'));
  }

  function schedule() {
    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(tick, _pollMs);
  }

  async function tick() {
    // 暂停语义（钉子③）：页面切走 / 浏览器后台 → 本拍跳过，定时链保留空转；
    // 回前台由 visibilitychange 立即补一拍，不等下一轮
    if (_inFlight || document.hidden || !pageVisible()) { schedule(); return; }
    _inFlight = true;
    try {
      if (!_loaded) await loadInitial();
      else await pollIncrement();
      _errStreak = 0;
      _pollMs = POLL_MS;
      syncHeadStatus();
    } catch (e) {
      _errStreak += 1;
      _pollMs = Math.min(POLL_MAX_MS, POLL_MS * Math.pow(2, Math.min(_errStreak, 4)));
      syncHeadStatus(e);
    } finally {
      _inFlight = false;
      schedule();
    }
  }

  /** 启动轮询（幂等；mount 时调用，tick 对隐藏页自守卫，空转零请求） */
  function start() {
    _pollMs = POLL_MS;
    schedule();
  }

  /** 停止轮询（供调试/卸载；本页常驻，正常路径不调） */
  function stop() {
    if (_timer) { clearTimeout(_timer); _timer = null; }
  }

  /** 手动/程序化全量重拉（清游标；首屏失败后的重试入口） */
  function refresh() {
    _loaded = false;
    _since = '';
    _pollMs = POLL_MS;
    if (!_inFlight) tick();
  }

  function ensureLoaded() {
    if (_loaded || _inFlight) return;
    tick();
  }

  // 回到前台：若本页正显示，立即补一拍（不等剩余退避/间隔）
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && pageVisible()) { _pollMs = POLL_MS; tick(); }
  });

  /* ---------------- [v5-step6] 潜水员插话（POST + 乐观 append） ---------------- */

  /** 每次发言新生成 uuid 作 trace_id：send 判重按全表 trace 等值——绝不能用内容
   *  派生（同句重发会被静默吞）；本 uuid 只作网络重试幂等键（超时重发同键 =
   *  落一次库、返回同一行）。crypto 缺席时时间戳兜底，唯一性够用。 */
  function composerUuid() {
    try {
      if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    } catch (e) { /* 忽略，走兜底 */ }
    return 'u' + Date.now().toString(16) + '-' + Math.random().toString(16).slice(2, 10);
  }

  function setComposerHint(text, isErr) {
    const hint = document.getElementById('synapseComposerHint');
    if (!hint) return;
    hint.textContent = text || '';
    hint.classList.toggle('synapse-composer-hint--error', !!isErr);
  }

  /** 发言一拍：POST → 用服务端回行（与 GET items 同构）直接 append + renderAll。
   *  不做「乐观占位-替换」两拍中间态；不等 3s 轮询。幂等：_since 用服务端
   *  created_at 前移（ISO 定长同偏移，字典序=时间序；后端 >= 语义下不重不漏），
   *  下一拍即使重拉到同 id 也被 pollIncrement 现有去重吞掉。失败：输入原文保留，
   *  hint 条报因（bus_disabled / 校验错 / 后端离线）。 */
  async function sendUserMessage() {
    if (_sending) return;
    const ta = document.getElementById('synapseComposerInput');
    const btn = document.getElementById('synapseComposerSend');
    if (!ta) return;
    const text = String(ta.value || '').trim();
    if (!text) return;
    _sending = true;
    if (btn) btn.disabled = true;
    setComposerHint('');
    try {
      const resp = await fetch(_base() + '/api/synapse/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, trace_id: composerUuid() }),
      });
      let j = null;
      try { j = await resp.json(); } catch (e) { /* 非 JSON 响应统一走下方错误分支 */ }
      if (!resp.ok || !j || j.ok !== true || !j.data || !j.data.id) {
        const why = (j && (j.message || j.code)) || ('HTTP ' + resp.status);
        throw new Error(String(why).slice(0, 120));
      }
      const row = toLocal(j.data);
      if (!_items.some(m => m.id === row.id)) {
        _items.push(row);
        _items.sort((a, b) => (a.createdAt - b.createdAt) || String(a.id).localeCompare(String(b.id)));
      }
      if (_since === '' || String(row.created_at) > _since) _since = row.created_at;
      ta.value = '';
      ta.style.height = 'auto';
      renderAll();
    } catch (e) {
      setComposerHint('没能发言：' + String((e && e.message) || e), true);
    } finally {
      _sending = false;
      if (btn) btn.disabled = false;
    }
  }

  /* ---------------- 渲染 ---------------- */

  function syncHeadStatus(err) {
    const el = document.getElementById('synapseHeadStatus');
    if (!el) return;
    if (err) {
      el.textContent = '后端离线，' + Math.round(_pollMs / 1000) + 's 后重试';
      el.title = String(err && err.message || err).slice(0, 200);
      return;
    }
    el.removeAttribute('title');
    const total = (_overview && typeof _overview.total === 'number') ? _overview.total : _items.length;
    const kinds = (_overview && Array.isArray(_overview.by_source)) ? _overview.by_source.length : 0;
    el.textContent = '共 ' + total + ' 条事件 · ' + kinds + ' 类 · 轮询 ' + Math.round(_pollMs / 1000) + 's';
  }

  function renderFilterRow() {
    const row = document.getElementById('synapseFilterRow');
    if (!row) return;
    const sources = (_overview && Array.isArray(_overview.by_source)) ? _overview.by_source : [];
    let html = '<button type="button" class="synapse-chip' + (_filter === '' ? ' active' : '') + '" data-source="">全部</button>';
    html += sources.map(s =>
      '<button type="button" class="synapse-chip' + (_filter === s.source ? ' active' : '') + '" data-source="' + escapeHtml(s.source) + '">'
      + escapeHtml(s.source || '(空)') + ' · ' + s.count + '</button>').join('');
    row.innerHTML = html;
  }

  /** 器官态势板（工作台态势感知，非成员列表——UI_DIRECTION §八禁区）。
   *  [v4-form④] 诚实口径：总线事件只在任务完成时落库（api.py rc==0 判据），
   *  故行内只说「最近完成于 N 前」+ 计数 + 类型分布，绝不出现
   *  在线/活跃/进行中（真状态 C 档无数据源——诊断报告红线）。
   *  [v5 step3] 主源 = overview.by_member（后端全库聚合：count / last_seen（ISO，
   *  与其余 created_at 同走 _iso 裁定，new Date 直解析）/ types[{source, count}]）；
   *  _overview 缺失或无 by_member（概览请求失败/旧后端无此字段）时整段回退
   *  _items 客户端聚合——观测窗口口径降级保留，主路径下计数不再被 100 条窗口截断。
   *  两路聚成同形行 [key, {count, last(ms), types}]；排序计数降序、同数按最近时间
   *  降序（排序留前端，改动面最小）；显示名走 MEMBER_DISPLAY，未登记原样。
   *  relTime 为 10_db.js 全局函数（运行时直调，跨片纪律同 clockTime）。
   *  [v5-step7] 诚实口径的 user 例外：潜水员不"完成"任务，user 行措辞
   *  「最近完成于」→「最近发言于」（display 一行条件化，数据层零动——第六条观察项收口）。 */
  function renderMemberRail() {
    const rail = document.getElementById('synapseMemberRail');
    if (!rail) return;
    let rows;
    if (_overview && Array.isArray(_overview.by_member) && _overview.by_member.length) {
      rows = _overview.by_member.map(b => {
        const types = {};
        (Array.isArray(b.types) ? b.types : []).forEach(t => {
          const s = String((t && t.source) || '(空)');
          types[s] = (types[s] || 0) + (Number((t && t.count) || 0));
        });
        const ms = new Date(b && b.last_seen).getTime();
        return [String((b && b.member) || '(空)'),
          { count: Number((b && b.count) || 0), last: isNaN(ms) ? 0 : ms, types }];
      });
    } else {
      const agg = new Map();
      _items.forEach(m => {
        const key = String(m.from_member || '(空)');
        const cur = agg.get(key) || { count: 0, last: 0, types: {} };
        cur.count += 1;
        if ((m.createdAt || 0) > cur.last) cur.last = m.createdAt || 0;
        const t = String(m.proxy_source || '(空)');
        cur.types[t] = (cur.types[t] || 0) + 1;
        agg.set(key, cur);
      });
      rows = Array.from(agg.entries());
    }
    rows.sort((a, b) => (b[1].count - a[1].count) || (b[1].last - a[1].last));
    const rel = typeof relTime === 'function' ? relTime : clockTime;
    rail.innerHTML = '<div class="synapse-member-rail-head">器官态势 · ' + rows.length + '</div>'
      + (rows.length ? rows.map(([k, v]) => {
        const types = Object.entries(v.types)
          .sort((a, b) => b[1] - a[1]).slice(0, 3)
          .map(([t, n]) => escapeHtml(t) + ' ×' + n).join('<br>');
        return '<div class="synapse-member-row">'
          + '<div class="synapse-member-top">'
          + '<span class="synapse-member-name" title="' + escapeHtml(k) + '">' + escapeHtml(MEMBER_DISPLAY[k] || k) + '</span>'
          + '<span class="synapse-member-meta">' + v.count + ' 条</span>'
          + '</div>'
          + '<div class="synapse-member-sub">' + (k === 'user' ? '最近发言于 ' : '最近完成于 ') + escapeHtml(rel(v.last)) + '</div>'
          + (types ? '<div class="synapse-member-sub synapse-member-types">' + types + '</div>' : '')
          + '</div>';
      }).join('')
      : '<div class="synapse-member-empty">窗口内暂无事件</div>');
  }

  function renderEmptyState() {
    const empty = document.getElementById('synapseEmpty');
    const box = document.getElementById('synapseMessages');
    if (!empty || !box) return;
    const show = _items.length === 0;
    empty.classList.toggle('hidden', !show);
    box.classList.toggle('hidden', show);
    if (!show) return;
    // 空态如实说边界：总闸关 / 库空两种可能一并给出；只读台账定位写明（钉子②）
    empty.innerHTML = `
      <div class="empty-art">
        <div class="empty-icon"><i data-lucide="share-2" class="w-5 h-5 text-text-tertiary"></i></div>
        <div class="empty-title">总线未开启或暂无事件</div>
        <div class="empty-hint">后端 .env 设 MORAY_SYNAPSE=1 并重启 8000 后，触发一次 ZCode 任务即可在此看到神经总线事件。<br>事件台账：各器官在任务生命周期节点（started/progress/succeeded/failed，v5 step1 已上线）旁路写入总线；潜水员从底部指令栏发言插话（user.message，v5 第六条已上线——只落账，不触发器官）。同一任务的多个生命周期事件以「链·N·前8位」徽章相认，点开侧栏看完整流转（v5 第七条）。</div>
      </div>`;
  }

  /** 执行轨迹胶囊（内联样式抄 memoryRecallHtml 30_chat.js:962-987）。
   *  [v4-form] 徽章行已承载 proxy_source / 耗时 / 退出码，胶囊收缩为
   *  trace_id（前 8 位）+ task_id 两项——纯取证用途（v5 深链落点候选）。
   *  两项全空返回 ''（如 reflection 类无执行负载的事件不挂空胶囊）。
   *  [v5-step7] 深链收口：task_id 丸在所属链 ≥2 事件时升级为可点深链（data-task
   *  走 #synapseMessages 的 [data-task] 委托，与行徽章同开同一抽屉——两入口一行为）；
   *  抽屉内行（inDrawer=true）不升级——丸就在链视图里，再点自己无意义且违递归纪律。 */
  function synapseTraceHtml(m, inDrawer) {
    const meta = (m && m.meta && typeof m.meta === 'object') ? m.meta : {};
    const trace = String((m && m.trace_id) || '').trim();
    const keys = ['task_id'];
    const hasMeta = keys.some(k => meta[k] !== undefined && meta[k] !== null && meta[k] !== '');
    if (!trace && !hasMeta) return '';
    const pill = 'display:inline-block;margin:1px 4px 1px 0;padding:0 6px;border:1px solid var(--color-line-ghost);border-radius:999px;font-size:10px;color:var(--color-text-tertiary)';
    const summary = 'cursor:pointer;font-size:10px;color:var(--color-text-tertiary);border:1px solid var(--color-line-ghost);border-radius:999px;padding:1px 8px';
    let chips = '';
    if (trace) chips += '<span style="' + pill + '">trace ' + escapeHtml(trace.slice(0, 8)) + '</span>';
    keys.forEach(k => {
      if (meta[k] !== undefined && meta[k] !== null && meta[k] !== '') {
        const val = String(meta[k]);
        const deep = !inDrawer && k === 'task_id'
          && ((_chainIdx.get(val) || []).length >= 2);
        chips += '<span style="' + pill + (deep ? ';cursor:pointer' : '') + '"'
          + (deep ? ' data-task="' + escapeHtml(val) + '" title="打开任务链视图"' : '')
          + '>' + k + ' ' + escapeHtml(val) + '</span>';
      }
    });
    return '<details class="mt-2" style="display:inline-block;max-width:100%">'
      + '<summary style="' + summary + '"><i data-lucide="terminal" class="w-3 h-3 inline"></i>执行轨迹</summary>'
      + '<div style="font-size:10px;color:var(--color-text-tertiary);margin-top:4px;max-width:560px">' + chips + '</div>'
      + '</details>';
  }

  /** [v4-form②] 台账时间槽：HH:MM:SS（clockTime 全局只有 HH:MM，故本页私有补秒位） */
  function synapseClockSec(ts) {
    const d = ts instanceof Date ? ts : new Date(ts || Date.now());
    return isNaN(d.getTime()) ? '--:--:--' : d.toTimeString().slice(0, 8);
  }

  /** [v4-form②] event_type → 徽章语义类（走 --color-success/warning/error 令牌，
   *  零硬编码）：succeeded→ok、failed/error/throttled→bad、warn/pending→warn，
   *  未知类型中性色——未知不冒充已知。数据源恒为顶层 proxy_source（live 实证
   *  meta.event_type 与顶层可不一致，不信 meta）。 */
  function synapseEventBadgeClass(ev) {
    const s = String(ev || '').toLowerCase();
    if (/succeeded|completed/.test(s)) return ' synapse-badge--ok';
    if (/failed|error|throttl/.test(s)) return ' synapse-badge--bad';
    if (/warn|pending|progress|started/.test(s)) return ' synapse-badge--warn';
    return '';
  }

  /** [v4-form②③] 事件行 → 单条 DOM：台账三列（时间槽 HH:MM:SS mono / 成员列 /
   *  event_type 徽章 + 耗时/退出码 + 正文文档流）。聊天模板残留全部移除：头像、
   *  气泡卡、beacon-dot 装饰脉冲点、左右 justify（user 分支本是死代码——总线只有
   *  organ.* 与 system 两类写入口）。
   *  [Synapse v3] 系统消息（from_member=system，闸触发 synapse.throttled 等）：
   *  居中灰斜体窄条保留（有语义的工作台低调警示，形态参照 memory_browser .mem-empty
   *  先例）；content 照常 renderMarkdown（容器内左对齐保可读），不进台账/轨迹分支。 */
  function synapseBuildEl(m, animate, inDrawer) {
    const wrap = document.createElement('div');
    if (String(m.from_member || '') === 'system') {
      wrap.className = (animate ? 'msg-enter ' : '') + 'flex justify-center';
      wrap.innerHTML = `
        <div class="max-w-[85%] rounded-lg border border-line-ghost/60 bg-surface-card/40 px-3 py-1.5">
          <div class="text-center text-[10px] text-text-tertiary msg-meta-line">系统 · ${escapeHtml(clockTime(m.createdAt || m.created_at))}</div>
          <div class="md-body ai-content text-[11px] italic text-text-tertiary">${renderMarkdown(m.content || '')}</div>
        </div>`;
      refreshIcons();
      return wrap;
    }
    wrap.className = (animate ? 'msg-enter ' : '') + 'synapse-row';
    wrap.dataset.msgId = m.id;
    const who = MEMBER_DISPLAY[String(m.from_member || 'system')] || String(m.from_member || 'system');
    const meta = (m.meta && typeof m.meta === 'object') ? m.meta : {};
    const evType = String(m.proxy_source || '').trim() || '-';
    const dur = (meta.duration_ms !== undefined && meta.duration_ms !== null && meta.duration_ms !== '')
      ? meta.duration_ms + 'ms' : '';
    const rc = (meta.exit_code !== undefined && meta.exit_code !== null && meta.exit_code !== '')
      ? 'rc=' + meta.exit_code : '';
    const mentions = Array.isArray(m.mentions) ? m.mentions : [];
    // [v4] B 类代理标注：仅被动代理成员（PROXY_MEMBERS）加标；A 类器官主动推不标
    const agentTag = PROXY_MEMBERS.has(String(m.from_member || ''))
      ? '<span class="synapse-agent-tag"><i data-lucide="bot" class="w-3 h-3"></i>经 Synapse 代理</span>'
      : '';
    // [v5-step7] 链徽章（甲一分入口）：同 task_id ≥2 事件才出现（单事件链/无键行
    // 不加，避免噪音——老链无 started 也按实际事件数算）；抽屉内行（inDrawer）不产，
    // 徽章→抽屉入口单向，无重开递归面。点击经 #synapseMessages 容器 [data-task] 委托。
    const chainTid = String(meta.task_id || '').trim();
    const chainOfRow = (!inDrawer && chainTid) ? (_chainIdx.get(chainTid) || null) : null;
    const chainTag = (chainOfRow && chainOfRow.length >= 2)
      ? '<button type="button" class="synapse-chain-tag" data-task="' + escapeHtml(chainTid) + '"'
        + ' title="查看该任务完整流转（' + chainOfRow.length + ' 个事件）">链·' + chainOfRow.length
        + '·' + escapeHtml(chainTid.slice(0, 8)) + '</button>'
      : '';
    const recallMsg = (m.meta && m.meta.memoryRecall && Array.isArray(m.meta.memoryRecall.items))
      ? { memoryRecall: m.meta.memoryRecall } : null;
    wrap.innerHTML = `
      <div class="synapse-time">${escapeHtml(synapseClockSec(m.createdAt || m.created_at))}</div>
      <div class="synapse-who" title="${escapeHtml(String(m.from_member || ''))}">${escapeHtml(who)}</div>
      <div class="synapse-main">
        <div class="synapse-flags">
          <span class="synapse-badge${synapseEventBadgeClass(evType)}">${escapeHtml(evType)}</span>
          ${dur ? '<span class="synapse-stat">' + escapeHtml(String(dur)) + '</span>' : ''}
          ${rc ? '<span class="synapse-stat">' + escapeHtml(String(rc)) + '</span>' : ''}
          ${chainTag}
          ${agentTag}
          ${mentions.map(t => (String(t) === 'user'
            ? '<span class="synapse-stat synapse-mention--user">@' + escapeHtml(MEMBER_DISPLAY.user) + '</span>'
            : '<span class="synapse-stat">@' + escapeHtml(t) + '</span>')).join(' ')}
        </div>
        <div class="synapse-body">
          <div class="md-body ai-content">${renderMarkdown(m.content || '')}</div>
          ${synapseTraceHtml(m, inDrawer)}
          ${recallMsg ? memoryRecallHtml(recallMsg) : ''}
        </div>
      </div>`;
    refreshIcons();
    return wrap;
  }

  function renderAll() {
    const box = document.getElementById('synapseMessages');
    if (!box) return;
    const scroll = document.getElementById('synapseScroll');
    const nearBottom = scroll
      ? (scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 60)
      : true;

    // [v5-step7] 链索引每拍重建（O(n) 现算；_items 恒时间升序 → 组内数组天然升序，
    // 抽屉免再排序）。无 task_id 行（user/system/argus/laya 空报）不入索引。
    _chainIdx = new Map();
    _items.forEach(m => {
      const tid = (m.meta && typeof m.meta === 'object') ? String(m.meta.task_id || '') : '';
      if (!tid) return;
      const arr = _chainIdx.get(tid);
      if (arr) arr.push(m); else _chainIdx.set(tid, [m]);
    });

    const list = _filter
      ? _items.filter(m => String(m.proxy_source || '') === _filter)
      : _items;
    box.innerHTML = '';
    if (list.length && typeof appendWithTimeSeparators === 'function') {
      // 复用 90_perf 时间分隔线（>5 分钟；读 m.createdAt 毫秒——toLocal 已备）
      appendWithTimeSeparators(box, list, synapseBuildEl);
    } else {
      const frag = document.createDocumentFragment();
      list.forEach(m => frag.appendChild(synapseBuildEl(m, false)));
      box.appendChild(frag);
    }
    renderEmptyState();
    renderFilterRow();
    renderMemberRail();
    syncHeadStatus();
    refreshIcons();
    renderUserWake();
    if (_drawerTask) renderChainDrawer();   // [v5-step7] 抽屉开着 → 内容跟拍刷新
    if (scroll && nearBottom) scroll.scrollTop = scroll.scrollHeight;
  }

  /** [v5 step2] @user 叫醒：renderAll 尾部跑（首屏/增量/手动刷新全路径覆盖）。
   *  mentions 含 'user' 的行 → 调 desktopNotify（95_features.js:487 跨分片全局，
   *  v4 纪律运行时调用：typeof 判存在，95 未加载时静默跳过）。
   *  两道去重：_notifiedIds 拦同条重弹（3s 轮询每次全量重绘）；
   *  _wakeSeeded 让首批带数据渲染只登记不弹——打开页面不为历史失败事件回放轰炸，
   *  之后轮询新到达的 @user 消息才真弹。 */
  function renderUserWake() {
    if (!_wakeSeeded) {
      if (!_items.length) return;  // buildPage 首帧空列表不算"首批带数据"
      _wakeSeeded = true;
      _items.forEach(m => {
        if (Array.isArray(m.mentions) && m.mentions.includes('user')) _notifiedIds.add(m.id);
      });
      return;
    }
    _items.forEach(m => {
      if (String(m.from_member || '') === 'user') return;  // [v5-step6] 防自弹第二道保险（端点已硬置空 mentions）
      if (!Array.isArray(m.mentions) || !m.mentions.includes('user')) return;
      if (_notifiedIds.has(m.id)) return;
      _notifiedIds.add(m.id);
      if (typeof desktopNotify === 'function') {
        desktopNotify('MoRay 需要你', String(m.content || '').slice(0, 120));
      }
    });
  }

  /* ---------------- 页面切换（memory_browser.js 同款实现；供启动期程序化进入） ---------------- */

  function switchTo(page) {
    if (_drawerTask) closeChainDrawer();   // [v5-step7] 抽屉是 fixed 覆盖层：切页必关，绝不悬在其他页上
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

  /* ---------------- 挂载（DOMContentLoaded 自挂载；幂等） ---------------- */

  function mount() {
    ensureStyle();
    ensureNavButton();
    ensurePage();
    buildPage();
    bindEvents();
    renderAll();          // 空态 + 筛选行 + 页头先就位（数据后到）
    start();              // 轮询常开：tick 对隐藏页自守卫，打开页面 ≤3s 内见数
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  return {
    mount: mount, switchTo: switchTo, PAGE: PAGE,
    start: start, stop: stop, refresh: refresh
  };
})();
window.SynapseChatApp = SynapseChatApp;
window.MoraySynapse = { start: SynapseChatApp.start, stop: SynapseChatApp.stop, refresh: SynapseChatApp.refresh };

/* 自挂载：140 位于组装块尾段、70 bootApp 之后加载，不等 bootApp；
   失败 console.warn 不中断组装块后续代码（SW 注册等）。 */
(function () {
  const boot = function () {
    try { SynapseChatApp.mount(); }
    catch (e) { console.warn('[MoRay] Synapse 事件总线页挂载失败:', e); }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
