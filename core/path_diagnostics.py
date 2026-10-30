# -*- coding: utf-8 -*-
"""path_diagnostics — 文件读写前置诊断层（2026-09-30 任务三新增）。

零业务依赖：不 import file_proxy（避免循环导入），仅标准库。

背景：Windows 上 PermissionError Errno 13 的真实成因按概率排序：
  1. 路径指向已存在目录，却被当文件打开        → TargetIsDirectory
  2. 目标文件只读属性 / 被其他进程独占          → ReadOnlyFile / FileLocked
  3. Docker 卷映射后容器内 uid/gid 无写权限     → VolumePermissionDenied
     （被动异常类：file_proxy 全部写入走宿主 Python 直接落盘，不经 Docker 卷，
      暂无主动探测逻辑；真实 PermissionError(winerror=5) 兜底转入此类）
  4. 超 MAX_PATH(260) / 保留名 / 尾空格尾点     → PathTooLong / InvalidPathName
  5. ACL 拒绝（父目录继承 Deny 项）             → AclDenied
外加白名单逃逸 → PathEscapeError、父目录不可写 → ParentNotWritable。

原则：先诊断分类，再决定修复动作——诊断失败的修复是赌博。

Windows 实测注意（2026-09-30 预验证）：
  - os.access(dir, W_OK) 对 chmod 0o555 的目录仍返回 True（_waccess 在 Windows
    上对目录的探测能力有限），ParentNotWritable 主要覆盖"只读属性位被误设"与
    非 Windows 语义场景；真实 ACL 拒绝由 icacls Deny 探测（AclDenied）与写入期
    convert_oserror 兜底。
  - os.replace 到只读目标抛 PermissionError winerror=5 errno=13（Errno 13 成因 ② 的
    真身之一），由 convert_oserror 统一转换为 FileProxyError 家族并附诊断快照。
"""
import logging
import os
import re
import stat as _stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PATH = 260

# Windows 保留设备名（Win32 层，不分大小写、可带扩展名，如 CON.txt）
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

# 独占锁冲突的 winerror：ERROR_SHARING_VIOLATION=32 / ERROR_LOCK_VIOLATION=33
_LOCK_WINERRORS = (32, 33)

_ICACLS_DENY_RE = re.compile(r"\(\s*DENY", re.IGNORECASE)


# ==================== 异常家族（全部继承 FileProxyError） ====================
class FileProxyError(Exception):
    """file 代理层诊断异常基类。"""


class PathEscapeError(FileProxyError):
    """路径逃逸白名单。"""


class PathTooLong(FileProxyError):
    """路径超过 Windows MAX_PATH(260)。"""


class InvalidPathName(FileProxyError):
    """路径组件为 Windows 保留名，或以尾空格/尾点结尾。"""


class TargetIsDirectory(FileProxyError):
    """目标路径是已存在的目录，却被当文件打开。"""


class ReadOnlyFile(FileProxyError):
    """目标文件带只读属性。"""


class ParentNotWritable(FileProxyError):
    """父目录（或最近存在的祖先目录）不可写（os.access 探测）。"""


class AclDenied(FileProxyError):
    """icacls 探测到 Deny 项。"""


class FileLocked(FileProxyError):
    """文件被其他进程独占/锁定（ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION）。"""


class VolumePermissionDenied(FileProxyError):
    """卷级权限拒绝（Docker 卷映射 uid/gid / ACL 拒绝等；被动类，写入期兜底转入）。"""


class _Abort(Exception):
    """内部控制流：携带已收集的 checks 与待抛异常，供 diagnose / diagnose_report 拆包。"""

    def __init__(self, checks, exc):
        super().__init__(str(exc))
        self.checks = checks
        self.exc = exc


# ==================== 诊断入口 ====================
def diagnose(logical_path: str, mode: str, allowed_roots) -> Path:
    """诊断并返回规范化物理路径。失败时 raise 对应 FileProxyError 子类（message 含诊断信息）。

    步骤：
      1. 规范化：展开变量（~ / %VAR%）+ os.path.abspath + os.path.normpath
      2. 白名单校验：严格 os.sep 边界（防 D:\\a 误匹配 D:\\ab）+ normcase
      3. 长度检查：Windows MAX_PATH=260
      4. 保留名/尾空格/尾点检查（在 abspath 之前做静态字符检查——
         GetFullPathName 会剥离组件尾部空格与点，规范化后 "a. " / "a." 无法检出）
      5. 写模式权限探测（按序）：TargetIsDirectory → ReadOnlyFile
         → ParentNotWritable → AclDenied
      6. 返回规范化 Path
    """
    try:
        path, _checks = _diagnose_full(logical_path, mode, allowed_roots)
    except _Abort as abort:
        raise abort.exc from None
    return path


def diagnose_report(logical_path: str, mode: str, allowed_roots) -> dict:
    """非抛异常版本：返回完整诊断报告（供 /api/file/diagnose 类接口使用）。

    形状：{path, mode, ok, error_type, error_message, checks: [...]}
    """
    try:
        path, checks = _diagnose_full(logical_path, mode, allowed_roots)
        return {
            "path": str(path),
            "mode": mode,
            "ok": True,
            "error_type": None,
            "error_message": None,
            "checks": checks,
        }
    except _Abort as abort:
        exc = abort.exc
        return {
            "path": str(logical_path),
            "mode": mode,
            "ok": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "checks": abort.checks,
        }
    except Exception as exc:  # 诊断层自身异常（如非法 mode）也进报告，不外抛
        return {
            "path": str(logical_path),
            "mode": mode,
            "ok": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "checks": [],
        }


# ==================== 诊断主流程 ====================
def _diagnose_full(logical_path: str, mode: str, allowed_roots):
    if mode not in ("r", "w"):
        raise ValueError("mode 必须是 'r' 或 'w'，收到 %r" % (mode,))
    checks = []

    def fail(exc):
        raise _Abort(checks, exc)

    # ---- 步骤 1a：展开变量（~ 与 %VAR%）--------------------------------
    raw = str(logical_path)
    expanded = os.path.expandvars(os.path.expanduser(raw))
    checks.append({"step": "expand", "ok": True, "detail": expanded})

    # ---- 步骤 4 提前：保留名 / 尾空格尾点（静态字符检查）------------------
    # 必须在 abspath 之前：GetFullPathName 会剥离组件尾部空格与点，规范化后检不出。
    bad = _find_invalid_component(expanded)
    if bad:
        fail(InvalidPathName(
            "路径组件 %r 为 Windows 保留名或以尾空格/尾点结尾: path=%s" % (bad, expanded)))
    checks.append({"step": "reserved_name", "ok": True, "detail": "无保留名/尾空格/尾点"})

    # ---- 步骤 1b：规范化为绝对物理路径 ----------------------------------
    physical = os.path.normpath(os.path.abspath(expanded))
    checks.append({"step": "normalize", "ok": True, "detail": physical})

    # ---- 步骤 2：白名单校验（严格 os.sep 边界 + normcase 大小写归一）------
    norm = os.path.normcase(physical)
    matched_root = None
    for root in (allowed_roots or []):
        root_abs = os.path.normcase(os.path.normpath(
            os.path.abspath(os.path.expandvars(os.path.expanduser(str(root))))))
        if norm == root_abs or norm.startswith(root_abs + os.sep):
            matched_root = root_abs
            break
    if not matched_root:
        fail(PathEscapeError(
            "路径逃逸白名单: path=%s (normalized=%s) allowed_roots=%s"
            % (raw, physical, list(allowed_roots or []))))
    checks.append({"step": "whitelist", "ok": True, "detail": matched_root})

    # ---- 步骤 3：MAX_PATH 长度 -------------------------------------------
    if len(physical) > MAX_PATH:
        fail(PathTooLong(
            "路径长度 %d 超过 Windows MAX_PATH=%d: path=%s"
            % (len(physical), MAX_PATH, physical)))
    checks.append({"step": "max_path", "ok": True,
                   "detail": "len=%d<=%d" % (len(physical), MAX_PATH)})

    # ---- 步骤 5：写模式权限探测（按序）------------------------------------
    if mode == "w":
        target = Path(physical)

        # 5a 目标是已存在目录，却被当文件打开（Errno 13 成因 1）
        try:
            is_dir = target.is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            fail(TargetIsDirectory(
                "目标是已存在目录却被当文件写入: path=%s" % physical))
        checks.append({"step": "target_is_dir", "ok": True, "detail": "目标不是目录"})

        # 5b 只读属性（Errno 13 成因 2a）
        readonly_attr = False
        if target.exists():
            try:
                st = target.stat()
            except OSError as exc:
                fail(convert_oserror(exc, physical, mode))
            readonly_attr = bool(getattr(st, "st_file_attributes", 0)
                                 & _stat.FILE_ATTRIBUTE_READONLY)
        if readonly_attr:
            fail(ReadOnlyFile(
                "目标文件带只读属性(Win32 FILE_ATTRIBUTE_READONLY): path=%s" % physical))
        checks.append({"step": "readonly_attr", "ok": True,
                       "detail": "readonly_attr=%s" % readonly_attr})

        # 5c 父目录可写（父目录不存在时向上找最近存在的祖先探测——写时会 makedirs）
        probe = target.parent
        while not probe.exists():
            nxt = probe.parent
            if str(nxt) == str(probe):
                break
            probe = nxt
        if not os.access(str(probe), os.W_OK):
            fail(ParentNotWritable(
                "父目录不可写(os.access W_OK=False): path=%s probe=%s parent_exists=%s"
                % (physical, str(probe), target.parent.exists())))
        checks.append({"step": "parent_writable", "ok": True, "detail": str(probe)})

        # 5d ACL Deny（icacls 只读探测；icacls 不可用时跳过，由真实写入兜底）
        deny = _icacls_deny(probe)
        if deny:
            fail(AclDenied(
                "父目录 ACL 含 Deny 项(icacls): path=%s probe=%s icacls输出=%s"
                % (physical, str(probe), deny[:200])))
        checks.append({"step": "acl_deny", "ok": True, "detail": "icacls 无 Deny 项"})

    # ---- 步骤 6：返回规范化 Path ------------------------------------------
    return Path(physical), checks


# ==================== 静态字符检查 ====================
def _find_invalid_component(expanded: str):
    """返回第一个非法组件（保留名 / 尾空格 / 尾点）；无则 None。

    跳过空组件（UNC 头）、"."、".."（相对组件，由白名单校验管）、含冒号的盘符段。
    """
    for comp in re.split(r"[\\/]+", expanded):
        if not comp or comp in (".", "..") or ":" in comp:
            continue
        stem = comp.split(".", 1)[0].upper()
        if stem in _RESERVED_NAMES:
            return comp
        if comp != comp.rstrip(" ."):
            return comp
    return None


# ==================== icacls 只读探测 ====================
def _icacls_deny(path):
    """探测路径 ACL 是否含 Deny 项。返回 icacls 输出（命中）或 None（未命中/不可用）。

    只读探测：subprocess 列表参数、shell=False、不改变任何 ACL。
    """
    try:
        proc = subprocess.run(
            ["icacls", str(path)],
            capture_output=True, text=True, errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("icacls 探测不可用，跳过 ACL Deny 检查: path=%s error=%s: %s",
                       path, type(exc).__name__, exc)
        return None
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if _ICACLS_DENY_RE.search(out):
        return out
    return None


# ==================== PermissionError 转换 ====================
def permission_diagnostics(path, mode: str) -> str:
    """生成 Errno 13 排查五要素快照：路径、mode、父目录存在性、只读属性、os.access 结果。"""
    p = Path(path)
    parent = p.parent
    try:
        parent_exists = parent.exists()
    except OSError:
        parent_exists = False
    try:
        readonly = bool(p.exists() and (getattr(p.stat(), "st_file_attributes", 0)
                                        & _stat.FILE_ATTRIBUTE_READONLY))
    except OSError:
        readonly = False
    try:
        writable = os.access(str(parent), os.W_OK)
    except OSError:
        writable = False
    return ("path=%s mode=%s parent=%s parent_exists=%s readonly_attr=%s os.access(W_OK)=%s"
            % (p, mode, parent, parent_exists, readonly, writable))


def convert_oserror(exc: OSError, path, mode: str) -> FileProxyError:
    """把裸 OSError/PermissionError 转成带诊断快照的 FileProxyError 家族异常。

    不吞：返回的异常由调用方 raise（raise convert_oserror(...) from exc）。
    """
    winerror = getattr(exc, "winerror", None)
    diag = permission_diagnostics(path, mode)
    if winerror in _LOCK_WINERRORS:
        return FileLocked(
            "文件被其他进程独占/锁定(WinError %s): %s | 原始错误=%s: %s"
            % (winerror, diag, type(exc).__name__, exc))
    if isinstance(exc, PermissionError):
        return VolumePermissionDenied(
            "访问被拒绝(WinError %s; 只读位/ACL/Docker 卷/占用等): %s | 原始错误=%s: %s"
            % (winerror, diag, type(exc).__name__, exc))
    return FileProxyError(
        "%s: %s | 原始错误=%s" % (type(exc).__name__, diag, exc))
