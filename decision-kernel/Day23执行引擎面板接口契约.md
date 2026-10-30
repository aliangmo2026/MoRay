# Day 23 执行引擎面板接口契约

> 定位：不是孤立监控 UI，是 Layer 0 世界状态编码器的数据源，输出直接对齐 WorldStateVectorV1 结构。



***

## 一、GET /api/engines 接口定义

### 响应结构（直接对齐 agent\_availability 字段）



```
interface EngineStatusResponse {

&#x20; // 元信息

&#x20; timestamp: string;

&#x20; world\_state\_id: string; // 生成世界状态向量ID，直接透传给决策层

&#x20; // 对齐 WorldStateVectorV1.agent\_availability

&#x20; engines: {

&#x20;   agent\_id: string;

&#x20;   name: string;

&#x20;   type: 'local\_adapter' | 'zcode\_adapter' | 'laya\_decision' | 'rule\_engine';

&#x20;   health: 'healthy' | 'degraded' | 'unavailable';

&#x20;   latency\_ms: number;

&#x20;   queue\_depth: number;

&#x20;   // 扩展字段，不影响编码器消费

&#x20;   version?: string;

&#x20;   capabilities?: string\[];

&#x20;   uptime\_s?: number;

&#x20; }\[];

&#x20; // 对齐 WorldStateVectorV1 全局状态

&#x20; global\_state: {

&#x20;   user\_permission\_level: 'read' | 'write' | 'admin';

&#x20;   current\_world\_state\_id: string;

&#x20;   request\_count\_1m: number;

&#x20;   error\_rate\_1m: number;

&#x20; };

&#x20; // 【v1.1新增】决策内核可观测指标

&#x20; decision\_metrics: {

&#x20;   total\_requests\_1h: number;

&#x20;   // 升级触发率，目标区间10%-25%

&#x20;   upgrade\_trigger\_rate: number;

&#x20;   // 分类型升级统计

&#x20;   upgrade\_breakdown: {

&#x20;     low\_confidence: number;

&#x20;     high\_complexity: number;

&#x20;     high\_risk: number;

&#x20;     unknown\_composite: number;

&#x20;   };

&#x20;   // 第一层/第二层一致率

&#x20;   chunk\_consistency\_rate: number;

&#x20; };

}
```

### 面板展示要求



1. 引擎列表区：直接渲染 `engines` 数组，健康状态用绿 / 黄 / 红三色标识

2. 顶部状态栏：展示全局请求量、错误率

3. 侧边栏决策健康区：

* 升级触发率仪表盘（正常区间绿，<5% 红，>40% 红）

* 四类升级占比饼图

* 前后一致率数值

1. 点击单个引擎 → 展开扩展字段（version/capabilities/uptime）



***

## 二、数据链路衔接



```
GET /api/engines 返回

&#x20;  ↓

World State Encoder v1 直接消费 engines 数组

&#x20;  ↓ 生成 WorldStateVectorV1

Laya 决策层输入：用户请求 + WorldStateVectorV1

&#x20;  ↓

DecisionInputSnapshot.world\_state\_id 直接引用当前返回的 world\_state\_id
```

**零转换层**，面板接口输出就是编码器输入，不做中间格式转换。



***

## 三、并行开发排期



| 线程  | 任务                                | 启动时间 | 依赖          |
| --- | --------------------------------- | ---- | ----------- |
| 训练线 | 238 条样本构造 + bad case 聚类 + LoRA 重训 | 今天   | 已冻结 v1.1 契约 |
| 前端线 | 面板 UI 开发，按本接口结构渲染                 | 今天   | 本接口契约       |
| 后端线 | API 实现 + WorldStateEncoder 骨架     | 明天   | 本接口契约       |

三条线并行，互不阻塞。