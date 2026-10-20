/* ============================================================================
 * 91_log_monitor.js — L5.2 主动日志监控的前端侧（2026-09-28）。
 *
 * 零改动包装：不动 30_chat / 骨架 / 86 核心 / 88 逻辑（红线1/5）。
 *   - 后端 server/app/log_monitor.py + monitor_routes.py（api.py 尾部折叠挂载）；
 *   - 对话指令接管：88 包装后轮询到 __crash88 标记再包一层（__mon91），
 *     "开启监控/关闭监控/监控状态" 走 /api/monitor/*，其余消息原样交回 88/正常发送；
 *   - 告警注入：chat 页每 15s 读一次 /api/monitor/status，enabled 才拉
 *     /api/monitor/alerts，新告警渲染成卡片插 #chatMessagesNormal（88 同款宿主
 *     与共存策略），带"立即修复/忽略"两按钮；未处理告警 pending 常驻（刷新重挂），
 *     处理后（ack）不再出现——与后端指纹去重共同满足"重复错误不重复告警"。
 * 默认关闭（红线7）：本分片只做只读轮询 + 用户显式指令才 enable；
 * localStorage.moray_monitor_ui === '0' 可整片静默（裸键范式，moray_test_intent 同族）。
 * 所有动态文本一律 textContent，绝不 innerHTML 拼日志/LLM 输出。
 * ==========================================================================*/
(function () {
  'use strict';
  var ON_RE = /(开启监控|启动监控|打开监控|监控\s*开启|monitor\s*on)/i;
  var OFF_RE = /(关闭监控|停止监控|退出监控|监控\s*关闭|monitor\s*off)/i;
  var STATUS_RE = /^.{0,6}(监控状态|监控\s*状态|monitor\s*status)/i;
  var FIX_RE = /(立即修复|马上修复|修复它|fix now)/i;
  var IGNORE_RE = /^(忽略|跳过|不管|ignore)$/i;
  var busy = false;
  var pollTimer = null;
  var seenAlertIds = {};   // 本次页面会话已渲染过的告警（防重复插卡）

  function uiEnabled() {
    try { return localStorage.getItem('moray_monitor_ui') !== '0'; } catch (e) { return true; }
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
  function msgHost() { return document.getElementById('chatMessagesNormal'); }

  function newCard(titleText) {
    var host = msgHost();
    if (!host) return null;
    var card = el('div', 'mon91-card');
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
    var body = el('div', 'mon91-body');
    body.setAttribute('style', 'white-space:pre-wrap;word-break:break-word;min-width:0;');
    card.appendChild(body);
    host.appendChild(card);
    host.scrollTop = host.scrollHeight;
    return { card: card, dot: dot, body: body };
  }

  function echoUser(raw) {
    var host = msgHost();
    if (!host) return;
    try { if (typeof removeWelcomeView === 'function') removeWelcomeView(); } catch (e) {}
    host.classList.remove('hidden');
    var u = el('div', null, raw);
    u.setAttribute('style',
      'margin:10px 0 4px;padding:8px 14px;border-radius:8px;font-size:13px;' +
      'background:rgba(255,255,255,.06);color:var(--color-text-primary,#e8e8e8);' +
      'word-break:break-word;max-width:92%;');
    host.appendChild(u);
  }

  function clearInput() {
    var inp = document.getElementById('chatInputNormal');
    if (!inp) return;
    inp.value = '';
    try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
  }

  function recentProject() {
    try {
      var arr = JSON.parse(localStorage.getItem('moray_task_projects_recent') || '[]');
      if (arr && arr[0] && arr[0].path) return arr[0].path;
    } catch (e) {}
    return '';
  }

  function postJson(path, payload) {
    return fetch(backendOrigin() + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    }).then(function (r) { return r.json().then(function (j) { return { st: r.status, j: j }; }); });
  }
  function getJson(path) {
    return fetch(backendOrigin() + path).then(function (r) { return r.json(); });
  }

  function statusText(st) {
    if (!st) return '监控当前未开启。';
    return '监控运行中\n· 扫描间隔：' + (st.interval_sec || '-') + 's' +
      '\n· 告警阈值：' + (st.alert_level || '-') +
      '\n· 扫描目录：' + ((st.scan_dirs || []).join('、') || '(未配置)') +
      '\n· 上次扫描：' + (st.last_scan || '-') +
      (st.last_error ? '\n· 最近错误：' + st.last_error : '') +
      '\n· 待处理告警：' + (st.pending || 0) + '｜已见指纹：' + (st.seen_count || 0);
  }

  function cmdEnable() {
    var card = newCard('日志监控 · 开启中…');
    if (!card) { busy = false; return; }
    postJson('/api/monitor/enable', { project_dir: recentProject() })
      .then(function (res) {
        if (!res.j || !res.j.ok) {
          card.dot.setAttribute('style', 'color:#d9534f;font-size:10px;');
          card.body.textContent = '开启失败：' + ((res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st));
          return;
        }
        card.body.textContent = statusText(res.j.data);
      })
      .catch(function (e) {
        card.dot.setAttribute('style', 'color:#d9534f;font-size:10px;');
        card.body.textContent = '无法连接 /api/monitor/enable：' + e + '（该路由随 MoRay 后端重启生效）';
      });
  }

  function cmdDisable() {
    var card = newCard('日志监控 · 关闭中…');
    if (!card) { busy = false; return; }
    postJson('/api/monitor/disable', {})
      .then(function (res) {
        card.body.textContent = (res.j && res.j.ok)
          ? '监控已关闭。后台循环协程已取消，不再扫描日志；告警队列与已见指纹仍留在 work/monitor_state/alerts.json。'
          : ('关闭失败：' + ((res.j && res.j.message) || ('HTTP ' + res.st)));
      })
      .catch(function (e) { card.body.textContent = '无法连接 /api/monitor/disable：' + e; });
  }

  function cmdStatus() {
    var card = newCard('日志监控 · 状态');
    if (!card) { busy = false; return; }
    getJson('/api/monitor/status')
      .then(function (j) {
        if (!j || !j.ok) { card.body.textContent = '读取状态失败。'; return; }
        card.body.textContent = j.data.enabled ? statusText(j.data)
          : '监控当前未开启（红线：默认关闭）。在对话里说"开启监控"即可启动，监控目录默认 logs/（可在 server/.env 的 MONITOR_LOG_DIR 调整）。';
      })
      .catch(function (e) { card.body.textContent = '无法连接 /api/monitor/status：' + e; });
  }

  /* ============ 告警卡 ============ */

  function fixBtnStyle() {
    return 'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;' +
      'border:1px solid var(--color-border,rgba(255,255,255,.16));' +
      'background:rgba(229,115,74,.18);color:inherit;';
  }
  function ghostBtnStyle() {
    return 'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;' +
      'border:1px solid var(--color-border,rgba(255,255,255,.12));' +
      'background:rgba(255,255,255,.06);color:inherit;';
  }

  function renderAlertCard(a) {
    var card = newCard('检测到新错误');
    if (!card) return;
    card.dot.setAttribute('style', 'color:#d9534f;font-size:10px;');
    var body = card.body;
    var head = el('div', null, '错误类型：' + (a.error_type || '未识别') +
      (a.analysis && a.analysis.severity ? '　严重度：' + a.analysis.severity : ''));
    body.appendChild(head);
    var src = el('div', null, '来源：' + (a.file || '-') + '　' + (a.ts || ''));
    src.setAttribute('style', 'opacity:.75;font-size:12px;');
    body.appendChild(src);
    if (a.level_line) {
      var lvl = el('div', null, '错误行：' + a.level_line);
      lvl.setAttribute('style', 'margin-top:4px;');
      body.appendChild(lvl);
    }
    var an = a.analysis;
    if (an && (an.root_cause || an.suggestion)) {
      if (an.root_cause) {
        var rc = el('div', null, '根因分析：' + an.root_cause);
        rc.setAttribute('style', 'margin-top:4px;');
        body.appendChild(rc);
      }
      if (an.suggestion) {
        var sg = el('div', null, '修复建议：' + an.suggestion);
        body.appendChild(sg);
      }
      if (an.experience_hint) {
        var eh = el('div', null, '经验库提示：\n' + an.experience_hint);
        eh.setAttribute('style', 'margin-top:4px;font-size:12px;opacity:.8;');
        body.appendChild(eh);
      }
    } else {
      var na = el('div', null, '根因分析暂不可用' +
        (a.analysis_error ? '（' + a.analysis_error + '）' : '') + '，仍可查看详情或直接修复。');
      na.setAttribute('style', 'margin-top:4px;opacity:.8;');
      body.appendChild(na);
    }
    if (a.stack) {
      var pre = el('pre', null, a.stack);
      pre.setAttribute('style',
        'margin:6px 0 0;padding:8px;border-radius:6px;background:rgba(255,255,255,.05);' +
        'max-height:180px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:11px;min-width:0;');
      body.appendChild(pre);
    }
    var row = el('div');
    row.setAttribute('style', 'display:flex;gap:8px;margin-top:8px;');
    var fixBtn = el('button', null, '立即修复');
    fixBtn.setAttribute('style', fixBtnStyle());
    var igBtn = el('button', null, '忽略');
    igBtn.setAttribute('style', ghostBtnStyle());
    fixBtn.addEventListener('click', function () {
      if (fixBtn.disabled) return;
      fixBtn.disabled = true; igBtn.disabled = true;
      submitFix(a, fixBtn);
    });
    igBtn.addEventListener('click', function () {
      if (igBtn.disabled) return;
      igBtn.disabled = true; fixBtn.disabled = true;
      postJson('/api/monitor/alerts/' + encodeURIComponent(a.id) + '/ack', { action: 'ignored' })
        .then(function () { igBtn.textContent = '已忽略'; })
        .catch(function () { igBtn.disabled = false; fixBtn.disabled = false; });
    });
    row.appendChild(fixBtn); row.appendChild(igBtn);
    body.appendChild(row);
    return card;
  }

  /* "立即修复"=复用既有 /api/tasks/run-error-fix（文本通道），卡内简版轮询（88 同款退避） */
  function submitFix(a, btn) {
    btn.textContent = '提交中…';
    var payload = {
      error_text: (a.level_line || '') + '\n' + (a.stack || '').slice(0, 2500),
      project_dir: a.project_dir || recentProject() || '',
      conversation_id: (function () {
        try {
          return (typeof AppState !== 'undefined' && AppState.activeConv && AppState.activeConv.id) ||
            (window.AppState && window.AppState.activeConv && window.AppState.activeConv.id) || '';
        } catch (e) { return ''; }
      })()
    };
    postJson('/api/tasks/run-error-fix', payload)
      .then(function (res) {
        if (!res.j || !res.j.ok) {
          btn.disabled = false; btn.textContent = '立即修复';
          appendLine(a, '提交失败：' + ((res.j && (res.j.message || res.j.code)) || ('HTTP ' + res.st)));
          return;
        }
        btn.textContent = '修复任务已启动';
        postJson('/api/monitor/alerts/' + encodeURIComponent(a.id) + '/ack', { action: 'fixing' }).catch(function () {});
        pollFixTask(btn, res.j.data.task_id);
      })
      .catch(function (e) {
        btn.disabled = false; btn.textContent = '立即修复';
        appendLine(a, '无法连接 /api/tasks/run-error-fix：' + e);
      });
  }

  function appendLine(alertish, text) {
    var cards = document.querySelectorAll('.mon91-card .mon91-body');
    for (var i = cards.length - 1; i >= 0; i--) {
      if (cards[i].__alertId === (alertish.id || alertish)) {
        cards[i].appendChild(el('div', null, text));
        return;
      }
    }
  }

  function pollFixTask(btn, taskId) {
    var t0 = Date.now(), i = 0;
    var wait = [4000, 4000, 8000, 8000, 15000];
    (function step() {
      if (Date.now() - t0 > 20 * 60 * 1000) { btn.textContent = '等待超时（任务可能仍在后台跑）'; return; }
      getJson('/api/tasks/' + encodeURIComponent(taskId))
        .then(function (j) {
          if (!j || !j.ok) { btn.textContent = '任务查询失败'; return; }
          var d = j.data;
          if (d.status === 'running') { i++; setTimeout(step, wait[Math.min(i, wait.length - 1)]); return; }
          btn.textContent = d.status === 'completed' ? '修复完成 ✓' : '修复失败 ✗（见任务卡/报告）';
        })
        .catch(function () { i++; setTimeout(step, wait[Math.min(i, wait.length - 1)]); });
    })();
  }

  /* ============ 轮询告警（只读；enabled 才拉队列） ============ */
  function pollTick() {
    if (!uiEnabled()) return;
    var host = msgHost();
    if (!host || host.classList.contains('hidden')) return;   // 不在 chat 页：不打扰，告警仍 pending
    getJson('/api/monitor/status')
      .then(function (j) {
        if (!j || !j.ok || !j.data || !j.data.enabled) return;
        return getJson('/api/monitor/alerts').then(function (aj) {
          if (!aj || !aj.ok || !aj.data) return;
          (aj.data.alerts || []).forEach(function (a) {
            if (seenAlertIds[a.id]) return;
            seenAlertIds[a.id] = 1;
            if (!host.querySelector('.mon91-card[data-alert="' + a.id + '"]')) {
              var c = renderAlertCard(a);
              if (c) { c.card.setAttribute('data-alert', a.id); c.body.__alertId = a.id; }
            }
          });
        });
      })
      .catch(function () {});   // 旧后端无此路由：静默（诚实：告警不会出现）
  }

  function startPolling() {
    if (pollTimer || !uiEnabled()) return;
    pollTimer = setInterval(pollTick, 15000);
    setTimeout(pollTick, 3000);   // 首拉提前，刷新前已产生的告警尽快可见
  }

  /* ============ 发送接管（包在 88 的 wrapper 之上） ============ */
  function tryWrap() {
    var orig = window.moraySendOverride;
    if (typeof orig !== 'function' || orig.__mon91) return;
    var wrapped = function () {
      try {
        var inp = document.getElementById('chatInputNormal');
        var raw = ((inp && inp.value) || '').trim();
        if (!busy && raw && raw.charAt(0) !== '/') {
          var atts = (typeof AppState !== 'undefined' && AppState.attachments)
            || (window.AppState && window.AppState.attachments) || [];
          if (!atts.length) {
            if (ON_RE.test(raw)) {
              busy = true; clearInput(); echoUser(raw); cmdEnable(); busy = false; return;
            }
            if (OFF_RE.test(raw)) {
              busy = true; clearInput(); echoUser(raw); cmdDisable(); busy = false; return;
            }
            if (STATUS_RE.test(raw)) {
              busy = true; clearInput(); echoUser(raw); cmdStatus(); busy = false; return;
            }
          }
        }
      } catch (e) {
        busy = false;
        try { console.warn('[mon91] 接管异常，交回原发送链路：', e); } catch (e2) {}
      }
      return orig.apply(this, arguments);
    };
    wrapped.__mon91 = true;
    window.moraySendOverride = wrapped;
  }

  function boot() {
    var n = 0;
    var t = setInterval(function () {
      n++;
      try { tryWrap(); } catch (e) {}
      // 88 必须先于本分片包装（它负责关键词→任务链路）；等 __crash88 出现或直接有宿主
      var ready = window.moraySendOverride && window.moraySendOverride.__crash88;
      if ((ready || n > 240) && window.moraySendOverride && !window.moraySendOverride.__mon91) {
        try { tryWrap(); } catch (e) {}
      }
      if (window.moraySendOverride && window.moraySendOverride.__mon91) { clearInterval(t); startPolling(); }
      else if (n > 240) {
        clearInterval(t);
        try { tryWrap(); } catch (e) {}   // 极端情况：88 不在（开关被关）也要能接管监控指令
        startPolling();
      }
    }, 150);
  }

  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();
