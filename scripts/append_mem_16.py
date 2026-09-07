# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**v3.17.2 阶段1.6 启动残留修复+演示顺滑**：
- M1 启动通知残留根因=静态骨架第一阶段脚本 L5337"MoRay 已就绪(⌘K)"（不可改区）+onboarding finish"一切就绪"(110:348)——根治=135 通知覆盖层加**启动窗口合并**（load 后 12s 内启动类[标题含已就绪/一切就绪或消息含 ⌘K/后端状态]只留首条,⌘K 引导并入首条 message,后续静默丢弃且后端状态反向并入首条）
- IAB **clip 截图绕开平铺伪影**：screenshot({clip:{x:0,y:0,w≤980,h≤640}}) 走正确合成管线（全帧/全页均平铺,pane 物理约 1000×654）；响应式截图=clip(w≤980,h≤640) 循环四档六页
- M3 示例工作区：banner「示例」按钮→loadSampleWorkspace（notes/a.txt、notes/b.txt、todo.md 中文自洽；read 探测已存在则跳过绝不覆盖；write approved 由按钮点击授权）；欢迎页第 5 卡「试试本地 Agent」（native 开启+后端在线才显示）→自动开开关+开关 native-on-pulse 脉冲 2s+载入示例+自动填演示任务；模型自检未通过时 banner 内一行温和提示
- M2 修坑：patch 脚本按行号重写函数体时**误删 bar=createElement/bar.id 两行**（症状=getElementById 永远 null+innerHTML of null）——重写函数体必须保留全部初始化行；计划状态流转同步 msg.plan（observe 终态分支+finishRemaining,否则会话重建计划卡显示初始 todo）
- 版本 3.17.2 四份 SHA256=971955fb3a28fcadc6e8f084e6d76fb77861e8a823f25ec0836f01ee55b86f71；备份 pre_stage16_20260905_164256
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
print('appended:', 'v3.17.2' in open(p, encoding='utf-8').read())
