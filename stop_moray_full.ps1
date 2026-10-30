# ============================================================
#  MoRay 全生态一键停止脚本  (stop_moray_full.ps1)
#  作用：
#    1) 按端口找到并结束 4 个本地 Python 服务进程
#    2) 停止 ZCode Docker 容器（只 stop，不删除容器）
#  所有端口 / 容器名集中在下方【可配置区】，与 start 脚本保持一致
# ============================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ======================= 可配置区（与 start_moray_full.ps1 保持一致） =======================

# 4 个本地 Python 服务：停止脚本按端口反查监听进程
$Services = @(
    [PSCustomObject]@{ Name='MoRay 中枢网关';  Port=8000 }
    [PSCustomObject]@{ Name='Laya 快速决策层'; Port=8001 }
    [PSCustomObject]@{ Name='Argus 情报官';    Port=8010 }
    [PSCustomObject]@{ Name='Iris 多模态感官';  Port=8004 }
)

# ZCode Docker 容器名（只停止，不删除）
$ZCodeContainer = 'zcode-engine'

# ============================================================ 配置区结束


# ---------- 彩色输出助手 ----------
function Write-Ok   { param([string]$msg) Write-Host $msg -ForegroundColor Green    }
function Write-Warn { param([string]$msg) Write-Host $msg -ForegroundColor Yellow   }
function Write-Err  { param([string]$msg) Write-Host $msg -ForegroundColor Red      }
function Write-Info { param([string]$msg) Write-Host $msg -ForegroundColor DarkGray }

# ---------- 按端口找到正在监听的进程 ----------
function Get-PortProcess {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return @() }
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    $list = @()
    foreach ($procId in $pids) {
        try { $list += Get-Process -Id $procId -ErrorAction Stop } catch { }
    }
    return $list
}

Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' MoRay 全生态停止 ' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan

# 1) 停 4 个 Python 服务（按端口找进程）
foreach ($svc in $Services) {
    Write-Host ''
    Write-Info ("---- {0}（端口 {1}）----" -f $svc.Name, $svc.Port)

    $procs = Get-PortProcess $svc.Port
    if (-not $procs) {
        Write-Info ("[跳过] {0} 未在运行（端口 {1} 无监听）" -f $svc.Name, $svc.Port)
        continue
    }

    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.Id -Force -ErrorAction Stop
            Write-Ok ("[停止] {0}（PID={1}）已结束" -f $svc.Name, $p.Id)
        } catch {
            Write-Err ("[失败] {0} PID={1}：{2}" -f $svc.Name, $p.Id, $_.Exception.Message)
        }
    }
}

# 2) 停 ZCode 容器（只 stop，不 rm）
Write-Host ''
Write-Info '---- ZCode 执行引擎（Docker）----'

$null = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Info '[跳过] Docker 守护进程未运行，无需停止容器'
} else {
    $state = & docker inspect -f '{{.State.Running}}' $ZCodeContainer 2>$null
    if (-not $state) {
        Write-Info "[跳过] 容器 $ZCodeContainer 不存在"
    } elseif ($state -eq 'false') {
        Write-Info "[跳过] 容器 $ZCodeContainer 已处于停止状态"
    } else {
        & docker stop $ZCodeContainer | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "[停止] 容器 $ZCodeContainer 已停止（未删除）"
        } else {
            Write-Err "[失败] 容器 $ZCodeContainer 停止失败"
        }
    }
}

Write-Host ''
Read-Host '全部操作完成，按回车键关闭本窗口'
