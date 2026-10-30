# -*- coding: utf-8 -*-
"""
工作二：训练格式转换
把 output/cleaned_samples.jsonl 转换为标准对话微调格式（JSONL），
每条记录恰好包含 system / user / assistant 三个字符串字段：

  system    : MoRay 决策系统规则（与生产规则引擎同源）+ DecisionResult 五字段契约定义
  user      : 请求文本 + 对应权限等级（+ L0 世界状态风险标签，决策规则的输入依据）
  assistant : standard_decision 的标准 JSON 字符串（ensure_ascii=False，UTF-8）

纯标准库实现，零依赖。运行即自测：每条 assistant 必须可 JSON 解析，
且 DecisionResult 核心五字段（嵌套路径与 audit-samples.js 一致）齐全且取值合法。

输出：output/train_format.jsonl + output/convert_report.md
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
SRC = os.path.join(OUT_DIR, "cleaned_samples.jsonl")
DST = os.path.join(OUT_DIR, "train_format.jsonl")

# ---------------- system 提示：决策规则 + 五字段契约 ----------------
SYSTEM_PROMPT = (
    "你是 MoRay 应用层 AGI 决策内核 v1.0 的 Laya 决策层（Layer 1）。"
    "根据用户请求与权限等级，产出一条符合 DecisionResult 契约的标准决策结果。\n"
    "【输出要求】只输出一个 JSON 对象，禁止 markdown 代码块与任何解释文字。顶层字段必须完整：\n"
    "decision_id, input_snapshot{query,world_state_id,context_id,timestamp},\n"
    "output{target_path,agent_chain[],param_mapping{},step_sequence[],expected_outcome,"
    "verification_points[],fallback_plan{condition,action,max_retry}},\n"
    "quality{confidence,complexity_score,risk_level,is_composite_scenario,composite_type},\n"
    "upgrade{should_upgrade,trigger_types[],trigger_reasons[],upgrade_priority},\n"
    "explainability{matched_meta_rules[],matched_domain_experience[],reasoning_summary,counterfactual_note},\n"
    "meta_consistency{constitution_alignment,deviation_note,from_meta_core},"
    "source,latency_ms,timestamp,version{meta_core,domain_adapter,world_encoder}\n"
    "【DecisionResult 五字段契约】以下五个核心字段必须存在且合法：\n"
    "① risk_level（quality.risk_level）∈ low|medium|high|critical；\n"
    "② confidence（quality.confidence）∈ [0,1] 数值；\n"
    "③ is_composite_scenario（quality.is_composite_scenario）布尔值，组合场景时 confidence=0.72，非组合 confidence=0.9；\n"
    "④ should_upgrade（upgrade.should_upgrade）布尔值，为 true 时必须给出 trigger_types 与 upgrade_priority"
    "（∈ none|P2|P1|P0|urgent；trigger_types ∈ risk|composite_scenario|complexity|low_confidence|deep_reasoning）；\n"
    "⑤ step_sequence（output.step_sequence）非空数组，元素结构 "
    "{step_id,step_type,agent_call{agent_id,params,order,timeout_ms},description,verification}，"
    "step_type ∈ intent_analysis|execution|user_confirm|audit；agent_chain 为其 agent_call 按序展开。\n"
    "【决策规则（与生产规则引擎同源，必须遵守）】\n"
    "1. 风险校验（META-RISK-01）：请求含「生产/线上」→ high；「删除/权限/配置」每命中一词风险升一级（封顶 critical）；"
    "high/critical 必触发 risk 升级维度，硬规则不可绕过；\n"
    "2. 路径生成（META-PATH-01）：普通 2 步（意图解析 + 执行）；高风险追加 user_confirm 步；生产环境追加 audit 步；\n"
    "3. 复杂度（META-COMP-01）：执行步骤数 ≥ 3 → 触发 complexity 升级维度；\n"
    "4. 置信度门槛（META-CONF-01）：confidence < 0.8 触发 low_confidence 升级维度（组合场景阈值 0.72 / 普通场景 0.9）；\n"
    "5. 能力匹配（META-CAP-01）：请求意图 → Agent 域映射，可用 Agent：code_agent / confirm_agent / audit_agent；\n"
    "6. 组合场景：代码/文件修改 × 高风险 → is_composite_scenario=true；\n"
    "7. 蒸馏场景：风险/组合/复杂度触发降级为仅记录，是否升级只看置信度是否 ≥ 0.8；\n"
    "8. decision_id 形如 dec-<毫秒时间戳>-<6位十六进制>；meta_consistency.from_meta_core=true。"
)


def build_user(sample):
    """user = 请求文本 + 权限等级 +（L0 世界状态风险标签，规则输入）"""
    req = sample["request"]
    ws = sample.get("world_state", {}) or {}
    parts = [
        "用户请求：" + req["query"],
        "权限等级：" + str(req.get("permission", "write")),
    ]
    if req.get("options"):
        parts.append("附加选项：" + json.dumps(req["options"], ensure_ascii=False, sort_keys=True))
    if ws.get("operation_risk_tag") is not None or ws.get("agent_availability") is not None:
        parts.append("世界状态（L0 编码结果）：" + json.dumps({
            "operation_risk_tag": ws.get("operation_risk_tag"),
            "user_permission_level": ws.get("user_permission_level"),
            "agent_availability": ws.get("agent_availability"),
        }, ensure_ascii=False))
    return "\n".join(parts)


RISK_SET = {"low", "medium", "high", "critical"}
PRIORITY_SET = {"none", "P2", "P1", "P0", "urgent"}


def validate_assistant(dec):
    """自测：核心五字段齐全且取值合法；合法返回 None，非法返回原因"""
    if not isinstance(dec, dict):
        return "assistant 不是 JSON 对象"
    q = dec.get("quality")
    if not isinstance(q, dict):
        return "quality 缺失"
    if q.get("risk_level") not in RISK_SET:
        return "risk_level 非法: %r" % (q.get("risk_level"),)
    c = q.get("confidence")
    if not isinstance(c, (int, float)) or isinstance(c, bool) or not (0 <= c <= 1):
        return "confidence 非法: %r" % (c,)
    if not isinstance(q.get("is_composite_scenario"), bool):
        return "is_composite_scenario 非布尔"
    up = dec.get("upgrade")
    if not isinstance(up, dict) or not isinstance(up.get("should_upgrade"), bool):
        return "should_upgrade 缺失/非布尔"
    if up.get("upgrade_priority") not in PRIORITY_SET:
        return "upgrade_priority 非法: %r" % (up.get("upgrade_priority"),)
    out = dec.get("output")
    if not isinstance(out, dict) or not isinstance(out.get("step_sequence"), list) or not out["step_sequence"]:
        return "step_sequence 缺失/为空"
    return None


def main():
    if not os.path.exists(SRC):
        sys.stderr.write("[FAIL] 未找到 %s，请先运行 clean_samples.py\n" % SRC)
        sys.exit(1)

    samples = []
    with open(SRC, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                samples.append(json.loads(ln))

    records = []
    fails = []
    for s in samples:
        dec = s["standard_decision"]
        assistant = json.dumps(dec, ensure_ascii=False)
        err = validate_assistant(json.loads(assistant))
        if err:
            fails.append((s.get("sample_id", "?"), err))
            continue
        records.append({"system": SYSTEM_PROMPT, "user": build_user(s), "assistant": assistant})

    with open(DST, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 转换报告
    lines = [
        "# 训练格式转换报告（工作二）",
        "",
        "- 输入：`output/cleaned_samples.jsonl`（%d 条）" % len(samples),
        "- 成功转换：**%d** 条 → `output/train_format.jsonl`" % len(records),
        "- 自测剔除（核心五字段校验失败）：%d 条" % len(fails),
    ]
    for sid, err in fails:
        lines.append("    - `%s`：%s" % (sid, err))
    lines += [
        "",
        "## 格式说明",
        "",
        "- 每条记录三个字符串字段：`system` / `user` / `assistant`。",
        "- `system`：MoRay 决策系统规则（与生产规则引擎同源的 7+1 条元规则）+ DecisionResult 五字段契约定义（risk_level / confidence / is_composite_scenario / should_upgrade / step_sequence）。",
        "- `user`：请求文本 + 权限等级（有附加选项 / L0 世界状态时一并拼接，保证规则输入完整）。",
        "- `assistant`：standard_decision 的标准 JSON 字符串（ensure_ascii=False，可直接 json.loads 还原）。",
        "",
        "## system 提示全文",
        "",
        "```",
        SYSTEM_PROMPT,
        "```",
    ]
    with open(os.path.join(OUT_DIR, "convert_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("[OK] converted=%d  selfcheck_failed=%d  -> output/train_format.jsonl" % (len(records), len(fails)))


if __name__ == "__main__":
    main()
