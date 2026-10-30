'use strict';
/**
 * run-all-tests.js —— MoRay 决策内核 v1.0 标准用例批量测试脚本
 * 对应《MoRay 应用层AGI决策内核技术基准文档 v1.0》——「标准测试用例 TC-01 ~ TC-06」
 *
 * 覆盖基准文档 6 条标准用例，每条独立执行（互不影响）：
 *   请求输入 → Layer 0 编码 → Layer 1 决策 → 断言核心结果 → 汇总通过率
 * 纯本地运行：零第三方依赖、无任何外部模型/API/网络调用。
 * 本脚本只读取与调用内核模块，不修改任何核心文件。
 */

const { performance } = require('perf_hooks');
const { encodeWorldState } = require('./2-Layer0-状态编码器.js');
const { makeDecision } = require('./3-Layer1-Laya决策层.js');

/* ---------- 模拟 Agent 池（与 POC 主脚本一致） ---------- */
const AGENT_POOL = [
  { agent_id: 'code_agent',    status: 'healthy' },
  { agent_id: 'confirm_agent', status: 'healthy' },
  { agent_id: 'audit_agent',   status: 'healthy' },
];

/* ============================================================
 * 6 条标准测试用例定义
 * checks：断言数组，每项 { label, fn(decision) → boolean }
 * ============================================================ */
const isHighRisk = (d) => d.quality.risk_level === 'high' || d.quality.risk_level === 'critical';

const TEST_CASES = [
  {
    id: 'TC-01',
    name: '修改本地普通代码文件 → 不升级，直接执行',
    query: '帮我修改本地的普通代码文件',
    permission: 'write',
    options: null,
    optionNote: '（无扩展上下文）',
    expectation: '不升级、直接执行（低风险快速路径，2 步）',
    checks: [
      { label: 'should_upgrade = false', fn: (d) => d.upgrade.should_upgrade === false },
      { label: '风险等级 = low', fn: (d) => d.quality.risk_level === 'low' },
      { label: '非组合场景', fn: (d) => d.quality.is_composite_scenario === false },
      { label: '执行步骤数 = 2', fn: (d) => d.output.step_sequence.length === 2 },
    ],
  },
  {
    id: 'TC-02',
    name: '修改生产环境配置文件 → 升级（高风险 + 组合场景）',
    query: '帮我修改生产环境的配置文件',
    permission: 'write',
    options: null,
    optionNote: '（无扩展上下文）',
    expectation: '升级；高风险触发 + 组合场景触发',
    checks: [
      { label: 'should_upgrade = true', fn: (d) => d.upgrade.should_upgrade === true },
      { label: '高风险（high/critical）', fn: isHighRisk },
      { label: '组合场景 is_composite_scenario = true', fn: (d) => d.quality.is_composite_scenario === true },
      { label: '触发维度含 risk 与 composite_scenario', fn: (d) => d.upgrade.trigger_types.includes('risk') && d.upgrade.trigger_types.includes('composite_scenario') },
    ],
  },
  {
    id: 'TC-03',
    name: '删除本地配置文件 → 升级（高风险触发）',
    query: '删除本地的配置文件',
    permission: 'write',
    options: null,
    optionNote: '（无扩展上下文）',
    expectation: '升级；「删除」「配置」双关键词命中 → 风险升至 high，风险维度触发升级',
    checks: [
      { label: 'should_upgrade = true', fn: (d) => d.upgrade.should_upgrade === true },
      { label: '高风险（high/critical）', fn: isHighRisk },
      { label: '触发维度含 risk', fn: (d) => d.upgrade.trigger_types.includes('risk') },
    ],
  },
  {
    id: 'TC-04',
    name: '读取生产环境日志文件（read 权限）→ 升级（高风险触发）',
    query: '读取生产环境的日志文件',
    permission: 'read',
    options: null,
    optionNote: '（无扩展上下文）',
    expectation: '升级；生产环境 → 风险 high，风险维度触发升级',
    checks: [
      { label: 'should_upgrade = true', fn: (d) => d.upgrade.should_upgrade === true },
      { label: '高风险（high/critical）', fn: isHighRisk },
      { label: '触发维度含 risk', fn: (d) => d.upgrade.trigger_types.includes('risk') },
      { label: '权限等级透传 = read', fn: (d) => d.output.param_mapping.user_permission === 'read' },
    ],
  },
  {
    id: 'TC-05',
    name: '修改生产环境配置 + 显式要求深度推理 → 升级（优先级 urgent）',
    query: '帮我修改生产环境的配置文件，我显式要求深度推理',
    permission: 'write',
    options: { explicit_deep_reasoning: true },
    optionNote: 'explicit_deep_reasoning = true（META-DEEP-01）',
    expectation: '升级；upgrade_priority = urgent',
    checks: [
      { label: 'should_upgrade = true', fn: (d) => d.upgrade.should_upgrade === true },
      { label: 'upgrade_priority = urgent', fn: (d) => d.upgrade.upgrade_priority === 'urgent' },
      { label: '触发维度含 deep_reasoning', fn: (d) => d.upgrade.trigger_types.includes('deep_reasoning') },
    ],
  },
  {
    id: 'TC-06',
    name: '修改生产环境配置（模拟已蒸馏场景，置信度 0.95）→ 不升级，直出',
    query: '帮我修改生产环境的配置文件',
    permission: 'write',
    options: { distilled: true, distilled_confidence: 0.95 },
    optionNote: 'distilled = true, distilled_confidence = 0.95（META-DIST-01）',
    expectation: '不升级、直出；蒸馏场景下风险/组合/复杂度触发降级为仅记录，置信度达标',
    checks: [
      { label: 'should_upgrade = false', fn: (d) => d.upgrade.should_upgrade === false },
      { label: '置信度 confidence = 0.95', fn: (d) => d.quality.confidence === 0.95 },
      { label: 'trigger_types 为空', fn: (d) => d.upgrade.trigger_types.length === 0 },
    ],
  },
];

/* ============================================================
 * 单条用例独立执行
 * ============================================================ */
function runCase(tc) {
  const SEp = '  ' + '-'.repeat(58);
  console.log(SEp);
  console.log(`【${tc.id}】${tc.name}`);
  console.log(`  输入请求   : ${tc.query}`);
  console.log(`  用户权限   : ${tc.permission}`);
  console.log(`  决策上下文 : ${tc.optionNote}`);
  console.log(`  预期结果   : ${tc.expectation}`);

  try {
    // 全链路：L0 编码 → L1 决策（独立计时）
    const t0 = performance.now();
    const worldState = encodeWorldState(tc.query, AGENT_POOL, tc.permission);
    const decision = makeDecision(tc.query, worldState, tc.options || {});
    const totalMs = Number((performance.now() - t0).toFixed(3));

    // 核心结果行
    console.log(
      `  核心结果   : 升级=${decision.upgrade.should_upgrade ? '是' : '否'}(${decision.upgrade.upgrade_priority})` +
      ` | 风险=${decision.quality.risk_level}` +
      ` | 组合场景=${decision.quality.is_composite_scenario ? '是' : '否'}` +
      ` | 步骤=${decision.output.step_sequence.length}` +
      ` | 置信度=${decision.quality.confidence}`
    );
    console.log(`  性能       : L0编码 ${worldState.encode_time_ms} ms | L1决策 ${decision.latency_ms} ms | 全链路 ${totalMs} ms`);

    // 逐条断言
    let allPassed = true;
    for (const c of tc.checks) {
      let ok = false;
      try {
        ok = Boolean(c.fn(decision));
      } catch (e) {
        ok = false;
      }
      if (!ok) allPassed = false;
      console.log(`    ${ok ? '✓' : '✗'} ${c.label}`);
    }
    console.log(`  用例判定   : ${allPassed ? 'PASS' : 'FAIL'}`);
    if (!allPassed) {
      console.log(`  实际升级维度: [${decision.upgrade.trigger_types.join(', ')}] ${decision.upgrade.upgrade_priority}`);
    }
    return { tc, passed: allPassed, decision, error: null };
  } catch (err) {
    // 单条用例异常不影响其余用例执行
    console.log(`  用例判定   : FAIL（执行异常）`);
    console.log(`  异常信息   : ${err && err.message ? err.message : String(err)}`);
    return { tc, passed: false, decision: null, error: err };
  }
}

/* ============================================================
 * 主流程 + 汇总统计
 * ============================================================ */
function main() {
  const line = '='.repeat(64);
  console.log(line);
  console.log('  MoRay 决策内核 v1.0 —— 标准用例批量测试（TC-01 ~ TC-06）');
  console.log(line);

  const results = [];
  for (const tc of TEST_CASES) {
    results.push(runCase(tc));
  }

  /* ---------- 汇总统计 ---------- */
  const total = results.length;
  const passed = results.filter((r) => r.passed).length;
  const failedList = results.filter((r) => !r.passed);
  const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : '0.0';

  console.log('');
  console.log(line);
  console.log('【汇总统计】');
  console.log(`  总用例数 : ${total}`);
  console.log(`  通过数   : ${passed}`);
  console.log(`  失败数   : ${failedList.length}`);
  console.log(`  通过率   : ${rate}%`);

  if (failedList.length > 0) {
    console.log('');
    console.log('【失败用例明细】');
    for (const r of failedList) {
      if (r.error) {
        console.log(`  ${r.tc.id} ${r.tc.name}`);
        console.log(`    执行异常: ${r.error.message || String(r.error)}`);
        continue;
      }
      const d = r.decision;
      const failedChecks = tcFailedLabels(r);
      console.log(`  ${r.tc.id} ${r.tc.name}`);
      console.log(`    实际结果: 升级=${d.upgrade.should_upgrade}(${d.upgrade.upgrade_priority}) 风险=${d.quality.risk_level} 组合=${d.quality.is_composite_scenario} 步骤=${d.output.step_sequence.length} 置信度=${d.quality.confidence} 触发维度=[${d.upgrade.trigger_types.join(', ')}]`);
      console.log(`    未过断言: ${failedChecks.join('；')}`);
    }
  } else {
    console.log('');
    console.log('  ✔ 全部 6 条标准用例通过，最小闭环行为与基准文档预期一致');
  }
  console.log(line);

  if (failedList.length > 0) process.exitCode = 1; // 存在失败用例时以非零退出码结束
}

/** 提取失败用例中未通过的断言标签（重新执行断言函数得到明细） */
function tcFailedLabels(r) {
  const labels = [];
  for (const c of r.tc.checks) {
    let ok = false;
    try { ok = Boolean(c.fn(r.decision)); } catch (_) { ok = false; }
    if (!ok) labels.push(c.label);
  }
  return labels.length > 0 ? labels : ['（断言全部通过但用例被判失败）'];
}

/* ---------- 全程异常捕获 ---------- */
try {
  main();
} catch (err) {
  console.error('\n[批量测试异常] 测试脚本整体执行失败！');
  console.error('  错误信息 : ' + (err && err.message ? err.message : String(err)));
  if (err && err.stack) console.error(err.stack);
  process.exitCode = 1;
}
