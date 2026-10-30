# -*- coding: utf-8 -*-
"""L7.6 任务编排闭环模块包（2026-09-29）。

四个子模块：
  * task_planner —— 目标 → 结构化步骤列表
  * tool_registry —— iris/argus/zcode/file 四类工具统一调用
  * self_check —— 每步结果自检（规则 + 小模型判断）+ 重试参数修正
  * executor —— 按步骤串行调度执行、上下文串联、进度事件流、取消

对外只走 laya_std_server.py 的 /planner 与 /execute（SSE）两个新接口，
本包内任何模块都不 import 主服务（避免循环依赖）。
"""
