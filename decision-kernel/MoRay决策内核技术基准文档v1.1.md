# MoRay 应用层 AGI 决策内核技术基准文档 v1.1

> 变更说明：在 v1.0 基础上新增流式分块协议、安全收敛硬约束、流式安全测试用例，冻结接口定义，前后端可并行开发。
> 变更点标记：【v1.1 新增】【v1.1 修正】



***

## 第一篇 架构总览

### 1.1 定位

MoRay 决策内核是应用层 AGI 的 Laya 决策层（Layer 1），基于 Qwen2.5-1.5B-Instruct + LoRA 微调实现，本地离线运行，输出严格符合 DecisionResult 契约的结构化决策结果。

### 1.2 部署形态



* 基座模型：Qwen2.5-1.5B-Instruct（bf16，2.9GB 显存）

* LoRA 适配器：8.7MB，rank=8，仅训练注意力层

* 运行环境：RTX 5060 Laptop，本地离线，无云端调用

* 推理速度：30token/s，完整决策单生成约 6.5 秒

### 1.3 分层职责



* Layer 1（当前交付）：Laya 决策层，负责意图识别、风险判断、步骤规划

* Layer 2（后续）：世界编码器，负责执行结果反馈、经验沉淀

* Layer 0（规则兜底）：硬编码安全规则，优先级高于模型输出



***

## 第二篇 接口契约

### 2.1 基础类型定义【v1.1 修正】



```
// 【v1.1修正】优先级统一为P级运维语义，替换原low/normal/high命名

type UpgradePriority = 'none' | 'P2' | 'P1' | 'P0' | 'urgent';

type RiskLevel = 'low' | 'medium' | 'high' | 'critical';

type SourceType = 'laya\_meta' | 'laya\_domain' | 'laya\_composite' | 'layer2' | 'cache' | 'rule';

type SecurityFlag = 'upgrade\_flip' | 'downgrade\_flip' | 'risk\_level\_jump';

interface AgentCall {

&#x20; agent\_id: string;

&#x20; capability: string;

&#x20; priority: number;

&#x20; timeout\_ms: number;

&#x20; required: boolean;

}

interface DecisionStep {

&#x20; step\_id: string;

&#x20; action: string;

&#x20; target: string;

&#x20; agent\_id: string;

&#x20; expected\_output: string;

}

interface FallbackPlan {

&#x20; condition: string;

&#x20; action: string;

&#x20; max\_retry: number;

}

interface DecisionResult {

&#x20; decision\_id: string;

&#x20; input\_snapshot: {

&#x20;   query: string;

&#x20;   world\_state\_id: string;

&#x20;   context\_id: string;

&#x20;   timestamp: string;

&#x20; };

&#x20; output: {

&#x20;   target\_path: string;

&#x20;   agent\_chain: AgentCall\[];

&#x20;   param\_mapping: Record\<string, any>;

&#x20;   step\_sequence: DecisionStep\[];

&#x20;   expected\_outcome: string;

&#x20;   verification\_points: string\[];

&#x20;   fallback\_plan: FallbackPlan;

&#x20; };

&#x20; quality: {

&#x20;   confidence: number; // \[0,1]

&#x20;   complexity\_score: number; // \[0,1]

&#x20;   risk\_level: RiskLevel;

&#x20;   is\_composite\_scenario: boolean;

&#x20;   composite\_type: string | null;

&#x20; };

&#x20; upgrade: {

&#x20;   should\_upgrade: boolean;

&#x20;   trigger\_types: string\[];

&#x20;   trigger\_reasons: string\[];

&#x20;   upgrade\_priority: UpgradePriority;

&#x20; };

&#x20; explainability: {

&#x20;   matched\_meta\_rules: string\[];

&#x20;   matched\_domain\_experience: string\[];

&#x20;   reasoning\_summary: string;

&#x20;   counterfactual\_note: string;

&#x20; };

&#x20; meta\_consistency: {

&#x20;   constitution\_alignment: number; // \[0,1]

&#x20;   deviation\_note: string | null;

&#x20;   from\_meta\_core: boolean;

&#x20; };

&#x20; source: SourceType;

&#x20; latency\_ms: number;

&#x20; timestamp: string;

&#x20; version: {

&#x20;   meta\_core: string;

&#x20;   domain\_adapter: string;

&#x20;   world\_encoder: string;

&#x20; };

}
```

### 2.2 五字段核心契约校验规则

模型输出必须通过以下五项校验，任意一项不通过触发兜底：



1. `quality.risk_level` 必须在 `low|medium|high|critical` 枚举内

2. `quality.confidence` 必须为 0\~1 之间的数值

3. `quality.is_composite_scenario` 必须为布尔值

4. `upgrade.should_upgrade` 为 true 时必须包含 `trigger_types` 和 `upgrade_priority`

5. `output.step_sequence` 必须为非空数组

### 2.3 决策规则



1. 风险判断：请求含「生产 / 线上」→ 基础风险 +1；「删除 / 权限 / 配置」每命中一词风险升一级，封顶 critical

2. 流程编排：低风险 2 步（意图解析 + 执行）；高风险追加用户确认节点；生产操作追加审计节点

3. 升级触发：步骤数≥3 触发 complexity 升级；confidence<0.8 触发 low\_confidence 升级

4. 组合场景：代码 / 文件修改 × 高风险 → `is_composite_scenario=true`，confidence=0.72



***

### 2.4 流式分块协议（v1.1 新增）

> 解决生成式模型首屏延迟问题，体感速度与判别式模型一致。

#### 2.4.1 分块类型定义



```
// 【v1.1新增】meta元数据块：前20个token内输出，≤50ms返回前端

interface StreamingDecisionChunk {

&#x20; chunk\_type: "meta";

&#x20; risk\_level: RiskLevel;

&#x20; should\_upgrade: boolean;

&#x20; upgrade\_priority: UpgradePriority;

&#x20; confidence: number;

&#x20; is\_composite\_scenario: boolean;

}

// 路径预览块

interface StreamingDecisionPath {

&#x20; chunk\_type: "path";

&#x20; target\_path: string;

}

// 【v1.1新增】agent链预览：仅展示用，禁止用于执行决策

interface StreamingDecisionChain {

&#x20; chunk\_type: "agent\_chain";

&#x20; preview: string\[];

&#x20; final: AgentCall\[] | null; // 流式阶段为null，done后填充完整结构

}

// 步骤流块

interface StreamingDecisionSteps {

&#x20; chunk\_type: "steps";

&#x20; step\_sequence: DecisionStep\[];

}

// 完成块：携带完整结果与安全标记

interface StreamingDecisionDone {

&#x20; chunk\_type: "done";

&#x20; full\_result: DecisionResult;

&#x20; latency\_ms: number;

&#x20; security\_flags: SecurityFlag\[];

}

// 【v1.1新增】统一union，前端做discriminated union类型收窄

type StreamingChunk =

&#x20; \| StreamingDecisionChunk

&#x20; \| StreamingDecisionPath

&#x20; \| StreamingDecisionChain

&#x20; \| StreamingDecisionSteps

&#x20; \| StreamingDecisionDone;
```

#### 2.4.2 推送顺序

严格按以下顺序推送，不可乱序：

`meta → path → agent_chain → steps → done`

#### 2.4.3 安全收敛原则（硬约束，后端实现，不依赖模型）

> 核心原则：安全相关字段只能向更严格方向收敛，不能反向。



1. **第一层宽松、第二层严格**：第一层输出不升级 / 低风险，第二层输出升级 / 高风险 → 强制采纳第二层结果，追加 `upgrade_flip` 安全标记

2. **第一层严格、第二层宽松**：第一层输出升级 / 高风险，第二层输出不升级 / 低风险 → 允许放宽，追加 `downgrade_flip` 安全标记

3. **放宽方向跳变≥2 级**：比如第一层 critical，第二层 low → 直接丢弃模型结果，走人工兜底，追加 `risk_level_jump` 标记

4. **严格方向任意跳变允许**：安全优先，比如第一层 low，第二层 critical → 直接采纳第二层，不拦截

#### 2.4.4 前端渲染约束



1. meta 块到达 → 仅显示可撤销预告（"正在评估，初步判断为高风险操作"），**禁止自动执行任何动作**

2. done 块到达 → 才执行最终动作（弹确认框、执行、升级）

3. security\_flags 非空 → 强制记录审计日志，UI 上展示安全提示

4. 第一层与第二层不一致时，UI 平滑过渡，禁止出现 "弹了又收" 的闪烁



***

## 第三篇 验证方案

### 3.1 离线验证集



* 训练集：238 条（v1.1 迭代后）

* 验证集：16 条独立样本

* 评估指标：JSON 解析成功率、五字段契约通过率、风险判断准确率、一致率

### 3.2 基础性能指标



| 指标           | 目标值    |
| ------------ | ------ |
| 完整决策生成耗时 P99 | ≤7 秒   |
| 峰值显存占用       | ≤3.5GB |
| JSON 解析成功率   | ≥95%   |
| 五字段契约通过率     | ≥98%   |

### 3.3 准确率指标



| 指标        | v1.0 基线 | v1.1 目标 |
| --------- | ------- | ------- |
| 常规场景决策准确率 | \~75%   | 88-92%  |
| 风险等级判断准确率 | \~80%   | ≥90%    |
| 边界场景准确率   | \~65%   | ≥80%    |



***

### 3.6 流式安全测试用例（v1.1 新增）



| 用例 ID  | 第一层 meta              | 第二层完整结果                 | 预期行为                              |
| ------ | --------------------- | ----------------------- | --------------------------------- |
| TC-S01 | 低风险，不升级               | 高风险，升级                  | 强制升级，打`upgrade_flip`标记            |
| TC-S02 | 高风险，升级                | 低风险，不升级                 | 允许取消，打`downgrade_flip`标记          |
| TC-S03 | risk\_level=low       | risk\_level=critical    | 采纳第二层，不拦截（严格方向跳变）                 |
| TC-S04 | risk\_level=critical  | risk\_level=critical    | 正常流程                              |
| TC-S05 | risk\_level=medium    | risk\_level=high（1 级放宽） | 采纳第二层，记录日志                        |
| TC-S06 | risk\_level=critical  | risk\_level=low（3 级放宽）  | 丢弃模型结果，走人工兜底，打`risk_level_jump`标记 |
| TC-S07 | should\_upgrade=false | should\_upgrade=true    | 强制升级，打`upgrade_flip`标记            |
| TC-S08 | should\_upgrade=true  | should\_upgrade=false   | 允许取消，打`downgrade_flip`标记          |



***

## 第四篇 演进与合规

### 4.1 安全硬约束



1. 所有模型输出必须经过规则引擎校验，不符合契约的结果直接丢弃

2. 安全字段收敛逻辑后端硬编码，模型无权绕过

3. 所有`security_flags`标记的决策必须记录审计日志，不可篡改

4. 生产环境 critical 级操作，必须有人工确认环节，模型不可自动执行

### 4.2 迭代路线



* v1.1（当前）：流式协议冻结，238 条样本迭代，常规场景追平 Jev

* v1.2：llama.cpp/GGUF 量化，生成速度提升至 150token/s 以上

* v2.0：500 + 样本迭代，业务域准确率 95%+，超过 Jev 定制场景表现

### 4.3 合规说明



* 全程本地离线运行，不调用云端 API，不泄露业务数据

* LoRA 适配器仅训练注意力层，不修改基座模型原始权重

* 所有决策结果可追溯、可审计、可回滚