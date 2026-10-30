/* ============================================================
   Iris MVP 工作台逻辑（assets/iris.js）
   ------------------------------------------------------------
   全流程（任务书阶段3.1）：
     上传报错截图 → [P1]预处理增强 → OCR 提取错误文本 → 大模型诊断根因
       → 生成修复代码 → ZCode 验证（降级实现，见下）→ 输出结果
   接口（全部来自阶段0审计 pre_audit.md 确认的真实地址，零新增后端）：
     1. POST {GATEWAY}/api/llm/chat   —— MoRay 统一大模型调度接口
        （server/app/llm_proxy.py:156；messages 原样透传，多模态 content 分片
         [{type:'text'},{type:'image_url'}] 走 OpenAI 兼容协议，30_chat.js:1411 同构；
         预算熔断：session_id 分桶，429 = session_budget_exceeded，llm_proxy.py:172-193）
     2. GET  {GATEWAY}/api/engines/list —— 执行引擎只读申报
        （server/app/engines/local_adapter.py:226；zcode 引擎实时态/offline_reason）
   ZCode 真实执行（[P0] 2026-09-27，D:\Iris\docs\p0_audit.md / delivery\p0\）：
     「执行验证」= POST /api/engines/zcode/run（ZCodeAdapter.run_code：沙箱白名单内
     真实子进程执行，四态状态机/审计全复用）。同步接口（≤30s），无轮询路由（红线4）。
       a) GET /api/engines/list 仍用于引擎真实状态展示（流程节点中）；
       b) 执行结果分栏展示 stdout / stderr / 退出码 / 耗时，支持一键复制；
       c) 降级兼容（P0 任务书 3.3，必须保留）：接口调用失败 / 引擎离线 / 503 →
          自动降级为经网关的静态语法校验，页面明示「ZCode 引擎暂不可用，已降级为静态校验」；
       d) 429（iris:zcode 桶 30 次/天用尽）→ 如实提示，不静默降级。
   预算熔断复用方式：所有 /api/llm/chat 调用携带 session_id（iris 独立桶，
     与 MoRay 'conv:' 桶、Argus sid 不撞桶，惯例见 20_ai.js:801-815）；
     收到 429 时输出可执行提示并终止流程（不重试）。
   ------------------------------------------------------------
   [P1] 2026-09-27 增量（任务书「报错修复场景体验与精准度全面升级」，docs/p1_audit.md）：
     阶段2 识别精准度（纯 Canvas，零后端）：
       - 预处理管线 preprocessPipeline：智能区域裁剪（边缘冗余 UI 去除）→ IDE 明暗主题
         检测 → CLAHE 自适应对比度增强（8×8 分块直方图均衡+削顶+双线性插值，亮度通道）→
         终端红字专项增强（红色主导像素定向提亮，深色底生效）→ 可选黑白二值化（Otsu）→
         小图 2× 超分 → 出口 ≤1600px JPEG/PNG。保留「手动框选裁剪」入口（覆盖层拖拽）。
         「关闭增强」= 原 MVP 压缩路径逐字节同参数（1280px/0.8）。
     阶段2 诊断 Prompt 优化：extractFeatures() 从 OCR 文本结构化提取错误类型/行号/
       文件路径/错误码 → buildDiagPrompt() 注入特征与语言策略；无特征时逐字回退原
       DIAG_PROMPT（保留原降级逻辑）；输出分段标记【错误根因】等保持 parseResult 兼容。
     阶段3 多轮迭代修复：state.rounds（≤3 轮），执行失败后「继续修复」把上一轮
       stdout/stderr/静态校验原因作为新报错信息传入下一轮诊断；每轮独立日志与分区展示；
       轮次标签切换（一键回第1轮）+ 清空重开；全部状态前端管理，零后端存储。
     阶段4 基础体验：localStorage 分析历史（iris.* 键空间，最近 20 条，回看/复用/
       删除/自动清理）；detectLang 扩展 Go/C/C++；全页面拖拽热区；分阶段进度提示
       （上传→预处理→识别→诊断→验证）；失败一键重试（保留当前截图）；复制成功 ✓ 反馈；
       pageshow 主题重同步（bfcache 回返场景）。
     不变量：LLM/执行结果一律 textContent 渲染（零注入面）；接口与载荷零变更；
       预算桶 session_id 语义零变更（iris:<id> / iris:zcode）。
   ============================================================
   [P2] 2026-09-27 增量（任务书「排错工作台体验升级」，docs/p2_audit.md）：
     阶段2 长报错日志适配（纯 Canvas + 既有 OCR 接口，零新增后端）：
       - 长图检测：解码高度 > 1600px 自动分段识别；长图解码工作副本上限提升至
         3600px（decodeFileToCanvas 增加可选 cap 参数，默认 2400 不变，既有调用不变）；
       - Canvas 水平切分：单段高 1200px、重叠 100px（段数硬上限 6，预算保护）；
         逐段按当前增强模式（auto/off/bin）复用 P1 增强函数后经 /api/llm/chat 串行识别；
       - 合并：行级最长首尾公共块去重（归一化窗口 ≤12 行）+ 断行修正启发式
         （前段末行与后段首行互为前后缀时保留完整行）；
       - 错误智能定位：classifyError() 纯前端正则分级（fatal/warn/info），结果区
         「错误定位」块优先展示致命错误块（跳过 Warning/Info 前导），完整日志用
         <details> 折叠（长日志自动折叠，展开/收起切换）。
     阶段3 修复前后 Diff 对比：
       - 结果区「修复对比」Tab（默认「修复结果」）；左右分栏 + 行级 LCS diff，
         增=绿 / 删=红 / 改=橙（任务书语义，独立 token）；行号对齐、单滚动容器
         天然同步滚动；「仅看改动行」过滤；逐行语法高亮复用 highlight.js 11.9
         （hljs.highlight 输出自带 HTML 转义，零注入面），失败回退 textContent。
     阶段4 轻量批量 + Markdown 导出 + 错误分级：
       - 批量：≤3 张硬上限（超限整批拒绝并提示分批），队列串行「预处理→OCR→
         诊断→分级」，单项失败不阻断队列；结果按 fatal>warn>info 排序；内联详情；
       - 导出：单条/批量合并 Markdown（截图缩略图/致命块/根因/方案/完整代码/
         执行结果），Blob 下载，文件名=时间+错误类型；
       - 分级徽章：fatal=红(--fail) / warn=橙(--brand) / info=灰，单条轮次头与
         批量列表统一。
     不变量延续：既有单图 OCR/诊断/多轮/历史/ZCode/预算路径逐字保留；
       LLM 内容一律 textContent（hljs 转义输出除外，见上）；接口与载荷零变更。
   ============================================================ */
(function () {
  'use strict';

  /* ---------------- 网关地址（阶段0审计确认） ----------------
     http(s) 部署态（MoRay wallpapers 静态挂载，同源）→ 相对路径；
     file:// 直开态 → 本机网关（MoRay CORS 正则放行 null/file:// 源，main.py:66-72）。 */
  var GATEWAY = (location.protocol === 'http:' || location.protocol === 'https:') ? '' : 'http://127.0.0.1:8000';
  var API_CHAT = GATEWAY + '/api/llm/chat';
  var API_ENGINES = GATEWAY + '/api/engines/list';
  var API_ZCODE_RUN = GATEWAY + '/api/engines/zcode/run'; // [P0] 沙箱真实执行（同步 ≤30s）

  /* 返回目标：部署态 http 下根路径即 MoRay（首屏=应用中心，docs/day24.12）；
     file:// 直开态按 D:\Iris\web 相对上溯到 D:\MoRay\moray-workbench.html。 */
  var MORAY_HOME = (location.protocol === 'file:') ? '../../MoRay/moray-workbench.html' : '/';

  var MAX_RAW_BYTES = 5 * 1024 * 1024;          // 阶段3.2.1：5MB 上限
  var ACCEPT_TYPES = ['image/png', 'image/jpeg'];
  var LLM_TIMEOUT_MS = 300000; // [P0] 对齐后端上游读超时 300s（llm_proxy.py:28）：用户路由层会把
  // 含"代码/修复"关键词的请求送往本地大模型（llm_proxy.py:123-153），耗时远超云端；120s 会误杀有效请求
  var ENGINES_TIMEOUT_MS = 10000;

  /* [P1] 多轮迭代上限（任务书 3.1：最多 3 轮，超过提示手动介入） */
  var MAX_ROUNDS = 3;
  /* [P1] 历史记录上限（任务书 4.1：最近 20 条，超出自动清理最早） */
  var HISTORY_MAX = 20;
  var HISTORY_KEY = 'iris.history'; // iris.* 键空间（与 iris.theme / iris.sessionId 同惯例）

  /* [P2 阶段2] 长报错日志适配参数（任务书 2.1：>1600px 触发分段，段高 1200px/重叠 100px） */
  var LONG_IMG_THRESHOLD = 1600;  // 截图高度阈值（解码工作副本高度）
  var SEGMENT_HEIGHT = 1200;      // 单段高度
  var SEGMENT_OVERLAP = 100;      // 段间重叠（避免边界断行）
  var LONG_DECODE_CAP = 3600;     // 长图解码工作副本上限（保文字分辨率，仅长图路径启用）
  var SEGMENT_MAX = 6;            // 分段数硬上限（预算保护：30 次/天桶内可控）
  var OCR_COLLAPSE_LINES = 14;    // 结果区完整日志折叠行数阈值（任务书 2.2：长日志自动折叠）
  var MERGE_WINDOW = 12;          // 分段合并去重的行匹配窗口
  /* [P2 阶段3] Diff 限制 */
  var DIFF_MAX_LINES = 400;       // 单侧行数上限（LCS DP 面积 400×400，性能无忧）
  /* [P2 阶段4] 批量上限（任务书 4.1：最多 3 张，超限提示分批） */
  var BATCH_MAX = 3;

  /* ---------------- DOM ---------------- */
  function $(id) { return document.getElementById(id); }
  var el = {
    back: $('back-btn'), themeBtn: $('theme-btn'), themeLabel: $('theme-label'),
    moon: $('theme-icon-moon'), sun: $('theme-icon-sun'), envTag: $('env-tag'),
    drop: $('dropzone'), fileInput: $('file-input'),
    previewPanel: $('preview-panel'), previewImg: $('preview-img'),
    fileName: $('file-name'), fileSize: $('file-size'), remove: $('remove-btn'),
    // [P1] 预处理 / 裁剪
    cropBtn: $('crop-btn'), cropLayer: $('crop-layer'), cropSel: $('crop-sel'),
    cropSize: $('crop-size'), cropApply: $('crop-apply'), cropCancel: $('crop-cancel'),
    enhanceMode: $('enhance-mode'), pipeMeta: $('pipe-meta'),
    analyze: $('analyze-btn'), analyzeLabel: $('analyze-label'), modelOverride: $('model-override'),
    progressStrip: $('progress-strip'),
    historyBox: $('history-box'), historyList: $('history-list'), historyClear: $('history-clear'),
    timeline: $('timeline'), logCounter: $('log-counter'), clearLog: $('clear-log-btn'),
    zcodeChip: $('zcode-chip'), zcodeDot: $('zcode-dot'), zcodeChipText: $('zcode-chip-text'),
    placeholder: $('report-placeholder'),
    // [P1] 多轮迭代
    roundBar: $('round-bar'), roundTabs: $('round-tabs'), roundRestart: $('round-restart'),
    roundsWrap: $('rounds-wrap'), continueFix: $('continue-fix-btn'),
    actions: $('report-actions'),
    copyCode: $('copy-code-btn'), copyAll: $('copy-all-btn'), syncZcode: $('sync-zcode-btn'),
    syncZcodeLabel: $('sync-zcode-label'), // [P0] 按钮文案 span（执行中状态切换）
    copyRun: $('copy-run-btn'),
    // [P2] 视图 Tab / Diff / 导出
    viewTabs: $('view-tabs'), tabResult: $('tab-result'), tabDiff: $('tab-diff'),
    diffOnlyLabel: $('diff-only-label'), diffOnly: $('diff-only'), diffWrap: $('diff-wrap'),
    exportMd: $('export-md-btn'),
    // [P2] 批量分析
    batchBox: $('batch-box'), batchPick: $('batch-pick'), batchInput: $('batch-input'),
    batchList: $('batch-list'), batchFoot: $('batch-foot'), batchExport: $('batch-export'),
    dragVeil: $('drag-veil'),
    toast: $('toast')
  };

  /* ---------------- 状态（流程中断时保留已生成内容，阶段3.3） ---------------- */
  var state = {
    file: null,            // 原始 File
    rawCanvas: null,       // [P1] 原始解码位图（≤2400px 工作副本），预处理管线输入
    procCanvas: null,      // [P1] 预处理后位图（预览与上传数据源）
    procTransform: null,   // [P1] {ax,ay,s} 手动裁剪坐标反解用（proc → raw）
    cropRect: null,        // [P1] 手动裁剪矩形（raw 坐标）
    dataUrl: null,         // 预处理后 dataURL（上传载荷）
    running: false,
    runBusy: false,        // [P0] ZCode 执行互斥
    ocrText: '',
    result: { root: '', plan: '', code: '', lang: '' }, // 当前轮结果镜像（兼容既有引用）
    zcodeEngine: null,
    rounds: [],            // [P1] 多轮记录：{n,ts,src,ocrText,errorInfo,result,run,outcome,verifyFailed}
    roundIdx: 0,           // [P1] 当前展示轮（1-based）
    viewing: null,         // [P1] 历史回看记录（非空时右栏只读展示该记录）
    historyId: null,       // [P1] 本轮分析对应的 history 记录 id
    isLongImage: false,    // [P2] 长截图标记（解码高度 > LONG_IMG_THRESHOLD → 分段识别）
    segments: null,        // [P2] 分段元数据 [{y,h}]（日志/导出说明用）
    diffTab: 'result',     // [P2] 右栏视图 Tab：'result' | 'diff'（默认修复结果）
    themeRaw: 'dark'
  };

  function currentRound() {
    if (state.viewing) return state.viewing;
    return state.rounds[state.roundIdx - 1] || null;
  }

  /* ---------------- 工具：日志（时间戳 + 状态标识） ----------------
     状态规范（阶段3.2.6）：进行中=灰 / 成功=#E5734A / 失败=红；info=次级文本 */
  var MARK = { running: '◐', ok: '●', fail: '✕', info: '·' };
  var logCount = 0;
  function log(text, status) {
    status = status || 'info';
    if (status === 'running' && state._runningLogRow) { // 进行中节点就位时收尾上一条
      finishRunningLog('ok');
    }
    var empty = el.timeline.querySelector('.timeline-empty');
    if (empty) empty.remove();
    var row = document.createElement('div');
    row.className = 'log-row';
    row.setAttribute('data-status', status);
    var t = document.createElement('span'); t.className = 'log-time';
    t.textContent = new Date().toTimeString().slice(0, 8);
    var m = document.createElement('span'); m.className = 'log-mark'; m.textContent = MARK[status] || '·';
    var x = document.createElement('span'); x.className = 'log-text'; x.textContent = text; // textContent：LLM 内容零注入
    row.appendChild(t); row.appendChild(m); row.appendChild(x);
    el.timeline.appendChild(row);
    el.timeline.scrollTop = el.timeline.scrollHeight;
    logCount++;
    el.logCounter.textContent = String(logCount);
    if (status === 'running') state._runningLogRow = row;
    return row;
  }
  function finishRunningLog(finalStatus, extraText) {
    var row = state._runningLogRow;
    state._runningLogRow = null;
    if (!row) return;
    row.setAttribute('data-status', finalStatus || 'ok');
    var m = row.querySelector('.log-mark'), x = row.querySelector('.log-text');
    if (m) m.textContent = MARK[finalStatus] || '●';
    if (x && extraText) x.textContent = x.textContent + ' → ' + extraText;
  }

  function toast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    clearTimeout(state._toastTimer);
    state._toastTimer = setTimeout(function () { el.toast.hidden = true; }, 3200);
  }

  /* ---------------- [P1] 分阶段进度提示（任务书 4.3） ----------------
     上传中→预处理中→识别中→诊断中→验证中；失败标红当前阶段 */
  var STAGES = ['upload', 'prep', 'ocr', 'diag', 'verify'];
  function stageSpans() {
    return el.progressStrip.querySelectorAll('span[data-stage]');
  }
  function setStage(name, fail) {
    el.progressStrip.hidden = false;
    var idx = STAGES.indexOf(name);
    var spans = stageSpans();
    for (var i = 0; i < spans.length; i++) {
      spans[i].className = '';
      if (i < idx) spans[i].classList.add('is-done');
      else if (i === idx) spans[i].classList.add(fail ? 'is-fail' : 'is-active');
    }
  }
  function resetStages() {
    el.progressStrip.hidden = true;
    var spans = stageSpans();
    for (var i = 0; i < spans.length; i++) spans[i].className = '';
  }
  function markAllStagesDone() {
    var spans = stageSpans();
    for (var i = 0; i < spans.length; i++) { spans[i].className = ''; spans[i].classList.add('is-done'); }
  }

  /* ---------------- 主题（复用 MoRay 'theme' 设置语义：dark/light/auto，110_polish.js:341） ---------------- */
  function applyTheme(mode) {
    if (mode === 'auto') {
      mode = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', mode);
    var light = (mode === 'light');
    el.themeLabel.textContent = light ? '浅色' : '深色';
    el.moon.hidden = light;
    el.sun.hidden = !light;
  }
  function initTheme() {
    // 优先级：URL 参数（桥接分片携带，阶段4.2）> 本页记忆 > 默认深色
    var t = new URLSearchParams(location.search).get('theme') || localStorage.getItem('iris.theme') || 'dark';
    if (t !== 'dark' && t !== 'light' && t !== 'auto') t = 'dark';
    state.themeRaw = t;
    applyTheme(t);
  }
  el.themeBtn.addEventListener('click', function () {
    var cur = document.documentElement.getAttribute('data-theme');
    var next = (cur === 'light') ? 'dark' : 'light';
    state.themeRaw = next;
    localStorage.setItem('iris.theme', next);
    applyTheme(next);
  });
  /* [P1 4.4] bfcache 回返时重读 URL 参数同步 MoRay 全局主题（pageshow 在缓存恢复时触发） */
  window.addEventListener('pageshow', function () {
    var t = new URLSearchParams(location.search).get('theme');
    if ((t === 'dark' || t === 'light' || t === 'auto') && t !== state.themeRaw) {
      state.themeRaw = t;
      try { localStorage.setItem('iris.theme', t); } catch (e) { /* 隐私态忽略 */ }
      applyTheme(t);
    }
  });

  /* ---------------- 返回按钮（等价 exit：回 MoRay 应用中心） ---------------- */
  el.back.addEventListener('click', function () {
    // 从应用中心卡片跳入时回退保留 MoRay 原状态（bfcache）；直开时回首页
    if (document.referrer && history.length > 1) { history.back(); }
    else { location.href = MORAY_HOME; }
  });

  /* ---------------- 会话标识（预算桶：iris 独立桶，20_ai.js:801-815 惯例） ---------------- */
  function sessionId() {
    var v = new URLSearchParams(location.search).get('session');
    if (!v) v = localStorage.getItem('iris.sessionId');
    if (!v) {
      v = 'iris-local-' + Date.now().toString(36);
      try { localStorage.setItem('iris.sessionId', v); } catch (e) { /* file:// 隐私态忽略 */ }
    }
    return 'iris:' + v;
  }

  /* ---------------- 接口封装：/api/llm/chat（非流式；统一错误/熔断处理） ---------------- */
  function BudgetError(message) { this.name = 'BudgetError'; this.message = message; }
  BudgetError.prototype = Object.create(Error.prototype);

  function apiChat(payload) {
    payload.session_id = sessionId();          // 预算分桶字段（llm_proxy.py:175）
    payload.stream = false;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, LLM_TIMEOUT_MS);
    return fetch(API_CHAT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: ctrl.signal,
      body: JSON.stringify(payload)
    }).then(function (res) {
      clearTimeout(timer);
      return res.json().catch(function () { return {}; }).then(function (j) {
        // 熔断响应体（llm_proxy.py:185-193）：{ok:false, error:'session_budget_exceeded', msg, message}
        if (res.status === 429 || (j && j.error === 'session_budget_exceeded')) {
          throw new BudgetError((j && (j.message || j.msg)) || '本次会话已达调用次数上限');
        }
        if (!res.ok || !j || j.ok !== true) {
          // 人话文案取值顺序与 MoRay 前端一致：message → msg → error（20_ai.js:440-444）
          var msg = (j && (j.message || j.msg || j.error)) || ('HTTP ' + res.status);
          throw new Error(msg);
        }
        return j; // {ok:true, content, reasoning, model, usage}
      });
    }, function (err) {
      clearTimeout(timer);
      if (err && err.name === 'AbortError') throw new Error('请求超时（' + Math.round(LLM_TIMEOUT_MS / 1000) + 's），请检查 MoRay 后端是否在线');
      throw new Error('无法连接 MoRay 网关（' + ((err && err.message) || '网络错误') + '）');
    });
  }

  function apiEngines() {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, ENGINES_TIMEOUT_MS);
    return fetch(API_ENGINES, { signal: ctrl.signal, cache: 'no-store' })
      .then(function (res) { clearTimeout(timer); return res.json(); })
      .then(function (j) {
        if (!j || j.ok !== true || !Array.isArray(j.engines)) throw new Error('engines 响应格式异常');
        return j.engines;
      }, function () { clearTimeout(timer); throw new Error('无法读取引擎列表（后端离线？）'); });
  }

  /* ---------------- 左栏：上传 / 预览 / 校验 / 删除重传 ---------------- */
  function formatBytes(n) {
    return n >= 1024 * 1024 ? (n / 1048576).toFixed(2) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
  }

  /* [P1] 解码原始文件为工作位图（≤2400px 上限，为裁剪/增强保留足量分辨率）
     [P2] 增加可选 cap 参数（默认 2400 不变，既有调用零影响）：长图路径以 3600
     解码保留文字分辨率（服务分段识别，见 [P2 阶段2] 模块） */
  function decodeFileToCanvas(file, cap) {
    if (!cap) cap = 2400;
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onerror = function () { reject(new Error('读取失败')); };
      reader.onload = function () {
        var img = new Image();
        img.onerror = function () { reject(new Error('解码失败')); };
        img.onload = function () {
          try {
            var RAW_CAP = cap;
            var w = img.naturalWidth, h = img.naturalHeight;
            var k = Math.min(1, RAW_CAP / Math.max(w, h, 1));
            var c = document.createElement('canvas');
            c.width = Math.max(1, Math.round(w * k));
            c.height = Math.max(1, Math.round(h * k));
            var ctx = c.getContext('2d');
            ctx.imageSmoothingEnabled = k < 1;
            ctx.drawImage(img, 0, 0, c.width, c.height);
            resolve(c);
          } catch (e) { reject(e); }
        };
        img.src = String(reader.result);
      };
      reader.readAsDataURL(file);
    });
  }

  function takeFile(file) {
    if (!file) return;
    /* [P6 守卫 G1] 输入通道互斥（任务书阶段2）：文本类文件改道「粘贴日志」通道；
       日志通道下拒绝截图输入并提示切换。截图通道行为与 P0-P5 逐字一致。实现见文件尾 [P6] 段。 */
    if (p6 && p6.takeFileGuard(file)) return;
    if (ACCEPT_TYPES.indexOf(file.type) === -1) {
      log('上传失败：仅支持 png/jpg 格式，收到「' + (file.type || '未知') + '」', 'fail');
      toast('仅支持 png/jpg 格式');
      return;
    }
    if (file.size > MAX_RAW_BYTES) {
      log('上传失败：图片 ' + formatBytes(file.size) + ' 超过 5MB 上限', 'fail');
      toast('图片超过 5MB 上限');
      return;
    }
    resetStages();
    setStage('upload');
    decodeFileToCanvas(file).then(function (raw) {
      // [P2] 长图检测：解码高度超阈值 → 以更高上限重新解码（保文字分辨率），随后分段识别
      if (raw.height > LONG_IMG_THRESHOLD) {
        return decodeFileToCanvas(file, LONG_DECODE_CAP).then(function (rawLong) {
          return { raw: rawLong, long: true };
        });
      }
      return { raw: raw, long: false };
    }).then(function (d) {
      var raw = d.raw;
      state.file = file;
      state.rawCanvas = raw;
      state.cropRect = null;
      state.isLongImage = d.long;
      state.segments = null;
      state.viewing = null; // 新截图 = 新分析（历史回看态退出）
      applyProcessing();
      setStage('prep');
      el.previewImg.src = state.dataUrl;
      el.fileName.textContent = file.name || '粘贴图片.png';
      el.fileName.title = file.name || '';
      el.fileSize.textContent = formatBytes(file.size) + ' → 处理后 ' + formatBytes(Math.round(state.dataUrl.length * 0.75));
      el.previewPanel.hidden = false;
      el.drop.classList.add('has-file');
      log('上传成功：' + (file.name || '粘贴图片') + '（' + formatBytes(file.size) + '，' + raw.width + '×' + raw.height + '）' +
        (d.long ? ' · 长截图，将自动分段识别' : ''), 'ok');
      el.analyze.disabled = false;
      runPipeline(); // 阶段5.1.3：上传触发全流程
    }).catch(function (e) {
      setStage('upload', true);
      log('上传失败：图片处理异常（' + (e && e.message || e) + '）', 'fail');
      toast('图片处理失败');
    });
  }

  /* ============================================================
     [P1 阶段2] 预处理管线（纯 Canvas API，零后端依赖）
     管线：智能裁剪 → 主题检测 → 增强（CLAHE/红字增强/二值化）→ 超分 → 出口编码
     「关闭增强」= MVP 原压缩路径同参数（1280px 上限 / JPEG 0.8 / png 保真），
     保证增强开关是一个纯增量、可回退的行为开关。
     ============================================================ */

  function newCanvas(w, h) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return c;
  }

  function cropCanvas(src, rect) {
    var c = newCanvas(rect.w, rect.h);
    c.getContext('2d').drawImage(src, rect.x, rect.y, rect.w, rect.h, 0, 0, c.width, c.height);
    return c;
  }

  function scaleCanvas(src, k) {
    var c = newCanvas(src.width * k, src.height * k);
    var ctx = c.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(src, 0, 0, c.width, c.height);
    return c;
  }

  /* [P1] 内容感知分析副本：降采样后的亮度矩阵（供裁剪/主题检测复用，控性能） */
  function analysisLuma(src, capW) {
    var k = Math.min(1, (capW || 360) / Math.max(src.width, 1));
    var w = Math.max(1, Math.round(src.width * k));
    var h = Math.max(1, Math.round(src.height * k));
    var c = newCanvas(w, h);
    var ctx = c.getContext('2d');
    ctx.drawImage(src, 0, 0, w, h);
    var d = ctx.getImageData(0, 0, w, h).data;
    var Y = new Float32Array(w * h);
    var sum = 0;
    for (var i = 0; i < w * h; i++) {
      Y[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
      sum += Y[i];
    }
    return { w: w, h: h, Y: Y, mean: sum / (w * h) };
  }

  /* [P1 2.1.1] 智能区域裁剪：逐行/逐列统计"文本活跃度"，
     剔除四边近均匀的冗余 UI 带（地址栏/侧边栏/状态栏等）。
     保守策略：单边最多裁 15%，且必须保留 1% 边距，防止误裁报错文本。 */
  function detectContentBox(src) {
    var A = analysisLuma(src, 420);
    var w = A.w, h = A.h, Y = A.Y;
    var i, x, y;
    // 每行/列活跃像素数（与该行/列均值偏差 > 14 视为内容像素）
    var rowAct = new Float32Array(h), colAct = new Float32Array(w);
    var rowMean = new Float32Array(h), colMean = new Float32Array(w);
    for (y = 0; y < h; y++) {
      var s = 0;
      for (x = 0; x < w; x++) s += Y[y * w + x];
      rowMean[y] = s / w;
    }
    for (x = 0; x < w; x++) {
      var s2 = 0;
      for (y = 0; y < h; y++) s2 += Y[y * w + x];
      colMean[x] = s2 / h;
    }
    for (y = 0; y < h; y++) {
      var c1 = 0;
      for (x = 0; x < w; x++) if (Math.abs(Y[y * w + x] - rowMean[y]) > 14) c1++;
      rowAct[y] = c1 / w;
    }
    for (x = 0; x < w; x++) {
      var c2 = 0;
      for (y = 0; y < h; y++) if (Math.abs(Y[y * w + x] - colMean[x]) > 14) c2++;
      colAct[x] = c2 / h;
    }
    var QUIET = 0.004; // 活跃占比 < 0.4% 视为均匀带
    var MAXTRIM = 0.15, PAD = 0.01;
    function span(arr, len) {
      var first = 0, last = len - 1;
      while (first < len && arr[first] < QUIET) first++;
      while (last > first && arr[last] < QUIET) last--;
      return [first, last];
    }
    var rs = span(rowAct, h), cs = span(colAct, w);
    var top = Math.max(0, Math.floor(rs[0] - PAD * h));
    var bottom = Math.min(h - 1, Math.ceil(rs[1] + PAD * h));
    var left = Math.max(0, Math.floor(cs[0] - PAD * w));
    var right = Math.min(w - 1, Math.ceil(cs[1] + PAD * w));
    // 单边限幅 15%
    top = Math.min(top, Math.floor(MAXTRIM * h));
    left = Math.min(left, Math.floor(MAXTRIM * w));
    bottom = Math.max(bottom, Math.floor((1 - MAXTRIM) * h));
    right = Math.max(right, Math.floor((1 - MAXTRIM) * w));
    var kx = src.width / w, ky = src.height / h;
    var bx = Math.round(left * kx), by = Math.round(top * ky);
    var bw = Math.min(src.width - bx, Math.round((right - left + 1) * kx));
    var bh = Math.min(src.height - by, Math.round((bottom - top + 1) * ky));
    var trimmed = (bx > 2 || by > 2 || bw < src.width - 2 || bh < src.height - 2);
    return { x: bx, y: by, w: bw, h: bh, trimmed: trimmed };
  }

  /* [P1 2.1.3] IDE 主题检测：内容区平均亮度阈值（<110 判深色，适配终端/VSCode dark 等） */
  function detectTheme(src) {
    return analysisLuma(src, 200).mean < 110 ? 'dark' : 'light';
  }

  /* [P1 2.1.2] CLAHE（前端实现）：亮度通道 8×8 分块直方图均衡
     —— clip 削顶限幅 + 均匀再分配 + CDF 映射 + 邻块双线性插值，抑制过增强噪声。
     增强作用于亮度，RGB 按 newY/oldY 等比缩放（保色相，红字不偏色）。 */
  function claheApply(ctx, w, h, strength) {
    var img = ctx.getImageData(0, 0, w, h);
    var d = img.data;
    var n = w * h;
    var i, p;
    var Y = new Uint8ClampedArray(n);
    for (i = 0; i < n; i++) {
      p = i * 4;
      Y[i] = (0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2]) | 0;
    }
    var TX = 8, TY = 8;
    var tw = Math.ceil(w / TX), th = Math.ceil(h / TY);
    var maps = new Uint8ClampedArray(TX * TY * 256);
    var hist = new Int32Array(256);
    var t, v, x, y;
    for (t = 0; t < TX * TY; t++) {
      hist.fill(0);
      var tx0 = (t % TX) * tw, ty0 = ((t / TX) | 0) * th;
      var x1 = Math.min(w, tx0 + tw), y1 = Math.min(h, ty0 + th);
      var cnt = 0;
      for (y = ty0; y < y1; y++) for (x = tx0; x < x1; x++) { hist[Y[y * w + x]]++; cnt++; }
      if (!cnt) { for (v = 0; v < 256; v++) maps[t * 256 + v] = v; continue; }
      // clip 削顶 + 均匀再分配（clip=3.0，CLAHE 惯用值）
      var clipLimit = Math.max(1, Math.floor(3.0 * cnt / 256));
      var excess = 0;
      for (v = 0; v < 256; v++) {
        if (hist[v] > clipLimit) { excess += hist[v] - clipLimit; hist[v] = clipLimit; }
      }
      var give = Math.floor(excess / 256);
      for (v = 0; v < 256; v++) hist[v] += give;
      // CDF → 映射表
      var cdf = 0, cdfMin = -1;
      for (v = 0; v < 256; v++) {
        if (hist[v] > 0) { cdf += hist[v]; if (cdfMin < 0) cdfMin = cdf; }
        maps[t * 256 + v] = (cdfMin < 0) ? v
          : Math.max(0, Math.min(255, Math.round((cdf - cdfMin) * 255 / Math.max(1, cnt - cdfMin))));
      }
    }
    // 逐像素：邻块双线性插值映射，再按亮度比例回写 RGB
    var changed = 0;
    for (y = 0; y < h; y++) {
      var gy = Math.min(TY - 1, Math.max(0, y / th - 0.5));
      var ty1 = Math.min(TY - 1, Math.max(0, Math.floor(gy)));
      var ty2 = Math.min(TY - 1, ty1 + 1);
      var fy = gy - ty1;
      for (x = 0; x < w; x++) {
        var gx = Math.min(TX - 1, Math.max(0, x / tw - 0.5));
        var tx1 = Math.min(TX - 1, Math.max(0, Math.floor(gx)));
        var tx2 = Math.min(TX - 1, tx1 + 1);
        var fx = gx - tx1;
        var yv = Y[y * w + x];
        var m11 = maps[(ty1 * TX + tx1) * 256 + yv], m12 = maps[(ty1 * TX + tx2) * 256 + yv];
        var m21 = maps[(ty2 * TX + tx1) * 256 + yv], m22 = maps[(ty2 * TX + tx2) * 256 + yv];
        var mapped = m11 * (1 - fx) * (1 - fy) + m12 * fx * (1 - fy) + m21 * (1 - fx) * fy + m22 * fx * fy;
        var ny = yv + (mapped - yv) * strength;
        p = (y * w + x) * 4;
        var ratio = ny / Math.max(yv, 1);
        if (Math.abs(ny - yv) > 2) changed++;
        d[p] = Math.min(255, d[p] * ratio);
        d[p + 1] = Math.min(255, d[p + 1] * ratio);
        d[p + 2] = Math.min(255, d[p + 2] * ratio);
      }
    }
    ctx.putImageData(img, 0, 0);
    return changed / n;
  }

  /* [P1 2.1.2] 终端红字专项增强：深色底红色报错文字（如 #8b0000）对比度不足，
     对"红色主导"像素定向提亮（等比缩放 RGB，保持色相不漂移）。 */
  function boostRedText(ctx, w, h) {
    var img = ctx.getImageData(0, 0, w, h);
    var d = img.data;
    var n = w * h, hit = 0;
    for (var i = 0; i < n; i++) {
      var p = i * 4, r = d[p], g = d[p + 1], b = d[p + 2];
      if (r > 46 && r > g * 1.22 && r > b * 1.22 && (r - Math.max(g, b)) > 18) {
        var target = Math.min(255, r * 1.38 + 30);
        var k = target / r;
        d[p] = target;
        d[p + 1] = Math.min(255, g * k);
        d[p + 2] = Math.min(255, b * k);
        hit++;
      }
    }
    ctx.putImageData(img, 0, 0);
    return hit / n;
  }

  /* [P1 2.1.2] 3×3 中值去噪（亮度域，RGB 等比缩放保色相）：抑制转发截图的 JPEG 块
     噪声/压缩伪影，防止后续 CLAHE 把噪声一同放大（实测退化图上 CLAHE 增益被噪声
     吞噬甚至为负）。blend<1 为与原图凸混合，避免小字号笔画被过度圆化。 */
  function medianDenoise(ctx, w, h, blend) {
    var img = ctx.getImageData(0, 0, w, h);
    var d = img.data;
    var n = w * h;
    var Y = new Uint8ClampedArray(n);
    var i, p, x, y;
    for (i = 0; i < n; i++) {
      p = i * 4;
      Y[i] = (0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2]) | 0;
    }
    var out = new Uint8ClampedArray(n);
    var win = new Uint8Array(9);
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        var wi = 0;
        for (var dy = -1; dy <= 1; dy++) {
          var ny = y + dy; if (ny < 0) ny = 0; if (ny >= h) ny = h - 1;
          for (var dx = -1; dx <= 1; dx++) {
            var nx = x + dx; if (nx < 0) nx = 0; if (nx >= w) nx = w - 1;
            win[wi++] = Y[ny * w + nx];
          }
        }
        // 9 元素插入排序取中位（无分配，控 GC）
        for (var a = 1; a < 9; a++) {
          var vv = win[a], b = a - 1;
          while (b >= 0 && win[b] > vv) { win[b + 1] = win[b]; b--; }
          win[b + 1] = vv;
        }
        out[y * w + x] = win[4];
      }
    }
    for (i = 0; i < n; i++) {
      var med = out[i], y0 = Y[i];
      if (y0 < 1) continue;
      var ratio = (y0 + (med - y0) * blend) / y0;
      p = i * 4;
      d[p] = Math.min(255, d[p] * ratio);
      d[p + 1] = Math.min(255, d[p + 1] * ratio);
      d[p + 2] = Math.min(255, d[p + 2] * ratio);
    }
    ctx.putImageData(img, 0, 0);
  }

  /* [P1 2.1.2] Otsu 全局阈值二值化（灰度化 + 黑白，适配浅色 IDE / 高对比偏好） */
  function binarizeCanvas(src) {
    var c = newCanvas(src.width, src.height);
    var ctx = c.getContext('2d');
    ctx.drawImage(src, 0, 0);
    var w = c.width, h = c.height;
    var img = ctx.getImageData(0, 0, w, h);
    var d = img.data;
    var n = w * h;
    var hist = new Int32Array(256);
    var i, p, v;
    for (i = 0; i < n; i++) {
      p = i * 4;
      var yv = (0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2]) | 0;
      d[p] = d[p + 1] = d[p + 2] = yv;
      hist[yv]++;
    }
    // Otsu：类间方差最大化
    var sum = 0;
    for (v = 0; v < 256; v++) sum += v * hist[v];
    var sumB = 0, wB = 0, maxVar = -1, thr = 128;
    for (var v2 = 0; v2 < 256; v2++) {
      wB += hist[v2];
      if (!wB) continue;
      var wF = n - wB;
      if (!wF) break;
      sumB += v2 * hist[v2];
      var mB = sumB / wB, mF = (sum - sumB) / wF;
      var between = wB * wF * (mB - mF) * (mB - mF);
      if (between > maxVar) { maxVar = between; thr = v2; }
    }
    for (i = 0; i < n; i++) {
      p = i * 4;
      var out = d[p] > thr ? 255 : 0;
      d[p] = d[p + 1] = d[p + 2] = out;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  /* [P1] 出口编码：小尺寸 png 保真（无损），其余 JPEG 0.85（文字清晰度优先） */
  function encodeCanvas(c, preferPng) {
    if (preferPng && c.width * c.height <= 800 * 600) return c.toDataURL('image/png');
    return c.toDataURL('image/jpeg', 0.85);
  }

  /* MVP 原压缩路径（「关闭增强」模式逐参数保留：compressImageFile(file,1280,0.8)，95_features.js:23 同参） */
  function legacyEncode(src, isPng) {
    var MAXW = 1280, QUALITY = 0.8;
    var scale = Math.min(1, MAXW / Math.max(src.width, 1));
    var c = newCanvas(src.width * scale, src.height * scale);
    var ctx = c.getContext('2d');
    ctx.drawImage(src, 0, 0, c.width, c.height);
    return (isPng && scale === 1) ? c.toDataURL('image/png') : c.toDataURL('image/jpeg', QUALITY);
  }

  /* [P1] 预处理管线编排。opts: {mode:'auto'|'off'|'bin', manualCrop, isPng} */
  function runPreprocessPipeline(src, opts) {
    var meta = { theme: null, steps: [] };
    if (opts.mode === 'off' && !opts.manualCrop) {
      // 原图直传：MVP 原路径
      return { canvas: src, meta: meta, dataUrl: legacyEncode(src, opts.isPng), legacy: true };
    }
    var c = src;
    var ax = 0, ay = 0;
    if (opts.manualCrop) {
      var r = opts.manualCrop;
      c = cropCanvas(c, r);
      ax = r.x; ay = r.y;
      meta.steps.push('手动裁剪 ' + r.w + '×' + r.h);
    } else if (opts.mode !== 'off') {
      var box = detectContentBox(c);
      if (box.trimmed && box.w >= 64 && box.h >= 48) {
        c = cropCanvas(c, box);
        ax = box.x; ay = box.y;
        meta.steps.push('智能裁剪 ' + box.w + '×' + box.h);
      }
    }
    meta.theme = detectTheme(c);
    if (opts.mode === 'bin') {
      c = binarizeCanvas(c);
      meta.steps.push('黑白二值化');
    } else if (opts.mode === 'auto') {
      var ctx = c.getContext('2d');
      var w = c.width, h = c.height;
      var dark = meta.theme === 'dark';
      medianDenoise(ctx, w, h, dark ? 0.55 : 0.35);
      meta.steps.push('中值去噪');
      var ratio = claheApply(ctx, w, h, dark ? 0.75 : 0.45);
      if (ratio > 0.02) meta.steps.push(dark ? 'CLAHE+亮度增强' : 'CLAHE 对比度');
      if (dark) {
        var red = boostRedText(ctx, w, h);
        if (red > 0.003) meta.steps.push('红字增强');
      }
    }
    // 超分 / 出口限幅：小图 2× 提升小字号识别；大图缩至 ≤1600px
    var maxDim = Math.max(c.width, c.height);
    var s = 1;
    if (opts.mode !== 'off') {
      if (maxDim < 800) s = 2;
      else if (maxDim > 1600) s = 1600 / maxDim;
    } else if (maxDim > 1600) {
      s = 1600 / maxDim;
    }
    if (s !== 1) {
      c = scaleCanvas(c, s);
      meta.steps.push(s > 1 ? '2×超分' : '限幅1600px');
    }
    state.procTransform = { ax: ax, ay: ay, s: s };
    return { canvas: c, meta: meta, dataUrl: encodeCanvas(c, opts.isPng), legacy: false };
  }

  function metaText(meta) {
    var parts = [];
    if (meta.theme) parts.push(meta.theme === 'dark' ? '深色主题' : '浅色主题');
    if (meta.steps.length) parts = parts.concat(meta.steps);
    return parts.join(' · ');
  }

  /* [P1] 应用当前预处理设置：rawCanvas → 管线 → 预览/载荷同步（手动裁剪/切换模式共用） */
  function applyProcessing() {
    if (!state.rawCanvas) return;
    var mode = el.enhanceMode.value || 'auto';
    state.procTransform = { ax: 0, ay: 0, s: 1 }; // 关闭增强/无裁剪路径的恒等变换默认值
    var out = runPreprocessPipeline(state.rawCanvas, {
      mode: mode,
      manualCrop: state.cropRect,
      isPng: !!(state.file && state.file.type === 'image/png')
    });
    state.procCanvas = out.canvas;
    state.dataUrl = out.dataUrl;
    el.previewImg.src = state.dataUrl;
    if (out.legacy && !out.meta.steps.length) {
      el.pipeMeta.hidden = true;
    } else {
      el.pipeMeta.hidden = false;
      el.pipeMeta.textContent = metaText(out.meta) + ' · 出口 ' + out.canvas.width + '×' + out.canvas.height;
    }
    return out;
  }

  el.enhanceMode.addEventListener('change', function () {
    if (!state.rawCanvas) return;
    applyProcessing();
    log('预处理策略已切换：' + el.enhanceMode.options[el.enhanceMode.selectedIndex].text, 'info');
  });

  /* ---------------- [P1 2.1.1] 手动框选裁剪（覆盖层拖拽 → 坐标反解到原始位图） ----------------
     显示坐标 → procCanvas 像素（image content rect 换算）→ 原始位图（逆管线变换 ax/ay/s） */
  function imageContentRect(img) {
    var iw = img.naturalWidth || 1, ih = img.naturalHeight || 1;
    var r = img.getBoundingClientRect();
    var s = Math.min(r.width / iw, r.height / ih);
    var w = iw * s, h = ih * s;
    return { x: r.left + (r.width - w) / 2, y: r.top + (r.height - h) / 2, w: w, h: h, scale: s };
  }

  var cropDrag = null;
  function enterCropMode() {
    if (!state.procCanvas || state.running) return;
    el.cropLayer.hidden = false;
    el.cropSel.hidden = true;
    el.cropApply.disabled = true;
    cropDrag = null;
    log('框选裁剪：在截图上按住拖拽选出报错区域，点击「应用裁剪」生效', 'info');
  }
  function exitCropMode() {
    el.cropLayer.hidden = true;
    el.cropSel.hidden = true;
    cropDrag = null;
  }
  el.cropBtn.addEventListener('click', enterCropMode);
  el.cropCancel.addEventListener('click', function () { exitCropMode(); log('已取消框选裁剪', 'info'); });

  el.cropLayer.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    e.preventDefault();
    var layerRect = el.cropLayer.getBoundingClientRect();
    cropDrag = { x0: e.clientX, y0: e.clientY, layerRect: layerRect };
    el.cropSel.hidden = false;
    updateCropSel(e.clientX, e.clientY);
  });
  document.addEventListener('mousemove', function (e) {
    if (!cropDrag) return;
    updateCropSel(e.clientX, e.clientY);
  });
  document.addEventListener('mouseup', function () {
    if (!cropDrag) return;
    var w = el.cropSel.offsetWidth, h = el.cropSel.offsetHeight;
    cropDrag = null;
    // 过小选区视为误触，保持可重选
    if (w < 12 || h < 12) { el.cropSel.hidden = true; el.cropApply.disabled = true; return; }
    el.cropApply.disabled = false;
  });

  function updateCropSel(cx, cy) {
    if (!cropDrag) return;
    var lr = cropDrag.layerRect;
    var x1 = Math.max(lr.left, Math.min(lr.right, cropDrag.x0));
    var y1 = Math.max(lr.top, Math.min(lr.bottom, cropDrag.y0));
    var x2 = Math.max(lr.left, Math.min(lr.right, cx));
    var y2 = Math.max(lr.top, Math.min(lr.bottom, cy));
    var left = Math.min(x1, x2) - lr.left, top = Math.min(y1, y2) - lr.top;
    var w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    el.cropSel.style.left = left + 'px';
    el.cropSel.style.top = top + 'px';
    el.cropSel.style.width = w + 'px';
    el.cropSel.style.height = h + 'px';
    // 选中区对应的 procCanvas 像素尺寸提示
    var pc = imageContentRect(el.previewImg);
    if (pc.w > 0 && pc.h > 0) {
      var pw = Math.round(w / pc.scale), ph = Math.round(h / pc.scale);
      el.cropSize.textContent = pw + '×' + ph + ' px';
    }
  }

  el.cropApply.addEventListener('click', function () {
    if (el.cropSel.hidden) return;
    var lr = el.cropLayer.getBoundingClientRect();
    var sel = {
      x: parseFloat(el.cropSel.style.left) || 0,
      y: parseFloat(el.cropSel.style.top) || 0,
      w: el.cropSel.offsetWidth,
      h: el.cropSel.offsetHeight
    };
    var pc = imageContentRect(el.previewImg);
    if (pc.w <= 0 || pc.h <= 0) { exitCropMode(); return; }
    // 显示坐标 → procCanvas 像素（相对图像内容区归一）
    var px = (lr.left + sel.x - pc.x) / pc.scale;
    var py = (lr.top + sel.y - pc.y) / pc.scale;
    var pw = sel.w / pc.scale, ph = sel.h / pc.scale;
    px = Math.max(0, Math.min(state.procCanvas.width - 4, px));
    py = Math.max(0, Math.min(state.procCanvas.height - 4, py));
    pw = Math.max(4, Math.min(state.procCanvas.width - px, pw));
    ph = Math.max(4, Math.min(state.procCanvas.height - py, ph));
    // procCanvas → 原始位图（逆管线变换）
    var tf = state.procTransform || { ax: 0, ay: 0, s: 1 };
    var raw = {
      x: Math.round(px / tf.s + tf.ax),
      y: Math.round(py / tf.s + tf.ay),
      w: Math.round(pw / tf.s),
      h: Math.round(ph / tf.s)
    };
    raw.x = Math.max(0, Math.min(state.rawCanvas.width - 4, raw.x));
    raw.y = Math.max(0, Math.min(state.rawCanvas.height - 4, raw.y));
    raw.w = Math.max(4, Math.min(state.rawCanvas.width - raw.x, raw.w));
    raw.h = Math.max(4, Math.min(state.rawCanvas.height - raw.y, raw.h));
    state.cropRect = raw;
    applyProcessing();
    exitCropMode();
    log('手动裁剪已应用：原始区域 ' + raw.w + '×' + raw.h + '，识别范围已更新', 'ok');
  });

  function clearFile() {
    state.file = null;
    state.rawCanvas = null;
    state.procCanvas = null;
    state.cropRect = null;
    state.dataUrl = null;
    el.previewPanel.hidden = true;
    el.previewImg.removeAttribute('src');
    el.fileInput.value = '';
    el.analyze.disabled = true;
    el.pipeMeta.hidden = true;
    resetStages();
    log('已移除截图，可重新上传', 'info');
  }

  el.drop.addEventListener('click', function () { if (!state.running) el.fileInput.click(); });
  el.drop.addEventListener('keydown', function (e) {
    if ((e.key === 'Enter' || e.key === ' ') && !state.running) { e.preventDefault(); el.fileInput.click(); }
  });
  el.fileInput.addEventListener('change', function () { takeFile(el.fileInput.files && el.fileInput.files[0]); });
  ['dragenter', 'dragover'].forEach(function (ev) {
    el.drop.addEventListener(ev, function (e) { e.preventDefault(); el.drop.classList.add('dragover'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    el.drop.addEventListener(ev, function (e) { e.preventDefault(); el.drop.classList.remove('dragover'); });
  });
  el.drop.addEventListener('drop', function (e) {
    if (state.running) return;
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) takeFile(f);
  });

  /* ---------------- [P1 4.3] 全页面拖拽热区（document 级；dropzone 自身事件优先，
     已 preventDefault 的事件在此跳过，避免双重 takeFile） ---------------- */
  var dragDepth = 0;
  function dragHasFile(e) {
    var dt = e.dataTransfer;
    return !!(dt && Array.prototype.indexOf.call(dt.types || [], 'Files') !== -1);
  }
  document.addEventListener('dragenter', function (e) {
    if (state.running || !dragHasFile(e)) return;
    dragDepth++;
    if (el.drop && !el.drop.contains(e.target)) el.dragVeil.hidden = false;
  });
  document.addEventListener('dragover', function (e) {
    if (state.running || !dragHasFile(e)) return;
    e.preventDefault(); // 允许在页面任意位置释放
  });
  document.addEventListener('dragleave', function (e) {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) el.dragVeil.hidden = true;
  });
  document.addEventListener('drop', function (e) {
    dragDepth = 0;
    el.dragVeil.hidden = true;
    if (e.defaultPrevented) return; // dropzone 已处理
    e.preventDefault();
    if (state.running) return;
    var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) takeFile(f);
  });

  // 粘贴截图（MoRay 输入台同款交互，95_features.js:75-82）
  window.addEventListener('paste', function (e) {
    if (state.running || !e.clipboardData) return;
    /* [P6 守卫 G2] 文本通道下剪贴板纯文本改道日志框（日志框内粘贴保持浏览器原生行为） */
    if (p6 && p6.pasteGuard(e)) return;
    var items = e.clipboardData.items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf('image/') === 0) {
        takeFile(items[i].getAsFile());
        break;
      }
    }
  });
  el.remove.addEventListener('click', function () { if (!state.running) clearFile(); });

  /* ============================================================
     [P2 阶段2] 长报错日志适配（纯前端，零新增后端）
       buildSegments      —— Canvas 水平切分（段高 1200px / 重叠 100px / 上限 6 段）
       prepareSegment     —— 按当前增强模式复用 P1 增强函数处理单段
       normLine/mergeSegmentTexts —— 行级重叠去重 + 断行修正
       classifyLine/classifyError —— 错误分级（fatal/warn/info）与致命块提取
     依据：任务书阶段2；docs/p2_audit.md §2 技术可行性
     ============================================================ */

  /* 长图切分：对解码工作副本按高度开窗，末段不足整高时按实际剩余高度 */
  function buildSegments(src) {
    var segs = [];
    var step = SEGMENT_HEIGHT - SEGMENT_OVERLAP;
    for (var y = 0; y < src.height && segs.length < SEGMENT_MAX; y += step) {
      var sh = Math.min(SEGMENT_HEIGHT, src.height - y);
      segs.push({ y: y, h: sh, canvas: cropCanvas(src, { x: 0, y: y, w: src.width, h: sh }) });
      if (y + sh >= src.height) break;
    }
    return segs;
  }

  /* 单段增强：遵循左栏「预处理」当前模式（auto/off/bin），复用 P1 既有函数；
     任一步失败回退原始段（不阻断识别） */
  function prepareSegment(c, mode) {
    try {
      if (mode === 'bin') return binarizeCanvas(c);
      if (mode === 'auto') {
        var ctx = c.getContext('2d');
        var dark = detectTheme(c) === 'dark';
        medianDenoise(ctx, c.width, c.height, dark ? 0.55 : 0.35);
        claheApply(ctx, c.width, c.height, dark ? 0.75 : 0.45);
        if (dark) boostRedText(ctx, c.width, c.height);
      }
    } catch (e) { /* 增强失败用原图段 */ }
    return c;
  }

  /* 行归一化（重叠匹配用）：压缩空白 + 小写 */
  function normLine(s) {
    return String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }

  /* [P2] 思考型模型清理：剥离 OCR 输出中泄漏的 <think>...</think> 推理块与孤儿标记行。
     实测（8011 隔离栈）：本地视觉模型每段输出都包裹 <think> 推理，推理文字含
     "traceback" 等词会污染错误分级与致命块；剥离后合并文本为纯 OCR 内容。
     仅移除成对块与独立标记行，正文原样保留。 */
  function stripThinkBlocks(text) {
    var s = String(text || '');
    s = s.replace(/<think>[\s\S]*?<\/think>/gi, '');
    s = s.replace(/^\s*<\/?think>\s*$/gim, '');
    return s.replace(/\n{3,}/g, '\n\n').trim();
  }

  /* 分段文本合并（任务书 2.1：自动去重重叠区域，修正断行拆分的代码行）
     策略：
     ① 断行修正——前段末行与后段首行归一后互为前缀/后缀（残缺行），保留完整版；
     ② 重叠去重——前块尾部与后块头部最长公共行块（窗口 ≤MERGE_WINDOW 行）整块去重；
     匹配不上时保留两份文本（宁可重复不丢行）。 */
  function mergeSegmentTexts(parts) {
    var dropped = 0, fixedBreaks = 0;
    var out = [];
    for (var p = 0; p < parts.length; p++) {
      var pl = String(parts[p] || '').split('\n');
      while (pl.length && !pl[0].trim()) pl.shift();
      while (pl.length && !pl[pl.length - 1].trim()) pl.pop();
      if (!pl.length) continue;
      if (!out.length) { out = pl; continue; }

      // ① 断行修正（残缺行长度 ≥5 才参与，防止碎片误合并）
      var a = out[out.length - 1], b = pl[0];
      var na = normLine(a), nb = normLine(b);
      if (na && nb && na !== nb && na.length >= 5 && nb.length >= 5) {
        if (nb.indexOf(na) === 0) {          // 后段首行 = 前段末行的完整版
          out[out.length - 1] = b; pl.shift(); fixedBreaks++;
        } else if (na.indexOf(nb) === 0) {   // 前段末行为完整版
          pl.shift(); fixedBreaks++;
        } else if (nb.length < na.length && na.slice(-nb.length) === nb) {
          pl.shift(); fixedBreaks++;         // 后段首行为前段末行的尾部残缺
        }
      }
      // ② 整行重叠去重（最长公共首尾块）
      var W = Math.min(MERGE_WINDOW, out.length, pl.length);
      var best = 0;
      for (var k = W; k >= 1; k--) {
        var match = true;
        for (var i = 0; i < k; i++) {
          if (normLine(out[out.length - k + i]) !== normLine(pl[i])) { match = false; break; }
        }
        if (match) { best = k; break; }
      }
      if (best > 0) { pl = pl.slice(best); dropped += best; }
      for (var j = 0; j < pl.length; j++) out.push(pl[j]);
    }
    return { text: stripThinkBlocks(out.join('\n')), lines: out.length, dropped: dropped, fixedBreaks: fixedBreaks };
  }

  /* [P2 2.2/4.3] 单行分级：fatal / warn / info / ''（中性）。
     顺序：先 warn/info（"Warning: ..." 不误判致命），再致命特征 */
  function classifyLine(line) {
    var s = String(line || '');
    if (!s.trim()) return '';
    if (/\bwarning\b|\bdeprecat/i.test(s) || /^\s*(warn|警告)/i.test(s)) return 'warn';
    if (/^\s*(info|debug|notice|hint|verbose|提示)\b/i.test(s)) return 'info';
    if (/(traceback|fatal error|panic:|segmentation fault|core dumped|uncaught\s|compilation error|build failed|\bexception\b|\w*error\b\s*:|\berror\b\s*:|\w+error\b|error\[|\b错误\b|\b失败\b)/i.test(s)) return 'fatal';
    return '';
  }

  /* 堆栈相关行（致命块延续判定）：File "..."/ at .../缩进代码行/.../^ 标记等 */
  function isStackLine(line) {
    return /^\s*(at\s|File\s"|in\s<module>|from\s|raise\s|\^\s*~|\.{3,}\s|\^+)/.test(String(line || ''));
  }

  /* [P2 2.2/4.3] 整段分级与致命块提取：
     - level：fatal > warn > info（按出现计数）
     - firstIdx：第一个致命行索引（自动跳过前导 Warning/Info/Debug）
     - blockLines：首个致命行起的连续相关块（致命/堆栈/少量上下文），上限 30 行 */
  function classifyError(text) {
    var lines = stripThinkBlocks(text).split('\n'); // [P2] 思考块先剥离，防推理文字污染分级
    var firstIdx = -1, lastFatal = -1, warnCount = 0, infoCount = 0, fatalCount = 0;
    for (var i = 0; i < lines.length; i++) {
      var c = classifyLine(lines[i]);
      if (c === 'fatal') { fatalCount++; if (firstIdx < 0) firstIdx = i; lastFatal = i; }
      else if (c === 'warn') warnCount++;
      else if (c === 'info') infoCount++;
    }
    var level = fatalCount ? 'fatal' : (warnCount ? 'warn' : 'info');
    var blockLines = [];
    if (firstIdx >= 0) {
      var neutralRun = 0;
      for (var j = firstIdx; j < lines.length && blockLines.length < 30; j++) {
        var cj = classifyLine(lines[j]);
        if (cj === 'fatal' || isStackLine(lines[j])) {
          blockLines.push(lines[j]); neutralRun = 0;
        } else if (cj === 'warn' || cj === 'info') {
          continue; // [P2 2.2] 自动跳过警告/提示行（任务书 2.2）
        } else if (lines[j].trim()) {
          neutralRun++;
          if (neutralRun <= 2) blockLines.push(lines[j]); // 保留最多 2 行中性上下文
        } else {
          neutralRun++;
          if (neutralRun <= 1) blockLines.push(lines[j]);
        }
        if (neutralRun >= 3) break;
      }
      // 去尾部中性行
      while (blockLines.length && !classifyLine(blockLines[blockLines.length - 1]) && !isStackLine(blockLines[blockLines.length - 1])) blockLines.pop();
    }
    return {
      level: level, firstIdx: firstIdx, lastFatal: lastFatal,
      fatalCount: fatalCount, warnCount: warnCount, infoCount: infoCount,
      blockLines: blockLines, totalLines: lines.length
    };
  }

  /* [P2 4.3] 分级徽章元数据（fatal=红 / warn=橙(--brand) / info=灰） */
  var LEVEL_META = {
    fatal: { text: '致命错误', cls: 'is-fatal' },
    warn: { text: '警告', cls: 'is-warn' },
    info: { text: '提示', cls: 'is-info' }
  };
  function levelRank(level) { return level === 'fatal' ? 0 : (level === 'warn' ? 1 : 2); }

  /* ---------------- 固化 Prompt（P0 任务书 3.2.3 原文，逐字保留为回退路径） ---------------- */
  var DIAG_PROMPT =
    '你是专业代码故障诊断专家，请根据以下OCR识别的报错信息，精准分析错误根因，给出修复方案和完整可运行的修复代码。\n' +
    '报错文本：{{OCR_RESULT}}\n' +
    '严格按以下格式输出，不要多余内容：\n' +
    '【错误根因】\n（根因描述）\n【修复方案】\n（方案说明）\n【修复代码】\n（完整代码块）';

  var OCR_PROMPT =
    '你是 OCR 引擎。请精确提取图片中的全部文字，按原始版面顺序输出；' +
    '如果是代码报错或终端截图，完整保留错误类型、错误消息、堆栈与代码行信息。' +
    '只输出提取的文字本身，不要任何解释、评论或额外格式。';

  var VERIFY_PROMPT =
    '请仅对以下代码做静态语法检查：不要运行代码，不要给修改建议，不要重写代码。\n' +
    '若语法正确，第一行输出：VERDICT: PASS\n' +
    '若发现语法错误，第一行输出：VERDICT: FAIL，随后用一行说明错误位置与原因。\n' +
    '语言：{{LANG}}\n代码：\n{{CODE}}';

  /* ============================================================
     [P1 阶段2] 诊断 Prompt 优化（任务书 2.2）
     1) extractFeatures：纯前端正则，从 OCR 文本提取结构化特征
     2) buildDiagPrompt：注入特征 + 语言策略 + 输出格式硬约束；
        无任何特征命中时逐字回退 DIAG_PROMPT（保留原降级逻辑）
     3) 分段标记【错误根因】/【修复方案】/【修复代码】不变 → parseResult 兼容
     ============================================================ */
  var ERROR_TYPE_RE = /(SyntaxError|TypeError|ValueError|NameError|IndexError|KeyError|AttributeError|ImportError|ModuleNotFoundError|ZeroDivisionError|FileNotFoundError|PermissionError|ConnectionRefusedError|RecursionError|IndentationError|TabError|UnicodeDecodeError|StopIteration|ArithmeticError|NullPointerException|ArrayIndexOutOfBoundsException|ClassCastException|IllegalArgumentException|NoSuchMethodException|SegmentationFault|StackOverflowError|OutOfMemoryError|ReferenceError|RangeError|AssertionError|Uncaught\s+\w+Error|panic:|fatal error:|COMPILATION ERROR|error:)/;

  var LANG_LABEL = { python: 'Python', javascript: 'JavaScript', java: 'Java', go: 'Go', c: 'C', cpp: 'C++', plaintext: '' };

  /* [P1 2.2.3] 多语言诊断策略（各语言高频踩坑点，作为提示注入） */
  var LANG_STRATEGY = {
    python: '（策略：优先核对 Traceback 最后一行的异常类型与消息，检查类型/变量名/缩进/导入路径）',
    javascript: '（策略：注意 undefined/null 属性访问、异步时序、作用域与拼写，浏览器报错含调用栈）',
    java: '（策略：堆栈第一行是根因，检查空指针/越界/类型转换/包名类名一致性）',
    go: '（策略：检查 error 返回值处理、nil 判断、未使用变量或导入、接口断言）',
    c: '（策略：检查指针/内存/数组越界/分号/头文件包含，按编译器指示的行号定位）',
    cpp: '（策略：检查指针/内存/越界/命名空间/模板匹配，注意编译器指示的行号）',
    plaintext: ''
  };

  function detectLang(code) {
    var s = String(code || '');
    // python：import 后不允许紧跟引号/括号（Go 的 import "x" / import ( 不误判）
    if (/Traceback/.test(s) || /^\s*(def |import (?!["(])|from .+ import|print\(|class .+:|if __name__)/m.test(s)) return 'python';
    if (/\b(package main|func |fmt\.)/.test(s)) return 'go';
    if (/(#\s*include|printf\s*\(|int\s+main\s*\()/.test(s) || /std::|cout\s*<|namespace\s+\w+/.test(s)) {
      return /std::|cout\s*<|namespace\s+\w+|::\w+|template\s*</.test(s) ? 'cpp' : 'c';
    }
    if (/\b(function|const|let|=>|console\.log)/.test(s)) return 'javascript';
    if (/\b(public static void|System\.out)/.test(s)) return 'java';
    return 'plaintext';
  }

  /* [P1 2.2.1] 结构化特征提取（错误类型 / 行号 / 文件路径 / 错误码） */
  function extractFeatures(ocr) {
    var f = { type: null, lines: [], paths: [], codes: [], lang: detectLang(ocr) };
    if (!ocr) return f;
    var m = ocr.match(ERROR_TYPE_RE);
    if (m) f.type = m[1];
    // 行号：`line 42` / `第42行` / `path:42:10`
    var reLine = /(?:line|Line|LINE|行)\s*[:：]?\s*(\d{1,4})/g;
    var seen = {};
    var mm;
    while ((mm = reLine.exec(ocr)) && f.lines.length < 5) {
      if (!seen[mm[1]]) { seen[mm[1]] = 1; f.lines.push(mm[1]); }
    }
    var reLoc = /:(\d{1,4})(?::\d{1,4})?(?=\s|$|"|'|\))/g;
    while ((mm = reLoc.exec(ocr)) && f.lines.length < 5) {
      if (!seen[mm[1]]) { seen[mm[1]] = 1; f.lines.push(mm[1]); }
    }
    // 文件路径：Windows / POSIX / Python `File "x", line n`（排除提示符尾随字符 > < （ ））
    var rePath = /(?:[A-Za-z]:\\[^\s:"'，。;）)><]+|(?:\/[\w.\-]+){2,}\/[\w.\-]+|[\w\-]+(?:\.[a-z]{1,4})?:?\s*,\s*line\s+\d+)/g;
    while ((mm = rePath.exec(ocr)) && f.paths.length < 5) f.paths.push(mm[0].trim());
    var rePy = /File "([^"]+)",\s*line (\d+)/g;
    while ((mm = rePy.exec(ocr))) {
      if (f.paths.indexOf(mm[1]) === -1 && f.paths.length < 5) f.paths.push(mm[1] + ':' + mm[2]);
      if (f.lines.indexOf(mm[2]) === -1 && f.lines.length < 5) f.lines.push(mm[2]);
    }
    // 错误码：E### / errno / exit code / 0x 地址
    var reCode = /\b(?:E\d{3,4}|errno[:：]?\s*\d+|exit code\s*-?\d+|exit status\s*\d+|0x[0-9A-Fa-f]{6,12})\b/g;
    while ((mm = reCode.exec(ocr)) && f.codes.length < 5) f.codes.push(mm[0]);
    return f;
  }

  /* [P1 2.2.2] 结构化诊断 Prompt（保留原输出分段契约） */
  function buildDiagPrompt(ocr, features) {
    var hasFeature = !!(features.type || features.lines.length || features.paths.length || features.codes.length ||
      (features.lang && features.lang !== 'plaintext'));
    if (!hasFeature) {
      // 无特征命中：逐字回退原固化 Prompt（原降级逻辑）
      return DIAG_PROMPT.replace('{{OCR_RESULT}}', ocr);
    }
    var L = [];
    L.push('你是专业代码故障诊断专家。以下是从报错截图 OCR 提取的文本，以及自动提取的结构化特征。');
    L.push('【结构化特征】');
    if (features.type) L.push('- 错误类型: ' + features.type);
    if (features.lines.length) L.push('- 涉及行号: ' + features.lines.join(', '));
    if (features.paths.length) L.push('- 涉及文件: ' + features.paths.slice(0, 5).join(' ; '));
    if (features.codes.length) L.push('- 错误码/退出码: ' + features.codes.slice(0, 5).join(', '));
    var langLabel = LANG_LABEL[features.lang] || '';
    if (langLabel) L.push('- 代码语言: ' + langLabel + (LANG_STRATEGY[features.lang] || ''));
    L.push('【报错文本】');
    L.push(ocr);
    L.push('请结合以上特征精准定位错误根因，给出修复方案和完整可运行的修复代码。');
    L.push('输出要求（严格遵守）：');
    L.push('1. 严格按以下分段输出，不要多余内容：【错误根因】、【修复方案】、【修复代码】；');
    L.push('2. 修复代码放在一个 ``` 围栏代码块中并在第一行标注语言（如 ```python），代码必须完整可运行、可直接复制使用；');
    L.push('3. 不要输出多个代码块，不要重复报错文本，不要输出与修复无关的内容。');
    return L.join('\n');
  }

  /* [v2] 代码评审 Prompt（代码分析 v2）：OCR 无报错特征但含代码形状时使用。
     输出新分段【总体评价】/【问题清单】/【最佳实践代码】（不复用诊断三段），
     由 parseResult 评审分支解析；词表 ERR_PAT/CODE_PAT 自 MoRay 87 桥移交至此，
     作为 agiDiagnose 分流依据（桥侧守卫词表第二步另行摘除重叠）。 */
  var ERR_PAT = /(traceback|exception|error|failed|failure|fatal|panic|segmentation|syntax\s*error|cannot|unable|invalid|undefined|not\s+defined|no\s+module|refused|denied|deprecated|warning\s*:|错误|异常|报错|失败|未定义|无法|不能|致命|堆栈|警告)/i;
  var CODE_PAT = /(\.py|\.js|\.java|\.ts|\.c|\.cpp|\.go|\.rs|Traceback|File\s+"|line\s+\d+|def\s+\w+\(|import\s+\w+|from\s+\w+\s+import|function\s+\w+|const\s+\w+|let\s+\w+|var\s+\w+|=>|console\.|print\(|Exception|Error|错误|异常|行\s*\d+|第\s*\d+\s*行)/i;

  function buildReviewPrompt(ocr) {
    var L = [];
    L.push('你是资深代码评审专家。以下是从截图 OCR 提取的代码文本（未检出报错特征，属于代码质量评审场景）。');
    L.push('【代码文本】');
    L.push(ocr);
    L.push('请从命名、重复、边界条件、异常处理、可读性等维度评审上述代码。');
    L.push('输出要求（严格遵守）：');
    L.push('1. 严格按以下分段输出，不要多余内容：【总体评价】、【问题清单】、【最佳实践代码】；');
    L.push('2. 【总体评价】2-3 句，概括代码整体质量/风格/可读性；');
    L.push('3. 【问题清单】逐条列出问题（命名/重复/边界/异常处理/可读性等），每条一行；');
    L.push('4. 【最佳实践代码】放在一个 ``` 围栏代码块中并在第一行标注语言（如 ```python），为完整改进版——保留原意，改掉问题；');
    L.push('5. 不要输出多个代码块，不要重复代码文本，不要输出与评审无关的内容。');
    return L.join('\n');
  }

  /* [P1 阶段3] 多轮迭代诊断 Prompt：上一轮代码 + 新报错信息 → 修正修复代码 */
  function buildRoundPrompt(rounds, errorInfo) {
    var first = rounds[0];
    var last = rounds[rounds.length - 1];
    var L = [];
    L.push('你是专业代码故障诊断专家。此前给出的修复代码执行后仍然报错，请基于新的报错信息修正修复代码。');
    L.push('【原始报错（第1轮）】');
    L.push(String(first.ocrText || '').slice(0, 1500));
    L.push('【上一轮修复代码（第' + last.n + '轮）】');
    L.push('```' + (last.result.lang || 'text') + '\n' + String(last.result.code || '').slice(0, 3000) + '\n```');
    L.push('【上一轮执行结果/新报错信息】');
    L.push(String(errorInfo).slice(0, 2000));
    L.push('输出要求（严格遵守）：');
    L.push('1. 严格按以下分段输出：【错误根因】（说明上一轮代码为什么没有修好）、【修复方案】、【修复代码】；');
    L.push('2. 修复代码放在一个 ``` 围栏代码块中并在第一行标注语言，必须完整可运行（不要只给片段或 diff）；');
    L.push('3. 不要输出多个代码块，不要输出与修复无关的内容。');
    return L.join('\n');
  }

  /* ---------------- 模型覆盖（默认空 = 网关兜底模型，llm_proxy.py:199-213） ---------------- */
  function modelField() {
    var m = (el.modelOverride.value || '').trim();
    return m ? { model: m } : {};
  }

  /* ---------------- 流程步骤实现 ---------------- */

  /* [P2 阶段2] 分段 OCR 核心（主流程与批量共用）：
     逐段增强 → 编码 → /api/llm/chat（每段失败重试 1 次）→ mergeSegmentTexts 合并。
     segs: buildSegments() 产物；onSeg(n, total, segY)：进度回调。 */
  function segmentedOcrCore(segs, mode, onSeg) {
    var idx = 0;
    var texts = [];
    function once(dataUrl) {
      var body = Object.assign({
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: OCR_PROMPT },
            { type: 'image_url', image_url: { url: dataUrl } } // OpenAI 视觉分片，30_chat.js:1417 同构
          ]
        }],
        temperature: 0
      }, modelField());
      return apiChat(body).then(function (j) { return String(j.content || '').trim(); });
    }
    function next() {
      if (idx >= segs.length) return Promise.resolve(mergeSegmentTexts(texts));
      var n = idx + 1;
      var seg = segs[idx];
      if (onSeg) onSeg(n, segs.length, seg.y, seg.h);
      var work = prepareSegment(seg.canvas, mode);
      if (work.width > 1600) work = scaleCanvas(work, 1600 / work.width); // 载荷限幅（与既有出口同量级）
      var dataUrl = encodeCanvas(work, false);
      return once(dataUrl).catch(function (e) {
        if (e instanceof BudgetError) throw e;
        return once(dataUrl); // 每段重试 1 次（与既有单图重试语义一致）
      }).then(function (text) {
        texts.push(text);
        idx++;
        return next();
      });
    }
    return next();
  }

  /** 步骤1a：[P2] 长图分段识别（高度 >1600px 自动触发；日志逐段输出进度） */
  function stepOcrSegmented() {
    var mode = el.enhanceMode.value || 'auto';
    var segs = buildSegments(state.rawCanvas);
    state.segments = segs.map(function (s) { return { y: s.y, h: s.h }; });
    setStage('ocr');
    log('长截图 ' + state.rawCanvas.width + '×' + state.rawCanvas.height + '（高度 >' + LONG_IMG_THRESHOLD +
      'px），自动分段识别：' + segs.length + ' 段（段高 ' + SEGMENT_HEIGHT + 'px / 重叠 ' + SEGMENT_OVERLAP + 'px）', 'info');
    return segmentedOcrCore(segs, mode, function (n, total, y, h) {
      log('OCR 分段 ' + n + '/' + total + '（y=' + y + '..' + (y + h) + '）...', 'running');
    }).then(function (merged) {
      finishRunningLog('ok', '分段识别完成');
      state.ocrText = merged.text;
      if (!state.ocrText) throw new Error('OCR 返回为空');
      log('分段文本合并完成：' + merged.lines + ' 行 / ' + state.ocrText.length + ' 字符' +
        '（去重 ' + merged.dropped + ' 行重叠，修正 ' + merged.fixedBreaks + ' 处断行）', 'ok');
      return state.ocrText;
    });
  }

  /** 步骤1：OCR 识别（多模态 messages 走 /api/llm/chat；失败自动重试 1 次）
     [P2] 长截图（解码高度 >1600px 且未手动裁剪）自动走分段识别 stepOcrSegmented，
     其余路径逐字保留；手动裁剪时尊重裁剪范围（dataUrl 已是裁剪区域） */
  function stepOcr() {
    if (state.isLongImage && !state.cropRect && state.rawCanvas && state.rawCanvas.height > LONG_IMG_THRESHOLD) {
      return stepOcrSegmented();
    }
    var body = Object.assign({
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: OCR_PROMPT },
          { type: 'image_url', image_url: { url: state.dataUrl } } // OpenAI 视觉分片，30_chat.js:1417 同构
        ]
      }],
      temperature: 0
    }, modelField());
    setStage('ocr');
    log('OCR识别中...（POST /api/llm/chat · 多模态）', 'running');
    return apiChat(body).then(function (j) {
      finishRunningLog('ok', '识别完成');
      state.ocrText = String(j.content || '').trim();
      if (!state.ocrText) throw new Error('OCR 返回为空');
      log('OCR 文本 ' + state.ocrText.length + ' 字符（模型 ' + (j.model || '默认') + '）', 'info');
      return state.ocrText;
    }, function (e) {
      if (e instanceof BudgetError) { finishRunningLog('fail', '预算熔断'); throw e; }
      log('OCR 首次调用失败（' + e.message + '），自动重试 1 次…', 'info');
      return apiChat(body).then(function (j) {
        finishRunningLog('ok', '重试后识别完成');
        state.ocrText = String(j.content || '').trim();
        if (!state.ocrText) throw new Error('OCR 返回为空');
        log('OCR 文本 ' + state.ocrText.length + ' 字符（重试成功）', 'info');
        return state.ocrText;
      }, function (e2) {
        finishRunningLog('fail', '重试仍失败');
        if (e2 instanceof BudgetError) throw e2;
        throw new Error('OCR 识别失败：' + e2.message);
      });
    });
  }

  /** 步骤2：故障诊断（[P1] 结构化特征 Prompt；无特征回退原固化 Prompt） */
  function stepDiagnose() {
    var features = extractFeatures(state.ocrText);
    var prompt = buildDiagPrompt(state.ocrText, features);
    if (prompt !== DIAG_PROMPT.replace('{{OCR_RESULT}}', state.ocrText)) {
      log('诊断特征：' + summarizeFeatures(features), 'info');
    }
    var body = Object.assign({
      messages: [{ role: 'user', content: prompt }],
      temperature: 0.2
    }, modelField());
    setStage('diag');
    log('故障诊断中...（' + (prompt.indexOf('【结构化特征】') !== -1 ? '结构化特征 prompt' : '固化诊断 prompt') + '）', 'running');
    return apiChat(body).then(function (j) {
      finishRunningLog('ok', '诊断完成');
      var text = String(j.content || '');
      if (!text.trim()) throw new Error('诊断返回为空');
      var r = parseResult(text);
      pushRound(1, '报错截图', r);
      return text;
    }, function (e) {
      finishRunningLog('fail', e instanceof BudgetError ? '预算熔断' : '失败');
      if (e instanceof BudgetError) throw e;
      throw new Error('故障诊断失败：' + e.message);
    });
  }

  function summarizeFeatures(f) {
    var parts = [];
    if (f.type) parts.push('类型 ' + f.type);
    if (f.lines.length) parts.push('行号 ' + f.lines.join(','));
    if (f.paths.length) parts.push('文件 ' + f.paths[0]);
    if (f.codes.length) parts.push('错误码 ' + f.codes[0]);
    if (f.lang && f.lang !== 'plaintext') parts.push('语言 ' + (LANG_LABEL[f.lang] || f.lang));
    return parts.length ? parts.join(' · ') : '未命中（回退通用模式）';
  }

  /** 步骤3：结果解析（拆分【错误根因】/【修复方案】/【修复代码】，阶段3.2.4；[P1] 改为纯函数返回） */
  function parseResult(text) {
    function section(name, nextName) {
      var re = new RegExp('【' + name + '】\\s*([\\s\\S]*?)(?=(【' + nextName + '】)|$)');
      var m = text.match(re);
      return m ? m[1].trim() : '';
    }
    /* [v2] 评审分支：模型输出含【总体评价】新分段（代码评审场景）→ 按评审契约解析。
       字段对齐诊断产物：root=总体评价 / plan=问题清单 / code=最佳实践代码，
       kind:'review' 供日志区分；诊断路径（三段标记）不受影响——parseResult 全部
       调用点零改动（按输出文本自适应，无需调用方传模式）。 */
    if (text.indexOf('【总体评价】') !== -1) {
      var rv = { root: '', plan: '', code: '', lang: '', verifyFailed: null, kind: 'review' };
      rv.root = section('总体评价', '问题清单');
      rv.plan = section('问题清单', '最佳实践代码');
      var codeRawRv = section('最佳实践代码', '$');
      // 代码块提取与诊断分支同款：优先取 ```lang 围栏，记录语言；无围栏则原文作为代码
      var fenceRv = codeRawRv.match(/```([A-Za-z0-9_+\-]*)\s*\n([\s\S]*?)```/);
      if (fenceRv) {
        rv.lang = (fenceRv[1] || detectLang(codeRawRv)).toLowerCase();
        rv.code = fenceRv[2].replace(/\s+$/, '');
      } else {
        rv.lang = detectLang(codeRawRv);
        rv.code = codeRawRv;
      }
      if (!rv.root && !rv.plan && !rv.code) {
        // 模型未按格式输出：整体落入总体评价区并提示（不白屏，同阶段3.3 语义）
        rv.root = text.trim();
        log('模型输出未含标准分段标记，已按原文展示', 'info');
      }
      return rv;
    }
    var r = { root: '', plan: '', code: '', lang: '', verifyFailed: null };
    r.root = section('错误根因', '修复方案');
    r.plan = section('修复方案', '修复代码');
    var codeRaw = section('修复代码', '$');
    // 代码块：优先取 ```lang 围栏，记录语言；无围栏则原文作为代码
    var fence = codeRaw.match(/```([A-Za-z0-9_+\-]*)\s*\n([\s\S]*?)```/);
    if (fence) {
      r.lang = (fence[1] || detectLang(codeRaw)).toLowerCase();
      r.code = fence[2].replace(/\s+$/, '');
    } else {
      r.lang = detectLang(codeRaw);
      r.code = codeRaw;
    }
    if (!r.root && !r.plan && !r.code) {
      // 模型未按格式输出：整体落入根因区并提示（不白屏，阶段3.3）
      r.root = text.trim();
      log('模型输出未含标准分段标记，已按原文展示', 'info');
    }
    return r;
  }

  /* ============================================================
     [P2 阶段3] 修复前后 Diff 对比（纯前端 LCS 行级 diff，零后端）
       extractOriginalCode —— 左侧原始代码来源（多轮=上一轮代码；单轮=OCR 提取）
       diffLines           —— LCS DP 行级对比 → eq/mod/del/ins 行序列
       renderDiffView      —— 左右分栏渲染（行号对齐 / 同步滚动 / 仅看改动行）
     交互：结果区「修复对比」Tab（默认「修复结果」）；切换仅 display 切换，
     两容器始终全量渲染（对既有轮次渲染逻辑零侵入）。
     ============================================================ */

  /* 单行是否像代码（OCR 原始代码提取用）：排除错误/警告行，要求含代码特征 */
  function isCodeLine(line) {
    if (classifyLine(line)) return false;
    if (!String(line).trim()) return false;
    if (/[{};=]|\([^\)]*\)|:\s*$/ .test(line)) return true;
    if (/^\s*(def |import |from |return |for |if |while |elif |else|print|try|except|catch|function |const |let |var |class |public |private )/.test(line)) return true;
    return false;
  }

  /* 左侧原始代码来源：
     1) OCR 文本含 ``` 代码围栏 → 直接使用
     2) 致命错误行上方的连续代码样行（≥3 行）→ 尽力提取
     返回 { code, src } 或 null */
  function extractOriginalCode(text) {
    var s = String(text || '');
    var fence = s.match(/```([A-Za-z0-9_+\-]*)\s*\n([\s\S]*?)```/);
    if (fence && fence[2].trim().split('\n').length >= 2) {
      return { code: fence[2].replace(/\s+$/, ''), src: 'OCR 文本中的代码块' };
    }
    var lines = s.split('\n');
    var ce = classifyError(s);
    var end = ce.firstIdx >= 0 ? ce.firstIdx : lines.length;
    var codeLines = [];
    for (var i = end - 1; i >= 0; i--) {
      var L = lines[i];
      if (!L.trim()) { if (codeLines.length) break; else continue; }
      if (isCodeLine(L)) codeLines.unshift(L);
      else break;
    }
    if (codeLines.length >= 3) {
      return { code: codeLines.join('\n'), src: '截图错误行上下文（尽力提取）' };
    }
    return null;
  }

  /* LCS 行级 diff：返回行序列 [{type:'eq'|'mod'|'del'|'ins', l:{no,text}|null, r:{no,text}|null}]
     单侧 >DIFF_MAX_LINES 行时限幅并标记 truncated（超大输入性能保护） */
  function diffLines(aText, bText) {
    var A = String(aText || '').replace(/\r\n/g, '\n').split('\n');
    var B = String(bText || '').replace(/\r\n/g, '\n').split('\n');
    while (A.length && !A[A.length - 1].trim()) A.pop();
    while (B.length && !B[B.length - 1].trim()) B.pop();
    var truncated = false;
    if (A.length > DIFF_MAX_LINES || B.length > DIFF_MAX_LINES) {
      truncated = true;
      A = A.slice(0, DIFF_MAX_LINES); B = B.slice(0, DIFF_MAX_LINES);
    }
    var n = A.length, m = B.length;
    // LCS DP（(n+1)*(m+1) 矩阵，≤400×400）
    var w = m + 1;
    var dp = new Uint32Array((n + 1) * w);
    var i, j;
    for (i = n - 1; i >= 0; i--) {
      for (j = m - 1; j >= 0; j--) {
        dp[i * w + j] = (A[i] === B[j])
          ? dp[(i + 1) * w + j + 1] + 1
          : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
      }
    }
    // 回溯出操作序列
    var ops = [];
    i = 0; j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { ops.push({ t: 'eq', ai: i, bi: j }); i++; j++; }
      else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) { ops.push({ t: 'del', ai: i }); i++; }
      else { ops.push({ t: 'ins', bi: j }); j++; }
    }
    while (i < n) { ops.push({ t: 'del', ai: i }); i++; }
    while (j < m) { ops.push({ t: 'ins', bi: j }); j++; }
    // 相邻 del 块 + ins 块按位配对为 mod（修改行，任务书 3.1 橙色）
    var rows = [];
    var k = 0;
    while (k < ops.length) {
      if (ops[k].t === 'del') {
        var dels = [];
        while (k < ops.length && ops[k].t === 'del') { dels.push(ops[k]); k++; }
        var inss = [];
        while (k < ops.length && ops[k].t === 'ins') { inss.push(ops[k]); k++; }
        var pair = Math.min(dels.length, inss.length);
        for (var p = 0; p < pair; p++) {
          rows.push({ type: 'mod', l: { no: dels[p].ai + 1, text: A[dels[p].ai] }, r: { no: inss[p].bi + 1, text: B[inss[p].bi] } });
        }
        for (var q = pair; q < dels.length; q++) rows.push({ type: 'del', l: { no: dels[q].ai + 1, text: A[dels[q].ai] }, r: null });
        for (var u = pair; u < inss.length; u++) rows.push({ type: 'ins', l: null, r: { no: inss[u].bi + 1, text: B[inss[u].bi] } });
      } else if (ops[k].t === 'ins') {
        rows.push({ type: 'ins', l: null, r: { no: ops[k].bi + 1, text: B[ops[k].bi] } }); k++;
      } else {
        rows.push({ type: 'eq', l: { no: ops[k].ai + 1, text: A[ops[k].ai] }, r: { no: ops[k].bi + 1, text: B[ops[k].bi] } }); k++;
      }
    }
    return { rows: rows, truncated: truncated, aCount: A.length, bCount: B.length };
  }

  /* 单行语法高亮：hljs 11.9 highlight() 输出自带 HTML 转义（源文本不可注入），
     失败/不可用/纯文本时回退 textContent */
  function highlightLineHtml(line, lang) {
    try {
      if (window.hljs && lang && lang !== 'plaintext') {
        return window.hljs.highlight(String(line), { language: lang, ignoreIllegals: true }).value;
      }
    } catch (e) { /* 回退纯文本 */ }
    return null;
  }

  /* Diff 计数摘要（图例用） */
  function diffCounts(rows) {
    var c = { add: 0, del: 0, mod: 0, eq: 0 };
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].type === 'ins') c.add++;
      else if (rows[i].type === 'del') c.del++;
      else if (rows[i].type === 'mod') c.mod++;
      else c.eq++;
    }
    return c;
  }

  /* Diff 视图渲染（进入 #diff-wrap）：左右分栏、行号对齐、单滚动容器同步、仅看改动行 */
  function renderDiffView() {
    el.diffWrap.innerHTML = '';
    el.diffWrap.className = 'diff-wrap';
    var cur = currentRound();
    var hasResult = !!(cur && (cur.result.root || cur.result.plan || cur.result.code));
    if (!hasResult) { el.diffWrap.hidden = true; return; }

    // 左侧原始代码：多轮取上一轮修复代码；单轮从 OCR 尽力提取
    var left = null, leftLabel = '', rightLabel = '修复后代码';
    if (!state.viewing && state.rounds.length > 1 && state.roundIdx > 1) {
      var prev = state.rounds[state.roundIdx - 2];
      if (prev && prev.result && prev.result.code) {
        left = prev.result.code;
        leftLabel = '原始代码（第' + prev.n + '轮）';
      }
    }
    if (left === null && cur.ocrText) {
      var ex = extractOriginalCode(cur.ocrText);
      if (ex) { left = ex.code; leftLabel = '原始代码（' + ex.src + '）'; }
    }

    var head = document.createElement('div');
    head.className = 'diff-head';
    var title = document.createElement('span'); title.className = 'diff-title'; title.textContent = '修复对比';
    var srcInfo = document.createElement('span'); srcInfo.className = 'diff-src';
    srcInfo.textContent = left
      ? (leftLabel + ' → ' + rightLabel + (state.viewing ? '（历史回看态：仅截图代码对比）' : ''))
      : '暂无可对比的原始代码（多轮迭代后自动对比上一轮修复代码）';
    head.appendChild(title); head.appendChild(srcInfo);
    var copyBtn = document.createElement('button');
    copyBtn.type = 'button'; copyBtn.className = 'ghost'; copyBtn.textContent = '复制修复后代码';
    copyBtn.addEventListener('click', function () {
      if (cur && cur.result.code) copyText(cur.result.code, '修复代码已复制到剪贴板');
    });
    head.appendChild(copyBtn);
    el.diffWrap.appendChild(head);

    if (!left) {
      var hint = document.createElement('p');
      hint.className = 'diff-hint';
      hint.textContent = '提示：执行失败后点击「继续修复」进入第 2 轮，即可自动对比上一轮与最新修复代码；或截图 OCR 中含完整代码块时自动提取对比。';
      el.diffWrap.appendChild(hint);
      return;
    }

    var lang = cur.result.lang || detectLang(cur.result.code || '');
    var d = diffLines(left, cur.result.code);
    var cnt = diffCounts(d.rows);

    var cols = document.createElement('div');
    cols.className = 'diff-cols';
    if (el.diffOnly.checked) cols.classList.add('only-changes');

    function buildCol(headText, side) {
      var col = document.createElement('div');
      col.className = 'diff-col';
      var ch = document.createElement('div');
      ch.className = 'diff-col-head';
      ch.textContent = headText;
      col.appendChild(ch);
      var linesBox = document.createElement('div');
      linesBox.className = 'diff-lines';
      for (var i = 0; i < d.rows.length; i++) {
        var row = d.rows[i];
        var cell = row[side];
        var dl = document.createElement('div');
        dl.className = 'dl dl-' + (row.type === 'eq' ? 'eq' : row.type);
        if (cell) {
          var no = document.createElement('span');
          no.className = 'dl-no';
          no.textContent = cell.no;
          var code = document.createElement('span');
          code.className = 'dl-code';
          var html = highlightLineHtml(cell.text, lang);
          if (html !== null) code.innerHTML = html; // hljs 转义输出，零注入面
          else code.textContent = cell.text;
          dl.appendChild(no); dl.appendChild(code);
        } else {
          var padNo = document.createElement('span');
          padNo.className = 'dl-no';
          var pad = document.createElement('span');
          pad.className = 'dl-pad';
          dl.appendChild(padNo); dl.appendChild(pad);
        }
        linesBox.appendChild(dl);
      }
      col.appendChild(linesBox);
      return col;
    }
    cols.appendChild(buildCol(leftLabel || '原始代码', 'l'));
    cols.appendChild(buildCol(rightLabel, 'r'));
    el.diffWrap.appendChild(cols);

    var legend = document.createElement('div');
    legend.className = 'diff-legend';
    function lg(cls, txt, n) {
      var s = document.createElement('span');
      var i = document.createElement('i'); i.className = cls;
      s.appendChild(i);
      s.appendChild(document.createTextNode(txt + ' ' + n));
      return s;
    }
    legend.appendChild(lg('lg-add', '新增', cnt.add));
    legend.appendChild(lg('lg-del', '删除', cnt.del));
    legend.appendChild(lg('lg-mod', '修改', cnt.mod));
    if (d.truncated) {
      var tr = document.createElement('span');
      tr.textContent = '（超长代码已截断至 ' + DIFF_MAX_LINES + ' 行/侧）';
      legend.appendChild(tr);
    }
    el.diffWrap.appendChild(legend);
  }

  /* Tab 切换（display 切换；渲染始终全量） */
  function setDiffTab(tab) {
    state.diffTab = (tab === 'diff') ? 'diff' : 'result';
    applyDiffTabVisibility();
  }
  function applyDiffTabVisibility() {
    var has = !!(state.viewing || state.rounds.length);
    el.viewTabs.hidden = !has;
    el.tabResult.classList.toggle('is-active', state.diffTab !== 'diff');
    el.tabDiff.classList.toggle('is-active', state.diffTab === 'diff');
    el.tabResult.setAttribute('aria-selected', state.diffTab !== 'diff' ? 'true' : 'false');
    el.tabDiff.setAttribute('aria-selected', state.diffTab === 'diff' ? 'true' : 'false');
    el.diffOnlyLabel.hidden = state.diffTab !== 'diff';
    var showDiff = has && state.diffTab === 'diff';
    el.diffWrap.hidden = !showDiff;
    el.roundsWrap.hidden = !has || showDiff; // [P2 修复] 结果容器与 Diff 容器互斥显隐（Tab 点击路径也生效）
  }
  el.tabResult.addEventListener('click', function () { setDiffTab('result'); });
  el.tabDiff.addEventListener('click', function () { setDiffTab('diff'); renderDiffView(); });
  el.diffOnly.addEventListener('change', function () {
    var cols = el.diffWrap.querySelector('.diff-cols');
    if (cols) cols.classList.toggle('only-changes', el.diffOnly.checked);
  });

  /* ---------------- [P1 阶段3] 多轮状态与渲染 ---------------- */

  function pushRound(n, src, result) {
    var rec = {
      n: n, ts: new Date().toTimeString().slice(0, 8), src: src,
      ocrText: n === 1 ? state.ocrText : (state.rounds[0] ? state.rounds[0].ocrText : ''),
      errorInfo: n > 1 ? state._lastErrorInfo || '' : '',
      result: result, run: null, outcome: 'pending', verifyFailed: null
    };
    state.rounds.push(rec);
    state.roundIdx = state.rounds.length;
    state.result = rec.result; // 兼容既有引用（copy/staticVerify 等）
    renderResult();
    if (n === 1) saveHistoryRecord(rec);
    return rec;
  }

  function outcomeBadge(outcome) {
    if (outcome === 'ok') return { text: '执行通过', cls: 'is-ok' };
    if (outcome === 'fail') return { text: '执行失败', cls: 'is-fail' };
    if (outcome === 'static-ok') return { text: '静态校验通过', cls: 'is-ok' };
    if (outcome === 'static-fail') return { text: '静态校验失败', cls: 'is-fail' };
    return { text: '待执行验证', cls: '' };
  }

  /* [P2 2.2] 错误定位块：致命错误块优先展示（跳过前导 Warning/Info）+ 完整日志折叠
     展示文本先剥离思考块（模型泄漏的 <think> 推理不属报错日志） */
  function buildErrorLocateBlock(gradeSrcRaw) {
    var gradeSrc = stripThinkBlocks(gradeSrcRaw);
    var ce = classifyError(gradeSrc);
    var art = document.createElement('article');
    art.className = 'result-block err-locate';
    var h = document.createElement('h3');
    h.textContent = '【错误定位】';
    var meta = document.createElement('span'); meta.className = 'mono lang-tag';
    meta.textContent = ce.totalLines + ' 行日志 · 致命 ' + ce.fatalCount + ' / 警告 ' + ce.warnCount + ' / 提示 ' + ce.infoCount;
    h.appendChild(meta);
    art.appendChild(h);

    if (ce.firstIdx >= 0 && ce.blockLines.length) {
      var pre = document.createElement('pre');
      pre.className = 'err-block';
      var code = document.createElement('code');
      code.textContent = ce.blockLines.join('\n'); // textContent：LLM/OCR 内容零注入
      pre.appendChild(code);
      art.appendChild(pre);
    } else if (String(gradeSrc).trim()) {
      var p = document.createElement('p');
      p.textContent = '未检出致命错误行（未匹配 Error/Exception/Fatal 等特征）。';
      art.appendChild(p);
    }

    // 长日志自动折叠（行数超阈值才折叠，短日志直接平铺展示）
    var lines = String(gradeSrc || '').split('\n');
    if (ce.totalLines > OCR_COLLAPSE_LINES) {
      var det = document.createElement('details');
      det.className = 'ocr-fold';
      var sum = document.createElement('summary');
      sum.textContent = '完整日志（' + ce.totalLines + ' 行，点击展开/收起）';
      var pre2 = document.createElement('pre');
      var code2 = document.createElement('code');
      code2.textContent = gradeSrc;
      pre2.appendChild(code2);
      det.appendChild(sum);
      det.appendChild(pre2);
      art.appendChild(det);
    } else if (String(gradeSrc).trim()) {
      var pre3 = document.createElement('pre');
      pre3.className = 'err-block';
      var code3 = document.createElement('code');
      code3.textContent = gradeSrc;
      pre3.appendChild(code3);
      art.appendChild(pre3);
    }
    return art;
  }

  /** [P1] 轮次分区渲染（每轮独立 result-block；textContent 零注入）
     [P2] 追加：轮次头分级徽章（fatal/warn/info）+「错误定位」块（优先致命块+完整日志折叠） */
  function buildRoundGroup(r, isCurrent) {
    var g = document.createElement('section');
    g.className = 'round-group' + (isCurrent ? ' is-current' : '');
    g.setAttribute('data-round', r.n);

    // [P2 4.3] 分级来源：第1轮=OCR 全文；后续轮=上一轮执行报错（无则沿用 OCR）；先剥离思考块
    var gradeSrc = stripThinkBlocks((r.n > 1 && r.errorInfo) ? r.errorInfo : (r.ocrText || ''));

    var head = document.createElement('div');
    head.className = 'round-head';
    var tt = document.createElement('span'); tt.textContent = '第' + r.n + '轮';
    var ts = document.createElement('span'); ts.className = 'rt-time'; ts.textContent = r.ts || '';
    var src = document.createElement('span'); src.className = 'round-src';
    src.textContent = r.src === '报错截图' ? '来源：报错截图' : (r.src ? '来源：' + r.src : '');
    head.appendChild(tt); head.appendChild(ts); head.appendChild(src);
    if (gradeSrc && String(gradeSrc).trim()) {
      var ce = classifyError(gradeSrc);
      var lv = document.createElement('span');
      var lm = LEVEL_META[ce.level] || LEVEL_META.info;
      lv.className = 'lv-badge ' + lm.cls;
      lv.title = '错误等级：' + lm.text + '（纯前端特征识别）';
      lv.textContent = lm.text;
      head.appendChild(lv);
    }
    var badge = document.createElement('span');
    var ob = outcomeBadge(r.outcome);
    badge.className = 'rt-badge ' + ob.cls;
    badge.textContent = ob.text;
    head.appendChild(badge);
    g.appendChild(head);

    // [P2 2.2] 错误定位块：优先于根因展示（有日志内容时渲染）
    if (gradeSrc && String(gradeSrc).trim()) {
      g.appendChild(buildErrorLocateBlock(gradeSrc));
    }

    var res = r.result;
    if (res.root) {
      var b1 = document.createElement('article'); b1.className = 'result-block';
      var h1 = document.createElement('h3'); h1.textContent = '【错误根因】';
      var p1 = document.createElement('p'); p1.textContent = res.root;
      b1.appendChild(h1); b1.appendChild(p1); g.appendChild(b1);
    }
    if (res.plan) {
      var b2 = document.createElement('article'); b2.className = 'result-block';
      var h2 = document.createElement('h3'); h2.textContent = '【修复方案】';
      var p2 = document.createElement('p'); p2.textContent = res.plan;
      b2.appendChild(h2); b2.appendChild(p2); g.appendChild(b2);
    }
    if (res.code) {
      var b3 = document.createElement('article'); b3.className = 'result-block';
      var h3 = document.createElement('h3');
      h3.textContent = '【修复代码】';
      var tag = document.createElement('span'); tag.className = 'mono lang-tag'; tag.textContent = res.lang || '';
      h3.appendChild(tag);
      var pre3 = document.createElement('pre'); pre3.className = 'code-wrap';
      var code3 = document.createElement('code');
      code3.className = 'hljs language-' + (res.lang || 'plaintext');
      code3.textContent = res.code;
      try { if (window.hljs) window.hljs.highlightElement(code3); } catch (e) { /* 高亮失败不阻断 */ }
      pre3.appendChild(code3);
      b3.appendChild(h3); b3.appendChild(pre3); g.appendChild(b3);
    }
    if (r.run) {
      g.appendChild(buildRunBlock(r));
    }
    return g;
  }

  /** [P0→P1] 执行结果分栏（stdout / stderr / 退出码 / 耗时），改为按轮构建 */
  function buildRunBlock(r) {
    var d = r.run;
    var art = document.createElement('article'); art.className = 'result-block';
    var h = document.createElement('h3');
    h.textContent = '【执行结果】';
    var meta = document.createElement('span'); meta.className = 'mono lang-tag';
    if (d.mode === 'static') {
      meta.textContent = '静态校验（降级通道）';
    } else {
      meta.textContent = 'exit ' + (d.exit_code === null || d.exit_code === undefined ? '—' : d.exit_code) +
        ' · ' + (d.duration_ms || 0) + 'ms';
    }
    h.appendChild(meta);
    art.appendChild(h);
    if (d.mode !== 'static') {
      var grid = document.createElement('div'); grid.className = 'run-grid';
      function cell(labelTxt, val, isErr) {
        var cellEl = document.createElement('div'); cellEl.className = 'run-cell';
        var lb = document.createElement('span'); lb.className = 'run-label' + (isErr ? ' run-label--err' : '');
        lb.textContent = labelTxt;
        var pre = document.createElement('pre'); pre.className = 'code-wrap';
        var code = document.createElement('code'); code.textContent = val;
        pre.appendChild(code);
        cellEl.appendChild(lb); cellEl.appendChild(pre);
        return cellEl;
      }
      grid.appendChild(cell('stdout', d.stdout || '（无输出）', false));
      grid.appendChild(cell('stderr', d.stderr || '（无错误输出）', true));
      art.appendChild(grid);
      var sum = document.createElement('p'); sum.className = 'run-summary mono';
      sum.textContent = '任务 ' + (d.task_id || '—') + ' · 工作目录 ' + (d.cwd || '—');
      art.appendChild(sum);
    } else {
      var p = document.createElement('p');
      p.textContent = d.ok ? '静态语法校验通过（降级通道，未真实执行）' : ('静态校验失败：' + (d.reason || '未给出原因'));
      art.appendChild(p);
    }
    return art;
  }

  function renderRoundTabs() {
    el.roundTabs.innerHTML = '';
    if (state.viewing || !state.rounds.length) { el.roundBar.hidden = true; return; }
    el.roundBar.hidden = false;
    state.rounds.forEach(function (r, i) {
      var n = i + 1;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'round-tab' + (n === state.roundIdx ? ' is-active' : '');
      b.setAttribute('role', 'tab');
      b.title = '查看第' + n + '轮结果';
      var txt = '第' + n + '轮';
      var st = document.createElement('span'); st.className = 'rt-state';
      if (r.outcome === 'ok' || r.outcome === 'static-ok') st.textContent = '✓';
      else if (r.outcome === 'fail' || r.outcome === 'static-fail') st.textContent = '✕';
      else st.textContent = '…';
      b.appendChild(document.createTextNode(txt));
      b.appendChild(st);
      b.addEventListener('click', function () {
        state.roundIdx = n;
        syncCurrentMirror();
        renderResult();
        log('已切换到第' + n + '轮结果', 'info');
      });
      el.roundTabs.appendChild(b);
    });
  }

  function syncCurrentMirror() {
    var cur = currentRound();
    state.result = cur ? cur.result : { root: '', plan: '', code: '', lang: '' };
  }

  /** [P1] 右栏渲染入口（轮次分区；历史回看态渲染单条只读记录）
     [P2] 追加：Diff 视图同步渲染 + Tab 可见性（切换仅 display，两容器全量渲染） */
  function renderResult() {
    var rounds = state.viewing ? [state.viewing] : state.rounds;
    var has = !!(state.viewing || rounds.length);
    el.placeholder.hidden = has;
    el.actions.hidden = !has;
    el.roundBar.hidden = !has || !!state.viewing; // 无轮次或回看态：轮次条隐藏
    el.roundsWrap.innerHTML = '';
    if (!has) {
      el.roundsWrap.hidden = true;
      el.diffWrap.hidden = true;
      el.viewTabs.hidden = true;
      el.diffOnlyLabel.hidden = true;
      return;
    }
    if (state.viewing) {
      el.roundsWrap.appendChild(buildRoundGroup(state.viewing, true));
    } else {
      state.rounds.forEach(function (r, i) {
        el.roundsWrap.appendChild(buildRoundGroup(r, (i + 1) === state.roundIdx));
      });
      renderRoundTabs();
    }
    // [P2] Tab 可见性与两容器显示切换（修复结果 / 修复对比；互斥显隐统一在 applyDiffTabVisibility）
    applyDiffTabVisibility();
    renderDiffView();
    updateActions();
  }

  /** [P1] 动作区可见性：复制类恒显；继续修复仅在「最新轮执行失败且未达上限」时出现 */
  function updateActions() {
    var cur = currentRound();
    var hasResult = !!(cur && (cur.result.root || cur.result.plan || cur.result.code));
    el.actions.hidden = !hasResult;
    el.exportMd.hidden = !hasResult; // [P2 4.2] 导出按钮随结果显隐
    var viewing = !!state.viewing;
    var isLatest = !viewing && cur && state.roundIdx === state.rounds.length;
    var canContinue = !viewing && isLatest && cur.outcome === 'fail' && state.rounds.length < MAX_ROUNDS && !state.running;
    el.continueFix.hidden = !canContinue;
    el.continueFix.disabled = state.rounds.length >= MAX_ROUNDS;
    // 历史回看态：只读（执行/继续修复隐藏），复制类保留
    el.syncZcode.hidden = viewing;
    var hasRun = !!(cur && cur.run && cur.run.mode !== 'static');
    el.copyRun.hidden = !hasRun;
  }

  /* [P1] 清空重开：轮次/结果/日志计数全部复位（截图保留可选场景：上传新图即重开） */
  function restartRounds() {
    state.rounds = [];
    state.roundIdx = 0;
    state.viewing = null;
    state.ocrText = '';
    state.result = { root: '', plan: '', code: '', lang: '' };
    state.historyId = null;
    state.diffTab = 'result'; // [P2] 复位到修复结果视图
    renderResult();
    resetStages();
    log('已清空所有轮次记录，可重新上传或重试分析', 'info');
    toast('已清空重开');
  }
  el.roundRestart.addEventListener('click', restartRounds);

  /** [P1 阶段3.2] 继续修复：上一轮执行报错 → 新一轮诊断（自动传入，无需重传截图） */
  function continueFix() {
    if (state.running || state.runBusy) { toast('流程进行中，请稍候'); return; }
    var rounds = state.rounds;
    if (!rounds.length) { toast('暂无可继续的轮次'); return; }
    if (rounds.length >= MAX_ROUNDS) {
      toast('已达 ' + MAX_ROUNDS + ' 轮迭代上限，请手动介入排查');
      log('多轮迭代已达上限（' + MAX_ROUNDS + ' 轮），建议人工检查报错现场后重新上传截图', 'fail');
      return;
    }
    var last = rounds[rounds.length - 1];
    var errInfo = '';
    if (last.run) {
      if (last.run.mode === 'zcode') {
        errInfo = last.run.stderr || last.run.stdout || '';
        if (last.run.error === 'timeout') errInfo = '执行超时（>15s 强制终止）';
      } else if (last.run.mode === 'static') {
        errInfo = last.run.reason || '';
      }
    }
    if (!errInfo) { toast('上一轮没有可用的报错信息'); return; }
    state._lastErrorInfo = errInfo;

    state.running = true;
    state.viewing = null;
    el.analyze.disabled = true;
    el.analyzeLabel.textContent = '迭代中…';
    setStage('diag');
    var nextN = rounds.length + 1;
    log('[第' + nextN + '轮] 继续修复：已传入上一轮执行报错（' + errInfo.length + ' 字符）', 'running');
    var body = Object.assign({
      messages: [{ role: 'user', content: buildRoundPrompt(rounds, errInfo) }],
      temperature: 0.2
    }, modelField());
    apiChat(body).then(function (j) {
      var text = String(j.content || '');
      if (!text.trim()) throw new Error('诊断返回为空');
      var r = parseResult(text);
      var rec = pushRound(nextN, '执行报错', r);
      log('[第' + nextN + '轮] 诊断完成，已生成新修复代码，可点击「执行验证（ZCode 沙箱）」', 'ok');
      setStage('verify');
      return rec;
    }, function (e) {
      finishRunningLog('fail', '失败');
      setStage('diag', true);
      if (e instanceof BudgetError) {
        log('预算熔断：' + e.message + '。请稍后重试（额度跨自然日自动清零）', 'fail');
        toast('已达调用次数上限，请稍后重试');
      } else {
        log('[第' + nextN + '轮] 继续修复失败：' + (e && e.message || e), 'fail');
        toast('继续修复失败');
      }
    }).then(function () {
      state.running = false;
      el.analyze.disabled = !state.dataUrl;
      el.analyzeLabel.textContent = '重新分析';
      updateActions();
    });
  }
  el.continueFix.addEventListener('click', continueFix);

  /** 步骤4：ZCode 环节（[P0] 引擎状态展示；真实执行由用户点击「执行验证」触发） */
  function stepZcode() {
    return apiEngines().then(function (engines) {
      var z = null;
      for (var i = 0; i < engines.length; i++) if (engines[i] && engines[i].name === 'zcode') z = engines[i];
      state.zcodeEngine = z;
      renderZcodeChip();
      if (z) {
        log('ZCode 引擎状态：' + (z.status || 'idle') + (z.offline_reason ? '（' + z.offline_reason + '）' : ''), 'info');
      } else {
        log('引擎列表未申报 zcode 引擎', 'info');
      }
      if (!currentRound() || !currentRound().result.code) {
        log('无修复代码，跳过 ZCode 环节', 'info');
        return;
      }
      // [P0] 沙箱执行接口已就绪：点击「执行验证（ZCode 沙箱）」真实运行修复代码
      log('修复代码已就绪：点击「执行验证（ZCode 沙箱）」在沙箱内真实运行（POST /api/engines/zcode/run）', 'info');
    }, function (e) {
      log('引擎状态读取失败（' + e.message + '），跳过 ZCode 环节', 'info');
      state.zcodeEngine = null;
      renderZcodeChip();
    });
  }

  function renderZcodeChip() {
    var z = state.zcodeEngine;
    el.zcodeChip.hidden = !z;
    if (!z) return;
    var off = z.status === 'offline' || z.available === false;
    el.zcodeDot.parentElement.className = 'zcode-chip ' + (off ? 'is-off' : 'is-ok');
    el.zcodeChipText.textContent = 'ZCode ' + (off ? '不可用' : (z.status === 'busy' ? '忙碌' : '可用'));
  }

  /* ---------------- 全流程编排（阶段3.1；任一步失败终止并保留现场） ---------------- */
  function runPipeline() {
    if (state.running) { toast('分析进行中，请稍候'); return; }
    if (batch.running) { toast('批量分析进行中，请稍候'); return; } // [P2] 与批量互斥（预算与模型保护）
    if (!state.dataUrl) { toast('请先上传报错截图'); return; }
    state.running = true;
    state.viewing = null;
    state.rounds = [];      // 新分析 = 第1轮重新开始（多轮续修走「继续修复」入口）
    state.roundIdx = 0;
    state.ocrText = '';
    state.result = { root: '', plan: '', code: '', lang: '' };
    state.historyId = null;
    state.diffTab = 'result'; // [P2] 新分析复位到修复结果视图
    renderResult();
    el.analyze.disabled = true;
    el.analyzeLabel.textContent = '分析中…';
    el.envTag.textContent = '会话 ' + sessionId().slice(5) + ' · 桶内限额 30 次/天';
    log('—— 全流程开始（预处理 → OCR → 诊断 → 解析 → ZCode 验证）——', 'info');
    setStage('ocr');

    stepOcr()
      .then(stepDiagnose)
      .then(stepZcode)
      .then(function () {
        log('—— 全流程结束 ——', 'ok');
      })
      .catch(function (e) {
        if (e instanceof BudgetError) {
          // 阶段3.3：熔断触发 → 引导稍后重试（服务端按自然日自动清零，llm_proxy.py:41-58）
          log('预算熔断：' + e.message + '。请稍后重试（额度跨自然日自动清零），当前日志与结果已保留', 'fail');
          toast('已达调用次数上限，请稍后重试');
        } else {
          log('流程终止：' + (e && e.message || e) + '。当前日志与已生成内容已保留，点击「重试分析」可重试', 'fail');
          toast('流程中断：' + String(e && e.message || e).slice(0, 60));
        }
      })
      .then(function () {
        state.running = false;
        el.analyze.disabled = !state.dataUrl;
        el.analyzeLabel.textContent = state.rounds.length ? '重新分析' : '重试分析';
        updateActions();
      });
  }
  el.analyze.addEventListener('click', runPipeline);

  /* ---------------- 一键复制 / 复制全部（[P1 4.3] 复制成功 ✓ 反馈） ---------------- */
  function copyText(text, okMsg) {
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast('✓ ' + okMsg); }
      catch (e) { toast('复制失败，请手动选择代码'); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast('✓ ' + okMsg); }, fallback);
    } else fallback();
  }
  el.copyCode.addEventListener('click', function () {
    var cur = currentRound();
    if (!cur || !cur.result.code) { toast('暂无可复制的代码'); return; }
    copyText(cur.result.code, '修复代码已复制到剪贴板');
  });
  el.copyAll.addEventListener('click', function () {
    var cur = currentRound();
    if (!cur || (!cur.result.root && !cur.result.plan && !cur.result.code)) { toast('暂无可复制的结果'); return; }
    var r = cur.result;
    copyText('【错误根因】\n' + r.root + '\n\n【修复方案】\n' + r.plan + '\n\n【修复代码】\n' + r.code, '全部结果已复制');
  });

  /* ---------------- 执行验证（[P0] ZCode 沙箱真实执行 + 降级兼容；[P1] 结果按轮存储） ---------------- */

  /** 语言映射：接口仅支持 python/javascript；其余语言直接走降级静态校验 */
  function mapRunLanguage(lang) {
    var l = String(lang || '').toLowerCase();
    if (l === 'python') return 'python';
    if (l === 'javascript' || l === 'js') return 'javascript';
    return '';
  }

  /** 降级通道（P0 任务书 3.3 必须保留）：接口不可用/引擎离线/503 时经网关静态语法校验 */
  function staticVerify() {
    var cur = currentRound();
    setStage('verify');
    log('静态语法校验中...（降级通道 · POST /api/llm/chat）', 'running');
    var body = Object.assign({
      messages: [{
        role: 'user',
        content: VERIFY_PROMPT
          .replace('{{LANG}}', cur.result.lang || 'python')
          .replace('{{CODE}}', cur.result.code)
      }],
      temperature: 0
    }, modelField());
    return apiChat(body).then(function (j) {
      var out = String(j.content || '');
      if (/VERDICT:\s*PASS/i.test(out)) {
        finishRunningLog('ok', '静态校验通过');
        log('静态语法校验通过（降级通道，未真实执行）', 'ok');
        cur.run = { mode: 'static', ok: true, reason: '' };
        cur.outcome = 'static-ok';
        markAllStagesDone();
      } else {
        var reason = (out.split('\n').slice(1).join(' ') || '未给出原因').trim();
        finishRunningLog('fail', '静态校验失败');
        log('静态校验失败：' + reason.slice(0, 200), 'fail');
        cur.run = { mode: 'static', ok: false, reason: reason };
        cur.outcome = 'static-fail';
        cur.result.verifyFailed = reason;
      }
      updateHistoryOutcome(cur);
      renderResult();
    }, function (e) {
      finishRunningLog('fail', '静态校验调用失败');
      if (e instanceof BudgetError) throw e;
      log('静态校验异常（不阻断结果展示）：' + e.message, 'info');
    });
  }

  el.syncZcode.addEventListener('click', function () {
    var cur = currentRound();
    if (state.viewing) { toast('历史回看态为只读，重新上传后可执行'); return; }
    if (!cur || !cur.result.code) { toast('暂无可执行的修复代码'); return; }
    if (state.runBusy) { toast('正在执行中，请稍候'); return; }
    var lang = mapRunLanguage(cur.result.lang);
    if (!lang) {
      log('语言「' + (cur.result.lang || '未知') + '」不在沙箱执行支持范围（python/javascript），已降级为静态校验', 'info');
      staticVerify().catch(function () {});
      return;
    }
    state.runBusy = true;
    el.syncZcode.disabled = true;
    var oldLabel = el.syncZcodeLabel.textContent;
    el.syncZcodeLabel.textContent = 'ZCode 执行中…';
    setStage('verify');
    log('ZCode 执行中...（POST /api/engines/zcode/run · 沙箱内真实执行）', 'running');
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 45000); // 执行 ≤30s + 传输余量
    fetch(API_ZCODE_RUN, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: ctrl.signal,
      body: JSON.stringify({ code: cur.result.code, language: lang, timeout: 15 })
    }).then(function (res) {
      clearTimeout(timer);
      return res.json().catch(function () { return {}; }).then(function (j) {
        // 429：iris:zcode 桶（30 次/天）用尽 → 如实提示并终止（不静默降级、不白屏）
        if (res.status === 429 || (j && j.error === 'session_budget_exceeded')) {
          finishRunningLog('fail', '额度用尽');
          log('ZCode 执行次数已达今日上限（30 次），明日自动清零后可继续。当前结果已保留', 'fail');
          toast(String(j.message || j.msg || 'ZCode 执行额度已用尽').slice(0, 80));
          return;
        }
        // 降级触发（P0 任务书 3.3）：接口调用失败 / 引擎离线 / 503 / 其他非 200
        if (!res.ok || !j || j.ok !== true) {
          finishRunningLog('fail', '接口不可用');
          log('ZCode 引擎暂不可用，已降级为静态校验', 'info');
          return staticVerify().catch(function (e2) {
            if (e2 instanceof BudgetError) {
              log('预算熔断：' + e2.message + '。请稍后重试', 'fail');
              toast('已达调用次数上限，请稍后重试');
            }
          });
        }
        // 真实执行结果（HTTP 200）：completed / failed(exit_code_N) / failed(timeout)
        var d = j.data || {};
        cur.run = {
          mode: 'zcode',
          stdout: d.stdout || '', stderr: d.stderr || '',
          exit_code: d.exit_code, duration_ms: d.duration_ms,
          task_id: d.task_id, cwd: d.cwd, status: d.status, error: d.error
        };
        if (d.status === 'completed') {
          finishRunningLog('ok', '验证通过（真实执行）');
          log('验证通过：退出码 0，耗时 ' + (d.duration_ms || 0) + 'ms，stdout/stderr 见结果区', 'ok');
          cur.outcome = 'ok';
          markAllStagesDone();
        } else if (d.error === 'timeout') {
          finishRunningLog('fail', '执行超时');
          log('执行超时（>15s）：进程已被强制终止，已捕获的部分输出见结果区', 'fail');
          cur.outcome = 'fail';
          cur.result.verifyFailed = 'timeout';
          setStage('verify', true);
        } else {
          finishRunningLog('fail', '验证失败');
          log('验证失败：退出码 ' + d.exit_code + '，stderr 已在结果区展示。可点击「继续修复」迭代', 'fail');
          cur.outcome = 'fail';
          cur.result.verifyFailed = String(d.stderr || '').split('\n')[0] || ('exit_code_' + d.exit_code);
          setStage('verify', true);
        }
        updateHistoryOutcome(cur);
        renderResult();
      });
    }, function (e) {
      // 网络失败（后端离线等）→ 降级（P0 任务书 3.3：接口调用失败）
      clearTimeout(timer);
      finishRunningLog('fail', '接口不可达');
      log('ZCode 引擎暂不可用，已降级为静态校验', 'info');
      return staticVerify().catch(function (e2) {
        if (e2 instanceof BudgetError) {
          log('预算熔断：' + e2.message + '。请稍后重试', 'fail');
          toast('已达调用次数上限，请稍后重试');
        }
      });
    }).catch(function (e) {
      log('执行验证异常（不白屏）：' + (e && e.message || e), 'fail');
    }).then(function () {
      state.runBusy = false;
      el.syncZcode.disabled = false;
      el.syncZcodeLabel.textContent = oldLabel;
      updateActions();
    });
  });

  el.copyRun.addEventListener('click', function () {
    var cur = currentRound();
    if (!cur || !cur.run || cur.run.mode === 'static') { toast('暂无可复制的执行结果'); return; }
    var d = cur.run;
    var s = 'stdout:\n' + (d.stdout || '（无输出）') + '\n\nstderr:\n' + (d.stderr || '（无错误输出）') +
      '\n\nexit ' + (d.exit_code === null || d.exit_code === undefined ? '—' : d.exit_code) +
      ' · ' + (d.duration_ms || 0) + 'ms' +
      '\n任务 ' + (d.task_id || '—') + ' · 工作目录 ' + (d.cwd || '—');
    copyText(s, '执行结果已复制到剪贴板');
  });

  /* ============================================================
     [P1 4.1] 本地分析历史（localStorage，最近 20 条；回看 / 复用 / 删除 / 自动清理）
     记录内容：截图缩略图、时间、错误根因、修复代码（+语言/轮次/执行结果摘要）
     容量：单条 ≈5-8KB（缩略图 160px JPEG），20 条 ≈150KB，远低于 5MB 配额
     ============================================================ */
  function loadHistory() {
    try {
      var arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
      if (!Array.isArray(arr)) return [];
      return arr.filter(function (x) { return x && typeof x.id === 'string'; }).slice(0, HISTORY_MAX);
    } catch (e) { return []; }
  }
  function persistHistory(arr) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(arr.slice(0, HISTORY_MAX))); return true; }
    catch (e) {
      log('历史记录保存失败（本地存储不可用或已满），不影响当前分析', 'info');
      return false;
    }
  }
  /* [P1] 缩略图（默认 160px 历史用；[P2] 导出报告传 maxSize=420 取更清晰缩略图） */
  function makeThumb(canvas, maxSize) {
    try {
      var ms = maxSize || 160;
      var k = Math.min(1, ms / Math.max(canvas.width, canvas.height, 1));
      var c = newCanvas(canvas.width * k, canvas.height * k);
      var ctx = c.getContext('2d');
      ctx.drawImage(canvas, 0, 0, c.width, c.height);
      return c.toDataURL('image/jpeg', 0.6);
    } catch (e) { return ''; }
  }
  function saveHistoryRecord(rec) {
    if (!rec.result.root && !rec.result.code) return;
    var arr = loadHistory();
    var entry = {
      id: 'h' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36),
      ts: Date.now(),
      thumb: state.procCanvas ? makeThumb(state.procCanvas) : '',
      root: String(rec.result.root || '').slice(0, 200),
      code: rec.result.code || '',
      lang: rec.result.lang || '',
      rounds: state.rounds.length,
      outcome: rec.outcome
    };
    arr.unshift(entry);
    while (arr.length > HISTORY_MAX) arr.pop(); // 超出容量自动清理最早记录
    if (persistHistory(arr)) {
      state.historyId = entry.id;
      renderHistory();
      log('已存入本地分析历史（' + arr.length + '/' + HISTORY_MAX + '）', 'info');
    }
  }
  function updateHistoryOutcome(round) {
    if (!state.historyId) return;
    var arr = loadHistory();
    for (var i = 0; i < arr.length; i++) {
      if (arr[i].id === state.historyId) {
        arr[i].outcome = round.outcome;
        arr[i].rounds = Math.max(arr[i].rounds || 1, state.rounds.length);
        persistHistory(arr);
        renderHistory();
        return;
      }
    }
  }
  function fmtHistoryTime(ts) {
    var d = new Date(ts);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function renderHistory() {
    var arr = loadHistory();
    el.historyList.innerHTML = '';
    if (!arr.length) {
      var empty = document.createElement('p');
      empty.className = 'history-empty';
      empty.textContent = '暂无历史记录。完成一次分析后自动保存在本机。';
      el.historyList.appendChild(empty);
      return;
    }
    arr.forEach(function (rec) {
      var item = document.createElement('div');
      item.className = 'history-item';
      if (rec.thumb) {
        var img = document.createElement('img');
        img.className = 'history-thumb';
        img.alt = '截图缩略图';
        img.src = rec.thumb; // 本地 localStorage 缩略图，非外部输入
        item.appendChild(img);
      }
      var main = document.createElement('div'); main.className = 'history-main';
      var t1 = document.createElement('div'); t1.className = 'history-title';
      t1.textContent = rec.root || '（无根因描述）';
      t1.title = rec.root || '';
      var t2 = document.createElement('div'); t2.className = 'history-sub';
      t2.textContent = fmtHistoryTime(rec.ts) + ' · ' + (rec.lang || '通用') +
        (rec.rounds > 1 ? ' · ' + rec.rounds + ' 轮' : '') +
        (rec.outcome === 'ok' || rec.outcome === 'static-ok' ? ' · 已通过' :
          (rec.outcome === 'fail' || rec.outcome === 'static-fail' ? ' · 未通过' : ''));
      main.appendChild(t1); main.appendChild(t2);
      item.appendChild(main);

      function mkBtn(txt, title, fn) {
        var b = document.createElement('button');
        b.type = 'button'; b.className = 'ghost'; b.textContent = txt; b.title = title;
        b.addEventListener('click', fn);
        return b;
      }
      item.appendChild(mkBtn('查看', '在右侧结果区查看这条记录', function () { viewHistory(rec.id); }));
      item.appendChild(mkBtn('复制代码', '一键复用这条记录的修复代码', function () {
        if (rec.code) copyText(rec.code, '历史修复代码已复制到剪贴板');
        else toast('该记录没有修复代码');
      }));
      item.appendChild(mkBtn('删除', '删除这条历史记录', function () {
        deleteHistory(rec.id);
      }));
      el.historyList.appendChild(item);
    });
  }
  function viewHistory(id) {
    var arr = loadHistory();
    for (var i = 0; i < arr.length; i++) {
      if (arr[i].id === id) {
        var rec = arr[i];
        state._viewingId = id;
        state.viewing = {
          n: 1, ts: fmtHistoryTime(rec.ts), src: '历史记录（只读回看）',
          ocrText: '', errorInfo: '',
          result: { root: rec.root, plan: '', code: rec.code, lang: rec.lang, verifyFailed: null },
          run: null,
          outcome: rec.outcome === 'ok' ? 'ok' : (rec.outcome === 'fail' ? 'fail' :
            (rec.outcome === 'static-ok' ? 'static-ok' : (rec.outcome === 'static-fail' ? 'static-fail' : 'pending'))),
          verifyFailed: null
        };
        renderResult();
        log('历史回看：' + fmtHistoryTime(rec.ts) + ' 的分析记录（只读，复制按钮可用）', 'info');
        return;
      }
    }
  }
  function deleteHistory(id) {
    var arr = loadHistory();
    arr = arr.filter(function (x) { return x.id !== id; });
    persistHistory(arr);
    renderHistory();
    if (state.viewing && state._viewingId === id) { state.viewing = null; renderResult(); }
    toast('已删除该条历史记录');
  }
  el.historyClear.addEventListener('click', function () {
    persistHistory([]);
    renderHistory();
    toast('已清空全部历史记录');
    log('本地分析历史已清空', 'info');
  });

  /* ============================================================
     [P2 4.2] Markdown 报告导出（纯前端生成 .md + Blob 下载，零后端）
     内容：报错缩略图、错误等级与致命块、错误根因、修复方案、完整修复代码、执行结果
     ============================================================ */

  /* 四反引号围栏（内容本身可能含 ``` 三反引号代码块，防破坏 md 结构） */
  function mdFence(body, lang) {
    return '````' + (lang || '') + '\n' + String(body || '') + '\n````';
  }

  function mdEscapeFileName(s) {
    return String(s || '').replace(/[^A-Za-z0-9_\-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40).toLowerCase();
  }

  function fmtFullTime(ts) {
    var d = ts ? new Date(ts) : new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes());
  }

  /* 组装单条 Markdown 报告。rec: {title, ts, name, ocrText, result:{root,plan,code,lang}, run} */
  function buildMarkdown(rec) {
    var r = rec.result || { root: '', plan: '', code: '', lang: '' };
    var ce = classifyError(rec.ocrText || '');
    var lm = LEVEL_META[ce.level] || LEVEL_META.info;
    var features = extractFeatures(rec.ocrText || '');
    var L = [];
    L.push('# Iris 报错修复报告');
    L.push('');
    L.push('- 生成时间：' + (rec.tsText || new Date().toTimeString().slice(0, 8)));
    if (rec.name) L.push('- 来源截图：' + rec.name);
    if (rec.segments) L.push('- 识别方式：长图分段识别（' + rec.segments + ' 段，段高 ' + SEGMENT_HEIGHT + 'px / 重叠 ' + SEGMENT_OVERLAP + 'px）');
    L.push('- 错误等级：' + lm.text);
    if (features.type) L.push('- 错误类型：' + features.type);
    if (features.lang && features.lang !== 'plaintext') L.push('- 代码语言：' + (LANG_LABEL[features.lang] || features.lang));
    L.push('');
    L.push('## 报错截图');
    L.push('');
    if (rec.thumb) {
      L.push('![报错截图](' + rec.thumb + ')');
    } else {
      L.push('（无缩略图）');
    }
    L.push('');
    if (ce.blockLines.length) {
      L.push('## 致命错误块');
      L.push('');
      L.push(mdFence(ce.blockLines.join('\n'), 'text'));
      L.push('');
    }
    L.push('## 错误根因');
    L.push('');
    L.push(r.root || '（无）');
    L.push('');
    L.push('## 修复方案');
    L.push('');
    L.push(r.plan || '（无）');
    L.push('');
    L.push('## 修复代码');
    L.push('');
    L.push(mdFence(r.code, r.lang || ''));
    L.push('');
    L.push('## 执行结果');
    L.push('');
    var d = rec.run;
    if (d && d.mode === 'zcode') {
      var st = d.status === 'completed' ? '执行通过（退出码 0）' :
        (d.error === 'timeout' ? '执行超时（>15s 强制终止）' : '执行失败（退出码 ' + d.exit_code + '）');
      L.push('- 验证方式：ZCode 沙箱真实执行（POST /api/engines/zcode/run）');
      L.push('- 结果：' + st + '，耗时 ' + (d.duration_ms || 0) + 'ms');
      L.push('- 任务：' + (d.task_id || '—'));
      L.push('');
      L.push('### stdout');
      L.push('');
      L.push(mdFence(d.stdout || '（无输出）', 'text'));
      L.push('');
      L.push('### stderr');
      L.push('');
      L.push(mdFence(d.stderr || '（无错误输出）', 'text'));
    } else if (d && d.mode === 'static') {
      L.push('- 验证方式：静态语法校验（降级通道，未真实执行）');
      L.push('- 结果：' + (d.ok ? '通过' : '失败：' + (d.reason || '')));
    } else {
      L.push('- 未执行验证（记录生成时未点击「执行验证」，或历史记录不含执行信息）');
    }
    L.push('');
    L.push('> 由 Iris 视觉智能体（P2）自动生成 · 本地分析记录，纯前端导出');
    L.push('');
    return L.join('\n');
  }

  /* Blob 下载（file:// 与 http 同源均兼容）；返回是否成功 */
  function downloadText(filename, text, mime) {
    try {
      var blob = new Blob([text], { type: (mime || 'text/markdown') + ';charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      return true;
    } catch (e) {
      log('导出失败：' + (e && e.message || e), 'fail');
      return false;
    }
  }

  /* 导出文件名：iris-fix-时间-错误类型.md（任务书 4.2：时间+错误类型命名） */
  function exportFileName(ocrText, ts) {
    var features = extractFeatures(ocrText || '');
    var slug = mdEscapeFileName(features.type) || 'error';
    return 'iris-fix-' + fmtFullTime(ts) + '-' + slug + '.md';
  }

  /* 导出当前轮修复记录 */
  function exportCurrentRound() {
    var cur = currentRound();
    if (!cur || (!cur.result.root && !cur.result.plan && !cur.result.code)) { toast('暂无可导出的结果'); return; }
    var rec = {
      tsText: new Date().toTimeString().slice(0, 8),
      name: (state.file && state.file.name) || (state._p6Text ? '' : '粘贴图片.png'), // [P6 守卫 G4] 纯文本来源不再误标「截图」
      ocrText: cur.ocrText || '',
      segments: state.segments ? state.segments.length : null,
      result: cur.result,
      run: cur.run,
      thumb: state.procCanvas ? makeThumb(state.procCanvas, 420) : ''
    };
    var okFile = downloadText(exportFileName(rec.ocrText), buildMarkdown(rec));
    if (okFile) {
      toast('✓ Markdown 报告已导出');
      log('已导出 Markdown 报告（' + (rec.name || (state._p6Text ? '纯文本日志' : '未命名')) + '）', 'ok');
    }
  }
  el.exportMd.addEventListener('click', exportCurrentRound);

  /* ============================================================
     [P2 4.1] 轻量批量分析（最多 3 张；队列串行「预处理→OCR→诊断→分级」）
     独立于主流程 state（batch 对象），单项失败不阻断队列；
     结果按 fatal > warn > info 排序；内联详情 + 单条/全部导出。
     ============================================================ */
  var batch = { items: [], running: false };

  /* 批量单项：OCR（长图自动分段 / 普通图走独立重试通道，不动既有 stepOcr 代码） */
  function batchOcrText(item, raw, isLong) {
    if (isLong) {
      var segs = buildSegments(raw);
      item.segCount = segs.length;
      log('批量[' + item.name + '] 长图分段识别：' + segs.length + ' 段', 'info');
      return segmentedOcrCore(segs, el.enhanceMode.value || 'auto', function (n, total) {
        log('批量[' + item.name + '] OCR 段 ' + n + '/' + total + '...', 'running');
      }).then(function (merged) {
        log('批量[' + item.name + '] 分段合并完成（' + merged.lines + ' 行，去重 ' + merged.dropped + ' 行）', 'info');
        return merged.text;
      });
    }
    var out = runPreprocessPipeline(raw, {
      mode: el.enhanceMode.value || 'auto',
      isPng: item.file.type === 'image/png'
    });
    var dataUrl = out.dataUrl;
    function once() {
      var body = Object.assign({
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: OCR_PROMPT },
            { type: 'image_url', image_url: { url: dataUrl } }
          ]
        }],
        temperature: 0
      }, modelField());
      return apiChat(body).then(function (j) { return String(j.content || '').trim(); });
    }
    return once().catch(function (e) {
      if (e instanceof BudgetError) throw e;
      log('批量[' + item.name + '] OCR 首次失败，重试 1 次…', 'info');
      return once();
    });
  }

  /* 批量单项全流程：解码 →（长图重解码）→ OCR → 诊断 → 解析 → 分级 → 缩略图 */
  function batchProcessItem(item) {
    return decodeFileToCanvas(item.file).then(function (raw) {
      item.longHeightPx = raw.height;
      var isLong = raw.height > LONG_IMG_THRESHOLD;
      var prep = isLong ? decodeFileToCanvas(item.file, LONG_DECODE_CAP) : Promise.resolve(raw);
      return prep.then(function (raw2) {
        item.long = isLong && raw2.height > LONG_IMG_THRESHOLD;
        item.thumb = makeThumb(raw2, 420); // 导出报告用缩略图
        return batchOcrText(item, raw2, item.long);
      });
    }).then(function (text) {
      if (!text) throw new Error('OCR 返回为空');
      item.ocrText = text;
      var features = extractFeatures(text);
      var prompt = buildDiagPrompt(text, features);
      log('批量[' + item.name + '] 诊断中（' + (prompt.indexOf('【结构化特征】') !== -1 ? '结构化特征 prompt' : '固化诊断 prompt') + '）...', 'running');
      var body = Object.assign({
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.2
      }, modelField());
      return apiChat(body).then(function (j) {
        var r = parseResult(String(j.content || ''));
        item.root = r.root; item.plan = r.plan; item.code = r.code; item.lang = r.lang;
        item.level = classifyError(text).level;
        item.status = 'ok';
      });
    });
  }

  /* 严重程度排序：fatal(0) > warn(1) > info(2)，同级保持加入顺序（稳定） */
  function sortBatchItems() {
    batch.items = batch.items.slice().sort(function (a, b) {
      var d = levelRank(a.level) - levelRank(b.level);
      return d !== 0 ? d : (a._ord - b._ord);
    });
  }

  /* 批量队列渲染（textContent 零注入） */
  function renderBatchList() {
    el.batchList.innerHTML = '';
    var hasOk = batch.items.some(function (it) { return it.status === 'ok'; });
    el.batchFoot.hidden = !hasOk;
    batch.items.forEach(function (item) {
      var det = document.createElement('details');
      det.className = 'batch-item';
      var sum = document.createElement('summary');
      var st = document.createElement('span');
      st.className = 'batch-st ' + (item.status === 'ok' ? 'is-ok' : item.status === 'fail' ? 'is-fail' : 'is-running');
      st.textContent = item.status === 'ok' ? '●' : item.status === 'fail' ? '✕' : (item.status === 'running' ? '◐' : '…');
      var nm = document.createElement('span');
      nm.className = 'batch-name';
      nm.textContent = item.name + (item.long ? '（长图 ' + item.longHeightPx + 'px）' : '');
      nm.title = item.name;
      sum.appendChild(st); sum.appendChild(nm);
      if (item.status === 'ok' && item.level) {
        var lm = LEVEL_META[item.level] || LEVEL_META.info;
        var lv = document.createElement('span');
        lv.className = 'lv-badge ' + lm.cls;
        lv.textContent = lm.text;
        sum.appendChild(lv);
      }
      if (item.status === 'fail') {
        var err = document.createElement('span');
        err.className = 'batch-err';
        err.textContent = '失败：' + (item.error || '未知错误').slice(0, 60);
        sum.appendChild(err);
      }
      det.appendChild(sum);
      if (item.status === 'ok') {
        var body = document.createElement('div');
        body.className = 'batch-detail';
        function h4(t) { var x = document.createElement('h4'); x.textContent = t; return x; }
        function p(t) { var x = document.createElement('p'); x.textContent = t || '（无）'; return x; }
        body.appendChild(h4('【错误根因】')); body.appendChild(p(item.root));
        body.appendChild(h4('【修复方案】')); body.appendChild(p(item.plan));
        body.appendChild(h4('【修复代码】' + (item.lang || '')));
        var pre = document.createElement('pre');
        pre.className = 'code-wrap';
        var code = document.createElement('code');
        code.textContent = item.code || '';
        pre.appendChild(code);
        body.appendChild(pre);
        var actions = document.createElement('div');
        actions.style.display = 'flex';
        actions.style.gap = '6px';
        var bCopy = document.createElement('button');
        bCopy.type = 'button'; bCopy.className = 'ghost'; bCopy.textContent = '复制代码';
        bCopy.addEventListener('click', function () {
          if (item.code) copyText(item.code, '批量修复代码已复制到剪贴板');
        });
        var bExport = document.createElement('button');
        bExport.type = 'button'; bExport.className = 'ghost'; bExport.textContent = '导出 Markdown';
        bExport.addEventListener('click', function () { exportBatchItem(item); });
        actions.appendChild(bCopy); actions.appendChild(bExport);
        body.appendChild(actions);
        det.appendChild(body);
      }
      el.batchList.appendChild(det);
    });
  }

  /* 批量单条导出 */
  function exportBatchItem(item) {
    var rec = {
      tsText: fmtHistoryTime(item.ts),
      name: item.name,
      ocrText: item.ocrText || '',
      segments: item.long ? (item.segCount || null) : null,
      result: { root: item.root, plan: item.plan, code: item.code, lang: item.lang },
      run: null,
      thumb: item.thumb || ''
    };
    if (downloadText(exportFileName(rec.ocrText, item.ts), buildMarkdown(rec))) {
      toast('✓ 已导出「' + item.name + '」');
    }
  }

  /* 批量全部导出（单文件合并报告） */
  function exportBatchAll() {
    var oks = batch.items.filter(function (it) { return it.status === 'ok'; });
    if (!oks.length) { toast('暂无可导出的批量结果'); return; }
    var parts = ['# Iris 批量修复报告（' + oks.length + ' 条）', ''];
    parts.push('- 生成时间：' + new Date().toTimeString().slice(0, 8));
    parts.push('- 排序：按错误严重程度（致命错误 > 警告 > 提示）');
    parts.push('');
    oks.forEach(function (item, i) {
      var md = buildMarkdown({
        tsText: fmtHistoryTime(item.ts),
        name: item.name,
        ocrText: item.ocrText || '',
        segments: item.long ? (item.segCount || null) : null,
        result: { root: item.root, plan: item.plan, code: item.code, lang: item.lang },
        run: null,
        thumb: item.thumb || ''
      });
      parts.push('---');
      parts.push('');
      parts.push('## 批次 ' + (i + 1) + ' / ' + oks.length);
      parts.push('');
      // 去掉单条报告首行 H1，避免多 H1
      parts.push(md.split('\n').slice(1).join('\n'));
    });
    if (downloadText('iris-fix-batch-' + fmtFullTime() + '.md', parts.join('\n'))) {
      toast('✓ 批量报告已导出（' + oks.length + ' 条）');
      log('已导出批量 Markdown 报告（' + oks.length + ' 条）', 'ok');
    }
  }
  el.batchExport.addEventListener('click', exportBatchAll);

  /* 批量入口：文件数组 → 校验（≤3 张硬上限）→ 入队 → 串行执行
     [P2] change 处理器与本函数复用（验收自动化经 __iris.p2.runBatchFiles 同源注入） */
  function runBatchFiles(files) {
    if (batch.running) { toast('批量分析进行中，请稍候'); return; }
    if (state.running) { toast('单图分析进行中，请稍候'); return; }
    if (p6 && p6.isTextMode()) { toast('批量分析仅支持截图通道，请先切回「上传截图」'); return; } // [P6 守卫 G3] 通道互斥
    if (files.length > BATCH_MAX) {
      log('批量分析：一次最多 ' + BATCH_MAX + ' 张，收到 ' + files.length + ' 张，已整批拒绝。请分批处理（每次 ≤' + BATCH_MAX + ' 张）', 'fail');
      toast('批量上限 ' + BATCH_MAX + ' 张，请分批处理');
      return;
    }
    batch.items = files.map(function (f, i) {
      return {
        _ord: i, file: f, name: f.name || ('图片' + (i + 1) + '.png'),
        status: 'pending', level: null, long: false, longHeightPx: 0, segCount: 0,
        ocrText: '', root: '', plan: '', code: '', lang: '', error: '', ts: Date.now(), thumb: ''
      };
    });
    renderBatchList();
    batch.running = true;
    el.batchPick.disabled = true;
    log('—— 批量分析开始：' + files.length + ' 张，队列依次执行（预处理 → OCR → 诊断 → 分级）——', 'info');
    var chain = Promise.resolve();
    batch.items.forEach(function (item) {
      chain = chain.then(function () {
        item.status = 'running';
        item.ts = Date.now();
        renderBatchList();
        log('批量[' + item.name + '] 开始（' + formatBytes(item.file.size) + '）', 'info');
        return batchProcessItem(item).catch(function (e) {
          item.status = 'fail';
          item.error = (e && e.message) || String(e);
          if (e instanceof BudgetError) {
            item.error = '预算熔断：' + item.error;
            log('批量[' + item.name + '] ' + item.error + '。剩余任务终止，已生成结果保留', 'fail');
            throw { stopQueue: true, budget: true, message: item.error };
          }
          log('批量[' + item.name + '] 失败：' + item.error, 'fail');
        }).then(function () {
          renderBatchList();
        });
      });
    });
    chain.catch(function (e) {
      if (e && e.stopQueue) {
        // 预算熔断：剩余 pending 项标记跳过，队列终止（如实提示）
        batch.items.forEach(function (it) {
          if (it.status === 'pending') { it.status = 'fail'; it.error = '已跳过（预算熔断）'; }
        });
      }
    }).then(function () {
      batch.running = false;
      el.batchPick.disabled = false;
      sortBatchItems();
      renderBatchList();
      var okN = batch.items.filter(function (it) { return it.status === 'ok'; }).length;
      log('—— 批量分析结束：' + okN + '/' + batch.items.length + ' 成功，已按错误严重程度排序 ——', okN ? 'ok' : 'fail');
      if (okN) toast('批量分析完成：' + okN + '/' + batch.items.length + ' 成功');
    });
  }

  /* 批量入口事件绑定（UI 交互走文件选择器；验收自动化走 __iris.p2.runBatchFiles） */
  el.batchPick.addEventListener('click', function () {
    if (batch.running) { toast('批量分析进行中，请稍候'); return; }
    if (state.running) { toast('单图分析进行中，请稍候'); return; }
    el.batchInput.click();
  });
  el.batchInput.addEventListener('change', function () {
    var files = Array.prototype.slice.call(el.batchInput.files || []);
    el.batchInput.value = '';
    if (files.length) runBatchFiles(files);
  });

  /* ---------------- 日志清空 ---------------- */
  el.clearLog.addEventListener('click', function () {
    el.timeline.innerHTML = '<p class="timeline-empty">日志已清空。</p>';
    logCount = 0;
    el.logCounter.textContent = '0';
    state._runningLogRow = null;
  });

  /* ---------------- 环境标签（网关连通性自检，只读 /api/health 既有路由） ---------------- */
  fetch(GATEWAY + '/api/health', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
    el.envTag.textContent = (j && j.ok) ? ('MoRay ' + (j.version || '') + ' · 在线') : 'MoRay 离线';
  }).catch(function () { el.envTag.textContent = 'MoRay 离线'; });

  /* ---------------- 启动 ---------------- */
  initTheme();
  renderResult();
  renderHistory();
  log('Iris 工作台就绪。接口：POST /api/llm/chat · GET /api/engines/list（阶段0审计确认）', 'info');

  // 冒烟/自动化辅助钩子（只读状态注入，不改变任何用户路径）
  window.__iris = {
    takeFile: takeFile,
    runPipeline: runPipeline,
    renderResult: renderResult, // [P0] 供验收自动化复现结果区渲染（[P1] 语义=轮次渲染）
    renderRounds: renderResult, // [P1] 别名
    state: state,
    api: { chat: API_CHAT, engines: API_ENGINES, zcodeRun: API_ZCODE_RUN },
    // [P1] 预处理/多轮/多语言 内部函数暴露（仅测试与验收自动化使用）
    preprocess: { applyProcessing: applyProcessing, extractFeatures: extractFeatures, detectLang: detectLang, buildDiagPrompt: buildDiagPrompt, runPreprocessPipeline: runPreprocessPipeline },
    continueFix: continueFix,
    history: { load: loadHistory, view: viewHistory, remove: deleteHistory },
    // [P2] 长日志/Diff/分级/批量/导出 内部函数暴露（只增不改，P0/P1 验收脚本兼容）
    p2: {
      classifyError: classifyError, classifyLine: classifyLine,
      buildSegments: buildSegments, mergeSegmentTexts: mergeSegmentTexts,
      diffLines: diffLines, extractOriginalCode: extractOriginalCode,
      renderDiffView: renderDiffView, setDiffTab: setDiffTab,
      buildMarkdown: buildMarkdown, downloadText: downloadText,
      batch: batch, renderBatchList: renderBatchList,
      exportCurrentRound: exportCurrentRound,
      runBatchFiles: runBatchFiles, // 批量入口函数化（change 处理器复用；验收自动化同源注入用）
      pushRound: pushRound          // 轮次注入（P0/P1 同源自动化先例：仅经公开钩子）
    }
  };

  /* ============================================================
     [P3] IrisAgent —— 能力原子化与标准接口封装
     （任务书「面向 AGI 的能力原子化与标准接口封装」，docs/p3_audit.md §1.4 设计判定）
     ------------------------------------------------------------
     定位：把既有能力（预处理 → OCR → 特征提取 → 诊断 → 修复生成 → ZCode 验证 →
     多轮迭代）封装为可被 AGI 调度的标准前端门面 window.IrisAgent（__iris.agent 同引用）。
     红线符合性（docs/p3_audit.md §4 预检，全程不变量）：
       1. 零新增后端：仅复用 /api/llm/chat、/api/engines/list、/api/engines/zcode/run、
          /api/health 四个既有路由；预算桶 session_id 由 apiChat 内部注入，语义零变更。
       2. 零主路径改动：AGI 任务持独立任务态（tasks 注册表 + taskId），不写用户
          state/rounds/结果区 DOM；界面痕迹仅经既有 log() 通道追加「[AGI]」前缀日志
          （info/ok/fail 级，不触碰 running 行与分阶段进度条），用户查看中的结果零覆盖。
       3. 业务零重写：decodeFileToCanvas / runPreprocessPipeline / buildSegments /
          segmentedOcrCore / extractFeatures / buildDiagPrompt / buildRoundPrompt /
          parseResult / classifyError / mapRunLanguage / detectLang / apiChat /
          OCR_PROMPT / VERIFY_PROMPT / API_ZCODE_RUN / LANG_LABEL / MAX_ROUNDS /
          MAX_RAW_BYTES / ACCEPT_TYPES / LONG_IMG_THRESHOLD / LONG_DECODE_CAP
          全部逐字复用同闭包既有实现（IrisAgent 位于同一 IIFE 作用域内）。
       4. 既有降级/熔断语义逐字保留：OCR 失败自动重试 1 次；ZCode 不可用/503/网络失败
          降级静态校验（staticVerify 同语义）；429 预算熔断如实上报不静默重试；
          ZCode 执行参数 {timeout:15} 与 45s 传输余量均与手动路径一致。
     并发与安全（任务书阶段5）：
       - 诊断链（analyzeImage/continueFix）与验证链（runValidation）各自串行调度，
         两链可并行 → 同一时刻至多 1 个诊断类任务 + 1 个验证任务在执行；
       - 调用审计日志（内存环形 200 条，getAuditLog 只读）；
       - 任务注册表上限 50，超出自动清理最旧已终止任务（防内存膨胀）；
       - 取消语义：pending 即时取消；运行中协作式取消（阶段边界/分段边界检查点）；
         验证任务经 AbortController 即时中断。
     说明（能力边界，如实暴露）：长图（>1600px）分段识别复用 segmentedOcrCore，
     其内部模型覆盖沿用页面「高级：模型覆盖」设置（modelField）；options.model 作用于
     单图 OCR 与诊断/修复/验证调用。
     ============================================================ */
  var IrisAgent = (function () {
    'use strict';

    /* ---------------- 契约常量（任务书 2.2 统一状态/事件/错误码） ---------------- */
    var STATUS = {
      PENDING: 'pending', PREPROCESSING: 'preprocessing', OCR: 'ocr',
      DIAGNOSING: 'diagnosing', FIXING: 'fixing', VALIDATING: 'validating',
      COMPLETED: 'completed', FAILED: 'failed', CANCELLED: 'cancelled'
    };
    var STATUS_LIST = ['pending', 'preprocessing', 'ocr', 'diagnosing', 'fixing',
      'validating', 'completed', 'failed', 'cancelled'];
    var EVENTS = ['task-start', 'task-progress', 'task-ocr-done', 'task-diagnosis-done',
      'task-fix-done', 'task-validation-done', 'task-completed', 'task-failed', 'task-cancelled'];
    var ERROR_CODES = ['INVALID_IMAGE', 'PREPROCESS_FAILED', 'OCR_FAILED', 'DIAGNOSIS_FAILED',
      'VALIDATION_FAILED', 'BUDGET_EXCEEDED', 'TIMEOUT', 'TASK_NOT_FOUND', 'TASK_CANCELLED',
      'UNSUPPORTED_OPERATION'];

    var TASK_CAP = 50;    // 任务注册表上限（超出清理最旧已终止任务）
    var AUDIT_CAP = 200;  // 审计日志环形上限（仅前端内存，不落盘）

    /* ---------------- 标准化错误对象（reject 载荷；err.code ∈ ERROR_CODES） ---------------- */
    function AgiError(code, message, taskId) {
      this.name = 'AgiError';
      this.code = code;
      this.message = message || code;
      if (taskId) this.taskId = taskId;
    }
    AgiError.prototype = Object.create(Error.prototype);

    /* ---------------- 事件总线（on/off/once/emit；处理器异常不阻断任务） ---------------- */
    var bus = {}; // event -> [handler]
    function on(event, handler) {
      if (typeof handler !== 'function') {
        throw new AgiError('UNSUPPORTED_OPERATION', 'handler 必须为函数');
      }
      if (EVENTS.indexOf(event) === -1) {
        throw new AgiError('UNSUPPORTED_OPERATION', '未知事件「' + event + '」，可用：' + EVENTS.join(' / '));
      }
      (bus[event] = bus[event] || []).push(handler);
      return true;
    }
    function off(event, handler) {
      var arr = bus[event];
      if (!arr) return false;
      var before = arr.length;
      bus[event] = arr.filter(function (h) { return h !== handler; });
      return bus[event].length < before;
    }
    function once(event, handler) {
      function wrapped(payload) { off(event, wrapped); handler(payload); }
      return on(event, wrapped);
    }
    function emit(event, payload) {
      var arr = bus[event] || [];
      for (var i = 0; i < arr.length; i++) {
        try { arr[i](payload); }
        catch (e) { log('[AGI] 事件处理器异常（已忽略，不影响任务）：' + (e && e.message || e), 'info'); }
      }
    }

    /* ---------------- 任务注册表与调用审计日志（任务书 5.1） ---------------- */
    var tasks = {};     // taskId -> task（仅前端内存，页面刷新即清）
    var audit = [];     // 环形审计日志
    var auditSeq = 0;

    function createTaskId() {
      return 'agi-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    }
    function newTask(kind, argsSummary) {
      var t = {
        id: createTaskId(), kind: kind, source: 'agi',   // 来源标记：AGI 调用（任务书 5.3）
        status: STATUS.PENDING, stage: '', progress: 0,
        createdAt: Date.now(), startedAt: null, finishedAt: null,
        cancelRequested: false, _started: false, _stage: '', _abort: null,
        error: null, argsSummary: argsSummary || '',
        imageText: '', features: null, segments: 0,
        rounds: [],          // {n, ocrText, result:{root,plan,code,lang}, outcome, errorInfo}
        validation: null,    // runValidation 产物（validate 任务本体或 analyze 任务回填）
        result: null         // 完成后的统一结果对象（任务书 3.3 结构）
      };
      tasks[t.id] = t;
      pruneTasks();
      return t;
    }
    function isTerminal(st) {
      return st === STATUS.COMPLETED || st === STATUS.FAILED || st === STATUS.CANCELLED;
    }
    function pruneTasks() {
      var ids = Object.keys(tasks);
      if (ids.length <= TASK_CAP) return;
      var done = ids.map(function (id) { return tasks[id]; })
        .filter(function (t) { return isTerminal(t.status); })
        .sort(function (a, b) { return (a.finishedAt || 0) - (b.finishedAt || 0); });
      while (done.length && Object.keys(tasks).length > TASK_CAP) delete tasks[done.shift().id];
    }
    function auditLog(task, event, detail, status) {
      audit.push({
        seq: ++auditSeq, ts: Date.now(),
        taskId: task ? task.id : null, kind: task ? task.kind : null,
        event: event, status: status || (task ? task.status : null),
        detail: String(detail || '').slice(0, 200),
        argsSummary: task ? task.argsSummary : ''
      });
      if (audit.length > AUDIT_CAP) audit.splice(0, audit.length - AUDIT_CAP);
    }

    /* ---------------- 状态推进 / 事件发射 / 取消检查点 ---------------- */
    function setStatus(task, status, stage, prog) {
      task.status = status;
      if (stage !== undefined && stage !== null) task.stage = stage;
      if (typeof prog === 'number') task.progress = Math.max(0, Math.min(100, Math.round(prog)));
      if (!task.startedAt && status !== STATUS.PENDING) task.startedAt = Date.now();
    }
    function taskEvent(task, event, data) {
      emit(event, {
        event: event, taskId: task.id, kind: task.kind, source: task.source,
        status: task.status, stage: task.stage, progress: task.progress,
        ts: Date.now(), data: data || null
      });
    }
    function progress(task, stage, pct, message) {
      setStatus(task, task.status, stage, pct);
      taskEvent(task, 'task-progress', { stage: stage, progress: task.progress, message: message || stage });
    }
    function checkCancelled(task) {
      if (task.cancelRequested) throw new AgiError('TASK_CANCELLED', '任务已被取消', task.id);
    }

    /* ---------------- 错误归一：按阶段映射标准错误码 ----------------
       BudgetError（apiChat 429 熔断）→ BUDGET_EXCEEDED；apiChat 超时文案 → TIMEOUT；
       其余按任务所处阶段映射（prep/ocr/diag/validate）。 */
    function stageError(stage, err, taskId) {
      if (err instanceof AgiError) return err;
      if (err instanceof BudgetError) return new AgiError('BUDGET_EXCEEDED', err.message, taskId);
      var msg = (err && err.message) || String(err || '未知错误');
      if (msg.indexOf('请求超时') !== -1) return new AgiError('TIMEOUT', msg, taskId);
      var map = {
        prep: 'PREPROCESS_FAILED', ocr: 'OCR_FAILED',
        diag: 'DIAGNOSIS_FAILED', fix: 'DIAGNOSIS_FAILED',
        validate: 'VALIDATION_FAILED', image: 'INVALID_IMAGE'
      };
      return new AgiError(map[stage] || 'UNSUPPORTED_OPERATION', msg, taskId);
    }
    function failTask(task, err) {
      if (task.status === STATUS.CANCELLED) return; // cancelTask 已置态并广播，不重复
      var e = stageError(task._stage || 'diag', err, task.id);
      task.finishedAt = Date.now();
      if (e.code === 'TASK_CANCELLED') {
        setStatus(task, STATUS.CANCELLED, '已取消', task.progress);
        taskEvent(task, 'task-cancelled', { reason: e.message });
        auditLog(task, 'cancelled', e.message, 'cancelled');
        log('[AGI ' + task.id + '] 任务已取消', 'info');
        return;
      }
      setStatus(task, STATUS.FAILED, '失败', task.progress);
      task.error = { code: e.code, message: e.message };
      taskEvent(task, 'task-failed', { code: e.code, message: e.message });
      auditLog(task, 'failed', e.code + ': ' + e.message, 'failed');
      log('[AGI ' + task.id + '] 任务失败（' + e.code + '）：' + e.message.slice(0, 120), 'fail');
    }

    /* ---------------- 并发与队列约束（任务书 5.2） ----------------
       诊断链（analyzeImage/continueFix）与验证链（runValidation）各自严格串行；
       两链可并行。链自身吞错保持后续任务可继续调度；失败经 failTask 标准化后向调用方传播。 */
    var diagChain = Promise.resolve();
    var valChain = Promise.resolve();
    function startAndRun(task, runFn) {
      if (task.status === STATUS.CANCELLED) {
        throw new AgiError('TASK_CANCELLED', '任务在队列等待时被取消', task.id);
      }
      task._started = true;
      if (!task.startedAt) task.startedAt = Date.now();
      auditLog(task, 'started', task.argsSummary);
      return runFn();
    }
    function enqueueDiag(task, runFn) {
      var exec = diagChain.then(function () { return startAndRun(task, runFn); });
      diagChain = exec.catch(function () { /* 链吞错 */ });
      return exec.catch(function (err) { failTask(task, err); throw err; });
    }
    function enqueueVal(task, runFn) {
      var exec = valChain.then(function () { return startAndRun(task, runFn); });
      valChain = exec.catch(function () { /* 链吞错 */ });
      return exec.catch(function (err) { failTask(task, err); throw err; });
    }

    /* ---------------- 图片入参归一（File/Blob/dataURL → Blob，复用主流程校验语义） ---------------- */
    function normalizeImage(image) {
      var blobP;
      if (image && typeof image === 'object' && typeof Blob === 'function' && image instanceof Blob) {
        blobP = Promise.resolve(image);
      } else if (typeof image === 'string' && /^data:image\/(png|jpe?g);/i.test(image)) {
        blobP = fetch(image).then(function (r) { return r.blob(); });
      } else {
        return Promise.reject(new AgiError('INVALID_IMAGE', 'image 必须是 png/jpg 的 File/Blob 或 dataURL'));
      }
      return blobP.then(function (blob) {
        if (ACCEPT_TYPES.indexOf(blob.type) === -1) {
          throw new AgiError('INVALID_IMAGE', '仅支持 png/jpg（收到「' + (blob.type || '未知') + '」）');
        }
        if (blob.size > MAX_RAW_BYTES) {
          throw new AgiError('INVALID_IMAGE', '图片超过 5MB 上限（' + (Math.round(blob.size / 10485.76) / 100) + 'MB）');
        }
        return blob;
      });
    }

    /* ---------------- 入参摘要（审计用，不含图片/代码全文） ---------------- */
    function argsDigest(image, options) {
      var d = '';
      try {
        if (image && typeof image === 'object' && image instanceof Blob) {
          d = 'Blob ' + (image.type || '?') + ' ' + formatBytes(image.size);
        } else if (typeof image === 'string') {
          d = 'dataURL ' + image.length + ' chars';
        }
        var keys = options ? Object.keys(options) : [];
        d += '；options: ' + (keys.length ? keys.join(',') : '（默认）');
      } catch (e) { d = '（摘要失败）'; }
      return d;
    }

    /* ---------------- OCR（复用既有 OCR_PROMPT / segmentedOcrCore / apiChat） ----------------
       长图（>1600px）→ buildSegments 分段 + segmentedOcrCore（段间 onSeg 即取消检查点）；
       单图 → runPreprocessPipeline 后经 apiChat 识别（payload 与 stepOcr 同构，失败重试 1 次）。 */
    function agiOcr(task, blob, options) {
      var mode = (options && (options.enhanceMode === 'off' || options.enhanceMode === 'bin' ||
        options.enhanceMode === 'auto')) ? options.enhanceMode : 'auto';
      var isPng = blob.type === 'image/png';
      task._stage = 'prep';
      setStatus(task, STATUS.PREPROCESSING, '预处理', 8);
      progress(task, '预处理', 8, '解码与预处理（' + mode + '）');
      return decodeFileToCanvas(blob).then(function (raw) {
        checkCancelled(task);
        // [P2] 长图检测语义与主流程一致：超阈值以更高上限重解码（保文字分辨率）
        if (raw.height > LONG_IMG_THRESHOLD) {
          return decodeFileToCanvas(blob, LONG_DECODE_CAP).then(function (rawLong) {
            return { raw: rawLong, long: true };
          });
        }
        return { raw: raw, long: false };
      }).then(function (d) {
        checkCancelled(task);
        task._stage = 'ocr';
        setStatus(task, STATUS.OCR, 'OCR 识别', 30);
        if (d.long) {
          var segs = buildSegments(d.raw);
          task.segments = segs.length;
          log('[AGI ' + task.id + '] 长截图 ' + d.raw.width + '×' + d.raw.height + '，分段识别 ' + segs.length + ' 段', 'info');
          return segmentedOcrCore(segs, mode, function (n, total) {
            checkCancelled(task); // 分段边界 = 协作式取消检查点
            progress(task, 'OCR 分段 ' + n + '/' + total,
              30 + Math.round(25 * (n - 1) / Math.max(total, 1)), '分段识别 ' + n + '/' + total);
          }).then(function (merged) {
            task.imageText = merged.text;
            return task.imageText;
          });
        }
        var out = runPreprocessPipeline(d.raw, { mode: mode, manualCrop: null, isPng: isPng });
        progress(task, 'OCR 识别', 35, '单图识别');
        var payload = {
          messages: [{
            role: 'user',
            content: [
              { type: 'text', text: OCR_PROMPT },
              { type: 'image_url', image_url: { url: out.dataUrl } } // OpenAI 视觉分片（与 stepOcr 同构）
            ]
          }],
          temperature: 0
        };
        if (options && options.model) payload.model = String(options.model);
        return apiChat(payload).catch(function (e) {
          if (e instanceof BudgetError || e instanceof AgiError) throw e;
          return apiChat(payload); // 与主流程 stepOcr 一致：失败自动重试 1 次
        }).then(function (j) {
          task.imageText = String(j.content || '').trim();
          return task.imageText;
        });
      }).then(function (text) {
        if (!text) throw new AgiError('OCR_FAILED', 'OCR 返回为空', task.id);
        // [P3] OCR 边界剥离思考块（复用 P2 stripThinkBlocks）：模型泄漏的 <think> 推理
        // 不是截图 OCR 内容，保留会污染 AGI 结果契约并放大下游诊断模型推理循环
        // （冒烟实测：含 think 的多轮 Prompt 使本地模型 2 次 >300s 超时；剥离后 100s 级返回）。
        // 仅作用于 AGI 路径；手动主路径 ocrText 语义不变。
        task.imageText = stripThinkBlocks(text);
        taskEvent(task, 'task-ocr-done', {
          chars: task.imageText.length, segments: task.segments || 1,
          // [P4] 同步前置校验载荷：主对话 87 分片 AGI 执行器据此在诊断启动前完成
          // 「非报错截图」判定（agiDiagnose 入口 checkCancelled 为同步检查点），
          // 命中即同步 cancelTask → 省下一次诊断 LLM 调用。head+tail 保留长日志首尾特征。
          text: task.imageText.length > 4000
            ? (task.imageText.slice(0, 2000) + '\n...\n' + task.imageText.slice(-2000))
            : task.imageText
        });
        auditLog(task, 'ocr-done', task.imageText.length + ' chars');
        log('[AGI ' + task.id + '] OCR 完成（' + task.imageText.length + ' 字符）', 'info');
        return task.imageText;
      });
    }

    /* ---------------- 诊断 + 修复生成（复用 extractFeatures/buildDiagPrompt/apiChat/parseResult） ---------------- */
    function agiDiagnose(task, options) {
      checkCancelled(task);
      task._stage = 'diag';
      setStatus(task, STATUS.DIAGNOSING, '故障诊断', 62);
      progress(task, '故障诊断', 62, '结构化特征 + 根因诊断');
      var features = extractFeatures(task.imageText);
      task.features = features;
      /* [v2] 分流：ERR_PAT 命中 → 诊断（现状不动）；无报错但 CODE_PAT 命中 → 代码评审；
         都不命中 → 仍走 buildDiagPrompt（其内部无特征时逐字回退 DIAG_PROMPT，原回退路径）。 */
      var prompt = (ERR_PAT.test(task.imageText) || !CODE_PAT.test(task.imageText))
        ? buildDiagPrompt(task.imageText, features)
        : buildReviewPrompt(task.imageText);
      var payload = { messages: [{ role: 'user', content: prompt }], temperature: 0.2 };
      if (options && options.model) payload.model = String(options.model);
      return apiChat(payload).then(function (j) {
        checkCancelled(task);
        var text = String(j.content || '');
        if (!text.trim()) throw new AgiError('DIAGNOSIS_FAILED', '诊断返回为空', task.id);
        var r = parseResult(text);
        taskEvent(task, 'task-diagnosis-done', { rootCause: (r.root || '').slice(0, 120) });
        auditLog(task, 'diagnosis-done', (r.root || '').slice(0, 80));
        setStatus(task, STATUS.FIXING, '修复代码生成', 85);
        taskEvent(task, 'task-fix-done', { lang: r.lang, codeChars: (r.code || '').length });
        log('[AGI ' + task.id + '] 诊断完成，修复代码 ' + (r.code || '').length + ' 字符（' + (r.lang || '?') + '）', 'info');
        return r;
      });
    }

    /* ---------------- 统一结果对象（任务书 3.3 结构；字段全部来自既有解析/分级产物） ---------------- */
    function buildAnalyzeResult(task) {
      var last = task.rounds[task.rounds.length - 1];
      var r = last.result;
      var ce = classifyError(task.imageText);
      var f = task.features || extractFeatures(task.imageText);
      return {
        taskId: task.id,
        roundId: task.id + ':r' + last.n,
        status: STATUS.COMPLETED,
        imageText: task.imageText,
        features: {
          errorType: f.type || null,
          filePath: (f.paths && f.paths.length) ? f.paths[0] : null,
          lineNumber: (f.lines && f.lines.length) ? f.lines[0] : null,
          errorMessage: (ce.blockLines && ce.blockLines.length) ? ce.blockLines[0] : null,
          language: (f.lang && f.lang !== 'plaintext') ? (LANG_LABEL[f.lang] || f.lang) : (f.lang || null)
        },
        diagnosis: {
          rootCause: r.root || '',
          severity: ce.level, // fatal | warn | info（classifyError 分级，任务书 3.3 契约一致）
          description: r.plan || ''
        },
        fix: {
          language: r.lang || null,
          code: r.code || '',
          explanation: r.plan || ''
        },
        validation: task.validation || null,
        history: task.rounds.map(function (rr) {
          return { round: rr.n, status: rr.outcome, summary: String(rr.result.root || '').slice(0, 120) };
        })
      };
    }

    /* ============================================================
       能力 1：analyzeImage(image, options)
       职责：预处理 → OCR → 特征提取 → 诊断 → 修复生成（不含执行验证，
       与主流程一致——验证由 runValidation 显式发起）。Promise<统一结果对象>。
       ============================================================ */
    function analyzeImage(image, options) {
      options = options || {};
      var task = newTask('analyze', argsDigest(image, options));
      auditLog(task, 'accepted', task.argsSummary);
      taskEvent(task, 'task-start', { kind: 'analyze' });
      log('[AGI ' + task.id + '] analyzeImage 已受理（' + task.argsSummary + '）· AGI 调用不影响当前页面结果', 'info');
      return enqueueDiag(task, function () {
        return normalizeImage(image)
          .then(function (blob) {
            checkCancelled(task);
            return agiOcr(task, blob, options);
          })
          .then(function () {
            return agiDiagnose(task, options);
          })
          .then(function (r) {
            task.rounds.push({ n: 1, ocrText: task.imageText, result: r, outcome: 'pending', errorInfo: '' });
            task.result = buildAnalyzeResult(task);
            setStatus(task, STATUS.COMPLETED, '完成', 100);
            task.finishedAt = Date.now();
            auditLog(task, 'completed', 'rounds=1, code=' + (r.code || '').length + ' chars', 'completed');
            taskEvent(task, 'task-completed', { roundId: task.result.roundId, severity: task.result.diagnosis.severity });
            log('[AGI ' + task.id + '] 分析任务完成（severity=' + task.result.diagnosis.severity + '），可调用 runValidation 验证修复代码', 'ok');
            return task.result;
          });
      });
    }

    /* ---------------- 静态校验（降级通道；与主流程 staticVerify 同语义） ---------------- */
    function agiStaticVerify(task, code, displayLang, options) {
      var t0 = Date.now();
      var payload = {
        messages: [{
          role: 'user',
          content: VERIFY_PROMPT.replace('{{LANG}}', displayLang || 'python').replace('{{CODE}}', code)
        }],
        temperature: 0
      };
      if (options && options.model) payload.model = String(options.model);
      return apiChat(payload).then(function (j) {
        var txt = String(j.content || '');
        var ok = /VERDICT:\s*PASS/i.test(txt);
        return {
          mode: 'static', ok: ok, exitCode: null, stdout: '', stderr: '',
          durationMs: Date.now() - t0, zcodeTaskId: null,
          reason: ok ? '' : (txt.split('\n').slice(1).join(' ') || '未给出原因').trim()
        };
      });
    }

    /* ---------------- ZCode 沙箱真实执行（与手动路径同语义同参数） ----------------
       429 → BUDGET_EXCEEDED；接口不可用/引擎离线/503/网络失败 → 降级静态校验；
       取消经 AbortController 即时中断。 */
    function agiZcodeRun(task, code, runLang, options) {
      task._stage = 'validate';
      setStatus(task, STATUS.VALIDATING, 'ZCode 沙箱执行', 40);
      progress(task, '验证', 40, 'POST /api/engines/zcode/run（沙箱真实执行）');
      task._abort = new AbortController();
      var timer = setTimeout(function () { try { task._abort.abort(); } catch (e) { /* 已结束 */ } }, 45000);
      return fetch(API_ZCODE_RUN, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: task._abort.signal,
        body: JSON.stringify({ code: code, language: runLang, timeout: 15 }) // 与手动路径同参
      }).then(function (res) {
        clearTimeout(timer);
        return res.json().catch(function () { return {}; }).then(function (j) { return { res: res, j: j }; });
      }).then(function (d) {
        checkCancelled(task);
        if (d.res.status === 429 || (d.j && d.j.error === 'session_budget_exceeded')) {
          throw new AgiError('BUDGET_EXCEEDED',
            (d.j && (d.j.message || d.j.msg)) || 'ZCode 执行次数已达今日上限（30 次）', task.id);
        }
        if (!d.res.ok || !d.j || d.j.ok !== true) return 'FALLBACK'; // 降级触发条件与手动路径一致
        var z = d.j.data || {};
        return {
          mode: 'zcode', ok: z.status === 'completed',
          exitCode: (z.exit_code === undefined ? null : z.exit_code),
          stdout: z.stdout || '', stderr: z.stderr || '',
          durationMs: z.duration_ms || 0, zcodeTaskId: z.task_id || null,
          reason: z.error === 'timeout' ? '执行超时（>15s 强制终止）'
            : (z.status === 'completed' ? '' : (String(z.stderr || '').split('\n')[0] || ('exit_code_' + z.exit_code)))
        };
      }, function (err) {
        clearTimeout(timer);
        if (task.cancelRequested) throw new AgiError('TASK_CANCELLED', '任务已被取消', task.id);
        if (err && err.name === 'AbortError') throw new AgiError('TIMEOUT', 'ZCode 执行超时（45s 未返回）', task.id);
        return 'FALLBACK'; // 网络失败 → 降级（与手动路径一致）
      }).then(function (out) {
        if (out !== 'FALLBACK') return out;
        progress(task, '静态校验（降级）', 60, 'ZCode 引擎暂不可用，已降级为静态校验');
        log('[AGI ' + task.id + '] ZCode 引擎暂不可用，已降级为静态校验', 'info');
        return agiStaticVerify(task, code, options && options._displayLang, options);
      });
    }

    /* ============================================================
       能力 2：runValidation(code, options)
       职责：修复代码 → ZCode 沙箱执行（不可用自动降级静态校验）→ 标准化结果。
       options.taskId 绑定已完成的 analyze/continue 任务时，validation 同步回填其
       result.validation（任务书 5.2「执行验证与任务上下文绑定」）。
       ============================================================ */
    function runValidation(code, options) {
      options = options || {};
      if (typeof code !== 'string' || !code.trim()) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', 'code 必须为非空字符串'));
      }
      var langRaw = options.language || detectLang(code);
      var runLang = mapRunLanguage(langRaw); // 仅 python/javascript 可真实执行；其余走静态校验（与手动语义一致）
      var task = newTask('validate', 'code ' + code.length + ' chars, lang=' + langRaw +
        (runLang ? '' : ' → 静态校验') + (options.taskId ? ', bound=' + options.taskId : ''));
      auditLog(task, 'accepted', task.argsSummary);
      taskEvent(task, 'task-start', { kind: 'validate' });
      log('[AGI ' + task.id + '] runValidation 已受理（' + task.argsSummary + '）', 'info');
      return enqueueVal(task, function () {
        checkCancelled(task);
        var work = runLang
          ? agiZcodeRun(task, code, runLang, options)
          : agiStaticVerify(task, code, (LANG_LABEL[langRaw] || langRaw), options);
        return work.then(function (v) {
          checkCancelled(task);
          task.validation = v;
          taskEvent(task, 'task-validation-done', { mode: v.mode, ok: v.ok, exitCode: v.exitCode });
          var bound = options.taskId && tasks[options.taskId];
          if (bound && (bound.kind === 'analyze' || bound.kind === 'continue') && bound.result) {
            bound.validation = v;
            bound.result.validation = v; // 绑定回填（任务书 5.2）
            auditLog(task, 'bound', 'validation 回填至 ' + bound.id);
          }
          task.result = {
            taskId: task.id, roundId: null, status: STATUS.COMPLETED,
            imageText: '', features: null, diagnosis: null, fix: null,
            validation: v, history: []
          };
          setStatus(task, STATUS.COMPLETED, '完成', 100);
          task.finishedAt = Date.now();
          auditLog(task, 'completed', 'mode=' + v.mode + ' ok=' + v.ok, 'completed');
          log('[AGI ' + task.id + '] 验证完成（' + (v.mode === 'zcode' ? 'ZCode 真实执行' : '静态校验') + '，' +
            (v.ok ? '通过' : '未通过：' + String(v.reason).slice(0, 60)) + '）', v.ok ? 'ok' : 'fail');
          return task.result;
        });
      });
    }

    /* ============================================================
       能力 3：continueFix(taskId, newError, options)
       职责：基于该任务的轮次历史 + 新报错信息继续迭代修复（复用
       buildRoundPrompt，MAX_ROUNDS=3 与主流程同限）。taskId 不变，roundId 递增。
       ============================================================ */
    function continueFix(taskId, newError, options) {
      options = options || {};
      var task = tasks[taskId];
      if (!task) {
        return Promise.reject(new AgiError('TASK_NOT_FOUND', '任务不存在：' + taskId, taskId));
      }
      if (task.kind === 'validate') {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', '验证任务无修复轮次上下文，不可 continueFix', taskId));
      }
      if (!isTerminal(task.status)) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', '任务仍在进行中（' + task.status + '），完成后方可续修', taskId));
      }
      if (task.status === STATUS.CANCELLED) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', '已取消的任务不可续修，请重新 analyzeImage', taskId));
      }
      if (typeof newError !== 'string' || !newError.trim()) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', 'newError 必须为非空字符串（上一轮执行报错信息）', taskId));
      }
      if (task.rounds.length >= MAX_ROUNDS) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', '已达 ' + MAX_ROUNDS + ' 轮迭代上限，请人工排查', taskId));
      }
      var nextN = task.rounds.length + 1;
      task.status = STATUS.PENDING;
      task.error = null; task.finishedAt = null;
      task.startedAt = null; // [P3] 续修轮次重置计时（getTaskStatus elapsedMs 如实反映本轮）
      task._started = false; task._stage = ''; task._abort = null;
      task.cancelRequested = false;
      auditLog(task, 'accepted', 'continue round=' + nextN + ', newError ' + newError.length + ' chars');
      taskEvent(task, 'task-start', { kind: 'continue', round: nextN });
      log('[AGI ' + task.id + '] continueFix 已受理（第 ' + nextN + ' 轮，新报错 ' + newError.length + ' 字符）', 'info');
      return enqueueDiag(task, function () {
        checkCancelled(task);
        task._stage = 'diag';
        setStatus(task, STATUS.DIAGNOSING, '第' + nextN + '轮诊断', 60);
        progress(task, '多轮诊断', 60, '第 ' + nextN + ' 轮（携带 ' + task.rounds.length + ' 轮历史上下文）');
        var prompt = buildRoundPrompt(task.rounds, newError);
        var payload = { messages: [{ role: 'user', content: prompt }], temperature: 0.2 };
        if (options.model) payload.model = String(options.model);
        return apiChat(payload).then(function (j) {
          checkCancelled(task);
          var text = String(j.content || '');
          if (!text.trim()) throw new AgiError('DIAGNOSIS_FAILED', '诊断返回为空', task.id);
          var r = parseResult(text);
          task.rounds.push({
            n: nextN, ocrText: task.rounds[0] ? task.rounds[0].ocrText : '',
            result: r, outcome: 'pending', errorInfo: String(newError).slice(0, 2000)
          });
          taskEvent(task, 'task-diagnosis-done', { round: nextN, rootCause: (r.root || '').slice(0, 120) });
          setStatus(task, STATUS.FIXING, '修复代码生成', 85);
          taskEvent(task, 'task-fix-done', { round: nextN, lang: r.lang, codeChars: (r.code || '').length });
          task.result = buildAnalyzeResult(task);
          setStatus(task, STATUS.COMPLETED, '完成', 100);
          task.finishedAt = Date.now();
          auditLog(task, 'completed', 'round=' + nextN + '/' + MAX_ROUNDS, 'completed');
          taskEvent(task, 'task-completed', { roundId: task.result.roundId });
          log('[AGI ' + task.id + '] 第 ' + nextN + ' 轮修复完成，可再次 runValidation 验证', 'ok');
          return task.result;
        });
      });
    }

    /* ---------------- 任务快照与查询（getTaskStatus / getTask / listTasks / clearTask） ---------------- */
    function taskSnapshot(task, full) {
      var last = task.rounds[task.rounds.length - 1] || null;
      var snap = {
        taskId: task.id, kind: task.kind, source: task.source,
        status: task.status, stage: task.stage, progress: task.progress,
        createdAt: task.createdAt, startedAt: task.startedAt, finishedAt: task.finishedAt,
        elapsedMs: (task.finishedAt || Date.now()) - (task.startedAt || task.createdAt),
        error: task.error, cancelRequested: task.cancelRequested,
        argsSummary: task.argsSummary,
        roundCount: task.rounds.length,
        roundId: last ? (task.id + ':r' + last.n) : null,
        segments: task.segments || 0
      };
      if (full) {
        snap.imageText = task.imageText || '';
        snap.features = task.features;
        snap.latestRound = last ? {
          round: last.n, outcome: last.outcome,
          rootCause: String(last.result.root || '').slice(0, 200),
          codeChars: (last.result.code || '').length, lang: last.result.lang || null
        } : null;
        snap.validation = task.validation;
        snap.result = task.result; // 完成后的统一结果对象（validation 可能被后续绑定回填）
      }
      return snap;
    }
    function getTaskStatus(taskId) {
      var t = tasks[taskId];
      if (!t) return Promise.reject(new AgiError('TASK_NOT_FOUND', '任务不存在：' + taskId, taskId));
      return Promise.resolve(taskSnapshot(t, true));
    }
    function getTask(taskId) { return getTaskStatus(taskId); } // 任务书 3.2：getTask 与 getTaskStatus 同义
    function listTasks() {
      return Object.keys(tasks).map(function (id) { return taskSnapshot(tasks[id], false); });
    }
    function clearTask(taskId) {
      var t = tasks[taskId];
      if (!t) return Promise.reject(new AgiError('TASK_NOT_FOUND', '任务不存在：' + taskId, taskId));
      if (!isTerminal(t.status)) {
        return Promise.reject(new AgiError('UNSUPPORTED_OPERATION', '运行中任务不可清除（当前 ' + t.status + '），请先 cancelTask', taskId));
      }
      delete tasks[taskId];
      auditLog(t, 'cleared', '', 'cleared');
      return Promise.resolve({ taskId: taskId, cleared: true });
    }

    /* ============================================================
       能力 5：cancelTask(taskId)（能力 4 getTaskStatus 见上）
       pending 即时取消；运行中协作式取消（诊断链于阶段/分段边界检查点终止）；
       验证任务经 AbortController 即时中断。返回标准化取消结果（不抛异常）。
       ============================================================ */
    function cancelTask(taskId) {
      var t = tasks[taskId];
      if (!t) return Promise.reject(new AgiError('TASK_NOT_FOUND', '任务不存在：' + taskId, taskId));
      if (isTerminal(t.status)) {
        return Promise.resolve({
          taskId: taskId, ok: false, status: t.status,
          message: '任务已结束（' + t.status + '），无需取消'
        });
      }
      t.cancelRequested = true;
      auditLog(t, 'cancel-requested', 'status=' + t.status);
      if (t.status === STATUS.PENDING && !t._started) {
        setStatus(t, STATUS.CANCELLED, '已取消', 0);
        t.finishedAt = Date.now();
        taskEvent(t, 'task-cancelled', { reason: '任务在队列等待时被取消' });
        log('[AGI ' + taskId + '] 任务已取消（尚未开始执行）', 'info');
        return Promise.resolve({ taskId: taskId, ok: true, status: STATUS.CANCELLED, message: '已取消（任务尚未开始）' });
      }
      if (t.kind === 'validate' && t._abort) {
        try { t._abort.abort(); } catch (e) { /* 已结束 */ }
      }
      log('[AGI ' + taskId + '] 已请求取消：将在当前请求返回后的下一个检查点终止', 'info');
      return Promise.resolve({
        taskId: taskId, ok: true, status: 'cancelling',
        message: '取消请求已受理：协作式终止于下一检查点（最长等待当前单次请求超时上限）'
      });
    }

    /* ---------------- 能力元信息（任务书 4.1） ---------------- */
    var capabilities = {
      name: 'iris-error-repair',
      version: '1.0-p3',
      description: '代码报错截图识别、诊断、修复与验证能力（Iris 视觉智能体 P3 原子能力封装，纯前端复用既有网关路由）',
      abilities: ['analyzeImage', 'runValidation', 'continueFix'],
      inputSchema: {
        type: 'object',
        required: ['ability'],
        properties: {
          ability: { type: 'string', enum: ['analyzeImage', 'runValidation', 'continueFix'], description: '能力名' },
          image: { type: 'string|File|Blob', description: 'analyzeImage 必填：报错截图（png/jpg dataURL 或 File/Blob），≤5MB' },
          code: { type: 'string', description: 'runValidation 必填：待验证的修复代码' },
          taskId: { type: 'string', description: 'continueFix 必填 / runValidation 可选（绑定回填 validation）：IrisAgent 任务 ID' },
          newError: { type: 'string', description: 'continueFix 必填：上一轮执行报错信息' },
          options: {
            type: 'object',
            properties: {
              enhanceMode: { type: 'string', enum: ['auto', 'off', 'bin'], description: 'analyzeImage 预处理策略，默认 auto' },
              language: { type: 'string', enum: ['python', 'javascript'], description: 'runValidation 可选；缺省自动识别，其他语言自动降级静态校验' },
              model: { type: 'string', description: '模型覆盖；长图分段 OCR 沿用页面模型覆盖设置' }
            }
          }
        }
      },
      outputSchema: {
        type: 'object',
        properties: {
          taskId: { type: 'string', description: 'IrisAgent 任务 ID' },
          roundId: { type: 'string', description: '轮次 ID（taskId:rN）；验证任务为 null' },
          status: { type: 'string', enum: STATUS_LIST, description: '统一任务状态' },
          imageText: { type: 'string', description: 'OCR 全文（验证任务为空串）' },
          features: {
            type: 'object',
            description: '结构化特征（extractFeatures 产物映射）',
            properties: {
              errorType: { type: 'string|null' }, filePath: { type: 'string|null' },
              lineNumber: { type: 'string|null' }, errorMessage: { type: 'string|null' },
              language: { type: 'string|null' }
            }
          },
          diagnosis: {
            type: 'object',
            properties: {
              rootCause: { type: 'string' },
              severity: { type: 'string', enum: ['fatal', 'warn', 'info'] },
              description: { type: 'string' }
            }
          },
          fix: {
            type: 'object',
            properties: {
              language: { type: 'string|null' }, code: { type: 'string' }, explanation: { type: 'string' }
            }
          },
          validation: {
            type: 'object|null',
            properties: {
              mode: { type: 'string', enum: ['zcode', 'static'] }, ok: { type: 'boolean' },
              exitCode: { type: 'number|null' }, stdout: { type: 'string' }, stderr: { type: 'string' },
              durationMs: { type: 'number' }, reason: { type: 'string' }, zcodeTaskId: { type: 'string|null' }
            }
          },
          history: { type: 'array', description: '轮次历史 [{round, status, summary}]' }
        }
      },
      errorCodes: ERROR_CODES,
      events: EVENTS,
      statuses: STATUS_LIST,
      notes: [
        '预算：AGI 调用与手动使用共用同一 iris:* 预算桶（LLM 30 次/天 + ZCode 30 次/天），429 → BUDGET_EXCEEDED',
        '并发：诊断类任务（analyzeImage/continueFix）全局串行；runValidation 独立串行链',
        '降级：ZCode 不可用时 runValidation 自动降级静态语法校验（validation.mode="static"）',
        '取消：pending 即时取消；运行中协作式取消（下一检查点终止）；验证任务即时中断',
        '隔离：AGI 任务不写入用户页面状态，不覆盖用户正在查看的结果'
      ]
    };

    /* ---------------- 调用示例（任务书 4.2，可整段复制给 AGI） ---------------- */
    var examples = {
      analyzeImage: [
        '// 单图分析：传入报错截图（File/Blob/dataURL），返回 Promise<统一结果对象>',
        'const res = await IrisAgent.analyzeImage(fileOrDataUrl, { enhanceMode: "auto" });',
        'console.log(res.diagnosis.severity, res.diagnosis.rootCause, res.fix.code);'
      ].join('\n'),
      runValidation: [
        '// 执行验证：ZCode 沙箱真实运行修复代码（不可用时自动降级静态校验）；taskId 绑定回填',
        'const v = await IrisAgent.runValidation(res.fix.code, { language: "python", taskId: res.taskId });',
        'console.log(v.validation.ok, v.validation.exitCode, v.validation.stdout, v.validation.stderr);'
      ].join('\n'),
      continueFix: [
        '// 多轮修复：验证失败后携带新报错继续迭代（同一 taskId，最多 3 轮）',
        'const r2 = await IrisAgent.continueFix(res.taskId, v.validation.stderr);',
        'console.log(r2.history.length, r2.fix.code); // history 长度 = 已完成轮数'
      ].join('\n'),
      events: [
        '// 事件监听：订阅任务生命周期（与 Promise 模式可并用）',
        'const onProgress = (e) => console.log(e.taskId, e.progress, e.stage);',
        'IrisAgent.on("task-progress", onProgress);',
        'IrisAgent.once("task-completed", (e) => console.log("completed", e.taskId));',
        'IrisAgent.off("task-progress", onProgress); // 取消订阅',
        '// 事件全集：' + EVENTS.join(' / ')
      ].join('\n')
    };

    /* ---------------- 自检接口（任务书 4.3）：模块就绪 + 网关/引擎可达（只读，不写页面状态） ---------------- */
    function healthCheck() {
      var modules = [];
      function m(name, ok, detail) { modules.push({ name: name, ok: !!ok, detail: detail || '' }); return !!ok; }
      var domOk = m('dom',
        !!(document.getElementById('dropzone') && document.getElementById('timeline') &&
          document.getElementById('rounds-wrap') && document.getElementById('analyze-btn')),
        '关键 DOM 就绪');
      m('events', typeof on === 'function' && typeof emit === 'function', '事件总线就绪');
      m('tasks', !!tasks, '任务注册表就绪（当前 ' + Object.keys(tasks).length + ' 个任务）');
      m('preprocess',
        typeof decodeFileToCanvas === 'function' && typeof runPreprocessPipeline === 'function' &&
        typeof buildSegments === 'function', '预处理/分段管线可复用');
      m('llm', typeof apiChat === 'function' && typeof API_CHAT === 'string', 'LLM 通道（/api/llm/chat）可复用');
      m('validate', typeof API_ZCODE_RUN === 'string' && typeof mapRunLanguage === 'function', '执行验证通道可复用');
      m('hljs', !!window.hljs, window.hljs ? 'highlight.js 已加载' : 'highlight.js 未加载（仅影响高亮，不影响能力）');
      m('bridge', !!window.__iris, '__iris 自动化钩子在线');
      var gwOk = false;
      var zcodeState = 'unknown';
      var gwCtrl = new AbortController();
      var gwTimer = setTimeout(function () { try { gwCtrl.abort(); } catch (e) { /* 已结束 */ } }, 4000);
      return fetch(GATEWAY + '/api/health', { cache: 'no-store', signal: gwCtrl.signal })
        .then(function (r) { clearTimeout(gwTimer); return r.json(); })
        .then(function (j) { gwOk = !!(j && j.ok); })
        .catch(function () { clearTimeout(gwTimer); gwOk = false; })
        .then(function () {
          return apiEngines().then(function (engines) {
            var z = null;
            for (var i = 0; i < engines.length; i++) if (engines[i] && engines[i].name === 'zcode') z = engines[i];
            zcodeState = z ? ((z.status === 'offline' || z.available === false) ? 'offline' : (z.status || 'ok')) : 'missing';
          }).catch(function () { zcodeState = 'unreachable'; });
        })
        .then(function () {
          m('gateway', gwOk, gwOk ? 'MoRay 网关在线' : 'MoRay 网关不可达');
          return {
            ok: domOk && gwOk,
            gateway: gwOk,
            zcode: zcodeState,
            modules: modules,
            sessionBucket: sessionId(), // 预算桶标识（AGI 与手动共用，30 次/天）
            ts: Date.now(),
            version: '1.0-p3'
          };
        });
    }

    /* ---------------- 审计日志读取（任务书 5.1，只读，供 AGI 排错） ---------------- */
    function getAuditLog(limit) {
      var n = Math.max(1, Math.min(Number(limit) || 50, AUDIT_CAP));
      return audit.slice(-n).map(function (x) { return Object.assign({}, x); });
    }

    return {
      version: '1.0-p3',
      capabilities: capabilities,
      examples: examples,
      STATUS: STATUS,               // 统一任务状态枚举（程序化读取）
      EVENTS: EVENTS,               // 统一事件名清单
      ERROR_CODES: ERROR_CODES,     // 统一错误码清单
      analyzeImage: analyzeImage,
      runValidation: runValidation,
      continueFix: continueFix,
      getTaskStatus: getTaskStatus,
      cancelTask: cancelTask,
      createTaskId: createTaskId,
      getTask: getTask,
      listTasks: listTasks,
      clearTask: clearTask,
      on: on, off: off, once: once,
      healthCheck: healthCheck,
      getAuditLog: getAuditLog
    };
  })();

  // [P3] 标准全局命名空间 + __iris 键只增不改（P0/P1/P2 验收脚本兼容）
  window.IrisAgent = IrisAgent;
  window.__iris.agent = IrisAgent;

  /* ============================================================
     [P4] 工作台 AGI 联调增强（任务书阶段4.2 双向跳转联动）
     ------------------------------------------------------------
     职责：
       1. ?taskId=<id> 恢复：读取主对话 87 分片写入的 localStorage 快照
         （iris.agiTask.<taskId>），构造只读 viewing 记录复用既有结果区
         渲染（renderResult/buildRoundGroup 零改动）；
       2. 「发送到对话」：当前结果摘要 → localStorage['iris.toChat.draft']
         → 跳转主对话（消费端 87 分片填入输入框草稿，不自动发送）。
     范式：IIFE 内部末尾追加（P3 IrisAgent 同款），既有代码 0 行修改；
     按钮显隐经 MutationObserver 跟随 report-actions（updateActions 零改动）。
     ============================================================ */
  (function () {
    var SEND_DRAFT_KEY = 'iris.toChat.draft';      // 与主对话消费端同键
    var SNAP_KEY = 'iris.agiTask.';                 // 与主对话写入端同键前缀

    function el2(id) { return document.getElementById(id); }

    /* ---- 1) 发送到对话（有结果即可用；发送内容=根因+代码+验证摘要） ---- */
    function buildChatDraft() {
      var cur = currentRound();
      if (!cur || !cur.result || !cur.result.code) return null;
      var lines = [];
      lines.push('请继续基于这条 Iris 修复结果分析：');
      if (cur.result.root) lines.push('根因：' + String(cur.result.root).slice(0, 300));
      if (cur.result.plan) lines.push('方案：' + String(cur.result.plan).slice(0, 200));
      lines.push('修复代码（' + (cur.result.lang || '未知语言') + '）：');
      lines.push('```' + (cur.result.lang || '').toLowerCase() + '\n' + String(cur.result.code).slice(0, 1600) + '\n```');
      if (cur.run) {
        var okTxt = cur.outcome === 'ok' || cur.outcome === 'static-ok';
        lines.push('验证：' + (okTxt ? '通过' : '未通过') + '（' + (cur.run.mode === 'zcode' ? '沙箱真实执行' : '静态校验') + '）');
        var errTxt = cur.run.stderr || cur.run.reason || '';
        if (errTxt) lines.push('stderr：' + String(errTxt).slice(0, 300));
      }
      lines.push('（来自 Iris 工作台「发送到对话」）');
      return lines.join('\n');
    }
    function sendToChat() {
      var text = buildChatDraft();
      if (!text) { toast('暂无可发送的修复结果'); return; }
      var draft = { text: text, ts: Date.now(), from: 'iris-workbench' };
      try {
        localStorage.setItem(SEND_DRAFT_KEY, JSON.stringify(draft));
      } catch (e) { toast('写入失败：浏览器存储不可用'); return; }
      log('已写入「发送到对话」草稿，正在返回主对话…（主对话输入框将填入摘要，确认后发送）', 'ok');
      toast('已就绪，返回主对话');
      var target = (location.protocol === 'file:') ? MORAY_HOME : '/';
      setTimeout(function () { location.href = target; }, 600);
    }

    /* ---- 2) ?taskId 恢复（只读 viewing，复用结果区渲染） ---- */
    function restoreFromAgiSnapshot() {
      var tid = new URLSearchParams(location.search).get('taskId');
      if (!tid) return false;
      var snap = null;
      try { snap = JSON.parse(localStorage.getItem(SNAP_KEY + tid) || 'null'); } catch (e) { snap = null; }
      if (!snap || (!snap.code && !snap.rootCause)) {
        log('未找到 AGI 任务快照（' + tid + '）：快照仅存本机浏览器且 12 条滚动清理，或主对话与工作台非同一浏览器档案', 'fail');
        toast('快照不可用，可回到对话重新分析');
        return false;
      }
      var rec = {
        n: 1,
        ts: new Date((snap._ts || Date.now())).toTimeString().slice(0, 8),
        src: 'AGI 任务恢复（' + tid + '）',
        ocrText: String(snap.imageTextHead || ''),
        errorInfo: '',
        result: {
          root: String(snap.rootCause || ''),
          plan: String(snap.description || snap.explanation || ''),
          code: String(snap.code || ''),
          lang: String(snap.language || '')
        },
        run: null,
        outcome: 'pending',
        verifyFailed: null
      };
      if (snap.validation) {
        var v = snap.validation;
        rec.run = {
          mode: v.mode === 'zcode' ? 'zcode' : 'static',
          stdout: String(v.stdout || ''), stderr: String(v.stderr || ''),
          exit_code: (v.exitCode === undefined ? null : v.exitCode),
          duration_ms: v.durationMs || 0,
          reason: String(v.reason || '')
        };
        rec.outcome = v.ok ? (v.mode === 'zcode' ? 'ok' : 'static-ok')
                           : (v.mode === 'zcode' ? 'fail' : 'static-fail');
      }
      // 恢复策略：工作台空白 → 恢复为可编辑轮次（可直接「执行验证」/「继续修复」）；
      // 已有内容 → 只读回看（不覆盖用户正在处理的分析，任务书 4.2 只读语义兜底）
      if (!state.rounds.length) {
        state.rounds = [rec];
        state.roundIdx = 1;
        state.result = rec.result;   // 兼容既有引用（copy/staticVerify 等）
        state.ocrText = rec.ocrText;
        state.viewing = null;
        state.historyId = null;
        try { renderResult(); } catch (e2) { /* 渲染异常不阻断 */ }
        log('已从主对话恢复 AGI 任务（' + tid + '）到工作台，可直接「执行验证（ZCode 沙箱）」或「继续修复」', 'ok');
        toast('已恢复任务，可直接执行验证');
      } else {
        state.viewing = rec;
        try { renderResult(); } catch (e3) { /* 渲染异常不阻断 */ }
        log('工作台已有分析内容，AGI 任务（' + tid + '）以只读方式回看；如需继续操作请先清空重开', 'info');
        toast('已只读恢复（工作台已有内容）');
      }
      var hist = (snap.history || []);
      if (hist.length > 1) log('该任务共 ' + hist.length + ' 轮迭代，此处展示最终轮结果', 'info');
      // ?focus=verify：滚动并高亮「执行验证」按钮（主对话卡片「去工作台验证」入口）
      if (new URLSearchParams(location.search).get('focus') === 'verify') {
        setTimeout(function () {
          var btn = el2('sync-zcode-btn');
          if (btn) {
            try { btn.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e4) { btn.scrollIntoView(); }
            btn.classList.add('p4-focus-pulse');
            setTimeout(function () { btn.classList.remove('p4-focus-pulse'); }, 2600);
          }
        }, 400);
      }
      return true;
    }

    /* ---- 3) 装配（DOM 已就绪：脚本位于 body 尾部） ---- */
    var sendBtn = el2('send-to-chat-btn');
    var restored = false;
    try { restored = restoreFromAgiSnapshot(); } catch (e) { restored = false; }
    if (sendBtn) {
      sendBtn.addEventListener('click', sendToChat);
      // 显隐跟随操作区（updateActions 为既有函数，零改动；观察 hidden 属性变化）
      var actions = el2('report-actions');
      if (actions && typeof MutationObserver === 'function') {
        var sync = function () { sendBtn.hidden = actions.hidden; };
        new MutationObserver(sync).observe(actions, { attributes: true, attributeFilter: ['hidden'] });
        sync();
      } else if (actions) {
        setInterval(function () { sendBtn.hidden = actions.hidden; }, 800); // 旧浏览器兜底
      }
    }
  })();

  /* ============================================================
     [P5] 智能排错 Agent 与修复质量升级（任务书「多轮智能排查 + 方案分级择优 + 修复风险自检」）
     ------------------------------------------------------------
     模块（本段全部为 IIFE 尾部纯追加，既有代码 0 行修改；范式同 P3 IrisAgent / P4 联调段）：
       1. IrisRisk —— 纯前端静态风险自检（阶段4）：语法合法性 / 变量完整性 / 边界条件 /
          硬编码 / 类型匹配 五类检测 + 低/中/高风险评分。零模型调用、零后端依赖，
          纯 JS 词法扫描（字符串/注释感知）+ JS 端 new Function 编译试探（非 AST，如实标注启发式）。
       2. IrisDebug —— 智能排查引擎（阶段2）：私有状态机
          reproducing→enumerating→verifying→concluding / failed / completed，
          排查迭代 ≤5 轮；错误复现→三层根因枚举（直接/间接/根本）→候选假设逐一
          沙箱验证→失败证据回灌续跑→结论收敛；支持暂停/恢复/终止（LLM 与沙箱请求
          经 AbortController 即时中断）；预算上限 12 次 LLM + 6 次沙箱执行（超限如实收敛）。
       3. IrisPlans —— 三档分级方案（阶段3）：快速/规范/兼容方案生成、Tab 切换、
          方案间 LCS Diff（复用既有 diffLines）、择优推荐（默认规范修复；错误类型与
          风险等级调整优先级）、高风险二次确认；每方案独立「执行验证/复制/发送到对话/风险自检」。
     红线符合性（docs/p5_audit.md §3）：
       - 零新增后端：全部经既有 /api/llm/chat、/api/engines/zcode/run（载荷 {code,language,timeout:15}
         + 45s 传输余量 + 429/降级语义与手动路径逐字一致）；
       - 预算桶复用：p5Chat 注入 sessionId()（iris:* 同桶），无新配额通道；
       - 不破坏既有：排查/方案状态挂独立 dbg 对象，不写 state.rounds/result/ocrText
         （「切换为手动修复」是唯一例外：按任务书阶段2要求显式落一轮 pushRound）；
         单次修复、批量、AGI 工具链（IrisAgent 契约）零触碰；互斥复用 state.running/runBusy/batch.running 既有闸；
       - P3 事件/状态/错误码契约（EVENTS/STATUS/ERROR_CODES）不增删，排查进度经私有总线 p5Emit。
     自动化钩子：window.__iris.p5 = { debug, plans, risk, prompts, chat, validate, dbg, standalone, renderRiskToPanel, state }（只增不改）。
     ============================================================ */

  /* ============================================================
     [P5-4] IrisRisk —— 修复代码静态风险自检（纯前端，零模型调用）
     输出结构：{ level:'low|medium|high', score, items:[{cat,sev,line,what,detail,fix}], checked:[cat...], note }
     依据：任务书阶段4 五类检测项；启发式扫描在注释/字符串剥离后的源码上运行，行号准确。
     ============================================================ */
  var IrisRisk = (function () {
    'use strict';

    var JS_GLOBALS = ('window document console Math JSON Object Array String Number Boolean Date RegExp Error ' +
      'Promise Symbol Map Set WeakMap WeakSet Proxy Reflect globalThis process module exports require __dirname ' +
      '__filename setTimeout clearTimeout setInterval clearInterval requestAnimationFrame cancelAnimationFrame ' +
      'fetch AbortController Blob File FileReader Image URL localStorage sessionStorage navigator history location ' +
      'alert confirm getComputedStyle addEventListener removeEventListener MutationObserver IntersectionObserver ' +
      'Uint8Array Uint8ClampedArray Int8Array Int16Array Uint16Array Uint32Array Int32Array Uint32Array Float32Array Float64Array ' +
      'ArrayBuffer DataView TextEncoder TextDecoder Intl encodeURIComponent decodeURIComponent parseInt parseFloat ' +
      'isNaN isFinite structuredClone crypto performance encodeURI unescape escape undefined null true false NaN Infinity this arguments').split(' ');

    var PY_GLOBALS = ('self cls print len range str int float bool list dict set tuple open enumerate zip map filter ' +
      'sum min max abs round type isinstance issubclass super object property staticmethod classmethod bytes ' +
      'bytearray complex frozenset slice memoryview ord chr hex oct bin divmod pow iter next any all sorted ' +
      'reversed repr hash id dir vars setattr getattr hasattr callable input format exit quit breakpoint ' +
      'globals locals help eval exec compile import __import__ name main None True False NotImplemented Ellipsis ' +
      'Exception BaseException ValueError TypeError KeyError IndexError AttributeError ImportError ModuleNotFoundError ' +
      'NameError ZeroDivisionError FileNotFoundError PermissionError StopIteration RecursionError OverflowError ' +
      'RuntimeError OSError IOError EOFError KeyboardInterrupt SystemExit Warning UserWarning DeprecationWarning ' +
      'os sys json re time math random subprocess threading multiprocessing collections itertools functools ' +
      'datetime io pathlib logging numpy pandas requests sqlite3 urllib base64 hashlib uuid copy string textwrap').split(' ');

    var JS_KW = ('var let const function return if else for while do break continue switch case default new delete ' +
      'typeof instanceof in of this that class extends super try catch finally throw await async yield static get set ' +
      'import export from as void null undefined true false').split(' ');
    var PY_KW = ('def class return if elif else for while break continue pass import from as try except finally raise ' +
      'with lambda yield global nonlocal assert del in is not and or None True False async await match case').split(' ');

    /* 词法屏蔽：注释与字符串替换为等长空格（保行号/列号），返回 {masked, strings:[{s,line}]} */
    function maskLexical(code, lang) {
      var src = String(code == null ? '' : code);
      var L = src.length;
      var out = src.split('');
      var strings = [];
      var i = 0, line = 1;
      var isJs = (lang === 'javascript' || lang === 'js');
      var isPy = (lang === 'python' || lang === 'py');
      var cmt = 'sl'; // sl = js/py；bc = c 系。行注释 // 全语言通用，# 仅 js/py，块注释 /* */ js+bc
      if (!isJs && !isPy) cmt = 'bc';
      function isHashComment() {
        return (isJs || isPy) && src[i] === '#';
      }
      while (i < L) {
        var ch = src[i], nx = src[i + 1];
        if (ch === '\n') { line++; i++; continue; }
        // 块注释 /* */（js 与 c 系均识别；python 无）
        if ((cmt === 'bc' || isJs) && ch === '/' && nx === '*') {
          var j = src.indexOf('*/', i + 2);
          if (j < 0) j = L - 2;
          for (var k = i; k < j + 2 && k < L; k++) { if (src[k] === '\n') line++; out[k] = ' '; }
          i = j + 2; continue;
        }
        // 行注释 //（全语言）或 #（js/py）
        if ((ch === '/' && nx === '/') || isHashComment()) {
          var e = i;
          while (e < L && src[e] !== '\n') { out[e] = ' '; e++; }
          i = e; continue;
        }
        // 三引号（python docstring）
        if (isPy && (ch === '"' || ch === "'") && src[i + 1] === ch && src[i + 2] === ch) {
          var q3 = ch + ch + ch;
          var e3 = src.indexOf(q3, i + 3);
          if (e3 < 0) e3 = L - 3;
          var body3 = src.slice(i + 3, e3);
          strings.push({ s: body3, line: line });
          for (var k3 = i; k3 < e3 + 3 && k3 < L; k3++) { if (src[k3] === '\n') line++; out[k3] = ' '; }
          i = e3 + 3; continue;
        }
        // 单/双/反引号字符串（python 无反引号）
        if (ch === '"' || ch === "'" || (isJs && ch === '`')) {
          var start = i, sBuf = '';
          i++;
          while (i < L) {
            var c = src[i];
            if (c === '\\' && i + 1 < L) { sBuf += c + src[i + 1]; out[i] = ' '; out[i + 1] = ' '; i += 2; continue; }
            if (c === '\n') { if (!isJs || ch !== '`') { break; } line++; sBuf += c; i++; continue; }
            if (c === ch) { break; }
            sBuf += c; i++;
          }
          strings.push({ s: sBuf, line: line });
          out[start] = ' ';
          for (var m = start + 1; m < Math.min(i, L); m++) if (src[m] !== '\n') out[m] = ' ';
          if (i < L && src[i] === ch) { out[i] = ' '; i++; }
          continue;
        }
        i++;
      }
      return { masked: out.join(''), strings: strings };
    }

    /* ① 括号/引号/注释平衡扫描（全语言；字符串感知） */
    function balanceScan(code, lang) {
      var items = [];
      var src = String(code || '');
      var M = maskLexical(src, lang);
      var masked = M.masked;
      var stack = [];
      var PAIR = { ')': '(', ']': '[', '}': '{' };
      for (var i = 0; i < masked.length; i++) {
        var ch = masked[i];
        if (ch === '(' || ch === '[' || ch === '{') stack.push({ c: ch, line: lineAt(src, i) });
        else if (ch === ')' || ch === ']' || ch === '}') {
          var top = stack.pop();
          if (!top || top.c !== PAIR[ch]) {
            items.push(mkItem('syntax', 'high', lineAt(src, i),
              '括号不匹配', '第 ' + lineAt(src, i) + ' 行出现未配对的「' + ch + '」' + (top ? '（最近未闭合为第 ' + top.line + ' 行的「' + top.c + '」）' : ''),
              '补齐或删除多余括号后重试'));
            if (!top) stack.push({ c: '?', line: lineAt(src, i) }); // 防级联误报
          }
        }
      }
      if (stack.length) {
        items.push(mkItem('syntax', 'high', stack[0].line,
          '括号未闭合', '第 ' + stack[0].line + ' 行的「' + stack[0].c + '」直到代码结尾仍未闭合', '检查该作用域是否遗漏结束括号'));
      }
      // 未闭合字符串（掩码时以行尾截断识别）
      var unq = checkUnterminatedQuotes(src, lang);
      if (unq) items.push(unq);
      // 块注释未闭合（在掩码文本上检测：字符串/行注释内的 /* 不误伤）
      if (isBracesCStyle(lang) || lang === 'javascript' || lang === 'js') {
        var bcOpen = masked.indexOf('/*');
        if (bcOpen >= 0 && masked.indexOf('*/', bcOpen + 2) < 0) {
          items.push(mkItem('syntax', 'high', lineAt(src, bcOpen), '块注释未闭合',
            '第 ' + lineAt(src, bcOpen) + ' 行的 /* 直到结尾没有 */', '补上 */ 结束注释'));
        }
      }
      return items;
    }
    function isBracesCStyle(lang) { return ['c', 'cpp', 'java', 'go'].indexOf(lang) !== -1; }
    function lineAt(src, idx) {
      var n = 1;
      for (var i = 0; i < idx && i < src.length; i++) if (src[i] === '\n') n++;
      return n;
    }
    function checkUnterminatedQuotes(src, lang) {
      var lines = src.split('\n');
      for (var li = 0; li < lines.length; li++) {
        var s = lines[li];
        if (/^\s*#/.test(s)) continue;
        var mD = (s.match(/"/g) || []).length;
        var isPy = (lang === 'python' || lang === 'py');
        if (!isPy && mD % 2 === 1 && !/`/.test(s)) return mkItem('syntax', 'high', li + 1, '疑似未闭合字符串', '第 ' + (li + 1) + ' 行双引号数量为奇数（未考虑转义）', '检查该行引号配对');
      }
      return null;
    }

    /* ① 语法合法性（JS 编译试探；不执行代码体） */
    function jsCompileCheck(code) {
      var res = { checked: true, error: null };
      try { new Function(String(code || '')); }
      catch (e) { res.error = (e && e.message) || String(e); }
      return res;
    }

    /* 变量完整性（JS/Python 声明收集 + 引用比对 + 未使用声明；其余语言跳过） */
    function undeclaredScan(code, lang, masked) {
      var items = [];
      var src = String(code || '');
      var isJs = (lang === 'javascript' || lang === 'js');
      var isPy = (lang === 'python' || lang === 'py');
      if (!isJs && !isPy) return { items: items, skipped: true };
      var declared = {};
      var m, re;
      if (isJs) {
        re = /\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\bfunction\s*([A-Za-z_$][\w$]*)?\s*\(([^)]*)\)/g;
        while ((m = re.exec(masked))) {
          if (m[1]) declared[m[1]] = 1;
          paramNames(m[2]).forEach(function (p) { declared[p] = 1; });
        }
        re = /\bclass\s+([A-Za-z_$][\w$]*)/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /(?:^|[^\w$])([A-Za-z_$][\w$]*)\s*=>/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\(\s*([A-Za-z_$][\w$,\s]*)\s*\)\s*=>/g;
        while ((m = re.exec(masked))) paramNames(m[1]).forEach(function (p) { declared[p] = 1; });
        re = /(?:const|let|var)\s*\{([^}]*)\}\s*=/g;
        while ((m = re.exec(masked))) declList(m[1]).forEach(function (p) { declared[p] = 1; });
        re = /(?:const|let|var)\s*\[([^\]]*)\]\s*=/g;
        while ((m = re.exec(masked))) declList(m[1]).forEach(function (p) { declared[p] = 1; });
        re = /import\s+(?:([A-Za-z_$][\w$]*)\s*,?\s*)?(?:\{([^}]*)\})?\s*from/g;
        while ((m = re.exec(masked))) {
          if (m[1]) declared[m[1]] = 1;
          if (m[2]) declList(m[2]).forEach(function (p) { declared[p.replace(/^\s*as\s+/, '').split(/\s+/).pop()] = 1; });
        }
      } else {
        re = /^[ \t]*([A-Za-z_][\w]*)[ \t]*(?==[^=>])/gm;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\bdef\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)/g;
        while ((m = re.exec(masked))) {
          declared[m[1]] = 1;
          pyParams(m[2]).forEach(function (p) { declared[p] = 1; });
        }
        re = /\bclass\s+([A-Za-z_][\w]*)/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\bimport\s+([A-Za-z_][\w.]*)(?:\s+as\s+([A-Za-z_][\w]*))?/g;
        while ((m = re.exec(masked))) declared[(m[2] || m[1]).split('.')[0]] = 1;
        re = /\bfrom\s+[A-Za-z_.]+\s+import\s+(?:\*|\(([^)]*)\)|([A-Za-z_][\w,\s]*))(?:\s+as\s+[A-Za-z_][\w]*)?/g;
        while ((m = re.exec(masked))) declList(m[1] || m[2] || '').forEach(function (p) { declared[p] = 1; });
        re = /\bfor\s+([A-Za-z_][\w,\s]*)\s+in\b/g;
        while ((m = re.exec(masked))) declList(m[1]).forEach(function (p) { declared[p] = 1; });
        re = /\bas\s+([A-Za-z_][\w]*)/g;
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /([A-Za-z_][\w]*)\s*=[^=]/g; // 函数体内赋值（行首匹配不到的补充）
        while ((m = re.exec(masked))) declared[m[1]] = 1;
        re = /\b(?:global|nonlocal)\s+([A-Za-z_][\w,\s]*)/g;
        while ((m = re.exec(masked))) declList(m[1]).forEach(function (p) { declared[p] = 1; });
      }
      var GLOBALS = isJs ? JS_GLOBALS : PY_GLOBALS;
      var KW = isJs ? JS_KW : PY_KW;
      var gset = {}, kset = {};
      GLOBALS.forEach(function (g) { gset[g] = 1; });
      KW.forEach(function (k) { kset[k] = 1; });
      var IDENT = isJs ? /[A-Za-z_$][\w$]*/g : /[A-Za-z_][\w]*/g;
      var seen = {};
      var mm;
      while ((mm = IDENT.exec(masked))) {
        var name = mm[0];
        var at = mm.index;
        if (name.length < 2) continue;
        // 排除：属性访问（前导 .）、对象字面量 key（后随 :）
        var before = masked.slice(Math.max(0, at - 2), at).trim();
        if (before.slice(-1) === '.') continue;
        var after = masked.slice(at + name.length);
        if (isJs && /^\s*:/.test(after) && /[{,]\s*$/.test(masked.slice(0, at))) continue;
        if (gset[name] || kset[name] || declared[name]) continue;
        if (seen[name]) continue;
        seen[name] = lineAt(src, at);
      }
      Object.keys(seen).forEach(function (name) {
        items.push(mkItem('variable', 'medium', seen[name], '未定义变量「' + name + '」',
          '引用了第 ' + seen[name] + ' 行的「' + name + '」，但在代码内未见声明（启发式，函数外部注入/内建未覆盖时可能误报）',
          '确认该变量已在别处定义，或补充声明/导入'));
      });
      // 未使用声明（仅 var/let/const 与 python 简单赋值；声明后全文引用数为 1 → 未使用）
      var countRef = function (name) {
        var c = 0, r2 = new RegExp('\\b' + escRe(name) + '\\b', 'g'), m2;
        while ((m2 = r2.exec(masked))) c++;
        return c;
      };
      var unusedRe = isJs ? /\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)/g : /^[ \t]*([A-Za-z_][\w]*)[ \t]*=[^=]/gm;
      var mu;
      while ((mu = unusedRe.exec(masked))) {
        var nm = mu[1];
        if (nm.length < 2 || nm === '_') continue;
        if (countRef(nm) <= 1) {
          items.push(mkItem('variable', 'low', lineAt(src, mu.index), '未使用变量「' + nm + '」',
            '声明后未在代码中引用（启发式）', '删除声明或补全使用'));
        }
      }
      return { items: items, skipped: false };
    }
    function paramNames(s) {
      return String(s || '').split(',').map(function (x) { return x.trim().split(/[\s=:]/)[0]; }).filter(function (x) { return /^[A-Za-z_$][\w$]*$/.test(x); });
    }
    function pyParams(s) {
      return String(s || '').split(',').map(function (x) { return x.trim().split(/[\s:=*]/)[0]; }).filter(function (x) { return /^[A-Za-z_][\w]*$/.test(x); });
    }
    function declList(s) {
      return String(s || '').split(',').map(function (x) { return x.trim().split(/\s+as\s+/).pop().trim().split(/[\s=:]/)[0]; })
        .filter(function (x) { return /^[A-Za-z_$][\w$]*$/.test(x); });
    }
    function escRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

    /* 边界条件（除零/越界/空集合/空值解引用/无基例递归） */
    function boundaryScan(code, lang, masked, strings) {
      var items = [];
      var src = String(code || '');
      var isPy = (lang === 'python' || lang === 'py');
      var m;
      // 字面量除零 / 模零
      var reDiv = /[\w)\]]\s*[\/%]\s*0(?![.\d\w])/g;
      while ((m = reDiv.exec(masked))) {
        items.push(mkItem('boundary', 'high', lineAt(src, m.index), '除/模零字面量',
          '第 ' + lineAt(src, m.index) + ' 行出现「/ 0」或「% 0」，执行将抛异常', '确认分母不为 0 或改为常量'));
      }
      // 变量作除数（中）：x / y 且 y 为标识符
      var reVarDiv = /[\w)\]]\s*\/\s*([A-Za-z_]\w*)\s*(?:$|[^\d\w.\/])/g;
      var seenVD = {};
      while ((m = reVarDiv.exec(masked))) {
        var dn = m[1];
        if (seenVD[dn]) continue;
        seenVD[dn] = lineAt(src, m.index);
        items.push(mkItem('boundary', 'medium', seenVD[dn], '变量作除数「' + dn + '」',
          '第 ' + seenVD[dn] + ' 行以变量「' + dn + '」为除数，未做 0 值保护（启发式）', '除法前判断分母是否为 0/空'));
      }
      // 空集合首元素：fn(...)[0] / [-1]
      var reIdx0 = /\)\s*\[\s*0\s*\]|\)\s*\[\s*-1\s*\]/g;
      while ((m = reIdx0.exec(masked))) {
        items.push(mkItem('boundary', 'medium', lineAt(src, m.index), '对调用结果直接取下标',
          '第 ' + lineAt(src, m.index) + ' 行对函数返回值直接取 [0]/[-1]，空集合将报错', '先判空再取元素'));
      }
      // [0] / [1] 常量下标（低，限 3 条）
      var reLit = /\b[a-zA-Z_$][\w$]*\s*\[\s*[0-1]\s*\]/g;
      var litCount = 0;
      while ((m = reLit.exec(masked)) && litCount < 3) { litCount++;
        items.push(mkItem('boundary', 'low', lineAt(src, m.index), '常量下标访问',
          '第 ' + lineAt(src, m.index) + ' 行以固定下标访问序列，长度不足将越界', '确认集合长度或使用安全访问'));
      }
      // .split(...)[n] 越界
      var reSplit = /\.split\([^)]*\)\s*\[\s*\d+\s*\]/g;
      while ((m = reSplit.exec(masked))) {
        items.push(mkItem('boundary', 'medium', lineAt(src, m.index), 'split 结果直接下标',
          '第 ' + lineAt(src, m.index) + ' 行 split 后立即取元素，缺列将越界', '先校验 split 结果长度'));
      }
      // 空值解引用：x = null/None 后 x.attr / x[ ]
      var nullPat = isPy ? /\b([A-Za-z_]\w*)\s*=\s*None\b/g : /\b([A-Za-z_$]\w*)\s*=\s*(?:null|undefined)\b/g;
      while ((m = nullPat.exec(masked))) {
        var v = m[1];
        var after = masked.slice(m.index + m[0].length);
        var use = new RegExp('\\b' + escRe(v) + '\\s*[.\\[]');
        if (use.test(after)) {
          items.push(mkItem('boundary', 'medium', lineAt(src, m.index + m[0].length + after.search(use)), '空值解引用风险',
            '变量「' + v + '」先赋空值，随后第 ' + lineAt(src, m.index) + ' 行起可能被直接取属性/下标', '使用前判空'));
        }
      }
      // 无基例递归（函数体内自调用且全函数无 if/elif/while/for/try 保护；py 体按缩进块截断）
      var fnRe = isPy ? /\bdef\s+([A-Za-z_][\w]*)\s*\(([\s\S]{0,40}?)\)\s*:\n[\t ]+([\s\S]*?)(?=\n(?![\t ])|\n\s*\n|\bdef\s|\bclass\s|$)/g :
        /\bfunction\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{([\s\S]*?)\}(?=\s*(function|$))/g;
      var fm;
      while ((fm = fnRe.exec(masked))) {
        var fname = fm[1], body = fm[3] || '';
        if (!fname || fname.length < 2) continue;
        var selfCall = new RegExp('\\b' + escRe(fname) + '\\s*\\(');
        if (selfCall.test(body) && !/\b(if|elif|while|for|try)\b/.test(body)) {
          items.push(mkItem('boundary', 'medium', lineAt(src, fm.index), '递归疑似无基例',
            '函数「' + fname + '」体内自调用且全函数无 if/while/try 等终止保护', '为递归补充终止条件或深度上限（如 if n <= 1: return 1）'));
        }
      }
      return items;
    }

    /* 硬编码风险（密钥/绝对路径/魔法值/IP）——密钥/凭证按原始行扫描（变量名+值都在行上） */
    function hardcodeScan(code, lang, masked, strings) {
      var items = [];
      var src = String(code || '');
      var secretRe = /(?:api[_-]?key|apikey|secret|passwd|password|access[_-]?key|access[_-]?token|auth[_-]?token|private[_-]?key|client[_-]?secret|token)["'\s:=]+["'][^"']{6,}["']/i;
      var tokRe = /(ghp_[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|xox[baprs]-[A-Za-z0-9-]{8,}|sk-[A-Za-z0-9_+-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{16,}|eyJ[A-Za-z0-9_+-]{16,}\.)/g;
      var rawLines = src.split('\n');
      rawLines.forEach(function (line, ln) {
        if (secretRe.test(line)) {
          items.push(mkItem('hardcode', 'high', ln + 1, '疑似硬编码凭证',
            '第 ' + (ln + 1) + ' 行含密钥/口令类赋值且值为 6+ 字符字面量（如为真实凭证必须移出代码）', '改用环境变量/配置注入（process.env.XXX / os.environ["XXX"]）'));
        }
        var tk = line.match(tokRe);
        if (tk) {
          items.push(mkItem('hardcode', 'high', ln + 1, '密钥特征串命中',
            '第 ' + (ln + 1) + ' 行含已知密钥前缀特征「' + String(tk[0]).slice(0, 12) + '…」', '立即替换为占位符并轮换该凭证'));
        }
      });
      // 绝对路径（字符串内）
      var pathRe = /^([A-Za-z]:[\\/]|[\\/](?:Users|home|var|usr|opt|root)[\\/])/i;
      strings.forEach(function (st) {
        if (pathRe.test(st.s)) {
          items.push(mkItem('hardcode', 'medium', st.line, '硬编码绝对路径',
            '第 ' + st.line + ' 行写死本机绝对路径「' + st.s.slice(0, 40) + '」，跨环境不可移植', '改用相对路径/环境变量/可配置目录'));
        }
      });
      // 非 localhost IP 字面量
      var ipRe = /["'](\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?["']/g;
      var im;
      while ((im = ipRe.exec(src))) {
        if (im[1] !== '127.0.0.1' && im[1] !== '0.0.0.0') {
          items.push(mkItem('hardcode', 'low', lineAt(src, im.index), '硬编码 IP 地址',
            '第 ' + lineAt(src, im.index) + ' 行写死 IP「' + im[1] + '」', '改用主机名/配置项/环境变量'));
        }
      }
      // 魔法大数（4 位以上非年份，出现在赋值右侧）
      var mgRe = /[=]\s*(\d{4,})\b/g;
      var mmg;
      while ((mmg = mgRe.exec(masked))) {
        var n = mmg[1];
        if (/^(19|20)\d\d$/.test(n)) continue;
        items.push(mkItem('hardcode', 'low', lineAt(src, mmg.index), '疑似魔法数字',
          '第 ' + lineAt(src, mmg.index) + ' 行直接写死「' + n + '」，语义不明', '提取为具名常量'));
      }
      return items;
    }

    /* 类型匹配启发式（字符串×数字混用 / typeof 误用 / 字面量类型矛盾 / input 混型） */
    function typeScan(code, lang, masked) {
      var items = [];
      var src = String(code || '');
      var isPy = (lang === 'python' || lang === 'py');
      var m;
      if (isPy) {
        // 字符串 + 非 str() 标识符（TypeError unsupported operand 高危，正是本次报错常见根因）
        var re1 = /["'][^"'\n]*["']\s*\+\s*(?![fF]["'])([A-Za-z_]\w*)(?!\s*[\)]?\s*$)[^\w]*/g;
        while ((m = re1.exec(masked))) {
          var t = m[1];
          if (/^(str|repr)$/.test(t)) continue;
          items.push(mkItem('type', 'medium', lineAt(src, m.index), '字符串与变量「' + t + '」直接相加',
            '第 ' + lineAt(src, m.index) + ' 行「"..." + ' + t + '」，若 ' + t + ' 为数字将抛 TypeError', '显式转换：str(' + t + ') 或使用 f-string'));
        }
        var re2 = /\b(\d+(?:\.\d+)?)\s*\+\s*["']/g;
        while ((m = re2.exec(masked))) {
          items.push(mkItem('type', 'medium', lineAt(src, m.index), '数字与字符串相加',
            '第 ' + lineAt(src, m.index) + ' 行「' + m[1] + ' + "..."」混型相加将抛 TypeError', 'str(' + m[1] + ') 后拼接'));
        }
        // input() 直接参与算术（未 int/float 包裹）
        var re3 = /input\s*\([^)]*\)\s*[+\-*\/]/g;
        while ((m = re3.exec(masked))) {
          items.push(mkItem('type', 'high', lineAt(src, m.index), 'input() 返回值直接算术',
            '第 ' + lineAt(src, m.index) + ' 行 input() 返回字符串参与算术运算，必然 TypeError', '用 int(input(...)) / float(input(...)) 包裹'));
        }
        // input() 赋值变量跨行追踪：v = input(...) 后 v 参与算术（本例常见 TypeError 根因）
        var inVars = {}, ai;
        var reIn = /([A-Za-z_]\w*)\s*=\s*input\s*\(/g;
        while ((ai = reIn.exec(masked))) inVars[ai[1]] = lineAt(src, ai.index);
        Object.keys(inVars).forEach(function (v) {
          var useRe = new RegExp('\\b' + escRe(v) + '\\b\\s*[+\\-*/]|[+\\-*/]\\s*\\b' + escRe(v) + '\\b');
          var ui = masked.search(useRe);
          if (ui >= 0) {
            items.push(mkItem('type', 'high', lineAt(src, ui), 'input() 结果「' + v + '」参与算术',
              '第 ' + inVars[v] + ' 行 ' + v + ' 来自 input()（字符串），第 ' + lineAt(src, ui) + ' 行直接算术将抛 TypeError', 'int(' + v + ') / float(' + v + ') 转换后再运算'));
          }
        });
      } else if (lang === 'javascript' || lang === 'js') {
        // typeof 比较缺引号
        var reT = /\btypeof\s+\w+\s*[=!]==?\s*(string|number|boolean|object|undefined|function)\b/g;
        while ((m = reT.exec(masked))) {
          items.push(mkItem('type', 'medium', lineAt(src, m.index), 'typeof 比较缺引号',
            '第 ' + lineAt(src, m.index) + ' 行 typeof 与裸标识符比较（应为字符串字面量）', 'typeof x === "' + m[1] + '"'));
        }
        // 数字字面量与字符串字面量比较（==/===）
        var reC = /(\w+)\s*[=!]==?\s*(["']\w+["']|\d+(?:\.\d+)?)/g;
        var seen = {};
        while ((m = reC.exec(masked))) {
          var v = m[1], lit = m[2];
          var assignedNum = new RegExp('\\b(?:var|let|const)\\s+' + escRe(v) + '\\s*=\\s*\\d', '');
          var assignedStr = new RegExp('\\b(?:var|let|const)\\s+' + escRe(v) + '\\s*=\\s*["\']', '');
          if (assignedStr.test(masked) && /^\d/.test(lit) && !seen[v + ':n']) {
            seen[v + ':n'] = 1;
            items.push(mkItem('type', 'medium', lineAt(src, m.index), '字符串变量与数字比较「' + v + '」',
              '第 ' + lineAt(src, m.index) + ' 行：' + v + ' 声明为字符串但此处与数字字面量比较', '先 parseInt/Number 转换再比较'));
          } else if (assignedNum.test(masked) && /^["']/.test(lit) && !seen[v + ':s']) {
            seen[v + ':s'] = 1;
            items.push(mkItem('type', 'medium', lineAt(src, m.index), '数字变量与字符串比较「' + v + '」',
              '第 ' + lineAt(src, m.index) + ' 行：' + v + ' 声明为数字但此处与字符串比较', '比较前统一类型或 String(' + v + ')'));
          }
        }
      }
      return items;
    }

    function mkItem(cat, sev, line, what, detail, fix) {
      return { cat: cat, sev: sev, line: line, what: what, detail: detail, fix: fix };
    }
    var CAT_LABEL = { syntax: '语法合法性', variable: '变量完整性', boundary: '边界条件', hardcode: '硬编码风险', type: '类型匹配' };

    /* 总入口：五类检测 + 评分（low/medium/high）+ 逐条整改建议 */
    function scan(code, lang) {
      lang = String(lang || detectLang(code || '') || 'plaintext').toLowerCase();
      var out = { level: 'low', score: 0, items: [], checked: [], note: '', heuristic: true, lang: lang };
      var src = String(code || '');
      if (!src.trim()) { out.note = '无代码可检'; return out; }
      var M = maskLexical(src, lang);
      var push = function (arr) { (arr || []).forEach(function (x) { out.items.push(x); }); };
      push(balanceScan(src, lang)); out.checked.push('syntax');
      var compileChecked = false;
      if (lang === 'javascript' || lang === 'js') {
        var cc = jsCompileCheck(src);
        if (cc.error) out.items.push(mkItem('syntax', 'high', 0, 'JS 编译失败', 'new Function 构造期语法错误：' + cc.error, '按报错信息修复语法后重试'));
        compileChecked = true;
      }
      var vres = undeclaredScan(src, lang, M.masked);
      if (vres.skipped) out.checked.push('variable-skip');
      else { push(vres.items); out.checked.push('variable'); }
      push(boundaryScan(src, lang, M.masked, M.strings)); out.checked.push('boundary');
      push(hardcodeScan(src, lang, M.masked, M.strings)); out.checked.push('hardcode');
      push(typeScan(src, lang, M.masked)); out.checked.push('type');
      // 去重（cat+line+what）与上限 40
      var uniq = {}, items = [];
      for (var i = 0; i < out.items.length; i++) {
        var it = out.items[i];
        var key = it.cat + '|' + it.line + '|' + it.what;
        if (uniq[key]) continue;
        uniq[key] = 1; items.push(it);
        if (items.length >= 40) break;
      }
      out.items = items;
      var hi = 0, me = 0, lo = 0;
      items.forEach(function (it) { if (it.sev === 'high') hi++; else if (it.sev === 'medium') me++; else lo++; });
      out.score = hi * 10 + me * 4 + lo;
      out.level = hi > 0 ? 'high' : (me >= 2 || out.score >= 6 ? 'medium' : 'low');
      out.summary = '高 ' + hi + ' / 中 ' + me + ' / 低 ' + lo + '（纯前端启发式' + (compileChecked ? ' + JS 编译试探' : '，非 AST 解析') + '）';
      return out;
    }

    return {
      scan: scan, maskLexical: maskLexical, balanceScan: balanceScan, jsCompileCheck: jsCompileCheck,
      undeclaredScan: undeclaredScan, boundaryScan: boundaryScan, hardcodeScan: hardcodeScan, typeScan: typeScan,
      CAT_LABEL: CAT_LABEL, mkItem: mkItem
    };
  })();

  /* ============================================================
     [P5 公共件] DOM 小工具 / 带预算与中止的 LLM 通道 / 沙箱执行通道
     ============================================================ */
  function p5El(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text); // textContent：零注入
    return n;
  }
  function p5Btn(cls, text, fn, title) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = cls; b.textContent = text;
    if (title) b.title = title;
    b.addEventListener('click', fn);
    return b;
  }
  /* p5Fences：按围栏顺序提取代码块（lang + body），返回 [{lang, code, at}] */
  function p5Fences(text) {
    var re = /```([A-Za-z0-9_+\-]*)[ \t]*\n([\s\S]*?)```/g, m, out = [];
    while ((m = re.exec(String(text || '')))) out.push({ lang: (m[1] || '').toLowerCase(), code: m[2].replace(/\s+$/, ''), at: m.index });
    return out;
  }

  /* P5 私有错误（区分预算/中止/网络，供状态机如实归一展示） */
  function P5Error(kind, message) {
    this.name = 'P5Error'; this.kind = kind; this.message = message || kind;
  }
  P5Error.prototype = Object.create(Error.prototype);

  var DEBUG_MAX_ROUNDS = 5;   // 排查迭代轮次上限（任务书阶段2：最多 5 轮；与修复轮次 MAX_ROUNDS=3 语义分离）
  var DEBUG_LLM_CAP = 12;     // 单次排查 LLM 预算硬上限（预算保护，超限如实收敛为部分结论）
  var DEBUG_RUN_CAP = 6;      // 单次排查沙箱执行预算上限
  var DEBUG_TOTAL_MS = 1800000; // 单次排查总超时 30 分钟（300s×12 请求的最坏情况上界取整）

  /* 排查引擎私有状态（不污染既有 state.rounds/result/ocrText；挂 state._debug 供自动化只读） */
  var dbg = {
    active: false, phase: 'idle', round: 0, paused: false, abortReq: false,
    llmUsed: 0, runUsed: 0, startedAt: 0, deadline: 0,
    evidence: '', features: null, causes: null, hypothesisQueue: [],
    history: [],           // 每轮 [{n, ts, causes, hypotheses:[{text,status,planSet,validation,err}], note}]
    planSet: null, bestHypo: null, endReason: '', ctrl: null, sess: null, signal: null, resumeWaits: []
  };
  state._debug = dbg;

  /* p5Chat：apiChat 的「带外部中止信号 + 私有预算计数」镜像（载荷/熔断/超时语义逐字一致，红线6 同桶） */
  function p5Chat(payload, opts) {
    opts = opts || {};
    if (dbg.abortReq) return Promise.reject(new P5Error('aborted', '排查已终止'));
    if (dbg.llmUsed >= DEBUG_LLM_CAP) return Promise.reject(new P5Error('budget-cap', '排查模型调用预算已达上限（' + DEBUG_LLM_CAP + ' 次）'));
    dbg.llmUsed++;
    payload.session_id = sessionId();          // 同桶注入（iris.js:303 语义）
    payload.stream = false;
    var ctrl = new AbortController();
    dbg.ctrl = ctrl;
    var timer = setTimeout(function () { try { ctrl.abort(); } catch (e) { /* noop */ } }, LLM_TIMEOUT_MS);
    var onAbort = function () { try { ctrl.abort(); } catch (e) { /* noop */ } };
    if (opts.signal) {
      if (opts.signal.aborted) { clearTimeout(timer); dbg.ctrl = null; return Promise.reject(new P5Error('aborted', '排查已终止')); }
      opts.signal.addEventListener('abort', onAbort);
    }
    function settle() {
      clearTimeout(timer);
      if (opts.signal) { try { opts.signal.removeEventListener('abort', onAbort); } catch (e) { /* noop */ } }
      dbg.ctrl = null;
    }
    return fetch(API_CHAT, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) {
        settle();
        if (res.status === 429 || (j && j.error === 'session_budget_exceeded')) throw new BudgetError((j && (j.message || j.msg)) || '本次会话已达调用次数上限');
        if (!res.ok || !j || j.ok !== true) {
          if (opts.signal && opts.signal.aborted) throw new P5Error('aborted', '排查已终止');
          throw new Error((j && (j.message || j.msg || j.error)) || ('HTTP ' + res.status));
        }
        return j;
      });
    }, function (err) {
      settle();
      if (opts.signal && opts.signal.aborted) throw new P5Error('aborted', '排查已终止');
      if (dbg.abortReq) throw new P5Error('aborted', '排查已终止');
      if (err && err.name === 'AbortError') throw new P5Error('timeout', '请求超时（' + Math.round(LLM_TIMEOUT_MS / 1000) + 's）');
      throw new P5Error('network', '无法连接 MoRay 网关（' + ((err && err.message) || '网络错误') + '）');
    });
  }

  /* p5Validate：修复代码 → ZCode 沙箱执行（载荷/超时/429/降级与手动路径 :2252-2326 同语义）→ 标准 validation 对象 */
  function p5Validate(code, lang, opts) {
    opts = opts || {};
    lang = String(lang || detectLang(code || '')).toLowerCase();
    var runLang = mapRunLanguage(lang);
    if (!runLang) {
      // 不可执行语言 → 静态校验（降级通道保留：P0 任务书 3.3 同语义）
      return p5StaticVerify(code, lang, opts.signal);
    }
    if (opts.debug) {
      if (dbg.runUsed >= DEBUG_RUN_CAP) return Promise.reject(new P5Error('budget-cap', '排查沙箱执行预算已达上限（' + DEBUG_RUN_CAP + ' 次）'));
      dbg.runUsed++;
    }
    var ctrl = new AbortController();
    var onExt = function () { try { ctrl.abort(); } catch (e) { /* noop */ } };
    if (opts.signal) opts.signal.addEventListener('abort', onExt);
    var timer = setTimeout(function () { try { ctrl.abort(); } catch (e) { /* noop */ } }, 45000);
    dbg.ctrl = ctrl;
    function settle() {
      clearTimeout(timer);
      if (opts.signal) { try { opts.signal.removeEventListener('abort', onExt); } catch (e) { /* noop */ } }
      dbg.ctrl = null;
    }
    return fetch(API_ZCODE_RUN, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: ctrl.signal,
      body: JSON.stringify({ code: code, language: runLang, timeout: 15 })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) {
        settle();
        if (res.status === 429 || (j && j.error === 'session_budget_exceeded')) {
          throw new P5Error('budget', (j && (j.message || j.msg)) || 'ZCode 执行次数已达今日上限（30 次）');
        }
        if (!res.ok || !j || j.ok !== true) {
          if (opts.signal && opts.signal.aborted) throw new P5Error('aborted', '验证已终止');
          return 'FALLBACK';
        }
        var d = j.data || {};
        return {
          mode: 'zcode', ok: d.status === 'completed', exitCode: (d.exit_code === undefined ? null : d.exit_code),
          stdout: d.stdout || '', stderr: d.stderr || '', durationMs: d.duration_ms || 0, zcodeTaskId: d.task_id || null,
          reason: d.error === 'timeout' ? '执行超时（>15s 强制终止）'
            : (d.status === 'completed' ? '' : (String(d.stderr || '').split('\n')[0] || ('exit_code_' + d.exit_code)))
        };
      });
    }, function (err) {
      settle();
      if (opts.signal && opts.signal.aborted) throw new P5Error('aborted', '验证已终止');
      if (err && err.name === 'AbortError') throw new P5Error('timeout', 'ZCode 执行超时（45s 未返回）');
      return 'FALLBACK'; // 网络失败 → 降级静态校验（与手动/AGI 路径一致）
    }).then(function (v) {
      if (v !== 'FALLBACK') return v;
      return p5StaticVerify(code, lang, opts.signal);
    });
  }

  /* 静态校验降级通道（VERIFY_PROMPT 同构，载荷与 agiStaticVerify :3369-3388 一致） */
  function p5StaticVerify(code, lang, signal) {
    var t0 = Date.now();
    var payload = {
      messages: [{ role: 'user', content: VERIFY_PROMPT.replace('{{LANG}}', (LANG_LABEL[lang] || lang || 'python')).replace('{{CODE}}', code) }],
      temperature: 0
    };
    Object.assign(payload, modelField());
    return p5Chat(payload, { signal: signal }).then(function (j) {
      var txt = String(j.content || '');
      var ok = /VERDICT:\s*PASS/i.test(txt);
      return {
        mode: 'static', ok: ok, exitCode: null, stdout: '', stderr: '',
        durationMs: Date.now() - t0, zcodeTaskId: null,
        reason: ok ? '' : (txt.split('\n').slice(1).join(' ') || '未给出原因').trim()
      };
    });
  }

  /* 发送到对话（复用 P4 消费键语义：localStorage iris.toChat.draft，不自动发送） */
  function p5SendToChat(text) {
    var draft = { text: text, ts: Date.now(), from: 'iris-workbench' };
    try {
      localStorage.setItem('iris.toChat.draft', JSON.stringify(draft));
    } catch (e) { toast('写入失败：浏览器存储不可用'); return; }
    log('已写入「发送到对话」草稿（分级方案摘要），正在返回主对话…', 'ok');
    toast('已就绪，返回主对话');
    setTimeout(function () { location.href = (location.protocol === 'file:') ? MORAY_HOME : '/'; }, 600);
  }

  /* ============================================================
     [P5-2/3] Prompt 编排：三层根因枚举 + 三档分级方案（含硬约束输出契约）
     ============================================================ */

  /* 枚举 Prompt：复现证据 + 已验证假设历史 → 三层根因 + ≤3 候选假设（每轮重枚举，新证据回灌） */
  function buildEnumPrompt(ocr, features, history, roundN) {
    var L = [];
    L.push('你是资深故障排查专家，正在执行多轮自动排查（第 ' + roundN + ' 轮 / 上限 ' + DEBUG_MAX_ROUNDS + ' 轮）。');
    L.push('排查流程：错误复现 → 根因枚举 → 逐一验证 → 结论收敛。请模拟专业开发者排错思路：先看触发点，再追逻辑链路，最后定位设计/依赖层根因。');
    L.push('【复现报错文本（OCR）】');
    L.push(String(ocr || '').slice(0, 2500));
    if (features) {
      L.push('【结构化特征】' + summarizeFeatures(features));
    }
    if (history && history.length) {
      L.push('【此前各轮假设与验证结果（失败证据将作为本轮输入）】');
      history.forEach(function (h) {
        h.hypotheses.forEach(function (x) {
          L.push('- 第' + h.n + '轮 假设「' + x.text.slice(0, 60) + '」 → ' +
            (x.status === 'pass' ? '验证通过' : (x.status === 'fail' ? '验证失败：' + String(x.err || '').slice(0, 120) : x.status === 'skip' ? '无法验证' : '未执行')));
        });
      });
      L.push('请结合上述失败证据修正思路：排除已验证失败的假设，沿剩余线索继续深挖。');
    }
    L.push('输出要求（严格遵守，不要输出任何额外内容）：');
    L.push('【直接原因】（触发报错的代码行/语句层，一句话 + 证据）');
    L.push('【间接原因】（出错代码所在逻辑链路：调用/数据流如何走到这一步）');
    L.push('【根本原因】（设计/依赖/约定层：为何会写成这样、环境或版本根因）');
    L.push('【候选假设】');
    L.push('H1: <最可能的可验证假设，含指向具体代码位置或数据形态>（每轮最多 3 个，按可能性降序）');
    L.push('H2: ...');
    L.push('H3: ...');
    if (roundN > 1) L.push('注意：上一轮候选已全部验证失败，本轮必须给出不同或更深层的假设，不得重复已失败假设。');
    return L.join('\n');
  }

  /* 枚举输出解析（分段标记 + H 行；缺段容错） */
  function parseEnumOutput(text) {
    var s = stripThinkBlocks(String(text || ''));
    function sec(name) {
      var m = s.match(new RegExp('【' + name + '】\\s*([\\s\\S]*?)(?=(【(?:直接原因|间接原因|根本原因|候选假设)】)|$)'));
      return m ? m[1].trim() : '';
    }
    var causes = { direct: sec('直接原因'), indirect: sec('间接原因'), root: sec('根本原因') };
    var hyps = [];
    var hSec = s.match(/【候选假设】\s*([\s\S]*?)(?=(【)|$)/);
    if (hSec) {
      // 仅在候选假设段存在时解析（无该段 = 模型未按契约输出，不得把正文当假设）
      hSec[1].split('\n').forEach(function (line) {
        var t = line.trim();
        if (!t) return;
        var m = t.match(/^(?:H\d+\s*[:：.、]\s*)?(.+)$/);
        var txt = m ? m[1].trim() : t;
        if (txt && !/^[【\[（(]/.test(txt) && txt.indexOf('输出要求') === -1) hyps.push(txt);
      });
    }
    // 去重（normLine）+ 每轮上限 3（任务书：逐一验证）
    var seen = {}; var out = [];
    hyps.forEach(function (h) {
      var key = normLine(h);
      if (!seen[key]) { seen[key] = 1; out.push(h); }
    });
    return { causes: causes, hypotheses: out.slice(0, 3) };
  }

  /* 三档方案 Prompt：证据 + 分层根因 + 当前假设 +（多轮时）上轮代码 */
  function buildPlansPrompt(ocr, causes, hypo, prevCode, features) {
    var L = [];
    L.push('你是专业代码故障诊断专家。请针对下述报错与根因假设，产出三档修复方案（快速/规范/兼容），全部为完整可运行代码。');
    L.push('【报错文本】');
    L.push(String(ocr || '').slice(0, 2000));
    if (features) L.push('【结构化特征】' + summarizeFeatures(features));
    if (causes && causes.direct) {
      L.push('【根因分层】');
      L.push('直接原因：' + String(causes.direct).slice(0, 400));
      L.push('间接原因：' + String(causes.indirect || '').slice(0, 400));
      L.push('根本原因：' + String(causes.root || '').slice(0, 400));
    }
    if (hypo) L.push('【本轮待验证假设】' + String(hypo).slice(0, 300));
    if (prevCode) {
      L.push('【上一版修复代码（执行后仍报错，请以最新报错为准修正）】');
      L.push('```' + (detectLang(prevCode) || 'text') + '\n' + String(prevCode).slice(0, 3000) + '\n```');
    }
    L.push('输出要求（严格遵守，不要输出任何额外内容、解释或多余围栏）：');
    L.push('每档方案一节，节内依次给出：根因说明 / 优缺点 / 适用场景 / 潜在风险 四个单行字段，再给一个 ``` 围栏代码块（完整可运行、第一行标注语言、不要 diff 片段）。');
    L.push('【方案一·快速修复】\n根因说明：…\n优缺点：…\n适用场景：…\n潜在风险：…\n```lang\n代码\n```');
    L.push('【方案二·规范修复】（默认推荐：符合编码规范、逻辑清晰、可维护性好）\n根因说明：…\n优缺点：…\n适用场景：…\n潜在风险：…\n```lang\n代码\n```');
    L.push('【方案三·兼容修复】（最大程度保留原有逻辑与接口、适配依赖版本、兼容性最优）\n根因说明：…\n优缺点：…\n适用场景：…\n潜在风险：…\n```lang\n代码\n```');
    L.push('三档必须体现真实差异（改动范围/写法/兼容策略），不得雷同。');
    return L.join('\n');
  }

  /* 三档方案解析：容错——单方案输出回退为仅规范修复档 */
  var PLAN_TITLES = { quick: '方案一·快速修复', std: '方案二·规范修复', compat: '方案三·兼容修复' };
  function parseTriPlan(text) {
    var s = stripThinkBlocks(String(text || ''));
    var plans = {};
    var marks = [];
    var re = /【方案([一二三])[^】]*】/g, m;
    while ((m = re.exec(s))) marks.push({ key: m[1] === '一' ? 'quick' : (m[1] === '二' ? 'std' : 'compat'), at: m.index, end: -1 });
    if (marks.length >= 2) {
      for (var i = 0; i < marks.length; i++) {
        marks[i].end = i + 1 < marks.length ? marks[i + 1].at : s.length;
        var secTxt = s.slice(marks[i].at, marks[i].end);
        var kind = marks[i].key;
        if (!plans[kind]) plans[kind] = parseOnePlan(secTxt, kind);
      }
    } else {
      // 容错：未分三档 → 按 parseResult 单方案语义归入规范档
      var one = parseResult(s);
      plans.std = {
        key: 'std', title: PLAN_TITLES.std, code: one.code, lang: one.lang || detectLang(one.code),
        root: one.root, pros: one.plan || '', cons: '', scene: '通用（模型未分档，已回退为单方案）', riskTxt: ''
      };
    }
    var order = ['quick', 'std', 'compat'];
    order.forEach(function (k) { if (!plans[k]) plans[k] = null; });
    var have = order.filter(function (k) { return plans[k] && plans[k].code; });
    if (!have.length) return null;
    var set = { list: have.map(function (k) { return plans[k]; }), keys: order };
    // 风险自检（纯前端，零模型调用）：每方案代码静态扫描
    set.list.forEach(function (p) {
      p.risk = IrisRisk.scan(p.code, p.lang);
      p.riskLevel = p.risk.level;
    });
    set.recommend = recommendPlan(set);
    return set;
  }
  function parseOnePlan(secTxt, kind) {
    var p = { key: kind, title: PLAN_TITLES[kind], code: '', lang: '', root: '', pros: '', cons: '', scene: '', riskTxt: '' };
    var fe = secTxt.match(/```([A-Za-z0-9_+\-]*)[ \t]*\n([\s\S]*?)```/);
    if (fe) {
      p.code = fe[2].replace(/\s+$/, '');
      p.lang = (fe[1] || detectLang(fe[2]) || '').toLowerCase();
    } else {
      var tail = secTxt.replace(/【[^】]*】/g, ' ');
      var after = tail.match(/潜在风险[：:]\s*([\s\S]+)$/);
      p.code = (after ? '' : tail.trim()).slice(0, 4000);
      p.lang = detectLang(p.code);
    }
    function field(label) {
      var mm = secTxt.match(new RegExp(label + '[：:]\\s*([^\\n]+(?:\\n(?![\\u3010【])(?!(?:根因说明|优缺点|适用场景|潜在风险))[^\\n]*)*)'));
      // 截断后续围栏（字段单段语义：值行 + 至多一个非标记续行，代码围栏属独立内容）
      return mm ? String(mm[1]).split('```')[0].trim() : '';
    }
    p.root = field('根因说明');
    var pc = field('优缺点');
    var parts = pc.split(/[；;]/);
    p.pros = parts[0] || ''; p.cons = (parts[1] || '').trim();
    p.scene = field('适用场景');
    p.riskTxt = field('潜在风险');
    return p;
  }

  /* 择优推荐：默认规范修复；高风险降权；错误类型调整优先级（任务书阶段3 3.3） */
  function recommendPlan(set) {
    var byKey = {};
    set.list.forEach(function (p) { byKey[p.key] = p; });
    var errType = (dbg.features && dbg.features.type) || '';
    var preferOrder;
    if (/(Import|ModuleNotFound|version|Deprecat|No module)/i.test(errType)) preferOrder = ['compat', 'std', 'quick'];
    else if (/(Syntax|Indentation|Tab|编译|COMPILATION)/i.test(errType)) preferOrder = ['std', 'quick', 'compat'];
    else preferOrder = ['std', 'quick', 'compat'];
    for (var i = 0; i < preferOrder.length; i++) {
      var p = byKey[preferOrder[i]];
      if (p && p.code && p.riskLevel !== 'high') return p.key;
    }
    for (var j = 0; j < set.list.length; j++) {
      if (set.list[j] && set.list[j].riskLevel !== 'high') return set.list[j].key;
    }
    return (set.list[0] && set.list[0].key) || 'std';
  }
  function planByKey(set, key) {
    for (var i = 0; i < set.list.length; i++) if (set.list[i].key === key) return set.list[i];
    return null;
  }
  function planLabel(key) {
    return key === 'quick' ? '快速修复' : (key === 'compat' ? '兼容修复' : '规范修复');
  }
  var RISK_META = { low: { text: '风险：低', cls: 'p5-risk-low' }, medium: { text: '风险：中', cls: 'p5-risk-med' }, high: { text: '风险：高', cls: 'p5-risk-high' } };

  /* ============================================================
     [P5-3] IrisPlans UI —— 方案 Tab / 展开体 / Diff / 独立操作 / 切换手动修复
     ============================================================ */
  var P5Plans = (function () {
    var pPanel = $('p5-plans-panel'), pTabs = $('p5-plan-tabs'), pBody = $('p5-plan-body'), pDiff = $('p5-plan-diff'), pDiffCols = $('p5-plan-diff-cols');
    var pDiffLeft = $('p5-diff-left'), pDiffRight = $('p5-diff-right'), pManualBtn = $('p5-plans-manual-btn');
    var current = null;       // {set, source}  planSet + 来源（'debug'|'debug-partial'|'standalone'）
    var activeKey = 'std';
    var running = false;      // 方案执行验证互斥（与手动执行 runBusy 叠加）

    function show(set, source) {
      current = { set: set, source: source || 'standalone' };
      activeKey = set.recommend || 'std';
      if (!planByKey(set, activeKey)) activeKey = set.list[0].key;
      pPanel.hidden = false;
      renderTabs();
      renderBody();
      hideDiff();
      log('分级方案已生成（' + set.list.length + ' 档，默认展示：' + planLabel(activeKey) + '，来源=' + source + '）', 'info');
    }
    function hide() { pPanel.hidden = true; hideDiff(); current = null; }
    function getSet() { return current && current.set; }

    function renderTabs() {
      pTabs.innerHTML = '';
      var set = current.set;
      set.list.forEach(function (p) {
        var b = document.createElement('button');
        b.type = 'button'; b.setAttribute('role', 'tab');
        b.className = 'p5-plan-tab' + (p.key === activeKey ? ' is-active' : '') + (p.riskLevel === 'high' ? ' is-risk-high' : '');
        b.appendChild(document.createTextNode(planLabel(p.key)));
        if (p.key === set.recommend) {
          b.appendChild(p5El('span', 'p5-reco-badge', '推荐'));
        }
        var rm = RISK_META[p.riskLevel] || RISK_META.low;
        b.appendChild(p5El('span', 'p5-risk-chip ' + rm.cls, rm.text.replace('风险：', '')));
        if (p.validation) b.appendChild(p5El('span', 'p5-val-chip ' + (p.validation.ok ? 'is-ok' : 'is-fail'), p.validation.ok ? '✓' : '✕'));
        b.addEventListener('click', function () {
          activeKey = p.key; renderTabs(); renderBody();
        });
        pTabs.appendChild(b);
      });
    }

    function renderBody() {
      pBody.innerHTML = '';
      var set = current.set, p = planByKey(set, activeKey);
      if (!p) return;
      // 元信息条
      var meta = p5El('div', 'p5-plan-meta');
      meta.appendChild(p5El('span', 'p5-plan-title', p.title + (p.key === set.recommend ? '（择优推荐）' : '')));
      meta.appendChild(p5El('span', 'mono lang-tag', p.lang || '未知语言'));
      if (current.source !== 'standalone' && dbg.bestHypo) {
        meta.appendChild(p5El('span', 'p5-hypo-line', '验证假设：' + String(dbg.bestHypo.text || '').slice(0, 80)));
      }
      pBody.appendChild(meta);

      function block(labelText, value, foldIfLong) {
        if (!value) return;
        if (foldIfLong && String(value).length > 160) {
          var d = document.createElement('details'); d.className = 'ocr-fold';
          d.appendChild(p5El('summary', '', labelText));
          d.appendChild(p5El('p', 'p5-f-text', value));
          pBody.appendChild(d);
        } else {
          var w = document.createElement('div');
          w.appendChild(p5El('span', 'p5-f-label', labelText));
          w.appendChild(p5El('p', 'p5-f-text', value));
          pBody.appendChild(w);
        }
      }
      block('根因说明', p.root);
      block('优缺点（可取/代价）', [p.pros && ('优：' + p.pros), p.cons && ('劣：' + p.cons)].filter(Boolean).join('　'), true);
      block('适用场景', p.scene);
      block('潜在风险', p.riskTxt);

      // 代码块（hljs 复用既有高亮，textContent 先行零注入）
      var art = p5El('article', 'result-block');
      var h3 = p5El('h3'); h3.textContent = '修复代码';
      h3.appendChild(p5El('span', 'mono lang-tag', p.lang || ''));
      art.appendChild(h3);
      var pre = p5El('pre', 'code-wrap');
      var code = p5El('code', 'hljs language-' + (p.lang || 'plaintext'), p.code);
      try { if (window.hljs) window.hljs.highlightElement(code); } catch (e) { /* 高亮失败不阻断 */ }
      pre.appendChild(code); art.appendChild(pre);
      pBody.appendChild(art);

      // 独立操作区（任务书阶段3 3.2：执行验证 / 复制代码 / 发送到对话 / 风险自检）
      var acts = p5El('div', 'p5-plan-acts');
      acts.appendChild(p5Btn('ghost', '执行验证（ZCode 沙箱）', function () { runPlan(p); }, p.riskLevel === 'high' ? '该方案风险自检为高风险，执行前将二次确认' : '在沙箱白名单目录内真实执行本方案代码'));
      acts.appendChild(p5Btn('ghost', '复制代码', function () { copyText(p.code, '「' + planLabel(p.key) + '」代码已复制到剪贴板'); }));
      acts.appendChild(p5Btn('ghost', '发送到对话', function () {
        var txt = '请基于 Iris 分级方案继续分析：\n推荐方案：' + planLabel(set.recommend) + '\n本方案：' + planLabel(p.key) +
          (p.scene ? '\n适用场景：' + p.scene : '') + (p.riskTxt ? '\n潜在风险：' + p.riskTxt : '') +
          '\n```' + (p.lang || '') + '\n' + String(p.code).slice(0, 1600) + '\n```';
        p5SendToChat(txt);
      }));
      acts.appendChild(p5Btn('ghost', '风险自检', function () { renderRiskToPanel(p.code, p.lang, '「' + planLabel(p.key) + '」'); }, '仅重新扫描本方案代码（纯前端，零模型调用）'));
      pBody.appendChild(acts);

      // 方案级风险自检摘要（阶段4：逐条 + 高亮警示）
      var rs = p.risk;
      if (rs && rs.items.length) {
        var det = document.createElement('details'); det.className = 'p5-plan-risk';
        det.appendChild(p5El('summary', 'p5-plan-risk-sum', '静态风险自检：' + rs.summary));
        rs.items.forEach(function (it) { det.appendChild(riskItemDom(it)); });
        pBody.appendChild(det);
      } else if (rs) {
        pBody.appendChild(p5El('p', 'p5-plan-risk-none', '静态风险自检：未检出风险点（' + (rs.summary || '五类已检测') + '）'));
      }

      // 本方案执行结果（若有）
      if (p.validation) {
        var v = p.validation;
        var vb = p5El('div', 'p5-plan-val ' + (v.ok ? 'is-ok' : 'is-fail'));
        vb.appendChild(p5El('p', '', (v.ok ? '✓ 执行验证通过' : '✕ 执行验证未通过') + '（' + (v.mode === 'zcode' ? '沙箱真实执行 exit ' + (v.exitCode === null ? '—' : v.exitCode) : v.mode === 'static' ? '静态校验降级' : '执行异常') + ' · ' + (v.durationMs || 0) + 'ms）'));
        if (v.stderr) vb.appendChild(p5El('pre', 'code-wrap', v.stderr.slice(0, 600)));
        pBody.appendChild(vb);
      }
    }

    function runPlan(p) {
      if (state.running || state.viewing) { toast(state.viewing ? '历史回看态为只读' : '流程进行中，请稍候'); return; }
      if (running || state.runBusy) { toast('正在执行验证，请稍候'); return; }
      if (!p.code) { toast('本方案无代码可执行'); return; }
      // 阶段4 验收：高风险方案执行前二次确认
      if (p.riskLevel === 'high') {
        var hi = (p.risk.items || []).filter(function (x) { return x.sev === 'high'; });
        var okConfirm = window.confirm('「' + planLabel(p.key) + '」风险自检为【高风险】：\n' +
          hi.map(function (x) { return '· 行' + x.line + ' ' + x.what; }).slice(0, 5).join('\n') +
          '\n\n确认仍要在沙箱执行吗？');
        if (!okConfirm) { log('已取消高风险方案的执行验证（可切换其他方案或先整改）', 'info'); return; }
      }
      running = true;
      log('方案「' + planLabel(p.key) + '」执行验证中…', 'running');
      p5Validate(p.code, p.lang, {}).then(function (v) {
        finishRunningLog(v.ok ? 'ok' : 'fail', v.ok ? '验证通过' : '验证未通过');
        p.validation = v;
        log('方案「' + planLabel(p.key) + '」' + (v.ok ? '执行验证通过' : '执行验证未通过：' + String(v.reason || '').slice(0, 100)), v.ok ? 'ok' : 'fail');
      }, function (e) {
        finishRunningLog('fail', '执行异常');
        p.validation = { mode: 'error', ok: false, reason: (e && e.message) || String(e), durationMs: 0, exitCode: null, stdout: '', stderr: '' };
        log('方案执行验证异常：' + ((e && e.message) || e), 'fail');
      }).then(function () {
        running = false;
        renderTabs(); renderBody();
      });
    }

    /* 方案间 Diff（复用既有 diffLines/highlightLineHtml/diffCounts/diff-cols 样式段） */
    function renderDiff(leftKey, rightKey) {
      var set = current.set;
      var a = planByKey(set, leftKey), b = planByKey(set, rightKey);
      pDiffCols.innerHTML = '';
      if (!a || !b || !a.code || !b.code) return;
      pDiff.hidden = false;
      var lang = b.lang || a.lang || detectLang(b.code);
      var d = diffLines(a.code, b.code);
      var cols = document.createElement('div');
      cols.className = 'diff-cols';
      function buildCol(headText, rows, side) {
        var col = document.createElement('div'); col.className = 'diff-col';
        col.appendChild(p5El('div', 'diff-col-head', headText));
        var box = document.createElement('div'); box.className = 'diff-lines';
        rows.forEach(function (row) {
          var cell = row[side];
          var dl = document.createElement('div');
          dl.className = 'dl dl-' + (row.type === 'eq' ? 'eq' : row.type);
          if (cell) {
            dl.appendChild(p5El('span', 'dl-no', String(cell.no)));
            var cs = document.createElement('span'); cs.className = 'dl-code';
            var html = highlightLineHtml(cell.text, lang);
            if (html !== null) cs.innerHTML = html; else cs.textContent = cell.text;
            dl.appendChild(cs);
          } else {
            dl.appendChild(p5El('span', 'dl-no', ''));
            dl.appendChild(p5El('span', 'dl-pad', ''));
          }
          box.appendChild(dl);
        });
        col.appendChild(box);
        return col;
      }
      cols.appendChild(buildCol('方案：' + planLabel(a.key), d.rows, 'l'));
      cols.appendChild(buildCol('方案：' + planLabel(b.key), d.rows, 'r'));
      pDiffCols.appendChild(cols);
      var legend = document.createElement('div'); legend.className = 'diff-legend';
      var cnt = diffCounts(d.rows);
      legend.appendChild(p5El('span', '', '新增 ' + cnt.add + ' · 删除 ' + cnt.del + ' · 修改 ' + cnt.mod + '（' + planLabel(a.key) + ' → ' + planLabel(b.key) + ' 差异）'));
      pDiffCols.appendChild(legend);
    }
    function hideDiff() { pDiff.hidden = true; pDiffCols.innerHTML = ''; }
    function fillDiffSelects() {
      if (!current) return;
      [pDiffLeft, pDiffRight].forEach(function (sel, idx) {
        sel.innerHTML = '';
        current.set.list.forEach(function (p) {
          var o = document.createElement('option');
          o.value = p.key; o.textContent = planLabel(p.key);
          sel.appendChild(o);
        });
        sel.value = idx === 0 ? (current.set.list[0] && current.set.list[0].key) : (current.set.recommend || 'std');
      });
    }

    /* 切换为手动修复（任务书阶段2：支持终止排查切手动修复；唯一写既有 rounds 的 P5 入口） */
    function adoptAsManualRound() {
      if (!current) return;
      var set = current.set;
      var p = planByKey(set, activeKey) || planByKey(set, 'std') || set.list[0];
      if (!p || !p.code) { toast('当前无可用方案代码'); return; }
      if (state.running || batch.running) { toast('流程进行中，请稍候'); return; }
      if (dbg.active) P5_DEBUG.abort();
      var r = {
        root: p.root || (dbg.causes && dbg.causes.direct) || ('（排查结论）' + planLabel(p.key)),
        plan: [p.pros && '优：' + p.pros, p.cons && '劣：' + p.cons, p.scene && '场景：' + p.scene, p.riskTxt && '风险：' + p.riskTxt].filter(Boolean).join('；') || p.title,
        code: p.code, lang: p.lang, verifyFailed: null
      };
      pushRound(state.rounds.length + 1, 'P5 排查采纳（' + planLabel(p.key) + '）', r);
      log('已切换为手动修复：采纳「' + planLabel(p.key) + '」为第 ' + state.rounds.length + ' 轮修复代码，可继续「执行验证」或「继续修复」', 'ok');
      toast('已采纳方案，进入手动修复轮次');
    }

    function wire() {
      if (!pPanel) return;
      $('p5-plans-close-btn') && $('p5-plans-close-btn').addEventListener('click', function () { hide(); });
      $('p5-diff-render-btn') && $('p5-diff-render-btn').addEventListener('click', function () { renderDiff(pDiffLeft.value, pDiffRight.value); });
      $('p5-diff-close-btn') && $('p5-diff-close-btn').addEventListener('click', hideDiff);
      pManualBtn && pManualBtn.addEventListener('click', adoptAsManualRound);
    }

    return {
      show: show, hide: hide, getSet: getSet, wire: wire, fillDiffSelects: fillDiffSelects,
      adoptAsManualRound: adoptAsManualRound, runPlan: runPlan,
      setManualVisible: function (v) { if (pManualBtn) pManualBtn.hidden = !v; },
      currentActive: function () { return current ? { set: current.set, key: activeKey } : null; }
    };
  })();

  /* ============================================================
     [P5-2] IrisDebug —— 智能排查状态机（reproducing/enumerating/verifying/concluding/failed/completed）
     ============================================================ */
  var P5_DEBUG = (function () {
    var dPanel = $('p5-debug-panel'), dChip = $('p5-debug-state-chip'), dProg = $('p5-debug-progress'),
      dRounds = $('p5-debug-rounds'), dCause = $('p5-debug-cause'),
      bPause = $('p5-debug-pause-btn'), bResume = $('p5-debug-resume-btn'), bAbort = $('p5-debug-abort-btn'), bClose = $('p5-debug-close-btn');

    var STATE_TEXT = {
      idle: '待启动', reproducing: '复现错误中', enumerating: '根因枚举中', verifying: '候选验证中',
      concluding: '结论收敛中', completed: '已完成', failed: '失败/已终止'
    };

    function setPhase(ph, note) {
      dbg.phase = ph;
      if (dChip) {
        dChip.textContent = STATE_TEXT[ph] || ph;
        dChip.className = 'p5-state-chip is-' + ph;
      }
      if (note && dProg) dProg.textContent = note;
    }

    function progressLine() {
      return '第 ' + Math.min(dbg.round, DEBUG_MAX_ROUNDS) + '/' + DEBUG_MAX_ROUNDS + ' 轮 · 模型 ' + dbg.llmUsed + '/' + DEBUG_LLM_CAP +
        ' 次 · 执行 ' + dbg.runUsed + '/' + DEBUG_RUN_CAP + ' 次';
    }
    function refreshProg() { if (dProg) dProg.textContent = progressLine(); }

    /* 私有总线：排查进度事件（P5 自定义事件名，不动 P3 EVENTS 契约；供自动化/外部订阅） */
    var p5bus = {};
    function p5On(ev, fn) { (p5bus[ev] = p5bus[ev] || []).push(fn); }
    function p5Emit(ev, payload) {
      (p5bus[ev] || []).forEach(function (fn) { try { fn(payload); } catch (e) { /* 处理器异常不阻断 */ } });
    }

    /* 协作式检查点：暂停挂起 / 终止抛出（先例 :3099/:3597 语义；LLM/沙箱在途请求经 AbortController 即时中断） */
    function checkpoint() {
      if (dbg.abortReq) return Promise.reject(new P5Error('aborted', '排查已终止'));
      if (Date.now() > dbg.deadline) return Promise.reject(new P5Error('deadline', '排查总超时（' + Math.round(DEBUG_TOTAL_MS / 60000) + ' 分钟）'));
      if (!dbg.paused) return Promise.resolve();
      return new Promise(function (resolve) {
        dbg.resumeWaits.push({ resolve: resolve });
        setPhase(dbg.phase, '已暂停 — 点击「继续」恢复');
      });
    }

    /* ---- 轮次 UI ---- */
    function roundDom(r) {
      var g = document.createElement('section');
      g.className = 'p5-round-group';
      g.setAttribute('data-round', r.n);
      var h = document.createElement('div'); h.className = 'p5-round-head';
      h.appendChild(p5El('span', 'p5-round-no', r.n === 0 ? '排查启动' : '排查第 ' + r.n + '/' + DEBUG_MAX_ROUNDS + ' 轮'));
      h.appendChild(p5El('span', 'p5-round-time mono', r.ts || ''));
      g.appendChild(h);
      if (r.causes) g.appendChild(causeDom(r.causes, r.n));
      r.hypotheses.forEach(function (x, i) { g.appendChild(hypoDom(x, r.n, i)); });
      if (r.note) g.appendChild(p5El('p', 'p5-round-note', r.note));
      return g;
    }
    function causeDom(causes, roundN) {
      var d = document.createElement('details'); d.className = 'ocr-fold p5-cause-fold';
      d.appendChild(p5El('summary', '', '三层根因（第' + roundN + '轮枚举，点击展开/收起）'));
      [['直接原因', causes.direct], ['间接原因', causes.indirect], ['根本原因', causes.root]].forEach(function (pair) {
        if (!pair[1]) return;
        var blk = p5El('div', 'p5-cause-item');
        blk.appendChild(p5El('span', 'p5-cause-tier', pair[0]));
        blk.appendChild(p5El('p', 'p5-cause-text', pair[1]));
        d.appendChild(blk);
      });
      return d;
    }
    function hypoDom(x, roundN, idx) {
      var d = p5El('div', 'p5-hypo is-' + (x.status || 'pending'));
      d.appendChild(p5El('div', 'p5-hypo-text', 'H' + (idx + 1) + '（第' + roundN + '轮）：' + x.text));
      d.appendChild(p5El('div', 'p5-hypo-status', x.status === 'pass' ? '● 验证通过' :
        x.status === 'fail' ? ('✕ 验证失败' + (x.err ? '：' + String(x.err).slice(0, 90) : '')) :
          x.status === 'skip' ? ('· 无法验证' + (x.err ? '：' + String(x.err).slice(0, 60) : '')) :
            x.status === 'running' ? '◐ 生成方案并验证中…' : '… 待验证'));
      if (x.validation) {
        d.appendChild(p5El('div', 'p5-hypo-val mono', '验证方式：' + (x.validation.mode === 'zcode' ? '沙箱真实执行' : '静态校验（降级）') +
          ' · exit ' + (x.validation.exitCode === null || x.validation.exitCode === undefined ? '—' : x.validation.exitCode) +
          ' · ' + (x.validation.durationMs || 0) + 'ms'));
      }
      return d;
    }
    function rerenderRounds() {
      if (!dRounds) return;
      dRounds.innerHTML = '';
      dbg.history.forEach(function (r) { dRounds.appendChild(roundDom(r)); });
    }
    function renderCauseSummary() {
      if (!dCause) return;
      dCause.innerHTML = '';
      if (!dbg.causes) return;
      var t = p5El('h3', 'p5-cause-title', dbg.phase === 'completed' ? '排查结论（根因分层，证据可展开）' : '当前根因线索（未收敛，排查失败/终止时的部分结论）');
      dCause.appendChild(t);
      dCause.appendChild(causeDom(dbg.causes, dbg.round || 1));
      if (dbg.bestHypo) {
        dCause.appendChild(p5El('p', 'p5-conclude', '✓ 收敛假设：' + String(dbg.bestHypo.text || '').slice(0, 200) +
          (dbg.bestHypo.validation ? '（' + (dbg.bestHypo.validation.mode === 'zcode' ? '沙箱验证 exit ' + dbg.bestHypo.validation.exitCode : '静态校验通过') + '）' : '')));
      }
      if (dbg.endReason && dbg.phase !== 'completed') {
        dCause.appendChild(p5El('p', 'p5-end-reason', '终止原因：' + dbg.endReason));
      }
    }

    /* ---- 复现 ---- */
    function reproduce() {
      setPhase('reproducing', '错误复现');
      if (state.ocrText) {
        dbg.evidence = state.ocrText;
        dbg.features = extractFeatures(dbg.evidence);
        dbgRoundEntry('复现完成：复用本次分析的 OCR 文本（' + dbg.evidence.length + ' 字符），跳过重复识别（省 1 次模型调用）');
        log('[P5 排查] 复现：复用已识别报错文本（' + dbg.evidence.length + ' 字符）', 'info');
        return Promise.resolve();
      }
      if (!state.dataUrl) {
        return Promise.reject(new P5Error('no-image', '尚未上传或识别报错截图，无法复现错误'));
      }
      dbgRoundEntry('复现：当前页面无已识别文本，自动执行一次 OCR 复现报错现场');
      log('[P5 排查] 复现：无已识别文本，自动执行 OCR…', 'running');
      return stepOcr().then(function (text) {
        finishRunningLog('ok', '复现完成');
        dbg.evidence = text;
        dbg.features = extractFeatures(text);
        log('[P5 排查] 复现完成：报错文本 ' + text.length + ' 字符 · 特征 [' + summarizeFeatures(dbg.features) + ']', 'ok');
      });
    }
    function dbgRoundEntry(note) {
      var last = dbg.history[dbg.history.length - 1];
      if (last) last.note = note;
    }

    /* ---- 枚举（1 次 LLM/轮） ---- */
    function enumerate() {
      return checkpoint().then(function () {
        dbg.round++;
        if (dbg.round > DEBUG_MAX_ROUNDS) return null;
        setPhase('enumerating', '第 ' + dbg.round + ' 轮：三层根因枚举中（1 次模型调用）');
        refreshProg();
        var prompt = buildEnumPrompt(dbg.evidence, dbg.features, dbg.history.filter(function (r) { return r.n >= 1; }), dbg.round);
        var body = Object.assign({ messages: [{ role: 'user', content: prompt }], temperature: 0.2 }, modelField());
        log('[P5 排查] 第 ' + dbg.round + '/' + DEBUG_MAX_ROUNDS + ' 轮：根因枚举中…', 'running');
        return p5Chat(body, { signal: dbg.signal }).then(function (j) {
          finishRunningLog('ok', '枚举完成');
          var parsed = parseEnumOutput(j.content);
          var rec = { n: dbg.round, ts: new Date().toTimeString().slice(0, 8), causes: parsed.causes, hypotheses: parsed.hypotheses.map(function (t) { return { text: t, status: 'pending' }; }), note: '' };
          dbg.history.push(rec);
          dbg.causes = parsed.causes; // 最新枚举覆盖（三层可展开）
          dbg.hypothesisQueue = parsed.hypotheses.map(function (t, i) { return { text: t, rec: rec, idx: i }; });
          rerenderRounds(); renderCauseSummary();
          log('[P5 排查] 第 ' + dbg.round + ' 轮枚举：新候选 ' + parsed.hypotheses.length + ' 个 · 根因分层已更新', 'ok');
          if (!parsed.hypotheses.length) dbgRoundEntry('本轮未产生新候选假设（模型判定线索已穷尽）');
          refreshProg();
          return rec;
        });
      });
    }

    /* ---- 逐一验证候选（生成三档方案 1 次 LLM + 沙箱执行 1 次） ---- */
    function verifyOne(h) {
      return checkpoint().then(function () {
        setPhase('verifying', '第 ' + dbg.round + ' 轮：候选验证中 — ' + String(h.text).slice(0, 40));
        refreshProg();
        h.rec.hypotheses[h.idx].status = 'running';
        rerenderRounds();
        var prevCode = null;
        for (var i = dbg.history.length - 1; i >= 0; i--) {
          var hy = dbg.history[i].hypotheses;
          for (var j = hy.length - 1; j >= 0; j--) if (hy[j].planSet && hy[j].planSet.list.length) { prevCode = hy[j].planSet.list[0].code; break; }
          if (prevCode) break;
        }
        var prompt = buildPlansPrompt(dbg.evidence, dbg.causes, h.text, prevCode, dbg.features);
        var body = Object.assign({ messages: [{ role: 'user', content: prompt }], temperature: 0.2 }, modelField());
        log('[P5 排查] 验证假设 H' + (h.idx + 1) + '（第' + dbg.round + '轮）：生成三档方案…', 'running');
        return p5Chat(body, { signal: dbg.signal }).then(function (j) {
          finishRunningLog('ok', '方案已生成');
          var set = parseTriPlan(j.content);
          if (!set) throw new P5Error('parse', '方案解析失败（模型未返回可用代码块）');
          h.rec.hypotheses[h.idx].planSet = set;
          var target = planByKey(set, set.recommend) || set.list[0];
          log('[P5 排查] 三档方案就绪（' + set.list.length + ' 档 · 推荐 ' + planLabel(set.recommend) + '），沙箱验证推荐方案…', 'info');
          refreshProg();
          return p5Validate(target.code, target.lang, { signal: dbg.signal, debug: true });
        }).then(function (v) {
          var rec2 = h.rec.hypotheses[h.idx];
          rec2.validation = v;
          rec2.status = v.ok ? 'pass' : 'fail';
          rec2.err = v.ok ? '' : String(v.reason || v.stderr || '').split('\n').slice(0, 3).join(' ');
          rerenderRounds(); refreshProg();
          if (v.ok) {
            log('[P5 排查] H' + (h.idx + 1) + ' 验证通过（' + (v.mode === 'zcode' ? '沙箱 exit ' + v.exitCode : '静态校验') + '），开始结论收敛', 'ok');
            return 'PASS';
          }
          log('[P5 排查] H' + (h.idx + 1) + ' 验证失败：' + String(rec2.err || '').slice(0, 80) + ' → 证据回灌，继续排查', 'info');
          return 'FAIL';
        }, function (err) {
          if (err instanceof BudgetError) throw err; // 预算熔断如实上抛 → 全局 failed
          if (err && err.name === 'P5Error' && (err.kind === 'aborted' || err.kind === 'budget-cap' || err.kind === 'deadline')) throw err;
          var rec2 = h.rec.hypotheses[h.idx];
          rec2.status = 'skip';
          rec2.err = (err && err.message) || String(err);
          rerenderRounds();
          log('[P5 排查] H' + (h.idx + 1) + ' 无法验证（' + String(rec2.err).slice(0, 80) + '），跳过续跑', 'info');
          return 'SKIP';
        });
      });
    }

    /* 主循环：枚举 → 队列验证 →（失败续跑/通过收敛）→ 轮次/预算/超时停止 */
    function loop() {
      if (dbg.abortReq) return Promise.reject(new P5Error('aborted', '排查已终止'));
      if (Date.now() > dbg.deadline) return Promise.reject(new P5Error('deadline', '排查总超时'));
      if (dbg.paused) return checkpoint().then(loop);
      if (dbg.hypothesisQueue.length > 0) {
        var h = dbg.hypothesisQueue.shift();
        return verifyOne(h).then(function (res) {
          if (res === 'PASS') {
            dbg.bestHypo = { text: h.text, validation: h.rec.hypotheses[h.idx].validation, planSet: h.rec.hypotheses[h.idx].planSet };
            return 'CONVERGED';
          }
          return loop();
        });
      }
      if (dbg.round >= DEBUG_MAX_ROUNDS) {
        // 候选穷尽且已达轮次上限 → 未收敛（收敛失败出口）
        return Promise.reject(new P5Error('exhausted', dbg.history.some(function (r) { return r.hypotheses.length; }) ?
          '候选假设已穷尽（' + DEBUG_MAX_ROUNDS + ' 轮内未通过验证），保留根因线索供人工介入' : '未能枚举出有效候选假设'));
      }
      return enumerate().then(function (rec) {
        if (rec === null) { dbg.round = DEBUG_MAX_ROUNDS; return loop(); }
        return loop();
      });
    }

    function concludeSuccess() {
      return checkpoint().then(function () {
        setPhase('concluding', '结论收敛：验证通过，固化最终方案');
        var set = dbg.bestHypo.planSet;
        P5Plans.show(set, 'debug');
        P5Plans.fillDiffSelects();
        P5Plans.setManualVisible(true);
        renderRisk(set);
        setPhase('completed', '已完成 — 根因已验证 · 最终方案见「分级修复方案」面板');
        rerenderRounds(); renderCauseSummary(); refreshProg();
        log('[P5 排查] ✓ 排查完成：第 ' + dbg.round + ' 轮假设「' + String(dbg.bestHypo.text).slice(0, 60) + '」经' +
          (dbg.bestHypo.validation.mode === 'zcode' ? '沙箱真实执行' : '静态校验') + '验证通过；' + progressLine(), 'ok');
        toast('排查完成，推荐「' + planLabel(set.recommend) + '」');
        p5Emit('debug-completed', { round: dbg.round, hypo: dbg.bestHypo.text, planSet: set });
        release();
      });
    }

    function failWith(err) {
      dbg.endReason = (err && err.message) || String(err);
      var kind = (err && err.kind) || ((err instanceof BudgetError) ? 'budget' : 'error');
      if (err instanceof BudgetError) dbg.endReason = '预算熔断：' + err.message + '（额度跨自然日自动清零）';
      try { finishRunningLog('fail', '排查中断'); } catch (e) { /* 无进行中行 */ }
      setPhase('failed', '已终止 — ' + dbg.endReason.slice(0, 60));
      if (kind === 'aborted') {
        log('[P5 排查] 已手动终止排查（' + progressLine() + '），已获线索与方案保留在面板', 'info');
      } else {
        log('[P5 排查] 排查终止（' + kind + '）：' + dbg.endReason + ' — 已获根因线索/候选方案保留，可「切换为手动修复」或重新排查', 'fail');
        toast('排查终止：' + dbg.endReason.slice(0, 40));
      }
      rerenderRounds(); renderCauseSummary(); refreshProg();
      // 穷尽/超预算但已有方案 → 展示最后一份 planSet 供人工挑选
      var lastSet = null;
      for (var i = dbg.history.length - 1; i >= 0 && !lastSet; i--) {
        var hs = dbg.history[i].hypotheses;
        for (var j = hs.length - 1; j >= 0; j--) if (hs[j].planSet) { lastSet = hs[j].planSet; break; }
      }
      if (lastSet && !P5Plans.getSet()) {
        P5Plans.show(lastSet, 'debug-partial');
        P5Plans.fillDiffSelects();
        P5Plans.setManualVisible(true);
        renderRisk(lastSet);
      } else if (P5Plans.getSet()) {
        P5Plans.setManualVisible(true);
      }
      p5Emit('debug-failed', { reason: dbg.endReason, kind: kind });
      release();
    }

    function release() {
      dbg.active = false; dbg.paused = false; dbg.abortReq = false;
      dbg.ctrl = null; dbg.sess = null; dbg.signal = null;
      state.running = false;
      el.analyze.disabled = !state.dataUrl;
      el.analyzeLabel.textContent = state.rounds.length ? '重新分析' : '重试分析';
      updateActions();
      syncButtons();
    }

    /* ---- 生命周期控制：启动 / 暂停 / 恢复 / 终止 ---- */
    function start() {
      if (dbg.active) { toast('排查进行中，可先暂停或终止'); return; }
      if (state.running || state.runBusy || batch.running) { toast('分析/验证/批量流程进行中，请稍候'); return; }
      if (!state.dataUrl && !state.ocrText) { toast('请先上传报错截图'); return; }
      dbg.active = true; dbg.phase = 'idle'; dbg.round = 0; dbg.paused = false; dbg.abortReq = false;
      dbg.llmUsed = 0; dbg.runUsed = 0; dbg.startedAt = Date.now(); dbg.deadline = Date.now() + DEBUG_TOTAL_MS;
      dbg.evidence = ''; dbg.features = null; dbg.causes = null; dbg.hypothesisQueue = [];
      dbg.history = [{ n: 0, ts: new Date().toTimeString().slice(0, 8), causes: null, hypotheses: [], note: '排查初始化' }];
      dbg.planSet = null; dbg.bestHypo = null; dbg.endReason = '';
      dbg.sess = new AbortController(); dbg.signal = dbg.sess.signal; // 会话级中止：sess 持有控制器，signal 传给请求
      state.running = true; // 复用既有互斥闸：排查中禁止单次修复/批量/执行
      el.analyze.disabled = true;
      el.analyzeLabel.textContent = '智能排查中…';
      if (dPanel) { dPanel.hidden = false; dRounds.innerHTML = ''; dCause.innerHTML = ''; }
      setPhase('idle', '排查启动');
      refreshProg();
      syncButtons();
      log('—— [P5] 智能排查开始：复现 → 枚举（≤' + DEBUG_MAX_ROUNDS + ' 轮）→ 逐一验证 → 收敛（预算 ' + DEBUG_LLM_CAP + ' 次模型调用 / ' + DEBUG_RUN_CAP + ' 次执行）——', 'info');
      p5Emit('debug-start', { ts: Date.now() });
      run();
    }
    function run() {
      return reproduce()
        .then(function () { return loop(); })
        .then(function (res) {
          if (res === 'CONVERGED') return concludeSuccess();
          failWith(new P5Error('internal', '排查流程异常结束'));
        })
        .catch(function (err) { failWith(err); });
    }
    function pause() {
      if (!dbg.active || dbg.paused) return;
      dbg.paused = true;
      setPhase(dbg.phase, '已暂停 — 在途请求将继续完成');
      syncButtons();
      log('[P5 排查] 已请求暂停：在途模型/执行请求完成后挂起，点「继续」恢复', 'info');
    }
    function resume() {
      if (!dbg.active || !dbg.paused) return;
      dbg.paused = false;
      syncButtons();
      refreshProg();
      var waits = dbg.resumeWaits.splice(0);
      waits.forEach(function (w) { w.resolve(); });
      log('[P5 排查] 已恢复排查', 'info');
    }
    function abort() {
      if (!dbg.active) return;
      dbg.abortReq = true; dbg.paused = false;
      var waits = dbg.resumeWaits.splice(0);
      waits.forEach(function (w) { w.resolve(); }); // loop 检查点将抛 aborted
      if (dbg.sess) { try { dbg.sess.abort(); } catch (e) { /* 已结束 */ } }
      if (dbg.ctrl) { try { dbg.ctrl.abort(); } catch (e) { /* 已结束 */ } }
      log('[P5 排查] 已请求终止…', 'info');
    }

    function snapshot() {
      return {
        active: dbg.active, phase: dbg.phase, round: dbg.round, paused: dbg.paused, abortRequested: dbg.abortReq,
        llmUsed: dbg.llmUsed, runUsed: dbg.runUsed, llmCap: DEBUG_LLM_CAP, runCap: DEBUG_RUN_CAP,
        maxRounds: DEBUG_MAX_ROUNDS, history: dbg.history, causes: dbg.causes,
        endReason: dbg.endReason, elapsedMs: dbg.startedAt ? (Date.now() - dbg.startedAt) : 0
      };
    }

    function syncButtons() {
      if (bPause) bPause.hidden = !(dbg.active && !dbg.paused && !dbg.abortReq);
      if (bResume) bResume.hidden = !(dbg.active && dbg.paused);
      if (bAbort) bAbort.hidden = !dbg.active || dbg.abortReq;
      var db = $('debug-btn');
      if (db) db.disabled = dbg.active;
      var dl = $('debug-btn-label');
      if (dl) dl.textContent = dbg.active ? '排查进行中…' : '智能排查（多轮定位根因）';
    }

    function wire() {
      bPause && bPause.addEventListener('click', pause);
      bResume && bResume.addEventListener('click', resume);
      bAbort && bAbort.addEventListener('click', abort);
      bClose && bClose.addEventListener('click', function () { if (dPanel) dPanel.hidden = true; });
      $('debug-btn') && $('debug-btn').addEventListener('click', start);
    }

    return { start: start, pause: pause, resume: resume, abort: abort, snapshot: snapshot, wire: wire,
      p5On: p5On, p5Emit: p5Emit, dbg: dbg, syncButtons: syncButtons };
  })();

  /* ============================================================
     [P5-4] 风险面板 UI（阶段4：等级评分 + 逐条风险/位置/建议；自动扫描挂观察器）
     ============================================================ */
  var rPanel = $('p5-risk-panel'), rLevel = $('p5-risk-level'), rMeta = $('p5-risk-meta'), rList = $('p5-risk-list');
  function riskItemDom(it) {
    var d = p5El('div', 'p5-risk-item sev-' + it.sev);
    var head = p5El('div', 'p5-risk-what');
    head.appendChild(p5El('span', 'p5-risk-sev', it.sev === 'high' ? '高' : (it.sev === 'medium' ? '中' : '低')));
    head.appendChild(p5El('span', 'p5-risk-cat', (IrisRisk.CAT_LABEL[it.cat] || it.cat) + ' · 第 ' + (it.line || '?') + ' 行'));
    head.appendChild(p5El('span', 'p5-risk-title', it.what));
    d.appendChild(head);
    d.appendChild(p5El('p', 'p5-risk-detail', it.detail));
    if (it.fix) d.appendChild(p5El('p', 'p5-risk-fix', '整改建议：' + it.fix));
    return d;
  }
  function renderRiskToPanel(code, lang, targetLabel) {
    if (!rPanel) return null;
    var rep = IrisRisk.scan(code, lang);
    rPanel.hidden = false;
    var lm = RISK_META[rep.level] || RISK_META.low;
    rLevel.textContent = lm.text + (rep.level === 'high' ? ' ⚠' : '');
    rLevel.className = 'p5-risk-level-chip ' + lm.cls;
    rMeta.textContent = '检测对象：' + (targetLabel || '当前轮修复代码') + ' · 语言 ' + (rep.lang || '?') + ' · 评分 ' + rep.score + ' · ' + (rep.summary || '');
    rList.innerHTML = '';
    if (!rep.items.length) {
      rList.appendChild(p5El('p', 'p5-risk-none', '未检出风险点（五类检测：语法合法性 / 变量完整性 / 边界条件 / 硬编码风险 / 类型匹配）。'));
    } else {
      rep.items.forEach(function (it) { rList.appendChild(riskItemDom(it)); });
    }
    return rep;
  }
  /* 排查 planSet 的推荐方案同步进风险面板（阶段2/3 联动：收敛即自检） */
  function renderRisk(set) {
    var p = planByKey(set, set.recommend) || set.list[0];
    if (p) renderRiskToPanel(p.code, p.lang, '推荐方案「' + planLabel(p.key) + '」');
  }
  var riskAuto = $('p5-risk-auto');
  $('p5-risk-recheck-btn') && $('p5-risk-recheck-btn').addEventListener('click', function () {
    var cur = currentRound();
    if (!cur || !cur.result.code) { toast('当前轮暂无修复代码可检'); return; }
    renderRiskToPanel(cur.result.code, cur.result.lang, '第' + cur.n + '轮修复代码');
  });
  $('p5-risk-close-btn') && $('p5-risk-close-btn').addEventListener('click', function () { if (rPanel) rPanel.hidden = true; });

  /* ============================================================
     [P5-3] 独立入口：生成分级方案（对当前轮修复结果，1 次模型调用）
     ============================================================ */
  function standalonePlans() {
    if (dbg.active) { toast('排查进行中，其结论自带分级方案'); return; }
    if (state.running || state.runBusy || batch.running) { toast('流程进行中，请稍候'); return; }
    var cur = currentRound();
    var ocr = (cur && cur.ocrText) || state.ocrText;
    var prevCode = cur && cur.result ? cur.result.code : '';
    if (!ocr && !prevCode) { toast('暂无报错上下文：请先上传截图分析或运行一次修复'); return; }
    dbg.llmUsed = 0; dbg.runUsed = 0; dbg.deadline = Date.now() + LLM_TIMEOUT_MS + 5000; // 独立入口单请求预算
    dbg.features = extractFeatures(ocr || prevCode || '');
    log('—— [P5] 分级方案生成开始（快速/规范/兼容，1 次模型调用）——', 'info');
    el.plansBtn.disabled = true;
    var pl = $('plans-btn-label'); if (pl) pl.textContent = '方案生成中…';
    var prompt = buildPlansPrompt(ocr, cur && cur.result && cur.result.root ? { direct: cur.result.root, indirect: '', root: '' } : null, null, prevCode, dbg.features);
    var body = Object.assign({ messages: [{ role: 'user', content: prompt }], temperature: 0.2 }, modelField());
    p5Chat(body, {}).then(function (j) {
      var set = parseTriPlan(j.content);
      if (!set) throw new Error('模型未返回可解析的方案代码块');
      P5Plans.show(set, 'standalone');
      P5Plans.fillDiffSelects();
      P5Plans.setManualVisible(false);
      log('分级方案生成完成（' + set.list.length + ' 档 · 推荐 ' + planLabel(set.recommend) + ' · 高风险档已警示）', 'ok');
      toast('已生成 ' + set.list.length + ' 档方案');
    }, function (e) {
      var msg = (e instanceof BudgetError) ? ('预算熔断：' + e.message) : ((e && e.message) || String(e));
      log('分级方案生成失败：' + msg + '（不影响既有单次修复结果）', 'fail');
      toast('方案生成失败');
    }).then(function () {
      el.plansBtn.disabled = false;
      var pl2 = $('plans-btn-label'); if (pl2) pl2.textContent = '生成分级方案（快速/规范/兼容）';
    });
  }

  /* ============================================================
     [P5] 装配：入口按钮显隐（观察既有闸位，零改动 updateActions）+ 互斥同步 + 自动化钩子
     ============================================================ */
  el.debugBtn = $('debug-btn');
  el.plansBtn = $('plans-btn');
  $('plans-btn') && $('plans-btn').addEventListener('click', standalonePlans);

  function p5SyncButtons() {
    // 智能排查：有截图即可用（进行中禁用态由 P5_DEBUG.syncButtons 管理）
    if (el.debugBtn) el.debugBtn.hidden = !(state.dataUrl || state.ocrText);
    // 分级方案：当前轮有修复代码且空闲
    if (el.plansBtn) {
      var cur = currentRound();
      el.plansBtn.hidden = !(cur && cur.result && cur.result.code) || dbg.active;
      el.plansBtn.disabled = state.running || state.runBusy || batch.running;
    }
  }
  P5_DEBUG.wire();
  P5Plans.wire();

  /* 既有流程与 P5 的互斥/复位（零侵入观察，范式同 P4 :3944-3950 MutationObserver / 轮询兜底）：
     - 并发互斥由既有三道闸逐字保证（runPipeline :2113 / continueFix :2018 / runBatchFiles :2829 均先查
       state.running——本模块排查期置位该闸，故排查中单次修复/继续修复/批量全部被既有逻辑拒绝，红线5 达成；
       反向「修复先跑」同理被 state.running 拒绝，见 start() 前置检查），故观察器不得反向终止排查；
     - roundsWrap 清空（restartRounds/新轮/清空重开）→ 同步复位方案/风险面板；
     - AGI 独立任务态运行于 iframe 页面实例，完全隔离于本模块（红线5）。 */
  setInterval(function () {
    try {
      p5SyncButtons();
      // 轮次为空 → 方案/风险面板复位（历史回看/清空重开场景）
      if (!state.rounds.length && !state.viewing) {
        if (!dbg.active) { P5Plans.hide(); if (rPanel) rPanel.hidden = true; }
      }
    } catch (e) { /* 观察器异常绝不阻断既有流程 */ }
  }, 600);

  /* 自动风险自检：结果区渲染后（MutationObserver）对当前轮修复代码纯前端扫描，零模型调用 */
  var p5RiskTimer = null;
  function scheduleAutoRisk() {
    if (riskAuto && !riskAuto.checked) return;
    clearTimeout(p5RiskTimer);
    p5RiskTimer = setTimeout(function () {
      try {
        var cur = currentRound();
        if (cur && cur.result && cur.result.code) renderRiskToPanel(cur.result.code, cur.result.lang, '第' + cur.n + '轮修复代码');
      } catch (e) { /* 不阻断 */ }
    }, 800);
  }
  if (el.roundsWrap && typeof MutationObserver === 'function') {
    new MutationObserver(scheduleAutoRisk).observe(el.roundsWrap, { childList: true, subtree: true });
  }
  riskAuto && riskAuto.addEventListener('change', scheduleAutoRisk);

  /* 自动化钩子（只增不改；冒烟/回归经此注入与断言） */
  window.__iris.p5 = {
    debug: P5_DEBUG, plans: P5Plans, risk: IrisRisk,
    prompts: { enum: buildEnumPrompt, plans: buildPlansPrompt, parseEnum: parseEnumOutput, parseTriPlan: parseTriPlan, recommend: recommendPlan },
    chat: p5Chat, validate: p5Validate, dbg: dbg,
    standalone: standalonePlans, renderRiskToPanel: renderRiskToPanel, state: function () { return P5_DEBUG.snapshot(); }
  };
  log('[P5] 智能排错 Agent 就绪：排查引擎（≤' + DEBUG_MAX_ROUNDS + ' 轮）/ 分级方案 / 风险自检（纯前端）已装配', 'info');
  /* ============================================================
     [P6] 全场景排错覆盖 —— 纯文本日志通道 + 配置文件专项 + 命令行/Shell 专项
     （任务书 P6 阶段2/3/4，docs/p6_audit.md §3/§4）
     ------------------------------------------------------------
     范式：IIFE 尾部纯追加（同 P3/P4/P5），既有代码仅 4 处声明式守卫 G1-G4
       （takeFile / window paste / runBatchFiles / export 命名，各带 [P6 守卫] 注释）；
       p6 未装配或截图通道下守卫全部短路，P0-P5 截图行为逐字不变。
     红线符合性：
       - 零新增后端/零新配额通道：诊断仍走 POST /api/llm/chat（apiChat :302，session_id
         同桶注入 :303；纯文本路径每次分析仅 1 次调用，低于截图路径 OCR+诊断 2 次）；
         执行验证复用既有按钮与降级语义（mapRunLanguage :2187：shell/配置产物非
         python/javascript → 自动静态校验降级，天然满足「危险命令不自动执行」红线）；
       - 纯文本路径 = 截图路径去掉 OCR 段：预处理 → 置位 state.ocrText → 与 stepDiagnose
         同构诊断（输出契约【错误根因】/【修复方案】/【修复代码】，parseResult :1441 兼容）
         → pushRound → 多轮/三档方案/风险自检/智能排查/Diff/导出/AGI 既有能力零改动复用
         （P5 reproduce :5187 优先复用 ocrText，文本态省 1 次模型调用）；
       - 长文本分段复用 mergeSegmentTexts（:1060 行级去重+断行修正）；噪音剥离复用
         stripThinkBlocks（:1048）+ ANSI/时间戳前端正则；分级/致命定位复用 classifyError（:1118）；
       - 规则引擎（YAML/JSON/Dockerfile/.env/Nginx + bash/PowerShell/cmd）全部纯前端确定性
         正则/扫描器：规则命中可修错误零模型调用直接修复（Diff 复用 diffLines :1514），
         未覆盖结构性问题走模型兜底（专项附录注入 buildDiagPrompt 产物，契约不变）；
       - 用户文本/LLM 内容一律 textContent 渲染（零注入面，沿用既有规范）。
     自动化钩子：window.__iris.p6 = {…}（只增不改）。
     ============================================================ */
  var p6 = (function () {
    'use strict';

    /* ---------------- 参数（预算/规模保护，与 P2 长图分段语义对齐） ---------------- */
    var TEXT_MAX_CHARS = 500000;         // 输入上限 50 万字符（≈2MB 文本）
    var TEXT_SEG_LINES = 200;            // 长文本分段：段行数（任务书 5.1：>200 行构建日志触发）
    var TEXT_SEG_OVERLAP = MERGE_WINDOW; // 重叠行数 = MERGE_WINDOW(12)，保证 mergeSegmentTexts 完整去重
    var TEXT_DIAG_FOCUS_MAX = 6500;      // 诊断 prompt 聚焦文本上限（单次调用 token 不超截图通道）
    var TEXT_PNG_MAX_LINES = 2200;       // 转截图存档渲染行数上限

    /* ---------------- DOM（index.html [P6] 段；缺失时模块静默降级为截图专用） ---------------- */
    var ta = $('p6-text'), tPanel = $('p6-text-panel');
    var tabImg = $('p6-tab-image'), tabText = $('p6-tab-text');
    var elStatus = $('p6-status'), elFindings = $('p6-findings'), elRuleBar = $('p6-rulebar'), elDiff = $('p6-diff');
    var btnAnalyze = $('p6-analyze-btn'), btnLoadOcr = $('p6-load-ocr-btn'), btnShot = $('p6-shot-btn'), btnClear = $('p6-clear-btn');
    var wired = !!(ta && tabImg && tabText && btnAnalyze);
    if (!wired) log('[P6] 文本通道 DOM 缺失，模块降级（既有截图能力不受影响）', 'info');

    var mode = 'image';          // 活动通道：'image' | 'text'（互斥，不允许同时存在）
    var draft = '';              // 切回截图通道时暂存的草稿（活动输入无残留；切回即恢复）
    var lastKind = null;         // 最近类型判定 {family:'log'|'config'|'shell', ...}
    var lastIssues = [];         // 规则层问题 [{line, cat, msg}]
    var lastFixed = null;        // 规则层确定性修复后的完整文本（config 专项）
    var lastFixedCount = 0;
    var lastDanger = false;      // 危险命令模式命中

    /* ============================================================
       阶段2 · 输入通道与互斥
       ============================================================ */
    function isTextMode() { return mode === 'text'; }
    function renderTabs() {
      if (!tabImg || !tabText) return;
      var t = mode === 'text';
      tabImg.classList.toggle('is-active', !t); tabText.classList.toggle('is-active', t);
      tabImg.setAttribute('aria-selected', t ? 'false' : 'true');
      tabText.setAttribute('aria-selected', t ? 'true' : 'false');
    }
    function setMode(m) {
      if (m !== 'text' && m !== 'image') return;
      if (!wired || m === mode) return;
      if (state.running || batch.running || dbg.active) { toast('流程进行中，请稍候'); return; }
      if (m === 'text') {
        if (state.file || state.dataUrl || state.ocrText || state.rounds.length) {
          clearFile();     // 既有函数：移除截图暂存
          restartRounds(); // 既有函数：轮次/OCR/结果复位（互斥：两通道输入不混杂）
          log('[P6] 通道互斥：已清空截图通道暂存（截图/OCR/轮次），切换到粘贴日志通道', 'info');
        }
        mode = 'text';
        ta.value = draft;
        document.body.classList.add('p6-text-mode');
        toast('已切换到「粘贴日志」通道（跳过 OCR）');
        log('[P6] 输入通道 = 粘贴日志：文本 → 预处理 → 诊断 → 修复 → 验证（复用 P2-P5 全链）', 'info');
      } else {
        mode = 'image';
        if (ta.value.trim()) {
          draft = ta.value; ta.value = '';
          log('[P6] 通道互斥：日志草稿已暂存（切回可恢复；活动输入不残留）', 'info');
        } else { ta.value = ''; draft = ''; }
        document.body.classList.remove('p6-text-mode');
        toast('已切换到「上传截图」通道');
      }
      lastKind = null; lastIssues = []; lastFixed = null; lastDanger = false;
      renderTabs(); renderStatusPanel();
    }

    /* G1：takeFile 顶部守卫（dropzone 点击/拖拽、全页拖拽均汇聚于 takeFile——一处守卫覆盖全部入口） */
    function takeFileGuard(file) {
      if (!wired) return false;
      var textFile = /\.(log|txt|err|out|traceback|stack|jsonl)$/i.test(file.name || '') ||
        (file.type || '').indexOf('text/') === 0;
      if (mode === 'text') {
        if (!textFile) { toast('日志通道仅接受 .log/.txt/.err 文本；截图请切回「上传截图」'); return true; }
        loadTextFile(file); return true;
      }
      if (textFile) { // 截图通道拖入文本文件 → 自动切通道并导入（任务书阶段2：拖拽读取内容填入）
        setMode('text');
        if (mode !== 'text') return true; // 进行中拒绝切换 → 文件不落入截图通道
        loadTextFile(file); return true;
      }
      state._p6Text = null; // [P6] 截图通道新输入 → 清除纯文本来源标记（导出命名守卫 G4 联动）
      return false;
    }
    function loadTextFile(file) {
      if (file.size > 4 * 1024 * 1024) { toast('文本文件超过 4MB 上限'); return; }
      var reader = new FileReader();
      reader.onerror = function () { toast('文本读取失败'); };
      reader.onload = function () {
        ta.value = String(reader.result || '').slice(0, TEXT_MAX_CHARS);
        log('[P6] 已导入文本文件「' + (file.name || 'clipboard') + '」（' + formatBytes(file.size) + '）', 'ok');
        btnAnalyze.disabled = !ta.value.trim();
        liveDetect();
      };
      reader.readAsText(file);
    }
    /* G2：window paste 守卫——日志框未聚焦时剪贴板纯文本改道载入（框内粘贴保持浏览器原生行为） */
    function pasteGuard(e) {
      if (!wired || mode !== 'text') return false;
      if (document.activeElement === ta) return false;
      var hasImage = e.clipboardData && e.clipboardData.items &&
        Array.prototype.some.call(e.clipboardData.items, function (it) { return it.type && it.type.indexOf('image/') === 0; });
      if (hasImage) return false; // 交由既有 takeFile/G1 拒绝并提示
      var txt = e.clipboardData && e.clipboardData.getData ? e.clipboardData.getData('text/plain') : '';
      if (!txt || !txt.trim()) return false;
      e.preventDefault();
      ta.value = ((ta.value || '').replace(/\s+$/, '') + (ta.value ? '\n' : '') + String(txt)).slice(0, TEXT_MAX_CHARS);
      ta.focus();
      btnAnalyze.disabled = !ta.value.trim();
      liveDetect();
      log('[P6] 剪贴板文本已载入日志框（' + txt.length + ' 字符）', 'info');
      return true;
    }
    /* ============================================================
       阶段2 · 文本预处理（噪音剥离 / 长文分段——复用 P1/P2 既有函数）
       ============================================================ */
    /* ANSI 转义序列（CSI / OSC）：用字符码构造，规避字面控制字符 */
    var ESC_CH = String.fromCharCode(27), BEL_CH = String.fromCharCode(7);
    var ANSI_CSI_RE = new RegExp(ESC_CH + '\\[[0-9;?]*[ -/]*[@-~]', 'g');
    var ANSI_OSC_RE = new RegExp(ESC_CH + '\\][^' + BEL_CH + ']*(' + BEL_CH + '|' + ESC_CH + '\\\\)', 'g');
    /* 行首时间戳前缀（ISO / [hh:mm:ss] / docker / US 日期 + AM/PM），要求其后仍有正文 */
    var TS_LINE_RE = /^(\[[0-2]?\d:[0-5]\d(?::[0-5]\d)?([.,]\d+)?\]|\d{4}[-\/]\d{1,2}[-\/]\d{1,2}[ T]\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?([Zz]|[+-]\d{2}:?\d{2})?|\d{1,2}\/\d{1,2}\/\d{2,4}\s+\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm]\.?)?)(\s*[-|>:\s]+)(?=\S)/;

    /* 噪音剥离：CRLF 归一 → ANSI → 思考块（stripThinkBlocks :1048 复用）→ 时间戳前缀 */
    function stripNoise(text) {
      var s = String(text || '').replace(/\r\n?/g, '\n');
      var removed = { ansi: 0, think: 0, ts: 0 };
      var pre = s;
      s = s.replace(ANSI_CSI_RE, '').replace(ANSI_OSC_RE, '');
      removed.ansi = (pre.length - s.length) > 0 ? Math.round((pre.length - s.length) / 4) : 0; // 每 4 字符近似 1 处，日志用途
      var t1 = s;
      s = stripThinkBlocks(s); // 既有函数（P2 :1048）：成对  块与孤儿标记行
      if (s !== t1) removed.think = 1;
      s = s.split('\n').map(function (line) {
        if (!TS_LINE_RE.test(line)) return line;
        removed.ts++;
        return line.replace(TS_LINE_RE, '');
      }).join('\n');
      s = s.replace(/\n{4,}/g, '\n\n\n');
      return { text: s.trim(), removed: removed };
    }

    /* 长文本分段（文本版 P2 长图分段）：固定行窗 + 重叠 = MERGE_WINDOW；
       合并去重复用 mergeSegmentTexts（:1060，行级最长公共首尾块 + 断行修正） */
    function segmentTextLines(text) {
      var lines = String(text || '').split('\n');
      var parts = [];
      if (lines.length <= TEXT_SEG_LINES) return { parts: [String(text)], total: lines.length, segs: 1 };
      var step = TEXT_SEG_LINES - TEXT_SEG_OVERLAP;
      for (var i = 0; i < lines.length; i += step) {
        parts.push(lines.slice(i, i + TEXT_SEG_LINES).join('\n'));
        if (i + TEXT_SEG_LINES >= lines.length) break;
      }
      return { parts: parts, total: lines.length, segs: parts.length };
    }
    function mergeTextSegments(parts) { return mergeSegmentTexts(parts); }

    /* ============================================================
       阶段2/3/4 · 输入类型判定（纯文本→日志 / 配置 / 命令行；失败默认通用）
       ============================================================ */
    var FMT_LABEL = { python: 'Python Traceback', java: 'Java Stack', node: 'Node.js 报错', compile: '编译日志', custom: '自定义日志' };
    var CFG_LABEL = { yaml: 'YAML', json: 'JSON', dockerfile: 'Dockerfile', env: '.env', nginx: 'Nginx' };
    var SHELL_LABEL = { bash: 'bash/zsh（Shell）', powershell: 'PowerShell', cmd: 'cmd（Windows 批处理）' };

    function detectLogFmt(text) {
      var s = String(text || '');
      if (/Traceback \(most recent call last\)/.test(s) || /^\s*File "[^"]+", line \d+/m.test(s)) return 'python';
      if (/Exception in thread|^\s*at (?:[\w$.]+\.)+[\w$<>]+\(/m.test(s) || /\b(?:NullPointerException|StackOverflowError|OutOfMemoryError|ClassCastException|ArrayIndexOutOfBoundsException|NoSuchMethodException|IllegalArgumentException)\b/.test(s)) return 'java';
      if (/node:internal|MODULE_NOT_FOUND|npm ERR!|at Object\.<anonymous>|UnhandledPromiseRejection/.test(s)) return 'node';
      if (/error\[E\d+\]|undefined reference to|collect2: error|gcc:\s*error|c\+\+:\s*error|ld: (?:error|symbol)|BUILD FAILED|BUILD FAILURE|\*\*\* \[[^\]]*\] Error|compilation (?:error|failed)|error: expected/i.test(s)) return 'compile';
      return 'custom';
    }

    /* 配置类型识别（内容特征驱动；识别失败返回 null → 通用模式） */
    function detectConfigType(text) {
      var s = String(text || '');
      var trimmed = s.trim();
      if (!trimmed) return null;
      if (/^[{[]/.test(trimmed) && /[}\]]$/.test(trimmed)) return 'json';
      var eff = [];
      var all = trimmed.split('\n');
      for (var i = 0; i < all.length; i++) {
        var x = all[i];
        if (x.trim() && !/^\s*[#!]/.test(x)) eff.push(x);
      }
      if (!eff.length) return null;
      var dockerHits = 0, envHits = 0, yamlHits = 0, nginxHits = 0;
      for (var j = 0; j < eff.length; j++) {
        var L = eff[j];
        if (/^(FROM|RUN|CMD|LABEL|MAINTAINER|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b/.test(L)) dockerHits++;
        if (/^[A-Za-z_][A-Za-z0-9_.]*=/.test(L)) envHits++;
        if (/^\s*[\w"'.-]+:(\s|$)/.test(L) || /^\s*-(\s|$)/.test(L) || /^---\s*$/.test(L)) yamlHits++;
        if (/^(server|http|upstream|events|location|map|geo)\b|^\s*(proxy_pass|listen|server_name|root|index)\s/.test(L)) nginxHits++;
      }
      if (dockerHits >= 1 && dockerHits / eff.length >= 0.4) return 'dockerfile';
      if (nginxHits >= 1 && /^\s*(server|http|upstream|events|location|map|geo)\b[^\n]*\{/m.test(trimmed)) return 'nginx';
      if (envHits / eff.length >= 0.6 && envHits >= 2) return 'env';
      if (yamlHits / eff.length >= 0.6 && yamlHits >= 3 && !/^\s*(function|const|var|let|if|for|while|def|class|import)\b/.test(eff[0])) return 'yaml';
      return null;
    }

    /* 命令行环境判定（错误特征 + 提示符特征） */
    function detectShellKind(text) {
      var s = String(text || '');
      if (/CommandNotFoundException|is not recognized as the name of a cmdlet|无法将.+识别为|Cannot bind argument|Get-Command|Set-ExecutionPolicy|about_Execution_Policies|禁止运行脚本|\bPS [A-Za-z]:\\/.test(s)) return 'powershell';
      if (/不是内部或外部命令|系统找不到指定的(?:文件|路径)|命令语法不正确|^\s*[A-Za-z]:\\[^|>]*>/.test(s)) return 'cmd';
      if (/bash:|zsh:|command not found|No such file or directory|Permission denied|syntax error near unexpected token|unbound variable|bad interpreter|sudo:/.test(s)) return 'bash';
      return null;
    }
    function looksLikeShellError(text) {
      return /command not found|No such file or directory|Permission denied|syntax error near unexpected token|unbound variable|CommandNotFoundException|Cannot bind argument|is not recognized|不是内部或外部命令|命令语法不正确|系统找不到|拒绝访问|Access is denied|禁止运行脚本|bad interpreter/i.test(String(text || ''));
    }

    /* 综合判定：强日志格式 → 配置 → 命令行 → 通用日志 */
    function detectKind(text) {
      var fmt = detectLogFmt(text);
      if (fmt !== 'custom') return { family: 'log', fmt: fmt, label: FMT_LABEL[fmt] };
      var cfg = detectConfigType(text);
      if (cfg) return { family: 'config', type: cfg, label: CFG_LABEL[cfg] + ' 配置' };
      if (looksLikeShellError(text)) {
        var sh = detectShellKind(text) || 'bash';
        return { family: 'shell', shell: sh, label: SHELL_LABEL[sh] + ' 报错' };
      }
      return { family: 'log', fmt: 'custom', label: FMT_LABEL.custom };
    }
    /* ============================================================
       阶段3 · 配置文件规则引擎（纯前端确定性检测；任务书阶段3 规则清单）
       统一返回：{ issues:[{line, cat, msg}], fixed, fixedCount, valid }
       fixed = 规则层确定性修复后的完整文本（null = 无可自动修复项）；
       规则未覆盖的结构性问题由 buildDiagPrompt + 专项附录走模型兜底。
       ============================================================ */

    /* ---------------- YAML：Tab缩进 / 冒号缺空格 / 列表符号 / 引号不闭合 / 锚点引用 ---------------- */
    function checkYaml(text) {
      var lines = String(text || '').split('\n');
      var issues = [], out = lines.slice(), fixedN = 0;
      var anchors = {};
      lines.forEach(function (l) { var m = l.match(/&([A-Za-z_][\w.\-]*)/); if (m) anchors[m[1]] = 1; });
      for (var i = 0; i < lines.length; i++) {
        var raw = lines[i], no = i + 1;
        if (!raw.trim() || /^\s*#/.test(raw) || /^\s*---\s*$/.test(raw)) continue;
        /* ① 缩进含 Tab（空格/tab 混用） */
        if (/^\t|^\s+\t/.test(raw)) {
          issues.push({ line: no, cat: 'indent', msg: '缩进含 Tab 制表符（YAML 仅允许空格缩进）' });
          var ws = (raw.match(/^[\t ]*/) || [''])[0];
          out[i] = ws.replace(/\t/g, '  ') + raw.slice(ws.length); fixedN++;
        }
        /* 掩码：完整引号串与非串内容等长占位（保留引号字符与位置），行内注释剔除 */
        var mask = raw.replace(/"[^"\n]*"|'[^'\n]*'/g, function (q) { return q.replace(/[^\s"']/g, '_'); })
          .replace(/#.*$/, '');
        if (/^\s*[|>][-+]?\s*$/.test(raw)) continue; // 块标量头行：后续值为自由文本
        /* ② 冒号后缺空格（key:value；排除值内时间/URL：仅匹配行首键区第一个冒号且其后非空格） */
        var mC = mask.match(/^(\s*(?:[-?]\s+)?[A-Za-z0-9_.\-"']+):(?=[^\s#])/);
        if (mC) {
          issues.push({ line: no, cat: 'colon', msg: '键值冒号后缺少空格（应为 "key: value"）' });
          var idx = mC[0].length - 1;
          out[i] = out[i].slice(0, idx + 1) + ' ' + out[i].slice(idx + 1); fixedN++;
        }
        /* ③ 列表符号错误：-xxx 后无空格（--- 文档头除外） */
        if (/^\s*-(?=[^\s\-])/.test(mask)) {
          issues.push({ line: no, cat: 'dash', msg: '列表符号 "-" 后缺少空格' });
          out[i] = out[i].replace(/^(\s*)-(?=[^\s\-])/, '$1- '); fixedN++;
        }
        /* ④ 引号不闭合（掩码后该行残余双引号为奇数） */
        if ((mask.match(/"/g) || []).length % 2 === 1) issues.push({ line: no, cat: 'quote', msg: '双引号不闭合（本行未配对）' });
        /* ⑤ 锚点引用错误：*alias 无对应 &anchor */
        (mask.match(/\*([A-Za-z_][\w.\-]*)/g) || []).forEach(function (a) {
          var nm = a.slice(1);
          if (!anchors[nm]) issues.push({ line: no, cat: 'anchor', msg: '别名 *' + nm + ' 无对应锚点 &' + nm });
        });
      }
      return { issues: issues, valid: !issues.length, fixed: fixedN ? out.join('\n') : null, fixedCount: fixedN };
    }

    /* ---------------- JSON：字符串感知扫描器（注释/单引号/括号栈/尾逗号/转义/闭合） ---------------- */
    function scanJson(s) {
      var strings = [], cmts = [], stack = [], errs = [], commaIdx = [];
      var i = 0, line = 1;
      while (i < s.length) {
        var c = s[i];
        if (c === '\n') { line++; i++; continue; }
        if (c === '"' || c === "'") {
          var q = c, a = i, sl = line, closed = false;
          i++;
          while (i < s.length) {
            var d = s[i];
            if (d === '\\') { i += 2; continue; }
            if (d === '\n') break;
            if (d === q) { closed = true; i++; break; }
            i++;
          }
          strings.push({ a: a, b: i, quote: q, line: sl, closed: closed });
          if (!closed) errs.push({ line: sl, cat: 'quote', msg: '字符串引号未闭合（' + q + '）' });
          continue;
        }
        if (c === '/' && s[i + 1] === '/') {
          var a1 = i; while (i < s.length && s[i] !== '\n') i++;
          cmts.push({ a: a1, b: i, line: line }); continue;
        }
        if (c === '/' && s[i + 1] === '*') {
          var a2 = i, sl2 = line, j = s.indexOf('*/', i);
          var b2 = j < 0 ? s.length : j + 2;
          for (var k = a2; k < b2 && k < s.length; k++) if (s[k] === '\n') line++;
          cmts.push({ a: a2, b: b2, line: sl2, unterminated: j < 0 });
          i = b2; continue;
        }
        if (c === '{' || c === '[') { stack.push({ c: c, line: line }); i++; continue; }
        if (c === '}' || c === ']') {
          var t = i - 1;
          while (t >= 0 && /\s/.test(s[t])) t--;
          var inStr = false;
          for (var si = 0; si < strings.length; si++) { if (strings[si].a < t && t < strings[si].b) { inStr = true; break; } }
          if (t >= 0 && s[t] === ',' && !inStr) commaIdx.push(t);
          var top = stack.pop();
          if (!top) errs.push({ line: line, cat: 'bracket', msg: '多余的闭合括号 "' + c + '"（无对应开括号）' });
          else if ((top.c === '{') !== (c === '}')) errs.push({ line: line, cat: 'bracket', msg: '括号不匹配：第 ' + top.line + ' 行 "' + top.c + '" 遇到 "' + c + '"' });
          i++; continue;
        }
        i++;
      }
      stack.forEach(function (op) { errs.push({ line: op.line, cat: 'bracket', msg: '"' + op.c + '" 未闭合（缺匹配 ' + (op.c === '{' ? '}' : ']') + '）' }); });
      return { strings: strings, cmts: cmts, errs: errs, commaIdx: commaIdx };
    }
    function checkJson(text) {
      var s = String(text || '');
      var sc = scanJson(s);
      var issues = sc.errs.slice();
      var parseOk = true, parseErr = '';
      try { JSON.parse(s); } catch (e) { parseOk = false; parseErr = String((e && e.message) || e); }
      sc.cmts.forEach(function (cm) {
        issues.push({ line: cm.line, cat: 'comment', msg: '注释残留（' + (s.substr(cm.a, 2) === '//' ? '//' : '/* */') + '）——标准 JSON 不支持注释' });
      });
      sc.strings.forEach(function (st) {
        if (st.quote === "'") issues.push({ line: st.line, cat: 'squote', msg: '使用了单引号字符串（标准 JSON 仅允许双引号）' });
        var body = s.slice(st.a + 1, st.closed ? st.b - 1 : st.b);
        var re = /\\./g, m2;
        while ((m2 = re.exec(body))) {
          var cc = m2[0][1];
          if ('"\\/bfnrt'.indexOf(cc) === -1) {
            if (!(cc === 'u' && /^[0-9a-fA-F]{4}/.test(body.slice(m2.index + 2)))) issues.push({ line: st.line, cat: 'escape', msg: '非法转义 "\\ ' + cc + '"（JSON 仅允许 " \\ / b f n r t uXXXX）' });
          }
        }
      });
      sc.commaIdx.forEach(function (pos) {
        issues.push({ line: s.slice(0, pos).split('\n').length, cat: 'trailing-comma', msg: '闭合括号前的尾逗号（JSON 不允许）' });
      });
      var dedup = {}, iss2 = [];
      issues.forEach(function (x) { var k = x.line + '|' + x.cat; if (!dedup[k]) { dedup[k] = 1; iss2.push(x); } });
      issues = iss2;
      /* 确定性修复管线：去注释 → 去尾逗号 →（全文无双引号时）单引号→双引号 */
      var fixed = null, fixedN = 0;
      if (!parseOk) {
        var arr = s.split('');
        sc.cmts.forEach(function (cm) { for (var k = cm.a; k < cm.b && k < arr.length; k++) if (arr[k] !== '\n') arr[k] = ' '; });
        var s2 = arr.join('');
        var sc2 = scanJson(s2);
        var arr2 = s2.split('');
        sc2.commaIdx.forEach(function (pos) { arr2[pos] = ' '; });
        var s3 = arr2.join('');
        var sc3 = scanJson(s3);
        if (s3.indexOf('"') === -1) {
          var arr3 = s3.split('');
          sc3.strings.forEach(function (st) {
            if (st.quote === "'" && st.closed) {
              var inner = s3.slice(st.a + 1, st.b - 1);
              if (inner.indexOf('"') === -1) { arr3[st.a] = '"'; arr3[st.b - 1] = '"'; }
            }
          });
          s3 = arr3.join('');
        }
        try { JSON.parse(s3); fixed = s3; fixedN = sc.cmts.length + sc.commaIdx.length + sc.strings.filter(function (x) { return x.quote === "'"; }).length; } catch (e3) { fixed = null; }
      }
      return { issues: issues, valid: parseOk && !issues.length, parseOk: parseOk, fixed: fixed, fixedCount: fixedN };
    }
    /* ---------------- Dockerfile：指令拼写 / FROM 位置 / ENV·ARG 语法 / 镜像标签 / 续行 ---------------- */
    var DOCKER_KNOWN = ['FROM', 'RUN', 'CMD', 'LABEL', 'MAINTAINER', 'EXPOSE', 'ENV', 'ADD', 'COPY', 'ENTRYPOINT', 'VOLUME', 'USER', 'WORKDIR', 'ARG', 'ONBUILD', 'STOPSIGNAL', 'HEALTHCHECK', 'SHELL'];
    function editDistance(a, b) {
      a = String(a); b = String(b);
      var m = a.length, n = b.length, prev = [], cur = [], i, j;
      for (j = 0; j <= n; j++) prev[j] = j;
      for (i = 1; i <= m; i++) {
        cur[0] = i;
        for (j = 1; j <= n; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1));
        prev = cur.slice();
      }
      return prev[n];
    }
    function closestKnown(word, list) {
      var best = null, bd = 3;
      list.forEach(function (k) { var d = editDistance(word.toLowerCase(), k.toLowerCase()); if (d < bd) { bd = d; best = k; } });
      return best;
    }
    function checkDockerfile(text) {
      var lines = String(text || '').split('\n');
      var issues = [], out = lines.slice(), fixedN = 0;
      var cont = false, firstInstr = -1, firstWord = '';
      for (var i = 0; i < lines.length; i++) {
        var raw = lines[i], t = raw.trim(), no = i + 1;
        var isCont = cont;
        cont = t && !/^^#/.test(t) && /\\$/.test(t);
        if (!t || /^#/.test(t)) continue;
        if (isCont) continue; // 上一指令的续行参数
        var mK = t.match(/^([A-Za-z][A-Za-z0-9_-]*)/);
        if (!mK) { issues.push({ line: no, cat: 'parse', msg: '行首无法解析为指令（检查残留引号/缩进）' }); continue; }
        var kw = mK[1], up = kw.toUpperCase();
        if (DOCKER_KNOWN.indexOf(up) === -1) {
          if (kw === up) {
            var best = closestKnown(up, DOCKER_KNOWN);
            if (best) { issues.push({ line: no, cat: 'spell', msg: '指令拼写错误 "' + kw + '"（应为 ' + best + '）' }); out[i] = raw.replace(kw, best); fixedN++; }
            else issues.push({ line: no, cat: 'unknown', msg: '未知指令 "' + kw + '"（非 Dockerfile 有效指令）' });
          } else {
            issues.push({ line: no, cat: 'case', msg: '指令 "' + kw + '" 建议使用大写（' + up + '）' });
            out[i] = raw.replace(kw, up); fixedN++;
          }
        } else {
          if (kw !== up) { issues.push({ line: no, cat: 'case', msg: '指令 "' + kw + '" 建议使用大写（' + up + '）' }); out[i] = raw.replace(kw, up); fixedN++; }
          if (firstInstr < 0 && up !== 'ARG') { firstInstr = no; firstWord = up; }
        }
        var mEnv = raw.match(/^(\s*ENV\s+)([A-Za-z_]\w*)([ \t]+)([^=\r\n].*)$/i);
        if (mEnv && raw.indexOf('=') === -1) {
          issues.push({ line: no, cat: 'env', msg: 'ENV 旧式空格语法（现代写法：ENV key=value）' });
          out[i] = mEnv[1] + mEnv[2] + '=' + mEnv[4].replace(/\s+$/, ''); fixedN++;
        }
        var mArg = raw.match(/^(\s*ARG\s+)([A-Za-z_]\w*)(\s+)=(\s*)(.*)$/);
        if (mArg) { issues.push({ line: no, cat: 'arg', msg: 'ARG 等号两侧含空格' }); out[i] = mArg[1] + mArg[2] + '=' + mArg[5]; fixedN++; }
        if (up === 'FROM') {
          var mF = raw.match(/^\s*FROM\s+(?:--\S+[ \t]+)*(\S+)/i);
          if (mF && mF[1].indexOf(':') === -1 && mF[1].indexOf('$') !== 0 && mF[1].toLowerCase() !== 'scratch') {
            issues.push({ line: no, cat: 'tag', msg: '镜像 "' + mF[1] + '" 未显式指定标签（隐式 :latest，存在版本漂移风险）' });
          }
        }
      }
      if (firstInstr >= 0 && !/^\s*FROM\b/i.test(lines[firstInstr - 1] || '')) {
        issues.push({ line: firstInstr, cat: 'from-order', msg: '首条构建指令应为 FROM（当前为 ' + firstWord + '；其前仅允许注释与 ARG）' });
      }
      for (var j = 0; j < lines.length; j++) {
        var lt = lines[j];
        if (/(&&|\|\|)\s*$/.test(lt.trim()) && !/\\\s*$/.test(lt)) issues.push({ line: j + 1, cat: 'continue', msg: '行以 && / || 结尾但未加续行符 "\\"' });
      }
      return { issues: issues, valid: !issues.length, fixed: fixedN ? out.join('\n') : null, fixedCount: fixedN };
    }

    /* ---------------- .env：等号两侧空格 / 值未引号 / 特殊字符截断 / 变量引用 ---------------- */
    function checkEnv(text) {
      var lines = String(text || '').split('\n');
      var issues = [], out = lines.slice(), fixedN = 0;
      var defined = {};
      lines.forEach(function (l) { var m = l.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/); if (m) defined[m[1]] = 1; });
      for (var i = 0; i < lines.length; i++) {
        var raw = lines[i], no = i + 1;
        if (!raw.trim() || /^\s*#/.test(raw)) continue;
        var m = raw.match(/^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*)(=)([\s\S]*)$/);
        if (!m) {
          var mSep = raw.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*):(\S.*)$/);
          if (mSep) {
            issues.push({ line: no, cat: 'sep', msg: '用 ":" 作分隔符（.env 必须用 "="）' });
            out[i] = mSep[1] + mSep[2] + '=' + mSep[4]; fixedN++;
          } else issues.push({ line: no, cat: 'assign', msg: '非 KEY=VALUE 行（键名非法或缺 "="）' });
          continue;
        }
        if (m[3]) issues.push({ line: no, cat: 'space', msg: '"=" 左侧含空格（解析后键名带空格，多数加载器读取失败）' });
        var val = m[5], vt = val.trim();
        if (!m[3] && /^\s+\S/.test(val) && !/^["']/.test(vt) && vt !== '') {
          issues.push({ line: no, cat: 'space', msg: '"=" 右侧空格（未加引号的值可能被解析器裁剪）' });
        }
        var quoted = vt.length >= 2 && /^["'].*["']$/.test(vt);
        var newLine = m[1] + m[2] + '=' + vt;
        if (!quoted && vt !== '') {
          if (/\s/.test(vt)) {
            issues.push({ line: no, cat: 'quote', msg: '值含空格但未用引号包裹（建议加双引号）' });
            newLine = m[1] + m[2] + '="' + vt.replace(/"/g, '\\"') + '"';
          } else if (vt.indexOf('#') !== -1) {
            issues.push({ line: no, cat: 'hash', msg: '未引号值含 "#"（会被当行内注释截断）' });
            newLine = m[1] + m[2] + '="' + vt + '"';
          }
        }
        if (newLine !== raw) { out[i] = newLine; fixedN++; }
        var refs = val.match(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g) || [];
        refs.forEach(function (r) {
          var nm = r.replace(/[${}]/g, '');
          if (!defined[nm] && !/^(PATH|HOME|USER|LANG|PWD|SHELL|TMP|TEMP|JAVA_HOME|GOPATH|NODE_ENV)$/i.test(nm)) {
            issues.push({ line: no, cat: 'ref', msg: '引用未定义变量 ' + r + '（值将展开为空）' });
          }
        });
      }
      return { issues: issues, valid: !issues.length, fixed: fixedN ? out.join('\n') : null, fixedCount: fixedN };
    }

    /* ---------------- Nginx：缺分号 / 括号不匹配 / 指令拼写 / location 匹配规则 ---------------- */
    var NGINX_BLOCKS = ['server', 'http', 'upstream', 'events', 'location', 'types', 'map', 'geo', 'if'];
    var NGINX_KNOWN = ('server listen server_name root index location if set return rewrite proxy_pass proxy_set_header ' +
      'proxy_redirect proxy_http_version proxy_buffering proxy_connect_timeout proxy_send_timeout proxy_read_timeout ' +
      'upstream keepalive keepalive_requests keepalive_timeout events worker_processes worker_connections http include ' +
      'deny allow auth_basic auth_basic_user_file try_files fastcgi_pass fastcgi_param fastcgi_index ' +
      'fastcgi_split_path_info gzip gzip_types gzip_min_length client_max_body_size access_log error_log types ' +
      'default_type charset sendfile tcp_nopush ssl ssl_certificate ssl_certificate_key ssl_protocols ssl_ciphers ' +
      'ssl_prefer_server_ciphers ssl_session_cache ssl_session_timeout add_header expires map geo limit_req_zone ' +
      'limit_req limit_conn split_clients valid_referers log_format open_file_cache real_ip_header set_real_ip_from').split(' ');
    function checkNginx(text) {
      var lines = String(text || '').split('\n');
      var issues = [], out = lines.slice(), fixedN = 0, stack = [];
      function nextCode(idx) {
        for (var k = idx + 1; k < lines.length; k++) {
          var t = lines[k].trim().replace(/("[^"]*"|'[^']*')/g, ' Q ').replace(/#.*$/, '').trim();
          if (t) return t;
        }
        return '';
      }
      for (var i = 0; i < lines.length; i++) {
        var raw = lines[i], no = i + 1;
        var t = raw.trim().replace(/("[^"]*"|'[^']*')/g, ' Q ').replace(/#.*$/, '').trim();
        if (!t) continue;
        var opens = (t.match(/\{/g) || []).length, closes = (t.match(/\}/g) || []).length;
        var mD = t.match(/^([A-Za-z_][\w-]*)/);
        var isBlock = mD && NGINX_BLOCKS.indexOf(mD[1].toLowerCase()) !== -1;
        if (mD && !/[{}]/.test(t)) {
          var d = mD[1].toLowerCase();
          var known = NGINX_KNOWN.indexOf(d) !== -1;
          if (!known && t.split(/\s+/).length >= 2) { // 指令拼写（与分号无关，先判）
            var best = closestKnown(d, NGINX_KNOWN);
            issues.push({ line: no, cat: 'directive', msg: '未知指令 "' + mD[1] + '"' + (best ? '（疑似拼写错误：' + best + '）' : '（或为多行指令续行）') });
          } else if (known && !isBlock && !/;$/.test(t)) {
            var nx = nextCode(i);
            var newStmt = /^[A-Za-z_][\w-]*[\s;]/.test(nx) || nx.charAt(0) === '}';
            if (newStmt && t.split(/\s+/).length >= 2) {
              issues.push({ line: no, cat: 'semicolon', msg: '指令 "' + mD[1] + '" 语句缺少结尾分号' });
              out[i] = raw.replace(/\s+$/, '') + ';'; fixedN++;
            }
          }
        }
        if (mD && /^location$/i.test(mD[1]) && !/\{/.test(t)) {
          if (nextCode(i).charAt(0) !== '{') issues.push({ line: no, cat: 'location', msg: 'location 块未以 "{" 开始（匹配规则不完整）' });
        }
        for (var a = 0; a < opens; a++) stack.push({ line: no });
        for (var b = 0; b < closes; b++) {
          if (!stack.length) issues.push({ line: no, cat: 'brace', msg: '多余 "}"（无对应 "{" 块）' });
          else stack.pop();
        }
      }
      stack.forEach(function (s0) { issues.push({ line: s0.line, cat: 'brace', msg: '"{" 块缺少闭合 "}"' }); });
      return { issues: issues, valid: !issues.length, fixed: fixedN ? out.join('\n') : null, fixedCount: fixedN };
    }
    var CONFIG_CHECKS = { yaml: checkYaml, json: checkJson, dockerfile: checkDockerfile, env: checkEnv, nginx: checkNginx };
    /* ============================================================
       阶段4 · 命令行/Shell 规则引擎（bash/zsh · PowerShell · cmd）
       ============================================================ */
    var SHELL_PATTERNS = [
      { key: 'not-found', re: /command not found|is not recognized as (?:an internal or external command|the name of a cmdlet|term)|不是内部或外部命令|无法将.+识别为/i, label: '命令未找到', cause: '命令未安装、不在 PATH，或拼写/包管理器差异', fix: '先定位再安装：bash/zsh→command -v X 或 which X；PowerShell→Get-Command X；cmd→where X。未安装按平台包管理器装（Linux: apt/dnf/pacman；macOS: brew；Windows: winget/choco）；仅 PATH 缺失则把安装目录加入 PATH 或用绝对路径调用' },
      { key: 'no-file', re: /No such file or directory|系统找不到指定的(?:文件|路径)|Cannot find path|ObjectNotFound.*Path/i, label: '路径不存在', cause: '路径拼写错误 / 工作目录错位 / 空格未加引号', fix: 'pwd（PS: Get-Location）核对当前目录；改用绝对路径或 ./ 前缀；含空格路径必须加引号。cmd 跨盘切换需 cd /d D:\\path' },
      { key: 'perm', re: /Permission denied|Access is denied|拒绝访问|EACCES|Operation not permitted/i, label: '权限不足', cause: '当前用户对目标文件/端口/进程无权限', fix: 'Linux/macOS：chmod +x（执行位）或按需 sudo；Windows：以管理员身份运行，或检查文件占用与 ACL。端口占用先排查：lsof -i :PORT（bash）/ Get-NetTCPConnection -LocalPort PORT（PS）/ netstat -ano | findstr :PORT（cmd）' },
      { key: 'syntax', re: /syntax error near unexpected token|unexpected token|命令语法不正确|意外的标记|表达式或语句中包含意外/i, label: 'Shell 语法错误', cause: '括号/引号未闭合，或特殊字符（( ) ; | & $）未加引号', fix: '定位报错 token 及前一行；给含特殊字符的参数加引号；bash 脚本可本地 bash -n 语法检查，PowerShell 用 [System.Management.Automation.PSParser]::Tokenize 检查' },
      { key: 'unbound', re: /unbound variable|undefined variable/i, label: '环境变量未定义', cause: 'set -u 下引用未赋值变量，或导出遗漏', fix: '赋默认值 "${VAR:-default}" 或先 export VAR=...；PowerShell：if (-not $env:VAR) { ... }；cmd：echo %VAR% 原样回显即未展开' },
      { key: 'ps-bind', re: /Cannot bind argument|ParameterArgumentValidation|无法将参数绑定/i, label: 'PowerShell 参数绑定失败', cause: '管道对象为 null / 类型不符 / 参数名拼写错误', fix: '检查管道对象非空；Get-Help X -Parameter * 核对参数名；必要时显式 [string]/[int] 转换' },
      { key: 'ps-exec', re: /无法加载文件.*因为在此系统上禁止运行脚本|PSSecurityException|about_Execution_Policies/i, label: 'PowerShell 执行策略限制', cause: 'ExecutionPolicy 阻止本地脚本运行', fix: 'Set-ExecutionPolicy -Scope CurrentUser RemoteSigned（用户级，无需管理员）；或临时 powershell -ExecutionPolicy Bypass -File x.ps1（风险自担）' },
      { key: 'interp', re: /bad interpreter|env: .+: No such file or program|cannot execute binary file/i, label: '解释器缺失/不兼容', cause: 'shebang 指向的解释器不存在、版本不符或架构不匹配', fix: 'which node/python3/bash 核对解释器；用版本管理器（nvm/pyenv）安装切换；Windows 注意 WSL 与 Git Bash 差异' }
    ];
    /* 危险命令黑名单（任务书阶段4：仅语法/逻辑校验，绝不自动执行） */
    var DANGER_PATS = [
      { re: /\brm\s+(-[a-z]+\s+)*-[a-z]*r[a-z]*f/i, tip: 'rm 递归强制删除' },
      { re: /\brm\s+(-[a-z]+\s+)*-[a-z]*f[a-z]*r/i, tip: 'rm 强制递归删除' },
      { re: /\brm\s+-[rf]+\s+[/\\]/i, tip: 'rm 针对根目录' },
      { re: /\bmkfs(\.\w+)?\b/i, tip: 'mkfs 格式化文件系统' },
      { re: /\bdd\s+if=/i, tip: 'dd 裸盘写入' },
      { re: /:\(\)\s*\{\s*:\|:\s*\}\s*;?\s*:/, tip: 'fork 炸弹' },
      { re: /\bdel\s+\/[fsq]/i, tip: 'del 强制/递归删除' },
      { re: /\brd\s+\/s/i, tip: 'rd 递归删除' },
      { re: /\bformat\s+[a-z]:/i, tip: 'format 格式化磁盘' },
      { re: /\bdiskpart\b/i, tip: 'diskpart 磁盘分区' },
      { re: /\bshutdown\b/i, tip: 'shutdown 关机' },
      { re: /\breg\s+delete\b/i, tip: 'reg delete 删除注册表' },
      { re: /\bnet\s+user\s+\S+\s+\/del/i, tip: '删除系统用户' },
      { re: /\bkill\s+-9\s+1\b/, tip: '杀 init/PID1' },
      { re: />\s*\/dev\/sd[a-z]/i, tip: '重定向覆盖裸盘' },
      { re: /\bchmod\s+(-[a-z]+\s+)*777\s+[/\\]/i, tip: 'chmod 777 根目录' }
    ];
    function dangerScan(text) {
      var hits = [];
      DANGER_PATS.forEach(function (p) { if (p.re.test(String(text || ''))) hits.push(p.tip); });
      return hits;
    }
    function checkShell(text) {
      var lines = String(text || '').split('\n');
      var issues = [], hitKeys = {};
      lines.forEach(function (l, i) {
        SHELL_PATTERNS.forEach(function (p) {
          if (p.re.test(l) && !hitKeys[p.key]) {
            hitKeys[p.key] = 1;
            issues.push({ line: i + 1, cat: p.key, msg: p.label + '｜原因：' + p.cause + '｜修复：' + p.fix });
          }
        });
      });
      return { issues: issues, shell: detectShellKind(text) || 'bash', danger: dangerScan(text) };
    }
    /* 命令/脚本前端语法校验（零模型零执行；引号/括号/管道结构平衡 + 危险命令拦截） */
    function shellSyntaxCheck(cmd) {
      var s = String(cmd || '').replace(/\r\n?/g, '\n');
      if (!s.trim()) return { ok: false, danger: false, why: '空命令，无可校验内容' };
      var dang = dangerScan(s);
      if (dang.length) return { ok: false, danger: true, why: '命中危险命令黑名单（' + dang.join('、') + '）——仅做语法/逻辑校验，绝不自动执行；如需执行请人工二次确认后自行操作' };
      var lines = s.split('\n'), q = null, par = 0, bk = 0, cu = 0, i, c;
      for (i = 0; i < s.length; i++) {
        c = s.charAt(i);
        if (q) { if (c === '\\') { i++; continue; } if (c === q) q = null; continue; }
        if (c === "'" || c === '"' || c === '`') { q = c; continue; }
        if (c === '(') par++; else if (c === ')') par--; else if (c === '{') bk++; else if (c === '}') bk--;
        else if (c === '[') cu++; else if (c === ']') cu--;
        if (par < 0 || bk < 0 || cu < 0) break;
      }
      if (q) return { ok: false, danger: false, why: '引号未闭合（' + q + '）' };
      if (par !== 0 || bk !== 0 || cu !== 0) return { ok: false, danger: false, why: '括号配对异常：( ) 差 ' + par + '，{ } 差 ' + bk + '，[ ] 差 ' + cu };
      for (i = 0; i < lines.length; i++) {
        if (/\|\s*$/.test(lines[i]) && !/\|\|/.test(lines[i]) && i === lines.length - 1) return { ok: false, danger: false, why: '第 ' + (i + 1) + ' 行以管道符结尾但无后续命令' };
      }
      return { ok: true, danger: false, why: '引号/括号/管道结构校验通过（bash·PowerShell·cmd 通用启发式；未真实执行）' };
    }
    /* 跨 Shell 等价语法对照表（注入模型 Prompt + 前端 findings 展示） */
    var CROSS_SHELL = [
      { op: '查命令是否存在', bash: 'command -v X（或 which X）', powershell: 'Get-Command X', cmd: 'where X' },
      { op: '查看环境变量', bash: 'echo $PATH', powershell: '$env:PATH', cmd: 'echo %PATH%' },
      { op: '设置环境变量（会话）', bash: 'export FOO=bar', powershell: '$env:FOO="bar"', cmd: 'set FOO=bar' },
      { op: '列文件', bash: 'ls -la', powershell: 'Get-ChildItem -Force', cmd: 'dir /a' },
      { op: '搜文本', bash: 'grep -rn "pat" .', powershell: 'Get-ChildItem -Recurse | Select-String "pat"', cmd: 'findstr /S /I "pat" *' },
      { op: '尾随日志', bash: 'tail -n 50 app.log', powershell: 'Get-Content app.log -Tail 50', cmd: 'more +50 app.log（近似，无原生 tail）' },
      { op: '提权执行', bash: 'sudo <cmd>', powershell: 'Start-Process <cmd> -Verb RunAs', cmd: '以管理员身份运行窗口后执行' },
      { op: '多行续接符', bash: '反斜杠 \\', powershell: '反引号 `', cmd: '脱字符 ^' },
      { op: '安装常见工具', bash: 'apt-get install / dnf install / pacman -S（Linux）· brew install（macOS）', powershell: 'winget install X / choco install X', cmd: '同 PowerShell（winget/choco 均可用）' }
    ];
    function crossShellText() {
      return CROSS_SHELL.map(function (r) { return '- ' + r.op + '：bash/zsh → ' + r.bash + ' ｜ PowerShell → ' + r.powershell + ' ｜ cmd → ' + r.cmd; }).join('\n');
    }

    /* ============================================================
       阶段3/4 · 专项 Prompt 附录（注入 buildDiagPrompt 产物；输出契约不变，
       parseResult :1441 仍按【错误根因】/【修复方案】/【修复代码】解析）
       ============================================================ */
    var CFG_FENCE = { yaml: 'yaml', json: 'json', dockerfile: 'dockerfile', env: 'ini', nginx: 'nginx' };
    function configAppendix(type, res) {
      var L = ['', '【P6 配置专项·规则引擎预检】该文本已被规则引擎判定为 ' + CFG_LABEL[type] + ' 文件。'];
      if (res.issues.length) {
        L.push('规则层检出 ' + res.issues.length + ' 个疑点（行号｜类别｜描述），为启发式结果，请逐条核对：');
        res.issues.slice(0, 40).forEach(function (x) { L.push('- 第' + x.line + '行｜' + x.cat + '｜' + x.msg); });
        L.push('确认为误报的请在【修复方案】中说明排除理由；结构性问题（缩进层级/引用链/块顺序）即使规则未命中也必须修复。');
      } else {
        L.push('规则层未检出典型格式错误；若报错指向结构性/语义问题（引用未定义、块顺序、参数越界等），请深入分析。');
      }
      L.push('输出契约补充（不改变分段标记）：【修复代码】必须给出「完整」修复后的 ' + CFG_LABEL[type] + ' 文件（单个 ``` 围栏块，第一行标注 ' + CFG_FENCE[type] + '），不得只给片段或 diff；【修复方案】逐条列出改动位置与依据。');
      return L.join('\n');
    }
    function shellAppendix(info) {
      var L = ['', '【P6 命令行专项·规则引擎预检】报错环境判定：' + SHELL_LABEL[info.shell] + '。'];
      if (info.issues.length) {
        L.push('命中规则（请逐条说明错误原因与修复依据）：');
        info.issues.slice(0, 10).forEach(function (x) { L.push('- 第' + x.line + '行｜' + x.msg); });
      }
      if (info.danger.length) L.push('⚠ 安全约束：文本含危险命令（' + info.danger.join('、') + '）。只允许给出语法/逻辑校验结论、原因解释与更安全替代方案；不得生成可直接一键执行的破坏性命令。');
      L.push('输出契约补充（不改变分段标记）：【修复方案】必须包含「跨 Shell 等价写法」小节——分别给出 bash/zsh、PowerShell、cmd 三种环境的对应语法与差异说明（which/Get-Command/where、export/$env:/set、路径分隔符与引号转义等）；给出正确命令或修正后的完整脚本作为【修复代码】（按判定环境标注 bash/powershell/batch）。');
      L.push('跨 Shell 对照参考：\n' + crossShellText());
      return L.join('\n');
    }
    function logAppendix(k) {
      return '\n【P6 输入说明】本次输入为纯文本日志（' + (FMT_LABEL[k.fmt] || '日志') + '，非截图 OCR），直接基于全文分析。';
    }

    /* 诊断聚焦文本：超长时致命块 ± 上下文 + 首尾保留（classifyError 分级优先） */
    function focusLongText(text) {
      var s = String(text || '');
      if (s.length <= TEXT_DIAG_FOCUS_MAX) return { text: s, omitted: 0 };
      var lines = s.split('\n');
      var ce = classifyError(s);
      var a = Math.max(0, (ce.firstIdx >= 0 ? ce.firstIdx : 0) - 60);
      var b = Math.min(lines.length, a + 300);
      var body = lines.slice(a, b);
      if (a > 0) body.unshift('……（前 ' + a + ' 行省略）……');
      if (b < lines.length) body.push('……（第 ' + (b + 1) + '-' + lines.length + ' 行省略）……');
      return { text: body.join('\n'), omitted: lines.length - (b - a) };
    }
    /* ============================================================
       阶段2 · 分析编排与诊断执行（与 runPipeline 同构；跳过 OCR，置位 state.ocrText 后
       走既有 stepDiagnose 同构链，多轮/方案/排查/风险/Diff/导出全复用）
       ============================================================ */
    function analyzeText(raw) {
      var pre = stripNoise(raw);
      var seg = segmentTextLines(pre.text);
      var merged = seg.segs > 1 ? mergeSegmentTexts(seg.parts) : { text: pre.text, lines: seg.total, dropped: 0, fixedBreaks: 0 };
      var evidence = merged.text;
      var k = detectKind(evidence);
      var ruleRes = null;
      if (k.family === 'config') ruleRes = CONFIG_CHECKS[k.type](evidence);
      else if (k.family === 'shell') ruleRes = checkShell(evidence);
      return {
        clean: evidence, kind: k, ruleRes: ruleRes, removed: pre.removed,
        segs: seg.segs, dropped: merged.dropped, fixedBreaks: merged.fixedBreaks
      };
    }
    function kindSrc(k) {
      if (k.family === 'config') return '粘贴日志（配置·' + CFG_LABEL[k.type] + '）';
      if (k.family === 'shell') return '粘贴日志（命令行·' + SHELL_LABEL[k.shell] + '）';
      return '粘贴日志（' + (FMT_LABEL[k.fmt] || '日志') + '）';
    }

    function runDiagnose() {
      if (state.running) { toast('分析进行中，请稍候'); return; }
      if (batch.running || dbg.active) { toast('批量/排查流程进行中，请稍候'); return; }
      var raw = (ta.value || '');
      if (!raw.trim()) { toast('请先粘贴报错文本'); return; }
      if (raw.length > TEXT_MAX_CHARS) { log('[P6] 文本超长（' + raw.length + ' 字符 > ' + TEXT_MAX_CHARS + '），请截取关键片段', 'fail'); toast('文本超过 50 万字符上限'); return; }
      var a = analyzeText(raw);
      lastKind = a.kind; lastIssues = a.ruleRes ? a.ruleRes.issues : [];
      lastFixed = (a.kind.family === 'config' && a.ruleRes && a.ruleRes.fixed) ? a.ruleRes.fixed : null;
      lastFixedCount = a.ruleRes && a.ruleRes.fixedCount || 0;
      lastDanger = a.kind.family === 'shell' && a.ruleRes ? a.ruleRes.danger.length > 0 : false;
      renderStatusPanel();
      /* 流程复位（镜像 runPipeline :2116-2123 语义） */
      state.running = true;
      state.viewing = null; state.rounds = []; state.roundIdx = 0;
      state.ocrText = ''; state.result = { root: '', plan: '', code: '', lang: '' };
      state.historyId = null; state.diffTab = 'result';
      state.file = null; state.rawCanvas = null; state.procCanvas = null; state.cropRect = null;
      state.dataUrl = null; state.isLongImage = false; state.segments = null;
      state._p6Text = { kind: a.kind.label, at: Date.now() };
      renderResult();
      resetStages(); setStage('prep');
      btnAnalyze.disabled = true;
      el.analyzeLabel.textContent = '诊断中…';
      el.envTag.textContent = '会话 ' + sessionId().slice(5) + ' · 桶内限额 30 次/天（纯文本·跳过 OCR）';
      log('—— [P6] 纯文本通道开始（预处理 → 分级定位 → 诊断 → 验证；跳过 OCR）——', 'info');
      log('[P6] 输入类型判定：' + a.kind.label + '（' + (a.kind.family === 'config' ? '规则先行，模型兜底' : a.kind.family === 'shell' ? '跨 Shell 专项' : '通用诊断链') + '）', 'info');
      var rm = a.removed;
      if (rm.ansi || rm.think || rm.ts) log('[P6] 噪音剥离：ANSI 色码 ~' + rm.ansi + ' 处 · 思考块 ' + (rm.think ? '已剥离' : '无') + ' · 时间戳前缀 ' + rm.ts + ' 行', 'info');
      if (a.segs > 1) log('[P6] 长文本分段：' + a.segs + ' 段（段 ' + TEXT_SEG_LINES + ' 行 / 重叠 ' + TEXT_SEG_OVERLAP + ' 行）→ 合并去重 ' + a.dropped + ' 行 · 修正断行 ' + a.fixedBreaks + ' 处（复用 P2 合并逻辑）', 'info');
      setStage('ocr'); finishRunningLog('ok', '跳过（纯文本直通）');
      state.ocrText = a.clean;
      var ce = classifyError(a.clean);
      var lv = LEVEL_META[ce.level] || LEVEL_META.info;
      log('[P6] 错误分级：' + lv.text + '（致命 ' + ce.fatalCount + ' / 警告 ' + ce.warnCount + ' / 提示 ' + ce.infoCount + ' 行）' + (ce.firstIdx >= 0 ? ' · 致命块起始第 ' + (ce.firstIdx + 1) + ' 行（优先定位）' : ''), 'info');
      var fx = focusLongText(a.clean);
      if (fx.omitted) log('[P6] 诊断聚焦：致命块 ± 上下文优先，省略 ' + fx.omitted + ' 行中性内容（完整文本保留在结果区折叠）', 'info');
      var appendix = a.kind.family === 'config' ? configAppendix(a.kind.type, a.ruleRes)
        : a.kind.family === 'shell' ? shellAppendix(a.ruleRes) : logAppendix(a.kind);
      var features = extractFeatures(fx.text);
      var prompt = buildDiagPrompt(fx.text, features);
      if (appendix) prompt += appendix;
      state._p6Prompt = prompt; // 自动化取证
      var body = Object.assign({ messages: [{ role: 'user', content: prompt }], temperature: 0.2 }, modelField());
      setStage('diag');
      log('纯文本诊断中...（POST /api/llm/chat · 文本通道仅 1 次模型调用' + (a.kind.family !== 'log' ? ' · 含专项附录' : '') + '）', 'running');
      apiChat(body).then(function (j) {
        finishRunningLog('ok', '诊断完成');
        var text = String(j.content || '');
        if (!text.trim()) throw new Error('诊断返回为空');
        var r = parseResult(text);
        pushRound(1, kindSrc(a.kind), r);
        if (a.kind.family === 'shell' && r.code) {
          var syn = shellSyntaxCheck(r.code);
          log('[P6] 修复命令前端语法校验：' + (syn.ok ? '通过' : '未通过') + ' — ' + syn.why, syn.ok ? 'ok' : 'fail');
        }
        return text;
      }, function (e) {
        finishRunningLog('fail', e instanceof BudgetError ? '预算熔断' : '失败');
        if (e instanceof BudgetError) throw e;
        throw new Error('纯文本诊断失败：' + (e && e.message || e));
      }).then(stepZcode).then(function () {
        log('—— [P6] 纯文本诊断结束：可继续「执行验证 / 继续修复 / 生成分级方案 / 智能排查」（全部既有能力）——', 'ok');
      }).catch(function (e) {
        if (e instanceof BudgetError) {
          log('预算熔断：' + e.message + '。请稍后重试（额度跨自然日自动清零），当前日志与结果已保留', 'fail');
          toast('已达调用次数上限，请稍后重试');
        } else {
          log('流程终止：' + (e && e.message || e) + '。当前日志与已生成内容已保留', 'fail');
          toast('流程中断：' + String(e && e.message || e).slice(0, 60));
        }
      }).then(function () {
        state.running = false;
        btnAnalyze.disabled = !(ta.value || '').trim();
        el.analyzeLabel.textContent = state.rounds.length ? '重新分析' : '重试分析';
        updateActions();
      });
    }
    /* ============================================================
       阶段2/3/4 · 通道 UI（状态指示 / 错误行列表与跳转 / 规则修复 + Diff / 互通）
       ============================================================ */
    function liveDetect() {
      var v = (ta.value || '');
      if (!v.trim()) { lastKind = null; lastIssues = []; lastFixed = null; lastFixedCount = 0; lastDanger = false; renderStatusPanel(); btnAnalyze.disabled = true; return; }
      btnAnalyze.disabled = state.running;
      var a = analyzeText(v);
      lastKind = a.kind;
      lastIssues = a.ruleRes ? a.ruleRes.issues : [];
      lastFixed = (a.kind.family === 'config' && a.ruleRes && a.ruleRes.fixed) ? a.ruleRes.fixed : null;
      lastFixedCount = a.ruleRes && a.ruleRes.fixedCount || 0;
      lastDanger = a.kind.family === 'shell' && a.ruleRes ? a.ruleRes.danger.length > 0 : false;
      renderStatusPanel();
    }
    function renderStatusPanel() {
      if (!elStatus || !elFindings || !elRuleBar) return;
      elStatus.innerHTML = ''; elFindings.innerHTML = ''; elRuleBar.innerHTML = '';
      var v = (ta && ta.value) || '';
      if (mode !== 'text' || !v.trim() || !lastKind) {
        elStatus.hidden = true; elFindings.hidden = true; elRuleBar.hidden = true;
        if (elDiff) { elDiff.hidden = true; elDiff.innerHTML = ''; }
        return;
      }
      elStatus.hidden = false;
      var lines = v.split('\n').length;
      elStatus.appendChild(p5El('span', 'p6-chip', lines + ' 行 / ' + v.length + ' 字符'));
      elStatus.appendChild(p5El('span', 'p6-chip p6-chip-kind', '判定：' + lastKind.label));
      if (lastKind.family === 'config') {
        var okN = lastIssues.length === 0;
        elStatus.appendChild(p5El('span', 'p6-chip ' + (okN ? 'p6-ok' : 'p6-bad'),
          okN ? '语法校验：合法 ✓' : '语法校验：' + lastIssues.length + ' 处错误'));
        var lns = lastIssues.slice(0, 12).map(function (x) { return x.line; });
        if (lns.length) elStatus.appendChild(p5El('span', 'p6-lines', '错误行：' + lns.join(', ') + (lastIssues.length > 12 ? ' …' : '')));
      } else if (lastKind.family === 'shell') {
        if (lastDanger) elStatus.appendChild(p5El('span', 'p6-chip p6-bad', '⚠ 危险命令：不自动执行，仅语法/逻辑校验'));
        elStatus.appendChild(p5El('span', 'p6-chip', '规则命中 ' + lastIssues.length + ' 条'));
      }
      if (lastIssues.length) {
        elFindings.hidden = false;
        lastIssues.slice(0, 40).forEach(function (x) {
          var row = p5El('div', 'p6-finding');
          row.appendChild(p5Btn('p6-jump', '第 ' + x.line + ' 行', function () { jumpToLine(x.line); }, '点击跳转定位该行'));
          row.appendChild(p5El('span', 'p6-fmsg', x.msg));
          elFindings.appendChild(row);
        });
        if (lastIssues.length > 40) elFindings.appendChild(p5El('p', 'p6-more', '…共 ' + lastIssues.length + ' 条，仅显示前 40 条'));
      } else elFindings.hidden = true;
      if (lastFixed) {
        elRuleBar.hidden = false;
        elRuleBar.appendChild(p5Btn('primary', '应用规则直接修复（' + lastFixedCount + ' 处 · 零模型调用）', applyRuleFix,
          '规则层确定性修复：直接修正文本并展示前后 Diff，不调用模型（节省预算）'));
        elRuleBar.appendChild(p5El('span', 'p6-rule-hint', '规则未覆盖的结构性问题走「开始诊断」模型兜底'));
      } else elRuleBar.hidden = true;
    }
    function jumpToLine(n) {
      if (!ta) return;
      var arr = ta.value.split('\n'), pos = 0;
      for (var i = 0; i < n - 1 && i < arr.length; i++) pos += arr[i].length + 1;
      ta.focus();
      try { ta.setSelectionRange(pos, pos + (arr[n - 1] || '').length); } catch (e) { /* 旧内核忽略 */ }
      var lh = parseFloat(window.getComputedStyle(ta).lineHeight) || 17;
      ta.scrollTop = Math.max(0, (n - 3) * lh);
    }
    function applyRuleFix() {
      if (!lastFixed || mode !== 'text') return;
      var before = ta.value;
      ta.value = lastFixed;
      var again = lastKind && lastKind.family === 'config' ? CONFIG_CHECKS[lastKind.type](lastFixed) : { issues: [], fixed: null, fixedCount: 0, valid: true };
      var applied = lastIssues.length - again.issues.filter(function (x) { return x.cat !== 'quote'; }).length; // 近似已修计数（展示用）
      lastIssues = again.issues; lastFixed = again.fixed || null; lastFixedCount = again.fixedCount || 0;
      renderFixDiff(before, ta.value);
      renderStatusPanel();
      toast('✓ 规则修复已应用（零模型调用）' + (again.issues.length ? '；剩余 ' + again.issues.length + ' 条走模型兜底' : '；复检通过'));
      log('[P6] 规则层直接修复（' + (lastKind ? lastKind.label : '') + '）：约 ' + Math.max(1, applied) + ' 处已应用并复检' + (again.issues.length ? '，剩余 ' + again.issues.length + ' 条为结构/语义问题（模型兜底）' : '，语法校验通过'), 'ok');
    }
    /* 规则修复前后 Diff（复用 P2 LCS diffLines / 高亮 / 样式段；纯前端零调用） */
    function renderFixDiff(before, after) {
      if (!elDiff) return;
      elDiff.innerHTML = '';
      var head = p5El('div', 'diff-head');
      head.appendChild(p5El('span', 'diff-title', '规则修复对比'));
      head.appendChild(p5El('span', 'diff-src', '修复前 → 修复后（纯前端 LCS · 零模型调用）'));
      head.appendChild(p5Btn('ghost', '复制修复后文本', function () { copyText(after, '修复后文本已复制到剪贴板'); }));
      head.appendChild(p5Btn('ghost', '关闭对比', function () { elDiff.hidden = true; elDiff.innerHTML = ''; }));
      elDiff.appendChild(head);
      var d = diffLines(before, after);
      var lang = (lastKind && lastKind.type) || 'plaintext';
      var cols = document.createElement('div');
      cols.className = 'diff-cols';
      function buildCol(headText, side) {
        var col = p5El('div', 'diff-col');
        col.appendChild(p5El('div', 'diff-col-head', headText));
        var box = p5El('div', 'diff-lines');
        d.rows.forEach(function (row) {
          var cell = row[side];
          var dl = p5El('div', 'dl dl-' + (row.type === 'eq' ? 'eq' : row.type));
          if (cell) {
            dl.appendChild(p5El('span', 'dl-no', String(cell.no)));
            var cs = document.createElement('span'); cs.className = 'dl-code';
            var html = highlightLineHtml(cell.text, lang);
            if (html !== null) cs.innerHTML = html; else cs.textContent = cell.text; // hljs 转义输出，零注入
            dl.appendChild(cs);
          } else {
            dl.appendChild(p5El('span', 'dl-no', ''));
            dl.appendChild(p5El('span', 'dl-pad', ''));
          }
          box.appendChild(dl);
        });
        col.appendChild(box);
        return col;
      }
      cols.appendChild(buildCol('修复前', 'l'));
      cols.appendChild(buildCol('修复后', 'r'));
      elDiff.appendChild(cols);
      var legend = p5El('div', 'diff-legend');
      var cnt = diffCounts(d.rows);
      legend.appendChild(p5El('span', '', '新增 ' + cnt.add + ' · 删除 ' + cnt.del + ' · 修改 ' + cnt.mod));
      if (d.truncated) legend.appendChild(p5El('span', '', '（超长已截断至 ' + DIFF_MAX_LINES + ' 行/侧）'));
      elDiff.appendChild(legend);
      elDiff.hidden = false;
    }

    /* ---------------- 输入互通（任务书阶段2-4） ---------------- */
    /* 截图 OCR 文本 → 载入文本框二次编辑后重新分析 */
    function loadOcrToText() {
      var src = (state.ocrText || (currentRound() && currentRound().ocrText) || '').trim();
      if (!src) { toast('暂无 OCR 文本：请先在「上传截图」通道完成一次识别'); return; }
      if ((ta.value || '').trim() && !window.confirm('日志框已有内容，载入 OCR 文本将覆盖，是否继续？')) return;
      setMode('text');
      if (mode !== 'text') return;
      ta.value = src; draft = '';
      btnAnalyze.disabled = false;
      liveDetect();
      log('[P6] 截图 OCR 文本已载入日志框（' + src.length + ' 字符），可二次编辑后重新诊断', 'info');
    }
    /* 文本 → 一键转截图存档（纯 Canvas 渲染 PNG 下载，零模型调用） */
    function textToPng() {
      var src = ((ta && ta.value) || state.ocrText || '').trim();
      if (!src) { toast('暂无文本内容可存档'); return ''; }
      var raw = src.split('\n'), lines = [];
      for (var i = 0; i < raw.length && lines.length < TEXT_PNG_MAX_LINES; i++) {
        var x = raw[i];
        if (x.length > 150) x = x.slice(0, 150) + '…（长行截断显示）';
        lines.push(x);
      }
      if (raw.length > lines.length) lines.push('……（共 ' + raw.length + ' 行，完整内容以文本通道为准）……');
      var lh = 16, pad = 14, w = 980;
      var c = newCanvas(w, lines.length * lh + pad * 2 + 22);
      var ctx = c.getContext('2d');
      ctx.fillStyle = '#0c0c0c'; ctx.fillRect(0, 0, c.width, c.height);
      ctx.font = 'bold 12px ui-monospace, Menlo, Consolas, "Courier New", monospace';
      ctx.fillStyle = '#E5734A';
      ctx.fillText('Iris 日志存档（P6 纯文本通道转截图）· ' + new Date().toTimeString().slice(0, 8) + ' · ' + raw.length + ' 行', pad, pad + 8);
      ctx.font = '12px ui-monospace, Menlo, Consolas, "Courier New", monospace';
      ctx.fillStyle = '#d4d4d4';
      for (var j = 0; j < lines.length; j++) ctx.fillText(lines[j], pad, pad + 28 + j * lh);
      var url = c.toDataURL('image/png');
      var aEl = document.createElement('a');
      aEl.href = url;
      aEl.download = 'iris-log-' + fmtFullTime() + '.png';
      document.body.appendChild(aEl); aEl.click(); document.body.removeChild(aEl);
      log('[P6] 文本已转截图存档（PNG ' + c.width + '×' + c.height + '，零模型调用；超 ' + TEXT_PNG_MAX_LINES + ' 行部分截断显示）', 'ok');
      toast('✓ 已生成截图存档 PNG');
      return url;
    }

    /* ---------------- 装配 ---------------- */
    function wire() {
      if (!wired) return;
      tabImg.addEventListener('click', function () { setMode('image'); });
      tabText.addEventListener('click', function () { setMode('text'); });
      ta.addEventListener('input', function () {
        btnAnalyze.disabled = !ta.value.trim() || state.running;
        clearTimeout(liveDetect._t);
        liveDetect._t = setTimeout(liveDetect, 350); // 配置预览：语法校验状态实时指示（防抖）
      });
      ta.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runDiagnose(); }
      });
      btnAnalyze.addEventListener('click', runDiagnose);
      btnLoadOcr.addEventListener('click', loadOcrToText);
      btnShot.addEventListener('click', textToPng);
      btnClear.addEventListener('click', function () {
        ta.value = ''; draft = '';
        lastKind = null; lastIssues = []; lastFixed = null; lastDanger = false;
        if (elDiff) { elDiff.hidden = true; elDiff.innerHTML = ''; }
        renderStatusPanel(); btnAnalyze.disabled = true;
      });
      renderTabs();
      renderStatusPanel();
    }
    wire();

    /* 对外接口（__iris.p6 自动化钩子；只增不改，P0-P5 钩子零触碰） */
    return {
      isTextMode: isTextMode, setMode: setMode, mode: function () { return mode; },
      takeFileGuard: takeFileGuard, pasteGuard: pasteGuard,
      stripNoise: stripNoise, segmentTextLines: segmentTextLines, mergeTextSegments: mergeTextSegments,
      detectKind: detectKind, detectConfigType: detectConfigType, detectLogFmt: detectLogFmt,
      detectShellKind: detectShellKind, looksLikeShellError: looksLikeShellError,
      checks: CONFIG_CHECKS, checkShell: checkShell, dangerScan: dangerScan,
      shellSyntaxCheck: shellSyntaxCheck, crossShell: CROSS_SHELL,
      configAppendix: configAppendix, shellAppendix: shellAppendix, focusLongText: focusLongText,
      analyzeText: analyzeText, runDiagnose: runDiagnose, applyRuleFix: applyRuleFix,
      renderFixDiff: renderFixDiff, jumpToLine: jumpToLine, liveDetect: liveDetect,
      loadOcrToText: loadOcrToText, textToPng: textToPng,
      info: function () { return { mode: mode, kind: lastKind, issues: lastIssues, fixed: lastFixed, fixedCount: lastFixedCount, danger: lastDanger }; },
      ta: ta
    };
  })();
  window.__iris.p6 = p6;
  log('[P6] 全场景排错通道就绪：粘贴日志（跳过 OCR 直通诊断链）/ 配置专项（YAML·JSON·Dockerfile·.env·Nginx 规则先行）/ 命令行专项（bash·PowerShell·cmd 跨 Shell 适配）', 'info');
  /* ============================================================
     [P7] 阶段 · 效率体验与生态升级（本地知识库 / 文件联动 / 导出扩展 / 快捷体系）
     ------------------------------------------------------------
     范式（docs/p7_audit.md §4）：主 IIFE 尾部纯追加 + 闭包函数重绑，
     既有函数体零改动；旧 20 条语义经 __iris.history 原钩子保留，新能力走 __iris.p7。
     红线：零后端调用（本段无任何 fetch/apiChat）、数据全部 localStorage/IndexedDB 本地。
     依据：任务书 P7 阶段2/3/4；docs/p7_audit.md
     ============================================================ */

  /* ---------------- [P7-1] 本地错误知识库（阶段2） ----------------
     200 条环形（收藏保护）· 错误类型/语言/严重度标签 · 全文搜索/组合筛选/三排序 ·
     新分析后 Top3 相似推荐一键复用 · 4.5MB 体积闸 LRU 淘汰 · 旧数据自动迁移。 */
  var p7kb = (function () {
    'use strict';

    var KB_MAX = 200;                        // 环形容量（HISTORY_MAX=20 旧常量不动，旧钩子语义保留）
    var KB_BYTE_CAP = 4.5 * 1024 * 1024;     // 写入前 UTF-16 体积闸（Chromium 配额 5MB，留安全边际）
    var KB_THUMB_W = 320;                    // 缩略图最大宽度（任务书阶段2-5；makeThumb 本体不改，P2 导出零影响）
    var RECO_MIN = 0.3;                      // 相似推荐最低分（errType 命中=0.4，语言=0.2，关键词 Jaccard=0.4）
    var SEV_LABEL = { high: '高', mid: '中', low: '低' };
    var SEV_RANK = { high: 0, mid: 1, low: 2 };
    var STOP = {};
    ('the and or of in to for is it this that with not but on as at be by an error file line code 错误 报错 无法 不能 未找到 找不到 异常 修复 代码 问题 请 检查 使用 如下 以下 一个 当前 支持 提示 信息 部分 可能 需要 已处 终止 抛出').split(' ')
      .forEach(function (w) { STOP[w] = 1; });

    /* [P7] 缩略图压缩：max 320px 宽 / JPEG q0.7（知识库专用，独立于 makeThumb） */
    function p7Thumb(c) {
      try {
        if (!c || !c.width) return '';
        var k = Math.min(1, KB_THUMB_W / c.width);
        var cv = newCanvas(Math.max(1, Math.round(c.width * k)), Math.max(1, Math.round(c.height * k)));
        cv.getContext('2d').drawImage(c, 0, 0, cv.width, cv.height);
        return cv.toDataURL('image/jpeg', 0.7);
      } catch (e) { return ''; }
    }

    /* 关键词抽取：ASCII 词 + CJK 连续段（≥2 字符），去停用词/纯数字，取前 10 */
    function tokenize(text) {
      var seen = {}, out = [];
      String(text || '').toLowerCase().split(/[^0-9a-z_$.\-一-龥]+/).forEach(function (t) {
        if (t.length < 2 || STOP[t] || /^\d+$/.test(t)) return;
        if (!seen[t]) { seen[t] = 1; out.push(t); }
      });
      return out.slice(0, 10);
    }

    /* 标签推导（纯规则，零模型）：errType=ERROR_TYPE_RE 优先，p6.detectKind 兜底（配置/命令错误）；
       sev=classifyError 分级映射；lang=结果语言 → 特征语言 → detectLang(代码) */
    function deriveTags(errText, result) {
      result = result || {};
      var src = String(errText || '');
      var f = extractFeatures(src || String(result.code || ''));
      var et = f.type || null;
      if (!et && src && p6 && p6.detectKind) {
        try {
          var k = p6.detectKind(src);
          if (k.family === 'config') et = '配置错误';
          else if (k.family === 'shell') et = '命令错误';
        } catch (e) { /* 静默回退通用 */ }
      }
      if (!et) et = '通用错误';
      var lang = result.lang || (f.lang && f.lang !== 'plaintext' ? f.lang : detectLang(result.code || ''));
      if (lang === 'plaintext') lang = '';
      var lvl = classifyError(src || String(result.root || '')).level;
      var sev = lvl === 'fatal' ? 'high' : (lvl === 'warn' ? 'mid' : 'low');
      var kw = tokenize(String(result.root || '') + ' ' + (et === '通用错误' ? '' : et) + ' ' + src.slice(0, 2000));
      return { errType: et, lang: lang, sev: sev, kw: kw };
    }

    /* 旧记录迁移：v1（无 ver）→ v2 超集，原字段零丢失 */
    function upgrade(rec) {
      if (!rec || rec.ver === 2) return rec;
      var t = deriveTags(String(rec.root || '') + '\n' + String(rec.code || ''), { root: rec.root, code: rec.code, lang: rec.lang });
      rec.ver = 2;
      rec.errType = t.errType;
      rec.sev = t.sev;
      rec.dur = null;
      rec.fav = false;
      rec.uses = 0;
      rec.last = rec.ts || Date.now();
      rec.kw = t.kw;
      rec.lang = rec.lang || t.lang;
      return rec;
    }

    function estBytes(arr) {
      try { return JSON.stringify(arr).length * 2; } catch (e) { return Number.MAX_SAFE_INTEGER; }
    }

    /* 读（替换 loadHistory 绑定）：解析 → 过滤 → 200 上限 → 检出旧结构自动迁移回写 */
    function kbLoad() {
      var arr = [];
      try { arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch (e) { return []; }
      if (!Array.isArray(arr)) return [];
      arr = arr.filter(function (x) { return x && typeof x.id === 'string'; }).slice(0, KB_MAX * 2); // [P7 修复] 2× 仅作损坏防护，不切尾部收藏（正常收缩由 kbPersist 双闸负责）
      var migrated = 0;
      for (var i = 0; i < arr.length; i++) {
        if (arr[i].ver !== 2) { upgrade(arr[i]); migrated++; }
      }
      if (migrated && kbPersist(arr)) {
        log('[P7] 历史数据自动迁移：' + migrated + ' 条旧记录 → 知识库 v2 格式（原字段零丢失）', 'info');
      }
      return arr;
    }

    /* 写（替换 persistHistory 绑定）：双闸容量治理——条数 >KB_MAX 或体积 >4.5MB 时，
       按「最久未使用（last）」淘汰非收藏记录；全收藏仍超限时停手，交给配额兜底（不崩溃） */
    function kbPersist(arr) {
      try {
        arr = (arr || []).slice(); // [P7 修复] 不预切尾部（会误删收藏），收缩全交给下方双闸 LRU 循环
        var evicted = 0, reason = '';
        for (;;) {
          var overLen = arr.length > KB_MAX;
          var overCap = !overLen && estBytes(arr) > KB_BYTE_CAP;
          if (!overLen && !overCap) break;
          var victim = -1, minLast = Infinity;
          for (var i = 0; i < arr.length; i++) {
            if (!arr[i].fav && (arr[i].last || 0) < minLast) { minLast = arr[i].last || 0; victim = i; }
          }
          if (victim < 0) break; // 全为收藏：不再淘汰（写入失败由 catch 兜底提示）
          reason = overLen ? '条数超上限' : '体积超 4.5MB';
          arr.splice(victim, 1); evicted++;
        }
        if (evicted) log('[P7] 知识库容量治理（' + reason + '）：已淘汰 ' + evicted + ' 条非收藏·最久未使用记录', 'info');
        localStorage.setItem(HISTORY_KEY, JSON.stringify(arr));
        return true;
      } catch (e) {
        log('历史记录保存失败（本地存储不可用或已满），不影响当前分析', 'info');
        return false;
      }
    }

    /* 相似匹配（纯计算）：errType 相同 +0.4 · 语言相同 +0.2 · 关键词 Jaccard ×0.4；取 ≥0.3 前 3 */
    function suggestFor(entry, pool) {
      var a = {};
      (entry.kw || []).forEach(function (t) { a[t] = 1; });
      var uniqA = 0;
      for (var k in a) uniqA++;
      var scored = [];
      (pool || []).forEach(function (rec) {
        if (rec.id === entry.id) return;
        var s = 0;
        if (rec.errType && rec.errType === entry.errType) s += 0.4;
        if (rec.lang && rec.lang === entry.lang) s += 0.2;
        var b = {}, inter = 0;
        (rec.kw || []).forEach(function (t) { if (!b[t]) { b[t] = 1; if (a[t]) inter++; } });
        var uniqB = 0;
        for (var k2 in b) uniqB++;
        var union = uniqA + uniqB - inter;
        s += 0.4 * (union > 0 ? inter / union : 0);
        if (s >= RECO_MIN && (rec.root || rec.code)) scored.push({ rec: rec, score: Math.min(0.99, s) });
      });
      scored.sort(function (x, y) { return y.score - x.score; });
      return scored.slice(0, 3);
    }

    /* 写入（替换 saveHistoryRecord 绑定，pushRound :1749 按名解析自动生效）：
       v2 条目入环形 + 收藏保护淘汰 + 相似推荐渲染 */
    function kbSave(rec) {
      if (!rec.result.root && !rec.result.code) return;
      var errText = rec.ocrText || state.ocrText || '';
      var t = deriveTags(errText, rec.result);
      var arr = kbLoad();
      var entry = {
        id: 'h' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36),
        ts: Date.now(),
        thumb: p7Thumb(state.procCanvas),
        root: String(rec.result.root || '').slice(0, 200),
        code: rec.result.code || '',
        lang: t.lang,
        rounds: state.rounds.length,
        outcome: rec.outcome,
        ver: 2, errType: t.errType, sev: t.sev, dur: null,
        fav: false, uses: 0, last: Date.now(), kw: t.kw
      };
      arr.unshift(entry);
      if (kbPersist(arr)) {
        state.historyId = entry.id;
        renderHistory();
        log('已存入本地知识库（' + arr.length + '/' + KB_MAX + ' · ' + entry.errType + ' · 收藏优先保留）', 'info');
        renderReco(suggestFor(entry, arr));
      }
    }

    /* 结果回写（替换 updateHistoryOutcome 绑定）：outcome 回填 + 修复时长统计（首次定案计时） */
    function kbOutcome(round) {
      if (!state.historyId) return;
      var arr = kbLoad();
      for (var i = 0; i < arr.length; i++) {
        if (arr[i].id === state.historyId) {
          arr[i].outcome = round.outcome;
          arr[i].rounds = Math.max(arr[i].rounds || 1, state.rounds.length);
          if (/^(ok|static-ok|fail|static-fail)$/.test(round.outcome) && !arr[i].dur) {
            arr[i].dur = Math.max(0, Date.now() - arr[i].ts);
            arr.last = undefined;
            arr[i].last = Date.now();
          }
          kbPersist(arr);
          renderHistory();
          return;
        }
      }
    }

    /* ---------------- [P7-1] 知识库视图（搜索/筛选/排序/收藏/回看/复用/删除） ---------------- */
    function getVal(id) { var x = $(id); return x ? String(x.value || '') : ''; }
    function mkEl(tag, cls, text) {
      var x = document.createElement(tag);
      if (cls) x.className = cls;
      if (text != null) x.textContent = text;
      return x;
    }
    function mkBtn(txt, title, fn, cls) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = cls || 'ghost'; b.textContent = txt; b.title = title;
      b.addEventListener('click', fn);
      return b;
    }
    function fillSelect(id, values, current, labelAll) {
      var s = $(id); if (!s) return;
      s.innerHTML = '';
      var o0 = document.createElement('option'); o0.value = ''; o0.textContent = labelAll; s.appendChild(o0);
      values.forEach(function (v) {
        var o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o);
      });
      s.value = (current && values.indexOf(current) !== -1) ? current : '';
    }
    function fmtDur(ms) {
      if (!ms || ms < 0) return '';
      return ms < 120000 ? ' · 用时 ' + Math.max(1, Math.round(ms / 1000)) + 's' : ' · 用时 ' + Math.round(ms / 60000) + 'min';
    }
    var kbLastRendered = []; // [P7-3] kbRender 最近一次实际展示的 id 集（筛选后）
    function outcomeWord(o) {
      var b = outcomeBadge(o);
      return b && b.text && b.cls ? b.text : '待执行验证';
    }

    function findRec(arr, id) {
      for (var i = 0; i < arr.length; i++) if (arr[i].id === id) return arr[i];
      return null;
    }

    /* 回看（替换 viewHistory 绑定）：原只读回看语义 + 使用计数/最近使用刷新（LRU 依据） */
    function kbView(id) {
      var arr = kbLoad();
      var rec = findRec(arr, id);
      if (!rec) return;
      rec.uses = (rec.uses || 0) + 1;
      rec.last = Date.now();
      kbPersist(arr);
      state._viewingId = id;
      state.viewing = {
        n: 1, ts: fmtHistoryTime(rec.ts), src: '历史记录（只读回看）',
        ocrText: '', errorInfo: '',
        result: { root: rec.root, plan: '', code: rec.code, lang: rec.lang, verifyFailed: null },
        run: null,
        outcome: rec.outcome === 'ok' ? 'ok' : (rec.outcome === 'fail' ? 'fail' :
          (rec.outcome === 'static-ok' ? 'static-ok' : (rec.outcome === 'static-fail' ? 'static-fail' : 'pending'))),
        verifyFailed: null
      };
      renderResult();
      renderHistory();
      log('历史回看：' + fmtHistoryTime(rec.ts) + ' 的分析记录（只读，复制按钮可用）', 'info');
    }
    /* 一键复用：复制修复代码 + 只读回看（uses 已在 kbView 计数） */
    function kbReuse(id) {
      var arr = kbLoad(), rec = findRec(arr, id);
      if (!rec) return;
      if (!rec.code) { toast('该记录没有修复代码'); return; }
      kbView(id);
      copyText(rec.code, '历史修复代码已复制到剪贴板（第 ' + rec.uses + ' 次复用）');
    }
    /* 收藏切换（收藏项淘汰时优先保留，任务书阶段2-2/5） */
    function kbToggleFav(id) {
      var arr = kbLoad(), rec = findRec(arr, id);
      if (!rec) return;
      rec.fav = !rec.fav;
      if (rec.fav) rec.last = Date.now();
      kbPersist(arr);
      renderHistory();
      toast(rec.fav ? '★ 已收藏：容量治理时优先保留' : '已取消收藏');
      log('[P7] 知识库收藏' + (rec.fav ? '加入' : '移出') + '：' + String(rec.root || rec.errType || id).slice(0, 40), 'info');
    }
    function kbDelete(id) {
      var arr = kbLoad().filter(function (x) { return x.id !== id; });
      kbPersist(arr);
      renderHistory();
      if (state.viewing && state._viewingId === id) { state.viewing = null; renderResult(); }
      toast('已删除该条历史记录');
    }

    /* 相似推荐面板（阶段2-4）：Top3 · 相似度% · 历史使用次数 · 一键复用 */
    function renderReco(list) {
      var box = $('p7-reco');
      if (!box) return;
      box.innerHTML = '';
      if (!list || !list.length) { box.hidden = true; return; }
      box.hidden = false;
      var head = mkEl('div', 'p5-panel-title', '💡 相似历史推荐（本地知识库 · 一键复用）');
      box.appendChild(head);
      list.forEach(function (it) {
        var r = it.rec;
        var row = mkEl('div', 'p7-reco-row');
        row.appendChild(mkEl('span', 'p7-reco-score', '相似度 ' + Math.round(it.score * 100) + '%'));
        row.appendChild(mkEl('span', 'p7-reco-meta mono',
          fmtHistoryTime(r.ts) + ' · ' + (r.errType || '通用错误') + ' · ' + (LANG_LABEL[r.lang] || r.lang || '通用') +
          ' · 复用 ' + (r.uses || 0) + ' 次' + (r.fav ? ' · ★' : '')));
        row.appendChild(mkEl('div', 'p7-reco-root', String(r.root || '（无根因描述）').slice(0, 120)));
        var ops = mkEl('div', 'p7-reco-ops');
        ops.appendChild(mkBtn('一键复用', '复制这条历史修复代码并只读回看', function () { kbReuse(r.id); }, 'primary'));
        ops.appendChild(mkBtn('查看', '在右侧结果区只读回看', function () { kbView(r.id); }));
        row.appendChild(ops);
        box.appendChild(row);
      });
    }

    /* 列表渲染（替换 renderHistory 绑定）：搜索/标签筛选/时间范围/排序/收藏 全条件组合 */
    function kbRender() {
      if (!el.historyList) return;
      var arr = kbLoad();
      // 下拉选项随库内容联动（去重 + 保当前选中）
      var types = [], langs = [];
      arr.forEach(function (r) {
        var e = r.errType || '通用错误';
        if (types.indexOf(e) === -1) types.push(e);
        var lg = r.lang || '';
        if (lg && langs.indexOf(lg) === -1) langs.push(lg);
      });
      var curT = getVal('p7-kb-type'), curL = getVal('p7-kb-lang');
      fillSelect('p7-kb-type', types, curT, '全部类型');
      fillSelect('p7-kb-lang', langs.map(function (x) { return LANG_LABEL[x] || x; }), curL, '全部语言');
      var langByLabel = {};
      langs.forEach(function (x) { langByLabel[LANG_LABEL[x] || x] = x; });

      var q = getVal('p7-kb-search').trim().toLowerCase();
      var fT = curT, fL = langByLabel[curL] || (langs.indexOf(curL) !== -1 ? curL : '');
      var days = parseInt(getVal('p7-kb-time'), 10) || 0;
      var sort = getVal('p7-kb-sort') || 'ts';
      var favEl = $('p7-kb-fav');
      var favOnly = !!(favEl && favEl.checked);

      var filtered = arr.filter(function (r) {
        if (fT && (r.errType || '通用错误') !== fT) return false;
        if (fL && (r.lang || '') !== fL) return false;
        if (favOnly && !r.fav) return false;
        if (days && Date.now() - (r.ts || 0) > days * 86400000) return false;
        if (q) {
          var hay = (String(r.root || '') + '\n' + String(r.code || '') + '\n' +
            (r.kw || []).join(' ') + '\n' + (r.errType || '') + '\n' + (r.lang || '')).toLowerCase();
          if (hay.indexOf(q) === -1) return false;
        }
        return true;
      });
      kbLastRendered = filtered.map(function (r) { return r.id; }); // [P7-3] 当前筛选结果（历史批量操作依据）
      if (sort === 'uses') filtered.sort(function (a, b) { return (b.uses || 0) - (a.uses || 0) || (b.ts - a.ts); });
      else if (sort === 'sev') filtered.sort(function (a, b) {
        return (SEV_RANK[a.sev] == null ? 9 : SEV_RANK[a.sev]) - (SEV_RANK[b.sev] == null ? 9 : SEV_RANK[b.sev]) || (b.ts - a.ts);
      });
      else filtered.sort(function (a, b) { return b.ts - a.ts; });

      el.historyList.innerHTML = '';
      var stat = $('p7-kb-stat');
      if (stat) {
        var favN = 0;
        arr.forEach(function (r) { if (r.fav) favN++; });
        stat.textContent = '共 ' + arr.length + ' 条 · 显示 ' + filtered.length + ' · 收藏 ' + favN +
          ' · ' + (estBytes(arr) / 1048576).toFixed(2) + 'MB';
      }
      if (!filtered.length) {
        var p = mkEl('p', 'history-empty', arr.length ? '无匹配记录（清空搜索/筛选条件试试）' : '暂无历史记录。完成一次分析后自动保存在本机知识库。');
        el.historyList.appendChild(p);
        return;
      }
      filtered.forEach(function (rec) {
        var item = mkEl('div', 'history-item p7-kb-item' + (rec.fav ? ' is-fav' : ''));
        item.setAttribute('data-kb-id', rec.id);
        if (rec.thumb) {
          var img = document.createElement('img');
          img.className = 'history-thumb'; img.alt = '截图缩略图';
          img.src = rec.thumb; // 本地 localStorage 缩略图，非外部输入
          item.appendChild(img);
        } else {
          item.appendChild(mkEl('span', 'p7-kb-nothumb mono', '文'));
        }
        var main = mkEl('div', 'history-main');
        var t1 = mkEl('div', 'history-title p7-kb-title');
        t1.textContent = rec.root || '（无根因描述）';
        t1.title = rec.root || '';
        t1.appendChild(mkEl('span', 'p7-kb-tag', rec.errType || '通用错误'));
        var t2 = mkEl('div', 'history-sub');
        t2.textContent = fmtHistoryTime(rec.ts) + ' · ' + (LANG_LABEL[rec.lang] || rec.lang || '通用') +
          ' · 严重度' + (SEV_LABEL[rec.sev] || '低') +
          ' · 复用 ' + (rec.uses || 0) + ' 次' + fmtDur(rec.dur) +
          ' · ' + outcomeWord(rec.outcome);
        main.appendChild(t1); main.appendChild(t2);
        item.appendChild(main);
        var favBtn = mkBtn(rec.fav ? '★' : '☆', rec.fav ? '取消收藏' : '收藏（容量治理时优先保留）', function () { kbToggleFav(rec.id); }, 'p7-kb-favbtn' + (rec.fav ? ' is-on' : ''));
        item.appendChild(favBtn);
        item.appendChild(mkBtn('查看', '在右侧结果区查看这条记录', function () { kbView(rec.id); }));
        item.appendChild(mkBtn('复用代码', '一键复制这条记录的修复代码并计数', function () { kbReuse(rec.id); }));
        item.appendChild(mkBtn('删除', '删除这条记录', function () { kbDelete(rec.id); }));
        el.historyList.appendChild(item);
      });
    }

    /* 工具条事件装配 + 面板标题升级（DOM 缺失时静默降级，范式同 P6 wired） */
    function wireKb() {
      ['p7-kb-search', 'p7-kb-type', 'p7-kb-lang', 'p7-kb-time', 'p7-kb-sort', 'p7-kb-fav'].forEach(function (id) {
        var x = $(id);
        if (!x) return;
        x.addEventListener('input', kbRender);
        x.addEventListener('change', kbRender);
      });
      var hb = $('history-box');
      if (hb) {
        var sm = hb.querySelector('summary');
        if (sm) sm.textContent = '错误知识库 · 分析历史（本机最近 ' + KB_MAX + ' 条，收藏优先保留）';
      }
    }

    /* ---------------- 闭包重绑（既有函数体零改动；调用点按名解析自动切换新引擎） ---------------- */
    loadHistory = kbLoad;
    persistHistory = kbPersist;
    saveHistoryRecord = kbSave;
    updateHistoryOutcome = kbOutcome;
    viewHistory = kbView;
    deleteHistory = kbDelete;
    renderHistory = kbRender;

    wireKb();
    kbRender(); // 启动首绘：:2920 旧引擎已渲染 → 同数据 KB 引擎重渲（幂等）

    /* 对外接口（__iris.p7.kb 自动化钩子；只增不改） */
    return {
      KB_MAX: KB_MAX, KB_BYTE_CAP: KB_BYTE_CAP, RECO_MIN: RECO_MIN,
      load: kbLoad, persist: kbPersist, save: kbSave, render: kbRender,
      view: kbView, reuse: kbReuse, remove: kbDelete, toggleFav: kbToggleFav,
      suggest: suggestFor, upgrade: upgrade, deriveTags: deriveTags,
      tokenize: tokenize, thumb: p7Thumb, estBytes: estBytes, renderReco: renderReco,
      lastIds: function () { return kbLastRendered.slice(); } // [P7-3]
    };
  })();
  window.__iris.p7 = { kb: p7kb };
  log('[P7] 本地错误知识库就绪：200 条环形（收藏保护）· 标签/搜索/筛选/排序 · 相似推荐一键复用 · 4.5MB 体积闸（数据全部存本机 localStorage，零上传）', 'info');

  /* ---------------- [P7-2] 本地文件联动（阶段3，File System Access API + IndexedDB 句柄持久化） ----------------
     识别报错 → 「打开源文件」（用户手动授权选择）→ 行定位/高亮 → 应用修复（Diff 二次确认写入 + 内存备份撤销）。
     红线：全程用户主动触发（按钮点击=transient activation）；不自动读取任何文件；句柄仅存本机 IndexedDB；
     绝不批量操作；写入前必须 Diff 预览 + 显式点击「确认写入」。不支持 FS API 的浏览器隐藏入口 + 友好说明。 */
  var p7fs = (function () {
    'use strict';
    var FS_SUPPORT = typeof window.showOpenFilePicker === 'function';
    var IDB_NAME = 'iris.p7', IDB_STORE = 'filehandles';
    var CTX = 5;              // 报错行上下文行数（任务书：上下各 5 行）
    var VIEW_MAX_LINES = 20000; // 全量渲染上限（超限截断提示）
    var FILE_MAX_BYTES = 2 * 1024 * 1024; // 读取上限 2MB（查看器适配）
    var p7loc = null;         // 当前轮提取到的 {path, line}
    var cur = null;           // 已打开文件态 {handle,name,text,lines,path,line,backup,pending,wrote}
    var mem = {};             // IndexedDB 不可用（如 file://）时的会话内存兜底
    var hintShown = false;

    /* 文件名/路径工具 */
    function basename(p) { return String(p || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || ''; }
    function looksLikePath(s) {
      if (!s || /:\/\//.test(s) || s.indexOf('//') === 0) return false; // 排除 http(s) URL 与协议相对 URL
      var b = basename(s);
      return /\.[A-Za-z][A-Za-z0-9]{0,7}$/.test(b);
    }

    /* 报错文本 → 源文件路径 + 行号（纯正则多栈适配；无行号时仍返回路径供打开） */
    var LOC_RES = [
      /File "([^"\n]+)",\s*line\s*(\d+)/,                                   // Python traceback
      /\(([\w$.\/+-]+\.(?:java|kt|scala|groovy)):(\d+)\)/,                  // Java/JVM 栈帧
      /\(([\w\-./\\:~]+?\.(?:js|jsx|ts|tsx|mjs|cjs)):(\d+)(?::\d+)?\)/,     // Node/V8 栈帧
      /([\w\-./\\:~]+?\.(?:js|jsx|ts|tsx|mjs|cjs)):(\d+)(?::\d+)?(?=[\s(]|$)/, // Node 顶层 path:line:col
      /([\w\-./\\:~]+?\.(?:go|rs|c|cpp|cc|h|hpp)):(\d+)/,                   // Go/C/C++ 编译定位
      /([\w\-./\\:~]+?\.(?:py|rb|php|sh|bash|ps1|bat|cmd))[:,]\s*line\s+(\d+)/i, // 文本式 path, line N
      /([\w\-./\\:~]+?\.(?:py|java|js|ts|go|c|cpp|sh|ps1|yaml|yml|toml|ini|conf|json|xml|env|md|vue))(?:[：:,，]\s*(?:line|行|第)\s*(\d+))/i // 中文/通用
    ];
    function extractFileLoc(text) {
      var s = String(text || '');
      if (!s) return null;
      var i, m, re, line = null;
      for (i = 0; i < LOC_RES.length; i++) {
        re = new RegExp(LOC_RES[i].source, 'g');
        m = re.exec(s);
        if (m && looksLikePath(m[1])) {
          line = m[2] ? parseInt(m[2], 10) : null;
          if (line && (line < 1 || line > 1e6)) line = null;
          return { path: m[1].trim(), line: line };
        }
      }
      // 无行号兜底：仅文件名（可打开、无高亮）
      var fm = s.match(/([\w\-./\\:~]+?\.(?:py|js|ts|java|go|c|cpp|sh|ps1|yaml|yml|json|conf))(?=[\s"'：:，,、）)（(\[]|$)/i);
      if (fm && looksLikePath(fm[1])) return { path: fm[1].trim(), line: null };
      return null;
    }

    /* ---------------- IndexedDB 句柄存储（不可用时降级会话内存） ---------------- */
    function idbOpen() {
      return new Promise(function (res, rej) {
        try {
          if (!window.indexedDB) return rej(new Error('no-idb'));
          var r = window.indexedDB.open(IDB_NAME, 1);
          r.onupgradeneeded = function () { try { r.result.createObjectStore(IDB_STORE); } catch (e) {} };
          r.onsuccess = function () { res(r.result); };
          r.onerror = function () { rej(r.error || new Error('idb-open')); };
        } catch (e) { rej(e); }
      });
    }
    function storePut(key, val) {
      return idbOpen().then(function (db) {
        return new Promise(function (res) {
          try {
            var tx = db.transaction(IDB_STORE, 'readwrite');
            tx.objectStore(IDB_STORE).put(val, key);
            tx.oncomplete = function () { res(true); };
            tx.onerror = function () { res(false); };
          } catch (e) { res(false); }
        });
      }).catch(function () { mem[key] = val; return false; });
    }
    function storeGet(key) {
      return idbOpen().then(function (db) {
        return new Promise(function (res) {
          try {
            var rq = db.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).get(key);
            rq.onsuccess = function () { res(rq.result || null); };
            rq.onerror = function () { res(null); };
          } catch (e) { res(null); }
        });
      }).catch(function () { return mem[key] || null; });
    }
    function storeDel(key) {
      return idbOpen().then(function (db) {
        try { db.transaction(IDB_STORE, 'readwrite').objectStore(IDB_STORE).delete(key); } catch (e) {}
      }).catch(function () { delete mem[key]; });
    }

    /* 权限：queryPermission 已授权则免重复授权；requestPermission 必须在用户手势内（按钮点击调用链满足） */
    function ensurePermission(handle, mode) {
      if (!handle || !handle.queryPermission) return Promise.resolve(true); // 测试桩句柄/老浏览器无该 API 时放行
      return handle.queryPermission({ mode: mode }).then(function (st) {
        if (st === 'granted') return true;
        if (handle.requestPermission) {
          return handle.requestPermission({ mode: mode }).then(function (s2) { return s2 === 'granted'; });
        }
        return false;
      }).catch(function () { return false; });
    }
    function readFileText(file) {
      return new Promise(function (res, rej) {
        try {
          var rd = new FileReader();
          rd.onerror = function () { rej(new Error('文件读取失败')); };
          rd.onload = function () { res(String(rd.result || '')); };
          rd.readAsText(file);
        } catch (e) { rej(e); }
      });
    }

    /* ---------------- 结果区联动：按钮显隐 / 兼容降级提示 ---------------- */
    function currentLoc() {
      if (state.viewing) return null; // 历史回看态无完整轮文本，不做联动
      var r = currentRound();
      if (!r) return null;
      var srcs = [r.errorInfo, r.ocrText, r.result.root];
      for (var i = 0; i < srcs.length; i++) {
        var loc = extractFileLoc(srcs[i]);
        if (loc) return loc;
      }
      return null;
    }
    function syncFromState() {
      p7loc = currentLoc();
      var btn = $('p7-open-file-btn');
      var hint = $('p7-fs-hint');
      if (btn) btn.hidden = !(p7loc && FS_SUPPORT && !state.running);
      if (hint) {
        var show = !!(p7loc && !FS_SUPPORT && !hintShown);
        hint.hidden = !show;
        if (show) { hintShown = true; log('[P7] 当前浏览器不支持本地文件联动（无 File System Access API），入口已隐藏；推荐 Chrome/Edge', 'info'); }
      }
    }

    /* ---------------- 打开文件（用户手势触发；先试持久化句柄免重复授权） ---------------- */
    function openFile() {
      if (!FS_SUPPORT) return;
      var key = p7loc ? basename(p7loc.path) : '';
      var stored = key ? storeGet(key) : Promise.resolve(null);
      stored.then(function (h) {
        if (h && (!h.getFile || !h.createWritable)) { storeDel(key); h = null; } // 跨会话句柄失效/仅存元数据 → 清掉重选
        if (h) return ensurePermission(h, 'read').then(function (ok) { if (ok) return h; return null; });
        return null;
      }).then(function (h) {
        if (h) { log('[P7] 复用已授权文件句柄「' + (h.name || key) + '」（免重复授权）', 'ok'); return adoptHandle(h); }
        return window.showOpenFilePicker({ multiple: false, excludeAcceptAllOption: false })
          .then(function (arr) { return arr && arr[0]; })
          .then(function (hh) {
            if (!hh) return null;
            if (key) storePut(key, hh);
            storePut(hh.name, hh); // 同名人后续免选
            return ensurePermission(hh, 'read').then(function (ok) { return ok ? hh : null; });
          });
      }).then(function (h) {
        if (!h) { toast('未获得文件读取授权（已取消）'); return; }
        return adoptHandle(h);
      }).catch(function (e) {
        if (e && (e.name === 'AbortError' || e.name === 'NotAllowedError')) { log('[P7] 已取消文件选择，未读取任何本地文件', 'info'); return; }
        log('[P7] 打开源文件失败：' + ((e && e.message) || e), 'fail');
      });
    }
    function adoptHandle(h) {
      return h.getFile().then(function (f) {
        if (f.size > FILE_MAX_BYTES) { toast('文件超过 2MB 查看上限（' + Math.round(f.size / 1048576 * 10) / 10 + 'MB）'); return; }
        return readFileText(f).then(function (text) {
          cur = {
            handle: h, name: h.name || f.name, text: text, lines: text.split(/\r\n|\r|\n/),
            path: p7loc ? p7loc.path : '', line: p7loc ? p7loc.line : null,
            backup: null, pending: null, wrote: false, full: false
          };
          renderFileView();
          log('[P7] 已打开本地源文件「' + cur.name + '」（' + cur.lines.length + ' 行；内容仅存内存与本机，不上传）', 'ok');
        });
      });
    }

    /* ---------------- 查看器：行号/±5 上下文/报错行红色高亮/行号点击跳转 ---------------- */
    function renderFileView() {
      var sec = $('p7-filesec'), view = $('p7-file-view');
      if (!sec || !view) return;
      if (!cur) { sec.hidden = true; view.innerHTML = ''; return; }
      sec.hidden = false;
      var nm = $('p7-file-name'); if (nm) nm.textContent = cur.name + ' · ' + cur.lines.length + ' 行' + (cur.line ? ' · 报错行 ' + cur.line : '');
      var warn = $('p7-file-warn');
      var mismatch = cur.path && basename(cur.path) !== cur.name;
      if (warn) {
        warn.hidden = !mismatch;
        if (mismatch) warn.textContent = '⚠ 所选文件与报错路径不同名（' + basename(cur.path) + ' ≠ ' + cur.name + '）';
      }
      view.innerHTML = '';
      var start = 1, end = cur.lines.length, windowed = false;
      if (!cur.full && cur.line && cur.line > 0) {
        start = Math.max(1, cur.line - CTX); end = Math.min(cur.lines.length, cur.line + CTX); windowed = true;
      } else if (!cur.full && cur.lines.length > 200) {
        start = 1; end = 200; windowed = true;
      }
      if (cur.lines.length > VIEW_MAX_LINES && cur.full) end = Math.min(end, VIEW_MAX_LINES);
      for (var ln = start; ln <= end; ln++) {
        var row = document.createElement('div');
        row.className = 'p7fl-line' + (cur.line && ln === cur.line ? ' is-err' : '') + (cur.wrote && cur.line && ln === cur.line ? ' is-wrote' : '');
        row.setAttribute('data-line', String(ln));
        var no = document.createElement('span');
        no.className = 'p7fl-no mono'; no.textContent = ln; no.title = '点击跳转到该行';
        (function (n, r) {
          no.addEventListener('click', function () { jumpToLine(n, r); });
        })(ln, row);
        var tx = document.createElement('span');
        tx.className = 'p7fl-txt mono';
        tx.textContent = cur.lines[ln - 1] == null ? '' : cur.lines[ln - 1]; // textContent 零注入
        row.appendChild(no); row.appendChild(tx);
        view.appendChild(row);
      }
      var bar = document.createElement('div');
      bar.className = 'p7fl-bar';
      var tog = document.createElement('button');
      tog.type = 'button'; tog.className = 'ghost'; tog.textContent = cur.full ? '只看报错行上下文' : '显示全文件';
      tog.addEventListener('click', function () { cur.full = !cur.full; renderFileView(); });
      bar.appendChild(tog);
      if (windowed) {
        var tip = document.createElement('span'); tip.className = 'p7fl-tip mono';
        tip.textContent = (cur.line ? '报错行 ' + cur.line + '（上下 ' + CTX + ' 行上下文）' : '默认前 200 行');
        if (cur.lines.length > VIEW_MAX_LINES) tip.textContent += ' · 渲染上限 ' + VIEW_MAX_LINES + ' 行';
        bar.appendChild(tip);
      }
      view.appendChild(bar);
      var act = $('p7-file-actions'); if (act) act.hidden = true;
      var dbox = $('p7-file-diff'); if (dbox) { dbox.hidden = true; dbox.innerHTML = ''; }
      var undo = $('p7-write-undo'); if (undo) undo.hidden = !cur.wrote;
      if (cur.line) jumpToLine(cur.line);
    }
    function jumpToLine(n, row) {
      var view = $('p7-file-view');
      if (!view) return;
      var r = row || view.querySelector('.p7fl-line[data-line="' + n + '"]');
      if (r) {
        Array.prototype.forEach.call(view.querySelectorAll('.is-jump'), function (x) { x.classList.remove('is-jump'); });
        r.classList.add('is-jump');
        if (r.scrollIntoView) r.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }

    /* ---------------- Diff 确认视图（复用 P2 diffLines + .diff-cols 组件；独立容器不动主 Diff Tab） ---------------- */
    function renderDiffInto(box, leftText, rightText, lang) {
      box.innerHTML = '';
      var d = diffLines(leftText, rightText);
      var head = document.createElement('div'); head.className = 'diff-head';
      var t = document.createElement('span'); t.className = 'diff-title'; t.textContent = '写入前 Diff 确认';
      head.appendChild(t);
      box.appendChild(head);
      var cols = document.createElement('div'); cols.className = 'diff-cols';
      function buildCol(headText, side) {
        var col = document.createElement('div'); col.className = 'diff-col';
        var ch = document.createElement('div'); ch.className = 'diff-col-head'; ch.textContent = headText; col.appendChild(ch);
        var linesBox = document.createElement('div'); linesBox.className = 'diff-lines';
        for (var i = 0; i < d.rows.length; i++) {
          var row = d.rows[i], cell = row[side];
          var dl = document.createElement('div');
          dl.className = 'dl dl-' + (row.type === 'eq' ? 'eq' : row.type);
          if (cell) {
            var no = document.createElement('span'); no.className = 'dl-no'; no.textContent = cell.no;
            var code = document.createElement('span'); code.className = 'dl-code';
            var html = highlightLineHtml(cell.text, lang);
            if (html !== null) code.innerHTML = html; else code.textContent = cell.text;
            dl.appendChild(no); dl.appendChild(code);
          } else {
            var pn = document.createElement('span'); pn.className = 'dl-no';
            var pad = document.createElement('span'); pad.className = 'dl-pad';
            dl.appendChild(pn); dl.appendChild(pad);
          }
          linesBox.appendChild(dl);
        }
        col.appendChild(linesBox);
        return col;
      }
      cols.appendChild(buildCol('本地文件当前内容（' + cur.name + '）', 'l'));
      cols.appendChild(buildCol('Iris 修复代码（将写入）', 'r'));
      box.appendChild(cols);
      var cnt = diffCounts(d.rows);
      var legend = document.createElement('div'); legend.className = 'diff-legend';
      function lg(cls, txt, n) {
        var sp = document.createElement('span'); var ic = document.createElement('i'); ic.className = cls;
        sp.appendChild(ic); sp.appendChild(document.createTextNode(txt + ' ' + n)); return sp;
      }
      legend.appendChild(lg('lg-add', '新增', cnt.add));
      legend.appendChild(lg('lg-del', '删除', cnt.del));
      legend.appendChild(lg('lg-mod', '修改', cnt.mod));
      if (d.truncated) { var tr = document.createElement('span'); tr.textContent = '（Diff 已截断至 ' + DIFF_MAX_LINES + ' 行/侧，实际写入为完整内容）'; legend.appendChild(tr); }
      box.appendChild(legend);
    }

    /* 应用修复入口：准备 Diff + 显式确认按钮（写入绝不静默发生） */
    function prepareApply() {
      if (!cur) { openFile(); return; }
      var r = currentRound();
      var code = r && r.result.code;
      if (!code) { toast('当前轮暂无修复代码可写入'); return; }
      cur.pending = code;
      cur.backup = cur.text; // 写入前内存备份原文（撤销依据）
      var dbox = $('p7-file-diff');
      if (dbox) {
        renderDiffInto(dbox, cur.text, code, r.result.lang || detectLang(code));
        dbox.hidden = false;
      }
      var act = $('p7-file-actions'); if (act) act.hidden = false;
      var confirm = $('p7-write-confirm'); if (confirm) { confirm.disabled = false; confirm.textContent = '确认写入本地文件'; }
      var undo = $('p7-write-undo'); if (undo) undo.hidden = true;
      log('[P7] 已生成写入前 Diff（左侧=磁盘原文，右侧=将写入内容）；点击「确认写入」后才会修改本地文件', 'info');
    }
    function cancelApply() {
      if (cur) cur.pending = null;
      var dbox = $('p7-file-diff'); if (dbox) { dbox.hidden = true; dbox.innerHTML = ''; }
      var act = $('p7-file-actions'); if (act) act.hidden = true;
    }
    /* 确认写入：readwrite 权限（手势内申请）→ createWritable → 原文已在内存备份 */
    function confirmWrite() {
      if (!cur || !cur.pending || !cur.handle) return;
      var btn = $('p7-write-confirm'); if (btn) { btn.disabled = true; btn.textContent = '写入中…'; }
      ensurePermission(cur.handle, 'readwrite').then(function (ok) {
        if (!ok) throw new Error('write-permission-denied');
        return cur.handle.createWritable().then(function (w) {
          return w.write(cur.pending).then(function () { return w.close(); });
        });
      }).then(function () {
        cur.text = cur.pending;
        cur.lines = cur.text.split(/\r\n|\r|\n/);
        cur.pending = null;
        cur.wrote = true;
        var act = $('p7-file-actions'); if (act) act.hidden = false;
        var dbox = $('p7-file-diff'); if (dbox) { dbox.hidden = true; dbox.innerHTML = ''; }
        renderFileView();
        var undo2 = $('p7-write-undo'); if (undo2) undo2.hidden = false;
        toast('✓ 修复代码已写入本地文件');
        log('[P7] 已写入「' + cur.name + '」（Diff 用户确认后执行；原文内存备份保留，可一键撤销）', 'ok');
      }).catch(function (e) {
        var nm = e && e.message === 'write-permission-denied' ? '写入权限未授予' : ((e && e.message) || e);
        toast('写入失败：' + nm + '（文件未改动）');
        log('[P7] 写入失败（' + nm + '），本地文件未被修改', 'fail');
        if (btn) { btn.disabled = false; btn.textContent = '确认写入本地文件'; }
      });
    }
    /* 撤销：把写入前内存备份写回（恢复原状） */
    function undoWrite() {
      if (!cur || !cur.backup) return;
      var snapshot = cur.backup;
      ensurePermission(cur.handle, 'readwrite').then(function (ok) {
        if (!ok) throw new Error('write-permission-denied');
        return cur.handle.createWritable().then(function (w) {
          return w.write(snapshot).then(function () { return w.close(); });
        });
      }).then(function () {
        cur.text = snapshot;
        cur.lines = snapshot.split(/\r\n|\r|\n/);
        cur.backup = null; cur.wrote = false;
        renderFileView();
        toast('已撤销写入（本地文件恢复原内容）');
        log('[P7] 已撤销写入，「' + cur.name + '」恢复为写入前内容', 'ok');
      }).catch(function (e) {
        toast('撤销失败：' + ((e && e.message) || e));
        log('[P7] 撤销写入失败', 'fail');
      });
    }
    function closeFile() {
      cur = null;
      var sec = $('p7-filesec'); if (sec) sec.hidden = true;
    }

    /* 事件装配（DOM 缺失静默降级） */
    function wireFs() {
      var b;
      b = $('p7-open-file-btn'); if (b) b.addEventListener('click', function () { openFile(); });
      b = $('p7-file-reload'); if (b) b.addEventListener('click', function () {
        if (!cur) return;
        var h = cur.handle;
        h.getFile().then(function (f) { return readFileText(f); }).then(function (text) {
          cur.text = text; cur.lines = text.split(/\r\n|\r|\n/); cur.backup = null; cur.pending = null; cur.wrote = false;
          renderFileView(); log('[P7] 已重新读取「' + cur.name + '」', 'ok');
        }).catch(function () { toast('重新读取失败'); });
      });
      b = $('p7-file-apply'); if (b) b.addEventListener('click', prepareApply);
      b = $('p7-file-close'); if (b) b.addEventListener('click', closeFile);
      b = $('p7-write-confirm'); if (b) b.addEventListener('click', confirmWrite);
      b = $('p7-write-cancel'); if (b) b.addEventListener('click', cancelApply);
      b = $('p7-write-undo'); if (b) b.addEventListener('click', undoWrite);
      if (!FS_SUPPORT) {
        var ob = $('p7-open-file-btn'); if (ob) ob.style.display = 'none';
        log('[P7] 本地文件联动不可用（浏览器无 File System Access API）：入口已隐藏，其余功能不受影响', 'info');
      }
    }

    /* renderResult 装饰重绑（既有渲染零改动；结果刷新时同步联动按钮/查看器行号） */
    var _origRenderResult = renderResult;
    renderResult = function () {
      _origRenderResult.apply(null, arguments);
      try { syncFromState(); } catch (e) { /* 联动失败不影响主渲染 */ }
    };
    wireFs();

    return {
      support: FS_SUPPORT, extractFileLoc: extractFileLoc, basename: basename, looksLikePath: looksLikePath,
      sync: syncFromState, open: openFile, adopt: adoptHandle, cur: function () { return cur; },
      renderFileView: renderFileView, prepareApply: prepareApply, confirmWrite: confirmWrite,
      undoWrite: undoWrite, close: closeFile, ensurePermission: ensurePermission,
      storePut: storePut, storeGet: storeGet, IDB_NAME: IDB_NAME
    };
  })();
  window.__iris.p7.fs = p7fs;
  log('[P7] 本地文件联动就绪：' + (p7fs.support ? '打开源文件 → 报错行高亮定位 → Diff 二次确认写入 → 内存备份撤销（句柄仅存本机 IndexedDB）' : '当前浏览器不支持 File System Access API，联动入口已隐藏'), 'info');

  /* ---------------- [P7-3] 导出扩展 + 快捷键 + 文件夹导入 + 历史快速操作（阶段4） ---------------- */
  var p7ux = (function () {
    'use strict';

    /* ============ 阶段4-1 导出格式扩展 ============
       GitHub Issue / GitLab 工单：Markdown 模板（标题/复现步骤/报错截图/修复方案/环境信息），
       产物经既有 downloadText Blob 下载，可直接粘贴到对应平台；
       PDF：window.print() + 打印专用 CSS（零第三方库，用户可在打印预览「另存为 PDF」）。 */
    function envInfo() {
      return {
        ua: String(navigator.userAgent || '').slice(0, 160),
        lang: navigator.language || '—',
        screen: (screen.width || '—') + '×' + (screen.height || '—'),
        origin: location.origin !== 'null' ? location.origin : 'file:// 本地直开',
        time: new Date().toLocaleString(),
        iris: 'Iris 视觉智能体 P7（本地分析，数据不出本机）'
      };
    }
    /* 当前轮 → 报告素材（与 exportCurrentRound 同源字段，独立取数不改 P2 函数） */
    function issueMaterial() {
      var cur = currentRound();
      if (!cur || (!cur.result.root && !cur.result.plan && !cur.result.code)) return null;
      return {
        cur: cur,
        env: envInfo(),
        channel: state._p6Text ? '粘贴日志文本（跳过 OCR）' : (state.file ? '截图 OCR' : '本地分析'),
        thumb: state.procCanvas ? makeThumb(state.procCanvas, 420) : '',
        errType: (p7kb.load().filter(function (r) { return r.id === state.historyId; })[0] || {}).errType || extractFeatures(cur.ocrText || '').type || '通用错误'
      };
    }
    function buildIssueMarkdown(platform, m) {
      /* platform: 'github' | 'gitlab' —— 两平台同构模板，语法差异点：
         GitHub 使用 > [!NOTE] 提示块与 **标签建议** 行；GitLab 使用 /label 斜杠命令与 :::note 块。 */
      var isGH = platform === 'github';
      var cur = m.cur;
      var title = '[' + m.errType + '] ' + String(cur.result.root || '运行时报错').replace(/\s+/g, ' ').slice(0, 60);
      var L = [];
      L.push('# ' + title);
      L.push('');
      L.push(isGH ? '> [!NOTE]' : ':::note');
      L.push(isGH ? '> 由 Iris 视觉智能体（P7）自动生成 · 本地分析记录 · 请按需替换截图附件' : '::: 由 Iris 视觉智能体（P7）自动生成 · 本地分析记录 · 请按需替换截图附件');
      L.push(isGH ? '' : ':::');
      L.push('');
      L.push('## 问题描述');
      L.push('');
      L.push(String(cur.result.root || '（无根因描述）'));
      if (cur.result.plan) { L.push(''); L.push('**初步方案**：' + String(cur.result.plan).replace(/\s+/g, ' ').slice(0, 300)); }
      L.push('');
      L.push('## 复现步骤');
      L.push('');
      L.push('1. 输入通道：' + m.channel);
      L.push('2. 提交 Iris 分析（预处理 → 识别 → 诊断 → 修复链路）');
      L.push('3. 观察到报错（见下方报错信息/截图）');
      L.push('');
      L.push('## 报错信息');
      L.push('');
      L.push(mdFence((cur.ocrText || cur.errorInfo || '（无）').slice(0, 4000), 'text'));
      L.push('');
      L.push('## 报错截图');
      L.push('');
      if (m.thumb) {
        L.push('<!-- 注：GitHub/GitLab 不渲染 base64 内联图，请将下方 data URI 另存或直接把原截图拖拽进编辑框替换本块 -->');
        L.push('');
        L.push('<details><summary>报错截图（base64，粘贴前建议替换为拖拽上传）</summary>');
        L.push('');
        L.push('![error-screenshot](' + m.thumb + ')');
        L.push('');
        L.push('</details>');
      } else {
        L.push('（本次为纯文本分析，无截图附件）');
      }
      L.push('');
      L.push('## 修复方案');
      L.push('');
      L.push(cur.result.plan || '（见修复代码注释）');
      L.push('');
      L.push('## 修复代码');
      L.push('');
      L.push(mdFence(cur.result.code || '（无代码产出）', cur.result.lang || ''));
      L.push('');
      L.push('## 执行结果');
      L.push('');
      L.push(cur.outcome === 'ok' ? 'ZCode 沙箱执行通过 ✅' :
        cur.outcome === 'fail' ? 'ZCode 沙箱执行失败 ❌（详见下方输出）' :
        cur.outcome === 'static-ok' ? '静态校验通过 ✅' :
        cur.outcome === 'static-fail' ? '静态校验失败 ❌' : '待执行验证');
      if (cur.run && (cur.run.stdout || cur.run.stderr)) {
        L.push('');
        L.push(mdFence(String(cur.run.stdout || '') + '\n' + String(cur.run.stderr || ''), 'log'));
      }
      L.push('');
      L.push('## 环境信息');
      L.push('');
      L.push('| 项 | 值 |');
      L.push('|---|---|');
      L.push('| 生成时间 | ' + m.env.time + ' |');
      L.push('| 浏览器 | ' + m.env.ua + ' |');
      L.push('| 语言/屏幕 | ' + m.env.lang + ' / ' + m.env.screen + ' |');
      L.push('| 运行环境 | ' + m.env.origin + ' |');
      L.push('| 工具 | ' + m.env.iris + ' |');
      L.push('');
      L.push(isGH ? '**建议标签**: `bug` `' + m.errType.toLowerCase() + '`' : '/label ~bug ~"' + m.errType + '"');
      return L.join('\n');
    }
    function exportIssue(platform) {
      var m = issueMaterial();
      if (!m) { toast('暂无可导出的分析结果'); return; }
      var name = 'iris-issue-' + platform + '-' + fmtFullTime(Date.now()) + '.md';
      if (downloadText(name, buildIssueMarkdown(platform, m))) {
        toast('✓ ' + (platform === 'github' ? 'GitHub Issue' : 'GitLab 工单') + ' 格式已导出（可直接粘贴）');
        log('已导出 ' + platform + ' 工单格式（标题/复现步骤/截图/方案/环境信息，' + m.errType + '）', 'ok');
      }
    }

    /* PDF：构造打印视图（textContent 重建零注入）→ window.print()；afterprint 复位 */
    function buildPrintDom(m) {
      var box = $('p7-print');
      if (!box) return null;
      box.innerHTML = '';
      var cur = m.cur;
      function el2(tag, cls, txt) { var x = document.createElement(tag); if (cls) x.className = cls; if (txt != null) x.textContent = txt; return x; }
      box.appendChild(el2('h1', null, 'Iris 排错报告'));
      box.appendChild(el2('p', 'pp-meta', m.env.time + ' · ' + m.channel + ' · ' + m.errType + ' · ' + m.env.iris));
      if (m.thumb) { var im = document.createElement('img'); im.className = 'pp-thumb'; im.src = m.thumb; box.appendChild(im); }
      box.appendChild(el2('h2', null, '【错误根因】'));
      box.appendChild(el2('p', null, cur.result.root || '（无）'));
      if (cur.ocrText) { box.appendChild(el2('h2', null, '【报错信息】')); box.appendChild(el2('pre', 'pp-code', String(cur.ocrText).slice(0, 6000))); }
      box.appendChild(el2('h2', null, '【修复方案】'));
      box.appendChild(el2('p', null, cur.result.plan || '（见修复代码）'));
      box.appendChild(el2('h2', null, '【修复代码】'));
      box.appendChild(el2('pre', 'pp-code', cur.result.code || '（无）'));
      box.appendChild(el2('h2', null, '【执行结果】'));
      box.appendChild(el2('p', null, cur.outcome === 'ok' ? 'ZCode 沙箱执行通过' : cur.outcome === 'fail' ? 'ZCode 沙箱执行失败' : cur.outcome === 'static-ok' ? '静态校验通过' : cur.outcome === 'static-fail' ? '静态校验失败' : '待执行验证'));
      box.appendChild(el2('h2', null, '【环境信息】'));
      box.appendChild(el2('p', 'pp-meta', m.env.ua + ' · ' + m.env.lang + ' · ' + m.env.screen + ' · ' + m.env.origin));
      return box;
    }
    function exportPdf() {
      var m = issueMaterial();
      if (!m) { toast('暂无可导出的分析结果'); return; }
      if (!buildPrintDom(m)) { toast('打印容器缺失'); return; }
      document.body.classList.add('p7-printing');
      log('[P7] 已生成打印视图：在打印预览中选择「另存为 PDF」即得报告 PDF（浏览器原生，零第三方库）', 'info');
      var done = function () {
        document.body.classList.remove('p7-printing');
        window.removeEventListener('afterprint', done);
      };
      window.addEventListener('afterprint', done);
      setTimeout(function () { window.print(); }, 60); // 等待样式应用
    }

    /* ============ 阶段4-2 全局快捷键（冲突避让 + 设置面板） ============
       设置键位（localStorage iris.p7.keys：{id:{combo,enabled}}）：
         focus=Ctrl+Shift+I / paste=Ctrl+V / analyze=Ctrl+Enter / copyCode=Ctrl+Shift+C
       红线 5：浏览器/系统占用快捷键（F12、Ctrl+W/T/N、Ctrl+Shift+I/C/J/K 等）注册时自动跳过并记录，
       绝不 preventDefault 劫持原生行为。 */
    var KEY_DEFS = [
      { id: 'focus', label: '唤起/聚焦 Iris 工作台', def: 'ctrl+shift+i', browser: true, desc: '浏览器 DevTools 保留键，默认跳过（可在设置改绑）' },
      { id: 'paste', label: '粘贴截图/文本自动分析', def: 'ctrl+v', native: true, desc: '由页面 paste 事件原生承接（P1/P6 既有链路），无需劫持按键' },
      { id: 'analyze', label: '触发当前分析', def: 'ctrl+enter' },
      { id: 'copyCode', label: '一键复制修复代码', def: 'ctrl+shift+c', browser: true, desc: '浏览器 DevTools 保留键，默认跳过（可在设置改绑）' }
    ];
    /* 浏览器/系统保留组合（小写归一化后精确匹配；不在此表内的组合才允许注册） */
    var RESERVED = {};
    ['f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12',
      'ctrl+n', 'ctrl+t', 'ctrl+w', 'ctrl+m', 'ctrl+shift+n', 'ctrl+shift+t', 'ctrl+shift+w',
      'ctrl+shift+i', 'ctrl+shift+j', 'ctrl+shift+c', 'ctrl+shift+k', 'ctrl+shift+e', 'ctrl+shift+m', 'ctrl+shift+delete',
      'ctrl+alt+delete', 'alt+f4', 'ctrl+tab', 'ctrl+shift+tab'
    ].forEach(function (k) { RESERVED[k] = 1; });
    var KEYS_STORE = 'iris.p7.keys';
    function persistDefaults() { try { localStorage.setItem(KEYS_STORE, JSON.stringify(keys)); } catch (e) {} }
    var keys = {};
    (function loadKeys() {
      var stored = null;
      try { stored = JSON.parse(localStorage.getItem(KEYS_STORE) || 'null'); } catch (e) {}
      KEY_DEFS.forEach(function (d) {
        var s = stored && stored[d.id];
        keys[d.id] = {
          combo: s && typeof s.combo === 'string' ? s.combo : d.def,
          enabled: s ? s.enabled !== false : !d.browser // 浏览器占用项默认禁用（如实标注），用户改绑非保留键后可启用
        };
      });
      persistDefaults(); // [P7] 首载即落盘默认配置（本机 iris.p7.keys）
    })();
    function persistKeys() {
      try { localStorage.setItem(KEYS_STORE, JSON.stringify(keys)); } catch (e) {}
    }
    function comboOf(e) {
      var s = '';
      if (e.ctrlKey) s += 'ctrl+';
      if (e.metaKey) s += 'meta+';
      if (e.altKey) s += 'alt+';
      if (e.shiftKey && e.key.toLowerCase() !== 'shift') s += 'shift+';
      s += String(e.key).toLowerCase();
      return s;
    }
    var keyActions = {
      analyze: function () {
        if (p6 && p6.isTextMode()) { p6.runDiagnose(); return; }
        if (!el.analyze.disabled) el.analyze.click();
        else toast('请先上传截图');
      },
      copyCode: function () {
        var cur = currentRound();
        if (cur && cur.result.code) copyText(cur.result.code, '修复代码已复制到剪贴板');
        else toast('当前没有可复制的修复代码');
      },
      focus: function () {
        window.focus();
        el.drop.scrollIntoView({ block: 'nearest' });
        el.fileInput.focus();
      },
      paste: function () { /* paste 事件原生承接，见 KEY_DEFS.native */ }
    };
    var skipped = []; // 注册时被保留键占用的记录（展示于设置面板）
    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented) return;
      var tag = (e.target && e.target.tagName || '').toUpperCase();
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        if (!(e.ctrlKey && e.key === 'Enter')) return; // 输入框内仅放行 Ctrl+Enter（日志框既有语义一致）
      }
      var combo = comboOf(e);
      if (RESERVED[combo]) return; // 双保险：即便用户改绑到保留键也绝不拦截
      for (var id in keys) {
        var k = keys[id];
        if (!k.enabled || k.combo !== combo) continue;
        var def = KEY_DEFS.filter(function (d) { return d.id === id; })[0];
        if (def && def.native) continue; // paste 走原生事件
        e.preventDefault();
        try { keyActions[id](); } catch (err) { /* 动作失败不劫持按键 */ }
        return;
      }
    });
    KEY_DEFS.forEach(function (d) {
      if (d.browser && keys[d.id] && keys[d.id].combo === d.def) skipped.push(d.label + '（' + d.def.toUpperCase().replace('+', ' + ').toLowerCase() + '）：浏览器保留，已跳过注册');
    });
    if (skipped.length) log('[P7] 快捷键冲突避让：' + skipped.join('；') + '；可在「快捷键设置」改绑非保留组合', 'info');

    function renderKeysBox() {
      var box = $('p7-keys-list');
      if (!box) return;
      box.innerHTML = '';
      KEY_DEFS.forEach(function (d) {
        var k = keys[d.id];
        var row = document.createElement('div'); row.className = 'p7-key-row';
        var lab = document.createElement('label'); lab.className = 'p7-key-name';
        var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = k.enabled && !d.native;
        cb.disabled = !!d.native || (d.browser && k.combo === d.def);
        cb.addEventListener('change', function () { k.enabled = cb.checked; persistKeys(); renderKeysBox(); });
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(' ' + d.label));
        row.appendChild(lab);
        var combo = document.createElement('input');
        combo.className = 'p7-key-combo mono'; combo.value = k.combo; combo.size = 14; combo.readOnly = true;
        combo.title = '点击后按下新组合键改绑（Esc 取消；浏览器保留键会被拒绝）';
        var capturing = false;
        combo.addEventListener('click', function () {
          if (capturing) return;
          capturing = true; combo.value = '按下组合键…';
          var onKey = function (ev) {
            ev.preventDefault(); ev.stopPropagation();
            var c = comboOf(ev);
            document.removeEventListener('keydown', onKey, true);
            capturing = false;
            if (c === 'escape') { combo.value = k.combo; renderKeysBox(); return; }
            if (RESERVED[c]) { toast('「' + c.toUpperCase() + '」是浏览器保留快捷键，不可占用（红线：不劫持原生行为）'); combo.value = k.combo; renderKeysBox(); return; }
            k.combo = c; k.enabled = true; persistKeys();
            log('[P7] 快捷键已改绑：' + d.label + ' → ' + c, 'ok');
            renderKeysBox();
          };
          document.addEventListener('keydown', onKey, true);
        });
        row.appendChild(combo);
        var st = document.createElement('span'); st.className = 'p7-key-status';
        st.textContent = d.native ? '原生 paste 承接' :
          (d.browser && k.combo === d.def ? '浏览器已占用 · 自动跳过' : (k.enabled ? '已启用' : '已禁用'));
        row.appendChild(st);
        if (d.desc) { var dd = document.createElement('div'); dd.className = 'p7-key-desc'; dd.textContent = d.desc; row.appendChild(dd); }
        box.appendChild(row);
      });
    }

    /* ============ 阶段4-3 批量文件夹导入（webkitdirectory + 目录拖拽，过滤后 ≤10 复用批量队列） ============ */
    var FOLDER_MAX = 10;
    function isLogLike(name, type) {
      return /\.(log|txt|err)$/i.test(name || '') || (type || '') === 'text/plain';
    }
    function isImageLike(name, type) {
      return type === 'image/png' || type === 'image/jpeg' || /\.(png|jpe?g)$/i.test(name || '');
    }
    /* 目录遍历（webkitGetAsEntry，深度≤3、扫描上限 200） */
    function collectFromItems(dt) {
      var files = [];
      var entries = [];
      var items = dt && dt.items;
      if (!items) return Promise.resolve(files);
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.kind !== 'file') continue;
        var entry = it.webkitGetAsEntry && it.webkitGetAsEntry();
        if (entry) entries.push(entry); else { var f = it.getAsFile(); if (f) files.push(f); }
      }
      if (!entries.length) return Promise.resolve(files);
      var stopAt = Date.now() + 8000; // 超时保护（超大目录不冻结页面）
      function walk(en, depth) {
        if (depth > 3 || files.length > 200 || Date.now() > stopAt) return Promise.resolve();
        if (en.isFile) {
          return new Promise(function (res) {
            en.file(function (f) { if (files.length <= 200) files.push(f); res(); }, function () { res(); });
          });
        }
        if (en.isDirectory) {
          return new Promise(function (res) {
            var reader = en.createReader();
            var acc = [];
            function readBatch() {
              reader.readEntries(function (chunk) {
                if (!chunk.length) {
                  var chain = Promise.resolve();
                  acc.forEach(function (child) { chain = chain.then(function () { return walk(child, depth + 1); }); });
                  chain.then(res, res);
                } else { acc = acc.concat(chunk); readBatch(); }
              }, function () { res(); });
            }
            readBatch();
          });
        }
        return Promise.resolve();
      }
      var chain = Promise.resolve();
      entries.forEach(function (en) { chain = chain.then(function () { return walk(en, 0); }); });
      return chain.then(function () { return files; });
    }
    /* 文本文件诊断项（复用批量队列同一诊断链：stripNoise→特征→诊断 prompt→apiChat→parseResult） */
    function processTextItem(item) {
      return new Promise(function (res, rej) {
        var rd = new FileReader();
        rd.onerror = function () { rej(new Error('文本读取失败')); };
        rd.onload = function () {
          try {
            var stripped = p6 ? p6.stripNoise(String(rd.result || '')).text : String(rd.result || '');
            var focused = p6 && stripped.split('\n').length > 200 ? p6.focusLongText(stripped) : stripped;
            if (!focused.trim()) { rej(new Error('文件内容为空')); return; }
            item.ocrText = focused;
            var features = extractFeatures(focused);
            var body = Object.assign({ messages: [{ role: 'user', content: buildDiagPrompt(focused, features) }], temperature: 0.2 }, modelField());
            apiChat(body).then(function (j) {
              var r = parseResult(String(j.content || ''));
              item.root = r.root; item.plan = r.plan; item.code = r.code; item.lang = r.lang;
              item.level = classifyError(focused).level;
              item.status = 'ok';
              res();
            }, rej);
          } catch (e) { rej(e); }
        };
        rd.readAsText(item.file);
      });
    }
    function runFolderFiles(allFiles) {
      var files = (allFiles || []).filter(function (f) { return isImageLike(f.name, f.type) || isLogLike(f.name, f.type); });
      var rejected = (allFiles || []).length - files.length;
      if (!files.length) { toast('文件夹内没有报错截图（png/jpg）或日志文件（.log/.txt/.err）'); return; }
      if (files.length > FOLDER_MAX) {
        log('[P7] 文件夹导入：过滤后 ' + files.length + ' 个文件，超过单次上限 ' + FOLDER_MAX + ' 个，已整批拒绝，请分批导入（过滤掉非图片/非日志 ' + rejected + ' 个）', 'fail');
        toast('超过 ' + FOLDER_MAX + ' 个文件上限（已过滤 ' + rejected + ' 个非目标文件），请分批');
        return;
      }
      if (batch.running) { toast('批量分析进行中，请稍候'); return; }
      if (state.running) { toast('单图分析进行中，请稍候'); return; }
      if (p6 && p6.isTextMode()) { toast('文件夹导入仅支持截图通道，请先切回「上传截图」'); return; }
      batch.items = files.map(function (f, i) {
        return {
          _ord: i, file: f, name: f.name || ('文件' + (i + 1)),
          p7Text: !isImageLike(f.name, f.type),
          status: 'pending', level: null, long: false, longHeightPx: 0, segCount: 0,
          ocrText: '', root: '', plan: '', code: '', lang: '', error: '', ts: Date.now(), thumb: ''
        };
      });
      renderBatchList();
      batch.running = true;
      el.batchPick.disabled = true;
      log('—— [P7] 文件夹导入批量分析：' + files.length + ' 个文件（含日志文本项），队列依次执行 ——', 'info');
      var chain = Promise.resolve();
      batch.items.forEach(function (item) {
        chain = chain.then(function () {
          item.status = 'running'; item.ts = Date.now(); renderBatchList();
          return (item.p7Text ? processTextItem(item) : batchProcessItem(item)).catch(function (e) {
            item.status = 'fail'; item.error = (e && e.message) || String(e);
            if (e instanceof BudgetError) { item.error = '预算熔断：' + item.error; throw { stopQueue: true, message: item.error }; }
            log('[P7] 导入项[' + item.name + '] 失败：' + item.error, 'fail');
          }).then(function () { renderBatchList(); });
        });
      });
      chain.catch(function (e) {
        if (e && e.stopQueue) batch.items.forEach(function (it) { if (it.status === 'pending') { it.status = 'fail'; it.error = '已跳过（预算熔断）'; } });
      }).then(function () {
        batch.running = false;
        el.batchPick.disabled = false;
        sortBatchItems(); renderBatchList();
        var okN = batch.items.filter(function (it) { return it.status === 'ok'; }).length;
        log('—— [P7] 文件夹导入批量结束：' + okN + '/' + batch.items.length + ' 成功 ——', okN ? 'ok' : 'fail');
        if (okN) toast('导入批量完成：' + okN + '/' + batch.items.length + ' 成功');
      });
    }
    function wireFolderDrop() {
      /* 捕获层目录拖拽守卫（capture 先于 P1/P6 既有 bubble drop 处理，目录场景吞掉事件，单文件行为零变化） */
      function hasDir(e) {
        var items = e.dataTransfer && e.dataTransfer.items;
        if (!items) return false;
        for (var i = 0; i < items.length; i++) {
          try {
            var en = items[i].webkitGetAsEntry && items[i].webkitGetAsEntry();
            if (en && en.isDirectory) return true;
          } catch (err) {}
        }
        return false;
      }
      window.addEventListener('drop', function (e) {
        if (!hasDir(e)) return;
        e.preventDefault(); e.stopPropagation();
        if (state.running || batch.running) { toast('分析进行中，请稍候'); return; }
        collectFromItems(e.dataTransfer).then(function (files) { runFolderFiles(files); });
      }, true);
      /* webkitdirectory 按钮（特性检测：不支持 → 隐藏并说明） */
      var btn = $('p7-folder-btn'), inp = $('p7-folder-input');
      var supported = 'webkitdirectory' in HTMLInputElement.prototype;
      if (btn) btn.style.display = supported ? '' : 'none';
      if (inp && supported) {
        inp.addEventListener('change', function () {
          var files = Array.prototype.slice.call(inp.files || []);
          inp.value = '';
          if (files.length) runFolderFiles(files);
        });
        btn.addEventListener('click', function () { if (!batch.running && !state.running) inp.click(); });
      } else if (btn) {
        log('[P7] 当前浏览器不支持 webkitdirectory 文件夹选择，按钮已隐藏（拖拽目录同样受特性限制）', 'info');
      }
    }

    /* ============ 阶段4-4 历史快速操作（上下键切换 / 批量删除筛选结果 / JSON 导出） ============ */
    function wireHistOps() {
      var list = el.historyList;
      if (list) {
        list.tabIndex = 0;
        list.addEventListener('keydown', function (e) {
          var items = Array.prototype.slice.call(list.querySelectorAll('.p7-kb-item'));
          if (!items.length) return;
          var curIdx = items.findIndex(function (x) { return x.classList.contains('is-sel'); });
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            items.forEach(function (x) { x.classList.remove('is-sel'); });
            var next = e.key === 'ArrowDown' ? Math.min(items.length - 1, curIdx + 1) : Math.max(0, curIdx - 1);
            items[next].classList.add('is-sel');
            items[next].scrollIntoView({ block: 'nearest' });
          } else if (e.key === 'Enter' && curIdx >= 0) {
            e.preventDefault();
            p7kb.view(items[curIdx].getAttribute('data-kb-id'));
          }
        });
      }
      var exp = $('p7-history-json');
      if (exp) exp.addEventListener('click', function () {
        var arr = p7kb.load();
        if (!arr.length) { toast('知识库暂无记录'); return; }
        if (downloadText('iris-history-' + fmtFullTime(Date.now()) + '.json', JSON.stringify(arr, null, 2), 'application/json')) {
          toast('✓ 全部历史已导出 JSON（' + arr.length + ' 条，本机备份）');
          log('已导出全部历史 JSON（' + arr.length + ' 条，含收藏/标签/使用计数；数据不出本机）', 'ok');
        }
      });
      var delBtn = $('p7-kb-batchdel'), armed = 0, armTimer = 0;
      if (delBtn) delBtn.addEventListener('click', function () {
        var ids = p7kb.lastIds();
        if (!ids.length) { toast('当前筛选结果为空'); return; }
        if (armed !== 1) {
          armed = 1; delBtn.textContent = '确认删除这 ' + ids.length + ' 条？';
          clearTimeout(armTimer);
          armTimer = setTimeout(function () { armed = 0; delBtn.textContent = '批量删除筛选结果'; }, 4000);
          return;
        }
        armed = 0; delBtn.textContent = '批量删除筛选结果'; clearTimeout(armTimer);
        var idSet = {};
        ids.forEach(function (x) { idSet[x] = 1; });
        var arr = p7kb.load().filter(function (r) { return !idSet[r.id]; });
        var deleted = p7kb.load().length - arr.length;
        p7kb.persist(arr);
        p7kb.render();
        toast('已删除 ' + deleted + ' 条筛选结果（收藏未纳入筛选时不受影响）');
        log('[P7] 历史批量删除：按当前筛选条件删除 ' + deleted + ' 条', 'info');
      });
    }

    /* ---------------- 装配 ---------------- */
    function wireP7Buttons() {
      var b;
      b = $('export-gh-btn'); if (b) b.addEventListener('click', function () { exportIssue('github'); });
      b = $('export-gl-btn'); if (b) b.addEventListener('click', function () { exportIssue('gitlab'); });
      b = $('export-pdf-btn'); if (b) b.addEventListener('click', exportPdf);
      renderKeysBox();
    }
    wireP7Buttons();
    wireFolderDrop();
    wireHistOps();
    if (skipped.length) renderKeysBox();

    return {
      KEY_DEFS: KEY_DEFS, keys: keys, RESERVED: RESERVED, comboOf: comboOf,
      isImageLike: isImageLike, isLogLike: isLogLike, FOLDER_MAX: FOLDER_MAX,
      runFolderFiles: runFolderFiles, buildIssueMarkdown: buildIssueMarkdown, issueMaterial: issueMaterial,
      exportIssue: exportIssue, exportPdf: exportPdf, renderKeys: renderKeysBox, keyActions: keyActions
    };
  })();
  window.__iris.p7.ux = p7ux;
  log('[P7] 效率体系就绪：GitHub/GitLab 工单与打印版 PDF 导出 · 快捷键（冲突自动避让）· 文件夹导入（过滤 ≤' + p7ux.FOLDER_MAX + ' 复用批量队列）· 历史键盘/批量删除/JSON 备份', 'info');

})();

