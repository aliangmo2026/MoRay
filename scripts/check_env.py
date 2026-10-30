# -*- coding: utf-8 -*-
"""MoRay 环境自检脚本（批次一 · 任务 A）

纯标准库实现——本脚本自身就是用来查环境的，不引入任何第三方依赖。

检查项：
  1. python-dotenv   import 探测；缺失时 server/.env 会静默失效（历史事故根因，
                     即 server/app/config.py 42-47 行 load_dotenv 的 try/except 吞错）
  2. node            ZCode 引擎 CLI 依赖
  3. ollama          http://127.0.0.1:11434/api/tags 存活探测（5 秒超时）
  4. server/.env     子进程内 load_dotenv 实加载 + 必需键存在性/取值校验

用法（用装了 python-dotenv 的解释器跑，MoRay 服务端即 D:\\python3.12\\python.exe）：
  D:\\python3.12\\python.exe D:\\MoRay\\scripts\\check_env.py
退出码：0 = 全部 OK；1 = 存在 FAIL

输出编码：交互式控制台用 Python 原生 WinConsoleIO（中文恒可显，不挑代码页）；
管道/重定向时跟随系统 locale（本机 cp936），需要 UTF-8 捕获时设
PYTHONIOENCODING=utf-8 即可——脚本只兜底 errors=replace，绝不因字符编码炸掉。
"""

import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "server", ".env")
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"
REQUIRED_ENV_KEYS = ("ENABLE_CLOUD_API",)

# .env 实加载验证放到子进程：主进程环境可能被 shell 预先 export 的同名变量污染，
# 只有"干净子进程 + 显式 load_dotenv(该文件)"才能证明这个文件本身可被加载。
# 必需键经 argv[2:] 传入，避免键名在代码里两处维护。
# 注意：子进程只输出状态字（DOTENV_MISSING/LOAD_FAILED/KEY_MISSING/KEY_INVALID/ENV_OK），
# 绝不回显任何键值——保证本脚本输出永远不含 API key 明文。
_ENV_CHECK_BODY = """\
import os, sys
path = sys.argv[1]
required = sys.argv[2:]
try:
    from dotenv import load_dotenv
except ImportError:
    print("DOTENV_MISSING")
    sys.exit(6)
ok = load_dotenv(path, override=True)
if not ok:
    print("LOAD_FAILED: load_dotenv returned False")
    sys.exit(3)
missing = [k for k in required if os.environ.get(k) is None]
if missing:
    print("KEY_MISSING: " + ", ".join(missing))
    sys.exit(4)
invalid = [k for k in required if os.environ.get(k) not in ("false", "true")]
if invalid:
    print("KEY_INVALID(expect false/true): " + ", ".join(invalid))
    sys.exit(5)
print("ENV_OK")
"""


def check_dotenv():
    """a. python-dotenv 可导入性（用当前解释器探测：服务端 config.py 就是它跑的）"""
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import dotenv"],
            capture_output=True, timeout=30,
        )
    except Exception as e:
        return False, "python-dotenv 探测子进程异常: %s: %s" % (type(e).__name__, e)
    if r.returncode == 0:
        return True, "可导入（解释器 %s）" % sys.executable
    return False, (
        "❌ [环境故障] python-dotenv 未安装！server/.env 将静默失效（历史事故根因）。请执行：\n"
        "       pip install python-dotenv"
    )


def check_node():
    """b. node 是否可用（ZCode 引擎 CLI 的运行时）"""
    try:
        r = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except FileNotFoundError:
        return False, "❌ node 缺失，ZCode 引擎无法启动"
    except Exception as e:
        return False, "❌ node 探测异常: %s: %s" % (type(e).__name__, e)
    if r.returncode == 0:
        ver = (r.stdout or "").strip() or "(无版本输出)"
        return True, "node 可用 %s" % ver
    return False, "❌ node 缺失，ZCode 引擎无法启动（exit=%s）" % r.returncode


def check_ollama():
    """c. Ollama 本机服务存活（本地模型依赖）"""
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=5) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return False, "❌ Ollama 未运行，本地模型 qwen3.5:9b 不可用（HTTP %s）" % e.code
    except Exception as e:
        return False, "❌ Ollama 未运行，本地模型 qwen3.5:9b 不可用（%s: %s）" % (
            type(e).__name__, e)
    if status != 200:
        return False, "❌ Ollama 未运行，本地模型 qwen3.5:9b 不可用（HTTP %s）" % status
    try:
        n = len(json.loads(raw.decode("utf-8", "replace")).get("models", []))
    except Exception:
        n = None
    detail = "Ollama 存活 %s" % OLLAMA_URL
    if n is not None:
        detail += "，已登记模型 %d 个" % n
    return True, detail


def _print_env_failure_block(details):
    """d. .env 加载失败时的醒目错误块（★ 围栏）"""
    bar = "★" * 64
    lines = [
        "",
        bar,
        "★★  [严重故障] server/.env 加载验证失败！",
        "★★",
        "★★  .env 加载失败会导致配置全部失效：MORAY_LLM_API_KEY、模型路由、",
        "★★  熔断阈值、Argus/Iris 地址等所有依赖 .env 的配置将静默丢失；",
        "★★  而 server/app/config.py（42-47 行）的 try/except 会把故障整个吞掉，",
        "★★  服务照样启动，问题要拖到 LLM 调用时才以 503/空配置的形式暴露。",
        "★★",
        "★★  排查步骤：",
        "★★    1. 确认文件存在: %s" % ENV_PATH,
        "★★    2. 用启动 MoRay 的那个解释器执行: pip install python-dotenv",
        "★★    3. 检查文件编码（UTF-8 无 BOM）与行格式（KEY=value）",
        "★★    4. 确认必需键存在: %s" % ", ".join(REQUIRED_ENV_KEYS),
    ]
    for d in details:
        if d:
            lines.append("★★  子进程输出: %s" % d)
    lines.append(bar)
    lines.append("")
    print("\n".join(lines))
    sys.stdout.flush()


def check_env_file():
    """d. server/.env 实加载验证（干净子进程内 load_dotenv）"""
    if not os.path.isfile(ENV_PATH):
        _print_env_failure_block(["文件不存在: %s" % ENV_PATH])
        return False, "server/.env 文件不存在"
    try:
        r = subprocess.run(
            [sys.executable, "-c", _ENV_CHECK_BODY, ENV_PATH] + list(REQUIRED_ENV_KEYS),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception as e:
        _print_env_failure_block(["探测子进程异常: %s: %s" % (type(e).__name__, e)])
        return False, "子进程执行失败"
    if r.returncode == 0 and "ENV_OK" in (r.stdout or ""):
        return True, "load_dotenv 实加载成功，必需键齐全且取值合法: %s" % ", ".join(REQUIRED_ENV_KEYS)
    msgs = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()][:3]
    tail = (r.stderr or "").strip().splitlines()
    if tail:
        msgs.append("stderr 末行: " + tail[-1][:200])
    _print_env_failure_block(msgs)
    return False, ".env 加载验证未通过（exit=%s，详见上方 ★ 错误块）" % r.returncode


CHECKS = [
    ("python-dotenv", check_dotenv),
    ("node", check_node),
    ("ollama", check_ollama),
    (".env 加载", check_env_file),
]


def main():
    try:
        # 只换 errors 不换 encoding：交互式控制台保持 WinConsoleIO 原生 Unicode 输出，
        # 管道下跟随 locale（cp936 也行），不可编码字符降级为 ? 而不是让脚本崩掉。
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("=" * 72)
    print("MoRay 环境自检  %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("解释器: %s" % sys.executable)
    print("=" * 72)

    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # 检查函数自身炸掉也按 FAIL 计，不让脚本裸抛
            ok, detail = False, "检查函数异常: %s: %s" % (type(e).__name__, e)
        results.append((name, ok, detail))

    print("-" * 72)
    print("%-14s %-6s %s" % ("项名", "状态", "说明"))
    print("-" * 72)
    for name, ok, detail in results:
        print("%-14s %-6s %s" % (name, "OK" if ok else "FAIL", detail))
    print("-" * 72)

    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print("结论: 共 %d 项，FAIL %d 项 -> %s（退出码 1）" % (
            len(results), len(failed), ", ".join(failed)))
        return 1
    print("结论: 共 %d 项，全部 OK（退出码 0）" % len(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
