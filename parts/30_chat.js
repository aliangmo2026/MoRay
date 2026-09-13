/* ============================================================
   模块：任务三 对话系统（会话CRUD / 消息渲染 / 流式发送 / 导出）
   ============================================================ */

/** [回复简洁化] 内置默认系统提示词（默认值；用户可在 设置→对话 中编辑或关闭）
 * 仅当会话级与全局级用户自定义系统提示词都为空时使用；用户一旦自定义即完全采用用户的，绝不叠加默认。 */
const DEFAULT_SYSTEM_PROMPT = [
  '你是 MoRay 内置 AI 编程助手，用与用户相同的语言、默认中文回答。',
  '回答要简洁、直接、就事论事：问什么答什么；寒暄、问候、感谢只用一两句话友好回应，不展开、不主动贴代码。',
  '严格围绕用户最新一条消息作答；除非用户明确说"继续/接着上面"，否则不主动续写更早的未完成话题或代码。',
  '需要代码时给出精炼、可直接运行的最小片段并配一两句说明，不堆砌无关实现，不做无谓的长篇分点。',
  '不确定时先简短澄清，不臆测。',
  '不输出你自己的推理过程。'
].join('\n');

/** [体验优化] 组装默认系统提示词（集中入口：普通对话与对比页统一走它）。
 * 开关 defaultSysPromptEnabled 关闭时返回空串；文本可在 设置→对话 中编辑（defaultSysPrompt 设置项）。 */
function buildSystemPrompt() {
  if (MoraySettings.get('defaultSysPromptEnabled') === false) return '';
  const custom = (MoraySettings.get('defaultSysPrompt') || '').trim();
  return custom || DEFAULT_SYSTEM_PROMPT;
}

/** 全局应用状态 */
const AppState = {
  /** 会话列表 @type {Array} */
  conversations: [],
  /** 当前激活会话 @type {Object|null} */
  activeConv: null,
  /** 当前会话消息 @type {Array} */
  messages: [],
  /** 是否正在生成 @type {boolean} */
  generating: false,
  /** 当前生成控制器 @type {AbortController|null} */
  genController: null,
  /** 输入历史 @type {string[]} */
  inputHistory: [],
  /** 附件（图片等，dataURL） @type {Array<{name:string,dataUrl:string}>} */
  attachments: []
};

/** 会话搜索词 @type {string} */
let sessionSearchQuery = '';

/* ---------- 会话列表渲染 ---------- */

/** 渲染会话列表（置顶优先 -> 按更新时间倒序 -> 时间分组）
 * @returns {void} */
function renderSessionList() {
  const list = document.getElementById('sessionList') || document.querySelector('#sidebar-chat .flex-1.overflow-y-auto');
  if (!list) return;
  if (list.id !== 'sessionList') list.id = 'sessionList';
  const q = sessionSearchQuery.toLowerCase().trim();

  const convs = AppState.conversations
    .filter(c => !c.archived)
    // [V2 功能8] 置顶优先；order 字段支持手动拖拽排序（新会话 order=创建时间，天然时间序）
    .sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));

  // 搜索：标题 + 消息内容全文（消息内容搜索用缓存索引）
  let filtered = convs;
  // [V3.2 审计补全] 分组筛选
  if (typeof morayCurrentGroup !== 'undefined' && morayCurrentGroup !== 'all') {
    filtered = filtered.filter(c => (c.group || '') === morayCurrentGroup);
  }
  if (q) {
    filtered = convs.filter(c => {
      if ((c.title || '').toLowerCase().includes(q)) return true;
      const msgs = AppState._convMsgIndex && AppState._convMsgIndex[c.id];
      return msgs && msgs.some(m => (m.content || '').toLowerCase().includes(q));
    });
  }

  if (!filtered.length) {
    // [V3 P2.5] 统一空状态：搜索无结果 or 全新空列表
    if (q) {
      list.innerHTML = `<div class="empty-art"><div class="empty-icon"><i data-lucide="search-x" class="w-5 h-5 text-text-tertiary"></i></div>
        <div class="empty-title">没有匹配「${escapeHtml(q)}」的会话</div><div class="empty-hint">试试其他关键词，或新建一个对话</div></div>`;
    } else {
      list.innerHTML = renderEmptyState({ icon: 'message-square-plus', title: '还没有对话', desc: '开始你的第一次 AI 对话', actionText: '新建第一个对话' });
      bindEmptyAction(list, () => createNewConversation());
    }
    refreshIcons();
    return;
  }
  // [夜间优化1.2] 超过50条分流到固定行高虚拟滚动
  if (typeof renderSessionListVirtual === 'function' && renderSessionListVirtual(filtered)) return;

  // [V2 功能7] 双模式渲染：手动拖拽过 -> 平铺（置顶仍在最前）；未拖拽 -> 时间分组
  const showModelTag = MoraySettings.get('showSessionModel') === true; // [Trae 化] 默认不显示模型药丸
  const itemHtml = (c) => {
    const msgs = (AppState._convMsgIndex && AppState._convMsgIndex[c.id]) || [];
    const preview = (msgs.length ? msgs[msgs.length - 1].content : (c.preview || '空会话')).slice(0, 40);
    return `
      <div class="session-item ${AppState.activeConv && AppState.activeConv.id === c.id ? 'active' : ''} ${c.pinned ? 'pinned' : ''}"
           data-conv-id="${c.id}" data-title="${escapeHtml(c.title || '')}" draggable="true"
           title="模型：${escapeHtml(shortModelName(c.model))}">
        <div class="flex items-center justify-between">
          <div class="session-title flex items-center gap-1.5 truncate">${escapeHtml(c.title || '新对话')}${c.unread ? '<span class="unread-dot"></span>' : ''}</div>
          <div class="session-item-actions">
            <button class="session-item-action" data-op="move" title="移动到项目" onclick="showMoveToGroupMenu(event, this)"><i data-lucide="folder-input" class="w-3 h-3"></i></button>
            <button class="session-item-action ${c.pinned ? 'active-pin' : ''}" data-op="pin" title="${c.pinned ? '取消置顶' : '置顶'}"><i data-lucide="pin" class="w-3 h-3"></i></button>
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
  };
  const manualMode = filtered.some(c => c.orderManual);
  let html = '';
  if (manualMode) {
    const pinned = filtered.filter(c => c.pinned);
    const rest = filtered.filter(c => !c.pinned);
    if (pinned.length) {
      html += `<div class="text-[10px] text-text-tertiary px-2 py-1.5 font-semibold uppercase tracking-wider">置顶</div>`;
      pinned.forEach(c => { html += itemHtml(c); });
    }
    rest.forEach(c => { html += itemHtml(c); });
  } else {
    const groups = {};
    filtered.forEach(c => {
      const g = c.pinned ? '置顶' : timeGroup(c.updatedAt);
      (groups[g] = groups[g] || []).push(c);
    });
    const order = ['置顶', '今天', '昨天', '7天内', '更早'];
    order.forEach(g => {
      if (!groups[g] || !groups[g].length) return;
      html += `<div class="text-[10px] text-text-tertiary px-2 py-1.5 ${g !== '置顶' ? 'mt-2' : ''} font-semibold uppercase tracking-wider">${g}</div>`;
      groups[g].forEach(c => { html += itemHtml(c); });
    });
  }
  list.innerHTML = html;
  refreshIcons();
}

/** 模型名缩短显示（去 tag 与版本噪声）
 * @param {string} model - 完整模型名
 * @returns {string} 短名（始终为字符串） */
function shortModelName(model) {
  if (!model) return '默认';
  const p = model.split(':');
  return (typeof p[0] === 'string' && p[0]) ? p[0] : '默认';
}

/** [路由修复] 精确模型名（保留 :4b/:9b 参数量后缀，气泡头部展示实际命中模型用）
 * @param {string} model - 模型名
 * @returns {string} */
function preciseModelName(model) {
  return model || '默认';
}

/** [显示去重] 唯一可区分的模型显示名：
 * 当前模型列表中“冒号前家族名”唯一时显示家族名（如 qwen3.5）；
 * 同家族存在多个模型时保留完整名（如 qwen2.5:1.5b / qwen2.5:7b），
 * 用于对比勾选区/栏标题/模型下拉等需要“同屏不重名”的场景。
 * @param {string} model - 完整模型名
 * @returns {string} */
function uniqueModelName(model) {
  const full = String(model || '');
  if (!full) return '默认';
  let names = [];
  try { names = (AI && Array.isArray(AI.models) ? AI.models : []).map(m => m && m.name).filter(Boolean); } catch (e) { names = []; }
  if (!names.length) return shortModelName(full);
  const family = (n) => { const s = String(n).split(':'); return (typeof s[0] === 'string' && s[0]) ? s[0] : n; };
  const base = family(full);
  const sameFamCount = names.reduce((c, n) => c + (family(n) === base ? 1 : 0), 0);
  return sameFamCount > 1 ? full : base;
}

/* ---------- [显示统一] 实际生效模型单一状态（气泡 / 输入台 / 状态栏共用） ---------- */

/** 最后一次成功回复/手选后“真正生效”的模型。
 * 云端失败回退本地、或走备用链后，只有这里记录的名字才是权威；
 * 输入台标签、状态栏模型名都不再各自读“默认模型”导致三处不一致。 */
let __activeModelState = null;

/** 当前会话内应展示的实际模型名（跨会话不串：仅当记录属于当前会话时生效）
 * @returns {string} */
function activeModelName() {
  const s = __activeModelState;
  if (!s || !s.name) return '';
  if (!AppState || !AppState.activeConv || !s.convId) return '';
  return s.convId === AppState.activeConv.id ? s.name : '';
}

/** [显示统一] 统一入口：把“真正生效模型”同步到输入台标签与状态栏
 * @param {string} name - 实际模型名
 * @param {string} [source] - route | fallback | user | default
 * @param {string} [reason] - 路由/回退说明（展示用） */
function setActiveModel(name, source, reason) {
  name = String(name || '').trim();
  __activeModelState = name ? {
    name, source: source || 'route', reason: reason || '',
    convId: (AppState && AppState.activeConv) ? AppState.activeConv.id : null,
    ts: Date.now()
  } : null;
  if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
  if (typeof updateStatusBar === 'function') updateStatusBar();
}

/** 清除“实际生效模型”覆盖（默认模型变更/清除手选后调用），恢复按 conv/默认值显示 */
function resetActiveModel() {
  __activeModelState = null;
  if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
  if (typeof updateStatusBar === 'function') updateStatusBar();
}

/** 重建消息内容索引（供会话全文搜索）
 * @returns {Promise<void>} */
async function rebuildMsgIndex() {
  const index = {};
  for (const c of AppState.conversations) {
    try { index[c.id] = await DB.listMessagesByConversation(c.id); } catch (e) { index[c.id] = []; }
  }
  AppState._convMsgIndex = index;
}

/* ---------- 会话操作 ---------- */

/** 创建新会话并激活
 * @returns {Promise<void>} */
async function createNewConversation() {
  const conv = await DB.createConversation({
    title: '新对话',
    model: MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '',
    // [路由修复] 显式锁定标记：写入默认模型≠用户手选，userPickedModel 才代表手选
    userPickedModel: false
  });
  AppState.conversations.unshift(conv);
  AppState._convMsgIndex[conv.id] = [];
  await openConversation(conv.id);
  // [模型同步] 新建会话后刷新输入台模型名
  syncInputModelLabel();
  showNotification('新会话', '已创建空白对话', 'info', 1200);
}

/** [模型同步] 当前会话实际将使用的模型（与 generateAssistantReply 的解析完全一致：
 * conv.model（用户锁定）> 全局默认模型 > 首个可用模型；无模型返回空串） */
function currentConvModel(conv) {
  const c = conv || AppState.activeConv;
  return (c && c.model) || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
}

/** [模型同步] 输入台模型标签 = 弹层勾选 = 实际发送（同一数据源 currentConvModel）；
 * 在启动/切换会话/新建删除/手选模型/默认模型修改/后端重检后调用 */
function syncInputModelLabel() {
  const wrap = document.querySelector('#coreInputContainer .input-toolbar-model');
  // [选择器修复] 精确选取 .input-model-name（beacon-dot 是第一个 span，绝不写文字）
  const label = wrap ? wrap.querySelector('.input-model-name') : null;
  if (!label) return;
  // [显示统一] 有“实际生效模型”（本次回复/回退后的权威）优先展示它；否则回退会话选择/默认模型
  const m = activeModelName() || currentConvModel();
  // [v3.15.13] 统一展示完整模型名（本地含 :tag），不再丢 tag
  label.textContent = m ? preciseModelName(m) : '选择模型';
  if (wrap) wrap.title = m ? m : '未检测到模型，请先在设置中配置后端';
  // [深度思考] 与模型标签同类时机刷新三态按钮
  if (typeof refreshThinkModeBtn === 'function') refreshThinkModeBtn();
}

/* ---------- [深度思考] 三态开关（auto / on / off） ---------- */

const THINK_MODES = [
  { key: 'auto', tag: 'AUTO', title: '深度思考：自动（复杂任务自动开启思考）', cls: '' },
  { key: 'on',   tag: 'ON',   title: '深度思考：开启（所有请求强制思考）',       cls: 'think-on' },
  { key: 'off',  tag: 'OFF',  title: '深度思考：关闭（所有请求不思考）',         cls: 'think-off' }
];

/** [深度思考] 与普通对话完全一致的 think 决策（对比页/工作流复用，不另写一套）：
 * on→true；off→false；auto 档对 qwen3.5 系列默认 think=false（复杂任务也不自动开），
 * 其余思考型模型仅复杂任务才 true；非思考模型由 20_ai 的 isThinkingModel 过滤不下发。
 * @param {string} model - 完整模型名
 * @param {Array} [messages] - 已组装的请求消息（auto 档分类用）
 * @returns {boolean} */
function decideThinkFor(model, messages) {
  const mode = MoraySettings.get('thinkMode') || 'auto';
  if (mode === 'on') return true;
  if (mode === 'off') return false;
  // [速度/思考优化] qwen3.5 系列默认 think=false：仅用户把深度思考切到 ON 才下发 think=true
  if (/^qwen3\.5/i.test(String(model || ''))) return false;
  return !!(typeof TaskRouter !== 'undefined' && TaskRouter.classify(messages || []) === 'complex');
}

/** [深度思考] 按钮 UI 同步：按当前 thinkMode 设置 图标高亮/小标/禁用态
 * @returns {void} */
function refreshThinkModeBtn() {
  const btn = document.getElementById('thinkModeBtn');
  if (!btn) return;
  const mode = MoraySettings.get('thinkMode') || 'auto';
  const conf = THINK_MODES.find(t => t.key === mode) || THINK_MODES[0];
  btn.className = 'input-toolbar-btn think-mode-btn' + (conf.cls ? ' ' + conf.cls : '');
  btn.title = conf.title + (AppState.generating ? '（生成中已禁用）' : '');
  const tag = btn.querySelector('.think-mode-tag');
  if (tag) tag.textContent = conf.tag;
}

/** [深度思考] 点击三态循环：auto → on → off → auto；生成中禁用；持久化到 MoraySettings
 * @returns {void} */
function cycleThinkMode() {
  if (AppState.generating) {
    showNotification('深度思考', 'AI 正在回复中，请在生成结束后切换', 'warning', 1600);
    return;
  }
  const cur = MoraySettings.get('thinkMode') || 'auto';
  const idx = THINK_MODES.findIndex(t => t.key === cur);
  const next = THINK_MODES[(idx + 1) % THINK_MODES.length];
  MoraySettings.set('thinkMode', next.key);
  refreshThinkModeBtn();
  const hint = { auto: '已切换为自动：复杂任务自动开启深度思考', on: '已开启深度思考：所有请求强制思考', off: '已关闭深度思考：所有请求不思考' }[next.key];
  showNotification('深度思考', hint, 'info', 1800);
}

/** [深度思考] 输入工具栏动态插入三态按钮（静态骨架无此按钮，纯 JS 增量，不改 HTML）+
 * 运行时注入按钮专属样式
 * @returns {void} */
function installThinkModeBtn() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || document.getElementById('thinkModeBtn')) return;
  const btn = document.createElement('button');
  btn.id = 'thinkModeBtn';
  btn.className = 'input-toolbar-btn think-mode-btn';
  btn.setAttribute('aria-label', '深度思考');
  btn.innerHTML = '<i data-lucide="brain" class="w-4 h-4"></i><span class="think-mode-tag">AUTO</span>';
  on(btn, 'click', cycleThinkMode);
  const divider = toolbar.querySelector('.input-toolbar-divider');
  if (divider) toolbar.insertBefore(btn, divider);
  else toolbar.appendChild(btn);
  // 样式注入（不改静态骨架 CSS，纯运行时）
  if (!document.getElementById('thinkModeBtnStyle')) {
    const st = document.createElement('style');
    st.id = 'thinkModeBtnStyle';
    st.textContent = '.think-mode-btn{position:relative}.think-mode-btn .think-mode-tag{font-size:10px;font-weight:800;line-height:1;position:absolute;bottom:1px;right:2px;letter-spacing:.5px}.think-mode-btn.think-on{background:rgba(91,140,255,.15);color:#5B8CFF;box-shadow:inset 0 0 0 1px rgba(91,140,255,.3)}.think-mode-btn.think-off{opacity:.4}';
    document.head.appendChild(st);
  }
  refreshIcons();
  refreshThinkModeBtn();
}

/** [阶段0 本机 Agent] 输入工具栏"本机工具/Agent"开关按钮（对齐深度思考按钮模式）：
 * 控制 nativeToolsEnabled（native 工具进入 listForRequest 的总门）。
 * 点击异步探测本地后端：在线 → 成功开启并提示工作区；离线 → 仍可开启（工具执行时给出
 * 明确降级错误，不卡死），banner 展示离线态。 */
function installNativeAgentToggle() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || document.getElementById('nativeAgentBtn')) return;
  const btn = document.createElement('button');
  btn.id = 'nativeAgentBtn';
  btn.className = 'input-toolbar-btn native-agent-btn';
  btn.setAttribute('aria-label', '本机工具');
  btn.innerHTML = '<i data-lucide="bot" class="w-4 h-4"></i><span class="native-agent-tag">ON</span>';
  on(btn, 'click', toggleNativeAgent);
  const divider = toolbar.querySelector('.input-toolbar-divider');
  if (divider) toolbar.insertBefore(btn, divider);
  else toolbar.appendChild(btn);
  if (!document.getElementById('nativeAgentBtnStyle')) {
    const st = document.createElement('style');
    st.id = 'nativeAgentBtnStyle';
    st.textContent = '.native-agent-btn{position:relative}.native-agent-btn .native-agent-tag{font-size:10px;font-weight:800;line-height:1;position:absolute;bottom:1px;right:2px;letter-spacing:.5px;display:none}.native-agent-btn.native-on{background:rgba(157,123,255,.15);color:#9D7BFF;box-shadow:inset 0 0 0 1px rgba(157,123,255,.35)}.native-agent-btn.native-on .native-agent-tag{display:inline}';
    document.head.appendChild(st);
  }
  syncNativeAgentBtn();
  refreshIcons();
}

/** [批次修复 #1] 本机 Agent 离线原因与后端可用性判断
 * @returns {string} 离线原因（在线返回空串） */
function agentOfflineReason() {
  const mb = window.MorayBackend;
  if (mb && mb.connected === true) return '';
  return '本机 Agent 需要本地后端，请双击「启动MoRay.bat」（或运行 server\\ 下 uvicorn）后刷新';
}

/** 同步按钮态（开启高亮 + ON 标 + 提示文案；[批次修复 #1] 后端离线时置灰并给出明确原因）
 * @returns {void} */
function syncNativeAgentBtn() {
  const btn = document.getElementById('nativeAgentBtn');
  if (!btn) return;
  const on = MoraySettings.get('nativeToolsEnabled') === true;
  const offline = agentOfflineReason();
  btn.classList.toggle('native-on', on);
  btn.classList.toggle('native-offline', !!offline);
  btn.title = offline
    ? '本机工具不可用 · ' + offline
    : on
      ? '本机工具已开启：模型可列目录/读文件/写文件/执行只读命令（写与命令需人工审批；点击关闭）'
      : '本机工具/Agent（默认关）：开启后模型可操作工作区文件与只读命令（需本地后端，写/命令会先征求你同意）';
}

/** 点击切换本机工具开关（[批次修复 #1] 后端离线时拒绝开启并提示原因）
 * @returns {Promise<void>} */
async function toggleNativeAgent() {
  const next = !(MoraySettings.get('nativeToolsEnabled') === true);
  // [批次修复 #1] 仅“开启”方向受后端可用性约束（离线时仍可关闭已开启的开关）
  if (next && agentOfflineReason()) {
    showNotification('本机 Agent 暂不可用', agentOfflineReason(), 'warning', 5200);
    return;
  }
  await MoraySettings.set('nativeToolsEnabled', next);
  // [通宵回归 3.2 修复] 开启本机 Agent 时联动总开关：toolsEnabled 默认关会让请求体无 tools 而静默失效（开 native 的意图即是用工具）
  if (next && MoraySettings.get('toolsEnabled') !== true) await MoraySettings.set('toolsEnabled', true);
  syncNativeAgentBtn();
  refreshNativeAgentBanner();
  if (next) {
    const cfg = await agentBackendConfig(true);
    if (cfg) showNotification('本机工具已开启', '工作区：' + cfg.workspace, 'success', 2600);
    else showNotification('本机工具已开启，但本地后端离线', '本机动作需启动后端（默认 http://127.0.0.1:8000）后可用', 'warning', 4200);
    // [批次修复 #2] Agent 模式默认模型：自检未通过/未自检时切换到工具调用最稳模型
    if (typeof agentApplyRecommendedModel === 'function') agentApplyRecommendedModel();
    // [批次修复 #17] 首次开启说明（仅一次、仅说明，不改变默认关闭策略）
    try {
      if (!localStorage.getItem('moray_native_first_note')) {
        localStorage.setItem('moray_native_first_note', '1');
        showModal(
          '<div class="space-y-2 text-xs text-text-secondary leading-relaxed">' +
          '<div class="flex items-center gap-1.5 text-text-primary font-medium"><i data-lucide="shield-check" class="w-4 h-4 text-brand-violet"></i>本机 Agent 默认关闭是为了安全</div>' +
          '<p>开启后，模型可在受控工作区内调用工具；每次工具执行受以下保护：</p>' +
          '<ul class="list-disc pl-4 space-y-1">' +
          '<li>工作区隔离与路径越界防护（只许相对路径，符号链接/危险后缀/非白名单命令均被拒绝）</li>' +
          '<li>写文件 / 编辑 / 只读命令执行前的人工审批（含 unified diff 预览）</li>' +
          '<li>全部工具调用写入审计日志（可在设置→本机 Agent 查看）</li>' +
          '</ul>' +
          '<p class="text-[10px] text-text-tertiary">你可随时关闭本机工具回到纯聊天模式。</p></div>',
          { title: '本机 Agent 安全说明', icon: 'shield-alert', footer: false }
        );
      }
    } catch (e) { /* 忽略 */ }
  } else {
    showNotification('本机工具已关闭', '已回到纯聊天模式（请求不再携带本机工具）', 'info', 1800);
  }
}

/** 会话区顶部轻提示条："本机工具已开启 · 工作区 X"（含自检/文件树入口，常驻显示）
 * @returns {Promise<void>} */
async function refreshNativeAgentBanner() {
  const content = document.getElementById('chatNormalContent');
  if (!content) return;
  let banner = document.getElementById('nativeAgentBanner');
  const on = MoraySettings.get('nativeToolsEnabled') === true;
  if (!on) {
    if (banner) banner.remove();
    return;
  }
  const cfg = await agentBackendConfig();
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'nativeAgentBanner';
    banner.className = 'native-agent-banner text-[10px] text-text-tertiary flex items-center gap-2 px-3 py-1.5 mx-3 mt-2 rounded-lg border border-line-ghost/50 bg-surface-panel/50';
    const anchor = content.querySelector('#chatMessagesNormal');
    content.insertBefore(banner, anchor || content.firstChild);
  }
  const ok = !!cfg;
  banner.innerHTML = ok
    ? '<span class="beacon-dot" style="width:6px;height:6px;background:var(--color-success)"></span>本机工具已开启 · 工作区 <span class="font-mono text-text-secondary">' + escapeHtml(cfg.workspace || '') + '</span><span class="text-text-tertiary">（写文件与只读命令需你授权）</span>' +
      '<span class="ml-auto flex items-center gap-1.5 shrink-0">' +
      '<button data-agent-selfcheck-btn class="text-[10px] px-2 py-0.5 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="检测当前模型能否触发工具调用"><i data-lucide="shield-check" class="w-3 h-3"></i>自检</button>' +
      '<button data-ws-sample-btn class="text-[10px] px-2 py-0.5 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="载入示例工作区（notes/a.txt、notes/b.txt、todo.md，不覆盖已有文件）"><i data-lucide="sparkles" class="w-3 h-3"></i>示例</button>' +
      '<button data-ws-tree-btn class="text-[10px] px-2 py-0.5 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="查看工作区文件树（只读）"><i data-lucide="folder-tree" class="w-3 h-3"></i>文件树</button>' +
      '</span>'
    : '<span class="beacon-dot" style="width:6px;height:6px;background:var(--color-warning)"></span>本机工具已开启，但本地后端离线 —— ' + escapeHtml(agentOfflineReason()) +
      '<span class="ml-auto flex items-center gap-1.5 shrink-0">' +
      '<button data-agent-selfcheck-btn class="agent-offline text-[10px] px-2 py-0.5 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="shield-check" class="w-3 h-3"></i>自检</button>' +
      '<button data-ws-sample-btn class="agent-offline text-[10px] px-2 py-0.5 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="sparkles" class="w-3 h-3"></i>示例</button>' +
      '<button data-ws-tree-btn class="agent-offline text-[10px] px-2 py-0.5 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="folder-tree" class="w-3 h-3"></i>文件树</button>' +
      '</span>';
  // [阶段1.6 M3] 当前模型自检未通过 → 输入台上方一行温和提示（不打断输入）
  const selfCheck = MoraySettings.get('agentSelfCheck');
  const curModel = (typeof activeModelName === 'function') ? activeModelName() : '';
  if (ok && selfCheck && selfCheck.ok === false && curModel && selfCheck.model === curModel) {
    banner.innerHTML += '<div class="w-full text-[10px] text-warning mt-1 flex items-center gap-1.5">' +
      '<i data-lucide="alert-triangle" class="w-3 h-3"></i>该模型（' + escapeHtml(curModel) + '）工具调用偶发失败（自检未通过），Agent 任务建议 qwen2.5:7b，或点「自检」复测</div>';
  }
  refreshIcons();
}

/** 打开会话：加载消息并渲染
 * @param {string} convId - 会话ID
 * @returns {Promise<void>} */
async function openConversation(convId) {
  const conv = AppState.conversations.find(c => c.id === convId);
  if (!conv) return;
  // [P0 治本] 先同步清空并指向新会话，再异步加载回填——切换那一刻起全局数组绝不可能是上一个会话内容
  AppState.activeConv = conv;
  AppState.messages = [];
  conv.unread = false;
  document.getElementById('chatTitle').textContent = conv.title || '新对话';
  renderMessages();
  updateTokenMeter();
  syncInputModelLabel();
  try { AppState.messages = await DB.listMessagesByConversation(convId); }
  catch (e) { console.error(e); AppState.messages = []; showNotification('加载失败', '消息读取出错', 'error'); }
  renderMessages();
  renderSessionList();
  updateTokenMeter();
  // [夜间优化2.2] 超大对话提示归档/清理
  if (AppState.messages.length > 500 && !conv.__archivePrompted) {
    conv.__archivePrompted = true;
    showConfirm('会话消息过多', `当前会话已有 ${AppState.messages.length} 条消息，可能影响渲染性能。是否清理较早消息（保留最近 100 条）？`, async () => {
      await trimOldMessages(100);
    }, { okText: '清理' });
  }
  // 保存激活会话ID，便于下次启动恢复
  MoraySettings.set('activeConversationId', convId);
}

/** [P0 根治] 从 _convMsgIndex（当前会话索引）中移除消息，与 AppState.messages 删除操作保持双写一致
 * @param {string} msgId - 消息ID */
function removeMsgFromIndex(msgId) {
  const cid = AppState.activeConv ? AppState.activeConv.id : null;
  if (!cid) return;
  AppState._convMsgIndex[cid] = (AppState._convMsgIndex[cid] || []).filter(m => m.id !== msgId);
}

/** [P0 治本] 数据健康检查：查找孤儿消息（conversationId 不属于任何会话 / 无 id）
 * @returns {Promise<{orphans: Array, total: number}>} */
async function findOrphanMessages() {
  const convs = await DB.listConversations();
  const ids = new Set(convs.map(c => c.id));
  const all = await DB._tx('messages', 'readonly', tx => tx.objectStore('messages').getAll());
  const orphans = all.filter(m => !m.conversationId || !ids.has(m.conversationId));
  return { orphans, total: all.length };
}

/** [P0 治本] 清理孤儿消息（IndexedDB + 内存双存储同步清，只删无主数据）
 * @returns {Promise<number>} 清理条数 */
async function cleanOrphanMessages() {
  const { orphans } = await findOrphanMessages();
  for (const m of orphans) await DB.deleteMessage(m.id).catch(() => {});
  const orphanIds = new Set(orphans.map(o => o.id));
  AppState.messages = AppState.messages.filter(m => !orphanIds.has(m.id));
  const cid = AppState.activeConv ? AppState.activeConv.id : null;
  if (cid) AppState._convMsgIndex[cid] = (AppState._convMsgIndex[cid] || []).filter(m => !orphanIds.has(m.id));
  return orphans.length;
}

/** 删除会话（带确认）
 * @param {string} convId - 会话ID
 * @returns {Promise<void>} */
async function deleteConversationById(convId) {
  const conv = AppState.conversations.find(c => c.id === convId);
  showConfirm('删除会话', `确定删除会话「${conv ? conv.title : ''}」？该会话的全部消息将一并删除。`, async () => {
    await DB.deleteConversation(convId);
    AppState.conversations = AppState.conversations.filter(c => c.id !== convId);
    delete AppState._convMsgIndex[convId];
    if (AppState.activeConv && AppState.activeConv.id === convId) {
      AppState.activeConv = null;
      AppState.messages = [];
      renderMessages();
      if (AppState.conversations.length) await openConversation(AppState.conversations[0].id);
      else showWelcomeView();
    }
    renderSessionList();
    // [模型同步] 删除会话后刷新输入台模型名
    syncInputModelLabel();
    showNotification('已删除', '会话已删除', 'success', 1500);
  }, { danger: true, okText: '删除' });
}

/** 重命名会话（内联输入框）
 * @param {string} convId - 会话ID
 * @returns {void} */
function renameConversation(convId) {
  const conv = AppState.conversations.find(c => c.id === convId);
  if (!conv) return;
  const item = document.querySelector(`.session-item[data-conv-id="${convId}"] .session-title`);
  if (!item) return;
  const old = conv.title || '';
  item.innerHTML = `<input type="text" value="${escapeHtml(old)}" style="width:100%;background:var(--color-surface-card);border:1px solid rgba(91,140,255,0.5);border-radius:6px;padding:2px 6px;font-size:12px;color:var(--color-text-primary);outline:none">`;
  const input = item.querySelector('input');
  input.focus(); input.select();
  const commit = async () => {
    const val = input.value.trim() || old;
    conv.title = val;
    await DB.updateConversation(convId, { title: val });
    if (AppState.activeConv && AppState.activeConv.id === convId) document.getElementById('chatTitle').textContent = val;
    renderSessionList();
  };
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); commit(); }
    if (e.key === 'Escape') { e.stopPropagation(); renderSessionList(); }
  });
  input.addEventListener('blur', commit);
}

/** 切换置顶
 * @param {string} convId - 会话ID
 * @returns {Promise<void>} */
async function togglePinConversation(convId) {
  const conv = AppState.conversations.find(c => c.id === convId);
  if (!conv) return;
  conv.pinned = !conv.pinned;
  await DB.updateConversation(convId, { pinned: conv.pinned });
  renderSessionList();
}

/** 会话列表事件委托（选择 / 置顶 / 重命名 / 删除 / 双击重命名）
 * @returns {void} */
function setupSessionListEvents() {
  const list = document.getElementById('sessionList');
  if (!list) return;
  list.addEventListener('click', (e) => {
    const opBtn = e.target.closest('.session-item-action');
    const item = e.target.closest('.session-item');
    if (!item) return;
    const convId = item.dataset.convId;
    if (opBtn) {
      e.stopPropagation();
      const op = opBtn.dataset.op;
      if (op === 'delete') deleteConversationById(convId);
      else if (op === 'pin') togglePinConversation(convId);
      else if (op === 'rename') renameConversation(convId);
      return;
    }
    openConversation(convId);
  });
  list.addEventListener('dblclick', (e) => {
    const item = e.target.closest('.session-item');
    if (item) renameConversation(item.dataset.convId);
  });
  // 会话搜索框
  const search = document.getElementById('sessionSearchInput');
  if (search) {
    search.addEventListener('input', debounce(function () {
      sessionSearchQuery = this.value;
      renderSessionList();
      const clearBtn = document.getElementById('sessionSearchClear');
      if (clearBtn) clearBtn.style.display = this.value ? '' : 'none';
    }, 150));
  }
  const clearBtn = document.getElementById('sessionSearchClear');
  if (clearBtn) clearBtn.addEventListener('click', () => {
    const s = document.getElementById('sessionSearchInput');
    if (s) { s.value = ''; sessionSearchQuery = ''; renderSessionList(); s.focus(); }
  });
  setupSessionDragSort();
}

/** [V2 功能7] 会话拖拽排序（HTML5 DnD；置顶区与普通区各自成序）
 * 拖放后重写可视序列的 order 字段并持久化。
 * @returns {void} */
function setupSessionDragSort() {
  const list = document.getElementById('sessionList');
  if (!list || list.__dragBound) return;
  list.__dragBound = true;
  let dragId = null;

  list.addEventListener('dragstart', (e) => {
    const item = e.target.closest('.session-item');
    if (!item) return;
    dragId = item.dataset.convId;
    item.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', dragId); } catch (err) { /* IE 兼容 */ }
  });

  list.addEventListener('dragend', () => {
    list.querySelectorAll('.session-item').forEach(i => i.classList.remove('dragging', 'drop-above', 'drop-below'));
    dragId = null;
  });

  list.addEventListener('dragover', (e) => {
    if (!dragId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const item = e.target.closest('.session-item');
    list.querySelectorAll('.session-item').forEach(i => i.classList.remove('drop-above', 'drop-below'));
    if (item && item.dataset.convId !== dragId) {
      const rect = item.getBoundingClientRect();
      const above = e.clientY < rect.top + rect.height / 2;
      item.classList.add(above ? 'drop-above' : 'drop-below');
    }
  });

  list.addEventListener('drop', async (e) => {
    if (!dragId) return;
    e.preventDefault();
    const item = e.target.closest('.session-item');
    if (!item || item.dataset.convId === dragId) return;
    const targetId = item.dataset.convId;
    const convs = AppState.conversations;
    const fromIdx = convs.findIndex(c => c.id === dragId);
    const toIdx = convs.findIndex(c => c.id === targetId);
    if (fromIdx < 0 || toIdx < 0) return;
    // 从原位置摘出，插入目标之前/之后
    const [moved] = convs.splice(fromIdx, 1);
    const rect = item.getBoundingClientRect();
    const above = e.clientY < rect.top + rect.height / 2;
    let insertIdx = convs.findIndex(c => c.id === targetId);
    if (!above) insertIdx++;
    convs.splice(insertIdx, 0, moved);
    // 重写可视序列的 order：位置0拿最大值（列表按 order 降序渲染，保证所见即所得）
    // 并标记手动排序模式（渲染层将切换为平铺，避免时间分组打乱手动顺序）
    const now = Date.now();
    for (let i = 0; i < convs.length; i++) {
      convs[i].order = now - i * 1000;
      convs[i].orderManual = true;
      DB.putConversation(convs[i]).catch(() => {});
    }
    renderSessionList();
    showNotification('排序已更新', '会话顺序已保存', 'success', 1200);
  });
}

/* ---------- 消息渲染 ---------- */

/** [体验优化] 思考过程展示策略：'folded' 始终折叠一行（默认）| 'hidden' 完全隐藏 | 'auto' 流式展开完成折叠
 * @returns {string} 当前策略 */
function thinkingDisplayMode() {
  return MoraySettings.get('thinkingDisplay') || 'folded';
}

/** 思考面板 HTML（[体验优化] 流式期间也默认折叠为一行"深度思考中…"，点击展开；完成态保持折叠）
 * @param {string} reasoning - 思考内容
 * @param {boolean} [streaming] - 是否流式中
 * @param {number} [ms] - 思考用时
 * @returns {string} HTML */
function thinkingPanelHtml(reasoning, streaming, ms) {
  const body = streaming
    ? `<div class="thinking-step"><div class="thinking-step-icon"><i data-lucide="loader-2" class="w-2.5 h-2.5 text-brand-cobalt animate-spin"></i></div><div>深度思考中，正在梳理上下文与解题路径...</div></div>`
    : (reasoning || '').split('\n').filter(Boolean).map(line =>
        `<div class="thinking-step"><div class="thinking-step-icon"><i data-lucide="circle-dot" class="w-2.5 h-2.5 text-brand-cobalt"></i></div><div>${escapeHtml(line)}</div></div>`).join('');
  // 'auto'：流式展开、完成折叠；其余策略一律折叠为一行
  const autoExpand = thinkingDisplayMode() === 'auto';
  const collapsed = !(streaming && autoExpand);
  // [修复] collapsed class 同时落在外层与 content（toggle 切 content；CSS 以 .thinking-content.collapsed 控制）
  return `<div class="thinking-panel${collapsed ? ' collapsed' : ''}">
    <div class="thinking-header" data-toggle-thinking>
      <div class="thinking-title">
        <i data-lucide="brain-circuit" class="w-3.5 h-3.5"></i>
        <span>${streaming ? '深度思考中' : '思考过程'}</span>
        ${ms ? `<span class="text-[10px] text-text-tertiary">用时 ${(ms / 1000).toFixed(1)}s</span>` : ''}
      </div>
      <i data-lucide="chevron-down" class="w-3.5 h-3.5 text-text-tertiary thinking-chevron transition-transform" style="transform:${collapsed ? 'rotate(-90deg)' : 'rotate(0)'}"></i>
    </div>
    <div class="thinking-content${collapsed ? ' collapsed' : ''}">${body}</div>
  </div>`;
}

/** 是否渲染思考面板（'hidden' 策略下不渲染，reasoning 仍存库）
 * @returns {boolean} */
function thinkingVisible() {
  return thinkingDisplayMode() !== 'hidden';
}

/** 消息操作条 HTML（[P2] 供 buildMessageEl 与流式完成就地渲染复用）
 * @param {Object} msg - 消息对象
 * @returns {string} HTML */
function msgActionsHtml(msg) {
  return `<div class="msg-actions">
    <button class="msg-action-btn" data-op="copy" title="复制"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="regenerate" title="重新生成"><i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="saveSnippet" title="保存为片段"><i data-lucide="bookmark" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn ${msg.starred ? 'active' : ''}" data-op="star" title="收藏"><i data-lucide="star" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="quote" title="引用回复"><i data-lucide="reply" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="forward" title="转发到新会话"><i data-lucide="forward" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn ${msg.feedback === 1 ? 'active' : ''}" data-op="like" title="点赞"><i data-lucide="thumbs-up" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn ${msg.feedback === -1 ? 'active' : ''}" data-op="dislike" title="点踩"><i data-lucide="thumbs-down" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="branch" title="从此分支"><i data-lucide="git-branch" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="edit" title="编辑重发"><i data-lucide="edit-3" class="w-3.5 h-3.5"></i></button>
    <button class="msg-action-btn" data-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
  </div>`;
}

/** 渲染单条消息 DOM
 * @param {Object} msg - 消息对象
 * @param {boolean} [animate] - 入场动画
 * @returns {HTMLElement} 消息节点 */
function buildMessageEl(msg, animate) {
  const wrap = document.createElement('div');
  wrap.className = (animate ? 'msg-enter ' : '') + 'msg-bubble flex ' + (msg.role === 'user' ? 'justify-end' : 'justify-start');
  wrap.dataset.msgId = msg.id;
  const time = clockTime(msg.createdAt);

  if (msg.role === 'user') {
    wrap.innerHTML = `
      <div class="max-w-[75%]">
        <div class="text-[10px] text-text-tertiary text-right mb-1">你 · ${time}</div>
        <div class="rounded-xl px-4 py-3 bg-brand-cobalt/14 border border-brand-cobalt/20 msg-bubble__user-box">
          <div class="md-body user-msg-content"></div>
          ${(msg.attachments && msg.attachments.length) ? `<div class="flex gap-1.5 flex-wrap mt-2">${msg.attachments.map(a => `<img src="${a.dataUrl}" alt="${escapeHtml(a.name || '图片')}" class="w-20 h-20 object-cover rounded-lg border border-line-ghost">`).join('')}</div>` : ''}
        </div>
        <div class="msg-timestamp text-[10px] text-text-tertiary right-0 mt-1 flex items-center justify-end gap-1">
          ${(msg.attachments && msg.attachments.length) ? '<span class="local-only-badge" title="图片附件仅保存在本机浏览器 IndexedDB，清除浏览器数据或更换设备将丢失">仅存本机</span>' : ''}
          <i data-lucide="clock" class="w-3 h-3"></i><span>${time}</span>
        </div>
      </div>`;
    const content = wrap.querySelector('.user-msg-content');
    content.innerHTML = renderMarkdown(msg.content || '');
    enhanceCodeBlocks(content, false);
  } else {
    wrap.innerHTML = `
      <div class="max-w-[85%] group">
        <div class="flex items-center gap-2 mb-1 flex-wrap">
          <span class="beacon-dot"></span>
          <span class="text-[10px] text-text-tertiary">${escapeHtml(preciseModelName(msg.model))} · ${time}</span>
          ${typeof renderCostPills === 'function' ? renderCostPills(msg) : ''}
        </div>
        ${(typeof renderToolSteps === 'function') ? renderToolSteps(msg.toolCalls) : ''}
        ${(typeof PlanTracker !== 'undefined' && msg.plan) ? PlanTracker.renderStatic(msg.plan) : ''}
        <div class="rounded-xl px-4 py-3 bg-surface-card border border-line-ghost/60 msg-bubble__ai-box">
          <div class="ai-msg-inner"></div>
          ${msgActionsHtml(msg)}
          ${msg.stats ? `<div class="text-[10px] text-text-tertiary mt-1.5 flex items-center gap-2">
            ${msg.stats.tokPerSec ? `<span class="compare-stat"><i data-lucide="zap" class="w-3 h-3 inline"></i> ${msg.stats.tokPerSec} tok/s</span>` : ''}
            ${msg.stats.ms ? `<span class="compare-stat">用时 ${(msg.stats.ms / 1000).toFixed(1)}s</span>` : ''}
            ${msg.stats.tokens ? `<span class="compare-stat">${msg.stats.tokens} tokens</span>` : ''}
          </div>` : ''}
        </div>
      </div>`;
    fillAssistantBody(wrap.querySelector('.ai-msg-inner'), msg, false);
  }
  refreshIcons();
  return wrap;
}

/** 填充 AI 消息主体（思考面板 + 正文 + 引用来源）
 * @param {HTMLElement} el - 容器
 * @param {Object} msg - 消息对象
 * @param {boolean} streaming - 是否流式（追加光标）
 * @returns {void} */
function fillAssistantBody(el, msg, streaming) {
  let html = '';
  // [体验优化] 'hidden' 策略不渲染思考面板（reasoning 仍存库）；流式与完成态均按策略折叠/展开
  // [小补丁] 面板显示收紧：完成态有 reasoning 照常；流式/占位态必须 思考型模型 && think===true 才显示
  //          （非思考模型 qwen2.5 等 / think=false 绝不出现"深度思考中"；think 未定义的既有调用保持原行为）
  let showThinking = false;
  if (msg.reasoning) showThinking = true;
  else if (streaming) {
    if (msg.think === true) showThinking = isThinkingModel(msg.model);
    else if (msg.think === false) showThinking = false;
    else showThinking = true; // think 未定义（对比页等既有调用）保持原行为
  }
  if (showThinking && thinkingVisible()) html += thinkingPanelHtml(msg.reasoning, streaming && !msg.reasoning, msg.reasoningMs);
  html += `<div class="md-body ai-content">${renderMarkdown(msg.content || '')}${streaming ? '<span class="stream-cursor"></span>' : ''}</div>`;
  if (msg.citations && msg.citations.length) {
    // [3.20 阶段2 / 3.20.1 修复] 行内来源标注与底部可点击引用区并存，但**不得前缀"来源："**：
    // 标注本身已是 [来源：文档名·第N段]，再加前缀会渲染成「来源：[来源：…]」的嵌套重复。
    const ann = (c) => '[来源：' + escapeHtml(c.docName) + '·第' + ((typeof c.chunkIdx === 'number' ? c.chunkIdx : 0) + 1) + '段]';
    const chips = msg.citations.map(c =>
      `<span class="citation-chip" data-doc-id="${escapeHtml(c.docId)}" data-chunk-idx="${c.chunkIdx}" title="相关度 ${((c.score || 0) * 100).toFixed(1)}%${c.meta ? ' · ' + escapeHtml(c.meta) : ''}"><i data-lucide="file-text" class="w-3 h-3"></i>${escapeHtml(c.docName)} · 第${(typeof c.chunkIdx === 'number' ? c.chunkIdx : 0) + 1}段</span>`).join('');
    html += `<div class="mt-2 pt-2 border-t border-line-ghost/50" data-rag-sources>`
      + `<div class="text-[10px] text-text-tertiary mb-1">引用来源</div>${chips}`
      + `<div class="rag-source-annot text-[11px] text-text-secondary mt-1.5" data-rag-annot>${msg.citations.map(ann).join(' ')}</div>`
      + `</div>`;
  }
  // [V2] 缓存命中标签
  if (msg.cacheInfo) {
    const label = msg.cacheInfo.from === 'semantic' ? `来自相似缓存 · 相似度 ${Math.round((msg.cacheInfo.similarity || 0) * 100)}%` : '此回复来自缓存';
    html += `<div class="mt-2 flex items-center gap-2"><span class="cache-chip" title="缓存命中：零 token 消耗">
      <i data-lucide="zap" class="w-3 h-3"></i>${label} · 省 ${msg.cacheInfo.tokens || 0} tokens</span>
      <button class="cache-chip cache-chip-btn" data-op="regenerate" data-bypass-cache="1" title="重新生成（绕过缓存，走真实请求）">
        <i data-lucide="refresh-cw" class="w-3 h-3"></i>重新生成(绕过缓存)</button></div>`;
  }
  // [深度思考] 可解释性：auto 档自动开启思考时，在消息底部展示一行 tertiary 小字（含路由去向）
  if (msg.thinkAuto && msg.think) {
    const route = msg.routedTo ? ' · 路由到 ' + shortModelName(msg.routedTo) : '';
    html += `<div class="mt-1.5 flex items-center gap-1 text-[10px] text-text-tertiary think-note"><i data-lucide="sparkles" class="w-3 h-3"></i>复杂任务 · 已自动开启深度思考${route}</div>`;
  }
  el.innerHTML = html;
  enhanceCodeBlocks(el, false);
  refreshIcons();
}

/** 渲染整个消息区（超过保留上限时提示可清理）
 * @returns {void} */
function renderMessages() {
  const box = document.getElementById('chatMessagesNormal');
  if (!box) return;
  box.innerHTML = '';
  if (!AppState.messages.length) {
    box.classList.add('hidden');
    showWelcomeView();
    // [阶段0] 欢迎页存在时不显示本机工具 banner（避免干扰欢迎布局）
    if (typeof refreshNativeAgentBanner === 'function') refreshNativeAgentBanner();
    return;
  }
  box.classList.remove('hidden');
  removeWelcomeView();
  // [阶段三] 超过100条启用窗口化渲染（顶部"加载更早"分页），否则全量渲染
  if (AppState.messages.length > 100 && typeof renderMessagesWindowed === 'function') {
    renderMessagesWindowed(AppState.messages, buildMessageEl, scrollToMsgBottom);
  } else if (typeof appendWithTimeSeparators === 'function') {
    // [夜间优化3.1] 相邻消息间隔>5分钟插入时间分隔线
    appendWithTimeSeparators(box, AppState.messages, buildMessageEl);
    refreshIcons();
    if (MoraySettings.get('autoScroll')) scrollToMsgBottom();
  } else {
    const frag = document.createDocumentFragment();
    AppState.messages.forEach(m => frag.appendChild(buildMessageEl(m, false)));
    box.appendChild(frag);
    refreshIcons();
    if (MoraySettings.get('autoScroll')) scrollToMsgBottom();
  }
  // [阶段三] 会话切换淡入动画
  box.classList.remove('msg-fade');
  void box.offsetWidth;
  box.classList.add('msg-fade');
  // [阶段0] 有消息时刷新本机工具顶部轻提示（10s 缓存，不阻塞渲染）
  if (typeof refreshNativeAgentBanner === 'function') refreshNativeAgentBanner();
}

/** 显示欢迎视图（无会话/空会话时）
 * @returns {void} */
function showWelcomeView() {
  if (document.getElementById('welcomeScreen')) return;
  const content = document.getElementById('chatNormalContent');
  if (!content) return;
  const welcome = document.createElement('div');
  welcome.id = 'welcomeScreen';
  welcome.className = 'welcome-screen';
  welcome.innerHTML = `
    <div class="welcome-logo"><span class="gradient-text font-bold text-3xl relative z-10">◈</span></div>
    <div class="welcome-title">你好，我是 MoRay</div>
    <div class="welcome-subtitle">本地 AI 开发者工作台，所有计算在本地完成，数据不会离开你的设备</div>
    <div class="welcome-suggestions">
      <div class="welcome-suggestion" onclick="useSuggestion('帮我写一个 Python 函数，实现快速排序算法')">
        <div class="welcome-suggestion-icon" style="background:rgba(91,140,255,0.12);color:#5B8CFF;"><i data-lucide="code-2" class="w-4 h-4"></i></div>
        <div class="welcome-suggestion-text"><strong>写代码</strong>帮我实现一个算法</div>
      </div>
      <div class="welcome-suggestion" onclick="useSuggestion('解释一下 Transformer 的自注意力机制')">
        <div class="welcome-suggestion-icon" style="background:rgba(157,123,255,0.12);color:#9D7BFF;"><i data-lucide="brain" class="w-4 h-4"></i></div>
        <div class="welcome-suggestion-text"><strong>问概念</strong>解释一个技术原理</div>
      </div>
      <div class="welcome-suggestion" onclick="useSuggestion('帮我优化这段代码的性能')">
        <div class="welcome-suggestion-icon" style="background:rgba(58,214,232,0.12);color:#3AD6E8;"><i data-lucide="zap" class="w-4 h-4"></i></div>
        <div class="welcome-suggestion-text"><strong>优化代码</strong>提升运行效率</div>
      </div>
      <div class="welcome-suggestion" onclick="useSuggestion('帮我调试这个错误：ConnectionRefusedError')">
        <div class="welcome-suggestion-icon" style="background:rgba(242,178,76,0.12);color:#F2B24C;"><i data-lucide="bug" class="w-4 h-4"></i></div>
        <div class="welcome-suggestion-text"><strong>调试错误</strong>排查问题原因</div>
      </div>
      ${(typeof MoraySettings !== 'undefined' && MoraySettings.get('nativeToolsEnabled') === true && window.MorayBackend && window.MorayBackend.connected) ? `
      <div class="welcome-suggestion welcome-suggestion--agent" onclick="tryLocalAgentSuggestion()" title="载入示例工作区并演示本机 Agent">
        <div class="welcome-suggestion-icon" style="background:rgba(63,214,143,0.12);color:#3FD68F;"><i data-lucide="bot" class="w-4 h-4"></i></div>
        <div class="welcome-suggestion-text"><strong>试试本地 Agent</strong>演示查找→读取→汇总工作区文件</div>
      </div>` : ''}
    </div>`;
  content.insertBefore(welcome, content.firstChild);
  refreshIcons();
}

/** 移除欢迎视图
 * @returns {void} */
function removeWelcomeView() {
  const w = document.getElementById('welcomeScreen');
  if (w) w.remove();
}

/** [阶段1.6 M3] “试试本地 Agent”引导卡动作：
 * 1) 载入示例工作区（不覆盖已有文件）；2) 确保本机工具开关开启并高亮 2 秒；
 * 3) 自动填好一句演示任务并聚焦输入台。
 * @returns {Promise<void>} */
async function tryLocalAgentSuggestion() {
  try {
    // 1) 本机工具开关：未开启则自动开启（联动总开关，避免请求体无 tools 静默失效）
    if (MoraySettings.get('nativeToolsEnabled') !== true) {
      await MoraySettings.set('nativeToolsEnabled', true);
      await MoraySettings.set('toolsEnabled', true);
      syncNativeAgentBtn();
      refreshNativeAgentBanner();
    }
    // 2) 开关高亮 2 秒（克制 beacon）
    const btn = document.getElementById('nativeAgentBtn');
    if (btn) {
      btn.classList.add('native-on-pulse');
      setTimeout(() => btn.classList.remove('native-on-pulse'), 2000);
    }
    // 3) 示例工作区（后端在线时；失败不阻塞演示任务填写）
    try {
      if (typeof loadSampleWorkspace === 'function') await loadSampleWorkspace();
    } catch (e) { /* 离线等：用户仍可看输入台里的演示任务 */ }
    // 4) 自动填好演示任务
    const ta = document.getElementById('chatInputNormal');
    if (ta) {
      ta.value = '先列出计划，然后找到工作区里所有 txt 文件，读取 notes/a.txt 与 notes/b.txt，把两个素材的要点汇总写入 summary.md';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.focus();
    }
    showNotification('演示就绪', '示例文件已就位，任务已填入输入框——点发送即可观看完整 Agent 流程', 'info', 5000);
  } catch (e) {
    showNotification('引导失败', String((e && e.message) || e).slice(0, 140), 'error', 3000);
  }
}

/** 滚动消息区到底部
 * @returns {void} */
function scrollToMsgBottom() {
  const box = document.getElementById('chatMessagesNormal');
  const scrollParent = box ? box.parentElement : null;
  if (scrollParent) scrollParent.scrollTo({ top: scrollParent.scrollHeight, behavior: 'smooth' });
}

/* ---------- 上下文计量 ---------- */

/** 更新 token 计量条（当前会话上下文用量 / 限制）
 * @returns {void} */
function updateTokenMeter() {
  const meter = document.getElementById('tokenMeter');
  if (!meter) return;
  const limit = MoraySettings.get('contextLimit') || 8192;
  // [P0 治本] 只统计当前激活会话的消息（与请求口径一致；空会话显示 0）
  const cid = AppState.activeConv ? AppState.activeConv.id : null;
  const own = cid ? AppState.messages.filter(m => m.conversationId === cid) : [];
  const total = own.reduce((s, m) => s + (estimateTokens(m.content).tokens), 0);
  const pct = Math.min(100, Math.round(total / limit * 100));
  document.getElementById('tokenMeterText').textContent = total + ' / ' + limit;
  const fill = document.getElementById('tokenMeterFill');
  fill.style.width = pct + '%';
  meter.classList.toggle('warn', pct > 80);
  meter.title = `当前会话上下文用量 ${total} tokens（${pct}%），上限 ${limit}`;
}

/* ---------- 发送流程（真实 AI） ---------- */

/** [v3.15.13] 模型身份元问题确定性直答：
 * 命中即不调用 LLM，由前端按“本次实际生效模型”生成助手消息（精确名+来源+手选/路由+reason）。
 * @param {Object} conv - 会话
 * @param {string} question - 用户元问题原文
 * @returns {Promise<void>} */
async function appendDeterministicMetaReply(conv, question) {
  let model = currentConvModel(conv);
  let routeInfo = null;
  const autoRoute = MoraySettings.get('routingEnabled') && conv.userPickedModel !== true &&
    typeof Gateway !== 'undefined' && typeof Gateway.route === 'function';
  if (autoRoute) {
    try {
      const r = await Gateway.route({ model, messages: [{ role: 'user', content: question }], _userPicked: false });
      if (r && r.model) model = r.model;
      routeInfo = r ? r.routeInfo : null;
    } catch (e) { /* 路由失败则退回当前模型 */ }
  }
  model = preciseModelName(model || currentConvModel(conv));
  const source = (typeof isLocalModelName === 'function' && isLocalModelName(model)) ? '本地 Ollama' : '云端 OpenAI 兼容';
  const picked = conv.userPickedModel === true;
  const parts = ['当前实际使用 ' + model + '（' + source + '）', picked ? '由你手动选择' : '由智能路由选择'];
  if (routeInfo && routeInfo.reason) parts.push('路由：' + routeInfo.reason);
  const content = parts.join('；') + '。';
  const msg = {
    id: uid('msg'), conversationId: conv.id, role: 'assistant', content,
    model, createdAt: Date.now(), tokens: 0, stats: null, feedback: 0, citations: [],
    routeReason: routeInfo ? routeInfo.reason : '', think: false, directAnswer: true
  };
  AppState.messages.push(msg);
  (AppState._convMsgIndex[conv.id] = AppState._convMsgIndex[conv.id] || []).push(msg);
  if (!isPrivacyMode()) persistMessage(msg).then(broadcastDataChange).catch(() => {});
  renderMessages();
  updateTokenMeter();
  setActiveModel(model, picked ? 'user' : 'route', routeInfo ? routeInfo.reason : '');
  showNotification('已直答', '模型身份元问题由前端直接回答（未调用 LLM）', 'info', 1800);
}

/**
 * 发送消息（第二阶段真实实现，接管第一阶段 moraySendOverride）。
 * 流程：构建消息 -> 落库 -> 渲染 -> 流式请求 -> 落库更新 -> 统计。
 * @returns {Promise<void>} */
async function sendUserMessage() {
  if (AppState.generating) {
    showNotification('请稍候', 'AI 正在回复中，可点击停止按钮终止', 'warning', 1800);
    return;
  }
  // [夜间优化1.3] 快速连点保护（400ms 内的重复触发直接忽略）
  if (typeof canSendNow === 'function' && !canSendNow()) return;
  const input = document.getElementById('chatInputNormal');
  const raw = input ? input.value.trim() : '';
  if (!raw && !AppState.attachments.length) return;

  // [P0] 发送前拦截：后端可用但模型为空 → 不发请求（避免 400 model is required），弹模型列表
  if (AI.backend !== 'none' && typeof currentConvModel === 'function' && !currentConvModel()) {
    showNotification('未选择模型', '已为你打开模型列表，请选择一个模型', 'warning', 2600);
    const trigger = document.querySelector('#coreInputContainer .input-toolbar-model');
    if (trigger && trigger.__modelSelBound) {
      trigger.click();
    } else {
      // 兜底：直接打开设置页模型区
      document.querySelector('.nav-icon-btn[data-page="settings"]').click();
      setTimeout(() => { const el = document.getElementById('setOpenaiBase'); if (el) el.closest('.glass-card').scrollIntoView({ behavior: 'smooth', block: 'center' }); }, 300);
    }
    return;
  }

  // 确保有会话
  if (!AppState.activeConv) await createNewConversation();
  const conv = AppState.activeConv;

  // 快捷指令解析：/ 提示词调用已在 slash 菜单处理，这里处理 /doc 与 @ 引用
  let content = raw;

  // 移除欢迎页，显示消息流
  removeWelcomeView();
  const box = document.getElementById('chatMessagesNormal');
  box.classList.remove('hidden');

  // 自动命名：首条消息截取前 18 字
  if (conv.title === '新对话' && content) {
    conv.title = content.slice(0, 18) + (content.length > 18 ? '…' : '');
    await DB.updateConversation(conv.id, { title: conv.title });
    document.getElementById('chatTitle').textContent = conv.title;
  }

  // [阶段三] @引用文档上下文（引用 chips 中的文档按当前输入语义检索）
  let extraContext = null, citations = [];
  if (typeof ChatRefs !== 'undefined' && ChatRefs.pending.length) {
    const refCtx = await ChatRefs.buildContext(content);
    if (refCtx) { extraContext = refCtx.text; citations = refCtx.citations; }
  }

  // 落库用户消息（隐私模式下不写盘，仅内存保留）
  const userMsg = { id: uid('msg'), conversationId: conv.id, role: 'user', content, createdAt: Date.now(), tokens: 0, feedback: 0, attachments: (AppState.attachments || []).slice() };
  AppState.messages.push(userMsg);
  // [P0 根治] 双写同步：_convMsgIndex 与 AppState.messages 保持一致（搜索/转发/成本等模块从此索引取数）
  (AppState._convMsgIndex[conv.id] = AppState._convMsgIndex[conv.id] || []).push(userMsg);
  if (!isPrivacyMode()) {
    persistMessage(userMsg).then(broadcastDataChange).catch(e => console.warn('[MoRay] msg persist failed:', e));
  }
  box.appendChild(buildMessageEl(userMsg, true));

  // 清空输入
  input.value = ''; input.style.height = 'auto';
  if (typeof updateInputStats === 'function') updateInputStats('');
  if (typeof ChatRefs !== 'undefined') ChatRefs.clear();
  if (MoraySettings.get('autoScroll')) scrollToMsgBottom();
  // [阶段0] 首条消息落地（欢迎页已移除）→ 显示本机工具顶部轻提示
  if (typeof refreshNativeAgentBanner === 'function') refreshNativeAgentBanner();

  // [V2 功能9] 视觉能力提示：附件图片 + 疑似不支持视觉的模型
  if ((AppState.attachments || []).length && typeof isVisionModel === 'function' && !isVisionModel(conv.model || MoraySettings.get('defaultModel'))) {
    showNotification('模型可能不支持图片', `「${shortModelName(conv.model || '默认模型')}」疑似无视觉能力，图片可能被忽略。可换用 llava/gpt-4o 等视觉模型`, 'warning', 4500);
  }
  // [v3.15.13] 模型身份元问题：metaDirectAnswer 开启时前端确定性直答，不调用 LLM
  if (MoraySettings.get('metaDirectAnswer') !== false &&
      typeof isModelMetaQuestion === 'function' &&
      isModelMetaQuestion(content) &&
      !(AppState.attachments || []).length) {
    await appendDeterministicMetaReply(conv, content);
    if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear();
    return;
  }
  await generateAssistantReply(conv, extraContext, citations);
  // [夜间优化4.1] 附件已随消息发送，清空待发送区
  if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear();
}

/** 构建带上下文的请求消息数组（清洗 + 智能截断策略 + 摘要压缩）
 * [V2] 策略：recent=保留最近（默认）/ starred=标星优先 / summary=AI摘要压缩旧消息
 * @param {Object} conv - 会话对象
 * @param {Object} [extraContext] - 附加上下文 {text, citations}
 * @returns {Promise<Array<{role:string,content:string}>>} 请求消息 */
async function buildRequestMessages(conv, extraContext) {
  const msgs = [];
  // [回复简洁化] 优先级：会话级 > 全局 > 内置默认（仅当两级自定义都为空才用默认，不叠加）
  const rawSys = (conv.systemPrompt || MoraySettings.get('systemPrompt') || '').trim();
  const sysSrc = rawSys || buildSystemPrompt();
  // [V2 功能3] 系统提示词清洗：去冗余空白与重复，节省 token
  let sys = (typeof PromptCompression !== 'undefined') ? PromptCompression.clean(sysSrc).text : sysSrc;
  // [阶段1 M5] 本机 Agent 系统提示：开关开启且请求携带本机工具时追加（用户自定义覆盖内置）
  if (MoraySettings.get('nativeToolsEnabled') && typeof ToolRegistry !== 'undefined' &&
      ToolRegistry.listForRequest().some(t => t.function.name === 'list_directory' || t.function.name === 'submit_plan')) {
    const override = (MoraySettings.get('agentSysPromptOverride') || '').trim();
    const agentPrompt = override || (typeof PlanTracker !== 'undefined' && PlanTracker.SYSTEM_PROMPT) || '';
    if (agentPrompt) sys = (sys ? sys + '\n\n' : '') + agentPrompt;
  }
  // [3.20 阶段2] 知识库引用约定：本次请求携带 query_knowledge_base 时追加标注规范，
  // 让模型在用到检索片段时写出行内来源（确定性兜底由消息底部「来源：」标注负责）
  let kbCite = '';
  if (typeof ToolRegistry !== 'undefined' && ToolRegistry.listForRequest().some(t => t.function.name === 'query_knowledge_base')) {
    kbCite = '【知识库引用】使用 query_knowledge_base 的结果回答时，请在对应结论后标注来源，格式为 [来源：文档名·第N段]（N 用工具返回的 segment 字段）；未命中知识库时如实说明，不要编造文档内容。';
  }
  if (sys || kbCite) msgs.push({ role: 'system', content: kbCite ? (sys ? sys + '\n\n' + kbCite : kbCite) : sys });
  // [批次修复 #6] 短问候短路：新会话首条 ≤8 字符的纯问候（无引用/附件）→ 系统提示追加精简约束
  const _userMsgs = (AppState.messages || []).filter(m => m.conversationId === conv.id && m.role === 'user');
  const _lastUserText = _userMsgs.length ? String((_userMsgs[_userMsgs.length - 1] || {}).content || '').trim() : '';
  const _isShortGreet = _userMsgs.length === 1 && _lastUserText.length <= 8 && !extraContext &&
    !(AppState.attachments || []).length &&
    /^(你?好|您好|在吗|嗨|hi|hello|hey|哈喽|早上好|下午好|晚上好|谢谢|嗯|哦|ok|好的|再见|bye)[!！。.~～\s]*$/i.test(_lastUserText);
  if (_isShortGreet) {
    sys = (sys ? sys + '\n\n' : '') + '约束：当前是新会话的简短问候。请只做简短回应（一两句即可），不要展开介绍功能、不要罗列能力、不要输出无关内容。';
    msgs[0] = { role: 'system', content: sys };
  }
  const limit = MoraySettings.get('historyRetention') || 100;
  // [P0 治本] 严格只认当前会话：conversationId 必须全等 conv.id，无 id 的旧/脏消息一律不放行
  const rawAll = AppState.messages || [];
  const strayCount = rawAll.filter(m => m.conversationId !== conv.id).length;
  if (strayCount) console.warn('[MoRay] 越界/无主消息残留（已剔除不发送）:', strayCount, '条 → 当前 conv', conv.id);
  let history = rawAll.filter(m => m.conversationId === conv.id).slice(-limit);
  // [路由修复] 过滤空 content 消息（user/assistant 均滤）：历史不允许携带空消息；
  // 仅含图片附件的合法用户消息以附件为准保留，不得被当空消息误删
  history = history.filter(m => {
    const hasContent = String(m.content || '').trim().length > 0;
    const hasAttachments = !!(m.attachments && m.attachments.length);
    return hasContent || hasAttachments;
  });
  // [P0 治本] 发送前最终自检：理论上已无越界，双重保险 + 控制台可见请求规模
  const leaked = history.filter(m => m.conversationId !== conv.id || !m.conversationId);
  if (leaked.length) {
    console.error('[MoRay] 请求历史仍含越界消息，已强制剔除:', leaked.length, '条');
    history = history.filter(m => m.conversationId === conv.id);
  }
  console.log('[MoRay ctx] 请求消息', history.length, '条 · 估算', estimateTokens(history.map(m => m.content).join(' ')).tokens, 'tok');
  // 上下文长度预算（估算 token），保留最近的历史
  const budget = (MoraySettings.get('contextLimit') || 8192) - (MoraySettings.get('maxTokens') || 2048) - 100;
  let used = estimateTokens(sys).tokens;
  const strategy = MoraySettings.get('truncationStrategy') || 'recent';
  let picked = [];

  if (strategy === 'starred' && typeof PromptCompression !== 'undefined') {
    // [V2 功能3] 策略C：系统 + 标星消息无条件保留 + 最近消息填充
    const idx = PromptCompression.selectByStrategy(history, budget - used);
    if (idx) picked = idx.map(i => history[i]);
  }
  if (!picked.length) {
    // 策略A（recent）：默认从最新往回收集
    let fitIndex = -1; // 最后一条放入预算的消息下标
    for (let i = history.length - 1; i >= 0; i--) {
      const t = estimateTokens(history[i].content).tokens;
      if (used + t > budget && history.length - 1 - i >= 2) break;
      used += t;
      fitIndex = i;
    }
    if (strategy === 'summary' && fitIndex > 2) {
      // [V2 功能3] 策略B：对预算外旧消息做一次 AI 摘要，替换进上下文（不改动原始记录）
      try {
        const cut = history[fitIndex];
        const needFresh = !conv.summary || !conv.summaryUpTo || conv.summaryUpTo < cut.createdAt;
        if (needFresh && AI.backend !== 'none') {
          showNotification('上下文压缩', '正在为较早的对话生成摘要…', 'info', 2500);
          const toSum = history.slice(0, fitIndex).map(m => (m.role === 'user' ? '用户: ' : 'AI: ') + m.content).join('\n').slice(0, 4000);
          const r = await AI.chat({ model: conv.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name), messages: [{ role: 'user', content: '把以下对话压缩为200字内要点摘要，保留关键结论/代码要点/未尽事项：\n' + toSum }] });
          conv.summary = r.content;
          conv.summaryUpTo = cut.createdAt;
          DB.updateConversation(conv.id, { summary: conv.summary, summaryUpTo: conv.summaryUpTo }).catch(() => {});
        }
        if (conv.summary) {
          msgs.push({ role: 'system', content: '此前对话的摘要：' + conv.summary });
          picked = history.slice(fitIndex);
        }
      } catch (e) { console.warn('[MoRay] summary truncation failed:', e); }
    }
    if (!picked.length) {
      for (let i = history.length - 1; i >= (strategy === 'summary' ? fitIndex : 0); i--) {
        if (strategy !== 'summary') { picked.unshift(history[i]); continue; }
        picked.unshift(history[i]);
      }
      if (strategy !== 'summary') picked = history.slice(Math.max(0, fitIndex === -1 ? 0 : fitIndex));
    }
  }
  // [V2 功能3] 上下文 80% 预警（每会话一次）
  if (used > budget * 0.8 && !conv.__ctxWarned) {
    conv.__ctxWarned = true;
    showNotification('上下文即将满', `已用约 ${Math.round(used / budget * 100)}%，较早消息将被自动截断以节省 token`, 'warning', 3500);
  }
  // [成本中心] 记录本次压缩/截断节省（系统提示词清洗 + 截断掉的旧消息）
  const totalHistory = history.reduce((s, m) => s + estimateTokens(m.content).tokens, 0);
  const kept = picked.reduce((s, m) => s + estimateTokens(m.content).tokens, 0);
  const sysSaved = (typeof PromptCompression !== 'undefined') ? PromptCompression.clean(rawSys).savedTokens : 0;
  conv.__lastCompressionSaved = Math.max(0, sysSaved + (totalHistory - kept));
  picked.forEach(m => msgs.push({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }));
  if (extraContext && extraContext.text) {
    msgs.push({ role: 'system', content: extraContext.text });
  }
  // [夜间优化4.1] 多模态：附件图片并入最后一条用户消息（Ollama: images / OpenAI: image_url）
  const atts = AppState.attachments || [];
  if (atts.length) {
    const lastUser = msgs.slice().reverse().find(m => m.role === 'user');
    if (lastUser) {
      if (AI.backend === 'openai') {
        lastUser.content = [{ type: 'text', text: String(lastUser.content) }].concat(atts.map(a => ({ type: 'image_url', image_url: { url: a.dataUrl } })));
      } else {
        lastUser.images = atts.map(a => String(a.dataUrl).split(',')[1]);
      }
    }
  }
  return msgs;
}

/** 生成 AI 回复（流式渲染到气泡）
 * @param {Object} conv - 会话对象
 * @param {Object} [extraContext] - RAG 注入的上下文
 * @param {Array} [citations] - 引用来源
 * @returns {Promise<void>} */
async function generateAssistantReply(conv, extraContext, citations, genOpts) {
  genOpts = genOpts || {};
  if (AI.backend === 'none') {
    await AI.detectBackend();
    if (AI.backend === 'none') {
      showNotification('未连接后端', '未检测到 Ollama 或 OpenAI 兼容 API，请到设置页配置。本次回复为离线占位。', 'warning', 5000);
    }
  }
  const model = conv.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name) || '';
  // [小补丁] 提前计算 think 决策（占位气泡渲染思考面板/状态文案需要；与流式调用处同一逻辑）
  const thinkMode = MoraySettings.get('thinkMode');
  let think;
  if (thinkMode === 'on') think = true;
  else if (thinkMode === 'off') think = false;
  else think = false; // auto 档需 classify 结果，buildRequestMessages 完成后补齐（见下方 await 后）
  const assistantMsg = {
    id: uid('msg'), conversationId: conv.id, role: 'assistant',
    content: '', reasoning: '', model, createdAt: Date.now(), tokens: 0, stats: null, feedback: 0,
    citations: citations || [],
    // [小补丁] 占位阶段即记录思考决策（加载态面板/状态文案判断用）
    think: think === true
  };
  AppState.messages.push(assistantMsg);
  // [P0 根治] 双写同步：_convMsgIndex 与 AppState.messages 保持一致
  (AppState._convMsgIndex[conv.id] = AppState._convMsgIndex[conv.id] || []).push(assistantMsg);

  // 创建流式气泡
  const box = document.getElementById('chatMessagesNormal');
  const el = document.createElement('div');
  el.className = 'msg-bubble flex justify-start msg-enter';
  el.dataset.msgId = assistantMsg.id;
  el.innerHTML = `
    <div class="max-w-[85%]">
      <div class="flex items-center gap-2 mb-1">
        <span class="beacon-dot"></span>
        <!-- [小补丁] 占位头部：精确模型名（含 :b 后缀）+ 按 think 决策显示 思考中/正在生成 -->
        <span class="text-[10px] text-text-tertiary">${escapeHtml(preciseModelName(model))} · <span class="gen-status-text">${(think === true && isThinkingModel(model)) ? '思考中' : '正在生成'}</span></span>
      </div>
      <div class="rounded-xl px-4 py-3 bg-surface-card border border-line-ghost/60 msg-bubble__ai-box">
        <div class="ai-msg-inner"></div>
        <div class="stream-actions flex items-center gap-2 mt-2">
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1 stop-gen-btn">
            <i data-lucide="square" class="w-3 h-3"></i>停止生成
          </button>
        </div>
      </div>
    </div>`;
  box.appendChild(el);
  const inner = el.querySelector('.ai-msg-inner');
  fillAssistantBody(inner, { reasoning: '', citations: citations || [] }, true);
  // [阶段三] 打字指示器（首个增量到达后移除）
  const typingEl = showTypingIndicator(inner);
  // [批次修复 #3] 冷加载等待反馈：2s 无首 token → 明确占位（首 token/收尾移除指示器后自然失效）
  const coldStartTimer = setTimeout(() => {
    try {
      const label = typingEl && typingEl.isConnected ? typingEl.querySelector('.typing-label') : null;
      if (label && AppState.generating && !assistantMsg.content && !assistantMsg.reasoning) {
        label.textContent = '模型加载中（冷启动约 5~15s，首次使用后更快）…';
      }
    } catch (e) { /* 忽略 */ }
  }, 2000);
  refreshIcons();
  if (MoraySettings.get('autoScroll')) scrollToMsgBottom();

  // 停止按钮
  let aborted = false;
  on(el.querySelector('.stop-gen-btn'), 'click', () => {
    aborted = true;
    if (AppState.genController) AppState.genController.abort();
  });

  if (AI.backend === 'none') {
    // 离线占位回复
    typingEl.remove();
    assistantMsg.content = '> ⚠️ 当前未连接 AI 后端。\n>\n> 请在 **设置 → Ollama 本地服务** 中配置服务地址并确认 Ollama 已启动；\n> 或在 **设置 → OpenAI 兼容 API** 中填写接口地址与 API Key。\n>\n> 配置完成后重新发送消息即可获得真实回复。';
    persistMessage(assistantMsg).catch(() => {});
    fillAssistantBody(inner, assistantMsg, false);
    finalizeAssistantUI(el, assistantMsg);
    return;
  }

  const requestMessages = await buildRequestMessages(conv, extraContext);
  // [小补丁] auto 档在 requestMessages 就绪后补齐 classify 决策并回写占位消息（on/off 已提前定）
  if (thinkMode === 'auto') {
    // [深度思考] 统一决策入口（qwen3.5 auto 默认 false 逻辑已并入 decideThinkFor）
    think = decideThinkFor(model, requestMessages);
    assistantMsg.think = think === true;
  }
  const genStart = performance.now();
  // [流式性能] 每个生成气泡独立的 rAF 合并调度（token 只标脏，≥60ms 才整段重绘 Markdown）
  let rafPending = false, lastStreamPaint = 0;
  const scheduleStreamPaint = () => {
    if (rafPending || !AppState.generating) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      if (!AppState.generating) return; // 完成/停止后不再补画流式光标
      const now = performance.now();
      if (now - lastStreamPaint < 60) {
        setTimeout(scheduleStreamPaint, Math.max(1, 60 - (now - lastStreamPaint)));
        return;
      }
      lastStreamPaint = now;
      const ce = inner.querySelector('.ai-content');
      if (ce && ce.isConnected) {
        ce.innerHTML = renderMarkdown(assistantMsg.content) + '<span class="stream-cursor"></span>';
        if (MoraySettings.get('autoScroll')) scrollToMsgBottom();
      }
    });
  };
  // [V2] 经由 API 智能网关：缓存 → 智能路由 → 超时 → 重试 → 降级
  const useGateway = MoraySettings.get('gatewayEnabled') !== false && typeof Gateway !== 'undefined';
  const chatFn = useGateway ? Gateway : AI;
  const { controller, promise } = chatFn.chatStream({
    model,
    messages: requestMessages,
    hasImages: (AppState.attachments || []).length > 0,
    bypassCache: !!genOpts.bypassCache,
    // [深度思考] 三态决策结果（on/off/auto-complex 时下发布尔；非思考模型由 20_ai 过滤不加字段）
    think,
    // [小补丁] 路由结果早期回调：加载态气泡立即刷新为实际命中模型/reason/思考面板（不等流式结束）
    onRoute: (routed) => {
      if (!routed || !routed.model) return;
      assistantMsg.model = routed.model;
      if (routed.routeInfo) {
        assistantMsg.routedTo = routed.routeInfo.model || routed.model;
        assistantMsg.routeReason = routed.routeInfo.reason || '';
      }
      const hdr = el.querySelector('.text-\\[10px\\]');
      if (hdr) {
        const thinkingNow = assistantMsg.think === true && isThinkingModel(assistantMsg.model);
        hdr.innerHTML = escapeHtml(preciseModelName(assistantMsg.model)) + ' · <span class="gen-status-text">' + (thinkingNow ? '思考中' : '正在生成') + '</span>';
        // [TTFT 反馈] 实际模型确定后区分“已驻留”与“正在唤醒（冷加载）”
        (async () => {
          try {
            if (typeof isLocalModelName !== 'function' || !isLocalModelName(assistantMsg.model)) return;
            if (typeof ollamaLoadedModels !== 'function') return;
            const loaded = await ollamaLoadedModels();
            const st = hdr.querySelector('.gen-status-text');
            if (st && !assistantMsg.content && AppState.generating) {
              st.textContent = loaded.includes(assistantMsg.model) ? '响应中…' : '正在唤醒模型，约 5-10 秒…';
            }
          } catch (e) { /* 反馈失败不影响请求 */ }
        })();
      }
      // 思考面板按实际模型重新评估：非思考型/think=false 立即移除占位"深度思考中"
      const tp = inner.querySelector('.thinking-panel');
      const eligible = assistantMsg.think === true && isThinkingModel(assistantMsg.model);
      if (!eligible && tp) tp.remove();
      else if (eligible && !tp && !assistantMsg.reasoningRendered && AppState.generating) {
        inner.insertAdjacentHTML('afterbegin', thinkingPanelHtml('', true, null));
        refreshIcons();
      }
    },
    // [路由修复] 显式锁定：仅 userPickedModel 为 true（会话内手动选过模型）才算锁定；
    // 全局 defaultModel 仅作兜底候选，不锁路由
    _userPicked: conv.userPickedModel === true,
    // [工具调用] 步骤事件 → 实时步骤卡（首个事件自动插入最终回复之前）
    onToolStep: (evt) => {
      if (typeof ToolRegistry !== 'undefined') ToolRegistry.renderLiveStep(el, evt);
      // [阶段1 M3] 计划编排：submit_plan 登记 / 真实工具事件推进计划步骤状态
      if (typeof PlanTracker !== 'undefined') PlanTracker.observe(evt, el);
    },
    onChunk: ({ content, reasoning }) => {
      if (content) assistantMsg.content += content;
      if (reasoning) assistantMsg.reasoning += reasoning;
      // [夜间优化3.1] 生成速度实时显示
      const secs = (performance.now() - genStart) / 1000;
      if (typeof updateGenStatus === 'function') {
        // [小补丁] 无正文时的状态文案按 think 决策区分：思考型+think=true 才"深度思考中"
        updateGenStatus(el, assistantMsg.content
          ? `生成中 · ${Math.round((assistantMsg.content.length / 4) / Math.max(0.5, secs))} tok/s`
          : (assistantMsg.think === true && isThinkingModel(assistantMsg.model) ? '深度思考中' : '正在生成'));
      }
      // 流式渲染：token 到达只标记脏帧，由 rAF 按帧合并（≥60ms 才真正整段重绘 Markdown），
      // 避免每个 token 全量 reparse；代码高亮统一留到结束后的完整渲染，不在流式中逐 token 高亮。
      if (typingEl.isConnected) typingEl.remove();
      scheduleStreamPaint();
      if (reasoning) {
        const tp = inner.querySelector('.thinking-panel');
        if (tp && !assistantMsg.reasoningRendered) {
          assistantMsg.reasoningRendered = true;
          tp.outerHTML = thinkingPanelHtml(assistantMsg.reasoning, false);
          refreshIcons();
        } else if (assistantMsg.reasoningRendered) {
          const tc = inner.querySelector('.thinking-content');
          if (tc) tc.textContent = assistantMsg.reasoning;
        }
      }
    }
  });
  AppState.generating = true;
  AppState.genController = controller;
  // [3.20 阶段2] 清空知识库来源收集器：保证上一轮的来源不会串到本轮
  if (typeof RagSources !== 'undefined') RagSources.clear();

  try {
    const result = await promise;
    assistantMsg.content = result.content || assistantMsg.content;
    assistantMsg.reasoning = result.reasoning || assistantMsg.reasoning;
    assistantMsg.stats = result.stats;
    assistantMsg.tokens = result.stats.tokens || 0;
    // [阶段1 M3] 全部步骤结束：模型总结已输出 → 计划剩余待办步骤标记完成
    if (typeof PlanTracker !== 'undefined' && PlanTracker.current) {
      PlanTracker.current.steps.forEach(s => { if (s.status === 'todo' || s.status === 'running') s.status = 'done'; });
      PlanTracker.render(el);
      // [阶段1.6] 同步最终状态到消息持久化
      try {
        const msgId = el && el.dataset ? el.dataset.msgId : null;
        const msg = msgId ? (AppState.messages || []).find(m => m.id === msgId) : null;
        if (msg && msg.plan) {
          msg.plan.steps.forEach((ps, i) => { if (PlanTracker.current.steps[i]) ps.status = PlanTracker.current.steps[i].status; });
          if (typeof persistMessage === 'function') persistMessage(msg).catch(() => {});
        }
      } catch (e) { /* 忽略 */ }
    }
    // [V2] 记录缓存命中与路由信息
    if (result._cache) assistantMsg.cacheInfo = { from: result._cache.from, similarity: result._cache.similarity, tokens: result._cache.tokens || 0 };
    if (result._routed) {
      assistantMsg.routedTo = result._routed.model;
      // [路由修复] 回写实际命中模型（自动路由分流后气泡精确显示，含 :4b/:9b 后缀）
      if (result._routed.model) assistantMsg.model = result._routed.model;
    }
    // [工具调用] 持久化工具步骤（旧消息无此字段 → 正常显示，向后兼容）
    if (result._toolSteps) assistantMsg.toolCalls = result._toolSteps;
    // [3.20 阶段2] 知识库来源标注：把 query_knowledge_base 本轮登记的来源并入消息引用
    // （按相关度降序、按 文档+段号 去重），随后 fillAssistantBody 会渲染「[来源：文档名·第N段]」
    if (typeof RagSources !== 'undefined') {
      const rag = RagSources.drain();
      if (rag.length) {
        const base = (assistantMsg.citations || []).slice();
        const seen = new Set(base.map(c => String(c.docId) + '#' + c.chunkIdx));
        rag.forEach(c => {
          const k = String(c.docId) + '#' + c.chunkIdx;
          if (seen.has(k)) return;
          seen.add(k);
          base.push(c);
        });
        assistantMsg.citations = base;
      }
    }
    typingEl.remove();
    // [V3 P3.6] AI 自动命名：标题还是自动截断时，后台静默生成更好的标题
    if (MoraySettings.get('autoNaming') && !conv.renamed && !conv.autoNamed && assistantMsg.content) {
      const firstUser = AppState.messages.find(m => m.role === 'user');
      if (firstUser) autoNameConversation(conv, firstUser.content).catch(() => {});
    }
    // [成本中心] 持久化成本/压缩/路由字段（缺失按0，向后兼容）
    assistantMsg.promptTokens = (result._cost && result._cost.promptTokens) || 0;
    assistantMsg.costCNY = (result._cost && result._cost.cost) || 0;
    assistantMsg.baselineCNY = (result._cost && result._cost.baseline) || 0;
    assistantMsg.savedCNY = (result._cost && result._cost.saved) || 0;
    assistantMsg.routeReason = (result._routed && result._routed.reason) || '';
    // [深度思考] 记录决策与是否自动档（用于消息底部一行小字展示，让用户看得见"为何思考"）
    assistantMsg.think = think === true;
    assistantMsg.thinkAuto = thinkMode === 'auto';
    assistantMsg.compressionSaved = conv.__lastCompressionSaved || 0;
    // [显示统一] 以“最终真正发出请求并成功返回的模型”为唯一权威，刷新输入台/状态栏
    setActiveModel(
      assistantMsg.model || model,
      (result._routed && result._routed.fallback) ? 'fallback' : (result._routed ? 'route' : 'default'),
      assistantMsg.routeReason || ''
    );
    persistMessage(assistantMsg).catch(() => {});
    fillAssistantBody(inner, assistantMsg, false);
    recordPerf(model, result.stats, true);
    updatePerfUI();
  } catch (e) {
    const errMsg = String(e && e.message || e);
    if (e && e.name === 'AbortError' || aborted) {
      assistantMsg.content += '\n\n*[已停止生成]*';
      persistMessage(assistantMsg).catch(() => {});
      fillAssistantBody(inner, assistantMsg, false);
      showNotification('已停止', '生成已终止', 'info', 1500);
    } else {
      console.error('[MoRay] chat error:', e);
      recordPerf(model, null, false);
      updatePerfUI();
      assistantMsg.content = (assistantMsg.content || '') + `\n\n> ❌ **生成失败**：${escapeHtml(errMsg)}`;
      persistMessage(assistantMsg).catch(() => {});
      fillAssistantBody(inner, assistantMsg, false);
      // [阶段三] 错误卡片（带重试按钮）
      el.querySelector('.rounded-xl').appendChild(buildErrorCard('生成失败', errMsg, () => {
        AppState.messages = AppState.messages.filter(m => m.id !== assistantMsg.id);
      removeMsgFromIndex(assistantMsg.id);
        DB.deleteMessage(assistantMsg.id).catch(() => {});
        el.remove();
        generateAssistantReply(conv, extraContext, citations);
      }));
      showNotification('生成失败', errMsg, 'error', 4000);
    }
  } finally {
    AppState.generating = false;
    AppState.genController = null;
    // [工具调用] 完成/中止/失败统一恢复"工具执行中"发送按钮状态
    if (typeof ToolRegistry !== 'undefined') ToolRegistry.resetToolUI();
    finalizeAssistantUI(el, assistantMsg);
    renderSessionList();
    updateTokenMeter();
  }
}

/** 生成结束后清理流式UI（移除停止按钮、刷新统计行）
 * @param {HTMLElement} el - 消息容器
 * @param {Object} msg - 消息对象
 * @returns {void} */
function finalizeAssistantUI(el, msg) {
  const actions = el.querySelector('.stream-actions');
  if (actions) actions.remove();
  const header = el.querySelector('.text-\\[10px\\]');
  if (header) {
    // [路由修复] 气泡头部精确显示实际模型（含 :4b/:9b），不再只显示家族名
    header.textContent = `${preciseModelName(msg.model)} · ${clockTime(msg.createdAt)}`;
    // [路由修复] 有路由决策时在 meta 区下方展示一行 reason 小字（无路由信息不显示）
    if (msg.routeReason) {
      const metaWrap = header.parentElement;
      const parent = metaWrap ? metaWrap.parentElement : null;
      if (parent) {
        let note = parent.querySelector('.route-reason-note');
        if (!note) {
          note = document.createElement('div');
          note.className = 'route-reason-note';
          parent.insertBefore(note, metaWrap.nextSibling);
        }
        // [v3.15.13] 默认折叠成“路由详情”小胶囊；routingDebug 开启时才平铺完整链路
        if (MoraySettings.get('routingDebug')) {
          note.className = 'route-reason-note text-[10px] text-text-tertiary mt-0.5';
          note.textContent = msg.routeReason;
        } else {
          note.className = 'route-reason-note mt-0.5';
          note.innerHTML = '<details style="display:inline-block"><summary style="cursor:pointer;font-size:10px;color:var(--color-text-tertiary);border:1px solid var(--color-line-ghost);border-radius:999px;padding:1px 8px">路由详情</summary><div style="font-size:10px;color:var(--color-text-tertiary);margin-top:4px">' + escapeHtml(msg.routeReason) + '</div></details>';
        }
      }
    }
  }
  // [v3.15.13] 统一收尾：所有结束路径清理流式光标/打字指示，避免残留青色竖条
  el.querySelectorAll('.stream-cursor, .typing-indicator').forEach(n => n.remove());
  // [P2] 流式完成/停止/失败后就地补齐消息操作条（无需重开/重建会话）
  const content = el.querySelector('.rounded-xl');
  if (content && !content.querySelector('.msg-actions') && msg && msg.role === 'assistant') {
    content.insertAdjacentHTML('beforeend', msgActionsHtml(msg));
    refreshIcons();
  }
  updateTokenMeter();
}

/* ---------- 消息操作 ---------- */

/** 消息操作统一入口（悬浮按钮 + 右键菜单共用）
 * @param {string} op - 操作名
 * @param {HTMLElement} bubbleEl - 所在消息气泡
 * @param {boolean} [bypassFlag] - [返修] 显式绕过缓存标志（缓存标签按钮传入 true）
 * @returns {Promise<boolean>} 是否已处理 */
async function handleMsgAction(op, bubbleEl, bypassFlag) {
  const msgId = bubbleEl && bubbleEl.dataset.msgId;
  const msg = AppState.messages.find(m => m.id === msgId);
  switch (op) {
    case 'copy': {
      const text = msg ? msg.content : (bubbleEl ? bubbleEl.textContent.trim() : '');
      const ok = await copyToClipboard(text);
      showNotification(ok ? '已复制' : '复制失败', ok ? '消息内容已复制到剪贴板' : '', ok ? 'success' : 'error', 1600);
      return true;
    }
    case 'regenerate': {
      if (!msg || AppState.generating) return true;
      // 删除该条 AI 消息及其后所有消息，重新生成
      const idx = AppState.messages.findIndex(m => m.id === msgId);
      const removed = AppState.messages.splice(idx);
      for (const m of removed) { await DB.deleteMessage(m.id).catch(() => {}); removeMsgFromIndex(m.id); }
      bubbleEl.remove();
      // [V2] 从缓存消息点"重新生成"= 绕过缓存真实请求；[返修] 显式绕过标志优先（缓存标签按钮）
      const bypass = bypassFlag === true ? true : !!(msg.cacheInfo || msg.routedTo);
      if (bypass) showNotification('绕过缓存', '本次将发起真实 API 请求', 'info', 1800);
      generateAssistantReply(AppState.activeConv, null, null, { bypassCache: bypass });
      return true;
    }
    case 'saveSnippet': {
      if (!msg) return true;
      const codeBlock = bubbleEl.querySelector('pre code');
      const snip = await DB.createSnippet({
        title: '来自对话 ' + clockTime(msg.createdAt),
        code: codeBlock ? codeBlock.textContent : (msg.content || '').slice(0, 2000),
        language: codeBlock ? (detectLanguage(codeBlock.textContent) || 'text') : 'markdown',
        description: '保存自会话「' + (AppState.activeConv ? AppState.activeConv.title : '') + '」',
        tags: ['对话保存']
      });
      SnippetsApp.cache.push(snip);
      showNotification('已保存为片段', snip.title, 'success', 2000);
      return true;
    }
    case 'quote': {
      if (!msg) return true;
      quoteReply(msg);
      return true;
    }
    case 'forward': {
      if (!msg) return true;
      forwardToNewConversation(msg);
      return true;
    }
    case 'star': {
      if (!msg) return true;
      await toggleStarMessage(msg);
      const starBtn = bubbleEl.querySelector('[data-op="star"]');
      if (starBtn) starBtn.classList.toggle('active', !!msg.starred);
      return true;
    }
    case 'like': case 'dislike': {
      if (!msg) return true;
      msg.feedback = op === 'like' ? (msg.feedback === 1 ? 0 : 1) : (msg.feedback === -1 ? 0 : -1);
      await DB.putMessage(msg);
      bubbleEl.querySelectorAll('.msg-action-btn[data-op="like"],.msg-action-btn[data-op="dislike"]').forEach(b => b.classList.remove('active'));
      const btn = bubbleEl.querySelector(`.msg-action-btn[data-op="${op}"]`);
      if (btn && msg.feedback) btn.classList.add('active');
      showNotification('反馈已记录', msg.feedback === 1 ? '感谢正面反馈' : '已记录负面反馈', 'info', 1200);
      return true;
    }
    case 'branch': {
      if (!msg) return true;
      const idx = AppState.messages.findIndex(m => m.id === msgId);
      const clipped = AppState.messages.slice(0, idx + 1);
      const conv = await DB.createConversation({ title: (AppState.activeConv.title || '新对话') + ' · 分支', model: AppState.activeConv.model, userPickedModel: AppState.activeConv.userPickedModel === true });
      for (const m of clipped) {
        await DB.putMessage(Object.assign({}, m, { id: uid('msg'), conversationId: conv.id, createdAt: m.createdAt }));
      }
      AppState.conversations.unshift(conv);
      AppState._convMsgIndex[conv.id] = clipped.map(m => Object.assign({}, m, { id: uid('msg'), conversationId: conv.id }));
      renderSessionList();
      showNotification('已创建分支', '新会话「' + conv.title + '」保留该消息前的全部上下文', 'success', 2500);
      return true;
    }
    case 'edit': {
      if (!msg) return true;
      // 将消息内容放回输入框并删除该消息（用户改完重发）
      const input = document.getElementById('chatInputNormal');
      if (input) { input.value = msg.content; input.focus(); if (typeof updateInputStats === 'function') updateInputStats(msg.content); }
      if (msg.role === 'user') {
        const idx = AppState.messages.findIndex(m => m.id === msgId);
        AppState.messages.splice(idx, 1);
    removeMsgFromIndex(msgId);
        await DB.deleteMessage(msgId).catch(() => {});
        bubbleEl.remove();
      }
      showNotification('编辑模式', '内容已放回输入框，修改后重新发送', 'info', 2200);
      return true;
    }
    case 'delete': {
      if (!msg) return true;
      const idx = AppState.messages.findIndex(m => m.id === msgId);
      if (idx >= 0) { AppState.messages.splice(idx, 1); removeMsgFromIndex(msgId); }
    removeMsgFromIndex(msgId);
      await DB.deleteMessage(msgId).catch(() => {});
      bubbleEl.remove();
      updateTokenMeter();
      showNotification('已删除', '消息已删除', 'success', 1400);
      return true;
    }
  }
  return false;
}

/** 消息区事件委托
 * @returns {void} */
function setupMessageEvents() {
  const box = document.getElementById('chatMessagesNormal');
  if (!box) return;
  box.addEventListener('click', (e) => {
    // 思考面板折叠
    const th = e.target.closest('[data-toggle-thinking]');
    if (th) {
      const content = th.nextElementSibling;
      content.classList.toggle('collapsed');
      const chev = th.querySelector('.thinking-chevron');
      if (chev) chev.style.transform = content.classList.contains('collapsed') ? 'rotate(-90deg)' : 'rotate(0)';
      return;
    }
    // 引用来源点击 -> 跳转文档预览
    const chip = e.target.closest('.citation-chip');
    if (chip) {
      openDocPreview(chip.dataset.docId, parseInt(chip.dataset.chunkIdx, 10));
      return;
    }
    const btn = e.target.closest('.msg-action-btn') || e.target.closest('.cache-chip-btn');
    if (btn) {
      const bubble = btn.closest('.msg-bubble');
      // [返修] 缓存标签"重新生成(绕过缓存)"按钮（data-bypass-cache=1）→ 显式真实请求
      handleMsgAction(btn.dataset.op, bubble, btn.dataset.bypassCache === '1');
    }
  });
}

/** 右键菜单动作接管（配合第一阶段 morayContextAction 钩子）
 * @param {string} action - 动作名
 * @returns {boolean} 是否已处理 */
function contextActionDispatcher(action) {
  // 右键时记录目标气泡
  const map = { copy: 'copy', edit: 'edit', regenerate: 'regenerate', branch: 'branch', delete: 'delete' };
  const op = map[action];
  if (!op) return false;
  const bubble = window.__morayCtxBubble;
  if (bubble && bubble.closest && bubble.closest('#chatMessagesNormal')) {
    handleMsgAction(op, bubble);
    return true;
  }
  return false;
}

// 记录右键目标气泡
document.addEventListener('contextmenu', (e) => {
  const bubble = e.target.closest && e.target.closest('.msg-bubble');
  window.__morayCtxBubble = bubble;
}, true);

/* ---------- 导出 ---------- */

/** 导出当前会话
 * @param {string} [format] - md | html | json，缺省弹出选择
 * @returns {Promise<void>} */
async function exportConversation(format) {
  const conv = AppState.activeConv;
  if (!conv || !AppState.messages.length) {
    showNotification('无法导出', '当前会话为空', 'warning');
    return;
  }
  const fname = (conv.title || '会话').replace(/[\\/:*?"<>|]/g, '_');
  if (!format) {
    const box = showModal(`
      <div class="space-y-2">
        <button class="w-full text-left px-4 py-3 rounded-lg border border-line-ghost hover:border-brand-cobalt/50 transition-colors" data-fmt="md">
          <div class="text-sm text-text-primary">导出为 Markdown</div><div class="text-[11px] text-text-tertiary">纯文本标记格式，适合导入笔记软件</div>
        </button>
        <button class="w-full text-left px-4 py-3 rounded-lg border border-line-ghost hover:border-brand-cobalt/50 transition-colors" data-fmt="html">
          <div class="text-sm text-text-primary">导出为 HTML</div><div class="text-[11px] text-text-tertiary">带样式的独立网页文件</div>
        </button>
        <button class="w-full text-left px-4 py-3 rounded-lg border border-line-ghost hover:border-brand-cobalt/50 transition-colors" data-fmt="txt">
          <div class="text-sm text-text-primary">导出为纯文本</div><div class="text-[11px] text-text-tertiary">无格式标记，适合任何场景粘贴</div>
        </button>
      </div>
      <div class="mt-3 pt-3 border-t border-line-ghost/50 flex gap-4 text-xs text-text-secondary">
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="expOptTime" class="accent-brand-cobalt w-3.5 h-3.5">含时间戳</label>
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="expOptModel" class="accent-brand-cobalt w-3.5 h-3.5">含模型信息</label>
        <label class="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" id="expOptStarred" class="accent-brand-cobalt w-3.5 h-3.5">仅导出收藏消息</label>
      </div>`, { title: '导出会话', icon: 'download', footer: false });
    // [V2 优化1] 导出选项
    const opts = {
      time: box.querySelector('#expOptTime').checked,
      model: box.querySelector('#expOptModel').checked,
      starredOnly: box.querySelector('#expOptStarred').checked
    };
    window.__exportOpts = opts;
    box.querySelectorAll('[data-fmt]').forEach(b => b.addEventListener('click', () => { closeModal(); exportConversation(b.dataset.fmt); }));
    return;
  }
  // [V2 优化1] 应用导出选项
  const expOpts = window.__exportOpts || {};
  let msgs = AppState.messages;
  if (expOpts.starredOnly) {
    msgs = msgs.filter(m => m.starred);
    if (!msgs.length) { showNotification('无收藏消息', '先在消息操作栏星标需要导出的消息', 'warning', 2500); return; }
  }
  const timeTag = (m) => expOpts.time ? `（${clockTime(m.createdAt)}）` : '';
  const modelTag = (m) => expOpts.model && m.model ? ` [${m.model}]` : '';
  if (format === 'md') {
    let md = `# ${conv.title}\n\n> 导出时间：${new Date().toLocaleString()} · 模型：${conv.model || '默认'}\n\n---\n\n`;
    msgs.forEach(m => { md += m.role === 'user' ? `## 👤 用户${timeTag(m)}\n\n${m.content}\n\n` : `## 🤖 ${m.model || 'AI'}${modelTag(m)}${timeTag(m)}\n\n${m.content}\n\n`; });
    downloadText(fname + '.md', md, 'text/markdown;charset=utf-8');
  } else if (format === 'txt') {
    // [V2 优化1] 纯文本导出
    let txt = `${conv.title}\n${'='.repeat(30)}\n\n`;
    msgs.forEach(m => {
      txt += (m.role === 'user' ? '【用户】' : '【AI】') + (expOpts.model && m.model ? `（${m.model}）` : '') + (expOpts.time ? ` ${clockTime(m.createdAt)}` : '') + '\n';
      txt += m.content + '\n\n';
    });
    downloadText(fname + '.txt', txt, 'text/plain;charset=utf-8');
  } else if (format === 'html') {
    let body = '';
    for (const m of AppState.messages) {
      body += `<div class="msg ${m.role}"><div class="who">${m.role === 'user' ? '👤 用户' : '🤖 ' + escapeHtml(m.model || 'AI')} · ${clockTime(m.createdAt)}</div><div class="bubble">${renderMarkdown(m.content)}</div></div>`;
    }
    const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>${escapeHtml(conv.title)}</title>
<style>body{font-family:system-ui,sans-serif;background:#0A0C14;color:#E8ECF5;max-width:820px;margin:0 auto;padding:32px 20px}
h1{font-size:22px}.msg{margin:18px 0}.who{font-size:12px;color:#616C82;margin-bottom:6px}
.bubble{padding:14px 16px;border-radius:12px;line-height:1.7;font-size:14px}
.user .bubble{background:rgba(91,140,255,0.14);border:1px solid rgba(91,140,255,0.2)}
.assistant .bubble{background:#1A1F30;border:1px solid #262C3E}
pre{background:#0D1119;padding:12px;border-radius:8px;overflow-x:auto;font-size:13px}
code{font-family:ui-monospace,monospace}a{color:#5B8CFF}table{border-collapse:collapse}th,td{border:1px solid #262C3E;padding:6px 10px}</style>
</head><body><h1>${escapeHtml(conv.title)}</h1>${body}</body></html>`;
    downloadText(fname + '.html', html, 'text/html;charset=utf-8');
  } else if (format === 'json') {
    downloadJson(fname + '.json', { conversation: conv, messages: msgs });
  }
  showNotification('导出成功', fname + '.' + format, 'success', 2000);
}

/** 批量导出全部会话为单个 JSON
 * @returns {Promise<void>} */
async function exportAllConversations() {
  if (!AppState.conversations.length) { showNotification('无法导出', '暂无会话', 'warning'); return; }
  const all = await DB.exportAll();
  downloadJson('moray_conversations_' + new Date().toISOString().slice(0, 10) + '.json', { version: 2, conversations: all.conversations, messages: all.messages });
  showNotification('导出成功', `共 ${all.conversations.length} 个会话`, 'success');
}

/* ---------- 聊天页头部工具 ---------- */

/** 设置聊天页头部按钮行为
 * @returns {void} */
function setupChatHeaderEvents() {
  const clearBtn = document.getElementById('clearChatBtn');
  if (clearBtn) clearBtn.addEventListener('click', () => {
    if (!AppState.activeConv || !AppState.messages.length) { showNotification('会话为空', '没有可清理的内容', 'info'); return; }
    showConfirm('清空当前会话', '将删除本会话的全部消息（保留会话本身），此操作不可恢复。', async () => {
      await DB.clearMessagesByConversation(AppState.activeConv.id);
      AppState.messages = [];
      AppState._convMsgIndex[AppState.activeConv.id] = [];
      renderMessages();
      updateTokenMeter();
      showNotification('已清空', '会话消息已全部删除', 'success');
    }, { danger: true, okText: '清空' });
  });
  const exportBtn = document.getElementById('exportChatBtn');
  if (exportBtn) exportBtn.addEventListener('click', () => exportConversation());
  const renameBtn = document.getElementById('renameChatBtn');
  if (renameBtn) renameBtn.addEventListener('click', () => {
    if (AppState.activeConv) renameConversation(AppState.activeConv.id);
  });
  const settingsBtn = document.getElementById('chatSettingsBtn');
  if (settingsBtn) settingsBtn.addEventListener('click', openChatSettings);
}

/** 会话设置弹窗（系统提示词 / 模型 / 历史清理）
 * @returns {void} */
function openChatSettings() {
  const conv = AppState.activeConv;
  if (!conv) { showNotification('提示', '请先打开一个会话', 'info'); return; }
  const models = '<option value="">（跟随默认模型 / 自动路由）</option>' + AI.models.map(m => `<option value="${escapeHtml(m.name)}" ${conv.model === m.name ? 'selected' : ''}>${escapeHtml(m.name)}</option>`).join('');
  const box = showModal(`
    <div class="form-row">
      <label class="form-label">会话模型</label>
      <select id="convModelSel" class="form-select">${models || '<option value="">（跟随默认模型）</option>'}</select>
    </div>
    <div class="form-row">
      <label class="form-label">系统提示词（本会话独立）</label>
      <textarea id="convSystemPrompt" class="form-textarea" placeholder="例如：你是一位资深 Python 工程师，回答务必附带示例代码...">${escapeHtml(conv.systemPrompt || '')}</textarea>
    </div>
    <div class="form-row">
      <div class="flex items-center justify-between">
        <div><div class="text-xs text-text-primary">上下文用量</div><div class="text-[10px] text-text-tertiary">清理较早消息可释放上下文空间</div></div>
        <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="convTrimBtn">清理较早消息</button>
      </div>
    </div>`,
    {
      title: '会话设置', icon: 'sliders-horizontal',
      footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-modal-close2>取消</button>
               <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="convSaveBtn">保存</button>`
    });
  on(box.querySelector('[data-modal-close2]'), 'click', closeModal);
  on(box.querySelector('#convSaveBtn'), 'click', async () => {
    // [路由修复] 空选项=清除手选（恢复自动路由）；具体模型=显式手选锁定
    const picked = box.querySelector('#convModelSel').value;
    conv.model = picked;
    conv.userPickedModel = !!picked;
    conv.systemPrompt = box.querySelector('#convSystemPrompt').value.trim();
    await DB.updateConversation(conv.id, { model: conv.model, userPickedModel: conv.userPickedModel, systemPrompt: conv.systemPrompt });
    closeModal();
    // [显示统一] 会话设置改模型后同步输入台/状态栏（清空选项 = 恢复自动路由）
    if (picked) setActiveModel(picked, 'user', '用户在会话设置中手动选择');
    else resetActiveModel();
    showNotification('已保存', '会话设置已更新', 'success', 1500);
  });
  on(box.querySelector('#convTrimBtn'), 'click', async () => {
    const keep = 10;
    if (AppState.messages.length <= keep) { showNotification('无需清理', '消息数未超过 ' + keep + ' 条', 'info'); return; }
    const removed = AppState.messages.splice(0, AppState.messages.length - keep);
    for (const m of removed) { await DB.deleteMessage(m.id).catch(() => {}); removeMsgFromIndex(m.id); }
    renderMessages(); updateTokenMeter();
    showNotification('已清理', `移除了较早的 ${removed.length} 条消息`, 'success');
    closeModal();
  });
}


/** 清理会话较早消息（保留最近 keep 条）
 * @param {number} keep - 保留条数
 * @returns {Promise<void>} */
async function trimOldMessages(keep) {
  const conv = AppState.activeConv;
  if (!conv || AppState.messages.length <= keep) return;
  const removed = AppState.messages.splice(0, AppState.messages.length - keep);
  for (const m of removed) { await DB.deleteMessage(m.id).catch(() => {}); removeMsgFromIndex(m.id); }
  renderMessages();
  updateTokenMeter();
  showNotification('已清理', `移除了 ${removed.length} 条较早消息`, 'success', 2000);
}

/** 广播数据变更（配合 parts/85 的多标签同步哨兵） */
if (typeof broadcastDataChange !== 'function') {
  // 占位定义，避免 parts/85 缺失时报错
  window.broadcastDataChange = function () {};
}


/* ===================== [V2 优化7] ChatGPT 对话历史导入 ===================== */

/**
 * 从 ChatGPT 导出的 conversations.json 导入对话历史。
 * 解析官方导出格式：[{title, create_time, mapping: {id: {message: {author:{role}, content:{parts}}, create_time}, parent, children}}]
 * @param {File} file - conversations.json
 * @returns {Promise<void>} */
async function importChatGPTConversations(file) {
  try {
    const data = JSON.parse(await file.text());
    if (!Array.isArray(data)) throw new Error('格式不符：应为 conversations 数组');
    let convCount = 0, msgCount = 0;
    for (const raw of data.slice(0, 200)) { // 上限保护
      if (!raw.mapping) continue;
      const conv = await DB.createConversation({
        title: raw.title || 'ChatGPT 导入',
        createdAt: raw.create_time ? raw.create_time * 1000 : Date.now(),
        model: 'chatgpt',
        userPickedModel: false
      });
      // 从叶子节点回溯到根，得到正序消息链
      let msgs = [];
      const mapping = raw.mapping;
      const nodes = Object.values(mapping).filter(n => n.message && n.message.content && n.message.content.parts);
      const withTime = nodes.map(n => n.message).filter(m => m.author && m.author.role !== 'system');
      withTime.sort((a, b) => (a.create_time || 0) - (b.create_time || 0));
      msgs = withTime.map(m => ({
        id: uid('msg'), conversationId: conv.id,
        role: m.author.role === 'assistant' ? 'assistant' : 'user',
        content: (m.content.parts || []).filter(p => typeof p === 'string').join('\n').trim(),
        createdAt: (m.create_time || 0) * 1000 || Date.now(),
        tokens: 0, feedback: 0, attachments: []
      })).filter(m => m.content);
      if (!msgs.length) { await DB.deleteConversation(conv.id); continue; }
      await DB.putMessages(msgs);
      convCount++; msgCount += msgs.length;
    }
    AppState.conversations = await DB.listConversations();
    AppState.conversations.sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));
    await rebuildMsgIndex();
    renderSessionList();
    showNotification('导入完成', `${convCount} 个会话 / ${msgCount} 条消息`, 'success', 4000);
  } catch (e) {
    console.error('[MoRay] ChatGPT import failed:', e);
    showNotification('导入失败', e.message, 'error', 3500);
  }
}

/* ===================== [Trae 化] 新建对话主按钮 + 更多下拉（复用现有能力，不新写创建逻辑） ===================== */

/** 绑定侧栏主按钮与「更多新建方式」下拉（幂等；点击外部/Esc 关闭）
 * @returns {void} */
function bindNewChatMain() {
  const main = document.getElementById('newChatMainBtn');
  const more = document.getElementById('newChatMoreBtn');
  const menu = document.getElementById('newChatMoreMenu');
  if (!main || !more || !menu || main.__traeBound) return;
  main.__traeBound = true;
  // 主按钮：与 Ctrl/N、⌘K「新建对话」走同一动作 createNewConversation
  main.addEventListener('click', () => createNewConversation());
  const closeMenu = () => { menu.style.display = 'none'; };
  more.addEventListener('click', (e) => {
    e.stopPropagation(); // 阻止冒泡到 document 立即关闭
    menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
  });
  menu.querySelectorAll('[data-new-action]').forEach(item => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const act = item.dataset.newAction;
      closeMenu();
      if (act === 'template') openConversationTemplates();
      else if (act === 'project') openGroupManager();
    });
  });
  document.addEventListener('click', (e) => {
    if (menu.style.display !== 'none' && !menu.contains(e.target) && e.target !== more && !more.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  // [Trae 化] Ctrl+N / ⌘+N：与主按钮同一动作（createNewConversation）
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'n' || e.key === 'N')) {
      e.preventDefault();
      createNewConversation();
    }
  }, true);
  // [Trae 化] ⌘K 命令面板「新建对话」与新主按钮走同一动作（包装全局 executeCommand，其余命令原样）
  if (typeof executeCommand === 'function' && !executeCommand.__traeWrapped) {
    const origExec = executeCommand;
    executeCommand = function (action) {
      if (action === 'new-chat') { createNewConversation(); return; }
      return origExec.apply(this, arguments);
    };
    executeCommand.__traeWrapped = true;
  }
}
bindNewChatMain();

// [深度思考] 输入工具栏三态按钮安装（script 位于 </body> 前，静态骨架已就绪）
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installThinkModeBtn);
else installThinkModeBtn();

// [阶段0 本机 Agent] 输入工具栏"本机工具"开关（同工具栏、同装载时机；幂等）
if (typeof installNativeAgentToggle === 'function') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installNativeAgentToggle);
  else installNativeAgentToggle();
}
