#!/usr/bin/env sh
# MoRay Tauri 构建脚本（macOS / Linux）
# 用法：sh scripts/build.sh [bundle格式...]  例：sh scripts/build.sh dmg
set -e

TARGETS="$@"

if ! command -v cargo-tauri >/dev/null 2>&1; then
  echo "[MoRay] 未检测到 tauri CLI，正在安装 @tauri-apps/cli..."
  npm i -g @tauri-apps/cli
fi

echo "[MoRay] 开始构建..."
if [ -n "$TARGETS" ]; then
  # shellcheck disable=SC2086
  cargo tauri build --bundles $TARGETS
else
  cargo tauri build
fi

echo ""
echo "[MoRay] 构建完成，产物位于 src-tauri/target/release/bundle/"
find src-tauri/target/release/bundle -type f \( -name '*.dmg' -o -name '*.AppImage' -o -name '*.deb' -o -name '*.msi' -o -name '*.exe' \) 2>/dev/null | sed 's/^/  /'
