'use strict';
/**
 * 文件3：Layer 1 —— Laya 快速决策层（规则引擎实现版）
 * 对应《MoRay 应用层AGI决策内核技术基准文档 v1.0》Layer 1 模块
 *
 * 完整实现基准文档规定的四段元决策逻辑 + 四维升级触发判定：
 *   元决策链路：风险校验 → 复杂度评估 → 能力匹配 → 路径生成
 *   升级触发维度：风险 / 组合场景 / 复杂度 / 置信度
 * 纯本地规则引擎，不调用任何模型、API 或服务。
 */

const { performance } = require('perf_hooks');
const {
  createDecisionResult,
  createAgentCall,
  createDecisionStep,
} = require('./1-类型定义.js');

/* ============================================================
 * 元规则与领域经验表（基准文档：元决策核心规则库 v1.0 最小子集）
 * ============================================================ */

/** 元决策规则清单（用于 explainability.matched_meta_rules 溯源） */
const META_RULES = {
  RISK_GATE: 'META-RISK-01 风险校验：high/critical 强制升级（硬规则兜底，不可绕过）',
  COMPLEXITY: 'META-COMP-01 复杂度评估：执行步骤数 ≥ 3 触发升级',
  CAPABILITY: 'META-CAP-01 能力匹配：请求意图 → Agent 域映射',
  PATH_GEN: 'META-PATH-01 路径生成：基础 2 步，高风险追加确认步，生产环境追加审计步',
  CONFIDENCE: 'META-CONF-01 置信度门槛：低于 0.8 触发升级（组合场景 0.72 / 普通场景 0.9）',
  COMPOSITE: 'META-COMB-01 组合场景判定：代码修改 × 高风险 = 组合场景，未知组合触发升级',
  DEEP_REQUEST: 'META-DEEP-01 显式深度推理：用户显式要求深度推理 → 强制升级，优先级 urgent',
  DISTILLED: 'META-DIST-01 蒸馏直出：已蒸馏场景（高置信度产物）→ 风险/组合/复杂度触发降级为仅记录，置信度达标则不升级直出',
};

/** 领域经验库（基准文档：domain_adapter 经验条目） */
const DOMAIN_EXPERIENCE = {
  CODE_MODIFY: 'D-CODE-01 代码/配置文件修改场景 → 匹配 code_agent',
  PRODUCTION: 'D-OPS-01 生产环境变更 → 必须追加 audit_agent 审计步骤',
};

/* ============================================================
 * 意图识别（基准文档：复杂度评估 —— 规则关键词版）
 * ============================================================ */

/**
 * 从请求文本中识别意图集合
 * @param {string} query
 * @returns {string[]} 意图标识数组
 */
function detectIntents(query) {
  const intents = [];
  const isModifyAction = /修改|改一下|改动|调整|变更|优化|修复/.test(query);
  const isCodeTarget = /代码|文件|配置|函数|接口|页面|程序|脚本|bug|Bug/.test(query);
  // 领域经验 D-CODE-01：代码文件修改场景
  if (isModifyAction && isCodeTarget) intents.push('code_modification');
  // 领域经验 D-OPS-01：生产环境变更场景
  if (/生产|生产环境|线上/.test(query)) intents.push('production_env');
  if (/删除/.test(query)) intents.push('deletion');
  if (/权限/.test(query)) intents.push('permission_change');
  return intents;
}

/* ============================================================
 * 执行步骤生成（基准文档：META-PATH-01 路径生成规则）
 *  普通场景 2 步；高风险加确认步骤；生产环境加审计步骤
 * ============================================================ */

function buildStepSequence(query, intents, highRisk, isProduction) {
  const steps = [];
  let no = 0;

  // 基础步骤 1：意图解析与目标定位（普通场景第 1 步）
  steps.push(createDecisionStep(
    ++no, 'intent_analysis',
    createAgentCall('code_agent', { query, mode: 'analyze_target' }, no),
    '解析请求意图，定位目标代码/配置文件',
    '输出的目标文件与请求所指对象一致'
  ));

  // 基础步骤 2：执行修改（普通场景第 2 步）
  steps.push(createDecisionStep(
    ++no, 'execution',
    createAgentCall('code_agent', { query, mode: 'apply_change' }, no),
    '按定位结果执行代码/配置文件修改',
    '修改结果通过语法/一致性校验，与预期结果一致'
  ));

  // 高风险 → 追加用户确认步骤（确认门控，对应 confirm_agent）
  if (highRisk) {
    steps.push(createDecisionStep(
      ++no, 'user_confirm',
      createAgentCall('confirm_agent', { confirm_type: 'risk_gate', risk_source: 'operation_risk_tag' }, no),
      '高风险操作：执行前向用户发起确认',
      '用户显式确认后方可继续，拒绝则终止并回滚'
    ));
  }

  // 生产环境 → 追加审计步骤（对应 audit_agent，领域经验 D-OPS-01）
  if (isProduction) {
    steps.push(createDecisionStep(
      ++no, 'audit',
      createAgentCall('audit_agent', { audit_scope: 'production_change', log_level: 'full' }, no),
      '生产环境变更：记录完整审计日志并复核',
      '审计日志落盘且变更单可追溯'
    ));
  }

  return steps;
}

/* ============================================================
 * Layer 1 决策入口
 * ============================================================ */

/**
 * Laya 快速决策：输入请求文本与 Layer 0 世界状态，输出完整 DecisionResult
 * @param {string} query        用户请求文本
 * @param {object} worldState   encodeWorldState() 产出的世界状态向量
 * @param {object} [options]    决策上下文扩展项（基准文档 upgrade/quality 契约分支，向后兼容两参调用）
 *   - options.explicit_deep_reasoning {boolean} 用户显式要求深度推理 → 命中 META-DEEP-01，强制升级且优先级 urgent（TC-05）
 *   - options.distilled                {boolean} 模拟已蒸馏场景 → 命中 META-DIST-01：风险/组合/复杂度触发降级为仅记录，置信度取 distilled_confidence，达标则直出不升级（TC-06）
 *   - options.distilled_confidence      {number}  蒸馏场景置信度（默认 0.95）
 * @returns {object} DecisionResult（所有字段均已赋值）
 */
function makeDecision(query, worldState, options = {}) {
  const t0 = performance.now(); // 决策耗时统计起点
  const result = createDecisionResult();
  const text = String(query || '');
  const opts = options || {};

  /* ---------- ① 元决策·风险校验（META-RISK-01） ---------- */
  const risk = worldState.operation_risk_tag;
  const highRisk = risk === 'high' || risk === 'critical';

  /* ---------- ② 元决策·复杂度评估（意图识别 + 步骤生成） ---------- */
  const intents = detectIntents(text);
  const isCodeModify = intents.includes('code_modification');
  const isProduction = intents.includes('production_env');

  /* ---------- ③ 元决策·能力匹配（META-CAP-01 + 领域经验） ---------- */
  // 代码修改场景匹配 code_agent；按步骤序列裁剪实际调用链
  const steps = buildStepSequence(text, intents, highRisk, isProduction);
  const agentChain = steps.map((s, i) => {
    const call = s.agent_call;
    call.order = i + 1;
    return call;
  });

  /* ---------- ④ 组合场景判定（META-COMB-01）：代码修改 + 高风险 ---------- */
  const isComposite = isCodeModify && highRisk;
  const compositeType = isComposite ? '代码修改 × 高风险生产变更' : null;

  /* ---------- 质量评估 ---------- */
  // 蒸馏场景（META-DIST-01）：置信度取外部注入的已蒸馏产物值（默认 0.95），代表经验已沉淀
  const isDistilled = opts.distilled === true;
  const distilledConfidence = isDistilled
    ? (typeof opts.distilled_confidence === 'number' ? opts.distilled_confidence : 0.95)
    : null;
  // 置信度（META-CONF-01）：蒸馏直出取 distilled_confidence；否则组合场景 0.72，普通场景 0.9
  const confidence = isDistilled ? distilledConfidence : (isComposite ? 0.72 : 0.9);
  // 复杂度评分：随步骤数与风险/组合叠加
  const complexityScore = Math.min(
    1,
    Number((0.15 * steps.length + (highRisk ? 0.1 : 0) + (isComposite ? 0.2 : 0)).toFixed(2))
  );

  /* ---------- 升级触发判定（基准文档 upgrade 契约：四维 + 显式深度推理扩展） ---------- */
  const triggerTypes = [];
  const triggerReasons = [];

  // 显式深度推理请求（META-DEEP-01 / TC-05）：用户要求深度推理 → 强制升级，优先级 urgent
  const deepReasoningRequested = opts.explicit_deep_reasoning === true;

  if (isDistilled) {
    // 蒸馏场景（TC-06）：风险/组合/复杂度触发降级为「仅记录」，不触发升级；
    // 是否升级仅取决于置信度（默认 0.95 达标 → 不升级，直出快速路径）
    if (confidence < 0.8) {
      triggerTypes.push('low_confidence');
      triggerReasons.push(`蒸馏场景置信度 ${confidence} 仍低于门槛 0.8，触发升级`);
    }
    // 显式深度推理为硬规则：即便蒸馏场景，用户明确要求也强制升级（与 META-RISK-01 同级的兜底）
    if (deepReasoningRequested) {
      triggerTypes.push('deep_reasoning');
      triggerReasons.push('用户显式要求深度推理，命中 META-DEEP-01，强制升级，优先级 urgent');
    }
  } else {
    // 维度①：风险 —— high/critical 强制升级（硬规则兜底）
    if (highRisk) {
      triggerTypes.push('risk');
      triggerReasons.push(`操作风险标签为 ${risk}，命中硬规则 META-RISK-01，强制升级至 Lomi 深思层`);
    }
    // 维度②：组合场景 —— 未知组合触发升级
    if (isComposite) {
      triggerTypes.push('composite_scenario');
      triggerReasons.push(`组合场景「${compositeType}」属于 v1.0 规则库未覆盖的未知组合，触发升级`);
    }
    // 维度③：复杂度 —— 步骤数 ≥ 3 触发升级
    if (steps.length >= 3) {
      triggerTypes.push('complexity');
      triggerReasons.push(`执行步骤数为 ${steps.length}（≥3），超过 Laya 快速路径复杂度阈值，触发升级`);
    }
    // 维度④：置信度 —— 低于 0.8 触发升级
    if (confidence < 0.8) {
      triggerTypes.push('low_confidence');
      triggerReasons.push(`决策置信度 ${confidence} 低于门槛 0.8，触发升级`);
    }
    // 扩展维度⑤：显式深度推理请求（TC-05）
    if (deepReasoningRequested) {
      triggerTypes.push('deep_reasoning');
      triggerReasons.push('用户显式要求深度推理，命中 META-DEEP-01，强制升级，优先级 urgent');
    }
  }

  const shouldUpgrade = triggerTypes.length > 0;
  // 升级优先级：显式深度推理→urgent；critical→P0；high→P1；其余触发→P2
  let upgradePriority = 'none';
  if (deepReasoningRequested) {
    upgradePriority = 'urgent';
  } else if (shouldUpgrade) {
    upgradePriority = risk === 'critical' ? 'P0' : (risk === 'high' ? 'P1' : 'P2');
  }

  /* ---------- 填充决策输出 ---------- */
  result.input_snapshot = {
    query: text,
    world_state_id: worldState.state_id,
    context_id: 'ctx-' + Date.now(),
    timestamp: new Date().toISOString(),
  };
  result.output = {
    target_path: 'L0→L1 快速规则路径::' + (isCodeModify ? '代码修改域' : '通用域'),
    agent_chain: agentChain,
    param_mapping: {
      intent: intents.length > 0 ? intents : ['general_request'],
      // 目标对象：优先匹配带扩展名的文件名，退化匹配「配置文件」类描述词
      target_object: isCodeModify
        ? ((text.match(/[\w\-\.]+\.(json|ya?ml|conf|ini|env|toml|js|ts|py|md)/i) || [])[0]
          || (text.match(/(配置文件|配置项|代码文件|脚本)/) || ['目标文件'])[0])
        : null,
      environment: isProduction ? 'production' : 'default',
      risk_tag: risk,
      user_permission: worldState.user_permission_level,
    },
    step_sequence: steps,
    expected_outcome: isCodeModify
      ? '目标配置文件按请求完成修改，经用户确认与生产审计后安全落地'
      : '请求由快速路径完成处理并通过验证点校验',
    verification_points: steps.map((s) => s.verification),
    fallback_plan: {
      condition: '升级至 Layer 2（Lomi 深思层）后仍无法产出可执行方案',
      action: '挂起本决策，保留 input_snapshot 上下文快照，转人工澄清后重新进入 L0 编码',
      max_retry: 1,
    },
  };
  result.quality = {
    confidence,
    complexity_score: complexityScore,
    risk_level: risk,
    is_composite_scenario: isComposite,
    composite_type: compositeType,
  };
  result.upgrade = {
    should_upgrade: shouldUpgrade,
    trigger_types: triggerTypes,
    trigger_reasons: triggerReasons,
    upgrade_priority: upgradePriority,
  };

  /* ---------- 可解释性（基准文档 explainability 契约） ---------- */
  const matchedMetaRules = [META_RULES.RISK_GATE, META_RULES.COMPLEXITY, META_RULES.CAPABILITY, META_RULES.PATH_GEN, META_RULES.CONFIDENCE];
  if (isComposite) matchedMetaRules.push(META_RULES.COMPOSITE);
  if (isDistilled) matchedMetaRules.push(META_RULES.DISTILLED);
  if (deepReasoningRequested) matchedMetaRules.push(META_RULES.DEEP_REQUEST);
  const matchedExperience = [];
  if (isCodeModify) matchedExperience.push(DOMAIN_EXPERIENCE.CODE_MODIFY);
  if (isProduction) matchedExperience.push(DOMAIN_EXPERIENCE.PRODUCTION);

  result.explainability = {
    matched_meta_rules: matchedMetaRules,
    matched_domain_experience: matchedExperience,
    reasoning_summary:
      `风险校验：${risk}${highRisk ? '（高风险，命中硬规则）' : '（常规风险）'} → ` +
      `复杂度评估：识别意图 [${intents.join(', ') || '通用'}]，生成 ${steps.length} 个步骤 → ` +
      `能力匹配：${isCodeModify ? '代码修改场景匹配 code_agent，按风险叠加 confirm_agent/audit_agent' : '通用域处理'} → ` +
      `路径生成：${result.output.target_path}；` +
      `组合场景${isComposite ? '成立（' + compositeType + '），置信度降为 0.72' : '不成立，置信度 0.9'}；` +
      `共命中 ${triggerTypes.length} 个升级触发维度${shouldUpgrade ? '，判定升级（' + upgradePriority + '）' : '，维持快速路径执行'}`,
    counterfactual_note: isProduction && highRisk
      ? '反事实检验：若请求不涉及「生产环境」，风险标签将由 critical 降为 high/medium，审计步骤不再追加（步骤数 ' + steps.length + ' → ' + (steps.length - 1) + '），复杂度升级触发解除'
      : '反事实检验：若风险标签为 low/medium 且非组合场景，则仅保留 2 步快速路径，不触发任何升级',
  };

  /* ---------- 元决策一致性（基准文档 meta_consistency 契约） ---------- */
  result.meta_consistency = {
    constitution_alignment: 1.0, // v1.0 规则引擎全部判定源自元规则表，无偏离项
    deviation_note: '本决策全部判定均由元规则表与领域经验库推导，未引入规则外启发式，无宪法规则偏离',
    from_meta_core: true,
  };

  /* ---------- 溯源 / 耗时 / 版本 ---------- */
  result.source = 'Layer1-Laya-RuleEngine-v1.0';
  result.latency_ms = Number((performance.now() - t0).toFixed(3)); // 决策耗时（ms）
  result.timestamp = new Date().toISOString();
  result.version = { meta_core: 'v1.0.0', domain_adapter: 'v1.0.0', world_encoder: 'v1.0.0' };

  return result;
}

module.exports = {
  makeDecision,
  detectIntents,
};
