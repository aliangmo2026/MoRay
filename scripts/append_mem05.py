# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**v3.16.1 阶段0.5 闭环打磨+自动化回归**：
- scripts/agent_e2e_check.py=协议层全链路 e2e(mock 上游线程HTTPServer剧本驱动+TestClient 真实 app[MORAY_DB/WORKSPACE 指 %TEMP%]+run_agent_loop 模拟器 1:1 复刻 runWithTools 协议),6 场景 27 断言全绿;TestClient 用 with 块才触发 lifespan;mock 特判场景值必须是 list/专属分支(字符串会让 handler step.get 崩)
- 步骤卡:ToolRegistry.formatArgs(write=路径+字符数,command=完整命令行)+展开区完整 resultFull(max-h-40 滚动)+失败真实错误[data-tool-err]+retryStep(消息 toolCalls[name/args]重放走完整安全层审批,就地更新,toast 引导重新生成);摘要行 stepsSummaryText(共 N 步·总耗时 Xs,data-ms 累计)——renderLiveStep 终态后 250ms maybeCollapseSteps,新事件先展开;renderSteps 历史同结构默认折叠;步骤卡容器从 .tool-steps 变 .tool-steps-wrap(summary+.tool-steps)
- 对比串行停止:CompareApp.__pendingCols 登记/runOne 销账/stop() 给未轮到列写"已停止（未开始）"(contentEl 空才写)+__stoppedByUser 防 run 尾覆盖状态文案
- 文件树(125_tools.js):modal 树,dirCache 缓存懒加载,wsFetchTool 只读直调,read_file 预览 64KB,moray:ws-file-written 事件刷新+高亮 2s(父级链自动展开);modal 阻塞聊天→写入与面板互斥,自动刷新仅事件可触发(日常流=任务后打开看最新)
- 信任指纹:__agentTrustedOps Map(name+键排序稳定 JSON),审批卡勾选(仅非 run_command)入表,agentNativeRun 短路,设置卡计数+清除;内存态刷新失效
- 60s 健康探测 hidden 跳过在 v3.15 已实现(startHealthMonitor L219),别重复改
- 产物四份 SHA256=34550975e6e13d594feea3a5be439fec3bc03ee9a37deec07e40f57351186afc;备份 pre_agent05_20260905_114207
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
txt = open(p, encoding='utf-8').read()
print('appended, has 0.5 section:', 'v3.16.1 阶段0.5' in txt, '| total chars:', len(txt))
