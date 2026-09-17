"""本机 Agent 工具路由（阶段0：让 Agent 能受控操作本机，最小可用闭环）

只读/写文件与白名单只读命令四类本机工具，全部限制在"受控工作区"内执行：

安全模型（层层设防，全部可脱离模型单测）：
1. 工作区根：MORAY_WORKSPACE 环境变量 > SQLite kv(agent_workspace，设置页可改) > 默认 D:\\MoRayWorkspace；
   目录不存在时自动创建。所有路径解析后必须仍位于工作区内。
2. 路径安全：pathlib + resolve()（解析符号链接/junction）+ commonpath 前缀判定 + normcase 大小写归一；
   拒绝绝对路径（盘符/UNC/根斜杠）、.. 越界、符号链接/junction 逃逸、Windows 保留设备名（CON/NUL/...）。
3. 副作用双保险：write_file / run_command 在 approved !== true 时绝不执行，返回 {needsApproval, summary}；
   只读工具默认直接执行（审批策略由前端控制，后端不重复拦截只读）。
4. 写文件安全：父目录自动创建；危险/可执行后缀命中即拒；overwrite=false 且文件存在即拒；内容有大小上限。
5. run_command：subprocess.run(shell=False, 列表参数)，命令名必须在只读白名单（git 子命令受限 /
   python|node|npm --version / where <名>），参数逐个正则校验并黑名单敏感词，杜绝 shell 元字符与管道；
   单命令 15s 超时；stdout/stderr 截断。
6. 结果截断：read_file 默认 64KB（可传 maxBytes，上限 1MB），超过 2000 行再截断；list_directory 单层最多 500 条。
7. 审计：所有到达本路由的工具调用（含被拒/未审批/客户端拒绝）写入 SQLite agent_tool_log，GET /api/agent/log 分页。

约束：只绑 127.0.0.1（config.HOST）；不做删除/移动/联网下载执行；本模块自身无任何 shell 拼串。
"""
import codecs
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config, crud

router = APIRouter()
_logger = logging.getLogger("moray.agent")

# ---------------------------------------------------------------- 常量

# 危险/可执行后缀（大小写不敏感，命中即拒写）。不含纯文本扩展的常用类型。
DANGEROUS_EXTS = frozenset(
    ".exe .dll .bat .cmd .ps1 .vbs .vbe .msi .msp .scr .reg .com .pif .jar .hta .cpl .wsf .wsh .lnk "
    ".sh .pyc .pyd .sys .drv .ocx .fon .appx .msix".split()
)

# Windows 保留设备名（作为文件名写会落到设备而非磁盘）
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}

# 副作用工具：approved !== true 绝不执行
SIDE_EFFECT_TOOLS = frozenset({"write_file", "create_file", "move_file", "run_command", "edit_file"})

# 参数合法字符（纯 token：字母数字 + 少量安全标点；禁空格/引号/重定向/管道/百分号等元字符）
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:@/\\+~-]+$")
# git 子命令参数黑名单子串：可导致写文件/执行外挂/网络动作的选项一律拒绝
_GIT_FORBIDDEN = ("output", "ext-diff", "ext_diff", "exec", "upload-pack", "upload_pack", "recurse", "object-format")

# 只读命令白名单：{命令名: 参数校验函数}；校验函数返回 (bool, 拒绝原因)
def _no_args(args):
    return (not args), "该命令不允许带参数（仅支持裸命令形态，如 git status）"


def _git_args(args):
    if not args:
        return True, ""
    if args[0] not in ("status", "log", "diff", "branch"):
        return False, f"git 仅允许 status/log/diff/branch 子命令，收到「{args[0]}」"
    for a in args[1:]:
        if not _TOKEN_RE.match(a) or ".." in a:
            return False, f"git 参数含非法字符：{a[:40]}"
        low = a.lower()
        if any(f in low for f in _GIT_FORBIDDEN):
            return False, f"git 参数被安全策略拒绝（敏感选项）：{a[:40]}"
    return True, ""


def _version_args(args):
    return (args == ["--version"]), "该命令仅允许 --version 形态"


def _where_args(args):
    if not args:
        return False, "where 需要一个命令名参数（如 where python）"
    for a in args:
        if not _TOKEN_RE.match(a) or not re.match(r"^[A-Za-z0-9._\-]+$", a):
            return False, f"where 参数含非法字符：{a[:40]}"
    return True, ""


CMD_WHITELIST = {
    "git": _git_args,
    "python": _version_args,
    "python3": _version_args,
    "node": _version_args,
    "npm": _version_args,
    "where": _where_args,
}

MAX_LIST_ITEMS = 500          # list_directory 单层上限
DEFAULT_MAX_BYTES = 64 * 1024  # read_file 默认字节上限（或 2000 行，先到先截）
MAX_READ_BYTES = 1024 * 1024   # read_file maxBytes 上限
MAX_WRITE_BYTES = 5 * 1024 * 1024  # write_file 内容上限
MAX_LINES = 2000              # read_file 行数上限
CMD_TIMEOUT_SECONDS = 15      # run_command 单命令超时
OUTPUT_LIMIT = 64 * 1024      # run_command stdout/stderr 各截断
FIND_LIMIT = 1000             # find_files 结果上限
SEARCH_LIMIT = 200            # search_text 命中上限
SEARCH_SCAN_LIMIT = 2000      # search_text 单次扫描文件数上限
SEARCH_LINE_CHARS = 200       # search_text 单行截断
EDIT_SNIPPET_CHARS = 80       # edit_file 变更前后片段上下文宽度

# ---------------------------------------------------------------- 错误

class ToolError(Exception):
    """工具可预期错误（参数/文件状态等），以 {ok:false, code, message} 返回"""

    def __init__(self, message, code="tool_error"):
        super().__init__(message)
        self.code = code


class SafeError(ToolError):
    """安全层拒绝：越界 / 危险后缀 / 白名单外命令等（审计 status=rejected）"""

    def __init__(self, message):
        super().__init__(message, code="unsafe")


# ---------------------------------------------------------------- 工作区

def _workspace_root(create: bool = True) -> Path:
    """解析工作区根：环境变量 > kv(agent_workspace) > 默认；必要时自动创建。

    环境变量优先级最高（进程级配置，运维/测试可用）；设置页修改写 kv。
    """
    env_ws = os.environ.get("MORAY_WORKSPACE", "").strip()
    if env_ws:
        root = Path(env_ws)
    else:
        saved = crud.kv_get("agent_workspace") or ""
        root = Path(saved.strip()) if saved.strip() else Path(config.WORKSPACE_DEFAULT)
    try:
        root = root.expanduser().resolve()
    except OSError:
        root = Path(str(root).replace("/", os.sep)).resolve()
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # noqa: BLE001
            raise ToolError(f"无法创建工作区目录 {root}：{e}") from e
    return root


def _is_reserved_name(name: str) -> bool:
    base = name.split(".")[0].upper()
    return base in _RESERVED_NAMES


def _safe_path(raw, *, allow_missing: bool = True) -> Path:
    """把相对工作区的入参 path 解析为受控绝对路径；任何逃逸/越界/保留名都拒绝。

    - 拒绝：绝对路径（盘符 C:/x、UNC \\\\server\\share、根斜杠 /x、\\x）、.. 越界、
      解析后（含符号链接/junction 指向）超出工作区根、Windows 保留设备名、含 NUL 等非法字符。
    - 大小写：Windows 路径比较经 normcase 归一（盘符/全路径小写化），盘符不同会抛 ValueError → 拒绝。
    """
    ws = _workspace_root()
    if raw is None:
        raw = ""
    p = Path(str(raw).strip().replace("\\", "/"))
    if _is_reserved_name(p.name):
        raise SafeError(f"「{p.name}」是 Windows 保留设备名，不允许作为工作区文件")
    # 绝对路径逃逸：pathlib 在 Windows 上 Path(ws) / 'C:/x' 会整体替换为 'C:/x'，必须先行拒绝
    # 注意 drive-relative 形态（'C:secret.txt'）is_absolute()=False 但带盘符，同样拒绝
    if p.is_absolute() or p.drive:
        raise SafeError("不允许绝对路径，请使用相对工作区的路径（如 sub/hello.txt）")
    try:
        target = (ws / p).resolve(strict=False)
    except OSError as e:  # noqa: BLE001 - NUL 等非法字符在 resolve 阶段抛
        raise SafeError(f"路径非法：{e}") from e
    # 解析后（symlink/junction 已展开）必须仍在工作区内；commonpath 不同盘会抛 ValueError
    try:
        ws_n = os.path.normcase(str(ws))
        tgt_n = os.path.normcase(str(target))
        if os.path.commonpath([ws_n, tgt_n]) != ws_n:
            raise SafeError(f"路径越界被拒绝：{raw}（解析后 {target} 不在工作区 {ws} 内）")
    except ValueError:
        raise SafeError(f"路径越界被拒绝：{raw}（跨盘符路径不被允许）") from None
    if not allow_missing and not target.exists():
        raise ToolError(f"路径不存在：{raw}")
    return target


def _summarize_args(name: str, args: dict) -> str:
    """审计用参数摘要：写文件记录目标路径+字节数，命令记录完整命令，其余截断 JSON"""
    try:
        if name == "write_file":
            path = str(args.get("path") or "").strip()
            content = args.get("content")
            size = len(content) if isinstance(content, str) else (len(json.dumps(content, ensure_ascii=False)) if content is not None else 0)
            return f"path={path} bytes={size}"
        if name == "create_file":
            path = str(args.get("path") or "").strip()
            content = args.get("content")
            size = len(content) if isinstance(content, str) else 0
            return f"create path={path} bytes={size}"
        if name == "move_file":
            return "move " + str(args.get("from") or args.get("src") or "").strip()[:80] + \
                " -> " + str(args.get("to") or args.get("dst") or "").strip()[:80]
        if name == "run_command":
            cmd = str(args.get("command") or "").strip()
            a = args.get("args")
            if isinstance(a, list):
                cmd += " " + " ".join(str(x) for x in a)
            return "command=" + cmd[:200]
        return json.dumps(args, ensure_ascii=False)[:200]
    except Exception:  # noqa: BLE001
        return str(args)[:200]


def _audit(name: str, args: dict, approved: bool, status: str, ms: int = 0, detail: str = "", source: str = "web"):
    """写审计日志 + 同步写事件溯源快照（Kernel 第二阶段）。

    快照记录完整的工具入参、审批状态、执行结果摘要，供 /api/agent/replay 重放使用。
    快照写入失败不影响主流程（内部 try/catch，仅 warning 日志）。

    source：调用通道（web = 前端/HTTP 直调，mcp = MCP Server）。非 web 时在 detail 前缀标注
    "source=xxx"：不改变 agent_tool_log 表结构、不影响 GET /api/agent/log 契约，审计表格与
    时间轴都能直接看到来源（既有调用方不传该参数 → 行为与之前完全一致）。
    """
    detail_text = str(detail or "")
    if source and source != "web":
        detail_text = ("source=%s | %s" % (source, detail_text)).rstrip(" |")
    log_id = 0
    try:
        log_id = crud.append_agent_log(name, _summarize_args(name, args), approved, status, ms, detail_text)
    except Exception as e:  # noqa: BLE001 - 审计失败不影响工具结果
        _logger.warning("agent audit write failed: %s", e)
    # 同步写事件溯源快照（仅对真实工具调用写快照，配置类操作不写）
    if log_id and name in TOOL_IMPLS:
        try:
            snapshot = {
                "tool": name,
                "args": args,
                "approved": bool(approved),
                "status": status,
                "ms": int(ms or 0),
                "detail": detail_text[:1000],
                "timestamp": _TS_snapshot(),
                "workspace": str(_workspace_root(create=False)),
                "schema_version": 1,
            }
            crud.append_event_snapshot("tool_call", f"{name}:{log_id}", log_id, snapshot)
        except Exception as e:  # noqa: BLE001 - 快照失败绝不影响工具执行
            _logger.warning("event snapshot write failed: %s", e)


def _TS_snapshot() -> str:
    """快照用时间戳（ISO 格式）"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _audit_snapshot(event_type: str, entity_id: str, source_log_id: int | None, snapshot: dict):
    """事件溯源快照写入钩子（预埋，当前不自动调用；未来回放系统启用后，
    在关键事件点（工具调用完成、会话创建、模型切换等）调用此函数记录状态快照）。
    失败不影响主流程（内部 try/catch，仅 warning 日志）。"""
    try:
        crud.append_event_snapshot(event_type, entity_id, source_log_id, snapshot)
    except Exception as e:  # noqa: BLE001 - 快照失败绝不影响工具执行
        _logger.warning("event snapshot write failed: %s", e)


def _err(status_code: int, code: str, message: str):
    return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "message": message})


# ---------------------------------------------------------------- 四个工具实现

def _rel(ws: Path, target: Path) -> str:
    """工作区内路径的展示形式（相对正斜杠）"""
    try:
        return str(target.relative_to(ws)).replace(os.sep, "/")
    except ValueError:
        return str(target)


def _read_bytes(path: Path, max_bytes: int):
    """读文件字节；截断读取由调用方计算（此处按 max_bytes 截断读取）。返回 (data, truncated)"""
    size = path.stat().st_size
    with open(path, "rb") as f:
        data = f.read(min(size, max_bytes))
    return data, size > max_bytes


def _tool_list_directory(args: dict):
    raw = (args.get("path") or "").strip()
    ws = _workspace_root()
    target = ws if not raw or raw in (".", "./") else _safe_path(raw)
    if not target.exists():
        raise ToolError(f"目录不存在：{_rel(ws, target)}")
    if not target.is_dir():
        raise ToolError(f"不是目录：{_rel(ws, target)}")
    entries = []
    truncated = False
    try:
        for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if len(entries) >= MAX_LIST_ITEMS:
                truncated = True
                break
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": st.st_size if child.is_file() else 0,
                    "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
                })
            except OSError:
                entries.append({"name": child.name, "type": "file", "size": 0, "mtime": ""})
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"列目录失败：{e}") from e
    return {
        "path": _rel(ws, target),
        "count": len(entries),
        "truncated": truncated,
        "note": f"条目超过 {MAX_LIST_ITEMS} 条，已截断" if truncated else "",
        "items": entries,
    }


def _tool_read_file(args: dict):
    ws = _workspace_root()
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise ToolError("缺少 path 参数（相对工作区的文件路径）")
    target = _safe_path(raw)
    if not target.exists():
        raise ToolError(f"文件不存在：{_rel(ws, target)}")
    if not target.is_file():
        raise ToolError(f"不是文件：{_rel(ws, target)}")
    try:
        max_bytes = int(args.get("maxBytes") or DEFAULT_MAX_BYTES)
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = min(max(1024, max_bytes), MAX_READ_BYTES)
    try:
        data, truncated = _read_bytes(target, max_bytes)
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"读取失败：{e}") from e
    # 二进制探测：前 8KB 含 NUL 判定为二进制，返回明确错误而非乱码
    if b"\x00" in data[:8192]:
        raise ToolError(f"「{_rel(ws, target)}」是二进制文件或含 NUL 数据，不支持文本读取", code="binary_file")
    # UTF-8 严格解码；截断边界若正好切在多字节序列中，允许丢弃尾部不完整序列（用增量解码器）
    dec = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        text = dec.decode(data)
    except UnicodeDecodeError as e:
        raise ToolError(f"「{_rel(ws, target)}」不是 UTF-8 文本（解码失败：{e}），拒绝返回乱码", code="not_utf8") from None
    try:
        tail = dec.decode(b"", final=True)  # 截断边界不完整多字节会在此报错
    except UnicodeDecodeError:
        tail = ""  # 仅因截断导致：丢弃尾部不完整字节，truncated 已为 True
    text += tail
    # 行数上限：截断后仍超过 MAX_LINES 行则按行再截
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        text = "\n".join(lines[:MAX_LINES])
        truncated = True
    return {
        "path": _rel(ws, target),
        "content": text,
        "truncated": truncated,
        "bytes": len(data),
        "note": ("内容超过限制已截断" if truncated else ""),
    }


def _tool_write_file(args: dict):
    ws = _workspace_root()
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise ToolError("缺少 path 参数（相对工作区的目标文件路径）")
    target = _safe_path(raw)
    if target.suffix.lower() in DANGEROUS_EXTS:
        raise SafeError(f"禁止写入危险/可执行后缀文件：{target.suffix}（{_rel(ws, target)}）")
    content = args.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            content = str(content)
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ToolError(f"写入内容超过 {MAX_WRITE_BYTES // 1048576}MB 上限，已拒绝")
    overwrite = args.get("overwrite", True)
    if overwrite is False and target.exists():
        raise ToolError(f"文件已存在且 overwrite=false，已拒绝覆盖：{_rel(ws, target)}")
    try:
        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"写入失败：{e}") from e
    return {"path": _rel(ws, target), "bytes": len(content.encode("utf-8")), "written": True}


def _tool_create_file(args: dict):
    """create_file(path, content?)：只新建、绝不覆盖（等价 write_file + overwrite=false 强制）"""
    ws = _workspace_root()
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise ToolError("缺少 path 参数（相对工作区的新建文件路径）")
    target = _safe_path(raw)
    if target.suffix.lower() in DANGEROUS_EXTS:
        raise SafeError(f"禁止创建危险/可执行后缀文件：{target.suffix}（{_rel(ws, target)}）")
    if target.exists():
        # 安全边界：本工具只负责"新建"，已存在一律拒绝（覆盖请显式用 write_file 并单独授权）
        raise ToolError(f"文件已存在，create_file 不覆盖：{_rel(ws, target)}（如需覆盖请使用写文件工具）")
    content = args.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            content = str(content)
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ToolError(f"内容超过 {MAX_WRITE_BYTES // 1048576}MB 上限，已拒绝")
    try:
        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "x", encoding="utf-8", newline="") as f:  # 'x' = 独占创建，双保险不覆盖
            f.write(content)
    except FileExistsError:
        raise ToolError(f"文件已存在，create_file 不覆盖：{_rel(ws, target)}") from None
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"创建失败：{e}") from e
    return {"path": _rel(ws, target), "bytes": len(content.encode("utf-8")), "created": True}


def _tool_move_file(args: dict):
    """move_file(from, to)：移动/重命名（同一实现，前端两个工具名共用）。
    安全边界：源必须存在且在工作区内；目标必须在工作区内、父目录自动创建、
    目标已存在一律拒绝（不做静默覆盖），也不允许危险后缀落盘。"""
    ws = _workspace_root()
    raw_src = str(args.get("from") or args.get("src") or "").strip()
    raw_dst = str(args.get("to") or args.get("dst") or "").strip()
    if not raw_src or not raw_dst:
        raise ToolError("缺少 from / to 参数（相对工作区的源路径与目标路径）")
    src = _safe_path(raw_src, allow_missing=False)
    dst = _safe_path(raw_dst)
    if not src.is_file() and not src.is_dir():
        raise ToolError(f"源路径不是文件或目录：{raw_src}")
    if src == dst:
        raise ToolError("源与目标是同一路径，无需移动")
    if dst.suffix.lower() in DANGEROUS_EXTS:
        raise SafeError(f"禁止移动为危险/可执行后缀：{dst.suffix}")
    if dst.exists():
        raise ToolError(f"目标已存在，拒绝覆盖：{_rel(ws, dst)}")
    # 目录移动到自身子目录 → 会形成递归，必须拒绝
    try:
        if src.is_dir() and os.path.normcase(str(dst.resolve(strict=False))).startswith(
                os.path.normcase(str(src)) + os.sep):
            raise ToolError("不允许把目录移动到它自己的子目录内")
    except OSError:
        pass
    try:
        if not dst.parent.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"移动失败：{e}") from e
    return {"from": _rel(ws, src), "to": _rel(ws, dst), "moved": True, "kind": "dir" if dst.is_dir() else "file"}


def _proc_mem_mb_windows():
    """Windows 进程内存（ctypes，无第三方依赖）：pid -> 工作集 MB；失败返回空表"""
    out = {}
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        # 枚举进程 pid（EnumProcesses），避免解析 tasklist 文本
        arr = (wintypes.DWORD * 2048)()
        need = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(need)):
            return out
        n = need.value // ctypes.sizeof(wintypes.DWORD)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        for i in range(min(n, 2048)):
            pid = int(arr[i])
            if pid <= 0:
                continue
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                continue
            try:
                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                    out[pid] = round(pmc.WorkingSetSize / 1048576.0, 1)
            finally:
                k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 - 平台差异一律降级为空表
        return {}
    return out


def _tool_list_processes(args: dict):
    """list_processes(limit?, sort?)：只读进程列表（占内存前 N）。
    无第三方依赖：Windows 走 psapi EnumProcesses+GetProcessMemoryInfo（ctypes），
    Linux 走 /proc/<pid>/statm；两者都拿不到时返回可读的降级说明而不是报错。"""
    limit = max(1, min(200, int(args.get("limit") or 30)))
    sort_by = str(args.get("sort") or "memory").lower()
    rows = []
    mem = {}
    names = {}
    if os.name == "nt":
        mem = _proc_mem_mb_windows()
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            for pid in list(mem.keys()):
                h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not h:
                    continue
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wintypes.DWORD(len(buf))
                    if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        names[pid] = os.path.basename(buf.value)
                finally:
                    k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            names = {}
    else:
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                try:
                    with open(f"/proc/{pid}/statm", "r", encoding="utf-8") as f:
                        pages = int(f.read().split()[1])
                    mem[pid] = round(pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0, 1)
                    with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as f:
                        names[pid] = f.read().strip()
                except (OSError, ValueError):
                    continue
        except OSError:
            mem, names = {}, {}
    for pid, mb in mem.items():
        rows.append({"pid": pid, "name": names.get(pid, ""), "memMB": mb})
    if sort_by == "memory":
        rows.sort(key=lambda r: r["memMB"], reverse=True)
    else:
        rows.sort(key=lambda r: r["pid"])
    return {
        "count": len(rows),
        "limit": limit,
        "sort": sort_by,
        "items": rows[:limit],
        "note": "" if rows else "当前平台无法读取进程内存信息（未安装 psutil 时降级为空列表，不影响其它工具）",
    }


def _tool_system_info(args: dict):
    """system_info()：CPU / 内存 / 磁盘 / 运行时的只读概览（不采集任何用户隐私数据）"""
    import platform

    disk = {}
    try:
        usage = shutil.disk_usage(str(_workspace_root()))
        disk = {"totalGB": round(usage.total / 1073741824.0, 1),
                "usedGB": round(usage.used / 1073741824.0, 1),
                "freeGB": round(usage.free / 1073741824.0, 1),
                "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0}
    except OSError:
        disk = {}
    mem = {}
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            mem = {"totalGB": round(m.ullTotalPhys / 1073741824.0, 1),
                   "availGB": round(m.ullAvailPhys / 1073741824.0, 1),
                   "percent": int(m.dwMemoryLoad)}
    except Exception:  # noqa: BLE001 - 非 Windows / 调用失败一律降级
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            psize = os.sysconf("SC_PAGE_SIZE")
            mem = {"totalGB": round(pages * psize / 1073741824.0, 1)}
        except (ValueError, OSError, AttributeError):
            mem = {}
    return {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpuCount": os.cpu_count(),
        "cpuLoad1m": (lambda: (round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None))(),
        "memory": mem,
        "disk": disk,
        "workspace": str(_workspace_root()),
        "note": "只读概览：不含任何用户名/环境变量/文件内容",
    }


def _decode_output(data: bytes) -> str:
    """命令输出解码：优先 UTF-8 严格；失败（GBK 代码页等）回退 gbk + replace"""
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk", errors="replace")
        except Exception:  # noqa: BLE001
            return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- M2 三个高价值工具

def _iter_workspace_files(root: Path, rel_dir: str, limit: int):
    """递归枚举工作区内真实文件（跳过符号链接/junction，不跟随逃逸），yield (abs_path, rel_path)。

    手动 scandir 而非 rglob：显式跳过 symlink/junction 目录，防止链接逃逸进入遍历。
    """
    base = root if not rel_dir or rel_dir == "." else root / rel_dir
    stack = [base]
    scanned = 0
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue  # 链接不进结果也不深入
                if entry.is_dir(follow_symlinks=False):
                    # junction/挂载点在 Windows 上 is_symlink() 可能为 False，再核对解析位置仍在工作区
                    try:
                        real = Path(entry.path).resolve()
                        if os.path.normcase(str(real)) != os.path.normcase(str(Path(entry.path))) and \
                           os.path.commonpath([os.path.normcase(str(root)), os.path.normcase(str(real))]) != os.path.normcase(str(root)):
                            continue
                    except (OSError, ValueError):
                        continue
                    stack.append(Path(entry.path))
                    continue
                if entry.is_file(follow_symlinks=False):
                    scanned += 1
                    if scanned > limit:
                        return
                    yield Path(entry.path)
            except OSError:
                continue


def _tool_find_files(args: dict):
    """find_files(path?, pattern?)：工作区内按文件名通配递归查找（只读，限 1000 条）"""
    ws = _workspace_root()
    raw_dir = str(args.get("path") or "").strip()
    pattern = str(args.get("pattern") or "*").strip() or "*"
    if len(pattern) > 200:
        raise ToolError("pattern 过长（>200 字符）")
    target_dir = "." if not raw_dir or raw_dir in (".", "./") else raw_dir
    # path 本身也要过安全校验（目录存在性在遍历时体现）
    dir_abs = ws if target_dir == "." else _safe_path(target_dir)
    if not dir_abs.is_dir():
        raise ToolError(f"目录不存在：{target_dir}")

    import fnmatch
    matched = []
    truncated = False
    scanned = 0
    for f in _iter_workspace_files(ws, target_dir, SEARCH_SCAN_LIMIT * 2):
        scanned += 1
        rel = _rel(ws, f)
        if dir_abs != ws and not os.path.normcase(str(f)).startswith(os.path.normcase(str(dir_abs) + os.sep)):
            if os.path.normcase(str(f)) != os.path.normcase(str(dir_abs)):
                continue
        if fnmatch.fnmatch(f.name.lower(), pattern.lower()):
            if len(matched) >= FIND_LIMIT:
                truncated = True
                break
            try:
                st = f.stat()
                mtime = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
                size = st.st_size
            except OSError:
                size, mtime = 0, ""
            matched.append({"path": rel, "size": size, "mtime": mtime})
    return {
        "pattern": pattern,
        "path": target_dir,
        "count": len(matched),
        "truncated": truncated,
        "scanned": scanned,
        "items": matched,
        "note": f"结果超过 {FIND_LIMIT} 条已截断" if truncated else "",
    }


def _tool_search_text(args: dict):
    """search_text(query, path?, glob?, regex?)：工作区内全文检索（只读，默认 200 条命中）。

    - 二进制（含 NUL）/非 UTF-8 文件跳过并计数（不中断检索，返回 skipped 计数）；
    - regex=true 时 query 为正则，非法正则明确报错；
    - 单行截断 SEARCH_LINE_CHARS 字符。
    """
    import fnmatch
    import re as _re

    ws = _workspace_root()
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("缺少 query 参数（要搜索的文本或正则）")
    raw_dir = str(args.get("path") or "").strip()
    glob_pat = str(args.get("glob") or "").strip()
    use_regex = args.get("regex") is True

    if use_regex:
        try:
            rx = _re.compile(query)
        except _re.error as e:  # noqa: BLE001
            raise ToolError(f"正则表达式非法：{e}（query={query[:80]}）") from None
        matcher = lambda s: rx.search(s)  # noqa: E731
    else:
        needle = query.lower()
        matcher = lambda s: needle in s.lower()  # noqa: E731

    target_dir = "." if not raw_dir or raw_dir in (".", "./") else raw_dir
    dir_abs = ws if target_dir == "." else _safe_path(target_dir)
    if not dir_abs.is_dir():
        raise ToolError(f"目录不存在：{target_dir}")

    hits = []
    skipped_binary = 0
    scanned = 0
    truncated = False
    for f in _iter_workspace_files(ws, target_dir, SEARCH_SCAN_LIMIT):
        scanned += 1
        rel = _rel(ws, f)
        if dir_abs != ws and os.path.normcase(str(f)) != os.path.normcase(str(dir_abs)) and \
           not os.path.normcase(str(f)).startswith(os.path.normcase(str(dir_abs) + os.sep)):
            continue
        if glob_pat and not fnmatch.fnmatch(f.name.lower(), glob_pat.lower()):
            continue
        try:
            if f.stat().st_size > MAX_READ_BYTES:
                data = f.read_bytes()[:MAX_READ_BYTES]
            else:
                data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            skipped_binary += 1
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            skipped_binary += 1  # 非 UTF-8 文本同样跳过计数
            continue
        file_hits = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if matcher(line):
                file_hits.append({"file": rel, "line": lineno, "text": line[:SEARCH_LINE_CHARS]})
                if len(hits) + len(file_hits) >= SEARCH_LIMIT:
                    break
        hits.extend(file_hits)
        if len(hits) >= SEARCH_LIMIT:
            hits = hits[:SEARCH_LIMIT]
            truncated = True
            break
    return {
        "query": query,
        "regex": use_regex,
        "glob": glob_pat or None,
        "count": len(hits),
        "truncated": truncated,
        "scanned_files": scanned,
        "skipped_binary": skipped_binary,
        "items": hits,
        "note": f"命中超过 {SEARCH_LIMIT} 条已截断" if truncated else "",
    }


def _tool_edit_file(args: dict):
    """edit_file(path, old_str, new_str, replace_all?)：精确字符串替换（副作用，需审批）。

    - old_str 必须在文件中唯一匹配（除非 replace_all=true），0 个或多个匹配都报错并提示补充上下文；
    - 返回替换次数与变更前后片段（各 EDIT_SNIPPET_CHARS 上下文宽度）；
    - 危险后缀/二进制/非 UTF-8 明确拒绝。
    """
    ws = _workspace_root()
    raw = str(args.get("path") or "").strip()
    if not raw:
        raise ToolError("缺少 path 参数")
    old_str = args.get("old_str")
    new_str = args.get("new_str")
    if not isinstance(old_str, str) or old_str == "":
        raise ToolError("old_str 必须是非空字符串（要被替换的精确文本）")
    if not isinstance(new_str, str):
        raise ToolError("new_str 必须是字符串（替换后的文本，可为空串表示删除）")
    replace_all = args.get("replace_all") is True

    target = _safe_path(raw)
    if target.suffix.lower() in DANGEROUS_EXTS:
        raise SafeError(f"禁止编辑危险/可执行后缀文件：{target.suffix}（{_rel(ws, target)}）")
    if not target.exists():
        raise ToolError(f"文件不存在：{_rel(ws, target)}（新建文件请用 write_file）")
    if not target.is_file():
        raise ToolError(f"不是文件：{_rel(ws, target)}")
    try:
        content = target.read_bytes()
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"读取失败：{e}") from e
    if b"\x00" in content[:8192]:
        raise ToolError(f"「{_rel(ws, target)}」是二进制文件（含 NUL），edit_file 仅支持文本文件", code="binary_file")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ToolError(f"「{_rel(ws, target)}」不是 UTF-8 文本，edit_file 拒绝编辑（避免编码损坏）", code="not_utf8") from None

    occurrences = text.count(old_str)
    if occurrences == 0:
        raise ToolError(
            f"old_str 在文件中未找到（0 处匹配）。请先用 read_file 核对实际内容（注意空白与换行），"
            f"或提供更长的上下文片段"
        )
    if occurrences > 1 and not replace_all:
        raise ToolError(
            f"old_str 在文件中匹配 {occurrences} 处，不唯一。请提供更长的上下文使匹配唯一，"
            f"或指定 replace_all=true 全部替换"
        )
    count = occurrences if replace_all else 1
    idx = text.find(old_str)
    ctx_lo = max(0, idx - EDIT_SNIPPET_CHARS)
    ctx_hi = min(len(text), idx + len(old_str) + EDIT_SNIPPET_CHARS)
    before_snippet = text[ctx_lo:ctx_hi]
    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    after_idx = new_text.find(new_str[:64]) if new_str else idx
    after_lo = max(0, after_idx - EDIT_SNIPPET_CHARS)
    after_hi = min(len(new_text), after_idx + len(new_str) + EDIT_SNIPPET_CHARS)
    after_snippet = new_text[after_lo:after_hi]
    try:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"写入失败：{e}") from e
    return {
        "path": _rel(ws, target),
        "replacements": count,
        "before_snippet": before_snippet,
        "after_snippet": after_snippet,
        "bytes": len(new_text.encode("utf-8")),
        "edited": True,
    }


def _tool_run_command(args: dict):
    ws = _workspace_root()
    command = str(args.get("command") or "").strip()
    raw_args = args.get("args")
    if raw_args is None:
        cmd_args = []
    elif isinstance(raw_args, list):
        cmd_args = [str(a) for a in raw_args]
    else:
        cmd_args = [str(raw_args)]
    # 1) 命令名白名单
    checker = CMD_WHITELIST.get(command)
    if checker is None:
        raise SafeError(
            f"命令「{command}」不在只读白名单（允许：git status/log/diff/branch、python --version、"
            "node --version、npm --version、where <命令名>），已拒绝"
        )
    # 2) 显式拒绝 shell 元字符（纵深防御；token 正则已排除绝大部分）
    for a in cmd_args:
        if any(ch in a for ch in '&|;<>^`"\'()%$!*\r\n\t '):
            raise SafeError(f"参数含 shell 元字符/空白，已拒绝：{a[:40]}")
    ok, why = checker(cmd_args)
    if not ok:
        raise SafeError(f"命令参数被拒绝：{why}")
    # 3) 解析真实可执行文件（防 PATH 注入歧义；npm 在 Windows 为 npm.cmd，CreateProcess 可直接执行）
    exe = shutil.which(command)
    if not exe:
        raise SafeError(f"在系统中找不到可执行文件：{command}")
    # 4) shell=False + 列表参数执行；cwd 锁定工作区；15s 超时
    full = [exe] + cmd_args
    try:
        t0 = time.perf_counter()
        proc = subprocess.run(
            full,
            capture_output=True,
            timeout=CMD_TIMEOUT_SECONDS,
            shell=False,
            cwd=str(ws),
            check=False,
        )
        ms = int((time.perf_counter() - t0) * 1000)
    except subprocess.TimeoutExpired as e:
        raise ToolError(
            f"命令执行超过 {CMD_TIMEOUT_SECONDS}s 超时，已终止（输出截断）："
            + (_decode_output((e.stdout or b""))[:200] if e.stdout else ""),
            code="timeout",
        ) from None
    except OSError as e:  # noqa: BLE001
        raise ToolError(f"命令无法启动：{e}") from e
    return {
        "command": command,
        "args": cmd_args,
        "stdout": _decode_output(proc.stdout)[:OUTPUT_LIMIT],
        "stderr": _decode_output(proc.stderr)[:OUTPUT_LIMIT],
        "exitCode": proc.returncode,
        "ms": ms,
    }


TOOL_IMPLS = {
    "list_directory": _tool_list_directory,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "run_command": _tool_run_command,
    "find_files": _tool_find_files,
    "search_text": _tool_search_text,
    "edit_file": _tool_edit_file,
    "create_file": _tool_create_file,
    "move_file": _tool_move_file,
    "list_processes": _tool_list_processes,
    "system_info": _tool_system_info,
}


def _human_summary(name: str, args: dict) -> dict:
    """needsApproval 响应中的可读摘要（前端审批卡同样自渲染参数，此字段供 API 直调方/审计展示）"""
    ws = _workspace_root()
    if name == "create_file":
        path = str(args.get("path") or "").strip()
        content = str(args.get("content") or "")
        return {
            "tool": name,
            "title": f"新建文件 {path}",
            "detail": f"将在工作区内新建文件 {path}（{len(content.encode('utf-8'))} 字节）；已存在则拒绝，不会覆盖任何现有文件",
            "preview": content[:200] + ("…" if len(content) > 200 else ""),
        }
    if name == "move_file":
        src = str(args.get("from") or args.get("src") or "").strip()
        dst = str(args.get("to") or args.get("dst") or "").strip()
        return {
            "tool": name,
            "title": f"移动/重命名 {src} → {dst}",
            "detail": f"将工作区内 {src} 移动或重命名为 {dst}；目标已存在会拒绝，不做静默覆盖",
        }
    if name == "write_file":
        path = str(args.get("path") or "").strip()
        content = str(args.get("content") or "")
        preview = content[:200] + ("…" if len(content) > 200 else "")
        overwrite = "覆盖" if args.get("overwrite", True) is not False else "仅新建（不覆盖）"
        return {
            "tool": name,
            "title": f"写文件 {path}",
            "detail": f"将{'创建' if overwrite == '仅新建（不覆盖）' else '写入/覆盖'}工作区内文件 {path}（{len(content.encode('utf-8'))} 字节，{overwrite}）",
            "preview": preview,
        }
    if name == "run_command":
        cmd = str(args.get("command") or "").strip()
        a = args.get("args")
        if isinstance(a, list):
            cmd += " " + " ".join(str(x) for x in a)
        return {"tool": name, "title": f"执行命令 {cmd[:120]}", "detail": f"将在工作区 {ws} 内执行只读白名单命令：{cmd}"}
    if name == "edit_file":
        path = str(args.get("path") or "").strip()
        old = str(args.get("old_str") or "")
        new = str(args.get("new_str") or "")
        mode = "全部替换" if args.get("replace_all") is True else "唯一匹配替换"
        return {
            "tool": name,
            "title": f"编辑文件 {path}",
            "detail": f"精确替换 {path} 中的文本（{len(old.encode('utf-8'))} 字节 → {len(new.encode('utf-8'))} 字节，{mode}）",
            "preview": old[:200] + ("…" if len(old) > 200 else ""),
        }
    return {"tool": name, "title": name, "detail": json.dumps(args, ensure_ascii=False)[:300]}


# ---------------------------------------------------------------- 统一执行入口

def _execute_tool_internal(name: str, args: dict, approved: bool = False,
                           decision: str = "", source: str = "web") -> tuple[dict, int]:
    """执行一个本机工具 —— 唯一执行入口（/api/agent/tool 与 MCP Server 共用）。

    安全边界完全沿用既有实现，本函数不做任何放宽：
      - 工具查找：仅 TOOL_IMPLS 里的 11 个本机工具，其它名字一律拒绝；
      - 副作用双保险：SIDE_EFFECT_TOOLS 且 approved is not True → 绝不执行（needs_approval）；
      - 路径/后缀/命令白名单等防护全部在各自 _tool_* 实现内经 _safe_path 与 SafeError 生效，
        本函数既不绕过也不复制这些校验；
      - 每一次调用（含客户端拒绝、未审批、安全拒绝、执行失败）都写审计与事件快照；
        source != "web" 时在 detail 前缀标注来源（MCP 调用落 "source=mcp"）。
    返回 (payload, http_status)：未知工具 → 400；其余一律 200（与既有路由行为一致）。
    """
    if not isinstance(args, dict):
        args = {}
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"ok": False, "code": "unknown_tool",
                "message": f"未知本机工具：{name}（可用：{', '.join(sorted(TOOL_IMPLS))}）"}, 400
    is_side_effect = name in SIDE_EFFECT_TOOLS
    # 客户端拒绝上报：只审计，绝不执行任何工具（即使是只读的也按上报处理）
    if str(decision or "") == "denied":
        _audit(name, args, False, "denied", 0, "客户端明确拒绝", source=source)
        return {"ok": True, "data": {"denied": True}}, 200
    # 副作用双保险：未带 approved:true 一律不执行
    if is_side_effect and approved is not True:
        _audit(name, args, False, "needs_approval", 0, "副作用工具未获审批，未执行", source=source)
        return {"ok": True, "needsApproval": True, "summary": _human_summary(name, args)}, 200
    t0 = time.perf_counter()
    try:
        data = impl(args)
        ms = int((time.perf_counter() - t0) * 1000)
        _audit(name, args, True, "ok", ms, source=source)
        return {"ok": True, "data": data}, 200
    except SafeError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        _audit(name, args, approved, "rejected", ms, str(e)[:200], source=source)
        return {"ok": False, "code": e.code, "message": str(e)}, 200
    except ToolError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        _audit(name, args, approved, "error", ms, str(e)[:200], source=source)
        return {"ok": False, "code": e.code, "message": str(e)}, 200
    except Exception as e:  # noqa: BLE001 - 兜底：内部错误不泄漏堆栈
        _logger.exception("agent tool %s crashed", name)
        ms = int((time.perf_counter() - t0) * 1000)
        _audit(name, args, approved, "error", ms, "internal: " + type(e).__name__, source=source)
        return {"ok": False, "code": "internal_error",
                "message": f"工具执行内部错误：{type(e).__name__}"}, 200


# ---------------------------------------------------------------- 路由

@router.post("/api/agent/tool")
async def agent_tool_call(request: Request):
    """执行一个本机工具（HTTP 外观层，行为与抽取前完全一致）。

    body: {name, args, approved?, decision?}
    - 只读工具（list_directory/read_file）：approved 无关，直接执行；
    - 副作用工具（write_file/run_command）：approved !== true → {needsApproval:true, summary} 绝不执行；
    - decision='denied'：客户端拒绝上报，仅写审计（status=denied），绝不执行。

    真正的执行/审批/审计在 _execute_tool_internal —— 与 MCP Server 共用同一入口，
    不存在第二条执行路径（避免"两套实现安全策略漂移"）。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    payload, status = _execute_tool_internal(
        str(body.get("name") or "").strip(),
        body.get("args"),
        approved=body.get("approved") is True,
        decision=str(body.get("decision") or ""),
        source="web",
    )
    if status != 200:
        return _err(status, str(payload.get("code") or "error"), str(payload.get("message") or ""))
    return payload


@router.get("/api/agent/config")
def agent_config():
    """当前本机 Agent 配置（工作区根/版本/副作用工具清单），供前端展示与探测"""
    try:
        ws = _workspace_root(create=True)
        writable = os.access(str(ws), os.W_OK) if ws.exists() else False
        return {
            "ok": True,
            "data": {
                "workspace": str(ws),
                "workspaceExists": ws.exists(),
                "writable": bool(writable),
                "fromEnv": bool(os.environ.get("MORAY_WORKSPACE", "").strip()),
                "version": config.VERSION,
                "build": config.BUILD,
                "sideEffectTools": sorted(SIDE_EFFECT_TOOLS),
                "approvalPolicy": "readonly_auto",  # 只读自动执行；前端设置决定是否把只读也纳入人工审批
            },
        }
    except ToolError as e:
        return {"ok": False, "code": e.code, "message": str(e)}


@router.post("/api/agent/config")
async def agent_config_set(request: Request):
    """修改工作区根（设置页）：校验目录可创建/可写后持久化到 kv(agent_workspace)。只读回 config"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")
    raw = str(body.get("workspace") or "").strip()
    if not raw:
        return _err(400, "bad_request", "缺少 workspace 字段")
    try:
        ws = Path(raw).expanduser().resolve()
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".moray_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:  # noqa: BLE001
        return _err(400, "not_writable", f"目录不可写或无法创建：{e}")
    try:
        crud.kv_put("agent_workspace", str(ws))
    except Exception as e:  # noqa: BLE001
        return _err(500, "db_error", f"保存失败：{e}")
    _audit("config_set_workspace", {"workspace": str(ws)}, True, "ok", 0)
    return {"ok": True, "data": {"workspace": str(ws), "workspaceExists": True, "writable": True}}


@router.get("/api/agent/log")
def agent_log(limit: int = 50, offset: int = 0):
    """审计日志分页（新→旧）"""
    try:
        rows, total = crud.list_agent_log(limit, offset)
    except Exception as e:  # noqa: BLE001
        return _err(500, "db_error", f"读取审计失败：{e}")
    return {"ok": True, "data": {"rows": rows, "total": total, "limit": min(max(int(limit or 50), 1), 500), "offset": max(int(offset or 0), 0)}}


@router.delete("/api/agent/log")
def agent_log_clear():
    """清空审计日志（设置页手动清空，前端二次确认后调用）"""
    try:
        n = crud.clear_agent_log()
        _audit("config_clear_audit", {}, True, "ok", 0, f"cleared {n}")
        return {"ok": True, "data": {"cleared": n}}
    except Exception as e:  # noqa: BLE001
        return _err(500, "db_error", f"清空失败：{e}")


# ---------------------------------------------------------------- Kernel 第二阶段：事件溯源重放

@router.post("/api/agent/replay")
async def agent_replay(request: Request):
    """重放一次历史工具调用（Kernel 第二阶段：Event Sourcing 确定性回放）。

    body: {snapshot_id, modified_args?, approved?}
    - 从 event_snapshots 表读取原始快照（工具名、原始参数）
    - 如果提供 modified_args，则用修改后的参数重放（允许用户调整参数后重试）
    - 重放强制经过原有安全校验（_safe_path 路径防护、SIDE_EFFECT_TOOLS 审批逻辑）
    - 副作用工具必须带 approved:true 才会执行，否则返回 needsApproval
    - 重放结果写入新的审计日志和事件快照（source_log_id 指向原始日志）
    - 返回执行结果 + 重放元信息（原始快照ID、新日志ID、新快照ID）
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _err(400, "bad_json", "请求体不是合法 JSON")

    snapshot_id = body.get("snapshot_id")
    if snapshot_id is None:
        return _err(400, "bad_request", "缺少 snapshot_id 字段（要重放的事件快照 ID）")

    # 1. 读取原始快照
    try:
        snap = crud.get_event_snapshot(int(snapshot_id))
    except Exception as e:  # noqa: BLE001
        return _err(500, "db_error", f"读取快照失败：{e}")

    if not snap:
        return _err(404, "snapshot_not_found", f"快照 ID {snapshot_id} 不存在")

    snapshot_data = snap.get("snapshot") or {}
    tool_name = snapshot_data.get("tool", "")
    original_args = snapshot_data.get("args", {})

    if not tool_name or tool_name not in TOOL_IMPLS:
        return _err(400, "invalid_snapshot", f"快照中的工具名无效：{tool_name}")

    # 2. 确定重放参数（modified_args 覆盖原始参数）
    modified_args = body.get("modified_args")
    if isinstance(modified_args, dict) and modified_args:
        replay_args = dict(original_args)
        replay_args.update(modified_args)
    else:
        replay_args = dict(original_args)

    # 3. 安全校验：副作用工具需要 approved
    is_side_effect = tool_name in SIDE_EFFECT_TOOLS
    approved = body.get("approved") is True

    if is_side_effect and not approved:
        # 副作用工具未审批：不执行，返回 needsApproval（与 /api/agent/tool 行为一致）
        _audit(tool_name, replay_args, False, "needs_approval", 0, f"重放未获审批（原始快照 {snapshot_id}）")
        return {
            "ok": True,
            "needsApproval": True,
            "summary": _human_summary(tool_name, replay_args),
            "replay": {"original_snapshot_id": int(snapshot_id), "tool": tool_name, "status": "needs_approval"},
        }

    # 4. 执行重放（复用原有工具实现，自动经过 _safe_path 等安全校验）
    impl = TOOL_IMPLS[tool_name]
    t0 = time.perf_counter()
    try:
        data = impl(replay_args)
        ms = int((time.perf_counter() - t0) * 1000)
        status = "ok"
        error_code = None
        error_message = None
    except SafeError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        status = "rejected"
        error_code = e.code
        error_message = str(e)
        data = None
    except ToolError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        status = "error"
        error_code = e.code
        error_message = str(e)
        data = None
    except Exception as e:  # noqa: BLE001
        _logger.exception("agent replay %s crashed", tool_name)
        ms = int((time.perf_counter() - t0) * 1000)
        status = "error"
        error_code = "internal_error"
        error_message = f"重放内部错误：{type(e).__name__}"
        data = None

    # 5. 写审计日志（_audit 会自动写事件快照，source_log_id 指向新日志）
    detail = f"重放自快照 {snapshot_id}"
    if error_message:
        detail += f"：{error_message[:200]}"
    _audit(tool_name, replay_args, approved, status, ms, detail)

    # 6. 返回结果
    result = {
        "ok": status == "ok",
        "replay": {
            "original_snapshot_id": int(snapshot_id),
            "tool": tool_name,
            "status": status,
            "ms": ms,
            "args_used": replay_args,
        },
    }
    if data is not None:
        result["data"] = data
    if error_code:
        result["code"] = error_code
        result["message"] = error_message

    return result
