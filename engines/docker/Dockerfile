# syntax=docker/dockerfile:1
############################################################
# MoRay × ZCode 一次性隔离容器镜像
# 设计文档：D:\MoRay\docs\zcode_docker_isolation.md（b 节）
#
# 硬约束（验收口径，改任何一条须重跑 verify_isolation）：
#   - 从 ZCode 官方源码快照构建，不用桌面版（无 Electron/GUI/自动更新面）
#   - 镜像内零密钥：无 ENV apiKey、无 COPY .env、无凭证文件进任何镜像层
#     （provider 配置在运行期以只读 bind mount 注入，见 docker-compose.yml）
#   - 运行非 root（node 用户）、只读 rootfs —— 由 compose/run 参数强制，
#     镜像本身不自带任何提权面（无 setuid、无 sudo、cap 由运行时 drop）
#
# 构建前提：把 ZCode 源码快照放到本目录 zcode-src/ 下
#   （干净树：含 pnpm-workspace.yaml 与 apps/，排除 node_modules；见 .dockerignore）
# 构建命令（在本目录执行）：
#   docker compose build --build-arg ZCODE_COMMIT=<hash（若有）>
#   或 docker build -f Dockerfile -t moray/zcode-agent:0.16.9 .
############################################################

########## Stage 1: builder（联网构建，开发者受控动作） ##########
FROM node:24.14.0-slim AS builder
# 基础镜像钉死补丁版本：apps/zcode-cli engines.node = 24.14.0（Day 9 实测口径）

ARG ZCODE_COMMIT=unknown-no-git-metadata
ARG ZCODE_VERSION=0.16.9

WORKDIR /src

# 源码快照（构建上下文的 zcode-src/ 子目录）。快照树无 .git 元数据，
# 身份钉死靠 ZCODE_COMMIT 构建参数（可后补）+ 下方 BUILD_INFO 的 bundle sha256。
COPY zcode-src/ /src/

# 原生模块兜底：slim 无编译工具链，仅 builder 阶段安装（runtime 不带）
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential python3 \
 && rm -rf /var/lib/apt/lists/*

# packageManager 字段声明 pnpm@10.33.2，corepack 按版本激活（构建期联网拉取）
RUN corepack enable && corepack prepare pnpm@10.33.2 --activate

# HUSKY=0：跳过 prepare 钩子里的 husky（容器内无 .git，不跳过会安装失败）
# PLAYWRIGHT/PUPPETEER skip：无头容器不需要浏览器二进制
# ELECTRON skip：agent 镜像只要 CLI bundle，不需要 Electron 运行时（Day 11b-b 修复①）
ENV HUSKY=0 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    ELECTRON_SKIP_BINARY_DOWNLOAD=1

RUN pnpm install --frozen-lockfile

# 构建 CLI bundle。预期产物：apps/zcode-cli/packages/cli/dist/zcode.cjs
# 若 build:zcode 链路变化不产出该文件，下面的断言会让构建即刻失败（不产坏镜像）；
# 备选命令：pnpm --dir apps/zcode-cli cli:build（Day 11b 冒烟时核对）
#
# ZCODE_DIST_BASE_URL：build-zcode.mjs 的硬依赖，仅作为字符串写入发行组装产物
# install.sh / latest.json（二者不进本镜像），zcode.cjs 运行时不读 —— Day 11b-b
# 修复②：ARG 只存在于构建期，值用明显 placeholder，不落 .env、不进最终镜像。
# 注意：本 ARG 必须放在 pnpm install 层之后，否则击穿 install 缓存（30 分钟）。
ARG ZCODE_DIST_BASE_URL=https://dist.zcode.dev/v1
RUN pnpm --filter @zcode/cli... build \
 && test -f apps/zcode-cli/packages/cli/dist/zcode.cjs \
    || { echo "BUILD FAILED: dist/zcode.cjs not produced; try 'pnpm --dir apps/zcode-cli cli:build'"; exit 1; }

# 构建事实钉死：版本 + commit（若有）+ bundle sha256 + 构建时间。
# sha256 是可复核身份：docker run --rm <image> sha256sum /opt/zcode/zcode.cjs 可比对
RUN printf 'zcode_cli_version=%s\ncommit=%s\nbundle_sha256=%s\nbuilt_at=%s\n' \
      "$ZCODE_VERSION" "$ZCODE_COMMIT" \
      "$(sha256sum apps/zcode-cli/packages/cli/dist/zcode.cjs | cut -d' ' -f1)" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /src/BUILD_INFO

########## Stage 2: runtime（最小面，运行期默认无出站） ##########
FROM node:24.14.0-slim AS runtime

ARG ZCODE_COMMIT=unknown-no-git-metadata
ARG ZCODE_VERSION=0.16.9
LABEL org.opencontainers.image.title="moray-zcode-agent" \
      org.opencontainers.image.version="${ZCODE_VERSION}" \
      org.opencontainers.image.revision="${ZCODE_COMMIT}" \
      org.opencontainers.image.description="ZCode CLI headless runner for MoRay. Gateway-only egress, zero secrets baked in."

# curl + ca-certificates 仅用于 verify_isolation 网络验收（f 节）；
# 运行期容器无出站，curl 不构成额外能力面
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 只带构建产物，不带源码树/node_modules/构建工具链
COPY --from=builder /src/apps/zcode-cli/packages/cli/dist/zcode.cjs /opt/zcode/zcode.cjs
COPY --from=builder /src/BUILD_INFO /opt/zcode/BUILD_INFO

# CLI 入口（2.3）：zcode 进 PATH，任意工作目录可调
RUN printf '#!/bin/sh\nexec node /opt/zcode/zcode.cjs "$@"\n' > /usr/local/bin/zcode \
 && chmod 0755 /usr/local/bin/zcode

# 非 root：镜像自带 node 用户（UID 1000）。compose/run 侧再显式 user: node 双保险
USER node

# 默认工作目录 = 沙箱挂载点（compose 里 /workspace 是唯一持久挂载）
WORKDIR /workspace

# 缺省命令 = 无害冒烟；真实任务由 docker compose run / docker run 传参覆盖：
#   zcode -p - --mode edit --cwd /workspace/tasks/<id>/work --disallowed-tools ... --json
CMD ["zcode", "--help"]
