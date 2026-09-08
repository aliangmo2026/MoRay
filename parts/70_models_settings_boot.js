/* ============================================================
   模块：模型管理与监控（任务八）/ 设置页（任务九）/ 启动器
   ============================================================ */

/* ===================== [任务八] 模型管理与监控 ===================== */

/** 模型管理应用 */
const ModelsApp = {
  /** 拉取进度控制 @type {AbortController|null} */
  pullController: null,

  /** 渲染设置页的模型列表（含操作）
   * @returns {Promise<void>} */
  async renderModelList() {
    const wrap = document.getElementById('modelListWrap');
    if (!wrap) return;
    const defaultModel = MoraySettings.get('defaultModel');
    if (!AI.models.length) {
      // [V3 P2.5] 统一空状态
      wrap.innerHTML = `<div class="empty-art" style="padding:20px 8px">
        <div class="empty-icon" style="width:40px;height:40px"><i data-lucide="cpu" class="w-5 h-5 text-text-tertiary"></i></div>
        <div class="empty-title" style="font-size:12px">${AI.backend === 'none' ? '未检测到后端服务' : '还没有可用模型'}</div>
        <div class="empty-hint">${AI.backend === 'none' ? '启动 Ollama 或配置云端 API 后点击刷新' : '点击上方「拉取模型」下载第一个模型'}</div>
      </div>`;
      refreshIcons();
      return;
    }
    wrap.innerHTML = AI.models.map(m => {
      const isDefault = defaultModel === m.name;
      return `<div class="flex items-center justify-between px-3 py-2 rounded-lg bg-surface-panel/60" data-model="${escapeHtml(m.name)}">
        <div class="flex items-center gap-2 min-w-0">
          <span class="beacon-dot"></span>
          <span class="text-xs text-text-primary truncate" title="${escapeHtml(m.name)}">${escapeHtml(m.name)}</span>
        </div>
        <div class="flex items-center gap-3 flex-shrink-0">
          ${m.size ? `<span class="text-[10px] text-text-tertiary">${formatBytes(m.size)}</span>` : ''}
          ${isDefault
            ? '<span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt">默认</span>'
            : `<button class="text-[10px] text-brand-cobalt hover:text-brand-cyan" data-model-op="default">设为默认</button>`}
          <button class="text-[10px] text-text-tertiary hover:text-text-secondary" data-model-op="info">详情</button>
          ${AI.backend === 'ollama' ? `<button class="text-[10px] text-danger hover:opacity-80" data-model-op="delete">删除</button>` : ''}
        </div>
      </div>`;
    }).join('');
    refreshIcons();
    wrap.querySelectorAll('[data-model-op]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const name = btn.closest('[data-model]').dataset.model;
        const op = btn.dataset.modelOp;
        if (op === 'default') {
          await MoraySettings.set('defaultModel', name);
          this.renderModelList();
          // [显示统一] 默认模型变更 → 清除“实际生效模型”覆盖，恢复显示新默认值
          if (typeof resetActiveModel === 'function') resetActiveModel();
          else { updateStatusBar(); if (typeof syncInputModelLabel === 'function') syncInputModelLabel(); }
          showNotification('已设为默认', name, 'success', 1600);
        } else if (op === 'delete') {
          showConfirm('删除模型', `确定从 Ollama 删除模型「${name}」？此操作不可恢复。`, async () => {
            try { await AI.deleteModel(name); await AI.listModels(); await this.renderModelList(); showNotification('已删除', name, 'success'); }
            catch (e) { showNotification('删除失败', e.message, 'error', 3000); }
          }, { danger: true, okText: '删除' });
        } else if (op === 'info') {
          try {
            const info = await AI.showModel(name);
            const d = info.details || {};
            showModal(`
              <div class="space-y-2 text-xs text-text-secondary">
                <div class="flex justify-between"><span>参数量</span><span class="text-text-primary">${escapeHtml(d.parameter_size || '--')}</span></div>
                <div class="flex justify-between"><span>量化</span><span class="text-text-primary">${escapeHtml(d.quantization_level || '--')}</span></div>
                <div class="flex justify-between"><span>家族</span><span class="text-text-primary">${escapeHtml(d.family || '--')}</span></div>
                <div class="pt-2"><div class="form-label">系统提示模板</div><pre class="chunk-item" style="white-space:pre-wrap;max-height:140px;overflow-y:auto">${escapeHtml(info.system || '（无）')}</pre></div>
              </div>`, { title: name, icon: 'cpu', footer: false });
          } catch (e) { showNotification('获取详情失败', e.message, 'error', 2500); }
        }
      });
    });
  },

  /** 打开拉取模型对话框（带进度）
   * @returns {void} */
  openPullDialog() {
    if (AI.backend !== 'ollama') { showNotification('需要 Ollama', '拉取模型功能需要 Ollama 后端', 'warning'); return; }
    const box = showModal(`
      <div class="form-row">
        <label class="form-label">模型名称</label>
        <input type="text" id="pullModelName" class="form-input" placeholder="例如 qwen2.5-coder:7b">
        <div class="form-hint">常用：qwen2.5-coder:7b · llama3.1:8b · deepseek-coder-v2:16b · nomic-embed-text</div>
      </div>
      <div id="pullProgressWrap" style="display:none">
        <div class="progress-thin"><div id="pullProgressBar" style="width:0%"></div></div>
        <div class="text-[10px] text-text-tertiary mt-1 flex justify-between">
          <span id="pullStatusText"></span><span id="pullPercentText"></span>
        </div>
        <!-- [V3 P1.3] 速度与取消 -->
        <div class="text-[10px] text-text-tertiary mt-0.5 flex justify-between">
          <span id="pullSpeedText"></span>
          <button class="text-danger hover:opacity-80" id="pullCancelBtn" style="display:none">取消下载</button>
        </div>
      </div>`,
      { title: '拉取新模型', icon: 'download-cloud',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-pull-cancel>取消</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="pullStartBtn">开始拉取</button>` });
    on(box.querySelector('[data-pull-cancel]'), 'click', () => { if (this.pullController) this.pullController.abort(); closeModal(); });
    on(box.querySelector('#pullStartBtn'), 'click', async () => {
      const name = box.querySelector('#pullModelName').value.trim();
      if (!name) { showNotification('请输入模型名', '', 'warning'); return; }
      const wrapEl = box.querySelector('#pullProgressWrap');
      wrapEl.style.display = '';
      const btn = box.querySelector('#pullStartBtn');
      btn.textContent = '拉取中…';
      btn.disabled = true;
      // [V3 P1.3] 取消支持
      const pullCtrl = new AbortController();
      const cancelBtn = box.querySelector('#pullCancelBtn');
      cancelBtn.style.display = '';
      cancelBtn.onclick = () => { pullCtrl.abort(); };
      try {
        await AI.pullModel(name, (p) => {
          box.querySelector('#pullProgressBar').style.width = p.percent + '%';
          box.querySelector('#pullStatusText').textContent = p.status || '';
          box.querySelector('#pullPercentText').textContent = p.total ? `${formatBytes(p.done)} / ${formatBytes(p.total)}` : '';
          box.querySelector('#pullSpeedText').textContent = p.speed ? (formatBytes(p.speed) + '/s') : '';
        }, pullCtrl.signal);
        await AI.listModels();
        await this.renderModelList();
        closeModal();
        showNotification('拉取完成', name + ' 已可用', 'success');
      } catch (e) {
        btn.textContent = '开始拉取'; btn.disabled = false;
        showNotification('拉取失败', String(e.message || e), 'error', 4000);
      }
    });
    box.querySelector('#pullModelName').focus();
  }
};

/** 更新性能监控 UI（柱状图 + 统计）
 * @returns {void} */
function updatePerfUI() {
  // rAF 优化：合并同一帧内的多次更新
  if (updatePerfUI._scheduled) return;
  updatePerfUI._scheduled = true;
  requestAnimationFrame(() => {
    updatePerfUI._scheduled = false;
    const chart = document.getElementById('monitorChart');
    const live = document.getElementById('monitorLiveText');
    if (!chart) return;
    const data = PerfHistory.slice(-20);
    const colors = ['#5B8CFF', '#9D7BFF', '#3AD6E8', '#3FD68F', '#F2B24C', '#FF5C6C'];
    const modelColor = {};
    let ci = 0;
    data.forEach(d => { if (!(d.model in modelColor)) modelColor[d.model] = colors[ci++ % colors.length]; });
    // 图例
    const legend = document.getElementById('monitorLegend');
    if (legend) {
      legend.innerHTML = Object.keys(modelColor).map(m =>
        `<div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full" style="background:${modelColor[m]}"></span><span class="text-[10px] text-text-secondary">${escapeHtml(shortModelName(m))}</span></div>`).join('');
    }
    // 柱状图
    if (live) live.textContent = data.length ? '实时更新' : '等待请求';
    chart.innerHTML = '';
    const maxTps = Math.max(60, ...data.map(d => d.tokPerSec || 0));
    data.forEach(d => {
      const bar = document.createElement('div');
      bar.className = 'monitor-bar';
      bar.style.height = Math.max(6, Math.round((d.tokPerSec || 4) / maxTps * 100)) + '%';
      bar.style.background = d.ok ? (modelColor[d.model] || '#5B8CFF') : 'var(--color-danger)';
      bar.title = `${shortModelName(d.model)} · ${d.tokPerSec || 0} tok/s · 首字 ${d.firstMs || 0}ms · ${d.ok ? '成功' : '失败'}`;
      chart.appendChild(bar);
    });
    // 统计
    const okList = data.filter(d => d.ok);
    const avgTps = okList.length ? Math.round(okList.reduce((s, d) => s + (d.tokPerSec || 0), 0) / okList.length) : 0;
    const avgFirst = okList.length ? Math.round(okList.reduce((s, d) => s + (d.firstMs || 0), 0) / okList.length) : 0;
    const rate = data.length ? Math.round(okList.length / data.length * 100) : 100;
    const a1 = document.getElementById('statAvgSpeed'); if (a1) a1.textContent = avgTps || '--';
    const a2 = document.getElementById('statAvgLatency'); if (a2) a2.textContent = okList.length ? (avgFirst + 'ms') : '--';
    const a3 = document.getElementById('statSuccessRate'); if (a3) a3.textContent = data.length ? rate + '%' : '--';
  });
}

/** 更新底部状态栏（连接状态 / 模型 / 速度）
 * @returns {void} */
function updateStatusBar() {
  const dot = document.getElementById('statusConnDot');
  const text = document.getElementById('statusConnText');
  if (dot && text) {
    dot.className = 'beacon-dot ' + (AI.health === 'ok' ? 'success' : AI.health === 'checking' ? '' : 'danger');
    text.textContent = AI.health === 'ok' ? (AI.backend === 'ollama' ? 'Ollama 已连接' : 'OpenAI API 已连接')
      : AI.health === 'checking' ? '检测中...' : '后端未连接';
  }
  const modelText = document.getElementById('statusModelText');
  if (modelText) {
    // [显示统一] 状态栏模型名以“实际生效模型”优先（云端回退本地后立即显示本地），否则显示默认模型
    const active = (typeof activeModelName === 'function') ? activeModelName() : '';
    // [v3.15.13] 状态栏与消息头/输入台一致：显示完整模型名（本地含 :tag）
    modelText.textContent = active || MoraySettings.get('defaultModel') || (AI.models[0] && AI.models[0].name) || '--';
  }
}

/** [配置侧防护] OpenAI 兼容模型名校验：DeepSeek 官方仅 deepseek-chat/deepseek-reasoner，
 * 明显不存在的 deepseek-* 名称给出警告（不强制阻止自定义端点）。 */
function warnIfSuspiciousOpenaiModel(model) {
  const v = String(model || '').trim();
  const lower = v.toLowerCase();
  if (lower.indexOf('deepseek-') === 0 && lower !== 'deepseek-chat' && lower !== 'deepseek-reasoner') {
    try {
      showNotification('模型名疑似有误',
        'DeepSeek 官方模型为 deepseek-chat / deepseek-reasoner；当前「' + v + '」不在官方列表。若为自定义兼容端点，可忽略此提示。',
        'warning', 6000);
    } catch (e) { /* 提示失败不影响保存 */ }
  }
}

/** 定时健康检查（60s），并联动状态栏与设置页信标
 * @returns {void} */
function startHealthMonitor() {
  const check = async () => {
    // [阶段4/性能] 页面隐藏时暂停健康探测（后台标签不空跑 Ollama/OpenAI 探测）
    if (document.hidden) return;
    await AI.detectBackend();
    updateStatusBar();
    if (typeof SettingsApp !== 'undefined' && SettingsApp.renderConnStatus) SettingsApp.renderConnStatus();
    if (AI.backend === 'ollama') ModelsApp.renderModelList();
    if (typeof ConnectionGuide !== 'undefined') ConnectionGuide.refresh();
  };
  check();
  setInterval(check, 60000);
}

/* ===================== [任务九] 设置页 ===================== */

/** 设置页应用（重建页面并双向绑定） */
const SettingsApp = {
  /** 重建设置页面
   * @returns {void} */
  build() {
    const page = document.getElementById('page-settings');
    if (!page) return;
    const c = MoraySettings.get();
    page.innerHTML = `
      <header class="h-14 px-5 flex items-center border-b border-line-ghost/50 flex-shrink-0">
        <h1 class="text-base font-semibold text-text-primary">设置</h1>
        <span class="text-[10px] text-text-tertiary ml-3">所有配置自动保存到本地 IndexedDB</span>
      </header>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-2xl space-y-4">

          <!-- Ollama 服务配置 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60" id="card-model">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="cpu" class="w-4 h-4 text-brand-cobalt"></i>Ollama 本地服务
            </h3>
            <div class="space-y-4">
              <div>
                <label class="form-label">服务地址</label>
                <div class="flex gap-2">
                  <input type="text" id="setOllamaURL" class="form-input" value="${escapeHtml(c.ollamaURL)}" placeholder="http://localhost:11434">
                  <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost whitespace-nowrap flex items-center gap-1.5" id="testOllamaBtn">
                    <i data-lucide="plug-zap" class="w-3.5 h-3.5"></i>测试连接
                  </button>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2" id="ollamaConnStatus">
                  <span class="beacon-dot" id="ollamaConnDot"></span>
                  <span class="text-xs text-text-secondary" id="ollamaConnText">未检测</span>
                </div>
                <div class="flex items-center gap-2">
                  <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="refreshModelsBtn">
                    <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>刷新模型
                  </button>
                  <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="pullModelBtn">
                    <i data-lucide="download-cloud" class="w-3.5 h-3.5"></i>拉取模型
                  </button>
                </div>
              </div>
              <div class="space-y-1.5">
                <div class="text-xs text-text-tertiary mb-2">已安装模型</div>
                <div class="space-y-1.5" id="modelListWrap">
                  <!-- [阶段三] 加载骨架屏 -->
                  <div class="skeleton" style="height:36px;border-radius:8px"></div>
                  <div class="skeleton" style="height:36px;border-radius:8px;opacity:0.7"></div>
                  <div class="skeleton" style="height:36px;border-radius:8px;opacity:0.4"></div>
                </div>
              </div>
            </div>
          </div>

          <!-- OpenAI 兼容 API -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="globe" class="w-4 h-4 text-brand-violet"></i>OpenAI 兼容 API
            </h3>
            <div class="space-y-4">
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">启用（Ollama 不可用时自动作为备用后端）</span>
                <div class="toggle-track ${c.openaiEnabled ? 'active' : ''}" id="setOpenaiEnabled"><div class="toggle-thumb"></div></div>
              </div>
              <!-- [V3 P1.2] 服务商预设 -->
              <div>
                <label class="form-label">服务商预设（自动填充地址，只需填 Key）</label>
                <div class="flex gap-2">
                  <select id="providerPresetSel" class="form-select" style="flex:1">
                    <option value="">— 选择服务商 —</option>
                    ${(typeof PROVIDER_PRESETS !== 'undefined' ? PROVIDER_PRESETS : []).map(p => `<option value="${escapeHtml(p.name)}" data-base="${escapeHtml(p.base)}" data-model="${escapeHtml(p.models)}" data-doc="${escapeHtml(p.doc)}">${escapeHtml(p.name)}</option>`).join('')}
                    <option value="custom">自定义</option>
                  </select>
                  <a id="providerDocLink" href="#" target="_blank" rel="noopener" class="btn-ghost px-3 rounded-lg text-[11px] border border-line-ghost flex items-center text-text-tertiary" style="display:none">控制台 ↗</a>
                </div>
              </div>
              <div>
                <label class="form-label">接口地址（baseURL）</label>
                <input type="text" id="setOpenaiBase" class="form-input" value="${escapeHtml(c.openaiBaseURL)}" placeholder="https://api.openai.com/v1">
              </div>
              <div>
                <label class="form-label">API Key（本地混淆存储，非加密）</label>
                <div class="flex gap-2">
                  <input type="password" id="setOpenaiKey" class="form-input" value="${escapeHtml(MoraySettings.getAPIKey())}" placeholder="sk-...">
                  <button class="btn-ghost px-3 rounded-lg text-[11px] border border-line-ghost text-text-tertiary flex-shrink-0" id="toggleKeyBtn" title="显示/隐藏" aria-label="显示或隐藏 API Key"><i data-lucide="eye" class="w-3.5 h-3.5"></i></button>
                </div>
                <div class="form-hint" id="keyMaskHint">${MoraySettings.getAPIKey() ? '已保存：sk-****' + escapeHtml(MoraySettings.getAPIKey().slice(-4)) : ''}</div>
                <!-- [M5.1] 更安全方式说明：混淆 ≠ 加密，推荐走本地后端 .env -->
                <div class="form-hint" style="color:var(--color-text-tertiary)">混淆存储不是加密。更安全方式：把 Key 填入后端 server/.env 并选择「经本地后端代理」，Key 不经过浏览器。</div>
              </div>
              <!-- [M2] 云端通道二选一：本地后端代理（key 存 server/.env）/ 前端直连（现有方式） -->
              <div id="proxyModeWrap" style="display:none">
                <label class="form-label">云端通道（本地后端在线）</label>
                <div class="flex gap-2">
                  <button type="button" class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5 justify-center" id="proxyModeBtn" style="flex:1" title="key 只保存在本地后端 server/.env，不进入浏览器">
                    <i data-lucide="shield" class="w-3.5 h-3.5"></i>经本地后端代理
                  </button>
                  <button type="button" class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5 justify-center" id="directModeBtn" style="flex:1" title="现有方式：key 保存在浏览器本地">
                    <i data-lucide="plug" class="w-3.5 h-3.5"></i>前端直连
                  </button>
                </div>
                <div class="form-hint">代理模式下云端 API Key 保存在本地后端（server/.env），不会进入浏览器；后端离线时自动回退直连。</div>
              </div>
              <div>
                <label class="form-label">默认模型</label>
                <input type="text" id="setOpenaiModel" class="form-input" value="${escapeHtml(c.openaiModel)}" placeholder="留空跟随服务商（示例：deepseek-chat / qwen-max）">
              </div>
              <div class="flex items-center justify-between">
                <button class="text-[10px] text-danger hover:opacity-80" id="clearKeyBtn">清除存储的 API Key</button>
                <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="testOpenaiBtn">
                  <i data-lucide="zap" class="w-3.5 h-3.5"></i>测试连接
                </button>
              </div>
              <!-- [BYOK] 跨域代理帮助 -->
              <details class="rounded-lg bg-surface-panel/60 px-3 py-2">
                <summary class="text-[10px] text-text-secondary cursor-pointer select-none">浏览器报跨域(CORS)怎么办？</summary>
                <div class="text-[10px] text-text-tertiary mt-1.5 leading-relaxed">
                  直连服务商被浏览器跨域拦截时，可部署一个 Cloudflare Worker 透传代理（免费，步骤见 deploy/README.md），
                  然后把"接口地址"按规则替换为代理地址即可，其它设置不变：
                  <code class="block mt-1 break-all">https://你的worker.workers.dev/服务商域名/v1路径</code>
                </div>
                <div class="flex gap-1.5 mt-1.5">
                  <input type="text" id="proxyWorkerHost" class="form-input flex-1" style="font-size:10px;padding:3px 6px" placeholder="你的worker.workers.dev">
                  <select id="proxyWorkerPreset" class="form-select" style="font-size:10px;padding:3px 6px;width:auto">
                    <option value="">选服务商</option>
                    ${(typeof PROVIDER_PRESETS !== 'undefined' ? PROVIDER_PRESETS : []).map(p => `<option value="${escapeHtml(p.name)}" data-base="${escapeHtml(p.base)}">${escapeHtml(p.name)}</option>`).join('')}
                  </select>
                  <button class="btn-ghost px-2 rounded-lg text-[10px] border border-line-ghost text-text-tertiary flex-shrink-0" id="proxyBuildBtn">拼地址并填入</button>
                </div>
              </details>
              <div class="text-[10px] text-text-tertiary leading-relaxed">${escapeHtml(typeof PRIVACY_NOTE !== 'undefined' ? PRIVACY_NOTE : '你的 API Key 仅保存在本浏览器本地，不会上传到 MoRay；直连时仅发往你选择的服务商；若你填写了第三方代理地址，请求会经该代理转发，请使用官方或自建代理。')}</div>
            </div>
          </div>

          <!-- 生成参数 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="sliders" class="w-4 h-4 text-brand-cyan"></i>生成参数
            </h3>
            <!-- [夜间优化4.6] 参数预设 -->
            <div class="flex gap-2 mb-4" id="paramPresets">
              ${typeof PARAM_PRESETS !== 'undefined' ? PARAM_PRESETS.map(p => `<button type="button" class="btn-ghost px-2.5 py-1 rounded-lg text-[11px] border border-line-ghost text-text-tertiary hover:text-text-primary" data-param-preset="${p.name}" title="${p.desc}（temp ${p.temp} / top_p ${p.topP}）">${p.name}</button>`).join('') : ''}
            </div>
            <div class="space-y-4">
              <div>
                <div class="flex justify-between text-xs mb-1.5"><span class="text-text-secondary">Temperature</span><span class="text-text-primary font-mono" id="tempValue">${c.temperature.toFixed(2)}</span></div>
                <input type="range" min="0" max="2" step="0.05" value="${c.temperature}" id="setTemperature" class="w-full h-1.5 bg-surface-floating rounded-full appearance-none cursor-pointer accent-brand-cobalt">
              </div>
              <div>
                <div class="flex justify-between text-xs mb-1.5"><span class="text-text-secondary">Top P</span><span class="text-text-primary font-mono" id="topPValue">${c.topP.toFixed(2)}</span></div>
                <input type="range" min="0" max="1" step="0.05" value="${c.topP}" id="setTopP" class="w-full h-1.5 bg-surface-floating rounded-full appearance-none cursor-pointer accent-brand-cobalt">
              </div>
              <div>
                <div class="flex justify-between text-xs mb-1.5"><span class="text-text-secondary">最大生成 Tokens</span><span class="text-text-primary font-mono" id="maxTokValue">${c.maxTokens}</span></div>
                <input type="range" min="256" max="16384" step="256" value="${c.maxTokens}" id="setMaxTokens" class="w-full h-1.5 bg-surface-floating rounded-full appearance-none cursor-pointer accent-brand-cobalt">
              </div>
              <div>
                <div class="flex justify-between text-xs mb-1.5"><span class="text-text-secondary">上下文长度限制</span><span class="text-text-primary font-mono" id="ctxValue">${c.contextLimit}</span></div>
                <input type="range" min="2048" max="131072" step="2048" value="${c.contextLimit}" id="setContextLimit" class="w-full h-1.5 bg-surface-floating rounded-full appearance-none cursor-pointer accent-brand-cobalt">
              </div>
              <div class="form-row">
                <label class="form-label">全局默认系统提示词</label>
                <textarea id="setSystemPrompt" class="form-textarea" placeholder="所有会话默认使用的系统提示词（可被会话级设置覆盖）">${escapeHtml(c.systemPrompt)}</textarea>
              </div>
              <!-- [体验优化] 默认系统提示（简洁直答）与思考过程展示策略 -->
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">启用默认系统提示（简洁直答）</span>
                <div class="toggle-track ${c.defaultSysPromptEnabled !== false ? 'active' : ''}" id="setDefaultSysPromptToggle"><div class="toggle-thumb"></div></div>
              </div>
              <div class="form-row">
                <label class="form-label">默认系统提示内容（可编辑；会话级提示词优先级更高）</label>
                <textarea id="setDefaultSysPrompt" class="form-textarea" style="max-height:120px" placeholder="内置简洁直答提示词（修改后立即生效）">${escapeHtml(c.defaultSysPrompt || '')}</textarea>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">显示模型思考过程</span>
                <select id="setThinkingDisplay" class="form-select" style="width:auto;font-size:12px;padding:4px 8px">
                  <option value="folded" ${c.thinkingDisplay !== 'hidden' && c.thinkingDisplay !== 'auto' ? 'selected' : ''}>折叠显示（一行）</option>
                  <option value="auto" ${c.thinkingDisplay === 'auto' ? 'selected' : ''}>自动展开</option>
                  <option value="hidden" ${c.thinkingDisplay === 'hidden' ? 'selected' : ''}>完全隐藏</option>
                </select>
              </div>
            </div>
          </div>

          <!-- 界面设置 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60" id="card-ui">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="palette" class="w-4 h-4 text-warning"></i>界面设置
            </h3>
            <div class="space-y-4">
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">主题</span>
                <div class="flex gap-2">
                  <button class="px-3 py-1 rounded-lg text-xs ${c.theme === 'dark' ? 'bg-brand-cobalt/15 text-brand-cobalt border border-brand-cobalt/30' : 'text-text-tertiary border border-line-ghost'}" data-theme-set="dark">深色</button>
                  <button class="px-3 py-1 rounded-lg text-xs ${c.theme === 'light' ? 'bg-brand-cobalt/15 text-brand-cobalt border border-brand-cobalt/30' : 'text-text-tertiary border border-line-ghost'}" data-theme-set="light">浅色</button>
                  <button class="px-3 py-1 rounded-lg text-xs ${c.theme === 'auto' ? 'bg-brand-cobalt/15 text-brand-cobalt border border-brand-cobalt/30' : 'text-text-tertiary border border-line-ghost'}" data-theme-set="auto" title="跟随系统深浅色偏好">跟随系统</button>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">主题强调色</span>
                <div class="theme-color-picker">
                  <div class="theme-color-option ${c.accent === 'cobalt' ? 'active' : ''}" data-accent-set="cobalt" style="background:linear-gradient(135deg,#5B8CFF,#9D7BFF)" title="冷蓝紫"></div>
                  <div class="theme-color-option ${c.accent === 'cyan' ? 'active' : ''}" data-accent-set="cyan" style="background:linear-gradient(135deg,#3AD6E8,#5B8CFF)" title="青蓝"></div>
                  <div class="theme-color-option ${c.accent === 'violet' ? 'active' : ''}" data-accent-set="violet" style="background:linear-gradient(135deg,#9D7BFF,#FF6B9D)" title="紫粉"></div>
                  <div class="theme-color-option ${c.accent === 'green' ? 'active' : ''}" data-accent-set="green" style="background:linear-gradient(135deg,#3FD68F,#3AD6E8)" title="青绿"></div>
                  <div class="theme-color-option ${c.accent === 'orange' ? 'active' : ''}" data-accent-set="orange" style="background:linear-gradient(135deg,#F2B24C,#FF6B6B)" title="暖橙"></div>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">消息气泡样式</span>
                <div class="flex gap-2">
                  <button class="px-3 py-1 rounded-lg text-xs ${c.bubbleStyle !== 'square' ? 'bg-brand-cobalt/15 text-brand-cobalt border border-brand-cobalt/30' : 'text-text-tertiary border border-line-ghost'}" data-bubble-set="round">圆角</button>
                  <button class="px-3 py-1 rounded-lg text-xs ${c.bubbleStyle === 'square' ? 'bg-brand-cobalt/15 text-brand-cobalt border border-brand-cobalt/30' : 'text-text-tertiary border border-line-ghost'}" data-bubble-set="square">直角</button>
                </div>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">正文字号</span>
                <select id="setFontSize" class="form-select" style="width:auto;padding:4px 10px;font-size:12px">
                  <option value="sm" ${c.fontSize === 'sm' ? 'selected' : ''}>小</option>
                  <option value="md" ${c.fontSize === 'md' ? 'selected' : ''}>标准</option>
                  <option value="lg" ${c.fontSize === 'lg' ? 'selected' : ''}>大</option>
                </select>
              </div>
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">减少动效模式</span>
                <div class="toggle-track ${c.reduceMotion ? 'active' : ''}" id="setReduceMotion"><div class="toggle-thumb"></div></div>
              </div>
              <div class="mt-2 pt-4 border-t border-line-ghost/50">
                <div class="flex items-center justify-between mb-3">
                  <span class="text-xs text-text-secondary">桌面壁纸</span>
                  <span class="text-[10px] text-text-tertiary">6 款预设 + 自定义</span>
                </div>
                <div class="wallpaper-picker" id="settingsWallpaperPicker"></div>
              </div>
            </div>
          </div>

          <!-- 对话设置 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60" id="card-chat">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="message-square" class="w-4 h-4 text-brand-cobalt"></i>对话设置
            </h3>
            <div class="space-y-3">
              <label class="flex items-center justify-between cursor-pointer">
                <span class="text-xs text-text-secondary">Enter 发送（关闭后为 Ctrl+Enter 发送）</span>
                <input type="checkbox" id="setSendOnEnter" ${c.sendOnEnter ? 'checked' : ''} class="accent-brand-cobalt w-4 h-4">
              </label>
              <label class="flex items-center justify-between cursor-pointer">
                <span class="text-xs text-text-secondary">自动滚动到最新消息</span>
                <input type="checkbox" id="setAutoScroll" ${c.autoScroll ? 'checked' : ''} class="accent-brand-cobalt w-4 h-4">
              </label>
              <label class="flex items-center justify-between cursor-pointer">
                <span class="text-xs text-text-secondary">流式输出（关闭后等待完整回复）</span>
                <input type="checkbox" id="setStreamOutput" ${c.streamOutput ? 'checked' : ''} class="accent-brand-cobalt w-4 h-4">
              </label>
              <div>
                <label class="form-label">每会话保留消息数上限</label>
                <input type="number" id="setHistoryRetention" class="form-input" value="${c.historyRetention}" min="10" max="1000" step="10">
              </div>
              <!-- [V3 P3.6] AI 自动命名 -->
              <div class="flex items-center justify-between">
                <span class="text-xs text-text-secondary">AI 自动命名会话</span>
                <div class="flex items-center gap-2">
                  <select id="setNamingStyle" class="form-select" style="width:auto;padding:4px 8px;font-size:12px">
                    ${['简洁', '专业', '活泼'].map(s => `<option ${c.namingStyle === s ? 'selected' : ''}>${s}</option>`).join('')}
                  </select>
                  <input type="checkbox" id="setAutoNaming" ${c.autoNaming ? 'checked' : ''} class="accent-brand-cobalt w-4 h-4">
                </div>
              </div>


            </div>
          </div>

          <!-- 数据管理 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60" id="card-data">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="database" class="w-4 h-4 text-brand-cobalt"></i>数据管理
            </h3>
            <div class="space-y-3">
              <div class="flex items-center justify-between text-xs">
                <span class="text-text-secondary">本地存储用量</span>
                <span class="font-mono text-text-primary" id="storageUsageText">统计中...</span>
              </div>
              <!-- [M3] 数据与同步：后端同步开关 / 立即同步 / 上次同步 / 双端计数 -->
              <div class="border-t border-line-ghost/40 pt-3 mt-3">
                <div class="flex items-center justify-between text-xs mb-1">
                  <span class="text-text-secondary">后端同步（SQLite）</span>
                  <button id="syncToggleBtn" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">开启</button>
                </div>
                <div class="text-[10px] text-text-tertiary mb-2" id="syncInfoText">后端离线，纯前端模式（IndexedDB）</div>
                <div class="flex flex-wrap gap-2">
                  <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="syncNowBtn"><i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>立即同步</button>
                </div>
                <div class="text-[10px] text-text-tertiary mt-2 leading-relaxed" id="syncStatsText"></div>
              </div>
              <div class="flex flex-wrap gap-2 pt-2">
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="exportAllBtn"><i data-lucide="download" class="w-3.5 h-3.5"></i>导出全部数据</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="importAllBtn"><i data-lucide="upload" class="w-3.5 h-3.5"></i>导入数据</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="importChatGPTBtn"><i data-lucide="history" class="w-3.5 h-3.5"></i>导入 ChatGPT 对话</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="restoreBackupBtn"><i data-lucide="archive" class="w-3.5 h-3.5"></i>恢复自动备份</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="errorLogBtn"><i data-lucide="bug" class="w-3.5 h-3.5"></i>错误日志</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-danger/30 text-danger flex items-center gap-1.5" id="clearDataBtn"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i>清除数据</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-danger/30 text-danger flex items-center gap-1.5" id="dangerZoneBtn"><i data-lucide="alert-triangle" class="w-3.5 h-3.5"></i>危险操作区</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="clearSamplesBtn"><i data-lucide="eraser" class="w-3.5 h-3.5"></i>清空示例数据</button>
                <button class="btn-ghost px-3 py-2 rounded-lg text-xs border border-line-ghost flex items-center gap-1.5" id="cleanOrphansBtn" title="清理无归属的孤儿消息（串台残留）"><i data-lucide="sparkles" class="w-3.5 h-3.5"></i>清理孤儿消息</button>
              </div>
              <!-- [批次修复 #4] 存储边界显式说明 -->
              <div class="rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2 text-[10px] text-text-tertiary leading-relaxed mt-2">
                <b class="text-text-secondary">存储边界</b>：图片附件<b class="text-warning">仅保存在本机浏览器 IndexedDB</b>，
                清除浏览器数据或更换设备将丢失；文本消息可通过本地后端（SQLite）同步。
                附件不随文本同步、不外传。
              </div>
            </div>
          </div>

          <!-- 快捷键 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="keyboard" class="w-4 h-4 text-brand-violet"></i>快捷键
            </h3>
            <div class="space-y-2 text-xs">
              ${[
                ['⌘/Ctrl + K', '全局命令面板'], ['⌘/Ctrl + 1~6', '切换页面'], ['⌘/Ctrl + F', '搜索会话'],
                ['⌘/Ctrl + L', '清空输入框'], ['Enter / Shift+Enter', '发送 / 换行'], ['↑ / ↓', '输入历史切换'],
                ['/', '呼出快捷指令'], ['ESC', '逐级关闭面板']
              ].map(([k, d]) => `<div class="flex items-center justify-between py-1.5 border-b border-line-ghost/40 last:border-0">
                <span class="text-text-secondary">${d}</span><kbd class="px-2 py-0.5 font-mono text-[10px] text-text-tertiary bg-surface-floating rounded border border-line-ghost">${k}</kbd>
              </div>`).join('')}
            </div>
          </div>

          <!-- 关于 -->
          <div class="glass-card rounded-xl p-5 border border-line-ghost/60" id="card-about">
            <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
              <i data-lucide="info" class="w-4 h-4 text-brand-cyan"></i>关于 MoRay
            </h3>
            <button class="btn-ghost w-full px-3 py-2 rounded-lg text-xs border border-line-ghost mb-3 flex items-center gap-1.5 justify-center" id="replayOnboardingBtn">
              <i data-lucide="sparkles" class="w-3.5 h-3.5 text-brand-cobalt"></i>重新查看新手引导
            </button>
            <div class="space-y-2 text-xs text-text-secondary">
              <div class="flex justify-between"><span>版本</span><span class="font-mono text-text-primary">v${(typeof window.MORAY_VERSION !== 'undefined') ? window.MORAY_VERSION : '1.0.0'}</span></div>
              <div class="flex justify-between"><span>开源协议</span><span class="text-text-primary">MIT License</span></div>
              <div class="flex justify-between"><span>运行模式</span><span class="text-text-primary">本地优先 · 单文件应用</span></div>
              <div class="flex justify-between"><span>数据存储</span><span class="text-text-primary">IndexedDB（moray_db）</span></div>
            </div>
          </div>

        </div>
      </div>`;
    this.bind();
    refreshIcons();
  },

  /** 绑定设置页全部控件
   * @returns {void} */
  bind() {
    const $ = (id) => document.getElementById(id);
    // Ollama
    on($('setOllamaURL'), 'change', async () => {
      await MoraySettings.set('ollamaURL', $('setOllamaURL').value.trim());
      showNotification('已保存', '服务地址已更新', 'success', 1500);
    });
    on($('testOllamaBtn'), 'click', async () => {
      await MoraySettings.set('ollamaURL', $('setOllamaURL').value.trim());
      this.renderConnStatus('checking');
      await AI.detectBackend();
      this.renderConnStatus();
      showNotification(AI.backend === 'ollama' ? '连接成功' : '连接失败', AI.backend === 'ollama' ? `检测到 ${AI.models.length} 个模型` : '无法访问 Ollama 服务', AI.backend === 'ollama' ? 'success' : 'error', 3000);
    });
    on($('refreshModelsBtn'), 'click', async () => {
      await AI.listModels();
      await ModelsApp.renderModelList();
      showNotification('已刷新', `发现 ${AI.models.length} 个模型`, 'info', 1800);
    });
    on($('pullModelBtn'), 'click', () => ModelsApp.openPullDialog());
    // OpenAI
    on($('setOpenaiEnabled'), 'click', async function () {
      this.classList.toggle('active');
      await MoraySettings.set('openaiEnabled', this.classList.contains('active'));
    });
    on($('setOpenaiBase'), 'change', () => MoraySettings.set('openaiBaseURL', $('setOpenaiBase').value.trim()));
    on($('setOpenaiModel'), 'change', () => {
      const v = $('setOpenaiModel').value.trim();
      warnIfSuspiciousOpenaiModel(v);
      MoraySettings.set('openaiModel', v);
    });
    on($('setOpenaiKey'), 'change', () => MoraySettings.set('openaiAPIKey', $('setOpenaiKey').value.trim()));
    on($('clearKeyBtn'), 'click', async () => {
      await MoraySettings.set({ openaiAPIKeyStored: '' });
      $('setOpenaiKey').value = '';
      showNotification('已清除', 'API Key 已从本地删除', 'success', 1600);
    });
    // [M2] 云端通道二选一（本地后端在线时显示；选择持久化到 settings）
    function syncProxyModeUI() {
      const wrap = document.getElementById('proxyModeWrap');
      if (!wrap) return;
      const connected = !!(window.MorayBackend && window.MorayBackend.connected);
      wrap.style.display = connected ? '' : 'none';
      const useProxy = MoraySettings.get('useBackendProxy') === true;
      const pb = document.getElementById('proxyModeBtn');
      const db = document.getElementById('directModeBtn');
      if (pb) pb.classList.toggle('active', useProxy);
      if (db) db.classList.toggle('active', !useProxy);
    }
    on($('proxyModeBtn'), 'click', async () => {
      await MoraySettings.set('useBackendProxy', true);
      syncProxyModeUI();
      showNotification('云端通道', '已切换为本地后端代理：key 保存在 server/.env，不会进入浏览器', 'info', 2800);
    });
    on($('directModeBtn'), 'click', async () => {
      await MoraySettings.set('useBackendProxy', false);
      syncProxyModeUI();
      showNotification('云端通道', '已切换为前端直连（现有方式）', 'info', 2000);
    });
    window.addEventListener('moray-backend-probed', syncProxyModeUI);
    syncProxyModeUI();
    // [M3] 数据与同步：开关 / 立即同步 / 上次同步 / 双端计数
    function syncSettingsUI() {
      const toggle = $('syncToggleBtn');
      const info = $('syncInfoText');
      const stats = $('syncStatsText');
      if (!toggle || !info) return;
      const enabled = MoraySettings.get('useBackendSync') !== false;
      const connected = !!(window.MorayBackend && window.MorayBackend.connected);
      toggle.textContent = enabled ? '开启' : '关闭';
      toggle.classList.toggle('active', enabled);
      if (!connected) {
        info.textContent = '后端离线，纯前端模式（IndexedDB）；启动后端后可开启同步';
      } else if (!enabled) {
        info.textContent = '同步已关闭：数据只存本地 IndexedDB';
      } else {
        info.textContent = '同步已开启：在线时双写后端 SQLite，离线自动回退本地';
      }
      // 双端计数
      if (stats && typeof BackendStore !== 'undefined') {
        const p = [];
        p.push('上次同步：' + BackendStore.lastSyncTimeText());
        DB.listConversations().then(local => {
          p.push('本地(IndexedDB)：' + local.length + ' 条会话');
          if (connected && BackendStore.base()) {
            fetch(BackendStore.base() + '/api/health').then(r => r.json()).then(h => {
              const c = (h && h.counts) || {};
              p.push('后端(SQLite)：' + c.conversations + ' 条会话 / ' + c.messages + ' 条消息');
              stats.textContent = p.join(' · ');
            }).catch(() => { stats.textContent = p.join(' · '); });
          } else {
            stats.textContent = p.join(' · ');
          }
        }).catch(() => { stats.textContent = p.join(' · '); });
      }
    }
    on($('syncToggleBtn'), 'click', async () => {
      const next = MoraySettings.get('useBackendSync') === false;
      await MoraySettings.set('useBackendSync', next);
      syncSettingsUI();
      showNotification('后端同步', next ? '已开启：在线双写后端 SQLite' : '已关闭：数据只存本地', 'info', 2000);
    });
    on($('syncNowBtn'), 'click', async () => {
      if (typeof BackendStore === 'undefined') return;
      if (!(window.MorayBackend && window.MorayBackend.connected)) {
        showNotification('后端离线', '请先启动本地后端再同步', 'warning', 2500);
        return;
      }
      const btn = $('syncNowBtn');
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>同步中…';
      refreshIcons();
      try {
        await BackendStore.pushAll((msg) => {
          if (msg && msg !== 'done') { btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>' + msg; refreshIcons(); }
        });
        // [M3修复] 同步完成后重载内存会话列表（renderSessionList 只渲染内存，必须从 DB 重新读入）
        if (typeof reloadSessionsFromDB === 'function') await reloadSessionsFromDB();
        else { if (typeof renderSessionList === 'function') renderSessionList(); }
        syncSettingsUI();
        showNotification('同步完成', '本地与后端数据已双向合并（较新者胜）', 'success', 2500);
      } catch (e) {
        showNotification('同步失败', String(e && e.message || e).slice(0, 60), 'error', 3000);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>立即同步';
        refreshIcons();
      }
    });
    window.addEventListener('moray-backend-probed', syncSettingsUI);
    syncSettingsUI();
    // [V3 P1.2] 服务商预设填充
    on($('providerPresetSel'), 'change', function () {
      const opt = this.options[this.selectedIndex];
      const docLink = $('providerDocLink');
      if (!opt.dataset.base) { docLink.style.display = 'none'; return; }
      $('setOpenaiBase').value = opt.dataset.base;
      if (opt.dataset.model) $('setOpenaiModel').value = opt.dataset.model;
      docLink.href = 'https://' + opt.dataset.doc;
      docLink.textContent = opt.dataset.doc + ' ↗';
      docLink.style.display = '';
      showNotification('已填充 ' + opt.value, '填写 API Key 后点击「测试连接」', 'info', 2500);
    });
    // [V3 P1.2] Key 显示/隐藏 + trim
    on($('toggleKeyBtn'), 'click', () => {
      const inp = $('setOpenaiKey');
      inp.type = inp.type === 'password' ? 'text' : 'password';
      $('toggleKeyBtn').innerHTML = `<i data-lucide="${inp.type === 'password' ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>`;
      refreshIcons();
    });
    on($('setOpenaiKey'), 'blur', function () {
      const v = this.value.trim();
      if (v !== this.value) { this.value = v; }
      const hint = $('keyMaskHint');
      if (hint) hint.textContent = v ? ('已保存：' + v.slice(0, 3) + '****' + v.slice(-4)) : '';
    });
    // [V3 P1.2] 真实连接测试（1 token 最小请求 + 具体诊断）
    on($('testOpenaiBtn'), 'click', async () => {
      const btn = $('testOpenaiBtn');
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>测试中…';
      refreshIcons();
      warnIfSuspiciousOpenaiModel($('setOpenaiModel').value);
      await MoraySettings.set({
        openaiBaseURL: $('setOpenaiBase').value.trim(),
        openaiModel: $('setOpenaiModel').value.trim(),
        openaiEnabled: true,
        openaiAPIKey: $('setOpenaiKey').value.trim()
      });
      const r = await testCloudConnectionDetailed();
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="zap" class="w-3.5 h-3.5"></i>测试连接';
      refreshIcons();
      const resultHtml = r.ok
        ? `<span class="text-success">✓ 连接成功</span> · 延迟 ${r.latency}ms · 模型 ${escapeHtml(r.model || '')}`
        : `<span class="text-danger">✗ ${escapeHtml(r.error)}</span><br><span class="text-text-tertiary">建议：${escapeHtml(r.advice || '')}</span>`;
      let line = document.getElementById('cloudTestResult');
      if (!line) {
        line = document.createElement('div');
        line.id = 'cloudTestResult';
        line.className = 'text-[11px] mt-2 leading-relaxed';
        $('testOpenaiBtn').closest('.flex').parentElement.appendChild(line);
      }
      line.innerHTML = resultHtml;
      showNotification(r.ok ? '连接成功' : '连接失败', r.ok ? `延迟 ${r.latency}ms` : r.error, r.ok ? 'success' : 'error', 3000);
    });
    // [BYOK] 跨域代理地址一键拼接：Worker 域名 + 服务商 → 完整 baseURL 填入
    on($('proxyBuildBtn'), 'click', () => {
      const host = ($('proxyWorkerHost') ? $('proxyWorkerHost').value : '').trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');
      const sel = $('proxyWorkerPreset');
      const opt = sel ? sel.options[sel.selectedIndex] : null;
      if (!host) { showNotification('请先填写 Worker 域名', '形如 xxx.workers.dev（部署步骤见 deploy/README.md）', 'warning', 3000); return; }
      if (!opt || !opt.dataset.base) { showNotification('请选择服务商', '用于拼接其官方接口路径', 'warning', 2500); return; }
      const inner = opt.dataset.base.replace(/^https?:\/\//, '');
      const proxyBase = 'https://' + host + '/' + inner;
      $('setOpenaiBase').value = proxyBase;
      showNotification('已拼接代理地址并填入', '粘贴 Key 后点击「测试连接」', 'success', 2500);
    });
    // [V3 P5] 数据安全入口
    on($('restoreBackupBtn'), 'click', () => AutoBackup.openRestore());
    // [P0 治本] 数据健康检查：清理孤儿消息（二次确认，只删无主数据）
    on($('cleanOrphansBtn'), 'click', async () => {
      let info = null;
      try { info = await findOrphanMessages(); } catch (e) { showNotification('检查失败', String(e.message || e), 'error', 2500); return; }
      if (!info.orphans.length) { showNotification('数据健康', '未发现孤儿消息，数据正常', 'success', 2200); return; }
      showConfirm('清理孤儿消息', `发现 ${info.orphans.length} 条无归属消息（不属于任何会话或缺少会话ID，通常为早期串台残留）。将永久删除，正常会话不受影响。确定清理？`, async () => {
        const n = await cleanOrphanMessages();
        showNotification('已清理', `删除了 ${n} 条孤儿消息`, 'success', 2500);
      }, { danger: true, okText: '清理' });
    });
    on($('errorLogBtn'), 'click', () => ErrorLog.open());
    on($('dangerZoneBtn'), 'click', () => AutoBackup.openDangerZone());
    // [V3 P3.6] 自动命名设置
    on($('setAutoNaming'), 'change', () => MoraySettings.set('autoNaming', $('setAutoNaming').checked));
    on($('setNamingStyle'), 'change', () => MoraySettings.set('namingStyle', $('setNamingStyle').value));
    // 生成参数
    const bindRange = (id, key, valId, fmt) => {
      const el = $(id);
      el.addEventListener('input', () => { $(valId).textContent = fmt ? fmt(el.value) : el.value; });
      el.addEventListener('change', async () => { await MoraySettings.set(key, parseFloat(el.value)); });
    };
    // [夜间优化4.6] 参数预设一键应用
    document.querySelectorAll('[data-param-preset]').forEach(b => {
      b.addEventListener('click', () => {
        const preset = (typeof PARAM_PRESETS !== 'undefined') && PARAM_PRESETS.find(x => x.name === b.dataset.paramPreset);
        if (preset && typeof applyParamPreset === 'function') applyParamPreset(preset);
      });
    });
    bindRange('setTemperature', 'temperature', 'tempValue', v => parseFloat(v).toFixed(2));
    bindRange('setTopP', 'topP', 'topPValue', v => parseFloat(v).toFixed(2));
    bindRange('setMaxTokens', 'maxTokens', 'maxTokValue');
    bindRange('setContextLimit', 'contextLimit', 'ctxValue');
    on($('setSystemPrompt'), 'change', () => MoraySettings.set('systemPrompt', $('setSystemPrompt').value.trim()));
    // [体验优化] 默认系统提示（简洁直答）开关与内容编辑
    on($('setDefaultSysPromptToggle'), 'click', async function () {
      this.classList.toggle('active');
      await MoraySettings.set('defaultSysPromptEnabled', this.classList.contains('active'));
    });
    on($('setDefaultSysPrompt'), 'change', () => MoraySettings.set('defaultSysPrompt', $('setDefaultSysPrompt').value.trim()));
    // [体验优化] 思考过程展示策略三态
    on($('setThinkingDisplay'), 'change', () => MoraySettings.set('thinkingDisplay', $('setThinkingDisplay').value));
    // 界面
    document.querySelectorAll('[data-theme-set]').forEach(b => b.addEventListener('click', async () => {
      await MoraySettings.set('theme', b.dataset.themeSet);
      this.build();
    }));
    document.querySelectorAll('[data-accent-set]').forEach(b => b.addEventListener('click', async () => {
      await MoraySettings.set('accent', b.dataset.accentSet);
      showNotification('强调色已切换', b.title, 'success', 1200);
    }));
    document.querySelectorAll('[data-bubble-set]').forEach(b => b.addEventListener('click', async () => {
      await MoraySettings.set('bubbleStyle', b.dataset.bubbleSet);
      this.build();
    }));
    on($('setFontSize'), 'change', () => MoraySettings.set('fontSize', $('setFontSize').value));
    on($('setReduceMotion'), 'click', async function () {
      this.classList.toggle('active');
      await MoraySettings.set('reduceMotion', this.classList.contains('active'));
    });
    this.renderWallpaperPicker();
    // 对话
    on($('setSendOnEnter'), 'change', () => MoraySettings.set('sendOnEnter', $('setSendOnEnter').checked));
    on($('setAutoScroll'), 'change', () => MoraySettings.set('autoScroll', $('setAutoScroll').checked));
    on($('setStreamOutput'), 'change', () => MoraySettings.set('streamOutput', $('setStreamOutput').checked));
    on($('setHistoryRetention'), 'change', () => MoraySettings.set('historyRetention', parseInt($('setHistoryRetention').value, 10) || 100));
    // 数据管理
    on($('exportAllBtn'), 'click', async () => {
      showNotification('正在导出', '正在读取全部数据...', 'info', 1500);
      const data = await DB.exportAll();
      downloadJson('moray_backup_' + new Date().toISOString().slice(0, 10) + '.json', data);
      showNotification('导出成功', '备份文件已下载', 'success');
    });
    on($('importAllBtn'), 'click', () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.json';
      inp.onchange = async () => {
        try {
          // [阶段三] 统一走导入流程（自动识别普通/加密备份）
          await encryptedImportFlow(inp.files[0]);
        } catch (e) { showNotification('导入失败', e.message, 'error', 3500); }
      };
      inp.click();
    });
    // [V2 优化7] ChatGPT 对话导入
    on($('importChatGPTBtn'), 'click', () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.json';
      inp.onchange = () => importChatGPTConversations(inp.files[0]);
      inp.click();
    });
    on($('clearDataBtn'), 'click', () => {
      const box = showModal(`
        <div class="space-y-2">
          <p class="text-xs text-text-tertiary mb-2">按类型清除本地数据，操作不可恢复：</p>
          ${[['conversations', '会话与消息'], ['prompts', '提示词'], ['snippets', '代码片段'], ['documents', '文档与向量'], ['workflows', '工作流']].map(([s, n]) => `
            <label class="flex items-center gap-2 text-sm text-text-secondary cursor-pointer py-1.5">
              <input type="checkbox" data-clear-store="${s}" class="accent-danger w-3.5 h-3.5">${n}
            </label>`).join('')}
        </div>`, { title: '清除数据', icon: 'trash-2',
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-clr-cancel>取消</button>
                 <button class="px-4 py-2 rounded-lg text-sm bg-danger text-white" id="clrOk">清除所选</button>` });
      on(box.querySelector('[data-clr-cancel]'), 'click', closeModal);
      on(box.querySelector('#clrOk'), 'click', async () => {
        const stores = Array.from(box.querySelectorAll('[data-clear-store]:checked')).map(el => el.dataset.clearStore);
        if (!stores.length) { showNotification('未选择', '请先勾选要清除的数据类型', 'warning'); return; }
        closeModal();
        await DB.clearStores(stores);
        showNotification('已清除', '所选数据已清空，刷新页面后生效', 'success', 3000);
      });
    });
    // [V3 P2] 重新查看新手引导
    const replayBtn = document.getElementById('replayOnboardingBtn');
    if (replayBtn) replayBtn.addEventListener('click', () => Onboarding.openV2());
    // 存储用量
    this.renderStorageUsage();
    this.renderConnStatus();
  },

  /** 渲染壁纸选择器（设置页内）
   * @returns {void} */
  renderWallpaperPicker() {
    const picker = document.getElementById('settingsWallpaperPicker');
    if (!picker) return;
    const saved = (() => { try { return localStorage.getItem('moray_wallpaper') || 'anime-starry'; } catch (e) { return 'anime-starry'; } })();
    const walls = [['aurora', '极光', 'wallpaper-aurora'], ['deep-space', '深空', 'wallpaper-deep-space'], ['gradient', '渐变', 'wallpaper-gradient'], ['mountain', '山脉', 'wallpaper-mountain'], ['cyber', '赛博', 'wallpaper-cyber'], ['pure', '纯黑', 'wallpaper-pure'],
      ['anime-starry', '星夜', 'wallpaper-anime-starry'], ['anime-aurora', '极光动漫', 'wallpaper-anime-aurora'], ['anime-window', '窗边', 'wallpaper-anime-window']];
    picker.innerHTML = walls.map(([k, n, cls]) => `
      <div class="wallpaper-option ${cls} ${saved === k ? 'active' : ''}" data-wallpaper="${k}" onclick="switchWallpaper('${k}')"><span class="wallpaper-option-label">${n}</span></div>`).join('') + `
      <div class="wallpaper-option custom-upload" onclick="document.getElementById('customWallpaperInput').click()">
        <i data-lucide="image-plus" class="w-5 h-5"></i><span>自定义</span>
      </div>
      <input type="file" id="customWallpaperInput" accept="image/*" style="display:none" onchange="handleCustomWallpaper(this)">`;
    refreshIcons();
  },

  /** 渲染连接状态信标
   * @param {string} [force] - 强制状态
   * @returns {void} */
  renderConnStatus(force) {
    const dot = document.getElementById('ollamaConnDot');
    const text = document.getElementById('ollamaConnText');
    if (!dot || !text) return;
    const state = force || AI.health;
    dot.className = 'beacon-dot ' + (state === 'ok' ? 'success' : state === 'checking' ? '' : 'danger');
    text.textContent = state === 'ok' ? `已连接（${AI.models.length} 个模型 · ${AI.backend}）` : state === 'checking' ? '检测中...' : '未连接';
  },

  /** 渲染存储用量统计
   * @returns {Promise<void>} */
  async renderStorageUsage() {
    const el = document.getElementById('storageUsageText');
    if (!el) return;
    try {
      if (navigator.storage && navigator.storage.estimate) {
        const est = await navigator.storage.estimate();
        el.textContent = `${formatBytes(est.usage || 0)} / ${formatBytes(est.quota || 0)}`;
      } else {
        const all = await DB.exportAll();
        el.textContent = formatBytes(JSON.stringify(all).length);
      }
    } catch (e) { el.textContent = '不可用'; }
  }
};

/* ===================== 命令面板扩展 ===================== */

/** 扩展命令面板条目（追加到现有列表）
 * @returns {void} */
function extendCommandPalette() {
  const list = document.getElementById('commandList');
  if (!list || list.querySelector('[data-action="goto-automation"]')) return;
  const extra = document.createElement('div');
  extra.innerHTML = `
    <div class="command-group-title">更多</div>
    <div class="command-item" data-action="goto-automation">
      <div class="command-item-icon"><i data-lucide="workflow" class="w-4 h-4"></i></div>
      <div class="command-item-text"><div class="command-item-name">自动化工作流</div><div class="command-item-desc">运行和管理 AI 工作流</div></div>
      <div class="command-item-shortcut"><kbd>⌘</kbd><kbd>6</kbd></div>
    </div>
    <div class="command-item" data-action="export-data">
      <div class="command-item-icon"><i data-lucide="download" class="w-4 h-4"></i></div>
      <div class="command-item-text"><div class="command-item-name">导出全部数据</div><div class="command-item-desc">备份会话/提示词/片段/文档到 JSON</div></div>
    </div>
    <div class="command-item" data-action="toggle-theme">
      <div class="command-item-icon"><i data-lucide="sun-moon" class="w-4 h-4"></i></div>
      <div class="command-item-text"><div class="command-item-name">切换深浅主题</div><div class="command-item-desc">在深色与浅色主题间切换</div></div>
    </div>`;
  list.appendChild(extra);
  refreshIcons();
}

/* ===================== 启动器与种子数据 ===================== */

/** [修复] 一次性清理 3 条演示会话（精确标题全等匹配，绝不误删用户自建会话；仅执行一次）
 * 老用户首次升级时清理，新用户无此数据自然跳过。
 * @returns {Promise<void>} */
async function cleanupDemoConversations() {
  try {
    if (localStorage.getItem('moray_cleanup_demo_conv_v1')) return;
    const DEMO_TITLES = ['LRU 缓存实现对比', 'FastAPI 流式接口', 'Transformer 注意力机制'];
    const all = await DB.listConversations();
    for (const c of all) {
      if (DEMO_TITLES.includes(c.title)) {
        const msgs = await DB.listMessagesByConversation(c.id);
        for (const m of msgs) await DB.deleteMessage(m.id).catch(() => {});
        await DB.deleteConversation(c.id).catch(() => {});
        console.log('[MoRay] 已清理演示会话:', c.id, c.title);
      }
    }
    localStorage.setItem('moray_cleanup_demo_conv_v1', 'done');
  } catch (e) { console.warn('[MoRay] demo conversation cleanup failed:', e); }
}

/** 首次运行种子数据（仅提示词/片段/工作流模板三类库种子；演示会话已移除）
 * [修复] 独立幂等判据（方案 a）：以提示词库是否已有数据判断"库类种子已播种"，
 * 不再依赖会话数——否则新用户会话恒为 0 会导致每次启动重复播种。
 * 用户手动清空提示词库后允许重新播种一次（可接受）。
 * @returns {Promise<void>} */
async function seedDataIfEmpty() {
  const seeded = await DB.listPrompts();
  if (seeded.length) return;
  // 提示词种子
  const prompts = [
    ['Python 代码优化专家', '你是一个资深Python工程师，请分析以下代码的性能瓶颈，给出优化方案，要求时间复杂度从O(n²)降低到O(n)，并解释优化原理：\n\n{{代码}}', '代码生成', ['python', '优化'], 128],
    ['技术文档解析器', '请阅读以下技术文档，提取核心要点，按「功能概述/API接口/使用示例/注意事项」四个维度整理成结构化笔记：\n\n{{文档内容}}', '文档', ['文档', '解析'], 86],
    ['Bug 排查助手', '我遇到了以下错误，请帮我分析可能的原因，按「错误解读/可能原因/排查步骤/修复方案」的格式给出详细解答：\n\n{{错误信息}}', '其他', ['调试'], 64],
    ['项目架构顾问', '我想做一个{{项目描述}}项目，请帮我设计技术架构，包括技术选型、模块划分、数据流转、部署方案，给出优缺点分析和替代方案。', '其他', ['架构'], 42],
    ['代码翻译器', '请将以下代码从{{源语言}}翻译成{{目标语言}}，保持相同的功能和逻辑，使用现代语法，添加必要的类型注释：\n\n{{代码}}', '代码生成', ['翻译'], 38],
    ['README 生成器', '根据以下项目代码，生成一份专业的README.md，包含项目简介、功能特性、安装步骤、使用方法、API文档、许可证：\n\n{{代码}}', '文档', ['README'], 29]
  ];
  for (const [title, content, category, tags, useCount] of prompts) {
    await DB.createPrompt({ title, content, category, tags, useCount, isFavorite: useCount > 50 });
  }

  // 片段种子
  const snippets = [
    ['防抖函数 debounce', 'function debounce(fn, delay = 300) {\n  let timer = null;\n  return function (...args) {\n    if (timer) clearTimeout(timer);\n    timer = setTimeout(() => {\n      fn.apply(this, args);\n    }, delay);\n  };\n}', 'javascript', ['工具函数', '性能优化']],
    ['深拷贝 deepClone', 'function deepClone(obj, hash = new WeakMap()) {\n  if (obj === null || typeof obj !== "object") return obj;\n  if (hash.has(obj)) return hash.get(obj);\n  const clone = new obj.constructor();\n  hash.set(obj, clone);\n  for (const key in obj) {\n    if (obj.hasOwnProperty(key)) {\n      clone[key] = deepClone(obj[key], hash);\n    }\n  }\n  return clone;\n}', 'javascript', ['工具函数', '递归']],
    ['单例模式装饰器', 'def singleton(cls):\n    instances = {}\n    def get_instance(*args, **kwargs):\n        if cls not in instances:\n            instances[cls] = cls(*args, **kwargs)\n        return instances[cls]\n    return get_instance', 'python', ['设计模式', '装饰器']],
    ['Tailwind 渐变文字', '.gradient-text {\n  background: linear-gradient(135deg, #5B8CFF, #9D7BFF, #3AD6E8);\n  -webkit-background-clip: text;\n  -webkit-text-fill-color: transparent;\n  background-clip: text;\n}', 'css', ['样式']]
  ];
  for (const [title, code, language, tags] of snippets) {
    await DB.createSnippet({ title, code, language, tags, isFavorite: title.includes('防抖') || title.includes('单例') });
  }

  // 工作流模板种子
  for (const tpl of builtinWorkflowTemplates()) {
    await DB.createWorkflow({
      name: tpl.name, description: tpl.description, icon: tpl.icon, accent: tpl.accent,
      nodes: tpl.nodes, isTemplate: true
    });
  }
  console.log('[MoRay] Seed data created');
}

/** 应用启动主流程
 * @returns {Promise<void>} */
/** [兼容迁移] localStorage 旧键 aether_* -> moray_*
 * @returns {void} */
function migrateLegacyStorage() {
  try {
    const legacyKeys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('aether_')) legacyKeys.push(k);
    }
    legacyKeys.forEach(k => {
      const nk = 'moray_' + k.slice(7);
      if (localStorage.getItem(nk) === null) localStorage.setItem(nk, localStorage.getItem(k));
      localStorage.removeItem(k);
    });
    if (legacyKeys.length) console.log('[MoRay] 已迁移存储键:', legacyKeys.join(', '));
  } catch (e) { console.warn('[MoRay] storage migration failed:', e); }
}

async function bootApp() {
  try {
    // [健壮 P0-4] 启动链韧性：每步独立 try-catch，任一模块失败只记录到 ErrorLog，不影响其余绑定
    const runStep = async (name, fn) => {
      try { await fn(); }
      catch (e) {
        console.error('[MoRay] boot step failed:', name, e);
        if (typeof ErrorLog !== 'undefined') ErrorLog.push('boot:' + name, String((e && e.message) || e));
        try { showNotification('模块加载失败（已跳过）', name + '：' + String((e && e.message) || e).slice(0, 50), 'error', 3000); } catch (e2) { /* 忽略 */ }
      }
    };

    // 0. 旧版数据迁移（localStorage 键名 + IndexedDB 库名），先于一切读取
    await runStep('migrate', async () => {
      migrateLegacyStorage();
      await migrateLegacyDB();
    });

    // 1. 设置初始化（先于其他，应用主题）
    await runStep('settings', async () => {
      await MoraySettings.init();
      window.moraySettingsV2 = true; // 第一阶段设置持久化让位
    });

    // 2. 打开数据库 + 首次种子 + 演示会话一次性清理
    await runStep('db', async () => {
      await DB.open();
      await seedDataIfEmpty();
      await cleanupDemoConversations();
      // [缓存根治] 版本升级后首次启动一次性清空旧语义缓存（MoraySettings.lastCacheWipeVersion 记录，只执行一次）
      try {
        const APP_VERSION = 'v3.19.0'; // 版本号升级时同步更新此处（与 CHANGELOG 保持一致）
        localStorage.removeItem('moray_cache_purge_v3_10_10'); // 清理旧的 localStorage 一次性标志（已迁移到 MoraySettings）
        if (MoraySettings.get('lastCacheWipeVersion') !== APP_VERSION) {
          await CacheStore.clear();
          await MoraySettings.set('lastCacheWipeVersion', APP_VERSION);
          console.log('[MoRay] 版本升级缓存清空完成 (-> ' + APP_VERSION + ')');
        }
      } catch (e) { console.warn('[MoRay] cache purge failed:', e); }
    });

    // 3. 加载会话
    await runStep('conversations', async () => {
      AppState.conversations = await DB.listConversations();
      AppState.conversations.sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));
      await rebuildMsgIndex();
    });

    // 4. 渲染各模块（核心交互绑定最先，随后各 App 独立初始化）
    await runStep('ui-core', async () => {
      AutomationPage.build(); // 重建自动化页面骨架
      renderSessionList();
      setupSessionListEvents();
      setupMessageEvents();
      setupChatHeaderEvents();
    });
    await runStep('robustness', async () => {
      if (typeof Robustness !== 'undefined') Robustness.install();
      if (typeof attachAutoPagination === 'function') {
        attachAutoPagination(PromptsApp, 50);
        attachAutoPagination(SnippetsApp, 50);
      }
    });
    await runStep('prompts', async () => { PromptsApp.bindEvents(); await PromptsApp.reload(); });
    await runStep('snippets', async () => { SnippetsApp.bindEvents(); await SnippetsApp.reload(); });
    await runStep('docs', async () => { DocsApp.build(); DocsApp.bindEvents(); await DocsApp.reload(); });
    await runStep('workflows', async () => { WorkflowsApp.bindEvents(); await WorkflowsApp.reload(); });

    // 5. 设置页 + 模型管理
    await runStep('settings-ui', async () => { SettingsApp.build(); ModelsApp.renderModelList(); });

    // 6. 对比模式重建（模型列表就绪后）
    await runStep('compare', async () => { CompareApp.build(); });

    // 7. 后端探测 + 状态栏 + 健康监控
    await runStep('health', async () => { startHealthMonitor(); });

    // 8. 接管第一阶段原型函数（对话可用性的关键绑定）
    await runStep('overrides', async () => {
      window.moraySendOverride = sendUserMessage;
      window.morayNewChatOverride = () => createNewConversation();
      window.moraySlashOverride = (item) => PromptsApp.handleSlashSelection(item);
      window.morayContextAction = contextActionDispatcher;
      extendCommandPalette();
    });

    // [阶段三/四/五] 增强接线
    await runStep('enhance', async () => {
      setupDocRefTrigger();
      CustomShortcuts.install();
      updatePrivacyBadge();
      appendEnhanceSettings();
      if (typeof appendGatewaySettings === 'function') appendGatewaySettings();
      installGlobalErrorHandler();
      installMicroInteractions();
      AutoBackup.maybeBackup().catch(() => {});
      if (typeof renderInsightsPanel === 'function') renderInsightsPanel();
      if (typeof applyProviderPreset === 'function') applyProviderPreset();
      if (Onboarding.needed()) setTimeout(() => Onboarding.openV2(), 1200);
      const origCheck = HealthMonitor.checkAll.bind(HealthMonitor);
      HealthMonitor.checkAll = async function () { await origCheck(); ConnectionGuide.refresh(); if (typeof syncInputModelLabel === 'function') syncInputModelLabel(); };
      if (typeof HealthMonitor !== 'undefined') HealthMonitor.start();
      if (typeof updateGatewayStatusBar === 'function') updateGatewayStatusBar();
      if (typeof ChatAttachments !== 'undefined') ChatAttachments.install();
      if (typeof installModelSelector === 'function') installModelSelector();
      // [模型同步] 启动初始同步：输入台模型名 = 当前会话将实际使用的模型
      if (typeof syncInputModelLabel === 'function') syncInputModelLabel();
      if (typeof installCompareSyncScroll === 'function') installCompareSyncScroll();
      // [成本中心] 页面构建 + 设置卡 + 示例包入口
      if (typeof CostCenterApp !== 'undefined') CostCenterApp.build();
      if (typeof appendCostSettings === 'function') appendCostSettings();
      // [工具调用] 设置卡片（AI 工具总开关/轮数/每工具开关）
      if (typeof appendToolsSettings === 'function') appendToolsSettings();
      // [阶段0 本机 Agent] 设置分区 + 顶部轻提示 + 按钮态同步（按钮已由 30_chat 文件尾安装，
      // 但彼时 MoraySettings 未初始化，需在设置就绪后按持久化状态刷新按钮高亮）
      if (typeof appendAgentSettings === 'function') appendAgentSettings();
      if (typeof syncNativeAgentBtn === 'function') syncNativeAgentBtn();
      if (typeof refreshNativeAgentBanner === 'function') refreshNativeAgentBanner();
      // [批次修复 #2] 上次会话开启过本机工具 → 应用 Agent 推荐默认模型（当前模型自检通过则跳过）
      if (MoraySettings.get('nativeToolsEnabled') === true && typeof agentApplyRecommendedModel === 'function') {
        setTimeout(() => agentApplyRecommendedModel(), 400);
      }
      // [工具调用] 对比模式提示（CompareApp.build 已重建面板，此时注入不被清掉）
      if (typeof installCompareToolsTip === 'function') installCompareToolsTip();
      // [任务1] 统一全局搜索面板（接管 ⌘K）
      if (typeof PaletteSearch !== 'undefined') PaletteSearch.init();
      const clearSamplesBtn = document.getElementById('clearSamplesBtn');
      if (clearSamplesBtn) clearSamplesBtn.addEventListener('click', async () => {
        const n = await clearSamplePack();
        showNotification('示例数据已清空', `删除 ${n} 条示例条目`, 'success', 2500);
      });
      if (typeof enhanceAccessibility === 'function') enhanceAccessibility();
      if (typeof installPerfGuards === 'function') installPerfGuards();
      if (typeof checkSharedSnippet === 'function') checkSharedSnippet();
      const newFromTemplateBtn = document.getElementById('newFromTemplateBtn');
      if (newFromTemplateBtn) newFromTemplateBtn.addEventListener('click', openConversationTemplates);
      const chatInputEl = document.getElementById('chatInputNormal');
      if (chatInputEl && typeof handleSmartPaste === 'function') chatInputEl.addEventListener('paste', handleSmartPaste);
    });

    // 9. 命令面板新增动作处理
    await runStep('palette-actions', async () => {
      document.querySelectorAll('[data-action="goto-automation"]').forEach(el => {
        el.addEventListener('click', () => document.querySelector('.nav-icon-btn[data-page="automation"]').click());
      });
      document.querySelectorAll('[data-action="export-data"]').forEach(el => {
        el.addEventListener('click', async () => {
          const data = await DB.exportAll();
          downloadJson('moray_backup.json', data);
          showNotification('导出成功', '备份文件已下载', 'success');
        });
      });
      document.querySelectorAll('[data-action="toggle-theme"]').forEach(el => {
        el.addEventListener('click', async () => {
          await MoraySettings.set('theme', MoraySettings.get('theme') === 'light' ? 'dark' : 'light');
          SettingsApp.build();
        });
      });
    });

    // 10. 恢复上次会话
    await runStep('restore', async () => {
      const lastId = MoraySettings.get('activeConversationId');
      if (lastId && AppState.conversations.some(c => c.id === lastId)) {
        await openConversation(lastId);
      } else if (AppState.conversations.length) {
        await openConversation(AppState.conversations[0].id);
      } else {
        showWelcomeView();
        const title = document.getElementById('chatTitle');
        if (title) title.textContent = '新对话';
      }
    });

    // 11. 全局拖拽上传接管：文件拖入时导入文档库
    await runStep('drag-import', async () => {
      document.addEventListener('drop', (e) => {
        const files = Array.from(e.dataTransfer && e.dataTransfer.files || []);
        if (files.length) {
          const supported = files.filter(f => DocsApp.docType(f.name));
          if (supported.length) {
            document.querySelector('.nav-icon-btn[data-page="docs"]').click();
            DocsApp.uploadFiles(supported);
          }
        }
      }, true); // 捕获阶段先于第一阶段处理器
    });

    // 12. 欢迎通知：由探测模块在探测完成后生成（状态文案准确，见 110_polish.js probeBackend）
  } catch (e) {
    console.error('[MoRay] boot failed:', e);
    if (typeof ErrorLog !== 'undefined') ErrorLog.push('boot', (e && e.stack) ? e.stack : String(e.message || e));
    try { showNotification('启动异常', String(e.message || e), 'error', 6000); } catch (e2) { /* 忽略 */ }
  }
}// DOM 就绪后启动（排在第一阶段 bootPhase1 之后执行）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(bootApp, 50));
} else {
  setTimeout(bootApp, 50);
}

// 暴露调试接口
window.MoRay = { DB, AI, AppState, MoraySettings, CompareApp, PromptsApp, SnippetsApp, DocsApp, WorkflowEngine, WorkflowsApp, ModelsApp, SettingsApp, renderMarkdown, PerfHistory };


/* ===================== [V3.2] 设置侧栏导航滚动 ===================== */

/** 设置侧栏导航：点击滚动到对应设置卡片并高亮
 * @returns {void} */
function setupSettingsSideNav() {
  document.addEventListener('click', (e) => {
    const item = e.target.closest('[data-nav-target]');
    if (!item) return;
    const target = item.dataset.navTarget;
    const card = document.getElementById('card-' + target);
    if (!card) return;
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    // 高亮反馈
    card.style.boxShadow = '0 0 0 2px rgba(91,140,255,0.35)';
    setTimeout(() => { card.style.boxShadow = ''; }, 900);
    // 同级 active 态
    item.parentElement.querySelectorAll('.list-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');
  });
}
setupSettingsSideNav();
