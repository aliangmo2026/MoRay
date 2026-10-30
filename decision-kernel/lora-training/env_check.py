# -*- coding: utf-8 -*-
"""
工作四：本地环境预检
检测项：
  1. Python 解释器（当前 + 系统内其他候选）版本；
  2. pip 可用性（pip 命令与 python -m pip 两种口径）；
  3. 本地 GPU / CUDA 可用性（nvidia-smi + torch.cuda 探测）；
  4. 微调生态包（torch / transformers / peft 等，缺哪个提示哪个）。

判定标准（LoRA 微调条件，QLoRA/PEFT 生态当前基线）：
  - Python ≥ 3.9（新版 PyTorch 的最低要求；3.8 已停止支持）
  - pip 可用
  - NVIDIA GPU + 驱动正常（有 CUDA）
  - torch ≥ 2.7 + cu128：Blackwell 架构卡（RTX 50 系，sm_120）必须满足，
    旧 CUDA 构建的 torch 无法识别该显卡算力。

输出：output/env_report.md（环境预检报告 + 是否满足 LoRA 微调条件结论）
纯标准库实现，零依赖。
"""
import json
import os
import shutil
import subprocess
import sys
import platform

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
REPORT = os.path.join(OUT_DIR, "env_report.md")

# 系统内已知的 Python 候选（探测到的其他解释器一并验证）
PY_CANDIDATES = ["python", "python3", r"D:\python3.12\python.exe", r"C:\Program Files\Python38\python.exe"]

TORCH_PROBE = (
    "import json,sys\n"
    "out={}\n"
    "try:\n"
    "    import torch\n"
    "    out['torch_version']=torch.__version__\n"
    "    out['torch_cuda_build']=torch.version.cuda\n"
    "    out['cuda_available']=bool(torch.cuda.is_available())\n"
    "    if out['cuda_available']:\n"
    "        out['device_count']=torch.cuda.device_count()\n"
    "        out['device_name']=torch.cuda.get_device_name(0)\n"
    "        out['capability']=list(torch.cuda.get_device_capability(0))\n"
    "except ImportError:\n"
    "    out['torch_version']=None\n"
    "except Exception as e:\n"
    "    out['error']=repr(e)\n"
    "print('<<<JSON>>>'+json.dumps(out))\n"
)


def run(cmd, timeout=60):
    """运行命令，返回 (returncode, stdout+stderr)；命令不存在/超时返回 None"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False)
        text = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, text.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def probe_python(exe):
    path = shutil.which(exe) or (exe if os.path.exists(exe) else None)
    if not path:
        return None
    rc, out = run([path, "--version"])
    if rc != 0 or not out:
        return None
    return {"exe": path, "version": out.replace("Python ", "").strip()}


def probe_pip(exe):
    rc, out = run([exe, "-m", "pip", "--version"])
    return (rc == 0), (out or "")


def probe_torch(exe):
    rc, out = run([exe, "-c", TORCH_PROBE], timeout=120)
    if rc != 0 or not out:
        return {"error": (out or "探测失败")[:200]}
    marker = "<<<JSON>>>"
    idx = out.rfind(marker)
    if idx < 0:
        return {"error": out[:200]}
    try:
        return json.loads(out[idx + len(marker):].strip().splitlines()[-1])
    except Exception:
        return {"error": out[:200]}


def vtuple(v):
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0, 0, 0)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    report = []
    add = report.append

    add("# 本地环境预检报告（工作四）")
    add("")
    add("- 检测时间：%s" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("- 操作系统：%s %s (%s)" % (platform.system(), platform.release(), platform.machine()))
    add("")

    # ---------- 1. Python ----------
    add("## 1. Python 解释器")
    add("")
    add("| 解释器 | 路径 | 版本 | pip (python -m pip) |")
    add("| --- | --- | --- | --- |")
    pys = []
    seen_paths = set()
    cur = {"exe": sys.executable, "version": "%d.%d.%d" % sys.version_info[:3]}
    pys.append(cur)
    seen_paths.add(os.path.normcase(cur["exe"]))
    for cand in PY_CANDIDATES:
        p = probe_python(cand)
        if p and os.path.normcase(p["exe"]) not in seen_paths:
            seen_paths.add(os.path.normcase(p["exe"]))
            pys.append(p)
    pip_ok_by_exe = {}
    for p in pys:
        ok, out = probe_pip(p["exe"])
        pip_ok_by_exe[p["exe"]] = (ok, out)
        add("| 主入口 `%s` | %s | %s | %s |" % (
            os.path.basename(p["exe"]), p["exe"], p["version"],
            "✔ " + out.splitlines()[0][:60] if ok else "✘ 不可用"))
    add("")

    # pip 命令本体
    pip_cmd = shutil.which("pip")
    add("- `pip` 命令解析到：`%s`" % (pip_cmd or "未找到"))
    if pip_cmd:
        rc, out = run([pip_cmd, "--version"])
        add("  - `pip --version`：%s" % (out.splitlines()[0] if out else "失败"))
    add("")

    # ---------- 2. GPU / CUDA ----------
    add("## 2. GPU / CUDA")
    add("")
    gpu_ok = False
    gpu_lines = []
    rc, smi = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap",
                   "--format=csv,noheader"])
    if rc == 0 and smi:
        gpu_ok = True
        add("- `nvidia-smi` 探测结果（name, driver, 显存, 算力）：")
        add("")
        add("```")
        add(smi)
        add("```")
        add("")
        compute_cap = None
        for line in smi.splitlines():
            gpu_lines.append(line)
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 4:
                try:
                    compute_cap = parts[3]
                except Exception:
                    pass
    else:
        add("- ✘ 未检测到 `nvidia-smi`（无 NVIDIA 显卡或驱动未安装）。")
    add("")

    # torch.cuda 逐解释器探测
    add("### torch / CUDA 生态探测")
    add("")
    add("| 解释器 | torch 版本 | CUDA 构建 | cuda.is_available | 设备 |")
    add("| --- | --- | --- | --- | --- |")
    torch_info_by_exe = {}
    for p in pys:
        t = probe_torch(p["exe"])
        torch_info_by_exe[p["exe"]] = t
        if t.get("torch_version"):
            dev = "%s %s" % (t.get("device_name", "-"), t.get("capability", "")) if t.get("cuda_available") else "-"
            add("| %s | %s | %s | %s | %s |" % (
                p["exe"], t["torch_version"], t.get("torch_cuda_build") or "-",
                "✔" if t.get("cuda_available") else "✘", dev))
        else:
            add("| %s | 未安装 | - | - | - |" % p["exe"])
    add("")

    # ---------- 3. 条件判定 ----------
    add("## 3. LoRA 微调条件判定")
    add("")
    # 选最优解释器：优先版本 ≥3.9 且 pip 可用
    best = None
    for p in pys:
        if vtuple(p["version"]) >= (3, 9, 0) and pip_ok_by_exe.get(p["exe"], (False,))[0]:
            best = p
            break
    fallback_cur_ok = vtuple(cur["version"]) >= (3, 9, 0) and pip_ok_by_exe.get(cur["exe"], (False,))[0]
    if not best and fallback_cur_ok:
        best = cur
    if best is None:
        best = cur  # 退化：仍用当前解释器给出结论

    # Blackwell 判定：算力 >= 12.0（RTX 50 系）
    blackwell = False
    try:
        cap_major = float((smi.splitlines()[0].split(",")[3] if gpu_ok and smi else "0").strip()) if gpu_ok else 0
        blackwell = cap_major >= 12.0
    except Exception:
        blackwell = gpu_ok and "50" in " ".join(gpu_lines)

    torch_best = torch_info_by_exe.get(best["exe"], {})
    gpu_cuda_ready = bool(torch_best.get("cuda_available"))

    def torch_ver_ge(v, target):
        return vtuple(str(v or "0")) >= target

    checks = []
    checks.append(("Python ≥ 3.9", vtuple(best["version"]) >= (3, 9, 0),
                   "推荐解释器 %s（Python %s）" % (best["exe"], best["version"])))
    pip_detail = ((pip_ok_by_exe.get(best["exe"], (False,))[1] or "-").splitlines() or ["-"])[0][:80]
    checks.append(("pip 可用", pip_ok_by_exe.get(best["exe"], (False,))[0],
                   pip_detail if pip_ok_by_exe.get(best["exe"], (False,))[0] else "不可用"))
    checks.append(("NVIDIA GPU + 驱动", gpu_ok, smi.splitlines()[0] if gpu_ok else "未检测到 nvidia-smi"))
    if blackwell:
        checks.append(("Blackwell（sm≥12.0）需 torch≥2.7 + cu128",
                       bool(torch_best.get("torch_version")) and torch_ver_ge(torch_best.get("torch_version"), (2, 7))
                       and str(torch_best.get("torch_cuda_build") or "").startswith("12.8"),
                       "torch=%s cuda_build=%s" % (torch_best.get("torch_version") or "未安装", torch_best.get("torch_cuda_build") or "-")))
    else:
        checks.append(("torch 已安装且 CUDA 可用", gpu_cuda_ready,
                       "torch=%s / cuda=%s" % (torch_best.get("torch_version") or "未安装", torch_best.get("cuda_available"))))

    add("| 条件 | 结果 | 详情 |")
    add("| --- | --- | --- |")
    all_ok = True
    core_ok = True
    for i, (name, ok, detail) in enumerate(checks):
        add("| %s | %s | %s |" % (name, "✔" if ok else "✘", detail))
        if not ok:
            all_ok = False
            if i < 3:
                core_ok = False
    add("")

    # ---------- 4. 结论 ----------
    add("## 4. 结论与建议")
    add("")
    if all_ok:
        add("**满足 LoRA 微调条件。** 可直接使用解释器 `%s` 开展训练。" % best["exe"])
    elif core_ok:
        add("**有条件满足：硬件与环境就绪，训练软件栈待补齐。**")
        add("")
        add("- 硬件满足要求（GPU + 驱动 + 可用解释器 + pip）；")
        if blackwell:
            add("- 检测到 Blackwell 架构显卡（算力 ≥ 12.0），必须使用支持 sm_120 的 PyTorch：")
            add("")
            add("  ```")
            add("  \"%s\" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128" % best["exe"])
            add("  \"%s\" -m pip install -U transformers peft datasets bitsandbytes accelerate" % best["exe"])
            add("  ```")
        else:
            add("- 当前解释器缺少可用的 CUDA 版 torch，安装后即可训练：")
            add("")
            add("  ```")
            add("  \"%s\" -m pip install torch --index-url https://download.pytorch.org/whl/cu128" % best["exe"])
            add("  \"%s\" -m pip install -U transformers peft datasets bitsandbytes accelerate" % best["exe"])
            add("  ```")
        add("- 注意：PATH 上的默认 `python`（%s）版本低于 3.9，训练时请显式使用 `%s`。" % (cur["version"], best["exe"]))
        add("")
        add("- 显存约束参考（8GB VRAM）：1.5B~3B 全参 LoRA（fp16/bf16）或 7B QLoRA（4bit）可行；7B fp16 LoRA 偏紧，建议 bf16 + 短序列 + 梯度累积。")
    else:
        add("**不满足 LoRA 微调条件**，请先修复上表中失败项（Python/pip/GPU 驱动为核心阻塞项）。")
    add("")
    add("> 本预检只读取环境信息，不安装、不修改任何系统配置；核心决策文件零改动。")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print("[OK] env report -> %s" % REPORT)


if __name__ == "__main__":
    main()
