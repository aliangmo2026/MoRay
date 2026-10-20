/* ============================================================
   [Day23] 执行引擎面板 —— 设置页「Agent 与工具」组 · 纯只读
   - 数据源：GET /api/engines/list（后端只读路由 local_adapter.py:166，本文件零写操作）
   - 落位：紧随「本机 Agent」卡之后；无该卡时落到兜底锚 #settingsTailAnchor 之前
   - 边界：不改后端 / 不改骨架 / 不碰其它分片逻辑；只读展示 + 手动刷新
   说明：本分片刻意排在 125（appendAgentSettings 定义处）之前，故只做函数声明，
   挂载由 70 的启动步与 build() 两处调用触发，不在本文件顶层执行 DOM 操作。
   ============================================================ */

/** 状态点修饰类（对应 135 §28 的 .engine-dot.is-ok / .is-off / .is-busy） */
const ENGINES_DOT = { ok: 'is-ok', off: 'is-off', busy: 'is-busy' };

/** [Day23.6] 后端 offline_reason → 中文（未登记的原因原样显示，不臆造） */
const ENGINES_REASON = {
  cli_not_found: '未找到 CLI',
  cli_cmd_unset: '未配置 CLI',
  probe_failed: '探针异常'
};

/** 引擎 id → 展示名。API 的 name 是小写 id（local / zcode，见 local_adapter.py:169-173）；
    未登记的 id 原样显示，不臆造名字。 */
const ENGINES_LABEL = { local: 'LocalAdapter', zcode: 'ZCodeAdapter' };

/** 能力位 → 中文标签（顺序即渲染顺序）。字段名取自 ExecutionEngine.capabilities 契约
    （local_adapter.py:55-63 / zcode_adapter.py）；只渲染值为 true 的位。 */
const ENGINES_CAP_LABELS = [
  ['can_read', '只读'], ['can_write', '读写'], ['is_isolated', '隔离'],
  ['can_network', '联网'], ['can_exec', '执行']
];

/** 列表缓存（10s TTL，避免每次设置页重建都打一次后端；「刷新」按钮强制穿透） */
let __enginesState = { engines: null, online: false, at: 0 };

/** [Day23c] 展开态记录（键=引擎原始 id）。刷新重绘只换 innerHTML，展开态靠这里恢复，
    不因重绘而弹回收起。 */
const __enginesOpen = new Set();

/** 后端 origin：沿用 125 agentBackendBase 的三级解析链（MorayBackend → resolveBackendOrigin → 默认端口）。
    file:// 打开或静态托管时后端不存在，fetch 失败并落到「引擎信息不可用」态。
    @returns {string} 后端 origin（无尾斜杠） */
function enginesBackendBase() {
  const trim = (s) => String(s).replace(/\/+$/, '');
  try {
    const mb = window.MorayBackend;
    if (mb && mb.origin) return trim(mb.origin);
  } catch (e) { /* 探测结构缺失：走下一级 */ }
  if (typeof window.resolveBackendOrigin === 'function') {
    try { return trim(window.resolveBackendOrigin()); } catch (e) { /* 忽略 */ }
  }
  if (typeof agentBackendBase === 'function') {
    try { return trim(agentBackendBase()); } catch (e) { /* 忽略 */ }
  }
  return 'http://127.0.0.1:8000';
}

/** 拉取引擎列表。3s 超时；任何异常 / 非 ok / 结构不符都收敛为 null，绝不抛给调用方
    @returns {Promise<Array|null>} 引擎数组；null = 后端不可用 */
async function fetchEnginesList() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(enginesBackendBase() + '/api/engines/list', { signal: ctrl.signal, cache: 'no-store' });
    if (!res.ok) return null;
    const j = await res.json();
    if (!j || j.ok !== true || !Array.isArray(j.engines)) return null;
    return j.engines;
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** 载入并缓存引擎列表
    @param {boolean} [force] - 忽略 TTL 强制重取（「刷新」按钮用）
    @returns {Promise<Object>} 模块内状态 {engines, online, at} */
async function loadEnginesState(force) {
  const now = Date.now();
  if (!force && __enginesState.at && now - __enginesState.at < 10000) return __enginesState;
  const engines = await fetchEnginesList();
  __enginesState = { engines: engines, online: Array.isArray(engines), at: now };
  return __enginesState;
}

/** 单个引擎的状态点判定。
    后端返回的 status 已由后端自身诚实派生（Day23.5 起），本函数只做值到视觉的映射：
    busy → 橙点；offline / unavailable / off / available===false → 灰点；其余（idle）→ 绿点。
    [Day23.6] 'offline' 是后端新增的静态不可用态（探针结果），此前不在判定表里 → 会掉进
    兜底的绿点，正是"假绿"的前端一环，故本次补上。 */
function engineDotOf(e) {
  if (e && (e.status === 'busy' || e.busy === true)) return ENGINES_DOT.busy;
  if (e && (e.status === 'offline' || e.status === 'unavailable' || e.status === 'off'
            || e.available === false)) return ENGINES_DOT.off;
  return ENGINES_DOT.ok;
}

/** 当前任务数：后端暂无该字段，只在真的给出数字时渲染（不把缺失臆造成 0）。
    @returns {number|null} */
function engineTaskCount(e) {
  if (!e) return null;
  const vals = [e.task_count, e.tasks, e.running];
  for (let i = 0; i < vals.length; i++) {
    if (typeof vals[i] === 'number' && isFinite(vals[i])) return vals[i];
  }
  return null;
}

/** [Day23c] 最近任务时间。后端契约（Day23.5）只有 current_task（进行中任务的
    started_at/created_at，任务结束即清空），没有「最近一次任务」字段——故只在真的给出
    时间时渲染，缺数据不臆造（与 task_count 同一诚实口径）；后端将来补字段即自动生效。
    @returns {string|null} HH:MM */
function engineLastTaskText(e) {
  if (!e) return null;
  const t = (e.current_task && (e.current_task.started_at || e.current_task.created_at))
    || e.last_task_at || e.last_run_at;
  if (!t) return null;
  const d = new Date(t);
  if (isNaN(d.getTime())) return null;
  const pad = (x) => (x < 10 ? '0' + x : '' + x);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

/** 渲染单个引擎行。[Day23c] 收起/展开重构：头部只留 状态点+引擎名+箭头（任务书指定
    「默认收起，只显示引擎名+状态点」），状态字/原始 id/任务数/最近任务时间/能力标签/描述
    全部收进 .engine-detail，点头部切换；展开态记录在 __enginesOpen（按原始 id），重绘后保持。
    @param {Object} e - {name, description, capabilities, status?, current_task?, task_count?}
    @returns {string} HTML */
function renderEngineRow(e) {
  const raw = String((e && e.name) || '').trim();
  const label = ENGINES_LABEL[raw] || raw || '未命名引擎';
  const caps = (e && e.capabilities) || {};
  const tags = ENGINES_CAP_LABELS
    .filter(pair => caps[pair[0]] === true)
    .map(pair => '<span class="engine-tag">' + pair[1] + '</span>')
    .join('');
  const dot = engineDotOf(e);
  const stateText = dot === ENGINES_DOT.busy ? '忙碌' : (dot === ENGINES_DOT.off ? '不可用' : '可用');
  // [Day23.6] 不可用时把后端给的原因带进 title——可见文案仍严格是「不可用」（不改验收口径），
  // 原因只在悬停时可见。未登记的原因字符串原样显示，不臆造。
  const reason = dot === ENGINES_DOT.off
    ? (ENGINES_REASON[String((e && e.offline_reason) || '')] || String((e && e.offline_reason) || ''))
    : '';
  const title = reason ? stateText + '：' + reason : stateText;
  const n = engineTaskCount(e);
  // [Day23c] 任务数挪进展开区后，0 也如实显示（后端明确给 0 是真数据）；字段缺失仍不渲染。
  //   Day23.6 的「隐去任务 0」针对的是收起头部的一行噪音，展开态是用户主动要看的明细。
  const count = (n === null) ? '' : '<span class="engine-count">任务 ' + n + '</span>';
  const last = engineLastTaskText(e);
  const desc = String((e && e.description) || '').trim();
  const open = __enginesOpen.has(raw) ? ' is-open' : '';
  return '<div class="engine-row' + open + '" data-engine="' + escapeHtml(raw) + '">' +
    '<div class="engine-head" role="button" tabindex="0" aria-expanded="' + (open ? 'true' : 'false') + '" title="点击展开或收起">' +
      '<span class="engine-dot ' + dot + '" title="' + escapeHtml(title) + '"></span>' +
      '<span class="engine-name">' + escapeHtml(label) + '</span>' +
      '<span class="engine-chevron" aria-hidden="true"></span>' +
    '</div>' +
    '<div class="engine-detail">' +
      '<div class="engine-meta">' +
        '<span class="engine-state">' + stateText + '</span>' +
        (raw && raw !== label ? '<span class="engine-id">' + escapeHtml(raw) + '</span>' : '') +
        count +
        (last ? '<span class="engine-last">最近任务 ' + escapeHtml(last) + '</span>' : '') +
      '</div>' +
      '<div class="engine-caps">' + (tags || '<span class="engine-tag engine-tag--none">无能力申报</span>') + '</div>' +
      (desc ? '<div class="engine-desc" title="' + escapeHtml(desc) + '">' + escapeHtml(desc) + '</div>' : '') +
    '</div>' +
  '</div>';
}

/** [Day23] 卡片正文 HTML。后端不可用时给「引擎信息不可用」占位，不空白、不抛错。
    @returns {string} HTML */
function renderEnginesCard() {
  const st = __enginesState;
  if (!st.online) {
    return '<div class="engine-empty"><span class="engine-dot is-off"></span>' +
      '<span>引擎信息不可用（本地后端未连接）</span></div>' +
      '<div class="engine-hint">执行引擎由本地后端提供：双击「启动MoRay.bat」（或运行 server\\ 下 uvicorn）后点「刷新」重试。</div>';
  }
  if (!st.engines.length) {
    return '<div class="engine-empty"><span class="engine-dot is-off"></span>' +
      '<span>暂无引擎</span></div>';
  }
  return st.engines.map(renderEngineRow).join('');
}

/** 卡片骨架（首次挂载建一次；之后只替换 #enginesCardBody，刷新按钮不重复绑定）
    @returns {HTMLElement} */
function buildEnginesCardShell() {
  const card = document.createElement('div');
  card.id = 'enginesSettingsCard';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  card.innerHTML = [
    // 卡内眉标：与「Agent 与工具」组其它运行时追加卡（125/68/69/100）同构
    '<div class="settings-group-tag" style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
      '<span style="width:3px;height:10px;border-radius:2px;background:var(--brand,#E5734A);flex-shrink:0"></span>' +
      '<span style="font-size:10px;font-weight:500;color:var(--color-text-secondary,#a3a3a3);letter-spacing:0.05em">Agent 与工具</span></div>',
    // h3 首个文本节点 =「执行引擎」：左导航按此取卡名（70 buildSideNav 的 labelOf）
    '<h3 class="text-sm font-semibold text-text-primary mb-1 flex items-center gap-2"><i data-lucide="box" class="w-4 h-4 text-brand-violet"></i>执行引擎</h3>',
    '<p class="text-[10px] text-text-tertiary mb-3">本地后端注册的执行引擎与能力申报（只读）。「隔离」= 在 Docker 沙箱内执行；本机目录写入引擎无隔离。</p>',
    '<div id="enginesCardBody" class="engine-list"></div>',
    // 底部操作行（任务书指定「底部：一个刷新按钮」）；右对齐沿用本页动作按钮的既有排布
    '<div class="engine-foot">' +
      '<button id="enginesRefreshBtn" class="btn-ghost px-3 py-2 rounded-lg text-[10px] border border-line-ghost flex items-center gap-2" title="重新拉取 GET /api/engines/list">' +
        '<i data-lucide="refresh-cw" class="w-3 h-3"></i>刷新</button>' +
    '</div>'
  ].join('');
  return card;
}

/** 刷新按钮绑定（卡片实例级，只绑一次）+ [Day23c] 展开/收起委托。
    刷新：点击即进加载态（按钮 is-loading 图标自旋 + 正文 loading 行），失败在正文尾部
    追加红色 error 行（仅刷新动作触发；首载失败仍是既有灰点离线态）。
    展开/收起：委托在 #enginesCardBody 上——该节点只换 innerHTML 不换节点，绑一次即可；
    查找目标一律走 composedPath()，因为 refreshIcons 会把被点的 <i> 换成游离 <svg>，
    e.target.closest() 在冒泡阶段拿空（既有陷阱）。
    @param {HTMLElement} card
    @returns {void} */
function bindEnginesCardEvents(card) {
  const btn = card.querySelector('#enginesRefreshBtn');
  const body = card.querySelector('#enginesCardBody');
  if (!btn) return;
  on(btn, 'click', async () => {
    btn.disabled = true;
    btn.classList.add('is-loading');
    if (body) body.innerHTML = '<div class="engine-loading">正在读取引擎列表…</div>';
    let online = false;
    try {
      await loadEnginesState(true);
      online = __enginesState.online === true;
    } finally {
      renderEnginesBody(card);
      btn.classList.remove('is-loading');
      btn.disabled = false;
    }
    if (!online && body) {
      body.insertAdjacentHTML('beforeend',
        '<div class="engine-error">刷新失败：本地后端未连接（启动后端后点「刷新」重试）</div>');
    }
    if (typeof showNotification === 'function') {
      showNotification(
        online ? '引擎列表已刷新' : '引擎信息不可用',
        online ? '共 ' + __enginesState.engines.length + ' 个执行引擎' : '本地后端未连接',
        online ? 'success' : 'warning', 1800);
    }
  });
  if (!body || body.__enginesToggleBound) return;
  body.__enginesToggleBound = true;
  const rowFromEvent = (ev) => {
    const path = ev.composedPath ? ev.composedPath() : [];
    for (let i = 0; i < path.length; i++) {
      const n = path[i];
      if (n && n.classList && n.classList.contains('engine-head')) return n.closest('.engine-row');
    }
    return null;
  };
  const toggleRow = (row) => {
    if (!row) return;
    const id = row.getAttribute('data-engine') || '';
    const open = !__enginesOpen.has(id);
    if (open) __enginesOpen.add(id); else __enginesOpen.delete(id);
    row.classList.toggle('is-open', open);
    const head = row.querySelector('.engine-head');
    if (head) head.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  body.addEventListener('click', (ev) => { toggleRow(rowFromEvent(ev)); });
  body.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const row = rowFromEvent(ev);
    if (!row) return;
    ev.preventDefault();
    toggleRow(row);
  });
}

/** 只重绘卡片正文（不动卡片骨架，避免刷新按钮失焦）
    @param {HTMLElement} cardEl
    @returns {void} */
function renderEnginesBody(cardEl) {
  const card = cardEl || document.getElementById('enginesSettingsCard');
  if (!card) return;
  const body = card.querySelector('#enginesCardBody');
  if (!body) return;
  body.innerHTML = renderEnginesCard();
  if (typeof refreshIcons === 'function') refreshIcons();
}

/** 把卡片摆到目标位置：① 「本机 Agent」卡之后 ② 兜底锚 #settingsTailAnchor（= 容器最后一个元素子级）之前。
    只在位置不对时移动，避免无谓 DOM 抖动（移动会改变 70 侧导航的卡序签名，从而触发一次导航重建）。
    @param {HTMLElement} container @param {HTMLElement} card
    @returns {void} */
function placeEnginesCard(container, card) {
  const agentCard = container.querySelector('#agentSettingsCard');
  const after = (agentCard && agentCard.parentElement === container) ? agentCard : null;
  const want = after ? after.nextElementSibling : container.lastElementChild;
  // want === card：卡已在「本机 Agent」之后；card.nextElementSibling === want：已在目标位之前 → 都不动
  const placed = card.parentElement === container && (want === card || card.nextElementSibling === want);
  if (!placed) container.insertBefore(card, want);
}

/** 「本机 Agent」卡由 125 异步挂载（appendAgentSettings 先 await 后端配置），可能晚于本卡落地，
    那样本卡就会被它插到前面。故在其出现前挂一个短时观察（最多 3s），出现即复位位置后自清理。
    @param {HTMLElement} container @param {HTMLElement} card
    @returns {void} */
function watchForAgentCard(container, card) {
  if (typeof MutationObserver === 'undefined' || card.__engineAgentWatch) return;
  let mo = null;
  let timer = null;
  const stop = () => {
    if (mo) { mo.disconnect(); mo = null; }
    if (timer) { clearTimeout(timer); timer = null; }
    card.__engineAgentWatch = null;
  };
  mo = new MutationObserver(() => {
    if (!container.querySelector('#agentSettingsCard')) return;
    placeEnginesCard(container, card);
    stop();
  });
  mo.observe(container, { childList: true });
  card.__engineAgentWatch = mo;
  timer = setTimeout(stop, 3000);
}

/** [Day23] 挂载 / 复位引擎卡到设置页「Agent 与工具」组末尾。
    幂等：已存在则复用（含事件绑定），仅在位置不对时挪动。
    落位优先级：① 「本机 Agent」卡之后 ② 兜底锚 #settingsTailAnchor 之前。
    @returns {Promise<void>} */
async function appendEnginesSettings() {
  const container = document.querySelector('#settingsBody, #page-settings .max-w-2xl');
  if (!container) return;
  let card = document.getElementById('enginesSettingsCard');
  if (!card) {
    card = buildEnginesCardShell();
    bindEnginesCardEvents(card);
  }
  placeEnginesCard(container, card);
  if (!container.querySelector('#agentSettingsCard')) watchForAgentCard(container, card);
  await loadEnginesState(false);
  renderEnginesBody(card);
}
