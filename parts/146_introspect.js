/* ===================== [自省可见化 v1] 左侧栏「自省」面板 ===================== */
/* 数据源：GET /api/self_check/report（server/app/self_check.py 只读端点；
   首轮自检未完成前返回 503 not_ready，前端如实提示、不伪造数据）。
   范式：仿 140_synapse/86_home_apps/memory_browser 运行时注入——nav 按钮 +
   page-panel 容器 + 自带 switchTo（骨架切页总控只绑解析期存在的按钮，注入
   按钮必须自带切页，140 注释 [v5-step7] 同款纪律）。
   红线：不改后端、不改 self_check.py、不改骨架 HTML、不动任何已有面板；
   图标复用 activity/brain/refresh-cw（均已被现网使用，不新增图标名，
   构建期 Lucide 校验图标总数保持 124）。
   刷新策略：进入面板拉一次 + 页头手动刷新按钮；无轮询。
   [v1.1 2026-10-10] 追加第六卡「记忆」：GET /api/memory/stats 与 report 并行拉取、
   独立失败（只标本卡不拖五卡）；不新增图标名（emoji 标题与现有卡同款）。
   [v1.2 2026-10-13] 追加第八卡「写入拒绝」：GET /api/memory/rejections 同款并行拉取、
   独立失败（只标本卡）；数据源=errors.log 尾部 [memory_reject] 行（只读回读，零写库）。
   [v1.3 2026-10-21] 追加第九卡「自我叙事」：GET /api/self_narrative/latest 同款并行拉取、
   独立失败（只标本卡）；MEM_KIND_ORDER 补 self_narrative 单列（桥三 stage2 产物）。
   [v1.4 2026-10-23] 追加第十卡「🎯 自驱目标」：GET /api/self_driven/queue 同款并行拉取、
   独立失败（只标本卡）；每条目标带「执行（送 ZCode）」「忽略（X）」按钮——执行=前端取目标
   文本 POST /api/engines/zcode/tasks {prompt}，成功后 POST /api/self_driven/approve 出队
   + Toast「已派发给 ZCode 引擎」；忽略=POST /api/self_driven/reject 出队。事件委托用
   composedPath（125:409 纪律）；不新增图标名（emoji 标题 + 纯文字按钮，Lucide 总数守 124）。
   [v2 2026-10-23] 自驱目标卡多引擎路由 + 执行闭环：卡带 target_engine 标签（[ZCode]/
   [Laya]）与状态徽章（待审批/执行中…/已完成/失败）；「执行」按卡上引擎拼通用路由
   POST /api/engines/<name>/tasks（引擎名过 URL 段白名单，30_chat ENGINE_NAME_RE 同款
   纪律），拿到引擎 task_id 后 POST /api/self_driven/approve {task_id, engine_task_id}
   登记派发——卡不删、就地流转「执行中…」且按钮置灰；终态（done/failed）由后端引擎
   收尾事件经 synapse 闭环回写，刷新即见历史（queue 已返全状态卡）。 */
(function () {
  'use strict';
  const PAGE = 'introspect';

  let _loaded = false;    // 是否已成功取到报告（失败保持 false，下次进入/刷新重试）
  let _inFlight = false;  // 防并发拉取

  /* ---------------- 工具 ---------------- */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /** 复用 110_polish 的统一 origin 解析：优先 window.MorayBackend.origin */
  function backendOrigin() {
    try { if (window.MorayBackend && window.MorayBackend.origin) return window.MorayBackend.origin; } catch (e) { /* 忽略 */ }
    try { if (typeof window.resolveBackendOrigin === 'function') return window.resolveBackendOrigin(); } catch (e) { /* 忽略 */ }
    return 'http://127.0.0.1:8000';
  }

  /** ISO8601 → 「刚刚 / X 分钟前 / X 小时前 / X 天前」；解析失败返回 '' */
  function agoLabel(iso) {
    try {
      const t = Date.parse(iso);
      if (isNaN(t)) return '';
      const d = Date.now() - t;
      if (d < 60000) return '刚刚';
      if (d < 3600000) return Math.floor(d / 60000) + ' 分钟前';
      if (d < 86400000) return Math.floor(d / 3600000) + ' 小时前';
      return Math.floor(d / 86400000) + ' 天前';
    } catch (e) { return ''; }
  }

  /** monotonic 读数按原样展示（保留 2 位小数），非时间戳、不伪算秒差 */
  function fmtMonotonic(v) {
    const n = Number(v);
    return isFinite(n) ? n.toFixed(2) : '—';
  }

  /* ---------------- 注入：导航按钮（系统组·「设置」之前）+ 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    const group = document.querySelector('.nav-group[data-nav-group="system"]');
    const settingsBtn = document.querySelector('.nav-icon-btn[data-page="settings"]');
    const nav = group || (settingsBtn ? settingsBtn.parentElement : null);
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', '自省（系统自检报告）');
    btn.setAttribute('title', '自省（系统自检报告）');
    btn.innerHTML = '<i data-lucide="activity" class="w-4 h-4"></i><span class="nav-icon-label">自省</span>';
    if (settingsBtn && settingsBtn.parentElement === nav) nav.insertBefore(btn, settingsBtn);
    else nav.appendChild(btn);
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

  /* ---------------- 页面 DOM（静态壳；报告数据由 renderReport 注入） ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page || page.querySelector('#introspectBody')) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="brain" class="w-4 h-4 text-brand-cobalt flex-shrink-0"></i>
          <span class="text-sm font-medium text-text-primary">自省</span>
          <span class="text-[11px] text-text-tertiary truncate" id="introspectHeadStatus">进入面板后自动拉取…</span>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <button id="introspectRefreshBtn" class="text-[11px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="立即重新拉取自检报告">
            <i data-lucide="refresh-cw" class="w-3 h-3"></i>刷新
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-3xl mx-auto" id="introspectBody">
          <div class="text-[11px] text-text-tertiary">等待拉取——点右上「刷新」或切换页面后进入。</div>
        </div>
      </div>`;
    const btn = page.querySelector('#introspectRefreshBtn');
    if (btn) btn.addEventListener('click', refresh);
    const bd = document.getElementById('introspectBody');
    if (bd) bd.addEventListener('click', onGoalClick);
  }

  /* ---------------- 渲染 ---------------- */

  function setHead(t) {
    const el = document.getElementById('introspectHeadStatus');
    if (el) el.textContent = t;
  }

  function card(titleHtml, bodyHtml) {
    return '<div class="rounded-lg border border-line-ghost/60 bg-surface-panel/40 px-4 py-3 mb-3">' +
      '<div class="text-xs font-medium text-text-primary mb-2">' + titleHtml + '</div>' +
      bodyHtml + '</div>';
  }

  function renderOffline(note) {
    const body = document.getElementById('introspectBody');
    if (!body) return;
    body.innerHTML = card('状态',
      '<div class="text-[11px] text-text-tertiary leading-relaxed">' + esc(note) + '</div>');
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  function renderReport(d, mem, fail, reject, nr, gs) {
    const body = document.getElementById('introspectBody');
    if (!body) return;
    const parts = [];

    // ── 状态 + 最近自检 ──
    const lvlHtml = d.level === 'info'
      ? '<span class="text-success">● 健康</span><span class="text-[11px] text-text-tertiary">（level: info）</span>'
      : d.level === 'warn'
        ? '<span class="text-warning">⚠ 注意</span><span class="text-[11px] text-text-tertiary">（level: warn）</span>'
        : '<span class="text-text-tertiary">○ 未知（level: ' + esc(d.level) + '）</span>';
    const ago = agoLabel(d.ts);
    parts.push(card('状态',
      '<div class="text-xs flex items-center gap-2">' + lvlHtml + '</div>' +
      '<div class="text-[11px] text-text-tertiary mt-1">最近自检：' + esc(ago || String(d.ts)) +
      (ago ? '（' + esc(d.ts) + '）' : '') + '</div>'));

    // ── 端口 ──
    const ports = d.ports || {};
    const keys = Object.keys(ports).sort(function (a, b) { return Number(a) - Number(b); });
    const chips = keys.map(function (k) {
      const v = ports[k] || {};
      return '<span class="inline-flex items-center gap-1.5 mr-4 mb-1 text-[11px]">' +
        '<span class="text-text-secondary">' + esc(k) + '</span>' +
        (v.up ? '<span class="text-success">●</span>' : '<span class="text-error">✗</span>') +
        '</span>';
    }).join('');
    parts.push(card('📡 端口（' + keys.length + ' 个）',
      '<div class="flex flex-wrap items-center">' + (chips || '<span class="text-[11px] text-text-tertiary">无数据</span>') + '</div>'));

    // ── 算力闸（预算闸 budget_gate + 活动链闸 gate，两路如实分列） ──
    const bg = d.budget_gate || {};
    let bgHtml;
    if (bg.status === 'ok') {
      bgHtml = '<div class="text-xs text-text-primary">日计数 ' + esc(bg.daily_used) + '/' + esc(bg.daily_limit) +
        ' · 归属日期 ' + esc(bg.daily_date) + ' · per-trace 冷却表 ' + esc(bg.per_trace_count) + ' 条</div>' +
        '<div class="text-[11px] text-text-tertiary mt-1 leading-relaxed">全局冷却最近放行：' + fmtMonotonic(bg.global_cooldown_last) +
        '（' + esc(bg.global_cooldown_last_clock || 'time.monotonic') + ' 读数——非时间戳，进程重启即清零，故不换算秒差）</div>';
    } else {
      bgHtml = '<div class="text-[11px] text-text-tertiary">预算闸：no_data' + (bg.reason ? '：' + esc(bg.reason) : '') + '</div>';
    }
    const g = d.gate || {};
    const gHtml = g.status === 'ok'
      ? '<div class="text-[11px] text-text-tertiary mt-1">活动链闸（' + esc(g.source) + '）：活跃链 ' + esc(g.traces) + ' 条' +
        (g.limits ? ' · 上限 depth=' + esc(g.limits.max_depth) + ' / ttl=' + esc(g.limits.event_ttl_s) + 's / turns=' + esc(g.limits.max_turns) : '') + '</div>'
      : '<div class="text-[11px] text-text-tertiary mt-1">活动链闸：no_data' + (g.reason ? '：' + esc(g.reason) : '') + '</div>';
    parts.push(card('⚡ 算力闸', bgHtml + gHtml));

    // ── 事件积压 ──
    const bl = d.backlog || {};
    parts.push(card('📊 事件积压', bl.status === 'ok'
      ? '<div class="text-xs text-text-primary">未闭合链：' + esc(bl.open_chains) + (bl.truncated ? '（链列表已截断，最多随报 20 条）' : '') + '</div>'
      : '<div class="text-[11px] text-text-tertiary">no_data' + (bl.reason ? '：' + esc(bl.reason) : '') + '</div>'));

    // ── ERROR（30 分钟窗口） ──
    const er = d.errors || {};
    const n = Number(er.recent_30min);
    let errHtml = '<div class="text-xs text-text-primary">' +
      (isFinite(n) && n > 0 ? '<span class="text-warning">⚠ ' + esc(n) + ' 条</span>' : esc(isFinite(n) ? n : '?') + ' 条') + '</div>';
    const items = Array.isArray(er.recent_items) ? er.recent_items : [];
    if (items.length) {
      errHtml += '<details class="mt-2"><summary class="text-[11px] text-text-tertiary cursor-pointer">最近 ' +
        items.length + ' 条（时间倒序）</summary><ul class="mt-1 space-y-1">' +
        items.map(function (it) {
          return '<li class="text-[11px] text-text-secondary leading-relaxed">' +
            '<span class="text-text-tertiary">' + esc(it.ts) + '</span> · ' + esc(it.msg) + '</li>';
        }).join('') + '</ul></details>';
    }
    parts.push(card('⚠️ ERROR（30 分钟）', errHtml));

    // ── 记忆（v1.1：独立数据源 /api/memory/stats，随报刷新） ──
    parts.push(memoryCardHtml(mem));

    // ── 失败档案（v1：独立数据源 /api/memory/failures，随报刷新；只读标记层，零提炼） ──
    parts.push(failuresCardHtml(fail));

    // ── 写入拒绝（v1.2：独立数据源 /api/memory/rejections，随报刷新；只读回读，零写库） ──
    parts.push(rejectionsCardHtml(reject));

    // ── 自我叙事（v1.3：独立数据源 /api/self_narrative/latest，随报刷新） ──
    parts.push(narrativeCardHtml(nr));

    // ── 自驱目标（v1.4：独立数据源 /api/self_driven/queue，随报刷新） ──
    parts.push(goalsCardHtml(gs));

    body.innerHTML = parts.join('');
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 记忆卡（v1.1：GET /api/memory/stats，独立失败不拖五卡） ---------------- */

  const MEM_KIND_ORDER = [
    ['self', '身份记忆（桥三）'],
    ['self_narrative', '自我叙事（桥三）'],
    ['self_observation', '过程观察（桥二）'],
    ['insight', '反思产出'],
    ['task_result', '任务结果'],
    ['preference', '你的偏好'],
    ['fact', '预留']
  ];

  async function fetchStats() {
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/memory/stats',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) return data;
      return { ok: false, note: '记忆统计返回 HTTP ' + r.status };
    } catch (e) {
      return { ok: false, note: '后端不可达' };
    }
  }

  async function fetchFailures() {
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/memory/failures',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) return data;
      return { ok: false, note: '失败档案返回 HTTP ' + r.status };
    } catch (e) {
      return { ok: false, note: '后端不可达' };
    }
  }

  async function fetchRejections() {
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/memory/rejections?limit=50',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) return data;
      return { ok: false, note: '写入拒绝返回 HTTP ' + r.status };
    } catch (e) {
      return { ok: false, note: '后端不可达' };
    }
  }

  async function fetchNarrative() {
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/self_narrative/latest',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) return data;
      return { ok: false, note: '自我叙事返回 HTTP ' + r.status };
    } catch (e) {
      return { ok: false, note: '后端不可达' };
    }
  }

  async function fetchGoals() {
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/self_driven/queue',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) return data;
      return { ok: false, note: '自驱队列返回 HTTP ' + r.status };
    } catch (e) {
      return { ok: false, note: '后端不可达' };
    }
  }

  function memoryCardHtml(s) {
    if (!s || s.ok !== true) {
      return card('🧠 记忆',
        '<div class="text-[11px] text-text-tertiary">' + esc((s && s.note) || '后端不可达') + '</div>');
    }
    const counts = s.counts || {};
    if (Object.keys(counts).length === 0) {
      return card('🧠 记忆', '<div class="text-[11px] text-text-tertiary">暂无记忆</div>');
    }
    const rows = MEM_KIND_ORDER.map(function (k) {
      const c = Number(counts[k[0]]) || 0;
      if (k[0] === 'fact' && c === 0) return '';   // 预留：>0 才显示
      return '<div class="text-xs flex items-center justify-between py-0.5">' +
        '<span class="text-text-secondary">' + esc(k[1]) + '</span>' +
        '<span class="text-text-primary">' + esc(c) + ' 条</span></div>';
    }).join('');
    const total = Number(s.total);
    return card('🧠 记忆', rows +
      '<div class="text-[11px] text-text-tertiary mt-1">合计 ' + (isFinite(total) ? esc(total) : '?') + ' 条（全部 kind）</div>');
  }

  function failuresCardHtml(f) {
    if (!f || f.ok !== true) {
      return card('🗄️ 失败档案',
        '<div class="text-[11px] text-text-tertiary">' + esc((f && f.note) || '后端不可达') + '</div>');
    }
    const items = Array.isArray(f.items) ? f.items : [];
    if (items.length === 0) {
      return card('🗄️ 失败档案', '<div class="text-[11px] text-text-tertiary">暂无失败记录</div>');
    }
    const rows = items.map(function (it) {
      const kw = Array.isArray(it.matched) ? it.matched.join(',') : '';
      return '<li class="text-[11px] leading-relaxed">' +
        '<div class="flex items-center gap-2 flex-wrap">' +
        '<span class="text-text-tertiary">' + esc(agoLabel(it.created_at) || it.created_at) + '</span>' +
        '<span class="text-text-secondary">' + esc(it.source || '—') + '</span>' +
        (kw ? '<span class="text-warning">命中: ' + esc(kw) + '</span>' : '') +
        '</div>' +
        '<div class="text-text-secondary mt-0.5">' + esc((it.summary || '').slice(0, 60)) + '</div>' +
        (it.verdict_excerpt ? '<div class="text-text-tertiary mt-0.5">' + esc(it.verdict_excerpt) + '</div>' : '') +
        '</li>';
    }).join('');
    return card('🗄️ 失败档案', '<ul class="space-y-1">' + rows + '</ul>' +
      '<div class="text-[11px] text-text-tertiary mt-1">共 ' + esc(f.total) + ' 条候选（关键词命中=标记，不判真伪）</div>');
  }

  /* ---------------- 写入拒绝卡（v1.2：GET /api/memory/rejections，只读回读 errors.log） ---------------- */

  function rejectionsCardHtml(rj) {
    if (!rj || rj.ok !== true) {
      return card('🚫 写入拒绝',
        '<div class="text-[11px] text-text-tertiary">' + esc((rj && rj.note) || '后端不可达') + '</div>');
    }
    const items = Array.isArray(rj.items) ? rj.items : [];
    if (items.length === 0) {
      return card('🚫 写入拒绝', '<div class="text-[11px] text-text-tertiary">暂无写入拒绝</div>');
    }
    const rows = items.map(function (it) {
      return '<li class="text-[11px] leading-relaxed">' +
        '<div class="flex items-center gap-2 flex-wrap">' +
        '<span class="text-text-tertiary">' + esc(agoLabel(it.ts) || it.ts || '') + '</span>' +
        '<span class="text-text-secondary">' + esc(it.source || '—') + '</span>' +
        '<span class="text-text-secondary">' + esc(it.kind || '—') + '</span>' +
        '</div>' +
        '<div class="text-text-secondary mt-0.5">' + esc(String(it.content || '').slice(0, 60)) + '</div>' +
        '</li>';
    }).join('');
    return card('🚫 写入拒绝', '<ul class="space-y-1">' + rows + '</ul>' +
      '<div class="text-[11px] text-text-tertiary mt-1">共 ' + esc(rj.total) + ' 条（errors.log 尾部' +
      (rj.truncated ? '，已按上限截取' : '，全量扫描') + '；留痕=拒绝事实，不代表代码有问题）</div>');
  }

  /* ---------------- 自我叙事卡（v1.3：GET /api/self_narrative/latest，桥三 stage2 产物） ---------------- */

  function narrativeCardHtml(nr) {
    if (!nr || nr.ok !== true) {
      return card('📜 自我叙事',
        '<div class="text-[11px] text-text-tertiary">' + esc((nr && nr.note) || '后端不可达') + '</div>');
    }
    if (!nr.found) {
      return card('📜 自我叙事',
        '<div class="text-[11px] text-text-tertiary">尚未生成——重启后端后由自省循环自动产出（素材够 10 条且过 7 天冷却），或 POST /api/self_narrative/generate 手动触发。</div>');
    }
    const raw = String(nr.content || '');
    /* [第九卡 JSON 2026-10-22] 落库 content=self_narrative 产物的 JSON 串
     * {"narrative","mental_state"}：解析成功只渲染 narrative 正文（mental_state
     * 出小徽章 focus_shift）；解析失败（旧纯文本产物/异常串）整段当纯文本——
     * 与后端 fail-open 同口径，新旧两种落库形态都可展示。 */
    let text = raw;
    let focus = '';
    try {
      const obj = JSON.parse(raw);
      if (obj && typeof obj === 'object' && typeof obj.narrative === 'string') {
        text = obj.narrative;
        const ms = obj.mental_state;
        if (ms && typeof ms === 'object' && typeof ms.focus_shift === 'string') {
          focus = ms.focus_shift.slice(0, 60);
        }
      }
    } catch (e) { /* 旧纯文本产物：整段当正文渲染 */ }
    const chars = text.length;
    const meta = '<div class="text-[11px] text-text-tertiary">生成于 ' +
      esc(agoLabel(nr.created_at) || nr.created_at || '—') + ' · ' + esc(chars) + ' 字' +
      (focus ? '　<span class="text-warning">[当前关注：' + esc(focus) + ']</span>' : '') +
      '</div>';
    const body = '<div class="text-[11px] leading-relaxed whitespace-pre-wrap text-text-secondary mt-1">' + esc(text) + '</div>';
    return card('📜 自我叙事', meta +
      (text.length > 500
        ? '<details class="mt-1"><summary class="text-[11px] text-text-tertiary cursor-pointer">展开全文（' + esc(text.length) + ' 字）</summary>' + body + '</details>'
        : body));
  }

  /* ---------------- 自驱目标卡（v1.4：GET /api/self_driven/queue，独立失败不拖他卡） ---------------- */

  /* ---- v2 引擎/状态小件（引擎名进 URL 段前必须过白名单：localStorage/后端字段
          都可能被塞 ../ 或 :// 注入串进请求路径，30_chat ENGINE_NAME_RE 同款纪律） ---- */

  var GOAL_ENGINE_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;
  var GOAL_ENGINE_DISPLAY = { zcode: 'ZCode', laya: 'Laya', local: 'Local' };
  var GOAL_STATUS_META = {
    pending_approval: { label: '待审批', cls: 'text-text-tertiary' },
    dispatched: { label: '执行中…', cls: 'text-brand-cyan' },
    done: { label: '已完成', cls: 'text-success' },
    failed: { label: '失败', cls: 'text-error' }
  };
  var GOAL_BTN_DISABLED_CLS = 'text-[11px] px-2 py-1 rounded-md border border-line-ghost text-text-tertiary opacity-50 cursor-default';

  function goalEngineSafe(n) {
    var k = String(n || '').trim();
    return GOAL_ENGINE_RE.test(k) ? k : 'zcode';
  }
  function goalEngineLabel(n) {
    var k = String(n || '');
    return GOAL_ENGINE_DISPLAY[k] || (k.charAt(0).toUpperCase() + k.slice(1));
  }
  function goalStatusMeta(st) {
    return GOAL_STATUS_META[String(st || '')] || { label: String(st || '未知'), cls: 'text-text-tertiary' };
  }

  function goalRowHtml(it) {
    const gid = String(it.task_id || '');
    const goal = String(it.goal || '');
    const engine = goalEngineSafe(it.target_engine);
    const st = String(it.status || 'pending_approval');
    const sm = goalStatusMeta(st);
    // 终态卡只留徽章（历史只读）；dispatched 按钮置灰不删（第四段契约）；待审批可操作
    const graying = st === 'dispatched';
    const btns = (st === 'done' || st === 'failed') ? '' :
      '<button type="button" data-goal-act="run" data-goal-id="' + esc(gid) + '"' +
      (graying ? ' disabled' : '') +
      ' class="' + (graying ? GOAL_BTN_DISABLED_CLS : 'text-[11px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel') + '">' +
      (graying ? '执行中…' : '执行（送 ' + esc(goalEngineLabel(engine)) + '）') + '</button>' +
      '<button type="button" data-goal-act="ignore" data-goal-id="' + esc(gid) + '"' +
      (graying ? ' disabled' : '') +
      ' class="' + (graying ? GOAL_BTN_DISABLED_CLS : 'text-[11px] px-2 py-1 rounded-md border border-line-ghost text-text-tertiary hover:text-error hover:bg-surface-panel') + '">忽略（X）</button>';
    return '<div class="flex items-start gap-2 py-2 border-b border-line-ghost/40" data-goal-row="1"' +
      ' data-goal-id="' + esc(gid) + '" data-goal-text="' + esc(goal) + '"' +
      ' data-goal-engine="' + esc(engine) + '" data-goal-status="' + esc(st) + '">' +
      '<div class="flex-1 min-w-0">' +
      '<div class="text-[11px] font-bold text-text-primary leading-relaxed">' +
      '<span class="text-[10px] font-bold text-brand-cyan mr-1">[' + esc(goalEngineLabel(engine)) + ']</span>' +
      esc(goal) + '</div>' +
      '<div class="text-[11px] text-text-tertiary mt-0.5">' +
      '<span data-goal-status-badge class="' + esc(sm.cls) + '">' + esc(sm.label) + '</span> · 生成于 ' +
      esc(agoLabel(it.created_at) || it.created_at || '—') + '</div></div>' +
      '<div class="flex items-center gap-1.5 flex-shrink-0">' + btns + '</div></div>';
  }

  function goalsCardHtml(gs) {
    if (!gs || gs.ok !== true) {
      return card('🎯 自驱目标',
        '<div class="text-[11px] text-text-tertiary">' + esc((gs && gs.note) || '后端不可达') + '</div>');
    }
    const items = Array.isArray(gs.items) ? gs.items : [];
    if (!items.length) {
      return card('🎯 自驱目标', '<div class="text-[11px] text-text-tertiary">暂无自驱目标</div>');
    }
    const rows = items.map(goalRowHtml).join('');
    return card('🎯 自驱目标',
      '<div id="selfDrivenGoalRows">' + rows + '</div>' +
      '<div class="text-[11px] text-text-tertiary mt-2">「执行」派发到卡上引擎（进入执行中，完成后自动回写已完成/失败），' +
      '「忽略」移除该目标；每日最多生成 3 条（安全熔断）。</div>');
  }

  /* ---- v1.4 交互：事件委托用 composedPath（125:409 纪律：refreshIcons 把 <i> 换成 <svg> 后
          e.target.closest 会静默失效）。Toast 为本文件私有轻量实现（全库无全局 toast 助手）。 ---- */

  function toast(msg) {
    try {
      const el = document.createElement('div');
      el.setAttribute('role', 'status');
      el.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:9999;' +
        'padding:8px 12px;border-radius:8px;font-size:11px;max-width:320px;' +
        'background:rgba(20,20,22,0.92);color:#f2f2f2;border:1px solid rgba(255,255,255,0.14);' +
        'box-shadow:0 6px 24px rgba(0,0,0,0.35);opacity:0;transition:opacity .18s';
      el.textContent = String(msg || '');
      document.body.appendChild(el);
      requestAnimationFrame(function () { el.style.opacity = '1'; });
      setTimeout(function () {
        el.style.opacity = '0';
        setTimeout(function () { try { el.remove(); } catch (e) { /* 忽略 */ } }, 220);
      }, 2400);
    } catch (e) { /* Toast 失败绝不影响主流程 */ }
  }

  async function postJson(path, body) {
    const r = await fetch(backendOrigin() + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    return { status: r.status, data: data };
  }

  function removeGoalRow(row) {
    try {
      row.remove();
      const box = document.getElementById('selfDrivenGoalRows');
      if (box && !box.querySelector('[data-goal-row]')) {
        box.innerHTML = '<div class="text-[11px] text-text-tertiary">暂无自驱目标</div>';
      }
    } catch (e) { /* 忽略 */ }
  }

  /* [v2] 就地流转 dispatched：状态徽章换「执行中…」、按钮置灰（不删除，第四段契约）。
     行内 DOM 变更而非整行重渲染——created_at 不在行属性上，重渲染会丢显示。 */
  function markGoalRowDispatched(row) {
    try {
      row.setAttribute('data-goal-status', 'dispatched');
      const badge = row.querySelector('[data-goal-status-badge]');
      if (badge) {
        badge.textContent = '执行中…';
        badge.className = 'text-brand-cyan';
      }
      const btns = row.querySelectorAll('button[data-goal-act]');
      for (let i = 0; i < btns.length; i++) {
        const b = btns[i];
        b.disabled = true;
        b.className = GOAL_BTN_DISABLED_CLS;
        if (b.getAttribute('data-goal-act') === 'run') b.textContent = '执行中…';
      }
    } catch (e) { /* 置灰失败不影响主流程（卡已登记 dispatched，刷新自愈） */ }
  }

  async function onGoalAction(act, gid, goalText, row) {
    if (!gid || !row || row.getAttribute('data-busy') === '1') return;
    row.setAttribute('data-busy', '1');
    try {
      if (act === 'ignore') {
        const res = await postJson('/api/self_driven/reject', { task_id: gid });
        const d = res.data || {};
        if (res.status === 200 && d.ok === true) {
          removeGoalRow(row);
          toast('已忽略该目标');
          return;
        }
        toast('忽略失败：' + (d.reason || ('HTTP ' + res.status)));
      } else if (act === 'run') {
        // [v2] 按卡上 target_engine 拼通用路由（引擎名过白名单后才进 URL 段）
        const engine = goalEngineSafe(row.getAttribute('data-goal-engine'));
        const res = await postJson('/api/engines/' + encodeURIComponent(engine) + '/tasks',
          { prompt: String(goalText || '') });
        const d = res.data || {};
        const engineTaskId = d.task_id || (d.data && d.data.task_id) || '';
        if (res.status >= 200 && res.status < 300 && engineTaskId) {
          // approve 登记引擎 task_id（闭环匹配键；目标卡 task_id 与它是两套 id）——
          // 卡不删、就地流转「执行中…」，终态由后端引擎收尾事件经 synapse 闭环回写
          const ap = await postJson('/api/self_driven/approve',
            { task_id: gid, engine_task_id: engineTaskId });
          if (ap.status === 200 && (ap.data || {}).ok === true) {
            markGoalRowDispatched(row);
            toast('已派发给 ' + goalEngineLabel(engine) + ' 引擎（执行中）');
            return;
          }
          toast('已派发给 ' + goalEngineLabel(engine) + ' 引擎（状态登记失败，请刷新）');
        } else {
          toast('派发失败：' + (d.message || d.msg || d.error || ('HTTP ' + res.status)));
        }
      }
    } catch (e) {
      toast('操作失败：后端不可达');
    }
    row.removeAttribute('data-busy');
  }

  function onGoalClick(e) {
    const path = (typeof e.composedPath === 'function') ? e.composedPath() : [];
    let btn = null;
    let row = null;
    for (let i = 0; i < path.length; i++) {
      const n = path[i];
      if (!n || typeof n.getAttribute !== 'function') continue;
      if (!row && n.getAttribute('data-goal-row')) row = n;
      if (n.getAttribute('data-goal-act')) { btn = n; break; }
    }
    if (!btn || !row) return;
    onGoalAction(btn.getAttribute('data-goal-act'),
      row.getAttribute('data-goal-id') || btn.getAttribute('data-goal-id') || '',
      row.getAttribute('data-goal-text') || '', row);
  }

  /* ---------------- 拉数 ---------------- */

  async function loadReport() {
    if (_inFlight) return;
    _inFlight = true;
    setHead('拉取中…');
    const statsPromise = fetchStats();   // 与 report 并行拉取，成功分支里 await
    const failuresPromise = fetchFailures();   // 失败档案 v1：同款并行拉取，成功分支里 await
    const rejectionsPromise = fetchRejections();   // 写入拒绝 v1.2：同款并行拉取，成功分支里 await
    const narrativePromise = fetchNarrative();   // 自我叙事 v1.3：同款并行拉取，成功分支里 await
    const goalsPromise = fetchGoals();   // 自驱目标 v1.4：同款并行拉取，成功分支里 await
    let note = '';
    try {
      const ctrl = ('AbortController' in window) ? new AbortController() : null;
      const timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
      const r = await fetch(backendOrigin() + '/api/self_check/report',
        { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined });
      if (timer) clearTimeout(timer);
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      if (r.status === 200 && data && data.ok === true) {
        renderReport(data, await statsPromise, await failuresPromise, await rejectionsPromise, await narrativePromise, await goalsPromise);
        _loaded = true;
        setHead('最近报告 ' + (agoLabel(data.ts) || data.ts));
        _inFlight = false;
        return;
      }
      if (r.status === 503 || (data && data.code === 'not_ready')) {
        note = '首轮自检尚未就绪：' + ((data && data.message) || '通常 <2s，稍后点刷新');
      } else {
        note = '后端返回 HTTP ' + r.status + (data && data.message ? '：' + data.message : '');
      }
    } catch (e) {
      note = '后端不可达（' + backendOrigin() + '）：确认本地后端已启动，或稍后点刷新';
    }
    _loaded = false;
    renderOffline(note);
    setHead('未取到报告');
    _inFlight = false;
  }

  function ensureLoaded() {
    if (_loaded || _inFlight) return;
    loadReport();
  }

  function refresh() {
    _loaded = false;
    loadReport();
  }

  /* ---------------- 页面切换（140_synapse / memory_browser 同款实现） ---------------- */

  function switchTo(page) {
    document.querySelectorAll('.nav-icon-btn[data-page]').forEach(function (b) {
      b.classList.toggle('active', b.dataset.page === page);
    });
    document.querySelectorAll('.page-panel').forEach(function (p) {
      p.classList.add('hidden');
      p.classList.remove('flex');
    });
    const target = document.getElementById('page-' + page);
    if (target) {
      target.classList.remove('hidden');
      target.classList.add('flex');
      void target.offsetWidth;
    }
    document.querySelectorAll('.sidebar-panel').forEach(function (p) { p.classList.add('hidden'); });
    const sb = document.getElementById('sidebar-' + page);
    if (sb) { sb.classList.remove('hidden'); sb.classList.add('flex'); }
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 挂载（DOMContentLoaded 自挂载；幂等；失败只 warn 不中断启动） ---------------- */

  function mount() {
    ensureNavButton();
    ensurePage();
    buildPage();
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  window.MorayIntrospect = { mount: mount, switchTo: switchTo, refresh: refresh, PAGE: PAGE };

  function boot() {
    try { mount(); } catch (e) { console.warn('[MoRay][146] 自省面板挂载失败：', e); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
