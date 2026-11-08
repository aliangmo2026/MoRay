#Requires -Version 5.1
<#
 =============================================================================
  MoRay 全套服务一键启动器      v1.0.1     2026-10-08
 -----------------------------------------------------------------------------
  幂等：已在监听的端口会跳过，只拉起没起来的服务；重复运行安全。

  服务 / 端口 / 启动命令
    11434  Ollama     D:\Ollama\app\ollama.exe serve
    8000   MoRay      cmd /c D:\MoRay\_start_moray.bat            (cwd D:\MoRay)
    8001   Laya       D:\python3.12\python.exe laya_std_server.py
                       (cwd D:\MoRay\decision-kernel\lora-training)
    8005   Sandbox    D:\python3.12\python.exe zcode_sandbox_server.py  (同 cwd)
    8010   Argus      cmd /c D:\Argus\start_argus.bat             (cwd D:\Argus)
    9301   Iris-SSE   powershell -File D:\Iris\server\start_server.ps1
    8004   Iris-RPC   D:\MoRay\server\.venv\Scripts\python.exe D:\Iris\server\mcp_server.py
                       (cwd D:\Iris\server)
    9310   Scribe     E:\moray-scribe\.venv\Scripts\python.exe -m moray_scribe.mcp.http_main
                       (cwd E:\moray-scribe —— out/ 相对项目根，必须指向项目根)
    9311   Surgeon    D:\python3.12\python.exe -m moray_surgeon.mcp_server
                       (cwd E:\moray-surgeon; MORAY_MCP_TRANSPORT=streamable-http)
    9312   AIOps      E:\moray-aio\.venv\Scripts\python.exe -m app.tools.mcp_http
                       (cwd E:\moray-aio; FastAPI 包手写 JSON-RPC)

  注意：9301 与 8004 是同一个 mcp_server.py 的两个实例，唯一区别是
  IRIS_MCP_PORT（不设 = 默认 9301；显式设 8004）。不是两个不同的程序。
  9310/9311/9312 均为 streamable-HTTP，绑定 127.0.0.1，路径统一 /mcp；
  FastMCP 两个器官用 stateless_http + json_response（无 initialize/会话头）。

  设计说明（为什么这么写）
    * 环境变量按服务隔离。PowerShell 5.1 的 Start-Process 没有 -Environment 参数
      （那是 PS7 的）；且 PS5.1 跑在 .NET Framework 上，UseShellExecute=$true 时
      EnvironmentVariables 会被静默忽略。所以统一走
      [System.Diagnostics.ProcessStartInfo] + UseShellExecute=$false。
      本脚本不写调用者会话的 $env:，不留残留。
    * .bat 只能经 cmd.exe /c 启动（CreateProcess 不能直接执行 bat）。
    * PID 一律回探 netstat 取“真监听者”，不用启动器返回值——MoRay / Argus 走 bat，
      进程链是 cmd.exe -> python.exe，返回值只是 cmd 的 PID。
    * 等待策略：每 0.7s 探一次端口，出现即继续；最多等各服务自己的预算秒数。
    * 探针带重试（v1.0.1）：端口刚 LISTEN 时服务未必已经能应答 HTTP。Ollama 就是
      典型——先绑端口，稍后才服务 /api/tags。单打一次会把健康服务误报成 FAIL。

  失败判定：端口不在听 = 失败（不看启动器是否报错——那层判断不可靠）。
            探针只用于展示存活，不参与失败判定。

  遵守的红线
    * 不读不写、不修改 server\.env
    * 不修改任何既有 .bat / .ps1
    * 不设 MORAY_REFLECTION / MORAY_REFLECTION_TIMEOUT
      （_start_moray.bat 的红线：这两个值以 server\.env 为准，进程 env 会覆盖 .env，
        历史上重复内联设置正是 120/300/600 漂移的来源）
 =============================================================================
#>

$ErrorActionPreference = 'Continue'

$SCRIPT_VERSION = '1.0.1'
$SCRIPT_DATE    = '2026-10-08'
$PROBE_TIMEOUT  = 5      # 秒，单次探针超时
$POLL_MS        = 700    # 毫秒，端口轮询间隔

# ---------------------------------------------------------------- 工具函数

function Get-ListeningPid {
    # 返回在该端口 LISTENING 的 PID（数组，已去重）；没有则 $null
    param([Parameter(Mandatory)][int]$Port)
    $netstat = Join-Path $env:SystemRoot 'System32\netstat.exe'
    $lines = & $netstat -ano 2>$null
    $found = @()
    foreach ($line in $lines) {
        if ($line -match '^\s*TCP\S*\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
            if ([int]$Matches[1] -eq $Port) { $found += [int]$Matches[2] }
        }
    }
    if ($found.Count -eq 0) { return $null }
    return @($found | Sort-Object -Unique)
}

function Get-DisplayWidth {
    # 中文按双宽计算，保证表格在终端里对齐
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return 0 }
    $w = 0
    foreach ($ch in $Text.ToCharArray()) {
        $c = [int][char]$ch
        if (($c -ge 0x1100 -and $c -le 0x115F) -or ($c -ge 0x2E80 -and $c -le 0xA4CF) -or
            ($c -ge 0xAC00 -and $c -le 0xD7A3) -or ($c -ge 0xF900 -and $c -le 0xFAFF) -or
            ($c -ge 0xFE30 -and $c -le 0xFE6F) -or ($c -ge 0xFF00 -and $c -le 0xFF60) -or
            ($c -ge 0xFFE0 -and $c -le 0xFFE6)) {
            $w += 2
        } else {
            $w += 1
        }
    }
    return $w
}

function Format-Cell {
    param([string]$Text, [int]$Width)
    if ($null -eq $Text) { $Text = '' }
    $pad = $Width - (Get-DisplayWidth -Text $Text)
    if ($pad -lt 1) { $pad = 1 }
    return $Text + (' ' * $pad)
}

function Start-HiddenProcess {
    # 隐藏窗口拉起进程；环境变量只作用于该子进程（不碰主会话）
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string]$Arguments = '',
        [string]$WorkingDirectory = '',
        $EnvSet = $null,
        $EnvRemove = $null
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName  = $FilePath
    $psi.Arguments = $Arguments
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    # UseShellExecute 必须为 false：PS5.1/.NET Framework 下为 true 时会忽略 EnvironmentVariables
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true
    $psi.WindowStyle     = [System.Diagnostics.ProcessWindowStyle]::Hidden

    if ($EnvRemove) {
        foreach ($k in @($EnvRemove)) { [void]$psi.EnvironmentVariables.Remove([string]$k) }
    }
    if ($EnvSet) {
        foreach ($k in $EnvSet.Keys) { $psi.EnvironmentVariables[[string]$k] = [string]$EnvSet[$k] }
    }

    return [System.Diagnostics.Process]::Start($psi)
}

function Invoke-Probe {
    param([Parameter(Mandatory)][string]$Url, [int]$TimeoutSec = 5)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ('OK ' + [int]$r.StatusCode)
    } catch {
        $resp = $_.Exception.Response
        if ($resp -and $resp.StatusCode) { return ('HTTP ' + [int]$resp.StatusCode) }
        return 'FAIL'
    }
}

function Invoke-ProbeRetry {
    # 端口刚 LISTEN 时服务未必已经能应答 HTTP（Ollama 就是典型：先绑端口，
    # 稍后才服务 /api/tags）。所以探针要带重试，否则会把健康服务报成 FAIL。
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$Attempts = 5,
        [int]$GapMs = 1500,
        [int]$TimeoutSec = 5
    )
    $last = 'FAIL'
    for ($i = 1; $i -le $Attempts; $i++) {
        $last = Invoke-Probe -Url $Url -TimeoutSec $TimeoutSec
        if ($last -like 'OK*') { return $last }
        if ($i -lt $Attempts) { Start-Sleep -Milliseconds $GapMs }
    }
    return $last
}

# ---------------------------------------------------------------- 服务清单

$CmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
$PsExe  = Join-Path $PSHOME 'powershell.exe'

$services = @(
    @{
        Name    = 'Ollama 11434'
        Port    = 11434
        WaitSec = 5
        Probe   = 'http://127.0.0.1:11434/api/tags'
        File    = 'D:\Ollama\app\ollama.exe'
        Args    = 'serve'
        WorkDir = 'D:\Ollama\app'
        Check   = 'D:\Ollama\app\ollama.exe'
        EnvSet  = $null
        EnvDel  = $null
    }
    @{
        Name    = 'MoRay 8000'
        Port    = 8000
        WaitSec = 8
        Probe   = 'http://127.0.0.1:8000/api/monitor/status'
        File    = $CmdExe
        Args    = '/c ""D:\MoRay\_start_moray.bat""'
        WorkDir = 'D:\MoRay'
        Check   = 'D:\MoRay\_start_moray.bat'
        EnvSet  = $null
        EnvDel  = $null
    }
    @{
        Name    = 'Laya 8001'
        Port    = 8001
        WaitSec = 5
        Probe   = 'http://127.0.0.1:8001/health'
        File    = 'D:\python3.12\python.exe'
        Args    = 'laya_std_server.py'
        WorkDir = 'D:\MoRay\decision-kernel\lora-training'
        Check   = 'D:\MoRay\decision-kernel\lora-training\laya_std_server.py'
        EnvSet  = $null
        EnvDel  = $null
    }
    @{
        Name    = 'Sandbox 8005'
        Port    = 8005
        WaitSec = 5
        Probe   = 'http://127.0.0.1:8005/health'
        File    = 'D:\python3.12\python.exe'
        Args    = 'zcode_sandbox_server.py'
        WorkDir = 'D:\MoRay\decision-kernel\lora-training'
        Check   = 'D:\MoRay\decision-kernel\lora-training\zcode_sandbox_server.py'
        EnvSet  = @{ LAYA_ZCODE_SANDBOX_ROOT = 'D:\MoRay\workspace\mapped' }
        EnvDel  = $null
    }
    @{
        Name    = 'Argus 8010'
        Port    = 8010
        WaitSec = 8
        Probe   = 'http://127.0.0.1:8010/api/health'
        File    = $CmdExe
        Args    = '/c ""D:\Argus\start_argus.bat""'
        WorkDir = 'D:\Argus'
        Check   = 'D:\Argus\start_argus.bat'
        EnvSet  = $null
        EnvDel  = $null
    }
    @{
        Name    = 'Iris-SSE 9301'
        Port    = 9301
        WaitSec = 6
        Probe   = 'http://127.0.0.1:9301/health'
        File    = $PsExe
        Args    = '-NoProfile -ExecutionPolicy Bypass -File "D:\Iris\server\start_server.ps1"'
        WorkDir = 'D:\Iris\server'
        Check   = 'D:\Iris\server\start_server.ps1'
        # start_server.ps1 自己会设 IRIS_MCP_API_KEY / IRIS_GATEWAY_URL / IRIS_LLM_TIMEOUT_SEC，
        # 只需要把网关覆盖值喂给它。EnvDel 剥掉 IRIS_MCP_PORT，
        # 否则会话里残留的 8004 会把 9301 这个实例顶到 8004 去，9301 永远起不来。
        EnvSet  = @{ IRIS_GATEWAY_URL_OVERRIDE = 'http://127.0.0.1:8000' }
        EnvDel  = @('IRIS_MCP_PORT')
    }
    @{
        Name    = 'Iris-RPC 8004'
        Port    = 8004
        WaitSec = 5
        Probe   = 'http://127.0.0.1:8004/health'
        File    = 'D:\MoRay\server\.venv\Scripts\python.exe'
        Args    = 'D:\Iris\server\mcp_server.py'
        WorkDir = 'D:\Iris\server'
        Check   = 'D:\Iris\server\mcp_server.py'
        EnvSet  = @{
            IRIS_MCP_PORT        = '8004'
            IRIS_MCP_API_KEY     = 'morayIrisKey2026'
            IRIS_GATEWAY_URL     = 'http://127.0.0.1:8000'
            IRIS_LLM_TIMEOUT_SEC = '90'
        }
        EnvDel  = $null
    }
    # ---- [MCP 网关第3晚 2026-11-06] Scribe / Surgeon / AIOps HTTP 触手 ----
    # Scribe 的 WorkDir 必须是 E:\moray-scribe：out/ 是相对 CWD 解析的，指错会写错产物。
    @{
        Name    = 'Scribe 9310'
        Port    = 9310
        WaitSec = 10
        Probe   = 'http://127.0.0.1:9310/mcp'
        File    = 'E:\moray-scribe\.venv\Scripts\python.exe'
        Args    = '-m moray_scribe.mcp.http_main'
        WorkDir = 'E:\moray-scribe'
        Check   = 'E:\moray-scribe\src\moray_scribe\mcp\http_main.py'
        EnvSet  = @{ MORAY_MCP_HOST = '127.0.0.1'; MORAY_MCP_PORT = '9310' }
        EnvDel  = $null
    }
    @{
        Name    = 'Surgeon 9311'
        Port    = 9311
        WaitSec = 10
        Probe   = 'http://127.0.0.1:9311/mcp'
        File    = 'D:\python3.12\python.exe'
        Args    = '-m moray_surgeon.mcp_server'
        WorkDir = 'E:\moray-surgeon'
        Check   = 'E:\moray-surgeon\src\moray_surgeon\mcp_server.py'
        EnvSet  = @{
            MORAY_MCP_TRANSPORT = 'streamable-http'
            MORAY_MCP_HOST      = '127.0.0.1'
            MORAY_MCP_PORT      = '9311'
        }
        EnvDel  = $null
    }
    @{
        Name    = 'AIOps 9312'
        Port    = 9312
        WaitSec = 10
        Probe   = 'http://127.0.0.1:9312/health'
        File    = 'E:\moray-aio\.venv\Scripts\python.exe'
        Args    = '-m app.tools.mcp_http'
        WorkDir = 'E:\moray-aio'
        Check   = 'E:\moray-aio\app\tools\mcp_http.py'
        EnvSet  = @{
            MORAY_AIO_MCP_HOST = '127.0.0.1'
            MORAY_AIO_MCP_PORT = '9312'
        }
        EnvDel  = $null
    }
)

# ---------------------------------------------------------------- banner

$scriptStart = Get-Date

Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ('  MoRay 全套服务一键启动器   v' + $SCRIPT_VERSION + '   ' + $SCRIPT_DATE) -ForegroundColor Cyan
Write-Host '  幂等：已在监听的端口会跳过，只拉起没起来的服务。' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan

# 会话残留环境变量提醒（本脚本不设任何 $env:，但如果调用者会话里已有，会影响子进程）
if ($env:IRIS_MCP_PORT) {
    Write-Host ('  [提醒] 当前会话已设 IRIS_MCP_PORT=' + $env:IRIS_MCP_PORT + '：Iris-SSE 会剥掉它（仍走默认 9301），Iris-RPC 由脚本显式覆盖为 8004。') -ForegroundColor Yellow
}
if ($env:MORAY_REFLECTION -or $env:MORAY_REFLECTION_TIMEOUT) {
    Write-Host '  [提醒] 当前会话已设 MORAY_REFLECTION*：进程 env 会覆盖 server\.env，可能造成 timeout 漂移。本脚本不设也不改它们。' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 逐个服务：探端口 -> 拉起 -> 回探

$results = @()

foreach ($svc in $services) {
    $row = [ordered]@{
        Port     = $svc.Port
        Name     = $svc.Name
        Status   = '失败'
        Pid      = '-'
        Probe    = '-'
        ProbeUrl = $svc.Probe
        Note     = ''
        Color    = 'Red'
    }

    $pre = Get-ListeningPid -Port $svc.Port
    if ($pre) {
        $row.Status = '已运行'
        $row.Pid    = (@($pre) -join ',')
        $row.Color  = 'DarkGray'
        Write-Host ('  [跳过] ' + $svc.Name + '  端口 ' + $svc.Port + ' 已在监听 (PID ' + $row.Pid + ')') -ForegroundColor DarkGray
        # 已在听的服务不重新拉起，但仍探活（同样给重试，防止偶发抖动误报）
        $row.Probe = Invoke-ProbeRetry -Url $svc.Probe -Attempts 3 -GapMs 800 -TimeoutSec $PROBE_TIMEOUT
        $results += [pscustomobject]$row
        continue
    }

    Write-Host ('  [拉起] ' + $svc.Name + '  <- ' + $svc.File + ' ' + $svc.Args) -ForegroundColor Cyan

    $fail = $null
    if ($svc.Check -and -not (Test-Path -LiteralPath $svc.Check)) {
        $fail = '启动文件不存在: ' + $svc.Check
    } else {
        try {
            $p = Start-HiddenProcess -FilePath $svc.File -Arguments $svc.Args `
                                     -WorkingDirectory $svc.WorkDir `
                                     -EnvSet $svc.EnvSet -EnvRemove $svc.EnvDel
            Write-Host ('         启动器 PID ' + $p.Id + ' (' + $p.ProcessName + ')，等端口最多 ' + $svc.WaitSec + 's') -ForegroundColor DarkGray
        } catch {
            $fail = $_.Exception.Message
        }
    }

    if ($fail) {
        # 进程根本没起来，探活没有意义，单打一次即可
        $row.Note  = $fail
        $row.Probe = Invoke-Probe -Url $svc.Probe -TimeoutSec $PROBE_TIMEOUT
        Write-Host ('  [错误] ' + $fail) -ForegroundColor Red
        $results += [pscustomobject]$row
        continue
    }

    $deadline = (Get-Date).AddSeconds($svc.WaitSec)
    $post = $null
    do {
        Start-Sleep -Milliseconds $POLL_MS
        $post = Get-ListeningPid -Port $svc.Port
    } while (-not $post -and (Get-Date) -lt $deadline)

    # 失败判定只看端口是否在听，不看启动器有没有报错，也不看探针
    if ($post) {
        $row.Status = '新拉'
        $row.Pid    = (@($post) -join ',')
        $row.Color  = 'Green'
    } else {
        $row.Status = '失败'
        $row.Color  = 'Red'
        $row.Note   = '等待 ' + $svc.WaitSec + 's 后端口 ' + $svc.Port + ' 仍未监听'
        Write-Host ('  [失败] 端口 ' + $svc.Port + ' 未在 ' + $svc.WaitSec + 's 内监听') -ForegroundColor Red
    }

    $row.Probe = Invoke-ProbeRetry -Url $svc.Probe -TimeoutSec $PROBE_TIMEOUT
    $results += [pscustomobject]$row
}

# ---------------------------------------------------------------- 状态表

$elapsed = (Get-Date) - $scriptStart

Write-Host ''
Write-Host '=== 状态表 ===' -ForegroundColor Cyan

$head = (Format-Cell '端口' 7) + (Format-Cell '服务' 20) + (Format-Cell '状态' 10) +
        (Format-Cell 'PID' 12) + (Format-Cell '探活' 12)
Write-Host $head -ForegroundColor White
Write-Host ('-' * ((Get-DisplayWidth -Text $head) - 1)) -ForegroundColor DarkGray

foreach ($r in $results) {
    $line = (Format-Cell ([string]$r.Port) 7) + (Format-Cell $r.Name 20) +
            (Format-Cell $r.Status 10) + (Format-Cell $r.Pid 12) + (Format-Cell $r.Probe 12)
    Write-Host $line -ForegroundColor $r.Color
}

$nNew  = @($results | Where-Object { $_.Status -eq '新拉' }).Count
$nRun  = @($results | Where-Object { $_.Status -eq '已运行' }).Count
$nFail = @($results | Where-Object { $_.Status -eq '失败' }).Count

Write-Host ''
Write-Host ('汇总: 新拉 ' + $nNew + ' / 已运行 ' + $nRun + ' / 失败 ' + $nFail) -ForegroundColor Cyan
Write-Host ('总耗时: ' + ('{0:N1}' -f $elapsed.TotalSeconds) + ' 秒') -ForegroundColor Yellow
Write-Host '  （这是脚本自身耗时。以后双击大致等这么久；已在听的服务会被跳过，通常更快。）' -ForegroundColor DarkGray

# ---------------------------------------------------------------- 失败明细

$failed = @($results | Where-Object { $_.Status -eq '失败' })
if ($failed.Count -gt 0) {
    Write-Host ''
    Write-Host '=== 失败明细 ===' -ForegroundColor Red
    foreach ($f in $failed) {
        Write-Host ('  ' + $f.Name + '  端口 ' + $f.Port + '  探针 ' + $f.ProbeUrl + ' -> ' + $f.Probe) -ForegroundColor Red
        if ($f.Note) { Write-Host ('      原因: ' + $f.Note) -ForegroundColor Red }
    }
    Write-Host ''
    Write-Host '  日志位置：' -ForegroundColor DarkYellow
    Write-Host '    MoRay 8000  -> %TEMP%\moray.out.log  /  moray.err.log' -ForegroundColor DarkYellow
    Write-Host '    Iris  9301  -> D:\Iris\server\server_9301.out.log  /  .err.log' -ForegroundColor DarkYellow
    Write-Host '    其余服务把输出丢进隐藏窗口，没有日志文件；可到各自目录手工起一次看报错。' -ForegroundColor DarkYellow
}

Write-Host ''
Write-Host '完成。' -ForegroundColor Cyan
