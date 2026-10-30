'use strict';
/**
 * 文件4：run-poc.js —— MoRay 决策内核 v1.0 最小闭环测试主脚本
 * 对应《MoRay 应用层AGI决策内核技术基准文档 v1.0》——「最小闭环验证」
 *
 * 全链路：请求输入 → Layer 0 状态编码 → Layer 1 Laya 决策 → 结果输出
 * 纯本地运行：零第三方依赖、无任何外部模型/API/网络调用。
 */

const { encodeWorldState } = require('./2-Layer0-状态编码器.js');
const { makeDecision } = require('./3-Layer1-Laya决策层.js');

function main() {
  const line = '='.repeat(64);
  console.log(line);
  console.log('  MoRay 应用层 AGI 决策内核 v1.0 —— 最小闭环 POC 测试');
  console.log(line);

  /* ---------- 测试输入 ---------- */
  const query = '帮我改一下生产环境的配置文件'; // POC 测试用例
  const userPermission = 'write';               // 用户权限等级

  // 模拟 Agent 池（基准文档：Agent 健康池最小样例）
  const agentPool = [
    { agent_id: 'code_agent',   status: 'healthy' },
    { agent_id: 'confirm_agent', status: 'healthy' },
    { agent_id: 'audit_agent',  status: 'healthy' },
  ];

  console.log(`\n>> 用户请求   : ${query}`);
  console.log(`>> 用户权限   : ${userPermission}`);
  console.log(`>> Agent 池   : ${agentPool.map((a) => `${a.agent_id}(${a.status})`).join(', ')}\n`);

  /* ---------- 全链路计时起点 ---------- */
  const t0 = Date.now();

  // 第一段：Layer 0 世界状态编码
  const worldState = encodeWorldState(query, agentPool, userPermission);

  // 第二段：Layer 1 Laya 快速决策
  const decision = makeDecision(query, worldState);

  const totalMs = Date.now() - t0; // 全链路耗时

  /* ---------- 第一部分：核心指标摘要 ---------- */
  console.log('【第一部分：核心指标摘要】');
  console.log(line);
  console.log(`  状态编码耗时   : ${worldState.encode_time_ms} ms`);
  console.log(`  决策耗时       : ${decision.latency_ms} ms`);
  console.log(`  全链路耗时     : ${totalMs} ms`);
  console.log(`  风险等级       : ${decision.quality.risk_level}`);
  console.log(`  是否组合场景   : ${decision.quality.is_composite_scenario ? '是（' + decision.quality.composite_type + '）' : '否'}`);
  console.log(`  是否升级       : ${decision.upgrade.should_upgrade ? '是（优先级 ' + decision.upgrade.upgrade_priority + '）' : '否'}`);
  console.log(`  升级原因       : ${decision.upgrade.trigger_reasons.length > 0 ? decision.upgrade.trigger_reasons.join('；') : '无'}`);
  console.log(`  执行步骤数     : ${decision.output.step_sequence.length}`);
  console.log(`  元决策对齐度   : ${decision.meta_consistency.constitution_alignment}（来源元核心：${decision.meta_consistency.from_meta_core}）`);
  console.log(line);

  /* ---------- 第二部分：完整 DecisionResult ---------- */
  console.log('\n【第二部分：完整 DecisionResult（JSON）】');
  console.log(JSON.stringify(decision, null, 2));
  console.log('\n' + line);
  console.log('  POC 最小闭环执行完毕：输入 → L0 编码 → L1 决策 → 结构化输出');
  console.log(line);
}

/* ---------- 全程异常捕获（基准文档要求：错误须显式暴露） ---------- */
try {
  main();
} catch (err) {
  console.error('\n[POC 执行异常] 全链路闭环测试失败！');
  console.error('  错误类型 : ' + (err && err.name ? err.name : 'Unknown'));
  console.error('  错误信息 : ' + (err && err.message ? err.message : String(err)));
  if (err && err.stack) {
    console.error('  调用堆栈 :');
    console.error(err.stack);
  }
  process.exitCode = 1; // 非零退出码，便于外部感知失败
}
