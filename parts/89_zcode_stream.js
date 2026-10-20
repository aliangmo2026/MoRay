/* ============================================================
   [Day24b] ZCode 任务流监控面板 —— 对话页右侧 320px SSE 时间线
   [Day24b-c 视觉重构] 终端日志风（GitHub Actions / Vercel Logs 向）：
   事件行 = [8px 色点][emoji][粗体标签][灰色摘要][右对齐时间戳]，行高 ~28px、
   行间 1px #2a2a2a 分隔、无彩色卡片底；done 收敛为浅灰描边小节；
   文件路径/命令等宽字体、默认只显文件名（title 悬停看全路径）；输入截断 80 字符。
   ------------------------------------------------------------
   逻辑层一字未动（SSE 解析 / 类型路由 / 白名单 / 字段契约）。
   数据源（后端 api.py 文件尾追加路由；事件形状以 Day24a 真机样本为准，
   docs/day24a_streamjson_schema.md，勿凭源码接口猜字段名）：
     POST /api/engines/zcode/tasks        {prompt, language?} → {ok, task_id, status}
     GET  /api/engines/zcode/stream/<id>  SSE：data: <原始行>，哨兵 [DONE] 结束
   事件白名单（后端已过滤）：turn.started / tool.updated(kind=scheduled|started|result|error|batch)
     / checkpoint.created / turn.completed / result
     / model.streaming [2026-10-01 U-5 新放行]——payload 统一 {assistantMessageId, delta, done, kind}，
       kind=start/reasoning_start/reasoning_delta/reasoning_end/text_start/text_delta/text_end/
       tool_input_start/tool_input_delta/tool_input_end/tool_call/finish；delta 是增量拼接（非快照，
       仅 text_delta 拼接 == turn.completed.response，真机实测）；本面板正文只消费 text_delta（打字机
       +80ms 节流），思考流聚合 reasoning_delta（保留最近 3 段、默认折叠），
       tool_input_* 与 tool_call 和 tool.updated(scheduled) 信息重复故跳过
   字段契约（Day24a 177 行实测）：
     - 序号字段是 seq（不是 sequenceNumber）；timestamp 是毫秒 epoch；result 尾行无 seq/timestamp
     - tool.updated 的 kind/toolName/input/duration 都在 payload 里
     - turn.completed.payload.duration = 全程毫秒；尾行 result 自带 response/usage 但无 duration
       → 面板缓存 turn.completed 的 duration，供 done 摘要小节展示「耗时」
     - eventCount ≠ 事件行数，禁止拿它做进度分母（本面板无进度条，天然规避）
   边界（任务书红线）：
     - 纯新增分片：不改 85/86/70 既有渲染（70 仅一行 runStep 挂载）；不进消息区
       （30_chat 渲染会整块 innerHTML='' 抹掉注入节点——面板挂 body，fixed 定位，天然免抹）
     - 启动按钮与 30_chat 的 Day22a 系列按钮同法注入 #coreInputContainer .input-toolbar
     - 点击委托一律 composedPath()（refreshIcons 会把被点 <i> 换成游离 <svg>，closest 冒泡拿空）
     - 无重连（任务书：none）；单任务单面板会话；语言参数仅透传记录，CLI 命令不含该 flag
   ============================================================ */
(function () {
  'use strict';
  if (window.MorayZCodeStream) return;

  const PANEL_ID = 'morayZcodeStreamPanel';
  const BTN_ID = 'morayZcodeStreamBtn';
  const STYLE_ID = 'morayZcodeStreamStyle';
  const MAX_ROWS = 400;          // 时间行上限（白名单流很小，防御性上限而已）

  let es = null;                 // EventSource（单任务一会话，不重连）
  let taskId = null;
  let state = 'idle';            // idle | running | done
  let lastTurnMs = null;         // turn.completed.payload.duration（ms）→ done 摘要耗时
  let launching = false;
  // [2026-10-01 U-5] 累积改动文件（替代原 lastFilePath 单值——多文件改动不再丢失）：
  // Map<file_path, {toolName, count, firstSeenAt, lastSeenAt}>，done 小节完整清单数据源
  let changedFiles = new Map();
  // [2026-10-01 U-5] model.streaming 消费状态：正文权威串 + 思考段落 + 80ms 渲染节流句柄
  let textAll = '';              // 正文（text_delta 增量拼接；text_start 时已有内容则加分隔）
  let reasoningSegs = [];        // 思考段落 [{text, pre}]，只保留最近 3 段
  let reasoningSeen = 0;         // 思考段累计数（折叠标题 (N 段) 用，超出 3 段仍如实计）
  let curReasoning = null;       // 未收尾思考段（reasoning_start 建，end/finish 收）
  let textTimer = null;          // 渲染节流：只合并 DOM 更新，不丢数据
  let reasoningTimer = null;
  let rowCount = 0;
  let previewObjectUrl = null;   // 图片预览的 blob URL（换图时 revoke 旧的，防泄漏）

  /* ---------------- 小工具（自带转义，不依赖其它分片的全局） ---------------- */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtSec(ms) {
    const n = Number(ms);
    return isFinite(n) && n >= 0 ? (n / 1000).toFixed(1) + 's' : '';
  }
  function fmtTime(ms) {
    const d = new Date(Number(ms));
    if (isNaN(d.getTime())) return '';
    const p = function (x) { return x < 10 ? '0' + x : '' + x; };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function clip(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n) + '…' : s;
  }
  /** 路径 → 文件名（默认只显文件名，全路径进 title 悬停展示——Day24b-c §4） */
  function basename(p) {
    const s = String(p || '');
    const i = Math.max(s.lastIndexOf('\\'), s.lastIndexOf('/'));
    return i >= 0 ? s.slice(i + 1) : s;
  }

  /** 后端 origin：沿用 85_engines 的三级解析链（MorayBackend → resolveBackendOrigin →
      agentBackendBase → 默认端口）。与应用同源时返回空串（走相对路径，免硬编码）。 */
  function streamBase() {
    const trim = function (s) { return String(s).replace(/\/+$/, ''); };
    let base = '';
    try {
      const mb = window.MorayBackend;
      if (mb && mb.origin) base = trim(mb.origin);
    } catch (e) { /* 结构缺失走下一级 */ }
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

  /** 面板顶部 = 应用顶栏高度（骨架 header 一律 h-14=56px；量不到就用 56 兜底）。 */
  function headerH() {
    try {
      const hdr = document.querySelector('header');
      const h = hdr ? hdr.offsetHeight : 0;
      if (h > 24 && h < 120) return h;
    } catch (e) { /* 忽略 */ }
    return 56;
  }

  /* ---------------- 样式（一次性注入；终端日志风，两套主题下均成立） ---------------- */

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = [
      '#' + PANEL_ID + '{position:fixed;top:56px;right:0;bottom:0;width:320px;z-index:1500;',
      'display:none;flex-direction:column;background:rgba(22,22,22,.97);color:#e7e5e4;',
      'border-left:1px solid rgba(255,255,255,.08);box-shadow:-12px 0 32px rgba(0,0,0,.35);',
      'font-size:12px;line-height:1.4}',
      '#' + PANEL_ID + '.is-open{display:flex}',
      '.mzs-head{display:flex;align-items:center;gap:8px;padding:10px 12px;flex-shrink:0;',
      'border-bottom:1px solid rgba(255,255,255,.08)}',
      '.mzs-dot{width:8px;height:8px;border-radius:50%;background:#666;flex-shrink:0;align-self:center}',
      '.mzs-dot.is-orange{background:#E5734A}',
      '.mzs-dot.is-blue{background:#60A5FA}',
      '.mzs-dot.is-yellow{background:#FBBF24}',
      '.mzs-dot.is-green{background:#4ADE80}',
      '.mzs-dot.is-red{background:#F87171}',
      '.mzs-dot.is-gray{background:#666}',
      '.mzs-dot.is-idle{background:#666}',
      '.mzs-dot.is-running{background:#E5734A}',
      '.mzs-dot.is-done{background:#4ADE80}',
      '@keyframes mzsPulse{0%,100%{opacity:1}50%{opacity:.35}}',
      '.mzs-dot.is-running{animation:mzsPulse 1.2s ease-in-out infinite}',
      '.mzs-title{font-size:13px;font-weight:600;color:#fff;flex-shrink:0}',
      '.mzs-tid{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;',
      'font-size:10px;color:#8a8a8a;font-family:ui-monospace,Consolas,monospace}',
      '.mzs-close{width:22px;height:22px;flex-shrink:0;border:1px solid rgba(255,255,255,.12);',
      'border-radius:6px;background:transparent;color:#a3a3a3;cursor:pointer;font-size:14px;line-height:1;padding:0}',
      '.mzs-close:hover{color:#fff;border-color:rgba(255,255,255,.3)}',
      '.mzs-body{flex:1;min-height:0;overflow-y:auto;padding:0 12px 14px}',
      /* 事件行：[色点][emoji][标签][摘要][时间] 单行日志形；行间 1px 分隔，无卡片底无圆角 */
      '.mzs-row{display:flex;align-items:baseline;gap:7px;min-height:28px;padding:5px 0;',
      'border-bottom:1px solid #2a2a2a;border-radius:0}',
      '.mzs-row.is-ind{padding-left:14px}',
      '.mzs-emoji{flex-shrink:0;font-size:12px;line-height:1}',
      '.mzs-label{font-weight:600;color:#ededed;flex-shrink:0}',
      '.mzs-label.is-green{color:#4ADE80}',
      '.mzs-label.is-red{color:#F87171}',
      '.mzs-label.is-dim{color:#8a8a8a;font-weight:400}',
      '.mzs-sum{color:#8a8a8a;min-width:0;word-break:break-all}',
      '.mzs-mono{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:11px}',
      '.mzs-t{color:#666;font-size:11px;flex-shrink:0;margin-left:auto;',
      'font-family:ui-monospace,Consolas,monospace}',
      '.mzs-subtag{color:#3B82F6;font-size:10px;flex-shrink:0;margin-right:4px}',
      /* done 摘要小节：浅灰描边，无底色无庆祝卡 */
      '.mzs-section{border:1px solid #3a3a3a;border-radius:0;padding:8px 10px;margin:8px 0 4px}',
      '.mzs-done-line{color:#4ADE80;font-weight:600}',
      '.mzs-done-meta{color:#8a8a8a;font-weight:400}',
      '.mzs-file{color:#a3a3a3;margin-top:4px;display:flex;align-items:baseline;gap:6px;min-width:0}',
      '.mzs-resp{color:#666;font-size:11px;margin-top:4px;word-break:break-all;',
      'font-family:ui-monospace,Consolas,"Courier New",monospace}',
      '.mzs-empty{color:#555;text-align:left;padding:14px 0;',
      'font-family:ui-monospace,Consolas,"Courier New",monospace}',
      /* 异常行：只留红字 + 红色左边线，无底色 */
      '.mzs-error{border-left:2px solid #F87171;color:#F87171;padding:5px 0 5px 10px;',
      'margin:8px 0;word-break:break-all}',
      '.mzs-form{display:flex;flex-direction:column;gap:8px;padding:10px 0 8px}',
      '.mzs-prompt{width:100%;box-sizing:border-box;min-height:88px;resize:vertical;',
      'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.14);border-radius:8px;',
      'color:#e7e5e4;font-size:12px;padding:8px;outline:none;font-family:inherit;line-height:1.6}',
      '.mzs-prompt:focus{border-color:rgba(229,115,74,.6)}',
      '.mzs-form-hint{color:#737373;font-size:10px;word-break:break-all}',
      '.mzs-form-btn{align-self:flex-end;background:#E5734A;color:#fff;border:none;border-radius:8px;',
      'padding:6px 14px;font-size:12px;cursor:pointer}',
      '.mzs-form-btn:disabled{opacity:.5;cursor:default}',
      '.mzs-again{margin:10px auto 0;display:block;background:transparent;color:#a3a3a3;',
      'border:1px solid rgba(255,255,255,.14);border-radius:8px;padding:5px 14px;font-size:11px;cursor:pointer}',
      '.mzs-again:hover{color:#fff;border-color:rgba(255,255,255,.3)}',
      /* [Day24b-d] 产物预览：File 行「▸ 预览」开关 + 分型渲染容器 */
      '.mzs-preview-btn{margin-left:8px;background:transparent;border:1px solid #2a2a2a;color:#8a8a8a;',
      'font-size:10px;line-height:1;padding:2px 6px;cursor:pointer;border-radius:4px;font-family:inherit;flex-shrink:0}',
      '.mzs-preview-btn:hover{color:#ededed;border-color:#3a3a3a}',
      '.mzs-preview{max-height:320px;overflow-y:auto;margin-top:6px;border:1px solid #2a2a2a;',
      'padding:8px;background:#1a1a1a;font-size:11px}',
      '.mzs-pre{margin:0;font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:11px;',
      'white-space:pre-wrap;word-break:break-all;color:#d4d4d4}',
      '.mzs-iframe{display:block;width:100%;height:320px;border:1px solid #3a3a3a;background:#fff}',
      '.mzs-img{display:block;max-width:100%;max-height:320px}',
      '.mzs-md{color:#d4d4d4;line-height:1.6;word-break:break-word}',
      '.mzs-md p{margin:0 0 6px}',
      '.mzs-md pre{margin:0 0 6px;padding:6px;background:#222;border-radius:4px;overflow-x:auto}',
      '.mzs-md code{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:10px}',
      '.mzs-preview-loading{color:#8a8a8a}',
      '.mzs-preview-err{color:#F87171}',
      /* [2026-10-01 U-5] 思考过程折叠区（#1A1A1A 斜体浅灰）+ 模型输出区（#0A0E14 等宽浅灰） */
      '.mzs-reasoning{margin:8px 0 4px;border:1px solid #2a2a2a;background:#1A1A1A}',
      '.mzs-reasoning>summary{cursor:pointer;padding:6px 10px;color:#A0A0A0;font-size:11px;',
      'list-style:none;user-select:none}',
      '.mzs-reasoning>summary::-webkit-details-marker{display:none}',
      '.mzs-reasoning>summary:before{content:"\\25B8  ";color:#666}',
      '.mzs-reasoning[open]>summary:before{content:"\\25BE  "}',
      '.mzs-reasoning-body{max-height:200px;overflow-y:auto;padding:0 10px 8px}',
      '.mzs-reasoning-seg{margin:6px 0 0;font-style:italic;color:#A0A0A0;font-size:11px;line-height:1.5;',
      'white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,"Courier New",monospace}',
      '.mzs-textstream{margin:8px 0 4px;border:1px solid #2a2a2a;background:#0A0E14;',
      'max-height:200px;overflow-y:auto}',
      '.mzs-textstream>pre{margin:0;padding:8px 10px;color:#E0E0E0;font-size:11px;line-height:1.55;',
      'white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,"Courier New",monospace}',
      '.mzs-files-head{color:#4ADE80;font-weight:600;margin-top:4px}',
      '.mzs-file-ops{color:#666;font-size:10px;flex-shrink:0}',
      /* [ZCode 产物卡] Write/Edit 改动文件 → 蓝色卡片 + 应用内代码预览（复用 125 的 data-preview-path 委托） */
      '.mzs-artifact-card{display:flex;align-items:center;gap:8px;margin-top:6px;padding:7px 9px;',
      'border:1px solid rgba(96,165,250,.32);background:rgba(96,165,250,.10);border-radius:8px;min-width:0}',
      '.mzs-artifact-icon{flex-shrink:0;font-size:13px;line-height:1}',
      '.mzs-artifact-path{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;',
      'color:#DBEAFE;font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:11px}',
      '.mzs-artifact-meta{flex-shrink:0;color:#93C5FD;font-size:10px}',
      '.mzs-artifact-open{flex-shrink:0;padding:3px 9px;border:1px solid rgba(96,165,250,.5);border-radius:6px;',
      'background:rgba(96,165,250,.16);color:#BFDBFE;font-size:10px;line-height:1.4;cursor:pointer;font-family:inherit}',
      '.mzs-artifact-open:hover{background:rgba(96,165,250,.26)}',
      '.mzs-artifact-card[hidden],.mzs-file[hidden],.mzs-preview[hidden]{display:none}',
      '.mzs-file-more{margin-top:4px;background:transparent;border:none;color:#60A5FA;',
      'font-size:11px;cursor:pointer;padding:0;font-family:inherit}'
    ].join('');
    document.head.appendChild(st);
  }

  /* ---------------- 面板骨架（body 级 fixed，懒建一次） ---------------- */

  let panelEl = null;
  let bodyEl = null;

  function ensurePanel() {
    if (panelEl && document.getElementById(PANEL_ID)) return panelEl;
    injectStyle();
    panelEl = document.getElementById(PANEL_ID);
    if (!panelEl) {
      panelEl = document.createElement('aside');
      panelEl.id = PANEL_ID;
      panelEl.setAttribute('role', 'complementary');
      panelEl.setAttribute('aria-label', 'ZCode 任务流监控');
      panelEl.innerHTML =
        '<div class="mzs-head">' +
          '<span class="mzs-dot is-idle"></span>' +
          '<span class="mzs-title">ZCode 任务监控</span>' +
          '<span class="mzs-tid"></span>' +
          '<button type="button" class="mzs-close" title="关闭面板" aria-label="关闭面板">×</button>' +
        '</div>' +
        '<div class="mzs-body"></div>';
      document.body.appendChild(panelEl);
      // 关闭按钮直接绑（头部节点建后不重建）；体节点内元素按状态重渲染 → 用委托
      const closeBtn = panelEl.querySelector('.mzs-close');
      closeBtn.addEventListener('click', hidePanel);
      bodyEl = panelEl.querySelector('.mzs-body');
      bodyEl.addEventListener('click', function (ev) {
        const path = ev.composedPath ? ev.composedPath() : [];
        for (let i = 0; i < path.length; i++) {
          const n = path[i];
          if (!n || !n.classList) continue;
          if (n.classList.contains('mzs-form-btn')) { submitForm(); return; }
          if (n.classList.contains('mzs-again')) { resetForm(); return; }
          if (n.classList.contains('mzs-preview-btn')) { togglePreview(n); return; }
          if (n.classList.contains('mzs-file-more')) { expandAllFiles(n); return; }
        }
      });
      bodyEl.addEventListener('keydown', function (ev) {
        if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') submitForm();
      });
    }
    bodyEl = panelEl.querySelector('.mzs-body');
    return panelEl;
  }

  function setHead(tid, st) {
    if (!panelEl) return;
    const tidEl = panelEl.querySelector('.mzs-tid');
    const dot = panelEl.querySelector('.mzs-dot');
    if (tidEl) tidEl.textContent = tid ? String(tid).slice(0, 8) : '';
    if (dot) dot.className = 'mzs-dot' + (st === 'running' ? ' is-running' : st === 'done' ? ' is-done' : ' is-idle');
  }

  function setBody(html) {
    rowCount = 0;
    if (bodyEl) bodyEl.innerHTML = html;
  }

  function addRowHTML(html) {
    if (!bodyEl) return;
    const near = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 80;
    bodyEl.insertAdjacentHTML('beforeend', html);
    rowCount++;
    // [U-5] 头部钉住容器（思考过程/模型输出区）不参与行数裁剪；行超限时跳过钉住节点找首个可删行
    while (state !== 'idle' && bodyEl.children.length > MAX_ROWS + 2) {
      const kids = bodyEl.children;
      let victim = null;
      for (let i = 0; i < kids.length; i++) {
        const cls = kids[i].classList;
        if (!cls.contains('mzs-reasoning') && !cls.contains('mzs-textstream')) { victim = kids[i]; break; }
      }
      if (!victim) break;
      bodyEl.removeChild(victim);
    }
    if (near || state !== 'running') bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  /** 事件行统一构造：[色点][emoji][标签][摘要HTML][时间]。
      sumHtml 由调用方拼好（内部文本必须先过 esc/mono 助手）。 */
  function addRow(o) {
    addRowHTML(
      '<div class="mzs-row' + (o.ind ? ' is-ind' : '') + '">' +
        (o.dot ? '<span class="mzs-dot is-' + o.dot + '"></span>' : '') +
        (o.emoji ? '<span class="mzs-emoji">' + o.emoji + '</span>' : '') +
        (o.label ? '<span class="mzs-label' + (o.labelCls ? ' is-' + o.labelCls : '') + '">' + esc(o.label) + '</span>' : '') +
        (o.sumHtml ? '<span class="mzs-sum">' + o.sumHtml + '</span>' : '') +
        (o.time ? '<span class="mzs-t">' + esc(o.time) + '</span>' : '') +
      '</div>');
  }

  function addError(msg) {
    addRowHTML('<div class="mzs-error">' + esc(msg) + '</div>');
  }

  /* ---------------- 摘要渲染助手（路径/命令等宽 + 文件名化 + 80 字截断） ---------------- */

  /** tool.updated(scheduled) 的 input 摘要：file_path → 文件名（title=全路径）；
      command 系字段 → 等宽截断 80；其余 → JSON 等宽截断 80。 */
  function toolInputSummaryHtml(input) {
    if (input && typeof input === 'object') {
      if (typeof input.file_path === 'string' && input.file_path) {
        return '<span class="mzs-mono" title="' + esc(input.file_path) + '">' + esc(basename(input.file_path)) + '</span>';
      }
      const keys = ['command', 'cmd', 'pattern', 'path', 'url', 'query'];
      for (let i = 0; i < keys.length; i++) {
        const v = input[keys[i]];
        if (typeof v === 'string' && v) {
          // Bash 命令提取可执行名+文件名，去掉长路径前缀（python "D:\...\hello.py" → python hello.py）
          const short = v.replace(/"[^"\\]*[\\\/]([^"\\]*)"/g, '"$1"').replace(/'[^'\\]*[\\\/]([^'\\]*)'/g, "'$1'");
          return '<span class="mzs-mono" title="' + esc(v) + '">' + esc(clip(short, 50)) + '</span>';
        }
      }
    }
    let s = '';
    try { s = JSON.stringify(input == null ? '' : input); } catch (e) { s = String(input == null ? '' : input); }
    if (!s || s === '""' || s === '{}' || s === '[]') return '';
    return '<span class="mzs-mono">' + esc(clip(s, 80)) + '</span>';
  }

  function isSubagent(obj, p) {
    return !!(obj.subagent || p.subagent || (p.payload && p.payload.subagent));
  }

  /* ---------------- [2026-10-01 U-5] model.streaming 消费：正文打字机 + 思考流折叠区 ----------------
     payload 统一 {assistantMessageId, delta, done, kind}；delta 是增量（非快照）。
     渲染节流 80ms：delta 先进权威串/段缓冲，DOM 每 80ms 合并刷一次（只合并渲染，不丢数据）；
     流终态（[DONE]/onerror）在 closeStream 里强制同步冲刷，避免尾帧滞留缓冲。
     正文最多保留最近 3000 字符（超出裁头）；思考段只保留最近 3 段、默认折叠。
     tool_input_* 与 tool_call 和 tool.updated(scheduled) 重复，跳过。 */

  const RENDER_THROTTLE_MS = 80;
  const TEXT_KEEP_CHARS = 3000;
  const REASONING_KEEP_SEGS = 3;

  let textPre = null;           // 正文 <pre>（懒建，首帧才占位）
  let reasoningEl = null;       // <details>（懒建；无 reasoning 帧则整区不出现）
  let reasoningBody = null;
  let reasoningCountEl = null;

  function trackChangedFile(fp, toolName) {
    const entry = changedFiles.get(fp) || { toolName: toolName, count: 0, firstSeenAt: Date.now() };
    entry.count += 1;
    entry.lastSeenAt = Date.now();
    changedFiles.set(fp, entry);
  }

  /** 正文区容器：面板时间线上方（bodyEl 头部，思考区之下），钉住不参与行裁剪。 */
  function ensureTextStream() {
    if (textPre && textPre.isConnected) return textPre;
    const host = document.createElement('div');
    host.className = 'mzs-textstream';
    host.innerHTML = '<pre></pre>';
    const anchor = (reasoningEl && reasoningEl.isConnected) ? reasoningEl.nextSibling : bodyEl.firstChild;
    bodyEl.insertBefore(host, anchor);
    textPre = host.querySelector('pre');
    return textPre;
  }

  /** 思考过程折叠区：默认折叠（无 open），标题计数如实显示累计段数。 */
  function ensureReasoning() {
    if (reasoningEl && reasoningEl.isConnected) return reasoningBody;
    const host = document.createElement('details');
    host.className = 'mzs-reasoning';
    host.innerHTML = '<summary>思考过程 (<span class="mzs-reasoning-count">0</span> 段)</summary>' +
      '<div class="mzs-reasoning-body"></div>';
    if (textPre && textPre.isConnected) bodyEl.insertBefore(host, textPre.parentNode);
    else bodyEl.insertBefore(host, bodyEl.firstChild);
    reasoningEl = host;
    reasoningBody = host.querySelector('.mzs-reasoning-body');
    reasoningCountEl = host.querySelector('.mzs-reasoning-count');
    reasoningCountEl.textContent = String(reasoningSeen);
    return reasoningBody;
  }

  function openReasoningSeg() {
    if (curReasoning) return curReasoning;
    const host = ensureReasoning();
    reasoningSeen += 1;
    reasoningCountEl.textContent = String(reasoningSeen);
    const pre = document.createElement('pre');
    pre.className = 'mzs-reasoning-seg';
    host.appendChild(pre);
    const seg = { text: '', pre: pre };
    reasoningSegs.push(seg);
    while (reasoningSegs.length > REASONING_KEEP_SEGS) {
      const old = reasoningSegs.shift();
      if (old.pre && old.pre.parentNode) old.pre.parentNode.removeChild(old.pre);
    }
    curReasoning = seg;
    return seg;
  }

  function flushTextNow() {
    if (textTimer != null) { clearTimeout(textTimer); textTimer = null; }
    if (!textPre || !textPre.isConnected) return;
    textPre.textContent = textAll.length > TEXT_KEEP_CHARS
      ? textAll.slice(textAll.length - TEXT_KEEP_CHARS) : textAll;
    const box = textPre.parentNode;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    if (near) box.scrollTop = box.scrollHeight;   // 打字机跟底；用户上翻阅读不强拉
  }

  function scheduleTextFlush() {
    if (textTimer != null) return;
    textTimer = setTimeout(flushTextNow, RENDER_THROTTLE_MS);
  }

  function flushReasoningNow() {
    if (reasoningTimer != null) { clearTimeout(reasoningTimer); reasoningTimer = null; }
    const seg = curReasoning || reasoningSegs[reasoningSegs.length - 1];
    if (seg && seg.pre && seg.pre.isConnected) {
      seg.pre.textContent = seg.text;
      const box = reasoningBody;
      const near = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      if (near) box.scrollTop = box.scrollHeight;
    }
  }

  function scheduleReasoningFlush() {
    if (reasoningTimer != null) return;
    reasoningTimer = setTimeout(flushReasoningNow, RENDER_THROTTLE_MS);
  }

  /** model.streaming 分发（renderEvent 调用；时间线不加行）。 */
  function handleModelStreaming(p) {
    const kind = p.kind;
    if (kind === 'text_start') {
      ensureTextStream();
      if (textAll) textAll += '\n\n';   // 新消息开始：已有内容时段落分隔
      scheduleTextFlush();
    } else if (kind === 'text_delta') {
      if (typeof p.delta === 'string' && p.delta) {
        ensureTextStream();
        textAll += p.delta;
        scheduleTextFlush();
      }
    } else if (kind === 'reasoning_start') {
      openReasoningSeg();
    } else if (kind === 'reasoning_delta') {
      if (typeof p.delta === 'string' && p.delta) {
        const seg = openReasoningSeg();   // 缺 start 的 delta 兜底开段
        seg.text += p.delta;
        scheduleReasoningFlush();
      }
    } else if (kind === 'reasoning_end' || kind === 'finish') {
      curReasoning = null;                // 段落收尾（finish 兜底收段）
    }
    // text_end：段落分隔推迟到下一个 text_start 统一处理；start/tool_input_*/tool_call 跳过
  }

  /** 新任务/重置：清空 U-5 新增状态（句柄先撤，防挂起定时器触碰已拆 DOM）。 */
  function resetStreamState() {
    changedFiles = new Map();
    textAll = '';
    reasoningSegs = [];
    reasoningSeen = 0;
    curReasoning = null;
    if (textTimer != null) { clearTimeout(textTimer); textTimer = null; }
    if (reasoningTimer != null) { clearTimeout(reasoningTimer); reasoningTimer = null; }
    textPre = null;
    reasoningEl = null;
    reasoningBody = null;
    reasoningCountEl = null;
  }

  /* ---------------- 事件渲染（按 Day24a 实测字段；文案按 Day24b-c 词表） ---------------- */

  function renderEvent(obj) {
    const type = obj.type;
    const p = obj.payload || {};
    const t = fmtTime(obj.timestamp);
    const subTag = isSubagent(obj, p) ? '<span class="mzs-subtag">↳ 子代理</span>' : '';

    if (type === 'turn.started') {
      addRow({ dot: 'orange', emoji: '▶', label: 'task started',
               sumHtml: subTag + esc(clip(p.input, 80)), time: t });
    } else if (type === 'model.streaming') {
      // [2026-10-01 U-5] 正文打字机 + 思考流折叠区渲染（时间线不加行）
      handleModelStreaming(p);
    } else if (type === 'tool.updated') {
      const name = p.toolName || 'tool';
      if (p.kind === 'scheduled') {
        // [2026-10-01 U-5] Write/Edit 的 file_path 累积进 changedFiles（done 小节完整清单数据源）
        const fp = (p.input && typeof p.input === 'object') ? p.input.file_path : null;
        if (typeof fp === 'string' && fp && (p.toolName === 'Write' || p.toolName === 'Edit')) {
          trackChangedFile(fp, p.toolName);
        }
        addRow({ dot: 'blue', emoji: '🔧', label: 'tool: ' + name,
                 sumHtml: subTag + toolInputSummaryHtml(p.input), time: t });
      } else if (p.kind === 'started') {
        addRow({ dot: 'yellow', emoji: '⏵', label: 'running...', labelCls: 'dim', ind: true, time: t });
      } else if (p.kind === 'result') {
        addRow({ dot: 'green', emoji: '✅', label: 'done' + (p.duration != null ? ' · ' + p.duration + 'ms' : ''),
                 labelCls: 'green', ind: true, time: t });
      } else if (p.kind === 'error') {
        const errMsg = String(p.error || p.message || '');
        addRow({ dot: 'red', emoji: '❌', label: 'failed', labelCls: 'red', ind: true,
                 sumHtml: subTag + esc(clip((name !== 'tool' ? name + ' · ' : '') + errMsg, 80)), time: t });
      } else if (p.kind === 'batch') {
        addRow({ dot: 'gray', emoji: '·', label: 'batch', labelCls: 'dim', ind: true, time: t });
      } else {
        addRow({ emoji: '🔧', label: name + '（' + (p.kind || '?') + '）', labelCls: 'dim',
                 sumHtml: subTag, time: t });
      }
    } else if (type === 'checkpoint.created') {
      addRow({ dot: 'gray', emoji: '📌', label: 'checkpoint',
               sumHtml: p.fileCount != null ? esc('· ' + p.fileCount + ' files') : '', time: t });
    } else if (type === 'turn.completed') {
      // result 尾行无 duration：在此缓存，供 done 摘要小节展示耗时
      lastTurnMs = p.duration;
      const usage = p.usage || {};
      const meta = [];
      if (p.duration != null) meta.push(fmtSec(p.duration));
      if (usage.totalTokens != null) {
        try { meta.push(Number(usage.totalTokens).toLocaleString('en-US') + ' tokens'); } catch (e) { /* 忽略 */ }
      }
      addRow({ dot: 'green', emoji: '◆', label: 'turn completed',
               sumHtml: meta.length ? esc('· ' + meta.join(' · ')) : '', time: t });
    } else if (type === 'result') {
      // 底部 done 摘要小节已含 ✓ done · 耗时 · tokens；时间线不再重复一行
      renderDoneSection(obj);
    }
  }

  /** [Day24b-c] done 摘要小节：浅灰描边、无底色、无圆角。
      [2026-10-01 U-5] File 单行 → 完整改动文件清单（changedFiles）：
      「N 个文件已更改」+ 每行蓝色产物卡（文件名 + 操作次数 + 预览代码）；
      路径编码为 zcode:<task_id>:<filename>，点击走 125 的全局预览模态框；
      清单 >5 个折叠其余行，点「展开全部」补显；无 Write/Edit 事件则整块不出现。 */
  function renderDoneSection(obj) {
    const usage = obj.usage || {};
    const meta = [];
    if (lastTurnMs != null) meta.push(fmtSec(lastTurnMs));
    if (usage.totalTokens != null) {
      try { meta.push(Number(usage.totalTokens).toLocaleString('en-US') + ' tokens'); } catch (e) { /* 忽略 */ }
    }
    let html = '<div class="mzs-section">' +
      '<div class="mzs-done-line">✓ done' +
      (meta.length ? '<span class="mzs-done-meta"> · ' + esc(meta.join(' · ')) + '</span>' : '') +
      '</div>';
    if (changedFiles.size) {
      html += '<div class="mzs-files-head">' + changedFiles.size + ' 个文件已更改</div>';
      let idx = 0;
      changedFiles.forEach(function (entry, fp) {
        idx += 1;
        const folded = idx > 5;
        const name = basename(fp);
        const zcodePath = taskId
          ? 'zcode:' + String(taskId) + ':' + encodeURIComponent(name)
          : '';
        html += '<div class="mzs-artifact-card"' + (folded ? ' hidden' : '') + '>' +
          '<span class="mzs-artifact-icon">📄</span>' +
          '<span class="mzs-artifact-path" title="' + esc(fp) + '">' + esc(name) + '</span>' +
          '<span class="mzs-artifact-meta">(' + entry.count + ' 次操作)</span>' +
          (zcodePath ? '<button type="button" class="mzs-artifact-open" data-preview-path="' + esc(zcodePath) + '">预览代码</button>' : '') +
          '</div>';
      });
      if (changedFiles.size > 5) {
        html += '<button type="button" class="mzs-file-more">展开全部（共 ' + changedFiles.size + ' 个）</button>';
      }
    }
    if (obj.response) {
      html += '<div class="mzs-resp">' + esc(clip(String(obj.response), 80)) + '</div>';
    }
    html += '</div>';
    addRowHTML(html);
  }

  /* ---------------- [Day24b-d] 产物预览：GET /api/engines/zcode/files/<task_id>/<filename> ---------------- */

  const CODE_EXTS = { '.py': 1, '.js': 1, '.ts': 1, '.json': 1, '.sh': 1, '.bat': 1 };
  const IMAGE_MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                       '.gif': 'image/gif', '.webp': 'image/webp' };
  // 已知二进制扩展名：不发请求直接提示不支持（spec 边缘情况；真正未知的扩展名仍按纯文本兜底）
  const BINARY_EXTS = { '.zip': 1, '.gz': 1, '.tar': 1, '.7z': 1, '.rar': 1, '.exe': 1, '.dll': 1,
    '.bin': 1, '.pyc': 1, '.class': 1, '.jar': 1, '.pdf': 1, '.doc': 1, '.docx': 1, '.xls': 1,
    '.xlsx': 1, '.ppt': 1, '.pptx': 1, '.db': 1, '.sqlite': 1, '.sqlite3': 1, '.woff': 1,
    '.woff2': 1, '.ttf': 1, '.otf': 1, '.mp3': 1, '.mp4': 1, '.mov': 1, '.avi': 1, '.iso': 1 };

  function previewErr(box, msg) {
    delete box.dataset.loaded;   // 出错允许下次展开重试
    box.innerHTML = '<div class="mzs-preview-err">加载失败: ' + esc(msg) + '</div>';
  }

  /** [2026-10-01 U-5] 「展开全部」：补显被折叠的文件行（>5 个时出现，点后自毁）。 */
  function expandAllFiles(btn) {
    const section = btn.closest('.mzs-section');
    if (!section) return;
    const nodes = section.querySelectorAll('.mzs-artifact-card[hidden], .mzs-file[hidden], .mzs-preview[hidden]');
    for (let i = 0; i < nodes.length; i++) nodes[i].removeAttribute('hidden');
    if (btn.parentNode) btn.parentNode.removeChild(btn);
  }

  /** ▸/▾ 开关（事件委托进入）：懒加载一次，之后只切换显隐。
      [2026-10-01 U-5] 预览容器按文件行定位（.mzs-file 的相邻兄弟节点），
      不再共用 section 级单箱——多文件清单下各文件各预览各的。 */
  function togglePreview(btn) {
    const row = btn.closest('.mzs-file');
    let box = row ? row.nextElementSibling : null;
    if (!box || !box.classList.contains('mzs-preview')) {
      const section = btn.closest('.mzs-section');
      box = section ? section.querySelector('.mzs-preview') : null;
    }
    if (!box) return;
    if (box.hidden) {
      box.hidden = false;
      btn.textContent = '▾ 预览';
      if (!box.dataset.loaded) loadPreview(box);
    } else {
      box.hidden = true;
      btn.textContent = '▸ 预览';
    }
  }

  async function loadPreview(box) {
    box.dataset.loaded = '1';
    const srcPath = box.dataset.filePath || '';   // [U-5] 预览容器自带完整路径
    const fname = srcPath ? basename(srcPath) : '';
    const dot = fname.lastIndexOf('.');
    const ext = dot >= 0 ? fname.slice(dot).toLowerCase() : '';
    if (!taskId || !fname) {
      previewErr(box, '缺少任务或文件信息');
      return;
    }
    if (BINARY_EXTS[ext]) {
      box.innerHTML = '<div class="mzs-preview-err">不支持预览此文件类型</div>';
      return;
    }
    box.innerHTML = '<div class="mzs-preview-loading">加载中...</div>';
    let res = null;
    try {
      res = await fetch(streamBase() + '/api/engines/zcode/files/' +
        encodeURIComponent(taskId) + '/' + encodeURIComponent(fname));
    } catch (e) {
      previewErr(box, (e && e.message) || e);
      return;
    }
    if (!res.ok) {
      if (res.status === 413) {
        box.innerHTML = '<div class="mzs-preview-err">文件过大，无法预览</div>';
        return;
      }
      let msg = 'HTTP ' + res.status;
      try {
        const j = await res.json();
        msg = (j && (j.message || j.error)) || msg;
      } catch (e) { /* 保留 HTTP 码 */ }
      previewErr(box, msg);
      return;
    }
    try {
      if (IMAGE_MIME[ext]) {
        // 图片：blob → objectURL（换图 revoke 旧的，防泄漏）
        const blob = await res.blob();
        if (previewObjectUrl) { try { URL.revokeObjectURL(previewObjectUrl); } catch (e) { /* 忽略 */ } }
        previewObjectUrl = URL.createObjectURL(blob);
        const img = document.createElement('img');
        img.className = 'mzs-img';
        img.alt = fname;
        img.src = previewObjectUrl;
        box.innerHTML = '';
        box.appendChild(img);
        return;
      }
      const text = await res.text();
      if (ext === '.html' || ext === '.htm') {
        // sandbox 无 allow-same-origin：脚本跑在不透明源，触达不了父页面（spec 硬要求）
        box.innerHTML = '<iframe class="mzs-iframe" sandbox="allow-scripts" title="preview" srcdoc="' + esc(text) + '"></iframe>';
        return;
      }
      if (ext === '.md') {
        let mdHtml = '';
        try {
          if (typeof marked !== 'undefined') mdHtml = marked.parse ? String(marked.parse(text)) : String(marked(text));
          else mdHtml = '<pre class="mzs-pre">' + esc(text) + '</pre>';
        } catch (e) {
          mdHtml = '<pre class="mzs-pre">' + esc(text) + '</pre>';
        }
        try { if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) mdHtml = DOMPurify.sanitize(mdHtml); } catch (e) { /* 保持原样 */ }
        box.innerHTML = '<div class="mzs-md">' + mdHtml + '</div>';
        return;
      }
      // 代码扩展名 → hljs.highlightAuto（缺失/失败退纯文本）；.txt/未知 → 纯 pre
      let body = esc(text);
      if (CODE_EXTS[ext]) {
        try {
          if (typeof hljs !== 'undefined' && hljs.highlightAuto) {
            body = String(hljs.highlightAuto(text).value || body);
          }
        } catch (e) { /* 高亮失败退纯文本 */ }
      }
      box.innerHTML = '<pre class="mzs-pre"><code' +
        (ext ? ' class="language-' + esc(ext.slice(1)) + '"' : '') + '>' + body + '</code></pre>';
    } catch (e) {
      previewErr(box, (e && e.message) || e);
    }
  }

  /* ---------------- 受理表单 / 状态切换 ---------------- */

  function renderForm() {
    setBody(
      '<div class="mzs-form">' +
        '<textarea class="mzs-prompt" placeholder="要交给 ZCode 的任务指令，例如：写一个 FizzBuzz.py 并运行验证"></textarea>' +
        '<div class="mzs-form-hint">提交后在本地沙箱 workspace\\zcode_sandbox\\day24b_runs\\&lt;task_id&gt; 内执行（--mode edit）；事件流实时显示在下方，Ctrl+Enter 可直接提交。</div>' +
        '<button type="button" class="mzs-form-btn">启动任务</button>' +
      '</div>');
  }

  function resetForm() {
    closeStream(true);
    taskId = null;
    state = 'idle';
    lastTurnMs = null;
    resetStreamState();
    setHead('', 'idle');
    renderForm();
  }

  function submitForm() {
    if (launching) return;
    const ta = bodyEl ? bodyEl.querySelector('.mzs-prompt') : null;
    const prompt = ta ? ta.value.trim() : '';
    if (!prompt) { if (ta) ta.focus(); return; }
    launch(prompt);
  }

  async function launch(prompt, language) {
    const panel = ensurePanel();
    showPanel();
    if (launching) return;
    launching = true;
    state = 'idle';
    lastTurnMs = null;
    resetStreamState();
    setHead('', 'running');
    setBody('<div class="mzs-empty">正在受理任务…</div>');
    let res = null;
    try {
      res = await fetch(streamBase() + '/api/engines/zcode/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: String(prompt || ''), language: language || 'python' })
      });
    } catch (e) {
      launching = false;
      setHead('', 'idle');
      setBody('<div class="mzs-error">受理失败：无法连接本地后端（' + esc((e && e.message) || e) + '）。请确认 MoRay 后端已启动。</div>');
      return;
    }
    let j = null;
    try { j = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    const tid = j && (j.task_id || (j.data && j.data.task_id));
    launching = false;
    if (!res.ok || !tid) {
      // 错误链 message||msg||error（后端 429/400 体字段名不统一的既有教训）
      const msg = (j && (j.message || j.msg || j.error)) || ('HTTP ' + res.status);
      setHead('', 'idle');
      setBody('<div class="mzs-error">受理失败：' + esc(msg) + '</div>');
      return;
    }
    taskId = tid;
    state = 'running';
    setHead(tid, 'running');
    setBody('<div class="mzs-empty">waiting for events…</div>');
    openStream(tid);
  }

  /* ---------------- SSE 消费（无重连；[DONE] 即终态） ---------------- */

  function openStream(tid) {
    closeStream(true);
    let src = null;
    try {
      src = new EventSource(streamBase() + '/api/engines/zcode/stream/' + encodeURIComponent(tid));
    } catch (e) {
      addError('EventSource 创建失败：' + ((e && e.message) || e));
      return;
    }
    es = src;
    src.onmessage = function (ev) {
      if (ev.data === '[DONE]') { finishStream(); return; }
      let obj = null;
      try { obj = JSON.parse(ev.data); } catch (e) { return; }
      if (obj && typeof obj === 'object') renderEvent(obj);
    };
    src.onerror = function () {
      // 任务书：重连策略 none —— 出错即关，避免 EventSource 默认自动重连制造假活状态
      const wasRunning = state === 'running';
      closeStream(true);
      if (wasRunning) {
        state = 'done';
        setHead(taskId, 'done');
        addError('连接中断（按任务书不做自动重连）');
      }
    };
  }

  function closeStream(silent) {
    // 终态强制同步冲刷：80ms 节流缓冲里的尾帧不能随连接关闭丢失（U-5 数据完整性铁律）
    flushTextNow();
    flushReasoningNow();
    if (es) {
      es.onmessage = null;
      es.onerror = null;
      try { es.close(); } catch (e) { /* 已关闭 */ }
      es = null;
    }
    if (!silent && taskId) {
      state = 'done';
      setHead(taskId, 'done');
    }
  }

  function finishStream() {
    closeStream(true);
    state = 'done';
    setHead(taskId, 'done');
    addRowHTML('<div class="mzs-row"><span class="mzs-label is-dim">任务已结束</span></div>');
    addRowHTML('<button type="button" class="mzs-again">＋ 新任务</button>');
  }

  /* ---------------- 显示切换 / 启动按钮 ---------------- */

  function showPanel() {
    const panel = ensurePanel();
    panel.style.top = headerH() + 'px';
    panel.classList.add('is-open');
    if (state === 'idle' && bodyEl && !bodyEl.children.length) renderForm();
  }

  function hidePanel() {
    if (panelEl) panelEl.classList.remove('is-open');
    // 不关 EventSource：后台任务继续入队，重开面板不丢事件（重连本来就没有）
  }

  function togglePanel() {
    if (panelEl && panelEl.classList.contains('is-open')) hidePanel();
    else showPanel();
  }

  function injectLauncher() {
    if (document.getElementById(BTN_ID)) return true;
    const toolbar = document.querySelector('#coreInputContainer .input-toolbar');
    if (!toolbar) return false;
    const btn = document.createElement('button');
    btn.id = BTN_ID;
    btn.type = 'button';
    btn.className = 'input-toolbar-btn';
    btn.title = 'ZCode 任务流监控';
    btn.setAttribute('aria-label', 'ZCode 任务流监控');
    btn.innerHTML = '<i data-lucide="zap" class="w-4 h-4"></i>';
    const divider = toolbar.querySelector('.input-toolbar-divider');
    if (divider && divider.parentNode === toolbar) toolbar.insertBefore(btn, divider);
    else toolbar.appendChild(btn);
    btn.addEventListener('click', togglePanel);
    if (typeof refreshIcons === 'function') {
      try { refreshIcons(); } catch (e) { /* 图标刷新失败不影响功能 */ }
    }
    return true;
  }

  function mount() {
    injectStyle();
    // [2026-10-01 U-6-2] ⚡ 图标不再自动注入：主入口改为主输入框 + Agent 模式。
    // 保留 injectLauncher() 函数定义，控制台可手动恢复：
    //   window.MorayZCodeStream._injectLauncher && window.MorayZCodeStream._injectLauncher()
    // 或将 injectLauncher 挂到 window 便于调试。（已在下方导出对象挂为 _injectLauncher）
    // if (injectLauncher()) return;
    // // 输入台由骨架静态提供，理论上首拍即中；此处兜底重试 10s（找不到则放弃，不报错打扰）
    // // 注意：重试循环同样在调 injectLauncher，必须一并停用，否则 ⚡ 会在挂载后 0.5s 内被补回
    // let n = 0;
    // const timer = setInterval(function () {
    //   if (injectLauncher() || ++n >= 20) clearInterval(timer);
    // }, 500);
  }

  window.MorayZCodeStream = {
    mount: mount,
    launch: launch,          // 程序化入口：launch(prompt[, language])（验收/自动化用）
    show: showPanel,
    hide: hidePanel,
    _injectLauncher: injectLauncher   // [2026-10-01 U-6-2] ⚡ 手动恢复/调试入口（不再自动调用）
  };
})();
