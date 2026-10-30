@echo off
title MoRay LoRA 一键训练 Qwen2.5-1.5B-Instruct
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo   MoRay 决策内核 LoRA 微调
echo   基座 Qwen2.5-1.5B-Instruct   方法 LoRA r=8 仅注意力层
echo ============================================
echo.

for /f "delims=" %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%T"

rem ---------- 1. 数据与登记文件就位检查 ----------
if not exist "output\train.jsonl" goto no_data
if not exist "output\val.jsonl" goto no_data
if not exist "dataset_info.json" goto no_info
if not exist "train_config.yaml" goto no_cfg

rem ---------- 2. 训练工具链检查 ----------
where llamafactory-cli >nul 2>nul
if errorlevel 1 goto no_cli

rem ---------- 3. 基座模型定位：本地优先，默认禁止远程下载 ----------
set "OFFLINE="
set "MODEL_ARG="
set "HFC=%USERPROFILE%\.cache\huggingface\hub\models--Qwen--Qwen2.5-1.5B-Instruct\snapshots"

dir /b /s "%HFC%\*.safetensors" >nul 2>nul
if not errorlevel 1 (
  set "OFFLINE=1"
  echo [模型] 命中 HuggingFace 本地缓存，离线加载
  goto model_ready
)

call :try_dir "%USERPROFILE%\.cache\modelscope\hub\Qwen\Qwen2___5-1___5B-Instruct"
call :try_dir "D:\models\Qwen2.5-1.5B-Instruct"
call :try_dir "D:\models\Qwen\Qwen2.5-1.5B-Instruct"
if defined MODEL_ARG (
  set "OFFLINE=1"
  echo [模型] 命中本地 HF 权重目录 %MODEL_ARG%
  goto model_ready
)

if "%ALLOW_REMOTE%"=="1" (
  echo [模型] 本地未找到权重；已显式设 ALLOW_REMOTE=1，允许本次远程下载
  goto model_ready
)
goto no_model

:model_ready
if defined OFFLINE (
  set "HF_HUB_OFFLINE=1"
  set "TRANSFORMERS_OFFLINE=1"
)

rem ---------- 4. 训练执行，日志自动落 logs 目录 ----------
if not exist "logs" mkdir "logs"
set "LOGF=logs\train_%TS%.log"
echo.
echo [训练] llamafactory-cli train train_config.yaml
echo [日志] 实时写入 %LOGF%
echo [跟踪] 另开窗口可跟看 powershell -Command "Get-Content -Wait '%LOGF%'"
echo.

llamafactory-cli train train_config.yaml %MODEL_ARG% > "%LOGF%" 2>&1
if errorlevel 1 goto train_fail

echo ============================================
echo   训练完成，LoRA 适配器已生成
echo   适配器目录 %CD%\lora-adapter
echo   训练日志   %CD%\%LOGF%
echo ============================================
dir /b lora-adapter 2>nul
goto end

:train_fail
echo [错误] 训练进程异常退出，退出码 %ERRORLEVEL%
echo [日志] 末尾 20 行如下，完整日志见 %LOGF%
powershell -NoProfile -Command "Get-Content -Path '%LOGF%' -Tail 20"
goto end

:no_data
echo [错误] 缺少 output\train.jsonl 或 output\val.jsonl。
echo 请先运行 build_dataset.bat 生成数据集，样本清洗-转换-拆分三步。
goto end

:no_info
echo [错误] 缺少 dataset_info.json，它把 output 下两份 jsonl 注册进 LLaMA-Factory 的映射表。
goto end

:no_cfg
echo [错误] 缺少 train_config.yaml。
goto end

:no_cli
echo [错误] PATH 上找不到 llamafactory-cli。
echo 安装参考 pip install "llamafactory[torch,metrics]"，Blackwell 显卡需先装 cu128 版 torch。
goto end

:no_model
echo [错误] 本机未找到 Qwen2.5-1.5B-Instruct 的 HF 格式权重，按不执行远程下载的约定已停止。
echo 说明 Ollama 缓存的 qwen2.5:1.5b 是 GGUF 量化格式，不能用作 LoRA 训练权重，需要 HF safetensors。
echo 方案A 手动补一份本地权重，本脚本从不自动联网：
echo     set HF_ENDPOINT=https://hf-mirror.com
echo     hf download Qwen/Qwen2.5-1.5B-Instruct --local-dir D:\models\Qwen2.5-1.5B-Instruct
echo     完成后重跑本脚本即可命中本地目录。
echo 方案B 明确允许一次性远程下载后重跑：
echo     set ALLOW_REMOTE=1 ^&^& start-training.bat
echo 权重若在别处也可设 HF_HOME 指向该缓存根后重跑。
goto end

:try_dir
if defined MODEL_ARG exit /b 0
if not exist "%~1\config.json" exit /b 0
set "MODEL_ARG=--model_name_or_path=%~1"
exit /b 0

:end
echo.
pause
