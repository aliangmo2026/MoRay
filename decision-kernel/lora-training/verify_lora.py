import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import json

# 路径配置
base_model_path = "./base_model"
lora_adapter_path = "./lora-adapter"

print("正在加载基座模型...")
tokenizer = AutoTokenizer.from_pretrained(base_model_path)
model = AutoModelForCausalLM.from_pretrained(
    base_model_path,
    torch_dtype=torch.bfloat16,
    device_map="cuda",
    trust_remote_code=True
)

print("正在加载LoRA适配器...")
model = PeftModel.from_pretrained(model, lora_adapter_path)
model.eval()

# 测试用例：覆盖典型决策场景
test_cases = [
    # 场景1：生产环境高风险操作
    {
        "query": "变更生产环境的数据库配置项 db.host",
        "permission": "write",
        "risk_tag": "critical"
    },
    # 场景2：普通代码修复
    {
        "query": "修复页面脚本 home.js 里的一个 bug",
        "permission": "write",
        "risk_tag": "low"
    },
    # 场景3：用户新增场景
    {
        "query": "删除用户表中的一条测试数据",
        "permission": "write",
        "risk_tag": "high"
    }
]

system_prompt = """你是 MoRay 应用层 AGI 决策内核 v1.0 的 Laya 决策层（Layer 1）。根据用户请求与权限等级，产出一条符合 DecisionResult 契约的标准决策结果。
【输出要求】只输出一个 JSON 对象，禁止 markdown 代码块与任何解释文字。顶层字段必须完整：
decision_id, input_snapshot{query,world_state_id,context_id,timestamp},
output{target_path,agent_chain[],param_mapping{},step_sequence[],expected_outcome,verification_points[],fallback_plan{condition,action,max_retry}},
quality{confidence,complexity_score,risk_level,is_composite_scenario,composite_type},
upgrade{should_upgrade,trigger_types[],trigger_reasons[],upgrade_priority},
explainability{matched_meta_rules[],matched_domain_experience[],reasoning_summary,counterfactual_note},
meta_consistency{constitution_alignment,deviation_note,from_meta_core},source,latency_ms,timestamp,version{meta_core,domain_adapter,world_encoder}
【DecisionResult 五字段契约】以下五个核心字段必须存在且合法：
① risk_level（quality.risk_level）∈ low|medium|high|critical；
② confidence（quality.confidence）∈ [0,1] 数值；
③ is_composite_scenario（quality.is_composite_scenario）布尔值，组合场景时 confidence=0.72，非组合 confidence=0.9；
④ should_upgrade（upgrade.should_upgrade）布尔值，为 true 时必须给出 trigger_types 与 upgrade_priority（∈ none|P2|P1|P0|urgent；trigger_types ∈ risk|composite_scenario|complexity|low_confidence|deep_reasoning）；
⑤ step_sequence（output.step_sequence）非空数组。
【决策规则】
1. 风险校验：请求含「生产/线上」→ high；「删除/权限/配置」每命中一词风险升一级（封顶 critical）；
2. 普通2步（意图解析+执行）；高风险加user_confirm；生产加audit；
3. 步骤数≥3触发complexity升级；confidence<0.8触发low_confidence升级；
4. 组合场景：代码/文件修改 × 高风险 → is_composite_scenario=true。
"""

print("\n=== 开始模型对齐度验证 ===")
for i, case in enumerate(test_cases):
    print(f"\n--- 测试用例 {i+1}: {case['query']} ---")
    user_input = f"""用户请求：{case['query']}
权限等级：{case['permission']}
世界状态：{{"operation_risk_tag": "{case['risk_tag']}"}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ]
    
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to("cuda")
    
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=2048,
            temperature=0.1,
            top_p=0.9,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id
        )
    
    response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    print("模型原始输出：")
    print(response[:800] + "..." if len(response) > 800 else response)
    
    # 去除markdown代码块标记
    clean_response = response.strip()
    if clean_response.startswith("```json"):
        clean_response = clean_response[7:]
    if clean_response.startswith("```"):
        clean_response = clean_response[3:]
    if clean_response.endswith("```"):
        clean_response = clean_response[:-3]
    clean_response = clean_response.strip()
    
    # 验证JSON合法性
    try:
        result = json.loads(clean_response)
        print("\n✅ JSON格式合法")
        
        # 验证五字段契约
        checks = {
            "risk_level合法": result["quality"]["risk_level"] in ["low","medium","high","critical"],
            "confidence在0-1": 0 <= result["quality"]["confidence"] <= 1,
            "is_composite_scenario布尔": isinstance(result["quality"]["is_composite_scenario"], bool),
            "should_upgrade布尔": isinstance(result["upgrade"]["should_upgrade"], bool),
            "step_sequence非空": len(result["output"]["step_sequence"]) > 0
        }
        
        print("\n五字段契约检查：")
        all_pass = True
        for check, passed in checks.items():
            status = "✅ 通过" if passed else "❌ 失败"
            if not passed:
                all_pass = False
            print(f"  {check}: {status}")
        
        if all_pass:
            print(f"\n🎉 用例 {i+1} 全部检查通过！")
        else:
            print(f"\n⚠️  用例 {i+1} 存在字段不合规")
            
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON解析失败: {e}")

print("\n=== 验证完成 ===")
