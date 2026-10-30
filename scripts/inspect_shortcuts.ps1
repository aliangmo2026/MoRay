# Read-only: dump targets of desktop shortcuts whose name/target/icon mentions MoRay.
# ASCII-only: match by bytes to dodge the GBK/UTF-8 .ps1 read issue.
$sh = New-Object -ComObject WScript.Shell
$desktop = Join-Path $env:USERPROFILE 'Desktop'
Get-ChildItem $desktop -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
  $l = $sh.CreateShortcut($_.FullName)
  $hay = ($_.Name + ' ' + $l.TargetPath + ' ' + $l.IconLocation + ' ' + $l.WorkingDirectory)
  if ($hay -match 'MoRay|moray|electron|Harness') {
    Write-Output ("### " + $_.Name)
    Write-Output ("  TargetPath   = " + $l.TargetPath)
    Write-Output ("  Arguments    = " + $l.Arguments)
    Write-Output ("  WorkingDir   = " + $l.WorkingDirectory)
    Write-Output ("  IconLocation = " + $l.IconLocation)
  }
}
Write-Output "### electron-app exe/bat/dist"
Get-ChildItem 'D:\MoRay\electron-app' -Filter '*.exe' -ErrorAction SilentlyContinue |
  ForEach-Object { Write-Output ("  EXE " + $_.FullName) }
Get-ChildItem 'D:\MoRay\electron-app' -Filter '*.bat' -ErrorAction SilentlyContinue |
  ForEach-Object { Write-Output ("  BAT " + $_.FullName) }
if (Test-Path 'D:\MoRay\electron-app\dist') {
  Get-ChildItem 'D:\MoRay\electron-app\dist' -Filter '*.exe' -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Output ("  DIST-EXE " + $_.FullName) }
}
if (Test-Path 'D:\MoRay\electron-app\node_modules\electron\dist\electron.exe') {
  Write-Output ("  ELECTRON-RUNTIME D:\MoRay\electron-app\node_modules\electron\dist\electron.exe")
}
