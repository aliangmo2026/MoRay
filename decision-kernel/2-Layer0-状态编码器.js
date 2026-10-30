'use strict';
/**
 * 文件2：Layer 0 —— 世界状态编码器（v1.0 最小实现）
 * 对应《MoRay 应用层AGI决策内核技术基准文档 v1.0》Layer 0 模块
 *
 * v1.0 严格最小化原则：只编码三个核心维度——
 *   ① agent_availability      Agent 池可用性
 *   ② user_permission_level   用户权限等级
 *   ③ operation_risk_tag      操作风险标签（规则关键词判定）
 * 不扩展其他字段。纯本地规则实现，无任何外部调用。
 */

// Node 原生高精度计时（perf_hooks 为 Node 内置模块，非第三方依赖）
const { performance } = require('perf_hooks');

/** 风险等级序位表（升序），用于「升级一级风险」计算 */
const RISK_ORDER = ['low', 'medium', 'high', 'critical'];

/**
 * 计算操作风险标签（基准文档 Layer0 规则：风险关键词 → 风险序位）
 *  - 命中「生产/生产环境」→ 直接置为 high
 *  - 命中「删除/权限/配置」→ 每命中一个关键词升级一级风险（封顶 critical）
 * @param {string} query 用户请求文本
 * @returns {string} low | medium | high | critical
 */
function computeRiskTag(query) {
  let idx = 0; // 默认 low
  if (/生产|生产环境/.test(query)) {
    idx = RISK_ORDER.indexOf('high'); // 生产环境 → high
  }
  for (const kw of ['删除', '权限', '配置']) {
    if (query.includes(kw)) {
      idx = Math.min(idx + 1, RISK_ORDER.length - 1); // 每命中一词升级一级风险
    }
  }
  return RISK_ORDER[idx];
}

/**
 * Layer 0 世界状态编码入口
 * @param {string} query          用户请求文本
 * @param {Array}  agentPool      Agent 健康池数组，元素形如 { agent_id, status }
 * @param {string} userPermission 用户权限等级（如 read / write / admin）
 * @returns {object} 结构化世界状态向量（v1.0 三维度 + 标识 + 耗时）
 */
function encodeWorldState(query, agentPool = [], userPermission = 'read') {
  const t0 = performance.now(); // 编码耗时统计起点

  // —— 维度③：操作风险标签（规则关键词判定）——
  const operation_risk_tag = computeRiskTag(String(query || ''));

  // —— 维度①：Agent 池可用性 ——
  const pool = Array.isArray(agentPool) ? agentPool : [];
  const healthyAgents = pool.filter((a) => a && a.status === 'healthy');
  const agent_availability = {
    total: pool.length,                                   // 池内 Agent 总数
    healthy_count: healthyAgents.length,                  // 健康 Agent 数
    healthy_agent_ids: healthyAgents.map((a) => a.agent_id), // 健康 Agent 清单
    ratio: pool.length > 0
      ? Number((healthyAgents.length / pool.length).toFixed(2))
      : 0,                                                // 可用率 [0,1]
  };

  // —— 维度②：用户权限等级 ——
  const user_permission_level = String(userPermission || 'read');

  const encode_time_ms = Number((performance.now() - t0).toFixed(3)); // 编码耗时（毫秒）

  return {
    state_id: 'ws-' + Date.now() + '-' + Math.random().toString(16).slice(2, 8),
    agent_availability,      // 维度①
    user_permission_level,   // 维度②
    operation_risk_tag,      // 维度③
    encode_time_ms,          // 性能指标：编码耗时（ms）
  };
}

module.exports = {
  encodeWorldState,
  computeRiskTag, // 导出供测试与 Layer 1 交叉校验使用
};
