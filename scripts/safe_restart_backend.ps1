# safe_restart_backend.ps1 - the ONLY sanctioned way to restart the MoRay 8000 backend.
#
# Why this exists: killing uvicorn/python from an agent terminal severs the
# agent's own LLM gateway ("pulling its own oxygen tube" - the task dies with
# a Connection error). This script is safe to run from any terminal:
#   1. snapshots the current listener for forensics,
#   2. stops the old process tree (port must be free before re-bind),
#   3. spawns the official _start_moray.bat DETACHED (survives this terminal
#      closing) with a hidden window,
#   4. polls /api/health until 200, then reports RESULT: OK.
# ASCII-only on purpose: PowerShell/cmd GBK pitfalls with non-ASCII literals.

$ErrorActionPreference = 'Stop'
$Port      = 8000
$HealthUrl = 'http://127.0.0.1:8000/api/health'
$Bat       = 'D:\MoRay\_start_moray.bat'
$WorkDir   = 'D:\MoRay'
$LogDir    = 'D:\MoRay\logs'
$Stamp     = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogFile   = Join-Path $LogDir ("backend_restart_" + $Stamp + ".log")

function Write-Log([string]$msg) {
  $line = ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg)
  Write-Output $line
  try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

function Get-ListenerPid {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  return ($conns | Select-Object -ExpandProperty OwningProcess -Unique | Select-Object -First 1)
}

# ---- [1] snapshot current listener (forensics: who was running before) ----
$oldPid = Get-ListenerPid
if ($oldPid) {
  $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $oldPid) -ErrorAction SilentlyContinue
  $oldCmd = 'unknown'
  if ($proc -and $proc.CommandLine) { $oldCmd = [string]$proc.CommandLine }
  if ($oldCmd.Length -gt 160) { $oldCmd = $oldCmd.Substring(0, 160) }
  Write-Log ("old backend: pid={0} cmd={1}" -f $oldPid, $oldCmd)
} else {
  Write-Log 'old backend: none listening (fresh start)'
}

# ---- [2] stop the old tree, wait for the port to actually free up ----
if ($oldPid) {
  try { taskkill /PID $oldPid /T /F 2>&1 | Out-Null } catch { }
  $deadline = (Get-Date).AddSeconds(10)
  while (((Get-Date) -lt $deadline) -and (Get-ListenerPid)) { Start-Sleep -Milliseconds 300 }
  Write-Log ("old backend stopped (pid={0}), port released" -f $oldPid)
}

# ---- [3] spawn the official bat detached + hidden ----
# Start-Process = independent process (not a child that dies with this
# terminal); WindowStyle Hidden keeps the console invisible. WorkingDirectory
# matters: _start_moray.bat uses relative venv paths via its own cd /d.
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $Bat -WindowStyle Hidden -WorkingDirectory $WorkDir
Write-Log 'spawned _start_moray.bat (detached, hidden window)'

# ---- [4] poll until healthy ----
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 800
  try {
    $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) {
      $newPid = Get-ListenerPid
      Write-Log ("backend healthy: new pid={0} health=200" -f $newPid)
      Write-Output 'RESULT: OK'
      exit 0
    }
  } catch { }
}
Write-Output 'RESULT: TIMEOUT - backend did not come back within 45s; check D:\MoRay\logs'
exit 1
