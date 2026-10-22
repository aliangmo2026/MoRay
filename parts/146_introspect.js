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
   独立失败（只标本卡）；MEM_KIND_ORDER 补 self_narrative 单列（桥三 stage2 产物）。 */
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

  function renderReport(d, mem, fail, reject, nr) {
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
    const text = String(nr.content || '');
    const chars = Number(nr.chars) || text.length;
    const meta = '<div class="text-[11px] text-text-tertiary">生成于 ' +
      esc(agoLabel(nr.created_at) || nr.created_at || '—') + ' · ' + esc(chars) + ' 字</div>';
    const body = '<div class="text-[11px] leading-relaxed whitespace-pre-wrap text-text-secondary mt-1">' + esc(text) + '</div>';
    return card('📜 自我叙事', meta +
      (text.length > 500
        ? '<details class="mt-1"><summary class="text-[11px] text-text-tertiary cursor-pointer">展开全文（' + esc(text.length) + ' 字）</summary>' + body + '</details>'
        : body));
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
        renderReport(data, await statsPromise, await failuresPromise, await rejectionsPromise, await narrativePromise);
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
