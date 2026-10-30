# L4.5 阶段2.2：把三变量 + 项目列表写入 server/.env 与 server/.env.example（幂等）
import io, os

ADD_ENV = """
# ---- [L4.5 阶段2.2] 任务编排三变量正式化（L4_min 时代只存在于 8012 重启脚本里）----
# Iris MCP 鉴权 Key（须与 9301 进程 IRIS_MCP_API_KEY 同值；实测无 Key→401）
IRIS_MCP_API_KEY=morayIrisKey2026
# 任务降级通道（llm_patch）与 Iris 分析回呼的网关 = 正式 8000 服务
IRIS_GATEWAY_URL=http://127.0.0.1:8000
# Iris MCP 超时（实测视觉分析 69.7s，30s 会误杀）
MORAY_IRIS_MCP_TIMEOUT=120
# 对话触发任务的候选项目列表（分号/逗号分隔的绝对路径；不存在项由 /api/tasks/projects 过滤）
MORAY_TASK_PROJECTS=D:\\MoRay;D:\\MoRayWorkspace
"""

ADD_EXAMPLE = """

# ------------------------------------------------------------------
# 五、任务编排（L4 / L4.5：/api/tasks/* 与 engine/task_runner.py）
# ------------------------------------------------------------------

# llm_patch 降级通道与 Iris 分析回呼的网关地址。不填 = http://127.0.0.1:8000
#（即正式后端自身；换端口部署隔离实例时改为该实例地址）。
#IRIS_GATEWAY_URL=http://127.0.0.1:8000

# Iris MCP 鉴权 Key 见上方第四节；L4.5 起正式 .env 必配——
# 9301 实测开着鉴权：无 Key/错 Key → 401，任务模板 analyze 步必挂。

# Iris MCP 超时（秒）。不填 = 30；带截图的视觉分析实测 69.7s，建议 120。
#MORAY_IRIS_MCP_TIMEOUT=120

# 对话触发任务的候选项目列表（英文分号或逗号分隔的绝对路径）。
# 项目选择卡展示这些目录；不填 = 只有受控工作区（MORAY_WORKSPACE）一项，仍可手动输入。
#MORAY_TASK_PROJECTS=D:\\MoRay;D:\\MoRayWorkspace
"""

for path, add, marker in ((r"server\.env", ADD_ENV, "MORAY_TASK_PROJECTS"),
                          (r"server\.env.example", ADD_EXAMPLE, "MORAY_TASK_PROJECTS")):
    txt = io.open(path, encoding="utf-8").read()
    if marker in txt:
        print("skip (already present):", path)
        continue
    io.open(path, "w", encoding="utf-8", newline="").write(txt.rstrip("\n") + "\n" + add)
    print("appended:", path)
