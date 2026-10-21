"""[国庆冲刺 Day7] LocalAdapter：内置最小执行引擎（任务 2 + 任务 3 的路由）

定位：演示 ExecutionEngine 契约如何落地 + 无外部后端时的兜底引擎。

安全边界（与 agent_tools 的受控工作区思想一致，但独立实现、互不依赖）：
- 路径白名单：只允许写 D:\\MoRay\\workspace\\local_engine\\ 之下的文件。
  相对路径以白名单根解析，绝对路径与 ".." 穿越一律先 resolve() 再验祖先，
  任何落在白名单外的目标直接拒绝（failed，error=path_outside_whitelist）。
- 动作白名单：只支持 {"action": "write_file", "path": "...", "content": "..."}。
  exec / network / read_file 等其余动作一律拒绝（failed，error=action_not_supported），
  因此"读取白名单外文件"天然不可能发生——本引擎根本不接受任何读任务。
- capabilities 诚实声明：can_exec=False、can_network=False、is_isolated=False，
  上层派发前即可判定此引擎不能碰进程与网络。

状态管理：内存字典 _tasks（进程内、重启即清零——与 Day1 熔断计数同一取舍：
兜底引擎不值得引入持久化，审计仍由上层事件流负责）。任务同步执行，
start() 返回时已处于终态（completed/failed），故 cancel() 恒为 False。
"""
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter

from .base import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ExecutionEngine,
)
from .laya_adapter import LayaEngine  # [引擎解耦 v1 2026-10-21] Laya 8001 编排引擎（构造无副作用）
from .zcode_adapter import ZCodeAdapter  # [Day10] engines_list 注册用（构造无副作用，目录懒创建）

# 任务 2.c：路径白名单根（唯一允许写入的目录）
DEFAULT_ROOT = Path(r"D:\MoRay\workspace\local_engine")

# 唯一支持的动作（任务 2.b）
SUPPORTED_ACTIONS = frozenset({"write_file"})


class LocalAdapter(ExecutionEngine):
    """本机目录写入引擎：仅白名单内 write_file，零执行、零网络、零隔离。"""

    NAME = "local"
    DESCRIPTION = "内置最小执行引擎：仅允许写 workspace/local_engine 白名单目录（演示与兜底）"

    def __init__(self, root: Path | str | None = None):
        # root 可注入仅为测试隔离；生产路径固定 DEFAULT_ROOT
        self.root = Path(root).resolve() if root else DEFAULT_ROOT
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ ExecutionEngine 契约
    def capabilities(self) -> dict:
        """任务 2.a：能力声明（诚实上报，上层据此准入）"""
        return {
            "can_read": True,       # 架构上有读能力，但当前任务词表故意不放行任何读动作
            "can_write": True,
            "can_exec": False,
            "can_network": False,
            "is_isolated": False,
        }

    def start(self, task: dict) -> str:
        """受理任务并同步执行。任何拒绝都以 failed+error 落记录，绝不静默。"""
        if not isinstance(task, dict):
            raise ValueError("task must be a dict")
        task_id = uuid.uuid4().hex[:12]
        rec = {
            "task_id": task_id,
            "status": STATUS_PENDING,
            "task": dict(task),
            "created_at": time.time(),
            "result": None,
            "error": None,
        }
        with self._lock:
            self._tasks[task_id] = rec
        self._execute(rec)
        return task_id

    def status(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {"task_id": task_id, "status": rec["status"], "error": rec["error"]}

    def cancel(self, task_id: str) -> bool:
        """任务同步执行、start 返回即终态，因此正常情况下恒为 False。
        保留 pending/running 分支以严守接口语义（未来改异步时行为不变）。"""
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec and rec["status"] in (STATUS_PENDING, STATUS_RUNNING):
                rec["status"] = STATUS_FAILED
                rec["error"] = "canceled"
                return True
            return False

    def result(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {
            "task_id": task_id,
            "status": rec["status"],
            "result": rec["result"],
            "error": rec["error"],
            "created_at": rec["created_at"],
        }

    # ------------------------------------------------------------ 内部实现
    def _get(self, task_id: str) -> dict:
        with self._lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            raise KeyError(f"unknown task_id: {task_id}")
        return rec

    def _in_whitelist(self, target: Path) -> bool:
        """白名单判定：resolve 后必须是 root 本身的后代（root 本身不可作为写入目标）"""
        try:
            rp = target.resolve()
        except OSError:
            return False
        return rp != self.root and rp.is_relative_to(self.root)

    def _execute(self, rec: dict) -> None:
        rec["status"] = STATUS_RUNNING
        task = rec["task"]
        try:
            action = task.get("action")
            # 拒绝规则 2.e：exec / network / 读任务及一切未知动作
            if action not in SUPPORTED_ACTIONS:
                raise PermissionError(
                    f"action_not_supported: {action!r}（LocalAdapter 仅支持 write_file；"
                    "exec/network/读文件一律拒绝）"
                )
            raw_path = str(task.get("path") or "").strip()
            if not raw_path:
                raise ValueError("missing path")
            content = task.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a string")

            p = Path(raw_path)
            target = p if p.is_absolute() else (self.root / p)
            # 拒绝规则 2.e：越界路径（含 ".." 穿越、盘符绝对路径、白名单外绝对路径）
            if not self._in_whitelist(target):
                raise PermissionError(f"path_outside_whitelist: {raw_path}")

            resolved = target.resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            rec["status"] = STATUS_COMPLETED
            rec["result"] = {"path": str(resolved), "bytes": len(content.encode("utf-8"))}
        except Exception as exc:  # noqa: BLE001 - 拒绝与故障统一收敛为 failed+error
            rec["status"] = STATUS_FAILED
            rec["error"] = str(exc)


# ------------------------------------------------------------ 任务 3：只读路由
router = APIRouter()

# 模块级默认实例：仅用于 /api/engines/list 的能力申报，构造无任何副作用（目录懒创建）
_default_engine = LocalAdapter()
_default_zcode_engine = ZCodeAdapter()  # [Day10] 骨架注册：capabilities 诚实申报 is_isolated=False
_default_laya_engine = LayaEngine()  # [引擎解耦 v1 2026-10-21] Laya 8001 编排引擎注册

# [引擎解耦 v1 2026-10-21] 模块级注册表：engines_list 只读路由与 api.py 通用派发路由
# （/api/engines/{engine_name}/*）共用同一名单；新增引擎时在本列表登记即可。
all_engines = [_default_engine, _default_zcode_engine, _default_laya_engine]


def get_engine_by_name(name: str):
    """按 NAME 短名（小写）查注册表，未知名返回 None（路由层据此出 404）。"""
    key = str(name or "").strip().lower()
    for e in all_engines:
        if str(getattr(e, "NAME", "")).lower() == key:
            return e
    return None


# [Day23.5] 引擎实时态：只读汇总各引擎**已有的**内存任务表（_tasks）。
# 不新增任务跟踪机制、不改执行流程、不引入状态机——属"在现有结构上多返回几个已有字段"。
#
# status 取值（当前只可能产出前两个，第三个刻意未实现，见 docs/day23.5_engines_backend.md §3）：
#   "idle"  — 无处于 pending/running 的任务
#   "busy"  — 至少一个任务处于 pending/running
#   "offline" 无存活探针可用（zcode 的 CLI 是否在 PATH、Docker 守护是否可达均不可知），
#             凭空返回该值属编造，故本引擎列表暂不产出。
#
# current_task：并发多个时取**最早开始**的那个（started_at/created_at 最小）；真实并发度由
#   task_count 承载，不隐藏信息。name 取任务自报的 action——本契约里任务的唯一名称来源
#   （两个引擎共用同一 task 形状，任务体没有 name 字段，不臆造人类可读名）。
#   started_at 沿用本模块既有惯例的 epoch 秒浮点（与 created_at/ended_at 同口径）；
#   pending 阶段尚未置值时为 null。
def _engine_live_state(e) -> dict:
    """从引擎既有的内存任务表读实时态（纯只读；与引擎自身 _lock 同一互斥，防 GC 删条目竞态）。"""
    # [Day23.6] 引擎可自报"静态不可用"（纯路径检查，实现见 ZCodeAdapter.unavailable_reason）。
    # 本助手只负责问一句，探什么由引擎自己决定；未实现该钩子的引擎按"可用"处理
    # （LocalAdapter 本轮不动——它的 offline 依赖 Ollama 可达性，另立项）。
    probe = getattr(e, "unavailable_reason", None)
    if callable(probe):
        try:
            reason = probe()
        except Exception:  # noqa: BLE001 - 探针异常一律按不可用处理，绝不崩
            reason = "probe_failed"
        if reason:
            return {"status": "offline", "current_task": None,
                    "task_count": 0, "offline_reason": reason}
    tasks = getattr(e, "_tasks", None)
    lock = getattr(e, "_lock", None)
    snapshot = []
    if isinstance(tasks, dict):
        try:
            if lock is not None:
                with lock:
                    snapshot = list(tasks.values())
            else:
                snapshot = list(tasks.values())
        except Exception:  # noqa: BLE001 - 实时态汇总失败绝不该让整个引擎列表不可用
            snapshot = []
    active = [r for r in snapshot
              if isinstance(r, dict) and r.get("status") in (STATUS_PENDING, STATUS_RUNNING)]
    active.sort(key=lambda r: r.get("started_at") or r.get("created_at") or 0)
    current = None
    if active:
        head = active[0]
        task = head.get("task") if isinstance(head.get("task"), dict) else {}
        current = {
            "id": head.get("task_id"),
            "name": task.get("action"),
            "started_at": head.get("started_at"),
        }
    return {
        "status": "busy" if active else "idle",
        "current_task": current,
        "task_count": len(active),
        "offline_reason": None,
    }


@router.get("/api/engines/list")
def engines_list():
    """[Day7] 当前可用执行引擎列表（只读；新增引擎时在 _all_engines 里注册）

    [Day23.5] 追加实时态字段 status / current_task / task_count（只读汇总，既有字段
    name/description/capabilities 原样保留、语义不变）。
    """
    return {
        "ok": True,
        "engines": [
            {"name": e.NAME, "description": e.DESCRIPTION, "capabilities": e.capabilities(),
             **_engine_live_state(e)}
            for e in all_engines
        ],
    }
