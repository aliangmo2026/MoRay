# -*- coding: utf-8 -*-
"""LocalLogAdapter：本地日志三源采集器（p_mcp 迭代·任务3，阶段4.1）。

collect() 依次尝试三个数据源（任务书 4.1）：
  1. 项目日志目录 {project_root}/logs/：读最新 .log 尾部 N 行，过滤 ERROR/Traceback
  2. 终端输出缓冲：进程内 ring buffer（最近 50 行）——
     ⚠ 诚实说明：MoRay 后端无法枚举其它进程的终端，本缓冲是"本进程可见"的
     终端输出汇聚点（push_line() 由任何跑在后端进程内的命令执行器喂数据）；
     没有生产者时该源为空，属预期，不是缺陷。
  3. Git 变更记录：最近 1 次 commit 的文件清单（subprocess，5s 硬超时，非 git 目录=跳过）

清洗：堆栈跟踪块提取（Python Traceback / Java Caused by / node at ...）、错误码、关键异常。
返回：{logs, stack_traces, git_changes, source, has_error_data, notes}
零第三方依赖；不写磁盘；所有 subprocess 带超时。
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

TAIL_LINES = 100          # 任务书：日志尾部 100 行
BUFFER_MAX = 50           # 任务书：终端缓冲最近 50 行
GIT_TIMEOUT = 5.0

_ERROR_LINE_RE = re.compile(
    r"\b(ERROR|FATAL|CRITICAL|Traceback|Exception|panic|Segmentation fault|core dumped|"
    r"失败|错误|异常|崩溃)\b", re.IGNORECASE)
_STACK_HEAD_RE = re.compile(r"^(Traceback \(most recent call last\):|Caused by:|"
                            r"^\s*at\s|\w+(\.\w+)*(Error|Exception)\b)")
_FRAME_RE = re.compile(r'^(?:\s+File "|	at \S|\s+at |Caused by:)')

# 进程内共享的终端输出环形缓冲（阶段4.2 路由与任何同进程执行器共用）
_TERMINAL_BUFFER: deque = deque(maxlen=BUFFER_MAX)


def push_terminal_line(line: str) -> None:
    _TERMINAL_BUFFER.append(str(line).rstrip("\n"))


def clear_terminal_buffer() -> None:
    _TERMINAL_BUFFER.clear()


class LocalLogAdapter:
    def __init__(self, terminal_buffer: Optional[deque] = None):
        self._buffer = terminal_buffer if terminal_buffer is not None else _TERMINAL_BUFFER

    # ---------------- 主入口 ----------------

    def collect(self, intent_hint: str = "", project_root: str = "") -> Dict[str, Any]:
        notes: List[str] = []
        root = self._resolve_root(intent_hint, project_root, notes)
        logs = self._read_logs(root)
        buffer = self._read_buffer()
        git = self._read_git(root, notes)

        error_lines = [l for l in (logs.get("tail") or []) + buffer
                       if _ERROR_LINE_RE.search(l)]
        traces = self._extract_traces((logs.get("tail") or []) + buffer)
        has = bool(error_lines or traces)

        return {
            "source": logs.get("file") or ("terminal_buffer" if error_lines and not root else
                                           ("project_dir" if root else "none")),
            "logs": {"file": logs.get("file"), "tail": logs.get("tail"),
                     "error_lines": error_lines[-40:],
                     "available": bool(logs.get("file"))},
            "terminal_buffer": {"lines": buffer[-BUFFER_MAX:], "available": bool(buffer)},
            "git_changes": git,
            "stack_traces": traces,
            "has_error_data": has,
            "project_root": str(root) if root else "",
            "notes": notes,
        }

    # ---------------- 源 1：项目日志 ----------------

    def _resolve_root(self, hint: str, explicit: str, notes: List[str]) -> Optional[Path]:
        cands: List[str] = []
        if explicit:
            cands.append(str(explicit))
        for token in re.findall(r"[A-Za-z]:\\[^\s,，。'\"（）()<>|]+|/(?:[\w.\-]+/)+[\w.\-]+",
                                hint or ""):
            cands.append(token)
        env = os.environ.get("MORAY_LOG_PROJECT", "").strip()
        if env:
            cands.append(env)
        for c in cands:
            p = Path(c)
            try:
                if p.is_dir():
                    return p
            except OSError:
                continue
        if cands:
            notes.append("意图文本含疑似路径但目录不存在：" + cands[0][:120])
        return None

    def _read_logs(self, root: Optional[Path]) -> Dict[str, Any]:
        if not root:
            return {"file": None, "tail": None}
        logs_dir = root / "logs"
        candidates: List[Path] = []
        if logs_dir.is_dir():
            candidates = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime,
                                reverse=True)
        if not candidates and root.is_dir():
            candidates = sorted(root.glob("*.log"), key=lambda p: p.stat().st_mtime,
                                reverse=True)
        if not candidates:
            return {"file": None, "tail": None}
        newest = candidates[0]
        tail = self._tail_lines(newest, TAIL_LINES)
        return {"file": str(newest), "tail": tail}

    @staticmethod
    def _tail_lines(path: Path, n: int, max_bytes: int = 256 * 1024) -> List[str]:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                block = min(size, max_bytes)
                f.seek(size - block)
                data = f.read()
        except OSError:
            return []
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", "replace")
        lines = text.splitlines()
        return lines[-n:]

    # ---------------- 源 2：终端缓冲 ----------------

    def _read_buffer(self) -> List[str]:
        return list(self._buffer)[-BUFFER_MAX:]

    # ---------------- 源 3：git ----------------

    def _read_git(self, root: Optional[Path], notes: List[str]) -> Optional[Dict[str, Any]]:
        if not root or not (root / ".git").exists():
            return None
        try:
            r = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--pretty=format:%h%x09%ad%x09%s",
                 "--date=iso"],
                capture_output=True, timeout=GIT_TIMEOUT)
            if r.returncode != 0:
                notes.append("git log 失败：" + (r.stderr or b"")[:200].decode("utf-8", "replace"))
                return None
            head = r.stdout.decode("utf-8", "replace").strip()
            r2 = subprocess.run(["git", "-C", str(root), "show", "--stat", "--name-only",
                                 "--pretty=format:", "HEAD"],
                                capture_output=True, timeout=GIT_TIMEOUT)
            files = [l.strip() for l in r2.stdout.decode("utf-8", "replace").splitlines()
                     if l.strip()][:50]
            parts = head.split("\t")
            return {"commit": parts[0] if parts else "", "date": parts[1] if len(parts) > 1 else "",
                    "subject": parts[2] if len(parts) > 2 else "", "files": files}
        except FileNotFoundError:
            notes.append("git 命令不存在")
        except subprocess.TimeoutExpired:
            notes.append("git 查询超时（>5s）")
        except Exception as e:  # noqa: BLE001
            notes.append(f"git 异常：{type(e).__name__}")
        return None

    # ---------------- 清洗：堆栈块提取 ----------------

    @staticmethod
    def _extract_traces(lines: List[str], max_blocks: int = 4) -> List[str]:
        blocks: List[List[str]] = []
        cur: List[str] = []
        for ln in lines:
            if _STACK_HEAD_RE.match(ln.strip()) or (cur and (_FRAME_RE.match(ln) or ln.startswith("    "))):
                cur.append(ln)
            else:
                if len(cur) >= 2:
                    blocks.append(cur)
                cur = []
        if len(cur) >= 2:
            blocks.append(cur)
        # 关键异常行也单独成块（无多行 Traceback 的场景）
        out = ["\n".join(b).strip() for b in blocks[:max_blocks]]
        if not out:
            for ln in reversed(lines):
                if _ERROR_LINE_RE.search(ln) and len(ln.strip()) > 12:
                    out.append(ln.strip())
                    if len(out) >= 3:
                        break
        return [b[:3000] for b in out]


def summarize_for_prompt(collected: Dict[str, Any], max_chars: int = 4000) -> str:
    """把采集结果压成送入 iris_analyze_text 的报错文本。"""
    parts: List[str] = []
    if collected.get("stack_traces"):
        parts.append("【堆栈跟踪】")
        parts.extend(collected["stack_traces"])
    if collected.get("logs", {}).get("error_lines"):
        parts.append("【错误行】")
        parts.extend(collected["logs"]["error_lines"][-15:])
    if collected.get("git_changes"):
        g = collected["git_changes"]
        parts.append(f"【最近提交】{g['commit']} {g['subject']}；涉及文件：{', '.join(g['files'][:15])}")
    text = "\n".join(parts)
    return text[:max_chars]
