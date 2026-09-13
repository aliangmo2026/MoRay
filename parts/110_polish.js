/* ============================================================
   MoRay V3 体验打磨与健壮性
   P1 连接闭环：Ollama检测引导卡 / 错误诊断 / 拉取进度增强
   P2 引导与空状态：3步Onboarding / 统一空状态组件
   P3 差异化：AI自动命名 / 模糊命令面板 / 数据洞察面板
   P4 微交互：滚动到底按钮 / 粘贴保护 / 响应式抽屉
   P5 稳定性：全局错误环形缓冲 / 自动备份 / 危险操作区
   ============================================================ */

/* ===================== [P1.1] Ollama 连接引导卡 ===================== */

/** 连接引导管理器（对话页无后端时显示友好引导） */
const ConnectionGuide = {
  /** @type {boolean} 用户手动关闭本次引导 */
  dismissed: false,

  /** 诊断连接错误类型
   * @param {Error} e - 错误
   * @returns {{type:string, title:string, advice:string}} 诊断结果 */
  diagnose(e) {
    const msg = String((e && e.message) || e);
    if (/abort/i.test(msg) || /timeout/i.test(msg)) {
      return { type: 'timeout', title: '连接超时', advice: 'Ollama 响应过慢或端口被其他程序占用。可尝试重启 Ollama 服务，或在设置中修改服务地址。' };
    }
    // 浏览器层面 refused 与 CORS 均表现为 Failed to fetch，给出双重排查建议
    return { type: 'refused', title: '无法连接到 Ollama 服务', advice: '常见原因：① Ollama 服务未启动（运行 ollama serve）② 跨域限制（设置环境变量 OLLAMA_ORIGINS=* 后重启 Ollama）③ 端口不是 11434（请在设置中修改服务地址）' };
  },

  /**
   * 刷新对话页的连接引导卡（无后端时显示，已连接时移除）
   * @returns {Promise<void>} */
  async refresh() {
    const content = document.getElementById('chatNormalContent');
    if (!content) return;
    let card = document.getElementById('connGuideCard');
    // [V3 修复] 后端不可用时始终显示引导卡（用户本次会话可关闭），不影响消息查看
    if (AI.health === 'ok' || this.dismissed) {
      if (card) card.remove();
      return;
    }
    if (!card) {
      card = document.createElement('div');
      card.id = 'connGuideCard';
      card.className = 'conn-guide glass-card rounded-xl border border-line-ghost/60';
      content.insertBefore(card, content.firstChild.nextSibling || content.firstChild);
    }
    if (AI.health === 'checking') {
      card.innerHTML = `<div class="p-4 flex items-center gap-3">
        <div class="skeleton" style="width:36px;height:36px;border-radius:10px"></div>
        <div class="flex-1"><div class="skeleton" style="height:12px;width:180px;margin-bottom:6px"></div>
        <div class="skeleton" style="height:10px;width:260px"></div></div>
        <span class="text-[11px] text-text-tertiary">正在检测本地 AI 服务…</span></div>`;
      return;
    }
    // down 状态：引导卡（[BYOK] 云端使用为主路径，本地 Ollama 折叠为次要项）
    card.innerHTML = `
      <div class="p-4">
        <div class="flex items-center gap-2 mb-3">
          <div class="w-8 h-8 rounded-lg bg-brand-cobalt/12 flex items-center justify-center flex-shrink-0">
            <i data-lucide="cloud" class="w-4 h-4 text-brand-cobalt"></i>
          </div>
          <div class="flex-1">
            <div class="text-sm font-medium text-text-primary">连接 AI 服务（填你自己的 Key 即可用）</div>
            <div class="text-[11px] text-text-tertiary">选服务商 → 粘贴 Key → 测试连接，三步开始对话</div>
          </div>
          <button class="tool-btn" id="connGuideClose" title="本次不显示" aria-label="关闭引导"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>
        </div>
        <div class="space-y-2 mb-2">
          <select id="connGuidePreset" class="form-select" style="font-size:12px;padding:6px 8px">
            <option value="">— 选择服务商 —</option>
            ${(typeof PROVIDER_PRESETS !== 'undefined' ? PROVIDER_PRESETS : []).map(p => `<option value="${escapeHtml(p.name)}" data-base="${escapeHtml(p.base)}" data-model="${escapeHtml(p.models)}" data-doc="${escapeHtml(p.doc)}">${escapeHtml(p.name)}</option>`).join('')}
            <option value="custom">自定义（手动填地址）</option>
          </select>
          <input type="text" id="connGuideBase" class="form-input" style="font-size:12px;padding:6px 8px" placeholder="接口地址（选服务商自动填充）">
          <div class="flex gap-2">
            <input type="password" id="connGuideKey" class="form-input flex-1" style="font-size:12px;padding:6px 8px" placeholder="粘贴 API Key（sk-...）" autocomplete="off">
            <button class="btn-ghost px-2 rounded-lg border border-line-ghost text-text-tertiary" id="connGuideKeyToggle" title="显示/隐藏" aria-label="显示或隐藏 API Key"><i data-lucide="eye" class="w-3.5 h-3.5"></i></button>
          </div>
          <div class="flex gap-2">
            <button class="btn-primary px-3 py-1.5 rounded-lg text-[11px] flex items-center gap-1" id="connGuideTest"><i data-lucide="zap" class="w-3.5 h-3.5"></i>测试连接</button>
            <button class="btn-primary px-3 py-1.5 rounded-lg text-[11px] flex items-center gap-1 hidden" id="connGuideGo"><i data-lucide="message-circle" class="w-3.5 h-3.5"></i>开始第一次对话</button>
          </div>
          <div class="text-[10px] leading-relaxed" id="connGuideDiag"></div>
        </div>
        <details class="rounded-lg bg-surface-panel/60 px-3 py-2 mb-2">
          <summary class="text-[11px] text-text-secondary cursor-pointer select-none">偏好本地运行？使用 Ollama（免费 · 隐私最佳）</summary>
          <div class="space-y-2 mt-2">
            ${[
              ['1', '下载并安装 Ollama', '访问 ollama.com 下载对应系统版本（支持 Win/Mac/Linux）'],
              ['2', '启动服务', '安装后 Ollama 通常自动后台运行；也可手动执行 ollama serve'],
              ['3', '拉取第一个模型', '终端执行：ollama pull qwen2.5:7b（约 4.7GB，一次即可）']
            ].map(([n, t, d]) => `
              <div class="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-surface-panel/60">
                <span class="w-5 h-5 rounded-full bg-brand-cobalt/15 text-brand-cobalt text-[10px] font-mono flex items-center justify-center flex-shrink-0 mt-0.5">${n}</span>
                <div><div class="text-xs text-text-primary">${t}</div><div class="text-[10px] text-text-tertiary">${d}</div></div>
              </div>`).join('')}
            <div class="flex flex-wrap gap-2">
              <a href="https://ollama.com" target="_blank" rel="noopener" class="btn-ghost px-3 py-1.5 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1 text-text-secondary">
                <i data-lucide="external-link" class="w-3 h-3"></i>ollama.com
              </a>
              <button class="btn-ghost px-3 py-1.5 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1 text-text-secondary" id="connGuideRedetect">
                <i data-lucide="refresh-cw" class="w-3 h-3"></i>我已安装，重新检测
              </button>
              ${window.__TAURI__ ? `<button class="btn-ghost px-3 py-1.5 rounded-lg text-[11px] border border-line-ghost flex items-center gap-1 text-text-secondary" id="connGuideStart">
                <i data-lucide="play" class="w-3 h-3"></i>一键启动 Ollama 服务
              </button>` : ''}
            </div>
          </div>
        </details>
        <div class="text-[10px] text-text-tertiary leading-relaxed">${escapeHtml(typeof PRIVACY_NOTE !== 'undefined' ? PRIVACY_NOTE : '')}</div>
      </div>`;
    refreshIcons();
    on(card.querySelector('#connGuideClose'), 'click', () => { this.dismissed = true; card.remove(); });
    // [BYOK] 服务商预设 → 自动填 baseURL 与默认模型（Key 始终由用户自填）
    const presetSel = card.querySelector('#connGuidePreset');
    const baseInput = card.querySelector('#connGuideBase');
    on(presetSel, 'change', function () {
      const opt = this.options[this.selectedIndex];
      if (opt.dataset.base) {
        baseInput.value = opt.dataset.base;
        card.dataset.model = opt.dataset.model || '';
        card.dataset.doc = opt.dataset.doc || '';
      }
    });
    const keyInput = card.querySelector('#connGuideKey');
    on(card.querySelector('#connGuideKeyToggle'), 'click', () => {
      keyInput.type = keyInput.type === 'password' ? 'text' : 'password';
      card.querySelector('#connGuideKeyToggle').innerHTML = `<i data-lucide="${keyInput.type === 'password' ? 'eye' : 'eye-off'}" class="w-3.5 h-3.5"></i>`;
      refreshIcons();
    });
    on(card.querySelector('#connGuideTest'), 'click', async () => {
      const btn = card.querySelector('#connGuideTest');
      const diag = card.querySelector('#connGuideDiag');
      const base = baseInput.value.trim();
      const key = keyInput.value.trim();
      if (!base) { diag.innerHTML = '<span class="text-danger">请先选择服务商或填写接口地址</span>'; return; }
      if (!key) { diag.innerHTML = '<span class="text-danger">请先粘贴 API Key</span>'; return; }
      btn.disabled = true;
      btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>测试中…';
      refreshIcons();
      await MoraySettings.set({ openaiBaseURL: base, openaiAPIKey: key, openaiModel: card.dataset.model || '', openaiEnabled: true });
      const r = await testCloudConnectionDetailed();
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="zap" class="w-3.5 h-3.5"></i>测试连接';
      refreshIcons();
      if (r.ok) {
        await AI.detectBackend();
        diag.innerHTML = `<span class="text-success">✓ 连接成功</span> · 延迟 ${r.latency}ms · 模型 ${escapeHtml(r.model || '')}`;
        const go = card.querySelector('#connGuideGo');
        go.classList.remove('hidden');
        go.addEventListener('click', () => {
          card.remove();
          const inp = document.getElementById('chatInputNormal');
          if (inp) inp.focus();
          showNotification('准备就绪', '已连接 ' + (card.dataset.doc || '服务商') + '，输入内容即可开始对话', 'success', 3000);
        });
        showNotification('连接成功', '延迟 ' + r.latency + 'ms', 'success', 2500);
      } else {
        const corsHint = /fetch|网络/i.test(r.error || '') && (location.protocol === 'https:' || location.hostname)
          ? '<br><span class="text-text-tertiary">若控制台报 CORS 跨域：可部署 Cloudflare Worker 代理后，把接口地址换成代理地址（规则见设置页"跨域怎么办"帮助或 deploy/README.md）。</span>' : '';
        diag.innerHTML = `<span class="text-danger">✗ ${escapeHtml(r.error)}</span><br><span class="text-text-tertiary">建议：${escapeHtml(r.advice || '')}</span>${corsHint}`;
        showNotification('连接失败', r.error, 'error', 3500);
      }
    });
    on(card.querySelector('#connGuideRedetect'), 'click', async () => {
      await AI.detectBackend();
      await this.refresh();
      showNotification(AI.health === 'ok' ? '连接成功' : '仍未检测到', AI.health === 'ok' ? `发现 ${AI.models.length} 个模型` : '请确认 Ollama 已启动后重试', AI.health === 'ok' ? 'success' : 'warning', 3000);
      if (AI.health === 'ok' && ModelsApp) ModelsApp.renderModelList();
    });
    const startBtn = card.querySelector('#connGuideStart');
    if (startBtn) startBtn.addEventListener('click', async () => {
      try {
        const { invoke } = window.__TAURI__.core;
        await invoke('start_ollama');
        showNotification('启动命令已发送', '等待 3 秒后自动重新检测', 'info', 2500);
        setTimeout(async () => { await AI.detectBackend(); await this.refresh(); }, 3000);
      } catch (e) { showNotification('启动失败', String(e.message || e), 'error', 3000); }
    });
    refreshIcons();
  }
};

/* ===================== [P1.2] 云端服务商预设 + 连接测试 ===================== */

/** 隐私说明（引导卡与设置页共用，原文统一） */
const PRIVACY_NOTE = '你的 API Key 仅保存在本浏览器本地，不会上传到 MoRay；直连时仅发往你选择的服务商；若你填写了第三方代理地址，请求会经该代理转发，请使用官方或自建代理。';

/** 云端服务商预设（baseURL 自动填充） */
const PROVIDER_PRESETS = [
  { name: 'DeepSeek', base: 'https://api.deepseek.com/v1', models: 'deepseek-chat', doc: 'platform.deepseek.com' },
  { name: 'OpenAI', base: 'https://api.openai.com/v1', models: 'gpt-4o-mini', doc: 'platform.openai.com' },
  { name: 'Moonshot Kimi', base: 'https://api.moonshot.cn/v1', models: 'moonshot-v1-8k', doc: 'platform.moonshot.cn' },
  { name: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', models: 'glm-4-flash', doc: 'open.bigmodel.cn' },
  { name: 'OpenRouter', base: 'https://openrouter.ai/api/v1', models: 'openai/gpt-4o-mini', doc: 'openrouter.ai' },
  { name: '硅基流动', base: 'https://api.siliconflow.cn/v1', models: 'deepseek-ai/DeepSeek-V3', doc: 'siliconflow.cn' },
  { name: '通义千问', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: 'qwen-plus', doc: 'dashscope.console.aliyun.com' }
];

/**
 * 云端连接真实验测（1 token 最小请求，返回延迟/模型/具体错误）
 * @returns {Promise<{ok:boolean, latency?:number, model?:string, error?:string, advice?:string}>} 结果 */
async function testCloudConnectionDetailed() {
  const base = AI.openaiBase;
  const key = MoraySettings.getAPIKey();
  const model = MoraySettings.get('openaiModel') || 'gpt-4o-mini';
  if (!base) return { ok: false, error: '未填写接口地址', advice: '选择服务商预设或手动输入 baseURL（以 /v1 结尾）' };
  if (!key) return { ok: false, error: '未填写 API Key', advice: '在服务商控制台创建 Key 后粘贴到此处' };
  const started = performance.now();
  try {
    const res = await fetch(base + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key },
      body: JSON.stringify({ model, messages: [{ role: 'user', content: 'hi' }], max_tokens: 1, stream: false })
    });
    const latency = Math.round(performance.now() - started);
    if (res.ok) {
      const j = await res.json().catch(() => ({}));
      return { ok: true, latency, model: (j.model) || model };
    }
    const text = await res.text().catch(() => '');
    const map = {
      401: { error: 'API Key 无效（401）', advice: '检查 Key 是否复制完整、是否属于该服务商、账户是否有余额' },
      404: { error: '地址或模型不存在（404）', advice: '确认 baseURL 以 /v1 结尾；确认模型名在服务商处可用' },
      429: { error: '请求被限流（429）', advice: 'Key 有效但触发限流，稍后重试或升级套餐' },
      402: { error: '余额不足（402）', advice: '请前往服务商控制台充值' }
    };
    const m = map[res.status] || { error: 'HTTP ' + res.status, advice: text.slice(0, 140) || '检查地址与网络' };
    return Object.assign({ ok: false, latency }, m);
  } catch (e) {
    const msg = String(e.message || e);
    let advice = '检查网络连接；若使用代理请确认代理可用；国内网络建议选择 DeepSeek/智谱/通义等服务商';
    // [BYOK] https/线上环境下直连常见 CORS 拦截 → 提示 Worker 代理
    if (/failed to fetch|networkerror|load failed/i.test(msg)) {
      advice += '。若控制台报 CORS 跨域：部署 Cloudflare Worker 代理后，把接口地址按 deploy/README 规则换成代理地址即可（设置页"跨域怎么办"帮助可一键拼接）';
    }
    return { ok: false, error: '网络错误：' + msg, advice };
  }
}

/* ===================== [P2.4] 首次启动 Onboarding 向导 ===================== */

/** 3 步新手引导 */
const Onboarding = {
  LS_KEY: 'moray_onboarded',
  /** 是否需要显示
   * @returns {boolean} */
  needed() { try { return !localStorage.getItem(this.LS_KEY); } catch (e) { return false; } },

  /** 标记完成
   * @returns {void} */
  done() { try { localStorage.setItem(this.LS_KEY, '1'); } catch (e) { /* 忽略 */ } },

  /** 打开引导（步骤1欢迎 -> 2选后端 -> 3选主题）
   * @returns {void} */
  open() {
    let step = 0;
    const box = showModal('<div id="obBody"></div>', { title: '欢迎使用 MoRay', icon: 'sparkles', footer: false });
    box.style.width = '560px';
    const render = () => {
      const body = box.querySelector('#obBody');
      const dots = [0, 1, 2].map(i => `<span class="ob-dot ${i === step ? 'active' : ''}"></span>`).join('');
      let inner = '';
      if (step === 0) {
        inner = `
          <div class="text-center py-4">
            <div class="w-16 h-16 mx-auto mb-4 rounded-2xl bg-gradient-to-br from-brand-cobalt/20 to-brand-cyan/15 border border-brand-cobalt/25 flex items-center justify-center">
              <span class="gradient-text font-bold text-2xl">◈</span>
            </div>
            <div class="text-lg font-semibold text-text-primary mb-2">你好，我是 <span class="gradient-text font-semibold">MoRay</span></div>
            <p class="text-xs text-text-tertiary mb-5">本地优先的 AI 开发者工作台 —— 数据永不离开你的设备</p>
            <div class="grid grid-cols-3 gap-2 text-center">
              ${[['shield-check', '本地隐私', '对话/文档全本地'], ['columns-2', '多模型对比', '竞速+评分'], ['zap', '省钱网关', '缓存省 30-50% token']].map(([ic, t, d]) => `
                <div class="rounded-lg bg-surface-panel/70 border border-line-ghost/60 p-3">
                  <i data-lucide="${ic}" class="w-4 h-4 text-brand-cobalt mx-auto mb-1.5"></i>
                  <div class="text-[11px] text-text-primary mb-0.5">${t}</div>
                  <div class="text-[10px] text-text-tertiary">${d}</div>
                </div>`).join('')}
            </div>
          </div>`;
      } else if (step === 1) {
        inner = `
          <div class="text-center mb-4"><div class="text-sm font-medium text-text-primary">选择你的 AI 后端</div>
          <div class="text-[11px] text-text-tertiary">之后可随时在设置中添加另一种</div></div>
          <div class="grid grid-cols-2 gap-3">
            <div class="doc-card p-4 cursor-pointer" data-ob-backend="ollama">
              <i data-lucide="hard-drive" class="w-5 h-5 text-brand-cobalt mb-2"></i>
              <div class="text-sm text-text-primary mb-1">本地 Ollama</div>
              <div class="text-[10px] text-text-tertiary leading-relaxed">完全免费 · 隐私最佳<br>需要 8GB+ 内存</div>
            </div>
            <div class="doc-card p-4 cursor-pointer" data-ob-backend="cloud">
              <i data-lucide="cloud" class="w-5 h-5 text-brand-violet mb-2"></i>
              <div class="text-sm text-text-primary mb-1">云端 API</div>
              <div class="text-[10px] text-text-tertiary leading-relaxed">无需本地算力<br>按量计费（有免费额度）</div>
            </div>
          </div>
          <div class="text-[10px] text-text-tertiary mt-3" id="obBackendHint"></div>`;
      } else {
        inner = `
          <div class="text-center mb-4"><div class="text-sm font-medium text-text-primary">选择主题</div>
          <div class="text-[11px] text-text-tertiary">之后可在设置中更换壁纸与强调色</div></div>
          <div class="grid grid-cols-2 gap-3">
            <div class="doc-card p-4 cursor-pointer text-center" data-ob-theme="dark">
              <div class="h-16 rounded-lg mb-2 border border-line-ghost" style="background:linear-gradient(135deg,#0A0C14,#1A1F30)"></div>
              <span class="text-xs text-text-primary">深色</span>
            </div>
            <div class="doc-card p-4 cursor-pointer text-center" data-ob-theme="light">
              <div class="h-16 rounded-lg mb-2 border border-line-ghost" style="background:linear-gradient(135deg,#F3F5FA,#FFFFFF)"></div>
              <span class="text-xs text-text-primary">浅色</span>
            </div>
          </div>`;
      }
      body.innerHTML = `
        <div class="flex justify-center gap-1.5 mb-4">${dots}</div>
        <div class="ob-step" style="animation:modalIn 200ms ease-in-out">${inner}</div>
        <div class="flex justify-between items-center mt-5">
          <button class="text-[11px] text-text-tertiary hover:text-text-secondary" id="obSkip">跳过引导</button>
          <div class="flex gap-2">
            ${step > 0 ? '<button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="obPrev">上一步</button>' : ''}
            <button class="btn-primary px-4 py-1.5 rounded-lg text-xs" id="obNext">${step === 2 ? '开始使用' : '下一步'}</button>
          </div>
        </div>`;
      refreshIcons();
      on(box.querySelector('#obSkip'), 'click', finish);
      const next = box.querySelector('#obNext');
      if (step === 0) next.addEventListener('click', () => { step = 1; render(); });
      else if (step === 1) {
        // 未选择后端时"下一步"直接进主题
        box.querySelectorAll('[data-ob-backend]').forEach(el => el.addEventListener('click', () => {
          const b = el.dataset.obBackend;
          const hint = box.querySelector('#obBackendHint');
          if (b === 'ollama') hint.innerHTML = '已选择本地 Ollama：完成引导后按对话页的"三步开启"操作即可';
          else {
            hint.innerHTML = '已选择云端 API：正在打开设置页…';
            setTimeout(() => { document.querySelector('.nav-icon-btn[data-page="settings"]').click(); }, 400);
          }
        }));
        next.addEventListener('click', () => { step = 2; render(); });
      } else {
        box.querySelectorAll('[data-ob-theme]').forEach(el => el.addEventListener('click', () => {
          MoraySettings.set('theme', el.dataset.obTheme);
        }));
        next.addEventListener('click', finish);
      }
      const prev = box.querySelector('#obPrev');
      if (prev) prev.addEventListener('click', () => { step = Math.max(0, step - 1); render(); });
    };
    const finish = () => { this.done(); closeModal(); showNotification('一切就绪', '按 ⌘/Ctrl+K 打开命令面板探索更多', 'success', 3500); };
    render();
  }
};

/* ===================== [P2.5] 统一空状态组件 ===================== */

/**
 * 统一空状态渲染（图标+标题+说明+主操作）
 * @param {{icon:string, title:string, desc:string, actionText:string, actionFn:Function, dragHint?:boolean}} cfg - 配置
 * @returns {string} HTML */
function renderEmptyState(cfg) {
  return `<div class="empty-art">
    <div class="empty-icon"><i data-lucide="${cfg.icon}" class="w-6 h-6 text-brand-cobalt"></i></div>
    <div class="empty-title">${cfg.title}</div>
    <div class="empty-hint mb-3">${cfg.desc}${cfg.dragHint ? '<br>也可以直接拖拽文件到窗口' : ''}</div>
    ${cfg.actionText ? `<button class="btn-primary px-4 py-1.5 rounded-lg text-xs relative overflow-hidden" data-empty-action onclick="createRipple(event)">${cfg.actionText}</button>` : ''}
  </div>`;
}

/** 绑定空状态主操作（事件委托，渲染后调用一次）
 * @param {HTMLElement} container - 容器
 * @param {Function} fn - 点击回调
 * @returns {void} */
function bindEmptyAction(container, fn) {
  const btn = container.querySelector('[data-empty-action]');
  if (btn) btn.addEventListener('click', fn);
}

/* ===================== [P3.6] AI 自动命名会话 ===================== */

/**
 * AI 自动生成会话标题（静默，失败不影响主流程）
 * @param {Object} conv - 会话
 * @param {string} firstMessage - 首条用户消息
 * @returns {Promise<void>} */
async function autoNameConversation(conv, firstMessage) {
  if (!MoraySettings.get('autoNaming')) return;      // 设置关闭
  if (conv.renamed) return;                           // 用户手动命名过，不覆盖
  const model = conv.model || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name);
  if (!model || AI.backend === 'none') return;
  const styleHint = { 简洁: '4-8个字，直击主题', 专业: '技术术语准确，8字内', 活泼: '带一点趣味emoji，8字内' }[MoraySettings.get('namingStyle') || '简洁'];
  try {
    const r = await AI.chat({
      model,
      messages: [{ role: 'user', content: `为下面的对话起一个标题（${styleHint}，不要引号和句号）：\n${firstMessage.slice(0, 100)}` }]
    });
    let title = (r.content || '').trim().replace(/^["'「『]|["'」』]$/g, '').split('\n')[0].slice(0, 20);
    if (!title) return;
    conv.title = title;
    conv.autoNamed = true;
    await DB.updateConversation(conv.id, { title, autoNamed: true });
    const titleEl = document.getElementById('chatTitle');
    if (AppState.activeConv && AppState.activeConv.id === conv.id && titleEl) titleEl.textContent = title;
    renderSessionList();
  } catch (e) { console.warn('[MoRay] 自动命名失败（不影响使用）:', e); }
}

/* ===================== [P3.7] 命令面板模糊搜索 + 最近使用 ===================== */

/** 模糊匹配（子序列 + 连续加分）
 * @param {string} target - 目标文本
 * @param {string} query - 查询（如拼音首字母 xhd）
 * @returns {boolean} 是否匹配 */
function fuzzyMatch(target, query) {
  if (!query) return true;
  const t = String(target).toLowerCase(), q = String(query).toLowerCase();
  let ti = 0, score = 0, streak = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const idx = t.indexOf(q[qi], ti);
    if (idx < 0) return false;
    score += (idx === ti ? ++streak : (streak = 1, 1));
    ti = idx + 1;
  }
  return score > 0;
}

/** 记录命令使用（最近使用置顶 + 频率排序）
 * @param {string} action - 命令 action 标识
 * @returns {void} */
function trackCommandUse(action) {
  try {
    const list = JSON.parse(localStorage.getItem('moray_cmd_recent') || '{}');
    list[action] = (list[action] || 0) + 1;
    list.__last = action;
    localStorage.setItem('moray_cmd_recent', JSON.stringify(list));
  } catch (e) { /* 忽略 */ }
}

/* ===================== [P4.10] 输入粘贴保护 ===================== */

/** 超长文本粘贴保护（>5万字提示并截断）
 * @param {ClipboardEvent} e - 粘贴事件
 * @returns {void} */
function guardPasteLength(e) {
  const text = e.clipboardData && e.clipboardData.getData('text');
  if (!text) return;
  const LIMIT = 50000;
  if (text.length > LIMIT) {
    e.preventDefault();
    const input = e.target;
    const truncated = text.slice(0, LIMIT);
    const start = input.selectionStart || 0, end = input.selectionEnd || 0;
    input.value = input.value.slice(0, start) + truncated + '\n…[超长文本已截断，原文共 ' + text.length + ' 字]' + input.value.slice(end);
    if (typeof updateInputStats === 'function') updateInputStats(input.value);
    showNotification('粘贴已截断', `原文 ${text.length} 字超过 5 万字上限，已保留前 ${LIMIT} 字（超长文本建议走知识库）`, 'warning', 5000);
  }
}

/* ===================== [P5.11] 全局错误环形缓冲 ===================== */

/** 错误日志环形缓冲（最近50条，设置页可查看/复制/导出） */
const ErrorLog = {
  /** @type {Array} */
  entries: [],
  MAX: 50,

  /**
   * 记录错误
   * @param {string} source - 来源（window/workflow/db…）
   * @param {string} message - 消息
   * @returns {void} */
  push(source, message) {
    this.entries.unshift({ at: Date.now(), source, message: String(message).slice(0, 300) });
    if (this.entries.length > this.MAX) this.entries.length = this.MAX;
  },

  /** 打开错误日志面板
   * @returns {void} */
  open() {
    const items = this.entries.length
      ? this.entries.map(e => `<div class="chunk-item"><div class="flex justify-between mb-1">
          <span class="text-warning">${escapeHtml(e.source)}</span>
          <span class="text-text-tertiary text-[10px]">${relTime(e.at)}</span></div>
          <div class="text-text-secondary" style="word-break:break-all">${escapeHtml(e.message)}</div></div>`).join('')
      : '<div class="text-xs text-text-tertiary text-center py-8">运行良好，没有错误记录 🎉</div>';
    const box = showModal(`<div class="space-y-2" style="max-height:55vh;overflow-y:auto">${items}</div>`,
      { title: '错误日志（最近 ' + this.entries.length + ' 条）', icon: 'bug', wide: true,
        footer: `<button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="errCopyBtn">复制全部</button>
                 <button class="btn-primary px-3 py-1.5 rounded-lg text-xs" id="errCloseBtn">关闭</button>` });
    on(box.querySelector('#errCloseBtn'), 'click', closeModal);
    on(box.querySelector('#errCopyBtn'), 'click', async () => {
      const ok = await copyToClipboard(JSON.stringify(this.entries, null, 2));
      showNotification(ok ? '已复制' : '复制失败', '', ok ? 'success' : 'error', 1500);
    });
  }
};

/** 安装全局错误兜底（友好的错误条 + 环形缓冲，不白屏）
 * @returns {void} */
function installGlobalErrorHandler() {
  window.addEventListener('error', (e) => {
    ErrorLog.push('window', e.message + ' @' + (e.lineno || '?'));
    showNotification('出现问题，已记录', String(e.message).slice(0, 60), 'error', 2500);
  });
  window.addEventListener('unhandledrejection', (e) => {
    ErrorLog.push('promise', String((e.reason && e.reason.message) || e.reason).slice(0, 300));
    showNotification('操作未完成，已记录', String((e.reason && e.reason.message) || e.reason).slice(0, 60), 'error', 2500);
  });
}

/* ===================== [P5.12] 自动备份 + 危险操作区 ===================== */

/** 自动备份管理（每24小时首次启动备份到 settings store，保留最近3份） */
const AutoBackup = {
  LS_KEY: 'moray_last_backup',

  /** 启动时检查是否需要备份
   * @returns {Promise<void>} */
  async maybeBackup() {
    try {
      const last = parseInt(localStorage.getItem(this.LS_KEY) || '0', 10);
      if (Date.now() - last < 24 * 3600e3) return;
      const data = await DB.exportAll();
      const backups = (await DB.getSetting('auto_backups', [])) || [];
      backups.unshift({ at: Date.now(), size: JSON.stringify(data).length, data });
      const trimmed = backups.slice(0, 3);
      await DB.setSetting('auto_backups', trimmed);
      localStorage.setItem(this.LS_KEY, String(Date.now()));
      console.log('[MoRay] 自动备份完成（保留', trimmed.length, '份）');
    } catch (e) { console.warn('[MoRay] 自动备份失败:', e); }
  },

  /** 打开备份恢复面板（列表 + 预览 + 恢复确认）
   * @returns {Promise<void>} */
  async openRestore() {
    const backups = (await DB.getSetting('auto_backups', [])) || [];
    if (!backups.length) { showNotification('暂无自动备份', '自动备份每 24 小时创建一次', 'info', 2500); return; }
    const box = showModal(`<div class="space-y-2" style="max-height:55vh;overflow-y:auto">
      ${backups.map((b, i) => `
        <div class="doc-card p-3 flex items-center justify-between">
          <div><div class="text-xs text-text-primary">备份 #${i + 1}</div>
            <div class="text-[10px] text-text-tertiary">${new Date(b.at).toLocaleString()} · ${formatBytes(b.size)}</div></div>
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" data-restore-idx="${i}">恢复此备份</button>
        </div>`).join('')}
    </div>`, { title: '自动备份恢复', icon: 'archive', wide: true, footer: false });
    box.querySelectorAll('[data-restore-idx]').forEach(btn => {
      btn.addEventListener('click', () => {
        const idx = parseInt(btn.dataset.restoreIdx, 10);
        const b = backups[idx];
        showConfirm('恢复备份', `将把 ${new Date(b.at).toLocaleString()} 的备份合并导入当前数据（合并模式，不删除现有数据）。继续？`, async () => {
          const r = await DB.importAll(b.data, false);
          showNotification('恢复完成', `写入 ${r.imported} 条记录，刷新页面生效`, 'success', 4000);
        }, { okText: '恢复' });
      });
    });
  },

  /** 危险操作区（清空所有数据需输入 DELETE；重置应用）
   * @returns {void} */
  openDangerZone() {
    const box = showModal(`
      <div class="space-y-3">
        <div class="rounded-lg border border-danger/30 bg-danger/5 p-3">
          <div class="text-xs text-danger font-medium mb-1">清空所有数据</div>
          <div class="text-[10px] text-text-tertiary mb-2">删除全部会话/提示词/片段/文档/工作流/缓存，不可恢复。输入 <span class="font-mono text-danger">DELETE</span> 确认：</div>
          <div class="flex gap-2">
            <input type="text" id="dzConfirmInput" class="form-input" style="flex:1;font-family:var(--font-mono)" placeholder="DELETE">
            <button class="px-3 py-2 rounded-lg text-xs bg-danger text-white" id="dzWipeBtn">清空</button>
          </div>
        </div>
        <div class="rounded-lg border border-line-ghost p-3">
          <div class="text-xs text-text-primary font-medium mb-1">重置应用</div>
          <div class="text-[10px] text-text-tertiary mb-2">清空数据并恢复默认设置与引导（等同于全新安装）。</div>
          <button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="dzResetBtn">重置应用</button>
        </div>
      </div>`, { title: '危险操作区', icon: 'alert-triangle', footer: false });
    const input = box.querySelector('#dzConfirmInput');
    on(box.querySelector('#dzWipeBtn'), 'click', () => {
      if (input.value.trim() !== 'DELETE') { showNotification('请输入 DELETE', '确认文字不匹配', 'warning', 2000); return; }
      closeModal();
      DB.clearStores(['conversations', 'messages', 'prompts', 'snippets', 'workflows', 'documents', 'apicache']).then(() => {
        showNotification('已清空', '全部数据已删除，刷新页面生效', 'success', 4000);
      }).catch(e => showNotification('清空失败', e.message, 'error'));
    });
    on(box.querySelector('#dzResetBtn'), 'click', () => {
      showConfirm('重置应用', '将清空全部数据并恢复默认设置，等同全新安装。确定继续？', async () => {
        await DB.clearStores(['conversations', 'messages', 'prompts', 'snippets', 'workflows', 'documents', 'apicache', 'settings']);
        try { localStorage.removeItem('moray_onboarded'); localStorage.removeItem('moray_settings'); } catch (e) { /* 忽略 */ }
        showNotification('已重置', '页面即将刷新…', 'success', 2000);
        setTimeout(() => location.reload(), 1500);
      }, { danger: true, okText: '重置' });
    });
  }
};

/* ===================== [P4] 微交互安装器 ===================== */

// [V3 修复] 切换到对话页时即时刷新引导卡
document.addEventListener('click', (e) => {
  const nav = e.target.closest && e.target.closest('.nav-icon-btn[data-page="chat"]');
  if (nav && typeof ConnectionGuide !== 'undefined') setTimeout(() => ConnectionGuide.refresh(), 250);
}, true);

// [成本中心] 切换到成本中心页时刷新数据
document.addEventListener('click', (e) => {
  const nav = e.target.closest && e.target.closest('.nav-icon-btn[data-page="cost"]');
  if (nav && typeof CostCenterApp !== 'undefined') setTimeout(() => CostCenterApp.build(), 250);
}, true);

/** 安装微交互：按压反馈 / 列表错峰淡入 / 回到底部按钮 / 响应式抽屉
 * @returns {void} */
function installMicroInteractions() {
  // 1) 全局按钮按压反馈（CSS 类挂 body 即生效）
  document.body.classList.add('moray-micro');

  // 2) 消息区"回到底部"悬浮按钮
  const box = document.getElementById('chatMessagesNormal');
  const scrollParent = box ? box.parentElement : null;
  if (scrollParent && !document.getElementById('scrollBottomBtn')) {
    const btn = document.createElement('button');
    btn.id = 'scrollBottomBtn';
    btn.className = 'scroll-bottom-btn';
    btn.setAttribute('aria-label', '回到底部');
    btn.innerHTML = '<i data-lucide="arrow-down" class="w-4 h-4"></i>';
    scrollParent.style.position = 'relative';
    scrollParent.appendChild(btn);
    btn.addEventListener('click', () => { scrollParent.scrollTo({ top: scrollParent.scrollHeight, behavior: 'smooth' }); btn.classList.remove('visible'); });
    let ticking = false;
    scrollParent.addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const away = scrollParent.scrollHeight - scrollParent.scrollTop - scrollParent.clientHeight > 240;
        btn.classList.toggle('visible', away && AppState.messages.length > 0);
        ticking = false;
      });
    }, { passive: true });
    refreshIcons();
  }

  // 3) 响应式：窗口 <1024px 时中间栏折叠为抽屉（汉堡按钮）
  const mq = window.matchMedia('(max-width: 1023px)');
  const midBar = document.querySelector('aside.w-\\[260px\\], aside:nth-child(2)');
  if (midBar && !document.getElementById('drawerToggleBtn')) {
    const drawerBtn = document.createElement('button');
    drawerBtn.id = 'drawerToggleBtn';
    drawerBtn.className = 'tool-btn drawer-toggle-btn';
    drawerBtn.setAttribute('aria-label', '切换侧栏');
    drawerBtn.innerHTML = '<i data-lucide="panel-left" class="w-4 h-4"></i>';
    drawerBtn.addEventListener('click', () => {
      document.body.classList.toggle('drawer-open');
      midBar.classList.toggle('drawer-visible');
    });
    // 插入到聊天页头部左侧
    const chatHeader = document.querySelector('#page-chat header');
    if (chatHeader) chatHeader.insertBefore(drawerBtn, chatHeader.firstChild);
    refreshIcons();
  }
  const applyResponsive = () => { document.body.classList.toggle('narrow-screen', mq.matches); };
  mq.addEventListener ? mq.addEventListener('change', applyResponsive) : mq.addListener(applyResponsive);
  applyResponsive();

  // 4) 超长粘贴保护
  const input = document.getElementById('chatInputNormal');
  if (input) input.addEventListener('paste', guardPasteLength);
}


/* ===================== [P3.8] 数据洞察面板（原生 SVG/CSS，无图表库） ===================== */

/** 数字滚动增长动画（500ms，从0到目标值）
 * @param {HTMLElement} el - 目标元素
 * @param {number} target - 目标数值
 * @param {string} [format] - 格式化函数名（k/cost/pct/int） */
function countUp(el, target, format) {
  const started = performance.now();
  const DUR = 500;
  const fmt = (v) => {
    if (format === 'k') return (v / 1000).toFixed(1) + 'k';
    if (format === 'cost') return '¥' + v.toFixed(3);
    if (format === 'pct') return Math.round(v) + '%';
    return String(Math.round(v));
  };
  const tick = (now) => {
    const p = Math.min(1, (now - started) / DUR);
    const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
    el.textContent = fmt(target * eased);
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/** 小时级用量记录（供热力图）：包装 UsageTracker.record
 * @returns {void} */
function installHourlyTracking() {
  if (UsageTracker.__hourlyBound) return;
  UsageTracker.__hourlyBound = true;
  const orig = UsageTracker.record.bind(UsageTracker);
  UsageTracker.record = async function (model, stats, fromCache, costInfo) {
    const r = await orig(model, stats, fromCache, costInfo);
    try {
      const h = new Date().getHours();
      const key = dayKey();
      const hourly = await gatewayMetaGet('__hourly__', {});
      hourly[key] = hourly[key] || {};
      hourly[key][h] = (hourly[key][h] || 0) + 1;
      await gatewayMetaSet('__hourly__', hourly);
    } catch (e) { /* 忽略 */ }
    return r;
  };
}

/** 渲染数据洞察面板（插入设置页网关卡片之后）
 * @returns {Promise<void>} */
async function renderInsightsPanel() {
  const container = document.querySelector('#page-settings .max-w-2xl');
  if (!container) return;
  let anchor = container.querySelector('#gatewaySettingsCard');
  if (!anchor) anchor = container.querySelector('#enhanceSettingsCard');
  if (!anchor || container.querySelector('#insightsPanel')) return;

  installHourlyTracking();
  const usage = await gatewayMetaGet('__usage__', { byDay: {} });
  const hourly = await gatewayMetaGet('__hourly__', {});
  const cacheStats = await GatewayCache.stats();
  const days = Object.keys(usage.byDay).sort();
  let totalPrompt = 0, totalCompletion = 0, totalCost = 0, totalReq = 0;
  const byModel = {};
  days.forEach(k => {
    const d = usage.byDay[k];
    totalPrompt += d.prompt || 0; totalCompletion += d.completion || 0;
    // [统一人民币] 优先 costCNY，历史日（无 costCNY）回退 d.cost
    totalCost += (d.costCNY != null ? d.costCNY : d.cost) || 0; totalReq += d.requests || 0;
    Object.keys(d.byModel || {}).forEach(m => {
      byModel[m] = byModel[m] || 0;
      byModel[m] += (d.byModel[m].completion || 0) + (d.byModel[m].prompt || 0);
    });
  });
  const msgTotal = Object.values(AppState._convMsgIndex || {}).reduce((s, a) => s + a.length, 0);
  const avgSpeed = PerfHistory.length ? Math.round(PerfHistory.filter(p => p.ok).reduce((s, p) => s + (p.tokPerSec || 0), 0) / Math.max(1, PerfHistory.filter(p => p.ok).length)) : 0;

  const card = document.createElement('div');
  card.id = 'insightsPanel';
  card.className = 'glass-card rounded-xl p-5 border border-line-ghost/60';
  // 近7天对话次数折线（SVG）
  const last7 = days.slice(-7);
  const maxReq = Math.max(1, ...last7.map(k => usage.byDay[k].requests || 0));
  const pts = last7.map((k, i) => `${20 + i * (300 / Math.max(1, last7.length - 1))},${58 - (usage.byDay[k].requests || 0) / maxReq * 46}`).join(' ');
  // 模型环形图（SVG stroke 分段）
  const modelNames = Object.keys(byModel);
  const modelTotal = modelNames.reduce((s, m) => s + byModel[m], 0) || 1;
  const COLORS = ['#5B8CFF', '#9D7BFF', '#3AD6E8', '#3FD68F', '#F2B24C', '#FF5C6C'];
  let acc = 0;
  const donut = modelNames.map((m, i) => {
    const frac = byModel[m] / modelTotal;
    const seg = `<circle cx="30" cy="30" r="22" fill="none" stroke="${COLORS[i % COLORS.length]}" stroke-width="9"
      stroke-dasharray="${(frac * 138.2).toFixed(1)} 138.2" stroke-dashoffset="${(-acc * 138.2).toFixed(1)}"
      transform="rotate(-90 30 30)"><title>${escapeHtml(m)}：${byModel[m]} tokens</title></circle>`;
    acc += frac;
    return seg;
  }).join('') || '<circle cx="30" cy="30" r="22" fill="none" stroke="var(--color-surface-floating)" stroke-width="9"/>';
  // token 消耗 vs 缓存节省（7天柱状）
  const maxTok = Math.max(1, ...last7.map(k => (usage.byDay[k].prompt || 0) + (usage.byDay[k].completion || 0)));
  const bars = last7.map(k => {
    const d = usage.byDay[k];
    const used = (d.prompt || 0) + (d.completion || 0);
    return `<div class="flex-1 flex flex-col items-center gap-0.5" title="${k}：消耗 ${used} tokens">
      <div style="width:70%;max-width:22px;height:${Math.max(3, used / maxTok * 42)}px;background:linear-gradient(180deg,var(--color-brand-cobalt),var(--color-brand-cobalt-hover));border-radius:3px"></div>
      <span class="text-[9px] text-text-tertiary">${k.slice(5)}</span></div>`;
  }).join('');
  // 7×24 活跃热力
  const heatCells = [];
  for (let d = 6; d >= 0; d--) {
    const day = dayKey(Date.now() - d * 86400e3);
    for (let h = 0; h < 24; h += 2) {
      const v = (hourly[day] && hourly[day][h]) || 0;
      const alpha = Math.min(0.9, v * 0.25);
      heatCells.push(`<div title="${day.slice(5)} ${h}时：${v} 次请求" style="width:100%;aspect-ratio:1;border-radius:2px;background:rgba(91,140,255,${alpha.toFixed(2)})"></div>`);
    }
  }

  card.innerHTML = `
    <h3 class="text-sm font-semibold text-text-primary mb-4 flex items-center gap-2">
      <i data-lucide="chart-column" class="w-4 h-4 text-brand-cyan"></i>数据洞察
      <span class="text-[10px] text-text-tertiary font-normal ml-1">悬停图表查看数值</span>
    </h3>
    <!-- 数字卡片 -->
    <div class="grid grid-cols-6 gap-2 mb-4 text-center">
      ${[['总会话', AppState.conversations.length, 'int'], ['累计消息', msgTotal, 'int'], ['累计tokens', totalPrompt + totalCompletion, 'k'],
         ['累计费用', totalCost, 'cost'], ['缓存节省率', cacheStats.hitRate, 'pct'], ['平均速度', avgSpeed, 'int']].map(([label, val, fmt], i) => `
        <div class="rounded-lg bg-surface-panel/60 py-2">
          <div class="text-sm font-mono text-text-primary" id="insNum${i}">0</div>
          <div class="text-[10px] text-text-tertiary">${label}</div>
        </div>`).join('')}
    </div>
    <div class="grid grid-cols-2 gap-4 mb-4">
      <div>
        <div class="text-[10px] text-text-tertiary mb-1.5">近 7 天对话次数</div>
        <svg viewBox="0 0 340 64" style="width:100%;height:64px">
          <polyline points="${pts}" fill="none" stroke="var(--color-brand-cobalt)" stroke-width="2" stroke-linejoin="round"/>
          ${last7.map((k, i) => `<circle cx="${20 + i * (300 / Math.max(1, last7.length - 1))}" cy="${58 - (usage.byDay[k].requests || 0) / maxReq * 46}" r="2.5" fill="var(--color-brand-cyan)"><title>${k}：${usage.byDay[k].requests || 0} 次</title></circle>`).join('')}
        </svg>
      </div>
      <div>
        <div class="text-[10px] text-text-tertiary mb-1.5">模型使用占比</div>
        <div class="flex items-center gap-3">
          <svg viewBox="0 0 60 60" style="width:60px;height:60px">${donut}</svg>
          <div class="text-[10px] text-text-tertiary space-y-0.5">
            ${modelNames.slice(0, 4).map((m, i) => `<div class="flex items-center gap-1"><span style="width:8px;height:8px;border-radius:2px;background:${COLORS[i % COLORS.length]};display:inline-block"></span>${escapeHtml(shortModelName(m))}</div>`).join('') || '暂无数据'}
          </div>
        </div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-4">
      <div>
        <div class="text-[10px] text-text-tertiary mb-1.5">近 7 天 token 消耗</div>
        <div class="flex items-end gap-1" style="height:52px">${bars || '<span class="text-[10px] text-text-tertiary">暂无数据</span>'}</div>
      </div>
      <div>
        <div class="text-[10px] text-text-tertiary mb-1.5">活跃时段（近7天 · 2小时粒度）</div>
        <div class="grid gap-px" style="grid-template-columns:repeat(12,1fr)">${heatCells.join('')}</div>
      </div>
    </div>`;
  if (anchor.nextSibling) container.insertBefore(card, anchor.nextSibling);
  else container.appendChild(card);
  refreshIcons();
  // 数字滚动动画
  [['insNum0', AppState.conversations.length, 'int'], ['insNum1', msgTotal, 'int'], ['insNum2', totalPrompt + totalCompletion, 'k'],
   ['insNum3', totalCost, 'cost'], ['insNum4', cacheStats.hitRate, 'pct'], ['insNum5', avgSpeed, 'int']]
    .forEach(([id, v, fmt]) => { const el = document.getElementById(id); if (el) countUp(el, v, fmt); });
}


/* ===================== [V3.2 审计修复] 模型选择器 / 同步滚动 / 文件夹上传 ===================== */

/** 安装输入台模型选择器（点击模型名弹出模型列表，选择后设为当前会话模型）
 * @returns {void} */
function installModelSelector() {
  const trigger = document.querySelector('#coreInputContainer .input-toolbar-model');
  if (!trigger || trigger.__modelSelBound) return;
  trigger.__modelSelBound = true;
  trigger.style.cursor = 'pointer';
  trigger.setAttribute('aria-label', '选择模型');
  trigger.title = '点击切换模型';
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const container = document.getElementById('coreInputContainer');
    let pop = document.getElementById('modelPickerPop');
    if (pop) { pop.remove(); return; } // 已开则关闭（切换）
    pop = document.createElement('div');
    pop.id = 'modelPickerPop';
    pop.className = 'model-popover active';
    pop.style.left = '12px';
    pop.style.bottom = 'calc(100% - 4px)';
    const models = (AI.models || []).map(m => m.name);
    // [模型同步] 弹层勾选与输入台标签同一数据源（currentConvModel）
    const current = (typeof currentConvModel === 'function') ? currentConvModel() : ((AppState.activeConv && AppState.activeConv.model) || MoraySettings.get('defaultModel') || models[0] || '');
    if (!models.length) {
      pop.innerHTML = '<div class="text-xs text-text-tertiary text-center py-4">未检测到模型。<br>请启动 Ollama 或在设置中配置云端 API。</div>';
    } else {
      pop.innerHTML = models.map(m => `
        <div class="model-pop-item ${m === current ? 'selected' : ''}" data-pick-model="${escapeHtml(m)}">
          <span class="truncate" title="${escapeHtml(m)}">${escapeHtml(uniqueModelName(m))}</span>
          ${m === current ? '<i data-lucide="check" class="w-3.5 h-3.5 text-brand-cobalt"></i>' : ''}
        </div>`).join('');
      pop.querySelectorAll('[data-pick-model]').forEach(item => {
        item.addEventListener('click', () => {
          const name = item.dataset.pickModel;
          if (AppState.activeConv) {
            AppState.activeConv.model = name;
            // [显示统一] 输入台手选 = 用户在界面手动锁定模型：必须置 userPickedModel=true，
            // 否则智能路由会把这次“手选”当默认值覆盖（此前正是气泡/输入台/状态栏三处漂移的根因之一）。
            AppState.activeConv.userPickedModel = true;
            DB.updateConversation(AppState.activeConv.id, { model: name, userPickedModel: true }).catch(() => {});
            renderSessionList();
          }
          MoraySettings.set('defaultModel', name);
          // [模型同步] 手选后统一入口刷新输入台标签 + 状态栏（单一权威）
          if (typeof setActiveModel === 'function') setActiveModel(name, 'user', '用户在输入台手动选择');
          else { if (typeof syncInputModelLabel === 'function') syncInputModelLabel(); updateStatusBar(); }
          pop.remove();
          showNotification('模型已切换', name, 'success', 1600);
        });
      });
    }
    container.appendChild(pop);
    refreshIcons();
    // 点击外部关闭
    const closer = (ev) => {
      if (!pop.contains(ev.target) && !trigger.contains(ev.target)) { pop.remove(); document.removeEventListener('click', closer); }
    };
    setTimeout(() => document.addEventListener('click', closer), 0);
  });
}

/** 对比模式真实同步滚动（开关打开时，双栏滚动互相联动）
 * @returns {void} */
function installCompareSyncScroll() {
  const root = document.getElementById('chat-compare');
  if (!root || root.__syncBound) return;
  root.__syncBound = true;
  const getCols = () => Array.from(root.querySelectorAll('.col-messages'));
  let syncing = false;
  root.querySelectorAll('.col-messages').forEach(col => {
    col.addEventListener('scroll', () => {
      const toggle = document.getElementById('syncScrollToggle');
      if (!toggle || !toggle.classList.contains('active') || syncing) return;
      syncing = true;
      const ratio = col.scrollTop / Math.max(1, col.scrollHeight - col.clientHeight);
      getCols().forEach(other => {
        if (other !== col) other.scrollTop = ratio * (other.scrollHeight - other.clientHeight);
      });
      requestAnimationFrame(() => { syncing = false; });
    }, { passive: true });
  });
}

/**
 * 递归遍历拖拽条目（支持文件夹），收集所有文件
 * @param {DataTransferItemList} items - 拖拽条目
 * @returns {Promise<File[]>} 文件列表 */
async function traverseDroppedEntries(items) {
  const files = [];
  const traverseEntry = async (entry, path) => {
    if (!entry) return;
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      try { Object.defineProperty(file, 'webkitRelativePath', { value: path + file.name }); } catch (e) { /* 忽略 */ }
      files.push(file);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const children = await new Promise((resolve) => {
        const all = [];
        const readBatch = () => reader.readEntries((batch) => {
          if (!batch.length) { resolve(all); return; }
          all.push(...batch);
          readBatch();
        }, () => resolve(all));
        readBatch();
      });
      for (const child of children) await traverseEntry(child, path + entry.name + '/');
    }
  };
  const entries = [];
  for (const item of Array.from(items || [])) {
    if (item.webkitGetAsEntry) {
      const entry = item.webkitGetAsEntry();
      if (entry) entries.push(entry);
    }
  }
  for (const entry of entries) await traverseEntry(entry, '');
  return files;
}


/* ===================== [V3.2 审计补全] 会话分组（原UI存在但函数缺失，整链路实现） ===================== */

/** 当前分组筛选（'all'=全部） */
let morayCurrentGroup = 'all';

/** 会话分组管理器 */
const SessionGroups = {
  /** 分组列表
   * @returns {Array<{id:string, name:string}>} 分组 */
  list() { return MoraySettings.get('sessionGroups') || []; },

  /** 创建分组
   * @param {string} name - 分组名
   * @returns {Promise<void>} */
  async create(name) {
    name = String(name || '').trim();
    if (!name) { showNotification('请输入分组名', '', 'warning', 1800); return; }
    const groups = this.list();
    if (groups.some(g => g.name === name)) { showNotification('分组已存在', name, 'warning', 1800); return; }
    groups.push({ id: 'grp_' + Date.now().toString(36), name });
    await MoraySettings.set('sessionGroups', groups);
    this.renderChips();
    showNotification('分组已创建', name, 'success', 1500);
  },

  /** 重命名分组
   * @param {string} gid - 分组ID
   * @param {string} name - 新名称
   * @returns {Promise<void>} */
  async rename(gid, name) {
    name = String(name || '').trim();
    if (!name) return;
    const groups = this.list();
    const g = groups.find(x => x.id === gid);
    if (g) { g.name = name; await MoraySettings.set('sessionGroups', groups); this.renderChips(); }
  },

  /** 删除分组（会话移回未分组）
   * @param {string} gid - 分组ID
   * @returns {Promise<void>} */
  async remove(gid) {
    const groups = this.list().filter(g => g.id !== gid);
    await MoraySettings.set('sessionGroups', groups);
    for (const c of AppState.conversations) {
      if (c.group === gid) { c.group = ''; DB.putConversation(c).catch(() => {}); }
    }
    if (morayCurrentGroup === gid) morayCurrentGroup = 'all';
    this.renderChips();
    renderSessionList();
  },

  /** 把会话移入分组
   * @param {string} convId - 会话ID
   * @param {string} gid - 分组ID（''=未分组）
   * @returns {Promise<void>} */
  async assign(convId, gid) {
    const conv = AppState.conversations.find(c => c.id === convId);
    if (!conv) return;
    conv.group = gid;
    await DB.updateConversation(convId, { group: gid });
    renderSessionList();
    const g = this.list().find(x => x.id === gid);
    showNotification('已移动', g ? '项目：' + g.name : '已移出项目', 'success', 1400);
  },

  /** 渲染分组 chips（全部 + 各分组 + 管理）
   * @returns {void} */
  renderChips() {
    const bar = document.getElementById('sessionGroupsBar');
    if (!bar) return;
    const groups = this.list();
    const chip = (gid, label, active, extra) =>
      `<span class="session-group-chip ${active ? 'active' : ''}" data-group="${gid}" onclick="filterByGroup('${gid}')" ${extra || ''}>${label}</span>`;
    let html = chip('all', '全部', morayCurrentGroup === 'all');
    groups.forEach(g => {
      html += chip(g.id, escapeHtml(g.name), morayCurrentGroup === g.id, `title="${escapeHtml(g.name)}（项目）"`);
    });
    html += `<span class="session-group-chip add-group" onclick="openGroupManager()" title="新建项目 / 管理项目">
      <i data-lucide="folder-plus" class="w-3 h-3"></i></span>`;
    bar.innerHTML = html;
    refreshIcons();
  }
};

/** 按分组筛选会话
 * @param {string} gid - 分组ID（'all'=全部）
 * @returns {void} */
function filterByGroup(gid) {
  morayCurrentGroup = gid;
  SessionGroups.renderChips();
  renderSessionList();
  showNotification('已筛选', gid === 'all' ? '显示全部会话' : '项目：' + (SessionGroups.list().find(g => g.id === gid) || {}).name || '', 'info', 1200);
}

/** 打开分组管理面板（创建/重命名/删除）
 * [修复] 输入框与「添加」按钮均以 box 作用域绑定（不再依赖全局 id / 内联 onclick），
 * 杜绝与页面其它同名 id 撞车导致取值错误。
 * @returns {void} */
function openGroupManager() {
  const groups = SessionGroups.list();
  const box = showModal(`
    <div class="form-row">
      <div class="flex gap-2">
        <input type="text" id="newGroupNameInput" placeholder="输入新项目名称..." class="form-input">
        <button class="btn-primary px-3 h-8 rounded-lg text-xs whitespace-nowrap" id="addGroupBtn">添加</button>
      </div>
    </div>
    <div class="space-y-1.5" id="groupListArea">
      ${groups.length ? groups.map(g => `
        <div class="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-panel/60">
          <i data-lucide="folder" class="w-3.5 h-3.5 text-brand-cobalt flex-shrink-0"></i>
          <input type="text" value="${escapeHtml(g.name)}" class="form-input" style="flex:1;padding:4px 8px;font-size:12px"
            onchange="renameSessionGroup('${g.id}', this.value)">
          <span class="icon-btn" style="cursor:pointer;color:var(--color-danger);font-size:12px" title="删除项目" onclick="deleteSessionGroup('${g.id}')">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </span>
        </div>`).join('') : '<div class="text-xs text-text-tertiary text-center py-4">还没有项目，输入名称创建一个</div>'}
    </div>`,
    { title: '新建项目（分组）', icon: 'folder-plus', footer: false });
  const foot = document.createElement('div');
  foot.style.cssText = 'display:flex;justify-content:flex-end;padding:14px 20px;border-top:1px solid var(--color-line-ghost)';
  foot.innerHTML = `<button class="px-3 h-8 rounded-lg text-xs text-text-tertiary hover:text-text-primary" onclick="closeGroupManager()">关闭</button>`;
  box.appendChild(foot);
  refreshIcons();
  // [修复] 作用域绑定：闭包直接读取本 box 内的输入框
  const input = box.querySelector('#newGroupNameInput');
  const addBtn = box.querySelector('#addGroupBtn');
  const createFromBox = () => {
    const name = input ? input.value.trim() : '';
    if (!name) { showNotification('请输入分组名', '', 'warning', 1800); return; }
    SessionGroups.create(name); // 内部处理重名提示与 chips 刷新
    if (input) input.value = '';
    renderGroupList();
  };
  const renderGroupList = () => {
    const area = box.querySelector('#groupListArea');
    if (!area) return;
    const list = SessionGroups.list();
    area.innerHTML = list.length ? list.map(g => `
      <div class="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-panel/60">
        <i data-lucide="folder" class="w-3.5 h-3.5 text-brand-cobalt flex-shrink-0"></i>
        <input type="text" value="${escapeHtml(g.name)}" class="form-input" style="flex:1;padding:4px 8px;font-size:12px"
          onchange="renameSessionGroup('${g.id}', this.value)">
        <span class="icon-btn" style="cursor:pointer;color:var(--color-danger);font-size:12px" title="删除项目" onclick="deleteSessionGroup('${g.id}')">
          <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
        </span>
      </div>`).join('') : '<div class="text-xs text-text-tertiary text-center py-4">还没有项目，输入名称创建一个</div>';
    refreshIcons();
  };
  if (addBtn) addBtn.addEventListener('click', createFromBox);
  if (input) {
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); createFromBox(); } });
    setTimeout(() => input.focus(), 50);
  }
}

/** 创建分组（兼容入口：命令面板等外部调用时从当前 modalBox 作用域取值，
 * 绝不使用全局 getElementById 撞静态节点）
 * @returns {Promise<void>} */
async function createSessionGroup() {
  const box = document.getElementById('modalBox');
  const input = box ? box.querySelector('#newGroupNameInput') : null;
  const name = input ? input.value : '';
  const r = await SessionGroups.create(name);
  if (r && input) input.value = '';
}

/** 重命名分组（内联onchange入口）
 * @param {string} gid - 分组ID
 * @param {string} name - 新名称
 * @returns {void} */
function renameSessionGroup(gid, name) { SessionGroups.rename(gid, name); }

/** 删除分组（内联onclick入口，带确认）
 * @param {string} gid - 分组ID
 * @returns {void} */
function deleteSessionGroup(gid) {
  const g = SessionGroups.list().find(x => x.id === gid);
  showConfirm('删除分组', `删除分组「${g ? g.name : ''}」？该分组下的会话将移回「全部」。`, () => SessionGroups.remove(gid), { danger: true, okText: '删除' });
}

/** 关闭分组管理面板
 * @returns {void} */
function closeGroupManager() { closeModal(); }

/** 显示"移动到分组"菜单（会话项悬浮按钮）
 * @param {Event} e - 点击事件
 * @param {HTMLElement} btn - 按钮
 * @returns {void} */
function showMoveToGroupMenu(e, btn) {
  e.stopPropagation();
  const item = btn.closest('.session-item');
  const convId = item ? item.dataset.convId : null;
  const groups = SessionGroups.list();
  const current = convId ? (AppState.conversations.find(c => c.id === convId) || {}).group : '';
  // 关闭旧菜单
  const old = document.getElementById('moveGroupMenu');
  if (old) { old.remove(); return; }
  const menu = document.createElement('div');
  menu.id = 'moveGroupMenu';
  menu.className = 'model-popover active';
  menu.style.cssText = 'position:fixed;z-index:80;width:180px;right:auto';
  const rect = btn.getBoundingClientRect();
  menu.style.left = Math.max(8, rect.left - 120) + 'px';
  menu.style.top = (rect.bottom + 6) + 'px';
  const row = (gid, label) => `<div class="model-pop-item ${current === gid ? 'selected' : ''}" data-move-to="${gid}">
    ${gid ? '<i data-lucide="folder" class="w-3 h-3"></i>' : '<i data-lucide="inbox" class="w-3 h-3"></i>'} ${escapeHtml(label)}
    ${current === gid ? '<i data-lucide="check" class="w-3 h-3 text-brand-cobalt"></i>' : ''}</div>`;
  menu.innerHTML = `<div class="text-[10px] text-text-tertiary px-3 py-1.5">移动到项目</div>
    ${row('', '未分组')}
    ${groups.map(g => row(g.id, g.name)).join('')}`;
  document.body.appendChild(menu);
  refreshIcons();
  menu.querySelectorAll('[data-move-to]').forEach(el => {
    el.addEventListener('click', () => {
      menu.remove();
      if (convId) SessionGroups.assign(convId, el.dataset.moveTo);
    });
  });
  const closer = (ev) => {
    if (!menu.contains(ev.target) && !btn.contains(ev.target)) { menu.remove(); document.removeEventListener('click', closer); }
  };
  setTimeout(() => document.addEventListener('click', closer), 0);
}


/* ===================== [成本中心 阶段五] Onboarding v2（演示友好三步向导） ===================== */

/** 新版三步引导：选后端 → 选模型与参数 → 选主题与示例包
 * @returns {void} */
Onboarding.openV2 = function () {
  let step = 0;
  let chosenBackend = 'offline';
  const box = showModal('<div id="ob2Body"></div>', { title: '欢迎使用 MoRay', icon: 'sparkles', footer: false });
  box.style.width = '600px';
  const render = () => {
    const body = box.querySelector('#ob2Body');
    const dots = [0, 1, 2].map(i => `<span class="ob-dot ${i === step ? 'active' : ''}"></span>`).join('');
    let inner = '';
    if (step === 0) {
      inner = `
        <div class="text-center mb-4"><div class="text-sm font-medium text-text-primary">选择你的 AI 后端</div>
        <div class="text-[11px] text-text-tertiary">可随时在设置中修改或同时使用多种</div></div>
        <div class="space-y-3">
          <div class="doc-card p-4" data-ob2-backend="cloud">
            <div class="flex items-center gap-2 mb-2">
              <i data-lucide="cloud" class="w-4 h-4 text-brand-violet"></i>
              <span class="text-sm text-text-primary">云端 API（示例：DeepSeek，价格按官方价估算）</span>
            </div>
            <div class="flex gap-2">
              <input type="text" id="ob2Base" class="form-input" value="https://api.deepseek.com/v1" style="flex:1">
              <input type="password" id="ob2Key" class="form-input" value="" placeholder="sk-...（选填）" style="flex:1">
              <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost whitespace-nowrap" id="ob2Test">测试连接</button>
            </div>
            <div class="text-[10px] text-text-tertiary mt-1.5" id="ob2CloudHint">填写 Key 后点击测试；也可稍后在设置页配置</div>
          </div>
          <div class="doc-card p-4" data-ob2-backend="ollama">
            <div class="flex items-center gap-2">
              <i data-lucide="hard-drive" class="w-4 h-4 text-brand-cobalt"></i>
              <span class="text-sm text-text-primary">本地 Ollama（免费 · 隐私最佳）</span>
              <span class="beacon-dot ${AI.health === 'ok' ? 'success' : ''}" id="ob2OllamaDot"></span>
              <span class="text-[10px] text-text-tertiary" id="ob2OllamaText">${AI.health === 'ok' ? '已连接（' + AI.models.length + ' 个模型）' : '未检测到，可稍后在对话页查看三步指引'}</span>
            </div>
          </div>
          <div class="doc-card p-4" data-ob2-backend="offline">
            <div class="flex items-center gap-2">
              <i data-lucide="eye-off" class="w-4 h-4 text-text-tertiary"></i>
              <span class="text-sm text-text-primary">先体验（离线探索）</span>
            </div>
          </div>
        </div>`;
    } else if (step === 1) {
      const models = AI.models.map(m => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)}</option>`).join('');
      inner = `
        <div class="text-center mb-4"><div class="text-sm font-medium text-text-primary">默认模型与生成参数</div>
        <div class="text-[11px] text-text-tertiary">之后可在设置中调整</div></div>
        <div class="space-y-3">
          <div>
            <label class="form-label">默认模型</label>
            <select id="ob2Model" class="form-select">${models || '<option value="">（暂无可用模型，可在设置中配置）</option>'}</select>
          </div>
          <div>
            <div class="flex justify-between text-xs mb-1.5"><span class="text-text-secondary">Temperature（创意↔精确）</span><span class="font-mono text-text-primary" id="ob2TempVal">0.70</span></div>
            <input type="range" min="0" max="2" step="0.05" value="0.7" id="ob2Temp" class="w-full accent-brand-cobalt">
          </div>
        </div>`;
    } else {
      inner = `
        <div class="text-center mb-4"><div class="text-sm font-medium text-text-primary">主题与示例包</div>
        <div class="text-[11px] text-text-tertiary">也可稍后在设置中更换</div></div>
        <div class="grid grid-cols-2 gap-3 mb-3">
          <div class="doc-card p-3 cursor-pointer text-center" data-ob2-theme="dark">
            <div class="h-14 rounded-lg mb-1 border border-line-ghost" style="background:linear-gradient(135deg,#0A0C14,#1A1F30)"></div>
            <span class="text-xs text-text-primary">深色</span>
          </div>
          <div class="doc-card p-3 cursor-pointer text-center" data-ob2-theme="light">
            <div class="h-14 rounded-lg mb-1 border border-line-ghost" style="background:linear-gradient(135deg,#F3F5FA,#FFFFFF)"></div>
            <span class="text-xs text-text-primary">浅色</span>
          </div>
        </div>
        <label class="flex items-center gap-2 text-xs text-text-secondary cursor-pointer rounded-lg bg-surface-panel/50 px-3 py-2.5">
          <input type="checkbox" id="ob2Sample" checked class="accent-brand-cobalt w-3.5 h-3.5">
          载入示例包（10 条精选提示词 + 6 个代码片段 + 1 个工作流，随时可一键清空）
        </label>`;
    }
    body.innerHTML = `
      <div class="flex justify-center gap-1.5 mb-4">${dots}</div>
      <div style="animation:modalIn 200ms ease-in-out">${inner}</div>
      <div class="flex justify-between items-center mt-5">
        <button class="text-[11px] text-text-tertiary hover:text-text-secondary" id="ob2Skip">跳过引导</button>
        <div class="flex gap-2">
          ${step > 0 ? '<button class="btn-ghost px-3 py-1.5 rounded-lg text-xs border border-line-ghost" id="ob2Prev">上一步</button>' : ''}
          <button class="btn-primary px-4 py-1.5 rounded-lg text-xs" id="ob2Next">${step === 2 ? '开始使用' : '下一步'}</button>
        </div>
      </div>`;
    refreshIcons();
    on(box.querySelector('#ob2Skip'), 'click', finish);
    const next = box.querySelector('#ob2Next');
    if (step === 0) {
      box.querySelectorAll('[data-ob2-backend]').forEach(el => el.addEventListener('click', () => {
        chosenBackend = el.dataset.ob2Backend;
        box.querySelectorAll('[data-ob2-backend]').forEach(x => x.style.borderColor = '');
        el.style.borderColor = 'var(--color-brand-cobalt)';
      }));
      const testBtn = box.querySelector('#ob2Test');
      if (testBtn) testBtn.addEventListener('click', async () => {
        const base = box.querySelector('#ob2Base').value.trim();
        const key = box.querySelector('#ob2Key').value.trim();
        await MoraySettings.set({ openaiBaseURL: base, openaiAPIKey: key, openaiEnabled: true, openaiModel: 'deepseek-chat' });
        const r = await testCloudConnectionDetailed();
        box.querySelector('#ob2CloudHint').innerHTML = r.ok
          ? `<span class="text-success">✓ 连接成功 · 延迟 ${r.latency}ms · ${escapeHtml(r.model || '')}</span>`
          : `<span class="text-danger">✗ ${escapeHtml(r.error)}</span> <span class="text-text-tertiary">${escapeHtml(r.advice || '')}</span>`;
      });
      next.addEventListener('click', () => { step = 1; render(); });
    } else if (step === 1) {
      const temp = box.querySelector('#ob2Temp');
      temp.addEventListener('input', () => box.querySelector('#ob2TempVal').textContent = parseFloat(temp.value).toFixed(2));
      next.addEventListener('click', () => {
        const model = box.querySelector('#ob2Model').value;
        if (model) MoraySettings.set('defaultModel', model);
        MoraySettings.set('temperature', parseFloat(temp.value));
        step = 2; render();
      });
    } else {
      box.querySelectorAll('[data-ob2-theme]').forEach(el => el.addEventListener('click', () => MoraySettings.set('theme', el.dataset.ob2Theme)));
      next.addEventListener('click', finish);
    }
    const prev = box.querySelector('#ob2Prev');
    if (prev) prev.addEventListener('click', () => { step = Math.max(0, step - 1); render(); });
  };
    const finish = async () => {
      Onboarding.done();
      closeModal();
      if (box.querySelector('#ob2Sample') && box.querySelector('#ob2Sample').checked) {
        const n = await loadSamplePack();
        showNotification('示例包已载入', n ? `共写入 ${n} 条示例数据` : '示例数据已存在', 'success', 3000);
      }
      // [阶段1.5 A] 启动摘要由 probeBackend 统一弹出（此处不再叠加“一切就绪”，避免启动连叠）
    };
  render();
};

/* ===================== [交互健壮性] 开发态交互自检（仅手动调用，不在生产环境自动弹错） ===================== */

/** 控制台交互自检：window.__morayAudit()
 * 遍历当前 DOM 中带 data-op / data-action / data-page(导航) / data-modal-close / data-cost-range 的元素，
 * 校验动作是否在分发表、目标视图是否存在；把"无处理器/目标缺失"以 console.warn 列表输出，
 * 正常则输出可交互元素总数。@returns {void} */
window.__morayAudit = function () {
  // data-action：全局动作（网关提示卡 / ⌘K 命令面板 executeCommand / 消息右键菜单 morayContextAction）
  const KNOWN_ACTIONS = ['goto-automation', 'export-data', 'toggle-theme', 'new-chat', 'toggle-compare',
    'goto-prompts', 'goto-snippets', 'goto-docs', 'goto-settings', 'switch-model',
    'copy', 'edit', 'regenerate', 'branch', 'delete'];
  const problems = [];
  let count = 0;
  const audit = (els, describe) => {
    els.forEach(el => {
      count++;
      const issue = describe(el);
      if (issue) problems.push({ el, text: (el.textContent || '').trim().slice(0, 30), issue });
    });
  };
  // data-op：按作用域区分分发表（消息气泡 / 会话列表 / 提示词·片段·对比卡片）
  const MSG_OPS = ['copy', 'regenerate', 'saveSnippet', 'star', 'quote', 'forward', 'like', 'dislike', 'branch', 'edit', 'delete'];
  const SESSION_OPS = ['move', 'pin', 'rename', 'delete'];
  const CARD_OPS = ['fav', 'use', 'copy', 'edit', 'delete', 'share', 'run', 'expand'];
  audit(document.querySelectorAll('[data-op]'), (el) => {
    const op = el.dataset.op;
    if (op.includes('$') || op.includes('+')) return null; // 动态拼接的 op（模板字符串），跳过静态校验
    const inSession = !!el.closest('.session-item');
    if (inSession) {
      if (!SESSION_OPS.includes(op)) return '未知 data-op="' + op + '"（不在会话操作分发表）';
      return null;
    }
    if (MSG_OPS.includes(op) || CARD_OPS.includes(op)) return null;
    return '未知 data-op="' + op + '"（不在任何操作分发表）';
  });
  // data-action：全局动作
  audit(document.querySelectorAll('[data-action]'), (el) => {
    const a = el.dataset.action;
    if (!KNOWN_ACTIONS.includes(a)) return '未知 data-action="' + a + '"（未注册监听）';
    return null;
  });
  // 导航按钮：目标视图面板存在
  audit(document.querySelectorAll('.nav-icon-btn[data-page]'), (el) => {
    const p = el.dataset.page;
    if (!document.getElementById('page-' + p)) return '导航目标 #page-' + p + ' 不存在';
    return null;
  });
  // 弹窗关闭
  audit(document.querySelectorAll('[data-modal-close], [data-modal-close2]'), () => {
    if (typeof closeModal !== 'function') return 'closeModal 函数不可用';
    return null;
  });
  // 成本中心时间范围
  audit(document.querySelectorAll('[data-cost-range]'), () => {
    if (typeof CostCenterApp === 'undefined') return 'CostCenterApp 未加载';
    return null;
  });
  // 会话/片段/工作流等容器上的动态 action（data-view 校验目标视图存在）
  audit(document.querySelectorAll('[data-view]'), (el) => {
    const v = el.dataset.view;
    if (v && !document.getElementById('page-' + v) && !document.getElementById(v)) return 'data-view="' + v + '" 目标不存在';
    return null;
  });
  if (problems.length) {
    console.warn('[MoRay 交互自检] 发现 ' + problems.length + ' 个问题：');
    problems.forEach((p, i) => {
      const sel = p.el.tagName.toLowerCase() + (p.el.id ? '#' + p.el.id : '') + (p.el.className && typeof p.el.className === 'string' ? '.' + p.el.className.split(' ').slice(0, 2).join('.') : '');
      console.warn('  ' + (i + 1) + ') [' + p.issue + '] 文字="' + p.text + '" 选择器=' + sel, p.el);
    });
  } else {
    console.log('[MoRay 交互自检] 交互自检通过，' + count + ' 个可交互元素');
  }
  return { count, problems };
};

/* ===================== [优化] 中间列表栏拖拽调宽（240–400px / 持久化 / 窄屏禁用） ===================== */
(function installSidebarResize() {
  var MIN_W = 240, MAX_W = 400, KEY = 'moray_sidebar_width';
  function applyWidth(aside, w) {
    if (window.innerWidth < 768) return;
    w = Math.max(MIN_W, Math.min(MAX_W, Math.round(w)));
    aside.style.width = w + 'px';
    aside.style.flexBasis = w + 'px';
  }
  function init() {
    var aside = document.getElementById('middleSidebar');
    if (!aside || aside.__morayResizable) return;
    aside.__morayResizable = true;
    if (getComputedStyle(aside).position === 'static') aside.style.position = 'relative';
    if (!document.getElementById('morayResizeStyle')) {
      var st = document.createElement('style'); st.id = 'morayResizeStyle';
      st.textContent =
        '.moray-resize-handle{position:absolute;top:0;right:-3px;width:7px;height:100%;cursor:col-resize;z-index:40;touch-action:none}' +
        '.moray-resize-handle::after{content:"";position:absolute;top:0;bottom:0;left:3px;width:1px;background:transparent;transition:background .15s}' +
        '.moray-resize-handle:hover::after,.moray-resize-handle.dragging::after{background:var(--color-brand-cobalt,#5B8CFF);box-shadow:0 0 8px rgba(91,140,255,.6)}' +
        'body.moray-resizing{cursor:col-resize!important;user-select:none!important}';
      document.head.appendChild(st);
    }
    var handle = document.createElement('div');
    handle.className = 'moray-resize-handle';
    handle.setAttribute('title', '拖拽调整侧栏宽度（240-400px）');
    aside.appendChild(handle);
    try { var saved = parseInt(localStorage.getItem(KEY), 10); if (saved >= MIN_W && saved <= MAX_W) applyWidth(aside, saved); } catch (e) {}
    var startX = 0, startW = 0, dragging = false;
    handle.addEventListener('pointerdown', function (e) {
      if (window.innerWidth < 768) return;
      dragging = true; startX = e.clientX; startW = aside.getBoundingClientRect().width;
      handle.classList.add('dragging'); document.body.classList.add('moray-resizing');
      try { handle.setPointerCapture(e.pointerId); } catch (err) {}
      e.preventDefault();
    });
    handle.addEventListener('pointermove', function (e) { if (dragging) applyWidth(aside, startW + (e.clientX - startX)); });
    function end() {
      if (!dragging) return; dragging = false;
      handle.classList.remove('dragging'); document.body.classList.remove('moray-resizing');
      try { localStorage.setItem(KEY, Math.round(aside.getBoundingClientRect().width)); } catch (e) {}
    }
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
    window.addEventListener('resize', function () { if (window.innerWidth < 768) { aside.style.width = ''; aside.style.flexBasis = ''; } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  setTimeout(init, 300);
})();

/* ===================== [壁纸贯穿] data-wallpaper 作用域 + 毛玻璃穿透 ===================== */
(function () {
  /* ---------- A. data-wallpaper 属性同步（包装静态骨架三函数 + 观察兜底） ---------- */
  const __WALL_LIST = ['aurora', 'deep-space', 'gradient', 'mountain', 'cyber', 'pure',
    'anime-starry', 'anime-aurora', 'anime-window'];
  /** 按当前状态把 data-wallpaper 同步到 #app（[壁纸修复] 以 moray_wallpaper 当前选中项为唯一权威）：
   * ① moray_wallpaper==='custom' 且 moray_custom_wallpaper 存在 → 'custom'
   * ② moray_wallpaper 命中 __WALL_LIST → 该内置名（即使 custom 数据残留也不受影响——保留它
   *    是为了能切回自定义，绝不压过明确的内置选择）
   * ③ moray_wallpaper 为空：存在自定义数据 → 回退 'custom'；否则默认 'anime-starry'
   * 顺带保证 #app 始终有壁纸类（首次加载即有壁纸背景）
   * @returns {void} */
  function __syncWallpaperAttr() {
    const shell = document.getElementById('appShell') || document.getElementById('app');
    if (!shell) return;
    let v = 'anime-starry'; // 默认壁纸（首次使用/无 saved 值）
    try {
      const saved = localStorage.getItem('moray_wallpaper');
      const custom = localStorage.getItem('moray_custom_wallpaper');
      if (saved === 'custom' && custom) v = 'custom';
      else if (saved && __WALL_LIST.includes(saved)) v = saved;
      else if (!saved && custom) v = 'custom';
    } catch (e) { /* 忽略 */ }
    shell.setAttribute('data-wallpaper', v);
    // [M5.4] 深色图片壁纸锁定深色外观：anime-*/custom 强制 dark（忽略浅色/跟随系统），
    // 切回其它壁纸时恢复用户原本的主题选择（不改动 MoraySettings，只动 html data-theme）
    const __IMG_WALL = ['anime-starry', 'anime-aurora', 'anime-window', 'custom'];
    if (__IMG_WALL.includes(v)) {
      if (document.documentElement.getAttribute('data-theme') !== 'dark') {
        window.__wallForcedDark = true;
        document.documentElement.setAttribute('data-theme', 'dark');
      }
    } else if (window.__wallForcedDark) {
      window.__wallForcedDark = false;
      // 恢复用户选择（MoraySettings.apply 按用户 theme 设置，auto 跟随系统）
      try { if (typeof MoraySettings !== 'undefined' && typeof MoraySettings.apply === 'function') MoraySettings.apply(); } catch (e) { /* 忽略 */ }
    }
    // 兜底默认壁纸类（内置壁纸时才加类；自定义壁纸走内联背景图）
    if (__WALL_LIST.includes(v)) {
      // [壁纸修复] 内置壁纸时清除内联背景残留（静态 load 的 custom 分支在 custom 数据存在时
      // 会先设内联图，若 saved 是内置则内联会盖过 CSS 背景 → 必须清掉）
      if (shell.style.backgroundImage) shell.style.backgroundImage = '';
      if (!shell.classList.contains('wallpaper-' + v)) {
        __WALL_LIST.forEach(w => shell.classList.remove('wallpaper-' + w));
        shell.classList.add('wallpaper-' + v);
      }
    }
  }
  // 包装静态骨架的 switchWallpaper / loadWallpaperFromStorage（原行为不变，只补属性；
  // 静态 switchWallpaper 已负责：清内联背景、移除/添加 wallpaper-* 类、写 moray_wallpaper）
  const __origSwitch = window.switchWallpaper;
  if (typeof __origSwitch === 'function') {
    window.switchWallpaper = function (name) {
      if (name === 'custom') {
        // [壁纸修复] 切回自定义：静态实现会清空内联背景，这里从 localStorage 恢复自定义图
        const data = (() => { try { return localStorage.getItem('moray_custom_wallpaper'); } catch (e) { return null; } })();
        const shell = document.getElementById('appShell') || document.getElementById('app');
        __origSwitch('custom');
        if (shell && data) {
          shell.style.backgroundImage = 'url(' + data + ')';
          shell.style.backgroundSize = 'cover';
          shell.style.backgroundPosition = 'center';
        }
        __syncWallpaperAttr();
        return;
      }
      __origSwitch(name);
      __syncWallpaperAttr();
    };
  }
  const __origLoad = window.loadWallpaperFromStorage;
  if (typeof __origLoad === 'function') {
    window.loadWallpaperFromStorage = function () { __origLoad(); __syncWallpaperAttr(); };
  }
  // 自定义上传：回调内异步落图（背景图=inline style），用 MutationObserver 兜底标记 custom；
  // [壁纸修复] 仅当"当前选中就是 custom 且内联背景存在"才设 custom，其余一律走修正后的
  // __syncWallpaperAttr——绝不把内置选择反向拉回 custom
  const __shell = document.getElementById('appShell') || document.getElementById('app');
  if (__shell && typeof MutationObserver === 'function') {
    new MutationObserver(() => {
      const savedNow = (() => { try { return localStorage.getItem('moray_wallpaper'); } catch (e) { return null; } })();
      if (savedNow === 'custom' && __shell.style.backgroundImage) {
        __shell.setAttribute('data-wallpaper', 'custom');
      } else {
        __syncWallpaperAttr();
      }
    }).observe(__shell, { attributes: true, attributeFilter: ['style'] });
  }
  __syncWallpaperAttr();

  /* ---------- B. 壁纸穿透/毛玻璃 CSS（集中一处；作用域全部挂在 #app[data-wallpaper] 下） ---------- */
  if (document.getElementById('wallpaperThroughStyle')) return;
  const st = document.createElement('style');
  st.id = 'wallpaperThroughStyle';
  st.textContent = `
/* ========== [壁纸贯穿/毛玻璃] 统一作用域 #app[data-wallpaper] ========== */
#app[data-wallpaper] {
  background-attachment: fixed; /* 壁纸固定全屏；移动端 fixed 失效时自然退化为 cover 铺满 */
  background-size: cover;
  background-position: center;
  /* 统一变量，便于回调：面板透明度 / 毛玻璃模糊 / 压暗层 / 内部卡片透明度 / 浮层不透明度 */
  --wall-panel-alpha: 0.62;
  --wall-blur: 10px;
  --wall-scrim: rgba(6, 8, 14, 0.30);
  --wall-card-alpha: 0.45;
  --wall-floating-alpha: 0.90;
  /* [3.20 阶段1] 联动光晕色（默认 = 品牌钴蓝/青），由 data-wall-tone 分档覆盖；
     组件规则以 rgba(var(--wall-glow-rgb), α) 消费，未分档时回退本默认值 */
  --wall-glow-rgb: 91, 140, 255;
  --wall-accent-rgb: 58, 214, 232;
  --wall-tint-color: rgb(91, 140, 255);
}
/* ---- 三栏骨架：唯一 backdrop-filter 层（内部卡片/气泡不再 blur，防嵌套掉帧） ---- */
#app[data-wallpaper] aside.bg-surface-deep {
  background-color: rgba(16, 19, 29, var(--wall-panel-alpha));
  backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  -webkit-backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  box-shadow: inset 0 0 0 9999px var(--wall-scrim); /* 淡暗色压暗层：壁纸最亮时文字仍可读 */
}
#app[data-wallpaper] #middleSidebar {
  background-color: rgba(21, 25, 39, var(--wall-panel-alpha));
  backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  -webkit-backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  box-shadow: inset 0 0 0 9999px var(--wall-scrim);
}
#app[data-wallpaper] main.bg-surface-void {
  background-color: rgba(10, 12, 20, var(--wall-panel-alpha));
  backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  -webkit-backdrop-filter: blur(var(--wall-blur)) saturate(1.1);
  box-shadow: inset 0 0 0 9999px var(--wall-scrim);
}
/* ---- 三栏内部卡片/气泡/输入台：更透明的纯色，取消嵌套 blur（性能红线） ---- */
#app[data-wallpaper] main .glass-card {
  background: rgba(26, 31, 48, var(--wall-card-alpha));
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
#app[data-wallpaper] main .bg-surface-card {
  background-color: rgba(26, 31, 48, var(--wall-card-alpha));
}
#app[data-wallpaper] #coreInputContainer {
  background: rgba(15, 18, 28, var(--wall-card-alpha));
}
/* ---- 浮层（modal/命令面板/右键/通知/浮出菜单）：高不透明 + blur，文字不被壁纸干扰。
     浮层位于 body 直属（#app 之外），故用全局规则 + 浅色主题单独覆盖 ---- */
.modal-box,
.command-palette,
.context-menu,
.notification,
.surface-floating,
.slash-menu {
  background-color: rgba(21, 25, 39, var(--wall-floating-alpha, 0.90));
  backdrop-filter: blur(18px) saturate(1.1);
  -webkit-backdrop-filter: blur(18px) saturate(1.1);
}
html[data-theme="light"] .modal-box,
html[data-theme="light"] .command-palette,
html[data-theme="light"] .context-menu,
html[data-theme="light"] .notification,
html[data-theme="light"] .surface-floating,
html[data-theme="light"] .slash-menu {
  background-color: rgba(255, 255, 255, 0.92);
}
/* ---- pure 纯黑：面板接近不透明，与旧观感一致 ---- */
#app[data-wallpaper="pure"] {
  --wall-panel-alpha: 0.96;
  --wall-card-alpha: 0.94;
  --wall-scrim: rgba(6, 8, 14, 0.02);
}
/* ---- [动漫图片壁纸] 内置三张 + 自定义上传：图片类壁纸专用穿透参数（人物不被默认遮罩压暗） ---- */
#app[data-wallpaper="anime-starry"] {
  background-image: url("wallpapers/wp1_starry.jpg");
  background-size: cover;
  background-position: center;
  background-attachment: fixed;
}
#app[data-wallpaper="anime-aurora"] {
  background-image: url("wallpapers/wp2_aurora.jpg");
  background-size: cover;
  background-position: center;
  background-attachment: fixed;
}
#app[data-wallpaper="anime-window"] {
  background-image: url("wallpapers/wp3_window.jpg");
  background-size: cover;
  background-position: center;
  background-attachment: fixed;
}
/* 设置页缩略图：类选择器（与 #app 属性选择器并存，缩略图 div 复用背景） */
.wallpaper-option.wallpaper-anime-starry { background-image: url("wallpapers/wp1_starry.jpg"); background-size: cover; background-position: center; }
.wallpaper-option.wallpaper-anime-aurora { background-image: url("wallpapers/wp2_aurora.jpg"); background-size: cover; background-position: center; }
.wallpaper-option.wallpaper-anime-window { background-image: url("wallpapers/wp3_window.jpg"); background-size: cover; background-position: center; }
#app[data-wallpaper="anime-starry"],
#app[data-wallpaper="anime-aurora"],
#app[data-wallpaper="anime-window"],
#app[data-wallpaper="custom"] {
  --wall-panel-alpha: 0.32;
  --wall-blur: 4px;
  --wall-scrim: rgba(6, 8, 14, 0.08);
  --wall-card-alpha: 0.62;
}
/* ---- 浅色主题：浅色半透明值（壁纸仍可透出，对比度正常） ---- */
html[data-theme="light"] #app[data-wallpaper] {
  --wall-scrim: rgba(255, 255, 255, 0.22);
}
html[data-theme="light"] #app[data-wallpaper] aside.bg-surface-deep {
  background-color: rgba(233, 237, 245, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper] #middleSidebar {
  background-color: rgba(255, 255, 255, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper] main.bg-surface-void {
  background-color: rgba(243, 245, 250, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper] main .glass-card,
html[data-theme="light"] #app[data-wallpaper] main .bg-surface-card {
  background-color: rgba(255, 255, 255, var(--wall-card-alpha));
}
html[data-theme="light"] #app[data-wallpaper] #coreInputContainer {
  background: rgba(255, 255, 255, var(--wall-card-alpha));
}
/* ---- [M5.2] 深色图片壁纸（anime 系列与 custom）在浅色主题下保持深色半透：
 * 浅色规则会给面板套白色半透+白色 scrim，叠在深色动漫图上形成白雾；
 * 此处为深色图片壁纸单独恢复深色面板值（三栏深色底 + 深色 scrim + 深色卡片），
 * 任何主题下都深邃不发灰；渐变壁纸（含 pure）的浅色覆盖不受影响。 ---- */
html[data-theme="light"] #app[data-wallpaper="anime-starry"],
html[data-theme="light"] #app[data-wallpaper="anime-aurora"],
html[data-theme="light"] #app[data-wallpaper="anime-window"],
html[data-theme="light"] #app[data-wallpaper="custom"] {
  --wall-scrim: rgba(6, 8, 14, 0.08);
  --wall-panel-alpha: 0.32;
  --wall-blur: 4px;
  --wall-card-alpha: 0.62;
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] aside.bg-surface-deep,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] aside.bg-surface-deep,
html[data-theme="light"] #app[data-wallpaper="anime-window"] aside.bg-surface-deep,
html[data-theme="light"] #app[data-wallpaper="custom"] aside.bg-surface-deep {
  background-color: rgba(16, 19, 29, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #middleSidebar,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #middleSidebar,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #middleSidebar,
html[data-theme="light"] #app[data-wallpaper="custom"] #middleSidebar {
  background-color: rgba(21, 25, 39, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] main.bg-surface-void,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] main.bg-surface-void,
html[data-theme="light"] #app[data-wallpaper="anime-window"] main.bg-surface-void,
html[data-theme="light"] #app[data-wallpaper="custom"] main.bg-surface-void {
  background-color: rgba(10, 12, 20, var(--wall-panel-alpha));
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] main .glass-card,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] main .glass-card,
html[data-theme="light"] #app[data-wallpaper="anime-window"] main .glass-card,
html[data-theme="light"] #app[data-wallpaper="custom"] main .glass-card,
html[data-theme="light"] #app[data-wallpaper="anime-starry"] main .bg-surface-card,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] main .bg-surface-card,
html[data-theme="light"] #app[data-wallpaper="anime-window"] main .bg-surface-card,
html[data-theme="light"] #app[data-wallpaper="custom"] main .bg-surface-card {
  background-color: rgba(26, 31, 48, var(--wall-card-alpha));
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="custom"] #coreInputContainer {
  background: rgba(15, 18, 28, var(--wall-card-alpha));
}
/* ---- [M5.3] 深色图片壁纸下消息气泡对比度：气泡比普通卡片更实，正文显式亮色。
 * 仅 anime 系列与 custom 作用域生效；其它内置壁纸与无壁纸状态保持原样。 ---- */
#app[data-wallpaper="anime-starry"] main .msg-bubble__user-box,
#app[data-wallpaper="anime-aurora"] main .msg-bubble__user-box,
#app[data-wallpaper="anime-window"] main .msg-bubble__user-box,
#app[data-wallpaper="custom"] main .msg-bubble__user-box {
  background-color: rgba(64, 104, 205, 0.42);
  border-color: rgba(130, 165, 255, 0.40);
}
#app[data-wallpaper="anime-starry"] main .msg-bubble__ai-box,
#app[data-wallpaper="anime-aurora"] main .msg-bubble__ai-box,
#app[data-wallpaper="anime-window"] main .msg-bubble__ai-box,
#app[data-wallpaper="custom"] main .msg-bubble__ai-box {
  background-color: rgba(18, 22, 36, 0.82);
}
#app[data-wallpaper="anime-starry"] .msg-bubble .md-body,
#app[data-wallpaper="anime-aurora"] .msg-bubble .md-body,
#app[data-wallpaper="anime-window"] .msg-bubble .md-body,
#app[data-wallpaper="custom"] .msg-bubble .md-body {
  color: var(--color-text-primary);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.35);
}
/* 浅色主题下图片壁纸气泡：浅衬底 + 深字（--color-text-primary 浅色主题为深色，自动适配） */
html[data-theme="light"] #app[data-wallpaper="anime-starry"] main .msg-bubble__user-box,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] main .msg-bubble__user-box,
html[data-theme="light"] #app[data-wallpaper="anime-window"] main .msg-bubble__user-box,
html[data-theme="light"] #app[data-wallpaper="custom"] main .msg-bubble__user-box {
  background-color: rgba(214, 225, 255, 0.88);
  border-color: rgba(91, 140, 255, 0.45);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] main .msg-bubble__ai-box,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] main .msg-bubble__ai-box,
html[data-theme="light"] #app[data-wallpaper="anime-window"] main .msg-bubble__ai-box,
html[data-theme="light"] #app[data-wallpaper="custom"] main .msg-bubble__ai-box {
  background-color: rgba(255, 255, 255, 0.88);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] .msg-bubble .md-body,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] .msg-bubble .md-body,
html[data-theme="light"] #app[data-wallpaper="anime-window"] .msg-bubble .md-body,
html[data-theme="light"] #app[data-wallpaper="custom"] .msg-bubble .md-body {
  color: var(--color-text-primary);
  text-shadow: none;
}
/* ---- [M5.4] 深色图片壁纸下光核输入台：加深浮起 + 输入框衬底 + 图标提亮 ----
 * 仅 anime 系列与 custom 作用域生效；其它壁纸/无壁纸/纯黑保持原样。 ---- */
#app[data-wallpaper="anime-starry"] #coreInputContainer,
#app[data-wallpaper="anime-aurora"] #coreInputContainer,
#app[data-wallpaper="anime-window"] #coreInputContainer,
#app[data-wallpaper="custom"] #coreInputContainer {
  background-color: rgba(12, 15, 25, 0.90);
  border: 1px solid rgba(var(--wall-glow-rgb, 120, 150, 255), 0.24);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
}
/* 聚焦时边框过渡到壁纸主色光晕（原为品牌蓝；--wall-glow-rgb 未定义时回退同色） */
#app[data-wallpaper="anime-starry"] #coreInputContainer:focus-within,
#app[data-wallpaper="anime-aurora"] #coreInputContainer:focus-within,
#app[data-wallpaper="anime-window"] #coreInputContainer:focus-within,
#app[data-wallpaper="custom"] #coreInputContainer:focus-within {
  border-color: rgba(var(--wall-glow-rgb, 91, 140, 255), 0.90);
  box-shadow: 0 0 0 1px rgba(var(--wall-glow-rgb, 91, 140, 255), 0.35), 0 0 28px rgba(var(--wall-glow-rgb, 91, 140, 255), 0.12), 0 10px 30px rgba(0, 0, 0, 0.45);
}
/* 输入框：极轻衬底 + 圆角；正文保持主题色；placeholder 提亮 */
#app[data-wallpaper="anime-starry"] #chatInputNormal,
#app[data-wallpaper="anime-aurora"] #chatInputNormal,
#app[data-wallpaper="anime-window"] #chatInputNormal,
#app[data-wallpaper="custom"] #chatInputNormal {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 10px;
  color: var(--color-text-primary);
}
#app[data-wallpaper="anime-starry"] #chatInputNormal::placeholder,
#app[data-wallpaper="anime-aurora"] #chatInputNormal::placeholder,
#app[data-wallpaper="anime-window"] #chatInputNormal::placeholder,
#app[data-wallpaper="custom"] #chatInputNormal::placeholder {
  color: rgba(186, 200, 225, 0.72);
}
/* 工具图标提亮（hover 更亮）；模型名保持主题色；发送按钮品牌蓝不变 */
#app[data-wallpaper="anime-starry"] #coreInputContainer .input-toolbar-btn,
#app[data-wallpaper="anime-aurora"] #coreInputContainer .input-toolbar-btn,
#app[data-wallpaper="anime-window"] #coreInputContainer .input-toolbar-btn,
#app[data-wallpaper="custom"] #coreInputContainer .input-toolbar-btn {
  color: #AEB9CE;
}
#app[data-wallpaper="anime-starry"] #coreInputContainer .input-toolbar-btn:hover,
#app[data-wallpaper="anime-aurora"] #coreInputContainer .input-toolbar-btn:hover,
#app[data-wallpaper="anime-window"] #coreInputContainer .input-toolbar-btn:hover,
#app[data-wallpaper="custom"] #coreInputContainer .input-toolbar-btn:hover {
  color: #E8ECF5;
}
/* 浅色主题兜底（联动强制深色为主，此处保证万一浅色时也可读） */
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="custom"] #coreInputContainer {
  background-color: rgba(255, 255, 255, 0.90);
  border: 1px solid rgba(var(--wall-glow-rgb, 91, 140, 255), 0.30);
  box-shadow: 0 10px 30px rgba(26, 34, 51, 0.18);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #chatInputNormal,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #chatInputNormal,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #chatInputNormal,
html[data-theme="light"] #app[data-wallpaper="custom"] #chatInputNormal {
  background: rgba(10, 14, 22, 0.05);
  color: var(--color-text-primary);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #chatInputNormal::placeholder,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #chatInputNormal::placeholder,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #chatInputNormal::placeholder,
html[data-theme="light"] #app[data-wallpaper="custom"] #chatInputNormal::placeholder {
  color: rgba(86, 96, 118, 0.75);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #coreInputContainer .input-toolbar-btn,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #coreInputContainer .input-toolbar-btn,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #coreInputContainer .input-toolbar-btn,
html[data-theme="light"] #app[data-wallpaper="custom"] #coreInputContainer .input-toolbar-btn {
  color: #566076;
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #coreInputContainer .input-toolbar-btn:hover,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #coreInputContainer .input-toolbar-btn:hover,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #coreInputContainer .input-toolbar-btn:hover,
html[data-theme="light"] #app[data-wallpaper="custom"] #coreInputContainer .input-toolbar-btn:hover {
  color: #1A2233;
}
`;
  document.head.appendChild(st);
})();

/* ===================== [M5.1] 布局：欢迎区底部预留输入台高度 ===================== */
(function () {
  if (document.getElementById('layoutFixStyle')) return;
  const st = document.createElement('style');
  st.id = 'layoutFixStyle';
  st.textContent = `
/* [M5.1] 欢迎区/消息滚动容器底部预留"输入台高度+安全间距"，1280×720 不被遮挡 */
.welcome-screen { padding-bottom: calc(40px + 96px); }
#chatMessagesNormal { padding-bottom: 96px; box-sizing: border-box; }
/* 视口 ≤720px：压缩欢迎区上下间距与快捷卡片 */
@media (max-height: 720px) {
  .welcome-screen { padding-top: 14px; padding-bottom: calc(14px + 92px); justify-content: flex-start; }
  .welcome-logo { width: 52px; height: 52px; margin-bottom: 10px; }
  .welcome-title { font-size: 20px; }
  .welcome-subtitle { margin-top: 4px; font-size: 12px; }
  .welcome-suggestions { gap: 8px; margin-top: 12px; }
}
`;
  document.head.appendChild(st);
})();

/* ===================== [里程碑M1/M5] 本地薄后端探测 ===================== * 页面启动后静默请求 /api/health（AbortController 2s 超时）；
 * 成功 → window.MorayBackend.connected=true 并刷新底部状态栏"本地后端 ● 已连接"；
 * 失败/超时 → connected=false 显示"本地后端 ○ 离线模式"；全程静默，绝不弹错误。
 * [M5修复] 后端地址统一解析（全局唯一事实源，其他模块经 MorayBackend.origin 复用）。 */

/** [M5.1] 前端单一版本常量（与后端 /api/health version 保持一致，见 server/app/config.py）；
 * MORAY_VERSION = 产品版本（对外）；MORAY_BUILD = 内部构建号（对应 CHANGELOG 迭代序号） */
window.MORAY_VERSION = '1.0.0';
window.MORAY_BUILD = '3.20.1';

(function () {
  /** [M5修复] 统一后端 origin 解析，优先级从高到低：
   * ① URL 查询参数 ?backend=（支持端口或完整 origin）
   * ② localStorage moray_backend_origin（用户手动配置）
   * ③ location.protocol 为 http/https 时 → location.origin（同源，一键脚本换任意端口自动配对）
   * ④ 仅 file:// 直开 → 回退 http://127.0.0.1:8000
   * @returns {string} 后端 origin（无尾斜杠） */
  function resolveBackendOrigin() {
    try {
      const p = new URLSearchParams(location.search).get('backend');
      if (p && String(p).trim()) {
        const v = String(p).trim();
        if (/^https?:\/\//.test(v)) return v.replace(/\/+$/, '');
        const portOnly = v.replace(/[^0-9]/g, '');
        return 'http://127.0.0.1:' + (portOnly || '8000');
      }
      const manual = (() => { try { return localStorage.getItem('moray_backend_origin'); } catch (e) { return null; } })();
      if (manual && /^https?:\/\//.test(manual)) return manual.replace(/\/+$/, '');
      if (location.protocol === 'http:' || location.protocol === 'https:') return location.origin;
    } catch (e) { /* 回退 */ }
    return 'http://127.0.0.1:8000';
  }

  window.resolveBackendOrigin = resolveBackendOrigin;
  const BACKEND_ORIGIN = resolveBackendOrigin();
  const HEALTH_URL = BACKEND_ORIGIN + '/api/health';
  window.MorayBackend = { connected: false, checked: false, url: HEALTH_URL, origin: BACKEND_ORIGIN };

  /** [M5] 底部状态栏版本号统一为 MORAY_VERSION（静态骨架写死 v2.0，此处运行期替换） */
  function syncVersionLabel() {
    try {
      const items = document.querySelectorAll('.mini-statusbar .status-item span');
      items.forEach(s => {
        if (s.textContent && s.textContent.trim().indexOf('MoRay v') === 0) {
          s.textContent = 'MoRay v' + window.MORAY_VERSION;
        }
      });
    } catch (e) { /* 忽略 */ }
  }

  /** 按探测结果刷新状态栏后端状态项（只动本项，不影响其它状态项） */
  function refreshBackendStatus() {
    const item = document.getElementById('statusBackendItem');
    const label = item ? item.querySelector('.backend-status-text') : null;
    if (!item || !label) return;
    const ok = !!(window.MorayBackend && window.MorayBackend.connected);
    const dot = item.querySelector('.beacon-dot');
    if (ok) {
      item.title = '本地后端已连接：' + HEALTH_URL;
      if (dot) dot.style.background = 'var(--color-success)';
      label.textContent = '本地后端 ● 已连接';
      label.style.color = 'var(--color-success)';
    } else {
      item.title = '本地同步后端未启动：当前用浏览器存储，Ollama 本地对话不受影响；启动后端可多设备同步';
      if (dot) dot.style.background = 'var(--color-text-tertiary)';
      label.textContent = '本地后端未启动';
      label.style.color = 'var(--color-text-tertiary)';
    }
  }

  /** 向底部状态栏插入后端状态项（仅一次；后端项不参与其它状态逻辑） */
  function installBackendStatusItem() {
    const bar = document.querySelector('.mini-statusbar');
    if (!bar || document.getElementById('statusBackendItem')) return;
    const group = bar.querySelector('.status-group');
    if (!group) return;
    const item = document.createElement('div');
    item.className = 'status-item';
    item.id = 'statusBackendItem';
    item.title = '本地后端连接状态（探测中...）';
    item.innerHTML = '<span class="beacon-dot" style="width:6px;height:6px;background:var(--color-text-tertiary)"></span>' +
      '<span class="backend-status-text" style="color:var(--color-text-tertiary)">本地后端未启动</span>';
    group.appendChild(item);
  }

  /** 静默探测：超时/异常全部捕获，只更新 MorayBackend 与状态栏。
   * 每次带随机 query 参数，彻底绕开浏览器/SW 对 /api/health 的缓存（避免连到旧响应误判已连接） */
  async function probeBackend() {
    try {
      installBackendStatusItem();
      syncVersionLabel();
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2000);
      const url = HEALTH_URL + (HEALTH_URL.includes('?') ? '&' : '?') + 't=' + Date.now();
      const res = await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (res.ok) {
        const h = await res.json().catch(() => null);
        window.MorayBackend = (h && h.ok === true)
          ? Object.assign({ connected: true, checked: true, url: HEALTH_URL, origin: BACKEND_ORIGIN }, h)
          : { connected: false, checked: true, url: HEALTH_URL, origin: BACKEND_ORIGIN };
      } else {
        window.MorayBackend = { connected: false, checked: true, url: HEALTH_URL, origin: BACKEND_ORIGIN };
      }
    } catch (e) { /* 静默：离线/超时/网络错误均不打扰用户 */ window.MorayBackend = { connected: false, checked: true, url: HEALTH_URL, origin: BACKEND_ORIGIN }; }
    refreshBackendStatus();
    // [阶段1.5 A] 启动通知合并为唯一一条状态摘要（消除“已就绪/持久化/纯前端”三连叠）：
    // 后端连接状态 + Ollama 模型数一并在摘要中；纯前端提示并入同一句话（不再单独弹）
    try {
      const ok = !!(window.MorayBackend && window.MorayBackend.connected);
      const modelN = (window.AI && Array.isArray(window.AI.models)) ? window.AI.models.length : null;
      const msg = ok
        ? ('本地后端已连接：' + BACKEND_ORIGIN + (modelN != null ? ' · 本地模型 ' + modelN + ' 个' : ''))
        : '本地同步后端未启动：当前用浏览器存储，Ollama 本地对话不受影响；启动后端可多设备同步';
      showNotification('MoRay v' + window.MORAY_VERSION + ' 已就绪', msg, ok ? 'success' : 'info', 5000);
    } catch (e) { /* 忽略 */ }
    // [M2] 通知设置页等模块刷新"代理/直连"通道 UI
    try { window.dispatchEvent(new CustomEvent('moray-backend-probed')); } catch (e) { /* 忽略 */ }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', probeBackend);
  else probeBackend();
})();

/* ===================== [3.20 阶段1] 壁纸联动系统主题 =====================
 * 目标：换壁纸不止换背景 —— 按壁纸主色联动整个 UI 的光晕/强调色相、面板与卡片叠色、
 * 以及纯色/渐变类壁纸的面板透明度；自定义上传图用 Canvas 取主色自动分档。
 *
 * 实现边界（只加不改）：
 *  - 只向 #app 写三个新属性：data-wall-tone（色相档）/ data-wall-tint（叠色开关）/
 *    data-wall-bright（浅色图标记），不改任何现有类名、DOM 结构与函数签名；
 *  - 光晕色相通过 110_polish 的壁纸规则里新增的 --wall-glow-rgb / --wall-accent-rgb /
 *    --wall-tint-color 三个变量消费，未分档时回退品牌钴蓝（视觉零回归）；
 *  - 样式表独立成 <style id="wallpaperLinkStyle">，位置在 wallpaperThroughStyle 之后，
 *    同权重规则「后定义获胜」，因此无需改动既有壁纸规则即可叠加联动。
 * 可读性红线：正文对比度不低于现状——浅色（高亮度）自定义图自动加深遮罩（data-wall-bright）。
 */
(function () {
  /** 色相档 → 联动取值。
   *  glow/accent：RGB 三元组，供 rgba(var(--x), α) 消费（光晕、边框、焦点环、当前项）；
   *  scrim       ：按色相着色的压暗层（纯色/渐变类壁纸）——比默认 rgba(6,8,14,.30) 更深，
   *                所以"面板色相随壁纸变"的同时正文对比度只会更高，不会更低（可读性红线）；
   *  scrimImg    ：图片壁纸（动漫/自定义）用的浅版着色压暗层，只微微加深，保持"不压暗人物"；
   *  scrimLight  ：浅色主题下的着色薄纱（亮度与既有白色薄纱相当，只带色相）。 */
  const WALL_TONES = {
    cobalt: {
      glow: '91, 140, 255', accent: '157, 123, 255',
      scrim: 'rgba(4, 9, 22, 0.36)', scrimImg: 'rgba(4, 10, 22, 0.12)', scrimLight: 'rgba(240, 244, 255, 0.24)'
    },
    cyan: {
      glow: '58, 214, 232', accent: '91, 140, 255',
      scrim: 'rgba(2, 12, 15, 0.38)', scrimImg: 'rgba(4, 12, 15, 0.12)', scrimLight: 'rgba(238, 252, 255, 0.24)'
    },
    violet: {
      glow: '157, 123, 255', accent: '110, 140, 255',
      scrim: 'rgba(11, 6, 22, 0.38)', scrimImg: 'rgba(10, 7, 22, 0.12)', scrimLight: 'rgba(246, 240, 255, 0.24)'
    },
    magenta: {
      glow: '214, 108, 214', accent: '157, 123, 255',
      scrim: 'rgba(16, 6, 15, 0.38)', scrimImg: 'rgba(14, 6, 15, 0.12)', scrimLight: 'rgba(255, 240, 252, 0.24)'
    },
    green: {
      glow: '86, 214, 160', accent: '58, 214, 232',
      scrim: 'rgba(2, 13, 9, 0.38)', scrimImg: 'rgba(3, 12, 9, 0.12)', scrimLight: 'rgba(238, 255, 246, 0.24)'
    },
    warm: {
      glow: '232, 176, 92', accent: '232, 130, 110',
      scrim: 'rgba(18, 11, 4, 0.38)', scrimImg: 'rgba(16, 11, 5, 0.12)', scrimLight: 'rgba(255, 248, 238, 0.24)'
    },
    neutral: {
      glow: '138, 150, 172', accent: '110, 124, 150',
      // 中性档（pure 等）：保持既有近乎不压暗的观感，不做着色
      scrim: 'rgba(6, 8, 14, 0.02)', scrimImg: 'rgba(6, 8, 14, 0.08)', scrimLight: 'rgba(255, 255, 255, 0.22)'
    }
  };

  /** 内置壁纸联动档案：tone = 光晕色相档；panel/card = 面板透明度微调（null = 保持既有值，
   *  避免压暗动漫人物）。压暗层色相由 tone 决定，不在此处重复声明。 */
  const WALL_PROFILE = {
    aurora: { tone: 'cyan', panel: 0.60, card: 0.44 },
    'deep-space': { tone: 'cobalt', panel: 0.66, card: 0.46 },
    gradient: { tone: 'cobalt', panel: 0.62, card: 0.45 },
    mountain: { tone: 'violet', panel: 0.58, card: 0.44 },
    cyber: { tone: 'cyan', panel: 0.60, card: 0.44 },
    pure: { tone: 'neutral', panel: 0.96, card: 0.94 },
    'anime-starry': { tone: 'cyan', panel: null, card: null },
    'anime-aurora': { tone: 'green', panel: null, card: null },
    'anime-window': { tone: 'warm', panel: null, card: null }
  };

  /** 内置壁纸清单（与上方 __WALL_LIST 同序，用于判断是否为内置壁纸） */
  const WALL_BUILTIN = ['aurora', 'deep-space', 'gradient', 'mountain', 'cyber', 'pure',
    'anime-starry', 'anime-aurora', 'anime-window'];

  const CUSTOM_TONE_KEY = 'moray_custom_wall_tone';

  /** 当前 #app 元素（与壁纸属性同步逻辑同源） */
  function wallShell() {
    return document.getElementById('appShell') || document.getElementById('app');
  }

  /** 读取当前壁纸 id（权威来源与 __syncWallpaperAttr 一致：localStorage） */
  function wallCurrentId() {
    try {
      const saved = localStorage.getItem('moray_wallpaper');
      const custom = localStorage.getItem('moray_custom_wallpaper');
      if (saved === 'custom' && custom) return 'custom';
      if (saved && WALL_BUILTIN.includes(saved)) return saved;
      if (!saved && custom) return 'custom';
    } catch (e) { /* 忽略 */ }
    return 'anime-starry';
  }

  /** 字符串指纹（用于自定义图取色缓存命中判断；改图即换键） */
  function wallKeyOf(s) {
    const t = s.length > 4096 ? (s.slice(0, 2048) + s.slice(-2048)) : s;
    let h = 2166136261;
    for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return h.toString(36) + ':' + s.length;
  }

  /** RGB → 色相档（饱和度/明度过低判为中性档） */
  function wallToneFromRgb(r, g, b) {
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    const dlt = mx - mn;
    const sat = mx === 0 ? 0 : dlt / mx;
    if (sat < 0.12 || mx < 40) return 'neutral';
    let h;
    if (mx === r) h = 60 * (((g - b) / dlt) % 6);
    else if (mx === g) h = 60 * ((b - r) / dlt + 2);
    else h = 60 * ((r - g) / dlt + 4);
    if (h < 0) h += 360;
    if (h < 15 || h >= 320) return 'magenta';
    if (h < 75) return 'warm';
    if (h < 165) return 'green';
    if (h < 205) return 'cyan';
    if (h < 260) return 'cobalt';
    return 'violet';
  }

  /** 自定义图取主色（Canvas 32×32 降采样 + 饱和度加权平均；跨域/解码失败 → null）
   * @param {string} dataUrl 图片 dataURL
   * @returns {Promise<{tone:string, bright:number, rgb:number[]}|null>} 分档结果 */
  function wallExtractProfile(dataUrl) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
      try {
        const img = new Image();
        const timer = setTimeout(() => finish(null), 4000);
        img.onerror = () => { clearTimeout(timer); finish(null); };
        img.onload = () => {
          clearTimeout(timer);
          try {
            const N = 32;
            const cv = document.createElement('canvas');
            cv.width = N; cv.height = N;
            const cx = cv.getContext('2d', { willReadFrequently: true });
            if (!cx) { finish(null); return; }
            cx.drawImage(img, 0, 0, img.width, img.height, 0, 0, N, N);
            const d = cx.getImageData(0, 0, N, N).data;
            let r = 0, g = 0, b = 0, w = 0, lum = 0, px = 0;
            for (let i = 0; i < d.length; i += 4) {
              const R = d[i], G = d[i + 1], B = d[i + 2];
              if (d[i + 3] < 128) continue;
              px++;
              lum += (0.2126 * R + 0.7152 * G + 0.0722 * B) / 255;
              const mx = Math.max(R, G, B), mn = Math.min(R, G, B);
              const sat = mx === 0 ? 0 : (mx - mn) / mx;
              const weight = Math.max(0.06, sat); // 饱和度加权：鲜艳像素主导"主色"
              r += R * weight; g += G * weight; b += B * weight; w += weight;
            }
            const bright = px > 0 && (lum / px) > 0.6 ? 1 : 0;
            if (w <= 0) { finish(null); return; }
            const rr = Math.round(r / w), gg = Math.round(g / w), bb = Math.round(b / w);
            finish({ tone: wallToneFromRgb(rr, gg, bb), bright, rgb: [rr, gg, bb] });
          } catch (e) { finish(null); } // 画布污染（file:// 跨域图）等 → 回退默认冷蓝
        };
        img.src = dataUrl;
      } catch (e) { finish(null); }
    });
  }

  /** 读自定义图取色缓存（键 = 图指纹；换图自动失效） */
  function wallCachedCustomTone(dataUrl) {
    try {
      const raw = localStorage.getItem(CUSTOM_TONE_KEY);
      if (!raw) return null;
      const o = JSON.parse(raw);
      if (o && o.key === wallKeyOf(dataUrl) && WALL_TONES[o.tone]) return o;
    } catch (e) { /* 忽略 */ }
    return null;
  }

  /** 写自定义图取色缓存（容量异常时静默；仅缓存，失败不影响功能） */
  function wallCacheCustomTone(dataUrl, prof) {
    try {
      localStorage.setItem(CUSTOM_TONE_KEY, JSON.stringify({
        key: wallKeyOf(dataUrl), tone: prof.tone, bright: prof.bright
      }));
    } catch (e) { /* 忽略 */ }
  }

  /** 把联动结果写到 #app 属性上（CSS 侧的 tonal 变量组据此生效） */
  function wallPaint(tone, bright, isImg) {
    const shell = wallShell();
    if (!shell) return;
    shell.setAttribute('data-wall-tone', WALL_TONES[tone] ? tone : 'cobalt');
    if (isImg) shell.setAttribute('data-wall-img', '1');
    else shell.removeAttribute('data-wall-img');
    if (bright) shell.setAttribute('data-wall-bright', '1');
    else shell.removeAttribute('data-wall-bright');
  }

  /** 主入口：按当前壁纸套用联动（内置同步分档；自定义走缓存/异步取色）
   * @returns {void} */
  function wallLinkApply() {
    const id = wallCurrentId();
    if (id === 'custom') {
      let data = null;
      try { data = localStorage.getItem('moray_custom_wallpaper'); } catch (e) { data = null; }
      if (!data) { wallPaint('cobalt', 0, true); return; }
      const cached = wallCachedCustomTone(data);
      if (cached) { wallPaint(cached.tone, cached.bright, true); return; }
      wallPaint('cobalt', 0, true); // 取色完成前先用默认冷蓝，避免空白期
      wallExtractProfile(data).then((prof) => {
        const p = prof || { tone: 'cobalt', bright: 0 };
        wallCacheCustomTone(data, p);
        if (wallCurrentId() === 'custom') wallPaint(p.tone, p.bright, true);
      }).catch(() => { /* 已回退 */ });
      return;
    }
    const prof = WALL_PROFILE[id];
    if (!prof) { wallPaint('cobalt', 0, false); return; }
    wallPaint(prof.tone, 0, id.indexOf('anime-') === 0);
  }

  /* ---------- 样式：色相变量组 + 组件联动（独立 style，位于壁纸规则之后） ---------- */
  function injectWallLinkCss() {
    if (document.getElementById('wallpaperLinkStyle')) return;
    const st = document.createElement('style');
    st.id = 'wallpaperLinkStyle';
    let toneCss = '';
    Object.keys(WALL_TONES).forEach((k) => {
      const t = WALL_TONES[k];
      // 1) 光晕/强调色相（供 rgba(var(--x), α) 消费）
      toneCss += '#app[data-wall-tone="' + k + '"] { --wall-glow-rgb: ' + t.glow +
        '; --wall-accent-rgb: ' + t.accent + '; }\n';
      // 2) 面板压暗层按色相着色：纯色/渐变类用深版（更暗 → 对比度只升不降），
      //    图片类用浅版（保住"不压暗人物"），浅色主题用同亮度薄纱只带色相。
      //    :not([data-wall-img]) 保证图片壁纸不会套用深版（否则会压暗人物）。
      toneCss += '#app[data-wall-tone="' + k + '"]:not([data-wall-img]) { --wall-scrim: ' + t.scrim + '; }\n';
      toneCss += '#app[data-wall-tone="' + k + '"][data-wall-img] { --wall-scrim: ' + t.scrimImg + '; }\n';
      toneCss += 'html[data-theme="light"] #app[data-wallpaper][data-wall-tone="' + k +
        '"]:not([data-wall-img]) { --wall-scrim: ' + t.scrimLight + '; }\n';
    });
    let panelCss = '';
    Object.keys(WALL_PROFILE).forEach((k) => {
      const p = WALL_PROFILE[k];
      if (p.panel == null && p.card == null) return;
      let s = '#app[data-wallpaper="' + k + '"] {';
      if (p.panel != null) s += ' --wall-panel-alpha: ' + p.panel + ';';
      if (p.card != null) s += ' --wall-card-alpha: ' + p.card + ';';
      panelCss += s + ' }\n';
    });
    st.textContent = `
/* ========== [3.20 阶段1] 壁纸联动系统主题（独立样式表：同权重后定义获胜） ========== */
/* ---- 1) 色相分档：决定光晕/强调/叠色（由 JS 写 #app[data-wall-tone]） ---- */
${toneCss}/* 未分档（JS 尚未执行 / 未知壁纸）时回退品牌钴蓝，视觉零回归 */
#app[data-wallpaper] {
  --shadow-glove: 0 0 0 1px rgba(var(--wall-glow-rgb), 0.25), 0 0 16px rgba(var(--wall-accent-rgb), 0.15);
  --shadow-glove-focus: 0 0 0 1px rgba(var(--wall-glow-rgb), 0.55), 0 0 28px rgba(var(--wall-accent-rgb), 0.30);
  --shadow-glow-cobalt: 0 0 12px rgba(var(--wall-glow-rgb), 0.40);
  --shadow-glow-cyan: 0 0 12px rgba(var(--wall-accent-rgb), 0.40);
}
/* ---- 2) 三栏面板与输入台的色相联动 = 压暗层着色（--wall-scrim 按色相档取深版/浅版）。
        纯色/渐变类用的深版比原 rgba(6,8,14,.30) 更暗 → 换壁纸改变整屏色相，同时正文对比度
        只升不降（可读性红线）；图片类用浅版，保住"不压暗人物" ---- */
/* ---- 2b) 卡片：边框取壁纸主色（所有壁纸通用，含动漫/自定义图）。只改边框色，
        不加外发光、不改 background —— 既不动卡片自带渐变底，也不抬高正文可读性基线 ---- */
#app[data-wall-tone] main .glass-card,
#app[data-wall-tone] main .bg-surface-card,
#app[data-wall-tone] main .welcome-suggestion,
#app[data-wall-tone] main .doc-card,
#app[data-wall-tone] main .workflow-card,
#app[data-wall-tone] main .timestamp-card,
#app[data-wall-tone] main .model-detail-card,
#app[data-wall-tone] main .error-card {
  border-color: rgba(var(--wall-glow-rgb), 0.34);
}
/* ---- 2c) 侧栏右边框取壁纸主色：1px 精确色相提示，不动任何不透明度与亮度（可读性零影响），
        图片类壁纸也适用 —— 让"侧栏随壁纸变色"在任何壁纸下都肉眼可辨 ---- */
#app[data-wall-tone] aside.bg-surface-deep,
#app[data-wall-tone] #middleSidebar {
  border-right-color: rgba(var(--wall-glow-rgb), 0.35);
}
/* ---- 3) 输入台光晕 = 壁纸主色（非图片壁纸作用域；图片壁纸见第 6 节） ---- */
#app[data-wallpaper] .core-input {
  box-shadow: 0 0 0 2px rgba(var(--wall-glow-rgb), 0.30), 0 0 20px rgba(var(--wall-accent-rgb), 0.20);
}
#app[data-wallpaper] .core-input:focus-within {
  box-shadow: 0 0 0 2px rgba(var(--wall-glow-rgb), 0.55), 0 0 32px rgba(var(--wall-accent-rgb), 0.32);
}
/* ---- 4) 侧栏导航当前项 = 壁纸主色（原为固定品牌蓝） ---- */
#app[data-wallpaper] .nav-icon-btn.active {
  background: rgba(var(--wall-glow-rgb), 0.16);
  color: rgb(var(--wall-glow-rgb));
}
/* ---- 5) 面板透明度微调（仅纯色/渐变类；图片类保持"不压暗人物"既有值） ---- */
${panelCss}/* ---- 6) 图片壁纸（动漫内置 + 自定义）：保留深色浮起结构，只换光晕/边框色相 ---- */
#app[data-wallpaper="anime-starry"] #coreInputContainer,
#app[data-wallpaper="anime-aurora"] #coreInputContainer,
#app[data-wallpaper="anime-window"] #coreInputContainer,
#app[data-wallpaper="custom"] #coreInputContainer {
  border-color: rgba(var(--wall-glow-rgb), 0.28);
  box-shadow: 0 0 0 2px rgba(var(--wall-glow-rgb), 0.20), 0 10px 30px rgba(0, 0, 0, 0.45);
}
#app[data-wallpaper="anime-starry"] #coreInputContainer:focus-within,
#app[data-wallpaper="anime-aurora"] #coreInputContainer:focus-within,
#app[data-wallpaper="anime-window"] #coreInputContainer:focus-within,
#app[data-wallpaper="custom"] #coreInputContainer:focus-within {
  border-color: rgba(var(--wall-glow-rgb), 0.90);
  box-shadow: 0 0 0 1px rgba(var(--wall-glow-rgb), 0.40), 0 0 30px rgba(var(--wall-accent-rgb), 0.18), 0 10px 30px rgba(0, 0, 0, 0.45);
}
html[data-theme="light"] #app[data-wallpaper="anime-starry"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-aurora"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="anime-window"] #coreInputContainer,
html[data-theme="light"] #app[data-wallpaper="custom"] #coreInputContainer {
  border-color: rgba(var(--wall-glow-rgb), 0.34);
}
/* ---- 7) 可读性红线：高亮度自定义图自动加深遮罩（正文对比度不低于现状）。
        权重：[data-wall-tone] 后缀保证压过上面的按色相着色规则（同权重时后定义获胜） ---- */
#app[data-wall-bright="1"][data-wall-tone] {
  --wall-scrim: rgba(6, 8, 14, 0.52);
}
#app[data-wall-bright="1"][data-wall-tone] main .msg-bubble .md-body {
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.38);
}
html[data-theme="light"] #app[data-wallpaper][data-wall-bright="1"][data-wall-tone] {
  --wall-scrim: rgba(6, 8, 14, 0.42);
}
`;
    document.head.appendChild(st);
  }

  injectWallLinkCss();
  wallLinkApply();

  // 属性兜底：data-wallpaper / 内联背景变化时重算联动（只读 data-wallpaper，不写回，无循环）
  const shell = wallShell();
  if (shell && typeof MutationObserver === 'function') {
    new MutationObserver(() => { wallLinkApply(); })
      .observe(shell, { attributes: true, attributeFilter: ['data-wallpaper', 'style'] });
  }
  // 主题切换（data-theme）后重算：浅色/深色遮罩取值不同
  if (typeof MutationObserver === 'function') {
    new MutationObserver(() => { wallLinkApply(); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }
  // 自定义上传完成后（静置 0.4s 等内联背景落盘）再取一次色
  if (typeof window.handleCustomWallpaper === 'function') {
    const origHandle = window.handleCustomWallpaper;
    window.handleCustomWallpaper = function (input) {
      const r = origHandle.apply(this, arguments);
      setTimeout(() => {
        try { localStorage.removeItem(CUSTOM_TONE_KEY); } catch (e) { /* 忽略 */ }
        wallLinkApply();
      }, 400);
      return r;
    };
  }

  /** 调试/自检出口：查当前联动状态（不改状态，只读） */
  window.__wallLink = {
    apply: wallLinkApply,
    extract: wallExtractProfile,
    toneOf: wallCurrentId,
    profiles: WALL_PROFILE,
    tones: WALL_TONES,
    /** 当前 #app 上的联动属性快照 */
    snapshot() {
      const s = wallShell();
      return s ? {
        wallpaper: s.getAttribute('data-wallpaper'),
        tone: s.getAttribute('data-wall-tone'),
        bright: s.getAttribute('data-wall-bright')
      } : null;
    }
  };
})();
