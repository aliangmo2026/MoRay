# -*- coding: utf-8 -*-
"""[v5.3] 只读白名单**真机**端到端探针（不是 mock 测试）。

做的是完整闭环，且用真引擎、真命令、真台账：
  1. 临时状态目录（绝不碰 D:\MoRay\server\data 的真台账，避免给自己刷分）；
  2. 只 mock LLM 那一层（省推理时间），三件套由脚本给定为**只读**卡；
  3. generate_goal → 应自动派发（readonly_class=True, status=dispatched, auto_approved=True）；
  4. 等引擎收尾 → verify_goal_card 跑真命令 → 出 verdict；
  5. 打印台账拆分（judged_auto / judged_human）证明这张卡记在"预授权"名下。

用法：python _probe_v53_live.py
"""
import asyncio
import json
import pathlib
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\MoRay\server")

from unittest import mock  # noqa: E402

from app import self_driven as sd  # noqa: E402

PROBE_GOAL = {
    "goal": r"核对 D:\MoRay\utf_test.txt 的真实字节数（历史断言为 12 字节）",
    "claim": r"D:\MoRay\utf_test.txt 的文件大小为 12 字节",
    "prediction": "dir 输出中该文件显示 12 字节",
    "verify_cmd": r"dir D:\MoRay\utf_test.txt",
    "target_engine": "zcode",
}


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sd_probe_"))
    sd.STATE_DIR = tmp
    sd.STATE_FILE = tmp / "state.json"
    print("[1] 临时台账:", tmp)
    print("    白名单开关 auto_approve_enabled() =", sd.auto_approve_enabled())

    async def fake_llm(messages):
        # 只 mock 推理：三件套按只读卡给。verify_cmd 是 dir（只读动词 + 绝对路径）
        return (json.dumps(PROBE_GOAL, ensure_ascii=False), "")

    card = None
    with mock.patch.object(sd, "_llm_goal", fake_llm):
        try:
            card = asyncio.run(sd.generate_goal())
        except Exception as e:  # noqa: BLE001
            print("!! generate_goal 抛异常:", type(e).__name__, str(e)[:300])
            return 2

    if card is None:
        print("!! 未生成卡（见上面日志）")
        return 2

    tid = card["task_id"]
    print("[2] 卡已生成 task_id=", tid)
    for k in ("readonly_class", "readonly_reason", "status", "auto_approved",
              "auto_reason", "engine_task_id", "verify_problem"):
        print("    %-16s %s" % (k, card.get(k)))

    # 引擎侧真终端状态：直接读状态文件，别信内存里的 card
    deadline = time.time() + 360
    last = None
    while time.time() < deadline:
        cur = sd._card_by_id(tid) or {}
        st = str(cur.get("status") or "")
        if st != last:
            print("[3] 状态:", st, "| engine_task_id=",
                  str(cur.get("engine_task_id") or "")[:12])
            last = st
        if st in ("done", "failed", "abandoned"):
            break
        time.sleep(10)

    print("[4] 跑真判据:", PROBE_GOAL["verify_cmd"])
    res = sd.verify_goal_card(tid, force=True)
    print("    verify_goal_card →", json.dumps(res, ensure_ascii=False)[:600])

    final = sd._card_by_id(tid) or {}
    print("[5] 终卡：status=%s verdict=%s ro_tree_changed=%s"
          % (final.get("status"), final.get("verdict"),
             final.get("ro_tree_changed")))
    print("    obs:", str(final.get("observation") or final.get("result_note") or "")[:400])

    st = sd._load_state()
    print("[6] 台账行:", json.dumps(st.get("verdict_log"), ensure_ascii=False)[:400])
    vs = sd.verdict_stats(7)
    print("[7] verdict_stats: judged=%s auto=%s human=%s days=%s"
          % (vs.get("judged"), vs.get("judged_auto"), vs.get("judged_human"),
             len(vs.get("day_list") or [])))
    ok = (final.get("verdict") in ("confirmed", "falsified")
          and vs.get("judged_auto") == 1)
    print("RESULT:", "OK 只读卡自跑并拿到真判决" if ok else "INCOMPLETE")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
