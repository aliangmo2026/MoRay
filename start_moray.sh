#!/usr/bin/env bash
# ============================================================
#  MoRay 一键启动（macOS / Linux）
#  1) 启动本地后端（scripts/start_backend.sh，独立后台）
#  2) 轮询 /api/health 就绪后，自动用默认浏览器打开应用
#  端口：默认 8000，换端口用： MORAY_PORT=8123 ./start_moray.sh
# ============================================================
set -e
cd "$(dirname "$0")"

PORT="${MORAY_PORT:-8000}"

echo "[MoRay] 正在启动本地后端（端口 $PORT）..."
"$PWD/scripts/start_backend.sh" &
BACKEND_PID=$!

echo "[MoRay] 等待后端就绪..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "[MoRay] 后端就绪，正在打开应用：http://127.0.0.1:$PORT/"
    python3 -c "import webbrowser; webbrowser.open('http://127.0.0.1:$PORT/')" >/dev/null 2>&1 || \
      (command -v open >/dev/null 2>&1 && open "http://127.0.0.1:$PORT/") || true
    break
  fi
  sleep 1
  if [ $i -eq 60 ]; then
    echo "[MoRay] 后端 60 秒内未就绪，请查看后端输出（Ctrl+C 停止）。"
  fi
done

wait $BACKEND_PID
