# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
lines = io.open(p, encoding='utf-8').readlines()
entry = '''## 3.18.3（2026-09-07）修复批次 3：[#5] 线上演示版自动标注与 PWA 缓存版本化 + [#7] 重试语义显式化

- **#5 演示版横幅与缓存**：
  - `assemble.py --web` 导出的成品（web/index.html 与 web/moray-workbench.html）自动注入
    不可关闭的 sticky 顶部横幅“当前为在线演示版（纯前端 BYOK）——本机 Agent 能力需本地运行
    完整版…”，位于 body 首元素；根产物与发布包不含横幅（hash 按组校验：根/release 一组、
    web 两份一组，差异为有意设计）。
  - sw.js 缓存名绑定内部构建号：assemble 构建时把模板 CACHE_NAME 替换为 `moray-<MORAY_BUILD>`，
    升级后浏览器自动换缓存、不再依赖用户硬刷新；修复 assemble.py 缺 `import re` 导致
    stage 替换未生效的问题。
  - README「在线部署」节补充：线上为演示版无 Agent、更新需整文件夹覆盖重传、PWA 自动取新包。
- **#7 重试语义显式化**（125_tools.js）：步骤卡新增“重放标记”——`retryStep` 重放后记录
  `replayed{at, changed}`（结果与上次对比），stepHtml 渲染警示行“已重放该步（仅重新执行工具，
  不自动改写已生成回复）· 结果与上次不同/一致 · 可点「重新生成」让模型基于最新结果作答”；
  重放结果更新进步骤卡与持久化 toolCalls（重建后标记仍在），toast 同步明确该语义。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  产物断言：web 两份含 #demoModeBanner 且位于 body 首、根/release 不含；sw CACHE_NAME=
  moray-3.18.3。hash：根=release=`d28dd9e3…`，web 两份=`3aa86780…`（横幅分组）。

'''
out = lines[:2] + [entry] + lines[2:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('batch3 changelog inserted')
