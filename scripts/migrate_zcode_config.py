# -*- coding: utf-8 -*-
"""ZCode 全局配置 → 项目内迁移脚本（批次二 · 任务D；批次二·5 任务4 补强）

把全局 ZCode 配置迁移为项目内可用的相对路径配置，配合 ZCode 官方的
``ZCODE_DATA_BASE_DIR`` 环境变量（provider-runtime-env.ts:60 源码确认：
dataBaseDir 缺省 homedir()，env 可覆盖），ZCode 启动时自动检测并应用项目内配置。

【批次二·5 补强】CLI 真正消费的个人 provider 配置是
``<dataBaseDir>\\.zcode\\v2\\provider_config.json``（源码
packages/provider-node/src/runtime-paths.ts:5 PERSONAL_PROVIDER_CONFIG_FILE_NAME），
因此本脚本迁移**两个**文件：
  config.json          —— 桌面端 provider 注册表（provider.*.options.apiKey 形态）
  provider_config.json —— CLI 个人 provider 规则（providerConfigRules 形态，
                          本机 E2E 实证模型调用走的就是它）

产物（全部在项目内；scripts/zcode.cmd 用 %~dp0 相对解析，换机不绑死盘符）：
  work/zcode-home/.zcode/v2/config.json
  work/zcode-home/.zcode/v2/provider_config.json
  scripts/zcode.cmd                       启动 shim：设置 ZCODE_DATA_BASE_DIR 后调 CLI

【绝对红线】全局配置里的云端 Key（DeepSeek / bigmodel 等）原文绝不写入任何产物、
绝不打印到输出。脱敏规则：
  * 字段名匹配 (?i)apikey|api_key|token|secret|password 的一律置空；
  * 唯一例外：api.baseUrl 指向本机回环（localhost / 127.0.0.1）的 provider 的
    apiKey 且值恰为占位符 "ollama"（Ollama 不校验 key，这是本地占位不是凭据）；
  * 写盘前再全文扫描 "sk-" 兜底，命中即拒绝写盘（双保险）。
本脚本对全局配置**只读**，绝不修改 C:\\Users\\<user>\\.zcode 下任何文件。

用法：
  python scripts/migrate_zcode_config.py             # 预览（dry-run，不写盘）
  python scripts/migrate_zcode_config.py --apply     # 写入项目产物（含自检）
  python scripts/migrate_zcode_config.py --check     # 校验既有迁移产物完整且无 Key 泄漏
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ZCODE_V2_GLOBAL = Path.home() / ".zcode" / "v2"
GLOBAL_CONFIG = ZCODE_V2_GLOBAL / "config.json"
GLOBAL_PERSONAL = ZCODE_V2_GLOBAL / "provider_config.json"
DATA_BASE_DIR = PROJECT_ROOT / "work" / "zcode-home"
MIGRATED_CONFIG = DATA_BASE_DIR / ".zcode" / "v2" / "config.json"
MIGRATED_PERSONAL = DATA_BASE_DIR / ".zcode" / "v2" / "provider_config.json"
SHIM = PROJECT_ROOT / "scripts" / "zcode.cmd"
DEFAULT_ZCODE_SCRIPT = r"D:\ZCode_Test\zcode-repo\apps\zcode-cli\packages\cli\dist\zcode.cjs"

SENS_KEY = re.compile(r"(?i)apikey|api_key|token|secret|password")
LOCAL_HINT = re.compile(r"//(?:(?:localhost|127\.0\.0\.1)(?::\d+)?)", re.I)
# provider kind（桌面端注册表）→ CLI rules 的 api.type（provider-data-schema.ts 已证实枚举）
API_TYPE_MAP = {
    "anthropic": "anthropic-messages",
    "openai-compatible": "openai-chat-completions",
}

SHIM_TEMPLATE = """@echo off
rem MoRay project-local ZCode launcher (batch 2 task D / batch 2.5 task 4).
rem HARD-OVERLAY the project-migrated config: the parent shell may carry the
rem desktop ZCode's ZCODE_PERSONAL_PROVIDER_CONFIG_FILE (pointing at the user's
rem real C:\\ config with cloud keys) -- it must never leak into MoRay runs.
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\\work\\zcode-home") do set "ZCODE_DATA_BASE_DIR=%%~fI"
set "ZCODE_STORAGE_DIR=%ZCODE_DATA_BASE_DIR%"
set "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=%ZCODE_DATA_BASE_DIR%\\.zcode\\v2\\provider_config.json"
set "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE="
set "ZCODE_MODEL_TELEMETRY_ENABLED=0"
rem OTLP telemetry vars are left UNSET on purpose: no endpoint = telemetry fully off.
if "%ZCODE_SCRIPT%"=="" set "ZCODE_SCRIPT={zcode_script}"
if not exist "%ZCODE_SCRIPT%" (
  echo [moray-zcode] ZCode CLI script not found: %ZCODE_SCRIPT%
  echo [moray-zcode] Set ZCODE_SCRIPT to your zcode.cjs build and retry.
  exit /b 1
)
node "%ZCODE_SCRIPT%" %*
"""


def _scrub_sk(node, path, redacted):
    """通用兜底：任何字符串值里出现 sk- 即整字段置空（防字段名漏网）。"""
    if isinstance(node, dict):
        for k in list(node):
            child = node[k]
            if isinstance(child, str) and "sk-" in child.lower():
                node[k] = ""
                redacted.append(path + "." + str(k) + " (contains sk-)")
            else:
                _scrub_sk(child, path + "." + str(k), redacted)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scrub_sk(v, "%s[%d]" % (path, i), redacted)


def redact_v2_registry(cfg: dict):
    """脱敏桌面端注册表（provider.*.options.apiKey 形态），返回 (redacted, kept)。"""
    redacted, kept = [], []
    providers = cfg.get("provider") if isinstance(cfg, dict) else None
    if isinstance(providers, dict):
        for pid, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            opts = entry.get("options")
            if not isinstance(opts, dict):
                continue
            base = str(opts.get("baseURL") or "")
            is_local = bool(LOCAL_HINT.search(base))
            for k in list(opts):
                if not SENS_KEY.search(str(k)):
                    continue
                v = opts[k]
                if not isinstance(v, str) or v == "":
                    continue
                if is_local and k.lower() == "apikey" and v == "ollama":
                    kept.append("provider.%s.options.%s (local placeholder)" % (pid, k))
                    continue
                opts[k] = ""
                redacted.append("config.json provider.%s.options.%s" % (pid, k))
    _scrub_sk(cfg, "root", redacted)
    return redacted, kept


def build_local_only_personal(registry: dict):
    """从桌面端注册表构建「仅本地」的 CLI 个人 provider 规则（批次二·5 任务4 补强）。

    背景：E2E 实证（rollout model-io）CLI 会经 C 盘凭据库解析云端 provider 的密钥
    （ZCode接入评估报告 §4.5 风险 8「凭据复用」）——仅把云端规则"清空 key"不足以
    阻断云端调用。因此本项目 personal 配置 **只含回环 provider 规则、云端规则一律
    不迁入**：CLI 的可选 provider 被物理收敛到本机服务。
    """
    redacted, kept, warnings = [], [], []
    providers = registry.get("provider") if isinstance(registry, dict) else None
    rules, order, model_rules = [], [], []
    if isinstance(providers, dict):
        for pid, entry in providers.items():
            if not isinstance(entry, dict):
                continue
            opts = entry.get("options") or {}
            base = str(opts.get("baseURL") or "")
            if not LOCAL_HINT.search(base):
                redacted.append("provider_config.json 云端规则不迁入: %s (%s)"
                                % (pid, entry.get("name")))
                continue
            kind = API_TYPE_MAP.get(str(entry.get("kind") or ""))
            if kind is None:
                warnings.append("未知 provider kind %r，跳过 %s" % (entry.get("kind"), pid))
                continue
            models = list((entry.get("models") or {}).keys())
            raw_key = str(opts.get("apiKey") or "")
            if raw_key == "ollama":
                api_key = raw_key  # 唯一允许保留的占位（Ollama 不校验 key）
                kept.append("provider_config.json %s.apiKey (local placeholder)" % pid)
            else:
                api_key = ""
                redacted.append("provider_config.json %s.access.apiKey 置空" % pid)
            rules.append({
                "providerId": pid,
                "providerName": str(entry.get("name") or pid),
                "config": {
                    "group": "standard-personal",
                    "access": {"type": "api-key", "apiKey": api_key},
                    "api": {"type": kind, "baseUrl": base},
                    "personalModelIds": models,
                    "modelOrder": models,
                },
            })
            order.append(pid)
            for m in models:
                lim = ((entry.get("models") or {}).get(m) or {}).get("limit") or {}
                props = {}
                if lim.get("context"):
                    props["contextWindow"] = lim["context"]
                model_rules.append({
                    "modelId": m,
                    "config": {"properties": props},
                    "providerId": pid,
                })
    personal = {
        "schemaVersion": 1,
        "config": {
            "providerOrder": order,
            "providerConfigRules": {"providerRules": rules},
            "modelConfigRules": {
                "providerModelRules": model_rules,
                "manualProviderModelRules": [],
            },
        },
    }
    return personal, redacted, kept, warnings


def deep_scan_sk(cfg: dict) -> list:
    """写盘前终检：返回仍含 'sk-' 的字段路径（应为空）。"""
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + "." + str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))
        elif isinstance(node, str) and "sk-" in node.lower():
            hits.append(path)

    walk(cfg, "root")
    return hits


def _write_json(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cfg, ensure_ascii=False, indent=2)
    if "sk-" in text.lower():
        raise RuntimeError("写盘前终检失败（sk- 命中）: %s" % path)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="ZCode 全局配置迁移（云端 Key 绝不落盘）")
    ap.add_argument("--apply", action="store_true", help="写入项目产物（默认 dry-run 预览）")
    ap.add_argument("--check", action="store_true", help="校验既有迁移产物（完整性 + 无 Key 泄漏）")
    args = ap.parse_args()

    if args.check:
        ok = True
        for p in (MIGRATED_CONFIG, MIGRATED_PERSONAL):
            if not p.is_file():
                print("[check] 缺少迁移产物：%s" % p)
                ok = False
                continue
            leaks = deep_scan_sk(json.loads(p.read_text(encoding="utf-8")))
            print("[check] %s（%d 字节）sk- 泄漏：%s"
                  % (p, p.stat().st_size, "无" if not leaks else leaks))
            ok = ok and not leaks
        print("[check] shim 存在：%s" % ("是" if SHIM.is_file() else "否（--apply 重新生成）"))
        return 0 if ok else 1

    print("=" * 68)
    print("ZCode 配置迁移预览（dry-run，未写盘）")
    all_redacted, all_kept, leaks_all = [], [], []
    payloads = []
    # ① 桌面端注册表 config.json：脱敏原样迁移（参考层）
    if GLOBAL_CONFIG.is_file():
        cfg = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        red, kept = redact_v2_registry(cfg)
        leaks = deep_scan_sk(cfg)
        all_redacted += red
        all_kept += kept
        leaks_all += ["config.json %s" % x for x in leaks]
        payloads.append((MIGRATED_CONFIG, cfg))
        print("  config.json：%s（%d 字节）→ %s" % (GLOBAL_CONFIG, GLOBAL_CONFIG.stat().st_size, MIGRATED_CONFIG))
        for r in red:
            print("    脱敏 - " + r)
    else:
        print("  [warn] 全局文件不存在，跳过：%s" % GLOBAL_CONFIG)
    # ② CLI 个人规则 provider_config.json：**仅本地**重建（云端规则一律不迁入）
    if GLOBAL_CONFIG.is_file():
        registry = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        personal, red, kept, warns = build_local_only_personal(registry)
        leaks = deep_scan_sk(personal)
        all_redacted += red
        all_kept += kept
        leaks_all += ["provider_config.json %s" % x for x in leaks]
        payloads.append((MIGRATED_PERSONAL, personal))
        print("  provider_config.json：由注册表重建为「仅本地」规则 → %s" % MIGRATED_PERSONAL)
        for w in warns:
            print("    [warn] " + w)
        for r in red:
            print("    " + r)
    print("  保留的本地占位：%s" % (all_kept or "无"))
    print("  sk- 兜底扫描：%s" % ("干净" if not leaks_all else "仍有泄漏 -> %s" % leaks_all))
    if leaks_all:
        print("[error] 脱敏后仍存在 sk- 泄漏，拒绝写盘")
        return 1
    if not payloads:
        print("[error] 没有任何可迁移的全局配置")
        return 1

    if not args.apply:
        print("\n（dry-run 结束；加 --apply 写入产物）")
        return 0

    for dst, cfg in payloads:
        _write_json(dst, cfg)
        print("[apply] 已写入 %s" % dst)
    SHIM.write_text(SHIM_TEMPLATE.format(zcode_script=DEFAULT_ZCODE_SCRIPT.replace("\\", "\\\\")),
                    encoding="utf-8", newline="\r\n")
    print("[apply] 已写入 %s（ASCII 内容，规避 bat 中文 GBK 坑）" % SHIM)
    print("[apply] 验证：cmd /c scripts\\zcode.cmd --version   （或 --check 自检）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
