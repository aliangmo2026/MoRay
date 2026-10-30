#!/usr/bin/env bash
############################################################
# MoRay × ZCode 隔离验收脚本（硬闸门 / 设计文档 f 节，Day 8 决策 D4）
#
# 三项检查（对应任务书 a/b/c）：
#   A) 容器直连公网必须失败
#   B) 容器连 MoRay 网关（经边车代理 moray-gw-proxy:8000）必须成功
#      Day 12-13 架构师决策：internal 网到宿主无路由（Day 12 证据链），
#      由双网卡边车代理转发 moray-zcode-net:8000 → 宿主 127.0.0.1:8000；
#      代理定义在 docker-compose.yml，本脚本发现其不在运行时自动拉起。
#      本变更已同步到 verify_isolation.bat（两侧一致）。
#   C) 容器碰不到沙箱之外的宿主文件（rootfs 只读、无 docker.sock、
#      无宿主路径、无 .env、非 root、沙箱本身可写）
# 任何一项不通过 → exit 1 → ZCodeAdapter 禁止受理任务，不裸跑（f.4）。
#
# 运行（Windows 宿主，Git-Bash 或 WSL）：
#   bash D:/MoRay/engines/docker/verify_isolation.sh
# 前提：Docker Desktop 已启动；MoRay 后端在宿主 8000 端口运行；镜像已构建。
# Windows cmd/PowerShell 用户请改用同目录 verify_isolation.bat（语义等价）。
############################################################
set -u

IMAGE="${ZCODE_IMAGE:-moray/zcode-agent:0.16.9}"
NET="moray-zcode-net"
SANDBOX="${ZCODE_SANDBOX:-D:/MoRay/workspace/zcode_sandbox}"
GATEWAY="http://moray-gw-proxy:8000/api/health"
PROXY="moray-gw-proxy"
COMPOSE_FILE="$(dirname "$0")/docker-compose.yml"
FAILS=0

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1"; FAILS=$((FAILS+1)); }
sect() { printf '\n=== %s ===\n' "$1"; }

# 与生产运行完全一致的安全 flag（设计文档 f 节口径）
drun() {
  docker run --rm \
    --network "$NET" \
    --add-host host.docker.internal:host-gateway \
    --read-only --tmpfs /tmp:size=512m \
    --cap-drop=ALL --security-opt no-new-privileges \
    --user node \
    "$@"
}

############################################################
sect "0/3 前置检查（不满足直接 exit 1，不进入验收）"
############################################################
if ! docker info >/dev/null 2>&1; then
  echo "[FATAL] Docker daemon 不可达（未安装或未启动）。"
  echo "        这是 f.1 降级路径：提示用户安装/启动 Docker Desktop，"
  echo "        上层改派 LocalAdapter（仅写文件类任务），绝不宿主裸跑 ZCode。"
  exit 1
fi
pass "docker daemon 可达"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[FATAL] 镜像 $IMAGE 不存在。先按 README 构建（zcode-src/ 快照 + docker compose build）。"
  exit 1
fi
pass "镜像 $IMAGE 存在"

if ! docker network inspect "$NET" >/dev/null 2>&1; then
  echo "      网络 $NET 不存在，按 internal 语义创建..."
  docker network create --internal "$NET" >/dev/null || { echo "[FATAL] 网络创建失败"; exit 1; }
fi
if docker network inspect -f '{{.Internal}}' "$NET" 2>/dev/null | grep -q '^true$'; then
  pass "网络 $NET 存在且 internal=true（NAT 出站已切断）"
else
  fail "网络 $NET internal 不为 true —— 出站未切断，A 项必然无意义（f.4）"
fi

if [ ! -d "$SANDBOX" ]; then
  mkdir -p "$SANDBOX" && echo "      沙箱目录不存在，已创建：$SANDBOX"
fi
pass "沙箱目录就绪：$SANDBOX"

# B 项前置：确保网关边车代理在运行（Day 12-13 决策，定义见 docker-compose.yml）
if ! docker ps --format '{{.Names}}' | grep -qx "$PROXY"; then
  echo "      边车代理 $PROXY 未运行，docker compose up -d 拉起..."
  docker compose -f "$COMPOSE_FILE" up -d "$PROXY" >/dev/null 2>&1
  if ! docker ps --format '{{.Names}}' | grep -qx "$PROXY"; then
    echo "[FATAL] 边车代理 $PROXY 启动失败——手动执行 docker compose -f $COMPOSE_FILE up -d $PROXY 后重跑。B 项无法在无代理时执行。"
    exit 1
  fi
fi
pass "边车代理 $PROXY 运行中"

############################################################
sect "1/3 A 项：直连公网必须失败"
############################################################
if drun --entrypoint sh "$IMAGE" -c "curl -sS -m 10 -o /dev/null https://www.google.com" >/dev/null 2>&1; then
  fail "容器竟然连通了 https://www.google.com —— 出站未切断，禁止受理任何任务（f.4 硬闸门）"
else
  pass "curl https://www.google.com 失败（预期：出站被拒）"
fi
if drun --entrypoint sh "$IMAGE" -c "curl -sS -m 10 -o /dev/null https://example.com" >/dev/null 2>&1; then
  fail "容器竟然连通了 https://example.com —— 复测目标也通了，出站未切断（f.4）"
else
  pass "curl https://example.com 失败（预期：复测目标同样被拒）"
fi

############################################################
sect "2/3 B 项：连 MoRay 网关必须成功"
############################################################
GW_BODY="$(drun --entrypoint sh "$IMAGE" -c "curl -fsS -m 10 $GATEWAY" 2>&1)"
if [ $? -eq 0 ]; then
  pass "网关健康检查成功：$GW_BODY"
else
  fail "网关 $GATEWAY 不可达：$GW_BODY"
  echo "      排查顺序：① MoRay 后端是否在宿主 127.0.0.1:8000 运行（边车转发目标）"
  echo "                ② 边车代理状态：docker ps / docker logs $PROXY"
  echo "                ③ 边车到宿主通路：非 internal 桥经 host.docker.internal（vpnkit 落宿主回环），见设计文档 d 节"
fi

############################################################
sect "3/3 C 项：容器碰不到沙箱之外的宿主文件"
############################################################
if drun --entrypoint sh "$IMAGE" -c "touch /etc/moray_escape_test" >/dev/null 2>&1; then
  fail "C1 rootfs 竟然可写（touch /etc 成功）—— read-only 未生效"
else
  pass "C1 rootfs 只读（写 /etc 被拒）"
fi

if drun --entrypoint sh "$IMAGE" -c "test -e /var/run/docker.sock" >/dev/null 2>&1; then
  fail "C2 容器内可见 /var/run/docker.sock —— 等于交出宿主 Docker 全权"
else
  pass "C2 docker.sock 不可见"
fi

if drun --entrypoint sh "$IMAGE" -c 'for p in /mnt/d /moray /host /D /mnt/host; do [ -e "$p" ] && echo "$p" && exit 0; done; exit 1' >/dev/null 2>&1; then
  fail "C3 容器内可见宿主路径（/mnt/d、/moray 等之一）—— 挂载面越界"
else
  pass "C3 宿主路径（/mnt/d、/moray、/host、/D、/mnt/host）全部不可见"
fi

ENV_FOUND="$(drun --entrypoint sh "$IMAGE" -c 'find / -xdev -maxdepth 4 -name ".env" -not -path "/workspace/*" 2>/dev/null | head -n 5')"
if [ -n "$ENV_FOUND" ]; then
  fail "C4 容器内发现 .env 文件（宿主密钥面泄露）：$ENV_FOUND"
else
  pass "C4 全盘（rootfs 内）无 .env 文件"
fi

UID_N="$(drun --entrypoint id "$IMAGE" -u 2>/dev/null)"
if [ "$UID_N" = "1000" ]; then
  pass "C5 运行用户 uid=1000（node，非 root）"
else
  fail "C5 运行用户 uid=$UID_N（预期 1000/node）—— 非 root 约束未生效"
fi

if drun -v "$SANDBOX:/workspace" --entrypoint sh "$IMAGE" \
     -c "touch /workspace/.verify_write && rm /workspace/.verify_write" >/dev/null 2>&1; then
  pass "C6 沙箱挂载可写（/workspace 唯一合法持久写面正常）"
else
  fail "C6 沙箱挂载不可写 —— bind mount 或权限坏了，任务没法产出"
fi

############################################################
sect "验收汇总"
############################################################
if [ "$FAILS" -eq 0 ]; then
  echo "全部通过 —— f.4 硬闸门放行，ZCodeAdapter 可受理任务（缓存 TTL 5 分钟）。"
  echo "镜像重建/Docker 重启/网络变更后必须重跑本脚本。"
  exit 0
else
  echo "存在 $FAILS 项失败 —— 按 f.4 硬闸门：拒绝启动 ZCode、禁止受理任务、不裸跑。"
  echo "失败明细见上方 [FAIL] 行；隔离不可验证 = 引擎不可用，绝不'先跑起来再说'。"
  exit 1
fi
