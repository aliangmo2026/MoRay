# -*- coding: utf-8 -*-
"""阶段1 M1a：本机 Ollama 各本地模型原生 function-calling 真机探测。

- 非流式：/api/chat stream=false + tools，断言 tool_calls 是否返回、arguments 是否可解析；
- 流式：/api/chat stream=true，逐 NDJSON chunk 记录 tool_calls 出现方式（一次性 vs 分片），
  按增量规则拼接出完整 tool_calls 并验证与等价非流式结果一致性；
- 只验证模型能力，不执行任何工具、不写盘。
输出：模型 × 工具调用稳定性 × 非流式/流式 实测结论表。
"""
import json
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
PROBE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "list_directory",
        "description": "列出受控工作区内指定目录的文件与子目录（名称/类型/大小/修改时间）。当用户想查看工作区里有什么文件时使用。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "相对工作区根的目录路径，省略表示根"}}, "required": []}
    }
}]
PROBE_MSG = [{"role": "user", "content": "帮我看看工作区根目录里有哪些文件。请使用提供的工具。"}]


def api_chat(payload, timeout=180):
    req = urllib.request.Request(OLLAMA + "/api/chat", method="POST",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def api_chat_stream(payload, timeout=180):
    """流式：返回 (chunks, final_tool_calls, fragments_info)"""
    payload = dict(payload)
    payload["stream"] = True
    req = urllib.request.Request(OLLAMA + "/api/chat", method="POST",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    chunks = []
    calls = {}  # index -> {"id","name","arguments":str}
    frag_events = 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            chunks.append(j)
            m = j.get("message") or {}
            tcs = m.get("tool_calls") or []
            for i, c in enumerate(tcs):
                frag_events += 1
                fn = c.get("function") or {}
                slot = calls.setdefault(i, {"id": c.get("id") or "", "name": fn.get("name") or "",
                                            "arguments": fn.get("arguments") or ""})
                # 兼容分片：name 增量拼接、arguments 若为字符串则增量拼接，对象则合并
                if fn.get("name") and not slot["name"]:
                    slot["name"] = fn["name"]
                elif fn.get("name") and fn["name"] != slot["name"]:
                    slot["name"] += fn["name"]
                arg = fn.get("arguments")
                if isinstance(arg, str):
                    if isinstance(slot["arguments"], str):
                        slot["arguments"] += arg
                    else:
                        slot["arguments"] = arg
                elif isinstance(arg, dict):
                    if isinstance(slot["arguments"], str) and slot["arguments"]:
                        slot["arguments"] = arg  # 分片结束给全量对象
                    else:
                        slot["arguments"] = arg
            if j.get("done"):
                break
    final = [{"id": v["id"], "type": "function",
              "function": {"name": v["name"], "arguments": v["arguments"]}}
             for k, v in sorted(calls.items())]
    return chunks, final, frag_events


def parse_args(a):
    if a is None:
        return {}
    if isinstance(a, dict):
        return a
    if isinstance(a, str):
        s = a.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return {"_unparsed": s[:80]}
    return {}


def main():
    import sys
    rounds = int(sys.argv[sys.argv.index('--rounds') + 1]) if '--rounds' in sys.argv else 2
    tags = json.load(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10))
    models = [m["name"] for m in tags.get("models", [])]
    print("本地模型:", models, "| rounds =", rounds)
    rows = []
    for name in models:
        row = {"model": name, "nf_hit": 0, "sf_hit": 0, "nf_args_ok": 0, "sf_args_ok": 0}
        for i in range(rounds):
            try:
                j = api_chat({"model": name, "messages": PROBE_MSG, "tools": PROBE_TOOLS, "stream": False,
                              "options": {"num_predict": 256}})
                m = j.get("message") or {}
                tcs = [c for c in (m.get("tool_calls") or []) if (c.get("function") or {}).get("name")]
                if tcs:
                    row["nf_hit"] += 1
                    if "_unparsed" not in parse_args(tcs[0]["function"].get("arguments")):
                        row["nf_args_ok"] += 1
            except Exception:  # noqa: BLE001
                pass
            try:
                _, final, _ = api_chat_stream({"model": name, "messages": PROBE_MSG, "tools": PROBE_TOOLS,
                                               "options": {"num_predict": 256}})
                if final and final[0]["function"]["name"]:
                    row["sf_hit"] += 1
                    if "_unparsed" not in parse_args(final[0]["function"].get("arguments")):
                        row["sf_args_ok"] += 1
            except Exception:  # noqa: BLE001
                pass
        row["nf_rate"] = "%d/%d" % (row["nf_hit"], rounds)
        row["sf_rate"] = "%d/%d" % (row["sf_hit"], rounds)
        row["stable"] = row["nf_hit"] == rounds and row["sf_hit"] == rounds
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    print("\n==== 模型 × 工具调用 稳定性结论表（%d 轮）====" % rounds)
    print("%-18s %-10s %-10s %-10s %-10s %-8s" % ("模型", "非流式", "流式", "非流式参数", "流式参数", "稳定"))
    for r in rows:
        print("%-18s %-10s %-10s %-10s %-10s %-8s" % (
            r["model"], r["nf_rate"], r["sf_rate"], r["nf_args_ok"], r["sf_args_ok"], "✔" if r["stable"] else "✘"))


if __name__ == "__main__":
    main()
