# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
section = '''
**v1.0.0 正式版封版（2026-09-07）**：
- 对外版本 0.3.0→1.0.0（110 MORAY_VERSION + config VERSION），内部构建号统一 3.18.0（MORAY_BUILD/APP_VERSION/config BUILD）；关于页版本行去硬编码 v3.0.0 改动态 MORAY_VERSION；build_release 按 config VERSION 自动产出 release/MoRay-v1.0.0(.zip)（脚本读 config 无需改）；旧 release/MoRay-v0.3.0 保留为历史发布包
- README 正式版门面：Agent 三张实机截图入 docs/screenshots/agent-*.png；核心特性加本机 Agent 小节；快速开始标注"纯前端/在线 BYOK 无本机 Agent"边界+Agent 模型建议表(真机实测 qwen2.5:7b 最稳)；架构图补 /api/agent 与审计表；parts 18→19；路线图勾选六项
- 清理 scripts/peek_copy.py、scan_tmp2.py（无引用勘察脚本）；正式测试三件套保留
- 四份产物 SHA256=ce13bc342fcfed4a2728b533608f96455e94f6b9b096bb1744edef88c8acf436；备份 pre_v1_release_20260907_161404
'''
with open(p, 'a', encoding='utf-8') as f:
    f.write(section)
print('appended:', 'v1.0.0 正式版封版' in open(p, encoding='utf-8').read())
