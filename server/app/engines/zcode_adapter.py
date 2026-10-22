"""[国庆冲刺 Day10 骨架 → Day15 真身] ZCodeAdapter：宿主侧 subprocess 直调 ZCode CLI

形态分阶段（设计文档 c.0；Day 10 骨架 → Day 15 真身）：
- [Day15] Docker 隔离层已在 Day11b-b 通过验收（bat 真机 9/9 全绿，见 docs/day11b_b_report.md），
  capabilities 升格诚实申报 is_isolated=True；同时接入 Day2 事件流审计
  （zcode_task_started / zcode_task_finished）、Day3 agent_sessions 状态机
  （pending → running → completed/failed）、以及内存 GC（cleanup_stale_tasks）。
  审计/状态写入一律"失败只 warning"，绝不阻断任务主流程（与 agent_runner 同一铁律）。
  注：本引擎仍走宿主 subprocess 直调 CLI，容器隔离由上层 Docker 编排层（engines/docker/）
  在外围提供；is_isolated=True 申报的是"该引擎已被验证可在隔离编排下运行"这一事实。
- 全部 flag 以 Day 9 实测为准（D:\\ZCode_Test\\ZCode_CLI_Flag实测报告.md，CLI 0.16.9）：
  `-p`（无头提示词，缺省 mode 时默认 yolo——故必须显式传）、`--mode edit`（强制，
  不接受任务参数覆盖）、`--cwd`（钉死沙箱白名单）、`--disallowed-tools`（强制黑名单，
  `--allowed-tools` 不存在）、`--json`（公开机器可读输出）。
  ZCode CLI 的 strict 解析器对未知 flag **直接拒绝并 exit 1**，命令行没有写错的空间。
- 认证/模型无 CLI flag：走 provider 配置文件 + 环境变量注入
  （设置 MORAY_ZCODE_PROVIDER_CONFIG，本引擎透传为 ZCODE_PERSONAL_PROVIDER_CONFIG_FILE；
  配置内 baseUrl 应指向 MoRay 网关）。

与 agent_runner 同构的既有模式（复用成熟套路，不发明新轮子；按 Day7 LocalAdapter
"独立实现、互不依赖"惯例，小工具函数本地复刻而非跨包 import 私有函数）：
- 提示词落盘 + 短 argv 指令（[2026-10-05 stdin 修复] 原 `-p -` stdin 形态实测证伪：
  CLI 的 `-p` 是 `--prompt <text>` 带值参数，`-` 被当字面提示词、stdin 从未被消费——
  Iris 首跑 exit 0 空转实证。按设计文档 c.2 预设退化：完整提示词写沙箱 cwd 内文件，
  argv 只传一句 Read 指令；绕 cmd.exe 转义地狱的初衷不变）；
- 双守护线程逐行读 stdout/stderr；单行解码 UTF-8 优先、GBK 回退；
- watchdog 轮询 poll()；超时/取消走 taskkill /T /F 收树；
- 一切拒绝以 failed+error 落记录，绝不静默；状态严格四态词表，
  timeout/canceled 收敛为 failed（error 区分原因，Day5/Day7 惯例）。

状态管理：内存字典 _tasks（同 LocalAdapter 取舍——引擎不持久化、重启清零，
审计持久化责任在上层事件流）。start() 校验通过后起后台线程异步执行、即刻返回
task_id，调用方按契约轮询 status()/result()。
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .base import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ExecutionEngine,
    StreamTaskError,
)
# [v3 心智反哺 2026-10-23] 心智块数据源/渲染（纯函数层，顶层导入保持可 mock）
from ..self_card import collect_db_state, render_mental_context

_logger = logging.getLogger("moray.zcode_adapter")

# 任务 2.c：沙箱白名单根（--cwd 唯一允许钉到的目录）
SANDBOX_ROOT = Path(r"D:\MoRay\workspace\zcode_sandbox")
# [2026-09-30 用户要求] ZCode 引擎绝对路径调用，不再依赖 PATH/系统命令。
# ZCODE_CMD 用绝对路径 node（裸命令 node 未必在 PATH，Popen 直接找可执行文件最稳）；
# ZCODE_SCRIPT 为源码构建产物实测路径（仓库根 dist/ 下没有产物，
# 实际在 apps/zcode-cli/packages/cli/dist/zcode.cjs，29.7MB 单文件 bundle）。
# 均支持环境变量覆盖（MORAY_ZCODE_CMD / MORAY_ZCODE_SCRIPT），换机/换产物路径无需改代码。
ZCODE_CMD = os.environ.get("MORAY_ZCODE_CMD", r"C:\Program Files\nodejs\node.exe")
ZCODE_SCRIPT = os.environ.get(
    "MORAY_ZCODE_SCRIPT",
    r"D:\ZCode_Test\zcode-repo\apps\zcode-cli\packages\cli\dist\zcode.cjs",
)
# [批次二·5 任务4] 项目私有 ZCode 数据根：注入 ZCODE_DATA_BASE_DIR 后，MoRay 拉起的
# ZCode 启动时自动读取 <该目录>\.zcode\v2\config.json（批次二任务D 的脱敏迁移产物，
# 仅含本机 Ollama provider），不再读取 C:\Users\<user>\.zcode 的全局配置/凭据
# （ZCode接入评估报告 §6 风险 8 的官方规避手段；源码 provider-runtime-env.ts:60）。
# 可用 MORAY_ZCODE_DATA_DIR 覆盖；调用方 env 已显式设置 ZCODE_DATA_BASE_DIR 时不覆盖。
ZCODE_DATA_DIR = os.environ.get("MORAY_ZCODE_DATA_DIR", r"D:\MoRay\work\zcode-home")
# 项目内脱敏个人 provider 配置（批次二任务D 迁移产物；CLI 真正消费的文件名，
# 源码 PERSONAL_PROVIDER_CONFIG_FILE_NAME="provider_config.json"）
ZCODE_PERSONAL_CONFIG = os.path.join(ZCODE_DATA_DIR, ".zcode", "v2", "provider_config.json")

# 任务 2.a：MVP 阶段唯一支持的动作
SUPPORTED_ACTIONS = frozenset({"run_zcode"})

# 任务 2.b/2.d：强制注入的 CLI 参数（不接受任务体覆盖）
FORCED_MODE = "edit"                     # 绝不允许 yolo（-p 缺省 mode 时 CLI 默认 yolo，必须显式传）
DISALLOWED_TOOLS = "Bash,WebFetch,Write"  # CLI 只有黑名单（--allowed-tools 不存在，Day9 实测）

DEFAULT_TIMEOUT = 600                    # 与 agent_runner 同口径
MIN_TIMEOUT = 10
MAX_TIMEOUT = 3600
LOG_KEEP = 4000                          # 内存保留日志行数

CREATION_FLAGS = 0
if sys.platform == "win32":
    # CREATE_NEW_PROCESS_GROUP（配合 taskkill /T 干净收树）| CREATE_NO_WINDOW（不弹黑框）
    CREATION_FLAGS = 0x00000200 | 0x08000000


# ---------------- [引擎解耦 v1 2026-10-21] 流式任务子系统（自 api.py Day24b 收编） ----------------
# Day24b 曾把「受理 + SSE 直通 + kill + files」四件套手写在 api.py（绕过 ExecutionEngine 契约、
# 自持 _ZCODE_STREAM_TASKS/_PROCS 两张进程表）。本节将其整体收编进本模块：状态仍为模块级
# 单例（与 SANDBOX_ROOT 等常量同一惯例，每个引擎模块自持自己的状态表），读流/收树为模块级
# 函数，对外以 ZCodeAdapter 的 start_stream / stream_events / kill_stream_task / read_task_file
# 四个方法暴露；api.py 只留通用 /api/engines/{engine_name}/* 派发路由（经 local_adapter.
# get_engine_by_name 找到引擎单例）。语义零变化（红线）：出参/事件形状/白名单/清理时机与
# api.py 原实现逐字一致；api.py 保留同名兼容别名（tests/organ_memory_selftest.py 直引）。
_ZCODE_EVENT_WHITELIST = frozenset({
    "turn.started", "turn.completed", "tool.updated", "checkpoint.created", "result",
    "model.streaming",   # [2026-10-01 U-5] 放行模型文字流（占真机流 88%），
                          # 让前端能显示"模型在说什么"的过程
})

# task_id -> {task_id, status, prompt, language, cwd, cmd, proc, queue, loop, created_at, done_at}
_ZCODE_STREAM_TASKS: dict = {}

# [停止修复 2026-10-03] 进程常登登记表：与主表共享同一 rec 引用，spawn 时写入、流终态/断连**不弹**
# （主表弹出语义保持原样：重连 = 404）。页面关闭后孤儿 CLI 仍在跑、主表已被流 finally 弹掉，
# 执行卡「快照态停止」靠本表找回 proc。生命周期：reader 补 done_at 后由受理端惰性清扫（1h）回收。
_ZCODE_STREAM_PROCS: dict = {}


def _zcode_stream_reader(task_id: str, proc, queue, loop) -> None:
    """后台线程：逐行读 CLI stdout → 白名单过滤 → 原始行入队；进程退出补 [DONE] 哨兵。

    - 行内容原样透传（只去行尾换行，绝不重序列化——任务书 gotcha 5）；
    - 单行 JSON 解析失败 / 非 dict / type 不在白名单 → 静默跳过，绝不抛出（gotcha 铁律）；
    - asyncio.Queue 非线程安全，投递走 loop.call_soon_threadsafe；
      事件循环已关闭（服务停机）时无处投递，丢弃即可。
    """
    def _put(item: str) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # noqa: BLE001 - loop 已关：丢弃即可
            pass

    # [器官→记忆 线B] 终态帧暂存（只 reader 线程可见的局部，不入 rec）：
    #   result.response = 正文；turn.completed.payload.duration = 全程毫秒
    #   （Day24a schema：result 帧自带 response 但无 duration，时长在 turn.completed）。
    last_result = None
    last_turn = None
    # [v5 step1 2026-10-08] 生命周期：progress 轮次计数（本 reader 私有）+ 流侧 caller 判定
    # （受理端起线程前已写常登表，此处必命中；send 自吞异常）。
    progress_seq = 0
    _rec_init = _ZCODE_STREAM_PROCS.get(task_id)
    is_laya_stream = bool(_rec_init) and str(_rec_init.get("caller") or "") == "laya"
    # [桥接可观测 2026-10-10] 静默丢弃留痕：逐行计数，收尾进 meta
    _dropped_types: dict = {}
    _parse_errors = 0
    _empty_lines = 0
    # [桥接可观测 2026-10-10] 读流循环整体死亡的异常留痕："绝不抛出"铁律保持，只是不再吞掉指纹
    _loop_exit_reason: str = ""
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                _empty_lines += 1
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                _parse_errors += 1
                continue
            if not isinstance(obj, dict) or obj.get("type") not in _ZCODE_EVENT_WHITELIST:
                _t = obj.get("type") if isinstance(obj, dict) else type(obj).__name__
                _dropped_types[_t] = _dropped_types.get(_t, 0) + 1
                continue
            if obj.get("type") == "result":
                last_result = obj
            elif obj.get("type") == "turn.completed":
                last_turn = obj
            _put(line)
            # [v5 step1] turn.completed → progress（每轮一条上总线）。tool.updated/
            # model.streaming 禁上总线（真机流 88% 会刷屏台账）。异常绝不影响读流。
            if obj.get("type") == "turn.completed":
                try:
                    progress_seq += 1
                    from .. import synapse_bus
                    _p_dur = (obj.get("payload") or {}).get("duration")
                    synapse_bus.send(
                        "laya.task.progress" if is_laya_stream else "zcode.task.progress",
                        "ZCode：第 %d 轮完成·%s" % (
                            progress_seq,
                            ("%sms" % _p_dur) if _p_dur is not None else "?"),
                        thread_id=None,
                        meta={"trace_id": "%s:p%d" % (task_id, progress_seq),
                              "task_id": task_id, "round": progress_seq,
                              "duration_ms": _p_dur,
                              "caller": "laya" if is_laya_stream else "zcode"},
                        from_member="organ.laya" if is_laya_stream else "organ.zcode")
                except Exception:  # noqa: BLE001 - progress 上总线绝不影响读流
                    pass
    except Exception as e:  # noqa: BLE001 - 读流异常不影响收尾哨兵
        _loop_exit_reason = "%s: %s" % (type(e).__name__, str(e)[:200])
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        rec = _ZCODE_STREAM_TASKS.get(task_id) or _ZCODE_STREAM_PROCS.get(task_id)
        if rec is not None:
            rec["done_at"] = time.time()
        _put("[DONE]")
        # ---- [器官→记忆 线B] ZCode 流式任务成功 → 旁路写一条 task_result 记忆 ----
        # 判据（对齐 Laya "成功才钩"）：returncode==0 且截到 result 终态帧；
        # caller=="laya" 跳过（线C 双写防护：Laya executor 已写整链 laya_task_result）。
        # rec 已被流 finally 弹掉时（孤儿态）仍尽力用常登表 rec，取不到 prompt 也留痕。
        # 任何异常绝不影响哨兵/收尾。
        try:
            rc = proc.returncode
            is_laya = bool(rec) and str(rec.get("caller") or "") == "laya"
            if rc == 0 and last_result is not None:
                prompt = str((rec or {}).get("prompt") or "")[:200]
                language = str((rec or {}).get("language") or "")
                resp = str(last_result.get("response") or "")[:500]
                dur = ((last_turn or {}).get("payload") or {}).get("duration") \
                    if last_turn else None
                content = "ZCode：%s\n耗时：%s·退出码：%s\n结果：%s" % (
                    prompt, ("%sms" % dur) if dur is not None else "?", rc, resp)
                if not is_laya:
                    from .. import organ_memory
                    tags = ["zcode_stream"] + ([language] if language else [])
                    organ_memory.write_organ_memory(
                        content, kind="task_result", tags=tags,
                        source="zcode_stream", entity_id=task_id)
                # [Synapse v4 第三步] 总线放行 Laya 流（记忆仍跳＝线C 双写防护）：
                # Laya 流发 laya.task.succeeded（无规则，不触发 reflection）；
                # 前端执行卡直连仍发 zcode.task.succeeded（reflection 规则，行为不变）。
                # 总闸 MORAY_SYNAPSE 默认关 = send 内部首行 no-op；trace_id=task_id
                # 防同一任务重发（幂等判重键）；send 自吞异常，此处无需额外防护。
                from .. import synapse_bus
                synapse_bus.send(
                    "laya.task.succeeded" if is_laya else "zcode.task.succeeded",
                    content, thread_id=None,
                    meta={"trace_id": task_id, "task_id": task_id,
                          "duration_ms": dur, "exit_code": rc,
                          "caller": "laya" if is_laya else "zcode",
                          "dropped_types": _dropped_types,
                          "parse_errors": _parse_errors,
                          "empty_lines": _empty_lines,
                          "loop_exit_reason": _loop_exit_reason,
                          "stream_vanished": (
                              last_result is None
                              and not _dropped_types
                              and _parse_errors == 0
                              and _empty_lines == 0
                              and rc == 0
                          )},
                    from_member="organ.laya" if is_laya else "organ.zcode")
            else:
                # [v5 step1] rc!=0 或未截到 result 帧 → failed 上总线。记忆钩子维持
                # "成功才钩"（本分支不写 organ_memory）；error_text 不提供：stderr 是
                # DEVNULL（Popen 处既有裁定），不硬造；duration 用 done_at-created_at 兜底
                # （失败时 last_turn 不可靠；done_at 由本 finally 上方刚补）。
                prompt = str((rec or {}).get("prompt") or "")[:200]
                _f_dur = None
                if rec:
                    _da, _ca = rec.get("done_at"), rec.get("created_at")
                    if _da and _ca:
                        _f_dur = int((_da - _ca) * 1000)
                from .. import synapse_bus
                synapse_bus.send(
                    "laya.task.failed" if is_laya else "zcode.task.failed",
                    "ZCode：%s\n失败：退出码 %s·耗时 %s" % (
                        prompt, rc, ("%sms" % _f_dur) if _f_dur is not None else "?"),
                    mentions=['user'], thread_id=None,  # [v5 step2] 失败叫醒用户（@user 发送侧）
                    meta={"trace_id": task_id + ":failed", "task_id": task_id,
                          "exit_code": rc, "duration_ms": _f_dur,
                          "caller": "laya" if is_laya else "zcode",
                          "got_result": last_result is not None,
                          "dropped_types": _dropped_types,
                          "parse_errors": _parse_errors,
                          "empty_lines": _empty_lines,
                          "loop_exit_reason": _loop_exit_reason,
                          "stream_vanished": (
                              last_result is None
                              and not _dropped_types
                              and _parse_errors == 0
                              and _empty_lines == 0
                              and rc == 0
                          )},
                    from_member="organ.laya" if is_laya else "organ.zcode")
                # [读流告警 2026-10-10] 桥接层静默失败——走 ERROR 通道（桥一 v1.6 FileHandler + ErrorRingBuffer 天然收）
                _sv = (last_result is None and not _dropped_types
                       and _parse_errors == 0 and _empty_lines == 0 and rc == 0)
                if _sv or _loop_exit_reason:
                    import logging as _logging
                    _logging.getLogger("moray.zcode").error(
                        "[zcode] 读流异常：task=%s exit_code=%s got_result=%s "
                        "loop_exit_reason=%r dropped_types=%r parse_errors=%d empty_lines=%d stream_vanished=%s",
                        task_id, rc, last_result is not None,
                        _loop_exit_reason, _dropped_types, _parse_errors, _empty_lines, _sv,
                    )
        except Exception as e:  # noqa: BLE001 - 旁路写入失败绝不影响流收尾
            import logging
            logging.getLogger("moray.zcode").warning(
                "[zcode-memory] 写入钩子异常：task=%s err=%s: %s",
                task_id, type(e).__name__, str(e)[:200])


def _zcode_kill_proc_tree(pid: int) -> tuple:
    """[停止修复 2026-10-03] 收进程树（taskkill /T /F），与 _kill_tree 的 win32 分支同语义
    （spawn 已带 CREATE_NEW_PROCESS_GROUP，/T 收全树；CREATE_NO_WINDOW=0x08000000 不弹框）；
    非 win killpg。阻塞函数——调用方必须 asyncio.to_thread。异常一律折成 (False, detail)
    交调用方落 reason，不吞不抛。"""
    import subprocess
    import sys as _sys
    try:
        if _sys.platform == "win32":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20,
                               creationflags=0x08000000, check=False)
            return (r.returncode == 0, "" if r.returncode == 0 else "taskkill_rc_%s" % r.returncode)
        import os
        os.killpg(os.getpgid(pid), 9)
        return (True, "")
    except Exception as e:  # noqa: BLE001 - kill 失败以结果形态上报，由端点兜底
        return (False, str(e))


# ---------------------------------------------------------------- 工具函数（agent_runner 同策略）

def _decode_line(data: bytes) -> str:
    """单行字节解码：UTF-8 优先，失败回退 gbk + replace（agent_runner._decode_line 同策略）"""
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk", errors="replace")
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace")


def _clamp_timeout(v) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, n))


def _safe_name(s: str) -> str:
    """日志文件名安全化（agent_runner._safe_name 同策略）"""
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(s or "task"))[:80] or "task"


class ZCodeAdapter(ExecutionEngine):
    """ZCode CLI 执行引擎（Day10 骨架：subprocess 直调，Docker 隔离未做前诚实申报）。"""

    NAME = "zcode"
    DESCRIPTION = ("ZCode 编码 Agent（Day15 真身：Docker 隔离层经 Day11b-b 验收 9/9，is_isolated=True；"
                   "强制 --mode edit + 工具黑名单 + zcode_sandbox 白名单目录；已接审计/状态机/GC）")

    def __init__(self, root: Path | str | None = None, cli_cmd: list | None = None):
        # root/cli_cmd 可注入仅为测试隔离；生产默认：沙箱根 + 绝对路径 node + zcode.cjs
        # （MORAY_ZCODE_CLI 若设置则作为完整命令串优先使用，兼容旧部署）
        # 构造函数无任何副作用（沙箱目录懒创建，/api/engines/list 的模块级实例化才安全）
        self.root = Path(root).resolve() if root else SANDBOX_ROOT
        if cli_cmd:
            self.cli_cmd = [str(c) for c in cli_cmd]
        else:
            env_cmd = os.environ.get("MORAY_ZCODE_CLI", "").strip()
            self.cli_cmd = [str(c) for c in env_cmd.split()] if env_cmd else [ZCODE_CMD, ZCODE_SCRIPT]
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ ExecutionEngine 契约

    def capabilities(self) -> dict:
        """任务 2.4：诚实申报。

        can_read=False  引擎不接受任何读任务（动作词表只有 run_zcode）；
        can_write=True  ZCode 可在沙箱白名单目录内改文件（edit 模式）；
        can_exec=False  引擎不接受执行类任务；CLI 自身的 Bash 工具也被黑名单禁掉；
        can_network=False 无受控网络出口（网关代理配置属后置形态）；
        is_isolated=True  [Day15 升格] 依据：Day11b-b 已用 bat 真机验证 Docker 隔离层
            可用（验收 9/9 全绿，docs/day11b_b_report.md），引擎在隔离编排下运行；
            Day10 骨架期的 False 是"Docker 未接"的诚实降级，现已补齐故升格。
        """
        return {
            "can_read": False,
            "can_write": True,
            "can_exec": False,
            "can_network": False,
            "is_isolated": True,
        }

    # ------------------------------------------------------------ [Day23.6] 静态可用性探针

    def unavailable_reason(self) -> str:
        """静态判断本引擎当前是否可用。返回 "" = 可用；非空字符串 = 不可用原因。

        **纯路径检查**：不起子进程、不探端口、不调外部命令（STOP GATE 约束）。

        只回答一个问题：**配置的 CLI 入口能不能被解析到**。这是本机最典型的假阳性——
        默认配置 `zcode.cmd` 既不在 PATH、也没有 MORAY_ZCODE_CLI，任务在宿主上根本起不来
        （Popen 会 OSError），但能力申报里 is_isolated=True 会让面板凭空白亮绿灯。
        它**不**判断模型凭据 / provider 配置 / Docker 守护是否就绪——那需要另起探针
        （方案见 docs/day23.5_engines_backend.md §5，本次未做）。

        任务书的探针顺序（① MORAY_ZCODE_CLI 已设 → 验该路径 ② 否则 which(cli_cmd)）
        在 __init__ 里已完成第一步的取值：env 已设时 `self.cli_cmd[0]` 就是该值。
        故此处只需按"路径形态 vs 裸命令名"分流，且**与真正 spawn 时用的值保持一致**
        （运行时改 env 需重启后端才生效——与 spawn 行为同口径，不自相矛盾）。

        "可执行"在本平台的口径：路径形态 → 存在且是普通文件（`os.access(X_OK)` 在 Windows
        上不具判别力）；裸命令名 → 交给 `shutil.which`（纯文件系统扫描，按 PATHEXT 解析）。
        """
        try:
            cmd = self.cli_cmd[0] if self.cli_cmd else ""
            if not cmd:
                return "cli_cmd_unset"
            looks_like_path = os.path.isabs(cmd) or ("/" in cmd) or ("\\" in cmd)
            if looks_like_path:
                try:
                    if not Path(cmd).is_file():
                        return "cli_not_found"
                except OSError:
                    return "cli_not_found"
                # node + zcode.cjs 形态：脚本产物缺失同样按不可用处理
                if len(self.cli_cmd) > 1:
                    try:
                        if not Path(self.cli_cmd[-1]).is_file():
                            return "cli_script_not_found"
                    except OSError:
                        return "cli_script_not_found"
                return ""
            return "" if shutil.which(cmd) else "cli_not_found"
        except Exception:  # noqa: BLE001 - 探针任何异常一律按"不可用"处理，绝不抛给调用方
            return "probe_failed"

    def start(self, task: dict) -> str:
        """受理任务：校验（动作/提示词/cwd 白名单）→ 起后台线程 → 即刻返回 task_id。

        任何拒绝都以 failed+error 落记录，绝不静默（base.py 明文要求）。
        """
        if not isinstance(task, dict):
            raise ValueError("task must be a dict")
        task_id = uuid.uuid4().hex[:12]
        rec = {
            "task_id": task_id,
            "status": STATUS_PENDING,
            "task": dict(task),
            "created_at": time.time(),
            "started_at": None,
            "ended_at": None,
            "duration_sec": None,
            "exit_code": None,
            "result": None,
            "error": None,
            "cwd_path": None,
            "session_id": None,      # [Day15] 会话归属（受理通过后由 _resolve_sid 填，事件流/状态机用）
            "timeout_sec": _clamp_timeout(task.get("timeout_sec")),
            "lines": [],
            "dropped": 0,
            "_lock": threading.Lock(),
            "_fh": None,
            "_proc": None,
            "_killed": False,
            "_timed_out": False,
            "_usage": None,          # 诚实原则：CLI 真返回了才记，拿不到就是 None
            "_zcode_session": None,  # 只读回、不回传（设计文档 c.5）
        }
        with self._lock:
            self._tasks[task_id] = rec

        err = self._validate(rec, task)
        if err:
            rec["status"] = STATUS_FAILED
            rec["error"] = err
            return task_id
        try:
            rec["cwd_path"].mkdir(parents=True, exist_ok=True)
        except OSError as e:
            rec["status"] = STATUS_FAILED
            rec["error"] = "sandbox_mkdir_failed: %s" % e
            return task_id
        # [Day15] 审计 + 状态机接入（仅对受理通过、真正会 launch 的任务；
        # 校验/建目录被拒停在 rec 级 failed，不污染 agent_sessions）。
        # 顺序对齐 agent_runner._audit_run：先写事件，再流转状态，
        # last_event_id 才指得到本次 zcode_task_started 事件。
        rec["session_id"] = self._resolve_sid(task)
        self._write_event(rec, "zcode_task_started", "ok",
                          output={"task_id": rec["task_id"],
                                  "input": task.get("prompt"),
                                  "session_id": rec["session_id"]})
        self._transition(rec, "pending", error_msg="")
        self._launch(rec)
        return task_id

    def status(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {"task_id": task_id, "status": rec["status"], "error": rec["error"]}

    def cancel(self, task_id: str) -> bool:
        """pending/running → 收敛 failed（error="canceled"）+ 收树，返回 True；
        已终态或未知任务 → False（契约原文语义）。"""
        with self._lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            return False
        if rec["status"] not in (STATUS_PENDING, STATUS_RUNNING):
            return False
        rec["status"] = STATUS_FAILED
        rec["error"] = "canceled"
        self._kill_tree(rec, "用户取消")
        return True

    def result(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {
            "task_id": task_id,
            "status": rec["status"],
            "result": rec["result"],
            "error": rec["error"],
            "created_at": rec["created_at"],
        }

    def cleanup_stale_tasks(self, older_than_minutes: int = 60) -> int:
        """[Day15] GC：清理已终态（completed/failed）且 ended_at 早于阈值的内存任务记录，
        返回被清理的条数。审计事件已持久化在 event_snapshots，不受本方法影响（"保留审计"）。

        - 不做定时任务（避免引入新依赖）：由上层在需要时手动调用；
        - pending/running 一律不清（任务还活着，清了会让 status()/result() 误抛 KeyError）。
        """
        cutoff = time.time() - max(0, int(older_than_minutes)) * 60
        removed = 0
        with self._lock:
            for tid in list(self._tasks.keys()):
                rec = self._tasks[tid]
                if rec["status"] in (STATUS_COMPLETED, STATUS_FAILED):
                    ended = rec.get("ended_at") or rec.get("created_at") or 0
                    if ended and ended < cutoff:
                        del self._tasks[tid]
                        removed += 1
        return removed

    # ------------------------------------------------------------ 校验与命令构造

    def _get(self, task_id: str) -> dict:
        with self._lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            raise KeyError(f"unknown task_id: {task_id}")
        return rec

    # ------------------------------------------------------------ [Day15] 审计 / 状态机接入
    # 设计铁律（与 agent_runner / agent_tools 同口径）：以下三个助手任何失败都只 warning，
    # 绝不向上抛、绝不阻断任务主流程——审计与状态机是"记录者"，不是"拦截器"。

    def _resolve_sid(self, task: dict) -> str:
        """会话归属复用 Day2 解析链（显式 > task.session_id > "default"），与事件流/
        agent_runner 同一口径，不另写一份（两处各写迟早漂移）。解析失败兜底 "default"。"""
        try:
            from .. import agent_tools
            explicit = task.get("session_id") if isinstance(task, dict) else None
            return agent_tools._resolve_session_id(explicit, task)
        except Exception:  # noqa: BLE001 - 解析链不可用也不该挡住任务
            return "default"

    def _write_event(self, rec: dict, event_type: str, status: str,
                     duration_ms: int = 0, output=None, error_msg: str = "") -> None:
        """直接写一条 event_snapshots（镜像 Day2 全列，含 session_id，让
        latest_event_id_for_session 能闭环 last_event_id）。

        - 不用 crud.append_event_snapshot：它只填 4 列、不写 session_id（Day2 备注点明的坑），
          会导致状态机 last_event_id 查不到本会话事件；
        - source_log_id 恒 None：zcode 事件非 tool_call，不关联 agent_tool_log 行，
          否则前端时间线会对它长出坏重放按钮（Day2 铁律：operation 必须不关联）；
        - 写失败只 warning。
        """
        try:
            import json as _json
            from .. import db
            sid = rec.get("session_id") or "default"
            args = {
                "task_id": rec["task_id"],
                "action": (rec.get("task") or {}).get("action"),
                "cwd": str(rec.get("cwd_path") or ""),
                "session_id": sid,
            }
            snapshot = {
                "tool": event_type,
                "args": args,
                "approved": True,
                "status": status,
                "ms": int(duration_ms or 0),
                "detail": "",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                "workspace": str(rec.get("cwd_path") or ""),
                "schema_version": 2,
                "session_id": sid,
                "tool_name": event_type,
                "duration_ms": int(duration_ms or 0),
                "input": args,
                "output": output,
                "error_msg": str(error_msg or "")[:1000],
                "source": "zcode",
            }
            input_json = _json.dumps(args, ensure_ascii=False, default=str)[:50_000]
            output_json = (_json.dumps(output, ensure_ascii=False, default=str)[:50_000]
                           if output is not None else "")
            conn = db.connect()
            try:
                conn.execute(
                    "INSERT INTO event_snapshots (event_type, entity_id, source_log_id, snapshot_json,"
                    " session_id, tool_name, status, duration_ms, error_msg, input_json, output_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_type, "zcode:%s" % rec["task_id"], None,
                        _json.dumps(snapshot, ensure_ascii=False),
                        sid, event_type, status, int(duration_ms or 0),
                        str(error_msg or "")[:1000], input_json, output_json,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 - 事件写入失败绝不影响任务执行
            _logger.warning("zcode event snapshot write failed: %s", e)

    def _transition(self, rec: dict, new_status: str, error_msg=None) -> None:
        """推进 agent_sessions（agent_runner._session_transition 同构：失败只 warning，绝不挡）。
        last_event_id 指向该会话最新事件（在 _write_event 之后调用即可覆盖本次）。
        error_msg=None 保留原值；""/文本覆盖（running/completed 用 "" 清空）。"""
        try:
            from .. import crud
            sid = rec.get("session_id") or "default"
            crud.transition_agent_session(
                sid, new_status, error_msg=error_msg,
                last_event_id=crud.latest_event_id_for_session(sid))
        except Exception as e:  # noqa: BLE001 - 状态机是记录者，永远不许挡住执行
            _logger.warning("zcode session state update failed: %s", e)

    def _validate(self, rec: dict, task: dict):
        """受理校验（任务 2.2 a/c）。通过返回 None 并填好 rec["cwd_path"]；否则返回 error 字符串。"""
        # a) 动作白名单：MVP 只支持 run_zcode
        action = task.get("action")
        if action not in SUPPORTED_ACTIONS:
            return ("action_not_supported: %r（ZCodeAdapter 骨架仅支持 run_zcode）" % (action,))
        # 提示词必填（落盘沙箱 inbox/ 后由 argv 的 Read 指令交付 CLI，空任务拒绝受理）
        prompt = task.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return "missing prompt（任务指令为空，拒绝受理）"
        # c) --cwd 钉死沙箱白名单：相对路径以沙箱根解析；绝对路径与 ".." 穿越
        #    一律先 resolve() 再验祖先，越界直接拒绝（LocalAdapter._in_whitelist 同思路；
        #    差异：这里沙箱根本身允许作为 cwd——"锁定到沙箱根"正是默认值）
        raw_cwd = str(task.get("cwd") or "").strip()
        candidate = Path(raw_cwd) if raw_cwd else self.root
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return "path_outside_whitelist: %s（路径无法解析）" % raw_cwd
        if not resolved.is_relative_to(self.root):
            return "path_outside_whitelist: %s（--cwd 只允许钉在 %s 之内）" % (raw_cwd, self.root)
        rec["cwd_path"] = resolved
        # b) --mode edit 强制注入、d) --disallowed-tools 强制注入：
        #    均在 _build_command 里硬编码，任务体传什么 mode/tools 一律忽略（不允许 yolo）。
        return None

    def _build_command(self, rec: dict) -> list:
        """构造 zcode CLI 命令行（Day9 实测 flag，全部强制项在此收口）。

        [2026-10-05 stdin 修复] 提示词走沙箱内文件：`-p` 是 `--prompt <text>` 带值参数
        （CLI bundle parseArgs 实证，无 "`-` 读 stdin" 约定），argv 传短 Read 指令指向
        落盘文件（Read 工具不在黑名单）；--json 拿单块结构化输出（sessionId/usage），
        无 JSONL 事件流。
        """
        prompt_rel = rec.get("prompt_file_rel")
        if not prompt_rel:
            # 调用方（_worker）保证先落盘；防御到底：绝不无提示词起进程（=空转复现）
            raise ValueError("prompt file missing（提示词文件未落盘，拒绝起进程）")
        return [*self.cli_cmd,
                "-p", "Read %s and do exactly what it says." % prompt_rel,
                "--mode", FORCED_MODE,
                "--cwd", str(rec["cwd_path"]),
                "--disallowed-tools", DISALLOWED_TOOLS,
                "--json"]

    def _write_prompt_file(self, rec: dict) -> str | None:
        """[2026-10-05 stdin 修复·A] 完整提示词落盘到沙箱 cwd 内，返回进 argv 的相对路径。

        文件名带 task_id 防覆盖（同目录多次跑互不踩）；相对路径用 POSIX 斜杠直接进
        argv（--cwd 已钉到沙箱目录，CLI 在该目录内解析）。写失败返回 None，由调用方
        按 failed 收敛——绝不退回"无提示词起进程"。
        [v3 心智反哺 2026-10-23] 落盘前先拼心智块（_mental_prompt_block）：
        有心智数据 → "[背景状态]…\\n\\n[任务指令]\\n<原 prompt>"；无数据/异常 → 原文
        逐字落盘（fail-open，不给调用方和逐字断言添变数）。
        """
        rel = "inbox/prompt_%s.md" % rec["task_id"]
        body = str(rec["task"].get("prompt") or "")
        block = self._mental_prompt_block()
        text = (block + "\n\n[任务指令]\n" + body) if block else body
        try:
            inbox = rec["cwd_path"] / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / ("prompt_%s.md" % rec["task_id"])).write_text(text, encoding="utf-8")
        except OSError as e:
            self._append(rec, "[MoRay] 提示词落盘失败：%s" % e)
            return None
        rec["prompt_file_rel"] = rel
        return rel

    def _mental_prompt_block(self) -> str:
        """[v3 心智反哺 2026-10-23] 渲染心智块；无数据/任何异常 → ""（零注入，fail-open）。

        总闸 MORAY_MENTAL_CONTEXT（默认开；0/false/no/off=跳过，同 MORAY_SELF_CARD
        关断词表）。数据/渲染两段各兜一层：collect_db_state 读库失败、render 抛错都
        不许把提示词落盘带崩——落盘失败=任务直接 failed，代价远大于丢一次心智注入。
        """
        if os.environ.get("MORAY_MENTAL_CONTEXT", "1").strip().lower() in ("0", "false", "no", "off"):
            return ""
        try:
            state = collect_db_state()
        except Exception:  # noqa: BLE001 - 心智采集失败降级为零注入
            return ""
        try:
            return render_mental_context(state) or ""
        except Exception:  # noqa: BLE001 - 渲染失败同上
            return ""

    def _build_env(self) -> dict:
        """认证/模型无 CLI flag（Day9 实测）：走 provider 配置文件 + 环境变量注入。
        [批次二·5 任务4] 数据目录 + 个人配置**硬覆盖**（不能用 setdefault！）：
        MoRay 后端常从桌面 ZCode 会话派生进程，继承的 env 里带有
        ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=C:\\Users\\<user>\\.zcode\\...（桌面真实配置，
        含云端 Key）——E2E 实证它会让 CLI 无视项目隔离直接调用云端。这里强制指到
        项目内脱敏配置（仅本机 Ollama provider），并清掉桌面的 builtin 缓存路径，
        让 CLI 从重定向后的数据根自行物化。"""
        env = dict(os.environ)
        cfg = env.get("MORAY_ZCODE_PROVIDER_CONFIG", "").strip()
        if cfg:
            env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = cfg
        else:
            env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = ZCODE_PERSONAL_CONFIG
        env["ZCODE_DATA_BASE_DIR"] = ZCODE_DATA_DIR      # 硬覆盖：项目隔离
        env["ZCODE_STORAGE_DIR"] = ZCODE_DATA_DIR        # 硬覆盖：会话/日志/记忆同根
        env.pop("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", None)  # 桌面缓存路径清出，随数据根重解析
        env.setdefault("ZCODE_MODEL_TELEMETRY_ENABLED", "0")  # Day9 隔离 env 惯例：关遥测
        return env

    # ------------------------------------------------------------ 日志（agent_runner 同构）

    def _append(self, rec: dict, line: str):
        line = str(line).rstrip("\r\n")
        if line == "":
            return
        with rec["_lock"]:
            rec["lines"].append(line)
            if len(rec["lines"]) > LOG_KEEP:
                drop = len(rec["lines"]) - LOG_KEEP
                del rec["lines"][:drop]
                rec["dropped"] += drop
            fh = rec["_fh"]
        if fh is not None:
            try:
                fh.write(line + "\n")
            except OSError:
                pass

    def _open_log(self, rec: dict):
        rec["log_path"] = str(Path(tempfile.gettempdir()) / ("moray_zcode_%s.log" % _safe_name(rec["task_id"])))
        try:
            rec["_fh"] = open(rec["log_path"], "w", encoding="utf-8", buffering=1)
        except OSError as e:  # noqa: BLE001 - 日志落盘失败不影响运行
            _logger.warning("zcode task log open failed: %s", e)
            rec["_fh"] = None

    def _close_log(self, rec: dict):
        with rec["_lock"]:
            fh, rec["_fh"] = rec["_fh"], None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    # ------------------------------------------------------------ 进程管理（agent_runner 同构）

    def _kill_tree(self, rec: dict, why: str) -> bool:
        """taskkill /T /F 收树（Windows）/ killpg（其他平台），返回是否真的发出过终止动作。"""
        with rec["_lock"]:
            if rec["_killed"] or rec["_proc"] is None:
                return False
            rec["_killed"] = True
            proc = rec["_proc"]
        pid = proc.pid
        ok = False
        try:
            if sys.platform == "win32":
                r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=20,
                                   creationflags=0x08000000, check=False)
                ok = r.returncode == 0
            else:
                os.killpg(os.getpgid(pid), 9)
                ok = True
        except Exception as e:  # noqa: BLE001 - 兜底：至少杀掉直接子进程
            self._append(rec, "[MoRay] 进程树终止异常（转 kill 直系子进程）：%s" % e)
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - 进程可能已退出
            pass
        self._append(rec, "[MoRay] %s，已终止进程树（pid %s）" % (why, pid))
        return ok

    def _read_stream(self, rec: dict, stream, is_stdout: bool):
        """逐行读子进程输出：stdout 尝试提取 JSON 字段（sessionId/usage），stderr 加前缀原样入日志。"""
        try:
            while True:
                raw = stream.readline()
                if not raw:
                    break
                line = _decode_line(raw).rstrip("\r\n")
                if line == "":
                    continue
                if is_stdout:
                    self._maybe_json(rec, line)
                    self._append(rec, line[:500])
                else:
                    self._append(rec, "[stderr] " + line[:500])
        except Exception as e:  # noqa: BLE001 - 读取异常不影响收尾
            self._append(rec, "[MoRay] 读取输出异常：%s" % e)
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    def _maybe_json(self, rec: dict, line: str):
        """--json 输出是单块结构化 JSON（Day9 实测）：真拿到 usage/sessionId 才记录，绝不编造。"""
        s = line.strip()
        if not s.startswith("{"):
            return
        try:
            ev = json.loads(s)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        if isinstance(ev.get("usage"), dict):
            rec["_usage"] = ev["usage"]
        if ev.get("sessionId"):
            rec["_zcode_session"] = str(ev["sessionId"])

    def _launch(self, rec: dict):
        """起后台工作线程（测试里 patch 成 no-op 后可同步调 _worker，Day5 假 codex 测试经验）。"""
        threading.Thread(target=self._worker, args=(rec,),
                         name="moray-zcode-%s" % rec["task_id"], daemon=True).start()

    def _worker(self, rec: dict):
        """后台线程：拉起 zcode CLI、提示词落盘进沙箱、双线程收日志、watchdog 守超时。"""
        rec["status"] = STATUS_RUNNING
        rec["started_at"] = time.time()
        self._transition(rec, "running", error_msg="")  # [Day15] 状态机入口钩子（agent_runner 同位置）
        self._open_log(rec)
        # [2026-10-05 stdin 修复] 提示词先落盘再构造命令行；写失败按 failed 收敛（fail-closed）
        if self._write_prompt_file(rec) is None:
            self._finalize(rec, None, startup_error="prompt_file_write_failed")
            return
        cmd = self._build_command(rec)
        self._append(rec, "—— 任务 %s 开始（ZCodeAdapter 骨架，subprocess 直调）" % rec["task_id"])
        self._append(rec, "—— 命令：%s（提示词落盘 %s）"
                     % (" ".join(cmd), rec.get("prompt_file_rel")))
        self._append(rec, "—— 超时：%ss" % rec["timeout_sec"])
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(rec["cwd_path"]),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=CREATION_FLAGS,
                env=self._build_env(),
            )
        except OSError as e:
            self._append(rec, "[启动失败] %s" % e)
            self._finalize(rec, None, startup_error="startup_failed: %s" % e)
            return
        except Exception as e:  # noqa: BLE001
            self._finalize(rec, None, startup_error="startup_failed: %s" % e)
            return
        with rec["_lock"]:
            rec["_proc"] = proc
        self._append(rec, "—— 子进程 pid %s" % proc.pid)
        # [2026-10-05 stdin 修复] 提示词已经沙箱内文件交付，stdin 无用即关（不留悬挂写端；
        # CLI 的 -p 为带值参数，headless 链路从不读 stdin——bundle 实证）
        try:
            proc.stdin.close()
        except (OSError, AttributeError) as e:
            self._append(rec, "[MoRay] stdin 关闭失败：%s" % e)

        t_out = threading.Thread(target=self._read_stream, args=(rec, proc.stdout, True), daemon=True)
        t_err = threading.Thread(target=self._read_stream, args=(rec, proc.stderr, False), daemon=True)
        t_out.start()
        t_err.start()

        deadline = rec["started_at"] + rec["timeout_sec"]
        rc = None
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if rec["status"] == STATUS_FAILED:
                # cancel() 已置终态并收树，这里只等进程退出
                try:
                    rc = proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    rc = proc.poll()
                break
            if time.time() > deadline:
                rec["_timed_out"] = True
                self._append(rec, "[MoRay] 超过 %ss 未结束，判定超时，强制终止进程树" % rec["timeout_sec"])
                self._kill_tree(rec, "超时强制结束")
                try:
                    rc = proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    rc = proc.poll()
                break
            time.sleep(0.2)

        t_out.join(timeout=10)
        t_err.join(timeout=10)
        self._finalize(rec, rc)

    def _prompt_idle_reason(self, rec: dict) -> str | None:
        """[2026-10-05 stdin 修复·B] 检测 CLI "exit 0 但没干活" 的空转信号。

        只认两类硬信号（均来自 Iris 首跑实证 moray_zcode_d9c7998475b9.log）：
        - 样板空转回应：模型只收到字面 "-" 时，--json response 里的固定句式
          "came through as just"（CLI/模型侧文案，宿主不产生）；
        - 零输出：--json 模式成功必回单块 JSON，stdout 一行 CLI 输出都没有即异常。
        返回 None = 未命中；命中返回 error 文案。MoRay 自记行（——/[MoRay]/[stderr]/[启动失败]）
        不参与判定，避免宿主侧元信息被当 CLI 输出。
        """
        cli_lines = [ln for ln in rec["lines"]
                     if not ln.startswith(("[stderr]", "[MoRay]", "[启动失败]", "—— "))]
        if any("came through as just" in ln for ln in cli_lines):
            return ("prompt_not_delivered: CLI echoed no-task boilerplate"
                    " (prompt never reached the model)")
        if not cli_lines:
            return "prompt_not_delivered: empty CLI output (no --json payload on stdout)"
        return None

    def _finalize(self, rec: dict, rc, startup_error: str | None = None):
        """收尾：四态收敛（timeout/canceled→failed，error 区分原因）+ 组装 result + 关日志。"""
        if rec["status"] in (STATUS_PENDING, STATUS_RUNNING):
            if startup_error:
                rec["status"] = STATUS_FAILED
                rec["error"] = startup_error
            elif rec["_timed_out"]:
                rec["status"] = STATUS_FAILED
                rec["error"] = rec["error"] or "timeout"
            elif rc == 0:
                # [2026-10-05 stdin 修复·B] exit 0 ≠ 任务真干了：Iris 首跑 CLI 收到字面 "-"
                # 照样 exit 0（模型烧 38k tokens 自说自话）。命中空转信号 → 强制 failed，
                # 上层只拿到 ZCODE_FAILED，绝不 completed（记忆库不落 completed 记录）。
                idle_reason = self._prompt_idle_reason(rec)
                if idle_reason:
                    rec["status"] = STATUS_FAILED
                    rec["error"] = idle_reason
                else:
                    rec["status"] = STATUS_COMPLETED
            else:
                rec["status"] = STATUS_FAILED
                rec["error"] = rec["error"] or "exit_code_%s" % rc
        rec["exit_code"] = rc
        rec["ended_at"] = time.time()
        rec["duration_sec"] = round(rec["ended_at"] - (rec["started_at"] or rec["created_at"]), 1)
        if rec["status"] == STATUS_COMPLETED:
            # 失败时 result=None（契约要求）；usage 拿不到就是 None，绝不编造
            rec["result"] = {
                "exit_code": rc,
                "duration_sec": rec["duration_sec"],
                "cwd": str(rec["cwd_path"]),
                "log_path": rec.get("log_path"),
                "total_lines": rec["dropped"] + len(rec["lines"]),
                "usage": rec["_usage"],
                "metadata": {"zcode_session": rec["_zcode_session"]},
            }
        self._append(rec, "—— 运行结束：%s（退出码 %s，耗时 %ss）"
                     % (rec["status"], rc, rec["duration_sec"]))
        self._close_log(rec)
        # [Day15] 收尾审计 + 状态机终态（先写事件再流转，last_event_id 指向本 finished 事件）。
        # 引擎四态已在此前收敛为 completed/failed（timeout/canceled→failed），故会话终态二选一。
        duration_ms = int((rec["duration_sec"] or 0) * 1000)
        is_ok = rec["status"] == STATUS_COMPLETED
        audit_status = "ok" if is_ok else ("timeout" if rec["error"] == "timeout" else "error")
        self._write_event(
            rec, "zcode_task_finished", audit_status, duration_ms=duration_ms,
            output={"status": rec["status"], "duration_ms": duration_ms,
                    "output": rec["result"], "exit_code": rec["exit_code"]},
            error_msg="" if is_ok else str(rec["error"] or ""))
        self._transition(rec, "completed" if is_ok else "failed",
                         error_msg="" if is_ok else (rec["error"] or None))

    # ------------------------------------------------------------
    # [Iris P0] run_code —— ZCode 沙箱内真实执行代码（2026-09-27）
    # 授权与依据：D:\Iris\docs\p0_audit.md（红线3"仅在现有适配器层封装接口"；
    # 阶段2.1：新增方法，不修改既有 run_zcode 动作与引擎列表接口）。
    # 设计要点（p0_audit §5）：ZCode CLI 为 Agent 形态，--disallowed-tools Bash
    # 使其结构上无法执行代码（文件头 Day9 实测注记），故本方法在沙箱白名单内以
    # 子进程直接执行，完整复用本类既有构件：
    #   - 四态状态机：pending/running/completed/failed（base.py 词表，timeout 收敛 failed）
    #   - 沙箱白名单：SANDBOX_ROOT 之内按 task_id 建独立目录 iris_runs/<task_id>/
    #   - 进程管理：subprocess + 超时强制终止（_worker 惯例；单进程脚本 run() 即可回收）
    #   - 审计钩子：_write_event（zcode_task_started/finished，args.action="run_code"）
    #     + _transition（agent_sessions），失败只 warning 绝不阻断
    # 语言映射：python → sys.executable（MoRay venv 解释器）；javascript → PATH 中的 node；
    # 其余拒绝（unsupported_language）。输出解码 UTF-8 优先 GBK 回退（_decode_line 同策略）。
    # ------------------------------------------------------------
    RUN_CODE_DEFAULT_TIMEOUT = 10
    RUN_CODE_MAX_TIMEOUT = 30
    RUN_CODE_OUTPUT_LIMIT = 64 * 1024  # 与 agent_tools.OUTPUT_LIMIT 同口径
    RUN_CODE_LANGUAGES = {
        "python": (None, ".py"),       # None = 运行时取 sys.executable
        "python3": (None, ".py"),
        "javascript": ("node", ".js"),
        "js": ("node", ".js"),
    }

    def run_code(self, code, language="python", timeout=None):
        """[Iris P0] 在 ZCode 沙箱白名单内真实执行一段代码（同步；调用方置于线程池）。

        入参：code（代码内容）、language（默认 python）、timeout（秒，默认 10，上限 30）。
        出参：{task_id, status, stdout, stderr, exit_code, duration_ms, error, cwd}
        —— 任务书 2.1 出参 {status, stdout, stderr, exit_code, duration_ms} 的超集。
        任何拒绝/失败都以四态 + error 落记录，绝不静默（base.py 契约）。
        """
        language = str(language or "python").strip().lower()
        try:
            timeout_sec = int(float(timeout)) if timeout is not None else self.RUN_CODE_DEFAULT_TIMEOUT
        except (TypeError, ValueError):
            timeout_sec = self.RUN_CODE_DEFAULT_TIMEOUT
        timeout_sec = max(1, min(self.RUN_CODE_MAX_TIMEOUT, timeout_sec))

        task_id = uuid.uuid4().hex[:12]
        rec = {
            "task_id": task_id,
            "status": STATUS_PENDING,
            "task": {"action": "run_code", "language": language},
            "created_at": time.time(),
            "started_at": None,
            "ended_at": None,
            "duration_ms": None,
            "exit_code": None,
            "result": None,
            "error": None,
            "cwd_path": None,
            "session_id": None,     # 审计/状态机归属：iris-zcode（语义独立于主对话/Argus）
        }
        with self._lock:
            self._tasks[task_id] = rec

        def fail(err):
            """受理/准备阶段失败：四态收敛 failed + 审计收尾，返回统一出参。"""
            rec["status"] = STATUS_FAILED
            rec["error"] = err
            rec["ended_at"] = time.time()
            rec["duration_ms"] = int((rec["ended_at"] - rec["created_at"]) * 1000)
            self._write_event(rec, "zcode_task_finished", "error",
                              duration_ms=rec["duration_ms"], output=None, error_msg=err)
            self._transition(rec, "failed", error_msg=err)
            return self._run_code_payload(rec, "", "", None)

        # a) 校验：代码非空（base.py：空任务拒绝受理）
        code_text = str(code or "")
        if not code_text.strip():
            return fail("missing code（代码内容为空，拒绝执行）")
        # b) 语言白名单
        if language not in self.RUN_CODE_LANGUAGES:
            return fail("unsupported_language: %r（支持 python / javascript）" % language)
        interpreter, ext = self.RUN_CODE_LANGUAGES[language]
        if interpreter is None:
            interpreter = sys.executable  # MoRay venv 解释器，确定存在
        else:
            interpreter = shutil.which(interpreter)
            if not interpreter:
                return fail("interpreter_not_found: node（未在 PATH 找到 Node.js）")
        # c) 沙箱白名单：每次运行独立目录，resolve 后必须仍在 SANDBOX_ROOT 之内
        run_dir = self.root / "iris_runs" / task_id
        try:
            resolved = run_dir.resolve()
            if not resolved.is_relative_to(self.root):
                return fail("path_outside_whitelist: %s" % run_dir)
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return fail("sandbox_mkdir_failed: %s" % e)
        rec["cwd_path"] = resolved
        rec["session_id"] = self._resolve_sid({"session_id": "iris-zcode"})
        self._write_event(rec, "zcode_task_started", "ok",
                          output={"task_id": task_id, "input": code_text[:2000],
                                  "session_id": rec["session_id"], "language": language})
        self._transition(rec, "pending", error_msg="")
        # d) 写入代码文件（UTF-8；Python 3 源码默认按 UTF-8 解码）
        code_file = resolved / ("main" + ext)
        try:
            code_file.write_text(code_text, encoding="utf-8")
        except OSError as e:
            return fail("code_write_failed: %s" % e)

        # e) 同步执行（进程管理惯例：CREATE_NO_WINDOW 不弹黑框；超时由 subprocess 强杀）
        rec["status"] = STATUS_RUNNING
        rec["started_at"] = time.time()
        self._transition(rec, "running", error_msg="")
        env = self._build_env()
        env["PYTHONIOENCODING"] = "utf-8"  # 管道输出强制 UTF-8，避免 Windows 本地编码伤 unicode 打印
        try:
            proc = subprocess.run(
                [interpreter, code_file.name],
                cwd=str(resolved), capture_output=True,
                timeout=timeout_sec, env=env, creationflags=CREATION_FLAGS,
            )
            rc = proc.returncode
            out_text = self._run_code_decode(proc.stdout)
            err_text = self._run_code_decode(proc.stderr)
            rec["exit_code"] = rc
            rec["ended_at"] = time.time()
            rec["duration_ms"] = int((rec["ended_at"] - rec["started_at"]) * 1000)
            if rc == 0:
                rec["status"] = STATUS_COMPLETED
            else:
                rec["status"] = STATUS_FAILED
                rec["error"] = "exit_code_%s" % rc
        except subprocess.TimeoutExpired as e:
            # 超时收敛：四态 failed(error="timeout")，与 run_zcode 收口惯例一致
            rec["status"] = STATUS_FAILED
            rec["error"] = "timeout"
            rec["exit_code"] = None
            rec["ended_at"] = time.time()
            rec["duration_ms"] = int((rec["ended_at"] - rec["started_at"]) * 1000)
            out_text = self._run_code_decode(e.stdout or b"")
            err_text = self._run_code_decode(e.stderr or b"")
            if not err_text:
                err_text = "执行超过 %ss 未结束，进程已被强制终止" % timeout_sec
            rc = None
        except OSError as e:
            return fail("startup_failed: %s" % e)

        # f) 审计收尾（先事件后流转，last_event_id 指向 finished 事件——_finalize 同序）
        is_ok = rec["status"] == STATUS_COMPLETED
        audit_status = "ok" if is_ok else ("timeout" if rec["error"] == "timeout" else "error")
        rec["result"] = {
            "exit_code": rec["exit_code"],
            "duration_ms": rec["duration_ms"],
            "cwd": str(rec["cwd_path"]),
            "file": str(code_file),
        }
        self._write_event(rec, "zcode_task_finished", audit_status,
                          duration_ms=rec["duration_ms"],
                          output={"status": rec["status"], "exit_code": rec["exit_code"],
                                  "stdout": out_text[:2000], "stderr": err_text[:2000]},
                          error_msg="" if is_ok else str(rec["error"] or ""))
        self._transition(rec, "completed" if is_ok else "failed",
                         error_msg="" if is_ok else (rec["error"] or None))
        return self._run_code_payload(rec, out_text, err_text, rc)

    def _run_code_decode(self, data):
        """[Iris P0] 子进程输出解码：UTF-8 优先、GBK 回退（_decode_line 同策略），超限截断。"""
        if not data:
            return ""
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = data.decode("gbk", errors="replace")
        else:
            text = str(data)
        return text[: self.RUN_CODE_OUTPUT_LIMIT]

    def _run_code_payload(self, rec, stdout, stderr, exit_code):
        """[Iris P0] 统一出参组装（任务书 2.1 字段 + task_id/error/cwd 定位字段）。"""
        return {
            "task_id": rec["task_id"],
            "status": rec["status"],
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": rec["duration_ms"],
            "error": rec["error"],
            "cwd": str(rec["cwd_path"] or ""),
        }

    # ---------------- [引擎解耦 v1 2026-10-21] 流式任务四方法（自 api.py Day24b 收编） ----------------
    # 与 ExecutionEngine 五方法契约并行的"流式"扩展面：api.py 通用派发路由只认这四个方法名，
    # 未来 LayaEngine 等新引擎只要实现同名方法即可挂上 /api/engines/{name}/* 通用端点。
    # 语义与 api.py 原手写路由逐字一致（红线）；错误一律 raise StreamTaskError，路由层翻译。

    def start_stream(self, body: dict) -> dict:
        """[Day24b] 受理 ZCode 流式任务：spawn CLI（stream-json）即刻返回。

        出参 {"ok": True, "task_id", "status": "running"}（扁平形，与 GET /stream 的 SSE
        契约配对，前端按 task_id 直读）。校验失败/受理失败 raise StreamTaskError。
        需在事件循环上下文调用（内部取 running loop 供 reader 线程投递）。
        """
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise StreamTaskError("bad_request", "缺少 prompt 字段（要执行的任务指令）")
        language = str(body.get("language") or "python").strip().lower() or "python"
        # [器官→记忆 线C] 调用方标记：Laya tool_registry 调本端点时 body 带 caller="laya"。
        # 流旁路记忆钩子（线B）见 caller=="laya" 即跳过——Laya executor 已在任务链收尾写
        # laya_executor/task_result，本端点只是它链路里的一步，双写会 N+1（2026-10-04 裁断）。
        caller = str(body.get("caller") or "").strip().lower()[:32]

        # 惰性清理：已结束超过 1h 的任务记录顺手回收（无 lifespan 循环，与 L5.0 惯例一致）
        now = time.time()
        for stale in [t for t, r in _ZCODE_STREAM_TASKS.items()
                      if r.get("done_at") and now - r["done_at"] > 3600]:
            _ZCODE_STREAM_TASKS.pop(stale, None)
        # [停止修复 2026-10-03] 进程常登表同口径回收（done_at 由 reader 线程在进程 EOF 后补）
        for stale in [t for t, r in _ZCODE_STREAM_PROCS.items()
                      if r.get("done_at") and now - r["done_at"] > 3600]:
            _ZCODE_STREAM_PROCS.pop(stale, None)

        task_id = uuid.uuid4().hex
        sandbox_dir = Path(SANDBOX_ROOT) / "day24b_runs" / task_id
        try:
            sandbox_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StreamTaskError("sandbox_mkdir_failed", f"创建沙箱目录失败：{e}", 500) from e

        # CLI 解析与 __init__ 同口径：env MORAY_ZCODE_CLI 优先，缺省绝对路径 node + zcode.cjs
        env_cmd = os.environ.get("MORAY_ZCODE_CLI", "").strip()
        cli_cmd = [env_cmd] if env_cmd else [ZCODE_CMD, ZCODE_SCRIPT]
        # spawn 参数与 Day24a 实证命令逐字一致（stream-json 只在该形态下被真机验证过）；
        # --mode yolo 强制显式传（-p 缺省 mode 时 CLI 默认 yolo，Day9 实测）。
        cmd = [*cli_cmd, "-p", prompt, "--output-format", "stream-json",
               "--mode", "yolo", "--cwd", str(sandbox_dir)]
        # [批次二·5 任务4] 数据目录隔离：与 _build_env 同口径——**硬覆盖**。
        # 后端进程常继承桌面 ZCode 会话的 ZCODE_PERSONAL_PROVIDER_CONFIG_FILE（指向 C 盘
        # 真实配置、含云端 Key），setdefault 因变量已存在而失效（E2E 实证会偷跑云端），
        # 故这里强制指到项目内脱敏配置并清出桌面 builtin 缓存路径。
        zcode_env = dict(os.environ)
        zcode_env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = ZCODE_PERSONAL_CONFIG
        zcode_env["ZCODE_DATA_BASE_DIR"] = ZCODE_DATA_DIR
        zcode_env["ZCODE_STORAGE_DIR"] = ZCODE_DATA_DIR
        zcode_env.pop("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", None)
        zcode_env.setdefault("ZCODE_MODEL_TELEMETRY_ENABLED", "0")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(sandbox_dir),
                env=zcode_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # Day24a 实测 stderr 0 字节，不解析（任务书 gotcha 4）
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=CREATION_FLAGS,
            )
        except OSError as e:
            raise StreamTaskError("spawn_failed", f"ZCode CLI 启动失败：{e}", 500) from e

        queue: asyncio.Queue = asyncio.Queue()
        rec = {
            "task_id": task_id,
            "status": "running",
            "prompt": prompt,
            "language": language,
            "caller": caller,
            "cwd": str(sandbox_dir),
            "cmd": cmd,
            "proc": proc,
            "queue": queue,
            "loop": asyncio.get_running_loop(),
            "created_at": time.time(),
            "done_at": None,
        }
        _ZCODE_STREAM_TASKS[task_id] = rec
        _ZCODE_STREAM_PROCS[task_id] = rec   # [停止修复 2026-10-03] 进程常登（流断连不弹，kill 端点数据源）
        threading.Thread(
            target=_zcode_stream_reader, args=(task_id, proc, queue, rec["loop"]),
            name="moray-zcode-stream-" + task_id[:8], daemon=True,
        ).start()
        # [v5 step1] 受理成功（子进程起 + reader 线程起）→ started 上总线。trace_id 分键
        # task_id+":started"——succeeded 保持裸 task_id（send 幂等判重按 trace_id 全表唯一，
        # 裸键互吞即丢终态事件）。总闸关 = send no-op；异常只在此留痕，绝不阻断受理返回。
        try:
            from .. import synapse_bus
            _started_is_laya = caller == "laya"
            synapse_bus.send(
                "laya.task.started" if _started_is_laya else "zcode.task.started",
                "ZCode：%s（受理开始）" % prompt[:200],
                thread_id=None,
                meta={"trace_id": task_id + ":started", "task_id": task_id,
                      "language": language,
                      "caller": "laya" if _started_is_laya else "zcode"},
                from_member="organ.laya" if _started_is_laya else "organ.zcode")
        except Exception as e:  # noqa: BLE001 - started 上总线绝不影响受理
            import logging
            logging.getLogger("moray.zcode").warning(
                "[zcode-bus] started 事件异常：task=%s err=%s: %s",
                task_id, type(e).__name__, str(e)[:200])
        return {"ok": True, "task_id": task_id, "status": "running"}

    def stream_events(self, task_id: str):
        """[Day24b] 返回 SSE 事件 async 生成器：data: <原始行>\n\n 逐条透传，[DONE] 后关闭。

        - task_id 不在登记表 → raise StreamTaskError("not_found")（路由层翻译 404）；
        - 15s 无事件发一条 SSE 注释行保活（EventSource 自动忽略 ":" 开头行）；
        - 流结束后登记表即剔除（重连语义：无）。
        """
        task = _ZCODE_STREAM_TASKS.get(task_id)
        if task is None:
            raise StreamTaskError("not_found", f"任务不存在或已结束：{task_id}", 404)
        queue = task["queue"]

        async def _gen():
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    yield "data: " + item + "\n\n"
            finally:
                _ZCODE_STREAM_TASKS.pop(task_id, None)

        return _gen()

    async def kill_stream_task(self, task_id: str) -> dict:
        """[停止修复 2026-10-03] 终止流式任务进程树——执行卡「停止」的服务端入口。

        - 查 _ZCODE_STREAM_PROCS（spawn 常登，流断连不弹）：同时覆盖直播态与关页后的
          「执行快照」孤儿态；主表 _ZCODE_STREAM_TASKS 已被流 finally 弹掉也不影响本方法。
        - kill 与弹表解耦：本方法**不碰任何登记表条目**——主表弹出仍归 stream_events 的
          finally、done_at 补记仍归 reader 线程，僵尸记录由受理端惰性清扫回收。
        - proc.poll() 非空 → 不发 taskkill（防 pid 复用误杀无关进程），如实回 already_exited。
        - 杀成功后 stdout EOF → reader 投 [DONE] 哨兵 → 直播中的 SSE 自然收尾，无需额外通知。
        """
        rec = _ZCODE_STREAM_PROCS.get(task_id)
        if rec is None:
            raise StreamTaskError("not_found", f"任务不存在或已回收：{task_id}", 404)
        proc = rec.get("proc")
        pid = getattr(proc, "pid", None)
        if pid is None:
            raise StreamTaskError("no_proc", f"任务记录缺少进程句柄：{task_id}", 500)
        rc = proc.poll()
        if rc is not None:
            return {"ok": True, "task_id": task_id, "killed": False,
                    "reason": "already_exited", "exit_code": rc, "pid": pid}
        ok, detail = await asyncio.to_thread(_zcode_kill_proc_tree, pid)
        if not ok and proc.poll() is None:
            try:  # taskkill 失败兜底：至少杀掉直系子进程（_kill_tree 同形态）
                proc.kill()
                ok, detail = True, "fallback_proc_kill"
            except OSError as e:
                detail += "；proc.kill 亦失败：" + str(e)
        return {"ok": True, "task_id": task_id, "killed": bool(ok), "reason": detail, "pid": pid}

    def read_task_file(self, task_id: str, filename: str):
        """[Day24b-d] 读取任务沙箱内单个文件供前端预览（原始内容直出）。

        返回 (media_type, content)：图片 content 为 bytes、文本为 str（UTF-8 replace）；
        失败 raise StreamTaskError（413 走 body={"ok": false, "error": "file too large"}）。

        安全设计（spec 逐条，与 api.py 原实现同）：
        - 穿越形态前置拒绝（..、路径分隔符、盘符/NTFS 数据流的冒号、NUL）→ 403，先于存在性判定；
        - task_id 必须是本后端签发的 uuid4.hex（32 位十六进制），否则 404；
        - os.path.realpath 后必须仍落在该任务沙箱目录内（防符号链接/junction 逃逸）→ 越界 403；
        - 只服务该任务自己的 day24b_runs/<task_id>/ 顶层文件；Windows 保留设备名（CON/NUL/...）→ 404
          （open 这类名字可能阻塞事件循环，防御性拒绝）。
        存在性判定的实现偏差（报告已标）：SSE 流结束即从 _ZCODE_STREAM_TASKS 剔出（既有行为、
        红线禁改），而预览恰发生在任务结束后——故「登记表命中 或 沙箱目录存在」任一即可，
        两者皆无 → 404。安全属性不变：只有本后端真实受理过的 task_id 才会有沙箱目录。
        """
        text_exts = {".py", ".js", ".ts", ".json", ".sh", ".bat", ".txt", ".md"}
        image_mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                      ".gif": "image/gif", ".webp": "image/webp"}
        max_preview_bytes = 512 * 1024

        def _bad_segment(s: str) -> bool:
            """单段路径名非法形态：空、. / ..、含分隔符、盘符或 NTFS 数据流的冒号、NUL。"""
            return (not s) or s in (".", "..") or ("/" in s) or ("\\" in s) or (":" in s) or ("\x00" in s)

        # ---- 1. 穿越形态前置拒绝（403）----
        if _bad_segment(task_id) or _bad_segment(filename):
            raise StreamTaskError("path_traversal", "非法路径（拒绝访问）", 403)

        # ---- 2. task_id 必须是本后端签发的 uuid4.hex；登记表或沙箱目录任一命中，否则 404 ----
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise StreamTaskError("not_found", f"任务不存在：{task_id}", 404)
        task_dir = Path(SANDBOX_ROOT) / "day24b_runs" / task_id
        if task_id not in _ZCODE_STREAM_TASKS and not task_dir.is_dir():
            raise StreamTaskError("not_found", f"任务不存在：{task_id}", 404)
        # Windows 保留设备名（open 可能阻塞事件循环）→ 按不存在处理
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.[^/\\]*)?", filename):
            raise StreamTaskError("not_found", f"文件不存在：{filename}", 404)

        # ---- 3. realpath 包含性（spec：realpath 后必须仍以沙箱目录 realpath 开头）----
        base_real = os.path.realpath(str(task_dir))
        target_real = os.path.realpath(str(task_dir / filename))
        try:
            if not Path(target_real).is_relative_to(Path(base_real)):
                raise StreamTaskError("path_traversal", "非法路径（拒绝访问）", 403)
        except (OSError, ValueError) as e:
            raise StreamTaskError("read_error", f"路径解析失败：{e}", 500) from e

        target = Path(target_real)
        if not target.is_file():
            raise StreamTaskError("not_found", f"文件不存在：{filename}", 404)

        # ---- 4. 大小闸门（413 body 按 spec 逐字）----
        try:
            size = target.stat().st_size
        except OSError as e:
            raise StreamTaskError("read_error", f"读取失败：{e}", 500) from e
        if size > max_preview_bytes:
            raise StreamTaskError("file_too_large", "file too large", 413,
                                  body={"ok": False, "error": "file too large"})

        # ---- 5. 按扩展名出 MIME：图片原样 bytes；文本 UTF-8；未知 → octet-stream（前端按文本兜底）----
        ext = os.path.splitext(filename)[1].lower()
        try:
            with open(target_real, "rb") as f:
                data = f.read()
        except OSError as e:
            raise StreamTaskError("read_error", f"读取失败：{e}", 500) from e
        if ext in image_mime:
            return image_mime[ext], data
        if ext in text_exts:
            media = "text/plain; charset=utf-8"
        elif ext in (".html", ".htm"):
            media = "text/html; charset=utf-8"
        else:
            media = "application/octet-stream"
        return media, data.decode("utf-8", errors="replace")
