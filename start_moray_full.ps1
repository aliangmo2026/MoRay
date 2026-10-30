# ============================================================
#  MoRay 全生态一键启动脚本  (start_moray_full.ps1)
#  作用：
#    1) 后台隐藏窗口启动 4 个本地 Python 服务，stdout/stderr 全部落盘
#       （重定向日志同时解决 Rich/click 报的
#        'NoneType object has no attribute isatty' 问题）
#    2) 最后启动 ZCode Docker 执行引擎
#  所有路径 / 端口 / 参数均集中在下方【可配置区】，改这里即可
# ============================================================

# ---------- 控制台按 UTF-8 输出，避免中文乱码 ----------
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ---------- 子进程环境变量：无缓冲 + UTF-8 + 关闭彩色终端探测 ----------
# 子进程会继承这些变量；TERM=dumb 可让 Rich/click 不再尝试 isatty 探测
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'
$env:TERM             = 'dumb'

# ======================= 可配置区（改这里） =======================

# 日志目录（自动创建）
$LogDir = 'D:\MoRay\logs'

# Python 服务统一监听地址
#   '127.0.0.1' = 仅本机访问（推荐）；'0.0.0.0' = 局域网可访问
$ListenHost = '127.0.0.1'

# ---------- 4 个本地 Python 服务 ----------
#   Name    : 控制台显示名
#   LogName : 日志文件名前缀，实际生成 <LogName>.out.log / <LogName>.err.log
#   Port    : 监听端口（启动前会自动检测占用）
#   WorkDir : 工作目录（服务进程会切到这里再启动）
#   Py      : Python 解释器路径（优先用项目自己的 .venv\Scripts\python.exe；
#             直接写 'python' 表示走系统 PATH）
#   Mode    : 'uvicorn' = python -m uvicorn <Entry> --host .. --port ..
#             'script'   = python <Entry>
#   Entry   : uvicorn 形式为 "模块:app"；script 形式为脚本文件名
#   Env     : 可选，启动前注入子进程的环境变量（哈希表）；不需要就留 $null
$Services = @(
    [PSCustomObject]@{ Name='MoRay 中枢网关';  LogName='moray_main';   Port=8000; WorkDir='D:\MoRay';                                Py='D:\MoRay\server\.venv\Scripts\python.exe'; Mode='uvicorn'; Entry='server.app.main:app';                     Env=$null }
    # Laya 注入 LAYA_ARGUS_BASE：Argus 统一监听 8010（与主服务 config.ARGUS_BASE_URL 契约一致），laya 编排的 argus 工具链路随之对齐
    # Py 必须用 3.12 解释器（与 electron 启动器一致）：系统 PATH 的 python 是 3.8，无 fastapi
    [PSCustomObject]@{ Name='Laya 快速决策层'; LogName='laya_server';  Port=8001; WorkDir='D:\MoRay\decision-kernel\lora-training'; Py='D:\python3.12\python.exe';                   Mode='script';   Entry='laya_std_server.py';                     Env=@{ 'LAYA_ARGUS_BASE'='http://127.0.0.1:8010' } }
    # Argus：真实入口为 argus.main:argus_app，venv 在 argus 子目录里；端口 8010 = 主服务订阅投递/argus_proxy 的既定契约（config.ARGUS_BASE_URL 默认值）
    [PSCustomObject]@{ Name='Argus 情报官';    LogName='argus_server'; Port=8010; WorkDir='D:\Argus';                              Py='D:\Argus\argus\.venv\Scripts\python.exe';   Mode='uvicorn'; Entry='argus.main:argus_app';                   Env=$null }
    # Iris：真实入口是 server\mcp_server.py，端口/网关地址走环境变量（IRIS_MCP_PORT 不写死代码）
    [PSCustomObject]@{ Name='Iris 多模态感官'; LogName='iris_server';  Port=8004; WorkDir='D:\Iris\server';                         Py='D:\MoRay\server\.venv\Scripts\python.exe'; Mode='script';   Entry='mcp_server.py';                          Env=@{ 'IRIS_MCP_PORT'='8004'; 'IRIS_MCP_API_KEY'='morayIrisKey2026'; 'IRIS_GATEWAY_URL'='http://127.0.0.1:8000' } }
    # ZCode 沙箱 REST：本地正式实装 8005（替代从未落地的 zcode-engine Docker 容器路线），tool_registry zcode.* 原子工具的对端
    [PSCustomObject]@{ Name='ZCode 沙箱 REST'; LogName='zcode_sandbox'; Port=8005; WorkDir='D:\MoRay\decision-kernel\lora-training'; Py='D:\python3.12\python.exe';                   Mode='script';   Entry='zcode_sandbox_server.py';                Env=@{ 'LAYA_ZCODE_SANDBOX_ROOT'='D:\MoRay\workspace\mapped' } }
)

# ---------- ZCode Docker 执行引擎 ----------
$ZCodeContainer     = 'zcode-engine'        # 容器名
$ZCodeImage         = 'zcode-engine:latest'  # 首次 docker run 时使用的镜像（按需改成实际镜像名:tag）
$ZCodeHostPort      = 8005                  # 宿主机端口
$ZCodeContainerPort = 8000                  # 容器内端口
$ZCodeExtraRunArgs  = '--restart=no'        # docker run 额外参数（空格分隔，如 '--memory 4g --cpus 2'）

# ============================================================ 配置区结束


# ---------- 彩色输出助手：成功绿 / 警告黄 / 错误红 / 信息灰 ----------
function Write-Ok   { param([string]$msg) Write-Host $msg -ForegroundColor Green  }
function Write-Warn { param([string]$msg) Write-Host $msg -ForegroundColor Yellow }
function Write-Err  { param([string]$msg) Write-Host $msg -ForegroundColor Red    }
function Write-Info { param([string]$msg) Write-Host $msg -ForegroundColor DarkGray }

# ---------- 端口是否已被监听占用 ----------
function Test-PortBusy {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# ---------- 启动一个本地 Python 服务 ----------
function Start-PyService {
    param($Svc)

    Write-Host ''
    Write-Info ("---- {0}（端口 {1}）----" -f $Svc.Name, $Svc.Port)

    # 1) 端口占用检查
    if (Test-PortBusy $Svc.Port) {
        Write-Warn ("[跳过] 端口 {0} 已被占用，{1} 可能已在运行" -f $Svc.Port, $Svc.Name)
        return [PSCustomObject]@{ Name=$Svc.Name; Url=("http://{0}:{1}" -f $ListenHost,$Svc.Port); Status='已运行(端口占用跳过)'; Color='Yellow' }
    }

    # 2) 工作目录检查
    if (-not (Test-Path $Svc.WorkDir)) {
        Write-Err ("[失败] 工作目录不存在：{0}" -f $Svc.WorkDir)
        return [PSCustomObject]@{ Name=$Svc.Name; Url='-'; Status='启动失败(目录缺失)'; Color='Red' }
    }

    # 3) Python 解释器检查
    $pyExe = $Svc.Py
    if ($pyExe -ne 'python' -and $pyExe -ne 'py' -and -not (Test-Path $pyExe)) {
        Write-Err ("[失败] Python 解释器不存在：{0}（请到顶部配置区修改）" -f $pyExe)
        return [PSCustomObject]@{ Name=$Svc.Name; Url='-'; Status='启动失败(解释器缺失)'; Color='Red' }
    }

    # 4) 组装命令行参数
    if ($Svc.Mode -eq 'uvicorn') {
        $argList = @('-m', 'uvicorn', $Svc.Entry, '--host', $ListenHost, '--port', $Svc.Port.ToString())
    } else {
        $argList = @($Svc.Entry)
    }

    # 5) 日志路径（注意：Start-Process 要求 stdout / stderr 必须是两个不同文件）
    $outLog = Join-Path $LogDir ($Svc.LogName + '.out.log')
    $errLog = Join-Path $LogDir ($Svc.LogName + '.err.log')

    # 6) 注入本服务专属环境变量（启动后还原，避免污染后续服务）
    $savedEnv = @{}
    if ($Svc.Env) {
        foreach ($k in $Svc.Env.Keys) {
            $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k)
            [Environment]::SetEnvironmentVariable($k, $Svc.Env[$k], 'Process')
        }
    }

    # 7) 后台隐藏窗口启动 + 日志重定向（不弹任何黑框）
    try {
        $proc = Start-Process -FilePath $pyExe `
                              -ArgumentList $argList `
                              -WorkingDirectory $Svc.WorkDir `
                              -WindowStyle Hidden `
                              -RedirectStandardOutput $outLog `
                              -RedirectStandardError  $errLog `
                              -PassThru
        # 子进程已继承环境变量，立即还原父进程现场
        foreach ($k in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], 'Process') }
        Write-Ok ("[启动] PID={0}  日志：{1}.out.log / {1}.err.log" -f $proc.Id, $Svc.LogName)
    } catch {
        foreach ($k in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], 'Process') }
        Write-Err ("[失败] {0}" -f $_.Exception.Message)
        return [PSCustomObject]@{ Name=$Svc.Name; Url='-'; Status='启动失败'; Color='Red' }
    }

    # 8) [启动编排优化 2026-09-30] 启动后存活校验：端口探测 → 重试 → 结果上报。
    #    Python 服务就绪有延迟（uvicorn 绑定端口前要先完成 import），不做轮询会把
    #    "还在启动中"误判为成功；此前的静默缺失正是 ConnectError 刷屏的根因之一。
    $maxRetry = 5
    $retryIntervalSec = 2
    $retryCount = 0
    $started = $false
    while ($retryCount -lt $maxRetry -and -not $started) {
        Start-Sleep -Seconds $retryIntervalSec
        if (Test-PortBusy $Svc.Port) {
            $started = $true
            Write-Ok ("[校验] 端口 {0} 已监听，{1} 存活" -f $Svc.Port, $Svc.Name)
        } else {
            $retryCount++
            Write-Info ("  等待启动中... ({0}/{1})" -f $retryCount, $maxRetry)
        }
    }
    if ($started) {
        return [PSCustomObject]@{ Name=$Svc.Name; Url=("http://{0}:{1}" -f $ListenHost,$Svc.Port); Status='已启动(端口已监听)'; Color='Green' }
    }
    Write-Warn ("[超时] {0} 在 {1}×{2}s 内端口 {3} 未监听，该服务功能暂不可用（进程未强杀，可看日志继续排查）" -f $Svc.Name, $maxRetry, $retryIntervalSec, $Svc.Port)
    Write-Info ("    排查入口：{0}.err.log / 手动命令：cd {1}; & '{2}' {3}" -f $Svc.LogName, $Svc.WorkDir, $pyExe, ($argList -join ' '))
    return [PSCustomObject]@{ Name=$Svc.Name; Url='-'; Status='启动超时(端口未监听)'; Color='Red' }
}

# ---------- Docker 守护进程是否可用 ----------
function Test-DockerDaemon {
    $null = & docker info 2>&1
    return ($LASTEXITCODE -eq 0)
}

# ---------- 启动 ZCode 容器：不存在就 docker run，已存在就 docker start ----------
function Start-ZCode {
    Write-Host ''
    Write-Info '---- ZCode 执行引擎（Docker）----'

    if (-not (Test-DockerDaemon)) {
        Write-Err '[失败] 检测不到 Docker 守护进程。请先启动 Docker Desktop，等待鲸鱼图标变绿后再运行本脚本。'
        return [PSCustomObject]@{ Name='ZCode 执行引擎'; Url=("http://{0}:{1}" -f $ListenHost,$ZCodeHostPort); Status='未启动(Docker未运行)'; Color='Red' }
    }

    $exists = & docker inspect -f '{{.Name}}' $ZCodeContainer 2>$null
    if ($exists) {
        Write-Info '[Docker] 容器已存在，执行 docker start ...'
        & docker start $ZCodeContainer | Out-Null
    } else {
        Write-Info '[Docker] 容器不存在，执行 docker run 创建 ...'
        $runArgs = @('-d', '--name', $ZCodeContainer, '-p', ("{0}:{1}" -f $ZCodeHostPort,$ZCodeContainerPort))
        if ($ZCodeExtraRunArgs) { $runArgs += ($ZCodeExtraRunArgs -split '\s+') }
        $runArgs += $ZCodeImage
        & docker run @runArgs | Out-Null
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Ok ("[启动] 容器 {0} 已运行，端口 {1}:{2}" -f $ZCodeContainer, $ZCodeHostPort, $ZCodeContainerPort)
        return [PSCustomObject]@{ Name='ZCode 执行引擎'; Url=("http://{0}:{1}" -f $ListenHost,$ZCodeHostPort); Status='已启动(Docker)'; Color='Green' }
    } else {
        Write-Err "[失败] 容器启动失败，请执行  docker logs $ZCodeContainer  查看原因"
        return [PSCustomObject]@{ Name='ZCode 执行引擎'; Url=("http://{0}:{1}" -f $ListenHost,$ZCodeHostPort); Status='启动失败'; Color='Red' }
    }
}

# ======================= 主流程 =======================

# 0) 创建日志目录
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Info "已创建日志目录：$LogDir"
}

$results = @()

# 1) 先按顺序启动 4 个本地 Python 服务
foreach ($svc in $Services) { $results += Start-PyService $svc }

# 2) 最后启动 ZCode 容器
$results += Start-ZCode

# 3) 汇总面板
Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' MoRay 全生态启动结果 ' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
foreach ($r in $results) {
    Write-Host ("  {0,-14}  {1,-30}  {2}" -f $r.Name, $r.Url, $r.Status) -ForegroundColor $r.Color
}

# 4) 收尾提示
Write-Host ''
Write-Info '提示：各组件之间的联动开关由 D:\MoRay\config.yaml 控制。'
Write-Info '停止全部服务请运行：D:\MoRay\stop_moray_full.ps1'
Write-Host ''
Read-Host '按回车键关闭本窗口'
