# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**3.18.1-3.18.5 修复批次（2026-09-07，20 项）**：
- b1(3.18.1) #1 Agent 入口离线置灰(agentOfflineReason+输入台开关 native-offline+banner 三按钮灰化,守卫点击弹明确原因) #2 agentRecommendedModel(qwen2.5:7b 优先)/agentApplyRecommendedModel(自检未过→切默认,可手改)
- b2(3.18.2) #3 冷启动占位(2s 无首 token→typing-label 文案切加载中) #4 附件"仅存本机"角标+设置数据管理存储边界说明
- b3(3.18.3) #5 assemble --web 注入演示横幅(sticky body 首,web 两份含/根不含→hash 分组校验)+sw CACHE_NAME 绑定 MORAY_BUILD(构建自动递增,升级免硬刷新);修复 assemble 缺 import re #7 步骤卡 replayed{at,changed} 警示行"已重放该步仅重放不自动改写"
- b4(3.18.4) #6 新会话≤8 字纯问候→系统提示追加简短约束(真机"你好"→13 字) #8 agentFingerprintForApproval 写类并入 fnvHash64(目标内容),内容变→重审批(okBtn async)
- b5(3.18.5) #9 GatewayBreaker localStorage 持久化(moray_breaker_<BUILD>) #10 GET messages 404(前端无 GET 调用) #11 hidden 已有复核 #14 assemble 三处版本一致性校验(不一致构建失败并报差异) #16 状态栏离线加(估算) #17 native 首次开启弹安全说明(一次性 localStorage) #20 ≤900px 背景 scroll 兜底
- Tabbit CLI 验证链路：$LOCALAPPDATA\\Tabbit\\LocalAgent\\bin\\tabbit-cli.exe nodejs --task NAME + stdin JS（非 JSON 帧）
- 最终 hash：根=release ffa6ded4f4978301…，web×2 19f4e7e262e629e8…（横幅分组）；CHANGELOG 逐批
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
print('appended:', '修复批次' in open(p, encoding='utf-8').read())
