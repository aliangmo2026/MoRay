# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**v3.17.0 阶段1 多步Agent（M1-M5）**：
- M1 真机结论：Ollama tools 3轮统计 qwen2.5:7b 唯一全稳定(3/3)，qwen3.5 系列 2/3 偶发漏调，1.5b 不可靠；流式 tool_calls 实测一次性全量(frag=1)无分片；解析加固=sanitizeToolCalls(空过滤/args 对象↔字符串/tryCompleteJson 截断补全)+chatStream 增量聚合(accToolCallDeltas/Finalize,OpenAI delta 分片+Ollama 对象态)+_finish 透出 toolCalls；Agent 自检=banner「自检」按钮发 1 次非流式最小 tools 请求,结果存 agentSelfCheck；server/.env 的"云端"实为本地 Ollama /v1 端点(OpenAI 兼容)——cloud_tools_smoke.py 经 llm_proxy 透传 tools PASS
- M2 三工具：find_files(fnmatch 递归,手动 scandir 跳 symlink+解析位置核对防遍历逃逸,限1000)/search_text(行级,200 条,非法正则报错,二进制/非UTF8 跳过计数)/edit_file(唯一匹配校验,replace_all,before/after 片段,危险后缀+binary_file+not_utf8 拒)——edit_file 加入 SIDE_EFFECT_TOOLS；单测增至 97 项
- M3 编排：submit_plan 纯前端工具(native 门控,≤12 步)→PlanTracker 登记→计划卡(时间线上方,状态色 todo/running/done/failed/skipped)；onToolStep 按"工具名+最早待办"匹配推进,fails>=3→skipped 防死循环；生成完成回调剩余待办置 done；msg.plan 持久化+renderStatic 重建
- M4：文件树改常驻侧栏(#wsSidebar fixed 浮层可收起,容器 class .ws-tree-list/.ws-tree-preview 共享查询,模态互斥解除实测)；buildApprovalDiff=行级 LCS(≤400 行,-红/+绿/上下文灰,变更行±2 上下文)；write_file 审批自动 read 旧内容出全文件 diff(>64KB 注明),edit_file diff=old_str→new_str；moray:ws-file-written 事件驱动刷新+高亮 2s
- M5：PlanTracker.SYSTEM_PROMPT 内置 Agent 规范,设置卡可查看/覆盖(agentSysPromptOverride)/恢复；buildRequestMessages 仅 native 开启且带本机工具时追加
- e2e 37 断言全绿(S7 计划/S8 edit 审批/S9 拒绝不变/SEC find 越界)；产物四份 SHA256=2fcf395bb83939d28dec185046f2838b53b47c0b1f96dc59ea6c81878c124c24；备份 pre_stage1_20260905_124054
- **坑**：mock_llm_server.py 与 e2e 内嵌 mock 的剧本要同步维护(浏览器测试用的是前者)；Python dict 括号配平在嵌套 JSON 剧本里易错(py_compile 先行)；IAB 页面 reload 后 banner 常驻化(空会话也要显示,否则自检/文件树入口丢失)
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
print('memory appended:', 'v3.17.0 阶段1' in open(p, encoding='utf-8').read())
