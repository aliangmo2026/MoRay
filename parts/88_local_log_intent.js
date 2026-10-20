/* ============================================================================
 * 88_local_log_intent.js — 模糊排错意图接管（p_mcp 迭代 · 任务3 阶段4.2 · 前端侧）
 *
 * 零改动包装：不动 30_chat.js / 骨架 / 86 核心逻辑（红线3/4）。
 *   - 后端 api.py 尾部两行折叠了 /api/adapters/log-intent（D:\MoRay\adapters\routes.py）；
 *   - 本分片在 window 'load' 后轮询等待 bootApp 写入 window.moraySendOverride
 *     （70_models_settings_boot.js:1370），带 __crash88 标记替换为包装版——
 *     与 87 分片"轮询到对象就绪再注册"同一稳健范式（87:1108-1115 注释：bootApp 异步、
 *     'load' 不保证已写完 overrides，故轮询）。
 *   - 命中排错关键词（报错/崩了/跑不起来/出错了/挂了/闪退/traceback/exception/fatal）
 *     → 拦截本次发送（用户消息不落库、不进主 LLM），改调后端采集本地日志并送 Iris 分析；
 *   - 结果卡片直接插入 #chatMessagesNormal（87 同款宿主，30_chat.js:963 的
 *     innerHTML='' 仅发生在重渲染时，与 87 的卡片共存策略一致——卡片属一次性结果视图，
 *     被清掉可接受；发送链路完全不受影响）。
 * 可关闭：localStorage.moray_crash_intent === '0'（moray_skip_home 同款裸键范式）。
 * 所有动态文本一律 textContent 注入，绝不 innerHTML 拼 LLM/日志输出。
 * ==========================================================================*/
(function () {
  'use strict';
  var RE = /(报错|崩了|崩溃|跑不起来|跑不动|出错了|出错|挂掉|闪退|起不来|启动失败|异常退出|炸了|fatal|traceback|exception)/i;
  /* [L4 最小验证 2026-09-28] 报错修复任务编排分支（任务书 2.2）：
   * 触发 = 本正则命中关键词 且 消息带图片附件 → POST /api/tasks/run-error-fix（error_fix_flow
   * 模板异步执行）→ 任务状态卡轮询；无图关键词消息仍走上方原 log-intent 路径，一字未动。
   * 开关：localStorage.moray_errfix_intent === '0' 关闭本分支（moray_crash_intent 同族裸键）。 */
  var ERRFIX_RE = /(报错|修一下|修复|崩了|崩|出错了|出错|异常|挂|闪退|traceback|exception|fatal|error|fix)/i;
  /* [L4.5 阶段3.2 2026-09-28] 跑测试意图分支（任务书 3.2）：命中"跑测试/测试失败/pytest/test fail"
   * 且无图片附件 → 项目选择卡 → POST /api/tasks/run-test-flow（test_run_flow 模板）。
   * 判定顺序 = errfix（有图）→ test → log-intent（无图关键词老路径），errfix 分支一字未动，
   * 只把"直接提交"改为"先选项目"（任务书 2.1）；重试按钮重放已选项目、不再弹选择卡。
   * 开关：localStorage.moray_test_intent === '0' 关闭本分支（moray_errfix_intent 同族裸键）。 */
  var TEST_RE = /(跑测试|跑下测试|跑一下测试|跑一遍测试|运行测试|执行测试|跑用例|测试失败|测试挂了|测试不过|pytest|test\s*fail|run\s+tests?)/i;
  /* [L5.0 阶段5] 代码审查意图分支（任务书 5.2）：无图 + "代码审查/review/看看代码" →
   * 项目选择卡 → POST /api/tasks/run-review-flow（code_review_flow 模板）。
   * 开关：localStorage.moray_review_intent === '0'（moray_test_intent 同族裸键）。 */
  var REVIEW_RE = /(代码审查|审查.{0,2}代码|审.{0,2}代码|看看代码|检查代码|code\s*review|review\s*(the\s*)?code|\breview\b)/i;
  /* [L6.0 阶段5 2026-09-29] 自我评估意图分支：无图 + "看一下你的表现/表现如何/自我评估"
   * → GET /api/self/stats → 纯文本数字卡（复用 newCard，textContent 注入）。
   * 开关：localStorage.moray_selfstats_intent === '0'（同族裸键）。 */
  var SELF_RE = /(看一下你的表现|你的表现|表现如何|表现怎么样|自我评估|自评一下|你的成绩|修复统计|经验库规模|self\s*stats)/i;
  /* [L7.0 阶段2.3 2026-09-29] 模糊大意图分支：明确模板指令全没命中 + 大目标关键词 →
   * 确认卡「我理解为xxx，是否继续？」→ /api/autonomous/run。
   * 后端总闸 MORAY_TASK_PLANNER 默认关（红线7）——plannerOn 每 60s 探测一次，
   * 未开 = 本分支恒不命中。开关：localStorage.moray_planner_intent === '0'。 */
  var BIGGOAL_RE = /(优化一下|全面(检查|优化|梳理|修复|清理|治理)|整体(检查|优化|治理)|体检|大扫除|彻底(检查|修复|优化|清理)|自主(处理|修复|优化)|自动(处理|修复|优化)|检查一下服务|服务状态(如何|怎么样)?|帮我打理)/i;
  var plannerOn = false;

  function isPlannerEnabled() {
    try { return localStorage.getItem('moray_planner_intent') !== '0'; } catch (e) { return true; }
  }

  function probePlanner() {
    fetch(backendOrigin() + '/api/autonomous/status')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        plannerOn = !!(j && j.ok && j.data && j.data.enabled);
      })
      .catch(function () { plannerOn = false; });
  }
  /* [L5.0 阶段2] 语义意图兜底的启发式前闸（任务书 2.2）：正则三兄弟全部没命中时，
   * 只有"模糊但可能带任务意图"的短消息才值得花一次 LLM 调用；"今天天气怎么样"
   * 这类过不了闸，零成本直接走正常发送。开关 moray_llm_intent === '0' 关闭整层。 */
  var VAGUE_RE = /(不行了|不工作|不正常|没反应|没响应|不生效|不好用|不管用|不好使|出问题|又挂|挂了|起不来|打不开|进不去|白屏|卡死|测过了没|测试过了|测试过没|跑没跑|看看测试|测试结果|过没过|用例|审一下|审代码|有没有问题|有没有bug|修一下|检查一下)/i;
  var busy = false;
  var taskBusy = false;   // 任务卡轮询期间防止重复提交
  var pickerActive = false;  // [L4.5] 项目选择卡同时只允许一张
  var restoring88 = false;   // [L5.0 阶段4] 刷新恢复只做一次
  var llmIntentPending = false;  // [L5.0 阶段2] 分类在途，防连发

  function isReviewEnabled() {
    try { return localStorage.getItem('moray_review_intent') !== '0'; } catch (e) { return true; }
  }

  function isSelfStatsEnabled() {   // [L6.0 阶段5]
    try { return localStorage.getItem('moray_selfstats_intent') !== '0'; } catch (e) { return true; }
  }

  function isLlmIntentEnabled() {
    try { return localStorage.getItem('moray_llm_intent') !== '0'; } catch (e) { return true; }
  }

  function currentConvId() {   // [L5.0 阶段4] 提交/恢复都以它认领任务卡归属的对话
    try {
      return (typeof AppState !== 'undefined' && AppState.activeConv && AppState.activeConv.id)
        || (window.AppState && window.AppState.activeConv && window.AppState.activeConv.id) || '';
    } catch (e) { return ''; }
  }

  /* ============ [L5.0 阶段2] LLM 轻量意图分类（前端兜底，不动后端） ============
   * 三分类 fix_error / run_tests / general_chat；置信度 <0.6 一律按 general_chat（不拦截）；
   * 任何异常/超时（10s）→ null（不拦截）；结果按归一化文本缓存 localStorage 7 天。
   * 模型走主对话同一选择链（AI.chatStream 自动适配 代理/直连/Ollama 三形态）。 */
  var INTENT_CACHE_KEY = 'moray_intent_cache_v1';
  var INTENT_TTL_MS = 7 * 24 * 3600 * 1000;

  function normIntentKey(t) {
    return String(t || '').toLowerCase().replace(/\s+/g, '').replace(/[。！？!?~～，,；;：:]+$/g, '').slice(0, 60);
  }
  function intentCacheGet(k) {
    try {
      var obj = JSON.parse(localStorage.getItem(INTENT_CACHE_KEY) || '{}');
      var e = obj[k];
      if (e && Date.now() - (e.ts || 0) < INTENT_TTL_MS) return e.label;
      if (e) { delete obj[k]; localStorage.setItem(INTENT_CACHE_KEY, JSON.stringify(obj)); }
    } catch (err) {}
    return null;
  }
  function intentCachePut(k, label) {
    try {
      var obj = JSON.parse(localStorage.getItem(INTENT_CACHE_KEY) || '{}');
      var now = Date.now();
      Object.keys(obj).forEach(function (x) { if (now - (obj[x].ts || 0) > INTENT_TTL_MS) delete obj[x]; });
      obj[k] = { label: label, ts: now };
      var keys = Object.keys(obj);
      if (keys.length > 60) {  // 超额按时间戳丢最旧（裸键范式内的自洁）
        keys.sort(function (a, b) { return (obj[a].ts || 0) - (obj[b].ts || 0); })
          .slice(0, keys.length - 60).forEach(function (x) { delete obj[x]; });
      }
      localStorage.setItem(INTENT_CACHE_KEY, JSON.stringify(obj));
    } catch (err) {}
  }

  function intentClassify(raw) {
    var ck = 'hit';
    return Promise.resolve().then(function () {
      ck = normIntentKey(raw);
      var cached = intentCacheGet(ck);
      if (cached) return cached;
      if (typeof AI === 'undefined' || !AI || typeof AI.chatStream !== 'function') return null;
      var model = '';
      try {
        if (typeof preciseModelName === 'function' && typeof currentConvModel === 'function') {
          model = preciseModelName(currentConvModel(AppState && AppState.activeConv)) || '';
        }
      } catch (e) { model = ''; }
      var opts = {
        messages: [
          { role: 'system', content: '你是意图分类器。只输出一个 JSON 对象，不要任何其它文字：'
              + '{"intent":"fix_error|run_tests|general_chat","confidence":0到1}。'
              + 'fix_error=用户在说程序出错/崩溃/行为异常并期望修复；'
              + 'run_tests=用户想跑测试或了解测试结果；'
              + 'general_chat=其他一切（闲聊、咨询、让写代码）。' },
          { role: 'user', content: String(raw).slice(0, 300) }
        ],
        temperature: 0, max_tokens: 40
      };
      if (model) opts.model = model;
      var timeout = new Promise(function (_, rej) {
        setTimeout(function () { rej(new Error('intent-timeout')); }, 10000);
      });
      return Promise.race([AI.chatStream(opts).promise, timeout]).then(function (res) {
        var text = (res && res.content) || '';
        var m = text.match(/\{[\s\S]*?\}/);
        if (!m) return null;
        var j = JSON.parse(m[0]);
        var label = String(j.intent || '').trim();
        var conf = Number(j.confidence || 0);
        if (label !== 'fix_error' && label !== 'run_tests' && label !== 'general_chat') return null;
        if (label !== 'general_chat' && !(conf >= 0.6)) label = 'general_chat';  // 低置信度不拦截
        intentCachePut(ck, label);
        return label;
      });
    }).catch(function () { return null; });  // 分类失败/超时一律不拦截（=现状行为）
  }

  function isTestEnabled() {
    try { return localStorage.getItem('moray_test_intent') !== '0'; } catch (e) { return true; }
  }

  function isErrFixEnabled() {
    try { return localStorage.getItem('moray_errfix_intent') !== '0'; } catch (e) { return true; }
  }

  function isEnabled() {
    try { return localStorage.getItem('moray_crash_intent') !== '0'; } catch (e) { return true; }
  }

  function backendOrigin() {
    return (window.MorayBackend && window.MorayBackend.origin) || '';
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }

  function msgHost() {
    return document.getElementById('chatMessagesNormal');
  }

  function newCard(titleText) {
    var host = msgHost();
    if (!host) return null;
    var card = el('div', 'crash88-card');
    card.setAttribute('style',
      'margin:10px 0;padding:12px 16px;border-radius:8px;border:1px solid var(--color-border,rgba(255,255,255,.12));' +
      'background:var(--color-bg-card,rgba(26,26,26,.95));color:var(--color-text-primary,#e8e8e8);' +
      'font-size:13px;line-height:1.6;min-width:0;max-width:92%;');
    var head = el('div');
    head.setAttribute('style', 'display:flex;align-items:center;gap:8px;margin-bottom:6px;');
    var dot = el('span', null, '●');
    dot.setAttribute('style', 'color:#E5734A;font-size:10px;');
    head.appendChild(dot);
    head.appendChild(el('strong', null, titleText));
    card.appendChild(head);
    var body = el('div', 'crash88-body');
    body.setAttribute('style', 'white-space:pre-wrap;word-break:break-word;min-width:0;');
    card.appendChild(body);
    host.appendChild(card);
    host.scrollTop = host.scrollHeight;
    return { card: card, body: body };
  }

  function renderUserEcho(raw, atts) {
    var host = msgHost();
    if (!host) return;
    /* 接管发生在 sendUserMessage 之前：新会话时欢迎空态还盖着消息流，
     * 卡片会被挡住——与主流同款处理（removeWelcomeView + 去 hidden）。 */
    try { if (typeof removeWelcomeView === 'function') removeWelcomeView(); } catch (e) {}
    host.classList.remove('hidden');
    var u = el('div', null, raw);
    u.setAttribute('style',
      'margin:10px 0 4px;padding:8px 14px;border-radius:8px;font-size:13px;' +
      'background:rgba(255,255,255,.06);color:var(--color-text-primary,#e8e8e8);' +
      'word-break:break-word;max-width:92%;');
    if (atts && atts.length) {  // [L4] 可选：回显随消息的图片附件（本机 dataURL，与主消息流同款）
      var row = el('div');
      row.setAttribute('style', 'display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;');
      atts.forEach(function (a) {
        if (!a || !a.dataUrl) return;
        var img = document.createElement('img');
        img.src = a.dataUrl;
        img.alt = a.name || '报错截图';
        img.setAttribute('style',
          'max-width:220px;max-height:160px;border-radius:6px;' +
          'border:1px solid var(--color-border,rgba(255,255,255,.12));');
        row.appendChild(img);
      });
      u.appendChild(row);
    }
    host.appendChild(u);
  }

  /* ============ [L4] 任务状态卡骨架（L4.5 起两条流程共用，原样保留） ============ */

  var ST88 = { ok: ['✓', '#5cb85c'], failed: ['✗', '#d9534f'], skipped: ['–', '#999999'], pending: ['…', '#999999'] };

  function newTaskCard(titleText) {
    var host = msgHost();
    if (!host) return null;
    var card = el('div', 'crash88-card errfix88-card');
    card.setAttribute('style',
      'margin:10px 0;padding:12px 16px;border-radius:8px;border:1px solid var(--color-border,rgba(255,255,255,.12));' +
      'background:var(--color-bg-card,rgba(26,26,26,.95));color:var(--color-text-primary,#e8e8e8);' +
      'font-size:13px;line-height:1.6;min-width:0;max-width:92%;');
    var head = el('div');
    head.setAttribute('style', 'display:flex;align-items:center;gap:8px;margin-bottom:6px;');
    var dot = el('span', null, '●');
    dot.setAttribute('style', 'color:#E5734A;font-size:10px;');
    head.appendChild(dot);
    var title = el('strong', null, titleText);
    head.appendChild(title);
    card.appendChild(head);
    var body = el('div');
    body.setAttribute('style', 'white-space:pre-wrap;word-break:break-word;min-width:0;');
    card.appendChild(body);
    host.appendChild(card);
    host.scrollTop = host.scrollHeight;
    return { card: card, dot: dot, title: title, body: body };
  }

  function renderProgressLines(body, steps) {
    (steps || []).forEach(function (s) {
      var line = el('div');
      var st = ST88[s.status] || ST88.pending;
      var mk = el('span', null, st[0] + ' ');
      mk.setAttribute('style', 'color:' + st[1] + ';');
      line.appendChild(mk);
      line.appendChild(el('span', null,
        (s.label || s.id || '?') +
        (s.engine && s.engine !== '-' ? '［' + s.engine + '］' : '') +
        (s.duration_ms ? ' ' + Math.round(s.duration_ms / 100) / 10 + 's' : '')));
      if (s.error) {
        var er = el('div', null, '　' + s.error);
        er.setAttribute('style', 'color:#d9534f;margin-left:14px;');
        line.appendChild(er);
      }
      body.appendChild(line);
    });
  }

  /* ============ [L4.5] 最近项目历史（localStorage 裸键范式，moray_skip_home 同族） ============ */
  var RECENT_KEY = 'moray_task_projects_recent';
  function loadRecents() {
    try {
      var arr = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      return Array.isArray(arr) ? arr.filter(function (x) { return x && x.path; }) : [];
    } catch (e) { return []; }
  }
  function pushRecent(path, name) {
    if (!path) return;
    var arr = loadRecents().filter(function (x) { return x.path !== path; });
    arr.unshift({ path: path, name: name || String(path).split(/[\\/]/).pop(), ts: Date.now() });
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(arr.slice(0, 8))); } catch (e) {}
  }

  /* ============ [L4.5 阶段2.1] 项目选择卡 ============
   * 候选 = 最近用过的前 3 个（recents，MRU 序）∪ 后端配置列表（GET /api/tasks/projects），按路径去重；
   * 默认选中 = 上次使用（recents[0]），其下依次 旧裸键 moray_errfix_project > 后端 default；
   * 支持手动输入路径；点"开始"后才真正提交任务；选择结果写回 recents（记忆）。 */
  function openProjectPicker(kind, raw, atts) {
    pickerActive = true;
    var card = newCard(kind === 'test' ? '跑测试 · 先选择要测试的项目'
      : (kind === 'review' ? '代码审查 · 先选择要审查的项目' : '报错修复 · 先选择要修复的项目'));
    if (!card) { pickerActive = false; return; }
    var body = card.body;
    var radioName = 'pick88_' + Date.now();
    var chosen = { path: '', name: '' };
    var manual = null;

    function addOption(path, name, checked) {
      var row = el('label');
      row.setAttribute('style', 'display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer;min-width:0;');
      var radio = document.createElement('input');
      radio.type = 'radio'; radio.name = radioName; radio.checked = !!checked;
      radio.addEventListener('change', function () {
        chosen.path = path; chosen.name = name;
        if (manual) manual.value = path;
      });
      row.appendChild(radio);
      row.appendChild(el('span', null, name || path));
      var code = el('span', null, path);
      code.setAttribute('style', 'font-size:11px;opacity:.65;word-break:break-all;');
      row.appendChild(code);
      body.appendChild(row);
    }

    function finalize() {
      var tip = el('div', null, '或直接输入项目路径：');
      tip.setAttribute('style', 'margin:8px 0 4px;');
      body.appendChild(tip);
      manual = el('input');
      manual.setAttribute('type', 'text');
      manual.value = chosen.path || '';
      manual.setAttribute('style',
        'width:100%;box-sizing:border-box;padding:6px 10px;border-radius:6px;font-size:12px;min-width:0;' +
        'border:1px solid var(--color-border,rgba(255,255,255,.16));' +
        'background:rgba(255,255,255,.05);color:inherit;');
      manual.addEventListener('input', function () { chosen.path = manual.value.trim(); });
      body.appendChild(manual);

      var btnRow = el('div');
      btnRow.setAttribute('style', 'display:flex;gap:8px;margin-top:8px;');
      var btn = el('button', null, kind === 'test' ? '开始跑测试'
        : (kind === 'review' ? '开始审查' : '开始修复'));
      btn.setAttribute('style',
        'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;' +
        'border:1px solid var(--color-border,rgba(255,255,255,.16));' +
        'background:rgba(229,115,74,.18);color:inherit;');
      btn.addEventListener('click', function () {
        var p = ((manual && manual.value) || chosen.path || '').trim().replace(/^"|"$/g, '');
        if (!p) { btn.textContent = '请先选择或输入项目路径'; return; }
        if (btn.disabled) return;              // 防连点/重复提交
        pickerActive = false;
        var nm = String(p).split(/[\\/]/).pop();
        pushRecent(p, nm);
        // [L4.5] 提交即锁定本卡：防止选择卡残留再点造成同项目双任务
        btn.disabled = true; cancel.disabled = true;
        btn.textContent = '已提交 ✓';
        manual.disabled = true;
        body.querySelectorAll('input[type=radio]').forEach(function (rd) { rd.disabled = true; });
        if (kind === 'test') testSubmit(raw, p, nm);
        else if (kind === 'review') reviewSubmit(raw, p, nm);
        else taskSubmit(raw, atts, p, nm);
      });
      var cancel = el('button', null, '取消');
      cancel.setAttribute('style',
        'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;' +
        'border:1px solid var(--color-border,rgba(255,255,255,.12));' +
        'background:rgba(255,255,255,.06);color:inherit;');
      cancel.addEventListener('click', function () {
        pickerActive = false;
        card.card.style.display = 'none';
      });
      btnRow.appendChild(btn);
      btnRow.appendChild(cancel);
      body.appendChild(btnRow);
      var host = msgHost();
      if (host) host.scrollTop = host.scrollHeight;
    }

    var legacy = '';
    try { legacy = localStorage.getItem('moray_errfix_project') || ''; } catch (e) {}
    fetch(backendOrigin() + '/api/tasks/projects')
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false }; })
      .then(function (j) {
        var data = (j && j.ok && j.data) ? j.data : {};
        var cfg = data.projects || [];
        var seen = {};
        loadRecents().slice(0, 3).forEach(function (x) {
          if (!seen[x.path]) { seen[x.path] = 1; addOption(x.path, x.name || '', false); }
        });
        cfg.forEach(function (x) {
          if (!seen[x.path]) { seen[x.path] = 1; addOption(x.path, x.name || '', false); }
        });
        var defPath = '';
        var radios = body.querySelectorAll('input[type=radio]');
        if (loadRecents()[0]) defPath = loadRecents()[0].path;
        if (!defPath && legacy) defPath = legacy;
        if (!defPath) defPath = data.default || '';
        if (!defPath && cfg[0]) defPath = cfg[0].path;
        if (!defPath && radios.length) defPath = radios[0].parentNode.textContent.split(/\s{2,}/)[0];
        // 用默认路径回填 radio 选中态
        var got = false;
        radios.forEach(function (rd) {
          var pathTxt = rd.parentNode.textContent;
          if (!got && defPath && pathTxt.indexOf(defPath) >= 0) {
            rd.checked = true; got = true;
            chosen.path = defPath;
            chosen.name = String(defPath).split(/[\\/]/).pop();
          }
        });
        if (!got && defPath) { chosen.path = defPath; chosen.name = String(defPath).split(/[\\/]/).pop(); }
        finalize();
      });
  }

  /* ============ [L4] 任务状态卡：提交 + 轮询 + 三态渲染（L4.5：参数化项目名/retry 闭包） ============ */

  function addRetryButton(card, retry) {
    var btn = el('button', null, '重试');
    btn.setAttribute('style',
      'margin-top:8px;padding:4px 14px;border-radius:6px;' +
      'border:1px solid var(--color-border,rgba(255,255,255,.16));' +
      'background:rgba(255,255,255,.06);color:inherit;cursor:pointer;font-size:12px;');
    btn.addEventListener('click', function () {
      btn.disabled = true; btn.textContent = '重试中…';
      retry();   // [L4.5] 重放已选项目，不再弹选择卡（审计风险 3）
    });
    card.body.appendChild(btn);
  }

  function taskSubmit(raw, atts, projectDir, projName) {
    if (taskBusy) return;
    taskBusy = true;
    var pd = projectDir || '';
    if (!pd) {
      try { pd = localStorage.getItem('moray_errfix_project') || ''; } catch (e) {}  // L4_min 旧裸键兼容
    }
    var retry = function () { taskSubmit(raw, atts, pd, projName); };
    var card = newTaskCard('报错修复任务启动中…' + (projName ? ' · ' + projName : ''));
    if (!card) { taskBusy = false; return; }
    card.proj = projName || '';
    var payload = { error_image_base64: (atts && atts[0] && atts[0].dataUrl) || '', error_text: raw };
    payload.conversation_id = currentConvId();   // [L5.0 阶段4] 刷新恢复的归属线索
    if (pd) payload.project_dir = pd;
    fetch(backendOrigin() + '/api/tasks/run-error-fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().then(function (j) { return { st: r.status, j: j }; }); })
      .then(function (res) {
        if (!res.j || !res.j.ok) {
          renderFailedCard(card, { steps: [], human_intervention: (res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st) }, retry);
          return;
        }
        card.title.textContent = '修复任务执行中（' + (res.j.data.template || 'error_fix_flow') + '）'
          + (projName ? ' · 项目 ' + projName : '');
        pollTask(card, res.j.data.task_id, Date.now(), retry, '修复完成', '修复失败 · 可重试');
      })
      .catch(function (e) {
        renderFailedCard(card, { steps: [], human_intervention: '无法连接 /api/tasks/run-error-fix：' + e + '（该路由随 MoRay 后端重启生效）' }, retry);
      });
  }

  /* [L4.5 阶段3.2] 跑测试任务提交（与 taskSubmit 同一条卡片/轮询管线，仅端点/标题不同） */
  function testSubmit(raw, projectDir, projName) {
    if (taskBusy) return;
    taskBusy = true;
    var retry = function () { testSubmit(raw, projectDir, projName); };
    var card = newTaskCard('测试任务启动中…' + (projName ? ' · ' + projName : ''));
    if (!card) { taskBusy = false; return; }
    card.proj = projName || '';
    fetch(backendOrigin() + '/api/tasks/run-test-flow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_dir: projectDir || '', conversation_id: currentConvId() })
    })
      .then(function (r) { return r.json().then(function (j) { return { st: r.status, j: j }; }); })
      .then(function (res) {
        if (!res.j || !res.j.ok) {
          renderFailedCard(card, { steps: [], human_intervention: (res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st) }, retry, '测试任务失败 · 可重试');
          return;
        }
        card.title.textContent = '测试任务执行中（test_run_flow）· 项目 ' + (projName || '');
        pollTask(card, res.j.data.task_id, Date.now(), retry, '测试完成', '测试任务失败 · 可重试');
      })
      .catch(function (e) {
        renderFailedCard(card, { steps: [], human_intervention: '无法连接 /api/tasks/run-test-flow：' + e + '（该路由随 MoRay 后端重启生效）' }, retry, '测试任务失败 · 可重试');
      });
  }

  /* [L5.0 阶段5] 代码审查任务提交（同一条卡片/轮询管线，仅端点/文案不同） */
  function reviewSubmit(raw, projectDir, projName) {
    if (taskBusy) return;
    taskBusy = true;
    var retry = function () { reviewSubmit(raw, projectDir, projName); };
    var card = newTaskCard('审查任务启动中…' + (projName ? ' · ' + projName : ''));
    if (!card) { taskBusy = false; return; }
    card.proj = projName || '';
    fetch(backendOrigin() + '/api/tasks/run-review-flow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_dir: projectDir || '', conversation_id: currentConvId() })
    })
      .then(function (r) { return r.json().then(function (j) { return { st: r.status, j: j }; }); })
      .then(function (res) {
        if (!res.j || !res.j.ok) {
          renderFailedCard(card, { steps: [], human_intervention: (res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st) }, retry, '审查任务失败 · 可重试');
          return;
        }
        card.title.textContent = '审查任务执行中（code_review_flow）· 项目 ' + (projName || '');
        pollTask(card, res.j.data.task_id, Date.now(), retry, '审查完成', '审查任务失败 · 可重试');
      })
      .catch(function (e) {
        renderFailedCard(card, { steps: [], human_intervention: '无法连接 /api/tasks/run-review-flow：' + e + '（该路由随 MoRay 后端重启生效）' }, retry, '审查任务失败 · 可重试');
      });
  }

  function pollTask(card, taskId, t0, retry, doneLabel, failLabel) {
    var wait = [4000, 4000, 8000, 8000, 15000];  // 退避与 ArgusWatcher 同款，尾值封顶
    var i = 0;
    function step() {
      if (Date.now() - t0 > 20 * 60 * 1000) {
        renderFailedCard(card, { steps: [], human_intervention: '等待超过 20 分钟，已停止轮询（任务可能仍在后台跑）' }, retry, failLabel);
        return;
      }
      fetch(backendOrigin() + '/api/tasks/' + encodeURIComponent(taskId))
        .then(function (r) { return r.json().then(function (j) { return { st: r.status, j: j }; }); })
        .then(function (res) {
          if (!res.j || !res.j.ok) {
            renderFailedCard(card, { steps: [], human_intervention: (res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st) }, retry, failLabel);
            return;
          }
          var d = res.j.data;
          if (d.status === 'running') {
            card.body.textContent = '';
            card.title.textContent = '任务执行中 · ' + (d.current_stage || '准备中')
              + (card.proj ? ' · 项目 ' + card.proj : '');
            if ((d.steps || []).length) renderProgressLines(card.body, d.steps);
            else card.body.appendChild(el('div', null, '（第一步执行中，完成后显示进度）'));
            i++; setTimeout(step, wait[Math.min(i, wait.length - 1)]);
            return;
          }
          taskBusy = false;
          if (d.status === 'completed') renderDoneCard(card, d, doneLabel);
          else renderFailedCard(card, d, retry, failLabel);
        })
        .catch(function () { i++; setTimeout(step, wait[Math.min(i, wait.length - 1)]); });  // 瞬时网络错误继续轮询
    }
    setTimeout(step, 4000);
  }

  function renderDoneCard(card, d, doneLabel) {
    card.dot.setAttribute('style', 'color:#5cb85c;font-size:10px;');
    card.title.textContent = (doneLabel || '修复完成') + (card.proj ? ' · 项目 ' + card.proj : '') + ' · token ' + (d.tokens_spent || 0) + ' · 用时 ' + (d.duration_sec || '-') + 's';
    card.body.textContent = '';
    renderProgressLines(card.body, d.steps);
    if (d.report) {
      var pre = el('pre', null, d.report);
      pre.setAttribute('style',
        'margin:8px 0 0;padding:10px;border-radius:6px;background:rgba(255,255,255,.05);' +
        'max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;min-width:0;');
      card.body.appendChild(pre);
    }
    var wd = el('div', null, '工作目录：' + (d.work_dir || '-'));
    wd.setAttribute('style', 'margin-top:6px;font-size:11px;opacity:.7;word-break:break-all;');
    card.body.appendChild(wd);
  }

  function renderFailedCard(card, d, retry, failLabel) {
    taskBusy = false;
    card.dot.setAttribute('style', 'color:#d9534f;font-size:10px;');
    card.title.textContent = failLabel || '修复失败 · 可重试';
    card.body.textContent = '';
    renderProgressLines(card.body, d.steps);
    card.body.appendChild(el('div', null, d.human_intervention || d.error || '某一步失败'));
    if (typeof retry === 'function') addRetryButton(card, retry);
  }

  function listCandidates(body, cands) {
    if (!cands || !cands.length) return;
    var t = el('div'); t.setAttribute('style', 'margin:6px 0 2px;font-weight:600;');
    t.textContent = '根因候选（按优先级）';
    body.appendChild(t);
    cands.forEach(function (c, i) {
      var li = el('div', null, (i + 1) + '. ' + (c.cause || '') +
        (c.source ? '　[' + c.source + ']' : ''));
      li.setAttribute('style', 'margin-left:10px;');
      body.appendChild(li);
    });
  }

  async function analyze(raw) {
    var card = newCard('本地排错接管中…');
    if (!card) return;
    try {
      var r = await fetch(backendOrigin() + '/api/adapters/log-intent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: raw })
      });
      var resp = await r.json();
      card.body.textContent = '';
      if (resp.ok && resp.data && resp.data.has_data) {
        var a = resp.data.analysis || {};
        card.card.firstChild.lastChild.textContent = '本地日志分析（' + (resp.data.collected.source || '日志源') + '）';
        var line = el('div', null,
          '错误类型：' + (a.error_type || '未识别') + '　严重度：' + (a.severity || '-') +
          '　定位：' + (a.file_path || '?') + (a.line_number ? ':' + a.line_number : ''));
        card.body.appendChild(line);
        listCandidates(card.body, a.root_cause_candidates);
        if (a.diagnosis && a.diagnosis.root_cause) {
          var d = el('div', null, '诊断：' + a.diagnosis.root_cause);
          d.setAttribute('style', 'margin-top:6px;');
          card.body.appendChild(d);
        }
        if (a.diagnosis && a.diagnosis.plan) {
          var p = el('div', null, '方案：' + a.diagnosis.plan);
          card.body.appendChild(p);
        }
      } else if (resp.ok && resp.data && !resp.data.has_data) {
        card.card.firstChild.lastChild.textContent = '暂未采集到本地报错';
        card.body.textContent = resp.data.message || '请提供截图或贴入报错文本。';
      } else {
        card.card.firstChild.lastChild.textContent = '排错服务不可用';
        card.body.textContent = (resp && (resp.message || resp.code)) || ('HTTP ' + r.status);
      }
    } catch (e) {
      card.body.textContent = '调用 /api/adapters/log-intent 失败：' + e +
        '（提示：该路由随 MoRay 后端下次重启生效）';
    } finally {
      var host = msgHost();
      if (host) host.scrollTop = host.scrollHeight;
    }
  }

  /* ============ [L6.0 阶段5] 自我评估卡片：GET /api/self/stats → 纯文本数字卡 ============ */

  function fmtPct(x) {
    var n = Number(x);
    if (!isFinite(n)) return '-';
    return Math.round(n * 100) + '%';
  }

  function analyzeSelfStats() {
    var card = newCard('自我评估 · 表现报告');
    if (!card) return;
    fetch(backendOrigin() + '/api/self/stats')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        card.body.textContent = '';
        if (!j || !j.ok || !j.data) {
          card.body.textContent = '自我评估数据不可用' + (j && j.message ? ('：' + j.message) : '');
          return;
        }
        var d = j.data;
        var L = [];
        var g = d.global || {};
        L.push('【全局】修复成功率 ' + fmtPct(g.success_rate) + '（' + (g.successes || 0) + '/'
               + (g.records || 0) + ' 条），平均耗时 ' + (g.avg_duration_sec == null ? '-' : g.avg_duration_sec + 's')
               + '，项目数 ' + (g.projects || 0));
        (d.per_project || []).slice(0, 3).forEach(function (p) {
          L.push('  · ' + p.project_id + '：成功率 ' + fmtPct(p.success_rate) + '（' + p.records + ' 条）'
                 + (p.avg_duration_sec == null ? '' : '，均耗 ' + p.avg_duration_sec + 's'));
        });
        if ((d.top_errors || []).length) {
          L.push('【Top 高频错误】');
          d.top_errors.forEach(function (c, i) {
            L.push('  ' + (i + 1) + '. ' + c.cause_key + '（' + c.times + ' 次，成功率 ' + fmtPct(c.success_rate) + '）');
          });
        }
        if ((d.hardest_errors || []).length) {
          L.push('【最难错误】');
          d.hardest_errors.forEach(function (c, i) {
            L.push('  ' + (i + 1) + '. ' + c.cause_key + '（' + c.times + ' 次，成功率 ' + fmtPct(c.success_rate) + '）');
          });
        }
        var ex = d.experience || {};
        if (!ex.error) {
          L.push('【经验库】' + (ex.records || 0) + ' 条记录 · ' + (ex.projects || 0) + ' 项目 · '
                 + (ex.causes_total || 0) + ' 个根因簇' + (ex.global_causes ? ('（全局层 ' + ex.global_causes + '）') : ''));
        }
        var gt = (d.gates || {});
        if (!gt.error) {
          L.push('【进化策略】诊断优化 ' + (gt.strategy_optimize ? '开' : '关')
                 + ' · 修复自适应 ' + (gt.fix_adaptive ? '开' : '关')
                 + ' · 模式发现 ' + (gt.pattern_miner ? '开' : '关')
                 + '（自动生效 ' + (gt.pattern_miner_auto ? '开' : '关') + '）'
                 + ' · 跨项目经验 ' + (gt.experience_global ? '开' : '关'));
        }
        var st = d.strategy || {};
        if (st.top_weights && st.top_weights.length) {
          L.push('【诊断权重 Top3】');
          st.top_weights.slice(0, 3).forEach(function (w) {
            L.push('  · ' + w.cause_key + '：权重 ' + w.weight + '（反馈 ' + w.wins + '/' + w.tries + '）');
          });
        }
        if (st.skipped_causes && st.skipped_causes.length) {
          L.push('【已跳过（连败）】' + st.skipped_causes.map(function (s) { return s.cause_key; }).join('、'));
        }
        (d.fix_tiers || []).forEach(function (t) {
          L.push('【修复档位】' + t.project_id + ' · ' + t.label + '：' + t.wins + '/' + t.tries
                 + '（' + fmtPct(t.success_rate) + '）' + (t.consec_fail >= 2 ? '（连败 ' + t.consec_fail + '，将跳过）' : ''));
        });
        var pa = d.patterns || {};
        if (!pa.error && pa.total) {
          L.push('【模式发现】候选 ' + (pa.candidate || 0) + ' · 生效 ' + (pa.active || 0)
                 + ' · 衰减 ' + (pa.decayed || 0));
        }
        card.body.textContent = L.join('\n') || '暂无数据。';
      })
      .catch(function (e) {
        card.body.textContent = '调用 /api/self/stats 失败：' + e +
          '（提示：该路由随 MoRay 后端重启生效）';
      })
      .finally(function () {
        var host = msgHost();
        if (host) host.scrollTop = host.scrollHeight;
      });
  }

  /* ============ [L7.0 阶段2.3] 自治任务：确认卡 → 提交 → 轮询 ============ */

  function openPlannerConfirm(goal) {
    var card = newCard('自治任务确认');
    if (!card) return;
    var q = el('div', null, '我理解为一次全链路自治处理（自动规划子任务并执行）。是否继续？');
    q.setAttribute('style', 'margin-bottom:8px;');
    card.body.appendChild(q);
    var g = el('div', null, '目标：' + goal);
    g.setAttribute('style', 'color:var(--color-text-secondary,#9a9a9a);margin-bottom:10px;word-break:break-word;');
    card.body.appendChild(g);
    var row = el('div');
    row.setAttribute('style', 'display:flex;gap:8px;');
    var btnGo = el('button', null, '确认执行');
    btnGo.setAttribute('style', 'padding:5px 14px;border-radius:6px;border:1px solid rgba(255,255,255,.16);background:rgba(229,115,74,.18);color:#e8e8e8;cursor:pointer;font-size:12px;');
    var btnNo = el('button', null, '取消');
    btnNo.setAttribute('style', 'padding:5px 14px;border-radius:6px;border:1px solid rgba(255,255,255,.16);background:transparent;color:#9a9a9a;cursor:pointer;font-size:12px;');
    row.appendChild(btnGo);
    row.appendChild(btnNo);
    card.body.appendChild(row);
    btnNo.addEventListener('click', function () {
      card.card.firstChild.lastChild.textContent = '自治任务已取消';
      card.body.textContent = '未提交任何内容。';
    });
    btnGo.addEventListener('click', function () {
      btnGo.disabled = true;
      btnNo.disabled = true;
      submitAutonomous(goal, card);
    });
  }

  function submitAutonomous(goal, card) {
    card.card.firstChild.lastChild.textContent = '自治任务 · 规划中…';
    fetch(backendOrigin() + '/api/autonomous/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal: goal })
    }).then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }); })
      .then(function (res) {
        if (!res.j || !res.j.ok || !res.j.data || !res.j.data.plan_id) {
          card.card.firstChild.lastChild.textContent = '自治任务受理失败';
          card.body.textContent = (res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.s);
          return;
        }
        pollAutonomous(res.j.data.plan_id, card);
      })
      .catch(function (e) {
        card.card.firstChild.lastChild.textContent = '自治任务提交失败';
        card.body.textContent = '调用 /api/autonomous/run 失败：' + e +
          '（提示：该路由随 MoRay 后端重启生效；总闸 MORAY_TASK_PLANNER 默认关闭）';
      });
  }

  function pollAutonomous(planId, card) {
    var tries = 0;
    var timer = setInterval(function () {
      tries++;
      if (tries > 1440) {           // ~60 分钟兜底停表
        clearInterval(timer);
        return;
      }
      fetch(backendOrigin() + '/api/autonomous/' + encodeURIComponent(planId))
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || !j.ok || !j.data) return;
          var d = j.data;
          if (d.plan_source) {
            card.card.firstChild.lastChild.textContent =
              '自治任务（' + (d.status === 'completed' ? '已完成' : d.status === 'failed' ? '失败' : '执行中') + '）';
          }
          var L = [];
          (d.subtasks || []).forEach(function (s, i) {
            L.push((s.status === 'ok' ? '✅ ' : '❌ ') + (i + 1) + '. ' + (s.summary || s.title || s.type));
          });
          (d.decisions || []).slice(-4).forEach(function (x) {
            L.push('· 决策[' + (x.type || '') + '] ' + (x.reason || '') + ' → ' + (x.result || ''));
          });
          if (d.status === 'completed' || d.status === 'failed') {
            clearInterval(timer);
            if (d.report) {
              L.push('―― 最终报告（全文见 work/autonomous/' + planId + '/）――');
              L.push(String(d.report).slice(0, 2200));
            }
            if (d.status === 'failed' && d.error) L.push('错误：' + d.error);
          }
          card.body.textContent = L.join('\n') || '等待子任务启动…';
          var host = msgHost();
          if (host) host.scrollTop = host.scrollHeight;
        })
        .catch(function () {});
    }, 2500);
  }

  function tryWrap() {
    var orig = window.moraySendOverride;
    if (typeof orig !== 'function' || orig.__crash88) return;
    var wrapped = async function () {
      try {
        var inp = document.getElementById('chatInputNormal');
        var raw = ((inp && inp.value) || '').trim();
        var atts = (typeof AppState !== 'undefined' && AppState.attachments)
          || (window.AppState && window.AppState.attachments) || [];
        /* [L4] 图片 + 报错关键词 → 任务编排分支（先于原 log-intent 判定，无图消息行为不变）。
         * [L4.5 阶段2.1] 不再直接提交：先弹项目选择卡（选择后才执行、记忆上次默认选中）。
         * 任务在跑/选择卡未决时不接管（消息走正常发送，避免静默吞消息）。 */
        if (!busy && !taskBusy && !pickerActive && raw && raw.charAt(0) !== '/' && atts.length &&
            isErrFixEnabled() && ERRFIX_RE.test(raw)) {
          busy = true;
          var snap = atts.slice(0, 2);
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          renderUserEcho(raw, snap);
          openProjectPicker('errfix', raw, snap);   // 受理延迟到用户点"开始修复"
          busy = false;
          return;                  // 已接管：不落库、不进主 LLM（88 同款拦截语义）
        }
        /* [L4.5 阶段3.2] 无图 + 跑测试关键词 → test_run_flow（判定顺序 errfix→test→review→log-intent→LLM兜底） */
        if (!busy && !taskBusy && !pickerActive && raw && raw.charAt(0) !== '/' && !atts.length &&
            isTestEnabled() && TEST_RE.test(raw)) {
          busy = true;
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          renderUserEcho(raw);
          openProjectPicker('test', raw, null);
          busy = false;
          return;
        }
        /* [L5.0 阶段5] 无图 + 审查关键词 → code_review_flow */
        if (!busy && !taskBusy && !pickerActive && raw && raw.charAt(0) !== '/' && !atts.length &&
            isReviewEnabled() && REVIEW_RE.test(raw)) {
          busy = true;
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          renderUserEcho(raw);
          openProjectPicker('review', raw, null);
          busy = false;
          return;
        }
        /* [L6.0 阶段5] 无图 + 自我评估关键词 → GET /api/self/stats 文本卡（无项目选择，直接出） */
        if (!busy && !taskBusy && raw && raw.charAt(0) !== '/' && !atts.length &&
            isSelfStatsEnabled() && SELF_RE.test(raw)) {
          busy = true;
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          renderUserEcho(raw);
          analyzeSelfStats();
          busy = false;
          return;   // 已接管：不落库、不进主 LLM（88 同款拦截语义）
        }
        /* [L7.0 阶段2.3] 模糊大意图 → 自治引擎确认卡（总闸默认关；plannerOn 缓存探测，
         * 后端未开 = 本分支恒不命中，消息原样走正常发送，零影响）。确认后才提交
         * POST /api/autonomous/run（规划含 LLM，受理后轮询 /api/autonomous/{id}）。 */
        if (!busy && !taskBusy && !pickerActive && raw && raw.charAt(0) !== '/' && !atts.length &&
            isPlannerEnabled() && plannerOn && BIGGOAL_RE.test(raw)) {
          busy = true;
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          renderUserEcho(raw);
          openPlannerConfirm(raw);
          busy = false;
          return;   // 已接管（确认卡交互；用户取消即终止，不再触发任何发送）
        }
        if (!busy && raw && raw.charAt(0) !== '/' && isEnabled() && RE.test(raw)) {
          busy = true;
          if (inp) {
            inp.value = '';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          renderUserEcho(raw);
          await analyze(raw);
          busy = false;
          return; // 已接管：不落库、不进主 LLM（/订阅 先例的拦截位语义）
        }
        /* [L5.0 阶段2] LLM 意图兜底（最后一层，任务书 2.2）：关键词全没命中、但消息"模糊地
         * 像有任务意图"（VAGUE_RE 前闸，短消息、无附件、非 / 命令）→ 问一次对话 LLM 三分类。
         * fix_error → 无图报错修复（后端自动走 text_fix_flow）；run_tests → 跑测试；
         * general_chat/低置信/超时/异常 → 一律不拦截，输入框原样交回正常发送。
         * 分类期间输入框不动（用户所见不变），llmIntentPending 防连发。 */
        if (!llmIntentPending && !busy && !taskBusy && !pickerActive && raw && atts.length === 0 &&
            raw.charAt(0) !== '/' && raw.length <= 120 && isLlmIntentEnabled() && VAGUE_RE.test(raw)) {
          llmIntentPending = true;
          var label = null;
          try { label = await intentClassify(raw); } finally { llmIntentPending = false; }
          if (label === 'fix_error' || label === 'run_tests') {
            busy = true;
            if (inp) {
              inp.value = '';
              try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
            }
            try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
            renderUserEcho(raw);
            openProjectPicker(label === 'run_tests' ? 'test' : 'errfix', raw, null);
            busy = false;
            return;   // 已接管；general_chat / null 则继续向下走 orig 正常发送
          }
        }
      } catch (e) {
        busy = false;
        try { console.warn('[crash88] 接管异常，回退正常发送：', e); } catch (e2) {}
      }
      return orig.apply(this, arguments);
    };
    wrapped.__crash88 = true;
    window.moraySendOverride = wrapped;
  }

  function boot() {
    var n = 0;
    var t = setInterval(function () {
      n++;
      try { tryWrap(); } catch (e) {}
      if (window.moraySendOverride && window.moraySendOverride.__crash88) clearInterval(t);
      else if (n > 120) clearInterval(t); // 24s 兜底放弃（发送链路完全不受影响）
    }, 200);
    scheduleRestore();
    /* [L7.0] 自治引擎总闸探测：启动 3s 后首探 + 每 60s 刷新（闸默认关，探测失败=视为关）。 */
    setTimeout(probePlanner, 3000);
    setInterval(probePlanner, 60000);
  }

  /* ============ [L5.0 阶段4] 刷新恢复：running 任务重新挂卡继续轮询 ============ */

  function templateLabels(tpl) {
    if (tpl === 'test_run_flow') return ['测试完成', '测试任务失败'];
    if (tpl === 'code_review_flow') return ['审查完成', '审查任务失败'];
    return ['修复完成', '修复失败'];   // error_fix_flow / text_fix_flow（含串联体）
  }

  function restoreUnfinished() {
    if (restoring88) return;
    restoring88 = true;   // 只扫一次；失败也不重试（诚实：后端可能没有这个路由）
    fetch(backendOrigin() + '/api/tasks/unfinished')
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.ok || !j.data || !j.data.tasks || !j.data.tasks.length) return;
        var cid = currentConvId();
        j.data.tasks.forEach(function (d) {
          // 只恢复当前对话的任务卡（带归属但认不了主的一律跳过——不往别的对话里长卡）
          if (d.conversation_id && d.conversation_id !== cid) return;
          if (taskBusy) return;
          var lb = templateLabels(d.template);
          var card = newTaskCard('任务进行中（页面刷新已恢复） · ' + (d.current_stage || '准备中'));
          if (!card) return;
          card.proj = '';
          taskBusy = true;
          pollTask(card, d.task_id, Date.now(), null, lb[0], lb[1]);
        });
      })
      .catch(function () {});
  }

  function scheduleRestore() {
    var n = 0;
    var t = setInterval(function () {
      n++;
      var host = msgHost();
      if (host) { clearInterval(t); setTimeout(restoreUnfinished, 800); }
      else if (n > 40) clearInterval(t);   // 60s 内始终没有聊天页宿主（如落在应用中心）→ 放弃
    }, 1500);
  }

  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();
