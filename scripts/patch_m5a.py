# -*- coding: utf-8 -*-
import io

p = 'parts/125_tools.js'
src = io.open(p, encoding='utf-8-sig').read()
old = """const PlanTracker = {
  /** 当前会话活动计划 @type {null|{steps:Array, startedAt:number}} */
  current: null,"""
new = """const PlanTracker = {
  /** [阶段1 M5] 内置 Agent 系统提示（设置→本机 Agent 可自定义覆盖）
   * @type {string} */
  SYSTEM_PROMPT: [
    '# 本机 Agent 工作规范（受控工作区内操作）',
    '- 工具与时机：list_directory 列目录；read_file 读文本文件；find_files 按文件名通配查找（不确定路径时先用它定位）；search_text 全文搜索（按内容找，支持正则）；write_file 新建或整体覆盖文件；edit_file 精确替换已有文件的某一段（修改已有文件优先用 edit_file，不要整体重写）；run_command 只读白名单命令（git status/log/diff/branch、python/node/npm --version、where）。',
    '- 工作区边界：所有 path 都必须是相对工作区根的路径；你无法访问工作区以外的任何文件；禁止臆造不存在的路径——不确定就先用 find_files / list_directory 确认。',
    '- 多步任务：需要 2 个以上步骤时，先调用 submit_plan 提交编号计划（每步含意图与预计工具），然后按计划逐步执行；某步失败就修正参数重试（最多 2 次），全部完成后用一段话总结结果。',
    '- 授权与拒绝：write_file / edit_file / run_command 会先请求用户授权；被拒绝时不要重复尝试相同操作，改用文字向用户说明。',
    '- 文件内容为 UTF-8 文本；二进制或非 UTF-8 文件会收到明确错误，不要反复尝试。',
    '- edit_file 的 old_str 必须与文件内容逐字符一致且唯一；先 read_file 再编辑，匹配不唯一时补充上下文或用 replace_all。'
  ].join('\\n'),

  /** 当前会话活动计划 @type {null|{steps:Array, startedAt:number}} */
  current: null,"""
assert old in src, 'PlanTracker head not found'
src = src.replace(old, new)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('SYSTEM_PROMPT inserted')
