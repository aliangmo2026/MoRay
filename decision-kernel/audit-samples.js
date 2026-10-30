'use strict';
/**
 * audit-samples.js —— MoRay 决策内核 JEV 样本质检脚本（独立旁路工具）
 *
 * 定位：零侵入的样本库质量闸门。
 *   - 全量扫描 samples/ 目录下所有 JSON 样本文件；
 *   - 不依赖、不修改任何现有核心决策文件与生成器（纯 fs/path/crypto 内置模块）。
 *
 * 校验维度：
 *   ① 结构完整性：样本五要素（request / world_state / standard_decision /
 *      rule_baseline_decision / decision_origin）齐全；standard_decision 必须包含
 *      核心字段 risk_level、is_composite_scenario、should_upgrade、step_sequence、confidence
 *      （按 DecisionResult 契约嵌套路径定位：quality.* / upgrade.* / output.step_sequence）；
 *   ② 枚举合法性：risk_level ∈ low|medium|high|critical；should_upgrade ∈ true|false（严格布尔）；
 *   ③ 数值合理性：confidence ∈ [0,1]；step_sequence 为数组且长度 ≥ 1；
 *   ④ 场景分布：S01~S06 六大类场景样本数量与占比；
 *   ⑤ 重复检测：基于标准决策内容指纹（剔除时间戳/随机 id 后 sha256）跨文件查重。
 *
 * 报告输出：总样本数 / 有效 / 无效 / 重复；场景分布；无效明细（文件名+原因）；
 *           整体合格率与最终质检结论（存在无效或重复样本时结论为不合格，退出码 1）。
 *
 * 用法：node audit-samples.js
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

/* ============================================================
 * 配置与常量
 * ============================================================ */
const SAMPLES_DIR = path.join(__dirname, 'samples');

/** 六大类场景基准清单（用于分布统计兜底：0 样本场景也要出现在报告中） */
const SCENARIO_CATEGORIES = [
  { id: 'S01', name: '普通文件修改' },
  { id: 'S02', name: '生产环境操作' },
  { id: 'S03', name: '删除操作' },
  { id: 'S04', name: '只读操作' },
  { id: 'S05', name: '组合场景' },
  { id: 'S06', name: '跨Agent操作' },
];

/** 样本级必备五要素（顶层字段） */
const REQUIRED_SAMPLE_FIELDS = ['request', 'world_state', 'standard_decision', 'rule_baseline_decision', 'decision_origin'];

/** 核心五字段在 DecisionResult 契约中的嵌套路径 */
const CORE_FIELD_PATHS = {
  risk_level: ['quality', 'risk_level'],
  is_composite_scenario: ['quality', 'is_composite_scenario'],
  should_upgrade: ['upgrade', 'should_upgrade'],
  step_sequence: ['output', 'step_sequence'],
  confidence: ['quality', 'confidence'],
};

const RISK_SET = new Set(['low', 'medium', 'high', 'critical']);

/* ============================================================
 * 工具函数
 * ============================================================ */
const isPlainObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

/** 按路径数组安全取嵌套值，任一层缺失返回 undefined */
function getByPath(obj, p) {
  let cur = obj;
  for (const k of p) {
    if (!isPlainObj(cur)) return undefined;
    cur = cur[k];
  }
  return cur;
}

/**
 * 标准决策内容指纹：剔除时间戳/随机 id 等非内容字段后取 sha256（前 16 位）
 * 与生成器 dedup 口径一致：纯决策语义内容相同 → 指纹相同
 */
function decisionFingerprint(standardDecision) {
  const d = JSON.parse(JSON.stringify(standardDecision));
  delete d.decision_id;
  delete d.timestamp;
  delete d.latency_ms;
  if (isPlainObj(d.input_snapshot)) {
    delete d.input_snapshot.world_state_id;
    delete d.input_snapshot.context_id;
    delete d.input_snapshot.timestamp;
  }
  return crypto.createHash('sha256').update(JSON.stringify(d), 'utf-8').digest('hex').slice(0, 16);
}

/* ============================================================
 * 单文件校验：返回 { file, valid, errors, scenarioId, fingerprint|null }
 * ============================================================ */
function auditFile(filePath) {
  const file = path.basename(filePath);
  const rec = { file, valid: true, errors: [], scenarioId: '未知', origin: '(missing)', fingerprint: null };

  let sample;
  try {
    sample = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch (err) {
    rec.valid = false;
    rec.errors.push('JSON 解析失败：' + (err && err.message ? err.message : String(err)));
    return rec;
  }

  /* ---------- ① 结构完整性 ---------- */
  if (!isPlainObj(sample)) {
    rec.valid = false;
    rec.errors.push('顶层不是 JSON 对象');
    return rec;
  }
  for (const f of REQUIRED_SAMPLE_FIELDS) {
    if (sample[f] === undefined || sample[f] === null) rec.errors.push(`样本字段 ${f} 缺失`);
  }
  if (typeof sample.decision_origin === 'string') rec.origin = sample.decision_origin;

  const d = sample.standard_decision;
  if (!isPlainObj(d)) {
    rec.valid = false;
    if (rec.errors.length === 0) rec.errors.push('standard_decision 缺失或不是对象');
    rec.errors.push('standard_decision 缺失，核心字段校验无法执行');
    return rec;
  }

  // 核心五字段存在性（按契约嵌套路径）
  const present = {};
  for (const [key, p] of Object.entries(CORE_FIELD_PATHS)) {
    const v = getByPath(d, p);
    if (v === undefined || v === null) {
      rec.errors.push(`standard_decision 核心字段 ${key}（${p.join('.')}）缺失`);
    } else {
      present[key] = v;
    }
  }

  /* ---------- ② 枚举合法性 ---------- */
  if ('risk_level' in present) {
    if (typeof present.risk_level !== 'string' || !RISK_SET.has(present.risk_level)) {
      rec.errors.push(`risk_level 枚举非法：${JSON.stringify(present.risk_level)}（仅允许 low/medium/high/critical）`);
    }
  }
  if ('should_upgrade' in present) {
    if (typeof present.should_upgrade !== 'boolean') {
      rec.errors.push(`should_upgrade 非布尔值：${JSON.stringify(present.should_upgrade)}（仅允许 true/false）`);
    }
  }
  if ('is_composite_scenario' in present && typeof present.is_composite_scenario !== 'boolean') {
    rec.errors.push(`is_composite_scenario 非布尔值：${JSON.stringify(present.is_composite_scenario)}`);
  }

  /* ---------- ③ 数值合理性 ---------- */
  if ('confidence' in present) {
    const c = present.confidence;
    if (typeof c !== 'number' || !Number.isFinite(c) || c < 0 || c > 1) {
      rec.errors.push(`confidence 超出 [0,1] 或非数值：${JSON.stringify(c)}`);
    }
  }
  if ('step_sequence' in present) {
    const ss = present.step_sequence;
    if (!Array.isArray(ss)) {
      rec.errors.push(`step_sequence 不是数组：${JSON.stringify(ss)}`);
    } else if (ss.length < 1) {
      rec.errors.push(`step_sequence 长度 ${ss.length} < 1`);
    }
  }

  /* ---------- 场景归类：优先 scenario.id，回退文件名 jev-sample-Sxx --- */
  if (isPlainObj(sample.scenario) && typeof sample.scenario.id === 'string') {
    rec.scenarioId = sample.scenario.id;
  } else {
    const m = file.match(/jev-sample-(S\d{2})/i);
    if (m) rec.scenarioId = m[1].toUpperCase();
  }

  rec.valid = rec.errors.length === 0;

  /* ---------- 指纹：结构可用的样本才参与跨文件查重 ---------- */
  if (rec.valid) {
    try {
      rec.fingerprint = decisionFingerprint(d);
    } catch (err) {
      rec.valid = false;
      rec.errors.push('指纹计算失败：' + (err && err.message ? err.message : String(err)));
    }
  }
  return rec;
}

/* ============================================================
 * 主流程
 * ============================================================ */
function main() {
  const line = '='.repeat(64);
  console.log(line);
  console.log('  MoRay 决策内核 —— JEV 样本质检（audit-samples v1.0）');
  console.log(`  样本目录：${SAMPLES_DIR}`);
  console.log(line);

  if (!fs.existsSync(SAMPLES_DIR)) {
    console.error(`[质检失败] 样本目录不存在：${SAMPLES_DIR}，请先生成样本。`);
    process.exitCode = 1;
    return;
  }
  const jsonFiles = fs.readdirSync(SAMPLES_DIR).filter((f) => f.toLowerCase().endsWith('.json')).sort();
  if (jsonFiles.length === 0) {
    console.error('[质检失败] samples/ 目录下没有 JSON 样本文件。');
    process.exitCode = 1;
    return;
  }

  /* ---------- 逐文件校验 ---------- */
  const records = jsonFiles.map((f) => auditFile(path.join(SAMPLES_DIR, f)));

  const total = records.length;
  const validList = records.filter((r) => r.valid);
  const invalidList = records.filter((r) => !r.valid);

  /* ---------- 重复检测：按指纹分组（仅结构有效样本参与） ---------- */
  const groups = new Map();
  for (const r of validList) {
    if (!r.fingerprint) continue;
    if (!groups.has(r.fingerprint)) groups.set(r.fingerprint, []);
    groups.get(r.fingerprint).push(r.file);
  }
  const dupGroups = [...groups.entries()].filter(([, files]) => files.length > 1).sort((a, b) => b[1].length - a[1].length);
  const dupCount = dupGroups.reduce((s, [, files]) => s + (files.length - 1), 0);

  /* ---------- 场景分布与来源统计 ---------- */
  const byScenario = new Map();
  const byScenarioOrigin = new Map();
  for (const r of records) {
    byScenario.set(r.scenarioId, (byScenario.get(r.scenarioId) || 0) + 1);
    if (!byScenarioOrigin.has(r.scenarioId)) byScenarioOrigin.set(r.scenarioId, new Map());
    const om = byScenarioOrigin.get(r.scenarioId);
    om.set(r.origin, (om.get(r.origin) || 0) + 1);
  }
  const originTotal = new Map();
  for (const r of records) originTotal.set(r.origin, (originTotal.get(r.origin) || 0) + 1);
  const llmCount = originTotal.get('llm') || 0;

  /* ---------- 报告输出 ---------- */
  console.log('\n【一、总量核验】');
  console.log(`  总样本数   : ${total}`);
  console.log(`  有效样本数 : ${validList.length}`);
  console.log(`  无效样本数 : ${invalidList.length}`);
  console.log(`  重复样本数 : ${dupCount}（标准决策内容指纹相同，冗余条数）`);
  console.log(`  LLM 样本数 : ${llmCount}（decision_origin = llm）`);
  console.log(`  LLM 样本占比 : ${total > 0 ? ((llmCount / total) * 100).toFixed(1) : '0.0'}%`);
  console.log(`  来源分布   : ${[...originTotal.entries()].map(([o, n]) => `${o}=${n}`).sort().join(' | ')}`);

  console.log('\n【二、场景分布（S01~S06 六大类）】');
  const knownIds = new Set(SCENARIO_CATEGORIES.map((c) => c.id));
  const dispW = (s) => [...s].reduce((w, ch) => w + (ch.charCodeAt(0) > 255 ? 2 : 1), 0);
  const padCJK = (s, width) => s + ' '.repeat(Math.max(0, width - dispW(s)));
  for (const cat of SCENARIO_CATEGORIES) {
    const n = byScenario.get(cat.id) || 0;
    const pct = total > 0 ? ((n / total) * 100).toFixed(1) : '0.0';
    console.log(`  ${cat.id} ${padCJK(cat.name, 14)}: ${String(n).padStart(4)} 条  占比 ${pct}%  来源[${[...(byScenarioOrigin.get(cat.id) || new Map()).entries()].map(([o, c]) => `${o}:${c}`).join(' ') || '空'}]`);
  }
  const unknownIds = [...byScenario.keys()].filter((k) => !knownIds.has(k));
  if (unknownIds.length > 0) {
    for (const k of unknownIds.sort()) {
      console.log(`  ?? ${k.padEnd(12)} : ${String(byScenario.get(k)).padStart(4)} 条（未归类场景）`);
    }
  }

  console.log('\n【三、无效样本明细】');
  if (invalidList.length === 0) {
    console.log('  （无）');
  } else {
    for (const r of invalidList) {
      console.log(`  ✗ ${r.file}`);
      for (const e of r.errors) console.log(`      - ${e}`);
    }
  }

  console.log('\n【四、重复样本组明细】');
  if (dupGroups.length === 0) {
    console.log('  （无）');
  } else {
    dupGroups.forEach(([fp, files], i) => {
      console.log(`  ≡ 组 ${i + 1}（指纹 ${fp}，${files.length} 条互为重复）：`);
      for (const f of files) console.log(`      - ${f}`);
    });
  }

  /* ---------- 合格率与结论 ---------- */
  const passRate = total > 0 ? ((validList.length / total) * 100).toFixed(1) : '0.0';
  const dupRate = validList.length > 0 ? ((dupCount / validList.length) * 100).toFixed(1) : '0.0';
  const conclusion = invalidList.length === 0 && dupCount === 0;

  console.log('');
  console.log(line);
  console.log('【质检结论】');
  console.log(`  整体合格率 : ${passRate}%（有效样本 ${validList.length}/${total}）`);
  console.log(`  有效样本冗余率 : ${dupRate}%（重复 ${dupCount}/${validList.length}）`);
  if (conclusion) {
    console.log('  ✔ 最终结论：合格 —— 全部样本结构/枚举/数值校验通过，且无内容重复样本');
  } else {
    const reasons = [];
    if (invalidList.length > 0) reasons.push(`${invalidList.length} 条无效样本`);
    if (dupCount > 0) reasons.push(`${dupCount} 条内容重复样本`);
    console.log(`  ✗ 最终结论：不合格 —— 存在 ${reasons.join('、')}（详见上方明细）`);
  }
  console.log(line);

  if (!conclusion) process.exitCode = 1;
}

/* ---------- 全程异常捕获（错误须显式暴露） ---------- */
try {
  main();
} catch (err) {
  console.error('\n[质检脚本异常]', err && err.stack ? err.stack : String(err));
  process.exitCode = 1;
}
