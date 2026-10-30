# -*- coding: utf-8 -*-
"""zcode_proxy — ZCode 执行引擎 + 子智能体中间层代理（2026-09-30 部署，与 file_proxy 同级）。

对外接口与任务书完全一致：run_file / run_code / sub_agent / zcode_run / zcode_sub_agent，
返回体字段（success/stdout/stderr/exit_code/previews/raw_output/modified_files）逐字保留。

三处实况修正（任务书命令形状基于理想假设，真机 zcode CLI 为严格解析器，未知子命令即 exit 1；
zcode.cmd --help 实测：无 run/agent 子命令；有效形态 `-p <文本> --mode build|edit|plan|yolo --cwd <path>`）：
  ① run_file：改为解释器直接执行（py→当前进程 python、js→node、bat/cmd→cmd /c），
     保持"运行脚本、捕获输出、提取预览"能力与返回体形状不变；
  ② sub_agent：真实 CLI 形状（zcode.cmd --help 实测）= 内联 `-p <提示词>`
     + `--mode %MORAY_ZCODE_SUBAGENT_MODE%（缺省 yolo，与 00:34 zcode_adapter FORCED_MODE 同口径）`
     + `--cwd 工作目录`；本 CLI 版本无 `-p -` stdin 读取逻辑（E2E 实测 agent 收到空消息，
     与 Day16 结论一致）；且 zcode.cmd 只是 node shim，为绕开 cmd.exe 引号重解析改为
     解析后直调 [node.exe, zcode.cjs]；不加 --json（子智能体按提示词输出四段结构化文本，直接解析）；
     work_dir 存在真实目录时直接在其中执行（授权已校验，改动天然写回原文件），
     仅在真实目录缺失时落到沙箱映射目录并回写；
  ③ _extract_previews：原任务书端口正则捕获组错位（group(1) 实为 URL 段、端口落在 group(2)），
     修正为单一捕获组=端口，并补 Uvicorn / http.server 常见行。
安全边界：执行/派发前校验目标路径为授权目录（config/authorized_paths.json）或工作区/沙箱内，
越界一律拒绝（与 agent_tools 既有"路径未授权即拒"语义一致）。
"""
import os
import re
import json
import shlex
import logging
import subprocess
import sys
from typing import Dict, List
from uuid import uuid4

logger = logging.getLogger(__name__)

# ========== 基础配置 ==========
ZCODE_CLI_PATH = os.environ.get("MORAY_ZCODE_CLI", "").strip() or r"D:\MoRay\engines\bin\zcode.cmd"
SANDBOX_MAPPED_ROOT = r"D:\MoRay\workspace\mapped"
PROMPTS_DIR = r"D:\MoRay\config\prompts"
DEFAULT_TIMEOUT = 300  # 执行超时时间，单位秒
SUB_AGENT_TIMEOUT = 600
SUB_AGENT_MODE = (os.environ.get("MORAY_ZCODE_SUBAGENT_MODE", "").strip() or "yolo")


# ========== 路径映射：复用 file_proxy 逻辑，保证读写同目录 ==========
def _translate_path(real_path: str) -> str:
    """和 file_proxy 共用路径翻译逻辑，确保读写路径一致"""
    # [2026-10-01 U-3] 幂等守卫：路径已在沙箱映射根内 → 原样返回，不再映射。
    # 修复 U-2 侦察发现的路径叠加 bug（与 file_proxy.translate_to_sandbox 同源）：
    # sandbox_root 是授权目录子目录，已映射路径会再次命中授权并被叠加一层；
    # 守卫逻辑对照 _resolve_exec_path。
    real_abs = os.path.abspath(real_path)
    sandbox_root_abs = os.path.abspath(SANDBOX_MAPPED_ROOT)
    if real_abs == sandbox_root_abs or real_abs.startswith(sandbox_root_abs + os.sep):
        return real_path
    try:
        from core.file_proxy import translate_to_sandbox
        sandbox_path = translate_to_sandbox(real_path)
        return sandbox_path if sandbox_path else real_path
    except Exception:
        return real_path


def _under(base: str, path: str) -> bool:
    b, p = os.path.abspath(base), os.path.abspath(path)
    return p == b or p.startswith(b + os.sep)


def _resolve_exec_path(user_path: str) -> str:
    """执行路径解析（实况补强：任务书只翻译不回写，沙箱副本可能不存在/过期）：
    已在沙箱根内 → 原样；否则翻译到沙箱并把真实文件同步过去，副本不存在则回退真实路径。"""
    real = os.path.abspath(user_path)
    if _under(SANDBOX_MAPPED_ROOT, real):
        return real
    sandbox = os.path.abspath(_translate_path(real))
    if sandbox != real and os.path.isfile(real):
        try:
            from core.file_proxy import sync_file_to_sandbox
            sync_file_to_sandbox(real, sandbox)
        except Exception as exc:
            # 任务三③：静默回退改 WARNING 后回退（不阻断）——副本缺失/过期时执行真实路径
            logger.warning("_resolve_exec_path 沙箱同步失败，回退真实路径: "
                           "path=%s sandbox=%s error=%s: %s",
                           real, sandbox, type(exc).__name__, exc)
        if os.path.exists(sandbox):
            return sandbox
    return real


def _atomic_copy_file(src: str, dst: str) -> None:
    """任务三②：原子复制（tmp + fsync + os.replace），避免回写半截文件。"""
    with open(src, "rb") as f:
        data = f.read()
    tmp = os.path.join(os.path.dirname(dst), ".%s.%s.tmp" % (os.path.basename(dst), uuid4().hex))
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as cleanup_exc:
            logger.warning("_atomic_copy_file 临时文件清理失败(原异常仍抛出): tmp=%s error=%s: %s",
                           tmp, type(cleanup_exc).__name__, cleanup_exc)
        raise


def _sync_tree_back(sandbox_dir: str, real_dir: str):
    """子智能体在沙箱目录完成后，把新增/更新的文件回写用户真实目录。

    任务三②：改原子写（tmp + os.replace）；失败不再裸吞，收集进 failed_files。
    返回 (回写数, 失败清单)：failed_files = [{"path": 目标路径, "error": "类型: 消息"}, ...]
    """
    n = 0
    failed_files = []
    if not os.path.isdir(sandbox_dir):
        return n, failed_files
    for root, _dirs, files in os.walk(sandbox_dir):
        for fn in files:
            sp = os.path.join(root, fn)
            rel = os.path.relpath(sp, sandbox_dir)
            rp = os.path.join(real_dir, rel)
            try:
                if not os.path.exists(rp) or os.path.getmtime(sp) > os.path.getmtime(rp):
                    os.makedirs(os.path.dirname(rp), exist_ok=True)
                    _atomic_copy_file(sp, rp)
                    n += 1
            except OSError as exc:
                failed_files.append({
                    "path": rp,
                    "error": "%s: %s" % (type(exc).__name__, str(exc)),
                })
    return n, failed_files


def _is_permitted_path(path: str) -> bool:
    """授权目录（file_proxy 白名单）或 工作区/沙箱映射根之下 → 允许执行。"""
    try:
        from core.file_proxy import is_authorized_path
        if is_authorized_path(path):
            return True
    except Exception:
        pass
    ap = os.path.abspath(path)
    for base in (SANDBOX_MAPPED_ROOT, os.environ.get("MORAY_WORKSPACE", "").strip() or ""):
        if base and (ap == os.path.abspath(base) or ap.startswith(os.path.abspath(base) + os.sep)):
            return True
    return False


def _resolve_cli_argv() -> List[str]:
    """解析 CLI 入口（实况修正②b）：zcode.cmd 只是 node shim——
    subprocess 直调 .cmd 会经 cmd.exe /c 重解析，多行提示词里的 & " % 等会踩引号地狱；
    故从 .cmd 中解出 [node.exe, zcode.cjs] 直调（纯 CreateProcess 引号规则，安全）。
    解析失败则原样返回 .cmd。"""
    p = ZCODE_CLI_PATH
    if p.lower().endswith((".cmd", ".bat")) and os.path.exists(p):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
            paths = [os.path.expandvars(q) for q in re.findall(r'"([^"]+)"', text)]
            if len(paths) >= 2 and os.path.exists(paths[0]) and os.path.exists(paths[1]):
                return paths[:2]
        except OSError:
            pass
    return [p]


# ========== 分场景提示词加载 ==========
def load_scene_prompt(scene: str = "general") -> str:
    """按场景加载对应子智能体提示词，不存在则回退通用版"""
    prompt_map = {
        "bug_fix": "bug_fix_prompt.txt",
        "feature_dev": "feature_dev_prompt.txt",
        "debug": "debug_prompt.txt",
        "general": "zcode_sub_agent_prompt.txt"
    }
    filename = prompt_map.get(scene, prompt_map["general"])
    file_path = os.path.join(PROMPTS_DIR, filename)

    if not os.path.exists(file_path):
        file_path = os.path.join(PROMPTS_DIR, prompt_map["general"])

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()


# ========== 预览资源提取 ==========
def _extract_previews(stdout: str) -> List[Dict]:
    """从运行输出中提取服务端口、生成文件、图片等预览资源"""
    previews = []
    seen_ports = set()

    # 提取本地 Web 服务端口（实况修正：每组唯一捕获组 = 端口号）
    port_patterns = [
        r'(?:http://127\.0\.0\.1|http://localhost|http://0\.0\.0\.0|Running on https?://[\d\.]+):(\d{2,5})',
        r'Uvicorn running on https?://[^\s/]+:?(\d{0,5})',
        r'Serving HTTP on [\d\.]+ port (\d{2,5})',
        r'local:\s*https?://[^\s/]*:?(\d{4,5})',
    ]
    for pattern in port_patterns:
        for m in re.finditer(pattern, stdout):
            port = m.group(1)
            if port and port not in seen_ports:
                seen_ports.add(port)
                previews.append({
                    "type": "web",
                    "url": "/preview/%s/" % port,
                    "label": "Web 服务预览 (端口 %s)" % port,
                })

    # 提取生成的图片、HTML、文档等文件
    file_patterns = [
        r'(?:Saved|Generated|Output|Exported)[:：\s]+([^\n]+\.(?:png|jpg|jpeg|html|pdf|xlsx|csv))',
        r'文件已保存[:：\s]+([^\n]+\.\w+)',
    ]
    for pattern in file_patterns:
        matches = re.findall(pattern, stdout, re.IGNORECASE)
        for f in matches:
            f = f.strip()
            previews.append({
                "type": "file",
                "path": f,
                "label": os.path.basename(f),
            })

    return previews


# ========== 核心执行能力 ==========
class ZCodeProxy:
    @staticmethod
    def run_file(file_path: str, args: str = "", lang: str = "auto") -> Dict:
        """运行指定路径的脚本文件，自动路径映射、捕获输出、提取预览。

        实况修正①：zcode CLI 无 run 子命令 → 解释器直接执行（返回体形状与任务书一致）。
        """
        sandbox_path = _resolve_exec_path(file_path)

        if not _is_permitted_path(file_path):
            return {
                "success": False,
                "error": "路径未授权：%s（请添加到 config/authorized_paths.json 或使用工作区/沙箱内路径）" % file_path,
                "previews": [],
            }

        ext = os.path.splitext(sandbox_path)[1].lower()
        lang = (lang or "auto").lower()
        if lang == "auto":
            lang = {"py": "python", "js": "javascript", "mjs": "javascript",
                    "cjs": "javascript", "bat": "batch", "cmd": "batch"}.get(ext.lstrip("."), "auto")
        if lang in ("python", "py"):
            cmd = [sys.executable, sandbox_path]
        elif lang in ("javascript", "js", "node"):
            cmd = ["node", sandbox_path]
        elif lang in ("batch", "bat", "cmd"):
            cmd = ["cmd", "/c", sandbox_path]
        else:
            return {"success": False, "error": "无法识别运行方式（lang=auto，扩展名 %s 无对应解释器）" % ext,
                    "previews": []}
        if args:
            try:
                cmd.extend(shlex.split(args))
            except ValueError:
                cmd.extend(args.split())

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=DEFAULT_TIMEOUT,
                cwd=os.path.dirname(sandbox_path)
            )

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            success = result.returncode == 0

            return {
                "success": success,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.returncode,
                "previews": _extract_previews(stdout + stderr),
                "raw_output": stdout + stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "执行超时（%d秒）" % DEFAULT_TIMEOUT,
                "previews": [],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "previews": [],
            }

    @staticmethod
    def run_code(code: str, lang: str = "python", filename: str = "temp_run.py") -> Dict:
        """直接运行代码片段，自动写入临时文件后执行"""
        temp_dir = os.path.join(SANDBOX_MAPPED_ROOT, "_temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, filename)

        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(code)

        return ZCodeProxy.run_file(temp_path, lang=lang)

    @staticmethod
    def sub_agent(task_prompt: str, work_dir: str, scene: str = "general") -> Dict:
        """调用 ZCode 原生子智能体执行任务，按场景注入对应提示词。

        实况修正②：真实 CLI 形状（zcode.cmd --help 实测）= `-p -`（stdin 喂提示词）
        + `--mode yolo|edit|build|plan` + `--cwd`；env 注入与 zcode_adapter._build_env 同口径。
        """
        # 任务三①：派发前对目标工作目录做写模式预检（诊断失败明确报错，不静默派发）
        try:
            from core.file_proxy import load_config
            allowed_roots = list(load_config().get("authorized_dirs") or [])
        except Exception as exc:
            logger.warning("sub_agent 预检：authorized_paths 读取失败，白名单按空处理(fail-closed): %s: %s",
                           type(exc).__name__, exc)
            allowed_roots = []
        try:
            from core.path_diagnostics import FileProxyError, diagnose
        except Exception as exc:  # 诊断层自身不可用也 fail-closed，不静默派发
            return {
                "success": False,
                "error": "PathDiagnosticsError: %s: %s" % (type(exc).__name__, exc),
                "modified_files": [],
                "previews": [],
            }
        # 任务三①：派发前对目标工作目录做写模式预检（诊断失败明确报错，不静默派发）。
        # 实况修正：work_dir 本身是目录属合法预期（isdir 分支直接在其中执行），
        # 直接 diagnose(work_dir,"w") 会误触 TargetIsDirectory（诊断层语义=写文件）；
        # 故目录场景改用"目录内虚拟探针文件"预检（diagnose 只读探测，不落盘无痕迹），
        # 白名单边界/可写探测语义不变。
        probe_target = (os.path.join(work_dir, "_diag_probe.tmp")
                        if os.path.isdir(work_dir) else work_dir)
        try:
            diagnose(probe_target, "w", allowed_roots)
        except FileProxyError as exc:
            return {
                "success": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "modified_files": [],
                "previews": [],
            }
        except Exception as exc:  # 诊断层自身运行异常同样 fail-closed
            return {
                "success": False,
                "error": "PathDiagnosticsError: %s: %s" % (type(exc).__name__, exc),
                "modified_files": [],
                "previews": [],
            }

        real_dir = os.path.abspath(work_dir)
        if os.path.isdir(real_dir):
            # 真实目录存在 → 直接在其中执行（授权已由 _is_permitted_path 把关；
            # 改动天然"写回原文件"，无需沙箱往返复制）
            run_dir, sandbox_dir = real_dir, real_dir
        else:
            sandbox_dir = os.path.abspath(_translate_path(real_dir))
            os.makedirs(sandbox_dir, exist_ok=True)
            run_dir = sandbox_dir

        if not _is_permitted_path(work_dir):
            return {
                "success": False,
                "error": "路径未授权：%s（请添加到 config/authorized_paths.json）" % work_dir,
                "modified_files": [],
                "previews": [],
            }

        system_prompt = load_scene_prompt(scene)
        full_prompt = "%s\n\n【当前任务】\n%s" % (system_prompt, task_prompt)

        cmd = [
            *_resolve_cli_argv(),
            "-p", full_prompt,          # 内联提示词（本 CLI 无 stdin 读取逻辑，`-p -` 实测透传空消息）
            "--mode", SUB_AGENT_MODE,
            "--cwd", run_dir,
        ]

        env = dict(os.environ)
        cfg = env.get("MORAY_ZCODE_PROVIDER_CONFIG", "").strip()
        if cfg:
            env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = cfg
        env.setdefault("ZCODE_MODEL_TELEMETRY_ENABLED", "0")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=SUB_AGENT_TIMEOUT,
                cwd=run_dir,
                env=env,
            )
            output = (result.stdout or b"").decode("utf-8", errors="replace")
            errtext = (result.stderr or b"").decode("utf-8", errors="replace")
            success = result.returncode == 0

            # 实况补强：子智能体在沙箱副本上的改动回写用户真实目录（闭环"写回原文件"）
            synced_back = 0
            failed_files = []
            if sandbox_dir != real_dir:
                synced_back, failed_files = _sync_tree_back(sandbox_dir, real_dir)

            # 提取修改文件列表（子智能体按提示词第 3 节四段格式输出 [修改文件] 清单）
            modified_files = re.findall(r'[-•] ([^\n]+?\.[a-zA-Z0-9]+)[:：]', output)

            return {
                "success": success,
                "result": output + (("\n[stderr]\n" + errtext) if errtext.strip() else ""),
                "exit_code": result.returncode,
                "modified_files": modified_files,
                "synced_back": synced_back,
                "failed_files": failed_files,
                "previews": _extract_previews(output + errtext),
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "子智能体执行超时（%d秒）" % SUB_AGENT_TIMEOUT,
                "modified_files": [],
                "previews": [],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "modified_files": [],
                "previews": [],
            }


# ========== 对外统一接口（工具调度层直接调用） ==========
def zcode_run(file_path: str, **kwargs) -> Dict:
    return ZCodeProxy.run_file(file_path, **kwargs)


def zcode_sub_agent(task: str, work_dir: str, scene: str = "general") -> Dict:
    return ZCodeProxy.sub_agent(task, work_dir, scene)
