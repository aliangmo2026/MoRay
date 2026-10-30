# MoRay 贡献指南（CONTRIBUTING）

## 一、项目简介

MoRay 是一个**本地优先**的 AI 开发者工作台：浏览器工作台 + Python 后端，内置多步 Agent
执行引擎（ExecutionEngine 抽象接口 + LocalAdapter 兜底 / ZCodeAdapter Docker 隔离引擎）、
MCP 工具网关、会话状态机与事件流回放。所有密钥与用户数据只留在本机（`.env` + SQLite），
不依赖云端账号体系；ZCode 引擎在 Docker 沙箱中运行（internal 网络 + 只读 rootfs + 非 root，
隔离验收 9/9 硬闸门通过后才受理任务）。

## 二、如何运行测试

前置：Python 3.11+，在仓库根目录 `pip install -r requirements.txt`。

```bash
# 根目录回归（预算/安全，21 例）
python test_budget.py
python test_security.py

# tests/ 目录用例（72 例）
pytest tests/
```

- 当前回归基线：**93 例分套件全绿**（根目录 21 + tests/ 72）。
- `tests/test_mcp.py` 是交互式手动脚本（模块级 `input()`），**不参与自动回归**，
  pytest discover 时请排除。
- 已知问题：整目录单次 discover 存在 1 例字母序跨套件数据污染（test_mvp_e2e），
  单独复跑即通过，属测试隔离缺陷而非产品回归。

## 三、提交规范

commit message 使用以下前缀之一：

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能 | `feat: agent_sessions 五态状态机` |
| `fix` | 缺陷修复 | `fix: verify_isolation C5 捕获缺陷` |
| `docs` | 仅文档 | `docs: 补充 CONTRIBUTING` |
| `refactor` | 重构（不改行为） | `refactor: 抽取 ExecutionEngine 接口` |
| `chore` | 构建/杂务 | `chore: 更新 .gitignore` |

## 四、红线声明

以下内容**绝对不允许提交进仓库**（`.gitignore` 已忽略，提交前请再自查一遍暂存区）：

- `.env` 及一切密钥/环境变量文件——上游厂商 API Key 只存在于本机 `.env`，泄露即损失；
- `data/`——本地数据库与用户数据（含真实聊天记录），绝不公开；
- `backup/`——历史备份（体积巨大，仅本地保留）。

完整忽略清单见 [.gitignore](.gitignore)；隔离相关产物（`engines/docker/zcode-src/`
第三方源码快照、构建与验收日志）同样不入库。

## 五、联系方式

- GitHub: [aliangmo2026](https://github.com/aliangmo2026)
- 问题与建议请开 Issue；PR 前请确认回归全绿、红线零触碰。
