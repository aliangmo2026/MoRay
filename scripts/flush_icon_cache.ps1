# MoRay: flush Windows icon cache + rebuild the desktop shell shortcut.
# Atomic: try/finally guarantees explorer restarts even if a step throws.
# ASCII-only to dodge the PowerShell GBK/UTF-8 .ps1 read issue.

$ErrorActionPreference = 'Stop'
$ico = 'D:\MoRay\electron-app\build\icon.ico'
$electron = 'D:\MoRay\electron-app\node_modules\electron\dist\electron.exe'
$appDir = 'D:\MoRay\electron-app'

if (-not (Test-Path $ico))   { Write-Output ("MISSING ico: " + $ico); exit 1 }
if (-not (Test-Path $electron)) { Write-Output ("MISSING electron: " + $electron); exit 1 }

# ---- Step 1: (re)build the shortcut BEFORE touching explorer ----
$sh = New-Object -ComObject WScript.Shell
$desktop = Join-Path $env:USERPROFILE 'Desktop'
$target = $null
Get-ChildItem $desktop -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
  $l = $sh.CreateShortcut($_.FullName)
  if ($l.TargetPath -match 'electron-app') { $target = $_.FullName }
}
if (-not $target) {
  $target = Join-Path $desktop 'MoRay Desktop.lnk'
  Write-Output ("No existing shortcut matched; will create: " + $target)
} else {
  # Backup the old .lnk instead of deleting (recoverable).
  $bak = $target + '.bak_icon'
  Copy-Item $target $bak -Force
  Write-Output ("Backed up old shortcut -> " + $bak)
}
$lnk = $sh.CreateShortcut($target)
$lnk.TargetPath = $electron
$lnk.Arguments = ('"' + $appDir + '"')
$lnk.WorkingDirectory = $appDir
$lnk.IconLocation = ($ico + ',0')
$lnk.Description = 'MoRay Workbench'
$lnk.Save()
Write-Output ("REBUILT shortcut: " + $target)
Write-Output ("  -> IconLocation = " + $lnk.IconLocation)

# ---- Step 2: kill explorer, purge icon cache, restart ----
$local = $env:LOCALAPPDATA
try {
  Write-Output "Killing explorer.exe (screen will flash)..."
  Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2

  # Purge icon + thumbnail caches. Explorer is down, so locks are released.
  $targets = @()
  $targets += Join-Path $local 'IconCache.db'
  $explorerDir = Join-Path $local 'Microsoft\Windows\Explorer'
  if (Test-Path $explorerDir) {
    $targets += (Get-ChildItem $explorerDir -Filter 'iconcache_*.db' -ErrorAction SilentlyContinue).FullName
    $targets += (Get-ChildItem $explorerDir -Filter 'thumbcache_*.db' -ErrorAction SilentlyContinue).FullName
  }
  $removed = 0
  foreach ($f in $targets) {
    if ($f -and (Test-Path $f)) {
      try { Remove-Item $f -Force -ErrorAction Stop; $removed++ } catch { Write-Output ("  skip " + $f) }
    }
  }
  Write-Output ("Purged " + $removed + " cache files")
} finally {
  Write-Output "Restarting explorer.exe..."
  Start-Process explorer.exe
  Start-Sleep -Seconds 3
}

# ---- Step 3: light-touch the icon so shell re-reads mtime ----
(Get-Item $ico).LastWriteTime = Get-Date
Write-Output ("Touched ico mtime -> " + (Get-Item $ico).LastWriteTime)

# ---- Step 4: ask shell to refresh icon caches without a full logoff ----
try { ie4uinit.exe -show } catch { Write-Output "ie4uinit unavailable (optional)" }

Write-Output "DONE. Desktop icon should now be the new ray logo."
