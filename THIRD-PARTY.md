# THIRD-PARTY.md — 第三方组件合规声明（ZCode）

> 本文件为 MoRay 项目对第三方组件 ZCode 的合规结论。
> 三段结论与 `D:\ZCode_Test\ZCode接入评估报告.md`（2026-09-24，Day 6 第二阶段预研）逐条一致；
> 引文均出自该报告原文，条款出处随文标注。

---

## 一、来源：GitHub `zai-org/ZCode`

**结论：ZCode 来源为官方开源仓库 `github.com/zai-org/ZCode`，MoRay 仅使用从该仓库源码构建的 Agent CLI（`@zcode/cli`）。**

> 报告原文（§0 结论速览）：
> "✅ **能接**：官方开源仓库 `zai-org/ZCode`(Apache-2.0)可从源码构建独立 Agent CLI；源码构建的 CLI 在本次全部 8 项实测中**零外部网络请求、零静默上传**，遥测(OTLP)默认关闭、需显式配置 endpoint 才生效；数据目录可完全重定向。"

> 报告原文（§1）：
> "仓库 `github.com/zai-org/ZCode`(TypeScript,约 6.7k stars),第一方代码 Apache-2.0。测试当日最新版本 v3.14.3(2026-09-23 发布),Agent CLI 版本 0.16.9。"

本项目集成事实：`server/app/engines/zcode_adapter.py` 以绝对路径调用源码构建产物
`zcode.cjs`（锁定 commit 快照 + sha256，见 `docs/zcode_docker_isolation.md`）。

---

## 二、协议：Apache-2.0

**结论：ZCode 第一方代码采用 Apache-2.0 协议，允许源码构建与集成使用；本项目遵守其署名与 NOTICE 要求。**

> 报告原文（§0）：
> "官方开源仓库 `zai-org/ZCode`(Apache-2.0)可从源码构建独立 Agent CLI"

> 报告原文（§1）：
> "第一方代码 Apache-2.0。"

---

## 三、禁止打包其桌面版二进制

**结论：禁止将 ZCode 桌面版（Electron）二进制打包进 MoRay 或用于生产集成；只允许源码构建的 CLI。**

> 报告原文（§0 前提 1）：
> "⚠️ **前提 1**：必须用**源码构建的 CLI**(`@zcode/cli`),不要用桌面版 Electron 应用做集成——桌面版二进制含阿里云 RUM 监控组件,且第三方研究曾指认 3.11.2 桌面版存在"工作区快照上传"代码路径(本机 3.14.3 字节级扫描未再发现该特征,详见 §6 风险 2)。"

> 报告原文（§6 风险 2 规避列）：
> "只用**源码构建 CLI**,锁定 commit + 自构建;不装桌面版到生产机;CI 里对构建产物做域名白名单扫描(本次方法可复用:`tools/byte-scan-asar.ps1`)"

---

## 附：本文件维护规则

- 三段结论若需修订，必须先修订 `D:\ZCode_Test\ZCode接入评估报告.md` 对应条款，
  再同步本文件（两侧逐条一致是本文件的验收标准）。
- ZCode 版本升级（当前锁定 CLI 0.16.9 / 仓库 v3.14.3 快照）必须重走评估报告 §6 风险 2
  的字节级域名白名单扫描后再更新锁定 commit。
