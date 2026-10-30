# -*- coding: utf-8 -*-
"""ZCode 沙箱 REST 服务（正式化实装，2026-10-03）—— 127.0.0.1:8005。

替代从未落地的 Docker 容器路线（zcode-engine:latest 镜像未构建、容器名
zcode-engine/zcode-sandbox 两处不一致），对齐 tool_registry._zcode_request 契约：
HTTP 200 + JSON 即 ok=True，客户端不检查任何 body 字段，schema 由本服务单方定义。

7 条路由（与 modules/tool_registry.py:504-606 的 zcode.* 原子工具一一对应）：
  POST /api/fs/list        {path}                        -> {dir, count, files:[{name,path,is_dir,size}]}
  POST /api/fs/read        {path}                        -> {path, content, size}
  POST /api/fs/write       {path, content}               -> {ok, path, written}
  POST /api/fs/edit        {path, line_start, line_end, content} -> {ok, path, lines_total, replaced_from, replaced_to, written}
  POST /api/cmd/exec       {command, cwd, timeout}       -> {exit_code, stdout, stderr, cwd}
  POST /api/proc/kill      {pid}                         -> {ok, pid}
  GET  /api/net/port/{port}                              -> {port, open}
  GET  /health                                          -> 存活探针（registry_status/启动校验用）

安全边界（硬，本机单用户受信编排场景）：
  * 所有文件路径 realpath 后必须落在 ROOT 之内（== 或 startswith ROOT+sep），
    越界一律 400；ROOT = 环境变量 LAYA_ZCODE_SANDBOX_ROOT，默认 D:\\MoRay\\workspace\\mapped
    （与 core/file_proxy 的 authorized_paths.json sandbox_root 同源，不新增概念）。
  * cmd_exec 的 cwd 同样必须在 ROOT 内；命令本身不设白名单——本服务只监听
    127.0.0.1、仅被 Laya 编排链（tool_registry zcode.* 工具）调用，等价于把
    shell 交给受信编排器；公网/多用户场景必须先加白名单再暴露。
  * proc_kill 不校验进程归属（同上受信前提），仅拒绝 pid<=4（系统保留）与
    本服务自身 PID，防自噬。
独立可运行：D:\\python3.12\\python.exe zcode_sandbox_server.py（uvicorn 自绑 127.0.0.1:8005）。
"""
import os
import signal
import socket
import subprocess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

ROOT = os.environ.get("LAYA_ZCODE_SANDBOX_ROOT", r"D:\MoRay\workspace\mapped")
_ROOT_REAL = os.path.realpath(ROOT)
os.makedirs(_ROOT_REAL, exist_ok=True)

_CMD_TIMEOUT_MAX = 60      # 单条命令上限（客户端 HTTP 超时 = 命令 timeout + 5，见 tool_registry）
_OUTPUT_CAP = 8000         # stdout/stderr 截断（防巨量输出灌爆 decision 上下文）

app = FastAPI(title="ZCode Sandbox REST (8005)")


def _under_root(real_path: str) -> bool:
    """realpath 后的前缀校验（大小写不敏感；拒绝 'mapped_evil' 这类纯字符串前缀）。"""
    rp = os.path.realpath(real_path).lower()
    base = _ROOT_REAL.lower()
    return rp == base or rp.startswith(base + os.sep)


def _resolve(p: str) -> str:
    """路径解析 + 越界拦截：相对路径锚定 ROOT；越界 HTTPException 400。"""
    raw = str(p or "").strip() or "."
    if not os.path.isabs(raw):
        raw = os.path.join(_ROOT_REAL, raw)
    rp = os.path.realpath(raw)
    if not _under_root(rp):
        raise HTTPException(status_code=400,
                            detail="路径越界（沙箱根 %s）：%s" % (_ROOT_REAL, rp))
    return rp


class PathBody(BaseModel):
    path: str = ""


class WriteBody(PathBody):
    content: str = ""


class EditBody(PathBody):
    content: str = ""
    line_start: int = 1
    line_end: int = 10 ** 9


class CmdBody(BaseModel):
    command: str = ""
    cwd: str = ""
    timeout: int = 30


class KillBody(BaseModel):
    pid: int


@app.get("/health")
def health():
    return {"ok": True, "service": "zcode-sandbox-rest", "root": _ROOT_REAL,
            "pid": os.getpid()}


@app.post("/api/fs/list")
def fs_list(body: PathBody):
    rp = _resolve(body.path)
    if not os.path.isdir(rp):
        if not os.path.exists(rp):
            raise HTTPException(status_code=404, detail="目录不存在：%s" % rp)
        raise HTTPException(status_code=400, detail="不是目录：%s" % rp)
    files = []
    for name in sorted(os.listdir(rp)):
        fp = os.path.join(rp, name)
        is_dir = os.path.isdir(fp)
        files.append({"name": name, "path": fp, "is_dir": is_dir,
                      "size": None if is_dir else os.path.getsize(fp)})
    return {"dir": rp, "count": len(files), "files": files}


@app.post("/api/fs/read")
def fs_read(body: PathBody):
    rp = _resolve(body.path)
    if not os.path.isfile(rp):
        raise HTTPException(status_code=404, detail="文件不存在：%s" % rp)
    # errors=replace：只读展示用，坏字节显示为 U+FFFD，不影响原文件
    with open(rp, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return {"path": rp, "content": content, "size": len(content)}


@app.post("/api/fs/write")
def fs_write(body: WriteBody):
    rp = _resolve(body.path)
    os.makedirs(os.path.dirname(rp) or _ROOT_REAL, exist_ok=True)
    data = body.content.encode("utf-8")
    with open(rp, "wb") as f:
        f.write(data)
    return {"ok": True, "path": rp, "written": len(data)}


@app.post("/api/fs/edit")
def fs_edit(body: EditBody):
    """真行号语义：把 [line_start, line_end] 闭区间的行整体替换为 content。

    line_end 超出文件行数时截到末尾；content 不以换行结尾且后方还有剩余行时
    自动补换行，避免与下一行粘连。executor 的"整文件重写"缺省（1..1e9）恰好
    退化为全量覆盖。surrogateescape 读写保证非 UTF-8 字节往返无损。
    """
    rp = _resolve(body.path)
    if not os.path.isfile(rp):
        raise HTTPException(status_code=404, detail="文件不存在：%s" % rp)
    with open(rp, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        lines = f.readlines()
    start = max(1, int(body.line_start))
    end = min(len(lines), int(body.line_end))
    end = max(end, start - 1)
    tail = lines[end:]
    content = body.content
    if tail and content and not content.endswith("\n"):
        content += "\n"
    new_lines = lines[:start - 1] + [content] + tail
    with open(rp, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.writelines(new_lines)
    written = len(body.content.encode("utf-8"))
    return {"ok": True, "path": rp, "lines_total": len(new_lines),
            "replaced_from": start, "replaced_to": end, "written": written}


@app.post("/api/cmd/exec")
def cmd_exec(body: CmdBody):
    if not body.command.strip():
        raise HTTPException(status_code=400, detail="command 不能为空")
    # cwd 与文件路径同一道墙：必须在 ROOT 内（空 = ROOT 默认）
    rcwd = _resolve(body.cwd)
    if not os.path.isdir(rcwd):
        raise HTTPException(status_code=400, detail="cwd 不是目录：%s" % rcwd)
    timeout = max(1, min(int(body.timeout or 30), _CMD_TIMEOUT_MAX))
    try:
        r = subprocess.run(body.command, shell=True, cwd=rcwd,
                           capture_output=True, timeout=timeout)
        stdout = r.stdout.decode("utf-8", errors="replace")
        stderr = r.stderr.decode("utf-8", errors="replace")
        code = r.returncode
    except subprocess.TimeoutExpired as e:
        # 显式契约（非吞异常）：超时按 stub 先例回 200 + exit_code=-1，输出带回已捕获部分
        stdout = (e.stdout or b"").decode("utf-8", errors="replace")
        stderr = "command timeout after %ds" % timeout
        code = -1
    except OSError as e:
        raise HTTPException(status_code=400,
                            detail="命令启动失败：%s: %s" % (type(e).__name__, e))
    return {"exit_code": code, "stdout": stdout[:_OUTPUT_CAP],
            "stderr": stderr[:_OUTPUT_CAP], "cwd": rcwd}


@app.post("/api/proc/kill")
def proc_kill(body: KillBody):
    if body.pid <= 4:
        raise HTTPException(status_code=400, detail="拒绝终止系统保留 PID：%d" % body.pid)
    if body.pid == os.getpid():
        raise HTTPException(status_code=400, detail="拒绝终止本服务自身")
    try:
        os.kill(body.pid, signal.SIGTERM)
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail="进程不存在：%d" % body.pid)
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权终止进程：%d" % body.pid)
    return {"ok": True, "pid": body.pid, "signal": "SIGTERM"}


@app.get("/api/net/port/{port}")
def net_port(port: int):
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="port 需在 1~65535，收到=%d" % port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        opened = s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()
    return {"port": port, "open": opened}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8005, log_level="error", access_log=False)
