# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
lines = io.open(p, encoding='utf-8').readlines()
entry = '''## 3.18.5（2026-09-07）修复批次 5（收尾）：熔断持久化 / 404 语义 / hidden 复核 / 版本校验 / 估算标注 / 首次说明 / 移动端

- **#9 熔断持久化**（100_gateway.js GatewayBreaker）：失败记录同步 localStorage
  （键名 `moray_breaker_<MORAY_BUILD>` 带版本前缀防脏数据），启动时恢复——刷新后保留对
  失败模型的临时禁用直到冷却期结束；record/cooling/clear 惰性写回。
- **#10 GET /messages 404**（api.py）：会话不存在返回 404 not_found（不再宽容 200 空数组）；
  前端无 GET messages 调用（拉取走已 404 的 GET /api/conversations/{id}），无需前端分支改动。
  验证：不存在会话 → 404 {code:not_found}；存在会话 → 200。
- **#11 健康探测 hidden 复核**：70_models_settings_boot.js startHealthMonitor 首行
  `if (document.hidden) return;` 已存在（v3.15 落地）——复核确认，未重复改动。
- **#14 版本一致性构建校验**（assemble.py）：构建替换前校验
  parts/110_polish.js MORAY_BUILD == parts/70 APP_VERSION == config BUILD，不一致即
  “构建失败：内部版本号三处不一致 —— …（请统一后重试）”并退出。负向验证：改 70 为 v9.99.99
  → 构建失败并指出差异；恢复后通过。
- **#16 纯前端成本估算标注**（100_gateway.js updateGatewayStatusBar）：后端未连接时
  状态栏“今日 Xk tok”后追加“（估算）”标注。
- **#17 本机工具首次开启说明**（30_chat.js）：首次开启时弹一次安全说明（工作区隔离/人工审批
  unified diff/全程审计 + 可随时关闭），仅说明不改变默认关闭策略（localStorage 一次性标志）。
- **#20 移动端 fixed 背景防御**（135_ui_polish.js）：≤900px 时 body/#app/.aurora-bg
  background-attachment 强制 scroll，防 fixed 背景在窄屏/触控环境渲染溢出。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`ffa6ded4f4978301…`，web 两份=`19f4e7e262e629e8…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.5。

'''
out = lines[:2] + [entry] + lines[2:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('batch5 changelog inserted')
