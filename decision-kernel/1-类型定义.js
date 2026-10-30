'use strict';
/**
 * 文件1：类型定义
 * 对应《MoRay 应用层AGI决策内核技术基准文档 v1.0》——「决策输出契约」数据结构定义
 *
 * 本模块只提供纯数据结构工厂函数（零逻辑、零依赖），
 * 所有字段默认值与层级结构严格对应基准文档的 DecisionResult 契约：
 *   decision_id / input_snapshot / output / quality / upgrade
 *   / explainability / meta_consistency / source / latency_ms
 *   / timestamp / version
 */

/** 生成简短随机 id 片段（用于 decision_id / state_id，本地实现，无外部依赖） */
function randHex(len = 6) {
  return Math.random().toString(16).slice(2, 2 + len).padEnd(len, '0');
}

/**
 * Agent 调用子结构（基准文档：output.agent_chain 元素）
 * @param {string} agentId  目标 Agent 标识
 * @param {object} params   调用参数映射
 * @param {number} order    链上执行顺序（从 1 开始）
 */
function createAgentCall(agentId = 'unknown_agent', params = {}, order = 1) {
  return {
    agent_id: agentId,   // 执行主体
    params,              // 参数映射（与 param_mapping 对应）
    order,               // 在 agent_chain 中的序号
    timeout_ms: 30000,   // 单次调用超时上限（v1.0 默认值）
  };
}

/**
 * 决策步骤子结构（基准文档：output.step_sequence 元素）
 * @param {number} stepId       步骤序号
 * @param {string} stepType     步骤类型：intent_analysis / execution / user_confirm / audit
 * @param {object} agentCall    该步骤的 AgentCall（createAgentCall 产物）
 * @param {string} description  步骤说明（可解释性来源）
 * @param {string} verification 该步骤对应的验证点
 */
function createDecisionStep(stepId = 1, stepType = 'execution', agentCall = null, description = '', verification = '') {
  return {
    step_id: stepId,                                                       // 步骤序号
    step_type: stepType,                                                   // 步骤类型
    agent_call: agentCall || createAgentCall('unknown_agent', {}, stepId), // 执行主体
    description,                                                           // 步骤说明
    verification,                                                          // 步骤级验证点
  };
}

/**
 * 决策结果完整对象工厂（基准文档：决策输出契约 DecisionResult）
 * 返回带合理默认值的完整结构，调用方（Layer 1）负责逐字段填充。
 */
function createDecisionResult() {
  const now = new Date().toISOString();
  return {
    /* ---------- 决策标识 ---------- */
    decision_id: 'dec-' + Date.now() + '-' + randHex(), // 决策唯一标识

    /* ---------- 输入快照（基准文档：input_snapshot） ---------- */
    input_snapshot: {
      query: '',                       // 用户原始请求文本
      world_state_id: '',              // 关联的 Layer 0 世界状态向量 id
      context_id: '',                  // 会话/上下文标识（v1.0 最小实现：同请求级）
      timestamp: now,                  // 输入采集时间
    },

    /* ---------- 决策输出（基准文档：output） ---------- */
    output: {
      target_path: '',                 // 命中的决策路径标识
      agent_chain: [],                 // Agent 调用链（AgentCall 数组）
      param_mapping: {},               // 意图→Agent 参数的映射
      step_sequence: [],               // 执行步骤序列（DecisionStep 数组）
      expected_outcome: '',            // 预期结果描述
      verification_points: [],         // 全局验证点数组
      fallback_plan: {                 // 降级/兜底方案
        condition: '',                 // 触发兜底的条件
        action: '',                    // 兜底动作
        max_retry: 0,                  // 最大重试次数
      },
    },

    /* ---------- 质量评估（基准文档：quality） ---------- */
    quality: {
      confidence: 0.0,                 // 决策置信度 [0,1]
      complexity_score: 0.0,           // 复杂度评分 [0,1]
      risk_level: 'low',               // 操作风险标签：low/medium/high/critical
      is_composite_scenario: false,    // 是否组合场景
      composite_type: null,            // 组合场景类型（非组合为 null）
    },

    /* ---------- 升级判定（基准文档：upgrade） ---------- */
    upgrade: {
      should_upgrade: false,           // 是否升级至 Layer 2（Lomi 深思层）
      trigger_types: [],               // 命中的升级触发维度数组
      trigger_reasons: [],             // 与触发维度一一对应的理由
      upgrade_priority: 'none',        // 升级优先级：none/P2/P1/P0
    },

    /* ---------- 可解释性（基准文档：explainability） ---------- */
    explainability: {
      matched_meta_rules: [],          // 命中的元决策规则清单
      matched_domain_experience: [],   // 命中的领域经验清单
      reasoning_summary: '',           // 推理过程摘要（自然语言）
      counterfactual_note: '',         // 反事实说明（若条件不同会如何）
    },

    /* ---------- 元决策一致性（基准文档：meta_consistency） ---------- */
    meta_consistency: {
      constitution_alignment: 0.0,     // 与元核心宪法规则的对齐度 [0,1]
      deviation_note: '',              // 偏离说明（无偏离时须显式声明）
      from_meta_core: false,           // 本决策是否全部源自元决策核心
    },

    /* ---------- 溯源与元数据 ---------- */
    source: 'unknown',                 // 决策来源层标识
    latency_ms: 0,                     // Layer 1 决策耗时（毫秒）
    timestamp: now,                    // 决策产出时间
    version: {                         // 各模块版本（基准文档：版本契约）
      meta_core: 'v1.0.0',             // 元决策核心版本
      domain_adapter: 'v1.0.0',        // 领域适配层版本
      world_encoder: 'v1.0.0',         // 世界状态编码器版本
    },
  };
}

module.exports = {
  createDecisionResult,
  createAgentCall,
  createDecisionStep,
};
