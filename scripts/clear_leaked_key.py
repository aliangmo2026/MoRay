# -*- coding: utf-8 -*-
"""[P0-1 2026-11-01] 泄露 DeepSeek Key 清理脚本（换 Key 前后两用）。

用法：
  1) 换 Key 前清空：  D:\\python3.12 scripts\\clear_leaked_key.py
     —— .env 的 MORAY_LLM_API_KEY 置空 + cloud_providers 表 api_key 清空。
  2) 换 Key 时写入：  set MORAY_NEW_DEEPSEEK_KEY=新Key && D:\\python3.12 scripts\\clear_leaked_key.py
     —— 新 Key 只从环境变量读取，绝不经命令行参数（防 pslist 泄露）、
        绝不写进本脚本/日志/测试（Mimosa 凭据纪律）。

安全性质：
  - 改 .env 前先备份到 scripts\\.env.backup-<timestamp>（只读权限语义靠文件本身）。
  - 全程只回显"已配置/长度/掩码"，从不打印任何 Key 明文。
  - 数据库写操作走参数绑定（? 占位），无拼接。
"""
import datetime
import io
import os
import re
import sqlite3
import sys

ENV_PATH = os.path.join("D:", os.sep, "MoRay", "server", ".env")
DB_PATH = os.path.join("D:", os.sep, "MoRay", "server", "data", "moray.sqlite3")
BACKUP_DIR = os.path.join("D:", os.sep, "MoRay", "scripts")
KEY_LINE = re.compile(r"^(MORAY_LLM_API_KEY\s*=)(.*)$")


def mask(value: str) -> str:
    """只回显长度与首尾各2字符，永不落明文。"""
    v = value.strip()
    if not v:
        return "(空)"
    head, tail = v[:2], v[-2:]
    return f"{head}…{tail}（长度 {len(v)}）"


def main() -> int:
    new_key = os.environ.get("MORAY_NEW_DEEPSEEK_KEY", "").strip()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- 1. .env：备份 -> 改写 ----
    with io.open(ENV_PATH, "r", encoding="utf-8") as f:
        env_text = f.read()
    backup_path = os.path.join(BACKUP_DIR, f".env.backup-{stamp}")
    with io.open(backup_path, "w", encoding="utf-8") as f:
        f.write(env_text)
    print(f"[1/3] .env 已备份 -> {backup_path}")

    hit = False
    out_lines = []
    for line in env_text.splitlines(keepends=True):
        m = KEY_LINE.match(line.rstrip("\r\n"))
        if m:
            hit = True
            eol = line[len(line.rstrip("\r\n")):]
            value = new_key if new_key else ""
            out_lines.append(f"{m.group(1)}{value}{eol}")
            print(f"[1/3] MORAY_LLM_API_KEY -> "
                  f"{'写入新 Key ' + mask(value) if new_key else '已置空'}")
        else:
            out_lines.append(line)
    if not hit:
        print("[1/3] ⚠ .env 里没找到 MORAY_LLM_API_KEY 行（无需改动）")
    else:
        with io.open(ENV_PATH, "w", encoding="utf-8", newline="") as f:
            f.writelines(out_lines)

    # ---- 2. cloud_providers 表：清空 api_key（参数绑定）----
    conn = sqlite3.connect(DB_PATH)
    try:
        before = conn.execute(
            "SELECT name, LENGTH(api_key) FROM cloud_providers"
        ).fetchall()
        for name, length in before:
            print(f"[2/3] 表内现状：{name} api_key {mask('x' * (length or 0)) if length else '(空)'}")
        conn.execute("UPDATE cloud_providers SET api_key = ?",
                     (new_key if new_key else "",))
        conn.commit()
        after = conn.execute(
            "SELECT name, LENGTH(api_key) FROM cloud_providers"
        ).fetchall()
        for name, length in after:
            print(f"[2/3] {name} api_key -> "
                  f"{'新 Key ' + mask('x' * length) if new_key else '已清空'}")
    finally:
        conn.close()

    # ---- 3. 收尾提示 ----
    if new_key:
        print("[3/3] 完成。重启 8000 后生效（safe_restart_backend.ps1）。")
    else:
        print("[3/3] 完成（置空模式）。把新 Key 填入 .env 的 MORAY_LLM_API_KEY，")
        print("      或 set MORAY_NEW_DEEPSEEK_KEY=新Key 后重跑本脚本，再重启 8000。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
