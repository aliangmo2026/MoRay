/* ============================================================
 * [L7.7 2026-09-29] MoRay Agent 模式前端接入（任务书 一 / 三·前端优化）
 *
 * 纯加性运行时注入，零改动既有业务代码：
 *   1) 顶栏 #chatModeSwitch 右侧注入「Agent模式」切换按钮（默认关，localStorage 记忆）；
 *   2) Agent 模式下发送 → 不走普通聊天，改调 Laya POST /execute（SSE 流），
 *      用户目标照常落库进消息流；final 帧自动作为 AI 回复插入聊天；
 *   3) 右侧固定宽度执行面板（覆盖式定位，不动原 flex 布局）：
 *      状态 + 总进度条 + 步骤列表（等待灰/执行中黄呼吸/完成绿/失败红、
 *      "正在重试第 N 次"、已完成可展开中间结果）+ 耗时 + 取消执行；
 *   4) 面板可一键折叠成侧边小标签；SSE 意外断开弹提示；
 *   5) 最终结果一键复制 / 导出本地文件；输入框下方 Agent 模式快捷任务标签。
 *   6) [输出体验] 步骤详情/最终结果按工具类型结构化渲染（file=可折叠目录树+文件大小、
 *      argus=标题/链接/摘要卡片、zcode=代码高亮+等宽日志、失败=红色错误块）；
 *      聊天只留 状态+短结论+「查看详情」（点击展开面板并定位最终结果），
 *      完整数据只在右侧面板；每个结果区右下角「查看原始 JSON」切换（默认隐藏）。
 *
 * 接口契约（Laya 8001，L7.6+L7.7）：
 *   POST /execute {goal, max_retry} → SSE data: 帧 {event: start|step_start|step_result|
 *   check|retry|final, step_id, tool, description, status, data, error, attempt,
 *   retry_count, ts(ms), execution_id, ...}
 *   POST /execute/cancel {execution_id} → {code:0, cancelled:bool}
 *   服务地址 localStorage 'moray_laya_base' 可覆盖，默认 http://localhost:8001。
 *   7) [U-6-1 2026-10-01] 代码意图直调 ZCode：decideMode 扩展返回 'code'（mode=agent 且
 *      intent_type ∈ code/file_op/tool_call），新增 runZCodeInChat + renderExecutionCard——
 *      ZCode 沙箱任务全过程（思考/模型输出/工具时间线/文件清单）以执行卡片渲染进主对话流
 *      （msg.kind='zcode-execution'，状态存 msg.execution 走 IndexedDB 持久化与重建恢复）。
 *   8) [U-7-4 2026-10-02] 'code' 分支发送前路径存在性闸：抽取输入中的绝对路径 → GET
 *      /api/agent/path_exists 查存在（20s 短缓存；2026-10-03 旧链退役，自 agent_runner
 *      preflight 迁入独立端点，响应契约 data.path_exists 布尔不变）→ 有不存在的弹三按钮确认框
 *      （重新输入/仍然继续/取消，复用 20_ai showModal 零改动）；「仍然继续」在 prompt 尾
 *      附加系统提示，禁止模型对缺失路径就近猜改（U-7-0 实锤）。fail-open：path_exists
 *      不可达/超时/结构不符一律放行，不阻塞正常发送。
 * ============================================================ */
(function () {
  'use strict';

  var LS_MODE = 'moray_agent_mode';
  var LS_BASE = 'moray_laya_base';
  var DEFAULT_BASE = 'http://localhost:8001';
  var MAX_RETRY = 5;

  var busy = false;          // 一次只跑一个执行
  var state = null;          // 当前执行状态快照
  var timerId = null;        // 耗时刷新计时器
  var abortCtrl = null;      // SSE AbortController

  function agentMode() {
    var v = localStorage.getItem(LS_MODE);
    if (v === '1') return 'on';
    if (v === 'auto') return 'auto';
    return 'off';
  }
  function agentOn() { return agentMode() === 'on'; }   // 兼容包装：旧调用点语义不变（仅 'on' 为 true）

  // [L7.8 前端 2026-10-01] auto 模式意图判定：调 8001 /api/intent/decide（超时 1500ms），失败降级 'chat'
  async function decideMode(raw) {
    /* 调 8001 /api/intent/decide，超时 1500ms，失败降级 'chat'；
     * [U-6-1] 返回扩展为 'chat' | 'agent' | 'code'（intent_type 分流见下方） */
    try {
      var ctrl = new AbortController();
      var timer = setTimeout(function () { ctrl.abort(); }, 1500);
      var resp = await fetch(layaBase() + '/api/intent/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: raw, history: [], attachments: [], explicit_mode: null }),
        signal: ctrl.signal
      });
      clearTimeout(timer);
      if (!resp.ok) return 'chat';
      var j = await resp.json();
      if (j && j.mode === 'agent') {
        /* [U-6-1] 代码/文件操作/工具调用意图 → 'code'：直调 ZCode 沙箱（执行卡片渲染进
         * 主对话流）；其余 agent 意图维持 'agent'（Laya 拆解执行）。'chat'/'agent' 原语义不变。 */
        var it = String(j.intent_type || '');
        // [2026-10-01 U-6-5] 搜索类任务落回普通聊天：
        // Laya 链的 tool_registry._call_argus 硬编码 search_web，
        // query 直传 Bing 质量差（"2026年"抢主关键词）。
        // 落回聊天后主模型可自主调 detective_research 工具（深度研究），
        // 与"Cursor 价格"走的路径一致（U-6-5 侦察实锤）。
        // 代价：主模型可能不调工具直接回答，取决于其对任务的判断。
        if (it === 'search') return 'chat';
        if (it === 'code' || it === 'file_op' || it === 'tool_call') return 'code';
        return 'agent';
      }
      return 'chat';
    } catch (e) {
      return 'chat';   // 超时/网络失败/JSON 解析失败 一律降级 chat
    }
  }
  window.morayDecideMode = decideMode;   // 手工验收入口
  window.morayAgentMode = agentMode;
  function layaBase() { return (localStorage.getItem(LS_BASE) || DEFAULT_BASE).replace(/\/+$/, ''); }
  function esc(s) { var d = document.createElement('div'); d.textContent = String(s == null ? '' : s); return d.innerHTML; }
  function attr(s) { return esc(s).replace(/"/g, '&quot;'); }   // 用于 HTML 属性位（innerHTML 序列化不转义双引号）

  /* ---------------- [输出体验] 数据解析 / 小工具 ---------------- */
  function parseLoose(s) {
    if (s == null) return { ok: false, val: '' };
    if (typeof s !== 'string') return { ok: true, val: s };
    try { return { ok: true, val: JSON.parse(s) }; } catch (e) { return { ok: false, val: s }; }
  }
  function fmtBytes(n) {
    var v = Number(n);
    if (!isFinite(v) || v < 0) return '';
    if (v < 1024) return v + ' B';
    if (v < 1048576) return (v / 1024).toFixed(1) + ' KB';
    if (v < 1073741824) return (v / 1048576).toFixed(1) + ' MB';
    return (v / 1073741824).toFixed(2) + ' GB';
  }
  function mdPlain(s) { return String(s == null ? '' : s).replace(/[`*_~#[\]>]/g, ''); }
  function clampStr(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n) + '…' : s; }
  function highlightCode(code) {
    var src = String(code || '');
    try {
      if (typeof hljs !== 'undefined' && hljs.highlight) {
        try { return hljs.highlight(src, { language: 'python' }).value; } catch (e1) {}
        if (hljs.highlightAuto) return String(hljs.highlightAuto(src).value || esc(src));
      }
    } catch (e2) {}
    return esc(src);
  }

  /* ---------------- 样式（一次性注入；只碰自有类名，不动既有选择器） ---------------- */
  var STYLE = [
    '.laya-agent-btn{margin-left:6px;padding:4px 10px;border-radius:9px;font-size:12px;cursor:pointer;',
    'border:1px solid var(--color-line-ghost,rgba(255,255,255,.1));background:transparent;color:var(--color-text-secondary,#9ca3af);',
    'display:inline-flex;align-items:center;gap:4px;transition:all .18s;line-height:1.4}',
    '.laya-agent-btn:hover{border-color:rgba(229,115,74,.45)}',
    '.laya-agent-btn.on{background:rgba(229,115,74,.16);color:#E5734A;border-color:rgba(229,115,74,.5)}',
    '.laya-agent-btn.auto{background:rgba(59,130,246,.16);color:#3B82F6;border-color:rgba(59,130,246,.5)}',
    '#layaExecPanel{position:fixed;right:14px;top:64px;bottom:16px;width:352px;z-index:1200;',
    'display:none;flex-direction:column;background:rgba(20,20,20,.97);border:1px solid #2a2a2a;border-radius:12px;',
    'box-shadow:0 12px 40px rgba(0,0,0,.5);font-size:12px;color:#d4d4d4;overflow:hidden}',
    '#layaExecPanel.show{display:flex}',
    '#layaExecPanel.collapsed{display:none}',
    '.laya-reopen{position:fixed;right:14px;top:64px;z-index:1200;display:none;padding:8px 10px;',
    'background:rgba(20,20,20,.95);border:1px solid #2a2a2a;border-radius:10px;cursor:pointer;color:#E5734A;font-size:14px}',
    '.laya-reopen.show{display:block}',
    '.laya-p-head{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #2a2a2a;flex-shrink:0}',
    '.laya-p-title{font-weight:600;color:#e8e8e8}',
    '.laya-p-status{font-size:11px;padding:2px 8px;border-radius:999px}',
    '.laya-st-run{background:rgba(251,191,36,.15);color:#FBBF24}',
    '.laya-st-ok{background:rgba(74,222,128,.15);color:#4ade80}',
    '.laya-st-fail{background:rgba(248,113,113,.15);color:#f87171}',
    '.laya-st-cancel{background:rgba(156,163,175,.15);color:#9ca3af}',
    '.laya-p-collapse{margin-left:auto;background:none;border:none;color:#8a8a8a;cursor:pointer;font-size:14px;padding:2px 4px}',
    '.laya-p-prog{height:4px;background:#242424;flex-shrink:0}',
    '.laya-p-prog i{display:block;height:100%;width:0;background:linear-gradient(90deg,#E5734A,#FBBF24);transition:width .3s}',
    '.laya-p-steps{flex:1;min-height:0;overflow-y:auto;padding:8px 10px;display:flex;flex-direction:column;gap:6px}',
    '.laya-step{border:1px solid #262626;border-radius:9px;padding:7px 9px;background:rgba(255,255,255,.02)}',
    '.laya-step .row{display:flex;align-items:center;gap:7px;cursor:default}',
    '.laya-step.expandable .row{cursor:pointer}',
    '.laya-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;background:#555}',
    '.laya-step.running .laya-dot{background:#FBBF24;animation:layaBreathe 1.2s ease-in-out infinite}',
    '.laya-step.done .laya-dot{background:#4ade80}',
    '.laya-step.failed .laya-dot{background:#f87171}',
    '@keyframes layaBreathe{0%,100%{opacity:.35;transform:scale(.85)}50%{opacity:1;transform:scale(1.15)}}',
    '.laya-step .tool{color:#8a8a8a;flex-shrink:0;display:inline-flex}',
    '.laya-step .desc{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.laya-step .retry-badge{color:#FBBF24;font-size:10px;flex-shrink:0}',
    '.laya-step .detail{display:none;margin-top:6px;padding:6px 8px;background:#181818;border-radius:6px;',
    'white-space:pre-wrap;word-break:break-all;max-height:160px;overflow:auto;color:#a8a8a8;font-size:11px}',
    '.laya-step.open .detail{display:block}',
    '.laya-p-foot{border-top:1px solid #2a2a2a;padding:8px 12px;display:flex;align-items:center;gap:8px;flex-shrink:0}',
    '.laya-p-elapsed{color:#8a8a8a;font-variant-numeric:tabular-nums}',
    '.laya-p-actions{margin-left:auto;display:flex;gap:6px}',
    '.laya-btn{padding:3px 10px;border-radius:8px;border:1px solid #333;background:rgba(255,255,255,.04);color:#d4d4d4;font-size:11px;cursor:pointer}',
    '.laya-btn:hover{border-color:#E5734A;color:#E5734A}',
    '.laya-btn.danger{border-color:rgba(248,113,113,.4);color:#f87171}',
    '.laya-btn.danger:hover{background:rgba(248,113,113,.12)}',
    '.laya-btn[hidden]{display:none}',
    '#layaAgentTags{display:none;gap:6px;flex-wrap:wrap;padding:6px 2px 0}',
    '#layaAgentTags.show{display:flex}',
    '.laya-tag{padding:3px 10px;border-radius:999px;border:1px solid #2e2e2e;background:rgba(229,115,74,.07);',
    'color:#c9a18c;font-size:11px;cursor:pointer;white-space:nowrap}',
    '.laya-tag:hover{border-color:rgba(229,115,74,.5);color:#E5734A}',
    /* ---- [输出体验] 结果格式化层 ---- */
    '.laya-res{min-width:0}',
    '.laya-res [hidden]{display:none!important}',
    '.laya-res-foot{display:flex;justify-content:flex-end;margin-top:4px}',
    '.laya-raw-btn{background:none;border:1px solid #2c2c2c;border-radius:6px;color:#6f6f6f;font-size:10px;padding:1px 8px;cursor:pointer}',
    '.laya-raw-btn:hover{border-color:rgba(229,115,74,.45);color:#E5734A}',
    '.laya-res-raw{margin:6px 0 0;padding:8px;background:#141414;border:1px solid #262626;border-radius:6px;white-space:pre-wrap;word-break:break-all;max-height:220px;overflow:auto;color:#9a9a9a;font-size:11px;line-height:1.5;font-family:Consolas,Menlo,monospace}',
    '.laya-cap{display:flex;align-items:center;gap:6px;color:#8a8a8a;font-size:11px;margin:8px 0 4px}',
    '.laya-cap .cnt{margin-left:auto;color:#6f6f6f;flex-shrink:0}',
    '.laya-cap .pth{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.laya-tree{border:1px solid #262626;border-radius:8px;overflow:hidden}',
    '.laya-tree-head{display:flex;align-items:center;gap:6px;padding:7px 9px;background:rgba(255,255,255,.03);cursor:pointer;color:#d4d4d4}',
    '.laya-tree-head:hover{background:rgba(255,255,255,.05)}',
    '.laya-tree-head .pth{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}',
    '.laya-tree-head .cnt{color:#6f6f6f;font-size:10px;flex-shrink:0}',
    '.laya-tree-head .chev{color:#6f6f6f;transition:transform .18s;flex-shrink:0}',
    '.laya-tree-head.folded .chev{transform:rotate(-90deg)}',
    '.laya-tree-body{display:flex;flex-direction:column}',
    '.laya-tree-body.closed{display:none}',
    '.laya-tree-row{display:flex;align-items:center;gap:7px;padding:5px 9px 5px 14px;border-top:1px solid #222;color:#c4c4c4;font-size:11px;min-width:0}',
    '.laya-tree-row i,.laya-tree-row svg{flex-shrink:0;color:#8a8a8a}',
    '.laya-tree-row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.laya-tree-row .sz{color:#6f6f6f;font-size:10px;flex-shrink:0;font-variant-numeric:tabular-nums}',
    '.laya-tree-row.is-dir{color:#e0c9a8}',
    '.laya-tree-row.is-dir i,.laya-tree-row.is-dir svg{color:#c9a18c}',
    '.laya-search{display:flex;flex-direction:column;gap:6px}',
    '.laya-search-q{display:flex;align-items:center;gap:6px;color:#8a8a8a;font-size:11px;min-width:0}',
    '.laya-search-q .cnt{margin-left:auto;color:#6f6f6f;flex-shrink:0}',
    '.laya-sr{border:1px solid #262626;border-radius:8px;padding:7px 9px;background:rgba(255,255,255,.02);min-width:0}',
    '.laya-sr .tt{color:#E5734A;text-decoration:none;font-size:12px;font-weight:500;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.laya-sr .tt:hover{text-decoration:underline}',
    '.laya-sr .tt-plain{color:#d4d4d4;font-size:12px;font-weight:500;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.laya-sr .snip{color:#9a9a9a;font-size:11px;line-height:1.5;margin-top:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}',
    '.laya-code,.laya-log{margin:0;padding:8px 10px;background:#141414;border:1px solid #262626;border-radius:6px;overflow:auto;max-height:240px;font-family:Consolas,Menlo,monospace;font-size:11px;line-height:1.55;white-space:pre-wrap;word-break:break-all;color:#c8c8c8}',
    '.laya-code{white-space:pre;word-break:normal}',
    '.laya-zmeta{display:flex;align-items:center;gap:6px}',
    '.laya-exit{font-size:10px;padding:2px 8px;border-radius:999px;background:rgba(74,222,128,.15);color:#4ade80}',
    '.laya-exit.bad{background:rgba(248,113,113,.15);color:#f87171}',
    '.laya-exit.neutral{background:rgba(156,163,175,.12);color:#9ca3af}',
    '.laya-err{background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.35);border-radius:8px;padding:8px 10px}',
    '.laya-err .h{display:flex;align-items:center;gap:6px;font-weight:600;font-size:12px;color:#f87171;margin-bottom:3px}',
    '.laya-err .m{white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.55;color:#e8b4b4}',
    '.laya-txt{white-space:pre-wrap;word-break:break-word;color:#c4c4c4;font-size:11px;line-height:1.6;min-width:0}',
    '.laya-txt i,.laya-txt svg{margin-right:6px;color:#8a8a8a}',
    '.laya-kv{display:flex;gap:8px;padding:3px 0;font-size:11px;border-bottom:1px dashed #242424;min-width:0}',
    '.laya-kv:last-child{border-bottom:none}',
    '.laya-kv .k{color:#8a8a8a;flex-shrink:0}',
    '.laya-kv .v{color:#c4c4c4;min-width:0;word-break:break-all}',
    '.laya-final{border-top:1px dashed #3a3a3a;margin-top:8px;padding-top:8px}',
    '.laya-final-h{font-weight:600;color:#e8e8e8;font-size:12px;margin-bottom:6px}',
    '.laya-flash{animation:layaFlash 1.4s ease 1}',
    '@keyframes layaFlash{0%{background:rgba(229,115,74,.22)}100%{background:transparent}}',
    '.laya-chat-detail{margin-top:10px;padding:3px 12px;border-radius:8px;font-size:11px;cursor:pointer;background:transparent;',
    'border:1px solid var(--color-line-ghost,rgba(127,127,127,.3));color:var(--color-text-secondary,#9ca3af);transition:all .18s}',
    '.laya-chat-detail:hover{border-color:rgba(229,115,74,.5);color:#E5734A}',
    '.laya-code .hljs-keyword,.laya-code .hljs-selector-tag,.laya-code .hljs-meta{color:#c792ea}',
    '.laya-code .hljs-string,.laya-code .hljs-attr{color:#a5d6a7}',
    '.laya-code .hljs-number,.laya-code .hljs-literal{color:#f7bd7f}',
    '.laya-code .hljs-comment{color:#6a6a6a;font-style:italic}',
    '.laya-code .hljs-title,.laya-code .hljs-name,.laya-code .hljs-function{color:#7fb3e8}',
    '.laya-code .hljs-built_in,.laya-code .hljs-type{color:#82d8d8}'
  ].join('\n');

  function injectStyle() {
    if (document.getElementById('layaAgentStyle')) return;
    var st = document.createElement('style');
    st.id = 'layaAgentStyle';
    st.textContent = STYLE;
    document.head.appendChild(st);
  }

  /* ---------------- 顶栏 Agent 按钮 ---------------- */
  var agentBtn = null;
  function applyAgentBtnVisual(btn) {
    var m = agentMode();
    btn.className = 'laya-agent-btn' + (m === 'on' ? ' on' : (m === 'auto' ? ' auto' : ''));
    var label = m === 'on' ? 'Agent 开' : (m === 'auto' ? 'Agent 自动' : 'Agent 关');
    btn.innerHTML = '<i data-lucide="bot" class="w-3.5 h-3.5"></i><span>' + label + '</span>';
  }
  function ensureButton() {
    if (agentBtn && agentBtn.isConnected) return;
    var seg = document.getElementById('chatModeSwitch');
    if (!seg || !seg.parentNode) return;
    agentBtn = document.createElement('button');
    agentBtn.type = 'button';
    agentBtn.title = 'Agent 模式三态：关=全部聊天；自动=由意图判定分流（复杂任务走 Laya）；开=全部交 Laya 拆解执行';
    applyAgentBtnVisual(agentBtn);
    agentBtn.addEventListener('click', function () {
      var cur = agentMode();
      var next = cur === 'off' ? 'auto' : (cur === 'auto' ? 'on' : 'off');
      localStorage.setItem(LS_MODE, next === 'on' ? '1' : (next === 'auto' ? 'auto' : '0'));
      applyAgentBtnVisual(agentBtn);
      toggleTags(next === 'on');
      if (next === 'auto') {
        showNotification('Agent 自动模式已开启', '复杂任务自动切换 Agent，普通对话走本地聊天', 'info', 2600);
      } else if (next === 'on') {
        showNotification('Agent 模式已开启', '接下来发送的目标将由 Laya 拆解执行（' + layaBase() + '）', 'info', 2600);
      } else {
        showNotification('已回到普通对话', '所有输入走本地聊天', 'info', 2000);
      }
      try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
    });
    seg.parentNode.insertBefore(agentBtn, seg.nextSibling);
    try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
  }

  /* ---------------- 快捷任务标签（输入框下方） ---------------- */
  var TAGS = ['列出D盘MoRay目录的文件', '搜索最新大模型微调资讯', '读取日志统计报错数量'];
  var tagsRow = null;
  function ensureTags() {
    if (tagsRow && tagsRow.isConnected) return;
    var dock = document.getElementById('coreInputContainer');
    if (!dock || !dock.parentElement) return;
    tagsRow = document.createElement('div');
    tagsRow.id = 'layaAgentTags';
    TAGS.forEach(function (t) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'laya-tag';
      b.textContent = t;
      b.addEventListener('click', function () {
        var inp = document.getElementById('chatInputNormal');
        if (!inp) return;
        inp.value = t;
        try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
        inp.focus();
      });
      tagsRow.appendChild(b);
    });
    dock.parentElement.appendChild(tagsRow);
    tagsRow.classList.toggle('show', agentOn());
  }
  function toggleTags(on) { if (tagsRow) tagsRow.classList.toggle('show', !!on); }

  /* ---------------- 执行面板 ---------------- */
  var panel = null, reopen = null;
  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'layaExecPanel';
    panel.innerHTML = [
      '<div class="laya-p-head">',
      '  <span class="laya-p-title">⚡ Agent 执行</span>',
      '  <span class="laya-p-status laya-st-run" id="layaPStatus">执行中</span>',
      '  <button class="laya-p-collapse" id="layaPCollapse" title="折叠面板">⟩</button>',
      '</div>',
      '<div class="laya-p-prog"><i id="layaPProg"></i></div>',
      '<div class="laya-p-steps" id="layaPSteps"></div>',
      '<div class="laya-p-foot">',
      '  <span class="laya-p-elapsed" id="layaPElapsed">0.0s</span>',
      '  <span class="laya-p-actions">',
      '    <button class="laya-btn" id="layaPCopy" hidden title="复制最终结果">复制</button>',
      '    <button class="laya-btn" id="layaPExport" hidden title="导出为本地文件">导出</button>',
      '    <button class="laya-btn danger" id="layaPCancel" hidden>取消执行</button>',
      '  </span>',
      '</div>'
    ].join('');
    document.body.appendChild(panel);
    reopen = document.createElement('button');
    reopen.className = 'laya-reopen';
    reopen.type = 'button';
    reopen.title = '展开执行面板';
    reopen.textContent = '⚡';
    reopen.addEventListener('click', function () {
      panel.classList.remove('collapsed');
      reopen.classList.remove('show');
    });
    document.body.appendChild(reopen);

    document.getElementById('layaPCollapse').addEventListener('click', function () {
      panel.classList.add('collapsed');
      reopen.classList.add('show');
    });
    document.getElementById('layaPCancel').addEventListener('click', function () {
      if (!state) return;
      var execId = state.executionId;   // start 帧未到（8001 规划阶段）时服务端尚无登记 id，
      var stoppingLocal = !!abortCtrl;  // 本地 SSE fetch 是否仍在飞
      if (execId) fetch(layaBase() + '/execute/cancel', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ execution_id: execId })
      }).then(function (r) { return r.json(); })
        .then(function (j) {
          if (j && j.code === 0 && j.cancelled) {
            showNotification('已请求取消', '将在当前步骤边界生效', 'info', 2200);
          } else if (!stoppingLocal) {
            showNotification('取消未生效', (j && j.error) || '执行可能已结束或 id 不匹配', 'warning', 2600);
          }
          // stoppingLocal 且未命中：本地流已断、服务端流式任务随之被连坐取消，
          // /execute/cancel 只是幂等收尾通道，未命中不是失败，不弹误导通知
        })
        .catch(function () {
          showNotification('取消请求失败', stoppingLocal ? '无法连接 Laya 服务（本地执行流已中止）' : '无法连接 Laya 服务', 'error', 3000);
        });
      /* [停止修复 2026-10-03] abort 断本地 SSE fetch：断连使 starlette 取消 /execute 流式任务，
       * CancelledError 传导进当前步骤 tool/decision 模型调用的 await 点（在飞 httpx 请求随之终止），
       * Ollama/llama-server 因连接关闭停止生成——不再是"只在步骤边界响应 cancel"地烧算力。
       * 上方服务端 cancel 仍保留：步骤边界/重试前检查与 _ACTIVE 清理的干净收尾通道，两者并行。 */
      if (stoppingLocal) { try { abortCtrl.abort(); } catch (e) { /* abort 异常不影响服务端 cancel 通道 */ } }
    });
    document.getElementById('layaPCopy').addEventListener('click', function () {
      copyText(state && state.finalText ? state.finalText : '');
    });
    document.getElementById('layaPExport').addEventListener('click', function () {
      exportText(state && state.finalText ? state.finalText : '');
    });
    return panel;
  }

  function panelOpen(goal) {
    ensurePanel();
    panel.classList.add('show');
    panel.classList.remove('collapsed');
    reopen.classList.remove('show');
    document.getElementById('layaPSteps').innerHTML = '';
    setStepStatus('执行中', 'run');
    document.getElementById('layaPProg').style.width = '0%';
    document.getElementById('layaPCancel').hidden = false;
    document.getElementById('layaPCopy').hidden = true;
    document.getElementById('layaPExport').hidden = true;
    var el = document.getElementById('layaPElapsed'); if (el) el.textContent = '0.0s';
    var title = panel.querySelector('.laya-p-title');
    if (title) title.textContent = '⚡ ' + (goal.length > 20 ? goal.slice(0, 20) + '…' : goal);
  }
  function setStepStatus(text, kind) {
    var s = document.getElementById('layaPStatus');
    if (!s) return;
    s.textContent = text;
    s.className = 'laya-p-status laya-st-' + kind;
  }

  var TOOL_ICON = { iris: 'image', argus: 'search', zcode: 'code-2', file: 'file-text' };
  function stepRow(stepId, tool, desc) {
    var wrap = document.createElement('div');
    wrap.className = 'laya-step';
    wrap.dataset.stepId = String(stepId);
    wrap.innerHTML = '<div class="row"><span class="laya-dot"></span>' +
      '<span class="step-no" style="color:#8a8a8a;flex-shrink:0">#' + stepId + '</span>' +
      '<span class="tool"><i data-lucide="' + (TOOL_ICON[tool] || 'wrench') + '" class="w-3.5 h-3.5"></i></span>' +
      '<span class="desc" title="' + esc(desc) + '">' + esc(desc || tool) + '</span>' +
      '<span class="retry-badge"></span></div>' +
      '<div class="detail"></div>';
    wrap.querySelector('.row').addEventListener('click', function () {
      if (wrap.classList.contains('expandable')) wrap.classList.toggle('open');
    });
    return wrap;
  }
  function getRow(stepId) {
    return document.querySelector('#layaPSteps .laya-step[data-step-id="' + stepId + '"]');
  }
  function rowDetail(stepId, text) {
    var r = getRow(stepId); if (!r) return;
    var d = r.querySelector('.detail'); d.textContent = text;
    r.classList.add('expandable');
  }
  function rowMark(stepId, cls, retryText) {
    var r = getRow(stepId); if (!r) return;
    r.classList.remove('running', 'done', 'failed');
    if (cls) r.classList.add(cls);
    if (retryText !== undefined) r.querySelector('.retry-badge').textContent = retryText;
  }
  function updateProgress() {
    if (!state || !state.total) return;
    var pct = Math.min(100, Math.round(state.doneCount / state.total * 100));
    if (state.finished) pct = 100;
    var bar = document.getElementById('layaPProg'); if (bar) bar.style.width = pct + '%';
  }

  /* ---------------- [输出体验] 结果格式化层 ----------------
   * 数据形状（L7.6 工具信封，step_result.data / final.result 都是 _shrink 后的 JSON 字符串）：
   *   file  list_dir = {dir,count,entries:[{name,is_dir,size|null}]}；read = {path,content,size}；write = {path,written}
   *   argus search  = {query,count,results:[{title,url,snippet}]}
   *   zcode         = {output,exit_code}（stdout 等字段如实兼容）；代码原文取 step_start 事件里的 input.code
   *   iris / 未知   = 自由对象 → 文本 / 键值 / 缩进 JSON 兜底
   * 每个结果区 = 格式化主视图（默认）+ 原始 JSON（默认隐藏）+ 右下角切换按钮。 */

  function buildRawable(mainEl, parsed) {
    var wrap = document.createElement('div'); wrap.className = 'laya-res';
    if (mainEl) { mainEl.classList.add('laya-res-main'); wrap.appendChild(mainEl); }
    var raw = document.createElement('pre');
    raw.className = 'laya-res-raw'; raw.hidden = true;
    raw.textContent = (parsed && parsed.ok && parsed.val != null && typeof parsed.val === 'object')
      ? JSON.stringify(parsed.val, null, 2) : String(parsed && parsed.val != null ? parsed.val : '');
    wrap.appendChild(raw);
    var foot = document.createElement('div'); foot.className = 'laya-res-foot';
    var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'laya-raw-btn';
    btn.textContent = '查看原始 JSON';
    btn.addEventListener('click', function () {
      var showRaw = raw.hidden;
      raw.hidden = !showRaw;
      if (mainEl) mainEl.style.display = showRaw ? 'none' : '';
      btn.textContent = showRaw ? '收起原始 JSON' : '查看原始 JSON';
    });
    foot.appendChild(btn); wrap.appendChild(foot);
    return wrap;
  }

  function buildTextView(s) {
    var v = document.createElement('div'); v.className = 'laya-txt';
    v.textContent = String(s == null ? '' : s);
    return v;
  }

  function buildErrorBlock(err) {
    var e = document.createElement('div'); e.className = 'laya-err';
    var code = err && err.code != null ? err.code : null;
    var msg = err && err.message ? String(err.message) : String(err || '未知错误');
    e.innerHTML = '<div class="h"><i data-lucide="alert-triangle" class="w-3.5 h-3.5"></i><span>执行出错' +
      (code != null ? ' · 代码 ' + esc(String(code)) : '') + '</span></div><div class="m"></div>';
    e.querySelector('.m').textContent = msg;
    return e;
  }

  function buildFileView(p) {
    var d = p.val;
    if (!p.ok || !d || typeof d !== 'object') return buildTextView(p.val);
    if (Array.isArray(d.entries)) {          // list_dir：单层目录树（服务端只回一层，如实渲染）
      var root = document.createElement('div'); root.className = 'laya-tree';
      var head = document.createElement('div'); head.className = 'laya-tree-head';
      head.innerHTML = '<i data-lucide="folder" class="w-3.5 h-3.5"></i>' +
        '<span class="pth" title="' + attr(String(d.dir || '')) + '">' + esc(String(d.dir || '目录')) + '</span>' +
        '<span class="cnt">' + (d.count != null ? d.count : d.entries.length) + ' 项</span>' +
        '<i data-lucide="chevron-down" class="w-3.5 h-3.5 chev"></i>';
      var body = document.createElement('div'); body.className = 'laya-tree-body';
      d.entries.slice()
        .sort(function (a, b) { return (b.is_dir - a.is_dir) || String(a.name).localeCompare(String(b.name)); })
        .forEach(function (en) {
          var r = document.createElement('div');
          r.className = 'laya-tree-row' + (en && en.is_dir ? ' is-dir' : '');
          r.innerHTML = '<i data-lucide="' + (en && en.is_dir ? 'folder' : 'file-text') + '" class="w-3.5 h-3.5"></i>' +
            '<span class="nm" title="' + attr(String(en && en.name || '')) + '">' + esc(String(en && en.name || '')) + '</span>' +
            '<span class="sz">' + (en && en.is_dir ? '文件夹' : (en && en.size != null ? fmtBytes(en.size) : '')) + '</span>';
          body.appendChild(r);
        });
      head.addEventListener('click', function () { body.classList.toggle('closed'); head.classList.toggle('folded'); });
      root.appendChild(head); root.appendChild(body);
      return root;
    }
    if (typeof d.content === 'string') {     // read：文件内容等宽预览
      var w = document.createElement('div'); w.className = 'laya-file-read';
      var meta = document.createElement('div'); meta.className = 'laya-cap';
      meta.innerHTML = '<i data-lucide="file-text" class="w-3.5 h-3.5"></i>' +
        '<span class="pth" title="' + attr(String(d.path || '')) + '">' + esc(String(d.path || '文件')) + '</span>' +
        '<span class="cnt">' + fmtBytes(d.size != null ? d.size : d.content.length) + '</span>';
      var pre = document.createElement('pre'); pre.className = 'laya-log';
      pre.textContent = d.content.length > 6000 ? d.content.slice(0, 6000) + '\n…（已截断）' : d.content;
      w.appendChild(meta); w.appendChild(pre);
      return w;
    }
    if (d.written != null) {                 // write：落盘回执
      var v = document.createElement('div'); v.className = 'laya-txt';
      v.innerHTML = '<i data-lucide="file-text" class="w-3.5 h-3.5"></i><span>已写入 ' +
        esc(String(d.path || '')) + '（' + esc(String(d.written)) + ' 字符）</span>';
      return v;
    }
    return buildGenericView(p);
  }

  function buildArgusView(p) {
    var d = p.val;
    var list = null, q = '';
    if (p.ok && d && typeof d === 'object' && Array.isArray(d.results)) { list = d.results; q = String(d.query || ''); }
    else if (p.ok && Array.isArray(d)) { list = d; }
    if (!list) return buildTextView(p.val);
    var root = document.createElement('div'); root.className = 'laya-search';
    var qEl = document.createElement('div'); qEl.className = 'laya-search-q';
    qEl.innerHTML = '<i data-lucide="search" class="w-3.5 h-3.5"></i><span>' + esc(q || '搜索结果') + '</span>' +
      '<span class="cnt">' + (d && d.count != null ? d.count : list.length) + ' 条</span>';
    root.appendChild(qEl);
    list.forEach(function (r) {
      var card = document.createElement('div'); card.className = 'laya-sr';
      var url = String(r && r.url || '');
      var title = String(r && r.title || '') || url || '（无标题）';
      if (/^https?:\/\//i.test(url)) {       // 只放行 http(s)，杜绝 javascript: 之类注入
        var a = document.createElement('a');
        a.className = 'tt'; a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.title = url; a.textContent = title;
        card.appendChild(a);
      } else {
        var sp = document.createElement('span'); sp.className = 'tt-plain'; sp.textContent = title;
        card.appendChild(sp);
      }
      var sn = String(r && r.snippet || '');
      if (sn) { var sd = document.createElement('div'); sd.className = 'snip'; sd.textContent = sn; card.appendChild(sd); }
      root.appendChild(card);
    });
    return root;
  }

  function buildZcodeView(p, codeText) {
    var d = p.val;
    var root = document.createElement('div'); root.className = 'laya-zcode';
    var out = (p.ok && d && typeof d === 'object')
      ? String(d.output != null ? d.output : (d.stdout != null ? d.stdout : ''))
      : String(p.ok ? (d == null ? '' : d) : p.val);
    var exit = (p.ok && d && typeof d === 'object' && d.exit_code != null) ? d.exit_code : null;
    var meta = document.createElement('div'); meta.className = 'laya-zmeta';
    var badge = document.createElement('span');
    badge.className = 'laya-exit' + (exit === 0 ? '' : (exit == null ? ' neutral' : ' bad'));
    badge.textContent = exit == null ? '执行结果' : ('退出码 ' + exit);
    meta.appendChild(badge);
    root.appendChild(meta);
    if (codeText) {                          // 代码原文（step_start.input 带回）语法高亮
      var capC = document.createElement('div'); capC.className = 'laya-cap';
      capC.innerHTML = '<i data-lucide="code-2" class="w-3.5 h-3.5"></i><span>代码</span>';
      var preC = document.createElement('pre'); preC.className = 'laya-code';
      var codeEl = document.createElement('code'); codeEl.innerHTML = highlightCode(codeText);
      preC.appendChild(codeEl);
      root.appendChild(capC); root.appendChild(preC);
    }
    if (out) {                               // 输出日志等宽排版
      var capO = document.createElement('div'); capO.className = 'laya-cap';
      capO.innerHTML = '<i data-lucide="terminal" class="w-3.5 h-3.5"></i><span>输出</span>';
      var preO = document.createElement('pre'); preO.className = 'laya-log';
      preO.textContent = out.length > 6000 ? out.slice(0, 6000) + '\n…（已截断）' : out;
      root.appendChild(capO); root.appendChild(preO);
    }
    if (!codeText && !out && exit == null) root.appendChild(buildTextView(p.val));
    return root;
  }

  function buildGenericView(p) {             // iris / 未知工具 / 裸对象兜底
    var d = p.val;
    if (p.ok && d && typeof d === 'object' && !Array.isArray(d)) {
      if (typeof d.text === 'string') {      // iris 分析文本
        var w = document.createElement('div'); w.className = 'laya-txt';
        w.innerHTML = '<i data-lucide="image" class="w-3.5 h-3.5"></i>';
        w.appendChild(document.createTextNode(d.text));
        return w;
      }
      var keys = Object.keys(d);
      var flat = keys.length && keys.every(function (k) {
        var v = d[k]; return v == null || ['string', 'number', 'boolean'].indexOf(typeof v) >= 0;
      });
      if (flat) {                            // 纯标量键值对 → 键值列表
        var kv = document.createElement('div'); kv.className = 'laya-kvs';
        keys.forEach(function (k) {
          var row = document.createElement('div'); row.className = 'laya-kv';
          row.innerHTML = '<span class="k">' + esc(k) + '</span><span class="v"></span>';
          row.querySelector('.v').textContent = String(d[k]);
          kv.appendChild(row);
        });
        return kv;
      }
    }
    if (p.ok && typeof d === 'string') return buildTextView(d);
    return buildTextView(p.ok ? JSON.stringify(d, null, 2) : d);
  }

  function buildResultBlock(tool, dataStr, codeText) {
    var p = parseLoose(dataStr);
    var main;
    try {
      if (tool === 'file') main = buildFileView(p);
      else if (tool === 'argus') main = buildArgusView(p);
      else if (tool === 'zcode') main = buildZcodeView(p, codeText);
      else main = buildGenericView(p);
    } catch (e) { main = buildTextView(String(dataStr == null ? '' : dataStr)); }
    return buildRawable(main, p);
  }

  function rowDetailEl(stepId, el) {         // 结构化步骤详情（替代纯文本 rowDetail）
    var r = getRow(stepId); if (!r || !el) return;
    var d = r.querySelector('.detail');
    d.innerHTML = ''; d.appendChild(el);
    r.classList.add('expandable');
    try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
  }

  /* ---------------- 复制 / 导出 ---------------- */
  function copyText(t) {
    if (!t) return showNotification('无可复制内容', '', 'warning', 1600);
    var done = function () { showNotification('已复制', '最终结果在剪贴板', 'success', 1600); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(done, function () { fallbackCopy(t, done); });
    } else fallbackCopy(t, done);
  }
  function fallbackCopy(t, done) {
    try {
      var ta = document.createElement('textarea');
      ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta); done();
    } catch (e) { showNotification('复制失败', String(e && e.message || e), 'error', 2600); }
  }
  function exportText(t) {
    if (!t) return showNotification('无可导出内容', '', 'warning', 1600);
    try {
      var blob = new Blob([t], { type: 'text/plain;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'laya-agent-result-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.txt';
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 800);
    } catch (e) { showNotification('导出失败', String(e && e.message || e), 'error', 2600); }
  }

  /* ---------------- 聊天消息流插入（复用既有渲染函数，不改动它们） ---------------- */
  async function ensureConvAndBox() {
    if (typeof AppState === 'undefined' || !AppState) return null;
    if (!AppState.activeConv && typeof createNewConversation === 'function') await createNewConversation();
    var conv = AppState.activeConv;
    if (!conv) return null;
    if (typeof removeWelcomeView === 'function') removeWelcomeView();
    var box = document.getElementById('chatMessagesNormal');
    if (box) box.classList.remove('hidden');
    return { conv: conv, box: box };
  }
  async function insertChatMsg(role, content) {
    var host = await ensureConvAndBox();
    if (!host || !host.box || typeof buildMessageEl !== 'function' || typeof uid !== 'function') return;
    var msg = {
      id: uid('msg'), conversationId: host.conv.id, role: role, content: content,
      createdAt: Date.now(), tokens: 0, feedback: 0, attachments: []
    };
    if (role === 'assistant') msg.model = 'Laya Agent';
    // 首条消息自动命名会话（对齐 sendUserMessage 行为，仅本流程内实现、不改原函数）
    if (role === 'user' && host.conv.title === '新对话' && typeof DB !== 'undefined' && DB && DB.updateConversation) {
      host.conv.title = content.slice(0, 18) + (content.length > 18 ? '…' : '');
      try { DB.updateConversation(host.conv.id, { title: host.conv.title }); } catch (e) {}
      var tEl = document.getElementById('chatTitle');
      if (tEl) tEl.textContent = host.conv.title;
    }
    AppState.messages.push(msg);
    try { (AppState._convMsgIndex[host.conv.id] = AppState._convMsgIndex[host.conv.id] || []).push(msg); } catch (e) {}
    try {
      if (typeof isPrivacyMode !== 'function' || !isPrivacyMode()) {
        if (typeof persistMessage === 'function') {
          var p = persistMessage(msg);
          if (p && p.then) {
            p.then(function () {
              try { if (typeof broadcastDataChange === 'function') broadcastDataChange(); } catch (e) {}
            }).catch(function (e) { console.warn('[agent92] persist failed:', e); });
          }
        }
      }
    } catch (e) { console.warn('[agent92] persist failed:', e); }
    var el = buildMessageEl(msg, true);
    host.box.appendChild(el);
    try {
      if (typeof MoraySettings !== 'undefined' && MoraySettings.get('autoScroll') && typeof scrollToMsgBottom === 'function') {
        scrollToMsgBottom();
      }
    } catch (e) {}
    try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
    return el;
  }

  /* ---------------- [输出体验] 聊天精简结论 + 面板定位 ---------------- */

  /** 从 step_start/step_result 事件里捞代码原文（zcode 高亮用） */
  function zcodeInputCode(stepId) {
    var inp = state && state.stepInput && state.stepInput[stepId];
    return inp ? String(inp.code || inp.script || '') : '';
  }

  /** final.steps 里最后一步的工具名（决定最终结果用哪种视图渲染） */
  function lastStepTool(ev) {
    try {
      if (ev && Array.isArray(ev.steps) && ev.steps.length) return String(ev.steps[ev.steps.length - 1].tool || '');
    } catch (e) {}
    return (state && state.lastTool) || '';
  }

  /** 按工具类型生成一句话结论（聊天里只放这一句，不重复数据） */
  function summarizeResult(tool, rawStr) {
    var p = parseLoose(rawStr);
    var d = p.val;
    if (p.ok && d && typeof d === 'object') {
      if (tool === 'file' && Array.isArray(d.entries)) {
        var dirs = 0, files = 0;
        d.entries.forEach(function (en) { if (en && en.is_dir) dirs++; else files++; });
        return '已列出目录 ' + (d.dir ? '`' + mdPlain(String(d.dir)) + '`' : '') +
          '，共 ' + (d.count != null ? d.count : d.entries.length) + ' 项（文件夹 ' + dirs + ' · 文件 ' + files + '）';
      }
      if (tool === 'file' && typeof d.content === 'string') {
        return '已读取文件 ' + (d.path ? '`' + mdPlain(String(d.path)) + '`' : '') +
          '（' + fmtBytes(d.size != null ? d.size : d.content.length) + '）';
      }
      if (tool === 'file' && d.written != null) {
        return '已写入 ' + (d.path
          ? '<a href="#" data-preview-path="' + attr(String(d.path)) + '" style="color:var(--brand-cobalt,#60a5fa);text-decoration:underline;cursor:pointer" title="点击在应用内预览">' + esc(String(d.path)) + '</a>'
          : '') + '（' + d.written + ' 字符）';
      }
      if (Array.isArray(d.results)) {
        return '搜索「' + mdPlain(String(d.query || '')) + '」完成，共 ' +
          (d.count != null ? d.count : d.results.length) + ' 条结果';
      }
      if (typeof d.output === 'string' || d.exit_code != null) {
        return '代码执行完成，退出码 ' + (d.exit_code != null ? d.exit_code : '-') +
          (d.output ? '，输出 ' + d.output.length + ' 字符' : '');
      }
    }
    if (p.ok && Array.isArray(d)) return '返回 ' + d.length + ' 条结果';
    var s = mdPlain(String(p.ok && d != null ? (typeof d === 'object' ? JSON.stringify(d) : d) : rawStr || ''))
      .replace(/\s+/g, ' ').trim();
    return s ? clampStr(s, 90) : '完成';
  }

  /** 展开右侧面板并定位最终结果（聊天「查看详情」入口） */
  function revealFinal() {
    ensurePanel();
    panel.classList.add('show');
    panel.classList.remove('collapsed');
    if (reopen) reopen.classList.remove('show');
    var f = document.getElementById('layaPFinal');
    if (f) {
      try { f.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
      f.classList.remove('laya-flash');
      void f.offsetWidth;   // 重启动画
      f.classList.add('laya-flash');
    }
  }

  /** 在刚插入的 AI 消息气泡里补「查看详情」按钮（仅本次会话 UI，落库内容不含按钮） */
  function appendDetailButton(msgEl) {
    try {
      if (!msgEl || !msgEl.querySelector) return;
      var host = msgEl.querySelector('.ai-msg-inner');
      if (!host || host.querySelector('.laya-chat-detail')) return;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'laya-chat-detail';
      b.textContent = '查看详情';
      b.addEventListener('click', revealFinal);
      host.appendChild(b);
    } catch (e) {}
  }

  /** final → 聊天精简消息（异步落库+渲染，随后补详情按钮）；不 await，不阻塞 SSE 收流 */
  function emitFinalChat(kind, summary) {
    var md;
    if (kind === 'success') md = '**✅ Agent 执行完成**\n\n' + (summary || '');
    else if (kind === 'cancelled') md = '**⏹ Agent 执行已取消**\n' + (summary || '');
    else md = '**❌ Agent 执行失败**\n' + (summary || '');
    insertChatMsg('assistant', md).then(function (el) { appendDetailButton(el); })
      .catch(function (e) { try { console.warn('[agent92] 聊天回显失败：', e); } catch (e2) {} });
  }

  /** 面板：最终结果区（含格式化视图 + 原始 JSON），供「查看详情」定位 */
  function appendFinalBlock(title, el) {
    var host = document.getElementById('layaPSteps'); if (!host) return;
    var old = document.getElementById('layaPFinal'); if (old) old.remove();
    var blk = document.createElement('div'); blk.className = 'laya-final'; blk.id = 'layaPFinal';
    var h = document.createElement('div'); h.className = 'laya-final-h'; h.textContent = title;
    blk.appendChild(h);
    if (el) blk.appendChild(el);
    host.appendChild(blk);
    try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
    try { blk.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (e) {}
  }

  /* ---------------- SSE 执行主流程 ---------------- */
  function handleEvent(ev) {
    if (!state) return;
    if (ev.event === 'start') {
      state.executionId = ev.execution_id;
      state.total = Number(ev.total_steps) || 0;
      updateProgress();
    } else if (ev.event === 'step_start') {
      if (state) {   // 暂存每步工具与入参（zcode 高亮代码原文用）
        state.stepTool = state.stepTool || {}; state.stepTool[ev.step_id] = ev.tool;
        if (ev.input) { state.stepInput = state.stepInput || {}; state.stepInput[ev.step_id] = ev.input; }
      }
      var exist = getRow(ev.step_id);
      var host2 = document.getElementById('layaPSteps');
      if (!exist) { exist = stepRow(ev.step_id, ev.tool, ev.description); host2.appendChild(exist); }
      rowMark(ev.step_id, 'running', Number(ev.attempt) > 1 ? '正在重试第 ' + (Number(ev.attempt) - 1) + ' 次' : '');
      try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (e) {}
    } else if (ev.event === 'step_result') {
      if (state && ev.tool) { state.stepTool = state.stepTool || {}; state.stepTool[ev.step_id] = ev.tool; state.lastTool = ev.tool; }
      if (ev.ok) {   // 成功 → 按工具类型结构化渲染；失败 → 红色错误块（含原始 JSON 切换）
        var tool = ev.tool || (state && state.stepTool && state.stepTool[ev.step_id]) || '';
        var codeText = tool === 'zcode' ? zcodeInputCode(ev.step_id) : null;
        if (ev.data || codeText) rowDetailEl(ev.step_id, buildResultBlock(tool, ev.data, codeText));
        else rowDetailEl(ev.step_id, buildRawable(buildErrorBlock(ev.error), { ok: true, val: ev.error || {} }));
      } else {
        rowDetailEl(ev.step_id, buildRawable(buildErrorBlock(ev.error), { ok: true, val: ev.error || {} }));
      }
      rowMark(ev.step_id, ev.ok ? 'running' : 'failed',
        Number(ev.attempt) > 1 ? '正在重试第 ' + (Number(ev.retry_count != null ? ev.retry_count : ev.attempt - 1)) + ' 次' : '');
    } else if (ev.event === 'check') {
      if (ev.success) {
        rowMark(ev.step_id, 'done', getRow(ev.step_id) && Number(state.lastAttempt[ev.step_id]) > 0 ? '正在重试第 ' + state.lastAttempt[ev.step_id] + ' 次后成功' : '');
        state.doneCount++; updateProgress();
      } else if (getRow(ev.step_id)) {
        // 自检失败原因以红色提示插到详情顶部；已有结构化数据保留（重试的 step_result 会整体重建）
        var rowCk = getRow(ev.step_id);
        var dCk = rowCk.querySelector('.detail');
        if (dCk && dCk.childElementCount) {
          var note = document.createElement('div'); note.className = 'laya-err';
          note.innerHTML = '<div class="m"></div>';
          note.querySelector('.m').textContent = '自检未通过：' + (ev.reason || '');
          dCk.insertBefore(note, dCk.firstChild);
        } else {
          rowDetail(ev.step_id, '自检未通过：' + (ev.reason || ''));
        }
        rowCk.classList.add('expandable');
      }
      state.lastAttempt[ev.step_id] = Number(ev.retry_count || 0);
    } else if (ev.event === 'retry') {
      rowMark(ev.step_id, 'running', '正在重试第 ' + (ev.retry_count != null ? ev.retry_count : ev.attempt) + ' 次');
    } else if (ev.event === 'final') {
      state.finished = true; state.finalText = ''; state.finalEvent = ev;
      var st = ev.status;
      if (st === 'success') {
        setStepStatus('成功', 'ok');
        state.finalText = String(ev.result || '（无输出）');
        // 完整结果只进面板（结构化渲染）；聊天只留状态+一句话结论（不重复数据）
        appendFinalBlock('最终结果' + (ev.note ? ' · ' + ev.note : ''),
          buildResultBlock(lastStepTool(ev), state.finalText));
        emitFinalChat('success', summarizeResult(lastStepTool(ev), state.finalText));
      } else if (st === 'cancelled') {
        setStepStatus('已取消', 'cancel');
        state.finalText = '已取消：' + (ev.error || '');
        appendFinalBlock('执行已取消', buildRawable(buildErrorBlock(ev.error || '用户取消'),
          { ok: true, val: { status: 'cancelled', error: ev.error, steps: ev.steps || [] } }));
        emitFinalChat('cancelled', clampStr(mdPlain(ev.error || ''), 160));
      } else {
        setStepStatus('失败', 'fail');
        state.finalText = String(ev.error || '未知错误');
        appendFinalBlock('执行失败', buildRawable(buildErrorBlock(ev.error || '未知错误'),
          { ok: true, val: { status: 'failed', error: ev.error, failed_step: ev.failed_step || null, steps: ev.steps || [] } }));
        emitFinalChat('failed', clampStr(mdPlain(state.finalText), 160));
      }
      document.getElementById('layaPCancel').hidden = true;
      document.getElementById('layaPCopy').hidden = false;
      document.getElementById('layaPExport').hidden = false;
      updateProgress();
    }
  }

  function stopTimer() { if (timerId) { clearInterval(timerId); timerId = null; } }
  function startTimer() {
    stopTimer();
    var t0 = Date.now();
    timerId = setInterval(function () {
      var el = document.getElementById('layaPElapsed');
      if (el && state && !state.finished) el.textContent = ((Date.now() - t0) / 1000).toFixed(1) + 's';
    }, 200);
  }

  async function runAgent(goal) {
    busy = true;
    state = { goal: goal, executionId: null, total: 0, doneCount: 0, finished: false,
              finalText: '', lastAttempt: {} };
    panelOpen(goal);
    startTimer();
    var ctrl = null;
    try { ctrl = new AbortController(); abortCtrl = ctrl; } catch (e) { abortCtrl = null; }
    try {
      var resp = await fetch(layaBase() + '/execute', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: goal, max_retry: MAX_RETRY }),
        signal: ctrl ? ctrl.signal : undefined
      });
      if (!resp.ok || !resp.body) {
        throw new Error('HTTP ' + resp.status + '（Laya ' + layaBase() + '）');
      }
      var reader = resp.body.getReader();
      var dec = new TextDecoder('utf-8');
      var bufStr = '';
      var gotFinal = false;
      for (;;) {
        var chunk = await reader.read();
        if (chunk.done) break;
        bufStr += dec.decode(chunk.value, { stream: true });
        var frames = bufStr.split('\n\n');
        bufStr = frames.pop(); // 末尾可能是半帧，留待下次
        for (var i = 0; i < frames.length; i++) {
          var lines = frames[i].split('\n');
          for (var j = 0; j < lines.length; j++) {
            var ln = lines[j];
            if (ln.indexOf('data:') !== 0) continue;
            var payload = ln.slice(5).trim();
            if (!payload) continue;
            try {
              var ev = JSON.parse(payload);
              if (ev.event === 'final') gotFinal = true;
              handleEvent(ev);
            } catch (e) { /* 坏帧跳过，不断流 */ }
          }
        }
      }
      if (!gotFinal) {
        setStepStatus('连接中断', 'fail');
        document.getElementById('layaPCancel').hidden = true;
        showNotification('执行连接意外断开', '任务可能仍在服务端运行，可稍后在聊天区查看结果或重试', 'error', 4200);
      }
    } catch (e) {
      var aborted = e && (e.name === 'AbortError');
      if (!aborted) {
        setStepStatus(state && state.finished ? state.lastStatus || '失败' : '失败', 'fail');
        document.getElementById('layaPCancel').hidden = true;
        showNotification('Agent 执行异常', String((e && e.message) || e) + '；请确认 Laya（' + layaBase() + '）已启动', 'error', 4500);
      } else {
        /* [停止修复 2026-10-03] 用户点"取消执行"abort 本地流：面板落"已停止"终态。
         * 此前 else 为空——abort 后无任何状态反馈，面板卡在"执行中"观感像挂死。 */
        if (state) state.finished = true;   // 冻结耗时显示（startTimer 以 finished 为闸）
        setStepStatus('已停止', 'cancel');
        document.getElementById('layaPCancel').hidden = true;
      }
    } finally {
      busy = false;
      abortCtrl = null;
      stopTimer();
      var el = document.getElementById('layaPElapsed');
      if (state && el && state.finished === false) { /* 连接异常断开也冻结计时 */ }
      setTimeout(function () { stopTimer(); }, 1200);
    }
  }

  /* ---------------- [U-6-1 2026-10-01] 代码意图 → ZCode 沙箱直跑，完整过程渲染进主对话流 ----------------
   * 入口：tryWrap 的 'code' 分支（decideMode 判定 mode=agent 且 intent_type ∈ code/file_op/tool_call）。
   * 数据流：POST 8000 /api/engines/zcode/tasks → GET /api/engines/zcode/stream/<task_id>
   *   （fetch+ReadableStream 手写 SSE 解析，与 runAgent 同款分帧；无重连，[DONE] 即终态）。
   * 渲染契约：每帧 applyZCodeEvent 只改 msg.execution（纯可序列化状态 → IndexedDB），
   *   80ms 节流 zcodePaint → renderExecutionCard 从状态全量重绘卡片；终态强制重绘+落库。
   * 重建契约：30_chat buildMessageEl 命中 kind='zcode-execution' 时调
   *   window.morayRenderExecutionCard(msg, el) —— 只依赖 msg.execution，零闭包依赖；
   *   进行中任务经 zcodeLive 注册表重绑新节点继续直播；刷新后无注册表 → 快照态渲染。
   * 持久化节流：占位即存、拿到 task_id 即存、流中随绘制节拍 ≥3s 一次、终态必存。 */

  function backendBase() {   // 与 89 的 streamBase 同链：MorayBackend → resolveBackendOrigin → agentBackendBase
    var trim = function (s) { return String(s).replace(/\/+$/, ''); };
    var base = '';
    try { var mb = window.MorayBackend; if (mb && mb.origin) base = trim(mb.origin); } catch (e) { /* 结构缺失走下一级 */ }
    if (!base && typeof window.resolveBackendOrigin === 'function') {
      try { base = trim(window.resolveBackendOrigin()); } catch (e) { /* 忽略 */ }
    }
    if (!base && typeof agentBackendBase === 'function') {
      try { base = trim(agentBackendBase()); } catch (e) { /* 忽略 */ }
    }
    if (!base) base = 'http://127.0.0.1:8000';
    try { if (trim(location.origin) === base) return ''; } catch (e) { /* file:// 等 */ }
    return base;
  }

  function zcodeBasename(p) {
    var s = String(p || '');
    var i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
    return i >= 0 ? s.slice(i + 1) : s;
  }
  function zcodeClip(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n) + '…' : s; }
  function zcodeFmtSec(ms) {
    var n = Number(ms);
    return isFinite(n) && n >= 0 ? (n / 1000).toFixed(1) + 's' : '';
  }
  function zcodeFmtTime(ms) {
    var d = new Date(Number(ms));
    if (isNaN(d.getTime())) return '';
    var p2 = function (x) { return x < 10 ? '0' + x : '' + x; };
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
  }

  var zcodeLive = {};   // msg.id → { el, timer, curReasoning, lastPaintAt, lastPersistAt, ctrl, stopping, stop }（后三项：[停止修复 2026-10-03]）
  var zcodeSnapStopping = {};   // [停止修复 2026-10-03] 快照态（刷新后无 zcodeLive 条目）停止防重点位图

  /** [停止修复 2026-10-03] 服务端收进程树（POST …/kill）。5s 超时；网络/非 2xx 一律
   *  诚实折成 {killed:false, reason}——调用方停止流程照常推进，绝不抛穿。 */
  async function zcodeKillTask(taskId) {
    var killed = false, reason = '', pid = null, exitCode = null;
    try {
      var kr = await fetch(backendBase() + '/api/engines/zcode/tasks/' + encodeURIComponent(taskId) + '/kill',
        { method: 'POST',
          signal: (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(5000) : undefined });
      var kj = null;
      try { kj = await kr.json(); } catch (eKJ) { /* 非 JSON 响应 */ }
      if (kj && kj.ok === true) {
        killed = !!kj.killed; reason = String(kj.reason || '');
        pid = kj.pid == null ? null : kj.pid;
        exitCode = kj.exit_code == null ? null : kj.exit_code;
      } else {
        reason = (kj && (kj.message || kj.error)) || ('HTTP ' + kr.status);
      }
    } catch (eK) { reason = '无法连接后端：' + String((eK && eK.message) || eK); }
    return { killed: killed, reason: reason, pid: pid, exitCode: exitCode };
  }

  /** [停止修复 2026-10-03] 「已手动停止」终态文案（直播/快照两条停止路径同一口径，如实反映 kill 结果）。 */
  function zcodeStopError(r) {
    if (r.killed) return '已手动停止（进程树已终止' + (r.pid != null ? '，pid ' + r.pid : '') + '）';
    if (r.reason === 'already_exited') {
      return '已停止（进程此前已自行结束' + (r.exitCode != null ? '，退出码 ' + r.exitCode : '') + '）';
    }
    return '已手动停止（进程终止未确认：' + (r.reason || '原因未知') + '）';
  }

  /** [停止修复 2026-10-03] 执行卡停止按钮事件委托。实机教训（两轮）：①绑在 host 上——卡片
   *  经消息区 innerHTML 重建，监听器随节点置换丢失；②借 renderExecutionCard 的 host 创建分支
   *  懒装——快照卡渲染路径不创建新 host，分支不走、委托永不装。最终形态：本函数在 IIFE 求值时
   *  直接调用（不依赖任何节点生命周期），document 捕获位只装一次。点击若落在被 refreshIcons
   *  置换的 <i>→游离 <svg> 上，closest 冒泡拿空（89 红线教训）→ 一律 composedPath()。 */
  function installZecStopDelegation() {
    document.addEventListener('click', function (ev) {
      var path = ev.composedPath ? ev.composedPath() : [];
      var btn = null;
      for (var i = 0; i < path.length; i++) {
        var n = path[i];
        if (n && n.classList && n.classList.contains('zec-stop')) { btn = n; break; }
      }
      if (!btn || btn.disabled) return;
      var mid = btn.getAttribute('data-msg-id') || '';
      var tid = btn.getAttribute('data-task-id') || '';
      if (!mid || !tid) return;
      var entry = zcodeLive[mid];
      if (entry && typeof entry.stop === 'function') {
        try { entry.stop(); } catch (e) { console.warn('[zcode92] stop 异常：', e); }
        return;
      }
      try { zcodeStopSnapshot(mid, tid); } catch (e2) { console.warn('[zcode92] 快照停止异常：', e2); }
    }, true);
  }
  installZecStopDelegation();

  /** [停止修复 2026-10-03] 快照态停止：页面刷新后本地直播流已随旧页面消失（无 zcodeLive、无 ctrl），
   *  后端可能还有孤儿 CLI 在跑（主表已被流 finally 弹掉，进程常登表仍在）。本函数兜底：
   *  kill → 消息置终态 → 卡片重绘 + 落库。找不到消息对象也至少 kill 掉进程并提示。 */
  async function zcodeStopSnapshot(msgId, taskId) {
    if (zcodeSnapStopping[msgId]) return;
    zcodeSnapStopping[msgId] = true;
    var r = await zcodeKillTask(taskId);
    var msg = null;
    try {
      if (typeof AppState !== 'undefined' && AppState.messages) {
        for (var i = 0; i < AppState.messages.length; i++) {
          if (AppState.messages[i] && AppState.messages[i].id === msgId) { msg = AppState.messages[i]; break; }
        }
      }
    } catch (eL) { /* 查找失败：kill 已尽力，卡片状态下次加载自然反映 */ }
    var el = null;
    try {
      var selId = window.CSS && CSS.escape ? CSS.escape(msgId) : String(msgId).replace(/["\\]/g, '\\$&');
      el = document.querySelector('.msg-bubble[data-msg-id="' + selId + '"]');
    } catch (eQ) { el = null; }
    if (msg && msg.execution && msg.execution.status === 'running') {
      msg.execution.status = 'failed';
      msg.execution.error = zcodeStopError(r);
    }
    if (el && msg && msg.execution) renderExecutionCard(msg, el);   // 终态后按钮随渲染条件消失
    if (msg) zcodePersist(msg, true);
    showNotification(r.killed ? '已停止' : (r.reason === 'already_exited' ? '任务已结束' : '终止未确认'),
      'ZCode 任务 ' + String(taskId).slice(0, 8) + '：' + (r.killed ? '进程树已终止' : (r.reason || '进程状态未确认')),
      r.killed ? 'info' : 'warning', 3000);
  }

  function injectExecCardStyle() {   // 卡片样式自带注入（不依赖 92 boot 顺序；幂等）
    if (document.getElementById('morayZcodeExecCardStyle')) return;
    var st = document.createElement('style');
    st.id = 'morayZcodeExecCardStyle';
    st.textContent = [
      '.zcode-exec-card{font-size:12px;line-height:1.5;color:var(--color-text-secondary,#a3a3a3);min-width:0}',
      '.zcode-exec-card .zec-status{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}',
      '.zcode-exec-card .zec-chip{font-size:10px;padding:2px 8px;border-radius:999px;flex-shrink:0}',
      '.zcode-exec-card .zec-chip.is-run{background:rgba(251,191,36,.15);color:#FBBF24;animation:zecPulse 1.2s ease-in-out infinite}',
      '.zcode-exec-card .zec-chip.is-ok{background:rgba(74,222,128,.15);color:#4ade80}',
      '.zcode-exec-card .zec-chip.is-fail{background:rgba(248,113,113,.15);color:#f87171}',
      '.zcode-exec-card .zec-chip.is-snap{background:rgba(156,163,175,.12);color:#9ca3af}',
      '@keyframes zecPulse{0%,100%{opacity:1}50%{opacity:.45}}',
      '.zcode-exec-card .zec-tid{font-family:ui-monospace,Consolas,monospace;font-size:10px;color:var(--color-text-tertiary,#8a8a8a)}',
      '.zcode-exec-card .zec-meta{font-size:10px;color:var(--color-text-tertiary,#8a8a8a)}',
      /* [停止修复 2026-10-03] 停止按钮：状态行右端，红色描边胶囊，与 chip 同尺寸档 */
      '.zcode-exec-card .zec-stop{margin-left:auto;display:inline-flex;align-items:center;gap:4px;font-size:10px;padding:2px 8px;border-radius:999px;flex-shrink:0;cursor:pointer;font-family:inherit;line-height:1.6;border:1px solid rgba(248,113,113,.45);background:rgba(248,113,113,.12);color:#f87171}',
      '.zcode-exec-card .zec-stop:hover{background:rgba(248,113,113,.24)}',
      '.zcode-exec-card .zec-stop:disabled{opacity:.45;cursor:default}',
      '.zcode-exec-card .zec-reasoning{margin:6px 0;border:1px solid var(--color-line-ghost,rgba(127,127,127,.28));border-radius:8px;background:var(--color-bg-app,rgba(0,0,0,.14))}',
      '.zcode-exec-card .zec-reasoning>summary{cursor:pointer;padding:6px 10px;font-size:11px;color:var(--color-text-secondary,#A0A0A0);list-style:none;user-select:none}',
      '.zcode-exec-card .zec-reasoning>summary::-webkit-details-marker{display:none}',
      '.zcode-exec-card .zec-reasoning>summary:before{content:"\\25B8  ";color:var(--color-text-tertiary,#666)}',
      '.zcode-exec-card .zec-reasoning[open]>summary:before{content:"\\25BE  "}',
      '.zcode-exec-card .zec-reasoning-body{max-height:200px;overflow-y:auto;padding:0 10px 8px}',
      '.zcode-exec-card .zec-reasoning-seg{margin:6px 0 0;font-style:italic;color:var(--color-text-secondary,#A0A0A0);font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,monospace}',
      '.zcode-exec-card .zec-text-stream{margin:6px 0;border:1px solid var(--color-line-ghost,rgba(127,127,127,.28));border-radius:8px;background:var(--color-bg-app,rgba(0,0,0,.14));max-height:220px;overflow-y:auto}',
      '.zcode-exec-card .zec-text-stream>pre{margin:0;padding:8px 10px;color:var(--color-text-primary,#E0E0E0);font-size:11px;line-height:1.55;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,monospace}',
      '.zcode-exec-card .zec-steps{margin:6px 0;border:1px solid var(--color-line-ghost,rgba(127,127,127,.28));border-radius:8px;max-height:240px;overflow-y:auto}',
      '.zcode-exec-card .zec-row{display:flex;align-items:baseline;gap:7px;min-height:24px;padding:4px 10px;border-top:1px solid var(--color-line-ghost,rgba(127,127,127,.16));font-size:11px;min-width:0}',
      '.zcode-exec-card .zec-row:first-child{border-top:none}',
      '.zcode-exec-card .zec-row.is-ind{padding-left:22px}',
      '.zcode-exec-card .zec-t{color:var(--color-text-tertiary,#666);font-size:10px;flex-shrink:0;font-family:ui-monospace,Consolas,monospace}',
      '.zcode-exec-card .zec-label{font-weight:600;color:var(--color-text-primary,#ededed);flex-shrink:0}',
      '.zcode-exec-card .zec-row.zec-l-dim .zec-label{color:var(--color-text-tertiary,#8a8a8a);font-weight:400}',
      '.zcode-exec-card .zec-row.zec-l-green .zec-label{color:#4ade80}',
      '.zcode-exec-card .zec-row.zec-l-red .zec-label{color:#F87171}',
      '.zcode-exec-card .zec-sum{color:var(--color-text-tertiary,#8a8a8a);min-width:0;word-break:break-all;font-family:ui-monospace,Consolas,monospace;font-size:11px}',
      '.zcode-exec-card .zec-files{margin:6px 0}',
      '.zcode-exec-card .zec-files-head{color:#4ade80;font-weight:600;font-size:11px;margin-bottom:2px}',
      '.zcode-exec-card .zec-file{display:flex;align-items:baseline;gap:6px;min-width:0;padding:2px 0;font-size:11px}',
      '.zcode-exec-card .zec-mono{font-family:ui-monospace,Consolas,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}',
      '.zcode-exec-card .zec-file-ops{color:var(--color-text-tertiary,#666);font-size:10px;flex-shrink:0}',
      /* [ZCode 产物卡] Write/Edit 改动文件 → 蓝色卡片 + 应用内代码预览（复用 125 的 data-preview-path 委托） */
      '.zcode-exec-card .zec-artifact-card{display:flex;align-items:center;gap:8px;margin:4px 0;padding:6px 9px;border:1px solid rgba(96,165,250,.32);background:rgba(96,165,250,.10);border-radius:8px;min-width:0}',
      '.zcode-exec-card .zec-artifact-icon{flex-shrink:0;font-size:12px;line-height:1}',
      '.zcode-exec-card .zec-artifact-path{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--color-text-primary,#E0E0E0);font-family:ui-monospace,Consolas,monospace;font-size:11px}',
      '.zcode-exec-card .zec-artifact-meta{flex-shrink:0;color:var(--color-text-tertiary,#8a8a8a);font-size:10px}',
      '.zcode-exec-card .zec-artifact-open{flex-shrink:0;padding:2px 8px;border:1px solid rgba(96,165,250,.5);border-radius:6px;background:rgba(96,165,250,.16);color:var(--color-brand-cobalt,#2563EB);font-size:10px;line-height:1.5;cursor:pointer;font-family:inherit}',
      '.zcode-exec-card .zec-artifact-open:hover{background:rgba(96,165,250,.26)}',
      '.zcode-exec-card .zec-resp{margin-top:6px;padding-top:6px;border-top:1px dashed var(--color-line-ghost,rgba(127,127,127,.25));color:var(--color-text-secondary,#a3a3a3);font-size:11px;word-break:break-word}',
      '.zcode-exec-card .zec-err{margin-top:6px;border-left:2px solid #F87171;color:#F87171;padding:4px 0 4px 10px;font-size:11px;word-break:break-all}',
      '.zcode-exec-card .zec-note{margin-top:6px;color:var(--color-text-tertiary,#8a8a8a);font-size:10px}'
    ].join('\n');
    document.head.appendChild(st);
  }

  /** 追加一条时间线步骤（状态里存纯文本，渲染时再转义；cap 200 行防 IndexedDB 膨胀） */
  function zcodePushStep(ex, label, cls, sum, ind, tsMs, fp) {
    ex.steps.push({ l: label, c: cls || '', s: sum || '', i: !!ind, t: zcodeFmtTime(tsMs), fp: fp || null });
    if (ex.steps.length > 200) ex.steps.shift();
  }

  function zcodeTrackFile(ex, fp) {
    for (var i = 0; i < ex.changedFiles.length; i++) {
      if (ex.changedFiles[i].path === fp) { ex.changedFiles[i].count += 1; return; }
    }
    ex.changedFiles.push({ path: fp, count: 1 });
  }

  /** tool.updated(scheduled) 入参摘要：file_path → 文件名(title=全路径)；command 系 → 等宽截断；
   *  其余 → JSON 截断 80。返回 {s, fp}（对齐 89 toolInputSummaryHtml 语义，存纯文本）。 */
  function zcodeToolSummary(input) {
    if (input && typeof input === 'object') {
      if (typeof input.file_path === 'string' && input.file_path) {
        return { s: zcodeBasename(input.file_path), fp: input.file_path };
      }
      var keys = ['command', 'cmd', 'pattern', 'path', 'url', 'query'];
      for (var i = 0; i < keys.length; i++) {
        var v = input[keys[i]];
        if (typeof v === 'string' && v) {
          var short = v.replace(/"[^"\\]*[\\\/]([^"\\]*)"/g, '"$1"').replace(/'[^'\\]*[\\\/]([^'\\]*)'/g, "'$1'");
          return { s: zcodeClip(short, 50), fp: v };
        }
      }
    }
    var s = '';
    try { s = JSON.stringify(input == null ? '' : input); } catch (e) { s = String(input == null ? '' : input); }
    if (!s || s === '""' || s === '{}' || s === '[]') return null;
    return { s: zcodeClip(s, 80), fp: null };
  }

  /** 事件 → 状态（只改 msg.execution，不碰 DOM；事件形状对齐 89 消费语义） */
  function applyZCodeEvent(msg, ev) {
    var ex = msg && msg.execution;
    if (!ex || !ev) return;
    var type = ev.type;
    var p = ev.payload || {};
    if (type === 'turn.started') {
      zcodePushStep(ex, 'task started', '', zcodeClip(String(p.input || ''), 80), false, ev.timestamp);
    } else if (type === 'model.streaming') {
      var kind = p.kind;
      if (kind === 'text_start') {
        if (ex.modelText) ex.modelText += '\n\n';   // 新消息开始：已有内容时段落分隔（89 同语义）
      } else if (kind === 'text_delta') {
        if (typeof p.delta === 'string' && p.delta) {
          ex.modelText += p.delta;
          // 渲染口径=状态口径（89 只在渲染裁 3000；这里连状态一起裁，防长输出无界写进 IndexedDB）
          if (ex.modelText.length > 3000) ex.modelText = ex.modelText.slice(-3000);
        }
      } else if (kind === 'reasoning_start') {
        ex.reasoning.push({ t: '' });
        if (ex.reasoning.length > 3) ex.reasoning.shift();   // 只保最近 3 段（89 同语义）
        var en0 = zcodeLive[msg.id];
        if (en0) en0.curReasoning = ex.reasoning.length - 1;
      } else if (kind === 'reasoning_delta') {
        if (typeof p.delta === 'string' && p.delta) {
          var en1 = zcodeLive[msg.id];
          var idx = en1 ? en1.curReasoning : (ex.reasoning.length ? ex.reasoning.length - 1 : -1);
          if (idx < 0 || !ex.reasoning[idx]) {   // 缺 start 的 delta 兜底开段（89 同语义）
            ex.reasoning.push({ t: '' });
            if (ex.reasoning.length > 3) ex.reasoning.shift();
            idx = ex.reasoning.length - 1;
            if (en1) en1.curReasoning = idx;
          }
          ex.reasoning[idx].t += p.delta;
          if (ex.reasoning[idx].t.length > 2000) ex.reasoning[idx].t = ex.reasoning[idx].t.slice(-2000);
        }
      } else if (kind === 'reasoning_end' || kind === 'finish') {
        var en2 = zcodeLive[msg.id];
        if (en2) en2.curReasoning = null;   // 段落收尾（finish 兜底收段）
      }
      // text_end：段分隔推迟到下一个 text_start 统一处理；start/tool_input_*/tool_call 跳过（89 同语义）
    } else if (type === 'tool.updated') {
      var name = p.toolName || 'tool';
      if (p.kind === 'scheduled') {
        var fp = (p.input && typeof p.input === 'object') ? p.input.file_path : null;
        if (typeof fp === 'string' && fp && (p.toolName === 'Write' || p.toolName === 'Edit')) {
          zcodeTrackFile(ex, fp);   // Write/Edit 的 file_path 累积（done 文件清单数据源，U-5 同口径）
        }
        var sum = zcodeToolSummary(p.input);
        zcodePushStep(ex, 'tool: ' + name, '', sum ? sum.s : '', false, ev.timestamp, sum ? sum.fp : null);
      } else if (p.kind === 'started') {
        zcodePushStep(ex, 'running...', 'dim', '', true, ev.timestamp);
      } else if (p.kind === 'result') {
        zcodePushStep(ex, 'done' + (p.duration != null ? ' · ' + p.duration + 'ms' : ''), 'green', '', true, ev.timestamp);
      } else if (p.kind === 'error') {
        zcodePushStep(ex, 'failed', 'red',
          (name !== 'tool' ? name + ' · ' : '') + zcodeClip(String(p.error || p.message || ''), 80), true, ev.timestamp);
      } else if (p.kind === 'batch') {
        zcodePushStep(ex, 'batch', 'dim', '', true, ev.timestamp);
      }
    } else if (type === 'checkpoint.created') {
      zcodePushStep(ex, 'checkpoint', 'dim', p.fileCount != null ? '· ' + p.fileCount + ' files' : '', false, ev.timestamp);
    } else if (type === 'turn.completed') {
      // result 尾行无 duration：在此缓存（89 同口径）
      if (p.duration != null) ex.durationMs = p.duration;
      var usage = p.usage || {};
      if (usage.totalTokens != null) ex.totalTokens = usage.totalTokens;
      var meta = [];
      if (ex.durationMs != null) meta.push(zcodeFmtSec(ex.durationMs));
      if (ex.totalTokens != null) {
        try { meta.push(Number(ex.totalTokens).toLocaleString('en-US') + ' tokens'); } catch (eT) { /* 忽略 */ }
      }
      zcodePushStep(ex, 'turn completed', 'green', meta.length ? '· ' + meta.join(' · ') : '', false, ev.timestamp);
    } else if (type === 'result') {
      ex.status = 'success';
      ex.response = zcodeClip(String(ev.response || ''), 2000);
      var u2 = ev.usage || {};
      if (u2.totalTokens != null) ex.totalTokens = u2.totalTokens;
    }
  }

  /** 状态 → DOM：从 msg.execution 全量重绘执行卡片（增量直播与重建恢复共用同一条路径）。
   *  bubbleEl = buildMessageEl 的外层 wrap（.msg-bubble，带 data-msg-id）。 */
  function renderExecutionCard(msg, bubbleEl) {
    var ex = msg && msg.execution;
    if (!ex || !bubbleEl) return;
    injectExecCardStyle();
    var box = bubbleEl.querySelector('.msg-bubble__ai-box') || bubbleEl;
    var host = box.querySelector('.zcode-exec-host');
    if (!host) {
      host = document.createElement('div');
      host.className = 'zcode-exec-host';
      // [停止修复 2026-10-03] 委托不再绑 host（监听器活不过消息区 innerHTML 重建）；
      // 已由 installZecStopDelegation() 在分片求值时装到 document 捕获位。
      box.insertBefore(host, box.firstChild);
    }
    // 重建前保留交互态：思考区开合、正文流/步骤流是否贴底
    var oldCard = host.querySelector('.zcode-exec-card');
    var hadCard = !!oldCard;
    var keepOpen = false, keepTextNear = false, keepStepsNear = false;
    if (oldCard) {
      var oldDetails = oldCard.querySelector('.zec-reasoning');
      if (oldDetails) keepOpen = oldDetails.hasAttribute('open');
      var oldStream = oldCard.querySelector('.zec-text-stream');
      if (oldStream) keepTextNear = oldStream.scrollHeight - oldStream.scrollTop - oldStream.clientHeight < 48;
      var oldSteps = oldCard.querySelector('.zec-steps');
      if (oldSteps) keepStepsNear = oldSteps.scrollHeight - oldSteps.scrollTop - oldSteps.clientHeight < 48;
      oldCard.remove();
    }
    var live = zcodeLive[msg.id];
    if (live) live.el = bubbleEl;   // 会话切走再切回：buildMessageEl 重建时经此重绑直播目标

    var card = document.createElement('div');
    card.className = 'zcode-exec-card';
    var h = [];
    // 状态行
    var snap = (!live && ex.status === 'running');   // 无直播注册表且仍标 running → 快照态（刷新后流不再续接）
    var chipCls = ex.status === 'success' ? 'is-ok' : (ex.status === 'failed' ? 'is-fail' : (snap ? 'is-snap' : 'is-run'));
    var chipTxt = ex.status === 'success' ? '✓ 已完成' : (ex.status === 'failed' ? '✗ 失败' : (snap ? '◇ 执行快照' : '● 执行中'));
    h.push('<div class="zec-status"><span class="zec-chip ' + chipCls + '">' + chipTxt + '</span>');
    if (ex.taskId) h.push('<span class="zec-tid" title="' + attr(ex.taskId) + '">' + esc(String(ex.taskId).slice(0, 8)) + '</span>');
    var meta = [];
    if (ex.durationMs != null) meta.push(zcodeFmtSec(ex.durationMs));
    if (ex.totalTokens != null) {
      try { meta.push(Number(ex.totalTokens).toLocaleString('en-US') + ' tokens'); } catch (eM) { /* 忽略 */ }
    }
    if (meta.length) h.push('<span class="zec-meta">' + esc(meta.join(' · ')) + '</span>');
    /* [停止修复 2026-10-03] 「■ 停止」按钮：running + 已有 taskId 就出现——直播态与刷新后
     * 「执行快照」孤儿态都可停（快照走 zcodeStopSnapshot）。受理 POST 期间 taskId 未落位 →
     * 按钮不出现（没 id 无法 kill，裁定③）。防重复：stopping 置位后禁用改「停止中…」。 */
    if (ex.status === 'running' && ex.taskId) {
      var stopDis = !!(live && live.stopping) || !!zcodeSnapStopping[msg.id];
      h.push('<button type="button" class="zec-stop"' + (stopDis ? ' disabled' : '') +
        ' data-msg-id="' + attr(msg.id) + '" data-task-id="' + attr(String(ex.taskId)) + '"' +
        ' title="终止 ZCode 进程树并停止本任务"><i data-lucide="square" class="w-3 h-3"></i>' +
        (stopDis ? '停止中…' : '停止') + '</button>');
    }
    h.push('</div>');
    // 思考过程（默认折叠；重建时保留用户开合态）
    if (ex.reasoning && ex.reasoning.length) {
      h.push('<details class="zec-reasoning"' + (keepOpen ? ' open' : '') + '><summary>思考过程 (' +
        ex.reasoning.length + ' 段)</summary><div class="zec-reasoning-body">');
      ex.reasoning.forEach(function (seg) {
        if (seg && seg.t) h.push('<pre class="zec-reasoning-seg">' + esc(seg.t) + '</pre>');
      });
      h.push('</div></details>');
    }
    // 模型输出（打字机正文）
    if (ex.modelText) {
      h.push('<div class="zec-text-stream"><pre>' + esc(ex.modelText) + '</pre></div>');
    }
    // 工具时间线
    if (ex.steps && ex.steps.length) {
      h.push('<div class="zec-steps">');
      ex.steps.forEach(function (s) {
        h.push('<div class="zec-row' + (s.i ? ' is-ind' : '') + (s.c ? ' zec-l-' + s.c : '') + '">' +
          (s.t ? '<span class="zec-t">' + esc(s.t) + '</span>' : '') +
          '<span class="zec-label">' + esc(s.l) + '</span>' +
          (s.s ? '<span class="zec-sum"' + (s.fp ? ' title="' + attr(s.fp) + '"' : '') + '>' + esc(s.s) + '</span>' : '') +
          '</div>');
      });
      h.push('</div>');
    }
    // 文件清单
    if (ex.changedFiles && ex.changedFiles.length) {
      h.push('<div class="zec-files"><div class="zec-files-head">' + ex.changedFiles.length + ' 个文件已更改</div>');
      ex.changedFiles.forEach(function (f) {
        var fname = zcodeBasename(f.path);
        var zcodePath = ex.taskId
          ? 'zcode:' + String(ex.taskId) + ':' + encodeURIComponent(fname)
          : '';
        h.push('<div class="zec-artifact-card">' +
          '<span class="zec-artifact-icon">📄</span>' +
          '<span class="zec-artifact-path" title="' + attr(f.path) + '">' + esc(fname) + '</span>' +
          '<span class="zec-artifact-meta">(' + f.count + ' 次操作)</span>' +
          (zcodePath ? '<button type="button" class="zec-artifact-open" data-preview-path="' + attr(zcodePath) + '">预览代码</button>' : '') +
          '</div>');
      });
      h.push('</div>');
    }
    // 终态补充：result 回显 / 失败原因 / 快照说明
    if (ex.status === 'success' && ex.response) {
      h.push('<div class="zec-resp">' + esc(zcodeClip(ex.response, 160)) + '</div>');
    }
    if (ex.status === 'failed' && ex.error) {
      h.push('<div class="zec-err">' + esc(ex.error) + '</div>');
    }
    if (snap) {
      h.push('<div class="zec-note">过程流快照：页面刷新后不再续接实时事件（ZCode 任务无重连）</div>');
    }
    card.innerHTML = h.join('');
    host.appendChild(card);
    var streamEl = card.querySelector('.zec-text-stream');
    if (streamEl && (keepTextNear || !hadCard)) streamEl.scrollTop = streamEl.scrollHeight;
    var stepsEl = card.querySelector('.zec-steps');
    if (stepsEl && (keepStepsNear || !hadCard)) stepsEl.scrollTop = stepsEl.scrollHeight;
  }

  /** 持久化节流：随绘制节拍 ≥3s 一次；force=true 终态/关键节点必存（IndexedDB 写放大防护） */
  function zcodePersist(msg, force) {
    var entry = zcodeLive[msg.id];
    var now = Date.now();
    if (!force && entry && entry.lastPersistAt && now - entry.lastPersistAt < 3000) return;
    if (entry) entry.lastPersistAt = now;
    try {
      if (typeof persistMessage === 'function') {
        var p = persistMessage(msg);
        if (p && p.then) p.catch(function (e) { try { console.warn('[zcode92] persist failed:', e); } catch (e2) {} });
      }
    } catch (e) { try { console.warn('[zcode92] persist failed:', e); } catch (e2) {} }
  }

  /** 渲染节流：80ms 合并 DOM 更新（89 同款"只合并渲染，不丢数据"），并搭车节流落库 */
  function zcodePaint(msg, immediate) {
    var entry = zcodeLive[msg.id];
    if (!entry) return;
    var now = Date.now();
    if (!immediate && now - (entry.lastPaintAt || 0) < 80) {
      if (entry.timer == null) {
        entry.timer = setTimeout(function () {
          entry.timer = null;
          zcodePaint(msg, true);
        }, 80);
      }
      return;
    }
    entry.lastPaintAt = now;
    if (entry.timer != null) { clearTimeout(entry.timer); entry.timer = null; }
    if (entry.el && entry.el.isConnected) renderExecutionCard(msg, entry.el);
    zcodePersist(msg, false);
  }

  /** [U-6-1] 代码意图主流程：占位执行卡 → 受理 task → 消费 SSE → 状态驱动重绘 → 终态落库。
   *  busy 与 Agent 共用（tryWrap 的同一执行闸）；任何退出路径经 finally 收尾，不留假 running。 */
  async function runZCodeInChat(prompt) {
    busy = true;
    var msg = null;
    try {
      if (typeof uid !== 'function' || typeof buildMessageEl !== 'function' || typeof AppState === 'undefined') {
        showNotification('执行卡不可用', '核心渲染组件未就绪，无法在对话流中执行 ZCode 任务', 'error', 3200);
        return;
      }
      var host = null;
      try { host = await ensureConvAndBox(); } catch (eH) { host = null; }
      if (!host || !host.conv || !host.box) {
        showNotification('无法创建执行卡片', '会话容器未就绪', 'error', 3000);
        return;
      }
      // 1. 占位 assistant 消息：kind='zcode-execution'，状态全在 msg.execution（可序列化 → IndexedDB）
      msg = {
        id: uid('msg'), conversationId: host.conv.id, role: 'assistant', content: '',
        kind: 'zcode-execution', model: 'ZCode',
        execution: { taskId: null, status: 'running', steps: [], changedFiles: [],
                     modelText: '', reasoning: [], durationMs: null, totalTokens: null,
                     response: '', error: '', startedAt: Date.now() },
        createdAt: Date.now(), tokens: 0, feedback: 0, attachments: []
      };
      AppState.messages.push(msg);
      try { (AppState._convMsgIndex[host.conv.id] = AppState._convMsgIndex[host.conv.id] || []).push(msg); } catch (eI) {}
      try {
        if (typeof persistMessage === 'function') {
          var p0 = persistMessage(msg);
          if (p0 && p0.then) p0.catch(function (eP0) { console.warn('[zcode92] persist failed:', eP0); });
        }
      } catch (eP0b) { /* 隐私模式/写库失败不阻断执行 */ }
      var el = buildMessageEl(msg, true);
      host.box.appendChild(el);
      try {
        if (typeof MoraySettings !== 'undefined' && MoraySettings.get('autoScroll') && typeof scrollToMsgBottom === 'function') scrollToMsgBottom();
      } catch (eS0) { /* 忽略 */ }
      try { if (typeof refreshIcons === 'function') refreshIcons(); } catch (eR0) { /* 忽略 */ }

      /* [停止修复 2026-10-03] AbortController 挂接：两个 fetch（受理 + stream）都吃这一路 signal。
       * stop() 硬时序 = ①先 kill 后 ②置终态 ③再 abort——顺序颠倒会让服务端 _gen finally
       * 先弹主表、kill 找不到 rec（孤儿进程照跑，停止变假）。快照态（页面刷新过）没有 ctrl，
       * 委托自动落 zcodeStopSnapshot，同一 kill 端点收进程树。 */
      var ctrl = new AbortController();
      var zEntry = { el: el, timer: null, curReasoning: null, lastPaintAt: 0, lastPersistAt: Date.now(),
                     ctrl: ctrl, stopping: false, stop: null };
      zEntry.stop = async function () {
        if (zEntry.stopping) return;
        zEntry.stopping = true;
        zcodePaint(msg, true);   // 按钮即时置灰「停止中…」
        try {
          var killR = await zcodeKillTask(msg.execution.taskId);   // 1. 先 kill（服务端进程树）
          // 2. 置终态：仅当服务端流还没先落终态（result/失败兜底）才覆盖
          if (msg.execution.status === 'running') {
            msg.execution.status = 'failed';
            msg.execution.error = zcodeStopError(killR);
            zcodePaint(msg, true);
            zcodePersist(msg, true);
          }
          // kill 结果同步进通知（finally 先落占位「已手动停止」的场景，pid 级事实以此为准）
          showNotification(killR.killed ? '已停止' : (killR.reason === 'already_exited' ? '任务已结束' : '终止未确认'),
            'ZCode 任务 ' + String(msg.execution.taskId).slice(0, 8) + '：' + (killR.killed ? '进程树已终止' : (killR.reason || '进程状态未确认')),
            killR.killed ? 'info' : 'warning', 3000);
        } finally {
          // 3. 后 abort：断本地 fetch；已终态/流已自然收尾时空操作无害
          try { ctrl.abort(); } catch (eAb) { console.warn('[zcode92] abort 异常（不影响已完成的 kill）：', eAb); }
        }
      };
      zcodeLive[msg.id] = zEntry;
      renderExecutionCard(msg, el);   // 首帧：执行中外壳

      // 2. 受理任务（受理失败 → 置终态后 return，finally 统一重绘+落库+撤销注册表）
      var tid = null;
      try {
        var resp = await fetch(backendBase() + '/api/engines/zcode/tasks', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          signal: ctrl.signal,   // [停止修复 2026-10-03] 受理 fetch 同闸（按钮到 taskId 才亮，点不到受理期，接 signal 属防御完备）
          body: JSON.stringify({ prompt: String(prompt || ''), language: 'auto' })
        });
        var j = null;
        try { j = await resp.json(); } catch (eJ) { /* 非 JSON 响应 */ }
        tid = j && (j.task_id || (j.data && j.data.task_id));
        if (!resp.ok || !tid) {
          // 错误链 message||msg||error（后端 429/400 体字段名不统一的既有教训）
          var accErr = (j && (j.message || j.msg || j.error)) || ('HTTP ' + resp.status);
          msg.execution.status = 'failed';
          msg.execution.error = '受理失败：' + accErr;
          return;
        }
      } catch (eAcc) {
        msg.execution.status = 'failed';
        msg.execution.error = '受理失败：无法连接本地后端（' + String((eAcc && eAcc.message) || eAcc) + '）';
        return;
      }
      msg.execution.taskId = tid;
      zcodePersist(msg, true);
      zcodePaint(msg, true);

      // 3. 消费 SSE：每帧 applyZCodeEvent 改状态 → 80ms 节流重绘；[DONE]/result 不提前 break，
      //    服务端发完 [DONE] 即关流，读到 chunk.done 自然结束
      var gotResult = false;
      try {
        var sResp = await fetch(backendBase() + '/api/engines/zcode/stream/' + encodeURIComponent(tid),
          { signal: ctrl.signal });   // [停止修复 2026-10-03] stop() abort 断此流（kill 先行，见上）
        if (!sResp.ok || !sResp.body) throw new Error('HTTP ' + sResp.status);
        var reader = sResp.body.getReader();
        var dec = new TextDecoder('utf-8');
        var bufStr = '';
        for (;;) {
          var chunk = await reader.read();
          if (chunk.done) break;
          bufStr += dec.decode(chunk.value, { stream: true });
          var frames = bufStr.split('\n\n');
          bufStr = frames.pop();   // 末尾可能是半帧，留待下次
          for (var i = 0; i < frames.length; i++) {
            var lines = frames[i].split('\n');
            for (var k = 0; k < lines.length; k++) {
              var ln = lines[k];
              if (ln.indexOf('data:') !== 0) continue;
              var payload = ln.slice(5).trim();
              if (!payload) continue;
              if (payload === '[DONE]') { gotResult = true; continue; }   // 哨兵行
              try {
                var ev = JSON.parse(payload);
                /* [停止修复 2026-10-03] 手动停止后在飞尾帧丢弃：防 result 帧把「已手动停止」
                 * 覆写成「✓ 已完成」（kill 与 result 帧同毫秒级竞态，如实保停不保谎）。 */
                if (msg.execution.status !== 'running') continue;
                applyZCodeEvent(msg, ev);
                if (ev && ev.type === 'result') gotResult = true;
                zcodePaint(msg, false);
              } catch (eF) { /* 坏帧跳过，不断流 */ }
            }
          }
        }
      } catch (eSse) {
        // 无重连（对齐 89/后端契约）：失败如实落终态
        // [停止修复 2026-10-03] stop() 已置 stopping/终态：abort 触发的 AbortError 不再覆写为「事件流中断」
        var entSse = zcodeLive[msg.id];
        if (msg.execution.status === 'running' && !(entSse && entSse.stopping)) {
          msg.execution.status = 'failed';
          msg.execution.error = '事件流中断：' + String((eSse && eSse.message) || eSse);
        }
      }
      if (!gotResult && msg.execution.status === 'running') {
        msg.execution.status = 'failed';
        msg.execution.error = msg.execution.error || '任务结束但未收到 result 帧';
      }
      // 4/5. 终态重绘 + 强制落库在 finally 统一做
    } finally {
      busy = false;
      if (msg) {
        var entryEnd = zcodeLive[msg.id];
        if (entryEnd) {
          if (entryEnd.timer != null) { clearTimeout(entryEnd.timer); entryEnd.timer = null; }
          if (msg.execution.status === 'running') {   // 兜底：任何未标终态的退出路径都落 failed
            msg.execution.status = 'failed';
            /* [停止修复 2026-10-03] stopping 已置而到这里：kill POST 未回、流先收尾——
             * 先落「已手动停止」占位终态；stop() 后半程若见 running 才覆写（此时已非 running 不会覆写），
             * pid 级 kill 结果以通知为准。 */
            msg.execution.error = (entryEnd.stopping ? '已手动停止' : (msg.execution.error || '执行中断'));
          }
          if (entryEnd.el && entryEnd.el.isConnected) renderExecutionCard(msg, entryEnd.el);
          zcodePersist(msg, true);
          delete zcodeLive[msg.id];
        }
      }
    }
  }

  /* [U-6-1 改动点5] 跨分片暴露：30_chat buildMessageEl 的重建路径经 window 调用本渲染器
   * （30_chat 在 assemble 顺序上先于 92 解析，但调用发生在 boot 之后，此处赋值即可达；
   *  typeof 判空 + 30_chat 自带文本摘要兜底，92 缺位/异常都不炸消息渲染）。 */
  window.morayRenderExecutionCard = renderExecutionCard;

  /* ---------------- [U-7-4 2026-10-02] 'code' 分支路径存在性闸 ----------------
   * 背景（U-7-0 实锤）：用户给的路径不存在时，ZCode 里的模型不自问反自猜——
   * D:\test\demo.py 不存在 → 被就近改成重构 D:\test.py。故发送前由前端把关：
   * 抽取输入中的绝对路径 → 复用 GET /api/agent/run/preflight 逐个查存在性
   * （agent_runner.py:631，接受任意路径、返回 data.path_exists；68_task_dispatch
   * checkPath 同款先例含 20s 短缓存）→ 有不存在的弹三按钮确认框（复用 20_ai
   * showModal 零改动；showConfirm 只有两键做不了三选一）。
   * 三裁定：①不加后端接口 ②「仍然继续」在 prompt 尾附加轻量系统提示 ③preflight
   * 无 is_file、只判 path_exists。fail-open：preflight 不可达/超时/结构不符 →
   * 视为存在放行；闸自身异常同样放行——绝不阻塞正常发送。 */

  var U74_HINT = '\n\n【系统提示：以上路径经检查不存在。若你无法确定目标，请直接停下报告，不要自选相似路径。】';
  var U74_CACHE_TTL = 20000;            // 抄 68_task_dispatch checkPath 的 20s 短缓存
  var u74ExistsCache = {};              // 归一化路径 → {exists, at}；查询失败（null）不缓存，下次重查

  /* 绝对路径正则（三类，提取后统一去重）：
   * - Windows 盘符：(?<![A-Za-z0-9]) 前置守卫防吞 "https://" 里的 "s://"；
   * - UNC：\\server\share\...；
   * - POSIX：裸 / 开头误伤率高（日期 3/4、英文 and/or），首段用常见 Unix 根白名单收敛。
   * 连续段遇 空白/引号/CJK 及全角标点/非法文件字符 即止——路径内不含 CJK（含中文名的
   * 路径抽不到 → 不检查 → 与现状等价；好过把后面的中文说明吞进路径，fail-safe 方向）。 */
  var U74_WIN_RE = /(?<![A-Za-z0-9])[A-Za-z]:[\/\\][^\s"'‘’“”…`<>()\[\]{},;:!?|*?\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]+/g;
  var U74_UNC_RE = /\\\\[^\s"'‘’“”…`<>()\[\]{},;:!?|*?\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]+/g;
  var U74_POSIX_RE = /(?:^|(?<![\w\/:.\-]))\/(?:home|root|tmp|var|usr|etc|opt|mnt|media|srv|data|Users|workspace|projects?|apps?|www|logs?)\/[\w.\-]+(?:\/[\w.\-]+)*/g;

  /** 抽取 raw 全部绝对路径：三类正则 → 剥尾部分隔符/标点 → 头部形态复核 →
   *  归一（分隔符统一 + 小写）去重，保留首个原文作展示。0 个路径 = 不检查直接放行。 */
  function u74ExtractPaths(raw) {
    var s = String(raw || '');
    if (!s) return [];
    var found = [], seen = {};
    function push(m) {
      var p = String(m).replace(/[\/\\]+$/, '').replace(/[.,;:!?，。；：！？、…]+$/, '');
      if (!p || !/^(?:[A-Za-z]:[\/\\]|\\\\|\/)./.test(p)) return;
      var key = p.replace(/\//g, '\\').toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      found.push(p);
    }
    (s.match(U74_WIN_RE) || []).forEach(push);
    (s.match(U74_UNC_RE) || []).forEach(push);
    (s.match(U74_POSIX_RE) || []).forEach(push);
    return found;
  }

  /** 单路径存在性：GET /api/agent/path_exists（旧 preflight 的同款响应，data.path_exists 布尔）。返回 true/false；null=查询失败（fail-open） */
  async function u74PathExists(p) {
    var key = String(p).replace(/\//g, '\\').toLowerCase();
    var hit = u74ExistsCache[key];
    if (hit && Date.now() - hit.at < U74_CACHE_TTL) return hit.exists;
    var res = null;
    try {
      var ctrl = new AbortController();
      var timer = setTimeout(function () { ctrl.abort(); }, 3000);
      var r = await fetch(backendBase() + '/api/agent/path_exists?path=' + encodeURIComponent(p),
        { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (r.ok) {
        var j = await r.json();
        if (j && j.ok === true && j.data && typeof j.data.path_exists === 'boolean') res = j.data.path_exists;
      }
    } catch (e) { res = null; }
    if (res !== null) u74ExistsCache[key] = { exists: res, at: Date.now() };
    return res;
  }

  /** 三按钮确认框（复用 20_ai showModal，零改动）。onChoice('retry'|'go'|'cancel')。
   *  遮罩点击 / ESC 关闭（20_ai 全局监听直接摘 .active、不经按钮）→ 视同取消，
   *  用 MutationObserver 兜住，避免上方 Promise 永久悬挂。 */
  function u74MissingModal(missing, onChoice) {
    var items = missing.map(function (p) {
      return '<li style="font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-all">' + esc(p) + '</li>';
    }).join('');
    var html =
      '<div style="font-size:13px;line-height:1.7">' +
        '<p style="margin:0 0 6px;font-weight:600">以下路径不存在：</p>' +
        '<ul style="margin:0 0 10px;padding-left:20px;max-height:30vh;overflow:auto">' + items + '</ul>' +
        '<p style="margin:0 0 2px">继续后 ZCode 会自行判断，可能：</p>' +
        '<ul style="margin:0 0 10px;padding-left:20px;color:var(--color-text-tertiary,#8a8a8a)">' +
          '<li>就近匹配到类似路径（可能改错文件）</li>' +
          '<li>停下报告错误</li>' +
        '</ul>' +
        '<p style="margin:0">建议：检查路径后重新输入，或选择「仍然继续」。</p>' +
      '</div>';
    var foot =
      '<div style="display:flex;gap:8px;justify-content:flex-end">' +
        '<button type="button" id="u74BtnRetry" style="padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;border:none;background:var(--brand,#E5734A);color:#fff">重新输入</button>' +
        '<button type="button" id="u74BtnGo" style="padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;border:1px solid rgba(251,191,36,.5);background:rgba(251,191,36,.08);color:#FBBF24">仍然继续</button>' +
        '<button type="button" id="u74BtnCancel" style="padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;border:1px solid var(--color-line-ghost,rgba(127,127,127,.3));background:transparent;color:var(--color-text-secondary,#9ca3af)">取消</button>' +
      '</div>';
    var box = showModal(html, { title: '⚠️ 路径不存在', footer: foot });
    var settled = false, mo = null;
    function settle(choice) {
      if (settled) return;
      settled = true;
      try { if (mo) mo.disconnect(); } catch (e) {}
      try { onChoice(choice); } catch (e) { try { console.warn('[u74] 弹框回调异常：', e); } catch (e2) {} }
    }
    try {
      var bR = box.querySelector('#u74BtnRetry');
      var bG = box.querySelector('#u74BtnGo');
      var bC = box.querySelector('#u74BtnCancel');
      if (bR) bR.addEventListener('click', function () { closeModal(); settle('retry'); });
      if (bG) bG.addEventListener('click', function () { closeModal(); settle('go'); });
      if (bC) bC.addEventListener('click', function () { closeModal(); settle('cancel'); });
      var overlay = document.getElementById('modalOverlay');
      if (overlay && typeof MutationObserver !== 'undefined') {
        mo = new MutationObserver(function () {
          if (!overlay.classList.contains('active')) settle('cancel');
        });
        mo.observe(overlay, { attributes: true, attributeFilter: ['class'] });
      }
    } catch (e) { settle('cancel'); }
  }

  /** 'code' 分支总闸：{send:true, prompt} 放行（prompt 可能已附加提示）| {send:false} 中止。
   *  0 路径直接放行 → ≥1 路径并发查 → 全存在放行 → 有缺失弹框三选一。
   *  showModal/closeModal 缺位（20_ai 未载）也放行——本闸是增强，不是依赖。 */
  async function u74PathGate(raw) {
    try {
      var paths = u74ExtractPaths(raw);
      if (!paths.length) return { send: true, prompt: raw };
      if (typeof showModal !== 'function' || typeof closeModal !== 'function') return { send: true, prompt: raw };
      var checks = await Promise.all(paths.map(function (p) { return u74PathExists(p); }));
      var missing = [];
      for (var i = 0; i < paths.length; i++) {
        if (checks[i] === false) missing.push(paths[i]);   // null（查询失败）不算缺失
      }
      if (!missing.length) return { send: true, prompt: raw };
      return await new Promise(function (resolve) {
        u74MissingModal(missing, function (choice) {
          if (choice === 'go') resolve({ send: true, prompt: raw + U74_HINT });
          else resolve({ send: false, focus: choice === 'retry' });
        });
      });
    } catch (e) {
      try { console.warn('[u74] 路径闸异常，放行：', e); } catch (e2) {}
      return { send: true, prompt: raw };
    }
  }

  /* ---------------- 发送接管：包在 88/91 包装链最外层 ---------------- */
  function tryWrap() {
    var orig = window.moraySendOverride;
    if (typeof orig !== 'function' || orig.__agent92) return;
    var deciding = false;   // auto 意图判定防重入：同一 wrapped 闭包内共享，判定期间忽略再次发送
    var wrapped = async function () {
      try {
        var inp = document.getElementById('chatInputNormal');
        var raw = ((inp && inp.value) || '').trim();
        var m = agentMode();
        var finalMode = 'chat';
        /* [2026-10-01 U-6-2] on/auto 统一走 decideMode：auto 语义不变（chat→聊天 /
         * agent→浮层 / code→卡片）；on 模式经判定分流（code→卡片、agent→浮层），
         * 判定为 chat 时按 on 语义强制改 agent（用户强制开 Agent 不回落纯聊天）。
         * 三态按钮与 off 路径（finalMode 恒 'chat' → 原生发送）不动。 */
        if (raw && (m === 'on' || m === 'auto')) {
          if (deciding) {
            showNotification('意图判定中', '上一条输入还在判定，请稍候', 'info', 1500);
            return;
          }
          deciding = true;
          try {
            finalMode = await decideMode(raw);   // 阻塞 ~几十到 1500ms（失败/超时降级 chat）
          } finally {
            deciding = false;
          }
          if (finalMode === 'agent') {
            showNotification('意图判定：Agent', '判定为复杂任务，切换 Agent 执行', 'info', 1600);
          } else if (finalMode === 'code') {
            showNotification('意图判定：代码任务', '转交 ZCode 沙箱执行，过程在对话流展示', 'info', 1600);
          }
          // [2026-10-01 U-6-2] on 模式：忽略 decideMode 的 'chat' 结果（用户强制 agent）
          // 'code' → 卡片；'agent' → 浮层；'chat' → 强制改 'agent'（on 语义）
          if (m === 'on' && finalMode === 'chat') {
            finalMode = 'agent';
          }
        }
        /* [U-6-1] 代码意图：直调 ZCode 沙箱（8000 tasks+stream），执行卡片渲染进主对话流。
         * [U-7-4] 发送前路径存在性闸：有不存在的路径先弹框三选一。检查赶在清空输入框
         * 之前——「重新输入」要把原文留在输入框；放行后才走原时序（清空→落库→受理）。 */
        if (finalMode === 'code' && raw && !busy) {
          var gate = await u74PathGate(raw);   // 弹框期间输入框原文未动，可继续编辑
          if (!gate || !gate.send) {           // 取消 / 重新输入 / ESC 关闭：不发任务
            if (gate && gate.focus && inp) {   // 重新输入：聚焦输入框，光标落末尾（原文未清空）
              try {
                inp.focus();
                var u74Len = inp.value.length;
                inp.setSelectionRange(u74Len, u74Len);
              } catch (eF0) {}
            }
            return;
          }
          if (inp) {
            inp.value = ''; inp.style.height = 'auto';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          await insertChatMsg('user', raw);    // 用户目标照常进消息流（落库原文；附加提示只进 ZCode prompt）
          runZCodeInChat(gate.prompt);         // 不 await：卡片执行期间不卡发送循环
          return;
        }
        if (finalMode === 'code' && raw && busy) {
          showNotification('ZCode 任务执行中', '请等待当前任务完成后再发送', 'warning', 2200);
          return;
        }
        if (finalMode === 'agent' && raw && !busy) {
          if (inp) {
            inp.value = ''; inp.style.height = 'auto';
            try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
          }
          try { if (typeof ChatAttachments !== 'undefined') ChatAttachments.clear(); } catch (e) {}
          await insertChatMsg('user', raw);   // 用户目标照常进消息流（落库）
          runAgent(raw);                       // 不 await：面板执行期间不卡发送循环
          return;                              // 已接管：不进普通聊天接口
        }
        if (finalMode === 'agent' && raw && busy) {
          showNotification('Agent 执行中', '请等待当前任务完成或先取消', 'warning', 2200);
          return;
        }
      } catch (e) {
        busy = false;
        try { console.warn('[agent92] 接管异常，回退正常发送：', e); } catch (e2) {}
      }
      return orig.apply(this, arguments);
    };
    wrapped.__agent92 = true;
    window.moraySendOverride = wrapped;
  }

  function boot() {
    injectStyle();
    if (boot._guard) return;   // load 后的幂等二次 boot 并入同一守卫，不再起双 interval
    /* 发送链是 88/91 两段异步包装竞速的产物：91 先包（其 tick1）、88 后包盖顶时，
     * __mon91 从此不在链顶且双方轮询均已自停——旧实现单等 __mon91 标记会永久落空，
     * 只能靠 30s 兜底才接管（表现为"开关亮了但发送走普通聊天"）。
     * 改为守卫循环：首包等链顶出现 __crash88/__mon91（该瞬间 88/91 均已自停、
     * 不再互相覆盖）后包最外层；此后链顶若被任何后来者盖住则随时重包（自愈）。
     * 45s 兜底防 88/91 全部弃权时饿死；守卫只做"包回最外层"，不破坏普通发送。 */
    var n = 0;
    var engaged = false;
    boot._guard = setInterval(function () {
      n++;
      if (n <= 225) { ensureButton(); ensureTags(); ensurePanel(); }
      var cur = window.moraySendOverride;
      if (typeof cur !== 'function') return;          // bootApp 尚未写入发送入口
      if (cur.__agent92) { engaged = true; return; }  // 本层已在链顶
      if (!engaged && !(cur.__crash88 || cur.__mon91) && n <= 225) return;
      tryWrap();                                       // 未接管或被盖住 → 重新包回最外层
    }, 200);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
  window.addEventListener('load', function () { setTimeout(boot, 600); }); // 幂等再跑一次，防时序漏网
})();
