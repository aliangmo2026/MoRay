# Ultimate fix: inject ray icon into electron.exe's internal resources via rcedit.
# Closes the running MoRay shell first (stateless web container - safe & reversible),
# backs up the pristine exe, injects, then relaunches the shell.
$ErrorActionPreference = 'Stop'
$exe = 'D:\MoRay\electron-app\node_modules\electron\dist\electron.exe'
$appDir = 'D:\MoRay\electron-app'
$ico = 'D:\MoRay\electron-app\build\icon.ico'
$rcedit = Join-Path $env:APPDATA 'npm\node_modules\rcedit\bin\rcedit-x64.exe'

if (-not (Test-Path $exe))    { Write-Output ("MISSING exe: " + $exe); exit 1 }
if (-not (Test-Path $ico))    { Write-Output ("MISSING ico: " + $ico); exit 1 }
if (-not (Test-Path $rcedit)) { Write-Output ("MISSING rcedit: " + $rcedit); exit 1 }

# --- Step A: close the running shell so the exe file lock is released ---
$running = Get-Process -Name electron -ErrorAction SilentlyContinue
if ($running) {
  Write-Output ("Closing " + $running.Count + " electron process(es) for icon injection...")
  Stop-Process -Name electron -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  # Confirm the lock is gone before touching the file.
  $still = Get-Process -Name electron -ErrorAction SilentlyContinue
  if ($still) { Write-Output "!! electron still running - cannot inject"; exit 2 }
  Write-Output "Shell closed, file lock released."
} else {
  Write-Output "No electron running."
}

# --- Step B: keep ONE pristine backup (do not overwrite after first run) ---
$bak = $exe + '.pristine'
if (-not (Test-Path $bak)) {
  Copy-Item $exe $bak -Force
  Write-Output ("Backed up pristine -> " + $bak)
} else {
  Write-Output ("Pristine backup kept: " + $bak)
}

# --- Step C: inject the icon ---
Write-Output "Injecting icon into electron.exe ..."
& $rcedit $exe --set-icon $ico
if ($LASTEXITCODE -ne 0) {
  Write-Output ("rcedit FAILED exit=" + $LASTEXITCODE + " - restoring pristine")
  Copy-Item $bak $exe -Force
  Start-Process -FilePath $exe -ArgumentList ('"' + $appDir + '"') -WorkingDirectory $appDir
  exit 3
}
Write-Output ("Icon injected. exe mtime: " + (Get-Item $exe).LastWriteTime)

# --- Step D: relaunch the shell ---
Start-Process -FilePath $exe -ArgumentList ('"' + $appDir + '"') -WorkingDirectory $appDir
Write-Output "Shell relaunched."
Write-Output "DONE"
