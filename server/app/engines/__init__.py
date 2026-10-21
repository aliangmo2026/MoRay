"""[国庆冲刺 Day7] 通用执行引擎包

- base.py          ExecutionEngine 抽象接口（capabilities/start/status/cancel/result）
- local_adapter.py LocalAdapter 内置最小实现（仅白名单目录 write_file）+ /api/engines/list 只读路由

红线约束：本包全部为新增文件；挂载 main.py 只允许 1 行 import + 1 行 include_router。
"""
