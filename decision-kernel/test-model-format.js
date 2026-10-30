'use strict';
/**
 * test-model-format.js —— 本地 Ollama 小模型「结构化决策输出能力」验证脚本（独立旁路工具）
 *
 * 定位：零侵入的旁路评测脚本。
 *   - 不依赖、不修改任何现有核心决策文件（1~3 号模块 / run-poc / run-all-tests 均不参与本脚本运行）；
 *   - 规则基线结果按基准文档 TC-01 ~ TC-06 的预期核心字段值硬编码为常量，供逐字段比对；
 *   - 通过 OpenAI 兼容接口调用本地 Ollama（http://localhost:11434/v1），模型：qwen2.5:1.5b；
 *   - 专用决策 Prompt 要求模型严格按 MoRay DecisionResult 契约输出标准 JSON，
 *     核心字段：risk_level / is_composite_scenario / should_upgrade / step_count / confidence。
 *
 * 输出内容：
 *   每条用例：模型原始输出 → 解析结果 → 规则基线 → 五字段对齐比对；
 *   末尾汇总：总用例数、结构化成功率、核心字段平均对齐率。
 *
 * 运行前提：本地已启动 Ollama 并拉取模型：ollama pull qwen2.5:1.5b
 * 运行方式：node test-model-format.js
 * 纯 Node 内置能力（global fetch），零第三方依赖。
 */

/* ============================================================
 * 调用配置
 * ============================================================ */
const OLLAMA_BASE_URL = 'http://localhost:11434/v1'; // OpenAI 兼容接口地址
const OLLAMA_MODEL = 'qwen2.5:1.5b';                 // 被测本地小模型
const REQUEST_TIMEOUT_MS = 120000;                   // 单次调用超时上限（小模型 CPU 推理留足余量）

/* ============================================================
 * MoRay DecisionResult 核心字段契约（Prompt 与校验共用）
 * ============================================================ */
const RISK_LEVELS = ['low', 'medium', 'high', 'critical'];

/** 契约字段清单：字段名 → 合法类型描述（用于结构化输出校验） */
const CONTRACT_FIELDS = [
  { key: 'risk_level',            type: 'string，枚举：low / medium / high / critical' },
  { key: 'is_composite_scenario', type: 'boolean' },
  { key: 'should_upgrade',        type: 'boolean' },
  { key: 'step_count',            type: '整数（≥1），执行步骤数' },
  { key: 'confidence',            type: '小数，范围 [0,1]' },
];

/* ============================================================
 * 6 条标准用例（TC-01 ~ TC-06）+ 规则基线
 * baseline 取自基准文档 v1.0 规则引擎（L0 风险编码 + L1 四维升级触发）
 * 对各场景的既定输出：risk_level / is_composite_scenario / should_upgrade / step_count / confidence
 * ============================================================ */
const TEST_CASES = [
  {
    id: 'TC-01',
    name: '修改本地普通代码文件（write 权限）',
    query: '帮我修改本地的普通代码文件',
    permission: 'write',
    contextNote: '无扩展上下文',
    baseline: { risk_level: 'low', is_composite_scenario: false, should_upgrade: false, step_count: 2, confidence: 0.9 },
  },
  {
    id: 'TC-02',
    name: '修改生产环境配置文件（write 权限）',
    query: '帮我修改生产环境的配置文件',
    permission: 'write',
    contextNote: '无扩展上下文',
    baseline: { risk_level: 'critical', is_composite_scenario: true, should_upgrade: true, step_count: 4, confidence: 0.72 },
  },
  {
    id: 'TC-03',
    name: '删除本地配置文件（write 权限）',
    query: '删除本地的配置文件',
    permission: 'write',
    contextNote: '无扩展上下文',
    baseline: { risk_level: 'high', is_composite_scenario: false, should_upgrade: true, step_count: 3, confidence: 0.9 },
  },
  {
    id: 'TC-04',
    name: '读取生产环境日志文件（read 权限）',
    query: '读取生产环境的日志文件',
    permission: 'read',
    contextNote: '无扩展上下文',
    baseline: { risk_level: 'high', is_composite_scenario: false, should_upgrade: true, step_count: 4, confidence: 0.9 },
  },
  {
    id: 'TC-05',
    name: '修改生产环境配置 + 显式要求深度推理（write 权限）',
    query: '帮我修改生产环境的配置文件，我显式要求深度推理',
    permission: 'write',
    contextNote: '用户显式要求深度推理（META-DEEP-01）',
    baseline: { risk_level: 'critical', is_composite_scenario: true, should_upgrade: true, step_count: 4, confidence: 0.72 },
  },
  {
    id: 'TC-06',
    name: '修改生产环境配置 · 已蒸馏场景（write 权限）',
    query: '帮我修改生产环境的配置文件',
    permission: 'write',
    contextNote: '本场景为已蒸馏场景，蒸馏产物置信度 = 0.95（META-DIST-01：高置信直出，不升级）',
    baseline: { risk_level: 'critical', is_composite_scenario: true, should_upgrade: false, step_count: 4, confidence: 0.95 },
  },
];

/* ============================================================
 * 专用决策 Prompt
 * 把基准文档的最小判定规则压缩进 System 指令，
 * 并强约束「只输出一个 JSON 对象、固定五个核心字段」
 * ============================================================ */
function buildSystemPrompt() {
  return [
    '你是 MoRay 决策内核的快速决策器 Laya。请对给定操作请求做出结构化决策，',
    '并严格按照 MoRay DecisionResult 契约输出结果。',
    '',
    '【输出契约（硬性要求）】',
    '只输出一个 JSON 对象，禁止任何解释文字、禁止 Markdown 代码块标记。JSON 必须且只能包含以下 5 个字段：',
    '{"risk_level": "low|medium|high|critical 四选一",',
    ' "is_composite_scenario": true或false,',
    ' "should_upgrade": true或false,',
    ' "step_count": 正整数,',
    ' "confidence": 0到1之间的小数}',
    '',
    '【判定规则（必须遵守）】',
    '1. 风险等级 risk_level：基础为 low；请求含「生产/生产环境/线上」→ high；',
    '   关键词「删除」「权限」「配置」每命中一个，在现有等级上再升一级（封顶 critical）。',
    '2. 组合场景 is_composite_scenario：同时满足「代码/配置文件被修改」且 risk_level 为 high/critical → true，否则 false。',
    '3. 步骤数 step_count：基础 2 步（意图解析 + 执行）；高风险追加 1 步用户确认；生产环境追加 1 步审计。',
    '4. 置信度 confidence：组合场景 0.72；非组合场景 0.9；若上下文声明为已蒸馏场景，则直接取声明的蒸馏置信度。',
    '5. 是否升级 should_upgrade：以下任一命中 → true：',
    '   ① risk_level 为 high/critical；② 组合场景；③ step_count ≥ 3；④ confidence < 0.8；',
    '   ⑤ 上下文声明用户显式要求深度推理。',
    '   例外：若上下文声明为已蒸馏场景，则 ①②③ 降级为仅记录、不触发升级，仅当 confidence < 0.8 或命中 ⑤ 时才为 true。',
  ].join('\n');
}

function buildUserPrompt(tc) {
  return [
    '请对以下操作请求做出决策：',
    '用户请求：' + tc.query,
    '用户权限：' + tc.permission,
    '决策上下文：' + tc.contextNote,
    '再次强调：只输出一个包含 risk_level / is_composite_scenario / should_upgrade / step_count / confidence 五个字段的 JSON 对象。',
  ].join('\n');
}

/* ============================================================
 * Ollama OpenAI 兼容接口调用（Node 内置 fetch，零依赖）
 * ============================================================ */
async function callOllama(userPrompt) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const resp = await fetch(OLLAMA_BASE_URL + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: OLLAMA_MODEL,
        temperature: 0, // 贪婪解码，降低输出随机性
        stream: false,
        messages: [
          { role: 'system', content: buildSystemPrompt() },
          { role: 'user', content: userPrompt },
        ],
      }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const bodyText = await resp.text().catch(() => '');
      throw new Error(describeHttpError(resp.status, bodyText));
    }
    const data = await resp.json();
    const content = data && data.choices && data.choices[0] && data.choices[0].message
      ? String(data.choices[0].message.content || '')
      : '';
    if (!content) throw new Error('接口返回 200 但 choices[0].message.content 为空');
    return content;
  } catch (err) {
    if (err && err.name === 'AbortError') {
      throw new Error(`调用超时（>${Math.round(REQUEST_TIMEOUT_MS / 1000)}s）：小模型推理过慢或服务无响应`);
    }
    if (err && err.cause && /ECONNREFUSED|ENOTFOUND|UND_ERR_CONNECT/i.test(String(err.cause.code || err.cause.message || ''))) {
      throw new Error(`无法连接 Ollama 服务 ${OLLAMA_BASE_URL}，请先确认已运行「ollama serve」`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** 将 HTTP 状态码 + 响应体翻译为明确的人类可读提示 */
function describeHttpError(status, bodyText) {
  const snip = (bodyText || '').slice(0, 200).replace(/\s+/g, ' ');
  if (status === 404) return `HTTP 404：模型「${OLLAMA_MODEL}」不存在，请先执行「ollama pull ${OLLAMA_MODEL}」（响应：${snip}）`;
  if (status === 400 || status === 422) return `HTTP ${status}：请求体或参数不被接受（响应：${snip}）`;
  if (status === 429) return 'HTTP 429：请求频率超限，请降低并发或稍后重试';
  if (status >= 500) return `HTTP ${status}：Ollama 服务端错误（响应：${snip}）`;
  return `HTTP ${status}：接口调用失败（响应：${snip}）`;
}

/** 服务预检：模型列表接口可达性 + 被测模型是否已拉取 */
async function preflightCheck() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const resp = await fetch(OLLAMA_BASE_URL + '/models', { signal: controller.signal });
    if (!resp.ok) throw new Error('预检返回 HTTP ' + resp.status);
    const data = await resp.json();
    const ids = (data.data || []).map((m) => m.id || '');
    const found = ids.some((id) => id === OLLAMA_MODEL || id.startsWith(OLLAMA_MODEL));
    if (!found) {
      console.warn(`[预检警告] Ollama 服务可达，但模型列表中没有「${OLLAMA_MODEL}」，` +
        `建议先执行：ollama pull ${OLLAMA_MODEL}（仍将继续尝试调用）`);
    }
    return true;
  } catch (err) {
    const cause = err && err.cause && (err.cause.code || err.cause.message) ? `（底层原因：${err.cause.code || err.cause.message}）` : '';
    throw new Error(
      `Ollama 服务预检失败：${err && err.message ? err.message : String(err)}${cause}\n` +
      `  请确认：① 已启动 Ollama（ollama serve）；② 服务地址为 ${OLLAMA_BASE_URL}；` +
      `③ 已拉取模型（ollama pull ${OLLAMA_MODEL}）`
    );
  } finally {
    clearTimeout(timer);
  }
}

/* ============================================================
 * 模型输出解析：抽取 JSON 对象 + 契约字段校验
 * ============================================================ */
/** 从自由文本中抽取第一个完整 JSON 对象（容忍代码块围栏与前后杂句） */
function extractJsonObject(text) {
  const start = text.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) {
        const candidate = text.slice(start, i + 1);
        try { return JSON.parse(candidate); } catch (_) { return null; }
      }
    }
  }
  return null;
}

/** 宽松类型归一：把小模型常见的字符串化布尔/数字转回原生类型 */
function coerceField(key, value) {
  switch (key) {
    case 'risk_level': {
      const v = String(value || '').trim().toLowerCase();
      return RISK_LEVELS.includes(v) ? v : null;
    }
    case 'is_composite_scenario':
    case 'should_upgrade': {
      if (typeof value === 'boolean') return value;
      const v = String(value).trim().toLowerCase();
      if (v === 'true' || v === '1' || v === 'yes') return true;
      if (v === 'false' || v === '0' || v === 'no') return false;
      return null;
    }
    case 'step_count': {
      const n = Number(value);
      return Number.isFinite(n) && n >= 1 ? Math.round(n) : null;
    }
    case 'confidence': {
      const n = Number(value);
      return Number.isFinite(n) && n >= 0 && n <= 1 ? n : null;
    }
    default:
      return null;
  }
}

/**
 * 解析 + 契约校验：返回 { ok, fields, missing }
 * fields 为归一后的 { 字段名: 值 }，missing 为缺失/非法字段清单
 */
function parseDecisionOutput(rawText) {
  const obj = extractJsonObject(rawText);
  if (!obj || typeof obj !== 'object') {
    return { ok: false, fields: null, missing: CONTRACT_FIELDS.map((f) => f.key), reason: '输出中未找到可解析的 JSON 对象' };
  }
  const fields = {};
  const missing = [];
  for (const f of CONTRACT_FIELDS) {
    const coerced = coerceField(f.key, obj[f.key]);
    if (coerced === null || coerced === undefined) missing.push(f.key);
    else fields[f.key] = coerced;
  }
  return { ok: missing.length === 0, fields, missing, reason: missing.length > 0 ? '字段缺失或取值非法：' + missing.join(', ') : '' };
}

/* ============================================================
 * 核心字段对齐比对
 * 前四字段严格相等；confidence 允许 ±0.1 容差（模型估值近似）
 * ============================================================ */
function compareWithBaseline(fields, baseline) {
  const rows = [];
  let alignedCount = 0;
  const check = (key, modelVal, baseVal, aligned) => {
    if (aligned) alignedCount++;
    rows.push({ key, model: String(modelVal), base: String(baseVal), aligned });
  };
  check('risk_level', fields.risk_level, baseline.risk_level, fields.risk_level === baseline.risk_level);
  check('is_composite_scenario', fields.is_composite_scenario, baseline.is_composite_scenario, fields.is_composite_scenario === baseline.is_composite_scenario);
  check('should_upgrade', fields.should_upgrade, baseline.should_upgrade, fields.should_upgrade === baseline.should_upgrade);
  check('step_count', fields.step_count, baseline.step_count, fields.step_count === baseline.step_count);
  check('confidence', fields.confidence, baseline.confidence, Math.abs(fields.confidence - baseline.confidence) <= 0.1);
  return { rows, alignedCount, total: CONTRACT_FIELDS.length };
}

/* ============================================================
 * 单条用例执行与打印
 * ============================================================ */
async function runCase(tc) {
  const sep = '  ' + '-'.repeat(58);
  console.log(sep);
  console.log(`【${tc.id}】${tc.name}`);
  console.log(`  输入请求   : ${tc.query}`);
  console.log(`  用户权限   : ${tc.permission}`);
  console.log(`  决策上下文 : ${tc.contextNote}`);

  const record = { tc, structuredOk: false, alignedCount: 0, total: CONTRACT_FIELDS.length, error: null };

  const t0 = Date.now();
  let raw;
  try {
    raw = await callOllama(buildUserPrompt(tc));
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    record.error = msg;
    console.log(`  接口调用   : ✗ 失败（${msg}）`);
    console.log(`  用例判定   : 结构化输出失败（API 异常）`);
    return record;
  }
  const costMs = Date.now() - t0;
  console.log(`  调用耗时   : ${costMs} ms`);

  console.log('  模型原始输出:');
  console.log('    ┌' + '─'.repeat(56));
  for (const line of String(raw).split('\n')) console.log('    │ ' + line);
  console.log('    └' + '─'.repeat(56));

  const parsed = parseDecisionOutput(raw);
  if (!parsed.ok) {
    record.error = parsed.reason;
    console.log(`  结构化校验 : ✗ 不通过（${parsed.reason}）`);
    console.log('  规则基线   : ' + formatBaseline(tc.baseline));
    console.log(`  用例判定   : 结构化输出失败，核心字段对齐计 0/${record.total}`);
    return record;
  }

  console.log('  结构化校验 : ✓ 通过（五字段齐全且取值合法）');
  console.log('  模型解析   : ' + formatBaseline(parsed.fields));
  console.log('  规则基线   : ' + formatBaseline(tc.baseline));

  const cmp = compareWithBaseline(parsed.fields, tc.baseline);
  for (const r of cmp.rows) {
    console.log(`    ${r.aligned ? '✓' : '✗'} ${r.key.padEnd(22)} 模型=${r.model.padEnd(9)} 基线=${r.base}`);
  }
  console.log(`  字段对齐   : ${cmp.alignedCount}/${cmp.total}`);
  console.log(`  用例判定   : ${cmp.alignedCount === cmp.total ? '完全对齐' : '存在偏差'}`);

  record.structuredOk = true;
  record.alignedCount = cmp.alignedCount;
  return record;
}

function formatBaseline(b) {
  return `风险=${b.risk_level} | 组合=${b.is_composite_scenario} | 升级=${b.should_upgrade} | 步骤=${b.step_count} | 置信度=${b.confidence}`;
}

/* ============================================================
 * 主流程 + 汇总统计
 * ============================================================ */
async function main() {
  const line = '='.repeat(64);
  console.log(line);
  console.log('  MoRay 决策内核 —— Ollama 小模型结构化决策输出验证（TC-01 ~ TC-06）');
  console.log(`  接口：${OLLAMA_BASE_URL}/chat/completions | 模型：${OLLAMA_MODEL} | temperature=0`);
  console.log('  本脚本为独立旁路工具：不加载、不修改任何核心决策文件');
  console.log(line);

  /* ---------- 服务预检：不可达则给出明确提示并终止 ---------- */
  try {
    await preflightCheck();
  } catch (err) {
    console.error('\n[预检失败] ' + (err && err.message ? err.message : String(err)));
    process.exitCode = 1;
    return;
  }

  const results = [];
  for (const tc of TEST_CASES) {
    results.push(await runCase(tc));
  }

  /* ---------- 汇总统计 ---------- */
  const total = results.length;
  const structuredOk = results.filter((r) => r.structuredOk).length;
  const structuredRate = total > 0 ? ((structuredOk / total) * 100).toFixed(1) : '0.0';
  const alignedSum = results.reduce((s, r) => s + r.alignedCount, 0);
  const alignedTotal = total * CONTRACT_FIELDS.length;
  const avgAlignRate = alignedTotal > 0 ? ((alignedSum / alignedTotal) * 100).toFixed(1) : '0.0';

  console.log('');
  console.log(line);
  console.log('【汇总统计】');
  console.log(`  总用例数         : ${total}`);
  console.log(`  结构化成功数     : ${structuredOk}`);
  console.log(`  结构化成功率     : ${structuredRate}%（五字段齐全且取值合法的用例占比）`);
  console.log(`  核心字段平均对齐率: ${avgAlignRate}%（${alignedSum}/${alignedTotal} 个字段与规则基线一致，结构化失败用例按 0 对齐计入）`);

  const failed = results.filter((r) => !r.structuredOk);
  if (failed.length > 0) {
    console.log('');
    console.log('【失败用例明细】');
    for (const r of failed) {
      console.log(`  ${r.tc.id} ${r.tc.name}`);
      console.log(`    失败原因: ${r.error || '未知'}`);
    }
  }
  const misaligned = results.filter((r) => r.structuredOk && r.alignedCount < r.total);
  if (misaligned.length > 0) {
    console.log('');
    console.log('【对齐偏差提示】以下用例结构化通过但存在字段偏差：' +
      misaligned.map((r) => `${r.tc.id}(${r.alignedCount}/${r.total})`).join('、'));
  }
  console.log(line);

  if (failed.length > 0) process.exitCode = 1;
}

/* ---------- 全程异常捕获（错误须显式暴露） ---------- */
main().catch((err) => {
  console.error('\n[脚本执行异常] 验证流程整体失败！');
  console.error('  错误类型 : ' + (err && err.name ? err.name : 'Unknown'));
  console.error('  错误信息 : ' + (err && err.message ? err.message : String(err)));
  if (err && err.stack) console.error(err.stack);
  process.exitCode = 1;
});
