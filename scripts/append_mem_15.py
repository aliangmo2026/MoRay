# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**v3.17.1 阶段1.5 UI/UX 精修**：
- 新增 parts/135_ui_polish.js（第 19 分片，assemble PARTS 已加）：**静态骨架的 showNotification(5147 行不可改区)用同名 window.showNotification 覆盖升级**——NotificationRuntime 队列(同屏≤3 新顶旧)/分类时长(成功3.5s信息5s警告8s错误10s)/hover 暂停(进度条 paused+计时器剩余)/类型图标+左缘状态色/深色 scrim+blur/max-width(min(400px,100vw-32px))/role=status|alert；精修 CSS 全部集中 uiPolishStyle 注入(模态 scrim/焦点环/滚动条/grid 4-3-2-1 列响应式+1560 居中/输入台横滚/native ON glow/计划卡折叠/审批 Enter/侧栏拖拽 220-460/caret 旋转/空态 empty-state/prefers-reduced-motion)
- 启动通知合并：110_polish probeBackend 尾部唯一一条摘要(后端连接+模型数)，删 onboarding finish"一切就绪"与"纯前端(离线)"独立通知；状态栏"离线模式"→"本地后端未启动"
- 审计升级：showAgentAuditLog 表格化+分页 25/页+导出 JSON(downloadJson)+清空(后端新增 DELETE /api/agent/log+crud.clear_agent_log,前端二次确认)
- **IAB 截图管线在本机 DPI 环境对视口渲染产生 2×2 平铺伪影(1920/1440/1600/1280 全中,fullPage 同样)**——DOM 诊断证明单份 #app/scrollW 正常；响应式自证改用 DOM 程序化诊断(scrollWidth/重叠/可见性,四档 0 issues)+截图留档并注明伪影
- 版本 3.17.1 四份 SHA256=b26eae8129a61857fc07a9abfc4a70917a53e1ace68dfcfb3d0b8a028aa2689f；备份 pre_ui_polish_20260905_151706
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
print('appended:', 'v3.17.1' in open(p, encoding='utf-8').read())
