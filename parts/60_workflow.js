/* ============================================================
   模块：自动化工作流引擎（任务七）/ 模型管理与监控（任务八）
   ============================================================ */

/* ===================== [任务七] 工作流引擎 ===================== */

/** 节点类型定义
 *  - input    输入节点：提供初始文本（或使用运行时传入的输入）
 *  - ai       AI 处理节点：用 prompt 模板调用模型（支持 {{input}} / {{变量}}）
 *  - code     代码执行节点：安全沙盒函数体（仅提供文本处理，无网络/无DOM）
 *  - condition 条件分支节点：表达式为真走 onTrue 后继，否则走 onFalse（线性引擎中为跳过分支节点）
 *  - loop     循环节点：对输入按行/分隔符循环执行后续节点（简化：对子节点重复执行）
 *  - output   输出节点：展示最终结果
 */

/** 内置工作流模板定义（6个）
 * @returns {Array<Object>} 模板数组 */
function builtinWorkflowTemplates() {
  return [
    {
      key: 'code-review', name: '代码审查', icon: 'scan-text', accent: '#5B8CFF',
      description: '自动分析代码质量、安全漏洞和性能问题',
      nodes: [
        { type: 'input', name: '输入代码', config: { placeholder: '粘贴待审查的代码…' } },
        { type: 'ai', name: 'AI 审查', config: { prompt: '你是一位资深代码审查专家。请审查以下代码，从【正确性、安全性、性能、可读性】四个维度分析，每个维度列出具体问题与改进建议，最后给出综合评分（1-10）。\n\n代码：\n{{input}}' } },
        { type: 'output', name: '输出审查报告', config: {} }
      ]
    },
    {
      key: 'doc-gen', name: '文档生成', icon: 'file-text', accent: '#3FD68F',
      description: '为代码自动生成注释、README 和 API 文档',
      nodes: [
        { type: 'input', name: '输入代码', config: { placeholder: '粘贴需要生成文档的代码…' } },
        { type: 'ai', name: '生成文档', config: { prompt: '请为以下代码生成专业文档：1) 功能概述 2) 函数/类说明表 3) 使用示例 4) 注意事项。使用 Markdown 格式。\n\n代码：\n{{input}}' } },
        { type: 'output', name: '输出文档', config: {} }
      ]
    },
    {
      key: 'test-gen', name: '测试生成', icon: 'flask-conical', accent: '#F2B24C',
      description: '自动生成单元测试用例，覆盖边界条件',
      nodes: [
        { type: 'input', name: '输入代码', config: { placeholder: '粘贴需要生成测试的代码…' } },
        { type: 'ai', name: '生成测试', config: { prompt: '请为以下代码生成完整的单元测试：覆盖正常路径、边界条件、异常情况。使用 pytest（Python）或 jest（JS），并说明每个用例的意图。\n\n代码：\n{{input}}' } },
        { type: 'output', name: '输出测试文件', config: {} }
      ]
    },
    {
      key: 'batch-translate', name: '批量翻译', icon: 'languages', accent: '#9D7BFF',
      description: '逐段翻译文本，保持原格式',
      nodes: [
        { type: 'input', name: '输入文本', config: { placeholder: '每行一条，将逐条翻译为英文…' } },
        { type: 'loop', name: '逐行循环', config: { split: '\n' } },
        { type: 'ai', name: '翻译', config: { prompt: '将以下内容翻译为英文，保持原格式与术语准确性，只输出译文：\n{{input}}' } },
        { type: 'output', name: '输出译文', config: {} }
      ]
    },
    {
      key: 'refactor', name: '代码重构', icon: 'wand-2', accent: '#FF5C6C',
      description: '自动优化代码结构、命名和性能问题',
      nodes: [
        { type: 'input', name: '输入代码', config: { placeholder: '粘贴需要重构的代码…' } },
        { type: 'ai', name: '检测问题', config: { prompt: '分析以下代码的问题（结构/命名/性能/坏味道），用要点列出，不超过8条：\n{{input}}' } },
        { type: 'ai', name: '执行重构', config: { prompt: '基于以下问题清单重构代码，输出完整重构后代码 + 修改说明。\n\n问题清单：\n{{input}}' } },
        { type: 'output', name: '输出重构结果', config: {} }
      ]
    },
    {
      key: 'benchmark', name: '性能基准测试', icon: 'activity', accent: '#3AD6E8',
      description: '多模型并发测试并生成对比报告',
      nodes: [
        { type: 'input', name: '测试提示词', config: { placeholder: '输入统一的测试提示词…' } },
        { type: 'ai', name: '多模型并发', config: { prompt: '{{input}}', multiModel: true } },
        { type: 'ai', name: '生成对比报告', config: { prompt: '以下是多份模型回答与性能数据，请生成 Markdown 对比报告（响应速度、内容质量、适用场景与推荐）：\n\n{{input}}' } },
        { type: 'output', name: '输出报告', config: {} }
      ]
    },
    // [夜间优化4.5] 新增模板
    {
      key: 'code-explain', name: '代码解释', icon: 'book-open', accent: '#5B8CFF',
      description: '逐段解释代码逻辑与设计意图',
      nodes: [
        { type: 'input', name: '输入代码', config: { placeholder: '粘贴需要解释的代码…' } },
        { type: 'ai', name: '逐段解释', config: { prompt: '请逐段解释以下代码：先一句话概括整体功能，再按逻辑块解释，标注关键算法/数据结构，最后列出学习要点。\n\n代码：\n{{input}}' } },
        { type: 'output', name: '输出解释', config: {} }
      ]
    },
    {
      key: 'summarize', name: '长文摘要', icon: 'align-left', accent: '#3AD6E8',
      description: '提取要点生成结构化摘要',
      nodes: [
        { type: 'input', name: '输入文章', config: { placeholder: '粘贴长文…' } },
        { type: 'ai', name: '生成摘要', config: { prompt: '请为以下内容生成结构化摘要：【一句话总结 / 核心要点（5条内）/ 关键数据 / 行动建议】。\n\n内容：\n{{input}}' } },
        { type: 'output', name: '输出摘要', config: {} }
      ]
    },
    {
      key: 'qa-gen', name: '问答对生成', icon: 'help-circle', accent: '#3FD68F',
      description: '从文档生成训练/复习用问答对',
      nodes: [
        { type: 'input', name: '输入资料', config: { placeholder: '粘贴资料…' } },
        { type: 'ai', name: '生成问答', config: { prompt: '从以下资料生成10组问答对（问题+简洁答案），覆盖主要知识点，按难度从易到难排列。\n\n资料：\n{{input}}' } },
        { type: 'output', name: '输出问答', config: {} }
      ]
    },
    {
      key: 'weekly-report', name: '周报生成', icon: 'calendar-check', accent: '#9D7BFF',
      description: '流水账整理为结构化周报',
      nodes: [
        { type: 'input', name: '工作流水账', config: { placeholder: '每行一条本周做的事…' } },
        { type: 'ai', name: '生成周报', config: { prompt: '把以下流水账整理成周报：【本周完成/数据亮点/问题与风险/下周计划】，要点化，量化优先，300字内。\n\n流水账：\n{{input}}' } },
        { type: 'output', name: '输出周报', config: {} }
      ]
    },
    {
      key: 'regex-gen', name: '正则生成', icon: 'regex', accent: '#F2B24C',
      description: '从需求生成正则并逐段解释',
      nodes: [
        { type: 'input', name: '匹配需求', config: { placeholder: '描述要匹配的内容规则…' } },
        { type: 'ai', name: '生成正则', config: { prompt: '为以下需求生成正则表达式：给出正则、逐段解释、3个匹配示例、2个不匹配示例、常见误用提醒。\n\n需求：\n{{input}}' } },
        { type: 'output', name: '输出正则', config: {} }
      ]
    }
  ];
}

/** 工作流引擎：负责执行、日志、暂停/停止与历史记录 */
const WorkflowEngine = {
  /** 当前运行状态 @type {Object|null} */
  current: null,

  /**
   * 执行工作流
   * @param {Object} wf - 工作流定义 {id, name, nodes}
   * @param {string} [initialInput] - 运行时输入
   * @returns {Promise<void>} */
  async run(wf, initialInput) {
    if (this.current) { showNotification('正在运行', '已有工作流在执行，请先停止', 'warning'); return; }
    if (AI.backend === 'none') await AI.detectBackend();
    if (AI.backend === 'none') { showNotification('未连接后端', '工作流的 AI 节点需要 Ollama 或 OpenAI 兼容后端', 'error', 4000); return; }

    const run = {
      wf, startedAt: Date.now(), status: 'running',
      paused: false, stopRequested: false,
      nodeIndex: 0, variables: { input: initialInput || '' },
      log: [], results: []
    };
    this.current = run;
    this.renderRunPanel();
    this.appendLog('info', '启动', `工作流「${wf.name}」开始执行，共 ${wf.nodes.length} 个节点`);

    try {
      await this.executeNodes(run);
      run.status = run.stopRequested ? 'stopped' : 'success';
      this.appendLog(run.status === 'success' ? 'success' : 'warn', '结束', `执行${run.status === 'success' ? '成功' : '已停止'}，用时 ${((Date.now() - run.startedAt) / 1000).toFixed(1)}s`);
    } catch (e) {
      run.status = 'failed';
      run.error = String(e.message || e);
      this.appendLog('error', '失败', run.error);
      showNotification('工作流失败', run.error, 'error', 4000);
    } finally {
      const dur = Date.now() - run.startedAt;
      // 记录历史（保留最近20条）
      const record = {
        id: uid('run'), workflowId: wf.id, workflowName: wf.name,
        startedAt: run.startedAt, finishedAt: Date.now(), durationMs: dur,
        status: run.status, error: run.error || '',
        nodeResults: run.results.map(r => ({ name: r.name, type: r.type, ms: r.ms, ok: r.ok, output: String(r.output || '').slice(0, 500) }))
      };
      try {
        const fresh = await DB.getWorkflow(wf.id);
        if (fresh) {
          fresh.runCount = (fresh.runCount || 0) + 1;
          fresh.lastRunAt = run.startedAt;
          fresh.runs = [record].concat(fresh.runs || []).slice(0, 20);
          await DB.putWorkflow(fresh);
        }
      } catch (e) { console.warn('[MoRay] save run history failed:', e); }
      await WorkflowsApp.reload();
      this.renderRunPanel();
      // [夜间优化4.5] 完成桌面通知
      if (typeof desktopNotify === 'function') {
        desktopNotify('工作流' + (run.status === 'success' ? '已完成' : '已结束'),
          wf.name + ' · ' + ((Date.now() - run.startedAt) / 1000).toFixed(1) + 's · ' + run.status);
      }
      this.current = null;
    }
  },

  /** 依序执行节点（支持暂停/停止）
   * @param {Object} run - 运行状态
   * @returns {Promise<void>} */
  async executeNodes(run) {
    const nodes = run.wf.nodes;
    for (let i = 0; i < nodes.length; i++) {
      if (run.stopRequested) return;
      // 循环节点内部已消费循环体节点，跳过一次
      if (run._skipOnce) { run._skipOnce = false; continue; }
      // 等待暂停恢复
      while (run.paused && !run.stopRequested) { await new Promise(r => setTimeout(r, 300)); }
      if (run.stopRequested) return;
      run.nodeIndex = i;
      const node = nodes[i];
      this.renderRunPanel();
      const started = Date.now();
      const nodeInputSnapshot = run.variables.input; // 供日志记录节点输入
      this.appendLog('info', node.name, `开始执行（${node.type}）`);
      let output = '';
      try {
        switch (node.type) {
          case 'input':
            output = run.variables.input || '';
            if (!output) {
              output = await this.requestInput(node);
              run.variables.input = output;
            }
            break;
          case 'ai':
            output = await this.runAINode(node, run);
            break;
          case 'code':
            output = this.runCodeNode(node, run);
            break;
          case 'condition':
            output = this.runConditionNode(node, run);
            break;
          case 'loop':
            output = await this.runLoopNode(node, run, nodes, i);
            // loop 节点内部已执行完循环体，直接跳到 output 前
            break;
          case 'output':
          default:
            output = run.variables.input || '';
            break;
        }
        run.variables.input = output;
        run.results.push({ name: node.name, type: node.type, ms: Date.now() - started, ok: true, output });
        this.appendLog('success', node.name, `完成（${((Date.now() - started) / 1000).toFixed(1)}s），输出 ${output.length} 字符`,
          { input: String(nodeInputSnapshot || '').slice(0, 800), output: String(output).slice(0, 800) });
      } catch (e) {
        run.results.push({ name: node.name, type: node.type, ms: Date.now() - started, ok: false, output: String(e.message || e) });
        this.appendLog('error', node.name, `失败：${e.message || e}`);
        throw e;
      }
      // 展示最终输出
      if (node.type === 'output') this.showOutput(run, output);
    }
  },

  /** 运行中输入请求（input 节点无输入时弹出）
   * @param {Object} node - 节点
   * @returns {Promise<string>} 用户输入 */
  requestInput(node) {
    return new Promise((resolve, reject) => {
      const box = showModal(`
        <div class="form-row">
          <label class="form-label">${escapeHtml(node.name)} - 请输入内容</label>
          <textarea id="wfInputArea" class="form-textarea" placeholder="${escapeHtml((node.config && node.config.placeholder) || '输入内容...')}"></textarea>
        </div>`,
        { title: '工作流输入', icon: 'log-in', footer: false });
      const ta = box.querySelector('#wfInputArea');
      ta.focus();
      const submit = () => { const v = ta.value.trim(); if (!v) { showNotification('需要输入', '内容不能为空', 'warning'); return; } closeModal(); resolve(v); };
      ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) submit(); });
      // 工作流被停止时自动关闭输入框
      const check = setInterval(() => {
        if (!this.current || this.current.stopRequested) { clearInterval(check); closeModal(); reject(new Error('已停止')); }
      }, 400);
      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;margin-top:12px';
      btnRow.innerHTML = `<button class="btn-primary px-4 py-2 rounded-lg text-sm" id="wfInputOk">确定 (Ctrl+Enter)</button>`;
      box.querySelector('.modal-body').appendChild(btnRow);
      on(btnRow.querySelector('#wfInputOk'), 'click', submit);
      refreshIcons();
    });
  },

  /** AI 节点：模板变量替换后调用模型
   * @param {Object} node - 节点
   * @param {Object} run - 运行状态
   * @returns {Promise<string>} 模型输出 */
  async runAINode(node, run) {
    let prompt = (node.config && node.config.prompt) || '{{input}}';
    prompt = prompt.split('{{input}}').join(run.variables.input);
    const messages = [
      ...(MoraySettings.get('systemPrompt') ? [{ role: 'system', content: MoraySettings.get('systemPrompt') }] : []),
      { role: 'user', content: prompt }
    ];
    if (node.config && node.config.multiModel) {
      // [多模型对比修复] 多模型并发：先按当前真实模型列表清洗 compareModels
      // （过滤占位串/失效模型，避免空名/占位名下发给 Ollama 产生 400），再取前 2 个
      const allNames = (AI.models || []).map(m => m && m.name).filter(Boolean);
      const stored = MoraySettings.get('compareModels');
      const models = (Array.isArray(stored) ? stored : []).filter(n => allNames.includes(n)).slice(0, 2);
      const useModels = models.length ? models : allNames.slice(0, 1);
      if (!useModels.length) return '';
      const parts = await Promise.all(useModels.map(async (m) => {
        try {
          // [深度思考/常驻] 与普通对话一致的 think 决策；keep_alive:30m 由 AI.chat 的 Ollama 分支统一附带
          const think = (typeof decideThinkFor === 'function') ? decideThinkFor(m, messages) : false;
          const r = await AI.chat({ model: m, messages, think });
          recordPerf(m, r.stats, true);
          return `### 模型：${m}\n（${r.stats.tokPerSec || '?'} tok/s · ${r.stats.ms}ms）\n\n${r.content}`;
        } catch (e) { return `### 模型：${m}\n请求失败：${e.message}`; }
      }));
      updatePerfUI();
      return parts.join('\n\n---\n\n');
    }
    const model = (this.current.wf.model) || MoraySettings.get('defaultModel') || (AI.models && AI.models[0] && AI.models[0].name);
    // [V2 功能5] 批量优化：走网关（重复输入直接命中精确缓存，零重复调用）
    let result;
    if (MoraySettings.get('gatewayEnabled') !== false && typeof Gateway !== 'undefined') {
      const g = Gateway.chat({ model, messages });
      result = await g;
    } else {
      result = await AI.chat({ model, messages });
    }
    // [V2 功能5] 批量任务 QPS 限速（默认 500ms 间隔，防触发 API 限流）
    const qpsGap = 500;
    const now = Date.now();
    const wait = Math.max(0, (this._lastAICall || 0) + qpsGap - now);
    this._lastAICall = now + wait;
    if (wait > 0) await new Promise(r => setTimeout(r, wait));
    recordPerf(model, result.stats, true);
    updatePerfUI();
    return result.content;
  },

  /** [M5.1安全] 安全条件表达式解析（不执行任意 JS）：支持 text.length 比较 /
   * text.includes / text.startsWith / text.endsWith / 相等比较 / 布尔字面量；
   * 无法安全解析的表达式回退 text.length > 0（不 new Function）
   * @param {string} expr - 表达式
   * @param {string} text - 输入文本
   * @returns {boolean} */
  safeCondition(expr, text) {
    const s = String(text || '');
    const e = String(expr || '').trim();
    const num = (v) => { const n = parseFloat(v); return isNaN(n) ? null : n; };
    // 布尔字面量
    if (e === 'true') return true;
    if (e === 'false') return false;
    // 长度比较：text.length > 10 / >= / < / <= / ==
    const lenM = e.match(/^text\.length\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)?)$/);
    if (lenM) {
      const n = num(lenM[2]);
      if (n === null) return false;
      const L = s.length;
      switch (lenM[1]) {
        case '>': return L > n;
        case '>=': return L >= n;
        case '<': return L < n;
        case '<=': return L <= n;
        case '==': return L === n;
        case '!=': return L !== n;
      }
    }
    // 包含/前后缀：text.includes('x') / startsWith / endsWith
    const strM = e.match(/^text\.(includes|startsWith|endsWith)\(\s*'([^']*)'\s*\)$/);
    if (strM) {
      const needle = strM[2];
      if (strM[1] === 'includes') return s.includes(needle);
      if (strM[1] === 'startsWith') return s.startsWith(needle);
      return s.endsWith(needle);
    }
    // 相等比较：text == 'x' / !=
    const eqM = e.match(/^text\s*(==|!=)\s*'([^']*)'$/);
    if (eqM) {
      return eqM[1] === '==' ? s === eqM[2] : s !== eqM[2];
    }
    // 空判断：text == '' / text.length == 0
    if (e === "text == ''" || e === "text === ''") return s === '';
    return s.length > 0;
  },

  /** 代码节点：[M5.1安全] 默认禁用（new Function 主线程执行可访问 window/document/localStorage）；
   * 仅当设置 codeNodesEnabled 开启时执行（用户已确认风险）
   * @param {Object} node - 节点
   * @param {Object} run - 运行状态
   * @returns {string} 输出 */
  runCodeNode(node, run) {
    if (MoraySettings.get('codeNodesEnabled') !== true) {
      return '⚠️ 自定义代码节点已禁用（默认安全设置）。如需运行，请到 设置 → 自动化 开启"允许自定义 JS 代码节点"。';
    }
    const fn = new Function('text', '"use strict";\n' + (node.config && node.config.code || 'return text;'));
    return String(fn(run.variables.input));
  },

  /** 条件节点：表达式真 -> 输出 yesValue 否则 noValue（供后续节点提示）
   * [M5.1安全] 不再直接 new Function：关闭时用安全解析器；开启时仍执行完整表达式
   * @param {Object} node - 节点
   * @param {Object} run - 运行状态
   * @returns {string} 'yes' | 'no' */
  runConditionNode(node, run) {
    const expr = (node.config && node.config.expression) || 'text.length > 0';
    let ok;
    if (MoraySettings.get('codeNodesEnabled') === true) {
      try { ok = !!new Function('text', '"use strict"; try { return !!(' + expr + '); } catch(e) { return false; }')(run.variables.input); }
      catch (e) { ok = false; }
    } else {
      ok = this.safeCondition(expr, run.variables.input);
    }
    this.appendLog('info', node.name, `条件判定：${ok ? '真' : '假'}`);
    return ok ? 'yes' : 'no';
  },

  /** 循环节点：把输入按分隔符切开，对每段执行后续第一个 AI 节点后拼接
   * @param {Object} node - loop 节点
   * @param {Object} run - 运行状态
   * @param {Array} nodes - 全部节点
   * @param {number} i - 当前索引
   * @returns {Promise<string>} 拼接结果 */
  async runLoopNode(node, run, nodes, i) {
    const split = (node.config && node.config.split) || '\n';
    const parts = String(run.variables.input).split(split).map(s => s.trim()).filter(Boolean);
    // 找到循环体（下一个节点，通常是 ai）
    const bodyNode = nodes[i + 1];
    const outputs = [];
    for (let k = 0; k < parts.length; k++) {
      if (run.stopRequested) break;
      while (run.paused && !run.stopRequested) { await new Promise(r => setTimeout(r, 300)); }
      this.appendLog('info', node.name, `循环 ${k + 1}/${parts.length}：${parts[k].slice(0, 24)}…`);
      run.variables.input = parts[k];
      if (bodyNode && bodyNode.type === 'ai') {
        outputs.push(await this.runAINode(bodyNode, run));
      } else {
        outputs.push(parts[k]);
      }
      this.renderRunPanel();
    }
    // 循环体节点已消费：通知外层循环跳过下一个节点
    run._skipOnce = true;
    run.variables.input = outputs.join(split === '\n' ? '\n\n' : split);
    return run.variables.input;
  },

  /** 展示工作流最终输出
   * @param {Object} run - 运行状态
   * @param {string} output - 输出内容
   * @returns {void} */
  showOutput(run, output) {
    const box = showModal(`<div class="md-body" style="max-height:60vh;overflow-y:auto">${renderMarkdown(output)}</div>`,
      {
        title: '工作流输出 · ' + run.wf.name, icon: 'check-circle-2', wide: true,
        footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-out-copy>复制</button>
                 <button class="btn-primary px-4 py-2 rounded-lg text-sm" data-out-close>关闭</button>`
      });
    on(box.querySelector('[data-out-close]'), 'click', closeModal);
    on(box.querySelector('[data-out-copy]'), 'click', async () => {
      const ok = await copyToClipboard(output);
      showNotification(ok ? '已复制' : '复制失败', '', ok ? 'success' : 'error', 1500);
    });
  },

  /** 追加执行日志（可携带节点输入输出详情，点击条目查看）
   * @param {string} level - info|success|error|warn
   * @param {string} node - 节点名
   * @param {string} msg - 消息
   * @param {Object} [detail] - {input, output} 节点IO摘要
   * @returns {void} */
  appendLog(level, node, msg, detail) {
    if (!this.current) return;
    this.current.log.push({ level, node, msg, detail: detail || null, ts: Date.now() });
    const logEl = document.getElementById('runLog');
    if (logEl) {
      const entry = document.createElement('div');
      entry.className = 'log-entry ' + level;
      if (detail) entry.title = '点击查看节点输入输出';
      entry.dataset.logIdx = this.current.log.length - 1;
      entry.innerHTML = `<span class="log-time">${clockTime(Date.now())} </span><span class="log-node">[${escapeHtml(node)}]</span><span class="log-msg">${escapeHtml(msg)}${detail ? ' ⤵' : ''}</span>`;
      logEl.appendChild(entry);
      logEl.scrollTop = logEl.scrollHeight;
    }
  },

  /** 渲染运行监控面板（进度/步骤/按钮状态）
   * @returns {void} */
  renderRunPanel() {
    const section = document.getElementById('workflowRunSection');
    if (!section) return;
    const run = this.current;
    if (!run) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');
    document.getElementById('runWorkflowName').textContent = run.wf.name;
    const done = run.nodeIndex;
    document.getElementById('runWorkflowMeta').textContent =
      `已运行 ${((Date.now() - run.startedAt) / 1000).toFixed(0)}s · 第 ${Math.min(done + 1, run.wf.nodes.length)}/${run.wf.nodes.length} 步 · ${run.status === 'running' ? (run.paused ? '已暂停' : '运行中') : run.status}`;
    const fill = document.getElementById('runProgressFill');
    fill.style.width = Math.round((run.nodeIndex / Math.max(1, run.wf.nodes.length)) * 100) + '%';
    const steps = document.getElementById('runSteps');
    steps.innerHTML = run.wf.nodes.map((n, i) => {
      const cls = i < run.nodeIndex ? 'done' : i === run.nodeIndex ? (run.paused ? 'paused' : 'active') : '';
      return `<div class="running-step ${cls}">${escapeHtml(n.name)}</div>`;
    }).join('');
    const pauseBtn = document.getElementById('runPauseBtn');
    if (pauseBtn) pauseBtn.textContent = run.paused ? '继续' : '暂停';
  },

  /** 暂停/继续切换
   * @returns {void} */
  togglePause() {
    if (!this.current) return;
    this.current.paused = !this.current.paused;
    this.appendLog('info', '控制', this.current.paused ? '已暂停' : '继续执行');
    this.renderRunPanel();
  },

  /** 请求停止
   * @returns {void} */
  requestStop() {
    if (!this.current) return;
    this.current.stopRequested = true;
    this.appendLog('warn', '控制', '收到停止请求，正在终止…');
    closeModal();
  }
};

/** 工作流页面应用 */
const WorkflowsApp = {
  /** 全部工作流 @type {Array} */
  cache: [],

  /** 重建自动化页面内容（接管静态模板卡片区域）
   * @returns {Promise<void>} */
  async reload() {
    try { this.cache = await DB.listWorkflows(); } catch (e) { this.cache = []; console.error(e); }
    this.renderGrid();
    this.renderHistory();
    const pill = document.getElementById('workflowCountPill');
    if (pill) pill.textContent = this.cache.length + ' 个模板';
    // [夜间优化4.5] 确保调度器已启动
    if (typeof WorkflowScheduler !== 'undefined') WorkflowScheduler.start();
  },

  /** 渲染工作流卡片
   * @returns {void} */
  renderGrid() {
    const grid = document.getElementById('workflowGrid');
    if (!grid) return;
    if (!this.cache.length) {
      // [V3 P2.5] 统一空状态
      grid.innerHTML = `<div class="col-span-2">${renderEmptyState({
        icon: 'workflow', title: '创建自动化工作流', desc: '把重复任务编排为节点流水线，一键执行', actionText: '新建工作流'
      })}</div>`;
      bindEmptyAction(grid, () => this.openEditor());
      refreshIcons();
      return;
    }
    grid.innerHTML = this.cache.map(wf => {
      const nodeNames = (wf.nodes || []).map(n => n.name);
      const iconFix = { 'code-review': 'scan-text' }; // 旧数据图标名兼容
      const wfIcon = iconFix[wf.icon] || wf.icon || 'workflow';
      const stepsHtml = nodeNames.map((n, i) =>
        `<span class="workflow-step">${escapeHtml(n)}</span>${i < nodeNames.length - 1 ? '<span class="workflow-step-arrow">→</span>' : ''}`).join('');
      return `
      <div class="workflow-card" style="--accent: ${escapeHtml(wf.accent || '#5B8CFF')}" data-wf-id="${wf.id}">
        <div class="workflow-icon" style="background:${escapeHtml((wf.accent || '#5B8CFF'))}1F;color:${escapeHtml(wf.accent || '#5B8CFF')}">
          <i data-lucide="${escapeHtml(wfIcon)}" class="w-5 h-5"></i>
        </div>
        <div class="text-sm font-medium text-text-primary">${escapeHtml(wf.name)}</div>
        <div class="text-[11px] text-text-tertiary mt-1">${escapeHtml(wf.description || '')}</div>
        <div class="workflow-steps">${stepsHtml}</div>
        <div class="flex items-center justify-between mt-3">
          <span class="text-[10px] text-text-tertiary">运行 ${wf.runCount || 0} 次${wf.lastRunAt ? ' · ' + relTime(wf.lastRunAt) : ''}</span>
          <div class="flex items-center gap-1">
            <button class="tool-btn" data-wf-op="edit" title="编辑"><i data-lucide="edit-3" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-wf-op="copy" title="复制"><i data-lucide="copy" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-wf-op="export" title="导出"><i data-lucide="download" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-wf-op="delete" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>
        </div>
        <button class="workflow-run-btn" data-wf-op="run">
          <i data-lucide="play" class="w-3 h-3"></i>运行工作流
        </button>
      </div>`;
    }).join('');
    refreshIcons();
  },

  /** 渲染执行历史
   * @returns {void} */
  renderHistory() {
    const list = document.getElementById('workflowHistoryList');
    if (!list) return;
    const runs = [];
    this.cache.forEach(wf => (wf.runs || []).forEach(r => runs.push(r)));
    runs.sort((a, b) => b.startedAt - a.startedAt);
    const recent = runs.slice(0, 8);
    if (!recent.length) {
      list.innerHTML = '<div class="text-center py-6 text-text-tertiary text-xs">暂无执行记录</div>';
      return;
    }
    list.innerHTML = recent.map(r => {
      const cls = r.status === 'success' ? 'success' : r.status === 'failed' ? 'error' : 'indexing';
      const text = r.status === 'success' ? '成功' : r.status === 'failed' ? '失败' : '停止';
      return `<div class="doc-card px-4 py-2.5 flex items-center justify-between" data-run-view="${r.id}" style="cursor:pointer">
        <div class="flex items-center gap-3 min-w-0">
          <span class="doc-status ${cls}">${text}</span>
          <span class="text-xs text-text-primary truncate">${escapeHtml(r.workflowName)}</span>
          ${r.error ? `<span class="text-[10px] text-danger truncate">${escapeHtml(r.error.slice(0, 40))}</span>` : ''}
        </div>
        <div class="text-[10px] text-text-tertiary flex-shrink-0">${(r.durationMs / 1000).toFixed(1)}s · ${relTime(r.startedAt)}</div>
      </div>`;
    }).join('');
    list.querySelectorAll('[data-run-view]').forEach(el => {
      el.addEventListener('click', () => this.viewRun(el.dataset.runView));
    });
  },

  /** 查看某次执行详情
   * @param {string} runId - 执行记录ID
   * @returns {void} */
  viewRun(runId) {
    let run = null;
    this.cache.forEach(wf => (wf.runs || []).forEach(r => { if (r.id === runId) run = r; }));
    if (!run) return;
    showModal(`<div class="space-y-2">${(run.nodeResults || []).map(n => `
      <div class="chunk-item">
        <div class="flex items-center justify-between mb-1">
          <span class="${n.ok ? 'text-success' : 'text-danger'}">${n.ok ? '✓' : '✗'} ${escapeHtml(n.name)}（${n.type}）</span>
          <span class="text-text-tertiary">${(n.ms / 1000).toFixed(1)}s</span>
        </div>
        <div class="text-text-secondary" style="white-space:pre-wrap;max-height:120px;overflow-y:auto">${escapeHtml(n.output || '').slice(0, 600)}</div>
      </div>`).join('') || '<div class="text-xs text-text-tertiary">无节点记录</div>'}</div>`,
      { title: '执行详情 · ' + run.workflowName, icon: 'history', wide: true, footer: false });
  },

  /** 网格事件委托
   * @returns {void} */
  bindEvents() {
    const grid = document.getElementById('workflowGrid');
    if (grid) {
      grid.addEventListener('click', (e) => {
        const card = e.target.closest('.workflow-card');
        const btn = e.target.closest('[data-wf-op]');
        if (!card || !btn) return;
        const id = card.dataset.wfId;
        const wf = this.cache.find(w => w.id === id);
        if (!wf) return;
        const op = btn.dataset.wfOp;
        if (op === 'run') {
          const hasInputNode = (wf.nodes || []).some(n => n.type === 'input');
          if (hasInputNode) WorkflowEngine.run(wf);
          else WorkflowEngine.run(wf, '');
        }
        else if (op === 'edit') this.openEditor(wf.id);
        else if (op === 'copy') this.duplicate(wf.id);
        else if (op === 'export') { downloadJson('workflow_' + wf.name + '.json', wf); showNotification('已导出', wf.name, 'success', 1600); }
        else if (op === 'delete') {
          showConfirm('删除工作流', `确定删除「${wf.name}」？`, async () => {
            await DB.deleteWorkflow(id);
            await this.reload();
            showNotification('已删除', '工作流已删除', 'success', 1500);
          }, { danger: true, okText: '删除' });
        }
      });
    }
    // 顶部按钮
    const newBtn = document.getElementById('newWorkflowBtn');
    if (newBtn) newBtn.addEventListener('click', () => this.openEditor());
    const importBtn = document.getElementById('importWorkflowBtn');
    if (importBtn) importBtn.addEventListener('click', () => {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.json';
      inp.onchange = async () => {
        try {
          const data = JSON.parse(await inp.files[0].text());
          const nodes = data.nodes || [];
          // [M5.1安全] 导入含"自定义代码/条件表达式"节点的工作流 → 先确认（代码将在本机运行）
          const hasCode = nodes.some(n => n && (n.type === 'code' || n.type === 'condition'));
          const doImport = async () => {
            await DB.createWorkflow({ name: data.name || '导入工作流', description: data.description || '', icon: data.icon || 'workflow', accent: data.accent || '#5B8CFF', nodes });
            await this.reload();
            showNotification('导入成功', data.name || '', 'success');
          };
          if (hasCode) {
            if (typeof showConfirm === 'function') {
              showConfirm('工作流包含可执行代码', '该工作流含自定义代码/条件表达式节点，将在本机浏览器中执行（默认已禁用，需在设置中开启后才会运行）。是否仍要导入？', doImport, { okText: '仍要导入', danger: true });
            } else { await doImport(); }
          } else {
            await doImport();
          }
        } catch (e) { showNotification('导入失败', e.message, 'error'); }
      };
      inp.click();
    });
    // [零摆设 P0-1] 导出全部工作流为 JSON（复用 downloadJson；无数据时禁用并提示）
    const exportBtn = document.getElementById('exportWorkflowBtn');
    if (exportBtn) {
      const refreshExportState = async () => {
        try {
          const all = await DB.listWorkflows();
          const empty = !all.length;
          exportBtn.disabled = empty;
          exportBtn.title = empty ? '暂无可导出的工作流' : '导出全部工作流（JSON）';
          exportBtn.style.opacity = empty ? '0.35' : '';
        } catch (e) { /* 查询失败保持可用 */ }
      };
      refreshExportState();
      exportBtn.addEventListener('click', async () => {
        const all = await DB.listWorkflows();
        if (!all.length) { showNotification('暂无可导出的工作流', '先新建或导入工作流后再导出', 'warning', 2500); return; }
        downloadJson('moray_workflows_' + Date.now() + '.json', all);
        showNotification('已导出 ' + all.length + ' 个工作流', 'JSON 文件已下载', 'success', 2200);
      });
      // 列表变更后刷新禁用态
      const origReload = this.reload.bind(this);
      this.reload = async function () { await origReload(); refreshExportState(); };
    }
    // 运行控制
    const pauseBtn = document.getElementById('runPauseBtn');
    if (pauseBtn) pauseBtn.addEventListener('click', () => WorkflowEngine.togglePause());
    const stopBtn = document.getElementById('runStopBtn');
    if (stopBtn) stopBtn.addEventListener('click', () => WorkflowEngine.requestStop());
    const logClear = document.getElementById('runLogClear');
    if (logClear) logClear.addEventListener('click', () => { const el = document.getElementById('runLog'); if (el) el.innerHTML = ''; });
    // [阶段三] 点击日志条目查看节点输入输出
    const runLog = document.getElementById('runLog');
    if (runLog) {
      runLog.addEventListener('click', (e) => {
        const entry = e.target.closest('.log-entry');
        if (!entry || entry.dataset.logIdx === undefined) return;
        const idx = parseInt(entry.dataset.logIdx, 10);
        const rec = (WorkflowEngine.current && WorkflowEngine.current.log[idx]) || null;
        if (!rec || !rec.detail) return;
        showModal(`<div class="space-y-3">
          <div>
            <div class="form-label">节点输入</div>
            <pre class="chunk-item" style="white-space:pre-wrap;max-height:160px;overflow-y:auto">${escapeHtml(rec.detail.input || '（空）')}</pre>
          </div>
          <div>
            <div class="form-label">节点输出</div>
            <pre class="chunk-item" style="white-space:pre-wrap;max-height:220px;overflow-y:auto">${escapeHtml(rec.detail.output || '（空）')}</pre>
          </div>
        </div>`, { title: '节点日志 · ' + rec.node, icon: 'scroll-text', wide: true, footer: false });
      });
    }
    const clearHist = document.getElementById('clearWfHistoryBtn');
    if (clearHist) clearHist.addEventListener('click', () => {
      showConfirm('清空执行历史', '将删除所有工作流的执行记录（不影响工作流本身）。', async () => {
        for (const wf of this.cache) {
          if ((wf.runs || []).length) { wf.runs = []; await DB.putWorkflow(wf); }
        }
        await this.reload();
        showNotification('已清空', '执行历史已清空', 'success', 1500);
      }, { danger: true, okText: '清空' });
    });
  },

  /** 复制工作流
   * @param {string} id - 工作流ID
   * @returns {Promise<void>} */
  async duplicate(id) {
    const wf = this.cache.find(w => w.id === id);
    if (!wf) return;
    await DB.createWorkflow({ name: wf.name + ' 副本', description: wf.description, icon: wf.icon, accent: wf.accent, nodes: JSON.parse(JSON.stringify(wf.nodes)) });
    await this.reload();
    showNotification('已复制', wf.name + ' 副本', 'success', 1600);
  },

  /** 工作流编辑器（节点列表编辑：增删改/上下移动）
   * @param {string} [id] - 工作流ID
   * @returns {void} */
  openEditor(id) {
    const wf = id ? this.cache.find(w => w.id === id) : null;
    const state = {
      name: wf ? wf.name : '',
      description: wf ? wf.description : '',
      accent: wf ? wf.accent : '#5B8CFF',
      icon: wf ? wf.icon : 'workflow',
      nodes: wf ? JSON.parse(JSON.stringify(wf.nodes)) : [{ type: 'input', name: '输入', config: {} }]
    };
    const ACCENTS = ['#5B8CFF', '#3FD68F', '#F2B24C', '#9D7BFF', '#FF5C6C', '#3AD6E8'];

    const render = () => {
      const box = showModal(`
        <div class="form-row form-inline">
          <div style="flex:2"><label class="form-label">名称 *</label><input type="text" id="weName" class="form-input" value="${escapeHtml(state.name)}" placeholder="工作流名称"></div>
          <div><label class="form-label">强调色</label><div class="flex gap-1.5 h-9 items-center">${ACCENTS.map(c => `<div class="theme-color-option ${state.accent === c ? 'active' : ''}" data-accent="${c}" style="width:20px;height:20px;background:${c};border-radius:6px;cursor:pointer"></div>`).join('')}</div></div>
        </div>
        <div class="form-row form-inline">
          <div style="flex:2"><label class="form-label">描述</label><input type="text" id="weDesc" class="form-input" value="${escapeHtml(state.description)}" placeholder="一句话说明用途"></div>
          <div>
            <label class="form-label">定时调度</label>
            <select id="weSchedule" class="form-select" style="padding:8px">
              <option value="off" ${(wf && wf.schedule === 'off') || !(wf && wf.schedule) ? 'selected' : ''}>关闭</option>
              <option value="interval:30" ${wf && wf.schedule === 'interval:30' ? 'selected' : ''}>每30分钟</option>
              <option value="interval:120" ${wf && wf.schedule === 'interval:120' ? 'selected' : ''}>每2小时</option>
              <option value="daily:09:00" ${wf && wf.schedule === 'daily:09:00' ? 'selected' : ''}>每天 09:00</option>
            </select>
            <!-- [M5.1] 定时工作流可见性限制标注 -->
            <div class="form-hint">仅在 MoRay 页面保持打开时运行；关闭/最小化/休眠不会执行（后端调度列入后续路线图）</div>
          </div>
        </div>
        <div class="form-row">
          <div class="flex items-center justify-between mb-2">
            <label class="form-label" style="margin:0">节点（按顺序执行）</label>
            <div class="flex gap-1">
              <select id="weNodeType" class="form-select" style="width:auto;padding:4px 8px;font-size:11px">
                <option value="input">输入节点</option><option value="ai">AI 处理</option><option value="code">代码执行</option>
                <option value="condition">条件分支</option><option value="loop">循环</option><option value="output">输出</option>
              </select>
              <button class="btn-ghost px-2 py-1 rounded text-[11px] border border-line-ghost" id="weAddNode">+ 添加</button>
            </div>
          </div>
          <div class="space-y-2" id="weNodeList"></div>
        </div>`,
        {
          title: wf ? '编辑工作流' : '新建工作流', icon: 'workflow', wide: true,
          footer: `<button class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost" data-we-cancel>取消</button>
                   <button class="btn-primary px-4 py-2 rounded-lg text-sm" id="weSave">保存</button>`
        });
      on(box.querySelector('[data-we-cancel]'), 'click', closeModal);
      box.querySelectorAll('[data-accent]').forEach(el => el.addEventListener('click', () => { state.accent = el.dataset.accent; box.querySelectorAll('[data-accent]').forEach(x => x.classList.toggle('active', x === el)); }));
      on(box.querySelector('#weAddNode'), 'click', () => {
        const type = box.querySelector('#weNodeType').value;
        const defaults = {
          input: { config: { placeholder: '' } },
          ai: { config: { prompt: '{{input}}' } },
          code: { config: { code: 'return text.toUpperCase();' } },
          condition: { config: { expression: 'text.length > 100' } },
          loop: { config: { split: '\n' } },
          output: { config: {} }
        };
        state.nodes.push(Object.assign({ type, name: { input: '输入', ai: 'AI 处理', code: '代码执行', condition: '条件判断', loop: '循环', output: '输出' }[type] }, defaults[type]));
        renderNodes();
      });
      const nodeList = () => box.querySelector('#weNodeList');
      const renderNodes = () => {
        nodeList().innerHTML = state.nodes.map((n, i) => `
          <div class="chunk-item flex items-center gap-2" data-node-idx="${i}">
            <span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt flex-shrink-0">${n.type}</span>
            <input type="text" class="form-input" style="flex:1;padding:4px 8px;font-size:11px" data-node-name="${i}" value="${escapeHtml(n.name)}">
            <button class="tool-btn" data-node-op="cfg" title="配置"><i data-lucide="settings-2" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-node-op="up" title="上移"><i data-lucide="chevron-up" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-node-op="down" title="下移"><i data-lucide="chevron-down" class="w-3.5 h-3.5"></i></button>
            <button class="tool-btn" data-node-op="del" title="删除"><i data-lucide="trash-2" class="w-3.5 h-3.5 text-danger"></i></button>
          </div>`).join('');
        refreshIcons();
        nodeList().querySelectorAll('[data-node-name]').forEach(inp => {
          inp.addEventListener('change', () => { state.nodes[parseInt(inp.dataset.nodeName, 10)].name = inp.value; });
        });
        nodeList().querySelectorAll('[data-node-op]').forEach(btn => {
          btn.addEventListener('click', () => {
            const idx = parseInt(btn.closest('[data-node-idx]').dataset.nodeIdx, 10);
            const op = btn.dataset.nodeOp;
            if (op === 'del') state.nodes.splice(idx, 1);
            else if (op === 'up' && idx > 0) { [state.nodes[idx - 1], state.nodes[idx]] = [state.nodes[idx], state.nodes[idx - 1]]; }
            else if (op === 'down' && idx < state.nodes.length - 1) { [state.nodes[idx + 1], state.nodes[idx]] = [state.nodes[idx], state.nodes[idx + 1]]; }
            else if (op === 'cfg') { openNodeConfig(state.nodes[idx], renderNodes); return; }
            renderNodes();
          });
        });
      };
      // 节点配置子弹窗
      const openNodeConfig = (node, refresh) => {
        const cfgBox = showModal(`
          <div class="form-row"><label class="form-label">节点名称</label><input type="text" id="ncName" class="form-input" value="${escapeHtml(node.name)}"></div>
          ${node.type === 'input' ? `<div class="form-row"><label class="form-label">输入占位提示</label><input type="text" id="ncCfg" class="form-input" value="${escapeHtml(node.config.placeholder || '')}" placeholder="提示用户输入什么"></div>` : ''}
          ${node.type === 'ai' ? `<div class="form-row"><label class="form-label">提示词模板（{{input}} 为上游输出）</label><textarea id="ncCfg" class="form-textarea" style="min-height:140px">${escapeHtml(node.config.prompt || '{{input}}')}</textarea></div>` : ''}
          ${node.type === 'code' ? `<div class="form-row"><label class="form-label">JS 函数体（参数 text，return 结果）</label><textarea id="ncCfg" class="form-textarea" style="min-height:120px">${escapeHtml(node.config.code || '')}</textarea></div>` : ''}
          ${node.type === 'condition' ? `<div class="form-row"><label class="form-label">条件表达式（变量 text）</label><input type="text" id="ncCfg" class="form-input" value="${escapeHtml(node.config.expression || '')}" placeholder="text.length > 100"></div>` : ''}
          ${node.type === 'loop' ? `<div class="form-row"><label class="form-label">分隔符（\\n 表示按行）</label><input type="text" id="ncCfg" class="form-input" value="${escapeHtml(node.config.split || '\\n')}"></div>` : ''}
          ${node.type === 'output' ? '<div class="text-xs text-text-tertiary">输出节点展示最终结果，无需配置</div>' : ''}`,
          { title: '节点配置 · ' + node.type, icon: 'settings-2', footer: '<button class="btn-primary px-4 py-2 rounded-lg text-sm" id="ncOk">确定</button>' });
        on(cfgBox.querySelector('#ncOk'), 'click', () => {
          node.name = cfgBox.querySelector('#ncName').value || node.name;
          const cfg = cfgBox.querySelector('#ncCfg');
          if (cfg) {
            if (node.type === 'input') node.config.placeholder = cfg.value;
            else if (node.type === 'ai') node.config.prompt = cfg.value;
            else if (node.type === 'code') node.config.code = cfg.value;
            else if (node.type === 'condition') node.config.expression = cfg.value;
            else if (node.type === 'loop') node.config.split = cfg.value.replace(/\\n/g, '\n');
          }
          closeModal(); if (refresh) refresh();
        });
      };
      renderNodes();
      on(box.querySelector('#weSave'), 'click', async () => {
        const name = box.querySelector('#weName').value.trim();
        if (!name) { showNotification('请填写名称', '', 'warning'); return; }
        state.name = name;
        state.description = box.querySelector('#weDesc').value.trim();
        state.schedule = box.querySelector('#weSchedule') ? box.querySelector('#weSchedule').value : 'off';
        if (wf) await DB.updateWorkflow(wf.id, state);
        else await DB.createWorkflow(state);
        closeModal();
        await this.reload();
        showNotification(wf ? '已更新' : '已创建', name, 'success', 1600);
      });
    };
    render();
  }
};
