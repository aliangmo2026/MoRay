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
          <div class="session-title flex items-center gap-2 truncate">${escapeHtml(c.title || '新对话')}${c.unread ? '<span class="unread-dot"></span>' : ''}</div>
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
      html += `<div class="text-[10px] text-text-tertiary px-2 py-2 font-semibold uppercase tracking-wider">置顶</div>`;
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
      html += `<div class="text-[10px] text-text-tertiary px-2 py-2 ${g !== '置顶' ? 'mt-2' : ''} font-semibold uppercase tracking-wider">${g}</div>`;
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
  // [Day22b] 顶栏那行"模型 · 本地优先/云端"小字已下线，模型身份随模型名并入这一行显示
  const local = (typeof isLocalModelName === 'function') && isLocalModelName(m);
  label.textContent = m ? (preciseModelName(m) + ' · ' + (local ? '本地优先' : '云端')) : '选择模型';
  if (wrap) wrap.title = m ? (m + (local ? '（本地模型 · 数据不出本机）' : '（云端模型 · 请求会离开本机）')) : '未检测到模型，请先在设置中配置后端';
  // [深度思考] 与模型标签同类时机刷新三态按钮
  if (typeof refreshThinkModeBtn === 'function') refreshThinkModeBtn();
}

/* ---------- [深度思考] 三态开关（auto / on / off） ---------- */

const THINK_MODES = [
  { key: 'auto', tag: '标准', desc: '（均衡）',    title: '思考强度：标准（模型自动决定是否思考）' },
  { key: 'on',   tag: '深度', desc: '（长链推理）', title: '思考强度：深度（强制深度思考，消耗更多 Token）' },
  { key: 'off',  tag: '极速', desc: '（最快响应）', title: '思考强度：极速（关闭思考，最快响应）' }
];

/** [思考强度 Popover 滑条] 三档拖拽区间：ratio（0~1）落入区间 → 档位 + 滑块描边/label 高亮色。
 * 色彩循序渐进：极速青 → 标准蓝（品牌）→ 深度紫。 */
const THINK_ZONES = [
  { max: 0.333, key: 'off',  color: '#06b6d4' },
  { max: 0.666, key: 'auto', color: '#2f7cf6' },
  { max: 1.001, key: 'on',   color: '#a855f7' }
];
/** 松手/回位吸附锚点：三档在轨道上的归位百分比 */
const THINK_SNAP = { off: 100 / 6, auto: 50, on: 500 / 6 };

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

/** [思考强度 Popover 滑条] 按轨道位置（0~1）渲染 Popover：落入区间决定档位文案/高亮色/滑块位置
 * @param {number} ratio - 0（最左/极速）~ 1（最右/深度）
 * @returns {void} */
function renderThinkPopover(ratio) {
  const label = document.getElementById('thinkModeLabel');
  const thumb = document.getElementById('thinkModeThumb');
  const zone = THINK_ZONES.find(z => ratio < z.max) || THINK_ZONES[THINK_ZONES.length - 1];
  const conf = THINK_MODES.find(t => t.key === zone.key) || THINK_MODES[0];
  const r = Math.min(1, Math.max(0, ratio));
  if (label) { label.textContent = conf.tag + conf.desc; label.style.color = zone.color; }
  // 水位填充：流光+星尘只在滑块左侧点亮，右侧留暗轨（宽度与滑块同步涨落）
  const fill = document.getElementById('thinkModeFill');
  if (fill) fill.style.width = (r * 100).toFixed(2) + '%';
  if (thumb) {
    // 偏移=滑块半宽（32px/2=16）；color 同步档位色，霓虹光晕 box-shadow: 0 0 24px currentColor 随之变色
    thumb.style.left = 'calc(' + (r * 100).toFixed(2) + '% - 16px)';
    thumb.style.borderColor = zone.color;
    thumb.style.color = zone.color;
  }
}

/** [思考强度 Popover 滑条] 触发器按钮文字/title 同步；Popover 开着时同步其吸附位
 * @returns {void} */
function refreshThinkModeBtn() {
  const btn = document.getElementById('thinkModeBtn');
  if (!btn) return;
  const mode = MoraySettings.get('thinkMode') || 'auto';
  const conf = THINK_MODES.find(t => t.key === mode) || THINK_MODES[0];
  const tag = btn.querySelector('.think-mode-tag');
  if (tag) tag.textContent = conf.tag;
  btn.title = conf.title + (AppState.generating ? '（生成中已禁用）' : '');
  const pop = document.getElementById('thinkModePopover');
  if (pop && pop.classList.contains('open')) {
    renderThinkPopover((THINK_SNAP[mode] != null ? THINK_SNAP[mode] : THINK_SNAP.auto) / 100);
  }
}

/** [思考强度 Popover 滑条] 输入工具栏触发器按钮 + body 级悬浮滑动变阻器（纯 JS 增量，不改 HTML）：
 * 平时只占「脑图标 + 档位」小按钮；点击在按钮正上方弹出长条渐变轨道（极速青→标准蓝→深度紫），
 * Pointer Events 拖拽实时跟手（label/描边色随区间变），松手吸附生效并收起；点击外部/Esc/滚动收起。
 * popover 挂 body（fixed 按按钮 rect 定位）而非按钮内部：#coreInputContainer 有 overflow-hidden，
 * 挂内会被裁剪；样式全手写注入（Tailwind 动态类对运行时注入元素不可靠，--color-* 类另有钉死橙前科）。
 * @returns {void} */
function installThinkModeBtn() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || document.getElementById('thinkModeBtn')) return;
  // 1) 触发器按钮
  const btn = document.createElement('button');
  btn.id = 'thinkModeBtn';
  btn.className = 'input-toolbar-btn think-mode-btn';
  btn.setAttribute('aria-label', '思考强度');
  btn.innerHTML = '<i data-lucide="brain" class="w-3 h-3"></i><span class="think-mode-tag">标准</span>';
  const divider = toolbar.querySelector('.input-toolbar-divider');
  if (divider) toolbar.insertBefore(btn, divider);
  else toolbar.appendChild(btn);
  // 2) Popover 结构（body 级）
  const pop = document.createElement('div');
  pop.id = 'thinkModePopover';
  pop.innerHTML = '<div id="thinkModeLabel">标准（均衡）</div>'
    + '<div id="thinkModeTrack"><div id="thinkModeFill"></div><div id="thinkModeThumb"></div></div>'
    + '<div class="think-zone-labels"><span class="think-zone-label">极速</span>'
    + '<span class="think-zone-label">标准</span><span class="think-zone-label">深度</span></div>';
  document.body.appendChild(pop);
  // 3) 控件专属样式（运行时注入；深浅主题双档）
  if (!document.getElementById('thinkModeBtnStyle')) {
    const st = document.createElement('style');
    st.id = 'thinkModeBtnStyle';
    st.textContent = '#thinkModePopover{position:fixed;display:none;transform:translateX(-50%);width:280px;padding:20px;border-radius:16px;z-index:9999;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);background:rgba(30,30,35,.85);border:1px solid rgba(255,255,255,.15);box-shadow:0 10px 40px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.05) inset;transition:background .3s,border-color .3s,color .3s}'
      + '#thinkModePopover.open{display:block}'
      + 'html[data-theme="light"] #thinkModePopover{background:rgba(255,255,255,.85) !important;border:1px solid rgba(0,0,0,.08);box-shadow:0 10px 40px rgba(0,0,0,.1),0 0 0 1px rgba(255,255,255,.5) inset;color:#1d1d1f}'
      + 'html[data-theme="dark"] #thinkModePopover{background:rgba(30,30,35,.85) !important;border:1px solid rgba(255,255,255,.15);box-shadow:0 10px 40px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.05) inset;color:#fff}'
      + '#thinkModeLabel{font-size:12px;font-weight:600;text-align:center;margin-bottom:14px;color:var(--text-primary);transition:color .15s ease}'
      + 'html[data-theme="light"] #thinkModeLabel{color:#1d1d1f}'
      + 'html[data-theme="dark"] #thinkModeLabel{color:#fff}'
      + '#thinkModeTrack{position:relative;height:24px;border-radius:999px;cursor:pointer;touch-action:none;background:rgba(255,255,255,.12);box-shadow:inset 0 2px 4px rgba(0,0,0,.2)}'
      + 'html[data-theme="light"] #thinkModeTrack{background:rgba(0,0,0,.08)}'
      // 水位填充层：流光+星尘全挂这里，宽度=滑块比例随拖拽涨落（不加 width 过渡——拖拽中会滞后于滑块）；
      // 渐变 background-size:200% 相对 fill 自身宽，窄窗口下随动画滑动仍是流光
      + '#thinkModeFill{position:absolute;top:0;left:0;height:100%;width:0;background:linear-gradient(90deg,#06b6d4,#2f7cf6,#a855f7,#06b6d4);background-size:200% 100%;animation:energyFlow 3s linear infinite;pointer-events:none}'
      // 星尘·质数尺寸错位法：X/Y 全质数（43/67/31 × 11/17/23，53/71 × 13/19）多行平铺互不整除；
      // Y 按全高铺满修正：股2 posY 12px→20.5px 带（底）、股3 posY -8px→3.5px 带（顶）；X 位移=格距无缝
      + '#thinkModeFill::before{content:"";position:absolute;inset:0;background-image:radial-gradient(circle,rgba(255,255,255,.9) 1.5px,transparent 2px),radial-gradient(circle,rgba(255,255,255,.6) 1px,transparent 1.5px),radial-gradient(circle,rgba(255,255,255,.8) .5px,transparent 1px);background-size:43px 11px,67px 17px,31px 23px;background-position:0 0,15px 12px,7px -8px;animation:dustFloat 4s linear infinite;pointer-events:none;z-index:1}'
      + '@keyframes dustFloat{0%{background-position:0 0,15px 12px,7px -8px}100%{background-position:-43px 0,-52px 12px,-24px -8px}}'
      + '#thinkModeFill::after{content:"";position:absolute;inset:0;background-image:radial-gradient(circle,rgba(255,255,255,.7) .5px,transparent 1px),radial-gradient(circle,rgba(255,255,255,.9) 1px,transparent 1.5px);background-size:53px 13px,71px 19px;background-position:3px 8px,20px 1px;animation:dustFloatFast 2.5s linear infinite;pointer-events:none;z-index:1}'
      + '@keyframes dustFloatFast{0%{background-position:3px 8px,20px 1px}100%{background-position:-50px 8px,-51px 1px}}'
      + '@keyframes energyFlow{0%{background-position:100% 0}100%{background-position:-100% 0}}'
      + '#thinkModeThumb{position:absolute;top:50%;transform:translateY(-50%);width:32px;height:32px;border-radius:50%;background:#ffffff;border:2px solid rgba(255,255,255,.9);color:#2f7cf6;box-shadow:0 0 12px rgba(255,255,255,.8),0 0 24px currentColor;transition:left .15s ease-out,border-color .3s ease,color .3s ease;z-index:2}'
      + '#thinkModeThumb.is-dragging{transition:border-color .3s ease,color .3s ease}'
      + '.think-zone-labels{display:flex;justify-content:space-between;font-size:10px;margin-top:12px}'
      + '.think-zone-label{color:#888}'
      + 'html[data-theme="dark"] .think-zone-label{color:rgba(255,255,255,.5)}'
      + '.think-mode-btn{display:inline-flex;align-items:center;gap:4px;width:auto;padding:3px 10px;border-radius:9999px;color:var(--text-tertiary);transition:background .15s ease,color .15s ease}'
      + '.think-mode-btn:hover{background:var(--brand-light);color:var(--brand-text)}'
      + '.think-mode-btn .think-mode-tag{font-size:11px;font-weight:600;line-height:1}';
    document.head.appendChild(st);
  }
  // 4) 拖拽（Pointer Events + capture：移出轨道仍跟手）
  const track = pop.querySelector('#thinkModeTrack');
  const thumb = pop.querySelector('#thinkModeThumb');
  const snapOf = () => (THINK_SNAP[MoraySettings.get('thinkMode') || 'auto'] != null ? THINK_SNAP[MoraySettings.get('thinkMode') || 'auto'] : THINK_SNAP.auto) / 100;
  const ratioFrom = (e) => {
    const r = track.getBoundingClientRect();
    return r.width ? Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) : 0.5;
  };
  let dragging = false;
  const closeThinkPopover = () => { pop.classList.remove('open'); };
  on(track, 'pointerdown', (e) => {
    if (AppState.generating) {
      showNotification('思考强度', 'AI 正在回复中，请在生成结束后切换', 'warning', 1600);
      return;
    }
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch (err) { /* 捕获失败仍可拖（事件未出 track 时） */ }
    thumb.classList.add('is-dragging');
    renderThinkPopover(ratioFrom(e));
    e.preventDefault();
  });
  on(track, 'pointermove', (e) => { if (dragging) renderThinkPopover(ratioFrom(e)); });
  const endDrag = (e, commit) => {
    if (!dragging) return;
    dragging = false;
    thumb.classList.remove('is-dragging');
    if (!commit) { renderThinkPopover(snapOf()); return; } // pointercancel：回到已存档位
    const zone = THINK_ZONES.find(z => ratioFrom(e) < z.max) || THINK_ZONES[THINK_ZONES.length - 1];
    MoraySettings.set('thinkMode', zone.key);
    refreshThinkModeBtn();
    closeThinkPopover();
  };
  on(track, 'pointerup', (e) => endDrag(e, true));
  on(track, 'pointercancel', (e) => endDrag(e, false));
  // 5) 开关与收起：按钮 toggle；点击外部/Esc/滚动/缩放收起
  on(btn, 'click', (e) => {
    e.stopPropagation();
    // [弹窗互斥] 模型下拉开着先强关：模型触发器的 stopPropagation 会阻断其 document closer，
    // 不显式 remove 就会与本 Popover 重叠
    const mp = document.getElementById('modelPickerPop');
    if (mp) mp.remove();
    if (pop.classList.contains('open')) { closeThinkPopover(); return; }
    const rect = btn.getBoundingClientRect();
    pop.classList.add('open');
    pop.style.left = (rect.left + rect.width / 2) + 'px';
    pop.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
    renderThinkPopover(snapOf());
  });
  on(document, 'click', (e) => {
    if (!pop.classList.contains('open')) return;
    // composedPath 判归属（Lucide 重渲染 detach e.target 的老坑）
    const path = (e.composedPath && e.composedPath()) || [];
    if (path.indexOf(pop) !== -1 || path.indexOf(btn) !== -1) return;
    closeThinkPopover();
  });
  on(document, 'keydown', (e) => { if (e.key === 'Escape') closeThinkPopover(); });
  on(window, 'resize', closeThinkPopover);
  on(document, 'scroll', closeThinkPopover, true);
  refreshIcons();
  refreshThinkModeBtn();
}

/** [多引擎切换 2026-10-21] 输入工具栏"执行引擎"选择器（对齐深度思考按钮模式）：
 * 启动时 GET /api/engines/list 动态拉取已注册引擎（local/zcode/laya…，后端注册即前端可见，
 * 零写死），点击弹出 Popover 列表；选中存 localStorage 'moray_engine'（默认 zcode），
 * 92 的任务派发据此拼 /api/engines/<name>/tasks 通用路由。
 * 样式手写注入（Tailwind 动态类对运行时注入元素不可靠，thinkModeBtn 同款教训）；
 * Popover 挂 body（#coreInputContainer overflow-hidden 会裁剪）。弹窗互斥：开本弹窗强拆
 * modelPickerPop+thinkModePopover（thinkMode 强拆 modelPickerPop 同款）；外点收起走
 * capture 阶段 mousedown——模型/思考触发器的 click stopPropagation 会阻断 document click
 * closer，capture 先行即免动 110/30 既有闭合逻辑。引擎名过 URL 段白名单后才拼进 fetch
 * （防 localStorage 被塞 ../ 或 :// 注入串进请求路径）；列表渲染前 escapeHtml。
 * @returns {void} */
function installEngineSelector() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || document.getElementById('engineSelector')) return;
  const ENGINE_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;
  const STORE_KEY = 'moray_engine';
  const savedName = () => {
    var n = '';
    try { n = String(localStorage.getItem(STORE_KEY) || ''); } catch (e) { /* 隐私模式 */ }
    return ENGINE_NAME_RE.test(n) ? n : 'zcode';
  };
  const displayName = (n) => ({ zcode: 'ZCode', laya: 'Laya', local: 'Local' })[n]
    || (String(n).charAt(0).toUpperCase() + String(n).slice(1));
  const esc = (typeof escapeHtml === 'function') ? escapeHtml : (s) => String(s)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let engineList = [];   // 启动预取存内存；每次开弹窗复检（后端可能晚于页面启动上线/热插拔）
  // 后端基址与 89 的 streamBase / 92 的 backendBase 同链（跨 part 不共享闭包，各持一份小解析）
  const backendOrigin = () => {
    var trim = (s) => String(s).replace(/\/+$/, '');
    var base = '';
    try { var mb = window.MorayBackend; if (mb && mb.origin) base = trim(mb.origin); } catch (e) { /* 下一级 */ }
    if (!base && typeof window.resolveBackendOrigin === 'function') {
      try { base = trim(window.resolveBackendOrigin()); } catch (e) { /* 忽略 */ }
    }
    if (!base && typeof agentBackendBase === 'function') {
      try { base = trim(agentBackendBase()); } catch (e) { /* 忽略 */ }
    }
    if (!base) base = 'http://127.0.0.1:8000';
    try { if (trim(location.origin) === base) return ''; } catch (e) { /* file:// 等 */ }
    return base;
  };
  // 1) 触发器按钮（icon + 当前引擎名）
  const btn = document.createElement('button');
  btn.id = 'engineSelector';
  btn.className = 'input-toolbar-btn engine-selector-btn';
  btn.setAttribute('aria-label', '执行引擎');
  btn.innerHTML = '<i data-lucide="server" class="w-3.5 h-3.5"></i><span id="engineName" class="engine-name-tag"></span>';
  const divider = toolbar.querySelector('.input-toolbar-divider');
  if (divider) toolbar.insertBefore(btn, divider);
  else toolbar.appendChild(btn);
  // 2) Popover（body 级）
  const pop = document.createElement('div');
  pop.id = 'enginePopover';
  document.body.appendChild(pop);
  // 3) 控件专属样式（运行时注入；深浅主题双档）
  if (!document.getElementById('engineSelectorStyle')) {
    const st = document.createElement('style');
    st.id = 'engineSelectorStyle';
    st.textContent = '#enginePopover{position:fixed;display:none;transform:translateX(-50%);width:260px;padding:8px;border-radius:16px;z-index:9999;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);background:rgba(30,30,35,.85);border:1px solid rgba(255,255,255,.15);box-shadow:0 10px 40px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.05) inset;max-height:280px;overflow-y:auto}'
      + '#enginePopover.open{display:block}'
      + 'html[data-theme="light"] #enginePopover{background:rgba(255,255,255,.85) !important;border:1px solid rgba(0,0,0,.08);box-shadow:0 10px 40px rgba(0,0,0,.1),0 0 0 1px rgba(255,255,255,.5) inset;color:#1d1d1f}'
      + 'html[data-theme="dark"] #enginePopover{background:rgba(30,30,35,.85) !important;border:1px solid rgba(255,255,255,.15);box-shadow:0 10px 40px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,.05) inset;color:#fff}'
      + '.engine-pop-item{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:10px;cursor:pointer;transition:background .15s ease}'
      + '.engine-pop-item:hover{background:var(--brand-light);color:var(--brand-text)}'
      + '.engine-pop-item.selected{background:var(--brand-light);color:var(--brand-text)}'
      + '.engine-pop-name{font-size:12px;font-weight:600;line-height:1.2}'
      + '.engine-pop-desc{font-size:10px;line-height:1.3;color:var(--text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
      + '.engine-pop-item.selected .engine-pop-desc{color:inherit}'
      + '.engine-pop-empty{font-size:11px;color:var(--text-tertiary);padding:8px 10px;text-align:center}'
      + '.engine-selector-btn{display:inline-flex;align-items:center;gap:4px;width:auto;padding:3px 10px;border-radius:9999px;color:var(--text-tertiary);transition:background .15s ease,color .15s ease}'
      + '.engine-selector-btn:hover{background:var(--brand-light);color:var(--brand-text)}'
      + '.engine-selector-btn .engine-name-tag{font-size:11px;font-weight:600;line-height:1}';
    document.head.appendChild(st);
  }
  // 4) 列表渲染与标签同步
  const renderList = () => {
    const cur = savedName();
    if (!engineList.length) {
      pop.innerHTML = '<div class="engine-pop-empty">暂无可用引擎（后端未返回）</div>';
      return;
    }
    pop.innerHTML = engineList.map((e) => {
      const n = String((e && e.name) || '');
      const desc = String((e && e.description) || '');
      return '<div class="engine-pop-item' + (n === cur ? ' selected' : '') + '" data-engine-name="' + esc(n) + '">'
        + '<div style="min-width:0"><div class="engine-pop-name">' + esc(displayName(n)) + '</div>'
        + '<div class="engine-pop-desc" title="' + esc(desc) + '">' + esc(desc) + '</div></div></div>';
    }).join('');
  };
  const refreshLabel = () => {
    const el = document.getElementById('engineName');
    if (el) el.textContent = displayName(savedName());
  };
  const loadEngines = async () => {
    try {
      const resp = await fetch(backendOrigin() + '/api/engines/list');
      const j = await resp.json();
      const arr = (j && j.engines) || [];
      engineList = arr.filter((e) => e && ENGINE_NAME_RE.test(String(e.name || '')));
    } catch (e) { /* 后端离线：保留旧列表，开弹窗时如实显示空 */ }
    refreshLabel();
    renderList();
  };
  // 5) 开合 + 弹窗互斥 + 外点/Esc/滚动收起
  const closeEnginePop = () => { pop.classList.remove('open'); };
  on(btn, 'click', (e) => {
    e.stopPropagation();
    // [弹窗互斥] 模型下拉/思考强度开着先强关：其触发器 stopPropagation 会阻断各自
    // document closer，不显式关就会与本 Popover 重叠（thinkMode 强拆 modelPickerPop 同款）
    const mp = document.getElementById('modelPickerPop');
    if (mp) mp.remove();
    const tp = document.getElementById('thinkModePopover');
    if (tp) tp.classList.remove('open');
    if (pop.classList.contains('open')) { closeEnginePop(); return; }
    loadEngines();   // 开弹窗顺手复检，列表鲜活（内存缓存兜底）
    const rect = btn.getBoundingClientRect();
    pop.classList.add('open');
    pop.style.left = (rect.left + rect.width / 2) + 'px';
    pop.style.bottom = (window.innerHeight - rect.top + 10) + 'px';
  });
  on(pop, 'click', (e) => {
    // composedPath 判归属（Lucide 重渲染 detach e.target 的老坑）
    const path = (e.composedPath && e.composedPath()) || [];
    const item = path.find((n) => n && n.dataset && n.dataset.engineName);
    if (!item) return;
    const name = String(item.dataset.engineName || '');
    if (!ENGINE_NAME_RE.test(name)) return;
    try { localStorage.setItem(STORE_KEY, name); } catch (eS) { /* 隐私模式：仅当次生效 */ }
    refreshLabel();
    renderList();
    closeEnginePop();
    showNotification('执行引擎已切换', displayName(name), 'success', 1600);
  });
  // 外点收起：capture mousedown 先行——模型/思考触发器的 click stopPropagation 拦不住它
  on(document, 'mousedown', (e) => {
    if (!pop.classList.contains('open')) return;
    const path = (e.composedPath && e.composedPath()) || [];
    if (path.indexOf(pop) !== -1 || path.indexOf(btn) !== -1) return;
    closeEnginePop();
  }, true);
  on(document, 'keydown', (e) => { if (e.key === 'Escape') closeEnginePop(); });
  on(window, 'resize', closeEnginePop);
  on(document, 'scroll', (e) => {
    // Popover 自身可滚（max-height 限高）：滚动冒到 document 也不收起
    const path = (e.composedPath && e.composedPath()) || [];
    if (path.indexOf(pop) !== -1) return;
    closeEnginePop();
  }, true);
  refreshLabel();
  loadEngines();   // 启动预取，列表存内存
  refreshIcons();
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
    st.textContent = '.native-agent-btn{position:relative}.native-agent-btn .native-agent-tag{font-size:10px;font-weight:800;line-height:1;position:absolute;bottom:1px;right:2px;letter-spacing:.5px;display:none}.native-agent-btn.native-on{background:rgba(229,115,74,.15);color:#E5734A;box-shadow:inset 0 0 0 1px rgba(229,115,74,.35)}.native-agent-btn.native-on .native-agent-tag{display:inline}';
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
          '<div class="flex items-center gap-2 text-text-primary font-medium"><i data-lucide="shield-check" class="w-4 h-4 text-brand-violet"></i>本机 Agent 默认关闭是为了安全</div>' +
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
    banner.className = 'native-agent-banner text-[10px] text-text-tertiary flex items-center gap-2 px-3 py-2 mx-3 mt-2 rounded-lg border border-line-ghost/50 bg-surface-panel/50';
    const anchor = content.querySelector('#chatMessagesNormal');
    content.insertBefore(banner, anchor || content.firstChild);
  }
  const ok = !!cfg;
  banner.innerHTML = ok
    ? '<span class="beacon-dot" style="width:6px;height:6px;background:var(--color-success)"></span>本机工具已开启 · 工作区 <span class="font-mono text-text-secondary">' + escapeHtml(cfg.workspace || '') + '</span><span class="text-text-tertiary">（写文件与只读命令需你授权）</span>' +
      '<span class="ml-auto flex items-center gap-2 shrink-0">' +
      '<button data-agent-selfcheck-btn class="text-[10px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="检测当前模型能否触发工具调用"><i data-lucide="shield-check" class="w-3 h-3"></i>自检</button>' +
      '<button data-ws-sample-btn class="text-[10px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="载入示例工作区（notes/a.txt、notes/b.txt、todo.md，不覆盖已有文件）"><i data-lucide="sparkles" class="w-3 h-3"></i>示例</button>' +
      '<button data-ws-tree-btn class="text-[10px] px-2 py-1 rounded-md border border-line-ghost text-text-secondary hover:text-brand-cyan hover:bg-surface-panel flex items-center gap-1" title="查看工作区文件树（只读）"><i data-lucide="folder-tree" class="w-3 h-3"></i>文件树</button>' +
      '</span>'
    : '<span class="beacon-dot" style="width:6px;height:6px;background:var(--color-warning)"></span>本机工具已开启，但本地后端离线 —— ' + escapeHtml(agentOfflineReason()) +
      '<span class="ml-auto flex items-center gap-2 shrink-0">' +
      '<button data-agent-selfcheck-btn class="agent-offline text-[10px] px-2 py-1 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="shield-check" class="w-3 h-3"></i>自检</button>' +
      '<button data-ws-sample-btn class="agent-offline text-[10px] px-2 py-1 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="sparkles" class="w-3 h-3"></i>示例</button>' +
      '<button data-ws-tree-btn class="agent-offline text-[10px] px-2 py-1 rounded-md border border-line-ghost/40 text-text-tertiary opacity-50 flex items-center gap-1" title="' + escapeHtml(agentOfflineReason()) + '"><i data-lucide="folder-tree" class="w-3 h-3"></i>文件树</button>' +
      '</span>';
  // [阶段1.6 M3] 当前模型自检未通过 → 输入台上方一行温和提示（不打断输入）
  const selfCheck = MoraySettings.get('agentSelfCheck');
  const curModel = (typeof activeModelName === 'function') ? activeModelName() : '';
  if (ok && selfCheck && selfCheck.ok === false && curModel && selfCheck.model === curModel) {
    banner.innerHTML += '<div class="w-full text-[10px] text-warning mt-1 flex items-center gap-2">' +
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

/** [删除修复] 按列表可见顺序排列会话（与 renderSessionList 排序规则一致：
 * 排除归档 → 置顶优先 → 手动 order / 更新时间倒序）。返回新数组，不改动 AppState。
 * @returns {Array} 排序后的会话列表 */
function visibleSessionOrder() {
  return AppState.conversations
    .filter(c => !c.archived)
    .sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));
}

/** [删除修复] 从持久层真正删除会话记录（IndexedDB 会话 + 其全部消息；后端同步由 DB 包装层负责）
 * 失败重试一次；仍失败如实提示，不回滚已完成的列表移除。
 * @param {string} convId - 会话ID
 * @returns {Promise<boolean>} 是否删除成功 */
async function purgeConversationRecord(convId) {
  if (!convId) return false;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await DB.deleteConversation(convId);
      return true;
    } catch (e) {
      if (attempt === 1) {
        console.error('[MoRay] 删除会话持久化失败:', convId, e);
        showNotification('删除未完成', '本地记录删除失败，刷新后该会话可能再次出现', 'error', 3500);
      }
    }
  }
  return false;
}

/** 删除会话（带确认）——先摘内存并立即重绘（会话马上从列表消失），再落持久层，最后按需切换/新建
 * @param {string} convId - 会话ID
 * @returns {Promise<void>} */
async function deleteConversationById(convId) {
  const idx = AppState.conversations.findIndex(c => c.id === convId);
  if (idx < 0) return;
  const conv = AppState.conversations[idx];
  showConfirm('删除会话', `确定删除会话「${conv.title || '新对话'}」？该会话的全部消息将一并删除。`, async () => {
    const wasActive = !!(AppState.activeConv && AppState.activeConv.id === convId);
    // 1) 先从数据源摘除并重绘：不等写库结果，被删会话必须立刻从左侧列表消失
    AppState.conversations.splice(idx, 1);
    if (AppState._convMsgIndex) delete AppState._convMsgIndex[convId];
    if (wasActive) {
      // 不再停留在已删除的会话上下文里
      AppState.activeConv = null;
      AppState.messages = [];
      renderMessages();
    }
    renderSessionList();
    // 2) 从 IndexedDB 真正删掉这条记录（含该会话的全部消息）
    await purgeConversationRecord(convId);
    // 3) 删的是当前会话 → 切到列表里第一个其他会话；列表已空则新建空白对话
    if (wasActive) {
      const next = visibleSessionOrder()[0];
      if (next) await openConversation(next.id);
      else await createNewConversation();
    }
    // 下次启动的恢复目标不得再指向已删除的会话
    if (MoraySettings.get('activeConversationId') === convId) {
      try {
        const p = MoraySettings.set('activeConversationId', (AppState.activeConv && AppState.activeConv.id) || '');
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } catch (e) { /* 忽略：仅影响下次启动恢复哪个会话 */ }
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
  item.innerHTML = `<input type="text" value="${escapeHtml(old)}" style="width:100%;background:var(--color-surface-card);border:1px solid rgba(229, 115, 74,0.5);border-radius:6px;padding:2px 8px;font-size:12px;color:var(--color-text-primary);outline:none">`;
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
 * [Day22a] 分两级：主操作（复制 / 重新生成 / 删除）常显，其余 8 项收进 .msg-actions-more ——
 *   悬停操作栏时由 135 §22.4 横向展开。**不删按钮**：这 8 项在对话页的唯一入口就是这里
 *   （右键菜单只覆盖 copy/edit/regenerate/branch/delete），删掉即功能回归。
 *   按钮顺序只影响呈现，事件委托与 handleMsgAction 都按 data-op 派发，与顺序无关。
 * @param {Object} msg - 消息对象
 * @returns {string} HTML */
function msgActionsHtml(msg) {
  return `<div class="msg-actions">
    <div class="msg-actions-main">
      <button class="msg-action-btn" data-op="copy" title="复制"><i data-lucide="copy" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="regenerate" title="重新生成"><i data-lucide="refresh-cw" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="delete" title="删除"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
    </div>
    <div class="msg-actions-more">
      <button class="msg-action-btn" data-op="saveSnippet" title="保存为片段"><i data-lucide="bookmark" class="w-4 h-4"></i></button>
      <button class="msg-action-btn ${msg.starred ? 'active' : ''}" data-op="star" title="收藏"><i data-lucide="star" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="quote" title="引用回复"><i data-lucide="reply" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="forward" title="转发到新会话"><i data-lucide="forward" class="w-4 h-4"></i></button>
      <button class="msg-action-btn ${msg.feedback === 1 ? 'active' : ''}" data-op="like" title="点赞"><i data-lucide="thumbs-up" class="w-4 h-4"></i></button>
      <button class="msg-action-btn ${msg.feedback === -1 ? 'active' : ''}" data-op="dislike" title="点踩"><i data-lucide="thumbs-down" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="branch" title="从此分支"><i data-lucide="git-branch" class="w-4 h-4"></i></button>
      <button class="msg-action-btn" data-op="edit" title="编辑重发"><i data-lucide="edit-3" class="w-4 h-4"></i></button>
    </div>
  </div>`;
}

/** [U-6-1] ZCode 执行卡的降级摘要（92 缺位/渲染异常时，buildMessageEl 用它兜底） */
function zcodeExecFallbackText(msg) {
  const ex = (msg && msg.execution) || {};
  const stMap = { running: '执行中', success: '已完成', failed: '失败' };
  const parts = ['ZCode 任务 ' + (stMap[ex.status] || ex.status || '未知')];
  if (ex.taskId) parts.push('task ' + String(ex.taskId).slice(0, 8));
  if (Array.isArray(ex.changedFiles) && ex.changedFiles.length) parts.push(ex.changedFiles.length + ' 个文件已更改');
  if (ex.error) parts.push(String(ex.error).slice(0, 80));
  return parts.join(' · ');
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

  // [U-6-1] ZCode 执行卡：kind='zcode-execution' 的消息渲染为对话流内执行卡片。
  // 状态全部来自 msg.execution（IndexedDB 持久化），重建路径零闭包依赖；渲染器由 92 提供
  // （window.morayRenderExecutionCard，内部自带直播重绑与快照降级），缺失/异常时退化为文本摘要。
  // 老消息（无 kind 字段）不受影响，走原有 user/assistant 分支。
  if (msg.kind === 'zcode-execution') {
    wrap.innerHTML = `
      <div class="msg-avatar" aria-hidden="true"><i data-lucide="terminal" class="w-3.5 h-3.5"></i></div>
      <div class="max-w-[85%] group msg-ai-body">
        <div class="flex items-center gap-2 mb-1 flex-wrap">
          <span class="beacon-dot"></span>
          <span class="text-[10px] text-text-tertiary msg-meta-line">ZCode 执行 · ${time}</span>
        </div>
        <div class="rounded-xl px-4 py-3 bg-surface-card border border-line-ghost/60 msg-bubble__ai-box">
          <div class="zcode-exec-host"></div>
          ${msgActionsHtml(msg)}
        </div>
      </div>`;
    const execHost = wrap.querySelector('.zcode-exec-host');
    let rendered = false;
    if (typeof window.morayRenderExecutionCard === 'function') {
      try { window.morayRenderExecutionCard(msg, wrap); rendered = true; }
      catch (e) { console.warn('[MoRay] 执行卡渲染失败:', e); }
    }
    if (!rendered) execHost.textContent = zcodeExecFallbackText(msg);
    refreshIcons();
    return wrap;
  }

  if (msg.role === 'user') {
    wrap.innerHTML = `
      <div class="max-w-[75%]">
        <div class="text-[10px] text-text-tertiary text-right mb-1">你 · ${time}</div>
        <div class="rounded-xl px-4 py-3 bg-brand-cobalt/14 border border-brand-cobalt/20 msg-bubble__user-box">
          <div class="md-body user-msg-content"></div>
          ${(msg.attachments && msg.attachments.length) ? `<div class="flex gap-2 flex-wrap mt-2">${msg.attachments.map(a => `<img src="${a.dataUrl}" alt="${escapeHtml(a.name || '图片')}" class="w-20 h-20 object-cover rounded-lg border border-line-ghost">`).join('')}</div>` : ''}
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
      <div class="msg-avatar" aria-hidden="true"><i data-lucide="sparkles" class="w-3.5 h-3.5"></i></div>
      <div class="max-w-[85%] group msg-ai-body">
        <div class="flex items-center gap-2 mb-1 flex-wrap">
          <span class="beacon-dot"></span>
          <!-- [Day22a] 信息行 11px 由 135 §22.2 覆盖 .msg-meta-line 达成；类名 text-[10px] 必须保留 ——
               finalizeAssistantUI 以 el.querySelector('.text-\\[10px\\]') 取头部节点回写模型名/时间，换名即静默失效。 -->
          <span class="text-[10px] text-text-tertiary msg-meta-line">${escapeHtml(preciseModelName(msg.model))} · ${time}</span>
          ${typeof renderCostPills === 'function' ? renderCostPills(msg) : ''}
        </div>
        ${(typeof renderToolSteps === 'function') ? renderToolSteps(msg.toolCalls) : ''}
        ${(typeof PlanTracker !== 'undefined' && msg.plan) ? PlanTracker.renderStatic(msg.plan) : ''}
        <div class="rounded-xl px-4 py-3 bg-surface-card border border-line-ghost/60 msg-bubble__ai-box">
          <div class="ai-msg-inner"></div>
          ${msgActionsHtml(msg)}
          ${msg.stats ? `<div class="text-[10px] text-text-tertiary mt-2 flex items-center gap-2">
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

/** [路由透明化] 气泡底部“实际模型 / 路由原因”小字（纯展示，不参与任何路由与请求决策）
 * 实际模型：优先取接口返回的 servedModel（后端做模型名兜底后服务端真正应答的模型），
 *   接口未透出该字段时退回本次请求模型，两者都没有才显示“未知模型”。
 * 路由原因：接口未定义 route_reason 字段，这里由两处**已有**信息拼装，不要求后端新增字段 ——
 *   ① 请求模型 ≠ 实际应答模型（服务端模型名兜底/归一，前端凭响应即可判定，恒显示）；
 *   ② 前端路由链路 msg.routeReason（选型与失败切换轨迹，默认折叠为“路由详情”胶囊，
 *      routingDebug 打开时平铺，沿用既有行为）。
 * @param {Object} msg - 消息对象
 * @returns {string} HTML 片段 */
function routeDetailHtml(msg) {
  const requested = String((msg && msg.model) || '').trim();
  const served = String((msg && msg.servedModel) || '').trim();
  const trace = String((msg && msg.routeReason) || '').trim();
  const actual = served || requested;
  const backendNote = (served && requested && served !== requested)
    ? '服务端模型名兜底/归一（请求 ' + requested + ' → 实际 ' + served + '）'
    : '';
  let html = '<div class="route-reason-note mt-2 flex items-center gap-1 flex-wrap text-[10px] text-text-tertiary">'
    + '<i data-lucide="route" class="w-3 h-3"></i>'
    + '<span>实际模型：' + escapeHtml(actual ? preciseModelName(actual) : '未知模型') + '</span>';
  if (backendNote) html += '<span>· 路由原因：' + escapeHtml(backendNote) + '</span>';
  if (trace && MoraySettings.get('routingDebug')) html += '<span>· 路由原因：' + escapeHtml(trace) + '</span>';
  else if (trace) {
    html += '<details style="display:inline-block"><summary style="cursor:pointer;font-size:10px;color:var(--color-text-tertiary);border:1px solid var(--color-line-ghost);border-radius:999px;padding:1px 8px">路由详情</summary>'
      + '<div style="font-size:10px;color:var(--color-text-tertiary);margin-top:4px">' + escapeHtml(trace) + '</div></details>';
  }
  return html + '</div>';
}

/** [M5-P2 2026-10-06] 气泡底部"记忆召回"胶囊（纯展示，不参与任何召回与请求决策）：
 * 与 routeDetailHtml 的"路由详情"并列的独立 details 胶囊，同在 !streaming 时渲染。
 * 展开列出本次发送时 /api/memory/recall 的命中明细，回答三问——
 *   反哺了哪几条：排名 ≤ injectedCount 标"已注入"（进了 system prompt 的【长期记忆】块）；
 *   从哪几条原料来：tags 内 basis 原料 memory_id（反思回写时埋入，32 位 hex）；
 *   为什么被选中：排名 + score（后端 bm25×时间衰减×weight，score DESC 即排名序）。
 * 数据源 msg.memoryRecall = {items, injectedCount}（buildRequestMessages 召回后挂上，
 * persistMessage 整对象落库，历史消息重载可复现）。无召回数据（总闸关/未命中/旧消息）返回 ''。
 * @param {Object} msg - 消息对象
 * @returns {string} HTML 片段 */
function memoryRecallHtml(msg) {
  const rec = msg && msg.memoryRecall;
  if (!rec || !Array.isArray(rec.items) || !rec.items.length) return '';
  const injected = Math.max(0, parseInt(rec.injectedCount, 10) || 0);
  const isBasis = (t) => /^[0-9a-f]{32}$/i.test(String(t || ''));
  const pill = 'display:inline-block;margin:1px 4px 1px 0;padding:0 6px;border:1px solid var(--color-line-ghost);border-radius:999px;font-size:10px;color:var(--color-text-tertiary)';
  const rows = rec.items.map(function (it, i) {
    const rank = i + 1;
    const tags = Array.isArray(it.tags) ? it.tags : [];
    const plain = tags.filter(function (t) { return t && !isBasis(t); });
    const basis = tags.filter(isBasis);
    let row = '<div style="margin-top:6px;padding-top:6px;border-top:1px dashed var(--color-line-ghost)">'
      + '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:0">'
      + '<span style="' + pill + '">#' + rank + (rank <= injected ? ' · 已注入' : ' · 未注入') + '</span>'
      + '<span style="' + pill + '">score ' + escapeHtml(String(it.score)) + '</span>'
      + '<span style="' + pill + '">' + escapeHtml(String(it.kind || '-')) + ' / ' + escapeHtml(String(it.source || '-')) + '</span>'
      + plain.map(function (t) { return '<span style="' + pill + '">' + escapeHtml(t) + '</span>'; }).join('')
      + '</div>'
      + '<div style="margin-top:3px">' + escapeHtml(String(it.content || '').slice(0, 80)) + (String(it.content || '').length > 80 ? '…' : '') + '</div>';
    if (basis.length) {
      row += '<div style="margin-top:3px">原料 memory_id：' + basis.map(function (u) {
        return '<span style="font-family:ui-monospace,monospace;font-size:10px;color:var(--color-text-tertiary)">' + escapeHtml(u) + '</span>';
      }).join('、') + '</div>';
    }
    return row + '</div>';
  }).join('');
  return '<details class="mt-2" style="display:inline-block;max-width:100%">'
    + '<summary style="cursor:pointer;font-size:10px;color:var(--color-text-tertiary);border:1px solid var(--color-line-ghost);border-radius:999px;padding:1px 8px">'
    + '<i data-lucide="brain" class="w-3 h-3 inline"></i>记忆召回 ' + rec.items.length + ' 条 · 注入 ' + injected + '</summary>'
    + '<div style="font-size:10px;color:var(--color-text-tertiary);margin-top:4px;max-width:560px">' + rows + '</div>'
    + '</details>';
}

/** [2026-10-01 U-6-3] 裸 JSON 防御：识别"模型把工具调用 JSON 当聊天正文返回"的形态
 * （典型：判定专用模型 moray-laya-final 输出 {"name":"detective_research","arguments":{...}}）。
 * 规则按 U-6-3 任务书：正文以 {"name"/"tool_call"/"tool_calls"/"function": 开头 且全文 <1000 字符。
 * @param {string} content - 消息正文
 * @returns {boolean} 是否命中（命中则 fillAssistantBody 渲染友好提示而非裸 JSON） */
const BARE_TOOL_CALL_RE = /^\s*\{\s*"(?:name|tool_call|tool_calls|function)"\s*:/;
function isBareToolCallJson(content) {
  const s = String(content || '');
  return s.length > 0 && s.length < 1000 && BARE_TOOL_CALL_RE.test(s);
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
      + `<div class="rag-source-annot text-[11px] text-text-secondary mt-2" data-rag-annot>${msg.citations.map(ann).join(' ')}</div>`
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
    html += `<div class="mt-2 flex items-center gap-1 text-[10px] text-text-tertiary think-note"><i data-lucide="sparkles" class="w-3 h-3"></i>复杂任务 · 已自动开启深度思考${route}</div>`;
  }
  // [路由透明化] 气泡最底部：实际命中模型 / 路由原因（流式期间不显示，生成完成后才出现）
  if (!streaming) html += routeDetailHtml(msg);
  // [M5-P2 2026-10-06] 记忆召回胶囊：与"路由详情"并列的独立 details（流式期间不显示，时序一致）
  if (!streaming) html += memoryRecallHtml(msg);
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

  // [Day24.13] Argus 模式：/exit 退出（放在一切发送守卫之前——未配模型也能退出模式）
  if (window.MorayArgusMode && window.MorayArgusMode.isActive() && /^\/exit\s*$/i.test(raw)) {
    if (input) { input.value = ''; input.style.height = 'auto'; }
    window.MorayArgusMode.exit('slash');
    return;
  }

  // [阶段3b] /订阅 命令：登记 Argus 周期复查订阅（走 8010 链路，与主 LLM 无关）。
  // 刻意排在模型/后端检查之前：没配模型也能订阅；也排在 Argus 模式
  // launchResearch 之前（那在函数更下方），研究模式下 /订阅 依然可用。
  // 只传 question：user_id/interval_hours 由后端定死（单用户、24h MVP）。
  if (raw === '/订阅' || raw.startsWith('/订阅 ')) {
    const question = raw.slice(4).trim();
    if (input) { input.value = ''; input.style.height = 'auto'; }
    if (!question) {
      showNotification('用法：/订阅', '例如「/订阅 帮我盯着 XXX 的价格」，默认每 24 小时复查一次', 'warning', 6000);
      return;
    }
    // file:// 直开时相对 fetch 无效，一律走 MorayBackend.origin
    const subBase = (window.MorayBackend && window.MorayBackend.origin) || '';
    try {
      const resp = await fetch(subBase + '/api/argus/subscriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question })
      });
      const j = await resp.json().catch(() => null);
      // showNotification 走 innerHTML，问题文本回显前转义尖括号
      const qEcho = question.slice(0, 30).replace(/[<>&]/g, '·') + (question.length > 30 ? '…' : '');
      if (j && j.ok) {
        showNotification('✅ 已订阅', '「' + qEcho + '」每 24 小时复查一次，异动会推送到「🔔 Argus 订阅通知」对话', 'success', 5000);
      } else {
        showNotification('❌ 订阅失败', (j && (j.message || j.code)) || ('后端返回 ' + resp.status), 'error', 6000);
      }
    } catch (e) {
      showNotification('❌ 订阅失败', (e && e.message) || '无法发出请求（MoRay 后端是否在运行？）', 'error', 6000);
    }
    return;
  }

  // [开源友好] 发送前拦截：未检测到任何模型后端（无本地 Ollama、无云端 Key）
  // 不再把请求发出去换一句"没有可用的 AI 后端"，而是给一句可执行的引导并落到设置页；
  // 输入框内容保留，用户配好模型后可直接重发。
  if (AI.backend === 'none') {
    showNotification('请先配置模型',
      '二选一：① 本机运行 Ollama（默认 localhost:11434）后点设置里的「重新检测」；' +
      '② 在「设置 → OpenAI 兼容 API」填写服务商接口地址与 API Key。详见 server/.env.example。',
      'warning', 8000);
    const navBtn = document.querySelector('.nav-icon-btn[data-page="settings"]');
    if (navBtn) navBtn.click();
    setTimeout(() => {
      const anchor = document.getElementById('setOpenaiBase');
      if (anchor && anchor.closest) anchor.closest('.glass-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 300);
    return;
  }

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
  // [Day24.13] Argus 模式：绕过主 LLM，直接发起 detective_research（不走 generateAssistantReply）。
  // 用户消息此刻已落库并渲染；进度面板与报告自动贴入由 125 的 ArgusWatcher 链路承接。
  // 刻意排在 metaDirectAnswer 之前：模式激活时任何消息都视为研究问题。
  if (window.MorayArgusMode && window.MorayArgusMode.isActive()) {
    await window.MorayArgusMode.launchResearch(conv, content);
    if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear();
    return;
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
  // [M3b 2026-10-02] 长期记忆召回（MemoryBridge）：总闸 MoraySettings.memoryRecallEnabled
  // 默认关；recall 内部 <3 字符不发请求、800ms 超时、任何失败 fail-open 返回 ''。
  // query 取本会话最后一条用户消息（发送时序上即本次原句；后端 _fts_expr 自做 CJK 滑窗，
  // 前端不预处理）。_userMsgs/_lastUserText 自短问候判定处上移至此，值语义不变。
  const _userMsgs = (AppState.messages || []).filter(m => m.conversationId === conv.id && m.role === 'user');
  const _lastUserText = _userMsgs.length ? String((_userMsgs[_userMsgs.length - 1] || {}).content || '').trim() : '';
  let memoryNote = '';
  // [M5-P2 2026-10-06] 召回可观测：优先 recallWithItems（单次 API 调用 limit=10，前 3 注入、
  // 全量 items 供"记忆召回"胶囊；关闸/失败 fail-open 同旧路径），旧 recallForPrompt 作降级兜底。
  let memoryRecallDebug = null;
  if (typeof window.MorayMemory !== 'undefined') {
    if (typeof window.MorayMemory.recallWithItems === 'function') {
      const memRecall = await window.MorayMemory.recallWithItems(_lastUserText);
      memoryNote = memRecall.promptText;
      if (memRecall.items && memRecall.items.length) {
        memoryRecallDebug = { items: memRecall.items, injectedCount: memRecall.injectedCount };
      }
    } else {
      memoryNote = await window.MorayMemory.recallForPrompt(_lastUserText);
    }
  }
  // 数组附带属性：随返回值传给调用方；JSON.stringify(数组) 自动丢弃 → 不会进请求体
  msgs.memoryRecallDebug = memoryRecallDebug;
  // [Argus 深度研究] 能力通告（红线 R2 的最简版兜底）：工作台已接入 Argus 研究模块，
  // 这里无条件把这件事告诉模型，杜绝"我没有 Argus 这个工具"这类否认。
  // 两种形态——工具调用开着时直接点名 detective_research，并把"先调工具"写成硬约束：
  // 小模型（实测 qwen2.5:7b）在只看到"拿到 session_id 就告诉用户"时，会跳过工具调用
  // 直接编一个 id 出来（例如 "session_id 为 123456"），用户拿着假 id 去查必然查不到，
  // 所以这里必须把"先调用、id 只能来自工具返回、不许编造"放在同一条规则里。
  // 关着时（本次请求不带 tools）也保留说明，让模型给出正确的启动路径而不是假装已经启动。
  const argusHasTool = typeof ToolRegistry !== 'undefined' &&
    ToolRegistry.listForRequest().some(t => t.function.name === 'detective_research');
  const argusNote = argusHasTool
    ? '【Argus 深度研究】遇到"查资料 / 做调研 / 对比产品价格 / 了解行业信息"的请求时，你的第一个动作必须是调用 detective_research 工具（参数 question = 用户的问题），不要先回答、也不要先解释你打算做什么。\n' +
      '· 如果对话里已经有一条 detective_research 的工具返回（系统可能已替你发起），就直接用它返回的 session_id，不要重复调用。\n' +
      '· session_id 只能来自工具返回。在本轮没有工具返回之前，绝对不要说"已启动研究任务"，也不要写出任何 id —— 编造的 id 会让用户去查一个不存在的任务。\n' +
      '· 拿到工具返回的 session_id 后，再告诉用户"已启动研究任务"并给出这个 id，说明研究需要几分钟；报告生成后系统会**自动把它贴进本轮对话**，所以不要让用户"稍后再来问一次"（用户想主动问也可以，那时再调 detective_status）。\n' +
      '· detective_research 一轮里只调用一次：已经拿到 session_id 之后（或返回里出现 reused:true），立刻停止调用它并直接回答用户，重复调用只会起一堆重复研究。\n' +
      '· 之后凡是问那个研究的进度或结果（"好了吗 / 进度如何 / 查到了什么"），必须先调用 detective_status（参数 session_id = 上面那个 id）拿真实状态，不允许凭记忆或想象回答：还在跑就如实说还在进行中，报告就绪就基于它返回的报告正文作答。\n' +
      '· 报告里的价格、结论、来源只能引用工具返回的正文；正文里没有的，一个字都不要编（包括"约 X 元/月"这类看似合理的数字和 [来源：…] 标记）。'
    : '【Argus 深度研究】本工作台已接入 Argus 深度研究模块（研究接口 http://127.0.0.1:8010/api/argus/orchestrate），' +
      '但本次请求未启用工具调用，你无法直接启动研究。当用户要求查资料/做调研/对比产品价格时，' +
      '如实说明"已接入 Argus 深度研究，但需要在设置里开启「工具调用」后我才能直接启动"，' +
      '不要回答"我没有 Argus 这个工具"，也不要编造 session_id 或研究结论。';
  // 组装 system 消息：**顺序有意义** —— argusNote 放最后。
  // 小模型对长 system prompt 里靠后的指令更敏感；此前 argusNote 排在 kbCite 之前，
  // 末尾被"知识库引用"那段落了脚，实测 qwen2.5:7b 会跳过工具调用、直接编一个 session_id。
  const sysParts = [sys, kbCite, memoryNote, argusNote].filter(Boolean);
  if (sysParts.length) msgs.push({ role: 'system', content: sysParts.join('\n\n') });
  // [批次修复 #6] 短问候短路：新会话首条 ≤8 字符的纯问候（无引用/附件）→ 系统提示追加精简约束
  // [M3b 2026-10-02] _userMsgs/_lastUserText 已上移至 sysParts 组装前（记忆召回需要 query），此处不再重复定义
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
  // 仅含图片附件的合法用户消息以附件为准保留，不得被当空消息误删；
  // [历史断片修复 2026-10-20] 带 toolCalls 的无正文助手消息同样保留（重建时会序列化为工具轮记录，不再整轮蒸发）
  history = history.filter(m => {
    const hasContent = String(m.content || '').trim().length > 0;
    const hasAttachments = !!(m.attachments && m.attachments.length);
    const hasToolCalls = !!(m.toolCalls && m.toolCalls.length);
    return hasContent || hasAttachments || hasToolCalls;
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
  // [历史断片修复 2026-10-20] 回发历史时重建工具轮：上一轮的代码常活在 toolCalls（步骤卡）的 args 里，
  // 仅序列化 role/content 会让模型"忘记自己写过什么"（实探：create_file 被拒后反问"需要写入什么内容"）。
  // 工具名+执行状态+参数（单条截断 2000 字符防爆 token）追加进正文；content 为空时该重建块即占位。
  picked.forEach(m => {
    let text = m.content || '';
    if (m.toolCalls && m.toolCalls.length) {
      text += '\n\n【本轮实际执行过的工具调用（用户已在步骤卡看到相同内容）】\n';
      m.toolCalls.forEach(tc => {
        let argStr = '';
        try { argStr = JSON.stringify(tc.args || {}); } catch (e) { argStr = String((tc.args == null ? '' : tc.args)); }
        if (argStr.length > 2000) argStr = argStr.slice(0, 2000) + '…(参数已截断)';
        const st = tc.status === 'denied' ? '已被用户拒绝' : (tc.status === 'success' ? '已成功' : (tc.status === 'error' ? '失败' : (tc.status || '状态未知')));
        text += '- ' + (tc.name || '?') + '（' + st + '）参数：' + argStr + '\n';
      });
    }
    msgs.push({ role: m.role === 'assistant' ? 'assistant' : 'user', content: text });
  });
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
    <div class="msg-avatar" aria-hidden="true"><i data-lucide="sparkles" class="w-3.5 h-3.5"></i></div>
    <div class="max-w-[85%] msg-ai-body">
      <div class="flex items-center gap-2 mb-1">
        <span class="beacon-dot"></span>
        <!-- [小补丁] 占位头部：精确模型名（含 :b 后缀）+ 按 think 决策显示 思考中/正在生成
             [Day22a] 头像/信息行与 buildMessageEl 的 AI 分支结构保持一致（否则流式完成前后会跳版）；
             text-[10px] 类名同样必须保留（finalizeAssistantUI 取头部节点用）。 -->
        <span class="text-[10px] text-text-tertiary msg-meta-line">${escapeHtml(preciseModelName(model))} · <span class="gen-status-text">${(think === true && isThinkingModel(model)) ? '思考中' : '正在生成'}</span></span>
      </div>
      <div class="rounded-xl px-4 py-3 bg-surface-card border border-line-ghost/60 msg-bubble__ai-box">
        <div class="ai-msg-inner"></div>
        <div class="stream-actions flex items-center gap-2 mt-2">
          <button class="btn-ghost px-3 py-1 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1 stop-gen-btn">
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

  /* [停止缝修复 2026-10-03] 停止按钮绑定在上方，但真实生成控制器要等 chatStream 返回
   * 才写入 genController——buildRequestMessages（RAG 召回/历史压缩，秒级）窗口内点击
   * 此前只置 aborted 标志、genController 仍为 null、窗口结束 fetch 照常发出、底层照烧。
   * 现在用早期控制器占住 genController：窗口结束见 aborted 即不发请求（停止=无请求）；
   * 真实控制器产生后再联动（对齐 100_gateway attemptFn 的 abort 联动形态），防御接线。 */
  const earlyCtl = new AbortController();
  AppState.genController = earlyCtl;

  const requestMessages = await buildRequestMessages(conv, extraContext);
  // [M5-P2 2026-10-06] 召回明细挂到本条 AI 消息（routeReason 同款挂法；persistMessage 走
  // DB.putMessage 整对象落库，历史消息重渲染时胶囊可复现）。无召回（关闸/未命中）不挂字段。
  if (requestMessages.memoryRecallDebug) assistantMsg.memoryRecall = requestMessages.memoryRecallDebug;

  if (aborted || earlyCtl.signal.aborted) {
    // 窗口期已点停止：不发起任何生成请求，直接按"已停止"收尾
    AppState.genController = null;
    typingEl.remove();
    assistantMsg.content += '\n\n*[已停止生成]*';
    persistMessage(assistantMsg).catch(() => {});
    fillAssistantBody(inner, assistantMsg, false);
    showNotification('已停止', '生成已终止', 'info', 1500);
    finalizeAssistantUI(el, assistantMsg);
    return;
  }
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
  /* [停止缝修复] 早期控制器 → 真实控制器联动：chatStream 已发起 fetch 但 genController
   * 尚未换绑的同步窗口里，若早期信号已 aborted（或随后有人经旧引用 abort），直接传导。 */
  if (earlyCtl.signal.aborted) { try { controller.abort(); } catch (e) { /* 忽略 */ } }
  else earlyCtl.signal.addEventListener('abort', () => { try { controller.abort(); } catch (e) { /* 忽略 */ } }, { once: true });
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
    // [路由透明化] 接口返回的真实命中模型（后端把不受支持的模型名静默兜底改写时，它与路由模型不同）
    if (result.servedModel) assistantMsg.servedModel = String(result.servedModel);
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
    // [路由透明化] 实际模型/路由原因改由 fillAssistantBody 在气泡底部统一渲染（见 routeDetailHtml）：
    // 原先在此处插入的 .route-reason-note 会命中消息体内已渲染的那一份并被覆盖，故不再重复插入；
    // 移到消息体后，重开会话的历史消息同样能看到实际模型与路由详情。
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
        <label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" id="expOptTime" class="accent-brand-cobalt w-3.5 h-3.5">含时间戳</label>
        <label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" id="expOptModel" class="accent-brand-cobalt w-3.5 h-3.5">含模型信息</label>
        <label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" id="expOptStarred" class="accent-brand-cobalt w-3.5 h-3.5">仅导出收藏消息</label>
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
h1{font-size:22px}.msg{margin:16px 0}.who{font-size:12px;color:#616C82;margin-bottom:8px}
.bubble{padding:16px 16px;border-radius:12px;line-height:1.7;font-size:14px}
.user .bubble{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08)}
.assistant .bubble{background:#161616;border:1px solid rgba(255,255,255,0.08)}
pre{background:#0D1119;padding:12px;border-radius:8px;overflow-x:auto;font-size:13px}
code{font-family:ui-monospace,monospace}a{color:var(--brand)}table{border-collapse:collapse}th,td{border:1px solid var(--border);padding:8px 12px}</style>
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
        <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost" id="convTrimBtn">清理较早消息</button>
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

// [多引擎切换 2026-10-21] 输入工具栏"执行引擎"选择器（同工具栏、同装载时机；幂等）
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installEngineSelector);
else installEngineSelector();

// [阶段0 本机 Agent] 输入工具栏"本机工具"开关（同工具栏、同装载时机；幂等）
if (typeof installNativeAgentToggle === 'function') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installNativeAgentToggle);
  else installNativeAgentToggle();
}

/* ===================== [Day22a] 输入工具栏"AI 工具"开关 + 顶栏模型状态小字 ===================== */

/** [Day22a] 输入工具栏"AI 工具（Function Calling）"开关：与深度思考 / 本机工具同族，
 * 控制 toolsEnabled —— 内置工具（时间/计算器/知识库/片段/工作流/费用）进入 listForRequest 的总门。
 * 语义与设置页「AI 工具」卡的"启用工具调用"开关完全一致（关掉即请求体不携带 tools 字段），
 * 不额外联动 nativeToolsEnabled：那会让同一开关在设置页与工具栏表现不一致。
 * 插入锚点 = 骨架静态的代码模式按钮，使工具行读作「附件 → 工具 → 模式/开关」。
 * @returns {void} */
function installToolsToggle() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || document.getElementById('toolsToggleBtn')) return;
  const btn = document.createElement('button');
  btn.id = 'toolsToggleBtn';
  btn.type = 'button';
  btn.className = 'input-toolbar-btn tools-toggle-btn';
  btn.setAttribute('aria-label', 'AI 工具');
  btn.innerHTML = '<i data-lucide="wrench" class="w-4 h-4"></i>';
  on(btn, 'click', toggleToolsEnabled);
  const anchor = document.getElementById('codeModeBtn') || toolbar.querySelector('.input-toolbar-divider');
  if (anchor) toolbar.insertBefore(btn, anchor);
  else toolbar.appendChild(btn);
  syncToolsToggleBtn();
  refreshIcons();
}

/** 同步"AI 工具"开关态（激活底色 + 提示文案）
 * @returns {void} */
function syncToolsToggleBtn() {
  const btn = document.getElementById('toolsToggleBtn');
  if (!btn) return;
  const on = MoraySettings.get('toolsEnabled') === true;
  btn.classList.toggle('tools-on', on);
  btn.title = on
    ? 'AI 工具已开启：模型可调用内置工具（时间/计算器/知识库检索/片段/工作流/费用；需 OpenAI 兼容后端，Ollama 自动跳过）。点击关闭'
    : 'AI 工具（默认关）：开启后请求体携带 tools 字段，模型可调用内置工具。点击开启';
}

/** 点击切换 AI 工具总开关
 * @returns {Promise<void>} */
async function toggleToolsEnabled() {
  const next = !(MoraySettings.get('toolsEnabled') === true);
  await MoraySettings.set('toolsEnabled', next);
  syncToolsToggleBtn();
  showNotification(next ? 'AI 工具已开启' : 'AI 工具已关闭',
    next ? '模型可调用内置工具（需 OpenAI 兼容后端）' : '请求不再携带 tools 字段',
    next ? 'success' : 'info', 2000);
}

/** [Day22b] 顶栏模型小字下线：模型身份（本地优先/云端）改随模型名显示在输入台模型行
 * （见 syncInputModelLabel 写入的 .input-model-name），顶栏不再挤这行小字。
 * 保留同名函数是给可能存在的调用点做兼容：它们现在只做"移除"，不会再装回来。
 * @returns {void} */
function removeChatHeaderStatus() {
  const el = document.getElementById('chatHeaderStatus');
  if (el) el.remove();
}

/** [Day22b] 原安装函数 → 改为移除（幂等，供既有调用点沿用）
 * @returns {void} */
function installChatHeaderStatus() { removeChatHeaderStatus(); }

/** [Day22b] 原自愈刷新 → 改为移除（幂等，供既有调用点沿用）
 * @returns {void} */
function syncChatHeaderStatus() { removeChatHeaderStatus(); }

/** 把"顶栏小字下线"挂到既有"显示统一"链路上：syncInputModelLabel 已在启动 / 切换会话 / 新建删除 /
 * 手选模型 / 默认模型变更 / 后端重检后被调用（见该函数自身注释），包装它即可全覆盖这些时机。
 * 包装不改原逻辑（原函数照常执行并返回），与 executeCommand / toggleCodeMode 的既有包装手法一致。 */
(function wrapSyncInputModelLabelForHeader() {
  if (typeof syncInputModelLabel !== 'function' || syncInputModelLabel.__day22aWrapped) return;
  const orig = syncInputModelLabel;
  syncInputModelLabel = function () {
    const r = orig.apply(this, arguments);
    try { removeChatHeaderStatus(); } catch (e) { /* 顶栏小字移除失败不影响模型标签 */ }
    return r;
  };
  syncInputModelLabel.__day22aWrapped = true;
})();

/* ===================== [Day22b] 对话页空状态：输入台居中 + 快捷入口行 + 工具栏分组 ===================== */

/** 空状态欢迎语文案（骨架静态文案在 HTML 里，不在允许改动范围，故由 JS 覆盖） @type {string} */
const EMPTY_STATE_TITLE = '你好，我是 MoRay。今天想研究什么？';

/** 空状态布局（幂等）：欢迎语一句话 + 输入台上移与其同轴 + 快捷入口移到输入框下方 12px。
 * 判定空状态 = #welcomeScreen 存在（骨架静态欢迎页与 30_chat 动态重建两条路径同源，都有该 id）；
 * 布局本身由 135 §23 的 `#chat-normal:has(#welcomeScreen)` 规则完成，本函数只做三件 CSS 做不到的事：
 * ① 覆盖欢迎语文案（骨架 HTML 不改）；② 把 .welcome-suggestions 从欢迎页移进输入台底部容器
 * （CSS 无法跨父节点移位）；③ 清掉上一轮遗留的行。
 * @returns {void} */
function applyEmptyStateLayout() {
  const welcome = document.getElementById('welcomeScreen');
  const chatNormal = document.getElementById('chat-normal');
  const dock = chatNormal ? chatNormal.lastElementChild : null;
  const inDock = dock && !!dock.querySelector('#coreInputContainer');
  const existing = document.getElementById('welcomeQuickRow');
  if (!welcome || !inDock) {           // 非空状态（或有消息）：确保不留残行
    if (existing) existing.remove();
    return;
  }
  const title = welcome.querySelector('.welcome-title');
  if (title && title.textContent !== EMPTY_STATE_TITLE) title.textContent = EMPTY_STATE_TITLE;
  const sugg = welcome.querySelector('.welcome-suggestions');
  if (existing && sugg && existing !== sugg) sugg.remove();            // 欢迎页重建过：丢弃新的，保留已就位的那行
  else if (sugg && !existing) { sugg.id = 'welcomeQuickRow'; dock.appendChild(sugg); }
  if (typeof refreshIcons === 'function') refreshIcons();
}

/** 空状态清理：消息出现时把快捷入口行一并移除（欢迎页本体由 removeWelcomeView 负责）
 * @returns {void} */
function clearEmptyStateLayout() {
  const row = document.getElementById('welcomeQuickRow');
  if (row) row.remove();
}

/** 把空状态布局挂到既有的欢迎页显示/隐藏链路上（70 / 95 / 125 的调用点都走这两个全局函数，
 * 包装后全覆盖；包装不改原逻辑，原函数照常执行并返回）
 * @returns {void} */
(function wrapWelcomeForEmptyState() {
  if (typeof showWelcomeView === 'function' && !showWelcomeView.__day22bWrapped) {
    const origShow = showWelcomeView;
    showWelcomeView = function () {
      const r = origShow.apply(this, arguments);
      try { applyEmptyStateLayout(); } catch (e) { /* 空状态布局失败不影响欢迎页 */ }
      return r;
    };
    showWelcomeView.__day22bWrapped = true;
  }
  if (typeof removeWelcomeView === 'function' && !removeWelcomeView.__day22bWrapped) {
    const origRemove = removeWelcomeView;
    removeWelcomeView = function () {
      const r = origRemove.apply(this, arguments);
      try { clearEmptyStateLayout(); } catch (e) { /* 清理失败不影响消息显示 */ }
      return r;
    };
    removeWelcomeView.__day22bWrapped = true;
  }
})();

/** [第一屏v1] 欢迎页副标题下方一行「上次来过是 X」。
 * 数据：IndexedDB messages 全库最新一条（数值 createdAt，字符串兜底 Date.parse）——不走 LLM、后端、memory_bank。
 * 为何取全库而非当前会话：欢迎页只在当前会话 0 条消息时出现，若按当前会话读则永远为空、这行永不可见；
 * 「上次来过」本意是上一次使用时间，故取全库最新一条；无任何消息或读取失败仍直接 return 不显示。
 * 静态骨架版与动态版互斥、共用 #welcomeScreen（showWelcomeView 有 id 存在即早退），按 id 取当前挂载的那个。
 * @returns {Promise<void>} */
async function attachLastVisitLine() {
  try {
    const all = await DB._tx('messages', 'readonly', tx => tx.objectStore('messages').getAll());
    if (!all || !all.length) return;
    let t = 0;
    for (const m of all) {
      const ts = (typeof m.createdAt === 'number') ? m.createdAt : Date.parse(m.createdAt);
      if (ts > t) t = ts;
    }
    if (!t) return;
    const diff = Date.now() - t;
    if (diff < 0) return;
    let label;
    if (diff < 60000) label = '刚刚';
    else if (diff < 3600000) label = Math.floor(diff / 60000) + ' 分钟前';
    else if (diff < 86400000) label = Math.floor(diff / 3600000) + ' 小时前';
    else if (diff < 604800000) label = Math.floor(diff / 86400000) + ' 天前';
    else { const d = new Date(t); label = (d.getMonth() + 1) + ' 月 ' + d.getDate() + ' 日'; }
    const welcome = document.getElementById('welcomeScreen');
    if (!welcome) return;
    const prev = document.getElementById('welcomeLastVisit');
    if (prev) prev.remove();
    const sub = welcome.querySelector('.welcome-subtitle');
    if (!sub) return;
    sub.insertAdjacentHTML('afterend',
      '<div id="welcomeLastVisit" style="opacity:0.55;font-size:13px;margin-top:10px;letter-spacing:0.3px;">上次来过是 ' + label + '</div>');
    /* [窗户v1.2] 「陪伴天数」行：复用刚读到的 messages 全表 all（零新增 DB 读取），取最小 createdAt 算天数；
     * 文案每天固定：new Date().toDateString() 字符码累加做 seed，% 5 从 5 条文案例池选一条；
     * all 为空在函数开头已早退（整行不显示）；days=0 由 Math.max(1, ...) 兜底为 1；先去旧 #welcomeCompanyDays 防叠加；
     * 插在 .welcome-subtitle 的 afterend——afterend 后插者靠前，故代码落在「上次来过」插入之后，
     * 最终顺序为：陪伴天数 → 上次来过 → 上次你在做。*/
    const staleCompany = document.getElementById('welcomeCompanyDays');
    if (staleCompany) staleCompany.remove();
    let firstCreatedAt = Infinity;
    for (const m of all) {
      const ts = (typeof m.createdAt === 'number') ? m.createdAt : Date.parse(m.createdAt);
      if (ts && ts < firstCreatedAt) firstCreatedAt = ts;
    }
    if (firstCreatedAt !== Infinity) {
      const days = Math.max(1, Math.floor((Date.now() - firstCreatedAt) / 86400000));
      const seedStr = new Date().toDateString();
      let seed = 0;
      for (let i = 0; i < seedStr.length; i++) seed += seedStr.charCodeAt(i);
      const pool = [
        `你已经陪我走过 ${days} 天`,
        `我们已经认识 ${days} 天了`,
        `第 ${days} 天，你好`,
        `你在这里 ${days} 天了`,
        `又见面了，第 ${days} 天`,
      ];
      sub.insertAdjacentHTML('afterend',
        '<div id="welcomeCompanyDays" style="opacity:0.6;font-size:14px;margin-top:10px;letter-spacing:0.3px;">' + pool[seed % 5] + '</div>');
    }
    /* [窗户v1.3] 第一行下再补一行「👋 上次你在做：X」：复用刚读到的 messages 全表 all（零新增 DB 读取），
     * 取 role==='user' 中从新到旧第一条通过寒暄过滤的（实现为单遍扫描：通过者里 createdAt 最大者，严格等价）；
     * 过滤：内容去首尾空白、换行转空格后为空或不足 6 字 → 跳过（空即长度 0，被此规则覆盖）；以纯寒暄词开头（chitchatRe）→ 跳过；
     * 全部不通过 → lastUser 为 null、doingText 为空，整行不显示（沿用 v1.1 隐藏机制）；先去旧 #welcomeLastDoing 防叠加；
     * 通过者截前 20 字加省略号——纯 IndexedDB 渲染，不走 LLM。 */
    const staleDoing = document.getElementById('welcomeLastDoing');
    if (staleDoing) staleDoing.remove();
    const chitchatRe = /^(你好|您好|早上好|中午好|晚上好|下午好|嗨|hi|hello|在吗|在不在|谢谢|多谢|好的|ok|嗯|嗯嗯|哦|早|晚安|再见|拜拜|哈喽|哈啰)[\s，。！？!?.,、；;：:]*$/i;
    /* [窗户v2] 问题句识别：短问句（<30 字）以问句尾字（吗/呢/吧/?/？）结尾、且不含强任务动词 → 元问题而非“在做的事”，滤之。
     * /^.{0,28}[吗呢吧?？]$/ 等价于「以问尾字结尾且总长 ≤29（<30）」；含动词即视为任务句放行（如「为什么…？帮我查一下」）。 */
    const questionRe = /^.{0,28}[吗呢吧?？]$/;
    const taskVerbRe = /(写|改|修|查|建|删|加|跑|执行|帮我|实现|优化|调试)/;
    let lastUser = null; let lastUserT = 0;
    for (const m of all) {
      if (m.role !== 'user') continue;
      const ts = (typeof m.createdAt === 'number') ? m.createdAt : Date.parse(m.createdAt);
      if (!(ts > lastUserT)) continue;
      const txt = (typeof m.content === 'string') ? m.content.trim().replace(/[\r\n]/g, ' ') : '';
      if (txt.length < 6 || chitchatRe.test(txt)) continue;
      if (questionRe.test(txt) && !taskVerbRe.test(txt)) continue;
      lastUserT = ts; lastUser = m;
    }
    const doingText = (lastUser && typeof lastUser.content === 'string')
      ? lastUser.content.trim().replace(/[\r\n]/g, ' ') : '';
    if (doingText) {
      const doingClip = doingText.length > 20 ? doingText.slice(0, 20) + '…' : doingText;
      const visitEl = document.getElementById('welcomeLastVisit');
      if (visitEl) visitEl.insertAdjacentHTML('afterend',
        '<div id="welcomeLastDoing" style="opacity:0.55;font-size:13px;margin-top:6px;letter-spacing:0.3px;">👋 上次你在做：' + escapeHtml(doingClip) + '</div>');
    }
  } catch (e) { /* 第一屏行失败不影响欢迎页 */ }
}

/** [认识你 v1] 欢迎页顶部问候行「你好，X」：与 attachLastVisitLine 并列的独立行——
 * 不依赖 messages 全表（首启 0 消息、三行整体早退时也要能显示）。
 * 数据源=记忆库 preference 行（MeetYou 弹窗写入，content 形如「用户的名字是：X」），
 * 取 tags 含 'name' 的最新一条（list 为 created_at DESC → 首条命中即最新，改名即换新）。
 * 锚点确定性：#welcomeCompanyDays 已存在 → 对其 beforebegin（插在三行最顶）；
 * 不存在 → 对 .welcome-subtitle afterend——与 attachLastVisitLine 的执行先后无关。
 * 幂等：先 remove 旧 #welcomeHello 防叠加。
 * 失败边界：后端不可达/无 identity 行/解析失败 → 静默不显示（fail-open，绝不阻塞欢迎页）。
 * @returns {Promise<void>} */
async function attachHelloLine() {
  try {
    const welcome = document.getElementById('welcomeScreen');
    if (!welcome) return;
    const sub = welcome.querySelector('.welcome-subtitle');
    if (!sub) return;
    if (typeof MorayMemory === 'undefined' || !MorayMemory || typeof MorayMemory.list !== 'function') return;
    const data = await MorayMemory.list({ limit: 200 });
    const items = (data && Array.isArray(data.items)) ? data.items : [];
    let nameRow = null;
    for (const it of items) {
      if (!it || it.kind !== 'preference') continue;
      const tg = Array.isArray(it.tags) ? it.tags : [];
      if (tg.indexOf('name') === -1) continue;
      nameRow = it;   // list 按 created_at DESC 返回 → 首条命中即最新
      break;
    }
    if (!nameRow) return;
    const m = String(nameRow.content || '').match(/^用户的名字是[:：]\s*(.+)$/);
    const name = (m && m[1]) ? m[1].trim() : '';
    if (!name) return;
    const stale = document.getElementById('welcomeHello');
    if (stale) stale.remove();
    const anchor = document.getElementById('welcomeCompanyDays') || sub;
    anchor.insertAdjacentHTML(anchor === sub ? 'afterend' : 'beforebegin',
      '<div id="welcomeHello" style="opacity:0.7;font-size:14px;margin-top:10px;letter-spacing:0.3px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;" title="点此改名">你好，' + escapeHtml(name) + '<i data-lucide="pencil" class="w-3 h-3" style="opacity:.5;vertical-align:middle;"></i></div>');
    // [窗户v3] 改名入口：直绑 click（不走委托——refreshIcons 会把 <i> 换成 <svg>，委托的 closest() 会游离失效）。
    // 已有弹窗激活时不覆盖（同 maybeAsk 护栏）；_fetchPrefill 失败返回 null → ask 退化首启形态（fail-open）。
    const helloEl = document.getElementById('welcomeHello');
    if (helloEl) {
      helloEl.addEventListener('click', () => {
        try {
          const ov = document.getElementById('modalOverlay');
          if (ov && ov.classList.contains('active')) return;
          const pfP = (typeof MeetYou !== 'undefined' && MeetYou && typeof MeetYou._fetchPrefill === 'function')
            ? MeetYou._fetchPrefill() : Promise.resolve(null);
          Promise.resolve(pfP).then(pf => { try { MeetYou.ask(pf); } catch (e) { /* 忽略 */ } })
            .catch(() => { try { MeetYou.ask(); } catch (e) { /* 忽略 */ } });
        } catch (e) { /* 改名入口失败不影响欢迎页 */ }
      });
      if (typeof refreshIcons === 'function') refreshIcons();   // [窗户v3] 注入带图标 HTML 后必须补扫，否则 pencil 不渲染
    }
  } catch (e) { /* 问候行失败不影响欢迎页 */ }
}

/** [窗户v2.1] 欢迎页记忆引用行「📌 它记得你……」：与 attachHelloLine 并列的独立行——
 * 不依赖 messages 全表。数据源=记忆库 preference 行（MeetYou 弹窗写入），取 tags 同时含 'identity' 与
 * （'usage' 或 'style'）、且不含 'name'（名字已由「你好，X」展示）的最新一条；list 为 created_at DESC → 首条命中即最新。
 * 解析：content 形如「用户的主要用途是：X」「用户偏好的回答风格是：X」→ 通用取首个冒号后的 X，
 * 按 tags 分模板渲染：usage →「📌 它记得你主要用 MoRay 做：X」；style →「📌 它记得你偏好：X的回答」。
 * 锚点：#welcomeHello afterend（“你好，X”下方）；无 → #welcomeCompanyDays beforebegin；再无 → .welcome-subtitle afterend。
 * 幂等：先 remove 旧 #welcomeRemember 防叠加。
 * 失败边界：后端不可达/无合规行/解析失败 → 静默不显示（fail-open，绝不阻塞欢迎页）。
 * @returns {Promise<void>} */
async function attachRememberLine() {
  try {
    const welcome = document.getElementById('welcomeScreen');
    if (!welcome) return;
    if (typeof MorayMemory === 'undefined' || !MorayMemory || typeof MorayMemory.list !== 'function') return;
    const data = await MorayMemory.list({ limit: 200 });
    const items = (data && Array.isArray(data.items)) ? data.items : [];
    let prefRow = null; let prefKind = '';
    for (const it of items) {
      if (!it || it.kind !== 'preference') continue;
      const tg = Array.isArray(it.tags) ? it.tags : [];
      if (tg.indexOf('identity') === -1) continue;
      if (tg.indexOf('usage') === -1 && tg.indexOf('style') === -1) continue;
      if (tg.indexOf('name') !== -1) continue;
      prefRow = it;   // list 按 created_at DESC 返回 → 首条命中即最新
      prefKind = (tg.indexOf('usage') !== -1) ? 'usage' : 'style';   // MeetYou 两类互斥；并含时 usage 优先
      break;
    }
    if (!prefRow) return;
    const m = String(prefRow.content || '').match(/^[^:：]{1,30}[:：]\s*(.+)$/);
    const said = (m && m[1]) ? m[1].trim() : '';
    if (!said) return;
    /* [窗户v2.1] 按 tags 分模板：usage 直引原文（做：X）；style 把风格词嵌回短句（偏好：X的回答）。 */
    const saidHtml = (prefKind === 'usage')
      ? '📌 它记得你主要用 MoRay 做：' + escapeHtml(said)
      : '📌 它记得你偏好：' + escapeHtml(said) + '的回答';
    const stale = document.getElementById('welcomeRemember');
    if (stale) stale.remove();
    const hello = document.getElementById('welcomeHello');
    const company = document.getElementById('welcomeCompanyDays');
    const sub = welcome.querySelector('.welcome-subtitle');
    if (!hello && !company && !sub) return;
    const lineHtml = '<div id="welcomeRemember" style="opacity:0.6;font-size:13px;margin-top:6px;letter-spacing:0.3px;">' + saidHtml + '</div>';
    if (hello) hello.insertAdjacentHTML('afterend', lineHtml);
    else if (company) company.insertAdjacentHTML('beforebegin', lineHtml);
    else sub.insertAdjacentHTML('afterend', lineHtml);
  } catch (e) { /* 记忆引用行失败不影响欢迎页 */ }
}

/** [第一屏v1] 挂在 Day22b 包装链之后再包一层 showWelcomeView：原函数照常执行并返回，fire-and-forget 补行 */
(function wrapWelcomeForLastVisit() {
  if (typeof showWelcomeView === 'function' && !showWelcomeView.__lastVisitWrapped) {
    const origShow = showWelcomeView;
    showWelcomeView = function () {
      const r = origShow.apply(this, arguments);
      // [认识你 v1] 问候行串在 attachLastVisitLine 之后（.then 链仍 fire-and-forget，不改其逻辑）：
      // 等 #welcomeCompanyDays 插入完成，attachHelloLine 的 beforebegin 锚点才落在三行最顶
      // [窗户v2.1] 记忆引用行再串在 attachHelloLine 之后：主锚 #welcomeHello 须等问候行落定才存在，
      // 退化锚点（.welcome-subtitle afterend）也不会与问候行抢同一位（串行保证插入顺序可预期）
      try { attachLastVisitLine().catch(() => {}).then(() => { attachHelloLine().catch(() => {}).then(() => { attachRememberLine().catch(() => {}); }); }); } catch (e) { /* 问候行失败不影响欢迎页 */ }
      return r;
    };
    showWelcomeView.__lastVisitWrapped = true;
  }
})();

/** 工具栏图标分三组（附件 / 工具 / 输入模式）：只重排既有按钮顺序，不增删节点、不动事件绑定。
 * 骨架顺序是 附件 · 图片 · 代码 · 麦克风，运行时又插入了 工具/思考/本机，混在一起；
 * 目标顺序按任务书：附件(附件·图片) → 工具(代码·思考·工具·本机) → 输入模式(麦克风)，
 * 组间 8px 间距由 135 §23 的 margin 规则给出（组内沿用骨架 2px gap）。
 * 幂等：顺序已对就不动 DOM，避免与后到的按钮注入互相打架。
 * @returns {void} */
function installToolbarGroups() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar) return;
  const divider = toolbar.querySelector('.input-toolbar-divider');
  if (!divider || divider.parentNode !== toolbar) return;   // 结构不在预期（分隔线是插入锚点）→ 不动
  const want = ['attachImageBtn', 'attachImageBtn2', 'codeModeBtn', 'thinkModeBtn', 'toolsToggleBtn', 'nativeAgentBtn', 'voiceInputBtn']
    .map(id => document.getElementById(id)).filter(Boolean);
  if (want.length < 2) return;
  const cur = Array.from(toolbar.children).filter(el => el.classList && el.classList.contains('input-toolbar-btn') && want.includes(el));
  if (cur.length === want.length && cur.every((el, i) => el === want[i])) return;
  want.forEach(btn => toolbar.insertBefore(btn, divider));
  if (typeof refreshIcons === 'function') refreshIcons();
}

/** 工具栏子节点集变化时补一次分组（按钮由多处分片按需注入/移除，插完即重排）
 * @returns {void} */
function watchToolbarGroups() {
  const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
  if (!toolbar || typeof MutationObserver === 'undefined' || toolbar.dataset.day22bWatch) return;
  toolbar.dataset.day22bWatch = '1';
  let scheduled = false;
  const mo = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      try { installToolbarGroups(); } catch (e) { /* 重排失败不影响工具栏可用 */ }
    });
  });
  mo.observe(toolbar, { childList: true });
}

// [Day22a] 输入工具栏"AI 工具"开关（同工具栏、同装载时机；幂等）
if (typeof installToolsToggle === 'function') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installToolsToggle);
  else installToolsToggle();
}

// [Day22a] 顶栏模型状态小字（幂等；须在 bootApp 首次调用 syncInputModelLabel 之前就位）
if (typeof installChatHeaderStatus === 'function') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installChatHeaderStatus);
  else installChatHeaderStatus();
}

// [Day22b] 空状态布局：骨架里那份静态欢迎页不经 showWelcomeView，须在启动时补一次（幂等）
if (typeof applyEmptyStateLayout === 'function') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', applyEmptyStateLayout);
  else applyEmptyStateLayout();
}

// [Day22b] 工具栏三组重排：等各分片把运行时按钮插完后再排一次（幂等），并监听后续增删
if (typeof installToolbarGroups === 'function') {
  const runToolbarGroups = () => { try { installToolbarGroups(); watchToolbarGroups(); } catch (e) { /* 分组失败不影响工具栏 */ } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', runToolbarGroups);
  else runToolbarGroups();
  // 分片按需注入（深度思考/本机工具/AI 工具）可能在 DOMContentLoaded 之后，补两拍
  setTimeout(runToolbarGroups, 600);
  setTimeout(runToolbarGroups, 2000);
}
