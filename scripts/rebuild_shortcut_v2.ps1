# MoRay: force-delete old desktop shortcuts + backups, create a fresh "MoRay v2.lnk".
# ASCII-only (match by target path, not Chinese literals) to dodge GBK .ps1 read issue.
$ErrorActionPreference = 'Continue'
$ico = 'D:\MoRay\electron-app\build\icon.ico'
$electron = 'D:\MoRay\electron-app\node_modules\electron\dist\electron.exe'
$appDir = 'D:\MoRay\electron-app'
$desktop = Join-Path $env:USERPROFILE 'Desktop'

# --- Step 2: delete old MoRay shell shortcuts + any .bak_icon backups ---
# Match by icon/target path so we never touch unrelated .lnk files.
$sh = New-Object -ComObject WScript.Shell
$removed = 0
Get-ChildItem $desktop -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
  try { $l = $sh.CreateShortcut($_.FullName) } catch { $l = $null }
  if ($l -and (($l.TargetPath -match 'electron-app') -or ($_.Name -match 'v2'))) {
    Write-Output ("DELETE lnk  " + $_.Name)
    Remove-Item $_.FullName -Force
    $removed++
  }
}
# Delete leftover .bak_icon / .bak backups (they linger from prior rounds).
Get-ChildItem $desktop -Filter '*.lnk.*' -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Name -match 'bak') {
    Write-Output ("DELETE bak  " + $_.Name)
    Remove-Item $_.FullName -Force
    $removed++
  }
}
Write-Output ("Removed " + $removed + " old shortcut/backup files")

# --- Step 3: create a brand-new "MoRay v2.lnk" (fresh name avoids historical cache) ---
$newPath = Join-Path $desktop 'MoRay v2.lnk'
if (Test-Path $newPath) { Remove-Item $newPath -Force }
$lnk = $sh.CreateShortcut($newPath)
$lnk.TargetPath = $electron
$lnk.Arguments = ('"' + $appDir + '"')
$lnk.WorkingDirectory = $appDir
$lnk.IconLocation = ($ico + ',0')
$lnk.Description = 'MoRay Workbench (ray logo)'
$lnk.Save()
Write-Output ("CREATED " + $newPath)
$chk = $sh.CreateShortcut($newPath)
Write-Output ("  verify Target   = " + $chk.TargetPath)
Write-Output ("  verify Icon     = " + $chk.IconLocation)
Write-Output "Done. Press F5 on the desktop to refresh."
