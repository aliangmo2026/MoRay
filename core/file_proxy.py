# -*- coding: utf-8 -*-
"""file_proxy — file 工具路径翻译 + 同步中间件（2026-09-30 任务三重构：诊断层 + 原子写）。

设计（沿用"加层不拆墙"）：原有沙箱/白名单校验逻辑与全部 7 个公开函数签名 100% 保留。
本次在入口新增 path_diagnostics 诊断层，并把"写用户原目录"从 copy2 改为原子写
（tmp + fsync + os.replace），根治 PermissionError Errno 13 被裸抛/被吞的两类问题：

  用户输入外部真实路径
    → 授权校验（authorized_paths.json，唯一白名单源，不缓存即时生效）
    → path_diagnostics.diagnose（逃逸/超长/保留名/目录冲突/只读/父目录/ACL 分类）
    → 映射到沙箱白名单目录（sandbox_root/<目录名>/相对路径）
    → 增量同步（读前复制到沙箱 / 写后原子回写原路径）
    → 交给原有 file 工具执行（操作的是沙箱内合法路径）
    → 结果路径再翻译回用户原路径

异常语义（架构师裁定映射表，2026-09-30）：
  is_authorized_path false            → dict{"success": False, "error": "路径未授权：..."}   (403)
  translate_to_sandbox None           → dict{"success": False, "error": "路径映射失败：..."}   (403)
  diagnose raise PathEscapeError      → dict{"success": False, "error": "路径逃逸：..."}       (403)
  diagnose raise 其余 8 类诊断异常     → raise（tool_registry 兜底 -1，绝不吞）
  原子写/回写 PermissionError          → raise（经 convert_oserror 转换，附五要素诊断快照）

安全边界：非授权路径直接拒绝；所有写入必须落在 authorized_paths.json 内。
"""
import json
import logging
import os
import shutil
from pathlib import Path
from uuid import uuid4

try:  # 双形态导入：服务主链路（D:\MoRay 在 sys.path）与 tool_registry 裸模块链路（core/ 在 sys.path）
    from core.path_diagnostics import (
        FileProxyError,
        PathEscapeError,
        convert_oserror,
        diagnose,
    )
except ImportError:  # pragma: no cover
    from path_diagnostics import (  # type: ignore
        FileProxyError,
        PathEscapeError,
        convert_oserror,
        diagnose,
    )

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get(
    "MORAY_FILE_PROXY_CONFIG",
    r"D:\MoRay\config\authorized_paths.json",
)


def load_config() -> dict:
    """读取授权目录配置（不缓存，改配置文件即时生效）。"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _allowed_roots() -> list:
    """diagnose 的白名单来源：authorized_paths.json 唯一权威。

    不缓存的理由：读取频率 = file 工具调用频度（微秒级开销），mtime 缓存引入陈旧
    窗口与竞态，而"改配置即时生效"是本模块 docstring 明文承诺的行为。
    读取失败按空白名单处理（fail-closed：is_authorized_path 会先行拒绝）。
    """
    try:
        return list(load_config().get("authorized_dirs") or [])
    except Exception as exc:
        logger.warning("authorized_paths.json 读取失败，白名单按空处理(fail-closed): "
                       "config=%s error=%s: %s", CONFIG_PATH, type(exc).__name__, exc)
        return []


def is_authorized_path(user_path: str) -> bool:
    """检查用户路径是否在任一授权目录内（含目录本身；严格路径边界，防 D:\\MoRay 误匹配 D:\\MoRayWorkspace）。"""
    try:
        config = load_config()
    except Exception:
        return False
    user_path = os.path.abspath(user_path)
    for auth_dir in config["authorized_dirs"]:
        auth_dir = os.path.abspath(auth_dir)
        if user_path == auth_dir or user_path.startswith(auth_dir + os.sep):
            return True
    return False


def translate_to_sandbox(user_path: str) -> str:
    """把用户外部路径翻译成沙箱内的合法路径；未授权返回 None。"""
    try:
        config = load_config()
    except Exception:
        return None
    user_path = os.path.abspath(user_path)
    sandbox_root = os.path.abspath(config["sandbox_root"])
    # [2026-10-01 U-3] 幂等守卫：路径已在 sandbox_root 内 → 直接返回，不再映射。
    # 修复 U-2 侦察发现的路径叠加 bug：sandbox_root 是授权目录的子目录，已映射路径
    # 会再次命中授权并被叠加一层（如 mapped\_ui_test → mapped\MoRay\workspace\mapped\_ui_test）。
    # 守卫逻辑对照 zcode_proxy._resolve_exec_path。
    if user_path == sandbox_root or user_path.startswith(sandbox_root + os.sep):
        return user_path
    for auth_dir in config["authorized_dirs"]:
        auth_dir_abs = os.path.abspath(auth_dir)
        if user_path == auth_dir_abs or user_path.startswith(auth_dir_abs + os.sep):
            rel_path = os.path.relpath(user_path, auth_dir_abs)
            dir_name = os.path.basename(auth_dir_abs) or "root"
            sandbox_path = os.path.join(sandbox_root, dir_name, rel_path)
            os.makedirs(os.path.dirname(sandbox_path), exist_ok=True)
            return sandbox_path
    return None


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """原子写：tmp + fsync + os.replace；失败清理 tmp，PermissionError 转诊断异常后抛出（不吞）。"""
    parent = target.parent
    if not parent.exists():
        os.makedirs(parent, exist_ok=True)
    tmp = parent / (".%s.%s.tmp" % (target.name, uuid4().hex))
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            # 清理失败不掩盖原始异常：留 WARNING 后按原异常抛出
            logger.warning("原子写临时文件清理失败(原异常仍抛出): tmp=%s error=%s: %s",
                           tmp, type(cleanup_exc).__name__, cleanup_exc)
        if isinstance(exc, PermissionError):
            raise convert_oserror(exc, target, "w") from exc
        raise


def sync_file_to_sandbox(user_path: str, sandbox_path: str) -> None:
    """增量同步用户文件到沙箱（仅在文件更新或沙箱副本缺失时复制）。

    读方向：失败降级不阻塞主流程（读旧副本优于不可用），但必须留结构化 WARNING
    （路径/错误类型/消息），不再裸吞。
    """
    try:
        if not os.path.exists(user_path):
            return
        if not os.path.exists(sandbox_path) or \
                os.path.getmtime(user_path) > os.path.getmtime(sandbox_path):
            os.makedirs(os.path.dirname(sandbox_path), exist_ok=True)
            shutil.copy2(user_path, sandbox_path)
    except OSError as exc:
        logger.warning("sync_file_to_sandbox 降级(沙箱副本可能过期): user=%s sandbox=%s "
                       "error=%s: %s", user_path, sandbox_path, type(exc).__name__, exc)


def sync_file_back(sandbox_path: str, user_path: str) -> None:
    """沙箱内修改的文件同步回用户原目录（仅当沙箱副本更新时）。

    写方向：原子写(tmp + os.replace)替代 copy2，避免半截文件；
    失败抛诊断异常（写方向失败 = 丢用户数据，不降级不吞）。
    """
    if not os.path.exists(sandbox_path):
        return
    if not os.path.exists(user_path) or \
            os.path.getmtime(sandbox_path) > os.path.getmtime(user_path):
        os.makedirs(os.path.dirname(user_path), exist_ok=True)
        data = Path(sandbox_path).read_bytes()
        _atomic_write_bytes(Path(user_path), data)


def proxy_file_read(user_path: str, original_read_func):
    """代理读取：授权校验 → 映射沙箱 → diagnose(r) → 同步最新 → 调用原读取函数（沙箱副本）。

    返回：original_read_func 的返回值（成功）；{"success": False, "error": ...}（授权类失败）。
    诊断异常（PathTooLong/InvalidPathName 等）原样 raise，不吞。
    """
    if not is_authorized_path(user_path):
        return {"success": False, "error": "路径未授权：%s（请先添加到 authorized_paths.json）" % user_path}
    sandbox_path = translate_to_sandbox(user_path)
    if not sandbox_path:
        return {"success": False, "error": "路径映射失败：%s" % user_path}
    try:
        diagnose(user_path, "r", _allowed_roots())
    except PathEscapeError as exc:
        return {"success": False, "error": "路径逃逸：%s" % exc}
    sync_file_to_sandbox(user_path, sandbox_path)
    try:
        return original_read_func(sandbox_path)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": "%s: %s" % (type(exc).__name__, str(exc))}


def proxy_file_write(user_path: str, content: str, original_write_func):
    """代理写入：授权校验 → 映射沙箱 → diagnose(w) → 沙箱写入 → 原子回写原目录。

    返回：original_write_func 的返回值（成功）；{"success": False, "error": ...}（授权类失败）。
    诊断异常（ReadOnlyFile/TargetIsDirectory 等）与回写失败原样 raise，不吞。
    original_write_func 为 None 时跳过沙箱副本，直接对 user_path 原子写。
    """
    if not is_authorized_path(user_path):
        return {"success": False, "error": "路径未授权：%s（请先添加到 authorized_paths.json）" % user_path}
    sandbox_path = translate_to_sandbox(user_path)
    if not sandbox_path:
        return {"success": False, "error": "路径映射失败：%s" % user_path}
    try:
        diagnose(user_path, "w", _allowed_roots())
    except PathEscapeError as exc:
        return {"success": False, "error": "路径逃逸：%s" % exc}
    # 其余 FileProxyError（TargetIsDirectory/ReadOnlyFile/...）按裁定原样 raise

    if original_write_func is None:
        _atomic_write_bytes(Path(user_path), str(content).encode("utf-8"))
        return {"success": True, "path": user_path, "via": "atomic"}

    try:
        result = original_write_func(sandbox_path, content)
    except PermissionError as exc:
        raise convert_oserror(exc, sandbox_path, "w") from exc
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": "%s: %s" % (type(exc).__name__, str(exc))}
    # 写入成功（result 为真值或带 success=True）后回写原目录（原子写，失败抛诊断异常）
    ok = bool(result) if isinstance(result, (bool, str, int)) else bool(result.get("success"))
    if ok:
        sync_file_back(sandbox_path, user_path)
    return result
