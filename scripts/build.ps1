# MoRay Tauri 构建脚本（Windows PowerShell）
# 用法：powershell -File scripts/build.ps1 [bundle格式...]  例：scripts/build.ps1 msi nsis
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Targets
)

$ErrorActionPreference = 'Stop'

# 检查 tauri CLI
if (-not (Get-Command cargo-tauri -ErrorAction SilentlyContinue)) {
    Write-Host '[MoRay] 未检测到 tauri CLI，正在安装 @tauri-apps/cli...' -ForegroundColor Yellow
    npm i -g @tauri-apps/cli
}

Write-Host '[MoRay] 开始构建...' -ForegroundColor Cyan
if ($Targets -and $Targets.Count -gt 0) {
    cargo tauri build --bundles @($Targets)
} else {
    cargo tauri build
}

Write-Host ''
Write-Host '[MoRay] 构建完成，产物位于 src-tauri\target\release\bundle\' -ForegroundColor Green
Get-ChildItem -Recurse src-tauri\target\release\bundle -Include *.msi,*.exe,*.dmg,*.AppImage,*.deb -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ('  ' + $_.FullName) }
