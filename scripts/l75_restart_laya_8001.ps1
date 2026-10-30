# L7.5 · 重启正式 Laya 8001（新磁盘代码含 /api/v1/task/*；由用户手动执行）
# 背景：8001 现实例加载的是改动前的 laya_std_server.py。总闸默认关，不重启不影响任何现状；
#       翻转 MORAY_LAYA_LINK=1 前需重启本服务让 dispatch 路由生效。
# 若只重启 MoRay 未重启 Laya：dispatch 404 会被连接器记为失败，3 连败后自动熔断走原生——安全但无收益。
$ErrorActionPreference = "Stop"
$dir = "D:\MoRay\decision-kernel\lora-training"
$py  = "D:\python3.12\python.exe"

$pid8001 = (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -First 1
if ($pid8001) {
    Write-Host "kill old laya pid=$pid8001"
    Stop-Process -Id $pid8001 -Force
    Start-Sleep -Seconds 1
}
# 回调地址可改：默认打正式 MoRay 8000 的事件入口
$env:LAYA_MORAY_CALLBACK_URL = "http://127.0.0.1:8000/api/laya/events"
Start-Process -FilePath $py -ArgumentList "-X","utf8","laya_std_server.py" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "laya started, waiting /health ..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:8001/health" -TimeoutSec 2
        if ($h.status -eq "running") { Write-Host "HEALTH OK:" ($h | ConvertTo-Json -Compress); break }
    } catch { Start-Sleep -Milliseconds 500 }
}
# 验证新路由已生效（空 body 应回 422 校验错 = 路由存在且服务在跑）
try {
    Invoke-RestMethod "http://127.0.0.1:8001/api/v1/task/dispatch" -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 3 | Out-Null
} catch {
    Write-Host "dispatch route probe responded (expect 422-ish):" $_.Exception.Message
}
