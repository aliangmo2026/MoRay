# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
lines = io.open(p, encoding='utf-8').readlines()
entry = '''## 3.18.4（2026-09-07）修复批次 4：[#6] 短消息回复风格短路 + [#8] 写类工具信任指纹加内容哈希

- **#6 短问候短路**（30_chat.js buildRequestMessages）：新会话首条 ≤8 字符的纯问候
  （你好/在吗/hi/hello/谢谢/嗯 等白名单 + 结尾标点）且无引用与附件时，系统提示追加约束
  “对简短问候只做简短回应（一两句），不展开介绍功能、不罗列能力”。真机验证（qwen2.5:7b）：
  新会话发“你好”→ 回复 13 字符“你好！有什么可以帮到你吗？”，无长篇罗列。
- **#8 指纹加入内容时序**（125_tools.js）：新增 `agentFingerprintForApproval` ——
  write_file/edit_file 的审批用指纹额外并入“目标文件当前内容哈希”（fnvHash64，读失败记
  missing）；文件内容已变化时即使工具+参数相同也重新弹审批；read 类只读工具不受影响。
  勾选“本会话信任”时写入的也是含内容哈希的指纹（okBtn 改 async）。函数级断言：
  同参数写内容 A→指纹 fp1；文件改为 B 后同参数指纹 fp2≠fp1；写回 A 后 fp3==fp1
  （内容未变时免确认语义仍生效）；read_file 指纹不含内容哈希。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`7e03365a…`，web 两份=`bc781757…`（演示横幅分组）。sw CACHE_NAME=moray-3.18.4。

'''
out = lines[:2] + [entry] + lines[2:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('batch4 changelog inserted')
