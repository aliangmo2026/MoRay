/* ============================================================
   [Iris MVP] 87_iris_bridge —— 应用中心 Iris 卡 → Iris 工作台桥接分片
   ------------------------------------------------------------
   定位（阶段4.1/4.2）：Iris 卡片配置（parts/86_home_apps.js APPS 数组 iris 行）
   的 action 值 'iris' 不在 86 既有派发分支内 —— 86 对未知真值 action 静默跳过
   （bindCardEvents 的 if/else if 链），本分片在 document 级以 composedPath 委托
   承接该点击（与 86 自身「运行时注入、不动骨架」范式一致，docs/day24.12）。
   红线符合性：86_home_apps.js 仅改 iris 行配置字段（status/desc/action），
   核心逻辑零改动；本文件为新增分片，不改任何既有分片/骨架/vendor/后端。

   跳转目标：Iris 工作台静态页（构建产物部署于 D:\MoRay\wallpapers\iris\，
   经 MoRay 既有静态挂载 main.py:125-127 同源服务；规范源文件在 D:\Iris\web\）。
   使用相对路径 'wallpapers/iris/index.html'：
     - http://127.0.0.1:8000/ 打开 → 解析为 /wallpapers/iris/index.html（同源）
     - file:// 直开 moray-workbench.html → 解析为 D:\MoRay\wallpapers\iris\index.html
   两种运行态均可正常导航（http 页面导航到 file:// 被浏览器禁止，故不做该尝试）。

   上下文传递（阶段4.2，Argus「全局单例 + 运行时状态」方案的跨页等价实现）：
     ?theme=<MoraySettings.get('theme')>  —— 主题配置（110_polish.js:341 同键名）
     ?session=<iris 会话标识>              —— 预算桶会话信息（20_ai.js:801-815 惯例）
   返回：Iris 页 history.back()（bfcache 保留 MoRay 内存态）/ 回退根路径；
   应用中心状态不受跳转影响（无共享可变状态）。
   ============================================================ */
(function () {
  'use strict';

  var IRIS_PAGE = 'wallpapers/iris/index.html';

  function currentTheme() {
    try {
      if (typeof MoraySettings === 'object' && MoraySettings && typeof MoraySettings.get === 'function') {
        var t = MoraySettings.get('theme');
        if (t === 'dark' || t === 'light' || t === 'auto') return t;
      }
    } catch (e) { /* 设置层未就绪时用默认值 */ }
    return 'dark';
  }

  function irisSessionId() {
    // 与 Iris 页同键名（同源 http 下共享 localStorage；file:// 下各页自持，Iris 页有同链兜底）
    var KEY = 'iris.sessionId';
    try {
      var v = localStorage.getItem(KEY);
      if (!v) {
        v = 'iris-local-' + Date.now().toString(36);
        localStorage.setItem(KEY, v);
      }
      return v;
    } catch (e) { return 'ephemeral-' + Date.now().toString(36); }
  }

  function goIrisFromCard() {
    var qs = '?theme=' + encodeURIComponent(currentTheme()) +
             '&session=' + encodeURIComponent(irisSessionId());
    // 先 HEAD 预检部署产物是否就位（StaticFiles 对目录请求返回 404，故探测完整文件名）
    fetch(IRIS_PAGE, { method: 'HEAD' }).then(function (res) {
      if (res && res.ok) {
        window.location.href = IRIS_PAGE + qs;
      } else {
        notifyMissing();
      }
    }).catch(notifyMissing);
  }

  function notifyMissing() {
    if (typeof showNotification === 'function') {
      showNotification('Iris 工作台未部署',
        '缺少 wallpapers/iris/index.html（部署脚本：D:\\Iris\\tools\\deploy_to_moray.ps1）。' +
        '也可直接双击 D:\\Iris\\web\\index.html 使用', 'warning', 6000);
    }
  }

  // document 级委托（86_home_apps.js bindCardEvents 同款 composedPath 兜底写法）
  document.addEventListener('click', function (e) {
    var path = typeof e.composedPath === 'function' ? e.composedPath() : [];
    for (var i = 0; i < path.length; i++) {
      var n = path[i];
      if (n && n.nodeType === 1 && n.classList && n.classList.contains('homeapps-card')) {
        if (n.getAttribute && n.getAttribute('data-app-id') === 'iris') {
          goIrisFromCard();
        }
        return; // 命中任意卡片即停止上溯；非 iris 卡交给 86 原派发逻辑
      }
    }
  });
})();

/* ============================================================
   [Iris P4] AGI 联调模块 —— 主对话 Iris 工具注册与执行器
   ------------------------------------------------------------
   定位（P4 任务书阶段2/3/4）：把 P3 IrisAgent 原子能力封装为
   ToolRegistry 标准 Function Calling 工具 fix_code_error_screenshot，
   实现「对话发截图 → AGI 自动调度 Iris → 结果卡片直接返回对话」。
   范式：本文件尾部纯追加（P3 iris.js 末尾追加 IrisAgent 同款），
   既有代码 0 行修改；iframe 复用同一份部署产物（红线6 双向兼容）。

   依赖（全部 typeof 守卫，缺失即跳过注册，不影响主对话）：
     ToolRegistry（125_tools.js 公开注册表）/ AppState / MoraySettings /
     showNotification / escapeHtml / refreshIcons / hljs
   通道：隐藏 iframe（?session=agi → iris:agi 独立预算桶，iris.js:288-296
   URL 参数覆盖实现，零后端改动）→ contentWindow.IrisAgent（同源直访，
   8011/8000 实测无 X-Frame-Options/CSP，docs/p4_audit.md §2.1）。
   回灌契约：data 前 4000 字符供模型（100_gateway.js:1426），前 10000 字符
   进步骤卡 resultFull（持久化 IndexedDB，历史回看恢复数据源）。
   ============================================================ */
(function () {
  'use strict';
  if (window.__IrisAGI) return; // 幂等防御
  var AGI = {};
  window.__IrisAGI = AGI;

  /* ---------------- 常量（docs/p4_audit.md §2.2 契约） ---------------- */
  var TOOL_NAME = 'fix_code_error_screenshot';
  var IRIS_URL = 'wallpapers/iris/index.html?session=agi'; // iris:agi 独立桶
  var FRAME_TIMEOUT_MS = 20000;   // iframe 就绪超时
  var TOOL_TIMEOUT_MS = 330000;   // 对齐 Iris LLM_TIMEOUT 300s + 30s 余量
  var MAX_ROUNDS = 3;             // 与工作台 MAX_ROUNDS 对齐（iris.js:94）
  var RATE_WINDOW_MS = 60000;     // 频率窗
  var RATE_MAX = 3;               // 每窗最多 3 次
  var DEDUP_MS = 10 * 60000;      // 同图 10 分钟去重
  var FILE_PROTOCOL = (location.protocol === 'file:');

  /* ---------------- 小工具 ---------------- */
  function esc(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(String(s == null ? '' : s));
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(title, body, type, ms) {
    if (typeof showNotification === 'function') {
      try { showNotification(title, body, type || 'info', ms || 3200); } catch (e) { /* 忽略 */ }
    }
  }
  function hashStr(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = (((h << 5) + h) + s.charCodeAt(i)) >>> 0;
    return h.toString(36) + '-' + s.length.toString(36);
  }
  function nowStr() {
    var d = new Date();
    function p(v) { return (v < 10 ? '0' : '') + v; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  /* ---------------- 隐藏 iframe 管理（懒加载 + 单飞 + 就绪确认） ----------------
     就绪判定：iframe load 后 contentWindow.IrisAgent 存在且 analyzeImage 可用；
     失败可重试（frameP 复位）。file:// 态浏览器禁止跨帧脚本访问，如实降级。 */
  var frameEl = null, frameAgent = null, frameP = null;
  function ensureAgent() {
    if (FILE_PROTOCOL) {
      return Promise.reject(new Error('IRIS_UNSUPPORTED_CONTEXT: AGI 联调需 http 部署态（file:// 下浏览器禁止跨帧访问，请经 MoRay 打开）'));
    }
    if (frameAgent) return Promise.resolve(frameAgent);
    if (frameP) return frameP;
    frameP = new Promise(function (resolve, reject) {
      var el = document.createElement('iframe');
      el.src = IRIS_URL;
      el.style.cssText = 'position:fixed;width:1px;height:1px;left:-9999px;top:-9999px;opacity:0;pointer-events:none;border:0;';
      el.setAttribute('aria-hidden', 'true');
      el.setAttribute('title', 'Iris AGI 服务帧');
      var settled = false, polls = 0;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        reject(new Error('IRIS_FRAME_TIMEOUT: Iris 服务帧加载超时（' + Math.round(FRAME_TIMEOUT_MS / 1000) + 's），请确认 MoRay 后端在线'));
      }, FRAME_TIMEOUT_MS);
      function poll() {
        if (settled) return;
        try {
          var w = el.contentWindow;
          if (w && w.IrisAgent && typeof w.IrisAgent.analyzeImage === 'function') {
            settled = true;
            clearTimeout(timer);
            frameEl = el;
            frameAgent = w.IrisAgent;
            resolve(frameAgent);
            return;
          }
        } catch (e) { /* 跨帧访问异常 → 继续轮询到超时 */ }
        if (++polls > 200) return; // 20s 内每 100ms 一次，由超时兜底
        setTimeout(poll, 100);
      }
      el.addEventListener('load', poll);
      el.addEventListener('error', function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error('IRIS_FRAME_ERROR: Iris 页面加载失败（部署产物缺失？部署脚本 deploy_to_moray.ps1）'));
      });
      document.body.appendChild(el);
      poll();
    });
    frameP = frameP.catch(function (e) { frameP = null; if (frameEl) { try { frameEl.remove(); } catch (e2) { /* 已移除 */ } frameEl = null; } throw e; });
    return frameP;
  }

  /* ---------------- 图片提取（阶段3 辅助；audit §1.3 结论） ----------------
     发送当轮 AppState.attachments 优先；历史轮从 AppState.messages 倒序取
     最近一条带 attachments 的 user 消息（消息对象永久携带 dataUrl）。 */
  function lastUserImage() {
    try {
      var atts = (typeof AppState !== 'undefined' && AppState.attachments) || [];
      for (var i = atts.length - 1; i >= 0; i--) {
        if (atts[i] && atts[i].dataUrl) return atts[i];
      }
      var msgs = (typeof AppState !== 'undefined' && AppState.messages) || [];
      for (var j = msgs.length - 1; j >= 0; j--) {
        var m = msgs[j];
        if (m && m.role === 'user' && m.attachments && m.attachments.length) {
          for (var k = m.attachments.length - 1; k >= 0; k--) {
            if (m.attachments[k] && m.attachments[k].dataUrl) return m.attachments[k];
          }
        }
      }
    } catch (e) { /* AppState 未就绪 → 视为无图 */ }
    return null;
  }

  /* ---------------- 防滥用器（任务书阶段3.3） ---------------- */
  var rateWin = [], seen = {};
  function precheck(dataUrl) {
    var now = Date.now();
    while (rateWin.length && now - rateWin[0] > RATE_WINDOW_MS) rateWin.shift();
    if (rateWin.length >= RATE_MAX) {
      return { ok: false, reason: '调用频率限制（每分钟最多 ' + RATE_MAX + ' 次），请稍后再试' };
    }
    var h = hashStr(String(dataUrl).slice(0, 4096));
    if (seen[h] && now - seen[h] < DEDUP_MS) {
      return { ok: false, reason: '同一张截图 10 分钟内已分析过（防重复调用），如需重新分析请到 Iris 工作台操作' };
    }
    seen[h] = now;
    rateWin.push(now);
    return { ok: true };
  }
  /* 单图连续调用不超过 1 次（工具轮内防循环：模型对同一图重复 tool_call 时拒绝） */
  var lastToolCall = { hash: '', at: 0 };
  function toolDedup(dataUrl) {
    var h = hashStr(String(dataUrl).slice(0, 4096));
    var now = Date.now();
    if (lastToolCall.hash === h && now - lastToolCall.at < 15000) {
      return { ok: false, reason: '该截图刚刚已分析（15 秒内同图不重复调用），结果见上方卡片' };
    }
    lastToolCall = { hash: h, at: now };
    return { ok: true };
  }

  /* ---------------- OCR 特征前置校验（任务书阶段3.3：非报错图终止调用） ----------------
     单闸（代码结构信号）未检出 → cancelTask 终止，省 30 次/天桶。
     判定数据源：P4 起 task-ocr-done 事件载荷携带 OCR 文本（head+tail 4000 字符），
     守卫同步判定并同步 cancelTask —— agiDiagnose 入口为同步 checkCancelled 检查点，
     故诊断 LLM 调用发生前即截停（iris.js [P4] 段）。守卫实现见下方 wireOcrGuard。 */
  var CODE_PAT = /(\.py|\.js|\.java|\.ts|\.c|\.cpp|\.go|\.rs|Traceback|File\s+"|line\s+\d+|def\s+\w+\(|import\s+\w+|from\s+\w+\s+import|function\s+\w+|const\s+\w+|let\s+\w+|var\s+\w+|=>|console\.|print\(|行\s*\d+|第\s*\d+\s*行)/i;

  /* ---------------- 标准错误 → 人话文案（契约对齐：任务书阶段2.3 错误码映射） ---------------- */
  var CODE_TEXT = {
    INVALID_IMAGE: '图片无效（仅支持 png/jpg 且 ≤5MB）',
    PREPROCESS_FAILED: '图片预处理失败',
    OCR_FAILED: 'OCR 识别失败（图片中可能没有可识别文本）',
    DIAGNOSIS_FAILED: '诊断失败（模型未返回有效结果）',
    VALIDATION_FAILED: '执行验证异常',
    BUDGET_EXCEEDED: 'Iris 预算已达今日上限（30 次/天），明日自动清零；可到应用中心打开 Iris 工作台查看配额',
    TIMEOUT: '模型推理超时（单次请求 300s 上限），可点击重试',
    TASK_NOT_FOUND: '任务不存在（可能已被清理或页面刷新）',
    TASK_CANCELLED: '任务已取消',
    UNSUPPORTED_OPERATION: '不支持的操作',
    NOT_ERROR_SCREENSHOT: '非代码报错截图',
    IRIS_FRAME_TIMEOUT: 'Iris 服务帧加载超时',
    IRIS_FRAME_ERROR: 'Iris 页面加载失败',
    IRIS_NETWORK: '无法连接 MoRay 网关',
    IRIS_UNSUPPORTED_CONTEXT: '当前环境不支持 AGI 联调'
  };
  function humanize(err) {
    var msg = String((err && err.message) || err || '未知错误');
    // 网关不可达：P3 按所处阶段归类（OCR_FAILED/DIAGNOSIS_FAILED），语义上优先按网络错误呈现
    if (/无法连接 MoRay 网关/.test(msg)) {
      return { code: 'IRIS_NETWORK', text: '【无法连接 MoRay 网关】' + msg + '；确认后端在线后可点「重试」' };
    }
    var rawCode = String((err && err.code) || '');
    // P3 链上 429 抛的是原始 BudgetError（无 code 字段）→ 按 name 归类
    if (!rawCode && err && err.name === 'BudgetError') rawCode = 'BUDGET_EXCEEDED';
    var m = msg.match(/IRIS_([A-Z_]+)\s*:\s*([\s\S]+)/) || msg.match(/^([A-Z_]{6,})\s*:\s*([\s\S]+)/);
    // 错误码来源优先级：消息内前缀（IRIS_/大写码）> AgiError.code（P3 reject 载荷）> 未知
    var code = m ? m[1] : ((rawCode && CODE_TEXT[rawCode]) ? rawCode : 'IRIS_UNKNOWN');
    var text = m ? m[2] : msg;
    if (CODE_TEXT[code]) text = '【' + CODE_TEXT[code] + '】' + text;
    return { code: code, text: text };
  }

  /* ---------------- 回灌摘要组装（R1：控制 ≤4000，code 全文优先） ---------------- */
  function clip(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n) + '…（截断）' : s;
  }
  function buildResultData(res, extra) {
    var d = extra || {};
    d.ok = true;
    d.taskId = res.taskId;
    d.roundId = res.roundId || null;
    d.status = res.status;
    if (res.features) {
      d.errorType = res.features.errorType || null;
      d.filePath = res.features.filePath || null;
      d.lineNumber = res.features.lineNumber || null;
      d.language = res.fix && res.fix.language ? res.fix.language : (res.features.language || null);
    }
    if (res.diagnosis) {
      d.severity = res.diagnosis.severity;
      d.rootCause = clip(res.diagnosis.rootCause, 400);
      d.description = clip(res.diagnosis.description, 200);
    }
    if (res.fix) {
      d.code = String(res.fix.code || '');       // 修复代码全文（模型回灌核心，放前段）
      d.explanation = clip(res.fix.explanation, 200);
    }
    if (res.validation) {
      d.validation = {
        mode: res.validation.mode, ok: res.validation.ok, exitCode: res.validation.exitCode,
        stdout: clip(res.validation.stdout, 300), stderr: clip(res.validation.stderr, 300),
        durationMs: res.validation.durationMs
      };
    } else {
      d.validation = null;
    }
    d.history = (res.history || []).map(function (h) { return { round: h.round, status: h.status, summary: clip(h.summary, 80) }; });
    d.note = '结果卡片已渲染在对话中（含完整修复代码与执行输出）。若验证失败需继续修复，请再次调用本工具并传 taskId="' + res.taskId + '" 与 last_error（新的报错文本）；同一 taskId 最多 ' + MAX_ROUNDS + ' 轮。请基于以上结果向用户说明根因与修复方案，勿编造代码。';
    return d;
  }

  /* ---------------- 轮次状态修正（P4 显示层） ----------------
     P3 的 task.rounds[].outcome 不随 runValidation 回填（history 为完成时快照，
     验证结果只绑定到 result.validation）。P4 卡片需要展示"每轮是否通过"，
     故在执行器内按验证结果记录轮次状态，并在组装 data 时以克隆方式修正，
     不改动 P3 任务对象本身。 */
  function roundStatusOf(v, ok) {
    if (!v) return null;
    return ok ? (v.mode === 'zcode' ? 'ok' : 'static-ok') : (v.mode === 'zcode' ? 'fail' : 'static-fail');
  }
  function historyWithStatus(res, roundStatus) {
    return (res.history || []).map(function (h) {
      var st = roundStatus && roundStatus[h.round];
      return { round: h.round, status: st || h.status, summary: h.summary };
    });
  }
  function withFixedHistory(res, roundStatus) {
    return Object.assign({}, res, { history: historyWithStatus(res, roundStatus) });
  }

  /* ---------------- 执行器核心（任务书阶段2.2 逻辑 1-6 + 阶段4.3 多轮） ----------------
     fixFlowWithRounds(opts) → Promise<{res, agent}>
     opts: { image, runValidation, language, taskId?, lastError?, source, onEvent? }
     流程：取图 → 防滥用 → analyzeImage（OCR 守卫）→ [runValidation →
     失败自动 continueFix(stderr) → 再验证，自动迭代 ≤ MAX_ROUNDS-1 次]。 */
  function fixFlowWithRounds(opts) {
    return ensureAgent().then(function (agent) {
      var img = opts.image || null;
      var isContinue = !!(opts.taskId && opts.lastError);
      if (!isContinue && !img) {
        return Promise.reject(new Error('IRIS_NO_IMAGE: 未找到可分析的图片。请先在对话中上传报错截图，再调用本工具'));
      }
      if (!isContinue && img) {
        var pc = opts.source === 'tool' ? toolDedup(img.dataUrl) : precheck(img.dataUrl);
        if (!pc.ok) return Promise.reject(new Error('IRIS_DEDUP: ' + pc.reason));
      }
      var guardHandler = null;
      var chain;
      if (isContinue) {
        chain = agent.continueFix(String(opts.taskId), String(opts.lastError), opts.language ? { language: opts.language } : {});
      } else {
        chain = agent.analyzeImage(img.dataUrl, Object.assign(opts.language ? { language: opts.language } : {}, { enhanceMode: 'auto' }));
        guardHandler = wireOcrGuard(agent);
      }
      var autoRounds = 0;
      var roundStatus = {}; // 轮次号 → 验证状态（P3 outcome 不回填，P4 显示层修正）
      function step(res) {
        if (opts.runValidation !== true || !res || !res.fix || !res.fix.code) return res;
        return agent.runValidation(res.fix.code, { language: opts.language || res.fix.language || undefined, taskId: res.taskId })
          .then(function (v) {
            res.validation = v.validation; // 验证任务回填（统一结构 §2.2），保留 fix/diagnosis
            var n = (res.history || []).length;
            if (n >= 1) roundStatus[n] = roundStatusOf(v.validation, v.validation && v.validation.ok);
            var failed = v.validation && v.validation.ok === false &&
              String((v.validation.stderr || v.validation.reason) || '').trim().length > 0;
            if (failed && (res.history || []).length < MAX_ROUNDS && autoRounds < MAX_ROUNDS - 1 && opts.autoContinue !== false) {
              autoRounds++;
              var errText = String(v.validation.stderr || v.validation.reason || '').slice(0, 1500);
              if (opts.onEvent) opts.onEvent({ type: 'auto-continue', round: n + 1, message: '验证未通过，自动携带 stderr 发起第 ' + (n + 1) + ' 轮修复' });
              return agent.continueFix(res.taskId, errText, opts.language ? { language: opts.language } : {}).then(step);
            }
            return res;
          });
      }
      return chain.catch(translateCancellation).then(step).then(function (res) {
        if (guardHandler) { try { agent.off('task-ocr-done', guardHandler); } catch (e) { /* 已移除 */ } }
        return { res: res, agent: agent, roundStatus: roundStatus };
      }, function (err) {
        if (guardHandler) { try { agent.off('task-ocr-done', guardHandler); } catch (e) { /* 已移除 */ } }
        throw err;
      });
    });
  }

  /* wireOcrGuard：订阅 ocr-done → 同步读事件载荷中的 OCR 文本（P4 起 payload.text 提供）
     → 未检出代码结构信号（CODE_PAT 单闸）→ 同步 cancelTask（agiDiagnose 入口为同步 checkCancelled 检查点，
     故能在诊断 LLM 调用前截停）→ analyzeImage promise 随 TASK_CANCELLED reject，
     由 translateCancellation 转译为友好提示。载荷无 text（防御性回退）时退回异步拉状态。 */
  var guardCancel = { taskId: null, at: 0 }; // 守卫触发记录（区分用户主动取消）
  function wireOcrGuard(agent) {
    var handler = function (e) {
      if (!e || e.kind !== 'analyze') return;
      var text = String((e.data && e.data.text) || '');
      if (text) {
        if (!CODE_PAT.test(text)) {
          guardCancel = { taskId: e.taskId, at: Date.now() };
          agent.cancelTask(e.taskId).catch(function () { /* 已终态 */ });
        }
        return;
      }
      // 防御性回退：旧载荷无 text 时异步拉全量文本再判定（取消落点推迟一个阶段）
      agent.getTaskStatus(e.taskId).then(function (snap) {
        var t2 = String((snap && snap.imageText) || '');
        if (!CODE_PAT.test(t2)) {
          guardCancel = { taskId: e.taskId, at: Date.now() };
          agent.cancelTask(e.taskId).catch(function () { /* 已终态 */ });
        }
      }).catch(function () { /* 拉状态失败不拦截（宁误放不误杀正常任务） */ });
    };
    agent.on('task-ocr-done', handler);
    return handler;
  }
  /* 守卫触发的取消 → 转译为「非报错截图」语义错误（模型与卡片均收到可读原因） */
  function translateCancellation(err) {
    var code = String((err && err.code) || '');
    if (code === 'TASK_CANCELLED' && guardCancel.taskId && (Date.now() - guardCancel.at < 600000)) {
      var e2 = new Error('IRIS_NOT_ERROR_SCREENSHOT: 图片 OCR 结果中未发现代码痕迹（未检出代码结构信号），已在诊断前取消分析以节省配额。普通照片/设计稿/文档截图请勿使用本工具；确属排错需求可到 Iris 工作台手动分析。');
      e2.code = 'NOT_ERROR_SCREENSHOT';
      return Promise.reject(e2);
    }
    return Promise.reject(err);
  }

  /* ---------------- 快照桥（localStorage：工作台恢复 + 步骤卡历史回看恢复） ---------------- */
  var SNAPSHOT_KEY = 'iris.agiTask.';
  function saveSnapshot(data) {
    try {
      var rec = Object.assign({ _ts: Date.now(), _v: 1 }, data);
      localStorage.setItem(SNAPSHOT_KEY + data.taskId, JSON.stringify(rec));
      // 简易 GC：超过 12 条清最旧
      var keys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(SNAPSHOT_KEY) === 0) keys.push(k);
      }
      if (keys.length > 12) {
        keys.map(function (k) { return { k: k, ts: (JSON.parse(localStorage.getItem(k) || '{}')._ts) || 0 }; })
          .sort(function (a, b) { return a.ts - b.ts; })
          .slice(0, keys.length - 12)
          .forEach(function (x) { localStorage.removeItem(x.k); });
      }
    } catch (e) { /* 隐私态/超限 → 静默降级（历史回看退化为步骤卡） */ }
  }
  function loadSnapshot(taskId) {
    try { return JSON.parse(localStorage.getItem(SNAPSHOT_KEY + String(taskId)) || 'null'); }
    catch (e) { return null; }
  }

  /* ---------------- 对话内结果卡片（任务书阶段4.1 三段式 + 实时区） ----------------
     挂载点：当前 AI 气泡 .ai-msg-inner 的 afterend（msg-bubble__ai-box 内）——
     流式重绘只更新 .ai-content（30_chat.js:1537-1539），完成态 fillAssistantBody
     只重写 .ai-msg-inner 内部（30_chat.js:913-926），两者均不覆盖本卡片。 */
  var STYLE_ID = 'iris-agi-style';
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = [
      '.iris-card{margin:8px 0 4px;border:1px solid var(--color-line-ghost,#3a3f4b);border-radius:12px;background:var(--color-surface-card,#232733);overflow:hidden;font-size:12px;}',
      '.iris-card__hd{display:flex;align-items:center;gap:6px;padding:8px 12px;background:var(--color-surface-panel,#1c202b);border-bottom:1px solid var(--color-line-ghost,#3a3f4b);font-weight:600;}',
      '.iris-card__bd{padding:10px 12px;}',
      '.iris-card__sec{margin-bottom:10px;}',
      '.iris-card__sec:last-child{margin-bottom:0;}',
      '.iris-card__ttl{font-size:10px;color:var(--color-text-tertiary,#9aa3b2);margin-bottom:4px;display:flex;align-items:center;gap:4px;font-weight:600;letter-spacing:.4px;}',
      '.iris-kv{display:flex;flex-wrap:wrap;gap:4px 10px;}',
      '.iris-kv span{color:var(--color-text-secondary,#c3cad6);}',
      '.iris-kv b{color:var(--color-text-primary,#e8ecf2);font-weight:600;}',
      '.iris-badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:10px;font-weight:600;}',
      '.iris-badge--fatal{background:rgba(239,68,68,.16);color:#f87171;}',
      '.iris-badge--warn{background:rgba(245,158,11,.16);color:#fbbf24;}',
      '.iris-badge--info{background:rgba(59,130,246,.16);color:#60a5fa;}',
      '.iris-badge--ok{background:rgba(34,197,94,.16);color:#4ade80;}',
      '.iris-cause{color:var(--color-text-secondary,#c3cad6);line-height:1.6;white-space:pre-wrap;word-break:break-word;}',
      '.iris-code{position:relative;margin-top:4px;border:1px solid var(--color-line-ghost,#3a3f4b);border-radius:8px;background:var(--color-surface-panel,#1c202b);overflow:hidden;}',
      '.iris-code pre{margin:0;padding:8px 10px;overflow:auto;max-height:320px;font-size:11px;line-height:1.5;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}',
      '.iris-code__bar{display:flex;align-items:center;gap:6px;padding:4px 10px;border-bottom:1px solid var(--color-line-ghost,#3a3f4b);font-size:10px;color:var(--color-text-tertiary,#9aa3b2);}',
      '.iris-code__copy{margin-left:auto;cursor:pointer;border:1px solid var(--color-line-ghost,#3a3f4b);border-radius:6px;padding:1px 8px;background:transparent;color:var(--color-text-secondary,#c3cad6);font-size:10px;}',
      '.iris-code__copy:hover{color:#67e8f9;border-color:#67e8f9;}',
      '.iris-acts{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-top:1px solid var(--color-line-ghost,#3a3f4b);background:var(--color-surface-panel,#1c202b);}',
      '.iris-btn{cursor:pointer;border:1px solid var(--color-line-ghost,#3a3f4b);border-radius:8px;padding:3px 10px;background:transparent;color:var(--color-text-secondary,#c3cad6);font-size:11px;display:inline-flex;align-items:center;gap:4px;}',
      '.iris-btn:hover{color:#67e8f9;border-color:#67e8f9;}',
      '.iris-btn--primary{color:#67e8f9;border-color:rgba(103,232,249,.5);}',
      '.iris-live{padding:6px 12px;border-top:1px dashed var(--color-line-ghost,#3a3f4b);font-size:10px;color:var(--color-text-tertiary,#9aa3b2);white-space:pre-wrap;word-break:break-word;max-height:140px;overflow:auto;}',
      '.iris-run{margin-top:6px;border:1px dashed var(--color-line-ghost,#3a3f4b);border-radius:8px;padding:6px 10px;font-size:11px;}',
      '.iris-run__io{margin-top:4px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:10px;white-space:pre-wrap;word-break:break-word;max-height:140px;overflow:auto;color:var(--color-text-secondary,#c3cad6);}',
      '.iris-quick{cursor:pointer;display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;border:1px dashed rgba(103,232,249,.55);color:#67e8f9;font-size:11px;background:rgba(103,232,249,.08);white-space:nowrap;}',
      '.iris-quick:hover{background:rgba(103,232,249,.16);}',
      '.iris-quick-x{cursor:pointer;color:var(--color-text-tertiary,#9aa3b2);font-size:12px;line-height:1;padding:0 1px;}',
      '.iris-quick-x:hover{color:#f87171;}',
      '.iris-hist{margin-top:6px;border-top:1px dashed var(--color-line-ghost,#3a3f4b);padding-top:6px;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);}'
    ].join('\n');
    document.head.appendChild(st);
  }

  function severityBadge(sev) {
    var cls = sev === 'fatal' ? 'iris-badge--fatal' : (sev === 'warn' ? 'iris-badge--warn' : 'iris-badge--info');
    var txt = sev === 'fatal' ? '致命' : (sev === 'warn' ? '警告' : '提示');
    return '<span class="iris-badge ' + cls + '">' + txt + '</span>';
  }
  function codeBlock(code, lang) {
    var langLabel = String(lang || 'text');
    return '<div class="iris-code"><div class="iris-code__bar"><span>' + esc(langLabel) + '</span>' +
      '<button class="iris-code__copy" data-iris-copy type="button">复制</button></div>' +
      '<pre><code class="language-' + esc(langLabel) + '">' + esc(code) + '</code></pre></div>';
  }
  function runBlock(validation) {
    if (!validation) return '';
    var okTxt = validation.ok ? '通过' : '未通过';
    var badge = validation.ok ? '<span class="iris-badge iris-badge--ok">验证' + okTxt + '</span>'
      : '<span class="iris-badge iris-badge--fatal">验证' + okTxt + '</span>';
    var io = '';
    var out = String(validation.stdout || '');
    var err = String(validation.stderr || '');
    if (out || err) {
      io = '<div class="iris-run__io">' + esc((out ? '[stdout]\n' + clip(out, 1200) : '') + (out && err ? '\n' : '') + (err ? '[stderr]\n' + clip(err, 1200) : '')) + '</div>';
    }
    return '<div class="iris-run"><div class="iris-kv"><span>' + badge +
      '<span>模式 <b>' + esc(validation.mode === 'zcode' ? '沙箱真实执行' : '静态校验（降级）') + '</b></span>' +
      (validation.exitCode != null ? '<span>退出码 <b>' + esc(validation.exitCode) + '</b></span>' : '') +
      '<span>耗时 <b>' + esc(validation.durationMs || 0) + 'ms</b></span></div>' + io + '</div>';
  }

  /* 卡片 DOM：data = buildResultData 产物（含 validation/history/rounds） */
  function cardHtml(data) {
    var hist = (data.history || []);
    var histTxt = hist.map(function (h) { return '第' + h.round + '轮·' + esc(h.status); }).join(' · ');
    var html = '<div class="iris-card" data-iris-card="' + esc(data.taskId) + '">' +
      '<div class="iris-card__hd"><span>🛠 Iris 报错修复</span>' + (data.severity ? severityBadge(data.severity) : '') +
      '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);font-weight:400;">' + esc(data.taskId) + '</span></div>' +
      '<div class="iris-card__bd">';
    // 段1 错误概览
    html += '<div class="iris-card__sec"><div class="iris-card__ttl">错误概览</div><div class="iris-kv">' +
      (data.errorType ? '<span>类型 <b>' + esc(data.errorType) + '</b></span>' : '') +
      (data.filePath ? '<span>位置 <b>' + esc(data.filePath) + (data.lineNumber ? ':' + esc(data.lineNumber) : '') + '</b></span>' : '') +
      (data.language ? '<span>语言 <b>' + esc(data.language) + '</b></span>' : '') +
      '</div>' + (data.rootCause ? '<div class="iris-cause" style="margin-top:4px;">' + esc(data.rootCause) + '</div>' : '') + '</div>';
    // 段2 修复方案
    if (data.code) {
      html += '<div class="iris-card__sec"><div class="iris-card__ttl">修复方案</div>' +
        (data.explanation ? '<div class="iris-cause">' + esc(data.explanation) + '</div>' : '') +
        codeBlock(data.code, data.language) + '</div>';
    }
    // 段2.5 执行验证
    if (data.validation) {
      html += '<div class="iris-card__sec"><div class="iris-card__ttl">执行验证</div>' + runBlock(data.validation) + '</div>';
    }
    // 轮次历史
    if (hist.length > 1 && histTxt) {
      html += '<div class="iris-hist">轮次：' + histTxt + '（详见 Iris 工作台）</div>';
    }
    html += '</div>';
    // 段3 操作区（任务书阶段4.1：查看完整报告 / 去工作台验证 / 重新分析）
    html += '<div class="iris-acts">' +
      '<button class="iris-btn iris-btn--primary" data-iris-open type="button" title="在 Iris 工作台打开该任务的完整记录（含 OCR 全文/轮次/执行输出）">查看完整报告</button>' +
      '<button class="iris-btn" data-iris-verify type="button" title="打开 Iris 工作台并聚焦「执行验证」，可直接在沙箱复跑修复代码">去工作台验证</button>' +
      (data.code ? '<button class="iris-btn" data-iris-quote type="button">引用到对话</button>' : '') +
      '<button class="iris-btn" data-iris-retry type="button">重新分析</button>' +
      '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);align-self:center;" data-iris-ts>' + esc(nowStr()) + '</span>' +
      '</div>';
    html += '<div class="iris-live" data-iris-live style="display:none;"></div>';
    html += '</div>';
    return html;
  }
  function hljsCard(cardEl) {
    try {
      if (window.hljs && window.hljs.highlightElement) {
        cardEl.querySelectorAll('.iris-code code').forEach(function (c) { window.hljs.highlightElement(c); });
      }
    } catch (e) { /* 高亮失败不影响展示（esc 后明文可读） */ }
  }
  function bindCard(cardEl, data, ctx) {
    var taskId = data.taskId;
    var copyBtn = cardEl.querySelector('[data-iris-copy]');
    if (copyBtn) {
      copyBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var doToast = function () { toast('已复制', '修复代码已复制到剪贴板', 'success', 1600); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(String(data.code || '')).then(doToast, function () { fallbackCopy(data.code, doToast); });
        } else fallbackCopy(data.code, doToast);
      });
    }
    var openBtn = cardEl.querySelector('[data-iris-open]');
    if (openBtn) {
      openBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        window.open('wallpapers/iris/index.html?taskId=' + encodeURIComponent(taskId), '_blank');
      });
    }
    var verifyBtn = cardEl.querySelector('[data-iris-verify]');
    if (verifyBtn) {
      verifyBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        window.open('wallpapers/iris/index.html?taskId=' + encodeURIComponent(taskId) + '&focus=verify', '_blank');
      });
    }
    var quoteBtn = cardEl.querySelector('[data-iris-quote]');
    if (quoteBtn) {
      quoteBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var ta = document.getElementById('chatInputNormal');
        if (!ta) return;
        var brief = '请基于 Iris 的修复结果继续分析：\n任务 ' + taskId +
          '\n根因：' + clip(data.rootCause, 120) +
          '\n修复代码：\n' + clip(data.code, 800) +
          (data.validation ? ('\n验证：' + (data.validation.ok ? '通过' : '未通过') + (data.validation.stderr ? '（stderr：' + clip(data.validation.stderr, 120) + '）' : '')) : '');
        ta.value = brief;
        ta.focus();
        try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) { /* 旧浏览器忽略 */ }
        toast('已引用', 'Iris 结果摘要已填入输入框，可直接发送续聊', 'success', 2200);
      });
    }
    var retryBtn = cardEl.querySelector('[data-iris-retry]');
    if (retryBtn) {
      retryBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        retryBtn.disabled = true;
        retryBtn.textContent = '重试中…';
        var live = cardEl.querySelector('[data-iris-live]');
        // 重试：以最近图片重跑（去重记录刷新）
        var img = lastUserImage();
        var opts = { image: img, runValidation: ctx && ctx.runValidation, source: 'quick', autoContinue: true };
        runQuickFlow(opts, live, function (nd, ctx2) {
          var fresh = document.createElement('div');
          fresh.innerHTML = cardHtml(nd);
          var nc = fresh.firstChild;
          cardEl.parentNode.replaceChild(nc, cardEl);
          hljsCard(nc);
          bindCard(nc, nd, ctx2);
        }, function (e) {
          retryBtn.disabled = false;
          retryBtn.textContent = '重新分析';
          showCardError(cardEl, e);
        });
      });
    }
  }
  function fallbackCopy(text, done) {
    try {
      var ta = document.createElement('textarea');
      ta.value = String(text || '');
      ta.style.cssText = 'position:fixed;left:-9999px;top:0;';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      if (done) done();
    } catch (e) { toast('复制失败', '请手动选择代码复制', 'warning', 2200); }
  }
  function showCardError(cardEl, err) {
    var live = cardEl.querySelector('[data-iris-live]');
    if (!live) return;
    var h = humanize(err);
    live.style.display = '';
    live.innerHTML = '<span style="color:#f87171;">✕ ' + esc(h.text) + '</span> <button class="iris-btn" data-iris-retry2 type="button" style="margin-left:6px;">重试</button>';
    var rb = live.querySelector('[data-iris-retry2]');
    if (rb) rb.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var main = cardEl.querySelector('[data-iris-retry]');
      if (main) main.click(); else location.reload();
    });
  }

  /* 失败态错误卡片（阶段5.2：超时/预算/网络/非报错图 → 卡片内友好提示 + 重试按钮） */
  function errorCardHtml(h, taskId) {
    return '<div class="iris-card" data-iris-card="error">' +
      '<div class="iris-card__hd"><span>🛠 Iris 报错修复</span>' +
      '<span class="iris-badge iris-badge--fatal" style="margin-left:6px;">未完成</span>' +
      (taskId ? '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);font-weight:400;">' + esc(taskId) + '</span>' : '') +
      '</div>' +
      '<div class="iris-card__bd"><div class="iris-cause" style="color:#f87171;">✕ ' + esc(h.text) + '</div></div>' +
      '<div class="iris-acts">' +
      '<button class="iris-btn iris-btn--primary" data-iris-retry type="button">重试</button>' +
      '<button class="iris-btn" data-iris-open-fix type="button">去工作台手动分析</button>' +
      '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);align-self:center;">' + esc(nowStr()) + '</span>' +
      '</div></div>';
  }
  function bindErrorCard(cardEl, err, opts) {
    var h = humanize(err);
    cardEl.setAttribute('data-iris-err-code', h.code);
    var retry = cardEl.querySelector('[data-iris-retry]');
    if (retry) retry.addEventListener('click', function (ev) {
      ev.stopPropagation();
      retry.disabled = true;
      retry.textContent = '重试中…';
      var live = document.createElement('div');
      live.className = 'iris-live';
      live.setAttribute('data-iris-live', '');
      live.style.display = 'block';
      live.textContent = '◐ 正在重试…';
      cardEl.querySelector('.iris-card__bd').appendChild(live);
      runQuickFlow({
        image: (opts && opts.image) || lastUserImage(),
        runValidation: !!(opts && opts.runValidation), source: 'quick', autoContinue: true
      }, live, function (nd, ctx2) {
        var holder = document.createElement('div');
        holder.innerHTML = cardHtml(nd);
        var nc = holder.firstChild;
        cardEl.parentNode.replaceChild(nc, cardEl);
        hljsCard(nc);
        bindCard(nc, nd, ctx2);
      }, function (e) {
        retry.disabled = false;
        retry.textContent = '重试';
        live.innerHTML = '<span style="color:#f87171;">✕ ' + esc(humanize(e).text) + '</span>';
      });
    });
    var wb = cardEl.querySelector('[data-iris-open-fix]');
    if (wb) wb.addEventListener('click', function (ev) {
      ev.stopPropagation();
      window.open('wallpapers/iris/index.html', '_blank');
    });
  }
  /* 工具路径失败 → 进度挂件替换为错误卡片（保留在对话内可见） */
  function finalizeErrorStub(stub, err, opts) {
    ensureStyle();
    var h = humanize(err);
    if (!stub || !stub.el || !stub.el.isConnected) return null;
    var holder = document.createElement('div');
    holder.innerHTML = errorCardHtml(h);
    var cardEl = holder.firstChild;
    stub.el.parentNode.replaceChild(cardEl, stub.el);
    bindErrorCard(cardEl, err, opts);
    return cardEl;
  }

  /* 挂载：当前 AI 气泡（生成中占位即存在）；找不到 → 消息流尾部兜底 */
  function mountCard(data, ctx) {
    ensureStyle();
    var holder = document.createElement('div');
    holder.innerHTML = cardHtml(data);
    var cardEl = holder.firstChild;
    var inner = findActiveInner();
    if (inner) inner.insertAdjacentElement('afterend', cardEl);
    else {
      var box = document.getElementById('chatMessagesNormal');
      if (box) {
        var wrapEl = document.createElement('div');
        wrapEl.className = 'msg-bubble flex justify-start msg-enter';
        wrapEl.setAttribute('data-iris-quick-card', '1');
        wrapEl.appendChild(cardEl);
        box.appendChild(wrapEl);
        if (box.scrollHeight) box.scrollTop = box.scrollHeight;
      }
    }
    hljsCard(cardEl);
    bindCard(cardEl, data, ctx);
    return cardEl;
  }
  function findActiveInner() {
    var box = document.getElementById('chatMessagesNormal');
    if (!box) return null;
    var inners = box.querySelectorAll('.msg-bubble .ai-msg-inner');
    for (var i = inners.length - 1; i >= 0; i--) {
      // 跳过快捷卡片容器（无 msg-ai-body 特征）与已含 Iris 卡片的消息
      var bubble = inners[i].closest('.msg-bubble');
      if (!bubble || bubble.hasAttribute('data-iris-quick-card')) continue;
      if (bubble.querySelector('[data-iris-card]')) continue;
      return inners[i];
    }
    return null;
  }

  /* 实时进度订阅：任务事件 → 卡片实时区（阶段4.1 执行状态/stdout 实时展示） */
  function wireLive(agent, liveEl, taskIdRef) {
    if (!liveEl) return function () {};
    liveEl.style.display = '';
    var handler = function (e) {
      if (!e || (taskIdRef.v && e.taskId !== taskIdRef.v)) return;
      if (e.event === 'task-progress') {
        liveEl.textContent = '◐ [' + nowStr() + '] ' + (e.stage || '') + (e.data && e.data.message ? ' · ' + e.data.message : '') + '（' + (e.progress || 0) + '%）';
      } else if (e.event === 'task-ocr-done') {
        liveEl.textContent = '◐ [' + nowStr() + '] OCR 完成（' + (e.data && e.data.chars ? e.data.chars + ' 字符' : '') + (e.data && e.data.segments > 1 ? ' · ' + e.data.segments + ' 段' : '') + '），诊断中…';
      } else if (e.event === 'task-diagnosis-done') {
        liveEl.textContent = '◐ [' + nowStr() + '] 诊断完成，生成修复…';
      } else if (e.event === 'task-validation-done') {
        liveEl.textContent = (e.data && e.data.ok ? '✓' : '✕') + ' [' + nowStr() + '] 执行验证' + (e.data && e.data.ok ? '通过' : '未通过') + '（' + (e.data && e.data.mode === 'zcode' ? '沙箱' : '静态') + '）';
      }
    };
    agent.on('task-progress', handler);
    agent.on('task-ocr-done', handler);
    agent.on('task-diagnosis-done', handler);
    agent.on('task-validation-done', handler);
    return function () {
      ['task-progress', 'task-ocr-done', 'task-diagnosis-done', 'task-validation-done'].forEach(function (n) {
        try { agent.off(n, handler); } catch (e) { /* 已移除 */ }
      });
    };
  }

  /* ---------------- 快捷/重试统一流（不走模型：直调 IrisAgent） ---------------- */
  function runQuickFlow(opts, liveEl, onDone, onFail) {
    var taskIdRef = { v: '' };
    var unlive = null;
    ensureAgent().then(function (agent) {
      unlive = wireLive(agent, liveEl, taskIdRef);
      var img = opts.image || lastUserImage();
      if (!img) throw new Error('IRIS_NO_IMAGE: 未找到可分析的图片，请先上传报错截图');
      // 防滥用校验：调用方已前置校验（onQuickTrigger）时跳过，避免同图二次校验自拦截
      var pc = opts.prechecked ? { ok: true } : precheck(img.dataUrl);
      if (!pc.ok) throw new Error('IRIS_DEDUP: ' + pc.reason);
      var chain = agent.analyzeImage(img.dataUrl, { enhanceMode: 'auto' });
      // OCR 守卫（快捷路径同样生效）
      var guard = wireOcrGuard(agent);
      return chain.catch(translateCancellation).then(function (res) { try { agent.off('task-ocr-done', guard); } catch (e) { /* 已移除 */ } return res; },
        function (err) { try { agent.off('task-ocr-done', guard); } catch (e) { /* 已移除 */ } throw err; })
        .then(function (res) {
          taskIdRef.v = res.taskId;
          if (opts.runValidation !== false && res.fix && res.fix.code) {
            return agent.runValidation(res.fix.code, { language: res.fix.language || undefined, taskId: res.taskId })
              .then(function (v) { res.validation = v.validation; return res; })
              .catch(function () { return res; }); // 验证失败不影响主结果展示（失败信息进 validation）
          }
          return res;
        });
    }).then(function (res) {
      if (unlive) unlive();
      var lst = (res.history || []).length;
      var qStatus = {};
      if (res.validation && lst >= 1) qStatus[lst] = roundStatusOf(res.validation, res.validation.ok);
      var fixed = withFixedHistory(res, qStatus);
      var data = buildResultData(fixed, { source: 'quick' });
      data.rounds = (fixed.history || []).map(function (h) { return { n: h.round, status: h.status, summary: clip(h.summary, 120) }; });
      data.imageTextHead = clip(res.imageText || '', 1200);
      saveSnapshot(data);
      if (liveEl) { liveEl.style.display = 'none'; }
      onDone(data, opts);
    }).catch(function (err) {
      if (unlive) unlive();
      onFail(err);
    });
  }

  /* ---------------- 工具注册（阶段2.1 标准化定义 + 阶段3.1 意图描述） ---------------- */
  var TOOL_DEF = {
    name: TOOL_NAME,
    label: 'Iris 报错截图修复',
    timeoutMs: TOOL_TIMEOUT_MS,
    description: '分析代码报错截图，识别错误内容、诊断根因、生成修复代码，并可选沙箱验证。' +
      '触发场景：用户上传/粘贴了图片且在问报错怎么修、帮我看看这个错误、修复代码、调试崩溃、' +
      '终端报错截图、编译失败提示、运行时异常（Traceback/Exception/Error 等）。' +
      '多轮上下文：用户此前发过报错截图，本轮说"还是报错/修一下/再看看这个错"且上一轮调用过本工具时，' +
      '可带 taskId 与 last_error 继续迭代。' +
      '排除场景：普通代码问答（无图）、UI 设计图、文档/白板照片、生活照片、非排错诉求——一律不要调用。' +
      '重要：无需提供图片数据，系统自动使用用户最近上传的截图；同一张截图不要重复调用。',
    parameters: {
      type: 'object',
      properties: {
        image_data: { type: 'string', description: '无需填写：系统自动从对话提取用户最近上传的截图（保留参数仅为兼容，传值会被忽略）' },
        run_validation: { type: 'boolean', description: '是否自动执行沙箱验证修复代码（默认 false；用户要求"验证/运行试试"时传 true）' },
        language: { type: 'string', description: '代码语言（python/javascript 等），不填自动识别' },
        taskId: { type: 'string', description: '继续上一次修复时填写（上一次工具结果的 taskId）' },
        last_error: { type: 'string', description: '继续修复时填写：最新一次的报错文本（如执行 stderr）' }
      },
      required: []
    },
    run(args, ctx) {
      args = args || {};
      var img = lastUserImage();
      var opts = {
        image: img,
        runValidation: args.run_validation === true,
        language: args.language || undefined,
        taskId: args.taskId || undefined,
        lastError: args.last_error || undefined,
        source: 'tool',
        autoContinue: true
      };
      // 就绪提示（步骤卡"执行中"阶段即可见）
      var liveCard = mountProgressStub();
      var unlive = null;
      var finished = false;
      return ensureAgent().then(function (agent) {
        unlive = wireLive(agent, liveCard.live, { v: '' });
        // 进度事件里动态捕获 taskId → 更新去重锚点
        return fixFlowWithRounds(opts).then(function (out) {
          if (unlive) unlive();
          finished = true;
          var fixed = withFixedHistory(out.res, out.roundStatus); // 轮次状态修正（P4 显示层）
          var data = buildResultData(fixed, { source: 'tool' });
          data.rounds = (fixed.history || []).map(function (h) { return { n: h.round, status: h.status, summary: clip(h.summary, 120) }; });
          data.imageTextHead = clip(out.res.imageText || '', 1200);
          saveSnapshot(data);
          finalizeProgressStub(liveCard, data, opts);
          return data; // ToolRegistry 回灌：JSON.stringify(data).slice(0,4000)
        }, function (err) {
          if (unlive) unlive();
          finished = true;
          finalizeErrorStub(liveCard, err, opts); // 失败 → 错误卡片（含重试），仍向模型回灌错误
          throw err;
        });
      }, function (err) {
        finished = true;
        finalizeErrorStub(liveCard, err, opts);
        throw err;
      });
    }
  };

  /* 步骤卡阶段轻量挂件：任务运行时在气泡下挂一个"进度条"占位（完成后替换为结果卡片） */
  function mountProgressStub() {
    ensureStyle();
    var stub = { el: null, live: null };
    var inner = findActiveInner();
    var holder = document.createElement('div');
    holder.innerHTML = '<div class="iris-card" data-iris-stub="1"><div class="iris-card__hd">🛠 Iris 报错修复' +
      '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);font-weight:400;">执行中…</span></div>' +
      '<div class="iris-live" data-iris-live style="display:block;">◐ 正在启动 Iris…</div></div>';
    var el = holder.firstChild;
    stub.el = el;
    stub.live = el.querySelector('[data-iris-live]');
    if (inner) inner.insertAdjacentElement('afterend', el);
    else {
      var box = document.getElementById('chatMessagesNormal');
      if (box) {
        var wrapEl = document.createElement('div');
        wrapEl.className = 'msg-bubble flex justify-start';
        wrapEl.setAttribute('data-iris-quick-card', '1');
        wrapEl.appendChild(el);
        box.appendChild(wrapEl);
      }
    }
    return stub;
  }
  function finalizeProgressStub(stub, data, ctx) {
    ensureStyle();
    if (!stub || !stub.el || !stub.el.isConnected) { mountCard(data, ctx); return; }
    var holder = document.createElement('div');
    holder.innerHTML = cardHtml(data);
    var cardEl = holder.firstChild;
    stub.el.parentNode.replaceChild(cardEl, stub.el);
    hljsCard(cardEl);
    bindCard(cardEl, data, ctx);
  }
  function removeProgressStub(stub) {
    if (stub && stub.el && stub.el.isConnected) stub.el.remove();
  }

  /* ---------------- 历史回看恢复（R4：从步骤卡 resultFull / 快照恢复卡片） ---------------- */
  function restoreFromToolStep(stepEl) {
    if (stepEl.getAttribute('data-iris-restored')) return;
    ensureStyle(); // 恢复路径同样需要注入卡片样式（刷新后首次渲染时样式表可能尚未注入）
    stepEl.setAttribute('data-iris-restored', '1');
    var bubble = stepEl.closest('.msg-bubble');
    if (!bubble || bubble.querySelector('[data-iris-card]')) return;
    var details = stepEl.closest('details.tool-step');
    if (!details) return;
    var full = details.querySelector('[data-tool-full]');
    if (!full) return;
    var data = null;
    try { data = JSON.parse(full.textContent || ''); } catch (e) { data = null; }
    if (!data || !data.taskId || data.ok !== true) return;
    var snap = loadSnapshot(data.taskId);
    if (snap && snap.code) data = snap; // 快照优先（含多轮/OCR 头）
    var inner = bubble.querySelector('.ai-msg-inner');
    if (!inner) return;
    var holder = document.createElement('div');
    holder.innerHTML = cardHtml(data);
    var cardEl = holder.firstChild;
    inner.insertAdjacentElement('afterend', cardEl);
    hljsCard(cardEl);
    bindCard(cardEl, data, {});
  }
  function scanHistory() {
    document.querySelectorAll('#chatMessagesNormal details.tool-step[data-status="success"]').forEach(function (d) {
      var sum = d.querySelector('summary');
      if (sum && sum.textContent.indexOf('Iris 报错截图修复') !== -1) restoreFromToolStep(d);
    });
  }
  var scanTimer = null;
  function scheduleScan() {
    if (scanTimer) return;
    scanTimer = setTimeout(function () {
      scanTimer = null;
      try { scanHistory(); } catch (e) { /* 扫描失败不影响主流程 */ }
    }, 300);
  }

  /* ---------------- 输入区快捷提示（任务书阶段3.2） ----------------
     附件 chips 出现且含图片 → 在 chips 尾部追加「用 Iris 修复报错」；
     点击旁路直调 IrisAgent（无需等待模型识别）；可关闭（本次会话记忆）。 */
  var QUICK_DISMISS_KEY = 'iris.quickHintDismissedUntil';
  var quickDismissUntil = 0;
  try { quickDismissUntil = parseInt(localStorage.getItem(QUICK_DISMISS_KEY) || '0', 10) || 0; } catch (e) { /* 隐私态 */ }
  function installQuickHint() {
    var container = document.getElementById('coreInputContainer');
    if (!container) return false;
    var chipsWrap = document.getElementById('attachChipsWrap');
    var hasImg = false;
    try {
      var atts = (typeof AppState !== 'undefined' && AppState.attachments) || [];
      hasImg = atts.length > 0;
    } catch (e) { hasImg = false; }
    var existing = document.getElementById('irisQuickHint');
    if (!hasImg || Date.now() < quickDismissUntil) {
      if (existing) existing.remove();
      return true;
    }
    if (existing) return true;
    var chip = document.createElement('span');
    chip.id = 'irisQuickHint';
    chip.className = 'iris-quick';
    chip.innerHTML = '🛠 用 Iris 修复报错<span class="iris-quick-x" data-iris-quick-x title="关闭提示">✕</span>';
    chip.addEventListener('click', function (e) {
      if (e.target && e.target.hasAttribute && e.target.hasAttribute('data-iris-quick-x')) {
        quickDismissUntil = Date.now() + 12 * 3600 * 1000; // 12h 内不再提示
        try { localStorage.setItem(QUICK_DISMISS_KEY, String(quickDismissUntil)); } catch (e2) { /* 隐私态 */ }
        chip.remove();
        e.stopPropagation();
        return;
      }
      e.stopPropagation();
      onQuickTrigger();
    });
    if (chipsWrap) chipsWrap.appendChild(chip);
    else container.insertBefore(chip, container.firstChild);
    return true;
  }
  function onQuickTrigger() {
    var img = lastUserImage();
    if (!img) { toast('未找到图片', '请先上传报错截图', 'warning', 2400); return; }
    var pc = precheck(img.dataUrl);
    if (!pc.ok) { toast('暂不能分析', pc.reason, 'warning', 3200); return; }
    ensureStyle();
    // 快捷卡片插到消息流尾部（一次性展示；「引用到对话」可带入续聊）
    var box = document.getElementById('chatMessagesNormal');
    var liveEl = null, wrapEl = null;
    if (box) {
      wrapEl = document.createElement('div');
      wrapEl.className = 'msg-bubble flex justify-start';
      wrapEl.setAttribute('data-iris-quick-card', '1');
      wrapEl.innerHTML = '<div class="iris-card"><div class="iris-card__hd">🛠 Iris 报错修复（快捷分析）' +
        '<span style="margin-left:auto;font-size:10px;color:var(--color-text-tertiary,#9aa3b2);font-weight:400;">执行中…</span></div>' +
        '<div class="iris-live" style="display:block;">◐ 正在启动 Iris…（不经过模型识别，直连 Iris 分析）</div></div>';
      box.appendChild(wrapEl);
      liveEl = wrapEl.querySelector('.iris-live');
      box.scrollTop = box.scrollHeight;
    }
    runQuickFlow({ image: img, runValidation: true, source: 'quick', prechecked: true }, liveEl, function (data, ctx) {
      if (wrapEl) {
        var holder = document.createElement('div');
        holder.innerHTML = cardHtml(data);
        var cardEl = holder.firstChild;
        wrapEl.innerHTML = '';
        wrapEl.appendChild(cardEl);
        hljsCard(cardEl);
        bindCard(cardEl, data, ctx);
        box.scrollTop = box.scrollHeight;
      } else mountCard(data, ctx);
      toast('Iris 分析完成', '结果已插入消息流，可点击「引用到对话」续聊', 'success', 3000);
    }, function (err) {
      var h = humanize(err);
      if (wrapEl && liveEl) {
        liveEl.innerHTML = '<span style="color:#f87171;">✕ ' + esc(h.text) + '</span>';
      } else toast('Iris 分析失败', h.text, 'error', 4200);
    });
  }

  /* ---------------- 工作台 → 主对话 草稿注入（阶段4.2 反向通道） ----------------
     工作台「发送到对话」写 localStorage['iris.toChat.draft']，本模块在
     load 与 visibilitychange 时消费：填入输入框（不自动发送）。 */
  var DRAFT_KEY = 'iris.toChat.draft';
  function consumeInboundDraft() {
    var raw = null;
    try { raw = localStorage.getItem(DRAFT_KEY); } catch (e) { return; }
    if (!raw) return;
    var d = null;
    try { d = JSON.parse(raw); } catch (e) { d = null; }
    if (!d || !d.text || !d.ts || Date.now() - d.ts > 5 * 60000) {
      try { localStorage.removeItem(DRAFT_KEY); } catch (e2) { /* 忽略 */ }
      return;
    }
    var ta = document.getElementById('chatInputNormal');
    if (!ta) return;
    if (String(ta.value || '').trim().length) {
      toast('Iris 结果待引用', '输入框已有内容，未覆盖。可清空后再次点击工作台「发送到对话」', 'info', 3600);
      return;
    }
    ta.value = d.text;
    try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) { /* 忽略 */ }
    try { localStorage.removeItem(DRAFT_KEY); } catch (e2) { /* 忽略 */ }
    toast('Iris 结果已就绪', '修复摘要已填入输入框，确认后发送即可', 'success', 3000);
  }

  /* ---------------- 启动（R10：87 分片先于 125 加载 → load 后注册） ---------------- */
  function boot() {
    // 1) 工具注册（ToolRegistry 就绪守卫：轮询最多 10s）
    var tries = 0;
    (function tryRegister() {
      tries++;
      if (typeof ToolRegistry !== 'undefined' && ToolRegistry && typeof ToolRegistry.register === 'function') {
        if (!ToolRegistry.get(TOOL_NAME)) ToolRegistry.register(TOOL_DEF);
      } else if (tries < 100) {
        setTimeout(tryRegister, 100);
        return;
      }
      // 2) 快捷提示安装：附件变化驱动（renderChips 重写 innerHTML → observer 兜底追加）
      setInterval(function () {
        try { installQuickHint(); } catch (e) { /* 忽略 */ }
      }, 800);
      // 3) 历史卡片恢复：消息 DOM 变化驱动
      var box = document.getElementById('chatMessagesNormal');
      if (box && typeof MutationObserver === 'function') {
        new MutationObserver(scheduleScan).observe(box, { childList: true, subtree: true });
      }
      scheduleScan();
      // 4) 工作台草稿消费
      consumeInboundDraft();
      document.addEventListener('visibilitychange', function () {
        if (!document.hidden) consumeInboundDraft();
      });
    })();
  }
  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();
