"""[国庆冲刺 Day7] 通用执行引擎包

- base.py          ExecutionEngine 抽象接口（capabilities/start/status/cancel/result）
- local_adapter.py LocalAdapter 内置最小实现（仅白名单目录 write_file）+ /api/engines/list 只读路由
- zcode_adapter.py ZCodeAdapter ZCode CLI 流式执行引擎（spawn stream-json + 流式扩展四方法）
- laya_adapter.py  LayaEngine Laya 8001 决策编排引擎（reader 线程桥接 SSE + 流式扩展四方法）

红线约束：本包全部为新增文件；挂载 main.py 只允许 1 行 import + 1 行 include_router。
"""
