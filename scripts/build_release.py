#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MoRay 干净发布包构建脚本（里程碑 M5）

用法：python scripts/build_release.py [--version X.Y.Z]
产出：release/MoRay-v<版本>/（目录）+ release/MoRay-v<版本>.zip
幂等：重复运行会先清空再重建，结果一致。

排除：.env / .venv / __pycache__ / server/data / backup / 历史 zip /
      node_modules / 过程性临时脚本与截图（u1.py u2.py clean2.py patch_tools1.py
      verify_*.txt *.png *.log tmp_* 等）；先打印待排除清单再排除。
"""
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 版本号：默认读 server/app/config.py 的 VERSION；可用 --version 覆盖
DEFAULT_VERSION = "0.0.0"
_m = re.search(r'VERSION = "([^"]+)"', (ROOT / "server/app/config.py").read_text(encoding="utf-8"))
if _m:
    DEFAULT_VERSION = _m.group(1)

VERSION = DEFAULT_VERSION
if "--version" in sys.argv:
    i = sys.argv.index("--version")
    if i + 1 < len(sys.argv):
        VERSION = sys.argv[i + 1]

RELEASE_DIR = ROOT / "release"
PKG_DIR = RELEASE_DIR / f"MoRay-v{VERSION}"
ZIP_PATH = RELEASE_DIR / f"MoRay-v{VERSION}.zip"

# 必须包含（相对工程根）
INCLUDE_FILES = [
    "moray-workbench.html",
    "manifest.webmanifest",
    "sw.js",
    "启动MoRay.bat",
    "start_moray.sh",
    "README.md",
    ".gitignore",
]
INCLUDE_DIRS = ["vendor", "deploy", "wallpapers"]

# server/ 内包含
SERVER_INCLUDE = [
    "app/__init__.py",
    "app/config.py",
    "app/db.py",
    "app/main.py",
    "app/api.py",
    "app/crud.py",
    "app/llm_proxy.py",
    "requirements.txt",
    ".env.example",
    "README.md",
]

# scripts/ 内包含
SCRIPTS_INCLUDE = ["start_backend.bat", "start_backend.sh", "build_release.py"]

# 排除模式（文件名/相对路径片段，命中即排除；先打印清单）
EXCLUDE_PATTERNS = [
    ".env", ".venv", "__pycache__", "server/data", "backup", "node_modules",
    "u1.py", "u2.py", "clean2.py", "patch_tools1.py",
    "verify_", "*.png", "*.log", "tmp_", "release/", ".git/",
    "moray_project.zip", "MoRay_网站部署包", "MoRay_最新完整工程",
]


def excluded(rel: str) -> bool:
    name = Path(rel).name
    # 精确 .env（不含 .env.example 模板）
    if name == ".env":
        return True
    for p in EXCLUDE_PATTERNS:
        if p == ".env" or p == ".env.example":
            continue
        if p.endswith("*"):
            if name.startswith(p[:-1]) or name.endswith(p[1:]):
                return True
        elif p in rel or p == name:
            return True
    return False


def collect() -> list:
    """收集发布文件（相对路径）"""
    files = []
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists() and not excluded(f):
            files.append(f)
    for d in INCLUDE_DIRS:
        dp = ROOT / d
        if dp.exists():
            for root, _dirs, fn in os.walk(dp):
                for f in fn:
                    rel = os.path.relpath(os.path.join(root, f), ROOT).replace("\\", "/")
                    if not excluded(rel):
                        files.append(rel)
    for rel in SERVER_INCLUDE:
        p = ROOT / "server" / rel
        if p.exists() and not excluded("server/" + rel):
            files.append("server/" + rel)
    for rel in SCRIPTS_INCLUDE:
        p = ROOT / "scripts" / rel
        if p.exists() and not excluded("scripts/" + rel):
            files.append("scripts/" + rel)
    return sorted(set(files))


def main() -> None:
    # 1) 打印将被排除的现存文件/目录
    print(f"== MoRay 发布构建 v{VERSION} ==")
    print("-- 待排除清单（工程中存在且命中排除规则）--")
    excluded_found = []
    for root, dirs, fn in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".venv", "node_modules", ".git", "__pycache__")]
        rel_root = os.path.relpath(root, ROOT).replace("\\", "/")
        for d in list(dirs):
            rel = (rel_root + "/" + d) if rel_root != "." else d
            if excluded(rel + "/"):
                excluded_found.append(rel + "/")
        for f in fn:
            rel = (rel_root + "/" + f) if rel_root != "." else f
            if excluded(rel):
                excluded_found.append(rel)
    for e in sorted(set(excluded_found)):
        print("  排除:", e)
    print(f"  共 {len(set(excluded_found))} 项")

    # 2) 收集并拷贝
    files = collect()
    if PKG_DIR.exists():
        shutil.rmtree(PKG_DIR)
    PKG_DIR.mkdir(parents=True)
    for rel in files:
        src = ROOT / rel
        dst = PKG_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # 3) 打 zip（排除 zip 自身，避免自包含）
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(PKG_DIR / rel, f"MoRay-v{VERSION}/{rel}")

    # 4) 打印清单与大小
    total = 0
    print(f"\n-- 发布包文件清单（{len(files)} 个文件）--")
    for rel in sorted(files):
        size = (PKG_DIR / rel).stat().st_size
        total += size
        print(f"  {size:>10,}  {rel}")
    print(f"\n目录: {PKG_DIR}")
    print(f"ZIP : {ZIP_PATH}（{total/1024:.0f} KB 源文件）")
    print("构建完成（幂等：重复运行结果一致）")


if __name__ == "__main__":
    main()
