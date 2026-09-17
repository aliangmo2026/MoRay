"""MoRay v3.21.0 —— 「一键自动指挥 Codex」后端编排层（新文件）

职责：把前端任务分派面板点「自动执行」的任务，落地成本机 `codex exec` 子进程，
把运行状态 / 增量日志 / 退出码 / token usage 通过 HTTP 回传前端。

设计约束（与既有代码风格一致）：
- 同一时刻只允许 1 个编排任务在跑（并发请求 → 409 busy）。产品是单机工作台，不做并发 UI；
- codex 全程非交互：`exec` + `--json`（JSONL 事件，含 usage）+ `--skip-git-repo-check`
  （工程目录常常不是 git 仓库）+ `-s workspace-write`（项目目录内可写、越界仍被沙箱拦住）
  + `-C <project>`（工作根）；**提示词走 stdin（`-`）**，彻底绕开 cmd.exe 对 & | " ^ 的转义地狱；
- 超时 / 用户停止 → `taskkill /T /F` 杀掉整棵进程树（codex.cmd → node 子进程），不残留；
- 日志：内存保留最近 LOG_KEEP 行供轮询增量拉取，全文同时落 %TEMP%\\moray_agent_<task_id>.log；
- 诚实原则：状态只由真实退出码决定；usage 只在 codex 真的返回时记录，拿不到就是 null
  （前端据此显示"未能获取"，绝不编造数字）。

协议（与 agent_tools.py 一致的 {ok,data} / {ok:false,code,message} 信封）：
  GET  /api/agent/run/preflight?project_path=&command=   执行前校验（路径/命令/占用/模型名）
  POST /api/agent/run                                    启动（task_id/prompt/project_path/timeout_sec/command）
  GET  /api/agent/tasks/{task_id}?offset=N               状态 + 增量日志
  POST /api/agent/tasks/{task_id}/stop                   停止（杀进程树）
  GET  /api/agent/runs?limit=N                           最近运行记录（页面刷新后可恢复展示）
"""
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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config

_logger = logging.getLogger("moray.agent_runner")

router = APIRouter()

# ---- 常量 ----
DEFAULT_TIMEOUT = 600          # 默认任务超时（秒）
MIN_TIMEOUT = 10
MAX_TIMEOUT = 3600
LOG_KEEP = 4000                # 内存保留日志行数（前端轮询增量拉取）
RUN_HISTORY = 50               # 内存保留运行记录条数
DEFAULT_COMMAND = "codex.cmd"  # 默认 codex CLI（cmd 包装器，规避 PowerShell 执行策略）
SANDBOX_MODE = "workspace-write"  # 固定沙箱：项目目录内可写；越界/网络等仍由 codex 沙箱拦截

CREATION_FLAGS = 0
if sys.platform == "win32":
    # CREATE_NEW_PROCESS_GROUP：独立进程组（配合 taskkill /T 干净收树）
    # CREATE_NO_WINDOW：不弹控制台窗口（后端在后台跑，用户不该看到黑框闪）
    CREATION_FLAGS = 0x00000200 | 0x08000000

# ---- 诚实告警：日志里出现这些真实信号时，明确提示"可能没真正干成" ----
WARNING_PATTERNS = [
    (re.compile(r"blocked by read-only sandbox|writing is blocked", re.I),
     "沙箱拒绝了写入（只读沙箱）：本次可能没有真正改文件"),
    (re.compile(r"rejected by user approval settings|patch rejected", re.I),
     "审批被拒：非交互模式下无法弹窗确认，操作未执行"),
    (re.compile(r"insufficient balance|余额不足|quota exceeded", re.I),
     "模型侧余额/配额异常"),
    (re.compile(r"\b401\b|invalid_api_key|Unauthorized", re.I),
     "模型侧鉴权失败（API Key / provider 配置）"),
]


# ---------------------------------------------------------------- 工具函数

def _err(status_code: int, code: str, message: str):
    return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "message": message})


def _decode_line(data: bytes) -> str:
    """单行字节解码：优先 UTF-8；失败回退 gbk + replace（与 agent_tools._decode_output 同策略）"""
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


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME", "").strip()
    return Path(env) if env else Path.home() / ".codex"


def _codex_model():
    """读取 ~/.codex/config.toml 里生效的 model 名（仅用于成本估算口径，失败返回 None）"""
    try:
        cfg = _codex_home() / "config.toml"
        text = cfg.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001 - 读不到不影响运行，只是成本显示降级
        return None


def _resolve_command(raw: str):
    """解析 codex 命令为可执行文件绝对路径。

    - 空 → codex.cmd；带路径分隔符 → 按路径校验（相对路径以工程根为基准）；
    - 纯命令名 → shutil.which（找不到再补 .cmd 后缀重试）。
    @returns {tuple} (可执行绝对路径|None, 说明文本)
    """
    name = (raw or "").strip() or DEFAULT_COMMAND
    p = Path(os.path.expandvars(os.path.expanduser(name)))
    if p.is_absolute() or os.sep in name or "/" in name:
        if p.is_file():
            return str(p.resolve()), "按给定路径解析"
        return None, f"路径不存在或不是文件：{p}"
    found = shutil.which(name)
    if not found and not name.lower().endswith((".cmd", ".bat", ".exe")):
        found = shutil.which(name + ".cmd")
    if not found:
        return None, f"PATH 中找不到命令：{name}（可改为绝对路径，例如 C:\\Users\\<你>\\AppData\\Roaming\\npm\\codex.cmd）"
    return str(Path(found).resolve()), "PATH 解析"


def _merge_usage(acc, new):
    """累计各轮 usage（多轮 turn 各自的输入/输出都要计费，故求和）"""
    if not isinstance(new, dict):
        return acc
    out = dict(acc or {})
    for k, v in new.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[k] = out.get(k, 0) + v
    return out


# ---------------------------------------------------------------- 运行对象

class Run:
    """一次 codex 编排运行（状态/日志/usage/进程句柄）"""

    def __init__(self, task_id: str, prompt: str, project_path: str, command_path: str,
                 command_display: str, timeout_sec: int, model):
        self.run_id = "r" + uuid.uuid4().hex[:12]
        self.task_id = task_id
        self.prompt = prompt
        self.project_path = project_path
        self.command_path = command_path
        self.command_display = command_display
        self.timeout_sec = timeout_sec
        self.model = model
        self.status = "running"        # running | completed | failed | timeout | canceled
        self.exit_code = None
        self.error = None
        self.usage = None
        self.warnings = []
        self.started_at = time.time()
        self.ended_at = None
        self.duration_sec = None
        self.dropped = 0               # 已被内存裁剪的日志行数（全局序号补偿）
        self.lines = []
        self.log_path = str(Path(tempfile.gettempdir()) / ("moray_agent_%s.log" % _safe_name(task_id)))
        self._lock = threading.Lock()
        self._fh = None
        self._proc = None
        self._killed = False

    # ---- 日志 ----
    def append(self, line: str):
        line = str(line).rstrip("\r\n")
        if line == "":
            return
        with self._lock:
            self.lines.append(line)
            if len(self.lines) > LOG_KEEP:
                drop = len(self.lines) - LOG_KEEP
                del self.lines[:drop]
                self.dropped += drop
            for pat, msg in WARNING_PATTERNS:
                if msg not in self.warnings and pat.search(line):
                    self.warnings.append(msg)
            if self._fh is not None:
                try:
                    self._fh.write(line + "\n")
                except OSError:
                    pass

    def open_log(self):
        try:
            self._fh = open(self.log_path, "w", encoding="utf-8", buffering=1)
        except OSError as e:  # noqa: BLE001 - 日志落盘失败不影响运行
            _logger.warning("agent run log open failed: %s", e)
            self._fh = None

    def close_log(self):
        with self._lock:
            fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    # ---- 进程 ----
    def set_proc(self, proc):
        with self._lock:
            self._proc = proc

    def pid(self):
        with self._lock:
            return self._proc.pid if self._proc is not None else None

    def kill_tree(self, why: str) -> bool:
        """杀掉整棵进程树（Windows: taskkill /T /F /PID；其他平台杀进程组）。

        返回是否真的发出过终止动作（用于日志措辞，不谎报）。
        """
        with self._lock:
            if self._killed or self._proc is None:
                return False
            self._killed = True
            proc = self._proc
        pid = proc.pid
        ok = False
        try:
            if sys.platform == "win32":
                r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=20,
                                   creationflags=0x08000000, check=False)
                ok = r.returncode == 0
                if not ok:
                    tail = _decode_line(r.stderr or r.stdout or b"").strip()[:200]
                    self.append("[MoRay] taskkill 返回 %s%s" % (r.returncode, ("：" + tail) if tail else ""))
            else:
                os.killpg(os.getpgid(pid), 9)
                ok = True
        except Exception as e:  # noqa: BLE001 - 兜底：至少杀掉直接子进程
            self.append("[MoRay] 进程树终止异常（转 kill 直系子进程）：%s" % e)
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - 进程可能已退出
            pass
        self.append("[MoRay] %s，已终止进程树（pid %s）" % (why, pid))
        return ok

    # ---- 序列化 ----
    def summary(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "task_id": self.task_id,
                "status": self.status,
                "exit_code": self.exit_code,
                "error": self.error,
                "usage": self.usage,
                "model": self.model,
                "warnings": list(self.warnings),
                "project_path": self.project_path,
                "command": self.command_display,
                "timeout_sec": self.timeout_sec,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration_sec": self.duration_sec,
                "log_path": self.log_path,
                "total_lines": self.dropped + len(self.lines),
            }

    def snapshot(self, offset: int = 0) -> dict:
        data = self.summary()
        with self._lock:
            total = self.dropped + len(self.lines)
            off = max(0, int(offset or 0))
            if off >= total:
                chunk = []
            else:
                start = max(0, off - self.dropped)
                chunk = list(self.lines[start:])
            data["offset"] = off
            data["lines"] = chunk
            data["truncated"] = off < self.dropped
        return data


def _safe_name(s: str) -> str:
    """日志文件名安全化（去掉路径分隔符与 Windows 非法字符）"""
    return re.sub(r'[^0-9A-Za-z_.-]', "_", str(s or "task"))[:80] or "task"


# ---------------------------------------------------------------- 运行注册表

_RUNS = {}          # run_id -> Run（含已结束的，保留最近 RUN_HISTORY 条）
_ORDER = []         # run_id 时间顺序（用于裁剪）
_ACTIVE = {"run_id": None}
_LOCK = threading.RLock()


def _register(run: Run):
    with _LOCK:
        _RUNS[run.run_id] = run
        _ORDER.append(run.run_id)
        _ACTIVE["run_id"] = run.run_id
        while len(_ORDER) > RUN_HISTORY:
            old = _ORDER.pop(0)
            if old != _ACTIVE["run_id"]:
                _RUNS.pop(old, None)


def _release(run: Run):
    with _LOCK:
        if _ACTIVE["run_id"] == run.run_id:
            _ACTIVE["run_id"] = None


def _busy_run():
    """当前占用执行闸门的运行。

    只有"状态仍是 running"才算忙：收尾线程在**设置状态之后**才释放闸门，
    这里再按状态兜一次，避免"状态已变但闸门未释放"的瞬时窗口把并发请求误判为 busy。
    """
    with _LOCK:
        rid = _ACTIVE["run_id"]
        run = _RUNS.get(rid) if rid else None
    if run is not None:
        with run._lock:
            if run.status != "running":
                return None
    return run


def _latest_for_task(task_id: str):
    with _LOCK:
        for rid in reversed(_ORDER):
            run = _RUNS.get(rid)
            if run is not None and run.task_id == task_id:
                return run
    return None


# ---------------------------------------------------------------- 执行体

def _parse_stdout_line(run: Run, raw_line: str):
    """把 codex --json 的 JSONL 事件翻译成人类可读日志行（未知事件降级为紧凑原文）"""
    s = raw_line.strip()
    if not s:
        return
    try:
        ev = json.loads(s)
    except ValueError:
        run.append("[原始] " + s[:500])
        return
    if not isinstance(ev, dict):
        run.append("[原始] " + s[:500])
        return
    etype = ev.get("type")
    item = ev.get("item") if isinstance(ev.get("item"), dict) else None
    if etype in ("item.started", "item.completed") and item is not None:
        itype = item.get("type")
        started = etype == "item.started"
        if itype == "command_execution":
            if started:
                run.append("▶ 执行命令：" + _short_command(item.get("command")))
            else:
                out = item.get("aggregated_output") or ""
                for l in str(out).splitlines():
                    run.append("  │ " + l)
                code = item.get("exit_code")
                run.append("  └ 命令结束（退出码 %s）" % ("-" if code is None else code))
        elif itype == "file_change":
            for ch in (item.get("changes") or []):
                if isinstance(ch, dict):
                    run.append("✎ 文件改动：%s（%s）" % (ch.get("path"), ch.get("kind") or "update"))
        elif itype == "agent_message":
            for l in str(item.get("text") or "").splitlines():
                run.append("🤖 " + l)
        elif itype == "reasoning":
            txt = str(item.get("text") or "").strip()
            if txt:
                run.append("💭 " + txt.splitlines()[0][:200])
        elif itype in ("todo_list", "plan"):
            run.append("🗒 " + json.dumps(item, ensure_ascii=False)[:400])
        else:
            if started:
                run.append("· " + str(itype) + " 开始")
            else:
                run.append("· " + json.dumps(item, ensure_ascii=False)[:400])
        return
    if etype == "turn.completed":
        usage = ev.get("usage")
        with run._lock:
            run.usage = _merge_usage(run.usage, usage)
        run.append("— 本轮结束 · %s" % _usage_text(usage))
        return
    if etype == "turn.started":
        run.append("— 模型开始处理")
        return
    if etype == "thread.started":
        run.append("— 会话已建立（thread %s）" % str(ev.get("thread_id") or "")[:12])
        return
    if etype in ("error", "turn.failed"):
        run.append("[错误] " + json.dumps(ev, ensure_ascii=False)[:500])
        return
    run.append("· " + json.dumps(ev, ensure_ascii=False)[:400])


def _usage_text(usage) -> str:
    """单轮 usage 摘要（真实数字，缺失就直说"未返回"）"""
    if not isinstance(usage, dict):
        return "usage 未返回"
    return "输入 %s / 缓存命中 %s / 输出 %s tokens" % (
        usage.get("input_tokens", "-"), usage.get("cached_input_tokens", "-"), usage.get("output_tokens", "-"))


def _short_command(cmd) -> str:
    """命令摘要：去掉 shell 绝对路径与引号噪声，便于阅读

    注意：必须**先**用正则吃掉落引号包裹的解释器全路径，最后再收尾去引号 ——
    反过来（先 strip('"')）会把开引号去掉，正则就再也匹配不上。
    """
    s = str(cmd or "").strip()
    s = re.sub(r'(?i)^"[^"]*powershell\.exe"\s*(-Command\s*)?', "powershell ", s)
    s = re.sub(r'(?i)^"[^"]*cmd\.exe"\s*(/c\s*)?', "cmd ", s)
    s = re.sub(r'(?i)^"[^"]*\\(?:python|node|pwsh)\.exe"\s*', "", s)
    return s.replace("\\\\", "\\").strip().strip('"')[:300]


def _read_stream(run: Run, stream, is_stdout: bool):
    """逐行读取子进程输出（stdout 走 JSON 解析，stderr 原样入日志）"""
    try:
        while True:
            raw = stream.readline()
            if not raw:
                break
            line = _decode_line(raw).rstrip("\r\n")
            if line == "":
                continue
            if is_stdout:
                _parse_stdout_line(run, line)
            else:
                run.append("[stderr] " + line[:500])
    except Exception as e:  # noqa: BLE001 - 读取异常不影响收尾
        run.append("[MoRay] 读取输出异常：%s" % e)
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def _build_command(run: Run):
    """构造 codex exec 命令（提示词走 stdin，见模块 docstring）"""
    return [run.command_path, "exec", "--json", "--skip-git-repo-check",
            "-s", SANDBOX_MODE, "-C", run.project_path, "-"]


def _finalize(run: Run, status: str, exit_code, error=None):
    with run._lock:
        if run.status == "running":
            run.status = status
        run.exit_code = exit_code
        if error and not run.error:
            run.error = error
        run.ended_at = time.time()
        run.duration_sec = round(run.ended_at - run.started_at, 1)
    # 先释放执行闸门（状态已定），再做日志/审计收尾：避免"状态已结束但闸门未释放"的窗口
    _release(run)
    run.append("—— 运行结束：%s（退出码 %s，耗时 %ss）" % (run.status, exit_code, run.duration_sec))
    if run.usage:
        run.append("—— token 汇总：%s" % json.dumps(run.usage, ensure_ascii=False))
    else:
        run.append("—— token 汇总：模型侧未返回 usage（本次无法估算成本）")
    _audit_run(run)
    run.close_log()


def _audit_run(run: Run):
    """写一条后端审计（复用既有 agent_log 表；失败不影响结果）"""
    try:
        from . import crud
        status = {"completed": "ok", "failed": "error", "timeout": "timeout", "canceled": "error"}.get(run.status, "error")
        detail = "%s · 退出码 %s · 耗时 %ss" % (run.status, run.exit_code, run.duration_sec)
        if run.status == "canceled":
            detail = "用户停止 · " + detail
        crud.append_agent_log("run_codex", "task=%s path=%s" % (run.task_id[:40], run.project_path[:160]),
                              True, status, int((run.duration_sec or 0) * 1000), detail[:200])
    except Exception as e:  # noqa: BLE001
        _logger.warning("agent run audit failed: %s", e)


def _worker(run: Run):
    """后台线程：拉起 codex、喂提示词、收日志、守超时"""
    run.open_log()
    run.append("—— 任务 %s 开始编排" % run.task_id)
    run.append("—— 工作目录：%s" % run.project_path)
    run.append("—— 命令：%s exec --json --skip-git-repo-check -s %s -C <工作目录> -（提示词走 stdin）"
               % (run.command_display, SANDBOX_MODE))
    run.append("—— 超时：%ss · 模型（成本口径，读 ~/.codex/config.toml）：%s"
               % (run.timeout_sec, run.model or "未知"))
    cmd = _build_command(run)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=run.project_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=CREATION_FLAGS,
        )
    except OSError as e:
        run.append("[启动失败] %s" % e)
        _finalize(run, "failed", None, error="进程启动失败：%s" % e)
        return
    except Exception as e:  # noqa: BLE001
        _finalize(run, "failed", None, error="进程启动异常：%s" % e)
        return
    run.set_proc(proc)
    run.append("—— 子进程 pid %s" % proc.pid)
    # 提示词写入 stdin 后立即关闭（codex 读到 EOF 即视为指令结束，不会等待人工输入）
    try:
        proc.stdin.write(run.prompt.encode("utf-8"))
        proc.stdin.flush()
        proc.stdin.close()
    except OSError as e:
        run.append("[MoRay] 提示词写入 stdin 失败：%s" % e)

    t_out = threading.Thread(target=_read_stream, args=(run, proc.stdout, True), daemon=True)
    t_err = threading.Thread(target=_read_stream, args=(run, proc.stderr, False), daemon=True)
    t_out.start()
    t_err.start()

    deadline = run.started_at + run.timeout_sec
    rc = None
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if time.time() > deadline:
            with run._lock:
                timed_out = run.status == "running"
                if timed_out:
                    run.status = "timeout"   # 先定状态，收尾阶段就不会被"按退出码判失败"覆盖
            if timed_out:
                run.append("[MoRay] 超过 %ss 未结束，判定超时，强制终止进程树" % run.timeout_sec)
            run.kill_tree("超时强制结束" if timed_out else "已在收尾阶段终止")
            try:
                rc = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                rc = proc.poll()
            break
        time.sleep(0.2)

    t_out.join(timeout=10)
    t_err.join(timeout=10)
    with run._lock:
        cur = run.status
    if cur == "running":
        _finalize(run, "completed" if rc == 0 else "failed", rc,
                  error=None if rc == 0 else "codex 进程退出码 %s（详见日志）" % rc)
    else:
        _finalize(run, cur, rc)


# ---------------------------------------------------------------- 路由

@router.get("/api/agent/run/preflight")
def agent_run_preflight(project_path: str = "", command: str = ""):
    """执行前校验（前端据此启用/禁用「自动执行」按钮，并把原因写进 tooltip）"""
    raw_path = (project_path or "").strip().strip('"')
    p = Path(os.path.expandvars(os.path.expanduser(raw_path))) if raw_path else None
    exists = bool(p and p.exists())
    is_dir = bool(p and p.is_dir())
    cmd_path, cmd_note = _resolve_command(command)
    busy = _busy_run()
    return {
        "ok": True,
        "data": {
            "project_path": str(p) if p else "",
            "path_exists": exists,
            "path_is_dir": is_dir,
            "path_ok": bool(exists and is_dir),
            "path_note": "" if (exists and is_dir) else (
                "未填写项目路径" if not raw_path else ("路径不存在" if not exists else "路径不是目录")),
            "command": (command or "").strip() or DEFAULT_COMMAND,
            "command_path": cmd_path,
            "command_ok": bool(cmd_path),
            "command_note": cmd_note,
            "model": _codex_model(),
            "busy": bool(busy),
            "active_task_id": busy.task_id if busy else None,
            "timeout_default": DEFAULT_TIMEOUT,
            "sandbox": SANDBOX_MODE,
            "build": config.BUILD,
        },
    }


@router.post("/api/agent/run")
async def agent_run(request: Request):
    """启动一次 codex 编排（body: task_id / prompt / project_path / timeout_sec / command?）"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return _err(400, "bad_request", "请求体必须是 JSON 对象")
    task_id = str(body.get("task_id") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    raw_path = str(body.get("project_path") or "").strip().strip('"')
    if not task_id:
        return _err(400, "bad_request", "缺少 task_id")
    if not prompt:
        return _err(400, "bad_request", "缺少 prompt（任务指令为空，拒绝启动）")
    if not raw_path:
        return _err(400, "bad_request", "缺少 project_path（本功能只在该目录下干活）")
    p = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    if not p.exists():
        return _err(400, "path_not_found", "项目路径不存在：%s" % p)
    if not p.is_dir():
        return _err(400, "path_not_dir", "项目路径不是目录：%s" % p)
    cmd_path, cmd_note = _resolve_command(str(body.get("command") or ""))
    if not cmd_path:
        return _err(400, "command_not_found", "Codex 命令不可用：%s" % cmd_note)
    busy = _busy_run()
    if busy is not None:
        return _err(409, "busy", "已有任务在执行中（task_id=%s，run_id=%s）：本产品单机串行，请先停止或等它跑完"
                    % (busy.task_id, busy.run_id))
    timeout_sec = _clamp_timeout(body.get("timeout_sec"))
    run = Run(task_id=task_id, prompt=prompt, project_path=str(p.resolve()),
              command_path=cmd_path, command_display=(str(body.get("command") or "").strip() or DEFAULT_COMMAND),
              timeout_sec=timeout_sec, model=_codex_model())
    _register(run)
    threading.Thread(target=_worker, args=(run,), name="moray-agent-run-%s" % run.run_id, daemon=True).start()
    _logger.info("agent run started: %s task=%s cwd=%s", run.run_id, task_id, run.project_path)
    return {"ok": True, "data": run.snapshot(0)}


@router.get("/api/agent/tasks/{task_id}")
def agent_task_status(task_id: str, offset: int = 0):
    """该任务最近一次运行的状态 + 自 offset 起的增量日志（前端轮询用）"""
    run = _latest_for_task(task_id)
    if run is None:
        return _err(404, "not_found", "没有该任务的运行记录（可能后端重启过）")
    return {"ok": True, "data": run.snapshot(offset)}


@router.post("/api/agent/tasks/{task_id}/stop")
def agent_task_stop(task_id: str):
    """停止该任务正在执行的运行（杀进程树 → 状态 canceled）"""
    run = _latest_for_task(task_id)
    if run is None:
        return _err(404, "not_found", "没有该任务的运行记录")
    with run._lock:
        running = run.status == "running"
        if running:
            run.status = "canceled"
    if not running:
        return {"ok": True, "data": run.summary()}
    run.append("[MoRay] 用户点击「停止」")
    run.kill_tree("用户停止")
    return {"ok": True, "data": run.summary()}


@router.get("/api/agent/runs")
def agent_runs(limit: int = 20):
    """最近运行记录（新→旧；页面刷新后据此恢复"运行中→中断"的展示）"""
    try:
        n = max(1, min(int(limit or 20), RUN_HISTORY))
    except (TypeError, ValueError):
        n = 20
    with _LOCK:
        ids = list(reversed(_ORDER))[:n]
        runs = [_RUNS[i] for i in ids if i in _RUNS]
    active = _busy_run()
    return {"ok": True, "data": {"runs": [r.summary() for r in runs],
                                 "active_run_id": active.run_id if active else None,
                                 "active_task_id": active.task_id if active else None}}
