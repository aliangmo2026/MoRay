#!/usr/bin/env bash
# ============================================================
#  MoRay 本地后端一键启动（macOS / Linux）
#  自动：找 python3 -> 建 venv -> 装依赖(官方源失败回退阿里镜像)
#        -> 端口占用检测 -> uvicorn 启动 -> 自动打开健康检查
#  端口：默认 8000，换端口用： MORAY_PORT=8123 ./scripts/start_backend.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."

VENV="server/.venv"
PORT="${MORAY_PORT:-8000}"

# ---- 1. 找 Python ----
if ! command -v python3 >/dev/null 2>&1; then
  echo "[MoRay] 未检测到 Python 3.11+，请先安装：https://www.python.org/downloads/"
  exit 1
fi

# ---- 2. 虚拟环境（不存在才创建）----
if [ ! -x "$VENV/bin/python" ]; then
  echo "[MoRay] 首次运行：创建虚拟环境 $VENV ..."
  python3 -m venv "$VENV"
fi

# ---- 3. 安装依赖（官方源失败回退阿里云镜像；已装则秒级跳过）----
echo "[MoRay] 安装依赖（fastapi, uvicorn[standard]）..."
if ! "$VENV/bin/python" -m pip install -q --timeout 30 --retries 1 -r server/requirements.txt >/dev/null 2>&1; then
  echo "[MoRay] 官方源安装失败，回退阿里云镜像重试..."
  "$VENV/bin/python" -m pip install -q --timeout 30 --retries 1 -r server/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ >/dev/null 2>&1
fi

# ---- 4. 端口占用检测（友好提示而非堆栈）----
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "[MoRay] 端口 $PORT 已被占用。"
  echo "[MoRay] 请换端口后重试： MORAY_PORT=8123 ./scripts/start_backend.sh"
  exit 1
fi

# ---- 4.5 [修复] VENV 转绝对路径：后续 cd server 后相对路径会失效 ----
VENV_ABS="$(cd "$(dirname "$VENV")" && pwd)/$(basename "$VENV")"

# ---- 5. 启动 uvicorn（延迟 2 秒自动打开健康检查）----
echo
echo "[MoRay] 后端地址：  http://127.0.0.1:$PORT"
echo "[MoRay] 健康检查：  http://127.0.0.1:$PORT/api/health"
echo "[MoRay] MoRay 后端运行中，Ctrl+C 停止服务。"
echo
( sleep 2 && python3 -c "import webbrowser; webbrowser.open('http://127.0.0.1:$PORT/api/health')" >/dev/null 2>&1 ) &
cd server
exec "$VENV_ABS/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
