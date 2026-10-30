# lora-training — MoRay 决策内核 LoRA 训练数据工坊

独立工作区：从 `../samples/` 的 JEV 决策样本构建 LoRA 微调数据集，并完成本地训练环境预检。
**只读引用样本目录，全程不修改任何核心决策文件。**

## 流程（按 4 项工作顺序）

| 步骤 | 脚本 | 输入 | 产物 |
| --- | --- | --- | --- |
| 1. 样本清洗与筛选 | `clean_samples.py` | `../samples/*.json` | `output/cleaned_samples.jsonl`、`output/cleaning_report.md` |
| 2. 训练格式转换 | `convert_format.py` | 步骤 1 产物 | `output/train_format.jsonl`、`output/convert_report.md` |
| 3. 数据集拆分 | `split_dataset.py` | 步骤 1+2 产物 | `output/train.jsonl`、`output/val.jsonl`、`output/dataset_report.md` |
| 4. 环境预检 | `env_check.py` | 本机环境 | `output/env_report.md` |

一键执行：`build_dataset.bat`（需 PATH 上有 `python`；全部脚本零第三方依赖，纯标准库）。

## 数据格式

每条训练样本恰好三个字符串字段（对齐主流对话微调 JSONL 格式，可直接喂 LLaMA-Factory / axolotl / 自建 PEFT 脚本）：

```json
{"system": "MoRay 决策系统规则 + DecisionResult 五字段契约定义",
 "user": "用户请求：…\n权限等级：…（+ 附加选项 / L0 世界状态，有则拼接）",
 "assistant": "standard_decision 的标准 JSON 字符串"}
```

- **五字段契约**与 `../audit-samples.js` 质检口径同源：
  `quality.risk_level` / `quality.confidence` / `quality.is_composite_scenario` /
  `upgrade.should_upgrade` / `output.step_sequence`；
- **决策规则**与生产规则引擎（`3-Layer1-Laya决策层.js` / `jev-sample-generator.js` SYSTEM_PROMPT）同源；
- 拆分：按 6 大类场景（S01~S06）分层、随机种子 42 固定可复现，比例 9:1。

## 复现与再拆分

```bat
python clean_samples.py && python convert_format.py && python split_dataset.py
```

调整比例/种子：改 `split_dataset.py` 顶部 `SEED` / `VAL_RATIO` 后重跑步骤 3 即可（步骤 1、2 产物不变）。
