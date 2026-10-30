'use strict';
/**
 * jev-sample-generator.js —— MoRay 决策内核 JEV 离线样本生成脚本（纯新增工具）
 *
 * 功能：批量构造不同场景的请求，通过 OpenAI 兼容接口调用大模型，
 *       生成符合 DecisionResult 契约的标准决策样本，供离线评估/训练使用。
 *
 * 设计原则：
 *   - 纯离线工具：只「读取」核心模块（L0/L1 用于生成规则基线与契约兜底），
 *     不修改任何现有核心文件，不影响生产决策链路；
 *   - 契约保障：模型输出先做 DecisionResult 结构化校验，非法或缺字段时
 *     自动补齐规则引擎基线，校验彻底失败则整条回退到规则引擎产物；
 *   - 零第三方依赖：仅用 Node 内置 http/https/fs/path/crypto 模块。
 *
 * 场景覆盖（6 类，与基准文档 TC-01 ~ TC-06 同类场景）：
 *   S01 普通文件修改 / S02 生产环境操作 / S03 删除操作
 *   S04 只读操作     / S05 组合场景（含深度推理变体）/ S06 跨Agent操作
 *
 * 用法：
 *   node jev-sample-generator.js                # 完整模式（调用大模型）
 *   node jev-sample-generator.js --dry-run      # 离线模式（不调模型，仅规则基线）
 *   node jev-sample-generator.js --count 5      # 每场景生成 5 条样本
 *
 * 输出：samples/ 目录下每样本一个 JSON 文件，包含：
 *   request（请求）/ world_state（环境状态）/ standard_decision（标准决策结果）
 *   + rule_baseline_decision（规则基线，交叉校验）+ decision_origin（产物来源）
 */

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const { performance } = require('perf_hooks');

// —— 复用核心模块（只调用，不修改）——
const { encodeWorldState } = require('./2-Layer0-状态编码器.js');
const { makeDecision } = require('./3-Layer1-Laya决策层.js');

/* ============================================================
 * 配置项（按需要修改此块）
 * 密钥约定：一律从系统环境变量读取（DEEPSEEK_API_KEY 优先），严禁硬编码到本文件
 * ============================================================ */
const CONFIG = {
  /** OpenAI 兼容接口地址（不含 /chat/completions 后缀） */
  API_BASE_URL: 'https://api.deepseek.com/v1',
  /** API 密钥：仅从环境变量读取，不落盘、不硬编码 */
  API_KEY: process.env.DEEPSEEK_API_KEY || process.env.OPENAI_API_KEY || '',
  /** 调用模型名 */
  MODEL: 'deepseek-chat',
  /** 每个场景生成的样本数量 */
  SAMPLES_PER_SCENARIO: 30,
  /** 跨运行历史去重开关：true = 启动时预载 samples/ 历史请求指纹，已存在的请求直接跳过不调 API（CLI --no-skip-existing 可临时关闭） */
  SKIP_EXISTING: true,
  /** 额度保护：检测到 HTTP 402 / 余额不足类错误时立即中止整轮生成，不再调用、不回退落盘（保证样本纯度与额度止损） */
  QUOTA_GUARD: true,
  /** 模拟模式总开关：false = 真实生成（命令行 --dry-run 可临时覆盖为离线） */
  DRY_RUN: false,
  /** 单条样本模型调用失败后的最大重试次数（首次之外再试 2 次） */
  MAX_RETRY: 2,
  /** 单次请求超时（毫秒） */
  REQUEST_TIMEOUT_MS: 60000,
  /** 采样温度：适当调高增加样本多样性，配合去重避免同质化 */
  TEMPERATURE: 0.85,
  /** 输出目录（不存在会自动创建） */
  OUTPUT_DIR: path.join(__dirname, 'samples'),
};

/* ---------- 命令行参数：--dry-run / --count N / --no-skip-existing / --only S03,S04 ---------- */
const ARGS = process.argv.slice(2);
const DRY_RUN = ARGS.includes('--dry-run') || CONFIG.DRY_RUN === true;
const countIdx = ARGS.indexOf('--count');
if (countIdx >= 0 && Number(ARGS[countIdx + 1]) > 0) {
  CONFIG.SAMPLES_PER_SCENARIO = Number(ARGS[countIdx + 1]);
}
/** 跨运行历史请求预筛开关的实际生效值（CLI 可临时关闭，用于同请求多样本定向补齐场景） */
const SKIP_EXISTING = CONFIG.SKIP_EXISTING !== false && !ARGS.includes('--no-skip-existing');
/** 场景范围过滤：--only S03,S04,S05,S06（缺省 = 全部 6 类场景） */
const onlyIdx = ARGS.indexOf('--only');
const SCENARIO_FILTER = onlyIdx >= 0 && ARGS[onlyIdx + 1]
  ? String(ARGS[onlyIdx + 1]).split(',').map((s) => s.trim().toUpperCase()).filter(Boolean)
  : null;
/* ---------- 真实生成模式启动前校验：必须持有环境变量密钥，否则快速失败不静默降级 ---------- */
let STARTUP_FAILED = false;
if (!DRY_RUN && !CONFIG.API_KEY) {
  console.error('[启动校验失败] 已关闭 dry-run（真实生成模式），但未检测到 API 密钥。');
  console.error('  请设置系统环境变量 DEEPSEEK_API_KEY（或 OPENAI_API_KEY）后重试；');
  console.error('  如需离线规则基线模式，请显式加 --dry-run。');
  STARTUP_FAILED = true;
  process.exitCode = 1;
}

/* ---------- 模拟 Agent 池（与 POC / 测试脚本一致的基准最小样例） ---------- */
const AGENT_POOL = [
  { agent_id: 'code_agent', status: 'healthy' },
  { agent_id: 'confirm_agent', status: 'healthy' },
  { agent_id: 'audit_agent', status: 'healthy' },
];

/* ============================================================
 * 场景库：6 类场景 × 多个请求变体（变体轮换 + 模型采样温度 → 样本多样性）
 *  queries 元素可为字符串，或 { q, options }（显式决策上下文，如深度推理）
 * ============================================================ */
const SCENARIOS = [
  {
    id: 'S01', category: '普通文件修改', name: '低风险本地修改 → 快速路径直出（TC-01 同类）',
    queries: [
      '帮我修改本地的普通代码文件 main.js 的错误处理',
      '调整一下 utils.js 函数的注释格式',
      '修复页面脚本 home.js 里的一个 bug',
      '优化本地配置文件 settings.ini 的格式',
    ],
  },
  {
    id: 'S02', category: '生产环境操作', name: '生产配置变更 → 高风险 + 组合场景升级（TC-02 同类）',
    queries: [
      '帮我修改生产环境的配置文件 nginx.conf',
      '变更生产环境的数据库配置项 db.host',
      '优化线上环境的网关配置 prod-gateway.yaml',
      '调整生产环境 app.env 配置文件的超时参数',
    ],
  },
  {
    id: 'S03', category: '删除操作', name: '删除配置文件 → 「删除+配置」风险升级（TC-03 同类）',
    queries: [
      '删除本地的配置文件 temp.env',
      '删除服务器上的废弃配置 config-old.yaml',
      '清空并删除缓存配置文件 cache.conf',
      '删除项目根目录下的环境配置文件',
    ],
  },
  {
    id: 'S04', category: '只读操作', name: 'read 权限读取生产日志 → 生产风险升级（TC-04 同类）',
    queries: [
      '读取生产环境的日志文件 app.log',
      '查看线上环境的服务运行日志',
      '读取生产环境 error.log 排查异常',
      { q: '读取生产环境的日志文件', options: {} , permission: 'read' },
    ],
  },
  {
    id: 'S05', category: '组合场景', name: '代码修改 × 高风险 / 显式深度推理（TC-05、TC-06 同类）',
    queries: [
      '帮我修改生产环境的配置文件 app.env，风险等级帮我评估',
      { q: '帮我修改生产环境的配置文件，我显式要求深度推理', options: { explicit_deep_reasoning: true } },
      { q: '优化生产环境配置文件的权限配置', options: {} },
      { q: '修改生产环境的配置文件（模拟已蒸馏经验，置信度 0.95）', options: { distilled: true, distilled_confidence: 0.95 } },
    ],
  },
  {
    id: 'S06', category: '跨Agent操作', name: '多 Agent 协作链路（code + confirm + audit 串联）',
    queries: [
      '修改生产环境的配置文件，先经用户确认后再执行并做完整审计',
      '变更线上权限配置，需要代码 Agent、确认 Agent 和审计 Agent 协同处理',
      '调整生产配置文件权限，执行前跨 Agent 确认后落审计日志',
      '修改配置文件 deploy.conf 并走用户确认流程',
    ],
  },
];

/* ============================================================
 * OpenAI 兼容接口调用（Node 内置模块实现，零依赖）
 * ============================================================ */

/** 通用 HTTP JSON 请求（自动区分 http/https） */
function requestJson(urlStr, method, headers, bodyObj, timeoutMs) {
  return new Promise((resolve, reject) => {
    let u;
    try { u = new URL(urlStr); } catch (e) { return reject(new Error('接口地址不合法: ' + urlStr)); }
    const lib = u.protocol === 'https:' ? https : http;
    const payload = bodyObj ? Buffer.from(JSON.stringify(bodyObj), 'utf-8') : null;
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + (u.search || ''),
        method,
        headers: Object.assign(
          { 'Content-Type': 'application/json; charset=utf-8' },
          payload ? { 'Content-Length': payload.length } : {},
          headers || {}
        ),
      },
      (res) => {
        let text = '';
        res.setEncoding('utf-8');
        res.on('data', (c) => (text += c));
        res.on('end', () => {
          let json = null;
          try { json = JSON.parse(text); } catch (_) { /* 非 JSON 响应保留原文 */ }
          resolve({ status: res.statusCode, json, text });
        });
      }
    );
    req.setTimeout(timeoutMs, () => req.destroy(new Error('请求超时 ' + timeoutMs + 'ms')));
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

/** 组装给模型的提示词（契约约束与生产规则引擎同源） */
const SYSTEM_PROMPT = [
  '你是 MoRay 应用层 AGI 决策内核 v1.0 的决策样本生成器。根据用户请求与世界状态（Layer 0 编码结果），产出一条符合 DecisionResult 契约的标准决策结果。',
  '【输出要求】只输出一个 JSON 对象，禁止 markdown 代码块与任何解释文字。顶层字段必须完整：',
  'decision_id, input_snapshot{query,world_state_id,context_id,timestamp},',
  'output{target_path,agent_chain[],param_mapping{},step_sequence[],expected_outcome,verification_points[],fallback_plan{condition,action,max_retry}},',
  'quality{confidence,complexity_score,risk_level,is_composite_scenario,composite_type},',
  'upgrade{should_upgrade,trigger_types[],trigger_reasons[],upgrade_priority},',
  'explainability{matched_meta_rules[],matched_domain_experience[],reasoning_summary,counterfactual_note},',
  'meta_consistency{constitution_alignment,deviation_note,from_meta_core},source,latency_ms,timestamp,version{meta_core,domain_adapter,world_encoder}',
  '【枚举约束】risk_level ∈ low|medium|high|critical；upgrade_priority ∈ none|P2|P1|P0|urgent；',
  'trigger_types ∈ risk|composite_scenario|complexity|low_confidence|deep_reasoning；step_type ∈ intent_analysis|execution|user_confirm|audit。',
  'step_sequence 元素结构 {step_id,step_type,agent_call{agent_id,params,order,timeout_ms},description,verification}；agent_chain 为其 agent_call 按序展开。',
  '【决策规则（必须遵守，与生产规则引擎同源）】',
  '1. 风险校验：请求含「生产/线上」→ high；「删除/权限/配置」每命中一词风险升一级（封顶 critical）；high/critical 必触发 risk 升级维度；',
  '2. 路径生成：普通 2 步（意图解析 + 执行）；高风险追加 user_confirm 步；生产环境追加 audit 步；',
  '3. 复杂度：步骤数 ≥ 3 → 触发 complexity 升级维度；',
  '4. 组合场景：代码/文件修改 × 高风险 → is_composite_scenario=true，confidence=0.72 并触发 composite_scenario 与 low_confidence；否则 confidence=0.9；',
  '5. 蒸馏场景：风险/组合/复杂度触发降级为仅记录，是否升级只看置信度是否 ≥ 0.8；',
  '6. decision_id 形如 dec-<毫秒时间戳>-<6位十六进制>；source="JEV-LLM-Sample-v1.0"；meta_consistency.from_meta_core=true。',
].join('\n');

function buildUserPrompt(query, permission, worldState) {
  return [
    '用户请求：' + query,
    '用户权限：' + permission,
    '世界状态（L0 编码结果）：' + JSON.stringify({
      operation_risk_tag: worldState.operation_risk_tag,
      user_permission_level: worldState.user_permission_level,
      agent_availability: worldState.agent_availability,
    }),
    '请按契约输出 DecisionResult JSON。',
  ].join('\n');
}

/** 调用一次 chat/completions，返回模型消息文本 */
async function chatOnce(messages, useResponseFormat) {
  const body = {
    model: CONFIG.MODEL,
    messages,
    temperature: CONFIG.TEMPERATURE,
  };
  if (useResponseFormat) body.response_format = { type: 'json_object' };
  const r = await requestJson(
    CONFIG.API_BASE_URL.replace(/\/+$/, '') + '/chat/completions',
    'POST',
    { Authorization: 'Bearer ' + CONFIG.API_KEY },
    body,
    CONFIG.REQUEST_TIMEOUT_MS
  );
  const content = r.json && r.json.choices && r.json.choices[0] && r.json.choices[0].message && r.json.choices[0].message.content;
  if (r.status !== 200 || !content) {
    const detail = (r.json && (r.json.error && r.json.error.message || r.json.message)) || (r.text || '').slice(0, 200);
    const err = new Error('HTTP ' + r.status + ' ' + (detail || '空响应'));
    err.status = r.status;
    throw err;
  }
  return content;
}

/** 从模型输出中提取平衡的 JSON 对象（容忍 markdown 围栏与前后杂文） */
function extractJson(text) {
  const s = String(text || '');
  const fenced = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const body = fenced ? fenced[1] : s;
  const start = body.indexOf('{');
  if (start < 0) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < body.length; i++) {
    const ch = body[i];
    if (inStr) {
      if (esc) esc = false;
      else if (ch === '\\') esc = true;
      else if (ch === '"') inStr = false;
      continue;
    }
    if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        try { return JSON.parse(body.slice(start, i + 1)); } catch (_) { return null; }
      }
    }
  }
  return null;
}

/* ============================================================
 * DecisionResult 契约校验与归一化
 * ============================================================ */
const RISK_SET = new Set(['low', 'medium', 'high', 'critical']);
const PRIORITY_SET = new Set(['none', 'P2', 'P1', 'P0', 'urgent']);
const TRIGGER_SET = new Set(['risk', 'composite_scenario', 'complexity', 'low_confidence', 'deep_reasoning']);
const STEP_TYPE_SET = new Set(['intent_analysis', 'execution', 'user_confirm', 'audit']);

/** 结构 + 枚举校验：合法返回 null，非法返回原因字符串 */
function validateDecision(d) {
  if (!d || typeof d !== 'object' || Array.isArray(d)) return '不是 JSON 对象';
  if (!d.input_snapshot || typeof d.input_snapshot.query !== 'string') return 'input_snapshot.query 缺失';
  if (!d.output || !Array.isArray(d.output.step_sequence) || d.output.step_sequence.length === 0) return 'output.step_sequence 缺失/为空';
  if (!Array.isArray(d.output.agent_chain) || d.output.agent_chain.length === 0) return 'output.agent_chain 缺失/为空';
  if (!d.output.step_sequence.every((s) => STEP_TYPE_SET.has(s.step_type))) return 'step_type 含非法枚举';
  if (!d.quality || !RISK_SET.has(d.quality.risk_level)) return 'quality.risk_level 非法';
  if (typeof d.quality.confidence !== 'number' || d.quality.confidence < 0 || d.quality.confidence > 1) return 'quality.confidence 非法';
  if (!d.upgrade || typeof d.upgrade.should_upgrade !== 'boolean') return 'upgrade.should_upgrade 缺失';
  if (!Array.isArray(d.upgrade.trigger_types) || !d.upgrade.trigger_types.every((t) => TRIGGER_SET.has(t))) return 'upgrade.trigger_types 含非法枚举';
  if (!PRIORITY_SET.has(d.upgrade.upgrade_priority)) return 'upgrade.upgrade_priority 非法';
  if (!d.explainability || !Array.isArray(d.explainability.matched_meta_rules)) return 'explainability.matched_meta_rules 缺失';
  return null;
}

const isPlainObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** 把模型产物逐字段并入规则基线深拷贝（基线保证缺失子字段有合法默认值） */
function mergeOntoBaseline(raw, baseline) {
  const out = JSON.parse(JSON.stringify(baseline));
  const assign = (target, src) => {
    for (const k of Object.keys(src)) {
      if (isPlainObj(target[k]) && isPlainObj(src[k])) assign(target[k], src[k]);
      else if (src[k] !== undefined) target[k] = src[k];
    }
  };
  assign(out, raw);
  return out;
}

/* ============================================================
 * 去重指纹：剔除时间戳/随机 id 等非内容字段后取 sha256
 * ============================================================ */
function dedupHash(query, permission, options, decision) {
  const d = JSON.parse(JSON.stringify(decision));
  delete d.decision_id;
  delete d.timestamp;
  delete d.latency_ms;
  if (isPlainObj(d.input_snapshot)) {
    delete d.input_snapshot.world_state_id;
    delete d.input_snapshot.context_id;
    delete d.input_snapshot.timestamp;
  }
  const canonical = JSON.stringify({ query, permission, options: options || {}, decision: d });
  return crypto.createHash('sha256').update(canonical, 'utf-8').digest('hex').slice(0, 16);
}

/** 请求内容指纹：query + permission + options（不含决策结果），用于跨运行历史请求预筛 */
function requestKeyHash(query, permission, options) {
  const canonical = JSON.stringify({ query, permission, options: options || {} });
  return crypto.createHash('sha256').update(canonical, 'utf-8').digest('hex').slice(0, 16);
}

/**
 * 跨运行历史指纹预载：扫描 samples/ 下全部现有样本，
 *   ① 提取各样本 request 的请求内容指纹 → historyReqKeys（调用 API 前预筛跳过）；
 *   ② 提取各样本的决策内容指纹（优先读 dedup_fingerprint 字段）→ 注入运行内去重池 seen，
 *     使原有「生成后去重」逻辑天然获得跨运行能力（--no-skip-existing 补齐同请求多样本时仍能防重复落盘）。
 */
function loadHistoryPools(dir) {
  const historyReqKeys = new Set();
  const historyDecisionFps = new Set();
  if (!fs.existsSync(dir)) return { historyReqKeys, historyDecisionFps, files: 0 };
  let files = 0;
  for (const f of fs.readdirSync(dir)) {
    if (!f.toLowerCase().endsWith('.json')) continue;
    try {
      const s = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf-8'));
      files++;
      if (s.request && typeof s.request.query === 'string') {
        historyReqKeys.add(requestKeyHash(s.request.query, s.request.permission || 'write', s.request.options));
        if (isPlainObj(s.standard_decision)) {
          historyDecisionFps.add(s.dedup_fingerprint || dedupHash(s.request.query, s.request.permission || 'write', s.request.options, s.standard_decision));
        }
      } else if (typeof s.dedup_fingerprint === 'string') {
        historyDecisionFps.add(s.dedup_fingerprint);
      }
    } catch (_) { /* 无法解析的历史文件不阻塞启动，交由质检脚本暴露 */ }
  }
  return { historyReqKeys, historyDecisionFps, files };
}

/* ============================================================
 * 主流程
 * ============================================================ */
async function generateOne(scenario, seq, total, counter) {
  // 解析场景变体：字符串 或 { q, options, permission }
  const raw = scenario.queries[(seq - 1) % scenario.queries.length];
  const item = typeof raw === 'string' ? { q: raw } : raw;
  const query = item.q;
  const permission = item.permission || 'write';
  const options = item.options || {};

  // 跨运行历史请求预筛（skip_existing）：已存在的请求直接跳过，不调 API、不产生费用
  const reqKey = requestKeyHash(query, permission, options);
  if (SKIP_EXISTING && counter.historyReqKeys.has(reqKey)) {
    counter.skippedExisting++;
    console.log(`[${counter.done()}/${total}] ${scenario.id} ${scenario.category} | ≡ 历史请求已存在，跳过（skip_existing，未调用 API，指纹 ${reqKey}）`);
    return;
  }

  // 第一段：本地全链路 → 规则引擎基线（恒定执行，用于契约兜底与交叉校验）
  const worldState = encodeWorldState(query, AGENT_POOL, permission);
  const baseline = makeDecision(query, worldState, options);

  // 第二段：调用大模型生成契约决策（含失败重试 2 次；非法产物回退基线）
  let standard = baseline;
  let origin = DRY_RUN ? 'rule-engine-dryrun' : 'rule-engine-fallback';
  let llmMeta = null;
  if (!DRY_RUN) {
    const messages = [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: buildUserPrompt(query, permission, worldState) },
    ];
    let useRespFormat = true;
    for (let attempt = 1; attempt <= CONFIG.MAX_RETRY + 1; attempt++) {
      const t0 = performance.now();
      try {
        const content = await chatOnce(messages, useRespFormat);
        const parsed = extractJson(content);
        const invalid = parsed ? validateDecision(parsed) : '无法解析 JSON';
        if (invalid) throw new Error('契约校验失败：' + invalid);
        standard = mergeOntoBaseline(parsed, baseline);
        origin = 'llm';
        llmMeta = { model: CONFIG.MODEL, attempts: attempt, latency_ms: Number((performance.now() - t0).toFixed(1)) };
        counter.llmOk++;
        break;
      } catch (err) {
        // 额度不足熔断：QUOTA_GUARD 开启时，402/余额类错误立即中止整轮生成（该样本不回退规则引擎、不落盘，保证纯 LLM 样本）
        if (CONFIG.QUOTA_GUARD && (err.status === 402 || /insufficient balance|quota|arrears|balance not enough/i.test(String((err && err.message) || '')))) {
          counter.quotaBlocked = true;
          console.log(`        ⛔ 接口额度不足：${err.message}`);
          console.log('        ⛔ QUOTA_GUARD 已开启 → 本轮生成立即中止（本样本不回退规则引擎、不落盘）');
          return;
        }
        if (err.status === 400 && String(err.message).includes('response_format')) useRespFormat = false; // 端点不支持 JSON 模式 → 降级重试
        if (attempt <= CONFIG.MAX_RETRY) {
          console.log(`        ↻ 第 ${attempt} 次调用失败（${err.message}），自动重试 ${attempt}/${CONFIG.MAX_RETRY} …`);
        } else {
          console.log(`        ✗ 模型调用失败（${err.message}），本样本回退规则引擎产物`);
          counter.fallback++;
        }
      }
    }
  }

  // 第三段：去重 + 落盘
  const hash = dedupHash(query, permission, options, standard);
  if (counter.seen.has(hash)) {
    counter.dedup++;
    console.log(`[${counter.done()}/${total}] ${scenario.id} ${scenario.category} | ≡ 去重丢弃（与已有样本决策内容相同，指纹 ${hash}）`);
    return;
  }
  counter.seen.add(hash);

  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const fileName = `jev-sample-${scenario.id}-${String(seq).padStart(3, '0')}-${stamp}.json`;
  const sample = {
    sample_id: 'jev-' + hash + '-' + stamp,
    task: 'MoRay 决策内核 v1.0 JEV 离线决策样本',
    contract_version: 'DecisionResult v1.0.0',
    generated_at: new Date().toISOString(),
    scenario: { id: scenario.id, category: scenario.category, name: scenario.name },
    request: { query, permission, options, agent_pool: AGENT_POOL },   // 请求
    world_state: worldState,                                            // 环境状态（L0 编码）
    standard_decision: standard,                                        // 标准决策结果（契约样本）
    decision_origin: origin,                                            // 产物来源：llm / rule-engine-fallback / rule-engine-dryrun
    dedup_fingerprint: hash,
    rule_baseline_decision: baseline,                                   // 规则引擎基线（交叉校验参照）
    llm_meta: llmMeta,
  };
  fs.writeFileSync(path.join(CONFIG.OUTPUT_DIR, fileName), JSON.stringify(sample, null, 2), 'utf-8');
  counter.saved++;
  console.log(
    `[${counter.done()}/${total}] ${scenario.id} ${scenario.category} | ✓ ${fileName}` +
    ` 来源=${origin} 风险=${standard.quality.risk_level}` +
    ` 升级=${standard.upgrade.should_upgrade ? '是(' + standard.upgrade.upgrade_priority + ')' : '否'}` +
    ` 步骤=${standard.output.step_sequence.length} 置信度=${standard.quality.confidence}`
  );
}

async function main() {
  const line = '='.repeat(64);
  console.log(line);
  console.log('  MoRay 决策内核 JEV 离线样本生成器 v1.0');
  console.log(line);
  console.log(`  模式     : ${DRY_RUN ? '离线 dry-run（不调用模型，样本取规则引擎产物）' : '在线（OpenAI 兼容接口）'}`);
  console.log(`  接口     : ${CONFIG.API_BASE_URL}/chat/completions`);
  console.log(`  模型     : ${CONFIG.MODEL} | 温度 ${CONFIG.TEMPERATURE} | 失败重试 ${CONFIG.MAX_RETRY} 次 | 额度熔断 ${CONFIG.QUOTA_GUARD ? '开（402 即中止）' : '关'}`);
  console.log(`  历史去重 : skip_existing = ${SKIP_EXISTING ? '开（历史请求跳过不调 API）' : '关（--no-skip-existing，同请求可再采样，决策指纹仍防重复落盘）'}`);

  fs.mkdirSync(CONFIG.OUTPUT_DIR, { recursive: true }); // 自动创建 samples 文件夹

  // 场景范围过滤（--only S03,S04,...）：未指定则全部 6 类
  let activeScenarios = SCENARIOS;
  if (SCENARIO_FILTER) {
    const known = new Set(SCENARIOS.map((s) => s.id));
    const unknown = SCENARIO_FILTER.filter((id) => !known.has(id));
    if (unknown.length > 0) console.warn(`[警告] --only 中的未知场景编号已忽略：${unknown.join(', ')}`);
    activeScenarios = SCENARIOS.filter((s) => SCENARIO_FILTER.includes(s.id));
    if (activeScenarios.length === 0) {
      console.error('[启动校验失败] --only 过滤后没有任何可生成场景。');
      process.exitCode = 1;
      return;
    }
  }
  console.log(`  场景     : ${activeScenarios.map((s) => `${s.id}(${s.category})`).join(' ')} × 每场景 ${CONFIG.SAMPLES_PER_SCENARIO} 条 = ${activeScenarios.length * CONFIG.SAMPLES_PER_SCENARIO} 条候选`);

  // 跨运行历史指纹预载（先于任何生成执行）
  const history = loadHistoryPools(CONFIG.OUTPUT_DIR);
  console.log(`  历史库   : 预载 samples/ 现有样本 ${history.files} 条 → 请求指纹 ${history.historyReqKeys.size} 个、决策指纹 ${history.historyDecisionFps.size} 个`);
  console.log(`  输出目录 : ${CONFIG.OUTPUT_DIR}`);
  console.log(line + '\n');

  const counter = {
    i: 0,
    seen: history.historyDecisionFps,          // 单轮运行内去重逻辑不变，仅把历史决策指纹注入池中 → 跨运行去重
    historyReqKeys: history.historyReqKeys,    // 跨运行历史请求指纹（调用 API 前预筛）
    saved: 0,
    dedup: 0,
    llmOk: 0,
    fallback: 0,
    skippedExisting: 0,                        // 历史已存在跳过计数（未调用 API）
    quotaBlocked: false,                       // 额度熔断标志
    done() { return this.i; },
  };
  const total = activeScenarios.length * CONFIG.SAMPLES_PER_SCENARIO;
  const t0 = performance.now();

  // 顺序执行：进度实时、日志整齐；如需提速可自行改为并发池
  for (const scenario of activeScenarios) {
    if (counter.quotaBlocked) break;
    for (let seq = 1; seq <= CONFIG.SAMPLES_PER_SCENARIO; seq++) {
      if (counter.quotaBlocked) break;
      counter.i++;
      // 每 10 条输出一次剩余候选预估（未处理候选数；历史跳过数单列，不重复计入）
      if (counter.i % 10 === 1) {
        console.log(`  ┄┄ 进度预估：候选 ${counter.i - 1}/${total} 已处理 | 历史跳过 ${counter.skippedExisting} | 剩余候选至多 ${total - (counter.i - 1)} 次待处理`);
      }
      await generateOne(scenario, seq, total, counter);
    }
  }

  console.log('\n' + line);
  console.log('【生成统计】');
  console.log(`  候选任务     : ${total}`);
  console.log(`  历史已存在跳过 : ${counter.skippedExisting}（skip_existing 预筛，未调用 API${SKIP_EXISTING ? '' : '——本轮开关为关，恒为 0'}）`);
  console.log(`  本次新增落盘 : ${counter.saved}`);
  console.log(`  去重丢弃     : ${counter.dedup}（单轮运行内去重不变 + 历史决策指纹跨运行命中）`);
  if (!DRY_RUN) {
    console.log(`  模型产物     : ${counter.llmOk} | 规则回退 : ${counter.fallback}`);
  }
  console.log(`  总耗时       : ${((performance.now() - t0) / 1000).toFixed(1)} s`);
  console.log(`  输出目录     : ${CONFIG.OUTPUT_DIR}`);
  if (counter.quotaBlocked) {
    console.log('');
    console.log('  ⛔ 本轮生成因接口额度不足（HTTP 402）被 QUOTA_GUARD 提前中止，剩余候选未执行；请充值后重跑补齐。');
    process.exitCode = 1;
  }
  console.log(line);
}

if (!STARTUP_FAILED) {
  main().catch((err) => {
    console.error('\n[样本生成异常]', err && err.stack ? err.stack : String(err));
    process.exitCode = 1;
  });
}
