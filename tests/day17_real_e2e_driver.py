# -*- coding: utf-8 -*-
"""[Day17] -p 传输链修复后的真实端到端驱动（新增测试文件，不改任何既有 .py）。

方案 B（架构师决策）：包装 bat 从 stdin 读取提示词并内联传给 -p，
适配器 argv（含 "-p -" 占位）与 .py 全部不动。

链路：本脚本 → ZCodeAdapter.start()（Day15 接线：审计/状态机/GC）
      → engines/docker/run_zcode_task.bat（Day17 版：stdin 排空 → 内联 -p）
      → 真实 docker run（Day 14 已验证三挂载形状 + -i）
      → 容器内 zcode -p "<提示词>" --mode edit --disallowed-tools ... --json

- 不设 MORAY_DB：读写真库 server/data/moray.sqlite3（与运行中的后端同一库）。
- GC 门控：引擎终态 completed 且产物（fizzbuzz.py）验证通过才执行
  cleanup_stale_tasks(older_than_minutes=0)——失败即停协议，绝不绕过。
- 运行：server/.venv/Scripts/python.exe -X utf8 tests/day17_real_e2e_driver.py
"""
import importlib.util
import json
import os
import sys
import time

sys.path.insert(0, r"D:\MoRay")

from server.app.engines.zcode_adapter import ZCodeAdapter

BAT = r"D:\MoRay\engines\docker\run_zcode_task.bat"
SANDBOX = r"D:\MoRay\workspace\zcode_sandbox"
SID = "zcode_day17_ppipe_fix"
PROMPT = ("在 /workspace 下写一个 Python 文件 fizzbuzz.py，包含函数 fizzbuzz(n) "
          "返回 1 到 n 的 FizzBuzz 结果列表。不需要测试文件。")

EXPECTED_15 = ["1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8",
               "Fizz", "Buzz", "11", "Fizz", "13", "14", "FizzBuzz"]


def main():
    eng = ZCodeAdapter(cli_cmd=[BAT])
    print("[driver] cli_cmd =", eng.cli_cmd, flush=True)
    print("[driver] capabilities =", json.dumps(eng.capabilities()), flush=True)

    task = {
        "action": "run_zcode",
        "session_id": SID,
        "prompt": PROMPT,
        "timeout_sec": 600,
    }
    tid = eng.start(task)
    print("[driver] task_id =", tid, flush=True)
    print("[driver] session_id =", SID, flush=True)

    t0 = time.time()
    while True:
        st = eng.status(tid)
        print("[driver] t=%.0fs status=%s error=%s" % (time.time() - t0, st["status"], st["error"]),
              flush=True)
        if st["status"] in ("completed", "failed"):
            break
        if time.time() - t0 > 900:
            print("[driver] driver-side watchdog: no terminal state in 900s", flush=True)
            break
        time.sleep(5)

    res = eng.result(tid)
    print("[driver] RESULT_JSON = " + json.dumps(res, ensure_ascii=False, default=str), flush=True)

    # 引擎内存任务记录快照（GC 前）
    print("[driver] memory task ids before GC:", list(eng._tasks.keys()), flush=True)

    # ---- 验证点3（产物）在驱动内完成，作为 GC 门控 ----
    gate = {"completed": res.get("status") == "completed"}
    fb_path = os.path.join(SANDBOX, "fizzbuzz.py")
    gate["file_exists"] = os.path.exists(fb_path)
    gate["run_ok"] = False
    if gate["file_exists"]:
        try:
            spec = importlib.util.spec_from_file_location("fizzbuzz_day17", fb_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            out = [str(x) for x in mod.fizzbuzz(15)]
            print("[driver] fizzbuzz(15) =", out, flush=True)
            gate["run_ok"] = (out == EXPECTED_15)
            if not gate["run_ok"]:
                print("[driver] fizzbuzz(15) != expected", EXPECTED_15, flush=True)
        except Exception as e:  # noqa: BLE001 - 产物验证失败按失败记录
            print("[driver] fizzbuzz import/run error:", repr(e), flush=True)
    print("[driver] gate =", gate, flush=True)

    # ---- 验证点4（GC，可选）：仅在全部通过时执行 ----
    if all(gate.values()):
        removed = eng.cleanup_stale_tasks(older_than_minutes=0)
        print("[driver] GC removed =", removed, flush=True)
        try:
            eng.result(tid)
            print("[driver] GC CHECK FAILED: record still in memory", flush=True)
        except KeyError:
            print("[driver] GC CHECK OK: record removed from memory", flush=True)
        print("[driver] memory task ids after GC:", list(eng._tasks.keys()), flush=True)
    else:
        print("[driver] GC SKIPPED (failure-stop protocol), gate not all pass", flush=True)


if __name__ == "__main__":
    main()
