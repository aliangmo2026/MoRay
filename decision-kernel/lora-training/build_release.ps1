# MoRay 决策内核 v1.1 一键发布脚本
# 功能：自动完成 LoRA合并 -> HF模型 -> GGUF fp16 -> GGUF Q5_K_M + KV4配置
# 用法：训练完新LoRA后，直接在lora-training目录下跑 .\build_release.ps1

$ErrorActionPreference = "Stop"
$base = Get-Location

Write-Host "=== Step 1/4: 合并LoRA到基座模型 ==="
D:\python3.12\python.exe merge_lora.py
if ($LASTEXITCODE -ne 0) { throw "合并LoRA失败" }

Write-Host "=== Step 2/4: HF模型转GGUF fp16 ==="
D:\python3.12\python.exe convert_hf_to_gguf.py ./merged_moray_decision --outfile moray-decision-fp16.gguf --outtype f16
if ($LASTEXITCODE -ne 0) { throw "GGUF转换失败" }

Write-Host "=== Step 3/4: 量化为Q5_K_M ==="
# 这里需要等llama.cpp编译好的quantize工具放进来，先预留路径
& ".\llama-cpp\llama-quantize.exe" ./moray-decision-fp16.gguf ./moray-decision-q5_k_m.gguf q5_k_m
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: quantize工具未就绪，仅保留fp16版本" }

Write-Host "=== Step 4/4: 生成llama.cpp启动配置 + 回退机制 ==="
# 产物独立目录，不覆盖现有transformers版本，保留一键回退
New-Item -ItemType Directory -Path "../models/v1.1-llamacpp" -Force
Copy-Item ".\moray-decision-*.gguf" "../models/v1.1-llamacpp/" -Force

@"
# MoRay 决策内核 v1.1 llama.cpp 版本
# 回退机制：配置层切换即可，transformers版本完整保留在 merged_moray_decision 目录，一键切回

## 启动命令
./llama-server.exe -m ../models/v1.1-llamacpp/moray-decision-q5_k_m.gguf --host 0.0.0.0 --port 8080 --n-gpu-layers 99 --cache-type-k q4_0 --cache-type-v q4_0

## 上线前必做验证清单
1.  GBNF + 流式联合验证：238条样本，meta块前20token覆盖率≥95%，JSON成功率100%
2.  量化对照：risk_level/should_upgrade一致率≥99.5%，upgrade_priority≥99%，confidence漂移≤±0.05
3.  回退验证：一键切回transformers版本可用
4.  灰度策略：先切5%流量，观察72小时
"@ | Out-File -Encoding utf8 run_llama_server.md

Write-Host "=== 发布完成！产物： ==="
Get-ChildItem -Filter "*.gguf" | Select-Object Name, @{N="SizeMB";E={[math]::Round($_.Length/1MB,1)}}
