# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
lines = io.open(p, encoding='utf-8').readlines()
entry = '''## 3.18.2（2026-09-07）修复批次 2：[#3] 冷启动等待反馈 + [#4] 附件持久化边界显式化

- **#3 冷加载等待反馈**（30_chat.js）：打字指示器（typing-indicator）在发送 2 秒后仍无
  首 token 时，标签文案切换为“模型加载中（冷启动约 5~15s，首次使用后更快）…”——
  首 token 到达或收尾移除指示器后自然消失，不再表现为“白等卡死”。
  保留 keep_alive 30m 透传（既有）不动。
- **#4 附件仅存本机显式化**（30_chat.js / 70_models_settings_boot.js / 135_ui_polish.js）：
  带图片的消息时间行新增“仅存本机”小角标（hover 说明：清除浏览器数据或更换设备将丢失）；
  设置→数据管理新增“存储边界”说明块（图片附件仅存本机浏览器 IndexedDB，文本消息可经
  本地后端 SQLite 同步，附件不随文本同步不外传）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL、
  四份产物 SHA256 一致 = `cc9dcb36b9316fa1130d43832d6bfc39598618e27278f11562c1acddddf56162`（3.18.2）。

'''
out = lines[:2] + [entry] + lines[2:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('batch2 changelog inserted')
