"""[国庆冲刺 Day7] ExecutionEngine 抽象接口（任务 1）

定义"通用执行引擎"的统一契约：任何后端（本机目录、沙箱、远端容器……）
只要实现这 5 个方法，就能被 MoRay 以同一套语义调度与审计。

设计要点：
- capabilities() 先声明能力，上层据此做准入判断（例如 can_exec=False 的引擎
  绝不会被派发执行类任务），而不是等任务跑一半才发现不支持；
- start() 只负责受理任务并返回 task_id，执行可以是同步也可以是异步，
  调用方一律通过 status()/result() 轮询，与 agent_runner 的 Run 语义同构；
- 状态词表固定四态：pending / running / completed / failed。
  "取消"不是独立状态：cancel 成功后任务收敛为 failed（error="canceled"），
  与 Day5 会话状态机 timeout/canceled→failed 的收口惯例一致。
"""
import abc

# 状态词表（四态，见模块 docstring；上层与测试都以此为准）
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED})


class StreamTaskError(Exception):
    """[引擎解耦 v1 2026-10-21] 流式任务扩展面的统一错误契约（可选扩展，非五方法契约的一部分）。

    流式扩展面（可选，引擎按需实现）：start_stream(body) / stream_events(task_id) /
    kill_stream_task(task_id) / read_task_file(task_id, filename)——api.py 通用派发路由
    /api/engines/{engine_name}/* 只认这四个方法名，缺实现的引擎回 engine_unsupported。

    错误语义：code/message/status 由路由层翻译为 _bad(code, message, status)；
    body 可携带非标准响应体（如 files 端点 413 的 {"ok": false, "error": "file too large"}），
    路由层优先使用 body。
    """

    def __init__(self, code: str, message: str, status: int = 400, body: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.body = body


class ExecutionEngine(abc.ABC):
    """通用执行引擎抽象基类。

    子类必须提供类属性 NAME（引擎唯一短名，供 /api/engines/list 与上层路由使用）
    并实现全部 5 个抽象方法。直接实例化本类会抛 TypeError（abc 保证）。
    """

    #: 引擎唯一短名（子类覆盖，如 "local"）
    NAME: str = "abstract"
    #: 一句话描述（子类覆盖；/api/engines/list 原样透出）
    DESCRIPTION: str = ""

    @abc.abstractmethod
    def capabilities(self) -> dict:
        """声明引擎支持的能力，至少包含以下布尔键：

        - can_read    能否读文件
        - can_write   能否写文件
        - can_exec    能否执行进程/命令
        - can_network 能否发起网络请求
        - is_isolated 运行环境是否与宿主隔离（沙箱/容器为 True）
        """

    @abc.abstractmethod
    def start(self, task: dict) -> str:
        """受理一个任务，返回 task_id。

        task 的结构由各引擎自行定义；不合法的任务应拒绝受理或立即置为
        failed（带 error），绝不允许静默吞掉。
        """

    @abc.abstractmethod
    def status(self, task_id: str) -> dict:
        """返回任务状态 dict，至少包含：

        - task_id: str
        - status:  pending / running / completed / failed 之一

        未知 task_id 抛 KeyError。
        """

    @abc.abstractmethod
    def cancel(self, task_id: str) -> bool:
        """取消任务。True=取消成功（任务收敛为 failed，error="canceled"）；
        False=无法取消（未知任务，或已处于终态）。"""

    @abc.abstractmethod
    def result(self, task_id: str) -> dict:
        """返回任务结果 dict，至少包含：

        - task_id / status
        - result: 成功产物（失败时为 None）
        - error:  失败原因（成功时为 None）

        未知 task_id 抛 KeyError。
        """
